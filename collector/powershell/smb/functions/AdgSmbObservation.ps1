<#
    Contract layer: turn raw SMB readings into contract v1 observations.

    Everything in this file is pure. No function here touches the network, the file
    system, or the clock except Get-AdgTimestamp, and every conversion is a total
    function of its arguments. That is deliberate: normalization is where a collector
    most easily starts inventing facts, so it is the part that must be exhaustively
    testable without a domain.

    The source-key derivations must agree byte for byte with
    backend/app/contracts/v1/keys.py. The server recomputes every key and rejects a
    mismatch, so a divergence here is not a cosmetic difference - it is a rejected run.
#>

$script:AdgSchemaVersion = '1.1'

function Get-AdgProperty {
    <#
        .SYNOPSIS
            Read a property that may not be there, without failing.
        .DESCRIPTION
            Strict mode makes a reference to a missing property an error, and the shapes
            this collector normalizes are not guaranteed: MSFT_SmbShare gained properties
            across Windows releases, and a Win32_SecurityDescriptor ACE can arrive with no
            Trustee at all. A property the source did not provide is a fact the source did
            not state, which is $null - not a reason to abandon the share.
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

function Get-AdgServerKey {
    [OutputType([string])]
    param([Parameter(Mandatory)][string] $Name)
    return "server|$($Name.ToLowerInvariant())"
}

function Get-AdgShareKey {
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $ServerName,
        [Parameter(Mandatory)][string] $ShareName
    )
    return "share|$($ServerName.ToLowerInvariant())|$($ShareName.ToLowerInvariant())"
}

function Get-AdgSmbAceKey {
    <#
        .SYNOPSIS
            smb_ace|<server>|<share>|<trustee>|<type>|<permission or 0x-mask>
        .DESCRIPTION
            The right form is part of the key on purpose. A share ACL read as levels and
            the same ACL read as masks are two different readings of one entry; keeping
            them distinct is honest, and the backend reconciles them later.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $ServerName,
        [Parameter(Mandatory)][string] $ShareName,
        [Parameter(Mandatory)][string] $TrusteeSid,
        [Parameter(Mandatory)][string] $AceType,
        [string] $Permission,
        [Nullable[long]] $AccessMask
    )

    if ([string]::IsNullOrEmpty($Permission) -and $null -eq $AccessMask) {
        throw 'An SMB ACE key needs exactly one right form: a permission level or an access mask.'
    }
    if (-not [string]::IsNullOrEmpty($Permission) -and $null -ne $AccessMask) {
        throw 'An SMB ACE carries a permission level or an access mask, never both.'
    }

    $right = if ($Permission) { $Permission } else { '0x{0:x8}' -f $AccessMask }
    return "smb_ace|$($ServerName.ToLowerInvariant())|$($ShareName.ToLowerInvariant())|$TrusteeSid|$AceType|$right"
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

# --- Derivations the contract deliberately does not carry on the wire --------------------

function Get-AdgShareUncPath {
    <#
        .SYNOPSIS
            \\server\share, the canonical identity of a share as a path.
        .DESCRIPTION
            The UNC path is not a field on smb_share and does not need to be: it is a
            total function of server_name and share_name, and storing it as well would
            create a second source of truth that can disagree with the first. Collectors
            and consumers derive it here so there is exactly one derivation.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $ServerName,
        [Parameter(Mandatory)][string] $ShareName
    )
    return "\\$ServerName\$ShareName"
}

function Test-AdgHiddenShareName {
    <#
        .SYNOPSIS
            Is this share hidden from browsing?
        .DESCRIPTION
            Hidden is a property of the name - a trailing '$' suppresses the share from
            enumeration - so it is derived rather than transmitted. It is NOT the same as
            an administrative share: an administrator can create an ordinary hidden share
            such as Data$, which the SMB server does not mark Special. See
            Test-AdgShareIncluded, which distinguishes the two.
    #>
    [OutputType([bool])]
    param([Parameter(Mandatory)][AllowEmptyString()][string] $ShareName)
    return $ShareName.EndsWith('$')
}

# --- Raw SMB values to contract enumerations --------------------------------------------

function ConvertTo-AdgShareType {
    <#
        .SYNOPSIS
            MSFT_SmbShare.ShareType to the contract's shareType enum.
        .DESCRIPTION
            Accepts the numeric form CIM returns and the string form the SMB cmdlets
            surface locally. Anything unrecognized becomes 'unknown' rather than a guess:
            'unknown' is a statement that the source did not say, and the backend must be
            able to tell that apart from 'disk'.
    #>
    [OutputType([string])]
    param([AllowNull()] $ShareType)

    if ($null -eq $ShareType) { return 'unknown' }

    # Numeric as CIM returns it, named as the SmbShare cmdlets surface it. A
    # SimpleReferral (a DFS referral share) has no value in the contract enumeration, so
    # it becomes 'unknown' - an honest "the contract cannot name this", not 'disk'.
    $text = [string] $ShareType
    switch -Regex ($text) {
        '^(0|FileSystemDirectory)$' { return 'disk' }
        '^(1|PrintQueue)$' { return 'print' }
        '^(2|CommunicationDevice|Device)$' { return 'device' }
        '^(3|InterprocessCommunication|IPC)$' { return 'ipc' }
        default { return 'unknown' }
    }
}

function ConvertTo-AdgSharePermission {
    <#
        .SYNOPSIS
            Get-SmbShareAccess AccessRight to the contract's sharePermission enum.
        .DESCRIPTION
            Returns $null for 'Custom', which the contract cannot express: Custom means
            the share ACL holds a mask that is not one of the three levels, and the level
            API does not report what that mask is. The caller records a collector error
            and reads the descriptor instead. Inventing 'change' here would understate or
            overstate access, and either is a wrong audit answer.
    #>
    [OutputType([string])]
    param([AllowNull()] $AccessRight)

    if ($null -eq $AccessRight) { return $null }

    switch -Regex ([string] $AccessRight) {
        '^(0|Full)$' { return 'full' }
        '^(1|Change)$' { return 'change' }
        '^(2|Read)$' { return 'read' }
        default { return $null }
    }
}

function ConvertTo-AdgAceType {
    <#
        .SYNOPSIS
            An ACE header type to 'allow' or 'deny', or $null for anything else.
        .DESCRIPTION
            $null covers audit entries (ACE type 2 and 3). A SACL entry governs logging,
            not access; reporting one as a DACL entry would fabricate access that does
            not exist, so the caller drops it.
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
            Mirrors the `sid` pattern in common.schema.json. A trustee whose SIDString
            came back empty or malformed is not reported with a placeholder; the ACE is
            recorded as an error instead.
    #>
    [OutputType([bool])]
    param([AllowNull()][AllowEmptyString()] $Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    return [string] $Value -cmatch '^S-1-(0[xX][0-9a-fA-F]{1,12}|[0-9]{1,20})(-[0-9]{1,10}){0,15}$'
}

# --- Observation builders ---------------------------------------------------------------

function ConvertTo-AdgServerObservation {
    <#
        .SYNOPSIS
            A server observation for a host the collector actually reached.
        .DESCRIPTION
            Only emitted for a host that answered. A configured host that could not be
            contacted produces a collector error, never a server observation: an
            observation asserts that the object was seen, and an unreachable host was not.

            computer_sid and domain_sid are left unset. They are available from Active
            Directory (Phase 1), and guessing them from a remote host's name would key a
            machine by something other than its SID.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $Name,
        [string] $DnsHostName,
        [string] $NetbiosName,
        [Nullable[bool]] $IsDomainMember,
        [string] $OperatingSystem,
        [string] $ObservedAt
    )

    return New-AdgObservation -Kind 'server' -RunId $RunId -ObservedAt $ObservedAt `
        -SourceKey (Get-AdgServerKey $Name) -Body @{
        name             = $Name
        dns_host_name    = if ($DnsHostName) { $DnsHostName } else { $null }
        netbios_name     = if ($NetbiosName) { $NetbiosName } else { $null }
        is_domain_member = $IsDomainMember
        operating_system = if ($OperatingSystem) { $OperatingSystem } else { $null }
    }
}

function ConvertTo-AdgShareObservation {
    <#
        .SYNOPSIS
            A share observation from one MSFT_SmbShare instance.
        .DESCRIPTION
            is_special carries the SMB server's own Special flag, which marks an
            administrative or system share (C$, ADMIN$, IPC$). It is a source fact and
            cannot be derived: hidden-ness follows from the name, but an ordinary hidden
            share such as Data$ is not Special. Hidden-ness itself is not transmitted -
            Test-AdgHiddenShareName derives it from share_name.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $ServerName,
        [Parameter(Mandatory)] $Share,
        [string] $ObservedAt
    )

    $shareName = [string] (Get-AdgProperty $Share 'Name')
    if ([string]::IsNullOrWhiteSpace($shareName)) {
        throw 'A share observation needs a share name; the source object had none.'
    }

    $rawPath = Get-AdgProperty $Share 'Path'
    $localPath = if ([string]::IsNullOrWhiteSpace($rawPath)) { $null } else { [string] $rawPath }

    # An IPC or print share has no directory behind it; Path comes back empty, and the
    # contract's localPath pattern requires a drive letter. Omit rather than coerce.
    if ($localPath -and $localPath -notmatch '^[A-Za-z]:\\') { $localPath = $null }

    $rawLimit = Get-AdgProperty $Share 'ConcurrentUserLimit'
    $limit = $null
    if ($null -ne $rawLimit -and [int] $rawLimit -ge 0) { $limit = [int] $rawLimit }

    $rawSpecial = Get-AdgProperty $Share 'Special'
    $isSpecial = if ($null -eq $rawSpecial) { $null } else { [bool] $rawSpecial }

    $rawDescription = Get-AdgProperty $Share 'Description'
    $rawCaching = Get-AdgProperty $Share 'CachingMode'

    return New-AdgObservation -Kind 'smb_share' -RunId $RunId -ObservedAt $ObservedAt `
        -SourceKey (Get-AdgShareKey $ServerName $shareName) -Body @{
        server_name           = $ServerName
        share_name            = $shareName
        local_path            = $localPath
        share_type            = ConvertTo-AdgShareType (Get-AdgProperty $Share 'ShareType')
        description           = if ([string]::IsNullOrWhiteSpace($rawDescription)) { $null } else { [string] $rawDescription }
        concurrent_user_limit = $limit
        caching_mode          = if ($null -eq $rawCaching) { $null } else { [string] $rawCaching }
        is_special            = $isSpecial
    }
}

function ConvertTo-AdgUnresolvedPrincipalObservation {
    <#
        .SYNOPSIS
            A principal observation for a trustee SID that did not resolve to a name.
        .DESCRIPTION
            An orphaned SID on a share ACL is a finding, not a defect to be tidied away.
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

function ConvertTo-AdgShareAceObservation {
    <#
        .SYNOPSIS
            Share ACEs from a raw security descriptor: SIDs and access masks.
        .DESCRIPTION
            This is the preferred reading. A descriptor stores SIDs, so a trustee whose
            name cannot be resolved still yields a usable ACE - which the level-based API
            cannot guarantee, because it reports names.

            Returns a hashtable with Observations, Principals, and Errors. Audit entries
            are dropped (they govern logging, not access). An ACE whose trustee SID is
            missing or malformed becomes an error rather than a guess.

        .PARAMETER Dacl
            The DACL array from Win32_SecurityDescriptor. An empty array means nobody has
            share access; a NULL DACL - which the caller detects from ControlFlags, not
            from here - means everybody does, and must never reach this function as an
            empty list.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $ServerName,
        [Parameter(Mandatory)][string] $ShareName,
        [AllowNull()][object[]] $Dacl,
        [string] $ObservedAt
    )

    $observations = [System.Collections.Generic.List[object]]::new()
    $principals = [System.Collections.Generic.List[object]]::new()
    $errors = [System.Collections.Generic.List[object]]::new()
    $seenSids = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $unc = Get-AdgShareUncPath -ServerName $ServerName -ShareName $ShareName

    $index = 0
    foreach ($ace in ($Dacl ?? @())) {
        $aceType = ConvertTo-AdgAceType (Get-AdgProperty $ace 'AceType')
        if ($null -eq $aceType) {
            # Audit and unknown ACE types are not access grants. Skipping them silently is
            # correct: they were never part of the DACL's access story.
            continue
        }

        $trustee = Get-AdgProperty $ace 'Trustee'
        $sid = [string] (Get-AdgProperty $trustee 'SIDString')

        if (-not (Test-AdgSidString $sid)) {
            $errors.Add((New-AdgCollectorError -Code 'lookup_failed' -Target $unc -OccurredAt $ObservedAt `
                        -Message "A share ACE at index $index has no usable trustee SID; the entry was recorded as an error rather than reported without an identity."))
            $index++
            continue
        }

        $mask = [long] ([uint32] (Get-AdgProperty $ace 'AccessMask'))

        $observations.Add((New-AdgObservation -Kind 'smb_ace' -RunId $RunId -ObservedAt $ObservedAt `
                    -SourceKey (Get-AdgSmbAceKey -ServerName $ServerName -ShareName $ShareName -TrusteeSid $sid `
                        -AceType $aceType -AccessMask $mask) -Body @{
                    server_name = $ServerName
                    share_name  = $ShareName
                    trustee_sid = $sid
                    ace_type    = $aceType
                    access_mask = $mask   # raw, generic bits included; never expanded
                    order_index = $index
                }))

        # A trustee the descriptor could not name is an unresolved principal, which is a
        # finding in its own right. Names that did resolve belong to the AD and local-group
        # collectors, which know what kind of principal each SID is.
        $hasName = -not [string]::IsNullOrWhiteSpace((Get-AdgProperty $trustee 'Name'))
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
    }
}

function ConvertTo-AdgShareAccessObservation {
    <#
        .SYNOPSIS
            Share ACEs from Get-SmbShareAccess: levels, and names that must be translated.
        .DESCRIPTION
            The fallback reading, used when the security descriptor is unavailable. It is
            weaker in two ways the caller must understand:

              * it reports an account name, so an ACE whose name will not translate to a
                SID cannot be reported at all - the contract requires trustee_sid. Such an
                ACE becomes an error, never a silent omission;
              * it reports 'Custom' for any mask that is not one of the three levels, and
                does not say what that mask is. That, too, becomes an error.

            Both cases make the run partial, which is the honest outcome: part of the
            share ACL was not read.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $ServerName,
        [Parameter(Mandatory)][string] $ShareName,
        [AllowNull()][object[]] $Access,
        [string] $ObservedAt
    )

    $observations = [System.Collections.Generic.List[object]]::new()
    $principals = [System.Collections.Generic.List[object]]::new()
    $errors = [System.Collections.Generic.List[object]]::new()
    $seenSids = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $unc = Get-AdgShareUncPath -ServerName $ServerName -ShareName $ShareName

    $index = 0
    foreach ($entry in ($Access ?? @())) {
        $aceType = ConvertTo-AdgAceType (Get-AdgProperty $entry 'AccessControlType')
        if ($null -eq $aceType) { $index++; continue }

        $accountName = [string] (Get-AdgProperty $entry 'AccountName')
        $accessRight = Get-AdgProperty $entry 'AccessRight'
        $permission = ConvertTo-AdgSharePermission $accessRight

        if ($null -eq $permission) {
            $errors.Add((New-AdgCollectorError -Code 'unmappable_right' -Target $unc -OccurredAt $ObservedAt `
                        -Message "Get-SmbShareAccess reported '$accessRight' for '$accountName', which is not one of read/change/full and carries no access mask. Read the share security descriptor to record this entry."))
            $index++
            continue
        }

        $sid = Resolve-AdgTrusteeSid -Trustee $accountName
        if (-not (Test-AdgSidString $sid)) {
            $errors.Add((New-AdgCollectorError -Code 'lookup_failed' -Target $unc -OccurredAt $ObservedAt `
                        -Message "'$accountName' did not translate to a SID, and the contract identifies a trustee only by SID. Read the share security descriptor, which stores SIDs directly."))
            $index++
            continue
        }

        $observations.Add((New-AdgObservation -Kind 'smb_ace' -RunId $RunId -ObservedAt $ObservedAt `
                    -SourceKey (Get-AdgSmbAceKey -ServerName $ServerName -ShareName $ShareName -TrusteeSid $sid `
                        -AceType $aceType -Permission $permission) -Body @{
                    server_name = $ServerName
                    share_name  = $ShareName
                    trustee_sid = $sid
                    ace_type    = $aceType
                    permission  = $permission   # exactly one right form: level OR mask
                    order_index = $index
                }))

        # The account name here IS the SID string when Windows could not resolve it.
        if ($accountName -eq $sid -and $seenSids.Add($sid)) {
            $principals.Add((ConvertTo-AdgUnresolvedPrincipalObservation -RunId $RunId -Sid $sid `
                        -Reason 'lookup_failed' -ObservedAt $ObservedAt))
        }

        $index++
    }

    return @{
        Observations = $observations.ToArray()
        Principals   = $principals.ToArray()
        Errors       = $errors.ToArray()
    }
}

function New-AdgCollectorError {
    <#
        .SYNOPSIS
            A collectorError entry for the completion envelope.
        .DESCRIPTION
            Every unreadable object becomes one of these. An error makes the run partial,
            and a partial run reconciles nothing - which is exactly what stops a failed
            read from being mistaken for an object that no longer exists.
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
