#Requires -Version 7.0
<#
    Turning one directory object into one observation.

    Everything under test here is pure: an entry goes in, a payload comes out. These are
    the functions that decide whether a computer account is reported as a computer, whether
    a distribution group is reported as one, and whether an excluded container actually
    excludes anything -- decisions that change how every grant the object holds is read.
#>

BeforeAll {
    $repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
    Import-Module (Join-Path $repoRoot 'collector/powershell/common/AdgCollector.Common.psd1') -Force
    Import-Module (Join-Path $repoRoot 'collector/powershell/ad/AdgCollector.ActiveDirectory.psd1') -Force

    $script:RunId = '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31'
    $script:DomainSid = 'S-1-5-21-1004336348-1177238915-682003330'
    $script:Nc = 'DC=corp,DC=example,DC=com'

    function New-TestEntry {
        param([string] $Dn, [hashtable] $Attributes)
        New-AdgDirectoryEntry -DistinguishedName $Dn -Attributes $Attributes
    }
}

Describe 'Distinguished names' {
    It 'folds case and insignificant whitespace' {
        ConvertTo-AdgNormalizedDistinguishedName 'CN=Alice, OU=Staff,  DC=Corp,DC=Example' |
            Should -Be 'cn=alice,ou=staff,dc=corp,dc=example'
    }

    It 'does not split on an escaped comma, which is part of a value' {
        ConvertTo-AdgNormalizedDistinguishedName 'CN=Smith\, Alice,OU=Staff,DC=corp' |
            Should -Be 'cn=smith\, alice,ou=staff,dc=corp'
    }

    Context 'Containment' {
        It 'matches an object inside the container' {
            Test-AdgDistinguishedNameUnder -DistinguishedName "CN=Alice,OU=Staff,$script:Nc" -Container "OU=Staff,$script:Nc" |
                Should -BeTrue
        }

        It 'matches the container itself' {
            Test-AdgDistinguishedNameUnder -DistinguishedName "OU=Staff,$script:Nc" -Container "OU=Staff,$script:Nc" |
                Should -BeTrue
        }

        It 'matches a nested container' {
            Test-AdgDistinguishedNameUnder -DistinguishedName "CN=Bob,OU=Temp,OU=Staff,$script:Nc" -Container "OU=Staff,$script:Nc" |
                Should -BeTrue
        }

        It 'requires the suffix to land on a component boundary' {
            # 'OU=Finance' must not be treated as living inside 'OU=nance'.
            Test-AdgDistinguishedNameUnder -DistinguishedName "OU=Finance,$script:Nc" -Container "OU=nance,$script:Nc" |
                Should -BeFalse
        }

        It 'does not match a sibling container' {
            Test-AdgDistinguishedNameUnder -DistinguishedName "CN=Alice,OU=Staff,$script:Nc" -Container "OU=Service,$script:Nc" |
                Should -BeFalse
        }

        It 'tolerates the spacing difference between two spellings of the same DN' {
            Test-AdgDistinguishedNameUnder -DistinguishedName "CN=Alice, OU=Staff, $script:Nc" -Container "OU=Staff,$script:Nc" |
                Should -BeTrue
        }
    }

    Context 'Foreign security principals' {
        It 'reads the SID out of the stub object name' {
            Get-AdgForeignSecurityPrincipalSid -DistinguishedName "CN=S-1-5-21-99-88-77-1234,CN=ForeignSecurityPrincipals,$script:Nc" |
                Should -Be 'S-1-5-21-99-88-77-1234'
        }

        It 'returns nothing for an ordinary object' {
            Get-AdgForeignSecurityPrincipalSid -DistinguishedName "CN=Alice,OU=Staff,$script:Nc" |
                Should -BeNullOrEmpty
        }
    }
}

Describe 'Attribute reading' {
    BeforeAll {
        $script:Entry = New-TestEntry -Dn "CN=Alice,$script:Nc" -Attributes @{
            sAMAccountName     = @('asmith')
            objectClass        = @('top', 'person', 'user')
            userAccountControl = @('514')
            groupType          = @('-2147483646')
            isDeleted          = @('TRUE')
            emptyish           = @('')
        }
    }

    It 'looks attributes up without regard to case, as LDAP does' {
        Get-AdgEntryValue -Entry $script:Entry -Name 'samaccountname' | Should -Be 'asmith'
        Get-AdgEntryValue -Entry $script:Entry -Name 'SAMACCOUNTNAME' | Should -Be 'asmith'
    }

    It 'returns nothing for an absent attribute instead of failing' {
        Get-AdgEntryValue -Entry $script:Entry -Name 'nothingHere' | Should -BeNullOrEmpty
        @(Get-AdgEntryValues -Entry $script:Entry -Name 'nothingHere').Count | Should -Be 0
    }

    It 'treats an empty value as "the source did not say"' {
        Get-AdgEntryValue -Entry $script:Entry -Name 'emptyish' | Should -BeNullOrEmpty
    }

    It 'parses an integer the directory delivered as a string' {
        Get-AdgEntryInteger -Entry $script:Entry -Name 'userAccountControl' | Should -Be 514
    }

    It 'masks a signed groupType back to its unsigned value, where the security bit lives' {
        # 0x80000002 arrives as -2147483646 because groupType is a signed 32-bit integer.
        Get-AdgEntryInteger -Entry $script:Entry -Name 'groupType' | Should -Be 2147483650
    }

    It 'reads the directory spelling of a boolean' {
        Get-AdgEntryBoolean -Entry $script:Entry -Name 'isDeleted' | Should -BeTrue
    }
}

Describe 'Classification' {
    Context 'Principal kind' {
        It 'reports a plain account as a user' {
            $entry = New-TestEntry -Dn 'CN=Alice' -Attributes @{ objectClass = @('top', 'person', 'organizationalPerson', 'user') }
            Get-AdgPrincipalKindFromEntry -Entry $entry | Should -Be 'user'
        }

        It 'reports a computer as a computer, though it also carries objectClass=user' {
            $entry = New-TestEntry -Dn 'CN=FS01' -Attributes @{ objectClass = @('top', 'person', 'organizationalPerson', 'user', 'computer') }
            Get-AdgPrincipalKindFromEntry -Entry $entry | Should -Be 'computer'
        }

        It 'reports a group managed service account as one, not as a computer' {
            $entry = New-TestEntry -Dn 'CN=gmsa' -Attributes @{
                objectClass = @('top', 'person', 'organizationalPerson', 'user', 'computer', 'msDS-GroupManagedServiceAccount')
            }
            Get-AdgPrincipalKindFromEntry -Entry $entry | Should -Be 'managed_service_account'
        }

        It 'reports a group as a domain group' {
            $entry = New-TestEntry -Dn 'CN=Finance' -Attributes @{ objectClass = @('top', 'group') }
            Get-AdgPrincipalKindFromEntry -Entry $entry | Should -Be 'domain_group'
        }

        It 'reports a foreign security principal as one' {
            $entry = New-TestEntry -Dn 'CN=S-1-5-21-1-2-3-4' -Attributes @{ objectClass = @('top', 'foreignSecurityPrincipal') }
            Get-AdgPrincipalKindFromEntry -Entry $entry | Should -Be 'foreign_security_principal'
        }

        It 'reports an object it cannot classify as unresolved rather than guessing' {
            $entry = New-TestEntry -Dn 'CN=Odd' -Attributes @{ objectClass = @('top', 'msExchSomething') }
            Get-AdgPrincipalKindFromEntry -Entry $entry | Should -Be 'unresolved'
        }
    }

    Context 'Group scope and type' {
        It 'maps a global security group' {
            Get-AdgGroupScopeFromType 2147483650 | Should -Be 'global'
            Get-AdgGroupTypeFromType 2147483650 | Should -Be 'security'
        }

        It 'maps a domain local security group' {
            Get-AdgGroupScopeFromType 2147483652 | Should -Be 'domain_local'
        }

        It 'maps a universal security group' {
            Get-AdgGroupScopeFromType 2147483656 | Should -Be 'universal'
        }

        It 'maps a builtin local group' {
            Get-AdgGroupScopeFromType 2147483649 | Should -Be 'builtin_local'
        }

        It 'reports a distribution group as one, because it never grants access' {
            Get-AdgGroupTypeFromType 2 | Should -Be 'distribution'
            Get-AdgGroupScopeFromType 2 | Should -Be 'global'
        }

        It 'says unknown when the directory did not say, never "distribution"' {
            Get-AdgGroupTypeFromType $null | Should -Be 'unknown'
            Get-AdgGroupScopeFromType $null | Should -Be 'unknown'
        }
    }

    Context 'Enabled state' {
        It 'reads the disable bit' {
            Test-AdgAccountEnabled 514 | Should -BeFalse
            Test-AdgAccountEnabled 512 | Should -BeTrue
        }

        It 'says nothing when there is no userAccountControl, as on a group' {
            Test-AdgAccountEnabled $null | Should -BeNullOrEmpty
        }
    }

    Context 'Domain SIDs' {
        It 'strips the RID from a domain principal SID' {
            Get-AdgDomainSidFromSid "$script:DomainSid-1104" | Should -Be $script:DomainSid
        }

        It 'returns nothing for a BUILTIN SID, which no domain issued' {
            Get-AdgDomainSidFromSid 'S-1-5-32-544' | Should -BeNullOrEmpty
        }

        It 'returns nothing for a well-known SID' {
            Get-AdgDomainSidFromSid 'S-1-1-0' | Should -BeNullOrEmpty
        }

        It 'composes a group SID from a domain SID and a RID' {
            Get-AdgSidWithRid -DomainSid $script:DomainSid -Rid 513 | Should -Be "$script:DomainSid-513"
        }
    }
}

Describe 'Principal observations from directory entries' {
    It 'translates a user, keeping the SID as identity and the name as metadata' {
        $entry = New-TestEntry -Dn "CN=Alice Smith,OU=Staff,$script:Nc" -Attributes @{
            objectClass        = @('top', 'person', 'organizationalPerson', 'user')
            objectSid          = @("$script:DomainSid-1104")
            sAMAccountName     = @('asmith')
            userPrincipalName  = @('asmith@corp.example.com')
            displayName        = @('Alice Smith')
            userAccountControl = @('512')
        }
        $observation = ConvertTo-AdgPrincipalObservationFromEntry -Entry $entry -RunId $script:RunId

        $observation['kind'] | Should -Be 'principal'
        $observation['principal_kind'] | Should -Be 'user'
        $observation['sid'] | Should -Be "$script:DomainSid-1104"
        $observation['source_key'] | Should -Be "principal|$script:DomainSid-1104"
        $observation['domain_sid'] | Should -Be $script:DomainSid
        $observation['sam_account_name'] | Should -Be 'asmith'
        $observation['user_principal_name'] | Should -Be 'asmith@corp.example.com'
        $observation['display_name'] | Should -Be 'Alice Smith'
        $observation['distinguished_name'] | Should -Be "CN=Alice Smith,OU=Staff,$script:Nc"
        $observation['enabled'] | Should -BeTrue
    }

    It 'reports a disabled account as disabled' {
        $entry = New-TestEntry -Dn "CN=Bob,$script:Nc" -Attributes @{
            objectClass        = @('top', 'user')
            objectSid          = @("$script:DomainSid-1105")
            userAccountControl = @('514')
        }
        (ConvertTo-AdgPrincipalObservationFromEntry -Entry $entry -RunId $script:RunId)['enabled'] | Should -BeFalse
    }

    It 'gives a group its scope and type and no enabled flag' {
        $entry = New-TestEntry -Dn "CN=Finance-RW,$script:Nc" -Attributes @{
            objectClass = @('top', 'group')
            objectSid   = @("$script:DomainSid-1202")
            groupType   = @('-2147483644')
            name        = @('Finance-RW')
        }
        $observation = ConvertTo-AdgPrincipalObservationFromEntry -Entry $entry -RunId $script:RunId

        $observation['group_scope'] | Should -Be 'domain_local'
        $observation['group_type'] | Should -Be 'security'
        $observation.Contains('enabled') | Should -BeFalse
    }

    It 'falls back to name when there is no displayName' {
        $entry = New-TestEntry -Dn "CN=Ring-A,$script:Nc" -Attributes @{
            objectClass = @('top', 'group')
            objectSid   = @("$script:DomainSid-1210")
            name        = @('Ring-A')
        }
        (ConvertTo-AdgPrincipalObservationFromEntry -Entry $entry -RunId $script:RunId)['display_name'] | Should -Be 'Ring-A'
    }

    It 'carries a deleted object through as deleted' {
        $entry = New-TestEntry -Dn "CN=Gone,$script:Nc" -Attributes @{
            objectClass = @('top', 'user')
            objectSid   = @("$script:DomainSid-1900")
            isDeleted   = @('TRUE')
        }
        (ConvertTo-AdgPrincipalObservationFromEntry -Entry $entry -RunId $script:RunId)['is_deleted'] | Should -BeTrue
    }

    It 'refuses an object with no SID rather than keying a principal by name' {
        $entry = New-TestEntry -Dn "CN=Nameless,$script:Nc" -Attributes @{
            objectClass    = @('top', 'user')
            sAMAccountName = @('nameless')
        }
        { ConvertTo-AdgPrincipalObservationFromEntry -Entry $entry -RunId $script:RunId } |
            Should -Throw '*has no objectSid*'
    }

    It 'keeps an unclassifiable object name as last_known_name, never as display_name' {
        $entry = New-TestEntry -Dn "CN=Odd,$script:Nc" -Attributes @{
            objectClass = @('top', 'msExchSomething')
            objectSid   = @("$script:DomainSid-1950")
            displayName = @('Odd Object')
        }
        $observation = ConvertTo-AdgPrincipalObservationFromEntry -Entry $entry -RunId $script:RunId

        $observation['principal_kind'] | Should -Be 'unresolved'
        $observation['last_known_name'] | Should -Be 'Odd Object'
        $observation.Contains('display_name') | Should -BeFalse
    }

    It 'reports no derived access anywhere in the payload' {
        $entry = New-TestEntry -Dn "CN=Alice,$script:Nc" -Attributes @{
            objectClass = @('top', 'user')
            objectSid   = @("$script:DomainSid-1104")
        }
        $json = ConvertTo-AdgPrincipalObservationFromEntry -Entry $entry -RunId $script:RunId | ConvertTo-Json -Depth 6
        foreach ($forbidden in @('effective', 'can_access', 'resolved_access', 'permission')) {
            $json | Should -Not -Match $forbidden
        }
    }
}

Describe 'Change metadata' {
    It 'captures uSNChanged and whenChanged for a future incremental run' {
        $entry = New-TestEntry -Dn "CN=Alice,$script:Nc" -Attributes @{
            uSNChanged  = @('41001')
            whenChanged = @('20260914080000.0Z')
        }
        $metadata = Get-AdgEntryChangeMetadata -Entry $entry

        $metadata.UsnChanged | Should -Be 41001
        $metadata.WhenChanged | Should -Be '20260914080000.0Z'
    }
}

Describe 'Configuration' {
    It 'applies defaults and keeps the batch size inside the contract limit' {
        $config = New-AdgAdCollectorConfig -Offline $true -OutputDirectory 'C:\temp\adg'
        $config.BatchSize | Should -Be 500
        $config.RangeStep | Should -Be 1500
        $config.ApiTokenEnvironmentVariable | Should -Be 'ADG_COLLECTOR_TOKEN'
        $config.CollectorHost | Should -Not -BeNullOrEmpty
    }

    It 'refuses a batch size the contract would reject' {
        { New-AdgAdCollectorConfig -Offline $true -OutputDirectory 'C:\temp\adg' -BatchSize 5000 } |
            Should -Throw '*between 1 and 1000*'
    }

    It 'refuses offline collection with nowhere to write' {
        { New-AdgAdCollectorConfig -Offline $true } | Should -Throw '*needs OutputDirectory*'
    }

    It 'refuses online collection with no API' {
        { New-AdgAdCollectorConfig } | Should -Throw '*needs ApiBaseUrl*'
    }

    It 'refuses an include scope that is not a distinguished name' {
        { New-AdgAdCollectorConfig -Offline $true -OutputDirectory 'C:\temp\adg' -IncludeOrganizationalUnits @('Staff') } |
            Should -Throw '*is not a distinguished name*'
    }

    Context 'Loading a file' {
        It 'reads the shipped example' {
            $repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
            $example = Join-Path $repoRoot 'collector/powershell/ad/config/adg-ad-collector.example.json'
            $config = Import-AdgAdCollectorConfig -Path $example

            $config.ApiBaseUrl | Should -Be 'https://adg.corp.example.com'
            $config.BatchSize | Should -Be 500
            $config.ApiTokenEnvironmentVariable | Should -Be 'ADG_COLLECTOR_TOKEN'
        }

        It 'refuses an unknown key, which would silently drop an exclusion' {
            $path = Join-Path $TestDrive 'typo.json'
            '{"apiBaseUrl":"https://adg.local","excludeOrganisationalUnits":["OU=Service,DC=corp"]}' |
                Set-Content -LiteralPath $path -Encoding utf8NoBOM
            { Import-AdgAdCollectorConfig -Path $path } | Should -Throw '*Unknown configuration key*'
        }

        It 'refuses a URL that carries what looks like a secret' {
            $path = Join-Path $TestDrive 'secret.json'
            '{"apiBaseUrl":"https://adg.local?token=hunter2"}' | Set-Content -LiteralPath $path -Encoding utf8NoBOM
            { Import-AdgAdCollectorConfig -Path $path } | Should -Throw '*secrets are never stored in configuration*'
        }

        It 'names the file when it does not exist' {
            { Import-AdgAdCollectorConfig -Path (Join-Path $TestDrive 'missing.json') } |
                Should -Throw '*does not exist*'
        }
    }
}

Describe 'Transient directory failures' {
    It 'retries a server that was unreachable' {
        $record = [System.Management.Automation.ErrorRecord]::new(
            [System.DirectoryServices.Protocols.LdapException]::new(81, 'Server Down'), 'ldap', 'ConnectionError', $null)
        Test-AdgTransientDirectoryFailure -ErrorRecord $record | Should -BeTrue
    }

    It 'does not retry a permissions failure, which would hide the real finding' {
        $record = [System.Management.Automation.ErrorRecord]::new(
            [System.UnauthorizedAccessException]::new('access denied'), 'acl', 'PermissionDenied', $null)
        Test-AdgTransientDirectoryFailure -ErrorRecord $record | Should -BeFalse
    }

    It 'does not retry a missing object' {
        $record = [System.Management.Automation.ErrorRecord]::new(
            [System.IO.DirectoryNotFoundException]::new('no such object'), 'dn', 'ObjectNotFound', $null)
        Test-AdgTransientDirectoryFailure -ErrorRecord $record | Should -BeFalse
    }
}

Describe 'Collector state' {
    BeforeAll {
        $script:GoodState = @{
            directory_server    = 'dc01.corp.example.com'
            status              = 'succeeded'
            incremental         = $false
            highest_usn_changed = 41206
        }
    }

    It 'accepts a watermark from the same server after a clean run' {
        (Test-AdgAdCollectorStateUsable -State $script:GoodState -Server 'dc01.corp.example.com').Usable |
            Should -BeTrue
    }

    It 'refuses a watermark from a different server, because USNs are per-server counters' {
        $result = Test-AdgAdCollectorStateUsable -State $script:GoodState -Server 'dc02.corp.example.com'
        $result.Usable | Should -BeFalse
        $result.Reason | Should -Match 'per-server counter'
    }

    It 'refuses a watermark left by a run that did not succeed' {
        $state = @{ directory_server = 'dc01.corp.example.com'; status = 'partial'; highest_usn_changed = 41206 }
        (Test-AdgAdCollectorStateUsable -State $state -Server 'dc01.corp.example.com').Usable | Should -BeFalse
    }

    It 'refuses a watermark left by an incremental run, which skipped part of the tree' {
        $state = @{
            directory_server    = 'dc01.corp.example.com'
            status              = 'succeeded'
            incremental         = $true
            highest_usn_changed = 41206
        }
        $result = Test-AdgAdCollectorStateUsable -State $state -Server 'dc01.corp.example.com'

        $result.Usable | Should -BeFalse
        $result.Reason | Should -Match 'would be skipped forever'
    }

    It 'refuses a state with no watermark in it' {
        $state = @{ directory_server = 'dc01.corp.example.com'; status = 'succeeded' }
        (Test-AdgAdCollectorStateUsable -State $state -Server 'dc01.corp.example.com').Usable | Should -BeFalse
    }
}
