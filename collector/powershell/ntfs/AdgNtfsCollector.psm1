<#
    ADG NTFS share-root collector.

    Reads the NTFS security descriptor of each configured share root and reports it as
    contract v1 observations. It reports readings, never conclusions: no effective access,
    no expanded membership, no inheritance resolved, no risk verdict. Those are the
    backend's job, computed from these facts and from the share layer - which is a separate
    layer entirely. Remote access is limited by the share ACL and the NTFS ACL together,
    and local access bypasses the share layer completely, so the two are never merged here.

    The module is layered so that the parts that can be wrong are the parts that can be
    tested:

        AdgNtfsObservation.ps1  raw descriptors to contract observations (pure)
        AdgNtfsConfig.ps1       which share roots to read, and what is refused
        AdgNtfsSource.ps1       the only code that touches a file system (the test seam)
        AdgNtfsScan.ps1         orchestration, retries, scopes, batching
        AdgNtfsTransport.ps1    submission, with the contract's retry rules

    Two derivations in AdgNtfsObservation.ps1 are mirrors of backend code and must not drift
    from it: the source keys (backend/app/contracts/v1/keys.py) and the ACL normal form and
    its hash (backend/app/domain/acl_hash.py). A contract test runs this collector and
    compares both against the Python implementations.

    See README.md for the privileges collection needs, and
    docs/contracts/collector-protocol.md for the normative protocol.
#>

Set-StrictMode -Version Latest

$functionRoot = Join-Path $PSScriptRoot 'functions'

# Order matters only for legibility - nothing runs at load time - but keeping the dependency
# order explicit makes a load failure tell you which layer is missing.
$files = @(
    'AdgNtfsObservation.ps1'
    'AdgNtfsConfig.ps1'
    'AdgNtfsSource.ps1'
    'AdgNtfsScan.ps1'
    'AdgNtfsTransport.ps1'
)

foreach ($file in $files) {
    $path = Join-Path $functionRoot $file
    if (-not (Test-Path -LiteralPath $path)) {
        throw "The ADG NTFS collector is incomplete: $path is missing."
    }
    . $path
}

Export-ModuleMember -Function @(
    # Configuration and filtering
    'Import-AdgNtfsTarget'
    'Test-AdgShareRootPath'

    # Acquisition (the mock seam)
    'Test-AdgResourceExists'
    'Get-AdgDirectorySecurity'
    'Resolve-AdgTrusteeName'

    # Contract
    'Get-AdgProperty'
    'Get-AdgTimestamp'
    'ConvertTo-AdgUncPath'
    'Get-AdgResourceComparisonKey'
    'Get-AdgNtfsResourceKey'
    'Get-AdgNtfsAceKey'
    'Get-AdgPrincipalKey'
    'Get-AdgAceContentLine'
    'Get-AdgNormalizedAcl'
    'Get-AdgAclHash'
    'Get-AdgSha256Hex'
    'ConvertTo-AdgAceType'
    'Test-AdgSidString'
    'New-AdgObservation'
    'New-AdgCollectorError'
    'ConvertTo-AdgUnresolvedPrincipalObservation'
    'ConvertTo-AdgNtfsAceObservation'
    'ConvertTo-AdgNtfsResourceObservation'

    # Orchestration and transport
    'Get-AdgNtfsResourceObservation'
    'Split-AdgNtfsObservationBatch'
    'Invoke-AdgNtfsScan'
    'Invoke-AdgNtfsScanRun'
    'Invoke-AdgPost'
    'Send-AdgNtfsScanRun'
)
