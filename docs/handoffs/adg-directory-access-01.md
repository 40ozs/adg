# ADG-DIRECTORY-ACCESS-01 — Reachable directories summarized into access roots, with drill-down

**Status: DRAFT — validation in progress.** Sections marked `__…__` are filled from measured results before the verdict is issued.

The principal view formerly labelled **Reachable directories** is now **Directory access**.
By default it shows **access roots**: the places where a principal's reach begins inside
the shares. Each root shows how many reachable directories lie beneath it without
interruption, and how many places inside that run the answer changes. A lazy **Tree** and the
unchanged flat **All paths** listing sit beside it, and two CSV exports keep the complete
underlying set available. Every verdict is still the access engine's: the summary groups
answers and never evaluates an ACL itself.

## 1. Git

| | |
| --- | --- |
| Repository | `40ozs/adg` |
| Branch | `claude/compassionate-hamilton-3sh5db`. This is the branch assigned to this session. The `feature/ADG-DIRECTORY-ACCESS-01` naming the prompt suggests was not used, because pushes are restricted to the assigned branch. |
| Starting point | `3e26e4a` = `integration/ADG-NEXT16-UI-02` = `deploy/ADG-FREE-STAGING-01` (verdict `READY_FOR_ADG_COMBINED_STAGING_REVIEW`) |
| `origin/main` at start | `1ab01a2` (26 commits behind the starting point; contained in it) |
| Commits | `145284a` the feature · `cd6016c` engine verdicts at stop points, benchmark · `7069db0` draft handoff · `c125d4b` export parameter renamed `content`, bottom-up tree counts, 30 s timeout for the two server-side fetches, stronger sort check · the commit carrying this report |
| Merged / deployed | **No.** Nothing was merged, deployed or opened as a PR. |

**Why the integrated tree, not `main`.** Pre-flight found the active UI work only on remote
branches: `fix/ADG-UI-UPDATE-01`/`fix/ADG-UI-COMPACT-IDENTITY-01` (sortable tables, the
principal layout/view split, compact identity), `fix/ADG-NEXT16-REACT418-01` (Next.js 16) and
`deploy/ADG-FREE-EDGE-SSR-03`, all integrated in `integration/ADG-NEXT16-UI-02`. `main` has
none of the shared patterns this feature must reuse (`SortableTable`, `Identifier`, the
`/identities/principal/{key}/{view}` object model). Building on `main` would have meant
re-implementing them or conflicting with them. `integration/ADG-UI-EDGE-01`'s one extra commit
is a document only. No other worktree existed in this container; no other branch was
modified.

**Shared files touched, reconciled on purpose:**

* `frontend/cloudflare/checks/navigation.mjs`: its view list named the old tab label.
  `"Reachable directories"` became `"Directory access"`; it passes 19/19 again (§9).
* `frontend/lib/identity.ts`: the view keeps `id: "directories"` so every existing link and
  bookmark still works. Only the label and hint changed.
* `frontend/components/EffectiveAccess.tsx`: `ResourceAccessTable` gained an optional
  `directoryAccessFor` prop (the share → directory-access link). Every existing caller
  renders exactly as before.

## 2. The existing engine, reused

| Need | Reused | Not re-implemented |
| --- | --- | --- |
| Which directories could involve the principal | `resources_named_by`'s union (reference index confirmed against current ACEs, plus NULL DACLs), now also `all_resources_named_by`, the same source in one statement | — |
| The token | `AccessService._build_token` | — |
| Each verdict | `AccessService._resource_page` → `resolve_access` → `classify_access` (now also via `evaluate_resources`, chunked 2,000 per bulk read) | No ACL is evaluated outside the engine |
| Rights comparison | `RightsMask.value` (the mask, never the label, as ADR-0005 requires) | — |
| Path identity | `parse_unc_path` (`comparison_key`, `parent`, `segments`) | No string-prefix parent logic |
| Inheritance facts | stored `dacl_protected`, `is_acl_boundary`, `boundary_reason` (ADR-0009) | — |
| Cache identity | `validator_for` over the collection basis (ADR-0017) | — |

## 3. The summarization rules (`backend/app/services/directory_summary.py`)

Pure, deterministic (directories are processed parents first, by depth then key; output is
sorted by share then key), with no I/O.

1. **Input.** Every directory read in the shares that hold a reachable candidate (the
   *outline*: keys and descriptor flags, no ACEs, one statement on `ix_ntfs_resources_share`).
   Also the engine's verdict for every candidate, and for every *stop point* (step 5).
2. **Reachable** means: engine outcome `granted` **and** the directory is a candidate.
   That is exactly what All paths counts as `access: true`.
3. **Root.** A reachable directory whose parent is not reachable. The reason is recorded:
   `share_root`, `no_access` (the parent was read and is not reachable), or `not_observed`
   (no run read the parent, so continuity is not claimed). Different shares are always
   different roots.
4. **Run and descendants.** A reachable directory whose parent is reachable joins its parent's
   root. `reachable_descendants` is the size of that run minus the root. Every reachable
   directory is in exactly one run, so the sum of `1 + reachable_descendants` over all roots
   equals the reachable count. This is asserted in unit, DB and browser tests.
5. **Exceptions, inside a run.**
   * Access continues, but differs from the parent: `rights_widen`, `rights_narrow`,
     `rights_differ` (compared by mask), `certainty_change`, and `inheritance_broken`
     (protected DACL, even when the rights are equal).
   * Access stops at a child of a reachable folder: `access_denied` (engine outcome
     `denied`), `access_unknown` (`indeterminate`, not a finding of no access), `access_ends`
     (`no_grant`), `not_observed` (a folder known only because something below it was read),
     and `owner_only`.
   * **Stop points are evaluated by the engine** even when their ACL names nobody in the
     token, so each carries the engine's own outcome. A grant there can only come from
     ownership (`READ_CONTROL | WRITE_DAC`, or an `OWNER RIGHTS` entry). It is reported as
     **Owner rights only** and *not* counted as reachable, because the candidate index the
     listing pages through does not index ownership (§11).
   * An ACL boundary that leaves *this principal's* answer unchanged (an explicit entry for
     someone else) is **not** an exception. The tree still marks protected folders.
6. **Resumed access.** A reachable directory below a stop point starts its own root.
   `resumes_within` names the root of the run above, and `resumes_below` names the folder in
   between.
7. **Semantic safety.** A row claims only "the engine found N reachable directories below
   here without interruption". `uniform` is true only when `exception_count` is 0, and
   `rights_breakdown` lists every mask held across the run with its count. The UI marks every
   non-uniform root **varies** ("Permissions vary…" in its title).
8. **Tree.** A node is offered when it is reachable, when anything reachable lies beneath it,
   or when it is a stop point. Other children are counted (`hidden_child_count`), not listed.
   `reachable_below` counts every reachable directory beneath a node, including resumed runs.

## 4. API (`backend/app/api/directory_access.py`, capability `access:read`)

| Route | Answer |
| --- | --- |
| `GET /api/v1/access/principals/{id}/directory-access` | totals, roots (`q`, `access`, `share` filters applied server-side, across all roots, before offset paging), `access_levels`, `complete`/`truncation`, token, basis |
| `…/directory-access/tree?parent=` | one level: the share roots, or the offered children of `parent`. `422` for a directory the tree does not offer |
| `…/directory-access/exceptions?root=` | one root's exceptions: path, reasons, the engine's verdict, the parent's verdict, `named` |
| `…/directory-access/export?content=summary\|paths` | CSV (§7) |

* Directories are addressed by **query parameter**: a UNC key cannot pass the frontend proxy
  as a path segment (`lib/api/proxy.ts`).
* **Bounded.** `MAX_SUMMARY_CANDIDATES = 50,000` evaluated paths and
  `MAX_OUTLINE_DIRECTORIES = 250,000` outline rows. Past either, the answer is computed from
  what was read with `complete: false` and the reason named (`candidates`, `outline`), and the
  export file is named `…-partial.csv` with `X-ADG-Complete: false`. The unpaged export is a
  named exemption in `test_access_bounds.py`, held to those ceilings.
* **One computation per collected state.** The four routes share a computation keyed by the
  basis validator, in a per-application LRU (8 entries) on `app.state`. Any ingestion changes
  the basis, and there is no time-to-live. Each route also has its own `ETag`/`304`.
* Current-state only. The new reads are registered as current-state-only in the as-of
  registry and refused by the simulation overlay (both coverage tests updated). `local`
  access is refused with a pointer to the paged listing.
* OpenAPI snapshot: 4 paths and 14 schemas added, none changed or removed (verified
  structurally).

## 5. Frontend

The view keeps its route, `/identities/principal/{key}/directories`; the mode is `?mode=`.
The principal is the path, so switching modes or views never loses it.

* **Summary** (default, server-rendered). Columns: Share · Access root · Effective access ·
  Reachable descendants (numeric sort) · Exceptions (numeric sort), rendered with the shared
  `SortableTable`. The heading reconciles the counts; filters use a `next/form` GET (text +
  access level; `share` from the link); two CSV links; and **varies** / **resumes** /
  **parent unscanned** badges. Each root's exceptions open in a native `<details>` that fetches
  them once, through the proxy, and shows the rights breakdown. Rows stay one line, and paths
  are `.identifier` (ellipsis, the full value in `title` and in the copied text).
* **Tree** (`DirectoryAccessTree`, client). The share roots arrive server-rendered and
  collapsed. Each expansion fetches one level through the proxy (`tree?parent=`); the
  `BranchLoader` caches levels and de-duplicates in-flight requests for as long as the page is
  open, forgets failures so they can be retried, and never prefetches. Nodes are disclosure
  buttons (`aria-expanded`, `aria-controls`, Enter/Space, → opens, ← closes, focus stays on
  the control), with access in words, reason badges, "N reachable below", "N other folders
  not shown", "Show more" past 100 children, and a polite live region.
* **All paths**: the previous flat listing, unchanged, paged with `mode=paths` kept.
* **Reachable shares** links each share to *Directory access* filtered to that share. The
  counts are labelled differently on purpose: shares whose ACL names the principal there,
  and "shares holding a reachable folder" here.
* **CSV export** (`app/api/directory-access/export/route.ts`): attaches the session token,
  forwards GET to the one API route, and passes the CSV and its headers through. The JSON
  proxy is unchanged.

### Ponytail

As `adg-ui-update-01.md` §3 records, **the repository has no Ponytail component library**.
It was applied as its rules: reuse what exists, then the platform, and add only what is
missing.

| Need | Used |
| --- | --- |
| Dense sortable table | `SortableTable` + `lib/sort.ts` (numeric counts, `aria-sort`) |
| View/mode switch | `filter-strip` links with `aria-current` (as the effective-members filter) |
| Disclosure | native `<details>` (as Identity information) |
| Paths | `Identifier` / `.identifier` |
| Badges | `.badge`, `.badge-deny`, `.badge-explicit`, `.badge-inherited` |
| Empty / error / coverage | `classify`, `StateMessage`, banners |
| Paging | `Pager` / `pageNavigation` |
| Search form | `next/form` |

New, because nothing existed: the lazy tree (no tree component in the codebase), the
exceptions disclosure (it loads on first open), and the export route handler. No dependency
was added.

## 6. Example (Null Meridian, `adg_demo`, fingerprint `228b9dce…f9efd0`)

Aaron Adams (`…-1885`, an Engineering-FS member; scenario C's share):

| | Before (All paths) | After (Summary) |
| --- | --- | --- |
| Rows | **272** directory rows, every one `access: true` | **43** access roots |
| Reachable | 272 | 272 = 43 roots + 229 reachable descendants |
| `\\NM-ENG-FS01\Engineering` | 33 separate rows | **1 root**: Modify, **32 reachable descendants**, **2 exceptions** (`Docs`, `Runbooks\Payments-Legacy`: inheritance broken, Modify), **varies** |

After stop points were evaluated by the engine (final code, same principal):

* totals are unchanged (272 reachable, 43 roots, 44 exceptions, 41 shares, 272 candidates,
  378 directories read in those shares);
* exception reasons across all roots: **Access ends ×25, Inheritance broken ×17, Wider
  rights ×4** (one exception can carry two reasons);
* `\\NM-FIN-FS01\Finance`: a root with **0 reachable descendants and 7 exceptions**.
  Accounting, Audit, Budgets, Commissions, Reports, Tax and Treasury each read *Access ends*,
  are not named (no entry names Aaron or his groups), and carry the engine's verdict
  `no_grant`. The share root is readable; the department folders are not. The flat list
  showed this as one row, with nothing to say where access stopped;
* the Engineering run's rights breakdown is `Modify ×33`. It is uniform in rights, and the
  2 exceptions are inheritance breaks that keep Modify. That is why the row says **varies**
  while the breakdown has one entry: inheritance changed even though the rights did not.

## 7. Export

| Mode | Contents |
| --- | --- |
| `summary` | One row per root: server, share, root path, effective access, mask, certainty, reachable descendants, exceptions, uniform, exception reasons with counts, rights breakdown, why it starts there, `resumes_within` |
| `paths` | **Every candidate the engine evaluated**, reachable or not, file or directory (the complete set All paths pages through): path, share, kind, observed, reachable, outcome, effective access, mask, certainty, limiting layer, conditions, ACL boundary, boundary reason, inheritance protected, summary root, exception reasons |

There was no export of this listing before, so no existing export semantics changed. Cells
that a spreadsheet would execute (`= + - @`, tab, CR) are prefixed with `'`.

## 8. Tests added

| Suite | Count | What |
| --- | ---: | --- |
| `backend/tests/services/test_directory_summary.py` | 25 | every rule in §3, determinism under shuffled input, case-insensitive paths, counts reconcile, a 24,000-directory pure run |
| `backend/tests/db/test_directory_access_api.py` | 23 | engine ≡ listing ≡ estate oracle; roots; protected/deny/widen/resumed; filters; tree levels; 422s; exceptions; both exports; cache reuse (0 resource/ACE reads on expansion); `304`; invalidation on ingestion; **statement count equal at 40 and 400 directories** |
| `backend/tests/api/*`, coverage registries | +2 | export bounds exemption with substitute checks; route capability matrix; as-of/overlay registries |
| `backend/tests/benchmarks/test_directory_access_benchmark_harness.py` | 2 | the benchmark still runs |
| `frontend/tests/directory-access.test.tsx` | 24 | summary default, varies vs uniform, numeric sort with no request, filters, empty filter, long path, exports, partial warning, modes, All paths, tree collapsed, lazy load by query param, nested, collapse, no refetch, keyboard, retry, no navigation, loader de-dup/caching, exceptions lazy + once, a11y labels, share link, verdict words |
| `frontend/cloudflare/checks/directory-access.mjs` | 20 checks | browser gate (§10) |

`backend/tests/support/directory_estate.py` generates the estates. Every directory carries
its full inherited DACL, as stored. There are protected folders without the subject, Deny,
widening grants, protected re-grants, unrelated-entry boundaries, and resumed access. An
in-order ACE oracle records what the subject should reach.

## 9. Validation

All on this container (Linux x86_64, Python 3.13.16, Node 22.22, PostgreSQL 16.15). The
commands are CI's (`.github/workflows/ci.yml`).

**Baseline** (the starting tree `3e26e4a`, before any change):

| Command | Result |
| --- | --- |
| `pytest -q -m "not smoke"` | 5,777 passed, 124 skipped |
| `ADG_RUN_SMOKE_TESTS=1 pytest -q -m smoke` | 1,240 passed, 1 xfailed (32 min 52 s) |

**Final:**

| Command (directory) | Tree | Result |
| --- | --- | --- |
| `ruff check .` (backend) | `c125d4b` | All checks passed |
| `ruff format --check .` | `c125d4b` | 409 files already formatted |
| `mypy app tests` | `c125d4b` | no issues in 408 source files |
| `pytest -q -m "not smoke"` | `c125d4b` | **5,810 passed, 124 skipped**, 0 failed (+33: 25 summarizer, 2 bounds, 1 harness, the rest registries and matrix) |
| `ADG_RUN_SMOKE_TESTS=1 pytest -q -m smoke` (whole DB suite) | `7069db0` | **1,264 passed, 1 xfailed**, 0 failed, 36 min 49 s (+24: 23 directory-access, 1 harness). The xfail is pre-existing |
| `ADG_RUN_SMOKE_TESTS=1 pytest -q -m smoke tests/db/test_directory_access_api.py tests/benchmarks/test_directory_access_benchmark_harness.py tests/test_database_smoke.py` | `c125d4b` | 26 passed |
| `python -m app.contracts.openapi` → snapshot tests | `c125d4b` | current (4 paths, 14 schemas added, none changed or removed) |
| `npm run lint` (frontend) | `c125d4b` | clean |
| `npm run typecheck` | `c125d4b` | clean |
| `npm run test` | `c125d4b` | **40 files, 1,366 passed** (baseline measured at `3e26e4a`: 39 files, 1,306; +60 = 24 directory-access tests + 12 new schemas × 3 contract-field checks) |
| `npm run build` (`next build --webpack`, Next.js 16.4.0) | `c125d4b` | compiled; `/api/directory-access/export` route present |

The whole DB suite last ran on `7069db0`. The one later change that touches backend behaviour
(`c125d4b`) renames this feature's export query parameter `mode` → `content` and replaces
the tree count with an equivalent bottom-up pass. For `c125d4b`, the whole hermetic suite and
every directory-access DB test were re-run, as listed. **One failure was found and fixed on
the way:** the first hermetic run on `7069db0` failed
`tests/demo/test_demo_mode.py::TestNoRuntimeSwitch::test_no_request_in_the_published_api_can_name_the_mode`,
because the export's `mode` parameter broke the rule that no published request field may
name a mode. That is the reason for the rename; the test passes on `c125d4b`.

Warnings: 69 in the DB suite and 6 hermetic, against 67 and 7 at baseline. The two
directory-access ones are Starlette's `HTTP_422_UNPROCESSABLE_ENTITY` deprecation from the
application-wide validation handler, which the existing 422 routes already raise.

Not run: the Windows-only collector Pester suite (no PowerShell here; this change touches no
collector code), and the Docker Compose config check (no change to compose files).

### Performance (`python -m tests.benchmarks.directory_access_benchmark --estates 4x250 8x1000 20x1000 --repeat 5`)

__BENCHMARK__

## 10. Manual browser acceptance (`directory-access.mjs`, Chromium 1194, 1360×900, local `next start` + API in demo mode over `adg_demo`)

`STAGING_URL=http://localhost:3000 node cloudflare/checks/directory-access.mjs`, on the
final build (`c125d4b`, `next start` + `python -m app` in demo mode). The principal is Aaron
Adams (`…-1885`). The result was **20/20 passed** on the final run and on each of four
earlier runs during development. Each move is checked for a document reload (a main-frame
document request, and a marker on `window`).

| Prompt step | Check | Result |
| --- | --- | --- |
| 1 | open the principal | Aaron Adams |
| 2–3 | Directory access opens, Summary is current | no reload |
| 4–6 | roots, not every descendant; counts | 43 rows for 272 reachable; Σ(1 + descendants) = 272; Engineering = Modify, 32, 2 exceptions, *varies* |
| 7 | exceptions indicated | 24 roots marked *varies*, 24 with an exceptions disclosure |
| 8 | sort by root/path | rows ordered by path (`\\NM-ARCH-FS01\Archive` … `\\NM-SHARE-FS01\Data`), `aria-sort=ascending`, no reload |
| 9 | sort by descendant count | descending numerically: 32, 13, 12, 11, 10…; no reload |
| 10–11 | expand a root (Tree) | 11 children of `\\NM-ENG-FS01\Engineering`, **1** request (`?parent=\\nm-eng-fs01\engineering`), no reload |
| 12 | nested expansion | `Benchmarks` opened, 2 requests in total |
| 13 | collapse; reopen | reopened from cache, still 2 requests |
| 14–16 | another view and back | Effective groups → Directory access: h1 Aaron Adams, same SID in the path, Summary current, no reload |
| 17–18 | Tree shows the shares collapsed | 41 share roots, 0 open, **0** browser tree requests on load |
| 19–20 | inspect a boundary | exceptions: `…\Docs`, `…\Runbooks\Payments-Legacy`, *inheritance broken*, Modify (parent Modify), 1 request, none on reopen. Tree: `Docs Modify inheritance broken 3 reachable below` under `Engineering Modify access root 32 reachable below` |
| 21 | All paths | the flat listing, 100 rows on page 1 |
| 22 | long paths | 42 closed rows, all 39.8 px; 24 clipped identifiers, every one with its full path as title |
| 23 | keyboard | Enter opens, ← closes, focus stays on the control |
| 24 | console/runtime | 0 page errors, 0 console errors (plus the pre-existing `/favicon.ico` 404, §11) |
| — | export | `content=paths`: 272 rows, 272 reachable, `X-ADG-Complete: true` |
| — | Reachable shares → Directory access | `share=nm-arch-fs01\|archive`: 1 root, one share |

**Layout at three widths** (measured on the summary): at 1360 px the table is 936 px in a
936 px box, so no column is scrolled away. At 1024 and 390 px the table scrolls inside its
own container and the page does not (`documentElement.scrollWidth` = viewport width). Two
defects were found and fixed this way during development: the count columns were scrolled
off at 1360 px, and the page scrolled sideways at 390 px. The second was caused by a visually
hidden span escaping the table scroller; its cell is now positioned.

**Regression:** the existing navigation gate (`cloudflare/checks/navigation.mjs`) passed
**19/19** on the same build. Its view list was updated for the renamed tab (§1).

Screenshots: [`adg-directory-access-01-summary.png`](adg-directory-access-01-summary.png),
[`adg-directory-access-01-tree.png`](adg-directory-access-01-tree.png).

## 11. Limitations

* **Ownership is outside the candidate set, as before.** The reference index records ACE
  trustees and NULL DACLs; a folder that grants only through ownership is not a candidate
  for All paths either. The summary now *finds* such a folder when it is a stop point and
  reports **Owner rights only**, without counting it. A folder reachable only through
  ownership deeper inside an unreachable branch is still not found. That is pre-existing and
  unchanged.
* **Remote (SMB) only.** `access_path=local` is refused by the summary; the paged listing
  still answers it.
* **Current state only.** No as-of or simulated summary; both repositories refuse the new
  reads.
* **Sorting orders the page in hand**, as everywhere in ADG. Roots page 100 at a time; a
  principal with more roots than a page sorts each page, and the caption says so.
* **Files are not summarized.** File candidates are excluded from roots (counted in
  `files_excluded`) and remain in All paths and the `paths` export.
* **The computation cache is per process.** Several API workers each compute once per
  collected state.
* **`/favicon.ico` is a 404** on every page (the app ships no icon). This is pre-existing,
  and the gate reports it separately.
* Not validated on the hosted staging deployment; nothing was deployed (§1).
