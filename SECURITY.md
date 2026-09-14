# Security posture

ADG reads and explains Windows permissions. Handled carelessly, its database is a map of
every weak point in a file estate, so the security posture is part of the product, not an
afterthought.

## Read-only by default

- Collectors perform **read-only** operations: directory enumeration, security-descriptor
  reads, and directory-service reads.
- The application never writes an ACL, never modifies group membership, and never deletes
  data on a target system.
- Later governance phases may *propose* remediation. Any change execution must be an
  explicit, separately authorized, audited capability — never a side effect of a scan.

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
- **Roles are `viewer`, `auditor`, and `admin`.** `remediator` is reserved for the future
  remediation capability and grants nothing; holding it is indistinguishable from holding no
  role. No role grants `remediation:execute`.
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
