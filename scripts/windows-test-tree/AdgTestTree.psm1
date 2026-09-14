#Requires -Version 7.0
<#
.SYNOPSIS
    Build repeatable local NTFS trees that exercise the cases an ACL scanner gets wrong.

.DESCRIPTION
    Every other NTFS test suite in this repository either mocks the file system or writes a
    handful of directories inline. Both are necessary and neither answers the question this
    module exists for: **does the collector describe a real tree the way Windows describes
    it?** Answering that needs a tree whose every interesting case was put there on purpose
    and written down, so a disagreement can be traced to a construction rather than guessed
    at.

    So this module builds trees and emits a manifest. The manifest is the contract: for each
    directory it records how the directory was built - protected or not, which explicit ACEs
    were added, whether it is a reparse point, whether its contents can be listed - and what
    the collector is therefore expected to say about it. The validation suite compares three
    things that must agree: what this module built, what Windows stored, and what the
    collector reported.

    ------------------------------------------------------------------------------------
    This module writes ACLs. ADR-0004 is not suspended.

    ADR-0004 makes the *application* read-only: the collector never writes to a target,
    never enables a privilege, never takes ownership, never modifies a descriptor to make a
    read succeed. That rule is about the thing that ships. This module is a developer and CI
    tool, it writes only beneath a root the caller names, it writes only through the Access
    section of a descriptor (never the SACL, which would need SeSecurityPrivilege), and
    Remove-AdgTestTree restores every ACL it changed before deleting anything.

    Everything here runs unelevated, as the collector must.

    ------------------------------------------------------------------------------------
    Two kinds of tree

    'semantics' is the correctness tree: small, hand-specified, one directory per case, and
    every directory carrying an expectation. It is what the validation suite runs against.

    'small' / 'medium' / 'large' are the performance trees: a shaped tree of a given size
    whose ACLs are drawn from a fixed small set, so the ratio of directories to distinct
    ACLs - the number the whole boundary design rests on - is known in advance rather than
    discovered. They carry no per-directory expectations, because their point is cost.

    ------------------------------------------------------------------------------------
    One case cannot be built unelevated, and it is not pretended

    A directory whose *descriptor* cannot be read. The owner of an object holds READ_CONTROL
    and WRITE_DAC implicitly, whatever the DACL says, and handing ownership to somebody else
    needs SeRestorePrivilege. So the generator builds the case it can build honestly -
    a directory whose descriptor reads fine and whose **contents cannot be listed**, which is
    a different right (FILE_LIST_DIRECTORY, not READ_CONTROL) and the case that actually
    turns up in an estate. The unreadable-descriptor path stays covered by the mocked walk
    suite, and the gap is recorded in the manifest as `deniedDescriptorNotBuildable`.
#>

Set-StrictMode -Version Latest

# Well-known SIDs, so a tree built on one machine carries the same trustees as a tree built
# on another. The last one resolves nowhere on purpose: an ACE naming an account that no
# longer exists is one of the findings ADG exists to report, and it has to survive a real
# descriptor round trip to be worth reporting.
$script:Sids = @{
    Everyone            = 'S-1-1-0'
    AuthenticatedUsers  = 'S-1-5-11'
    Users               = 'S-1-5-32-545'
    Administrators      = 'S-1-5-32-544'
    CreatorOwner        = 'S-1-3-0'
    Orphaned            = 'S-1-5-21-1111111111-2222222222-3333333333-1001'
}

# 0xe0010000: GENERIC_READ | GENERIC_WRITE | GENERIC_EXECUTE | READ_CONTROL - the generic
# form of Modify, which Explorer puts on directories and which broke both the mask reader
# and the inheritance projection in Phase 3B. FileSystemAccessRule cannot express it, so it
# is written through the raw descriptor.
$script:GenericModifyMask = 0xe0010000

function Resolve-AdgTestTreePath {
    <#
        .SYNOPSIS
            A relative path made absolute against the shell's location, not .NET's.
        .DESCRIPTION
            PowerShell's current location and the .NET process working directory are two
            different things, and they drift apart the moment anybody calls Set-Location. So
            a relative path handed to [System.IO.Path]::GetFullPath does not mean what it
            reads as: it resolves against whatever directory the process happened to start
            in, not against the directory the operator is standing in.

            That is a nuisance almost everywhere and a hazard here, because this module
            deletes what it is pointed at. It was caught doing exactly that - a -Remove given
            a relative root reported success against a path under the user's profile while
            the real tree stood untouched, and the next run silently reused the stale tree and
            measured it.
    #>
    [OutputType([string])]
    param([Parameter(Mandatory)][string] $Path)

    if ([System.IO.Path]::IsPathRooted($Path)) { return [System.IO.Path]::GetFullPath($Path) }
    return [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($PWD.ProviderPath, $Path))
}

function Get-AdgTestTreeOption {
    <#
        .SYNOPSIS
            One optional key of an instruction hashtable, or a default.
        .DESCRIPTION
            Strict mode turns a missing key into an error rather than into $null, and every
            instruction in the specification omits most of them. ContainsKey is the only
            reading that treats "not asked for" as a value rather than as a fault.
    #>
    param(
        [AllowNull()][hashtable] $Option,
        [Parameter(Mandatory)][string] $Name,
        $Default = $false
    )

    if ($null -eq $Option) { return $Default }
    if (-not $Option.ContainsKey($Name)) { return $Default }
    if ($null -eq $Option[$Name]) { return $Default }
    return $Option[$Name]
}

function Get-AdgTestTreeSid {
    <#
        .SYNOPSIS
            The well-known SIDs this module builds ACLs from, by name.
    #>
    [OutputType([hashtable])]
    param()
    return $script:Sids.Clone()
}

function Get-AdgTestTreeUncPath {
    <#
        .SYNOPSIS
            The UNC spelling of a local path, via the drive's administrative share.
        .DESCRIPTION
            The collector identifies a directory by its UNC path and refuses a drive-letter
            one, because a drive letter does not say which server it is on. On a local test
            machine the only UNC route to an ordinary directory is \\localhost\C$ - which
            needs local Administrators, a right the collector must never require and this
            module therefore never assumes. Callers pair this with
            Test-AdgTestTreeUncAccess and skip rather than fail.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string] $Path,
        [string] $Server = 'localhost'
    )

    $full = Resolve-AdgTestTreePath $Path
    if ($full -match '^([A-Za-z]):\\(.*)$') {
        return ('\\{0}\{1}$\{2}' -f $Server, $Matches[1], $Matches[2]).TrimEnd('\')
    }
    if ($full.StartsWith('\\')) { return $full.TrimEnd('\') }
    throw "'$Path' is neither a drive-letter path nor a UNC path, so it has no UNC spelling."
}

function Test-AdgTestTreeUncAccess {
    <#
        .SYNOPSIS
            Can this process reach that local path over UNC?
        .DESCRIPTION
            One listing attempt. A failure is an ordinary answer - an unelevated session on a
            machine with administrative shares disabled gets one - and the caller's job is to
            report the suite as skipped with a reason, never to report it as passed.
    #>
    [OutputType([bool])]
    param([Parameter(Mandatory)][string] $Path)

    try {
        $unc = Get-AdgTestTreeUncPath -Path $Path
        [void] ([System.IO.DirectoryInfo]::new($unc).EnumerateDirectories() | Select-Object -First 1)
        return $true
    }
    catch {
        Write-Verbose "No UNC route to '$Path': $($_.Exception.Message)"
        return $false
    }
}

function Get-AdgTestTreeAccess {
    <#
        .SYNOPSIS
            The Access section of one object's descriptor, and nothing else.
        .DESCRIPTION
            Get-Acl without an explicit section mask returns a descriptor claiming the SACL
            too, and handing that to Set-Acl tries to write it - which needs
            SeSecurityPrivilege. Reading and writing the Access section alone keeps every
            change inside what an unelevated owner may do.
    #>
    param([Parameter(Mandatory)][string] $Path, [switch] $AsFile)

    $info = if ($AsFile) { [System.IO.FileInfo]::new($Path) } else { [System.IO.DirectoryInfo]::new($Path) }
    return [System.IO.FileSystemAclExtensions]::GetAccessControl($info, 'Access')
}

function Set-AdgTestTreeAccess {
    <#
        .SYNOPSIS
            Write back an Access-only descriptor.
    #>
    param([Parameter(Mandatory)][string] $Path, [Parameter(Mandatory)] $Security, [switch] $AsFile)

    $info = if ($AsFile) { [System.IO.FileInfo]::new($Path) } else { [System.IO.DirectoryInfo]::new($Path) }
    [System.IO.FileSystemAclExtensions]::SetAccessControl($info, $Security)
}

function Get-AdgTestTreeAccessRule {
    <#
        .SYNOPSIS
            A descriptor's rules as SIDs.
        .DESCRIPTION
            A raw FileSystemSecurity has no .Access note property - Get-Acl's PSObject
            wrapper adds that - and reaching for one under strict mode is an error rather
            than an empty list.
    #>
    param([Parameter(Mandatory)] $Security)
    return @($Security.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
}

function New-AdgTestTreeAce {
    <#
        .SYNOPSIS
            One ACE specification, for Set-AdgTestTreeAcl.
        .PARAMETER Mask
            A raw access mask instead of named rights. Use it for a generic mask, which
            FileSystemAccessRule cannot express and which is not exotic: 0xe0010000 sits on
            almost every directory Explorer creates.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $Sid,
        [System.Security.AccessControl.FileSystemRights] $Rights = 'ReadAndExecute',
        [System.Security.AccessControl.InheritanceFlags] $Inheritance = 'None',
        [System.Security.AccessControl.PropagationFlags] $Propagation = 'None',
        [ValidateSet('Allow', 'Deny')][string] $Type = 'Allow',
        [Nullable[long]] $Mask,
        [Nullable[int]] $Flags
    )

    return [pscustomobject]@{
        Sid         = $Sid
        Rights      = $Rights
        Inheritance = $Inheritance
        Propagation = $Propagation
        Type        = $Type
        Mask        = $Mask
        Flags       = $Flags
    }
}

function Set-AdgTestTreeAcl {
    <#
        .SYNOPSIS
            Put a known DACL on one directory.
        .DESCRIPTION
            Three steps, in this order, because the order is what makes the result
            predictable: set or clear inheritance protection, remove the explicit rules that
            are already there, then add the ones asked for.

            -Protect with -CopyInherited is "disable inheritance, convert to explicit" -
            the Explorer button. -Protect without it is "disable inheritance, remove
            everything", which leaves a directory whose entire DACL is what this function
            adds. Neither is expressible through the other, and a scanner has to tell them
            apart.

            An ACE carrying -Mask or -Flags is written through the raw descriptor, because
            FileSystemAccessRule maps a mask through the file-system rights enumeration on
            the way in and cannot carry a generic bit at all.
    #>
    param(
        [Parameter(Mandatory)][string] $Path,
        [switch] $Protect,
        [switch] $CopyInherited,
        [object[]] $Ace = @(),
        [switch] $KeepExistingExplicit,
        [switch] $AsFile
    )

    $security = Get-AdgTestTreeAccess -Path $Path -AsFile:$AsFile
    $security.SetAccessRuleProtection([bool] $Protect, [bool] $CopyInherited)

    if (-not $KeepExistingExplicit) {
        foreach ($rule in (Get-AdgTestTreeAccessRule $security)) {
            if (-not $rule.IsInherited) { [void] $security.RemoveAccessRuleSpecific($rule) }
        }
    }

    $raw = @()
    foreach ($entry in @($Ace)) {
        if ($null -ne $entry.Mask -or $null -ne $entry.Flags) { $raw += $entry; continue }

        $identity = [System.Security.Principal.SecurityIdentifier]::new($entry.Sid)
        $rule = if ($AsFile) {
            [System.Security.AccessControl.FileSystemAccessRule]::new(
                $identity, $entry.Rights, 'None', 'None', $entry.Type)
        }
        else {
            [System.Security.AccessControl.FileSystemAccessRule]::new(
                $identity, $entry.Rights, $entry.Inheritance, $entry.Propagation, $entry.Type)
        }
        $security.AddAccessRule($rule)
    }

    Set-AdgTestTreeAccess -Path $Path -Security $security -AsFile:$AsFile
    if (@($raw).Count -eq 0) { return }

    # The raw pass, for masks the managed rule type cannot express. Read back first: the
    # descriptor on disk now is the one the managed pass produced, and rebuilding it from
    # the in-memory object would drop whatever Windows canonicalized.
    $current = Get-AdgTestTreeAccess -Path $Path -AsFile:$AsFile
    $descriptor = [System.Security.AccessControl.RawSecurityDescriptor]::new(
        $current.GetSecurityDescriptorBinaryForm(), 0)

    foreach ($entry in $raw) {
        $flags = if ($null -ne $entry.Flags) { [int] $entry.Flags } else {
            [int] ([System.Security.AccessControl.AceFlags] ("$($entry.Inheritance),$($entry.Propagation)" -replace 'None,?', '' -replace ',$', ''))
        }
        $mask = if ($null -ne $entry.Mask) { [long] $entry.Mask } else { [long] $entry.Rights }
        $qualifier = if ($entry.Type -eq 'Deny') {
            [System.Security.AccessControl.AceQualifier]::AccessDenied
        }
        else {
            [System.Security.AccessControl.AceQualifier]::AccessAllowed
        }

        $descriptor.DiscretionaryAcl.InsertAce(
            $descriptor.DiscretionaryAcl.Count,
            [System.Security.AccessControl.CommonAce]::new(
                [System.Security.AccessControl.AceFlags] $flags,
                $qualifier,
                # Narrowing to Int32 is the reinterpretation the CommonAce constructor wants:
                # a mask with the top bit set is a negative Int32, which is exactly how .NET
                # surfaces it on the way back out. See ConvertTo-AdgAccessMask.
                [int] ($mask -band 0xFFFFFFFF),
                [System.Security.Principal.SecurityIdentifier]::new($entry.Sid),
                $false, $null))
    }

    $bytes = [byte[]]::new($descriptor.BinaryLength)
    $descriptor.GetBinaryForm($bytes, 0)
    $target = if ($AsFile) {
        [System.Security.AccessControl.FileSecurity]::new()
    }
    else {
        [System.Security.AccessControl.DirectorySecurity]::new()
    }
    # The Access-only overload. A descriptor built from the full binary form claims the
    # SACL, and writing that needs SeSecurityPrivilege.
    $target.SetSecurityDescriptorBinaryForm($bytes, [System.Security.AccessControl.AccessControlSections]::Access)
    Set-AdgTestTreeAccess -Path $Path -Security $target -AsFile:$AsFile
}

function New-AdgTestTreeJunction {
    <#
        .SYNOPSIS
            A junction, optionally left pointing at nothing.
        .DESCRIPTION
            Junctions rather than symbolic links throughout: a directory symbolic link needs
            SeCreateSymbolicLinkPrivilege or Developer Mode, and a suite that silently needs
            either is a suite that silently does not run. A junction is the reparse point an
            estate actually has.

            -Dangling creates the target, links to it, and removes the target again, because
            New-Item validates that a junction target exists. A junction whose target is gone
            is an ordinary finding - a decommissioned volume - and the walk must report it
            rather than abandon the parent.
    #>
    param(
        [Parameter(Mandatory)][string] $Path,
        [Parameter(Mandatory)][string] $Target,
        [switch] $Dangling
    )

    if ($Dangling -and -not (Test-Path -LiteralPath $Target)) {
        New-Item -ItemType Directory -Path $Target -Force | Out-Null
    }
    New-Item -ItemType Junction -Path $Path -Target $Target -ErrorAction Stop | Out-Null
    if ($Dangling) { Remove-Item -LiteralPath $Target -Recurse -Force }
}

function New-AdgTestTreeNode {
    <#
        .SYNOPSIS
            Create one directory and record what it is, for the manifest.
        .PARAMETER ExpectedBoundary
            Whether the collector should report an ACL boundary here, or $null where the
            answer depends on something outside this directory (the scan root, a parent that
            could not be read). Recorded at construction time and deliberately not derived
            from the ACL afterwards: an expectation computed by the same arithmetic as the
            implementation tests nothing.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $Path,
        [Parameter(Mandatory)][string] $Case,
        [Parameter(Mandatory)][string] $Note,
        [Nullable[bool]] $ExpectedBoundary,
        [string] $ExpectedBoundaryReason,
        [switch] $Protected,
        [switch] $ReparsePoint,
        [switch] $Dangling,
        [bool] $Enumerable = $true,
        [bool] $Reported = $true,
        [string] $AclGroup,
        [string[]] $ExpectedTrustee = @()
    )

    return [pscustomobject]@{
        path                   = $Path
        case                   = $Case
        note                   = $Note
        protected              = [bool] $Protected
        reparsePoint           = [bool] $ReparsePoint
        danglingReparsePoint   = [bool] $Dangling
        enumerable             = $Enumerable
        reported               = $Reported
        aclGroup               = if ([string]::IsNullOrWhiteSpace($AclGroup)) { $null } else { $AclGroup }
        expectedBoundary       = $ExpectedBoundary
        expectedBoundaryReason = if ([string]::IsNullOrWhiteSpace($ExpectedBoundaryReason)) { $null } else { $ExpectedBoundaryReason }
        expectedTrustees       = @($ExpectedTrustee)
    }
}

function Get-AdgSemanticsTreeSpec {
    <#
        .SYNOPSIS
            The correctness tree, declared: one entry per directory, with its expectation.
        .DESCRIPTION
            Declared rather than built, because the tree has to be created in two passes and
            the order matters in a way that is easy to get wrong. A directory carrying a Deny
            ACE that descends cannot have children created beneath it afterwards - the
            generator is subject to the ACLs it writes, exactly as any other process is - and
            a directory protected before its children exist would propagate nothing to them.

            So: every directory and junction is created first, then every ACL is applied
            root-first. Windows propagates an inheritable entry to the children that already
            exist, which is what makes the second pass correct.

            Eleven cases. Ten are the ones Phase 3C names; the eleventh is a generic access
            mask, which is not exotic - it sits on almost every directory Explorer creates,
            and it broke both the mask reader and the inheritance projection in Phase 3B
            while every fixture-based test went on passing.

        .OUTPUTS
            One object per directory, in creation order, with Relative, Case, Note, the
            expectation fields, and an optional Acl or Junction instruction.
    #>
    [OutputType([object[]])]
    param()

    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $sid = $script:Sids
    $spec = [System.Collections.Generic.List[object]]::new()

    function Add-Spec {
        param(
            [string] $Relative,
            [string] $Case,
            [string] $Note,
            [Nullable[bool]] $Boundary,
            [string] $Reason,
            [string] $AclGroup,
            [string[]] $Trustee = @(),
            [hashtable] $Acl,
            [hashtable] $Junction,
            [bool] $Enumerable = $true,
            [bool] $Reported = $true
        )
        $spec.Add([pscustomobject]@{
                Relative   = $Relative
                Case       = $Case
                Note       = $Note
                Boundary   = $Boundary
                Reason     = $Reason
                AclGroup   = $AclGroup
                Trustee    = @($Trustee)
                Acl        = $Acl
                Junction   = $Junction
                Enumerable = $Enumerable
                Reported   = $Reported
            })
    }

    # ---------------------------------------------------------------- the root
    #
    # Protected and stripped, so nothing above the root - a developer's temp directory, a CI
    # agent's profile - reaches any directory in the tree. Without this the tree's ACLs would
    # differ between two machines and every expectation below would be a guess.
    Add-Spec -Relative '' -Case 'root' `
        -Note 'The scan root: protected, so the tree is identical on every machine. Its reason is protected_dacl rather than scan_root - protection is tested first, because it settles the question from this row alone while a scan root merely means nobody looked higher.' `
        -Boundary $true -Reason 'protected_dacl' -Trustee @($me, $sid.Users) `
        -Acl @{
        Protect = $true
        Ace     = @(
            (New-AdgTestTreeAce -Sid $me -Rights FullControl -Inheritance 'ContainerInherit,ObjectInherit'),
            (New-AdgTestTreeAce -Sid $sid.Users -Rights ReadAndExecute -Inheritance 'ContainerInherit,ObjectInherit')
        )
    }

    # -------------------------------------------- 01 a fully inherited subtree
    foreach ($relative in @('01-inherited', '01-inherited\child-a', '01-inherited\child-b',
            '01-inherited\child-a\grandchild')) {
        Add-Spec -Relative $relative -Case '01-fully-inherited' `
            -Note 'Nothing was written here. Its DACL must be exactly what the parent projects.' `
            -Boundary $false -AclGroup 'inherited-from-root' -Trustee @($me, $sid.Users)
    }

    # ------------------------------------------------ 02 explicit child ACEs
    Add-Spec -Relative '02-explicit-child' -Case '02-explicit-child' `
        -Note 'Carries nothing of its own; the container for the explicit cases below.' `
        -Boundary $false -AclGroup 'inherited-from-root'

    Add-Spec -Relative '02-explicit-child\plain' -Case '02-explicit-child' `
        -Note 'The control: a sibling of the explicit ones that inherits cleanly.' `
        -Boundary $false -AclGroup 'inherited-from-root'

    Add-Spec -Relative '02-explicit-child\explicit-here-only' -Case '02-explicit-child' `
        -Note 'One explicit ACE with no inheritance flags: this directory differs from the projection, and nothing below it does.' `
        -Boundary $true -Reason 'acl_differs_from_parent' -Trustee @($sid.AuthenticatedUsers) `
        -Acl @{ KeepExistingExplicit = $true; Ace = @(New-AdgTestTreeAce -Sid $sid.AuthenticatedUsers -Rights Modify) }

    Add-Spec -Relative '02-explicit-child\explicit-here-only\below' -Case '02-explicit-child' `
        -Note 'Below a non-inheriting explicit ACE: it carries the parent projection exactly, so it is not a boundary even though its parent is.' `
        -Boundary $false

    Add-Spec -Relative '02-explicit-child\explicit-inheriting' -Case '02-explicit-child' `
        -Note 'An explicit ACE that descends. A boundary here; everything below inherits it and is not.' `
        -Boundary $true -Reason 'acl_differs_from_parent' -Trustee @($sid.AuthenticatedUsers) `
        -Acl @{
        KeepExistingExplicit = $true
        Ace                  = @(New-AdgTestTreeAce -Sid $sid.AuthenticatedUsers -Rights Modify -Inheritance 'ContainerInherit,ObjectInherit')
    }

    foreach ($relative in @('02-explicit-child\explicit-inheriting\below',
            '02-explicit-child\explicit-inheriting\below\deeper')) {
        Add-Spec -Relative $relative -Case '02-explicit-child' `
            -Note 'Inherits the descending ACE. The projection reaches a fixed point after one level, which is the property that makes one comparison correct for a whole uniform subtree.' `
            -Boundary $false -AclGroup 'inherits-authenticated-modify' -Trustee @($sid.AuthenticatedUsers)
    }

    # ------------------------------------------- 03 inheritance disabled, both ways
    Add-Spec -Relative '03-inheritance-disabled' -Case '03-inheritance-disabled' -Note 'Container only.' `
        -Boundary $false -AclGroup 'inherited-from-root'

    Add-Spec -Relative '03-inheritance-disabled\protected-copied' -Case '03-inheritance-disabled' `
        -Note 'Inheritance disabled with the inherited entries converted to explicit - the Explorer button. The entries look the same; the INHERITED bit and the protected flag do not.' `
        -Boundary $true -Reason 'protected_dacl' `
        -Acl @{ Protect = $true; CopyInherited = $true; KeepExistingExplicit = $true }

    Add-Spec -Relative '03-inheritance-disabled\protected-copied\below' -Case '03-inheritance-disabled' `
        -Note 'Below a protected parent. It inherits from that parent normally, so it is not itself a boundary.' `
        -Boundary $false

    Add-Spec -Relative '03-inheritance-disabled\protected-emptied' -Case '03-inheritance-disabled' `
        -Note 'Inheritance disabled and the inherited entries dropped. A different fact from protected-copied, and a rule collection cannot tell them apart.' `
        -Boundary $true -Reason 'protected_dacl' -Trustee @($me, $sid.Administrators) `
        -Acl @{
        Protect = $true
        Ace     = @(
            (New-AdgTestTreeAce -Sid $me -Rights FullControl -Inheritance 'ContainerInherit,ObjectInherit'),
            (New-AdgTestTreeAce -Sid $sid.Administrators -Rights FullControl -Inheritance 'ContainerInherit,ObjectInherit')
        )
    }

    # -------------------------------------------------- 04 inheritance re-enabled
    #
    # Protected, then un-protected again, and the copied entries removed. The DACL ends up
    # equivalent to a directory that was never touched - which is the point: a scanner that
    # recorded "inheritance was once disabled here" from anything but the current descriptor
    # would be wrong.
    Add-Spec -Relative '04-inheritance-reenabled' -Case '04-inheritance-reenabled' -Note 'Container only.' `
        -Boundary $false -AclGroup 'inherited-from-root'

    Add-Spec -Relative '04-inheritance-reenabled\restored' -Case '04-inheritance-reenabled' `
        -Note 'Inheritance disabled, then re-enabled and the copied entries removed. Back to a purely inherited DACL, and it must read as one.' `
        -Boundary $false -AclGroup 'inherited-from-root' `
        -Acl @{ Restore = $true }

    Add-Spec -Relative '04-inheritance-reenabled\restored\below' -Case '04-inheritance-reenabled' `
        -Note 'Below the restored directory.' -Boundary $false -AclGroup 'inherited-from-root'

    # ------------------------------------------------------------ 05 a deep tree
    Add-Spec -Relative '05-deep' -Case '05-deep' -Note 'Container only.' `
        -Boundary $false -AclGroup 'inherited-from-root'
    $relative = '05-deep'
    for ($level = 1; $level -le 12; $level++) {
        $relative = Join-Path $relative ('d{0:d2}' -f $level)
        Add-Spec -Relative $relative -Case '05-deep' `
            -Note "Level $level of a twelve-level chain. Depth is what a maxDepth setting truncates, and a truncated tree reported as a complete one is the failure that matters." `
            -Boundary $false -AclGroup 'inherited-from-root'
    }

    # ------------------------------------------------------------ 06 a wide tree
    Add-Spec -Relative '06-wide' -Case '06-wide' -Note 'Container only.' `
        -Boundary $false -AclGroup 'inherited-from-root'
    for ($index = 1; $index -le 64; $index++) {
        Add-Spec -Relative ('06-wide\w{0:d4}' -f $index) -Case '06-wide' `
            -Note 'One of 64 siblings, all identical. Width is what the walk reads one level of at a time.' `
            -Boundary $false -AclGroup 'inherited-from-root'
    }

    # ------------------------------------------------- 07 an inaccessible directory
    #
    # Denied *enumeration*, not a denied descriptor: see the module header for why the second
    # cannot be built by an unelevated owner.
    Add-Spec -Relative '07-inaccessible' -Case '07-inaccessible' -Note 'Container only.' `
        -Boundary $false -AclGroup 'inherited-from-root'

    Add-Spec -Relative '07-inaccessible\unlistable' -Case '07-inaccessible' -Enumerable $false `
        -Note 'Its descriptor reads fine and its contents cannot be listed - FILE_LIST_DIRECTORY is a different right from READ_CONTROL. An ordinary result, and the run must stay non-exhaustive because of it.' `
        -Boundary $true -Reason 'acl_differs_from_parent' -Trustee @($me) `
        -Acl @{
        KeepExistingExplicit = $true
        Ace                  = @(New-AdgTestTreeAce -Sid $me -Rights ListDirectory -Type Deny -Inheritance 'ContainerInherit')
    }

    Add-Spec -Relative '07-inaccessible\unlistable\beneath' -Case '07-inaccessible' -Reported $false `
        -Note 'Exists, and must not be reported: the walk cannot list its parent, so it was never seen. Unobserved is not absent.' `
        -Boundary $null

    # ------------------------------------------------------- 08 reparse points
    Add-Spec -Relative '08-reparse' -Case '08-reparse' -Note 'Container, and the target of the cycle junction.' `
        -Boundary $false -AclGroup 'inherited-from-root'

    Add-Spec -Relative '08-reparse\target' -Case '08-reparse' `
        -Note 'An ordinary directory that a junction points at. Reported under its own path, once.' `
        -Boundary $false -AclGroup 'inherited-from-root'

    Add-Spec -Relative '08-reparse\target\inside' -Case '08-reparse' `
        -Note 'Below the junction target, reached by the real path.' `
        -Boundary $false -AclGroup 'inherited-from-root'

    Add-Spec -Relative '08-reparse\sibling-link' -Case '08-reparse' `
        -Note 'A junction to a sibling. Its own descriptor is real and is reported; what is below it belongs to the target.' `
        -Boundary $false -Junction @{ Target = '08-reparse\target' }

    Add-Spec -Relative '08-reparse\cycle-link' -Case '08-reparse' `
        -Note 'A junction to its own parent. Every path through it is new, so a visited-path set never fires - only comparing reparse targets along the branch ends the walk.' `
        -Boundary $false -Junction @{ Target = '08-reparse' }

    # Reported, and that is the point. The link is a real directory with a real descriptor;
    # only what it points at is gone. A walk that dropped the link because its target had
    # been decommissioned would lose the ACL of an object that still exists.
    Add-Spec -Relative '08-reparse\dangling-link' -Case '08-reparse' -Enumerable $false `
        -Note 'A junction whose target no longer exists - a decommissioned volume. Its own descriptor is read and reported; only following it fails, and under any policy but follow the walk never tries.' `
        -Boundary $false -Junction @{ Target = '08-reparse\gone'; Dangling = $true }

    # ---------------------------------------------------------- 09 mixed Allow/Deny
    Add-Spec -Relative '09-mixed-allow-deny' -Case '09-mixed-allow-deny' -Note 'Container only.' `
        -Boundary $false -AclGroup 'inherited-from-root'

    Add-Spec -Relative '09-mixed-allow-deny\deny-then-allow' -Case '09-mixed-allow-deny' `
        -Note 'A Deny ahead of an Allow for the same trustee. Evaluation order is what makes the Deny mean anything, so the collector must preserve DACL order rather than re-derive it.' `
        -Boundary $true -Reason 'protected_dacl' -Trustee @($sid.Everyone) `
        -Acl @{
        Protect = $true
        Ace     = @(
            (New-AdgTestTreeAce -Sid $me -Rights FullControl -Inheritance 'ContainerInherit,ObjectInherit'),
            (New-AdgTestTreeAce -Sid $sid.Everyone -Rights Write -Type Deny -Inheritance 'ContainerInherit,ObjectInherit'),
            (New-AdgTestTreeAce -Sid $sid.Everyone -Rights Modify -Inheritance 'ContainerInherit,ObjectInherit')
        )
    }

    Add-Spec -Relative '09-mixed-allow-deny\deny-then-allow\below' -Case '09-mixed-allow-deny' `
        -Note 'Inherits both halves, in order.' -Boundary $false

    # --------------------------------- 10 duplicate ACLs across unrelated branches
    #
    # Two directories in different branches given identical DACLs. Their acl_hash must be
    # equal - that equality is the whole storage argument - and their boundary verdicts must
    # still be reached independently.
    Add-Spec -Relative '10-duplicate-acls' -Case '10-duplicate-acls' -Note 'Container only.' `
        -Boundary $false -AclGroup 'inherited-from-root'

    foreach ($branch in @('branch-a', 'branch-b')) {
        Add-Spec -Relative "10-duplicate-acls\$branch" -Case '10-duplicate-acls' -Note 'Container only.' `
            -Boundary $false -AclGroup 'inherited-from-root'

        Add-Spec -Relative "10-duplicate-acls\$branch\team" -Case '10-duplicate-acls' `
            -Note 'One of two identically permissioned directories in unrelated branches. Same acl_hash, independent boundary verdicts, and one orphaned trustee that resolves to no name.' `
            -Boundary $true -Reason 'protected_dacl' -AclGroup 'duplicate-team-acl' `
            -Trustee @($me, $sid.Users, $sid.Orphaned) `
            -Acl @{
            Protect = $true
            Ace     = @(
                (New-AdgTestTreeAce -Sid $me -Rights FullControl -Inheritance 'ContainerInherit,ObjectInherit'),
                (New-AdgTestTreeAce -Sid $sid.Users -Rights ReadAndExecute -Inheritance 'ContainerInherit,ObjectInherit'),
                (New-AdgTestTreeAce -Sid $sid.Orphaned -Rights Modify -Inheritance 'ContainerInherit,ObjectInherit')
            )
        }
    }

    # --------------------------------------------------------- 11 a generic mask
    Add-Spec -Relative '11-generic-mask' -Case '11-generic-mask' -Note 'Container only.' `
        -Boundary $false -AclGroup 'inherited-from-root'

    Add-Spec -Relative '11-generic-mask\generic-modify' -Case '11-generic-mask' `
        -Note '0xe0010000 with CONTAINER_INHERIT|OBJECT_INHERIT. Windows materializes this onto a child as two ACEs - a mapped effective copy and an unmapped INHERIT_ONLY propagating copy - and a projection that maps one to one reports every directory below as a boundary.' `
        -Boundary $true -Reason 'protected_dacl' -Trustee @($sid.Everyone) `
        -Acl @{
        Protect = $true
        Ace     = @(
            (New-AdgTestTreeAce -Sid $me -Rights FullControl -Inheritance 'ContainerInherit,ObjectInherit'),
            (New-AdgTestTreeAce -Sid $sid.Everyone -Mask $script:GenericModifyMask -Flags 0x03)
        )
    }

    Add-Spec -Relative '11-generic-mask\generic-modify\below' -Case '11-generic-mask' `
        -Note 'The child of the generic ACE: it carries the split pair, and must not read as a boundary.' `
        -Boundary $false

    Add-Spec -Relative '11-generic-mask\generic-modify\below\deeper' -Case '11-generic-mask' `
        -Note 'One level further, where the split pair has reached its fixed point.' `
        -Boundary $false

    return , $spec.ToArray()
}

function New-AdgSemanticsTree {
    <#
        .SYNOPSIS
            Build the correctness tree from its specification, in two passes.
        .DESCRIPTION
            Pass one creates every directory and junction. Pass two writes every ACL, in
            specification order, which is root first.

            Both halves of that are load-bearing. A directory carrying a descending Deny
            cannot have children created beneath it afterwards - the generator obeys the ACLs
            it writes like any other process - and a directory protected before its children
            exist would propagate nothing to them. Creating everything first and then writing
            ACLs top-down sidesteps both: Windows propagates an inheritable entry to the
            children that already exist.
    #>
    [OutputType([object[]])]
    param([Parameter(Mandatory)][string] $Root)

    $spec = Get-AdgSemanticsTreeSpec
    $nodes = [System.Collections.Generic.List[object]]::new()

    foreach ($entry in $spec) {
        $path = if ([string]::IsNullOrEmpty($entry.Relative)) { $Root } else { Join-Path $Root $entry.Relative }
        if ($null -ne $entry.Junction) {
            New-AdgTestTreeJunction -Path $path -Target (Join-Path $Root $entry.Junction.Target) `
                -Dangling:([bool] (Get-AdgTestTreeOption $entry.Junction 'Dangling' $false))
        }
        else {
            New-Item -ItemType Directory -Path $path -Force | Out-Null
        }
    }

    foreach ($entry in $spec) {
        if ($null -eq $entry.Acl) { continue }
        $path = if ([string]::IsNullOrEmpty($entry.Relative)) { $Root } else { Join-Path $Root $entry.Relative }
        $acl = $entry.Acl

        if ([bool] (Get-AdgTestTreeOption $acl 'Restore' $false)) {
            # Disable inheritance keeping the copies, then re-enable it and drop them: the
            # round trip an administrator makes by accident, and the one whose result must be
            # indistinguishable from never having touched the directory at all.
            Set-AdgTestTreeAcl -Path $path -Protect -CopyInherited -KeepExistingExplicit
            Set-AdgTestTreeAcl -Path $path -Ace @()
            continue
        }

        Set-AdgTestTreeAcl -Path $path `
            -Protect:([bool] (Get-AdgTestTreeOption $acl 'Protect' $false)) `
            -CopyInherited:([bool] (Get-AdgTestTreeOption $acl 'CopyInherited' $false)) `
            -KeepExistingExplicit:([bool] (Get-AdgTestTreeOption $acl 'KeepExistingExplicit' $false)) `
            -Ace @(Get-AdgTestTreeOption $acl 'Ace' @())
    }

    foreach ($entry in $spec) {
        $path = if ([string]::IsNullOrEmpty($entry.Relative)) { $Root } else { Join-Path $Root $entry.Relative }
        $nodes.Add((New-AdgTestTreeNode -Path $path -Case $entry.Case -Note $entry.Note `
                    -ExpectedBoundary $entry.Boundary -ExpectedBoundaryReason $entry.Reason `
                    -Protected:([bool] (Get-AdgTestTreeOption $entry.Acl 'Protect' $false)) `
                    -ReparsePoint:($null -ne $entry.Junction) `
                    -Dangling:([bool] (Get-AdgTestTreeOption $entry.Junction 'Dangling' $false)) `
                    -Enumerable $entry.Enumerable -Reported $entry.Reported `
                    -AclGroup $entry.AclGroup -ExpectedTrustee $entry.Trustee))
    }

    return , $nodes.ToArray()
}

# Shapes for the performance trees. Each is a balanced tree - fanout^depth leaf directories
# plus the interior ones - with ACLs drawn from AclVariants so the ratio of directories to
# distinct permission states is known before the scan rather than discovered by it.
#
# FilesPerDirectory is non-zero only on 'small'. A file-level scan is the setting most likely to be
# switched on by somebody who has not measured what it costs, so the benchmark has to be able
# to measure it - and it is measurable on the smallest tree, because what it shows is a ratio
# rather than a total. Setting it on the larger trees would multiply the generator's build
# time, which is already the slowest part of a benchmark run and is not the cost under test.
$script:Scales = @{
    small  = @{ Fanout = 5; Depth = 4; AclVariants = 8; FilesPerDirectory = 3 }
    medium = @{ Fanout = 7; Depth = 5; AclVariants = 20; FilesPerDirectory = 0 }
    large  = @{ Fanout = 9; Depth = 5; AclVariants = 40; FilesPerDirectory = 0 }
}

function Get-AdgTestTreeScale {
    <#
        .SYNOPSIS
            The performance-tree shapes, and how many directories each produces.
    #>
    [OutputType([pscustomobject])]
    param([Parameter(Mandatory)][ValidateSet('small', 'medium', 'large')][string] $Name)

    $shape = $script:Scales[$Name]
    $directories = 0
    for ($level = 0; $level -le $shape.Depth; $level++) {
        $directories += [Math]::Pow($shape.Fanout, $level)
    }
    return [pscustomobject]@{
        Name              = $Name
        Fanout            = $shape.Fanout
        Depth             = $shape.Depth
        AclVariants       = $shape.AclVariants
        FilesPerDirectory      = $shape.FilesPerDirectory
        ExpectedDirectory = [int] $directories
    }
}

function New-AdgPerformanceTree {
    <#
        .SYNOPSIS
            A balanced tree of a known size, carrying a known number of distinct ACLs.
        .DESCRIPTION
            The directories-to-distinct-ACLs ratio is the entire economic argument for a
            boundary scan, so it is a parameter here rather than an outcome. A variant is
            applied by protecting the directory and writing a distinct explicit ACE, which
            makes it a boundary and makes everything beneath it inherit that state - so the
            tree has exactly AclVariants boundaries below the root, and every other
            directory is a clean inheritor.

            **Building the tree is slower than scanning it**, by roughly an order of
            magnitude: every directory is a create, and every variant is a descriptor write.
            That cost is the generator's, not the collector's, and the benchmark reports the
            two separately so neither is mistaken for the other.
    #>
    [OutputType([object[]])]
    param(
        [Parameter(Mandatory)][string] $Root,
        [Parameter(Mandatory)][ValidateSet('small', 'medium', 'large')][string] $Scale
    )

    $shape = Get-AdgTestTreeScale -Name $Scale
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $sid = $script:Sids

    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    Set-AdgTestTreeAcl -Path $Root -Protect -Ace @(
        (New-AdgTestTreeAce -Sid $me -Rights FullControl -Inheritance 'ContainerInherit,ObjectInherit'),
        (New-AdgTestTreeAce -Sid $sid.Users -Rights ReadAndExecute -Inheritance 'ContainerInherit,ObjectInherit')
    )

    $created = 1
    $variantsPlaced = 0
    # Breadth first, so the variants land across the tree rather than down one strand, and
    # so a partial build is a partial tree rather than one deep spike.
    $frontier = [System.Collections.Generic.Queue[object]]::new()
    $frontier.Enqueue([pscustomobject]@{ Path = $Root; Depth = 0 })

    while ($frontier.Count -gt 0) {
        $current = $frontier.Dequeue()
        if ($current.Depth -ge $shape.Depth) { continue }

        for ($index = 0; $index -lt $shape.Fanout; $index++) {
            $child = Join-Path $current.Path ('d{0:d2}-{1:d3}' -f ($current.Depth + 1), $index)
            New-Item -ItemType Directory -Path $child -Force | Out-Null
            $created++

            # One variant per (depth, index) slot until the budget is spent. Spreading them
            # over the first levels puts each variant above a large inheriting subtree,
            # which is the shape a real estate has and the shape the ratio describes.
            if ($variantsPlaced -lt $shape.AclVariants -and $current.Depth -lt 3) {
                $variantsPlaced++
                Set-AdgTestTreeAcl -Path $child -Protect -Ace @(
                    (New-AdgTestTreeAce -Sid $me -Rights FullControl -Inheritance 'ContainerInherit,ObjectInherit'),
                    (New-AdgTestTreeAce -Sid $sid.Users -Rights ReadAndExecute -Inheritance 'ContainerInherit,ObjectInherit'),
                    (New-AdgTestTreeAce -Sid ('{0}-{1}' -f $sid.Orphaned.Substring(0, $sid.Orphaned.LastIndexOf('-')), (2000 + $variantsPlaced)) `
                            -Rights Modify -Inheritance 'ContainerInherit,ObjectInherit')
                )
            }

            for ($fileIndex = 0; $fileIndex -lt $shape.FilesPerDirectory; $fileIndex++) {
                Set-Content -LiteralPath (Join-Path $child ('f{0:d3}.txt' -f $fileIndex)) -Value 'adg' -Encoding utf8
            }

            $frontier.Enqueue([pscustomobject]@{ Path = $child; Depth = $current.Depth + 1 })
        }
    }

    return , @([pscustomobject]@{
            path             = $Root
            case             = "performance-$Scale"
            note             = "A balanced tree: fanout $($shape.Fanout), depth $($shape.Depth), $variantsPlaced distinct ACL variants below the root."
            directoriesBuilt = $created
            aclVariants      = $variantsPlaced
        })
}

function New-AdgTestTree {
    <#
        .SYNOPSIS
            Build a test tree and return its manifest.
        .PARAMETER Root
            Where to build. Created if missing; refused if it already holds anything unless
            -Force, because overwriting a directory somebody named by accident is the one
            mistake a tool that writes ACLs must not make.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][string] $Root,
        [ValidateSet('semantics', 'small', 'medium', 'large')][string] $Profile = 'semantics',
        [switch] $Force
    )

    $root = Resolve-AdgTestTreePath $Root
    if (Test-Path -LiteralPath $root) {
        $existing = @(Get-ChildItem -LiteralPath $root -Force -ErrorAction SilentlyContinue)
        if ($existing.Count -gt 0 -and -not $Force) {
            throw "'$root' is not empty. Pass -Force to rebuild it, or Remove-AdgTestTree it first. This generator writes ACLs, so it never reuses a directory it did not create."
        }
        if ($existing.Count -gt 0) { Remove-AdgTestTree -Root $root }
    }

    $started = [datetime]::UtcNow
    $nodes = if ($Profile -eq 'semantics') {
        New-AdgSemanticsTree -Root $root
    }
    else {
        New-AdgPerformanceTree -Root $root -Scale $Profile
    }

    $elapsed = [Math]::Round(([datetime]::UtcNow - $started).TotalSeconds, 3)
    $directories = @(Get-ChildItem -LiteralPath $root -Recurse -Directory -Force -ErrorAction SilentlyContinue).Count + 1

    return [pscustomobject]@{
        generator                    = 'scripts/windows-test-tree'
        generatorVersion             = '1.0.0'
        profile                      = $Profile
        root                         = $root
        uncRoot                      = Get-AdgTestTreeUncPath -Path $root
        builtAt                      = $started.ToString('o')
        buildSeconds                 = $elapsed
        machine                      = [System.Environment]::MachineName
        identity                     = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        identitySid                  = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        elevated                     = [bool] ([System.Security.Principal.WindowsPrincipal]::new(
                [System.Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
                [System.Security.Principal.WindowsBuiltInRole]::Administrator))
        directoriesVisibleFromHere   = $directories
        # Recorded rather than silently absent: see the module header. An unelevated owner
        # cannot build a directory whose descriptor it may not read.
        deniedDescriptorNotBuildable = ($Profile -eq 'semantics')
        nodes                        = @($nodes)
    }
}

function Export-AdgTestTreeManifest {
    <#
        .SYNOPSIS
            Write a manifest next to the tree it describes.
    #>
    param(
        [Parameter(Mandatory)] $Manifest,
        [Parameter(Mandatory)][string] $Path
    )

    $parent = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($parent) -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $Manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding utf8
    return $Path
}

function Remove-AdgTestTree {
    <#
        .SYNOPSIS
            Delete a generated tree, restoring every ACL that would otherwise block it.
        .DESCRIPTION
            The Deny ACE that makes 07-inaccessible unlistable also stops Remove-Item from
            recursing into it, and a junction deleted carelessly deletes the target's
            contents rather than the link. So: junctions first, by their own handle; then
            every remaining directory's DACL reset to the owner's Full Control, deepest
            first; then the delete.

            The reset is possible because the process owns everything here - an owner holds
            READ_CONTROL and WRITE_DAC implicitly - and it touches only the Access section.
    #>
    param([Parameter(Mandatory)][string] $Root)

    $root = Resolve-AdgTestTreePath $Root
    if (-not (Test-Path -LiteralPath $root)) { return }

    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User

    # Junctions by their own handle. Remove-Item on a junction removes the link; recursing
    # through one first would walk into the target.
    foreach ($link in @(Get-ChildItem -LiteralPath $root -Recurse -Directory -Force -ErrorAction SilentlyContinue |
                Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 })) {
        try { [System.IO.Directory]::Delete($link.FullName) }
        catch { Write-Verbose "Could not remove junction $($link.FullName): $($_.Exception.Message)" }
    }

    $directories = @(Get-ChildItem -LiteralPath $root -Recurse -Directory -Force -ErrorAction SilentlyContinue) +
    @([System.IO.DirectoryInfo]::new($root))
    foreach ($directory in ($directories | Sort-Object { $_.FullName.Length } -Descending)) {
        try {
            $security = Get-AdgTestTreeAccess -Path $directory.FullName
            $security.SetAccessRuleProtection($true, $false)
            foreach ($rule in (Get-AdgTestTreeAccessRule $security)) { [void] $security.RemoveAccessRule($rule) }
            $security.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                    $me, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
            Set-AdgTestTreeAccess -Path $directory.FullName -Security $security
        }
        catch { Write-Verbose "Could not reset the ACL of $($directory.FullName): $($_.Exception.Message)" }
    }

    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction Stop
}

Export-ModuleMember -Function @(
    'Get-AdgTestTreeOption'
    'Resolve-AdgTestTreePath'
    'Get-AdgTestTreeSid'
    'Get-AdgTestTreeUncPath'
    'Test-AdgTestTreeUncAccess'
    'Get-AdgTestTreeAccess'
    'Set-AdgTestTreeAccess'
    'Get-AdgTestTreeAccessRule'
    'New-AdgTestTreeAce'
    'Set-AdgTestTreeAcl'
    'New-AdgTestTreeJunction'
    'New-AdgTestTreeNode'
    'Get-AdgSemanticsTreeSpec'
    'New-AdgSemanticsTree'
    'New-AdgPerformanceTree'
    'Get-AdgTestTreeScale'
    'New-AdgTestTree'
    'Export-AdgTestTreeManifest'
    'Remove-AdgTestTree'
)
