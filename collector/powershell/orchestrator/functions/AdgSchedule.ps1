<#
    When a job is due, and what mode it should run in.

    Both decisions are pure functions of (job, saved state, clock). That is deliberate:
    scheduling is the part of an orchestrator that is hardest to observe in production -
    nobody is watching at 02:00 - and the only way to know it is right is to be able to ask
    it, with a clock you supply, without running anything.

    ------------------------------------------------------------------------------------
    Every decision comes back with its reason

    Test-AdgJobDue returns a verdict *and a sentence*. An operator running the entrypoint
    with -ValidateOnly gets "not due: last ran 12m ago, interval 15m" rather than silence,
    and the silence is what makes a mis-scheduled job invisible: a job that never runs and
    a job that runs and finds nothing look identical from outside.
#>

function Test-AdgInWindow {
    <#
        .SYNOPSIS
            Whether a local time of day falls inside a job's onlyBetween window.
        .DESCRIPTION
            A window that wraps midnight - 22:00 to 06:00, the usual one - is the normal
            case and not an edge case, so it is handled by the comparison rather than by a
            caller remembering to special-case it.
    #>
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][AllowNull()] $WindowStart,
        [Parameter(Mandatory)][AllowNull()] $WindowEnd,
        [Parameter(Mandatory)][datetime] $Now
    )

    if ($null -eq $WindowStart -or $null -eq $WindowEnd) { return $true }

    $moment = $Now.TimeOfDay
    if ($WindowStart -lt $WindowEnd) {
        return ($moment -ge $WindowStart -and $moment -lt $WindowEnd)
    }
    return ($moment -ge $WindowStart -or $moment -lt $WindowEnd)
}


function Test-AdgJobDue {
    <#
        .SYNOPSIS
            Whether a job should run now, and why.
        .PARAMETER Now
            Supplied rather than read, so that the schedule can be tested against a clock.
        .PARAMETER Force
            Run regardless of the interval. The window is still honored unless -IgnoreWindow
            is given as well: "run it now" and "run it now even though the estate is in
            business hours" are different instructions, and conflating them is how an
            operator triggers a deep scan across a production file server at 10am.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Job,
        [AllowNull()] $State,
        [Parameter(Mandatory)][datetime] $Now,
        [switch] $Force,
        [switch] $IgnoreWindow
    )

    if (-not $Job.Enabled -and -not $Force) {
        return [pscustomobject]@{ Due = $false; Reason = "job '$($Job.Name)' is disabled" }
    }

    if (-not $IgnoreWindow -and -not (Test-AdgInWindow -WindowStart $Job.WindowStart -WindowEnd $Job.WindowEnd -Now $Now)) {
        $window = '{0:hh\:mm}-{1:hh\:mm}' -f $Job.WindowStart, $Job.WindowEnd
        return [pscustomobject]@{
            Due    = $false
            Reason = "outside the $window window (local time is $($Now.ToString('HH:mm')))"
        }
    }

    if ($Force) {
        return [pscustomobject]@{ Due = $true; Reason = 'forced' }
    }

    $last = if ($null -ne $State) { $State.LastEndedAt } else { $null }
    if ($null -eq $last) {
        return [pscustomobject]@{ Due = $true; Reason = 'has never completed here' }
    }

    $elapsed = $Now.ToUniversalTime() - $last
    if ($elapsed -ge $Job.Every) {
        return [pscustomobject]@{
            Due    = $true
            Reason = ('last completed {0} ago, interval {1}' -f (Format-AdgElapsed $elapsed), (Format-AdgElapsed $Job.Every))
        }
    }
    return [pscustomobject]@{
        Due    = $false
        Reason = ('last completed {0} ago, interval {1}' -f (Format-AdgElapsed $elapsed), (Format-AdgElapsed $Job.Every))
    }
}


function Format-AdgElapsed {
    <#
        .SYNOPSIS
            A TimeSpan in the same units the configuration is written in.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][timespan] $Span)

    if ($Span.TotalDays -ge 1) { return ('{0:0.#}d' -f $Span.TotalDays) }
    if ($Span.TotalHours -ge 1) { return ('{0:0.#}h' -f $Span.TotalHours) }
    if ($Span.TotalMinutes -ge 1) { return ('{0:0}m' -f $Span.TotalMinutes) }
    return ('{0:0}s' -f $Span.TotalSeconds)
}


function Resolve-AdgJobMode {
    <#
        .SYNOPSIS
            Whether this run is a delta, a full read, or a reconciliation - and why.
        .DESCRIPTION
            'auto' resolves to a delta only when every one of these holds:

            * the job's kind has a source that publishes change metadata at all;
            * a checkpoint was saved, by a run that succeeded;
            * the checkpoint's issuer matches the one this run will read from.

            The last is the one that bites in practice. A uSNChanged watermark is a counter
            on one domain controller, so binding a different DC - or the same DC after a
            restore from backup, which reissues numbers it has already handed out - makes it
            meaningless. Resuming anyway would skip every object whose USN on the new server
            falls below the old number, permanently and invisibly. So a mismatch resolves to
            a full read, which costs one expensive scan and loses nothing.

        .PARAMETER Issuer
            The issuer this run will read from, when it is already known. Omit it when the
            collector has not bound its source yet; the collector re-checks before using the
            watermark, and this is then advisory.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Job,
        [AllowNull()] $State,
        [string] $Issuer
    )

    # The repair pass, not merely a job that reconciles. smb_inventory and ntfs_deep_scan
    # reconcile as part of ordinary collection and are `full` runs; calling them
    # 'reconcile' would leave an operator unable to tell a scheduled repair from a routine
    # scan when the drift number is non-zero, which is the only thing the distinction is for.
    if ($Job.IsRepairPass) {
        return [pscustomobject]@{
            Mode = 'reconcile'; Baseline = $null
            Reason = 'this is the repair pass: it reads everything and records what the deltas could not see'
        }
    }
    if ($Job.Strategy -eq 'full') {
        return [pscustomobject]@{ Mode = 'full'; Baseline = $null; Reason = "strategy is 'full'" }
    }
    if (-not $Job.SupportsDelta) {
        return [pscustomobject]@{
            Mode = 'full'; Baseline = $null
            Reason = "a $($Job.Kind) source publishes no change metadata a delta could filter on"
        }
    }

    $checkpoint = if ($null -ne $State) { $State.Checkpoint } else { $null }
    if ($null -eq $checkpoint -or [string]::IsNullOrWhiteSpace($checkpoint.Token)) {
        $reason = 'no checkpoint has been recorded, so there is no point to resume from'
        if ($Job.Strategy -eq 'delta') {
            throw "Job '$($Job.Name)' is configured strategy='delta' but $reason. Run it once as 'full', or set strategy='auto' so the first run reads everything and later ones resume."
        }
        return [pscustomobject]@{ Mode = 'full'; Baseline = $null; Reason = $reason }
    }

    if ($Issuer -and $checkpoint.Issuer -and $checkpoint.Issuer -ine $Issuer) {
        return [pscustomobject]@{
            Mode = 'full'; Baseline = $null
            Reason = "the saved checkpoint was issued by '$($checkpoint.Issuer)' and this run reads from '$Issuer'; a per-server cursor does not transfer, so this run reads everything"
        }
    }

    if ($null -ne $State -and $State.LastStatus -and $State.LastStatus -ne 'succeeded') {
        return [pscustomobject]@{
            Mode = 'full'; Baseline = $null
            Reason = "the last run finished as '$($State.LastStatus)', so the saved checkpoint does not mark a point everything below was read"
        }
    }

    return [pscustomobject]@{
        Mode     = 'delta'
        Baseline = $checkpoint
        Reason   = "resuming from $($checkpoint.Kind) cursor $($checkpoint.Token) issued by '$($checkpoint.Issuer)'"
    }
}


function Get-AdgRetryDelaySeconds {
    <#
        .SYNOPSIS
            Exponential backoff with jitter, capped.
        .DESCRIPTION
            Jitter is not decoration. A site running the same schedule on twenty collector
            hosts recovering from one API restart would otherwise retry in lockstep and
            restart the outage they are recovering from.
    #>
    [OutputType([double])]
    param(
        [Parameter(Mandatory)][int] $Attempt,
        [Parameter(Mandatory)][int] $BaseSeconds,
        [int] $CapSeconds = 300,
        [double] $Jitter = -1
    )

    $delay = [double] $BaseSeconds * [Math]::Pow(2, [Math]::Max(0, $Attempt - 1))
    $delay = [Math]::Min($delay, $CapSeconds)
    $fraction = if ($Jitter -ge 0) { $Jitter } else { Get-Random -Minimum 0.0 -Maximum 1.0 }
    # Full jitter over the top quarter of the window: enough spread to break a herd,
    # not so much that a retry lands before the source has had time to recover.
    return [Math]::Round($delay * (0.75 + 0.25 * $fraction), 2)
}
