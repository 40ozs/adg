<#
    Configuration layer: which servers to ask, and which of their shares to record.

    Two rules shape this file.

    Targets are explicit. There is no "scan the domain" mode, and adding one would be a
    change of posture, not a convenience: an auditing tool that sprays connection attempts
    across every computer object looks exactly like reconnaissance to a SOC, and it
    silently changes what a run's declared scope means. A caller who wants the whole
    estate enumerates it themselves and hands over the list.

    Filtering is a decision, not an accident. Every share the server reported is passed
    through Test-AdgShareIncluded, which returns both the verdict and the reason for it,
    so a scan can say how many shares it chose not to read rather than quietly appearing
    to have found fewer.
#>

# Administrative shares the SMB server creates itself. Their ACL is a Windows constant
# (Administrators, full) and auditing it says nothing about how an organization shares
# data, so they are excluded unless a caller deliberately asks for them.
$script:AdgDefaultAdminShares = @('ADMIN$', 'IPC$', 'PRINT$', 'FAX$', 'SYSVOL', 'NETLOGON')

function Test-AdgAdminShareName {
    <#
        .SYNOPSIS
            Is this the name of a share Windows creates rather than an administrator?
        .DESCRIPTION
            Covers the named system shares and the per-volume drive shares (C$, D$ ...).
            It is a name test only; the authoritative signal is the SMB server's own
            Special flag, which Test-AdgShareIncluded prefers when it is available. The
            name test exists because a caller may filter a list of share names that never
            came from Get-SmbShare.
    #>
    [OutputType([bool])]
    param([Parameter(Mandatory)][AllowEmptyString()][string] $ShareName)

    if ($script:AdgDefaultAdminShares -contains $ShareName) { return $true }
    return $ShareName -match '^[A-Za-z]\$$'
}

function Test-AdgShareIncluded {
    <#
        .SYNOPSIS
            Decide whether one share should be collected, and say why.
        .DESCRIPTION
            Returns a hashtable with Included and Reason. The reason is kept because an
            audit that cannot explain a gap in its own coverage is not an audit.

            Precedence, highest first:

              1. an explicit Exclude pattern always wins - a deny list a caller wrote is
                 never overridden by a default;
              2. an explicit Include pattern, when any are configured;
              3. administrative shares, excluded unless -IncludeAdminShares;
              4. ordinary hidden shares (Data$), excluded unless -IncludeHiddenShares;
              5. non-disk shares (print, IPC, device), excluded unless -IncludeNonDiskShares,
                 because they publish no directory and so carry no NTFS layer to compare
                 against;
              6. otherwise included.

            Patterns are wildcard expressions matched case-insensitively against the share
            name, never regular expressions: an operator writing 'Temp*' in a config file
            should not have to reason about anchoring.
        .PARAMETER IsSpecial
            The SMB server's own Special flag. Pass it whenever it is known; it is the
            only way to tell an administrative share from an ordinary hidden one.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string] $ShareName,
        [string] $ShareType = 'disk',
        [Nullable[bool]] $IsSpecial,
        [string[]] $Include = @(),
        [string[]] $Exclude = @(),
        [switch] $IncludeAdminShares,
        [switch] $IncludeHiddenShares,
        [switch] $IncludeNonDiskShares
    )

    foreach ($pattern in ($Exclude ?? @())) {
        if ([string]::IsNullOrWhiteSpace($pattern)) { continue }
        if ($ShareName -like $pattern) {
            return @{ Included = $false; Reason = "excluded by pattern '$pattern'" }
        }
    }

    $activeIncludes = @(($Include ?? @()) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($activeIncludes.Count -gt 0) {
        $matched = $null
        foreach ($pattern in $activeIncludes) {
            if ($ShareName -like $pattern) { $matched = $pattern; break }
        }
        if ($null -eq $matched) {
            return @{ Included = $false; Reason = 'no include pattern matched' }
        }
        return @{ Included = $true; Reason = "included by pattern '$matched'" }
    }

    # Prefer what the server said over what the name suggests: an administrator can create
    # an ordinary hidden share called Data$, and Windows does not mark it Special.
    $isAdmin = if ($null -ne $IsSpecial) { [bool] $IsSpecial } else { Test-AdgAdminShareName $ShareName }
    if ($isAdmin -and -not $IncludeAdminShares) {
        return @{ Included = $false; Reason = 'administrative or system share' }
    }

    if ((Test-AdgHiddenShareName $ShareName) -and -not $isAdmin -and -not $IncludeHiddenShares) {
        return @{ Included = $false; Reason = 'hidden share' }
    }

    if ($ShareType -ne 'disk' -and -not $IncludeNonDiskShares) {
        return @{ Included = $false; Reason = "share type '$ShareType' publishes no directory" }
    }

    return @{ Included = $true; Reason = 'included' }
}

function Import-AdgSmbTarget {
    <#
        .SYNOPSIS
            Read and validate a collector target configuration.
        .DESCRIPTION
            Accepts a JSON file, a server list, or both, and returns a normalized settings
            object. Every field is validated here rather than at the point of use, so a
            malformed configuration fails before the collector opens a single session -
            with a message naming the field, not a null-reference deep inside a scan.

            The file holds no credentials. Collection runs as the identity of the process
            (a group managed service account in production); a password in a config file
            would be a secret in source control waiting to happen.

        .PARAMETER Path
            Path to a JSON configuration file. See adg-smb-targets.example.json.

        .PARAMETER Server
            Server names, merged with any the file lists. Supplying at least one server
            by either route is mandatory: there is no domain-wide default.
    #>
    [OutputType([pscustomobject])]
    param(
        [string] $Path,
        [string[]] $Server = @()
    )

    $document = [pscustomobject]@{}
    if (-not [string]::IsNullOrWhiteSpace($Path)) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
            throw "Target configuration '$Path' does not exist."
        }
        $raw = Get-Content -LiteralPath $Path -Raw -Encoding utf8
        try {
            $document = $raw | ConvertFrom-Json -ErrorAction Stop
        }
        catch {
            throw "Target configuration '$Path' is not valid JSON: $($_.Exception.Message)"
        }
        if ($null -eq $document) {
            throw "Target configuration '$Path' is empty."
        }
    }

    function Get-Field {
        # Indexed lookup rather than a -contains over .Properties.Name: strict mode makes
        # member enumeration over an empty property collection an error, which an empty
        # configuration object produces every time.
        param($Document, [string] $Name, $Default)
        if ($null -eq $Document) { return $Default }
        $property = $Document.PSObject.Properties[$Name]
        if ($null -eq $property -or $null -eq $property.Value) { return $Default }
        return $property.Value
    }

    $servers = [System.Collections.Generic.List[string]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($name in @(Get-Field $document 'servers' @()) + @($Server)) {
        $trimmed = ([string] $name).Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed)) { continue }
        # A host name, not a UNC path and not a path fragment: the contract's hostName
        # pattern rejects both, and accepting '\\FS01' here would fail much later.
        if ($trimmed -match '[\\/]') {
            throw "Target '$name' is not a host name. Configure servers as names such as 'FS01', not UNC paths."
        }
        if ($trimmed.Length -gt 255) {
            throw "Target '$trimmed' is longer than the 255 characters the contract allows for a host name."
        }
        if ($seen.Add($trimmed)) { $servers.Add($trimmed) }
    }

    if ($servers.Count -eq 0) {
        throw 'No target servers were configured. ADG does not scan a domain implicitly: list the file servers to audit in the configuration file or pass -Server.'
    }

    $protocol = [string] (Get-Field $document 'protocol' 'Wsman')
    if ($protocol -notin @('Wsman', 'Dcom')) {
        throw "protocol must be 'Wsman' or 'Dcom', not '$protocol'."
    }

    $timeout = [int] (Get-Field $document 'timeoutSeconds' 30)
    if ($timeout -lt 1 -or $timeout -gt 3600) {
        throw "timeoutSeconds must be between 1 and 3600, not $timeout."
    }

    $retries = [int] (Get-Field $document 'retryCount' 2)
    if ($retries -lt 0 -or $retries -gt 10) {
        throw "retryCount must be between 0 and 10, not $retries."
    }

    $retryDelay = [int] (Get-Field $document 'retryDelaySeconds' 2)
    if ($retryDelay -lt 0 -or $retryDelay -gt 300) {
        throw "retryDelaySeconds must be between 0 and 300, not $retryDelay."
    }

    $batchSize = [int] (Get-Field $document 'batchSize' 500)
    if ($batchSize -lt 1 -or $batchSize -gt 1000) {
        throw "batchSize must be between 1 and 1000 (the contract's per-batch maximum), not $batchSize."
    }

    $aclMethod = [string] (Get-Field $document 'aclMethod' 'Auto')
    if ($aclMethod -notin @('Auto', 'Descriptor', 'Access')) {
        throw "aclMethod must be 'Auto', 'Descriptor', or 'Access', not '$aclMethod'."
    }

    return [pscustomobject]@{
        Servers              = $servers.ToArray()
        Protocol             = $protocol
        TimeoutSeconds       = $timeout
        RetryCount           = $retries
        RetryDelaySeconds    = $retryDelay
        BatchSize            = $batchSize
        AclMethod            = $aclMethod
        Include              = @(Get-Field $document 'includeShares' @())
        Exclude              = @(Get-Field $document 'excludeShares' @())
        IncludeAdminShares   = [bool] (Get-Field $document 'includeAdminShares' $false)
        IncludeHiddenShares  = [bool] (Get-Field $document 'includeHiddenShares' $false)
        IncludeNonDiskShares = [bool] (Get-Field $document 'includeNonDiskShares' $false)
        # Record that an excluded share exists, without reading its ACL. On by default so
        # that a run can still claim to have enumerated every share on the server; see the
        # scope discussion at the top of AdgSmbScan.ps1 for what turning it off costs.
        RecordExcludedShares = [bool] (Get-Field $document 'recordExcludedShares' $true)
    }
}
