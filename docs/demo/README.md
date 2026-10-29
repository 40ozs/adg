# ADG demo guide

> **Synthetic data only.** All identities, organizations, directory objects, groups,
> resources, ACLs, findings, collection runs, provider responses, audit evidence, comparison
> data and credentials used by the ADG demo are synthetic or sanitized. No employer data,
> production credentials, customer information or live production directory data is
> included. The demo estate is **Genesee Press**, a generated company
> ([dataset](genesee-press.md)). No Active Directory, SMB server, Windows host or collector is
> connected, and nothing in the demo can change real infrastructure.

This page ties the reviewer walkthroughs together. It takes 10–15 minutes against a running
demo ([how to run it](../../README.md#run-the-demo)). Every step uses the real application:
the real API, access engine, risk rules and comparison code, reading the synthetic estate.

| Walkthrough | Question it answers | Detail |
| --- | --- | --- |
| [1. Access investigation](#walkthrough-1--access-investigation) | Why does this person have access, and how sure is ADG? | [access-investigation.md](access-investigation.md) |
| [2. Coverage and risk](#walkthrough-2--coverage-and-risk) | How complete is the evidence behind the numbers? | [coverage-and-recovery.md](coverage-and-recovery.md) |
| [3. Comparison failure and recovery](#walkthrough-3--comparison-failure-and-recovery) | What happens when a comparison cannot finish interactively? | [coverage-and-recovery.md](coverage-and-recovery.md#the-walkthrough) |

**Before you start.** Open `http://localhost:3000/login`, sign in as **`auditor`**, then
open `http://localhost:3000/demo`. A development deployment verifies no credential, and the
banner at the top of every page says so. The **Demo environment · Synthetic enterprise data ·
Genesee Press** pill in the header and the footer line mark every page as synthetic.

---

## Walkthrough 1 — Access investigation

**The question:** *Why does Fatima Gordon have access to*
`\\GP-ENG-FS01\Engineering\Projects\Apollo`*?*

| # | Do this | Where | What to notice |
| --- | --- | --- | --- |
| 1 | Open the seeded investigation | `/demo` → *Start here: a guided access investigation* → **Investigate access** | The page heading is the question. A note says the scenario is synthetic. |
| 2 | Read the result | `/access/investigate`, section 1 (`#observed-access`) | Access **granted**, rights **Modify**, certainty **`at_least`**, over SMB (share ACL and NTFS ACL). |
| 3 | Inspect the direct and derived routes | section 2 (`#access-routes`) | NTFS: Fatima → NTFS entry #1, a **direct, explicit** entry. Share: Fatima → `ENG-Mobile` → share entry #3, **through one group membership**. Open *Every other route ADG found* for the **inherited**, redundant route through five nested engineering groups. |
| 4 | Inspect provenance | section 2, **Evidence for hop N** under any hop | The collector (Active Directory, SMB share or NTFS), the first and last scan that observed the hop, and the stored row. |
| 5 | Inspect the missing evidence | section 3 (`#open-questions`) | `GPCORP\lhansen`, a former employee's SID that no collector could resolve, and `Proj-Zephyr-RW`, whose membership was never collected. Both read **Missing evidence** and **Does not affect the conclusion**. |
| 6 | Read the `at_least` semantics | sections 1, 3 and 4 (`#investigation-summary`) | Either gap could make Fatima's rights *wider*. Neither can take the established Modify away, so ADG reports the known rights as a lower bound instead of claiming certainty. |

Optional: section 6 (`#collection-coverage`) shows the four collection scopes this answer reads
(the directory, and `GP-ENG-FS01`'s shares, folders and local groups). All four were collected
completely, so the incomplete scopes elsewhere cannot change this answer.

To see ambiguity rather than a lower bound, open *Investigate a different pair* and ask about
`S-1-5-18` (SYSTEM) on `\\GP-FS-01\Shared`. Over SMB the answer is **Not determined**, with
mapping **ambiguous**, and never "No access" ([ADG-DEMO-01](reports/ADG-DEMO-01-report.md)).

## Walkthrough 2 — Coverage and risk

| # | Do this | Where | What to notice |
| --- | --- | --- | --- |
| 1 | Open the risk summary | `/risks` | 9,386 open findings: 3 critical, 8 high, 1,957 medium, 423 low, 6,995 informational. |
| 2 | Read how the counts may be read | banner `#risk-reading` | **These counts are not exhaustive.** ADG names the reason (collection is incomplete) rather than calling the counts totals. |
| 3 | Inspect collection completeness | *Collection behind these counts* (`#risk-coverage`) | `98 of 100 collection scopes collected completely · 1 unavailable · 1 partial · 1 holding older evidence`, a count of scopes, never a percentage. Also the snapshot the counts describe. |
| 4 | Inspect unavailable, partial and stale evidence | **Which scopes are not complete (2)**; `/collectors` for every scope | NTFS on `GP-FS-03` is **unavailable** (`host_unreachable`), and ADG still holds its 2026-09-17 scan (**older evidence**). NTFS on `GP-MIG-FS01` is **partial**. |
| 5 | Observe that findings remain counted | banner `#risk-reading` | The 189 open findings on `GP-FS-03` are still counted, from the scan that last read them. A failed scan closes nothing, and it is never shown as "checked, found nothing". |
| 6 | Observe the non-exhaustive reading | `#risk-coverage` | **Not exhaustive:** read these counts as what ADG holds, not as everything the estate contains. |

The two incomplete scopes are deterministic. They come from the dataset's own scan history,
not from a live failure.

## Walkthrough 3 — Comparison failure and recovery

| # | Do this | Where | What to notice |
| --- | --- | --- | --- |
| 1 | Launch the deterministic drill | `/demo` → **Run the comparison drill** | It compares the whole estate between the first and latest scans, inside a deliberately short 1 ms window that only the demo scenario uses. |
| 2 | Observe `timed_out` | `/changes/compare`, *How this comparison ended* | **Timed out — no comparison is presented.** No partial list is shown. **Diagnostics** shows the window, how far it got and what it read. |
| 3 | Retry and observe `incomplete` | **Retry inside the standard 4-second window** | **Incomplete — this is not the whole comparison.** The estate holds more objects per instant (13,765 and 14,139) than one comparison reads (5,000). Both sides are compared up to one named boundary and labelled as a part. |
| 4 | Narrow the scope | **Compare only folder permission entries (3,947 → 4,124 objects)** | A narrower comparison that fits inside the bounds. Each recovery action is a real request that names the attempt it recovers from. |
| 5 | Observe `recovered` and complete | the same panel | **Complete — this comparison recovered the earlier attempt**, with 221 differences. |

Partial output is never presented as complete, and the supported recovery is a narrower
scope. An estate-wide comparison of Genesee Press cannot finish inside one interactive
request. That is the bound working as intended (see [limitations](#limitations-to-keep-in-mind)).

---

## Where the claims are proven

| Claim | Tests (see [README: representative tests](../../README.md#representative-tests)) | Report |
| --- | --- | --- |
| SYSTEM is never answered with false certainty | `backend/tests/access_engine/test_system_mapping.py`, `backend/tests/db/test_system_mapping_api.py`, `frontend/tests/system-mapping.test.tsx` | [ADG-DEMO-01](reports/ADG-DEMO-01-report.md) |
| The investigation's routes, provenance and open questions | `backend/tests/access_engine/test_investigation.py`, `backend/tests/db/test_genesee_press_demo.py::TestTheAccessInvestigation`, `frontend/tests/investigation-page.test.tsx` | [ADG-DEMO-02](reports/ADG-DEMO-02-report.md) |
| Coverage, risk reading, bounded comparison and recovery | `backend/tests/domain/test_coverage.py`, `backend/tests/db/test_collection_coverage.py`, `backend/tests/db/test_comparison_bounds.py`, `backend/tests/db/test_genesee_press_demo.py::TestTheCoverageAndRecoveryDemo`, `frontend/tests/risks-page.test.tsx`, `frontend/tests/compare-page.test.tsx` | [ADG-DEMO-03](reports/ADG-DEMO-03-report.md) |

Screenshots of every step are in [ADG-DEMO-02-evidence](reports/ADG-DEMO-02-evidence/) and
[ADG-DEMO-03-evidence](reports/ADG-DEMO-03-evidence/), with the browser scripts that produced
them.

## Limitations to keep in mind

These are accepted limitations of the current demo, not hidden defects:

* Expected collectors are declared per collector **kind**, not per server.
* **Stale** means the latest scan of a scope delivered nothing (it failed or could not reach
  its target) while an earlier scan did. It is not an age threshold.
* The page-wide collection-health banner was not redesigned. Coverage sits beside the
  numbers it qualifies.
* An estate-wide demo comparison cannot finish inside one interactive request. The supported
  recovery is a narrower scope.
* Comparison bounds are in-process checkpoints inside the request, not a background job
  system.
* Remote-database behavior was modeled with injected per-statement latency, not measured on
  hosted production infrastructure.
* Browser validation used one-off evidence scripts, not a committed browser-test framework.
* SYSTEM over a share is never resolved to a computer account. `NETWORK SERVICE` and
  `LOCAL SERVICE` are not specially handled.

The full list, with reasons, is in each [report](reports/) and in
[the DEMO-PUBLIC-01 report](reports/DEMO-PUBLIC-01-report.md#15-known-limitations).
