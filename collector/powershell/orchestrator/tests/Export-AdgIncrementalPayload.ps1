#Requires -Version 7.0
<#
.SYNOPSIS
    Write the contract 1.4 payloads a delta run and an affirming run produce.

.DESCRIPTION
    A fixture generator, not a collector. It builds each envelope with the same functions
    the collectors use, so the files it writes are the bytes a real run would POST, and
    backend/tests/contracts/test_incremental_payloads.py validates them against the
    published JSON Schemas and the backend models.

    That cross-check is the point. Every field contract 1.4 added exists on both sides of
    the wire, and the two sides are written in different languages by different code. The
    only thing that stops them drifting is a test that runs one and validates it with the
    other.

.PARAMETER OutputDirectory
    Where the JSON files are written. Created if it does not exist.

.EXAMPLE
    .\Export-AdgIncrementalPayload.ps1 -OutputDirectory C:\code\adg\.tmp\incremental
#>
[CmdletBinding()]
param([Parameter(Mandatory)][string] $OutputDirectory)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$collectorRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Import-Module (Join-Path $collectorRoot 'common/AdgCollector.Common.psd1') -Force

if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}

function Write-Payload {
    param([string] $Name, [System.Collections.IDictionary] $Payload)
    $path = Join-Path $OutputDirectory $Name
    $Payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $path -Encoding utf8NoBOM
    Write-Host "wrote $path"
}

$runId = '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31'
$batchId = 'b1a7c0de-1111-4222-8333-444455556666'
$domainSid = 'S-1-5-21-1004336348-1177238915-682003330'
$startedAt = '2026-03-02T09:00:00Z'
$completedAt = '2026-03-02T09:05:00Z'

# --- A delta run of the ad-principals job ----------------------------------------------
#
# The issuer is dsServiceName and invocationId joined: the first changes when the collector
# binds a different domain controller, the second when the same one is restored from backup
# and starts reissuing USNs it has already handed out.
$issuer = 'CN=NTDS Settings,CN=DC01,CN=Servers,CN=Site,CN=Sites,CN=Configuration,DC=corp,DC=example,DC=com|2f0f9a3c-7c4e-4c0e-9a02-6b5f0a1f9d11'
$baseline = New-AdgCheckpoint -Kind usn -Token '184213' -Issuer $issuer -IssuedAt '2026-03-01T09:05:00Z'
$result = New-AdgCheckpoint -Kind usn -Token '184987' -Issuer $issuer -IssuedAt $completedAt

Write-Payload 'delta-start.json' (New-AdgScanRunStart -RunId $runId `
        -Collector 'active_directory' -CollectorHost 'COLLECTOR01' `
        -Method 'System.DirectoryServices.Protocols.LdapConnection' -CollectorVersion '0.1.0' `
        -Target 'DC=corp,DC=example,DC=com' -StartedAt $startedAt `
        -Scopes @(New-AdgScope -Kind 'domain' -Key $domainSid) `
        -Incremental $true -Mode 'delta' -Job 'ad-principals' -Baseline $baseline `
        -Notes 'reads only the principals of the domain; marked incremental so it cannot reconcile the domain scope.')

$principal = New-AdgPrincipalObservation -RunId $runId -ObservedAt $startedAt `
    -Sid "$domainSid-1104" -PrincipalKind 'user' -DisplayName 'Alice Anderson' `
    -SamAccountName 'alice' -DistinguishedName 'CN=Alice,OU=Staff,DC=corp,DC=example,DC=com' `
    -Enabled $true

Write-Payload 'delta-batch.json' (New-AdgObservationBatch -RunId $runId -BatchId $batchId `
        -Sequence 1 -Observations @($principal) -IsFinal $true `
        -Checkpoint (New-AdgCheckpoint -Kind usn -Token '184987' -Issuer $issuer -IssuedAt $completedAt))

Write-Payload 'delta-completion.json' (New-AdgScanRunCompletion -RunId $runId -Status 'succeeded' `
        -CompletedAt $completedAt -BatchCount 1 -ObservationCount 1 -Checkpoint $result)

# --- An affirming NTFS run ---------------------------------------------------------------
#
# Two directories were re-read. One changed and is sent in full; the other is affirmed with
# the digest this scan computed from the descriptor it just read. The run still enumerated
# its whole scope, so it reconciles.
$ntfsRun = 'c3d4e5f6-1111-4222-8333-444455556666'
$ntfsBatch = 'd4e5f6a7-2222-4333-8444-555566667777'
$tree = '\\fs01\finance'

Write-Payload 'affirm-start.json' (New-AdgScanRunStart -RunId $ntfsRun `
        -Collector 'ntfs' -CollectorHost 'COLLECTOR01' `
        -Method 'DirectorySecurity.GetSecurityDescriptorBinaryForm' -CollectorVersion '0.1.0' `
        -Target '\\FS01\Finance' -StartedAt $startedAt `
        -Scopes @(New-AdgScope -Kind 'directory_tree' -Key $tree) `
        -Incremental $false -Mode 'full' -Job 'ntfs-deep-scan')

$affirmations = @(
    New-AdgAffirmation -SourceKey 'resource|\\fs01\finance\reports' `
        -Digest '3b1f0c9d5e2a47b8c6d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6' `
        -ObservedAt $startedAt
    New-AdgAffirmation -SourceKey 'resource|\\fs01\finance\archive' `
        -Digest '9f2c1a4b5d63f0879a213e7c0d48b6f51122334455667788990011223344556e' `
        -ObservedAt $startedAt
)

# A batch of affirmations alone, which is what a quiet tree produces and what the batch
# envelope had to be relaxed to allow.
Write-Payload 'affirm-batch.json' (New-AdgObservationBatch -RunId $ntfsRun -BatchId $ntfsBatch `
        -Sequence 1 -Observations @() -Affirmations $affirmations -IsFinal $true)

Write-Payload 'affirm-completion.json' (New-AdgScanRunCompletion -RunId $ntfsRun `
        -Status 'succeeded' -CompletedAt $completedAt -BatchCount 1 -ObservationCount 0 `
        -AffirmationCount $affirmations.Count `
        -ReconciledScopes @(New-AdgScope -Kind 'directory_tree' -Key $tree))

Write-Host 'done'
