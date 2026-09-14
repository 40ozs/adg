# Resource inventory: servers, shares, directories, and their raw ACLs

How ADG stores what a share scan and a file-system scan observed, and what the resource APIs
will and will not claim. The membership half of the picture is in
[membership-graph.md](membership-graph.md); the domain types are in
[permission-domain-model.md](permission-domain-model.md); the DACL digest is specified in
[ntfs-acl-normalization.md](ntfs-acl-normalization.md), and how a tree scan decides where
permissions change in [ntfs-acl-boundaries.md](ntfs-acl-boundaries.md).

**The two authorization layers never merge here.** Remote access over SMB is limited by the
share ACL *and* the NTFS ACL; access at the console, or from a service on the box, is limited
only by the NTFS one. They are separate observations, separate tables, and separate routes,
so an auditor can see which layer is doing the restricting. Combining them is effective
access - a different claim, computed by the Phase 4 engine and reported as its own
representation.

## What a resource is

Three things, each identified by the string the domain layer produces for it.

| Object | Key | Domain source |
| --- | --- | --- |
| Server | `fs01` | `Server.identity_key` — the case-folded name it was collected under |
| Share | `fs01\|finance` | `SmbShare.identity_key` — server plus share name, case-folded |
| Share ACE | `fs01\|finance\|S-1-1-0\|allow\|read` | `SmbShareAce.identity_key(share_key)` |
| Directory | `\\fs01\finance` | `DirectoryResource.identity_key` - the case-folded canonical UNC path |
| NTFS ACE | `\\fs01\finance\|S-1-1-0\|allow\|0x001301bf\|0x03` | `NtfsAce.identity_key(resource_key)` |

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

A **file-system resource is identified by its canonical UNC path**, case-folded. A local
path such as `D:\Shares\Finance` names nothing on its own - it does not say which server - so
it is recorded beside the key rather than used as one. A file uses the same key space as a
directory, because the two are the same kind of securable object addressed the same way;
`resource_kind` is what says which it is, and a share root can only ever be a directory. An **NTFS ACE** is keyed by trustee, type,
mask, and the raw flags byte: the same trustee and mask carrying `ObjectInherit` and carrying
`ContainerInherit` are two different entries, applying to different children. `order_index`
is recorded and is not part of the key, exactly as for a share ACE - but a reordered *ACL* is
reported as changed, because `acl_hash` covers evaluation order
([ADR-0008](../decisions/0008-acl-normal-form-and-hash.md)).

## What is derived, never stored

| Value | Derived from |
| --- | --- |
| `\\fs01\Finance` | `SmbShare.unc_path` |
| `is_hidden` | the share name ending in `$` |
| `is_administrative` | `ADMIN$`, `IPC$`, or a drive-letter share |
| `carries_file_permissions` | the share type being `disk` |
| `is_share_root` | the directory's path having no segments below the share |
| `grants_everyone_full_access` | `dacl_present` being false - a NULL DACL |
| `denies_everyone` | a DACL that is present and empty |
| an NTFS entry's `rights` list | the raw `access_mask`, through `NtfsRight` |
| a directory's `parent_path` | the path, minus its last segment. `null` at a share root |
| the boundary verdict the **server** reports | the parent's stored ACEs, projected onto a child of this kind |
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

Revision `0004_ntfs_resources` adds two more, and widens `principal_references`'
`reference_kind` to include `ntfs_ace`.

| Table | Key | Holds |
| --- | --- | --- |
| `ntfs_resources` | `resource_key` | File-system resources whose security descriptor ADG has read |
| `ntfs_aces` | `ace_key` | Raw NTFS ACEs, exactly as read |

Revision `0005_ntfs_boundaries` adds three columns to `ntfs_resources`, all for the question
a tree scan exists to answer.

- **`resource_kind`** - `directory` or `file`. Not decoration: it decides which inheritance
  projection a resource is compared against, and comparing a file against the container
  projection would report a boundary on every file in the estate. Not nullable, defaulted to
  `directory`, because file scanning did not exist before contract 1.3 and every existing row
  therefore has a right answer.
- **`boundary_reason`** - why `is_acl_boundary` is true, from the seven values in
  [ntfs-acl-boundaries.md](ntfs-acl-boundaries.md). On a row that is *not* a boundary, `NULL`
  is the only value and means "carrying exactly what it inherited". On one that is, `NULL`
  means the collector predates the field. `ck_ntfs_resources_reason_implies_a_boundary`
  constrains one direction only, which is what keeps contract 1.2 collectors working.
- **`parent_acl_hash`** - the parent's digest as the run that wrote this row read it. *Not*
  the value the verdict was compared against - that is the parent's projection, which the
  server recomputes - but the record of which reading was judged, without which a later
  disagreement cannot be told apart from the parent having changed in between.

`ix_ntfs_resources_boundaries` is a partial index over `(share_key, resource_key)` where
`is_acl_boundary`, because the boundaries are the small minority of rows a full walk writes
and the ones that are not boundaries are never what is being looked for.

`ntfs_resources.share_key` is the link between the layers: it is the `SmbShare.identity_key`
of the share the path sits under, derived from the path itself rather than from anything a
collector says about it. With it, one share reports its raw SMB ACL and the raw NTFS ACL of
its root as two separate answers.

Three columns on `ntfs_resources` exist because the ACE list alone is ambiguous:

- **`dacl_present`** - `false` is a NULL DACL, which grants *everyone* full access. A
  present-but-empty DACL grants *nobody* access. Both arrive as zero entries and they are
  opposite facts, so the column is not nullable and has no default: "the collector did not
  say" must never be readable as "a DACL was present".
- **`dacl_protected` / `inheritance_enabled`** - `SE_DACL_PROTECTED`: the directory refuses
  inherited entries, which is by definition a place where permissions change. A check
  constraint holds that consistent with `is_acl_boundary`.
- **`ace_count`** - what the descriptor said, kept separate from how many `ntfs_aces` rows
  exist. A shortfall means entries were read and never stored, and the API reports both
  numbers rather than reconciling them away.

**There are no foreign keys between them.** A batch may arrive in any order, and an ACL read
from a machine no run has described as a server is still a fact. Rejecting it would discard
evidence at exactly the moment a partial scan most needs to record what it did manage to
read. This mirrors `membership_edges`, which already references principals it may not hold.
A missing parent is reported as `null`, never hidden.

`access_mask` is `bigint`. An access mask is unsigned 32-bit, and `0xFFFFFFFF` does not fit
PostgreSQL's signed `integer` — it would land as `-1`, which is a different mask.

## Trustee keys

An ACL - of either layer - is read **on a machine**, so the trustee it names is interpreted
in that machine's context. For an NTFS ACE the machine is the server in the resource's own
UNC path. Exactly one class of SID needs that context:

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
| `GET /shares/{share}/acl` | That share's raw **share-level** ACL, in DACL order |
| `GET /shares/{share}/root-acl` | The raw **NTFS** ACL of the directory that share publishes |
| `GET /resources/{path}` | One resource's descriptor facts, inheritance state, and boundary verdict |
| `GET /resources/{path}/acl` | That directory's raw NTFS ACL, in evaluation order |
| `GET /principals/{trustee}/shares` | Shares whose ACL names a SID |

**Raw facts are labelled raw.** Each ACL response carries a `kind` - `raw_smb_acl` or
`raw_ntfs_acl` - so a client cannot mistake one layer for the other, or either for an access
decision. Effective access needs both layers and the membership graph together, and will
arrive as its own representation on its own route.

`/shares/{share}/acl` and `/shares/{share}/root-acl` are deliberately two routes rather than
one merged answer. A single response could not express that a share granting Full Control
sits over a directory granting Read - which is exactly what an auditor needs to see.

A share whose NTFS root no run has read reports `root_resource: null`, and asking for its
root ACL is a 404 saying so. Null means *nobody has looked*, never *nothing restricts it*:
the two layers are collected by independent runs, and reporting the second would invent
access.

### The boundary verdict is reported twice

`GET /resources/{path}` carries a `boundary` block holding the collector's claim beside the
one the server derives from the parent it holds - the same "report both, settle nothing"
shape `acl_hash` already uses, and for the same reason: they disagree when the parent changed
between the two readings, when the collector's projection is wrong, or when entries were lost
in transit, and picking a winner would bury all three.

Three of its fields are easy to misread:

- **`projected_child_acl_hash` is what the resource was compared against**, and it is not
  `parent_acl_hash`. Those two differ by construction, because Windows sets the `INHERITED`
  bit on every entry it copies down - so a comparison against the parent's own digest would
  report every directory in the estate as a boundary.
- **`computed: null` means the server reached no verdict**, because the parent has not been
  read or has a NULL DACL. Null is unknown, never "no boundary".
- **`agrees: null` means neither side compared anything.** A collector reporting `share_root`,
  `scan_root`, `parent_unreadable` or `parent_null_dacl` never made a comparison, so the
  server knowing more than it did is not the collector having been wrong.

Everything the server used is on the wire, so a client can redo the arithmetic from the two
ACL responses rather than taking the verdict on trust.

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

### Naming a directory

A directory is named by its full UNC path (`\\FS01\Finance`, percent-encoded), in the same
spellings `parse_unc_path` canonicalizes. A share **key** is refused here rather than
converted: `fs01|finance` names a share, and a share and the directory it publishes have
different ACLs. `/shares/{share}/root-acl` is the route that goes from one to the other.

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
An ACL is a handful of entries, so the trade the listings make does not apply. The same holds
for an NTFS ACL, where evaluation order is what makes a Deny meaningful.

`acl_hash.computed` is taken over the **whole** DACL regardless of paging. A digest over a
page is not a digest of the ACL, and two clients paging differently must not end up
disagreeing about one directory.

Cursors are endpoint-specific; a foreign or malformed one is a 422, because silently
restarting at page one would make a client's second page look like a complete result set.
