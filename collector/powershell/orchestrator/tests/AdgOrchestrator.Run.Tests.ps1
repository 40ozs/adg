<#
    Running a job: the lock, the retry, and what is written afterwards.

    The acceptance criterion this file exists for is the fourth one: **a failed incremental
    run cannot silently declare the source current**. That is enforced twice, in two
    places, by two parties who know different things - the collector knows about its own
    errors, and only the server knows what actually arrived - and this is the collector half.
#>

BeforeAll {
    $repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
    Import-Module (Join-Path $repoRoot 'powershell/orchestrator/AdgOrchestrator.psd1') -Force

    $script:DC01 = 'CN=NTDS Settings,CN=DC01|2f0f9a3c-7c4e-4c0e-9a02-6b5f0a1f9d11'

    function New-Workspace {
        $path = Join-Path ([System.IO.Path]::GetTempPath()) "adg-orch-$([guid]::NewGuid())"
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        return $path
    }

    function New-TestConfig {
        param([string] $StateDirectory, [object[]] $Jobs)
        return [pscustomobject]@{
            Path                        = 'memory'
            StateDirectory              = $StateDirectory
            ApiBaseUrl                  = 'https://adg.example'
            ApiTokenEnvironmentVariable = 'ADG_COLLECTOR_TOKEN'
            CollectorHost               = 'COLLECTOR01'
            Jobs                        = $Jobs
        }
    }

    function New-Checkpoint {
        param([string] $Token, [string] $Issuer = $script:DC01)
        return [pscustomobject]@{
            Kind     = 'usn'
            Token    = $Token
            Issuer   = $Issuer
            IssuedAt = '2026-03-02T09:05:00Z'
        }
    }

    function New-FakeInvoker {
        <#
            A handler table that records what it was asked to do and returns whatever the
            test told it to. This is the seam the runner takes by injection: every rule
            about locks, retries, modes and checkpoints is exercised without a directory, a
            file server or an API.
        #>
        param([object[]] $Results)

        $calls = [System.Collections.Generic.List[object]]::new()
        $queue = [System.Collections.Generic.Queue[object]]::new()
        foreach ($result in @($Results)) { $queue.Enqueue($result) }

        $handler = {
            param($Request)
            $calls.Add([pscustomobject]@{
                    Job      = $Request.Job.Name
                    Mode     = $Request.Mode
                    Baseline = $Request.Baseline
                    Attempt  = $Request.Attempt
                })
            if ($queue.Count -eq 0) { throw 'The fake invoker ran out of results.' }
            $next = $queue.Dequeue()
            if ($next -is [string]) { throw $next }
            return $next
        }.GetNewClosure()

        return @{
            CollectorRoot = 'unused'
            Config        = $null
            RunScript     = { param($Script, $Arguments) throw 'not used' }
            Calls         = $calls
            Handlers      = @{
                ad_principals        = $handler
                ad_memberships       = $handler
                smb_inventory        = $handler
                ntfs_important_roots = $handler
                ntfs_deep_scan       = $handler
                full_reconciliation  = $handler
            }
        }
    }

    function New-Result {
        param(
            [string] $Status = 'succeeded',
            [int] $Observations = 10,
            [object] $Checkpoint,
            [int] $Errors = 0
        )
        return [pscustomobject]@{
            Status           = $Status
            RunId            = [guid]::NewGuid().ToString()
            StartedAt        = '2026-03-02T09:00:00Z'
            EndedAt          = '2026-03-02T09:05:00Z'
            ObservationCount = $Observations
            AffirmationCount = 0
            ErrorCount       = $Errors
            Reconciled       = $false
            Checkpoint       = $Checkpoint
            Message          = ''
        }
    }

    $script:NoSleep = { param($Seconds) }
    $script:Now = [datetime]::new(2026, 3, 2, 9, 0, 0, [System.DateTimeKind]::Utc)
}

Describe 'A clean run records its checkpoint' {
    It 'writes the cursor the run reached' {
        $state = New-Workspace
        $job = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m'
        $config = New-TestConfig -StateDirectory $state -Jobs @($job)
        $invoker = New-FakeInvoker -Results @((New-Result -Checkpoint (New-Checkpoint '4711')))

        $outcome = Invoke-AdgJob -Job $job -Config $config -Invoker $invoker -Now $script:Now -Sleep $script:NoSleep

        $outcome.Status | Should -Be 'succeeded'
        $saved = Read-AdgJobState -StateDirectory $state -JobName 'ad-principals'
        $saved.Checkpoint.Token | Should -Be '4711'
        $saved.ConsecutiveFailures | Should -Be 0
    }

    It 'resumes from it on the next run' {
        $state = New-Workspace
        $job = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m'
        $config = New-TestConfig -StateDirectory $state -Jobs @($job)

        $first = New-FakeInvoker -Results @((New-Result -Checkpoint (New-Checkpoint '4711')))
        Invoke-AdgJob -Job $job -Config $config -Invoker $first -Now $script:Now -Sleep $script:NoSleep | Out-Null

        $second = New-FakeInvoker -Results @((New-Result -Checkpoint (New-Checkpoint '4900')))
        $outcome = Invoke-AdgJob -Job $job -Config $config -Invoker $second `
            -Now $script:Now.AddHours(1) -Sleep $script:NoSleep

        $outcome.Mode | Should -Be 'delta'
        $second.Calls[0].Baseline.Token | Should -Be '4711'
    }
}

Describe 'A failed incremental run cannot declare the source current' {
    It 'does not retry a partial run, which would re-read everything to be denied again' {
        $state = New-Workspace
        $job = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m' -MaxAttempts 3
        $config = New-TestConfig -StateDirectory $state -Jobs @($job)
        $invoker = New-FakeInvoker -Results @((New-Result -Status 'partial' -Errors 1))

        $outcome = Invoke-AdgJob -Job $job -Config $config -Invoker $invoker -Now $script:Now -Sleep $script:NoSleep

        $outcome.Status | Should -Be 'partial'
        $outcome.Attempts | Should -Be 1
        $invoker.Calls.Count | Should -Be 1
    }

    It 'does not record the checkpoint of a partial run' {
        # The acceptance criterion, on the collector side. A partial run did not read
        # everything below its watermark, so a later run starting there would skip exactly
        # what this one failed on -- and the gap would never be noticed, because nothing
        # afterwards would look missing.
        $state = New-Workspace
        $job = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m'
        $config = New-TestConfig -StateDirectory $state -Jobs @($job)

        $clean = New-FakeInvoker -Results @((New-Result -Checkpoint (New-Checkpoint '4711')))
        Invoke-AdgJob -Job $job -Config $config -Invoker $clean -Now $script:Now -Sleep $script:NoSleep | Out-Null

        $dirty = New-FakeInvoker -Results @(
            (New-Result -Status 'partial' -Errors 1 -Checkpoint (New-Checkpoint '9000'))
        )
        Invoke-AdgJob -Job $job -Config $config -Invoker $dirty `
            -Now $script:Now.AddHours(1) -Sleep $script:NoSleep -WarningAction SilentlyContinue | Out-Null

        $saved = Read-AdgJobState -StateDirectory $state -JobName 'ad-principals'
        $saved.Checkpoint.Token | Should -Be '4711'
        $saved.LastStatus | Should -Be 'partial'
    }

    It 'makes the run after a partial one read everything' {
        $state = New-Workspace
        $job = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m'
        $config = New-TestConfig -StateDirectory $state -Jobs @($job)

        $clean = New-FakeInvoker -Results @((New-Result -Checkpoint (New-Checkpoint '4711')))
        Invoke-AdgJob -Job $job -Config $config -Invoker $clean -Now $script:Now -Sleep $script:NoSleep | Out-Null

        $dirty = New-FakeInvoker -Results @((New-Result -Status 'partial' -Errors 1))
        Invoke-AdgJob -Job $job -Config $config -Invoker $dirty `
            -Now $script:Now.AddHours(1) -Sleep $script:NoSleep | Out-Null

        $third = New-FakeInvoker -Results @((New-Result))
        $outcome = Invoke-AdgJob -Job $job -Config $config -Invoker $third `
            -Now $script:Now.AddHours(2) -Sleep $script:NoSleep

        $outcome.Mode | Should -Be 'full'
        $third.Calls[0].Baseline | Should -BeNullOrEmpty
    }

    It 'counts consecutive failures, so a job that is quietly stuck can be seen' {
        $state = New-Workspace
        $job = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m' -MaxAttempts 1
        $config = New-TestConfig -StateDirectory $state -Jobs @($job)

        foreach ($offset in 1..3) {
            $invoker = New-FakeInvoker -Results @('the directory is unreachable')
            Invoke-AdgJob -Job $job -Config $config -Invoker $invoker `
                -Now $script:Now.AddHours($offset) -Sleep $script:NoSleep | Out-Null
        }

        (Read-AdgJobState -StateDirectory $state -JobName 'ad-principals').ConsecutiveFailures |
            Should -Be 3
    }
}

Describe 'Retry' {
    It 'retries a transient failure and succeeds' {
        $state = New-Workspace
        $job = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m' -MaxAttempts 3
        $config = New-TestConfig -StateDirectory $state -Jobs @($job)
        $invoker = New-FakeInvoker -Results @(
            'The RPC server is unavailable.'
            (New-Result -Checkpoint (New-Checkpoint '4711'))
        )

        $delays = [System.Collections.Generic.List[object]]::new()
        $sleep = { param($Seconds) $delays.Add($Seconds) }.GetNewClosure()

        $outcome = Invoke-AdgJob -Job $job -Config $config -Invoker $invoker -Now $script:Now `
            -Sleep $sleep -WarningAction SilentlyContinue

        $outcome.Status | Should -Be 'succeeded'
        $outcome.Attempts | Should -Be 2
        $delays.Count | Should -Be 1
        $delays[0] | Should -BeGreaterThan 0
    }

    It 'does not retry a failure a retry cannot fix' {
        $state = New-Workspace
        $job = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m' -MaxAttempts 3
        $config = New-TestConfig -StateDirectory $state -Jobs @($job)
        $invoker = New-FakeInvoker -Results @(
            'POST was rejected with HTTP 422 and retrying cannot help: bad payload'
        )

        $outcome = Invoke-AdgJob -Job $job -Config $config -Invoker $invoker -Now $script:Now `
            -Sleep $script:NoSleep

        $outcome.Status | Should -Be 'failed'
        $outcome.Attempts | Should -Be 1
        $outcome.Message | Should -Match 'Not retried'
        $invoker.Calls.Count | Should -Be 1
    }

    It 'gives up after maxAttempts' {
        $state = New-Workspace
        $job = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m' -MaxAttempts 2
        $config = New-TestConfig -StateDirectory $state -Jobs @($job)
        $invoker = New-FakeInvoker -Results @('Server Down', 'Server Down')

        $outcome = Invoke-AdgJob -Job $job -Config $config -Invoker $invoker -Now $script:Now `
            -Sleep $script:NoSleep -WarningAction SilentlyContinue

        $outcome.Status | Should -Be 'failed'
        $invoker.Calls.Count | Should -Be 2
    }

    It 'treats a collector that returned nothing as a failure, never as success' {
        $state = New-Workspace
        $job = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m' -MaxAttempts 1
        $config = New-TestConfig -StateDirectory $state -Jobs @($job)
        $invoker = New-FakeInvoker -Results @($null)

        $outcome = Invoke-AdgJob -Job $job -Config $config -Invoker $invoker -Now $script:Now -Sleep $script:NoSleep
        $outcome.Status | Should -Be 'failed'
        $outcome.Message | Should -Match 'no run summary'
    }
}

Describe 'The job lock' {
    It 'skips a job another process is already running' {
        $state = New-Workspace
        $job = New-AdgJobDefinition -Name 'deep' -Kind 'ntfs_deep_scan' -Every '7d'
        $config = New-TestConfig -StateDirectory $state -Jobs @($job)

        $held = Enter-AdgJobLock -StateDirectory $state -JobName 'deep'
        try {
            $invoker = New-FakeInvoker -Results @((New-Result))
            $outcome = Invoke-AdgJob -Job $job -Config $config -Invoker $invoker -Now $script:Now -Sleep $script:NoSleep

            $outcome.Status | Should -Be 'skipped'
            $outcome.Reason | Should -Match 'already running'
            $invoker.Calls.Count | Should -Be 0
        }
        finally {
            Exit-AdgJobLock -Lock $held
        }
    }

    It 'releases the lock when the job throws, so the next invocation is not blocked forever' {
        $state = New-Workspace
        $job = New-AdgJobDefinition -Name 'deep' -Kind 'ntfs_deep_scan' -Every '7d' -MaxAttempts 1
        $config = New-TestConfig -StateDirectory $state -Jobs @($job)

        $first = New-FakeInvoker -Results @('exploded')
        Invoke-AdgJob -Job $job -Config $config -Invoker $first -Now $script:Now -Sleep $script:NoSleep | Out-Null

        $second = New-FakeInvoker -Results @((New-Result))
        $outcome = Invoke-AdgJob -Job $job -Config $config -Invoker $second `
            -Now $script:Now.AddDays(8) -Sleep $script:NoSleep
        $outcome.Status | Should -Be 'succeeded'
    }
}

Describe 'A scheduled invocation' {
    BeforeEach {
        $script:State = New-Workspace
        $script:Jobs = @(
            New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m'
            New-AdgJobDefinition -Name 'smb-inventory' -Kind 'smb_inventory' -Every '6h' -Reconcile $true
            New-AdgJobDefinition -Name 'deep' -Kind 'ntfs_deep_scan' -Every '7d' -Enabled $false
        )
        $script:Config = New-TestConfig -StateDirectory $script:State -Jobs $script:Jobs
    }

    It 'runs every job that is due and skips the rest' {
        $invoker = New-FakeInvoker -Results @((New-Result), (New-Result))
        $run = Invoke-AdgCollectionRun -Config $script:Config -Invoker $invoker -Now $script:Now -Sleep $script:NoSleep

        $run.Succeeded | Should -Be 2
        $run.Skipped | Should -Be 1
        $run.Outcomes.Count | Should -Be 3
    }

    It 'keeps going after a job fails, because the jobs are independent' {
        $script:Jobs[0] = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' `
            -Every '15m' -MaxAttempts 1
        $script:Config = New-TestConfig -StateDirectory $script:State -Jobs $script:Jobs
        $invoker = New-FakeInvoker -Results @('the directory is unreachable', (New-Result))
        $run = Invoke-AdgCollectionRun -Config $script:Config -Invoker $invoker -Now $script:Now `
            -Sleep $script:NoSleep -WarningAction SilentlyContinue

        $run.Failed | Should -Be 1
        $run.Succeeded | Should -Be 1
        ($run.Outcomes | Where-Object { $_.Job -eq 'smb-inventory' }).Status | Should -Be 'succeeded'
    }

    It 'plans without running under -WhatIf, and writes nothing' {
        $invoker = New-FakeInvoker -Results @()
        $run = Invoke-AdgCollectionRun -Config $script:Config -Invoker $invoker -Now $script:Now `
            -WhatIf -Sleep $script:NoSleep

        $run.Planned | Should -Be 2
        $invoker.Calls.Count | Should -Be 0
        @(Get-ChildItem -Path $script:State -Filter '*.state.json').Count | Should -Be 0
    }

    It 'refuses a job name that is not configured, naming the ones that are' {
        $invoker = New-FakeInvoker -Results @()
        {
            Invoke-AdgCollectionRun -Config $script:Config -Invoker $invoker -Now $script:Now `
                -JobName 'ntfs-important-roots' -Sleep $script:NoSleep
        } | Should -Throw '*No job named*'
    }

    It 'runs only the named job' {
        $invoker = New-FakeInvoker -Results @((New-Result))
        $run = Invoke-AdgCollectionRun -Config $script:Config -Invoker $invoker -Now $script:Now `
            -JobName 'ad-principals' -Sleep $script:NoSleep
        $run.Outcomes.Count | Should -Be 1
        $run.Outcomes[0].Job | Should -Be 'ad-principals'
    }
}

Describe 'The state file' {
    It 'survives a process that died mid-write, by never being half-written' {
        # The write is a rename. A reader either sees the previous document or the new one,
        # and a leftover .tmp is not a state file at all.
        $state = New-Workspace
        $job = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m'
        $config = New-TestConfig -StateDirectory $state -Jobs @($job)
        $invoker = New-FakeInvoker -Results @((New-Result -Checkpoint (New-Checkpoint '4711')))
        Invoke-AdgJob -Job $job -Config $config -Invoker $invoker -Now $script:Now -Sleep $script:NoSleep | Out-Null

        $path = Get-AdgJobStatePath -StateDirectory $state -JobName 'ad-principals'
        Test-Path -LiteralPath $path | Should -BeTrue
        Test-Path -LiteralPath "$path.tmp" | Should -BeFalse
    }

    It 'reports an unparsable state file as no state, and warns' {
        # No state means the next run reads everything, which is exactly right when the
        # resume point cannot be trusted. The warning is there because an installation
        # silently doing full scans for ever is a cost somebody should be told about.
        $state = New-Workspace
        Set-Content -LiteralPath (Get-AdgJobStatePath -StateDirectory $state -JobName 'x') `
            -Value '{ not json' -Encoding utf8NoBOM

        $result = Read-AdgJobState -StateDirectory $state -JobName 'x' -WarningVariable warnings -WarningAction SilentlyContinue
        $result | Should -BeNullOrEmpty
        $warnings.Count | Should -BeGreaterThan 0
    }

    It 'reports a state file of an unknown format as no state' {
        $state = New-Workspace
        @{ schema = 'adg-orchestrator-state/99'; job = 'x' } | ConvertTo-Json |
            Set-Content -LiteralPath (Get-AdgJobStatePath -StateDirectory $state -JobName 'x') -Encoding utf8NoBOM

        Read-AdgJobState -StateDirectory $state -JobName 'x' -WarningAction SilentlyContinue |
            Should -BeNullOrEmpty
    }
}
