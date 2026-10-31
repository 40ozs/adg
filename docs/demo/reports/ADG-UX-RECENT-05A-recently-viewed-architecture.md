# ADG-UX-RECENT-05A: Recently Viewed architecture and privacy contract

- **Date:** 2026-10-30
- **Lane:** ADG-UX-RECENT-05A (decision and design only; no product code)
- **Branch:** `design/ADG-UX-RECENT-05A-recently-viewed-architecture` (not merged, not deployed)
- **Worktree:** `/Volumes/T5 EVO/mmm/worktrees/adg-UX-RECENT-05A`

```
BASELINE=origin/integration/ADG-UX-WAVE-B-01 @ 10c73d1fcb5b12b31fa54e57b95da3483c079fba
STARTING_SHA=10c73d1fcb5b12b31fa54e57b95da3483c079fba
FINAL_SHA=the commit that adds this report (the branch head)
```

On 2026-10-30, `git fetch` found no remote ref newer than `10c73d1`. The Wave B report names
`10c73d1` as the next UX baseline, and no later report replaces it.

**Verdict: `READY_FOR_ADG_UX_RECENT_IMPLEMENTATION_WITH_LIMITATIONS`.** The limitations are
trade-offs this design accepts on purpose (§13, §15). None of them blocks 05B.

---

## 1. Decision

Recently Viewed is a **per-tab convenience list kept in the browser's `sessionStorage`**:

- The list is bound to the signed-in account and holds at most **10** entries, each for at
  most **8 hours**.
- It is cleared on sign-out and when a different account signs in.
- It is filtered against the viewer's current capabilities every time it is shown.
- **ADG never receives it.** There is no server storage, no new API endpoint, no migration,
  and no extra API read or write per page view.

Four entity kinds are eligible: **identity, server, share and directory**. A view is
recorded only when the entity's own detail read succeeds. The list appears as one Overview
section, between the operational sections and "Where to start".

The evidence that decided it:

1. **ADG has no opaque identifiers to fall back on.** Every canonical key is itself the
   sensitive value:
   - server: case-folded host name (`backend/app/domain/resources.py:66`);
   - share: `host|share` (`resources.py:110`);
   - directory: the UNC path's comparison key (`resources.py:202`);
   - identity: the SID, or `host|SID` for a local group (`backend/app/domain/identity.py:400`, `:482`).

   Option D's "store only opaque IDs" has nothing to store unless a server-side handle table
   is built, and that table is Option C.
2. **The browser already holds those keys.** Detail URLs carry them:
   `/identities/principal/{SID}` and `/resources/directory?key=\\FS01\…`
   (`frontend/lib/resources.ts:1-15`, `frontend/lib/identity.ts:183`). Browser history
   therefore already holds every key Recently Viewed would store. Document titles are
   deliberately generic, with no names or paths (ADG-UX-TITLES-03 §4–§5). So the only new
   data this feature puts in a browser is **an identity's display label**.
3. **Authorization is per capability, not per object** (ADR-0014). Two routers guard
   everything this list can point at:
   - `graph.router` requires `identities:read`;
   - `resources.router` requires `resources:read` (`backend/app/api/__init__.py:85-90`).

   `/auth/me` is re-read on every render (`frontend/lib/auth/current.ts`), so the client can
   re-authorize each entry **exactly** without a backend call.
4. **The public demo shares accounts between strangers.** Its sign-in list is two dev
   accounts, `viewer` and `planner` (`docs/deployment/cloudflare-demo.md:118`). History
   keyed by the server-side subject would show one visitor's history to the next. History
   kept in the browser cannot.
5. **The browser may write only through narrow, reviewable routes.** The `/api/adg` proxy
   is GET-only on purpose, and watches are the single per-person write
   (`frontend/app/api/watches/route.ts:1-17`). ADG schedules no retention pruning in any
   mode (`docs/architecture/demo-mode.md` §11). A server-side activity record would need
   both a new browser write path and a new retention job.

## 2. Product contract

> **Recently viewed** takes a person back to an identity, server, share or directory they
> opened a short while ago in this browser tab, so that they do not have to search for it
> again. It is kept only in this tab and only for this account. ADG does not receive it,
> store it or report on it.

Use cases it serves:

- going back to an identity that was investigated minutes ago;
- going back to a server, share or directory after a detour through Findings, Changes or a
  review;
- searching less often during one investigation.

It is **not**:

- an audit trail;
- a monitoring history;
- a record that an administrator can read;
- an analytics source;
- a list of what the account is allowed to see.

It never leaves the browser. It is never shown to anyone except the account that made it,
and only in the tab where that account made it.

## 3. Threat model

"Mitigated" means the contract (§14) contains a control for the risk. "Accepted" means the
residual risk is no larger than what the browser already holds without this feature.

| # | Risk | Disposition |
| --- | --- | --- |
| T1 | **Shared workstation:** the next person at the machine sees the previous person's list. | **Mitigated.** The list lives only in that tab; sign-out clears it; at display, an owner mismatch clears it; entries expire after 8 h. **Accepted residual:** if the first person walks away without signing out, the next person can read the tab's storage in developer tools. The tab's browser history shows the same keys already, so only identity labels are additional. |
| T2 | **Browser-profile leakage:** storage is written to disk and synced. | **Mitigated.** `sessionStorage` is not synced, and it does not outlive the tab in a normal browser session. **Accepted residual:** browsers that restore tabs (for example Chrome's "Continue where you left off") keep the tab's `sessionStorage` on disk so they can restore it. The 8 h maximum age bounds what a restored tab shows. |
| T3 | **Filesystem paths and server names on disk.** | **Accepted.** The stored key is already in the URL, so it is already in browser history. No other path data is stored. Full UNC paths are never stored as labels (§6). |
| T4 | **Identity names kept in the browser.** | **Mitigated by bounds.** The one new datum is an identity's display label, kept tab-scoped, ≤10 entries, ≤8 h, ≤200 characters. The upgrade rule in §15 removes stored labels if the list ever moves to durable storage. |
| T5 | **Account switching**, including demo users. | **Mitigated.** The value records an owner digest, `sha256(subject)`. If a different subject reads it, the whole value is deleted before anything is rendered. |
| T6 | **Stale authorization**: a role is lost after the view. | **Mitigated.** Entries are filtered against the capabilities from `/auth/me` on every render. Entries the account can no longer open are deleted from storage and never rendered. |
| T7 | **Server-side activity history** becoming surveillance. | **Eliminated.** No server-side storage, no write endpoint and no telemetry exist for this feature. ADG never learns what a person opened. Ordinary request logs of page loads exist independently of this feature and are unchanged. |
| T8 | **Support and debug exports.** | **Mitigated.** No export, log line, error report, telemetry event or URL may include the stored value. ADG has no client error reporter today, and 05B must not add one that captures storage. |
| T9 | **Script injection reading storage.** | **Accepted.** An injected script on the ADG origin can already call the `/api/adg` proxy with the session cookie and read everything the account can read. Storage adds nothing for it. |
| T10 | **Cross-tab sign-out**: tab B keeps its list after tab A signs out. | **Mitigated by T5 and T6.** Tab B renders nothing while signed out, and a different account signing in to tab B deletes the list. **Accepted residual:** the list stays in tab B's storage until then. |

## 4. Storage options considered

| | A: `localStorage` / IndexedDB | **B: `sessionStorage` (selected)** | C: server-side per user | D: opaque IDs plus resolution |
| --- | --- | --- | --- | --- |
| Work | Client only | Client only | Migration, write route, API, retention job, deletion | New handle table or batch endpoint |
| Persistence | Across restarts and sign-outs | Lifetime of the tab | Durable | Depends on where tokens are kept |
| Shared workstation | Stays on disk until a TTL passes or the next sign-in | Gone when the tab closes | Not in the browser | Like A or B |
| Account separation | Namespacing needed; survives an unobserved sign-out | Owner check; tab-scoped | Strong by subject, **but the public demo shares subjects** | Like A or B |
| Cross-device | No | No | Yes | No |
| Across tabs | Yes | **No** (per tab) | Yes | Like A or B |
| Privacy posture | Weakest | Strong | Creates an activity record about a person | No opaque ID exists in ADG (§1.1) |
| Re-authorization | Client capability filter | Client capability filter | Server | Server |

## 5. Selected architecture

- **Option B.** One `sessionStorage` key per origin holds one JSON value (§6).
- **Writer:** a small client component on each eligible detail page. It runs in an effect
  after the page's primary entity read succeeded.
- **Reader:** a client component in one Overview section. It reads the value, applies the
  owner, age and capability rules, prunes, and renders links.
- **No network.** The module has no dependency on `lib/api`. Labels come from the page that
  recorded the entry, so the list needs no resolution request. Its cost is zero requests,
  not N or one batch (§12).

## 6. Data schema

Storage key: **`adg.recentlyViewed.v1`**. One value per tab:

```json
{
  "v": 1,
  "owner": "<hex sha256 of the viewer's /auth/me subject>",
  "entries": [
    { "kind": "identity", "key": "S-1-5-21-…-1104", "label": "Jane Smith", "at": 1793400000000 }
  ]
}
```

| Field | Rule |
| --- | --- |
| `owner` | Computed on the server (`node:crypto`), then passed to the client components as a prop. The raw subject is never written to storage. A digest is not a secret; its job is to make the owner check exact without storing a UPN or email. |
| `kind` | One of `identity`, `server`, `share`, `directory`. Unknown kinds are dropped on read. |
| `key` | The storage key the API issued, exactly as the detail route uses it. A key over **1,024** characters is not recorded. |
| `label` | The short name the detail page already shows, trimmed to **200** characters (the watch label limit, `backend/app/alerts/model.py:71`). Per kind: identity = `principalLabel(...)`, which falls back to the SID when unresolved; server = host name; share = share name; directory = the **final path segment** only, never the full UNC path. |
| `at` | Epoch ms of the latest recorded view. |

Data per field:

| Datum | Decision |
| --- | --- |
| Canonical entity key | **Stored.** Navigation needs it, and it is already in browser history. |
| Display name (identity) | **Stored**, bounded (T4). This is the only datum not already in browser history. |
| SID | Only as the identity key, or as the label of an unresolved identity. |
| GUID, distinguished name, UPN, email | **Never stored.** |
| Server host name | Only as the server key or label. |
| Share name | Only as the share key or label. |
| Directory path or full UNC path | Only as the directory key. Never as a label. |
| Finding title | **Never stored** (findings are not eligible). |
| Timestamp | **Stored**, one per entry, for ordering and expiry. |

Invalid JSON, a wrong `v` or a wrong shape deletes the whole value. It is never partly
trusted and never makes the section throw.

## 7. Retention

- **10 entries at most.** The oldest entry is evicted first.
- **8 hours** maximum age per entry, enforced on every read and write. That covers one
  working investigation and bounds a restored tab (T2).
- **The tab's lifetime** is the outer bound.
- **One entry per `(kind, key)`.** A repeat view moves the entry to the front and replaces
  its `label` and `at`.

## 8. Account separation

| Event | Behavior |
| --- | --- |
| Sign-out (`SignOutButton`) | Delete the key **before** the `POST /api/auth/logout` request, so a failed request still clears it. |
| Sign-in as a different account in the same tab | The first reader or writer sees an owner mismatch and deletes the value. No entry is rendered. |
| Session expiry | No client event exists, because the session cookie is httpOnly. Nothing is shown while signed out. If the same subject signs in again, the list is kept; a different subject means it is deleted. The 8 h limit still applies. |
| Role or capability loss | Filtered and deleted on the next render (§9). |
| Demo account switching (`viewer` ↔ `planner`) | Same as a different account: the subjects differ, so the list is deleted. |
| Other tabs | Each tab is independent (T10). No cross-tab broadcast. |

## 9. Authorization behavior

- **Every render re-authorizes.** The Overview's server component passes
  `viewer.principal.capabilities` from that request's `/auth/me`. An identity entry
  requires `identities:read`. A server, share or directory entry requires
  `resources:read`. These are exactly the capabilities of the routers the detail pages read
  (§1.3).
- **Access lost:** the entry is deleted from storage and not rendered. Its label and key are
  never shown.
- **Neither capability held:** the server omits the section entirely, because nothing could
  be eligible.
- **Object deleted or unresolvable:** the list does not re-check existence on display,
  because that would cost a read. The link leads to the detail page, which already says
  "not found" in its own words. That page's not-found branch then **deletes the entry**.
  ADG keeps tombstoned objects in history (current-state presence), so many "deleted"
  objects still open with a no-longer-present notice, which is the honest result.
- **This holds while ADG authorization stays per capability.** If object-level scoping is
  ever added, the client filter stops being exact, and §15 rule 2 applies.

## 10. UX behavior

### 10.1 Placement

Recently viewed is an Overview section. It is not a search helper; a search empty state may
reuse it later (§15). Overview order:

1. Data health (top, when collection is not healthy)
2. Needs attention
3. Recent changes | Reviews
4. Data health (bottom, when collection is healthy)
5. **Recently viewed** (new)
6. Where to start
7. What ADG will not do

Rationale:

- The estate's shared operational state comes before a personal convenience list.
- "Recently viewed" (where to resume) sits next to "Where to start" (where to begin), and
  both are navigation.
- The list is filled on the client after hydration. Placed below the streamed sections, it
  can neither delay them nor shift them. Only the two static cards below it can move.

### 10.2 Section markup

- `<section className="card" aria-labelledby="overview-recent">` with
  `<h2 id="overview-recent">Recently viewed</h2>`, matching the existing sections.
- No `aria-busy`. The PERF-01 gates wait for `aria-busy` to clear, and this section has no
  server read to wait for.
- The server renders the heading and the description line. The client renders either the
  list or the empty state; it never shows a stale empty state on the server first.
- Description: "Kept in this browser tab for this account only. Not sent to ADG."
- Each row has a kind label (Identity, Server, Share, Directory) and the label as a link to
  the canonical href (§10.4).
- One **Clear** button deletes the key. There is no per-item remove and no disable switch: a
  disable preference would itself need durable storage. The list already expires on its own.

### 10.3 States

| State | Copy |
| --- | --- |
| No history yet | "Nothing yet. Identities, servers, shares and directories you open in this tab will appear here." |
| Some entries removed for lost access (shown on the render that removed them only) | "Some items were removed because this account can no longer open them." No count, label or kind is shown. |
| Object deleted | Nothing on the Overview. The detail page's own not-found notice applies, and the entry is then gone. |
| Storage unavailable or blocked (access throws) | "Recently viewed isn't available in this browser because it blocks session storage." |
| Cleared | The empty state. |

The copy never says "history", "activity", "tracked" or "monitored".

### 10.4 Eligibility and routing

| Object | Eligible | Canonical href |
| --- | --- | --- |
| Identity: user, group, computer, local group | **Yes** | `principalHrefForKey(key)`, the base (default) view; the `[[...view]]` tab is not part of the entry |
| Server | **Yes** | `serverHref(key)` |
| Share | **Yes** | `shareHref(key)`, with no `extra` |
| Directory | **Yes** | `directoryHref(key)`, with no `extra` |
| Finding | No: no detail route; `/risks` is a filtered list |  |
| Review campaign or item | No: the Overview already has a Reviews section and the reviewer queue |  |
| What-if simulation | No: it has its own stored list at `/simulations` |  |
| Access explain or investigate, Changes compare or impact, Search, list pages, filters, Demo guide | No: query or filter state, not an entity |  |

Rendered URLs are never stored. The href is rebuilt from `(kind, key)` with the existing
helpers, so a route change moves every entry with it. Labels never take part in identity or
routing.

## 11. Demo behavior

- **Same code path as production.** No demo special case.
- **Reset:** a demo reset does not touch browsers and does not need to. Genesee Press is
  deterministic (the same fingerprint on every reset), so stored keys stay valid. A key a
  later generation removed is handled by the detail page's not-found branch (§9).
- **Synthetic paths and names are safe to keep.** The data is fictional.
- **Shared public accounts:** each visitor's list lives in that visitor's own tab. Two
  strangers signed in as `planner` never see each other's list. This is the decisive
  advantage over Option C (§1.4).

## 12. Performance implications

| Cost | Amount |
| --- | --- |
| API reads | **0** added: no resolution and no batch lookup |
| Server writes | **0** |
| Storage per detail view | One `sessionStorage` write after the detail read succeeds (synchronous, ≤16 KiB JSON, well under 1 ms) |
| Overview | One `sessionStorage` read on hydration. It sits outside every Suspense slot, so it cannot hold back streaming. |
| Debounce | None needed. A write is local and idempotent per `(kind, key)`; a reload moves the entry to the front. |
| Caches | No effect: nothing reaches the API, and Next prefetches do not mount client components, so they cannot record a view. |
| Client JS | One small module plus two components (writer and section). No dependency. |

## 13. Rejected alternatives

- **A: `localStorage` or IndexedDB.** It survives sign-outs that the app never sees (an
  expired session, a closed browser) and stays on disk across restarts. It also needs TTL
  and namespace upkeep for a list that is only worth minutes to hours. It is kept as an
  upgrade path (§15 rule 1); the schema is the same.
- **C: server-side per-user storage.** It creates the per-person activity record the
  product contract forbids (T7). It needs a migration, a browser write route next to
  watches, a retention job ADG does not have, and deletion handling. On the public demo,
  every visitor shares `dev:planner`, so it would leak lists between strangers.
- **D: opaque IDs.** ADG's canonical keys are the sensitive values (§1.1). An opaque handle
  needs a server mapping, which is C. Key-only storage with render-time batch label
  resolution was also considered. It avoids storing identity labels, but it needs a new
  backend endpoint and an extra read on every Overview render, to protect a label that is
  bounded and tab-scoped. It becomes mandatory only under §15 rule 1.
- **Stored full UNC paths as labels.** Rejected as convenience only. The final segment is
  enough to recognize an entry, and the key still navigates.
- **Per-item remove or a disable toggle.** Rejected as unneeded for a tab-scoped list that
  expires. "Clear" covers a screen-share or a hand-over.

## 14. Implementation contract for ADG-UX-RECENT-05B

This contract is authoritative for 05B. A deviation needs a new decision report.

**Scope**

1. Eligible kinds: `identity`, `server`, `share`, `directory`. Nothing else.
2. Storage: `window.sessionStorage`, key `adg.recentlyViewed.v1`, schema as in §6. No
   `localStorage`, IndexedDB, cookie, URL parameter, server storage, migration or new API
   endpoint.
3. The store module imports nothing from `lib/api` and makes no network request. A unit
   test asserts this.

**Recording**

4. Trigger: a client writer component rendered **only** in the branch where the detail
   page's primary entity read is `ok`:
   - identity: in `app/identities/principal/[key]/layout.tsx`, so switching views does not
     re-record;
   - server, share, directory: in their `page.tsx`.
5. The writer writes in `useEffect` keyed on `(kind, key)`. It never records on prefetch,
   hover, an API call alone, `not_found`, `forbidden`, an error, a timeout or a signed-out
   render.
6. A `not_found` branch renders a forget component that deletes that `(kind, key)` entry.
7. Deduplication: one entry per `(kind, key)`; newest wins; move to front; replace `label`
   and `at`.
8. Limits: 10 entries, 8 h maximum age (applied on every read and write), key ≤1,024
   characters (longer keys are not recorded), label ≤200 characters. Labels follow the
   per-kind rule in §6; a directory label is its final path segment.

**Isolation and authorization**

9. `owner` = hex SHA-256 of `viewer.principal.subject`, computed on the server and passed
   as a prop. An owner mismatch deletes the value before anything renders.
10. `SignOutButton` deletes the key before `POST /api/auth/logout`.
11. Every Overview render filters entries against `viewer.principal.capabilities`:
    - `identity` needs `identities:read`;
    - `server`, `share` and `directory` need `resources:read`.

    Filtered entries are deleted from storage. The section is omitted when neither
    capability is held.
12. Every storage access goes through `try/catch`. Blocked storage gives the §10.3 copy and
    never throws. A corrupt value is deleted whole.

**Overview UI**

13. Placement, markup, copy and the Clear button follow §10. Terms follow the UX-01
    contract (Identity, not principal). No `aria-busy`. No per-item remove. No disable
    switch.

**Hard rules**

14. Never include the value in logs, telemetry, error reports, exports, URLs or requests.
    Document titles stay static (TITLES-03); no `generateMetadata` may read the list.

**Tests 05B must add**

15. Store unit tests:
    - deduplication and move-to-front;
    - the 10-entry cap;
    - 8 h expiry;
    - owner mismatch;
    - capability purge;
    - corrupt JSON;
    - storage that throws;
    - the over-long key;
    - label trimming;
    - no `lib/api` import.
16. A writer test: it is mounted only on the `ok` branch, for each of the four detail routes.
17. A forget-on-`not_found` test.
18. A `SignOutButton` test: it clears before the request.
19. An Overview section test: states, Clear, capability omission, order relative to
    "Where to start".
20. A browser gate step:
    1. open an identity and a directory;
    2. check the Overview lists both, newest first;
    3. sign out, then sign in as another account (`admin` ↔ `planner`);
    4. check the list is empty.

    The existing Overview, titles, Wave A and Wave B gates must stay green.

## 15. Open questions and upgrade rules

There is no blocking question. Three rules decide future changes:

1. **If per-tab scope proves too narrow** (users report losing the list across tabs or
   browser restarts), move to `localStorage` with the same schema, owner check, limits and
   sign-out clear. In the same change:
   - **stop storing identity labels**;
   - add one bounded batch label endpoint on `graph.router` (`identities:read`, ≤10 keys,
     one query);
   - make the Overview render a single read for its identity entries.
2. **If ADG adds object-level authorization** (scoped roles), the capability filter is no
   longer exact. Before display, entries must be re-authorized through bounded batch reads,
   at most one per router.
3. **Search empty state.** Reusing the same reader on `/search` with no query is a possible
   later addition. It is not part of 05B.

Limitations carried by this design:

- The list is per tab.
- A tab restored by the browser keeps its list on disk for up to 8 h (T2).
- A deleted object's label stays until its link is followed.
- The design was checked against the code and the reports, not exercised in a browser
  (there is no implementation in this lane).

## Not done, by design

- **NO PRODUCT IMPLEMENTATION:** no storage code, hooks, components, endpoints, migrations
  or tests were added. This branch changes only this report.
- **NOT MERGED.**
- **NOT DEPLOYED.**
