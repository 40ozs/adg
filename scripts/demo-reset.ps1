<#
.SYNOPSIS
    Reset a Demo Mode database to the canonical Null Meridian estate.

.DESCRIPTION
    Runs `python -m app.demo.null_meridian reset`: migrates the demo database, empties it,
    posts the three Null Meridian scan generations through ADG's own ingestion endpoints
    in-process, evaluates the risk rules, and prints a report whose fingerprint is identical
    on every run.

    Refuses to run unless ADG_RUNTIME_MODE=demo and ADG_DATABASE_URL names a database whose
    name contains 'demo'. Contacts no directory, file server or collector: the estate is
    synthetic. See docs\demo\null-meridian.md.

.PARAMETER DatabaseUrl
    The demo database. Defaults to the local development server's 'adg_demo' database.

.PARAMETER Report
    Also write the reset report as JSON to this path.

.EXAMPLE
    .\scripts\demo-reset.ps1
    .\scripts\demo-reset.ps1 -Report .\.tmp\reset.json
#>
[CmdletBinding()]
param(
    [string] $DatabaseUrl = 'postgresql+psycopg://adg:adg_dev_password@localhost:5432/adg_demo',
    [string] $Report
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot 'backend'
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    throw "Backend virtual environment missing. Run .\scripts\bootstrap.ps1 first."
}

$arguments = @('-m', 'app.demo.null_meridian', 'reset')
if ($Report) { $arguments += @('--report', $Report) }

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
