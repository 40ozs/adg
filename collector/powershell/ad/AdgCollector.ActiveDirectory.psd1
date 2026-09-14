@{
    RootModule           = 'AdgCollector.ActiveDirectory.psm1'
    ModuleVersion        = '0.1.0'
    GUID                 = '3a8e847a-2b73-422c-bd9d-7ba3740eb40d'
    Author               = 'ADG'
    CompanyName          = 'ADG'
    Copyright            = 'Internal project. All rights reserved.'
    Description          = 'Read-only Active Directory collector for ADG: users, groups, computers, and direct membership edges. Nested groups are reported as edges and never flattened.'
    PowerShellVersion    = '7.0'
    CompatiblePSEditions = @('Core')
    FunctionsToExport    = @(
        'ConvertTo-AdgNormalizedDistinguishedName'
        'Test-AdgDistinguishedNameUnder'
        'Get-AdgForeignSecurityPrincipalSid'
        'New-AdgDirectoryEntry'
        'Get-AdgEntryValues'
        'Get-AdgEntryValue'
        'Get-AdgEntryInteger'
        'Get-AdgEntryBoolean'
        'Get-AdgPrincipalKindFromEntry'
        'Get-AdgGroupScopeFromType'
        'Get-AdgGroupTypeFromType'
        'Get-AdgDomainSidFromSid'
        'Get-AdgSidWithRid'
        'Test-AdgAccountEnabled'
        'ConvertTo-AdgPrincipalObservationFromEntry'
        'Get-AdgEntryChangeMetadata'
        'Test-AdgTransientDirectoryFailure'
        'Invoke-AdgDirectoryOperation'
        'Invoke-AdgDirectorySearch'
        'New-AdgFixtureDirectoryProvider'
        'New-AdgLdapDirectoryProvider'
        'Test-AdgFixtureFilterMatch'
        'Split-AdgFilterClause'
        'Select-AdgFixtureAttributes'
        'ConvertFrom-AdgLdapEntry'
        'Get-AdgLdapRootDse'
        'Get-AdgRangedAttributeState'
        'Get-AdgGroupMemberReference'
        'New-AdgAdCollectorConfig'
        'Import-AdgAdCollectorConfig'
        'New-AdgCollectionState'
        'Add-AdgCollectionError'
        'Add-AdgCollectedObservation'
        'Publish-AdgBufferedBatch'
        'Get-AdgSearchBase'
        'Test-AdgEntryExcluded'
        'Invoke-AdgAdPrincipalPass'
        'Invoke-AdgAdMembershipPass'
        'Resolve-AdgMemberReference'
        'Add-AdgGroupMemberEdge'
        'Invoke-AdgAdCollection'
        'Get-AdgDomainSidFromProvider'
        'Save-AdgAdCollectorState'
        'Test-AdgAdCollectorStateUsable'
    )
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @()
    PrivateData          = @{
        PSData = @{
            Tags       = @('ADG', 'Collector', 'ActiveDirectory')
            ProjectUri = 'https://adg.local'
        }
    }
}
