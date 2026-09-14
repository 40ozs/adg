#requires -Version 7.0
<#
.SYNOPSIS
    Asks Windows what a token may do to a security descriptor.

.DESCRIPTION
    ADG computes effective access in `app.access_engine`. This module exists so that the
    answer can be checked against the only authority that matters: the access check Windows
    itself performs.

    It wraps the **Authz** API, which is what the "Effective Access" tab in the Windows
    security dialog uses. Three properties make it the right instrument here:

    * It evaluates a security descriptor supplied as bytes, so a case does not need to exist
      on disk. Every DACL shape is reachable, including ones `Set-Acl` refuses to write or
      silently reorders into canonical order.
    * `AuthzInitializeContextFromSid` with `AUTHZ_SKIP_TOKEN_GROUPS` builds a context holding
      only the SIDs supplied, so ADG's constructed token and Windows' token can be made
      identical and the comparison is about the access check alone.
    * Asking for `MAXIMUM_ALLOWED` returns the whole granted mask rather than a yes/no, which
      is the same question `evaluate_acl` answers.

    None of it requires elevation, a domain, or a writable share: the SIDs in a descriptor
    need not resolve to anything, and no object is created. That is what lets the comparison
    run everywhere rather than only on a prepared host.

.NOTES
    Generic rights are mapped through `MapGenericMask` before the check, because that is what
    the file system does when a descriptor is written. Authz applies no generic mapping of
    its own -- an unmapped `GENERIC_ALL` ACE grants nothing at all -- so skipping this step
    would compare ADG's expansion against Windows declining to expand anything.
#>

Set-StrictMode -Version Latest

if (-not ('Adg.Authz.Probe' -as [type])) {
    Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;

namespace Adg.Authz
{
    public static class Probe
    {
        [StructLayout(LayoutKind.Sequential)]
        public struct LUID { public uint LowPart; public int HighPart; }

        [StructLayout(LayoutKind.Sequential)]
        public struct GENERIC_MAPPING
        {
            public uint GenericRead, GenericWrite, GenericExecute, GenericAll;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct AUTHZ_ACCESS_REQUEST
        {
            public uint DesiredAccess;
            public IntPtr PrincipalSelfSid;
            public IntPtr ObjectTypeList;
            public int ObjectTypeListLength;
            public IntPtr OptionalArguments;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct AUTHZ_ACCESS_REPLY
        {
            public int ResultListLength;
            public IntPtr GrantedAccessMask;
            public IntPtr SaclEvaluationResults;
            public IntPtr Error;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct SID_AND_ATTRIBUTES { public IntPtr Sid; public uint Attributes; }

        [DllImport("authz.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern bool AuthzInitializeResourceManager(uint flags, IntPtr accessCheck,
            IntPtr computeDynamicGroups, IntPtr freeDynamicGroups, string name, out IntPtr rm);

        [DllImport("authz.dll", SetLastError = true)]
        static extern bool AuthzInitializeContextFromSid(uint flags, byte[] userSid, IntPtr rm,
            IntPtr expirationTime, LUID identifier, IntPtr dynamicGroupArgs, out IntPtr ctx);

        [DllImport("authz.dll", SetLastError = true)]
        static extern bool AuthzAddSidsToContext(IntPtr orig, IntPtr sids, uint sidCount,
            IntPtr restrictedSids, uint restrictedCount, out IntPtr newCtx);

        [DllImport("authz.dll", SetLastError = true)]
        static extern bool AuthzAccessCheck(uint flags, IntPtr ctx,
            ref AUTHZ_ACCESS_REQUEST request, IntPtr auditEvent, byte[] sd,
            IntPtr sdArray, uint sdCount, ref AUTHZ_ACCESS_REPLY reply, IntPtr handle);

        [DllImport("authz.dll", SetLastError = true)]
        static extern bool AuthzGetInformationFromContext(IntPtr ctx, int infoClass,
            int bufferSize, out int sizeRequired, IntPtr buffer);

        [DllImport("authz.dll", SetLastError = true)]
        static extern bool AuthzFreeContext(IntPtr ctx);

        [DllImport("authz.dll", SetLastError = true)]
        static extern bool AuthzFreeResourceManager(IntPtr rm);

        [DllImport("advapi32.dll")]
        static extern void MapGenericMask(ref uint accessMask, ref GENERIC_MAPPING mapping);

        const uint AUTHZ_RM_FLAG_NO_AUDIT = 0x1;
        const uint AUTHZ_SKIP_TOKEN_GROUPS = 0x2;
        const uint MAXIMUM_ALLOWED = 0x02000000;
        const uint SE_GROUP_ENABLED = 0x4;
        const int AuthzContextInfoGroupsSids = 2;
        const int ERROR_ACCESS_DENIED = 5;

        /// <summary>FILE_GENERIC_READ/WRITE/EXECUTE and FILE_ALL_ACCESS.</summary>
        public static GENERIC_MAPPING FileMapping()
        {
            return new GENERIC_MAPPING
            {
                GenericRead = 0x00120089,
                GenericWrite = 0x00120116,
                GenericExecute = 0x001200A0,
                GenericAll = 0x001F01FF
            };
        }

        /// <summary>Windows' own generic expansion, for one mask.</summary>
        public static uint MapFileMask(uint mask)
        {
            GENERIC_MAPPING mapping = FileMapping();
            MapGenericMask(ref mask, ref mapping);
            return mask;
        }

        static byte[] SidBytes(string sid)
        {
            SecurityIdentifier parsed = new SecurityIdentifier(sid);
            byte[] bytes = new byte[parsed.BinaryLength];
            parsed.GetBinaryForm(bytes, 0);
            return bytes;
        }

        static IntPtr NewManager()
        {
            IntPtr rm;
            if (!AuthzInitializeResourceManager(AUTHZ_RM_FLAG_NO_AUDIT, IntPtr.Zero, IntPtr.Zero,
                    IntPtr.Zero, "ADG", out rm))
                throw new Win32Exception(Marshal.GetLastWin32Error(),
                    "AuthzInitializeResourceManager failed");
            return rm;
        }

        /// <summary>A context holding exactly the SIDs given, with no group lookup.</summary>
        static IntPtr NewContext(IntPtr rm, string subjectSid, string[] groupSids,
            out IntPtr baseContext, out List<IntPtr> allocations, out IntPtr array)
        {
            LUID luid = new LUID();
            allocations = new List<IntPtr>();
            array = IntPtr.Zero;
            if (!AuthzInitializeContextFromSid(AUTHZ_SKIP_TOKEN_GROUPS, SidBytes(subjectSid), rm,
                    IntPtr.Zero, luid, IntPtr.Zero, out baseContext))
                throw new Win32Exception(Marshal.GetLastWin32Error(),
                    "AuthzInitializeContextFromSid failed for " + subjectSid);
            if (groupSids == null || groupSids.Length == 0) return baseContext;

            int stride = Marshal.SizeOf<SID_AND_ATTRIBUTES>();
            array = Marshal.AllocHGlobal(stride * groupSids.Length);
            for (int i = 0; i < groupSids.Length; i++)
            {
                byte[] raw = SidBytes(groupSids[i]);
                IntPtr p = Marshal.AllocHGlobal(raw.Length);
                Marshal.Copy(raw, 0, p, raw.Length);
                allocations.Add(p);
                Marshal.StructureToPtr(
                    new SID_AND_ATTRIBUTES { Sid = p, Attributes = SE_GROUP_ENABLED },
                    IntPtr.Add(array, i * stride), false);
            }
            IntPtr extended;
            if (!AuthzAddSidsToContext(baseContext, array, (uint)groupSids.Length, IntPtr.Zero, 0,
                    out extended))
                throw new Win32Exception(Marshal.GetLastWin32Error(),
                    "AuthzAddSidsToContext failed");
            return extended;
        }

        static uint CheckOne(IntPtr ctx, byte[] sd)
        {
            AUTHZ_ACCESS_REQUEST request = new AUTHZ_ACCESS_REQUEST
            {
                DesiredAccess = MAXIMUM_ALLOWED,
                PrincipalSelfSid = IntPtr.Zero,
                ObjectTypeList = IntPtr.Zero,
                ObjectTypeListLength = 0,
                OptionalArguments = IntPtr.Zero
            };
            IntPtr granted = Marshal.AllocHGlobal(4);
            IntPtr error = Marshal.AllocHGlobal(4);
            try
            {
                Marshal.WriteInt32(granted, 0);
                Marshal.WriteInt32(error, 0);
                AUTHZ_ACCESS_REPLY reply = new AUTHZ_ACCESS_REPLY
                {
                    ResultListLength = 1,
                    GrantedAccessMask = granted,
                    SaclEvaluationResults = IntPtr.Zero,
                    Error = error
                };
                if (!AuthzAccessCheck(0, ctx, ref request, IntPtr.Zero, sd, IntPtr.Zero, 0,
                        ref reply, IntPtr.Zero))
                {
                    int err = Marshal.GetLastWin32Error();
                    // The check ran and granted nothing. That is an answer, not a failure.
                    if (err == ERROR_ACCESS_DENIED) return 0;
                    throw new Win32Exception(err, "AuthzAccessCheck failed");
                }
                return unchecked((uint)Marshal.ReadInt32(granted));
            }
            finally { Marshal.FreeHGlobal(granted); Marshal.FreeHGlobal(error); }
        }

        /// <summary>The granted mask for one token against one descriptor.</summary>
        public static uint Check(string subjectSid, string[] groupSids, byte[] sd)
        {
            IntPtr rm = NewManager();
            try
            {
                IntPtr baseCtx;
                List<IntPtr> allocations;
                IntPtr array;
                IntPtr ctx = NewContext(rm, subjectSid, groupSids, out baseCtx, out allocations,
                    out array);
                try { return CheckOne(ctx, sd); }
                finally
                {
                    if (ctx != baseCtx) AuthzFreeContext(ctx);
                    AuthzFreeContext(baseCtx);
                    foreach (IntPtr p in allocations) Marshal.FreeHGlobal(p);
                    if (array != IntPtr.Zero) Marshal.FreeHGlobal(array);
                }
            }
            finally { AuthzFreeResourceManager(rm); }
        }

        /// <summary>
        /// Every group SID Windows itself places in a context built from this SID, with the
        /// group attributes. This is the measurement ADG's token assumptions are checked
        /// against; it needs the SID to be one the local system can resolve.
        /// </summary>
        public static string[] ContextGroups(string subjectSid)
        {
            IntPtr rm = NewManager();
            try
            {
                LUID luid = new LUID();
                IntPtr ctx;
                if (!AuthzInitializeContextFromSid(0, SidBytes(subjectSid), rm, IntPtr.Zero, luid,
                        IntPtr.Zero, out ctx))
                    throw new Win32Exception(Marshal.GetLastWin32Error(),
                        "AuthzInitializeContextFromSid failed for " + subjectSid);
                try
                {
                    int needed;
                    AuthzGetInformationFromContext(ctx, AuthzContextInfoGroupsSids, 0, out needed,
                        IntPtr.Zero);
                    if (needed <= 0) return new string[0];
                    IntPtr buffer = Marshal.AllocHGlobal(needed);
                    try
                    {
                        int again;
                        if (!AuthzGetInformationFromContext(ctx, AuthzContextInfoGroupsSids,
                                needed, out again, buffer))
                            throw new Win32Exception(Marshal.GetLastWin32Error(),
                                "AuthzGetInformationFromContext failed");
                        int count = Marshal.ReadInt32(buffer);
                        List<string> results = new List<string>();
                        int stride = Marshal.SizeOf<SID_AND_ATTRIBUTES>();
                        for (int i = 0; i < count; i++)
                        {
                            IntPtr at = IntPtr.Add(buffer, IntPtr.Size + i * stride);
                            SID_AND_ATTRIBUTES item =
                                Marshal.PtrToStructure<SID_AND_ATTRIBUTES>(at);
                            results.Add(new SecurityIdentifier(item.Sid).Value);
                        }
                        return results.ToArray();
                    }
                    finally { Marshal.FreeHGlobal(buffer); }
                }
                finally { AuthzFreeContext(ctx); }
            }
            finally { AuthzFreeResourceManager(rm); }
        }

        /// <summary>
        /// Answer a whole batch against one resource manager. Creating a resource manager per
        /// case dominates the run time of a several-thousand-case matrix, and nothing about
        /// the answer depends on which manager asked.
        /// </summary>
        public static uint[] CheckBatch(string[] subjectSids, string[][] groupSids, byte[][] sds,
            string[] errors)
        {
            uint[] results = new uint[subjectSids.Length];
            IntPtr rm = NewManager();
            try
            {
                for (int i = 0; i < subjectSids.Length; i++)
                {
                    IntPtr baseCtx = IntPtr.Zero;
                    List<IntPtr> allocations = new List<IntPtr>();
                    IntPtr array = IntPtr.Zero;
                    IntPtr ctx = IntPtr.Zero;
                    try
                    {
                        ctx = NewContext(rm, subjectSids[i], groupSids[i], out baseCtx,
                            out allocations, out array);
                        results[i] = CheckOne(ctx, sds[i]);
                    }
                    catch (Exception failure)
                    {
                        // One unanswerable case must not lose the other five thousand
                        // answers. The caller decides whether a failure is fatal, and it
                        // needs to know which case produced it to decide.
                        errors[i] = failure.Message;
                    }
                    finally
                    {
                        if (ctx != IntPtr.Zero && ctx != baseCtx) AuthzFreeContext(ctx);
                        if (baseCtx != IntPtr.Zero) AuthzFreeContext(baseCtx);
                        foreach (IntPtr p in allocations) Marshal.FreeHGlobal(p);
                        if (array != IntPtr.Zero) Marshal.FreeHGlobal(array);
                    }
                }
                return results;
            }
            finally { AuthzFreeResourceManager(rm); }
        }
    }
}
'@
}


function ConvertTo-AdgMappedDescriptor {
    <#
    .SYNOPSIS
        An SDDL string as descriptor bytes, with every ACE's generic rights expanded.

    .DESCRIPTION
        The file system maps generic rights when a descriptor is written, so a stored file ACE
        never carries a generic bit. A synthetic descriptor built from SDDL does, and Authz
        will not expand it -- an unmapped `GENERIC_ALL` grants nothing. Mapping here reproduces
        what the file system would have done, which makes the comparison a comparison of
        access checks rather than of who expands generics.

    .OUTPUTS
        System.Byte[]
    #>
    [CmdletBinding()]
    [OutputType([byte[]])]
    param(
        [Parameter(Mandatory)][string] $Sddl
    )

    $descriptor = [System.Security.AccessControl.RawSecurityDescriptor]::new($Sddl)
    if ($null -ne $descriptor.DiscretionaryAcl) {
        $replacement = [System.Security.AccessControl.RawAcl]::new(
            $descriptor.DiscretionaryAcl.Revision, $descriptor.DiscretionaryAcl.Count)
        $index = 0
        foreach ($ace in $descriptor.DiscretionaryAcl) {
            # AccessMask is a signed Int32: 0x80000000 arrives as a negative number and a
            # direct [uint32] cast throws. Mask through Int64 to get the bits back.
            $unsigned = [uint32](([int64]$ace.AccessMask) -band 0xFFFFFFFFL)
            $mapped = [Adg.Authz.Probe]::MapFileMask($unsigned)
            $rebuilt = [System.Security.AccessControl.CommonAce]::new(
                $ace.AceFlags,
                $ace.AceType,
                [BitConverter]::ToInt32([BitConverter]::GetBytes($mapped), 0),
                $ace.SecurityIdentifier,
                $false,
                $null)
            $replacement.InsertAce($index, $rebuilt)
            $index++
        }
        $descriptor.DiscretionaryAcl = $replacement
    }
    $bytes = [byte[]]::new($descriptor.BinaryLength)
    $descriptor.GetBinaryForm($bytes, 0)
    return , $bytes
}


function Invoke-AdgAccessCheck {
    <#
    .SYNOPSIS
        What Windows grants a token of exactly these SIDs against this descriptor.

    .PARAMETER Sddl
        The security descriptor. Generic rights are expanded before the check.

    .PARAMETER SubjectSid
        The SID the context is built from. It need not resolve to anything.

    .PARAMETER GroupSid
        Additional SIDs the token carries. Added with SE_GROUP_ENABLED.

    .OUTPUTS
        System.UInt32 -- the granted access mask.
    #>
    [CmdletBinding()]
    [OutputType([uint32])]
    param(
        [Parameter(Mandatory)][string] $Sddl,
        [Parameter(Mandatory)][string] $SubjectSid,
        [string[]] $GroupSid = @()
    )

    $bytes = ConvertTo-AdgMappedDescriptor -Sddl $Sddl
    return [Adg.Authz.Probe]::Check($SubjectSid, [string[]]$GroupSid, $bytes)
}


function Invoke-AdgAccessCheckBatch {
    <#
    .SYNOPSIS
        Run many cases against one resource manager.

    .PARAMETER Case
        Objects carrying `sddl`, `subject_sid` and `token_sids`, as emitted by
        `tests.access_engine.matrix`. The first entry of `token_sids` is the subject.

    .OUTPUTS
        One PSCustomObject per case, carrying the case id and the granted mask.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object[]] $Case
    )

    $subjects = [string[]]::new($Case.Count)
    $groups = [string[][]]::new($Case.Count)
    $descriptors = [byte[][]]::new($Case.Count)

    for ($i = 0; $i -lt $Case.Count; $i++) {
        $item = $Case[$i]
        $subjects[$i] = $item.subject_sid
        # token_sids[0] is the subject itself; the context already carries it.
        $rest = @($item.token_sids | Select-Object -Skip 1)
        $groups[$i] = [string[]]$rest
        $descriptors[$i] = ConvertTo-AdgMappedDescriptor -Sddl $item.sddl
    }

    $errors = [string[]]::new($Case.Count)
    $masks = [Adg.Authz.Probe]::CheckBatch($subjects, $groups, $descriptors, $errors)

    for ($i = 0; $i -lt $Case.Count; $i++) {
        if ($null -ne $errors[$i]) {
            [pscustomobject]@{
                case_id     = $Case[$i].case_id
                fingerprint = $Case[$i].fingerprint
                mask        = $null
                error       = $errors[$i]
            }
        }
        else {
            [pscustomobject]@{
                case_id     = $Case[$i].case_id
                fingerprint = $Case[$i].fingerprint
                mask        = ('0x{0:X8}' -f $masks[$i])
                error       = $null
            }
        }
    }
}


function Get-AdgTokenGroup {
    <#
    .SYNOPSIS
        The group SIDs Windows places in a token built from one SID.

    .DESCRIPTION
        ADG constructs a token from collected memberships plus a named assumption, because it
        has never seen a logon. This is the measurement that says whether the assumption is
        right: it needs a SID the local system can resolve, so it runs only against real local
        or domain principals.

    .OUTPUTS
        System.String[]
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory)][string] $Sid
    )

    return [Adg.Authz.Probe]::ContextGroups($Sid)
}


function ConvertTo-AdgFileMappedMask {
    <#
    .SYNOPSIS
        One access mask through Windows' own file-system generic mapping.

    .DESCRIPTION
        The measurement behind `app.access_engine.rights.FILE_SYSTEM_GENERIC_MAPPING`. ADG
        hard-codes the four mapped values; this returns what `MapGenericMask` on this host
        actually produces, so the constant can be held to it rather than to a document.

    .OUTPUTS
        System.UInt32
    #>
    [CmdletBinding()]
    [OutputType([uint32])]
    param(
        [Parameter(Mandatory)][uint32] $Mask
    )

    return [Adg.Authz.Probe]::MapFileMask($Mask)
}


Export-ModuleMember -Function @(
    'ConvertTo-AdgMappedDescriptor',
    'ConvertTo-AdgFileMappedMask',
    'Get-AdgTokenGroup',
    'Invoke-AdgAccessCheck',
    'Invoke-AdgAccessCheckBatch'
)
