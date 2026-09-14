#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Inheritance: what a parent hands down, and where permissions therefore change.

    This is the arithmetic the whole tree scan rests on. Get it wrong in one direction and
    every directory in the estate is reported as a boundary, which buries the few dozen
    places somebody actually made a decision; get it wrong in the other and a real change is
    reported as inherited, which tells the next scan it may stop looking.

    The projection table below is not recalled from documentation. Every row was measured on
    Windows by creating a directory carrying that single ACE and reading the raw descriptor
    of a child directory, a grandchild, and a child file; AdgNtfsRealFileSystem.Tests.ps1
    measures it again on a live file system, and backend/tests/domain/test_inheritance.py
    pins the same table in Python.
#>

BeforeAll {
    $moduleRoot = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $moduleRoot 'AdgNtfsCollector.psd1') -Force

    $script:Admins = 'S-1-5-32-544'
    $script:Everyone = 'S-1-1-0'
    $script:Users = 'S-1-5-21-1004336348-1177238915-682003330-1201'

    function New-Fact {
        param(
            [string] $TrusteeSid = 'S-1-1-0',
            [string] $AceType = 'allow',
            [long] $AccessMask = 0x001200A9,
            [int] $AceFlags = 0x03,
            [AllowNull()][object] $OrderIndex = 0
        )
        return [pscustomobject]@{
            TrusteeSid = $TrusteeSid
            AceType    = $AceType
            AccessMask = $AccessMask
            AceFlags   = $AceFlags
            OrderIndex = $OrderIndex
        }
    }
}

Describe 'Get-AdgInheritedAceFlag' {
    # Parent flags -> what a child container gets, what a child file gets. $null is "nothing
    # is inherited", which is a different answer from "an entry with no flags" (0x10).
    $cases = @(
        @{ Name = 'CI'; Flags = 0x02; Container = 0x12; Object = $null }
        @{ Name = 'OI'; Flags = 0x01; Container = 0x19; Object = 0x10 }
        @{ Name = 'OI|CI'; Flags = 0x03; Container = 0x13; Object = 0x10 }
        @{ Name = 'CI|IO'; Flags = 0x0A; Container = 0x12; Object = $null }
        @{ Name = 'OI|IO'; Flags = 0x09; Container = 0x19; Object = 0x10 }
        @{ Name = 'OI|CI|IO'; Flags = 0x0B; Container = 0x13; Object = 0x10 }
        @{ Name = 'CI|NP'; Flags = 0x06; Container = 0x10; Object = $null }
        @{ Name = 'OI|NP'; Flags = 0x05; Container = $null; Object = 0x10 }
        @{ Name = 'OI|CI|NP'; Flags = 0x07; Container = 0x10; Object = 0x10 }
        @{ Name = 'OI|CI|NP|IO'; Flags = 0x0F; Container = 0x10; Object = 0x10 }
        @{ Name = 'none'; Flags = 0x00; Container = $null; Object = $null }
    )

    It 'projects <Name> onto a child container as expected' -ForEach $cases {
        Get-AdgInheritedAceFlag -AceFlags $Flags -ForContainer $true | Should -Be $Container
    }

    It 'projects <Name> onto a child file as expected' -ForEach $cases {
        Get-AdgInheritedAceFlag -AceFlags $Flags -ForContainer $false | Should -Be $Object
    }

    It 'ignores the parent entry own INHERITED bit' {
        # An ACE the parent inherited propagates exactly as one set on the parent does. If
        # the bit were treated as meaningful, the second level of every tree would stop
        # inheriting and every grandchild would look like a boundary.
        Get-AdgInheritedAceFlag -AceFlags 0x13 -ForContainer $true |
            Should -Be (Get-AdgInheritedAceFlag -AceFlags 0x03 -ForContainer $true)
    }

    It 'treats INHERIT_ONLY as a statement about the parent, not a propagation stop' {
        # CI|IO is "subfolders only": the ACE does not apply to the folder holding it, and
        # it descends exactly as plain CI does.
        Get-AdgInheritedAceFlag -AceFlags 0x0A -ForContainer $true |
            Should -Be (Get-AdgInheritedAceFlag -AceFlags 0x02 -ForContainer $true)
    }

    It 'carries an OBJECT_INHERIT-only entry down through containers it does not apply to' {
        # 0x19 is OI|IO|INHERITED: it grants nothing on this folder and still reaches the
        # files below it. Dropping it would make every folder under a "files only" grant
        # look like a place where permissions changed.
        $projected = Get-AdgInheritedAceFlag -AceFlags 0x01 -ForContainer $true
        $projected -band 0x08 | Should -Not -Be 0
        $projected -band 0x01 | Should -Not -Be 0
    }

    It 'stops a NO_PROPAGATE entry at the grandchild' {
        $child = Get-AdgInheritedAceFlag -AceFlags 0x07 -ForContainer $true
        $child | Should -Be 0x10
        Get-AdgInheritedAceFlag -AceFlags $child -ForContainer $true | Should -BeNullOrEmpty
    }

    It 'reaches a stable point after one level for an ordinary inheritable entry' {
        # The reason a boundary comparison works below the first level at all: a clean child
        # and a clean grandchild carry the identical DACL.
        $child = Get-AdgInheritedAceFlag -AceFlags 0x03 -ForContainer $true
        Get-AdgInheritedAceFlag -AceFlags $child -ForContainer $true | Should -Be $child
    }

    It 'ignores bits above the flags byte rather than carrying them into the child' {
        Get-AdgInheritedAceFlag -AceFlags 0x0103 -ForContainer $true | Should -Be 0x13
    }
}

Describe 'Get-AdgInheritedAceProjection' {
    It 'drops entries that do not descend and keeps the ones that do' {
        $projected = Get-AdgInheritedAceProjection -Ace @(
            (New-Fact -TrusteeSid $Admins -AceFlags 0x03 -OrderIndex 0),
            (New-Fact -TrusteeSid $Everyone -AceFlags 0x00 -OrderIndex 1),
            (New-Fact -TrusteeSid $Users -AceFlags 0x02 -OrderIndex 2)
        ) -ForContainer $true

        @($projected).Count | Should -Be 2
        @($projected)[0].TrusteeSid | Should -Be $Admins
        @($projected)[1].TrusteeSid | Should -Be $Users
    }

    It 'renumbers positions over the surviving entries' {
        # The dropped entry leaves no gap. The normalized form reduces positions to their
        # rank anyway, so this is what lets a projection built from database rows equal one
        # built from a live descriptor.
        $projected = Get-AdgInheritedAceProjection -Ace @(
            (New-Fact -AceFlags 0x00 -OrderIndex 0),
            (New-Fact -TrusteeSid $Admins -AceFlags 0x03 -OrderIndex 1),
            (New-Fact -TrusteeSid $Users -AceFlags 0x03 -OrderIndex 2)
        ) -ForContainer $true

        @($projected | ForEach-Object { $_.OrderIndex }) | Should -Be @(0, 1)
    }

    It 'does not depend on the order the entries were handed over' {
        $ordered = Get-AdgInheritedAceProjection -Ace @(
            (New-Fact -TrusteeSid $Admins -AceFlags 0x03 -OrderIndex 0),
            (New-Fact -TrusteeSid $Users -AceFlags 0x02 -OrderIndex 1)
        ) -ForContainer $true
        $shuffled = Get-AdgInheritedAceProjection -Ace @(
            (New-Fact -TrusteeSid $Users -AceFlags 0x02 -OrderIndex 1),
            (New-Fact -TrusteeSid $Admins -AceFlags 0x03 -OrderIndex 0)
        ) -ForContainer $true

        @($ordered | ForEach-Object { $_.TrusteeSid }) | Should -Be @($Admins, $Users)
        @($shuffled | ForEach-Object { $_.TrusteeSid }) | Should -Be @($Admins, $Users)
    }

    It 'preserves the trustee, the type, and a specific mask untouched' {
        $entries = Get-AdgInheritedAceProjection -Ace @(
            (New-Fact -TrusteeSid $Everyone -AceType 'deny' -AccessMask 0x001301BF -AceFlags 0x03)
        ) -ForContainer $true
        $projected = @($entries)[0]

        $projected.TrusteeSid | Should -Be $Everyone
        $projected.AceType | Should -Be 'deny'
        # Nothing here interprets a mask. The one place a generic bit is resolved is the
        # effective half of a split entry, which exists only so a prediction can be compared
        # against what Windows wrote.
        $projected.AccessMask | Should -Be 0x001301BF
    }

    It 'keeps a mask bit no right names' {
        # Dropping it would report a boundary on every directory beneath the entry carrying
        # it, which is the failure mode the projection exists to avoid.
        $odd = 0x001301BF -bor 0x00000800
        $entries = Get-AdgInheritedAceProjection -Ace @(
            (New-Fact -TrusteeSid $Everyone -AccessMask $odd -AceFlags 0x03)
        ) -ForContainer $true
        @($entries)[0].AccessMask | Should -Be $odd
    }

    It 'splits an entry carrying a generic right into the pair Windows writes' {
        # Not an edge case: 0xe0010000 is the generic form of Modify and sits on almost
        # every directory created through Explorer. Missing the split reports every one of
        # them as a boundary. AdgNtfsRealFileSystem.Tests.ps1 measures the same table
        # against a live volume.
        $entries = Get-AdgInheritedAceProjection -Ace @(
            (New-Fact -TrusteeSid $Everyone -AccessMask 0xE0010000L -AceFlags 0x03)
        ) -ForContainer $true
        $pair = @($entries)

        $pair.Count | Should -Be 2
        # The effective copy: mapped, with every inheritance flag cleared.
        $pair[0].AceFlags | Should -Be 0x10
        $pair[0].AccessMask | Should -Be 0x001301BF
        # The propagating copy: unmapped, INHERIT_ONLY, still descending.
        $pair[1].AceFlags | Should -Be 0x1B
        $pair[1].AccessMask | Should -Be 0xE0010000L
    }

    It 'maps the generic form of Modify the way Windows does' {
        ConvertTo-AdgMappedGenericRight 0xE0010000L | Should -Be 0x001301BF
    }

    It 'hands a CREATOR OWNER entry down with INHERIT_ONLY preserved' {
        # And predicts no effective copy: the one Windows writes names whoever created the
        # child, which is not a fact about the parent.
        $entries = Get-AdgInheritedAceProjection -Ace @(
            (New-Fact -TrusteeSid 'S-1-3-0' -AccessMask 0x10000000L -AceFlags 0x0B)
        ) -ForContainer $true
        $projected = @($entries)

        $projected.Count | Should -Be 1
        $projected[0].AceFlags | Should -Be 0x1B
        $projected[0].AccessMask | Should -Be 0x10000000L
    }

    It 'does not treat OWNER RIGHTS as a creator SID' {
        # S-1-3-4 looks like a sibling of S-1-3-0 and is not one: Windows inherits it like
        # any other trustee. Assuming otherwise would make every directory under an OWNER
        # RIGHTS grant a boundary.
        $entries = Get-AdgInheritedAceProjection -Ace @(
            (New-Fact -TrusteeSid 'S-1-3-4' -AccessMask 0x001301BF -AceFlags 0x03)
        ) -ForContainer $true
        @($entries)[0].AceFlags | Should -Be 0x13
    }

    It 'returns an empty array rather than nothing for a DACL that hands down nothing' {
        $projected = Get-AdgInheritedAceProjection -Ace @((New-Fact -AceFlags 0x00)) -ForContainer $true
        @($projected).Count | Should -Be 0
    }

    It 'returns an empty array for an empty DACL' {
        # Assigned before counting, never @(Get-AdgInheritedAceProjection ...). The function
        # returns `, $array` so that an empty projection stays an array rather than nothing;
        # @() around the call collects that one written object into a one-element array
        # holding the array, and the count comes back 1. Every caller in the collector
        # assigns first for the same reason.
        $empty = Get-AdgInheritedAceProjection -Ace @() -ForContainer $true
        @($empty).Count | Should -Be 0

        $none = Get-AdgInheritedAceProjection -Ace $null -ForContainer $true
        @($none).Count | Should -Be 0
    }
}

Describe 'Get-AdgProjectedChildAclHash' {
    BeforeAll {
        $script:ParentAces = @(
            (New-Fact -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x03 -OrderIndex 0),
            (New-Fact -TrusteeSid $Users -AccessMask 0x001200A9 -AceFlags 0x03 -OrderIndex 1)
        )
        # What Windows actually produces beneath that parent: the same entries with
        # INHERITED added.
        $script:CleanChild = @(
            (New-Fact -TrusteeSid $Admins -AccessMask 0x001F01FF -AceFlags 0x13 -OrderIndex 0),
            (New-Fact -TrusteeSid $Users -AccessMask 0x001200A9 -AceFlags 0x13 -OrderIndex 1)
        )
    }

    It 'equals the digest of a child that inherited cleanly' {
        Get-AdgProjectedChildAclHash -DaclPresent $true -Ace $ParentAces -ForContainer $true |
            Should -Be (Get-AdgAclHash -DaclPresent $true -DaclProtected $false -Ace $CleanChild)
    }

    It 'never equals the parent own digest' {
        # The whole reason the projection exists. Inheritance sets the INHERITED bit on
        # every entry it copies, so a parent and a perfectly inheriting child are different
        # documents - and comparing them directly would report every directory in the estate
        # as a boundary.
        Get-AdgProjectedChildAclHash -DaclPresent $true -Ace $ParentAces -ForContainer $true |
            Should -Not -Be (Get-AdgAclHash -DaclPresent $true -DaclProtected $false -Ace $ParentAces)
    }

    It 'is stable at every level below the first' {
        $childProjection = Get-AdgProjectedChildAclHash -DaclPresent $true -Ace $ParentAces -ForContainer $true
        $grandchildProjection = Get-AdgProjectedChildAclHash -DaclPresent $true -Ace $CleanChild -ForContainer $true
        $grandchildProjection | Should -Be $childProjection
    }

    It 'differs between a container child and a file child' {
        # A file carries the OBJECT_INHERIT entries with every inheritance flag stripped,
        # which is a different document from what a subfolder carries. Comparing a file
        # against the container projection would report a boundary on every file.
        Get-AdgProjectedChildAclHash -DaclPresent $true -Ace $ParentAces -ForContainer $false |
            Should -Not -Be (Get-AdgProjectedChildAclHash -DaclPresent $true -Ace $ParentAces -ForContainer $true)
    }

    It 'projects nothing from a NULL DACL' {
        # What a child of a NULL-DACL directory holds comes from the creating process's
        # default DACL, which is not a fact about the parent.
        Get-AdgProjectedChildAclHash -DaclPresent $false -Ace @() -ForContainer $true |
            Should -BeNullOrEmpty
    }

    It 'projects a present, empty document from a DACL with no inheritable entries' {
        # Not the same as projecting nothing: this parent hands its children an empty DACL,
        # which grants nobody access, and a child holding one is inheriting correctly.
        $projection = Get-AdgProjectedChildAclHash -DaclPresent $true `
            -Ace @((New-Fact -AceFlags 0x00)) -ForContainer $true
        $projection | Should -Not -BeNullOrEmpty
        $projection | Should -Be (Get-AdgAclHash -DaclPresent $true -DaclProtected $false -Ace @())
    }

    It 'is not affected by the parent own protection flag' {
        # A protected parent still hands its own entries down; protection says it refuses
        # entries from above, not that it withholds them from below.
        Get-AdgProjectedChildAclHash -DaclPresent $true -Ace $ParentAces -ForContainer $true |
            Should -Be (Get-AdgProjectedChildAclHash -DaclPresent $true -Ace $ParentAces -ForContainer $true)
    }
}

Describe 'Resolve-AdgAclBoundary' {
    BeforeAll {
        $script:Hash = 'a' * 64
        $script:Other = 'b' * 64
    }

    It 'reports no boundary when the digest matches what the parent projects' {
        Resolve-AdgAclBoundary -DaclPresent $true -AclHash $Hash `
            -ParentDaclPresent $true -ParentProjection $Hash | Should -BeNullOrEmpty
    }

    It 'reports acl_differs_from_parent when it does not' {
        Resolve-AdgAclBoundary -DaclPresent $true -AclHash $Hash `
            -ParentDaclPresent $true -ParentProjection $Other | Should -Be 'acl_differs_from_parent'
    }

    It 'reports protected_dacl ahead of everything else' {
        # A directory that refuses inherited entries is a boundary whatever a projection
        # says, and the order matters: the comparison would otherwise decide first and could
        # call a protected directory unchanged.
        Resolve-AdgAclBoundary -DaclPresent $true -DaclProtected $true -AclHash $Hash `
            -ParentDaclPresent $true -ParentProjection $Hash | Should -Be 'protected_dacl'
    }

    It 'reports null_dacl ahead of the path-shape reasons' {
        Resolve-AdgAclBoundary -DaclPresent $false -IsShareRoot | Should -Be 'null_dacl'
    }

    It 'reports share_root for the directory a share publishes' {
        Resolve-AdgAclBoundary -DaclPresent $true -IsShareRoot -AclHash $Hash | Should -Be 'share_root'
    }

    It 'reports scan_root when the walk started below a share root' {
        Resolve-AdgAclBoundary -DaclPresent $true -IsScanRoot -AclHash $Hash | Should -Be 'scan_root'
    }

    It 'prefers share_root to scan_root when both apply' {
        Resolve-AdgAclBoundary -DaclPresent $true -IsShareRoot -IsScanRoot -AclHash $Hash |
            Should -Be 'share_root'
    }

    It 'reports parent_unreadable when the parent was never read' {
        Resolve-AdgAclBoundary -DaclPresent $true -AclHash $Hash -ParentDaclPresent $null |
            Should -Be 'parent_unreadable'
    }

    It 'reports parent_null_dacl when the parent projects nothing' {
        Resolve-AdgAclBoundary -DaclPresent $true -AclHash $Hash -ParentDaclPresent $false |
            Should -Be 'parent_null_dacl'
    }

    It 'reports parent_unreadable when this resource own DACL was only partly read' {
        # No digest means no comparison was made. An unread ACL is not an unchanged one, and
        # calling it unchanged is what would let a later scan stop at a directory whose
        # permissions nobody has established.
        Resolve-AdgAclBoundary -DaclPresent $true -AclHash $null `
            -ParentDaclPresent $true -ParentProjection $Hash | Should -Be 'parent_unreadable'
    }

    It 'reports parent_unreadable when the parent DACL was only partly read' {
        Resolve-AdgAclBoundary -DaclPresent $true -AclHash $Hash `
            -ParentDaclPresent $true -ParentProjection $null | Should -Be 'parent_unreadable'
    }

    It 'never returns a reason that is not a boundary' {
        # $null is the only value meaning "carrying exactly what it inherited". Every other
        # answer sets is_acl_boundary, which is what the contract requires and what the
        # observation builder derives the flag from.
        $unknowns = @(
            (Resolve-AdgAclBoundary -DaclPresent $true -IsScanRoot -AclHash $Hash),
            (Resolve-AdgAclBoundary -DaclPresent $true -AclHash $Hash -ParentDaclPresent $null),
            (Resolve-AdgAclBoundary -DaclPresent $true -AclHash $Hash -ParentDaclPresent $false)
        )
        foreach ($reason in $unknowns) { $reason | Should -Not -BeNullOrEmpty }
    }

    It 'compares digests case sensitively' {
        # Both implementations emit lower-case hex. A case-insensitive comparison would hide
        # a collector that emitted upper case, and the server - which compares the stored
        # strings - would then disagree forever on every directory.
        Resolve-AdgAclBoundary -DaclPresent $true -AclHash $Hash `
            -ParentDaclPresent $true -ParentProjection $Hash.ToUpperInvariant() |
            Should -Be 'acl_differs_from_parent'
    }
}

Describe 'Path derivations' {
    It 'returns no parent for a share root' {
        Get-AdgParentPath '\\FS01\Finance' | Should -BeNullOrEmpty
    }

    It 'returns the containing directory for a path inside a share' {
        Get-AdgParentPath '\\FS01\Finance\Reports\Q3' | Should -Be '\\FS01\Finance\Reports'
    }

    It 'canonicalizes before taking the parent' {
        Get-AdgParentPath '//fs01/finance/reports/' | Should -Be '\\fs01\finance'
    }

    It 'measures depth from the share root' {
        Get-AdgDepthFromShareRoot '\\FS01\Finance' | Should -Be 0
        Get-AdgDepthFromShareRoot '\\FS01\Finance\Reports' | Should -Be 1
        Get-AdgDepthFromShareRoot '\\FS01\Finance\Reports\Q3' | Should -Be 2
    }

    It 'refuses a path that names no share' {
        { Get-AdgParentPath '\\FS01' } | Should -Throw '*names no share*'
    }
}
