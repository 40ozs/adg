@{
    RootModule           = 'AdgLocalGroupCollector.psm1'
    ModuleVersion        = '0.1.0'
    GUID                 = '0b7f3c2e-6a51-4c58-9d3e-2f4a8e61c7b9'
    Author               = 'ADG'
    CompanyName          = 'ADG'
    Copyright            = 'ADG'
    Description          = 'Read-only collector for Windows hosts'' local groups and their direct members, reporting contract v1 observations.'

    # PowerShell 7 per the project baseline. Reading a host runs only on Windows (netapi32);
    # the translation and orchestration layers load and are tested anywhere.
    PowerShellVersion    = '7.2'
    CompatiblePSEditions = @('Core')

    FunctionsToExport    = @(
        'Set-AdgLocalGroupFixture'
        'Initialize-AdgLocalGroupNative'
        'ConvertTo-AdgNetApiServerName'
        'Get-AdgRemoteServerInfo'
        'Get-AdgRemoteLocalGroup'
        'Resolve-AdgRemoteAccountName'
        'Get-AdgRemoteLocalGroupMember'
        'ConvertTo-AdgLocalMemberKind'
        'Test-AdgHostLocalGroupAccount'
        'ConvertTo-AdgLocalGroupObservation'
        'Get-AdgLocalGroupHostObservation'
        'Get-AdgLocalGroupErrorCode'
        'Invoke-AdgLocalGroupHostRun'
    )
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @()

    PrivateData          = @{
        PSData = @{
            Tags       = @('ADG', 'LocalGroups', 'Audit', 'Security', 'ReadOnly')
            ProjectUri = 'https://github.com/adg'
        }
    }
}
