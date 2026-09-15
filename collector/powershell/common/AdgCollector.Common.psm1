#Requires -Version 7.0
<#
.SYNOPSIS
    Contract v1 primitives shared by every ADG collector: payload construction, source-key
    derivation, batching, retrying transport, and an offline sink.

.DESCRIPTION
    This module is the PowerShell mirror of `backend/app/contracts/v1/`. It knows how to
    build the payloads in `docs/contracts/v1/` and how to send them per
    `docs/contracts/collector-protocol.md`. It knows nothing about Active Directory, SMB,
    or NTFS: each collector supplies the facts, this module shapes and ships them.

    Two rules from the protocol are enforced here rather than left to the caller, because
    a collector that gets them wrong produces an audit answer that is quietly too small:

      * A batch never exceeds 1000 observations and is never truncated to fit.
      * A run that reported any error cannot be 'succeeded' and cannot reconcile a scope.

    Validation is deliberately strict and local. The API would reject a malformed payload
    with a 422 anyway; failing here names the offending field while the collector still
    has the context to say which object it came from.

.NOTES
    ADG is read-only (ADR-0004). Nothing in this module writes to a target system.
#>

Set-StrictMode -Version Latest

$script:SchemaVersion = '1.0'

# The minor that introduced checkpoints, collection mode, and affirmations. An envelope
# declares it only when it actually carries one of those fields, because that is what
# "additive" means in both directions: a later minor's rule may not be applied to an
# earlier payload, and an earlier payload may not use a later minor's field. A collector
# that never sends any of them keeps sending 1.0 and stays correct.
$script:IncrementalSchemaVersion = '1.4'
$script:MaxBatchObservations = 1000

# Affirmations get their own, higher ceiling (contract 1.4). An affirmation is a key and a
# digest, not an object: a thousand of them cost less to send and to apply than a hundred
# resources with their entries. Capping them at the observation ceiling would force a
# collector re-reading a large, quiet tree to open batches for no reason other than an
# accounting rule, and every extra batch is another round trip that can fail.
$script:MaxBatchAffirmations = 5000

# Enum values mirrored from docs/contracts/v1/common.schema.json. Kept as script-scope
# lists so the validation messages can name the accepted values instead of just failing.
$script:PrincipalKinds = @(
    'user', 'domain_group', 'local_group', 'computer', 'managed_service_account',
    'well_known', 'foreign_security_principal', 'unresolved'
)
$script:MembershipEdgeKinds = @(
    'directory_group_member', 'primary_group', 'local_group_member', 'well_known_implicit'
)
$script:GroupScopes = @('domain_local', 'global', 'universal', 'builtin_local', 'unknown')
$script:GroupTypes = @('security', 'distribution', 'unknown')
$script:UnresolvedReasons = @('deleted', 'untrusted_domain', 'lookup_failed', 'unknown')
$script:ScopeKinds = @('domain', 'server', 'share', 'directory_tree', 'local_groups_host')
$script:CollectorKinds = @('active_directory', 'smb', 'ntfs', 'local_groups')
$script:RunStatuses = @('succeeded', 'partial', 'failed', 'canceled')

$script:UuidPattern = '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
$script:SidPattern = '^S-1-(0[xX][0-9a-fA-F]{1,12}|[0-9]{1,20})(-[0-9]{1,10}){0,15}$'


function Get-AdgSchemaVersion {
    <#
        .SYNOPSIS
            The contract version every payload from this module carries.
    #>
    [OutputType([string])]
    param()
    return $script:SchemaVersion
}


function Get-AdgMaxBatchSize {
    <#
        .SYNOPSIS
            The hard ceiling on observations per batch. An oversized batch is rejected by
            the server, never truncated: silent truncation would look like coverage.
    #>
    [OutputType([int])]
    param()
    return $script:MaxBatchObservations
}


function Get-AdgTimestamp {
    <#
        .SYNOPSIS
            RFC 3339 timestamp in UTC.
        .DESCRIPTION
            The contract rejects a naive timestamp: observations from hosts in different
            time zones would otherwise be unorderable.
    #>
    [OutputType([string])]
    param([datetime] $Instant = [datetime]::UtcNow)

    $utc = if ($Instant.Kind -eq [System.DateTimeKind]::Unspecified) {
        [datetime]::SpecifyKind($Instant, [System.DateTimeKind]::Utc)
    }
    else {
        $Instant.ToUniversalTime()
    }
    return ([datetimeoffset]::new($utc, [timespan]::Zero)).ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
}


function New-AdgIdentifier {
    <#
        .SYNOPSIS
            A UUIDv4 for a run or a batch.
        .DESCRIPTION
            The collector generates these before sending and reuses them verbatim on
            retry. That is what makes start, batch, and completion idempotent: the server
            recognizes a repeat instead of guessing.
    #>
    [OutputType([string])]
    param()
    return [guid]::NewGuid().ToString()
}


function Assert-AdgUuid {
    param([string] $Value, [string] $Name)
    if ($Value -notmatch $script:UuidPattern) {
        throw "$Name must be a UUID; received '$Value'. The collector generates it and reuses it on retry, which is what makes the operation idempotent."
    }
}


function Assert-AdgSid {
    <#
        .SYNOPSIS
            Reject anything that is not a canonical string-form SID.
        .DESCRIPTION
            SID is identity in ADG (ADR-0001). A name that reached a SID field would key a
            principal by something that can be changed by anyone who can rename an object.
    #>
    param([string] $Value, [string] $Name)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "$Name must be a SID; received an empty value. SID is identity in ADG; a principal is never keyed by name."
    }
    if ($Value -notmatch $script:SidPattern) {
        throw "$Name must be a canonical string-form SID such as 'S-1-5-21-...-1104'; received '$Value'."
    }
}


function Assert-AdgHostName {
    param([string] $Value, [string] $Name)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "$Name must be a host name; received an empty value."
    }
    if ($Value -match '[\\/]') {
        throw "$Name must be a host name alone, not a UNC path; received '$Value'."
    }
}


# --- Source keys -------------------------------------------------------------------------
# The normative implementation is backend/app/contracts/v1/keys.py. These derivations are
# pinned to it by a test that runs this collector and parses its output through the backend
# models, which re-derive every key.

function Get-AdgPrincipalSourceKey {
    <#
        .SYNOPSIS
            principal|<sid>, or principal|<host>|<sid> for a local group.
        .DESCRIPTION
            Local groups are host-scoped because S-1-5-32-544 is identical on every Windows
            computer: BUILTIN\Administrators on FS01 and on FS02 are different groups.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $Sid,
        [Parameter(Mandatory)][string] $PrincipalKind,
        [string] $HostKey
    )

    Assert-AdgSid -Value $Sid -Name 'Sid'
    if ($PrincipalKind -eq 'local_group') {
        if ([string]::IsNullOrWhiteSpace($HostKey)) {
            throw "A local_group source key needs HostKey: $Sid is identical on every Windows computer, so host plus SID identifies the group."
        }
        return "principal|$($HostKey.ToLowerInvariant())|$Sid"
    }
    return "principal|$Sid"
}


function Get-AdgMembershipSourceKey {
    <#
        .SYNOPSIS
            edge|<group_key>-><member_key>|<edge_kind>.
        .DESCRIPTION
            Only a local-group edge is host-scoped, and within one the member is scoped
            only when it is itself a BUILTIN principal: a domain group nested into a local
            group keeps its global key.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $GroupSid,
        [Parameter(Mandatory)][string] $MemberSid,
        [Parameter(Mandatory)][string] $EdgeKind,
        [string] $HostKey
    )

    Assert-AdgSid -Value $GroupSid -Name 'GroupSid'
    Assert-AdgSid -Value $MemberSid -Name 'MemberSid'

    $groupKey = $GroupSid
    $memberKey = $MemberSid
    if ($EdgeKind -eq 'local_group_member') {
        if ([string]::IsNullOrWhiteSpace($HostKey)) {
            throw 'A local_group_member edge needs HostKey: BUILTIN group SIDs are identical on every computer.'
        }
        $lowerHost = $HostKey.ToLowerInvariant()
        $groupKey = "$lowerHost|$GroupSid"
        if ($MemberSid.StartsWith('S-1-5-32-')) {
            $memberKey = "$lowerHost|$MemberSid"
        }
    }
    return "edge|$groupKey->$memberKey|$EdgeKind"
}


# --- Observations ------------------------------------------------------------------------

function New-AdgObservation {
    <#
        .SYNOPSIS
            Prefix a kind-specific body with the five fields every observation carries.
        .DESCRIPTION
            Body must be an ordered dictionary so payloads are byte-stable across runs,
            which makes a diff of two dry runs readable. Keys whose value is $null are
            omitted: the contract forbids unknown fields and nulls where not allowed, and
            an absent optional field is how "the source did not say" is expressed.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $Kind,
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $SourceKey,
        [Parameter(Mandatory)][System.Collections.IDictionary] $Body,
        [string] $ObservedAt
    )

    Assert-AdgUuid -Value $RunId -Name 'RunId'
    if ([string]::IsNullOrWhiteSpace($SourceKey)) {
        throw "A $Kind observation must carry a source_key; ingestion is idempotent on (run_id, source_key)."
    }
    if (-not $ObservedAt) { $ObservedAt = Get-AdgTimestamp }

    $observation = [ordered]@{
        schema_version = $script:SchemaVersion
        kind           = $Kind
        run_id         = $RunId
        observed_at    = $ObservedAt
        source_key     = $SourceKey
    }
    foreach ($key in $Body.Keys) {
        if ($null -ne $Body[$key]) {
            $observation[$key] = $Body[$key]
        }
    }
    return $observation
}


function New-AdgPrincipalObservation {
    <#
        .SYNOPSIS
            A principal a collector resolved, or failed to resolve.
        .DESCRIPTION
            An unresolvable SID is a valid observation and often a finding. It is reported
            with a reason and never with a guessed name: a name from an earlier scan goes
            in LastKnownName, where it cannot be mistaken for a current resolution.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $Sid,
        [Parameter(Mandatory)][string] $PrincipalKind,
        [string] $HostKey,
        [string] $DomainSid,
        [string] $DisplayName,
        [string] $SamAccountName,
        [string] $UserPrincipalName,
        [string] $DistinguishedName,
        [string] $GroupScope,
        [string] $GroupType,
        [Nullable[bool]] $Enabled,
        [bool] $IsDeleted = $false,
        [string] $UnresolvedReason,
        [string] $LastKnownName,
        [string] $ObservedAt
    )

    if ($PrincipalKind -notin $script:PrincipalKinds) {
        throw "principal_kind '$PrincipalKind' is not in the contract. Accepted values: $($script:PrincipalKinds -join ', ')."
    }
    if ($GroupScope -and $GroupScope -notin $script:GroupScopes) {
        throw "group_scope '$GroupScope' is not in the contract. Accepted values: $($script:GroupScopes -join ', ')."
    }
    if ($GroupType -and $GroupType -notin $script:GroupTypes) {
        throw "group_type '$GroupType' is not in the contract. Accepted values: $($script:GroupTypes -join ', ')."
    }
    if ($UnresolvedReason -and $UnresolvedReason -notin $script:UnresolvedReasons) {
        throw "unresolved_reason '$UnresolvedReason' is not in the contract. Accepted values: $($script:UnresolvedReasons -join ', ')."
    }
    if ($PrincipalKind -eq 'unresolved' -and $DisplayName) {
        throw "An unresolved principal must not carry display_name (got '$DisplayName'). Report a name seen in an earlier scan as LastKnownName so it cannot be mistaken for a current resolution."
    }
    if ($HostKey) { Assert-AdgHostName -Value $HostKey -Name 'HostKey' }

    $sourceKey = Get-AdgPrincipalSourceKey -Sid $Sid -PrincipalKind $PrincipalKind -HostKey $HostKey
    if ($DomainSid) { Assert-AdgSid -Value $DomainSid -Name 'DomainSid' }

    $body = [ordered]@{
        sid                 = $Sid
        principal_kind      = $PrincipalKind
        domain_sid          = (ConvertTo-AdgOptional $DomainSid)
        host_key            = (ConvertTo-AdgOptional $HostKey)
        display_name        = (ConvertTo-AdgOptional $DisplayName)
        sam_account_name    = (ConvertTo-AdgOptional $SamAccountName)
        user_principal_name = (ConvertTo-AdgOptional $UserPrincipalName)
        distinguished_name  = (ConvertTo-AdgOptional $DistinguishedName)
        group_scope         = (ConvertTo-AdgOptional $GroupScope)
        group_type          = (ConvertTo-AdgOptional $GroupType)
        enabled             = $Enabled
        is_deleted          = $IsDeleted
        unresolved_reason   = (ConvertTo-AdgOptional $UnresolvedReason)
        last_known_name     = (ConvertTo-AdgOptional $LastKnownName)
    }
    return New-AdgObservation -Kind 'principal' -RunId $RunId -SourceKey $sourceKey -Body $body -ObservedAt $ObservedAt
}


function New-AdgMembershipObservation {
    <#
        .SYNOPSIS
            One directed edge: MemberSid is a member of GroupSid.
        .DESCRIPTION
            Membership is reported as edges, never as an expanded set: the edge chain is
            the explanation of access, and a flattened set cannot be explained or fixed.
            A group is never a direct member of itself; observing that indicates a defect
            in whatever produced the input, so it is refused here rather than emitted.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $GroupSid,
        [Parameter(Mandatory)][string] $MemberSid,
        [Parameter(Mandatory)][string] $EdgeKind,
        [string] $HostKey,
        [string] $MemberKind,
        [bool] $IsForeignSecurityPrincipal = $false,
        [string] $ObservedAt
    )

    if ($EdgeKind -notin $script:MembershipEdgeKinds) {
        throw "edge_kind '$EdgeKind' is not in the contract. Accepted values: $($script:MembershipEdgeKinds -join ', ')."
    }
    if ($MemberKind -and $MemberKind -notin $script:PrincipalKinds) {
        throw "member_kind '$MemberKind' is not in the contract. Accepted values: $($script:PrincipalKinds -join ', ')."
    }
    if ($GroupSid -eq $MemberSid) {
        throw "A group cannot be a direct member of itself ($GroupSid). Windows does not create such an edge; observing one indicates a collector defect."
    }
    if ($EdgeKind -eq 'local_group_member') {
        Assert-AdgHostName -Value $HostKey -Name 'HostKey'
    }
    elseif ($HostKey) {
        throw "host_key applies only to local_group_member edges; edge kind is '$EdgeKind'."
    }

    $sourceKey = Get-AdgMembershipSourceKey -GroupSid $GroupSid -MemberSid $MemberSid -EdgeKind $EdgeKind -HostKey $HostKey

    $body = [ordered]@{
        group_sid                     = $GroupSid
        member_sid                    = $MemberSid
        edge_kind                     = $EdgeKind
        host_key                      = (ConvertTo-AdgOptional $HostKey)
        member_kind                   = (ConvertTo-AdgOptional $MemberKind)
        is_foreign_security_principal = $IsForeignSecurityPrincipal
    }
    return New-AdgObservation -Kind 'membership_edge' -RunId $RunId -SourceKey $sourceKey -Body $body -ObservedAt $ObservedAt
}


function ConvertTo-AdgOptional {
    <#
        .SYNOPSIS
            Turn an empty string into $null so New-AdgObservation omits the field.
        .DESCRIPTION
            PowerShell binds an unsupplied [string] parameter to '', which would be sent as
            an empty display name rather than as "the source did not say".
    #>
    param([string] $Value)
    if ([string]::IsNullOrEmpty($Value)) { return $null }
    return $Value
}


# --- Envelopes ---------------------------------------------------------------------------

function New-AdgScope {
    <#
        .SYNOPSIS
            A reconciliation scope: the boundary inside which absence may be inferred.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $Kind,
        [Parameter(Mandatory)][string] $Key
    )

    if ($Kind -notin $script:ScopeKinds) {
        throw "Scope kind '$Kind' is not in the contract. Accepted values: $($script:ScopeKinds -join ', ')."
    }
    if ([string]::IsNullOrWhiteSpace($Key)) {
        throw "Scope key must not be empty; a scope with no key claims a boundary nobody can check."
    }
    # Scope keys are compared, never displayed: host names, share keys, and UNC paths are
    # all case-insensitive on Windows.
    return [ordered]@{ kind = $Kind; key = $Key.ToLowerInvariant() }
}


function New-AdgCheckpoint {
    <#
        .SYNOPSIS
            A resume point and the identity it belongs to (contract 1.4).
        .DESCRIPTION
            The issuer is not optional and is not a convenience. A uSNChanged cursor is a
            counter on one domain controller, so DC1's number replayed against DC2 skips
            every object whose USN on DC2 falls below it, and the same DC restored from
            backup reissues numbers it has already handed out. The server compares the
            issuer before it will move a stored cursor, and refuses when it changed.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][ValidateSet('usn', 'timestamp', 'opaque')][string] $Kind,
        [Parameter(Mandatory)][string] $Token,
        [Parameter(Mandatory)][string] $Issuer,
        [Parameter(Mandatory)][string] $IssuedAt
    )

    if ([string]::IsNullOrWhiteSpace($Token)) {
        throw 'A checkpoint token must not be empty: a cursor with no value cannot be resumed from, and storing one would let the next run believe it had a resume point when it has none.'
    }
    if ([string]::IsNullOrWhiteSpace($Issuer)) {
        throw 'A checkpoint needs the issuer that produced it. A cursor with no issuer looks usable everywhere, which is the one thing a per-server counter must never look like.'
    }
    if ($Kind -eq 'usn' -and $Token -notmatch '^[0-9]+$') {
        throw "A usn checkpoint's token must be a non-negative integer; received '$Token'. uSNChanged is a counter, and a token that is not one cannot be compared to the stored watermark."
    }

    return [ordered]@{
        kind      = $Kind
        token     = $Token
        issuer    = $Issuer
        issued_at = $IssuedAt
    }
}


function New-AdgAffirmation {
    <#
        .SYNOPSIS
            An object this scan re-read and found unchanged (contract 1.4).
        .DESCRIPTION
            The digest must come from *this* scan's reading. Copying it out of the
            collector's record of what it sent last time would make the affirmation a
            statement about the collector's memory rather than about the object, and the two
            diverge in exactly the case that matters: when the ACL has changed.

            The server recomputes and refuses a mismatch, so a stale digest cannot make ADG
            keep a stale ACL. What it can do is waste a round trip - and hide, from the
            operator reading the refusal count, the fact that the collector's index is the
            thing that is wrong.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $SourceKey,
        [Parameter(Mandatory)][string] $Digest,
        [Parameter(Mandatory)][string] $ObservedAt,
        [string] $Kind = 'ntfs_resource'
    )

    if ($Kind -ne 'ntfs_resource') {
        throw "Only 'ntfs_resource' may be affirmed in contract 1.4: it is the one kind with a published whole-object digest the server already recomputes from what it stores."
    }
    if ($Digest -notmatch '^[0-9a-f]{64}$') {
        throw "An affirmation's digest must be 64 lower-case hexadecimal characters; received '$Digest'. A malformed digest would be refused as a mismatch, and the collector would re-read the directory forever without being told the digest was the problem."
    }

    return [ordered]@{
        kind        = $Kind
        source_key  = $SourceKey
        digest      = $Digest
        observed_at = $ObservedAt
    }
}


function New-AdgScanRunStart {
    <#
        .SYNOPSIS
            The envelope that opens a run and declares what it intends to enumerate.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $Collector,
        [Parameter(Mandatory)][string] $CollectorHost,
        [Parameter(Mandatory)][string] $Method,
        [string] $CollectorVersion,
        [string] $Target,
        [Parameter(Mandatory)][string] $StartedAt,
        [Parameter(Mandatory)][AllowEmptyCollection()][System.Collections.IDictionary[]] $Scopes,
        [bool] $Incremental = $false,
        [string] $Notes,
        [ValidateSet('', 'full', 'delta', 'reconcile')][string] $Mode,
        [string] $Job,
        [System.Collections.IDictionary] $Baseline
    )

    Assert-AdgUuid -Value $RunId -Name 'RunId'
    Assert-AdgHostName -Value $CollectorHost -Name 'CollectorHost'
    if ($Mode) {
        if (($Mode -eq 'delta') -ne $Incremental) {
            throw "mode '$Mode' and incremental=$Incremental contradict each other. `incremental` is the flag that decides whether this run may ever mark an object absent, and `mode` says why; a payload where they disagree does not state which one the server should act on."
        }
        if ($Baseline -and $Mode -ne 'delta') {
            throw "A '$Mode' run resumes from nothing: it reads its whole scope, so a baseline checkpoint would record a starting point it did not start from."
        }
    }
    elseif ($Baseline) {
        throw 'A baseline checkpoint needs a mode. Send Mode delta alongside it.'
    }
    if ($Collector -notin $script:CollectorKinds) {
        throw "collector '$Collector' is not in the contract. Accepted values: $($script:CollectorKinds -join ', ')."
    }
    if ($Scopes.Count -lt 1) {
        throw 'A run must declare at least one scope; scopes is the boundary inside which absence may later be inferred.'
    }
    $scopeKeys = @($Scopes | ForEach-Object { "$($_['kind'])|$($_['key'])" })
    if (@($scopeKeys | Sort-Object -Unique).Count -ne $scopeKeys.Count) {
        throw 'A run must declare each scope once; duplicates make coverage ambiguous.'
    }

    $source = [ordered]@{
        collector      = $Collector
        collector_host = $CollectorHost
        method         = $Method
    }
    if ($CollectorVersion) { $source['collector_version'] = $CollectorVersion }
    if ($Target) { $source['target'] = $Target }

    $uses14 = [bool] ($Mode -or $Job -or $Baseline)
    $start = [ordered]@{
        schema_version = if ($uses14) { $script:IncrementalSchemaVersion } else { $script:SchemaVersion }
        run_id         = $RunId
        source         = $source
        started_at     = $StartedAt
        scopes         = @($Scopes)
        incremental    = $Incremental
    }
    if ($Notes) { $start['notes'] = $Notes }
    if ($Mode) { $start['mode'] = $Mode }
    if ($Job) { $start['job'] = $Job }
    if ($Baseline) { $start['baseline'] = $Baseline }
    return $start
}


function New-AdgObservationBatch {
    <#
        .SYNOPSIS
            A chunk of observations belonging to one run. The batch is the unit of retry.
        .DESCRIPTION
            Two contract rules are enforced here rather than discovered as a 422: a batch
            carries at most 1000 observations, and no source_key appears twice. Two
            observations with one key are indistinguishable, so the second would silently
            overwrite the first.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $BatchId,
        [Parameter(Mandatory)][int] $Sequence,
        [Parameter(Mandatory)][AllowEmptyCollection()][System.Collections.IDictionary[]] $Observations,
        [bool] $IsFinal = $false,
        [string] $ContinuationToken,
        [AllowEmptyCollection()][System.Collections.IDictionary[]] $Affirmations = @(),
        [System.Collections.IDictionary] $Checkpoint
    )

    Assert-AdgUuid -Value $RunId -Name 'RunId'
    Assert-AdgUuid -Value $BatchId -Name 'BatchId'
    if ($Sequence -lt 1) {
        throw "Batch sequence must start at 1; received $Sequence."
    }
    if ($Observations.Count -lt 1 -and $Affirmations.Count -lt 1) {
        throw 'A batch must carry at least one observation or affirmation; an empty batch consumes a batch id and a sequence number, tells the server nothing, and still counts towards coverage.'
    }
    if ($Affirmations.Count -gt $script:MaxBatchAffirmations) {
        throw "A batch carries at most $($script:MaxBatchAffirmations) affirmations; this one has $($Affirmations.Count). Split it."
    }
    if ($Observations.Count -gt $script:MaxBatchObservations) {
        throw "A batch carries at most $($script:MaxBatchObservations) observations; this one has $($Observations.Count). Split it. An oversized batch is rejected, never truncated, because silent truncation looks like coverage."
    }

    $mismatched = @($Observations | Where-Object { $_['run_id'] -ne $RunId })
    if ($mismatched.Count -gt 0) {
        throw "Every observation must carry the batch's run_id ($RunId); $($mismatched.Count) did not. Mixing runs in one batch would attribute observations to a run that never claimed their scope."
    }

    $keys = @($Observations | ForEach-Object { $_['source_key'] })
    $duplicates = @($keys | Group-Object | Where-Object Count -gt 1 | ForEach-Object Name)
    if ($duplicates.Count -gt 0) {
        throw "A batch must not contain the same source_key twice; found $($duplicates -join ', '). Two observations with one key are indistinguishable, so the second would silently overwrite the first."
    }

    $affirmedKeys = @($Affirmations | ForEach-Object { $_['source_key'] })
    $repeated = @($affirmedKeys | Group-Object | Where-Object Count -gt 1 | ForEach-Object Name)
    if ($repeated.Count -gt 0) {
        throw "A batch must not affirm the same source_key twice; found $($repeated -join ', ')."
    }
    $contradicted = @($affirmedKeys | Where-Object { $keys -contains $_ })
    if ($contradicted.Count -gt 0) {
        throw "A batch must not both observe and affirm $($contradicted -join ', '). One says the object's state is what this payload carries and the other says it is whatever the server already holds, and nothing in the batch says which reading was taken."
    }

    $uses14 = [bool] (($Affirmations.Count -gt 0) -or $Checkpoint)
    $batch = [ordered]@{
        schema_version = if ($uses14) { $script:IncrementalSchemaVersion } else { $script:SchemaVersion }
        run_id         = $RunId
        batch_id       = $BatchId
        sequence       = $Sequence
        is_final       = $IsFinal
    }
    if ($ContinuationToken) { $batch['continuation_token'] = $ContinuationToken }
    $batch['observations'] = @($Observations)
    if ($Affirmations.Count -gt 0) { $batch['affirmations'] = @($Affirmations) }
    if ($Checkpoint) { $batch['checkpoint'] = $Checkpoint }
    return $batch
}


function New-AdgCollectorError {
    <#
        .SYNOPSIS
            A failure the collector hit. Reported, never silently swallowed.
        .DESCRIPTION
            An unreadable object is a fact about coverage. Dropping it would let an
            incomplete scan present itself as complete, which is the most dangerous kind
            of wrong answer an audit tool can give.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $Code,
        [Parameter(Mandatory)][string] $Message,
        [string] $Target,
        [string] $OccurredAt
    )

    if ([string]::IsNullOrWhiteSpace($Code)) { throw 'A collector error needs a code.' }
    if ([string]::IsNullOrWhiteSpace($Message)) { throw 'A collector error needs a message.' }
    if (-not $OccurredAt) { $OccurredAt = Get-AdgTimestamp }

    # Not named $error: that is an automatic variable holding the session's error history.
    $record = [ordered]@{
        code    = $Code
        # The message is what an operator reads to decide whether this is a permissions
        # problem or an outage, so it is truncated rather than dropped if it is huge.
        message = if ($Message.Length -gt 2000) { $Message.Substring(0, 1997) + '...' } else { $Message }
    }
    if ($Target) { $record['target'] = if ($Target.Length -gt 1024) { $Target.Substring(0, 1024) } else { $Target } }
    $record['occurred_at'] = $OccurredAt
    return $record
}


function New-AdgScanRunCompletion {
    <#
        .SYNOPSIS
            Closes a run and, only when it is entitled to, reconciles the scopes it
            enumerated completely.
        .DESCRIPTION
            Reconciliation is the only path to absence in ADG. A run that hit any error
            cannot be 'succeeded' and cannot reconcile, so a partial scan is structurally
            incapable of marking an object it merely failed to read as gone.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $Status,
        [Parameter(Mandatory)][string] $CompletedAt,
        [Parameter(Mandatory)][int] $BatchCount,
        [Parameter(Mandatory)][int] $ObservationCount,
        [AllowEmptyCollection()][System.Collections.IDictionary[]] $Errors = @(),
        [AllowEmptyCollection()][System.Collections.IDictionary[]] $ReconciledScopes = @(),
        [string] $Notes,
        [int] $AffirmationCount = 0,
        [System.Collections.IDictionary] $Checkpoint
    )

    Assert-AdgUuid -Value $RunId -Name 'RunId'
    if ($Status -notin $script:RunStatuses) {
        throw "Run status '$Status' is not in the contract. Accepted values: $($script:RunStatuses -join ', ')."
    }
    $errorCount = $Errors.Count
    if ($Status -eq 'succeeded' -and $errorCount -gt 0) {
        throw "A run that hit $errorCount error(s) is 'partial' or 'failed', never 'succeeded'. Reporting complete coverage that was not achieved understates access."
    }
    if ($ReconciledScopes.Count -gt 0 -and ($Status -ne 'succeeded' -or $errorCount -gt 0)) {
        throw "A '$Status' run with $errorCount error(s) must not reconcile any scope. Objects it did not observe may still exist, and marking them absent would delete real access from the record."
    }
    if ($Checkpoint -and ($Status -ne 'succeeded' -or $errorCount -gt 0)) {
        throw "A '$Status' run with $errorCount error(s) must not record a checkpoint. The cursor would claim everything below it had been read, and the next delta would start above exactly the objects this run failed on - a gap that closes only by accident, because nothing afterwards looks missing."
    }

    $uses14 = [bool] (($AffirmationCount -gt 0) -or $Checkpoint)
    $completion = [ordered]@{
        schema_version    = if ($uses14) { $script:IncrementalSchemaVersion } else { $script:SchemaVersion }
        run_id            = $RunId
        status            = $Status
        completed_at      = $CompletedAt
        batch_count       = $BatchCount
        observation_count = $ObservationCount
        error_count       = $errorCount
        errors            = @($Errors)
        reconciled_scopes = @($ReconciledScopes)
    }
    if ($Notes) { $completion['notes'] = $Notes }
    if ($AffirmationCount -gt 0) { $completion['affirmation_count'] = $AffirmationCount }
    if ($Checkpoint) { $completion['checkpoint'] = $Checkpoint }
    return $completion
}


# --- Publishing --------------------------------------------------------------------------

function New-AdgCollectorHeaders {
    <#
        .SYNOPSIS
            The HTTP headers that authenticate a collector to the ADG API.
        .DESCRIPTION
            Since Phase 6A the ingestion endpoints reject an anonymous request. There are
            two credentials because there are two kinds of caller:

            - A **collector key** (X-ADG-Collector-Key) is the normal one. A scheduled task
              on a file server has no interactive user to borrow a token from, and the key
              grants exactly one capability: writing observations. It cannot read anything
              back, which is what makes it safe to leave in a task's configuration.
            - A **bearer token** is for an operator replaying a payload by hand, and carries
              whatever that account's roles grant.

            Both may be supplied; the API checks the key first. Built in one place so that
            every transport sends the same thing and a retry carries the same credential as
            the first attempt.
    #>
    [OutputType([hashtable])]
    param(
        [string] $CollectorKey,
        [string] $AuthenticationToken
    )

    $headers = @{}
    if ($AuthenticationToken) { $headers['Authorization'] = "Bearer $AuthenticationToken" }
    if ($CollectorKey) { $headers['X-ADG-Collector-Key'] = $CollectorKey }
    return $headers
}


function New-AdgPublisher {
    <#
        .SYNOPSIS
            Where a run's payloads go: the ADG API, or a directory on disk.
        .DESCRIPTION
            Offline mode exists so a collector can be validated without a live API -- the
            payloads it writes are the exact bytes it would have POSTed. The ADG test
            suite runs collectors this way and validates the output against the published
            schemas, which is what keeps a collector from drifting from the contract.

            No secret is ever read from a configuration file. A bearer token or collector
            key is taken from an environment variable the configuration names.

            Two credentials are accepted because there are two kinds of caller. A collector
            key (X-ADG-Collector-Key) is the normal one: a scheduled task on a file server
            has no interactive user to borrow a token from, and the key grants exactly one
            capability -- writing observations. A bearer token is for an operator replaying
            a payload by hand. Neither is optional: since Phase 6A the ingestion endpoints
            reject an anonymous request.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][ValidateSet('Api', 'Offline')][string] $Mode,
        [string] $ApiBaseUrl,
        [string] $OutputDirectory,
        [string] $AuthenticationToken,
        [string] $CollectorKey,
        [switch] $SkipCertificateCheck,
        [int] $MaxAttempts = 5,
        [int] $TimeoutSeconds = 100
    )

    if ($Mode -eq 'Api') {
        if ([string]::IsNullOrWhiteSpace($ApiBaseUrl)) {
            throw 'Api mode needs ApiBaseUrl, for example http://localhost:8000.'
        }
        if ($ApiBaseUrl -notmatch '^https?://') {
            throw "ApiBaseUrl must be an http or https URL; received '$ApiBaseUrl'."
        }
        if ($ApiBaseUrl.StartsWith('http://') -and -not ($ApiBaseUrl -match '^http://(localhost|127\.0\.0\.1)(:|/|$)')) {
            # Observations name every principal with access to every share: readable in
            # transit, they are a map of what to attack. Plain HTTP is allowed only for a
            # local development API.
            Write-Warning "ApiBaseUrl '$ApiBaseUrl' is plain HTTP. Collector payloads describe who can reach what; use HTTPS outside local development."
        }
    }
    else {
        if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
            throw 'Offline mode needs OutputDirectory: it is where the payloads are written instead of sent.'
        }
        if (-not (Test-Path -LiteralPath $OutputDirectory)) {
            New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
        }
    }

    $headers = New-AdgCollectorHeaders -CollectorKey $CollectorKey -AuthenticationToken $AuthenticationToken

    if ($Mode -eq 'Api' -and $headers.Count -eq 0) {
        # A warning rather than a refusal: an unauthenticated local API is still a valid
        # target while a developer is working on one, and the API's own 401 says exactly
        # what is missing. Silence here would turn that into a mysterious failed run.
        Write-Warning 'No collector key or authentication token was supplied. The ADG API rejects anonymous ingestion; set CollectorKey or AuthenticationToken.'
    }

    return @{
        Mode                 = $Mode
        ApiBaseUrl           = if ($ApiBaseUrl) { $ApiBaseUrl.TrimEnd('/') } else { $null }
        OutputDirectory      = $OutputDirectory
        Headers              = $headers
        SkipCertificateCheck = [bool] $SkipCertificateCheck
        MaxAttempts          = $MaxAttempts
        TimeoutSeconds       = $TimeoutSeconds
        BatchesPublished     = 0
    }
}


function Get-AdgHttpStatusCode {
    <#
        .SYNOPSIS
            The HTTP status behind a failed Invoke-RestMethod, or 0 for a transport error.
    #>
    [OutputType([int])]
    param([System.Management.Automation.ErrorRecord] $ErrorRecord)

    $exception = $ErrorRecord.Exception
    if ($null -eq $exception) { return 0 }
    if ($exception.PSObject.Properties.Name -contains 'Response' -and $null -ne $exception.Response) {
        try { return [int] $exception.Response.StatusCode } catch { return 0 }
    }
    if ($exception.PSObject.Properties.Name -contains 'StatusCode' -and $null -ne $exception.StatusCode) {
        try { return [int] $exception.StatusCode } catch { return 0 }
    }
    return 0
}


function Test-AdgRetryableStatus {
    <#
        .SYNOPSIS
            Whether re-sending the identical payload could plausibly succeed.
        .DESCRIPTION
            0 means the request never reached the API (DNS, TCP, TLS, timeout) and is worth
            retrying. 408, 429, and 5xx are transient by definition. Everything else is the
            API telling the collector the request itself is wrong: a 422 retried unchanged
            fails identically, and a 409 means the run is already closed.
    #>
    [OutputType([bool])]
    param([int] $StatusCode)

    if ($StatusCode -eq 0) { return $true }
    if ($StatusCode -in @(408, 429)) { return $true }
    return ($StatusCode -ge 500 -and $StatusCode -le 599)
}


function Invoke-AdgApiRequest {
    <#
        .SYNOPSIS
            POST a payload, retrying only what a retry could fix.
        .DESCRIPTION
            The retry reuses run_id and batch_id, so a retry after a timeout is recognized
            as a duplicate rather than applied twice. Backoff is exponential with jitter so
            a fleet of collectors recovering from a DC or API restart does not resynchronize
            into a thundering herd.
    #>
    param(
        [Parameter(Mandatory)][hashtable] $Publisher,
        [Parameter(Mandatory)][string] $Uri,
        [Parameter(Mandatory)][System.Collections.IDictionary] $Payload
    )

    $json = $Payload | ConvertTo-Json -Depth 12 -Compress
    $maxAttempts = [Math]::Max(1, $Publisher.MaxAttempts)

    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            $arguments = @{
                Method      = 'Post'
                Uri         = $Uri
                Body        = $json
                ContentType = 'application/json'
                Headers     = $Publisher.Headers
                TimeoutSec  = $Publisher.TimeoutSeconds
                ErrorAction = 'Stop'
            }
            if ($Publisher.SkipCertificateCheck) { $arguments['SkipCertificateCheck'] = $true }
            return Invoke-RestMethod @arguments
        }
        catch {
            $status = Get-AdgHttpStatusCode -ErrorRecord $_
            if (-not (Test-AdgRetryableStatus -StatusCode $status)) {
                throw "POST $Uri was rejected with HTTP $status and retrying cannot help: $($_.Exception.Message)"
            }
            if ($attempt -ge $maxAttempts) {
                throw "POST $Uri failed after $attempt attempt(s) (last status $status): $($_.Exception.Message)"
            }
            $delay = [Math]::Min([Math]::Pow(2, $attempt), 30)
            $jitter = (Get-Random -Minimum 0.0 -Maximum 1.0)
            Start-Sleep -Seconds ([Math]::Round($delay + $jitter, 2))
        }
    }
}


function Publish-AdgPayload {
    <#
        .SYNOPSIS
            Send one envelope to the API, or write it to the offline directory.
        .DESCRIPTION
            Offline mode writes both a readable per-payload JSON file and one line of
            envelopes.ndjson, in send order, so the whole run can be replayed or validated
            as a stream without reassembling it from file names.
    #>
    param(
        [Parameter(Mandatory)][hashtable] $Publisher,
        [Parameter(Mandatory)][ValidateSet('start', 'batch', 'completion')][string] $PayloadKind,
        [Parameter(Mandatory)][System.Collections.IDictionary] $Payload
    )

    if ($Publisher.Mode -eq 'Offline') {
        $name = switch ($PayloadKind) {
            'start' { 'start.json' }
            'completion' { 'completion.json' }
            'batch' { 'batch-{0:d3}.json' -f $Payload['sequence'] }
        }
        $target = Join-Path $Publisher.OutputDirectory $name
        $Payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $target -Encoding utf8NoBOM
        $stream = Join-Path $Publisher.OutputDirectory 'envelopes.ndjson'
        ($Payload | ConvertTo-Json -Depth 12 -Compress) |
            Add-Content -LiteralPath $stream -Encoding utf8NoBOM
        if ($PayloadKind -eq 'batch') { $Publisher.BatchesPublished++ }
        return [pscustomobject]@{ offline = $true; path = $target }
    }

    $runId = $Payload['run_id']
    $uri = switch ($PayloadKind) {
        'start' { "$($Publisher.ApiBaseUrl)/api/v1/scan-runs" }
        'batch' { "$($Publisher.ApiBaseUrl)/api/v1/scan-runs/$runId/batches" }
        'completion' { "$($Publisher.ApiBaseUrl)/api/v1/scan-runs/$runId/completion" }
    }
    $response = Invoke-AdgApiRequest -Publisher $Publisher -Uri $uri -Payload $Payload
    if ($PayloadKind -eq 'batch') { $Publisher.BatchesPublished++ }
    return $response
}


Export-ModuleMember -Function @(
    'Get-AdgSchemaVersion'
    'Get-AdgMaxBatchSize'
    'Get-AdgTimestamp'
    'New-AdgIdentifier'
    'Assert-AdgUuid'
    'Assert-AdgSid'
    'Assert-AdgHostName'
    'Get-AdgPrincipalSourceKey'
    'Get-AdgMembershipSourceKey'
    'New-AdgObservation'
    'New-AdgPrincipalObservation'
    'New-AdgMembershipObservation'
    'New-AdgScope'
    'New-AdgCheckpoint'
    'New-AdgAffirmation'
    'New-AdgScanRunStart'
    'New-AdgObservationBatch'
    'New-AdgCollectorError'
    'New-AdgScanRunCompletion'
    'New-AdgCollectorHeaders'
    'New-AdgPublisher'
    'Get-AdgHttpStatusCode'
    'Test-AdgRetryableStatus'
    'Invoke-AdgApiRequest'
    'Publish-AdgPayload'
)
