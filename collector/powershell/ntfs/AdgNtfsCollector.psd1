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
        'Import-AdgNtfsTarget'
        'Test-AdgShareRootPath'
        'Test-AdgResourceExists'
        'Get-AdgDirectorySecurity'
        'Resolve-AdgTrusteeName'
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
        'Get-AdgNtfsResourceObservation'
        'Split-AdgNtfsObservationBatch'
        'Invoke-AdgNtfsScan'
        'Invoke-AdgNtfsScanRun'
        'Invoke-AdgPost'
        'Send-AdgNtfsScanRun'
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
