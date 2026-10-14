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
| Commits | `145284a` the feature; `cd6016c` engine verdicts at stop points, benchmark; `__FINAL__` this report and evidence |
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
| `…/directory-access/export?mode=summary\|paths` | CSV (§7) |

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
* OpenAPI snapshot: 4 paths and 15 schemas added, none changed or removed (verified
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

__EXAMPLE_AFTER_REFINEMENT__

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

__VALIDATION__

## 10. Manual browser acceptance (`directory-access.mjs`, Chromium 1194, 1360×900, local `next start` + API in demo mode over `adg_demo`)

__BROWSER__

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
