@{
    RootModule           = 'AdgNtfsCollector.psm1'
    ModuleVersion        = '0.1.0'
    GUID                 = '00566bb8-e1dd-4906-bcb0-b404e50fe383'
    Author               = 'ADG'
    CompanyName          = 'ADG'
    Copyright            = 'ADG'
    Description          = 'Read-only collector for the NTFS security descriptors of SMB share roots, reporting contract v1 observations.'

    # PowerShell 7 per the project baseline. The security-descriptor APIs this collector
    # uses are in the base class library and work correctly here; nothing needs Windows
    # PowerShell 5.1.
    PowerShellVersion    = '7.2'
    CompatiblePSEditions = @('Core')

    # Windows only: NTFS security descriptors are a Windows concept, and there is no
    # cross-platform substitute that preserves ACE flags and DACL order.
    # No RequiredModules: everything is read through System.Security.AccessControl and
    # System.IO, so the collector does not depend on the PowerShell provider stack.

    FunctionsToExport    = @(
        'Get-AdgNtfsSafeDefault'
        'Import-AdgNtfsTarget'
        'Test-AdgShareRootPath'
        'Test-AdgPathMatch'
        'Test-AdgPatternReachesBelow'
        'Test-AdgPathInScope'
        'Test-AdgResourceExists'
        'Get-AdgChildDirectory'
        'Get-AdgChildFile'
        'Get-AdgDirectorySecurity'
        'Get-AdgFileSecurity'
        'ConvertFrom-AdgRawSecurityDescriptor'
        'Resolve-AdgTrusteeName'
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
        'ConvertTo-AdgMappedGenericRight'
        'Get-AdgInheritedAceFlag'
        'Get-AdgInheritedAce'
        'Get-AdgInheritedAceProjection'
        'Get-AdgProjectedChildAclHash'
        'Resolve-AdgAclBoundary'
        'Invoke-AdgParallelMap'
        'New-AdgNtfsWalkMetric'
        'ConvertTo-AdgNtfsResourceGroup'
        'Read-AdgNtfsSecurity'
        'Read-AdgNtfsDirectoryUnit'
        'Read-AdgNtfsFileGroup'
        'Invoke-AdgNtfsDirectoryWalk'
        'Get-AdgScanFingerprint'
        'New-AdgNtfsCheckpoint'
        'Save-AdgNtfsCheckpoint'
        'Import-AdgNtfsCheckpoint'
        'Remove-AdgNtfsCheckpoint'
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
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @()

    PrivateData          = @{
        PSData = @{
            Tags       = @('ADG', 'NTFS', 'Audit', 'Security', 'ReadOnly')
            ProjectUri = 'https://github.com/adg'
        }
    }
}
