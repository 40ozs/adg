# Handoff — Phase 4B (`phase-04/02-effective-access-resolver.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-04a-rights-model.md](phase-04a-rights-model.md)
**Contract version after this phase:** `1.3` — unchanged. No collector payload changed.

## Scope completed

The engine that turns three independently collected layers — the identity graph, the share
ACL, and the NTFS DACL — into an answer to *who can reach this data, and how*. It performs
the Windows access check as Windows performs it, over an access token ADG constructs from
named assumptions, and it names every observation it had to assume in order to produce a
number.

1. **`backend/app/access_engine/` gained four framework-free modules**: `conditions.py`
   (the closed vocabulary of what could not be settled), `subjects.py` (the token),
   `evaluation.py` (the access check over one DACL), `resolver.py` (the two layers crossed
   for one access path). No FastAPI, no SQLAlchemy, no I/O.
2. **Every open limitation from Phase 4A is closed** — true DACL-order evaluation with the
   non-canonical finding, owner implicit rights, `CREATOR OWNER`, inheritance and
   `INHERIT_ONLY`, NULL- and empty-DACL semantics.
3. **`backend/app/services/access.py`** supplies observations to the engine with query
   shapes that do not grow with the estate, including the trustee-inversion that makes
   "who can reach this" linear in the ACL rather than quadratic in the domain.
4. **Four endpoints under `/api/v1/access`**, all bounded and paged.
5. **One migration** (`0006_effective_access`), adding one partial index. No table, no
   column, no contract change.
6. **`docs/architecture/effective-access.md`** — the normative specification.
7. **ADR-0010, ADR-0011, ADR-0012.**
8. **273 new tests** — 226 hermetic, 41 through HTTP against PostgreSQL, 6 counting
   statements — including all twelve canonical Phase 0B transcripts resolved end to end
   against their own `expectations` blocks, which no phase had checked before this one.

## Files and modules added or materially changed

### Added

| File | Lines | Contents |
| --- | --- | --- |
| `backend/app/access_engine/conditions.py` | 359 | `AccessCondition` (27 values), `AccessFinding`, the over/under-statement sets |
| `backend/app/access_engine/subjects.py` | 411 | `SubjectToken`, `TokenAssumption`, `SidOrigin`, `build_token`, the logon-session SID set |
| `backend/app/access_engine/evaluation.py` | 771 | `AclEntry`, `evaluate_acl`, `canonical_order_violations`, `DaclFacts`, `AppliedAce` |
| `backend/app/access_engine/resolver.py` | 499 | `resolve_access`, `EffectiveAccess`, `AccessCertainty`, `LimitingLayer`, `AclProvenance` |
| `backend/app/services/access.py` | 853 | `AccessService` — the three questions, bounded |
| `backend/app/api/access.py` | 864 | Four endpoints and their response models |
| `database/migrations/versions/0006_effective_access.py` | 42 | `ix_ntfs_resources_null_dacl` |
| `backend/tests/access_engine/support.py` | 231 | Builders over the real constructors |
| `backend/tests/access_engine/test_subjects.py` | 312 | 28 tests |
| `backend/tests/access_engine/test_evaluation.py` | 548 | 61 tests |
| `backend/tests/access_engine/test_resolver.py` | 415 | 44 tests |
| `backend/tests/access_engine/test_scenarios.py` | 607 | 91 tests over the canonical transcripts |
| `backend/tests/access_engine/test_ceilings.py` | 27 | 2 tests pinning the two ACL ceilings equal |
| `backend/tests/db/test_access_api.py` | 532 | 41 tests through HTTP against PostgreSQL |
| `docs/architecture/effective-access.md` | 312 | The specification |
| `docs/decisions/0010-…`, `0011-…`, `0012-…` | — | The three ADRs |

### Changed

| File | Change |
| --- | --- |
| `backend/app/access_engine/__init__.py` | Re-exports the Phase 4B API; docstring describes the four-module stack |
| `backend/app/repositories/resources.py` | Added `full_ntfs_acl`, `full_share_acl`, `ntfs_acls_for`, `share_acls_for`, `resources_named_by`, `shares_named_by`, `MAX_ACL_FETCH`. Nothing existing changed |
| `backend/app/repositories/membership.py` | Added `keys_with_members`. Nothing existing changed |
| `backend/app/models/schema.py` | One index declaration (`ix_ntfs_resources_null_dacl`) |
| `backend/app/api/graph.py` | `_resolve` → `resolve_principal`, made public so the access routes fail identically on an ambiguous SID. Behavior unchanged |
| `backend/app/api/__init__.py` | Registers `access.router` |
| `backend/tests/db/test_query_cost.py` | Six new tests and one wide-membership fixture |
| `backend/tests/fixtures/scenarios/10-smb-more-restrictive.json` | Two expectation values corrected — see below |
| `docs/architecture/rights-model.md` | One paragraph linking forward; no existing text altered |
| `docs/architecture/system-overview.md` | One section added |
| `docs/decisions/README.md` | Three rows |

**No Phase 0A/0B contract was modified.** No JSON Schema, no observation model, no
collector payload, no API response of any earlier phase.

## The one fixture correction

`10-smb-more-restrictive` declared `expected_effective_remote: "read"` and
`expected_effective_local: "full"`. Both are wrong under the accepted rights model, and
the first is provably so: `11-ntfs-more-restrictive` declares `"read_execute"` for **the
same effective mask**, `0x001200A9`. They cannot both stand.

SMB `Read` and NTFS `Read & Execute` are one mask under two names — which is precisely why
ADR-0005 forbids comparing rights by label — and an effective mask is labelled with a
`RightsCategory`, whose members are `read_execute` and `full_control`. Scenario 10 was
amended to those two values, as the fixture README instructs
(*"if a fixture's expectation turns out to be wrong, fix the fixture in that phase and say
so in its handoff"*). `TestTheFixtureContract` pins the two scenarios to agree, so the
divergence cannot come back.

## Public API introduced

```
AccessCondition / AccessFinding          27 named conditions, with evidence and a sentence
OVERSTATING_CONDITIONS                   gaps that could only widen the truth
UNDERSTATING_CONDITIONS                  gaps that could only narrow it

SubjectFacts / SubjectToken / TokenSid   the token, and why each SID is in it
TokenAssumption                          authenticated_user | anonymous | sids_only
SidOrigin                                subject | group_membership | well_known | logon_type
build_token / default_assumption
LOGON_SESSION_SIDS                       SIDs that belong to a session, not a principal

AclEntry / ntfs_entry / share_entry      one ACE, layer-tagged, independent of its store
DaclFacts                                dacl_present, protected, owner (sid and key)
evaluate_acl -> AclEvaluation            the access check; granted_by/denied_by/superseded
AppliedAce                               one matching ACE and what it contributed
canonical_order_violations               where a DACL departs from canonical order
MAX_ACL_ENTRIES / OWNER_IMPLICIT_RIGHTS

ResourceDacl / ShareDacl                 the resolver's two inputs
resolve_access -> EffectiveAccess        the answer, with both layers and every condition
AccessCertainty                          certain | at_most | at_least | uncertain
LimitingLayer                            none | smb_share | ntfs | both | unknown
AclProvenance                            observed | derived | unobserved
certainty_of / unobserved_trustee_findings

AccessService                            effective_access / effective_principals /
                                         accessible_resources
```

## Important architecture decisions

Full reasoning in ADR-0010, ADR-0011 and ADR-0012. The load-bearing ones:

1. **The DACL is evaluated in the order it is stored.** An Allow ahead of a Deny wins,
   because that is what Windows does. `resolve_canonical` is computed alongside and reported
   as `canonical_rights`; a divergence raises `ORDER_DEPENDENT_RESULT` **per subject**,
   which is a stronger and more actionable statement than `NON_CANONICAL_DACL` about the
   object. Closes Phase 4A limitation 1.
2. **The token is constructed, and the construction is reported.** ADG has never seen a
   logon. `Everyone`, `Authenticated Users` and one logon-type SID are added under a named
   `TokenAssumption`, every added SID is listed on a finding, and every entry carries its
   origin. Without this, scenario 10 — a share ACE naming `Authenticated Users` — resolves
   to no access, which is wrong.
3. **Session-dependent SIDs are refused rather than guessed.** `BATCH`, `SERVICE`,
   `REMOTE INTERACTIVE` and the authentication-type SIDs produce `LOGON_TYPE_TRUSTEE` rather
   than being counted as unmatched.
4. **Owner rights are granted before the DACL is read**, so an explicit Deny on the owner
   does not remove `WRITE_DAC` — and an `OWNER RIGHTS` entry replaces them rather than adding
   to them. Closes limitations 2 and 3.
5. **A gap is never a default.** An unread share ACL is an upper bound, not an open share. A
   path with no stored descriptor gets its DACL projected from the nearest ancestor that has
   one, labelled `derived`. A group with no collected membership makes the answer a lower
   bound. Each has its own condition, and `AccessCertainty` says which direction.
6. **`access: false` with `certainty: at_least` means no access was *established*.** The
   distinction is the point of the field.
7. **Matching is by storage key at every layer, including the owner.** `DaclFacts` carries
   `owner_key` beside `owner_sid` for exactly this: an object owned by `S-1-5-32-544` on FS01
   is owned by `fs01|S-1-5-32-544`, and matching the bare SID would hand every server's local
   administrators ownership of every other's files.
8. **"Who can reach this" inverts the traversal.** Each ACL trustee is expanded *downward*
   once and the map inverted, because a principal's rights here depend on nothing but which
   trustees it belongs to. Linear in the ACL, not quadratic in the domain, and exact.
9. **The candidate set for "what can this principal reach" unions the NULL-DACL rows in.**
   They name nobody and so appear in no reference index; omitting them would omit exactly the
   resources open to the whole estate.
10. **Candidates that grant nothing are returned with their verdict.** Filtering inside a
    page would break `has_more` and would hide the listed-but-no-access finding.

## Schemas and contracts

**One migration, one index, no contract change.**

`0006_effective_access` adds `ix_ntfs_resources_null_dacl` — a partial index on
`resource_key WHERE NOT dacl_present`. It exists so the "what can this principal reach"
candidate query can union in the resources that name nobody without a sequential scan of the
largest table in the schema. `alembic check` reports no drift after it.

The API adds `/api/v1/access/*` and changes no existing response. `AccessCondition`'s values
are a wire contract: clients branch on them, so adding one is additive and renaming one is
breaking.

## Tests run and exact results

All commands from `C:\code\adg\backend` using `.venv\Scripts\python.exe`.

### This phase's own gates — all pass

| Gate | Command | Result |
| --- | --- | --- |
| Domain tests | `pytest tests/access_engine -q` | **389 passed, 1 skipped in 0.71s** |
| Database tests | `pytest tests/db/test_access_api.py -q` (smoke) | **41 passed in 17.15s** |
| Query cost | `pytest tests/db/test_query_cost.py -q` (smoke) | **15 passed in 9.70s** |
| Migration | `alembic upgrade head` then `alembic check` | **No new upgrade operations detected** |

The one skip is `test_the_expected_ntfs_mask_is_produced[09-broken-inheritance]`: that
scenario declares no `expected_ntfs_mask`, and the test skips rather than inventing one.

### Repository-wide — all pass

| Command | Result |
| --- | --- |
| `pytest tests -q -m "not smoke"` | **3641 passed, 10 skipped, 306 deselected in 14.69s** |
| `ADG_RUN_SMOKE_TESTS=1 pytest tests -q` | **3947 passed, 10 skipped in 139.87s** |
| `scripts\backend-lint.ps1` | **Backend checks passed** — ruff, ruff format, mypy strict, 131 files |

Before this phase the hermetic suite stood at 3415 and the full suite at 3674.

### What the canonical-scenario suite actually checks

`tests/access_engine/test_scenarios.py` turns each Phase 0B transcript into the resolver's
own inputs — using the same key derivations ingestion uses, and the shipped
`app.domain.expand` over an in-memory adjacency — and asserts against the `expectations`
block written in Phase 0B. Every scenario is checked for its access verdict, its NTFS mask,
and that the layer it declares as limiting is the one whose rights equal the effective
rights. Beyond that, per scenario:

| Scenario | What is now pinned |
| --- | --- |
| 01 | The grant matches the subject directly, not through a group |
| 02, 03 | The membership chain and hop count equal the declared `expected_path` |
| 04 | Three independent paths exist, and removing one does not remove access |
| 05 | A cyclic graph terminates, still grants, and the cycle is reported |
| 06 | The orphan holds Full Control, gets no guessed name, and its grant reaches nobody ADG can name; resolving *as* the orphan is `uncertain` |
| 07 | Deny wins, the Allow is reported as superseded, the order is canonical — **and reversing the stored order reverses the answer** |
| 08 | The inherited grant reaches the child and names the ancestor to fix |
| 09 | A protected DACL stops the parent's grant; access arrives only through the host-scoped local group, and the same BUILTIN SID on another server would not grant |
| 10, 11 | Both produce the same effective mask by opposite routes; the limiting layer differs; local access bypasses the share in one and not the other |
| 12 | A partial run still answers about what it read, and the path it could not read has no DACL to evaluate |

### What the query-cost suite pins

- Every access listing costs the same statements for one row as for a hundred.
- Resolving one principal against one resource reads each table exactly once.
- Forty group members cost **at most 8** reads of `membership_edges`, not forty — the
  trustee-inversion, measured.
- A one-row page and a hundred-row page of the principal listing cost identically, so paging
  slices a traversal rather than re-fanning it out.

## Known limitations

1. **Privileges are not modeled.** `SeBackupPrivilege` bypasses the DACL entirely and
   `SeTakeOwnershipPrivilege` leads to `WRITE_DAC`. Neither is collected, so an
   administrator's real reach exceeds any answer here. Not reported as a condition, because
   ADG has no evidence either way.
2. **The token is a reconstruction.** If an estate excludes `Everyone` from anonymous
   sessions by policy, or a machine's local group membership was never collected, the token
   is wrong in a way only the conditions disclose.
3. **`BUILTIN\Users` membership is usually invisible.** Every domain user is in it on every
   member server, and ADG only knows so where a local-group collection ran. Reported through
   `TRUSTEE_MEMBERSHIP_UNOBSERVED`, not assumed.
4. **A projected DACL is a prediction.** It holds only while nothing between the path and its
   ancestor breaks inheritance, which is exactly what an unscanned intermediate directory
   could do. `INTERMEDIATE_PATH_UNOBSERVED` reports the gap; it cannot close it.
5. **Canonical-order checking cannot see inheritance levels.** `inherited_from` is still
   never populated, so an inherited Deny behind an inherited Allow is deliberately **not**
   reported: it is canonical across two ancestors, and flagging it would produce a false
   finding on a very large number of directories.
6. **`enumeration.complete` is false on most real ACLs**, because most carry an `Everyone` or
   `Authenticated Users` entry whose membership no database holds. That is accurate and will
   read as noise until a consumer renders it well.
7. **Offset paging re-runs the traversal per page** for `/resources/{r}/principals`. The same
   caveat the recursive membership endpoints already carry.
8. **Conditional ACEs, central access policies, DAC claims, and the SACL** remain unmodeled,
   unchanged from Phase 0A.
9. **Access-based enumeration is not folded into rights.** Whether a principal can *see* a
   folder they cannot open is a share setting ADG records and does not evaluate.
10. **The trustee expansions for one resource are sequential.** Independent, and could be
    issued together; no measurement yet says they need to be.

## Security and privilege assumptions

- **No new privilege is required and nothing is written to a target.** This phase adds no
  collector and no collection. ADR-0004's read-only posture is unaffected.
- **The engine errs toward *not* narrowing.** Every default in it — an unread share ACL as an
  upper bound, an unresolved subject assumed authenticated, an unobserved group membership as
  unknown — reports access that may not exist rather than hiding access that does. An audit
  tool's false positive is a wasted hour; its false negative is the finding nobody made.
- **Escalation stays visible.** `OWNER_IMPLICIT_RIGHTS` reports the `WRITE_DAC` no ACE shows,
  and `ESCALATION_RIGHTS` is raised on the **final** effective mask, so a share granting Full
  Control over NTFS granting nothing does not produce a false one.
- **The endpoints are read-only**, and carry no authorization of their own: the API is
  unauthenticated until Phase 6, and "who can reach this data" is itself sensitive. That is
  unchanged from every other endpoint and is called out here because these are the first ones
  whose *output* is a reconnaissance map.
- **No secrets, no new configuration.**

## Migration and compatibility notes

- **One migration, additive and reversible.** `0006_effective_access` creates one partial
  index and its `downgrade()` drops it. No data is read, written, or moved. Applying it to a
  populated database takes one index build over the rows where `dacl_present` is false, which
  on a healthy estate is none of them.
- **No breaking change.** Every earlier endpoint, response model, JSON Schema, and observation
  kind is untouched. `resolve_principal` was renamed from a private `_resolve` within
  `app.api.graph`; no behavior and no route changed.
- **Anything persisting an effective-access result must store the mask and the layer**, never
  the label, and must store `certainty` with it. A stored mask without its certainty is a
  claim the engine did not make.

## Prerequisites for the next prompt

**For Phase 5 (risk):**

1. Branch on `AccessCertainty`, not on the mask alone. `access: false` with `at_least` is not
   a clean result, and a rule that treats it as one will silently drop the directories whose
   memberships were never collected.
2. Read `ESCALATION_RIGHTS`, `OWNER_IMPLICIT_RIGHTS` and `NULL_DACL` from the conditions
   rather than re-deriving them from a mask. The owner's `WRITE_DAC` appears in no ACE.
3. `enumeration.complete` is the field that says whether "these are the people with access"
   is true. On an ACL naming `Everyone` it will be false, and that is the correct answer.
4. A rule needing the same answer across many resources wants a query shape none of these
   three endpoints has. Add it deliberately rather than looping over `effective_access`.

**For Phase 6 (UI/auth):**

5. Render `access: false, certainty: at_least` differently from `access: false,
   certainty: certain`. They mean opposite things to an operator.
6. `rights.mask` is the value; `rights.label` is a rendering. Never store or compare the
   label.
7. These endpoints output a reconnaissance map of the estate. They need authorization before
   the product is deployed anywhere real.

**For the collectors, when they are next touched:**

8. Populating `inherited_from` would let canonical-order checking distinguish one ancestor's
   inherited block from another's, and would let an explanation name the exact ancestor rather
   than the nearest observed one. It is the single highest-value collector change for this
   engine.
9. Collecting local-group membership on every scanned server closes limitation 3, which is
   currently the most common cause of a lower-bound answer on a real estate.

## Commit

`9f45eda` — *Phase 4B: the effective-access resolver*. Not pushed; no remote is
configured for this repository.

## `git status --short`

Taken at the end of this phase, before the commit. Entries marked ✓ belong to this phase and
were staged by explicit path; `backend/tests/contracts/test_smb_collector.py` is a formatting
change that was already in the working tree when this phase started and was **not** staged.

```
 M backend/app/access_engine/__init__.py                       ✓
 M backend/app/api/__init__.py                                 ✓
 M backend/app/api/graph.py                                    ✓
 M backend/app/models/schema.py                                ✓
 M backend/app/repositories/membership.py                      ✓
 M backend/app/repositories/resources.py                       ✓
 M backend/tests/contracts/test_smb_collector.py
 M backend/tests/db/test_query_cost.py                         ✓
 M backend/tests/fixtures/scenarios/10-smb-more-restrictive.json ✓
 M docs/architecture/rights-model.md                           ✓
 M docs/architecture/system-overview.md                        ✓
 M docs/decisions/README.md                                    ✓
?? backend/app/access_engine/conditions.py                     ✓
?? backend/app/access_engine/evaluation.py                     ✓
?? backend/app/access_engine/resolver.py                       ✓
?? backend/app/access_engine/subjects.py                       ✓
?? backend/app/api/access.py                                   ✓
?? backend/app/services/access.py                              ✓
?? backend/tests/access_engine/support.py                      ✓
?? backend/tests/access_engine/test_ceilings.py                ✓
?? backend/tests/access_engine/test_evaluation.py              ✓
?? backend/tests/access_engine/test_resolver.py                ✓
?? backend/tests/access_engine/test_scenarios.py               ✓
?? backend/tests/access_engine/test_subjects.py                ✓
?? backend/tests/db/test_access_api.py                         ✓
?? database/migrations/versions/0006_effective_access.py       ✓
?? docs/architecture/effective-access.md                       ✓
?? docs/decisions/0010-effective-access-is-an-access-check.md  ✓
?? docs/decisions/0011-answers-carry-their-uncertainty.md      ✓
?? docs/decisions/0012-bounded-access-queries.md               ✓
```
