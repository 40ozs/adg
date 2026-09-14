<#
.SYNOPSIS
    Worked example of the ADG collector protocol: start a scan run, send a batch of
    observations, and complete the run.

.DESCRIPTION
    This is a teaching example for collector authors, not a collector. It uses a small set
    of hard-coded observations that mirror the canonical fixture
    `backend/tests/fixtures/scenarios/03-nested-group-grant.json`, so the payload shapes
    here are exactly the ones the contract tests validate.

    What it demonstrates:

      * building each observation with the five fields every observation carries;
      * deriving a stable `source_key` per kind, which is what makes ingestion idempotent;
      * converting .NET ACL objects to the raw `ace_flags` byte the contract expects;
      * batching, retrying with the same batch_id, and completing with reconciliation.

    Run it with -DryRun to write the exact JSON it would POST, without a server. The ADG
    test suite runs it that way and validates the output against the published schemas, so
    this example cannot drift from the contract.

.PARAMETER ApiBaseUrl
    Base URL of the ADG API, for example http://localhost:8000.

.PARAMETER DryRun
    Write the payloads instead of sending them. Requires -OutputDirectory.

.PARAMETER OutputDirectory
    Where -DryRun writes start.json, batch-001.json, and completion.json.

.EXAMPLE
    .\Send-AdgScanRun.ps1 -DryRun -OutputDirectory C:\code\adg\.tmp\example

.EXAMPLE
    .\Send-AdgScanRun.ps1 -ApiBaseUrl http://localhost:8000

.NOTES
    ADG is read-only. Nothing here writes to a target system.
    See docs/contracts/collector-protocol.md for the normative rules.
#>
[CmdletBinding(DefaultParameterSetName = 'Send')]
param(
    [Parameter(ParameterSetName = 'Send', Mandatory = $true)]
    [string] $ApiBaseUrl,

    [Parameter(ParameterSetName = 'DryRun', Mandatory = $true)]
    [switch] $DryRun,

    [Parameter(ParameterSetName = 'DryRun', Mandatory = $true)]
    [string] $OutputDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$SchemaVersion = '1.0'

# --- Contract helpers -------------------------------------------------------------------

function Get-AdgTimestamp {
    <#
        .SYNOPSIS
            RFC 3339 timestamp in UTC. A naive timestamp is rejected by the contract:
            observations from hosts in different time zones must stay orderable.
    #>
    [OutputType([string])]
    param([datetime] $Instant = [datetime]::UtcNow)
    return ([datetimeoffset]::new($Instant, [timespan]::Zero)).ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
}

function ConvertTo-AdgAceFlags {
    <#
        .SYNOPSIS
            Collapse the three .NET inheritance properties into the raw ACE flags byte.
        .DESCRIPTION
            Get-Acl splits inheritance across InheritanceFlags, PropagationFlags, and
            IsInherited. The contract wants the ACE_HEADER.AceFlags byte, unmodified.
    #>
    [OutputType([int])]
    param(
        [System.Security.AccessControl.InheritanceFlags] $InheritanceFlags,
        [System.Security.AccessControl.PropagationFlags] $PropagationFlags,
        [bool] $IsInherited
    )

    [int] $flags = 0
    if ($InheritanceFlags.HasFlag([System.Security.AccessControl.InheritanceFlags]::ObjectInherit)) {
        $flags = $flags -bor 0x01
    }
    if ($InheritanceFlags.HasFlag([System.Security.AccessControl.InheritanceFlags]::ContainerInherit)) {
        $flags = $flags -bor 0x02
    }
    if ($PropagationFlags.HasFlag([System.Security.AccessControl.PropagationFlags]::NoPropagateInherit)) {
        $flags = $flags -bor 0x04
    }
    if ($PropagationFlags.HasFlag([System.Security.AccessControl.PropagationFlags]::InheritOnly)) {
        $flags = $flags -bor 0x08
    }
    if ($IsInherited) {
        $flags = $flags -bor 0x10
    }
    return $flags
}

function Get-AdgPrincipalKey {
    [OutputType([string])]
    param([string] $Sid, [string] $PrincipalKind, [string] $HostKey)
    if ($PrincipalKind -eq 'local_group') {
        if ([string]::IsNullOrWhiteSpace($HostKey)) {
            throw 'A local group needs a host: S-1-5-32-544 is identical on every computer.'
        }
        return "principal|$($HostKey.ToLowerInvariant())|$Sid"
    }
    return "principal|$Sid"
}

function Get-AdgMembershipKey {
    [OutputType([string])]
    param([string] $GroupSid, [string] $MemberSid, [string] $EdgeKind, [string] $HostKey)

    $groupKey = $GroupSid
    $memberKey = $MemberSid
    if ($EdgeKind -eq 'local_group_member') {
        $lowerHost = $HostKey.ToLowerInvariant()
        $groupKey = "$lowerHost|$GroupSid"
        # Only a BUILTIN member is host-scoped; a domain principal keeps its global key.
        if ($MemberSid.StartsWith('S-1-5-32-')) {
            $memberKey = "$lowerHost|$MemberSid"
        }
    }
    return "edge|$groupKey->$memberKey|$EdgeKind"
}

function Get-AdgServerKey {
    [OutputType([string])]
    param([string] $Name)
    return "server|$($Name.ToLowerInvariant())"
}

function Get-AdgShareKey {
    [OutputType([string])]
    param([string] $ServerName, [string] $ShareName)
    return "share|$($ServerName.ToLowerInvariant())|$($ShareName.ToLowerInvariant())"
}

function Get-AdgSmbAceKey {
    [OutputType([string])]
    param(
        [string] $ServerName,
        [string] $ShareName,
        [string] $TrusteeSid,
        [string] $AceType,
        [string] $Permission,
        [Nullable[int]] $AccessMask
    )
    $right = if ($Permission) { $Permission } else { '0x{0:x8}' -f $AccessMask }
    return "smb_ace|$($ServerName.ToLowerInvariant())|$($ShareName.ToLowerInvariant())|$TrusteeSid|$AceType|$right"
}

function Get-AdgResourceKey {
    [OutputType([string])]
    param([string] $Path)
    return "resource|$($Path.ToLowerInvariant())"
}

function Get-AdgNtfsAceKey {
    [OutputType([string])]
    param(
        [string] $Path,
        [string] $TrusteeSid,
        [string] $AceType,
        [int] $AccessMask,
        [int] $AceFlags
    )
    $mask = '0x{0:x8}' -f $AccessMask
    $flags = '0x{0:x2}' -f $AceFlags
    return "ntfs_ace|$($Path.ToLowerInvariant())|$TrusteeSid|$AceType|$mask|$flags"
}

function New-AdgObservation {
    <#
        .SYNOPSIS
            Add the five fields every observation carries to a kind-specific body.
    #>
    [OutputType([hashtable])]
    param(
        [string] $Kind,
        [string] $RunId,
        [string] $SourceKey,
        [hashtable] $Body,
        [string] $ObservedAt = (Get-AdgTimestamp)
    )

    $observation = [ordered]@{
        schema_version = $SchemaVersion
        kind           = $Kind
        run_id         = $RunId
        observed_at    = $ObservedAt
        source_key     = $SourceKey
    }
    foreach ($key in $Body.Keys) {
        # Omit absent values entirely; the contract rejects unknown and null-where-not-allowed.
        if ($null -ne $Body[$key]) {
            $observation[$key] = $Body[$key]
        }
    }
    return $observation
}

# --- Transport --------------------------------------------------------------------------

function Invoke-AdgPost {
    <#
        .SYNOPSIS
            POST a payload, retrying transient failures with the identical body.
        .DESCRIPTION
            The retry reuses run_id/batch_id, so a retry after a timeout is recognized as a
            duplicate rather than applied twice. A 422 is never retried: the payload is
            wrong and will fail identically.
    #>
    param(
        [string] $Uri,
        [hashtable] $Payload,
        [int] $MaxAttempts = 5
    )

    $json = $Payload | ConvertTo-Json -Depth 12 -Compress

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            return Invoke-RestMethod -Method Post -Uri $Uri -Body $json -ContentType 'application/json'
        }
        catch {
            $status = 0
            if ($_.Exception.PSObject.Properties.Name -contains 'Response' -and $_.Exception.Response) {
                $status = [int] $_.Exception.Response.StatusCode
            }
            if ($status -eq 422) {
                Write-Error "The payload was rejected as invalid; retrying cannot help. $($_.Exception.Message)"
                throw
            }
            if ($attempt -eq $MaxAttempts) { throw }
            $delay = [Math]::Pow(2, $attempt)
            Write-Warning "POST $Uri failed (attempt $attempt/$MaxAttempts); retrying in ${delay}s."
            Start-Sleep -Seconds $delay
        }
    }
}

# --- Example observations ---------------------------------------------------------------
# A real collector reads these from Active Directory, Get-SmbShare, and Get-Acl. They are
# inline here so the example runs anywhere and stays comparable to fixture 03.

$server = 'FS01'
$share = 'Finance'
$sharePath = '\\FS01\Finance'
$domain = 'S-1-5-21-1004336348-1177238915-682003330'
$alice = "$domain-1104"
$financeTeam = "$domain-1201"
$financeRw = "$domain-1202"
$modifyMask = 0x001301BF

$runId = [guid]::NewGuid().ToString()
$batchId = [guid]::NewGuid().ToString()
$startedAt = Get-AdgTimestamp

$observations = @(
    New-AdgObservation -Kind 'server' -RunId $runId -SourceKey (Get-AdgServerKey $server) -Body @{
        name             = $server
        dns_host_name    = 'fs01.corp.example.com'
        is_domain_member = $true
    }

    New-AdgObservation -Kind 'smb_share' -RunId $runId -SourceKey (Get-AdgShareKey $server $share) -Body @{
        server_name = $server
        share_name  = $share
        local_path  = 'D:\Shares\Finance'
        share_type  = 'disk'
    }

    New-AdgObservation -Kind 'smb_ace' -RunId $runId `
        -SourceKey (Get-AdgSmbAceKey $server $share 'S-1-1-0' 'allow' 'full' $null) -Body @{
        server_name = $server
        share_name  = $share
        trustee_sid = 'S-1-1-0'
        ace_type    = 'allow'
        permission  = 'full'      # exactly one right form: level OR mask, never both
        order_index = 0
    }

    New-AdgObservation -Kind 'principal' -RunId $runId `
        -SourceKey (Get-AdgPrincipalKey $alice 'user' $null) -Body @{
        sid              = $alice
        principal_kind   = 'user'
        display_name     = 'Alice Smith'
        sam_account_name = 'asmith'
        enabled          = $true
    }

    New-AdgObservation -Kind 'principal' -RunId $runId `
        -SourceKey (Get-AdgPrincipalKey $financeTeam 'domain_group' $null) -Body @{
        sid            = $financeTeam
        principal_kind = 'domain_group'
        display_name   = 'Finance-Team'
        group_scope    = 'global'
        group_type     = 'security'
    }

    New-AdgObservation -Kind 'principal' -RunId $runId `
        -SourceKey (Get-AdgPrincipalKey $financeRw 'domain_group' $null) -Body @{
        sid            = $financeRw
        principal_kind = 'domain_group'
        display_name   = 'Finance-RW'
        group_scope    = 'domain_local'
        group_type     = 'security'
    }

    New-AdgObservation -Kind 'membership_edge' -RunId $runId `
        -SourceKey (Get-AdgMembershipKey $financeTeam $alice 'directory_group_member' $null) -Body @{
        group_sid   = $financeTeam
        member_sid  = $alice
        edge_kind   = 'directory_group_member'
        member_kind = 'user'
    }

    New-AdgObservation -Kind 'membership_edge' -RunId $runId `
        -SourceKey (Get-AdgMembershipKey $financeRw $financeTeam 'directory_group_member' $null) -Body @{
        group_sid   = $financeRw
        member_sid  = $financeTeam
        edge_kind   = 'directory_group_member'
        member_kind = 'domain_group'
    }

    New-AdgObservation -Kind 'ntfs_resource' -RunId $runId `
        -SourceKey (Get-AdgResourceKey $sharePath) -Body @{
        path                  = $sharePath
        server_name           = $server
        share_name            = $share
        dacl_present          = $true       # $false means a NULL DACL: everyone has access
        dacl_protected        = $false
        ace_count             = 1
        inheritance_enabled   = $true
        is_acl_boundary       = $true
        depth_from_share_root = 0
    }

    New-AdgObservation -Kind 'ntfs_ace' -RunId $runId `
        -SourceKey (Get-AdgNtfsAceKey $sharePath $financeRw 'allow' $modifyMask 0x03) -Body @{
        path        = $sharePath
        trustee_sid = $financeRw
        ace_type    = 'allow'
        access_mask = $modifyMask   # raw mask; never translated, never expanded
        ace_flags   = ConvertTo-AdgAceFlags `
            ([System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit') `
            ([System.Security.AccessControl.PropagationFlags]::None) `
            $false
        source      = 'explicit'
        order_index = 0
    }
)

$start = [ordered]@{
    schema_version = $SchemaVersion
    run_id         = $runId
    source         = [ordered]@{
        collector         = 'ntfs'
        collector_host    = $env:COMPUTERNAME ?? 'COLLECTOR01'
        method            = 'System.IO.DirectoryInfo.GetAccessControl'
        collector_version = '0.1.0'
        target            = $sharePath
    }
    started_at     = $startedAt
    scopes         = @(
        [ordered]@{ kind = 'directory_tree'; key = $sharePath.ToLowerInvariant() }
    )
    incremental    = $false
}

$batch = [ordered]@{
    schema_version = $SchemaVersion
    run_id         = $runId
    batch_id       = $batchId
    sequence       = 1
    is_final       = $true
    observations   = $observations
}

# A clean, complete run is the only kind that may reconcile: reconciliation is what lets the
# server mark unseen objects absent, so a partial run must never claim it.
$completion = [ordered]@{
    schema_version    = $SchemaVersion
    run_id            = $runId
    status            = 'succeeded'
    completed_at      = Get-AdgTimestamp
    batch_count       = 1
    observation_count = $observations.Count
    error_count       = 0
    errors            = @()
    reconciled_scopes = @(
        [ordered]@{ kind = 'directory_tree'; key = $sharePath.ToLowerInvariant() }
    )
}

# --- Send or dump -----------------------------------------------------------------------

if ($DryRun) {
    if (-not (Test-Path -LiteralPath $OutputDirectory)) {
        New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    }
    $pairs = @(
        @{ Name = 'start.json'; Payload = $start },
        @{ Name = 'batch-001.json'; Payload = $batch },
        @{ Name = 'completion.json'; Payload = $completion }
    )
    foreach ($pair in $pairs) {
        $target = Join-Path $OutputDirectory $pair.Name
        $pair.Payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $target -Encoding utf8
        Write-Host "Wrote $target"
    }
    Write-Host "Dry run complete: $($observations.Count) observations in 1 batch."
    return
}

Write-Host "Starting run $runId against $ApiBaseUrl"
Invoke-AdgPost -Uri "$ApiBaseUrl/api/v1/scan-runs" -Payload $start | Out-Null

Write-Host "Sending batch $batchId with $($observations.Count) observations"
Invoke-AdgPost -Uri "$ApiBaseUrl/api/v1/scan-runs/$runId/batches" -Payload $batch | Out-Null

Write-Host 'Completing the run and reconciling the scope'
Invoke-AdgPost -Uri "$ApiBaseUrl/api/v1/scan-runs/$runId/completion" -Payload $completion | Out-Null

Write-Host "Run $runId complete."
