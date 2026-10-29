# The guided access investigation (ADG-DEMO-02)

Part of the [ADG demo guide](README.md) (Walkthrough 1). This page is the detailed account.

> **Synthetic.** Every identity, group, folder, SID and scan here is generated Genesee Press
> demo data. None of it describes a real person, organization or system.

**The question:** *Why does Fatima Gordon have access to*
`\\GP-ENG-FS01\Engineering\Projects\Apollo`*?*

ADG's reviewer-facing demo opens with one access-troubleshooting question and answers it
with the real application: the real access engine, the real explanation API, and the real
pages over the reset Genesee Press estate. It is designed to be read in about five minutes.
It answers which route carries the access, what evidence supports each hop, what ADG could
not confirm, whether that matters, and what to inspect next.

## Starting it

* **From the demo guide:** `/demo` → *Start here: a guided access investigation* →
  **Investigate access**.
* **Directly:** `/access/investigate?principal=<SID>&resource=<UNC path>`. This works for
  any pair, not only the demo's, and so does the launcher on `/access`.
* **From an explanation:** every `/access/explain` page links to *Read this answer as a
  guided investigation*, and every investigation links back to the full explanation.

## The five-minute walkthrough

| # | Step | Where on `/access/investigate` |
| --- | --- | --- |
| 1 | Open the seeded investigation | `/demo`, **Investigate access** |
| 2 | Read the access question | page heading (`#question`) |
| 3 | Inspect what ADG evaluated: effective access, rights, certainty, both ACLs, collection | section 1 (`#observed-access`) |
| 4 | Read the route that carries the access on each ACL: direct or through groups, explicit or inherited | section 2 (`#access-routes`) |
| 5 | Open **Evidence for hop …** under any hop: the collector, the scans, the stored row | section 2, per hop |
| 6 | Inspect what ADG could not confirm, and whether it affects the conclusion | section 3 (`#open-questions`) |
| 7 | Read the investigation summary: seven questions, seven answers | section 4 (`#investigation-summary`) |
| 8 | Follow the suggested next action | section 5 (`#next-steps`) |

The demo guide renders the same eight steps, each linked to its section.

## The fixture

The Apollo folder was authored in scenario C, *Engineering sprawl*, to make the question
honest rather than easy (`backend/app/demo/genesee_press/estate.py`). It needs no new
fixture names and no change to the estate.

| ACL | Entries that matter here |
| --- | --- |
| NTFS, `\\GP-ENG-FS01\Engineering\Projects\Apollo` | `Proj-Apollo` (Modify); **a veteran engineer, directly** (Modify, intent `historical-engineer`); **a former employee's account** (Modify, intent `former-user`; deleted before ADG first scanned, so stored as an `unresolved` SID, last known `GPCORP\lhansen`); five inherited engineering grants, among them `Share_Engineering_Modify` |
| Share, `\\GP-ENG-FS01\Engineering` | every engineering team at Change, `ENG-Mobile` among them; the veteran engineer directly; the former employee's SID; **`Proj-Zephyr-RW`** (scenario H: no members since 2019, so ADG holds none) |

The seed (`app.demo.genesee_press.scenarios.investigations`) reads the principal off that
ACL by intent, the same way the estate placed them. It names a pair and says nothing about
the answer. It reaches the UI through Demo Mode's optional `investigations()` contract and
`GET /api/v1/demo`.

## The answer, as ADG computes it

Pinned against a real reset in
`backend/tests/db/test_genesee_press_demo.py::TestTheAccessInvestigation`.

* **Observed:** `granted`, **Modify**, certainty **`at_least`**, conclusive, over SMB. Both
  ACLs grant Modify, so no layer narrows the answer.
* **NTFS route:** Fatima Gordon → NTFS entry #1. This is a **direct** entry, set
  **explicitly** on the folder. Removing it was measured to **change nothing**.
* **Share route:** Fatima Gordon → `ENG-Mobile` → share entry #3. This route is **derived**
  through one group membership, and removing it also changes nothing.
* **Derived, redundant:** Fatima Gordon → `ENG-Mobile` → `Engineering-Frontend` →
  `Engineering` → `Engineering-FS` → `Share_Engineering_Modify` → an **inherited** Modify
  entry. It is redundant because the direct entry ahead of it already settled every right
  it names. A seven-group detour runs through `APP-GitHub-Users`.
* **Not confirmed (both `missing`, both `extent`):**
  * the former employee's SID, which no collector could identify;
  * `Proj-Zephyr-RW`, whose membership was never collected.

  Either could make Fatima's rights *wider*. Neither can take the established access away.
* **Next:** identify the orphaned SID, then collect or confirm Zephyr's membership, then
  review the direct entry. Removing it changes nothing, which makes it the clean-up
  candidate.

## Architecture: one computation, no demo-only engine

```
GET /api/v1/access/explain ── AccessService.explain_access ── resolve_access + explain_access   (unchanged)
                          ├── ProvenanceRepository.observed ── one UNION ALL by key  → evidence
                          └── access_engine.investigate(explanation)                 → investigation
```

* **Paths** come from `app.access_engine.causality`, the engine that already explained
  every answer (see [access causality](../architecture/access-causality.md)). Nothing in
  this feature evaluates an ACL or matches a trustee.
* **`investigation`** (`app/access_engine/investigation.py`) is a pure function of the
  explanation:
  * presence is read off the verdict;
  * the primary route is the contributing grant path, per layer, whose entry names the
    most of the final mask, with DACL order breaking ties;
  * the removal effect is read from the engine's measured removal targets.
* **`evidence`** (`app/repositories/provenance.py`) reads the provenance columns every
  stored row already carries: `source_key` and the first and last observation, as run and
  instant. Each row is joined to the run and collector that last observed it, in one
  statement whose cost the explanation's own limits bound.
* **Frontend:** `/access/investigate` (`frontend/app/access/investigate/page.tsx`,
  `components/Investigation.tsx`, `lib/investigation.ts`) makes the same single request as
  `/access/explain` and words the engine's fields. It decides nothing (see
  [the one rule](../contracts/derived-responses.md#the-one-rule)).

The contract is described in
[derived responses, `evidence` and `investigation`](../contracts/derived-responses.md#evidence-and-investigation-contract-11).

## Uncertainty: state, impact, and why they are separate

Each finding becomes an open question with two independent properties:

* **`state`** is the vocabulary ADG-DEMO-01 fixed for SYSTEM (`confirmed`, `ambiguous`,
  `missing`, `conflicting`), plus `assumed`, `derived` and `context`. It is a display
  classification.
* **`impact`** is decided by the finding's **direction** alone:
  * On a granted answer, a finding that may only *understate* is `extent`: the access stands
    and could be wider. One that may *overstate* is `presence`: the access could narrow to
    nothing.
  * Without access, only a finding that could hide a grant is `presence`.

A mislabelled state therefore cannot produce a wrong conclusion. The UI writes every state
as a word and a symbol beside its color.

A stored-but-unresolved identity stays visibly unresolved (ADG-DEMO-01). Building this demo
found that the coverage check treated a trustee record of kind `unresolved` as "not a
group". That ruled the subject out of an orphaned SID on no evidence. It now counts as
possibly a group, the same way a SID with no record does
([effective access §6](../architecture/effective-access.md#the-condition-that-hides-findings)).

## Relationship to ADG-DEMO-01

The summary has no opinion of its own about SYSTEM. Its presence is the verdict's outcome
and its states are the mapping finding's, so:

* SYSTEM over a share is **undetermined**, its mapping **ambiguous**, with no route and
  "settle the mapping" as the next step;
* SYSTEM locally is **present**, its mapping **confirmed**, with `extent` impact (a lower
  bound).

Engine tests sweep every description × path × assumption × share-shape cell, and the
reset-estate test repeats the original request. Neither ever shows "absent".

## Not in scope

ADG-DEMO-03 added the collection behind the answer: section 6 of the page names the scopes this
answer reads, whether any is incomplete, and whether that could change the conclusion. See
[coverage and recovery](coverage-and-recovery.md). Still deferred:

* SYSTEM-to-computer-account correlation;
* `NETWORK SERVICE` and `LOCAL SERVICE`.

The current-state rules keep a row until a complete scan proves it gone. Provenance shows
when each hop was last observed, and section 6 says whether the scope that hop came from is
current; the summary itself still does not treat an older observation as an open question
(the answer's own certainty is unchanged by collection context).
