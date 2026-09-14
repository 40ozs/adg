<#
.SYNOPSIS
    Fill a local ADG database with the demo estate (AD + SMB + NTFS).

.DESCRIPTION
    Generates a deterministic estate and posts it through the ordinary ingestion API — the
    same four endpoints a Windows collector uses, with the same validation and the same
    status codes. Nothing is written to the database directly.

    Seeding is idempotent. Run ids are derived from the estate, so running this twice leaves
    one estate behind rather than two. Use -FreshRunIds to add a second set of runs to the
    history on purpose.

    The estate is deliberately imperfect: one file server's scan is partial, another failed
    outright, a directory carries a deny that wins, one account is disabled and still holds
    rights, and one ACE names a SID nobody can resolve. That is what makes it worth
    demonstrating — a clean estate only ever shows the happy path.

.PARAMETER ApiUrl
    The ADG API to post to. Defaults to the local development stack.

.PARAMETER Account
    A development-mode account to sign in as. Requires ADG_AUTH_MODE=development on the API.

.PARAMETER Token
    A bearer token for an account holding 'collectors:ingest'. Use instead of -Account
    against a deployment that is not in development mode.

.PARAMETER CollectorKey
    A value from ADG_COLLECTOR_API_KEYS. The normal ingestion credential.

.PARAMETER Profile
    small | standard | large. The feature set is identical in all three; only the volume
    differs. 'large' is for measuring, not for demonstrating.

.PARAMETER OutDir
    Write the transcripts to this directory as JSON instead of posting them.

.PARAMETER FreshRunIds
    Post under new run ids, adding to the run history instead of replaying.

.EXAMPLE
    .\scripts\seed-demo.ps1
    .\scripts\seed-demo.ps1 -Profile large -CollectorKey $env:ADG_COLLECTOR_KEY
    .\scripts\seed-demo.ps1 -OutDir .\.tmp\demo
#>
[CmdletBinding()]
param(
    [string] $ApiUrl = 'http://localhost:8000',
    [string] $Account = 'admin',
    [string] $Token,
    [string] $CollectorKey,
    [ValidateSet('small', 'standard', 'large')]
    [string] $Profile = 'standard',
    [string] $OutDir,
    [switch] $FreshRunIds
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot 'backend'
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    throw "Backend virtual environment missing. Run .\scripts\bootstrap.ps1 first."
}

$arguments = @('-m', 'app.demo', '--profile', $Profile)

if ($OutDir) {
    $arguments += @('--out', $OutDir)
}
else {
    $arguments += @('--api-url', $ApiUrl)
    # Precedence matches the API's own: a collector key is the normal ingestion credential,
    # a bearer token is an operator replaying by hand, and the development login is a
    # convenience that only exists when the API is running in development mode.
    if ($CollectorKey) { $arguments += @('--collector-key', $CollectorKey) }
    elseif ($Token) { $arguments += @('--token', $Token) }
    else { $arguments += @('--dev-login', $Account) }
}

if ($FreshRunIds) { $arguments += '--fresh-run-ids' }

Push-Location $backend
try {
    & $venvPython @arguments
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($exitCode -ne 0) {
    throw "Seeding failed with exit code $exitCode."
}

if (-not $OutDir) {
    Write-Host 'Demo estate seeded.' -ForegroundColor Green
    Write-Host '  Collectors page: http://localhost:3000/collectors'
    Write-Host '  Start here     : http://localhost:3000/resources'
    Write-Host ''
    Write-Host 'What is deliberately wrong with it:' -ForegroundColor DarkGray
    & $venvPython -m app.demo --features
}
