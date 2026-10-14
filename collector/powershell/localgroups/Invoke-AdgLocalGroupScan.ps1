#Requires -Version 7.2
<#
.SYNOPSIS
    Read Windows hosts' local groups and their direct members, one ADG run per host.

.DESCRIPTION
    For each named host: every local group in its own account database (BUILTIN
    Administrators, Remote Desktop Users, Remote Management Users and the rest), and each
    group's direct members by SID, reported as contract v1 `local_group` principals and
    `local_group_member` edges scoped to that host. A host read completely reconciles its
    `local_groups_host` scope, so a member removed since the last run is recorded as removed;
    a host read with any error reconciles nothing.

    Read-only (ADR-0004): NetServerGetInfo, NetLocalGroupEnum, NetLocalGroupGetMembers and
    LookupAccountName, nothing else. Domain controllers are refused: they have no local
    account database, and their groups are the directory collector's to report.

    Name each host exactly as the SMB and NTFS collectors address it. The host name is part of
    every local group's identity, and `BUILTIN\Administrators` read here joins the
    `BUILTIN\Administrators` an NTFS entry on that host names only when the two spellings agree.

.PARAMETER ComputerName
    The hosts to read. Explicit targets only: there is no domain-wide sweep.

.PARAMETER ApiBaseUrl
    Base URL of the ADG API, for example https://adg.corp.example.com.

.PARAMETER Offline
    Write each host's payloads under -OutputDirectory\<host> instead of sending them. The files
    are the exact bytes that would have been POSTed.

.PARAMETER OutputDirectory
    Where -Offline writes.

.PARAMETER CollectorKeyVariable
    The environment variable holding the collector key. No secret is ever a parameter value.

.PARAMETER HostFixture
    Development and test only: read hosts from a JSON fixture instead of the network. This is
    how the test suites exercise the collector without a Windows host; it is never a way to
    scan a real environment.

.EXAMPLE
    .\Invoke-AdgLocalGroupScan.ps1 -ComputerName FS01, FS02 -ApiBaseUrl https://adg.corp.example.com

.EXAMPLE
    .\Invoke-AdgLocalGroupScan.ps1 -ComputerName FS01 -Offline -OutputDirectory C:\adg\out
#>
[CmdletBinding(DefaultParameterSetName = 'Api')]
param(
    [Parameter(Mandatory)][string[]] $ComputerName,
    [Parameter(Mandatory, ParameterSetName = 'Api')][string] $ApiBaseUrl,
    [Parameter(Mandatory, ParameterSetName = 'Offline')][switch] $Offline,
    [Parameter(Mandatory, ParameterSetName = 'Offline')][string] $OutputDirectory,
    [string] $CollectorKeyVariable = 'ADG_COLLECTOR_KEY',
    [string] $HostFixture
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptRoot = $PSScriptRoot
Import-Module (Join-Path $scriptRoot 'AdgLocalGroupCollector.psd1') -Force
Import-Module (Join-Path (Split-Path -Parent $scriptRoot) 'common/AdgCollector.Common.psd1') -Force

if ($HostFixture) { Set-AdgLocalGroupFixture -Path $HostFixture }

$collectorKey = [System.Environment]::GetEnvironmentVariable($CollectorKeyVariable)
$summaries = foreach ($name in $ComputerName) {
    $publisher = if ($Offline) {
        New-AdgPublisher -Mode Offline -OutputDirectory (Join-Path $OutputDirectory $name.ToLowerInvariant())
    }
    else {
        New-AdgPublisher -Mode Api -ApiBaseUrl $ApiBaseUrl -CollectorKey $collectorKey
    }
    Invoke-AdgLocalGroupHostRun -ComputerName $name -Publisher $publisher
}

$summaries | Format-Table ComputerName, Status, Reconciled, Observations, Errors -AutoSize | Out-Host
$incomplete = @($summaries | Where-Object { -not $_.Reconciled })
# 2 mirrors the operator commands: it worked, and something needs a person.
if ($incomplete.Count -gt 0) { exit 2 }
exit 0
