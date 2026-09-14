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
* **No duplicate `source_key` within a batch.** Two observations with the same key are
  indistinguishable; the payload is rejected rather than half-applied.
* **`is_final`** is advisory. Only the completion envelope ends a run.
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

Marking absent is not deletion: history is retained (Phase 7), and the object is recorded as
no longer observed as of this run.

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
