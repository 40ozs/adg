<#
    Orchestration layer: read one host's local groups and send them as one run.

    One run per host, always. A `local_groups_host` scope claims "every local group on this
    host and every one of their members", and reconciling it is what lets the backend record a
    membership as removed. One unreachable host in a combined run would stop every other host
    from ever reconciling, so hosts are never combined.

    The scope is claimed only when it was earned. The run reconciles its host only when it
    enumerated every local group and read every group's members without one error; anything
    less finishes the run as `partial` and reconciles nothing, so a permission problem shows
    up as an incomplete scan rather than as members that silently disappeared.

    Domain controllers are refused. A domain controller has no local account database:
    NetLocalGroupEnum there returns the domain's own BUILTIN and domain-local groups, which
    are the directory collector's to report and which, recorded as one host's local groups,
    would be scoped to the wrong boundary.
#>


function Get-AdgLocalGroupHostObservation {
    <#
        .SYNOPSIS
            Read one host: every local group and its direct members.
        .DESCRIPTION
            Returns a hashtable with Observations, Errors and Complete. Never throws for a
            condition a real estate produces: an unreachable host, an access denial, a group
            that vanished between enumeration and reading are all collector errors, and
            Complete is $false whenever any of them occurred.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [Parameter(Mandatory)][string] $RunId
    )

    $observations = [System.Collections.Generic.List[object]]::new()
    $errors = [System.Collections.Generic.List[object]]::new()
    $hostKey = $ComputerName.ToLowerInvariant()

    try {
        $server = Get-AdgRemoteServerInfo -ComputerName $ComputerName
    }
    catch {
        $errors.Add((New-AdgCollectorError -Code (Get-AdgLocalGroupErrorCode $_) -Target $ComputerName `
                    -Message "Could not reach $ComputerName to read its local groups: $($_.Exception.Message)"))
        return @{ Observations = @(); Errors = $errors.ToArray(); Complete = $false }
    }
    if ($server.IsDomainController) {
        $errors.Add((New-AdgCollectorError -Code 'domain_controller' -Target $ComputerName `
                    -Message "$ComputerName is a domain controller. It has no local account database; its groups are the directory collector's to report, and nothing was read."))
        return @{ Observations = @(); Errors = $errors.ToArray(); Complete = $false }
    }

    try {
        $groups = @(Get-AdgRemoteLocalGroup -ComputerName $ComputerName)
    }
    catch {
        $errors.Add((New-AdgCollectorError -Code (Get-AdgLocalGroupErrorCode $_) -Target $ComputerName `
                    -Message "Could not enumerate the local groups of ${ComputerName}: $($_.Exception.Message)"))
        return @{ Observations = @(); Errors = $errors.ToArray(); Complete = $false }
    }

    foreach ($group in $groups) {
        $target = "\\$ComputerName\$($group.Name)"
        try {
            $account = Resolve-AdgRemoteAccountName -ComputerName $ComputerName -Name $group.Name
        }
        catch {
            $errors.Add((New-AdgCollectorError -Code 'lookup_failed' -Target $target `
                        -Message "Could not resolve the local group $($group.Name) to a SID on ${ComputerName}: $($_.Exception.Message)"))
            continue
        }
        if (-not (Test-AdgHostLocalGroupAccount -Account $account -HostNetBiosName $server.Name)) {
            $errors.Add((New-AdgCollectorError -Code 'lookup_failed' -Target $target `
                        -Message "The local group $($group.Name) on $ComputerName resolved to $($account.Domain) ($($account.Sid), SID type $($account.SidUsage)), which is not one of this host's own groups; it is not recorded."))
            continue
        }
        try {
            $members = @(Get-AdgRemoteLocalGroupMember -ComputerName $ComputerName -GroupName $group.Name)
        }
        catch {
            $errors.Add((New-AdgCollectorError -Code (Get-AdgLocalGroupErrorCode $_) -Target $target `
                        -Message "Could not read the members of $($group.Name) on ${ComputerName}: $($_.Exception.Message)"))
            continue
        }
        $translated = ConvertTo-AdgLocalGroupObservation -RunId $RunId -HostKey $hostKey `
            -GroupSid $account.Sid -GroupName $group.Name -Members $members -ObservedAt (Get-AdgTimestamp)
        foreach ($item in $translated.Observations) { $observations.Add($item) }
        foreach ($item in $translated.Errors) { $errors.Add($item) }
    }

    return @{
        Observations = $observations.ToArray()
        Errors       = $errors.ToArray()
        Complete     = ($errors.Count -eq 0)
    }
}


function Get-AdgLocalGroupErrorCode {
    <#
        .SYNOPSIS
            A stable code for a failed network-management call, from its Win32 status.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][System.Management.Automation.ErrorRecord] $ErrorRecord)

    $exception = $ErrorRecord.Exception
    while ($null -ne $exception) {
        if ($exception -is [System.ComponentModel.Win32Exception]) {
            switch ($exception.NativeErrorCode) {
                5 { return 'access_denied' }
                53 { return 'unreachable' }
                1722 { return 'unreachable' }
                2220 { return 'not_found' }
            }
            return 'enumeration_failed'
        }
        $exception = $exception.InnerException
    }
    return 'enumeration_failed'
}


function Invoke-AdgLocalGroupHostRun {
    <#
        .SYNOPSIS
            Read one host and publish it as one complete run: start, batches, completion.
        .DESCRIPTION
            The host is read before the run starts, so the start envelope declares exactly
            the scope the run then claims or declines, and an offline run writes the same
            bytes an online one would send. Returns a summary.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [Parameter(Mandatory)][hashtable] $Publisher,
        [string] $CollectorHost = [System.Net.Dns]::GetHostName(),
        [string] $CollectorVersion = '0.1.0',
        [string] $RunId = (New-AdgIdentifier)
    )

    $startedAt = Get-AdgTimestamp
    $reading = Get-AdgLocalGroupHostObservation -ComputerName $ComputerName -RunId $RunId
    $scope = New-AdgScope -Kind 'local_groups_host' -Key $ComputerName

    $start = New-AdgScanRunStart -RunId $RunId -Collector 'local_groups' -CollectorHost $CollectorHost `
        -Method 'NetLocalGroupGetMembers' -CollectorVersion $CollectorVersion -Target $ComputerName `
        -StartedAt $startedAt -Scopes @($scope)
    Publish-AdgPayload -Publisher $Publisher -PayloadKind 'start' -Payload $start | Out-Null

    $observations = @($reading.Observations)
    $size = Get-AdgMaxBatchSize
    $batchCount = 0
    for ($offset = 0; $offset -lt $observations.Count; $offset += $size) {
        $batchCount++
        $last = [Math]::Min($offset + $size, $observations.Count) - 1
        $batch = New-AdgObservationBatch -RunId $RunId -BatchId (New-AdgIdentifier) -Sequence $batchCount `
            -Observations @($observations[$offset..$last]) -IsFinal ($last -eq $observations.Count - 1)
        Publish-AdgPayload -Publisher $Publisher -PayloadKind 'batch' -Payload $batch | Out-Null
    }

    $errors = @($reading.Errors)
    $status = if ($reading.Complete) { 'succeeded' } elseif ($observations.Count -gt 0) { 'partial' } else { 'failed' }
    # Assigned rather than taken from an if: an empty array returned from a statement
    # collapses to $null, and a completion must carry an empty list, not a missing one.
    $reconciled = @()
    if ($reading.Complete) { $reconciled = @($scope) }
    $completion = New-AdgScanRunCompletion -RunId $RunId -Status $status -CompletedAt (Get-AdgTimestamp) `
        -BatchCount $batchCount -ObservationCount $observations.Count -Errors $errors -ReconciledScopes $reconciled
    Publish-AdgPayload -Publisher $Publisher -PayloadKind 'completion' -Payload $completion | Out-Null

    return [pscustomobject]@{
        ComputerName = $ComputerName
        RunId        = $RunId
        Status       = $status
        Reconciled   = $reading.Complete
        Observations = $observations.Count
        Batches      = $batchCount
        Errors       = $errors.Count
    }
}
