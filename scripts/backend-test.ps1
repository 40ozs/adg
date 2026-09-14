<#
.SYNOPSIS
    Run the ADG backend test suite.

.PARAMETER Smoke
    Also run the tests marked 'smoke', which require the local development stack
    (PostgreSQL reachable at ADG_DATABASE_URL).

.EXAMPLE
    .\scripts\backend-test.ps1
    .\scripts\backend-test.ps1 -Smoke
#>
[CmdletBinding()]
param(
    [switch]$Smoke,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot 'backend'
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    throw "Backend virtual environment missing. Run .\scripts\bootstrap.ps1 first."
}

$arguments = @('-m', 'pytest', '-q')
if ($Smoke) {
    $env:ADG_RUN_SMOKE_TESTS = '1'
    Write-Host 'Including smoke tests (requires a running PostgreSQL).' -ForegroundColor Yellow
}
else {
    $env:ADG_RUN_SMOKE_TESTS = '0'
    $arguments += @('-m', 'not smoke')
}
if ($PytestArgs) {
    $arguments += $PytestArgs
}

Push-Location $backend
try {
    & $venvPython @arguments
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode
