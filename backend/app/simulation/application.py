r"""Applying an overlay to stored rows, as a pure function.

Every function here takes the records a repository read and returns **new** records. Nothing
is mutated: the inputs are frozen dataclasses and the outputs are freshly constructed
tuples, so a simulation cannot reach back into the objects the baseline read produced even
by accident. That is half of the isolation guarantee; the other half is that nothing in
:mod:`app.simulation` opens a write transaction against a collected-state table, which
:mod:`app.simulation.store` is the only module with any reason to, and does not.

**A simulated row is marked, not disguised.** Every record these functions invent carries
``source_key = "simulated"``, the epoch as its observation timestamps, and the nil run id —
a combination no collector can produce, because a collector always reports a real run and a
real instant. Invented ACEs and edges additionally carry a ``simulated|`` prefix on their
key, so that an explanation naming one reads as hypothetical rather than as an entry
somebody could go and look at.

**Order is part of the meaning.** A DACL is evaluated in the order it is stored, so an
addition has to land somewhere specific. Left unplaced, an entry is placed canonically —
Deny ahead of every Allow, both ahead of everything inherited — which is where the Windows
ACL editor puts one. Every entry is then renumbered contiguously, because two entries
claiming one position is an ambiguity :func:`app.domain.normalize_acl` refuses outright and
:func:`app.access_engine.evaluate_acl` would resolve by accident.

**ACE edits are applied before the inheritance toggle**, so that every ``ace_key`` in an
overlay refers to an entry as the baseline holds it — the entry the operator was looking at
when they wrote the proposal. Protecting a directory and converting its inherited entries
rewrites those entries, keys and all; if the toggle ran first, a removal written against the
ACL on screen would silently match nothing.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Final, TypeVar
from uuid import UUID

from app.domain import (
    AceFlag,
    AceSource,
    AceType,
    AclAceFacts,
    AclBoundaryReason,
    Direction,
    DomainValidationError,
    GraphEdge,
    ResourceKind,
    Sid,
    acl_hash,
    ntfs_ace_identity_key,
    parse_unc_path,
    project_inherited_acl,
    referenced_principal_key,
    share_ace_identity_key,
    share_ace_right_token,
)
from app.repositories.resources import NtfsAceRecord, NtfsResourceRecord, ShareAceRecord
from app.simulation.overlay import (
    SIMULATED_ACE_PREFIX,
    SIMULATED_SOURCE_KEY,
    ChangeKind,
    InheritanceChange,
    InheritedAceDisposition,
    MembershipChange,
    NtfsAceChange,
    ShareAceChange,
    SimulationOverlay,
)

AceRecordT = TypeVar("AceRecordT", NtfsAceRecord, ShareAceRecord)

__all__ = [
    "SIMULATED_AT",
    "SIMULATED_RUN_ID",
    "canonical_position",
    "is_simulated_record",
    "overlay_edges",
    "overlay_ntfs_acl",
    "overlay_resource",
    "overlay_share_acl",
    "simulated_edge",
]

SIMULATED_RUN_ID: Final = UUID(int=0)
"""The run id every invented row reports.

The nil UUID, which no scan run can ever be: ``scan_runs`` rows are created with a generated
identifier, and nothing generates this one. A row claiming to come from run
``00000000-0000-0000-0000-000000000000`` is a row nobody collected.
"""

SIMULATED_AT: Final = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
"""The observation timestamps every invented row reports.

The epoch, deliberately, for two reasons. It is not a plausible observation time, so a
simulated record cannot be mistaken for a collected one by a reader skimming timestamps. And
it is a *constant*, which keeps these functions pure: a simulation run twice over unchanged
data produces byte-identical records, so a difference between two reports means the proposal
or the estate changed and never that the clock moved.
"""


def is_simulated_record(record: NtfsAceRecord | ShareAceRecord | NtfsResourceRecord) -> bool:
    """Whether a record was invented by an overlay rather than read from a collector."""
    return record.source_key == SIMULATED_SOURCE_KEY


# --------------------------------------------------------------------------------------
# Membership
# --------------------------------------------------------------------------------------


def simulated_edge(change: MembershipChange) -> GraphEdge:
    """The edge an ``add_member`` change stands for.

    Built through :class:`app.domain.GraphEdge` rather than assembled by hand, so a proposal
    that could not exist as a stored edge — a group containing itself, most obviously — is
    refused here exactly as it would be on ingestion.
    """
    return GraphEdge(
        edge_key=change.edge_key,
        group_key=change.group_key,
        member_key=change.member_key,
        kind=change.edge_kind,
        host_key=change.host_key,
        member_kind=change.member_kind,
        is_foreign_security_principal=False,
    )


def overlay_edges(
    overlay: SimulationOverlay,
    direction: Direction,
    keys: Sequence[str],
    baseline: Mapping[str, Sequence[GraphEdge]],
) -> dict[str, list[GraphEdge]]:
    """The adjacency a traversal would see if the overlay were real.

    Removals match on ``(group, member, kind)`` and not on the stored ``edge_key``, because a
    proposal says *"take Alice out of Finance-RW"* and not *"delete row 4718"*. The two
    coincide today — the edge key is derived from exactly that triple — and matching on the
    triple keeps them coinciding if the key derivation ever gains a field.

    Additions are appended after the observed edges rather than interleaved. A traversal is
    breadth-first over a set, so the order within one node's adjacency does not change which
    nodes are reached; appending keeps the observed edges in the position a reader of a
    truncated traversal would expect to find them.
    """
    result = {key: list(baseline.get(key, ())) for key in dict.fromkeys(keys)}
    if not overlay.membership:
        return result

    removed = {
        (change.group_key, change.member_key, change.edge_kind)
        for change in overlay.membership
        if change.kind is ChangeKind.REMOVE_MEMBER
    }
    if removed:
        for key, edges in result.items():
            result[key] = [
                edge
                for edge in edges
                if (edge.group_key, edge.member_key, edge.kind) not in removed
            ]

    for change in overlay.membership:
        if change.kind is not ChangeKind.ADD_MEMBER:
            continue
        edge = simulated_edge(change)
        origin = edge.origin(direction)
        if origin not in result:
            continue
        if any(
            (existing.group_key, existing.member_key, existing.kind)
            == (edge.group_key, edge.member_key, edge.kind)
            for existing in result[origin]
        ):
            # Already a member. Adding a second edge would double the chains an explanation
            # enumerates and change nothing about the access.
            continue
        result[origin].append(edge)
    return result


# --------------------------------------------------------------------------------------
# NTFS
# --------------------------------------------------------------------------------------


def overlay_ntfs_acl(
    overlay: SimulationOverlay,
    resource_key: str,
    baseline: Sequence[NtfsAceRecord],
    *,
    parent_entries: Sequence[NtfsAceRecord] | None = None,
    for_container: bool = True,
) -> tuple[NtfsAceRecord, ...]:
    """One directory's DACL as the overlay would leave it.

    Args:
        overlay: the proposal.
        resource_key: the directory, folded.
        baseline: the entries a repository read, in evaluation order.
        parent_entries: the parent's entries, needed only to clear protection — what flows
            back down is a projection of them. ``None`` means the parent was not read, and
            the toggle then changes the flag and nothing else; the caller reports that as
            the gap it is rather than projecting from an empty parent, which would look
            exactly like a parent that grants nobody anything.
        for_container: whether this resource is a directory, which decides which of the two
            inheritance projections applies.

    Returns:
        The entries, in evaluation order. The baseline tuple itself when the overlay touches
        neither this DACL nor its inheritance, so an untouched resource costs nothing and is
        demonstrably the same object.
    """
    folded = resource_key.casefold()
    ace_changes = overlay.ntfs_changes_for(folded)
    inheritance = overlay.inheritance_change_for(folded)
    if not ace_changes and inheritance is None:
        return tuple(baseline)

    # Decided from the *baseline*, before anything is added: the question is whether the
    # collector numbered this ACL. An entry an overlay invents has no position of its own, so
    # reading the flag off the result would make one unplaced addition turn a numbered ACL
    # into an unnumbered one -- and an unnumbered DACL cannot express a Deny.
    ordered = all(entry.order_index is not None for entry in baseline)
    entries = list(baseline)
    entries = _apply_ntfs_ace_changes(entries, ace_changes, folded)
    if inheritance is not None:
        entries = _apply_inheritance(
            entries,
            inheritance,
            parent_entries=parent_entries,
            for_container=for_container,
        )
    return _renumbered(entries, ordered=ordered)


def _apply_ntfs_ace_changes(
    entries: list[NtfsAceRecord], changes: Sequence[NtfsAceChange], resource_key: str
) -> list[NtfsAceRecord]:
    """Removals, modifications, then additions, in that order.

    Removals and modifications first because both name an entry by the key it has in the
    baseline; running an addition ahead of them could introduce an entry that a removal then
    matched, which would make the result depend on the order two independent changes were
    written in.
    """
    by_key = {change.ace_key: change for change in changes if change.ace_key is not None}
    result: list[NtfsAceRecord] = []
    for entry in entries:
        change = by_key.get(entry.ace_key)
        if change is None:
            result.append(entry)
            continue
        if change.kind is ChangeKind.REMOVE_NTFS_ACE:
            continue
        result.append(_modified_ntfs_entry(entry, change))

    server = parse_unc_path(resource_key).server
    for change in changes:
        if change.kind is not ChangeKind.ADD_NTFS_ACE:
            continue
        candidate = _added_ntfs_entry(change, resource_key, server)
        if any(_same_ntfs_ace(existing, candidate) for existing in result):
            # The entry is already on the ACL. Windows would merge, not duplicate, and a
            # duplicate here would double every path an explanation enumerates through it.
            continue
        result.insert(_insertion_point(result, candidate, change.order_index), candidate)
    return result


def _modified_ntfs_entry(entry: NtfsAceRecord, change: NtfsAceChange) -> NtfsAceRecord:
    """An existing entry with the proposed fields replaced, keeping its position.

    The key is rebuilt, because an NTFS ACE's identity *is* its trustee, type, mask and
    flags: an entry whose mask changed is a different entry, and reusing the old key would
    make a stored ACE and a simulated one with different rights compare equal.
    """
    ace_type = change.ace_type or entry.ace_type
    access_mask = entry.access_mask if change.access_mask is None else change.access_mask
    ace_flags = entry.ace_flags if change.ace_flags is None else change.ace_flags
    return replace(
        entry,
        ace_type=ace_type,
        access_mask=access_mask,
        ace_flags=ace_flags,
        ace_key=_simulated_ntfs_key(
            entry.resource_key, entry.trustee_sid, ace_type, access_mask, ace_flags
        ),
        source_key=SIMULATED_SOURCE_KEY,
        first_observed_at=SIMULATED_AT,
        last_observed_at=SIMULATED_AT,
        first_observed_run_id=SIMULATED_RUN_ID,
        last_observed_run_id=SIMULATED_RUN_ID,
    )


def _added_ntfs_entry(change: NtfsAceChange, resource_key: str, server: str) -> NtfsAceRecord:
    """A brand-new entry, with its trustee resolved in this server's context."""
    if change.trustee_sid is None or change.ace_type is None or change.access_mask is None:
        raise DomainValidationError(  # pragma: no cover - the change validates this itself
            "An addition must carry a trustee, a type and a mask.", field="trustee_sid"
        )
    flags = 0 if change.ace_flags is None else change.ace_flags
    return NtfsAceRecord(
        ace_key=_simulated_ntfs_key(
            resource_key, change.trustee_sid, change.ace_type, change.access_mask, flags
        ),
        resource_key=resource_key,
        trustee_sid=change.trustee_sid,
        trustee_key=referenced_principal_key(Sid(change.trustee_sid), server),
        ace_type=change.ace_type,
        access_mask=change.access_mask,
        ace_flags=flags,
        # An entry somebody proposes adding is an explicit entry on this object. An
        # inherited one is not something an administrator creates; it arrives from a parent,
        # which is what the inheritance toggle models.
        source=AceSource.EXPLICIT,
        inherited_from=None,
        order_index=change.order_index,
        source_key=SIMULATED_SOURCE_KEY,
        first_observed_at=SIMULATED_AT,
        first_observed_run_id=SIMULATED_RUN_ID,
        last_observed_at=SIMULATED_AT,
        last_observed_run_id=SIMULATED_RUN_ID,
    )


def _apply_inheritance(
    entries: list[NtfsAceRecord],
    change: InheritanceChange,
    *,
    parent_entries: Sequence[NtfsAceRecord] | None,
    for_container: bool,
) -> list[NtfsAceRecord]:
    """Set or clear protection, and do to the inherited entries what the proposal says."""
    if change.protected:
        if change.inherited_entries is InheritedAceDisposition.REMOVE:
            return [entry for entry in entries if not entry.is_inherited]
        return [_converted_to_explicit(entry) if entry.is_inherited else entry for entry in entries]

    if parent_entries is None:
        # Nothing to project. The flag still flips — the caller reports the gap — because
        # projecting from a parent nobody read would produce an empty inheritance, which is
        # indistinguishable from a parent that hands its children nothing.
        return entries

    explicit = [entry for entry in entries if not entry.is_inherited]
    projected = project_inherited_acl(
        [entry.acl_facts for entry in parent_entries], for_container=for_container
    )
    parent_key = parent_entries[0].resource_key if parent_entries else None
    resource_key = entries[0].resource_key if entries else change.resource_key
    server = parse_unc_path(resource_key).server
    inherited = [_projected_record(fact, resource_key, server, parent_key) for fact in projected]
    # Explicit entries come first in a canonical DACL and inherited ones behind them, which
    # is also the order Windows writes after re-enabling inheritance.
    return [*explicit, *inherited]


def _converted_to_explicit(entry: NtfsAceRecord) -> NtfsAceRecord:
    """One inherited entry, rewritten as the explicit copy protection leaves behind."""
    flags = entry.ace_flags & ~int(AceFlag.INHERITED)
    return replace(
        entry,
        source=AceSource.EXPLICIT,
        ace_flags=flags,
        inherited_from=None,
        ace_key=_simulated_ntfs_key(
            entry.resource_key, entry.trustee_sid, entry.ace_type, entry.access_mask, flags
        ),
        source_key=SIMULATED_SOURCE_KEY,
        first_observed_at=SIMULATED_AT,
        last_observed_at=SIMULATED_AT,
        first_observed_run_id=SIMULATED_RUN_ID,
        last_observed_run_id=SIMULATED_RUN_ID,
    )


def _projected_record(
    fact: AclAceFacts, resource_key: str, server: str, parent_key: str | None
) -> NtfsAceRecord:
    """One entry a parent projects onto this child, as a record.

    ``source`` is read back off the flag byte rather than assumed, exactly as
    ``app.services.access._projected_entry`` does it: every entry a projection produces
    carries the ``INHERITED`` flag, and deriving the two from one another is what keeps the
    record's own consistency rule from being violated by a value nobody checked.
    """
    return NtfsAceRecord(
        ace_key=_simulated_ntfs_key(
            resource_key, fact.trustee_sid, fact.ace_type, fact.access_mask, fact.ace_flags
        ),
        resource_key=resource_key,
        trustee_sid=fact.trustee_sid,
        trustee_key=referenced_principal_key(Sid(fact.trustee_sid), server),
        ace_type=fact.ace_type,
        access_mask=fact.access_mask,
        ace_flags=fact.ace_flags,
        source=(
            AceSource.INHERITED if fact.ace_flags & int(AceFlag.INHERITED) else AceSource.EXPLICIT
        ),
        inherited_from=parent_key,
        order_index=fact.order_index,
        source_key=SIMULATED_SOURCE_KEY,
        first_observed_at=SIMULATED_AT,
        first_observed_run_id=SIMULATED_RUN_ID,
        last_observed_at=SIMULATED_AT,
        last_observed_run_id=SIMULATED_RUN_ID,
    )


def overlay_resource(
    overlay: SimulationOverlay,
    record: NtfsResourceRecord,
    entries: Sequence[NtfsAceRecord],
) -> NtfsResourceRecord:
    """The directory's own row, made consistent with the DACL the overlay produced.

    Three fields have to move with the entries, and every one of them changes an answer if
    it does not:

    * ``ace_count`` is what the descriptor declared, and the access check compares it with
      the entries it was handed — a mismatch raises ``ACE_COUNT_MISMATCH`` on every single
      simulated resolution, which would bury the finding the simulation was run to produce.
    * ``dacl_protected`` is a finding in its own right (``PROTECTED_DACL``) and is part of
      the ACL's normal form.
    * ``acl_hash`` is the digest of the normalized ACL; leaving the collected one in place
      would claim that a changed ACL still matches its parent's projection.

    ``is_acl_boundary`` and ``boundary_reason`` follow protection, and only protection: the
    other reasons a directory is a boundary are facts about its parent, which an overlay
    does not change. A directory that was a boundary for some other reason stays one.
    """
    folded = record.resource_key.casefold()
    inheritance = overlay.inheritance_change_for(folded)
    if inheritance is None and not overlay.ntfs_changes_for(folded):
        return record

    protected = record.dacl_protected if inheritance is None else inheritance.protected
    boundary_reason = record.boundary_reason
    is_boundary = record.is_acl_boundary
    if inheritance is not None:
        if protected:
            boundary_reason = AclBoundaryReason.PROTECTED_DACL
            is_boundary = True
        elif record.boundary_reason is AclBoundaryReason.PROTECTED_DACL:
            # Protection was the only reason recorded, and it is being cleared. Whether the
            # directory still differs from its parent is a comparison against the parent's
            # projection that nothing here has made, so it is reported as not a boundary
            # rather than as one for a reason that no longer applies.
            boundary_reason = None
            is_boundary = False

    return replace(
        record,
        dacl_protected=protected,
        inheritance_enabled=not protected,
        is_acl_boundary=is_boundary,
        boundary_reason=boundary_reason,
        ace_count=len(entries),
        acl_hash=_digest(record.dacl_present, protected, entries),
        source_key=SIMULATED_SOURCE_KEY,
        first_observed_at=SIMULATED_AT,
        last_observed_at=SIMULATED_AT,
        first_observed_run_id=SIMULATED_RUN_ID,
        last_observed_run_id=SIMULATED_RUN_ID,
    )


def _digest(
    dacl_present: bool, dacl_protected: bool, entries: Sequence[NtfsAceRecord]
) -> str | None:
    """The normalized digest of a simulated ACL, or ``None`` when one cannot honestly be taken.

    A NULL DACL carries no entries by definition, and
    :func:`app.domain.normalize_acl` refuses the combination rather than hashing a
    contradiction. A simulation that added an entry to such a directory has produced a state
    that is not representable, and ``None`` — "no digest could honestly be taken" — is the
    value the rest of the system already understands for that.
    """
    if not dacl_present and entries:
        return None
    return acl_hash(
        dacl_present=dacl_present,
        dacl_protected=dacl_protected,
        aces=[entry.acl_facts for entry in entries],
    )


# --------------------------------------------------------------------------------------
# Share
# --------------------------------------------------------------------------------------


def overlay_share_acl(
    overlay: SimulationOverlay,
    share_key: str,
    baseline: Sequence[ShareAceRecord],
) -> tuple[ShareAceRecord, ...]:
    """One share's ACL as the overlay would leave it.

    **An empty baseline is left empty.** Zero stored entries means nobody has read this
    share's ACL, not that it grants nothing (Windows shares always carry one), and
    :func:`app.services.access._share_dacl_from` reads the difference straight off the
    count. Adding a single simulated entry to an unread ACL would turn *"we have never
    looked at this share"* into *"this share grants exactly this and nothing else"* — an
    invented certainty, and in the direction that hides access. The change is refused here
    and reported as not applicable by the caller.
    """
    folded = share_key.casefold()
    changes = overlay.share_changes_for(folded)
    if not changes or not baseline:
        return tuple(baseline)

    ordered = all(entry.order_index is not None for entry in baseline)
    by_key = {change.ace_key: change for change in changes if change.ace_key is not None}
    result: list[ShareAceRecord] = []
    for entry in baseline:
        change = by_key.get(entry.ace_key)
        if change is None:
            result.append(entry)
            continue
        if change.kind is ChangeKind.REMOVE_SHARE_ACE:
            continue
        result.append(_modified_share_entry(entry, change))

    for change in changes:
        if change.kind is not ChangeKind.ADD_SHARE_ACE:
            continue
        candidate = _added_share_entry(change)
        if any(_same_share_ace(existing, candidate) for existing in result):
            continue
        result.insert(_insertion_point(result, candidate, change.order_index), candidate)
    return _renumbered(result, ordered=ordered)


def _modified_share_entry(entry: ShareAceRecord, change: ShareAceChange) -> ShareAceRecord:
    """An existing share entry with the proposed right replaced.

    Setting one right form clears the other. A share ACE carries a mask or a permission
    level and never both — the two are different readings of the same ACL — and a record
    holding both would be a shape :class:`app.domain.SmbShareAce` refuses.
    """
    ace_type = change.ace_type or entry.ace_type
    if change.access_mask is not None:
        access_mask, permission = change.access_mask, None
    elif change.permission is not None:
        access_mask, permission = None, change.permission
    else:
        access_mask, permission = entry.access_mask, entry.permission
    token = share_ace_right_token(access_mask, permission)
    return replace(
        entry,
        ace_type=ace_type,
        access_mask=access_mask,
        permission=permission,
        right_token=token,
        ace_key=f"{SIMULATED_ACE_PREFIX}"
        + share_ace_identity_key(
            share_key=entry.share_key,
            trustee_sid=Sid(entry.trustee_sid),
            ace_type=ace_type,
            right_token=token,
        ),
        source_key=SIMULATED_SOURCE_KEY,
        first_observed_at=SIMULATED_AT,
        last_observed_at=SIMULATED_AT,
        first_observed_run_id=SIMULATED_RUN_ID,
        last_observed_run_id=SIMULATED_RUN_ID,
    )


def _added_share_entry(change: ShareAceChange) -> ShareAceRecord:
    if change.trustee_sid is None or change.ace_type is None:
        raise DomainValidationError(  # pragma: no cover - the change validates this itself
            "An addition must carry a trustee and a type.", field="trustee_sid"
        )
    trustee_key = change.trustee_key or change.trustee_sid
    token = share_ace_right_token(change.access_mask, change.permission)
    return ShareAceRecord(
        ace_key=f"{SIMULATED_ACE_PREFIX}"
        + share_ace_identity_key(
            share_key=change.share_key,
            trustee_sid=Sid(change.trustee_sid),
            ace_type=change.ace_type,
            right_token=token,
        ),
        share_key=change.share_key,
        trustee_sid=change.trustee_sid,
        trustee_key=trustee_key,
        ace_type=change.ace_type,
        access_mask=change.access_mask,
        permission=change.permission,
        right_token=token,
        order_index=change.order_index,
        source_key=SIMULATED_SOURCE_KEY,
        first_observed_at=SIMULATED_AT,
        first_observed_run_id=SIMULATED_RUN_ID,
        last_observed_at=SIMULATED_AT,
        last_observed_run_id=SIMULATED_RUN_ID,
    )


# --------------------------------------------------------------------------------------
# Placement and identity
# --------------------------------------------------------------------------------------


def canonical_position(
    entries: Sequence[NtfsAceRecord | ShareAceRecord], ace_type: AceType, *, inherited: bool
) -> int:
    """Where the Windows ACL editor would put a new entry of this type.

    Explicit Deny, explicit Allow, inherited Deny, inherited Allow — the canonical order
    :func:`app.access_engine.canonical_order_violations` measures departures from. A new
    entry goes at the end of its own band, which is what makes adding a second Allow leave
    the first one's precedence alone.
    """
    band = _band(ace_type, inherited=inherited)
    position = 0
    for index, entry in enumerate(entries):
        entry_inherited = getattr(entry, "is_inherited", False)
        if _band(entry.ace_type, inherited=bool(entry_inherited)) <= band:
            position = index + 1
    return position


def _band(ace_type: AceType, *, inherited: bool) -> int:
    deny = ace_type is AceType.DENY
    if not inherited:
        return 0 if deny else 1
    return 2 if deny else 3


def _insertion_point(
    entries: Sequence[NtfsAceRecord | ShareAceRecord],
    candidate: NtfsAceRecord | ShareAceRecord,
    requested: int | None,
) -> int:
    """The index an addition lands at: the one asked for, clamped, or the canonical one.

    An explicit position is honored even where it produces a non-canonical DACL. Simulating
    one is exactly how an operator discovers that an ACL their editor draws as "Deny wins"
    behaves the other way round, so refusing to build it would remove the finding.
    """
    if requested is not None:
        return max(0, min(requested, len(entries)))
    inherited = bool(getattr(candidate, "is_inherited", False))
    return canonical_position(entries, candidate.ace_type, inherited=inherited)


def _renumbered(entries: Sequence[AceRecordT], *, ordered: bool) -> tuple[AceRecordT, ...]:
    """Positions 0..n-1 over the result, when the baseline ACL carried positions.

    Two entries claiming one position is an ambiguity :func:`app.domain.normalize_acl`
    refuses and the access check would resolve by accident, so an ACL that came back ordered
    is handed on ordered -- with the additions renumbered into the sequence. One whose
    entries carried no position keeps them that way: inventing positions for entries a
    collector never numbered would turn an unordered hash into an ordered one and claim an
    evaluation order nobody observed.
    """
    listed = list(entries)
    if not ordered:
        return tuple(listed)
    return tuple(
        replace(entry, order_index=index) if entry.order_index != index else entry
        for index, entry in enumerate(listed)
    )


def _simulated_ntfs_key(
    resource_key: str, trustee_sid: str, ace_type: AceType, access_mask: int, flags: int
) -> str:
    return SIMULATED_ACE_PREFIX + ntfs_ace_identity_key(
        resource_key=resource_key,
        trustee_sid=Sid(trustee_sid),
        ace_type=ace_type,
        access_mask=access_mask,
        flags=AceFlag(flags),
    )


def _same_ntfs_ace(left: NtfsAceRecord, right: NtfsAceRecord) -> bool:
    """Whether two entries are the same permission, whatever their keys say.

    Compared on the fields an NTFS ACE's identity is made of rather than on the key itself,
    because a simulated key carries a prefix and the entry it duplicates does not.
    """
    return (
        left.trustee_key == right.trustee_key
        and left.ace_type is right.ace_type
        and left.access_mask == right.access_mask
        and left.ace_flags == right.ace_flags
    )


def _same_share_ace(left: ShareAceRecord, right: ShareAceRecord) -> bool:
    return (
        left.trustee_key == right.trustee_key
        and left.ace_type is right.ace_type
        and left.right_token == right.right_token
    )


def resource_is_container(record: NtfsResourceRecord | None) -> bool:
    """Whether children of this resource inherit through the container projection.

    A resource nobody read is treated as a directory, which is what the path of a
    simulation that toggles inheritance on it implies: files do not have children to
    protect from.
    """
    return record is None or record.resource_kind is ResourceKind.DIRECTORY


def entries_by_key(entries: Iterable[NtfsAceRecord]) -> dict[str, NtfsAceRecord]:
    """An ACL indexed by ACE key, for applicability checks that need one."""
    return {entry.ace_key: entry for entry in entries}
