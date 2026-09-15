<#
    The job catalogue, and what a configuration file is allowed to say.

    ------------------------------------------------------------------------------------
    Why six jobs and not one scan

    A single "collect everything" run has one schedule, one failure, and one cost. The
    estate does not work that way: AD memberships change hourly and can be read in seconds,
    a deep NTFS walk takes hours and changes slowly, and an SMB inventory is a handful of
    objects per server. Tying them together means either reading the directory as rarely as
    the file system or walking the file system as often as the directory, and the second is
    how an installation ends up scanning continuously and still being a day behind.

    So each job is independent: its own schedule, its own state, its own lock, its own
    checkpoint, and its own failure. One job failing does not stop the next, and a job that
    is already running is skipped rather than started twice.

    ------------------------------------------------------------------------------------
    What each job may and may not claim

    Two of these are narrower than the scope they declare, and that is what makes them
    incremental in the protocol's sense:

    * ad_principals and ad_memberships each read one half of the domain. Neither has
      enumerated the domain, so neither may reconcile it - a principals pass that reconciled
      the domain scope would mark every membership edge absent, because it observed none.
      They are always incremental, whatever their strategy.
    * ntfs_important_roots reads a named subset of the tree on purpose.

    The full_reconciliation job is the one that repairs what the others cannot see, and it
    is the only one marked IsRepairPass. That is deliberately *not* the same as "reconciles":
    smb_inventory and ntfs_deep_scan reconcile too, on their own schedules, as ordinary
    collection. Only one job exists in order to repair, and the distinction is what lets an
    operator reading a non-zero drift number tell a scheduled repair from a routine scan.
#>

$script:AdgOrchestratorFormat = 'adg-orchestrator/1'

$script:AdgJobKinds = @{
    ad_principals        = @{
        Collector          = 'active_directory'
        SupportsDelta      = $true
        MayReconcile       = $false
        AlwaysIncremental  = $true
        IsRepairPass       = $false
        Description        = 'Users, groups and computers. Delta by uSNChanged where the watermark is usable.'
    }
    ad_memberships       = @{
        Collector          = 'active_directory'
        SupportsDelta      = $true
        MayReconcile       = $false
        AlwaysIncremental  = $true
        IsRepairPass       = $false
        Description        = 'Direct membership edges. A group''s uSNChanged moves when its member attribute does.'
    }
    smb_inventory        = @{
        Collector          = 'smb'
        SupportsDelta      = $false
        MayReconcile       = $true
        AlwaysIncremental  = $false
        IsRepairPass       = $false
        Description        = 'Shares and share ACLs. No source change metadata exists, and none is needed: a server has tens of shares.'
    }
    ntfs_important_roots = @{
        Collector          = 'ntfs'
        SupportsDelta      = $false
        MayReconcile       = $false
        AlwaysIncremental  = $true
        IsRepairPass       = $false
        Description        = 'A named subset of the tree, read often. Narrowed on purpose, so it never reconciles.'
    }
    ntfs_deep_scan       = @{
        Collector          = 'ntfs'
        SupportsDelta      = $false
        MayReconcile       = $true
        AlwaysIncremental  = $false
        IsRepairPass       = $false
        Description        = 'The whole tree. Re-reads every descriptor and affirms the unchanged ones instead of re-sending them.'
    }
    full_reconciliation  = @{
        Collector          = $null
        SupportsDelta      = $false
        MayReconcile       = $true
        AlwaysIncremental  = $false
        IsRepairPass       = $true
        Description        = 'Every collector, reading everything, reconciling every scope. The repair pass.'
    }
}


function Get-AdgJobKinds {
    <#
        .SYNOPSIS
            The job catalogue, as a copy.
        .DESCRIPTION
            A copy rather than the table itself: a caller that mutated the catalogue would
            change what every later job in the same process is allowed to do.
    #>
    [OutputType([hashtable])]
    param()

    $copy = @{}
    foreach ($name in $script:AdgJobKinds.Keys) {
        $copy[$name] = $script:AdgJobKinds[$name].Clone()
    }
    return $copy
}


function Get-AdgJobKind {
    <#
        .SYNOPSIS
            One job kind's rules, or a refusal naming the ones that exist.
    #>
    [OutputType([hashtable])]
    param([Parameter(Mandatory)][AllowEmptyString()][string] $Kind)

    if (-not $script:AdgJobKinds.ContainsKey($Kind)) {
        $known = ($script:AdgJobKinds.Keys | Sort-Object) -join ', '
        throw "'$Kind' is not a job kind. The configured kinds are: $known."
    }
    return $script:AdgJobKinds[$Kind]
}


function ConvertFrom-AdgDuration {
    <#
        .SYNOPSIS
            '15m', '6h', '7d', '90s' as a TimeSpan.
        .DESCRIPTION
            A unit is required. A bare number is refused rather than assumed to be minutes
            or seconds: the difference between those two readings of "30" is a job that runs
            sixty times more often than its author intended, against a production domain
            controller, and nothing about the result would look wrong.
    #>
    [OutputType([timespan])]
    param([Parameter(Mandatory)][AllowEmptyString()][string] $Text)

    $value = ([string] $Text).Trim().ToLowerInvariant()
    if (-not ($value -match '^([0-9]+)([smhd])$')) {
        throw "'$Text' is not a duration. Write a number followed by s, m, h or d - for example '15m', '6h' or '7d'. A bare number is refused because a mistaken unit is a schedule that is wrong by a factor of sixty and still looks plausible."
    }

    $quantity = [int] $Matches[1]
    if ($quantity -le 0) {
        throw "'$Text' is a zero duration. A job that is always due would run continuously; omit the schedule instead, or give an interval."
    }

    switch ($Matches[2]) {
        's' { return [timespan]::FromSeconds($quantity) }
        'm' { return [timespan]::FromMinutes($quantity) }
        'h' { return [timespan]::FromHours($quantity) }
        'd' { return [timespan]::FromDays($quantity) }
    }
}


function ConvertTo-AdgTimeOfDay {
    <#
        .SYNOPSIS
            'HH:mm' as a TimeSpan since midnight.
    #>
    [OutputType([timespan])]
    param([Parameter(Mandatory)][AllowEmptyString()][string] $Text)

    $value = ([string] $Text).Trim()
    if (-not ($value -match '^([01][0-9]|2[0-3]):([0-5][0-9])$')) {
        throw "'$Text' is not a time of day. Use 24-hour HH:mm, for example '22:00'."
    }
    return [timespan]::new([int] $Matches[1], [int] $Matches[2], 0)
}


function New-AdgJobDefinition {
    <#
        .SYNOPSIS
            One validated job.
        .DESCRIPTION
            Validation is total and happens here rather than at run time. An orchestrator
            invoked by Task Scheduler at 02:00 has nobody to read its errors, so a
            configuration that cannot work must fail when somebody types
            -ValidateOnly - not six hours later, quietly, in an event log.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string] $Name,
        [Parameter(Mandatory)][AllowEmptyString()][string] $Kind,
        [string] $Every,
        [bool] $Enabled = $true,
        [string] $Strategy = 'auto',
        [string] $CollectorConfig,
        [string[]] $Roots = @(),
        [string] $OnlyBetweenStart,
        [string] $OnlyBetweenEnd,
        [int] $MaxAttempts = 3,
        [int] $RetryBaseSeconds = 30,
        [int] $JitterSeconds = 0,
        [bool] $Reconcile = $false,
        [hashtable] $Settings = @{}
    )

    if ([string]::IsNullOrWhiteSpace($Name)) {
        throw 'A job needs a name. It is the key its checkpoint, its state file and its lock are all stored under, so it cannot be blank or duplicated.'
    }
    if ($Name -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
        throw "Job name '$Name' is not usable. It becomes a file name and a server-side checkpoint key, so it must be 1-128 characters of letters, digits, dot, dash or underscore, starting with a letter or digit."
    }

    $rules = Get-AdgJobKind -Kind $Kind

    if ($Strategy -notin @('auto', 'full', 'delta')) {
        throw "Job '$Name' has strategy '$Strategy'. Use 'auto' (delta when the source supports it and a usable checkpoint exists), 'full', or 'delta'."
    }
    if ($Strategy -eq 'delta' -and -not $rules.SupportsDelta) {
        throw "Job '$Name' is a $Kind job and cannot run as a delta: its source publishes no change metadata a delta could filter on. Use 'full' or 'auto'. See docs/architecture/incremental-collection.md."
    }
    if ($Reconcile -and -not $rules.MayReconcile) {
        throw "Job '$Name' is a $Kind job and may not reconcile. $($rules.Description) A run that reconciled a scope it only partly enumerated would mark the rest of that scope absent."
    }

    $interval = if ($Every) { ConvertFrom-AdgDuration -Text $Every } else { $null }
    if ($null -eq $interval -and $Enabled) {
        throw "Job '$Name' is enabled but has no 'every' interval, so nothing decides when it is due. Give it an interval, or disable it and invoke it by name."
    }

    $windowStart = if ($OnlyBetweenStart) { ConvertTo-AdgTimeOfDay -Text $OnlyBetweenStart } else { $null }
    $windowEnd = if ($OnlyBetweenEnd) { ConvertTo-AdgTimeOfDay -Text $OnlyBetweenEnd } else { $null }
    if (($null -eq $windowStart) -ne ($null -eq $windowEnd)) {
        throw "Job '$Name' declares only one end of its onlyBetween window. Give both 'start' and 'end', or neither."
    }
    if ($null -ne $windowStart -and $windowStart -eq $windowEnd) {
        throw "Job '$Name' has an onlyBetween window that starts and ends at the same minute, which is either the whole day or none of it. Remove the window if you meant the whole day."
    }

    if ($MaxAttempts -lt 1) {
        throw "Job '$Name' has maxAttempts $MaxAttempts. A job that may not be attempted at all cannot be scheduled; use enabled=false to switch it off."
    }
    if ($RetryBaseSeconds -lt 1) {
        throw "Job '$Name' has retryBaseSeconds $RetryBaseSeconds. A retry with no delay re-attacks a source that is already failing."
    }
    if ($JitterSeconds -lt 0) {
        throw "Job '$Name' has a negative jitterSeconds."
    }
    if ($Kind -eq 'ntfs_important_roots' -and @($Roots).Count -eq 0) {
        throw "Job '$Name' scans important roots but names none. Set 'roots' to the paths that must be read often; without them the job would either read nothing or quietly widen to the whole tree."
    }

    return [pscustomobject]@{
        Name             = $Name
        Kind             = $Kind
        Collector        = $rules.Collector
        Enabled          = $Enabled
        Every            = $interval
        Strategy         = $Strategy
        SupportsDelta    = $rules.SupportsDelta
        # Whether this job *is* the repair pass, not merely whether it reconciles.
        IsRepairPass     = $rules.IsRepairPass
        # A job whose kind is always incremental stays incremental however it is configured.
        # This is the flag the server reads before letting anything be marked absent, so it
        # is derived from the kind rather than taken from the file.
        AlwaysIncremental = $rules.AlwaysIncremental
        Reconcile        = [bool] ($Reconcile -and $rules.MayReconcile)
        CollectorConfig  = $CollectorConfig
        Roots            = @($Roots)
        WindowStart      = $windowStart
        WindowEnd        = $windowEnd
        MaxAttempts      = $MaxAttempts
        RetryBaseSeconds = $RetryBaseSeconds
        JitterSeconds    = $JitterSeconds
        Settings         = $Settings
    }
}


function Get-AdgConfigProperty {
    <#
        .SYNOPSIS
            A property of a ConvertFrom-Json object, or a default.
        .DESCRIPTION
            ConvertFrom-Json produces PSCustomObjects whose absent properties throw under
            Set-StrictMode. Every read of a configuration value goes through here so that
            an omitted optional setting is a default rather than a crash.
    #>
    param(
        [Parameter(Mandatory)][AllowNull()] $Object,
        [Parameter(Mandatory)][string] $Name,
        $Default = $null
    )

    if ($null -eq $Object) { return $Default }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) { return $Default }
    return $property.Value
}


function Import-AdgOrchestratorConfig {
    <#
        .SYNOPSIS
            Read and validate an orchestrator configuration file.
        .DESCRIPTION
            Every job is validated, and a single bad job fails the whole load. That is
            deliberate: the alternative is an orchestrator that runs five of six jobs and
            reports success, which is how a scope stops being collected without anybody
            being told.
    #>
    [OutputType([pscustomobject])]
    param([Parameter(Mandatory)][string] $Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Orchestrator configuration '$Path' does not exist. Copy adg-orchestrator.example.json and edit it."
    }

    $document = try {
        Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "Orchestrator configuration '$Path' is not valid JSON: $($_.Exception.Message)"
    }

    $format = [string] (Get-AdgConfigProperty $document 'schema' '')
    if ($format -ne $script:AdgOrchestratorFormat) {
        throw "Orchestrator configuration '$Path' declares schema '$format'; this orchestrator reads '$($script:AdgOrchestratorFormat)'."
    }

    $stateDirectory = [string] (Get-AdgConfigProperty $document 'stateDirectory' '')
    if ([string]::IsNullOrWhiteSpace($stateDirectory)) {
        throw "Orchestrator configuration '$Path' names no stateDirectory. Checkpoints, run records and locks live there, and without it a delta has nowhere to resume from."
    }

    $jobs = [System.Collections.Generic.List[object]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in @(Get-AdgConfigProperty $document 'jobs' @())) {
        $name = [string] (Get-AdgConfigProperty $entry 'name' '')
        if (-not $seen.Add($name)) {
            throw "Orchestrator configuration '$Path' defines job '$name' twice. Two jobs with one name share a checkpoint and a lock, so each would resume from the other's cursor."
        }
        $window = Get-AdgConfigProperty $entry 'onlyBetween' $null
        $jobs.Add((New-AdgJobDefinition `
                    -Name $name `
                    -Kind ([string] (Get-AdgConfigProperty $entry 'kind' '')) `
                    -Every ([string] (Get-AdgConfigProperty $entry 'every' '')) `
                    -Enabled ([bool] (Get-AdgConfigProperty $entry 'enabled' $true)) `
                    -Strategy ([string] (Get-AdgConfigProperty $entry 'strategy' 'auto')) `
                    -CollectorConfig ([string] (Get-AdgConfigProperty $entry 'collectorConfig' '')) `
                    -Roots (@(Get-AdgConfigProperty $entry 'roots' @())) `
                    -OnlyBetweenStart ([string] (Get-AdgConfigProperty $window 'start' '')) `
                    -OnlyBetweenEnd ([string] (Get-AdgConfigProperty $window 'end' '')) `
                    -MaxAttempts ([int] (Get-AdgConfigProperty $entry 'maxAttempts' 3)) `
                    -RetryBaseSeconds ([int] (Get-AdgConfigProperty $entry 'retryBaseSeconds' 30)) `
                    -JitterSeconds ([int] (Get-AdgConfigProperty $entry 'jitterSeconds' 0)) `
                    -Reconcile ([bool] (Get-AdgConfigProperty $entry 'reconcile' $false))))
    }

    if ($jobs.Count -eq 0) {
        throw "Orchestrator configuration '$Path' defines no jobs, so a scheduled invocation would do nothing and report success."
    }

    return [pscustomobject]@{
        Path           = $Path
        StateDirectory = $stateDirectory
        ApiBaseUrl     = [string] (Get-AdgConfigProperty $document 'apiBaseUrl' '')
        ApiTokenEnvironmentVariable = [string] (Get-AdgConfigProperty $document 'apiTokenEnvironmentVariable' 'ADG_COLLECTOR_TOKEN')
        CollectorHost  = [string] (Get-AdgConfigProperty $document 'collectorHost' $env:COMPUTERNAME)
        Jobs           = $jobs.ToArray()
    }
}
