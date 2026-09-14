<#
.SYNOPSIS
    Measure what a membership answer costs.

.DESCRIPTION
    Runs the in-memory traversal benchmarks and, with -Database, the PostgreSQL suite as
    well. The database suite seeds its own '<database>_bench' database — created and
    migrated on demand, and truncated on every run — so it never touches development or
    test data.

    The numbers are recorded in docs\architecture\ad-graph-validation.md together with the
    machine that produced them. Re-run this and update that document whenever the query
    path, the schema, or the indexes change; a benchmark nobody re-runs is a number that
    used to be true.

    These are not pass/fail tests. A performance threshold that passes on one machine and
    fails on another teaches people to ignore the suite.

.PARAMETER Scale
    small, medium (default), or large. 'large' seeds roughly 310,000 principals and
    400,000 edges and takes a few minutes.

.PARAMETER Database
    Also run the PostgreSQL suite. Requires the stack up (.\scripts\stack-up.ps1 -DbOnly).

.PARAMETER AsJson
    Emit JSON instead of the text table.

.PARAMETER OutFile
    Also write the output to this path.

.EXAMPLE
    .\scripts\graph-benchmark.ps1

.EXAMPLE
    .\scripts\stack-up.ps1 -DbOnly
    .\scripts\graph-benchmark.ps1 -Scale large -Database -OutFile .\.tmp\bench-large.txt
#>
[CmdletBinding()]
param(
    [ValidateSet('small', 'medium', 'large')]
    [string] $Scale = 'medium',

    [switch] $Database,
    [switch] $AsJson,
    [string] $OutFile
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot 'backend'
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Backend virtual environment missing. Run .\scripts\bootstrap.ps1 first."
}

$arguments = @('-m', 'tests.benchmarks.graph_benchmark', '--scale', $Scale)
if ($Database) {
    $arguments += '--database'
    Write-Host "Seeding and truncating the '<database>_bench' database." -ForegroundColor Yellow
}
if ($AsJson) { $arguments += '--json' }

Push-Location $backend
try {
    if ($OutFile) {
        $resolved = if ([System.IO.Path]::IsPathRooted($OutFile)) { $OutFile }
        else { Join-Path $repoRoot $OutFile }
        $parent = Split-Path -Parent $resolved
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        & $venvPython @arguments | Tee-Object -FilePath $resolved
    }
    else {
        & $venvPython @arguments
    }
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode
