#Requires -Version 7.0
<#
.SYNOPSIS
    Run an ADG Active Directory collection: users, groups, computers, and their direct
    membership edges.

.DESCRIPTION
    Reads the directory and submits contract v1 observations to the ADG API, or writes
    them to a directory for validation without a live API.

    The collector is read-only and needs no special privilege: read access to the
    directory, which any authenticated domain account has by default, is enough. Domain
    Admin is never required. See docs/collectors/ad.md.

    Membership is reported as direct edges. Nested groups are never expanded: the chain of
    edges is the explanation of access, and a flattened set cannot be explained or fixed.

.PARAMETER ConfigPath
    A JSON configuration file. Copy config/adg-ad-collector.example.json and edit it. Every
    parameter below overrides the corresponding file setting.

.PARAMETER DomainController
    The directory server to bind. Omit to let the LDAP client locate one for this host's
    own domain.

.PARAMETER SearchBase
    The distinguished name to enumerate. Defaults to the directory's default naming
    context.

.PARAMETER IncludeOrganizationalUnit
    Enumerate only these containers. A run narrowed this way is marked incremental and can
    never reconcile the domain scope: it deliberately read only part of the domain.

.PARAMETER ExcludeOrganizationalUnit
    Do not enumerate these containers. A principal inside one is still recorded when an
    in-scope group grants it membership, because an edge pointing at a SID with no
    principal behind it cannot be audited.

.PARAMETER ApiBaseUrl
    Base URL of the ADG API, for example https://adg.corp.example.com.

.PARAMETER Offline
    Write the payloads instead of sending them. Requires -OutputDirectory. The files are
    the exact bytes that would have been POSTed.

.PARAMETER OutputDirectory
    Where -Offline writes start.json, batch-NNN.json, completion.json, and
    envelopes.ndjson.

.PARAMETER DirectoryFixture
    Development and test only: run the collector against a JSON fixture instead of a
    directory server. This is how the test suite exercises collection without a domain; it
    is never a way to scan a real environment.

.PARAMETER StateFile
    Where to record the change watermark (uSNChanged and whenChanged) this run reached.
    Only a clean, complete run writes one.

.PARAMETER Job
    The scheduled job this run belongs to. The server keys this collector's checkpoint by
    it, so a run that sends a checkpoint must name one.

.PARAMETER Passes
    'all' (the default), 'principals', or 'memberships'. A run that reads one half of the
    domain has not enumerated the domain: it declares the domain scope, because that is what
    it set out to look at, and marks itself incremental, which is what stops it reconciling.
    A principals pass that reconciled the domain would mark every membership edge absent.

.PARAMETER SinceUsn
    Read only objects whose uSNChanged is at or above this watermark. Requires
    -CheckpointIssuer: a USN is a counter on one domain controller, and one replayed against
    a different server -- or the same one after a restore from backup -- silently skips every
    object whose USN falls below it. A delta can never report a deletion, so it never
    reconciles; run the reconciliation job for that.

.PARAMETER CheckpointIssuer
    The directory-server incarnation the watermark came from: dsServiceName and invocationId
    joined. The run compares it against the server it binds and reads everything on a
    mismatch.

.EXAMPLE
    .\Invoke-AdgAdCollector.ps1 -ConfigPath .\config\adg-ad-collector.json

.EXAMPLE
    .\Invoke-AdgAdCollector.ps1 -Offline -OutputDirectory C:\code\adg\.tmp\ad-run

.EXAMPLE
    .\Invoke-AdgAdCollector.ps1 -DomainController dc01.corp.example.com -ApiBaseUrl https://adg.corp.example.com

.NOTES
    The collector token is read from the environment variable named by
    apiTokenEnvironmentVariable (default ADG_COLLECTOR_TOKEN). Secrets are never stored in
    configuration.
#>
[CmdletBinding()]
param(
    [string] $ConfigPath,
    [string] $Domain,
    [string] $DomainController,
    [int] $Port,
    [switch] $UseTls,
    [string] $SearchBase,
    [string[]] $IncludeOrganizationalUnit,
    [string[]] $ExcludeOrganizationalUnit,
    [int] $BatchSize,
    [string] $ApiBaseUrl,
    [switch] $Offline,
    [string] $OutputDirectory,
    [string] $DirectoryFixture,
    [switch] $Incremental,
    [string] $StateFile,
    [string] $Job,
    [ValidateSet('all', 'principals', 'memberships')][string] $Passes,
    [Nullable[long]] $SinceUsn,
    [string] $CheckpointIssuer,
    # Emit one normalized run summary to the pipeline. The orchestrator needs it: a
    # scheduled invocation has to record the cursor a run reached and whether it was clean
    # enough to keep it, and an exit code carries neither. Without the switch the script
    # behaves exactly as it did before.
    [switch] $PassThru,
    [string] $CollectorHost,
    [switch] $SkipCertificateCheck
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptRoot = Split-Path -Parent $PSCommandPath
Import-Module (Join-Path $scriptRoot 'AdgCollector.ActiveDirectory.psd1') -Force
Import-Module (Join-Path (Split-Path -Parent $scriptRoot) 'common/AdgCollector.Common.psd1') -Force

$config = if ($ConfigPath) {
    Import-AdgAdCollectorConfig -Path $ConfigPath
}
else {
    $null
}

# Command-line arguments override the file. Only parameters the caller actually bound are
# applied: an unbound [int] is 0 and an unbound [switch] is $false, and either would
# silently overwrite a good value from the configuration file.
$overrides = @{}
$overrideMap = @{
    Domain                    = 'Domain'
    DomainController          = 'DomainController'
    Port                      = 'Port'
    UseTls                    = 'UseTls'
    SearchBase                = 'SearchBase'
    IncludeOrganizationalUnit = 'IncludeOrganizationalUnits'
    ExcludeOrganizationalUnit = 'ExcludeOrganizationalUnits'
    BatchSize                 = 'BatchSize'
    ApiBaseUrl                = 'ApiBaseUrl'
    Offline                   = 'Offline'
    OutputDirectory           = 'OutputDirectory'
    Incremental               = 'Incremental'
    StateFile                 = 'StateFile'
    CollectorHost             = 'CollectorHost'
    SkipCertificateCheck      = 'SkipCertificateCheck'
    Job                       = 'Job'
    Passes                    = 'Passes'
    SinceUsn                  = 'SinceUsn'
    CheckpointIssuer          = 'CheckpointIssuer'
}
foreach ($parameter in $overrideMap.Keys) {
    if ($PSBoundParameters.ContainsKey($parameter)) {
        $value = $PSBoundParameters[$parameter]
        if ($value -is [switch]) { $value = [bool] $value.IsPresent }
        $overrides[$overrideMap[$parameter]] = $value
    }
}

if ($null -eq $config) {
    $config = New-AdgAdCollectorConfig @overrides
}
else {
    foreach ($key in $overrides.Keys) { $config[$key] = $overrides[$key] }
    $config = New-AdgAdCollectorConfig `
        -Domain $config.Domain -DomainController $config.DomainController -Port $config.Port `
        -UseTls $config.UseTls -SearchBase $config.SearchBase `
        -IncludeOrganizationalUnits $config.IncludeOrganizationalUnits `
        -ExcludeOrganizationalUnits $config.ExcludeOrganizationalUnits `
        -BatchSize $config.BatchSize -PageSize $config.PageSize -RangeStep $config.RangeStep `
        -ApiBaseUrl $config.ApiBaseUrl -ApiTokenEnvironmentVariable $config.ApiTokenEnvironmentVariable `
        -CollectorKeyEnvironmentVariable $config.CollectorKeyEnvironmentVariable `
        -SkipCertificateCheck $config.SkipCertificateCheck -CollectorHost $config.CollectorHost `
        -CollectorVersion $config.CollectorVersion -Offline $config.Offline `
        -OutputDirectory $config.OutputDirectory -Incremental $config.Incremental `
        -StateFile $config.StateFile -MaxAttempts $config.MaxAttempts `
        -TimeoutSeconds $config.TimeoutSeconds -PrincipalFilter $config.PrincipalFilter `
        -GroupFilter $config.GroupFilter -Job $config.Job -Passes $config.Passes `
        -SinceUsn $config.SinceUsn -CheckpointIssuer $config.CheckpointIssuer
}

$provider = if ($DirectoryFixture) {
    Write-Warning "Running against the directory fixture '$DirectoryFixture'. This is a development facility; the output describes the fixture, not a real domain."
    New-AdgFixtureDirectoryProvider -Path $DirectoryFixture
}
else {
    New-AdgLdapDirectoryProvider -Server $config.DomainController -SearchBase $config.SearchBase `
        -Port $config.Port -UseTls:$config.UseTls -PageSize $config.PageSize `
        -TimeoutSeconds $config.TimeoutSeconds
}

$publisherArguments = if ($config.Offline) {
    @{ Mode = 'Offline'; OutputDirectory = $config.OutputDirectory }
}
else {
    $token = if ($config.ApiTokenEnvironmentVariable) {
        [System.Environment]::GetEnvironmentVariable($config.ApiTokenEnvironmentVariable)
    }
    else {
        $null
    }
    # A collector key is the normal credential for an unattended run: it grants only
    # 'collectors:ingest', so a key stolen from a scheduled task cannot read the estate back
    # out. A bearer token is for an operator replaying a payload by hand.
    $collectorKey = if ($config.CollectorKeyEnvironmentVariable) {
        [System.Environment]::GetEnvironmentVariable($config.CollectorKeyEnvironmentVariable)
    }
    else {
        $null
    }
    if (-not $token -and -not $collectorKey) {
        Write-Warning "No credential found in `$env:$($config.CollectorKeyEnvironmentVariable) or `$env:$($config.ApiTokenEnvironmentVariable). The ADG API rejects anonymous ingestion and will answer 401."
    }
    @{
        Mode                 = 'Api'
        ApiBaseUrl           = $config.ApiBaseUrl
        AuthenticationToken  = $token
        CollectorKey         = $collectorKey
        MaxAttempts          = $config.MaxAttempts
        SkipCertificateCheck = [bool] $config.SkipCertificateCheck
    }
}
$publisher = New-AdgPublisher @publisherArguments

Write-Host "ADG Active Directory collector $($config.CollectorVersion) on $($config.CollectorHost)"
Write-Host "  directory : $($provider.Server) ($($provider.DefaultNamingContext))"
Write-Host "  output    : $(if ($config.Offline) { $config.OutputDirectory } else { $config.ApiBaseUrl })"

$summary = Invoke-AdgAdCollection -Config $config -Provider $provider -Publisher $publisher

Write-Host ''
Write-Host "Run $($summary.RunId) finished as '$($summary.Status)'."
Write-Host "  mode       : $($summary.Mode) ($($summary.Passes) pass)"
Write-Host "  principals : $($summary.PrincipalCount)"
Write-Host "  edges      : $($summary.EdgeCount)"
Write-Host "  batches    : $($summary.BatchCount) ($($summary.ObservationCount) observations)"
Write-Host "  errors     : $($summary.ErrorCount)"
Write-Host "  reconciled : $($summary.Reconciled)"
if ($summary.Checkpoint) {
    Write-Host "  checkpoint : uSNChanged $($summary.Checkpoint.Token) from $($summary.Checkpoint.Issuer)"
}
else {
    Write-Host '  checkpoint : none recorded; the next run reads everything'
}

if ($summary.ErrorCount -gt 0) {
    Write-Host ''
    Write-Host 'The run did not achieve complete coverage and reconciled nothing:' -ForegroundColor Yellow
    foreach ($item in $summary.Errors) {
        Write-Host "  [$($item['code'])] $($item['message'])" -ForegroundColor Yellow
    }
}

if ($PassThru) {
    [pscustomobject]@{
        Status           = $summary.Status
        RunId            = $summary.RunId
        Mode             = $summary.Mode
        StartedAt        = $summary.StartedAt
        EndedAt          = $summary.EndedAt
        ObservationCount = $summary.ObservationCount
        AffirmationCount = 0
        ErrorCount       = $summary.ErrorCount
        Reconciled       = $summary.Reconciled
        Checkpoint       = $summary.Checkpoint
        Message          = "$($summary.PrincipalCount) principal(s), $($summary.EdgeCount) edge(s)"
    }
}

# A partial run is a real result, not a crash: its observations are valid and were ingested.
# Exit 2 lets a scheduled task alert on incomplete coverage without treating it as a
# failure to run at all.
switch ($summary.Status) {
    'succeeded' { exit 0 }
    'partial' { exit 2 }
    default { exit 1 }
}
