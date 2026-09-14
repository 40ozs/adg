@{
    RootModule           = 'AdgSmbCollector.psm1'
    ModuleVersion        = '0.1.0'
    GUID                 = '46dc1288-1516-4a52-b283-41b25f2bf393'
    Author               = 'ADG'
    CompanyName          = 'ADG'
    Copyright            = 'ADG'
    Description          = 'Read-only collector for Windows SMB shares and raw share-level ACLs, reporting contract v1 observations.'

    # PowerShell 7 per the project baseline. The SMB cmdlets and CIM work correctly here;
    # nothing in this collector needs Windows PowerShell 5.1.
    PowerShellVersion    = '7.2'
    CompatiblePSEditions = @('Core')

    # Windows only: SmbShare and CIM are Windows APIs, and there is no cross-platform
    # substitute that reads a Windows share security descriptor.
    RequiredModules      = @(
        @{ ModuleName = 'CimCmdlets'; ModuleVersion = '7.0.0.0' }
        @{ ModuleName = 'SmbShare'; ModuleVersion = '2.0.0.0' }
    )

    FunctionsToExport    = @(
        'Import-AdgSmbTarget'
        'Test-AdgShareIncluded'
        'Test-AdgAdminShareName'
        'Test-AdgHiddenShareName'
        'New-AdgSmbSession'
        'Remove-AdgSmbSession'
        'Get-AdgRemoteComputerFact'
        'Get-AdgRemoteShare'
        'Get-AdgRemoteShareSecurity'
        'Get-AdgRemoteShareAccess'
        'Resolve-AdgTrusteeSid'
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
        'Get-AdgSmbServerObservation'
        'Read-AdgShareAcl'
        'Split-AdgObservationBatch'
        'Invoke-AdgSmbScan'
        'Invoke-AdgSmbScanRun'
        'Invoke-AdgPost'
        'Send-AdgSmbScanRun'
    )
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @()

    PrivateData          = @{
        PSData = @{
            Tags       = @('ADG', 'SMB', 'Audit', 'Security', 'ReadOnly')
            ProjectUri = 'https://github.com/adg'
        }
    }
}
