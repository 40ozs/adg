#requires -Version 7.0
<#
.SYNOPSIS
    Observes access Windows actually grants on a real directory, to a real token.

.DESCRIPTION
    `AdgAuthzProbe.psm1` asks Windows to evaluate a *synthetic* descriptor. That is the right
    instrument for a large matrix -- it reaches DACL shapes no API will write to disk -- but it
    has one blind spot, and the blind spot matters: a synthetic descriptor carries no object
    type, so Authz cannot apply the file system's valid-rights mask. On a NULL DACL it
    therefore answers `0x001FFFFF`, every bit in the standard and specific ranges, where a
    real directory grants `FILE_ALL_ACCESS` (`0x001F01FF`).

    This module closes that gap by measuring the real thing: it creates directories, writes
    descriptors to them, opens them with `MAXIMUM_ALLOWED`, and reads the access the kernel
    actually granted out of the handle with `NtQueryObject`. No inference, no second model --
    the number a running process was given.

    The subject is whoever runs it, and the token is that process's real token, so the
    membership side is real too. It needs no elevation: every directory is created under the
    caller's own temporary directory, and every descriptor is written as its owner.

.NOTES
    `FILE_FLAG_BACKUP_SEMANTICS` is required to obtain a handle to a directory at all.
#>

Set-StrictMode -Version Latest

if (-not ('Adg.Authz.RealAccess' -as [type])) {
    Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace Adg.Authz
{
    public static class RealAccess
    {
        [StructLayout(LayoutKind.Sequential)]
        struct OBJECT_BASIC_INFORMATION
        {
            public uint Attributes;
            public uint GrantedAccess;
            public uint HandleCount;
            public uint PointerCount;
            public uint PagedPoolCharge;
            public uint NonPagedPoolCharge;
            public uint Reserved1, Reserved2, Reserved3;
            public uint NameInfoSize;
            public uint TypeInfoSize;
            public uint SecurityDescriptorSize;
            public long CreationTime;
        }

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern IntPtr CreateFileW(string fileName, uint desiredAccess, uint shareMode,
            IntPtr securityAttributes, uint creationDisposition, uint flagsAndAttributes,
            IntPtr templateFile);

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool CloseHandle(IntPtr handle);

        [DllImport("ntdll.dll")]
        static extern int NtQueryObject(IntPtr handle, int infoClass, IntPtr info,
            int infoLength, out int returnLength);

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern bool SetFileSecurityW(string fileName, uint securityInformation,
            byte[] securityDescriptor);

        const uint MAXIMUM_ALLOWED = 0x02000000;
        const uint FILE_SHARE_ALL = 0x00000007;
        const uint OPEN_EXISTING = 3;
        const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
        const uint DACL_SECURITY_INFORMATION = 0x00000004;
        const int ObjectBasicInformation = 0;
        static readonly IntPtr INVALID_HANDLE = new IntPtr(-1);

        /// <summary>
        /// The access mask the kernel granted this process on this path. Opening with
        /// MAXIMUM_ALLOWED asks for everything the caller is entitled to; the handle then
        /// carries exactly that, and NtQueryObject reads it back.
        /// </summary>
        public static uint GrantedAccess(string path)
        {
            IntPtr handle = CreateFileW(path, MAXIMUM_ALLOWED, FILE_SHARE_ALL, IntPtr.Zero,
                OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, IntPtr.Zero);
            if (handle == INVALID_HANDLE)
            {
                int err = Marshal.GetLastWin32Error();
                // ACCESS_DENIED means the open was refused outright: nothing was granted.
                if (err == 5) return 0;
                throw new Win32Exception(err, "CreateFileW failed for " + path);
            }
            try
            {
                int size = Marshal.SizeOf<OBJECT_BASIC_INFORMATION>();
                IntPtr buffer = Marshal.AllocHGlobal(size);
                try
                {
                    int returned;
                    int status = NtQueryObject(handle, ObjectBasicInformation, buffer, size,
                        out returned);
                    if (status != 0)
                        throw new Win32Exception(status, "NtQueryObject failed, status 0x"
                            + status.ToString("X8"));
                    return Marshal.PtrToStructure<OBJECT_BASIC_INFORMATION>(buffer).GrantedAccess;
                }
                finally { Marshal.FreeHGlobal(buffer); }
            }
            finally { CloseHandle(handle); }
        }

        /// <summary>
        /// Write a NULL DACL onto a path -- SE_DACL_PRESENT clear, which grants everyone
        /// everything. .NET's ObjectSecurity cannot express this: it always carries a DACL,
        /// and an empty one means the opposite.
        /// </summary>
        public static void SetNullDacl(string path)
        {
            // Revision 1, SE_SELF_RELATIVE (0x8000) only: no owner, no group, no DACL, no SACL.
            byte[] descriptor = new byte[20];
            descriptor[0] = 1;      // Revision
            descriptor[1] = 0;      // Sbz1
            descriptor[2] = 0x00;   // Control low byte -- SE_DACL_PRESENT deliberately clear
            descriptor[3] = 0x80;   // Control high byte -- SE_SELF_RELATIVE
            // Owner, Group, Sacl and Dacl offsets all remain zero.
            if (!SetFileSecurityW(path, DACL_SECURITY_INFORMATION, descriptor))
                throw new Win32Exception(Marshal.GetLastWin32Error(),
                    "SetFileSecurityW failed for " + path);
        }
    }
}
'@
}


function Get-AdgGrantedAccess {
    <#
    .SYNOPSIS
        The access mask Windows grants the current process on a path.

    .OUTPUTS
        System.UInt32
    #>
    [CmdletBinding()]
    [OutputType([uint32])]
    param(
        [Parameter(Mandatory)][string] $Path
    )

    return [Adg.Authz.RealAccess]::GrantedAccess($Path)
}


function Set-AdgNullDacl {
    <#
    .SYNOPSIS
        Clear SE_DACL_PRESENT on a path, giving it a NULL DACL.

    .DESCRIPTION
        A NULL DACL grants every principal every right, and it is invisible in an ACL viewer:
        an object with no DACL and one whose DACL grants Everyone Full Control are displayed
        identically. Requires WRITE_DAC, which the owner of a newly created directory holds.
    #>
    [CmdletBinding(SupportsShouldProcess)]
    param(
        [Parameter(Mandatory)][string] $Path
    )

    if ($PSCmdlet.ShouldProcess($Path, 'Write a NULL DACL')) {
        [Adg.Authz.RealAccess]::SetNullDacl($Path)
    }
}


function Get-AdgCurrentTokenSid {
    <#
    .SYNOPSIS
        Every SID in the caller's own access token, as the kernel sees it.

    .DESCRIPTION
        This is the list ADG's constructed token is measured against. A SID marked
        `UseForDenyOnly` is in the token but can only ever match a Deny ACE -- the state a
        filtered administrator token is in -- and is reported so that it is not mistaken for
        a grant.
    #>
    [CmdletBinding()]
    param()

    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    [pscustomobject]@{
        user_sid       = $identity.User.Value
        group_sids     = @($identity.Groups | ForEach-Object { $_.Value })
        deny_only_sids = @(
            $identity.Claims |
                Where-Object { $_.Type -eq 'http://schemas.microsoft.com/ws/2008/06/identity/claims/denyonlyprimarygroupsid' -or
                               $_.Type -eq 'http://schemas.microsoft.com/ws/2008/06/identity/claims/denyonlysid' } |
                ForEach-Object { $_.Value }
        )
        is_elevated    = ([System.Security.Principal.WindowsPrincipal]::new($identity)).IsInRole(
            [System.Security.Principal.WindowsBuiltInRole]::Administrator)
    }
}


Export-ModuleMember -Function @(
    'Get-AdgGrantedAccess',
    'Get-AdgCurrentTokenSid',
    'Set-AdgNullDacl'
)
