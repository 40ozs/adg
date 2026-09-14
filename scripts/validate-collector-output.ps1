<#
.SYNOPSIS
    Validate ADG collector output before anything trusts it.

.DESCRIPTION
    Reads scan-run transcripts, observation batches, or whole directories of them, and
    reports two kinds of problem:

      * payloads the API would reject, with the same message the endpoint would return and
        the file and field that caused it;
      * hazards in the identity graph the observations describe, which no single-payload
        check can see: membership cycles, one SID reported as several kinds, a BUILTIN
        group stored with no host scope, edges naming members nothing describes, and
        nesting or group widths that will run into the default traversal limits.

    Nothing is sent anywhere. The check is offline, so it belongs in a collector's own
    build as well as in an operator's hands.

    Exit codes: 0 when the API would accept every document, 1 when it would not (or, with
    -Strict, when there are warnings), 2 when there was nothing to read.

.PARAMETER Path
    One or more JSON files or directories. Defaults to the committed adversarial fixtures,
    which is a useful smoke test that the tool itself works.

.PARAMETER AsJson
    Emit the report as JSON, for a build that wants to parse it.

.PARAMETER Strict
    Treat warnings as failures. Use this once a directory is known to be clean.

.PARAMETER Quiet
    Print findings only, without the summary header.

.EXAMPLE
    .\scripts\validate-collector-output.ps1 -Path .\out\ad-run.json

.EXAMPLE
    # Validate everything a collector wrote, and fail the build on any finding at all.
    .\scripts\validate-collector-output.ps1 -Path .\out -Strict

.EXAMPLE
    # Capture a collector's payloads, then check them.
    .\collector\powershell\ad\Invoke-AdgAdCollector.ps1 -OutputDirectory .\out -NoUpload
    .\scripts\validate-collector-output.ps1 -Path .\out -AsJson | Out-File findings.json
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]] $Path,

    [switch] $AsJson,
    [switch] $Strict,
    [switch] $Quiet
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot 'backend'
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Backend virtual environment missing. Run .\scripts\bootstrap.ps1 first."
}

# @() so that a single path does not unroll into a bare string with no Count — and the
# filter because @($null) is a one-element array, not an empty one, which would otherwise
# send an empty path to the validator and quietly scan the current directory.
$targets = @($Path | Where-Object { $_ })
if ($targets.Count -eq 0) {
    $targets = @(Join-Path $backend 'tests\fixtures\ad_graph')
    Write-Host 'No path given; validating the committed adversarial fixtures.' -ForegroundColor Yellow
}

$resolved = foreach ($candidate in $targets) {
    if ([System.IO.Path]::IsPathRooted($candidate)) { $candidate }
    else { Join-Path (Get-Location).Path $candidate }
}

$arguments = @('-m', 'app.validation') + @($resolved)
if ($AsJson) { $arguments += '--json' }
if ($Strict) { $arguments += '--strict' }
if ($Quiet) { $arguments += '--quiet' }

# The report renders arrows and box characters, and a Windows console is usually cp1252,
# which cannot encode them: without this the tool dies in print() with a UnicodeEncodeError
# and reports nothing at all about the payloads it was asked to check.
$previousEncoding = $env:PYTHONIOENCODING
$env:PYTHONIOENCODING = 'utf-8'

Push-Location $backend
try {
    & $venvPython @arguments
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
    $env:PYTHONIOENCODING = $previousEncoding
}

exit $exitCode
