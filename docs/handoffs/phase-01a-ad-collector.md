# Handoff — Phase 1A (`phase-01/01-ad-collector.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-00b-contracts.md](phase-00b-contracts.md)
**Documentation:** [docs/collectors/ad.md](../collectors/ad.md)

## Scope completed

A least-privilege, read-only Active Directory collector that reports principals and their
**direct** membership edges as contract v1 observations, plus the shared PowerShell
primitives every later collector will build on.

1. **`AdgCollector.Common`** — the PowerShell mirror of `backend/app/contracts/v1/`:
   payload construction, source-key derivation, batching, retrying transport, and an
   offline sink.
2. **`AdgCollector.ActiveDirectory`** — a layered collector: a *directory provider* owns
   the LDAP connection, *translation* functions turn one raw entry into one observation and
   are pure, and *collection* walks the directory and hands payloads to a publisher.
3. **`Invoke-AdgAdCollector.ps1`** — the entry point, configurable by file and by command
   line, with online and offline modes.
4. **A fixture directory provider** that emulates the parts of LDAP the collector depends
   on — subtree and base scope, the filters it issues, ranged `member` retrieval, and
   objects the caller cannot read — so the whole collector runs end to end without a domain.
5. **144 Pester tests** plus **13 backend contract tests** that run the real collector
   offline and validate its output against the published JSON Schemas and the backend
   models.
6. **`docs/collectors/ad.md`** — privileges, configuration, operational examples, and what
   is deliberately not covered.

## Files and modules added

### Collector (`collector/powershell/`)

| File | Lines | Contents |
| --- | --- | --- |
| `common/AdgCollector.Common.psm1` | 829 | `Get-AdgTimestamp`, `New-AdgIdentifier`, `Assert-Adg{Uuid,Sid,HostName}`, `Get-Adg{Principal,Membership}SourceKey`, `New-Adg{Observation,PrincipalObservation,MembershipObservation,Scope,ScanRunStart,ObservationBatch,CollectorError,ScanRunCompletion,Publisher}`, `Test-AdgRetryableStatus`, `Invoke-AdgApiRequest`, `Publish-AdgPayload`. |
| `common/AdgCollector.Common.psd1` | — | Manifest. |
| `ad/AdgCollector.ActiveDirectory.psm1` | 2006 | DN handling, attribute reading, classification, translation, the LDAP and fixture providers, ranged membership, configuration, collection, and the change watermark. |
| `ad/AdgCollector.ActiveDirectory.psd1` | — | Manifest. |
| `ad/Invoke-AdgAdCollector.ps1` | 219 | Entry point. Exit `0` succeeded, `2` partial, `1` failed. |
| `ad/config/adg-ad-collector.example.json` | — | Configuration template. Contains no secret and never will. |
| `tests/AdgCollector.Common.Tests.ps1` | 355 | Source keys, observation validation, batching limits, completion rules, retry classification, transport, offline publishing. |
| `tests/AdgCollector.Ad.Translation.Tests.ps1` | 450 | DN normalization and containment, attribute reading, classification, entry→observation translation, configuration loading, transient-failure classification, watermark usability. |
| `tests/AdgCollector.Ad.Collection.Tests.ps1` | 603 | End-to-end collection: no flattening, primary groups, ranged membership, foreign security principals, scope and reconciliation, batching, failure reporting, rename recovery, transient retry, the watermark. |
| `tests/fixtures/corp-domain.json` | — | 26-entry synthetic directory. All data is invented; no domain-captured data is ever committed. |

### Backend (`backend/tests/contracts/`)

| File | Contents |
| --- | --- |
| `test_ad_collector_offline.py` | Runs `Invoke-AdgAdCollector.ps1 -Offline -DirectoryFixture` and validates every payload against the v1 schemas and through the pydantic models, which re-derive each `source_key`. |

### Documentation and CI

`docs/collectors/ad.md` (new), `.github/workflows/ci.yml` (added a `collector` job on
`windows-latest`).

### Not added

`scripts/collector-test.ps1` already existed — written by a concurrent session — and
already runs everything under `collector/`. It was left untouched.

## Important architecture decisions

1. **`System.DirectoryServices.Protocols`, not the RSAT `ActiveDirectory` module.** It is
   present on any Windows host with .NET so the collector needs no Windows feature
   installed; it exposes paging and ranged retrieval explicitly instead of hiding them; and
   it surfaces LDAP result codes, which is what lets the collector tell a permissions
   failure from an outage rather than guessing. `Get-ADGroup -Recursive` would also have
   made flattening the path of least resistance.

2. **A directory *provider* is the seam.** Everything except the socket is testable without
   a domain: a provider is a hashtable carrying a `SearchCommand`, and the Pester suite
   substitutes a fixture-backed one. This is what makes the no-flattening guarantee an
   assertion rather than a claim.

3. **Two passes, not one.** Pass 1 enumerates principals (without `member`) and builds a
   DN→SID index; pass 2 enumerates groups with `member` and resolves each reference against
   that index. The alternative — one pass carrying `member` — loads member arrays for every
   user in the domain, and still needs the index.

4. **Membership resolution never invents an edge.** A member DN resolves via the FSP naming
   convention, then the index, then a direct read. A miss is reported, not guessed.

5. **A narrowed run is an incremental run.** `ScopeKind` has no `organizational_unit`, and
   inventing one would have been a contract change. A run limited by include or exclude OUs
   declares the `domain` scope with `incremental: true`, which the contract already makes
   structurally incapable of reconciling. That is exactly the intended meaning.

6. **An exclusion limits enumeration, not membership.** A principal inside an excluded
   container is still recorded when an in-scope group grants it membership: an edge
   pointing at a SID with no principal behind it cannot be audited, and silently omitting a
   principal that holds access is the failure mode ADG exists to prevent.

7. **Change metadata lives in a collector state file, not on observations.** Contract v1
   has no field for `uSNChanged` and forbids extra ones. The watermark is written only by a
   clean, complete, non-incremental run, and records which directory server issued it —
   `uSNChanged` is a per-server counter.

8. **Truncation is an error, never a result.** A ranged `member` read that stops making
   progress throws; a search that hits the server's size limit throws. Both would otherwise
   look exactly like a smaller directory.

9. **Validation happens in the collector, not only at the API.** The API would reject a
   malformed payload with a 422 anyway; failing locally names the offending field while the
   collector still knows which object it came from.

## Schemas and contracts introduced or changed

**None.** Contract v1 is consumed unchanged. The collector emits only `principal` and
`membership_edge` observations, plus the three envelopes, all exactly as published in
`docs/contracts/v1/`.

Source-key derivations are mirrored in PowerShell (`Get-AdgPrincipalSourceKey`,
`Get-AdgMembershipSourceKey`) and pinned to the normative Python ones by
`test_ad_collector_offline.py`, which parses real collector output through the backend
models and lets them re-derive every key.

One new local artifact, not part of the ingestion contract: the collector state file,
`schema: "adg-ad-collector-state/1"`, documented in `docs/collectors/ad.md` §8.

## Tests run and exact results

| Command | Result |
| --- | --- |
| `.\scripts\collector-test.ps1 -Path collector\powershell\tests` | **144 passed, 0 failed, 0 skipped, of 144** |
| `pytest tests/contracts/test_ad_collector_offline.py -q` | **13 passed** in 0.82s |
| `pytest tests/contracts -q` | **388 passed, 1 skipped** in 2.51s |
| `.\scripts\backend-test.ps1` (`pytest -q -m "not smoke"`) | **847 passed, 1 skipped, 27 deselected** in 7.68s |
| `ruff check` / `ruff format --check` / `mypy` on `tests/contracts/test_ad_collector_offline.py` | **All checks passed** / **1 file already formatted** / **Success: no issues found in 1 source file** |
| PowerShell AST parse of all six new `.ps1`/`.psm1` files | **0 parse errors** |
| `Invoke-AdgAdCollector.ps1 -Offline -DirectoryFixture ...` | **exit 0**, `succeeded`, 25 principals, 33 edges, 1 batch, 0 errors, reconciled |
| Same with an exclusion and `-BatchSize 7` | **exit 0**, `succeeded`, 24 principals, 32 edges, 8 batches, **not** reconciled |

`.\scripts\backend-lint.ps1` (repo-wide) **fails**: `mypy --strict` reports 23
`no-untyped-def` errors in `backend/tests/db/test_ingestion.py`. That file belongs to a
concurrent session's in-progress work and was not touched here; every file this phase added
passes the same gate when run scoped.

### Defects found by running the code, not by inspecting it

1. **`0xFFFFFFFF` is `Int32 -1` in PowerShell.** The mask that converts a signed
   `groupType` back to its unsigned value was therefore a no-op, and every group would have
   been classified `unknown`/`unknown` instead of, say, global/security. Fixed with
   `0xFFFFFFFFL`; the test that caught it asserts `-2147483646` reads back as `2147483650`.

2. **`.GetNewClosure()` rebinds a scriptblock into a new dynamic module**, where
   module-private functions are invisible. Both providers build their `SearchCommand` that
   way, so the LDAP provider would have failed at its first search against a real DC with
   "`ConvertFrom-AdgLdapEntry` is not recognized". The five functions called from inside a
   closure are now exported, with a comment saying why.

3. **A one-element array unrolls on return**, and `Set-StrictMode -Version Latest` then
   rejects `.Count` on the bare value. This affected every call site that counted search
   results — including the paths that decide whether a member was found. Wrapped in `@()`.

4. **The watermark guard did not check `incremental`.** Found by running a real
   exclusion-scoped collection and reading the state file it wrote. An incremental run
   succeeds, so it wrote a watermark whose `uSNChanged` sits above objects it deliberately
   never read; a later run starting there would have skipped them permanently.
   `Test-AdgAdCollectorStateUsable` now refuses it, with a test.

5. **The published `sid` schema pattern is laxer than the domain model.**
   `common.schema.json` allows sub-authorities of `[0-9]{1,10}` — up to `9999999999` —
   while `app.domain.Sid` rejects anything above 32 bits. A collector can therefore send a
   SID the schema accepts and the model refuses. Found because the test fixture used such a
   SID and passed schema validation before failing model validation. **Not fixed here**:
   `docs/contracts/v1/` is an accepted Phase 0B contract and a concurrent session was
   editing it during this phase. The fix is to narrow the `sid` pattern's sub-authority
   group to `(0|[1-9][0-9]{0,9})` with a `4294967295` upper bound expressed the way the
   `uncPath` patterns were tightened in Phase 0B. See "Prerequisites" below.

## Known limitations

1. **`New-AdgLdapDirectoryProvider` has never been run against a domain controller.** No
   domain was reachable from this workstation. The provider is deliberately thin for that
   reason — it opens the connection, pages, and normalizes entries — and everything above it
   is exercised through the fixture provider. It must be validated against a real DC before
   the collector is trusted in an environment. The binding, the paged search, referral
   handling, and the byte-array `objectSid` conversion are all unverified.

2. **The fixture provider is a test double, not an LDAP server.** It understands the
   handful of filters this collector issues and throws on anything else rather than quietly
   matching everything. A future collector that issues a different filter must extend it.

3. **Incremental collection is not implemented.** The watermark is captured and guarded;
   the `(uSNChanged>=N)` query that would consume it is not written. A run today always
   reads everything in its scope.

4. **Deleted objects are only seen where normally discoverable.** The collector does not set
   the show-deleted control, so it does not enumerate the Deleted Objects container. A
   member DN pointing into it resolves as unreadable and is reported as an error.

5. **`unresolved_reason` is always `unknown` for an unclassifiable object.** The richer
   reasons (`deleted`, `untrusted_domain`, `lookup_failed`) are supported by the contract
   helper but the AD collector does not yet distinguish them, because doing so honestly
   needs the failure's LDAP result code threaded through the resolver.

6. **Local groups are out of scope.** `local_group` principals and `local_group_member`
   edges are a per-host collection (scope kind `local_groups_host`), not a domain one. The
   source-key helpers handle them; no collector emits them yet.

7. **Cross-domain and cross-forest membership is recorded only as far as this domain knows
   it.** A foreign security principal contributes its SID and nothing else; ADG will show
   an unresolved principal until a collector runs in the trusted domain.

8. **One run, one directory server.** Concurrency across DCs, and the "do not reconcile the
   same scope concurrently" rule from the protocol, are left to the operator.

9. **Rename recovery re-reads once.** If an object is renamed twice during a single scan, or
   between the re-read and the retry, the reference is reported as lost rather than chased
   indefinitely.

## Security and privilege assumptions

- **Domain Admin is never required.** Normal collection needs read access to the directory,
  which `Authenticated Users` has by default for every attribute the collector requests.
  `docs/collectors/ad.md` §3 lists those attributes, recommends a dedicated `Domain Users`
  account or gMSA with `Deny log on locally`, and says explicitly what not to grant.
- **Read-only, enforced by absence.** There is no add, modify, delete, or move anywhere in
  the collector, and contract v1 has no payload that could express a permission change. A
  container the account cannot read produces an error and a `partial` run — never an
  attempt to take ownership or change a descriptor (ADR-0004).
- **Under-reporting coverage is structurally prevented.** Any error costs the run its
  `succeeded` status and its ability to reconcile; a narrowed run is marked incremental and
  cannot reconcile at all; a partial or incremental run writes no watermark.
- **No secret is stored in configuration.** The bearer token is read at run time from the
  environment variable the configuration names, and a configuration whose `apiBaseUrl`
  carries something that looks like a credential is rejected. Only
  `adg-ad-collector.example.json` is committed.
- **Transport.** LDAP is signed and sealed by default, or LDAPS with `useTls`. A plain-HTTP
  `apiBaseUrl` that is not localhost produces a warning: collector payloads name every
  principal in the domain, which in transit is a map of what to attack.
- **All fixture data is synthetic.** The fixture header says so, and domain-captured data
  must never be committed.

## Migration and compatibility notes

- Additive only. No existing file's meaning changed; the only modified tracked file is
  `.github/workflows/ci.yml`.
- **New CI job:** `collector` on `windows-latest`, installing Pester 5.5+ and running
  `scripts/collector-test.ps1 -Path collector/powershell/tests`. It is scoped to this
  collector's tests so that another collector's in-progress suite cannot turn CI red; widen
  the path as each further collector lands its tests.
- **New local prerequisite:** Pester 5 (`Install-Module Pester -MinimumVersion 5.5.0 -Scope CurrentUser`).
  `scripts/collector-test.ps1` throws with that command if it is missing.
- PowerShell sources are pure ASCII. A BOM-less UTF-8 `.psm1` containing non-ASCII is
  misread by Windows PowerShell 5.1, and the existing Phase 0B example is ASCII too.
- The `AdgCollector.ActiveDirectory` module imports `AdgCollector.Common` **without**
  `-Force`: forcing a reload of the shared module there unloads the copy the caller
  imported, and its functions vanish from the caller's session mid-run.

## Prerequisites for the next prompt

1. **The ingestion endpoints do not exist yet.** `POST /api/v1/scan-runs`, `/batches`, and
   `/completion` are still specified-not-implemented as of Phase 0B. The collector's API
   mode has therefore never been exercised against a server — only offline mode has. Whoever
   implements ingestion should replay this collector's offline output as a test.
2. **Validate the LDAP provider against a real domain controller** before the collector is
   trusted anywhere. See limitation 1 for exactly what is unverified.
3. **Narrow the `sid` pattern in `docs/contracts/v1/common.schema.json`** so the schema
   stops accepting sub-authorities the domain model rejects (defect 5 above). It is a
   tightening, not a semantic change, and belongs in whichever phase next owns the contracts
   directory.
4. **Reuse `AdgCollector.Common`.** The SMB, NTFS, and local-group collectors need the same
   envelopes, batching, retry, and offline sink. Add each new kind's source-key function
   there and pin it with an offline-output test the way
   `test_ad_collector_offline.py` does, rather than re-deriving keys per collector.
5. **Local groups (`local_groups_host` scope)** are the natural companion to this phase:
   `Get-AdgPrincipalSourceKey` and `Get-AdgMembershipSourceKey` already implement the
   host-scoping rules, and `AdgCollector.Common.Tests.ps1` already pins them.
6. Run both gates before reporting: `.\scripts\collector-test.ps1` and
   `.\scripts\backend-test.ps1`.

## `git status --short`

Captured immediately before the phase commit, filtered to the paths this phase owns —
two other sessions were working in the same tree, so their files are excluded here and were
excluded from the commit:

```text
 M .github/workflows/ci.yml
?? backend/tests/contracts/test_ad_collector_offline.py
?? collector/powershell/ad/
?? collector/powershell/common/
?? collector/powershell/tests/
?? docs/collectors/
?? docs/handoffs/phase-01a-ad-collector.md
```

The unfiltered `git status --short` additionally lists another session's Phase 4A rights
model and SMB collector work (`backend/app/access_engine/`, `backend/app/ingestion/`,
`backend/app/repositories/`, `backend/app/services/`, `collector/powershell/smb/`,
`database/migrations/versions/0002_ad_graph.py`, and several modified `docs/` files). None
of it was staged or committed by this phase.
