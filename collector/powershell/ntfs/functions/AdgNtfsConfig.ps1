<#
    Configuration layer: which share roots to read.

    Two rules shape this file, and they are the SMB collector's rules applied to paths.

    Targets are explicit. There is no "find every share in the domain and read it" mode, and
    adding one would be a change of posture rather than a convenience: an auditing tool that
    walks an estate it was never pointed at looks exactly like reconnaissance to a SOC, and
    it silently changes what a run's declared scope means. A caller who wants everything
    enumerates it themselves - from ADG's own share inventory, which Phase 2B already
    collected - and hands over the list.

    Phase 3A reads share roots only. A path below a root is refused here rather than read,
    because two of the facts a resource observation carries cannot be established without
    the ancestors: whether the directory's DACL differs from its parent's, and how deep it
    sits in a tree nothing has walked. Guessing either would put a wrong boundary into
    storage, and a wrong boundary is what a later scan uses to decide where to stop looking.
#>

function Test-AdgShareRootPath {
    <#
        .SYNOPSIS
            Is this UNC path a share root rather than a directory inside one?
        .DESCRIPTION
            A name test on the canonical form, so \\FS01\Finance\ and //fs01/finance are
            both roots and \\FS01\Finance\Reports is not.
    #>
    [OutputType([bool])]
    param([Parameter(Mandatory)][string] $Path)

    $canonical = ConvertTo-AdgUncPath $Path
    return @($canonical.Substring(2).Split('\')).Count -eq 2
}

function Import-AdgNtfsTarget {
    <#
        .SYNOPSIS
            Read and validate a collector target configuration.
        .DESCRIPTION
            Accepts a JSON file, a list of UNC paths, or both, and returns a normalized
            settings object. Every field is validated here rather than at the point of use,
            so a malformed configuration fails before the collector reads a single
            descriptor - with a message naming the field, not a null reference deep inside a
            scan.

            The file holds no credentials. Collection runs as the identity of the process (a
            group managed service account in production); a password in a config file would
            be a secret in source control waiting to happen.

        .PARAMETER Path
            Path to a JSON configuration file. See adg-ntfs-targets.example.json.

        .PARAMETER ShareRoot
            Share roots as UNC paths, merged with any the file lists. Supplying at least one
            by either route is mandatory: there is no estate-wide default.
    #>
    [OutputType([pscustomobject])]
    param(
        [string] $Path,
        [string[]] $ShareRoot = @()
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

    $roots = [System.Collections.Generic.List[string]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($candidate in @(Get-Field $document 'shareRoots' @()) + @($ShareRoot)) {
        $text = ([string] $candidate).Trim()
        if ([string]::IsNullOrWhiteSpace($text)) { continue }

        $canonical = try {
            ConvertTo-AdgUncPath $text
        }
        catch {
            throw "Target '$candidate' is not a usable share root: $($_.Exception.Message)"
        }

        if (-not (Test-AdgShareRootPath $canonical)) {
            $parts = $canonical.Substring(2).Split('\')
            $root = '\\{0}\{1}' -f $parts[0], $parts[1]
            throw "Target '$candidate' names a directory inside a share, not a share root. This phase reads share roots only: whether a subdirectory's DACL differs from its parent's cannot be established without reading the ancestors, and storing a guessed boundary would mislead the recursive scan that follows. Configure '$root' instead, or wait for the tree walk."
        }

        if ($canonical.Length -gt 32767) {
            throw "Target '$canonical' is longer than the contract allows for a UNC path."
        }
        if ($seen.Add($canonical.ToLowerInvariant())) { $roots.Add($canonical) }
    }

    if ($roots.Count -eq 0) {
        throw 'No share roots were configured. ADG does not sweep an estate implicitly: list the share roots to audit in the configuration file or pass -ShareRoot, for example \\FS01\Finance.'
    }

    $retries = [int] (Get-Field $document 'retryCount' 1)
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

    return [pscustomobject]@{
        ShareRoots        = $roots.ToArray()
        RetryCount        = $retries
        RetryDelaySeconds = $retryDelay
        BatchSize         = $batchSize
        # Report a principal observation for every trustee SID that did not resolve to a
        # name. On by default: an orphaned SID on a folder ACL is one of the findings this
        # tool exists to produce, and the backend cannot infer it from the ACE alone.
        ReportUnresolved  = [bool] (Get-Field $document 'reportUnresolvedPrincipals' $true)
    }
}
