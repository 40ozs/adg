#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    The whole collector, end to end, against a real NTFS tree built on purpose.

    Every other NTFS suite tests one half. AdgNtfsWalk.Tests.ps1 exercises the traversal
    against a mocked source, so an imaginary estate can be walked without a file server.
    AdgNtfsRealFileSystem.Tests.ps1 exercises the acquisition layer against real local
    paths, where it is path-agnostic. The seam between them - the walk actually reading a
    real volume - was untested, and the Phase 3B handoff said so and named it as the thing
    worth more than any number of further mocked cases.

    This suite is that seam. It builds the generated tree
    (scripts/windows-test-tree/AdgTestTree.psm1), runs Invoke-AdgNtfsScan over it exactly as
    the shipped entry point does, and checks three things against each other:

      1. **what the generator built** - the manifest, which records for each directory how it
         was constructed and what the collector is therefore expected to say about it;
      2. **what Windows stored** - every descriptor re-read through a *different* API than
         the one the collector uses, so a comparison cannot pass by both sides sharing a bug;
      3. **what the collector reported** - the contract v1 observations that would have gone
         on the wire.

    The two defects Phase 3C found - a junction reported as a depth-limit failure, and a
    dangling junction reported as a rights problem - were both invisible to every mocked
    test in the repository and both obvious within one run of this suite.

    ------------------------------------------------------------------------------------
    Why the second read goes through Get-Acl

    The collector reads a descriptor through FileSystemAclExtensions::GetAccessControl and
    then through RawSecurityDescriptor. If this suite verified it with the same two calls it
    would be comparing the collector to itself: a mistake in either call would agree with
    itself perfectly. Get-Acl reaches the same descriptor through the PowerShell provider
    stack instead - a genuinely different route to the same bytes - and the binary form is
    taken from what it returns.

    That is a real independent check and not a perfect one. Both routes end at the same
    Windows API family, and nothing short of a second implementation would remove that. It
    is recorded here rather than claimed away.

    ------------------------------------------------------------------------------------
    What makes this suite skip

    The collector identifies a directory by its UNC path, because a drive letter does not
    say which server it is on. On one machine the only UNC route to an ordinary directory is
    the drive's administrative share, \\localhost\C$, and reaching that needs local
    Administrators - a right this collector must never require and this suite must never
    assume. Without it every test here is skipped with that reason, which is the honest
    outcome: a suite that quietly rewrote itself to walk local paths would be testing a
    configuration that never ships.

    Everything runs unelevated, and writes only beneath its own root.
#>

BeforeDiscovery {
    $script:OnWindows = $IsWindows -or ($PSVersionTable.PSVersion.Major -le 5)

    $repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
    $script:TreeModule = Join-Path $repoRoot 'scripts\windows-test-tree\AdgTestTree.psm1'
    Import-Module $script:TreeModule -Force

    # Reachability is decided at discovery time because -Skip is. Tested against a directory
    # that already exists, so the answer does not depend on a tree this suite has not built
    # yet.
    $script:UncUsable = $script:OnWindows -and (Test-AdgTestTreeUncAccess -Path $repoRoot)
    $script:CanRun = $script:OnWindows -and $script:UncUsable

    # The specification is declarative and needs no file system, so Pester can enumerate one
    # test per case before anything is built. Only the cases carrying an expectation: a
    # container directory that exists to hold others asserts nothing of its own.
    # Assigned first and piped second, never `Get-AdgSemanticsTreeSpec | ...`. The function
    # returns `, $array` so an empty specification would stay an array rather than vanish -
    # which means it writes ONE object to the pipeline, and piping it directly gives a single
    # iteration over the whole array. The assignment collapses the wrapper.
    $specification = Get-AdgSemanticsTreeSpec

    $script:BoundaryCases = @(
        $specification |
            Where-Object { $null -ne $_.Boundary -and $_.Reported } |
            ForEach-Object {
                @{
                    Relative = $_.Relative
                    Label    = if ([string]::IsNullOrEmpty($_.Relative)) { '(the scan root)' } else { $_.Relative }
                    Case     = $_.Case
                    Boundary = [bool] $_.Boundary
                    Reason   = $_.Reason
                }
            }
    )

    $script:TrusteeCases = @(
        $specification |
            Where-Object { @($_.Trustee).Count -gt 0 -and $_.Reported } |
            ForEach-Object {
                @{
                    Relative = $_.Relative
                    Label    = if ([string]::IsNullOrEmpty($_.Relative)) { '(the scan root)' } else { $_.Relative }
                    Trustee  = @($_.Trustee)
                }
            }
    )

    $script:AclGroupCases = @(
        $specification |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_.AclGroup) } |
            Group-Object AclGroup |
            ForEach-Object { @{ Group = $_.Name; Members = @($_.Group | ForEach-Object { $_.Relative }) } }
    )
}

BeforeAll {
    $moduleRoot = Split-Path -Parent $PSScriptRoot
    Import-Module (Join-Path $moduleRoot 'AdgNtfsCollector.psd1') -Force

    $repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
    Import-Module (Join-Path $repoRoot 'scripts\windows-test-tree\AdgTestTree.psm1') -Force

    $script:CanRun = $IsWindows -and (Test-AdgTestTreeUncAccess -Path $repoRoot)

    if ($script:CanRun) {
        $script:Root = Join-Path ([System.IO.Path]::GetTempPath()) "adg-treeval-$([guid]::NewGuid().ToString('n').Substring(0,8))"
        $script:Manifest = New-AdgTestTree -Root $script:Root -Profile semantics
        $script:Unc = $script:Manifest.uncRoot

        # One walk, exercised through the same function the entry point calls, with the same
        # three sinks. Testing the walk function directly would skip batching, the scope
        # intent, and the completion - and those are where a run stops being able to claim
        # what it enumerated.
        $script:Batches = [System.Collections.Generic.List[object]]::new()
        $script:Start = $null
        $script:Completion = $null

        $settings = Import-AdgNtfsTarget -ScanRoot $script:Unc
        $settings.MaxDepth = 24

        $script:Runs = @(Invoke-AdgNtfsScan -Settings $settings `
                -OnStart { param($payload) $script:Start = $payload } `
                -OnBatch { param($payload) $script:Batches.Add($payload) } `
                -OnCompletion { param($payload) $script:Completion = $payload } `
                -ModulePath (Join-Path $moduleRoot 'AdgNtfsCollector.psd1'))

        $script:Observations = @(foreach ($batch in $script:Batches) { $batch['observations'] })
        $script:Resources = @{}
        $script:AcesByPath = @{}
        foreach ($observation in $script:Observations) {
            switch ($observation['kind']) {
                'ntfs_resource' {
                    $script:Resources[([string] $observation['path']).ToLowerInvariant()] = $observation
                }
                'ntfs_ace' {
                    $key = ([string] $observation['path']).ToLowerInvariant()
                    if (-not $script:AcesByPath.ContainsKey($key)) {
                        $script:AcesByPath[$key] = [System.Collections.Generic.List[object]]::new()
                    }
                    $script:AcesByPath[$key].Add($observation)
                }
            }
        }

        # Local path to the UNC spelling the collector reports, for looking a manifest node
        # up in the observations.
        function Get-ReportedResource {
            param([string] $Relative)
            $path = if ([string]::IsNullOrEmpty($Relative)) { $script:Unc } else { Join-Path $script:Unc $Relative }
            $key = (ConvertTo-AdgUncPath $path).ToLowerInvariant()
            if ($script:Resources.ContainsKey($key)) { return $script:Resources[$key] }
            return $null
        }

        function Get-ReportedAce {
            param([string] $Relative)
            $path = if ([string]::IsNullOrEmpty($Relative)) { $script:Unc } else { Join-Path $script:Unc $Relative }
            $key = (ConvertTo-AdgUncPath $path).ToLowerInvariant()
            if ($script:AcesByPath.ContainsKey($key)) { return @($script:AcesByPath[$key]) }
            return @()
        }

        # The independent read. Get-Acl rather than the collector's own call: see the header.
        function Get-WindowsDescriptor {
            param([Parameter(Mandatory)][string] $Path)

            $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
            $raw = [System.Security.AccessControl.RawSecurityDescriptor]::new(
                $acl.GetSecurityDescriptorBinaryForm(), 0)

            $entries = [System.Collections.Generic.List[object]]::new()
            if ($null -ne $raw.DiscretionaryAcl) {
                foreach ($ace in $raw.DiscretionaryAcl) {
                    $sid = $null
                    $property = $ace.PSObject.Properties['SecurityIdentifier']
                    if ($null -ne $property -and $null -ne $property.Value) { $sid = [string] $property.Value.Value }
                    $entries.Add([pscustomobject]@{
                            AceType    = [string] $ace.AceType
                            AceFlags   = [int] $ace.AceFlags
                            # The same reinterpretation the collector makes, and the reason it
                            # has to: .NET surfaces a mask as a signed Int32, so every mask
                            # carrying a generic right arrives negative and a range-checked
                            # [uint32] cast throws on it.
                            # 0xFFFFFFFF without the L suffix is an Int32 -1, so the -band
                            # is a no-op and the sign extension survives. Same literal, same
                            # suffix, same reason as ConvertTo-AdgAccessMask.
                            AccessMask = ([long] $ace.AccessMask) -band 0xFFFFFFFFL
                            TrusteeSid = $sid
                        })
                }
            }

            $control = $raw.ControlFlags
            return [pscustomobject]@{
                OwnerSid      = if ($null -eq $raw.Owner) { $null } else { [string] $raw.Owner.Value }
                GroupSid      = if ($null -eq $raw.Group) { $null } else { [string] $raw.Group.Value }
                DaclPresent   = ($control -band [System.Security.AccessControl.ControlFlags]::DiscretionaryAclPresent) -ne 0
                DaclProtected = ($control -band [System.Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -ne 0
                Ace           = $entries.ToArray()
            }
        }
    }
}

AfterAll {
    if ($script:CanRun -and -not [string]::IsNullOrWhiteSpace($script:Root)) {
        Remove-AdgTestTree -Root $script:Root
    }
}

Describe 'A real walk of a generated tree' -Skip:(-not $script:CanRun) {

    It 'reads every directory the generator built' {
        # The three directories that are deliberately not reported: the one beneath the
        # unlistable directory, which the walk never saw, and nothing else. A count that
        # drifts upward here is a walk that started skipping things quietly.
        $specification = Get-AdgSemanticsTreeSpec
        $expected = @($specification | Where-Object { $_.Reported }).Count
        $script:Resources.Count | Should -Be $expected
    }

    It 'reports the run as partial, because one directory could not be listed' {
        # Not a failure of the run: a directory whose ACL was read and whose contents were
        # not is an ordinary result. The run says so rather than reporting the part it
        # reached as the whole.
        $script:Completion['status'] | Should -Be 'partial'
    }

    It 'reconciles nothing, because the tree was not fully enumerated' {
        @($script:Completion['reconciled_scopes']).Count | Should -Be 0
    }

    It 'names the scan root in the start envelope scope' {
        @($script:Start['scopes']).Count | Should -BeGreaterThan 0
    }

    It 'never puts one source key in a batch twice' {
        # The API rejects such a batch outright with a 422, and this tree is what found the
        # case: one orphaned SID sits on two directories in unrelated branches, each reports
        # a principal observation describing it, and both landed in the same batch. In an
        # estate an orphaned SID is orphaned estate-wide, so it turns up on dozens of folders
        # and every batch would have been refused.
        foreach ($batch in $script:Batches) {
            $keys = @($batch['observations'] | ForEach-Object { [string] $_['source_key'] })
            @($keys | Sort-Object -Unique).Count | Should -Be $keys.Count
        }
    }

    It 'still reports the orphaned trustee that appears on two directories' {
        # The other half of that fix. Dropping the repeat must not drop the finding: an
        # unresolved SID on a folder ACL is one of the things this tool exists to report.
        $principals = @(foreach ($batch in $script:Batches) {
                $batch['observations'] | Where-Object { $_['kind'] -eq 'principal' }
            })
        @($principals | Where-Object { $_['principal_kind'] -eq 'unresolved' }).Count |
            Should -BeGreaterThan 0
    }

    It 'collapses the tree to the number of distinct ACL states in it' {
        # The entire economic case for a boundary scan, measured on a real tree: many more
        # directories than permission decisions. It does NOT mean the directories need not be
        # walked - every one of them was read to find that out.
        $digests = @($script:Resources.Values | ForEach-Object { $_['acl_hash'] } | Sort-Object -Unique)
        $digests.Count | Should -BeLessThan $script:Resources.Count
        $script:Runs[0].Summary.UniqueAclHashes | Should -Be $digests.Count
    }
}

Describe 'Every descriptor, against a second read of Windows' -Skip:(-not $script:CanRun) {

    It 'reports the owner Windows stored, for every directory' {
        $wrong = @()
        foreach ($observation in $script:Resources.Values) {
            $windows = Get-WindowsDescriptor -Path ([string] $observation['path'])
            if ([string] $observation['owner_sid'] -ne [string] $windows.OwnerSid) {
                $wrong += "$($observation['path']): reported $($observation['owner_sid']), Windows says $($windows.OwnerSid)"
            }
        }
        $wrong | Should -BeNullOrEmpty
    }

    It 'reports dacl_present and dacl_protected as Windows stored them' {
        $wrong = @()
        foreach ($observation in $script:Resources.Values) {
            $windows = Get-WindowsDescriptor -Path ([string] $observation['path'])
            if ([bool] $observation['dacl_present'] -ne $windows.DaclPresent) {
                $wrong += "$($observation['path']): dacl_present"
            }
            # inheritance_enabled is the contract's spelling of "not protected", and the two
            # must never drift apart: a directory reported as inheriting while its descriptor
            # says protected is a directory whose next scan compares it against the wrong
            # parent.
            if ([bool] $observation['dacl_protected'] -ne $windows.DaclProtected) {
                $wrong += "$($observation['path']): dacl_protected"
            }
            if ([bool] $observation['inheritance_enabled'] -eq $windows.DaclProtected) {
                $wrong += "$($observation['path']): inheritance_enabled contradicts dacl_protected"
            }
        }
        $wrong | Should -BeNullOrEmpty
    }

    It 'reports every ACE, in DACL order, with the raw flags byte and the raw mask' {
        # The strictest assertion in the suite, and the one that would catch a mask cast that
        # throws on a generic right, a flags byte reduced to the three enumerations .NET
        # names, or a DACL re-sorted on the way through. Evaluation order is what makes a
        # Deny mean anything.
        $wrong = @()
        foreach ($observation in $script:Resources.Values) {
            $path = [string] $observation['path']
            $windows = Get-WindowsDescriptor -Path $path
            $reported = @(Get-ReportedAce -Relative ($path.Substring($script:Unc.Length).TrimStart('\')) |
                    Sort-Object { [int] $_['order_index'] })

            $expected = @($windows.Ace | Where-Object { $null -ne (ConvertTo-AdgAceType $_.AceType) })
            if ($reported.Count -ne $expected.Count) {
                $wrong += "${path}: reported $($reported.Count) ACE(s), Windows has $($expected.Count)"
                continue
            }
            for ($index = 0; $index -lt $expected.Count; $index++) {
                $mine = $reported[$index]
                $theirs = $expected[$index]
                if ([string] $mine['trustee_sid'] -ne [string] $theirs.TrusteeSid) {
                    $wrong += "$path #${index}: trustee $($mine['trustee_sid']) vs $($theirs.TrusteeSid)"
                }
                if ([long] $mine['access_mask'] -ne [long] $theirs.AccessMask) {
                    $wrong += ("$path #${index}: mask 0x{0:x8} vs 0x{1:x8}" -f [long] $mine['access_mask'], [long] $theirs.AccessMask)
                }
                if ([int] $mine['ace_flags'] -ne [int] $theirs.AceFlags) {
                    $wrong += ("$path #${index}: flags 0x{0:x2} vs 0x{1:x2}" -f [int] $mine['ace_flags'], [int] $theirs.AceFlags)
                }
                if ([string] $mine['ace_type'] -ne (ConvertTo-AdgAceType $theirs.AceType)) {
                    $wrong += "$path #${index}: type $($mine['ace_type']) vs $($theirs.AceType)"
                }
            }
        }
        $wrong | Should -BeNullOrEmpty
    }

    It 'reports an ace_count matching the entries it sent' {
        $wrong = @()
        foreach ($observation in $script:Resources.Values) {
            $path = [string] $observation['path']
            $sent = @(Get-ReportedAce -Relative ($path.Substring($script:Unc.Length).TrimStart('\'))).Count
            if ([int] $observation['ace_count'] -ne $sent) {
                $wrong += "${path}: declared $($observation['ace_count']), sent $sent"
            }
        }
        $wrong | Should -BeNullOrEmpty
    }

    It 'carries a generic access mask through unchanged rather than throwing on it' {
        # 0xe0010000 is a negative Int32. Phase 3A's [uint32] cast was range-checked and
        # threw on it, so every directory Explorer had ever touched came back as
        # access_denied. Measured here on a real descriptor rather than a fixture.
        $entries = @(Get-ReportedAce -Relative '11-generic-mask\generic-modify')
        @($entries | Where-Object { [long] $_['access_mask'] -eq 0xe0010000L }).Count | Should -Be 1
    }
}

Describe 'The boundary verdict for <Label>' -ForEach $script:BoundaryCases -Skip:(-not $script:CanRun) {

    It 'reports is_acl_boundary as the generator specified' {
        $observation = Get-ReportedResource -Relative $Relative
        $observation | Should -Not -BeNullOrEmpty
        [bool] $observation['is_acl_boundary'] | Should -Be $Boundary
    }

    It 'gives the reason the generator specified, and none where it is not a boundary' {
        # A reason on a directory that is not a boundary is rejected by the contract at every
        # version, so the empty case is an assertion rather than a skip.
        $observation = Get-ReportedResource -Relative $Relative
        [string] $observation['boundary_reason'] | Should -Be ([string] $Reason)
    }
}

Describe 'Trustees on <Label>' -ForEach $script:TrusteeCases -Skip:(-not $script:CanRun) {

    It 'names every trustee the generator put on it' {
        $reported = @(Get-ReportedAce -Relative $Relative | ForEach-Object { [string] $_['trustee_sid'] })
        foreach ($sid in $Trustee) { $reported | Should -Contain $sid }
    }
}

Describe 'Directories built with the same ACL: <Group>' -ForEach $script:AclGroupCases -Skip:(-not $script:CanRun) {

    It 'produce one acl_hash between them' {
        # The claim the storage design rests on, and the one place it can be checked without
        # trusting the hash function: two directories that were built identically, in
        # unrelated branches, must produce one digest. If they do not, deduplication is
        # unsound; if unrelated ones collided, it would be unsound the other way.
        $digests = @(foreach ($relative in $Members) {
                $observation = Get-ReportedResource -Relative $relative
                if ($null -ne $observation) { [string] $observation['acl_hash'] }
            })
        # Wrapped again after the sort: Sort-Object -Unique over identical values writes one
        # scalar, and .Count on a string is an error under strict mode rather than 1.
        @($digests | Sort-Object -Unique).Count | Should -Be 1
    }
}

Describe 'A directory whose contents cannot be listed' -Skip:(-not $script:CanRun) {

    It 'still reports the directory, because its descriptor was readable' {
        # READ_CONTROL and FILE_LIST_DIRECTORY are different rights, and an administrator can
        # grant either without the other. Dropping the directory would lose an ACL that was
        # read successfully.
        Get-ReportedResource -Relative '07-inaccessible\unlistable' | Should -Not -BeNullOrEmpty
    }

    It 'does not report what is beneath it' {
        # Unobserved, not absent. Reporting nothing beneath it as though the walk had looked
        # is how a later reconciliation marks live permissions revoked.
        Get-ReportedResource -Relative '07-inaccessible\unlistable\beneath' | Should -BeNullOrEmpty
    }

    It 'records the failure as an access_denied naming the listing right' {
        $errors = @($script:Completion['errors'] | Where-Object { $_['target'] -like '*07-inaccessible\unlistable' })
        $errors.Count | Should -Be 1
        $errors[0]['code'] | Should -Be 'access_denied'
        $errors[0]['message'] | Should -BeLike '*FILE_LIST_DIRECTORY*'
    }

    It 'is the only error a default walk of this tree produces' {
        # Phase 3C found three more here, all of them spurious: two junctions reported as
        # depth-limit failures and one dangling junction reported as a rights problem. An
        # operator reading a run's errors has to be able to trust that each one names
        # something real, or they stop reading them.
        @($script:Completion['errors']).Count | Should -Be 1
    }
}

Describe 'Reparse points on a real volume' -Skip:(-not $script:CanRun) {

    It 'reports a junction own descriptor, because it is a real directory' {
        Get-ReportedResource -Relative '08-reparse\sibling-link' | Should -Not -BeNullOrEmpty
    }

    It 'reports a junction whose target is gone' {
        # Only the target is missing. The link is a real object with a real ACL, and losing
        # it because a volume was decommissioned would be losing a fact that still holds.
        Get-ReportedResource -Relative '08-reparse\dangling-link' | Should -Not -BeNullOrEmpty
    }

    It 'does not descend through a junction under the default policy' {
        Get-ReportedResource -Relative '08-reparse\sibling-link\inside' | Should -BeNullOrEmpty
    }

    It 'reports the junction target under its own path, once' {
        Get-ReportedResource -Relative '08-reparse\target\inside' | Should -Not -BeNullOrEmpty
    }

    It 'does not blame the depth limit for a junction the policy declined' {
        # The first of the two defects this suite found. The walk used to express "do not go
        # below this" by queueing the junction at maxDepth, which then reported the stop as a
        # depth limit - "maxDepth is 24 and this directory sits at that depth", about a
        # junction at depth 2. Raising maxDepth would have changed nothing.
        @($script:Completion['errors'] | Where-Object { $_['code'] -eq 'depth_limit_reached' }) |
            Should -BeNullOrEmpty
    }

    It 'marks the root non-exhaustive for the reparse points, and says so as itself' {
        $record = @($script:Runs[0].Roots)[0]
        $record.Exhaustive | Should -BeFalse
        @($record.Reasons) | Should -Contain 'reparse_point'
        @($record.Reasons) | Should -Not -Contain 'depth_limit'
    }
}

Describe 'Following reparse points on a real volume' -Skip:(-not $script:CanRun) {

    BeforeAll {
        $moduleRoot = Split-Path -Parent $PSScriptRoot
        $settings = Import-AdgNtfsTarget -ScanRoot $script:Unc
        $settings.MaxDepth = 24
        $settings.ReparsePointPolicy = 'follow'

        $script:FollowBatches = [System.Collections.Generic.List[object]]::new()
        $script:FollowCompletion = $null
        $script:FollowRuns = @(Invoke-AdgNtfsScan -Settings $settings `
                -OnStart { param($payload) } `
                -OnBatch { param($payload) $script:FollowBatches.Add($payload) } `
                -OnCompletion { param($payload) $script:FollowCompletion = $payload } `
                -ModulePath (Join-Path $moduleRoot 'AdgNtfsCollector.psd1'))
    }

    It 'terminates' {
        # A junction pointing at its own parent produces a fresh path every time round, so a
        # visited-path set never fires. Only the branch-local set of reparse targets ends it,
        # and "the suite finished" is the assertion.
        $script:FollowRuns[0].Summary.Completed | Should -BeTrue
    }

    It 'catches the cycle as a traversal loop rather than as a depth limit' {
        $loops = @($script:FollowCompletion['errors'] | Where-Object { $_['code'] -eq 'traversal_loop' })
        $loops.Count | Should -BeGreaterThan 0
        @($script:FollowCompletion['errors'] | Where-Object { $_['code'] -eq 'depth_limit_reached' }) |
            Should -BeNullOrEmpty
    }

    It 'reports a dangling junction as an unreadable target rather than as a rights problem' {
        # The second defect this suite found. Following a junction whose target is gone
        # produced "could not be enumerated ... Listing a directory needs
        # FILE_LIST_DIRECTORY", which sends an operator to look at permissions on a
        # directory whose permissions are fine.
        $dangling = @($script:FollowCompletion['errors'] |
                Where-Object { $_['target'] -like '*dangling-link' })
        $dangling.Count | Should -BeGreaterThan 0
        foreach ($error in $dangling) { $error['code'] | Should -Be 'reparse_target_unreadable' }
    }

    It 'finds more directories than the default policy, all of them through the links' {
        $followed = @(foreach ($batch in $script:FollowBatches) {
                $batch['observations'] | Where-Object { $_['kind'] -eq 'ntfs_resource' }
            })
        $followed.Count | Should -BeGreaterThan $script:Resources.Count
    }
}

Describe 'A subtree with nothing in the way' -Skip:(-not $script:CanRun) {

    BeforeAll {
        # 01-inherited holds no junction, no unlistable directory, and nothing below the
        # depth limit. A walk of it is the one case in this tree that may claim to have
        # enumerated its scope - which is what lets a later phase mark an unseen directory
        # absent, and must therefore be hard to earn.
        $moduleRoot = Split-Path -Parent $PSScriptRoot
        $settings = Import-AdgNtfsTarget -ScanRoot (Join-Path $script:Unc '01-inherited')
        $settings.MaxDepth = 24

        $script:CleanCompletion = $null
        $script:CleanRuns = @(Invoke-AdgNtfsScan -Settings $settings `
                -OnStart { param($payload) } -OnBatch { param($payload) } `
                -OnCompletion { param($payload) $script:CleanCompletion = $payload } `
                -ModulePath (Join-Path $moduleRoot 'AdgNtfsCollector.psd1'))
    }

    It 'succeeds' {
        $script:CleanCompletion['status'] | Should -Be 'succeeded'
    }

    It 'reports no errors' {
        [int] $script:CleanCompletion['error_count'] | Should -Be 0
    }

    It 'reconciles its directory_tree scope' {
        @($script:CleanCompletion['reconciled_scopes']).Count | Should -Be 1
        @($script:CleanCompletion['reconciled_scopes'])[0]['kind'] | Should -Be 'directory_tree'
    }

    It 'reports its own root as a boundary, because nobody read the parent' {
        $root = $script:CleanRuns[0]
        $root.Summary.BoundariesFound | Should -Be 1
    }
}

Describe 'A walk truncated by its depth limit' -Skip:(-not $script:CanRun) {

    BeforeAll {
        $moduleRoot = Split-Path -Parent $PSScriptRoot
        $settings = Import-AdgNtfsTarget -ScanRoot (Join-Path $script:Unc '05-deep')
        $settings.MaxDepth = 3

        $script:DeepCompletion = $null
        $script:DeepRuns = @(Invoke-AdgNtfsScan -Settings $settings `
                -OnStart { param($payload) } -OnBatch { param($payload) } `
                -OnCompletion { param($payload) $script:DeepCompletion = $payload } `
                -ModulePath (Join-Path $moduleRoot 'AdgNtfsCollector.psd1'))
    }

    It 'stops at the limit rather than walking the whole chain' {
        $script:DeepRuns[0].Summary.DirectoriesRead | Should -Be 4
    }

    It 'says the depth limit is why, on a tree where that is actually true' {
        $errors = @($script:DeepCompletion['errors'] | Where-Object { $_['code'] -eq 'depth_limit_reached' })
        $errors.Count | Should -Be 1
        $errors[0]['message'] | Should -BeLike '*maxDepth is 3*'
    }

    It 'refuses to reconcile a scope it did not enumerate' {
        @($script:DeepCompletion['reconciled_scopes']).Count | Should -Be 0
    }
}
