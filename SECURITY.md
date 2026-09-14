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

## Data sensitivity

- ADG stores permission metadata: SIDs, names, group edges, share and directory paths, and
  ACEs. It does **not** read, index, or classify file contents.
- Treat the ADG database and its backups as sensitive infrastructure data.
- Access to the ADG application must be restricted to authorized auditors and
  administrators. Production authentication is OIDC / Microsoft Entra ID.

## Out of scope

Data-loss prevention, content classification, SharePoint/OneDrive, Exchange, and
Linux/NFS permissions are outside the product boundary.

## Reporting a vulnerability

This project has no public distribution yet. Report suspected vulnerabilities to the
repository owner directly; do not open a public issue containing exploit detail or live
environment data.
