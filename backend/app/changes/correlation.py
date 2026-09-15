r"""Two things a change cannot know about itself, both answered by its neighbours.

## An ACL edit is stored as a removal and an addition

An ACE's identity includes what it grants. ``ntfs_ace|<resource>|<sid>|allow|0x1f01ff|0x03``
and ``ntfs_ace|<resource>|<sid>|allow|0x1200a9|0x03`` are two different objects, so tightening
a directory's DACL from Full Control to Read & Execute does not modify a row — it removes one
and adds another. That is the right storage model (Phase 3A chose it so that a reordered ACL
does not read as every entry being deleted and recreated), and it is the wrong *reading*: an
operator shown "an entry was removed" and, eleven lines later, "an entry was added" has been
shown a permission tightening as two unrelated events, and will read at most one of them.

:func:`correlate` pairs them back up. The pair is what makes a real before/after possible,
and it is the only place in ADG where a **direction is computed rather than assigned**: the
two masks are compared, so ``Full Control -> Read & Execute`` is narrowed, ``Read ->
Modify`` is broadened, and ``Read -> Write`` — which gains and loses bits at once — is
``mixed`` rather than one of the two halves of the truth.

The pairing is deliberately conservative. Two changes pair only when they are the same kind,
on the same container, for the same trustee, of the same Allow/Deny type, one removed and one
added, and **each side pairs at most once**. Where a trustee has several entries of one type
on one ACL and more than one of them moved, nothing is paired: which removal goes with which
addition is not recoverable from the data, and a guess would print a before/after that never
existed. The changes are then reported unpaired, which is what they are.

## A position that moved may or may not mean anything

``order_index`` is the other one. Windows evaluates a DACL in order, so an entry that has
moved below a Deny now grants what that Deny refused — and an entry renumbered because an
audit ACE above it was deleted has moved nothing at all. The number changed in both cases.

:func:`ordering_materiality` decides it the way the product already decides whether two ACLs
are the same ACL: it rebuilds the normalized DACL at both ends of the change window and
compares the digests (:func:`app.domain.acl_hash.normalize_acl`). The normal form reduces
positions to their **rank**, so a renumbering that preserves the sequence produces an
identical document and a genuine reordering does not. Reusing it rather than writing a
second comparison is the point: there is one definition of "the same ACL" in this codebase
and this is it.

The share layer has no such digest — a share ACE carries no flags and a share ACL has no
DACL-present bit — so :func:`_share_sequence` renders the equivalent sequence for it, in one
function, next to the NTFS case it mirrors.

**A failure to reconstruct returns ``None``, never ``False``.** "We checked and the order
did not move" and "we could not check" are different answers, and only the first one may be
rendered as noise.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.access_engine.rights import RightsMask
from app.changes.model import (
    SEVERITY_ORDER,
    ChangeAction,
    ChangeDirection,
    ChangeSeverity,
    FieldSignificance,
    ObjectChange,
)
from app.changes.principals import trustee_display
from app.contracts.v1.common import ObservationKind
from app.domain.access import AceType
from app.domain.acl_hash import AclAceFacts, normalize_acl
from app.domain.errors import DomainValidationError
from app.history.repository import VersionReader

__all__ = [
    "AceEdit",
    "AceIdentity",
    "ChangeKey",
    "Correlation",
    "correlate",
    "ordering_materiality",
]

#: How a change is referred to from outside the object holding it: kind, key, and the
#: instant its newer version opened. Unique, because at most one version of an object opens
#: at any instant (``uq_object_versions_identity``).
ChangeKey = tuple[ObservationKind, str, dt.datetime]

ACE_KINDS = frozenset({ObservationKind.SMB_ACE, ObservationKind.NTFS_ACE})


def key_of(change: ObjectChange) -> ChangeKey:
    return change.kind, change.key, change.at


@dataclass(frozen=True, slots=True)
class AceIdentity:
    """The part of an ACE that survives an edit to what it grants."""

    kind: ObservationKind
    container_key: str
    trustee_key: str
    ace_type: str


@dataclass(frozen=True, slots=True)
class AceEdit:
    """One entry rewritten: the removal and the addition, read as a single edit."""

    identity: AceIdentity
    removed: ObjectChange
    added: ObjectChange
    rights_before: RightsMask | None
    rights_after: RightsMask | None
    direction: ChangeDirection
    severity: ChangeSeverity
    summary: str

    @property
    def members(self) -> tuple[ChangeKey, ChangeKey]:
        return key_of(self.removed), key_of(self.added)


@dataclass(frozen=True, slots=True)
class Correlation:
    """The edits found in a set of changes, and the index from change back to edit."""

    edits: tuple[AceEdit, ...]
    edit_of: Mapping[ChangeKey, int]
    """Change identity to index into :attr:`edits`. Changes not part of an edit are absent,
    which is a different statement from being part of an edit with no partner."""

    def for_change(self, change: ObjectChange) -> AceEdit | None:
        index = self.edit_of.get(key_of(change))
        return self.edits[index] if index is not None else None


def correlate(changes: Sequence[ObjectChange]) -> Correlation:
    """Pair removals with additions that are one edit of one ACL entry.

    Only pairs within the set it is given. A removal on Tuesday and an addition on Friday
    pair when both are in the window being reported and do not when only one of them is —
    which is correct rather than unfortunate: the pair is a claim that these two events are
    one edit, and a window that contains half of it has no evidence for that claim.
    """
    removals: dict[AceIdentity, list[ObjectChange]] = {}
    additions: dict[AceIdentity, list[ObjectChange]] = {}
    for change in changes:
        if change.kind not in ACE_KINDS:
            continue
        identity = _identity_of(change)
        if identity is None:
            continue
        if change.action is ChangeAction.REMOVED:
            removals.setdefault(identity, []).append(change)
        elif change.action is ChangeAction.ADDED:
            additions.setdefault(identity, []).append(change)

    edits: list[AceEdit] = []
    index: dict[ChangeKey, int] = {}
    for identity, removed in removals.items():
        added = additions.get(identity, [])
        # Exactly one each way, or nothing. See the module docstring: with two removals and
        # two additions for one trustee, which pairs with which is not in the data.
        if len(removed) != 1 or len(added) != 1:
            continue
        edit = _edit(identity, removed[0], added[0])
        index[key_of(edit.removed)] = len(edits)
        index[key_of(edit.added)] = len(edits)
        edits.append(edit)
    return Correlation(edits=tuple(edits), edit_of=index)


def _identity_of(change: ObjectChange) -> AceIdentity | None:
    state = change.after.state if change.after.state is not None else None
    if state is None and change.before is not None:
        state = change.before.state
    if state is None:
        return None
    container = change.after.container_key or (
        change.before.container_key if change.before is not None else None
    )
    trustee = state.get("trustee_key") or change.after.related_key
    ace_type = state.get("ace_type")
    if not container or not trustee or not ace_type:
        return None
    return AceIdentity(
        kind=change.kind,
        container_key=str(container),
        trustee_key=str(trustee),
        ace_type=str(ace_type),
    )


def _edit(identity: AceIdentity, removed: ObjectChange, added: ObjectChange) -> AceEdit:
    before = _rights_of(removed)
    after = _rights_of(added)
    direction = _direction(identity, before, after)
    severity = max(
        (removed.severity, added.severity), key=lambda value: SEVERITY_ORDER.index(value)
    )
    return AceEdit(
        identity=identity,
        removed=removed,
        added=added,
        rights_before=before,
        rights_after=after,
        direction=direction,
        severity=severity,
        summary=_summary(identity, before, after, direction),
    )


def _rights_of(change: ObjectChange) -> RightsMask | None:
    """The mask an ACE change's own state carries, in the layer it belongs to.

    Reuses :class:`app.changes.rules.ChangeFacts` rather than re-reading the columns, so
    the share layer's mask-or-permission-level question has exactly one answer in this
    package.
    """
    from app.changes.rules import ChangeFacts

    facts = ChangeFacts(
        kind=change.kind,
        key=change.key,
        action=change.action,
        before=change.before.state if change.before is not None else None,
        after=change.after.state,
        deltas=change.deltas,
        container_key=change.after.container_key,
        related_key=change.after.related_key,
    )
    return facts.granted_mask


def _direction(
    identity: AceIdentity, before: RightsMask | None, after: RightsMask | None
) -> ChangeDirection:
    """Which way the rewrite moved access, by comparing the two masks.

    **Deny inverts.** A Deny entry whose mask grew withholds more, so the change narrows
    access even though the number went up. Reading a Deny's mask as a grant is the single
    most common way an ACL tool gets a report backwards.
    """
    if before is None or after is None:
        return ChangeDirection.UNDETERMINED
    gained = after.value & ~before.value
    lost = before.value & ~after.value
    if gained and lost:
        return ChangeDirection.MIXED
    if not gained and not lost:
        return ChangeDirection.NEUTRAL
    widened = bool(gained)
    if identity.ace_type == AceType.DENY.value:
        widened = not widened
    return ChangeDirection.BROADENED if widened else ChangeDirection.NARROWED


def _summary(
    identity: AceIdentity,
    before: RightsMask | None,
    after: RightsMask | None,
    direction: ChangeDirection,
) -> str:
    who = trustee_display(identity.trustee_key)
    verb = "Deny" if identity.ace_type == AceType.DENY.value else "Allow"
    old = f"0x{before.value:08x}" if before is not None else "an unreadable mask"
    new = f"0x{after.value:08x}" if after is not None else "an unreadable mask"
    return (
        f"The {verb} entry for {who} was rewritten from {old} to {new} "
        f"({direction.value}). ADG stores an ACE's rights as part of its identity, so one "
        "edit is recorded as a removal and an addition."
    )


# ------------------------------------------------------------------ ordering materiality


async def ordering_materiality(
    session: AsyncSession, changes: Sequence[ObjectChange]
) -> dict[ChangeKey, bool | None]:
    """Whether each order-only ACE change actually moved the normalized ACL.

    Only asked for changes whose *only* differences are positional: anything else is already
    classified by a field the table has an opinion about, and rebuilding two ACLs to confirm
    what is already known would be a pair of queries per change for no answer.

    Returns one entry per candidate. Candidates that could not be reconstructed map to
    ``None``.
    """
    candidates = [change for change in changes if _is_order_only(change)]
    if not candidates:
        return {}

    reader = VersionReader(session)
    answers: dict[ChangeKey, bool | None] = {}
    # One reconstruction per (container, instant pair). A single scan usually renumbers
    # every entry of one ACL at once, so this collapses a page of candidates into one or
    # two pairs of reads.
    groups: dict[tuple[ObservationKind, str, dt.datetime, dt.datetime], list[ObjectChange]] = {}
    for change in candidates:
        container = change.after.container_key
        assert change.before is not None
        if not container:
            answers[key_of(change)] = None
            continue
        groups.setdefault(
            (change.kind, str(container), change.before.last_seen_at, change.after.valid_from), []
        ).append(change)

    for (kind, container, was, now), members in groups.items():
        verdict = await _acl_moved(reader, kind, container, was, now)
        for change in members:
            answers[key_of(change)] = verdict
    return answers


def _is_order_only(change: ObjectChange) -> bool:
    """A modification whose every meaningful delta is a position.

    ``source_key`` is allowed alongside, because every superseding observation carries a new
    one and it is classified as noise; requiring the delta list to be exactly ``order_index``
    would make this fire almost never.
    """
    if change.action is not ChangeAction.MODIFIED or change.kind not in ACE_KINDS:
        return False
    significances = {delta.significance for delta in change.deltas}
    return FieldSignificance.ORDER in significances and significances <= {
        FieldSignificance.ORDER,
        FieldSignificance.NOISE,
    }


async def _acl_moved(
    reader: VersionReader,
    kind: ObservationKind,
    container_key: str,
    was: dt.datetime,
    now: dt.datetime,
) -> bool | None:
    """Whether the normalized ACL of ``container_key`` differs between two instants."""
    before = await _normalized_acl(reader, kind, container_key, was)
    after = await _normalized_acl(reader, kind, container_key, now)
    if before is None or after is None:
        return None
    return before != after


async def _normalized_acl(
    reader: VersionReader, kind: ObservationKind, container_key: str, moment: dt.datetime
) -> str | None:
    """The canonical rendering of one ACL as of an instant, or ``None`` if unavailable."""
    entries = await reader.contained_at(kind, [container_key], moment)
    states = [version.state for version in entries if version.state is not None]
    if kind is ObservationKind.SMB_ACE:
        return _share_sequence(states)
    resource = await reader.version_at(ObservationKind.NTFS_RESOURCE, container_key, moment)
    if resource is None or resource.state is None:
        return None
    try:
        return normalize_acl(
            dacl_present=bool(resource.state.get("dacl_present", True)),
            dacl_protected=bool(resource.state.get("dacl_protected", False)),
            aces=[_facts(state) for state in states],
        ).digest
    except DomainValidationError:
        # A NULL DACL carrying entries, or two entries claiming one position. Both are real
        # states a collector can report and neither can be normalized, so the honest answer
        # about whether the order moved is that ADG does not know.
        return None


def _facts(state: Mapping[str, Any]) -> AclAceFacts:
    return AclAceFacts(
        trustee_sid=str(state["trustee_sid"]),
        ace_type=AceType(str(state["ace_type"])),
        access_mask=int(state["access_mask"]),
        ace_flags=int(state["ace_flags"]),
        order_index=None if state.get("order_index") is None else int(state["order_index"]),
    )


def _share_sequence(states: Iterable[Mapping[str, Any]]) -> str:
    r"""The share layer's equivalent of a normalized DACL: entries in evaluation order.

    Not :func:`app.domain.acl_hash.normalize_acl`, deliberately. That form is defined over a
    file-system descriptor — it hashes ``dacl_present``, ``dacl_protected`` and an ACE flags
    byte, none of which a share ACL has — and feeding it zeros for the fields the share layer
    does not have would produce a digest that *looks* comparable to a real NTFS one. Keeping
    the two forms visibly different is the same discipline that keeps an SMB mask from
    comparing equal to an NTFS mask carrying the same bits.

    Unordered entries sort by content and are marked as such, exactly as the NTFS form does,
    so a reading that knew the order can never compare equal to one that did not.
    """
    entries = list(states)
    ordered = all(state.get("order_index") is not None for state in entries)
    lines = [
        "adg-share-acl/1",
        f"order={'observed' if ordered else 'unordered'}",
    ]
    rendered = [
        (
            state.get("order_index"),
            f"{state.get('trustee_key')}|{state.get('ace_type')}|{state.get('right_token')}",
        )
        for state in entries
    ]
    if ordered:
        rendered.sort(key=lambda item: (int(item[0] or 0), item[1]))
    else:
        rendered.sort(key=lambda item: item[1])
    lines.extend(content for _, content in rendered)
    return "".join(f"{line}\n" for line in lines)
