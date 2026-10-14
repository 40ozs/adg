<#
    Acquisition layer: the only functions in this collector that touch a remote host.

    Every one of them is a thin wrapper over one read-only Win32 network-management call, with
    no normalization and no policy, so this file is the seam the Pester suite mocks: the
    translation and orchestration layers run against an imaginary estate with no domain and
    no network.

    Why netapi32 rather than CIM. `Win32_GroupUser` resolves every member to an account
    object, and a member whose SID no longer resolves - a deleted account, a domain the host
    cannot reach - is silently left out of the association. An audit tool that drops the
    orphaned SID in BUILTIN\Administrators has hidden exactly the finding it exists for.
    `NetLocalGroupGetMembers` returns the SID itself, resolved or not, with the SID type the
    host could establish.

    ADG is read-only (ADR-0004). These are the Net*Enum / Net*GetMembers / Net*GetInfo
    queries and one name lookup; nothing here writes to a target, and the collector needs no
    right beyond what reading a remote host's local account database requires.
#>

$script:AdgLocalGroupNativeSource = @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;

namespace Adg.LocalGroups
{
    public sealed class ServerInfo
    {
        public string Name;
        public int Type;
        public bool IsDomainController;
    }

    public sealed class GroupInfo
    {
        public string Name;
        public string Comment;
    }

    public sealed class MemberInfo
    {
        public string Sid;
        public int SidUsage;
        public string AccountName;
    }

    public sealed class AccountInfo
    {
        public string Sid;
        public string Domain;
        public int SidUsage;
    }

    public static class Native
    {
        private const int NERR_Success = 0;
        private const int ERROR_MORE_DATA = 234;
        private const int ERROR_INSUFFICIENT_BUFFER = 122;
        private const int MAX_PREFERRED_LENGTH = -1;
        private const int SV_TYPE_DOMAIN_CTRL = 0x00000008;
        private const int SV_TYPE_DOMAIN_BAKCTRL = 0x00000010;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct SERVER_INFO_101
        {
            public int sv101_platform_id;
            public string sv101_name;
            public int sv101_version_major;
            public int sv101_version_minor;
            public int sv101_type;
            public string sv101_comment;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct LOCALGROUP_INFO_1
        {
            public string lgrpi1_name;
            public string lgrpi1_comment;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct LOCALGROUP_MEMBERS_INFO_2
        {
            public IntPtr lgrmi2_sid;
            public int lgrmi2_sidusage;
            public string lgrmi2_domainandname;
        }

        [DllImport("netapi32.dll", CharSet = CharSet.Unicode)]
        private static extern int NetServerGetInfo(string servername, int level, out IntPtr bufptr);

        [DllImport("netapi32.dll", CharSet = CharSet.Unicode)]
        private static extern int NetLocalGroupEnum(
            string servername, int level, out IntPtr bufptr, int prefmaxlen,
            out int entriesread, out int totalentries, ref IntPtr resumehandle);

        [DllImport("netapi32.dll", CharSet = CharSet.Unicode)]
        private static extern int NetLocalGroupGetMembers(
            string servername, string localgroupname, int level, out IntPtr bufptr,
            int prefmaxlen, out int entriesread, out int totalentries, ref IntPtr resumehandle);

        [DllImport("netapi32.dll")]
        private static extern int NetApiBufferFree(IntPtr buffer);

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool LookupAccountName(
            string lpSystemName, string lpAccountName, byte[] sid, ref int cbSid,
            StringBuilder referencedDomainName, ref int cchReferencedDomainName, out int peUse);

        // PowerShell hands $null to a string parameter as "", and these functions are documented
        // to read the local computer for NULL, so an empty name is NULL again here.
        private static string Server(string name) { return string.IsNullOrEmpty(name) ? null : name; }

        public static ServerInfo GetServer(string server)
        {
            IntPtr buffer;
            int status = NetServerGetInfo(Server(server), 101, out buffer);
            if (status != NERR_Success) { throw new Win32Exception(status); }
            try
            {
                SERVER_INFO_101 info = Marshal.PtrToStructure<SERVER_INFO_101>(buffer);
                return new ServerInfo
                {
                    Name = info.sv101_name,
                    Type = info.sv101_type,
                    IsDomainController =
                        (info.sv101_type & (SV_TYPE_DOMAIN_CTRL | SV_TYPE_DOMAIN_BAKCTRL)) != 0,
                };
            }
            finally { NetApiBufferFree(buffer); }
        }

        public static List<GroupInfo> GetGroups(string server)
        {
            var groups = new List<GroupInfo>();
            IntPtr resume = IntPtr.Zero;
            int status;
            do
            {
                IntPtr buffer;
                int read, total;
                status = NetLocalGroupEnum(
                    Server(server), 1, out buffer, MAX_PREFERRED_LENGTH, out read, out total, ref resume);
                if (status != NERR_Success && status != ERROR_MORE_DATA)
                {
                    throw new Win32Exception(status);
                }
                try
                {
                    int size = Marshal.SizeOf<LOCALGROUP_INFO_1>();
                    for (int index = 0; index < read; index++)
                    {
                        var entry = Marshal.PtrToStructure<LOCALGROUP_INFO_1>(
                            IntPtr.Add(buffer, index * size));
                        groups.Add(new GroupInfo { Name = entry.lgrpi1_name, Comment = entry.lgrpi1_comment });
                    }
                }
                finally { if (buffer != IntPtr.Zero) { NetApiBufferFree(buffer); } }
            } while (status == ERROR_MORE_DATA);
            return groups;
        }

        public static List<MemberInfo> GetMembers(string server, string group)
        {
            var members = new List<MemberInfo>();
            IntPtr resume = IntPtr.Zero;
            int status;
            do
            {
                IntPtr buffer;
                int read, total;
                status = NetLocalGroupGetMembers(
                    Server(server), group, 2, out buffer, MAX_PREFERRED_LENGTH, out read, out total, ref resume);
                if (status != NERR_Success && status != ERROR_MORE_DATA)
                {
                    throw new Win32Exception(status);
                }
                try
                {
                    int size = Marshal.SizeOf<LOCALGROUP_MEMBERS_INFO_2>();
                    for (int index = 0; index < read; index++)
                    {
                        var entry = Marshal.PtrToStructure<LOCALGROUP_MEMBERS_INFO_2>(
                            IntPtr.Add(buffer, index * size));
                        members.Add(new MemberInfo
                        {
                            Sid = new SecurityIdentifier(entry.lgrmi2_sid).Value,
                            SidUsage = entry.lgrmi2_sidusage,
                            AccountName = entry.lgrmi2_domainandname,
                        });
                    }
                }
                finally { if (buffer != IntPtr.Zero) { NetApiBufferFree(buffer); } }
            } while (status == ERROR_MORE_DATA);
            return members;
        }

        public static AccountInfo LookupName(string server, string name)
        {
            int sidSize = 0;
            int domainSize = 0;
            int use;
            server = Server(server);
            LookupAccountName(server, name, null, ref sidSize, null, ref domainSize, out use);
            int error = Marshal.GetLastWin32Error();
            if (error != ERROR_INSUFFICIENT_BUFFER) { throw new Win32Exception(error); }
            var sid = new byte[sidSize];
            var domain = new StringBuilder(domainSize);
            if (!LookupAccountName(server, name, sid, ref sidSize, domain, ref domainSize, out use))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return new AccountInfo
            {
                Sid = new SecurityIdentifier(sid, 0).Value,
                Domain = domain.ToString(),
                SidUsage = use,
            };
        }
    }
}
'@


$script:AdgLocalGroupFixture = $null


function Set-AdgLocalGroupFixture {
    <#
        .SYNOPSIS
            Development and test only: read hosts from a JSON fixture instead of the network.
        .DESCRIPTION
            How the test suite and the backend's contract tests exercise the collector without
            a Windows host. The fixture answers exactly the four questions the network would -
            what the host is, its groups, what each name resolves to, each group's members -
            so everything above this seam runs unchanged. Pass $null to read hosts again.
            Never a way to scan a real environment.
    #>
    param([AllowNull()][string] $Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        $script:AdgLocalGroupFixture = $null
        return
    }
    $document = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json -AsHashtable
    $script:AdgLocalGroupFixture = $document['hosts']
}


function Get-AdgLocalGroupFixtureHost {
    param([Parameter(Mandatory)][string] $ComputerName)

    $entry = $script:AdgLocalGroupFixture[$ComputerName.ToLowerInvariant()]
    if ($null -eq $entry) {
        # The network's answer for a host that is not there: ERROR_BAD_NETPATH.
        throw [System.ComponentModel.Win32Exception]::new(53)
    }
    return $entry
}


function Initialize-AdgLocalGroupNative {
    <#
        .SYNOPSIS
            Compile the netapi32 bindings once per session.
        .DESCRIPTION
            Compiled on first use rather than at import, so the module loads - and its pure
            layers can be tested - on a machine where the bindings could never run. Calling
            into them anywhere but Windows fails with a clear message instead of a missing
            entry point.
    #>
    if (-not $IsWindows) {
        throw 'Reading a host''s local groups uses the Windows network-management API and runs only on Windows.'
    }
    if (-not ('Adg.LocalGroups.Native' -as [type])) {
        Add-Type -TypeDefinition $script:AdgLocalGroupNativeSource -Language CSharp
    }
}


function ConvertTo-AdgNetApiServerName {
    <#
        .SYNOPSIS
            The server argument the Net* functions take: $null for this machine, \\name otherwise.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][string] $ComputerName)

    if ($ComputerName -in @('.', 'localhost', $env:COMPUTERNAME)) { return $null }
    return '\\' + $ComputerName.TrimStart('\')
}


function Get-AdgRemoteServerInfo {
    <#
        .SYNOPSIS
            The host's NetBIOS name and whether it is a domain controller (NetServerGetInfo 101).
    #>
    [OutputType([pscustomobject])]
    param([Parameter(Mandatory)][string] $ComputerName)

    if ($null -ne $script:AdgLocalGroupFixture) {
        $entry = Get-AdgLocalGroupFixtureHost $ComputerName
        return [pscustomobject]@{
            Name               = $entry['netbios_name']
            IsDomainController = [bool] $entry['is_domain_controller']
        }
    }
    Initialize-AdgLocalGroupNative
    $info = [Adg.LocalGroups.Native]::GetServer((ConvertTo-AdgNetApiServerName $ComputerName))
    return [pscustomobject]@{
        Name               = $info.Name
        IsDomainController = $info.IsDomainController
    }
}


function Get-AdgRemoteLocalGroup {
    <#
        .SYNOPSIS
            Every local group on the host, by name (NetLocalGroupEnum level 1).
    #>
    [OutputType([pscustomobject[]])]
    param([Parameter(Mandatory)][string] $ComputerName)

    if ($null -ne $script:AdgLocalGroupFixture) {
        return @(
            (Get-AdgLocalGroupFixtureHost $ComputerName)['groups'] |
                ForEach-Object { [pscustomobject]@{ Name = $_['name']; Comment = $_['comment'] } }
        )
    }
    Initialize-AdgLocalGroupNative
    $groups = [Adg.LocalGroups.Native]::GetGroups((ConvertTo-AdgNetApiServerName $ComputerName))
    return @($groups | ForEach-Object { [pscustomobject]@{ Name = $_.Name; Comment = $_.Comment } })
}


function Resolve-AdgRemoteAccountName {
    <#
        .SYNOPSIS
            A name's SID, domain and SID type, as the host itself resolves it (LookupAccountName).
        .DESCRIPTION
            Resolved on the target, because a local group's name is the host's to interpret:
            BUILTIN group names are localized, and a machine-local group's name means
            nothing anywhere else.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [Parameter(Mandatory)][string] $Name
    )

    if ($null -ne $script:AdgLocalGroupFixture) {
        $group = (Get-AdgLocalGroupFixtureHost $ComputerName)['groups'] | Where-Object { $_['name'] -eq $Name }
        if ($null -eq $group) { throw [System.ComponentModel.Win32Exception]::new(1332) }
        return [pscustomobject]@{ Sid = $group['sid']; Domain = $group['domain']; SidUsage = 4 }
    }
    Initialize-AdgLocalGroupNative
    $account = [Adg.LocalGroups.Native]::LookupName((ConvertTo-AdgNetApiServerName $ComputerName), $Name)
    return [pscustomobject]@{ Sid = $account.Sid; Domain = $account.Domain; SidUsage = $account.SidUsage }
}


function Get-AdgRemoteLocalGroupMember {
    <#
        .SYNOPSIS
            One local group's direct members (NetLocalGroupGetMembers level 2).
        .DESCRIPTION
            Each member is its SID, whatever the host could or could not resolve it to, and
            the SID type it reported. Direct members only: Windows does not nest local
            groups, and a domain group's own members are the directory collector's to report.
    #>
    [OutputType([pscustomobject[]])]
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [Parameter(Mandatory)][string] $GroupName
    )

    if ($null -ne $script:AdgLocalGroupFixture) {
        $group = (Get-AdgLocalGroupFixtureHost $ComputerName)['groups'] | Where-Object { $_['name'] -eq $GroupName }
        if ($null -eq $group) { throw [System.ComponentModel.Win32Exception]::new(2220) }
        if ($group['unreadable']) { throw [System.ComponentModel.Win32Exception]::new(5) }
        return @(
            $group['members'] | ForEach-Object {
                [pscustomobject]@{ Sid = $_['sid']; SidUsage = [int] $_['sid_usage']; AccountName = $_['account_name'] }
            }
        )
    }
    Initialize-AdgLocalGroupNative
    $members = [Adg.LocalGroups.Native]::GetMembers((ConvertTo-AdgNetApiServerName $ComputerName), $GroupName)
    return @(
        $members | ForEach-Object {
            [pscustomobject]@{ Sid = $_.Sid; SidUsage = $_.SidUsage; AccountName = $_.AccountName }
        }
    )
}
