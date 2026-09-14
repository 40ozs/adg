<#
.SYNOPSIS
    Run the ADG frontend checks: lint, type check, unit tests, and production build.

.PARAMETER SkipBuild
    Skip the Next.js production build (the slowest step).
#>
[CmdletBinding()]
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $repoRoot 'frontend'

if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
    throw "Frontend dependencies missing. Run .\scripts\bootstrap.ps1 first."
}

$steps = @('lint', 'typecheck', 'test')
if (-not $SkipBuild) {
    $steps += 'build'
}

$failed = @()

Push-Location $frontend
try {
    foreach ($step in $steps) {
        Write-Host "npm run $step" -ForegroundColor Cyan
        npm run $step
        if ($LASTEXITCODE -ne 0) { $failed += $step }
    }
}
finally {
    Pop-Location
}

if ($failed.Count -gt 0) {
    Write-Host "Frontend checks failed: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}

Write-Host 'Frontend checks passed.' -ForegroundColor Green
