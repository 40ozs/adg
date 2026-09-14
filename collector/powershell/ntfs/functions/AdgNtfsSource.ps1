<#
    Acquisition layer: the only functions in this collector that touch a file system.

    Every one of them is a thin wrapper over a single Windows or .NET call, with no
    normalization and no policy. That is the point: this file is the seam the Pester suite
    mocks, so the orchestration and normalization layers can be exercised against an entire
    imaginary estate of share roots without a network, a domain, or a single real ACL.

    Keep it thin. Logic that creeps in here becomes logic that is never tested.

    ADG is read-only (ADR-0004). Nothing here writes to a target, enables a privilege, takes
    ownership, or modifies a security descriptor to make a read succeed. A directory whose
    descriptor cannot be read is reported as an error, which makes the run partial - which
    is the honest outcome, and the one that stops an unread ACL from being mistaken for an
    empty one.
#>

function Test-AdgResourceExists {
    <#
        .SYNOPSIS
            Does this UNC path name a directory the collector can see?
        .DESCRIPTION
            Separate from the descriptor read so that "the share is not there" and "the
            share is there and its ACL is unreadable" stay distinguishable. They call for
            different fixes, and an audit that reports them identically sends somebody to
            the wrong place.
    #>
    [OutputType([bool])]
    param([Parameter(Mandatory)][string] $Path)

    return Test-Path -LiteralPath $Path -PathType Container
}

function Get-AdgChildDirectory {
    <#
        .SYNOPSIS
            The immediate subdirectories of one directory, with their reparse state.
        .DESCRIPTION
            The walk's only way down. It returns Name, Path, IsReparsePoint and LinkTarget
            and nothing else: which of those children to descend into is policy, and policy
            belongs in the layer that can be exercised without a file system.

            Reparse state is reported for every child, whether or not the caller intends to
            follow one. A junction and an ordinary directory are indistinguishable by name,
            and a walk that does not ask here will follow a loop until it runs out of path.

            LinkTarget is whatever the reparse point stores, unresolved. Resolving it would
            need the file system walked again from the target, and an unresolvable target -
            a junction to a volume that is gone - is an ordinary finding rather than a
            reason to abandon the parent.

        .OUTPUTS
            An array of objects with Name, Path, IsReparsePoint, and LinkTarget.
    #>
    [OutputType([object[]])]
    param([Parameter(Mandatory)][string] $Path)

    $children = [System.Collections.Generic.List[object]]::new()
    foreach ($info in [System.IO.DirectoryInfo]::new($Path).EnumerateDirectories()) {
        $linkTarget = $null
        try { $linkTarget = $info.LinkTarget }
        catch { Write-Verbose "LinkTarget of $($info.FullName) was unreadable: $($_.Exception.Message)" }

        $children.Add([pscustomobject]@{
                Name           = $info.Name
                Path           = $info.FullName
                IsReparsePoint = ($info.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
                LinkTarget     = $linkTarget
            })
    }
    # The comma keeps an empty directory an empty array rather than nothing at all, which
    # strict mode turns into a .Count failure in the caller.
    return , $children.ToArray()
}

function Get-AdgChildFile {
    <#
        .SYNOPSIS
            The immediate files of one directory, for an opt-in file-level scan.
        .DESCRIPTION
            Never called unless file scanning is switched on. A file has a DACL like any
            other securable object and a file whose ACL differs from its folder's is a real
            finding - but an estate has orders of magnitude more files than directories, and
            reading every one of them turns a scan that takes minutes into one that takes
            days. Which is why the default is off, and why the cost is the operator's to
            accept deliberately.
    #>
    [OutputType([object[]])]
    param([Parameter(Mandatory)][string] $Path)

    $children = [System.Collections.Generic.List[object]]::new()
    foreach ($info in [System.IO.DirectoryInfo]::new($Path).EnumerateFiles()) {
        $children.Add([pscustomobject]@{
                Name           = $info.Name
                Path           = $info.FullName
                IsReparsePoint = ($info.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
            })
    }
    return , $children.ToArray()
}

function ConvertFrom-AdgRawSecurityDescriptor {
    <#
        .SYNOPSIS
            A .NET security descriptor reduced to the facts the contract carries.
        .DESCRIPTION
            Shared by the directory and file readers so the two cannot drift. A file's DACL
            and a directory's are the same structure, and reading them through two
            near-identical blocks is how one of them quietly stops reporting DaclPresent.

            Everything is taken from the raw binary form rather than from the rule
            collection, for the three reasons set out in Get-AdgDirectorySecurity.
    #>
    [OutputType([hashtable])]
    param([Parameter(Mandatory)] $Acl)

    # One binary round trip, then everything is read from the raw descriptor. This is the
    # form Windows actually stores, so nothing below is a reconstruction.
    $raw = [System.Security.AccessControl.RawSecurityDescriptor]::new($Acl.GetSecurityDescriptorBinaryForm(), 0)

    $control = $raw.ControlFlags
    $daclPresent = ($control -band [System.Security.AccessControl.ControlFlags]::DiscretionaryAclPresent) -ne 0
    $daclProtected = ($control -band [System.Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -ne 0

    $entries = [System.Collections.Generic.List[object]]::new()
    if ($daclPresent -and $null -ne $raw.DiscretionaryAcl) {
        foreach ($ace in $raw.DiscretionaryAcl) {
            $sid = $null
            $sidProperty = $ace.PSObject.Properties['SecurityIdentifier']
            if ($null -ne $sidProperty -and $null -ne $sidProperty.Value) {
                $sid = [string] $sidProperty.Value.Value
            }

            $entries.Add([pscustomobject]@{
                    AceType     = [string] $ace.AceType
                    AceFlags    = [int] $ace.AceFlags
                    AccessMask  = ConvertTo-AdgAccessMask $ace.AccessMask
                    TrusteeSid  = $sid
                    TrusteeName = Resolve-AdgTrusteeName $sid
                })
        }
    }

    return @{
        OwnerSid      = if ($null -eq $raw.Owner) { $null } else { [string] $raw.Owner.Value }
        GroupSid      = if ($null -eq $raw.Group) { $null } else { [string] $raw.Group.Value }
        DaclPresent   = $daclPresent
        DaclProtected = $daclProtected
        Ace           = $entries.ToArray()
    }
}

function Get-AdgFileSecurity {
    <#
        .SYNOPSIS
            One file's raw security descriptor, for an opt-in file-level scan.
        .DESCRIPTION
            The same descriptor a directory carries, read the same way. A file is a leaf: it
            holds inherited entries and explicit ones, and the explicit ones are exactly what
            a file-level scan exists to find - but nothing inherits *from* it, so the walk
            never descends here and never projects from here.
    #>
    [OutputType([hashtable])]
    param([Parameter(Mandatory)][string] $Path)

    $sections = [System.Security.AccessControl.AccessControlSections]::Access -bor
    [System.Security.AccessControl.AccessControlSections]::Owner -bor
    [System.Security.AccessControl.AccessControlSections]::Group

    $info = [System.IO.FileInfo]::new($Path)
    $acl = if ([System.IO.FileSystemAclExtensions] -as [type]) {
        [System.IO.FileSystemAclExtensions]::GetAccessControl($info, $sections)
    }
    else {
        Get-Acl -LiteralPath $Path -ErrorAction Stop
    }

    return ConvertFrom-AdgRawSecurityDescriptor -Acl $acl
}

function Get-AdgDirectorySecurity {
    <#
        .SYNOPSIS
            One directory's raw security descriptor: owner, control flags, and DACL entries.
        .DESCRIPTION
            Reads the descriptor through the .NET security-descriptor APIs rather than
            through Get-Acl's formatted output, because three facts the contract needs
            survive only in the raw form:

              * whether a DACL is present at all. A NULL DACL grants every user full access;
                an empty DACL grants nobody access. Both look like "no rules" through the
                rule collection, and they are opposite facts. ControlFlags says which.
              * the raw ACE_HEADER.AceFlags byte. FileSystemAccessRule splits it across
                InheritanceFlags, PropagationFlags, and IsInherited, which loses any bit
                those three enumerations do not name.
              * the entries in DACL order. Evaluation order is what makes a Deny meaningful,
                and a rule collection is not promised to preserve it.

            Trustees come back as SIDs, never as names. A descriptor stores SIDs, so an
            account that no longer resolves still yields a usable ACE - which is exactly the
            orphaned-trustee finding this tool exists to report. TrusteeName is filled in
            only as metadata, and its absence is what marks a SID unresolved.

            SACL is deliberately not requested. Audit entries govern logging, not access,
            and reading them needs SeSecurityPrivilege - a privilege ADG has no reason to
            hold. Owner and Group are requested because the owner holds implicit
            READ_CONTROL and WRITE_DAC whatever the DACL says.

        .OUTPUTS
            A hashtable with OwnerSid, GroupSid, DaclPresent, DaclProtected, and Ace.
    #>
    [OutputType([hashtable])]
    param([Parameter(Mandatory)][string] $Path)

    $sections = [System.Security.AccessControl.AccessControlSections]::Access -bor
    [System.Security.AccessControl.AccessControlSections]::Owner -bor
    [System.Security.AccessControl.AccessControlSections]::Group

    $info = [System.IO.DirectoryInfo]::new($Path)
    # FileSystemAclExtensions is the .NET 6+ home of GetAccessControl; the instance method
    # was removed when the API moved out of the base class library. Get-Acl is the fallback
    # rather than the default: it returns the same DirectorySecurity, but it goes through
    # the PowerShell provider stack, which adds failure modes that have nothing to do with
    # the file system.
    $acl = if ([System.IO.FileSystemAclExtensions] -as [type]) {
        [System.IO.FileSystemAclExtensions]::GetAccessControl($info, $sections)
    }
    else {
        Get-Acl -LiteralPath $Path -ErrorAction Stop
    }

    return ConvertFrom-AdgRawSecurityDescriptor -Acl $acl
}

function Resolve-AdgTrusteeName {
    <#
        .SYNOPSIS
            Translate a SID to an account name, or return $null.
        .DESCRIPTION
            Metadata only, and never identity: the ACE is reported by SID whatever this
            returns. $null is an ordinary observation about a real environment - a deleted
            account, a broken trust, a SID from a domain this host cannot reach - and the
            caller turns it into an unresolved-principal observation, which is a finding
            rather than a failure.
    #>
    [OutputType([string])]
    param([AllowNull()][AllowEmptyString()][string] $Sid)

    if ([string]::IsNullOrWhiteSpace($Sid)) { return $null }

    try {
        $identifier = [System.Security.Principal.SecurityIdentifier]::new($Sid)
        return $identifier.Translate([System.Security.Principal.NTAccount]).Value
    }
    catch {
        Write-Verbose "$Sid did not translate to a name: $($_.Exception.Message)"
        return $null
    }
}
