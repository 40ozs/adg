# ADG-WAVE-D-STAGING-RELEASE-01 — Wave D on the ADG demo staging

## 1. Verdict

**`ADG_WAVE_D_STAGING_RELEASE_VERIFIED_WITH_LIMITATIONS`**

**`WAVE_D_HOSTED_STREAMING_REGRESSION_PASS`**

The verified ADG Wave D baseline was deployed to the existing public demo staging:

- **Release:** `genesee-press-2.1.0-bb370f8e4531` (adg-demo `5c7805e`).
- **Pipeline:** the existing synthetic-enterprise → adg-demo pipeline and the normal
  `Free staging (ADG demo)` workflow, run with **`bootstrap=false`**.
- **No database reset:** the hosted dataset is byte-for-byte unchanged (§13).
- **Hosted checks:** smoke, the workflow's browser gate, the streaming regression and the hosted
  product checks all pass.

**The main limitation (§21)** is that the canonical Genesee Press dataset holds **no review
campaign**, and the public accounts cannot create one. Wave D's two user-visible changes therefore
cannot be clicked through on hosted:

- campaign → item client navigation;
- review scope names.

The owner chose to keep hosted canonical rather than seed a campaign. On hosted, the evidence for
those two changes is:

- the deployed tree is byte-identical to the locally verified Wave D;
- the hosted client bundle carries the NAV-07 change;
- the surfaces hosted *can* show pass.

`adg` `main` is untouched. Production was not deployed.

```
Client ─► Cloudflare edge ─► Worker adg-demo-staging ─► Render edge ─► render/gate.ts ─► Next.js (+ FastAPI on loopback)
```

Evidence: [`ADG-WAVE-D-STAGING-RELEASE-01-evidence/`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/). The
release and deployment record is [`deployment.txt`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/deployment.txt).

## 2. ADG source baseline

| | |
|---|---|
| Branch | `origin/integration/ADG-UX-WAVE-D-01` (verdict `READY_FOR_ADG_UX_WAVE_E_WITH_LIMITATIONS`) |
| Baseline SHA | `bb370f8e4531a15166006a1f79f9ca2d64e11a3a`. It is a docs-only commit over the tested tree. `git diff fc7c7a2 bb370f8` touches only the Wave D report |
| Code/report SHA | `fc7c7a29f7254ae657e37c287ffb4f75f1ef4be5` |
| Newer baseline? | **None.** After `git fetch --prune`, the only `integration/ADG-UX-*` refs are Waves A–D, and Wave D's head is `bb370f8`. No feature branch was used |
| Report read | [`ADG-UX-WAVE-D-INTEGRATION-01.md`](ADG-UX-WAVE-D-INTEGRATION-01.md) §21. The hosting paths are byte-identical to Wave C, so the brief asks for only the small post-deploy streaming regression |

## 3. Source CI

- **Tested tree.** Run [41223498140](https://github.com/40ozs/adg/actions/runs/41223498140) on
  `fc7c7a2` was a **success**:
  - Backend: unit 6052; DB 1306 passed + 1 xfailed.
  - Frontend: 1696.
  - Pester and Compose also passed.
- **The docs-only head.** Its own push run,
  [41228396705](https://github.com/40ozs/adg/actions/runs/41228396705) on `bb370f8`, is recorded in
  §23.

## 4. Synthetic-enterprise spec update

The spec is `tools/cutover/specs/adg.json` on branch **`release/ADG-WAVE-D-STAGING-01`**, cut from
SE `main` `a6e63f9`. Commit **`e50115c`** sets:

| Field | Value |
|---|---|
| `lane` | `ADG-WAVE-D-STAGING-RELEASE-01` |
| `release_id` | `genesee-press-2.1.0-bb370f8e4531` |
| `source_branch` | `integration/ADG-UX-WAVE-D-01` |
| `application_upstream_sha` | `bb370f8e4531a15166006a1f79f9ca2d64e11a3a` |
| release note | 79ec257's "deploys with `bootstrap: false` over the existing dataset" sentence, carried over. It replaces `main`'s stale "first deploy runs with `bootstrap: true`" |

Everything else is identical to SE `main`: the lock expectation (`ce7c0c7`, `adg@0.3.0`, dataset
2.1.0), the include/exclude rules, the scan exceptions and the expected fingerprints.

- **The SHA choice.** The spec pins the docs-only baseline head `bb370f8`, not the tested tree
  `fc7c7a2`. The precedent is HOSTED-STREAMING-11, which pinned Wave C's docs-only head `559dbaf`
  over its tested `52024ea`.
- **No difference in what ships.** The exporter excludes `docs/demo/reports/`, so the released
  files are the same for both SHAs. The 901 application files equal `git show bb370f8:<path>`.
- **The old Wave C spec.** `79ec257` (`release/ADG-HOSTED-STREAMING-11`, pointing at `559dbaf`) was
  read as reference and **not merged**. No newer spec than `79ec257` existed.

## 5. Spec merge status

| | SHA |
|---|---|
| SE `main` before | `a6e63f9` |
| Spec commit | `e50115c` |
| Merge into SE `main` | **`113d3b1`**: `--no-ff` merge of `release/ADG-WAVE-D-STAGING-01`, the repository's convention (see `196b672` for CSW-HOSTED-DEMO-01). Pushed as a fast-forward of `main`, with no force push |
| `79ec257` in SE `main`? | **NO** |
| Re-check from SE `main` | `demo_release.py compare` against adg-demo `5c7805e`: `identical: true` |

SE has no CI workflow and no branch protection (SYNTH-DEFAULT-BRANCH-01). The merge was made only
after the deploy and the hosted checks had passed, so SE `main` describes a live release.

## 6. Release ID

**`genesee-press-2.1.0-bb370f8e4531`**. The convention is `genesee-press-<dataset version>-<first 12
hex of application_upstream_sha>`, as in `…-aebf16fcf594` and `…-559dbaf913a0`.

## 7. adg-demo SHA

`40ozs/adg-demo` `main` went from `4e565da` (Wave C) to **`5c7805e423fd1632b310502d07a926b9b221a421`**,
"Release genesee-press-2.1.0-bb370f8e4531". It was pushed normally.

**Export** (`demo_release.py export --generated-at 2026-10-31T18:24:16Z`):

- 906 files, 0 redactions, and 25 accepted scan findings (the same 25 as Wave C);
- `verify_release.py` reports OK, with 905 files matching;
- the 901 application files were byte-checked against `git show bb370f8:<path>`: **0 mismatches**.

**Reproducibility.** `demo_release.py compare` re-exports and compares with the committed tree. It
reports `identical: true`, 906/906.

**Diff against the live release**
([`export-diff.txt`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/export-diff.txt)) is 16 files:

- The **13 application files** are exactly the 13 non-report paths of `559dbaf..bb370f8`:
  - NAV-07: `components/DecisionForm.tsx`.
  - REVIEW-NAMES-08: `api/governance.py`, `api/scan_runs.py`, `openapi.json`, `lib/contracts.ts`,
    `lib/governance.ts`, the campaign page comment, and tests including the new
    `tests/contracts/test_scope_display_name.py`.
  - The titles gate `frontend/cloudflare/checks/titles.mjs`, which is test tooling under the
    exported `frontend/**`.
- The **3 generated files** are `DEMO-RELEASE.md`, `demo-release.json` and `demo-release/files.sha256`.

**What did not ship.** Nothing from `docs/demo/reports/`, which holds the local
`setup-fixtures.sh`, `fixtures.json` and the evidence. No developer database state either: the
exporter reads committed git objects only.

**The hosting path is unchanged**
([`hosting-blobs.txt`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/hosting-blobs.txt)). These have the
same git blob in live `4e565da`, new `5c7805e` and source `bb370f8`:

- the Worker: `worker.ts`, `routing.ts`, `environment.ts`;
- `render/gate.ts`, `next.config.mjs`, `wrangler.jsonc`;
- `Dockerfile.render`, `Dockerfile.backend` and the staging workflow;
- the Overview: `app/page.tsx`, `layout.tsx`, `Overview.tsx`, `overview.module.css`,
  `RecentlyViewed.tsx`;
- `package-lock.json`, `pyproject.toml`, the lock and the manifest;
- the `database/` and `backend/vendor/` trees.

## 8. Release CI

"Demo release verification" run
[41229450920](https://github.com/40ozs/adg-demo/actions/runs/41229450920) on `5c7805e` succeeded
(Release integrity and provenance). The source mapping is in the release itself: `demo-release.json`
records adg `bb370f8` and synthetic-enterprise `ce7c0c7`.

## 9. Deployment workflow

"Free staging (ADG demo)" was dispatched on `main` with **`bootstrap=false`** and no `confirm_reset`.
Run [41229548980](https://github.com/40ozs/adg-demo/actions/runs/41229548980) started at 18:28:01Z
and succeeded:

| Job | Result |
|---|---|
| Preflight | ✓ |
| Code gates | ✓ |
| Bootstrap | **skipped**, so no reset |
| Render origin deploy | ✓ (18:28:31–18:31:17Z) |
| Worker deploy | ✓ (18:31:20–18:33:27Z) |
| Hosted checks | ✓ (18:33:35–18:36:01Z) |

## 10. Render deploy

The Render API shows **`dep-dbj36k67bikc73e9rskg` live at adg-demo `5c7805e423fd…`**. It was created
at 18:28:33Z and finished at 18:31:13Z. The previous deploy, `dep-dbj177142hec73boioj0`, is now
deactivated. The service settings are unchanged: repo `40ozs/adg-demo`, branch `main`, auto-deploy
off.

## 11. Worker version

The Cloudflare API shows deployment `b9e13ffb-0a2a-4046-a356-8004bf5ee5b7`, created at 18:33:23Z. It
serves version **`5e87f21f-5bf3-46ab-938c-ca32f9a9d763`** at 100 %, with the message "ADG staging
5c7805e423fd (main)".

**Proof the deployment serves Wave D.** The workflow's success was not taken on trust:

- **Metadata.** Render reports commit `5c7805e`, the Worker version's message names `5c7805e`, and
  `5c7805e`'s `demo-release.json` names adg `bb370f8`.
- **Content.** Hosted has no version endpoint, so
  [`bundle-markers.py`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/bundle-markers.py) searched the
  client JavaScript for a Wave D-only pattern: NAV-07's item link, compiled as a `next/link` with
  `prefetch:!1` beside the `/governance/items/` href.
  - In the local builds
    ([`local-markers.json`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/local-markers.json)), the
    Wave C chunk has the href **without** that pattern, and the Wave D chunk has it **with** it.
  - On hosted ([`hosted-markers.json`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/hosted-markers.json)),
    the campaign page's chunk `8993-7c24db0542ab7976.js` has it **with** the pattern.

  Hosted therefore runs the Wave D client code.

## 12. Rollback point

The rollback point was recorded before the deploy and has not been removed:

| | Rollback (before) | Live (after) |
|---|---|---|
| adg-demo `main` | `4e565da` (adg `559dbaf`, Wave C) | `5c7805e` (adg `bb370f8`, Wave D) |
| Render deploy | **`dep-dbj177142hec73boioj0`** | `dep-dbj36k67bikc73e9rskg` |
| Worker version | **`77405344-7dc6-491b-aed1-68f88c82ebc8`** | `5e87f21f-5bf3-46ab-938c-ca32f9a9d763` |

To roll back:

1. Redeploy commit `4e565da` on Render.
2. Run `wrangler rollback 77405344-7dc6-491b-aed1-68f88c82ebc8`.

No database step is needed.

## 13. Dataset fingerprint before and after

**The migration gate.** `git diff 559dbaf bb370f8` changes nothing under:

- `database/`, `backend/vendor/`, `backend/app/demo*` or `backend/app/models/`;
- the Genesee Press manifest or the lock.

There is **no migration and no dataset change**. So the deploy ran with `bootstrap=false` and **no
reset**.

[`dataset.txt`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/dataset.txt) compares three read-only
measurements: before the deploy, from the workflow after it, and after all hosted testing.

| | Before | After deploy | After testing |
|---|---|---|---|
| `verify` | ok, 23 probes | ok, 23 probes | ok, 23 probes |
| Demo Mode fingerprint | `4667d3cf…9b270f08` | `4667d3cf…9b270f08` | `4667d3cf…9b270f08` |
| Genesee Press fingerprint | `b4b93b65…e59445d0` (match) | the same | the same |

All three runs also have identical per-source digests (12 sources) and identical counts. The counts
are 2,139 current principals, 6,224 membership edges, 33 servers, 124 shares, 4,117 NTFS ACEs and
300 scan runs.

## 14. Smoke results

The workflow's `frontend/cloudflare/checks/smoke.mjs` gave **23/23**
([`hosted-checks/smoke.log`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/hosted-checks/smoke.log)).

## 15. Browser results

| Gate | Result |
|---|---|
| Workflow browser gate (`browser.mjs`, headless Chromium) | **22/22** ([log](ADG-WAVE-D-STAGING-RELEASE-01-evidence/hosted-checks/browser.log)) |
| Hosted Wave D checks ([`hosted-wave-d.mjs`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/hosted-wave-d.mjs), planner, system Chrome) | **6/6** ([`hosted-wave-d.txt`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/hosted-wave-d.txt)) |
| Titles journey, live window (`frontend/cloudflare/checks/titles.mjs`, planner, no proxy) | **14/15** ([`titles-hosted.txt`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/titles-hosted.txt)) |

**The hosted Wave D checks** passed at **1360, 375 and 320 px**:

- sign-in;
- Overview → Findings → Reviews by client navigation, with titles;
- Back and Forward;
- **0 document loads**;
- the Reviews empty state with no SID and no "(name not loaded)";
- an unknown campaign titled `Review campaign — ADG`;
- no sideways scroll;
- 0 page and 0 hydration errors. The one console error is the known `/favicon.ico` 404.

**The titles journey (§25).** Following the brief, the anchoring proxy was not used: it cannot
reach the hosted Next.js → API connection.

- **What passed:**
  - the 9 primary sections, Back and Forward;
  - the Changes journey on the **real** 90-day window, including History and "Why access changed";
  - 15 Back and 15 Forward steps without reload;
  - 13 direct loads with one matching server `<title>`;
  - invalid-object titles;
  - no hydration errors.
- **The one failure** is the deeper journey's `Review campaign` step: there is no campaign to open
  on the canonical dataset (§21).

The real date window still works today, because the data ends 2026-10-01. It stops working after
about 2026-12-30 (Wave D limitation 5).

## 16. NAV-07 hosted results

**This could not be exercised on hosted: there is no campaign, so there is no item link to click
(§21).**

On hosted:

- The hosted client bundle carries the NAV-07 `next/link` with `prefetch:!1` (§11).
- Client navigation, Back and Forward across the primary sections and Reviews give **0 document
  reloads** at three widths (§15).
- The routing and transport the move depends on are byte-identical to the Wave C release HS-11
  verified (§7).

Locally on Wave D, the same code was verified as follows:

- click, Back, Forward and keyboard each gave 0 document reloads, at desktop and 375 px;
- the titles were `Review campaign — ADG` → `Review item — ADG`;
- the move made 3 API reads instead of 5.

These are the Wave D report's §8 and §18 results.

## 17. REVIEW-NAMES hosted results

**This could not be exercised on hosted: there is no campaign, so there are no scopes to name
(§21).**

- **The Reviews page.** It renders the empty state at 1360, 375 and 320 px, with no SID and no
  fallback text.
- **The deployed backend.** It is byte-identical to Wave D's `backend/app/api/governance.py`, the
  file that adds `display_name`.
- **Locally on Wave D:**
  - `Identity: Brianna Reddy, Identity: sec-share-design-read-only` on Reviews and the campaign
    page;
  - `Identity: S-1-…-999999 (name not loaded)` for an unresolved identity;
  - no SID beside a name;
  - `display_name` never the UPN or an email.

## 18. API contract result

The read-only checks ran against the hosted API as `planner` (§15 checks 1–2).

- **Governance.** `GET /api/v1/governance/campaigns` returns 200 with `items: []`, and
  `/api/v1/governance/queue` returns 200. `viewer` still gets **403**, so authorization is
  unchanged.
- **`ScopeView`.** Its `display_name` cannot be observed on hosted, because no governance response
  carries a scope there. The deployed `openapi.json` and `governance.py` equal Wave D's, where the
  field is additive and optional. Locally, a payload without the field still parses
  (`test_a_payload_without_the_name_still_parses`), and the Wave C client renders the new API.
- **`ScanScopeView`.** Over the 50 newest hosted scan runs, every `declared_scopes` and
  `reconciled_scopes` entry is exactly `{kind, key}`. **The scan-run wire JSON is unchanged.**
- **Compatibility.** There is no contract break. The OpenAPI diff is the additive field plus the
  `ScanScopeView` component (Wave D §11).

## 19. Small streaming regression

The hosting path and the Overview streaming code are byte-identical to the Wave C release that
HOSTED-STREAMING-11 measured in full (§7). Only the small regression was therefore run, with HS-10's
and HS-11's probes, unchanged
([`streaming.txt`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/streaming.txt)).

**Warm-up.** `/health/ready` answered in 0.23–0.25 s.

**Structure** ([`hosted-structure.json`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/hosted-structure.json)):

- 5 pending Suspense boundaries, 5 streamed segments and 5 `$RC` reveals;
- 4 `aria-busy` placeholders with the loading copy;
- all four resolved section ids.

This is the same as Wave C.

**Byte level** (`stream-probe.mjs`, planner, `Accept-Encoding: gzip, deflate, br` → served `br`;
HS-11's `analyze.mjs` pass rule): **5/5 pass.**

| Run | Headers | Shell | First section | Last section | Shell → last | Complete |
|---:|---:|---:|---|---|---:|---:|
| 1 | 328 | 330 | Reviews 520 | Needs attention 950 | 620 | 898 |
| 2 | 275 | 276 | Reviews 485 | Needs attention 870 | 594 | 869 |
| 3 | 319 | 321 | Reviews 609 | Needs attention 921 | 600 | 921 |
| 4 | 391 | 392 | Reviews 577 | Needs attention 877 | 485 | 877 |
| 5 | 311 | 311 | Reviews 502 | Needs attention 801 | 490 | 895 |

- **Timing.** All times are in ms. The headers, the shell and the placeholders arrive together.
  Reviews follows about 200 ms later, and Data health and Needs attention come last, 485–620 ms
  after the shell. This is the same shape as HS-11 (402–1093 ms).
- **Run 1's last marker (950) is stamped after the response ended (898).** The probe times a marker
  when its bytes come out of the brotli decoder, which can lag the end event. The marker's bytes
  still arrived before the end.

**Browser** (`browser-stream.hosted.mjs`, system Chrome, HTTP/3, `zstd`): two sessions, each with
3 runs at 1360 px and 3 at 375 px. **12/12 runs pass** ([`browser-stream.json`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/browser-stream.json)
has session 2's timelines).

- Every run showed the h1 and a resolved section on screen while a placeholder was still loading.
  The last section followed the h1 by at least 100 ms.
- The h1 appeared at 195–2202 ms, and the last section at 1036–3929 ms.
- There were no duplicates, no sideways scroll, and 0 page or hydration errors.
- The client navigation to Findings passed at both widths in both sessions.
- Screens of the mid-stream page are in [`screens/`](ADG-WAVE-D-STAGING-RELEASE-01-evidence/screens/).

**Result:**

- headers, shell and placeholders arrive first;
- one section is rendered while another is still pending;
- the later section arrives afterwards;
- the response completes;
- there are no hydration errors.

**BUFFERING OBSERVED: NO. `WAVE_D_HOSTED_STREAMING_REGRESSION_PASS`.** The full HS-11 method was not
needed.

## 20. Cache and encoding

| | Value |
|---|---|
| `cf-cache-status` | `DYNAMIC`, on every run |
| `cache-control` | `private, no-cache, no-store, max-age=0, must-revalidate` |
| `transfer-encoding` | `chunked` |
| `vary` | `rsc, next-router-state-tree, next-router-prefetch, next-router-segment-prefetch, Accept-Encoding` |
| `content-encoding` | `br` for a browser-style raw request, and `zstd` in Chrome over HTTP/3 |

Next.js compresses gzip only, so the `br` and `zstd` come from edge re-encoding (HS-11 §5). That is
not a failure, and it did not buffer.

## 21. Limitations

1. **Wave D's campaign features are not exercisable on hosted.**
   - The canonical Genesee Press dataset ships no review campaign, so hosted has 0 campaigns.
   - The public accounts (`viewer`, `planner`) are refused governance writes by design.
   - The owner chose to keep hosted canonical. Campaign → item navigation, scope names, the
     unresolved fallback and a live `ScopeView.display_name` were therefore verified on hosted only
     indirectly (§11, §16–§18). Their behaviour is verified locally on the identical tree.
   - Exercising them on hosted would need one of:
     - a synthetic campaign written to `adg_demo`. That would be persistent and visible to every
       visitor, and would leave the fingerprints unchanged because governance tables are not
       fingerprinted;
     - a dataset that ships one.
2. **The hosted titles journey uses the real date window.**
   - Its Changes part works until about 2026-12-30.
   - Its campaign, item and proposal steps cannot run on the canonical dataset.
   - The anchoring proxy cannot reach the hosted Next.js → API connection (TEST-STABILITY-09).
3. **Streaming is measured on natural variance only.** This was one vantage point (US west Mac) and
   one window (about 18:40Z). The raw probe cannot decode `zstd`, so Chrome's `zstd` path is shown
   by the browser timeline only (HS-11 limitations 1–5).
4. **Staging runs an integration branch, not `adg` `main`.** Wave D is not merged to `main`.
5. **The docs-only head `bb370f8` has its own CI run** (§23). The release rests on run 41223498140
   for the identical code tree.
6. **The Wave D product limitations are carried unchanged.** See Wave D §22: the filter links still
   reload, focus lands on `BODY`, the `?principal=` heading can fall back to the SID, the scope label
   shows the current name while items keep the baseline name, missing-object pages answer HTTP 200,
   and SVG tooltips show raw SIDs.

The Wave C hosted-streaming gate (HS-11) is closed and is not a defect.

## 22. Production status

**PRODUCTION NOT DEPLOYED.** Only the demo staging pair was changed: Render `adg-demo-web` and Worker
`adg-demo-staging`. Not changed:

- any production Worker, Render service or DNS;
- the hosted dataset;
- Cloudflare, Render and GitHub environment settings;
- `adg` `main`.

## 23. CI on the docs-only Wave D head

Pending at report time; recorded by the follow-up commit.

## Security

The evidence holds:

- no cookies, session tokens, bearer tokens or origin key;
- no Cloudflare account id or Render workspace id;
- no database host;
- no staging or Render hostnames. Two CI logs were redacted to `<staging>` and `<render-origin>`.

It was scanned for:

- JWT, `adg_session=`, `Bearer`, `access_token` and pooler patterns;
- the literal origin-key value;
- the account, workspace, service and project ids;
- both hostnames.

The only pattern hits are the `adg_session=` cookie-building code in the HS-10 and HS-11 probes.

## Status

`NOT MERGED TO MAIN` · `PRODUCTION NOT DEPLOYED` · staging runs **`genesee-press-2.1.0-bb370f8e4531`**
(adg-demo `5c7805e`, adg `integration/ADG-UX-WAVE-D-01` @ `bb370f8`)
