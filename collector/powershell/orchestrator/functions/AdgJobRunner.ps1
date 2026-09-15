<#
    Running one job: lock it, decide its mode, invoke the collector, retry what a retry can
    fix, and record what happened.

    ------------------------------------------------------------------------------------
    The collector is invoked through a table of script blocks

    Invoke-AdgJob does not know how to scan anything. It is handed an *invoker* - a table
    mapping job kind to a script block - exactly the way the collectors are handed a
    publisher. Two things follow. The orchestrator's own logic is testable without a domain
    controller, a file server or an API, which is what the suite does. And a new job kind is
    a new entry in one table rather than a new branch in the middle of the retry loop.

    ------------------------------------------------------------------------------------
    What a retry may and may not be

    A retried job is a *new run*: a new run_id, a new set of batch ids. That is safe because
    ingestion is keyed on (run_id, source_key) and every write is newest-wins, so a job that
    half-completed and is retried converges on the same state rather than doubling anything.
    What a retry must never do is inherit the failed attempt's checkpoint, because the
    failed attempt did not read everything below it.

    A retry is also only for failures a retry could fix. A configuration error, a rejected
    payload, a scope the server refuses - those fail identically on the second attempt, and
    retrying them three times with backoff turns a clear error into a slow one.
#>

function New-AdgJobInvoker {
    <#
        .SYNOPSIS
            The table Invoke-AdgJob calls to actually collect.
        .DESCRIPTION
            Each script block takes one hashtable - the job, the resolved mode, the
            configuration, the publisher settings - and returns a run outcome. The real
            invokers shell out to the collector entry points; the test suite substitutes its
            own, which is the point of the indirection.
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Config,
        [string] $CollectorRoot
    )

    $root = if ($CollectorRoot) { $CollectorRoot } else { Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }

    $runScript = {
        param([string] $Script, [hashtable] $Arguments)
        & $Script @Arguments
    }

    return @{
        CollectorRoot = $root
        Config        = $Config
        RunScript     = $runScript
        Handlers      = @{
            ad_principals        = { param($Request) Invoke-AdgAdJob -Request $Request -Pass 'principals' }
            ad_memberships       = { param($Request) Invoke-AdgAdJob -Request $Request -Pass 'memberships' }
            smb_inventory        = { param($Request) Invoke-AdgSmbJob -Request $Request }
            ntfs_important_roots = { param($Request) Invoke-AdgNtfsJob -Request $Request -Scoped $true }
            ntfs_deep_scan       = { param($Request) Invoke-AdgNtfsJob -Request $Request -Scoped $false }
            full_reconciliation  = { param($Request) Invoke-AdgReconciliationJob -Request $Request }
        }
    }
}


function New-AdgJobOutcome {
    <#
        .SYNOPSIS
            The record one attempt leaves behind, whether it worked or not.
        .DESCRIPTION
            Always the same shape. A failed attempt that returned nothing would make the
            telemetry of a failing job indistinguishable from the telemetry of a job that
            was never scheduled, and the whole point of the run record is to tell those
            apart.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $JobName,
        [Parameter(Mandatory)][string] $Status,
        [string] $Mode = 'full',
        [string] $RunId,
        [string] $StartedAt,
        [string] $EndedAt,
        [int] $ObservationCount = 0,
        [int] $AffirmationCount = 0,
        [int] $UnchangedCount = 0,
        [int] $RefusedAffirmations = 0,
        [int] $ErrorCount = 0,
        [bool] $Reconciled = $false,
        [int] $DriftAbsent = 0,
        [int] $Attempts = 1,
        [AllowNull()] $Checkpoint = $null,
        [string] $Message,
        [string] $Reason
    )

    return [pscustomobject]@{
        Job                 = $JobName
        Status              = $Status
        Mode                = $Mode
        RunId               = $RunId
        StartedAt           = $StartedAt
        EndedAt             = $EndedAt
        ObservationCount    = $ObservationCount
        AffirmationCount    = $AffirmationCount
        UnchangedCount      = $UnchangedCount
        RefusedAffirmations = $RefusedAffirmations
        ErrorCount          = $ErrorCount
        Reconciled          = $Reconciled
        DriftAbsent         = $DriftAbsent
        Attempts            = $Attempts
        Checkpoint          = $Checkpoint
        Message             = $Message
        Reason              = $Reason
    }
}


function Test-AdgRetryableJobFailure {
    <#
        .SYNOPSIS
            Whether re-running this job could plausibly succeed.
        .DESCRIPTION
            The inverse list is what matters. A payload the server refused with 422, a scope
            it will not let the run reconcile, a configuration that does not parse - all of
            these fail the same way on the second attempt, and retrying them replaces a clear
            error at 02:00 with the same error at 02:07 and again at 02:21.
    #>
    [OutputType([bool])]
    param([Parameter(Mandatory)][AllowEmptyString()][string] $Message)

    $permanent = @(
        'retrying cannot help',
        'is not a job kind',
        'not valid JSON',
        'does not exist',
        'may not reconcile',
        'HTTP 422',
        'HTTP 409',
        'HTTP 401',
        'HTTP 403'
    )
    foreach ($needle in $permanent) {
        if ($Message -like "*$needle*") { return $false }
    }
    return $true
}


function Invoke-AdgJob {
    <#
        .SYNOPSIS
            Run one job once, with retry and backoff, and return its outcome.
        .DESCRIPTION
            Never throws for a collection failure. A scheduled invocation runs six jobs and
            an exception out of the third would leave the last three unrun - so a failure
            becomes an outcome with status 'failed', the loop continues, and the exit code
            at the end says that something failed.

            Only an outright failure is retried. A 'partial' run is a real result on an
            estate with an unreadable corner in it: it collected valid observations,
            reported the errors that stopped it, and reconciled nothing. Retrying it means
            reading the whole scope again to reach the same directory that denied access the
            first time, which costs an hour and changes nothing.

            What it does throw for is a problem with the *orchestrator*: an unreadable
            configuration, a state directory it cannot create. Those are not one job's
            failure and pretending otherwise would run five jobs with no resume points.

        .PARAMETER Sleep
            Supplied so the suite can exercise backoff without waiting for it. The default
            sleeps; a test passes a script block that records the delay instead.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Job,
        [Parameter(Mandatory)][pscustomobject] $Config,
        [Parameter(Mandatory)][hashtable] $Invoker,
        [Parameter(Mandatory)][datetime] $Now,
        [switch] $Force,
        [switch] $IgnoreWindow,
        [switch] $WhatIf,
        [scriptblock] $Sleep = { param($Seconds) Start-Sleep -Seconds $Seconds }
    )

    $stateDirectory = Initialize-AdgStateDirectory -StateDirectory $Config.StateDirectory
    $state = Read-AdgJobState -StateDirectory $stateDirectory -JobName $Job.Name

    $due = Test-AdgJobDue -Job $Job -State $state -Now $Now -Force:$Force -IgnoreWindow:$IgnoreWindow
    if (-not $due.Due) {
        return New-AdgJobOutcome -JobName $Job.Name -Status 'skipped' -Reason $due.Reason
    }

    $plan = Resolve-AdgJobMode -Job $Job -State $state
    if ($WhatIf) {
        return New-AdgJobOutcome -JobName $Job.Name -Status 'planned' -Mode $plan.Mode `
            -Reason ('{0}; would run as {1} because {2}' -f $due.Reason, $plan.Mode, $plan.Reason)
    }

    # Taken before the first attempt and held across all of them: two attempts of one job
    # overlapping would be two runs reading the same checkpoint.
    $lock = Enter-AdgJobLock -StateDirectory $stateDirectory -JobName $Job.Name
    if ($null -eq $lock) {
        return New-AdgJobOutcome -JobName $Job.Name -Status 'skipped' `
            -Reason 'another process is already running this job; a second one would read the same checkpoint and leave whichever finished last as the resume point'
    }

    try {
        $handler = $Invoker.Handlers[$Job.Kind]
        if ($null -eq $handler) {
            return New-AdgJobOutcome -JobName $Job.Name -Status 'failed' `
                -Message "No invoker is registered for job kind '$($Job.Kind)'."
        }

        $outcome = $null
        for ($attempt = 1; $attempt -le $Job.MaxAttempts; $attempt++) {
            $request = @{
                Job           = $Job
                Config        = $Config
                Mode          = $plan.Mode
                Baseline      = $plan.Baseline
                Reconcile     = $Job.Reconcile
                Attempt       = $attempt
                State         = $state
                CollectorRoot = $Invoker.CollectorRoot
                RunScript     = $Invoker.RunScript
            }

            try {
                $result = & $handler $request
                $outcome = ConvertTo-AdgJobOutcome -JobName $Job.Name -Mode $plan.Mode `
                    -Attempt $attempt -Reason $plan.Reason -Result $result
            }
            catch {
                $outcome = New-AdgJobOutcome -JobName $Job.Name -Status 'failed' -Mode $plan.Mode `
                    -Attempts $attempt -Reason $plan.Reason -Message $_.Exception.Message
            }

            # Only an outright failure is retried. A 'partial' run read what it could,
            # reported the errors that stopped it, reconciled nothing, and stored valid
            # observations; re-running it produces the same partial result against the same
            # unreadable corner of the estate, three times, with backoff in between. A
            # 'canceled' run hit its deadline, and the deadline will still be there.
            if ($outcome.Status -ne 'failed') { break }
            if ($attempt -ge $Job.MaxAttempts) { break }
            if (-not (Test-AdgRetryableJobFailure -Message ([string] $outcome.Message))) {
                $outcome = New-AdgJobOutcome -JobName $Job.Name -Status $outcome.Status -Mode $plan.Mode `
                    -Attempts $attempt -Reason $plan.Reason `
                    -Message ("$($outcome.Message) Not retried: re-running would fail identically.")
                break
            }

            $delay = Get-AdgRetryDelaySeconds -Attempt $attempt -BaseSeconds $Job.RetryBaseSeconds
            Write-Warning "Job '$($Job.Name)' attempt $attempt/$($Job.MaxAttempts) failed ($($outcome.Message)); retrying in ${delay}s."
            & $Sleep $delay
        }

        Save-AdgJobState -StateDirectory $stateDirectory -JobName $Job.Name -Outcome $outcome -Previous $state | Out-Null
        return $outcome
    }
    finally {
        Exit-AdgJobLock -Lock $lock
    }
}


function ConvertTo-AdgJobOutcome {
    <#
        .SYNOPSIS
            Normalize whatever an invoker returned into a run outcome.
        .DESCRIPTION
            An invoker returns the collector's own summary, and the collectors do not all
            report the same fields. Normalizing here rather than making each collector
            produce an orchestrator shape keeps the collectors usable on their own, which is
            how every one of them is documented and how an operator debugs one.

            A result with no status at all is treated as a failure. An invoker that returned
            something unrecognizable has not demonstrated that it collected anything, and
            reading silence as success is the mistake this whole codebase is written against.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $JobName,
        [Parameter(Mandatory)][string] $Mode,
        [Parameter(Mandatory)][int] $Attempt,
        [string] $Reason,
        [AllowNull()] $Result
    )

    if ($null -eq $Result) {
        return New-AdgJobOutcome -JobName $JobName -Status 'failed' -Mode $Mode -Attempts $Attempt `
            -Reason $Reason -Message 'The collector returned no run summary, so nothing demonstrates that it collected anything.'
    }

    $status = [string] (Get-AdgConfigProperty $Result 'Status' '')
    if ([string]::IsNullOrWhiteSpace($status)) {
        return New-AdgJobOutcome -JobName $JobName -Status 'failed' -Mode $Mode -Attempts $Attempt `
            -Reason $Reason -Message 'The collector returned a summary with no status.'
    }

    $checkpoint = Get-AdgConfigProperty $Result 'Checkpoint' $null
    return New-AdgJobOutcome -JobName $JobName -Status $status -Mode $Mode -Attempts $Attempt `
        -Reason $Reason `
        -RunId ([string] (Get-AdgConfigProperty $Result 'RunId' '')) `
        -StartedAt ([string] (Get-AdgConfigProperty $Result 'StartedAt' '')) `
        -EndedAt ([string] (Get-AdgConfigProperty $Result 'EndedAt' '')) `
        -ObservationCount ([int] (Get-AdgConfigProperty $Result 'ObservationCount' 0)) `
        -AffirmationCount ([int] (Get-AdgConfigProperty $Result 'AffirmationCount' 0)) `
        -UnchangedCount ([int] (Get-AdgConfigProperty $Result 'UnchangedCount' 0)) `
        -RefusedAffirmations ([int] (Get-AdgConfigProperty $Result 'RefusedAffirmations' 0)) `
        -ErrorCount ([int] (Get-AdgConfigProperty $Result 'ErrorCount' 0)) `
        -Reconciled ([bool] (Get-AdgConfigProperty $Result 'Reconciled' $false)) `
        -DriftAbsent ([int] (Get-AdgConfigProperty $Result 'DriftAbsent' 0)) `
        -Checkpoint $checkpoint `
        -Message ([string] (Get-AdgConfigProperty $Result 'Message' ''))
}


function Invoke-AdgCollectionRun {
    <#
        .SYNOPSIS
            Run every job that is due, in order, and report all of them.
        .DESCRIPTION
            Jobs are independent, so one failing never stops the next. The caller gets every
            outcome and decides the exit code; a scheduled task that stopped at the first
            failure would leave the estate partly collected and say only that one thing went
            wrong.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Config,
        [Parameter(Mandatory)][hashtable] $Invoker,
        [Parameter(Mandatory)][datetime] $Now,
        [string[]] $JobName = @(),
        [switch] $Force,
        [switch] $IgnoreWindow,
        [switch] $WhatIf,
        [scriptblock] $Sleep = { param($Seconds) Start-Sleep -Seconds $Seconds }
    )

    $selected = if (@($JobName).Count -gt 0) {
        $wanted = [System.Collections.Generic.HashSet[string]]::new(
            [string[]] @($JobName), [System.StringComparer]::OrdinalIgnoreCase)
        $matched = @($Config.Jobs | Where-Object { $wanted.Contains($_.Name) })
        $missing = @($JobName | Where-Object { -not ($Config.Jobs.Name -contains $_) })
        if ($missing.Count -gt 0) {
            throw "No job named $($missing -join ', ') is configured. The configured jobs are: $(($Config.Jobs.Name | Sort-Object) -join ', ')."
        }
        $matched
    }
    else {
        @($Config.Jobs)
    }

    $outcomes = [System.Collections.Generic.List[object]]::new()
    foreach ($job in $selected) {
        $outcomes.Add((Invoke-AdgJob -Job $job -Config $Config -Invoker $Invoker -Now $Now `
                    -Force:$Force -IgnoreWindow:$IgnoreWindow -WhatIf:$WhatIf -Sleep $Sleep))
    }

    $all = $outcomes.ToArray()
    return [pscustomobject]@{
        StartedAt = $Now.ToUniversalTime().ToString('o')
        Outcomes  = $all
        Succeeded = @($all | Where-Object { $_.Status -eq 'succeeded' }).Count
        Failed    = @($all | Where-Object { $_.Status -in @('failed', 'partial') }).Count
        Skipped   = @($all | Where-Object { $_.Status -eq 'skipped' }).Count
        Planned   = @($all | Where-Object { $_.Status -eq 'planned' }).Count
    }
}
