<#
.SYNOPSIS
    Measure what an NTFS tree scan costs.

.DESCRIPTION
    Builds generated trees of known size (scripts\windows-test-tree) and walks them, recording
    the numbers an operator sizing a scan window actually needs: paths per second, ACL reads
    per second, how many distinct ACL states the tree collapses to, what the walk retains in
    memory, and how much of the wall clock is the walk rather than the submission.

    These are not pass/fail tests. A performance threshold that passes on one machine and
    fails on another teaches people to ignore the suite. The numbers, and the machine that
    produced them, are recorded in docs\architecture\ntfs-scan-performance.md; re-run this and
    update that document whenever the walk, the observation layer, or the batching changes.

    ------------------------------------------------------------------------------------
    What these numbers are not

    A local NTFS volume reached over \\localhost\C$ is not a file server reached over a
    network. A descriptor read here costs CPU and a page-cache hit; the same read against a
    remote server costs a round trip, and a round trip is what -ConcurrencyLimit exists to
    hide. So the *shape* of what follows transfers and the absolute figures do not, and the
    concurrency measurement in particular will understate the gain that matters. It is
    reported anyway, with that stated, rather than left out and guessed at later.

    ------------------------------------------------------------------------------------
    Hashing does not save you a traversal

    The ratio of directories to distinct ACL states is the number the boundary design rests
    on, and it is easy to misread. It says a large tree holds few distinct permission states,
    so **storing and querying** them is cheap. It says nothing about collection: every one of
    those directories was opened and had its descriptor read, because that is the only way to
    discover which state it is in. The benchmark reports the two costs separately for exactly
    this reason - DirectoriesRead is what the scan paid, UniqueAclHashes is what the database
    keeps.

.PARAMETER Scale
    small, medium, or large. Sizes are in scripts\windows-test-tree\AdgTestTree.psm1; large
    builds roughly 66,000 directories and the build takes several minutes.

.PARAMETER Concurrency
    Concurrency settings to compare. Defaults to 1 and 8 - the built-in default and the
    safe-defaults profile.

.PARAMETER IncludeFiles
    Also measure a file-level scan, which is the setting most likely to be turned on by
    somebody who has not measured what it costs.

.PARAMETER KeepTree
    Leave the generated tree in place afterwards. Useful for repeated runs: the build is
    slower than the scan.

.PARAMETER Root
    Where to build. Defaults to .tmp\windows-test-tree\bench-<scale> under the repository.

.PARAMETER AsJson
    Emit JSON instead of the text table.

.PARAMETER OutFile
    Also write the output to this path.

.PARAMETER Database
    Also run the backend ingestion and query benchmark, which needs PostgreSQL
    (.\scripts\stack-up.ps1 -DbOnly). It uses its own '<database>_bench' database.

.EXAMPLE
    .\scripts\ntfs-benchmark.ps1

.EXAMPLE
    .\scripts\ntfs-benchmark.ps1 -Scale medium -IncludeFiles -OutFile .\.tmp\ntfs-bench.txt

.EXAMPLE
    .\scripts\stack-up.ps1 -DbOnly
    .\scripts\ntfs-benchmark.ps1 -Scale small -Database
#>
[CmdletBinding()]
param(
    [ValidateSet('small', 'medium', 'large')][string] $Scale = 'small',
    [int[]] $Concurrency = @(1, 8),
    [switch] $IncludeFiles,
    [switch] $KeepTree,
    [string] $Root,
    [switch] $AsJson,
    [string] $OutFile,
    [switch] $Database
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $repoRoot 'scripts\windows-test-tree\AdgTestTree.psm1') -Force
$modulePath = Join-Path $repoRoot 'collector\powershell\ntfs\AdgNtfsCollector.psd1'
Import-Module $modulePath -Force

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $repoRoot ".tmp\windows-test-tree\bench-$Scale"
}
$Root = Resolve-AdgTestTreePath $Root

if (-not (Test-AdgTestTreeUncAccess -Path $repoRoot)) {
    throw ("This benchmark walks a real tree, and the collector identifies a directory by its UNC path. " +
        "On one machine the only UNC route to an ordinary directory is the drive's administrative share, " +
        "and it is not reachable from this session. Nothing here can be measured without it, and measuring " +
        "a local path instead would be measuring a configuration that never ships.")
}

# ---------------------------------------------------------------------- the tree

$existing = (Test-Path -LiteralPath $Root) -and @(Get-ChildItem -LiteralPath $Root -Force -ErrorAction SilentlyContinue).Count -gt 0
if ($existing) {
    Write-Host "Reusing the tree at $Root (pass -Root elsewhere, or remove it, to rebuild)."
    $buildSeconds = 0.0
    $treeDirectories = @(Get-ChildItem -LiteralPath $Root -Recurse -Directory -Force).Count + 1
    $unc = Get-AdgTestTreeUncPath -Path $Root
}
else {
    $shape = Get-AdgTestTreeScale -Name $Scale
    Write-Host ("Building the '{0}' tree: fanout {1}, depth {2}, about {3} directories, {4} ACL variants." -f `
            $shape.Name, $shape.Fanout, $shape.Depth, $shape.ExpectedDirectory, $shape.AclVariants)
    $manifest = New-AdgTestTree -Root $Root -Profile $Scale -Force
    $buildSeconds = $manifest.buildSeconds
    $treeDirectories = $manifest.directoriesVisibleFromHere
    $unc = $manifest.uncRoot
    Write-Host ("Built {0} director(ies) in {1:n1}s." -f $treeDirectories, $buildSeconds)
}

# --------------------------------------------------------------------- measuring

function Measure-Walk {
    <#
        .SYNOPSIS
            One walk, timed, with the numbers an operator sizing a scan needs.
        .DESCRIPTION
            The payloads are counted and dropped rather than kept. Keeping them would measure
            this script's memory rather than the collector's - and the claim under test is
            that a streaming walk does not retain the tree, which cannot be checked by a
            harness that does.

            Managed memory is read after a forced collection on both sides, so what it reports
            is what the walk is still *holding*: the visited set, the digest set, and the
            frontier. Working set is sampled during the walk instead, because it is the number
            that decides whether a scan fits on a collector host.
    #>
    param(
        [Parameter(Mandatory)][string] $Name,
        [Parameter(Mandatory)][string] $ScanRoot,
        [int] $ConcurrencyLimit = 1,
        [switch] $WithFiles
    )

    $settings = Import-AdgNtfsTarget -ScanRoot $ScanRoot -SafeDefaults
    $settings.ConcurrencyLimit = $ConcurrencyLimit
    $settings.IncludeFiles = [bool] $WithFiles
    # No deadline while measuring: a walk that stopped at the profile's four hours would be
    # reporting a timeout rather than a throughput.
    $settings.TimeoutSeconds = 0

    # Counted on a shared object rather than in plain variables. A script block invoked by
    # the scan reads its own scope, so `$batches++` inside one increments a local that
    # vanishes when it returns - and the benchmark would report zero batches for a walk that
    # produced hundreds. A property set on an object both scopes hold is seen by both.
    $counters = [pscustomobject]@{
        Observations   = 0
        Batches        = 0
        Samples        = 0
        PeakWorkingSet = 0L
    }
    $process = [System.Diagnostics.Process]::GetCurrentProcess()

    [void] [System.GC]::Collect()
    $managedBefore = [System.GC]::GetTotalMemory($true)

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $runs = @(Invoke-AdgNtfsScan -Settings $settings -ModulePath $modulePath `
            -OnStart { param($payload) } `
            -OnBatch {
            param($payload)
            $counters.Batches++
            $counters.Observations += @($payload['observations']).Count
            # Sampled rather than read every batch: Refresh() is a syscall, and a benchmark
            # that spends its time measuring itself is not measuring the walk.
            $counters.Samples++
            if ($counters.Samples % 8 -eq 0) {
                $process.Refresh()
                if ($process.WorkingSet64 -gt $counters.PeakWorkingSet) {
                    $counters.PeakWorkingSet = $process.WorkingSet64
                }
            }
        } `
            -OnCompletion { param($payload) })
    $stopwatch.Stop()

    $managedAfter = [System.GC]::GetTotalMemory($true)
    $process.Refresh()
    if ($process.WorkingSet64 -gt $counters.PeakWorkingSet) { $counters.PeakWorkingSet = $process.WorkingSet64 }

    $summary = $runs[0].Summary
    $seconds = [Math]::Max($stopwatch.Elapsed.TotalSeconds, 0.001)

    return [pscustomobject]@{
        Name                = $Name
        Concurrency         = $ConcurrencyLimit
        IncludeFiles        = [bool] $WithFiles
        DirectoriesRead     = [int] $summary.DirectoriesRead
        FilesRead           = [int] $summary.FilesRead
        AclsRead            = [int] $summary.AclsRead
        UniqueAclHashes     = [int] $summary.UniqueAclHashes
        BoundariesFound     = [int] $summary.BoundariesFound
        Observations        = $counters.Observations
        Batches             = $counters.Batches
        Errors              = [int] $summary.ErrorCount
        Seconds             = [Math]::Round($seconds, 3)
        PathsPerSecond      = [Math]::Round(($summary.DirectoriesRead + $summary.FilesRead) / $seconds, 1)
        AclReadsPerSecond   = [Math]::Round($summary.AclsRead / $seconds, 1)
        ObservationsPerSec  = [Math]::Round($counters.Observations / $seconds, 1)
        RetainedKiB         = [Math]::Round(($managedAfter - $managedBefore) / 1KB, 1)
        PeakWorkingSetMiB   = [Math]::Round($counters.PeakWorkingSet / 1MB, 1)
        BytesRetainedPerDir = if ($summary.DirectoriesRead -gt 0) {
            [Math]::Round(($managedAfter - $managedBefore) / $summary.DirectoriesRead, 1)
        }
        else { 0 }
    }
}

$measurements = [System.Collections.Generic.List[object]]::new()

foreach ($limit in ($Concurrency | Sort-Object -Unique)) {
    Write-Host "Walking at concurrency $limit..."
    $measurements.Add((Measure-Walk -Name "walk@$limit" -ScanRoot $unc -ConcurrencyLimit $limit))
}

if ($IncludeFiles) {
    Write-Host 'Walking with file scanning on...'
    $measurements.Add((Measure-Walk -Name 'walk+files' -ScanRoot $unc -ConcurrencyLimit ($Concurrency | Select-Object -First 1) -WithFiles))
}

# ---------------------------------------------------------------------- reporting

$machine = [ordered]@{
    machine    = [System.Environment]::MachineName
    processors = [System.Environment]::ProcessorCount
    powershell = $PSVersionTable.PSVersion.ToString()
    os         = [System.Runtime.InteropServices.RuntimeInformation]::OSDescription.Trim()
    volume     = (Split-Path -Qualifier $Root)
    measuredAt = [datetime]::UtcNow.ToString('o')
}

$report = [ordered]@{
    scale           = $Scale
    root            = $Root
    uncRoot         = $unc
    treeDirectories = $treeDirectories
    buildSeconds    = $buildSeconds
    machine         = $machine
    measurements    = @($measurements)
}

if ($AsJson) {
    $rendered = $report | ConvertTo-Json -Depth 6
}
else {
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add("ADG NTFS scan benchmark - scale=$Scale")
    $lines.Add("$($machine.powershell) on $($machine.os), $($machine.processors) processors")
    $lines.Add("tree: $treeDirectories directories at $unc")
    if ($buildSeconds -gt 0) {
        $lines.Add(("building it took {0:n1}s - the generator's cost, not the collector's" -f $buildSeconds))
    }
    $lines.Add('')
    $lines.Add(('{0,-12} {1,-5} {2,9} {3,8} {4,8} {5,9} {6,9} {7,8} {8,9} {9,9}' -f `
            'measurement', 'conc', 'dirs', 'files', 'unique', 'paths/s', 'acls/s', 'batches', 'held KiB', 'peak MiB'))
    foreach ($item in $measurements) {
        $lines.Add(('{0,-12} {1,-5} {2,9} {3,8} {4,8} {5,9} {6,9} {7,8} {8,9} {9,9}' -f `
                $item.Name, $item.Concurrency, $item.DirectoriesRead, $item.FilesRead,
            $item.UniqueAclHashes, $item.PathsPerSecond, $item.AclReadsPerSecond,
            $item.Batches, $item.RetainedKiB, $item.PeakWorkingSetMiB))
    }
    $lines.Add('')
    $first = $measurements[0]
    $lines.Add(('{0} directories collapsed to {1} distinct ACL state(s) and {2} boundar(ies).' -f `
                $first.DirectoriesRead, $first.UniqueAclHashes, $first.BoundariesFound))
    $lines.Add('Every one of those directories was opened and read to find that out. The ratio is what')
    $lines.Add('storage and query cost; it is not a traversal the scan gets to skip.')
    $rendered = $lines -join [Environment]::NewLine
}

Write-Output $rendered

if ($OutFile) {
    $resolved = if ([System.IO.Path]::IsPathRooted($OutFile)) { $OutFile } else { Join-Path $repoRoot $OutFile }
    $parent = Split-Path -Parent $resolved
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    Set-Content -LiteralPath $resolved -Value $rendered -Encoding utf8
    Write-Host "Written to $resolved"
}

# ----------------------------------------------------------------- the database

if ($Database) {
    $backend = Join-Path $repoRoot 'backend'
    $venvPython = Join-Path $backend '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "Backend virtual environment missing. Run .\scripts\bootstrap.ps1 first."
    }

    Write-Host ''
    Write-Host "Seeding and truncating the '<database>_bench' database." -ForegroundColor Yellow
    $arguments = @('-m', 'tests.benchmarks.ntfs_benchmark', '--scale', $Scale)
    if ($AsJson) { $arguments += '--json' }

    Push-Location $backend
    try { & $venvPython @arguments }
    finally { Pop-Location }
}

if (-not $KeepTree -and -not $existing) {
    Write-Host "Removing $Root"
    Remove-AdgTestTree -Root $Root
}
