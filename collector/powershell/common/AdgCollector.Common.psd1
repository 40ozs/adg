@{
    RootModule           = 'AdgCollector.Common.psm1'
    ModuleVersion        = '0.1.0'
    GUID                 = '6c5989ef-201c-4e59-a8e6-d00dc82da586'
    Author               = 'ADG'
    CompanyName          = 'ADG'
    Copyright            = 'Internal project. All rights reserved.'
    Description          = 'Contract v1 primitives shared by every ADG collector: payload construction, source-key derivation, batching, retrying transport, and an offline sink.'
    PowerShellVersion    = '7.0'
    CompatiblePSEditions = @('Core')
    FunctionsToExport    = @(
        'Get-AdgSchemaVersion'
        'Get-AdgMaxBatchSize'
        'Get-AdgTimestamp'
        'New-AdgIdentifier'
        'Assert-AdgUuid'
        'Assert-AdgSid'
        'Assert-AdgHostName'
        'Get-AdgPrincipalSourceKey'
        'Get-AdgMembershipSourceKey'
        'New-AdgObservation'
        'New-AdgPrincipalObservation'
        'New-AdgMembershipObservation'
        'New-AdgScope'
        'New-AdgScanRunStart'
        'New-AdgObservationBatch'
        'New-AdgCollectorError'
        'New-AdgScanRunCompletion'
        'New-AdgPublisher'
        'Get-AdgHttpStatusCode'
        'Test-AdgRetryableStatus'
        'Invoke-AdgApiRequest'
        'Publish-AdgPayload'
    )
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @()
    PrivateData          = @{
        PSData = @{
            Tags       = @('ADG', 'Collector', 'Contracts')
            ProjectUri = 'https://adg.local'
        }
    }
}
