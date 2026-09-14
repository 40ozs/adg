<#
.SYNOPSIS
    Run the ADG PowerShell collector test suites (Pester).

.DESCRIPTION
    Collector tests are Pester tests under collector/powershell/**/tests. They mock every
    function that touches a remote host, so the whole suite runs on a workstation with no
    domain, no file server, and no network.

.PARAMETER Path
    Restrict the run to one path (a directory or a single .Tests.ps1 file).

.PARAMETER Detailed
    Show per-test output instead of the default summary.

.EXAMPLE
    .\scripts\collector-test.ps1
    .\scripts\collector-test.ps1 -Path collector\powershell\smb\tests\AdgSmbScan.Tests.ps1 -Detailed
#>
[CmdletBinding()]
param(
    [string] $Path,
    [switch] $Detailed
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot

$pester = Get-Module -ListAvailable Pester |
    Where-Object { $_.Version.Major -ge 5 } |
    Sort-Object Version -Descending |
    Select-Object -First 1

if (-not $pester) {
    throw 'Pester 5 or later is required. Install it with: Install-Module Pester -MinimumVersion 5.0 -Scope CurrentUser -Force'
}

Import-Module $pester.Path -Force

$target = if ($Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $repoRoot $Path }
}
else {
    Join-Path $repoRoot 'collector'
}

if (-not (Test-Path -LiteralPath $target)) {
    throw "Test path '$target' does not exist."
}

$configuration = New-PesterConfiguration
$configuration.Run.Path = $target
$configuration.Run.PassThru = $true
$configuration.Output.Verbosity = if ($Detailed) { 'Detailed' } else { 'Normal' }
# A suite that silently finds no tests passes, which is indistinguishable from a suite
# that passes. Fail instead.
$configuration.Run.Throw = $false

Write-Host "Running collector tests under $target (Pester $($pester.Version))"
$result = Invoke-Pester -Configuration $configuration

if ($result.TotalCount -eq 0) {
    throw "No tests were discovered under '$target'. A run that finds nothing is not a run that passed."
}

Write-Host ("Collector tests: {0} passed, {1} failed, {2} skipped, of {3}." -f `
        $result.PassedCount, $result.FailedCount, $result.SkippedCount, $result.TotalCount)

exit ([int]($result.FailedCount -gt 0))
