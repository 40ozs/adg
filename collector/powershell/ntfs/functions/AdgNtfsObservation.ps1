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

# Contract 1.2: ntfs_resource gained the optional acl_hash.
$script:AdgSchemaVersion = '1.2'

# First line of the normalized ACL document. Mirrors ACL_NORMAL_FORM_VERSION. A change of
# format changes this token, so two digests from different formats can never be compared as
# though they agreed.
$script:AdgAclNormalFormVersion = 'adg-acl/1'

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

        $mask = [long] ([uint32] (Get-AdgProperty $entry 'AccessMask'))
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

        .PARAMETER IsShareRoot
            A share root is always reported as an ACL boundary. Its parent lies outside the
            share - often outside anything ADG audits - so there is nothing to compare it
            against, and "not a boundary" would tell a later tree walk it could skip the one
            directory every path through the share must pass.
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
        [Nullable[int]] $DepthFromShareRoot,
        [switch] $IsShareRoot,
        [string] $AclHash,
        [string] $ObservedAt
    )

    $path = ConvertTo-AdgUncPath $Path
    $parts = $path.Substring(2).Split('\')

    if (-not $DaclPresent -and $AceCount -ne 0) {
        throw "A NULL DACL on $path carries no ACEs, but ace_count=$AceCount was supplied. Report ace_count=0, and do not confuse it with a present but empty DACL, which grants nobody access."
    }

    # SE_DACL_PROTECTED means the object refuses inherited entries, which is by definition a
    # place where permissions change. The contract rejects the pair being inconsistent.
    $inheritanceEnabled = -not $DaclProtected
    $isBoundary = $DaclProtected -or [bool] $IsShareRoot

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
        depth_from_share_root = if ($null -eq $DepthFromShareRoot) { $null } else { [int] $DepthFromShareRoot }
        acl_hash              = if ([string]::IsNullOrWhiteSpace($AclHash)) { $null } else { $AclHash }
    }
}
