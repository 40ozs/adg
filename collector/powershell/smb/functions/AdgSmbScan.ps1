<#
    Orchestration layer: walk the configured servers and assemble a contract-shaped run.

    The scan reads everything first and builds the payloads afterwards. Two things follow
    from that, and both are deliberate:

      * the declared scopes can name the shares that actually exist, which a start
        envelope sent before enumeration could only guess at;
      * a dry run produces exactly the bytes a real run would POST, so the test suite can
        validate the collector's output against the published schemas.

    It also means the run is held in memory. That is comfortable at share granularity -
    an estate of a thousand shares is a few thousand observations - and will not be for
    the NTFS walk in Phase 2B, which must stream.

    ------------------------------------------------------------------------------------
    Scopes, and why the filter changes them

    A `server` scope claims "every share on this server"; a `share` scope claims "this
    share's definition and its ACL". Reconciling a scope is what lets the backend mark
    unseen objects absent, so a scope must never claim more than the run read.

    That is why an excluded share is still reported as a share by default: if the run
    claimed the `server` scope while silently omitting C$, reconciliation would mark C$
    deleted. Excluding a share means "do not read its ACL", and no `share` scope is
    declared for it, so its unread ACL is never mistaken for an empty one - which would
    read as "nobody has access", the exact inversion the contract warns about.

    Set RecordExcludedShares to $false to omit excluded shares entirely. The `server`
    scope is then not declared for that host, because the run no longer enumerates it
    completely, and deletions on that host stop being detectable. That is the trade, and
    it is the caller's to make.
#>

function Get-AdgSmbServerObservation {
    <#
        .SYNOPSIS
            Collect one server: its identity, its shares, and their share ACLs.
        .DESCRIPTION
            Returns a hashtable with Observations, Errors, Scopes, Reached, and
            ExcludedCount. Never throws for a condition a real estate produces: an
            unreachable host, an unreadable ACL, and an untranslatable trustee are all
            recorded as collector errors and the scan moves on.

            Connection failures are retried with a bounded backoff so that one dead server
            costs a few seconds rather than a wedged scan. A failure to read one share's
            ACL is not retried - it is almost always a permission problem, and retrying an
            access denial just spends time arriving at the same answer.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [Parameter(Mandatory)][pscustomobject] $Settings,
        [Parameter(Mandatory)][string] $RunId,
        [AllowNull()][pscredential] $Credential
    )

    $observations = [System.Collections.Generic.List[object]]::new()
    $errors = [System.Collections.Generic.List[object]]::new()
    $scopes = [System.Collections.Generic.List[object]]::new()
    $hostKey = $ComputerName.ToLowerInvariant()
    $excluded = 0

    $session = $null
    $shares = $null

    # --- Reach the server ---------------------------------------------------------------
    $attempts = [Math]::Max(1, $Settings.RetryCount + 1)
    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        try {
            $session = New-AdgSmbSession -ComputerName $ComputerName -Protocol $Settings.Protocol `
                -TimeoutSeconds $Settings.TimeoutSeconds -Credential $Credential
            $shares = Get-AdgRemoteShare -Session $session
            break
        }
        catch {
            Remove-AdgSmbSession -Session $session
            $session = $null

            if ($attempt -ge $attempts) {
                $errors.Add((New-AdgCollectorError -Code 'rpc_unavailable' -Target $ComputerName `
                            -Message "$ComputerName could not be enumerated after $attempts attempt(s): $($_.Exception.Message)"))
                # No server observation. An observation asserts the object was seen, and a
                # host that never answered was not seen - it is unobserved, not empty.
                return @{
                    Observations  = @()
                    Errors        = $errors.ToArray()
                    Scopes        = @([ordered]@{ kind = 'server'; key = $hostKey })
                    Reached       = $false
                    ExcludedCount = 0
                }
            }

            Write-Warning "$ComputerName did not answer (attempt $attempt/$attempts): $($_.Exception.Message)"
            if ($Settings.RetryDelaySeconds -gt 0) {
                Start-Sleep -Seconds ($Settings.RetryDelaySeconds * $attempt)
            }
        }
    }

    try {
        # --- The server itself ----------------------------------------------------------
        $facts = @{ DnsHostName = $null; NetbiosName = $null; IsDomainMember = $null; OperatingSystem = $null }
        try {
            $facts = Get-AdgRemoteComputerFact -Session $session
        }
        catch {
            # Descriptive metadata. Its loss does not make the share inventory wrong, so it
            # is recorded and the scan continues - but it does make the run partial.
            $errors.Add((New-AdgCollectorError -Code 'lookup_failed' -Target $ComputerName `
                        -Message "Host identity facts were unreadable on $ComputerName; shares were still enumerated. $($_.Exception.Message)"))
        }

        $observations.Add((ConvertTo-AdgServerObservation -RunId $RunId -Name $ComputerName `
                    -DnsHostName $facts.DnsHostName -NetbiosName $facts.NetbiosName `
                    -IsDomainMember $facts.IsDomainMember -OperatingSystem $facts.OperatingSystem))

        # --- Its shares -----------------------------------------------------------------
        foreach ($share in ($shares ?? @())) {
            $shareName = [string] (Get-AdgProperty $share 'Name')
            $shareType = ConvertTo-AdgShareType (Get-AdgProperty $share 'ShareType')
            $rawSpecial = Get-AdgProperty $share 'Special'
            $isSpecial = if ($null -eq $rawSpecial) { $null } else { [bool] $rawSpecial }

            $verdict = Test-AdgShareIncluded -ShareName $shareName -ShareType $shareType -IsSpecial $isSpecial `
                -Include $Settings.Include -Exclude $Settings.Exclude `
                -IncludeAdminShares:$Settings.IncludeAdminShares `
                -IncludeHiddenShares:$Settings.IncludeHiddenShares `
                -IncludeNonDiskShares:$Settings.IncludeNonDiskShares

            if (-not $verdict.Included) {
                $excluded++
                Write-Verbose "Skipping the ACL of \\$ComputerName\$shareName ($($verdict.Reason))."
                if ($Settings.RecordExcludedShares) {
                    # The share exists; that much was observed. Only its ACL was not read,
                    # and no share scope is declared for it, so nothing will mistake the
                    # missing ACEs for an empty ACL.
                    $observations.Add((ConvertTo-AdgShareObservation -RunId $RunId -ServerName $ComputerName -Share $share))
                }
                continue
            }

            $observations.Add((ConvertTo-AdgShareObservation -RunId $RunId -ServerName $ComputerName -Share $share))

            $acl = Read-AdgShareAcl -Session $session -RunId $RunId -ServerName $ComputerName `
                -Share $share -AclMethod $Settings.AclMethod

            foreach ($item in $acl.Observations) { $observations.Add($item) }
            foreach ($item in $acl.Principals) { $observations.Add($item) }
            foreach ($item in $acl.Errors) { $errors.Add($item) }

            if ($acl.Complete) {
                $scopes.Add([ordered]@{ kind = 'share'; key = "$hostKey|$($shareName.ToLowerInvariant())" })
            }
        }

        # The server scope claims every share on the host was enumerated. That is true
        # whenever excluded shares were still recorded, and false when they were dropped.
        if ($Settings.RecordExcludedShares -or $excluded -eq 0) {
            $scopes.Add([ordered]@{ kind = 'server'; key = $hostKey })
        }

        return @{
            Observations  = $observations.ToArray()
            Errors        = $errors.ToArray()
            Scopes        = $scopes.ToArray()
            Reached       = $true
            ExcludedCount = $excluded
        }
    }
    finally {
        Remove-AdgSmbSession -Session $session
    }
}

function Read-AdgShareAcl {
    <#
        .SYNOPSIS
            Read one share's ACL, preferring the descriptor over the permission levels.
        .DESCRIPTION
            The descriptor is preferred because it stores SIDs: a trustee that no longer
            resolves to a name still produces a usable ACE. The level API reports names,
            so an unresolvable trustee there cannot be reported at all - the contract
            identifies a trustee only by SID.

            Returns Observations, Principals, Errors, and Complete. Complete is false
            whenever any part of the ACL could not be recorded; the caller uses it to
            decide whether the share's scope may be declared, which is what keeps a
            partially read ACL from ever being reconciled.
        .PARAMETER AclMethod
            Auto prefers the descriptor and falls back on failure. Descriptor and Access
            pin one method, which is the setting to use when a mixed estate would
            otherwise make the same ACL alternate between mask form and level form
            between runs - the two forms are legitimately different observations, and
            alternating between them churns the history for no reason.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)] $Session,
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $ServerName,
        [Parameter(Mandatory)] $Share,
        [ValidateSet('Auto', 'Descriptor', 'Access')][string] $AclMethod = 'Auto'
    )

    $shareName = [string] (Get-AdgProperty $Share 'Name')
    $unc = Get-AdgShareUncPath -ServerName $ServerName -ShareName $shareName
    $errors = [System.Collections.Generic.List[object]]::new()

    if ($AclMethod -ne 'Access') {
        try {
            $security = Get-AdgRemoteShareSecurity -Session $Session -ShareName $shareName

            if (-not $security.DaclPresent) {
                # A NULL DACL grants everyone full share access. Reporting it as zero ACEs
                # would read as "nobody has access" - the exact opposite - and contract v1
                # has no field for it on a share, so it is raised loudly instead.
                $errors.Add((New-AdgCollectorError -Code 'null_share_dacl' -Target $unc `
                            -Message "$unc has a NULL share DACL, which grants every authenticated user full share access. Contract v1 cannot express this on a share observation, so no ACE was reported; treat this as a finding."))
                return @{ Observations = @(); Principals = @(); Errors = $errors.ToArray(); Complete = $false }
            }

            $converted = ConvertTo-AdgShareAceObservation -RunId $RunId -ServerName $ServerName `
                -ShareName $shareName -Dacl $security.Dacl
            foreach ($item in $converted.Errors) { $errors.Add($item) }

            return @{
                Observations = $converted.Observations
                Principals   = $converted.Principals
                Errors       = $errors.ToArray()
                Complete     = ($errors.Count -eq 0)
            }
        }
        catch {
            if ($AclMethod -eq 'Descriptor') {
                $errors.Add((New-AdgCollectorError -Code 'access_denied' -Target $unc `
                            -Message "The share security descriptor for $unc could not be read: $($_.Exception.Message)"))
                return @{ Observations = @(); Principals = @(); Errors = $errors.ToArray(); Complete = $false }
            }
            Write-Verbose "The descriptor for $unc was unreadable; falling back to permission levels. $($_.Exception.Message)"
        }
    }

    try {
        $access = Get-AdgRemoteShareAccess -Session $Session -ShareName $shareName
        $converted = ConvertTo-AdgShareAccessObservation -RunId $RunId -ServerName $ServerName `
            -ShareName $shareName -Access $access
        foreach ($item in $converted.Errors) { $errors.Add($item) }

        return @{
            Observations = $converted.Observations
            Principals   = $converted.Principals
            Errors       = $errors.ToArray()
            Complete     = ($errors.Count -eq 0)
        }
    }
    catch {
        $errors.Add((New-AdgCollectorError -Code 'access_denied' -Target $unc `
                    -Message "The share ACL for $unc could not be read by either method: $($_.Exception.Message)"))
        return @{ Observations = @(); Principals = @(); Errors = $errors.ToArray(); Complete = $false }
    }
}

function Split-AdgObservationBatch {
    <#
        .SYNOPSIS
            Chunk observations into contract-sized batches, one source key per batch.
        .DESCRIPTION
            Order is preserved, so a share's defining observation stays in the same batch
            as its ACEs or an earlier one, which keeps partial data interpretable. Each
            batch gets a UUID generated once and reused on every retry - that is what
            makes a retry after a timeout a recognized duplicate instead of a second
            application.

            **One source key, once per batch.** The contract forbids a repeat and the API
            rejects the whole batch with a 422, and this collector produces one in the
            ordinary case: an orphaned SID is orphaned estate-wide, so it sits on the ACL of
            several shares of one server, and every one of them reports a `principal`
            observation describing it. Those are the same fact about the same SID, so the
            repeat is dropped rather than sent - the server stores one row for it either way,
            and sending it would make the batch unsendable.

            The case was found by walking a generated tree through the NTFS collector, which
            carried the identical defect. This one had shipped since Phase 2A: no fixture had
            two shares sharing an unresolved trustee.

            Across batches a repeat is not a repeat. The server keys observations by
            (run_id, source_key) and ignores the second arrival, so the set is cleared with
            each batch rather than held for the run.
    #>
    [OutputType([object[]])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [AllowNull()][object[]] $Observations,
        [ValidateRange(1, 1000)][int] $BatchSize = 500
    )

    $items = @($Observations ?? @())
    # The comma operator keeps an empty result an empty array. A bare `return @()` unrolls
    # to nothing, and under strict mode the caller's .Count then fails on $null.
    if ($items.Count -eq 0) { return , @() }

    $batches = [System.Collections.Generic.List[object]]::new()
    $pending = [System.Collections.Generic.List[object]]::new()
    $keys = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    $sequence = 1

    foreach ($item in $items) {
        $key = [string] $item['source_key']
        # A missing key is malformed, and the API rejects it naming the field. Kept rather
        # than dropped: deduplicating on a key that is not there would discard every
        # observation after the first, which is a far worse failure than the 422 that is
        # coming anyway.
        if (-not [string]::IsNullOrEmpty($key) -and -not $keys.Add($key)) { continue }

        $pending.Add($item)
        if ($pending.Count -ge $BatchSize) {
            $batches.Add([ordered]@{
                    schema_version = $script:AdgSchemaVersion
                    run_id         = $RunId
                    batch_id       = [guid]::NewGuid().ToString()
                    sequence       = $sequence
                    is_final       = $false
                    observations   = @($pending.ToArray())
                })
            $sequence++
            $pending.Clear()
            $keys.Clear()
        }
    }

    if ($pending.Count -gt 0) {
        $batches.Add([ordered]@{
                schema_version = $script:AdgSchemaVersion
                run_id         = $RunId
                batch_id       = [guid]::NewGuid().ToString()
                sequence       = $sequence
                is_final       = $false
                observations   = @($pending.ToArray())
            })
    }

    # Marked at the end rather than predicted inside the loop. How many batches there are is
    # not known until the deduplication has finished, so a flag computed from the length of
    # the input can land on the wrong one.
    if ($batches.Count -gt 0) { $batches[$batches.Count - 1].is_final = $true }

    return , $batches.ToArray()
}

function Invoke-AdgSmbScan {
    <#
        .SYNOPSIS
            Scan the configured servers and return a contract-shaped scan run.
        .DESCRIPTION
            Returns one run object per invocation - Start, Batches, Completion, and a
            Summary - or one per server when -RunPerServer is given.

            Prefer -RunPerServer for an estate of any size. Reconciliation is refused for
            a run that reported any error, so in a single run covering fifty servers, one
            unreachable host prevents every other host's shares from ever being marked
            absent. Per-server runs contain that blast radius: the forty-nine healthy
            servers still reconcile.

        .PARAMETER Settings
            A settings object from Import-AdgSmbTarget.

        .PARAMETER RunPerServer
            Emit a separate scan run per target server, each with its own run_id.
    #>
    [OutputType([object[]])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Settings,
        [AllowNull()][pscredential] $Credential,
        [switch] $RunPerServer,
        [string] $CollectorHost = $env:COMPUTERNAME,
        [string] $CollectorVersion = '0.1.0'
    )

    if ([string]::IsNullOrWhiteSpace($CollectorHost)) { $CollectorHost = 'COLLECTOR' }

    # Built by hand rather than as the value of an if-expression: assigning `, @(...)` out
    # of one lets PowerShell unroll the wrapper, and a combined scan then silently behaves
    # as though -RunPerServer had been passed.
    $groups = [System.Collections.Generic.List[object]]::new()
    if ($RunPerServer) {
        foreach ($server in $Settings.Servers) { $groups.Add(@($server)) }
    }
    else {
        $groups.Add(@($Settings.Servers))
    }

    $runs = [System.Collections.Generic.List[object]]::new()
    foreach ($group in $groups) {
        $runs.Add((Invoke-AdgSmbScanRun -Settings $Settings -Servers @($group) -Credential $Credential `
                    -CollectorHost $CollectorHost -CollectorVersion $CollectorVersion))
    }
    return , $runs.ToArray()
}

function Invoke-AdgSmbScanRun {
    <#
        .SYNOPSIS
            Collect one scan run over a set of servers.
        .DESCRIPTION
            The run's status is decided by what happened, not by what the caller hoped
            for: any recorded error makes it partial, and no server reached at all makes
            it failed, because coverage is then unknown rather than merely incomplete.
            Reconciliation is offered only for a clean, complete run, which is the single
            mechanism that keeps a bad scan from deleting shares that are still there.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Settings,
        [Parameter(Mandatory)][string[]] $Servers,
        [AllowNull()][pscredential] $Credential,
        [string] $CollectorHost = 'COLLECTOR',
        [string] $CollectorVersion = '0.1.0'
    )

    $runId = [guid]::NewGuid().ToString()
    $startedAt = Get-AdgTimestamp

    $observations = [System.Collections.Generic.List[object]]::new()
    $errors = [System.Collections.Generic.List[object]]::new()
    $scopes = [System.Collections.Generic.List[object]]::new()
    $reachable = [System.Collections.Generic.List[string]]::new()
    $unreachable = [System.Collections.Generic.List[string]]::new()
    $excludedTotal = 0

    foreach ($server in $Servers) {
        $result = Get-AdgSmbServerObservation -ComputerName $server -Settings $Settings -RunId $runId -Credential $Credential

        foreach ($item in $result.Observations) { $observations.Add($item) }
        foreach ($item in $result.Errors) { $errors.Add($item) }
        foreach ($item in $result.Scopes) { $scopes.Add($item) }
        $excludedTotal += [int] $result.ExcludedCount

        if ($result.Reached) { $reachable.Add($server) } else { $unreachable.Add($server) }
    }

    $batches = Split-AdgObservationBatch -RunId $runId -Observations $observations.ToArray() -BatchSize $Settings.BatchSize

    $status = if ($reachable.Count -eq 0) { 'failed' }
    elseif ($errors.Count -gt 0) { 'partial' }
    else { 'succeeded' }

    # A run reporting any error cannot be 'succeeded', and only a 'succeeded' run may
    # reconcile. An unreachable server therefore cannot cause anything to be marked
    # absent - which is the whole point: unseen is not gone.
    $reconciled = if ($status -eq 'succeeded') { $scopes.ToArray() } else { @() }

    $start = [ordered]@{
        schema_version = $script:AdgSchemaVersion
        run_id         = $runId
        source         = [ordered]@{
            collector         = 'smb'
            collector_host    = $CollectorHost
            method            = if ($Settings.AclMethod -eq 'Access') { 'Get-SmbShareAccess' } else { 'Win32_LogicalShareSecuritySetting.GetSecurityDescriptor' }
            collector_version = $CollectorVersion
            target            = (@($Servers) -join ',')
        }
        started_at     = $startedAt
        scopes         = @($scopes.ToArray())
        incremental    = $false
    }

    # scopes must be non-empty. A run whose every target was unreachable still declares
    # what it set out to enumerate; it simply reconciles none of it.
    if ($start.scopes.Count -eq 0) {
        $start.scopes = @($Servers | ForEach-Object { [ordered]@{ kind = 'server'; key = $_.ToLowerInvariant() } })
    }

    $completion = [ordered]@{
        schema_version    = $script:AdgSchemaVersion
        run_id            = $runId
        status            = $status
        completed_at      = Get-AdgTimestamp
        batch_count       = $batches.Count
        # What the batches actually carry, not what the scan accumulated. The two differ
        # whenever a source key repeating across shares was deduplicated, and a run claiming
        # more coverage than it delivered overstates what ADG knows - which is exactly what
        # the output validator refuses.
        observation_count = [int] (@($batches | ForEach-Object { @($_.observations).Count } |
                Measure-Object -Sum).Sum)
        error_count       = $errors.Count
        errors            = @($errors.ToArray())
        reconciled_scopes = @($reconciled)
    }

    return [pscustomobject]@{
        RunId      = $runId
        Start      = $start
        Batches    = $batches
        Completion = $completion
        Summary    = [pscustomobject]@{
            RunId              = $runId
            Status             = $status
            ServersRequested   = @($Servers).Count
            ServersReached     = $reachable.Count
            ServersUnreachable = @($unreachable.ToArray())
            ObservationCount   = $observations.Count
            BatchCount         = $batches.Count
            ErrorCount         = $errors.Count
            SharesExcluded     = $excludedTotal
            ReconciledScopes   = @($reconciled).Count
        }
    }
}
