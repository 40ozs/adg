<#
    Transport layer: submit a run to the ADG API as the walk produces it.

    The retry rules are the contract's (collector-protocol.md section 7), not this
    collector's invention, and they depend on payload identity: a retry re-sends the same
    run_id and the same batch_id, so the server recognizes a repeat instead of applying the
    work twice. That is why batch ids are generated during collection rather than at send
    time.

    Credentials
    -----------

    Since Phase 6A the ingestion endpoints reject an anonymous request. A collector normally
    presents a key in ``X-ADG-Collector-Key``; an operator replaying a payload by hand
    presents a bearer token instead. The headers are built once, up front, and threaded
    through every POST, so a retry carries the same credential as the first attempt.

    Built here rather than borrowed from AdgCollector.Common: this collector is a standalone
    module by design and imports nothing from the others.

    ------------------------------------------------------------------------------------
    Streaming changes what a transport is

    Phase 3A collected a whole run and then posted it, so the sender could be one function
    over a finished object. A tree walk produces batches for hours, and a batch has to reach
    the server before the checkpoint that assumes it did - so the transport is now a small
    piece of state the scan streams into, rather than something handed a completed run.

    One consequence worth naming: the completion envelope is mutated on its way out when
    batches were rejected. The scan builds it from what the walk found; the transport knows
    what actually arrived, and those are different facts. A run whose ACEs are sitting on
    the floor is not 'succeeded' whatever the walk thought, and it must not reconcile.
#>

function Invoke-AdgPost {
    <#
        .SYNOPSIS
            POST a payload, retrying transient failures with the identical body.
        .DESCRIPTION
            A 422 is never retried: the payload is wrong, and an identical retry fails
            identically. Everything else - a timeout, a 5xx, a 429 - is retried with
            exponential backoff and jitter, because the failure may have happened after the
            server applied the work, and re-sending the same idempotency key is the only
            safe way to find out.
    #>
    param(
        [Parameter(Mandatory)][string] $Uri,
        [Parameter(Mandatory)] $Payload,
        [hashtable] $Headers = @{},
        [ValidateRange(1, 10)][int] $MaxAttempts = 5,
        [int] $TimeoutSeconds = 100
    )

    $json = $Payload | ConvertTo-Json -Depth 12 -Compress

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            return Invoke-RestMethod -Method Post -Uri $Uri -Body $json -ContentType 'application/json' `
                -Headers $Headers -TimeoutSec $TimeoutSeconds -ErrorAction Stop
        }
        catch {
            $status = 0
            $response = $_.Exception.PSObject.Properties['Response']
            if ($null -ne $response -and $null -ne $response.Value) {
                $status = [int] $response.Value.StatusCode
            }

            if ($status -eq 401 -or $status -eq 403) {
                # Never retried. A credential the server refuses is refused identically on
                # every attempt, and retrying only delays the message that says so.
                throw "The ADG API refused the collector's credential ($status) for $Uri. Set a collector key (ADG_COLLECTOR_API_KEYS on the server, -CollectorKey here). $($_.Exception.Message)"
            }
            if ($status -eq 422) {
                throw "The ADG API rejected the payload as invalid (422); retrying cannot help. $($_.Exception.Message)"
            }
            if ($status -eq 409) {
                throw "The ADG API reported a conflict (409) for $Uri. $($_.Exception.Message)"
            }
            if ($attempt -eq $MaxAttempts) { throw }

            $delay = [Math]::Pow(2, $attempt) + (Get-Random -Minimum 0.0 -Maximum 1.0)
            Write-Warning "POST $Uri failed (attempt $attempt/$MaxAttempts); retrying in $([Math]::Round($delay, 1))s."
            Start-Sleep -Seconds $delay
        }
    }
}

function New-AdgNtfsTransport {
    <#
        .SYNOPSIS
            The submission sink a streaming scan writes into.
        .DESCRIPTION
            Counts what reached the server, which is not the same as what the walk produced.
            batch_count on the completion reports the first number, because a mismatch with
            what the server received is how loss is detected - and inflating it would hide
            exactly the loss it exists to reveal.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $ApiBaseUrl,
        [string] $CollectorKey,
        [string] $AuthenticationToken
    )

    # Built once and carried on the transport, so every POST in a scan that runs for hours
    # sends the same credential -- including the retries.
    $headers = @{}
    if ($AuthenticationToken) { $headers['Authorization'] = "Bearer $AuthenticationToken" }
    if ($CollectorKey) { $headers['X-ADG-Collector-Key'] = $CollectorKey }
    if ($headers.Count -eq 0) {
        Write-Warning 'No collector key or authentication token was supplied. The ADG API rejects anonymous ingestion; set CollectorKey or AuthenticationToken.'
    }

    return [pscustomobject]@{
        BaseUrl         = $ApiBaseUrl.TrimEnd('/')
        RunId           = $null
        Headers         = $headers
        BatchesSent     = 0
        BatchesRejected = 0
    }
}

function Send-AdgNtfsStart {
    <#
        .SYNOPSIS
            Open a run on the server.
    #>
    param(
        [Parameter(Mandatory)][pscustomobject] $Transport,
        [Parameter(Mandatory)] $Start
    )

    $Transport.RunId = [string] $Start['run_id']
    Write-Verbose "Starting run $($Transport.RunId) against $($Transport.BaseUrl)"
    Invoke-AdgPost -Uri "$($Transport.BaseUrl)/api/v1/scan-runs" -Payload $Start -Headers $Transport.Headers | Out-Null
}

function Send-AdgNtfsBatch {
    <#
        .SYNOPSIS
            Submit one batch, counting a rejection rather than aborting the run.
        .DESCRIPTION
            One malformed observation must not discard everything else the scan has read, so
            a rejected batch is logged and the walk carries on. The cost is recorded and paid
            at the completion: the run is downgraded to 'partial' and reconciles nothing,
            because part of what it collected never arrived.
    #>
    param(
        [Parameter(Mandatory)][pscustomobject] $Transport,
        [Parameter(Mandatory)] $Batch
    )

    try {
        Invoke-AdgPost -Uri "$($Transport.BaseUrl)/api/v1/scan-runs/$($Transport.RunId)/batches" -Payload $Batch -Headers $Transport.Headers | Out-Null
        $Transport.BatchesSent++
    }
    catch {
        $Transport.BatchesRejected++
        Write-Error "Batch $($Batch['sequence']) of run $($Transport.RunId) was not accepted: $($_.Exception.Message)" -ErrorAction Continue
    }
}

function Send-AdgNtfsCompletion {
    <#
        .SYNOPSIS
            Close a run, correcting the status for anything that never arrived.
        .DESCRIPTION
            The completion is built by the scan from what the walk found; this is where what
            actually reached the server is folded in. A run that lost a batch is downgraded
            to 'partial' and its reconciled scopes are dropped - which matters far more now
            than it did in Phase 3A, where no run could reconcile anything: a reconciled
            scope is permission for the backend to mark unseen objects absent, and a scope
            reconciled while a batch of ACEs sits on the floor would mark live access as
            revoked.
    #>
    param(
        [Parameter(Mandatory)][pscustomobject] $Transport,
        [Parameter(Mandatory)] $Completion
    )

    if ($Transport.BatchesRejected -gt 0) {
        $Completion['status'] = 'partial'
        $Completion['reconciled_scopes'] = @()
        $Completion['error_count'] = [int] $Completion['error_count'] + $Transport.BatchesRejected
        $Completion['errors'] = @(@($Completion['errors']) + @(New-AdgCollectorError -Code 'batch_rejected' `
                    -Target $Transport.RunId -Message "$($Transport.BatchesRejected) batch(es) of run $($Transport.RunId) were rejected by the API and their observations never arrived. The run is downgraded to partial and reconciles nothing: a scope reconciled while part of its evidence is missing would mark live permissions as revoked."))
    }
    # What actually reached the server, never what was produced.
    $Completion['batch_count'] = $Transport.BatchesSent

    Invoke-AdgPost -Uri "$($Transport.BaseUrl)/api/v1/scan-runs/$($Transport.RunId)/completion" -Payload $Completion -Headers $Transport.Headers | Out-Null
}

function New-AdgNtfsFileSink {
    <#
        .SYNOPSIS
            The dry-run sink: the exact payloads a real run would POST, written to disk.
        .DESCRIPTION
            Written as they are produced rather than collected and dumped at the end, so a
            dry run over a large tree costs the same memory as a real one - and so the files
            on disk are a faithful record of a scan that was interrupted, rather than
            nothing at all.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $OutputDirectory,
        [string] $Prefix = 'run-01'
    )

    if (-not (Test-Path -LiteralPath $OutputDirectory)) {
        New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    }

    return [pscustomobject]@{
        OutputDirectory = $OutputDirectory
        Prefix          = $Prefix
        BatchesWritten  = 0
    }
}

function Write-AdgNtfsPayload {
    <#
        .SYNOPSIS
            Write one payload of a dry run to its own file.
    #>
    param(
        [Parameter(Mandatory)][pscustomobject] $Sink,
        [Parameter(Mandatory)] $Payload,
        [Parameter(Mandatory)][ValidateSet('start', 'batch', 'completion')][string] $Kind
    )

    $name = switch ($Kind) {
        'start' { "$($Sink.Prefix)-start.json" }
        'completion' { "$($Sink.Prefix)-completion.json" }
        default {
            $Sink.BatchesWritten++
            '{0}-batch-{1:d3}.json' -f $Sink.Prefix, $Payload['sequence']
        }
    }

    $Payload | ConvertTo-Json -Depth 12 |
        Set-Content -LiteralPath (Join-Path $Sink.OutputDirectory $name) -Encoding utf8
}
