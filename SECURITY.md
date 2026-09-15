# Security posture

ADG reads and explains Windows permissions. Handled carelessly, its database is a map of
every weak point in a file estate, so the security posture is part of the product, not an
afterthought.

## Read-only by default

- Collectors perform **read-only** operations: directory enumeration, security-descriptor
  reads, and directory-service reads.
- The application never writes an ACL, never modifies group membership, and never deletes
  data on a target system.
- Governance *proposes* remediation and executes none of it. A review decision records a
  judgment about an observation and never alters the observation; a `revoke` produces a
  `RemediationProposal`, which is ADG's record of a change somebody might make in Windows. No
  code path in `app/governance` can write a table holding collected state, and two tests
  enforce that — one over the syntax tree, one by digesting every collected table across a
  whole campaign. See
  [ADR-0028](docs/decisions/0028-governance-is-metadata-about-observations.md).
- **A change plan is an instruction and ADG carries out none of it.** Phase 10C turns a
  decision into a precise, simulated, approved plan and exports it as a signed document plus a
  PowerShell runbook for a human administrator. There is no write adapter in this codebase and
  no dependency that could provide one; no route reaches an executor; `remediation:execute` is
  granted by no role; and the executor every deployment gets refuses unconditionally.
  `backend/tests/remediation/test_no_write_path.py` checks all five, and each guard is fed the
  mistake it exists to catch. What enabling a write path would require is
  [`docs/architecture/remediation.md`](docs/architecture/remediation.md) §8; the decision is
  [ADR-0035](docs/decisions/0035-a-change-plan-is-an-instruction-never-an-act.md).
- **A plan cannot be exported against facts that have moved.** Every step carries a content
  digest of the entry it was written against, re-checked against the timeline before anything
  is signed; a plan whose estate has moved is invalidated rather than exported
  ([ADR-0036](docs/decisions/0036-an-approval-binds-to-a-plan-digest-and-a-collection-basis.md)).
- **A plan may only narrow.** A modification that widens a mask, raises a share permission
  level, or narrows a *Deny* — which grants access — is refused before the plan is stored.
  Remediation that can grant access is not remediation.
- A review decision does not rewrite the review item either. Baseline drift — what the estate
  has done to a reviewed grant since the campaign was frozen — is computed on read and stored
  nowhere, so the evidence digest recorded in the audit trail keeps meaning what it meant when
  the decision was made
  ([ADR-0031](docs/decisions/0031-drift-is-reported-beside-an-item-never-applied-to-it.md)).
- Change execution remains out of scope. `Capability.REMEDIATION_EXECUTE` is reserved and
  granted by no role; when it arrives it must be an explicit, separately authorized, audited
  capability — never a side effect of a scan.

## Least privilege

- Normal collection must never require Domain Admin.
- Each collector must document the minimum rights it needs, for example:
  - Active Directory: read access to user, group, and membership attributes.
  - SMB shares: rights sufficient to enumerate shares and read share security descriptors.
  - NTFS: `READ_CONTROL` on the directories being inventoried; ideally traverse rights
    granted through a dedicated auditing group rather than broad administrative rights.
- Collector service accounts should be dedicated, non-interactive, and monitored.

## Secrets

- No secret value is committed. `.env.example` documents variable names and safe local
  development placeholders; `.env` is ignored by Git.
- Development credentials in `.env.example` and `docker-compose.yml` are for a local
  container only and must never be reused in a shared environment.
- The backend refuses to start with `ADG_ENVIRONMENT=production` while the development
  database URL is still configured.
- Diagnostics must never echo connection strings, credentials, or tokens. The readiness
  probe reports the failure class, not the DSN.

## Authentication and authorization

The application boundary is enforced in the backend. Every endpoint that speaks about
collected facts requires an authenticated principal holding a named capability; the frontend
hides sections an account cannot use, but that is a courtesy and not a control.

- **Production authentication is OIDC / Microsoft Entra ID.** Tokens are verified against
  the tenant's JWKS endpoint with an explicit asymmetric algorithm allow-list, and the
  issuer, audience, and expiry are all checked.
- **Development authentication issues its own tokens and verifies no credential.** It exists
  so that the product can be driven without a tenant. The API **refuses to start** with
  `ADG_AUTH_MODE=development` and `ADG_ENVIRONMENT=production`, and the development sign-in
  route is not registered at all outside development mode, so a production deployment's
  OpenAPI document does not describe one.
- **Roles are `viewer`, `auditor`, `admin`, `reviewer`, `governance_admin`,
  `remediation_planner` and `remediation_approver`.** `remediator` is reserved for the future
  ability to *carry out* a change and grants nothing; holding it is indistinguishable from
  holding no role. **No role grants `remediation:execute`**, and nothing implements it.
- **Proposing a change, approving it and producing the signed instruction are three pairs of
  hands.** `remediation:plan`, `remediation:approve` and `remediation:export` are held by
  disjoint roles, and the rule is checked against the *person* as well: the requestor cannot
  approve, and neither the requestor nor the approver can export — so somebody legitimately
  holding two roles is still refused the second act. A database constraint refuses a
  self-approved row whatever code writes it. See
  [ADR-0038](docs/decisions/0038-proposing-approving-and-carrying-out-are-three-pairs-of-hands.md).
- **A change plan is signed, and never unsigned.** With no `ADG_REMEDIATION_SIGNING_KEY`
  configured the export is refused rather than emitted unsigned: an unsigned change plan is
  indistinguishable from one somebody typed, and the administrator executing it has no way to
  tell. The signature proves the document came from this deployment unmodified — not that a
  named approver pressed a button, which is recorded inside the document and in the audit
  chain. A configured key must be **at least 32 characters**, refused at startup otherwise:
  refusing an unsigned export and then accepting a guessable key would be a control in name
  only.
- **A role ADG does not recognize grants nothing.** Unrecognized values are reported on
  `/auth/me` and logged, so a misassigned app role is diagnosable rather than mysterious.
- **Ingestion is never anonymous.** Collectors authenticate with a key from
  `ADG_COLLECTOR_API_KEYS` (compared in constant time, minimum 32 characters). A collector
  key grants exactly one capability, `collectors:ingest`: it can write observations and
  cannot read a single one back. With no keys configured, ingestion requires an
  administrator's access token.

### The browser holds no token

The web tier is a backend-for-frontend. The access token lives in an `httpOnly`, `SameSite`
cookie that only the Next.js server reads; browser code calls `/api/adg/*` on its own origin
and the server attaches the token. A token in `localStorage` or in a readable cookie is a
token any injected script can take, and this token is a key to a map of every weak permission
in the estate.

`/auth/me` is called on each render rather than cached in the cookie, so a role revoked in
the tenant takes effect on the next page load.

## Data sensitivity

- ADG stores permission metadata: SIDs, names, group edges, share and directory paths, and
  ACEs. It does **not** read, index, or classify file contents.
- Treat the ADG database and its backups as sensitive infrastructure data.
- **Decision rationales are a wider category of disclosure than the rest of the estate.**
  They are free text written about named people — "Alice moved teams in February" — and they
  are now reachable in a browser, not only over the API. `governance:read` is therefore not
  granted to `viewer`, the rationale column is personal data in the database and in every
  backup of it, and the retention question `docs/operations/mvp-runbook.md` §6 raises for the
  log stream applies to it too.
- **An alert payload is the one thing in ADG that leaves the deployment.** Everything else
  here is read over an authenticated API by somebody inside the boundary. A webhook sink
  posts ADG's own alert document — SIDs, account names, UNC paths, what changed and what a
  risk finding matched — to whatever URL the alert policy names, over whatever network the
  API can reach. Three things bound it, and they are the whole of the control:

  - the destination is **operator configuration** in `ADG_ALERT_POLICY_PATH`, never something
    a request can set;
  - `alerts:manage` — which decides what is watched and therefore what is sent — is granted
    to `admin` alone, and deliberately not to a governance administrator;
  - the sink **does not follow redirects**: a 3xx is treated as a permanent failure, because
    following one would deliver an estate's exposure report to a host nobody configured.

  The policy file may also carry a bearer token for that endpoint, so it is configuration and
  never source control. Ship it outside the repository, readable only by the API's service
  account.

- Access to the ADG application must be restricted to authorized auditors and
  administrators. Production authentication is OIDC / Microsoft Entra ID; see
  "Authentication and authorization" above.

## Out of scope

Data-loss prevention, content classification, SharePoint/OneDrive, Exchange, and
Linux/NFS permissions are outside the product boundary.

## Reporting a vulnerability

This project has no public distribution yet. Report suspected vulnerabilities to the
repository owner directly; do not open a public issue containing exploit detail or live
environment data.
