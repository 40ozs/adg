# ADG-UX-BACKEND-COPY-04 — Identity wording in backend prose, readable identifiers

**Verdict: `READY_FOR_ADG_UX_BACKEND_COPY_INTEGRATION_WITH_LIMITATIONS`**

| | |
| --- | --- |
| Baseline | `origin/integration/ADG-UX-WAVE-B-01` @ `10c73d1fcb5b12b31fa54e57b95da3483c079fba` (fetched 2026-10-30; still the branch head; no newer validated UX baseline in the repository's reports or branches) |
| Branch | `feature/ADG-UX-BACKEND-COPY-04-identity-wording` (worktree `/Volumes/T5 EVO/mmm/worktrees/adg-UX-BACKEND-COPY-04`) |
| Code commit | `e8fd2dd` |
| Merged | **No.** Not merged to the UX baseline or to `main` |
| Deployed | **No.** No hosted service, Render, Worker or Supabase was touched |
| Evidence | [`ADG-UX-BACKEND-COPY-04-evidence/`](ADG-UX-BACKEND-COPY-04-evidence/) |

This lane closes the items that ENTITY-02 §2.5 deferred. These are the backend engine,
impact and simulation prose that still said "principal", the raw `principal:<sid>` node ids in
the explain removal table, and the raw review scope tokens. Only sentences and their
presentation changed. No query parameter, JSON field, graph node id, scope token, route, rule
id, rule version, OpenAPI document or stored value changed.

## 1. Summary

| Category | Count |
| --- | --- |
| Backend user-facing strings changed (engine, simulation, changes, governance, risk, error details) | 39 changed string-literal lines in 14 files (§2.1 lists each sentence) |
| Frontend presentation changes (removal labels + export, scope labels, campaign heading, Changes scope line) | 5 files |
| Contract examples updated (captured `verdict.reason` only) | 3 lines in 3 files |
| Test updates (pinned old wording) | 2 backend, 3 frontend factories/assertions |
| New tests | backend `tests/contracts/test_identity_wording.py` (10), frontend 5 |

Product files: `backend/app/{access_engine/conditions,access_engine/verdict,api/graph,changes/impact,changes/rules,changes/service,domain/graph,governance/drift,governance/service,risk_engine/severity,services/access,simulation/describe,simulation/model,simulation/overlay}.py`;
`frontend/{components/Explanation,components/Governance}.tsx`, `frontend/lib/{explanation,governance}.ts`,
`frontend/app/changes/page.tsx`, `frontend/app/governance/campaigns/[campaignId]/page.tsx`.

## 2. Classification

The audit was an AST scan of every non-docstring string constant in `backend/app` that contains
the word "principal" ([`principal-scan.py`](ADG-UX-BACKEND-COPY-04-evidence/principal-scan.py)).
It found 147 before ([`principal-inventory.before.txt`](ADG-UX-BACKEND-COPY-04-evidence/principal-inventory.before.txt))
and 111 after ([`principal-inventory.after.txt`](ADG-UX-BACKEND-COPY-04-evidence/principal-inventory.after.txt)).
Each one was then traced to where it surfaces. Error `detail`s count as user-facing, because
`frontend/lib/api/client.ts` and `lib/state.ts:90` render them as the page's explanation.

The rule: change generic prose in runtime text that a user can reach, whether response content
or an error on an ordinary navigation path. Keep text that defines identifier or key syntax,
names a contract term, is OpenAPI or JSON-schema documentation, or is a Windows term of art.

### 2.1 Changed: generic user-facing prose → identity

| Surface | File | Before → after |
| --- | --- | --- |
| Explain / investigate verdict (`verdict.reason`) | `access_engine/verdict.py` | "This principal holds rights on this object." → "**This identity** holds rights on this object."; the five "This principal holds no rights here…" no-grant/denied reasons → "This identity…" |
| Explain conditions | `access_engine/conditions.py` | "…so this principal's access depends on the order of the entries." → "this identity's"; "No run has described this principal, so…" → "this identity" |
| What-if change vocabulary (`kind_description`, `/simulations/vocabulary`) | `simulation/describe.py` | "Put a principal into a group." → "**Add an identity to a group.**"; "Take a principal out of a group." → "**Remove an identity from a group.**" (same verbs as the entry kinds: "Add an entry…", "Remove an entry…") |
| What-if caveats | `simulation/model.py` | "What this principal holds today / would hold…" → "this identity"; "…and the principal still has another." → "the identity" |
| What-if truncation notes | `simulation/model.py` | "More principals are affected…" → "More identities…"; "The principal/resource budget…" → "The identity/resource budget…"; "…affected principals are missing." → "affected identities" |
| What-if validation errors | `simulation/model.py`, `simulation/overlay.py` | "…is about one principal and must name it." → "one identity"; "…the set of principals and resources…" → "identities" |
| Change explanations (rule sentences) | `changes/rules.py` | "A principal that had been deleted…" / "The principal was deleted…" / "…this principal's scope…" / "A principal appeared." / "…names a different principal than it did." → identity |
| Change impact explanations | `changes/impact.py` | "…about neither a principal nor a resource…", "…always about a principal as well.", "…the principal's own state still governs." → identity |
| Changes window error | `changes/service.py` | "…name a server, share, directory, principal or group…" → "identity" (the UI's scope words; the parameter is still `principal=`) |
| Finding qualifier | `risk_engine/severity.py` | "No run has described a principal this finding names…" → "an identity" |
| Review drift summary | `governance/drift.py` | "…no entry names this principal." → "this identity" |
| Bulk-decision refusal (422 detail) | `governance/service.py` | "…cover N principals across…", "one principal over many targets, or one target over many principals" → identities (matches the frontend's own wording from ENTITY-02) |
| Shares-locally error | `services/access.py` | "Asking which shares a principal can reach locally…" → "an identity" |
| Identity-page errors (409 / 404 / 422) | `api/graph.py`, `domain/graph.py` | "{sid} names N principals. A BUILTIN SID…" → "N identities"; "…no principal observation and no membership edge names it." → "…no run has described it and no membership edge names it." (same fact, in the app's usual phrasing); "…is both the principal and the group. A principal is not a member of itself; name two distinct principals." → "the member and the group. An identity is not a member of itself; name two distinct identities." |

No more specific noun (User, Group) was available without new data. These sentences come from
lookup tables keyed by outcome or condition, not by subject kind, so "identity" is the honest
generic word. Semantics are unchanged in every row.

**Persistence.** Each of these sentences is attached at response time from an enum: verdict
reasons, qualifiers, change rules (classified on read), simulation vocabulary, caveats and
truncations. The one exception is `simulation_evaluations.report` (JSONB), which keeps the
explanations of an evaluation as it was computed. A simulation evaluated before this change
keeps the old verdict wording in its stored report, and that is correct for a record. The
local demo dataset has zero stored evaluations.

### 2.2 Preserved: technically precise terms

- "Windows grants every principal full access to an object with a NULL DACL…": this paraphrases
  Windows' documented behaviour (kept by ENTITY-02 §2.3 too).
- "the well-known SYSTEM principal" (SYSTEM confirmed/disputed conditions): a Windows term.
- "foreign security principal" and "User principal name" are untouched (frontend, ENTITY-02).
- "a Windows principal (owner_principal_key)" in the ownership-record error names the field.

### 2.3 Preserved: internal identifiers and serialized contracts

- Graph node ids `principal:<sid>` and edge ids `membership:principal:…->group:…`, `NodeKind.PRINCIPAL`.
- The `principal=` query parameter on `/access/explain`, `/risks`, `/changes`, campaign items.
- Scope tokens: `ScopeView {kind: "principal", key}`, `ReviewScopeKind.PRINCIPAL`, `CampaignFocus.PRINCIPAL`.
- Codes: `add_member`, `remove_member`, `principal_budget`, rule ids such as
  `disabled_principal_retains_access`, change rule ids `principal.deleted` etc.
- `redundant_access_paths` is still version `1.0`, with title "One identity reaches a resource by
  several independent routes" (API-checked in the browser gate; `tests/risk_engine/test_model.py`).

### 2.4 Preserved: API and schema documentation, identifier syntax, developer output

- **OpenAPI and JSON-schema descriptions** (`Field(description=…)`, `Query(description=…)`,
  route `summary`/`responses`, model docstrings such as "Put a principal into a group, or take
  one out.", and `contracts/derived.py` titles). These are API reference for integrators and
  document the `principal` contract. Changing them would also regenerate
  `docs/contracts/v1/openapi.json`. The file is byte-identical, and the snapshot test passes.
- **Identifier-syntax errors**: "… is not a principal identifier. Give a SID…", "… is not a
  host-scoped principal key…", "… does not name a principal. A principal scope key is a SID…",
  and the share-key hint that names the `/principals/{principal}/shares` route. They describe
  the key format a caller must send.
- **Collector and ingestion validation** (`validation/collector_output.py`,
  `contracts/v1/observations.py`, `domain/identity.py`): "principal" is the observation kind
  (`kind: "principal"`), and the reader is a collector operator.
- **Programming-error invariants** (`alerts/model.py`, `risk_engine/findings.py`), **DB column
  comments** (`models/schema.py`), **CLI output** (`python -m app.demo`: estate counts and
  `estate.py` feature notes), **API paths** quoted in demo scenarios, and the PowerShell
  `System.Security.Principal` type names in remediation export.

### 2.5 Raw identifiers translated for presentation (frontend)

| Where | Before | After | Data used |
| --- | --- | --- | --- |
| `/access/explain` "What a removal would do", Relationship column | `<code>principal:S-1-…</code> → <code>group:S-1-…</code>` | "Brianna Reddy is a member of Sales-Ops", "NTFS entry #1 names Brianna Reddy", with the kind underneath as before | `removalLabel()` reuses `edgeLabel()`/`nodeLabel()`, the same sentence the diagram's structure table shows, from `explanation.graph` (already in the response) |
| Same table, unresolved identity | — | The SID (`nodeLabel` falls back to `display_name ?? sid ?? key`). Nothing invented | |
| Same table, edge missing from the graph | — | `principal:… → group:…` (the old ids, as plain text) | |
| Explain text export | `  [<edge_id>]` | `  Brianna Reddy is a member of Sales-Ops [<edge_id>]`: name first, canonical id kept for audit | |
| Removal row DOM | — | `data-removal-edge="<edge_id>"` keeps the canonical id on the row (not `data-edge`, which the diagram/table parity test counts) | |
| `/governance` campaign rows | `share:gp-fin-fs01\|finance, principal:S-1-…` | `Share: gp-fin-fs01\|finance`, `Directory tree: \\GP-…`, `Identity: S-1-… (name not loaded)` | `scopeLabel()`. Campaign rows carry no names, so the fallback says so instead of passing a SID off as a name |
| `/governance/campaigns/{id}` Reviewers, Scope column | `principal:S-1-…` | `Identity: sec-share-design-read-only`, `Identity: Brianna Reddy` | `itemNames()` over the items the page already fetches (`principal_display_name` at the baseline) |
| Campaign items heading with `?principal=` | "Items for S-1-…" | "Items for sec-share-design-read-only" | the same map |
| `/changes?principal=…` scope line | "Scope: principal: S-1-…" | "Scope: identity: S-1-…" | query parameter unchanged |

**No N+1.** Every name comes from data already on the page: the explain response's own graph,
or the campaign page's single items request. Nothing is fetched per row or per token. Sorting
uses the displayed label, as it did before (the label was the token then). Row height is
unchanged: one line plus the existing kind note, no SID stacked under a name.

### 2.6 Remaining and deferred

1. **Reviews list names.** `CampaignView.scopes` is `{kind, key}` only, so `/governance` cannot
   name an identity scope without a lookup per row. It shows the explicit "(name not loaded)"
   fallback. Naming it needs an additive `display_name` on `ScopeView`. That is an API change,
   deferred to a separate lane (spec §9).
2. **Item list on large campaigns.** The campaign page names reviewer scopes from its first 100
   items. A scope whose identity is not among them falls back to "(name not loaded)".
3. **Stored simulation reports** keep the verdict wording that was current when they were
   evaluated (§2.1 Persistence).
4. **OpenAPI/schema descriptions** still say "principal" (§2.4). They are developer-facing.
   Rewording them would mean regenerating the published OpenAPI document, and that belongs in a
   docs lane.
5. **Historical evidence** (ENTITY-02, Wave A/B) quoting old strings is left as recorded.

## 3. Contract safety

[`contract-safety.txt`](ADG-UX-BACKEND-COPY-04-evidence/contract-safety.txt), diffing `10c73d1`
against `e8fd2dd`:

- `docs/contracts`: only the three captured `"reason"` lines in the derived examples changed.
  They now match what the server sends. `openapi.json` and the derived schemas are
  byte-identical. `test_openapi_snapshot.py` and `test_derived_schemas.py` pass.
- `backend/app`: every changed line is a string literal or an enum-keyed prose entry. No
  `Field`, `Query`, alias, route, enum value, rule id or version line changed.
- New `test_identity_wording.py` pins both halves. The prose tables (verdict, no-grant,
  conditions, change kinds, caveats, truncations) carry no generic "principal", and the terms
  of art are still present. `NodeKind.PRINCIPAL`, `ReviewScopeKind.PRINCIPAL`,
  `CampaignFocus.PRINCIPAL`, `principal_budget`, `add_member`/`remove_member` and the
  explain `principal` parameter are unchanged.
- Live API (browser gate checks 1–3): explain still returns `principal:<sid>` node ids and
  removal sources, `/risks/rules` still has `redundant_access_paths` v`1.0`, and the vocabulary
  codes are unchanged.

## 4. Tests

| Gate | Result |
| --- | --- |
| `ruff check app tests` / `ruff format --check` | pass / 415 files formatted |
| Backend `pytest tests --ignore=tests/db --ignore=tests/benchmarks` | **6130 passed**, 41 skipped (DB-gated) |
| Backend contract + wording focus (`tests/contracts`, risk model, simulation, verdict, decision rules) | 1076 passed, 1 skipped |
| `vitest run` | 46 files, **1599/1599** |
| `eslint .` | pass |
| `tsc --noEmit` | pass |
| `cf:typecheck` | pass |
| `next build --webpack` (standalone) | pass |

New frontend tests: removal relationships named as the diagram names them, with no
`principal:S-1-` label and the id kept on the row (`explanation-views`). An unresolved identity
shows its SID, and an edge missing from the graph falls back to ids. The export carries
`<label> [m1]`. Reviewer identity scopes are named from page names. The "(name not loaded)"
fallback appears without them. Every scope kind is labelled in a campaign row
(`governance-views`). Updated: two backend assertions on old wording
(`test_decision_rules.py`, `simulation/test_model.py`) and the frontend factories that mirror
backend strings.

DB suites were not run. No database logic or schema changed. Every changed sentence is read
at request time, apart from the stored simulation report covered in §2.1.

Logs: [`backend-gates.txt`](ADG-UX-BACKEND-COPY-04-evidence/backend-gates.txt),
[`frontend-gates.txt`](ADG-UX-BACKEND-COPY-04-evidence/frontend-gates.txt).

## 5. Browser validation (real local stack)

| Part | What ran |
| --- | --- |
| Database | Postgres 16.2 (`pgserver` binaries) on 127.0.0.1:55477, a copy of Wave B's shut-down `adg_demo` (Genesee Press, fingerprint `9c8177e2…`). Isolated from a sibling lane on :55467/:8030/:3030 |
| Campaigns | The demo dataset has none. [`campaign-setup.py`](ADG-UX-BACKEND-COPY-04-evidence/campaign-setup.py) created two through the public API as `governance`: an identity campaign (scopes: group `sec-share-design-read-only`, person Brianna Reddy; reviewers scoped to each) and a resource campaign (share + directory tree). See [`campaigns.json`](ADG-UX-BACKEND-COPY-04-evidence/campaigns.json). The list also shows one older "Overview gate review" campaign from the copied database |
| API | This worktree's backend, `python -m app --port 8040`, demo mode, development auth with a throwaway secret |
| Web | This lane's standalone build, `server.js` on :3040 with `render/gate.ts`'s web environment |
| Browser | Google Chrome (/Applications) via Playwright 1.62.1, Node 22.23.3 arm64, signed in as `admin` |

Harness: [`copy-check.mjs`](ADG-UX-BACKEND-COPY-04-evidence/copy-check.mjs) → **17/17 passed**
([`copy-check.txt`](ADG-UX-BACKEND-COPY-04-evidence/copy-check.txt), [`.json`](ADG-UX-BACKEND-COPY-04-evidence/copy-check.json)):

- API contracts: explain ids and parameter, rule id/version/title, vocabulary codes.
- Findings explanation (`/access/explain`, scenario F): the verdict reads "This identity holds
  rights on this object.". 19 removal rows are named (first: "Brianna Reddy is a member of
  Sales-Ops"). No `principal:S-1-` text. Every row keeps its id. The export carries label +
  `[edge_id]`. No generic "principal" in text or accessible names.
- Access investigation, identity detail, group detail, findings (`redundant_access_paths`) and
  Changes scoped to an identity: no generic "principal". Links < 400.
- What-if (`/simulations/new`, vocabulary opened): "Add/Remove an identity to/from a group.".
- Reviews list: kind labels, explicit fallback, no raw token. Campaign page: reviewer scopes
  named "Identity: sec-share-design-read-only" and "Identity: Brianna Reddy". The filtered
  heading names the group.
- 375 px and 320 px: explain, What-if, reviews and campaign pages do not scroll sideways.
- No page errors.

First run: 16/17 ([`copy-check.first-run.txt`](ADG-UX-BACKEND-COPY-04-evidence/copy-check.first-run.txt)).
The export check scanned the bracketed canonical edge ids (`[membership:principal:S-…]`), which
are meant to be there, for the generic word. That was fixed in the harness, not the product.
Screens: [`screens/`](ADG-UX-BACKEND-COPY-04-evidence/screens/). The element shots
`explain-removal-table-{1440,375}.png` and `campaign-reviewers-table-{1440,375}.png` come from
[`element-shots.mjs`](ADG-UX-BACKEND-COPY-04-evidence/element-shots.mjs).

## 6. Limitations

1. Reviews list identity scopes are not named (§2.6.1). This needs an additive API field.
2. Campaign-page names cover the first 100 items only (§2.6.2).
3. Stored simulation reports keep their original wording (§2.1).
4. OpenAPI/schema descriptions and identifier-syntax errors still say "principal" on purpose (§2.4).
5. No manual screen-reader pass. Accessible names were checked by DOM scan and Testing Library.
6. DB-backed backend suites were not run (no DB logic change). CI on the pushed branch has not
   been checked by this lane.
7. On the reviews list at desktop width, a long identity scope (SID + fallback) still runs past
   the column inside the table's own scroll container. The old `principal:S-1-…` token did the
   same. No page-level overflow.

## 7. Intentional remaining uses of "principal" (user-visible)

| Where | Text | Why |
| --- | --- | --- |
| Explain conditions | "Windows grants every principal full access…" (null DACL), "the well-known SYSTEM principal" | Windows terms (§2.2) |
| Identity page | "foreign security principal", "User principal name" | Windows / AD terms (ENTITY-02) |
| Error on a malformed key | "… is not a principal identifier / host-scoped principal key / principal scope key" | Key syntax (§2.4) |
| What-if vocabulary codes | `add_member`, `remove_member`, `principal_budget` in `<code>` | Serialized codes |
| Explain text export | `[membership:principal:S-…->group:S-…]` after each label | Canonical edge id, kept for audit |
