<#
    Orchestration layer: read the configured share roots and assemble a contract-shaped run.

    The scan reads everything first and builds the payloads afterwards, exactly as the SMB
    collector does, so that a dry run produces the same bytes a real run would POST and the
    test suite can validate genuine collector output against the published schemas.

    ------------------------------------------------------------------------------------
    Scopes, and why this phase reconciles nothing

    Reconciling a scope is what lets the backend mark unseen objects absent, so a scope must
    never claim more than the run read. The contract's scope kind for the file system is
    `directory_tree`, and it claims the whole tree beneath a path was enumerated. A run that
    reads share roots and nothing else has enumerated no tree: reconciling that scope would
    mark every directory under every root as deleted.

    So every run this collector produces is marked **incremental**, which the contract
    defines as "deliberately re-read only part of its scopes" and which the backend refuses
    to let reconcile at all. The `directory_tree` scopes are still declared, because they
    state what the run set out to look at and are what a later full walk will reconcile;
    `reconciled_scopes` stays empty on every completion, including a clean one.

    That is a deliberate floor, not an oversight. The tree walk that can honestly claim a
    `directory_tree` scope arrives with the recursive scanner; until then, absence of a
    directory from ADG means nobody has looked, which is exactly what it should mean.

    ------------------------------------------------------------------------------------
    Batching keeps a resource with its ACEs

    Split-AdgNtfsObservationBatch never splits a directory's observations across two
    batches. That is not tidiness: the backend verifies a reported acl_hash against the ACEs
    that arrive with it, and can only do so when the batch carries the whole DACL. Splitting
    would silently disable the one check that catches entries lost in transit.
#>

function Get-AdgNtfsResourceObservation {
    <#
        .SYNOPSIS
            Read one share root: its descriptor facts, its DACL, and its unresolved trustees.
        .DESCRIPTION
            Returns a hashtable with Observations, Errors, Scope, and Read. Never throws for
            a condition a real estate produces: a share that has gone away, a descriptor the
            collector lacks rights to read, and a trustee that will not resolve are all
            recorded as collector errors and the scan moves on.

            A transient failure is retried with a bounded backoff. An access denial is not
            retried in any meaningful sense - it is almost always a permission problem, and
            retrying it just spends time arriving at the same answer - but the retry loop
            does not try to tell the two apart, because a wrong guess about which is which
            would turn a recoverable blip into a permanent gap. The bound keeps the cost
            small either way.

            acl_hash is reported only when the whole DACL was reported. A digest over part
            of a DACL is indistinguishable from a digest of all of it, and comparing one to
            a parent's would answer the boundary question wrong without ever looking wrong.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][string] $Path,
        [Parameter(Mandatory)][pscustomobject] $Settings,
        [Parameter(Mandatory)][string] $RunId
    )

    $path = ConvertTo-AdgUncPath $Path
    $observations = [System.Collections.Generic.List[object]]::new()
    $errors = [System.Collections.Generic.List[object]]::new()
    $scope = [ordered]@{ kind = 'directory_tree'; key = $path.ToLowerInvariant() }
    $observedAt = Get-AdgTimestamp

    if (-not (Test-AdgResourceExists -Path $path)) {
        # No resource observation. An observation asserts the object was seen, and a path
        # that did not answer was not seen - it is unobserved, not empty.
        $errors.Add((New-AdgCollectorError -Code 'path_not_found' -Target $path -OccurredAt $observedAt `
                    -Message "$path could not be reached as a directory. Its NTFS permissions are unobserved, not absent; the run reconciles nothing."))
        return @{ Observations = @(); Errors = $errors.ToArray(); Scope = $scope; Read = $false }
    }

    $security = $null
    $attempts = [Math]::Max(1, $Settings.RetryCount + 1)
    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        try {
            $security = Get-AdgDirectorySecurity -Path $path
            break
        }
        catch {
            if ($attempt -ge $attempts) {
                $errors.Add((New-AdgCollectorError -Code 'access_denied' -Target $path -OccurredAt $observedAt `
                            -Message "The security descriptor of $path could not be read after $attempts attempt(s): $($_.Exception.Message). Reading a DACL needs READ_CONTROL on the directory; ADG never takes ownership or changes a descriptor to make a read succeed."))
                return @{ Observations = @(); Errors = $errors.ToArray(); Scope = $scope; Read = $false }
            }
            Write-Warning "$path was unreadable (attempt $attempt/$attempts): $($_.Exception.Message)"
            if ($Settings.RetryDelaySeconds -gt 0) {
                Start-Sleep -Seconds ($Settings.RetryDelaySeconds * $attempt)
            }
        }
    }

    $daclPresent = [bool] $security.DaclPresent
    $daclProtected = [bool] $security.DaclProtected

    if (-not $daclPresent) {
        # A NULL DACL grants every user full access to the directory. It is reported as a
        # resource observation with dacl_present=false and no ACEs - which is the contract's
        # way of saying "unrestricted" - and raised as a finding besides, because nothing
        # about the ACE list distinguishes it from a locked-down folder.
        $errors.Add((New-AdgCollectorError -Code 'null_dacl' -Target $path -OccurredAt $observedAt `
                    -Message "$path has a NULL DACL, which grants every user full access to it and everything beneath it that inherits. Reported as dacl_present=false with no ACEs; treat this as a finding."))
    }

    $converted = @{
        Observations = @()
        Principals   = @()
        Errors       = @()
        Facts        = @()
        Complete     = $true
    }
    if ($daclPresent) {
        $converted = ConvertTo-AdgNtfsAceObservation -RunId $RunId -Path $path `
            -Ace $security.Ace -ObservedAt $observedAt
    }

    foreach ($item in $converted.Errors) { $errors.Add($item) }

    $aclHash = $null
    if ($converted.Complete) {
        $aclHash = Get-AdgAclHash -DaclPresent $daclPresent -DaclProtected $daclProtected -Ace $converted.Facts
    }

    # The resource first, then its entries. The backend does not require the order, but a
    # partially delivered batch stays interpretable when the thing being described arrives
    # before the things describing it.
    $observations.Add((ConvertTo-AdgNtfsResourceObservation -RunId $RunId -Path $path `
                -DaclPresent $daclPresent -DaclProtected $daclProtected `
                -OwnerSid ([string] $security.OwnerSid) -GroupSid ([string] $security.GroupSid) `
                -AceCount @($converted.Observations).Count -DepthFromShareRoot 0 -IsShareRoot `
                -AclHash $aclHash -ObservedAt $observedAt))

    foreach ($item in $converted.Observations) { $observations.Add($item) }
    if ($Settings.ReportUnresolved) {
        foreach ($item in $converted.Principals) { $observations.Add($item) }
    }

    return @{
        Observations = $observations.ToArray()
        Errors       = $errors.ToArray()
        Scope        = $scope
        Read         = $true
    }
}

function Split-AdgNtfsObservationBatch {
    <#
        .SYNOPSIS
            Chunk observations into contract-sized batches without splitting a directory.
        .DESCRIPTION
            Takes groups rather than a flat list, and never puts one group's observations in
            two batches. The backend verifies a reported acl_hash against the ntfs_ace
            observations that arrive with it and skips the check when the batch holds fewer
            than the declared ace_count, so a split would quietly disable the one check that
            catches ACEs lost in transit.

            A group larger than BatchSize gets a batch of its own rather than being cut. It
            can exceed the requested size but never the contract's hard ceiling of 1000; a
            DACL with more than a thousand entries is refused loudly, because truncating it
            would look exactly like a shorter ACL.

            Each batch gets a UUID generated once and reused on every retry - that is what
            makes a retry after a timeout a recognized duplicate instead of a second
            application.
    #>
    [OutputType([object[]])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [AllowNull()][object[]] $Group,
        [ValidateRange(1, 1000)][int] $BatchSize = 500
    )

    $groups = @($Group ?? @())
    # The comma operator keeps an empty result an empty array. A bare `return @()` unrolls
    # to nothing, and under strict mode the caller's .Count then fails on $null.
    if ($groups.Count -eq 0) { return , @() }

    # Accumulate the slices first, then render them. A nested helper would be the obvious
    # way to close each batch, but a function's assignment to $sequence writes a local copy
    # rather than the enclosing variable, and the sequence numbers would all come out 1.
    $slices = [System.Collections.Generic.List[object]]::new()
    $pending = [System.Collections.Generic.List[object]]::new()

    foreach ($items in $groups) {
        $observations = @($items ?? @())
        if ($observations.Count -eq 0) { continue }
        if ($observations.Count -gt 1000) {
            throw "One directory produced $($observations.Count) observations, which exceeds the contract's ceiling of 1000 per batch. Splitting them would disable the server's acl_hash check, and truncating them would look like a shorter ACL. Report this directory: a DACL that large is itself a finding."
        }

        if ($pending.Count -gt 0 -and ($pending.Count + $observations.Count) -gt $BatchSize) {
            $slices.Add($pending.ToArray())
            $pending.Clear()
        }
        foreach ($observation in $observations) { $pending.Add($observation) }
    }
    if ($pending.Count -gt 0) { $slices.Add($pending.ToArray()) }

    $batches = [System.Collections.Generic.List[object]]::new()
    for ($index = 0; $index -lt $slices.Count; $index++) {
        $batches.Add([ordered]@{
                schema_version = $script:AdgSchemaVersion
                run_id         = $RunId
                batch_id       = [guid]::NewGuid().ToString()
                sequence       = $index + 1
                is_final       = ($index -eq ($slices.Count - 1))
                observations   = @($slices[$index])
            })
    }

    return , $batches.ToArray()
}

function Invoke-AdgNtfsScan {
    <#
        .SYNOPSIS
            Read the configured share roots and return contract-shaped scan runs.
        .DESCRIPTION
            Returns one run object per invocation - Start, Batches, Completion, and a
            Summary - or one per share root when -RunPerShareRoot is given.

            Prefer -RunPerShareRoot for an estate of any size. A run that reports any error
            cannot be 'succeeded', so in a single run covering fifty roots one unreadable
            descriptor downgrades the whole run. It reconciles nothing either way in this
            phase, but the status is what an operator reads, and "partial" over fifty roots
            hides which one actually failed.
    #>
    [OutputType([object[]])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Settings,
        [switch] $RunPerShareRoot,
        [string] $CollectorHost = $env:COMPUTERNAME,
        [string] $CollectorVersion = '0.1.0'
    )

    if ([string]::IsNullOrWhiteSpace($CollectorHost)) { $CollectorHost = 'COLLECTOR' }

    # Built by hand rather than as the value of an if-expression: assigning `, @(...)` out of
    # one lets PowerShell unroll the wrapper, and a combined scan then silently behaves as
    # though -RunPerShareRoot had been passed.
    $groups = [System.Collections.Generic.List[object]]::new()
    if ($RunPerShareRoot) {
        foreach ($root in $Settings.ShareRoots) { $groups.Add(@($root)) }
    }
    else {
        $groups.Add(@($Settings.ShareRoots))
    }

    $runs = [System.Collections.Generic.List[object]]::new()
    foreach ($group in $groups) {
        $runs.Add((Invoke-AdgNtfsScanRun -Settings $Settings -ShareRoot @($group) `
                    -CollectorHost $CollectorHost -CollectorVersion $CollectorVersion))
    }
    return , $runs.ToArray()
}

function Invoke-AdgNtfsScanRun {
    <#
        .SYNOPSIS
            Collect one scan run over a set of share roots.
        .DESCRIPTION
            The run's status is decided by what happened, not by what the caller hoped for:
            any recorded error makes it partial, and no root read at all makes it failed,
            because coverage is then unknown rather than merely incomplete.

            reconciled_scopes is empty on every run this collector produces, including a
            clean one, because the run is incremental - see the note at the top of this file
            for why a share-root read cannot honestly reconcile a directory_tree scope.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Settings,
        [Parameter(Mandatory)][string[]] $ShareRoot,
        [string] $CollectorHost = 'COLLECTOR',
        [string] $CollectorVersion = '0.1.0'
    )

    $runId = [guid]::NewGuid().ToString()
    $startedAt = Get-AdgTimestamp

    $groups = [System.Collections.Generic.List[object]]::new()
    $errors = [System.Collections.Generic.List[object]]::new()
    $scopes = [System.Collections.Generic.List[object]]::new()
    $read = [System.Collections.Generic.List[string]]::new()
    $unread = [System.Collections.Generic.List[string]]::new()
    $observationCount = 0

    foreach ($root in $ShareRoot) {
        $result = Get-AdgNtfsResourceObservation -Path $root -Settings $Settings -RunId $runId

        foreach ($item in $result.Errors) { $errors.Add($item) }
        $scopes.Add($result.Scope)

        $observations = @($result.Observations)
        if ($observations.Count -gt 0) {
            # One group per directory, which is what keeps a resource and its ACEs together
            # through batching.
            $groups.Add($observations)
            $observationCount += $observations.Count
        }

        if ($result.Read) { $read.Add($root) } else { $unread.Add($root) }
    }

    $batches = Split-AdgNtfsObservationBatch -RunId $runId -Group $groups.ToArray() -BatchSize $Settings.BatchSize

    $status = if ($read.Count -eq 0) { 'failed' }
    elseif ($errors.Count -gt 0) { 'partial' }
    else { 'succeeded' }

    $start = [ordered]@{
        schema_version = $script:AdgSchemaVersion
        run_id         = $runId
        source         = [ordered]@{
            collector         = 'ntfs'
            collector_host    = $CollectorHost
            method            = 'DirectorySecurity.GetSecurityDescriptorBinaryForm'
            collector_version = $CollectorVersion
            target            = (@($ShareRoot) -join ',')
        }
        started_at     = $startedAt
        scopes         = @($scopes.ToArray())
        # Always. A share-root read enumerates no tree, and the backend refuses to let an
        # incremental run reconcile anything - which is precisely the guarantee wanted here.
        incremental    = $true
    }

    $completion = [ordered]@{
        schema_version    = $script:AdgSchemaVersion
        run_id            = $runId
        status            = $status
        completed_at      = Get-AdgTimestamp
        batch_count       = $batches.Count
        observation_count = $observationCount
        error_count       = $errors.Count
        errors            = @($errors.ToArray())
        reconciled_scopes = @()
    }

    return [pscustomobject]@{
        RunId      = $runId
        Start      = $start
        Batches    = $batches
        Completion = $completion
        Summary    = [pscustomobject]@{
            RunId               = $runId
            Status              = $status
            ShareRootsRequested = @($ShareRoot).Count
            ShareRootsRead      = $read.Count
            ShareRootsUnread    = @($unread.ToArray())
            ObservationCount    = $observationCount
            BatchCount          = $batches.Count
            ErrorCount          = $errors.Count
            ReconciledScopes    = 0
        }
    }
}
