#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Orchestration: an imaginary estate of share roots, with no file system.

    Every function that touches a file system lives in AdgNtfsSource.ps1 and is mocked here,
    so these tests exercise the parts that decide what a scan *means*: what it declares it
    looked at, what it will let the backend mark absent, what it refuses to claim when
    something went wrong, and what it refuses to hash when it could not read everything.

    The cases the phase requires - an ordinary root, a protected DACL, a NULL DACL, an
    unreadable descriptor, and an unresolved trustee - are each a Context below.
#>

BeforeAll {
    $moduleRoot = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $moduleRoot 'AdgNtfsCollector.psd1') -Force

    $script:FinanceRw = 'S-1-5-21-1004336348-1177238915-682003330-1202'
    $script:Orphan = 'S-1-5-21-1004336348-1177238915-682003330-9999'

    function New-Ace {
        param(
            [string] $AceType = 'AccessAllowed',
            [int] $AceFlags = 3,
            [long] $AccessMask = 1179817,
            [string] $TrusteeSid = 'S-1-5-11',
            [AllowNull()][string] $TrusteeName = 'CORP\Authenticated Users'
        )
        return [pscustomobject]@{
            AceType     = $AceType
            AceFlags    = $AceFlags
            AccessMask  = $AccessMask
            TrusteeSid  = $TrusteeSid
            TrusteeName = $TrusteeName
        }
    }

    # An estate expressed as a map from case-folded path to descriptor. A path that is not
    # in the map does not exist; a descriptor that is $null throws when read, which is what
    # an access denial looks like from here.
    function Set-TestEstate {
        param([Parameter(Mandatory)][hashtable] $Estate)

        # Two scoping rules meet here, and getting either wrong makes every mock below
        # silently do nothing:
        #
        #   * the mock body runs in the module's session state, where nothing from this test
        #     file is visible. GetNewClosure() is what carries $Estate in;
        #   * a closed-over body does NOT see the mocked function's parameters as inherited
        #     variables. $Path comes back empty, ContainsKey('') is false, and every path
        #     looks absent - which reads exactly like a real estate where nothing is there.
        #     Declaring param() makes Pester bind the call's arguments instead.
        Mock -ModuleName AdgNtfsCollector Test-AdgResourceExists {
            param([string] $Path)
            $Estate.ContainsKey($Path.ToLowerInvariant())
        }.GetNewClosure()

        Mock -ModuleName AdgNtfsCollector Get-AdgDirectorySecurity {
            param([string] $Path)
            $descriptor = $Estate[$Path.ToLowerInvariant()]
            if ($null -eq $descriptor) { throw "Access to the path '$Path' is denied." }
            $descriptor
        }.GetNewClosure()
    }

    function New-Descriptor {
        param(
            [bool] $DaclPresent = $true,
            [bool] $DaclProtected = $false,
            [string] $OwnerSid = 'S-1-5-32-544',
            [object[]] $Ace = @()
        )
        return @{
            OwnerSid      = $OwnerSid
            GroupSid      = $null
            DaclPresent   = $DaclPresent
            DaclProtected = $DaclProtected
            Ace           = $Ace
        }
    }

    function Get-TestRun {
        # Assigned before indexing, never @(Invoke-AdgNtfsScan ...)[0]. The scan returns its
        # runs through the `, $array` idiom - which is what stops PowerShell unrolling an
        # empty result to nothing - so it writes the array as ONE object. Wrapping that call
        # in @() therefore yields a one-element array holding the array, and everything
        # downstream silently works on the wrong thing: a count of 1 for three runs, and a
        # member lookup that finds nothing when the single run's Batches list is empty.
        param([Parameter(Mandatory)] $Settings, [switch] $PerShareRoot)
        $runs = Invoke-AdgNtfsScan -Settings $Settings -RunPerShareRoot:$PerShareRoot
        return $runs[0]
    }

    function Get-TestRunSet {
        param([Parameter(Mandatory)] $Settings, [switch] $PerShareRoot)
        $runs = Invoke-AdgNtfsScan -Settings $Settings -RunPerShareRoot:$PerShareRoot
        return , @($runs)
    }

    function Get-Observations {
        # The comma operator is load bearing. PowerShell unrolls a returned array, so a
        # one-element result would come back as a bare observation - and .Count on an
        # ordered dictionary is its number of keys, which silently turns "one observation"
        # into however many fields it happens to have.
        param([Parameter(Mandatory)] $Run)
        $items = [System.Collections.Generic.List[object]]::new()
        foreach ($batch in @($Run.Batches)) {
            foreach ($observation in @($batch.observations)) { $items.Add($observation) }
        }
        return , $items.ToArray()
    }

    function Select-Kind {
        # No comma wrapper here, unlike Get-Observations. Callers immediately re-wrap with
        # @(), and a wrapper on top of that would make every count 1: one array.
        param([Parameter(Mandatory)] $Observations, [Parameter(Mandatory)][string] $Kind)
        return @($Observations) | Where-Object { $_.kind -eq $Kind }
    }

    $script:Settings = Import-AdgNtfsTarget -ShareRoot '\\FS01\Finance'
    $script:Settings.RetryCount = 0
    $script:Settings.RetryDelaySeconds = 0
}

Describe 'A share root with inheritance intact' {
    BeforeAll {
        Set-TestEstate @{
            '\\fs01\finance' = New-Descriptor -Ace @(
                New-Ace -AceType 'AccessDenied' -AceFlags 3
                New-Ace -TrusteeSid $script:FinanceRw -TrusteeName 'CORP\Finance-RW' -AccessMask 1245631 -AceFlags 3
                New-Ace -TrusteeSid 'S-1-5-32-544' -TrusteeName 'BUILTIN\Administrators' -AccessMask 2032127 -AceFlags 19
            )
        }
        $settings = Import-AdgNtfsTarget -ShareRoot '\\FS01\Finance'
        $settings.RetryCount = 0
        $settings.RetryDelaySeconds = 0
        $script:Run = Get-TestRun -Settings $settings
        $script:Items = Get-Observations -Run $script:Run
    }

    It 'reports the directory and every entry of its DACL' {
        @(Select-Kind -Observations $script:Items -Kind 'ntfs_resource').Count | Should -Be 1
        @(Select-Kind -Observations $script:Items -Kind 'ntfs_ace').Count | Should -Be 3
    }

    It 'reports ace_count equal to the number of entries it sent' {
        # The contract requires the two to match; a resource claiming more entries than
        # arrived would look like ACEs lost in transit.
        $resource = @(Select-Kind -Observations $script:Items -Kind 'ntfs_resource')[0]
        $resource.ace_count | Should -Be @(Select-Kind -Observations $script:Items -Kind 'ntfs_ace').Count
    }

    It 'marks the root a boundary and leaves inheritance enabled' {
        $resource = @(Select-Kind -Observations $script:Items -Kind 'ntfs_resource')[0]
        $resource.inheritance_enabled | Should -BeTrue
        $resource.is_acl_boundary | Should -BeTrue
        $resource.depth_from_share_root | Should -Be 0
    }

    It 'carries an acl_hash the server can recompute' {
        $resource = @(Select-Kind -Observations $script:Items -Kind 'ntfs_resource')[0]
        $resource.acl_hash | Should -Match '^[0-9a-f]{64}$'
    }

    It 'hashes exactly the entries it reported' {
        $resource = @(Select-Kind -Observations $script:Items -Kind 'ntfs_resource')[0]
        $facts = @(Select-Kind -Observations $script:Items -Kind 'ntfs_ace') | ForEach-Object {
            [pscustomobject]@{
                TrusteeSid = $_.trustee_sid
                AceType    = $_.ace_type
                AccessMask = $_.access_mask
                AceFlags   = $_.ace_flags
                OrderIndex = $_.order_index
            }
        }
        $resource.acl_hash | Should -BeExactly (Get-AdgAclHash -DaclPresent $true -DaclProtected $false -Ace $facts)
    }

    It 'keeps the directory and its entries in one batch' {
        # The server verifies a reported acl_hash against the entries that arrive with it,
        # and skips the check when the batch holds fewer than ace_count. A split would
        # silently disable the one check that catches ACEs lost in transit.
        @($script:Run.Batches).Count | Should -Be 1
    }

    It 'succeeds, and still reconciles nothing' {
        # A share-root read enumerates no directory tree. Reconciling the directory_tree
        # scope would mark every directory under the root as deleted.
        $script:Run.Completion.status | Should -Be 'succeeded'
        @($script:Run.Completion.reconciled_scopes).Count | Should -Be 0
        $script:Run.Start.incremental | Should -BeTrue
    }

    It 'declares the tree it looked at, so a later full walk can reconcile it' {
        @($script:Run.Start.scopes).Count | Should -Be 1
        $script:Run.Start.scopes[0].kind | Should -Be 'directory_tree'
        $script:Run.Start.scopes[0].key | Should -Be '\\fs01\finance'
    }

    It 'names itself an ntfs collector reading a security descriptor' {
        $script:Run.Start.source.collector | Should -Be 'ntfs'
        $script:Run.Start.source.method | Should -Be 'DirectorySecurity.GetSecurityDescriptorBinaryForm'
    }
}

Describe 'A share root that blocks inheritance' {
    BeforeAll {
        Set-TestEstate @{
            '\\fs01\locked' = New-Descriptor -DaclProtected $true -Ace @(New-Ace -AceFlags 3)
        }
        $settings = Import-AdgNtfsTarget -ShareRoot '\\FS01\Locked'
        $settings.RetryCount = 0
        $script:Run = Get-TestRun -Settings $settings
        $script:Items = Get-Observations -Run $script:Run
    }

    It 'reports the protection explicitly rather than leaving it to be inferred' {
        $resource = @(Select-Kind -Observations $script:Items -Kind 'ntfs_resource')[0]
        $resource.dacl_protected | Should -BeTrue
        $resource.inheritance_enabled | Should -BeFalse
        $resource.is_acl_boundary | Should -BeTrue
    }

    It 'hashes differently from the same entries without protection' {
        $protected = @(Select-Kind -Observations $script:Items -Kind 'ntfs_resource')[0].acl_hash
        $entry = [pscustomobject]@{
            TrusteeSid = 'S-1-5-11'; AceType = 'allow'; AccessMask = 1179817; AceFlags = 3; OrderIndex = 0
        }
        $protected | Should -Not -Be (Get-AdgAclHash -DaclPresent $true -DaclProtected $false -Ace @($entry))
    }
}

Describe 'A share root with a NULL DACL' {
    BeforeAll {
        Set-TestEstate @{ '\\fs01\wide' = New-Descriptor -DaclPresent $false }
        $settings = Import-AdgNtfsTarget -ShareRoot '\\FS01\Wide'
        $settings.RetryCount = 0
        $script:Run = Get-TestRun -Settings $settings
        $script:Items = Get-Observations -Run $script:Run
    }

    It 'reports it as dacl_present false with no entries, never as an empty ACL' {
        # A NULL DACL grants every user full access; an empty DACL grants nobody access.
        # Collapsing them would invert the answer.
        $resource = @(Select-Kind -Observations $script:Items -Kind 'ntfs_resource')[0]
        $resource.dacl_present | Should -BeFalse
        $resource.ace_count | Should -Be 0
        @(Select-Kind -Observations $script:Items -Kind 'ntfs_ace').Count | Should -Be 0
    }

    It 'raises it as a finding as well as reporting it' {
        $script:Run.Completion.status | Should -Be 'partial'
        @($script:Run.Completion.errors)[0].code | Should -Be 'null_dacl'
    }
}

Describe 'A share root whose descriptor cannot be read' {
    BeforeAll {
        Set-TestEstate @{ '\\fs01\secret' = $null }
        $settings = Import-AdgNtfsTarget -ShareRoot '\\FS01\Secret'
        $settings.RetryCount = 0
        $settings.RetryDelaySeconds = 0
        $script:Run = Get-TestRun -Settings $settings
    }

    It 'reports no resource observation at all' {
        # An observation asserts the object was seen. An unreadable descriptor was not seen,
        # and a resource row with no ACEs would read as "nobody has access".
        #
        # Assigned before counting: Get-Observations returns through the `, $array` idiom,
        # so @(Get-Observations ...) would be a one-element array holding an empty one.
        $items = Get-Observations -Run $script:Run
        @($items).Count | Should -Be 0
    }

    It 'fails the run, because coverage is unknown rather than merely incomplete' {
        $script:Run.Completion.status | Should -Be 'failed'
        @($script:Run.Completion.errors)[0].code | Should -Be 'access_denied'
    }

    It 'says what right the read needed, and that ADG will not acquire it' {
        @($script:Run.Completion.errors)[0].message | Should -Match 'READ_CONTROL'
        @($script:Run.Completion.errors)[0].message | Should -Match 'never takes ownership'
    }
}

Describe 'A share root that is not there' {
    BeforeAll {
        Set-TestEstate @{}
        $settings = Import-AdgNtfsTarget -ShareRoot '\\FS02\Gone'
        $settings.RetryCount = 0
        $script:Run = Get-TestRun -Settings $settings
    }

    It 'distinguishes "not there" from "there and unreadable"' {
        # They call for different fixes, and an audit that reports them identically sends
        # somebody to the wrong place.
        @($script:Run.Completion.errors)[0].code | Should -Be 'path_not_found'
        $script:Run.Completion.status | Should -Be 'failed'
    }

    It 'still declares the scope it set out to look at' {
        @($script:Run.Start.scopes).Count | Should -Be 1
        @($script:Run.Completion.reconciled_scopes).Count | Should -Be 0
    }
}

Describe 'An orphaned trustee' {
    BeforeAll {
        Set-TestEstate @{
            '\\fs01\finance' = New-Descriptor -Ace @(
                New-Ace -TrusteeSid $script:Orphan -TrusteeName $null -AceFlags 3
            )
        }
        $settings = Import-AdgNtfsTarget -ShareRoot '\\FS01\Finance'
        $settings.RetryCount = 0
        $script:Run = Get-TestRun -Settings $settings
        $script:Items = Get-Observations -Run $script:Run
    }

    It 'reports the ACE and an unresolved principal beside it' {
        @(Select-Kind -Observations $script:Items -Kind 'ntfs_ace').Count | Should -Be 1
        $principal = @(Select-Kind -Observations $script:Items -Kind 'principal')[0]
        $principal.principal_kind | Should -Be 'unresolved'
        $principal.sid | Should -Be $script:Orphan
    }

    It 'still succeeds: an orphaned SID is a finding, not a failure' {
        $script:Run.Completion.status | Should -Be 'succeeded'
    }

    It 'omits the unresolved principal when the caller turned it off' {
        $settings = Import-AdgNtfsTarget -ShareRoot '\\FS01\Finance'
        $settings.RetryCount = 0
        $settings.ReportUnresolved = $false
        $run = Get-TestRun -Settings $settings

        $items = Get-Observations -Run $run
        @(Select-Kind -Observations $items -Kind 'principal').Count | Should -Be 0
        @(Select-Kind -Observations $items -Kind 'ntfs_ace').Count | Should -Be 1
    }
}

Describe 'A DACL holding an entry the contract cannot express' {
    BeforeAll {
        Set-TestEstate @{
            '\\fs01\odd' = New-Descriptor -Ace @(
                New-Ace -AceFlags 3
                New-Ace -AceType 'AccessAllowedCallback' -AceFlags 3 -TrusteeSid 'S-1-1-0' -TrusteeName 'Everyone'
            )
        }
        $settings = Import-AdgNtfsTarget -ShareRoot '\\FS01\Odd'
        $settings.RetryCount = 0
        $script:Run = Get-TestRun -Settings $settings
        $script:Items = Get-Observations -Run $script:Run
    }

    It 'reports what it could and records an error for what it could not' {
        @(Select-Kind -Observations $script:Items -Kind 'ntfs_ace').Count | Should -Be 1
        $script:Run.Completion.status | Should -Be 'partial'
        @($script:Run.Completion.errors)[0].code | Should -Be 'unmappable_ace_type'
    }

    It 'omits the acl_hash entirely rather than hashing half a DACL' {
        # A digest over part of a DACL is indistinguishable from a digest of all of it, and
        # comparing one to a parent's would answer the boundary question wrong without ever
        # looking wrong.
        $resource = @(Select-Kind -Observations $script:Items -Kind 'ntfs_resource')[0]
        $resource.Contains('acl_hash') | Should -BeFalse
    }
}

Describe 'Split-AdgNtfsObservationBatch' {
    It 'returns an empty array rather than nothing when there is nothing to send' {
        # A bare `return @()` unrolls to nothing, and .Count on $null then fails under
        # strict mode - in the caller, far from here.
        $batches = Split-AdgNtfsObservationBatch -RunId ([guid]::NewGuid().ToString()) -Group @()
        @($batches).Count | Should -Be 0
    }

    It 'never splits one directory across two batches' {
        $runId = [guid]::NewGuid().ToString()
        $group = @(1..7 | ForEach-Object { [ordered]@{ kind = 'ntfs_ace'; n = $_ } })
        $batches = Split-AdgNtfsObservationBatch -RunId $runId -Group @(, $group) -BatchSize 3

        @($batches).Count | Should -Be 1
        @($batches[0].observations).Count | Should -Be 7
    }

    It 'starts a new batch rather than overfilling one' {
        $runId = [guid]::NewGuid().ToString()
        $first = @(1..2 | ForEach-Object { [ordered]@{ n = $_ } })
        $second = @(3..4 | ForEach-Object { [ordered]@{ n = $_ } })
        $batches = Split-AdgNtfsObservationBatch -RunId $runId -Group @($first, $second) -BatchSize 3

        @($batches).Count | Should -Be 2
        $batches[0].sequence | Should -Be 1
        $batches[0].is_final | Should -BeFalse
        $batches[1].sequence | Should -Be 2
        $batches[1].is_final | Should -BeTrue
    }

    It 'gives every batch its own id, generated once so a retry is recognized' {
        $runId = [guid]::NewGuid().ToString()
        $group = @([ordered]@{ n = 1 })
        $batches = Split-AdgNtfsObservationBatch -RunId $runId -Group @($group, $group) -BatchSize 1

        $batches[0].batch_id | Should -Not -Be $batches[1].batch_id
        $batches[0].run_id | Should -Be $runId
    }

    It 'refuses a directory whose DACL exceeds the contract ceiling' {
        $group = @(1..1001 | ForEach-Object { [ordered]@{ n = $_ } })
        { Split-AdgNtfsObservationBatch -RunId ([guid]::NewGuid().ToString()) -Group @(, $group) } |
            Should -Throw '*ceiling of 1000*'
    }
}

Describe 'Several roots in one run' {
    BeforeAll {
        Set-TestEstate @{
            '\\fs01\finance'  = New-Descriptor -Ace @(New-Ace -AceFlags 3)
            '\\fs01\projects' = New-Descriptor -Ace @(New-Ace -AceFlags 3)
        }
        $script:Combined = Import-AdgNtfsTarget -ShareRoot '\\FS01\Finance', '\\FS01\Projects', '\\FS01\Gone'
        $script:Combined.RetryCount = 0
        $script:Combined.RetryDelaySeconds = 0
    }

    It 'declares one scope per root and reports the ones it could not read' {
        $run = Get-TestRun -Settings $script:Combined

        @($run.Start.scopes).Count | Should -Be 3
        $run.Completion.status | Should -Be 'partial'
        $run.Summary.ShareRootsRead | Should -Be 2
        @($run.Summary.ShareRootsUnread) | Should -Be @('\\FS01\Gone')
    }

    It 'contains the blast radius when asked for a run per root' {
        # One unreadable root downgrades a combined run; per-root runs keep the healthy ones
        # reporting 'succeeded', which is what an operator reads.
        $runs = Get-TestRunSet -Settings $script:Combined -PerShareRoot

        @($runs).Count | Should -Be 3
        @($runs | Where-Object { $_.Completion.status -eq 'succeeded' }).Count | Should -Be 2
        @($runs | Where-Object { $_.Completion.status -eq 'failed' }).Count | Should -Be 1
    }

    It 'gives each run its own id' {
        $runs = Get-TestRunSet -Settings $script:Combined -PerShareRoot
        @($runs | ForEach-Object { $_.RunId } | Sort-Object -Unique).Count | Should -Be 3
    }
}
