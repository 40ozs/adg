# ADG-DEMO-02 — Guided access investigation walkthrough

**Verdict: `READY_FOR_ADG_DEMO_03_WITH_LIMITATIONS`**

A reviewer opens `/demo` and clicks **Investigate access**. That starts a guided
investigation of one synthetic question:

> *Why does Fatima Gordon have access to `\\NM-ENG-FS01\Engineering\Projects\Apollo`?*

The real access engine answers it over the reset Null Meridian estate. The investigation page
shows:

* the observed answer, kept separate from its explanation;
* the route that carries the access on each ACL, hop by hop, with the collector and scans
  behind every hop;
* two genuine evidence gaps, and why neither affects the conclusion;
* a seven-answer summary and the next steps.

Every acceptance criterion (§2.4) is met and proven by tests at the engine, API +
PostgreSQL, UI and browser layers. ADG-DEMO-01's SYSTEM invariants still pass. The
limitations in §9 are scope and environment limits. None of them weakens a conclusion the
walkthrough shows.

Building the walkthrough exposed one pre-existing correctness gap, and it is fixed here (§4.3).
The coverage check treated an ACL trustee stored as kind `unresolved` as "not a group". That
ruled the subject out of an orphaned SID on no evidence, so an answer could read `certain`
where it was a lower bound. The canonical folder names such a SID, so the walkthrough would
otherwise have understated its own uncertainty.

---

## 1. Repository state

| | |
| --- | --- |
| Repository | `40ozs/adg` |
| Branch | `claude/serene-mayer-itiwyi` (see note) |
| Worktree | `/home/user/adg`, the session's clone; no separate worktree |
| Starting branch | `claude/serene-mayer-itiwyi`, created by the session at `1ab01a2` (`origin/main`), then reset to the prerequisite before any change |
| Starting SHA | `c37ca5535d7bfba4d7f4f37af3e540ed44f7482b` (required ADG-DEMO-02 start; head of `claude/adg-system-explanation-correctness-4iaijw`) |
| Prerequisite | ADG-DEMO-01, implementation `b949735c62ce42bcb1e0996b63ac11b2664345e1`, verdict `READY_FOR_ADG_DEMO_02_WITH_LIMITATIONS` |
| `origin/main` | `1ab01a2058a8cb6d090bf5abeb6971b4b2ee33af` (unchanged; nothing merged to it) |
| Commits | `a7c9e9f` engine fix → `60bae4b` contract 1.1 + seed → `6196eaa` UI + docs → the commit adding this report (§10) |

**Branch name.** The preferred name was `feature/ADG-DEMO-02-access-investigation`. This
session's repository policy allows pushes only to `claude/serene-mayer-itiwyi`, so the work is
there. It was created from the required starting SHA and holds nothing else. Renaming it is
a ref operation: `git push origin claude/serene-mayer-itiwyi:feature/ADG-DEMO-02-access-investigation`.

**Prior work reconciled.** The prerequisite was read from the repository, not from session
storage:

* `docs/demo/reports/ADG-DEMO-01-report.md` and `ADG-DEMO-01-evidence/`;
* the ADG-DEMO-01 branch head (`c37ca55`), which equals the required start;
* the ADG-GRAPH-01A/01B/01C reports on their branches.

No ADG-DEMO-02 work existed on any remote head.

## 2. Scenario

### 2.1 The question, the principal, the resource

| | |
| --- | --- |
| Investigation question | *Why does Fatima Gordon have access to `\\NM-ENG-FS01\Engineering\Projects\Apollo`?* |
| Synthetic principal | Fatima Gordon, `S-1-5-21-3949361974-1583827165-633756269-1323`, engineering, hired before 2019 |
| Synthetic resource | `\\NM-ENG-FS01\Engineering\Projects\Apollo` over SMB (share `\\NM-ENG-FS01\Engineering`) |
| Golden scenarios | C (Engineering sprawl: the Apollo folder, historical direct entries) and H (`Proj-Zephyr-RW`, empty since 2019) |
| Entry point | `/demo`, *Start here: a guided access investigation*, **Investigate access** → `/access/investigate?principal=…&resource=…&access_path=remote_smb` |

The scenario reuses an existing authored folder: no new fixture names, and no change to the
estate, canon, manifest or fingerprint. The seed (`app.demo.null_meridian.scenarios.investigations`)
reads the principal off the folder's authored ACL by intent (`historical-engineer`), as
the estate placed it, and names the orphaned SID the same way (`former-user`). It carries no
expected answer.

Why Apollo rather than Scenario F (Brianna Reddy, `Finance\Reports\Revenue`)? F has the
cleanest direct-vs-derived story, but on the live estate its answer is `certain` with no open
question. The prompt requires at least one incomplete-evidence case, and F has none to show.
Apollo has both shapes and two genuine gaps. Both choices were measured on a live reset
before choosing (§2.3).

### 2.2 Expected effective access and the primary path

Pinned by `TestTheAccessInvestigation` against a real reset.

* **Effective access:** `granted`, **Modify**, certainty **`at_least`**, conclusive, ACL
  observed, enumeration complete. Both ACLs grant Modify, so no layer narrows it.
* **Primary path, NTFS:** `Fatima Gordon → NTFS entry #1 → folder`. A **direct**,
  **explicit** Modify entry, intent `historical-engineer`. Removing it was **measured to
  change nothing**.
* **Primary path, share:** `Fatima Gordon → ENG-Mobile → share entry #3 → folder`.
  **Derived** through one membership, Change. Removing the membership also changes
  nothing: her own share entry #17 is redundant behind it.
* **Derived (inherited) path, shown and redundant:** `Fatima Gordon → ENG-Mobile →
  Engineering-Frontend → Engineering → Engineering-FS → Share_Engineering_Modify → NTFS
  entry #5` (inherited from `\\NM-ENG-FS01\Engineering`). It is redundant because entry #1
  already settled every right it names. A seven-group detour runs through
  `APP-GitHub-Users`.
* **Open, not confirmed:**
  * `S-1-5-21-…-1108`, a former employee's account (`NULLMERIDIAN\lhansen`) that no collector
    could resolve, named on both ACLs;
  * `Proj-Zephyr-RW`, whose membership was never collected.

  Both are `missing`, both `extent`: they can only widen the rights.
* **Next steps:** identify the orphaned SID; then collect or confirm Zephyr's membership;
  then review the direct entry, which is a clean-up candidate because removing it changes
  nothing.

### 2.3 How the scenario was chosen (measured, not assumed)

All 136 direct-user explicit entries were explained on the live reset. Brianna on Revenue
(F) answered `granted Modify certain` with only the `assumed_token_sids` note. Apollo was one
of a handful with a contributing direct entry, a nested-group route and a directional
finding. It was also the only one authored for a scenario. Its orphaned SID then exposed the
§4.3 gap.

### 2.4 Acceptance criteria

| # | Criterion | Where it is proven |
| --- | --- | --- |
| 1 | Obvious way to start | `/demo` card + 8-step walkthrough (`DemoInvestigationList`); `/access` launcher; links from `/access/explain` and from scenario people. Browser: *demo guide offers the guided investigation* |
| 2 | Question clearly stated | page `h1` = the question (`investigationQuestion`); also on the demo card |
| 3 | Effective access shown | section 1 *What ADG evaluated*: verdict, rights, certainty, path, both ACLs, collection |
| 4 | A real ADG-derived path is inspectable | section 2, built from `/explain` `paths` + `graph` (the causality engine); DB test pins hop order |
| 5 | Direct vs inherited/derived distinguishable | two axes per route: `route` (direct/group/assumed/ownership) and `placement` (explicit/inherited/owner) |
| 6 | Provenance inspectable per hop | `evidence[]`, one per graph edge; a native `<details>` per hop: collector, method, first and last run and instant, run status, stored row |
| 7 | An ambiguous or incomplete case | the unresolved SID (`missing`) and `Proj-Zephyr-RW` (`missing`); SYSTEM's `ambiguous` and `conflicting` covered in tests |
| 8 | No manufactured certainty | certainty stays `at_least`; both gaps appear as open questions; the §4.3 fix; empty layers on inconclusive answers read "none established" |
| 9 | Says whether uncertainty affects the conclusion | per item (`impact`) and overall (`impact`, `affects_conclusion`), worded "Does not affect the conclusion…" |
| 10 | Concise summary | section 4: seven questions, seven answers, all from structured fields |
| 11 | Deterministic synthetic data | the seed is a pure function of the canon; repeat requests are byte-equal (`test_it_is_deterministic`); fingerprint unchanged |
| 12 | ADG-DEMO-01 intact | §7 |
| 13 | Accessible new UI | native `<details>`, named regions, labelled lists, words and symbols beside color; browser keyboard checks, no sideways scroll at 390 px (§6.4) |
| 14 | Tests pass | §6 |
| 15 | No employer, production, credential or customer data | synthetic Null Meridian only; evidence scanned (the only "password" match is the synthetic group `sg-okta-1password-user`) |
| 16 | Durable evidence | this report and [`ADG-DEMO-02-evidence/`](ADG-DEMO-02-evidence/) |

## 3. Architecture (what changed, and what deliberately did not)

There is no demo-only engine. The page makes one request to `GET /api/v1/access/explain`,
the same computation the explanation page uses, and contract 1.1 adds two fields to it:

```
/explain ─ AccessService.explain_access ─ resolve_access + explain_access (unchanged)
         ├ ProvenanceRepository.observed ─ one UNION ALL by key + run/collector join → evidence
         └ access_engine.investigate(explanation) → investigation
```

* **`app/access_engine/investigation.py`** is new and pure. It answers seven questions:
  * presence: the verdict, never "absent" while inconclusive;
  * primary routes: the strongest **contributing** grant path per layer. "Strongest" means
    the entry names the most of the final mask, then observed over assumed, then DACL
    order;
  * route kind and placement;
  * the measured removal effect of the route's first removable link;
  * open questions, each with a `state` and an `impact`;
  * deterministic next-step codes.
* **`app/repositories/provenance.py`** is new. It reads the provenance columns every stored
  row already carries, in one statement whose size the explanation's limits bound.
* **`app/api/access.py`** gains `HopEvidenceView`, `InvestigationView`, `PrimaryRouteView`,
  `OpenQuestionView` and `NextStepView`. `CONTRACT_VERSION` moves `1.0` → `1.1` (additive,
  per `docs/contracts/derived-responses.md`). Schemas, OpenAPI snapshots and captured
  examples are regenerated; examples are recaptured from live responses
  (`ADG_WRITE_EXAMPLES=1`). `FindingView`'s two direction descriptions had them backwards
  and are corrected.
* **Demo Mode** gains an optional `investigations()` dataset member, served on
  `GET /api/v1/demo` (`openapi.demo.json` regenerated).
* **Frontend:** `/access/investigate` (`app/access/investigate/page.tsx`),
  `components/Investigation.tsx` and `investigation.module.css` are new. So is
  `lib/investigation.ts`: wording only, no permission logic. Contract 1.1 types are in
  `lib/derived.ts` and `DemoInvestigation` is in `lib/contracts.ts`. `DemoGuide`, the
  launcher (`mode`), `/access` and `/access/explain` are changed.

These are deliberately unchanged: the causality engine, the resolver, the verdict, the
SYSTEM mapping, the Null Meridian estate and canon, the risk engine, the paths endpoint's
shape (other than its version), and every point-in-time and simulation repository.

## 4. Evidence model

### 4.1 Path provenance

`evidence` has one entry per edge of `graph`, in graph order:

| `basis` | Meaning | Carries |
| --- | --- | --- |
| `observed` | a stored row a collector reported | `record` (membership_edge / ntfs_ace / smb_share_ace / ntfs_resource), `record_key`, `source_key`, first and last observing run and instant, `collector` and `collection_method` of the last run, `last_run_status` |
| `assumed` | a SID Windows places in the token (Everyone, Authenticated Users, NETWORK) | nothing: no collector reports it and no row exists to edit |
| `projected` | an NTFS entry projected from an ancestor because the folder was never read | nothing; `effective.acl_provenance` is `derived` |
| `unrecorded` | no stored row backs the hop | nothing; treat it as unverified |

On Apollo, all 28 edges are `observed`:

* memberships by the **Active Directory collector**;
* NTFS entries by the **NTFS collector** (`System.IO.DirectoryInfo.GetAccessControl`);
* share entries by the **SMB share collector** (`Get-SmbShare`).

The direct NTFS entry was first observed 2026-09-03 (g1) and last observed 2026-10-01 (g3).
Collection context is shown as well: the run count, the basis token, and ADG's existing
coverage caveat for the estate (`NM-FS-03`'s failed g3 scan).

### 4.2 Direct and inherited/derived semantics

There are two independent axes, so "inherited" never has to mean two things:

* **reach:**
  * `direct` (the entry names the principal);
  * `group` (derived through N observed memberships);
  * `assumed` (derived through a token SID);
  * `ownership`.
* **placement:**
  * `explicit` (on this folder, or on the share, which has no inheritance);
  * `inherited` (set on a parent folder);
  * `owner`.

A redundant or constrained route is shown, but it is never the primary route. A route that
matched is not necessarily a cause.

### 4.3 Ambiguous and incomplete evidence

* **The orphaned SID.** Before this change, the coverage check
  (`AccessService._coverage_findings`) counted a trustee as "might be a group" only when it
  had no record or a group kind. A record of kind `unresolved` exists only to say that no
  authority could resolve the SID, so it holds no more knowledge of the kind than a missing
  row. `_could_contain_members` now treats it like a missing row. This is the same line
  `SubjectFacts.resolved` already draws for the subject.

  On Apollo this adds a second `trustee_membership_unobserved`; certainty stays `at_least`
  because Zephyr was already open. Estate-wide, an answer over an ACL naming an unresolved
  SID can no longer read `certain`. The risk engine does not use this check, so risk findings
  and the demo fingerprint are unaffected (the full DB suite and `TestRepeatability` pass,
  §6). The condition's sentence now names both cases.
* **`Proj-Zephyr-RW`.** It is named on the share ACL, and ADG holds no member for it. Empty,
  or never enumerated? ADG cannot tell, so it does not rule Fatima out.
* **Assumed token SIDs** are shown as `assumed`, impact `none`, recorded for context.

### 4.4 Does the uncertainty affect the conclusion?

**No.** Both open questions are `understating` (`may_understate` only). On a granted answer
that is impact `extent`: they could make Fatima's rights *wider*, and they cannot take the
established Modify away. The summary says so in words:

> Access is established through the routes shown. 2 open questions remain, and none of them
> can take that access away: they mean the rights shown are a lower bound.

Impact is decided by direction alone, never by the display `state`:

* on a granted answer, an overstating finding is `presence` (the access could narrow to
  nothing), and an understating-only finding is `extent`;
* without access, only a finding that could hide a grant is `presence`.

The tests cover the case where uncertainty *does* matter (an unread share ACL → `presence`,
"provisional"), and the SYSTEM cases (§7).

## 5. UI walkthrough (reviewer steps, routes, components)

| # | Step | Route / anchor | Component |
| --- | --- | --- | --- |
| 1 | Open the seeded investigation | `/demo` → **Investigate access** | `DemoInvestigationList`, `WALKTHROUGH` (`components/DemoGuide.tsx`) |
| 2 | Read the access question | `/access/investigate` `#question` | `InvestigationQuestion` (+ synthetic-data note when `data_source = synthetic`) |
| 3 | Inspect effective access | `#observed-access` | `ObservedAccessPanel` |
| 4 | Expand the primary path | `#access-routes` | `RoutesPanel` → `PrimaryRoute` → `HopList` |
| 5 | Inspect path provenance | per hop, **Evidence for hop N** | `HopItem` (`<details>`) |
| 6 | Inspect the ambiguous or incomplete evidence | `#open-questions` | `OpenQuestionsPanel` (unresolved identities via `PrincipalName`) |
| 7 | Read the summary | `#investigation-summary` | `SummaryPanel` (`investigationSummary`) |
| 8 | Follow the next action | `#next-steps` | `NextStepsPanel` → principal pages, `/access/explain#removals` |

Other surfaces lead to the same page:

* every other route (redundant, constrained, alternative) is one disclosure away;
* every caveat on the full explanation is one disclosure away;
* the full explanation (diagram, both ACLs, removal analysis) is one link away;
* `/access/explain` links back to the investigation.

Screenshots: [`01-demo-guide-entry.png`](ADG-DEMO-02-evidence/01-demo-guide-entry.png),
[`02-investigation-desktop.png`](ADG-DEMO-02-evidence/02-investigation-desktop.png),
[`03-investigation-phone.png`](ADG-DEMO-02-evidence/03-investigation-phone.png),
[`04-system-over-share.png`](ADG-DEMO-02-evidence/04-system-over-share.png).

## 6. Tests

Run in this cloud container: Python 3.13.16, PostgreSQL 16 (local cluster, restarted for this
session), Node 22.22, Chromium 1194 (pre-installed). The commands are CI's
(`.github/workflows/ci.yml`).

### 6.1 Backend

| Gate | Result |
| --- | --- |
| `ruff check .` | **passed** |
| `ruff format --check .` | **passed** (406 files) |
| `mypy app tests` | **passed** (405 files) |
| ADG-DEMO-01 engine regressions (`test_system_explanation_regression` 52 + `test_system_mapping` 49 + `test_resolver` 44) | **145 passed** (the prerequisite's 145) |
| ADG-DEMO-02 engine: `test_investigation.py` (93, of which 72 are the SYSTEM sweep: 4 descriptions × 3 share shapes × 3 assumptions × 2 access paths) + `test_unidentified_trustees.py` (9) | **102 passed** |
| ADG-DEMO-02 dataset seed: `tests/demo/test_null_meridian_investigation.py` | **6 passed** |
| `tests/access_engine` (all) | **694 passed, 1 skipped** |
| `tests/contracts` (unit) | **611 passed, 109 skipped** (schemas, examples, OpenAPI snapshots) |
| `pytest -m "not smoke"` (full unit suite), on `6196eaa` | **5974 passed, 124 skipped, 1237 deselected** (prerequisite at `b949735`: 5866 / 124 / 1227; +108 new unit tests, and +10 new DB tests deselected here) |

### 6.2 Database (PostgreSQL)

| Gate | Result |
| --- | --- |
| `TestTheAccessInvestigation` (10, new: seed, observed access, both routes, hop order, five-group chain, measured removals, per-hop provenance, open questions and impact, next steps, determinism, SYSTEM through the summary) + `TestTheSystemExplanation` (2) + `TestTheGoldenScenariosThroughTheApi` (11) on a real Null Meridian reset | **23 passed** |
| `test_explanation_api.py` + `test_access_paths_api.py` (examples recaptured under 1.1; the query-cost pin now names the one provenance statement) | **61 passed** |
| `pytest -m smoke` (full DB suite), on `6196eaa` | **1236 passed, 1 xfailed**, exit 0, 28m52s (prerequisite at `b949735`: 1226 passed, 1 xfailed; +10 = `TestTheAccessInvestigation`; the xfail is the same pre-existing one). Includes `test_system_mapping_api.py` (10), `TestTheSystemExplanation` (2) and `TestRepeatability` (a second reset reproducing the fingerprint) |

PostgreSQL was stopped when the container started. It was started with
`pg_ctlcluster 16 main start`, and the CI role and databases were created. That is an
environment step, not a product failure.

### 6.3 Frontend

| Gate | Result |
| --- | --- |
| `npm run lint` | **passed** |
| `npm run typecheck` | **passed** |
| ADG-DEMO-01 UI set: `system-mapping` 23 + `explanation` 82 + `explanation-views` 49 + `identity` 17 | **171 passed** (the prerequisite's 171) |
| `explanation-page` 15 + `explanation-contract` 97 (contract 1.1 fields, enums, and the captured 1.1 body through the investigation renderers) | **112 passed** |
| New: `investigation.test.ts` 20 + `investigation-page.test.tsx` 24 + `demo-guide.test.tsx` 14 (5 new) | **58 passed** |
| `npm run test` (full) | **1259 passed** (36 files; prerequisite 1186) |
| `npm run build` | **passed** (`/access/investigate` built) |

The page tests render the real page component, with only the API boundary stubbed. They
cover:

* the entry point and the launcher's mode;
* the question and the synthetic-data notice, which is absent on production;
* observed access kept separate from its explanation;
* hop order;
* provenance disclosure, opened by click;
* the open questions as words and symbols;
* an unresolved identity rendered unresolved, with `status-warn`;
* the context list;
* the conclusion-affecting variant;
* the seven answers and the next-step links;
* a 1.0 body handled honestly;
* SYSTEM ambiguous and conflicting, never "No access", including an empty layer;
* named regions;
* Tab order.

### 6.4 Browser (end to end, real stack)

The repository has **no** browser framework, and none was added. Like the earlier
integration handoff, this used a one-off script (kept as evidence,
[`browser-walkthrough.script.js`](ADG-DEMO-02-evidence/browser-walkthrough.script.js), not wired
into any suite). It ran the global Playwright with the pre-installed Chromium against:

* the API in demo mode on the reset `adg_demo` database (fingerprint `228b9dce…`, equal to
  the committed reset evidence);
* `next start` of the production build.

Result: **29 of 29 checks passed**
([`browser-walkthrough.json`](ADG-DEMO-02-evidence/browser-walkthrough.json)). Among them:

* the demo card and its 8 steps, followed by the click-through;
* the question and the synthetic notice;
* observed access;
* both routes;
* **Tab reaches the first hop's evidence disclosure, Enter opens it, Enter closes it, and it
  shows a focus outline**;
* two missing-evidence items, neither affecting the conclusion;
* the orphaned SID kept unresolved, and its principal page showing ADG-DEMO-01's unresolved
  banner;
* 7 summary answers and 3 next steps, followed to the removal analysis;
* SYSTEM over SMB `Not determined` / `Ambiguous` and never "No access"; SYSTEM locally
  `Confirmed`;
* no horizontal scroll at 390 px;
* no application console errors.

One console error is recorded and attributed: the browser's own `/favicon.ico` request 404s,
because the application ships no favicon. That predates this change.

### 6.5 CI

GitHub Actions, CI run 64 on `2b25b84` ([run](https://github.com/40ozs/adg/actions/runs/38645166406)).
That commit is `6196eaa` plus this report and its evidence: 11 documentation files, no code.
**Every job passed:**

* Backend: lint, type check, unit tests, and **database tests** (22:08–22:30 UTC);
* Collector (Pester, `windows-latest`);
* Frontend: lint, types, tests, build;
* Docker Compose configuration.

CI run 63 on `6196eaa` itself passed Collector, Frontend, Compose, and the backend's lint,
types and unit tests. Its database step was still running on its runner more than 30
minutes after it started, while run 64's identical step had already passed. Its final
conclusion is on the run page.

## 7. ADG-DEMO-01 regression status

**The SYSTEM correctness invariants remain passing.** Their tests were not modified, and all
of them run in the gates above:

* the prerequisite's engine set (145): §6.1;
* its 10 API/PostgreSQL mapping tests (`test_system_mapping_api.py`), in the full DB suite;
* its demo-estate pair (`TestTheSystemExplanation`, 2): §6.2;
* its UI set (171): §6.3.

The investigation adds no opinion of its own about SYSTEM:

* presence is the verdict's outcome;
* states are the mapping finding's (`test_no_system_answer_is_summarized_as_absent` sweeps
  description × path × assumption × share shape);
* the UI reuses ADG-DEMO-01's next-step wording for unsettled states;
* an empty layer on an inconclusive answer reads "none established".

Stored-but-unresolved identities stay visibly unresolved in the open questions, in hop
principals and on their principal page.

## 8. Parallel-branch assessment

Fresh `git fetch --prune`, then write-free trial merges (`git merge-tree --write-tree`)
against the ADG-DEMO-02 head `6196eaa`:

* **18 remote heads besides `main`** (and this branch). 5 are already contained in the
  head: the Demo integration head, the ADG-DEMO-01 branch (`c37ca55`, the start),
  `dazzling-keller`, `inspiring-volta` and `vibrant-gates`.
* **13 unmerged.** None touches `backend/app/access_engine/`.
* **11 conflict**, every one in `frontend/app/identities/principal/page.tsx`, the conflict
  ADG-DEMO-01 already recorded. This work does not modify that file:
  * `adoring-cannon`
  * `compassionate-hamilton`
  * `deploy/ADG-FREE-STAGING-01`
  * `feature/ADG-GRAPH-01A`
  * `feature/ADG-GRAPH-01B`
  * `feature/ADG-GRAPH-01C-path-engine`
  * `feature/ADG-GRAPH-01C-path-engine-claude`
  * `fix/ADG-NEXT16-REACT418-01`
  * `fix/ADG-UI-COMPACT-IDENTITY-01`
  * `integration/ADG-NEXT16-UI-02`
  * `integration/ADG-UI-EDGE-01`
* **New since ADG-DEMO-01:** `adoring-cannon` and `compassionate-hamilton` (directory-access
  and release-gate work) also conflict in the generated `docs/contracts/v1/openapi.json`,
  because this change regenerated it. The resolution is mechanical:
  1. take either side;
  2. run `python -m app.contracts.openapi` and `python -m app.contracts.derived`;
  3. run `tests/contracts`.

  The GRAPH-01B/01C branches also edit `access-explanation.schema.json` (`path_principals`).
  They merge textually but must be regenerated after a merge for the same reason.
* **2 merge cleanly:** `claude/wizardly-gates-6yjdxp` and `deploy/ADG-FREE-EDGE-SSR-03`
  (deployment work).

**Nothing was integrated.** The GRAPH-01C path engines walk *persisted privilege
relationships* (a separate graph, built on the Next 16 and staging stack). The investigation
explains *file-system effective access*, which the existing causality engine already does.
Merging 150+ unreviewed files was not needed and is out of scope. No conflict was resolved,
so `frontend/app/identities/principal/page.tsx` is untouched. When it is integrated, follow
ADG-DEMO-01 §1: take the newer page structure and reapply the `isUnresolved(principal)`
banner. Then rerun the SYSTEM, identity and investigation tests (§6).

## 9. Limitations

**Product**

1. **Provenance freshness is shown, not judged.** Current state keeps a row until a complete
   scan proves it gone, so a hop's `last_observed_at` can predate the latest run, for
   example on `NM-FS-03`, whose g3 NTFS scan failed. The page shows the dates and the
   estate-level coverage caveat. The summary does not treat a stale hop as an open
   question. Doing so is collection coverage: ADG-DEMO-03.
2. **One seeded investigation.** The dataset contract allows many, and the page works for any
   pair. Scenario F (direct vs derived, no uncertainty) and G (six-group chain) are reachable
   from the guide's "investigate it step by step" links. They are not curated
   investigations.
3. **"Strongest" is a defined ranking:** coverage of the final mask, then observed over
   assumed, then DACL order. It is not a risk judgment. The other routes are always one
   disclosure away.
4. **An unresolved SID's next step is "identify it".** ADG cannot tell a deleted account from
   a foreign or deleted group, so it cannot say which (§4.3).
5. **The estate-wide effect of §4.3:** answers over ACLs naming an unresolved SID now read
   `at_least`, honestly, where they could read `certain` before. No stored data, risk finding
   or fingerprint changes.
6. **ADG-DEMO-01 limits carry over unchanged:** SYSTEM over a share is never resolved to a
   computer account; `confirmed` is a lower bound; `resolved: true` still means "a record
   exists".

**Environment**

1. **Not run locally:** the Collector Pester suite (no Windows host) and
   `docker compose config` (no Docker). CI runs both (§6.5).
2. **Browser validation used a one-off script.** The repository has no E2E framework, and
   adding one was out of scope.
3. **The favicon 404 is pre-existing** (§6.4).

**Deferred to ADG-DEMO-03 (not attempted here)**

1. risk-total redesign and collection-coverage badges, including provenance freshness (product 1);
2. comparison performance, timeouts and recovery, and asynchronous comparison;
3. SYSTEM-to-computer-account correlation, `NETWORK SERVICE`, `LOCAL SERVICE`, SYSTEM's
   groups;
4. any broad graph-engine redesign, or integration of the GRAPH-01 branches.

## 10. Handoff

* **ADG-DEMO-03 should start from:** the head of `claude/serene-mayer-itiwyi` that contains
  this report. `git log -1 origin/claude/serene-mayer-itiwyi` gives it, and the final
  response states it.
* **Code and tests, all gates above:** `6196eaa`. The report commit adds only this file and
  `ADG-DEMO-02-evidence/`.
* Key files:

```
backend/app/access_engine/investigation.py     frontend/app/access/investigate/page.tsx
backend/app/repositories/provenance.py         frontend/components/Investigation.tsx
backend/app/api/access.py  (+ caching, demo)   frontend/components/investigation.module.css
backend/app/services/access.py (_could_contain_members)   frontend/lib/investigation.ts
backend/app/demo/null_meridian/scenarios.py    frontend/components/DemoGuide.tsx
backend/app/demo_mode/datasets.py              frontend/lib/derived.ts, lib/contracts.ts
backend/tests/access_engine/test_investigation.py         frontend/tests/investigation*.ts(x)
backend/tests/access_engine/test_unidentified_trustees.py frontend/tests/demo-guide.test.tsx
backend/tests/demo/test_null_meridian_investigation.py    docs/demo/access-investigation.md
backend/tests/db/test_null_meridian_demo.py (TestTheAccessInvestigation)
docs/contracts/derived-responses.md, docs/contracts/v1/** (1.1)
```
