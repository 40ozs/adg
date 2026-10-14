#Requires -Version 7.2
<#
    The local-groups collector against an imaginary host.

    The acquisition seam (AdgLocalGroupSource.ps1) is mocked, so these run anywhere. What
    they pin is what a reading of a host's local groups is allowed to claim:

      * a local group is identified by its host and its SID, never by its name;
      * a member the host could not resolve is reported - as `unresolved` - never dropped;
      * a host is reconciled only when every group and every member list was read, and a
        domain controller is never read as if it had local groups at all.
#>

BeforeAll {
    $collectorRoot = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path (Split-Path -Parent $collectorRoot) 'common/AdgCollector.Common.psd1') -Force
    Import-Module (Join-Path $collectorRoot 'AdgLocalGroupCollector.psd1') -Force

    $script:RunId = '0f8d2b8e-3c55-4b0e-9d6e-7a1c2b3d4e5f'
    $script:Domain = 'S-1-5-21-1004336348-1177238915-682003330'
    $script:Machine = 'S-1-5-21-111111111-222222222-333333333'
    $script:FinanceRw = "$script:Domain-1202"
    $script:Alice = "$script:Domain-1104"
    $script:Orphan = "$script:Domain-9999"
    $script:At = '2026-10-14T08:00:00.000Z'

    function script:New-Member([string] $Sid, [int] $Usage, [string] $Name = '') {
        [pscustomobject]@{ Sid = $Sid; SidUsage = $Usage; AccountName = $Name }
    }

    function script:Read-OfflineRun([string] $Directory) {
        $start = Get-Content -Raw (Join-Path $Directory 'start.json') | ConvertFrom-Json
        $completion = Get-Content -Raw (Join-Path $Directory 'completion.json') | ConvertFrom-Json
        $batches = @(Get-ChildItem -Path $Directory -Filter 'batch-*.json' | Sort-Object Name |
                ForEach-Object { Get-Content -Raw $_.FullName | ConvertFrom-Json })
        $observations = @($batches | ForEach-Object { $_.observations })
        [pscustomobject]@{
            Start        = $start
            Completion   = $completion
            Batches      = $batches
            Observations = $observations
        }
    }
}

Describe 'What a member is' {
    It 'reads <Usage> as <Kind>' -ForEach @(
        @{ Usage = 1; Sid = 'S-1-5-21-1-2-3-1104'; Kind = 'user' }
        @{ Usage = 2; Sid = 'S-1-5-21-1-2-3-1202'; Kind = 'domain_group' }
        @{ Usage = 4; Sid = 'S-1-5-32-545'; Kind = 'local_group' }
        @{ Usage = 4; Sid = 'S-1-5-21-1-2-3-1300'; Kind = 'domain_group' }
        @{ Usage = 5; Sid = 'S-1-5-4'; Kind = 'well_known' }
        @{ Usage = 9; Sid = 'S-1-5-21-1-2-3-2001'; Kind = 'computer' }
        @{ Usage = 6; Sid = 'S-1-5-21-1-2-3-9999'; Kind = 'unresolved' }
        @{ Usage = 8; Sid = 'S-1-5-21-1-2-3-9998'; Kind = 'unresolved' }
    ) {
        ConvertTo-AdgLocalMemberKind -SidUsage $Usage -Sid $Sid | Should -Be $Kind
    }

    It 'says nothing for a SID type no kind can be read from' {
        ConvertTo-AdgLocalMemberKind -SidUsage 3 -Sid 'S-1-5-21-1-2-3' | Should -BeNullOrEmpty
    }
}

Describe 'Which lookups found one of the host''s own groups' {
    It 'accepts a BUILTIN alias' {
        $account = [pscustomobject]@{ Sid = 'S-1-5-32-544'; Domain = 'BUILTIN'; SidUsage = 4 }
        Test-AdgHostLocalGroupAccount -Account $account -HostNetBiosName 'FS01' | Should -BeTrue
    }

    It 'accepts an alias the host itself issued, whatever the case of its name' {
        $account = [pscustomobject]@{ Sid = "$script:Machine-1001"; Domain = 'fs01'; SidUsage = 4 }
        Test-AdgHostLocalGroupAccount -Account $account -HostNetBiosName 'FS01' | Should -BeTrue
    }

    It 'refuses an alias a domain issued: recording it as the host''s would scope it wrongly' {
        $account = [pscustomobject]@{ Sid = "$script:Domain-1300"; Domain = 'CORP'; SidUsage = 4 }
        Test-AdgHostLocalGroupAccount -Account $account -HostNetBiosName 'FS01' | Should -BeFalse
    }

    It 'refuses anything that is not an alias' {
        $account = [pscustomobject]@{ Sid = "$script:Machine-500"; Domain = 'FS01'; SidUsage = 1 }
        Test-AdgHostLocalGroupAccount -Account $account -HostNetBiosName 'FS01' | Should -BeFalse
    }
}

Describe 'Translating one local group' {
    BeforeAll {
        $script:Translated = ConvertTo-AdgLocalGroupObservation -RunId $script:RunId -HostKey 'fs01' `
            -GroupSid 'S-1-5-32-544' -GroupName 'Administrators' -ObservedAt $script:At -Members @(
            (New-Member $script:FinanceRw 2 'CORP\Finance-RW')
            (New-Member $script:Orphan 6)
            (New-Member 'S-1-5-32-545' 4 'BUILTIN\Users')
            (New-Member $script:FinanceRw 2 'CORP\Finance-RW')
        )
        $script:Edges = @($script:Translated.Observations | Where-Object { $_['kind'] -eq 'membership_edge' })
    }

    It 'reports the group as a local group scoped to its host' {
        $group = $script:Translated.Observations[0]
        $group['kind'] | Should -Be 'principal'
        $group['source_key'] | Should -Be 'principal|fs01|S-1-5-32-544'
        $group['principal_kind'] | Should -Be 'local_group'
        $group['host_key'] | Should -Be 'fs01'
        $group['display_name'] | Should -Be 'Administrators'
    }

    It 'reports each member once, as a host-scoped local membership edge' {
        $script:Edges.Count | Should -Be 3
        $script:Edges | ForEach-Object {
            $_['edge_kind'] | Should -Be 'local_group_member'
            $_['host_key'] | Should -Be 'fs01'
            $_['is_foreign_security_principal'] | Should -BeFalse
        }
    }

    It 'keeps a member the host could not resolve, and says it could not' {
        $orphan = $script:Edges | Where-Object { $_['member_sid'] -eq $script:Orphan }
        $orphan['member_kind'] | Should -Be 'unresolved'
        $orphan['source_key'] | Should -Be "edge|fs01|S-1-5-32-544->$script:Orphan|local_group_member"
    }

    It 'scopes a BUILTIN member to the host, and a domain member not at all' {
        ($script:Edges | Where-Object { $_['member_sid'] -eq 'S-1-5-32-545' })['source_key'] |
            Should -Be 'edge|fs01|S-1-5-32-544->fs01|S-1-5-32-545|local_group_member'
        ($script:Edges | Where-Object { $_['member_sid'] -eq $script:FinanceRw })['source_key'] |
            Should -Be "edge|fs01|S-1-5-32-544->$script:FinanceRw|local_group_member"
    }

    It 'reports no principal for a member: a domain account is the directory collector''s' {
        @($script:Translated.Observations | Where-Object { $_['kind'] -eq 'principal' }).Count | Should -Be 1
    }

    It 'reports a member it cannot carry as an error, never as an edge' {
        $result = ConvertTo-AdgLocalGroupObservation -RunId $script:RunId -HostKey 'fs01' `
            -GroupSid 'S-1-5-32-544' -GroupName 'Administrators' -ObservedAt $script:At -Members @(
            (New-Member 'CORP\alice' 1)
            (New-Member 'S-1-5-32-544' 4)
        )
        @($result.Observations).Count | Should -Be 1
        @($result.Errors).Count | Should -Be 2
        $result.Errors | ForEach-Object { $_['code'] | Should -Be 'invalid_member' }
    }
}

Describe 'One host, one run' {
    BeforeEach {
        Mock -ModuleName AdgLocalGroupCollector Get-AdgRemoteServerInfo {
            [pscustomobject]@{ Name = 'FS01'; IsDomainController = $false }
        }
        Mock -ModuleName AdgLocalGroupCollector Get-AdgRemoteLocalGroup {
            @(
                [pscustomobject]@{ Name = 'Administrators'; Comment = '' }
                [pscustomobject]@{ Name = 'Remote Desktop Users'; Comment = '' }
            )
        }
        Mock -ModuleName AdgLocalGroupCollector Resolve-AdgRemoteAccountName {
            switch ($Name) {
                'Administrators' { [pscustomobject]@{ Sid = 'S-1-5-32-544'; Domain = 'BUILTIN'; SidUsage = 4 } }
                'Remote Desktop Users' { [pscustomobject]@{ Sid = 'S-1-5-32-555'; Domain = 'BUILTIN'; SidUsage = 4 } }
            }
        }
        Mock -ModuleName AdgLocalGroupCollector Get-AdgRemoteLocalGroupMember {
            switch ($GroupName) {
                'Administrators' { @([pscustomobject]@{ Sid = 'S-1-5-21-1004336348-1177238915-682003330-1202'; SidUsage = 2; AccountName = 'CORP\Finance-RW' }) }
                'Remote Desktop Users' { @([pscustomobject]@{ Sid = 'S-1-5-21-1004336348-1177238915-682003330-1104'; SidUsage = 1; AccountName = 'CORP\alice' }) }
            }
        }
        $script:Out = Join-Path $TestDrive ([guid]::NewGuid().ToString())
        $script:Publisher = New-AdgPublisher -Mode Offline -OutputDirectory $script:Out
    }

    It 'claims and reconciles the host when every group was read' {
        $summary = Invoke-AdgLocalGroupHostRun -ComputerName 'FS01' -Publisher $script:Publisher -RunId $script:RunId
        $summary.Status | Should -Be 'succeeded'
        $summary.Reconciled | Should -BeTrue

        $run = Read-OfflineRun $script:Out
        $run.Start.source.collector | Should -Be 'local_groups'
        @($run.Start.scopes).Count | Should -Be 1
        $run.Start.scopes[0].kind | Should -Be 'local_groups_host'
        $run.Start.scopes[0].key | Should -Be 'fs01'
        $run.Completion.status | Should -Be 'succeeded'
        @($run.Completion.reconciled_scopes).Count | Should -Be 1
        $run.Completion.observation_count | Should -Be 4
        $run.Observations.Count | Should -Be 4
        @($run.Observations | Where-Object { $_.kind -eq 'principal' }).source_key |
            Should -Be @('principal|fs01|S-1-5-32-544', 'principal|fs01|S-1-5-32-555')
    }

    It 'reconciles nothing when one group''s members could not be read' {
        Mock -ModuleName AdgLocalGroupCollector Get-AdgRemoteLocalGroupMember {
            if ($GroupName -eq 'Remote Desktop Users') { throw [System.ComponentModel.Win32Exception]::new(5) }
            @([pscustomobject]@{ Sid = 'S-1-5-21-1004336348-1177238915-682003330-1202'; SidUsage = 2; AccountName = '' })
        }
        $summary = Invoke-AdgLocalGroupHostRun -ComputerName 'FS01' -Publisher $script:Publisher -RunId $script:RunId
        $summary.Status | Should -Be 'partial'
        $summary.Reconciled | Should -BeFalse

        $run = Read-OfflineRun $script:Out
        @($run.Completion.reconciled_scopes).Count | Should -Be 0
        $run.Completion.errors[0].code | Should -Be 'access_denied'
        $run.Completion.errors[0].target | Should -Be '\\FS01\Remote Desktop Users'
    }

    It 'leaves out a group whose name resolved to something that is not the host''s own' {
        Mock -ModuleName AdgLocalGroupCollector Resolve-AdgRemoteAccountName {
            [pscustomobject]@{ Sid = 'S-1-5-21-1004336348-1177238915-682003330-1300'; Domain = 'CORP'; SidUsage = 4 }
        }
        $summary = Invoke-AdgLocalGroupHostRun -ComputerName 'FS01' -Publisher $script:Publisher -RunId $script:RunId
        $summary.Status | Should -Be 'failed'
        $run = Read-OfflineRun $script:Out
        $run.Observations.Count | Should -Be 0
        @($run.Completion.errors | ForEach-Object code | Sort-Object -Unique) | Should -Be @('lookup_failed')
    }

    It 'fails without a batch when the host cannot be reached' {
        Mock -ModuleName AdgLocalGroupCollector Get-AdgRemoteServerInfo { throw [System.ComponentModel.Win32Exception]::new(53) }
        $summary = Invoke-AdgLocalGroupHostRun -ComputerName 'FS01' -Publisher $script:Publisher -RunId $script:RunId
        $summary.Status | Should -Be 'failed'
        $summary.Batches | Should -Be 0
        $run = Read-OfflineRun $script:Out
        $run.Completion.errors[0].code | Should -Be 'unreachable'
        @($run.Completion.reconciled_scopes).Count | Should -Be 0
    }

    It 'refuses a domain controller and reads nothing from it' {
        Mock -ModuleName AdgLocalGroupCollector Get-AdgRemoteServerInfo {
            [pscustomobject]@{ Name = 'DC01'; IsDomainController = $true }
        }
        $summary = Invoke-AdgLocalGroupHostRun -ComputerName 'DC01' -Publisher $script:Publisher -RunId $script:RunId
        $summary.Status | Should -Be 'failed'
        Should -Invoke -ModuleName AdgLocalGroupCollector Get-AdgRemoteLocalGroup -Times 0 -Exactly
        $run = Read-OfflineRun $script:Out
        $run.Completion.errors[0].code | Should -Be 'domain_controller'
    }
}

Describe 'The read-only boundary' {
    It 'binds nothing but read-only network-management queries and the buffer release' {
        $source = Get-Content -Raw (Join-Path (Split-Path -Parent $PSScriptRoot) 'functions/AdgLocalGroupSource.ps1')
        $imports = [regex]::Matches($source, 'static extern \w+ (\w+)\(') | ForEach-Object { $_.Groups[1].Value }
        @($imports | Sort-Object) | Should -Be @(
            'LookupAccountName', 'NetApiBufferFree', 'NetLocalGroupEnum', 'NetLocalGroupGetMembers', 'NetServerGetInfo'
        )
    }
}

Describe 'Reading this machine' -Skip:(-not $IsWindows) {
    It 'finds BUILTIN\Administrators by SID, with every member reported by SID' {
        $server = Get-AdgRemoteServerInfo -ComputerName 'localhost'
        $server.Name | Should -Not -BeNullOrEmpty
        $server.IsDomainController | Should -BeFalse

        $groups = @(Get-AdgRemoteLocalGroup -ComputerName 'localhost')
        $groups.Count | Should -BeGreaterThan 0
        $resolved = foreach ($group in $groups) {
            $account = Resolve-AdgRemoteAccountName -ComputerName 'localhost' -Name $group.Name
            [pscustomobject]@{ Name = $group.Name; Account = $account }
        }
        $administrators = @($resolved | Where-Object { $_.Account.Sid -eq 'S-1-5-32-544' })
        $administrators.Count | Should -Be 1
        $administrators[0].Account.Domain | Should -Be 'BUILTIN'
        $administrators[0].Account.SidUsage | Should -Be 4

        $members = @(Get-AdgRemoteLocalGroupMember -ComputerName 'localhost' -GroupName $administrators[0].Name)
        $members.Count | Should -BeGreaterThan 0
        $members | ForEach-Object { $_.Sid | Should -Match '^S-1-' }
    }

    It 'reads the whole machine as one complete, reconcilable run' {
        $out = Join-Path $TestDrive 'this-machine'
        $publisher = New-AdgPublisher -Mode Offline -OutputDirectory $out
        $summary = Invoke-AdgLocalGroupHostRun -ComputerName 'localhost' -Publisher $publisher
        $summary.Errors | Should -Be 0
        $summary.Status | Should -Be 'succeeded'
        $run = Read-OfflineRun $out
        @($run.Observations | Where-Object { $_.source_key -eq 'principal|localhost|S-1-5-32-544' }).Count |
            Should -Be 1
    }
}
