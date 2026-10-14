# ADG-DEMO-01 — SYSTEM explanation correctness and regression protection

**Verdict: `READY_FOR_ADG_DEMO_02_WITH_LIMITATIONS`**

The contradictory SYSTEM explanation was reproduced on the seeded Null Meridian demo, traced
to the access engine's token construction and verdict sentence (repeated by the UI's fixed
wording), and removed at those layers. A SYSTEM subject now has one
authoritative mapping state, every downstream sentence and label is derived from it, and
regression tests at the engine, API, demo-dataset and UI layers pin it. The limitations in
§9 are scope limits, not open contradictions. None of them reintroduces a definitive answer
the evidence does not support.

| | |
| --- | --- |
| Repository | `40ozs/adg` (`https://github.com/40ozs/adg`) |
| Branch | `claude/adg-system-explanation-correctness-4iaijw` |
| Starting SHA | `1ab01a2058a8cb6d090bf5abeb6971b4b2ee33af` (= `origin/main` at start) |
| Implementation SHAs | `4cb297b` (state model, fix, tests) → `b949735` (confirmed state never a conclusive negative) |
| Report commit | the commit that adds this file (the branch head; see §11) |
| Data | Synthetic only: the Phase 0B fixture domain and the Null Meridian demo estate |

---

## 1. Prior work reconciled

No repository-owned document described this defect. Searched before implementation:
`docs/` (handoffs, demo, architecture, decisions, contracts) on `main` and on all 17 remote
heads (`claude/*`, `deploy/*`, `feature/ADG-GRAPH-01A/01B`, `fix/*`, `integration/*`). The
only SYSTEM references were the well-known SID table, the change-feed exclusion of SYSTEM
from "broad trustee" scoring, and a collector-hardening note in `phase-07b-incremental.md`.
This work therefore starts from `main` at `1ab01a2` and depends on no other branch. Nothing
was merged into or from any other branch.

Overlap with unmerged work, checked with `git diff` and trial merges (`git merge-tree`, which
writes nothing) against every remote head:

* No branch changes `app/access_engine/subjects.py`, `conditions.py` or `verdict.py`, so the
  engine fix has no overlap.
* Nine branches (`claude/adoring-cannon-7gh2pf`, `claude/compassionate-hamilton-3sh5db`,
  `deploy/ADG-FREE-STAGING-01`, `feature/ADG-GRAPH-01A/01B`, `fix/ADG-NEXT16-REACT418-01`,
  `fix/ADG-UI-COMPACT-IDENTITY-01`, `integration/ADG-NEXT16-UI-02`,
  `integration/ADG-UI-EDGE-01`) also change `services/access.py`, `Explanation.tsx`,
  `Identity.tsx` and `lib/identity.ts`. Those auto-merge.
* The one textual conflict, on all of them, is `frontend/app/identities/principal/page.tsx`.
  Those branches restructure that page, and this branch adds a banner for a kind-`unresolved`
  record to it. Resolution when integrating: take their structure and re-apply the
  `isUnresolved(principal)` banner where the page header now lives.

## 2. The original contradictory behavior (reproduced before the fix)

Reproduced against a fresh Null Meridian demo reset (canonical dataset, three generations)
on PostgreSQL 16, at the starting SHA. Request:
`GET /api/v1/access/explain?principal=S-1-5-18&resource=\\NM-FS-01\Shared`.

| Request | Before | After |
| --- | --- | --- |
| over SMB (default) | **`no_grant`, `certain`**, *"No entry on either ACL names it with anything to give."* while `effective.ntfs.granted_by` = **SYSTEM: Full Control** and a path ran from SYSTEM through it | `indeterminate`, `uncertain`, not conclusive; `system_mapping_ambiguous`; no SYSTEM entry applied, no path |
| over SMB, `assumption=authenticated_user` | **`granted` Full Control, `certain`**, from a token holding `S-1-5-18` *and* `NETWORK` | `indeterminate`, `uncertain`; `system_mapping_ambiguous` |
| local | `granted` Full Control, `certain` | `granted` Full Control, `at_least`; `system_mapping_confirmed` citing the describing run |

The same question answered "certainly nothing" and "certainly Full Control" depending on a
token assumption, and the first answer contradicted itself. Full bodies, before and after:
[`ADG-DEMO-01-evidence/`](ADG-DEMO-01-evidence/). The final engine regression module, run
against the unmodified starting SHA in a separate worktree, is
[`before-engine-regression-pytest.txt`](ADG-DEMO-01-evidence/before-engine-regression-pytest.txt):
**46 failed, 6 passed**. Every failure is the defect itself: the "no entry names it"
sentence beside an NTFS grant; `S-1-5-18` matched over SMB; SYSTEM's owner rights granted to
a network session; a certain answer about an undescribed or disputed SYSTEM. On the
implementation head the same module passes 52/52.

The UI rendered the contradiction as given: headline from `verdict.reason`, plus the fixed
`no_grant` guidance *"Nothing on either ACL reaches this principal…"*, above a route table
showing SYSTEM's NTFS grant.

## 3. Root cause

The contradiction came from **multiple layers**, and the fix is in the lowest of them.

1. **Identity correlation / token construction** (`app/access_engine/subjects.py`). No
   network logon carries `S-1-5-18`. A SYSTEM process reaching a share arrives as the
   *computer account* of the machine it runs on, and the question does not name that
   machine. `build_token` nonetheless put `S-1-5-18` in an SMB token as the subject. That
   is a mapping no evidence supports, and nothing qualified it, so certainty folded to
   `certain`. It also matched SYSTEM's ownership (implicit owner rights over the network)
   and, under `authenticated_user`, assembled a token Windows never issues. When no
   collector described `S-1-5-18`, or described it as something else, `default_assumption`
   treated it as an ordinary authenticated user and invented `Everyone`,
   `Authenticated Users` and a logon SID for it.
2. **Explanation text generation** (`app/access_engine/verdict.py`). The verdict sentence was
   a fixed string per outcome. `no_grant` always said no entry named the principal, even
   when one layer's entries named it and granted rights the other layer withheld. This half
   is not SYSTEM-specific.
3. **UI formatting** (`frontend/lib/explanation.ts`, `lib/identity.ts`). Guidance was fixed
   per outcome ("Nothing on either ACL reaches this principal"; "collection is incomplete")
   and contradicted the evidence on the same screen. Separately, a stored principal whose
   `kind` is `unresolved` rendered as resolved (no "unresolved" note), because the UI read
   only `resolved`, which the API sets whenever a record exists.

Data normalization and relationship derivation were checked and are not involved: the SID
canonicalizes correctly, and the ACE trustee key for `S-1-5-18` is the bare SID.

## 4. The invariant now enforced

> **Every answer about `S-1-5-18` carries exactly one `SystemMapping` state, assessed once
> in token construction and reported by exactly one `system_mapping_*` finding. Only
> `confirmed` may be stated as a resolved identity. No SYSTEM answer is ever a conclusive
> negative. Over a share, no entry naming SYSTEM, and no ownership by SYSTEM, applies to the
> subject. Every verdict sentence and UI wording about the answer is derived from that state
> and from the layers' own results.**

| State | Evidence | Finding | Certainty directions | What the answer may say |
| --- | --- | --- | --- | --- |
| `confirmed` | local access **and** a collector described `S-1-5-18` as `well_known` | `system_mapping_confirmed` | understates only | the mapping (this host's LocalSystem) and its provenance (describing run, time); rights are a lower bound |
| `ambiguous` | access over a share (any description) | `system_mapping_ambiguous` | both | that SYSTEM is a computer account the question does not name; entries naming SYSTEM were not applied |
| `missing` | local access, and no description, or a description of kind `unresolved` | `system_mapping_missing` | both | that the identity is unknown; the SID was matched literally and nothing was assumed |
| `conflicting` | a collector described `S-1-5-18` as any other kind | `system_mapping_conflicting` | both | that the description contradicts the SID's fixed meaning, with both sides and the describing run |

Precedence when two apply: `conflicting` → `ambiguous` → `missing` → `confirmed`. A dispute
in collected evidence is never hidden. Over a share, no description of `S-1-5-18` can
change the answer. The finding detail always has the same keys: `state`, `subject_sid`,
`subject_key`, `access_path`, `presented`, `described_as`, `described_name`,
`expected_kind`, `last_observed_run_id`, `last_observed_at`, `withheld_groups`.

`confirmed` understates because the token holds `S-1-5-18` alone. LocalSystem's real token
also carries groups (`BUILTIN\Administrators` among them) that ADG does not model for it,
so without this an ACL granting only the server's administrators would come back a
*certain* `no_grant` for SYSTEM.

## 5. What changed

**Backend, access engine (the fix):**

* `app/access_engine/subjects.py`: `SYSTEM_SID`, `SystemMapping`, `system_mapping_of()`,
  `SYSTEM_MAPPING_CONDITIONS`. `SubjectToken.system_mapping` and
  `SubjectToken.subject_presented`: `contains`/`entry` do not offer an unpresented subject
  entry, so no ACE and no ownership matches it. `build_token` attaches the mapping finding,
  withholds observed groups of SYSTEM's local identity over a share and names them, and
  keeps the subject entry first. `default_assumption` gives SYSTEM `sids_only` whatever a
  collector said. `SubjectFacts` gains optional provenance (`last_observed_run_id`,
  `last_observed_at`).
* `app/access_engine/conditions.py`: four conditions, one sentence each, and their
  direction sets.
* `app/access_engine/verdict.py`: `describe_verdict()`. The verdict sentence is derived from
  the mapping state and from per-layer rights. `describe_outcome()` is unchanged and still
  used whenever the evidence does not contradict it.
* `app/services/access.py`: passes the principal record's provenance into `SubjectFacts`.

Because the fix is in `build_token`, it reaches all three questions (principal → resource,
resource → principals, principal → resources) and the explanation, with no change to them.
Tokens built directly to ask what an ACL grants a SID (the risk engine's `_token_for`) carry
no mapping and are unchanged.

**API.** No schema change. The state crosses the boundary as the existing `FindingView`
(`warnings`, `effective.findings`, `effective.conditions`). `condition` and `detail` are
open fields in `docs/contracts/v1/derived/*.schema.json`, so `CONTRACT_VERSION` stays `1.0`.
`test_derived_schemas` and `test_openapi_snapshot` pass unchanged.

**Frontend:**

* `lib/explanation.ts`: `systemMapping()` reads the one finding (an unknown state is `null`,
  never guessed). `explanationWording()` derives guidance from the state and from the
  layers. The `inconclusive` caution and the text export use the same rules. The export no
  longer prints "Effective rights: No access" for an inconclusive answer, and states the
  mapping.
* `components/Explanation.tsx`: the verdict panel uses `explanationWording` and shows a
  "SYSTEM mapping" row.
* `lib/identity.ts`, `components/Identity.tsx`, `app/identities/principal/page.tsx`:
  `isUnresolved()` treats a record of kind `unresolved` as unresolved (note, warning style,
  and its own banner).

**Docs:** `docs/architecture/effective-access.md`, new section "SYSTEM: one mapping state,
assessed once".

## 6. Test cases added

Deterministic and synthetic. The fixture written specifically to prevent recurrence is
`share_root()` + `SHARES` in `backend/tests/access_engine/test_system_explanation_regression.py`:
the share root Windows and Null Meridian's generator write (SYSTEM and the server's
administrators at Full Control, a team at Modify, SYSTEM as owner) behind the three share
ACL shapes the demo generates.

| Layer | File | Tests | What it proves |
| --- | --- | --- | --- |
| engine (pre-existing API only) | `backend/tests/access_engine/test_system_explanation_regression.py` | 52 | the original contradiction is gone. A no-contradiction invariant holds over description × path × assumption × share shape (48 cells), and the `no_grant` sentence follows the layers. **Fails 46/52 on the starting SHA** |
| engine | `backend/tests/access_engine/test_system_mapping.py` | 49 | each state is reachable only from its evidence; finding ↔ state bijection; fixed detail shape; provenance; ambiguous never matches SYSTEM's entry, ownership or local groups; missing/conflicting invent no token; confirmed never yields a conclusive negative (the Administrators-only ACL); newer evidence changes the state only when it supports it; other subjects and literal tokens unchanged |
| engine | `backend/tests/access_engine/test_resolver.py` (updated) | — | the both-directions vocabulary test names the three unsettled conditions deliberately |
| API + PostgreSQL | `backend/tests/db/test_system_mapping_api.py` | 10 | the state survives collector transcript → DB → service → serializer, and validates against the published schema. Confirmed, ambiguous, missing (unresolved row), conflicting, each with provenance. **Temporal:** across six runs, `missing → confirmed → conflicting`, a late-posted *older* `well_known` reading does **not** settle a newer dispute, a newer one does, and over SMB it stays `ambiguous` throughout. The ETag is not served across a change of state. The resource → principals listing agrees. A SID with no record is a 404, not an invented mapping |
| demo dataset | `backend/tests/db/test_null_meridian_demo.py::TestTheSystemExplanation` | 2 | the exact original request on the Null Meridian estate: SMB is ambiguous and contradicts nothing; local is confirmed with provenance |
| UI | `frontend/tests/system-mapping.test.tsx` | 23 | reading the state (unknown states → null); wording never calls an unsettled mapping resolved or an empty one "no access"; "nothing reaches this principal" is never shown beside a granting layer; the verdict panel and text export agree; a kind-`unresolved` row renders as unresolved |
| UI fixtures | `frontend/tests/explanation-factories.ts` | — | `noGrant()` made internally consistent: it claimed no entry named Alice while its inherited share layer granted Full Control, which is an instance of the defect class. Added `noGrantBetweenLayers`, `systemOverShare`, `systemLocally`, `systemMappingFinding` |

## 7. Gates run (this environment)

All run in the cloud container: Python 3.13.16, PostgreSQL 16 (local), Node 22. CI-equivalent
commands from `.github/workflows/ci.yml`.

| Gate | Starting SHA `1ab01a2` | Final tree `b949735` |
| --- | --- | --- |
| `ruff check .` | — | **passed** |
| `ruff format --check .` | — | **passed** (401 files) |
| `mypy app tests` | — | **passed** (400 files) |
| `pytest -m "not smoke"` | 5765 passed, 124 skipped, 1215 deselected | **5866 passed, 124 skipped, 1227 deselected** |
| `pytest -m smoke` (PostgreSQL), full | 1214 passed, 1 xfailed, exit 0 (24m46s) | **1226 passed, 1 xfailed, exit 0** (24m18s) |
| `pytest -m smoke`, the 12 new DB tests (`test_system_mapping_api.py`, `TestTheSystemExplanation`) | — | **12 passed** (10 in 9.7 s, 2 in 85.9 s), isolated database |
| frontend `npm run lint` | — | **passed** |
| frontend `npm run typecheck` | — | **passed** |
| frontend `npm run test` | — | **1186 passed** (34 files) |
| frontend `npm run build` | — | **passed** |
| Collector Pester suite (`windows-latest`) | not run | not run locally (no Windows host); **passed in GitHub Actions** CI run 58 on `315fab0` |
| `docker compose config -q` | not run | not run locally (no Docker); **passed in GitHub Actions** CI run 58 on `315fab0` |

Method notes, so the numbers can be trusted:

* The baseline DB suite ran from a separate worktree at `1ab01a2` against its own database
  (`adgbase*`), and the final one against `adg*`, so the two could not share tables. An
  earlier attempt that ran both against one database interfered with itself (truncations
  from one run deadlocked the other) and its results were discarded.
* The final unit and DB suites ran on exactly `b949735`. The commits after it add only this
  report, the evidence directory and a documentation correction; no code or test changed.

### 7a. Full DB suite results

Both full PostgreSQL suites finished after this report was first committed (`315fab0`), and
their counts are now in the table above. 1226 = the baseline's 1214 + the 12 new DB tests:
no pre-existing DB test changed outcome, and the one `xfail` is the same pre-existing one.

GitHub Actions on the pushed branch (CI run 58, head `315fab0`, which contains `b949735`):
Docker Compose configuration, Collector (Pester, `windows-latest`), and Frontend (lint,
types, tests, build) passed. Backend lint, type check and unit tests passed, and its
database step was still running when this was written. The final response reports its
outcome, and the run is at `https://github.com/40ozs/adg/actions/runs/38625889131`.

## 8. Correctness and safety properties

* **No contradiction:** for every SYSTEM state × path × assumption × share shape in the
  matrix, the verdict sentence, the outcome, the certainty, the applied entries and the
  causal paths agree (engine), and the same holds on the wire (API) and on screen (UI).
* **No false certainty about SYSTEM:** certainty is never `certain`; the outcome is never
  `no_grant` or `denied`. Over SMB, SYSTEM's entry, ownership and local group memberships
  never apply.
* **No invented mapping:** a missing or disputed description gets `sids_only` and no assumed
  SIDs. A SID with no record at all is a 404 ("Nothing is stored about…"), as for every
  other undescribed SID.
* **Conflict preserved:** both the collected description and the SID's fixed meaning, with
  the describing run, reach the client. An older reading posted late cannot settle a newer
  dispute (newest-wins ingestion, verified end to end).
* **No regression elsewhere:** other subjects carry no mapping finding, and the risk
  engine's literal tokens are unchanged. Every pre-existing test passes. Two pre-existing
  test files were changed on purpose: the condition-vocabulary test in `test_resolver.py`
  (it exists to make adding a both-directions condition deliberate), and the frontend
  `noGrant()` fixture (made internally consistent, see §6).
* **Data boundary:** synthetic SIDs and names only. No live integration, provider, directory
  or remediation path was exercised or changed. No credentials were used beyond the local
  CI-equivalent test database password already in the workflow file.

## 9. Known limitations and deferred work

1. **Over a share, SYSTEM is never resolved to a computer account.** `ambiguous` is
   structural: nothing in the question names the originating machine, and ADG has no
   host → computer-account correlation. So the "resolved after newer evidence" requirement
   is proven for `missing → confirmed` and `conflicting → confirmed`, and it is proven that
   no newer description resolves the share case. Making `ambiguous` resolvable would need a
   host-qualified SYSTEM question and a correlation from a server observation to exactly
   one `computer` principal (with its own ambiguous and missing cases). That is a feature,
   and it is not in this prompt.
2. **`confirmed` answers are lower bounds.** LocalSystem's own groups
   (`BUILTIN\Administrators` among them) are not modeled into its token, because that needs
   the host-scoped key of the server holding each ACL. This is reported (`at_least`,
   `indeterminate` when empty) rather than hidden.
3. **Conflict is assessed against the latest stored description.** Ingestion is
   newest-wins per principal key, so two collectors disagreeing across runs show up as a
   conflict only when the newest description contradicts the SID. The version history is
   not consulted.
4. **Scope is `S-1-5-18`.** `NETWORK SERVICE` (`S-1-5-20`, also a computer account on the
   network) and `LOCAL SERVICE` (`S-1-5-19`, anonymous on the network) have analogous path
   semantics and are not changed here.
5. **A kind-`unresolved` record still serializes `resolved: true`.** That field is
   documented as "ADG holds a record", and changing its meaning would be a breaking
   contract change. The UI now reads `kind` as well (`isUnresolved`). The API meaning is
   unchanged.
6. **Integration conflict ahead.** Merging with the nine parallel UI branches conflicts in
   `frontend/app/identities/principal/page.tsx` only (see §1 for the resolution).
7. **Not run locally:** the Windows Pester collector suite and the Docker Compose validation
   (no Windows host or Docker in this environment). Both passed in GitHub Actions on the
   pushed branch. No browser screenshot pass was taken. The UI behavior is covered by jsdom component tests
   and a production `next build`.

## 10. Relevant files

```
backend/app/access_engine/subjects.py      backend/tests/access_engine/test_system_explanation_regression.py
backend/app/access_engine/conditions.py    backend/tests/access_engine/test_system_mapping.py
backend/app/access_engine/verdict.py       backend/tests/access_engine/test_resolver.py
backend/app/access_engine/__init__.py      backend/tests/db/test_system_mapping_api.py
backend/app/services/access.py             backend/tests/db/test_null_meridian_demo.py
frontend/lib/explanation.ts                frontend/tests/system-mapping.test.tsx
frontend/lib/identity.ts                   frontend/tests/explanation-factories.ts
frontend/components/Explanation.tsx        docs/architecture/effective-access.md
frontend/components/Identity.tsx           docs/demo/reports/ADG-DEMO-01-evidence/
frontend/app/identities/principal/page.tsx
```

## 11. SHAs for the next prompt

* Start: `1ab01a2058a8cb6d090bf5abeb6971b4b2ee33af`
* Implementation (code and tests, all gates above): `b949735c62ce42bcb1e0996b63ac11b2664345e1`
* Branch head (implementation + this report): see the final response, or
  `git log -1 origin/claude/adg-system-explanation-correctness-4iaijw`.
  ADG-DEMO-02 should build on the branch head.
