<#
.SYNOPSIS
    Run the ADG backend lint and type checks (ruff + mypy).

.PARAMETER Fix
    Apply ruff's safe autofixes and reformat before checking.
#>
[CmdletBinding()]
param(
    [switch]$Fix
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot 'backend'
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    throw "Backend virtual environment missing. Run .\scripts\bootstrap.ps1 first."
}

$failed = $false

Push-Location $backend
try {
    if ($Fix) {
        Write-Host 'ruff format' -ForegroundColor Cyan
        & $venvPython -m ruff format .
        Write-Host 'ruff check --fix' -ForegroundColor Cyan
        & $venvPython -m ruff check --fix .
    }

    Write-Host 'ruff check' -ForegroundColor Cyan
    & $venvPython -m ruff check .
    if ($LASTEXITCODE -ne 0) { $failed = $true }

    Write-Host 'ruff format --check' -ForegroundColor Cyan
    & $venvPython -m ruff format --check .
    if ($LASTEXITCODE -ne 0) { $failed = $true }

    Write-Host 'mypy' -ForegroundColor Cyan
    & $venvPython -m mypy app tests
    if ($LASTEXITCODE -ne 0) { $failed = $true }
}
finally {
    Pop-Location
}

if ($failed) {
    Write-Host 'Backend checks failed.' -ForegroundColor Red
    exit 1
}

Write-Host 'Backend checks passed.' -ForegroundColor Green
