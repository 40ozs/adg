<#
.SYNOPSIS
    Write the exact payloads the SMB collector would POST for a known fake estate.

.DESCRIPTION
    Test scaffolding, not a collector. It loads the collector module and replaces the
    acquisition layer - the only functions that touch a remote host - with fixed data, so
    the scan runs end to end with no network, no domain, and no file server.

    What it produces is the collector's genuine output: the same builders, the same source
    keys, the same batching, the same completion logic. backend/tests/contracts/
    test_smb_collector.py runs this and validates the result against the published JSON
    Schemas and through the backend models, which independently re-derive every
    source_key. That comparison is the valuable one - it checks the PowerShell
    derivations against the Python ones - and it is what keeps this collector from
    drifting away from the contract.

    Two runs are written:

      run-01  a healthy server, covering ordinary, hidden, administrative, and IPC shares,
              a resolvable trustee, and an orphaned SID;
      run-02  an unreachable server, which must come back 'failed' with an error and
              nothing reconciled.

.PARAMETER OutputDirectory
    Where the payloads are written.

.EXAMPLE
    .\Export-AdgSmbFixturePayload.ps1 -OutputDirectory C:\code\adg\.tmp\smb-fixture
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $OutputDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$moduleRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $moduleRoot 'AdgSmbCollector.psd1') -Force
$module = Get-Module AdgSmbCollector

# Redefine the acquisition layer inside the module's own session state, so the
# orchestration and normalization code calls these instead of the real ones.
#
# The script: scope qualifier is required. Invoking a scriptblock with & creates a
# child scope that is torn down on return, so a plain `function Foo {}` here would be
# defined and immediately discarded - and the collector would quietly go to the real
# network instead.
& $module {
    $script:FixtureShares = @(
        [pscustomobject]@{
            Name = 'Finance'; ShareType = 'FileSystemDirectory'; Special = $false
            Path = 'D:\Shares\Finance'; Description = 'Finance department share'
            ConcurrentUserLimit = 0; CachingMode = 'Manual'
        }
        [pscustomobject]@{
            Name = 'Projects'; ShareType = 'FileSystemDirectory'; Special = $false
            Path = 'E:\Data\Projects'; Description = 'Project working area'
            ConcurrentUserLimit = 25; CachingMode = 'None'
        }
        [pscustomobject]@{
            Name = 'Archive$'; ShareType = 'FileSystemDirectory'; Special = $false
            Path = 'E:\Data\Archive'; Description = 'Hidden archive'
            ConcurrentUserLimit = 0; CachingMode = 'Manual'
        }
        [pscustomobject]@{
            Name = 'C$'; ShareType = 'FileSystemDirectory'; Special = $true
            Path = 'C:\'; Description = 'Default share'
            ConcurrentUserLimit = 0; CachingMode = 'Manual'
        }
        [pscustomobject]@{
            Name = 'IPC$'; ShareType = 'InterprocessCommunication'; Special = $true
            Path = ''; Description = 'Remote IPC'
            ConcurrentUserLimit = 0; CachingMode = 'Manual'
        }
    )

    function script:New-AdgSmbSession {
        param($ComputerName, $Protocol, $TimeoutSeconds, $Credential)
        if ($ComputerName -eq 'FS-OFFLINE') {
            throw 'The RPC server is unavailable. (Exception from HRESULT: 0x800706BA)'
        }
        return [pscustomobject]@{ ComputerName = $ComputerName }
    }

    function script:Remove-AdgSmbSession { param($Session) }

    function script:Get-AdgRemoteComputerFact {
        param($Session)
        return @{
            DnsHostName     = 'fs01.corp.example.com'
            NetbiosName     = 'FS01'
            IsDomainMember  = $true
            OperatingSystem = 'Microsoft Windows Server 2022 Standard'
        }
    }

    function script:Get-AdgRemoteShare {
        param($Session)
        return $script:FixtureShares
    }

    function script:Get-AdgRemoteShareSecurity {
        param($Session, $ShareName)

        # Finance: a resolvable group and an orphaned SID left behind by a deleted group.
        # Projects: a deny entry ahead of an allow, and a generic-rights mask.
        $dacl = switch ($ShareName) {
            'Finance' {
                @(
                    [pscustomobject]@{
                        AceType = 0; AceFlags = 0; AccessMask = 1245631
                        Trustee = [pscustomobject]@{
                            SIDString = 'S-1-5-21-1004336348-1177238915-682003330-1202'
                            Name      = 'Finance-RW'; Domain = 'CORP'
                        }
                    }
                    [pscustomobject]@{
                        AceType = 0; AceFlags = 0; AccessMask = 1179817
                        Trustee = [pscustomobject]@{
                            SIDString = 'S-1-5-21-1004336348-1177238915-682003330-9999'
                            Name      = $null; Domain = $null
                        }
                    }
                )
            }
            'Projects' {
                @(
                    [pscustomobject]@{
                        AceType = 1; AceFlags = 0; AccessMask = 1179817
                        Trustee = [pscustomobject]@{
                            SIDString = 'S-1-5-21-1004336348-1177238915-682003330-1310'
                            Name      = 'Contractors'; Domain = 'CORP'
                        }
                    }
                    [pscustomobject]@{
                        AceType = 0; AceFlags = 0; AccessMask = 268435456
                        Trustee = [pscustomobject]@{ SIDString = 'S-1-1-0'; Name = 'Everyone'; Domain = '' }
                    }
                    # An audit entry, which must not appear in the output at all.
                    [pscustomobject]@{
                        AceType = 2; AceFlags = 0; AccessMask = 2032127
                        Trustee = [pscustomobject]@{ SIDString = 'S-1-1-0'; Name = 'Everyone'; Domain = '' }
                    }
                )
            }
            default { @() }
        }

        return @{ Dacl = $dacl; DaclPresent = $true }
    }

    function script:Get-AdgRemoteShareAccess {
        param($Session, $ShareName)
        throw "The fixture has no level-based reading for '$ShareName'."
    }
}

if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}

$settings = Import-AdgSmbTarget -Server 'FS01', 'FS-OFFLINE'
$settings.RetryCount = 0
$settings.RetryDelaySeconds = 0
# Small enough that the healthy server needs more than one batch, so the test exercises
# batch sequencing and the is_final marker rather than a single-batch special case.
$settings.BatchSize = 5

$runs = Invoke-AdgSmbScan -Settings $settings -RunPerServer -CollectorHost 'COLLECTOR01' -CollectorVersion '0.1.0'

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

    Write-Host ("{0}: {1}, {2} observation(s) in {3} batch(es), {4} error(s)." -f `
            $prefix, $run.Completion.status, $run.Completion.observation_count, `
            $run.Batches.Count, $run.Completion.error_count)
}

Write-Host "Fixture payloads written to $OutputDirectory"
