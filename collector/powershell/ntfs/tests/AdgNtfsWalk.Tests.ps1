#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Traversal: an imaginary estate of directories, with no file system.

    Every function that touches a file system lives in AdgNtfsSource.ps1 and is mocked here,
    so these tests exercise the parts that decide what a walk *means*: where it stops, what
    it refuses to descend into, what it reports as a place where permissions change, and -
    the thing that matters most - what it refuses to claim it enumerated.

    Two scoping rules meet in every mock below, and getting either wrong makes the mock
    silently do nothing:

      * the mock body runs in the module's session state, where nothing from this test file
        is visible. GetNewClosure() is what carries the estate in;
      * a closed-over body does NOT see the mocked function's parameters as inherited
        variables. $Path comes back empty, every lookup misses, and the estate looks empty -
        which reads exactly like a real file server where nothing is there. Declaring
        param() makes Pester bind the call's arguments instead.

    Concurrency is pinned at 1 throughout. That is not laziness: a PowerShell parallel
    runspace is a separate session state, a Pester mock does not exist inside one, and a
    suite that exercised the walk through the parallel path would silently be testing the
    real file system of whatever machine it ran on. Invoke-AdgParallelMap is tested at both
    settings below, with script blocks that need nothing mocked.
#>

BeforeAll {
    $moduleRoot = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $moduleRoot 'AdgNtfsCollector.psd1') -Force

    $script:Admins = 'S-1-5-32-544'
    $script:Users = 'S-1-5-21-1004336348-1177238915-682003330-1201'
    $script:Orphan = 'S-1-5-21-1004336348-1177238915-682003330-9999'

    function New-Ace {
        param(
            [string] $AceType = 'AccessAllowed',
            [int] $AceFlags = 0x03,
            [long] $AccessMask = 0x001200A9,
            [string] $TrusteeSid = 'S-1-5-32-544',
            [AllowNull()][string] $TrusteeName = 'BUILTIN\Administrators'
        )
        return [pscustomobject]@{
            AceType = $AceType; AceFlags = $AceFlags; AccessMask = $AccessMask
            TrusteeSid = $TrusteeSid; TrusteeName = $TrusteeName
        }
    }

    # One node of the imaginary estate. Dacl is the raw entry list; Children names the
    # subdirectories; Reparse marks a junction; Denied makes the descriptor read throw, and
    # ListDenied makes the enumeration throw - two different rights, and a directory can
    # legitimately grant one without the other.
    function New-Node {
        param(
            [string[]] $Children = @(),
            [object[]] $Dacl = @(),
            [bool] $Present = $true,
            [bool] $Protected = $false,
            [switch] $Reparse,
            [string] $LinkTarget,
            [switch] $Denied,
            [switch] $ListDenied,
            [string[]] $Files = @()
        )
        return @{
            Children = $Children; Dacl = $Dacl; Present = $Present; Protected = $Protected
            Reparse = [bool] $Reparse; LinkTarget = $LinkTarget
            Denied = [bool] $Denied; ListDenied = [bool] $ListDenied; Files = $Files
        }
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
            if ($node.ListDenied) { throw "Access to the path '$Path' is denied." }
            $children = foreach ($name in $node.Children) {
                $childPath = "$Path\$name"
                $child = $Estate[$childPath.ToLowerInvariant()]
                [pscustomobject]@{
                    Name = $name
                    Path = $childPath
                    IsReparsePoint = ($null -ne $child) -and $child.Reparse
                    LinkTarget = if ($null -eq $child) { $null } else { $child.LinkTarget }
                }
            }
            , @($children)
        }.GetNewClosure()

        Mock -ModuleName AdgNtfsCollector Get-AdgChildFile {
            param([string] $Path)
            $node = $Estate[$Path.ToLowerInvariant()]
            if ($null -eq $node) { throw "The system cannot find the path '$Path'." }
            $files = foreach ($name in $node.Files) {
                [pscustomobject]@{ Name = $name; Path = "$Path\$name"; IsReparsePoint = $false }
            }
            , @($files)
        }.GetNewClosure()

        Mock -ModuleName AdgNtfsCollector Get-AdgFileSecurity {
            param([string] $Path)
            $node = $Estate[$Path.ToLowerInvariant()]
            if ($null -eq $node) { throw "The system cannot find the path '$Path'." }
            if ($node.Denied) { throw "Access to the path '$Path' is denied." }
            @{
                OwnerSid = 'S-1-5-32-544'; GroupSid = $null
                DaclPresent = $node.Present; DaclProtected = $node.Protected; Ace = $node.Dacl
            }
        }.GetNewClosure()
    }

    # A three-level tree that inherits cleanly, so a test only has to state what it changes.
    function New-CleanEstate {
        $rootDacl = @((New-Ace -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x03),
            (New-Ace -TrusteeSid $Users -AccessMask 0x001200A9 -AceFlags 0x03))
        $inherited = @((New-Ace -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x13),
            (New-Ace -TrusteeSid $Users -AccessMask 0x001200A9 -AceFlags 0x13))
        return @{
            '\\fs01\finance' = New-Node -Children @('Reports', 'Payroll') -Dacl $rootDacl
            '\\fs01\finance\reports' = New-Node -Children @('Q3') -Dacl $inherited
            '\\fs01\finance\reports\q3' = New-Node -Dacl $inherited
            '\\fs01\finance\payroll' = New-Node -Dacl $inherited
        }
    }

    function Get-Settings {
        param([hashtable] $Override = @{})
        $settings = Import-AdgNtfsTarget -ScanRoot '\\FS01\Finance'
        foreach ($key in $Override.Keys) { $settings.$key = $Override[$key] }
        return $settings
    }

    # Runs a walk and returns everything a test might want to assert on. Observations are
    # collected here rather than in each test so the callback contract - one group per
    # resource, handed over as it is read - is exercised exactly once.
    function Invoke-TestWalk {
        param(
            [Parameter(Mandatory)][pscustomobject] $Settings,
            [string[]] $ScanRoot = @('\\FS01\Finance'),
            [AllowNull()][pscustomobject] $Checkpoint,
            [AllowNull()][System.Nullable[datetime]] $Deadline
        )
        $groups = [System.Collections.Generic.List[object]]::new()
        $flushes = [ref] 0
        $onGroup = { param($group) $groups.Add($group) }.GetNewClosure()
        $onFlush = { $flushes.Value++ }.GetNewClosure()

        $result = Invoke-AdgNtfsDirectoryWalk -Settings $Settings -RunId ([guid]::NewGuid().ToString()) `
            -ScanRoot $ScanRoot -OnGroup $onGroup -OnFlush $onFlush -Checkpoint $Checkpoint -Deadline $Deadline

        $observations = [System.Collections.Generic.List[object]]::new()
        foreach ($group in $groups) { foreach ($item in $group) { $observations.Add($item) } }

        return [pscustomobject]@{
            Result = $result
            Groups = $groups.ToArray()
            Observations = $observations.ToArray()
            Resources = @($observations | Where-Object { $_.kind -eq 'ntfs_resource' })
            Flushes = $flushes.Value
        }
    }

    function Get-Resource {
        param([Parameter(Mandatory)] $Walk, [Parameter(Mandatory)][string] $Path)
        return @($Walk.Resources | Where-Object { $_.path -eq $Path })[0]
    }
}

Describe 'Invoke-AdgParallelMap' {
    It 'returns nothing for an empty input' {
        $result = Invoke-AdgParallelMap -Item @() -ScriptBlock { param($x) $x }
        @($result).Count | Should -Be 0
    }

    It 'preserves input order at concurrency 1' {
        $result = Invoke-AdgParallelMap -Item @(1, 2, 3) -ScriptBlock { param($x) $x * 2 }
        @($result | ForEach-Object { $_.Value }) | Should -Be @(2, 4, 6)
    }

    It 'preserves input order at concurrency above 1' {
        # Results come back in whatever order the runspaces finish; an ACL list whose order
        # depended on scheduling would not be the ACL that was read. The sleep makes the
        # completion order deliberately the reverse of the input order.
        $items = @(1, 2, 3, 4)
        $result = Invoke-AdgParallelMap -Item $items -ConcurrencyLimit 4 `
            -ImportModulePath (Join-Path (Split-Path -Parent $PSScriptRoot) 'AdgNtfsCollector.psd1') `
            -ScriptBlock { param($x) Start-Sleep -Milliseconds (50 * (5 - $x)); $x * 2 }
        @($result | ForEach-Object { $_.Value }) | Should -Be @(2, 4, 6, 8)
    }

    It 'captures a failure instead of throwing' {
        # One unreadable directory must not discard the whole level's work.
        $result = Invoke-AdgParallelMap -Item @(1, 0, 3) -ScriptBlock {
            param($x) if ($x -eq 0) { throw 'no' } else { $x }
        }
        @($result)[1].Ok | Should -BeFalse
        @($result)[1].Error | Should -Be 'no'
        @($result)[0].Ok | Should -BeTrue
        @($result)[2].Value | Should -Be 3
    }

    It 'refuses to go parallel without a module to import' {
        # A parallel runspace starts with an empty session state, so the script block would
        # not find a single one of the collector's functions. Failing here beats failing
        # once per directory in production.
        { Invoke-AdgParallelMap -Item @(1, 2) -ConcurrencyLimit 4 -ScriptBlock { param($x) $x } } |
            Should -Throw '*ImportModulePath*'
    }

    It 'runs inline for a single item whatever the limit' {
        # Which is what keeps a one-directory scan from paying for a runspace, and what lets
        # the mocked suites below use the walk at all.
        $result = Invoke-AdgParallelMap -Item @(7) -ConcurrencyLimit 8 -ScriptBlock { param($x) $x + 1 }
        @($result)[0].Value | Should -Be 8
    }
}

Describe 'Walking a tree that inherits cleanly' {
    BeforeEach {
        Set-TestEstate (New-CleanEstate)
        $script:Walk = Invoke-TestWalk -Settings (Get-Settings)
    }

    It 'reads every directory exactly once' {
        $Walk.Result.Metrics.DirectoriesRead | Should -Be 4
        @($Walk.Resources).Count | Should -Be 4
    }

    It 'reports the scan root as a boundary, because its parent was not read' {
        $root = Get-Resource $Walk '\\FS01\Finance'
        $root.is_acl_boundary | Should -BeTrue
        # share_root, not scan_root: the path IS the directory the share publishes, and its
        # parent lies outside the share rather than merely outside this run.
        $root.boundary_reason | Should -Be 'share_root'
    }

    It 'reports every inheriting directory as not a boundary' {
        foreach ($path in @('\\FS01\Finance\Reports', '\\FS01\Finance\Reports\Q3', '\\FS01\Finance\Payroll')) {
            $resource = Get-Resource $Walk $path
            $resource.is_acl_boundary | Should -BeFalse -Because "$path inherits cleanly"
            $resource.PSObject.Properties['boundary_reason'] | Should -BeNullOrEmpty
        }
    }

    It 'collapses the tree to the number of distinct ACL states in it' {
        # The whole economic case for a boundary scan: four directories, two distinct DACLs
        # (the root's explicit entries and the inherited copy everything below shares).
        $Walk.Result.Metrics.UniqueAclHashes | Should -Be 2
        $Walk.Result.Metrics.BoundariesFound | Should -Be 1
    }

    It 'derives depth from the path rather than from the walk' {
        (Get-Resource $Walk '\\FS01\Finance').depth_from_share_root | Should -Be 0
        (Get-Resource $Walk '\\FS01\Finance\Reports').depth_from_share_root | Should -Be 1
        (Get-Resource $Walk '\\FS01\Finance\Reports\Q3').depth_from_share_root | Should -Be 2
    }

    It 'records the parent digest it judged against' {
        $reports = Get-Resource $Walk '\\FS01\Finance\Reports'
        $root = Get-Resource $Walk '\\FS01\Finance'
        # Which reading of the parent the verdict was made against - not the value compared,
        # which is the parent's projection onto a child.
        $reports.parent_acl_hash | Should -Be $root.acl_hash
    }

    It 'leaves a scan root with no parent digest' {
        (Get-Resource $Walk '\\FS01\Finance').PSObject.Properties['parent_acl_hash'] | Should -BeNullOrEmpty
    }

    It 'reports every resource as a directory' {
        foreach ($resource in $Walk.Resources) { $resource.resource_kind | Should -Be 'directory' }
    }

    It 'keeps each resource with its own entries in one group' {
        # The backend skips its acl_hash check when a batch holds fewer than the declared
        # ace_count, so a resource separated from its ACEs silently disables the only check
        # that catches entries lost in transit.
        foreach ($group in $Walk.Groups) {
            $resources = @($group | Where-Object { $_.kind -eq 'ntfs_resource' })
            $resources.Count | Should -Be 1
            $aces = @($group | Where-Object { $_.kind -eq 'ntfs_ace' })
            $aces.Count | Should -Be $resources[0].ace_count
            foreach ($ace in $aces) { $ace.path | Should -Be $resources[0].path }
        }
    }

    It 'claims the root as exhaustive, having skipped nothing' {
        $root = @($Walk.Result.Roots)[0]
        $root.Exhaustive | Should -BeTrue
        $root.Read | Should -BeTrue
        @($root.Reasons).Count | Should -Be 0
    }

    It 'finishes' {
        $Walk.Result.Completed | Should -BeTrue
        $Walk.Result.TimedOut | Should -BeFalse
        @($Walk.Result.Frontier).Count | Should -Be 0
    }
}

Describe 'Where permissions change' {
    It 'reports a protected DACL as a boundary' {
        $estate = New-CleanEstate
        $estate['\\fs01\finance\payroll'] = New-Node -Protected $true `
            -Dacl @((New-Ace -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x03))
        Set-TestEstate $estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        $payroll = Get-Resource $walk '\\FS01\Finance\Payroll'
        $payroll.is_acl_boundary | Should -BeTrue
        $payroll.boundary_reason | Should -Be 'protected_dacl'
        $payroll.inheritance_enabled | Should -BeFalse
    }

    It 'reports an added explicit entry as a boundary' {
        $estate = New-CleanEstate
        $estate['\\fs01\finance\payroll'] = New-Node -Dacl @(
            (New-Ace -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x13),
            (New-Ace -TrusteeSid $Users -AccessMask 0x001200A9 -AceFlags 0x13),
            (New-Ace -TrusteeSid $Orphan -AccessMask 0x001F01FF -AceFlags 0x03 -TrusteeName $null)
        )
        Set-TestEstate $estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        $payroll = Get-Resource $walk '\\FS01\Finance\Payroll'
        $payroll.is_acl_boundary | Should -BeTrue
        $payroll.boundary_reason | Should -Be 'acl_differs_from_parent'
    }

    It 'reports a removed inherited entry as a boundary' {
        # Possible when a parent's ACL was changed without propagating, which is exactly the
        # drift a boundary scan is meant to surface.
        $estate = New-CleanEstate
        $estate['\\fs01\finance\payroll'] = New-Node -Dacl @(
            (New-Ace -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x13)
        )
        Set-TestEstate $estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        (Get-Resource $walk '\\FS01\Finance\Payroll').boundary_reason | Should -Be 'acl_differs_from_parent'
    }

    It 'reports a reordered DACL as a boundary' {
        # A Deny moved below an Allow grants access that was previously refused, so the
        # order is part of the digest and therefore part of the comparison.
        $estate = New-CleanEstate
        $estate['\\fs01\finance\payroll'] = New-Node -Dacl @(
            (New-Ace -TrusteeSid $Users -AccessMask 0x001200A9 -AceFlags 0x13),
            (New-Ace -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x13)
        )
        Set-TestEstate $estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        (Get-Resource $walk '\\FS01\Finance\Payroll').boundary_reason | Should -Be 'acl_differs_from_parent'
    }

    It 'does not report a directory as a boundary merely for being one level deeper' {
        # The failure this whole design exists to avoid: comparing a child against its
        # parent's own digest rather than against the parent's projection would mark every
        # directory in the estate a boundary, and bury the ones that matter.
        Set-TestEstate (New-CleanEstate)
        $walk = Invoke-TestWalk -Settings (Get-Settings)
        $walk.Result.Metrics.BoundariesFound | Should -Be 1
    }

    It 'reports a NULL DACL as a boundary and as a finding' {
        $estate = New-CleanEstate
        $estate['\\fs01\finance\payroll'] = New-Node -Present $false -Dacl @()
        Set-TestEstate $estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        $payroll = Get-Resource $walk '\\FS01\Finance\Payroll'
        $payroll.dacl_present | Should -BeFalse
        $payroll.ace_count | Should -Be 0
        $payroll.boundary_reason | Should -Be 'null_dacl'
        @($walk.Result.Errors | Where-Object { $_.code -eq 'null_dacl' }).Count | Should -Be 1
    }

    It 'reports the children of a NULL DACL as parent_null_dacl' {
        # A NULL DACL projects nothing: what a child of it holds comes from the creating
        # process's default DACL, which is not a fact about the parent.
        $estate = New-CleanEstate
        $estate['\\fs01\finance\reports'] = New-Node -Children @('Q3') -Present $false -Dacl @()
        Set-TestEstate $estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        (Get-Resource $walk '\\FS01\Finance\Reports\Q3').boundary_reason | Should -Be 'parent_null_dacl'
    }

    It 'reports a walk started below a share root as scan_root' {
        Set-TestEstate (New-CleanEstate)
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ ScanRoots = @('\\FS01\Finance\Reports') }) `
            -ScanRoot @('\\FS01\Finance\Reports')

        $reports = Get-Resource $walk '\\FS01\Finance\Reports'
        $reports.is_acl_boundary | Should -BeTrue
        $reports.boundary_reason | Should -Be 'scan_root'
        # And its children are compared normally, because their parent WAS read.
        (Get-Resource $walk '\\FS01\Finance\Reports\Q3').is_acl_boundary | Should -BeFalse
    }

    It 'withholds a digest and falls back to unknown when part of a DACL could not be read' {
        # An entry type the contract cannot express makes the reading incomplete. A digest
        # over part of a DACL looks exactly like a digest of all of it, and comparing one to
        # a parent's projection would answer the boundary question wrong without ever
        # looking wrong.
        $estate = New-CleanEstate
        $estate['\\fs01\finance\payroll'] = New-Node -Dacl @(
            (New-Ace -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x13),
            (New-Ace -AceType 'SystemAudit' -TrusteeSid $Users)
        )
        Set-TestEstate $estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        $payroll = Get-Resource $walk '\\FS01\Finance\Payroll'
        $payroll.PSObject.Properties['acl_hash'] | Should -BeNullOrEmpty
        $payroll.boundary_reason | Should -Be 'parent_unreadable'
        @($walk.Result.Errors | Where-Object { $_.code -eq 'unmappable_ace_type' }).Count | Should -Be 1
    }

    It 'passes parent_unreadable down to the children of a directory it could not hash' {
        $estate = New-CleanEstate
        $estate['\\fs01\finance\reports'] = New-Node -Children @('Q3') -Dacl @(
            (New-Ace -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x13),
            (New-Ace -AceType 'SystemAudit' -TrusteeSid $Users)
        )
        Set-TestEstate $estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        (Get-Resource $walk '\\FS01\Finance\Reports\Q3').boundary_reason | Should -Be 'parent_unreadable'
    }
}

Describe 'Depth' {
    It 'reads only the roots at depth 0' {
        Set-TestEstate (New-CleanEstate)
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ MaxDepth = 0 })

        @($walk.Resources).Count | Should -Be 1
        $walk.Result.Metrics.SkippedDepthLimited | Should -Be 2
    }

    It 'stops at the configured depth' {
        Set-TestEstate (New-CleanEstate)
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ MaxDepth = 1 })

        @($walk.Resources | ForEach-Object { $_.path }) | Should -Contain '\\FS01\Finance\Reports'
        @($walk.Resources | ForEach-Object { $_.path }) | Should -Not -Contain '\\FS01\Finance\Reports\Q3'
    }

    It 'records the truncation as an error and drops the root exhaustive claim' {
        Set-TestEstate (New-CleanEstate)
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ MaxDepth = 1 })

        @($walk.Result.Errors | Where-Object { $_.code -eq 'depth_limit_reached' }).Count | Should -Be 1
        @($walk.Result.Roots)[0].Exhaustive | Should -BeFalse
        @($walk.Result.Roots)[0].Reasons | Should -Contain 'depth_limit'
    }

    It 'stays exhaustive when the limit is never actually reached' {
        # A depth limit is an upper bound, and a shallow tree does not hit it. Reporting a
        # tree as truncated because a limit existed would forbid reconciliation on every
        # configured scan.
        Set-TestEstate (New-CleanEstate)
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ MaxDepth = 64 })

        @($walk.Result.Errors | Where-Object { $_.code -eq 'depth_limit_reached' }).Count | Should -Be 0
        @($walk.Result.Roots)[0].Exhaustive | Should -BeTrue
    }
}

Describe 'Reparse points' {
    BeforeEach {
        $script:Estate = New-CleanEstate
        # A junction pointing back at the root of the tree it sits in: the shape that turns
        # a naive walk into one that never terminates.
        $script:Estate['\\fs01\finance'].Children = @('Reports', 'Payroll', 'Archive')
        $script:Estate['\\fs01\finance\archive'] = New-Node -Children @('Old') -Reparse `
            -LinkTarget 'D:\Shares\Finance' -Dacl @(
            (New-Ace -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x13),
            (New-Ace -TrusteeSid $Users -AccessMask 0x001200A9 -AceFlags 0x13))
        $script:Estate['\\fs01\finance\archive\old'] = New-Node -Dacl @()
    }

    It 'reads a junction own descriptor and does not descend, by default' {
        Set-TestEstate $Estate
        $walk = Invoke-TestWalk -Settings (Get-Settings)

        @($walk.Resources | ForEach-Object { $_.path }) | Should -Contain '\\FS01\Finance\Archive'
        @($walk.Resources | ForEach-Object { $_.path }) | Should -Not -Contain '\\FS01\Finance\Archive\Old'
        $walk.Result.Metrics.SkippedReparsePoints | Should -Be 1
    }

    It 'does not read a junction at all under the ignore policy' {
        Set-TestEstate $Estate
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ ReparsePointPolicy = 'ignore' })

        @($walk.Resources | ForEach-Object { $_.path }) | Should -Not -Contain '\\FS01\Finance\Archive'
        $walk.Result.Metrics.SkippedReparsePoints | Should -Be 1
    }

    It 'descends under the follow policy' {
        Set-TestEstate $Estate
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ ReparsePointPolicy = 'follow' })

        @($walk.Resources | ForEach-Object { $_.path }) | Should -Contain '\\FS01\Finance\Archive\Old'
    }

    It 'stops a junction cycle at the repeated target, not at the depth limit' {
        # The shape that a visited-path set cannot catch. Archive links to the tree it sits
        # in, and its subtree contains another link to the same target - so following it
        # produces \\...\Archive\Nested, \\...\Archive\Nested\Nested, and so on, every one a
        # path nothing has seen before. Only the repeated reparse target ends it.
        $Estate['\\fs01\finance\archive'].Children = @('Nested')
        $Estate['\\fs01\finance\archive\nested'] = New-Node -Reparse -LinkTarget 'D:\Shares\Finance' `
            -Dacl @((New-Ace -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x13),
            (New-Ace -TrusteeSid $Users -AccessMask 0x001200A9 -AceFlags 0x13))
        Set-TestEstate $Estate

        $walk = Invoke-TestWalk -Settings (Get-Settings @{ ReparsePointPolicy = 'follow' })
        $walk.Result.Completed | Should -BeTrue
        @($walk.Result.Errors | Where-Object { $_.code -eq 'traversal_loop' }).Count | Should -Be 1
        # The second junction is still read - it is a real directory with a real ACL - and
        # nothing below it is invented.
        @($walk.Resources | ForEach-Object { $_.path }) | Should -Contain '\\FS01\Finance\Archive\Nested'
        @($walk.Resources | ForEach-Object { $_.path }) |
            Should -Not -Contain '\\FS01\Finance\Archive\Nested\Nested'
        # Nowhere near the depth limit: the cycle was caught by the target, not by the stop.
        @($walk.Result.Roots)[0].Reasons | Should -Not -Contain 'depth_limit'
    }

    It 'does not follow a junction whose target it cannot read' {
        # An unverifiable link is exactly the one that might be the cycle.
        $Estate['\\fs01\finance\archive'].LinkTarget = $null
        Set-TestEstate $Estate

        $walk = Invoke-TestWalk -Settings (Get-Settings @{ ReparsePointPolicy = 'follow' })
        @($walk.Result.Errors | Where-Object { $_.code -eq 'reparse_target_unknown' }).Count | Should -Be 1
        @($walk.Resources | ForEach-Object { $_.path }) | Should -Contain '\\FS01\Finance\Archive'
        @($walk.Resources | ForEach-Object { $_.path }) | Should -Not -Contain '\\FS01\Finance\Archive\Old'
    }

    It 'reports a directory once however many paths reach it' {
        # Two junctions to one target, reached by different routes.
        $Estate['\\fs01\finance'].Children = @('Reports', 'Payroll', 'Archive', 'Mirror')
        $Estate['\\fs01\finance\mirror'] = New-Node -Children @('Old') -Reparse `
            -LinkTarget 'D:\Shares\Finance\Archive' -Dacl @()
        # Both links resolve onto the same child path in this estate.
        Set-TestEstate $Estate

        $walk = Invoke-TestWalk -Settings (Get-Settings @{ ReparsePointPolicy = 'follow' })
        $paths = @($walk.Resources | ForEach-Object { $_.path.ToLowerInvariant() })
        ($paths | Sort-Object -Unique).Count | Should -Be $paths.Count
    }

    It 'does not blame the depth limit for a junction the policy declined' {
        # Found by a real walk of a real tree, and invisible to every test in this file until
        # this one. The walk used to express "do not descend past this" by queueing the
        # junction at maxDepth, and the generic depth-limit branch then reported the stop as
        # "maxDepth is 20 and this directory sits at that depth" - about a junction at depth
        # 2. An operator raising maxDepth would see the message again, unchanged, and the
        # run's own account of why it could not reconcile named a setting that had nothing to
        # do with it.
        Set-TestEstate $Estate
        $walk = Invoke-TestWalk -Settings (Get-Settings)

        @($walk.Result.Errors | Where-Object { $_.code -eq 'depth_limit_reached' }) | Should -BeNullOrEmpty
        @($walk.Result.Roots)[0].Reasons | Should -Not -Contain 'depth_limit'
        $walk.Result.Metrics.SkippedDepthLimited | Should -Be 0
        # Still counted, and counted as what it is.
        $walk.Result.Metrics.SkippedReparsePoints | Should -Be 1
    }

    It 'does not even list a junction it has already decided not to descend into' {
        # The listing's every outcome would be discarded, and it is a round trip to a remote
        # server per junction. On a junction whose target is gone it is worse than wasted: the
        # listing fails, and the failure reads as a permissions problem.
        Set-TestEstate $Estate
        [void] (Invoke-TestWalk -Settings (Get-Settings))

        Should -Invoke Get-AdgChildDirectory -ModuleName AdgNtfsCollector -Times 0 -Exactly `
            -ParameterFilter { $Path -eq '\\FS01\Finance\Archive' }
    }

    It 'blames the link rather than the rights when a followed junction cannot be listed' {
        # The other half of the same finding. A junction to a decommissioned volume produced
        # "could not be enumerated ... Listing a directory needs FILE_LIST_DIRECTORY", which
        # sends somebody to look at permissions on a directory whose permissions are fine.
        $Estate['\\fs01\finance\archive'].ListDenied = $true
        Set-TestEstate $Estate

        $walk = Invoke-TestWalk -Settings (Get-Settings @{ ReparsePointPolicy = 'follow' })
        $errors = @($walk.Result.Errors | Where-Object { $_.target -eq '\\FS01\Finance\Archive' })
        $errors.Count | Should -Be 1
        $errors[0].code | Should -Be 'reparse_target_unreadable'
        $errors[0].message | Should -BeLike '*reparse point*'
        @($walk.Result.Roots)[0].Reasons | Should -Contain 'unreadable_link_target'
    }

    It 'still blames the rights when an ordinary directory cannot be listed' {
        # The control for the test above: the reparse-specific message must not swallow the
        # ordinary case, which is the common one and the one that really is about a right.
        $Estate['\\fs01\finance\reports'].ListDenied = $true
        Set-TestEstate $Estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        $errors = @($walk.Result.Errors | Where-Object { $_.target -eq '\\FS01\Finance\Reports' })
        $errors.Count | Should -Be 1
        $errors[0].code | Should -Be 'access_denied'
        $errors[0].message | Should -BeLike '*FILE_LIST_DIRECTORY*'
    }

    It 'never claims an exhaustive tree when a junction was not followed' {
        # A path under a junction is a distinct resource key, so skipping it leaves real
        # paths unreported - and a scope reconciled on that basis would mark them absent.
        Set-TestEstate $Estate
        $walk = Invoke-TestWalk -Settings (Get-Settings)
        @($walk.Result.Roots)[0].Exhaustive | Should -BeFalse
        @($walk.Result.Roots)[0].Reasons | Should -Contain 'reparse_point'
    }
}

Describe 'Include and exclude' {
    It 'does not read or descend into an excluded subtree' {
        Set-TestEstate (New-CleanEstate)
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ ExcludePaths = @('\\FS01\Finance\Reports') })

        $paths = @($walk.Resources | ForEach-Object { $_.path })
        $paths | Should -Not -Contain '\\FS01\Finance\Reports'
        $paths | Should -Not -Contain '\\FS01\Finance\Reports\Q3'
        $paths | Should -Contain '\\FS01\Finance\Payroll'
        $walk.Result.Metrics.SkippedExcluded | Should -Be 1
    }

    It 'passes through a directory outside includePaths to reach one inside it' {
        Set-TestEstate (New-CleanEstate)
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ IncludePaths = @('\\FS01\Finance\Reports\Q3') })

        $paths = @($walk.Resources | ForEach-Object { $_.path })
        $paths | Should -Be @('\\FS01\Finance\Reports\Q3')
        # Three directories passed over: the two ancestors the walk had to cross to reach
        # the match, and Payroll, which no pattern could ever reach below. All three were
        # visited and none reported, which is what an include filter means.
        $walk.Result.Metrics.SkippedNotInScope | Should -Be 3
        $walk.Result.Metrics.DirectoriesVisited | Should -Be 4
    }

    It 'reports a directory whose parent was passed through as unknown rather than unchanged' {
        # Nobody read the parent, so nobody knows whether the child differs from it.
        Set-TestEstate (New-CleanEstate)
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ IncludePaths = @('\\FS01\Finance\Reports\Q3') })
        (Get-Resource $walk '\\FS01\Finance\Reports\Q3').boundary_reason | Should -Be 'parent_unreadable'
    }

    It 'lets exclusion win over inclusion' {
        Set-TestEstate (New-CleanEstate)
        $walk = Invoke-TestWalk -Settings (Get-Settings @{
                IncludePaths = @('\\FS01\Finance')
                ExcludePaths = @('\\FS01\Finance\Reports')
            })
        @($walk.Resources | ForEach-Object { $_.path }) | Should -Not -Contain '\\FS01\Finance\Reports'
    }

    It 'never claims an exhaustive tree when either filter is set' {
        Set-TestEstate (New-CleanEstate)
        $excluded = Invoke-TestWalk -Settings (Get-Settings @{ ExcludePaths = @('\\FS01\Finance\Payroll') })
        @($excluded.Result.Roots)[0].Exhaustive | Should -BeFalse

        $included = Invoke-TestWalk -Settings (Get-Settings @{ IncludePaths = @('\\FS01\Finance') })
        @($included.Result.Roots)[0].Exhaustive | Should -BeFalse
        @($included.Result.Roots)[0].Reasons | Should -Contain 'include_filter'
    }
}

Describe 'What cannot be read' {
    It 'emits no resource observation for a descriptor it could not read' {
        # A row with no ACEs would read as "nobody has access", which is the opposite of
        # unknown.
        $estate = New-CleanEstate
        $estate['\\fs01\finance\payroll'] = New-Node -Denied
        Set-TestEstate $estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        @($walk.Resources | ForEach-Object { $_.path }) | Should -Not -Contain '\\FS01\Finance\Payroll'
        @($walk.Result.Errors | Where-Object { $_.code -eq 'access_denied' }).Count | Should -Be 1
        @($walk.Result.Roots)[0].Exhaustive | Should -BeFalse
    }

    It 'still reports a directory whose ACL it read and whose contents it could not list' {
        # Two different rights: READ_CONTROL reads the ACL, FILE_LIST_DIRECTORY lists the
        # contents, and an administrator can grant either without the other.
        $estate = New-CleanEstate
        $estate['\\fs01\finance\reports'] = New-Node -Children @('Q3') -ListDenied -Dacl @(
            (New-Ace -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x13),
            (New-Ace -TrusteeSid $Users -AccessMask 0x001200A9 -AceFlags 0x13))
        Set-TestEstate $estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        @($walk.Resources | ForEach-Object { $_.path }) | Should -Contain '\\FS01\Finance\Reports'
        @($walk.Resources | ForEach-Object { $_.path }) | Should -Not -Contain '\\FS01\Finance\Reports\Q3'
        @($walk.Result.Roots)[0].Reasons | Should -Contain 'unreadable_directory'
    }

    It 'reports an orphaned trustee as a principal observation' {
        $estate = New-CleanEstate
        $estate['\\fs01\finance\payroll'] = New-Node -Dacl @(
            (New-Ace -TrusteeSid $Orphan -AccessMask 0x001F01FF -AceFlags 0x13 -TrusteeName $null))
        Set-TestEstate $estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        $principals = @($walk.Observations | Where-Object { $_.kind -eq 'principal' })
        $principals.Count | Should -Be 1
        $principals[0].sid | Should -Be $Orphan
        $principals[0].principal_kind | Should -Be 'unresolved'
    }

    It 'carries on past an unreadable directory rather than abandoning the tree' {
        $estate = New-CleanEstate
        $estate['\\fs01\finance\reports'] = New-Node -Children @('Q3') -Denied
        Set-TestEstate $estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        @($walk.Resources | ForEach-Object { $_.path }) | Should -Contain '\\FS01\Finance\Payroll'
        $walk.Result.Completed | Should -BeTrue
    }
}

Describe 'File-level scanning' {
    BeforeEach {
        $script:Estate = New-CleanEstate
        $script:Estate['\\fs01\finance\payroll'].Files = @('budget.xlsx')
        # A file that inherited cleanly holds the OBJECT_INHERIT entries with every
        # inheritance flag stripped - which is a different document from what a subfolder
        # carries.
        $script:Estate['\\fs01\finance\payroll\budget.xlsx'] = New-Node -Dacl @(
            (New-Ace -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x10),
            (New-Ace -TrusteeSid $Users -AccessMask 0x001200A9 -AceFlags 0x10))
    }

    It 'reads no file by default' {
        Set-TestEstate $Estate
        $walk = Invoke-TestWalk -Settings (Get-Settings)
        $walk.Result.Metrics.FilesRead | Should -Be 0
        @($walk.Resources | Where-Object { $_.resource_kind -eq 'file' }).Count | Should -Be 0
    }

    It 'reads files when asked' {
        Set-TestEstate $Estate
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ IncludeFiles = $true })
        $walk.Result.Metrics.FilesRead | Should -Be 1
        $file = Get-Resource $walk '\\FS01\Finance\Payroll\budget.xlsx'
        $file.resource_kind | Should -Be 'file'
    }

    It 'compares a file against the object projection, not the container one' {
        # The mistake this guards against would report a boundary on every file in the
        # estate, because a file never carries the inheritance flags a folder does.
        Set-TestEstate $Estate
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ IncludeFiles = $true })
        (Get-Resource $walk '\\FS01\Finance\Payroll\budget.xlsx').is_acl_boundary | Should -BeFalse
    }

    It 'reports a file with an explicit entry as a boundary' {
        $Estate['\\fs01\finance\payroll\budget.xlsx'] = New-Node -Dacl @(
            (New-Ace -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x10),
            (New-Ace -TrusteeSid $Users -AccessMask 0x001200A9 -AceFlags 0x10),
            (New-Ace -TrusteeSid $Orphan -AccessMask 0x001F01FF -AceFlags 0x00 -TrusteeName $null))
        Set-TestEstate $Estate

        $walk = Invoke-TestWalk -Settings (Get-Settings @{ IncludeFiles = $true })
        $file = Get-Resource $walk '\\FS01\Finance\Payroll\budget.xlsx'
        $file.is_acl_boundary | Should -BeTrue
        $file.boundary_reason | Should -Be 'acl_differs_from_parent'
    }

    It 'never descends into a file' {
        Set-TestEstate $Estate
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ IncludeFiles = $true })
        # Four directories visited, whatever the files add.
        $walk.Result.Metrics.DirectoriesRead | Should -Be 4
    }

    It 'counts files and directories separately and both as ACLs read' {
        Set-TestEstate $Estate
        $walk = Invoke-TestWalk -Settings (Get-Settings @{ IncludeFiles = $true })
        $walk.Result.Metrics.AclsRead | Should -Be ($walk.Result.Metrics.DirectoriesRead + $walk.Result.Metrics.FilesRead)
    }
}

Describe 'Cancellation' {
    It 'stops at the deadline and keeps the rest of the frontier' {
        Set-TestEstate (New-CleanEstate)
        # Already past, so the walk stops before reading anything.
        $walk = Invoke-TestWalk -Settings (Get-Settings) -Deadline ([datetime]::UtcNow.AddSeconds(-1))

        $walk.Result.TimedOut | Should -BeTrue
        $walk.Result.Completed | Should -BeFalse
        @($walk.Result.Frontier).Count | Should -BeGreaterThan 0
        @($walk.Result.Errors | Where-Object { $_.code -eq 'timeout' }).Count | Should -Be 1
    }

    It 'never claims an exhaustive tree after a timeout' {
        Set-TestEstate (New-CleanEstate)
        $walk = Invoke-TestWalk -Settings (Get-Settings) -Deadline ([datetime]::UtcNow.AddSeconds(-1))
        @($walk.Result.Roots)[0].Exhaustive | Should -BeFalse
        @($walk.Result.Roots)[0].Reasons | Should -Contain 'timeout'
    }
}

Describe 'Metrics' {
    It 'tells directories visited from unique ACL states' {
        # The acceptance criterion in one assertion: an estate of many directories and few
        # distinct permission decisions must report both numbers, because the second is the
        # answer and the first is the cost of finding it.
        Set-TestEstate (New-CleanEstate)
        $walk = Invoke-TestWalk -Settings (Get-Settings)

        $walk.Result.Metrics.DirectoriesVisited | Should -Be 4
        $walk.Result.Metrics.UniqueAclHashes | Should -Be 2
        $walk.Result.Metrics.UniqueAclHashes | Should -BeLessThan $walk.Result.Metrics.DirectoriesRead
    }

    It 'keeps the four kinds of skip apart' {
        # Four different operator actions - a junction policy, an exclude pattern, a depth
        # limit, an include filter - and an operator reading one summed number cannot tell
        # which of their own settings produced it.
        $metrics = New-AdgNtfsWalkMetric
        foreach ($name in @('SkippedReparsePoints', 'SkippedExcluded', 'SkippedDepthLimited', 'SkippedNotInScope')) {
            $metrics.PSObject.Properties[$name] | Should -Not -BeNullOrEmpty
        }
    }

    It 'records elapsed time' {
        Set-TestEstate (New-CleanEstate)
        $walk = Invoke-TestWalk -Settings (Get-Settings)
        $walk.Result.Metrics.ElapsedSeconds | Should -BeGreaterOrEqual 0
    }

    It 'counts errors' {
        $estate = New-CleanEstate
        $estate['\\fs01\finance\payroll'] = New-Node -Denied
        Set-TestEstate $estate

        $walk = Invoke-TestWalk -Settings (Get-Settings)
        $walk.Result.Metrics.Errors | Should -Be 1
    }
}
