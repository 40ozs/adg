# ADG-UX-01 — Information architecture and terminology

**Verdict: `READY_FOR_ADG_UX_WAVE_B_WITH_LIMITATIONS`** (limitations: §9).

The primary navigation is now grouped by task: **Investigate**, **Directory**, **Act** and
**System**. "Risks" is called **Findings** everywhere a user reads it, and the `/risks` route,
the `risks:read` capability and every API name are unchanged. User-facing "principal" becomes
"identity" where it is ADG's own word. Section descriptions and page intros now say what each
section is for. Capability filtering, `aria-current`, the skip link and native links all work
as before. No backend, contract, migration, authentication or authorization change was made,
and nothing was deployed.

This report is the **IA and terminology contract** that later ADG UX lanes follow (§3).

## 1. Record

| | |
| --- | --- |
| `STARTING_MAIN_SHA` | `bc178c920155970a9c3abb51b3a275a99ac58317` (= `origin/main`, fetched 2026-10-30) |
| `BRANCH_NAME` | `feature/ADG-UX-01-information-architecture` |
| `WORKTREE_PATH` | `/Volumes/T5 EVO/mmm/worktrees/adg-UX-01` (canonical checkout `/Volumes/T5 EVO/mmm/adg`) |
| Last code commit | `4ef8a85` (feat(ui): group primary navigation and call risks Findings) |
| Final branch SHA | the commit that adds this report (it sits on top of `4ef8a85` and changes docs only) |
| Deploy branch | `deploy/ADG-HOSTED-NEXT16-01` untouched; no Render, Worker, Supabase or database action |
| Concurrent lane | ADG-UX-06 (Operational Overview). No UX-06 branch existed on `origin` at the start. The Overview page (`app/page.tsx`) was **not** touched |

### Prior evidence reconciled

Repository-owned reports read first: `docs/demo/reports/ADG-UI-RECONCILE-01-report.md`
(the newest UI lane on `main`: directories, path-keyed principal model, `SortableTable`),
`ADG-UI-STAGING-PROMOTE-01-report.md` (hosted checks and the `/identities/principal/{key}`
canonical route), `SYNTH-ADG-01-report.md` (current dataset), and
`docs/handoffs/phase-06a-frontend-shell.md` / `phase-08b-risk-alerts.md` (origin of the nav
and of the "placeholder badge" rule). No earlier IA or terminology lane exists. Session
scratchpads were not treated as authoritative.

## 2. Navigation, before and after

Before: one flat list. Overview, Resources, Identities, Access, Risks, Changes, What-if,
Reviews, Collectors, Settings.

After:

| Group | Sections (route, capability) |
| --- | --- |
| **Investigate** | Overview (`/`, none) · Findings (`/risks`, `risks:read`) · Access (`/access`, `access:read`) · Changes (`/changes`, `changes:read`) |
| **Directory** | Identities (`/identities`, `identities:read`) · Resources (`/resources`, `resources:read`) |
| **Act** | Reviews (`/governance`, `governance:read`) · What-if (`/simulations`, `simulations:read`) |
| **System** | Collectors (`/collectors`, `collectors:read`) · Settings (`/settings`, `settings:read`) |

Implementation (`frontend/lib/nav.ts`, `frontend/components/PrimaryNav.tsx`):

- Each `NavItem` has a `group`. `NAV_GROUPS` fixes the group order. `groupNavItems()` drops a
  group with no visible item, so a viewer (no `governance:read` or `simulations:read`) never
  sees an empty "Act" heading.
- The markup stays a `<nav aria-label="Primary">` holding lists of native `<a>`. Each group is
  a nested `<ul aria-labelledby="nav-group-…">` under a plain-text label, so screen readers
  announce "Investigate, list, 4 items". Group labels are not headings (the page `<h1>` stays
  the first heading after the nav) and never take focus.
- Under 60rem each group becomes one row: the label is in a fixed column and its links wrap
  inside their own column. The page never scrolls sideways.
- The capability check is unchanged: set membership on the `/auth/me` list. It is a courtesy
  to the user; the backend still refuses the data.

## 3. IA and terminology contract

Later UX lanes use these user-facing terms. Internal names (routes, API paths, contract types,
DB fields, capabilities, fixtures, component and file names) do **not** follow them.

| Technical/current concept | User-facing term | Status in this lane |
| --- | --- | --- |
| Risk | **Finding** | Adopted: nav label, `/risks` `<h1>` and intro, table caption, Governance item section heading, Coverage link, Demo guide, demo banner, alerts back-link |
| Finding | **Finding** | Unchanged |
| Principal | **Identity** | Adopted in nav descriptions, page intros (Access, Identities, explain, investigate), the explain/investigate form label, the resource "identities with access" tables, review decision table column, campaign copy, identity-page notices, access-graph title, explanation copy |
| Place | **Resource** | No user-facing "place" existed (only the `place` query parameter on `/risks`, which stays) |
| Dependency | **Access path** / **Why this applies** | Adopted "access path" for "route" in Access intros and the explanation explorer. `/status` "Dependencies" means *service* dependencies (database, API), which is a different concept, so it stays |
| Effective permissions | **Effective access** | Already the term in use. "Effective rights" stays where it means the computed rights *mask* (a narrower, precise idea) |
| Collector completeness | **Data coverage** | Collectors description and the Coverage link now say "data coverage". `completeness` only exists in code identifiers |
| Evidence records | **Evidence** | Findings intro says "evidence". Deeper drawer copy is unchanged |

Rules for later lanes:

1. Windows and NTFS terms of art stay exact: *foreign security principal*, *SID*, *ACE*, *ACL*,
   *token*, *share*, *directory*.
2. Never imply a score. Findings are rule matches, and severities are never added up (the
   `/risks` intro says so).
3. Coverage words stay honest. Keep "no findings", "no data", "incomplete" and "not evaluated"
   distinct. No wording in this lane changed those states.
4. Group names (Investigate, Directory, Act, System) are fixed. A new section joins one of them
   and declares `group` in `NAV_ITEMS`. A test fails if an item names an undeclared group.

### Deviations

- **The route stays `/risks`, with no `/findings` alias.** An alias would add a second URL for
  the same page and change nothing for anyone. Renaming the route would break pasted ticket
  links, `frontend/cloudflare/checks/browser.mjs` and `smoke.mjs`, the demo guide anchors
  (`/risks#risk-reading`, `#risk-coverage`) and committed evidence. Add an alias in a later
  lane only if user-visible URLs become a product requirement.
- **"Principal" stays on the identity detail tabs and in deeper technical copy.** Examples:
  "Groups this principal is directly inside", traversal counts, and the simulation and
  system-mapping explanations. These are inside entity pages, which this lane does not
  restructure, and many tests and harnesses assert them. They belong to the entity-context lane.

## 4. Page purpose

Primary-section intros now say what each section is for. Deeper detail stays below:

| Section | Intro (first sentence) |
| --- | --- |
| Findings | Findings show conditions ADG detected in the collected access data, along with the evidence behind each result. (Followed by: rule matches, never a score.) |
| Access | See who can reach a resource, what an identity can reach, and the access paths that explain why. |
| Changes | See what changed in the estate and whether those changes affected access. (Existing "bounded by observations" sentence kept.) |
| Reviews | Review access decisions that require human confirmation. (Existing campaign sentence kept.) |

Overview, Identities, Resources, What-if, Collectors and Settings intros were already
purpose-first, so they are unchanged. Overview is UX-06's area. Nav descriptions (the link
`title`) restate these purposes. They are a convenience, not the only place the information
appears: every page states its purpose in visible text, so nothing depends on hover.

## 5. Route compatibility

| URL | Behaviour |
| --- | --- |
| `/risks`, `/risks?severity=…&rule=…&finding=…&trail=…` | Same page, same parameters, now titled "Findings"; nav marks **Findings** current |
| `/risks/alerts` | Unchanged; marks **Findings** current; back-link reads "Back to findings" |
| `/risks#risk-reading`, `/risks#risk-coverage` | Anchors unchanged |
| `/api/v1/risks/*`, `/api/adg/api/v1/risks/*`, `risks:read` | Unchanged (no backend change) |
| All other section routes | Unchanged |

One hosted check had to change because it asserts visible copy: `smoke.mjs` "risk UI loads"
now expects "Findings, most consequential first" (the table caption) instead of
"Risk findings, most consequential first". The deploy workflow runs the smoke checks from the
same commit it deploys, so the copy and the check change together. `browser.mjs` and
`navigation.mjs` assert nothing that changed: they look up "Identities" by link name, and the
group labels do not change link names.

## 6. Accessibility

Checked in the real production build (`next build --webpack` + `next start`), in Chrome 1280×900
and 375×812, against a stub API that answers only `/auth/me` (admin and viewer capability lists).
Script, stub and output: [`ADG-UX-01-evidence/`](ADG-UX-01-evidence/). Result: **26/26 PASS**
([`nav-check.txt`](ADG-UX-01-evidence/nav-check.txt)).

- `<nav aria-label="Primary">`, nested `<ul>`/`<li>`/`<a>`, and each group list named by
  `aria-labelledby` (unit test and browser).
- Exactly one `aria-current="page"`, on Findings for `/risks`, `/risks/alerts` and
  `/risks?finding=…`. Correct for Identities, Resources, What-if and Reviews deep links.
- The active state is not shown by colour alone: font-weight 600 plus a left accent bar plus a
  raised background.
- The skip link is the first Tab stop, and Enter moves focus to `#main`.
- Tab visits exactly the 10 links in group order. No group label takes focus. Each link shows a
  visible focus outline. Enter on a focused link navigates.
- Group label contrast is 7.43:1. The app has a single dark theme; the `prefers-color-scheme`
  light and dark runs render the same colours.
- No horizontal overflow at 1280 or 375 px. Screenshots:
  [desktop](ADG-UX-01-evidence/nav-desktop-light.png),
  [phone](ADG-UX-01-evidence/nav-phone-light.png),
  [viewer, no Act or Settings](ADG-UX-01-evidence/nav-viewer-desktop.png).
- No custom widgets, no `<div>` links, and no hover-only information (see §4).

Not done: a manual VoiceOver or NVDA pass. The grouping semantics were checked through the
accessibility tree (Testing Library roles and names) and through the DOM in Chrome.

## 7. Tests and gates

Node 22 arm64 (nvm), in `frontend/`, the same commands as `ci.yml`:

| Command | Result |
| --- | --- |
| `npm run lint` | exit 0 |
| `npm run typecheck` | exit 0 |
| `npm run cf:typecheck` | exit 0 |
| `npm run test` | **44 files, 1518/1518 passed** |
| `npm run build` | exit 0 (run again after the last CSS change: exit 0) |
| Browser nav check (§6) | 26/26 |

The first full run had one failure, `explanation-page.test.tsx` "asks for both halves". It
asserted the old copy "one principal and one directory". The assertion and the `Principal` →
`Identity` form label lookups (3 files) were updated, and the next run was all green.

New and changed tests:

- `tests/nav.test.ts`: section order; the four groups and their members; every item in a
  declared group; empty groups dropped for a viewer and for a no-capability account;
  Findings label with the `/risks` href and `risks:read`, and no "Risks" label; every
  description is one sentence without retired terms; existing capability, active-section and
  prefix tests kept, with the viewer order updated.
- `tests/components.test.tsx`: Tab order across group boundaries (7 viewer links); groups
  exposed as named lists, with Act absent for a viewer; Findings `aria-current` on `/risks`;
  description carried on the link; the placeholder badge test kept (synthetic item now
  declares a group).

Not run: the hosted `smoke.mjs`/`browser.mjs`/`navigation.mjs` against staging (that would be
a deploy), backend tests (no backend change), and E2E with a real dataset. Push CI on the branch
runs the full frontend and backend matrix.

## 8. Files changed

Code commit `4ef8a85`, 33 files, +289/−112:

- Nav: `frontend/lib/nav.ts`, `frontend/components/PrimaryNav.tsx`, `frontend/app/globals.css`
- Findings wording: `app/risks/page.tsx`, `app/risks/alerts/page.tsx`, `components/Risks.tsx`,
  `components/Governance.tsx`, `components/Coverage.tsx`, `components/DemoGuide.tsx`,
  `components/Banners.tsx`, `cloudflare/checks/smoke.mjs`
- Identity, access path and purpose wording: `app/access/page.tsx`,
  `app/access/explain/page.tsx`, `app/access/investigate/page.tsx`, `app/changes/page.tsx`,
  `app/governance/page.tsx`, `app/governance/campaigns/[campaignId]/page.tsx`,
  `app/identities/page.tsx`, `app/identities/principal/page.tsx`,
  `app/identities/principal/[key]/layout.tsx`, `app/resources/directory/page.tsx`,
  `app/resources/share/page.tsx`, `components/AccessGraph.tsx`, `components/DecisionForm.tsx`,
  `components/ExplainLauncher.tsx`, `components/Explanation.tsx`,
  `components/ExplanationExplorer.tsx`
- Tests: `tests/nav.test.ts`, `tests/components.test.tsx`, `tests/explanation-page.test.tsx`,
  `tests/explanation-views.test.tsx`, `tests/investigation-page.test.tsx`
- Docs: `README.md` (section list, Findings page name)

Report commit: this file plus `docs/demo/reports/ADG-UX-01-evidence/`.

## 9. Limitations

1. **Identity detail tabs and deep explanation copy still say "principal".** See §3,
   Deviations. Owner: the entity-context lane.
2. **Every page's `<title>` is "ADG".** A per-section document title would help screen-reader
   orientation (WCAG 2.4.2). It needs section layouts, so it was left out of this lane.
   Candidate: a small follow-up, or the entity-context lane.
3. **No `/findings` alias.** This is deliberate (§3). The URL still reads `/risks`.
4. **No manual screen-reader pass.** Semantics were checked through the accessibility tree and
   the DOM (§6).
5. **The browser check used a stub API.** Page bodies show "could not reach its API". The
   check covers the shell, nav, routing and layout only, not data pages. A full local stack
   (Postgres + reset + gate) was not started.
6. **Concurrency with UX-06.** UX-06 had no branch on `origin` when this lane started, and
   this lane did not edit `app/page.tsx` or Overview components. A conflict is only possible
   if UX-06 edits `lib/nav.ts`, `PrimaryNav.tsx` or the nav CSS block.
   **Update after push:** `origin/feature/ADG-UX-06-operational-overview` @ `a03f72a` appeared
   while this lane was finishing. It shares no files with this branch, and
   `git merge-tree` of the two heads is clean. It adds one user-facing label that breaks §3:
   `<dt>Risk evaluation</dt>` in `frontend/components/Overview.tsx`. It should read
   "Findings evaluation" (or similar) when the two lanes are integrated.
7. **Two backend and docs names still say "risk"**, by design: `docs/architecture/risk-model.md`
   and the handoffs. These are internal and technical documents, so they are out of scope.

## 10. Not done, by design

No merge to `main`. No push to the deploy branch. No deploy. No hosted database, Cloudflare,
Render or Supabase action.
