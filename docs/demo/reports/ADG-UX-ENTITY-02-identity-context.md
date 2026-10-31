# ADG-UX-ENTITY-02 — Identity terminology and entity context

**Verdict: `READY_FOR_ADG_UX_ENTITY_INTEGRATION_WITH_LIMITATIONS`**

| | |
| --- | --- |
| Baseline | `origin/integration/ADG-UX-WAVE-A-01` @ `fcb792872363c131d2539bab1f629c5d5120445e` (fetched 2026-10-30; no newer Wave B baseline existed) |
| Branch | `feature/ADG-UX-ENTITY-02-identity-context` (worktree `/Volumes/T5 EVO/mmm/worktrees/adg-UX-ENTITY-02`) |
| Merged | **No.** Not merged to the Wave B baseline, not merged to `main` |
| Deployed | **No.** No hosted service, Render, Worker or Supabase touched |
| Evidence | [`ADG-UX-ENTITY-02-evidence/`](ADG-UX-ENTITY-02-evidence/) |

This lane finishes the Principal → Identity cleanup that UX-01 §3 deferred ("identity detail
tabs and deeper technical copy"). It also corrects the one backend-supplied rule title. It renames
copy only: no route, query parameter, API field, type, rule id, rule version or capability changed.

## 1. Summary of changes

| Category | Count |
| --- | --- |
| Frontend user-facing strings changed (text, headers, labels, aria-labels, titles) | 69 |
| Backend presentation strings changed (risk catalog prose, incl. the rule title) | 7 |
| Structural edits supporting copy (`MembershipTable` takes a `columnLabel`, like `ChainTable`) | 4 |
| Helper removed (`describeCount` in `lib/simulation.ts`: it appended "s", giving "identitys") | 1 |
| Tests updated for changed product text | 11 assertions in 9 files |
| New tests | 3 frontend (+ 1 parametrized backend × 11 rules, + 1 title test) |

Product files changed (20): `backend/app/risk_engine/catalog.py`;
`frontend/app/identities/principal/[key]/[[...view]]/page.tsx`, `frontend/app/changes/impact/page.tsx`;
`frontend/components/{DecisionForm,EffectiveAccess,ExplanationExplorer,Governance,Investigation,Simulation,SimulationDetail,SimulationEditor,Watches}.tsx`;
`frontend/lib/{acl,changes,explanation,governance,identity,operations,risks,simulation}.ts`.

## 2. Terminology classification

The audit covered every `principal`/`principals` in `frontend/app`, `frontend/components` and
`frontend/lib` (≈370 raw hits, 280 outside comments), plus backend strings that reach the UI.

### 2.1 Changed — generic user-facing → Identity (69 frontend)

| Surface | What changed |
| --- | --- |
| Identity detail tabs and panels (13) | Panel headings: "Groups this identity is directly inside", "Every group this identity ends up inside", "Shares/Directories this identity can reach", "Share ACLs that name this identity". Panel intros and empty-state subjects. Tab hints in `principalViews`. The membership table's "Principal" column is now **Group** (on a groups view) or **Member** (on a members view), matching the chain tables, which already said Group/Member. Tab *labels* already said Direct members / Parent groups / Effective groups etc. and are unchanged |
| Access and explanation (17) | Effective-access table column "Identity", "the ACL names this identity directly". Investigation `<dt>Identity</dt>` and its two aria-labels ("How this route reaches the identity", "Hops on route …, identity first"). Explanation guidance, the graph's first column "Identity", node-kind label "identity", the clipboard export line `Identity: …`. The ACL-results note. The impact-page note |
| Changes (6) | Impact wording "Needs an identity", "Name an identity …", "about an identity …" |
| Reviews / governance (11) | "By identity" campaign focus, decision meanings, bulk-selection refusal ("one identity across many targets"), items heading "2 identities on …", "About this identity on this target / elsewhere", decision-form caption and link title |
| What-if / simulation (18) | "Identities affected", the delta tables' "Identity" column, form label and options ("One identity against one directory"), limits, headlines ("2 identities would lose access…"), the text export's "Per identity" |
| Findings / collectors / alerts (4) | Finding relation label "Identity", the risk-coverage note, collectors' "Identities" row, the watch-key label ("a group's storage key", the term the identity page already uses) |

Specific nouns were kept where they are clearer than "identity": Group/Member columns,
"Parent groups", "a disabled account", "person or group".

### 2.2 Changed — backend presentation (7, `backend/app/risk_engine/catalog.py`)

| Rule id (unchanged) | Field | Before → after |
| --- | --- | --- |
| `redundant_access_paths` | `title` | "One principal reaches a resource by several independent routes" → "**One identity reaches a resource by several independent routes**" |
| `redundant_access_paths` | `detects`, 2 remediation strings | "A principal that belongs to…" → "An identity that belongs to…"; "…reach this identity…"; "…what the identity can do" |
| `disabled_principal_retains_access` | `detects` | "A principal ADG has described as disabled…" → "An identity…" |
| `unresolved_sid_on_acl` | `detects`, caution | "no principal record" → "no identity record"; "a live identity … removes that identity's access" |

Why this is safe:
- **Semantics.** The rule's `subject_kind` is `SubjectKind.ACCESS`: one (principal, resource) pair,
  where the principal is any account, group or computer that reaches the resource through several
  trustees. `SubjectKind.PRINCIPAL`'s own docstring already says such a finding "names an
  identity". "Identity" is the UX-01 word for exactly that set, so the title stays accurate. "User"
  would be too narrow, because groups and computers match too.
- **Title is presentation.** The module docstring says so: `RuleDefinition.version` changes when the
  predicate changes, and "editing this file's prose does not change it". The version stays `1.0`.
  Only `app/api/risks.py` reads `title/detects/matters_because/remediation`, at request time. No DB
  column stores a rule title. The alert payload snapshots a title only for critical findings, and
  this rule is LOW.
- **No title-keyed dependency.** Filtering and navigation use the id (`/risks?rule=redundant_access_paths`).
  No test, fixture, harness or snapshot asserted the old title. Only the Wave A evidence JSON quotes
  it, and that evidence is historical and left untouched.
- **Evaluation unchanged.** A fresh demo reset reproduced fingerprint `9c8177e2…` and 9,666
  matched / 9,386 open findings, identical to the Wave A reset. `tests/risk_engine` (rules,
  reproduction, model) passes.

### 2.3 Preserved — technical terms of art (UX-01 §3 rule 1)

- **foreign security principal**: membership notes on the identity page ("through a foreign
  security principal…", "via a foreign security principal"), and the kind chip when the backend
  `kind` is `foreign_security_principal`.
- **User principal name**: the identity facts and the user directory column (an AD attribute name).
- **"Windows grants every principal full access…"** (null-DACL condition, backend): this paraphrases
  Windows' own behaviour. It is also backend engine prose, see 2.5.

### 2.4 Preserved — internal identifiers (not user copy)

Routes `/identities/principal/{key}/{view}` and the legacy `?key=`; the query parameter `principal=`
on `/access/explain`, `/access/investigate`, `/risks`, campaigns; `risks:read` and every capability;
TypeScript types (`Principal`, `PrincipalSummary`, `PrincipalDetail`, `AnyPrincipal`), components
(`PrincipalName`, `PrincipalChain`, `PrincipalViewTabs`, `PrincipalDirectory`), functions
(`principalLabel`, `principalHref…`, `principalViews`, `PRINCIPAL_VIEW_IDS`), CSS classes
(`.principal`, `.principal-name`, `.principal-chain`, `.principal-qualifier`); enum values and API
fields (`CampaignFocus "principal"`, `FindingRelation "principal"`, node kind `"principal"`,
`<option value="principal">`, `principal_key`, `principals_affected`, `principals_losing/gaining`,
`max_principals`, `path_principals`, `viewer.principal`); rule id `disabled_principal_retains_access`,
`SubjectKind.PRINCIPAL`, `FactKind.PRINCIPAL`; engine graph node ids `principal:<sid>` (also in
`data-node`/`data-edge` attributes); code comments and docstrings.

### 2.5 Ambiguous / deferred (documented so later lanes do not rediscover them)

1. **Backend engine and service prose still says "principal".** Examples are access-engine reasons
   and conditions ("This principal holds rights on this object.", "No entry on either ACL names
   this principal…", "No run has described this principal…", "…this principal's access depends on
   the order of the entries"). Others are `changes/impact.py` explanations, the simulation
   vocabulary ("Take a principal out of a group."), and API error and OpenAPI descriptions (e.g.
   "A bare SID matched several host-scoped principals"). These travel inside the explain and
   simulation contracts, partly inside stored simulation documents, and are mirrored by frontend
   test factories. This lane's backend scope was the known rule title. **Recommend one
   backend-copy lane** for the engine/impact/simulation prose, with explain-contract tests. The
   browser gate reports the one such string it saw ("This principal holds rights on this object.",
   on `/access/explain` and `/access/investigate`) without failing on it.
2. **Removal-targets table prints raw node ids.** On `/access/explain` the "Relationship" cell shows
   `principal:S-1-… → group:S-1-…` in `<code>`. These are engine ids, so they were preserved.
   Showing names (`nodeLabel`) would be a context improvement for a later lane.
3. **Campaign scope tokens.** The reviews list shows `${scope.kind}:${scope.key}`, so an
   identity-focused campaign reads `principal:S-1-…` beside `directory_tree:…`. Every scope kind
   renders raw. A label map is a reviews-UX change, not a terminology one.

## 3. Entity detail context

Reviewed: the identity layout (user, group, computer, unresolved SID) and the server, share and
directory pages. They already share one pattern: `<h1>` = human-readable name +
`.title-kind` chip (user / domain group / server / share / directory). Secondary identifiers sit in
the collapsible "Identity information" (SID, storage key, account name, UPN, DN) or the page
intro. Each relationship view has its own tab (`aria-current="page"`) and a section heading that
names the relationship ("Groups this identity is directly inside", "Everything that ends up inside
this group"). Direct and effective answers are separate tabs whose intros state the difference.

No layout change was needed beyond the copy and the Group/Member column. Wave A's compact identity
display is untouched: no SID stacked under names, the SID shown only when unresolved, unchanged
`PrincipalName`/`Identity.tsx`.

## 4. Accessibility

Visible and accessible names were changed together: column headers (the accessible names of
sortable headers), `<dt>`, the form `<label>` (`htmlFor="simulation-subject"` unchanged), the two
`aria-label`s in `Investigation.tsx`, and the decision-form link `title`. The new page tests and
the browser gate scan `aria-label`, `title`, `th`, `label`, `summary`, `option` and `caption` for
generic "principal". None remain on the pages visited.

## 5. Tests

- Updated assertions (product text changed): `changes.test.ts`, `collectors-page.test.tsx`,
  `explanation-views.test.tsx`, `governance-views.test.tsx`, `governance.test.ts`,
  `simulation.test.ts`, `simulation-views.test.tsx`, `system-mapping.test.tsx` (3), `views.test.tsx`.
- New: `identity.test.ts` (no view label or hint says "principal"); `pages.test.tsx` "identity
  terminology on an identity's page" (Group column and heading on a user's groups view; Member
  column on a group's members view; "foreign security principal" kept exact; no generic principal
  in rendered text or `aria-label`/`title`); `backend/tests/risk_engine/test_model.py` (every
  rule's prose free of generic "principal", ×11; the redundant-routes title, with version `1.0`).

| Gate | Result |
| --- | --- |
| `vitest run` | 45 files, **1547/1547** (baseline 1544 + 3 new tests) |
| `eslint .` | pass |
| `tsc --noEmit` | pass |
| `cf:typecheck` | pass |
| `next build --webpack` (standalone) | pass |
| Backend `pytest` risk_engine, alerts, api, contracts, demo, demo_mode | **1493 passed**, 28 skipped (DB-gated) |
| `ruff check` / `ruff format --check` | pass |

The DB suites were not rerun. The backend change is prose read at request time, and the demo
reset proves the evaluation is identical. Logs: [`frontend-gates.txt`](ADG-UX-ENTITY-02-evidence/frontend-gates.txt),
[`backend-gates.txt`](ADG-UX-ENTITY-02-evidence/backend-gates.txt).

## 6. Browser validation (real local stack)

| Part | What ran |
| --- | --- |
| Database | Throwaway Postgres 16.2 (`pgserver` wheel binaries) on 127.0.0.1:55447, fresh UTF8 `adg_demo`. Isolated from a sibling lane's stack on :55437/:8000/:3000 |
| Dataset | `python -m app.demo_mode reset`: 164 s, 300 runs (297 ok, 2 failed, 1 partial), 41,756 observations, probes 23/23, 9,386 open findings, fingerprint `9c8177e2…` ([`demo-reset.txt`](ADG-UX-ENTITY-02-evidence/demo-reset.txt)) |
| API | `python -m app` on :8010, this worktree's backend, demo mode, development auth with a throwaway secret |
| Web | This lane's standalone build, `server.js` on :3010 with `render/gate.ts`'s web environment |
| Browser | Google Chrome (/Applications) via Playwright 1.62.1, Node 22.23.3 arm64, signed in as `admin` |

Harness: [`entity-check.mjs`](ADG-UX-ENTITY-02-evidence/entity-check.mjs) → **14/14 passed**
([`entity-check.txt`](ADG-UX-ENTITY-02-evidence/entity-check.txt), [`.json`](ADG-UX-ENTITY-02-evidence/entity-check.json), [`screens/`](ADG-UX-ENTITY-02-evidence/screens/)):

- User (scenario F, Brianna Reddy): direct groups, effective groups, reachable shares, reachable
  directories, named on share ACLs. Group (sec-share-design-read-only): direct members, effective
  members, parent groups. Each has `h1` name + kind, the expected section heading, one current tab,
  a Group/Member column where a table exists, no generic "principal" in text or accessible names,
  and internal links all < 400 (up to 30 per page).
- Server GP-ACQ-FS01, share `\\gp-acq-fs01\Acquisitions`, directory
  `\\GP-FIN-FS01\Finance\Reports\Revenue`: kind chip present, no generic principal, links work.
- Findings `/risks?rule=redundant_access_paths`: renders the new title, not the old one. The id-based
  filter still selects the rule.
- Access path: `/access/investigate` and `/access/explain` for the scenario person: no generic
  principal outside the deferred engine prose (§2.5.1) and engine node ids (§2.5.2).
- 375 px and 320 px: identity groups, group members and findings pages do not scroll sideways.
- No page errors.

Harness mistakes on the first run (11/14), fixed in the script, not the product: the Group column
check was not scoped to the open panel (a group with no parent groups has no table, only the
aliases "Kind" header); shares are linked from a server's page, not `/resources`; engine node ids
`principal:<sid>` needed classifying as identifiers.

## 7. Limitations

1. Backend engine, impact and simulation prose still says "principal" (§2.5.1). It is visible on
   the explain and investigate pages and in the What-if change vocabulary. Deferred to a
   backend-copy lane.
2. Raw node ids in the removal-targets table and raw campaign scope tokens (§2.5.2–3).
3. No manual screen-reader pass. Accessible names were checked by DOM scan and Testing Library roles.
4. The DB-backed backend suites were not rerun (prose-only backend change, see §5).
5. Historical evidence that quotes the old title (`ADG-UX-WAVE-A-INTEGRATION-01-evidence/*`) is
   left as recorded.

## 8. Intentional remaining uses of "principal" (user-visible)

| Where | Text | Why |
| --- | --- | --- |
| Identity page membership notes | "through/via a foreign security principal" | Windows term of art |
| Identity kind chip | "foreign security principal" (from backend `kind`) | Windows term of art |
| Identity facts, user directory | "User principal name" | AD attribute name |
| Explain / investigate | "This principal holds rights on this object." and other engine reasons | Backend engine prose, deferred (§2.5.1) |
| Explain removal table | `principal:S-1-…` | Engine node id (§2.5.2) |
| Reviews list | `principal:S-1-…` scope token | Serialized scope (§2.5.3) |
| What-if change vocabulary | "Take a principal out of a group." | Backend simulation prose, deferred (§2.5.1) |
