# ADG-UX-REVIEW-NAMES-08 — Review scope display names

**Verdict: `READY_FOR_ADG_UX_REVIEW_NAMES_INTEGRATION_WITH_LIMITATIONS`** (limitations in §12).

The Reviews list no longer shows `Identity: S-1-… (name not loaded)` when ADG knows the
identity's name. The governance `ScopeView` gains one optional field, `display_name`. Every
route that returns a `ScopeView` fills it in with one batched lookup. The frontend's
`scopeLabel()` uses that name first, then the names the page already holds, then the existing
explicit fallback. The scope token, authorization and request counts are unchanged.

Not merged. Not deployed.

## 1. Record

```
BASE_SHA=559dbaf913a02e0df4ddf18be89ef81f03e48596   (origin/integration/ADG-UX-WAVE-C-01; CI 41167906074 success)
BRANCH=feature/ADG-UX-REVIEW-NAMES-08-scope-display-name
WORKTREE=/Volumes/T5 EVO/mmm/worktrees/adg-UX-REVIEW-NAMES-08
CODE_SHA=9411659   (feat(adg-ux): include review scope display names)
REPORT_SHA=the commit that adds this report
```

**Baseline.** On 2026-10-31 `git fetch` found `559dbaf` at the head of
`origin/integration/ADG-UX-WAVE-C-01`. CI run 41167906074 on that exact SHA is a success. The
Wave C report (§15.1) and the COPY-04 report (§2.6.1) name this lane as deferred work. No newer
integration branch exists. The only newer remote branch is `feature/ADG-UX-NAV-07-…`, a
feature lane off `559dbaf`, not an integration.

## 2. What changed

| File | Change |
|---|---|
| `backend/app/api/governance.py` | `ScopeView.display_name: str \| None = None`. New `_scope_names()` does one `principals_by_keys` lookup over the identity scopes in a response. `_scope_view`, `_campaign_view`, `_assignment_view`, `_progress_view` and `_status_view` take the name map. All 9 routes that emit a `ScopeView` pass one: create, list, get, generate, activate, close, assign, list assignments, status |
| `backend/app/api/scan_runs.py` | Its own `ScopeView` class is renamed `ScanScopeView`, with the same shape (§4) |
| `docs/contracts/v1/openapi.json` | Regenerated (`python -m app.contracts.openapi`) |
| `frontend/lib/contracts.ts` | `ScopeView.display_name?: string \| null` |
| `frontend/lib/governance.ts` | `scopeLabel()`: `scope.display_name ?? names?.get(scope.key)`, then the fallback |
| `frontend/app/governance/campaigns/[campaignId]/page.tsx` | Comment only |
| Tests | §8 and §9 |

The name is `display_name`, else `sam_account_name`, else `last_known_name`. That is the
same order `principal_names_at` uses for item names. The UPN is never used, matching
`identityLabel()`.

## 3. Payload, old and new

`GET /api/v1/governance/campaigns`, local synthetic Genesee Press data. The same database was
read by the `559dbaf` API and by this branch's API
([`payload-old-new.json`](ADG-UX-REVIEW-NAMES-08-evidence/payload-old-new.json)):

```jsonc
// old (559dbaf)
{"kind": "principal", "key": "S-1-5-21-3949361974-1583827165-633756269-2471"}
// new
{"kind": "principal", "key": "S-1-5-21-3949361974-1583827165-633756269-2471",
 "display_name": "sec-share-design-read-only"}
// new, an identity ADG does not describe (local fixture, §6)
{"kind": "principal", "key": "S-1-5-21-3949361974-1583827165-633756269-999999",
 "display_name": null}
// new, other kinds
{"kind": "share", "key": "gp-fin-fs01|finance", "display_name": null}
```

Field by field over every campaign in that response, all other fields are identical. Scope
`kind` and `key` are identical and in the same order. The only difference is the added
`display_name`.

## 4. Schema and compatibility

[`openapi.diff.txt`](ADG-UX-REVIEW-NAMES-08-evidence/openapi.diff.txt):

- **`ScopeView`** gains `display_name` (`string | null`). It is not required:
  `required` is still `["kind", "key"]`. Nothing else in the component changed.
- **`ScanScopeView`.** Before this change, `scan_runs.ScopeView` and `governance.ScopeView`
  had identical shapes, so FastAPI published them as one shared `ScopeView` component. Once
  they differ, FastAPI would rename both to `app__api__governance__ScopeView` and
  `app__api__scan_runs__ScopeView`. Renaming only the scan-run class keeps the governance
  component's name. Scan runs now point at `ScanScopeView`, which has the same two required
  fields. The scan-run wire JSON is unchanged. Only that one component name in the published
  document moves.
- **Derived schemas** (`docs/contracts/v1/derived/`) are unchanged after regenerating them.
  Governance responses are not derived contracts.

**Versioning (§17).** `docs/contracts/README.md` versions payloads that carry
`schema_version`, and governance responses carry none. Their contract is `openapi.json`. So
this is an additive field in the existing API version, with no version bump. No rule version
changed.

**Old clients.** The browser gate ran the Wave C `559dbaf` web build against this branch's
API. It renders the Reviews list as before, with the fallback and no errors. A payload without
the field still parses (`test_a_payload_without_the_name_still_parses`), and the frontend type
marks the field optional.

## 5. Authorization

The field is served under each route's existing capability (`governance:read` or
`governance:manage`). No route, dependency or capability changed. The name comes from
`current_principals`, which `identities:read` already exposes through the identity pages. In
`app/auth/roles.py`, every role that holds `governance:read` also holds `identities:read`,
because every such role is built on `_VIEWER`. So the name discloses nothing a caller could
not already read. `test_every_governance_reader_may_also_read_identities` fixes that
invariant: a future role that reads governance but not identities fails the test. In the DB
tests, an auditor reads the name and a viewer still gets 403 on the list.

## 6. Missing and stale identities

`current_principals` excludes principals reconciled away. A key with no current row gets
`display_name: null`, and the row and every other scope still render. The frontend shows the
established `Identity: S-1-… (name not loaded)`. A principal that is still present but marked
deleted keeps its `last_known_name`, as everywhere else in ADG.

For the browser check, one local draft campaign was seeded through the API as `governance`.
It has the unknown SID `…-999999` (0 rows in `principals`) next to Brianna Reddy
([`unresolved-fixture.json`](ADG-UX-REVIEW-NAMES-08-evidence/unresolved-fixture.json)).

**Current, not baseline.** Item names are baseline names on purpose
(`principal_names_at`). A scope label is a pointer to the identity, which the identity page
also names by its current state. Resolving baseline names per campaign would need one query
per distinct baseline, which is N+1. A renamed identity therefore shows its current name in
scope labels and its baseline name on its items (§12).

## 7. Query and request counts

**DB (`test_the_list_names_every_row_with_one_lookup`).** `GET /campaigns` issues **4**
statements with 1 campaign and **4** with 7 campaigns (11 identity scopes, one of them
unknown). Exactly **1** of them reads `principals` in both cases. A page with no identity
scope reads `principals` **0** times (`test_a_page_with_no_identity_scope_makes_no_lookup`).
Mutation check: resolving per row instead gives 9 statements against 4, and the test fails
([`n-plus-one-mutation.txt`](ADG-UX-REVIEW-NAMES-08-evidence/n-plus-one-mutation.txt)).
`principals_by_keys` chunks keys at `KEY_CHUNK`, so a page needs at most
⌈keys / KEY_CHUNK⌉ lookups, never one per row. **N+1 = NO.**

**Web tier (browser gate, recording proxies in front of the same API):**

| Page | This branch | Wave C 559dbaf | Rows |
|---|---|---|---|
| `/governance` | 5: `/auth/config`, `/runtime`, `/auth/me`, `campaigns?limit`, `queue` | the same 5 | 4 campaigns |
| `/governance/campaigns/{id}` | 6 | the same 6 | — |

The frontend makes no extra request and no per-row request. The CampaignTable unit test also
asserts that `fetch` is never called.

## 8. Backend tests

New tests:
- `tests/db/test_governance_api.py::TestScopeDisplayNames` (7):
  - named on create, list and get;
  - named on assignment create, assignment list and status (campaign and reviewer scopes);
  - unknown SID gives null and the row survives;
  - share scope unchanged and unnamed;
  - auditor reads the name, viewer gets 403;
  - one lookup for 1 or 7 rows;
  - zero lookups without identity scopes.
- `tests/contracts/test_scope_display_name.py` (3): optional and additive; scan-run shape
  unchanged; old payload parses.
- `tests/auth/test_roles.py` (+1): the role invariant from §5.

| Gate (CI's commands; Python 3.13, `PYTHONPATH` = this worktree) | Result |
|---|---|
| `ruff check .` | pass |
| `ruff format --check .` | pass |
| `mypy app tests` | pass |
| `pytest -q -m "not smoke"` | **6164 passed**, 12 skipped (Wave C: 6160, +4) |
| `pytest -q -m smoke` (DB, `ADG_RUN_SMOKE_TESTS=1`, cluster `timezone=UTC`) | **1305 passed**, 1 xfailed, 0 failed (Wave C: 1298, +7) |
| OpenAPI snapshot (`test_openapi_snapshot.py`) | pass, regenerated |

Logs: [`backend-gates.txt`](ADG-UX-REVIEW-NAMES-08-evidence/backend-gates.txt),
[`frontend-gates.txt`](ADG-UX-REVIEW-NAMES-08-evidence/frontend-gates.txt).

## 9. Frontend tests

New in `tests/governance-views.test.tsx` (3):
- the API name beats the page's names;
- a campaign row is named on one compact line, with no SID, no fallback, one `.muted` line
  and no `fetch`;
- a `null` name keeps the fallback beside a named neighbour.

`tests/contracts.test.ts` now expects `display_name` on `ScopeView`.

| Gate | Result |
|---|---|
| `npm run lint` | pass |
| `npm run typecheck` | pass |
| `npm run cf:typecheck` | pass |
| `npm run test` | **1694 passed**, 49 files (Wave C: 1691, +3) |
| `ADG_NEXT_OUTPUT=standalone npm run build` | pass |

## 10. Browser gate

[`review-names.mjs`](ADG-UX-REVIEW-NAMES-08-evidence/review-names.mjs) gave **11/11**
([`review-names.txt`](ADG-UX-REVIEW-NAMES-08-evidence/review-names.txt)):

1. **API.** "COPY-04 identity review" scopes are named Brianna Reddy and
   sec-share-design-read-only. The unknown SID is null, other kinds are null, and the keys are
   still SIDs.
2. **Reviews.** The row's line is "By identity · Identity: Brianna Reddy, Identity:
   sec-share-design-read-only". It has no "(name not loaded)" and no SID, has one muted line,
   and is 63 px high.
3. **Unresolved.** The fixture row shows `Identity: S-1-…-999999 (name not loaded)` next to
   "Identity: Brianna Reddy".
4. **Resource rows.** The 2 resource campaigns are unchanged.
5. **Old client.** The Wave C build reads the new API and shows its old fallback.
6. **Reviews request count.** 5 calls on both builds.
7. **Campaign page.** A click from Reviews is a client move. The title is "Review campaign —
   ADG", and the Reviewers table names both identity scopes from the status response's
   `display_name`, with no fallback.
8. **Campaign request count.** 6 calls on both builds.
9. **375 px.** Reviews and the campaign page do not scroll sideways.
10. **320 px.** The same, with the table keeping its existing internal scroll.
11. **Errors.** 0 page errors. The only console error is the known `/favicon.ico` 404.

Screens: [`screens/`](ADG-UX-REVIEW-NAMES-08-evidence/screens/) (`reviews-1440`,
`reviews-375`, `reviews-320`, `reviews-baseline-1440`, `campaign-1440`).

**COPY-04's committed gate** (`ADG-UX-BACKEND-COPY-04-evidence/copy-check.mjs`, run with the
Wave C `campaigns.json` ids) gives **16/17**
([`copy-check.txt`](ADG-UX-REVIEW-NAMES-08-evidence/copy-check.txt)). The failing check
asserts `Identity: <group SID> (name not loaded)` on the Reviews list. That is exactly the
fallback this lane replaces, so it fails by design. The historical gate is left unedited, as
Wave C did with the Wave B gate. The campaign-page and every other check pass.

## 11. Campaign detail page and the first-100 limitation

The status response's reviewer scopes now carry `display_name`, so the Reviewers table names
every identity scope regardless of which 100 items the page loaded. **The first-100 limitation
for reviewer scopes is resolved, with no extra request.** The `?principal=` items heading
still names the identity from the filtered items, which are that identity's own items. It
falls back to the SID only when that identity has no item in the campaign. Using status names
there too is a separate small change, left out to keep the lane bounded.

## 12. Limitations

1. **Current name in scopes, baseline name on items.** Scope labels use the identity's current
   name (§6). Item rows keep the baseline name. A renamed identity reads differently in the two
   places on the campaign page.
2. **Items heading.** `?principal=` with no matching item shows the SID (§11).
3. **COPY-04's historical gate** now fails its Reviews-list fallback assertion by design (§10).
4. **Local only.** Nothing was deployed. The hosted path (Worker → gate.ts) was not exercised.
5. **OpenAPI component rename.** The scan-run scope component is now named `ScanScopeView`.
   Its shape and wire JSON are unchanged (§4).

## 13. Serialization safety

Unchanged:
- scope `kind` values (`principal`, `share`, `directory_tree`, `server`);
- `key` (the SID or path);
- `ReviewScopeKind` / `CampaignFocus`;
- the `principal=` query parameter;
- campaign, assignment and rule ids;
- persistence: `database/` and `backend/app/models` untouched, no migration;
- graph node ids (no graph code touched).

The field is never written: `CreateCampaignRequest` and `AssignReviewerRequest` scopes still
take only `kind` and `key`.

## 14. Environment

| | |
|---|---|
| Demo DB | pgserver PostgreSQL 16.2, 127.0.0.1:55517. Copy of NAV-07's stopped `adg_demo` (Wave C data + COPY-04 campaigns) + the §6 fixture. Local only |
| DB test cluster | pgserver, 127.0.0.1:55518, `fsync=off`, `timezone=UTC` |
| API | this worktree, `python -m app --port 8080`, demo mode, development auth, throwaway secret. Baseline API (559dbaf, Wave C worktree) ran on :8081 only to capture §3's old payload |
| Web | this branch's standalone build on :3080 (reads via the proxy on :8180); Wave C `559dbaf` standalone build (NAV-07's parked `.nav07-baseline`) on :3081 (proxy :8181) |
| Browser | Google Chrome (/Applications), Playwright 1.62.1 via `NODE_PATH`, Node 22.23.3 arm64 |

## 15. Scope

The change covers the field, its resolution, the label and tests. Not touched:
- identity search;
- review pagination;
- campaign navigation;
- Recently viewed;
- titles;
- SVG tooltips;
- missing-object statuses;
- OpenAPI description wording.

`main` was not touched. Nothing was deployed.
