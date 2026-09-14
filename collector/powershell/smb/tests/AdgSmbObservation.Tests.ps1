#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Normalization: raw SMB readings to contract v1 observations.

    This is where a collector most easily starts inventing facts, so it is tested hardest.
    The properties that matter are not "does it produce output" but "does it refuse to
    produce output it cannot justify": an ACE with no usable trustee, a permission level
    the contract cannot name, an audit entry that governs logging rather than access.
#>

BeforeAll {
    $moduleRoot = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $moduleRoot 'AdgSmbCollector.psd1') -Force

    $script:RunId = '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31'

    function New-TestShare {
        param(
            [string] $Name = 'Finance',
            [string] $ShareType = 'FileSystemDirectory',
            [object] $Special = $false,
            [string] $Path = 'D:\Shares\Finance',
            [string] $Description = 'Finance department share',
            [object] $ConcurrentUserLimit = 0,
            [string] $CachingMode = 'Manual'
        )
        return [pscustomobject]@{
            Name                = $Name
            ShareType           = $ShareType
            Special             = $Special
            Path                = $Path
            Description         = $Description
            ConcurrentUserLimit = $ConcurrentUserLimit
            CachingMode         = $CachingMode
        }
    }

    function New-TestAce {
        param(
            [int] $AceType = 0,
            [int] $AceFlags = 0,
            [long] $AccessMask = 1179817,
            [string] $SidString = 'S-1-5-11',
            [string] $TrusteeName = 'Authenticated Users',
            [switch] $NoTrustee
        )
        $trustee = if ($NoTrustee) {
            $null
        }
        else {
            [pscustomobject]@{ SIDString = $SidString; Name = $TrusteeName; Domain = 'NT AUTHORITY' }
        }
        return [pscustomobject]@{
            AceType    = $AceType
            AceFlags   = $AceFlags
            AccessMask = $AccessMask
            Trustee    = $trustee
        }
    }
}

AfterAll {
    Remove-Module AdgSmbCollector -Force -ErrorAction SilentlyContinue
}

Describe 'Source keys' {

    Context 'they must match backend/app/contracts/v1/keys.py exactly' {

        It 'case-folds the host in a server key' {
            Get-AdgServerKey 'FS01' | Should -BeExactly 'server|fs01'
        }

        It 'case-folds both halves of a share key' {
            Get-AdgShareKey 'FS01' 'Finance' | Should -BeExactly 'share|fs01|finance'
        }

        It 'puts the permission level in an SMB ACE key' {
            Get-AdgSmbAceKey -ServerName 'FS01' -ShareName 'Finance' -TrusteeSid 'S-1-1-0' `
                -AceType 'allow' -Permission 'full' |
                Should -BeExactly 'smb_ace|fs01|finance|S-1-1-0|allow|full'
        }

        It 'formats a mask as eight lower-case hexadecimal digits' {
            Get-AdgSmbAceKey -ServerName 'FS01' -ShareName 'Finance-RO' -TrusteeSid 'S-1-5-11' `
                -AceType 'allow' -AccessMask 1179817 |
                Should -BeExactly 'smb_ace|fs01|finance-ro|S-1-5-11|allow|0x001200a9'
        }

        It 'refuses to key an ACE that claims both right forms' {
            # The two forms are different readings of one entry. A key mixing them would
            # be a key no other implementation could reproduce.
            { Get-AdgSmbAceKey -ServerName 'FS01' -ShareName 'F' -TrusteeSid 'S-1-1-0' `
                    -AceType 'allow' -Permission 'full' -AccessMask 1 } | Should -Throw '*never both*'
        }

        It 'refuses to key an ACE with neither right form' {
            { Get-AdgSmbAceKey -ServerName 'FS01' -ShareName 'F' -TrusteeSid 'S-1-1-0' -AceType 'allow' } |
                Should -Throw '*exactly one right form*'
        }

        It 'keys an unresolved principal globally, not per host' {
            Get-AdgPrincipalKey 'S-1-5-21-1-2-3-4444' 'unresolved' | Should -BeExactly 'principal|S-1-5-21-1-2-3-4444'
        }
    }
}

Describe 'Derived values the contract deliberately omits' {

    It 'derives the UNC path from the two fields that identify the share' {
        Get-AdgShareUncPath -ServerName 'FS01' -ShareName 'Finance' | Should -BeExactly '\\FS01\Finance'
    }
}

Describe 'Enumeration mapping' {

    Context 'share types' {

        It 'maps <Raw> to <Expected>' -ForEach @(
            @{ Raw = 'FileSystemDirectory'; Expected = 'disk' }
            @{ Raw = 0; Expected = 'disk' }
            @{ Raw = 'PrintQueue'; Expected = 'print' }
            @{ Raw = 'CommunicationDevice'; Expected = 'device' }
            @{ Raw = 'InterprocessCommunication'; Expected = 'ipc' }
            @{ Raw = 3; Expected = 'ipc' }
        ) {
            ConvertTo-AdgShareType $Raw | Should -Be $Expected
        }

        It 'says unknown rather than guessing for a DFS referral share' {
            # SimpleReferral has no value in the contract enumeration. 'unknown' states
            # that the source said something the contract cannot name; 'disk' would be a
            # claim that a directory is published, which it is not.
            ConvertTo-AdgShareType 'SimpleReferral' | Should -Be 'unknown'
            ConvertTo-AdgShareType $null | Should -Be 'unknown'
        }
    }

    Context 'share permission levels' {

        It 'maps the three levels the contract names' {
            ConvertTo-AdgSharePermission 'Full' | Should -Be 'full'
            ConvertTo-AdgSharePermission 'Change' | Should -Be 'change'
            ConvertTo-AdgSharePermission 'Read' | Should -Be 'read'
        }

        It 'returns nothing for Custom rather than rounding it to a level' {
            # Custom means the ACL holds a mask that is not one of the three, and the
            # level API does not say what it is. Guessing would over- or under-state
            # access, and either is a wrong audit answer.
            ConvertTo-AdgSharePermission 'Custom' | Should -BeNullOrEmpty
        }
    }

    Context 'ACE types' {

        It 'maps allow and deny' {
            ConvertTo-AdgAceType 0 | Should -Be 'allow'
            ConvertTo-AdgAceType 'AccessAllowed' | Should -Be 'allow'
            ConvertTo-AdgAceType 1 | Should -Be 'deny'
        }

        It 'returns nothing for an audit entry' {
            # A SACL entry governs logging, not access. Reporting one as a DACL entry
            # would fabricate access that does not exist.
            ConvertTo-AdgAceType 2 | Should -BeNullOrEmpty
            ConvertTo-AdgAceType 3 | Should -BeNullOrEmpty
        }
    }
}

Describe 'ConvertTo-AdgShareObservation' {

    It 'carries the fields the contract defines, and the new is_special' {
        $observation = ConvertTo-AdgShareObservation -RunId $script:RunId -ServerName 'FS01' -Share (New-TestShare)

        $observation.kind | Should -Be 'smb_share'
        $observation.source_key | Should -BeExactly 'share|fs01|finance'
        $observation.server_name | Should -Be 'FS01'
        $observation.share_name | Should -Be 'Finance'
        $observation.local_path | Should -Be 'D:\Shares\Finance'
        $observation.share_type | Should -Be 'disk'
        $observation.is_special | Should -BeFalse
    }

    It 'omits a local path that is not a drive-letter path' {
        # IPC$ has no directory. The contract's localPath pattern requires a drive letter,
        # so an empty or odd path is omitted rather than coerced into something invalid.
        $share = New-TestShare -Name 'IPC$' -ShareType 'InterprocessCommunication' -Special $true -Path ''
        $observation = ConvertTo-AdgShareObservation -RunId $script:RunId -ServerName 'FS01' -Share $share

        $observation.Contains('local_path') | Should -BeFalse
        $observation.share_type | Should -Be 'ipc'
        $observation.is_special | Should -BeTrue
    }

    It 'leaves is_special unset when the source did not say' {
        # Absent is not false. A server that did not report the flag has not told us the
        # share is ordinary.
        $share = New-TestShare -Special $null
        $observation = ConvertTo-AdgShareObservation -RunId $script:RunId -ServerName 'FS01' -Share $share

        $observation.Contains('is_special') | Should -BeFalse
    }

    It 'survives a source object missing properties a newer Windows would have' {
        $sparse = [pscustomobject]@{ Name = 'Legacy'; ShareType = 0 }
        $observation = ConvertTo-AdgShareObservation -RunId $script:RunId -ServerName 'FS01' -Share $sparse

        $observation.share_name | Should -Be 'Legacy'
        $observation.Contains('caching_mode') | Should -BeFalse
    }

    It 'refuses to build an observation with no share name' {
        { ConvertTo-AdgShareObservation -RunId $script:RunId -ServerName 'FS01' -Share ([pscustomobject]@{ Path = 'D:\x' }) } |
            Should -Throw '*needs a share name*'
    }
}

Describe 'ConvertTo-AdgShareAceObservation (descriptor reading)' {

    It 'reports the raw mask, unexpanded, with exactly one right form' {
        $result = ConvertTo-AdgShareAceObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
            -Dacl @(New-TestAce -AccessMask 1179817)

        $ace = $result.Observations[0]
        $ace.kind | Should -Be 'smb_ace'
        $ace.access_mask | Should -Be 1179817
        $ace.Contains('permission') | Should -BeFalse
        $ace.trustee_sid | Should -Be 'S-1-5-11'
        $ace.ace_type | Should -Be 'allow'
        $ace.order_index | Should -Be 0
    }

    It 'preserves a generic-rights mask instead of translating it' {
        # GENERIC_ALL. Expanding it here would destroy the evidence of what was written.
        $result = ConvertTo-AdgShareAceObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
            -Dacl @(New-TestAce -AccessMask 0x10000000)

        $result.Observations[0].access_mask | Should -Be 268435456
    }

    It 'handles a mask with the high bit set without overflowing' {
        $result = ConvertTo-AdgShareAceObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
            -Dacl @(New-TestAce -AccessMask 4294967295)

        $result.Observations[0].access_mask | Should -Be 4294967295
        $result.Observations[0].source_key | Should -BeExactly 'smb_ace|fs01|finance|S-1-5-11|allow|0xffffffff'
    }

    It 'records a deny entry as a deny entry' {
        $result = ConvertTo-AdgShareAceObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
            -Dacl @(New-TestAce -AceType 1 -SidString 'S-1-5-21-1-2-3-1105')

        $result.Observations[0].ace_type | Should -Be 'deny'
    }

    It 'drops an audit entry without reporting it as access' {
        $result = ConvertTo-AdgShareAceObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
            -Dacl @((New-TestAce -AceType 2), (New-TestAce -AceType 0))

        $result.Observations.Count | Should -Be 1
        $result.Errors.Count | Should -Be 0
    }

    It 'numbers entries in ACL order' {
        $result = ConvertTo-AdgShareAceObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
            -Dacl @(
                (New-TestAce -SidString 'S-1-1-0')
                (New-TestAce -SidString 'S-1-5-11')
                (New-TestAce -SidString 'S-1-5-32-544')
            )

        $result.Observations.order_index | Should -Be @(0, 1, 2)
    }

    It 'returns nothing at all for an empty DACL' {
        # An empty DACL means nobody has share access. That is a real state, and it is not
        # an error.
        $result = ConvertTo-AdgShareAceObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' -Dacl @()

        $result.Observations.Count | Should -Be 0
        $result.Errors.Count | Should -Be 0
    }

    Context 'unresolved trustees' {

        It 'keeps the ACE and reports the trustee as an unresolved principal' {
            # An orphaned SID on a share ACL is a finding. Dropping the ACE would hide
            # access that Windows still grants.
            $result = ConvertTo-AdgShareAceObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
                -Dacl @(New-TestAce -SidString 'S-1-5-21-1-2-3-9999' -TrusteeName '')

            $result.Observations.Count | Should -Be 1
            $result.Observations[0].trustee_sid | Should -Be 'S-1-5-21-1-2-3-9999'

            $result.Principals.Count | Should -Be 1
            $result.Principals[0].kind | Should -Be 'principal'
            $result.Principals[0].principal_kind | Should -Be 'unresolved'
            $result.Principals[0].unresolved_reason | Should -Be 'lookup_failed'
        }

        It 'never invents a display name for an unresolved principal' {
            $result = ConvertTo-AdgShareAceObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
                -Dacl @(New-TestAce -SidString 'S-1-5-21-1-2-3-9999' -TrusteeName '')

            $result.Principals[0].Contains('display_name') | Should -BeFalse
            $result.Principals[0].Contains('last_known_name') | Should -BeFalse
        }

        It 'reports one principal per SID even when it appears in several entries' {
            $result = ConvertTo-AdgShareAceObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
                -Dacl @(
                    (New-TestAce -SidString 'S-1-5-21-1-2-3-9999' -TrusteeName '')
                    (New-TestAce -AceType 1 -SidString 'S-1-5-21-1-2-3-9999' -TrusteeName '')
                )

            $result.Observations.Count | Should -Be 2
            # Two observations with one source key inside a batch are rejected outright.
            $result.Principals.Count | Should -Be 1
        }

        It 'does not report a principal for a trustee that did resolve' {
            # Naming a resolved principal is the AD collector's job; it knows whether the
            # SID is a user, a group, or a computer. This collector does not.
            $result = ConvertTo-AdgShareAceObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
                -Dacl @(New-TestAce -TrusteeName 'Authenticated Users')

            $result.Principals.Count | Should -Be 0
        }

        It 'records an error instead of reporting an ACE with no usable SID' {
            $result = ConvertTo-AdgShareAceObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
                -Dacl @(New-TestAce -NoTrustee)

            $result.Observations.Count | Should -Be 0
            $result.Errors.Count | Should -Be 1
            $result.Errors[0].code | Should -Be 'lookup_failed'
            $result.Errors[0].target | Should -Be '\\FS01\Finance'
        }
    }
}

Describe 'ConvertTo-AdgShareAccessObservation (level reading)' {

    BeforeAll {
        function New-TestAccess {
            param(
                [string] $AccountName = 'CORP\Finance-RW',
                [string] $AccessControlType = 'Allow',
                [string] $AccessRight = 'Change'
            )
            return [pscustomobject]@{
                AccountName       = $AccountName
                AccessControlType = $AccessControlType
                AccessRight       = $AccessRight
            }
        }
    }

    It 'reports a level and never a mask' {
        Mock -ModuleName AdgSmbCollector Resolve-AdgTrusteeSid { 'S-1-5-21-1-2-3-1202' }

        $result = ConvertTo-AdgShareAccessObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
            -Access @(New-TestAccess)

        $ace = $result.Observations[0]
        $ace.permission | Should -Be 'change'
        $ace.Contains('access_mask') | Should -BeFalse
        $ace.source_key | Should -BeExactly 'smb_ace|fs01|finance|S-1-5-21-1-2-3-1202|allow|change'
    }

    It 'records an error rather than an ACE when a name will not translate' {
        # The contract identifies a trustee only by SID. An ACE without one cannot be
        # reported at all - but it must not vanish either, so it becomes an error and the
        # run goes partial.
        Mock -ModuleName AdgSmbCollector Resolve-AdgTrusteeSid { $null }

        $result = ConvertTo-AdgShareAccessObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
            -Access @(New-TestAccess -AccountName 'CORP\GoneGroup')

        $result.Observations.Count | Should -Be 0
        $result.Errors.Count | Should -Be 1
        $result.Errors[0].code | Should -Be 'lookup_failed'
        $result.Errors[0].message | Should -Match 'security descriptor'
    }

    It 'records an error for a Custom right, which carries no mask to report' {
        Mock -ModuleName AdgSmbCollector Resolve-AdgTrusteeSid { 'S-1-5-11' }

        $result = ConvertTo-AdgShareAccessObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
            -Access @(New-TestAccess -AccessRight 'Custom')

        $result.Observations.Count | Should -Be 0
        $result.Errors[0].code | Should -Be 'unmappable_right'
    }

    It 'treats an account name that is itself a SID as an unresolved principal' {
        # Windows reports the SID string as the account name when it cannot resolve one.
        $result = ConvertTo-AdgShareAccessObservation -RunId $script:RunId -ServerName 'FS01' -ShareName 'Finance' `
            -Access @(New-TestAccess -AccountName 'S-1-5-21-1-2-3-9999')

        $result.Observations[0].trustee_sid | Should -Be 'S-1-5-21-1-2-3-9999'
        $result.Principals.Count | Should -Be 1
        $result.Principals[0].principal_kind | Should -Be 'unresolved'
    }
}

Describe 'New-AdgObservation' {

    It 'stamps the five fields every observation carries' {
        $observation = New-AdgObservation -Kind 'server' -RunId $script:RunId -SourceKey 'server|fs01' -Body @{ name = 'FS01' }

        $observation.schema_version | Should -Match '^1\.[0-9]+$'
        $observation.kind | Should -Be 'server'
        $observation.run_id | Should -Be $script:RunId
        $observation.source_key | Should -Be 'server|fs01'
        # Offset-bearing, because observations from different time zones must be orderable.
        $observation.observed_at | Should -Match '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$'
    }

    It 'omits absent values rather than sending null' {
        $observation = New-AdgObservation -Kind 'server' -RunId $script:RunId -SourceKey 'server|fs01' `
            -Body @{ name = 'FS01'; dns_host_name = $null }

        $observation.Contains('dns_host_name') | Should -BeFalse
    }
}

Describe 'Test-AdgSidString' {

    It 'accepts canonical SIDs' {
        Test-AdgSidString 'S-1-5-32-544' | Should -BeTrue
        Test-AdgSidString 'S-1-1-0' | Should -BeTrue
        Test-AdgSidString 'S-1-5-21-1004336348-1177238915-682003330-1104' | Should -BeTrue
    }

    It 'rejects a name, a blank, and a lower-case prefix' {
        Test-AdgSidString 'CORP\Finance' | Should -BeFalse
        Test-AdgSidString '' | Should -BeFalse
        Test-AdgSidString $null | Should -BeFalse
        Test-AdgSidString 's-1-5-32-544' | Should -BeFalse
    }
}
