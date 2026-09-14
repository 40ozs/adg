<#
.SYNOPSIS
    Walk NTFS directory trees, find where permissions change, and submit what was read.

.DESCRIPTION
    The entry point for the ADG NTFS collector. For every directory it reaches it reads the
    security descriptor and reports it as contract v1 observations: the owner, whether a
    DACL is present at all, whether inheritance is blocked, and every ACE exactly as stored -
    raw access mask, raw ACE flags byte, evaluation order preserved.

    It also answers, for each directory, the question a tree scan exists to answer: did
    permissions change here? That is decided by comparing the directory's DACL against what
    its parent hands down - the parent's *projection* onto a child, not the parent's own
    DACL, which differs from every child's by construction because inheritance sets the
    INHERITED bit. Each verdict carries the reason behind it, and the cases where nobody
    knows - a scan root, an unreadable parent - say so and are reported as boundaries,
    because unknown must never read as unchanged.

    It does not compute effective access. Share permissions and NTFS permissions are
    separate layers: remote access is limited by both, and local access bypasses the share
    layer entirely. Combining them is the backend's job, from this collector's facts and the
    SMB collector's.

    It does not resolve inheritance into effective rights, expand generic rights, apply Deny
    precedence, or drop mask bits it does not recognize. An ACE is evidence, and a
    simplified ACE is no longer evidence.

    ADG is read-only. Nothing here writes to a target, enables a privilege, takes ownership,
    or modifies a security descriptor to make a read succeed. A directory whose descriptor
    cannot be read is reported as an error and the run is marked partial, which is the
    honest outcome - and the one that stops an unread ACL from being mistaken for an empty
    one.

    Targets are always explicit. There is no estate-wide sweep: pass -ScanRoot, or list the
    directories in a configuration file. See adg-ntfs-targets.example.json.

.PARAMETER ApiBaseUrl
    Base URL of the ADG API, for example http://localhost:8000.

.PARAMETER ConfigPath
    Path to a JSON target configuration. Merged with -ScanRoot if both are given.

.PARAMETER SafeDefaults
    Start from the production profile rather than the first-run defaults: concurrency 8,
    maxDepth 24, a four-hour deadline, and a checkpoint every minute. It changes only what
    the configuration file does not say, and a command-line switch still overrides it. See
    adg-ntfs-safe-defaults.example.json for every value and the reasoning, and set
    -CheckpointPath alongside it - the deadline is what makes an unresumable scan a problem.

.PARAMETER ScanRoot
    Directories to walk, as UNC paths: \\FS01\Finance. A path inside a share is accepted;
    its boundary is then reported as scan_root, because its parent was not read.

.PARAMETER ShareRoot
    The Phase 3A spelling of -ScanRoot, kept so existing command lines keep working.

.PARAMETER MaxDepth
    How many levels below a scan root to walk. 0 reads the roots and nothing else, which is
    exactly the Phase 3A behaviour. Overrides the configuration file.

.PARAMETER ExcludePath
    UNC path patterns whose directories and subtrees are not read at all. A scan that
    excludes anything cannot reconcile its scope, and says so.

.PARAMETER IncludePath
    UNC path patterns restricting which directories are reported. The walk still passes
    through the directories between a root and a match, and reports none of them.

.PARAMETER IncludeFiles
    Read the DACL of every file as well. Off by default, and expensive: an estate holds
    orders of magnitude more files than directories.

.PARAMETER ReparsePointPolicy
    skip (default) reads a junction's own descriptor and does not descend; ignore does not
    read it at all; follow descends, with the walk's visited set as the loop guard.

.PARAMETER ConcurrencyLimit
    How many descriptor reads may be in flight at once. 1 by default.

.PARAMETER TimeoutSeconds
    Stop after this long, write a checkpoint, and report the run as canceled. 0 is no limit.

.PARAMETER CheckpointPath
    Where to record the frontier so an interrupted scan can be resumed. Required for -Resume.

.PARAMETER Resume
    Continue the scan the checkpoint describes, keeping its run id. Refused if the roots,
    depth, patterns, reparse policy, or file setting have changed since it was written.

.PARAMETER RunPerScanRoot
    Emit one scan run per root instead of one run for all of them. Recommended for any
    estate larger than a handful: one unreadable descriptor downgrades a combined run to
    'partial', and it makes reconciliation per-tree rather than all-or-nothing.

.PARAMETER DryRun
    Write the exact payloads that would be POSTed, and send nothing. Requires
    -OutputDirectory.

.PARAMETER OutputDirectory
    Where -DryRun writes run-NN-start.json, run-NN-batch-NNN.json, and
    run-NN-completion.json.

.EXAMPLE
    .\Invoke-AdgNtfsScan.ps1 -ApiBaseUrl http://localhost:8000 -ScanRoot \\FS01\Finance

.EXAMPLE
    .\Invoke-AdgNtfsScan.ps1 -ApiBaseUrl http://localhost:8000 -ScanRoot \\FS01\Finance `
        -MaxDepth 6 -ExcludePath \\FS01\Finance\Archive -ConcurrencyLimit 8 `
        -CheckpointPath C:\ProgramData\ADG\finance.checkpoint.json

.EXAMPLE
    .\Invoke-AdgNtfsScan.ps1 -ApiBaseUrl http://localhost:8000 -ConfigPath .\adg-ntfs-targets.json -Resume

.NOTES
    Minimum privileges, prerequisites, and operational tuning are in README.md next to this
    script. Domain Admin is not required and must not be used.
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
    [switch] $SafeDefaults,
    [string[]] $ScanRoot = @(),
    [string[]] $ShareRoot = @(),
    [Nullable[int]] $MaxDepth,
    [string[]] $IncludePath = @(),
    [string[]] $ExcludePath = @(),
    [switch] $IncludeFiles,
    [ValidateSet('skip', 'ignore', 'follow')][string] $ReparsePointPolicy,
    [Nullable[int]] $ConcurrencyLimit,
    [Nullable[int]] $TimeoutSeconds,
    [string] $CheckpointPath,
    [switch] $Resume,
    [switch] $RunPerScanRoot,
    [string] $CollectorVersion = '0.1.0'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot 'AdgNtfsCollector.psd1'
Import-Module $modulePath -Force

$settings = Import-AdgNtfsTarget -Path $ConfigPath -ScanRoot $ScanRoot -ShareRoot $ShareRoot `
    -SafeDefaults:$SafeDefaults

if ($SafeDefaults -and [string]::IsNullOrWhiteSpace($settings.CheckpointPath)) {
    # Not fatal, because a caller may genuinely want one bounded pass. But the profile sets a
    # four-hour deadline, and a deadline without a checkpoint turns a long scan into a scan
    # that starts again from nothing.
    Write-Warning '-SafeDefaults sets a four-hour deadline and no checkpoint was configured. A scan that reaches the deadline will stop and cannot be resumed; pass -CheckpointPath so the next run continues instead of starting again.'
}

# Command-line overrides, applied after the file so a one-off run can narrow a standing
# configuration without editing it. Each is validated through the same bounds the file uses,
# because a limit is only a limit if every route to it is checked.
if ($null -ne $MaxDepth) {
    if ($MaxDepth -lt 0 -or $MaxDepth -gt 512) { throw "MaxDepth must be between 0 and 512, not $MaxDepth." }
    $settings.MaxDepth = [int] $MaxDepth
}
if ($null -ne $ConcurrencyLimit) {
    if ($ConcurrencyLimit -lt 1 -or $ConcurrencyLimit -gt 32) { throw "ConcurrencyLimit must be between 1 and 32, not $ConcurrencyLimit." }
    $settings.ConcurrencyLimit = [int] $ConcurrencyLimit
}
if ($null -ne $TimeoutSeconds) {
    if ($TimeoutSeconds -lt 0 -or $TimeoutSeconds -gt 86400) { throw "TimeoutSeconds must be between 0 and 86400, not $TimeoutSeconds." }
    $settings.TimeoutSeconds = [int] $TimeoutSeconds
}
if (@($IncludePath).Count -gt 0) { $settings.IncludePaths = @($IncludePath) }
if (@($ExcludePath).Count -gt 0) { $settings.ExcludePaths = @($ExcludePath) }
if ($IncludeFiles) { $settings.IncludeFiles = $true }
if (-not [string]::IsNullOrWhiteSpace($ReparsePointPolicy)) { $settings.ReparsePointPolicy = $ReparsePointPolicy }
if (-not [string]::IsNullOrWhiteSpace($CheckpointPath)) { $settings.CheckpointPath = $CheckpointPath }

if ($Resume -and [string]::IsNullOrWhiteSpace($settings.CheckpointPath)) {
    throw '-Resume needs a checkpoint to resume from. Pass -CheckpointPath, or set checkpointPath in the configuration file.'
}

Write-Host "Walking $($settings.ScanRoots.Count) tree(s): $($settings.ScanRoots -join ', ')"
Write-Host ("Depth limit {0}, reparse policy '{1}', concurrency {2}, files {3}{4}{5}." -f `
        $settings.MaxDepth, $settings.ReparsePointPolicy, $settings.ConcurrencyLimit,
    $(if ($settings.IncludeFiles) { 'on' } else { 'off' }),
    $(if ($settings.TimeoutSeconds -gt 0) { ", timeout $($settings.TimeoutSeconds)s" } else { '' }),
    $(if ($settings.CheckpointPath) { ", checkpoint $($settings.CheckpointPath)" } else { '' }))

$runIndex = 0
$sink = $null
$transport = $null

# The three sinks a run streams into. They are built per run rather than once, because
# -RunPerScanRoot produces several and each writes its own files or opens its own run.
$onStart = {
    param($start)
    $script:runIndex++
    if ($DryRun) {
        $script:sink = New-AdgNtfsFileSink -OutputDirectory $OutputDirectory -Prefix ('run-{0:d2}' -f $script:runIndex)
        Write-AdgNtfsPayload -Sink $script:sink -Payload $start -Kind 'start'
    }
    else {
        $script:transport = New-AdgNtfsTransport -ApiBaseUrl $ApiBaseUrl
        Send-AdgNtfsStart -Transport $script:transport -Start $start
    }
}

$onBatch = {
    param($batch)
    if ($DryRun) { Write-AdgNtfsPayload -Sink $script:sink -Payload $batch -Kind 'batch' }
    else { Send-AdgNtfsBatch -Transport $script:transport -Batch $batch }
}

$onCompletion = {
    param($completion)
    if ($DryRun) { Write-AdgNtfsPayload -Sink $script:sink -Payload $completion -Kind 'completion' }
    else { Send-AdgNtfsCompletion -Transport $script:transport -Completion $completion }
}

$runs = Invoke-AdgNtfsScan -Settings $settings -OnStart $onStart -OnBatch $onBatch -OnCompletion $onCompletion `
    -RunPerScanRoot:$RunPerScanRoot -Resume:$Resume -CollectorVersion $CollectorVersion -ModulePath $modulePath

foreach ($run in $runs) {
    $summary = $run.Summary
    Write-Host ("Run {0}: {1} - {2}/{3} tree(s) entered, {4} director(ies) read, {5} file(s), {6} observation(s) in {7} batch(es), {8} error(s), {9:n1}s." -f `
            $summary.RunId, $summary.Status, $summary.ScanRootsRead, $summary.ScanRootsRequested, `
            $summary.DirectoriesRead, $summary.FilesRead, $summary.ObservationCount, `
            $summary.BatchCount, $summary.ErrorCount, $summary.ElapsedSeconds)

    # The two numbers that carry the point of a boundary scan. Their ratio is why ADG walks
    # a tree at all: thousands of directories, and only as many distinct permission
    # decisions as there are distinct DACLs.
    Write-Host ("  {0} unique ACL state(s) across {1} director(ies) read; {2} boundar(ies)." -f `
            $summary.UniqueAclHashes, $summary.DirectoriesRead, $summary.BoundariesFound)

    if ($summary.ScanRootsUnread.Count -gt 0) {
        # Not a warning about tidiness: an unread root means this run cannot say anything
        # about that tree's permissions, and must not be read as having found none.
        Write-Warning ("Unread: {0}. Their NTFS permissions are unobserved, not absent." -f ($summary.ScanRootsUnread -join ', '))
    }

    if (-not $summary.Completed) {
        Write-Warning ("The walk did not finish: {0} director(ies) are still pending. Resume with -Resume to complete it; until then everything below them is unobserved, not absent." -f $summary.PendingDirectories)
    }

    foreach ($record in @($run.Roots)) {
        if ($record.Read -and -not $record.Exhaustive) {
            Write-Host ("  {0} was not fully enumerated ({1}); its scope is not reconciled." -f `
                    $record.Path, (@($record.Reasons) -join ', '))
        }
    }

    if ($summary.ReconciledScopes -gt 0) {
        Write-Host ("  {0} scope(s) reconciled: the backend may mark directories it did not see in this run as absent." -f $summary.ReconciledScopes)
    }
}

if ($DryRun) {
    Write-Host "Dry run complete. Payloads written to $OutputDirectory"
}
