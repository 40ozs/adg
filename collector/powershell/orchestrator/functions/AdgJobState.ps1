<#
    Per-job state: when it last ran, where it got to, and whether it is running now.

    Three files per job, in the state directory, all keyed by job name:

        <job>.state.json     the last run's outcome, and the checkpoint it left
        <job>.state.json.tmp the half-written one, if a process died mid-write
        <job>.lock           held while the job runs

    ------------------------------------------------------------------------------------
    Why a checkpoint is written after a batch and not after a run

    A delta that runs for an hour and dies at minute fifty has had fifty minutes of batches
    accepted. Throwing that away is safe but wasteful, and on a large estate "safe but
    wasteful" is a scan that never finishes. So the collector advances its cursor every time
    the server acknowledges a batch, and the server does the same on its side inside the
    same transaction that wrote the batch's rows.

    The direction of the error matters more than the saving. A cursor that lags the data
    makes the next run re-read objects, which costs time. A cursor that leads the data makes
    the next run skip objects, which costs nothing and produces an audit that is quietly
    wrong. Everything here is arranged so that only the first can happen.

    ------------------------------------------------------------------------------------
    Why the write is a rename

    The state file is written to a sibling temporary file and moved into place, because the
    thing it most needs to survive is the process being killed while writing it. A
    half-written JSON document is not a slightly stale checkpoint; it is a file that fails
    to parse, and the only honest response to that is to read everything again.
#>

$script:AdgJobStateFormat = 'adg-orchestrator-state/1'


function Get-AdgJobStatePath {
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $StateDirectory,
        [Parameter(Mandatory)][string] $JobName
    )
    return Join-Path $StateDirectory "$JobName.state.json"
}


function Get-AdgJobLockPath {
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $StateDirectory,
        [Parameter(Mandatory)][string] $JobName
    )
    return Join-Path $StateDirectory "$JobName.lock"
}


function Initialize-AdgStateDirectory {
    <#
        .SYNOPSIS
            Make sure the state directory exists, and say so if it cannot.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][string] $StateDirectory)

    if (-not (Test-Path -LiteralPath $StateDirectory)) {
        try {
            New-Item -ItemType Directory -Path $StateDirectory -Force | Out-Null
        }
        catch {
            throw "The state directory '$StateDirectory' could not be created: $($_.Exception.Message). Without it every run would be a full run, and no delta could ever resume."
        }
    }
    return $StateDirectory
}


function Read-AdgJobState {
    <#
        .SYNOPSIS
            A job's last recorded outcome, or $null when it has never run here.
        .DESCRIPTION
            A state file that cannot be parsed is reported as no state at all rather than
            as an error. That is the safe direction: no state means the next run reads
            everything, which is exactly what should happen when the resume point is
            unreadable. The warning says so, because an installation silently doing full
            scans forever is a cost somebody should be told about.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $StateDirectory,
        [Parameter(Mandatory)][string] $JobName
    )

    $path = Get-AdgJobStatePath -StateDirectory $StateDirectory -JobName $JobName
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }

    $document = try {
        Get-Content -LiteralPath $path -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        Write-Warning "State file '$path' is not valid JSON ($($_.Exception.Message)); job '$JobName' will read its whole scope. Delete the file to silence this."
        return $null
    }

    $format = [string] (Get-AdgConfigProperty $document 'schema' '')
    if ($format -ne $script:AdgJobStateFormat) {
        Write-Warning "State file '$path' is format '$format' and this orchestrator writes '$($script:AdgJobStateFormat)'; job '$JobName' will read its whole scope."
        return $null
    }

    $checkpoint = Get-AdgConfigProperty $document 'checkpoint' $null
    return [pscustomobject]@{
        Job           = [string] (Get-AdgConfigProperty $document 'job' $JobName)
        LastRunId     = [string] (Get-AdgConfigProperty $document 'last_run_id' '')
        LastStatus    = [string] (Get-AdgConfigProperty $document 'last_status' '')
        LastMode      = [string] (Get-AdgConfigProperty $document 'last_mode' '')
        LastStartedAt = ConvertTo-AdgInstant ([string] (Get-AdgConfigProperty $document 'last_started_at' ''))
        LastEndedAt   = ConvertTo-AdgInstant ([string] (Get-AdgConfigProperty $document 'last_ended_at' ''))
        LastReconciledAt = ConvertTo-AdgInstant ([string] (Get-AdgConfigProperty $document 'last_reconciled_at' ''))
        ConsecutiveFailures = [int] (Get-AdgConfigProperty $document 'consecutive_failures' 0)
        Checkpoint    = if ($null -eq $checkpoint) { $null } else {
            [pscustomobject]@{
                Kind     = [string] (Get-AdgConfigProperty $checkpoint 'kind' 'opaque')
                Token    = [string] (Get-AdgConfigProperty $checkpoint 'token' '')
                Issuer   = [string] (Get-AdgConfigProperty $checkpoint 'issuer' '')
                IssuedAt = [string] (Get-AdgConfigProperty $checkpoint 'issued_at' '')
            }
        }
        Digests       = [string] (Get-AdgConfigProperty $document 'digest_index' '')
    }
}


function ConvertTo-AdgInstant {
    <#
        .SYNOPSIS
            An RFC 3339 string as a UTC DateTime, or $null.
    #>
    [OutputType([Nullable[datetime]])]
    param([Parameter(Mandatory)][AllowEmptyString()][string] $Text)

    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
    [datetime] $parsed = [datetime]::MinValue
    $styles = [System.Globalization.DateTimeStyles]::AdjustToUniversal -bor [System.Globalization.DateTimeStyles]::AssumeUniversal
    if ([datetime]::TryParse($Text, [System.Globalization.CultureInfo]::InvariantCulture, $styles, [ref] $parsed)) {
        return $parsed
    }
    return $null
}


function Save-AdgJobState {
    <#
        .SYNOPSIS
            Record what a job run did, atomically.
        .DESCRIPTION
            The checkpoint is written only when the run reports one, and a run reports one
            only when it succeeded cleanly. A partial run did not read everything below its
            watermark, so a later run starting there would skip exactly the objects this one
            could not read - and the gap would never be noticed, because nothing would look
            missing. The previous checkpoint is kept instead, which makes the next delta
            re-read a range it has already covered. That is the error worth having.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $StateDirectory,
        [Parameter(Mandatory)][string] $JobName,
        [Parameter(Mandatory)][pscustomobject] $Outcome,
        [pscustomobject] $Previous
    )

    $checkpoint = if ($Outcome.Status -eq 'succeeded' -and $Outcome.Checkpoint) {
        $Outcome.Checkpoint
    }
    elseif ($Previous) { $Previous.Checkpoint }
    else { $null }

    if ($Outcome.Status -ne 'succeeded' -and $Outcome.Checkpoint) {
        Write-Warning "Job '$JobName' finished as '$($Outcome.Status)', so the checkpoint it reached was not recorded. The next run resumes from the previous one and re-reads the overlap."
    }

    $failures = if ($Outcome.Status -eq 'succeeded') { 0 }
    elseif ($Previous) { [int] $Previous.ConsecutiveFailures + 1 }
    else { 1 }

    $reconciledAt = if ($Outcome.Reconciled) { $Outcome.EndedAt }
    elseif ($Previous -and $Previous.LastReconciledAt) { Format-AdgInstant $Previous.LastReconciledAt }
    else { $null }

    $document = [ordered]@{
        schema               = $script:AdgJobStateFormat
        job                  = $JobName
        last_run_id          = $Outcome.RunId
        last_status          = $Outcome.Status
        last_mode            = $Outcome.Mode
        last_started_at      = $Outcome.StartedAt
        last_ended_at        = $Outcome.EndedAt
        last_reconciled_at   = $reconciledAt
        consecutive_failures = $failures
        observation_count    = $Outcome.ObservationCount
        affirmation_count    = $Outcome.AffirmationCount
        error_count          = $Outcome.ErrorCount
        checkpoint           = if ($null -eq $checkpoint) { $null } else {
            [ordered]@{
                kind      = $checkpoint.Kind
                token     = $checkpoint.Token
                issuer    = $checkpoint.Issuer
                issued_at = $checkpoint.IssuedAt
            }
        }
        digest_index         = if ($Outcome.PSObject.Properties['DigestIndex']) { $Outcome.DigestIndex } else { $null }
    }

    $path = Get-AdgJobStatePath -StateDirectory $StateDirectory -JobName $JobName
    $temporary = "$path.tmp"
    $document | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding utf8NoBOM
    Move-Item -LiteralPath $temporary -Destination $path -Force
    return $path
}


function Format-AdgInstant {
    [OutputType([string])]
    param([Parameter(Mandatory)][AllowNull()] $Value)

    if ($null -eq $Value) { return $null }
    if ($Value -is [datetime]) {
        return $Value.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
    }
    return [string] $Value
}


function Enter-AdgJobLock {
    <#
        .SYNOPSIS
            Take a job's lock, or report who holds it.
        .DESCRIPTION
            Two runs of one job at once would both read the same checkpoint, both decide
            they are ahead of it, and leave whichever finished last as the resume point -
            which may be the one that read less. The lock is a file opened for exclusive
            write and held open: the handle is what the lock *is*, so a process that dies
            releases it, which a lock consisting of a file's existence does not.

            Returns $null when the lock is held, so the caller skips the job rather than
            failing the whole invocation. A job already running is not an error.
    #>
    [OutputType([System.IO.FileStream])]
    param(
        [Parameter(Mandatory)][string] $StateDirectory,
        [Parameter(Mandatory)][string] $JobName
    )

    $path = Get-AdgJobLockPath -StateDirectory $StateDirectory -JobName $JobName
    try {
        $stream = [System.IO.File]::Open(
            $path,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None)
    }
    catch [System.IO.IOException] {
        return $null
    }

    $marker = [System.Text.Encoding]::UTF8.GetBytes(
        "pid=$PID host=$env:COMPUTERNAME started=$((Get-Date).ToUniversalTime().ToString('o'))`n")
    $stream.SetLength(0)
    $stream.Write($marker, 0, $marker.Length)
    $stream.Flush()
    return $stream
}


function Exit-AdgJobLock {
    <#
        .SYNOPSIS
            Release a job lock. The file is left behind; the handle is the lock.
    #>
    param([AllowNull()] $Lock)

    if ($null -eq $Lock) { return }
    try { $Lock.Dispose() } catch { Write-Verbose "Releasing a job lock failed: $($_.Exception.Message)" }
}
