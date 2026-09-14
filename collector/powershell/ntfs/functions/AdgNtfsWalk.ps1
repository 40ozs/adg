<#
    Traversal layer: walk a directory tree, decide where permissions change, and hand each
    directory's observations to the caller as they are read.

    ------------------------------------------------------------------------------------
    Breadth first, and why it is not a preference

    A directory's boundary is decided by comparing its DACL against what its parent hands
    down, so a parent must be read before its children. Breadth-first guarantees that and
    keeps the pending set to one frontier rather than one stack per branch - which is what
    makes a checkpoint a bounded thing to write, and what makes a resume start from a
    complete, consistent set of pending work rather than from the middle of a recursion.

    The parent facts travel on the queue entry rather than being looked up again. Two
    reasons: the parent may be gone by the time the child is read, and a resumed run would
    otherwise have to re-read parents that the original run already compared correctly.

    ------------------------------------------------------------------------------------
    A loop cannot form

    Four independent guards, because a traversal that does not terminate is not a slow
    scan, it is a scan that never reports anything:

      * the default reparse policy does not descend into a junction or symlink at all, and
        on NTFS those are the only way a directory can be its own ancestor;
      * under 'follow', the reparse targets crossed on the way to a directory travel with
        it, and a junction whose target has already been crossed on that branch is read and
        not descended into;
      * every path is added to a visited set before it is queued, so a directory is walked
        at most once per run whatever the policy;
      * maxDepth is a hard stop regardless of the other three.

    The second guard is the one that actually catches a cycle, and the reason it exists is
    worth stating: **a visited-path set does not detect a junction loop.** A junction
    pointing at its own grandparent produces \\fs\s\j, \\fs\s\j\j, \\fs\s\j\j\j - every one
    a new path, none of them ever repeating, so the visited set never fires and only the
    depth limit ends the walk, after inventing a chain of paths that describe one directory.
    Comparing reparse *targets* along the branch is what turns that into a single honest
    finding. A junction whose target cannot be read is not followed either, for the same
    reason: an unverifiable link is exactly the one that might be the cycle.

    Only the first of the four is a policy an operator can turn off.

    ------------------------------------------------------------------------------------
    Nothing skipped is silently skipped

    Every directory the walk declines to read or descend into is counted, and the count is
    what decides whether the run may claim to have enumerated its scope. A depth limit
    reached, an exclude pattern applied, a junction not followed, a descriptor denied, an
    enumeration refused - each marks its scan root as not exhaustive, and a root that is not
    exhaustive is never reconciled. A scan that stopped early and said so costs a re-run; a
    scan that stopped early and claimed completeness marks live permissions as revoked.
#>

function Invoke-AdgParallelMap {
    <#
        .SYNOPSIS
            Apply a script block to every item, at most N at a time, preserving order.
        .DESCRIPTION
            The walk's one concurrent stage. Reading a security descriptor over SMB is
            almost entirely latency, so a handful of reads in flight is the difference
            between a scan that takes an afternoon and one that takes an hour - while the
            decisions made from those reads stay strictly sequential, where they can be
            reasoned about and tested.

            **At -ConcurrencyLimit 1 the script block runs inline, in the caller's scope.**
            That is not only an optimization. PowerShell's parallel runspaces are separate
            session states: a Pester mock defined in the test's scope does not exist inside
            one, so a suite that exercised the walk through the parallel path would silently
            be testing the real file system. The walk's own tests therefore pin concurrency
            at 1, and this function is unit-tested at both settings with script blocks that
            need nothing mocked.

            Failures are captured, never thrown. One unreadable directory must not discard
            the whole level's work, so each result carries Ok, Value, and Error, and the
            caller decides what an error means.

        .PARAMETER ImportModulePath
            Module to import inside each parallel runspace. Required above concurrency 1:
            nothing from the caller's session state crosses the boundary, including this
            collector's own functions.

        .OUTPUTS
            One object per input, in input order, with Item, Ok, Value, and Error.
    #>
    [OutputType([object[]])]
    param(
        [AllowNull()][object[]] $Item,
        [Parameter(Mandatory)][scriptblock] $ScriptBlock,
        [ValidateRange(1, 32)][int] $ConcurrencyLimit = 1,
        [string] $ImportModulePath
    )

    $items = @($Item ?? @())
    if ($items.Count -eq 0) { return , @() }

    if ($ConcurrencyLimit -le 1 -or $items.Count -eq 1) {
        $results = [System.Collections.Generic.List[object]]::new()
        foreach ($entry in $items) {
            try {
                $results.Add([pscustomobject]@{
                        Item  = $entry
                        Ok    = $true
                        Value = & $ScriptBlock $entry
                        Error = $null
                    })
            }
            catch {
                $results.Add([pscustomobject]@{
                        Item = $entry; Ok = $false; Value = $null; Error = $_.Exception.Message
                    })
            }
        }
        return , $results.ToArray()
    }

    if ([string]::IsNullOrWhiteSpace($ImportModulePath)) {
        throw 'Invoke-AdgParallelMap needs -ImportModulePath above concurrency 1. A parallel runspace starts with an empty session state, so the script block would not find a single one of this collector''s functions.'
    }

    # Index carried through so the results can be put back in input order: ForEach-Object
    # -Parallel completes in whatever order the runspaces finish, and an ACL list whose
    # order depends on scheduling is not the ACL that was read.
    $indexed = @(for ($index = 0; $index -lt $items.Count; $index++) {
            [pscustomobject]@{ Index = $index; Item = $items[$index] }
        })

    # The script block crosses as text and is rebuilt inside. ForEach-Object -Parallel
    # refuses a script block as a $using: variable outright - it would carry a reference to
    # a session state the runspace does not have - and the text is exactly what a runspace
    # with its own session state can act on.
    $blockText = $ScriptBlock.ToString()

    $completed = $indexed | ForEach-Object -ThrottleLimit $ConcurrencyLimit -Parallel {
        $entry = $_
        $block = [scriptblock]::Create($using:blockText)
        Import-Module $using:ImportModulePath -Force -ErrorAction Stop
        try {
            [pscustomobject]@{
                Index = $entry.Index; Item = $entry.Item; Ok = $true
                Value = & $block $entry.Item
                Error = $null
            }
        }
        catch {
            [pscustomobject]@{
                Index = $entry.Index; Item = $entry.Item; Ok = $false
                Value = $null; Error = $_.Exception.Message
            }
        }
    }

    $ordered = [object[]]::new($items.Count)
    foreach ($result in @($completed)) { $ordered[$result.Index] = $result }
    return , $ordered
}

function New-AdgNtfsWalkMetric {
    <#
        .SYNOPSIS
            A zeroed metrics record.
        .DESCRIPTION
            The two counters worth reading together are DirectoriesRead and
            UniqueAclHashes. Their ratio is the whole economic case for a boundary scan: an
            estate with ten thousand directories and forty distinct DACLs has forty
            permission decisions in it, and a tool that reported ten thousand would bury
            them.

            SkippedReparsePoints, SkippedExcluded, SkippedDepthLimited and SkippedNotInScope
            are kept apart rather than summed, because they are four different operator
            actions - a junction policy, an exclude pattern, a depth limit, an include
            filter - and an operator reading "1,412 skipped" cannot tell which of their own
            settings produced it.
    #>
    [OutputType([pscustomobject])]
    param()

    return [pscustomobject]@{
        DirectoriesVisited   = 0
        DirectoriesRead      = 0
        FilesRead            = 0
        AclsRead             = 0
        UniqueAclHashes      = 0
        BoundariesFound      = 0
        SkippedReparsePoints = 0
        SkippedExcluded      = 0
        SkippedDepthLimited  = 0
        SkippedNotInScope    = 0
        Errors               = 0
        CheckpointsWritten   = 0
        ElapsedSeconds       = 0.0
    }
}

function ConvertTo-AdgNtfsResourceGroup {
    <#
        .SYNOPSIS
            One resource's descriptor turned into the observations that describe it.
        .DESCRIPTION
            Pure: it takes a security descriptor that has already been read and returns
            observations, errors, and the two digests the walk needs to judge this
            resource's children. Keeping it free of the file system is what lets an entire
            imaginary estate - broken inheritance, orphaned trustees, NULL DACLs - be
            exercised without one.

            acl_hash is reported only when the whole DACL was reported. A digest over part
            of a DACL is indistinguishable from a digest of all of it, and comparing one to
            a parent's projection would answer the boundary question wrong without ever
            looking wrong. When it is withheld, the boundary falls back to
            parent_unreadable - unknown, and therefore a boundary.

        .OUTPUTS
            A hashtable with Observations, Errors, AclHash, BoundaryReason,
            ChildProjection, FileProjection, DaclPresent, and Complete.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $Path,
        [Parameter(Mandatory)][hashtable] $Security,
        [ValidateSet('directory', 'file')][string] $ResourceKind = 'directory',
        [switch] $IsScanRoot,
        [AllowNull()][System.Nullable[bool]] $ParentDaclPresent,
        [AllowNull()][string] $ParentProjection,
        [AllowNull()][string] $ParentAclHash,
        [bool] $ReportUnresolved = $true,
        [string] $ObservedAt
    )

    $path = ConvertTo-AdgUncPath $Path
    if ([string]::IsNullOrWhiteSpace($ObservedAt)) { $ObservedAt = Get-AdgTimestamp }

    $observations = [System.Collections.Generic.List[object]]::new()
    $errors = [System.Collections.Generic.List[object]]::new()

    $daclPresent = [bool] $Security.DaclPresent
    $daclProtected = [bool] $Security.DaclProtected

    if (-not $daclPresent) {
        # A NULL DACL grants every user full access. It is reported as a resource
        # observation with dacl_present=false and no ACEs - the contract's way of saying
        # "unrestricted" - and raised as a finding besides, because nothing about the ACE
        # list distinguishes it from a locked-down folder.
        $errors.Add((New-AdgCollectorError -Code 'null_dacl' -Target $path -OccurredAt $ObservedAt `
                    -Message "$path has a NULL DACL, which grants every user full access to it and everything beneath it that inherits. Reported as dacl_present=false with no ACEs; treat this as a finding."))
    }

    $converted = @{ Observations = @(); Principals = @(); Errors = @(); Facts = @(); Complete = $true }
    if ($daclPresent) {
        $converted = ConvertTo-AdgNtfsAceObservation -RunId $RunId -Path $path `
            -Ace $Security.Ace -ObservedAt $ObservedAt
    }
    foreach ($item in $converted.Errors) { $errors.Add($item) }

    $aclHash = $null
    if ($converted.Complete) {
        $aclHash = Get-AdgAclHash -DaclPresent $daclPresent -DaclProtected $daclProtected -Ace $converted.Facts
    }

    $boundaryReason = Resolve-AdgAclBoundary -DaclPresent $daclPresent -DaclProtected $daclProtected `
        -IsShareRoot:(Test-AdgShareRootPath $path) -IsScanRoot:$IsScanRoot `
        -AclHash $aclHash -ParentDaclPresent $ParentDaclPresent -ParentProjection $ParentProjection

    # The resource first, then its entries. The backend does not require the order, but a
    # partially delivered batch stays interpretable when the thing being described arrives
    # before the things describing it.
    $observations.Add((ConvertTo-AdgNtfsResourceObservation -RunId $RunId -Path $path `
                -DaclPresent $daclPresent -DaclProtected $daclProtected `
                -OwnerSid ([string] $Security.OwnerSid) -GroupSid ([string] $Security.GroupSid) `
                -AceCount @($converted.Observations).Count -ResourceKind $ResourceKind `
                -BoundaryReason $boundaryReason -AclHash $aclHash -ParentAclHash $ParentAclHash `
                -ObservedAt $ObservedAt))

    foreach ($item in $converted.Observations) { $observations.Add($item) }
    if ($ReportUnresolved) {
        foreach ($item in $converted.Principals) { $observations.Add($item) }
    }

    # The two projections this resource hands down. Computed from the facts that were
    # reported rather than from the raw entries, so a DACL that could only be read in part
    # projects nothing - which reaches its children as parent_unreadable rather than as a
    # confident comparison against half an ACL.
    $childProjection = $null
    $fileProjection = $null
    if ($converted.Complete -and $ResourceKind -eq 'directory') {
        $childProjection = Get-AdgProjectedChildAclHash -DaclPresent $daclPresent `
            -Ace $converted.Facts -ForContainer $true
        $fileProjection = Get-AdgProjectedChildAclHash -DaclPresent $daclPresent `
            -Ace $converted.Facts -ForContainer $false
    }

    return @{
        Observations    = $observations.ToArray()
        Errors          = $errors.ToArray()
        AclHash         = $aclHash
        BoundaryReason  = $boundaryReason
        ChildProjection = $childProjection
        FileProjection  = $fileProjection
        DaclPresent     = $daclPresent
        Complete        = [bool] $converted.Complete
    }
}

function Read-AdgNtfsSecurity {
    <#
        .SYNOPSIS
            Read one resource's descriptor, retrying a transient failure.
        .DESCRIPTION
            An access denial is not retried in any meaningful sense - it is almost always a
            permission problem, and retrying just spends time arriving at the same answer -
            but the loop does not try to tell the two apart, because a wrong guess about
            which is which would turn a recoverable blip into a permanent gap. The bound
            keeps the cost small either way.

            ADG never escalates to make a read succeed. SeBackupPrivilege would bypass the
            DACL and WRITE_OWNER would let the collector grant itself READ_CONTROL; both are
            refused by design (ADR-0004), because a tool that can read what its own
            credentials may not read is measuring something other than the estate.

            **"It is not there" and "it is there and unreadable" stay apart.** They call for
            different fixes - one is a stale target list, the other a missing right - and an
            audit that reported them identically would send somebody to the wrong place. The
            existence check costs a round trip, so it is made only after a read has already
            failed, where the extra call is free relative to the failure.

        .OUTPUTS
            A hashtable with Security, Error, and Code; the descriptor or the pair, never
            both.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][string] $Path,
        [ValidateSet('directory', 'file')][string] $ResourceKind = 'directory',
        [int] $RetryCount = 1,
        [int] $RetryDelaySeconds = 2
    )

    $attempts = [Math]::Max(1, $RetryCount + 1)
    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        try {
            $security = if ($ResourceKind -eq 'file') {
                Get-AdgFileSecurity -Path $Path
            }
            else {
                Get-AdgDirectorySecurity -Path $Path
            }
            return @{ Security = $security; Error = $null; Code = $null }
        }
        catch {
            if ($attempt -ge $attempts) {
                $reachable = $true
                try { $reachable = [bool] (Test-AdgResourceExists -Path $Path) }
                catch { $reachable = $false }

                if (-not $reachable) {
                    return @{
                        Security = $null
                        Code     = 'path_not_found'
                        Error    = "$Path could not be reached as a $ResourceKind. Its NTFS permissions are unobserved, not absent; check the target list before checking rights."
                    }
                }
                return @{
                    Security = $null
                    Code     = 'access_denied'
                    Error    = "The security descriptor of $Path could not be read after $attempts attempt(s): $($_.Exception.Message). Reading a DACL needs READ_CONTROL on the object, and traverse on the path to it; ADG never takes ownership or changes a descriptor to make a read succeed."
                }
            }
            Write-Warning "$Path was unreadable (attempt $attempt/$attempts): $($_.Exception.Message)"
            if ($RetryDelaySeconds -gt 0) { Start-Sleep -Seconds ($RetryDelaySeconds * $attempt) }
        }
    }
    # Unreachable: the loop either returns a descriptor or returns an error on its last
    # attempt. Present so every path out of the function has the same shape.
    return @{ Security = $null; Code = 'access_denied'; Error = "The security descriptor of $Path could not be read." }
}

function Read-AdgNtfsDirectoryUnit {
    <#
        .SYNOPSIS
            One directory's descriptor and child list, read together.
        .DESCRIPTION
            The walk's unit of concurrent work, and the only function it runs in parallel.
            Descriptor and children are read in one unit because they are two round trips to
            the same directory on the same remote server, and because pairing them halves
            the number of times a scan waits for that server.

            Self-contained by construction: everything it needs is on the request object.
            A parallel runspace starts with an empty session state and cannot see a single
            variable from the caller's, so a unit that closed over settings would work
            perfectly at concurrency 1 and fail at 2.

            It never throws. Each half reports its own failure, because "the ACL was read
            and the contents were not" is an ordinary result - listing a directory needs
            FILE_LIST_DIRECTORY, reading its ACL needs READ_CONTROL, and an administrator
            can grant either without the other.

        .PARAMETER Request
            Path, ReadDescriptor, Enumerate, RetryCount, RetryDelaySeconds.

        .OUTPUTS
            A hashtable with Security, SecurityError, Children, and ChildrenError.
    #>
    [OutputType([hashtable])]
    param([Parameter(Mandatory)] $Request)

    $security = $null
    $securityError = $null
    $securityErrorCode = 'access_denied'
    if ($Request.ReadDescriptor) {
        $read = Read-AdgNtfsSecurity -Path $Request.Path -ResourceKind 'directory' `
            -RetryCount ([int] $Request.RetryCount) -RetryDelaySeconds ([int] $Request.RetryDelaySeconds)
        $security = $read.Security
        $securityError = $read.Error
        $securityErrorCode = $read.Code
    }

    $children = $null
    $childrenError = $null
    if ($Request.Enumerate) {
        try {
            # Assigned first, wrapped second, and never `@(Get-AdgChildDirectory ...)`.
            # That function returns `, $array` so an empty directory stays an empty array
            # rather than nothing - and @() around such a call collects the one written
            # object into a one-element array *holding* the array, which then iterates once
            # and turns every property read into a member lookup over a collection. The
            # assignment collapses the wrapper; the @() afterwards normalizes a mock that
            # returned a bare scalar.
            $children = Get-AdgChildDirectory -Path $Request.Path
            $children = @($children)
        }
        catch { $childrenError = $_.Exception.Message }
    }

    return @{
        Security          = $security
        SecurityError     = $securityError
        SecurityErrorCode = $securityErrorCode
        Children          = $children
        ChildrenError     = $childrenError
    }
}

function Invoke-AdgNtfsDirectoryWalk {
    <#
        .SYNOPSIS
            Walk the configured trees, emitting one group of observations per resource.
        .DESCRIPTION
            Streaming: each resource's observations are handed to -OnGroup as soon as they
            are built and are not retained here. A tree scan's memory use must not grow with
            the tree, or the scan that most needs to finish is the one that cannot.

            The group is the unit deliberately. The backend verifies a reported acl_hash
            against the ntfs_ace observations that arrive with it, and skips the check when
            the batch holds fewer than the declared ace_count - so a resource and its entries
            must stay together all the way to the wire (collector-protocol.md section 5).

            **One BFS level at a time.** The level is read concurrently and then processed
            strictly in order: every decision - scope, boundary, what to queue next - is made
            sequentially, where it can be reasoned about and tested, and only the waiting is
            parallel. The processing order within a level is the enumeration order, so two
            runs of the same tree produce the same observations in the same sequence whatever
            the concurrency setting.

        .PARAMETER OnGroup
            Invoked with one array of observations per resource read.

        .PARAMETER OnFlush
            Invoked before each checkpoint is written, and must not return until everything
            handed to -OnGroup has been submitted. A checkpoint written while observations
            are still buffered would, on resume, skip directories that were collected and
            never sent.

        .PARAMETER Checkpoint
            A checkpoint to resume from, and to record progress into. When it carries a
            frontier the scan roots are not re-seeded: the frontier already says where the
            walk had got to, and starting the roots again would re-walk the finished part.

        .OUTPUTS
            A hashtable with Errors, Metrics, Roots, Frontier, Visited, Completed, TimedOut.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Settings,
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string[]] $ScanRoot,
        [Parameter(Mandatory)][scriptblock] $OnGroup,
        [scriptblock] $OnFlush,
        [AllowNull()][pscustomobject] $Checkpoint,
        [AllowNull()][System.Nullable[datetime]] $Deadline,
        [string] $ModulePath
    )

    $started = [datetime]::UtcNow
    $metrics = New-AdgNtfsWalkMetric
    $errors = [System.Collections.Generic.List[object]]::new()
    $digests = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    $visited = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $queue = [System.Collections.Generic.Queue[object]]::new()

    # One record per scan root. Exhaustive starts true and is only ever turned off: a root
    # may claim its directory_tree scope exactly when nothing under it was skipped for any
    # reason, and every reason is a place that sets this false.
    $roots = [ordered]@{}
    foreach ($root in @($ScanRoot)) {
        $canonical = ConvertTo-AdgUncPath $root
        $roots[$canonical.ToLowerInvariant()] = [pscustomobject]@{
            Path       = $canonical
            Exhaustive = $true
            Read       = $false
            Reasons    = [System.Collections.Generic.List[string]]::new()
        }
    }

    # Mutates the record object rather than assigning through the name, which is what lets a
    # nested function change it: an assignment to a variable inside a function writes a
    # local copy, but a property set on an object the parent scope also holds is seen by
    # both.
    function Add-NotExhaustive {
        param([string] $RootKey, [string] $Reason)
        if (-not $roots.Contains($RootKey)) { return }
        $record = $roots[$RootKey]
        $record.Exhaustive = $false
        if (-not $record.Reasons.Contains($Reason)) { $record.Reasons.Add($Reason) }
    }

    # An include filter means, by construction, that directories inside the tree were not
    # read. Such a run is still useful; it simply cannot claim to have enumerated the tree.
    if (@($Settings.IncludePaths).Count -gt 0) {
        foreach ($key in @($roots.Keys)) { Add-NotExhaustive $key 'include_filter' }
    }

    $resuming = $null -ne $Checkpoint -and @($Checkpoint.Frontier).Count -gt 0
    if ($resuming) {
        foreach ($path in @($Checkpoint.Visited)) { [void] $visited.Add([string] $path) }
        foreach ($entry in @($Checkpoint.Frontier)) {
            [void] $visited.Add($entry.Path.ToLowerInvariant())
            $queue.Enqueue($entry)
        }
        # This half of the run did not walk the part the first half did, so it cannot claim
        # that part's scope. The two halves share a run id and the server sees one run; the
        # honest reading is that no single pass enumerated the tree end to end.
        foreach ($key in @($roots.Keys)) { Add-NotExhaustive $key 'resumed' }
    }
    else {
        foreach ($key in @($roots.Keys)) {
            $record = $roots[$key]
            [void] $visited.Add($key)
            $queue.Enqueue([pscustomobject]@{
                    Path              = $record.Path
                    Depth             = 0
                    Root              = $key
                    # The walk started here, so no parent was read and nothing can be
                    # compared - which is a boundary, because unknown is never unchanged.
                    IsScanRoot        = $true
                    ParentDaclPresent = $null
                    ParentProjection  = $null
                    ParentAclHash     = $null
                    # The reparse targets crossed on the way here. Empty at a root, and the
                    # branch-local set a junction's target is checked against.
                    LinkTargets       = @()
                })
        }
    }

    $lastCheckpoint = [datetime]::UtcNow
    $timedOut = $false
    # The part of the current level still to process. It has to be visible to the
    # checkpoint writer: a level is drained from the queue up front, so anything left in it
    # is pending work that the queue no longer knows about.
    $pending = [System.Collections.Generic.List[object]]::new()

    function Save-Progress {
        if ([string]::IsNullOrWhiteSpace($Settings.CheckpointPath) -or $null -eq $Checkpoint) { return }
        if ($null -ne $OnFlush) { & $OnFlush }
        $metrics.ElapsedSeconds = [Math]::Round(([datetime]::UtcNow - $started).TotalSeconds, 3)
        $Checkpoint.Frontier = @(@($pending) + @($queue.ToArray()))
        # Only under 'follow' is the visited set load-bearing across a resume: with the
        # other two policies the frontier is a tree frontier and a directory cannot be
        # reached twice, so persisting it would be the one part of a checkpoint that grows
        # without bound on a large estate.
        $Checkpoint.Visited = if ($Settings.ReparsePointPolicy -eq 'follow') { @($visited) } else { @() }
        $Checkpoint.Metrics = $metrics
        [void] (Save-AdgNtfsCheckpoint -Checkpoint $Checkpoint -Path $Settings.CheckpointPath)
        $metrics.CheckpointsWritten++
    }

    while ($queue.Count -gt 0 -and -not $timedOut) {
        if ($null -ne $Deadline -and [datetime]::UtcNow -ge $Deadline) { $timedOut = $true; break }

        # Drain one BFS level. The queue holds at most two adjacent depths, and everything
        # enqueued from here lands behind what is taken now, so this is exactly one level.
        $level = [System.Collections.Generic.List[object]]::new()
        while ($queue.Count -gt 0) { $level.Add($queue.Dequeue()) }

        $pending.Clear()
        foreach ($entry in $level) { $pending.Add($entry) }

        # In chunks, so a directory with a hundred thousand subdirectories does not become
        # a hundred thousand in-flight requests. The frontier is still held whole - that is
        # what a checkpoint has to persist - but the concurrent stage is bounded.
        $chunkSize = 512
        for ($offset = 0; $offset -lt $level.Count; $offset += $chunkSize) {
            if ($null -ne $Deadline -and [datetime]::UtcNow -ge $Deadline) { $timedOut = $true; break }

            $chunk = @($level[$offset..([Math]::Min($offset + $chunkSize, $level.Count) - 1)])
            $scopes = [System.Collections.Generic.List[object]]::new()
            $requests = [System.Collections.Generic.List[object]]::new()

            foreach ($entry in $chunk) {
                $scope = Test-AdgPathInScope -Path $entry.Path -Include $Settings.IncludePaths `
                    -Exclude $Settings.ExcludePaths
                $scopes.Add($scope)
                $requests.Add([pscustomobject]@{
                        Path              = $entry.Path
                        ReadDescriptor    = [bool] $scope.Read -and -not $scope.Excluded
                        # Enumerated even at the depth limit, so that "the limit was reached"
                        # can be told from "the tree ended here". Only the first makes a root
                        # non-exhaustive, and a truncated tree reported as a complete one is
                        # the failure that matters.
                        Enumerate         = [bool] $scope.Descend
                        RetryCount        = [int] $Settings.RetryCount
                        RetryDelaySeconds = [int] $Settings.RetryDelaySeconds
                    })
            }

            $reads = Invoke-AdgParallelMap -Item $requests.ToArray() -ConcurrencyLimit $Settings.ConcurrencyLimit `
                -ImportModulePath $ModulePath -ScriptBlock { param($request) Read-AdgNtfsDirectoryUnit -Request $request }

            for ($index = 0; $index -lt $chunk.Count; $index++) {
                $entry = $chunk[$index]
                $scope = $scopes[$index]
                $result = $reads[$index]
                [void] $pending.Remove($entry)

                $metrics.DirectoriesVisited++

                if ($scope.Excluded) {
                    $metrics.SkippedExcluded++
                    Add-NotExhaustive $entry.Root 'excluded_path'
                    continue
                }

                # Invoke-AdgParallelMap captures rather than throws, so a unit that failed
                # outright - a runspace that could not import the module, say - arrives here
                # as a failed result rather than as an aborted level.
                $unit = if ($result.Ok) { $result.Value } else {
                    @{
                        Security = $null; SecurityError = $result.Error
                        SecurityErrorCode = 'access_denied'
                        Children = $null; ChildrenError = $result.Error
                    }
                }

                $group = $null
                if ($scope.Read) {
                    if ($null -eq $unit.Security) {
                        # No resource observation. An observation asserts the object was
                        # seen, and a descriptor that could not be read was not seen - while
                        # a row with no ACEs would read as "nobody has access", which is the
                        # opposite of unknown.
                        $errors.Add((New-AdgCollectorError -Code ([string] $unit.SecurityErrorCode) `
                                    -Target $entry.Path -Message ([string] $unit.SecurityError)))
                        $metrics.Errors++
                        Add-NotExhaustive $entry.Root ([string] $unit.SecurityErrorCode)
                        continue
                    }

                    $group = ConvertTo-AdgNtfsResourceGroup -RunId $RunId -Path $entry.Path -Security $unit.Security `
                        -ResourceKind 'directory' -IsScanRoot:([bool] $entry.IsScanRoot) `
                        -ParentDaclPresent $entry.ParentDaclPresent -ParentProjection $entry.ParentProjection `
                        -ParentAclHash $entry.ParentAclHash -ReportUnresolved $Settings.ReportUnresolved

                    foreach ($item in $group.Errors) {
                        $errors.Add($item)
                        $metrics.Errors++
                        Add-NotExhaustive $entry.Root 'partial_descriptor'
                    }

                    $metrics.DirectoriesRead++
                    $metrics.AclsRead++
                    if ($null -ne $group.AclHash -and $digests.Add($group.AclHash)) { $metrics.UniqueAclHashes++ }
                    if ($null -ne $group.BoundaryReason) { $metrics.BoundariesFound++ }
                    if ($roots.Contains($entry.Root)) { $roots[$entry.Root].Read = $true }

                    & $OnGroup $group.Observations
                }
                else {
                    # Inside the tree but outside the include filter: passed through so the
                    # directories below can be reached, and not reported, because nobody
                    # asked about it.
                    $metrics.SkippedNotInScope++
                }

                # Whatever this directory hands its children. All three are $null when it
                # was passed through rather than read, which reaches the child as
                # parent_unreadable - correct, because nobody read the parent, so nobody
                # knows whether the child differs from it.
                $childDaclPresent = if ($null -eq $group) { $null } else { $group.DaclPresent }
                $childProjection = if ($null -eq $group) { $null } else { $group.ChildProjection }
                $childParentHash = if ($null -eq $group) { $null } else { $group.AclHash }

                if ($scope.Descend) {
                    if ($null -ne $unit.ChildrenError) {
                        $errors.Add((New-AdgCollectorError -Code 'access_denied' -Target $entry.Path `
                                    -Message "$($entry.Path) could not be enumerated: $($unit.ChildrenError). Listing a directory needs FILE_LIST_DIRECTORY on it, which is a different right from the READ_CONTROL that reading its ACL needs - so a directory whose ACL was read and whose contents were not is an ordinary result, and everything beneath it is unobserved rather than absent."))
                        $metrics.Errors++
                        Add-NotExhaustive $entry.Root 'unreadable_directory'
                    }
                    else {
                        $children = @($unit.Children ?? @())
                        if ($entry.Depth -ge $Settings.MaxDepth) {
                            if ($children.Count -gt 0) {
                                $metrics.SkippedDepthLimited += $children.Count
                                Add-NotExhaustive $entry.Root 'depth_limit'
                                $errors.Add((New-AdgCollectorError -Code 'depth_limit_reached' -Target $entry.Path `
                                            -Message "$($entry.Path) holds $($children.Count) subdirector(ies) that were not walked: maxDepth is $($Settings.MaxDepth) and this directory sits at that depth. Everything below it is unobserved, not absent."))
                            }
                        }
                        else {
                            foreach ($child in $children) {
                                $childPath = ConvertTo-AdgUncPath $child.Path
                                $childKey = $childPath.ToLowerInvariant()

                                if ($child.IsReparsePoint -and $Settings.ReparsePointPolicy -eq 'ignore') {
                                    $metrics.SkippedReparsePoints++
                                    Add-NotExhaustive $entry.Root 'reparse_point'
                                    continue
                                }

                                if (-not $visited.Add($childKey)) {
                                    # A path reached twice: two links to one target, or a
                                    # directory a previous branch already walked. The
                                    # directory is reported once, under the first path that
                                    # reached it; what is missing is the copy of the subtree
                                    # beneath this second path.
                                    $metrics.SkippedReparsePoints++
                                    Add-NotExhaustive $entry.Root 'traversal_loop'
                                    $through = if ($child.IsReparsePoint) { " through a reparse point to '$($child.LinkTarget)'" } else { '' }
                                    $errors.Add((New-AdgCollectorError -Code 'traversal_loop' -Target $childPath `
                                                -Message "$childPath was reached a second time$through and was not walked again. Its own descriptor is reported once, under the first path that reached it."))
                                    continue
                                }

                                $childDepth = $entry.Depth + 1
                                $childTargets = @($entry.LinkTargets ?? @())

                                if ($child.IsReparsePoint) {
                                    # Read, never descended past, under every policy but
                                    # 'follow'. The junction is a real directory with a real
                                    # ACL; what is below it belongs to the target, and is
                                    # reported under the target's own path when that is in
                                    # scope. Queued at the depth limit so nothing descends.
                                    $stop = $Settings.ReparsePointPolicy -ne 'follow'
                                    $reason = 'reparse_point'
                                    $target = [string] $child.LinkTarget

                                    if (-not $stop) {
                                        if ([string]::IsNullOrWhiteSpace($target)) {
                                            # An unverifiable link is exactly the one that
                                            # might be the cycle.
                                            $stop = $true
                                            $reason = 'unreadable_link_target'
                                            $errors.Add((New-AdgCollectorError -Code 'reparse_target_unknown' -Target $childPath `
                                                        -Message "$childPath is a reparse point whose target could not be read, so it was not followed. Following a link the collector cannot identify is how a junction loop becomes a walk that never ends; its own descriptor is still reported."))
                                            $metrics.Errors++
                                        }
                                        elseif ($childTargets -contains $target.ToLowerInvariant()) {
                                            # The guard that actually catches a cycle. A
                                            # junction to its own ancestor produces a fresh
                                            # path every time round, so the visited set above
                                            # never fires; the repeated target does.
                                            $stop = $true
                                            $reason = 'traversal_loop'
                                            $errors.Add((New-AdgCollectorError -Code 'traversal_loop' -Target $childPath `
                                                        -Message "$childPath is a reparse point to '$target', which this branch has already crossed. Following it would walk one directory under an endless chain of new paths; it was read and not descended into."))
                                            $metrics.Errors++
                                        }
                                        else {
                                            $childTargets = @($childTargets + $target.ToLowerInvariant())
                                        }
                                    }

                                    if ($stop) { $childDepth = $Settings.MaxDepth }
                                    $metrics.SkippedReparsePoints++
                                    Add-NotExhaustive $entry.Root $reason
                                }

                                $queue.Enqueue([pscustomobject]@{
                                        Path              = $childPath
                                        Depth             = $childDepth
                                        Root              = $entry.Root
                                        IsScanRoot        = $false
                                        ParentDaclPresent = $childDaclPresent
                                        ParentProjection  = $childProjection
                                        ParentAclHash     = $childParentHash
                                        LinkTargets       = $childTargets
                                    })
                            }
                        }
                    }
                }

                if ($Settings.IncludeFiles -and $null -ne $group) {
                    $fileResult = Read-AdgNtfsFileGroup -Settings $Settings -RunId $RunId -Directory $entry.Path `
                        -ParentDaclPresent $group.DaclPresent -ParentProjection $group.FileProjection `
                        -ParentAclHash $group.AclHash -OnGroup $OnGroup -Metrics $metrics -Digest $digests
                    foreach ($item in $fileResult.Errors) {
                        $errors.Add($item)
                        $metrics.Errors++
                        Add-NotExhaustive $entry.Root 'unreadable_file'
                    }
                }

                if (-not [string]::IsNullOrWhiteSpace($Settings.CheckpointPath) -and $null -ne $Checkpoint) {
                    if (([datetime]::UtcNow - $lastCheckpoint).TotalSeconds -ge $Settings.CheckpointIntervalSeconds) {
                        Save-Progress
                        $lastCheckpoint = [datetime]::UtcNow
                    }
                }

                if ($null -ne $Deadline -and [datetime]::UtcNow -ge $Deadline) { $timedOut = $true; break }
            }

            if ($timedOut) { break }
        }

        # Anything the level did not reach goes back on the queue, so the frontier this
        # function returns - and any checkpoint written from it - is the complete set of
        # work still to do.
        foreach ($entry in @($pending)) { $queue.Enqueue($entry) }
        $pending.Clear()
    }

    if ($timedOut) {
        $errors.Add((New-AdgCollectorError -Code 'timeout' -Target ($ScanRoot -join ',') `
                    -Message "The scan reached its timeoutSeconds limit with $($queue.Count) director(ies) still to read. It stopped where it was rather than reporting the part it reached as the whole; resume from the checkpoint to finish it."))
        $metrics.Errors++
        foreach ($key in @($roots.Keys)) { Add-NotExhaustive $key 'timeout' }
    }

    $metrics.ElapsedSeconds = [Math]::Round(([datetime]::UtcNow - $started).TotalSeconds, 3)

    return @{
        Errors    = $errors.ToArray()
        Metrics   = $metrics
        Roots     = @($roots.Values)
        Frontier  = @($queue.ToArray())
        Visited   = if ($Settings.ReparsePointPolicy -eq 'follow') { @($visited) } else { @() }
        Completed = (-not $timedOut)
        TimedOut  = $timedOut
    }
}

function Read-AdgNtfsFileGroup {
    <#
        .SYNOPSIS
            Read the files of one directory, when file scanning is switched on.
        .DESCRIPTION
            Off by default and never implicit. A file's ACL is a real fact and an explicit
            ACE on a file inside an otherwise uniform folder is a real finding, but an
            estate holds orders of magnitude more files than directories - so this turns a
            scan measured in minutes into one measured in days, and that is the operator's
            decision to make rather than the collector's.

            Files are leaves. They are never traversed, they project nothing, and they are
            compared against the *object* projection of their directory rather than the
            container one - a file that inherits cleanly carries its folder's OBJECT_INHERIT
            entries with every inheritance flag stripped, which is a different document from
            what a subfolder carries.

        .OUTPUTS
            A hashtable with Errors. Observations go to -OnGroup as they are built.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Settings,
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $Directory,
        [AllowNull()][System.Nullable[bool]] $ParentDaclPresent,
        [AllowNull()][string] $ParentProjection,
        [AllowNull()][string] $ParentAclHash,
        [Parameter(Mandatory)][scriptblock] $OnGroup,
        [Parameter(Mandatory)][pscustomobject] $Metrics,
        [Parameter(Mandatory)][object] $Digest
    )

    $errors = [System.Collections.Generic.List[object]]::new()

    $files = $null
    try {
        # Assigned, then wrapped. See Read-AdgNtfsDirectoryUnit for why @(command) around a
        # function that returns `, $array` yields an array holding an array.
        $files = Get-AdgChildFile -Path $Directory
        $files = @($files)
    }
    catch {
        $errors.Add((New-AdgCollectorError -Code 'access_denied' -Target $Directory `
                    -Message "The files of $Directory could not be enumerated: $($_.Exception.Message). Their permissions are unobserved, not absent."))
        return @{ Errors = $errors.ToArray() }
    }

    foreach ($file in $files) {
        $filePath = ConvertTo-AdgUncPath $file.Path
        $read = Read-AdgNtfsSecurity -Path $filePath -ResourceKind 'file' `
            -RetryCount $Settings.RetryCount -RetryDelaySeconds $Settings.RetryDelaySeconds

        if ($null -eq $read.Security) {
            $errors.Add((New-AdgCollectorError -Code ([string] $read.Code) -Target $filePath -Message $read.Error))
            continue
        }

        $group = ConvertTo-AdgNtfsResourceGroup -RunId $RunId -Path $filePath -Security $read.Security `
            -ResourceKind 'file' -ParentDaclPresent $ParentDaclPresent -ParentProjection $ParentProjection `
            -ParentAclHash $ParentAclHash -ReportUnresolved $Settings.ReportUnresolved

        foreach ($item in $group.Errors) { $errors.Add($item) }

        $Metrics.FilesRead++
        $Metrics.AclsRead++
        if ($null -ne $group.AclHash -and $Digest.Add($group.AclHash)) { $Metrics.UniqueAclHashes++ }
        if ($null -ne $group.BoundaryReason) { $Metrics.BoundariesFound++ }

        & $OnGroup $group.Observations
    }

    return @{ Errors = $errors.ToArray() }
}
