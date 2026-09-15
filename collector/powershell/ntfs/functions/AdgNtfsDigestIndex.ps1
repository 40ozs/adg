<#
    The digest index: what this collector last told the server about each path.

    ------------------------------------------------------------------------------------
    What it is not

    It is **not** a cache of ACLs, and the distinction is the whole safety argument. A
    cached ACL would let a scan skip reading a descriptor, and nothing in the file system
    would tell it when that was wrong: writing an ACL does not move a directory's
    LastWriteTime, does not change its size, and does not touch any attribute a walk can
    cheaply test. A scan that skipped unchanged-looking directories would skip exactly the
    changes ADG exists to find.

    So every scan still reads every descriptor and computes its digest fresh. The index
    decides only what to *transmit*: a path whose freshly computed digest equals the one
    the index holds is affirmed - a key and a digest - instead of being re-sent as a
    resource and forty entries. The read is unchanged; the payload, the parse, the upsert
    and the history write are what shrink.

    This ordering is not a convention, it is enforced: Get-AdgNtfsAffirmableDigest is
    handed the digest the walk just computed and returns a decision, so there is no path
    through this module by which an index entry can become an affirmation without a fresh
    reading agreeing with it.

    ------------------------------------------------------------------------------------
    Being wrong is safe in one direction only, and that is the direction it is wrong in

    An index entry that is stale (the server has moved on) produces an affirmation the
    server refuses, and the collector re-sends the object in full on the next pass. Cost: a
    wasted line in a payload. An index entry that is missing produces a full send. Cost: the
    payload it would have saved.

    Neither can produce a wrong answer, because the server never takes the collector's word
    for it: it compares the affirmed digest against the acl_hash it holds and refuses a
    mismatch. The index is an optimization whose failure modes are all expensive rather than
    incorrect.

    ------------------------------------------------------------------------------------
    Size

    One line per path: the path and a 64-character digest, about 120 bytes on a typical UNC
    path. A million directories is roughly 120 MB, which is why it is a flat, streamed file
    rather than a JSON document that has to be materialized whole. MaxEntries bounds it: a
    scan that would exceed the bound stops adding rather than growing without limit, and
    reports that it did, because an index silently truncated at a size limit would make an
    arbitrary half of the estate stop being affirmable for no stated reason.
#>

$script:AdgDigestIndexHeader = 'adg-ntfs-digest-index/1'
$script:AdgDigestIndexDefaultMax = 2000000


function New-AdgNtfsDigestIndex {
    <#
        .SYNOPSIS
            An empty index.
    #>
    [OutputType([pscustomobject])]
    param(
        [string] $Fingerprint = '',
        [int] $MaxEntries = 0
    )

    return [pscustomobject]@{
        Fingerprint = $Fingerprint
        MaxEntries  = if ($MaxEntries -gt 0) { $MaxEntries } else { $script:AdgDigestIndexDefaultMax }
        Known       = [System.Collections.Generic.Dictionary[string, string]]::new([System.StringComparer]::OrdinalIgnoreCase)
        Next        = [System.Collections.Generic.Dictionary[string, string]]::new([System.StringComparer]::OrdinalIgnoreCase)
        Affirmed    = 0
        Sent        = 0
        Truncated   = $false
    }
}


function Import-AdgNtfsDigestIndex {
    <#
        .SYNOPSIS
            Read an index, or start an empty one.
        .DESCRIPTION
            Every refusal returns an empty index rather than throwing, and warns. An
            unreadable index costs a scan that sends everything, which is what the collector
            did before this file existed; failing the run instead would turn an optimization
            into a dependency.

            The fingerprint is the scan fingerprint the checkpoint already uses - the
            settings that decide which directories a walk visits. An index written under
            different roots or exclusions describes paths this scan will not visit, and
            keeping it would make its entries immortal: never re-read, never re-affirmed,
            never expired.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string] $Path,
        [string] $Fingerprint = '',
        [int] $MaxEntries = 0
    )

    $index = New-AdgNtfsDigestIndex -Fingerprint $Fingerprint -MaxEntries $MaxEntries
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $index
    }

    $reader = $null
    try {
        $reader = [System.IO.StreamReader]::new($Path, [System.Text.Encoding]::UTF8)
        $header = $reader.ReadLine()
        if ($header -ne $script:AdgDigestIndexHeader) {
            Write-Warning "Digest index '$Path' is format '$header' and this collector writes '$($script:AdgDigestIndexHeader)'; this scan sends every descriptor and writes a new index."
            return $index
        }
        $stamp = $reader.ReadLine()
        if ($Fingerprint -and $stamp -ne $Fingerprint) {
            Write-Warning "Digest index '$Path' was written for a different scan (different roots, depth limit, include/exclude patterns, reparse policy or file setting). Its entries describe paths this scan may never visit, so it is discarded and rebuilt."
            return $index
        }

        while ($null -ne ($line = $reader.ReadLine())) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            # The digest is fixed-width and last, so the path may contain anything at all -
            # including the separator. Splitting on the first tab would truncate a path that
            # contains one; taking the last 64 characters cannot.
            if ($line.Length -lt 66) { continue }
            $digest = $line.Substring($line.Length - 64)
            if ($digest -notmatch '^[0-9a-f]{64}$') { continue }
            $path = $line.Substring(0, $line.Length - 65)
            if ([string]::IsNullOrWhiteSpace($path)) { continue }
            $index.Known[$path] = $digest
        }
    }
    catch {
        Write-Warning "Digest index '$Path' could not be read ($($_.Exception.Message)); this scan sends every descriptor."
        return (New-AdgNtfsDigestIndex -Fingerprint $Fingerprint -MaxEntries $MaxEntries)
    }
    finally {
        if ($null -ne $reader) { $reader.Dispose() }
    }

    return $index
}


function Get-AdgNtfsAffirmableDigest {
    <#
        .SYNOPSIS
            Whether this freshly read descriptor may be affirmed instead of re-sent.
        .DESCRIPTION
            The digest argument is the one the walk computed from the descriptor it just
            read. There is deliberately no overload that takes only a path: an affirmation
            must be a statement about the object, and the only way to make one is to have
            read the object.

            Returns $false when the collector reported no digest at all. A resource whose
            acl_hash was omitted - because an entry could not be reported - is one whose
            state the collector cannot summarize, and there is nothing to affirm.
    #>
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Index,
        [Parameter(Mandatory)][AllowEmptyString()][string] $Path,
        [AllowEmptyString()][AllowNull()][string] $Digest
    )

    if ([string]::IsNullOrWhiteSpace($Path) -or [string]::IsNullOrWhiteSpace($Digest)) { return $false }
    $known = ''
    if (-not $Index.Known.TryGetValue($Path, [ref] $known)) { return $false }
    return $known -eq $Digest
}


function Set-AdgNtfsDigest {
    <#
        .SYNOPSIS
            Record what this scan knows about a path, for the next scan to compare against.
        .DESCRIPTION
            Written to the *next* index rather than mutating the one being read. That is
            what makes a path that disappeared drop out: the new index contains exactly the
            paths this scan visited, so a deleted directory is simply not carried forward
            instead of accumulating for ever behind a rule nobody would remember to write.
    #>
    param(
        [Parameter(Mandatory)][pscustomobject] $Index,
        [Parameter(Mandatory)][AllowEmptyString()][string] $Path,
        [AllowEmptyString()][AllowNull()][string] $Digest
    )

    if ([string]::IsNullOrWhiteSpace($Path) -or [string]::IsNullOrWhiteSpace($Digest)) { return }
    if ($Index.Next.Count -ge $Index.MaxEntries -and -not $Index.Next.ContainsKey($Path)) {
        if (-not $Index.Truncated) {
            $Index.Truncated = $true
            Write-Warning "The digest index reached its ceiling of $($Index.MaxEntries) entries. Directories beyond it are sent in full on every scan. Raise digestIndexMaxEntries, or accept the cost and know why it is there."
        }
        return
    }
    $Index.Next[$Path] = $Digest
}


function Export-AdgNtfsDigestIndex {
    <#
        .SYNOPSIS
            Write the index this scan built, atomically.
        .DESCRIPTION
            Only for a walk that completed. A partial walk's index holds the paths it
            reached and not the ones it did not, and writing it would make the next scan
            treat every unreached directory as unknown - which is correct - while also
            *discarding* the entries the previous index held for them, which is a
            regression the scan after that would pay for again.

            Temp file plus rename, for the reason the run checkpoint uses it: a
            half-written index is not a slightly stale index, it is one that fails to load.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Index,
        [Parameter(Mandatory)][AllowEmptyString()][string] $Path,
        [switch] $Partial
    )

    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    if ($Partial) {
        Write-Verbose "The walk did not complete, so the digest index at '$Path' is left as it was."
        return $null
    }

    $directory = Split-Path -Parent $Path
    if ($directory -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    $temporary = "$Path.tmp"
    $writer = $null
    try {
        $writer = [System.IO.StreamWriter]::new($temporary, $false, [System.Text.UTF8Encoding]::new($false))
        $writer.WriteLine($script:AdgDigestIndexHeader)
        $writer.WriteLine($Index.Fingerprint)
        foreach ($pair in $Index.Next.GetEnumerator()) {
            $writer.WriteLine("$($pair.Key)`t$($pair.Value)")
        }
    }
    finally {
        if ($null -ne $writer) { $writer.Dispose() }
    }
    Move-Item -LiteralPath $temporary -Destination $Path -Force
    return $Path
}
