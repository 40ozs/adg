#Requires -Version 7.0
<#
    Contract primitives shared by every collector.

    These tests are about the rules that keep an ADG answer honest: a source key that two
    implementations agree on, a batch that is rejected rather than truncated, and a run
    that cannot claim complete coverage it did not achieve.
#>

BeforeAll {
    $repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
    Import-Module (Join-Path $repoRoot 'collector/powershell/common/AdgCollector.Common.psd1') -Force

    $script:RunId = '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31'
    $script:DomainSid = 'S-1-5-21-1004336348-1177238915-682003330'
    $script:Alice = "$script:DomainSid-1104"
    $script:FinanceTeam = "$script:DomainSid-1201"
}

Describe 'Source keys' {
    Context 'Principals' {
        It 'keys a domain principal by SID alone' {
            Get-AdgPrincipalSourceKey -Sid $script:Alice -PrincipalKind 'user' |
                Should -Be "principal|$script:Alice"
        }

        It 'scopes a local group by host, because BUILTIN SIDs repeat on every computer' {
            Get-AdgPrincipalSourceKey -Sid 'S-1-5-32-544' -PrincipalKind 'local_group' -HostKey 'FS01' |
                Should -Be 'principal|fs01|S-1-5-32-544'
        }

        It 'gives the same local group on two hosts two different keys' {
            $one = Get-AdgPrincipalSourceKey -Sid 'S-1-5-32-544' -PrincipalKind 'local_group' -HostKey 'FS01'
            $two = Get-AdgPrincipalSourceKey -Sid 'S-1-5-32-544' -PrincipalKind 'local_group' -HostKey 'FS02'
            $one | Should -Not -Be $two
        }

        It 'refuses a local group with no host rather than merging unrelated servers' {
            { Get-AdgPrincipalSourceKey -Sid 'S-1-5-32-544' -PrincipalKind 'local_group' } |
                Should -Throw '*identical on every Windows computer*'
        }

        It 'refuses a name where a SID belongs' {
            { Get-AdgPrincipalSourceKey -Sid 'CORP\asmith' -PrincipalKind 'user' } |
                Should -Throw '*canonical string-form SID*'
        }
    }

    Context 'Membership edges' {
        It 'keys a directory edge by the two SIDs and the kind' {
            Get-AdgMembershipSourceKey -GroupSid $script:FinanceTeam -MemberSid $script:Alice -EdgeKind 'directory_group_member' |
                Should -Be "edge|$script:FinanceTeam->$script:Alice|directory_group_member"
        }

        It 'distinguishes a primary-group edge from a member edge between the same pair' {
            $member = Get-AdgMembershipSourceKey -GroupSid $script:FinanceTeam -MemberSid $script:Alice -EdgeKind 'directory_group_member'
            $primary = Get-AdgMembershipSourceKey -GroupSid $script:FinanceTeam -MemberSid $script:Alice -EdgeKind 'primary_group'
            $member | Should -Not -Be $primary
        }

        It 'host-scopes the group of a local edge' {
            Get-AdgMembershipSourceKey -GroupSid 'S-1-5-32-544' -MemberSid $script:FinanceTeam -EdgeKind 'local_group_member' -HostKey 'FS01' |
                Should -Be "edge|fs01|S-1-5-32-544->$script:FinanceTeam|local_group_member"
        }

        It 'leaves a domain member of a local group unscoped, because it is not local to that host' {
            $key = Get-AdgMembershipSourceKey -GroupSid 'S-1-5-32-544' -MemberSid $script:FinanceTeam -EdgeKind 'local_group_member' -HostKey 'FS01'
            $key | Should -Not -Match 'fs01\|S-1-5-21'
        }

        It 'host-scopes a BUILTIN member of a local group' {
            Get-AdgMembershipSourceKey -GroupSid 'S-1-5-32-544' -MemberSid 'S-1-5-32-545' -EdgeKind 'local_group_member' -HostKey 'FS01' |
                Should -Be 'edge|fs01|S-1-5-32-544->fs01|S-1-5-32-545|local_group_member'
        }
    }
}

Describe 'Principal observations' {
    It 'carries the five fields every observation carries' {
        $observation = New-AdgPrincipalObservation -RunId $script:RunId -Sid $script:Alice -PrincipalKind 'user'

        $observation['schema_version'] | Should -Be '1.0'
        $observation['kind'] | Should -Be 'principal'
        $observation['run_id'] | Should -Be $script:RunId
        $observation['observed_at'] | Should -Match '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$'
        $observation['source_key'] | Should -Be "principal|$script:Alice"
    }

    It 'omits a field the source did not fill in rather than sending an empty one' {
        $observation = New-AdgPrincipalObservation -RunId $script:RunId -Sid $script:Alice -PrincipalKind 'user'
        $observation.Contains('display_name') | Should -BeFalse
        $observation.Contains('user_principal_name') | Should -BeFalse
    }

    It 'keeps enabled:false, which is a fact and not an absent value' {
        $observation = New-AdgPrincipalObservation -RunId $script:RunId -Sid $script:Alice -PrincipalKind 'user' -Enabled $false
        $observation.Contains('enabled') | Should -BeTrue
        $observation['enabled'] | Should -BeFalse
    }

    It 'refuses a display name on an unresolved principal' {
        { New-AdgPrincipalObservation -RunId $script:RunId -Sid $script:Alice -PrincipalKind 'unresolved' -DisplayName 'Alice Smith' } |
            Should -Throw '*must not carry display_name*'
    }

    It 'accepts a last known name on an unresolved principal' {
        $observation = New-AdgPrincipalObservation -RunId $script:RunId -Sid $script:Alice `
            -PrincipalKind 'unresolved' -UnresolvedReason 'deleted' -LastKnownName 'Alice Smith'
        $observation['last_known_name'] | Should -Be 'Alice Smith'
        $observation['unresolved_reason'] | Should -Be 'deleted'
    }

    It 'refuses a principal kind the contract does not define' {
        { New-AdgPrincipalObservation -RunId $script:RunId -Sid $script:Alice -PrincipalKind 'service' } |
            Should -Throw '*is not in the contract*'
    }
}

Describe 'Membership observations' {
    It 'refuses a self-edge' {
        { New-AdgMembershipObservation -RunId $script:RunId -GroupSid $script:FinanceTeam `
                -MemberSid $script:FinanceTeam -EdgeKind 'directory_group_member' } |
            Should -Throw '*cannot be a direct member of itself*'
    }

    It 'requires a host on a local-group edge' {
        { New-AdgMembershipObservation -RunId $script:RunId -GroupSid 'S-1-5-32-544' `
                -MemberSid $script:Alice -EdgeKind 'local_group_member' } |
            Should -Throw '*host name*'
    }

    It 'refuses a host on a directory edge, where it would mean nothing' {
        { New-AdgMembershipObservation -RunId $script:RunId -GroupSid $script:FinanceTeam `
                -MemberSid $script:Alice -EdgeKind 'directory_group_member' -HostKey 'FS01' } |
            Should -Throw '*applies only to local_group_member*'
    }

    It 'records a foreign security principal as one' {
        $observation = New-AdgMembershipObservation -RunId $script:RunId -GroupSid $script:FinanceTeam `
            -MemberSid 'S-1-5-21-3623811015-3361044348-30300820-1234' -EdgeKind 'directory_group_member' `
            -MemberKind 'foreign_security_principal' -IsForeignSecurityPrincipal $true
        $observation['is_foreign_security_principal'] | Should -BeTrue
    }
}

Describe 'Batching' {
    BeforeAll {
        $script:MakeObservations = {
            param([int] $Count)
            1..$Count | ForEach-Object {
                New-AdgPrincipalObservation -RunId $script:RunId -Sid "$script:DomainSid-$(2000 + $_)" -PrincipalKind 'user'
            }
        }
    }

    It 'rejects an oversized batch instead of truncating it' {
        $observations = & $script:MakeObservations 1001
        { New-AdgObservationBatch -RunId $script:RunId -BatchId (New-AdgIdentifier) -Sequence 1 -Observations $observations } |
            Should -Throw '*at most 1000 observations*'
    }

    It 'accepts a batch at exactly the limit' {
        $observations = & $script:MakeObservations 1000
        $batch = New-AdgObservationBatch -RunId $script:RunId -BatchId (New-AdgIdentifier) -Sequence 1 -Observations $observations
        $batch['observations'].Count | Should -Be 1000
    }

    It 'rejects a duplicated source key, which would silently overwrite its twin' {
        $one = New-AdgPrincipalObservation -RunId $script:RunId -Sid $script:Alice -PrincipalKind 'user'
        $two = New-AdgPrincipalObservation -RunId $script:RunId -Sid $script:Alice -PrincipalKind 'user' -DisplayName 'Alice'
        { New-AdgObservationBatch -RunId $script:RunId -BatchId (New-AdgIdentifier) -Sequence 1 -Observations @($one, $two) } |
            Should -Throw '*same source_key twice*'
    }

    It 'rejects an observation belonging to another run' {
        $mine = New-AdgPrincipalObservation -RunId $script:RunId -Sid $script:Alice -PrincipalKind 'user'
        $theirs = New-AdgPrincipalObservation -RunId (New-AdgIdentifier) -Sid "$script:DomainSid-1105" -PrincipalKind 'user'
        { New-AdgObservationBatch -RunId $script:RunId -BatchId (New-AdgIdentifier) -Sequence 1 -Observations @($mine, $theirs) } |
            Should -Throw "*must carry the batch's run_id*"
    }

    It 'rejects an empty batch, which cannot be told apart from a lost one' {
        { New-AdgObservationBatch -RunId $script:RunId -BatchId (New-AdgIdentifier) -Sequence 1 -Observations @() } |
            Should -Throw '*at least one observation*'
    }
}

Describe 'Completion' {
    BeforeAll {
        $script:Scope = New-AdgScope -Kind 'domain' -Key 'S-1-5-21-1004336348-1177238915-682003330'
        $script:AnError = New-AdgCollectorError -Code 'access_denied' -Message 'Could not read OU=Secret.' -Target 'OU=Secret,DC=corp'
    }

    It 'refuses to call a run with errors a success' {
        { New-AdgScanRunCompletion -RunId $script:RunId -Status 'succeeded' -CompletedAt (Get-AdgTimestamp) `
                -BatchCount 1 -ObservationCount 10 -Errors @($script:AnError) } |
            Should -Throw "*never 'succeeded'*"
    }

    It 'refuses to let a partial run reconcile a scope' {
        { New-AdgScanRunCompletion -RunId $script:RunId -Status 'partial' -CompletedAt (Get-AdgTimestamp) `
                -BatchCount 1 -ObservationCount 10 -Errors @($script:AnError) -ReconciledScopes @($script:Scope) } |
            Should -Throw '*must not reconcile any scope*'
    }

    It 'refuses to let a clean but failed run reconcile' {
        { New-AdgScanRunCompletion -RunId $script:RunId -Status 'failed' -CompletedAt (Get-AdgTimestamp) `
                -BatchCount 0 -ObservationCount 0 -ReconciledScopes @($script:Scope) } |
            Should -Throw '*must not reconcile any scope*'
    }

    It 'lets a clean successful run reconcile' {
        $completion = New-AdgScanRunCompletion -RunId $script:RunId -Status 'succeeded' -CompletedAt (Get-AdgTimestamp) `
            -BatchCount 2 -ObservationCount 42 -ReconciledScopes @($script:Scope)
        $completion['reconciled_scopes'].Count | Should -Be 1
        $completion['error_count'] | Should -Be 0
    }

    It 'derives error_count from the errors it was given, so it cannot understate them' {
        $completion = New-AdgScanRunCompletion -RunId $script:RunId -Status 'partial' -CompletedAt (Get-AdgTimestamp) `
            -BatchCount 1 -ObservationCount 10 -Errors @($script:AnError, $script:AnError)
        $completion['error_count'] | Should -Be 2
    }
}

Describe 'Scopes' {
    It 'case-folds the key, because scope keys are compared and never displayed' {
        (New-AdgScope -Kind 'server' -Key 'FS01')['key'] | Should -Be 'fs01'
    }

    It 'refuses a scope kind outside the contract' {
        { New-AdgScope -Kind 'organizational_unit' -Key 'OU=Staff,DC=corp' } |
            Should -Throw '*is not in the contract*'
    }
}

Describe 'Retry classification' {
    It 'retries a transport failure, where no status came back' {
        Test-AdgRetryableStatus -StatusCode 0 | Should -BeTrue
    }

    It 'retries throttling and server errors' {
        Test-AdgRetryableStatus -StatusCode 429 | Should -BeTrue
        Test-AdgRetryableStatus -StatusCode 503 | Should -BeTrue
        Test-AdgRetryableStatus -StatusCode 500 | Should -BeTrue
    }

    It 'does not retry a 422, which would fail identically' {
        Test-AdgRetryableStatus -StatusCode 422 | Should -BeFalse
    }

    It 'does not retry a 409, which means the run is already closed' {
        Test-AdgRetryableStatus -StatusCode 409 | Should -BeFalse
    }
}

Describe 'Transport' {
    BeforeAll {
        $script:Payload = [ordered]@{ run_id = $script:RunId; hello = 'world' }
    }

    AfterEach {
        Remove-Variable -Name AdgTestCalls, AdgTestBodies -Scope Global -ErrorAction SilentlyContinue
    }

    It 'reuses the identical body on retry, which is what makes the retry idempotent' {
        $publisher = New-AdgPublisher -Mode Api -ApiBaseUrl 'http://localhost:8000' -MaxAttempts 3
        $global:AdgTestCalls = 0
        $global:AdgTestBodies = [System.Collections.Generic.List[string]]::new()

        Mock -CommandName Start-Sleep -ModuleName AdgCollector.Common -MockWith { }
        Mock -CommandName Invoke-RestMethod -ModuleName AdgCollector.Common -MockWith {
            $global:AdgTestCalls++
            $global:AdgTestBodies.Add($Body)
            if ($global:AdgTestCalls -lt 3) {
                throw [System.Net.Http.HttpRequestException]::new('connection reset')
            }
            return @{ applied = 1 }
        }

        $response = Invoke-AdgApiRequest -Publisher $publisher -Uri 'http://localhost:8000/api/v1/scan-runs' -Payload $script:Payload

        $response.applied | Should -Be 1
        $global:AdgTestCalls | Should -Be 3
        @($global:AdgTestBodies | Select-Object -Unique).Count | Should -Be 1
    }

    It 'stops immediately on a rejection retrying cannot fix' {
        $publisher = New-AdgPublisher -Mode Api -ApiBaseUrl 'http://localhost:8000' -MaxAttempts 5
        $global:AdgTestCalls = 0

        Mock -CommandName Start-Sleep -ModuleName AdgCollector.Common -MockWith { }
        Mock -CommandName Get-AdgHttpStatusCode -ModuleName AdgCollector.Common -MockWith { 422 }
        Mock -CommandName Invoke-RestMethod -ModuleName AdgCollector.Common -MockWith {
            $global:AdgTestCalls++
            throw [System.InvalidOperationException]::new('unprocessable')
        }

        { Invoke-AdgApiRequest -Publisher $publisher -Uri 'http://localhost:8000/api/v1/scan-runs' -Payload $script:Payload } |
            Should -Throw '*retrying cannot help*'
        $global:AdgTestCalls | Should -Be 1
    }

    It 'gives up after MaxAttempts rather than retrying forever' {
        $publisher = New-AdgPublisher -Mode Api -ApiBaseUrl 'http://localhost:8000' -MaxAttempts 4
        $global:AdgTestCalls = 0

        Mock -CommandName Start-Sleep -ModuleName AdgCollector.Common -MockWith { }
        Mock -CommandName Invoke-RestMethod -ModuleName AdgCollector.Common -MockWith {
            $global:AdgTestCalls++
            throw [System.Net.Http.HttpRequestException]::new('timeout')
        }

        { Invoke-AdgApiRequest -Publisher $publisher -Uri 'http://localhost:8000/api/v1/scan-runs' -Payload $script:Payload } |
            Should -Throw '*failed after 4 attempt*'
        $global:AdgTestCalls | Should -Be 4
    }
}

Describe 'Offline publishing' {
    It 'writes the payloads it would have sent, plus one NDJSON line each in send order' {
        $directory = Join-Path $TestDrive 'offline'
        $publisher = New-AdgPublisher -Mode Offline -OutputDirectory $directory

        $start = New-AdgScanRunStart -RunId $script:RunId -Collector 'active_directory' -CollectorHost 'COLLECTOR01' `
            -Method 'test' -StartedAt (Get-AdgTimestamp) -Scopes @((New-AdgScope -Kind 'domain' -Key $script:DomainSid))
        $observation = New-AdgPrincipalObservation -RunId $script:RunId -Sid $script:Alice -PrincipalKind 'user'
        $batch = New-AdgObservationBatch -RunId $script:RunId -BatchId (New-AdgIdentifier) -Sequence 1 `
            -Observations @($observation) -IsFinal $true
        $completion = New-AdgScanRunCompletion -RunId $script:RunId -Status 'succeeded' -CompletedAt (Get-AdgTimestamp) `
            -BatchCount 1 -ObservationCount 1

        Publish-AdgPayload -Publisher $publisher -PayloadKind 'start' -Payload $start | Out-Null
        Publish-AdgPayload -Publisher $publisher -PayloadKind 'batch' -Payload $batch | Out-Null
        Publish-AdgPayload -Publisher $publisher -PayloadKind 'completion' -Payload $completion | Out-Null

        Test-Path (Join-Path $directory 'start.json') | Should -BeTrue
        Test-Path (Join-Path $directory 'batch-001.json') | Should -BeTrue
        Test-Path (Join-Path $directory 'completion.json') | Should -BeTrue

        $lines = Get-Content -LiteralPath (Join-Path $directory 'envelopes.ndjson')
        $lines.Count | Should -Be 3
        ($lines[0] | ConvertFrom-Json).scopes.Count | Should -Be 1
        ($lines[1] | ConvertFrom-Json).batch_id | Should -Not -BeNullOrEmpty
        ($lines[2] | ConvertFrom-Json).status | Should -Be 'succeeded'
    }

    It 'refuses offline mode with nowhere to write' {
        { New-AdgPublisher -Mode Offline } | Should -Throw '*needs OutputDirectory*'
    }

    It 'refuses API mode with no URL' {
        { New-AdgPublisher -Mode Api } | Should -Throw '*needs ApiBaseUrl*'
    }

    It 'sends a collector key in the header the API reads it from' {
        $publisher = New-AdgPublisher -Mode Api -ApiBaseUrl 'http://localhost:8000' -CollectorKey 'a-key'

        $publisher.Headers['X-ADG-Collector-Key'] | Should -Be 'a-key'
        $publisher.Headers.ContainsKey('Authorization') | Should -BeFalse
    }

    It 'sends a bearer token when an operator supplies one instead' {
        $publisher = New-AdgPublisher -Mode Api -ApiBaseUrl 'http://localhost:8000' -AuthenticationToken 'a-token'

        $publisher.Headers['Authorization'] | Should -Be 'Bearer a-token'
    }

    It 'warns when an API run carries no credential at all' {
        # The API rejects anonymous ingestion. Silence here would turn a 401 into a
        # mysterious failed run.
        $warnings = @()
        New-AdgPublisher -Mode Api -ApiBaseUrl 'http://localhost:8000' -WarningVariable warnings -WarningAction SilentlyContinue | Out-Null

        ($warnings -join ' ') | Should -BeLike '*rejects anonymous ingestion*'
    }

    It 'says nothing about credentials for an offline run' {
        $directory = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
        $warnings = @()
        New-AdgPublisher -Mode Offline -OutputDirectory $directory -WarningVariable warnings -WarningAction SilentlyContinue | Out-Null

        ($warnings -join ' ') | Should -Not -BeLike '*anonymous ingestion*'
    }
}
