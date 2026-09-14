#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Configuration: which share roots a run will read, and what it refuses to accept.

    The refusals matter more than the acceptances here. A configuration mistake that is
    caught turns into a message naming the field; one that is not turns into a scan that
    reads the wrong thing, or a resource observation carrying a boundary nobody established.
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
        Test-AdgShareRootPath '\\FS01\Finance\Reports' | Should -BeFalse
    }
}

Describe 'Import-AdgNtfsTarget' {
    It 'takes roots from the file and the parameter together' {
        $path = New-ConfigFile @{ shareRoots = @('\\FS01\Finance') }
        $settings = Import-AdgNtfsTarget -Path $path -ShareRoot '\\FS02\Departments'

        $settings.ShareRoots | Should -Be @('\\FS01\Finance', '\\FS02\Departments')
    }

    It 'canonicalizes what it was given' {
        $settings = Import-AdgNtfsTarget -ShareRoot '//fs01/finance/'
        $settings.ShareRoots | Should -Be @('\\fs01\finance')
    }

    It 'collapses two spellings of one root into one target' {
        # Otherwise the run would read the same descriptor twice and declare its scope
        # twice, which the contract rejects as an ambiguous claim of coverage.
        $settings = Import-AdgNtfsTarget -ShareRoot '\\FS01\Finance', '//fs01/FINANCE'
        @($settings.ShareRoots).Count | Should -Be 1
    }

    It 'refuses a path inside a share, and says which root to configure instead' {
        # Phase 3A reads roots only: whether a subdirectory's DACL differs from its parent's
        # cannot be established without the ancestors, and a guessed boundary would mislead
        # the recursive scan that follows.
        { Import-AdgNtfsTarget -ShareRoot '\\FS01\Finance\Reports' } |
            Should -Throw '*\\FS01\Finance*'
    }

    It 'refuses a local path, which does not say which server it is on' {
        { Import-AdgNtfsTarget -ShareRoot 'D:\Shares\Finance' } | Should -Throw '*not a usable share root*'
    }

    It 'refuses to run with no targets at all' {
        # There is no estate-wide sweep. An auditing tool that walks machines it was never
        # pointed at looks exactly like reconnaissance, and it silently changes what a run's
        # declared scope means.
        { Import-AdgNtfsTarget } | Should -Throw '*does not sweep an estate implicitly*'
    }

    It 'defaults the settings a caller did not state' {
        $settings = Import-AdgNtfsTarget -ShareRoot '\\FS01\Finance'

        $settings.RetryCount | Should -Be 1
        $settings.BatchSize | Should -Be 500
        # On by default: an orphaned SID on a folder ACL is a finding the backend cannot
        # infer from the ACE alone.
        $settings.ReportUnresolved | Should -BeTrue
    }

    It 'rejects a batch size outside the contract' {
        $path = New-ConfigFile @{ shareRoots = @('\\FS01\Finance'); batchSize = 5000 }
        { Import-AdgNtfsTarget -Path $path } | Should -Throw '*batchSize*'
    }

    It 'rejects a retry count outside its range' {
        $path = New-ConfigFile @{ shareRoots = @('\\FS01\Finance'); retryCount = 99 }
        { Import-AdgNtfsTarget -Path $path } | Should -Throw '*retryCount*'
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

        @($settings.ShareRoots).Count | Should -Be 3
        $settings.ShareRoots[0] | Should -Be '\\FS01\Finance'
    }
}
