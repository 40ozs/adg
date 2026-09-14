# ADG permission domain model

**Status:** accepted (Phase 0A)
**Implemented by:** `backend/app/domain/`
**Decisions:** [ADR-0001](../decisions/0001-sid-as-identity.md),
[ADR-0002](../decisions/0002-graph-preserving-membership.md),
[ADR-0003](../decisions/0003-raw-observations-vs-derived-state.md),
[ADR-0004](../decisions/0004-read-only-collector-posture.md)

This document fixes the meaning of every term ADG uses for identity, resources,
permissions, membership, and provenance. Later phases may add entities; they may not
redefine the ones here without a versioned migration and a new ADR.

The model exists to answer one question with evidence:

> **Who can access what, and exactly why?**

Everything below is shaped by that sentence. "Who" requires stable identity. "What"
requires stable resource identity. "Why" requires that the *path* to access — group edges
and specific ACEs — survives in storage rather than being collapsed into a yes/no.

---

## 1. Principles

1. **The SID is the identity.** Names, UPNs, sAMAccountNames, and distinguished names are
   metadata. See §3 and ADR-0001.
2. **Raw observations and derived state are different kinds of thing.** An ACE is a fact
   about a security descriptor; "Alice can read this folder" is a conclusion computed from
   many facts. They are stored in different places and never converted into one another.
   See §8 and ADR-0003.
3. **Membership is a graph of edges.** It is never stored pre-expanded. See §6 and ADR-0002.
4. **The two authorization layers stay separate.** SMB share permissions and NTFS
   permissions are collected, stored, and reasoned about independently; they are combined
   only at evaluation time. See §7.
5. **Anomalies are recorded, not discarded.** Unresolvable SIDs, membership cycles, NULL
   DACLs, and non-canonical ACL ordering are findings. A model that refuses to store them
   hides exactly what an auditor needs to see.
6. **Nothing is inferred that was not observed.** If a source did not report a value, the
   field is `None` — never a default that reads as fact. Tri-state properties
   (`True`/`False`/`None`) are used wherever "unknown" must not be reported as "no".

---

## 2. Entity overview

| Entity | Type | Identity key | Section |
| --- | --- | --- | --- |
| Domain / forest identifier | `DomainIdentifier` | `domain_sid` | §4 |
| Principal (base) | `Principal` | `sid` | §5 |
| User | `User` | `sid` | §5.1 |
| Domain group | `DomainGroup` | `sid` | §5.2 |
| Local group | `LocalGroup` | `host_key` + `sid` | §5.3 |
| Computer / service identity | `ComputerIdentity` | `sid` | §5.4 |
| Well-known principal | `WellKnownPrincipal` | `sid` | §5.5 |
| Unresolved / deleted SID | `UnresolvedPrincipal` | `sid` | §5.6 |
| Server | `Server` | case-folded `name` | §9.1 |
| SMB share | `SmbShare` | `server_key` + case-folded `name` | §9.2 |
| Directory / resource | `DirectoryResource` | canonical UNC path, case-folded | §9.3 |
| SMB ACE | `SmbShareAce` | (share, trustee, type, order) | §7.1 |
| NTFS ACE | `NtfsAce` | (resource, trustee, type, mask, flags, order) | §7.2 |
| Security descriptor facts | `SecurityDescriptorFacts` | the resource it describes | §7.3 |
| Membership edge | `MembershipEdge` | group key → member key + kind | §6 |
| Observation / source | `Observation`, `ObservationSource` | (scan run, fact) | §10 |
| Scan run | `ScanRun` | `run_id` | §10 |

Every type is a frozen dataclass that validates on construction and raises
`DomainValidationError` — with the offending value and field — when an invariant is
violated. A fact that cannot be represented correctly is never represented approximately.

---

## 3. SID normalization and uniqueness

### 3.1 Canonical form

A SID is stored as its string form: `S-R-IA[-SA...]` — revision, identifier authority, and
up to 15 sub-authorities. `Sid` canonicalizes on construction:

| Input | Canonical | Why |
| --- | --- | --- |
| `s-1-5-18` | `S-1-5-18` | Case of the literal `S` varies by API. |
| `S-1-05-21-1-2-3-0512` | `S-1-5-21-1-2-3-512` | Leading zeros appear in hand-edited data. |
| `  S-1-1-0  ` | `S-1-1-0` | Whitespace from text-based sources. |
| `S-1-0x000000000005-32-544` | `S-1-5-32-544` | Windows prints sub-2³² authorities in decimal. |
| `S-1-281474976710655-1` | `S-1-0xFFFFFFFFFFFF-1` | Windows prints larger authorities in hex. |

Validation rejects: a revision other than 1, a non-numeric component, more than 15
sub-authorities, a sub-authority above 2³²−1, an identifier authority above 2⁴⁸−1, and
anything that is not a SID at all (`CORP\alice`, a UPN, an empty string).

`Sid.try_parse` returns `None` instead of raising, for sources whose field may hold either
a SID or a name.

### 3.2 Uniqueness

The canonical string is the primary key for a principal everywhere in ADG: database keys,
API paths, join columns, and deduplication.

Two consequences that later phases must respect:

* **Names are never keys.** Two principals may share a display name; one principal may
  change names between scans. Neither affects identity.
* **A domain-issued SID is globally unique, but a BUILTIN SID is not.** `S-1-5-32-544` is
  `BUILTIN\Administrators` on *every* Windows computer. `LocalGroup` and local membership
  edges therefore key on `host_key|sid`. Merging them across servers would invent access
  that nobody has. This is the single most dangerous shortcut available in this domain.

### 3.3 Structure

`Sid` exposes structure rather than making callers parse strings:
`identifier_authority`, `sub_authorities`, `rid`, `domain_sid` (the issuing domain or
machine, for `S-1-5-21-x-y-z-RID`), `is_domain_sid`, `is_domain_relative`, `is_builtin`,
`is_well_known`, and `well_known_name`.

`WELL_KNOWN_SID_NAMES` and `WELL_KNOWN_RID_NAMES` provide conventional names for display
only. A well-known RID (512 = Domain Admins) does not make a SID well-known: `S-1-5-21-…-512`
is specific to one domain.

---

## 4. Domain and forest identifier

`DomainIdentifier` records a domain or a standalone machine's account database.

* **Identity:** `domain_sid`, which must be a domain SID (`S-1-5-21-x-y-z`, no RID).
* **Metadata:** `dns_name`, `netbios_name`, `forest_dns_name`, `kind`
  (`active_directory` / `local_machine` / `unknown`).
* **Forest:** `forest_root_domain_sid`; `is_forest_root` returns `None` when the forest root
  is unknown, because "unknown" must not render as "no".

NetBIOS names are not unique across trusted forests, and DNS names can be re-pointed:
neither may be a key.

---

## 5. Principals

`Principal` is the base: `sid`, `kind`, and optional `display_name`, `sam_account_name`,
`domain_sid`. `effective_domain_sid` returns the recorded domain if present, otherwise the
domain derived from the SID. A recorded domain that contradicts the SID is rejected — the
SID wins, always.

`PrincipalKind` values: `user`, `domain_group`, `local_group`, `computer`,
`managed_service_account`, `well_known`, `foreign_security_principal`, `unresolved`.

### 5.1 User

`User` adds `user_principal_name`, `distinguished_name`, `enabled`, `is_deleted`. A
disabled account still holds its ACEs and memberships; `enabled` is a risk signal, not an
identity property.

### 5.2 Domain group

`DomainGroup` adds `scope` (`domain_local`, `global`, `universal`, `builtin_local`,
`unknown`) and `group_type` (`security`, `distribution`, `unknown`).

`grants_access` returns `False` for a distribution group, `True` for a security group, and
`None` when the type is unknown. Scope matters for Phase 1: it constrains which nestings
are possible and where a group can be used across a trust.

### 5.3 Local group

`LocalGroup` lives in one computer's SAM database and **requires** `host_key`. Its identity
key is `host_key|sid` (see §3.2). This is how ADG represents the most common real-world
escalation path: a domain group nested into `BUILTIN\Administrators` on a file server.

### 5.4 Computer and service identity

`ComputerIdentity` covers computer accounts and managed service accounts, which appear on
ACLs and inside groups like any other principal.

### 5.5 Well-known principal

`WellKnownPrincipal` covers Everyone, Authenticated Users, SYSTEM, CREATOR OWNER, NETWORK,
and similar. Their SIDs are universal; their *meaning on an ACL* is contextual — CREATOR
OWNER is substituted at inheritance time, and Everyone excludes anonymous logon under
default policy. That interpretation belongs to the Phase 4 engine, not to storage.

### 5.6 Unresolved and deleted SIDs

`UnresolvedPrincipal` records a SID that no authority resolved, with a `reason`
(`deleted`, `untrusted_domain`, `lookup_failed`, `unknown`).

An orphaned SID on an ACL is a normal, storable observation and often a finding in its own
right. Two rules protect it:

* it may **not** carry a `display_name`; a previously observed name goes in
  `last_known_name`, so that a stale name can never be mistaken for a current resolution;
* it is never dropped from an ACL. Discarding it would make ADG report an ACL that has
  fewer entries than the real one.

---

## 6. Membership as an edge graph

`MembershipEdge` is a directed edge — `member_sid` is a member of `group_sid` — carrying
`kind`, optional `host_key`, optional `member_kind`, and
`is_foreign_security_principal`.

### 6.1 Edge kinds

| Kind | Meaning |
| --- | --- |
| `directory_group_member` | `member` / `memberOf` in a directory: user→group or group→group. |
| `primary_group` | Membership via `primaryGroupID`. |
| `local_group_member` | Membership in a group in one computer's local database. Requires `host_key`. |
| `well_known_implicit` | Implicit membership (Authenticated Users, Everyone) when a source states it. |

`primary_group` exists because it is a classic collection bug: a user's primary group —
usually `Domain Users`, which appears on a great many ACLs — does **not** appear in the
group's `member` attribute. A collector that reads only `member` silently under-reports
access for every user in the domain.

### 6.2 What the graph must survive

* **Nesting.** Groups contain groups, arbitrarily deep, within and across domains.
  `crosses_domain` reports `True`/`False`/`None` (`None` when either endpoint is not
  domain-relative).
* **Local groups.** Local edges are host-scoped; a domain member inside a local group keeps
  its global key, because the member is a domain principal regardless of where it was
  nested.
* **Foreign security principals.** A member from a trusted forest may be observable only as
  a SID. The edge is stored with `is_foreign_security_principal=True` and an unresolved
  member — a real edge to a principal this domain cannot describe.
* **Cycles and malformed graphs.** Cycles are recorded, never rejected: refusing the
  observation would hide the anomaly. Only a **self-edge** (a group as a direct member of
  itself) is rejected, because Windows does not create one and observing it indicates a
  collector defect.

### 6.3 Why edges and not expanded sets

Storing "these 480 users can reach Finance-RW" answers *who* and destroys *why*. It also
goes stale the moment one nested group changes, cannot be diffed usefully, and cannot
support simulation ("what if we removed this one edge?"). One row per observed edge keeps
the whole explanation reconstructible:

```text
alice → Finance-Team → Finance-RW → ACE(Allow, Modify) on \\FS01\Finance\Reports
```

Expansion is a query concern (Phase 1) and a caching concern (Phase 4), never a storage
format. See ADR-0002.

---

## 7. Raw permission facts: two separate layers

Access to a file over SMB is limited by **both** the share ACL and the NTFS ACL; the
effective right is the intersection. A share granting Full Control does not widen NTFS, and
NTFS Full Control is irrelevant through a share that grants Read. Local access (console,
service, a scheduled task) bypasses the share layer entirely.

The layers are modeled as two distinct types so that no code can mistake one for the other,
and so that ADG can explain *which* layer limits a given user.

### 7.1 SMB share ACE

`SmbShareAce` = `trustee_sid`, `ace_type` (allow/deny), and **exactly one** of:

* `access_mask` — from the security-descriptor APIs; or
* `permission` — `read` / `change` / `full`, the three levels reported by the SMB cmdlets.

Recording both would mean inventing a value the source did not provide. The conventional
mask for each level is documented in `SHARE_PERMISSION_MASKS` for the Phase 4 algebra, but
no conversion is applied to an observation.

### 7.2 NTFS ACE

`NtfsAce` = `trustee_sid`, `ace_type`, `access_mask` (raw, unsigned 32-bit, generic bits
included), `flags`, `source`, `inherited_from`, `order_index`.

Derived views, none of which alter the stored mask:

| Property | Meaning |
| --- | --- |
| `rights` | The mask as `NtfsRight` flags. |
| `unrecognized_bits` | Mask bits matching no known right — kept visible, never dropped. |
| `is_inherited` | Whether the ACE came from an ancestor. |
| `uses_generic_rights` | Whether the mask carries `GENERIC_*` bits. |
| `applies_to_this_object` | `False` for `INHERIT_ONLY` ACEs. |
| `is_inheritable` | Whether children receive it. |

**Invariant:** `source` and the `INHERITED_ACE` flag must agree, and an explicit ACE may not
record an inheritance origin. A collector must report what the descriptor says.

### 7.3 Descriptor-level facts

`SecurityDescriptorFacts` records `owner_sid`, `group_sid`, `dacl_present`,
`dacl_protected`, and `ace_count`, because the ACE list alone is ambiguous:

* **NULL DACL** (`dacl_present=False`) means **everyone has full access** — always a
  finding. A descriptor with no DACL may not carry ACEs.
* **Empty DACL** (`dacl_present=True, ace_count=0`) means **nobody has access** through the
  DACL.

Those are opposite meanings that collapse into the same empty list if only ACEs are stored.
`dacl_protected` records that the object blocks inheritance from its parent.

Ownership matters for the same reason: an owner holds implicit `READ_CONTROL` and
`WRITE_DAC` regardless of the DACL, so it can grant itself anything.

---

## 8. Windows authorization concerns the implementation must account for

This section is a checklist for Phases 3 and 4. The domain model must be able to *represent*
each item; the engine must *evaluate* it.

The rights algebra that evaluates the mask-level items — generic mapping, share/NTFS
intersection, Deny masking, and display categories — is specified in
[rights-model.md](rights-model.md) and decided by
[ADR-0005](../decisions/0005-internal-rights-representation.md).

| Concern | Representation | What the engine must do later |
| --- | --- | --- |
| **Allow and Deny** | `AceType` | Apply real DACL evaluation order — an explicit Deny normally precedes Allow, but order is a property of the ACL, and a non-canonical ACL must be evaluated as written and flagged. |
| **Explicit vs inherited** | `AceSource`, `AceFlag.INHERITED`, `inherited_from` | Explain *where* a grant is administered. In canonical order explicit ACEs precede inherited ones. |
| **Inheritance flags** | `OBJECT_INHERIT`, `CONTAINER_INHERIT` | Decide whether an ACE reaches child files, child directories, or both. |
| **Propagation flags** | `NO_PROPAGATE_INHERIT`, `INHERIT_ONLY` | Stop propagation after one level; exclude `INHERIT_ONLY` ACEs from the object holding them. |
| **Protected DACLs** | `SecurityDescriptorFacts.dacl_protected` | Stop walking inheritance upward at a protected object. |
| **Generic rights** | `GENERIC_*` bits preserved in the mask | Apply the file-system generic mapping at evaluation time, never at collection time. |
| **Unrecognized mask bits** | `unrecognized_bits` | Report rather than silently ignore. |
| **Share rights** | `SmbShareAce` | Intersect with NTFS for remote access; omit for local access. |
| **Nested groups** | `MembershipEdge` graph | Expand transitively, tolerate cycles, and keep the path for explanation. |
| **Primary groups** | `MembershipEdgeKind.PRIMARY_GROUP` | Include; they are absent from `member`. |
| **Local groups** | `LocalGroup`, host-scoped edges | Expand only on the server that owns them. |
| **Distribution groups** | `DomainGroup.grants_access` | Grant nothing, even when present on an ACL. |
| **Unresolved SIDs** | `UnresolvedPrincipal` | Keep on the ACL and report; never drop, never guess a name. |
| **Well-known principals** | `WellKnownPrincipal` | Interpret contextually (CREATOR OWNER substitution, Everyone vs anonymous). |
| **Ownership** | `SecurityDescriptorFacts.owner_sid` | Account for implicit owner rights. |
| **Deleted accounts** | `is_deleted`, `UnresolvedReason.DELETED` | Distinguish "no access" from "account gone". |

Deliberately **not** modeled in Phase 0A: SACL/audit entries (they govern logging, not
access), conditional ACEs and central access policies, Dynamic Access Control claims, and
object-type ACEs (an AD concept, not a file-system one). Each needs its own decision when a
phase requires it.

---

## 9. Resource identity and path normalization

### 9.1 Server

`Server` is identified by the case-folded name it was collected under. `dns_host_name`,
`netbios_name`, `computer_sid`, and `domain_sid` are recorded when known so that a later
phase can merge aliases **on evidence** — a shared computer SID — rather than by guessing
that `FS01` and `fs01.corp.example.com` are the same machine.

### 9.2 SMB share

`SmbShare` is identified by `server_key|name`, case-folded. A share is a *publication* of a
directory, not the directory itself: two shares can expose the same local path with
different share ACLs, and a share can be re-pointed without the directory changing. Keeping
them separate is what lets ADG say that access differs depending on which share is used.

`is_hidden` (trailing `$`), `is_administrative` (`C$`, `ADMIN$`, `IPC$`), and
`carries_file_permissions` (disk shares only) are derived.

### 9.3 Directory

`DirectoryResource` is identified by its canonical UNC path, case-folded. It also records
`local_path`, `share_key`, `inheritance_enabled`, `is_acl_boundary`, and
`depth_from_share_root`.

ADG does not inventory every directory — an estate has millions of directories and only
thousands of distinct permission decisions. It records **ACL boundaries**: directories whose
DACL differs from the parent's. A directory that blocks inheritance is by definition a
boundary, and the model rejects any attempt to record otherwise.

### 9.4 Path normalization rules

`parse_unc_path`, `parse_local_path`, and `parse_windows_path` produce `UncPath` /
`LocalPath`, applying:

1. `/` → `\`.
2. Extended-length prefixes removed: `\\?\UNC\server\share` → `\\server\share`, `\\?\C:\` → `C:\`.
3. Repeated separators collapsed (the leading `\\` of a UNC path preserved).
4. `.` segments removed; trailing separators removed.
5. Surrounding whitespace removed.
6. Drive letters upper-cased.
7. Components validated: no `<>:"/\|?*`, no control characters, no empty segment.

Rejected rather than guessed:

* **`..` segments** — resolving them requires the file system; a wrong guess silently
  merges two different directories.
* **Relative paths and bare share names** (`\\FS01`) — not resource identities.
* **Device paths** — not file-system resources.

**Case:** `value` preserves what was observed, for display. `comparison_key` is case-folded
and is what joins and deduplicates. `UncPath` and `LocalPath` also *compare and hash*
case-insensitively, so `==` can never disagree with `comparison_key` — without that, a share
recorded as `fs01` and a directory recorded as `FS01` would be two resources whose
permissions appeared to differ. Windows file systems are case-insensitive by default;
per-directory case sensitivity exists on modern Windows and is a known gap (§11).

`UncPath` also provides `segments`, `parent`, `child()`, `share_root`, and `is_share_root`,
so that boundary walking never re-implements string surgery.

---

## 10. Observations, sources, and scan runs

Every stored fact is an observation: something a named collector saw, on a named host, at a
known instant.

* **`ObservationSource`** — `collector` (`active_directory`, `smb`, `ntfs`,
  `local_groups`), `collector_host`, `method`, `collector_version`, `target`. `method` names
  the concrete mechanism (`Get-SmbShareAccess`, `System.IO.DirectoryInfo.GetAccessControl`)
  because different mechanisms report different detail — share permissions as levels versus
  masks, for instance. The method is evidence, not a label.
* **`ScanRun`** — `run_id`, `source`, `started_at`, `completed_at`, `status`,
  `observation_count`, `error_count`.
* **`Observation[FactT]`** — a raw fact plus `observed_at`, `scan_run_id`, and `source`.

Invariants:

| Invariant | Why |
| --- | --- |
| Timestamps are timezone-aware and normalized to UTC. | A naive timestamp from a server in another time zone reorders history. |
| A terminal run records `completed_at`; a running one must not. | Otherwise the validity window of its observations is unknown. |
| `completed_at >= started_at`. | Negative durations corrupt ordering. |
| A run with errors is `partial` or `failed`, never `succeeded`. | Reporting complete coverage that was not achieved understates access. |

`ScanStatus.yields_usable_observations` distinguishes a partial run (usable facts,
incomplete coverage) from a failed one. This matters for Phase 7: a fact missing from a
later **successful** run of the same scope is a removal, while a fact missing from a failed
run is simply unknown.

**Derived results are never wrapped in `Observation`.** That type means "a collector saw
this". Effective-access results, risk findings, and simulation outputs are computed and
carry their own provenance — which inputs, which engine version. See ADR-0003.

---

## 11. Known gaps

Recorded so that later phases fix them deliberately rather than discovering them as bugs.

1. **Per-directory case sensitivity.** Windows can mark a directory case-sensitive;
   `comparison_key` assumes the default case-insensitive behavior.
2. **Trailing dots and spaces.** Windows strips them from path components; ADG does not, so
   `Reports.` and `Reports` would be two rows.
3. **DFS namespaces.** A DFS path resolves to a target server and share; ADG currently
   treats the namespace path as an ordinary UNC path.
4. **Host aliases.** `FS01` and `fs01.corp.example.com` are separate `Server` rows until a
   later phase merges them on a shared computer SID.
5. **Short (8.3) names** are not normalized to long names.
6. **SACL, conditional ACEs, central access policies, and DAC claims** are not modeled.
7. **Cross-forest SID history** (`sIDHistory`) is not modeled; an ACE naming a historical
   SID will resolve as unresolved until a phase adds it.
8. **Trusts** are not modeled as entities; cross-domain edges are observable through
   `crosses_domain` but the trust itself is not recorded.

---

## 12. Worked example: "why does Alice have access?"

The stored facts needed to answer the question, and nothing else:

```text
Principals      User(S-1-5-21-…-1104, "Alice Smith")
                DomainGroup(S-1-5-21-…-1201, "Finance-Team", global, security)
                DomainGroup(S-1-5-21-…-1202, "Finance-RW", domain_local, security)

Edges           S-1-5-21-…-1104 → S-1-5-21-…-1201   (directory_group_member)
                S-1-5-21-…-1201 → S-1-5-21-…-1202   (directory_group_member)

Resource        Server("FS01")
                SmbShare("fs01", "Finance", D:\Shares\Finance)
                DirectoryResource(\\FS01\Finance\Reports, acl boundary)

Share layer     SmbShareAce(S-1-1-0 Everyone, allow, permission=full)
NTFS layer      NtfsAce(S-1-5-21-…-1202, allow, 0x001301BF, CI|OI, explicit)

Provenance      ScanRun(ntfs, COLLECTOR01, 2026-09-14T08:00Z, succeeded)
```

From these rows alone, the Phase 4 engine can compute the answer and the Phase 5 explanation
engine can render the path — `Alice → Finance-Team → Finance-RW → Allow Modify on
\\FS01\Finance\Reports`, limited by the share layer to Full — without any derived state
having been stored as though it were an observation.
