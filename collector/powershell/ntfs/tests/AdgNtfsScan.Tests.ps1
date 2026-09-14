#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Orchestration: what a run declares it looked at, and what it will let the backend act on.

    Every function that touches a file system lives in AdgNtfsSource.ps1 and is mocked here,
    so these tests exercise the parts that decide what a scan *means*: the scopes it
    declares, the batches it streams, the status it reports, and - the one with teeth -
    which scopes it is willing to reconcile.

    Reconciliation is new in this phase and is the only thing in ADG that can mark an object
    absent. Phase 3A could never reach it: a share-root read enumerates no tree, so every run
    it produced was incremental by construction. A tree walk can enumerate a tree, so the
    question becomes real - and most of what follows is about the cases where the answer is
    still no.
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

    function New-Node {
        param([string[]] $Children = @(), [object[]] $Dacl = @(), [bool] $Present = $true,
            [bool] $Protected = $false, [switch] $Denied)
        return @{ Children = $Children; Dacl = $Dacl; Present = $Present; Protected = $Protected; Denied = [bool] $Denied }
    }

    function Set-TestEstate {
        param([Parameter(Mandatory)][hashtable] $Estate)

        # The two scoping rules that make or break every mock here: the body runs in the
        # module's session state where nothing from this file is visible, so GetNewClosure()
        # carries $Estate in - and a closed-over body does not see the mocked function's
        # parameters as inherited variables, so param() is what binds $Path. Without it
        # every lookup misses and the estate looks empty, which reads exactly like a real
        # file server where nothing is there.
        Mock -ModuleName AdgNtfsCollector Test-AdgResourceExists {
            param([string] $Path)
            $Estate.ContainsKey($Path.ToLowerInvariant())
        }.GetNewClosure()

        Mock -ModuleName AdgNtfsCollector Get-AdgDirectorySecurity {
            param([string] $Path)
            $node = $Estate[$Path.ToLowerInvariant()]
            if ($null -eq $node) { throw "The system cannot find the path '$Path'." }
            if ($node.Denied) { throw "Access to the path '$Path' is denied." }
            @{
                OwnerSid = 'S-1-5-32-544'; GroupSid = $null
                DaclPresent = $node.Present; DaclProtected = $node.Protected; Ace = $node.Dacl
            }
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
            '\\fs01\finance'            = New-Node -Children @('Reports', 'Payroll') -Dacl $root
            '\\fs01\finance\reports'    = New-Node -Children @('Q3') -Dacl $inherited
            '\\fs01\finance\reports\q3' = New-Node -Dacl $inherited
            '\\fs01\finance\payroll'    = New-Node -Dacl $inherited
            '\\fs02\payroll'            = New-Node -Dacl $root
        }
    }

    function Get-Settings {
        param([string[]] $ScanRoot = @('\\FS01\Finance'), [hashtable] $Override = @{})
        $settings = Import-AdgNtfsTarget -ScanRoot $ScanRoot
        foreach ($key in $Override.Keys) { $settings.$key = $Override[$key] }
        return $settings
    }

    # Defined here rather than inside the Describe that uses it: code in a Describe body
    # runs during Pester's discovery pass, and a function defined there does not survive
    # into the run pass where the It blocks execute.
    function New-ObservationGroup {
        # Distinct source keys, because the batch writer deduplicates on them: observations
        # that all claimed one key would be one observation by the time they reached a batch,
        # which is the correct behaviour and the wrong fixture. A fresh path per call by
        # default, too, because two groups are two resources - sharing one would make the
        # second group a repeat of the first, and the writer would rightly drop all of it.
        param([int] $Count, [string] $Path = [guid]::NewGuid().ToString('n').Substring(0, 8))
        return @(1..$Count | ForEach-Object {
                [ordered]@{ kind = 'ntfs_ace'; source_key = "ace|$Path|$_"; n = $_ }
            })
    }

    # Records the whole conversation with the sink, in order, so a test can assert on what
    # was sent and when rather than only on what the run object says afterwards.
    function Invoke-TestScan {
        param(
            [Parameter(Mandatory)][pscustomobject] $Settings,
            [switch] $PerScanRoot,
            [string[]] $ScanRoot
        )
        $events = [System.Collections.Generic.List[object]]::new()
        $onStart = { param($p) $events.Add([pscustomobject]@{ Kind = 'start'; Payload = $p }) }.GetNewClosure()
        $onBatch = { param($p) $events.Add([pscustomobject]@{ Kind = 'batch'; Payload = $p }) }.GetNewClosure()
        $onCompletion = { param($p) $events.Add([pscustomobject]@{ Kind = 'completion'; Payload = $p }) }.GetNewClosure()

        # Assigned before wrapping, never @(Invoke-AdgNtfsScan ...)[0]. The scan returns its
        # runs through the `, $array` idiom - which is what stops PowerShell unrolling an
        # empty result to nothing - so it writes the array as ONE object. Wrapping the call
        # in @() would yield a one-element array holding the array, and everything
        # downstream would silently work on the wrong thing: a count of 1 for two runs.
        $runs = if ($PSBoundParameters.ContainsKey('ScanRoot')) {
            $single = Invoke-AdgNtfsScanRun -Settings $Settings -ScanRoot $ScanRoot `
                -OnStart $onStart -OnBatch $onBatch -OnCompletion $onCompletion
            @($single)
        }
        else {
            $result = Invoke-AdgNtfsScan -Settings $Settings -OnStart $onStart -OnBatch $onBatch `
                -OnCompletion $onCompletion -RunPerScanRoot:$PerScanRoot
            @($result)
        }

        $batches = @($events | Where-Object { $_.Kind -eq 'batch' } | ForEach-Object { $_.Payload })
        $observations = [System.Collections.Generic.List[object]]::new()
        foreach ($batch in $batches) { foreach ($item in $batch.observations) { $observations.Add($item) } }

        return [pscustomobject]@{
            Runs         = $runs
            Run          = $runs[0]
            Events       = $events.ToArray()
            Batches      = $batches
            Observations = $observations.ToArray()
            Resources    = @($observations | Where-Object { $_.kind -eq 'ntfs_resource' })
        }
    }
}

Describe 'The batch writer' {
    BeforeEach {
        # The list is created as a local and captured as one. GetNewClosure() copies the
        # *local* variables in scope, and $script:Emitted is not one of them - a closure
        # written over the script-scoped name captures $null, and every Add() then fails on
        # a null-valued expression at the first batch that is actually emitted. Both names
        # here refer to one List, so the tests can read $Emitted and the writer can fill it.
        $emitted = [System.Collections.Generic.List[object]]::new()
        $script:Emitted = $emitted
        $script:Writer = New-AdgNtfsBatchWriter -RunId ([guid]::NewGuid().ToString()) -BatchSize 4 `
            -OnBatch { param($b) $emitted.Add($b) }.GetNewClosure()
    }

    It 'emits nothing until a batch is full' {
        Add-AdgNtfsObservationGroup -Writer $Writer -Group (New-ObservationGroup 3)
        $Emitted.Count | Should -Be 0
    }

    It 'never splits a resource across two batches' {
        # The backend skips its acl_hash check when a batch holds fewer than the declared
        # ace_count, so a split silently disables the only check that catches ACEs lost in
        # transit. A batch over the requested size costs nothing; a split costs that.
        Add-AdgNtfsObservationGroup -Writer $Writer -Group (New-ObservationGroup 3)
        Add-AdgNtfsObservationGroup -Writer $Writer -Group (New-ObservationGroup 3)
        Complete-AdgNtfsBatchWriter -Writer $Writer

        $Emitted.Count | Should -Be 2
        @($Emitted[0].observations).Count | Should -Be 3
        @($Emitted[1].observations).Count | Should -Be 3
    }

    It 'sends one source key once per batch' {
        # Found by a real walk of a generated tree: one orphaned SID sat on two directories,
        # each resource group reported a principal observation describing it, and the API
        # rejected the whole batch with a 422. In an estate an orphaned SID is orphaned
        # estate-wide, so it appears on dozens of folders and every batch would be refused.
        $shared = [ordered]@{ kind = 'principal'; source_key = 'principal|S-1-5-21-1-2-3-1001' }
        Add-AdgNtfsObservationGroup -Writer $Writer -Group @(
            [ordered]@{ kind = 'ntfs_resource'; source_key = 'resource|a' }, $shared)
        Add-AdgNtfsObservationGroup -Writer $Writer -Group @(
            [ordered]@{ kind = 'ntfs_resource'; source_key = 'resource|b' }, $shared)
        Complete-AdgNtfsBatchWriter -Writer $Writer

        $keys = @($Emitted[0].observations | ForEach-Object { $_['source_key'] })
        $keys.Count | Should -Be 3
        @($keys | Sort-Object -Unique).Count | Should -Be 3
        $Writer.DuplicatesDropped | Should -Be 1
    }

    It 'sends the same key again in a later batch, because that is not a duplicate' {
        # The contract forbids a repeat *within* one batch. Across batches the server keys
        # observations by (run_id, source_key) and ignores the second arrival, so carrying
        # the set across batches would drop real observations - and would grow without bound
        # on a large estate.
        $shared = [ordered]@{ kind = 'principal'; source_key = 'principal|S-1-5-21-1-2-3-1001' }
        Add-AdgNtfsObservationGroup -Writer $Writer -Group @(
            [ordered]@{ kind = 'ntfs_resource'; source_key = 'resource|a' },
            [ordered]@{ kind = 'ntfs_ace'; source_key = 'ace|a|1' },
            [ordered]@{ kind = 'ntfs_ace'; source_key = 'ace|a|2' }, $shared)
        Add-AdgNtfsObservationGroup -Writer $Writer -Group @(
            [ordered]@{ kind = 'ntfs_resource'; source_key = 'resource|b' }, $shared)
        Complete-AdgNtfsBatchWriter -Writer $Writer

        $Emitted.Count | Should -Be 2
        @($Emitted[0].observations | ForEach-Object { $_['source_key'] }) |
            Should -Contain 'principal|S-1-5-21-1-2-3-1001'
        @($Emitted[1].observations | ForEach-Object { $_['source_key'] }) |
            Should -Contain 'principal|S-1-5-21-1-2-3-1001'
        $Writer.DuplicatesDropped | Should -Be 0
    }

    It 'drops a repeated key inside one group too' {
        # An ACE's key deliberately excludes order_index, so a DACL carrying the same entry
        # at two positions produces one key twice inside a single resource's group.
        Add-AdgNtfsObservationGroup -Writer $Writer -Group @(
            [ordered]@{ kind = 'ntfs_resource'; source_key = 'resource|a' },
            [ordered]@{ kind = 'ntfs_ace'; source_key = 'ace|a|same' },
            [ordered]@{ kind = 'ntfs_ace'; source_key = 'ace|a|same' })
        Complete-AdgNtfsBatchWriter -Writer $Writer

        @($Emitted[0].observations).Count | Should -Be 2
        $Writer.DuplicatesDropped | Should -Be 1
    }

    It 'keeps an observation with no source key rather than deduplicating on nothing' {
        # A missing key is malformed and the API says so by name. Treating every such
        # observation as a repeat of the first would silently discard the rest, which is a
        # far worse failure than the 422 the caller is about to be told about.
        Add-AdgNtfsObservationGroup -Writer $Writer -Group @(
            [ordered]@{ kind = 'ntfs_ace' }, [ordered]@{ kind = 'ntfs_ace' })
        Complete-AdgNtfsBatchWriter -Writer $Writer

        @($Emitted[0].observations).Count | Should -Be 2
        $Writer.DuplicatesDropped | Should -Be 0
    }

    It 'lets one oversized group exceed the requested batch size rather than cutting it' {
        Add-AdgNtfsObservationGroup -Writer $Writer -Group (New-ObservationGroup 9)
        Complete-AdgNtfsBatchWriter -Writer $Writer
        @($Emitted[0].observations).Count | Should -Be 9
    }

    It 'refuses a group past the contract ceiling rather than truncating it' {
        # Truncating would look exactly like a shorter ACL, which is the one thing an audit
        # tool must never produce.
        { Add-AdgNtfsObservationGroup -Writer $Writer -Group (New-ObservationGroup 1001) } |
            Should -Throw '*exceeds the contract*'
    }

    It 'numbers batches from one, without gaps' {
        1..3 | ForEach-Object { Add-AdgNtfsObservationGroup -Writer $Writer -Group (New-ObservationGroup 4) }
        Complete-AdgNtfsBatchWriter -Writer $Writer
        @($Emitted | ForEach-Object { $_.sequence }) | Should -Be @(1, 2, 3)
    }

    It 'gives every batch its own id' {
        1..3 | ForEach-Object { Add-AdgNtfsObservationGroup -Writer $Writer -Group (New-ObservationGroup 4) }
        Complete-AdgNtfsBatchWriter -Writer $Writer
        @($Emitted | ForEach-Object { $_.batch_id } | Sort-Object -Unique).Count | Should -Be 3
    }

    It 'marks only the last batch final' {
        1..3 | ForEach-Object { Add-AdgNtfsObservationGroup -Writer $Writer -Group (New-ObservationGroup 4) }
        Complete-AdgNtfsBatchWriter -Writer $Writer
        @($Emitted | ForEach-Object { $_.is_final }) | Should -Be @($false, $false, $true)
    }

    It 'emits nothing at all for a run that read nothing' {
        Complete-AdgNtfsBatchWriter -Writer $Writer
        $Emitted.Count | Should -Be 0
    }

    It 'ignores an empty group' {
        Add-AdgNtfsObservationGroup -Writer $Writer -Group @()
        Add-AdgNtfsObservationGroup -Writer $Writer -Group $null
        Complete-AdgNtfsBatchWriter -Writer $Writer
        $Emitted.Count | Should -Be 0
    }

    It 'counts what it emitted' {
        Add-AdgNtfsObservationGroup -Writer $Writer -Group (New-ObservationGroup 4)
        Add-AdgNtfsObservationGroup -Writer $Writer -Group (New-ObservationGroup 2)
        Complete-AdgNtfsBatchWriter -Writer $Writer
        $Writer.BatchCount | Should -Be 2
        $Writer.ObservationCount | Should -Be 6
    }

    It 'stamps the current contract version on every batch' {
        Add-AdgNtfsObservationGroup -Writer $Writer -Group (New-ObservationGroup 1)
        Complete-AdgNtfsBatchWriter -Writer $Writer
        $Emitted[0].schema_version | Should -Be '1.3'
    }
}

Describe 'Streaming a run' {
    BeforeEach {
        Set-TestEstate (New-CleanEstate)
        $script:Scan = Invoke-TestScan -Settings (Get-Settings)
    }

    It 'sends the start, then the batches, then the completion' {
        # A batch sent after the completion is refused by the server and its observations are
        # simply lost, so the order is not a convention.
        $kinds = @($Scan.Events | ForEach-Object { $_.Kind })
        $kinds[0] | Should -Be 'start'
        $kinds[-1] | Should -Be 'completion'
        @($kinds[1..($kinds.Count - 2)] | Sort-Object -Unique) | Should -Be @('batch')
    }

    It 'declares one directory_tree scope per scan root' {
        $start = @($Scan.Events | Where-Object { $_.Kind -eq 'start' })[0].Payload
        @($start.scopes).Count | Should -Be 1
        $start.scopes[0].kind | Should -Be 'directory_tree'
        $start.scopes[0].key | Should -Be '\\fs01\finance'
    }

    It 'names the collector and the method it used' {
        $start = @($Scan.Events | Where-Object { $_.Kind -eq 'start' })[0].Payload
        $start.source.collector | Should -Be 'ntfs'
        $start.source.method | Should -Be 'DirectorySecurity.GetSecurityDescriptorBinaryForm'
    }

    It 'reports every directory it read' {
        @($Scan.Resources).Count | Should -Be 4
    }

    It 'counts in the completion what it actually emitted' {
        $completion = @($Scan.Events | Where-Object { $_.Kind -eq 'completion' })[0].Payload
        $completion.batch_count | Should -Be @($Scan.Batches).Count
        $completion.observation_count | Should -Be @($Scan.Observations).Count
    }

    It 'keeps every resource with its own entries in one batch' {
        foreach ($batch in $Scan.Batches) {
            $declared = @{}
            foreach ($observation in $batch.observations) {
                if ($observation.kind -ne 'ntfs_resource') { continue }
                $declared[$observation.path] = $observation.ace_count
            }
            foreach ($path in $declared.Keys) {
                $aces = @($batch.observations | Where-Object { $_.kind -eq 'ntfs_ace' -and $_.path -eq $path })
                $aces.Count | Should -Be $declared[$path] -Because "$path must arrive with its whole DACL"
            }
        }
    }
}

Describe 'Status' {
    It 'is succeeded for a clean, complete walk' {
        Set-TestEstate (New-CleanEstate)
        (Invoke-TestScan -Settings (Get-Settings)).Run.Status | Should -Be 'succeeded'
    }

    It 'is partial when anything went wrong' {
        $estate = New-CleanEstate
        $estate['\\fs01\finance\payroll'] = New-Node -Denied
        Set-TestEstate $estate
        (Invoke-TestScan -Settings (Get-Settings)).Run.Status | Should -Be 'partial'
    }

    It 'is failed when no root could be read at all' {
        # Coverage is then unknown rather than merely incomplete, which is a different fact
        # and calls for a different response.
        Set-TestEstate @{}
        $scan = Invoke-TestScan -Settings (Get-Settings)
        $scan.Run.Status | Should -Be 'failed'
        $scan.Run.Summary.ScanRootsRead | Should -Be 0
    }

    It 'never reports succeeded alongside an error' {
        # The contract rejects that combination, for the right reason: reporting complete
        # coverage that was not achieved understates access.
        $estate = New-CleanEstate
        $estate['\\fs01\finance\payroll'] = New-Node -Present $false
        Set-TestEstate $estate

        $scan = Invoke-TestScan -Settings (Get-Settings)
        $completion = @($scan.Events | Where-Object { $_.Kind -eq 'completion' })[0].Payload
        $completion.error_count | Should -BeGreaterThan 0
        $completion.status | Should -Not -Be 'succeeded'
    }
}

Describe 'Intent: the incremental flag' {
    It 'declares a full enumeration for a plain configured walk' {
        # The first time an ADG file-system run has been able to say this. Phase 3A marked
        # every run incremental by construction.
        Set-TestEstate (New-CleanEstate)
        (Invoke-TestScan -Settings (Get-Settings)).Run.Incremental | Should -BeFalse
    }

    It 'declares incremental when <Field> says the run will not look at all of it' -ForEach @(
        @{ Field = 'IncludePaths'; Value = @('\\FS01\Finance\Reports') }
        @{ Field = 'ExcludePaths'; Value = @('\\FS01\Finance\Archive') }
        @{ Field = 'TimeoutSeconds'; Value = 30 }
    ) {
        Set-TestEstate (New-CleanEstate)
        (Invoke-TestScan -Settings (Get-Settings -Override @{ $Field = $Value })).Run.Incremental |
            Should -BeTrue
    }

    It 'does not treat a depth limit as an intent to under-enumerate' {
        # A depth limit is an upper bound a shallow tree never reaches. Declaring incremental
        # because a limit existed would forbid reconciliation on every configured scan; the
        # walk finds out during the scan whether it was actually hit.
        Set-TestEstate (New-CleanEstate)
        (Invoke-TestScan -Settings (Get-Settings -Override @{ MaxDepth = 64 })).Run.Incremental | Should -BeFalse
    }

    It 'is decided before the walk, because the server refuses to let it change' {
        Set-TestEstate (New-CleanEstate)
        $scan = Invoke-TestScan -Settings (Get-Settings)
        $start = @($scan.Events | Where-Object { $_.Kind -eq 'start' })[0].Payload
        $start.incremental | Should -Be $scan.Run.Incremental
    }
}

Describe 'Achievement: what a run will let the backend mark absent' {
    It 'reconciles a tree it enumerated completely' {
        Set-TestEstate (New-CleanEstate)
        $scan = Invoke-TestScan -Settings (Get-Settings)
        @($scan.Run.ReconciledScopes).Count | Should -Be 1
        @($scan.Run.ReconciledScopes)[0].kind | Should -Be 'directory_tree'
        @($scan.Run.ReconciledScopes)[0].key | Should -Be '\\fs01\finance'
    }

    It 'reconciles nothing when a depth limit was actually reached' {
        Set-TestEstate (New-CleanEstate)
        $scan = Invoke-TestScan -Settings (Get-Settings -Override @{ MaxDepth = 1 })
        @($scan.Run.ReconciledScopes).Count | Should -Be 0
    }

    It 'reconciles nothing when a descriptor could not be read' {
        $estate = New-CleanEstate
        $estate['\\fs01\finance\payroll'] = New-Node -Denied
        Set-TestEstate $estate
        @((Invoke-TestScan -Settings (Get-Settings)).Run.ReconciledScopes).Count | Should -Be 0
    }

    It 'reconciles nothing when a NULL DACL was found' {
        # A NULL DACL is a finding, a finding is an error, and a run with any error cannot
        # reconcile. Stricter than strictly necessary, and deliberately so: the run is still
        # fully reported, it simply does not get to mark anything absent.
        $estate = New-CleanEstate
        $estate['\\fs01\finance\payroll'] = New-Node -Present $false
        Set-TestEstate $estate
        @((Invoke-TestScan -Settings (Get-Settings)).Run.ReconciledScopes).Count | Should -Be 0
    }

    It 'reconciles only the trees that were themselves complete' {
        # Per-root, not all-or-nothing: one unreadable tree must not cost the reconciliation
        # of a tree that was read end to end.
        $estate = New-CleanEstate
        $estate['\\fs02\payroll'] = New-Node -Children @('Sealed') -Dacl @((New-Ace))
        $estate['\\fs02\payroll\sealed'] = New-Node -Denied
        Set-TestEstate $estate

        $settings = Get-Settings -ScanRoot @('\\FS01\Finance', '\\FS02\Payroll')
        $scan = Invoke-TestScan -Settings $settings -PerScanRoot
        @($scan.Runs).Count | Should -Be 2

        $finance = @($scan.Runs | Where-Object { $_.Start.source.target -eq '\\FS01\Finance' })[0]
        $payroll = @($scan.Runs | Where-Object { $_.Start.source.target -eq '\\FS02\Payroll' })[0]
        @($finance.ReconciledScopes).Count | Should -Be 1
        @($payroll.ReconciledScopes).Count | Should -Be 0
    }

    It 'never reconciles a scope it did not declare' {
        Set-TestEstate (New-CleanEstate)
        $scan = Invoke-TestScan -Settings (Get-Settings)
        $start = @($scan.Events | Where-Object { $_.Kind -eq 'start' })[0].Payload
        $declared = @($start.scopes | ForEach-Object { "$($_.kind)|$($_.key)" })
        foreach ($scope in $scan.Run.ReconciledScopes) {
            $declared | Should -Contain "$($scope.kind)|$($scope.key)"
        }
    }

    It 'reconciles nothing for a root it never managed to enter' {
        Set-TestEstate @{}
        @((Invoke-TestScan -Settings (Get-Settings)).Run.ReconciledScopes).Count | Should -Be 0
    }
}

Describe 'One run or several' {
    BeforeEach {
        Set-TestEstate (New-CleanEstate)
        $script:Settings = Get-Settings -ScanRoot @('\\FS01\Finance', '\\FS02\Payroll')
    }

    It 'covers every root in one run by default' {
        $scan = Invoke-TestScan -Settings $Settings
        @($scan.Runs).Count | Should -Be 1
        @($scan.Run.Start.scopes).Count | Should -Be 2
    }

    It 'emits one run per root when asked' {
        $scan = Invoke-TestScan -Settings $Settings -PerScanRoot
        @($scan.Runs).Count | Should -Be 2
        foreach ($run in $scan.Runs) { @($run.Start.scopes).Count | Should -Be 1 }
    }

    It 'gives each run its own id' {
        $scan = Invoke-TestScan -Settings $Settings -PerScanRoot
        @($scan.Runs | ForEach-Object { $_.RunId } | Sort-Object -Unique).Count | Should -Be 2
    }

    It 'refuses to share one checkpoint between several runs' {
        # Each run would resume the previous one's frontier and walk the wrong tree.
        $Settings.CheckpointPath = Join-Path ([System.IO.Path]::GetTempPath()) 'adg-shared.checkpoint.json'
        { Invoke-TestScan -Settings $Settings -PerScanRoot } | Should -Throw '*cannot be shared*'
    }

    It 'lets one unreadable root downgrade only its own run' {
        $estate = New-CleanEstate
        $estate.Remove('\\fs02\payroll')
        Set-TestEstate $estate

        $scan = Invoke-TestScan -Settings $Settings -PerScanRoot
        @($scan.Runs | Where-Object { $_.Status -eq 'succeeded' }).Count | Should -Be 1
        @($scan.Runs | Where-Object { $_.Status -eq 'failed' }).Count | Should -Be 1
    }
}

Describe 'Test-AdgNtfsFullEnumerationIntent' {
    It 'is true for a configuration that sets out to read everything' {
        Test-AdgNtfsFullEnumerationIntent -Settings (Get-Settings) | Should -BeTrue
    }

    It 'is false while resuming, whatever the settings say' {
        Test-AdgNtfsFullEnumerationIntent -Settings (Get-Settings) -Resuming | Should -BeFalse
    }
}

Describe 'A scan rooted below a share root' {
    BeforeEach {
        Set-TestEstate (New-CleanEstate)
        $script:Scan = Invoke-TestScan -Settings (Get-Settings -ScanRoot @('\\FS01\Finance\Reports'))
    }

    It 'is accepted, which Phase 3A refused' {
        @($Scan.Resources | ForEach-Object { $_.path }) | Should -Contain '\\FS01\Finance\Reports'
    }

    It 'declares the tree it actually walked, not the share above it' {
        @($Scan.Run.Start.scopes)[0].key | Should -Be '\\fs01\finance\reports'
    }

    It 'reports its starting directory as a boundary it could not establish' {
        $root = @($Scan.Resources | Where-Object { $_.path -eq '\\FS01\Finance\Reports' })[0]
        $root.is_acl_boundary | Should -BeTrue
        $root.boundary_reason | Should -Be 'scan_root'
    }

    It 'still compares the directories below it normally' {
        $child = @($Scan.Resources | Where-Object { $_.path -eq '\\FS01\Finance\Reports\Q3' })[0]
        $child.is_acl_boundary | Should -BeFalse
    }
}
