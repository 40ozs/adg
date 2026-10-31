# ADG-HOSTED-STREAMING-11 — deploy the verified build and close the hosted streaming gate

**Verdict: `ADG_HOSTED_STREAMING_VERIFIED_WITH_LIMITATIONS`**

**HOSTED STREAMING RELEASE GATE CLOSED.**

The newest verified ADG UX integration, Wave C `559dbaf`, was released to `40ozs/adg-demo`
through the existing exporter. It was deployed to the existing public demo staging with the
normal workflow and no database reset. The progressive Overview was then measured through the
whole hosted path:

```
Client ─► Cloudflare edge ─► Worker adg-demo-staging ─► Render edge ─► render/gate.ts ─► Next.js (+ FastAPI on loopback)
```

On that path, the page shell and the loading placeholders arrive first, then the sections one
by one. The last section arrives 402–1093 ms after the shell across 24 warm raw-HTTP runs and
four encodings, every run passes the pass rule (§13 of the brief), and Chrome visibly renders
the sections progressively at 1360 px and 375 px, with no page or hydration errors.

**BUFFERING OBSERVED: NO.** No product code changed. Production was not deployed.

The limitations are listed in §9. None of them weakens the result.

Predecessor: [ADG-HOSTED-STREAMING-10](ADG-HOSTED-STREAMING-10.md)
(`ADG_HOSTED_STREAMING_10_BLOCKED_ON_HOSTED_ENVIRONMENT`). It was blocked because hosted
staging ran a pre-Suspense build, and that result stands as recorded. This lane deployed the
missing build and reran its probes.

Evidence: [`ADG-HOSTED-STREAMING-11-evidence/`](ADG-HOSTED-STREAMING-11-evidence/). The release
and deployment record is [`deployment.txt`](ADG-HOSTED-STREAMING-11-evidence/deployment.txt).

## 1. Source

| | |
|---|---|
| Integration branch | `origin/integration/ADG-UX-WAVE-C-01` |
| ADG SHA | `559dbaf913a02e0df4ddf18be89ef81f03e48596` |
| CI | run `41167906074` on `559dbaf`: **success** (code/report head `52024ea`: run `41165819488`, success) |
| Why this SHA | It is the newest verified integration. No Wave D integration branch exists on origin; the newer remote branches are individual feature or test lanes (NAV-07, REVIEW-NAMES-08, TEST-STABILITY-09) and the HOSTED-STREAMING-10 docs branch. None of them may be deployed. |
| Previously deployed | adg `aebf16f`, an ancestor of `559dbaf`, 46 commits behind |

## 2. Release artifact

The existing path, with nothing new invented:

1. **Pin.** The `40ozs/synthetic-enterprise` file `tools/cutover/specs/adg.json` pins the source
   SHA. It was bumped on branch `release/ADG-HOSTED-STREAMING-11` @ `79ec257` (off SE `main`
   `a6e63f9`). The bump changed the lane, the release id `genesee-press-2.1.0-559dbaf913a0`, the
   source branch, the SHA, and one release-note sentence: "deploys with `bootstrap: false`".
   The Genesee Press lock is unchanged and there is no migration since `aebf16f`.
2. **Export.** `tools/cutover/demo_release.py export` produced 905 files and 0 redactions.
   - `verify_release.py` reports OK.
   - 267 application, gate, Worker and API files are byte-identical to `git show 559dbaf:<path>`.
3. **Release commit.** `40ozs/adg-demo` `main` moved `3d91fad` → **`4e565da1d9cc2caa7b65105cecf9b82309ee1fff`**
   ("Release genesee-press-2.1.0-559dbaf913a0").
   - 129 files: 28 added, 101 modified, 0 removed.
   - `demo_release.py compare` re-exports at the recorded timestamp and reports `identical: true`.
   - The "Demo release verification" CI run `41218491659` succeeded.
4. **Deploy.** "Free staging (ADG demo)" was dispatched on `main` with `bootstrap=false` (§4).

Release-only files are the exporter's usual five: the manifest, the release note, the digest
list, the verifier and its CI workflow. No unrelated application change is included.

**Hosting-path files did not change.** These files have the same git blob in deployed `3d91fad`,
the new release `4e565da` and the source `559dbaf`:

- `frontend/cloudflare/worker.ts`
- `frontend/cloudflare/routing.ts`
- `frontend/cloudflare/environment.ts`
- `frontend/render/gate.ts`
- `frontend/next.config.mjs`
- `frontend/wrangler.jsonc`
- `docker/Dockerfile.render`
- `.github/workflows/cloudflare-staging.yml`

The same holds for `package-lock.json` and `pyproject.toml`. HOSTED-STREAMING-10's block was
therefore version skew. The deploy changed only the application above the transport.

## 3. Rollback anchors (recorded before the deploy, not removed)

| | Before (rollback) | After (live) |
|---|---|---|
| adg-demo `main` | `3d91fad` (adg `aebf16f`) | `4e565da` (adg `559dbaf`) |
| Render `adg-demo-web` deploy | **`dep-dbhf9u8u01pc73b649sg`** | `dep-dbj177142hec73boioj0` (finished 16:15:58Z) |
| Worker `adg-demo-staging` version | **`ff44276e-37c3-4ffc-86c4-3e8858fa7700`** | `77405344-7dc6-491b-aed1-68f88c82ebc8` (16:18:10Z), message "ADG staging 4e565da1d9cc (main)" |
| Hostname | `adg-demo-staging.<subdomain>.workers.dev` | unchanged |

**Rollback.**

- Render: redeploy `dep-dbhf9u8u01pc73b649sg`'s commit `3d91fad`.
- Worker: `wrangler rollback ff44276e-37c3-4ffc-86c4-3e8858fa7700`.
- No database step is needed. The dataset was not touched.

## 4. Deployment

Staging run **`41218573339`** succeeded:

| Job | Result |
|---|---|
| Preflight | ✓ |
| Code gates | ✓ |
| Bootstrap | **skipped** (no reset) |
| Render origin deploy | ✓ |
| Worker deploy | ✓ |
| Hosted checks | ✓ |

The hosted checks found:

- `verify ok`.
- Demo Mode fingerprint `4667d3cf…9b270f08`, unchanged.
- Genesee Press fingerprint `b4b93b65…e59445d0`, match.
- HTTP smoke 23/23 and browser gate 22/22.

**The deploy was checked independently of the workflow result:**

- The Render API reports the live deploy at commit `4e565da`.
- The Cloudflare API reports Worker version `77405344` at 100 %, tagged with that commit.
- The public page is the progressive build
  ([`hosted-structure.json`](ADG-HOSTED-STREAMING-11-evidence/hosted-structure.json)). The
  signed-in Overview has:
  - 5 pending Suspense boundaries (`<!--$?--><template id="B:n">`), 5 streamed segments and 5
    `$RC` reveals;
  - 4 `aria-busy` placeholders with the Wave C loading copy ("Loading collection status",
    "Loading findings", "Loading changes", "Loading your review queue");
  - all four resolved section ids.

  The previous build had none of these. There is no deployment mismatch.

## 5. Cache and encoding

Every response from the Worker and from the Render origin had these headers:

- `cf-cache-status: DYNAMIC`
- `cache-control: private, no-cache, no-store, max-age=0, must-revalidate`
- `transfer-encoding: chunked`
- `vary: rsc, next-router-state-tree, next-router-prefetch, next-router-segment-prefetch, Accept-Encoding`
- no `age`

Nothing is served from cache, and no cache was purged or changed.

| Path | Requested `Accept-Encoding` | Returned `Content-Encoding` | Streams? |
|---|---|---|---|
| Worker (public) | `identity` | none | yes (6/6) |
| Worker (public) | `gzip` | `gzip` | yes (6/6) |
| Worker (public) | `br` | `br` | yes (6/6) |
| Worker (public) | `gzip, deflate, br` (browser-style) | `br` | yes (6/6) |
| Worker (public), Chrome | Chrome default (incl. `zstd`), HTTP/3 | `zstd` | yes (10/10 browser runs) |
| Render origin (Worker bypassed, origin key) | `identity` / `gzip` / `br` | none / `gzip` / `br` | yes (5/5 each) |

**Re-encoding.** Next.js compresses gzip only (HOSTED-STREAMING-10 §2). Two edges re-encode:

- **Render's edge.** The Render origin answers `br` to a `br` request, so its edge recompresses.
  It also sets `cf-cache-status`, which means Render's edge is itself fronted by Cloudflare.
- **Cloudflare's edge in front of the Worker.** Chrome receives `zstd`, which neither Next.js
  nor Render's edge was seen to produce.

**Neither re-encoding buffers.** Section markers arrive spread out over time with every
encoding. The upstream encoding the Worker receives from Render was not observable from outside.

## 6. Timing (raw HTTP, warm, complete hosted path)

Method:

- **Probe.** HOSTED-STREAMING-10's [`stream-probe.mjs`](ADG-HOSTED-STREAMING-10-evidence/stream-probe.mjs),
  unchanged. It signs in as `planner` through the development login, GETs `/`, and decodes
  gzip/br incrementally, timing each marker when its bytes land.
- **Runs.** 6 warm runs per encoding, after `/health/ready` answered in 0.19–0.33 s.
- **Time.** About 16:23Z, from one Mac on the US west coast, over HTTP/1.1.
- **Slow section.** Natural timing variance; no latency was injected.
- **Analysis.** [`analyze.mjs`](ADG-HOSTED-STREAMING-11-evidence/analyze.mjs) applies the pass rule.
  Each run must have:
  - status 200;
  - the shell (`<h1>`) and an `aria-busy` placeholder no later than the first section;
  - a resolved first section;
  - a *different* last section;
  - last section at least 100 ms after the shell, and at least 100 ms after the first section;
  - a completed response.

All times are in ms from request start. Every run passes.

| Asked | Run | Headers | Shell | First section | Last section | Shell → last | Complete | Chunks |
|---|---:|---:|---:|---|---|---:|---:|---:|
| identity | 1 | 306 | 308 | Reviews 505 | Needs attention 904 | 596 | 905 | 28 |
| identity | 2 | 397 | 400 | Reviews 593 | Needs attention 901 | 501 | 901 | 18 |
| identity | 3 | 497 | 499 | Reviews 997 | Needs attention 1403 | 904 | 1403 | 11 |
| identity | 4 | 304 | 304 | Reviews 593 | Needs attention 918 | 614 | 918 | 10 |
| identity | 5 | 376 | 377 | Reviews 576 | Needs attention 900 | 523 | 900 | 10 |
| identity | 6 | 278 | 278 | Recent changes 573 | Needs attention 785 | 507 | 785 | 6 |
| gzip | 1 | 310 | 312 | Reviews 503 | Needs attention 806 | 494 | 806 | 8 |
| gzip | 2 | 304 | 304 | Reviews 597 | Needs attention 917 | 613 | 917 | 10 |
| gzip | 3 | 282 | 283 | Reviews 577 | Needs attention 882 | 599 | 881 | 9 |
| gzip | 4 | 394 | 394 | Recent changes 593 | Needs attention 796 | 402 | 796 | 8 |
| gzip | 5 | 400 | 400 | Reviews 697 | Needs attention 922 | 522 | 922 | 6 |
| gzip | 6 | 284 | 284 | Reviews 476 | Needs attention 694 | 410 | 694 | 5 |
| br | 1 | 320 | 324 | Reviews 1114 | Needs attention 1417 | 1093 | 1416 | 10 |
| br | 2 | 405 | 406 | Reviews 605 | Needs attention 925 | 519 | 925 | 9 |
| br | 3 | 274 | 274 | Reviews 471 | Needs attention 769 | 495 | 769 | 9 |
| br | 4 | 344 | 345 | Reviews 705 | Needs attention 1004 | 659 | 1004 | 8 |
| br | 5 | 403 | 403 | Reviews 797 | Needs attention 1200 | 797 | 1200 | 6 |
| br | 6 | 208 | 209 | Reviews 398 | Needs attention 628 | 419 | 628 | 6 |
| gzip, deflate, br → br | 1 | 228 | 231 | Reviews 424 | Needs attention 719 | 488 | 719 | 9 |
| gzip, deflate, br → br | 2 | 307 | 308 | Reviews 498 | Needs attention 800 | 492 | 799 | 9 |
| gzip, deflate, br → br | 3 | 304 | 305 | Reviews 506 | Needs attention 803 | 498 | 803 | 10 |
| gzip, deflate, br → br | 4 | 312 | 313 | Reviews 499 | Needs attention 801 | 488 | 801 | 8 |
| gzip, deflate, br → br | 5 | 306 | 306 | Reviews 503 | Needs attention 825 | 519 | 825 | 6 |
| gzip, deflate, br → br | 6 | 279 | 279 | Reviews 571 | Needs attention 905 | 626 | 905 | 6 |

**24/24 runs pass.** Shell to last section takes at least 402 ms (max 1093). First to last
section takes at least 203 ms.

**What arrives when.**

1. **With the headers (≤ 3 ms after them):** the shell (`<h1>`) and the four "Loading …"
   placeholders, plus, by the page's structure, everything outside the Suspense boundaries.
2. **Next, about 200–300 ms later:** Reviews and Recent changes, in either order.
3. **Last:** Data health and Needs attention, together. They share the risk-summary read: Data
   health's top slot waits on findings to decide whether its caveat comes first.

The response ends with the last section.

Compared with HOSTED-STREAMING-10's baseline of the old build, where every marker landed within
0–3 ms of the headers, the sections are now spread over 400–1100 ms.

**Render origin, Worker bypassed** ([`render-origin.summary.txt`](ADG-HOSTED-STREAMING-11-evidence/render-origin.summary.txt)):
15/15 runs pass with the same shape. Shell to last section takes 407–1618 ms. The origin hop and
the full public path behave the same.

Raw data:

- `hosted-worker.{identity,gzip,br,negotiated}.json` and `render-origin.{identity,gzip,br}.json`:
  per-chunk arrival times and marker times.
- `*.summary.json` and `*.summary.txt`: the analysed runs.
- `hosted-worker.first-look.identity.json`: the first post-deploy probe (shell 499, Reviews and
  Recent changes 793, Needs attention and Data health 1001).

## 7. Browser

[`browser-stream.hosted.mjs`](ADG-HOSTED-STREAMING-11-evidence/browser-stream.hosted.mjs) setup:

- Chrome 156.0.8078.74 (system install) through Playwright 1.56.1, against the public host, signed in as
  `planner`.
- 5 runs at 1360×900 and 5 at 375×812.
- HTTP/3, `zstd`.

An init script records the following in page time:

- when the h1 and each section first become *visible* (`checkVisibility()`, so content still
  inside React's hidden `S:n` segment does not count);
- every change in the set of visible sections and visible `aria-busy` placeholders.

| Width | h1 visible | First section visible | Last section visible | Intermediate states seen |
|---|---|---|---|---|
| 1360 | 658–1664 ms | Reviews 1033–3562 | Data health + Needs attention 1698–3863 | every run: h1 + 4 placeholders → 1–2 sections + 2–3 placeholders → all 4 |
| 375 | 256–1048 ms | Reviews 659–1940 | Data health + Needs attention 1455–3141 | same, every run |

Results ([`browser-hosted.txt`](ADG-HOSTED-STREAMING-11-evidence/browser-hosted.txt),
[`browser-hosted.json`](ADG-HOSTED-STREAMING-11-evidence/browser-hosted.json)): **10/10 runs
pass, and the client navigation to Findings passes at each width.** In every run:

- The shell was visible with its placeholders first. There was no blank-page wait.
- A resolved section was on screen beside a still-loading placeholder.
- The last section followed at least 100 ms after the h1.
- Each section was present at most once.
- Nothing scrolled sideways.
- There were no page errors, console errors or hydration errors.

Screenshots:

- [`hosted-1360-run3-partial.png`](ADG-HOSTED-STREAMING-11-evidence/hosted-1360-run3-partial.png)
  shows the mid-stream page: Recent changes and Reviews are rendered while Data health and Needs
  attention still read "Loading collection status…" and "Loading findings…".
- At 375 px the placeholders are below the fold, so
  [`hosted-375-run2-partial.png`](ADG-HOSTED-STREAMING-11-evidence/hosted-375-run2-partial.png)
  shows the shell only. The 375 px proof is the recorded timeline.

The browser's h1 time includes the CSS fetch before first paint. As HOSTED-STREAMING-10 noted,
React 19.2 reveals streamed boundaries in throttled ~300 ms steps, so the browser shows sections
slightly later than their bytes arrive.

## 8. Buffering

**BUFFERING OBSERVED: NO.**

Every hop carries the progressive response:

| Hop | Result | Source |
|---|---|---|
| Next.js direct | streams | measured locally in HOSTED-STREAMING-10 |
| via `gate.ts` | streams | measured locally in HOSTED-STREAMING-10 |
| Render edge | streams | measured here, 15/15 |
| Cloudflare + Worker | streams | measured here, 24/24 raw + 10/10 browser |

The brotli re-encoding that HOSTED-STREAMING-10 flagged as the likeliest buffering point does not
buffer. Neither does Cloudflare's zstd for browsers.

Boundary isolation (§18 of the brief) and the Worker, gate and Render audits (§19–§21) were not
needed, so they were not run.

## 9. Limitations

1. **Natural variance only.** No hosted latency injector exists, and none was added. The
   observed gaps (≥ 402 ms shell → last) come from the free tier's real read times. A slower
   section, such as a cold API, spreads them further; it cannot invert the result.
2. **The raw-byte probe cannot decode `zstd`.** The `zstd` path that Chrome negotiates over
   HTTP/3 was verified by the browser timeline only, not by per-chunk byte timing.
3. **One vantage point and one time window.** All runs came from one Mac (US west) around
   16:20–16:30Z. The raw probes used HTTP/1.1; the browser used HTTP/3.
4. **One account.** All runs used `planner`, which sees all four sections. Other roles render
   fewer sections through the same mechanism.
5. **The upstream encoding the Worker receives from Render is not observable from outside.**
   Section 5 infers which edge produces which encoding from the two observable ends.

## 10. Product changes

**None.** No product or ADG source change was made. The only commits are:

- 40ozs/synthetic-enterprise `release/ADG-HOSTED-STREAMING-11` @ `79ec257`: the export spec
  bump only. Pushed, not merged into SE `main`.
- 40ozs/adg-demo `main` @ `4e565da`: the generated release.
- This report and its evidence, on `docs/ADG-HOSTED-STREAMING-11`, branched from
  `docs/ADG-HOSTED-STREAMING-10`.

No remediation branch was created, because nothing buffers.

## 11. Probe changes (separate from results)

- `stream-probe.mjs` (HOSTED-STREAMING-10): **unchanged**, used from that report's evidence
  directory.
- `browser-stream.mjs` (HOSTED-STREAMING-10): **unchanged and not used.** It assumes one injected
  4 s delay on Reviews.
- **New `browser-stream.hosted.mjs`**, a variant of `browser-stream.mjs` that names no slow
  section in advance:
  - It records visibility and placeholder transitions in-page.
  - It requires an observed moment where a resolved section and a loading placeholder are both
    on screen.
  - It keeps the duplicate, sideways-scroll, hydration and page-error checks and the client
    navigation.
  - It records the document's `content-encoding` and protocol.
- **New `analyze.mjs`:** applies the §13 pass rule to `stream-probe.mjs` output.
- **New `structure-probe.mjs`:** counts streaming-SSR and Wave C markers in the served HTML. It
  prints counts only, never the body.

## 12. Security

The evidence holds no cookies, session tokens, bearer tokens, origin key, Cloudflare account id,
Render workspace id or hostnames. It was scanned for:

- the `access_token`, `adg_session=`, `Bearer ` and JWT patterns;
- the literal value of the staging origin key;
- the account and workspace ids and the hostnames.

None were found. The probes build the session cookie in memory and log allow-listed response
headers only.

## 13. Release implication

**HOSTED STREAMING RELEASE GATE CLOSED.** The Wave B/C promise holds on hosted staging: a fast
shell under slower sections, through Cloudflare, the Worker, Render's edge and `gate.ts`.

Still open, outside this gate:

- **Public demo staging now runs Wave C (`559dbaf`), which is not merged into adg `main`.** The
  next merge or deploy decision should start from that.
- **The SE spec branch `release/ADG-HOSTED-STREAMING-11` awaits the owner's merge into SE `main`.**
  The precedent is CSW-HOSTED-DEMO-01.
- **A hosted Overview observation for the operator.** Recent changes shows "No changes in the
  last 7 days found — but a collector failed". That is dataset state, not a streaming issue.

```
HOSTED STREAMING RELEASE GATE CLOSED
PRODUCTION NOT DEPLOYED
```
