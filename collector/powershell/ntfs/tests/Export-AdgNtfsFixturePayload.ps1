<#
.SYNOPSIS
    Write the exact payloads the NTFS collector would POST for a known fake estate.

.DESCRIPTION
    Test scaffolding, not a collector. It loads the collector module and replaces the
    acquisition layer - the only functions that touch a file system - with fixed data, so
    the walk runs end to end with no share, no file server, and no ACL.

    What it produces is the collector's genuine output: the same builders, the same source
    keys, the same ACL normal form, the same inheritance projection, the same batching, the
    same completion logic. backend/tests/contracts/test_ntfs_collector.py runs this and
    validates the result against the published JSON Schemas, through the backend models -
    which independently re-derive every source_key - against the backend's own ACL
    normalizer, which independently re-derives every acl_hash, and against the backend's
    inheritance projection, which independently re-derives every boundary verdict. Those
    comparisons are the valuable ones: they check three PowerShell derivations against their
    Python counterparts, and they are what keeps this collector from drifting away from the
    contract.

    Six runs are written:

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
              a digest of the whole DACL, and comparing one to a parent's projection would
              answer the boundary question wrong without ever looking wrong;
      run-05  \\FS02\Missing - a root that is not there, which must come back 'failed' with
              an error, no observations, and nothing reconciled;
      run-06  \\FS01\Projects - a three-level tree, and the only run here that exercises the
              boundary comparison. It holds a directory that inherits cleanly (no boundary),
              a grandchild that also inherits cleanly (which is what proves the projection
              stays stable below the first level), a child with an added explicit entry, a
              child that blocks inheritance, and - with file scanning on - a file that
              inherited cleanly through the *object* projection rather than the container
              one. Nothing was skipped, so this is also the only run that reconciles its
              scope.

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
$modulePath = Join-Path $moduleRoot 'AdgNtfsCollector.psd1'
Import-Module $modulePath -Force
$module = Get-Module AdgNtfsCollector

# Redefine the acquisition layer inside the module's own session state, so the orchestration
# and normalization code calls these instead of the real ones.
#
# The script: scope qualifier is required. Invoking a scriptblock with & creates a child
# scope that is torn down on return, so a plain `function Foo {}` here would be defined and
# immediately discarded - and the collector would quietly go to the real file system.
& $module {
    # The Projects tree's DACLs, written out in full rather than derived, so the fixture
    # states what Windows actually stores rather than what the collector believes it would.
    $projectsRoot = @(
        [pscustomobject]@{
            AceType = 'AccessAllowed'; AceFlags = 19; AccessMask = 2032127
            TrusteeSid = 'S-1-5-32-544'; TrusteeName = 'BUILTIN\Administrators'
        }
        [pscustomobject]@{
            AceType = 'AccessAllowed'; AceFlags = 3; AccessMask = 1245631
            TrusteeSid = 'S-1-5-21-1004336348-1177238915-682003330-1202'
            TrusteeName = 'CORP\Finance-RW'
        }
    )
    # What a child container of that root inherits: INHERITED added, everything else the
    # same. The two entries differ from the parent's by exactly one bit, which is why a
    # naive parent-to-child hash comparison would call every directory a boundary.
    $projectsInherited = @(
        [pscustomobject]@{
            AceType = 'AccessAllowed'; AceFlags = 19; AccessMask = 2032127
            TrusteeSid = 'S-1-5-32-544'; TrusteeName = 'BUILTIN\Administrators'
        }
        [pscustomobject]@{
            AceType = 'AccessAllowed'; AceFlags = 19; AccessMask = 1245631
            TrusteeSid = 'S-1-5-21-1004336348-1177238915-682003330-1202'
            TrusteeName = 'CORP\Finance-RW'
        }
    )
    # What a child *file* inherits: every inheritance flag stripped, because a file has
    # nothing below it to pass them to.
    $projectsFile = @(
        [pscustomobject]@{
            AceType = 'AccessAllowed'; AceFlags = 16; AccessMask = 2032127
            TrusteeSid = 'S-1-5-32-544'; TrusteeName = 'BUILTIN\Administrators'
        }
        [pscustomobject]@{
            AceType = 'AccessAllowed'; AceFlags = 16; AccessMask = 1245631
            TrusteeSid = 'S-1-5-21-1004336348-1177238915-682003330-1202'
            TrusteeName = 'CORP\Finance-RW'
        }
    )

    $script:FixtureSecurity = @{
        '\\fs01\finance'                    = @{
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
        '\\fs01\odd'                        = @{
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
        '\\fs01\locked'                     = @{
            OwnerSid      = 'S-1-5-32-544'
            GroupSid      = $null
            DaclPresent   = $true
            DaclProtected = $true
            Ace           = @()
        }
        '\\fs01\wide'                       = @{
            OwnerSid      = 'S-1-5-32-544'
            GroupSid      = $null
            DaclPresent   = $false
            DaclProtected = $false
            Ace           = @()
        }

        # --- run-06: a tree, and the boundaries in it ---------------------------------
        '\\fs01\projects'                   = @{
            OwnerSid = 'S-1-5-32-544'; GroupSid = $null
            DaclPresent = $true; DaclProtected = $false; Ace = $projectsRoot
        }
        # Inherits cleanly: no boundary.
        '\\fs01\projects\apollo'            = @{
            OwnerSid = 'S-1-5-21-1004336348-1177238915-682003330-1105'; GroupSid = $null
            DaclPresent = $true; DaclProtected = $false; Ace = $projectsInherited
        }
        # Also inherits cleanly, one level further down. A clean child and a clean grandchild
        # carry the identical DACL, which is what makes the projection stable below the first
        # level - and is the property a hand-written comparison gets wrong. Its owner differs
        # from its parent's, which must not make it a boundary: every folder under a root is
        # owned by whoever created it while sharing one inherited DACL.
        '\\fs01\projects\apollo\designs'    = @{
            OwnerSid = 'S-1-5-21-1004336348-1177238915-682003330-1105'; GroupSid = $null
            DaclPresent = $true; DaclProtected = $false; Ace = $projectsInherited
        }
        # An explicit entry added here: a boundary, reason acl_differs_from_parent.
        '\\fs01\projects\gemini'            = @{
            OwnerSid = 'S-1-5-32-544'; GroupSid = $null
            DaclPresent = $true; DaclProtected = $false
            Ace         = @($projectsInherited + @([pscustomobject]@{
                        AceType = 'AccessAllowed'; AceFlags = 3; AccessMask = 1179817
                        TrusteeSid = 'S-1-5-21-1004336348-1177238915-682003330-1203'
                        TrusteeName = 'CORP\Gemini-RW'
                    }))
        }
        # Inheritance blocked: a boundary, reason protected_dacl, whatever the entries say.
        '\\fs01\projects\sealed'            = @{
            OwnerSid = 'S-1-5-32-544'; GroupSid = $null
            DaclPresent = $true; DaclProtected = $true
            Ace         = @([pscustomobject]@{
                    AceType = 'AccessAllowed'; AceFlags = 3; AccessMask = 2032127
                    TrusteeSid = 'S-1-5-32-544'; TrusteeName = 'BUILTIN\Administrators'
                })
        }
        # A file that inherited cleanly. Compared against the object projection it is not a
        # boundary; compared against the container one it would be, and so would every file
        # in the estate.
        '\\fs01\projects\apollo\readme.txt' = @{
            OwnerSid = 'S-1-5-32-544'; GroupSid = $null
            DaclPresent = $true; DaclProtected = $false; Ace = $projectsFile
        }
    }

    # Which directories hold which children. A path absent from here has none.
    $script:FixtureChildren = @{
        '\\fs01\projects'        = @('Apollo', 'Gemini', 'Sealed')
        '\\fs01\projects\apollo' = @('Designs')
    }
    $script:FixtureFiles = @{
        '\\fs01\projects\apollo' = @('readme.txt')
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

    function script:Get-AdgFileSecurity {
        param($Path)
        return Get-AdgDirectorySecurity -Path $Path
    }

    function script:Get-AdgChildDirectory {
        param($Path)
        $key = $Path.ToLowerInvariant()
        if (-not $script:FixtureSecurity.ContainsKey($key)) {
            throw "Access to the path '$Path' is denied."
        }
        $names = if ($script:FixtureChildren.ContainsKey($key)) { $script:FixtureChildren[$key] } else { @() }
        $children = foreach ($name in $names) {
            [pscustomobject]@{ Name = $name; Path = "$Path\$name"; IsReparsePoint = $false; LinkTarget = $null }
        }
        return , @($children)
    }

    function script:Get-AdgChildFile {
        param($Path)
        $key = $Path.ToLowerInvariant()
        $names = if ($script:FixtureFiles.ContainsKey($key)) { $script:FixtureFiles[$key] } else { @() }
        $files = foreach ($name in $names) {
            [pscustomobject]@{ Name = $name; Path = "$Path\$name"; IsReparsePoint = $false }
        }
        return , @($files)
    }

    function script:Resolve-AdgTrusteeName {
        param($Sid)
        throw 'The fixture resolves names from the descriptor, never by a second lookup.'
    }
}

if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}

$settings = Import-AdgNtfsTarget -ScanRoot '\\FS01\Finance', '\\FS01\Locked', '\\FS01\Wide', `
    '\\FS01\Odd', '\\FS02\Missing', '\\FS01\Projects'
$settings.RetryCount = 0
$settings.RetryDelaySeconds = 0
# Small enough that Finance alone exceeds it, so the exporter proves the batch writer keeps
# a resource's observations together rather than cutting at the requested size.
$settings.BatchSize = 3
# The one run that needs it. Files are off by default everywhere else, and the fixture holds
# exactly one, so this costs nothing and pins the object projection.
$settings.IncludeFiles = $true

$state = [pscustomobject]@{ Index = 0; Sink = $null }

$onStart = {
    param($start)
    $state.Index++
    $state.Sink = New-AdgNtfsFileSink -OutputDirectory $OutputDirectory -Prefix ('run-{0:d2}' -f $state.Index)
    Write-AdgNtfsPayload -Sink $state.Sink -Payload $start -Kind 'start'
}.GetNewClosure()

$onBatch = {
    param($batch)
    Write-AdgNtfsPayload -Sink $state.Sink -Payload $batch -Kind 'batch'
}.GetNewClosure()

$onCompletion = {
    param($completion)
    Write-AdgNtfsPayload -Sink $state.Sink -Payload $completion -Kind 'completion'
}.GetNewClosure()

$runs = Invoke-AdgNtfsScan -Settings $settings -OnStart $onStart -OnBatch $onBatch `
    -OnCompletion $onCompletion -RunPerScanRoot -CollectorHost 'COLLECTOR01' `
    -CollectorVersion '0.1.0' -ModulePath $modulePath

$index = 0
foreach ($run in $runs) {
    $index++
    Write-Host ("run-{0:d2}: {1}, {2} observation(s) in {3} batch(es), {4} error(s), {5} boundar(ies), {6} reconciled." -f `
            $index, $run.Completion.status, $run.Completion.observation_count, `
            $run.Completion.batch_count, $run.Completion.error_count, `
            $run.Metrics.BoundariesFound, @($run.ReconciledScopes).Count)
}

Write-Host "Fixture payloads written to $OutputDirectory"
