<#
    Acquisition layer: the only functions in this collector that touch a remote host.

    Every one of them is a thin wrapper over a single Windows API call, with no
    normalization and no policy. That is the point: this file is the seam the Pester
    suite mocks, so the orchestration and normalization layers can be exercised against
    an entire imaginary file-server estate without a domain, a share, or a network.

    Keep it thin. Logic that creeps in here becomes logic that is never tested.

    ADG is read-only (ADR-0004). Nothing here writes to a target, takes ownership, or
    changes a security descriptor to make a read succeed.
#>

function New-AdgSmbSession {
    <#
        .SYNOPSIS
            Open a CIM session to a target server, with a bounded timeout.
        .DESCRIPTION
            The timeout is the mechanism that stops one dead server from holding up an
            estate-wide scan: WSMan's own default is long enough that a handful of
            unreachable hosts can stall a run for many minutes.

            WSMan (WinRM, 5985/5986) is the default protocol. DCOM is offered for hosts
            where WinRM is not enabled; it needs RPC 135 plus the dynamic port range, so
            it is usually the harder option to allow through a firewall, not the easier.
    #>
    [OutputType([Microsoft.Management.Infrastructure.CimSession])]
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [ValidateSet('Wsman', 'Dcom')][string] $Protocol = 'Wsman',
        [ValidateRange(1, 3600)][int] $TimeoutSeconds = 30,
        [AllowNull()][pscredential] $Credential
    )

    $sessionOption = New-CimSessionOption -Protocol $Protocol
    $parameters = @{
        ComputerName        = $ComputerName
        SessionOption       = $sessionOption
        OperationTimeoutSec = $TimeoutSeconds
        ErrorAction         = 'Stop'
    }
    if ($Credential) { $parameters['Credential'] = $Credential }

    return New-CimSession @parameters
}

function Remove-AdgSmbSession {
    <#
        .SYNOPSIS
            Close a CIM session, never failing the scan because teardown failed.
    #>
    param([AllowNull()] $Session)

    if ($null -eq $Session) { return }
    try {
        Remove-CimSession -CimSession $Session -ErrorAction Stop
    }
    catch {
        Write-Verbose "Closing the session to $($Session.ComputerName) failed: $($_.Exception.Message)"
    }
}

function Get-AdgRemoteComputerFact {
    <#
        .SYNOPSIS
            Identity facts about the host itself: DNS name, NetBIOS name, domain, OS.
        .DESCRIPTION
            Deliberately does not attempt to read the machine SID. A computer's SID is an
            Active Directory fact that the Phase 1 collector reads from the directory;
            inferring one from a remote host would key a machine by something other than
            its own authoritative identity.
    #>
    [OutputType([hashtable])]
    param([Parameter(Mandatory)] $Session)

    $computer = Get-CimInstance -CimSession $Session -ClassName Win32_ComputerSystem -ErrorAction Stop

    $operatingSystem = $null
    try {
        $os = Get-CimInstance -CimSession $Session -ClassName Win32_OperatingSystem -ErrorAction Stop
        $operatingSystem = [string] $os.Caption
    }
    catch {
        # The OS caption is descriptive metadata; losing it must not fail a share scan.
        Write-Verbose "Win32_OperatingSystem was unreadable on $($Session.ComputerName): $($_.Exception.Message)"
    }

    $dnsHostName = $null
    if (-not [string]::IsNullOrWhiteSpace($computer.DNSHostName)) {
        $dnsHostName = [string] $computer.DNSHostName
        if (-not [string]::IsNullOrWhiteSpace($computer.Domain) -and [bool] $computer.PartOfDomain) {
            $dnsHostName = "$($computer.DNSHostName).$($computer.Domain)"
        }
    }

    return @{
        DnsHostName    = $dnsHostName
        NetbiosName    = [string] $computer.Name
        IsDomainMember = [bool] $computer.PartOfDomain
        OperatingSystem = $operatingSystem
    }
}

function Get-AdgRemoteShare {
    <#
        .SYNOPSIS
            Every share the server publishes, administrative shares included.
        .DESCRIPTION
            Filtering happens later, in Test-AdgShareIncluded. Enumerating everything and
            then deciding keeps the include/exclude policy in one testable place, and
            means the scan can report how many shares it chose not to look at rather than
            never knowing they existed.
    #>
    [OutputType([object[]])]
    param([Parameter(Mandatory)] $Session)

    # -IncludeHidden is required to see ordinary hidden shares (Data$). Without it the
    # SMB provider omits them, and a share the collector never saw is a share nobody
    # audits - the worst kind of gap, because the report still looks complete.
    return @(Get-SmbShare -CimSession $Session -IncludeHidden -ErrorAction Stop)
}

function Get-AdgRemoteShareSecurity {
    <#
        .SYNOPSIS
            The raw share security descriptor: SIDs and access masks.
        .DESCRIPTION
            The preferred way to read a share ACL. Win32_LogicalShareSecuritySetting
            returns the descriptor as it is stored, so every trustee arrives as a SID
            whether or not it resolves to a name - which is exactly what the contract
            wants, and what the level-based API cannot promise.

            Measured caveat: the class has no instance for an administrative share. C$,
            ADMIN$, and IPC$ carry a default descriptor rather than a stored one, so this
            throws for them and the caller falls back to the level-based reading. Those
            shares are excluded from collection by default anyway.

            Returns a hashtable with Dacl and DaclPresent. DaclPresent is read from the
            descriptor's control flags, because a NULL DACL (everybody has full share
            access) and an empty DACL (nobody does) both arrive here as an empty array and
            mean opposite things.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)] $Session,
        [Parameter(Mandatory)][string] $ShareName
    )

    $setting = Get-CimInstance -CimSession $Session -ClassName 'Win32_LogicalShareSecuritySetting' `
        -Filter "Name='$($ShareName.Replace("'", "''"))'" -ErrorAction Stop

    if ($null -eq $setting) {
        throw "Win32_LogicalShareSecuritySetting has no instance for share '$ShareName'."
    }

    $result = Invoke-CimMethod -InputObject $setting -MethodName 'GetSecurityDescriptor' -ErrorAction Stop
    if ([int] $result.ReturnValue -ne 0) {
        throw "GetSecurityDescriptor failed for share '$ShareName' with return value $($result.ReturnValue)."
    }

    $descriptor = $result.Descriptor
    # SE_DACL_PRESENT. Its absence is a NULL DACL, which grants everyone full access.
    $daclPresent = ([int] $descriptor.ControlFlags -band 0x0004) -ne 0

    return @{
        Dacl        = @($descriptor.DACL)
        DaclPresent = $daclPresent
    }
}

function Get-AdgRemoteShareAccess {
    <#
        .SYNOPSIS
            The share ACL as the three permission levels, via the SMB provider.
        .DESCRIPTION
            The fallback reading, used when the security descriptor cannot be read. It
            reports account names rather than SIDs and collapses any non-standard mask to
            'Custom', so it loses information the descriptor path keeps. The caller
            records which method it used.
    #>
    [OutputType([object[]])]
    param(
        [Parameter(Mandatory)] $Session,
        [Parameter(Mandatory)][string] $ShareName
    )

    return @(Get-SmbShareAccess -CimSession $Session -Name $ShareName -ErrorAction Stop)
}

function Resolve-AdgTrusteeSid {
    <#
        .SYNOPSIS
            Translate an account name to its string-form SID, or return $null.
        .DESCRIPTION
            Returns $null rather than throwing when the name does not resolve: an
            unresolvable trustee is an ordinary observation about a real environment
            (a deleted account, a broken trust), not an exceptional condition.

            A value that is already a SID is returned unchanged, because that is what
            Windows reports as the account name when it could not resolve one.
    #>
    [OutputType([string])]
    param([AllowNull()][AllowEmptyString()][string] $Trustee)

    if ([string]::IsNullOrWhiteSpace($Trustee)) { return $null }
    if (Test-AdgSidString $Trustee) { return $Trustee }

    try {
        $account = [System.Security.Principal.NTAccount]::new($Trustee)
        return $account.Translate([System.Security.Principal.SecurityIdentifier]).Value
    }
    catch {
        Write-Verbose "'$Trustee' did not translate to a SID: $($_.Exception.Message)"
        return $null
    }
}
