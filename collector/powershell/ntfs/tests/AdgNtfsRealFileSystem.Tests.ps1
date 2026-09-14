#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    The acquisition layer, against a real NTFS volume.

    Every other NTFS suite mocks AdgNtfsSource.ps1, which is what lets an entire imaginary
    estate be exercised without a file server - and which means the code that actually talks
    to Windows was, until this file, the one part nobody tested. That is the wrong part to
    leave untested: it is where `Attributes -band ReparsePoint` either works or silently
    reports every junction as an ordinary directory, and where a descriptor round trip either
    preserves the raw flags byte or quietly loses the bits three .NET enumerations do not
    name.

    **The test that matters most is the projection one.** Windows is the authority on what a
    child inherits, and the whole tree scan rests on ADG predicting that correctly. So this
    suite builds real directories carrying a known ACE, lets Windows create real children
    under them, reads the real descriptors back, and checks that
    Get-AdgProjectedChildAclHash predicted exactly what Windows produced - for a container,
    for a file, and for a grandchild. A table copied out of documentation would pass the
    unit tests and still be wrong; this cannot be.

    ------------------------------------------------------------------------------------
    What this suite deliberately does not cover

    The walk itself, which needs UNC paths. Writing to a UNC path on the local machine means
    `\\localhost\C$`, and reaching an administrative share needs local Administrators -
    which this collector must never require and this suite must never assume. So the source
    layer is exercised here against local paths, where it is path-agnostic, and the walk is
    exercised in AdgNtfsWalk.Tests.ps1 against a mocked source. Neither half is untested;
    what is untested is the seam between them, which is one function call wide.

    Everything runs unelevated, as the collector itself must.
#>

BeforeDiscovery {
    $script:OnWindows = $IsWindows -or ($PSVersionTable.PSVersion.Major -le 5)
}

BeforeAll {
    $moduleRoot = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $moduleRoot 'AdgNtfsCollector.psd1') -Force

    $script:Me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $script:Everyone = [System.Security.Principal.SecurityIdentifier]::new('S-1-1-0')

    # Get-Acl / Set-Acl are deliberately not used to WRITE here. Set-Acl persists whichever
    # sections the descriptor it was handed claims to carry, and one read without an explicit
    # section mask claims the SACL too - which needs SeSecurityPrivilege, a privilege this
    # suite does not have and the collector must never require. Reading and writing the
    # Access section alone keeps every change inside what an unelevated owner may do.
    function Get-AccessOnly {
        param([Parameter(Mandatory)][string] $Path, [switch] $AsFile)
        $info = if ($AsFile) { [System.IO.FileInfo]::new($Path) } else { [System.IO.DirectoryInfo]::new($Path) }
        return [System.IO.FileSystemAclExtensions]::GetAccessControl($info, 'Access')
    }

    function Set-AccessOnly {
        param([Parameter(Mandatory)][string] $Path, [Parameter(Mandatory)] $Security, [switch] $AsFile)
        $info = if ($AsFile) { [System.IO.FileInfo]::new($Path) } else { [System.IO.DirectoryInfo]::new($Path) }
        [System.IO.FileSystemAclExtensions]::SetAccessControl($info, $Security)
    }

    # A raw FileSystemSecurity has no .Access note property - that is added by Get-Acl's
    # PSObject wrapper - and reaching for one under strict mode is an error rather than an
    # empty list.
    function Get-AccessRule {
        param([Parameter(Mandatory)] $Security)
        return @($Security.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    }
    $script:Root = Join-Path ([System.IO.Path]::GetTempPath()) "adg-realfs-$([guid]::NewGuid().ToString('n').Substring(0,8))"
    New-Item -ItemType Directory -Path $script:Root -Force | Out-Null

    # A directory whose DACL is exactly what this suite put there: protected, so nothing is
    # inherited from the temp directory above it, and carrying Full Control for us so the
    # tree can be built and cleaned up.
    function New-ProtectedDirectory {
        param(
            [Parameter(Mandatory)][string] $Path,
            [System.Security.AccessControl.InheritanceFlags] $Inheritance = 'None',
            [System.Security.AccessControl.PropagationFlags] $Propagation = 'None',
            [System.Security.AccessControl.FileSystemRights] $Rights = 'ReadAndExecute'
        )
        New-Item -ItemType Directory -Path $Path -Force | Out-Null

        $acl = Get-AccessOnly -Path $Path
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($rule in (Get-AccessRule $acl)) { [void] $acl.RemoveAccessRule($rule) }
        $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                $script:Me, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow'))
        if ($Inheritance -ne 'None' -or $Propagation -ne 'None') {
            $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                    $script:Everyone, $Rights, $Inheritance, $Propagation, 'Allow'))
        }
        Set-AccessOnly -Path $Path -Security $acl
        return $Path
    }

    # A directory carrying exactly one probe ACE, written through the raw descriptor.
    #
    # FileSystemAccessRule cannot express a generic mask, and a generic mask is not an exotic
    # case: 0xe0010000 - the generic form of Modify - sits on almost every directory created
    # through Explorer, and it is the case that made the first version of the projection
    # wrong on every directory of a real machine. A suite that could only build specific
    # masks would have gone on passing.
    function New-RawAceDirectory {
        param(
            [Parameter(Mandatory)][string] $Path,
            [Parameter(Mandatory)][int] $Flags,
            [Parameter(Mandatory)][long] $Mask,
            [string] $Trustee = 'S-1-1-0'
        )
        New-Item -ItemType Directory -Path $Path -Force | Out-Null

        # Protected and stripped to our own Full Control, so nothing above the temp
        # directory reaches the probe.
        $info = [System.IO.DirectoryInfo]::new($Path)
        $acl = Get-AccessOnly -Path $Path
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($rule in (Get-AccessRule $acl)) { [void] $acl.RemoveAccessRule($rule) }
        $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                $script:Me, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow'))
        Set-AccessOnly -Path $Path -Security $acl

        $raw = [System.Security.AccessControl.RawSecurityDescriptor]::new(
            (Get-AccessOnly -Path $Path).GetSecurityDescriptorBinaryForm(), 0)
        $raw.DiscretionaryAcl.InsertAce($raw.DiscretionaryAcl.Count,
            [System.Security.AccessControl.CommonAce]::new(
                [System.Security.AccessControl.AceFlags] $Flags,
                [System.Security.AccessControl.AceQualifier]::AccessAllowed,
                [int] $Mask,
                [System.Security.Principal.SecurityIdentifier]::new($Trustee),
                $false, $null))

        $bytes = [byte[]]::new($raw.BinaryLength)
        $raw.GetBinaryForm($bytes, 0)
        # The Access-only overload. Setting a descriptor built from the full binary form
        # tries to write the SACL, which needs SeSecurityPrivilege.
        $target = [System.Security.AccessControl.DirectorySecurity]::new()
        $target.SetSecurityDescriptorBinaryForm($bytes, [System.Security.AccessControl.AccessControlSections]::Access)
        [System.IO.FileSystemAclExtensions]::SetAccessControl($info, $target)
        return $Path
    }

    # Everything the projection has to predict, for one trustee: the flags AND the mask.
    # Comparing flags alone would have missed the generic split entirely, because the
    # propagating copy of a split pair has plausible-looking flags and the wrong mask.
    function Get-TrusteeEntries {
        param([Parameter(Mandatory)] $Facts, [string] $Trustee = 'S-1-1-0')
        return @(@($Facts) |
                Where-Object { $_.TrusteeSid -eq $Trustee } |
                ForEach-Object { '0x{0:x2}/0x{1:x8}' -f $_.AceFlags, $_.AccessMask })
    }

    # The descriptor facts the collector would hash, read from a real object.
    function Get-Facts {
        param([Parameter(Mandatory)][string] $Path, [switch] $AsFile)

        $security = if ($AsFile) { Get-AdgFileSecurity -Path $Path } else { Get-AdgDirectorySecurity -Path $Path }
        $facts = [System.Collections.Generic.List[object]]::new()
        $index = 0
        foreach ($ace in $security.Ace) {
            $type = ConvertTo-AdgAceType $ace.AceType
            if ($null -eq $type) { $index++; continue }
            $facts.Add([pscustomobject]@{
                    TrusteeSid = [string] $ace.TrusteeSid
                    AceType    = $type
                    AccessMask = [long] $ace.AccessMask
                    AceFlags   = [int] $ace.AceFlags
                    OrderIndex = $index
                })
            $index++
        }
        return [pscustomobject]@{
            Security = $security
            Facts    = $facts.ToArray()
            Hash     = Get-AdgAclHash -DaclPresent $security.DaclPresent `
                -DaclProtected $security.DaclProtected -Ace $facts.ToArray()
        }
    }

    function Get-EveryoneFlags {
        param([Parameter(Mandatory)] $Facts)
        return @(@($Facts) | Where-Object { $_.TrusteeSid -eq 'S-1-1-0' } | ForEach-Object { $_.AceFlags })
    }
}

AfterAll {
    if (-not (Test-Path -LiteralPath $script:Root)) { return }

    # One top-down pass that restores access to each directory *before* trying to list it.
    # AllDirectories in one call would not do: this suite deliberately denies itself
    # FILE_LIST_DIRECTORY on one directory, and a recursive enumeration throws there rather
    # than skipping it - which would leave the whole temporary tree behind.
    #
    # Junctions are deleted rather than descended into, and through .NET rather than
    # Remove-Item -Recurse: deleting a tree that contains a junction has historically
    # followed it, and this pass must be incapable of touching anything outside the tree
    # this suite built.
    $pending = [System.Collections.Generic.Queue[string]]::new()
    $pending.Enqueue($script:Root)

    while ($pending.Count -gt 0) {
        $current = $pending.Dequeue()

        try {
            $acl = [System.IO.FileSystemAclExtensions]::GetAccessControl(
                [System.IO.DirectoryInfo]::new($current), 'Access')
            foreach ($rule in @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))) {
                if ($rule.AccessControlType -eq 'Deny') { [void] $acl.RemoveAccessRule($rule) }
            }
            $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                    $script:Me, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow'))
            [System.IO.FileSystemAclExtensions]::SetAccessControl([System.IO.DirectoryInfo]::new($current), $acl)
        }
        catch { Write-Verbose "restore $current : $_" }

        try {
            foreach ($child in [System.IO.Directory]::EnumerateDirectories($current)) {
                $directory = [System.IO.DirectoryInfo]::new($child)
                if (($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    try { [System.IO.Directory]::Delete($child) } catch { Write-Verbose "junction $child : $_" }
                    continue
                }
                $pending.Enqueue($child)
            }
        }
        catch { Write-Verbose "enumerate $current : $_" }
    }

    Remove-Item -LiteralPath $script:Root -Recurse -Force -ErrorAction SilentlyContinue
}

Describe 'Reading a real security descriptor' -Skip:(-not $script:OnWindows) {
    BeforeAll {
        $script:Plain = New-ProtectedDirectory -Path (Join-Path $script:Root 'plain') `
            -Inheritance 'ContainerInherit, ObjectInherit'
        Set-Content -LiteralPath (Join-Path $script:Plain 'note.txt') -Value 'x' -Encoding utf8
    }

    It 'reports a DACL as present' {
        (Get-AdgDirectorySecurity -Path $Plain).DaclPresent | Should -BeTrue
    }

    It 'reports the protection flag this suite actually set' {
        # SE_DACL_PROTECTED survives only in the raw control flags; the rule collection does
        # not carry it, which is why the collector goes through the binary form.
        (Get-AdgDirectorySecurity -Path $Plain).DaclProtected | Should -BeTrue
    }

    It 'reports an owner' {
        (Get-AdgDirectorySecurity -Path $Plain).OwnerSid | Should -Match '^S-1-'
    }

    It 'reports trustees as SIDs' {
        $sids = @((Get-AdgDirectorySecurity -Path $Plain).Ace | ForEach-Object { $_.TrusteeSid })
        $sids | Should -Contain 'S-1-1-0'
        foreach ($sid in $sids) { Test-AdgSidString $sid | Should -BeTrue }
    }

    It 'preserves the raw ACE flags byte' {
        # OI|CI is 0x03. FileSystemAccessRule splits that byte across three enumerations and
        # loses any bit they do not name, which is why nothing here goes through Get-Acl's
        # rule collection.
        Get-EveryoneFlags (Get-Facts $Plain).Facts | Should -Be @(0x03)
    }

    It 'reads a real file descriptor the same way' {
        $file = Get-AdgFileSecurity -Path (Join-Path $Plain 'note.txt')
        $file.DaclPresent | Should -BeTrue
        @($file.Ace).Count | Should -BeGreaterThan 0
    }

    It 'produces a digest of the documented shape' {
        (Get-Facts $Plain).Hash | Should -Match '^[0-9a-f]{64}$'
    }
}

Describe 'The inheritance projection, measured against Windows' -Skip:(-not $script:OnWindows) {
    <#
        The specification of the projection, and the only place it is checked against the
        authority rather than against somebody's reading of the documentation.

        Each case builds a directory carrying one ACE with the flags and mask under test,
        lets Windows create a real child directory, a real grandchild, and a real file
        beneath it, and reads back what Windows actually wrote. The prediction has to match
        exactly - flags **and** mask.

        The mask half is not padding. The first version of this projection compared flags
        only and looked entirely sound; it was wrong on every directory of a real machine,
        because an ACE carrying a generic right does not propagate as one entry. Windows
        splits it into an effective copy (mapped, inheritance flags cleared) and a
        propagating copy (unmapped, INHERIT_ONLY) - and the propagating copy's flags are
        plausible enough that a flags-only comparison passes while the mask is wrong.
    #>
    $specific = 0x001301BF
    $generic = 0xE0010000   # GENERIC_READ|WRITE|EXECUTE|DELETE: the generic form of Modify
    $creatorOwner = 'S-1-3-0'

    $cases = @(
        # --- ordinary, specific masks: one parent ACE becomes one child ACE --------------
        @{ Name = 'CI'; Flags = 0x02; Mask = $specific; Trustee = 'S-1-1-0' }
        @{ Name = 'OI'; Flags = 0x01; Mask = $specific; Trustee = 'S-1-1-0' }
        @{ Name = 'OICI'; Flags = 0x03; Mask = $specific; Trustee = 'S-1-1-0' }
        @{ Name = 'CIIO'; Flags = 0x0A; Mask = $specific; Trustee = 'S-1-1-0' }
        @{ Name = 'OIIO'; Flags = 0x09; Mask = $specific; Trustee = 'S-1-1-0' }
        @{ Name = 'OICIIO'; Flags = 0x0B; Mask = $specific; Trustee = 'S-1-1-0' }
        @{ Name = 'CINP'; Flags = 0x06; Mask = $specific; Trustee = 'S-1-1-0' }
        @{ Name = 'OINP'; Flags = 0x05; Mask = $specific; Trustee = 'S-1-1-0' }
        @{ Name = 'OICINP'; Flags = 0x07; Mask = $specific; Trustee = 'S-1-1-0' }
        @{ Name = 'NONE'; Flags = 0x00; Mask = $specific; Trustee = 'S-1-1-0' }

        # --- generic masks: one parent ACE becomes a pair ---------------------------------
        @{ Name = 'gen-CI'; Flags = 0x02; Mask = $generic; Trustee = 'S-1-1-0' }
        @{ Name = 'gen-OI'; Flags = 0x01; Mask = $generic; Trustee = 'S-1-1-0' }
        @{ Name = 'gen-OICI'; Flags = 0x03; Mask = $generic; Trustee = 'S-1-1-0' }
        @{ Name = 'gen-CIIO'; Flags = 0x0A; Mask = $generic; Trustee = 'S-1-1-0' }
        @{ Name = 'gen-OICIIO'; Flags = 0x0B; Mask = $generic; Trustee = 'S-1-1-0' }
        @{ Name = 'gen-CINP'; Flags = 0x06; Mask = $generic; Trustee = 'S-1-1-0' }
        @{ Name = 'gen-OINP'; Flags = 0x05; Mask = $generic; Trustee = 'S-1-1-0' }
        @{ Name = 'gen-OICINP'; Flags = 0x07; Mask = $generic; Trustee = 'S-1-1-0' }

        # --- OWNER RIGHTS: looks like a CREATOR SID and is not one ------------------------
        @{ Name = 'ownerrights-OICI'; Flags = 0x03; Mask = $specific; Trustee = 'S-1-3-4' }
    )

    It 'predicts what Windows hands a child directory for <Name>' -ForEach $cases {
        $parent = New-RawAceDirectory -Path (Join-Path $script:Root "proj-c-$Name") `
            -Flags $Flags -Mask $Mask -Trustee $Trustee
        $child = Join-Path $parent 'child'
        New-Item -ItemType Directory -Path $child -Force | Out-Null

        # Assigned before filtering, never piped straight out of the call. The projection
        # returns `, $array` so that an empty result stays an array; piping the call writes
        # that array as ONE object, a filter then tests a member lookup over a collection -
        # which is truthy - and every entry survives.
        $parentFacts = (Get-Facts $parent).Facts
        $projection = Get-AdgInheritedAceProjection -Ace $parentFacts -ForContainer $true
        $predicted = Get-TrusteeEntries $projection $Trustee
        $actual = Get-TrusteeEntries (Get-Facts $child).Facts $Trustee

        $predicted | Should -Be $actual -Because "Windows is the authority on what $Name hands a child directory"
    }

    It 'predicts what Windows hands a child file for <Name>' -ForEach $cases {
        $parent = New-RawAceDirectory -Path (Join-Path $script:Root "proj-o-$Name") `
            -Flags $Flags -Mask $Mask -Trustee $Trustee
        $file = Join-Path $parent 'child.txt'
        Set-Content -LiteralPath $file -Value 'x' -Encoding utf8

        $parentFacts = (Get-Facts $parent).Facts
        $projection = Get-AdgInheritedAceProjection -Ace $parentFacts -ForContainer $false
        $predicted = Get-TrusteeEntries $projection $Trustee
        $actual = Get-TrusteeEntries (Get-Facts $file -AsFile).Facts $Trustee

        $predicted | Should -Be $actual -Because "Windows is the authority on what $Name hands a child file"
    }

    It 'predicts what Windows hands a grandchild for <Name>' -ForEach $cases {
        # Two levels, which is where NO_PROPAGATE_INHERIT earns its name, where the
        # propagating half of a generic pair has to regenerate its effective half, and where
        # a projection that is merely plausible at one level comes apart.
        $parent = New-RawAceDirectory -Path (Join-Path $script:Root "proj-g-$Name") `
            -Flags $Flags -Mask $Mask -Trustee $Trustee
        $child = Join-Path $parent 'child'
        New-Item -ItemType Directory -Path $child -Force | Out-Null
        $grandchild = Join-Path $child 'grandchild'
        New-Item -ItemType Directory -Path $grandchild -Force | Out-Null

        $childFacts = (Get-Facts $child).Facts
        $projection = Get-AdgInheritedAceProjection -Ace $childFacts -ForContainer $true
        $predicted = Get-TrusteeEntries $projection $Trustee
        $actual = Get-TrusteeEntries (Get-Facts $grandchild).Facts $Trustee

        $predicted | Should -Be $actual
    }

    It 'maps the generic form of Modify to exactly what Windows writes' {
        # 0xe0010000 -> 0x001301bf. The one arithmetic fact the split depends on, pinned
        # here rather than only inside the case table above.
        ConvertTo-AdgMappedGenericRight 0xE0010000L | Should -Be 0x001301BF
    }

    It 'predicts a real directory carrying a generic entry as inheriting cleanly' {
        # The end-to-end version of the same bug: before the split was modelled, every
        # directory beneath a generic grant - which is most directories on a real machine -
        # was reported as a boundary.
        $parent = New-RawAceDirectory -Path (Join-Path $script:Root 'gen-tree') `
            -Flags 0x0B -Mask 0xE0010000
        $child = Join-Path $parent 'child'
        New-Item -ItemType Directory -Path $child -Force | Out-Null

        $parentRead = Get-Facts $parent
        $childRead = Get-Facts $child
        $reason = Resolve-AdgAclBoundary -DaclPresent $childRead.Security.DaclPresent `
            -DaclProtected $childRead.Security.DaclProtected -AclHash $childRead.Hash `
            -ParentDaclPresent $parentRead.Security.DaclPresent `
            -ParentProjection (Get-AdgProjectedChildAclHash -DaclPresent $parentRead.Security.DaclPresent `
                -Ace $parentRead.Facts -ForContainer $true)

        $reason | Should -BeNullOrEmpty
    }

    It 'reports a directory under a CREATOR OWNER grant as a boundary, and says why it cannot do better' {
        # A documented limitation rather than a defect: Windows adds an ACE naming whoever
        # created the child, which is not a fact about the parent. The verdict is a true
        # statement about the child's DACL and a misleading one about administrative intent.
        $parent = New-RawAceDirectory -Path (Join-Path $script:Root 'co-tree') `
            -Flags 0x0B -Mask 0x10000000 -Trustee 'S-1-3-0'
        $child = Join-Path $parent 'child'
        New-Item -ItemType Directory -Path $child -Force | Out-Null

        $parentRead = Get-Facts $parent
        $childRead = Get-Facts $child
        $reason = Resolve-AdgAclBoundary -DaclPresent $childRead.Security.DaclPresent `
            -DaclProtected $childRead.Security.DaclProtected -AclHash $childRead.Hash `
            -ParentDaclPresent $parentRead.Security.DaclPresent `
            -ParentProjection (Get-AdgProjectedChildAclHash -DaclPresent $parentRead.Security.DaclPresent `
                -Ace $parentRead.Facts -ForContainer $true)

        $reason | Should -Be 'acl_differs_from_parent'
        # And the CREATOR OWNER entry itself IS predicted correctly - what is missing is the
        # substituted one, which names the creator.
        $predicted = Get-TrusteeEntries (Get-AdgInheritedAceProjection -Ace $parentRead.Facts -ForContainer $true) 'S-1-3-0'
        $actual = Get-TrusteeEntries $childRead.Facts 'S-1-3-0'
        $predicted | Should -Be $actual
    }
}

Describe 'Boundaries on a real tree' -Skip:(-not $script:OnWindows) {
    BeforeAll {
        $script:Tree = New-ProtectedDirectory -Path (Join-Path $script:Root 'tree') `
            -Inheritance 'ContainerInherit, ObjectInherit'
        $script:Clean = Join-Path $script:Tree 'clean'
        New-Item -ItemType Directory -Path $script:Clean -Force | Out-Null
        $script:Deep = Join-Path $script:Clean 'deeper'
        New-Item -ItemType Directory -Path $script:Deep -Force | Out-Null
        $script:CleanFile = Join-Path $script:Clean 'note.txt'
        Set-Content -LiteralPath $script:CleanFile -Value 'x' -Encoding utf8

        # A real inheritance break, made the way an administrator makes one.
        $script:Broken = Join-Path $script:Tree 'broken'
        New-Item -ItemType Directory -Path $script:Broken -Force | Out-Null
        $acl = Get-AccessOnly -Path $script:Broken
        $acl.SetAccessRuleProtection($true, $true)
        Set-AccessOnly -Path $script:Broken -Security $acl

        # A real edit: one extra explicit entry, inheritance left intact.
        $script:Edited = Join-Path $script:Tree 'edited'
        New-Item -ItemType Directory -Path $script:Edited -Force | Out-Null
        $edit = Get-AccessOnly -Path $script:Edited
        $edit.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                [System.Security.Principal.SecurityIdentifier]::new('S-1-5-11'),
                'Modify', 'ContainerInherit, ObjectInherit', 'None', 'Allow'))
        Set-AccessOnly -Path $script:Edited -Security $edit
    }

    It 'reports a directory that really inherited cleanly as not a boundary' {
        $parent = Get-Facts $script:Tree
        $child = Get-Facts $script:Clean
        $reason = Resolve-AdgAclBoundary -DaclPresent $child.Security.DaclPresent `
            -DaclProtected $child.Security.DaclProtected -AclHash $child.Hash `
            -ParentDaclPresent $parent.Security.DaclPresent `
            -ParentProjection (Get-AdgProjectedChildAclHash -DaclPresent $parent.Security.DaclPresent `
                -Ace $parent.Facts -ForContainer $true)

        $reason | Should -BeNullOrEmpty
    }

    It 'reports a real grandchild as not a boundary either' {
        # The property a hand-written comparison gets wrong, on a tree Windows built.
        $parent = Get-Facts $script:Clean
        $child = Get-Facts $script:Deep
        $reason = Resolve-AdgAclBoundary -DaclPresent $child.Security.DaclPresent `
            -DaclProtected $child.Security.DaclProtected -AclHash $child.Hash `
            -ParentDaclPresent $parent.Security.DaclPresent `
            -ParentProjection (Get-AdgProjectedChildAclHash -DaclPresent $parent.Security.DaclPresent `
                -Ace $parent.Facts -ForContainer $true)

        $reason | Should -BeNullOrEmpty
    }

    It 'reports a real file that inherited cleanly as not a boundary' {
        $parent = Get-Facts $script:Clean
        $file = Get-Facts $script:CleanFile -AsFile
        $reason = Resolve-AdgAclBoundary -DaclPresent $file.Security.DaclPresent `
            -DaclProtected $file.Security.DaclProtected -AclHash $file.Hash `
            -ParentDaclPresent $parent.Security.DaclPresent `
            -ParentProjection (Get-AdgProjectedChildAclHash -DaclPresent $parent.Security.DaclPresent `
                -Ace $parent.Facts -ForContainer $false)

        $reason | Should -BeNullOrEmpty
    }

    It 'would report that same file as a boundary against the container projection' {
        # Which is the mistake this guards against: it would fire on every file in an estate.
        $parent = Get-Facts $script:Clean
        $file = Get-Facts $script:CleanFile -AsFile
        $reason = Resolve-AdgAclBoundary -DaclPresent $file.Security.DaclPresent `
            -DaclProtected $file.Security.DaclProtected -AclHash $file.Hash `
            -ParentDaclPresent $parent.Security.DaclPresent `
            -ParentProjection (Get-AdgProjectedChildAclHash -DaclPresent $parent.Security.DaclPresent `
                -Ace $parent.Facts -ForContainer $true)

        $reason | Should -Be 'acl_differs_from_parent'
    }

    It 'sees a real inheritance break' {
        $broken = Get-AdgDirectorySecurity -Path $script:Broken
        $broken.DaclProtected | Should -BeTrue
        Resolve-AdgAclBoundary -DaclPresent $broken.DaclPresent -DaclProtected $broken.DaclProtected `
            -AclHash ('a' * 64) -ParentDaclPresent $true -ParentProjection ('a' * 64) |
            Should -Be 'protected_dacl'
    }

    It 'sees a real explicit entry added to an inheriting directory' {
        $parent = Get-Facts $script:Tree
        $edited = Get-Facts $script:Edited
        $edited.Security.DaclProtected | Should -BeFalse
        $reason = Resolve-AdgAclBoundary -DaclPresent $edited.Security.DaclPresent `
            -DaclProtected $edited.Security.DaclProtected -AclHash $edited.Hash `
            -ParentDaclPresent $parent.Security.DaclPresent `
            -ParentProjection (Get-AdgProjectedChildAclHash -DaclPresent $parent.Security.DaclPresent `
                -Ace $parent.Facts -ForContainer $true)

        $reason | Should -Be 'acl_differs_from_parent'
    }

    It 'collapses a real tree to the number of distinct ACL states in it' {
        # Three directories below the root, two of them edited, so three distinct states -
        # which is the whole economic case for a boundary scan, on a tree Windows built.
        $digests = @($script:Clean, $script:Deep, $script:Broken, $script:Edited) |
            ForEach-Object { (Get-Facts $_).Hash }
        @($digests | Sort-Object -Unique).Count | Should -Be 3
    }
}

Describe 'Enumerating a real directory' -Skip:(-not $script:OnWindows) {
    BeforeAll {
        $script:Listing = New-ProtectedDirectory -Path (Join-Path $script:Root 'listing') `
            -Inheritance 'ContainerInherit, ObjectInherit'
        foreach ($name in @('alpha', 'beta')) {
            New-Item -ItemType Directory -Path (Join-Path $script:Listing $name) -Force | Out-Null
        }
        Set-Content -LiteralPath (Join-Path $script:Listing 'note.txt') -Value 'x' -Encoding utf8
    }

    It 'returns the subdirectories and nothing else' {
        $children = Get-AdgChildDirectory -Path $script:Listing
        @($children | ForEach-Object { $_.Name } | Sort-Object) | Should -Be @('alpha', 'beta')
    }

    It 'returns each child full path' {
        $children = Get-AdgChildDirectory -Path $script:Listing
        foreach ($child in $children) { Test-Path -LiteralPath $child.Path | Should -BeTrue }
    }

    It 'returns an empty array for an empty directory, not nothing' {
        # Assigned before wrapping: the function writes `, $array`, so @(call) would hand
        # back a one-element array holding the array and the count would come out 1.
        $empty = New-ProtectedDirectory -Path (Join-Path $script:Root 'empty')
        $children = Get-AdgChildDirectory -Path $empty
        @($children).Count | Should -Be 0
    }

    It 'returns the files only when asked' {
        $files = Get-AdgChildFile -Path $script:Listing
        @($files | ForEach-Object { $_.Name }) | Should -Be @('note.txt')
    }

    It 'throws when a real directory cannot be listed' {
        # FILE_LIST_DIRECTORY and READ_CONTROL are different rights, and this proves it: the
        # ACL is still readable after the Deny, and the listing is not.
        $sealed = New-ProtectedDirectory -Path (Join-Path $script:Root 'sealed')
        New-Item -ItemType Directory -Path (Join-Path $sealed 'hidden') -Force | Out-Null
        $acl = Get-AccessOnly -Path $sealed
        $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                $script:Me, 'ListDirectory', 'None', 'None', 'Deny'))
        Set-AccessOnly -Path $sealed -Security $acl

        { Get-AdgChildDirectory -Path $sealed } | Should -Throw
        # The descriptor is still readable, which is why the walk reports the directory and
        # not its contents rather than dropping both.
        { Get-AdgDirectorySecurity -Path $sealed } | Should -Not -Throw
    }
}

Describe 'Real reparse points' -Skip:(-not $script:OnWindows) {
    BeforeAll {
        # A junction needs no elevation; a symbolic link does, which is why this suite uses
        # junctions and a real estate's symlinks are covered by the same code path.
        $script:LinkRoot = New-ProtectedDirectory -Path (Join-Path $script:Root 'links') `
            -Inheritance 'ContainerInherit, ObjectInherit'
        $script:Target = Join-Path $script:LinkRoot 'target'
        New-Item -ItemType Directory -Path $script:Target -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $script:Target 'inside') -Force | Out-Null

        $script:MadeJunctions = $true
        try {
            New-Item -ItemType Junction -Path (Join-Path $script:LinkRoot 'link') -Target $script:Target -ErrorAction Stop | Out-Null
            # A junction pointing at its own grandparent: the shape a walk must not follow
            # round for ever, and the one a visited-path set cannot catch.
            New-Item -ItemType Junction -Path (Join-Path $script:Target 'loop') -Target $script:LinkRoot -ErrorAction Stop | Out-Null
        }
        catch {
            $script:MadeJunctions = $false
            Write-Warning "Junctions could not be created on this volume: $($_.Exception.Message)"
        }
    }

    BeforeEach {
        # Whether junctions could be created is a run-time fact, so it cannot gate
        # discovery: -Skip is evaluated before BeforeAll has run, and reading the flag there
        # is an error under strict mode rather than a false.
        if (-not $script:MadeJunctions) {
            Set-ItResult -Skipped -Because 'junctions could not be created on this volume'
        }
    }

    It 'marks a real junction as a reparse point' {
        $children = Get-AdgChildDirectory -Path $script:LinkRoot
        $link = @($children | Where-Object { $_.Name -eq 'link' })[0]
        $link.IsReparsePoint | Should -BeTrue
    }

    It 'does not mark an ordinary directory as one' {
        $children = Get-AdgChildDirectory -Path $script:LinkRoot
        $plain = @($children | Where-Object { $_.Name -eq 'target' })[0]
        $plain.IsReparsePoint | Should -BeFalse
    }

    It 'reports the target a junction points at' {
        # Which is what the walk compares along a branch to catch a cycle. Without it the
        # only thing that ends a loop is the depth limit, after inventing a chain of paths
        # that all describe one directory.
        $children = Get-AdgChildDirectory -Path $script:LinkRoot
        $link = @($children | Where-Object { $_.Name -eq 'link' })[0]
        $link.LinkTarget | Should -Not -BeNullOrEmpty
        $link.LinkTarget.TrimEnd('\') | Should -BeLike "*$([System.IO.Path]::GetFileName($script:Target))"
    }

    It 'shows that a real junction cycle produces new paths for ever' {
        # The measurement behind the walk's guard. Descending through the loop three times
        # yields three paths nothing has seen before, so a visited-path set never fires.
        $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
        $path = $script:Target
        for ($step = 0; $step -lt 3; $step++) {
            $path = Join-Path (Join-Path $path 'loop') 'target'
            Test-Path -LiteralPath $path | Should -BeTrue -Because 'the junction really does resolve'
            $seen.Add($path.ToLowerInvariant()) | Should -BeTrue -Because 'every trip round is a new path'
        }

        # And the target it repeatedly crosses is the same one every time, which is what the
        # walk's branch-local set compares.
        $first = [System.IO.DirectoryInfo]::new((Join-Path $script:Target 'loop')).LinkTarget
        $second = [System.IO.DirectoryInfo]::new((Join-Path (Join-Path $script:Target 'loop') 'target\loop')).LinkTarget
        $second | Should -Be $first
    }

    It 'reads a junction own descriptor, because it is a real directory' {
        $security = Get-AdgDirectorySecurity -Path (Join-Path $script:LinkRoot 'link')
        $security.DaclPresent | Should -BeTrue
    }
}

Describe 'A real deep tree' -Skip:(-not $script:OnWindows) {
    BeforeAll {
        $script:DeepRoot = New-ProtectedDirectory -Path (Join-Path $script:Root 'deep') `
            -Inheritance 'ContainerInherit, ObjectInherit'
        $script:Levels = 40
        $path = $script:DeepRoot
        for ($level = 1; $level -le $script:Levels; $level++) {
            $path = Join-Path $path "L$level"
            New-Item -ItemType Directory -Path $path -Force | Out-Null
        }
        $script:Deepest = $path
    }

    It 'enumerates every level' {
        $path = $script:DeepRoot
        for ($level = 1; $level -le $script:Levels; $level++) {
            $children = Get-AdgChildDirectory -Path $path
            @($children).Count | Should -Be 1
            $path = @($children)[0].Path
        }
        $path | Should -Be $script:Deepest
    }

    It 'reads the descriptor at the bottom' {
        (Get-AdgDirectorySecurity -Path $script:Deepest).DaclPresent | Should -BeTrue
    }

    It 'reports every level as carrying the same inherited ACL' {
        # Forty directories, one permission decision. The ratio the metrics report, measured
        # on a tree Windows actually built.
        $digests = [System.Collections.Generic.List[string]]::new()
        $path = $script:DeepRoot
        for ($level = 1; $level -le $script:Levels; $level++) {
            $path = Join-Path $path "L$level"
            $digests.Add((Get-Facts $path).Hash)
        }
        @($digests | Sort-Object -Unique).Count | Should -Be 1
    }
}

Describe 'A descriptor this account may not read' -Skip:(-not $script:OnWindows) {
    It 'fails rather than returning an empty ACL' {
        # A row with no ACEs would read as "nobody has access", which is the opposite of
        # unknown - so an unreadable descriptor must throw and become an error, never an
        # observation. System Volume Information is denied to everyone but SYSTEM on a
        # default Windows install; if this machine has been configured otherwise the case
        # cannot be exercised and the test says so rather than passing vacuously.
        $denied = Join-Path ([System.IO.Path]::GetPathRoot([System.IO.Path]::GetTempPath())) 'System Volume Information'
        if (-not (Test-Path -LiteralPath $denied)) {
            Set-ItResult -Skipped -Because 'this volume has no System Volume Information directory'
            return
        }

        $readable = $true
        try { Get-AdgDirectorySecurity -Path $denied | Out-Null } catch { $readable = $false }
        if ($readable) {
            Set-ItResult -Skipped -Because 'this account can read that descriptor, so the denial cannot be exercised'
            return
        }

        { Get-AdgDirectorySecurity -Path $denied } | Should -Throw
    }
}
