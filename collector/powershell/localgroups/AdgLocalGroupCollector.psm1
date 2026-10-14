<#
    ADG local-groups collector.

    Reads the local groups of named Windows hosts - BUILTIN\Administrators, Remote Desktop
    Users, Remote Management Users and every other group in the host's own account database -
    and their direct members, and reports them as contract v1 observations: `local_group`
    principals and `local_group_member` edges, scoped to the host they were read on.

    It reports readings, never conclusions. Whether a member can sign in over Remote Desktop
    or administer the host is derived by the backend (ADG-GRAPH-01B), which says what it
    could not evaluate.

    Layered so that the parts that can be wrong are the parts that can be tested:

        AdgLocalGroupSource.ps1       the only code that touches a host (the test seam)
        AdgLocalGroupObservation.ps1  readings to contract observations (pure)
        AdgLocalGroupScan.ps1         one host, one run: scopes, batching, completion

    Payload construction and transport are the shared contract module's.

    ADG is read-only (ADR-0004). See README.md for the rights collection needs.
#>

Set-StrictMode -Version Latest

$moduleRoot = $PSScriptRoot
Import-Module (Join-Path (Split-Path -Parent $moduleRoot) 'common/AdgCollector.Common.psd1') -ErrorAction Stop

$functionRoot = Join-Path $moduleRoot 'functions'
$files = @(
    'AdgLocalGroupSource.ps1'
    'AdgLocalGroupObservation.ps1'
    'AdgLocalGroupScan.ps1'
)
foreach ($file in $files) {
    $path = Join-Path $functionRoot $file
    if (-not (Test-Path -LiteralPath $path)) {
        throw "The ADG local-groups collector is incomplete: $path is missing."
    }
    . $path
}

Export-ModuleMember -Function @(
    # Acquisition (the mock seam)
    'Set-AdgLocalGroupFixture'
    'Initialize-AdgLocalGroupNative'
    'ConvertTo-AdgNetApiServerName'
    'Get-AdgRemoteServerInfo'
    'Get-AdgRemoteLocalGroup'
    'Resolve-AdgRemoteAccountName'
    'Get-AdgRemoteLocalGroupMember'

    # Translation
    'ConvertTo-AdgLocalMemberKind'
    'Test-AdgHostLocalGroupAccount'
    'ConvertTo-AdgLocalGroupObservation'

    # Orchestration
    'Get-AdgLocalGroupHostObservation'
    'Get-AdgLocalGroupErrorCode'
    'Invoke-AdgLocalGroupHostRun'
)
