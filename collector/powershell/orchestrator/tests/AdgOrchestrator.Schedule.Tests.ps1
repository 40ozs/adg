<#
    When a job is due, and what mode it runs in.

    Every decision here is a pure function of (job, saved state, clock), which is what makes
    it testable at all: scheduling is the part of an orchestrator nobody is watching when it
    goes wrong, because a job that never runs and a job that runs and finds nothing look
    identical from outside.
#>

BeforeAll {
    $repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
    Import-Module (Join-Path $repoRoot 'powershell/orchestrator/AdgOrchestrator.psd1') -Force

    $script:DC01 = 'CN=NTDS Settings,CN=DC01|2f0f9a3c-7c4e-4c0e-9a02-6b5f0a1f9d11'
    $script:DC02 = 'CN=NTDS Settings,CN=DC02|8a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d'
    # The same controller restored from backup: same name, new invocation id, and a USN
    # counter that has rolled backwards and is reissuing numbers it already handed out.
    $script:DC01_RESTORED = 'CN=NTDS Settings,CN=DC01|c0ffee00-1111-4222-8333-444455556666'

    function New-State {
        param(
            [string] $Status = 'succeeded',
            [Nullable[datetime]] $EndedAt,
            [string] $Token,
            [string] $Issuer = $script:DC01
        )
        return [pscustomobject]@{
            Job                 = 'test'
            LastRunId           = [guid]::NewGuid().ToString()
            LastStatus          = $Status
            LastMode            = 'delta'
            LastStartedAt       = $EndedAt
            LastEndedAt         = $EndedAt
            LastReconciledAt    = $null
            ConsecutiveFailures = 0
            Checkpoint          = if ($Token) {
                [pscustomobject]@{ Kind = 'usn'; Token = $Token; Issuer = $Issuer; IssuedAt = '2026-03-02T09:00:00Z' }
            }
            else { $null }
            Digests             = ''
        }
    }
}

Describe 'A job is due when its interval has elapsed' {
    BeforeEach {
        $script:Job = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m'
        $script:Now = [datetime]::new(2026, 3, 2, 9, 30, 0, [System.DateTimeKind]::Utc)
    }

    It 'is due when it has never completed here' {
        $verdict = Test-AdgJobDue -Job $script:Job -State $null -Now $script:Now
        $verdict.Due | Should -BeTrue
        $verdict.Reason | Should -Match 'never completed'
    }

    It 'is not due inside the interval, and says how far inside' {
        $state = New-State -EndedAt $script:Now.AddMinutes(-12)
        $verdict = Test-AdgJobDue -Job $script:Job -State $state -Now $script:Now
        $verdict.Due | Should -BeFalse
        $verdict.Reason | Should -Match '12m ago, interval 15m'
    }

    It 'is due once the interval has passed' {
        $state = New-State -EndedAt $script:Now.AddMinutes(-16)
        (Test-AdgJobDue -Job $script:Job -State $state -Now $script:Now).Due | Should -BeTrue
    }

    It 'is not due when disabled' {
        $job = New-AdgJobDefinition -Name 'x' -Kind 'ad_principals' -Every '15m' -Enabled $false
        (Test-AdgJobDue -Job $job -State $null -Now $script:Now).Reason | Should -Match 'disabled'
    }

    It 'runs a disabled job when forced, so an operator can invoke one by name' {
        $job = New-AdgJobDefinition -Name 'x' -Kind 'ad_principals' -Every '15m' -Enabled $false
        (Test-AdgJobDue -Job $job -State $null -Now $script:Now -Force).Due | Should -BeTrue
    }
}

Describe 'The onlyBetween window' {
    BeforeEach {
        $script:Job = New-AdgJobDefinition -Name 'deep' -Kind 'ntfs_deep_scan' -Every '7d' `
            -OnlyBetweenStart '22:00' -OnlyBetweenEnd '06:00'
    }

    It 'is inside the window at <time>' -ForEach @(
        @{ Time = '22:00' }, @{ Time = '23:59' }, @{ Time = '00:30' }, @{ Time = '05:59' }
    ) {
        $parts = $Time.Split(':')
        $now = [datetime]::new(2026, 3, 2, [int] $parts[0], [int] $parts[1], 0)
        Test-AdgInWindow -WindowStart $script:Job.WindowStart -WindowEnd $script:Job.WindowEnd -Now $now |
            Should -BeTrue
    }

    It 'is outside the window at <time>' -ForEach @(
        @{ Time = '06:00' }, @{ Time = '10:00' }, @{ Time = '21:59' }
    ) {
        $parts = $Time.Split(':')
        $now = [datetime]::new(2026, 3, 2, [int] $parts[0], [int] $parts[1], 0)
        Test-AdgInWindow -WindowStart $script:Job.WindowStart -WindowEnd $script:Job.WindowEnd -Now $now |
            Should -BeFalse
    }

    It 'holds the window even under -Force, because "run it now" is not "run it during business hours"' {
        $now = [datetime]::new(2026, 3, 2, 10, 0, 0)
        $verdict = Test-AdgJobDue -Job $script:Job -State $null -Now $now -Force
        $verdict.Due | Should -BeFalse
        $verdict.Reason | Should -Match '22:00-06:00'
    }

    It 'ignores the window when that is asked for explicitly' {
        $now = [datetime]::new(2026, 3, 2, 10, 0, 0)
        (Test-AdgJobDue -Job $script:Job -State $null -Now $now -Force -IgnoreWindow).Due | Should -BeTrue
    }
}

Describe 'Resolving the mode' {
    BeforeEach {
        $script:Delta = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m'
    }

    It 'is a delta when a clean run left a usable checkpoint' {
        $plan = Resolve-AdgJobMode -Job $script:Delta -State (New-State -Token '4711') -Issuer $script:DC01
        $plan.Mode | Should -Be 'delta'
        $plan.Baseline.Token | Should -Be '4711'
    }

    It 'is full on the first run, because there is no point to resume from' {
        $plan = Resolve-AdgJobMode -Job $script:Delta -State $null
        $plan.Mode | Should -Be 'full'
        $plan.Reason | Should -Match 'no checkpoint'
    }

    It 'is full when the last run did not succeed' {
        # A partial run did not read everything below its watermark, so resuming there would
        # skip exactly what it failed on -- and nothing afterwards would look missing.
        $state = New-State -Status 'partial' -Token '4711'
        $plan = Resolve-AdgJobMode -Job $script:Delta -State $state -Issuer $script:DC01
        $plan.Mode | Should -Be 'full'
        $plan.Reason | Should -Match "finished as 'partial'"
    }

    It 'is full when the checkpoint came from a different domain controller' {
        $state = New-State -Token '4711' -Issuer $script:DC01
        $plan = Resolve-AdgJobMode -Job $script:Delta -State $state -Issuer $script:DC02
        $plan.Mode | Should -Be 'full'
        $plan.Reason | Should -Match 'per-server cursor does not transfer'
    }

    It 'is full when the same controller was restored from backup' {
        # Same dsServiceName, new invocationId. The USN counter rolled backwards and the DC
        # is reissuing numbers, so a watermark compared on the server name alone would
        # survive the restore and skip every reused number.
        $state = New-State -Token '9000' -Issuer $script:DC01
        $plan = Resolve-AdgJobMode -Job $script:Delta -State $state -Issuer $script:DC01_RESTORED
        $plan.Mode | Should -Be 'full'
    }

    It 'is full for a source with no change metadata, whatever the state says' {
        $job = New-AdgJobDefinition -Name 'smb' -Kind 'smb_inventory' -Every '6h'
        $plan = Resolve-AdgJobMode -Job $job -State (New-State -Token '4711')
        $plan.Mode | Should -Be 'full'
        $plan.Reason | Should -Match 'publishes no change metadata'
    }

    It 'is reconcile for the repair pass' {
        $job = New-AdgJobDefinition -Name 'repair' -Kind 'full_reconciliation' -Every '7d' -Reconcile $true
        (Resolve-AdgJobMode -Job $job -State $null).Mode | Should -Be 'reconcile'
    }

    It 'is full for a job that merely reconciles, not reconcile' {
        # smb_inventory and ntfs_deep_scan reconcile as part of ordinary collection. Calling
        # either of them a 'reconcile' run would leave an operator reading a non-zero drift
        # number unable to tell a scheduled repair from a routine scan, which is the only
        # thing the third mode is for.
        foreach ($kind in @('smb_inventory', 'ntfs_deep_scan')) {
            $job = New-AdgJobDefinition -Name $kind -Kind $kind -Every '6h' -Reconcile $true
            $job.Reconcile | Should -BeTrue
            $job.IsRepairPass | Should -BeFalse
            (Resolve-AdgJobMode -Job $job -State $null).Mode | Should -Be 'full'
        }
    }

    It 'refuses strategy=delta with no checkpoint rather than silently reading everything' {
        $job = New-AdgJobDefinition -Name 'ad-p' -Kind 'ad_principals' -Every '15m' -Strategy 'delta'
        { Resolve-AdgJobMode -Job $job -State $null } | Should -Throw "*Run it once as 'full'*"
    }
}

Describe 'Backoff' {
    It 'grows exponentially from the base' {
        $first = Get-AdgRetryDelaySeconds -Attempt 1 -BaseSeconds 30 -Jitter 0
        $second = Get-AdgRetryDelaySeconds -Attempt 2 -BaseSeconds 30 -Jitter 0
        $third = Get-AdgRetryDelaySeconds -Attempt 3 -BaseSeconds 30 -Jitter 0
        $second | Should -BeGreaterThan $first
        $third | Should -BeGreaterThan $second
    }

    It 'is capped' {
        Get-AdgRetryDelaySeconds -Attempt 20 -BaseSeconds 30 -CapSeconds 300 -Jitter 1 |
            Should -BeLessOrEqual 300
    }

    It 'spreads the retries of a fleet recovering from one outage' {
        # Without jitter, twenty collector hosts on the same schedule retry in lockstep and
        # restart the outage they are recovering from.
        $low = Get-AdgRetryDelaySeconds -Attempt 2 -BaseSeconds 30 -Jitter 0
        $high = Get-AdgRetryDelaySeconds -Attempt 2 -BaseSeconds 30 -Jitter 1
        $high | Should -BeGreaterThan $low
    }

    It 'never returns a delay of zero, whatever the jitter' {
        Get-AdgRetryDelaySeconds -Attempt 1 -BaseSeconds 1 -Jitter 0 | Should -BeGreaterThan 0
    }
}

Describe 'What a retry can and cannot fix' {
    It 'retries <case>' -ForEach @(
        @{ Case = 'a timeout'; Message = 'The RPC server is unavailable.' }
        @{ Case = 'a 503'; Message = 'POST failed after 5 attempt(s) (last status 503)' }
        @{ Case = 'a transient bind failure'; Message = 'Server Down' }
    ) {
        Test-AdgRetryableJobFailure -Message $Message | Should -BeTrue
    }

    It 'does not retry <case>, because the second attempt fails identically' -ForEach @(
        @{ Case = 'a rejected payload'; Message = 'POST ... was rejected with HTTP 422 and retrying cannot help: ...' }
        @{ Case = 'a bad job kind'; Message = "'ntfs_shallow' is not a job kind." }
        @{ Case = 'a missing configuration'; Message = "Collector configuration 'x.json' does not exist." }
        @{ Case = 'a refused reconciliation'; Message = 'Run ... may not reconcile any scope' }
        @{ Case = 'a rejected credential'; Message = 'HTTP 401' }
    ) {
        Test-AdgRetryableJobFailure -Message $Message | Should -BeFalse
    }
}
