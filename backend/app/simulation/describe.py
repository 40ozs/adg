r"""One wording for every simulation vocabulary, and one sentence about what a simulation is.

Pure: values in, strings out. It exists because Phase 9B put three renderers in front of the
same report — an HTTP response, a structured export, and a web page — and three copies of
*"removes Finance-RW from the DACL of \\FS01\Finance"* would eventually be three different
sentences about one proposal. The one an operator pastes into a change ticket would then not
be the one the API said.

The descriptions for the outcome, caveat and truncation vocabularies already live in
:mod:`app.simulation.model`, beside the enums they explain. This module adds the two that
had no home — what a *change* says, and what a *direction* means — and re-exports the
others so that a renderer has one import rather than four.

**:data:`NON_DESTRUCTIVE_NOTICE` is not decoration.** It is the sentence every simulation
surface carries, and it is here rather than typed into each of them because the acceptance
criterion for this phase is that the non-destructive nature is unmistakable: a notice that
three surfaces each spell their own way is a notice one of them will eventually soften.
"""

from __future__ import annotations

from typing import Final

from app.simulation.model import (
    CAVEAT_DESCRIPTIONS,
    OUTCOME_DESCRIPTIONS,
    TRUNCATION_DESCRIPTIONS,
    ImpactDirection,
)
from app.simulation.overlay import (
    ChangeKind,
    InheritanceChange,
    InheritedAceDisposition,
    MembershipChange,
    NtfsAceChange,
    ShareAceChange,
    SimulationChange,
)

__all__ = [
    "CAVEAT_DESCRIPTIONS",
    "CHANGE_KIND_DESCRIPTIONS",
    "DIRECTION_DESCRIPTIONS",
    "DISPOSITION_DESCRIPTIONS",
    "NON_DESTRUCTIVE_NOTICE",
    "OUTCOME_DESCRIPTIONS",
    "TRUNCATION_DESCRIPTIONS",
    "describe_change",
    "describe_direction",
]

NON_DESTRUCTIVE_NOTICE: Final = (
    "NO CHANGES WILL BE APPLIED. This is a simulation: ADG computed the answer by reading "
    "its own collected facts through a proposed change held in memory. Nothing was written "
    "to Active Directory, to a share, or to an NTFS descriptor, and nothing in ADG's "
    "collected state was altered."
)
"""The sentence every simulation surface carries, in one place."""


DIRECTION_DESCRIPTIONS: Final[dict[ImpactDirection, str]] = {
    ImpactDirection.UNCHANGED: "Holds exactly what they hold today.",
    ImpactDirection.GAINED_ACCESS: "Holds nothing today and would hold something.",
    ImpactDirection.LOST_ACCESS: "Holds something today and would hold nothing.",
    ImpactDirection.EXPANDED: "Would hold strictly more than today.",
    ImpactDirection.REDUCED: "Would hold strictly less than today, and still something.",
    ImpactDirection.CHANGED: "Would gain some rights and lose others.",
}


CHANGE_KIND_DESCRIPTIONS: Final[dict[ChangeKind, str]] = {
    ChangeKind.ADD_MEMBER: "Put a principal into a group.",
    ChangeKind.REMOVE_MEMBER: "Take a principal out of a group.",
    ChangeKind.ADD_NTFS_ACE: "Add an entry to a directory's NTFS permissions.",
    ChangeKind.MODIFY_NTFS_ACE: "Change an entry already on a directory's NTFS permissions.",
    ChangeKind.REMOVE_NTFS_ACE: "Remove an entry from a directory's NTFS permissions.",
    ChangeKind.ADD_SHARE_ACE: "Add an entry to a share's permissions.",
    ChangeKind.MODIFY_SHARE_ACE: "Change an entry already on a share's permissions.",
    ChangeKind.REMOVE_SHARE_ACE: "Remove an entry from a share's permissions.",
    ChangeKind.SET_INHERITANCE: (
        "Protect a directory from its parent's permissions, or unprotect it."
    ),
}


DISPOSITION_DESCRIPTIONS: Final[dict[InheritedAceDisposition, str]] = {
    InheritedAceDisposition.CONVERT_TO_EXPLICIT: (
        "Keep the entries the directory inherits today, rewritten as its own."
    ),
    InheritedAceDisposition.REMOVE: (
        "Drop the entries the directory inherits today. Only what is already explicit on it "
        "survives, which is usually a much shorter list than anybody expects."
    ),
}


def describe_direction(direction: ImpactDirection) -> str:
    """The sentence rendered beside one principal's movement."""
    return DIRECTION_DESCRIPTIONS[direction]


def describe_change(change: SimulationChange) -> str:
    r"""One proposed change as a sentence, in the vocabulary an administrator uses.

    Keys rather than display names, deliberately. A change acts on a storage key — the SID,
    the UNC path, the share key — and rendering it by display name would produce a sentence
    that reads well and does not say which object was meant when two groups share a name.
    The API attaches resolved names alongside; this stays the unambiguous version, and it is
    the one that goes into a change ticket.
    """
    if isinstance(change, MembershipChange):
        verb = "Add" if change.kind is ChangeKind.ADD_MEMBER else "Remove"
        preposition = "to" if change.kind is ChangeKind.ADD_MEMBER else "from"
        return (
            f"{verb} {change.member_key} {preposition} {change.group_key} "
            f"({change.edge_kind.value})."
        )
    if isinstance(change, NtfsAceChange):
        return _describe_ace(
            change.kind,
            layer="the NTFS permissions of",
            target=change.resource_key,
            ace_key=change.ace_key,
            trustee=change.trustee_sid,
            ace_type=None if change.ace_type is None else change.ace_type.value,
            rights=_rights_phrase(mask=change.access_mask, permission=None),
            extra=_flag_phrase(change.ace_flags),
        )
    if isinstance(change, ShareAceChange):
        return _describe_ace(
            change.kind,
            layer="the share permissions of",
            target=change.share_key,
            ace_key=change.ace_key,
            trustee=change.trustee_sid,
            ace_type=None if change.ace_type is None else change.ace_type.value,
            rights=_rights_phrase(
                mask=change.access_mask,
                permission=None if change.permission is None else change.permission.value,
            ),
            extra="",
        )
    return _describe_inheritance(change)


def _describe_inheritance(change: InheritanceChange) -> str:
    if not change.protected:
        return (
            f"Let {change.resource_key} inherit from its parent again. The parent's "
            "inheritable entries flow back down onto it."
        )
    disposition = change.inherited_entries
    tail = "" if disposition is None else f" {DISPOSITION_DESCRIPTIONS[disposition]}"
    return f"Protect {change.resource_key} from its parent's permissions.{tail}"


def _describe_ace(
    kind: ChangeKind,
    *,
    layer: str,
    target: str,
    ace_key: str | None,
    trustee: str | None,
    ace_type: str | None,
    rights: str,
    extra: str,
) -> str:
    """One ACL entry change, rendered for whichever of the two layers it acts on."""
    if kind in (ChangeKind.ADD_NTFS_ACE, ChangeKind.ADD_SHARE_ACE):
        effect = "denying" if ace_type == "deny" else "allowing"
        return f"Add an entry to {layer} {target}: {effect} {trustee}{rights}{extra}."
    if kind in (ChangeKind.REMOVE_NTFS_ACE, ChangeKind.REMOVE_SHARE_ACE):
        return f"Remove entry {ace_key} from {layer} {target}."
    changes: list[str] = []
    if ace_type is not None:
        changes.append(f"to {ace_type}")
    if rights:
        changes.append(f"granting{rights}")
    if extra:
        changes.append(extra.strip())
    detail = ", ".join(changes) if changes else "nothing"
    return f"Change entry {ace_key} on {layer} {target}: {detail}."


def _rights_phrase(*, mask: int | None, permission: str | None) -> str:
    """What an entry would be worth, in whichever form the proposal is written in.

    An empty string when neither is given, so the caller can concatenate without deciding
    whether a separator is wanted — a modification that only changes the ACE type says
    nothing about rights, and a sentence with a dangling "granting" would be worse than one
    without the clause.
    """
    if permission is not None:
        return f" {permission}"
    if mask is not None:
        return f" 0x{mask:08X}"
    return ""


def _flag_phrase(flags: int | None) -> str:
    """Whether the entry is marked to flow down to children, when the proposal says.

    Only the two inheritance bits are rendered. They are the ones that decide whether a
    change reaches a subtree, which is the question an operator has to answer before signing
    anything off — and the one this phase's descendant limitation is about.
    """
    if not flags:
        return ""
    inheritable = bool(flags & 0x03)
    return ", inheritable by children" if inheritable else ", on this directory only"
