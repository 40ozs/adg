#requires -Version 7.0
<#
.SYNOPSIS
    Capture Windows' answer to every case in the ADG effective-access matrix.

.DESCRIPTION
    Emits the case matrix from `tests.access_engine.matrix`, asks Windows what it grants for
    each case, and writes the answers as a fixture the backend test suite compares against.

    The output is committed. That is deliberate: the comparison then runs on every machine
    and in CI, where no Windows host may be available, and stays a real measurement rather
    than becoming an opt-in step nobody runs. Each answer is keyed by case id and carries the
    fingerprint of the inputs it answered, so a matrix edited without regenerating the fixture
    fails loudly instead of being checked against a stale answer.

    Requires no elevation, no domain, and no file system access. See README.md.

.PARAMETER OutputPath
    Where to write the oracle fixture. Defaults to the committed location.

.PARAMETER CasePath
    Where to write the intermediate case file. Defaults to a temporary file.

.PARAMETER Python
    The interpreter to emit the matrix with. Defaults to the backend virtual environment.

.EXAMPLE
    pwsh -File scripts/windows-access-check/Invoke-AdgAccessOracle.ps1

.EXAMPLE
    pwsh -File scripts/windows-access-check/Invoke-AdgAccessOracle.ps1 -OutputPath .\oracle.json
#>
[CmdletBinding()]
param(
    [string] $OutputPath,
    [string] $CasePath,
    [string] $Python
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptRoot)
$backend = Join-Path $repoRoot 'backend'

if (-not $Python) {
    $Python = Join-Path $backend '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python interpreter not found at '$Python'. Run scripts/bootstrap.ps1 first, or pass -Python."
}

if (-not $OutputPath) {
    $OutputPath = Join-Path $backend 'tests\fixtures\windows\ntfs-access-oracle.json'
}
if (-not $CasePath) {
    $CasePath = Join-Path ([System.IO.Path]::GetTempPath()) ("adg-access-cases-$([guid]::NewGuid().ToString('N')).json")
}

Import-Module (Join-Path $scriptRoot 'AdgAuthzProbe.psm1') -Force

Write-Host "Emitting the case matrix..."
Push-Location $backend
try {
    & $Python -m tests.access_engine.matrix --emit $CasePath
    if ($LASTEXITCODE -ne 0) { throw "Emitting the matrix failed with exit code $LASTEXITCODE." }
}
finally {
    Pop-Location
}

$document = Get-Content -LiteralPath $CasePath -Raw | ConvertFrom-Json
$cases = $document.cases
Write-Host "Asking Windows about $($cases.Count) cases..."

$timer = [System.Diagnostics.Stopwatch]::StartNew()
$answers = Invoke-AdgAccessCheckBatch -Case $cases
$timer.Stop()
Write-Host ("Answered in {0:N2}s." -f $timer.Elapsed.TotalSeconds)

# The generic mapping ADG hard-codes, measured on this host rather than quoted.
$mapping = [ordered]@{
    generic_read    = ('0x{0:X8}' -f (ConvertTo-AdgFileMappedMask -Mask 0x80000000L))
    generic_write   = ('0x{0:X8}' -f (ConvertTo-AdgFileMappedMask -Mask 0x40000000L))
    generic_execute = ('0x{0:X8}' -f (ConvertTo-AdgFileMappedMask -Mask 0x20000000L))
    generic_all     = ('0x{0:X8}' -f (ConvertTo-AdgFileMappedMask -Mask 0x10000000L))
}

$os = Get-CimInstance -ClassName Win32_OperatingSystem
$payload = [ordered]@{
    schema           = 'adg.access-oracle/1'
    generated_at     = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    host             = [ordered]@{
        os_caption       = $os.Caption
        os_version       = $os.Version
        powershell       = $PSVersionTable.PSVersion.ToString()
        domain_joined    = (Get-CimInstance -ClassName Win32_ComputerSystem).PartOfDomain
    }
    method           = 'AuthzAccessCheck(MAXIMUM_ALLOWED) over a synthetic descriptor, generic rights mapped with MapGenericMask'
    generic_mapping  = $mapping
    case_count       = $answers.Count
    answers          = $answers
}

$directory = Split-Path -Parent $OutputPath
if ($directory -and -not (Test-Path -LiteralPath $directory)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
$payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
Remove-Item -LiteralPath $CasePath -Force -ErrorAction SilentlyContinue

Write-Host "Wrote $($answers.Count) answers to $OutputPath"
