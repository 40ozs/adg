<#
    ADG SMB share collector.

    Discovers Windows file servers' shares and raw share-level ACLs, and reports them as
    contract v1 observations. It reports readings, never conclusions: no effective access,
    no expanded membership, no risk verdict. Those are the backend's job, computed from
    these facts and from the NTFS layer, which is a separate layer entirely - remote
    access is limited by both share and NTFS permissions, and local access bypasses the
    share layer completely.

    The module is layered so that the parts that can be wrong are the parts that can be
    tested:

        AdgSmbConfig.ps1       what to ask for, and which shares to record
        AdgSmbSource.ps1       the only code that touches a remote host (the test seam)
        AdgSmbObservation.ps1  raw readings to contract observations (pure)
        AdgSmbScan.ps1         orchestration, retries, scopes, batching
        AdgSmbTransport.ps1    submission, with the contract's retry rules

    See README.md for the privileges and firewall rules collection needs, and
    docs/contracts/collector-protocol.md for the normative protocol.
#>

Set-StrictMode -Version Latest

$functionRoot = Join-Path $PSScriptRoot 'functions'

# Order matters: AdgSmbObservation defines helpers the others call at load time only
# indirectly, but keeping the dependency order explicit makes a load failure legible.
$files = @(
    'AdgSmbObservation.ps1'
    'AdgSmbConfig.ps1'
    'AdgSmbSource.ps1'
    'AdgSmbScan.ps1'
    'AdgSmbTransport.ps1'
)

foreach ($file in $files) {
    $path = Join-Path $functionRoot $file
    if (-not (Test-Path -LiteralPath $path)) {
        throw "The ADG SMB collector is incomplete: $path is missing."
    }
    . $path
}

Export-ModuleMember -Function @(
    # Configuration and filtering
    'Import-AdgSmbTarget'
    'Test-AdgShareIncluded'
    'Test-AdgAdminShareName'
    'Test-AdgHiddenShareName'

    # Acquisition (the mock seam)
    'New-AdgSmbSession'
    'Remove-AdgSmbSession'
    'Get-AdgRemoteComputerFact'
    'Get-AdgRemoteShare'
    'Get-AdgRemoteShareSecurity'
    'Get-AdgRemoteShareAccess'
    'Resolve-AdgTrusteeSid'

    # Contract
    'Get-AdgProperty'
    'Get-AdgTimestamp'
    'Get-AdgServerKey'
    'Get-AdgShareKey'
    'Get-AdgSmbAceKey'
    'Get-AdgPrincipalKey'
    'Get-AdgShareUncPath'
    'New-AdgObservation'
    'New-AdgCollectorError'
    'ConvertTo-AdgShareType'
    'ConvertTo-AdgSharePermission'
    'ConvertTo-AdgAceType'
    'Test-AdgSidString'
    'ConvertTo-AdgServerObservation'
    'ConvertTo-AdgShareObservation'
    'ConvertTo-AdgShareAceObservation'
    'ConvertTo-AdgShareAccessObservation'
    'ConvertTo-AdgUnresolvedPrincipalObservation'

    # Orchestration and transport
    'Get-AdgSmbServerObservation'
    'Read-AdgShareAcl'
    'Split-AdgObservationBatch'
    'Invoke-AdgSmbScan'
    'Invoke-AdgSmbScanRun'
    'Invoke-AdgPost'
    'Send-AdgSmbScanRun'
)
