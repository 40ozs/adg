#Requires -Version 7.0
<#
.SYNOPSIS
    The ADG Active Directory collector: users, groups, computers, and their direct
    membership edges.

.DESCRIPTION
    This collector reports what the directory says and nothing more. In particular it
    never expands nested groups. A flattened membership set answers "can Alice reach
    this?" but cannot answer "why, and what do I remove?", and the chain of edges is the
    only form of that answer an administrator can act on (ADR-0002).

    The module is layered so that everything except the socket is testable without a
    domain:

      * a **directory provider** owns the LDAP connection and yields normalized entries;
      * **translation** functions turn one raw entry into one contract observation, and
        are pure;
      * **collection** walks the directory, resolves member references, and hands
        payloads to a publisher from AdgCollector.Common.

    Tests substitute a fixture provider for the LDAP one, so the enumeration logic, the
    ranged-member reader, the rename recovery, and the no-flattening guarantee are all
    exercised by the Pester suite.

    Privileges: normal collection needs read access to the directory, which any
    authenticated domain account has by default. Domain Admin is never required and must
    never be used (ADR-0004). See docs/collectors/ad.md.

.NOTES
    Read-only. Nothing here writes to the directory.
#>

Set-StrictMode -Version Latest

$moduleRoot = Split-Path -Parent $PSCommandPath
# Not -Force: reloading the shared module here would unload the copy the caller
# imported, and its functions would vanish from the caller's session mid-run.
Import-Module (Join-Path (Split-Path -Parent $moduleRoot) 'common/AdgCollector.Common.psd1') -ErrorAction Stop

$script:CollectorVersion = '0.1.0'

# Attributes requested for every principal. userAccountControl, primaryGroupID, and
# groupType are what make "enabled", "primary group", and "security vs distribution"
# answerable; uSNChanged and whenChanged are the change metadata a future incremental run
# needs as a watermark.
$script:PrincipalAttributes = @(
    'objectSid', 'objectClass', 'distinguishedName', 'sAMAccountName', 'userPrincipalName',
    'displayName', 'name', 'userAccountControl', 'primaryGroupID', 'groupType',
    'isDeleted', 'whenChanged', 'uSNChanged', 'objectGUID'
)

# (objectSid=*) excludes contacts, organizational units, and every other object that can
# never appear in an ACL. Computers and group managed service accounts both carry
# objectClass=user, so they are collected by this filter too.
$script:DefaultPrincipalFilter = '(&(objectSid=*)(|(objectClass=user)(objectClass=group)(objectClass=foreignSecurityPrincipal)))'
$script:DefaultGroupFilter = '(&(objectSid=*)(objectClass=group))'

$script:BinaryAttributes = @('objectSid', 'objectGUID', 'objectGuid')

# groupType bits (MS-ADTS 2.2.12). The high bit is what separates a group that can grant
# access from a distribution list that never can.
$script:GroupTypeBuiltinLocal = 0x00000001
$script:GroupTypeAccountGlobal = 0x00000002
$script:GroupTypeResourceDomainLocal = 0x00000004
$script:GroupTypeUniversal = 0x00000008
$script:GroupTypeSecurityEnabled = 0x80000000L

$script:UacAccountDisable = 0x00000002


# --- Distinguished names -----------------------------------------------------------------

function ConvertTo-AdgNormalizedDistinguishedName {
    <#
        .SYNOPSIS
            Fold a DN to a comparable form: components trimmed, case lowered.
        .DESCRIPTION
            DN comparison decides whether an object falls inside an excluded container, so
            it has to be exact about two things. An escaped comma (\,) is part of a value
            and must not split a component; whitespace after an unescaped comma is
            insignificant and must not prevent a match. Getting either wrong would let an
            object the operator excluded be collected anyway, or drop one they wanted.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][AllowEmptyString()][string] $DistinguishedName)

    if ([string]::IsNullOrWhiteSpace($DistinguishedName)) { return '' }
    $components = [regex]::Split($DistinguishedName, '(?<!\\),')
    return (($components | ForEach-Object { $_.Trim() }) -join ',').ToLowerInvariant()
}


function Test-AdgDistinguishedNameUnder {
    <#
        .SYNOPSIS
            Whether a DN is the container itself or lives beneath it.
        .DESCRIPTION
            The suffix must land on a component boundary: 'OU=Finance,DC=x' must not be
            treated as inside 'OU=nance,DC=x'.
    #>
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string] $DistinguishedName,
        [Parameter(Mandatory)][AllowEmptyString()][string] $Container
    )

    $dn = ConvertTo-AdgNormalizedDistinguishedName $DistinguishedName
    $container = ConvertTo-AdgNormalizedDistinguishedName $Container
    if (-not $dn -or -not $container) { return $false }
    if ($dn -eq $container) { return $true }
    return $dn.EndsWith(",$container", [System.StringComparison]::Ordinal)
}


function Get-AdgForeignSecurityPrincipalSid {
    <#
        .SYNOPSIS
            The SID a foreign security principal's DN is named after, or $null.
        .DESCRIPTION
            A principal from a trusted domain appears in this domain as a stub object in
            CN=ForeignSecurityPrincipals whose RDN is the principal's SID. That is often
            all this domain knows about it, and it is enough: SID is identity.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][AllowEmptyString()][string] $DistinguishedName)

    if (-not $DistinguishedName) { return $null }
    $first = ([regex]::Split($DistinguishedName, '(?<!\\),'))[0].Trim()
    if ($first -match '^CN=(S-1-[0-9A-Fa-fXx\-]+)$') {
        return $Matches[1].ToUpperInvariant()
    }
    return $null
}


# --- Directory entries -------------------------------------------------------------------

function New-AdgDirectoryEntry {
    <#
        .SYNOPSIS
            One normalized directory object: a DN plus a case-insensitive attribute bag.
        .DESCRIPTION
            LDAP attribute names are case-insensitive and a ranged read returns them with a
            suffix ('member;range=0-1499'), so every consumer here looks attributes up
            without caring about case. Normalizing once at the provider boundary means the
            translation layer is plain string handling and a test fixture is plain JSON.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string] $DistinguishedName,
        [System.Collections.IDictionary] $Attributes = @{}
    )

    $bag = [System.Collections.Hashtable]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($key in $Attributes.Keys) { $bag[$key] = $Attributes[$key] }
    return @{ DistinguishedName = $DistinguishedName; Attributes = $bag }
}


function Get-AdgEntryValues {
    <#
        .SYNOPSIS
            Every value of an attribute, as an array. Empty when the attribute is absent.
    #>
    [OutputType([object[]])]
    param(
        [Parameter(Mandatory)][hashtable] $Entry,
        [Parameter(Mandatory)][string] $Name
    )

    if (-not $Entry.Attributes.ContainsKey($Name)) { return @() }
    $value = $Entry.Attributes[$Name]
    if ($null -eq $value) { return @() }
    return @($value)
}


function Get-AdgEntryValue {
    <#
        .SYNOPSIS
            The first value of an attribute, or $null. "Absent" and "empty" are the same
            answer here: the source did not say.
    #>
    param(
        [Parameter(Mandatory)][hashtable] $Entry,
        [Parameter(Mandatory)][string] $Name
    )

    # @() because PowerShell unrolls a one-element array on return, leaving a bare
    # value that has no Count under Set-StrictMode.
    $values = @(Get-AdgEntryValues -Entry $Entry -Name $Name)
    if ($values.Count -eq 0) { return $null }
    $first = $values[0]
    if ($first -is [string] -and [string]::IsNullOrEmpty($first)) { return $null }
    return $first
}


function Get-AdgEntryInteger {
    <#
        .SYNOPSIS
            An attribute as a 64-bit integer, or $null when absent or unparsable.
        .DESCRIPTION
            LDAP delivers these as strings and a JSON fixture delivers them as numbers;
            groupType arrives signed (0x80000002 reads as -2147483646) and is masked back
            to its unsigned value, because the security bit is the high bit.
    #>
    [OutputType([Nullable[long]])]
    param(
        [Parameter(Mandatory)][hashtable] $Entry,
        [Parameter(Mandatory)][string] $Name
    )

    $value = Get-AdgEntryValue -Entry $Entry -Name $Name
    if ($null -eq $value) { return $null }
    [long] $parsed = 0
    if (-not [long]::TryParse([string] $value, [ref] $parsed)) { return $null }
    # 0xFFFFFFFFL, not 0xFFFFFFFF: PowerShell parses the latter as Int32 -1, and the mask
    # would then be a no-op that left groupType negative.
    if ($parsed -lt 0) { return ($parsed -band 0xFFFFFFFFL) }
    return $parsed
}


function Get-AdgEntryBoolean {
    [OutputType([Nullable[bool]])]
    param(
        [Parameter(Mandatory)][hashtable] $Entry,
        [Parameter(Mandatory)][string] $Name
    )

    $value = Get-AdgEntryValue -Entry $Entry -Name $Name
    if ($null -eq $value) { return $null }
    if ($value -is [bool]) { return [bool] $value }
    return ([string] $value).Trim() -in @('TRUE', 'true', 'True', '1')
}


# --- Translation -------------------------------------------------------------------------

function Get-AdgPrincipalKindFromEntry {
    <#
        .SYNOPSIS
            Which kind of principal an entry describes.
        .DESCRIPTION
            Order matters and is not arbitrary: a computer and a group managed service
            account both carry objectClass=user, so the specific classes are tested first.
            A misclassified computer would be reported as a user account, which changes
            how a reviewer reads every grant it holds.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][hashtable] $Entry)

    $classes = @(Get-AdgEntryValues -Entry $Entry -Name 'objectClass' | ForEach-Object { ([string] $_).ToLowerInvariant() })

    if ($classes -contains 'foreignsecurityprincipal') { return 'foreign_security_principal' }
    if ($classes -contains 'group') { return 'domain_group' }
    if ($classes -contains 'msds-groupmanagedserviceaccount' -or $classes -contains 'msds-managedserviceaccount') {
        return 'managed_service_account'
    }
    if ($classes -contains 'computer') { return 'computer' }
    if ($classes -contains 'user') { return 'user' }

    # An object with a SID but no class this collector understands is still a principal
    # that can appear in an ACL. Reporting it as 'unresolved' says what is true: ADG has
    # its SID and could not classify it.
    return 'unresolved'
}


function Get-AdgGroupScopeFromType {
    <#
        .SYNOPSIS
            Map the groupType bits to the contract's group scope.
    #>
    [OutputType([string])]
    param([Nullable[long]] $GroupType)

    if ($null -eq $GroupType) { return 'unknown' }
    if ($GroupType -band $script:GroupTypeBuiltinLocal) { return 'builtin_local' }
    if ($GroupType -band $script:GroupTypeResourceDomainLocal) { return 'domain_local' }
    if ($GroupType -band $script:GroupTypeAccountGlobal) { return 'global' }
    if ($GroupType -band $script:GroupTypeUniversal) { return 'universal' }
    return 'unknown'
}


function Get-AdgGroupTypeFromType {
    <#
        .SYNOPSIS
            Security or distribution.
        .DESCRIPTION
            A distribution group never grants access. 'unknown' means the directory did
            not say, which must never be reported as 'distribution': that would read as
            "harmless" about a group that may well be on an ACL.
    #>
    [OutputType([string])]
    param([Nullable[long]] $GroupType)

    if ($null -eq $GroupType) { return 'unknown' }
    if ($GroupType -band $script:GroupTypeSecurityEnabled) { return 'security' }
    return 'distribution'
}


function Get-AdgDomainSidFromSid {
    <#
        .SYNOPSIS
            The domain SID a domain principal's SID belongs to, or $null.
        .DESCRIPTION
            Only S-1-5-21-x-y-z-RID SIDs have one. A BUILTIN or well-known SID such as
            S-1-5-32-544 or S-1-1-0 is not issued by a domain, and inventing a domain for
            it would attribute it to an authority that never issued it.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][AllowEmptyString()][string] $Sid)

    if ($Sid -match '^(S-1-5-21-\d{1,10}-\d{1,10}-\d{1,10})-\d{1,10}$') {
        return $Matches[1]
    }
    return $null
}


function Get-AdgSidWithRid {
    <#
        .SYNOPSIS
            Compose a principal SID from a domain SID and a RID.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $DomainSid,
        [Parameter(Mandatory)][long] $Rid
    )

    if ($Rid -lt 0) { throw "A RID cannot be negative; received $Rid." }
    return "$DomainSid-$Rid"
}


function Test-AdgAccountEnabled {
    <#
        .SYNOPSIS
            Whether an account is enabled, or $null when the directory did not say.
        .DESCRIPTION
            Groups have no userAccountControl. Reporting 'enabled: true' for them would
            assert something the source never stated.
    #>
    [OutputType([Nullable[bool]])]
    param([Nullable[long]] $UserAccountControl)

    if ($null -eq $UserAccountControl) { return $null }
    return -not [bool] ($UserAccountControl -band $script:UacAccountDisable)
}


function ConvertTo-AdgPrincipalObservationFromEntry {
    <#
        .SYNOPSIS
            Turn one directory entry into one principal observation.
        .DESCRIPTION
            Pure: it reads the entry and returns a payload. Every judgment it makes is
            about what the directory said, never about what the principal can reach.
    #>
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory)][hashtable] $Entry,
        [Parameter(Mandatory)][string] $RunId,
        [string] $ObservedAt
    )

    $sid = Get-AdgEntryValue -Entry $Entry -Name 'objectSid'
    if (-not $sid) {
        throw "The directory object '$($Entry.DistinguishedName)' has no objectSid, so it has no identity in ADG. A principal is never keyed by name."
    }
    $sid = ([string] $sid).ToUpperInvariant()

    $kind = Get-AdgPrincipalKindFromEntry -Entry $Entry
    $groupType = Get-AdgEntryInteger -Entry $Entry -Name 'groupType'
    $isGroup = ($kind -eq 'domain_group')
    $displayName = Get-AdgEntryValue -Entry $Entry -Name 'displayName'
    if (-not $displayName) { $displayName = Get-AdgEntryValue -Entry $Entry -Name 'name' }

    $arguments = @{
        RunId              = $RunId
        Sid                = $sid
        PrincipalKind      = $kind
        DomainSid          = (Get-AdgDomainSidFromSid $sid)
        SamAccountName     = (Get-AdgEntryValue -Entry $Entry -Name 'sAMAccountName')
        DistinguishedName  = $Entry.DistinguishedName
        UserPrincipalName  = (Get-AdgEntryValue -Entry $Entry -Name 'userPrincipalName')
        IsDeleted          = [bool] (Get-AdgEntryBoolean -Entry $Entry -Name 'isDeleted')
        ObservedAt         = $ObservedAt
    }

    if ($kind -eq 'unresolved') {
        # An object ADG could not classify keeps its name as last_known_name, never as
        # display_name, where it would read as a current resolution.
        $arguments['UnresolvedReason'] = 'unknown'
        $arguments['LastKnownName'] = $displayName
    }
    else {
        $arguments['DisplayName'] = $displayName
    }
    if ($isGroup) {
        $arguments['GroupScope'] = Get-AdgGroupScopeFromType $groupType
        $arguments['GroupType'] = Get-AdgGroupTypeFromType $groupType
    }
    else {
        $arguments['Enabled'] = Test-AdgAccountEnabled (Get-AdgEntryInteger -Entry $Entry -Name 'userAccountControl')
    }

    return New-AdgPrincipalObservation @arguments
}


function Get-AdgEntryChangeMetadata {
    <#
        .SYNOPSIS
            The change markers a future incremental run needs: uSNChanged and whenChanged.
        .DESCRIPTION
            These do not belong on an observation -- contract v1 has no field for them and
            forbids extra ones -- so the collector keeps them in its own state file. A USN
            is only meaningful against the directory server that issued it, which is why
            the state file records that server too.
    #>
    [OutputType([hashtable])]
    param([Parameter(Mandatory)][hashtable] $Entry)

    return @{
        UsnChanged  = Get-AdgEntryInteger -Entry $Entry -Name 'uSNChanged'
        WhenChanged = Get-AdgEntryValue -Entry $Entry -Name 'whenChanged'
    }
}


# --- Directory providers -----------------------------------------------------------------

function Test-AdgTransientDirectoryFailure {
    <#
        .SYNOPSIS
            Whether re-issuing an identical directory query could plausibly succeed.
        .DESCRIPTION
            A busy or restarting domain controller is worth waiting for. Insufficient
            rights and "no such object" are not: retrying them burns time and hides the
            real finding, which is that the collector could not read something and the run
            is therefore incomplete.
    #>
    [OutputType([bool])]
    param([Parameter(Mandatory)] $ErrorRecord)

    $exception = if ($ErrorRecord -is [System.Management.Automation.ErrorRecord]) {
        $ErrorRecord.Exception
    }
    else {
        $ErrorRecord
    }
    while ($null -ne $exception) {
        $typeName = $exception.GetType().FullName
        if ($typeName -eq 'System.DirectoryServices.Protocols.LdapException') {
            # A transport-level LDAP failure: the server was unreachable or dropped us.
            return $true
        }
        if ($exception.PSObject.Properties.Name -contains 'Response' -and $null -ne $exception.Response) {
            $resultCode = $null
            try { $resultCode = [string] $exception.Response.ResultCode } catch { $resultCode = $null }
            if ($resultCode) {
                return $resultCode -in @('Busy', 'Unavailable', 'TimeLimitExceeded', 'Other', 'ProtocolError')
            }
        }
        if ($exception -is [System.TimeoutException]) { return $true }
        $exception = $exception.InnerException
    }
    return $false
}


function Invoke-AdgDirectoryOperation {
    <#
        .SYNOPSIS
            Run a directory operation, retrying only transient failures.
    #>
    param(
        [Parameter(Mandatory)][scriptblock] $Operation,
        [int] $MaxAttempts = 3,
        [string] $Description = 'directory operation'
    )

    for ($attempt = 1; $attempt -le [Math]::Max(1, $MaxAttempts); $attempt++) {
        try {
            return & $Operation
        }
        catch {
            if (-not (Test-AdgTransientDirectoryFailure -ErrorRecord $_) -or $attempt -ge $MaxAttempts) {
                throw
            }
            $delay = [Math]::Min([Math]::Pow(2, $attempt), 30)
            Write-Warning "The $Description failed transiently (attempt $attempt/$MaxAttempts); retrying in ${delay}s: $($_.Exception.Message)"
            Start-Sleep -Seconds $delay
        }
    }
}


function Invoke-AdgDirectorySearch {
    <#
        .SYNOPSIS
            Run a search through a provider and emit normalized entries.
        .DESCRIPTION
            Every directory read in this collector goes through here, which is what lets
            the Pester suite drive the whole collector from a fixture: a provider is just
            a hashtable carrying a SearchCommand.
    #>
    [OutputType([hashtable[]])]
    param(
        [Parameter(Mandatory)][hashtable] $Provider,
        [Parameter(Mandatory)][string] $SearchBase,
        [Parameter(Mandatory)][string] $Filter,
        [Parameter(Mandatory)][string[]] $Attributes,
        [ValidateSet('Base', 'OneLevel', 'Subtree')][string] $Scope = 'Subtree'
    )

    $request = @{
        SearchBase = $SearchBase
        Filter     = $Filter
        Attributes = $Attributes
        Scope      = $Scope
    }
    $results = & $Provider.SearchCommand $request
    if ($null -eq $results) { return @() }
    return @($results)
}


function New-AdgFixtureDirectoryProvider {
    <#
        .SYNOPSIS
            A directory provider backed by a JSON file of entries, for development and
            tests.
        .DESCRIPTION
            It implements the parts of LDAP this collector depends on -- subtree and base
            scope, the handful of filters the collector issues, and ranged member
            retrieval -- so the collection logic can be exercised end to end without a
            domain. It is a test double, not a directory: it is never used for a real scan.

            Fixture shape:

              {
                "domainSid": "S-1-5-21-...",
                "defaultNamingContext": "DC=corp,DC=example,DC=com",
                "server": "dc01.corp.example.com",
                "rangeStep": 1500,
                "entries": [
                  { "distinguishedName": "CN=Alice,OU=Staff,DC=...",
                    "attributes": { "objectSid": "S-1-5-21-...-1104", ... } }
                ],
                "denied": ["CN=Locked,OU=Staff,DC=..."]
              }
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory, ParameterSetName = 'Path')][string] $Path,
        [Parameter(Mandatory, ParameterSetName = 'Document')][System.Collections.IDictionary] $Document
    )

    if ($PSCmdlet.ParameterSetName -eq 'Path') {
        if (-not (Test-Path -LiteralPath $Path)) {
            throw "Directory fixture '$Path' does not exist."
        }
        $Document = Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable -Depth 20
    }

    foreach ($required in @('defaultNamingContext', 'entries')) {
        if (-not $Document.Contains($required)) {
            throw "A directory fixture must declare '$required'."
        }
    }

    $entries = [System.Collections.Generic.List[hashtable]]::new()
    foreach ($item in $Document['entries']) {
        $attributes = if ($item.Contains('attributes')) { $item['attributes'] } else { @{} }
        $dn = [string] $item['distinguishedName']
        $entry = New-AdgDirectoryEntry -DistinguishedName $dn -Attributes $attributes
        if (-not $entry.Attributes.ContainsKey('distinguishedName')) {
            $entry.Attributes['distinguishedName'] = $dn
        }
        $entries.Add($entry)
    }

    $denied = if ($Document.Contains('denied')) { @($Document['denied']) } else { @() }
    $rangeStep = if ($Document.Contains('rangeStep')) { [int] $Document['rangeStep'] } else { 1500 }

    $state = @{
        Entries   = $entries
        Denied    = $denied
        RangeStep = $rangeStep
        Requests  = [System.Collections.Generic.List[hashtable]]::new()
    }

    $search = {
        param([hashtable] $Request)

        $state.Requests.Add($Request)

        foreach ($deniedDn in $state.Denied) {
            if (Test-AdgDistinguishedNameUnder -DistinguishedName $Request.SearchBase -Container $deniedDn) {
                throw [System.UnauthorizedAccessException]::new(
                    "The collector account cannot read '$($Request.SearchBase)'.")
            }
        }

        $candidates = switch ($Request.Scope) {
            'Base' {
                @($state.Entries | Where-Object {
                        (ConvertTo-AdgNormalizedDistinguishedName $_.DistinguishedName) -eq
                        (ConvertTo-AdgNormalizedDistinguishedName $Request.SearchBase)
                    })
            }
            default {
                @($state.Entries | Where-Object {
                        Test-AdgDistinguishedNameUnder -DistinguishedName $_.DistinguishedName -Container $Request.SearchBase
                    })
            }
        }

        if ($Request.Scope -eq 'Base' -and $candidates.Count -eq 0) {
            throw [System.IO.DirectoryNotFoundException]::new(
                "There is no object at '$($Request.SearchBase)'.")
        }

        foreach ($deniedDn in $state.Denied) {
            # LDAP does not show an object the caller cannot read; it simply is not in
            # the result. The collector must discover the gap when it looks the object
            # up by name, which is what the group-membership pass does.
            $candidates = @($candidates | Where-Object {
                    -not (Test-AdgDistinguishedNameUnder -DistinguishedName $_.DistinguishedName -Container $deniedDn)
                })
        }

        $matched = @($candidates | Where-Object { Test-AdgFixtureFilterMatch -Entry $_ -Filter $Request.Filter })
        return @($matched | ForEach-Object {
                Select-AdgFixtureAttributes -Entry $_ -Attributes $Request.Attributes -RangeStep $state.RangeStep
            })
    }.GetNewClosure()

    return @{
        Kind                 = 'Fixture'
        Server               = if ($Document.Contains('server')) { [string] $Document['server'] } else { 'fixture' }
        DomainSid            = if ($Document.Contains('domainSid')) { [string] $Document['domainSid'] } else { $null }
        DefaultNamingContext = [string] $Document['defaultNamingContext']
        DnsDomainName        = if ($Document.Contains('dnsDomainName')) { [string] $Document['dnsDomainName'] } else { 'fixture.local' }
        DsServiceName        = if ($Document.Contains('dsServiceName')) { [string] $Document['dsServiceName'] } else { 'CN=NTDS Settings,CN=FIXTURE' }
        InvocationId         = if ($Document.Contains('invocationId')) { [string] $Document['invocationId'] } else { '00000000-0000-4000-8000-000000000000' }
        SearchCommand        = $search
        State                = $state
    }
}


function Test-AdgFixtureFilterMatch {
    <#
        .SYNOPSIS
            Evaluate the small set of LDAP filters this collector issues.
        .DESCRIPTION
            Deliberately not a general LDAP filter engine: it understands (objectSid=*),
            (objectClass=x), (objectClass=*), and the (&...)/(|...) combinations the
            collector actually sends. An unrecognized filter throws rather than quietly
            matching everything, which would make a test pass for the wrong reason.
    #>
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][hashtable] $Entry,
        [Parameter(Mandatory)][string] $Filter
    )

    $text = $Filter.Trim()
    if ($text -eq '(objectClass=*)') { return $true }

    if ($text -match '^\((?<op>[&|])(?<rest>.+)\)$') {
        $operator = $Matches['op']
        $clauses = Split-AdgFilterClause $Matches['rest']
        $results = @($clauses | ForEach-Object { Test-AdgFixtureFilterMatch -Entry $Entry -Filter $_ })
        if ($operator -eq '&') { return (-not ($results -contains $false)) }
        return ($results -contains $true)
    }

    # Greater-or-equal, which is how a uSNChanged delta is expressed. Numeric, because
    # every attribute this collector filters on that way is a counter -- comparing USNs as
    # strings would place 9 after 10 and silently drop a whole range from a delta.
    if ($text -match '^\((?<name>[A-Za-z0-9\-]+)>=(?<value>[0-9]+)\)$') {
        $threshold = [long] $Matches['value']
        foreach ($candidate in @(Get-AdgEntryValues -Entry $Entry -Name $Matches['name'])) {
            [long] $number = 0
            if ([long]::TryParse([string] $candidate, [ref] $number) -and $number -ge $threshold) {
                return $true
            }
        }
        return $false
    }

    if ($text -match '^\((?<name>[A-Za-z0-9\-]+)=(?<value>.*)\)$') {
        $name = $Matches['name']
        $value = $Matches['value']
        $values = @(Get-AdgEntryValues -Entry $Entry -Name $name)
        if ($value -eq '*') { return $values.Count -gt 0 }
        return @($values | ForEach-Object { ([string] $_).ToLowerInvariant() }) -contains $value.ToLowerInvariant()
    }

    throw "The fixture directory provider does not understand the filter '$Filter'."
}


function Split-AdgFilterClause {
    <#
        .SYNOPSIS
            Split the body of a compound LDAP filter into its balanced clauses.
    #>
    [OutputType([string[]])]
    param([Parameter(Mandatory)][string] $Text)

    $clauses = [System.Collections.Generic.List[string]]::new()
    $depth = 0
    $start = 0
    for ($index = 0; $index -lt $Text.Length; $index++) {
        $character = $Text[$index]
        if ($character -eq '(') {
            if ($depth -eq 0) { $start = $index }
            $depth++
        }
        elseif ($character -eq ')') {
            $depth--
            if ($depth -eq 0) {
                $clauses.Add($Text.Substring($start, $index - $start + 1))
            }
        }
    }
    if ($depth -ne 0) { throw "Unbalanced parentheses in the LDAP filter fragment '$Text'." }
    return $clauses.ToArray()
}


function Select-AdgFixtureAttributes {
    <#
        .SYNOPSIS
            Return the requested attributes, emulating AD's ranged retrieval of 'member'.
        .DESCRIPTION
            A real directory refuses to return more than MaxValRange values at once and
            signals the truncation by renaming the attribute to 'member;range=0-1499'. A
            collector that ignores the suffix reads the first chunk and believes it saw the
            whole group. The fixture reproduces that behavior so the ranged reader is
            tested against the failure it exists to prevent.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][hashtable] $Entry,
        [Parameter(Mandatory)][string[]] $Attributes,
        [int] $RangeStep = 1500
    )

    $projected = @{}
    foreach ($requested in $Attributes) {
        if ($requested -match '^(?<name>[A-Za-z0-9\-]+);range=(?<low>\d+)-(?<high>[\d*]+)$') {
            $name = $Matches['name']
            $low = [int] $Matches['low']
            $all = @(Get-AdgEntryValues -Entry $Entry -Name $name)
            if ($low -ge $all.Count) { continue }
            $take = [Math]::Min($RangeStep, $all.Count - $low)
            $chunk = $all[$low..($low + $take - 1)]
            $upper = $low + $take - 1
            $key = if (($low + $take) -ge $all.Count) { "$name;range=$low-*" } else { "$name;range=$low-$upper" }
            $projected[$key] = $chunk
            continue
        }

        $values = @(Get-AdgEntryValues -Entry $Entry -Name $requested)
        if ($values.Count -eq 0) { continue }
        if ($requested -ieq 'member' -and $values.Count -gt $RangeStep) {
            $projected["member;range=0-$($RangeStep - 1)"] = $values[0..($RangeStep - 1)]
            continue
        }
        $projected[$requested] = $values
    }

    return New-AdgDirectoryEntry -DistinguishedName $Entry.DistinguishedName -Attributes $projected
}


function New-AdgLdapDirectoryProvider {
    <#
        .SYNOPSIS
            A directory provider backed by a real LDAP connection.
        .DESCRIPTION
            Uses System.DirectoryServices.Protocols directly rather than the RSAT
            ActiveDirectory module, for three reasons: it is present on any Windows host
            with .NET, it exposes paging and ranged retrieval explicitly instead of hiding
            them, and it surfaces LDAP result codes the collector needs in order to tell a
            permissions failure from an outage.

            Two truncation traps are treated as errors rather than as results. A search
            that exceeds the server's size limit and a ranged attribute that was not read
            to its end both look exactly like a smaller directory, and a smaller directory
            is the wrong answer an audit tool must never give.

            Signing and sealing are on by default: observations name every principal in
            the domain, and they should not cross the network in clear text.
    #>
    [OutputType([hashtable])]
    param(
        [string] $Server,
        [string] $SearchBase,
        [int] $Port = 389,
        [switch] $UseTls,
        [pscredential] $Credential,
        [int] $PageSize = 1000,
        [int] $TimeoutSeconds = 120
    )

    $identifier = if ($Server) {
        [System.DirectoryServices.Protocols.LdapDirectoryIdentifier]::new($Server, $Port)
    }
    else {
        # No server named: let the LDAP client locate a domain controller for the host's
        # own domain, the same way a domain-joined process normally binds.
        [System.DirectoryServices.Protocols.LdapDirectoryIdentifier]::new(
            [System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain().Name, $Port)
    }

    $connection = [System.DirectoryServices.Protocols.LdapConnection]::new($identifier)
    $connection.SessionOptions.ProtocolVersion = 3
    $connection.SessionOptions.ReferralChasing = [System.DirectoryServices.Protocols.ReferralChasingOptions]::None
    $connection.Timeout = [timespan]::FromSeconds($TimeoutSeconds)
    if ($UseTls) {
        $connection.SessionOptions.SecureSocketLayer = $true
    }
    else {
        $connection.SessionOptions.Signing = $true
        $connection.SessionOptions.Sealing = $true
    }
    $connection.AuthType = [System.DirectoryServices.Protocols.AuthType]::Negotiate
    if ($Credential) {
        $connection.Credential = $Credential.GetNetworkCredential()
    }
    $connection.Bind()

    $rootDse = Get-AdgLdapRootDse -Connection $connection
    if (-not $SearchBase) { $SearchBase = $rootDse.DefaultNamingContext }

    $search = {
        param([hashtable] $Request)

        $scope = switch ($Request.Scope) {
            'Base' { [System.DirectoryServices.Protocols.SearchScope]::Base }
            'OneLevel' { [System.DirectoryServices.Protocols.SearchScope]::OneLevel }
            default { [System.DirectoryServices.Protocols.SearchScope]::Subtree }
        }
        $searchRequest = [System.DirectoryServices.Protocols.SearchRequest]::new(
            $Request.SearchBase, $Request.Filter, $scope, $Request.Attributes)
        $pageControl = [System.DirectoryServices.Protocols.PageResultRequestControl]::new($PageSize)
        $searchRequest.Controls.Add($pageControl) | Out-Null

        $collected = [System.Collections.Generic.List[hashtable]]::new()
        while ($true) {
            $response = $connection.SendRequest($searchRequest)
            if ($response.ResultCode -eq [System.DirectoryServices.Protocols.ResultCode]::SizeLimitExceeded) {
                throw "The search under '$($Request.SearchBase)' hit the server's size limit. Paging did not take effect, so the result is truncated; a truncated enumeration would be reported as a smaller directory."
            }
            foreach ($entry in $response.Entries) {
                $collected.Add((ConvertFrom-AdgLdapEntry -Entry $entry))
            }
            $pageResponse = $response.Controls |
                Where-Object { $_ -is [System.DirectoryServices.Protocols.PageResultResponseControl] } |
                Select-Object -First 1
            if ($null -eq $pageResponse -or $pageResponse.Cookie.Length -eq 0) { break }
            $pageControl.Cookie = $pageResponse.Cookie
        }
        return $collected.ToArray()
    }.GetNewClosure()

    return @{
        Kind                 = 'Ldap'
        Connection           = $connection
        Server               = if ($Server) { $Server } else { $identifier.Servers[0] }
        DomainSid            = $null
        DefaultNamingContext = $SearchBase
        DnsDomainName        = $rootDse.DnsHostName
        DsServiceName        = $rootDse.DsServiceName
        RootDse              = $rootDse
        SearchCommand        = $search
    }
}


function Get-AdgDirectoryIssuer {
    <#
        .SYNOPSIS
            The identity a uSNChanged watermark from this provider belongs to.
        .DESCRIPTION
            Two facts joined, and both are needed.

            dsServiceName names the domain controller that answered. USNs are per-server
            counters, so DC1's watermark replayed against DC2 skips every object whose USN
            on DC2 happens to fall below it -- silently, permanently, and with nothing
            afterwards looking wrong.

            invocationId names this *incarnation* of that controller's database. A DC
            restored from backup keeps its name and rolls its USN counter backwards, so it
            reissues numbers it has already handed out. A watermark compared on the server
            name alone survives that restore and skips every reused number. The invocation
            id changes, which is what makes the restore visible.

            Returns $null when the invocation id cannot be read, and the caller must then
            refuse to run a delta. That is the safe direction: one expensive full scan
            against a gap nobody would ever detect.

            Every failure here is reported at Verbose level, not as a warning. The caller
            knows which of the two things it was about to do -- resume from a watermark, or
            leave one behind -- and only it can say which consequence matters; a warning
            from in here would fire on every full run that was never going to resume.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][hashtable] $Provider)

    $service = if ($Provider.ContainsKey('DsServiceName')) { [string] $Provider['DsServiceName'] } else { '' }
    if ([string]::IsNullOrWhiteSpace($service)) {
        Write-Verbose 'The directory did not report dsServiceName, so a uSNChanged watermark cannot be tied to the server that issued it.'
        return $null
    }

    $invocation = if ($Provider.ContainsKey('InvocationId')) { [string] $Provider['InvocationId'] } else { '' }
    if ([string]::IsNullOrWhiteSpace($invocation)) {
        $invocation = try {
            $results = @(Invoke-AdgDirectorySearch -Provider $Provider -SearchBase $service `
                    -Filter '(objectClass=*)' -Attributes @('invocationId') -Scope 'Base')
            if ($results.Count -ge 1) { [string] (Get-AdgEntryValue -Entry $results[0] -Name 'invocationId') } else { '' }
        }
        catch {
            Write-Verbose "The invocationId of '$service' could not be read: $($_.Exception.Message)"
            return $null
        }
    }

    if ([string]::IsNullOrWhiteSpace($invocation)) {
        Write-Verbose "The directory server '$service' reported no invocationId, so a restore from backup -- which reissues USNs already handed out -- would be invisible to a watermark."
        return $null
    }

    return "$service|$invocation"
}


function Get-AdgLdapRootDse {
    <#
        .SYNOPSIS
            Read RootDSE for the naming context and the server's identity.
        .DESCRIPTION
            dsServiceName identifies which domain controller answered. A uSNChanged
            watermark is only meaningful against that same server, so the collector
            records it rather than assuming every DC counts alike.
    #>
    [OutputType([hashtable])]
    param([Parameter(Mandatory)] $Connection)

    $request = [System.DirectoryServices.Protocols.SearchRequest]::new(
        $null, '(objectClass=*)', [System.DirectoryServices.Protocols.SearchScope]::Base,
        @('defaultNamingContext', 'dnsHostName', 'dsServiceName', 'highestCommittedUSN'))
    $response = $Connection.SendRequest($request)
    if ($response.Entries.Count -lt 1) {
        throw 'The directory server returned no RootDSE; the collector cannot determine the naming context to search.'
    }
    $entry = ConvertFrom-AdgLdapEntry -Entry $response.Entries[0]
    return @{
        DefaultNamingContext = [string] (Get-AdgEntryValue -Entry $entry -Name 'defaultNamingContext')
        DnsHostName          = [string] (Get-AdgEntryValue -Entry $entry -Name 'dnsHostName')
        DsServiceName        = [string] (Get-AdgEntryValue -Entry $entry -Name 'dsServiceName')
        HighestCommittedUsn  = Get-AdgEntryInteger -Entry $entry -Name 'highestCommittedUSN'
        InvocationId         = [string] (Get-AdgEntryValue -Entry $entry -Name 'invocationId')
    }
}


function ConvertFrom-AdgLdapEntry {
    <#
        .SYNOPSIS
            Normalize a SearchResultEntry into the collector's entry shape.
        .DESCRIPTION
            objectSid and objectGUID come back as raw bytes and are converted here, so that
            everything above this line deals in canonical string SIDs -- the identity ADG
            keys on.
    #>
    [OutputType([hashtable])]
    param([Parameter(Mandatory)] $Entry)

    $attributes = @{}
    foreach ($name in $Entry.Attributes.AttributeNames) {
        $attribute = $Entry.Attributes[$name]
        $bare = ([string] $name).Split(';')[0]
        if ($bare -in $script:BinaryAttributes) {
            $raw = $attribute.GetValues([byte[]])
            if ($bare -ieq 'objectSid') {
                $attributes[[string] $name] = @($raw | ForEach-Object {
                        ([System.Security.Principal.SecurityIdentifier]::new($_, 0)).Value
                    })
            }
            else {
                $attributes[[string] $name] = @($raw | ForEach-Object { ([guid]::new($_)).ToString() })
            }
            continue
        }
        $attributes[[string] $name] = @($attribute.GetValues([string]))
    }
    return New-AdgDirectoryEntry -DistinguishedName ([string] $Entry.DistinguishedName) -Attributes $attributes
}


# --- Ranged membership -------------------------------------------------------------------

function Get-AdgRangedAttributeState {
    <#
        .SYNOPSIS
            Find an attribute on an entry whether or not it arrived ranged, and say
            whether more values remain.
        .DESCRIPTION
            Returns the values present and the index the next range request must start
            from. Complete means the directory said this was the last chunk -- either by
            returning the attribute unranged, or by ending the range with '*'.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][hashtable] $Entry,
        [Parameter(Mandatory)][string] $Name
    )

    foreach ($key in $Entry.Attributes.Keys) {
        $text = [string] $key
        if ($text -ieq $Name) {
            return @{ Values = @(Get-AdgEntryValues -Entry $Entry -Name $text); Complete = $true; NextIndex = -1 }
        }
        if ($text -imatch "^$([regex]::Escape($Name));range=(?<low>\d+)-(?<high>[\d*]+)$") {
            $values = @(Get-AdgEntryValues -Entry $Entry -Name $text)
            $high = $Matches['high']
            if ($high -eq '*') {
                return @{ Values = $values; Complete = $true; NextIndex = -1 }
            }
            return @{ Values = $values; Complete = $false; NextIndex = ([int] $high + 1) }
        }
    }
    return @{ Values = @(); Complete = $true; NextIndex = -1 }
}


function Get-AdgGroupMemberReference {
    <#
        .SYNOPSIS
            Every distinguished name in a group's member attribute, following ranged
            retrieval to its end.
        .DESCRIPTION
            Active Directory returns at most MaxValRange (1500 by default) values of a
            multi-valued attribute per read, and signals the truncation only by renaming
            the attribute to 'member;range=0-1499'. A collector that stops there reports a
            5000-member group as a 1500-member group, and the 3500 people it dropped keep
            their access while the report says they have none.

            This function keeps requesting ranges until the directory marks the last one,
            and throws if the directory stops making progress rather than returning a
            partial list that looks complete.
    #>
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory)][hashtable] $Provider,
        [Parameter(Mandatory)][hashtable] $Entry,
        [int] $RangeStep = 1500,
        [int] $MaxAttempts = 3
    )

    $state = Get-AdgRangedAttributeState -Entry $Entry -Name 'member'
    $members = [System.Collections.Generic.List[string]]::new()
    foreach ($value in $state.Values) { $members.Add([string] $value) }

    $groupDn = $Entry.DistinguishedName
    $guard = 0
    while (-not $state.Complete) {
        if ($guard++ -gt 10000) {
            throw "Ranged retrieval of 'member' on '$groupDn' did not terminate. Refusing to report a partial member list as a complete one."
        }
        $next = $state.NextIndex
        $attribute = "member;range=$next-$($next + $RangeStep - 1)"
        $results = @(Invoke-AdgDirectoryOperation -MaxAttempts $MaxAttempts -Description "ranged member read of '$groupDn'" -Operation {
                Invoke-AdgDirectorySearch -Provider $Provider -SearchBase $groupDn -Filter '(objectClass=*)' `
                    -Attributes @($attribute) -Scope 'Base'
            })
        if ($results.Count -lt 1) {
            throw "Ranged retrieval of 'member' on '$groupDn' returned no object at range $next. The group's membership cannot be reported as complete."
        }
        $state = Get-AdgRangedAttributeState -Entry $results[0] -Name 'member'
        if ($state.Values.Count -eq 0 -and -not $state.Complete) {
            throw "Ranged retrieval of 'member' on '$groupDn' stopped making progress at range $next."
        }
        foreach ($value in $state.Values) { $members.Add([string] $value) }
    }

    return $members.ToArray()
}


# --- Configuration -----------------------------------------------------------------------

function New-AdgAdCollectorConfig {
    <#
        .SYNOPSIS
            A configuration with defaults applied and every value validated.
        .DESCRIPTION
            No secret is accepted here. A bearer token is read at run time from the
            environment variable named by CollectorKeyEnvironmentVariable or
            ApiTokenEnvironmentVariable, so a configuration
            file is safe to commit and to hand to an operator.
    #>
    [OutputType([hashtable])]
    param(
        [string] $Domain,
        [string] $DomainController,
        [int] $Port = 389,
        [bool] $UseTls = $false,
        [string] $SearchBase,
        [string[]] $IncludeOrganizationalUnits = @(),
        [string[]] $ExcludeOrganizationalUnits = @(),
        [int] $BatchSize = 500,
        [int] $PageSize = 1000,
        [int] $RangeStep = 1500,
        [string] $ApiBaseUrl,
        [string] $ApiTokenEnvironmentVariable = 'ADG_COLLECTOR_TOKEN',
        [string] $CollectorKeyEnvironmentVariable = 'ADG_COLLECTOR_KEY',
        [bool] $SkipCertificateCheck = $false,
        [string] $CollectorHost,
        [string] $CollectorVersion,
        [bool] $Offline = $false,
        [string] $OutputDirectory,
        [bool] $Incremental = $false,
        [string] $StateFile,
        [int] $MaxAttempts = 5,
        [int] $TimeoutSeconds = 120,
        [string] $PrincipalFilter,
        [string] $GroupFilter,
        [string] $Job,
        [string] $Passes = 'all',
        [Nullable[long]] $SinceUsn,
        [string] $CheckpointIssuer
    )

    $maxBatch = Get-AdgMaxBatchSize
    if ($BatchSize -lt 1 -or $BatchSize -gt $maxBatch) {
        throw "BatchSize must be between 1 and $maxBatch; received $BatchSize. The contract rejects an oversized batch rather than truncating it."
    }
    if ($RangeStep -lt 1) { throw "RangeStep must be at least 1; received $RangeStep." }
    if ($PageSize -lt 1) { throw "PageSize must be at least 1; received $PageSize." }
    if (-not $Offline -and $ApiBaseUrl -and $ApiBaseUrl -notmatch '^https?://') {
        throw "ApiBaseUrl must be an http or https URL; received '$ApiBaseUrl'."
    }
    if ($Offline -and -not $OutputDirectory) {
        throw 'Offline collection needs OutputDirectory: it is where the contract payloads are written instead of sent.'
    }
    if (-not $Offline -and -not $ApiBaseUrl) {
        throw 'Online collection needs ApiBaseUrl, or use offline mode with OutputDirectory.'
    }
    foreach ($ou in @($IncludeOrganizationalUnits) + @($ExcludeOrganizationalUnits)) {
        if ($ou -and $ou -notmatch '=') {
            throw "'$ou' is not a distinguished name. Include and exclude scopes are DNs, for example 'OU=Staff,DC=corp,DC=example,DC=com'."
        }
    }
    if (-not $CollectorHost) { $CollectorHost = $env:COMPUTERNAME }
    if (-not $CollectorHost) { $CollectorHost = [System.Net.Dns]::GetHostName() }
    if (-not $CollectorVersion) { $CollectorVersion = $script:CollectorVersion }

    if ($Passes -notin @('all', 'principals', 'memberships')) {
        throw "Passes must be 'all', 'principals' or 'memberships'; received '$Passes'. A run that reads one half of the domain has not enumerated the domain, so it is marked incremental and may never reconcile."
    }
    if ($null -ne $SinceUsn -and $SinceUsn -lt 0) {
        throw "SinceUsn must not be negative; received $SinceUsn."
    }
    if ($null -ne $SinceUsn -and [string]::IsNullOrWhiteSpace($CheckpointIssuer)) {
        throw 'A uSNChanged watermark needs the issuer that produced it. USNs are per-server counters, and one replayed against a different directory server -- or the same one after a restore from backup -- skips every object whose USN falls below it. Pass CheckpointIssuer with SinceUsn.'
    }
    if ($Job -and $Job -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
        throw "Job name '$Job' is not usable: it is the key the server stores this collector's checkpoint under."
    }

    return @{
        Domain                      = $Domain
        DomainController            = $DomainController
        Port                        = $Port
        UseTls                      = $UseTls
        SearchBase                  = $SearchBase
        IncludeOrganizationalUnits  = @($IncludeOrganizationalUnits | Where-Object { $_ })
        ExcludeOrganizationalUnits  = @($ExcludeOrganizationalUnits | Where-Object { $_ })
        BatchSize                   = $BatchSize
        PageSize                    = $PageSize
        RangeStep                   = $RangeStep
        ApiBaseUrl                  = $ApiBaseUrl
        ApiTokenEnvironmentVariable = $ApiTokenEnvironmentVariable
        CollectorKeyEnvironmentVariable = $CollectorKeyEnvironmentVariable
        SkipCertificateCheck        = $SkipCertificateCheck
        CollectorHost               = $CollectorHost
        CollectorVersion            = $CollectorVersion
        Offline                     = $Offline
        OutputDirectory             = $OutputDirectory
        Incremental                 = $Incremental
        StateFile                   = $StateFile
        MaxAttempts                 = $MaxAttempts
        TimeoutSeconds              = $TimeoutSeconds
        PrincipalFilter             = if ($PrincipalFilter) { $PrincipalFilter } else { $script:DefaultPrincipalFilter }
        GroupFilter                 = if ($GroupFilter) { $GroupFilter } else { $script:DefaultGroupFilter }
        Job                         = $Job
        Passes                      = $Passes
        SinceUsn                    = $SinceUsn
        CheckpointIssuer            = $CheckpointIssuer
    }
}


function Import-AdgAdCollectorConfig {
    <#
        .SYNOPSIS
            Load a JSON configuration file.
        .DESCRIPTION
            An unknown key is an error, not something to ignore: a misspelled
            'excludeOrganizationalUnits' would silently collect an OU the operator
            believed was excluded.
    #>
    [OutputType([hashtable])]
    param([Parameter(Mandatory)][string] $Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Collector configuration '$Path' does not exist. Copy collector/powershell/ad/config/adg-ad-collector.example.json and edit it."
    }
    $document = Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable -Depth 10

    $map = @{
        domain                      = 'Domain'
        domainController            = 'DomainController'
        port                        = 'Port'
        useTls                      = 'UseTls'
        searchBase                  = 'SearchBase'
        includeOrganizationalUnits  = 'IncludeOrganizationalUnits'
        excludeOrganizationalUnits  = 'ExcludeOrganizationalUnits'
        batchSize                   = 'BatchSize'
        pageSize                    = 'PageSize'
        rangeStep                   = 'RangeStep'
        apiBaseUrl                  = 'ApiBaseUrl'
        apiTokenEnvironmentVariable = 'ApiTokenEnvironmentVariable'
        collectorKeyEnvironmentVariable = 'CollectorKeyEnvironmentVariable'
        skipCertificateCheck        = 'SkipCertificateCheck'
        collectorHost               = 'CollectorHost'
        collectorVersion            = 'CollectorVersion'
        offline                     = 'Offline'
        outputDirectory             = 'OutputDirectory'
        incremental                 = 'Incremental'
        stateFile                   = 'StateFile'
        maxAttempts                 = 'MaxAttempts'
        timeoutSeconds              = 'TimeoutSeconds'
        principalFilter             = 'PrincipalFilter'
        groupFilter                 = 'GroupFilter'
        job                         = 'Job'
        passes                      = 'Passes'
        sinceUsn                    = 'SinceUsn'
        checkpointIssuer            = 'CheckpointIssuer'
    }

    $arguments = @{}
    foreach ($key in $document.Keys) {
        if ($key.StartsWith('$') -or $key -eq 'comment') { continue }
        if (-not $map.ContainsKey($key)) {
            throw "Unknown configuration key '$key' in '$Path'. Accepted keys: $(($map.Keys | Sort-Object) -join ', ')."
        }
        $value = $document[$key]
        if ($null -eq $value) { continue }
        $arguments[$map[$key]] = $value
    }

    if ($arguments.ContainsKey('ApiBaseUrl') -and $arguments['ApiBaseUrl'] -match '[?&](token|key|secret)=') {
        throw "ApiBaseUrl in '$Path' carries what looks like a secret. Put the collector token in the environment variable named by apiTokenEnvironmentVariable; secrets are never stored in configuration."
    }

    return New-AdgAdCollectorConfig @arguments
}


# --- Collection state --------------------------------------------------------------------

function New-AdgCollectionState {
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][hashtable] $Publisher,
        [Parameter(Mandatory)][hashtable] $Config
    )

    return @{
        RunId            = $RunId
        Publisher        = $Publisher
        Config           = $Config
        Buffer           = [System.Collections.Generic.List[System.Collections.IDictionary]]::new()
        Sequence         = 0
        BatchCount       = 0
        ObservationCount = 0
        PrincipalCount   = 0
        EdgeCount        = 0
        EmittedKeys      = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
        Errors           = [System.Collections.Generic.List[System.Collections.IDictionary]]::new()
        Cursor           = $null
        HighestUsn       = $null
        HighestWhen      = $null
        # The filters this run issues, which are the configured ones narrowed by the
        # watermark when it is running as a delta. Held on the state rather than recomputed
        # in each pass, so the two passes cannot end up filtering differently and producing
        # one run that read its scope under two rules.
        PrincipalFilter  = $Config.PrincipalFilter
        GroupFilter      = $Config.GroupFilter
    }
}


function Add-AdgCollectionError {
    <#
        .SYNOPSIS
            Record something the collector could not read.
        .DESCRIPTION
            Every call here costs the run its ability to reconcile, which is exactly the
            intent: a scan that could not read part of the directory must not be able to
            mark anything absent.
    #>
    param(
        [Parameter(Mandatory)][hashtable] $State,
        [Parameter(Mandatory)][string] $Code,
        [Parameter(Mandatory)][string] $Message,
        [string] $Target
    )

    $State.Errors.Add((New-AdgCollectorError -Code $Code -Message $Message -Target $Target))
    Write-Warning "[$Code] $Message$(if ($Target) { " (target: $Target)" })"
}


function Add-AdgCollectedObservation {
    <#
        .SYNOPSIS
            Buffer an observation, flushing a batch when it is full.
        .DESCRIPTION
            An object can legitimately be reached twice in one run -- a principal
            enumerated in its OU and again as a group member -- and two observations with
            one source_key in a batch are indistinguishable. The first wins; the duplicate
            is dropped here rather than being rejected by the API.
    #>
    param(
        [Parameter(Mandatory)][hashtable] $State,
        [Parameter(Mandatory)][System.Collections.IDictionary] $Observation
    )

    if (-not $State.EmittedKeys.Add([string] $Observation['source_key'])) { return }

    $State.Buffer.Add($Observation)
    if ($Observation['kind'] -eq 'principal') { $State.PrincipalCount++ } else { $State.EdgeCount++ }
    if ($State.Buffer.Count -ge $State.Config.BatchSize) {
        Publish-AdgBufferedBatch -State $State
    }
}


function Publish-AdgBufferedBatch {
    <#
        .SYNOPSIS
            Send the buffered observations as one batch.
    #>
    param(
        [Parameter(Mandatory)][hashtable] $State,
        [bool] $IsFinal = $false
    )

    if ($State.Buffer.Count -eq 0) { return }

    $State.Sequence++
    $batch = New-AdgObservationBatch -RunId $State.RunId -BatchId (New-AdgIdentifier) `
        -Sequence $State.Sequence -Observations $State.Buffer.ToArray() -IsFinal $IsFinal `
        -ContinuationToken $State.Cursor
    Publish-AdgPayload -Publisher $State.Publisher -PayloadKind 'batch' -Payload $batch | Out-Null
    $State.ObservationCount += $State.Buffer.Count
    $State.BatchCount++
    $State.Buffer.Clear()
}


function Update-AdgChangeWatermark {
    <#
        .SYNOPSIS
            Track the highest uSNChanged and whenChanged seen in this run.
    #>
    param(
        [Parameter(Mandatory)][hashtable] $State,
        [Parameter(Mandatory)][hashtable] $Entry
    )

    $metadata = Get-AdgEntryChangeMetadata -Entry $Entry
    if ($null -ne $metadata.UsnChanged) {
        if ($null -eq $State.HighestUsn -or $metadata.UsnChanged -gt $State.HighestUsn) {
            $State.HighestUsn = $metadata.UsnChanged
        }
    }
    if ($metadata.WhenChanged) {
        $when = [string] $metadata.WhenChanged
        if ($null -eq $State.HighestWhen -or $when -gt $State.HighestWhen) {
            $State.HighestWhen = $when
        }
    }
}


# --- Collection --------------------------------------------------------------------------

function Add-AdgUsnFilter {
    <#
        .SYNOPSIS
            Narrow a filter to objects whose uSNChanged is at or above a watermark.
        .DESCRIPTION
            Greater-or-*equal*, not greater-than, and the watermark the collector saves is
            the highest USN it actually saw. The overlap of one object is deliberate: the
            alternative is an off-by-one that drops exactly the object that sat on the
            boundary, and re-reading one object costs nothing while losing one is
            undetectable.

            What this filter cannot do is report a *deletion*. A deleted object is moved to
            the Deleted Objects container and produces no entry this search can return, so
            nothing arrives -- which is also what an unchanged object does. Only a full
            reconciliation tells those apart; see
            docs/architecture/incremental-collection.md.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $Filter,
        [Parameter(Mandatory)][long] $SinceUsn
    )

    return "(&(uSNChanged>=$SinceUsn)$Filter)"
}


function Get-AdgSearchBase {
    <#
        .SYNOPSIS
            The distinguished names this run enumerates.
    #>
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory)][hashtable] $Config,
        [Parameter(Mandatory)][hashtable] $Provider
    )

    if ($Config.IncludeOrganizationalUnits.Count -gt 0) {
        return @($Config.IncludeOrganizationalUnits)
    }
    if ($Config.SearchBase) { return @($Config.SearchBase) }
    if (-not $Provider.DefaultNamingContext) {
        throw 'The directory did not report a default naming context and no SearchBase was configured, so the collector does not know what to enumerate.'
    }
    return @($Provider.DefaultNamingContext)
}


function Test-AdgEntryExcluded {
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][hashtable] $Config,
        [Parameter(Mandatory)][AllowEmptyString()][string] $DistinguishedName
    )

    foreach ($excluded in $Config.ExcludeOrganizationalUnits) {
        if (Test-AdgDistinguishedNameUnder -DistinguishedName $DistinguishedName -Container $excluded) {
            return $true
        }
    }
    return $false
}


function Invoke-AdgAdPrincipalPass {
    <#
        .SYNOPSIS
            Enumerate principals and emit one observation each, plus every primary-group
            edge.
        .DESCRIPTION
            Returns the distinguished-name index the membership pass resolves member
            references against. Building it here means the common case costs no extra
            directory round trips: almost every member of an in-scope group is itself
            in scope.

            primaryGroupID is read here because it is the one membership that does not
            appear in any group's member attribute. A collector that reads only 'member'
            loses every user's primary group -- usually Domain Users, which is on a great
            many ACLs.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][hashtable] $State,
        [Parameter(Mandatory)][hashtable] $Provider,
        [bool] $Emit = $true
    )

    $config = $State.Config
    $index = [System.Collections.Generic.Dictionary[string, hashtable]]::new([System.StringComparer]::OrdinalIgnoreCase)

    foreach ($base in (Get-AdgSearchBase -Config $config -Provider $Provider)) {
        $entries = $null
        try {
            $entries = @(Invoke-AdgDirectoryOperation -MaxAttempts $config.MaxAttempts -Description "principal search under '$base'" -Operation {
                    Invoke-AdgDirectorySearch -Provider $Provider -SearchBase $base -Filter $State.PrincipalFilter `
                        -Attributes $script:PrincipalAttributes -Scope 'Subtree'
                })
        }
        catch {
            Add-AdgCollectionError -State $State -Code 'principal_search_failed' -Target $base `
                -Message "Could not enumerate principals under '$base': $($_.Exception.Message)"
            continue
        }

        $position = 0
        foreach ($entry in $entries) {
            $position++
            $dn = $entry.DistinguishedName
            if (Test-AdgEntryExcluded -Config $config -DistinguishedName $dn) { continue }
            $State.Cursor = "pass=principals;base=$base;index=$position"

            try {
                $observation = ConvertTo-AdgPrincipalObservationFromEntry -Entry $entry -RunId $State.RunId
            }
            catch {
                Add-AdgCollectionError -State $State -Code 'principal_translation_failed' -Target $dn `
                    -Message "Could not build an observation for '$dn': $($_.Exception.Message)"
                continue
            }

            if ($Emit) { Add-AdgCollectedObservation -State $State -Observation $observation }
            # The watermark advances over every entry this run *read*, emitted or not. A
            # memberships job that read a principal and chose not to send it has still
            # covered that principal's uSNChanged, and a watermark that lagged what was read
            # would make the next delta re-read a range for no reason.
            Update-AdgChangeWatermark -State $State -Entry $entry

            $sid = [string] $observation['sid']
            $kind = [string] $observation['principal_kind']
            $index[$dn] = @{ Sid = $sid; Kind = $kind }

            if ($kind -ne 'domain_group') {
                Add-AdgPrimaryGroupEdge -State $State -Entry $entry -Sid $sid -MemberKind $kind
            }
        }
    }

    return @{ Index = $index }
}


function Add-AdgPrimaryGroupEdge {
    <#
        .SYNOPSIS
            Emit the primary_group edge for an account, when the directory stated one.
    #>
    param(
        [Parameter(Mandatory)][hashtable] $State,
        [Parameter(Mandatory)][hashtable] $Entry,
        [Parameter(Mandatory)][string] $Sid,
        [Parameter(Mandatory)][string] $MemberKind
    )

    $rid = Get-AdgEntryInteger -Entry $Entry -Name 'primaryGroupID'
    if ($null -eq $rid) { return }

    $domainSid = Get-AdgDomainSidFromSid $Sid
    if (-not $domainSid) {
        Add-AdgCollectionError -State $State -Code 'primary_group_undeterminable' -Target $Entry.DistinguishedName `
            -Message "'$($Entry.DistinguishedName)' declares primaryGroupID $rid but its SID $Sid is not a domain SID, so the group's SID cannot be derived."
        return
    }

    $groupSid = Get-AdgSidWithRid -DomainSid $domainSid -Rid $rid
    if ($groupSid -eq $Sid) { return }

    Add-AdgCollectedObservation -State $State -Observation (
        New-AdgMembershipObservation -RunId $State.RunId -GroupSid $groupSid -MemberSid $Sid `
            -EdgeKind 'primary_group' -MemberKind $MemberKind)
}


function Resolve-AdgMemberReference {
    <#
        .SYNOPSIS
            Turn a member distinguished name into the SID and kind of the principal it
            names.
        .DESCRIPTION
            Three cases, in the order they are cheapest to answer:

              1. a foreign security principal, whose DN is literally the member's SID --
                 often everything this domain knows about a trusted-forest principal,
                 and enough, because SID is identity;
              2. an object already enumerated in this run;
              3. anything else, which is read directly. This covers members outside the
                 configured scope: an excluded OU limits what the run enumerates, but a
                 principal that holds membership in an in-scope group is still recorded,
                 because an edge pointing at a SID with no principal behind it cannot be
                 audited.

            A miss returns $null and the caller decides whether to refresh and retry or to
            report it. No edge is ever invented for an unresolvable reference.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][hashtable] $State,
        [Parameter(Mandatory)][hashtable] $Provider,
        [Parameter(Mandatory)][System.Collections.Generic.Dictionary[string, hashtable]] $Index,
        [Parameter(Mandatory)][string] $MemberDn
    )

    $foreignSid = Get-AdgForeignSecurityPrincipalSid -DistinguishedName $MemberDn
    if ($foreignSid) {
        return @{ Sid = $foreignSid; Kind = 'foreign_security_principal'; Foreign = $true; Entry = $null }
    }

    if ($Index.ContainsKey($MemberDn)) {
        $known = $Index[$MemberDn]
        return @{ Sid = $known.Sid; Kind = $known.Kind; Foreign = $false; Entry = $null }
    }

    $results = @(Invoke-AdgDirectoryOperation -MaxAttempts $State.Config.MaxAttempts -Description "lookup of '$MemberDn'" -Operation {
            Invoke-AdgDirectorySearch -Provider $Provider -SearchBase $MemberDn -Filter '(objectClass=*)' `
                -Attributes $script:PrincipalAttributes -Scope 'Base'
        })
    if ($results.Count -lt 1) { return $null }

    $entry = $results[0]
    $sid = Get-AdgEntryValue -Entry $entry -Name 'objectSid'
    if (-not $sid) { return $null }

    $resolved = @{
        Sid     = ([string] $sid).ToUpperInvariant()
        Kind    = (Get-AdgPrincipalKindFromEntry -Entry $entry)
        Foreign = $false
        Entry   = $entry
    }
    $Index[$MemberDn] = @{ Sid = $resolved.Sid; Kind = $resolved.Kind }
    return $resolved
}


function Invoke-AdgAdMembershipPass {
    <#
        .SYNOPSIS
            Emit one direct edge per group member. Nothing is expanded.
        .DESCRIPTION
            This is the function ADR-0002 is about. When group A contains group B and
            group B contains user C, this emits exactly two edges -- A to B, and B to C --
            and never the A-to-C edge that a flattening collector would produce. The
            difference matters when somebody has to answer "why does C have this, and what
            do I remove to take it away".

            Nesting is therefore never followed: a member that happens to be a group is
            recorded as a member and nothing more. Its own members are collected when the
            enumeration reaches it, which also means a membership cycle terminates on its
            own rather than needing to be detected.
    #>
    param(
        [Parameter(Mandatory)][hashtable] $State,
        [Parameter(Mandatory)][hashtable] $Provider,
        [Parameter(Mandatory)][System.Collections.Generic.Dictionary[string, hashtable]] $Index
    )

    $config = $State.Config

    foreach ($base in (Get-AdgSearchBase -Config $config -Provider $Provider)) {
        $groups = $null
        try {
            $groups = @(Invoke-AdgDirectoryOperation -MaxAttempts $config.MaxAttempts -Description "group search under '$base'" -Operation {
                    Invoke-AdgDirectorySearch -Provider $Provider -SearchBase $base -Filter $State.GroupFilter `
                        -Attributes ($script:PrincipalAttributes + 'member') -Scope 'Subtree'
                })
        }
        catch {
            Add-AdgCollectionError -State $State -Code 'group_search_failed' -Target $base `
                -Message "Could not enumerate groups under '$base': $($_.Exception.Message)"
            continue
        }

        $position = 0
        foreach ($group in $groups) {
            $position++
            $groupDn = $group.DistinguishedName
            if (Test-AdgEntryExcluded -Config $config -DistinguishedName $groupDn) { continue }
            $State.Cursor = "pass=membership;base=$base;index=$position"

            $groupSid = Get-AdgEntryValue -Entry $group -Name 'objectSid'
            if (-not $groupSid) {
                Add-AdgCollectionError -State $State -Code 'group_without_sid' -Target $groupDn `
                    -Message "The group '$groupDn' has no objectSid, so its membership cannot be attributed to a principal."
                continue
            }
            $groupSid = ([string] $groupSid).ToUpperInvariant()

            $members = $null
            try {
                $members = @(Get-AdgGroupMemberReference -Provider $Provider -Entry $group -RangeStep $config.RangeStep -MaxAttempts $config.MaxAttempts)
            }
            catch {
                Add-AdgCollectionError -State $State -Code 'member_read_failed' -Target $groupDn `
                    -Message "Could not read the full membership of '$groupDn': $($_.Exception.Message)"
                continue
            }

            Add-AdgGroupMemberEdge -State $State -Provider $Provider -Index $Index `
                -GroupDn $groupDn -GroupSid $groupSid -MemberDns $members -Refreshed $false
        }
    }
}


function Add-AdgGroupMemberEdge {
    <#
        .SYNOPSIS
            Emit the direct edges for one group's member list, recovering once from a
            rename that happened mid-scan.
        .DESCRIPTION
            A member DN read a moment ago can stop resolving while the scan is still
            running, because somebody moved or renamed the object. Active Directory
            rewrites the DN inside every group's member attribute when that happens, so
            re-reading the group's membership yields the new name -- and the principal's
            SID, which is what ADG keys on, never changed. The re-read happens once; a
            reference that still does not resolve is reported as an error and no edge is
            invented for it.
    #>
    param(
        [Parameter(Mandatory)][hashtable] $State,
        [Parameter(Mandatory)][hashtable] $Provider,
        [Parameter(Mandatory)][System.Collections.Generic.Dictionary[string, hashtable]] $Index,
        [Parameter(Mandatory)][string] $GroupDn,
        [Parameter(Mandatory)][string] $GroupSid,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]] $MemberDns,
        [bool] $Refreshed
    )

    $unresolved = [System.Collections.Generic.List[string]]::new()

    foreach ($memberDn in $MemberDns) {
        if (Test-AdgDistinguishedNameUnder -DistinguishedName $memberDn -Container $GroupDn) {
            # A group listing itself is not something Windows creates; emitting the edge
            # would violate the domain model, and ignoring it silently would hide whatever
            # produced it.
            if ((ConvertTo-AdgNormalizedDistinguishedName $memberDn) -eq (ConvertTo-AdgNormalizedDistinguishedName $GroupDn)) {
                Add-AdgCollectionError -State $State -Code 'self_membership' -Target $GroupDn `
                    -Message "The group '$GroupDn' lists itself as a direct member. A group is never a direct member of itself; the edge was not recorded."
                continue
            }
        }

        $resolved = $null
        try {
            $resolved = Resolve-AdgMemberReference -State $State -Provider $Provider -Index $Index -MemberDn $memberDn
        }
        catch {
            $resolved = $null
        }

        if ($null -eq $resolved) {
            $unresolved.Add($memberDn)
            continue
        }

        if ($resolved.Sid -eq $GroupSid) {
            Add-AdgCollectionError -State $State -Code 'self_membership' -Target $GroupDn `
                -Message "The group '$GroupDn' resolves one of its members to its own SID $GroupSid. A group is never a direct member of itself; the edge was not recorded."
            continue
        }

        if ($resolved.Entry) {
            # A principal outside the enumerated scope, read because an in-scope group
            # grants it membership. Recording it keeps the edge auditable.
            try {
                Add-AdgCollectedObservation -State $State -Observation (
                    ConvertTo-AdgPrincipalObservationFromEntry -Entry $resolved.Entry -RunId $State.RunId)
            }
            catch {
                Add-AdgCollectionError -State $State -Code 'principal_translation_failed' -Target $memberDn `
                    -Message "Could not build an observation for the member '$memberDn': $($_.Exception.Message)"
            }
        }
        elseif ($resolved.Foreign) {
            # The stub object carries no attributes worth reporting, but the SID is a real
            # principal that may hold access, so it is recorded rather than dropped.
            Add-AdgCollectedObservation -State $State -Observation (
                New-AdgPrincipalObservation -RunId $State.RunId -Sid $resolved.Sid `
                    -PrincipalKind 'foreign_security_principal' -DistinguishedName $memberDn)
        }

        Add-AdgCollectedObservation -State $State -Observation (
            New-AdgMembershipObservation -RunId $State.RunId -GroupSid $GroupSid -MemberSid $resolved.Sid `
                -EdgeKind 'directory_group_member' -MemberKind $resolved.Kind `
                -IsForeignSecurityPrincipal ([bool] $resolved.Foreign))
    }

    if ($unresolved.Count -eq 0) { return }

    if ($Refreshed) {
        foreach ($memberDn in $unresolved) {
            Add-AdgCollectionError -State $State -Code 'member_unresolved' -Target $memberDn `
                -Message "The group '$GroupDn' lists '$memberDn' as a member, but that object could not be read, so its SID is unknown and no edge was recorded. The run cannot claim complete coverage."
        }
        return
    }

    # An object that stopped resolving mid-scan was very likely moved or renamed, and
    # Active Directory rewrites the DN inside every group's member attribute when that
    # happens. Re-reading the membership once yields the new name; the principal's SID,
    # which is what ADG keys on, never changed.
    $refreshedMembers = $null
    try {
        $results = @(Invoke-AdgDirectoryOperation -MaxAttempts $State.Config.MaxAttempts -Description "membership re-read of '$GroupDn'" -Operation {
                Invoke-AdgDirectorySearch -Provider $Provider -SearchBase $GroupDn -Filter '(objectClass=*)' `
                    -Attributes @('member') -Scope 'Base'
            })
        if ($results.Count -gt 0) {
            $refreshedMembers = @(Get-AdgGroupMemberReference -Provider $Provider -Entry $results[0] `
                    -RangeStep $State.Config.RangeStep -MaxAttempts $State.Config.MaxAttempts)
        }
    }
    catch {
        $refreshedMembers = $null
    }

    if ($null -eq $refreshedMembers) {
        foreach ($memberDn in $unresolved) {
            Add-AdgCollectionError -State $State -Code 'member_unresolved' -Target $memberDn `
                -Message "The group '$GroupDn' lists '$memberDn' as a member, that object could not be read, and the group's membership could not be re-read to check for a rename. No edge was recorded and the run cannot claim complete coverage."
        }
        return
    }

    $normalizedRefreshed = @($refreshedMembers | ForEach-Object { ConvertTo-AdgNormalizedDistinguishedName $_ })
    $normalizedOriginal = @($MemberDns | ForEach-Object { ConvertTo-AdgNormalizedDistinguishedName $_ })

    # Still listed under the same name: this is a genuinely unreadable object, not a rename.
    $stillListed = @($unresolved | Where-Object {
            $normalizedRefreshed -contains (ConvertTo-AdgNormalizedDistinguishedName $_)
        })
    foreach ($memberDn in $stillListed) {
        Add-AdgCollectionError -State $State -Code 'member_unresolved' -Target $memberDn `
            -Message "The group '$GroupDn' lists '$memberDn' as a member, but that object could not be read even after re-reading the membership, so its SID is unknown and no edge was recorded. The run cannot claim complete coverage."
    }

    $vanished = $unresolved.Count - $stillListed.Count
    $newMembers = @($refreshedMembers | Where-Object {
            $normalizedOriginal -notcontains (ConvertTo-AdgNormalizedDistinguishedName $_)
        })

    $before = $State.EdgeCount
    if ($newMembers.Count -gt 0) {
        Add-AdgGroupMemberEdge -State $State -Provider $Provider -Index $Index `
            -GroupDn $GroupDn -GroupSid $GroupSid -MemberDns $newMembers -Refreshed $true
    }
    $recovered = $State.EdgeCount - $before

    # Every reference that disappeared has to be accounted for by an edge the re-read
    # produced. A shortfall means a member was lost between the two reads, which is a gap
    # in coverage even though nothing failed outright.
    if ($vanished -gt $recovered) {
        Add-AdgCollectionError -State $State -Code 'member_lost_during_scan' -Target $GroupDn `
            -Message "$($vanished - $recovered) member reference(s) of '$GroupDn' stopped resolving during the scan and were not accounted for by re-reading its membership. Those edges are missing from this run."
    }
}


function Invoke-AdgAdCollection {
    <#
        .SYNOPSIS
            Run one Active Directory collection: start, observe, complete.
        .DESCRIPTION
            The run declares the domain scope, but reconciles it only when it enumerated
            the whole naming context cleanly. A run narrowed by include or exclude OUs is
            marked incremental, which makes it structurally unable to reconcile: it read
            part of the domain on purpose, and letting it mark the rest absent would
            delete real access from the record.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][hashtable] $Config,
        [Parameter(Mandatory)][hashtable] $Provider,
        [Parameter(Mandatory)][hashtable] $Publisher,
        [string] $RunId
    )

    if (-not $RunId) { $RunId = New-AdgIdentifier }
    $startedAt = Get-AdgTimestamp

    $domainSid = if ($Config.Domain -and $Config.Domain -match '^S-1-5-21-') {
        $Config.Domain
    }
    else {
        $Provider.DomainSid
    }
    if (-not $domainSid) {
        $domainSid = Get-AdgDomainSidFromProvider -Provider $Provider
    }
    if (-not $domainSid) {
        throw 'The collector could not determine the domain SID, so it cannot declare the scope this run enumerates. Name the domain controller explicitly, or set searchBase.'
    }

    # --- what this run is, before a single entry is read ---------------------------------
    #
    # Three statements, and the protocol requires all of them to be made *up front*:
    # `incremental` because the server refuses to let it change mid-run, `mode` because it
    # must agree with `incremental`, and the scope because a run may only reconcile what it
    # declared. Deciding any of them from what the run turned out to read would be deciding
    # coverage after the fact.
    $passes = if ($Config.ContainsKey('Passes') -and $Config.Passes) { [string] $Config.Passes } else { 'all' }
    $narrowed = ($Config.IncludeOrganizationalUnits.Count -gt 0) -or ($Config.ExcludeOrganizationalUnits.Count -gt 0)

    $issuer = if ($Config.ContainsKey('CheckpointIssuer') -and $Config.CheckpointIssuer) {
        [string] $Config.CheckpointIssuer
    }
    else {
        Get-AdgDirectoryIssuer -Provider $Provider
    }

    $sinceUsn = if ($Config.ContainsKey('SinceUsn')) { $Config.SinceUsn } else { $null }
    $delta = $false
    if ($null -ne $sinceUsn) {
        if ([string]::IsNullOrWhiteSpace($issuer)) {
            # The watermark cannot be tied to the incarnation of the directory that issued
            # it, so it cannot be shown to still mean what it meant. Reading everything
            # costs one scan; resuming on a watermark whose provenance is unknown costs an
            # audit that is silently missing whatever fell below it.
            Write-Warning "Job '$($Config.Job)' was given a uSNChanged watermark but this run could not establish the directory server's identity, so it reads everything instead of resuming."
        }
        elseif ($Config.CheckpointIssuer -and $Config.CheckpointIssuer -ine $issuer) {
            Write-Warning "The watermark was issued by '$($Config.CheckpointIssuer)' and this run bound '$issuer'. A uSNChanged cursor is local to one directory-server incarnation, so this run reads everything."
        }
        else {
            $delta = $true
        }
    }

    # A pass that reads one half of the domain has not enumerated the domain. It declares
    # the scope -- that is what it set out to look at -- and marks itself incremental, which
    # is what structurally prevents it from reconciling. A principals pass that reconciled
    # the domain would mark every membership edge absent, because it observed none.
    $partial = ($passes -ne 'all')
    $incremental = [bool] ($Config.Incremental -or $narrowed -or $delta -or $partial)
    $mode = if ($delta) { 'delta' } elseif ($incremental) { 'delta' } else { 'full' }
    $scope = New-AdgScope -Kind 'domain' -Key $domainSid

    $searchBases = @(Get-AdgSearchBase -Config $Config -Provider $Provider)
    $reasons = [System.Collections.Generic.List[string]]::new()
    if ($narrowed) { $reasons.Add("narrowed to $($searchBases.Count) search base(s)") }
    if ($partial) { $reasons.Add("reads only the $passes of the domain") }
    if ($delta) { $reasons.Add("resumed from uSNChanged $sinceUsn issued by '$issuer'") }
    $notes = if ($reasons.Count -gt 0) {
        "$($reasons -join '; '); marked incremental so it cannot reconcile the domain scope."
    }
    else {
        $null
    }

    $baseline = if ($delta) {
        @{ kind = 'usn'; token = [string] $sinceUsn; issuer = $issuer; issued_at = $startedAt }
    }
    else { $null }

    $start = New-AdgScanRunStart -RunId $RunId -Collector 'active_directory' `
        -CollectorHost $Config.CollectorHost -Method 'System.DirectoryServices.Protocols.LdapConnection' `
        -CollectorVersion $Config.CollectorVersion -Target ($searchBases -join ';') `
        -StartedAt $startedAt -Scopes @($scope) -Incremental $incremental -Notes $notes `
        -Mode $mode -Job ([string] $Config.Job) -Baseline $baseline
    Publish-AdgPayload -Publisher $Publisher -PayloadKind 'start' -Payload $start | Out-Null

    $state = New-AdgCollectionState -RunId $RunId -Publisher $Publisher -Config $Config
    if ($delta) {
        $state.PrincipalFilter = Add-AdgUsnFilter -Filter $Config.PrincipalFilter -SinceUsn ([long] $sinceUsn)
        $state.GroupFilter = Add-AdgUsnFilter -Filter $Config.GroupFilter -SinceUsn ([long] $sinceUsn)
    }
    $status = 'succeeded'

    try {
        # The principal pass always runs, even for a memberships-only job: the membership
        # pass resolves member references against the index it builds, and without it every
        # edge would cost an extra directory round trip. What a memberships job suppresses
        # is the *emission* of principals, not the reading of them.
        $pass = Invoke-AdgAdPrincipalPass -State $state -Provider $Provider -Emit:($passes -ne 'memberships')
        if ($passes -ne 'principals') {
            Invoke-AdgAdMembershipPass -State $state -Provider $Provider -Index $pass.Index
        }
        Publish-AdgBufferedBatch -State $state -IsFinal $true
    }
    catch {
        # The run is closed rather than abandoned: a run left without a completion says
        # nothing about why it stopped, and ADG treats it as failed only after a timeout.
        Add-AdgCollectionError -State $state -Code 'collection_aborted' `
            -Message "Collection stopped: $($_.Exception.Message)"
        $status = 'failed'
        try {
            # What was already collected is still worth sending: a failed run's
            # observations are valid, only its coverage is unknown.
            Publish-AdgBufferedBatch -State $state -IsFinal $true
        }
        catch {
            Add-AdgCollectionError -State $state -Code 'final_batch_lost' `
                -Message "The observations buffered when collection stopped could not be sent: $($_.Exception.Message)"
        }
    }

    if ($status -ne 'failed' -and $state.Errors.Count -gt 0) { $status = 'partial' }

    $reconciled = @(if ($status -eq 'succeeded' -and -not $incremental) { $scope })
    $completedAt = Get-AdgTimestamp

    # The cursor this run may hand to the next one. Only a clean run leaves one: a partial
    # run did not read everything below its highest uSNChanged, so a later run starting
    # there would skip exactly the objects this one failed on -- and nothing afterwards
    # would look missing. The server enforces the same rule, and refuses one from a run it
    # downgraded; both are needed, because the collector knows about its own errors and
    # only the server knows what actually arrived.
    $checkpoint = if ($status -eq 'succeeded' -and $issuer -and $null -ne $state.HighestUsn) {
        @{ kind = 'usn'; token = [string] $state.HighestUsn; issuer = $issuer; issued_at = $completedAt }
    }
    else { $null }

    if ($status -eq 'succeeded' -and -not $issuer -and $Config.Job) {
        # Said once, where it is actionable, and only for a run that belongs to a scheduled
        # job -- because that is the only case where the absence has a lasting cost: the job
        # will read the whole directory on every run, for ever, and look healthy doing it.
        Write-Warning "Job '$($Config.Job)' left no checkpoint: the directory server's identity (dsServiceName and invocationId) could not be established, so a uSNChanged watermark could not be tied to the incarnation that issued it. Every run of this job will read the whole directory. Run with -Verbose to see which read failed."
    }

    $completion = New-AdgScanRunCompletion -RunId $RunId -Status $status -CompletedAt $completedAt `
        -BatchCount $state.BatchCount -ObservationCount $state.ObservationCount `
        -Errors $state.Errors.ToArray() -ReconciledScopes $reconciled -Checkpoint $checkpoint
    Publish-AdgPayload -Publisher $Publisher -PayloadKind 'completion' -Payload $completion | Out-Null

    $summary = @{
        RunId            = $RunId
        Status           = $status
        Incremental      = $incremental
        BatchCount       = $state.BatchCount
        ObservationCount = $state.ObservationCount
        PrincipalCount   = $state.PrincipalCount
        EdgeCount        = $state.EdgeCount
        ErrorCount       = $state.Errors.Count
        Errors           = $state.Errors.ToArray()
        Reconciled       = ($reconciled.Count -gt 0)
        DomainSid        = $domainSid
        SearchBases      = $searchBases
        HighestUsn       = $state.HighestUsn
        HighestWhen      = $state.HighestWhen
        Server           = $Provider.Server
        Mode             = $mode
        Passes           = $passes
        Issuer           = $issuer
        Checkpoint       = if ($null -eq $checkpoint) { $null } else {
            [pscustomobject]@{
                Kind     = $checkpoint.kind
                Token    = $checkpoint.token
                Issuer   = $checkpoint.issuer
                IssuedAt = $checkpoint.issued_at
            }
        }
        StartedAt        = $startedAt
        EndedAt          = $completedAt
    }

    if ($Config.StateFile) {
        Save-AdgAdCollectorState -Path $Config.StateFile -Summary $summary
    }
    return $summary
}


function Get-AdgDomainSidFromProvider {
    <#
        .SYNOPSIS
            The domain SID, read from the naming context's own object.
        .DESCRIPTION
            The domainDNS object at the root of the naming context carries the domain SID.
            It is read rather than derived from any principal, because a principal in the
            naming context may be a foreign security principal whose SID belongs to a
            different domain entirely.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][hashtable] $Provider)

    if ($Provider.DomainSid) { return $Provider.DomainSid }
    if (-not $Provider.DefaultNamingContext) { return $null }

    $results = @(Invoke-AdgDirectorySearch -Provider $Provider -SearchBase $Provider.DefaultNamingContext `
            -Filter '(objectClass=*)' -Attributes @('objectSid') -Scope 'Base')
    if ($results.Count -lt 1) { return $null }
    $sid = Get-AdgEntryValue -Entry $results[0] -Name 'objectSid'
    if (-not $sid) { return $null }
    return ([string] $sid).ToUpperInvariant()
}


# --- Collector state ---------------------------------------------------------------------

function Save-AdgAdCollectorState {
    <#
        .SYNOPSIS
            Record the change watermark this run reached.
        .DESCRIPTION
            Only a clean, complete run may leave a watermark. A partial run did not read
            everything below its highest uSNChanged, so a later run starting from that
            watermark would skip exactly the objects this one failed to read -- and the
            gap would never be noticed, because nothing would look missing.

            The watermark also records which directory server issued it. USNs are
            per-server counters, so a watermark from DC1 means nothing to DC2.
    #>
    param(
        [Parameter(Mandatory)][string] $Path,
        [Parameter(Mandatory)][hashtable] $Summary
    )

    if ($Summary.Status -ne 'succeeded') {
        Write-Warning "The run finished as '$($Summary.Status)', so no change watermark was written to '$Path'. A watermark from an incomplete run would make the next run skip what this one could not read."
        return
    }

    $directory = Split-Path -Parent $Path
    if ($directory -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    $document = [ordered]@{
        schema              = 'adg-ad-collector-state/1'
        run_id              = $Summary.RunId
        status              = $Summary.Status
        completed_at        = (Get-AdgTimestamp)
        directory_server    = $Summary.Server
        domain_sid          = $Summary.DomainSid
        search_bases        = @($Summary.SearchBases)
        incremental         = $Summary.Incremental
        highest_usn_changed = $Summary.HighestUsn
        highest_when_changed = $Summary.HighestWhen
        principal_count     = $Summary.PrincipalCount
        edge_count          = $Summary.EdgeCount
    }
    $document | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $Path -Encoding utf8NoBOM
}


function Test-AdgAdCollectorStateUsable {
    <#
        .SYNOPSIS
            Whether a saved watermark may be used against this directory server.
        .DESCRIPTION
            uSNChanged is a per-server counter. Replaying DC1's watermark against DC2
            would silently skip every object whose USN on DC2 happens to fall below it, so
            a mismatch means a full re-read, not a smaller one.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][System.Collections.IDictionary] $State,
        [Parameter(Mandatory)][string] $Server
    )

    if (-not $State.Contains('directory_server') -or -not $State['directory_server']) {
        return @{ Usable = $false; Reason = 'The saved state does not name the directory server that issued its watermark.' }
    }
    if ([string] $State['directory_server'] -ine $Server) {
        return @{
            Usable = $false
            Reason = "The watermark came from '$($State['directory_server'])' but this run binds '$Server'. uSNChanged is a per-server counter, so the watermark does not transfer; this run must read everything."
        }
    }
    if (-not $State.Contains('status') -or [string] $State['status'] -ne 'succeeded') {
        return @{ Usable = $false; Reason = 'The watermark was left by a run that did not succeed, so it does not mark a point everything below was read.' }
    }
    if ($State.Contains('incremental') -and $State['incremental']) {
        # An incremental run read only part of the tree on purpose. Its highest uSNChanged
        # is above objects it never looked at, so starting there would skip them for good.
        return @{
            Usable = $false
            Reason = 'The watermark was left by an incremental run, which deliberately read only part of the directory. Objects it never looked at sit below that uSNChanged and would be skipped forever.'
        }
    }
    if (-not $State.Contains('highest_usn_changed') -or $null -eq $State['highest_usn_changed']) {
        return @{ Usable = $false; Reason = 'The saved state carries no uSNChanged watermark.' }
    }
    return @{ Usable = $true; Reason = $null }
}


Export-ModuleMember -Function @(
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
    # Exported because a provider's SearchCommand is built with GetNewClosure(),
    # which rebinds the scriptblock into a new dynamic module: only exported
    # commands are visible from inside it.
    'Test-AdgFixtureFilterMatch'
    'Split-AdgFilterClause'
    'Select-AdgFixtureAttributes'
    'ConvertFrom-AdgLdapEntry'
    'Get-AdgLdapRootDse'
    'Get-AdgDirectoryIssuer'
    'Add-AdgUsnFilter'
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
