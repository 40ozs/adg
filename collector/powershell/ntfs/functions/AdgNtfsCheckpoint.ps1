<#
    Checkpoint layer: enough state to resume a walk, and nothing that would let it resume
    into a different scan.

    A tree scan over a real estate runs for hours. It will be interrupted - a reboot, a
    network blip, an operator with a deadline - and the cost of starting again is not just
    the time: a scan restarted from zero every evening never finishes, so the estate is
    never fully read, so nothing can ever be reconciled.

    ------------------------------------------------------------------------------------
    What is saved, and what deliberately is not

    The frontier: the directories the walk had discovered and not yet read, each with the
    parent facts it needs to judge its own boundary. Those facts have to travel with the
    entry. Recomputing them on resume would mean re-reading the parent, and the parent may
    itself be behind an exclusion, past the depth limit, or simply gone by then - at which
    point the resumed run would report parent_unreadable for a directory the original run
    compared correctly, and the two halves of one scan would disagree about the same tree.

    Not the observations. A checkpoint is written only after the caller has flushed
    everything collected so far, so anything not in the frontier has already been submitted.
    Storing it twice would make the checkpoint a second copy of the payload that could
    disagree with what the server holds.

    Not the visited set, unless reparsePointPolicy is 'follow'. Under 'skip' and 'ignore'
    the frontier is a tree frontier and a directory cannot be reached twice, so the set is
    dead weight - and on a large estate it is the only part of a checkpoint that grows
    without bound.

    ------------------------------------------------------------------------------------
    Resuming into a different scan is refused

    The fingerprint covers exactly the settings that change which directories a walk visits:
    the roots, the depth limit, the include and exclude patterns, the reparse policy, and
    whether files are read. Resume with any of those changed and the frontier describes a
    walk that the new settings would never have produced - the run would claim a scope it
    half-enumerated under one rule and half under another. Batch size, concurrency, the
    timeout and the checkpoint interval are all absent from it on purpose: they change how
    long a scan takes and nothing about what it sees.
#>

$script:AdgCheckpointFormat = 'adg-ntfs-checkpoint/1'

function Get-AdgScanFingerprint {
    <#
        .SYNOPSIS
            A digest of the settings that decide which directories a walk visits.
        .DESCRIPTION
            Deliberately narrow. Anything in here makes a checkpoint unresumable, so a
            setting belongs only if resuming with it changed would produce a run that
            enumerated its scope under two different rules.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][pscustomobject] $Settings)

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add($script:AdgCheckpointFormat)
    foreach ($root in @($Settings.ScanRoots)) { $lines.Add("root=$($root.ToLowerInvariant())") }
    $lines.Add("max_depth=$($Settings.MaxDepth)")
    foreach ($pattern in @($Settings.IncludePaths)) { $lines.Add("include=$($pattern.ToLowerInvariant())") }
    foreach ($pattern in @($Settings.ExcludePaths)) { $lines.Add("exclude=$($pattern.ToLowerInvariant())") }
    $lines.Add("reparse=$($Settings.ReparsePointPolicy)")
    $lines.Add("include_files=$(if ($Settings.IncludeFiles) { 'true' } else { 'false' })")

    $text = ''
    foreach ($line in $lines) { $text += "$line`n" }
    return Get-AdgSha256Hex $text
}

function New-AdgNtfsCheckpoint {
    <#
        .SYNOPSIS
            A fresh checkpoint object for one run.
        .DESCRIPTION
            Created before the walk starts rather than at the first save, so the run id in
            it is the run id the observations already carry. A resumed walk keeps that id:
            the two halves are one run, the server's per-(run_id, source_key) observation
            rows line up, and a re-read directory is recognized as a repeat instead of
            counted twice.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][pscustomobject] $Settings
    )

    return [pscustomobject]@{
        Format      = $script:AdgCheckpointFormat
        RunId       = $RunId
        Fingerprint = Get-AdgScanFingerprint -Settings $Settings
        StartedAt   = Get-AdgTimestamp
        UpdatedAt   = Get-AdgTimestamp
        Frontier    = @()
        Visited     = @()
        Completed   = @()
        Metrics     = $null
    }
}

function Save-AdgNtfsCheckpoint {
    <#
        .SYNOPSIS
            Write a checkpoint atomically.
        .DESCRIPTION
            Written to a sibling temporary file and moved into place, because the thing a
            checkpoint most needs to survive is the process dying while it is being written.
            A half-written JSON document is not a slightly stale checkpoint; it is a file
            that fails to parse on resume, at which point the only honest thing left to do
            is start the scan again.

            Returns the path, so a caller can report where the resume point is without
            reaching into the settings for it.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Checkpoint,
        [Parameter(Mandatory)][string] $Path
    )

    $Checkpoint.UpdatedAt = Get-AdgTimestamp
    $temporary = "$Path.tmp"
    $Checkpoint | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
    return $Path
}

function Import-AdgNtfsCheckpoint {
    <#
        .SYNOPSIS
            Read a checkpoint, or refuse it with a reason.
        .DESCRIPTION
            Every refusal here is a case where resuming would produce a run that claims to
            have enumerated a scope it enumerated under two different sets of rules. Each
            says what to do instead, because the alternative - delete the file and start
            again - is the right answer and the operator should not have to guess it.

            Returns $null when the file does not exist, which is the ordinary first run.

        .PARAMETER Settings
            The settings the resumed run will use. Their fingerprint must match the one the
            checkpoint was written with.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $Path,
        [Parameter(Mandatory)][pscustomobject] $Settings
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }

    $document = try {
        Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "Checkpoint '$Path' is not valid JSON: $($_.Exception.Message). It was probably written by a process that died mid-write. Delete it and start the scan again; a resumed walk needs a frontier it can trust."
    }

    $format = [string] (Get-AdgProperty $document 'Format')
    if ($format -ne $script:AdgCheckpointFormat) {
        throw "Checkpoint '$Path' is format '$format', and this collector writes '$($script:AdgCheckpointFormat)'. The frontier's shape is part of the format, so an older one cannot be resumed. Delete it and start the scan again."
    }

    $fingerprint = [string] (Get-AdgProperty $document 'Fingerprint')
    $expected = Get-AdgScanFingerprint -Settings $Settings
    if ($fingerprint -ne $expected) {
        throw "Checkpoint '$Path' was written for a different scan. The roots, depth limit, include/exclude patterns, reparse policy, or file-scanning setting have changed since, and the frontier in it describes directories the current settings would not have visited. Resuming would produce one run that enumerated its scope under two different rules. Delete the checkpoint to start the new scan, or restore the previous settings to finish the old one."
    }

    $runId = [string] (Get-AdgProperty $document 'RunId')
    if ([string]::IsNullOrWhiteSpace($runId)) {
        throw "Checkpoint '$Path' names no run. Without the original run id the resumed half would be a second run over the same tree, and neither half could reconcile the scope."
    }

    $frontier = [System.Collections.Generic.List[object]]::new()
    foreach ($entry in @((Get-AdgProperty $document 'Frontier') ?? @())) {
        $path = [string] (Get-AdgProperty $entry 'Path')
        if ([string]::IsNullOrWhiteSpace($path)) { continue }
        $frontier.Add([pscustomobject]@{
                Path              = ConvertTo-AdgUncPath $path
                Depth             = [int] (Get-AdgProperty $entry 'Depth')
                Root              = [string] (Get-AdgProperty $entry 'Root')
                IsScanRoot        = [bool] (Get-AdgProperty $entry 'IsScanRoot')
                # A tri-state that must survive the round trip: $null is "the parent was
                # never read", which is not the same fact as $false, "the parent has a NULL
                # DACL". ConvertFrom-Json gives back $null for an absent property, so the
                # cast has to be conditional rather than [bool].
                ParentDaclPresent = Get-AdgProperty $entry 'ParentDaclPresent'
                ParentProjection  = [string] (Get-AdgProperty $entry 'ParentProjection')
                ParentAclHash     = [string] (Get-AdgProperty $entry 'ParentAclHash')
                # The reparse targets already crossed on this branch. They have to survive a
                # resume: without them the second half of a walk would follow a junction the
                # first half had recognized as a cycle, and produce an endless chain of new
                # paths describing one directory.
                LinkTargets       = @((Get-AdgProperty $entry 'LinkTargets') ?? @())
                # The junction verdict the first half of the walk already reached, and the
                # two facts behind it. Without these a resumed walk re-decides a reparse
                # point that was already declined - and having lost the decision, it reports
                # the stop as whatever happens to stop it next.
                NoDescend         = [bool] (Get-AdgProperty $entry 'NoDescend')
                IsReparsePoint    = [bool] (Get-AdgProperty $entry 'IsReparsePoint')
                ReparseTarget     = [string] (Get-AdgProperty $entry 'ReparseTarget')
            })
    }

    return [pscustomobject]@{
        Format      = $format
        RunId       = $runId
        Fingerprint = $fingerprint
        StartedAt   = [string] (Get-AdgProperty $document 'StartedAt')
        UpdatedAt   = [string] (Get-AdgProperty $document 'UpdatedAt')
        Frontier    = $frontier.ToArray()
        Visited     = @((Get-AdgProperty $document 'Visited') ?? @())
        Completed   = @((Get-AdgProperty $document 'Completed') ?? @())
        Metrics     = Get-AdgProperty $document 'Metrics'
    }
}

function Remove-AdgNtfsCheckpoint {
    <#
        .SYNOPSIS
            Delete a checkpoint once the walk it describes has finished.
        .DESCRIPTION
            Only on a completed walk. A checkpoint left behind after a finished scan is the
            worse of the two failure modes: the next run resumes an empty frontier, reads
            nothing, and reports success over a tree it never looked at.
    #>
    param([Parameter(Mandatory)][string] $Path)

    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Remove-Item -LiteralPath $Path -Force
    }
    if (Test-Path -LiteralPath "$Path.tmp" -PathType Leaf) {
        Remove-Item -LiteralPath "$Path.tmp" -Force
    }
}
