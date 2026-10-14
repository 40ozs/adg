r"""A directory estate with the shapes a directory-access summary has to get right.

Built for ``tests/db/test_directory_access_api.py`` and the directory-access benchmark, at
any size. Every directory carries the DACL Windows would give it — inherited entries copied
down with ``INHERITED`` set, exactly as a collector stores them — so the reference index
names the subject's groups on every directory beneath a grant, which is what makes the flat
listing long and the summary worth having.

Per share, under ``\\FSnn\Dept##``:

* the share root grants the subject nothing (administrators and an unrelated group only);
* a few top-level folders grant ``Dept-RW`` Modify — each is an **access root**;
* inside those runs, some folders are **protected** and name only administrators — access
  **ends** there, and nothing on their ACL names the subject;
* below some of those, a folder grants ``Dept-RO`` Read & execute again — access **resumes**;
* some folders add an explicit Full control for the subject — the rights **widen**;
* some folders are protected but re-grant ``Dept-RW`` Modify — **inheritance is broken**
  with the same rights;
* some folders carry a Deny for ``Dept-RW`` — access is **denied**;
* some folders add an entry for an unrelated group — an ACL boundary that changes nothing
  for the subject.

The generator also records, from its own construction rules, which directories the subject
should reach and with what mask (:attr:`DirectoryEstate.expected`). That is an independent
statement of intent the engine-driven summary is checked against — not a second
implementation of the engine, since the rules here know which entries they wrote.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.contracts.v1 import keys
from app.contracts.v1.common import MAX_BATCH_OBSERVATIONS
from app.domain import AceType, AclAceFacts, MembershipEdgeKind, Sid, acl_hash

DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"
SUBJECT = f"{DOMAIN_SID}-1104"
GROUP_RW = f"{DOMAIN_SID}-1301"
GROUP_RO = f"{DOMAIN_SID}-1302"
UNRELATED = f"{DOMAIN_SID}-1303"
ADMINISTRATORS = "S-1-5-32-544"

FULL = 0x001F01FF
MODIFY = 0x001301BF
READ_EXECUTE = 0x001200A9

INHERIT = 0x03  # OBJECT_INHERIT | CONTAINER_INHERIT
INHERITED = 0x10

__all__ = [
    "FULL",
    "GROUP_RO",
    "GROUP_RW",
    "MODIFY",
    "READ_EXECUTE",
    "SUBJECT",
    "DirectoryEstate",
    "build_directory_estate",
]


@dataclass(frozen=True, slots=True)
class Entry:
    trustee: str
    ace_type: str
    mask: int
    flags: int


@dataclass
class DirectoryEstate:
    """The ingestion document and what its construction rules say the subject reaches."""

    document: dict[str, Any]
    directories: list[str]
    expected: dict[str, int] = field(default_factory=dict)
    """Canonical key → effective NTFS mask, for every directory the subject should reach."""
    denied: set[str] = field(default_factory=set)
    protected_without_subject: set[str] = field(default_factory=set)
    shares: list[str] = field(default_factory=list)

    @property
    def observation_count(self) -> int:
        return sum(len(batch["observations"]) for batch in self.document["batches"])


def build_directory_estate(
    *, shares: int = 2, per_share: int = 60, seed: int = 11, deep: bool = True
) -> DirectoryEstate:
    """A deterministic estate of ``shares`` shares of about ``per_share`` directories each."""
    rng = random.Random(seed)
    run_id = str(uuid.uuid4())
    observations: list[dict[str, Any]] = []
    counter = iter(range(1, 10_000_000))

    def add(kind: str, **fields: Any) -> None:
        index = next(counter)
        observations.append(
            {
                "schema_version": "1.3",
                "kind": kind,
                "run_id": run_id,
                "observed_at": f"2026-10-01T08:{index % 60:02d}:00Z",
                **fields,
            }
        )

    servers = sorted({f"FS{number // 4 + 1:02d}" for number in range(shares)})
    for server in servers:
        add("server", source_key=keys.server_key(server), name=server)

    for sid, kind in (
        (SUBJECT, "user"),
        (GROUP_RW, "domain_group"),
        (GROUP_RO, "domain_group"),
        (UNRELATED, "domain_group"),
    ):
        add("principal", source_key=f"principal|{sid}", sid=sid, principal_kind=kind)
    for group in (GROUP_RW, GROUP_RO):
        add(
            "membership_edge",
            source_key=keys.membership_key(
                Sid(group), Sid(SUBJECT), MembershipEdgeKind.DIRECTORY_GROUP_MEMBER
            ),
            group_sid=group,
            member_sid=SUBJECT,
            edge_kind=MembershipEdgeKind.DIRECTORY_GROUP_MEMBER.value,
        )

    estate = DirectoryEstate(document={}, directories=[])
    for number in range(shares):
        server = f"FS{number // 4 + 1:02d}"
        name = f"Dept{number:02d}"
        root = f"\\\\{server}\\{name}"
        estate.shares.append(root)
        add(
            "smb_share",
            source_key=keys.share_key(server, name),
            server_name=server,
            share_name=name,
        )
        for order, (trustee, permission) in enumerate(
            ((GROUP_RW, "full"), (GROUP_RO, "full"), (ADMINISTRATORS, "full"))
        ):
            add(
                "smb_ace",
                source_key=keys.smb_ace_key(server, name, Sid(trustee), "allow", None, permission),
                server_name=server,
                share_name=name,
                trustee_sid=trustee,
                ace_type="allow",
                permission=permission,
                order_index=order,
            )
        _tree(estate, add, rng, root, per_share, deep)

    estate.document = {
        "start": {
            "schema_version": "1.3",
            "run_id": run_id,
            "source": {
                "collector": "ntfs",
                "collector_host": "COLLECTOR01",
                "method": "DirectorySecurity.GetSecurityDescriptorBinaryForm",
            },
            "started_at": "2026-10-01T08:00:00Z",
            "scopes": [{"kind": "directory_tree", "key": root.lower()} for root in estate.shares],
            "incremental": True,
        },
        "batches": _batched(run_id, observations),
        "completion": {
            "schema_version": "1.3",
            "run_id": run_id,
            "status": "succeeded",
            "completed_at": "2026-10-01T09:00:00Z",
            "batch_count": max(1, -(-len(observations) // MAX_BATCH_OBSERVATIONS)),
            "observation_count": len(observations),
            "error_count": 0,
            "errors": [],
            "reconciled_scopes": [],
        },
    }
    return estate


def _tree(
    estate: DirectoryEstate,
    add: Any,
    rng: random.Random,
    root: str,
    per_share: int,
    deep: bool,
) -> None:
    """One share's directories, each written with its full inherited DACL."""
    explicit_root = [
        Entry(ADMINISTRATORS, "allow", FULL, INHERIT),
        Entry(UNRELATED, "allow", MODIFY, INHERIT),
    ]
    # (path, explicit entries, protected, inherited entries handed down from the parent)
    pending: list[tuple[str, list[Entry], bool, list[Entry]]] = [(root, explicit_root, False, [])]
    written = 0
    while pending and written < per_share:
        path, explicit, protected, inherited = pending.pop(0)
        dacl = _ordered(explicit, [] if protected else inherited)
        _write(add, path, dacl, protected, root)
        estate.directories.append(path)
        written += 1
        _expect(estate, path, dacl, protected)

        depth = path.count("\\") - 3
        if depth >= (5 if deep else 2):
            continue
        handed_down = [
            Entry(item.trustee, item.ace_type, item.mask, INHERITED | INHERIT)
            for item in dacl
            if item.flags & INHERIT
        ]
        width = 4 if depth == 0 else rng.randint(1, 4)
        prefix = "Team" if depth == 0 else "Sub"
        for index in range(width):
            explicit_child, protected_child = _choose(rng, depth, index, explicit)
            pending.append(
                (f"{path}\\{prefix}{index:02d}", explicit_child, protected_child, handed_down)
            )


def _choose(
    rng: random.Random, depth: int, index: int, parent_explicit: list[Entry]
) -> tuple[list[Entry], bool]:
    """Which shape one child takes. Deterministic for a seed."""
    if depth == 0:
        # Top-level teams: three of four grant Dept-RW Modify; the fourth grants nothing.
        if index < 3:
            return [Entry(GROUP_RW, "allow", MODIFY, INHERIT)], False
        return [], False
    roll = rng.random()
    protected_without_subject = (
        any(item.trustee == ADMINISTRATORS and item.flags == INHERIT for item in parent_explicit)
        and len(parent_explicit) == 1
    )
    if protected_without_subject and roll < 0.5:
        # Directly below a locked folder: access resumes, read-only.
        return [Entry(GROUP_RO, "allow", READ_EXECUTE, INHERIT)], False
    if roll < 0.06:
        return [Entry(ADMINISTRATORS, "allow", FULL, INHERIT)], True  # access ends
    if roll < 0.09:
        return [Entry(SUBJECT, "allow", FULL, INHERIT)], False  # rights widen
    if roll < 0.11:
        return [
            Entry(ADMINISTRATORS, "allow", FULL, INHERIT),
            Entry(GROUP_RW, "allow", MODIFY, INHERIT),
        ], True  # inheritance broken, same rights
    if roll < 0.13:
        return [Entry(GROUP_RW, "deny", FULL, INHERIT)], False  # denied
    if roll < 0.16:
        return [Entry(UNRELATED, "allow", READ_EXECUTE, INHERIT)], False  # boundary, no change
    return [], False


def _ordered(explicit: list[Entry], inherited: list[Entry]) -> list[Entry]:
    """Canonical DACL order: explicit deny, explicit allow, inherited deny, inherited allow."""
    return [
        *[item for item in explicit if item.ace_type == "deny"],
        *[item for item in explicit if item.ace_type == "allow"],
        *[item for item in inherited if item.ace_type == "deny"],
        *[item for item in inherited if item.ace_type == "allow"],
    ]


def _expect(estate: DirectoryEstate, path: str, dacl: list[Entry], protected: bool) -> None:
    """What the construction rules say the subject holds here (an oracle, not the engine).

    The subject's token holds itself and both groups. The entries are walked in the order
    written, each right decided by the first entry naming it — the rule Windows applies, and
    the reason an explicit Allow on a child outranks a Deny the child merely inherited.
    """
    mine = {SUBJECT, GROUP_RW, GROUP_RO}
    key = path.casefold()
    allowed = 0
    denied = 0
    for item in dacl:
        if item.trustee not in mine:
            continue
        if item.ace_type == "allow":
            allowed |= item.mask & ~denied
        else:
            denied |= item.mask & ~allowed
    if allowed:
        estate.expected[key] = allowed
    elif denied:
        estate.denied.add(key)
    elif protected:
        estate.protected_without_subject.add(key)


def _write(add: Any, path: str, dacl: list[Entry], protected: bool, root: str) -> None:
    facts = [
        AclAceFacts(
            trustee_sid=item.trustee,
            ace_type=AceType(item.ace_type),
            access_mask=item.mask,
            ace_flags=item.flags,
            order_index=order,
        )
        for order, item in enumerate(dacl)
    ]
    is_root = path == root
    has_explicit = any(not item.flags & INHERITED for item in dacl)
    reason = (
        "share_root"
        if is_root
        else "protected_dacl"
        if protected
        else "acl_differs_from_parent"
        if has_explicit
        else None
    )
    add(
        "ntfs_resource",
        source_key=keys.ntfs_resource_key(path),
        path=path,
        dacl_present=True,
        dacl_protected=protected,
        ace_count=len(dacl),
        acl_hash=acl_hash(dacl_present=True, dacl_protected=protected, aces=facts),
        is_acl_boundary=reason is not None,
        boundary_reason=reason,
        depth_from_share_root=path.count("\\") - 3,
    )
    for order, item in enumerate(dacl):
        add(
            "ntfs_ace",
            source_key=keys.ntfs_ace_key(
                path, Sid(item.trustee), item.ace_type, item.mask, item.flags
            ),
            path=path,
            trustee_sid=item.trustee,
            ace_type=item.ace_type,
            access_mask=item.mask,
            ace_flags=item.flags,
            source="inherited" if item.flags & INHERITED else "explicit",
            order_index=order,
        )


def _batched(run_id: str, observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks = [
        observations[start : start + MAX_BATCH_OBSERVATIONS]
        for start in range(0, len(observations), MAX_BATCH_OBSERVATIONS)
    ]
    return [
        {
            "schema_version": "1.3",
            "run_id": run_id,
            "batch_id": str(uuid.uuid4()),
            "sequence": sequence,
            "is_final": sequence == len(chunks),
            "observations": chunk,
        }
        for sequence, chunk in enumerate(chunks, start=1)
    ]
