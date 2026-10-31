# ADG-HOSTED-STREAMING-10 — Worker → render/gate.ts → Next.js streaming

**Verdict: `ADG_HOSTED_STREAMING_10_BLOCKED_ON_HOSTED_ENVIRONMENT`**

A hosted ADG environment exists and was reachable. It does not run the code under validation.
The hosted Overview predates progressive rendering: it makes one read, and it has no
`<Suspense>` and no `loading.tsx`. Hosted streaming of the Wave C Overview therefore cannot be
observed without a deploy, and this lane was not authorized to deploy. Nothing was deployed,
reset or reconfigured.

What this lane did establish:

- The transport code the hosted service runs is byte-identical to Wave C's.
- That code relays bodies as streams (static reading).
- The real `render/gate.ts` preserves progressive delivery, measured locally.
- A baseline of the hosted path as it stands.

What remains unverified is the two hosted-only hops, Cloudflare's edge and Render's edge.
**The hosted streaming release gate stays open.**

## 1. Environment and deployed SHA

| | |
|---|---|
| Hosted environment | ADG staging demo: Worker `adg-demo-staging` (`https://adg-demo-staging.<subdomain>.workers.dev`, workers.dev on) → Render `adg-demo-web` (`render/gate.ts` → FastAPI + Next.js in one service) |
| Render live deploy | `dep-dbhf9u8u01pc73b649sg` @ `40ozs/adg-demo` `3d91fada1991` (2026-10-29T07:28Z), auto-deploy off |
| Worker version | `ff44276e…` 100 %, message "ADG staging 3d91fada1991 (main)" |
| Release | `genesee-press-2.1.0-aebf16fcf594`: application upstream `40ozs/adg` **`aebf16f`** |
| Code under validation | `origin/integration/ADG-UX-WAVE-C-01` @ `559dbaf913a02e0df4ddf18be89ef81f03e48596` |
| Deployed vs Wave C | `aebf16f` is an ancestor of `559dbaf`, 46 commits behind. **Wave C is not deployed.** |
| Next.js | 16.4.1 in both (`next.config.mjs` blob `c1714730` in both) |

All of this came from read-only API calls (Render service and deploys, Worker deployments and
subdomain) and from git. See [`deployment.txt`](ADG-HOSTED-STREAMING-10-evidence/deployment.txt).

**The transport is the same code.** The deployed `adg-demo@3d91fad`, `adg@aebf16f` and Wave C
`559dbaf` have the same git blobs for:

- `cloudflare/worker.ts`
- `cloudflare/routing.ts`
- `cloudflare/environment.ts`
- `render/gate.ts`
- `next.config.mjs`
- `wrangler.jsonc`
- `docker/Dockerfile.render`

A Wave C release would ship this exact Worker and gate.

## 2. Topology

```
Client ─► Cloudflare edge ─► Worker adg-demo-staging (routing.ts forwardToOrigin)
       ─► Render edge (onrender.com) ─► render/gate.ts :$PORT ─┬─ API paths ─► FastAPI 127.0.0.1:8000
                                                               └─ the rest ──► Next.js 127.0.0.1:3000
                                                                   (its server reads → FastAPI on loopback)
```

**Static reading of the two code hops: neither buffers.**

- **Worker** (`routing.ts:95-122`): it builds a new `Request` and returns the upstream
  `fetch` Response object as is. It never calls `text()`, `arrayBuffer()` or `clone()` on a
  response, and it does not transform the body. Only a request body is read, and only for
  non-GET.
- **Gate** (`gate.ts:157-187`): `answer.pipe(response)`. It strips `transfer-encoding`, which
  is hop-by-hop, and Node re-chunks. There is no compression, no body read and no `end(body)`
  on the relay path.
- **Next.js:** default compression (gzip only), standalone `server.js`.

## 3. Hosted baseline (read-only)

[`stream-probe.mjs`](ADG-HOSTED-STREAMING-10-evidence/stream-probe.mjs) signs in through the
public development login as `planner`, the hosted walkthrough account, the same sign-in the
hosted smoke makes. It GETs `/` and times the headers, every body chunk and each marker,
decoding gzip/br as the bytes land. It ran three times per encoding, on 2026-10-31 at about
09:40Z. The first `/health/ready` took **81 s**, a Render free-tier cold start; it was 0.2 s
afterwards.

| Encoding asked | Headers (ms) | `<h1>` (ms) | Complete (ms) | Chunks | Wire bytes | Content-Encoding |
|---|---:|---:|---:|---:|---:|---|
| identity | 419 / 507 / 448 | 423 / 507 / 448 | 424 / 508 / 450 | 16 / 16 / 8 | 20 710 | none |
| gzip | 300 / 280 / 296 | 302 / 281 / 296 | 301 / 280 / 296 | 5 | 4 860 | gzip |
| br | 319 / 209 / 387 | 322 / 210 / 388 | 389 / 210 / 388 | 5 | 4 818 | **br** |

The response headers:

- `cf-cache-status: DYNAMIC`
- `cache-control: private, no-cache, no-store, max-age=0, must-revalidate`
- `transfer-encoding: chunked`
- `vary: rsc, …, Accept-Encoding`
- `server: cloudflare`

**Cache state:** not cached at Cloudflare. Every probe was a fresh render. No purge or cache
change was needed or made.

Reading:

1. **This Overview is not progressive by construction.** All the work happens before the
   headers, and the whole body follows within 0–3 ms (one run of br: 70 ms). That is correct for
   this code, and it says nothing either way about whether the chain buffers. It is a baseline,
   not a streaming test.
2. **An intermediary re-encodes the body.** Next.js compresses gzip only, yet a `br` request is
   answered in brotli, so Cloudflare's edge or Render's edge recompresses. This hop is the
   likeliest place for buffering, and only a progressive page on the hosted path can test it.
   Browsers send `br`.
3. The probe's HTTP client is HTTP/1.1. `curl` reached the same host over HTTP/2.

## 4. Local hops, measured (Wave C build, real gate code)

Hops 1 and 2 of the isolation plan (§8 of the brief) were run locally:

- **Direct:** this Wave C standalone build on :3090.
- **Through the gate:** the real `createGate` from `frontend/render/gate.ts`, imported unchanged
  by [`local-chain.mjs`](ADG-HOSTED-STREAMING-10-evidence/local-chain.mjs), relaying to the same
  Next.js server.

**The slow section** is Next.js's server read `/api/v1/governance/queue` (the Overview's
Reviews section), held for 4 s by a loopback delay proxy between Next.js and the API. Nothing
else is delayed, and nothing hosted is touched. Demo data: Genesee Press, signed in as `admin`.

Raw HTTP, three runs each, in ms from request start:

| Hop | Encoding | Headers | `<h1>` (shell) | First fast section (Recent changes) | Needs attention / Data health | Reviews (delayed) | Complete |
|---|---|---:|---:|---:|---:|---:|---:|
| Next.js direct | identity | 8–15 | 8–16 | 15–29 | 36–54 | 4014–4031 | 4014–4031 |
| Next.js direct | gzip | 9–16 | 10–17 | 19–29 | 39–56 | 4030–4033 | 4029–4033 |
| via `gate.ts` | identity | 8–15 | 8–16 | 16–25 | 37–48 | 4018–4028 | 4019–4028 |
| via `gate.ts` | gzip | 9–17 | 9–18 | 19–29 | 38–55 | 4022–4028 | 4022–4028 |

In both hops:

- the shell and every fast section arrive within about 60 ms;
- the delayed Reviews section arrives about 4 s later;
- the response completes then, over 9–10 chunks.

**`gate.ts` adds no buffering, with or without gzip.**

**Browser**
([`browser-stream.mjs`](ADG-HOSTED-STREAMING-10-evidence/browser-stream.mjs), Chrome, through
the gate and direct, at 1360 px and 375 px): **2/2 pass in each hop.**

| Event | Through the gate (1360 / 375 px) |
|---|---|
| h1 visible | 73 / 67 ms |
| Needs attention, Data health and Recent changes visible | 74–833 ms |
| no `[aria-busy]` left | 4043–4053 ms |
| Reviews visible | 4354 / 4356 ms |

Each section is present exactly once, nothing scrolls sideways, and there are no page errors
and no hydration errors. A client navigation to Findings afterwards lands with its title.

The ~0.8 s for the fast sections in the browser is React 19.2's throttled reveal of streamed
boundaries (see ADG-UX-PERF-01). The bytes arrive at about 50 ms, per the raw probe.

## 5. Local vs hosted

| Measurement | Local (via `gate.ts`) | Hosted |
|---|---:|---:|
| TTFB | 8–17 ms | 209–507 ms (warm; deployed pre-progressive Overview) |
| Shell | 8–18 ms | arrives with the whole body |
| First fast section | 16–29 ms | n/a: the deployed Overview has no sections |
| Slow section | 4018–4028 ms (4 s injected) | n/a: no slow-section method on hosted (§6) |
| Complete | 4019–4028 ms | headers + 0–3 ms |
| Buffering | **NO** (hops 1–2) | **not testable** with the deployed code |

## 6. Why the lane is blocked, and the exact prerequisites

1. **A hosted deployment of Wave C (`559dbaf`) or a verified descendant**, explicitly
   authorized by the owner. Either of these would do:
   - Export a Genesee Press demo release of that SHA to `40ozs/adg-demo` `main` and dispatch its
     "Free staging (ADG demo)" workflow. This is a real deploy of the public demo, and it changes
     what visitors see. The rollback pair is Render `dep-dbhf9u8u01pc73b649sg` and Worker
     `ff44276e`.
   - Deploy an isolated preview Worker plus Render service pair. That is new infrastructure, so
     it is the owner's call even on free tiers.
2. **A safe slow-section method on the hosted path.** None exists. On Render, Next.js and
   FastAPI are loopback siblings inside one container behind the gate, so there is nowhere to
   put a delay proxy. Adding a latency switch would be a product change, which the brief rules
   out for this lane. The acceptable options:
   - **(a) Natural variance (recommended).** On the 0.1-CPU free tier, the Overview's section
     reads take noticeably different times. A chain that streams spreads the section markers
     over time; a buffering chain delivers them in one burst, as in §3 today, where every marker
     lands within 3 ms. The pass criterion over at least 5 warm runs per encoding (identity,
     gzip, br) is:
     - `<h1>` precedes the last section marker by at least 100 ms in every run;
     - the marker order matches the local order.
   - **(b) An owner-approved, test-only latency switch** on a preview deployment only, as its
     own bounded lane.
3. **Warm the service first.** The cold start was 81 s. Run inside `caffeinate`.

**After a deploy, the commands are ready:**

- `stream-probe.mjs` against the public hostname for hop 4.
- With the Worker bypassed, the Render origin plus `ORIGIN_KEY_FILE` (the staging origin key)
  for hop 3. That isolates Cloudflare from Render if hop 4 buffers.
- `browser-stream.mjs` against the public host (no key needed), with `DELAY_MS` set to the
  observed gap.

## 7. Success criteria

| Criterion | Status |
|---|---|
| Deployed SHA verified | **Yes**: `3d91fad` (adg `aebf16f`). Not Wave C |
| Worker path verified | Code: identical to Wave C, returns the stream. Hosted behaviour for a progressive page: **not testable** |
| `render/gate.ts` path verified | **Yes**, locally with the real gate code: streams |
| Cache behaviour understood | **Yes**: `DYNAMIC`, `private, no-store`, so no cached responses |
| Shell before delayed section | Local yes; hosted not testable |
| Fast section before delayed section | Local yes; hosted not testable |
| Browser renders progressively | Local yes (1360, 375); hosted not testable |
| No hydration or page errors | Local yes |
| Raw timing evidence preserved | Yes: `hosted-overview.*.json`, `local-*.json` |
| No credentials in evidence | Yes. Scanned for tokens, cookie values, the throwaway origin key and the account and workspace ids; there are none. The probe builds the cookie in memory and logs allow-listed headers only |

## 8. Product code

None changed. This branch adds only this report and its evidence directory. No defect was found
in the code hops, so no remediation lane is proposed for them.

## 9. Release implication

The open risk is narrow. The application's own hops (Next.js, `gate.ts`, and a Worker that
returns the stream untouched) stream. What has not been seen is Cloudflare's and Render's edges
carrying a progressive response, and a body re-encoding was observed at one of them (§3.2). If
they do buffer, the cost is the Wave B/C promise of a fast shell under a slow section. The page
still renders correctly, only later (whole page ≈ the slowest read), as in the hosted baseline
today.

**HOSTED STREAMING RELEASE GATE: STILL OPEN.** The blocker is that no hosted environment runs
the progressive Overview (Wave C `559dbaf` or a descendant), and deploying one needs the owner's
authorization (§6.1).

```
NOT MERGED
NOT DEPLOYED
```
