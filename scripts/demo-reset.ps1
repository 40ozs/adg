<#
.SYNOPSIS
    Reset a Demo Mode database to the canonical Genesee Press estate.

.DESCRIPTION
    Runs `python -m app.demo_mode reset`, the one Demo Mode reset: migrates the demo
    database, checks it is positively a demo database (named with 'demo' as a word, and
    either empty or carrying a deployment_marker row that says demo), empties it, posts the
    three Genesee Press scan generations through ADG's own ingestion endpoints in-process,
    evaluates the risk rules, checks the canonical objects, and prints a report whose
    fingerprint is identical on every run.

    Contacts no directory, file server or collector: the estate is synthetic. See
    docs\architecture\demo-mode.md and docs\demo\genesee-press.md.

.PARAMETER DatabaseUrl
    The demo database. Defaults to the local development server's 'adg_demo' database.

.PARAMETER Report
    Also write the reset report as JSON to this path.

.PARAMETER Generations
    Apply only the first N scan generations (an earlier point of the history), so that
    `python -m app.demo_mode scan` can replay the rest. Default: the canonical baseline.

.EXAMPLE
    .\scripts\demo-reset.ps1
    .\scripts\demo-reset.ps1 -Report .\.tmp\reset.json
#>
[CmdletBinding()]
param(
    [string] $DatabaseUrl = 'postgresql+psycopg://adg:adg_dev_password@localhost:5432/adg_demo',
    [string] $Report,
    [int] $Generations = 0
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot 'backend'
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    throw "Backend virtual environment missing. Run .\scripts\bootstrap.ps1 first."
}

$arguments = @('-m', 'app.demo_mode', 'reset')
if ($Report) { $arguments += @('--report', $Report) }
if ($Generations -gt 0) { $arguments += @('--generations', $Generations) }

$previousMode = $env:ADG_RUNTIME_MODE
$previousUrl = $env:ADG_DATABASE_URL
try {
    $env:ADG_RUNTIME_MODE = 'demo'
    $env:ADG_DATABASE_URL = $DatabaseUrl
    Push-Location $backend
    try {
        & $venvPython @arguments
        if ($LASTEXITCODE -ne 0) { throw "The demo reset exited with code $LASTEXITCODE." }
    }
    finally { Pop-Location }
}
finally {
    $env:ADG_RUNTIME_MODE = $previousMode
    $env:ADG_DATABASE_URL = $previousUrl
}
