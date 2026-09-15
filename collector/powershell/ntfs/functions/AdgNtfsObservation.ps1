<#
    Contract layer: turn a raw security descriptor into contract v1 observations.

    Everything in this file is pure. No function here touches the network, the file system,
    or the clock except Get-AdgTimestamp, and every conversion is a total function of its
    arguments. That is deliberate: normalization is where a collector most easily starts
    inventing facts, so it is the part that must be exhaustively testable without a domain,
    a file server, or a single real ACL.

    Two derivations here are normative and must agree byte for byte with the backend:

      * the source keys, with backend/app/contracts/v1/keys.py. The server recomputes every
        key and rejects a mismatch, so a divergence is a rejected run, not a cosmetic one;
      * the ACL normal form and its hash, with backend/app/domain/acl_hash.py. The server
        recomputes the digest from the ACEs it stores and reports both, so a divergence
        would show up as a permanent, unexplainable disagreement on every directory.

    backend/tests/contracts/test_ntfs_collector.py runs this collector and checks both
    against the Python implementations. Changing either side alone fails that test.
#>

# Contract 1.3: ntfs_resource gained resource_kind, boundary_reason and parent_acl_hash.
$script:AdgSchemaVersion = '1.3'

# The minor that introduced affirmations. A batch declares it only when it carries one,
# because that is what "additive" means in both directions: a later minor's rule may not be
# applied to an earlier payload, and an earlier payload may not use a later minor's field.
$script:AdgIncrementalSchemaVersion = '1.4'

# Affirmations are a key and a digest rather than an object, so they are capped higher than
# observations. The ceiling still exists: a batch has to stay something a server can reject
# whole.
$script:AdgMaxBatchAffirmations = 5000

# First line of the normalized ACL document. Mirrors ACL_NORMAL_FORM_VERSION. A change of
# format changes this token, so two digests from different formats can never be compared as
# though they agreed.
$script:AdgAclNormalFormVersion = 'adg-acl/1'

# Mirrors SUBSTITUTED_TRUSTEES in app/domain/inheritance.py. CREATOR OWNER and CREATOR
# GROUP: a parent carrying one hands a child the propagating half of the entry plus an ACE
# naming whoever created that child, which is not a fact about the parent and cannot be
# predicted. OWNER RIGHTS (S-1-3-4) looks like a sibling and is not one - Windows inherits it
# like any other trustee and resolves it against the current owner at access time.
$script:AdgSubstitutedTrustees = @('S-1-3-0', 'S-1-3-1')

# Mirrors AclBoundaryReason in app/domain/inheritance.py, and the aclBoundaryReason
# enumeration in common.schema.json. Checked by hand rather than through a ValidateSet
# attribute, because $null has to be accepted here and is the most important value in the
# list: it is the only one that means "carrying exactly what it inherited", and a
# [string] parameter turns $null into an empty string that no ValidateSet can allow.
$script:AdgBoundaryReasons = @(
    'share_root'
    'scan_root'
    'protected_dacl'
    'null_dacl'
    'parent_null_dacl'
    'parent_unreadable'
    'acl_differs_from_parent'
)

function Get-AdgProperty {
    <#
        .SYNOPSIS
            Read a property that may not be there, without failing.
        .DESCRIPTION
            Strict mode makes a reference to a missing property an error, and the shapes
            this collector normalizes are not guaranteed. A property the source did not
            provide is a fact the source did not state, which is $null - not a reason to
            abandon the directory.

            Indexing PSObject.Properties rather than testing membership with -contains: the
            latter throws under Set-StrictMode when the property collection is empty.
    #>
    param(
        [AllowNull()] $InputObject,
        [Parameter(Mandatory)][string] $Name
    )

    if ($null -eq $InputObject) { return $null }
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-AdgTimestamp {
    <#
        .SYNOPSIS
            RFC 3339 timestamp in UTC.
        .DESCRIPTION
            The contract rejects a naive timestamp: observations collected from hosts in
            different time zones would otherwise be unorderable.
    #>
    [OutputType([string])]
    param([datetime] $Instant = [datetime]::UtcNow)

    return ([datetimeoffset]::new($Instant.ToUniversalTime(), [timespan]::Zero)).ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
}

# --- Path identity ----------------------------------------------------------------------

function ConvertTo-AdgUncPath {
    <#
        .SYNOPSIS
            Canonicalize a UNC path, or refuse it.
        .DESCRIPTION
            Mirrors app/domain/paths.py. One canonical spelling per directory is what stops
            \\FS01\Finance and \\fs01\finance\ from becoming two resources with two
            different sets of permissions.

            Normalization never guesses: forward slashes become backslashes and an extended
            prefix is stripped, because both are spellings of the same path, but '..' is
            rejected rather than resolved (resolving it needs the file system) and a host
            alias is left exactly as observed (resolving it needs DNS).
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][AllowEmptyString()][string] $Path)

    $text = ([string] $Path).Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        throw 'A resource path must not be empty. A directory is identified by its UNC path.'
    }

    $text = $text.Replace('/', '\')
    foreach ($prefix in @('\\?\UNC\', '\\.\UNC\')) {
        if ($text.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $text = '\\' + $text.Substring($prefix.Length)
            break
        }
    }
    $text = $text.TrimEnd('\')

    if (-not $text.StartsWith('\\')) {
        throw "'$Path' is not a UNC path. A directory is identified by \\server\share[\path]; a drive-letter path does not say which server it is on."
    }

    $parts = @($text.Substring(2).Split('\') | Where-Object { $_ -ne '' })
    if ($parts.Count -lt 2) {
        throw "'$Path' names no share. A UNC path must be at least \\server\share."
    }
    foreach ($part in $parts) {
        if ($part -eq '.' -or $part -eq '..') {
            throw "'$Path' contains a relative segment. ADG does not resolve '..' - doing so would need the file system, and a guess would name a different directory."
        }
        if ($part -match '[<>:"/\\|?*]' -or $part -match '[\x00-\x1f]') {
            throw "'$Path' contains a character Windows forbids in a path component."
        }
    }

    return '\\' + ($parts -join '\')
}

function Get-AdgResourceComparisonKey {
    <#
        .SYNOPSIS
            The case-folded canonical path: the identity of a directory.
        .DESCRIPTION
            Windows file systems are case-insensitive, so comparison folds case while the
            reported path keeps the spelling that was observed.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][string] $Path)
    return (ConvertTo-AdgUncPath $Path).ToLowerInvariant()
}

function Get-AdgNtfsResourceKey {
    <#
        .SYNOPSIS
            resource|<case-folded canonical UNC path>
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][string] $Path)
    return "resource|$(Get-AdgResourceComparisonKey $Path)"
}

function Get-AdgParentPath {
    <#
        .SYNOPSIS
            The containing directory's canonical UNC path, or $null at a share root.
        .DESCRIPTION
            Mirrors UncPath.parent. A share root's parent lies outside the share - often
            outside anything ADG audits - so there is nothing to return and nothing to
            compare a root against.

            Purely textual. Resolving a parent through the file system would need a round
            trip per directory during a walk that is already one round trip per directory,
            and it would answer differently for a path reached through a junction.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][string] $Path)

    $canonical = ConvertTo-AdgUncPath $Path
    $parts = @($canonical.Substring(2).Split('\'))
    if ($parts.Count -le 2) { return $null }
    return '\\' + (($parts[0..($parts.Count - 2)]) -join '\')
}

function Get-AdgDepthFromShareRoot {
    <#
        .SYNOPSIS
            How many directories below \\server\share this path sits. 0 at the root.
        .DESCRIPTION
            A function of the path and nothing else, which is why it is derived here rather
            than counted by the walk: a resume from a checkpoint, a scan rooted below the
            share root, and a first full walk must all report the same number for the same
            directory, and only the path is common to the three.
    #>
    [OutputType([int])]
    param([Parameter(Mandatory)][string] $Path)

    $canonical = ConvertTo-AdgUncPath $Path
    return (@($canonical.Substring(2).Split('\')).Count - 2)
}

function Get-AdgNtfsAceKey {
    <#
        .SYNOPSIS
            ntfs_ace|<resource>|<trustee>|<type>|0x%08x mask|0x%02x flags
        .DESCRIPTION
            The flags byte is part of the key because it is part of the grant: the same
            trustee, type, and mask carrying ObjectInherit and carrying ContainerInherit are
            two different entries applying to different children.

            order_index is deliberately absent. Two entries identical in trustee, type,
            mask, and flags are duplicates of one another, and an administrator reordering a
            DACL must not look like every entry being deleted and recreated. The order is
            still reported, on the ACE and in the ACL hash.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $Path,
        [Parameter(Mandatory)][string] $TrusteeSid,
        [Parameter(Mandatory)][ValidateSet('allow', 'deny')][string] $AceType,
        [Parameter(Mandatory)][long] $AccessMask,
        [Parameter(Mandatory)][int] $AceFlags
    )

    $resource = Get-AdgResourceComparisonKey $Path
    return ('ntfs_ace|{0}|{1}|{2}|0x{3:x8}|0x{4:x2}' -f $resource, $TrusteeSid, $AceType, $AccessMask, $AceFlags)
}

function Get-AdgPrincipalKey {
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $Sid,
        [Parameter(Mandatory)][string] $PrincipalKind,
        [string] $HostKey
    )

    if ($PrincipalKind -eq 'local_group') {
        if ([string]::IsNullOrWhiteSpace($HostKey)) {
            throw 'A local group needs a host: S-1-5-32-544 is identical on every computer.'
        }
        return "principal|$($HostKey.ToLowerInvariant())|$Sid"
    }
    return "principal|$Sid"
}

# --- The normalized ACL and its hash ------------------------------------------------------

function Get-AdgAceContentLine {
    <#
        .SYNOPSIS
            One ACE rendered without its position: everything the descriptor stores.
        .DESCRIPTION
            Mirrors AclAceFacts.content_line. Pure ASCII, so sorting these strings is an
            ordinal sort in both languages - which is what makes the two hashes agree.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $TrusteeSid,
        [Parameter(Mandatory)][ValidateSet('allow', 'deny')][string] $AceType,
        [Parameter(Mandatory)][long] $AccessMask,
        [Parameter(Mandatory)][int] $AceFlags
    )
    return ('{0}|{1}|0x{2:x8}|0x{3:x2}' -f $TrusteeSid, $AceType, $AccessMask, $AceFlags)
}

function Get-AdgNormalizedAcl {
    <#
        .SYNOPSIS
            Reduce a DACL to the canonical document the hash is taken over.
        .DESCRIPTION
            Mirrors app/domain/acl_hash.py::normalize_acl byte for byte. Read that module
            for why the document is shaped this way; the three decisions that matter here:

              * the owner is NOT part of it. Every folder under a root is legitimately owned
                by whoever created it while sharing one identical inherited DACL, and an
                owner-sensitive digest would mark every one of them a boundary;
              * order IS part of it, because Windows evaluates a DACL in order and a Deny
                moved below an Allow grants access that was previously refused. What is not
                part of it is the numeric order_index: positions are reduced to their rank,
                so two collectors that number a DACL differently still agree;
              * an ACL read without positions says order=unordered and sorts by content, so
                its digest can never collide with one from an ordered reading. The two are
                different-quality observations and must not look identical.

        .PARAMETER Ace
            Entries with TrusteeSid, AceType, AccessMask, AceFlags, and OrderIndex. Order in
            this array is irrelevant: the document is built from OrderIndex, so reading rows
            back in any order yields the same digest.

        .OUTPUTS
            A hashtable with Text, Digest, Ordered, and AceCount.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][bool] $DaclPresent,
        [bool] $DaclProtected = $false,
        [AllowNull()][object[]] $Ace
    )

    $entries = @($Ace ?? @())

    if (-not $DaclPresent -and $entries.Count -gt 0) {
        throw "A NULL DACL (dacl_present=false) grants everyone full access and carries no ACEs, but $($entries.Count) were supplied. A present-but-empty DACL - which grants nobody access - is the opposite fact."
    }

    $ordered = $true
    foreach ($entry in $entries) {
        if ($null -eq (Get-AdgProperty $entry 'OrderIndex')) { $ordered = $false; break }
    }

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add($script:AdgAclNormalFormVersion)
    $lines.Add("dacl_present=$(if ($DaclPresent) { 'true' } else { 'false' })")
    $lines.Add("dacl_protected=$(if ($DaclProtected) { 'true' } else { 'false' })")
    $lines.Add("order=$(if ($ordered) { 'observed' } else { 'unordered' })")

    $contents = [System.Collections.Generic.List[string]]::new()
    foreach ($entry in $entries) {
        $content = Get-AdgAceContentLine `
            -TrusteeSid ([string] (Get-AdgProperty $entry 'TrusteeSid')) `
            -AceType ([string] (Get-AdgProperty $entry 'AceType')) `
            -AccessMask ([long] (Get-AdgProperty $entry 'AccessMask')) `
            -AceFlags ([int] (Get-AdgProperty $entry 'AceFlags'))

        if ($ordered) {
            # Zero-padded so an ordinal string sort orders the positions numerically. The
            # width is fixed at 10, which is how the content is recovered below - and it is
            # the same key Python sorts on.
            $contents.Add(('{0:d10}|{1}' -f [int] (Get-AdgProperty $entry 'OrderIndex'), $content))
        }
        else {
            $contents.Add($content)
        }
    }

    # Ordinal, never culture-aware: Sort-Object and the default List.Sort() compare strings
    # by the current culture, which would order the same ACL differently on a machine with a
    # different locale and produce a digest Python could never reproduce.
    $contents.Sort([System.StringComparer]::Ordinal)

    if ($ordered) {
        $positions = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
        foreach ($key in $contents) {
            if (-not $positions.Add($key.Substring(0, 10))) {
                throw "Two ACEs claim DACL position $([int] $key.Substring(0, 10)). A position identifies one entry in the evaluation order; duplicates would make the normalized form depend on the order the entries happened to be read in."
            }
        }
        $rank = 0
        foreach ($key in $contents) {
            $lines.Add("ace=$rank|$($key.Substring(11))")
            $rank++
        }
    }
    else {
        foreach ($content in $contents) { $lines.Add("ace=-|$content") }
    }

    # Every line terminated, including the last: a document is a sequence of complete
    # records, so appending an entry cannot change the bytes of the ones before it.
    $text = ''
    foreach ($line in $lines) { $text += "$line`n" }

    return @{
        Text     = $text
        Digest   = Get-AdgSha256Hex $text
        Ordered  = $ordered
        AceCount = $entries.Count
    }
}

function Get-AdgSha256Hex {
    <#
        .SYNOPSIS
            Lower-case hexadecimal SHA-256 of a string's UTF-8 bytes.
        .DESCRIPTION
            UTF8Encoding constructed explicitly with no BOM: the static
            [System.Text.Encoding]::UTF8 emits none from GetBytes, but saying so removes the
            question, and a stray BOM would change every digest this collector produces.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][AllowEmptyString()][string] $Text)

    $encoding = [System.Text.UTF8Encoding]::new($false)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $sha.ComputeHash($encoding.GetBytes($Text))
    }
    finally {
        $sha.Dispose()
    }

    $builder = [System.Text.StringBuilder]::new(64)
    foreach ($byte in $bytes) { [void] $builder.Append($byte.ToString('x2')) }
    return $builder.ToString()
}

function Get-AdgAclHash {
    <#
        .SYNOPSIS
            The digest alone, for callers that only need to compare.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][bool] $DaclPresent,
        [bool] $DaclProtected = $false,
        [AllowNull()][object[]] $Ace
    )
    return (Get-AdgNormalizedAcl -DaclPresent $DaclPresent -DaclProtected $DaclProtected -Ace $Ace).Digest
}

# --- Inheritance: what a parent hands down, and where that stops ---------------------------

function ConvertTo-AdgMappedGenericRight {
    <#
        .SYNOPSIS
            An access mask with its generic bits replaced by what they stand for.
        .DESCRIPTION
            Mirrors app/domain/inheritance.py::map_generic_rights.

            GENERIC_READ and its siblings are not rights; they are an indirection Windows
            resolves through the object type's generic mapping when it materializes an ACE
            onto a real object. For a file-system object that mapping is fixed, and it is
            why a directory's stored DACL and its parent's inheritable entry can carry
            different masks while describing exactly the same grant.

            **This does not license expanding generic rights anywhere else.** Every mask this
            collector reports is exactly as read; a reported expansion would bake one
            interpretation into a stored fact. The expansion here is never reported - it
            exists so a prediction can be compared against what Windows actually wrote.
    #>
    [OutputType([long])]
    param([Parameter(Mandatory)][long] $Mask)

    $specific = $Mask -band 0x0FFFFFFFL
    if ($Mask -band 0x80000000L) { $specific = $specific -bor 0x00120089L }  # GENERIC_READ
    if ($Mask -band 0x40000000L) { $specific = $specific -bor 0x00120116L }  # GENERIC_WRITE
    if ($Mask -band 0x20000000L) { $specific = $specific -bor 0x001200A0L }  # GENERIC_EXECUTE
    if ($Mask -band 0x10000000L) { $specific = $specific -bor 0x001F01FFL }  # GENERIC_ALL
    return $specific
}

function Get-AdgInheritedAceFlag {
    <#
        .SYNOPSIS
            The flag byte a child receives from one ordinary parent ACE, or $null for none.
        .DESCRIPTION
            "Ordinary" is doing work: this is the single-entry case, which holds for an ACE
            whose mask carries no generic bits and whose trustee Windows does not
            substitute. Those two exceptions each split one parent entry into two child
            entries - see Get-AdgInheritedAce.

            Mirrors app/domain/inheritance.py::project_inherited_ace_flags. Read that module
            for the measured table; the three rules that are easiest to get wrong from
            memory:

              * INHERIT_ONLY is not a propagation stop. It says the ACE does not apply to
                the object holding it, which is a statement about the parent, and CI|IO
                propagates to a child container exactly as plain CI does.
              * an OBJECT_INHERIT-only ACE still reaches a child container, as
                OI|IO|INHERITED (0x19), so it can carry on down to the files below. Dropping
                it would make every folder under a "files only" grant look like a boundary.
              * NO_PROPAGATE_INHERIT clears OI, CI, NP and IO from the copy, which is what
                makes the grandchild inherit nothing.

            The parent ACE's own INHERITED bit is irrelevant - an entry the parent inherited
            propagates exactly as one set on the parent does - and is overwritten in the
            result.

        .PARAMETER ForContainer
            Whether the child is a directory. A file receives no inheritance flags at all,
            having nothing below it to pass them to.
    #>
    [OutputType([object])]
    param(
        [Parameter(Mandatory)][int] $AceFlags,
        [bool] $ForContainer = $true
    )

    $flags = $AceFlags -band 0xFF
    $objectInherit = ($flags -band 0x01) -ne 0
    $containerInherit = ($flags -band 0x02) -ne 0
    $noPropagate = ($flags -band 0x04) -ne 0

    if (-not $ForContainer) {
        # A file is a leaf: it either receives the entry or does not, and never carries
        # inheritance flags of its own. NO_PROPAGATE does not withhold it - that bit stops
        # grandchildren, and a file has none.
        if ($objectInherit) { return 0x10 }
        return $null
    }

    if ($containerInherit) {
        # Applies to this child and stops.
        if ($noPropagate) { return 0x10 }
        # Keeps propagating with the same reach. INHERIT_ONLY is dropped: the entry does
        # apply to the child container it just landed on.
        return (0x10 -bor ($flags -band 0x03))
    }

    if ($objectInherit) {
        # The entry is for immediate children that are files. This one is not.
        if ($noPropagate) { return $null }
        # Grants nothing on this container - which is what INHERIT_ONLY says - but has to
        # be carried so the files below still receive it.
        return 0x19
    }

    return $null
}

function Get-AdgInheritedAce {
    <#
        .SYNOPSIS
            What one parent ACE becomes on a child: nothing, one entry, or two.
        .DESCRIPTION
            Mirrors app/domain/inheritance.py::project_inherited_ace, which carries the full
            reasoning. The two-entry case is not an edge case: it fires on any ACE carrying
            a generic right, and 0xe0010000 - the generic form of Modify - sits on almost
            every directory created through Explorer. Missing it reports every one of those
            directories as a boundary, and it is invisible to any test whose fixture masks
            happen to be specific.

            A generic mask is an indirection Windows cannot apply to an object without
            resolving it, so materializing such an ACE onto a child writes both halves of
            what the parent meant: the **effective** copy, mapped and with every inheritance
            flag cleared, and the **propagating** copy, unmapped and INHERIT_ONLY so it keeps
            descending. The parent's own DACL holds the same pair, which is why this is a
            fixed point rather than something that grows with depth.

            CREATOR OWNER and CREATOR GROUP split halfway: the propagating copy descends
            with INHERIT_ONLY preserved, and the effective copy names whoever created the
            child - not a fact about the parent, so it is not predicted here.

        .OUTPUTS
            The entries, effective first then propagating, without OrderIndex.
    #>
    [OutputType([object[]])]
    param(
        [Parameter(Mandatory)] $Entry,
        [bool] $ForContainer = $true
    )

    $trustee = [string] (Get-AdgProperty $Entry 'TrusteeSid')
    $aceType = [string] (Get-AdgProperty $Entry 'AceType')
    $mask = [long] (Get-AdgProperty $Entry 'AccessMask')
    $flags = [int] (Get-AdgProperty $Entry 'AceFlags') -band 0xFF

    $containerInherit = ($flags -band 0x02) -ne 0
    $objectInherit = ($flags -band 0x01) -ne 0
    $noPropagate = ($flags -band 0x04) -ne 0

    $generic = ($mask -band 0xF0000000L) -ne 0
    $substituted = $trustee -in $script:AdgSubstitutedTrustees

    $projected = [System.Collections.Generic.List[object]]::new()

    if (-not $ForContainer) {
        # A file receives the effective copy or nothing. It never propagates, and a
        # substituted trustee's effective copy names the creator, which is unpredictable.
        if (-not $substituted -and $objectInherit) {
            $projected.Add([pscustomobject]@{
                    TrusteeSid = $trustee; AceType = $aceType
                    AccessMask = ConvertTo-AdgMappedGenericRight $mask
                    AceFlags   = 0x10
                })
        }
        return , $projected.ToArray()
    }

    if (-not ($generic -or $substituted)) {
        $single = Get-AdgInheritedAceFlag -AceFlags $flags -ForContainer $true
        if ($null -ne $single) {
            $projected.Add([pscustomobject]@{
                    TrusteeSid = $trustee; AceType = $aceType
                    AccessMask = $mask; AceFlags = [int] $single
                })
        }
        return , $projected.ToArray()
    }

    # The effective copy exists only where the entry applies to a child container, and only
    # where the trustee is knowable.
    if ($containerInherit -and -not $substituted) {
        $projected.Add([pscustomobject]@{
                TrusteeSid = $trustee; AceType = $aceType
                AccessMask = ConvertTo-AdgMappedGenericRight $mask
                AceFlags   = 0x10
            })
    }
    # The propagating copy carries the original mask unmapped, because it is still an
    # indirection for whatever object it eventually lands on.
    if (($containerInherit -or $objectInherit) -and -not $noPropagate) {
        $projected.Add([pscustomobject]@{
                TrusteeSid = $trustee; AceType = $aceType
                AccessMask = $mask
                AceFlags   = 0x10 -bor 0x08 -bor ($flags -band 0x03)
            })
    }
    return , $projected.ToArray()
}

function Get-AdgInheritedAceProjection {
    <#
        .SYNOPSIS
            The entries a child inherits from a parent DACL, in the parent's relative order.
        .DESCRIPTION
            Mirrors app/domain/inheritance.py::project_inherited_acl. Positions are
            renumbered from zero over the surviving entries, which loses nothing: the
            normalized form reduces positions to their rank anyway, and renumbering is what
            lets a projection built from a live descriptor equal one built from stored rows.

            A parent entry that does not descend is dropped rather than represented, so an
            empty result means "this parent hands its children nothing" - an ordinary DACL
            of explicit, non-inheritable entries. One carrying a generic right produces
            *two*, which is why this cannot be a simple map: see Get-AdgInheritedAce.
    #>
    [OutputType([object[]])]
    param(
        [AllowNull()][object[]] $Ace,
        [bool] $ForContainer = $true
    )

    $entries = @($Ace ?? @())
    if ($entries.Count -eq 0) { return , @() }

    # Ordinal throughout, and sorted by the reported position rather than by the order the
    # entries were handed over: rows read back from storage arrive in whatever order the
    # query produced, and the projection has to be the same document either way. Entries
    # with no position sort last, by content.
    $decorated = [System.Collections.Generic.List[object]]::new()
    foreach ($entry in $entries) {
        $content = Get-AdgAceContentLine `
            -TrusteeSid ([string] (Get-AdgProperty $entry 'TrusteeSid')) `
            -AceType ([string] (Get-AdgProperty $entry 'AceType')) `
            -AccessMask ([long] (Get-AdgProperty $entry 'AccessMask')) `
            -AceFlags ([int] (Get-AdgProperty $entry 'AceFlags'))

        $order = Get-AdgProperty $entry 'OrderIndex'
        $key = if ($null -eq $order) { "1|9999999999|$content" }
        else { '0|{0:d10}|{1}' -f [int] $order, $content }

        $decorated.Add([pscustomobject]@{ Key = $key; Entry = $entry })
    }
    # A .NET comparison rather than Sort-Object: the default string comparison is
    # culture-aware, which would order the same DACL differently on a machine with a
    # different locale and produce a projection Python could never reproduce.
    $decorated.Sort([System.Comparison[object]] {
            param($left, $right)
            [System.String]::CompareOrdinal($left.Key, $right.Key)
        })

    $projected = [System.Collections.Generic.List[object]]::new()
    foreach ($item in $decorated) {
        # Assigned before iterating: Get-AdgInheritedAce returns `, $array`, so piping or
        # wrapping the call would hand back an array holding an array.
        $children = Get-AdgInheritedAce -Entry $item.Entry -ForContainer $ForContainer
        foreach ($child in @($children)) {
            $projected.Add([pscustomobject]@{
                    TrusteeSid = $child.TrusteeSid
                    AceType    = $child.AceType
                    AccessMask = [long] $child.AccessMask
                    AceFlags   = [int] $child.AceFlags
                    OrderIndex = $projected.Count
                })
        }
    }

    # The comma keeps an empty projection an empty array rather than nothing at all.
    return , $projected.ToArray()
}

function Get-AdgProjectedChildAclHash {
    <#
        .SYNOPSIS
            The digest a cleanly inheriting child of this DACL would carry, or $null.
        .DESCRIPTION
            This is the value a child's own acl_hash is compared against - never the
            parent's own digest. A parent's explicit ACE carrying CONTAINER_INHERIT (0x02)
            arrives at the child as the same ACE with INHERITED added (0x12), so the two
            DACLs differ byte for byte precisely *because* inheritance worked. Comparing a
            child to its parent directly would report every directory in the estate as a
            boundary.

            $null means the parent cannot project: a NULL DACL produces no entries, and what
            a child of it ends up holding comes from the creating process's default DACL,
            which is not a fact about the parent.

            The projected document is always dacl_present=true and dacl_protected=false.
            Inheritance produces a present DACL even when it produces no entries, and a
            child that is itself protected is a boundary on that basis alone - so a
            projection claiming protection could only ever make a boundary invisible.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][bool] $DaclPresent,
        [AllowNull()][object[]] $Ace,
        [bool] $ForContainer = $true
    )

    if (-not $DaclPresent) { return $null }
    $projected = Get-AdgInheritedAceProjection -Ace $Ace -ForContainer $ForContainer
    return Get-AdgAclHash -DaclPresent $true -DaclProtected $false -Ace $projected
}

function Resolve-AdgAclBoundary {
    <#
        .SYNOPSIS
            Why this resource is a boundary, or $null when it carries what it inherited.
        .DESCRIPTION
            Mirrors app/domain/inheritance.py::boundary_reason_for, including the order of
            the tests, which is the order of certainty: a protected DACL is a boundary
            whatever a projection says, and a resource whose parent nobody read is unknown
            rather than unchanged.

            $null is the only value that means "not a boundary". Every unknowable case -
            a scan root, an unreadable parent, a parent with a NULL DACL - returns a reason
            and therefore reports a boundary. The asymmetry is deliberate: a boundary that
            is not really there costs one extra stored ACL, while a boundary reported false
            tells the next scan it may stop looking and silently drops every permission
            change beneath it.

        .PARAMETER AclHash
            This resource's own digest, or $null when its DACL was only partly read and no
            digest could honestly be taken over it.

        .PARAMETER ParentDaclPresent
            The parent's dacl_present, or $null when the parent was not read at all.

        .PARAMETER ParentProjection
            Get-AdgProjectedChildAclHash for the parent, or $null when it projects nothing.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][bool] $DaclPresent,
        [bool] $DaclProtected = $false,
        [switch] $IsShareRoot,
        [switch] $IsScanRoot,
        [AllowNull()][string] $AclHash,
        [AllowNull()][System.Nullable[bool]] $ParentDaclPresent,
        [AllowNull()][string] $ParentProjection
    )

    if ($DaclProtected) { return 'protected_dacl' }
    if (-not $DaclPresent) { return 'null_dacl' }
    if ($IsShareRoot) { return 'share_root' }
    if ($IsScanRoot) { return 'scan_root' }
    if ($null -eq $ParentDaclPresent) { return 'parent_unreadable' }
    if (-not $ParentDaclPresent) { return 'parent_null_dacl' }
    # Either side missing a digest means the comparison was never made. An unread ACL is
    # not an unchanged one, and calling it unchanged is what would let a later scan stop at
    # a directory whose permissions nobody has established.
    if ([string]::IsNullOrWhiteSpace($ParentProjection) -or [string]::IsNullOrWhiteSpace($AclHash)) {
        return 'parent_unreadable'
    }
    if ($AclHash -cne $ParentProjection) { return 'acl_differs_from_parent' }
    return $null
}

# --- Raw descriptor values to contract enumerations ---------------------------------------

function ConvertTo-AdgAceType {
    <#
        .SYNOPSIS
            A raw ACE header type to 'allow' or 'deny', or $null for anything else.
        .DESCRIPTION
            $null covers audit entries (SystemAudit) and the callback and object ACE types.
            A SACL entry governs logging, not access; reporting one as a DACL entry would
            fabricate access that does not exist. The caller records an error rather than
            dropping it silently, because an entry it could not classify is an entry it
            could not audit.
    #>
    [OutputType([string])]
    param([AllowNull()] $AceType)

    if ($null -eq $AceType) { return $null }

    switch -Regex ([string] $AceType) {
        '^(0|AccessAllowed|Allow)$' { return 'allow' }
        '^(1|AccessDenied|Deny)$' { return 'deny' }
        default { return $null }
    }
}

function ConvertTo-AdgAccessMask {
    <#
        .SYNOPSIS
            A raw access mask as an unsigned 32-bit value, whatever signed form it arrived in.
        .DESCRIPTION
            An access mask is unsigned 32 bits. .NET surfaces it as a signed Int32, so every
            mask with the top bit set - which is every mask carrying a generic right -
            arrives negative: GENERIC_READ alone is -2147483648, and the ACE that provoked
            this function, GENERIC_ALL|GENERIC_EXECUTE with standard bits, is -536805376.

            A plain `[uint32] $value` cast **throws** on those, because PowerShell's
            conversion is range-checked rather than a reinterpretation. That is the whole
            defect: a collector that crashes on any directory whose DACL holds a generic
            right crashes on a large share of a real estate, while passing every test whose
            fixture masks happen to be positive.

            Masking against 0xFFFFFFFF as a long is the reinterpretation - two's complement
            in, the same 32 bits out - and it leaves an already-unsigned value untouched.
    #>
    [OutputType([long])]
    param([Parameter(Mandatory)][AllowNull()] $Value)

    if ($null -eq $Value) { return [long] 0 }
    return ([long] $Value) -band 0xFFFFFFFFL
}

function Test-AdgSidString {
    <#
        .SYNOPSIS
            Does this look like a string-form SID the contract will accept?
        .DESCRIPTION
            Mirrors the `sid` pattern in common.schema.json. A trustee whose SID came back
            empty or malformed is not reported with a placeholder; the entry is recorded as
            an error instead.
    #>
    [OutputType([bool])]
    param([AllowNull()][AllowEmptyString()] $Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    return [string] $Value -cmatch '^S-1-(0[xX][0-9a-fA-F]{1,12}|[0-9]{1,20})(-[0-9]{1,10}){0,15}$'
}

function New-AdgObservation {
    <#
        .SYNOPSIS
            Attach the five fields every observation carries to a kind-specific body.
        .DESCRIPTION
            Keys whose value is $null are omitted rather than sent as null: the contract
            sets unevaluatedProperties to false and several fields reject null explicitly.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $Kind,
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $SourceKey,
        [Parameter(Mandatory)][hashtable] $Body,
        [string] $ObservedAt
    )

    if ([string]::IsNullOrWhiteSpace($ObservedAt)) { $ObservedAt = Get-AdgTimestamp }

    $observation = [ordered]@{
        schema_version = $script:AdgSchemaVersion
        kind           = $Kind
        run_id         = $RunId
        observed_at    = $ObservedAt
        source_key     = $SourceKey
    }
    foreach ($key in ($Body.Keys | Sort-Object)) {
        if ($null -ne $Body[$key]) { $observation[$key] = $Body[$key] }
    }
    return $observation
}

function New-AdgCollectorError {
    <#
        .SYNOPSIS
            A collectorError entry for the completion envelope.
        .DESCRIPTION
            Every unreadable object becomes one of these. An error makes the run partial,
            and a partial run reconciles nothing - which is what stops a failed read from
            being mistaken for a directory that no longer exists.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $Code,
        [Parameter(Mandatory)][string] $Message,
        [string] $Target,
        [string] $OccurredAt
    )

    if ([string]::IsNullOrWhiteSpace($OccurredAt)) { $OccurredAt = Get-AdgTimestamp }
    if ($Message.Length -gt 2000) { $Message = $Message.Substring(0, 2000) }

    # Not $error: that is an automatic variable holding the session's error stack.
    $item = [ordered]@{
        code    = $Code
        message = $Message
    }
    if (-not [string]::IsNullOrWhiteSpace($Target)) { $item['target'] = $Target }
    $item['occurred_at'] = $OccurredAt
    return $item
}

# --- Observation builders -----------------------------------------------------------------

function ConvertTo-AdgUnresolvedPrincipalObservation {
    <#
        .SYNOPSIS
            A principal observation for a trustee SID that did not resolve to a name.
        .DESCRIPTION
            An orphaned SID on a folder ACL is a finding, not a defect to be tidied away.
            The contract forbids display_name on an unresolved principal, so no name is
            invented; last_known_name stays unset because this collector has no history.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $Sid,
        [ValidateSet('deleted', 'untrusted_domain', 'lookup_failed', 'unknown')]
        [string] $Reason = 'lookup_failed',
        [string] $ObservedAt
    )

    return New-AdgObservation -Kind 'principal' -RunId $RunId -ObservedAt $ObservedAt `
        -SourceKey (Get-AdgPrincipalKey $Sid 'unresolved' $null) -Body @{
        sid               = $Sid
        principal_kind    = 'unresolved'
        unresolved_reason = $Reason
    }
}

function ConvertTo-AdgNtfsAceObservation {
    <#
        .SYNOPSIS
            Contract ACEs from the entries of one raw DACL.
        .DESCRIPTION
            Returns a hashtable with Observations, Principals, Errors, and Complete.

            Nothing here interprets a mask. Generic bits are not expanded, Deny precedence
            is not applied, INHERIT_ONLY entries are not filtered out, and unrecognized mask
            bits are preserved: an ACE is evidence, and a simplified ACE is no longer
            evidence. Windows resolves generic rights through the object's generic mapping
            at access time, and doing that mapping here would bake one interpretation into
            a stored fact.

            order_index is the entry's position in the DACL as read, counted across every
            entry including ones this function could not report. That is the real evaluation
            position, and it is what the ACL hash reduces to a rank.

            Complete is $false when any entry could not be reported. The caller then omits
            the resource's acl_hash entirely: a digest over part of a DACL looks exactly
            like a digest of the whole one, and comparing it to a parent's would silently
            answer the boundary question wrong.

        .PARAMETER Ace
            The DACL entries, in order, each with TrusteeSid, AceType, AccessMask, AceFlags.
            An empty array means a present-but-empty DACL, which grants nobody access. A
            NULL DACL - which grants everybody full access - is a different fact that the
            caller reports through dacl_present and must never pass here as an empty list.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $Path,
        [AllowNull()][object[]] $Ace,
        [string] $ObservedAt
    )

    $path = ConvertTo-AdgUncPath $Path
    $observations = [System.Collections.Generic.List[object]]::new()
    $principals = [System.Collections.Generic.List[object]]::new()
    $errors = [System.Collections.Generic.List[object]]::new()
    $facts = [System.Collections.Generic.List[object]]::new()
    $seenSids = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

    $index = 0
    foreach ($entry in @($Ace ?? @())) {
        $aceType = ConvertTo-AdgAceType (Get-AdgProperty $entry 'AceType')
        if ($null -eq $aceType) {
            # Not dropped in silence: an entry whose type the contract cannot express is an
            # entry nobody audits, and the run must not claim to have read the whole DACL.
            $errors.Add((New-AdgCollectorError -Code 'unmappable_ace_type' -Target $path -OccurredAt $ObservedAt `
                        -Message "The DACL entry at position $index on $path has type '$(Get-AdgProperty $entry 'AceType')', which contract v1 cannot express. It was not reported, so this directory's ACL is only partly recorded."))
            $index++
            continue
        }

        $sid = [string] (Get-AdgProperty $entry 'TrusteeSid')
        if (-not (Test-AdgSidString $sid)) {
            $errors.Add((New-AdgCollectorError -Code 'lookup_failed' -Target $path -OccurredAt $ObservedAt `
                        -Message "The DACL entry at position $index on $path has no usable trustee SID; it was recorded as an error rather than reported without an identity."))
            $index++
            continue
        }

        $mask = ConvertTo-AdgAccessMask (Get-AdgProperty $entry 'AccessMask')
        $flags = [int] (Get-AdgProperty $entry 'AceFlags')
        # 0x10 is INHERITED_ACE. source and the flag are two spellings of one fact, and the
        # contract rejects a payload where they disagree, so it is derived rather than taken
        # from the caller.
        $source = if (($flags -band 0x10) -ne 0) { 'inherited' } else { 'explicit' }

        $observations.Add((New-AdgObservation -Kind 'ntfs_ace' -RunId $RunId -ObservedAt $ObservedAt `
                    -SourceKey (Get-AdgNtfsAceKey -Path $path -TrusteeSid $sid -AceType $aceType -AccessMask $mask -AceFlags $flags) -Body @{
                    path        = $path
                    trustee_sid = $sid
                    ace_type    = $aceType
                    access_mask = $mask     # raw, generic bits included; never expanded
                    ace_flags   = $flags    # the raw header byte, unknown bits included
                    source      = $source
                    order_index = $index
                    # inherited_from is deliberately absent. Naming the ancestor an entry
                    # came from needs the Win32 GetInheritanceSource, which this collector
                    # does not call; the INHERITED bit already says the entry came from
                    # above, and Phase 3B - which reads the ancestors - can say which one.
                }))

        $facts.Add([pscustomobject]@{
                TrusteeSid = $sid
                AceType    = $aceType
                AccessMask = $mask
                AceFlags   = $flags
                OrderIndex = $index
            })

        $hasName = -not [string]::IsNullOrWhiteSpace((Get-AdgProperty $entry 'TrusteeName'))
        if (-not $hasName -and $seenSids.Add($sid)) {
            $principals.Add((ConvertTo-AdgUnresolvedPrincipalObservation -RunId $RunId -Sid $sid `
                        -Reason 'lookup_failed' -ObservedAt $ObservedAt))
        }

        $index++
    }

    return @{
        Observations = $observations.ToArray()
        Principals   = $principals.ToArray()
        Errors       = $errors.ToArray()
        Facts        = $facts.ToArray()
        Complete     = ($errors.Count -eq 0)
    }
}

function ConvertTo-AdgNtfsResourceObservation {
    <#
        .SYNOPSIS
            A resource observation for one directory and its descriptor-level facts.
        .DESCRIPTION
            The ACE list alone is ambiguous, which is the whole reason this observation
            exists: a NULL DACL (DaclPresent false) grants everyone full access, while a
            present-but-empty DACL grants nobody access, and both arrive as zero entries.

            ace_count is the number of ntfs_ace observations actually reported, which the
            contract requires it to equal. When an entry could not be reported the caller
            passes -Incomplete, and AclHash is then omitted rather than computed over the
            part that was readable.

        .PARAMETER BoundaryReason
            Why this resource is a place where permissions change, from
            Resolve-AdgAclBoundary. $null - the default - is the only value that means it is
            carrying exactly what its parent hands down, and is_acl_boundary follows from
            it rather than being passed separately: a boundary and the evidence for it
            cannot then disagree, which is the failure contract 1.3 exists to prevent.

        .PARAMETER ParentAclHash
            The parent's own acl_hash as this run read it. Not the value the verdict was
            compared against - that is the parent's projection onto a child - but the record
            of which reading of the parent was judged, without which a later disagreement
            cannot be told from the parent simply having changed in between.

        .PARAMETER ResourceKind
            'directory' or 'file'. A file inherits through the object projection rather than
            the container one, and is never traversed.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $Path,
        [Parameter(Mandatory)][bool] $DaclPresent,
        [bool] $DaclProtected = $false,
        [string] $OwnerSid,
        [string] $GroupSid,
        [string] $LocalPath,
        [int] $AceCount = 0,
        [ValidateSet('directory', 'file')][string] $ResourceKind = 'directory',
        [AllowNull()][AllowEmptyString()][string] $BoundaryReason,
        [string] $AclHash,
        [string] $ParentAclHash,
        [string] $ObservedAt
    )

    $path = ConvertTo-AdgUncPath $Path
    $parts = $path.Substring(2).Split('\')

    if (-not $DaclPresent -and $AceCount -ne 0) {
        throw "A NULL DACL on $path carries no ACEs, but ace_count=$AceCount was supplied. Report ace_count=0, and do not confuse it with a present but empty DACL, which grants nobody access."
    }
    if ($ResourceKind -eq 'file' -and $parts.Count -eq 2) {
        throw "$path is a share root, which is always a directory; it cannot be reported as a file."
    }
    if (-not [string]::IsNullOrWhiteSpace($BoundaryReason) -and
        $BoundaryReason -notin $script:AdgBoundaryReasons) {
        throw "'$BoundaryReason' is not a boundary reason the contract can express. Derive it with Resolve-AdgAclBoundary, which returns one of: $($script:AdgBoundaryReasons -join ', '), or nothing at all when the resource carries exactly what it inherited."
    }

    # SE_DACL_PROTECTED means the object refuses inherited entries, which is by definition a
    # place where permissions change. The contract rejects the pair being inconsistent, and
    # Resolve-AdgAclBoundary tests protection first for exactly that reason - so a protected
    # resource always arrives here with a reason, and this is only a guard against a caller
    # that assembled the pair by hand.
    $inheritanceEnabled = -not $DaclProtected
    if ($DaclProtected -and [string]::IsNullOrWhiteSpace($BoundaryReason)) {
        throw "$path blocks inheritance, which is by definition an ACL boundary, but no boundary_reason was supplied. Derive it with Resolve-AdgAclBoundary rather than assembling the pair by hand."
    }
    $isBoundary = -not [string]::IsNullOrWhiteSpace($BoundaryReason)

    if ($OwnerSid -and -not (Test-AdgSidString $OwnerSid)) { $OwnerSid = $null }
    if ($GroupSid -and -not (Test-AdgSidString $GroupSid)) { $GroupSid = $null }

    return New-AdgObservation -Kind 'ntfs_resource' -RunId $RunId -ObservedAt $ObservedAt `
        -SourceKey (Get-AdgNtfsResourceKey $path) -Body @{
        path                  = $path
        server_name           = $parts[0]
        share_name            = $parts[1]
        local_path            = if ([string]::IsNullOrWhiteSpace($LocalPath)) { $null } else { $LocalPath }
        owner_sid             = if ([string]::IsNullOrWhiteSpace($OwnerSid)) { $null } else { $OwnerSid }
        group_sid             = if ([string]::IsNullOrWhiteSpace($GroupSid)) { $null } else { $GroupSid }
        dacl_present          = $DaclPresent
        dacl_protected        = $DaclProtected
        inheritance_enabled   = $inheritanceEnabled
        is_acl_boundary       = $isBoundary
        ace_count             = $AceCount
        # Derived from the path, never counted by the walk: a resume from a checkpoint and
        # a first full walk must report the same number for the same directory, and only
        # the path is common to both.
        depth_from_share_root = Get-AdgDepthFromShareRoot $path
        resource_kind         = $ResourceKind
        boundary_reason       = if ($isBoundary) { $BoundaryReason } else { $null }
        acl_hash              = if ([string]::IsNullOrWhiteSpace($AclHash)) { $null } else { $AclHash }
        parent_acl_hash       = if ([string]::IsNullOrWhiteSpace($ParentAclHash)) { $null } else { $ParentAclHash }
    }
}
