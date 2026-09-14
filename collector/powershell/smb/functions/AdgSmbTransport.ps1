<#
    Transport layer: submit a collected run to the ADG API.

    The retry rules are the contract's (collector-protocol.md section 7), not this
    collector's invention, and they depend on payload identity: a retry re-sends the same
    run_id and the same batch_id, so the server recognizes a repeat instead of applying
    the work twice. That is why batch ids are generated once during collection rather than
    at send time.

    Credentials
    -----------

    Since Phase 6A the ingestion endpoints reject an anonymous request. A collector
    normally presents a key in ``X-ADG-Collector-Key``; an operator replaying a payload by
    hand presents a bearer token instead. The headers are built by the entry script and
    threaded through every POST, so a retry carries the same credential as the first
    attempt.
#>

function Invoke-AdgPost {
    <#
        .SYNOPSIS
            POST a payload, retrying transient failures with the identical body.
        .DESCRIPTION
            A 422 is never retried: the payload is wrong, and an identical retry fails
            identically. Everything else - a timeout, a 5xx, a 429 - is retried with
            exponential backoff and jitter, because the failure may have happened after
            the server applied the work, and re-sending the same idempotency key is the
            only safe way to find out.
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
            if ($_.Exception.PSObject.Properties.Name -contains 'Response' -and $_.Exception.Response) {
                $status = [int] $_.Exception.Response.StatusCode
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

function Send-AdgSmbScanRun {
    <#
        .SYNOPSIS
            Submit one collected run: start, then every batch, then completion.
        .DESCRIPTION
            A batch that is rejected as invalid is logged and skipped rather than aborting
            the run - one malformed observation must not discard everything else the scan
            read - but the run is then downgraded to 'partial' and reconciles nothing,
            because part of what it collected never arrived.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $ApiBaseUrl,
        [Parameter(Mandatory)][pscustomobject] $Run,
        [hashtable] $Headers = @{}
    )

    $baseUrl = $ApiBaseUrl.TrimEnd('/')
    $runId = $Run.Start.run_id

    Write-Verbose "Starting run $runId against $baseUrl"
    Invoke-AdgPost -Uri "$baseUrl/api/v1/scan-runs" -Payload $Run.Start -Headers $Headers | Out-Null

    $sent = 0
    $rejected = 0
    foreach ($batch in @($Run.Batches)) {
        try {
            Invoke-AdgPost -Uri "$baseUrl/api/v1/scan-runs/$runId/batches" -Payload $batch -Headers $Headers | Out-Null
            $sent++
        }
        catch {
            $rejected++
            Write-Error "Batch $($batch.sequence) of run $runId was not accepted: $($_.Exception.Message)" -ErrorAction Continue
        }
    }

    $completion = $Run.Completion
    if ($rejected -gt 0) {
        # Claiming complete coverage that was not achieved understates access, which is the
        # most dangerous wrong answer an audit tool can give.
        $completion['status'] = 'partial'
        $completion['reconciled_scopes'] = @()
        $completion['error_count'] = [int] $completion['error_count'] + $rejected
        $completion['errors'] = @(@($completion['errors']) + @(New-AdgCollectorError -Code 'batch_rejected' `
                    -Target $runId -Message "$rejected batch(es) of run $runId were rejected by the API and their observations never arrived."))
    }
    # batch_count reports what actually reached the server; a mismatch with what the
    # server received is how loss is detected, so inflating it would hide the loss.
    $completion['batch_count'] = $sent

    Invoke-AdgPost -Uri "$baseUrl/api/v1/scan-runs/$runId/completion" -Payload $completion -Headers $Headers | Out-Null

    return [pscustomobject]@{
        RunId          = $runId
        BatchesSent    = $sent
        BatchesRejected = $rejected
        Status         = $completion['status']
    }
}
