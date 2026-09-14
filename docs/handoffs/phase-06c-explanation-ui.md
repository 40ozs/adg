# Handoff — Phase 6C (`phase-06/03-access-explanation-ui.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-06b-core-views.md](phase-06b-core-views.md)
**Collector contract version after this phase:** `1.3` — unchanged.
**Derived-response contract consumed:** `1.0` — unchanged.
**No backend file was edited.** No route, no schema, no migration, no regeneration of
`docs/contracts/v1/openapi.json`.

## Scope completed

Phase 5B made the causality of an access answer a contract. Phase 6B put the *answers* on
screen — who reaches what, and through which groups. This phase is the **why**: one screen
that carries the whole derivation, and the part nobody had yet: what removing each
relationship would actually do.

1. **An access explanation screen** at `/access/explain?principal=…&resource=…`, driven
   entirely by `GET /api/v1/access/explain`. One request; nothing else on the page.
2. **The four outcomes worded as four answers.** `granted`, `denied`, `no_grant` and
   `indeterminate` do not share a word anywhere on the screen, and an inconclusive answer
   carries an alert that forbids the negative reading.
3. **An interactive diagram** — `User → Group(s) → ACE → Resource` — as inline SVG from a
   deterministic layout function, with a table that lists exactly the same nodes and edges.
4. **A route inspector**: every membership hop and whether it can be deleted at all, the
   exact ACE with its position and key, where an inherited entry was actually set, and
   whether the route contributed to the final answer.
5. **The measured removals**, with the case this screen exists for made loud: a removal that
   changes nothing is never worded as a fix, and the alternate routes that make it useless
   are named on the row.
6. **Copy and export** as the response's own JSON and as human-readable text, both with the
   caveats attached.
7. **Deep links from the Phase 6B tables** — a "Why" column on the access tables of the
   principal, share and directory pages.
8. **+219 frontend tests** (401 → 620), and a live run against a real API, a real
   PostgreSQL and a real collector transcript.

## Four things worth reading before the code

### 1. The engine labels an empty mask "No access", and that is the wrong thing to print

`VerdictPanel` originally rendered `effective.rights` through the shared `Rights` component
in every case. `tests/explanation-views.test.tsx` asserts that an `indeterminate` answer
contains no "No access" anywhere on the panel — and it failed, because the engine's label
for a zero mask *is* the string `No access`.

So the verdict said "Unknown", the alert underneath said "not a finding of no access", and
the rights row two lines down said `No access 0x00000000`. Every reader takes the concrete
line over the hedge.

**Fixed** by branching on `verdict.conclusive` in that one place: an inconclusive answer
reads `None established`, with the mask beside it and a note that no right was established,
which is a different statement. The wording matches `lib/access.ts`, which drew the same
distinction for the listing tables in Phase 6B.

The test was written before the defect was known, from the acceptance criterion rather than
from the implementation, which is why it caught it.

### 2. The diagram is not the control surface, on purpose

The acceptance criterion is that accessibility must not depend on the visualization. Two
ways to satisfy it were available: build a focusable, arrow-navigable SVG widget, or make
the picture a picture and put the real controls in the table.

The second was chosen. The `<svg>` is `role="img"` with a written summary that names the
counts and whether the route set is complete; its internals are `aria-hidden`. Selection is
a real `<button aria-pressed>` on every row of the route table, and the diagram follows the
selection. Clicking a node or an edge also selects, because for a mouse user that is the
fastest way in — but nothing is reachable only that way.

A half-built SVG widget that can be focused and not navigated is worse than an honest
picture beside a real table, and it is the shape that passes an automated audit while
failing a person.

`structureRows()` renders the diagram as text, edge for edge.
`tests/explanation.test.ts` asserts the two lists are equal as sets, against both a fixture
and the captured response body — so the accessible copy is the same facts rather than a
paraphrase that drifts.

### 3. The graph layout has to survive a cycle, because cycles are real here

`ExplanationGraphView` can contain a membership cycle: `05-cyclic-group-graph` is a
canonical fixture, and the API reports cycles in `cycles` as a finding rather than filtering
them out.

Layering by longest path from the subject is the right visual answer — a group reached both
directly and through two hops belongs to the right of the chain that reaches it, so no edge
points backwards — but longest path is undefined on a cyclic graph. `layoutGraph` relaxes
edge by edge and **caps the passes at one per node**, which terminates on any input and
degrades to a stable, arbitrary-but-deterministic column for a node inside a cycle.

Ordering inside a column is by label then by id, so identical facts draw an identical
diagram. A layout that moved between two renders of the same answer would make a screenshot
useless as evidence.

### 4. Typing a SID into a form is not something a non-expert does

The screen is anchored by two required parameters, which is correct — an access route that
answers about everybody when a parameter is omitted is the Cartesian product of the estate
wearing a query string. But it means the entry point matters as much as the screen.

So the Phase 6B access tables gained an optional `explainFor` / `explainOn` prop and a "Why"
column. The prop is **optional and the link is absent without it**, deliberately: a caller
that does not know whose answer it is showing cannot produce a link, and an explanation of
the wrong pair is worse than no link at all.

The share page passes the **directory** it publishes rather than the share, because an
explanation is always anchored on a directory and both layers are in the answer either way.

## Files and modules added or materially changed

### Added

| File | Lines | Contents |
| --- | ---: | --- |
| `frontend/lib/derived.ts` | 290 | The derived-answer contract v1.0 as TypeScript. Shared primitives imported from `lib/contracts.ts`, never restated |
| `frontend/lib/explanation.ts` | 1024 | Every pure decision on the screen: outcome wording, route reading, the graph layout, the removal notes, the caveats, both exports |
| `frontend/lib/api/explain.ts` | 79 | `EXPLAIN_PATHS`, `fetchAccessExplanation`, `fetchAccessPathPage` |
| `frontend/app/access/explain/page.tsx` | 148 | The screen. One API call plus the coverage verdict |
| `frontend/components/Explanation.tsx` | 387 | `VerdictPanel`, `LayerPanel`, `AceTable`, `RemovalPanel`, `CautionPanel`, `BasisNote`, `ExplanationHeader` |
| `frontend/components/ExplanationExplorer.tsx` | 321 | The one client component: selection, the route table, the inspector, the structure list |
| `frontend/components/AccessGraph.tsx` | 170 | The inline SVG and its written summary |
| `frontend/components/ExplanationExport.tsx` | 109 | JSON and text, with a textarea that works without clipboard permission |
| `frontend/components/ExplainLauncher.tsx` | 84 | A plain `GET` form, so the answer stays addressable |
| `frontend/components/explanation.module.css` | 213 | A CSS module — nothing here is reused by another page |
| `frontend/tests/explanation-factories.ts` | 605 | The four scenarios, built by hand |
| `frontend/tests/explanation.test.ts` | 689 | 82 tests, pure |
| `frontend/tests/explanation-contract.test.ts` | 365 | 73 tests against the published schema and the captured body |
| `frontend/tests/explanation-views.test.tsx` | 551 | 49 tests, rendered |
| `frontend/tests/explanation-page.test.tsx` | 248 | 15 tests, the whole page against a real captured response |
| `docs/handoffs/phase-06c-explanation-ui.md` | this file | |

### Changed

| File | Change |
| --- | --- |
| `frontend/components/EffectiveAccess.tsx` | Optional `explainFor` (resources) and `explainOn` (principals) props and a "Why" column. **Purely additive**: without the prop the tables render exactly as before, and every Phase 6B test passes unchanged |
| `frontend/app/access/page.tsx` | The launcher card |
| `frontend/app/identities/principal/page.tsx` | Passes `explainFor={principal.key}` |
| `frontend/app/resources/directory/page.tsx` | Passes `explainOn={resource.key}` |
| `frontend/app/resources/share/page.tsx` | Passes `explainOn={root.key}` — the directory, not the share |

## Important architecture decisions

1. **`lib/derived.ts` is a separate module from `lib/contracts.ts`.** The derived contract
   versions independently, carries a `schema_version` a listing response does not, and is
   checked against a different published document. The primitives the two share —
   `RightsView`, `PrincipalSummary`, `TokenView`, `FindingView`, `ResourceRef`, `ShareRef` —
   are imported rather than redeclared; a second declaration is a second thing to keep
   current, and the one that falls behind is invisible.
2. **Nothing on this screen computes access.** No mask is intersected, no ACE position
   compared, no Deny interpreted. The only joins are on stable identifiers: `ace_key` to the
   applied entry, edge ids to the graph, path ids to the removal targets.
3. **One client component.** The verdict, both layers, the measured removals and the caveats
   render on the server. A reader with no JavaScript loses the highlight and keeps every
   fact and every warning.
4. **`cautions()` is one function, used by both the screen and the text export.** An
   exported explanation that drops a warning is how "ADG says nobody has access" gets quoted
   about a directory nobody scanned.
5. **The JSON export is the response body verbatim.** A reshaped subset would be a second
   description of the answer, and the only thing worse than no export is one that quietly
   says something else.
6. **A CSS module rather than another block in `globals.css`.** Nothing here is reused, and
   the diagram alone carries a dozen rules that would otherwise live in the shell's
   stylesheet forever.
7. **`explanationText()` reads no clock and does no locale formatting**, so two exports of
   one answer are byte-identical and a diff between two is a difference in the estate.
8. **The removal table never sorts or filters.** The API's order is the deterministic
   enumeration order; re-ordering in the browser would make one screen's "first removal"
   different from another's.

## Schemas and contracts

**Nothing introduced, nothing changed.** This phase is a consumer. `EXPLAIN_PATHS` names the
two derived routes it calls and `tests/explanation-contract.test.ts` asserts the published
OpenAPI document serves both.

The frontend's belief about the response shape is checked against
`docs/contracts/v1/derived/access-explanation.schema.json` in both directions — every field
read must be declared, and no required field may be one this application ignores — plus
eight enum unions checked for exhaustiveness against the schema, because a value the API can
send and a union cannot hold falls through a `switch` at runtime in the one place this
product must not be vague.

## Tests run and exact results

All commands from `C:\code\adg\frontend`.

| Gate | Command | Result |
| --- | --- | --- |
| Explanation logic | `npx vitest run tests/explanation.test.ts` | **82 passed** |
| Derived contract | `npx vitest run tests/explanation-contract.test.ts` | **73 passed** |
| Components | `npx vitest run tests/explanation-views.test.tsx` | **49 passed** |
| The page | `npx vitest run tests/explanation-page.test.tsx` | **15 passed** |
| Whole suite | `npx vitest run` | **620 passed (17 files)** — 401 before this phase |
| Types | `npx tsc --noEmit` | **clean** |
| Lint | `npx eslint .` | **clean, 0 warnings** |
| Production build | `npx next build` | **compiled; `/access/explain` 8.96 kB, 115 kB first load** |

**219 tests are attributable to this phase.** No existing test was edited, weakened or
deleted.

### The live run

Not a claim from a stub. The screen was rendered by a real Next.js server (`next start`)
against a real API (`uvicorn`), a real PostgreSQL, and the `04-multiple-membership-paths`
transcript replayed through the ingestion endpoints.

The dev database was two migrations behind (`0003_smb_resources`); `alembic upgrade head`
brought it to `0006_effective_access`. Seeding and the direct API call used an administrator
token from the development login; the browser session used the **auditor** account, so the
page was rendered under the capability set a real reader has.

What the live API returned for Alice against `\\FS01\Finance`:

```
outcome: granted | rights: Modify
paths: 4 | removals: 8 | nodes: 10 | edges: 12
6 of 8 removals: changes_nothing = true
1 of 8 removals: revokes_all_access = true
```

What the live page rendered (asserted against the returned HTML):

| Checked | Present |
| --- | --- |
| Verdict, and the engine's own sentence | `Access` … "holds rights" |
| Effective rights and the narrower layer | `Modify`, "the NTFS ACL is the narrower one" |
| Route chains and ids | `Finance-Team`, `p0000` … |
| The alternate-route warning | "6 of the relationships below change nothing when removed … They are marked, and are not fixes. 1 of 8 single removals end access here." |
| The diagram | 10 `data-node`, 12 `data-edge`, matching the response exactly |
| The diagram's summary | "How Alice Smith reaches \\\\FS01\\Finance: 10 nodes and 12 relationships, carrying 4 routes." |
| Route selection controls | 4 `aria-pressed` buttons |
| Both layers, the caveats, the export, the basis | all present |

Two variants were rendered from the same live server:

* `access_path=local` — "Not applied on the local access path. Local access does not cross a
  share", and the diagram correctly drops to 8 nodes and 9 edges with the share entry gone.
* an unknown principal — the not-found state with the API's own message, no verdict and no
  route table.

**A local-run trap worth recording.** Recent uvicorn installs a `ProactorEventLoop` on
Windows and psycopg refuses it in async mode, so every request 500s with
`Psycopg cannot use the 'ProactorEventLoop'`. The API is deployed in a Linux container so
this is not a product defect, but a local run needs
`asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())` **and**
`uvicorn.Config(..., loop="none")` — setting the policy alone does not work, because
uvicorn's own loop setup overrides it.

### Where each acceptance criterion is checked

| Criterion | Test |
| --- | --- |
| A non-expert can see why access exists without traversing AD | `explanation-page.test.tsx` → "shows why, without anybody traversing AD by hand" (the full chain, named, in order, from the captured body), plus the four "Why" deep-link tests |
| Alternate paths are not hidden | `explanation.test.ts` → "the alternate-route warning" (6 tests) and "never offers a removal that changes nothing as a fix"; `explanation-views.test.tsx` → "marks each removal that changes nothing, and names what survives it" |
| Graph and table represent the same API facts | `explanation.test.ts` → "the diagram and the table carry the same facts"; `explanation-contract.test.ts` → the same assertion against the captured response; `explanation-page.test.tsx` → "draws the same relationships it lists", counting rendered `data-edge` against table rows |
| Accessibility does not depend on the graph alone | `explanation-views.test.tsx` → "can be selected and inspected from the keyboard, without the diagram", "is an image with a written summary", "repeats every relationship as text" |
| Multi-path, deny, no-access and incomplete-data cases | `tests/explanation-factories.ts` builds all four; every describe block in `explanation-views.test.tsx` exercises them |

## Known limitations

1. **No conditional GET.** Phase 5B's prerequisite 7 asks a client to send the `ETag` back
   in `If-None-Match`. `lib/api/client.ts` does not expose response headers, and a
   server-rendered page holds no cache to revalidate against, so nothing here sends one.
   The cost is one full explanation per page load; the correctness is unaffected, because
   the answer is computed fresh every time. Implementing it means a header seam in the
   shared client and a place to keep the validator — both bigger decisions than this screen.
2. **`/api/v1/access/paths` is typed and wrapped but not used.** `fetchAccessPathPage` and
   `AccessPathPageResponse` exist and are contract-checked; no screen pages the routes yet.
   An explanation with `complete: false` says so loudly and offers no "show me the rest",
   because the paged endpoint **does not widen the enumeration** — page five does not go
   beyond `max_causal_paths` — so a "more" control would promise something it cannot do.
3. **The limits are not adjustable from the UI.** `max_causal_paths`, `max_removal_targets`
   and `max_depth` are query parameters the API accepts and this screen never sends, so the
   server's defaults always apply. A truncated answer can only be widened by editing the URL.
4. **The diagram does not route around boxes.** Edges are cubic curves drawn straight from
   source to target; on a dense graph they cross nodes. Legible at the shapes a bounded
   explanation actually produces, and the structure table is authoritative either way.
5. **No zoom or pan.** The diagram scrolls horizontally inside its own container. A 40-group
   estate produces a wide picture that has to be scrolled.
6. **`graph.nodes` of kind `share` are laid out in the object column** beside the resource.
   No captured response has produced one yet; if the API starts emitting them the column
   will hold two boxes rather than one, which is correct but untested against real data.
7. **Every Phase 4C, 5A and 5B limitation is inherited**, and the ones that reach the screen
   are surfaced rather than hidden: `complete: false`, `membership_complete: false`, an
   unobserved descriptor, a non-`observed` ACL provenance and every `FindingView` all appear
   in the caveats panel and in the text export.
8. **No printing stylesheet.** An exported explanation is text or JSON, not a PDF. Reporting
   is a later phase and was deliberately not started here.

## Security and privilege assumptions

- **Read-only, no new collection, no new privilege.** One `GET`, plus the coverage verdict
  every page already fetches.
- **`ACCESS_READ` is required by the API**, declared at the include site in
  `app/api/__init__.py`; `backend/tests/api/test_authorization.py` asserts that list is
  exhaustive against the OpenAPI document. Nothing on the frontend grants access to
  anything: the BFF attaches the session's token and the backend decides.
- **The explanation is the sharpest reconnaissance artifact in the product** — the exact
  group to join to reach a share, and the entry that would have to change. That is what the
  screen is for, and it is why the capability boundary matters more here than on a plain
  answer. The export makes it portable, which is a real disclosure consideration for whoever
  adds a report builder: this exports **one pair**, on demand, to somebody already looking
  at it.
- **`Cache-Control: private, no-cache`** from the API is unaltered; the server-side fetch
  uses `cache: "no-store"`, so no permission answer sits in a shared cache.
- **No access token reaches the browser.** Unchanged from Phase 6A; this screen adds no new
  fetch path.
- The full explanation body **is** serialized into the RSC payload, because the client
  component holds the selection and the export renders from it. That payload travels over
  the same authenticated connection to the same reader who was authorized to see the page.

## Migration and compatibility notes

- **No migration, no schema change, no backend edit.**
- **`docs/contracts/v1/openapi.json` is untouched.**
- **`components/EffectiveAccess.tsx` changed additively.** Both new props are optional and
  the "Why" column is absent without them, so any existing caller — and every Phase 6B test
  — behaves exactly as before.
- **`lib/api/adg.ts` was not edited.** The two derived paths live in `lib/api/explain.ts`
  with their own contract test, which also keeps this phase clear of the file the concurrent
  Phase 6B session was editing.
- **The development database was migrated** from `0003_smb_resources` to
  `0006_effective_access` for the live run. That is the normal local step, not a change to
  the project.

## Prerequisites for the next prompt

**For the risk phase:**

1. **Link to `explainHref(principal, resource)` from every finding.** A risk that names a
   pair and does not link to its derivation makes somebody rebuild the derivation by hand.
2. **A risk rule must not fire on `changes_nothing: true` as a remediation.** The
   "recommended fix" field of a risk finding is exactly where this mistake will be made
   next; `describeRemoval()` already classifies it and can be reused.
3. **`alternateRouteWarning()` is the sentence to reuse**, not to re-word. It distinguishes
   "no single removal helps" from "some of these are not fixes" from "the enumeration was
   cut short", and those are three different things to tell an administrator.
4. Phase 5B's prerequisites 1–5 still hold, `verdict.outcome` over `effective.access` above
   all.

**For whoever extends this screen:**

5. **Read `complete` and `page.has_more` separately** if you add paging. The last page of a
   truncated enumeration carries `has_more: false` and `complete: false` together.
6. **Add a caveat to `cautions()`, not to a component.** The text export is generated from
   that one list, and a warning added anywhere else stops travelling with the finding.
7. **If you make the diagram a real widget, delete the claim in `AccessGraph`'s docblock**
   that it is deliberately not the control surface, and give the SVG real keyboard
   navigation. Half of that change is worse than neither half.
8. **`layoutGraph` must stay pure and deterministic.** Two tests depend on it —
   equality across renders, and set-equality with `structureRows()`.

**For whoever adds the conditional GET (limitation 1):**

9. Add a header seam to `lib/api/client.ts` rather than a second client. Every derived
   endpoint invalidates on the same event, so one place should hold the validator.
10. Do **not** add a client-side TTL on top. It would reintroduce exactly the staleness the
    basis design removes.

## Commit

`8ea7ffe` — *Phase 6C: the access explanation screen*. Not pushed; no remote is configured
for this repository.

## `git status --short`

Taken after the commit.

```
 M backend/tests/contracts/test_smb_collector.py
```

The only entry, and it is the pre-existing unstaged formatting change that predates Phase 4B
and that the last six handoffs have also recorded. Nothing else in the tree belongs to this
phase.
