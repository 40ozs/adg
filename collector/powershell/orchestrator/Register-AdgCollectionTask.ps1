#Requires -Version 7.0
<#
.SYNOPSIS
    Register (or print) the single Windows scheduled task that drives ADG collection.

.DESCRIPTION
    One task, on a short interval, calling Invoke-AdgCollection.ps1. The cadence lives in
    the orchestrator configuration, not in the task: six tasks with six schedules would put
    it in two places and the two would drift, and the first symptom of that drift is a job
    that quietly stops running.

    **Printing is the default.** Without -Register this writes the task XML to standard
    output and changes nothing, so the command can be reviewed, committed, or handed to
    whoever owns change control on the collector host. Registering a scheduled task is a
    change to a production machine, and this script does not make one because it was run.

.PARAMETER ConfigPath
    The orchestrator configuration the task will pass to Invoke-AdgCollection.ps1.

.PARAMETER TaskName
    Defaults to 'ADG Collection'.

.PARAMETER IntervalMinutes
    How often the task asks "what is due?". This is not how often anything is collected -
    each job's own interval decides that - so a short value costs almost nothing: a run with
    nothing due exits in milliseconds. Default 15.

.PARAMETER UserName
    The account the task runs as. Omit to be prompted by Register-ScheduledTask, or to run
    as SYSTEM with -UseSystemAccount.

.PARAMETER UseSystemAccount
    Run as SYSTEM. Convenient and usually wrong: SYSTEM on a member server authenticates to
    the rest of the domain as the *computer account*, so the shares and directories the
    collector can read become whatever that computer happens to have been granted. A
    dedicated, least-privileged domain account with read access is the documented
    arrangement - see docs/collectors/ad.md - and it is also the one an auditor can explain.

.PARAMETER Register
    Actually create the task. Without it, the XML is printed and nothing is changed.

.PARAMETER Force
    Replace an existing task of the same name.

.EXAMPLE
    .\Register-AdgCollectionTask.ps1 -ConfigPath C:\ProgramData\ADG\adg-orchestrator.json

.EXAMPLE
    .\Register-AdgCollectionTask.ps1 -ConfigPath C:\ProgramData\ADG\adg-orchestrator.json `
        -UserName CORP\svc-adg-collector -Register

.NOTES
    Registering needs administrative rights on the collector host. It needs none in the
    domain and none on any file server: this creates a scheduled task, and every collector
    it starts is read-only (ADR-0004).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $ConfigPath,
    [string] $TaskName = 'ADG Collection',
    [ValidateRange(1, 1440)][int] $IntervalMinutes = 15,
    [string] $UserName,
    [switch] $UseSystemAccount,
    [switch] $Register,
    [switch] $Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $PSCommandPath
$entryPoint = Join-Path $scriptRoot 'Invoke-AdgCollection.ps1'

if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
    throw "The orchestrator entry point '$entryPoint' is missing."
}
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "The orchestrator configuration '$ConfigPath' does not exist. Create it before registering a task that would fail every $IntervalMinutes minutes."
}

# Validated before anything is registered. A task that runs every fifteen minutes and fails
# every time on a typo is worse than no task: it produces an alert stream nobody reads, and
# under it the estate simply stops being collected.
Import-Module (Join-Path $scriptRoot 'AdgOrchestrator.psd1') -Force
$config = Import-AdgOrchestratorConfig -Path $ConfigPath
Write-Host "Configuration is valid: $($config.Jobs.Count) job(s), state in $($config.StateDirectory)." -ForegroundColor Green

$pwsh = (Get-Process -Id $PID).Path
if (-not $pwsh) { $pwsh = 'pwsh.exe' }

# -File, and every argument separately quoted. The argument string is what Task Scheduler
# hands to the shell verbatim, so a path containing a space becomes two arguments unless the
# quotes are in the string itself.
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -ConfigPath "{1}"' -f $entryPoint, $ConfigPath

Write-Host ''
Write-Host 'Task definition' -ForegroundColor White
Write-Host "  name      : $TaskName"
Write-Host "  program   : $pwsh"
Write-Host "  arguments : $arguments"
Write-Host "  trigger   : every $IntervalMinutes minute(s), indefinitely, starting at boot + 2 minutes"
Write-Host "  account   : $(if ($UseSystemAccount) { 'SYSTEM' } elseif ($UserName) { $UserName } else { '(prompted)' })"

if ($UseSystemAccount) {
    Write-Warning 'Running as SYSTEM authenticates to the rest of the domain as this computer account, so what the collector can read becomes whatever that computer was granted. A dedicated least-privileged domain account is the documented arrangement (docs/collectors/ad.md).'
}

if (-not $Register) {
    Write-Host ''
    Write-Host 'Nothing was changed. The equivalent schtasks command is:' -ForegroundColor Cyan
    Write-Host ''
    Write-Host ('  schtasks /Create /TN "{0}" /TR "\"{1}\" {2}" /SC MINUTE /MO {3} /RL LIMITED /F' -f `
            $TaskName, $pwsh, ($arguments -replace '"', '\"'), $IntervalMinutes)
    Write-Host ''
    Write-Host 'Re-run with -Register to create it, or -Register -Force to replace an existing task.' -ForegroundColor Cyan
    exit 0
}

if (-not (Get-Command -Name Register-ScheduledTask -ErrorAction SilentlyContinue)) {
    throw 'The ScheduledTasks module is not available on this host, so the task cannot be registered from here. Use the schtasks command printed by running this script without -Register.'
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and -not $Force) {
    throw "A scheduled task named '$TaskName' already exists. Pass -Force to replace it, or choose another -TaskName. Replacing it silently would discard whatever schedule or account somebody set on it."
}

$action = New-ScheduledTaskAction -Execute $pwsh -Argument $arguments -WorkingDirectory $scriptRoot
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date.AddMinutes(2)) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

# StartWhenAvailable, because a collector host that was switched off overnight should catch
# up rather than wait for the next window. MultipleInstances IgnoreNew, because two
# orchestrator runs at once would fight over the same job locks -- they would not corrupt
# anything, the locks see to that, but every job would be skipped by one of them and the
# logs would be twice as long and half as informative.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 12) `
    -DontStopOnIdleEnd -RestartCount 0

$principalArguments = if ($UseSystemAccount) {
    @{ UserId = 'SYSTEM'; LogonType = 'ServiceAccount'; RunLevel = 'Limited' }
}
elseif ($UserName) {
    @{ UserId = $UserName; LogonType = 'Password'; RunLevel = 'Limited' }
}
else {
    @{ UserId = "$env:USERDOMAIN\$env:USERNAME"; LogonType = 'S4U'; RunLevel = 'Limited' }
}
$principal = New-ScheduledTaskPrincipal @principalArguments

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force:$Force -Description `
    'Runs the ADG collection jobs that are due. Read-only; the cadence lives in the orchestrator configuration.' | Out-Null

Write-Host ''
Write-Host "Registered '$TaskName'." -ForegroundColor Green
Write-Host "Verify with: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
