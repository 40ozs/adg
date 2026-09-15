<#
    What a configuration is allowed to say, and every refusal.

    A configuration error found at run time is found at 02:00 by nobody, so all of it is
    validated at load. These tests are the specification of what "valid" means.
#>

BeforeAll {
    $repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
    Import-Module (Join-Path $repoRoot 'powershell/orchestrator/AdgOrchestrator.psd1') -Force

    $script:ExamplePath = Join-Path $repoRoot 'powershell/orchestrator/adg-orchestrator.example.json'

    function New-TestConfigFile {
        param([hashtable] $Document)
        $path = Join-Path ([System.IO.Path]::GetTempPath()) "adg-orch-$([guid]::NewGuid()).json"
        $Document | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $path -Encoding utf8NoBOM
        return $path
    }

    function New-BaseDocument {
        param([hashtable[]] $Jobs)
        return @{
            schema         = 'adg-orchestrator/1'
            stateDirectory = 'C:\ProgramData\ADG\state'
            jobs           = $Jobs
        }
    }
}

Describe 'Durations' {
    It 'reads <text> as <expected> minutes' -ForEach @(
        @{ Text = '90s'; Expected = 1.5 }
        @{ Text = '15m'; Expected = 15 }
        @{ Text = '6h'; Expected = 360 }
        @{ Text = '7d'; Expected = 10080 }
    ) {
        (ConvertFrom-AdgDuration -Text $Text).TotalMinutes | Should -Be $Expected
    }

    It 'refuses a bare number, because the unit is the difference between 30 seconds and 30 minutes' {
        { ConvertFrom-AdgDuration -Text '30' } | Should -Throw '*number followed by*'
    }

    It 'refuses a zero duration' {
        { ConvertFrom-AdgDuration -Text '0m' } | Should -Throw '*always due*'
    }

    It 'refuses an unknown unit' {
        { ConvertFrom-AdgDuration -Text '2w' } | Should -Throw '*not a duration*'
    }
}

Describe 'The job catalogue' {
    It 'names the six kinds the phase requires' {
        (Get-AdgJobKinds).Keys | Sort-Object | Should -Be @(
            'ad_memberships', 'ad_principals', 'full_reconciliation',
            'ntfs_deep_scan', 'ntfs_important_roots', 'smb_inventory'
        )
    }

    It 'returns a copy, so a caller cannot widen what a later job may do' {
        $first = Get-AdgJobKinds
        $first['smb_inventory'].MayReconcile = $true
        $first['ad_principals'].MayReconcile = $true
        (Get-AdgJobKinds)['ad_principals'].MayReconcile | Should -BeFalse
    }

    It 'refuses an unknown kind and names the ones that exist' {
        { Get-AdgJobKind -Kind 'ntfs_shallow' } | Should -Throw '*is not a job kind*'
    }

    It 'lets only the whole-scope kinds reconcile' {
        $kinds = Get-AdgJobKinds
        $kinds['ad_principals'].MayReconcile | Should -BeFalse
        $kinds['ad_memberships'].MayReconcile | Should -BeFalse
        $kinds['ntfs_important_roots'].MayReconcile | Should -BeFalse
        $kinds['smb_inventory'].MayReconcile | Should -BeTrue
        $kinds['ntfs_deep_scan'].MayReconcile | Should -BeTrue
        $kinds['full_reconciliation'].MayReconcile | Should -BeTrue
    }

    It 'offers a delta only where the source publishes change metadata' {
        $kinds = Get-AdgJobKinds
        $kinds['ad_principals'].SupportsDelta | Should -BeTrue
        $kinds['ad_memberships'].SupportsDelta | Should -BeTrue
        $kinds['smb_inventory'].SupportsDelta | Should -BeFalse
        $kinds['ntfs_deep_scan'].SupportsDelta | Should -BeFalse
    }
}

Describe 'A job definition' {
    It 'accepts the ordinary case' {
        $job = New-AdgJobDefinition -Name 'ad-principals' -Kind 'ad_principals' -Every '15m'
        $job.Collector | Should -Be 'active_directory'
        $job.Every.TotalMinutes | Should -Be 15
        $job.AlwaysIncremental | Should -BeTrue
    }

    It 'refuses a name that cannot be a file name, because it becomes one' {
        { New-AdgJobDefinition -Name 'ad/principals' -Kind 'ad_principals' -Every '15m' } |
            Should -Throw '*not usable*'
    }

    It 'refuses a blank name' {
        { New-AdgJobDefinition -Name '' -Kind 'ad_principals' -Every '15m' } | Should -Throw '*needs a name*'
    }

    It 'refuses a delta strategy on a source with no change metadata' {
        { New-AdgJobDefinition -Name 'smb' -Kind 'smb_inventory' -Every '6h' -Strategy 'delta' } |
            Should -Throw '*publishes no change metadata*'
    }

    It 'refuses reconciliation on a job that reads half its scope' {
        { New-AdgJobDefinition -Name 'ad-p' -Kind 'ad_principals' -Every '15m' -Reconcile $true } |
            Should -Throw '*may not reconcile*'
    }

    It 'keeps a half-scope job incremental however it is configured' {
        $job = New-AdgJobDefinition -Name 'ad-m' -Kind 'ad_memberships' -Every '15m' -Strategy 'full'
        $job.AlwaysIncremental | Should -BeTrue
        $job.Reconcile | Should -BeFalse
    }

    It 'refuses an enabled job with no interval, because nothing would decide when it is due' {
        { New-AdgJobDefinition -Name 'ad-p' -Kind 'ad_principals' } | Should -Throw '*no*interval*'
    }

    It 'allows a disabled job with no interval, so it can be invoked by name' {
        $job = New-AdgJobDefinition -Name 'deep' -Kind 'ntfs_deep_scan' -Enabled $false
        $job.Enabled | Should -BeFalse
    }

    It 'refuses half an onlyBetween window' {
        { New-AdgJobDefinition -Name 'deep' -Kind 'ntfs_deep_scan' -Every '7d' -OnlyBetweenStart '22:00' } |
            Should -Throw '*only one end*'
    }

    It 'refuses a window that starts and ends at the same minute' {
        {
            New-AdgJobDefinition -Name 'deep' -Kind 'ntfs_deep_scan' -Every '7d' `
                -OnlyBetweenStart '22:00' -OnlyBetweenEnd '22:00'
        } | Should -Throw '*same minute*'
    }

    It 'refuses important roots with no roots' {
        { New-AdgJobDefinition -Name 'roots' -Kind 'ntfs_important_roots' -Every '6h' } |
            Should -Throw '*names none*'
    }

    It 'refuses maxAttempts below one' {
        {
            New-AdgJobDefinition -Name 'ad-p' -Kind 'ad_principals' -Every '15m' -MaxAttempts 0
        } | Should -Throw '*may not be attempted*'
    }

    It 'refuses a retry with no delay' {
        {
            New-AdgJobDefinition -Name 'ad-p' -Kind 'ad_principals' -Every '15m' -RetryBaseSeconds 0
        } | Should -Throw '*re-attacks a source*'
    }
}

Describe 'Loading a configuration file' {
    It 'loads the shipped example' {
        $config = Import-AdgOrchestratorConfig -Path $script:ExamplePath
        $config.Jobs.Count | Should -Be 6
        @($config.Jobs | Where-Object { $_.Reconcile }).Name | Should -Contain 'full-reconciliation'
    }

    It 'gives the example every kind the phase requires' {
        $config = Import-AdgOrchestratorConfig -Path $script:ExamplePath
        $config.Jobs.Kind | Sort-Object | Should -Be @(
            'ad_memberships', 'ad_principals', 'full_reconciliation',
            'ntfs_deep_scan', 'ntfs_important_roots', 'smb_inventory'
        )
    }

    It 'refuses a file that does not exist' {
        { Import-AdgOrchestratorConfig -Path 'C:\nowhere\adg.json' } | Should -Throw '*does not exist*'
    }

    It 'refuses a different schema' {
        $path = New-TestConfigFile (New-BaseDocument @(@{ name = 'a'; kind = 'ad_principals'; every = '15m' }))
        (Get-Content -LiteralPath $path -Raw).Replace('adg-orchestrator/1', 'adg-orchestrator/2') |
            Set-Content -LiteralPath $path -Encoding utf8NoBOM
        { Import-AdgOrchestratorConfig -Path $path } | Should -Throw '*declares schema*'
    }

    It 'refuses a configuration with no stateDirectory, because a delta would have nowhere to resume from' {
        $document = New-BaseDocument @(@{ name = 'a'; kind = 'ad_principals'; every = '15m' })
        $document.Remove('stateDirectory')
        { Import-AdgOrchestratorConfig -Path (New-TestConfigFile $document) } |
            Should -Throw '*no stateDirectory*'
    }

    It 'refuses two jobs with one name, which would share a checkpoint and a lock' {
        $document = New-BaseDocument @(
            @{ name = 'ad'; kind = 'ad_principals'; every = '15m' }
            @{ name = 'AD'; kind = 'ad_memberships'; every = '15m' }
        )
        { Import-AdgOrchestratorConfig -Path (New-TestConfigFile $document) } | Should -Throw '*twice*'
    }

    It 'refuses a configuration with no jobs, which would report success having done nothing' {
        $document = New-BaseDocument @()
        { Import-AdgOrchestratorConfig -Path (New-TestConfigFile $document) } | Should -Throw '*no jobs*'
    }

    It 'fails the whole load for one bad job rather than running the other five' {
        $document = New-BaseDocument @(
            @{ name = 'good'; kind = 'ad_principals'; every = '15m' }
            @{ name = 'bad'; kind = 'smb_inventory'; every = '6h'; strategy = 'delta' }
        )
        { Import-AdgOrchestratorConfig -Path (New-TestConfigFile $document) } |
            Should -Throw '*publishes no change metadata*'
    }
}
