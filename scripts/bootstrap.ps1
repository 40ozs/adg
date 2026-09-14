<#
.SYNOPSIS
    Install ADG development dependencies on a Windows workstation.

.DESCRIPTION
    Creates the backend virtual environment, installs backend and frontend dependencies,
    and seeds a local .env from .env.example when one does not already exist.
    Existing .env files are never overwritten.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot 'backend'
$frontend = Join-Path $repoRoot 'frontend'

function Assert-Tool {
    param([string]$Name, [string]$InstallHint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required tool '$Name' was not found on PATH. $InstallHint"
    }
}

Assert-Tool -Name 'python' -InstallHint 'Install Python 3.11 or newer from python.org.'
Assert-Tool -Name 'npm' -InstallHint 'Install Node.js 20 or newer from nodejs.org.'

Write-Host 'Creating the backend virtual environment...' -ForegroundColor Cyan
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    python -m venv (Join-Path $backend '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the backend virtual environment.' }
}

Write-Host 'Installing backend dependencies...' -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw 'Failed to upgrade pip.' }
& $venvPython -m pip install --quiet -e "$backend[dev]"
if ($LASTEXITCODE -ne 0) { throw 'Failed to install backend dependencies.' }

Write-Host 'Installing frontend dependencies...' -ForegroundColor Cyan
Push-Location $frontend
try {
    npm install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'npm install failed.' }
}
finally {
    Pop-Location
}

$envFile = Join-Path $repoRoot '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $repoRoot '.env.example') $envFile
    Write-Host "Created $envFile from .env.example. Review it before starting the stack." -ForegroundColor Yellow
}
else {
    Write-Host "$envFile already exists; leaving it unchanged." -ForegroundColor DarkGray
}

Write-Host 'Bootstrap complete.' -ForegroundColor Green
Write-Host '  Backend tests : .\scripts\backend-test.ps1'
Write-Host '  Backend checks: .\scripts\backend-lint.ps1'
Write-Host '  Frontend check: .\scripts\frontend-check.ps1'
Write-Host '  Start stack   : .\scripts\stack-up.ps1'
