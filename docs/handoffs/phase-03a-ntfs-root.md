# Handoff — Phase 3A (`phase-03/01-ntfs-root-acl.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-02b-smb-ingestion.md](phase-02b-smb-ingestion.md)
**Contract version after this phase:** `1.2`

## Scope completed

Made the NTFS layer readable, storable, and answerable — separately from the share layer,
which is the whole point.

1. **A new collector**, `collector/powershell/ntfs/`, that reads the NTFS security
   descriptor of each configured **share root** and reports it as contract v1 observations.
   It uses the raw `RawSecurityDescriptor` rather than `Get-Acl`'s rule collection, because
   three facts the contract needs survive only there: whether a DACL is present at all, the
   raw `ACE_HEADER.AceFlags` byte, and the entries' evaluation order.
2. **A deterministic normalized ACL form and `acl_hash`** — one specification
   ([`docs/architecture/ntfs-acl-normalization.md`](../architecture/ntfs-acl-normalization.md)),
   two implementations that must agree byte for byte, and a contract test that runs the real
   collector and compares them. [ADR-0008](../decisions/0008-acl-normal-form-and-hash.md)
   records what the digest covers and, more importantly, what it deliberately does not.
3. **Contract 1.2**: `ntfs_resource` gains an optional, additive `acl_hash`.
4. **Two tables** — `ntfs_resources` and `ntfs_aces` — created by migration
   `0004_ntfs_resources`, which also widens `principal_references.reference_kind` to include
   `ntfs_ace`.
5. **The last two observation kinds accepted.** `plan_batch` now stores all seven kinds in
   contract v1; `ntfs_resource` and `ntfs_ace` are no longer a 422. The rejection path
   survives for whichever kind a later contract adds.
6. **Ingestion verifies a reported `acl_hash`** against the ACEs sent with it, when — and
   only when — the batch carries the whole DACL. A contradiction is a 422 naming both
   digests and printing the normalized document the server hashed.
7. **Each share linked to its root resource** through `ntfs_resources.share_key`, derived
   from the path rather than from anything a collector says about it.
8. **Three new endpoints**: `GET /resources/{path}`, `GET /resources/{path}/acl`, and
   `GET /shares/{share}/root-acl`. `GET /shares/{share}` now also reports `root_resource`.
9. **240 new tests** — 91 Pester, 106 hermetic Python, 43 against a real PostgreSQL.

## Files and modules added or materially changed

### Collector (new)

| File | Contents |
| --- | --- |
| `collector/powershell/ntfs/AdgNtfsCollector.psd1` / `.psm1` | Module manifest and loader. No `RequiredModules`: everything goes through `System.Security.AccessControl` and `System.IO`, so the collector does not depend on the PowerShell provider stack. |
| `functions/AdgNtfsObservation.ps1` | Pure. Path canonicalization, source keys, the ACL normal form and its SHA-256, and the observation builders. |
| `functions/AdgNtfsConfig.ps1` | Which share roots to read, and what is refused. |
| `functions/AdgNtfsSource.ps1` | The only code that touches a file system — the seam the tests mock. |
| `functions/AdgNtfsScan.ps1` | Orchestration, retries, scopes, and grouped batching. |
| `functions/AdgNtfsTransport.ps1` | Submission, with the contract's retry rules. |
| `Invoke-AdgNtfsScan.ps1` | Entry point, with `-DryRun -OutputDirectory`. |
| `adg-ntfs-targets.example.json` | Configuration template. No credential field: the collector runs as a gMSA. |
| `README.md` | Privileges it needs, and the ones it refuses to acquire. |
| `tests/*.Tests.ps1`, `tests/Export-AdgNtfsFixturePayload.ps1` | Pester suites and the fixture exporter the backend contract test drives. |

### Domain

| Module | Contents |
| --- | --- |
| `backend/app/domain/acl_hash.py` | **New.** `normalize_acl`, `acl_hash`, `AclAceFacts`, `NormalizedAcl`, `is_acl_hash`. |
| `backend/app/domain/access.py` | Adds `ntfs_ace_identity_key` as a module function, with `NtfsAce.identity_key(resource_key)` and `NtfsAce.layer` delegating to it. |
| `backend/app/contracts/v1/keys.py` | `ntfs_ace_key` now derives from the domain function rather than formatting its own string, matching what `smb_ace_key` already did. |

### Contract

| File | Contents |
| --- | --- |
| `docs/contracts/v1/common.schema.json` | Adds the `aclHash` definition. |
| `docs/contracts/v1/ntfs-resource-observation.schema.json` | Adds the optional `acl_hash`. |
| `backend/app/contracts/v1/observations.py` | `NtfsResourceObservation.acl_hash`, its validator, a new check that `server_name`/`share_name` agree with the path, and `NtfsAceObservation.unc_path`. |
| `backend/app/contracts/v1/common.py` | `SCHEMA_VERSION` `1.0` → `1.2`. It had been left at `1.0` through the 1.1 bump; it is only a default for payloads this codebase constructs, and every `1.x` is still accepted on the wire. |

### Storage and ingestion

| Module | Contents |
| --- | --- |
| `backend/app/models/schema.py` | Adds `ntfs_resources`, `ntfs_aces`, and `ReferenceKind.NTFS_ACE`. |
| `database/migrations/versions/0004_ntfs_resources.py` | **New.** Generated from that declaration; `alembic check` reports no drift and the downgrade round-trips. |
| `backend/app/ingestion/plan.py` | `NtfsResourceRow`, `NtfsAceRow`, `AclHashMismatch`, the two new planning branches, and `_verify_acl_hashes`. |
| `backend/app/ingestion/service.py` | `_write_ntfs_resources`, `_write_ntfs_aces`, and their newest-wins column sets. **`_write_references` moved out of `_write_share_aces` into `apply_batch`** — hanging it off the share-ACE path would have silently dropped every reference from an NTFS-only batch. |

### Query side

| Module | Contents |
| --- | --- |
| `backend/app/repositories/resources.py` | `NtfsResourceRecord`, `NtfsAceRecord`, `NtfsAclRecomputation`, and six queries including `get_share_root_resource` and `recompute_acl_hash`. |
| `backend/app/services/resources.py` | `ResourceDetail`, `NtfsAcl`, `ResolvedNtfsAce`, `resource_detail`, `ntfs_acl`, `share_root_acl`; `ShareDetail` gains `root_resource`. |
| `backend/app/api/resources.py` | Three endpoints, the `NtfsResourceSummary` / `NtfsAceView` / `AclHashView` models, and `_resource_key`. |

### Tooling and docs

| File | Contents |
| --- | --- |
| `.github/workflows/ci.yml` | The collector job now runs **every** suite, not only the AD one. The SMB suite had never been in CI. |
| `backend/app/validation/collector_output.py` | **Defect fixed.** `_check_unsupported_kinds` flagged every non-AD kind as unstorable, ignoring `SUPPORTED_KINDS` except in the message — so it raised an error naming the very kinds it had just listed as supported. Wrong since Phase 2B; Phase 3A made it loud. |
| `scripts/validate-collector-output.ps1` | **Defect fixed.** The report renders `→`; a cp1252 console could not encode it, so the tool died in `print()` and reported nothing at all. Now sets `PYTHONIOENCODING=utf-8`. |
| `docs/architecture/ntfs-acl-normalization.md` | **New.** The normative format. |
| `docs/decisions/0008-acl-normal-form-and-hash.md` | **New.** |
| `docs/architecture/resource-inventory.md`, `docs/contracts/collector-protocol.md`, `README.md`, `collector/README.md` | Extended for the second layer. |
| `backend/tests/fixtures/ad_graph/*.json` | Regenerated: they carry `schema_version` from `SCHEMA_VERSION`, which moved to `1.2`. Content is otherwise byte-identical. |

## Important architecture decisions

### The two layers are never merged, anywhere

Separate observations, separate tables, separate record types, separate services, separate
routes, separate `kind` discriminators. `GET /shares/{share}/acl` is what the SMB server
grants; `GET /shares/{share}/root-acl` is what the file system grants on the directory that
share publishes. A single merged response could not express "the share grants Full Control
over a directory that grants Read", which is exactly what an auditor needs to see — and it
would quietly become an effective-access claim wearing a raw-facts label.

A share whose NTFS root nothing has read reports `root_resource: null` and a 404 that says
so. **Null means nobody has looked, never nothing restricts it.**

### The ACL hash: order is in, the owner is out

Full reasoning in ADR-0008; the two rulings that will surprise a reader:

* **Evaluation order is part of the digest**, as a *rank* rather than as the raw
  `order_index`. A Deny moved below an Allow grants access that was previously refused. This
  does not contradict the rule that `order_index` is not part of an *ACE's* identity: an ACE
  keeps its identity when it moves, and the *ACL* is reported as changed.
* **The owner is excluded.** Every folder under a root is owned by whoever created it while
  sharing one inherited DACL; an owner-sensitive digest would mark every one of them a
  boundary, which is the opposite of what a boundary is for. Ownership is stored and
  reported separately — and Phase 7's change detection must therefore watch both.

### The digest is checked, not trusted — and reported twice

The collector computes it over the whole DACL it read. Ingestion recomputes it from the ACEs
in the same batch **only when the batch carries exactly the declared `ace_count`**: anything
less is a partial view, which normalizes differently by construction, and treating that as a
mismatch would reject a collector that did nothing wrong but split its batches. The NTFS
collector's batcher never splits a directory, so the check runs on every batch it sends.

The API reports `reported` and `computed` side by side with an `agrees` verdict, always both.
A disagreement means the stored entries are not the ones that were hashed — entries lost in
transit, a rejected batch, two collectors describing one path differently — and every one of
those is a coverage gap. Choosing a winner would bury it. `ace_count_agrees` reports the same
question for the entry count.

### A share-root run reconciles nothing, ever

The contract's file-system scope kind, `directory_tree`, claims the whole tree beneath a path
was enumerated. A run that reads roots and nothing else has enumerated no tree, so
reconciling it would mark **every directory under every root as deleted**. Every run this
collector produces is therefore marked `incremental`, which the server refuses to let
reconcile — including a clean, error-free one. The `directory_tree` scopes are still declared,
because they state what the run set out to look at and are what a later full walk will
reconcile.

This is the precedent Phase 1A set for an OU-narrowed AD run, applied to a narrower question.

### A share root is always an ACL boundary

Its parent lies outside the share, often outside anything ADG audits, so there is nothing to
compare it against. `is_acl_boundary: false` would tell the tree walk it could skip the one
directory every path through that share must pass.

### The collector omits `acl_hash` rather than hashing part of a DACL

An entry type the contract cannot express, or a trustee with no usable SID, makes the reading
incomplete. A digest over part of a DACL is indistinguishable from a digest of all of it, and
comparing one to a parent's would answer the boundary question wrong *without ever looking
wrong*. The entry becomes a `collectorError`, the run goes `partial`, and no digest is sent.

### `inherited_from` is never reported

Naming the ancestor an inherited entry came from needs the Win32 `GetInheritanceSource`,
which this collector does not call. The `INHERITED` bit already says an entry came from above;
the tree walk, which reads the ancestors, can say which one. The column and the contract field
exist and stay null rather than carrying a guess.

## Schemas and contracts introduced or changed

**Contract 1.2, additive.** `ntfs_resource.acl_hash`: SHA-256, lower-case hex, over the
normalized DACL. A `1.0` or `1.1` payload that omits it is still valid, and every `1.x`
server accepts both.

**New protocol rules** (`docs/contracts/collector-protocol.md`):

* §5 — keep a directory's `ntfs_resource` and its `ntfs_ace` observations in **one batch**,
  because the server's hash check skips a partial DACL;
* §6 — a scope must never claim more than the run read; a root-only file-system run marks
  itself `incremental` and reconciles nothing;
* §10 — the 1.2 section, with the three rules binding a collector that sends a digest.

**New model validation.** An `ntfs_resource` whose `server_name` or `share_name` contradicts
its own `path` is rejected. The path is the identity; letting them disagree would report one
share's NTFS root under another share's name.

**Storage:** `ntfs_resources` (keyed by the case-folded canonical UNC path) and `ntfs_aces`
(keyed by resource, trustee, type, mask, and the raw flags byte). No foreign keys between the
resource tables, matching `0003_smb_resources`. Check constraints encode the invariants: a
NULL DACL carries no ACEs, blocked inheritance implies a boundary, `source` agrees with bit
`0x10` of the flags byte, an origin is recorded only for an inherited entry, and `acl_hash`
matches `^[0-9a-f]{64}$`.

## Tests run and exact results

All run on 2026-09-14 against this tree.

| Gate | Command | Result |
| --- | --- | --- |
| Backend lint | `.\scripts\backend-lint.ps1` | **passed** — ruff check clean, 116 files formatted, mypy strict clean on 115 source files |
| Backend hermetic | `pytest tests -m "not smoke"` | **3314 passed, 9 skipped**, 235 deselected |
| Backend smoke | `pytest tests -m smoke` (PostgreSQL) | **235 passed** |
| Collector | `.\scripts\collector-test.ps1` | **336 passed, 0 failed** |
| Frontend | `.\scripts\frontend-check.ps1` | **passed** — lint, typecheck, unit tests, build |
| Migration | `alembic upgrade head` / `check` / `downgrade -1` / `upgrade head` / `check` | applied, **no drift**, downgrade round-trips |
| Collector output | `.\scripts\validate-collector-output.ps1 -Path <fixture>` | **no findings** on the collector's real output |

The smoke suite ran against `adg_phase3a_test` (`ADG_DATABASE_URL=...:5432/adg_phase3a`), not
the developer default, because two sessions share this workstation and the autouse
`clean_tables` fixture truncates everything.

### New tests

| Suite | Count | Covers |
| --- | --- | --- |
| `collector/powershell/ntfs/tests/AdgNtfsObservation.Tests.ps1` | 44 | Path canonicalization and refusals, source keys, explicit vs inherited, Deny position, generic masks, INHERIT_ONLY, unresolved trustees, unclassifiable ACE types, NULL vs empty DACL, boundary rules, and thirteen hash properties including the literal normal form |
| `…/AdgNtfsConfig.Tests.ps1` | 16 | Target merging and canonicalization, the share-root-only refusal, range checks, and the shipped example file |
| `…/AdgNtfsScan.Tests.ps1` | 31 | Ordinary root, protected DACL, NULL DACL, unreadable descriptor, absent path, orphaned trustee, unclassifiable entry, batching, and multi-root runs |
| `backend/tests/domain/test_acl_hash.py` | 36 | Stability *and* sensitivity: five things that must not change the digest and eight that must, plus every refusal |
| `backend/tests/ingestion/test_plan_ntfs.py` | 34 | Keys, the share link, trustee scoping, references, and ten `acl_hash` verification cases |
| `backend/tests/contracts/test_ntfs_collector.py` | 36 | Runs the real collector; validates against the schemas, the models, and the planner; **and recomputes every `acl_hash` in Python** |
| `backend/tests/db/test_ntfs_resources.py` | 43 | Ingestion, both layers side by side, the recomputed digest, NULL vs empty DACL, ACL order and paging, orphaned trustees, and a share whose NTFS layer nobody has read |

### Tests changed rather than added

Four tests asserted that NTFS payloads were *rejected*. That was correct until this phase and
is now false, so each was rewritten to assert the stronger property it was standing in for —
that nothing in a published transcript is dropped, and that the guard still refuses a kind
outside `SUPPORTED_KINDS`:

* `tests/ingestion/test_plan.py::TestRejection`
* `tests/ingestion/test_plan_resources.py::TestWhatTheEndpointStores` (renamed)
* `tests/db/test_ingestion.py::TestEveryContractKind` (renamed)
* `tests/db/test_smb_ingestion.py::test_a_batch_carrying_both_layers_is_accepted`
* `tests/validation/test_collector_output.py` — split into "a storable transcript raises
  nothing" and a direct unit test of the guard

### One cross-language check worth naming

`test_ntfs_collector.py` runs the PowerShell collector over a fake estate and recomputes
every `acl_hash` with `app.domain.normalize_acl`. The digests match over a DACL containing a
Deny ahead of two Allows, an inherited entry, an INHERIT_ONLY entry, a generic-rights mask,
and an orphaned SID. A divergence between the two implementations would not fail loudly on
its own — it would show up as a permanent, unexplainable disagreement on every directory in
the estate — which is exactly why it is pinned here.

## Known limitations

1. **Share roots only.** A path inside a share is refused by `Import-AdgNtfsTarget` with a
   message naming the root to configure instead. The backend stores any depth — the
   scenarios exercise `\\FS01\Finance\Payroll` — so this is a collector-side restriction, not
   a storage one. Phase 3B lifts it.
2. **No tree walk, and therefore no reconciliation.** Every run is `incremental`. Nothing ADG
   holds can yet be marked absent on the evidence of a file-system scan.
3. **`is_acl_boundary` for a non-root directory is whatever the collector claims.** This
   collector only ever reports roots, where the value is `true` by the rule above. When the
   tree walk lands, the value must be established by comparing against the parent, and the
   backend does not currently check that claim.
4. **`inherited_from` is never populated** (see above).
5. **The SACL is never read.** Audit entries govern logging, not access, and reading them
   needs `SeSecurityPrivilege`. Deliberate, and it means ADG cannot report what is audited.
6. **`local_path` is not reported** for an NTFS resource. The collector reads over UNC and
   does not know the server-side path; the SMB collector knows the share's `Path` but the two
   are not correlated. The column exists and stays null rather than carrying a guess.
7. **The real acquisition layer has never run against a real file server.** Every test mocks
   `AdgNtfsSource.ps1`. The same limitation the AD collector's LDAP provider has.
   `FileSystemAclExtensions` availability and the `RawSecurityDescriptor` round trip are
   untested outside a workstation.
8. **A directory's entry rows are never removed**, so a resource that flips to
   `dacl_present: false` keeps the ACEs a previous run stored. `recompute_acl_hash` excludes
   them from the document — a NULL DACL carries none by definition — and `ace_count_agrees`
   goes false, which surfaces the split. Cleaning it up belongs with Phase 7.
9. **`ntfs_aces` has no "which directories name this trustee" endpoint.** The reference rows
   are written and indexed; only `principals/{trustee}/shares` exists as a route. Not
   required by this phase.
10. **The open BUILTIN hazard from earlier phases is unchanged and now wider.** Only
    `local_group` principals and `local_group_member` edges are host-scoped, so two collected
    domains still merge their `BUILTIN\Administrators`. NTFS ACEs now reference those keys
    too. The validator warns `unscoped_well_known_group`; fixing it changes
    `Principal.identity_key` and every collector's `source_key`.

## Security and privilege assumptions

**Domain Admin is not required and must not be used.**

| To read | The account needs |
| --- | --- |
| A directory's DACL, owner, and control flags | `READ_CONTROL` on the directory, and traverse on the path to it |
| A share root over SMB | Enough share-level access to open the share; `Read` suffices |
| Nothing else | No write, no `WRITE_DAC`, no `WRITE_OWNER`, no `SeTakeOwnershipPrivilege`, no `SeBackupPrivilege`, no `SeSecurityPrivilege` |

`READ_CONTROL` comes implicitly with any of `Read`, `Modify`, or `Full Control`, so an account
that can read the data can already read the ACL. **No administrative rights on the file server
are needed** — a stronger position than the SMB collector, which needs local Administrators to
read a share security descriptor.

**The collector does not escalate when a read is denied.** `SeBackupPrivilege` would let it
bypass the DACL; `WRITE_OWNER` would let it take ownership and grant itself `READ_CONTROL`.
Both are refused by design: an auditing tool that can read what its own credentials are not
permitted to read is measuring something other than the estate's real permissions, and it is a
standing escalation path in an account that runs unattended. An unreadable descriptor becomes
an `access_denied` error, the run goes `partial`, and **no resource observation is emitted at
all** — a resource row with no ACEs would read as "nobody has access".

**No secrets in source control.** `adg-ntfs-targets.example.json` has no credential field;
the collector runs as the process identity, a gMSA in production.

**Read-only throughout** (ADR-0004). Nothing writes to a target, enables a privilege, takes
ownership, or modifies a descriptor to make a read succeed.

## Migration and compatibility notes

* **`0004_ntfs_resources` is additive.** Two new tables, plus a widened check constraint on
  `principal_references.reference_kind` — every value the old constraint allowed is still
  allowed, so no existing row can be invalidated. Applied, `alembic check` clean, downgrade
  round-trips. The downgrade deletes `ntfs_ace` reference rows before narrowing the
  constraint; they describe the tables it is about to drop.
* **Contract 1.2 is additive.** Collectors sending `1.0` or `1.1` are unaffected. A `1.1`
  SMB collector and a `1.2` NTFS collector can submit to the same server concurrently.
* **`SCHEMA_VERSION` moved `1.0` → `1.2`.** It is only the default for payloads this codebase
  constructs; the wire still accepts any `1.x`. The visible consequence is that the generated
  AD-graph fixtures were regenerated (`python -m tests.fixtures.build_ad_graph`) — a
  version-string change only. **Edit the generator, never the JSON**; a test compares them
  byte for byte.
* **Two behaviour changes that are not additive**, both intended:
  * `plan_batch` no longer rejects the NTFS kinds. A caller that relied on a 422 is now
    accepted. `tests/support/ingest.py::storable()` still subsets, so a kind added to the
    contract ahead of its ingestion support is dropped there rather than failing every test.
  * `_write_references` moved from `_write_share_aces` to `apply_batch`. Nothing observable
    changes for an SMB-only batch; an NTFS-only batch now writes its references, which it
    would not have.
* **No API response field was removed or renamed.** `ShareDetailView` gains
  `root_resource`, which is nullable.

## Prerequisites for the next prompt

**Phase 3B (the recursive tree scan) needs, in order:**

1. **A boundary rule that is established rather than claimed.** The collector currently sets
   `is_acl_boundary: true` for a root by fiat. For a subdirectory it must be derived by
   comparing the child's `acl_hash` to the parent's — which is what the hash exists for — and
   the backend should validate the claim rather than storing it unread.
2. **Streaming.** `Invoke-AdgNtfsScanRun` holds the whole run in memory, which is comfortable
   at root granularity and will not be for a tree. The batcher's grouping contract (never
   split a directory) must survive that rewrite, or the server's hash check silently stops
   running.
3. **A scope it can honestly reconcile.** Once a walk enumerates a tree, `directory_tree`
   becomes claimable and the run stops being `incremental` — at which point Phase 7's absence
   rules apply to directories for the first time. Get the reconciliation semantics reviewed
   before flipping that flag.
4. **`GetInheritanceSource`, or a derivation.** With ancestors in hand, `inherited_from` can
   be populated — by P/Invoke, or by matching a child's inherited entries against the
   parent's inheritable ones. The second needs the inheritance algebra Phase 4B owes.
5. **A `principals/{trustee}/resources` route**, symmetrical with the share one. The
   reference rows and indexes are already there.

**Phase 4B (effective access) can now assume:** both raw layers are stored and separately
addressable, `NtfsAce` carries the raw mask and the raw flags byte with nothing expanded, and
`app.domain.access` has the layer-tagged `RightsMask` algebra from Phase 4A. It still owes
DACL-order evaluation, owner implicit rights, `CREATOR OWNER`, inheritance, and NULL-DACL.

**Anyone touching the ACL hash** must read
[`ntfs-acl-normalization.md`](../architecture/ntfs-acl-normalization.md) §7 first. Any change
to the document's bytes invalidates every digest ADG has stored and is a new version token,
not an edit — and both implementations must move together, which the contract test enforces.

## `git status --short`

Taken after the commit, so only another session's in-flight work remains:

```text
 M backend/tests/contracts/test_smb_collector.py
```

That file was already modified when this phase started — a formatting-only change by a
concurrent session — and was deliberately excluded from this phase's commit.
