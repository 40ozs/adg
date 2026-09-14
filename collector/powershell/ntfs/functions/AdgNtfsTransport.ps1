<#
    Transport layer: submit a collected run to the ADG API.

    The retry rules are the contract's (collector-protocol.md section 7), not this
    collector's invention, and they depend on payload identity: a retry re-sends the same
    run_id and the same batch_id, so the server recognizes a repeat instead of applying the
    work twice. That is why batch ids are generated once during collection rather than at
    send time.
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
        [ValidateRange(1, 10)][int] $MaxAttempts = 5,
        [int] $TimeoutSeconds = 100
    )

    $json = $Payload | ConvertTo-Json -Depth 12 -Compress

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            return Invoke-RestMethod -Method Post -Uri $Uri -Body $json -ContentType 'application/json' `
                -TimeoutSec $TimeoutSeconds -ErrorAction Stop
        }
        catch {
            $status = 0
            $response = $_.Exception.PSObject.Properties['Response']
            if ($null -ne $response -and $null -ne $response.Value) {
                $status = [int] $response.Value.StatusCode
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

function Send-AdgNtfsScanRun {
    <#
        .SYNOPSIS
            Submit one collected run: start, then every batch, then completion.
        .DESCRIPTION
            A batch rejected as invalid is logged and skipped rather than aborting the run -
            one malformed observation must not discard everything else the scan read - but
            the run is then downgraded to 'partial', because part of what it collected never
            arrived.

            Downgrading changes nothing about reconciliation here, since this collector's
            runs are incremental and reconcile nothing regardless. It still matters: status
            is what an operator reads, and a run reported 'succeeded' while a batch of ACEs
            was on the floor would misdescribe the estate's coverage.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $ApiBaseUrl,
        [Parameter(Mandatory)][pscustomobject] $Run
    )

    $baseUrl = $ApiBaseUrl.TrimEnd('/')
    $runId = $Run.Start.run_id

    Write-Verbose "Starting run $runId against $baseUrl"
    Invoke-AdgPost -Uri "$baseUrl/api/v1/scan-runs" -Payload $Run.Start | Out-Null

    $sent = 0
    $rejected = 0
    foreach ($batch in @($Run.Batches)) {
        try {
            Invoke-AdgPost -Uri "$baseUrl/api/v1/scan-runs/$runId/batches" -Payload $batch | Out-Null
            $sent++
        }
        catch {
            $rejected++
            Write-Error "Batch $($batch.sequence) of run $runId was not accepted: $($_.Exception.Message)" -ErrorAction Continue
        }
    }

    $completion = $Run.Completion
    if ($rejected -gt 0) {
        $completion['status'] = 'partial'
        $completion['reconciled_scopes'] = @()
        $completion['error_count'] = [int] $completion['error_count'] + $rejected
        $completion['errors'] = @(@($completion['errors']) + @(New-AdgCollectorError -Code 'batch_rejected' `
                    -Target $runId -Message "$rejected batch(es) of run $runId were rejected by the API and their observations never arrived."))
    }
    # batch_count reports what actually reached the server; a mismatch with what the server
    # received is how loss is detected, so inflating it would hide the loss.
    $completion['batch_count'] = $sent

    Invoke-AdgPost -Uri "$baseUrl/api/v1/scan-runs/$runId/completion" -Payload $completion | Out-Null

    return [pscustomobject]@{
        RunId           = $runId
        BatchesSent     = $sent
        BatchesRejected = $rejected
        Status          = $completion['status']
    }
}
