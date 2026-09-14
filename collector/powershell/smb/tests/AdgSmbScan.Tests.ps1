#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Orchestration: an imaginary file-server estate, with no network.

    Every function that touches a remote host lives in AdgSmbSource.ps1 and is mocked
    here, so these tests exercise the parts that decide what a scan means: what it
    declares it enumerated, what it will let the backend mark absent, and what it refuses
    to claim when something went wrong.

    The scenarios the phase requires - a normal share, an unresolved trustee, an
    inaccessible server, and a replayed scan - are each a Context below.
#>

BeforeAll {
    $moduleRoot = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $moduleRoot 'AdgSmbCollector.psd1') -Force

    function New-TestShare {
        param(
            [string] $Name,
            [string] $ShareType = 'FileSystemDirectory',
            [bool] $Special = $false,
            [string] $Path = 'D:\Shares\Data'
        )
        return [pscustomobject]@{
            Name                = $Name
            ShareType           = $ShareType
            Special             = $Special
            Path                = $Path
            Description         = "$Name share"
            ConcurrentUserLimit = 0
            CachingMode         = 'Manual'
        }
    }

    function New-TestAce {
        param(
            [string] $SidString = 'S-1-5-11',
            [string] $TrusteeName = 'Authenticated Users',
            [int] $AceType = 0,
            [long] $AccessMask = 1179817
        )
        return [pscustomobject]@{
            AceType    = $AceType
            AceFlags   = 0
            AccessMask = $AccessMask
            Trustee    = [pscustomobject]@{ SIDString = $SidString; Name = $TrusteeName; Domain = 'CORP' }
        }
    }

    # A small estate: one ordinary share, one hidden share, and the shares Windows makes.
    function Set-TestEstate {
        param(
            [object[]] $Shares,
            [object[]] $Dacl,
            [switch] $DaclPresentFalse,
            [switch] $DescriptorThrows
        )

        Mock -ModuleName AdgSmbCollector New-AdgSmbSession {
            [pscustomobject]@{ ComputerName = $ComputerName }
        }
        Mock -ModuleName AdgSmbCollector Remove-AdgSmbSession { }
        Mock -ModuleName AdgSmbCollector Get-AdgRemoteComputerFact {
            @{
                DnsHostName     = 'fs01.corp.example.com'
                NetbiosName     = 'FS01'
                IsDomainMember  = $true
                OperatingSystem = 'Microsoft Windows Server 2022 Standard'
            }
        }
        $shareList = $Shares
        Mock -ModuleName AdgSmbCollector Get-AdgRemoteShare { $shareList }.GetNewClosure()

        if ($DescriptorThrows) {
            Mock -ModuleName AdgSmbCollector Get-AdgRemoteShareSecurity { throw 'The RPC server is unavailable.' }
        }
        else {
            $daclList = $Dacl
            $present = -not $DaclPresentFalse
            Mock -ModuleName AdgSmbCollector Get-AdgRemoteShareSecurity {
                @{ Dacl = $daclList; DaclPresent = $present }
            }.GetNewClosure()
        }
    }

    function Get-Observations {
        # The comma operator is load bearing. PowerShell unrolls a returned array, so a
        # one-element result would come back as a bare observation - and .Count on an
        # ordered dictionary is its number of keys, which silently turns "one server
        # observation" into ten.
        param($Run, [string] $Kind)
        $all = @($Run.Batches | ForEach-Object { $_.observations })
        if ($Kind) { return , @($all | Where-Object { $_.kind -eq $Kind }) }
        return , $all
    }
}

AfterAll {
    Remove-Module AdgSmbCollector -Force -ErrorAction SilentlyContinue
}

Describe 'A normal scan' {

    BeforeEach {
        Set-TestEstate -Shares @(
            (New-TestShare -Name 'Finance' -Path 'D:\Shares\Finance')
            (New-TestShare -Name 'Projects' -Path 'D:\Shares\Projects')
            (New-TestShare -Name 'C$' -Special $true -Path 'C:\')
            (New-TestShare -Name 'IPC$' -ShareType 'InterprocessCommunication' -Special $true -Path '')
            (New-TestShare -Name 'Data$' -Path 'D:\Shares\Data')
        ) -Dacl @(
            (New-TestAce -SidString 'S-1-5-21-1-2-3-1202' -TrusteeName 'Finance-RW' -AccessMask 1245631)
            (New-TestAce -SidString 'S-1-5-11' -TrusteeName 'Authenticated Users' -AccessMask 1179817)
        )
    }

    It 'reports the server it reached' {
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $run = (Invoke-AdgSmbScan -Settings $settings)[0]

        $servers = Get-Observations $run 'server'
        $servers.Count | Should -Be 1
        $servers[0].source_key | Should -BeExactly 'server|fs01'
        $servers[0].dns_host_name | Should -Be 'fs01.corp.example.com'
        $servers[0].is_domain_member | Should -BeTrue
    }

    It 'reports every share, and reads the ACL only of the ones it collects' {
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $run = (Invoke-AdgSmbScan -Settings $settings)[0]

        # All five exist; that much was observed, and recording it is what lets the server
        # scope be reconciled without marking C$ deleted.
        (Get-Observations $run 'smb_share').Count | Should -Be 5

        # Only Finance and Projects had their ACLs read: two ACEs each.
        (Get-Observations $run 'smb_ace').Count | Should -Be 4
        $run.Summary.SharesExcluded | Should -Be 3
    }

    It 'declares a share scope only for the shares whose ACL it actually read' {
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $run = (Invoke-AdgSmbScan -Settings $settings)[0]

        $shareScopes = @($run.Start.scopes | Where-Object { $_.kind -eq 'share' })
        $shareScopes.key | Should -Be @('fs01|finance', 'fs01|projects')

        # An unread ACL is never inside a declared scope, so its absent ACEs can never be
        # reconciled into "nobody has access".
        $shareScopes.key | Should -Not -Contain 'fs01|c$'
    }

    It 'declares the server scope, because every share on the host was enumerated' {
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $run = (Invoke-AdgSmbScan -Settings $settings)[0]

        @($run.Start.scopes | Where-Object { $_.kind -eq 'server' }).key | Should -Be @('fs01')
    }

    It 'succeeds and offers every declared scope for reconciliation' {
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $run = (Invoke-AdgSmbScan -Settings $settings)[0]

        $run.Completion.status | Should -Be 'succeeded'
        $run.Completion.error_count | Should -Be 0
        $run.Completion.reconciled_scopes.Count | Should -Be $run.Start.scopes.Count
    }

    It 'withholds the server scope when excluded shares are not recorded' {
        # Dropping excluded shares means the run no longer enumerates the whole server, so
        # claiming the server scope would mark C$ and IPC$ deleted on the next clean run.
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $settings.RecordExcludedShares = $false

        $run = (Invoke-AdgSmbScan -Settings $settings)[0]

        (Get-Observations $run 'smb_share').Count | Should -Be 2
        @($run.Start.scopes | Where-Object { $_.kind -eq 'server' }).Count | Should -Be 0
    }

    It 'collects hidden shares when asked, keeping them distinct from admin shares' {
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $settings.IncludeHiddenShares = $true

        $run = (Invoke-AdgSmbScan -Settings $settings)[0]

        @($run.Start.scopes | Where-Object { $_.kind -eq 'share' }).key | Should -Contain 'fs01|data$'
        @($run.Start.scopes | Where-Object { $_.kind -eq 'share' }).key | Should -Not -Contain 'fs01|c$'
    }

    It 'reports no derived access anywhere in the payload' {
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $run = (Invoke-AdgSmbScan -Settings $settings)[0]
        $json = @($run.Start, $run.Batches, $run.Completion) | ConvertTo-Json -Depth 15

        foreach ($forbidden in @('effective', 'can_access', 'resolved_access', 'risk')) {
            $json | Should -Not -Match $forbidden
        }
    }

    It 'names the reading method on the run, not on every observation' {
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $run = (Invoke-AdgSmbScan -Settings $settings)[0]

        $run.Start.source.collector | Should -Be 'smb'
        $run.Start.source.method | Should -Match 'GetSecurityDescriptor'
        (Get-Observations $run 'smb_ace')[0].Contains('source') | Should -BeFalse
    }
}

Describe 'Unresolved trustees' {

    It 'keeps the ACE, reports the orphaned SID, and still succeeds' {
        # An orphaned SID is a normal fact about a real domain, not a collection failure.
        # It must not make the run partial, or every estate would be permanently partial.
        Set-TestEstate -Shares @((New-TestShare -Name 'Finance')) -Dacl @(
            (New-TestAce -SidString 'S-1-5-21-1-2-3-9999' -TrusteeName '')
            (New-TestAce -SidString 'S-1-5-11' -TrusteeName 'Authenticated Users')
        )

        $run = (Invoke-AdgSmbScan -Settings (Import-AdgSmbTarget -Server 'FS01'))[0]

        (Get-Observations $run 'smb_ace').Count | Should -Be 2

        $principals = Get-Observations $run 'principal'
        $principals.Count | Should -Be 1
        $principals[0].sid | Should -Be 'S-1-5-21-1-2-3-9999'
        $principals[0].principal_kind | Should -Be 'unresolved'

        $run.Completion.status | Should -Be 'succeeded'
        @($run.Start.scopes | Where-Object { $_.kind -eq 'share' }).key | Should -Contain 'fs01|finance'
    }

    It 'falls back to permission levels when the descriptor is unreadable' {
        Set-TestEstate -Shares @((New-TestShare -Name 'Finance')) -DescriptorThrows
        Mock -ModuleName AdgSmbCollector Get-AdgRemoteShareAccess {
            @([pscustomobject]@{ AccountName = 'CORP\Finance-RW'; AccessControlType = 'Allow'; AccessRight = 'Change' })
        }
        Mock -ModuleName AdgSmbCollector Resolve-AdgTrusteeSid { 'S-1-5-21-1-2-3-1202' }

        $run = (Invoke-AdgSmbScan -Settings (Import-AdgSmbTarget -Server 'FS01'))[0]

        $aces = Get-Observations $run 'smb_ace'
        $aces.Count | Should -Be 1
        $aces[0].permission | Should -Be 'change'
        $aces[0].Contains('access_mask') | Should -BeFalse
        $run.Completion.status | Should -Be 'succeeded'
    }

    It 'goes partial and refuses the share scope when neither method can read the ACL' {
        Set-TestEstate -Shares @((New-TestShare -Name 'Finance')) -DescriptorThrows
        Mock -ModuleName AdgSmbCollector Get-AdgRemoteShareAccess { throw 'Access is denied.' }

        $run = (Invoke-AdgSmbScan -Settings (Import-AdgSmbTarget -Server 'FS01'))[0]

        $run.Completion.status | Should -Be 'partial'
        $run.Completion.errors[0].code | Should -Be 'access_denied'
        $run.Completion.reconciled_scopes.Count | Should -Be 0

        # The share itself was still observed: it exists, and only its ACL is unknown.
        (Get-Observations $run 'smb_share').Count | Should -Be 1
    }

    It 'raises a NULL share DACL loudly instead of reporting an empty ACL' {
        # A NULL DACL grants everyone full share access. Zero ACEs would read as the exact
        # opposite, so it is reported as an error and the share scope is withheld.
        Set-TestEstate -Shares @((New-TestShare -Name 'Finance')) -Dacl @() -DaclPresentFalse

        $run = (Invoke-AdgSmbScan -Settings (Import-AdgSmbTarget -Server 'FS01'))[0]

        (Get-Observations $run 'smb_ace').Count | Should -Be 0
        $run.Completion.errors[0].code | Should -Be 'null_share_dacl'
        $run.Completion.status | Should -Be 'partial'
        @($run.Start.scopes | Where-Object { $_.kind -eq 'share' }).Count | Should -Be 0
    }
}

Describe 'An inaccessible server' {

    BeforeEach {
        Mock -ModuleName AdgSmbCollector New-AdgSmbSession { throw 'The RPC server is unavailable.' }
        Mock -ModuleName AdgSmbCollector Remove-AdgSmbSession { }
        Mock -ModuleName AdgSmbCollector Start-Sleep { }
    }

    It 'reports the failure instead of reporting no shares' {
        $run = (Invoke-AdgSmbScan -Settings (Import-AdgSmbTarget -Server 'FS-DEAD'))[0]

        $run.Completion.status | Should -Be 'failed'
        $run.Completion.error_count | Should -Be 1
        $run.Completion.errors[0].code | Should -Be 'rpc_unavailable'
        $run.Completion.errors[0].target | Should -Be 'FS-DEAD'
    }

    It 'emits no server observation for a host that never answered' {
        # An observation asserts the object was seen. Recording FS-DEAD as observed would
        # be a claim the collector cannot support.
        $run = (Invoke-AdgSmbScan -Settings (Import-AdgSmbTarget -Server 'FS-DEAD'))[0]

        (Get-Observations $run 'server').Count | Should -Be 0
        $run.Completion.observation_count | Should -Be 0
    }

    It 'still declares the scope it set out to enumerate, and reconciles none of it' {
        $run = (Invoke-AdgSmbScan -Settings (Import-AdgSmbTarget -Server 'FS-DEAD'))[0]

        # Declaring it records the intent; not reconciling it is what stops the backend
        # from concluding that every share on FS-DEAD was deleted.
        @($run.Start.scopes).key | Should -Be @('fs-dead')
        $run.Completion.reconciled_scopes.Count | Should -Be 0
    }

    It 'retries a connection the configured number of times, then gives up' {
        $settings = Import-AdgSmbTarget -Server 'FS-DEAD'
        $settings.RetryCount = 2

        Invoke-AdgSmbScan -Settings $settings | Out-Null

        Should -Invoke New-AdgSmbSession -ModuleName AdgSmbCollector -Times 3 -Exactly
    }

    It 'does not retry when retries are switched off' {
        $settings = Import-AdgSmbTarget -Server 'FS-DEAD'
        $settings.RetryCount = 0

        Invoke-AdgSmbScan -Settings $settings | Out-Null

        Should -Invoke New-AdgSmbSession -ModuleName AdgSmbCollector -Times 1 -Exactly
    }

}

Describe 'A partly reachable estate' {

    BeforeEach {
        # One host answers, one never does. Declared in its own Describe so that no blanket
        # "everything throws" mock from a sibling block can decide the outcome instead.
        Mock -ModuleName AdgSmbCollector New-AdgSmbSession {
            if ($ComputerName -eq 'FS-DEAD') { throw 'The RPC server is unavailable.' }
            [pscustomobject]@{ ComputerName = $ComputerName }
        }
        Mock -ModuleName AdgSmbCollector Remove-AdgSmbSession { }
        Mock -ModuleName AdgSmbCollector Start-Sleep { }
        Mock -ModuleName AdgSmbCollector Get-AdgRemoteComputerFact {
            @{ DnsHostName = $null; NetbiosName = 'FS02'; IsDomainMember = $true; OperatingSystem = $null }
        }

        # Build the fixtures out here and close over them. A mock body runs in the module's
        # scope, where the helpers defined in this file do not exist; calling one from
        # inside the body throws, the scan records an unreachable server, and the test
        # fails for a reason that has nothing to do with the code under test.
        $shareList = @((New-TestShare -Name 'Finance'))
        $daclList = @((New-TestAce))
        Mock -ModuleName AdgSmbCollector Get-AdgRemoteShare { $shareList }.GetNewClosure()
        Mock -ModuleName AdgSmbCollector Get-AdgRemoteShareSecurity {
            @{ Dacl = $daclList; DaclPresent = $true }
        }.GetNewClosure()
    }

    It 'does not let one dead server stop the others from being collected' {
        $settings = Import-AdgSmbTarget -Server 'FS-DEAD', 'FS02'
        $settings.RetryCount = 0
        $run = (Invoke-AdgSmbScan -Settings $settings)[0]

        $run.Summary.ServersReached | Should -Be 1
        $run.Summary.ServersUnreachable | Should -Be @('FS-DEAD')
        (Get-Observations $run 'smb_share').Count | Should -Be 1

        # Usable observations with incomplete coverage: partial, and nothing reconciles.
        $run.Completion.status | Should -Be 'partial'
        $run.Completion.reconciled_scopes.Count | Should -Be 0
    }

    It 'contains the blast radius when each server gets its own run' {
        $settings = Import-AdgSmbTarget -Server 'FS-DEAD', 'FS02'
        $settings.RetryCount = 0
        $runs = Invoke-AdgSmbScan -Settings $settings -RunPerServer

        $runs.Count | Should -Be 2
        @($runs | Where-Object { $_.Completion.status -eq 'failed' }).Count | Should -Be 1

        # This is the point of per-server runs: the healthy server still reconciles, so a
        # share deleted on FS02 is still detectable while FS-DEAD is down.
        $healthy = @($runs | Where-Object { $_.Completion.status -eq 'succeeded' })
        $healthy.Count | Should -Be 1
        $healthy[0].Completion.reconciled_scopes.Count | Should -BeGreaterThan 0
    }
}

Describe 'A replayed scan' {

    BeforeEach {
        Set-TestEstate -Shares @(
            (New-TestShare -Name 'Finance' -Path 'D:\Shares\Finance')
            (New-TestShare -Name 'Projects' -Path 'D:\Shares\Projects')
        ) -Dacl @(
            (New-TestAce -SidString 'S-1-5-21-1-2-3-1202' -TrusteeName 'Finance-RW' -AccessMask 1245631)
            (New-TestAce -SidString 'S-1-5-11' -TrusteeName 'Authenticated Users' -AccessMask 1179817)
        )
    }

    It 'derives identical source keys from identical source data' {
        # Ingestion is keyed on (run_id, source_key). If a second scan of unchanged shares
        # produced different keys, every run would create a second row for every object
        # and change detection would be meaningless.
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $first = (Invoke-AdgSmbScan -Settings $settings)[0]
        $second = (Invoke-AdgSmbScan -Settings $settings)[0]

        $firstKeys = @(Get-Observations $first | ForEach-Object { $_.source_key })
        $secondKeys = @(Get-Observations $second | ForEach-Object { $_.source_key })

        $secondKeys | Should -Be $firstKeys
    }

    It 'produces identical observations apart from the run identity and the clock' {
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $first = (Invoke-AdgSmbScan -Settings $settings)[0]
        $second = (Invoke-AdgSmbScan -Settings $settings)[0]

        # Round-trip through JSON rather than reading the dictionary by key. An ordered
        # dictionary exposes Item[int] alongside Item[object], so a string key binds to
        # the integer overload, and dynamic property access on one is not reliable under
        # strict mode either. JSON is also the form these payloads are actually sent in.
        $strip = {
            param($run)
            @(Get-Observations $run | ForEach-Object {
                    ($_ | ConvertTo-Json -Depth 8 -Compress) `
                        -replace '"run_id":"[^"]*",?', '' `
                        -replace '"observed_at":"[^"]*",?', ''
                })
        }

        (& $strip $second) | Should -Be (& $strip $first)
    }

    It 'gives each run and each batch a fresh identifier' {
        # Re-sending a batch reuses its batch_id, which is what makes the retry a
        # recognized duplicate. Two different scans are not retries of each other, so they
        # must not share ids.
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $first = (Invoke-AdgSmbScan -Settings $settings)[0]
        $second = (Invoke-AdgSmbScan -Settings $settings)[0]

        $second.Start.run_id | Should -Not -Be $first.Start.run_id
        $second.Batches[0].batch_id | Should -Not -Be $first.Batches[0].batch_id
    }

    It 'never repeats a source key within one batch' {
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $run = (Invoke-AdgSmbScan -Settings $settings)[0]

        foreach ($batch in @($run.Batches)) {
            $keys = @($batch.observations | ForEach-Object { $_.source_key })
            ($keys | Sort-Object -Unique).Count | Should -Be $keys.Count
        }
    }

    It 'stamps every observation with its own run id' {
        $settings = Import-AdgSmbTarget -Server 'FS01'
        $run = (Invoke-AdgSmbScan -Settings $settings)[0]

        foreach ($observation in (Get-Observations $run)) {
            $observation.run_id | Should -Be $run.Start.run_id
        }
    }
}

Describe 'Split-AdgObservationBatch' {

    BeforeAll {
        $script:RunId = '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31'
        $script:Many = @(1..1250 | ForEach-Object {
                New-AdgObservation -Kind 'server' -RunId $script:RunId -SourceKey "server|fs$_" -Body @{ name = "FS$_" }
            })
    }

    It 'never exceeds the contract maximum' {
        $batches = Split-AdgObservationBatch -RunId $script:RunId -Observations $script:Many -BatchSize 1000

        $batches.Count | Should -Be 2
        @($batches | ForEach-Object { $_.observations.Count }) | Should -Be @(1000, 250)
    }

    It 'numbers batches from one and marks only the last as final' {
        $batches = Split-AdgObservationBatch -RunId $script:RunId -Observations $script:Many -BatchSize 500

        @($batches.sequence) | Should -Be @(1, 2, 3)
        @($batches.is_final) | Should -Be @($false, $false, $true)
    }

    It 'preserves order, so a share stays with or ahead of its ACEs' {
        $batches = Split-AdgObservationBatch -RunId $script:RunId -Observations $script:Many -BatchSize 500
        $flat = @($batches | ForEach-Object { $_.observations })

        $flat[0].source_key | Should -BeExactly 'server|fs1'
        $flat[-1].source_key | Should -BeExactly 'server|fs1250'
    }

    It 'gives every batch a distinct id' {
        $batches = Split-AdgObservationBatch -RunId $script:RunId -Observations $script:Many -BatchSize 500

        (@($batches.batch_id) | Sort-Object -Unique).Count | Should -Be 3
    }

    It 'produces no batch at all when there is nothing to send' {
        (Split-AdgObservationBatch -RunId $script:RunId -Observations @()).Count | Should -Be 0
    }
}
