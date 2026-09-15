<#
.SYNOPSIS
    Collect Windows SMB shares and raw share-level ACLs, and submit them to the ADG API.

.DESCRIPTION
    The entry point for the ADG SMB collector. It enumerates the shares published by the
    servers you name, reads each share's ACL as raw facts - SIDs and access masks, or the
    three permission levels when only those are available - and reports them as contract
    v1 observations.

    It does not compute effective access. Share permissions and NTFS permissions are
    separate layers: remote access is limited by both, and local access bypasses the share
    layer entirely. Combining them is the backend's job, from this collector's facts and
    the NTFS collector's.

    ADG is read-only. Nothing here writes to a target server, takes ownership, or modifies
    a security descriptor to make a read succeed.

    Targets are always explicit. There is no domain-wide sweep: pass -Server, or list
    servers in a configuration file. See adg-smb-targets.example.json.

.PARAMETER ApiBaseUrl
    Base URL of the ADG API, for example http://localhost:8000.

.PARAMETER ConfigPath
    Path to a JSON target configuration. Merged with -Server if both are given.

.PARAMETER Server
    Server names to scan.

.PARAMETER Credential
    Optional alternate credentials for the CIM sessions. Omit it in production: the
    collector is meant to run as a group managed service account, so that no password
    exists to store.

.PARAMETER RunPerServer
    Emit one scan run per server instead of one run for all of them. Recommended for any
    estate larger than a handful of hosts: a run that reports any error may not reconcile,
    so in a single combined run one unreachable server stops every other server's shares
    from ever being marked absent.

.PARAMETER DryRun
    Write the exact payloads that would be POSTed, and send nothing. Requires
    -OutputDirectory.

.PARAMETER OutputDirectory
    Where -DryRun writes run-NN-start.json, run-NN-batch-NNN.json, and
    run-NN-completion.json.

.EXAMPLE
    .\Invoke-AdgSmbScan.ps1 -ApiBaseUrl http://localhost:8000 -Server FS01,FS02 -RunPerServer

.EXAMPLE
    .\Invoke-AdgSmbScan.ps1 -ConfigPath .\adg-smb-targets.json -DryRun -OutputDirectory C:\code\adg\.tmp\smb

.NOTES
    Minimum privileges, firewall rules, and remote-management prerequisites are documented
    in README.md next to this script. Domain Admin is not required and must not be used.
#>
[CmdletBinding(DefaultParameterSetName = 'Send')]
param(
    [Parameter(ParameterSetName = 'Send', Mandatory = $true)]
    [string] $ApiBaseUrl,

    [Parameter(ParameterSetName = 'DryRun', Mandatory = $true)]
    [switch] $DryRun,

    [Parameter(ParameterSetName = 'DryRun', Mandatory = $true)]
    [string] $OutputDirectory,

    [string] $ConfigPath,
    # Emit one normalized run summary to the pipeline. The orchestrator needs it: a
    # scheduled invocation has to record the cursor a run reached and whether it was clean
    # enough to keep it, and an exit code carries neither. Without the switch the script
    # behaves exactly as it did before.
    [switch] $PassThru,
    [string[]] $Server = @(),
    [pscredential] $Credential,
    [switch] $RunPerServer,
    [string] $CollectorVersion = '0.1.0',

    # Where the ADG API credential is read from. An environment variable rather than a
    # parameter value, so the secret is never in a command line, a scheduled-task argument
    # list, or a shell history. The API rejects anonymous ingestion.
    [string] $CollectorKeyEnvironmentVariable = 'ADG_COLLECTOR_KEY',
    [string] $ApiTokenEnvironmentVariable = 'ADG_COLLECTOR_TOKEN'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'AdgSmbCollector.psd1') -Force

$settings = Import-AdgSmbTarget -Path $ConfigPath -Server $Server

Write-Host "Scanning $($settings.Servers.Count) server(s): $($settings.Servers -join ', ')"
if (-not $settings.IncludeAdminShares) {
    Write-Host 'Administrative and system shares are excluded. Their ACLs are a Windows constant, not an organizational decision.'
}

$runs = Invoke-AdgSmbScan -Settings $settings -Credential $Credential -RunPerServer:$RunPerServer `
    -CollectorVersion $CollectorVersion

foreach ($run in $runs) {
    $summary = $run.Summary
    Write-Host ("Run {0}: {1} - {2}/{3} server(s) reached, {4} observation(s) in {5} batch(es), {6} error(s), {7} share(s) excluded, {8} scope(s) reconcilable." -f `
            $summary.RunId, $summary.Status, $summary.ServersReached, $summary.ServersRequested, `
            $summary.ObservationCount, $summary.BatchCount, $summary.ErrorCount, $summary.SharesExcluded, `
            $summary.ReconciledScopes)

    if ($summary.ServersUnreachable.Count -gt 0) {
        # Not a warning about tidiness: an unreachable server means this run cannot say
        # anything about that server's shares, and must not be read as having found none.
        Write-Warning ("Unreachable: {0}. Their shares are unobserved, not absent; the run reconciles nothing." -f ($summary.ServersUnreachable -join ', '))
    }
}

if ($DryRun) {
    if (-not (Test-Path -LiteralPath $OutputDirectory)) {
        New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    }

    $index = 0
    foreach ($run in $runs) {
        $index++
        $prefix = 'run-{0:d2}' -f $index

        $run.Start | ConvertTo-Json -Depth 12 |
            Set-Content -LiteralPath (Join-Path $OutputDirectory "$prefix-start.json") -Encoding utf8

        foreach ($batch in @($run.Batches)) {
            $name = '{0}-batch-{1:d3}.json' -f $prefix, $batch.sequence
            $batch | ConvertTo-Json -Depth 12 |
                Set-Content -LiteralPath (Join-Path $OutputDirectory $name) -Encoding utf8
        }

        $run.Completion | ConvertTo-Json -Depth 12 |
            Set-Content -LiteralPath (Join-Path $OutputDirectory "$prefix-completion.json") -Encoding utf8
    }

    Write-Host "Dry run complete. Payloads written to $OutputDirectory"
    return
}

$headers = @{}
$collectorKey = [System.Environment]::GetEnvironmentVariable($CollectorKeyEnvironmentVariable)
$apiToken = [System.Environment]::GetEnvironmentVariable($ApiTokenEnvironmentVariable)
if ($apiToken) { $headers['Authorization'] = "Bearer $apiToken" }
if ($collectorKey) { $headers['X-ADG-Collector-Key'] = $collectorKey }
if ($headers.Count -eq 0) {
    Write-Warning "No credential found in `$env:$CollectorKeyEnvironmentVariable or `$env:$ApiTokenEnvironmentVariable. The ADG API rejects anonymous ingestion and will answer 401."
}

$submitted = [System.Collections.Generic.List[object]]::new()
foreach ($run in $runs) {
    $result = Send-AdgSmbScanRun -ApiBaseUrl $ApiBaseUrl -Run $run -Headers $headers
    Write-Host ("Submitted run {0}: {1}, {2} batch(es) sent, {3} rejected." -f `
            $result.RunId, $result.Status, $result.BatchesSent, $result.BatchesRejected)
    $submitted.Add($result)
}

if ($PassThru) {
    # One summary for what may have been several runs -- a server each, when -RunPerServer
    # is set. The *worst* status wins, because a sweep in which one file server failed has
    # not inventoried the estate, and reporting the best of its parts would be reporting
    # coverage that was not achieved.
    $ranked = @{ succeeded = 0; partial = 1; canceled = 2; failed = 3 }
    $worst = 'succeeded'
    $observations = 0
    foreach ($result in $submitted) {
        $status = [string] $result.Status
        if (-not $ranked.ContainsKey($status)) { $status = 'failed' }
        if ($ranked[$status] -gt $ranked[$worst]) { $worst = $status }
        $observations += [int] $result.ObservationCount
    }
    if ($submitted.Count -eq 0) { $worst = 'failed' }

    [pscustomobject]@{
        Status           = $worst
        RunId            = if ($submitted.Count -gt 0) { [string] $submitted[0].RunId } else { '' }
        Mode             = 'full'
        ObservationCount = $observations
        AffirmationCount = 0
        ErrorCount       = @($submitted | Where-Object { $_.Status -ne 'succeeded' }).Count
        Reconciled       = ($worst -eq 'succeeded')
        # None, and not an omission: the SMB server publishes no change metadata, so there
        # is no cursor a later run could resume from. Every SMB run reads everything.
        Checkpoint       = $null
        Message          = "$($submitted.Count) server run(s); worst status '$worst'"
    }
}
