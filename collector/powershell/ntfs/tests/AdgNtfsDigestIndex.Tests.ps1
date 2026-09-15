<#
    The digest index, and the affirmations it produces.

    The property every test here circles is the same one: **the index decides what to
    transmit, never what to read**. Writing an ACL moves no timestamp a walk could test, so
    a scan that trusted an index to skip a descriptor would skip exactly the changes ADG
    exists to find. Every affirmation is built from a digest the walk computed on this pass.
#>

BeforeAll {
    $repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
    Import-Module (Join-Path $repoRoot 'powershell/ntfs/AdgNtfsCollector.psd1') -Force

    $script:DigestA = 'a' * 64
    $script:DigestB = 'b' * 64
    $script:Root = 'resource|\\fs01\finance'
    $script:Reports = 'resource|\\fs01\finance\reports'

    function New-IndexPath {
        return Join-Path ([System.IO.Path]::GetTempPath()) "adg-digest-$([guid]::NewGuid()).idx"
    }

    function New-ResourceObservation {
        param([string] $SourceKey, [string] $Digest, [string] $ObservedAt = '2026-03-02T09:00:00Z')
        $observation = [ordered]@{
            kind        = 'ntfs_resource'
            run_id      = '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31'
            observed_at = $ObservedAt
            source_key  = $SourceKey
            path        = '\\FS01\Finance'
        }
        if ($Digest) { $observation['acl_hash'] = $Digest }
        return $observation
    }

    function New-AceObservation {
        param([string] $Key)
        return [ordered]@{
            kind        = 'ntfs_ace'
            run_id      = '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31'
            observed_at = '2026-03-02T09:00:00Z'
            source_key  = $Key
        }
    }
}

Describe 'Reading and writing an index' {
    It 'round-trips entries' {
        $path = New-IndexPath
        $index = New-AdgNtfsDigestIndex -Fingerprint 'fp1'
        Set-AdgNtfsDigest -Index $index -Path $script:Root -Digest $script:DigestA
        Set-AdgNtfsDigest -Index $index -Path $script:Reports -Digest $script:DigestB
        Export-AdgNtfsDigestIndex -Index $index -Path $path | Out-Null

        $reloaded = Import-AdgNtfsDigestIndex -Path $path -Fingerprint 'fp1'
        $reloaded.Known.Count | Should -Be 2
        $reloaded.Known[$script:Root] | Should -Be $script:DigestA
    }

    It 'keeps a path that contains the separator it writes' {
        # Windows folder names may contain a tab. The digest is fixed-width and last, so the
        # parser takes it from the end rather than splitting on the first separator -- which
        # would truncate the path and turn every later scan of that folder into a full send.
        $path = New-IndexPath
        $awkward = "resource|\\fs01\finance\odd`tname"
        $index = New-AdgNtfsDigestIndex
        Set-AdgNtfsDigest -Index $index -Path $awkward -Digest $script:DigestA
        Export-AdgNtfsDigestIndex -Index $index -Path $path | Out-Null

        (Import-AdgNtfsDigestIndex -Path $path).Known[$awkward] | Should -Be $script:DigestA
    }

    It 'starts empty when the file does not exist' {
        (Import-AdgNtfsDigestIndex -Path (New-IndexPath)).Known.Count | Should -Be 0
    }

    It 'discards an index written for a different scan, and says why' {
        # Its entries describe paths this scan may never visit, so keeping them would make
        # them immortal: never re-read, never re-affirmed, never expired.
        $path = New-IndexPath
        $index = New-AdgNtfsDigestIndex -Fingerprint 'roots=finance'
        Set-AdgNtfsDigest -Index $index -Path $script:Root -Digest $script:DigestA
        Export-AdgNtfsDigestIndex -Index $index -Path $path | Out-Null

        $reloaded = Import-AdgNtfsDigestIndex -Path $path -Fingerprint 'roots=hr' `
            -WarningVariable warnings -WarningAction SilentlyContinue
        $reloaded.Known.Count | Should -Be 0
        $warnings.Count | Should -BeGreaterThan 0
    }

    It 'treats a corrupt index as no index, rather than failing the scan' {
        $path = New-IndexPath
        Set-Content -LiteralPath $path -Value 'not an index at all' -Encoding utf8NoBOM
        $index = Import-AdgNtfsDigestIndex -Path $path -WarningAction SilentlyContinue
        $index.Known.Count | Should -Be 0
    }

    It 'leaves the previous index alone when the walk did not complete' {
        # A partial walk reached some paths and not others. Writing its index would discard
        # what the previous one knew about everything beyond the point it stopped.
        $path = New-IndexPath
        $first = New-AdgNtfsDigestIndex
        Set-AdgNtfsDigest -Index $first -Path $script:Root -Digest $script:DigestA
        Export-AdgNtfsDigestIndex -Index $first -Path $path | Out-Null

        $partial = New-AdgNtfsDigestIndex
        Set-AdgNtfsDigest -Index $partial -Path $script:Reports -Digest $script:DigestB
        Export-AdgNtfsDigestIndex -Index $partial -Path $path -Partial | Out-Null

        (Import-AdgNtfsDigestIndex -Path $path).Known[$script:Root] | Should -Be $script:DigestA
    }

    It 'drops a path this scan did not visit, so a deleted directory does not live for ever' {
        $path = New-IndexPath
        $first = New-AdgNtfsDigestIndex
        Set-AdgNtfsDigest -Index $first -Path $script:Root -Digest $script:DigestA
        Set-AdgNtfsDigest -Index $first -Path $script:Reports -Digest $script:DigestB
        Export-AdgNtfsDigestIndex -Index $first -Path $path | Out-Null

        $second = Import-AdgNtfsDigestIndex -Path $path
        Set-AdgNtfsDigest -Index $second -Path $script:Root -Digest $script:DigestA
        Export-AdgNtfsDigestIndex -Index $second -Path $path | Out-Null

        $third = Import-AdgNtfsDigestIndex -Path $path
        $third.Known.ContainsKey($script:Root) | Should -BeTrue
        $third.Known.ContainsKey($script:Reports) | Should -BeFalse
    }

    It 'stops at its ceiling rather than growing without bound' {
        $index = New-AdgNtfsDigestIndex -MaxEntries 1000
        $index.MaxEntries = 1
        Set-AdgNtfsDigest -Index $index -Path $script:Root -Digest $script:DigestA
        Set-AdgNtfsDigest -Index $index -Path $script:Reports -Digest $script:DigestB `
            -WarningVariable warnings -WarningAction SilentlyContinue

        $index.Next.Count | Should -Be 1
        $index.Truncated | Should -BeTrue
        $warnings.Count | Should -Be 1
    }
}

Describe 'Deciding whether a descriptor may be affirmed' {
    BeforeEach {
        $script:Index = New-AdgNtfsDigestIndex
        $script:Index.Known[$script:Root] = $script:DigestA
    }

    It 'affirms when the digest this scan computed matches what was last reported' {
        Get-AdgNtfsAffirmableDigest -Index $script:Index -Path $script:Root -Digest $script:DigestA |
            Should -BeTrue
    }

    It 'does not affirm when the ACL changed' {
        Get-AdgNtfsAffirmableDigest -Index $script:Index -Path $script:Root -Digest $script:DigestB |
            Should -BeFalse
    }

    It 'does not affirm a path the index has never seen' {
        Get-AdgNtfsAffirmableDigest -Index $script:Index -Path $script:Reports -Digest $script:DigestA |
            Should -BeFalse
    }

    It 'does not affirm when the collector reported no digest at all' {
        # acl_hash is omitted when an entry could not be reported, so there is no summary of
        # this DACL to affirm.
        Get-AdgNtfsAffirmableDigest -Index $script:Index -Path $script:Root -Digest '' | Should -BeFalse
        Get-AdgNtfsAffirmableDigest -Index $script:Index -Path $script:Root -Digest $null | Should -BeFalse
    }
}

Describe 'What a batch carries' {
    BeforeEach {
        $script:Batches = [System.Collections.Generic.List[object]]::new()
        $script:OnBatch = { param($batch) $script:Batches.Add($batch) }
    }

    It 'sends a changed descriptor in full and affirms an unchanged one' {
        $index = New-AdgNtfsDigestIndex
        $index.Known[$script:Root] = $script:DigestA
        $writer = New-AdgNtfsBatchWriter -RunId '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31' `
            -BatchSize 500 -OnBatch $script:OnBatch -DigestIndex $index

        Add-AdgNtfsResourceGroup -Writer $writer -Group @(
            (New-ResourceObservation -SourceKey $script:Root -Digest $script:DigestA)
            (New-AceObservation -Key 'ntfs_ace|a')
            (New-AceObservation -Key 'ntfs_ace|b')
        )
        Add-AdgNtfsResourceGroup -Writer $writer -Group @(
            (New-ResourceObservation -SourceKey $script:Reports -Digest $script:DigestB)
            (New-AceObservation -Key 'ntfs_ace|c')
        )
        Complete-AdgNtfsBatchWriter -Writer $writer

        $script:Batches.Count | Should -Be 1
        $batch = $script:Batches[0]
        $batch.schema_version | Should -Be '1.4'
        @($batch.observations).Count | Should -Be 2
        @($batch.affirmations).Count | Should -Be 1
        $batch.affirmations[0].source_key | Should -Be $script:Root
        $batch.affirmations[0].digest | Should -Be $script:DigestA
        $index.Affirmed | Should -Be 1
        $index.Sent | Should -Be 1
    }

    It 'declares 1.3 when nothing was affirmed, because the minor is additive' {
        $writer = New-AdgNtfsBatchWriter -RunId '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31' `
            -BatchSize 500 -OnBatch $script:OnBatch -DigestIndex (New-AdgNtfsDigestIndex)
        Add-AdgNtfsResourceGroup -Writer $writer -Group @(
            (New-ResourceObservation -SourceKey $script:Root -Digest $script:DigestA)
        )
        Complete-AdgNtfsBatchWriter -Writer $writer

        $script:Batches[0].schema_version | Should -Be '1.3'
    }

    It 'sends everything in full when no index is configured' {
        $writer = New-AdgNtfsBatchWriter -RunId '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31' `
            -BatchSize 500 -OnBatch $script:OnBatch
        Add-AdgNtfsResourceGroup -Writer $writer -Group @(
            (New-ResourceObservation -SourceKey $script:Root -Digest $script:DigestA)
            (New-AceObservation -Key 'ntfs_ace|a')
        )
        Complete-AdgNtfsBatchWriter -Writer $writer

        @($script:Batches[0].observations).Count | Should -Be 2
        $script:Batches[0].Contains('affirmations') | Should -BeFalse
    }

    It 'still sends the principal observations that travel with an affirmed group' {
        # They describe trustees, not the descriptor, and the resource's digest says nothing
        # about them. Dropping them would stop an orphaned SID's provenance advancing.
        $index = New-AdgNtfsDigestIndex
        $index.Known[$script:Root] = $script:DigestA
        $writer = New-AdgNtfsBatchWriter -RunId '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31' `
            -BatchSize 500 -OnBatch $script:OnBatch -DigestIndex $index

        Add-AdgNtfsResourceGroup -Writer $writer -Group @(
            (New-ResourceObservation -SourceKey $script:Root -Digest $script:DigestA)
            (New-AceObservation -Key 'ntfs_ace|a')
            [ordered]@{
                kind        = 'principal'
                run_id      = '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31'
                observed_at = '2026-03-02T09:00:00Z'
                source_key  = 'principal|S-1-5-21-9-9-9-1000'
            }
        )
        Complete-AdgNtfsBatchWriter -Writer $writer

        @($script:Batches[0].observations).Count | Should -Be 1
        $script:Batches[0].observations[0].kind | Should -Be 'principal'
        @($script:Batches[0].affirmations).Count | Should -Be 1
    }

    It 'sends a resource whose digest the collector omitted' {
        $index = New-AdgNtfsDigestIndex
        $index.Known[$script:Root] = $script:DigestA
        $writer = New-AdgNtfsBatchWriter -RunId '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31' `
            -BatchSize 500 -OnBatch $script:OnBatch -DigestIndex $index

        Add-AdgNtfsResourceGroup -Writer $writer -Group @(
            (New-ResourceObservation -SourceKey $script:Root -Digest $null)
        )
        Complete-AdgNtfsBatchWriter -Writer $writer

        @($script:Batches[0].observations).Count | Should -Be 1
        $script:Batches[0].Contains('affirmations') | Should -BeFalse
    }

    It 'records what it sent, so the next scan can affirm it' {
        $index = New-AdgNtfsDigestIndex
        $writer = New-AdgNtfsBatchWriter -RunId '6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31' `
            -BatchSize 500 -OnBatch $script:OnBatch -DigestIndex $index

        Add-AdgNtfsResourceGroup -Writer $writer -Group @(
            (New-ResourceObservation -SourceKey $script:Root -Digest $script:DigestA)
        )
        Complete-AdgNtfsBatchWriter -Writer $writer

        $index.Next[$script:Root] | Should -Be $script:DigestA
    }
}
