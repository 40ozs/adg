#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Configuration: where a walk starts, how far it goes, and what it refuses to accept.

    The refusals matter more than the acceptances here. A configuration mistake that is
    caught turns into a message naming the field; one that is not turns into a scan that
    reads the wrong thing, or - worse - one that declares a reach it never had.

    Every numeric limit is refused rather than clamped. A maxDepth of 100000 is a typo, not
    a request, and quietly reducing it to something workable produces a scan whose declared
    reach and actual reach differ, which is the one thing a scope must never do.
#>

BeforeAll {
    $moduleRoot = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $moduleRoot 'AdgNtfsCollector.psd1') -Force

    $script:Root = Join-Path ([System.IO.Path]::GetTempPath()) "adg-ntfs-config-$([guid]::NewGuid())"
    New-Item -ItemType Directory -Path $script:Root -Force | Out-Null

    function New-ConfigFile {
        param([Parameter(Mandatory)][hashtable] $Document)
        $path = Join-Path $script:Root "$([guid]::NewGuid()).json"
        $Document | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $path -Encoding utf8
        return $path
    }
}

AfterAll {
    if (Test-Path -LiteralPath $script:Root) {
        Remove-Item -LiteralPath $script:Root -Recurse -Force
    }
}

Describe 'Test-AdgShareRootPath' {
    It 'recognizes a share root in any spelling' -ForEach @(
        @{ Candidate = '\\FS01\Finance' }
        @{ Candidate = '\\FS01\Finance\' }
        @{ Candidate = '//fs01/finance' }
    ) {
        Test-AdgShareRootPath $Candidate | Should -BeTrue
    }

    It 'does not mistake a directory inside a share for the share root' {
        # Still load-bearing after the walk lifted the configuration restriction: a share
        # root is an ACL boundary by fiat, because its parent lies outside the share.
        Test-AdgShareRootPath '\\FS01\Finance\Reports' | Should -BeFalse
    }
}

Describe 'Path patterns' {
    It 'matches the directory a pattern names' {
        Test-AdgPathMatch -Path '\\FS01\Finance\Archive' -Pattern '\\FS01\Finance\Archive' | Should -BeTrue
    }

    It 'matches everything beneath it, which is what an operator means by a subtree' {
        Test-AdgPathMatch -Path '\\FS01\Finance\Archive\2019' -Pattern '\\FS01\Finance\Archive' | Should -BeTrue
    }

    It 'does not match a sibling whose name merely starts the same way' {
        Test-AdgPathMatch -Path '\\FS01\Finance\Archived' -Pattern '\\FS01\Finance\Archive' | Should -BeFalse
    }

    It 'honours a wildcard' {
        Test-AdgPathMatch -Path '\\FS01\Payroll\Archive' -Pattern '\\FS01\*\Archive' | Should -BeTrue
    }

    It 'is case-insensitive, as the file systems these paths name are' {
        Test-AdgPathMatch -Path '\\fs01\finance\archive' -Pattern '\\FS01\Finance\Archive' | Should -BeTrue
    }

    It 'accepts a pattern written with forward slashes or a trailing separator' {
        Test-AdgPathMatch -Path '\\FS01\Finance\Archive' -Pattern '//FS01/Finance/Archive/' | Should -BeTrue
    }

    It 'ignores an empty pattern rather than matching everything' {
        Test-AdgPathMatch -Path '\\FS01\Finance' -Pattern '' | Should -BeFalse
    }
}

Describe 'Test-AdgPatternReachesBelow' {
    It 'sees that a pattern can still match below an ancestor' {
        # The difference between a walk that honours includePaths and one that stops at the
        # first directory outside it.
        Test-AdgPatternReachesBelow -Path '\\FS01\Finance' -Pattern '\\FS01\*\HR' | Should -BeTrue
    }

    It 'sees that it cannot, once the prefix diverges' {
        Test-AdgPatternReachesBelow -Path '\\FS02\Finance' -Pattern '\\FS01\Finance\HR' | Should -BeFalse
    }

    It 'is false for a pattern no deeper than the path' {
        Test-AdgPatternReachesBelow -Path '\\FS01\Finance\HR' -Pattern '\\FS01\Finance' | Should -BeFalse
    }
}

Describe 'Test-AdgPathInScope' {
    It 'reads and descends everything when neither list is set' {
        $scope = Test-AdgPathInScope -Path '\\FS01\Finance\Anything' -Include @() -Exclude @()
        $scope.Read | Should -BeTrue
        $scope.Descend | Should -BeTrue
    }

    It 'stops both at an excluded subtree' {
        $scope = Test-AdgPathInScope -Path '\\FS01\Finance\Archive\2019' `
            -Include @() -Exclude @('\\FS01\Finance\Archive')
        $scope.Read | Should -BeFalse
        $scope.Descend | Should -BeFalse
        $scope.Excluded | Should -BeTrue
    }

    It 'passes through an ancestor of an included path without reporting it' {
        $scope = Test-AdgPathInScope -Path '\\FS01\Finance' `
            -Include @('\\FS01\Finance\Departments\HR') -Exclude @()
        $scope.Read | Should -BeFalse
        $scope.Descend | Should -BeTrue
    }

    It 'reads and descends an included path' {
        $scope = Test-AdgPathInScope -Path '\\FS01\Finance\Departments\HR' `
            -Include @('\\FS01\Finance\Departments\HR') -Exclude @()
        $scope.Read | Should -BeTrue
        $scope.Descend | Should -BeTrue
    }

    It 'stops at a path no include pattern can reach' {
        $scope = Test-AdgPathInScope -Path '\\FS01\Finance\Payroll' `
            -Include @('\\FS01\Finance\Departments\HR') -Exclude @()
        $scope.Read | Should -BeFalse
        $scope.Descend | Should -BeFalse
    }

    It 'lets exclusion win over inclusion' {
        # The narrower instruction is the one that was meant, and the alternative is a rule
        # whose outcome depends on the order two lists happen to be written in.
        $scope = Test-AdgPathInScope -Path '\\FS01\Finance\Archive' `
            -Include @('\\FS01\Finance') -Exclude @('\\FS01\Finance\Archive')
        $scope.Read | Should -BeFalse
        $scope.Excluded | Should -BeTrue
    }
}

Describe 'Import-AdgNtfsTarget' {
    It 'takes roots from the file and the parameter together' {
        $path = New-ConfigFile @{ scanRoots = @('\\FS01\Finance') }
        $settings = Import-AdgNtfsTarget -Path $path -ScanRoot '\\FS02\Departments'

        $settings.ScanRoots | Should -Be @('\\FS01\Finance', '\\FS02\Departments')
    }

    It 'still accepts the Phase 3A spellings, in the file and on the command line' {
        # An existing configuration and an existing command line both keep working. The new
        # name says directory rather than share because a scan root need no longer be one.
        $path = New-ConfigFile @{ shareRoots = @('\\FS01\Finance') }
        $settings = Import-AdgNtfsTarget -Path $path -ShareRoot '\\FS02\Departments'
        $settings.ScanRoots | Should -Be @('\\FS01\Finance', '\\FS02\Departments')
    }

    It 'canonicalizes what it was given' {
        $settings = Import-AdgNtfsTarget -ScanRoot '//fs01/finance/'
        $settings.ScanRoots | Should -Be @('\\fs01\finance')
    }

    It 'collapses two spellings of one root into one target' {
        # Otherwise the run would read the same descriptor twice and declare its scope
        # twice, which the contract rejects as an ambiguous claim of coverage.
        $settings = Import-AdgNtfsTarget -ScanRoot '\\FS01\Finance', '//fs01/FINANCE'
        @($settings.ScanRoots).Count | Should -Be 1
    }

    It 'accepts a path inside a share, which Phase 3A refused' {
        # The tree walk reads the ancestors, so the restriction is lifted. What a deeper
        # start costs is reported rather than hidden: the starting directory's boundary is
        # scan_root, and the run cannot reconcile the share's tree.
        $settings = Import-AdgNtfsTarget -ScanRoot '\\FS01\Finance\Reports'
        $settings.ScanRoots | Should -Be @('\\FS01\Finance\Reports')
    }

    It 'refuses a root that sits inside another root' {
        # Walking both would read every directory beneath the inner one twice, and report it
        # as a scan_root boundary in one run and a properly compared directory in the other.
        { Import-AdgNtfsTarget -ScanRoot '\\FS01\Finance', '\\FS01\Finance\Reports' } |
            Should -Throw '*sits inside*'
    }

    It 'refuses a local path, which does not say which server it is on' {
        { Import-AdgNtfsTarget -ScanRoot 'D:\Shares\Finance' } | Should -Throw '*not a usable scan root*'
    }

    It 'refuses to run with no targets at all' {
        # There is no estate-wide sweep. An auditing tool that walks machines it was never
        # pointed at looks exactly like reconnaissance, and it silently changes what a run's
        # declared scope means.
        { Import-AdgNtfsTarget } | Should -Throw '*does not sweep an estate implicitly*'
    }

    It 'defaults the settings a caller did not state' {
        $settings = Import-AdgNtfsTarget -ScanRoot '\\FS01\Finance'

        $settings.RetryCount | Should -Be 1
        $settings.BatchSize | Should -Be 500
        $settings.MaxDepth | Should -Be 64
        $settings.ConcurrencyLimit | Should -Be 1
        # No deadline: a scan runs to completion rather than stopping part way and leaving
        # the operator to notice.
        $settings.TimeoutSeconds | Should -Be 0
        # Off, and expensive: an estate holds orders of magnitude more files than
        # directories, and the cost is the operator's to accept deliberately.
        $settings.IncludeFiles | Should -BeFalse
        # A junction's own ACL is read; what lies below it belongs to the target.
        $settings.ReparsePointPolicy | Should -Be 'skip'
        $settings.CheckpointPath | Should -BeNullOrEmpty
        # On by default: an orphaned SID on a folder ACL is a finding the backend cannot
        # infer from the ACE alone.
        $settings.ReportUnresolved | Should -BeTrue
    }

    It 'rejects <Field> outside its range rather than clamping it' -ForEach @(
        @{ Field = 'batchSize'; Value = 5000 }
        @{ Field = 'retryCount'; Value = 99 }
        @{ Field = 'retryDelaySeconds'; Value = 9999 }
        @{ Field = 'maxDepth'; Value = 100000 }
        @{ Field = 'concurrencyLimit'; Value = 0 }
        @{ Field = 'timeoutSeconds'; Value = -1 }
        @{ Field = 'checkpointIntervalSeconds'; Value = 0 }
    ) {
        $path = New-ConfigFile @{ scanRoots = @('\\FS01\Finance'); $Field = $Value }
        { Import-AdgNtfsTarget -Path $path } | Should -Throw "*$Field*"
    }

    It 'reads the whole scan policy out of the file' {
        $path = New-ConfigFile @{
            scanRoots                 = @('\\FS01\Finance')
            maxDepth                  = 8
            includePaths              = @('\\FS01\Finance\Departments')
            excludePaths              = @('\\FS01\Finance\Archive')
            reparsePointPolicy        = 'follow'
            concurrencyLimit          = 8
            timeoutSeconds            = 3600
            includeFiles              = $true
            checkpointIntervalSeconds = 120
        }
        $settings = Import-AdgNtfsTarget -Path $path

        $settings.MaxDepth | Should -Be 8
        $settings.IncludePaths | Should -Be @('\\FS01\Finance\Departments')
        $settings.ExcludePaths | Should -Be @('\\FS01\Finance\Archive')
        $settings.ReparsePointPolicy | Should -Be 'follow'
        $settings.ConcurrencyLimit | Should -Be 8
        $settings.TimeoutSeconds | Should -Be 3600
        $settings.IncludeFiles | Should -BeTrue
        $settings.CheckpointIntervalSeconds | Should -Be 120
    }

    It 'accepts a depth of zero, which reads the roots and nothing below them' {
        $path = New-ConfigFile @{ scanRoots = @('\\FS01\Finance'); maxDepth = 0 }
        (Import-AdgNtfsTarget -Path $path).MaxDepth | Should -Be 0
    }

    It 'rejects a reparse policy it does not implement, and names the three that exist' {
        $path = New-ConfigFile @{ scanRoots = @('\\FS01\Finance'); reparsePointPolicy = 'resolve' }
        { Import-AdgNtfsTarget -Path $path } | Should -Throw '*skip*ignore*follow*'
    }

    It 'rejects a path pattern that is not a UNC path' {
        # Patterns are matched against canonical UNC paths, so one that cannot be is one
        # that silently matches nothing - and an exclude that matches nothing is an exclude
        # the operator believes is protecting a subtree.
        $path = New-ConfigFile @{ scanRoots = @('\\FS01\Finance'); excludePaths = @('Archive') }
        { Import-AdgNtfsTarget -Path $path } | Should -Throw '*not a UNC path pattern*'
    }

    It 'refuses a checkpoint path in a directory that does not exist' {
        # A checkpoint that cannot be written is discovered when the scan is interrupted,
        # which is the worst possible moment to find out.
        $path = New-ConfigFile @{
            scanRoots      = @('\\FS01\Finance')
            checkpointPath = (Join-Path $script:Root 'no-such-directory\scan.json')
        }
        { Import-AdgNtfsTarget -Path $path } | Should -Throw '*directory that does not exist*'
    }

    It 'names the file when it is not valid JSON' {
        $path = Join-Path $script:Root 'broken.json'
        Set-Content -LiteralPath $path -Value '{ not json' -Encoding utf8
        { Import-AdgNtfsTarget -Path $path } | Should -Throw '*not valid JSON*'
    }

    It 'says so when the file is not there' {
        { Import-AdgNtfsTarget -Path (Join-Path $script:Root 'absent.json') } |
            Should -Throw '*does not exist*'
    }

    It 'reads the example configuration that ships with the collector' {
        # The example is documentation a user copies; if it stopped parsing, the first thing
        # anybody does with this collector would fail.
        $moduleRoot = Split-Path -Parent $PSScriptRoot
        $settings = Import-AdgNtfsTarget -Path (Join-Path $moduleRoot 'adg-ntfs-targets.example.json')

        @($settings.ScanRoots).Count | Should -Be 3
        $settings.ScanRoots[0] | Should -Be '\\FS01\Finance'
    }
}

Describe 'The safe-defaults profile' {

    It 'changes the defaults an unconfigured run would otherwise use' {
        $plain = Import-AdgNtfsTarget -ScanRoot '\\FS01\Finance'
        $safe = Import-AdgNtfsTarget -ScanRoot '\\FS01\Finance' -SafeDefaults

        $plain.ConcurrencyLimit | Should -Be 1
        $safe.ConcurrencyLimit | Should -Be 8
        $plain.TimeoutSeconds | Should -Be 0
        $safe.TimeoutSeconds | Should -Be 14400
        $plain.MaxDepth | Should -Be 64
        $safe.MaxDepth | Should -Be 24
        $safe.CheckpointIntervalSeconds | Should -Be 60
        $safe.RetryCount | Should -Be 2
        $safe.RetryDelaySeconds | Should -Be 5
    }

    It 'leaves the settings that are already right alone' {
        # A profile that changed everything would be a profile nobody could reason about.
        # These three are the same in both because the built-in value is already the one a
        # production scan wants.
        $safe = Import-AdgNtfsTarget -ScanRoot '\\FS01\Finance' -SafeDefaults
        $safe.BatchSize | Should -Be 500
        $safe.ReparsePointPolicy | Should -Be 'skip'
        $safe.IncludeFiles | Should -BeFalse
        $safe.ReportUnresolved | Should -BeTrue
    }

    It 'never overrides a value the configuration file states' {
        # The profile supplies defaults, not policy. An operator who wrote concurrencyLimit 2
        # into their file meant 2, and a profile that quietly replaced it would be one nobody
        # could safely turn on.
        $path = Join-Path $script:Root 'explicit.json'
        Set-Content -LiteralPath $path -Encoding utf8 -Value @'
{ "scanRoots": ["\\\\FS01\\Finance"], "concurrencyLimit": 2, "maxDepth": 3, "timeoutSeconds": 60 }
'@
        $settings = Import-AdgNtfsTarget -Path $path -SafeDefaults

        $settings.ConcurrencyLimit | Should -Be 2
        $settings.MaxDepth | Should -Be 3
        $settings.TimeoutSeconds | Should -Be 60
        # And the fields the file did not mention still come from the profile.
        $settings.CheckpointIntervalSeconds | Should -Be 60
    }

    It 'validates a profile value through the same bounds as any other' {
        $profile = Get-AdgNtfsSafeDefault
        $profile.concurrencyLimit | Should -BeGreaterOrEqual 1
        $profile.concurrencyLimit | Should -BeLessOrEqual 32
        $profile.maxDepth | Should -BeLessOrEqual 512
        $profile.batchSize | Should -BeLessOrEqual 1000
        $profile.timeoutSeconds | Should -BeLessOrEqual 86400
        $profile.checkpointIntervalSeconds | Should -BeLessOrEqual 3600
    }

    It 'agrees with the example configuration that documents it' {
        # The whole point of this test: adg-ntfs-safe-defaults.example.json is what an
        # operator reads and copies, and -SafeDefaults is what the collector actually does.
        # Documentation that can drift from the code is documentation that will.
        $moduleRoot = Split-Path -Parent $PSScriptRoot
        $documented = Get-Content -LiteralPath (Join-Path $moduleRoot 'adg-ntfs-safe-defaults.example.json') `
            -Raw -Encoding utf8 | ConvertFrom-Json

        foreach ($entry in (Get-AdgNtfsSafeDefault).GetEnumerator()) {
            $property = $documented.PSObject.Properties[$entry.Key]
            $property | Should -Not -BeNullOrEmpty -Because "$($entry.Key) is in the profile and should be in the example file"
            $property.Value | Should -Be $entry.Value -Because "$($entry.Key) must mean the same thing in both"
        }
    }

    It 'leaves checkpointPath and scanRoots for the operator' {
        # Neither can be a default. One is a path on a disk the profile knows nothing about;
        # the other is the estate, and ADG never sweeps one it was not pointed at.
        $profile = Get-AdgNtfsSafeDefault
        $profile.ContainsKey('checkpointPath') | Should -BeFalse
        $profile.ContainsKey('scanRoots') | Should -BeFalse
    }

    It 'refuses a run with no scan roots even under the profile' {
        { Import-AdgNtfsTarget -SafeDefaults } | Should -Throw '*No scan roots were configured*'
    }
}
