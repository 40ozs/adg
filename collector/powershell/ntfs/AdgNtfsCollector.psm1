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
        AdgNtfsConfig.ps1       where a walk starts, how far it goes, what it refuses
        AdgNtfsSource.ps1       the only code that touches a file system (the test seam)
        AdgNtfsCheckpoint.ps1   enough state to resume a walk, and nothing more
        AdgNtfsWalk.ps1         the traversal, its loop guards, and its metrics
        AdgNtfsScan.ps1         streaming batches, scopes, and reconciliation
        AdgNtfsTransport.ps1    submission, with the contract's retry rules

    Three derivations here are mirrors of backend code and must not drift from it: the
    source keys (backend/app/contracts/v1/keys.py), the ACL normal form and its hash
    (backend/app/domain/acl_hash.py), and the inheritance projection that decides where
    permissions change (backend/app/domain/inheritance.py). A contract test runs this
    collector and compares all three against the Python implementations.

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
    'AdgNtfsCheckpoint.ps1'
    'AdgNtfsWalk.ps1'
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
    # Configuration, filtering, and scope
    'Get-AdgNtfsSafeDefault'
    'Import-AdgNtfsTarget'
    'Test-AdgShareRootPath'
    'Test-AdgPathMatch'
    'Test-AdgPatternReachesBelow'
    'Test-AdgPathInScope'

    # Acquisition (the mock seam)
    'Test-AdgResourceExists'
    'Get-AdgChildDirectory'
    'Get-AdgChildFile'
    'Get-AdgDirectorySecurity'
    'Get-AdgFileSecurity'
    'ConvertFrom-AdgRawSecurityDescriptor'
    'Resolve-AdgTrusteeName'

    # Contract
    'Get-AdgProperty'
    'Get-AdgTimestamp'
    'ConvertTo-AdgUncPath'
    'Get-AdgResourceComparisonKey'
    'Get-AdgNtfsResourceKey'
    'Get-AdgParentPath'
    'Get-AdgDepthFromShareRoot'
    'Get-AdgNtfsAceKey'
    'Get-AdgPrincipalKey'
    'Get-AdgAceContentLine'
    'Get-AdgNormalizedAcl'
    'Get-AdgAclHash'
    'Get-AdgSha256Hex'
    'ConvertTo-AdgAceType'
    'ConvertTo-AdgAccessMask'
    'Test-AdgSidString'
    'New-AdgObservation'
    'New-AdgCollectorError'
    'ConvertTo-AdgUnresolvedPrincipalObservation'
    'ConvertTo-AdgNtfsAceObservation'
    'ConvertTo-AdgNtfsResourceObservation'

    # Inheritance and boundaries
    'ConvertTo-AdgMappedGenericRight'
    'Get-AdgInheritedAceFlag'
    'Get-AdgInheritedAce'
    'Get-AdgInheritedAceProjection'
    'Get-AdgProjectedChildAclHash'
    'Resolve-AdgAclBoundary'

    # Traversal
    'Invoke-AdgParallelMap'
    'New-AdgNtfsWalkMetric'
    'ConvertTo-AdgNtfsResourceGroup'
    'Read-AdgNtfsSecurity'
    'Read-AdgNtfsDirectoryUnit'
    'Read-AdgNtfsFileGroup'
    'Invoke-AdgNtfsDirectoryWalk'

    # Checkpointing
    'Get-AdgScanFingerprint'
    'New-AdgNtfsCheckpoint'
    'Save-AdgNtfsCheckpoint'
    'Import-AdgNtfsCheckpoint'
    'Remove-AdgNtfsCheckpoint'

    # Orchestration and transport
    'New-AdgNtfsBatchWriter'
    'Add-AdgNtfsObservationGroup'
    'Send-AdgNtfsPendingBatch'
    'Complete-AdgNtfsBatchWriter'
    'Test-AdgNtfsFullEnumerationIntent'
    'Invoke-AdgNtfsScan'
    'Invoke-AdgNtfsScanRun'
    'Invoke-AdgPost'
    'New-AdgNtfsTransport'
    'Send-AdgNtfsStart'
    'Send-AdgNtfsBatch'
    'Send-AdgNtfsCompletion'
    'New-AdgNtfsFileSink'
    'Write-AdgNtfsPayload'
)
