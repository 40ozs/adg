#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    The credential a streaming scan carries.

    Since Phase 6A the ADG ingestion endpoints reject an anonymous request. A tree walk runs
    for hours and posts continuously, so the credential is built once and lives on the
    transport rather than being resolved per POST -- otherwise a retry three hours in could
    send something different from the attempt it is retrying.

    The warning matters as much as the header. A run that silently posts nothing usable
    fails with a 401 per batch and looks, from the outside, like a network problem.
#>

BeforeAll {
    $moduleRoot = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $moduleRoot 'AdgNtfsCollector.psd1') -Force
}

Describe 'New-AdgNtfsTransport' {
    It 'carries a collector key in the header the API reads it from' {
        $transport = New-AdgNtfsTransport -ApiBaseUrl 'http://localhost:8000' -CollectorKey 'a-key'

        $transport.Headers['X-ADG-Collector-Key'] | Should -Be 'a-key'
        $transport.Headers.ContainsKey('Authorization') | Should -BeFalse
    }

    It 'carries a bearer token when an operator supplies one instead' {
        $transport = New-AdgNtfsTransport -ApiBaseUrl 'http://localhost:8000' -AuthenticationToken 'a-token'

        $transport.Headers['Authorization'] | Should -Be 'Bearer a-token'
    }

    It 'carries both when both are supplied' {
        # The API checks the key first; sending both is not an error, and refusing it here
        # would only make a scheduled task harder to configure during a credential rollover.
        $transport = New-AdgNtfsTransport -ApiBaseUrl 'http://localhost:8000' `
            -CollectorKey 'a-key' -AuthenticationToken 'a-token'

        $transport.Headers.Count | Should -Be 2
    }

    It 'warns when a run would be submitted with no credential at all' {
        $warnings = @()
        New-AdgNtfsTransport -ApiBaseUrl 'http://localhost:8000' `
            -WarningVariable warnings -WarningAction SilentlyContinue | Out-Null

        ($warnings -join ' ') | Should -BeLike '*rejects anonymous ingestion*'
    }

    It 'still trims the base URL and starts with no run' {
        $transport = New-AdgNtfsTransport -ApiBaseUrl 'http://localhost:8000/' -CollectorKey 'k'

        $transport.BaseUrl | Should -Be 'http://localhost:8000'
        $transport.RunId | Should -BeNullOrEmpty
        $transport.BatchesSent | Should -Be 0
    }
}
