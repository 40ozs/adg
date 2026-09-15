@{
    RootModule           = 'AdgOrchestrator.psm1'
    ModuleVersion        = '0.1.0'
    GUID                 = 'b7f2c1a4-5d63-4f08-9a21-3e7c0d48b6f5'
    Author               = 'ADG'
    CompanyName          = 'ADG'
    Copyright            = 'ADG'
    Description          = 'Schedules and runs the ADG collection jobs: when each is due, whether it may resume from a checkpoint, and what it may reconcile.'

    # PowerShell 7 per the project baseline. Nothing here needs Windows PowerShell 5.1: the
    # orchestrator itself touches only the file system and the clock, and the collectors it
    # invokes state their own requirements.
    PowerShellVersion    = '7.2'
    CompatiblePSEditions = @('Core')

    # No RequiredModules. The collector modules are imported by path at run time, from the
    # tree beside this one, so the orchestrator can be tested with none of them present --
    # which is how the suite exercises every scheduling and checkpoint rule without a domain
    # controller or a file server.

    FunctionsToExport    = @(
        'Get-AdgJobKinds'
        'Get-AdgJobKind'
        'ConvertFrom-AdgDuration'
        'ConvertTo-AdgTimeOfDay'
        'New-AdgJobDefinition'
        'Get-AdgConfigProperty'
        'Import-AdgOrchestratorConfig'
        'Get-AdgJobStatePath'
        'Get-AdgJobLockPath'
        'Initialize-AdgStateDirectory'
        'Read-AdgJobState'
        'ConvertTo-AdgInstant'
        'Save-AdgJobState'
        'Format-AdgInstant'
        'Enter-AdgJobLock'
        'Exit-AdgJobLock'
        'Test-AdgInWindow'
        'Test-AdgJobDue'
        'Format-AdgElapsed'
        'Resolve-AdgJobMode'
        'Get-AdgRetryDelaySeconds'
        'New-AdgJobInvoker'
        'New-AdgJobOutcome'
        'Test-AdgRetryableJobFailure'
        'Invoke-AdgJob'
        'ConvertTo-AdgJobOutcome'
        'Invoke-AdgCollectionRun'
        'Import-AdgCollectorModule'
        'Get-AdgJobCollectorConfigPath'
        'Invoke-AdgAdJob'
        'Invoke-AdgSmbJob'
        'Invoke-AdgNtfsJob'
        'Invoke-AdgReconciliationJob'
    )
    CmdletsToExport      = @()
    VariablesToExport    = @()
    AliasesToExport      = @()
    PrivateData          = @{
        PSData = @{
            Tags       = @('ADG', 'Collector', 'Scheduling', 'Incremental')
            ProjectUri = 'https://adg.local'
        }
    }
}
