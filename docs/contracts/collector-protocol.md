# ADG collector protocol (contract v1)

**Status:** accepted (Phase 0B)
**Schemas:** [`docs/contracts/v1/`](v1/)
**Backend models:** `backend/app/contracts/v1/`
**Decisions:** [ADR-0003](../decisions/0003-raw-observations-vs-derived-state.md),
[ADR-0004](../decisions/0004-read-only-collector-posture.md)

This document tells a collector author exactly how to begin, batch, complete, fail, and
retry a scan. It is normative: a collector that follows it can be replayed, interrupted,
retried, and run concurrently with other collectors without corrupting what ADG believes.

Two rules govern everything here.

1. **Report readings, not conclusions.** Send the access mask, the flags, the SID — never
   "this user has Modify". Effective access is computed by the backend from these facts.
2. **Silence means nothing.** An object missing from a batch is not an object that was
   deleted. Only an explicitly *reconciled scope* on a successful run lets the server
   conclude that something is gone.

---

## 1. The sequence

```text
   collector                                            ADG API
       |                                                   |
       |  POST /api/v1/scan-runs            (start)        |
       |-------------------------------------------------->|   201 Created  (or 200 if replayed)
       |                                                   |
       |  POST /api/v1/scan-runs/{run_id}/batches          |
       |-------------------------------------------------->|   202 Accepted {"applied": 1000}
       |  ... repeat, at most 1000 observations per batch  |
       |-------------------------------------------------->|   202 Accepted {"duplicate": true}
       |                                                   |
       |  POST /api/v1/scan-runs/{run_id}/completion       |
       |-------------------------------------------------->|   200 OK
       |                                                   |
```

| Step | Endpoint | Body schema | Idempotency key |
| --- | --- | --- | --- |
| Begin | `POST /api/v1/scan-runs` | [`scan-run-start`](v1/scan-run-start.schema.json) | `run_id` |
| Send | `POST /api/v1/scan-runs/{run_id}/batches` | [`observation-batch`](v1/observation-batch.schema.json) | `(run_id, batch_id)` |
| Finish | `POST /api/v1/scan-runs/{run_id}/completion` | [`scan-run-completion`](v1/scan-run-completion.schema.json) | `run_id` |
| Inspect | `GET /api/v1/scan-runs/{run_id}` | — | — |

The collector generates `run_id` and every `batch_id` (UUIDv4) **before** sending, and
reuses them verbatim on retry. That is what makes each step idempotent: the server can
recognize a repeat rather than guess.

> The endpoints are specified here and implemented from Phase 1 onward. The payload
> contracts are accepted now and will not change shape without a `v2` directory and a
> migration note.

---

## 2. Beginning a scan

A start envelope declares **who is collecting**, **when**, and **what the run intends to
enumerate completely**:

```json
{
  "schema_version": "1.0",
  "run_id": "6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31",
  "source": {
    "collector": "ntfs",
    "collector_host": "COLLECTOR01",
    "method": "System.IO.DirectoryInfo.GetAccessControl",
    "collector_version": "0.1.0",
    "target": "\\\\FS01\\Finance"
  },
  "started_at": "2026-09-14T08:00:00Z",
  "scopes": [{ "kind": "directory_tree", "key": "\\\\fs01\\finance" }],
  "incremental": false
}
```

`scopes` is required and must be non-empty. A scope is the boundary inside which absence
may later be inferred:

| Scope kind | `key` | Meaning |
| --- | --- | --- |
| `domain` | domain SID | Every principal and membership edge in the domain. |
| `server` | case-folded host name | Every share on that server. |
| `share` | `server\|share`, case-folded | That share's definition and share ACL. |
| `directory_tree` | canonical UNC path, case-folded | Every ACL boundary at or beneath that path. |
| `local_groups_host` | case-folded host name | Every local group and local membership on that host. |

Set `incremental: true` when the run deliberately re-reads only part of its scopes (see
§7). An incremental run may never reconcile.

From **1.4** a start envelope may also carry `mode` (why the run is or is not incremental),
`job` (the scheduled job it belongs to), and `baseline` (the checkpoint a delta resumed
from). See §11.

**Responses:** `201 Created` for a new run; `200 OK` with the existing run for a replayed
start; `409 Conflict` if `run_id` exists with a different source or scopes — a collector
must not reuse a run id for a different run; `422 Unprocessable Entity` with field-level
errors for an invalid payload.

---

## 3. Every observation carries the same five fields

| Field | Purpose |
| --- | --- |
| `schema_version` | Contract version (`1.x`). |
| `kind` | Which fact this is: `principal`, `membership_edge`, `server`, `smb_share`, `smb_ace`, `ntfs_resource`, `ntfs_ace`. |
| `run_id` | The run this observation belongs to. Must match the batch's `run_id`. |
| `observed_at` | RFC 3339 timestamp **with an offset**. A naive timestamp is rejected. |
| `source_key` | Deterministic identity of the observed object (§4). |

The collector identity lives on the run, not on every observation: `source` is sent once in
the start envelope and applies to everything in the run.

---

## 4. Source keys: the basis of idempotency

`source_key` is derived **only** from identifying fields, so the same object always produces
the same key — across batches, across runs, and across collector implementations.
Ingestion is keyed on `(run_id, source_key)`, so re-sending an observation updates nothing
and creates nothing.

The server recomputes the key and **rejects a mismatch**, because a collector deriving keys
differently would silently create a second row for an object that already exists.

| Kind | Derivation |
| --- | --- |
| `principal` | `principal\|<sid>` — and `principal\|<host>\|<sid>` when `principal_kind` is `local_group` |
| `membership_edge` | `edge\|<group_key>-><member_key>\|<edge_kind>` |
| `server` | `server\|<host>` |
| `smb_share` | `share\|<host>\|<share>` |
| `smb_ace` | `smb_ace\|<host>\|<share>\|<trustee_sid>\|<ace_type>\|<permission or 0x%08x mask>` |
| `ntfs_resource` | `resource\|<unc path>` |
| `ntfs_ace` | `ntfs_ace\|<unc path>\|<trustee_sid>\|<ace_type>\|0x%08x mask\|0x%02x flags` |

Rules used above:

* Host names, share names, and UNC paths are **case-folded**; SIDs are canonical (upper-case
  `S`, no leading zeros).
* For a membership edge, `group_key` is `<host>|<sid>` for a `local_group_member` edge and
  `<sid>` otherwise; `member_key` is host-scoped only when the member's own SID is a BUILTIN
  SID. A domain group nested into a local group keeps its global key.
* `ntfs_ace` keys deliberately exclude `order_index`: two ACEs identical in trustee, type,
  mask, and flags are duplicates of each other, and reordering an ACL must not look like
  every ACE being deleted and recreated.

The normative implementation is `backend/app/contracts/v1/keys.py`, and a test asserts this
table matches it.

---

## 5. Batching

```json
{
  "schema_version": "1.0",
  "run_id": "6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31",
  "batch_id": "b1a7c0de-1111-4222-8333-444455556666",
  "sequence": 1,
  "is_final": false,
  "observations": [ /* 1..1000 observations */ ]
}
```

* **Chunking.** At most **1000 observations** per batch. An oversized batch is rejected, not
  truncated — silent truncation would look like coverage. Aim for batches of a few hundred
  so a retry is cheap.
* **Ordering.** Batches may be sent in any order and a `sequence` gap is allowed (an
  abandoned batch), but a batch must not be sent after the completion envelope. Send an
  object's defining observation before or in the same batch as its dependents where you
  can — an `ntfs_resource` with its `ntfs_ace` entries, a `smb_share` with its `smb_ace`
  entries — so partial data is interpretable. The server does not require it.
* **Mixed kinds are allowed** in one batch, and often natural: a resource plus its ACEs.
* **Keep a directory's observations in one batch.** For `ntfs_resource`, this is stronger
  than the "where you can" above. The server verifies a reported `acl_hash` against the
  `ntfs_ace` observations that arrive with it, and *skips* the check when the batch holds
  fewer than the declared `ace_count` — so a split silently disables the one check that
  catches ACEs lost in transit. Let a batch run slightly over your target size rather than
  cut a DACL, and never past the 1000 ceiling: a DACL that large is itself a finding.
* **No duplicate `source_key` within a batch.** Two observations with the same key are
  indistinguishable; the payload is rejected rather than half-applied.
* **`is_final`** is advisory. Only the completion envelope ends a run.
* **`affirmations`** (1.4) may accompany the observations, or replace them entirely. A batch
  must carry at least one observation *or* one affirmation; a batch with neither is rejected.
  A `source_key` may not appear in both lists. See §11.
* **`continuation_token`** is an opaque collector-side cursor echoed back for diagnostics.
  The server never interprets it. Use it to resume enumeration after a crash.

**Responses:** `202 Accepted` with `{"applied": <count>, "duplicate": false}`; `202` with
`{"duplicate": true, "applied": 0}` when `batch_id` was already applied; `409 Conflict` if
the run is already completed; `422` with per-observation field errors otherwise. A `422` is
never retried unchanged — the payload is wrong, and retrying it will fail identically.

---

## 6. Completing a scan

```json
{
  "schema_version": "1.0",
  "run_id": "6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31",
  "status": "succeeded",
  "completed_at": "2026-09-14T08:05:00Z",
  "batch_count": 3,
  "observation_count": 2412,
  "error_count": 0,
  "errors": [],
  "reconciled_scopes": [{ "kind": "directory_tree", "key": "\\\\fs01\\finance" }]
}
```

| Status | Meaning | May reconcile? |
| --- | --- | --- |
| `succeeded` | Every declared scope was enumerated completely, with no errors. | **Yes** |
| `partial` | Usable observations, incomplete coverage (something was unreadable). | No |
| `failed` | Coverage unknown. | No |
| `canceled` | Stopped deliberately. | No |

`batch_count` lets the server detect loss: if it received fewer batches than the collector
says it sent, the run is downgraded to `partial` regardless of the status claimed.

From **1.4** a completion may also carry `affirmation_count` and `checkpoint`. A checkpoint
is permitted **only** on a run reporting `succeeded` with no errors, and the server refuses
one on a run it downgraded for short delivery — see §11.

A run that reports any error **cannot** be `succeeded` — the contract rejects that
combination. Reporting complete coverage that was not achieved understates access, which is
the most dangerous kind of wrong answer an audit tool can give.

### Reconciliation: the only way anything is marked absent

`reconciled_scopes` is the end-of-scan marker. For each listed scope, the server may mark
objects it did not see in this run as no longer present. It is accepted only when **all** of
these hold:

1. `status` is `succeeded`;
2. `error_count` is `0`;
3. the run was not `incremental`;
4. every reconciled scope was declared in the start envelope.

Otherwise the server rejects the completion (or, at minimum, ignores the reconciliation and
records the run as `partial`). This is what makes the guarantee in §0 concrete: **a partial
scan cannot delete unseen objects**, because a partial scan is structurally incapable of
reconciling.

Marking absent is not deletion: the current-state row stays, and the object's timeline gains
a tombstone recording that it was no longer observed as of this run.

**A scope must never claim more than the run read**, and the file-system scopes are where
that bites. `directory_tree` claims the whole tree beneath a path was enumerated. A run that
reads share *roots* and nothing else has enumerated no tree, so reconciling that scope would
mark every directory under every root as deleted. Such a run therefore declares its
`directory_tree` scopes - they state what it set out to look at - but marks itself
`incremental`, which the server refuses to let reconcile at all. The NTFS collector shipped
in Phase 3A did exactly this on every run, including a clean one.

**A walk that really did enumerate a tree may claim it**, which the recursive scanner added
in Phase 3B. Two separate statements are involved and conflating them gets it wrong in one
direction or the other:

* `incremental` in the **start** envelope is a statement of *intent*, and it has to be made
  before a single directory is read, because the server refuses to let it change mid-run. It
  is `false` only when nothing in the configuration already says the run will look at part of
  its scope: no include or exclude patterns, no deadline, not resuming a checkpoint. A depth
  *limit* is deliberately not in that list - it is an upper bound a shallow tree never
  reaches, and treating it as intent would forbid reconciliation on every configured scan.
* `reconciled_scopes` in the **completion** envelope is a statement of *achievement*, decided
  from what actually happened. A tree is listed only if the walk read its root and skipped
  nothing at all beneath it: no depth limit reached, no exclude pattern applied, no junction
  left unfollowed, no descriptor denied, no directory left unlistable, no timeout, no resume.

A collector that cannot separate those two ends up either never reconciling anything or
reconciling a truncated scan - and the second marks live permissions as revoked.

---

## 7. Failure and retry

| Situation | What the collector does |
| --- | --- |
| Network error / timeout / `5xx` / `429` | Retry the **same** payload with the **same** `batch_id`, using exponential backoff with jitter (suggested: 1s, 2s, 4s, 8s, 30s; cap at 5 attempts). |
| `422 Unprocessable Entity` | Do **not** retry unchanged. Log the field errors, drop or fix the offending observation, and continue. A malformed observation must not abort the run. |
| `409 Conflict` (run already completed) | Stop sending batches. Start a new run. |
| Unreadable object (`access_denied`, `path_too_long`, RPC failure) | Record a `collectorError`, keep going, finish the run as `partial`, and reconcile nothing. Never escalate privilege, take ownership, or modify the object to make a read succeed (ADR-0004). |
| Collector crashed mid-run | Resume with the same `run_id` and a fresh `batch_id` per batch, or start a new run. A run left without a completion is treated as `failed` after a timeout and never reconciles. |
| Only part of a scope was re-read on purpose | Set `incremental: true` at start. The run contributes observations but can never reconcile. |

**Concurrency.** Two collectors may scan different scopes simultaneously; each owns its own
`run_id`. Do not send two runs that reconcile the same scope concurrently.

---

## 8. Reporting rules that keep observations usable

These follow from ADR-0003. A collector that "helps" by simplifying destroys evidence.

* **Send the raw access mask.** Do not expand `GENERIC_*` bits, do not drop bits you do not
  recognize, do not translate to Read/Write/Modify.
* **Send the raw `ace_flags` byte**, and set `source` to match bit `0x10` (see the mapping in
  §9). An `explicit` ACE must not carry `inherited_from`.
* **Report `dacl_present` honestly.** `false` (a NULL DACL) means *everyone has full
  access* — the opposite of an empty ACE list, which means nobody does. Never report a NULL
  DACL as zero ACEs with `dacl_present: true`.
* **Keep unresolvable SIDs.** Report them as `principal_kind: "unresolved"` with a
  `unresolved_reason`, and never invent a `display_name`. A name from an earlier scan goes in
  `last_known_name`.
* **Include primary-group membership** (`edge_kind: "primary_group"`). It does not appear in
  a group's `member` attribute; omitting it under-reports access for every user in the
  domain.
* **Scope local groups by host.** `S-1-5-32-544` is the same SID on every computer.
* **Share ACEs carry exactly one right form**: `permission` (what `Get-SmbShareAccess`
  reports) or `access_mask` (what the descriptor APIs report). Never both.
* **Never send derived facts.** There is no field anywhere in this contract for effective
  access, expanded membership, or a risk verdict, and there never will be in v1.

---

## 9. Building payloads in PowerShell

A runnable end-to-end example is at
[`collector/powershell/examples/Send-AdgScanRun.ps1`](../../collector/powershell/examples/Send-AdgScanRun.ps1).
It supports `-DryRun -OutputDirectory <path>` to write the exact JSON it would POST, and a
test validates that output against these schemas — so the example cannot drift from the
contract.

### Mapping .NET ACL objects to the contract

`Get-Acl` returns `FileSystemAccessRule` objects whose inheritance information is split
across three properties. The contract wants the raw ACE flags byte:

| Contract bit | Value | Comes from |
| --- | --- | --- |
| `OBJECT_INHERIT` | `0x01` | `InheritanceFlags` contains `ObjectInherit` |
| `CONTAINER_INHERIT` | `0x02` | `InheritanceFlags` contains `ContainerInherit` |
| `NO_PROPAGATE_INHERIT` | `0x04` | `PropagationFlags` contains `NoPropagateInherit` |
| `INHERIT_ONLY` | `0x08` | `PropagationFlags` contains `InheritOnly` |
| `INHERITED` | `0x10` | `IsInherited` is `$true` |

```powershell
function ConvertTo-AdgAceFlags {
    param(
        [System.Security.AccessControl.InheritanceFlags] $InheritanceFlags,
        [System.Security.AccessControl.PropagationFlags] $PropagationFlags,
        [bool] $IsInherited
    )
    [int] $flags = 0
    if ($InheritanceFlags.HasFlag([System.Security.AccessControl.InheritanceFlags]::ObjectInherit))    { $flags = $flags -bor 0x01 }
    if ($InheritanceFlags.HasFlag([System.Security.AccessControl.InheritanceFlags]::ContainerInherit)) { $flags = $flags -bor 0x02 }
    if ($PropagationFlags.HasFlag([System.Security.AccessControl.PropagationFlags]::NoPropagateInherit)) { $flags = $flags -bor 0x04 }
    if ($PropagationFlags.HasFlag([System.Security.AccessControl.PropagationFlags]::InheritOnly))        { $flags = $flags -bor 0x08 }
    if ($IsInherited) { $flags = $flags -bor 0x10 }
    return $flags
}
```

### Reading a DACL without resolving names

```powershell
$path = '\\FS01\Finance\Reports'
$acl  = Get-Acl -LiteralPath $path

# SIDs, not names: IdentityReference is translated only for display.
foreach ($rule in $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) {
    [pscustomobject]@{
        trustee_sid = $rule.IdentityReference.Value
        ace_type    = if ($rule.AccessControlType -eq 'Allow') { 'allow' } else { 'deny' }
        access_mask = [int] $rule.FileSystemRights   # raw mask; do not translate
        ace_flags   = ConvertTo-AdgAceFlags $rule.InheritanceFlags $rule.PropagationFlags $rule.IsInherited
        source      = if ($rule.IsInherited) { 'inherited' } else { 'explicit' }
    }
}
```

`$acl.AreAccessRulesProtected` gives `dacl_protected`. A NULL DACL surfaces as
`$acl.Access.Count -eq 0` with `$acl.Sddl` lacking a `D:` section — report it as
`dacl_present: $false`, never as an empty rule list.

### A share ACL as levels

```powershell
Get-SmbShareAccess -Name 'Finance' | ForEach-Object {
    [pscustomobject]@{
        trustee_sid = (New-Object System.Security.Principal.NTAccount($_.AccountName)).Translate([System.Security.Principal.SecurityIdentifier]).Value
        ace_type    = if ($_.AccessControlType -eq 'Allow') { 'allow' } else { 'deny' }
        permission  = $_.AccessRight.ToString().ToLowerInvariant()   # read | change | full
    }
}
```

If the account cannot be translated to a SID, report the ACE with the SID you *do* have (the
descriptor stores SIDs, not names) and a `principal` observation with
`principal_kind: "unresolved"`. Never drop the ACE.

### Sending a batch with retry

```powershell
function Send-AdgBatch {
    param([string] $ApiBaseUrl, [string] $RunId, [hashtable] $Batch)

    $json = $Batch | ConvertTo-Json -Depth 12 -Compress
    $uri  = "$ApiBaseUrl/api/v1/scan-runs/$RunId/batches"

    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            return Invoke-RestMethod -Method Post -Uri $uri -Body $json -ContentType 'application/json'
        }
        catch [Microsoft.PowerShell.Commands.HttpResponseException] {
            $status = [int] $_.Exception.Response.StatusCode
            if ($status -eq 422) { throw }          # payload is wrong; retrying cannot help
            if ($attempt -eq 5)  { throw }
            Start-Sleep -Seconds ([Math]::Pow(2, $attempt))   # same batch_id: the retry is idempotent
        }
    }
}
```

---

## 10. Versioning

* `docs/contracts/v1/` is contract major version 1. Every payload carries
  `schema_version: "1.x"`.
* **Additive** changes (a new optional field, a new enum value that older collectors never
  send) bump the minor version. Servers accept any `1.x`.
* **Breaking** changes (a removed or renamed field, a narrowed type, a new required field)
  require `docs/contracts/v2/`, a new endpoint prefix, and a migration note in the phase
  handoff that makes the change.
* An accepted schema file is never edited in place to mean something different.

### 1.1 (Phase 2A)

`smb_share` gains an optional `is_special`: the SMB server's own Special flag, marking an
administrative or system share. It is additive, so a `1.0` payload that omits it is still
valid and every `1.x` server accepts both.

It is a *source fact* and could not be derived. Hidden-ness follows from a trailing `$` in
the share name - and the collector does derive that rather than transmitting it - but an
ordinary hidden share such as `Data$` is hidden and *not* Special, so a name test cannot
tell an administrator's hidden share from Windows's own.

Two related values are deliberately still absent from `smb_share`, and neither is an
oversight:

* the **UNC path**, which is exactly `\\<server_name>\<share_name>`. Carrying it as well
  would create a second identity for the share that can disagree with the first.
* **hidden state**, derived from the share name as above.

Availability likewise has no field here. A share the collector could not read is reported
as a `collectorError` on the completion envelope and leaves the run `partial`; a server it
could not reach produces no `server` observation at all. Absence of an observation never
means the object is gone - only a reconciled scope says that.

### 1.2 (Phase 3A)

`ntfs_resource` gains an optional `acl_hash`: the digest of the normalized DACL the collector
read, specified in
[`docs/architecture/ntfs-acl-normalization.md`](../architecture/ntfs-acl-normalization.md)
and decided in [ADR-0008](../decisions/0008-acl-normal-form-and-hash.md). It is additive, so
a `1.0` or `1.1` payload that omits it is still valid and every `1.x` server accepts both.

It could not be derived from the ACEs alone, for the reason the field exists at all: the
digest covers `dacl_present` and `dacl_protected` as well as the entries, and it is the
collector's statement about the descriptor it held **in one piece**. The server recomputes it
from the entries it stores and reports both, so a disagreement surfaces as the coverage gap
it is rather than being settled by whichever number was written last.

Three rules bind a collector that sends one:

* compute it over the **whole** DACL, in the reading you are reporting;
* **omit it entirely** if any entry could not be reported - an unclassifiable ACE type, a
  trustee with no usable SID. A digest over part of a DACL is indistinguishable from a digest
  of all of it, and comparing one to a parent's would answer the boundary question wrong
  without ever looking wrong;
* send the resource and its `ntfs_ace` observations in one batch (§5), so the server can
  check it.

A batch whose reported digest contradicts the entries sent with it is rejected with `422`,
and nothing in it is stored. The error carries both digests and the normalized document the
server hashed.

### 1.3 (Phase 3B)

`ntfs_resource` gains three optional fields, all additive: a `1.0` through `1.2` payload that
omits them is still valid, and every `1.x` server accepts all four minors.

| Field | Carries |
| --- | --- |
| `resource_kind` | `directory` (the default, and what every earlier payload meant) or `file` |
| `boundary_reason` | Why `is_acl_boundary` is true |
| `parent_acl_hash` | The parent's `acl_hash` as this run read it |

**`resource_kind` could not be derived**, and it is not decoration: it decides which
inheritance projection a resource is compared against. A directory receives its parent's
`CONTAINER_INHERIT` entries and keeps propagating them; a file receives the `OBJECT_INHERIT`
ones with every inheritance flag stripped. Those are different documents, so comparing a file
against the container projection would report a boundary on every file in an estate. A path
does not say which kind it names, and a share root is always a directory - reporting one as a
file is rejected.

**`boundary_reason` is required when `is_acl_boundary` is true and forbidden when it is
false** - but the first half of that rule applies only to payloads that declare `1.3` or
later. A `1.2` collector sets the flag on a share root and has never heard of the field;
holding it to a rule it predates would reject it for being old rather than for being wrong,
which is not what "additive" means. Such rows store the reason as null, which reads as *the
collector that wrote this predates the field* rather than as a reason. The other half of the
rule holds at every version: a reason on a resource that is not a boundary contradicts
itself, and no collector of any minor can have meant it.

The seven values, and what each means, are specified in
[`docs/architecture/ntfs-acl-boundaries.md`](../architecture/ntfs-acl-boundaries.md) and
decided in
[ADR-0009](../decisions/0009-boundaries-are-derived-from-a-projection.md). Four of them -
`share_root`, `scan_root`, `parent_unreadable`, `parent_null_dacl` - mean the comparison was
never made, and all four still report a boundary. **Unknown is a boundary**: one that is not
really there costs a scan one extra stored ACL, while one reported `false` tells the next scan
it may stop looking.

**`parent_acl_hash` is not the value the verdict was compared against.** That value is the
parent's *projection* onto a child, which the server recomputes from the parent's own stored
entries. What this field records is *which reading of the parent* was judged - without it, a
later disagreement between collector and server cannot be told apart from the parent simply
having been changed in between.

Two rules bind a collector that sends these:

* derive `is_acl_boundary` **from** the reason rather than sending them as two independent
  fields. A boundary and the evidence for it cannot then disagree, which is the failure this
  minor exists to prevent;
* compare against the parent's projection, never against the parent's own `acl_hash`. Windows
  sets the `INHERITED` bit on every entry it copies down, so a perfectly inheriting child has
  a different digest from its parent - and that comparison reports every directory in the
  estate as a boundary.

The server stores the claim as sent and reports its own derivation beside it on
`GET /resources/{path}`. Neither overrides the other: they disagree when the parent changed
between the two readings, when the collector's projection is wrong, or when entries were lost
in transit, and picking a winner would bury all three.

### 1.4 (Phase 7B)

Three additions, all optional and all additive: a `1.0` through `1.3` payload that omits them
is still valid, and every `1.x` server accepts all five minors.

| Envelope | Field | Carries |
| --- | --- | --- |
| start | `mode` | `full`, `delta`, or `reconcile` |
| start | `job` | The scheduled job this run belongs to |
| start | `baseline` | The checkpoint a delta resumed from |
| batch | `affirmations` | Objects re-read and found unchanged |
| batch | `checkpoint` | The cursor covering everything in and before this batch |
| completion | `affirmation_count` | How many affirmations the run sent |
| completion | `checkpoint` | The cursor the next delta may resume from |

Full treatment: [`docs/architecture/incremental-collection.md`](../architecture/incremental-collection.md),
decided in [ADR-0025](../decisions/0025-incremental-collection-is-bounded-by-its-source.md)
and [ADR-0026](../decisions/0026-an-affirmation-is-verified-and-a-checkpoint-trails-its-data.md).

#### `mode` says why a run is or is not incremental

`incremental` remains the flag that decides whether a run may ever mark an object absent, and
nothing about it changes. `mode` is the finer statement layered over it, and the server
**rejects a payload where they disagree**: `mode` is `delta` if and only if `incremental` is
true. A collector that omits `mode` has it derived from `incremental`, so a `1.3` payload
setting `incremental: true` is recorded as a delta rather than as a full run.

`job` is free-form and the server never interprets it. It is the key the checkpoint store is
keyed by, so **a run that sends a checkpoint must name a job**; one that does not is rejected
with `409`.

#### A checkpoint is a cursor *and* the identity it belongs to

```json
{
  "kind": "usn",
  "token": "184987",
  "issuer": "CN=NTDS Settings,CN=DC01,...,DC=corp,DC=example,DC=com|2f0f9a3c-7c4e-4c0e-9a02-6b5f0a1f9d11",
  "issued_at": "2026-09-14T08:05:00Z"
}
```

| `kind` | `token` | Ordered? |
| --- | --- | --- |
| `usn` | a decimal integer | yes, against the same issuer only |
| `timestamp` | RFC 3339 with an offset | yes, to the second, and only as well as the clocks involved |
| `opaque` | anything the collector likes | no — the server can tell it changed, not that it moved forward |

**`issuer` is required and it is compared.** A `uSNChanged` cursor is a counter on one domain
controller, so DC1's number replayed against DC2 silently skips every object whose USN on
DC2 falls below it. For a domain controller the issuer is `dsServiceName` and `invocationId`
joined, and both halves are necessary: the first changes when the collector binds a different
DC, and the second changes when the *same* DC is restored from backup — which rolls its USN
counter backwards and makes it reissue numbers it has already handed out.

The server refuses a checkpoint it cannot show to be ahead of the one it holds — a different
issuer, a different kind, or a lower token — keeps the stored cursor, and **records the
refusal**, so the operator can see a job that is running successfully and making no progress.

Three rules bind a collector that sends one:

* **advance it only behind data that landed.** The server moves the job's cursor inside the
  transaction that applied the batch; a collector should write its own only after the server
  has accepted the batch;
* **never record one on a run that did not finish cleanly.** A partial run did not read
  everything below its watermark, so the next delta would start above exactly the objects
  this run failed on — a gap that closes only by accident, because nothing afterwards looks
  missing. The contract rejects a checkpoint on any completion that is not `succeeded` with
  zero errors, and the server additionally refuses one on a run it downgraded;
* **a delta run may still never reconcile.** A cursor-filtered query cannot report a
  deletion: a deleted object produces no entry, which is exactly what an unchanged object
  produces.

#### An affirmation is an object re-read and found unchanged

```json
{
  "kind": "ntfs_resource",
  "source_key": "resource|\\\\fs01\\finance\\reports",
  "digest": "3b1f0c9d5e2a47b8c6d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6",
  "observed_at": "2026-09-14T08:02:00Z"
}
```

It exists because the file system has **no change metadata a DACL edit reliably touches**:
writing an ACL does not move `LastWriteTime`, so a scan that skipped unchanged-looking
directories on a timestamp would skip exactly the changes ADG is for. The collector therefore
still reads every descriptor; what it sends for the unchanged ones is this.

Only `ntfs_resource` may be affirmed in 1.4 — it is the one kind with a published
whole-object digest (`acl_hash`, 1.2) that the server already recomputes from what it stores.
`digest` is exactly that value, over the whole DACL in the reading being affirmed, including
`dacl_present` and `dacl_protected`.

**The server verifies it and refuses what it cannot confirm.** Each refusal is returned in
the batch response naming the key, and the collector re-sends that object in full:

| `reason` | What it means |
| --- | --- |
| `digest_mismatch` | The ACL changed between the reading ADG holds and the one just taken. **This is the ordinary case, not an error.** |
| `unknown_object` | ADG holds no resource at that path; there is nothing to confirm |
| `no_stored_digest` | The stored reading predates 1.2 and carries no `acl_hash`, so the two cannot be compared — and *cannot compare* never resolves to *equal* |
| `absent` | ADG has recorded the object as gone. Reviving it is a claim about state, which an affirmation does not carry |

A refusal never fails the batch or the run.

Two rules bind a collector that sends one:

* **compute the digest from this scan's reading**, never from a record of what was sent last
  time. A cached digest makes the affirmation a statement about the collector's memory rather
  than about the object, and the two diverge precisely when an ACL has changed;
* **affirm only what you actually re-read.** An affirmation counts as an observation: it
  extends the object's timeline and marks it seen by this run, for the resource *and for the
  entries its digest covers*.

That last point is what makes affirmations worth having: **a run that affirmed everything it
did not re-send has still enumerated its whole scope**, so it remains a `full` run and may
reconcile. A scan of a quiet tree costs a key and a digest per directory instead of a
resource and all its entries, and gives up nothing.
