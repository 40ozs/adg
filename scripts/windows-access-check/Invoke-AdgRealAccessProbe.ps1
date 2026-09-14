#requires -Version 7.0
<#
.SYNOPSIS
    Measure the access Windows grants on real directories, and record it as a fixture.

.DESCRIPTION
    The synthetic-descriptor oracle (`Invoke-AdgAccessOracle.ps1`) covers thousands of DACL
    shapes but evaluates descriptors that never touch a disk. This measures the other side: a
    small number of cases built as real directories, with real descriptors, opened by a real
    process holding a real token, reading back the access the kernel actually granted.

    It is what settles questions the synthetic oracle cannot answer, the valid-rights mask
    above all: a NULL DACL evaluated as a bare descriptor answers `0x001FFFFF`, and on a real
    directory the same NULL DACL grants `FILE_ALL_ACCESS`. One of those is what an audit tool
    must report, and only this script can say which.

    Runs unelevated. Every directory is created beneath the caller's own temporary directory
    and removed afterwards; nothing outside it is read or written.

.PARAMETER OutputPath
    Where to write the fixture. Defaults to the committed location.

.PARAMETER WorkingDirectory
    Where to build the test directories. Defaults to a new directory under TEMP.

.PARAMETER KeepWorkingDirectory
    Leave the directories in place for inspection.

.EXAMPLE
    pwsh -File scripts/windows-access-check/Invoke-AdgRealAccessProbe.ps1
#>
[CmdletBinding()]
param(
    [string] $OutputPath,
    [string] $WorkingDirectory,
    [switch] $KeepWorkingDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptRoot)
$backend = Join-Path $repoRoot 'backend'

if (-not $OutputPath) {
    $OutputPath = Join-Path $backend 'tests\fixtures\windows\real-directory-access.json'
}
if (-not $WorkingDirectory) {
    $WorkingDirectory = Join-Path ([System.IO.Path]::GetTempPath()) (
        "adg-real-access-$([guid]::NewGuid().ToString('N').Substring(0, 8))")
}

Import-Module (Join-Path $scriptRoot 'AdgRealAccessProbe.psm1') -Force
Import-Module (Join-Path $scriptRoot 'AdgAuthzProbe.psm1') -Force

$identity = Get-AdgCurrentTokenSid
$me = $identity.user_sid
$script:SubjectSid = $identity.user_sid
$script:SubjectGroups = [string[]]$identity.group_sids
Write-Host "Subject: $me (elevated: $($identity.is_elevated))"

$FULL = [System.Security.AccessControl.FileSystemRights]::FullControl
$MODIFY = [System.Security.AccessControl.FileSystemRights]::Modify
$READ_EXECUTE = [System.Security.AccessControl.FileSystemRights]::ReadAndExecute

function New-Case {
    param(
        [Parameter(Mandatory)][string] $Name,
        [Parameter(Mandatory)][string] $Description,
        [scriptblock] $Prepare
    )

    $path = Join-Path $WorkingDirectory $Name
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    if ($Prepare) { & $Prepare $path }

    # A second, independent read: what Windows says it stored, not what we asked it to.
    $stored = (Get-Acl -LiteralPath $path).Sddl

    # Two instruments, deliberately both recorded, because they answer different questions
    # and the difference is itself a finding:
    #
    #  * `authz_granted` is the access check -- the same computation the Effective Access tab
    #    performs, and the one ADG models.
    #  * `open_granted` is what a real CreateFile handed back. It is the access check plus
    #    what the file system adds on a successful open (FILE_READ_ATTRIBUTES through the
    #    parent's traverse right) and minus every case where the open was refused outright.
    #    An object whose DACL grants the token nothing refuses the open even though its owner
    #    still holds READ_CONTROL -- demonstrably, since Get-Acl on it succeeds.
    $opened = Get-AdgGrantedAccess -Path $path
    $checked = Invoke-AdgAccessCheck -Sddl $stored -SubjectSid $script:SubjectSid `
        -GroupSid $script:SubjectGroups

    [pscustomobject]@{
        case          = $Name
        description   = $Description
        stored_sddl   = $stored
        authz_granted = ('0x{0:X8}' -f $checked)
        open_granted  = ('0x{0:X8}' -f $opened)
    }
}

function Set-ExplicitDacl {
    param(
        [Parameter(Mandatory)][string] $Path,
        # Not mandatory, and defaulting to empty: an empty DACL is a case, and a mandatory
        # array parameter refuses to bind one.
        [AllowEmptyCollection()][object[]] $Rule = @(),
        [bool] $Protected = $true
    )

    $acl = Get-Acl -LiteralPath $Path
    $acl.SetAccessRuleProtection($Protected, $false)
    foreach ($existing in @($acl.Access)) { [void]$acl.RemoveAccessRule($existing) }
    foreach ($item in $Rule) { $acl.AddAccessRule($item) }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function New-Rule {
    param(
        [Parameter(Mandatory)][string] $Sid,
        [Parameter(Mandatory)][object] $Rights,
        [string] $Type = 'Allow',
        [string] $Inheritance = 'None'
    )

    return [System.Security.AccessControl.FileSystemAccessRule]::new(
        [System.Security.Principal.SecurityIdentifier]::new($Sid),
        $Rights, $Inheritance, 'None', $Type)
}

New-Item -ItemType Directory -Path $WorkingDirectory -Force | Out-Null
Write-Host "Building cases under $WorkingDirectory"

try {
    $cases = @()

    $cases += New-Case -Name 'null-dacl' -Description @'
A directory with SE_DACL_PRESENT clear. Grants every principal every right. The case the
synthetic oracle cannot answer, because a bare descriptor carries no object type and Authz
therefore cannot apply the file system valid-rights mask.
'@ -Prepare { param($path) Set-AdgNullDacl -Path $path -Confirm:$false }

    $cases += New-Case -Name 'empty-dacl' -Description @'
A present but empty DACL. Identical to a NULL DACL in every ACL viewer and its exact
opposite: it grants nobody anything. The owner keeps READ_CONTROL and WRITE_DAC regardless.
'@ -Prepare { param($path) Set-ExplicitDacl -Path $path -Rule @() }

    $cases += New-Case -Name 'owner-only-no-ace' -Description @'
The subject owns the directory and no ACE names them. Windows grants the owner's implicit
READ_CONTROL and WRITE_DAC before reading a single entry.
'@ -Prepare { param($path) Set-ExplicitDacl -Path $path -Rule @((New-Rule -Sid 'S-1-5-18' -Rights $FULL)) }

    $cases += New-Case -Name 'explicit-full' -Description 'A direct Full Control grant to the subject.' -Prepare {
        param($path) Set-ExplicitDacl -Path $path -Rule @((New-Rule -Sid $me -Rights $FULL))
    }

    $cases += New-Case -Name 'explicit-modify' -Description 'A direct Modify grant to the subject.' -Prepare {
        param($path) Set-ExplicitDacl -Path $path -Rule @((New-Rule -Sid $me -Rights $MODIFY))
    }

    $cases += New-Case -Name 'everyone-read-execute' -Description @'
Read & Execute for Everyone and no entry naming the subject. Access arrives through a
well-known SID that no membership edge in any directory records, which is why a token has to
be constructed rather than read out of the identity graph.
'@ -Prepare { param($path) Set-ExplicitDacl -Path $path -Rule @((New-Rule -Sid 'S-1-1-0' -Rights $READ_EXECUTE)) }

    $cases += New-Case -Name 'deny-wins-over-allow' -Description @'
Deny Modify for Everyone ahead of Allow Full Control for the subject, in canonical order.
'@ -Prepare {
        param($path)
        Set-ExplicitDacl -Path $path -Rule @(
            (New-Rule -Sid 'S-1-1-0' -Rights $MODIFY -Type 'Deny'),
            (New-Rule -Sid $me -Rights $FULL))
    }

    $cases += New-Case -Name 'authenticated-users-modify' -Description @'
Modify for Authenticated Users. Confirms that SID is in a real interactive token.
'@ -Prepare { param($path) Set-ExplicitDacl -Path $path -Rule @((New-Rule -Sid 'S-1-5-11' -Rights $MODIFY)) }

    $cases += New-Case -Name 'unresolvable-trustee-only' -Description @'
The only ACE names a SID that resolves to nothing on this host -- the orphaned-SID finding.
It grants the subject nothing, and the owner's implicit rights are all that remain.
'@ -Prepare {
        param($path)
        Set-ExplicitDacl -Path $path -Rule @(
            (New-Rule -Sid 'S-1-5-21-999888777-666555444-333222111-1234' -Rights $FULL))
    }

    $payload = [ordered]@{
        schema       = 'adg.real-access/1'
        generated_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        method       = [ordered]@{
            authz_granted = 'AuthzAccessCheck(MAXIMUM_ALLOWED) over the descriptor as stored on disk, with the running process token SIDs'
            open_granted  = 'CreateFileW(MAXIMUM_ALLOWED, FILE_FLAG_BACKUP_SEMANTICS) then NtQueryObject(ObjectBasicInformation).GrantedAccess; 0x00000000 means the open was refused'
        }
        host         = [ordered]@{
            os_caption = (Get-CimInstance -ClassName Win32_OperatingSystem).Caption
            os_version = (Get-CimInstance -ClassName Win32_OperatingSystem).Version
            powershell = $PSVersionTable.PSVersion.ToString()
        }
        subject      = [ordered]@{
            user_sid    = $identity.user_sid
            group_sids  = $identity.group_sids
            is_elevated = $identity.is_elevated
        }
        cases        = $cases
    }

    $directory = Split-Path -Parent $OutputPath
    if ($directory -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8

    foreach ($case in $cases) {
        Write-Host ("  {0,-28} authz={1}  open={2}" -f $case.case, $case.authz_granted,
            $case.open_granted)
    }
    Write-Host "Wrote $($cases.Count) measured cases to $OutputPath"
}
finally {
    if (-not $KeepWorkingDirectory) {
        Remove-Item -LiteralPath $WorkingDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}
