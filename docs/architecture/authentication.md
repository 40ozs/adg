# Authentication and authorization

**Introduced:** Phase 6A. **Applies to:** the API and the web tier.

ADG's database is a map of every weak point in a file estate. The boundary in front of it is
part of the product, not an afterthought, and it is enforced in exactly one place.

## The shape of it

```
Browser ──cookie(httpOnly)──▶ Next.js (BFF) ──Bearer token──▶ FastAPI ──▶ PostgreSQL
                                   │                              │
                          /api/adg/* proxy               capability check per route
                                                                  │
Collector (Windows) ──X-ADG-Collector-Key────────────────────────▶ ingestion routes only
```

Three credentials exist and they do not overlap:

| Credential | Held by | Grants |
| --- | --- | --- |
| OIDC access token | A person, through Entra ID | Whatever their roles grant |
| Development token | A person, in development only | Whatever the chosen account's roles grant |
| Collector API key | A scheduled task on a file server | `collectors:ingest`, and nothing else |

## Roles and capabilities

The model is a frozen table in `backend/app/auth/roles.py`, and it is the only copy. The
frontend never reproduces it: `/auth/me` returns the caller's *capabilities*, and the UI does
set membership against that list.

| Capability | viewer | auditor | admin | remediator |
| --- | :-: | :-: | :-: | :-: |
| `resources:read` | ● | ● | ● | |
| `identities:read` | ● | ● | ● | |
| `access:read` | ● | ● | ● | |
| `risks:read` | ● | ● | ● | |
| `changes:read` | ● | ● | ● | |
| `collectors:read` | ● | ● | ● | |
| `search` | ● | ● | ● | |
| `settings:read` | | ● | ● | |
| `settings:write` | | | ● | |
| `collectors:ingest` | | | ● | |
| `remediation:execute` | | | | |

Two rules are structural rather than conventional:

- **A viewer can see whether collection succeeded.** `collectors:read` is granted to every
  role deliberately. Without it, a viewer cannot tell an empty estate from a failed scan,
  which is the misreading this product exists to prevent.
- **`remediator` grants nothing, and no role grants `remediation:execute`.** The role exists
  so a tenant can provision it ahead of the feature and so the name cannot be reused.
  Until remediation ships, holding it is indistinguishable from holding no role at all.

An account whose token carries a role ADG does not recognize gets nothing from it, and the
value is reported on `/auth/me` and logged at WARNING — so "I assigned `ADG-Auditors` and
they still see nothing" is answerable from a log line.

## Where enforcement lives

`backend/app/api/__init__.py` includes every data router with a capability dependency, in one
visible list. Enforcement at the include site rather than inside handlers means a route added
without one is a router with no entry in that file, not a handler somebody forgot to guard.

`backend/tests/api/test_authorization.py` reads the OpenAPI document — every route the
application actually serves — and calls each one without a credential. A new unprotected path
fails the suite whether or not anyone wrote a test for it. Its stub database raises if a
session is opened, which also pins a second property: a request that will be rejected is
rejected *before* it costs a database connection.

Ordering matters and is easy to get wrong. FastAPI solves route-level dependencies before a
handler's own, and a handler's own in declaration order. An auth dependency declared only as
a parameter *after* `session: Session` runs too late — the session is already open. The
ingestion routes therefore declare `Depends(ingest_principal)` on the route as well; FastAPI
caches it, so it resolves once.

## Verification

`backend/app/auth/tokens.py` holds two verifiers that share no code path and no defaults.

- `OidcTokenVerifier` — asymmetric algorithms only, from an explicit allow-list; signing keys
  from the tenant's JWKS endpoint (cached per application, fetched in a worker thread because
  the call blocks); `iss`, `aud`, `exp` and the presence of `sub` all required.
- `DevelopmentTokenVerifier` — HS256 only, under the issuer `adg-development-only`, which no
  real tenant can produce.

The allow-lists are the point. `tests/auth/test_tokens.py` forges an HS256 token signed with
the RSA *public* key — the classic algorithm-confusion attack, assembled by hand because
PyJWT refuses to sign one — and asserts it is rejected. It does the same for `alg: none`.

## Configuration refusals

`Settings` refuses to construct rather than starting a process with a hole in it:

- `ADG_AUTH_MODE=development` with `ADG_ENVIRONMENT=production`.
- `ADG_AUTH_MODE=oidc` without issuer, audience, and JWKS URL — all three named at once.
- An issuer or JWKS URL that is not `https`.
- A collector key shorter than 32 characters, or a duplicate key id.
- A group-to-role map or development account list naming a role that does not exist.

JWKS and the authorization/token endpoints are configured explicitly; the API performs no
discovery at startup. A process whose signature verification depends on a DNS answer is a
process whose security depends on DNS.

## The browser holds no token

The web tier is a backend-for-frontend. `frontend/app/api/adg/[...path]/route.ts` proxies
browser requests to the API with the session's token attached, against a path allow-list, and
forwards status codes and messages unchanged — a 403's message names the capability the
account is missing, which is what somebody needs to forward to an administrator. Only GET is
proxied: every write in ADG today is collector ingestion, which has no business arriving from
a browser session.

The OIDC authorization-code flow with PKCE lives in `frontend/lib/auth/oidc.ts` as pure
functions, with the route handlers in `frontend/app/api/auth/oidc/`. `state` is compared in
constant time against a short-lived `httpOnly` cookie, and the handshake cookies are cleared
on every path, success or failure.

**Not verified against a real tenant.** The pure parts are unit-tested; the round trip
through Entra ID has never been executed. See the Phase 6A handoff for what that leaves open.
