#Requires -Version 7.0
<#
.SYNOPSIS
    Run every ADG collection job that is due, and record what each of them did.

.DESCRIPTION
    One entry point for the whole cadence. Point a single Windows scheduled task at it on a
    short interval - every fifteen minutes is a reasonable default - and let the
    configuration decide what actually runs: each job has its own interval, and a job that is
    not due is skipped in milliseconds.

    That arrangement is deliberate and it is what the phase's requirement to be
    "scheduler-friendly, without requiring a particular scheduler" comes to. Six scheduled
    tasks with six schedules would put the cadence in two places - the tasks and the
    configuration file - and the two would drift. One task that asks "what is due?" puts it
    in one, and works identically under Task Scheduler, a service wrapper, cron on a
    management host, or an operator typing the command.

    Nothing here collects. It decides what should run, in what mode, hands the work to the
    collectors, and records the outcome; see docs/architecture/incremental-collection.md.

.PARAMETER ConfigPath
    The orchestrator configuration. Copy adg-orchestrator.example.json and edit it.

.PARAMETER JobName
    Run only these jobs, by name. They are still subject to their schedule unless -Force is
    given, so naming a job is "consider this one" rather than "run this one".

.PARAMETER Force
    Ignore each job's interval. The onlyBetween window is still honored: "run it now" and
    "run it now even though the estate is inside business hours" are different instructions,
    and a deep scan started across a production file server at 10am is the reason they are
    not the same switch. Add -IgnoreWindow when that is genuinely what you mean.

.PARAMETER IgnoreWindow
    Also ignore the onlyBetween window.

.PARAMETER WhatIf
    Report what would run, in what mode, and why - and change nothing. Nothing is locked, no
    collector is invoked, and no state is written.

.PARAMETER ValidateOnly
    Load and validate the configuration, print the job table, and exit. Intended for the
    change that installs a new configuration: a scheduled task running at 02:00 has nobody
    to read its errors.

.PARAMETER CollectorRoot
    Where the collector tree lives. Defaults to the directory above this one, which is where
    it sits in a normal install.

.PARAMETER Quiet
    Suppress the per-job progress lines. The summary is still printed.

.EXAMPLE
    .\Invoke-AdgCollection.ps1 -ConfigPath C:\ProgramData\ADG\adg-orchestrator.json

.EXAMPLE
    .\Invoke-AdgCollection.ps1 -ConfigPath C:\ProgramData\ADG\adg-orchestrator.json -WhatIf

.EXAMPLE
    .\Invoke-AdgCollection.ps1 -ConfigPath ... -JobName ntfs-deep-scan -Force -IgnoreWindow

.NOTES
    Exit codes, chosen so a scheduled task can alert on the right thing:

        0  every job that ran succeeded (or nothing was due)
        1  at least one job failed outright
        2  at least one job finished partial: its observations are valid, its coverage is not
        3  the configuration could not be loaded, so nothing ran

    2 is not 1 on purpose. A partial run collected real data and reconciled nothing, which is
    the system working correctly on an estate that has an unreadable corner in it; alerting
    on it as a failure teaches an operator to ignore the alert.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $ConfigPath,
    [string[]] $JobName = @(),
    [switch] $Force,
    [switch] $IgnoreWindow,
    [switch] $WhatIf,
    [switch] $ValidateOnly,
    [string] $CollectorRoot,
    [switch] $Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $PSCommandPath
Import-Module (Join-Path $scriptRoot 'AdgOrchestrator.psd1') -Force

function Write-AdgLine {
    param([string] $Text, [string] $Color = 'Gray')
    if (-not $Quiet) { Write-Host $Text -ForegroundColor $Color }
}

$config = try {
    Import-AdgOrchestratorConfig -Path $ConfigPath
}
catch {
    # Exit 3 rather than 1: nothing ran at all, which is a different thing to alert on from
    # a job that ran and failed. An operator who cannot tell those apart will look for a
    # broken domain controller when the problem is a comma.
    #
    # Written to the error stream directly rather than with Write-Error, which under a
    # caller's $ErrorActionPreference = 'Stop' becomes terminating and never reaches the
    # exit below -- so the process would end with whatever code the exception produced
    # instead of the one documented here.
    [Console]::Error.WriteLine(
        "The orchestrator configuration could not be loaded: $($_.Exception.Message)")
    exit 3
}

$root = if ($CollectorRoot) { $CollectorRoot } else { Split-Path -Parent $scriptRoot }

Write-AdgLine "ADG collection orchestrator on $($config.CollectorHost)" 'White'
Write-AdgLine "  configuration : $($config.Path)"
Write-AdgLine "  state         : $($config.StateDirectory)"
Write-AdgLine "  api           : $(if ($config.ApiBaseUrl) { $config.ApiBaseUrl } else { '(each collector configuration names its own)' })"
Write-AdgLine "  jobs          : $($config.Jobs.Count)"

if ($ValidateOnly) {
    Write-Host ''
    Write-Host 'Configuration is valid. Jobs:' -ForegroundColor Green
    foreach ($job in $config.Jobs) {
        $window = if ($null -ne $job.WindowStart) {
            ' window {0:hh\:mm}-{1:hh\:mm}' -f $job.WindowStart, $job.WindowEnd
        }
        else { '' }
        Write-Host ("  {0,-24} {1,-22} every {2,-6} strategy {3,-6}{4}{5}" -f `
                $job.Name, $job.Kind, (Format-AdgElapsed $job.Every), $job.Strategy, `
            $(if ($job.Reconcile) { ' reconciles' } else { '' }), $window)
        if (-not $job.Enabled) { Write-Host '      (disabled)' -ForegroundColor DarkGray }
    }
    exit 0
}

$invoker = New-AdgJobInvoker -Config $config -CollectorRoot $root
$now = Get-Date

$run = Invoke-AdgCollectionRun -Config $config -Invoker $invoker -Now $now `
    -JobName $JobName -Force:$Force -IgnoreWindow:$IgnoreWindow -WhatIf:$WhatIf

Write-Host ''
foreach ($outcome in $run.Outcomes) {
    $color = switch ($outcome.Status) {
        'succeeded' { 'Green' }
        'partial' { 'Yellow' }
        'failed' { 'Red' }
        'planned' { 'Cyan' }
        default { 'DarkGray' }
    }
    $detail = switch ($outcome.Status) {
        'skipped' { $outcome.Reason }
        'planned' { $outcome.Reason }
        default {
            $parts = @("$($outcome.ObservationCount) observation(s)")
            if ($outcome.AffirmationCount -gt 0) { $parts += "$($outcome.AffirmationCount) affirmed" }
            if ($outcome.RefusedAffirmations -gt 0) { $parts += "$($outcome.RefusedAffirmations) affirmation(s) refused" }
            if ($outcome.ErrorCount -gt 0) { $parts += "$($outcome.ErrorCount) error(s)" }
            if ($outcome.Reconciled) { $parts += 'reconciled' }
            if ($outcome.DriftAbsent -gt 0) { $parts += "$($outcome.DriftAbsent) object(s) marked absent" }
            if ($outcome.Attempts -gt 1) { $parts += "after $($outcome.Attempts) attempt(s)" }
            if ($outcome.Message) { $parts += $outcome.Message }
            $parts -join ', '
        }
    }
    Write-Host ("{0,-24} {1,-10} {2}" -f $outcome.Job, $outcome.Status, $detail) -ForegroundColor $color
}

Write-Host ''
Write-Host ("{0} succeeded, {1} failed or partial, {2} skipped{3}." -f `
        $run.Succeeded, $run.Failed, $run.Skipped, `
    $(if ($run.Planned -gt 0) { ", $($run.Planned) planned" } else { '' }))

if ($WhatIf) { exit 0 }

$failed = @($run.Outcomes | Where-Object { $_.Status -eq 'failed' })
$partial = @($run.Outcomes | Where-Object { $_.Status -eq 'partial' })
if ($failed.Count -gt 0) { exit 1 }
if ($partial.Count -gt 0) { exit 2 }
exit 0
