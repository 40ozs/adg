# Handoff — Phase 6B (`phase-06/02-identity-resource-views.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-06a-frontend-shell.md](phase-06a-frontend-shell.md)
**Contract version after this phase:** `1.3` — unchanged. No collector payload, no schema,
no migration, and **no backend file was edited**.

## Scope completed

Phase 6A built the shell and the authorization boundary; its Identities and Access sections
were lookup entry points with nothing behind them. This phase is the *behind them*: four
detail pages that an administrator can actually work in.

1. **A principal page** (`/identities/principal`) — user and group in one page, because ADG
   cannot know which it is before it looks. Identity facts, aliases, direct and effective
   groups, direct and effective members, the share ACLs that name the SID, and the shares and
   directories it can reach.
2. **A server page** (`/resources/server`) — machine facts, when a collector last reached it,
   and the shares it publishes.
3. **A share page** (`/resources/share`) — metadata, the share ACL, the NTFS ACL of the
   directory it publishes, and the engine's answer for who gets through both.
4. **A directory page** (`/resources/directory`) — breadcrumb, parent relation, ACL digest,
   explicit-versus-inherited entries, and the two boundary verdicts side by side.
5. **Server-side paging with the position in the URL**, on every list.
6. **The SID beside every name**, with the qualifier promoted when a name is ambiguous
   *within the list being shown*.
7. **Raw ACLs and effective access in visibly different frames**, each labelled with what
   kind of statement it is.
8. **+254 frontend tests** (147 → 401), and a live run of all four pages against a real API,
   a real PostgreSQL, and real collector transcripts.

**No new API surface.** Everything these pages read was already served by Phases 1–5.
`lib/api/adg.ts` grew 17 functions and 15 path entries; no route was added, changed, or
regenerated, so `docs/contracts/v1/openapi.json` is untouched by this phase.

## Five things worth reading before the code

### 1. A raw ACL and an engine answer never share a frame

`components/Layer.tsx` is the whole of it. `RawLayer` and `EffectiveLayer` give a section a
tint, a border, a landmark, and — because a tint is not a signal everybody receives — a word:
*Raw NTFS ACL — as collected* against *Effective access — computed answer*. Each carries a
caveat from one place (`lib/acl.ts`: `RAW_ACL_NOTICE`, `EFFECTIVE_ACCESS_NOTICE`), so the two
wordings cannot drift apart across four pages.

This is the phase's reason for existing. "Everyone / Full Control" on a share ACL is the most
commonly reported critical finding in this domain and most commonly is not one, because the
NTFS layer below refuses it. A table that looks like the effective-access table two sections
down invites exactly that conclusion.

The same discipline runs down to the row. `NtfsAceTable` carries **Source** (explicit /
inherited — where a fix has to be applied) and **Applies here**, which is `no — inherit-only`
for an `INHERIT_ONLY` entry: on the list, naming a trustee, carrying a mask, and granting
nothing on the directory being looked at.

### 2. `access: false` is not "no access"

`lib/access.ts` holds the table, and one row of it is the point:

| access | certainty | word on screen |
| --- | --- | --- |
| true | `certain` | Yes |
| true | `at_most` | Yes, at most |
| true | `at_least` | Yes, at least |
| false | `certain` / `at_most` | No |
| false | `at_least` | **None established** |
| any | `uncertain` | Unknown |

A directory whose descriptor no run has read produces `false` / `at_least`. Rendering it as
"No" is how a gap in collection becomes a clean bill of health. The certainty is never
dropped and never abbreviated into the word alone: the explanation sits in the cell beside it.
This was exercised for real — the seeded estate answers `false` / `uncertain` for `S-1-1-0`
and `S-1-5-11` on `\\FS01\Finance`, and the page says *Unknown*, not *No*.

### 3. Two "Administrators" rows are two groups

`lib/identity.ts` decides what to call a principal and when the name is not enough.
`ambiguousLabels(list)` is a property of **the list**, not of the principal: the same group is
unqualified on its own page and carries `(on fs01)` in a table beside its twin on `fs02`. The
storage key is under every name regardless, except where the name already *is* the key —
an undescribed trustee falls back to its SID, and printing it twice adds a line and no fact.

Nothing here invents. `is_group: null` renders as `kind unknown`, never as "account"; a
missing name falls through to the SID rather than to "(unknown)", because the SID is the one
identifier that can still be searched for.

### 4. Paging carries its position in the URL, and says when it does not trust it

The API pages forward only — a response carries `next_cursor` and nothing that walks back.
`lib/paging.ts` keeps the *trail* of cursors used to reach the current page in the query
string, so "back" is the trail minus its last element. Nothing is stored between requests,
every page is a link, and a finding can be pasted into a ticket.

Three refusals:

- A trail that is not base64url resets to page one **and says so** (`PagePosition`, an
  `alert`). A page one displayed as somebody's page four turns a short list into the end of a
  list.
- `Pager` never claims a total it was not given. `page.total` is null when the endpoint cannot
  count cheaply, and null means not counted, never zero.
- `PagePosition` also offers the way back from a deep page, which is what an API error leaves
  you on. **The live run found this.** A hand-edited cursor is still syntactically base64url,
  so `decodeTrail` cannot judge it; it reaches the API, which correctly answers 422 with no
  rows — and before this component the reader was left on an error banner with no exit.

### 5. One section, one query

Each detail page renders exactly one panel, chosen by `?tab=`. Membership traversal and
effective access are bounded but real work; a principal page that eagerly loaded direct
groups, effective groups, reachable shares, reachable directories and ACL references would
spend most of its time on the panels nobody opened. The tabs are links, not buttons, so the
panel and its page position are in the URL.

## Files and modules added or materially changed

### Frontend — new pure modules

| File | Lines | Contents |
| --- | ---: | --- |
| `lib/paging.ts` | 121 | The cursor trail, `hrefWith`, `pageNavigation` |
| `lib/identity.ts` | 150 | Principal labels, kinds, SID domains, list-scoped ambiguity, hrefs |
| `lib/resources.ts` | 146 | UNC parsing, parent/share-root derivation, the breadcrumb, hrefs |
| `lib/acl.ts` | 295 | Raw-entry notes, inheritance, DACL states, ACL digest, boundary verdicts |
| `lib/access.ts` | 188 | The certainty table, limiting layer, mask notes, the page summary |

### Frontend — new components

`components/Identity.tsx` (`PrincipalName`, `PrincipalCell`), `components/Layer.tsx`
(`RawLayer`, `EffectiveLayer`, `FactSection`), `components/RawAcl.tsx` (`ShareAceTable`,
`NtfsAceTable`), `components/EffectiveAccess.tsx` (`Verdict`, `Rights`,
`ResourceAccessTable`, `PrincipalAccessTable`, `TokenSummary`, `EnumerationNotice`,
`FindingsNotice`), `components/Pager.tsx` (`Pager`, `PagePosition`), `components/Tabs.tsx`
(`Tabs`, `Breadcrumbs`), `components/Facts.tsx` (`FactList`, `NotReported`, `TriState`,
`Maybe`, `NoteList`, `Provenance`).

### Frontend — new pages

`app/identities/principal/page.tsx` (six panels), `app/resources/server/page.tsx`,
`app/resources/share/page.tsx` (three panels), `app/resources/directory/page.tsx` (two
panels).

### Frontend — changed

- `lib/contracts.ts` — 41 new interfaces and unions for the identity, resource, and
  access views.
- `lib/api/adg.ts` — 17 fetchers, 15 new `USED_PATHS` entries, and `segment()`, which
  percent-encodes an identifier into a path segment the way the API's own tests do
  (`quote(value, safe="")`).
- `app/search/page.tsx` — every hit is now a link to the page for that object. A search
  result that leads nowhere is half a feature.
- `app/resources/page.tsx` — the server name links to the server page instead of re-searching.
- `app/globals.css` — +294 lines: the raw/effective distinction, badges, verdicts, tabs,
  pager, breadcrumb, and the principal name block.
- `README.md` — a "Detail pages" section.

### Tests

`tests/factories.ts` (fixtures that start from the *uninformative* value, so a test that
cares about a field has to say so), `tests/paging.test.ts`, `tests/identity.test.ts`,
`tests/resources.test.ts`, `tests/acl.test.ts`, `tests/access.test.ts`,
`tests/views.test.tsx` (components, jsdom), `tests/pages.test.tsx` (the four pages end to end
against a stubbed API), and 117 new cases in `tests/contracts.test.ts`.

## Schemas and contracts introduced or changed

**None.** No endpoint, no payload, no status code, no OpenAPI regeneration.

What did change is the frontend's side of the existing contract. `tests/contracts.test.ts`
now checks 38 further schemas in both directions (58 in all) — every field `lib/contracts.ts` reads must
be one the API declares, **and** no required field may be one the frontend ignores. The second
direction is the one that bites: it is why the interfaces carry every property of
`NtfsResourceDetailView` rather than the nine a page happens to render today.

Three vocabulary unions are pinned against the API's own enums, because a value the API can
send and a union cannot hold falls through every `switch` in `lib/access.ts` at runtime, in
the one place this product must not be vague: `AccessCertainty`, `LimitingLayer`,
`MemberInclusion`.

## Tests run and exact results

Windows 11, Node 22.20.0, Python 3.13.14, PostgreSQL 16 in Docker.

| Command | Result |
| --- | --- |
| `npm run lint` | clean |
| `npm run typecheck` | clean |
| `npm run test` | **401 passed** (13 files), 2.1s — 147 before this phase |
| `npm run build` | succeeded; 21 routes, all dynamic |
| `.\scripts\frontend-check.ps1` | **Frontend checks passed** (lint, typecheck, test, build) |
| Live page probe (below) | **16/16** |

No backend or collector file was touched, so their suites were not re-run. The backend was
left exactly as the concurrent Phase 5B session committed it.

### The live run

Static rendering proves markup, not that a UNC path survives a URL. So the whole stack was
run for real:

1. A **separate** database `adg_p6b` on the existing PostgreSQL container, migrated with
   `alembic upgrade head`. The concurrent session's databases were never touched, and
   `adg_p6b` was dropped afterwards.
2. The API on `127.0.0.1:8100` in development auth mode. **Note for anyone repeating this:**
   plain `uvicorn` fails readiness on Windows with `Psycopg cannot use the 'ProactorEventLoop'`.
   Add `--loop app.runtime:event_loop_factory`, which the readiness error itself tells you.
3. Five canonical scan transcripts replayed through the real ingestion API
   (`03-nested-group-grant`, `08-inherited-ace`, `09-broken-inheritance`,
   `10-smb-more-restrictive`, `11-ntfs-more-restrictive`).
4. `next dev` on `:3100`, signed in through the BFF's development login, then every detail
   page fetched with the session cookie and its rendered text asserted.

All sixteen checks passed: the four pages, every tab, the breadcrumb's links, the search →
principal → share → principal round trip, a SID that does not exist, and a tampered page
position.

**What the live run settled that no unit test could:** a UNC path percent-encoded into a path
segment (`%5C%5Cfs01%5Cfinance%5Creports`) reaches FastAPI intact through the browser, the
Next.js router, the BFF, and Starlette's `{resource:path}` converter — including on
`/api/v1/access/resources/{resource:path}/principals`. That was the main reason identifiers
travel as query parameters on this application's own URLs; it is now measured rather than
assumed on the API's.

Rendered pages were also captured as PNGs with headless Edge and looked at. Three things the
pictures changed:

- the effective-rights summary read "2 of them **is** a bound";
- `Direct members: 0` appeared on a user's page, which is noise at best and an invitation to
  read an account as a group at worst — the row now exists only for a group;
- an undescribed trustee printed its SID twice, once as the name and once as the key.

## Known limitations

1. **The ACL panel has no explicit-only filter.** The API does not offer one, and filtering a
   page in the browser would describe the page rather than the ACL. The page says so where a
   reader would look for the control.
2. **Paging is forward-and-back, not jump-to-page.** Cursors are opaque and offsets are not
   exposed, so there is no page 7 without walking to it. The trail makes "back" exact.
3. **`?tab=` means one panel per render.** Comparing a share's raw ACL with its effective
   answer is two page loads. A side-by-side view would need both queries on every load, which
   is the cost this design refuses.
4. **Phase 5B's new surfaces are not in the UI.** `GET /api/v1/access/explain`,
   `/api/v1/access/paths`, and `/api/v1/groups/{id}/resource-impact` landed in the concurrent
   session while this phase was in flight. The directory page shows *that* a principal reaches
   a resource; the causal explanation of *why*, and a group's blast radius, are the obvious
   next increment.
5. **The token panel lists a count, not the SIDs.** `TokenView.entries` is fetched and only
   its length and `membership_complete` are rendered. The entries are the material for an
   explanation view, which is limitation 4.
6. **No jump from a raw ACE to its trustee's effective access on that same object.** The
   trustee links to its own page; getting the answer for this directory is two more clicks.
7. **The breadcrumb is derived from the path, not from observations.** An ancestor crumb may
   be a directory no run has read. It is still shown — and the page it leads to says so —
   because hiding the path would be worse.
8. **Filtering is thin.** Only `include` on effective members is exposed. Server-side
   filtering by rights, certainty, or trustee kind would need API support first.
9. **The live run used one estate.** Five transcripts, one server, one share, three
   directories. Rendering at the scale the pagers exist for (a group with 40,000 members) has
   not been observed.
10. **Accessibility is asserted, not audited.** Landmarks, `aria-current`, table headers, and
    alert roles are under test; no screen reader or contrast audit has been run.

## Security and privilege assumptions

- **Unchanged from Phase 6A.** No new endpoint, no new capability, no change to the
  authorization boundary. Every page calls the API with the session's token through the
  existing BFF, and a route the account may not read returns 403, which renders as the
  `forbidden` state rather than as an empty table.
- **Nothing computes permissions in the browser.** Every verdict on screen is an engine
  answer with the engine's own certainty attached. `lib/access.ts` chooses wording and
  nothing else — a second implementation of the access check in TypeScript could only ever
  disagree with the one checked against Windows' `AuthzAccessCheck`.
- **No new data is exposed.** These pages read endpoints Phases 1–5 already served behind
  `identities:read`, `resources:read`, and `access:read`.
- **Read-only posture unchanged.** The BFF still proxies `GET` only.
- **Identifiers are user input and are treated as such.** They are percent-encoded into one
  path segment and validated by the API's own parsers; a share key sent where a directory is
  expected is refused by the API rather than converted.

## Migration and compatibility notes

1. **No database migration**, no table, column, or index change.
2. **No collector change.** The transports, payloads, and credentials are as Phase 6A left
   them.
3. **No new dependency**, frontend or backend.
4. **`Pager` lost its `trail` prop**, which moved to the new `PagePosition`. Both are internal
   components introduced in this phase; nothing outside it can be affected.
5. **`/resources` server links changed target**, from a search for `\\<server>` to
   `/resources/server?key=<key>`. A bookmark of the old link still works — it is a search URL.

## Prerequisites for the next prompt

1. **Every new list must page server-side and use `lib/paging.ts`.** Do not add a client-side
   filter over a page: it describes the page, not the set, and it makes the pager a claim
   about a different list than the one being paged.
2. **Every verdict must be rendered through `accessVerdict`.** Do not branch on `access`
   alone anywhere. `false` with `at_least` is "none established"; a view that words it as
   "No" reports a gap in collection as a clean result.
3. **Every raw-entry table goes inside `RawLayer` and every engine answer inside
   `EffectiveLayer`.** A new section that renders entries in a bare `<table>` is the
   regression this phase exists to prevent.
4. **Every new view still asks `GET /api/v1/collection/status`** and passes the verdict to
   `classify()` — unchanged from Phase 6A, and now also true of every panel.
5. **Add new response fields to `EXPECTED` in `frontend/tests/contracts.test.ts`.** The
   "no required field this application ignores" direction will fail on a new required field
   until it is listed, which is the test working.
6. **A principal is identified by `key`, not by `sid`.** `fs01|S-1-5-32-544` and
   `fs02|S-1-5-32-544` are different groups; a link built from `sid` sends the reader to the
   wrong one.
7. **Risk and change views (Phase 7) should reuse `Verdict`, `Rights` and `PrincipalName`**,
   and must surface `CollectionHealth` — a risk listing is exactly where an empty result gets
   misread.
8. **The explanation surfaces are ready to wire.** Phase 5B's `access/explain`,
   `access/paths` and `groups/{id}/resource-impact` need a view; a "why does this principal
   have this?" panel on the directory page and a blast-radius panel on a group page are the
   natural homes.
9. **To repeat the live run**, see "The live run" above. Use a database of your own on the
   shared container, and `--loop app.runtime:event_loop_factory`.

## `git status --short`

Immediately after the commit. **Every entry below belongs to the concurrent session** and
was deliberately left out of this commit:

```
 M backend/tests/contracts/test_smb_collector.py
?? frontend/lib/api/explain.ts
?? frontend/lib/derived.ts
```

That session committed Phase 5B (`58d90d3`, `78c787a`, `6cd8ef9`) partway through this phase
and has now begun frontend work of its own. Nothing in this commit touches those files, and
nothing in this phase touches the backend at all. Worth knowing before the next prompt: the
frontend is no longer a single-session area, so check `git status` before staging, and stage
paths explicitly rather than with `git add -A`.

**Commit:** `b53d634` — 32 files, +7,219/-5.
