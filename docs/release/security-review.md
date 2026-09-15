# ADG security review

**Status:** accepted
**Phase:** Release Audit
**Date:** 2026-09-15
**Scope:** every phase through 10C, as the tree stood at the audit.

What was examined, what holds, what was fixed, and — the part worth reading — what this
product does **not** defend against.

Everything below was checked against the code and, where the heading says *measured*, by
running it. Where a claim is only inspected statically, it says so.

---

## 1. The posture in one paragraph

ADG reads Windows permissions and explains them. It has no write adapter for Active
Directory, SMB or NTFS, no dependency that could supply one, and no route that reaches an
executor; the one capability that would name such a thing, `remediation:execute`, is granted
by no role and implemented by nothing (ADR-0035). The most dangerous artifact it produces is
a **signed change-plan document that a human being carries out by hand**. That is the whole
of its authority over an estate, and it is the reason the rest of this review is mostly about
disclosure rather than about damage.

The disclosure is not minor. A read-only account on ADG can see every weak permission in the
estate at once, which is a better target list than the file servers themselves.

---

## 2. Authentication

| Area | Finding |
| --- | --- |
| Production mode | OIDC / Entra ID. Tokens verified against the tenant JWKS with an explicit **asymmetric** algorithm allow-list (`RS256/384/512`, `ES256/384`), plus issuer, audience and expiry with a bounded leeway. `alg` is never taken from the token — the classic JWT forgery is foreclosed by passing `algorithms=` (`app/auth/tokens.py`). |
| Development mode | Issues its own tokens and verifies no credential. **The API refuses to start** with `ADG_AUTH_MODE=development` and `ADG_ENVIRONMENT=production`, and `POST /auth/dev/login` is *not registered at all* outside development mode — so a production OpenAPI document does not even describe one. |
| Development secret | Generated per process when unset, so no constant exists in the repository to be reused somewhere real, and a restart invalidates every development session. |
| Discovery | Every OIDC endpoint is configured explicitly rather than discovered. A startup that reaches out to the network to learn where to verify signatures is a startup whose security depends on DNS. |
| Issuer/JWKS scheme | Both must be `https`, refused at startup otherwise. |

**No finding.**

---

## 3. Authorization

Routes depend on **capabilities**, never on roles; a frozen table maps role → capability
(`app/auth/roles.py`). Adding a role therefore cannot silently change what an endpoint
requires.

This is the strongest-evidenced area in the product, because it is asserted against the route
table rather than against a hand-written list:

* `tests/api/test_authorization.py` reads **the OpenAPI document — every route the
  application actually serves** — and calls each one with no credential. A route added later
  and left unprotected fails here whether or not anybody wrote a test for it.
* It then calls every route **as each role** against one declared table, so "behind a
  capability" and "behind the *right* capability" are separately proven. The table is
  exhaustive over the served routes, so a new route with no entry fails rather than being
  skipped.
* The stub database raises if a session is opened, which turns a third property into a test:
  a request that will be rejected is rejected **before** it costs a connection.

**Separation of duties** is real and is enforced against the person, not only the token:
`remediation:plan`, `remediation:approve` and `remediation:export` are held by disjoint
roles, the requestor cannot approve, neither requestor nor approver can export, and a
database constraint refuses a self-approved row whatever code writes it (ADR-0038). Governance
is split the same way: `governance:manage` deliberately excludes `governance:review`, and
holding `governance:review` admits a request while granting authority over no particular item
— the **assignment** does that, re-checked against the database every time.

`admin` is a *platform* administrator and grants neither `governance:manage` nor
`governance:review`. The codebase states the honest limit of this: it is a control against
accident and casual misuse, not against a determined operator, because anyone with the
database credentials can do anything.

**No finding.**

---

## 4. Ingestion and collector transport

Ingestion is never anonymous. A collector presents `X-ADG-Collector-Key`, compared in
**constant time**, minimum 32 characters, and a presented-but-wrong key is refused outright
rather than falling through to the bearer check. A collector key grants exactly one
capability, `collectors:ingest`: **it can write observations and cannot read one back**, so a
key lifted off a file server is not a map of the estate.

> ### Finding S-1 (High) — fixed
>
> **The normative collector contract never said any of that.**
> `docs/contracts/collector-protocol.md` is the document a collector author implements
> against. It described the routes, the payloads, the idempotency rules and the retry
> semantics across ten sections and 661 lines, and contained **no mention of authentication
> or transport at all** — no `X-ADG-Collector-Key`, no bearer alternative, no TLS
> requirement. Its worked PowerShell example posted a batch with no credential.
>
> An author following it builds a collector that cannot authenticate, and — worse — has no
> reason to think it should, or that the payload needs TLS. The implementation was correct
> throughout; the specification was not.
>
> **Fixed:** an *Authenticating every request* section now precedes §1 (deliberately
> unnumbered, so no existing cross-reference moves), giving both credentials, the key-id
> convention, the constant-time and length rules, the one-capability property, a worked
> `Invoke-RestMethod` call, the https requirement, and the three failures — 401, 403, 422 —
> that must not be retried. The §9 example sends the header and stops retrying on 401/403.

---

## 5. Secrets

* No secret value is committed. `.env` is git-ignored; `.env.example` is a template.
* `ADG_COLLECTOR_API_KEYS` enforces a 32-character minimum at startup, with the stated
  reason that a guessable key is worse than none because it looks like a control.
* Logs are redacted at the handler (§8).
* The alert policy file may carry a webhook credential and is documented as configuration,
  never source control.

> ### Finding S-2 (Medium) — fixed
>
> **`ADG_REMEDIATION_SIGNING_KEY` had no minimum length.**
> It is the HMAC-SHA256 key over an exported change plan — the only thing separating an
> instruction this deployment produced from one somebody typed, and the only thing the
> administrator executing it at two in the morning can check. Export is *refused outright*
> when the key is empty, on exactly that reasoning. A one-character key was accepted.
>
> That is the middle case, and it is the worst of the three: it signs, every document it
> produces verifies, and the control is not one. Collector keys already enforced 32
> characters for the weaker of the two reasons.
>
> **Fixed:** `MIN_SIGNING_KEY_LENGTH = 32`, refused at startup with a message naming the
> setting and how to generate one; empty still means "this deployment cannot export".
> Pinned by `tests/test_config.py::TestTheChangePlanSigningKey` (5 tests, including that
> whitespace does not make a short key long). Documented in `.env.example`, `SECURITY.md`
> and the runbook.

---

## 6. The browser tier

The web application is a backend-for-frontend. **The access token never reaches the
browser**: it lives in an `httpOnly`, `SameSite=Lax` cookie only the Next.js server reads,
and `Secure` is set whenever `ADG_WEB_URL` is `https` — following the configured scheme
rather than `NODE_ENV`, which says nothing about the scheme actually in use.

The data proxy at `/api/adg/*` forwards **GET only**, deliberately, and has a path allow-list.
The two routes that write (`/api/simulations`, `/api/watches`) are separate handlers, so what
a browser may write is reviewable by reading two files rather than by reasoning about a
wildcard. Neither makes an authorization decision; the API's 403 is forwarded with its own
message, because that message names the missing capability.

The OIDC handshake compares `state` in constant time before exchanging the code, uses PKCE,
clears both handshake cookies on every path, and builds its redirect URI from configuration
rather than from the request's `Host` header.

> ### Finding S-3 (Low) — accepted, not fixed
>
> **The two mutating web routes have no explicit `Origin` check.**
> They are protected in practice, by two independent mechanisms: `SameSite=Lax` withholds
> the session cookie from any cross-site POST, and a JSON body triggers a CORS preflight
> that fails. Neither is a property of these handlers, though — both are properties of the
> browser — and a future change to either (a cookie moved to `SameSite=None`, a handler that
> starts accepting a form encoding) removes the protection silently.
>
> Recorded rather than fixed: adding an `Origin` assertion is a small, safe change, but it
> is a hardening measure and not a defect, and this audit is not the place to change how
> two working routes admit requests. It belongs in the next phase that touches them.

---

## 7. Input validation and query safety

| Concern | Finding |
| --- | --- |
| SQL injection | **No dynamic SQL reaches a query in `app/`.** Every statement is SQLAlchemy Core with bound parameters. `text()` appears only as fixed literals — index predicates in `app/models/schema.py` and the readiness probe's `SELECT 1` — and a grep for an f-string or `%`-format inside `execute(` or `text(` returns nothing. (Test and benchmark code does interpolate table names into `TRUNCATE` and `CREATE DATABASE`, from `metadata` and from configuration, never from a request.) |
| `LIKE` injection | Search escapes `\`, `%` and `_`, backslash first, with an explicit `escape=` — so a typed `%` searches for a percent sign. Matching is prefix-only and every statement carries a `LIMIT` whose truncation is reported (`app/repositories/search.py`). |
| Path parameters | Every one is constrained by `PRINTABLE_IDENTIFIER`, refusing control characters before a database round trip. This came from Phase 6D fuzzing every path parameter of every route: a NUL byte survived every parser, reached psycopg and produced a 500. |
| Pagination cursors | base64url JSON, **unsigned by design** — a cursor is a position, not a capability, and the value it carries is a key the caller already has. A malformed or foreign cursor is a 422 rather than a silent restart from page one, because a silent restart makes a second page look like a complete result set. |
| Traversal bounds | Caller-supplied limits are *clamped* to server ceilings and the response reports what was actually used; a truncated traversal sets `complete: false`, which every consumer is required to read as "lower bound, not a list". |
| Webhook delivery | `httpx` defaults (TLS verified, redirects **not** followed); a 3xx is classified permanent, because following one would deliver an estate exposure report to a host nobody configured. |

**No finding.** One note, below.

**Note (not a finding):** a webhook sink accepts an `http://` URL. Over plain http that
publishes an exposure report and a bearer token to anything on the path. This is operator
configuration of an off-by-default feature, it is stated in the operations document, and
refusing `http` outright would break a loopback receiver used for testing — so it is recorded
in `known-limitations.md` rather than changed here.

---

## 8. Log redaction

Applied **at the handler**, once, to everything: the rendered message, every positional
argument, every `extra=` value (walked into dicts and lists), cached exception text, and the
traceback and stack rendered at format time. Four shapes are removed — a JWT, an
`Authorization` header, a URL password, and labeled `key=value` secrets — and the rule order
is deliberate, each pattern refusing to re-match an already-redacted value.

What is **deliberately not redacted** is as important: SIDs, UNC paths, account names and
group names appear in full, because they are what an operator correlates against a Windows
event log and because ADG's whole subject is who and what.

> **That makes the log stream personal data**, which is a retention and access question
> rather than a redaction one. It is answered in the runbook, and a deployment that ships
> logs to a third-party aggregator is shipping an identity export there. Restated in
> `known-limitations.md` because it is the kind of thing that is agreed once and forgotten.

**No finding.**

---

## 9. Production defaults

Verified by reading the validators in `app/config.py`; each refuses at **startup**, so the
process does not begin rather than beginning wrong.

* `ADG_AUTH_MODE=development` + `ADG_ENVIRONMENT=production` → refused.
* Development database URL + production → refused.
* `ADG_REMEDIATION_EXECUTION_MODE` other than `disabled` + production → refused. (Stated
  twice on purpose — here and in `remediator_for` — because a guard written once is a guard
  somebody moves.)
* `lab` mode without a fixture → refused, rather than degraded to `disabled`, so a test that
  asked for a lab executor cannot silently receive a refusing one and pass for the wrong
  reason.
* Malformed role maps and collector-key tables are parsed at startup, where the error names
  the entry, rather than on the first sign-in attempt.

**No production default permits AD, SMB or NTFS mutation**, and no code path exists that
could. Five independent guards assert it (Phase 10C).

---

## 10. Minimum privileges

### Collectors — none of them needs Domain Admin

| Collector | Needs | Does not need |
| --- | --- | --- |
| Active Directory | Read on the directory — any authenticated domain account | Domain Admin |
| SMB | Read the share list and share ACLs on each file server (`Get-SmbShare`) | Local Administrator in general, though some servers restrict the SMB cmdlets |
| NTFS | **`READ_CONTROL`** on the directories walked | Read of file *contents* |

`READ_CONTROL` is the one to get right: it is the right to read a security descriptor, not
the right to read the data. A service account holding it and nothing else can audit a share
it cannot open. Where the account lacks it the collector records `access_denied`, the run is
recorded **partial**, and it is visible on the Collectors page rather than silently skipped.
A collector never escalates, takes ownership, or modifies an object to make a read succeed
(ADR-0004).

### The API service

PostgreSQL connection rights only. No Windows privilege, no domain membership requirement,
no filesystem access beyond its own configuration. It is a Linux container.

### The database account

Owner of its own database: DDL for migrations, DML at run time. No superuser.

### The person

The least-privileged useful account is `viewer`. Grant `auditor` for governance and
simulation *reading*; grant the three remediation roles to **three different people** or the
separation of duties is a formality the software cannot restore.

---

## 11. Findings summary

| # | Severity | Area | Status |
| --- | --- | --- | --- |
| S-1 | High | Collector contract specified no authentication or transport | **Fixed** |
| S-2 | Medium | Change-plan signing key had no minimum length | **Fixed** |
| S-3 | Low | Mutating web routes rely on `SameSite` rather than an `Origin` check | Accepted; recorded |

No critical finding. No finding required a contract version bump: S-1 is additive
documentation of behavior the server already enforced, and S-2 narrows an input the product
documents as a secret.

---

## 12. What this review did not cover

Stated so that its silence is not read as assurance.

1. **No penetration test, and no dependency CVE scan.** Neither was in scope and neither has
   been run against this tree.
2. **No live tenant.** OIDC verification is reviewed by reading the verifier and exercised
   against tokens this process mints. Nothing here has met Entra ID.
3. **No live domain controller or file server.** See `known-limitations.md` §1 — this is the
   largest gap in the product's evidence, and it is not primarily a security one.
4. **Denial of service is bounded, not solved.** Traversals, comparisons, campaigns and
   simulations all carry ceilings and report truncation, and an unauthenticated request is
   refused before it costs a database connection. No load test has been run, and
   `simulations:run` is deliberately held from `viewer` precisely because it is the most
   expensive request the API serves.
5. **The database is trusted.** Anyone holding its credentials can do anything, including
   rewriting the hash-chained governance audit trail wholesale. The chain detects a *quiet
   edit*, which is what it claims, and the codebase says so rather than implying more.
