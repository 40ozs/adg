<#
.SYNOPSIS
    Write the exact payloads the NTFS collector would POST for a known fake estate.

.DESCRIPTION
    Test scaffolding, not a collector. It loads the collector module and replaces the
    acquisition layer - the only functions that touch a file system - with fixed data, so
    the scan runs end to end with no share, no file server, and no ACL.

    What it produces is the collector's genuine output: the same builders, the same source
    keys, the same ACL normal form, the same batching, the same completion logic.
    backend/tests/contracts/test_ntfs_collector.py runs this and validates the result
    against the published JSON Schemas, through the backend models - which independently
    re-derive every source_key - and against the backend's own ACL normalizer, which
    independently re-derives every acl_hash. Those comparisons are the valuable ones: they
    check the PowerShell derivations against the Python ones, and they are what keeps this
    collector from drifting away from the contract.

    Five runs are written:

      run-01  \\FS01\Finance - an ordinary root with inheritance intact: an inherited entry,
              an explicit one, a Deny ahead of an Allow, an INHERIT_ONLY entry, a
              generic-rights mask, and an orphaned SID left behind by a deleted group.
              Everything was readable, so it carries an acl_hash;
      run-02  \\FS01\Locked - a protected DACL (broken inheritance) that is also, by being
              empty, a directory nobody has access to through its DACL;
      run-03  \\FS01\Wide - a NULL DACL, which grants every user full access and must never
              be reported as an empty ACE list;
      run-04  \\FS01\Odd - a DACL holding an entry type contract v1 cannot express. The
              entry becomes an error, the run is partial, and the resource carries **no**
              acl_hash: a digest over the part that was readable is indistinguishable from
              a digest of the whole DACL, and comparing one to a parent's would answer the
              boundary question wrong without ever looking wrong;
      run-05  \\FS02\Missing - a root that is not there, which must come back 'failed' with
              an error, no observations, and nothing reconciled.

.PARAMETER OutputDirectory
    Where the payloads are written.

.EXAMPLE
    .\Export-AdgNtfsFixturePayload.ps1 -OutputDirectory C:\code\adg\.tmp\ntfs-fixture
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $OutputDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$moduleRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $moduleRoot 'AdgNtfsCollector.psd1') -Force
$module = Get-Module AdgNtfsCollector

# Redefine the acquisition layer inside the module's own session state, so the orchestration
# and normalization code calls these instead of the real ones.
#
# The script: scope qualifier is required. Invoking a scriptblock with & creates a child
# scope that is torn down on return, so a plain `function Foo {}` here would be defined and
# immediately discarded - and the collector would quietly go to the real file system.
& $module {
    $script:FixtureSecurity = @{
        '\\fs01\finance' = @{
            OwnerSid      = 'S-1-5-21-1004336348-1177238915-682003330-512'
            GroupSid      = 'S-1-5-21-1004336348-1177238915-682003330-513'
            DaclPresent   = $true
            DaclProtected = $false
            Ace           = @(
                # A Deny ahead of the Allow entries, which is where canonical ordering puts
                # it and where it has to stay: moved below them it would stop denying.
                [pscustomobject]@{
                    AceType = 'AccessDenied'; AceFlags = 3; AccessMask = 1179817
                    TrusteeSid = 'S-1-5-21-1004336348-1177238915-682003330-1310'
                    TrusteeName = 'CORP\Contractors'
                }
                [pscustomobject]@{
                    AceType = 'AccessAllowed'; AceFlags = 3; AccessMask = 1245631
                    TrusteeSid = 'S-1-5-21-1004336348-1177238915-682003330-1202'
                    TrusteeName = 'CORP\Finance-RW'
                }
                # Inherited from the volume root: flags carry 0x10.
                [pscustomobject]@{
                    AceType = 'AccessAllowed'; AceFlags = 19; AccessMask = 2032127
                    TrusteeSid = 'S-1-5-32-544'; TrusteeName = 'BUILTIN\Administrators'
                }
                # INHERIT_ONLY (0x08): grants nothing on this directory, only on children.
                # Reported all the same - dropping it would hide a grant that reaches every
                # subdirectory.
                [pscustomobject]@{
                    AceType = 'AccessAllowed'; AceFlags = 11; AccessMask = 268435456
                    TrusteeSid = 'S-1-3-0'; TrusteeName = 'CREATOR OWNER'
                }
                # An orphaned SID: a group that was deleted while its ACE stayed behind.
                [pscustomobject]@{
                    AceType = 'AccessAllowed'; AceFlags = 3; AccessMask = 1179817
                    TrusteeSid = 'S-1-5-21-1004336348-1177238915-682003330-9999'
                    TrusteeName = $null
                }
            )
        }
        '\\fs01\odd'     = @{
            OwnerSid      = 'S-1-5-32-544'
            GroupSid      = $null
            DaclPresent   = $true
            DaclProtected = $false
            Ace           = @(
                [pscustomobject]@{
                    AceType = 'AccessAllowed'; AceFlags = 3; AccessMask = 1179817
                    TrusteeSid = 'S-1-5-11'; TrusteeName = 'NT AUTHORITY\Authenticated Users'
                }
                # A callback ACE. Windows evaluates it through a conditional expression the
                # contract has no field for, so reporting it as an ordinary grant would
                # overstate access and dropping it silently would understate coverage.
                [pscustomobject]@{
                    AceType = 'AccessAllowedCallback'; AceFlags = 3; AccessMask = 2032127
                    TrusteeSid = 'S-1-1-0'; TrusteeName = 'Everyone'
                }
            )
        }
        '\\fs01\locked'  = @{
            OwnerSid      = 'S-1-5-32-544'
            GroupSid      = $null
            DaclPresent   = $true
            DaclProtected = $true
            Ace           = @()
        }
        '\\fs01\wide'    = @{
            OwnerSid      = 'S-1-5-32-544'
            GroupSid      = $null
            DaclPresent   = $false
            DaclProtected = $false
            Ace           = @()
        }
    }

    function script:Test-AdgResourceExists {
        param($Path)
        return $script:FixtureSecurity.ContainsKey($Path.ToLowerInvariant())
    }

    function script:Get-AdgDirectorySecurity {
        param($Path)
        $key = $Path.ToLowerInvariant()
        if (-not $script:FixtureSecurity.ContainsKey($key)) {
            throw "Access to the path '$Path' is denied."
        }
        return $script:FixtureSecurity[$key]
    }

    function script:Resolve-AdgTrusteeName {
        param($Sid)
        throw 'The fixture resolves names from the descriptor, never by a second lookup.'
    }
}

if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}

$settings = Import-AdgNtfsTarget -ShareRoot '\\FS01\Finance', '\\FS01\Locked', '\\FS01\Wide', '\\FS01\Odd', '\\FS02\Missing'
$settings.RetryCount = 0
$settings.RetryDelaySeconds = 0
# Small enough that Finance alone exceeds it, so the exporter proves the batcher keeps a
# directory's observations together rather than cutting at the requested size.
$settings.BatchSize = 3

$runs = Invoke-AdgNtfsScan -Settings $settings -RunPerShareRoot -CollectorHost 'COLLECTOR01' -CollectorVersion '0.1.0'

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
        @($run.Batches).Count, $run.Completion.error_count)
}

Write-Host "Fixture payloads written to $OutputDirectory"
