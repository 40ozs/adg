<#
.SYNOPSIS
    Start the ADG local development stack (PostgreSQL, API, web).

.DESCRIPTION
    Windows collectors are not part of this stack; they run natively on Windows hosts.

.PARAMETER DbOnly
    Start only PostgreSQL, for developers running the API and frontend natively.

.PARAMETER Build
    Rebuild the API and web images before starting.
#>
[CmdletBinding()]
param(
    [switch]$DbOnly,
    [switch]$Build
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker was not found on PATH. Install Docker Desktop for Windows.'
}

docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'The Docker daemon is not responding. Start Docker Desktop and try again.'
}

$envFile = Join-Path $repoRoot '.env'
if (-not (Test-Path $envFile)) {
    Write-Host 'No .env found; compose will use the development defaults.' -ForegroundColor DarkGray
}

$arguments = @('compose', 'up', '-d')
if ($Build) { $arguments += '--build' }
if ($DbOnly) { $arguments += 'db' }

Push-Location $repoRoot
try {
    docker @arguments
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($exitCode -ne 0) {
    throw "docker compose up failed with exit code $exitCode."
}

Write-Host 'Stack started.' -ForegroundColor Green
if (-not $DbOnly) {
    Write-Host '  API : http://localhost:8000/health/ready'
    Write-Host '  Web : http://localhost:3000/status'
}
else {
    Write-Host '  PostgreSQL: localhost:5432'
}
