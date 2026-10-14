<#
    Translation layer: raw readings of one host's local groups to contract observations.

    Pure: no host, no network. Everything a reading means is decided here, so it is decided
    once and tested once.

    What a local-groups run reports, and what it deliberately does not:

      * each local group, as a `local_group` principal scoped to its host - a BUILTIN SID such
        as S-1-5-32-544 is identical on every Windows computer, and only the host makes it
        one group;
      * each direct member, as a `local_group_member` edge on that host, carrying the SID the
        host reported and the kind the host could establish - `unresolved` when it could
        not, which is a finding, never a reason to drop the member;
      * **not** a principal observation for a member. A domain user's name and attributes are
        the directory collector's to report, and a local host's view of them (often only a
        SID) must not overwrite that.
#>

# SID_NAME_USE, as NetLocalGroupGetMembers and LookupAccountName report it.
$script:SidTypeUser = 1
$script:SidTypeGroup = 2
$script:SidTypeDomain = 3
$script:SidTypeAlias = 4
$script:SidTypeWellKnownGroup = 5
$script:SidTypeDeletedAccount = 6
$script:SidTypeInvalid = 7
$script:SidTypeUnknown = 8
$script:SidTypeComputer = 9


function ConvertTo-AdgLocalMemberKind {
    <#
        .SYNOPSIS
            The contract member_kind for a SID type the host reported, or $null when it said
            nothing a kind can be read from.
        .DESCRIPTION
            An alias is a local group when it is BUILTIN, and otherwise a domain-local group:
            Windows does not nest one machine's local groups in each other, so a non-BUILTIN
            alias in a member list was issued by a domain. A deleted, invalid or unknown SID
            is `unresolved` - the host looked and could not say what it is - which the backend
            carries as an unresolved membership rather than as a membership of nobody.
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][int] $SidUsage,
        [Parameter(Mandatory)][string] $Sid
    )

    switch ($SidUsage) {
        $script:SidTypeUser { return 'user' }
        $script:SidTypeGroup { return 'domain_group' }
        $script:SidTypeAlias {
            if ($Sid.StartsWith('S-1-5-32-')) { return 'local_group' }
            return 'domain_group'
        }
        $script:SidTypeWellKnownGroup { return 'well_known' }
        $script:SidTypeComputer { return 'computer' }
        $script:SidTypeDeletedAccount { return 'unresolved' }
        $script:SidTypeInvalid { return 'unresolved' }
        $script:SidTypeUnknown { return 'unresolved' }
    }
    return $null
}


function Test-AdgHostLocalGroupAccount {
    <#
        .SYNOPSIS
            Whether a name lookup really found one of this host's own local groups.
        .DESCRIPTION
            LookupAccountName searches the host's own account database before any domain, so
            a local group's name resolves to that group - but the result is checked rather
            than assumed: it must be an alias, and issued by BUILTIN or by the host itself.
            A group whose name resolved to anything else is reported as an error and left
            out, because recording a domain group as this machine's would put it in the
            wrong scope.
    #>
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)][pscustomobject] $Account,
        [Parameter(Mandatory)][string] $HostNetBiosName
    )

    if ($Account.SidUsage -ne $script:SidTypeAlias) { return $false }
    if ($Account.Sid.StartsWith('S-1-5-32-')) { return $Account.Domain -eq 'BUILTIN' }
    return [string]::Equals($Account.Domain, $HostNetBiosName, [System.StringComparison]::OrdinalIgnoreCase)
}


function ConvertTo-AdgLocalGroupObservation {
    <#
        .SYNOPSIS
            One local group and its direct members, as contract observations.
        .DESCRIPTION
            Returns a hashtable: Observations (the group's principal, then one edge per
            member) and Errors (a member the contract cannot carry, which is reported rather
            than dropped: a self-membership or a value that is not a SID).
    #>
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)][string] $RunId,
        [Parameter(Mandatory)][string] $HostKey,
        [Parameter(Mandatory)][string] $GroupSid,
        [Parameter(Mandatory)][string] $GroupName,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]] $Members,
        [Parameter(Mandatory)][string] $ObservedAt
    )

    $observations = [System.Collections.Generic.List[object]]::new()
    $errors = [System.Collections.Generic.List[object]]::new()
    $observations.Add(
        (New-AdgPrincipalObservation -RunId $RunId -Sid $GroupSid -PrincipalKind 'local_group' `
            -HostKey $HostKey -DisplayName $GroupName -ObservedAt $ObservedAt)
    )

    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($member in $Members) {
        $sid = [string] $member.Sid
        $target = "\\$HostKey\$GroupName"
        if ($sid -notmatch '^S-1-(0[xX][0-9a-fA-F]{1,12}|[0-9]{1,20})(-[0-9]{1,10}){0,15}$') {
            $errors.Add((New-AdgCollectorError -Code 'invalid_member' -Target $target `
                        -Message "A member of $GroupName was reported without a valid SID ('$sid'); it is not recorded." -OccurredAt $ObservedAt))
            continue
        }
        if ($sid -eq $GroupSid) {
            $errors.Add((New-AdgCollectorError -Code 'invalid_member' -Target $target `
                        -Message "$GroupName was reported as a member of itself, which Windows does not create; the entry is not recorded." -OccurredAt $ObservedAt))
            continue
        }
        if (-not $seen.Add($sid)) { continue }
        $kind = ConvertTo-AdgLocalMemberKind -SidUsage ([int] $member.SidUsage) -Sid $sid
        $observations.Add(
            (New-AdgMembershipObservation -RunId $RunId -GroupSid $GroupSid -MemberSid $sid `
                -EdgeKind 'local_group_member' -HostKey $HostKey -MemberKind $kind `
                -ObservedAt $ObservedAt)
        )
    }
    return @{ Observations = $observations.ToArray(); Errors = $errors.ToArray() }
}
