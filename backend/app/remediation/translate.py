r"""Turning a change plan into the simulation engine's own vocabulary, and back.

Pure, and the whole reason the blast radius on a plan can be trusted: a plan is not measured
by a second implementation of the access check, and it is not measured by an estimate. It is
translated into a :class:`~app.simulation.overlay.SimulationOverlay` — the same value
Phase 9A built — and evaluated by :class:`~app.simulation.service.SimulationService`, which
runs :class:`app.services.AccessService` twice with not one line changed.

That is the same seam Phase 7A used to answer about a past instant and Phase 9A used to
answer about a hypothetical one, applied once more. The alternative — a remediation-specific
impact calculation — would eventually disagree with the access engine, and on that day
nobody would be able to say which of the two was right about a change somebody had already
made.

**The map is total, and a test says so.** Every member of
:class:`~app.domain.remediation.PlannedChangeKind` translates, and
``tests/remediation/test_translation.py`` asserts it over the enum rather than over the kinds
somebody remembered to list. A kind with no translation would be accepted, stored, approved
and exported without ever having been simulated: an instruction wearing the paperwork of one
that had been measured.

**Translation is the only direction that exists.** There is deliberately no function here
turning an overlay back into a plan. An overlay can express changes a plan may not — adding
an ACE, widening a mask, setting inheritance — and a converter would be a way to smuggle one
past :func:`app.remediation.model.validate_narrowing`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.domain import SharePermission
from app.domain.remediation import PlannedChangeKind
from app.remediation.errors import RemediationValidationError
from app.remediation.model import ChangePlan, PlannedChange
from app.simulation.overlay import (
    ChangeKind,
    MembershipChange,
    NtfsAceChange,
    ShareAceChange,
    SimulationChange,
    SimulationOverlay,
)

__all__ = [
    "SHARE_PERMISSION_ORDER",
    "changes_for",
    "overlay_for",
    "overlay_for_changes",
]

SHARE_PERMISSION_ORDER: dict[SharePermission, int] = {
    SharePermission.READ: 0,
    SharePermission.CHANGE: 1,
    SharePermission.FULL: 2,
}
"""The three levels, ordered. Used by the narrowing rule and repeated nowhere else."""


def overlay_for(plan: ChangePlan) -> SimulationOverlay:
    """The what-if that measures this plan.

    Built from every change in step order. ``REPLACE_WITH_GROUP`` contributes **two** changes
    — the removal and the membership addition — and they are kept together in the one overlay
    on purpose: simulating the removal alone would report a loss of access the plan never
    intends, and simulating the addition alone would report a gain nobody is being given.
    Measuring the pair is the only way to answer the question the plan actually asks, which is
    *does this principal end up where they started?*
    """
    return overlay_for_changes(plan.changes)


def overlay_for_changes(changes: Sequence[PlannedChange]) -> SimulationOverlay:
    """The same, for a set of changes that is not yet a plan — a draft being previewed."""
    return SimulationOverlay.from_changes(
        simulated
        for change in sorted(changes, key=lambda item: item.sequence_index)
        for simulated in changes_for(change)
    )


def changes_for(change: PlannedChange) -> tuple[SimulationChange, ...]:
    """One planned change, as the simulated changes that stand for it.

    Raises:
        RemediationValidationError: if the kind has no translation. Unreachable while the map
            below is total, and raised rather than returning an empty tuple because an empty
            translation is silent: the overlay would simply not contain the change, the
            simulation would report no impact, and the plan would be approved on the strength
            of a measurement that never looked at it.
    """
    match change.kind:
        case PlannedChangeKind.REMOVE_GROUP_MEMBER:
            return (_membership_removal(change),)
        case PlannedChangeKind.REMOVE_NTFS_ACE:
            return (_ntfs_removal(change),)
        case PlannedChangeKind.MODIFY_NTFS_ACE:
            return (_ntfs_modification(change),)
        case PlannedChangeKind.REMOVE_SHARE_ACE:
            return (_share_removal(change),)
        case PlannedChangeKind.MODIFY_SHARE_ACE:
            return (_share_modification(change),)
        case PlannedChangeKind.REPLACE_WITH_GROUP:
            return (_ntfs_removal(change), _membership_addition(change))
    raise RemediationValidationError(  # pragma: no cover - the match above is total
        f"{change.kind.value} has no simulated form, so a plan containing it could be "
        "approved without its impact ever having been measured.",
        field="kind",
    )


def _membership_removal(change: PlannedChange) -> MembershipChange:
    edge = change.membership
    assert edge is not None  # PlannedChange.__post_init__ guarantees it
    return MembershipChange(
        kind=ChangeKind.REMOVE_MEMBER,
        group_key=edge.group_key,
        member_key=edge.member_key,
        edge_kind=edge.edge_kind,
        member_kind=edge.member_kind,
    )


def _membership_addition(change: PlannedChange) -> MembershipChange:
    edge = change.membership
    assert edge is not None
    return MembershipChange(
        kind=ChangeKind.ADD_MEMBER,
        group_key=edge.group_key,
        member_key=edge.member_key,
        edge_kind=edge.edge_kind,
        member_kind=edge.member_kind,
    )


def _ntfs_removal(change: PlannedChange) -> NtfsAceChange:
    entry = change.entry
    assert entry is not None
    return NtfsAceChange(
        kind=ChangeKind.REMOVE_NTFS_ACE,
        resource_key=change.target_key,
        ace_key=entry.ace_key,
    )


def _ntfs_modification(change: PlannedChange) -> NtfsAceChange:
    entry = change.entry
    assert entry is not None
    return NtfsAceChange(
        kind=ChangeKind.MODIFY_NTFS_ACE,
        resource_key=change.target_key,
        ace_key=entry.ace_key,
        access_mask=change.after_access_mask,
    )


def _share_removal(change: PlannedChange) -> ShareAceChange:
    entry = change.entry
    assert entry is not None
    return ShareAceChange(
        kind=ChangeKind.REMOVE_SHARE_ACE,
        share_key=change.target_key,
        ace_key=entry.ace_key,
    )


def _share_modification(change: PlannedChange) -> ShareAceChange:
    entry = change.entry
    assert entry is not None
    # A share ACE carries a mask or a level, never both, and the overlay refuses a
    # modification that sets both. The plan is written in whichever form the observation was,
    # so the form is carried through rather than converted -- converting would invent a
    # precision the collector did not report.
    if change.after_permission is not None:
        return ShareAceChange(
            kind=ChangeKind.MODIFY_SHARE_ACE,
            share_key=change.target_key,
            ace_key=entry.ace_key,
            permission=change.after_permission,
        )
    return ShareAceChange(
        kind=ChangeKind.MODIFY_SHARE_ACE,
        share_key=change.target_key,
        ace_key=entry.ace_key,
        access_mask=change.after_access_mask,
    )


def translated_kinds(changes: Iterable[PlannedChange]) -> frozenset[ChangeKind]:
    """Which simulated change kinds a set of planned changes produces.

    Used by the guard that checks a plan can only ever remove or narrow: the set this returns
    must be a subset of the removing kinds plus ``ADD_MEMBER``, and nothing else. See
    ``tests/remediation/test_translation.py``.
    """
    return frozenset(simulated.kind for change in changes for simulated in changes_for(change))
