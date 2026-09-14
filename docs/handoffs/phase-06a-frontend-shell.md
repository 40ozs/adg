# Handoff — Phase 6A (`phase-06/01-frontend-shell-auth.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-05a-access-paths.md](phase-05a-access-paths.md)
**Contract version after this phase:** `1.3` — unchanged. No collector payload changed, no
schema, no migration. The collector *transport* gained a credential (see "Migration").

## Scope completed

Before this phase every ADG endpoint was open. The database is a map of every weak point in a
file estate, and the API was the only thing between that map and the network.

1. **An authorization boundary in the backend**, declared once per router in
   `app/api/__init__.py` and enforced as capabilities, not roles ([ADR-0014](../decisions/0014-authorization-is-capabilities-enforced-in-the-backend.md)).
2. **Two authentication modes that share no code path.** OIDC / Entra ID for production;
   a development mode that issues its own tokens and that the API **refuses to start** in
   production.
3. **Four roles**, one of them reserved and granting nothing.
4. **Ingestion is no longer anonymous.** Collector API keys, constant-time compared,
   granting exactly one capability.
5. **The operational frontend**: eight navigation sections, a global search shell, a
   backend-for-frontend that keeps the access token out of the browser, and loading /
   error / empty states that distinguish *nothing is there* from *nobody looked*
   ([ADR-0015](../decisions/0015-emptiness-is-attributed-by-the-backend.md)).
6. **Three new API surfaces** the shell needs: global search, the run list, and the
   collection-coverage verdict.
7. **A published OpenAPI snapshot** the frontend's hand-written types are checked against,
   in both directions.
8. **+367 tests** — 218 backend (95 authentication, 38 domain, 25 structural authorization,
   3 contract, 57 through HTTP against PostgreSQL), 10 collector, and 139 frontend.

## Three things worth reading before the code

### 1. The boundary is tested as a property, not as a list

`backend/tests/api/test_authorization.py` reads the OpenAPI document — every route the
application actually serves — and calls each one with no credential, asserting `401`. A route
added in a later phase and left open fails the suite whether or not anyone wrote a test for
it. A second test asserts the public set is *exactly* `PUBLIC_PATHS`, so making something
public is a deliberate edit with a reason beside it.

Its stub database raises if anything opens a session. **That found a real defect.** FastAPI
solves route-level dependencies before a handler's own, and a handler's own in declaration
order — so the ingestion routes, which took `session: Session` before
`principal: IngestPrincipal`, opened a database session for every anonymous request before
rejecting it. Fixed by declaring `Depends(ingest_principal)` on the route as well; FastAPI
caches it, so it resolves once. The exploding stub stays.

### 2. A collector key can write and cannot read

`collectors:ingest` and nothing else. The key lives in a scheduled task's configuration on a
file server; if stealing it also handed over the estate, the key would be the finding.
`tests/db/test_authorization.py` proves both halves against a real database, including that a
collector cannot inspect the run it just created.

With no keys configured, ingestion requires an administrator's token. It is never anonymous
in either configuration.

### 3. An empty page is attributed by the backend

`app/domain/collection.py` folds the latest run of each collector into one verdict —
`no_data`, `healthy`, `incomplete`, `failed` — published at `GET /api/v1/collection/status`.
`frontend/lib/state.ts` turns that plus an API result into a `ViewState`. Exactly one of its
five empty reasons words the emptiness as an answer, and it is reachable only under
`healthy`. Unknown coverage is never treated as good coverage.

The same verdict appears as a caveat *above data that is present*, which is the more
important half: twelve shares look complete whether or not a collector failed on a
thirteenth.

## Files and modules added or materially changed

### Backend — authentication and authorization (new)

| File | Lines | Contents |
| --- | ---: | --- |
| `app/auth/roles.py` | 143 | The whole authorization model: `Role`, `Capability`, the table, `capabilities_for`, `parse_roles` |
| `app/auth/principal.py` | 176 | `AuthenticatedPrincipal`, and the pure claims-to-principal mapping |
| `app/auth/tokens.py` | 220 | `OidcTokenVerifier`, `DevelopmentTokenVerifier`, `issue_development_token` |
| `app/auth/dependencies.py` | 300 | `requires(capability)`, `CurrentPrincipal`, `ingest_principal`, collector-key matching |
| `app/auth/dev_users.py` | 103 | The development account list and its refusals |
| `app/api/auth.py` | 255 | `/auth/config`, `/auth/me`, and the development-only `/auth/dev/login` |

### Backend — new surfaces

| File | Lines | Contents |
| --- | ---: | --- |
| `app/domain/search.py` | 128 | Reading a search box: SID, UNC path, server, share, or name |
| `app/domain/collection.py` | 147 | The coverage verdict, as a pure function |
| `app/repositories/search.py` | 245 | Four bounded, prefix-only, wildcard-escaped lookups |
| `app/services/search.py` | 163 | Which lookups to run, and which categories the caller may see |
| `app/api/search.py` | 181 | `GET /api/v1/search` |
| `app/api/collection.py` | 101 | `GET /api/v1/collection/status` |
| `app/contracts/openapi.py` | 62 | Generates `docs/contracts/v1/openapi.json` |

### Backend — changed

- `app/config.py` — 13 authentication settings and five startup refusals.
- `app/api/__init__.py` — `api_router` became `build_api_router(settings)`; every data
  router is now included behind a capability. **The Phase 5B session has already adopted this
  and added its `groups` router the same way.**
- `app/api/scan_runs.py` — ingestion behind `ingest_principal`; added `GET /api/v1/scan-runs`.
- `app/ingestion/service.py` — `list_runs`, `latest_run_per_collector`, `RunSummary`,
  `RunListPage`.
- `app/main.py` — installs the verifier and claims mapping once per application.
- `pyproject.toml` — added `pyjwt[crypto]>=2.9`.

### Frontend (new)

`lib/contracts.ts`, `lib/nav.ts`, `lib/state.ts`, `lib/api/{client,adg,health}.ts`,
`lib/auth/{session,oidc,current}.ts`; `components/{AppShell,PrimaryNav,GlobalSearch,Banners,SignOutButton,SignedOutNotice}.tsx`;
`app/api/adg/[...path]/route.ts` (the BFF proxy), `app/api/auth/{dev-login,logout,oidc/start,oidc/callback}/route.ts`;
pages for login, resources, identities, access, risks, changes, collectors, settings, search;
`vitest.config.ts` and five test files. `lib/api.ts` was folded into `lib/api/health.ts` —
a file and a directory of the same name is a resolution ambiguity waiting to happen.

### Collectors

The transports gained a credential, because ingestion now requires one. `New-AdgCollectorHeaders`
in the common module; `-Headers` threaded through the SMB transport and carried on the NTFS
transport; `-CollectorKey` on the publisher; and all three entry scripts read the credential
from an environment variable — never a parameter value, so it is not in a command line, a
scheduled-task argument list, or a shell history. A 401/403 is never retried: a credential the
server refuses is refused identically every time.

### Documentation

`docs/architecture/authentication.md` (new), ADR-0014 and ADR-0015 (new), `SECURITY.md`,
`README.md`, `.env.example`, `frontend/.env.example`, `docker-compose.yml`.

## Schemas and contracts introduced or changed

**No collector contract change.** Payloads, schemas, keys, and status codes are untouched.

**New:** `docs/contracts/v1/openapi.json` — the API's OpenAPI document, generated for a
development-mode application (the superset, since a tenant deployment serves one route
fewer). Two tests keep it honest in opposite directions:

- `backend/tests/contracts/test_openapi_snapshot.py` fails if the snapshot is stale.
  Regenerate with `python -m app.contracts.openapi`.
- `frontend/tests/contracts.test.ts` asserts, field by field for 21 schemas, that every
  property `lib/contracts.ts` reads is one the API declares **and** that no required property
  is one the frontend ignores. The second direction is the one that catches a new field the
  UI should be showing. Verified to bite: deleting one field name from the expectation fails
  the run.

**New response headers:** none. **New status codes on existing routes:** `401` and `403`,
everywhere.

## Tests run and exact results

Windows 11, Python 3.13.14, Node 22.20.0, PostgreSQL 16 in Docker.

| Command | Result |
| --- | --- |
| `pytest -q -m "not smoke"` | **3,965 passed, 13 skipped, 1 failed** (see below), 404 deselected, 19.8s |
| `pytest -q -m smoke` (PostgreSQL) | **403 passed, 1 xfailed**, 208s |
| `ruff check .` | 1 error, in another session's file (see below) |
| `ruff format --check .` | 2 files would be reformatted, both another session's |
| `mypy app tests` | 3 errors, all in another session's file |
| `npm run lint` | clean |
| `npm run typecheck` | clean |
| `npm run test` | **147 passed** (6 files), 1.9s |
| `npm run build` | succeeded; 17 routes, all dynamic |
| `.\scripts\collector-test.ps1` | **944 passed, 0 failed** (934 before this phase), 37s |

### The failures are not this phase's

A second session was working in this tree throughout, and committed Phase 5A partway through
(`153aff4`), then began Phase 5B. At the time of writing, its in-flight work leaves:

- `tests/contracts/test_json_schemas.py::test_every_contract_kind_has_a_schema` failing,
  because `app/contracts/derived.py` (untracked, theirs) has generated three schema files the
  test's expected set does not yet name.
- `ruff check` failing on `app/contracts/derived.py` (`__all__` not sorted).
- `ruff format --check` on `app/api/caching.py` and `app/repositories/basis.py`.
- `mypy` on `tests/access_engine/test_causality.py` (3 errors).

**None of these are touched by this phase, and none were introduced by it.** They are left
alone deliberately: reformatting or editing another session's open files mid-flight is how two
sessions produce one broken commit. The commit made here contains only this phase's paths.
Backend lint, format, mypy and the full hermetic suite are clean over everything this phase
added or changed.

### What the new tests actually pin

- **The role table, spelled out literally** (`tests/auth/test_roles.py`, 19). A test that
  recomputed the expected sets from `ROLE_CAPABILITIES` would agree with any table, including
  a wrong one.
- **Entra's real claim shapes** (`tests/auth/test_principal.py`, 23): a single string where a
  list was expected, group object ids, a custom claim name, a token with no `sub`. This is the
  part that cannot be exercised against a tenant locally, so it is exercised exhaustively
  without one.
- **The attacks the verifiers exist to stop** (`tests/auth/test_tokens.py`, 17): `alg: none`,
  and algorithm confusion — an HS256 token signed with the RSA *public* key, assembled by hand
  because PyJWT refuses to sign one. Also that a development verifier rejects a tenant token
  and vice versa.
- **Startup refusals** (`tests/auth/test_auth_settings.py`, 22).
- **The boundary, structurally** (`tests/api/test_authorization.py`, 25).
- **The wording of an empty page** (`frontend/tests/state.test.ts`, 15). The wording *is* the
  feature: one test asserts that exactly one of the five cases claims nothing is there.
- **Keyboard and screen-reader behavior** (`frontend/tests/components.test.tsx`, 20): tab
  order through the navigation, `aria-current` on the current section, Enter submitting
  search, `/` focusing it from elsewhere and **not** stealing it from someone typing a UNC
  path, and the development banner being an `alert` rather than grey text.

## Known limitations

1. **The OIDC round trip has never been executed against a real tenant.** There is no tenant
   available. The pure parts are unit-tested — authorize URL, PKCE challenge, constant-time
   `state` comparison, token request body, expiry — and the verifier is tested against a
   locally generated RSA key. What is untested is the actual redirect, consent, and code
   exchange with Entra ID. Expect to find configuration problems there (redirect URI mismatch,
   a token issued for Graph instead of for ADG) rather than logic problems; the scope handling
   in `scopeString` exists specifically because of the second.
2. **No token refresh.** When the access token expires the user signs in again. A refresh
   token would have to be stored server-side, which is a design decision this phase did not
   need to make.
3. **Sign-out is local.** The cookie is cleared; the token stays valid at the provider until
   it expires. ADG cannot revoke a tenant's token and does not pretend to.
4. **The BFF proxies GET only.** Every write in ADG today is collector ingestion, which
   authenticates with a key from a Windows host and has no business arriving from a browser
   session. Phase 7+ will need to widen this deliberately.
5. **Search is prefix-only, and says so.** `finance` does not match `Corp-Finance`. An infix
   match cannot use a btree index and degrades into a sequential scan of every path in the
   estate on a keystroke. The interpretation string tells the user.
6. **Search caps each category at 10** and reports truncation. It is a search box, not a
   report; the categories that deserve paging have their own endpoints.
7. **Identities and Access are lookup entry points, not listings.** A domain's principal
   table is hundreds of thousands of rows and scrolling it answers nothing. Detail pages for
   a principal and for a resource are the obvious next increment.
8. **Risks and Changes are labelled placeholders.** Deliberately labelled: an empty table
   headed "Risks" reads as "no risks found".
9. **Settings is read-only.** `settings:write` exists and no route uses it yet.
10. **The development signing secret is per process.** Restarting the API invalidates every
    development session. That is the intended trade; set `ADG_DEV_AUTH_SECRET` to avoid it
    locally.
11. **JWKS is fetched with a blocking client** (`PyJWKClient`) in a worker thread. Fine at
    this scale; a purpose-built async cache would be better under real load.

## Security and privilege assumptions

- **Production cannot run development authentication.** `Settings` refuses to construct, so
  the process does not start. The development login route is not registered outside
  development mode, so a production deployment's OpenAPI document does not describe one.
- **The browser never holds the access token.** It is in an `httpOnly` cookie only the
  Next.js server reads. `Secure` follows the configured `ADG_WEB_URL` scheme, not `NODE_ENV`.
- **`/auth/me` is called on every render**, not cached in the cookie, so a role revoked in
  the tenant takes effect on the next page load.
- **Frontend hiding is not security.** `visibleNavItems(["settings:write"])` returns the
  overview and nothing else — the frontend does not know that admins can do everything,
  because it does not know what an admin is.
- **Collector keys are a bearer secret**: minimum 32 characters, compared with
  `hmac.compare_digest`, every configured key compared even after a match.
- **No new privilege is required of a collector.** Read-only posture unchanged.
- **Nothing secret is published.** `/auth/config` carries issuer, client id, and endpoints —
  what a public client needs to start a sign-in. A test asserts the word "secret" does not
  appear in its response, and another asserts the committed OpenAPI snapshot carries no
  generated secret.

## Migration and compatibility notes

**This phase breaks unauthenticated callers, on purpose.**

1. **Collectors must present a credential.** Set `ADG_COLLECTOR_API_KEYS` on the API and put
   the matching secret in `$env:ADG_COLLECTOR_KEY` on each collector host (or
   `$env:ADG_COLLECTOR_TOKEN` for an operator's bearer token). Every entry script warns
   before a run that would be rejected. Without keys configured, ingestion still works with an
   administrator's token.
2. **Any existing script calling `/api/v1/*` needs a token.** There is no compatibility
   window and no opt-out flag; an opt-out would be the hole.
3. **Production deployments must configure OIDC** before they will start:
   `ADG_AUTH_MODE=oidc`, `ADG_OIDC_ISSUER`, `ADG_OIDC_AUDIENCE`, `ADG_OIDC_JWKS_URL`.
4. **No database migration.** No table, column, or index changed.
5. **`pyjwt[crypto]` is a new runtime dependency.** `pip install -e ".[dev]"` picks it up.
6. **New frontend dev dependencies:** `@testing-library/*`, `jsdom`, `@vitejs/plugin-react`;
   and `server-only` as a runtime dependency (aliased to a stub under Vitest, because the real
   module throws unless resolved through React's `react-server` condition).

## Prerequisites for the next prompt

1. **Every new route must be added to `build_api_router` behind a capability.** A router
   without an entry there is unreachable; `tests/api/test_authorization.py` fails on a route
   that answers unauthenticated. If a route genuinely must be public, add its path to
   `PUBLIC_PATHS` with a reason.
2. **Declare the auth dependency on the route, not only as a handler parameter**, for any
   route that also takes a `Session`. Parameters are solved in declaration order; the
   exploding-stub test will catch it, and the message says what to do.
3. **Do not compute permissions in the browser.** `frontend/lib/state.ts` and the pages read
   backend answers. The effective-access engine is checked against Windows' own
   `AuthzAccessCheck`; a second implementation in TypeScript could only disagree with it.
4. **Every new view must ask `GET /api/v1/collection/status` before rendering an empty
   state**, and pass the verdict to `classify()`. Do not branch on `items.length === 0`.
5. **Regenerate `docs/contracts/v1/openapi.json`** (`python -m app.contracts.openapi`) after
   changing any endpoint, and add the new response fields to `EXPECTED` in
   `frontend/tests/contracts.test.ts` if the frontend reads them.
6. **Risk rules (Phase 5/7) must branch on `AccessCertainty`** — unchanged from the Phase 4C
   handoff — and should now also surface `CollectionHealth`, since a risk listing is exactly
   the place an empty result gets misread.
7. **The `remediator` role and `remediation:execute` are reserved.** A remediation route can
   be written against the real capability today and will be provably unreachable until a role
   grants it. `tests/auth/test_roles.py` asserts none does.
8. **An OIDC tenant is needed to close limitation 1.** Until then, treat the redirect flow as
   unverified in any deployment note.

## `git status --short`

Immediately after the commit. **Every entry below belongs to the concurrent Phase 5B
session** and was deliberately left out of it:

```
 M backend/app/access_engine/__init__.py
 M backend/app/api/__init__.py
 M backend/app/api/access.py
 M backend/app/domain/__init__.py
 M backend/app/repositories/__init__.py
 M backend/tests/api/test_access_bounds.py
 M backend/tests/contracts/test_smb_collector.py
?? backend/app/access_engine/verdict.py
?? backend/app/api/caching.py
?? backend/app/api/groups.py
?? backend/app/contracts/derived.py
?? backend/app/domain/basis.py
?? backend/app/repositories/basis.py
?? backend/tests/access_engine/test_verdict.py
?? backend/tests/api/test_caching.py
?? backend/tests/db/test_explanation_api.py
?? backend/tests/domain/test_basis.py
?? docs/contracts/v1/access-explanation.schema.json
?? docs/contracts/v1/access-paths.schema.json
?? docs/contracts/v1/resource-impact.schema.json
```

`backend/app/api/__init__.py` shows as modified because that session has already added its
`groups` router to `build_api_router`, behind `ACCESS_READ`. Its version was **not**
committed here: `app/api/groups.py` is still untracked, so a commit importing it could not
be checked out and run. The committed content was staged from a prepared blob rather than
from the working tree, and the whole commit was verified in a throwaway worktree at `HEAD`
plus this phase's files only — **3,954 passed, 0 failed**, `ruff check` clean.

**One thing that session must do:** regenerate `docs/contracts/v1/openapi.json`
(`python -m app.contracts.openapi`) before committing Phase 5B. The committed snapshot
describes the 31 routes this commit serves; `tests/contracts/test_openapi_snapshot.py`
fails in the shared working tree until the new routes are published into it. That is the
test working, not a defect.

**Commit:** `8336ecc` — 103 files, +22,003/-160. Handoff hash recorded in the follow-up.
