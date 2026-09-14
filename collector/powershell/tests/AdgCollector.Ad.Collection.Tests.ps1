#Requires -Version 7.0
<#
    End-to-end collection against a synthetic directory.

    The whole collector runs here -- enumeration, member resolution, batching, completion --
    with a fixture provider standing in for the LDAP connection. That is what makes the
    central guarantee testable: given a group inside a group inside a group, this collector
    must emit the chain and never its closure.
#>

BeforeAll {
    $repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
    Import-Module (Join-Path $repoRoot 'collector/powershell/common/AdgCollector.Common.psd1') -Force
    Import-Module (Join-Path $repoRoot 'collector/powershell/ad/AdgCollector.ActiveDirectory.psd1') -Force

    $script:FixturePath = Join-Path $PSScriptRoot 'fixtures/corp-domain.json'
    $script:DomainSid = 'S-1-5-21-1004336348-1177238915-682003330'
    $script:Nc = 'DC=corp,DC=example,DC=com'
    $script:Corp = "OU=Corp,$script:Nc"
    $script:Groups = "OU=Groups,$script:Corp"
    $script:Staff = "OU=Staff,$script:Corp"
    $script:ForeignSid = 'S-1-5-21-3623811015-3361044348-30300820-1234'

    function Invoke-TestCollection {
        <#
            Run a whole collection offline and return everything it produced.
        #>
        param(
            [hashtable] $ConfigOverrides = @{},
            [hashtable] $Provider
        )

        $outputDirectory = Join-Path $TestDrive ([guid]::NewGuid().ToString('n'))
        $arguments = @{ Offline = $true; OutputDirectory = $outputDirectory }
        foreach ($key in $ConfigOverrides.Keys) { $arguments[$key] = $ConfigOverrides[$key] }
        $config = New-AdgAdCollectorConfig @arguments

        if (-not $Provider) { $Provider = New-AdgFixtureDirectoryProvider -Path $script:FixturePath }
        $publisher = New-AdgPublisher -Mode Offline -OutputDirectory $outputDirectory
        $summary = Invoke-AdgAdCollection -Config $config -Provider $Provider -Publisher $publisher

        $envelopes = @(Get-Content -LiteralPath (Join-Path $outputDirectory 'envelopes.ndjson') |
                ForEach-Object { $_ | ConvertFrom-Json -Depth 20 })
        $names = { param($item) $item.PSObject.Properties.Name }

        $start = $envelopes | Where-Object { (& $names $_) -contains 'scopes' } | Select-Object -First 1
        $completion = $envelopes | Where-Object { (& $names $_) -contains 'status' } | Select-Object -First 1
        $batches = @($envelopes | Where-Object { (& $names $_) -contains 'batch_id' })
        $observations = @($batches | ForEach-Object { $_.observations })

        return @{
            Summary         = $summary
            OutputDirectory = $outputDirectory
            Start           = $start
            Completion      = $completion
            Batches         = $batches
            Observations    = $observations
            Principals      = @($observations | Where-Object { $_.kind -eq 'principal' })
            Edges           = @($observations | Where-Object { $_.kind -eq 'membership_edge' })
        }
    }

    function Test-EdgePresent {
        param($Edges, [string] $GroupSid, [string] $MemberSid, [string] $EdgeKind = 'directory_group_member')
        return @($Edges | Where-Object {
                $_.group_sid -eq $GroupSid -and $_.member_sid -eq $MemberSid -and $_.edge_kind -eq $EdgeKind
            }).Count -gt 0
    }

    function Get-ExpectedDirectEdge {
        <#
            The edges the fixture literally states, derived straight from its member and
            primaryGroupID attributes. Comparing the run against this catches both halves
            of the guarantee at once: an edge that was invented, and one that was dropped.
        #>
        $document = Get-Content -LiteralPath $script:FixturePath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable -Depth 20
        $sidByDn = @{}
        foreach ($entry in $document['entries']) {
            if ($entry['attributes'].Contains('objectSid')) {
                $sidByDn[[string] $entry['distinguishedName']] = [string] $entry['attributes']['objectSid']
            }
        }

        $expected = [System.Collections.Generic.List[string]]::new()
        foreach ($entry in $document['entries']) {
            $attributes = $entry['attributes']
            if (-not $attributes.Contains('objectSid')) { continue }
            $sid = [string] $attributes['objectSid']
            $classes = @($attributes['objectClass'] | ForEach-Object { ([string] $_).ToLowerInvariant() })

            if ($attributes.Contains('member')) {
                foreach ($memberDn in @($attributes['member'])) {
                    $expected.Add("$sid|$($sidByDn[[string] $memberDn])|directory_group_member")
                }
            }
            if ($attributes.Contains('primaryGroupID') -and $classes -notcontains 'group') {
                $domainSid = Get-AdgDomainSidFromSid $sid
                $expected.Add("$domainSid-$($attributes['primaryGroupID'])|$sid|primary_group")
            }
        }
        return $expected.ToArray()
    }
}

Describe 'Membership is a graph, not a set' {
    BeforeAll { $script:Run = Invoke-TestCollection }

    It 'emits the link from the outer group to the inner group' {
        Test-EdgePresent -Edges $script:Run.Edges -GroupSid "$script:DomainSid-1202" -MemberSid "$script:DomainSid-1201" |
            Should -BeTrue
    }

    It 'emits the link from the inner group to the user' {
        Test-EdgePresent -Edges $script:Run.Edges -GroupSid "$script:DomainSid-1201" -MemberSid "$script:DomainSid-1104" |
            Should -BeTrue
    }

    It 'does NOT emit the flattened link from the outer group to the user' {
        # Finance-RW contains Finance-Team contains Alice. A collector that expanded
        # nesting would emit Finance-RW -> Alice, and the chain that explains Alice's
        # access -- the thing an administrator would have to change -- would be gone.
        Test-EdgePresent -Edges $script:Run.Edges -GroupSid "$script:DomainSid-1202" -MemberSid "$script:DomainSid-1104" |
            Should -BeFalse
    }

    It 'emits exactly the edges the directory stated: none invented, none dropped' {
        $expected = @(Get-ExpectedDirectEdge | Sort-Object)
        $actual = @($script:Run.Edges | ForEach-Object { "$($_.group_sid)|$($_.member_sid)|$($_.edge_kind)" } | Sort-Object)

        $actual | Should -Be $expected
    }

    It 'reports a membership cycle as two ordinary edges and still terminates' {
        Test-EdgePresent -Edges $script:Run.Edges -GroupSid "$script:DomainSid-1210" -MemberSid "$script:DomainSid-1211" |
            Should -BeTrue
        Test-EdgePresent -Edges $script:Run.Edges -GroupSid "$script:DomainSid-1211" -MemberSid "$script:DomainSid-1210" |
            Should -BeTrue
        $script:Run.Summary.Status | Should -Be 'succeeded'
    }

    It 'never sends an expanded-membership field' {
        $json = $script:Run.Observations | ConvertTo-Json -Depth 12
        foreach ($forbidden in @('effective', 'expanded', 'transitive', 'members_recursive')) {
            $json | Should -Not -Match $forbidden
        }
    }
}

Describe 'Primary group membership' {
    BeforeAll { $script:Run = Invoke-TestCollection }

    It 'emits the edge that no group member attribute contains' {
        # primaryGroupID 513 is Domain Users. A collector that read only 'member' would
        # lose this for every user in the domain.
        Test-EdgePresent -Edges $script:Run.Edges -GroupSid "$script:DomainSid-513" `
            -MemberSid "$script:DomainSid-1104" -EdgeKind 'primary_group' | Should -BeTrue
    }

    It 'emits it for a computer account too' {
        Test-EdgePresent -Edges $script:Run.Edges -GroupSid "$script:DomainSid-515" `
            -MemberSid "$script:DomainSid-1601" -EdgeKind 'primary_group' | Should -BeTrue
    }

    It 'distinguishes it from an ordinary member edge' {
        $primary = @($script:Run.Edges | Where-Object { $_.edge_kind -eq 'primary_group' })
        $primary.Count | Should -BeGreaterThan 0
        foreach ($edge in $primary) {
            $edge.source_key | Should -Match '\|primary_group$'
        }
    }
}

Describe 'Large groups' {
    BeforeAll { $script:Run = Invoke-TestCollection }

    It 'reads a ranged member list to its end instead of stopping at the first chunk' {
        # The fixture returns three members at a time, the way a real DC returns 1500.
        $members = @($script:Run.Edges | Where-Object {
                $_.group_sid -eq "$script:DomainSid-1220" -and $_.edge_kind -eq 'directory_group_member'
            })
        $members.Count | Should -Be 10
    }

    It 'follows ranged retrieval across many chunks without losing or duplicating a member' {
        $members = 1..5000 | ForEach-Object { "CN=Member$_,OU=Bulk,$script:Nc" }
        $document = @{
            defaultNamingContext = $script:Nc
            domainSid            = $script:DomainSid
            rangeStep            = 1500
            entries              = @(
                @{
                    distinguishedName = "CN=Huge,$script:Groups"
                    attributes        = @{ objectClass = @('top', 'group'); objectSid = "$script:DomainSid-1999"; member = $members }
                }
            )
        }
        $provider = New-AdgFixtureDirectoryProvider -Document $document
        $entries = @(Invoke-AdgDirectorySearch -Provider $provider -SearchBase $script:Nc `
                -Filter '(objectClass=group)' -Attributes @('objectSid', 'member') -Scope 'Subtree')

        $collected = @(Get-AdgGroupMemberReference -Provider $provider -Entry $entries[0] -RangeStep 1500)

        $collected.Count | Should -Be 5000
        @($collected | Sort-Object -Unique).Count | Should -Be 5000
        $collected[0] | Should -Be "CN=Member1,OU=Bulk,$script:Nc"
        $collected[-1] | Should -Be "CN=Member5000,OU=Bulk,$script:Nc"
    }

    It 'recognizes an unranged attribute as complete' {
        $entry = New-AdgDirectoryEntry -DistinguishedName 'CN=Small' -Attributes @{ member = @('CN=One', 'CN=Two') }
        $state = Get-AdgRangedAttributeState -Entry $entry -Name 'member'
        $state.Complete | Should -BeTrue
        $state.Values.Count | Should -Be 2
    }

    It 'recognizes a truncated range as incomplete and says where to continue' {
        $entry = New-AdgDirectoryEntry -DistinguishedName 'CN=Big' -Attributes @{ 'member;range=0-1499' = @('CN=One') }
        $state = Get-AdgRangedAttributeState -Entry $entry -Name 'member'
        $state.Complete | Should -BeFalse
        $state.NextIndex | Should -Be 1500
    }

    It 'recognizes the final range' {
        $entry = New-AdgDirectoryEntry -DistinguishedName 'CN=Big' -Attributes @{ 'member;range=3000-*' = @('CN=Last') }
        (Get-AdgRangedAttributeState -Entry $entry -Name 'member').Complete | Should -BeTrue
    }
}

Describe 'Foreign security principals' {
    BeforeAll { $script:Run = Invoke-TestCollection }

    It 'records the edge to a principal from another forest' {
        Test-EdgePresent -Edges $script:Run.Edges -GroupSid "$script:DomainSid-1202" -MemberSid $script:ForeignSid |
            Should -BeTrue
    }

    It 'marks the edge as foreign' {
        $edge = $script:Run.Edges | Where-Object { $_.member_sid -eq $script:ForeignSid } | Select-Object -First 1
        $edge.is_foreign_security_principal | Should -BeTrue
    }

    It 'records the principal itself, so the edge points at something auditable' {
        $principal = $script:Run.Principals | Where-Object { $_.sid -eq $script:ForeignSid } | Select-Object -First 1
        $principal | Should -Not -BeNullOrEmpty
        $principal.principal_kind | Should -Be 'foreign_security_principal'
    }
}

Describe 'Principals' {
    BeforeAll { $script:Run = Invoke-TestCollection }

    It 'collects users, groups, computers, and managed service accounts' {
        $kinds = @($script:Run.Principals | ForEach-Object { $_.principal_kind } | Sort-Object -Unique)
        $kinds | Should -Contain 'user'
        $kinds | Should -Contain 'domain_group'
        $kinds | Should -Contain 'computer'
        $kinds | Should -Contain 'managed_service_account'
        $kinds | Should -Contain 'foreign_security_principal'
    }

    It 'reports a disabled account as disabled' {
        $bob = $script:Run.Principals | Where-Object { $_.sid -eq "$script:DomainSid-1105" } | Select-Object -First 1
        $bob.enabled | Should -BeFalse
    }

    It 'reports a distribution group as one, so it is not mistaken for a grant' {
        $group = $script:Run.Principals | Where-Object { $_.sid -eq "$script:DomainSid-1203" } | Select-Object -First 1
        $group.group_type | Should -Be 'distribution'
    }

    It 'gives each principal exactly one observation, however many times it was reached' {
        $keys = @($script:Run.Principals | ForEach-Object { $_.source_key })
        @($keys | Sort-Object -Unique).Count | Should -Be $keys.Count
    }

    It 'carries the same run id on every observation' {
        $runIds = @($script:Run.Observations | ForEach-Object { $_.run_id } | Sort-Object -Unique)
        $runIds.Count | Should -Be 1
        $runIds[0] | Should -Be $script:Run.Summary.RunId
    }
}

Describe 'Scope and reconciliation' {
    It 'reconciles the domain only after enumerating all of it cleanly' {
        $run = Invoke-TestCollection

        $run.Summary.Status | Should -Be 'succeeded'
        $run.Summary.ErrorCount | Should -Be 0
        $run.Start.incremental | Should -BeFalse
        $run.Completion.reconciled_scopes.Count | Should -Be 1
        $run.Completion.reconciled_scopes[0].kind | Should -Be 'domain'
        $run.Completion.reconciled_scopes[0].key | Should -Be $script:DomainSid.ToLowerInvariant()
    }

    It 'refuses to reconcile when the run was narrowed to some organizational units' {
        $run = Invoke-TestCollection -ConfigOverrides @{ IncludeOrganizationalUnits = @($script:Corp) }

        $run.Summary.Status | Should -Be 'succeeded'
        $run.Start.incremental | Should -BeTrue
        $run.Completion.reconciled_scopes.Count | Should -Be 0
    }

    It 'refuses to reconcile when a container was excluded' {
        $run = Invoke-TestCollection -ConfigOverrides @{ ExcludeOrganizationalUnits = @("OU=Service,$script:Corp") }

        $run.Start.incremental | Should -BeTrue
        $run.Completion.reconciled_scopes.Count | Should -Be 0
    }

    It 'does not enumerate an excluded container' {
        $run = Invoke-TestCollection -ConfigOverrides @{ ExcludeOrganizationalUnits = @("OU=Service,$script:Corp") }
        @($run.Principals | Where-Object { $_.sid -eq "$script:DomainSid-1301" }).Count | Should -Be 0
    }

    It 'still records a principal outside the enumerated scope that an in-scope group grants membership' {
        # Contractors lives in CN=Users, outside OU=Corp. Dropping it would leave an edge
        # pointing at a SID with nothing behind it, which cannot be audited.
        $run = Invoke-TestCollection -ConfigOverrides @{ IncludeOrganizationalUnits = @($script:Corp) }

        Test-EdgePresent -Edges $run.Edges -GroupSid "$script:DomainSid-1202" -MemberSid "$script:DomainSid-1250" |
            Should -BeTrue
        $contractors = $run.Principals | Where-Object { $_.sid -eq "$script:DomainSid-1250" } | Select-Object -First 1
        $contractors.principal_kind | Should -Be 'domain_group'
    }

    It 'declares the domain scope it intends to enumerate' {
        $run = Invoke-TestCollection
        $run.Start.scopes.Count | Should -Be 1
        $run.Start.scopes[0].kind | Should -Be 'domain'
    }
}

Describe 'Batching a run' {
    It 'splits a run into batches no larger than configured and counts them honestly' {
        $run = Invoke-TestCollection -ConfigOverrides @{ BatchSize = 5 }

        $run.Batches.Count | Should -Be $run.Completion.batch_count
        $run.Observations.Count | Should -Be $run.Completion.observation_count
        foreach ($batch in $run.Batches) {
            $batch.observations.Count | Should -BeLessOrEqual 5
        }
    }

    It 'numbers batches from one, without gaps' {
        $run = Invoke-TestCollection -ConfigOverrides @{ BatchSize = 5 }
        $sequences = @($run.Batches | ForEach-Object { $_.sequence } | Sort-Object)
        $sequences | Should -Be @(1..$run.Batches.Count)
    }

    It 'marks the last batch final and carries a cursor for diagnostics' {
        $run = Invoke-TestCollection -ConfigOverrides @{ BatchSize = 5 }
        $last = $run.Batches | Sort-Object sequence | Select-Object -Last 1
        $last.is_final | Should -BeTrue
        $last.continuation_token | Should -Match '^pass='
    }

    It 'gives every batch its own identifier' {
        $run = Invoke-TestCollection -ConfigOverrides @{ BatchSize = 5 }
        $ids = @($run.Batches | ForEach-Object { $_.batch_id })
        @($ids | Sort-Object -Unique).Count | Should -Be $ids.Count
    }
}

Describe 'Failures are reported, never hidden' {
    It 'reports an unreadable member and finishes partial without reconciling' {
        $document = @{
            defaultNamingContext = $script:Nc
            domainSid            = $script:DomainSid
            denied               = @("OU=Locked,$script:Corp")
            entries              = @(
                @{
                    distinguishedName = "CN=Finance-RW,$script:Groups"
                    attributes        = @{
                        objectClass = @('top', 'group'); objectSid = "$script:DomainSid-1202"
                        name        = 'Finance-RW'; groupType = -2147483644
                        member      = @("CN=Hidden,OU=Locked,$script:Corp")
                    }
                },
                @{
                    distinguishedName = "CN=Hidden,OU=Locked,$script:Corp"
                    attributes        = @{ objectClass = @('top', 'user'); objectSid = "$script:DomainSid-1400" }
                }
            )
        }
        $run = Invoke-TestCollection -Provider (New-AdgFixtureDirectoryProvider -Document $document)

        $run.Summary.Status | Should -Be 'partial'
        $run.Completion.reconciled_scopes.Count | Should -Be 0
        $run.Completion.error_count | Should -BeGreaterThan 0
        $run.Completion.errors[0].code | Should -Be 'member_unresolved'
        $run.Completion.errors[0].target | Should -Be "CN=Hidden,OU=Locked,$script:Corp"
    }

    It 'names the object and the operation in the error, not just that something failed' {
        $document = @{
            defaultNamingContext = $script:Nc
            domainSid            = $script:DomainSid
            denied               = @("OU=Locked,$script:Corp")
            entries              = @(
                @{
                    distinguishedName = "CN=Finance-RW,$script:Groups"
                    attributes        = @{
                        objectClass = @('top', 'group'); objectSid = "$script:DomainSid-1202"
                        groupType   = -2147483644; member = @("CN=Hidden,OU=Locked,$script:Corp")
                    }
                }
            )
        }
        $run = Invoke-TestCollection -Provider (New-AdgFixtureDirectoryProvider -Document $document)
        $run.Completion.errors[0].message | Should -Match 'CN=Finance-RW'
        $run.Completion.errors[0].message | Should -Match 'CN=Hidden'
    }

    It 'refuses to record a group as a member of itself, and says so' {
        $document = @{
            defaultNamingContext = $script:Nc
            domainSid            = $script:DomainSid
            entries              = @(
                @{
                    distinguishedName = "CN=Self,$script:Groups"
                    attributes        = @{
                        objectClass = @('top', 'group'); objectSid = "$script:DomainSid-1212"
                        groupType   = -2147483644; member = @("CN=Self,$script:Groups")
                    }
                }
            )
        }
        $run = Invoke-TestCollection -Provider (New-AdgFixtureDirectoryProvider -Document $document)

        $run.Edges.Count | Should -Be 0
        $run.Summary.Status | Should -Be 'partial'
        $run.Completion.errors[0].code | Should -Be 'self_membership'
    }

    It 'closes the run even when collection stops, rather than leaving it open' {
        $provider = @{
            Kind                 = 'Broken'
            Server               = 'dc01.corp.example.com'
            DomainSid            = $script:DomainSid
            DefaultNamingContext = $script:Nc
            DnsDomainName        = 'corp.example.com'
            SearchCommand        = { param($Request) throw [System.InvalidOperationException]::new('the provider exploded') }
        }
        $run = Invoke-TestCollection -Provider $provider

        $run.Completion | Should -Not -BeNullOrEmpty
        $run.Summary.Status | Should -Be 'partial'
        $run.Completion.reconciled_scopes.Count | Should -Be 0
    }
}

Describe 'Objects renamed while the scan is running' {
    BeforeAll {
        function New-RenamingProvider {
            <#
                A directory in which one member is renamed between the first read of a
                group's membership and the attempt to resolve it -- the race a long scan
                runs into on a busy domain.
            #>
            $groupDn = "CN=Finance-Team,$script:Groups"
            $oldDn = "CN=Alice Smith,$script:Staff"
            $newDn = "CN=Alice Cooper,$script:Staff"
            $userSid = "$script:DomainSid-1104"
            $groupSid = "$script:DomainSid-1201"
            $nc = $script:Nc

            $search = {
                param([hashtable] $Request)

                $normalized = ConvertTo-AdgNormalizedDistinguishedName $Request.SearchBase

                if ($Request.Scope -eq 'Base') {
                    if ($normalized -eq (ConvertTo-AdgNormalizedDistinguishedName $oldDn)) {
                        # Gone under the name the first read reported.
                        throw [System.IO.DirectoryNotFoundException]::new("There is no object at '$oldDn'.")
                    }
                    if ($normalized -eq (ConvertTo-AdgNormalizedDistinguishedName $newDn)) {
                        return @(New-AdgDirectoryEntry -DistinguishedName $newDn -Attributes @{
                                objectClass        = @('top', 'person', 'organizationalPerson', 'user')
                                objectSid          = @($userSid)
                                sAMAccountName     = @('asmith')
                                displayName        = @('Alice Cooper')
                                userAccountControl = @('512')
                            })
                    }
                    if ($normalized -eq (ConvertTo-AdgNormalizedDistinguishedName $groupDn)) {
                        # Active Directory rewrites member DNs on rename, so the re-read
                        # returns the new name.
                        return @(New-AdgDirectoryEntry -DistinguishedName $groupDn -Attributes @{ member = @($newDn) })
                    }
                    throw [System.IO.DirectoryNotFoundException]::new("There is no object at '$($Request.SearchBase)'.")
                }

                $attributes = @{
                    objectClass = @('top', 'group')
                    objectSid   = @($groupSid)
                    name        = @('Finance-Team')
                    groupType   = @('-2147483646')
                }
                if ($Request.Attributes -contains 'member') { $attributes['member'] = @($oldDn) }
                return @(New-AdgDirectoryEntry -DistinguishedName $groupDn -Attributes $attributes)
            }.GetNewClosure()

            return @{
                Kind                 = 'Renaming'
                Server               = 'dc01.corp.example.com'
                DomainSid            = $script:DomainSid
                DefaultNamingContext = $nc
                DnsDomainName        = 'corp.example.com'
                SearchCommand        = $search
            }
        }
    }

    It 'recovers the edge by re-reading the membership, because the SID never changed' {
        $run = Invoke-TestCollection -Provider (New-RenamingProvider)

        Test-EdgePresent -Edges $run.Edges -GroupSid "$script:DomainSid-1201" -MemberSid "$script:DomainSid-1104" |
            Should -BeTrue
        $run.Summary.Status | Should -Be 'succeeded'
        $run.Summary.ErrorCount | Should -Be 0
    }

    It 'records the principal under the name it now has' {
        $run = Invoke-TestCollection -Provider (New-RenamingProvider)
        $alice = $run.Principals | Where-Object { $_.sid -eq "$script:DomainSid-1104" } | Select-Object -First 1
        $alice.display_name | Should -Be 'Alice Cooper'
    }
}

Describe 'Transient directory failures' {
    It 'retries a domain controller that was briefly unavailable' {
        $global:AdgTestAttempts = 0
        Mock -CommandName Start-Sleep -ModuleName AdgCollector.ActiveDirectory -MockWith { }

        $result = Invoke-AdgDirectoryOperation -MaxAttempts 3 -Description 'test search' -Operation {
            $global:AdgTestAttempts++
            if ($global:AdgTestAttempts -lt 3) {
                throw [System.DirectoryServices.Protocols.LdapException]::new(81, 'Server Down')
            }
            return 'collected'
        }

        $result | Should -Be 'collected'
        $global:AdgTestAttempts | Should -Be 3
        Remove-Variable -Name AdgTestAttempts -Scope Global -ErrorAction SilentlyContinue
    }

    It 'does not retry a permissions failure' {
        $global:AdgTestAttempts = 0
        Mock -CommandName Start-Sleep -ModuleName AdgCollector.ActiveDirectory -MockWith { }

        { Invoke-AdgDirectoryOperation -MaxAttempts 5 -Description 'test search' -Operation {
                $global:AdgTestAttempts++
                throw [System.UnauthorizedAccessException]::new('access denied')
            } } | Should -Throw '*access denied*'

        $global:AdgTestAttempts | Should -Be 1
        Remove-Variable -Name AdgTestAttempts -Scope Global -ErrorAction SilentlyContinue
    }
}

Describe 'The change watermark' {
    It 'records the highest uSNChanged seen, and which server issued it' {
        $statePath = Join-Path $TestDrive 'state-clean.json'
        $run = Invoke-TestCollection -ConfigOverrides @{ StateFile = $statePath }

        $run.Summary.Status | Should -Be 'succeeded'
        Test-Path -LiteralPath $statePath | Should -BeTrue

        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $state.status | Should -Be 'succeeded'
        $state.directory_server | Should -Be 'dc01.corp.example.com'
        $state.highest_usn_changed | Should -Be 41204
        $state.domain_sid | Should -Be $script:DomainSid
    }

    It 'writes no watermark for a run that did not read everything' {
        $statePath = Join-Path $TestDrive 'state-partial.json'
        $document = @{
            defaultNamingContext = $script:Nc
            domainSid            = $script:DomainSid
            denied               = @("OU=Locked,$script:Corp")
            entries              = @(
                @{
                    distinguishedName = "CN=Finance-RW,$script:Groups"
                    attributes        = @{
                        objectClass = @('top', 'group'); objectSid = "$script:DomainSid-1202"
                        groupType   = -2147483644; uSNChanged = 99999
                        member      = @("CN=Hidden,OU=Locked,$script:Corp")
                    }
                }
            )
        }
        $run = Invoke-TestCollection -ConfigOverrides @{ StateFile = $statePath } `
            -Provider (New-AdgFixtureDirectoryProvider -Document $document)

        # A watermark from a partial run would make the next run skip exactly the objects
        # this one could not read, and nothing would ever look missing.
        $run.Summary.Status | Should -Be 'partial'
        Test-Path -LiteralPath $statePath | Should -BeFalse
    }
}
