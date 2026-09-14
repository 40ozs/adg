#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Normalization: raw security descriptors to contract observations, with nothing else.

    Every function under test here is pure, so these tests need no file system, no share,
    and no ACL. That is the point of the layering: normalization is where a collector most
    easily starts inventing facts, so it is the part that gets exercised exhaustively.

    Two groups of tests below are not ordinary unit tests but pins on cross-language
    agreement, and they fail loudly on purpose:

      * the source keys must match backend/app/contracts/v1/keys.py. The server recomputes
        every key and rejects a mismatch, so a divergence is a rejected run;
      * the ACL normal form must match backend/app/domain/acl_hash.py byte for byte. The
        server recomputes the digest from the ACEs it stores, so a divergence would show up
        as a permanent, unexplainable disagreement on every directory in the estate.

    backend/tests/contracts/test_ntfs_collector.py checks both against the real Python.
    These tests state what the shape is supposed to be, so a failure here says which rule
    broke rather than only that two hex strings differ.
#>

BeforeAll {
    $moduleRoot = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $moduleRoot 'AdgNtfsCollector.psd1') -Force

    $script:RunId = '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31'
    $script:Observed = '2026-09-14T08:02:00.000Z'
    $script:FinanceRw = 'S-1-5-21-1004336348-1177238915-682003330-1202'
    $script:Contractors = 'S-1-5-21-1004336348-1177238915-682003330-1310'
    $script:Orphan = 'S-1-5-21-1004336348-1177238915-682003330-9999'

    function New-Ace {
        param(
            [string] $AceType = 'AccessAllowed',
            [int] $AceFlags = 0,
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

    function New-Fact {
        param(
            [string] $TrusteeSid = 'S-1-5-11',
            [string] $AceType = 'allow',
            [long] $AccessMask = 1179817,
            [int] $AceFlags = 3,
            [Nullable[int]] $OrderIndex = $null
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

Describe 'ConvertTo-AdgUncPath' {
    It 'canonicalizes the spellings that mean the same directory' -ForEach @(
        @{ Spelling = '\\FS01\Finance'; Expected = '\\FS01\Finance' }
        @{ Spelling = '\\FS01\Finance\'; Expected = '\\FS01\Finance' }
        @{ Spelling = '//FS01/Finance'; Expected = '\\FS01\Finance' }
        @{ Spelling = '\\?\UNC\FS01\Finance'; Expected = '\\FS01\Finance' }
        @{ Spelling = '  \\FS01\Finance  '; Expected = '\\FS01\Finance' }
        @{ Spelling = '\\FS01\Finance\Reports'; Expected = '\\FS01\Finance\Reports' }
    ) {
        ConvertTo-AdgUncPath $Spelling | Should -Be $Expected
    }

    It 'preserves the case it was given, because comparison folds case and display does not' {
        ConvertTo-AdgUncPath '\\fs01\FINANCE' | Should -Be '\\fs01\FINANCE'
        Get-AdgResourceComparisonKey '\\fs01\FINANCE' | Should -Be '\\fs01\finance'
    }

    It 'treats two spellings of one directory as one identity' {
        Get-AdgResourceComparisonKey '\\FS01\Finance' |
            Should -Be (Get-AdgResourceComparisonKey '//fs01/finance/')
    }

    It 'refuses a path it would have to guess at' -ForEach @(
        @{ Spelling = 'D:\Shares\Finance'; Because = 'a drive-letter path does not say which server' }
        @{ Spelling = '\\FS01'; Because = 'it names no share' }
        @{ Spelling = '\\FS01\Finance\..\Payroll'; Because = 'resolving .. needs the file system' }
        @{ Spelling = ''; Because = 'a directory is identified by its path' }
    ) {
        { ConvertTo-AdgUncPath $Spelling } | Should -Throw
    }
}

Describe 'Source keys' {
    It 'derives a resource key from the case-folded canonical path' {
        Get-AdgNtfsResourceKey '\\FS01\Finance' | Should -BeExactly 'resource|\\fs01\finance'
    }

    It 'derives an ACE key that includes the mask and the flags byte' {
        Get-AdgNtfsAceKey -Path '\\FS01\Finance' -TrusteeSid $script:FinanceRw -AceType 'allow' `
            -AccessMask 1245631 -AceFlags 3 |
            Should -BeExactly "ntfs_ace|\\fs01\finance|$($script:FinanceRw)|allow|0x001301bf|0x03"
    }

    It 'gives one entry the same key wherever it sits in the DACL' {
        # order_index is deliberately absent from the key: an administrator reordering an
        # ACL must not look like every entry being deleted and recreated.
        $first = Get-AdgNtfsAceKey -Path '\\FS01\Finance' -TrusteeSid $script:FinanceRw -AceType 'allow' -AccessMask 1245631 -AceFlags 3
        $second = Get-AdgNtfsAceKey -Path '\\fs01\FINANCE' -TrusteeSid $script:FinanceRw -AceType 'allow' -AccessMask 1245631 -AceFlags 3
        $first | Should -BeExactly $second
    }

    It 'gives two entries different keys when only the flags byte differs' {
        # ObjectInherit and ContainerInherit apply to different children: same trustee, same
        # mask, different grant.
        $objectInherit = Get-AdgNtfsAceKey -Path '\\FS01\Finance' -TrusteeSid $script:FinanceRw -AceType 'allow' -AccessMask 1245631 -AceFlags 1
        $containerInherit = Get-AdgNtfsAceKey -Path '\\FS01\Finance' -TrusteeSid $script:FinanceRw -AceType 'allow' -AccessMask 1245631 -AceFlags 2
        $objectInherit | Should -Not -Be $containerInherit
    }
}

Describe 'ConvertTo-AdgNtfsAceObservation' {
    It 'reports an explicit entry as explicit and an inherited one as inherited' {
        $result = ConvertTo-AdgNtfsAceObservation -RunId $script:RunId -Path '\\FS01\Finance' -Ace @(
            New-Ace -AceFlags 3
            New-Ace -AceFlags 19   # 0x13: ContainerInherit + ObjectInherit + INHERITED
        ) -ObservedAt $script:Observed

        @($result.Observations)[0].source | Should -Be 'explicit'
        @($result.Observations)[1].source | Should -Be 'inherited'
    }

    It 'derives source from the INHERITED bit rather than trusting a caller' {
        # The contract rejects a payload whose source and 0x10 disagree, so the collector
        # must never be able to send one.
        $result = ConvertTo-AdgNtfsAceObservation -RunId $script:RunId -Path '\\FS01\Finance' `
            -Ace @(New-Ace -AceFlags 0x10) -ObservedAt $script:Observed

        @($result.Observations)[0].source | Should -Be 'inherited'
        @($result.Observations)[0].ace_flags | Should -Be 0x10
    }

    It 'reports a Deny entry as deny, in the position the descriptor put it' {
        # Canonical ordering puts Deny first, and that is where it has to stay: moved below
        # the Allow entries it would stop denying.
        $result = ConvertTo-AdgNtfsAceObservation -RunId $script:RunId -Path '\\FS01\Finance' -Ace @(
            New-Ace -AceType 'AccessDenied' -TrusteeSid $script:Contractors -AceFlags 3
            New-Ace -AceType 'AccessAllowed' -TrusteeSid $script:FinanceRw -AceFlags 3
        ) -ObservedAt $script:Observed

        @($result.Observations)[0].ace_type | Should -Be 'deny'
        @($result.Observations)[0].order_index | Should -Be 0
        @($result.Observations)[1].ace_type | Should -Be 'allow'
        @($result.Observations)[1].order_index | Should -Be 1
    }

    It 'never expands a generic mask' {
        # GENERIC_ALL. Windows maps it through the object's generic mapping at access time;
        # doing that here would bake one interpretation into a stored fact.
        $result = ConvertTo-AdgNtfsAceObservation -RunId $script:RunId -Path '\\FS01\Finance' `
            -Ace @(New-Ace -AccessMask 268435456 -AceFlags 3) -ObservedAt $script:Observed

        @($result.Observations)[0].access_mask | Should -Be 268435456
    }

    It 'keeps an INHERIT_ONLY entry, which grants nothing here but everything below' {
        $result = ConvertTo-AdgNtfsAceObservation -RunId $script:RunId -Path '\\FS01\Finance' `
            -Ace @(New-Ace -AceFlags 11) -ObservedAt $script:Observed

        @($result.Observations).Count | Should -Be 1
        @($result.Observations)[0].ace_flags | Should -Be 11
    }

    It 'reports an unresolvable trustee as an unresolved principal and keeps its ACE' {
        $result = ConvertTo-AdgNtfsAceObservation -RunId $script:RunId -Path '\\FS01\Finance' `
            -Ace @(New-Ace -TrusteeSid $script:Orphan -TrusteeName $null -AceFlags 3) `
            -ObservedAt $script:Observed

        @($result.Observations).Count | Should -Be 1
        @($result.Observations)[0].trustee_sid | Should -Be $script:Orphan
        @($result.Principals).Count | Should -Be 1
        @($result.Principals)[0].principal_kind | Should -Be 'unresolved'
        # The contract forbids a name on an unresolved principal; none may be invented.
        @($result.Principals)[0].Contains('display_name') | Should -BeFalse
    }

    It 'reports one unresolved principal however many entries name the same orphan' {
        $result = ConvertTo-AdgNtfsAceObservation -RunId $script:RunId -Path '\\FS01\Finance' -Ace @(
            New-Ace -TrusteeSid $script:Orphan -TrusteeName $null -AceFlags 3
            New-Ace -TrusteeSid $script:Orphan -TrusteeName $null -AceFlags 11
        ) -ObservedAt $script:Observed

        @($result.Observations).Count | Should -Be 2
        @($result.Principals).Count | Should -Be 1
    }

    It 'records an entry it cannot classify as an error, and stops claiming the ACL is whole' {
        # An audit entry governs logging, not access; a callback ACE is evaluated through a
        # conditional expression the contract has no field for. Neither may be reported as
        # an ordinary grant, and neither may be dropped in silence.
        $result = ConvertTo-AdgNtfsAceObservation -RunId $script:RunId -Path '\\FS01\Finance' -Ace @(
            New-Ace -AceFlags 3
            New-Ace -AceType 'AccessAllowedCallback' -AceFlags 3
        ) -ObservedAt $script:Observed

        @($result.Observations).Count | Should -Be 1
        @($result.Errors).Count | Should -Be 1
        @($result.Errors)[0].code | Should -Be 'unmappable_ace_type'
        $result.Complete | Should -BeFalse
    }

    It 'counts a dropped entry when numbering the ones it kept' {
        # order_index is the position in the DACL, not the position in the output: an
        # evaluation order with a hole in it is still the real evaluation order.
        $result = ConvertTo-AdgNtfsAceObservation -RunId $script:RunId -Path '\\FS01\Finance' -Ace @(
            New-Ace -AceType 'AccessAllowedCallback' -AceFlags 3
            New-Ace -AceFlags 3
        ) -ObservedAt $script:Observed

        @($result.Observations)[0].order_index | Should -Be 1
    }

    It 'refuses to report an entry with no usable trustee SID' {
        $result = ConvertTo-AdgNtfsAceObservation -RunId $script:RunId -Path '\\FS01\Finance' `
            -Ace @(New-Ace -TrusteeSid '' -TrusteeName $null) -ObservedAt $script:Observed

        @($result.Observations).Count | Should -Be 0
        @($result.Errors)[0].code | Should -Be 'lookup_failed'
    }
}

Describe 'ConvertTo-AdgNtfsResourceObservation' {
    It 'derives the server and share from the path itself' {
        $resource = ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Finance' `
            -DaclPresent $true -AceCount 2 -ObservedAt $script:Observed

        $resource.server_name | Should -Be 'FS01'
        $resource.share_name | Should -Be 'Finance'
        $resource.source_key | Should -BeExactly 'resource|\\fs01\finance'
    }

    It 'reports a protected DACL as blocking inheritance and as a boundary' {
        $resource = ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Locked' `
            -DaclPresent $true -DaclProtected $true -AceCount 0 `
            -BoundaryReason 'protected_dacl' -ObservedAt $script:Observed

        $resource.dacl_protected | Should -BeTrue
        $resource.inheritance_enabled | Should -BeFalse
        # The contract rejects the pair being inconsistent: a directory refusing inherited
        # entries is by definition a place where permissions change.
        $resource.is_acl_boundary | Should -BeTrue
        $resource.boundary_reason | Should -Be 'protected_dacl'
    }

    It 'refuses a protected DACL that arrives without a reason' {
        # The pair can only be assembled by hand, and by hand is exactly where it goes
        # wrong. Resolve-AdgAclBoundary tests protection first, so a protected resource
        # always reaches here with a reason.
        { ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Locked' `
                -DaclPresent $true -DaclProtected $true -AceCount 0 -ObservedAt $script:Observed } |
            Should -Throw '*blocks inheritance*'
    }

    It 'derives is_acl_boundary from the reason rather than taking it separately' {
        # A boundary and the evidence for it cannot then disagree, which is the failure
        # contract 1.3 exists to prevent. A share root is a boundary because its parent lies
        # outside the share and there is nothing to compare against; "not a boundary" would
        # tell a later walk it could skip the one directory every path through the share
        # must pass.
        $boundary = ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Finance' `
            -DaclPresent $true -AceCount 2 -BoundaryReason 'share_root' -ObservedAt $script:Observed
        $inheriting = ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Finance\Reports' `
            -DaclPresent $true -AceCount 2 -ObservedAt $script:Observed

        $boundary.is_acl_boundary | Should -BeTrue
        $boundary.inheritance_enabled | Should -BeTrue
        $inheriting.is_acl_boundary | Should -BeFalse
        # Null is the only value meaning "carrying exactly what it inherited", and the
        # contract forbids a reason on a resource that is not a boundary - so the key is
        # omitted rather than sent as null.
        $inheriting.PSObject.Properties['boundary_reason'] | Should -BeNullOrEmpty
    }

    It 'refuses a reason the contract cannot express' {
        { ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Finance' `
                -DaclPresent $true -AceCount 0 -BoundaryReason 'felt_like_it' -ObservedAt $script:Observed } |
            Should -Throw '*not a boundary reason*'
    }

    It 'derives depth from the path rather than taking it from the caller' {
        # A resume from a checkpoint and a first full walk must report the same number for
        # the same directory, and only the path is common to both.
        $root = ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Finance' `
            -DaclPresent $true -AceCount 0 -BoundaryReason 'share_root' -ObservedAt $script:Observed
        $deep = ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Finance\Reports\Q3' `
            -DaclPresent $true -AceCount 0 -ObservedAt $script:Observed

        $root.depth_from_share_root | Should -Be 0
        $deep.depth_from_share_root | Should -Be 2
    }

    It 'reports a directory unless told otherwise' {
        $resource = ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Finance' `
            -DaclPresent $true -AceCount 0 -BoundaryReason 'share_root' -ObservedAt $script:Observed
        $resource.resource_kind | Should -Be 'directory'
    }

    It 'reports a file when a file-level scan read one' {
        $resource = ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId `
            -Path '\\FS01\Finance\Budget.xlsx' -DaclPresent $true -AceCount 2 `
            -ResourceKind 'file' -ObservedAt $script:Observed
        $resource.resource_kind | Should -Be 'file'
    }

    It 'refuses to report a share root as a file' {
        # A share publishes a directory. Accepting a file here would attach the share's
        # NTFS-layer link to a leaf nothing can be a child of.
        { ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Finance' `
                -DaclPresent $true -AceCount 0 -ResourceKind 'file' -ObservedAt $script:Observed } |
            Should -Throw '*always a directory*'
    }

    It 'records the parent digest the verdict was made against' {
        # Not the value compared - that is the parent's projection onto a child - but which
        # reading of the parent was judged, without which a later disagreement cannot be
        # told from the parent simply having changed in between.
        $resource = ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Finance\Reports' `
            -DaclPresent $true -AceCount 2 -ParentAclHash ('a' * 64) -ObservedAt $script:Observed
        $resource.parent_acl_hash | Should -Be ('a' * 64)
    }

    It 'distinguishes a NULL DACL from an empty one' {
        $nullDacl = ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Wide' `
            -DaclPresent $false -AceCount 0 -ObservedAt $script:Observed
        $emptyDacl = ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Locked' `
            -DaclPresent $true -AceCount 0 -ObservedAt $script:Observed

        # Everyone has full access, versus nobody does. Opposite facts, one field apart.
        $nullDacl.dacl_present | Should -BeFalse
        $emptyDacl.dacl_present | Should -BeTrue
        $nullDacl.ace_count | Should -Be 0
        $emptyDacl.ace_count | Should -Be 0
    }

    It 'refuses to report a NULL DACL that carries entries' {
        { ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Wide' `
                -DaclPresent $false -AceCount 3 -ObservedAt $script:Observed } |
            Should -Throw '*NULL DACL*'
    }

    It 'omits a field the source did not provide rather than sending null' {
        # The contract sets unevaluatedProperties to false and several fields reject null.
        $resource = ConvertTo-AdgNtfsResourceObservation -RunId $script:RunId -Path '\\FS01\Finance' `
            -DaclPresent $true -AceCount 0 -ObservedAt $script:Observed

        $resource.Contains('owner_sid') | Should -BeFalse
        $resource.Contains('local_path') | Should -BeFalse
        $resource.Contains('acl_hash') | Should -BeFalse
    }
}

Describe 'Get-AdgNormalizedAcl' {
    It 'produces the document the backend normalizer produces' {
        # Pinned literally. This exact text is what app/domain/acl_hash.py builds, and the
        # two digests are only equal because the bytes are.
        $normalized = Get-AdgNormalizedAcl -DaclPresent $true -DaclProtected $true -Ace @(
            New-Fact -TrusteeSid $script:FinanceRw -AccessMask 1245631 -AceFlags 3 -OrderIndex 0
            New-Fact -TrusteeSid 'S-1-5-32-544' -AccessMask 2032127 -AceFlags 19 -OrderIndex 1
        )

        $expected = "adg-acl/1`ndacl_present=true`ndacl_protected=true`norder=observed`n" +
        "ace=0|$($script:FinanceRw)|allow|0x001301bf|0x03`n" +
        "ace=1|S-1-5-32-544|allow|0x001f01ff|0x13`n"
        $normalized.Text | Should -BeExactly $expected
        $normalized.Digest | Should -BeExactly '0f8c3a6beab25438144c1407be0c2d04467246a1db1234dbb877ccde449cbd57'
    }

    It 'does not depend on the order the entries were handed over' {
        # A digest computed from rows read back in ace_key order must equal one computed
        # while walking the DACL, or the server could never agree with a collector.
        $forwards = Get-AdgAclHash -DaclPresent $true -Ace @(
            New-Fact -TrusteeSid 'S-1-1-0' -OrderIndex 0
            New-Fact -TrusteeSid 'S-1-5-32-544' -OrderIndex 1
        )
        $backwards = Get-AdgAclHash -DaclPresent $true -Ace @(
            New-Fact -TrusteeSid 'S-1-5-32-544' -OrderIndex 1
            New-Fact -TrusteeSid 'S-1-1-0' -OrderIndex 0
        )
        $forwards | Should -BeExactly $backwards
    }

    It 'ignores the numeric value of a position and keeps only its rank' {
        # One collector numbers around the entries it dropped, another numbers only what it
        # kept. Same ACL, same evaluation order, same digest.
        $contiguous = Get-AdgAclHash -DaclPresent $true -Ace @(
            New-Fact -TrusteeSid 'S-1-1-0' -OrderIndex 0
            New-Fact -TrusteeSid 'S-1-5-32-544' -OrderIndex 1
        )
        $gapped = Get-AdgAclHash -DaclPresent $true -Ace @(
            New-Fact -TrusteeSid 'S-1-1-0' -OrderIndex 4
            New-Fact -TrusteeSid 'S-1-5-32-544' -OrderIndex 9
        )
        $contiguous | Should -BeExactly $gapped
    }

    It 'changes when the entries are reordered' {
        # A Deny moved below an Allow grants access that was previously refused. A digest
        # that called those two ACLs equal would hide a real change.
        $denyFirst = Get-AdgAclHash -DaclPresent $true -Ace @(
            New-Fact -TrusteeSid 'S-1-1-0' -AceType 'deny' -OrderIndex 0
            New-Fact -TrusteeSid 'S-1-1-0' -AceType 'allow' -OrderIndex 1
        )
        $allowFirst = Get-AdgAclHash -DaclPresent $true -Ace @(
            New-Fact -TrusteeSid 'S-1-1-0' -AceType 'allow' -OrderIndex 0
            New-Fact -TrusteeSid 'S-1-1-0' -AceType 'deny' -OrderIndex 1
        )
        $denyFirst | Should -Not -Be $allowFirst
    }

    It 'never lets an unordered reading collide with an ordered one' {
        $ordered = Get-AdgNormalizedAcl -DaclPresent $true -Ace @(New-Fact -OrderIndex 0)
        $unordered = Get-AdgNormalizedAcl -DaclPresent $true -Ace @(New-Fact)

        $ordered.Ordered | Should -BeTrue
        $unordered.Ordered | Should -BeFalse
        $ordered.Digest | Should -Not -Be $unordered.Digest
    }

    It 'separates a NULL DACL from an empty one' {
        $nullDacl = Get-AdgAclHash -DaclPresent $false
        $emptyDacl = Get-AdgAclHash -DaclPresent $true
        $nullDacl | Should -Not -Be $emptyDacl
    }

    It 'separates a protected DACL from an unprotected one holding the same entries' {
        $protected = Get-AdgAclHash -DaclPresent $true -DaclProtected $true -Ace @(New-Fact -OrderIndex 0)
        $open = Get-AdgAclHash -DaclPresent $true -DaclProtected $false -Ace @(New-Fact -OrderIndex 0)
        $protected | Should -Not -Be $open
    }

    It 'can only ever be handed a canonically spelled SID' {
        # The normalizer does not fold SID case, and the Python mirror does - which would be
        # a silent, permanent hash disagreement if a non-canonical SID could reach either.
        # It cannot: Test-AdgSidString is case sensitive, so an ACE carrying 's-1-5-32-544'
        # is refused at the door rather than hashed.
        Test-AdgSidString 'S-1-5-32-544' | Should -BeTrue
        Test-AdgSidString 's-1-5-32-544' | Should -BeFalse

        $result = ConvertTo-AdgNtfsAceObservation -RunId $script:RunId -Path '\\FS01\Finance' `
            -Ace @(New-Ace -TrusteeSid 's-1-5-32-544' -TrusteeName $null) -ObservedAt $script:Observed
        @($result.Observations).Count | Should -Be 0
        @($result.Errors)[0].code | Should -Be 'lookup_failed'
    }

    It 'refuses a NULL DACL that carries entries' {
        { Get-AdgAclHash -DaclPresent $false -Ace @(New-Fact -OrderIndex 0) } | Should -Throw '*NULL DACL*'
    }

    It 'refuses two entries claiming one position' {
        # Ambiguous evaluation order would make the digest depend on the order the entries
        # happened to be read in, which is exactly what the normal form exists to remove.
        { Get-AdgAclHash -DaclPresent $true -Ace @(
                New-Fact -TrusteeSid 'S-1-1-0' -OrderIndex 0
                New-Fact -TrusteeSid 'S-1-5-32-544' -OrderIndex 0
            ) } | Should -Throw '*position*'
    }

    It 'is stable across repeated evaluation' {
        $entries = @(
            New-Fact -TrusteeSid $script:Contractors -AceType 'deny' -OrderIndex 0
            New-Fact -TrusteeSid $script:FinanceRw -AccessMask 1245631 -OrderIndex 1
        )
        $first = Get-AdgAclHash -DaclPresent $true -Ace $entries
        $second = Get-AdgAclHash -DaclPresent $true -Ace $entries
        $first | Should -BeExactly $second
        $first | Should -Match '^[0-9a-f]{64}$'
    }
}

Describe 'Get-AdgSha256Hex' {
    It 'hashes UTF-8 bytes with no byte-order mark' {
        # The empty string's SHA-256. A stray BOM would change every digest this collector
        # produces, and nothing else would look wrong.
        Get-AdgSha256Hex '' |
            Should -BeExactly 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    }
}
