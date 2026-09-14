<#
    Orchestration layer: turn a walk into a contract-shaped run, one batch at a time.

    ------------------------------------------------------------------------------------
    Streaming, because a tree does not fit in memory

    Phase 3A read every share root, then built every payload, then returned the lot. That is
    comfortable at root granularity and impossible for a tree: an estate with a million
    directories produces several million observations, and holding them all to decide how to
    chunk them would exhaust the collector before it exhausted the file server.

    So the walk hands each resource's observations to a batch writer as they are read, the
    writer emits a batch as soon as one is full, and the caller sends or writes it
    immediately. Nothing accumulates except the batch being filled and the walk's frontier.

    ------------------------------------------------------------------------------------
    A batch never splits a resource

    The one invariant that survived the rewrite unchanged, and the one most easily lost in
    it. The backend verifies a reported acl_hash against the ntfs_ace observations that
    arrive with it and *skips* the check when the batch holds fewer than the declared
    ace_count - so a split silently disables the only check that catches ACEs lost in
    transit. A batch may therefore run over its requested size, but never past the
    contract's ceiling of 1000: a DACL that large is refused loudly, because truncating it
    would look exactly like a shorter ACL.

    ------------------------------------------------------------------------------------
    Scope: intent at the start, achievement at the end

    `incremental` has to be declared before the walk begins - the server refuses to let it
    change mid-run, because whether a run may reconcile depends on it. So it states
    *intent*: false means this run set out to enumerate its scopes completely, which it can
    only claim when nothing in the configuration already says otherwise.

    `reconciled_scopes` states *achievement*, and is decided from what actually happened. A
    root is reconciled only if the walk read it and skipped nothing beneath it for any
    reason at all: not a depth limit, not an exclusion, not an unfollowed junction, not a
    denied descriptor, not a timeout. Anything less and the root is left out - because
    reconciling a scope is what lets the backend mark objects absent, and a scan that
    stopped early and claimed completeness would report live permissions as revoked.

    This is the first time an ADG file-system run can reconcile anything. Phase 3A marked
    every run incremental by construction, because a share-root read enumerates no tree.
#>

function New-AdgNtfsBatchWriter {
    <#
        .SYNOPSIS
            A batch accumulator that emits a contract batch as soon as one is full.
        .DESCRIPTION
            State on an object rather than in a closure. A closure over the pending list
            would be the obvious shape, but a script block created with GetNewClosure cannot
            see this module's other functions, and one that captured the sequence counter by
            value would hand every batch the number 1.

            The last batch is held back rather than emitted eagerly, so that
            Complete-AdgNtfsBatchWriter can mark it is_final. The flag is advisory - only the
            completion envelope ends a run - but a collector that never sets it makes every
            run look truncated to anyone reading the batches.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [ValidateRange(1, 1000)][int] $BatchSize = 500,
        [Parameter(Mandatory)][scriptblock] $OnBatch
    )

    return [pscustomobject]@{
        RunId            = $RunId
        BatchSize        = $BatchSize
        OnBatch          = $OnBatch
        Pending          = [System.Collections.Generic.List[object]]::new()
        Sequence         = 0
        BatchCount       = 0
        ObservationCount = 0
    }
}

function Add-AdgNtfsObservationGroup {
    <#
        .SYNOPSIS
            Add one resource's observations, emitting a batch if the group will not fit.
        .DESCRIPTION
            The group is indivisible. When it does not fit in what is pending, the pending
            batch goes first and the group starts the next one - which may take that batch
            over the requested size, and that is the trade: a batch slightly larger than
            asked for costs nothing, while a DACL split across two batches costs the server
            its ability to notice entries that never arrived.
    #>
    param(
        [Parameter(Mandatory)][pscustomobject] $Writer,
        [AllowNull()][object[]] $Group
    )

    $observations = @($Group ?? @())
    if ($observations.Count -eq 0) { return }
    if ($observations.Count -gt 1000) {
        throw "One resource produced $($observations.Count) observations, which exceeds the contract's ceiling of 1000 per batch. Splitting them would disable the server's acl_hash check, and truncating them would look like a shorter ACL. Report this directory: a DACL that large is itself a finding."
    }

    if ($Writer.Pending.Count -gt 0 -and ($Writer.Pending.Count + $observations.Count) -gt $Writer.BatchSize) {
        Send-AdgNtfsPendingBatch -Writer $Writer
    }
    foreach ($observation in $observations) { $Writer.Pending.Add($observation) }
}

function Send-AdgNtfsPendingBatch {
    <#
        .SYNOPSIS
            Emit whatever is pending as one batch.
        .DESCRIPTION
            The batch id is generated here and never regenerated. A retry after a timeout
            re-sends the same id, which is what makes it a recognized duplicate on the
            server instead of a second application of the same work.
    #>
    param(
        [Parameter(Mandatory)][pscustomobject] $Writer,
        [switch] $Final
    )

    if ($Writer.Pending.Count -eq 0) { return }

    $Writer.Sequence++
    $batch = [ordered]@{
        schema_version = $script:AdgSchemaVersion
        run_id         = $Writer.RunId
        batch_id       = [guid]::NewGuid().ToString()
        sequence       = $Writer.Sequence
        is_final       = [bool] $Final
        observations   = @($Writer.Pending.ToArray())
    }

    $Writer.ObservationCount += $Writer.Pending.Count
    $Writer.BatchCount++
    $Writer.Pending.Clear()

    & $Writer.OnBatch $batch
}

function Complete-AdgNtfsBatchWriter {
    <#
        .SYNOPSIS
            Emit the final batch, marked is_final.
    #>
    param([Parameter(Mandatory)][pscustomobject] $Writer)
    Send-AdgNtfsPendingBatch -Writer $Writer -Final
}

function Test-AdgNtfsFullEnumerationIntent {
    <#
        .SYNOPSIS
            Does this configuration set out to enumerate its scopes completely?
        .DESCRIPTION
            The value of `incremental`, inverted, and it has to be answered before a single
            directory is read because the server refuses to let the flag change mid-run.

            Every test here is a setting that says, in advance, "this run will not look at
            all of it". maxDepth is deliberately absent: a depth limit is an upper bound a
            shallow tree never reaches, and the walk finds out during the scan whether it
            was actually hit - at which point the root simply is not reconciled. Declaring
            intent and reporting achievement are two different statements, and conflating
            them would either forbid reconciliation on every configured scan or claim it on
            a truncated one.
    #>
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Settings,
        [switch] $Resuming
    )

    if (@($Settings.IncludePaths).Count -gt 0) { return $false }
    if (@($Settings.ExcludePaths).Count -gt 0) { return $false }
    if ($Settings.TimeoutSeconds -gt 0) { return $false }
    # A resumed run walked half its tree in a previous process. No single pass enumerated
    # the scope end to end, and the scope is what reconciliation acts on.
    if ($Resuming) { return $false }
    return $true
}

function Invoke-AdgNtfsScanRun {
    <#
        .SYNOPSIS
            Walk one set of scan roots and stream the run to the caller's sink.
        .DESCRIPTION
            Emits the start envelope, then every batch as it fills, then the completion
            envelope - in that order and never out of it, because a batch sent after the
            completion is refused by the server and its observations are simply lost.

            The run's status is decided by what happened rather than by what the caller
            hoped for: nothing read at all is `failed`, because coverage is then unknown
            rather than merely incomplete; a deadline reached is `canceled`; any recorded
            error is `partial`; and only a clean, complete walk is `succeeded`.

        .PARAMETER OnBatch
            Invoked with each batch. It must not return until the batch has been sent or
            written, because the checkpoint that may be written straight after it assumes so.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Settings,
        [Parameter(Mandatory)][string[]] $ScanRoot,
        [Parameter(Mandatory)][scriptblock] $OnStart,
        [Parameter(Mandatory)][scriptblock] $OnBatch,
        [Parameter(Mandatory)][scriptblock] $OnCompletion,
        [string] $CollectorHost = 'COLLECTOR',
        [string] $CollectorVersion = '0.1.0',
        [switch] $Resume,
        [string] $ModulePath
    )

    $checkpoint = $null
    $resuming = $false
    if (-not [string]::IsNullOrWhiteSpace($Settings.CheckpointPath)) {
        if ($Resume) {
            $checkpoint = Import-AdgNtfsCheckpoint -Path $Settings.CheckpointPath -Settings $Settings
            $resuming = $null -ne $checkpoint -and @($checkpoint.Frontier).Count -gt 0
        }
        elseif (Test-Path -LiteralPath $Settings.CheckpointPath -PathType Leaf) {
            throw "A checkpoint already exists at '$($Settings.CheckpointPath)'. Pass -Resume to continue that scan, or delete the file to start a new one. Overwriting it silently would abandon a walk that is part way through a tree, and nothing would record that the rest of it was never read."
        }
    }

    $runId = if ($resuming) { $checkpoint.RunId } else { [guid]::NewGuid().ToString() }
    if ($null -eq $checkpoint -and -not [string]::IsNullOrWhiteSpace($Settings.CheckpointPath)) {
        $checkpoint = New-AdgNtfsCheckpoint -RunId $runId -Settings $Settings
    }

    $startedAt = Get-AdgTimestamp
    $incremental = -not (Test-AdgNtfsFullEnumerationIntent -Settings $Settings -Resuming:$resuming)

    $scopes = [System.Collections.Generic.List[object]]::new()
    foreach ($root in @($ScanRoot)) {
        $scopes.Add([ordered]@{ kind = 'directory_tree'; key = (ConvertTo-AdgUncPath $root).ToLowerInvariant() })
    }

    $start = [ordered]@{
        schema_version = $script:AdgSchemaVersion
        run_id         = $runId
        source         = [ordered]@{
            collector         = 'ntfs'
            collector_host    = $CollectorHost
            method            = 'DirectorySecurity.GetSecurityDescriptorBinaryForm'
            collector_version = $CollectorVersion
            target            = (@($ScanRoot) -join ',')
        }
        started_at     = $startedAt
        scopes         = @($scopes.ToArray())
        incremental    = $incremental
    }
    & $OnStart $start

    $writer = New-AdgNtfsBatchWriter -RunId $runId -BatchSize $Settings.BatchSize -OnBatch $OnBatch
    $deadline = if ($Settings.TimeoutSeconds -gt 0) { [datetime]::UtcNow.AddSeconds($Settings.TimeoutSeconds) } else { $null }

    # Two thin script blocks. They close over $writer, but every use mutates the object
    # they hold a reference to rather than assigning through the captured name - which is
    # the difference between a batch writer that advances and one whose counters stay at
    # their starting values.
    $onGroup = { param($group) Add-AdgNtfsObservationGroup -Writer $writer -Group $group }.GetNewClosure()
    $onFlush = { Send-AdgNtfsPendingBatch -Writer $writer }.GetNewClosure()

    $walk = Invoke-AdgNtfsDirectoryWalk -Settings $Settings -RunId $runId -ScanRoot $ScanRoot `
        -OnGroup $onGroup -OnFlush $onFlush -Checkpoint $checkpoint -Deadline $deadline -ModulePath $ModulePath

    Complete-AdgNtfsBatchWriter -Writer $writer

    $errors = @($walk.Errors)
    $rootsRead = @(@($walk.Roots) | Where-Object { $_.Read })
    $exhaustive = @(@($walk.Roots) | Where-Object { $_.Exhaustive -and $_.Read })

    $status = if ($rootsRead.Count -eq 0) { 'failed' }
    elseif ($walk.TimedOut) { 'canceled' }
    elseif ($errors.Count -gt 0) { 'partial' }
    else { 'succeeded' }

    # Achievement, not intent. Every condition has to hold: the contract accepts a
    # reconciled scope only on a clean, complete, non-incremental run, and a root that
    # skipped anything at all is left out even when the run as a whole is clean.
    $reconciled = [System.Collections.Generic.List[object]]::new()
    if ($status -eq 'succeeded' -and $errors.Count -eq 0 -and -not $incremental -and $walk.Completed) {
        foreach ($record in $exhaustive) {
            $reconciled.Add([ordered]@{ kind = 'directory_tree'; key = $record.Path.ToLowerInvariant() })
        }
    }

    $completion = [ordered]@{
        schema_version    = $script:AdgSchemaVersion
        run_id            = $runId
        status            = $status
        completed_at      = Get-AdgTimestamp
        batch_count       = $writer.BatchCount
        observation_count = $writer.ObservationCount
        error_count       = $errors.Count
        errors            = @($errors)
        reconciled_scopes = @($reconciled.ToArray())
    }
    & $OnCompletion $completion

    # A checkpoint outlives only an unfinished walk. Deleting it after a completed one is
    # not tidiness: a stale checkpoint makes the next run resume an empty frontier, read
    # nothing, and report success over a tree it never looked at.
    if (-not [string]::IsNullOrWhiteSpace($Settings.CheckpointPath)) {
        if ($walk.Completed) {
            Remove-AdgNtfsCheckpoint -Path $Settings.CheckpointPath
        }
        elseif ($null -ne $checkpoint) {
            $checkpoint.Frontier = @($walk.Frontier)
            $checkpoint.Visited = @($walk.Visited)
            $checkpoint.Metrics = $walk.Metrics
            [void] (Save-AdgNtfsCheckpoint -Checkpoint $checkpoint -Path $Settings.CheckpointPath)
        }
    }

    return [pscustomobject]@{
        RunId            = $runId
        Status           = $status
        Start            = $start
        Completion       = $completion
        Metrics          = $walk.Metrics
        Roots            = @($walk.Roots)
        Incremental      = $incremental
        Resumed          = $resuming
        ReconciledScopes = @($reconciled.ToArray())
        Frontier         = @($walk.Frontier)
        Summary          = [pscustomobject]@{
            RunId              = $runId
            Status             = $status
            ScanRootsRequested = @($ScanRoot).Count
            ScanRootsRead      = $rootsRead.Count
            ScanRootsUnread    = @(@($walk.Roots) | Where-Object { -not $_.Read } | ForEach-Object { $_.Path })
            DirectoriesVisited = $walk.Metrics.DirectoriesVisited
            DirectoriesRead    = $walk.Metrics.DirectoriesRead
            FilesRead          = $walk.Metrics.FilesRead
            AclsRead           = $walk.Metrics.AclsRead
            UniqueAclHashes    = $walk.Metrics.UniqueAclHashes
            BoundariesFound    = $walk.Metrics.BoundariesFound
            ObservationCount   = $writer.ObservationCount
            BatchCount         = $writer.BatchCount
            ErrorCount         = $errors.Count
            ReconciledScopes   = $reconciled.Count
            ElapsedSeconds     = $walk.Metrics.ElapsedSeconds
            Completed          = $walk.Completed
            PendingDirectories = @($walk.Frontier).Count
        }
    }
}

function Invoke-AdgNtfsScan {
    <#
        .SYNOPSIS
            Walk the configured scan roots and stream each run to the caller's sink.
        .DESCRIPTION
            One run per invocation, or one per scan root when -RunPerScanRoot is given.

            Prefer -RunPerScanRoot for an estate of any size. A run that reports any error
            cannot be 'succeeded', so in a single run covering fifty trees one unreadable
            descriptor downgrades the whole run - and an operator reading "partial" over
            fifty trees cannot tell which one failed. It also makes reconciliation per-tree
            rather than all-or-nothing.

            -RunPerScanRoot and a shared checkpoint are mutually exclusive. One checkpoint
            file cannot describe several runs, and sharing it would have each run resume the
            previous one's frontier and walk the wrong tree.
    #>
    [OutputType([object[]])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Settings,
        [Parameter(Mandatory)][scriptblock] $OnStart,
        [Parameter(Mandatory)][scriptblock] $OnBatch,
        [Parameter(Mandatory)][scriptblock] $OnCompletion,
        [switch] $RunPerScanRoot,
        [switch] $Resume,
        [string] $CollectorHost = $env:COMPUTERNAME,
        [string] $CollectorVersion = '0.1.0',
        [string] $ModulePath
    )

    if ([string]::IsNullOrWhiteSpace($CollectorHost)) { $CollectorHost = 'COLLECTOR' }

    if ($RunPerScanRoot -and -not [string]::IsNullOrWhiteSpace($Settings.CheckpointPath) -and
        @($Settings.ScanRoots).Count -gt 1) {
        throw 'A checkpoint cannot be shared by several runs: each run would resume the previous one''s frontier and walk the wrong tree. Give each scan root its own invocation and its own checkpointPath, or drop -RunPerScanRoot.'
    }

    # Built by hand rather than as the value of an if-expression: assigning `, @(...)` out of
    # one lets PowerShell unroll the wrapper, and a combined scan then silently behaves as
    # though -RunPerScanRoot had been passed.
    $groups = [System.Collections.Generic.List[object]]::new()
    if ($RunPerScanRoot) {
        foreach ($root in $Settings.ScanRoots) { $groups.Add(@($root)) }
    }
    else {
        $groups.Add(@($Settings.ScanRoots))
    }

    $runs = [System.Collections.Generic.List[object]]::new()
    foreach ($group in $groups) {
        $runs.Add((Invoke-AdgNtfsScanRun -Settings $Settings -ScanRoot @($group) `
                    -OnStart $OnStart -OnBatch $OnBatch -OnCompletion $OnCompletion `
                    -CollectorHost $CollectorHost -CollectorVersion $CollectorVersion `
                    -Resume:$Resume -ModulePath $ModulePath))
    }
    return , $runs.ToArray()
}
