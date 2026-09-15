<#
    The handlers that actually collect: one per job kind.

    Each is handed a request - the job, the mode the scheduler resolved, the orchestrator
    configuration, the saved state - and returns a summary in the shape
    ConvertTo-AdgJobOutcome normalizes. They are the only part of the orchestrator that
    knows what a directory or a file system is, which is why the runner takes them by
    injection: everything else here is testable with a fake.

    ------------------------------------------------------------------------------------
    In process, not a child process

    A collector is invoked by importing its module and calling it, rather than by starting
    powershell.exe and reading an exit code. The reason is the checkpoint: a child process
    can report success or failure and nothing else, and what this orchestrator has to record
    is the cursor the run reached, the issuer that produced it, and whether the run was
    clean enough to be allowed to keep it. Parsing that back out of stdout would be a second,
    weaker copy of the summary the collector already returns.
#>

$script:AdgImportedCollectorModules = @{}


function Import-AdgCollectorModule {
    <#
        .SYNOPSIS
            Import one collector module once per process.
        .DESCRIPTION
            Cached because a scheduled invocation runs several jobs against the same
            collector, and -Force on every one of them would re-parse thousands of lines per
            job for no benefit.
    #>
    param(
        [Parameter(Mandatory)][string] $CollectorRoot,
        [Parameter(Mandatory)][string] $RelativePath
    )

    $path = Join-Path $CollectorRoot $RelativePath
    if ($script:AdgImportedCollectorModules.ContainsKey($path)) { return }
    if (-not (Test-Path -LiteralPath $path)) {
        throw "The collector module '$path' is missing. The orchestrator expects the collector tree beside it; pass -CollectorRoot if it lives elsewhere."
    }
    Import-Module $path -Force -Global
    $script:AdgImportedCollectorModules[$path] = $true
}


function Get-AdgJobCollectorConfigPath {
    <#
        .SYNOPSIS
            The collector configuration a job names, validated.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][hashtable] $Request)

    $path = [string] $Request.Job.CollectorConfig
    if ([string]::IsNullOrWhiteSpace($path)) {
        throw "Job '$($Request.Job.Name)' names no collectorConfig, so there is nothing to tell the collector what to read."
    }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Job '$($Request.Job.Name)' names collectorConfig '$path', which does not exist."
    }
    return $path
}


function Invoke-AdgAdJob {
    <#
        .SYNOPSIS
            One Active Directory pass, as a delta when the scheduler resolved one.
        .DESCRIPTION
            The watermark is handed over with the issuer that produced it, never alone. The
            collector compares that issuer against the directory server it actually binds
            and reads everything on a mismatch, so a cursor from a different domain
            controller - or from the same one before a restore from backup - cannot be
            resumed from by accident.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][hashtable] $Request,
        [Parameter(Mandatory)][ValidateSet('principals', 'memberships')][string] $Pass
    )

    Import-AdgCollectorModule -CollectorRoot $Request.CollectorRoot -RelativePath 'common/AdgCollector.Common.psd1'
    Import-AdgCollectorModule -CollectorRoot $Request.CollectorRoot -RelativePath 'ad/AdgCollector.ActiveDirectory.psd1'

    $arguments = @{
        ConfigPath = Get-AdgJobCollectorConfigPath -Request $Request
        Job        = $Request.Job.Name
        Passes     = $Pass
        # Without this the collector writes its summary to the host and returns nothing,
        # and the orchestrator would have no cursor to record.
        PassThru   = $true
    }
    if ($Request.Mode -eq 'delta' -and $null -ne $Request.Baseline) {
        $arguments['SinceUsn'] = [long] $Request.Baseline.Token
        $arguments['CheckpointIssuer'] = [string] $Request.Baseline.Issuer
    }

    $script = Join-Path $Request.CollectorRoot 'ad/Invoke-AdgAdCollector.ps1'
    return & $Request.RunScript $script $arguments
}


function Invoke-AdgSmbJob {
    <#
        .SYNOPSIS
            The share inventory and share ACLs of the configured servers.
        .DESCRIPTION
            Always a full read, and that is not a limitation being worked around. The SMB
            server publishes no change metadata a delta could filter on, and there is
            nothing to filter: a file server has tens of shares, and reading all of them
            costs less than deciding which ones to skip.
    #>
    [OutputType([pscustomobject])]
    param([Parameter(Mandatory)][hashtable] $Request)

    Import-AdgCollectorModule -CollectorRoot $Request.CollectorRoot -RelativePath 'common/AdgCollector.Common.psd1'
    Import-AdgCollectorModule -CollectorRoot $Request.CollectorRoot -RelativePath 'smb/AdgSmbCollector.psd1'

    $script = Join-Path $Request.CollectorRoot 'smb/Invoke-AdgSmbScan.ps1'
    return & $Request.RunScript $script @{
        ConfigPath = Get-AdgJobCollectorConfigPath -Request $Request
        PassThru   = $true
    }
}


function Invoke-AdgNtfsJob {
    <#
        .SYNOPSIS
            A file-system walk, scoped to important roots or across the whole tree.
        .DESCRIPTION
            Neither shape is a delta in the protocol's sense, because the file system offers
            nothing to make one from: writing an ACL moves no timestamp a walk can test, so
            a scan that skipped unchanged-looking directories would skip exactly the changes
            ADG exists to find. Both shapes therefore read every descriptor in the scope
            they cover.

            What differs is the scope and what gets *sent*. The important-roots job covers a
            named subset often and is always incremental, because it deliberately reads part
            of the tree. The deep scan covers everything and re-sends only the descriptors
            whose digest changed since it last reported them, affirming the rest - which is
            what lets it stay a full enumeration, and therefore keep the right to reconcile,
            at a fraction of the payload.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][hashtable] $Request,
        [Parameter(Mandatory)][bool] $Scoped
    )

    Import-AdgCollectorModule -CollectorRoot $Request.CollectorRoot -RelativePath 'common/AdgCollector.Common.psd1'
    Import-AdgCollectorModule -CollectorRoot $Request.CollectorRoot -RelativePath 'ntfs/AdgNtfsCollector.psd1'

    $arguments = @{
        ConfigPath = Get-AdgJobCollectorConfigPath -Request $Request
        PassThru   = $true
    }
    if ($Scoped) {
        $roots = @($Request.Job.Roots)
        if ($roots.Count -eq 0) {
            throw "Job '$($Request.Job.Name)' scans important roots but names none."
        }
        $arguments['ScanRoot'] = $roots
    }

    $script = Join-Path $Request.CollectorRoot 'ntfs/Invoke-AdgNtfsScan.ps1'
    return & $Request.RunScript $script $arguments
}


function Invoke-AdgReconciliationJob {
    <#
        .SYNOPSIS
            The repair pass: every collector, reading everything, reconciling every scope.
        .DESCRIPTION
            This is the only job that can discover an *absence*, and it is why the cadence
            is safe. A delta reads what its source says has changed, and nothing announces a
            deletion to a query that filters on change metadata: a principal that was deleted
            simply fails to appear, which is exactly what an unchanged one does. No amount of
            running the deltas more often distinguishes them. This does.

            It runs the passes in sequence and reports the worst status any of them reached,
            because a reconciliation that half worked has not reconciled the estate - and
            reporting the best of its parts would be reporting coverage it did not achieve.
        #>
    [OutputType([pscustomobject])]
    param([Parameter(Mandatory)][hashtable] $Request)

    $order = @('ad', 'smb', 'ntfs')
    $ranked = @{ succeeded = 0; partial = 1; canceled = 2; failed = 3 }

    $results = [System.Collections.Generic.List[object]]::new()
    $worst = 'succeeded'
    $observations = 0
    $affirmations = 0
    $errors = 0

    foreach ($part in $order) {
        $result = switch ($part) {
            'ad' { Invoke-AdgAdJob -Request $Request -Pass 'principals' }
            'smb' { Invoke-AdgSmbJob -Request $Request }
            'ntfs' { Invoke-AdgNtfsJob -Request $Request -Scoped $false }
        }
        $results.Add($result)

        $status = [string] (Get-AdgConfigProperty $result 'Status' 'failed')
        if (-not $ranked.ContainsKey($status)) { $status = 'failed' }
        if ($ranked[$status] -gt $ranked[$worst]) { $worst = $status }
        $observations += [int] (Get-AdgConfigProperty $result 'ObservationCount' 0)
        $affirmations += [int] (Get-AdgConfigProperty $result 'AffirmationCount' 0)
        $errors += [int] (Get-AdgConfigProperty $result 'ErrorCount' 0)
    }

    return [pscustomobject]@{
        Status           = $worst
        RunId            = [string] (Get-AdgConfigProperty $results[0] 'RunId' '')
        ObservationCount = $observations
        AffirmationCount = $affirmations
        ErrorCount       = $errors
        Reconciled       = ($worst -eq 'succeeded')
        Parts            = $results.ToArray()
        # Deliberately none. A reconciliation runs three collectors against three sources,
        # and there is no single cursor that could describe where all of them got to. Each
        # collector records its own through its own job.
        Checkpoint       = $null
        Message          = "ran $($results.Count) collector pass(es); worst status '$worst'"
    }
}
