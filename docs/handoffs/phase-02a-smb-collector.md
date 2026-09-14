# Phase 2A — Windows SMB share collector

**Status:** complete
**Date:** 2026-09-14
**Prompt:** phase-02/01 — Windows SMB Share Collector

---

## Scope completed

A read-only Windows collector that enumerates configured file servers' shares and raw
share-level ACLs and reports them as contract v1 observations, with no effective-access
derivation anywhere.

- native discovery of servers, shares, and share ACLs over CIM;
- explicit target configuration, with include/exclude rules for administrative, hidden,
  and non-disk shares;
- raw share rights preserved in whichever form the reading API provided — access mask or
  permission level, never both;
- per-server retry and bounded timeouts, so one dead server costs seconds rather than the
  scan;
- an unreachable or unreadable target reported as *incomplete*, never as empty;
- 101 Pester tests and 23 Python contract tests, all passing.

### Out of scope, deliberately

NTFS directory ACLs (Phase 2B), local group membership, and any combination of the share
and NTFS layers. Share and NTFS permissions are separate layers and are kept separate.

---

## Files added or materially changed

### Added

| Path | Role |
| --- | --- |
| `collector/powershell/smb/AdgSmbCollector.psd1` / `.psm1` | Module manifest and root |
| `collector/powershell/smb/functions/AdgSmbConfig.ps1` | Targets and the include/exclude policy |
| `collector/powershell/smb/functions/AdgSmbSource.ps1` | The only code that touches a remote host — the test seam |
| `collector/powershell/smb/functions/AdgSmbObservation.ps1` | Raw readings to contract observations (pure) |
| `collector/powershell/smb/functions/AdgSmbScan.ps1` | Orchestration, retries, scopes, batching |
| `collector/powershell/smb/functions/AdgSmbTransport.ps1` | Submission with the contract's retry rules |
| `collector/powershell/smb/Invoke-AdgSmbScan.ps1` | Entry point |
| `collector/powershell/smb/adg-smb-targets.example.json` | Configuration template (no secrets) |
| `collector/powershell/smb/README.md` | Privileges, firewall, remote-management prerequisites |
| `collector/powershell/smb/tests/AdgSmbConfig.Tests.ps1` | Configuration and filtering |
| `collector/powershell/smb/tests/AdgSmbObservation.Tests.ps1` | Normalization and source keys |
| `collector/powershell/smb/tests/AdgSmbScan.Tests.ps1` | Orchestration, scopes, coverage |
| `collector/powershell/smb/tests/Export-AdgSmbFixturePayload.ps1` | Test scaffolding: writes real payloads for a fake estate |
| `backend/tests/contracts/test_smb_collector.py` | Validates that output against schemas and models |
| `scripts/collector-test.ps1` | Pester runner for all PowerShell collectors |

### Changed

| Path | Change |
| --- | --- |
| `docs/contracts/v1/smb-share-observation.schema.json` | Added optional `is_special` (contract 1.1) |
| `backend/app/contracts/v1/observations.py` | `SmbShareObservation.is_special` |
| `docs/contracts/collector-protocol.md` | New §10 subsection documenting 1.1 |
| `collector/README.md` | Index entry and test-runner pointer |

---

## Architecture decisions

### 1. The acquisition layer is a seam, and everything else is pure

Every function that touches a remote host lives in `AdgSmbSource.ps1` and is a thin wrapper
over one API call. Normalization and orchestration are separate and side-effect free. The
entire Pester suite therefore runs on a workstation with no domain, no file server, and no
network — which is what makes the failure paths (an offline server, a denied ACL read, an
orphaned SID, a NULL DACL) testable at all. Those paths are exactly the ones a live test
environment never exercises on demand.

### 2. The share security descriptor is the preferred reading

| | Descriptor | Permission levels |
| --- | --- | --- |
| API | `Win32_LogicalShareSecuritySetting` | `Get-SmbShareAccess` |
| Trustee | **SID**, always | Account **name** |
| Right | Raw 32-bit mask | `read`/`change`/`full`, or `Custom` |

A descriptor *stores* SIDs, so a trustee whose name no longer resolves still yields a
usable ACE. The level API reports names, and the contract identifies a trustee only by SID
— so an ACE whose name will not translate cannot be reported at all. The collector records
a `lookup_failed` error for it rather than dropping it silently.

`Custom` is likewise recorded as an `unmappable_right` error, never rounded to `change`:
the level API does not say what the underlying mask is, and guessing would over- or
under-state access.

`aclMethod` pins the choice. Left on `Auto`, an ACL that flips between the two readings
produces different source keys and churns history for no reason.

### 3. Contract 1.1: `is_special` added, UNC path and hidden state deliberately not

`is_special` is the SMB server's own Special flag. It is a **source fact that cannot be
derived**: hidden-ness follows from a trailing `$`, but an ordinary hidden share such as
`Data$` is hidden and *not* Special, so a name test cannot distinguish an administrator's
hidden share from Windows's own. The addition is optional and additive, which §10 of the
protocol already sanctioned as a minor bump; a 1.0 payload remains valid.

The other two values the prompt lists are derivations, and are kept out of the contract on
purpose:

- **UNC path** is exactly `\\<server_name>\<share_name>`. Carrying it as well would create
  a second identity for the share that can disagree with the first. `Get-AdgShareUncPath`
  is the single derivation, and it is tested.
- **Hidden state** is a property of the name. `Test-AdgHiddenShareName` derives it.

**Availability** has no share field either, and should not: a share that could not be read
is a `collectorError` on the completion envelope leaving the run `partial`, and a server
that could not be reached produces no `server` observation at all. An observation asserts
the object was seen; a host that never answered was not seen.

### 4. Scopes follow what was actually read

This is the decision with the most consequence, because reconciling a scope is the only
mechanism by which ADG marks anything absent.

| Scope | Declared when | Means |
| --- | --- | --- |
| `server\|<host>` | every share on the host was enumerated | shares missing from the run were deleted |
| `share\|<host>\|<share>` | that share's ACL was read completely | ACEs missing from the run were removed |

An excluded share is still **recorded as existing** by default; only its ACL goes unread,
and no `share` scope is declared for it. Both halves matter:

- without the share observation, reconciling the `server` scope would mark `C$` deleted;
- without withholding the `share` scope, the unread ACL's absent ACEs would reconcile into
  *nobody has access* — the exact inversion §8 of the protocol warns about.

`recordExcludedShares: false` drops excluded shares entirely; the `server` scope is then
not declared for that host, and deletions there stop being detectable. Documented as the
caller's trade.

### 5. `-RunPerServer`, because reconciliation is all-or-nothing per run

The contract refuses reconciliation for any run reporting an error. In one combined run
over fifty servers, a single unreachable host therefore prevents every *other* server's
shares from ever being marked absent — so stale shares would linger indefinitely in any
estate with one flaky host. `-RunPerServer` issues one run per server and contains that;
the README recommends it for anything beyond a handful of hosts.

### 6. Collect first, then build payloads

The run is read completely before the start envelope is built, so declared scopes can name
the shares that actually exist, and a dry run emits exactly the bytes a real run would
POST. The cost is that the run is held in memory — comfortable at share granularity,
and explicitly *not* viable for the Phase 2B NTFS walk, which must stream.

### 7. Unresolved principals only

A trustee SID that did not resolve produces a `principal` observation with
`principal_kind: unresolved` and `unresolved_reason: lookup_failed`, and never a guessed
name. A trustee that *did* resolve produces no principal observation: naming a principal
belongs to the collectors that know whether a SID is a user, a group, or a computer. This
one does not, and must not guess.

### 8. Targets are always explicit

There is no domain-wide sweep. That is posture, not a missing feature: spraying connection
attempts across every computer object looks like reconnaissance to a SOC, and it makes a
run's declared scope mean something vague.

---

## Contract change

**`smb_share` schema 1.0 → 1.1** — additive, backward compatible.

```json
"is_special": { "type": ["boolean", "null"] }
```

`null` means the source did not say, which is not the same as `false`. A 1.0 payload that
omits the field is still valid, and any 1.x server accepts both. No migration is required
and no `v2` directory is created. Documented in `docs/contracts/collector-protocol.md`
§10 and mirrored in `backend/app/contracts/v1/observations.py`; the existing schema/model
parity test covers the pair.

---

## Tests run, and exact results

| Suite | Command | Result |
| --- | --- | --- |
| SMB collector (Pester) | `pwsh -File scripts\collector-test.ps1 -Path collector\powershell\smb\tests` | **101 passed, 0 failed, 0 skipped, of 101** |
| Backend contracts (pytest) | `python -m pytest tests/contracts -q` | **388 passed, 1 skipped** (includes the 23 new SMB tests) |
| SMB contract test alone | `python -m pytest tests/contracts/test_smb_collector.py -q` | **23 passed** |
| Lint | `python -m ruff check app/contracts tests/contracts` | **All checks passed** |
| Types | `python -m mypy app/contracts tests/contracts` | **Success: no issues found in 15 source files** |

Additionally exercised by hand, not only inspected:

- `Invoke-AdgSmbScan.ps1 -DryRun` against an unreachable host — retried, warned, finished
  `failed` with one `rpc_unavailable` error and no reconciled scope, and wrote its payloads;
- `ConvertTo-AdgShareObservation` and `Test-AdgShareIncluded` against this machine's **real**
  `Get-SmbShare -IncludeHidden` output — `ADMIN$`, `C$`, `IPC$`, `T$`, `V$` all classified
  correctly (`is_special` true, `IPC$` typed `ipc` with `local_path` omitted, all excluded
  with the reason "administrative or system share").

Three real defects were found by these tests and fixed:

1. `Invoke-AdgSmbScan` without `-RunPerServer` silently produced one run *per server*.
   Assigning `, @(...)` out of an `if` expression let PowerShell unroll the wrapper.
2. `Import-AdgSmbTarget` threw on any call without a config file: under strict mode,
   member enumeration over an empty property collection is an error.
3. `Split-AdgObservationBatch` returned `$null` rather than an empty array for an empty
   input, because `return @()` unrolls to nothing.

Two live measurements corrected the implementation before the tests existed:

- the `MSFT_SmbShare` ShareType enum names are `CommunicationDevice` and
  `InterprocessCommunication`, not `Device`/`IPC`;
- `Win32_LogicalShareSecuritySetting` has **no instance** for an administrative share, so
  the descriptor read throws for `C$`/`ADMIN$`/`IPC$` and falls back to the level API.

---

## Known limitations

1. **Not exercised against a live remote server.** No domain or file server was available
   here, so the acquisition layer's real behaviour over WinRM/DCOM is unverified end to
   end. The normalizers *were* checked against real local `MSFT_SmbShare` objects. This is
   the highest-value thing to validate first in a lab.
2. **A NULL share DACL cannot be expressed.** It grants everyone full share access, and
   reporting zero ACEs would read as the exact opposite, so it is raised as a
   `null_share_dacl` error and the share's scope is withheld. Contract v1 has no field for
   it on a share observation; a future minor bump could add `dacl_present`, as
   `ntfs_resource` already has.
3. **The run is held in memory.** Fine at share granularity; not viable for Phase 2B.
4. **`computer_sid` / `domain_sid` are not collected.** They are Active Directory facts
   belonging to the Phase 1 collector.
5. **A share's trustee *name* is not transmitted.** SID is identity (ADR-0001), and
   `smb_ace` has no name field. Names for resolved principals come from the AD and
   local-group collectors.
6. **Alternate credentials are supported but discouraged.** `-Credential` exists for
   interactive testing; production should use a gMSA so no password exists to store.
7. **No local-group collection.** `BUILTIN\Administrators` on a share ACL is host-scoped
   and cannot be expanded until the local-groups collector exists.

---

## Security and privilege assumptions

- **Read-only throughout.** No write, no `WRITE_DAC`, no `WRITE_OWNER`, no ownership
  change, ever — including to make a failing read succeed (ADR-0004).
- **Domain Admin is not required and must not be used.** The practical least-privilege
  configuration is a dedicated collector account in **local Administrators on the file
  servers only**. Reading a share security descriptor is an administrative operation on
  most Windows builds.
- **Insufficient privilege degrades honestly.** A denied read produces an `access_denied`
  error, a `partial` run, and no reconciliation — it never returns fewer ACEs silently.
- **No secrets in source control.** The configuration template has no credential field;
  a test asserts the shipped example contains no password/secret/credential key.
- **Firewall surface:** WinRM TCP 5985/5986 (default), or DCOM TCP 135 plus the dynamic
  RPC range. Full detail in `collector/powershell/smb/README.md`.

---

## Migration and compatibility notes

- Contract minor bump 1.0 → 1.1, additive only. No data migration, no endpoint change, no
  `v2` directory. Existing 1.0 collectors and fixtures remain valid.
- No accepted schema file was edited to mean something different.
- Nothing from Phase 0A/0B was removed or renamed.

---

## Prerequisites for the next prompt

1. **Phase 1 (AD collector) was not present when this phase ran.** The last commit was
   Phase 0B; `docs/handoffs/` held only bootstrap, 0A, and 0B. Phase 2A depends only on the
   Phase 0B contracts, so it was built on those. Share ACEs reference trustee SIDs that
   **no collector yet resolves to principals** except as `unresolved`.
2. **A second session was editing this working tree concurrently**, building the AD
   collector (`collector/powershell/ad/`, `collector/powershell/common/`), an ingestion and
   repository layer, and a Phase 4A rights model. Only the paths listed above were staged
   and committed here. Anyone running the repo-wide suites should expect unrelated failures
   from that in-flight work; gate with scoped commands.
3. **`collector/powershell/common/` overlaps this module.** The other session's shared
   module duplicates timestamp, source-key, batching, and transport helpers that
   `AdgSmbCollector` also implements. They should be reconciled onto one shared module once
   that work lands — deliberately not attempted here, against a half-written, currently
   failing tree. The source-key derivations are the part that must not diverge; both are
   pinned to `backend/app/contracts/v1/keys.py` by tests.
4. **Phase 2B (NTFS) must stream**, not collect-then-build. See decision 6.
5. **Validate against a live file server** before trusting collection in an estate.

---

## `git status --short`

At the time of commit, restricted to this phase's paths:

```
 M backend/app/contracts/v1/observations.py
 M collector/README.md
 M docs/contracts/collector-protocol.md
 M docs/contracts/v1/smb-share-observation.schema.json
?? backend/tests/contracts/test_smb_collector.py
?? collector/powershell/smb/
?? scripts/collector-test.ps1
```

The full working tree also contained the concurrent session's uncommitted work, which was
neither staged nor committed. See the repository status recorded in the completion report.
