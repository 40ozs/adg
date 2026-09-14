<#
.SYNOPSIS
    Read the NTFS security descriptor of each configured SMB share root and submit it.

.DESCRIPTION
    The entry point for the ADG NTFS collector. For every share root you name it reads the
    directory's security descriptor and reports it as contract v1 observations: the owner,
    whether a DACL is present at all, whether inheritance is blocked, and every ACE exactly
    as stored - raw access mask, raw ACE flags byte, evaluation order preserved.

    It does not compute effective access. Share permissions and NTFS permissions are
    separate layers: remote access is limited by both, and local access bypasses the share
    layer entirely. Combining them is the backend's job, from this collector's facts and the
    SMB collector's.

    It does not resolve inheritance, expand generic rights, apply Deny precedence, or drop
    mask bits it does not recognize. An ACE is evidence, and a simplified ACE is no longer
    evidence.

    ADG is read-only. Nothing here writes to a target, enables a privilege, takes ownership,
    or modifies a security descriptor to make a read succeed. A directory whose descriptor
    cannot be read is reported as an error and the run is marked partial, which is the
    honest outcome - and the one that stops an unread ACL from being mistaken for an empty
    one.

    This phase reads share roots only; a path inside a share is refused with a message
    saying so. Every run is marked incremental and reconciles nothing, because a share-root
    read has enumerated no directory tree - see the scope note in functions/AdgNtfsScan.ps1.

    Targets are always explicit. There is no estate-wide sweep: pass -ShareRoot, or list
    roots in a configuration file. See adg-ntfs-targets.example.json.

.PARAMETER ApiBaseUrl
    Base URL of the ADG API, for example http://localhost:8000.

.PARAMETER ConfigPath
    Path to a JSON target configuration. Merged with -ShareRoot if both are given.

.PARAMETER ShareRoot
    Share roots to read, as UNC paths: \\FS01\Finance.

.PARAMETER RunPerShareRoot
    Emit one scan run per share root instead of one run for all of them. Recommended for
    any estate larger than a handful: one unreadable descriptor downgrades a combined run to
    'partial', and an operator reading that status cannot tell which root failed.

.PARAMETER DryRun
    Write the exact payloads that would be POSTed, and send nothing. Requires
    -OutputDirectory.

.PARAMETER OutputDirectory
    Where -DryRun writes run-NN-start.json, run-NN-batch-NNN.json, and
    run-NN-completion.json.

.EXAMPLE
    .\Invoke-AdgNtfsScan.ps1 -ApiBaseUrl http://localhost:8000 -ShareRoot \\FS01\Finance

.EXAMPLE
    .\Invoke-AdgNtfsScan.ps1 -ConfigPath .\adg-ntfs-targets.json -DryRun -OutputDirectory C:\code\adg\.tmp\ntfs

.NOTES
    Minimum privileges and prerequisites are documented in README.md next to this script.
    Domain Admin is not required and must not be used.
#>
[CmdletBinding(DefaultParameterSetName = 'Send')]
param(
    [Parameter(ParameterSetName = 'Send', Mandatory = $true)]
    [string] $ApiBaseUrl,

    [Parameter(ParameterSetName = 'DryRun', Mandatory = $true)]
    [switch] $DryRun,

    [Parameter(ParameterSetName = 'DryRun', Mandatory = $true)]
    [string] $OutputDirectory,

    [string] $ConfigPath,
    [string[]] $ShareRoot = @(),
    [switch] $RunPerShareRoot,
    [string] $CollectorVersion = '0.1.0'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'AdgNtfsCollector.psd1') -Force

$settings = Import-AdgNtfsTarget -Path $ConfigPath -ShareRoot $ShareRoot

Write-Host "Reading $($settings.ShareRoots.Count) share root(s): $($settings.ShareRoots -join ', ')"
Write-Host 'Runs from this collector are incremental and reconcile nothing: a share-root read enumerates no directory tree, so nothing may be marked absent from it.'

$runs = Invoke-AdgNtfsScan -Settings $settings -RunPerShareRoot:$RunPerShareRoot -CollectorVersion $CollectorVersion

foreach ($run in $runs) {
    $summary = $run.Summary
    Write-Host ("Run {0}: {1} - {2}/{3} share root(s) read, {4} observation(s) in {5} batch(es), {6} error(s)." -f `
            $summary.RunId, $summary.Status, $summary.ShareRootsRead, $summary.ShareRootsRequested, `
            $summary.ObservationCount, $summary.BatchCount, $summary.ErrorCount)

    if ($summary.ShareRootsUnread.Count -gt 0) {
        # Not a warning about tidiness: an unread root means this run cannot say anything
        # about that directory's permissions, and must not be read as having found none.
        Write-Warning ("Unread: {0}. Their NTFS permissions are unobserved, not absent." -f ($summary.ShareRootsUnread -join ', '))
    }
}

if ($DryRun) {
    if (-not (Test-Path -LiteralPath $OutputDirectory)) {
        New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    }

    $index = 0
    foreach ($run in $runs) {
        $index++
        $prefix = 'run-{0:d2}' -f $index

        $run.Start | ConvertTo-Json -Depth 12 |
            Set-Content -LiteralPath (Join-Path $OutputDirectory "$prefix-start.json") -Encoding utf8

        foreach ($batch in @($run.Batches)) {
            $name = '{0}-batch-{1:d3}.json' -f $prefix, $batch.sequence
            $batch | ConvertTo-Json -Depth 12 |
                Set-Content -LiteralPath (Join-Path $OutputDirectory $name) -Encoding utf8
        }

        $run.Completion | ConvertTo-Json -Depth 12 |
            Set-Content -LiteralPath (Join-Path $OutputDirectory "$prefix-completion.json") -Encoding utf8
    }

    Write-Host "Dry run complete. Payloads written to $OutputDirectory"
    return
}

foreach ($run in $runs) {
    $result = Send-AdgNtfsScanRun -ApiBaseUrl $ApiBaseUrl -Run $run
    Write-Host ("Submitted run {0}: {1}, {2} batch(es) sent, {3} rejected." -f `
            $result.RunId, $result.Status, $result.BatchesSent, $result.BatchesRejected)
}
