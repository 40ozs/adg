<#
.SYNOPSIS
    Build a local NTFS test tree for the ADG collector, and write its manifest.

.DESCRIPTION
    Two kinds of tree, for two questions.

    'semantics' answers "does the collector describe a real tree the way Windows describes
    it?" - a small hand-specified tree with one directory per case a scanner gets wrong, and
    a manifest recording, for each directory, how it was built and what the collector is
    therefore expected to say about it.

    'small' / 'medium' / 'large' answer "what does a scan cost?" - a balanced tree of a
    known size whose ACLs come from a fixed small set, so the directories-to-distinct-ACLs
    ratio is a parameter rather than a discovery.

    **This script writes ACLs, beneath -Root and nowhere else.** ADR-0004 makes the
    application read-only; this is a developer and CI tool. It writes only through the
    Access section of a descriptor (never the SACL, which would need SeSecurityPrivilege),
    and -Remove restores every ACL it changed before deleting anything. It runs unelevated,
    as the collector must.

.PARAMETER Root
    Where to build. Defaults to .tmp\windows-test-tree\<profile> under the repository, which
    .gitignore already excludes.

.PARAMETER Profile
    semantics (default), small, medium, or large.

.PARAMETER Force
    Rebuild over an existing tree. Without it a non-empty root is refused.

.PARAMETER Remove
    Delete the tree at -Root instead of building one.

.PARAMETER ManifestPath
    Where to write the manifest. Defaults to <root>.manifest.json next to the tree.

.EXAMPLE
    .\scripts\windows-test-tree\New-AdgTestTree.ps1

.EXAMPLE
    .\scripts\windows-test-tree\New-AdgTestTree.ps1 -Profile medium -Force

.EXAMPLE
    .\scripts\windows-test-tree\New-AdgTestTree.ps1 -Remove

.NOTES
    The collector identifies a directory by its UNC path, so scanning a locally built tree
    means \\localhost\<drive>$ - which needs local Administrators. The script reports
    whether that route works from this session; when it does not, the tree is still built
    and the validation suite skips with a reason rather than failing.
#>
[CmdletBinding()]
param(
    [string] $Root,
    [ValidateSet('semantics', 'small', 'medium', 'large')][string] $Profile = 'semantics',
    [switch] $Force,
    [switch] $Remove,
    [string] $ManifestPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'AdgTestTree.psm1') -Force

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $repoRoot ".tmp\windows-test-tree\$Profile"
}
$Root = Resolve-AdgTestTreePath $Root

if ($Remove) {
    Remove-AdgTestTree -Root $Root
    Write-Host "Removed $Root"
    return
}

if ($Profile -ne 'semantics') {
    $shape = Get-AdgTestTreeScale -Name $Profile
    Write-Host ("Building the '{0}' tree: fanout {1}, depth {2}, about {3} directories, {4} distinct ACL variants." -f `
            $shape.Name, $shape.Fanout, $shape.Depth, $shape.ExpectedDirectory, $shape.AclVariants)
    Write-Host 'Building is slower than scanning: every directory is a create and every variant a descriptor write.'
}

$manifest = New-AdgTestTree -Root $Root -Profile $Profile -Force:$Force

if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
    $ManifestPath = "$Root.manifest.json"
}
[void] (Export-AdgTestTreeManifest -Manifest $manifest -Path $ManifestPath)

Write-Host ("Built {0} director(ies) at {1} in {2:n1}s." -f `
        $manifest.directoriesVisibleFromHere, $manifest.root, $manifest.buildSeconds)
Write-Host "Manifest: $ManifestPath"

$reachable = Test-AdgTestTreeUncAccess -Path $Root
if ($reachable) {
    Write-Host "Scan it with: -ScanRoot $($manifest.uncRoot)"
}
else {
    # Not a warning about tidiness. Without a UNC route the collector cannot be pointed at
    # this tree at all, and a suite that pretended otherwise would report a pass for a scan
    # that never happened.
    Write-Warning ("No UNC route to this tree: {0} is not reachable from this session. " -f $manifest.uncRoot +
        'The collector identifies a directory by its UNC path, and on a local machine the only such route is the drive''s administrative share, which needs local Administrators. The validation suite will skip rather than fail.')
}
