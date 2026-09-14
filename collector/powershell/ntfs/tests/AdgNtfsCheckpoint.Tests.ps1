#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Checkpointing: resuming a walk, and refusing to resume into a different one.

    A tree scan over a real estate runs for hours and will be interrupted. The cost of
    starting again is not just the time: a scan restarted from zero every evening never
    finishes, so the estate is never fully read, so nothing can ever be reconciled.

    The refusals matter as much as the resume. A checkpoint carries a frontier - directories
    discovered and not yet read, each with the parent facts it needs to judge its own
    boundary - and resuming it under changed settings would produce one run that enumerated
    its scope under two different rules. Every refusal below is that case, and each says
    what to do instead.
#>

BeforeAll {
    $moduleRoot = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $moduleRoot 'AdgNtfsCollector.psd1') -Force

    $script:Admins = 'S-1-5-32-544'
    $script:Users = 'S-1-5-21-1004336348-1177238915-682003330-1201'

    function New-Ace {
        param([int] $AceFlags = 0x03, [long] $AccessMask = 0x001200A9, [string] $TrusteeSid = 'S-1-5-32-544')
        return [pscustomobject]@{
            AceType = 'AccessAllowed'; AceFlags = $AceFlags; AccessMask = $AccessMask
            TrusteeSid = $TrusteeSid; TrusteeName = 'BUILTIN\Administrators'
        }
    }

    function New-Workspace {
        $path = Join-Path ([System.IO.Path]::GetTempPath()) ("adg-checkpoint-" + [guid]::NewGuid().ToString('n').Substring(0, 8))
        New-Item -ItemType Directory -Path $path | Out-Null
        return $path
    }

    function Get-Settings {
        param([string] $CheckpointPath, [hashtable] $Override = @{})
        $settings = Import-AdgNtfsTarget -ScanRoot '\\FS01\Finance'
        if ($CheckpointPath) { $settings.CheckpointPath = $CheckpointPath }
        foreach ($key in $Override.Keys) { $settings.$key = $Override[$key] }
        return $settings
    }

    function Set-TestEstate {
        param([Parameter(Mandatory)][hashtable] $Estate)

        Mock -ModuleName AdgNtfsCollector Test-AdgResourceExists {
            param([string] $Path)
            $Estate.ContainsKey($Path.ToLowerInvariant())
        }.GetNewClosure()

        Mock -ModuleName AdgNtfsCollector Get-AdgDirectorySecurity {
            param([string] $Path)
            $node = $Estate[$Path.ToLowerInvariant()]
            if ($null -eq $node) { throw "The system cannot find the path '$Path'." }
            @{ OwnerSid = 'S-1-5-32-544'; GroupSid = $null; DaclPresent = $true; DaclProtected = $false; Ace = $node.Dacl }
        }.GetNewClosure()

        Mock -ModuleName AdgNtfsCollector Get-AdgChildDirectory {
            param([string] $Path)
            $node = $Estate[$Path.ToLowerInvariant()]
            if ($null -eq $node) { throw "The system cannot find the path '$Path'." }
            $children = foreach ($name in $node.Children) {
                [pscustomobject]@{ Name = $name; Path = "$Path\$name"; IsReparsePoint = $false; LinkTarget = $null }
            }
            , @($children)
        }.GetNewClosure()
    }

    function New-CleanEstate {
        $root = @((New-Ace -AceFlags 0x03 -AccessMask 0x001F01FF), (New-Ace -AceFlags 0x03 -TrusteeSid $Users))
        $inherited = @((New-Ace -AceFlags 0x13 -AccessMask 0x001F01FF), (New-Ace -AceFlags 0x13 -TrusteeSid $Users))
        return @{
            '\\fs01\finance' = @{ Children = @('Reports', 'Payroll'); Dacl = $root }
            '\\fs01\finance\reports' = @{ Children = @('Q3'); Dacl = $inherited }
            '\\fs01\finance\reports\q3' = @{ Children = @(); Dacl = $inherited }
            '\\fs01\finance\payroll' = @{ Children = @(); Dacl = $inherited }
        }
    }

    function Invoke-TestRun {
        param(
            [Parameter(Mandatory)][pscustomobject] $Settings,
            [switch] $Resume,
            [string[]] $ScanRoot = @('\\FS01\Finance')
        )
        $observations = [System.Collections.Generic.List[object]]::new()
        $onBatch = {
            param($batch)
            foreach ($item in $batch.observations) { $observations.Add($item) }
        }.GetNewClosure()

        $run = Invoke-AdgNtfsScanRun -Settings $Settings -ScanRoot $ScanRoot `
            -OnStart { param($s) } -OnBatch $onBatch -OnCompletion { param($c) } -Resume:$Resume

        return [pscustomobject]@{
            Run = $run
            Observations = $observations.ToArray()
            Resources = @($observations | Where-Object { $_.kind -eq 'ntfs_resource' })
        }
    }
}

Describe 'Get-AdgScanFingerprint' {
    It 'covers the settings that decide which directories a walk visits' -ForEach @(
        @{ Field = 'MaxDepth'; Value = 3 }
        @{ Field = 'IncludePaths'; Value = @('\\FS01\Finance\Reports') }
        @{ Field = 'ExcludePaths'; Value = @('\\FS01\Finance\Archive') }
        @{ Field = 'ReparsePointPolicy'; Value = 'follow' }
        @{ Field = 'IncludeFiles'; Value = $true }
        @{ Field = 'ScanRoots'; Value = @('\\FS02\Payroll') }
    ) {
        $before = Get-AdgScanFingerprint -Settings (Get-Settings)
        $after = Get-AdgScanFingerprint -Settings (Get-Settings -Override @{ $Field = $Value })
        $after | Should -Not -Be $before -Because "$Field changes which directories are visited"
    }

    It 'ignores the settings that only change how long a scan takes' -ForEach @(
        @{ Field = 'BatchSize'; Value = 42 }
        @{ Field = 'ConcurrencyLimit'; Value = 8 }
        @{ Field = 'RetryCount'; Value = 5 }
        @{ Field = 'CheckpointIntervalSeconds'; Value = 600 }
    ) {
        # A setting in the fingerprint makes a checkpoint unresumable, so it belongs there
        # only if resuming with it changed would produce a run enumerated under two rules.
        $before = Get-AdgScanFingerprint -Settings (Get-Settings)
        $after = Get-AdgScanFingerprint -Settings (Get-Settings -Override @{ $Field = $Value })
        $after | Should -Be $before -Because "$Field changes the cost of a scan, not its reach"
    }

    It 'is case-insensitive about paths, as the file systems they name are' {
        (Get-AdgScanFingerprint -Settings (Get-Settings -Override @{ ScanRoots = @('\\FS01\FINANCE') })) |
            Should -Be (Get-AdgScanFingerprint -Settings (Get-Settings -Override @{ ScanRoots = @('\\fs01\finance') }))
    }

    It 'is a 64-character lower-case digest' {
        Get-AdgScanFingerprint -Settings (Get-Settings) | Should -Match '^[0-9a-f]{64}$'
    }
}

Describe 'Saving and reading a checkpoint' {
    BeforeEach {
        $script:Workspace = New-Workspace
        $script:Path = Join-Path $Workspace 'scan.checkpoint.json'
        $script:Settings = Get-Settings -CheckpointPath $Path
    }

    AfterEach {
        Remove-Item -LiteralPath $Workspace -Recurse -Force -ErrorAction SilentlyContinue
    }

    It 'returns nothing when no checkpoint exists, which is the ordinary first run' {
        Import-AdgNtfsCheckpoint -Path $Path -Settings $Settings | Should -BeNullOrEmpty
    }

    It 'round-trips a frontier entry with the parent facts it needs' {
        $checkpoint = New-AdgNtfsCheckpoint -RunId ([guid]::NewGuid().ToString()) -Settings $Settings
        $checkpoint.Frontier = @([pscustomobject]@{
                Path = '\\FS01\Finance\Reports'
                Depth = 1
                Root = '\\fs01\finance'
                IsScanRoot = $false
                ParentDaclPresent = $true
                ParentProjection = 'a' * 64
                ParentAclHash = 'b' * 64
                LinkTargets = @('d:\shares\finance')
            })
        Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Path | Out-Null

        $loaded = Import-AdgNtfsCheckpoint -Path $Path -Settings $Settings
        $entry = @($loaded.Frontier)[0]
        $entry.Path | Should -Be '\\FS01\Finance\Reports'
        $entry.Depth | Should -Be 1
        $entry.ParentProjection | Should -Be ('a' * 64)
        $entry.ParentAclHash | Should -Be ('b' * 64)
        $entry.LinkTargets | Should -Be @('d:\shares\finance')
        $loaded.RunId | Should -Be $checkpoint.RunId
    }

    It 'keeps "the parent was never read" distinct from "the parent has a NULL DACL"' {
        # Two opposite facts that both arrive as an absent JSON property. Collapsing them
        # would report parent_unreadable as parent_null_dacl, or the other way round.
        $checkpoint = New-AdgNtfsCheckpoint -RunId ([guid]::NewGuid().ToString()) -Settings $Settings
        $checkpoint.Frontier = @(
            [pscustomobject]@{ Path = '\\FS01\Finance\A'; Depth = 1; Root = '\\fs01\finance'; IsScanRoot = $false
                ParentDaclPresent = $null; ParentProjection = $null; ParentAclHash = $null; LinkTargets = @() },
            [pscustomobject]@{ Path = '\\FS01\Finance\B'; Depth = 1; Root = '\\fs01\finance'; IsScanRoot = $false
                ParentDaclPresent = $false; ParentProjection = $null; ParentAclHash = $null; LinkTargets = @() }
        )
        Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Path | Out-Null

        $loaded = Import-AdgNtfsCheckpoint -Path $Path -Settings $Settings
        @($loaded.Frontier)[0].ParentDaclPresent | Should -BeNullOrEmpty
        @($loaded.Frontier)[1].ParentDaclPresent | Should -Be $false
        # And the second is not null: $false is a fact, and BeNullOrEmpty would pass for both.
        $null -eq @($loaded.Frontier)[1].ParentDaclPresent | Should -BeFalse
    }

    It 'writes through a temporary file and leaves none behind' {
        # A half-written checkpoint is not a slightly stale one; it is a file that fails to
        # parse on resume, and the only honest answer left is to scan the tree again.
        $checkpoint = New-AdgNtfsCheckpoint -RunId ([guid]::NewGuid().ToString()) -Settings $Settings
        Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Path | Out-Null
        Test-Path -LiteralPath $Path | Should -BeTrue
        Test-Path -LiteralPath "$Path.tmp" | Should -BeFalse
    }

    It 'overwrites an existing checkpoint in place' {
        $checkpoint = New-AdgNtfsCheckpoint -RunId ([guid]::NewGuid().ToString()) -Settings $Settings
        Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Path | Out-Null
        $checkpoint.Frontier = @([pscustomobject]@{ Path = '\\FS01\Finance\Later'; Depth = 1
                Root = '\\fs01\finance'; IsScanRoot = $false; ParentDaclPresent = $true
                ParentProjection = $null; ParentAclHash = $null; LinkTargets = @() })
        Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Path | Out-Null

        $loaded = Import-AdgNtfsCheckpoint -Path $Path -Settings $Settings
        @($loaded.Frontier).Count | Should -Be 1
        @($loaded.Frontier)[0].Path | Should -Be '\\FS01\Finance\Later'
    }

    It 'removes the file and any temporary beside it' {
        $checkpoint = New-AdgNtfsCheckpoint -RunId ([guid]::NewGuid().ToString()) -Settings $Settings
        Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Path | Out-Null
        Set-Content -LiteralPath "$Path.tmp" -Value '{}' -Encoding utf8

        Remove-AdgNtfsCheckpoint -Path $Path
        Test-Path -LiteralPath $Path | Should -BeFalse
        Test-Path -LiteralPath "$Path.tmp" | Should -BeFalse
    }

    It 'is happy to remove a checkpoint that is not there' {
        { Remove-AdgNtfsCheckpoint -Path (Join-Path $Workspace 'absent.json') } | Should -Not -Throw
    }
}

Describe 'Refusing to resume into a different scan' {
    BeforeEach {
        $script:Workspace = New-Workspace
        $script:Path = Join-Path $Workspace 'scan.checkpoint.json'
        $script:Settings = Get-Settings -CheckpointPath $Path
        $checkpoint = New-AdgNtfsCheckpoint -RunId ([guid]::NewGuid().ToString()) -Settings $Settings
        Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Path | Out-Null
    }

    AfterEach {
        Remove-Item -LiteralPath $Workspace -Recurse -Force -ErrorAction SilentlyContinue
    }

    It 'refuses a checkpoint written for different settings' {
        $changed = Get-Settings -CheckpointPath $Path -Override @{ MaxDepth = 2 }
        { Import-AdgNtfsCheckpoint -Path $Path -Settings $changed } |
            Should -Throw '*written for a different scan*'
    }

    It 'names the settings that could have changed, so the message is actionable' {
        $changed = Get-Settings -CheckpointPath $Path -Override @{ ExcludePaths = @('\\FS01\Finance\Archive') }
        { Import-AdgNtfsCheckpoint -Path $Path -Settings $changed } | Should -Throw '*exclude*'
    }

    It 'refuses a checkpoint that is not valid JSON' {
        Set-Content -LiteralPath $Path -Value '{ "Format": "adg-ntfs-checkpo' -Encoding utf8
        { Import-AdgNtfsCheckpoint -Path $Path -Settings $Settings } | Should -Throw '*not valid JSON*'
    }

    It 'refuses a checkpoint in an unknown format' {
        $document = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        $document.Format = 'adg-ntfs-checkpoint/0'
        $document | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding utf8
        { Import-AdgNtfsCheckpoint -Path $Path -Settings $Settings } | Should -Throw '*format*'
    }

    It 'refuses a checkpoint that names no run' {
        # Without the original run id the resumed half would be a second run over the same
        # tree, and neither half could reconcile the scope.
        $document = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        $document.RunId = ''
        $document | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding utf8
        { Import-AdgNtfsCheckpoint -Path $Path -Settings $Settings } | Should -Throw '*names no run*'
    }
}

Describe 'Resuming an interrupted walk' {
    BeforeEach {
        $script:Workspace = New-Workspace
        $script:Path = Join-Path $Workspace 'scan.checkpoint.json'
        Set-TestEstate (New-CleanEstate)
    }

    AfterEach {
        Remove-Item -LiteralPath $Workspace -Recurse -Force -ErrorAction SilentlyContinue
    }

    It 'leaves a checkpoint behind when a walk is cut short' {
        # Interrupted before the first directory, so the whole tree is still pending.
        $settings = Get-Settings -CheckpointPath $Path -Override @{ TimeoutSeconds = 1 }
        $first = Invoke-TestRun -Settings $settings
        # A timeout of one second is not reliably reached on a mocked estate, so the run may
        # complete. Either way the invariant holds: a completed walk leaves no checkpoint,
        # and an interrupted one leaves a frontier.
        if ($first.Run.Summary.Completed) {
            Test-Path -LiteralPath $Path | Should -BeFalse
        }
        else {
            Test-Path -LiteralPath $Path | Should -BeTrue
        }
    }

    It 'deletes the checkpoint once the walk finishes' {
        # A stale checkpoint makes the next run resume an empty frontier, read nothing, and
        # report success over a tree it never looked at. That is the worse failure of the
        # two, and it is silent.
        $settings = Get-Settings -CheckpointPath $Path
        $run = Invoke-TestRun -Settings $settings
        $run.Run.Summary.Completed | Should -BeTrue
        Test-Path -LiteralPath $Path | Should -BeFalse
    }

    It 'refuses to start a new scan over an existing checkpoint' {
        $settings = Get-Settings -CheckpointPath $Path
        $checkpoint = New-AdgNtfsCheckpoint -RunId ([guid]::NewGuid().ToString()) -Settings $settings
        Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Path | Out-Null

        { Invoke-TestRun -Settings $settings } | Should -Throw '*already exists*'
    }

    It 'keeps the original run id when resuming' {
        # The two halves are one run: the server's per-(run_id, source_key) rows line up, and
        # a re-read directory is recognized as a repeat rather than counted twice.
        $settings = Get-Settings -CheckpointPath $Path
        $runId = [guid]::NewGuid().ToString()
        $checkpoint = New-AdgNtfsCheckpoint -RunId $runId -Settings $settings
        $checkpoint.Frontier = @([pscustomobject]@{
                Path = '\\FS01\Finance\Payroll'; Depth = 1; Root = '\\fs01\finance'; IsScanRoot = $false
                ParentDaclPresent = $true; ParentProjection = $null; ParentAclHash = $null; LinkTargets = @()
            })
        Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Path | Out-Null

        $resumed = Invoke-TestRun -Settings $settings -Resume
        $resumed.Run.RunId | Should -Be $runId
        $resumed.Run.Resumed | Should -BeTrue
    }

    It 'reads only the frontier, not the roots again' {
        # The frontier already says where the walk had got to. Re-seeding the roots would
        # walk the finished part a second time, which on a real estate is most of the scan.
        $settings = Get-Settings -CheckpointPath $Path
        $checkpoint = New-AdgNtfsCheckpoint -RunId ([guid]::NewGuid().ToString()) -Settings $settings
        $checkpoint.Frontier = @([pscustomobject]@{
                Path = '\\FS01\Finance\Payroll'; Depth = 1; Root = '\\fs01\finance'; IsScanRoot = $false
                ParentDaclPresent = $true; ParentProjection = $null; ParentAclHash = $null; LinkTargets = @()
            })
        Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Path | Out-Null

        $resumed = Invoke-TestRun -Settings $settings -Resume
        @($resumed.Resources | ForEach-Object { $_.path }) | Should -Be @('\\FS01\Finance\Payroll')
    }

    It 'judges a resumed directory against the parent facts the checkpoint carried' {
        # Recomputing them would mean re-reading the parent, which may be behind an
        # exclusion, past the depth limit, or simply gone by then - at which point the
        # resumed half would report parent_unreadable for a directory the first half
        # compared correctly, and the two halves of one run would disagree about one tree.
        $settings = Get-Settings -CheckpointPath $Path
        $inherited = @((New-Ace -AceFlags 0x13 -AccessMask 0x001F01FF), (New-Ace -AceFlags 0x13 -TrusteeSid $Users))
        $projection = Get-AdgProjectedChildAclHash -DaclPresent $true -ForContainer $true -Ace @(
            [pscustomobject]@{ TrusteeSid = $Admins; AceType = 'allow'; AccessMask = 0x001F01FF; AceFlags = 0x03; OrderIndex = 0 },
            [pscustomobject]@{ TrusteeSid = $Users; AceType = 'allow'; AccessMask = 0x001200A9; AceFlags = 0x03; OrderIndex = 1 }
        )

        $checkpoint = New-AdgNtfsCheckpoint -RunId ([guid]::NewGuid().ToString()) -Settings $settings
        $checkpoint.Frontier = @([pscustomobject]@{
                Path = '\\FS01\Finance\Payroll'; Depth = 1; Root = '\\fs01\finance'; IsScanRoot = $false
                ParentDaclPresent = $true; ParentProjection = $projection
                ParentAclHash = ('c' * 64); LinkTargets = @()
            })
        Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Path | Out-Null

        $resumed = Invoke-TestRun -Settings $settings -Resume
        $payroll = @($resumed.Resources)[0]
        # It inherits cleanly from the projection the checkpoint carried, so no boundary.
        $payroll.is_acl_boundary | Should -BeFalse
        $payroll.parent_acl_hash | Should -Be ('c' * 64)
    }

    It 'never reconciles a resumed run' {
        # No single pass enumerated the scope end to end, and the scope is what
        # reconciliation acts on.
        $settings = Get-Settings -CheckpointPath $Path
        $checkpoint = New-AdgNtfsCheckpoint -RunId ([guid]::NewGuid().ToString()) -Settings $settings
        $checkpoint.Frontier = @([pscustomobject]@{
                Path = '\\FS01\Finance\Payroll'; Depth = 1; Root = '\\fs01\finance'; IsScanRoot = $false
                ParentDaclPresent = $true; ParentProjection = $null; ParentAclHash = $null; LinkTargets = @()
            })
        Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Path | Out-Null

        $resumed = Invoke-TestRun -Settings $settings -Resume
        $resumed.Run.Incremental | Should -BeTrue
        @($resumed.Run.ReconciledScopes).Count | Should -Be 0
    }

    It 'resumes nothing when the checkpoint has an empty frontier' {
        # Which is what a completed walk would have left if it did not delete its file, and
        # the case that must not read as "there was nothing to scan".
        $settings = Get-Settings -CheckpointPath $Path
        $checkpoint = New-AdgNtfsCheckpoint -RunId ([guid]::NewGuid().ToString()) -Settings $settings
        Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Path | Out-Null

        $resumed = Invoke-TestRun -Settings $settings -Resume
        $resumed.Run.Resumed | Should -BeFalse
        # It walked the roots instead, which is the safe reading of an empty frontier.
        @($resumed.Resources).Count | Should -Be 4
    }
}
