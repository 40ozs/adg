<#
    Configuration layer: where the walk starts, how far it goes, and what it refuses.

    Two rules shape this file, and they are the SMB collector's rules applied to paths.

    Targets are explicit. There is no "find every share in the domain and walk it" mode, and
    adding one would be a change of posture rather than a convenience: an auditing tool that
    walks an estate it was never pointed at looks exactly like reconnaissance to a SOC, and
    it silently changes what a run's declared scope means. A caller who wants everything
    enumerates it themselves - from ADG's own share inventory, which Phase 2B already
    collected - and hands over the list.

    Every limit is bounded, and every bound is refused loudly rather than clamped. A
    maxDepth of 100000 is a typo, not a request, and quietly reducing it to something
    workable would produce a scan whose declared reach and actual reach differ - which is
    the one thing a scope must never do.

    ------------------------------------------------------------------------------------
    A scan root is no longer a share root

    Phase 3A refused any path below \\server\share, because two of the facts a resource
    observation carries could not be established without the ancestors. The tree walk reads
    the ancestors, so the restriction is lifted: a scan may start anywhere inside a share.

    What a deeper start costs is stated rather than hidden. The starting directory's parent
    is not read, so its boundary is reported as `scan_root` - unknown, and therefore a
    boundary - rather than compared against anything. And a run rooted below a share root
    cannot reconcile that share's directory_tree scope, because it did not enumerate it.
#>

function Test-AdgShareRootPath {
    <#
        .SYNOPSIS
            Is this UNC path a share root rather than a directory inside one?
        .DESCRIPTION
            A name test on the canonical form, so \\FS01\Finance\ and //fs01/finance are
            both roots and \\FS01\Finance\Reports is not. Still load-bearing: a share root
            is an ACL boundary by fiat, because its parent lies outside the share and there
            is nothing to compare it against.
    #>
    [OutputType([bool])]
    param([Parameter(Mandatory)][string] $Path)

    $canonical = ConvertTo-AdgUncPath $Path
    return @($canonical.Substring(2).Split('\')).Count -eq 2
}

function Test-AdgPathMatch {
    <#
        .SYNOPSIS
            Does a path match one include/exclude pattern, subtree included?
        .DESCRIPTION
            A pattern matches the directory it names AND everything beneath it, which is
            what an operator writing \\FS01\Finance\Archive means. Wildcards are PowerShell's
            -like wildcards, so \\FS01\*\Archive is a pattern over every share on FS01.

            Case-insensitive, because the file systems these paths name are, and applied to
            the canonical spelling, so a pattern written with forward slashes or a trailing
            separator behaves the same as one written without.
    #>
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][string] $Path,
        [Parameter(Mandatory)][AllowEmptyString()][string] $Pattern
    )

    $pattern = $Pattern.Trim().Replace('/', '\').TrimEnd('\')
    if ([string]::IsNullOrWhiteSpace($pattern)) { return $false }
    return ($Path -like $pattern) -or ($Path -like "$pattern\*")
}

function Test-AdgPatternReachesBelow {
    <#
        .SYNOPSIS
            Could an include pattern still match something beneath this directory?
        .DESCRIPTION
            The difference between a walk that honours includePaths and one that stops at
            the first directory outside it. Given the pattern \\FS01\*\HR and the directory
            \\FS01\Finance, nothing at \\FS01\Finance matches - but \\FS01\Finance\HR does,
            and a walk that refused to descend would never find it.

            The test is the pattern truncated to the directory's own depth. That is exact
            for -like patterns, which have no segment-spanning wildcard: a pattern can only
            reach below a directory if its first N components describe that directory.
    #>
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][string] $Path,
        [Parameter(Mandatory)][AllowEmptyString()][string] $Pattern
    )

    $pattern = $Pattern.Trim().Replace('/', '\').TrimEnd('\')
    if ([string]::IsNullOrWhiteSpace($pattern) -or -not $pattern.StartsWith('\\')) { return $false }

    $patternParts = @($pattern.Substring(2).Split('\') | Where-Object { $_ -ne '' })
    $pathParts = @($Path.Substring(2).Split('\') | Where-Object { $_ -ne '' })
    if ($patternParts.Count -le $pathParts.Count) { return $false }

    $prefix = '\\' + (($patternParts[0..($pathParts.Count - 1)]) -join '\')
    return $Path -like $prefix
}

function Test-AdgPathInScope {
    <#
        .SYNOPSIS
            Whether a directory is read, and whether the walk descends past it.
        .DESCRIPTION
            Two separate answers, because they are two separate questions. A directory
            outside includePaths is not reported, but the walk still has to pass through it
            to reach the directories that are - while a directory matching excludePaths
            stops both, because an operator excluding a subtree means the subtree.

            Exclusion wins over inclusion. A path named by both is excluded: the narrower
            instruction is the one that was meant, and the alternative is a rule whose
            outcome depends on the order two lists happen to be written in.

        .OUTPUTS
            A hashtable with Read (report this directory), Descend (look below it), and
            Excluded (which of the two reasons a directory is not read).
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][string] $Path,
        [AllowNull()][string[]] $Include,
        [AllowNull()][string[]] $Exclude
    )

    $canonical = ConvertTo-AdgUncPath $Path

    foreach ($pattern in @($Exclude ?? @())) {
        if (Test-AdgPathMatch -Path $canonical -Pattern $pattern) {
            return @{ Read = $false; Descend = $false; Excluded = $true }
        }
    }

    $include = @($Include ?? @())
    if ($include.Count -eq 0) {
        return @{ Read = $true; Descend = $true; Excluded = $false }
    }

    $read = $false
    $descend = $false
    foreach ($pattern in $include) {
        if (Test-AdgPathMatch -Path $canonical -Pattern $pattern) { $read = $true; $descend = $true }
        elseif (Test-AdgPatternReachesBelow -Path $canonical -Pattern $pattern) { $descend = $true }
    }
    return @{ Read = $read; Descend = $descend; Excluded = $false }
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

        .PARAMETER ScanRoot
            Directories to walk, as UNC paths, merged with any the file lists. Supplying at
            least one by either route is mandatory: there is no estate-wide default.

        .PARAMETER ShareRoot
            The Phase 3A name for -ScanRoot, kept so an existing command line and an
            existing configuration file both keep working. A scan root need no longer be a
            share root, which is why the new name says directory rather than share.
    #>
    [OutputType([pscustomobject])]
    param(
        [string] $Path,
        [string[]] $ScanRoot = @(),
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

    function Get-Bounded {
        # One message shape for every numeric limit, and a refusal rather than a clamp: a
        # value outside the range is a mistake, and silently correcting it produces a scan
        # whose declared reach and actual reach differ.
        param($Document, [string] $Name, [int] $Default, [int] $Minimum, [int] $Maximum)
        $value = [int] (Get-Field $Document $Name $Default)
        if ($value -lt $Minimum -or $value -gt $Maximum) {
            throw "$Name must be between $Minimum and $Maximum, not $value."
        }
        return $value
    }

    function Get-PatternList {
        param($Document, [string] $Name)
        $patterns = [System.Collections.Generic.List[string]]::new()
        foreach ($candidate in @(Get-Field $Document $Name @())) {
            $text = ([string] $candidate).Trim().Replace('/', '\').TrimEnd('\')
            if ([string]::IsNullOrWhiteSpace($text)) { continue }
            if (-not $text.StartsWith('\\')) {
                throw "$Name entry '$candidate' is not a UNC path pattern. Patterns are matched against canonical UNC paths, so they must begin with \\ - for example \\FS01\Finance\Archive, or \\FS01\*\Archive for every share on a server."
            }
            $patterns.Add($text)
        }
        return , $patterns.ToArray()
    }

    $roots = [System.Collections.Generic.List[string]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $configured = @(Get-Field $document 'scanRoots' @()) + @(Get-Field $document 'shareRoots' @()) +
    @($ScanRoot) + @($ShareRoot)

    foreach ($candidate in $configured) {
        $text = ([string] $candidate).Trim()
        if ([string]::IsNullOrWhiteSpace($text)) { continue }

        $canonical = try {
            ConvertTo-AdgUncPath $text
        }
        catch {
            throw "Target '$candidate' is not a usable scan root: $($_.Exception.Message)"
        }

        if ($canonical.Length -gt 32767) {
            throw "Target '$canonical' is longer than the contract allows for a UNC path."
        }
        if ($seen.Add($canonical.ToLowerInvariant())) { $roots.Add($canonical) }
    }

    if ($roots.Count -eq 0) {
        throw 'No scan roots were configured. ADG does not sweep an estate implicitly: list the directories to audit in the configuration file or pass -ScanRoot, for example \\FS01\Finance.'
    }

    # A root inside another root would walk the same subtree twice and report every
    # directory in it as a compared directory in one run and a scan_root boundary in the
    # other. Refused rather than deduplicated: the operator meant one of the two, and only
    # they know which.
    foreach ($outer in $roots) {
        foreach ($inner in $roots) {
            if ($outer -eq $inner) { continue }
            if ($inner.ToLowerInvariant().StartsWith("$($outer.ToLowerInvariant())\")) {
                throw "Scan root '$inner' sits inside scan root '$outer'. Walking both would read every directory beneath the inner one twice, and report it as a scan_root boundary in one run and a properly compared directory in the other. List one of them."
            }
        }
    }

    $reparse = ([string] (Get-Field $document 'reparsePointPolicy' 'skip')).Trim().ToLowerInvariant()
    if ($reparse -notin @('skip', 'ignore', 'follow')) {
        throw "reparsePointPolicy must be 'skip', 'ignore', or 'follow', not '$reparse'. 'skip' reads the junction's own descriptor and does not descend; 'ignore' does not read it at all; 'follow' descends, and the walk's visited set is what stops a loop."
    }

    $checkpointPath = [string] (Get-Field $document 'checkpointPath' '')
    if (-not [string]::IsNullOrWhiteSpace($checkpointPath)) {
        $parent = Split-Path -Parent $checkpointPath
        if (-not [string]::IsNullOrWhiteSpace($parent) -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
            throw "checkpointPath '$checkpointPath' is in a directory that does not exist. A checkpoint that cannot be written is discovered when the scan is interrupted, which is the worst possible moment to find out."
        }
    }
    else {
        $checkpointPath = $null
    }

    return [pscustomobject]@{
        ScanRoots                 = $roots.ToArray()
        RetryCount                = Get-Bounded $document 'retryCount' 1 0 10
        RetryDelaySeconds         = Get-Bounded $document 'retryDelaySeconds' 2 0 300
        BatchSize                 = Get-Bounded $document 'batchSize' 500 1 1000

        # 0 reads the scan roots and nothing below them, which is exactly the Phase 3A
        # behaviour and the cheapest useful scan. The ceiling is well past any real tree;
        # it exists so a typo cannot become a walk that never ends.
        MaxDepth                  = Get-Bounded $document 'maxDepth' 64 0 512

        IncludePaths              = Get-PatternList $document 'includePaths'
        ExcludePaths              = Get-PatternList $document 'excludePaths'
        ReparsePointPolicy        = $reparse

        # One descriptor read at a time by default. Parallel reads are a throughput win
        # against a remote file server, where the cost is latency rather than CPU, and they
        # change nothing about what is reported - see Invoke-AdgParallelMap for why the walk
        # itself stays sequential.
        ConcurrencyLimit          = Get-Bounded $document 'concurrencyLimit' 1 1 32

        # 0 means no deadline. A scan that runs out of time stops where it is, writes a
        # checkpoint, and reports 'canceled' - it never reports the part it reached as the
        # whole, because a truncated scan that looks complete is how permissions go missing.
        TimeoutSeconds            = Get-Bounded $document 'timeoutSeconds' 0 0 86400

        IncludeFiles              = [bool] (Get-Field $document 'includeFiles' $false)

        CheckpointPath            = $checkpointPath
        CheckpointIntervalSeconds = Get-Bounded $document 'checkpointIntervalSeconds' 30 1 3600

        # Report a principal observation for every trustee SID that did not resolve to a
        # name. On by default: an orphaned SID on a folder ACL is one of the findings this
        # tool exists to produce, and the backend cannot infer it from the ACE alone.
        ReportUnresolved          = [bool] (Get-Field $document 'reportUnresolvedPrincipals' $true)
    }
}
