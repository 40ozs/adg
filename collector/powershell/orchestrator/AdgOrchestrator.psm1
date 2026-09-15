<#
    ADG collection orchestrator.

    Six independent jobs, each with its own schedule, its own state, its own lock and its
    own checkpoint, driven by one entry point a Windows scheduled task can call. It does not
    collect anything itself: it decides *what should run now*, *what mode it should run in*,
    and *what to remember afterwards*, and hands the collecting to the collectors.

    The layering is the same one the collectors use, for the same reason - the parts that
    can be wrong are the parts that can be tested without a domain:

        AdgJobConfig.ps1         the job catalogue, and what a configuration may say
        AdgJobState.ps1          per-job state, the atomic write, and the run lock
        AdgSchedule.ps1          due-ness, mode resolution, backoff (all pure)
        AdgJobRunner.ps1         one job: lock, retry, record
        AdgCollectorInvokers.ps1 the only code that knows what a collector is

    ------------------------------------------------------------------------------------
    The one rule everything here is arranged around

    A cursor that lags the data costs a re-read. A cursor that leads the data costs an audit
    that is quietly missing whatever fell in the gap - and nothing downstream can detect it,
    because a skipped object produces no error, no gap, and no smaller number that looks
    wrong. Every refusal in these files exists to keep the error on the first side.

    See docs/architecture/incremental-collection.md.
#>

Set-StrictMode -Version Latest

$functionRoot = Join-Path $PSScriptRoot 'functions'

$files = @(
    'AdgJobConfig.ps1'
    'AdgJobState.ps1'
    'AdgSchedule.ps1'
    'AdgCollectorInvokers.ps1'
    'AdgJobRunner.ps1'
)

foreach ($file in $files) {
    $path = Join-Path $functionRoot $file
    if (-not (Test-Path -LiteralPath $path)) {
        throw "The ADG orchestrator is incomplete: $path is missing."
    }
    . $path
}

Export-ModuleMember -Function @(
    # Configuration
    'Get-AdgJobKinds'
    'Get-AdgJobKind'
    'ConvertFrom-AdgDuration'
    'ConvertTo-AdgTimeOfDay'
    'New-AdgJobDefinition'
    'Get-AdgConfigProperty'
    'Import-AdgOrchestratorConfig'
    # State
    'Get-AdgJobStatePath'
    'Get-AdgJobLockPath'
    'Initialize-AdgStateDirectory'
    'Read-AdgJobState'
    'ConvertTo-AdgInstant'
    'Save-AdgJobState'
    'Format-AdgInstant'
    'Enter-AdgJobLock'
    'Exit-AdgJobLock'
    # Scheduling
    'Test-AdgInWindow'
    'Test-AdgJobDue'
    'Format-AdgElapsed'
    'Resolve-AdgJobMode'
    'Get-AdgRetryDelaySeconds'
    # Running
    'New-AdgJobInvoker'
    'New-AdgJobOutcome'
    'Test-AdgRetryableJobFailure'
    'Invoke-AdgJob'
    'ConvertTo-AdgJobOutcome'
    'Invoke-AdgCollectionRun'
    # Collector handlers
    'Import-AdgCollectorModule'
    'Get-AdgJobCollectorConfigPath'
    'Invoke-AdgAdJob'
    'Invoke-AdgSmbJob'
    'Invoke-AdgNtfsJob'
    'Invoke-AdgReconciliationJob'
)
