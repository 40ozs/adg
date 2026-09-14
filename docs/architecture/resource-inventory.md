# Resource inventory: servers, shares, and raw share ACLs

How ADG stores what a share scan observed, and what the resource APIs will and will not
claim. The membership half of the picture is in
[membership-graph.md](membership-graph.md); the domain types are in
[permission-domain-model.md](permission-domain-model.md).

## What a resource is

Three things, each identified by the string the domain layer produces for it.

| Object | Key | Domain source |
| --- | --- | --- |
| Server | `fs01` | `Server.identity_key` — the case-folded name it was collected under |
| Share | `fs01\|finance` | `SmbShare.identity_key` — server plus share name, case-folded |
| Share ACE | `fs01\|finance\|S-1-1-0\|allow\|read` | `SmbShareAce.identity_key(share_key)` |

A **share is a publication of a directory, not the directory**. Two shares can point at one
path with different ACLs, and a share can be re-pointed without the directory changing, so
re-pointing updates the same row while renaming creates a new one. A rename really is a new
identity: the old name can be re-used for something else tomorrow.

A **share ACE is identified by what it grants, not by where it sits.** `order_index` is
recorded — canonical DACL order is what makes a Deny evaluable — but it is not part of the
key, so an administrator reordering an ACL does not look like every entry being deleted and
recreated.

A **share ACL reported as levels and the same ACL reported as masks are different
observations.** `Get-SmbShareAccess` can only say `change`; a security descriptor says
`0x001301bf`. The form is part of the key, and nothing converts between them: rendering a
level as a mask would claim precision the level never had. Reconciling the two is the Phase 4
rights algebra's job ([rights-model.md](rights-model.md)).

## What is derived, never stored

| Value | Derived from |
| --- | --- |
| `\\fs01\Finance` | `SmbShare.unc_path` |
| `is_hidden` | the share name ending in `$` |
| `is_administrative` | `ADMIN$`, `IPC$`, or a drive-letter share |
| `carries_file_permissions` | the share type being `disk` |
| a trustee's resolution | a left join against `principals` |

A stored copy of a derived value is a second version of the truth that can disagree with the
first. See [ADR-0007](../decisions/0007-resolution-is-a-join.md).

`is_special` is **not** derived, and is the reason the derivation is not enough on its own:
it is the SMB server's own Special flag, and an ordinary hidden share such as `Data$` is
hidden and not Special. `null` there means the source did not say, which is not `false`.

## The tables

Revision `0003_smb_resources` adds four, declared in `backend/app/models/schema.py`.

| Table | Key | Holds |
| --- | --- | --- |
| `servers` | `server_key` | Latest known state of each server |
| `smb_shares` | `share_key` | Latest known state of each share |
| `smb_share_aces` | `ace_key` | Raw share-level ACEs, exactly as read |
| `principal_references` | `(principal_key, reference_kind, reference_key)` | Which resources name which principals |

**There are no foreign keys between them.** A batch may arrive in any order, and an ACL read
from a machine no run has described as a server is still a fact. Rejecting it would discard
evidence at exactly the moment a partial scan most needs to record what it did manage to
read. This mirrors `membership_edges`, which already references principals it may not hold.
A missing parent is reported as `null`, never hidden.

`access_mask` is `bigint`. An access mask is unsigned 32-bit, and `0xFFFFFFFF` does not fit
PostgreSQL's signed `integer` — it would land as `-1`, which is a different mask.

## Trustee keys

A share ACL is read **on a machine**, so the trustee it names is interpreted in that
machine's context. Exactly one class of SID needs that context:

- a **BUILTIN SID** (`S-1-5-32-*`) is byte-identical on every Windows computer, so
  `S-1-5-32-544` on FS01 is stored as `fs01|S-1-5-32-544`;
- **every other SID** — a domain principal, a well-known SID such as `Everyone`, an account
  issued by the machine's own SID namespace — is globally unique and keeps its global key.

Host-scoping a domain SID would split one group into one row per server that mentions it.
Failing to host-scope a BUILTIN SID would merge every server's local administrators into one
group nobody is actually in. The rule has one implementation,
`app.domain.referenced_principal_key`, which `MembershipEdge.member_key` also calls — so an
ACE and a local-group edge naming one trustee land on the same node and the membership graph
reaches the ACL.

## Absence

**Nothing in this phase can mark an object absent.** There is no delete path: a share the
newest scan did not mention keeps its row, an ACE removed from an ACL keeps its row, and a
failed run removes nothing at all. Every write is a newest-wins upsert on
`last_observed_at`, so a delayed older scan contributes only what it alone knows — an
earlier `first_observed_at` — and cannot rewrite the present.

This is not caution for its own sake. A server rebooted mid-scan, a share whose ACL the
collector lacked rights to read, and a share that was genuinely deleted all produce the same
thing: fewer observations. Only a **reconciled scope on a clean successful run** distinguishes
them, and acting on that is Phase 7's decision. An audit tool that deleted on sight would
report access as revoked while it is still in force.

## The API surface

All under `/api/v1`, all read-only.

| Route | Answers |
| --- | --- |
| `GET /servers` | Every known server, with its share count |
| `GET /servers/{server}` | One server, with provenance |
| `GET /servers/{server}/shares` | Shares published by one server |
| `GET /shares/{share}` | One share, its server, and its ACE count |
| `GET /shares/{share}/acl` | That share's raw ACL, in DACL order |
| `GET /principals/{trustee}/shares` | Shares whose ACL names a SID |

**Raw facts are labelled raw.** The ACL responses carry `kind: "raw_smb_acl"`. Effective
access needs the NTFS layer and the membership graph together and will arrive as its own
representation on its own route; a client must not be able to mistake one for the other.

### Naming a share

A share is named by its key (`fs01|finance`) or by a UNC path (`\\FS01\Finance`,
percent-encoded). Every spelling `parse_unc_path` canonicalizes is accepted — forward
slashes, extended prefixes, a trailing separator, any casing.

Two identifiers are **refused rather than guessed at**:

- `\\FS01\Finance\Reports` names a *folder*, not a share. The two have different ACLs, so
  truncating it would answer a question about the share when the caller asked about the
  folder.
- a bare host name, or a key with extra `|` segments, names nothing; picking an
  interpretation would return one share's ACL under another's name.

One spelling a URL cannot carry: `//fs01/finance`. A percent-encoded `/` is decoded before
routing, so it can never be a single path segment. The domain parser accepts it; send the
backslash form or the key.

### Naming a trustee

A bare SID asks about the SID wherever it appears; a host-scoped key (`fs02|S-1-5-32-544`)
asks about one server's principal. The bare form is deliberately **not** a 409 for a BUILTIN
SID, unlike principal lookup in the graph API: "which shares grant `S-1-5-32-544`" is a
question with one correct answer that spans servers, and the response labels each entry's
trustee individually. A name is never an identifier (ADR-0001).

### Pagination

Keyset for servers, shares, and trustee references — all are index-ordered listings that
change while they are paged, and a skipped share is a missed finding. **Offset** for a share
ACL, because DACL order is the answer's content: keyset paging would need a unique monotonic
key and would force the entries into alphabetical `ace_key` order, which means nothing.
An ACL is a handful of entries, so the trade the listings make does not apply.

Cursors are endpoint-specific; a foreign or malformed one is a 422, because silently
restarting at page one would make a client's second page look like a complete result set.
