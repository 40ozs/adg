<#
.SYNOPSIS
    Stop the ADG local development stack.

.PARAMETER RemoveVolumes
    Also delete the PostgreSQL data volume. This destroys local collected data and is
    never the default.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [switch]$RemoveVolumes
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot

$arguments = @('compose', 'down')
if ($RemoveVolumes) {
    if (-not $PSCmdlet.ShouldProcess('adg_pgdata', 'Delete the local PostgreSQL volume')) {
        return
    }
    $arguments += '--volumes'
}

Push-Location $repoRoot
try {
    docker @arguments
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($exitCode -ne 0) {
    throw "docker compose down failed with exit code $exitCode."
}

Write-Host 'Stack stopped.' -ForegroundColor Green
