r"""NTFS ACE propagation, and the ACL-boundary question it answers.

A tree scan asks one question of every directory it reaches: *did permissions change here,
or is this folder simply carrying what its parent handed down?* The answer is what turns a
million directories into the few dozen places an administrator actually decided something,
and it is the only thing that makes a recursive scan affordable to store.

The naive test — compare the child's ``acl_hash`` to the parent's — is wrong, and wrong in
the direction that matters: it marks **every** directory a boundary. A parent's explicit
ACE carrying ``CONTAINER_INHERIT`` (``0x02``) arrives at the child as the same ACE with
``INHERITED`` added (``0x12``). The two DACLs are different byte sequences precisely
*because* inheritance worked. So the comparison has to be made against what the parent
**projects** onto a child, not against the parent's own DACL.

That projection is this module. Given the parent's DACL it computes the DACL a freshly
created, unprotected child would have, and :func:`inherited_child_acl_hash` reduces it to a
digest comparable with the child's own ``acl_hash``. Equal digests mean the child is
carrying exactly what it inherited; unequal means somebody changed something here.

**The rules, measured rather than recalled.** For each parent ACE with flag byte ``F`` whose
mask carries no generic bits and whose trustee Windows does not substitute — the two
exceptions are below, and each splits one parent entry into **two** child entries:

=========================  =========================  ====================
Parent ``F``               Child container receives   Child object receives
=========================  =========================  ====================
``CI`` (0x02)              ``0x12``                   nothing
``OI`` (0x01)              ``0x19``                   ``0x10``
``OI|CI`` (0x03)           ``0x13``                   ``0x10``
``CI|IO`` (0x0a)           ``0x12``                   nothing
``OI|IO`` (0x09)           ``0x19``                   ``0x10``
``OI|CI|IO`` (0x0b)        ``0x13``                   ``0x10``
``CI|NP`` (0x06)           ``0x10``                   nothing
``OI|NP`` (0x05)           nothing                    ``0x10``
``OI|CI|NP`` (0x07)        ``0x10``                   ``0x10``
``OI|CI|NP|IO`` (0x0f)     ``0x10``                   ``0x10``
``0x00``                   nothing                    nothing
=========================  =========================  ====================

Every row was measured on Windows by creating a directory with that single ACE and reading
the raw descriptor of a child directory, a grandchild, and a child file
(``backend/tests/domain/test_inheritance.py`` pins the table, and
``collector/powershell/ntfs/tests/AdgNtfsRealFileSystem.Tests.ps1`` re-measures it against a
live file system). Three of them are worth saying out loud because they are easy to get
wrong from memory:

* ``INHERIT_ONLY`` is **not** a propagation stop. ``CI|IO`` ("subfolders only") propagates to
  a child container exactly as plain ``CI`` does; the bit says the ACE does not apply to the
  object holding it, which is a statement about the *parent*, not about what descends.
* an ``OBJECT_INHERIT``-only ACE still reaches a child **container** — as ``OI|IO|INHERITED``
  (``0x19``), so that it can carry on down to files — even though it grants nothing on that
  container. Dropping it would make every folder under a "files only" grant look like a
  boundary.
* ``NO_PROPAGATE_INHERIT`` clears ``OI``, ``CI``, ``NP`` and ``IO`` from the inherited copy,
  which is what makes the grandchild inherit nothing.

**The two exceptions, both of which split one ACE into two.**

* **A generic mask.** ``GENERIC_READ`` and its siblings are an indirection Windows cannot
  apply to an object without resolving through that object's generic mapping — so it writes
  both halves: an *effective* copy (mapped, every inheritance flag cleared) and a
  *propagating* copy (unmapped, ``INHERIT_ONLY``, still descending). This is not exotic:
  ``0xe0010000`` is the generic form of Modify and sits on almost every directory Explorer
  creates, so a projection that misses it reports every one of them as a boundary. See
  :func:`project_inherited_ace`.
* **A substituted trustee.** ``CREATOR OWNER`` (``S-1-3-0``) and ``CREATOR GROUP``
  (``S-1-3-1``) hand down the propagating copy with ``INHERIT_ONLY`` *preserved*, and the
  effective copy is an ACE naming whoever created the child — which is not a fact about the
  parent and is therefore not predicted. A directory beneath such a grant consequently
  reports ``acl_differs_from_parent``: a true statement about its DACL, and a misleading one
  about administrative intent. :data:`SUBSTITUTED_TRUSTEES` names the SIDs so a caller can
  say which case it is looking at.

**What this module is not.** It is not effective access. It resolves no Deny precedence,
grants the owner nothing, substitutes nothing for ``CREATOR OWNER``, and evaluates no
conditional ACE. It answers one question — *which ACEs descend, carrying which flags and
which mask* — which is the part of the inheritance algebra a tree scan needs and the part
Phase 4B builds on. The one generic expansion it performs (:func:`map_generic_rights`)
produces a comparison value that is never stored and never reported.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from enum import StrEnum
from typing import Final

from app.domain.access import GENERIC_RIGHT_BITS, AceFlag, NtfsAce, NtfsRight
from app.domain.acl_hash import AclAceFacts, NormalizedAcl, normalize_acl

__all__ = [
    "SUBSTITUTED_TRUSTEES",
    "AclBoundaryReason",
    "boundary_reason_for",
    "inherited_child_acl_hash",
    "map_generic_rights",
    "project_inherited_ace",
    "project_inherited_ace_flags",
    "project_inherited_acl",
    "projected_child_acl",
]


SUBSTITUTED_TRUSTEES: Final = frozenset({"S-1-3-0", "S-1-3-1"})
"""SIDs Windows replaces with a concrete principal when it materializes an inherited ACE.

``CREATOR OWNER`` and ``CREATOR GROUP``. A parent carrying one of these hands a child the
propagating half of the entry *plus* an ACE naming whoever created that child — which is not
a fact about the parent, so the projection cannot predict it and a directory beneath such a
grant shows as a boundary. Named here so the difference between "somebody changed the
permissions" and "Windows substituted the creator" stays answerable.

``OWNER RIGHTS`` (``S-1-3-4``) is deliberately **not** in this set. It looks like a sibling
and is not one: Windows stores and inherits it like any other trustee and resolves it against
the current owner at access time, so it projects normally.
"""

_PROPAGATING: Final = AceFlag.OBJECT_INHERIT | AceFlag.CONTAINER_INHERIT

# The file-system generic mapping (GENERIC_MAPPING for the File object type). Fixed by
# Windows, and the reason a parent's inheritable entry and the child's effective copy of it
# carry different masks while describing one grant.
_FILE_GENERIC_READ: Final = 0x00120089
_FILE_GENERIC_WRITE: Final = 0x00120116
_FILE_GENERIC_EXECUTE: Final = 0x001200A0
_FILE_ALL_ACCESS: Final = 0x001F01FF


class AclBoundaryReason(StrEnum):
    """Why a resource is reported as a place where permissions change.

    ``is_acl_boundary`` on its own is a verdict with no evidence, and a tree scan's whole
    output is verdicts. Recording *why* is what lets an operator tell "an administrator set
    permissions here" from "the collector could not see far enough to know" — two findings
    that call for completely different responses, and which a bare boolean renders
    identical.

    The four unknowable cases (:attr:`SCAN_ROOT`, :attr:`SHARE_ROOT`,
    :attr:`PARENT_UNREADABLE`, :attr:`PARENT_NULL_DACL`) all report ``is_acl_boundary`` as
    **true**. A boundary that is not there costs a scan one extra stored ACL; a boundary
    that is there and was reported false tells a later scan it may stop looking, which
    silently drops every permission change beneath it.
    """

    SHARE_ROOT = "share_root"
    """The directory a share publishes. Its parent lies outside the share — often outside
    anything ADG audits — so there is nothing to compare against."""

    SCAN_ROOT = "scan_root"
    """The walk started here, below a share root. The parent exists and was simply not
    read by this run, so the comparison was never possible."""

    PROTECTED_DACL = "protected_dacl"
    """``SE_DACL_PROTECTED``: the directory refuses inherited entries. A boundary by
    definition, and the only reason that is a property of the descriptor alone."""

    NULL_DACL = "null_dacl"
    """This resource has a NULL DACL — everyone has full access — which cannot be anything
    it inherited: inheritance produces entries, never the absence of a DACL."""

    PARENT_NULL_DACL = "parent_null_dacl"
    """The parent has a NULL DACL, which projects nothing. What a child of it holds comes
    from the creating process's default DACL, which is not a fact about the parent."""

    PARENT_UNREADABLE = "parent_unreadable"
    """The parent's DACL was not read — denied, absent, or only partly reported — so the
    projection could not be computed. Unknown, reported as a boundary."""

    ACL_DIFFERS_FROM_PARENT = "acl_differs_from_parent"
    """The DACL does not match what the parent projects onto a child of this kind. The
    ordinary finding: somebody set permissions here."""


def map_generic_rights(mask: int) -> int:
    """A file-system access mask with its generic bits replaced by what they stand for.

    ``GENERIC_READ`` and its siblings are not rights; they are an indirection that Windows
    resolves through the object type's *generic mapping* when it materializes an ACE onto a
    real object. For a file-system object that mapping is fixed, and it is the reason a
    directory's stored DACL and its parent's inheritable entry can carry different masks
    while describing exactly the same grant.

    **This does not license expanding generic rights anywhere else.** ADG stores masks
    exactly as read (:class:`app.domain.NtfsRight`), because a stored expansion would bake
    one interpretation into a fact. The expansion here is not stored: it exists so a
    prediction can be compared against what Windows actually wrote, and nothing else uses it.
    """
    specific = mask & ~int(GENERIC_RIGHT_BITS)
    if mask & int(NtfsRight.GENERIC_READ):
        specific |= _FILE_GENERIC_READ
    if mask & int(NtfsRight.GENERIC_WRITE):
        specific |= _FILE_GENERIC_WRITE
    if mask & int(NtfsRight.GENERIC_EXECUTE):
        specific |= _FILE_GENERIC_EXECUTE
    if mask & int(NtfsRight.GENERIC_ALL):
        specific |= _FILE_ALL_ACCESS
    return specific


def project_inherited_ace_flags(flags: int, *, for_container: bool) -> int | None:
    """The flag byte a child receives from an ordinary parent ACE, or ``None`` for nothing.

    "Ordinary" is doing work: this is the single-entry case, which holds for an ACE whose
    mask carries no generic bits and whose trustee Windows does not substitute. Those two
    exceptions each split one parent entry into two child entries, and
    :func:`project_inherited_ace` handles the general case. This function is kept because
    the single-entry rule is the one worth reading, and because it is what the measured
    table in the module docstring states.

    Args:
        flags: the parent ACE's raw ``ACE_HEADER.AceFlags`` byte. Its own ``INHERITED`` bit
            is irrelevant — an ACE the parent inherited propagates exactly as one set on
            the parent does — and is simply overwritten in the result.
        for_container: whether the child is a directory. A file receives no inheritance
            flags at all, because it has nothing below it to pass them to.

    Returns:
        The child's flag byte, always with ``INHERITED`` set, or ``None`` when this ACE does
        not descend to a child of that kind.
    """
    parent = AceFlag(flags & 0xFF)
    no_propagate = bool(parent & AceFlag.NO_PROPAGATE_INHERIT)

    if not for_container:
        # A file is a leaf: it either receives the entry or does not, and it never carries
        # inheritance flags of its own. NO_PROPAGATE_INHERIT does not withhold it — the
        # bit stops grandchildren, and a file's children do not exist.
        return int(AceFlag.INHERITED) if parent & AceFlag.OBJECT_INHERIT else None

    if parent & AceFlag.CONTAINER_INHERIT:
        if no_propagate:
            # Applies to this child and stops: OI, CI, NP and IO are all cleared.
            return int(AceFlag.INHERITED)
        # Keeps propagating with the same reach. INHERIT_ONLY is dropped, because the
        # entry does apply to the child container it just landed on.
        return int(AceFlag.INHERITED | (parent & _PROPAGATING))

    if parent & AceFlag.OBJECT_INHERIT:
        if no_propagate:
            # The entry is for immediate children that are files. This one is not.
            return None
        # It grants nothing *on* this container, which is what INHERIT_ONLY says, but it
        # has to be carried so the files below still receive it.
        return int(AceFlag.INHERITED | AceFlag.OBJECT_INHERIT | AceFlag.INHERIT_ONLY)

    return None


def project_inherited_ace(entry: AclAceFacts, *, for_container: bool) -> tuple[AclAceFacts, ...]:
    """What one parent ACE becomes on a child: nothing, one entry, or **two**.

    The two-entry case is not an edge case. It fires on any ACE carrying a generic right,
    which on a real Windows estate is most of them — ``0xe0010000`` (the generic form of
    Modify) appears on almost every directory created through Explorer. Missing it reports
    every one of those directories as a boundary, which is exactly the failure the
    projection exists to prevent, and it is invisible in any test whose fixture masks
    happen to be specific.

    **Why an ACE splits.** A generic mask is an indirection: Windows cannot apply it to an
    object without resolving it through that object's generic mapping. So when it
    materializes such an ACE onto a child it writes *both* halves of what the parent meant —
    the **effective** copy, mapped and with every inheritance flag cleared, and the
    **propagating** copy, unmapped and marked ``INHERIT_ONLY`` so it keeps descending. The
    parent's own DACL shows the same pair, which is why the arrangement is a fixed point
    rather than something that grows with depth.

    ``CREATOR OWNER`` and ``CREATOR GROUP`` split for a different reason and only halfway:
    the propagating copy descends with ``INHERIT_ONLY`` preserved, and the effective copy is
    an ACE naming *whoever created the child* — which is not a fact about the parent and is
    therefore not predicted here. A directory beneath such a grant consequently reports
    ``acl_differs_from_parent``: a true statement about its DACL, and a misleading one about
    administrative intent.

    Returns:
        The entries, in the order Windows writes them — effective first, then propagating.
        ``order_index`` is left unset; :func:`project_inherited_acl` renumbers.
    """
    parent = AceFlag(entry.ace_flags & 0xFF)
    container_inherit = bool(parent & AceFlag.CONTAINER_INHERIT)
    object_inherit = bool(parent & AceFlag.OBJECT_INHERIT)
    no_propagate = bool(parent & AceFlag.NO_PROPAGATE_INHERIT)

    generic = bool(entry.access_mask & int(GENERIC_RIGHT_BITS))
    substituted = entry.trustee_sid in SUBSTITUTED_TRUSTEES

    def copy(flags: int, mask: int) -> AclAceFacts:
        return AclAceFacts(
            trustee_sid=entry.trustee_sid,
            ace_type=entry.ace_type,
            access_mask=mask,
            ace_flags=flags,
        )

    if not for_container:
        # A file receives the effective copy or nothing. It never propagates, and a
        # substituted trustee's effective copy names the creator, which is unpredictable.
        if substituted or not object_inherit:
            return ()
        return (copy(int(AceFlag.INHERITED), map_generic_rights(entry.access_mask)),)

    if not (generic or substituted):
        flags = project_inherited_ace_flags(entry.ace_flags, for_container=True)
        return () if flags is None else (copy(flags, entry.access_mask),)

    projected: list[AclAceFacts] = []
    # The effective copy exists only where the entry applies to a child container, and only
    # where the trustee is knowable.
    if container_inherit and not substituted:
        projected.append(copy(int(AceFlag.INHERITED), map_generic_rights(entry.access_mask)))
    # The propagating copy carries the original mask unmapped, because it is still an
    # indirection for whatever object it eventually lands on.
    if (container_inherit or object_inherit) and not no_propagate:
        projected.append(
            copy(
                int(AceFlag.INHERITED | AceFlag.INHERIT_ONLY | (parent & _PROPAGATING)),
                entry.access_mask,
            )
        )
    return tuple(projected)


def project_inherited_acl(
    aces: Iterable[AclAceFacts | NtfsAce], *, for_container: bool
) -> tuple[AclAceFacts, ...]:
    """The entries a child inherits from a parent DACL, in the parent's relative order.

    Positions are renumbered from zero over the surviving entries. That is not a loss:
    :func:`app.domain.normalize_acl` reduces positions to their rank anyway, and it is what
    lets a projection built from database rows equal one built from a live descriptor.

    A parent entry that does not descend is dropped rather than represented, so an empty
    result means "this parent hands its children nothing", which is a perfectly ordinary
    DACL of explicit, non-inheritable entries. One that carries a generic right produces
    *two* — see :func:`project_inherited_ace` — which is why this cannot be a simple map.
    """
    entries = [
        item if isinstance(item, AclAceFacts) else AclAceFacts.from_ace(item) for item in aces
    ]
    # Sort by reported position so the projection does not depend on the order rows came
    # back in. Entries with no position sort last, by content, which keeps the result
    # deterministic without inventing an order the source never reported.
    ordered = sorted(
        entries,
        key=lambda entry: (entry.order_index is None, entry.order_index or 0, entry.content_line),
    )

    projected: list[AclAceFacts] = []
    for entry in ordered:
        for child in project_inherited_ace(entry, for_container=for_container):
            projected.append(replace(child, order_index=len(projected)))
    return tuple(projected)


def projected_child_acl(
    *,
    dacl_present: bool,
    aces: Iterable[AclAceFacts | NtfsAce] = (),
    for_container: bool = True,
) -> NormalizedAcl | None:
    """The normalized DACL a cleanly inheriting child would carry, or ``None``.

    ``None`` means the parent's DACL cannot project: a NULL DACL produces no entries at
    all, and what a child of it ends up holding comes from the creating process's default
    DACL rather than from the parent.

    The projected document is always ``dacl_present=true`` and ``dacl_protected=false``.
    Inheritance produces a present DACL even when it produces no entries, and a child that
    *is* protected is a boundary on that basis alone — so a projection that claimed
    protection could only ever make a boundary invisible.
    """
    if not dacl_present:
        return None
    return normalize_acl(
        dacl_present=True,
        dacl_protected=False,
        aces=project_inherited_acl(aces, for_container=for_container),
    )


def inherited_child_acl_hash(
    *,
    dacl_present: bool,
    aces: Iterable[AclAceFacts | NtfsAce] = (),
    for_container: bool = True,
) -> str | None:
    """The digest of :func:`projected_child_acl`, for callers that only need to compare."""
    projection = projected_child_acl(
        dacl_present=dacl_present, aces=aces, for_container=for_container
    )
    return None if projection is None else projection.digest


def boundary_reason_for(
    *,
    is_share_root: bool,
    is_scan_root: bool,
    dacl_present: bool,
    dacl_protected: bool,
    acl_hash: str | None,
    parent_dacl_present: bool | None,
    parent_projection: str | None,
) -> AclBoundaryReason | None:
    """Why this resource is a boundary, or ``None`` when it is carrying what it inherited.

    The order of the tests is the order of certainty, and it is deliberate: a protected
    DACL is a boundary whatever the projection says, and a directory whose parent nobody
    read is unknown rather than unchanged.

    Args:
        is_share_root: the path is ``\\\\server\\share`` itself.
        is_scan_root: the walk started here, so no parent was read.
        dacl_present: ``False`` is a NULL DACL on this resource.
        dacl_protected: ``SE_DACL_PROTECTED`` on this resource.
        acl_hash: this resource's own digest, or ``None`` when the DACL was only partly
            read and no digest could honestly be taken over it.
        parent_dacl_present: the parent's ``dacl_present``, or ``None`` when the parent was
            not read at all.
        parent_projection: :func:`inherited_child_acl_hash` for the parent, or ``None``
            when the parent projects nothing or was not read.

    Returns:
        The reason, or ``None`` — which is the *only* value that means "not a boundary".
    """
    if dacl_protected:
        return AclBoundaryReason.PROTECTED_DACL
    if not dacl_present:
        return AclBoundaryReason.NULL_DACL
    if is_share_root:
        return AclBoundaryReason.SHARE_ROOT
    if is_scan_root:
        return AclBoundaryReason.SCAN_ROOT
    if parent_dacl_present is None:
        return AclBoundaryReason.PARENT_UNREADABLE
    if not parent_dacl_present:
        return AclBoundaryReason.PARENT_NULL_DACL
    # Either side missing a digest means the comparison was never made. An unread ACL is
    # not an unchanged one, and reporting it as unchanged is what would let a later scan
    # stop at a directory whose permissions nobody has established.
    if parent_projection is None or acl_hash is None:
        return AclBoundaryReason.PARENT_UNREADABLE
    if acl_hash != parent_projection:
        return AclBoundaryReason.ACL_DIFFERS_FROM_PARENT
    return None
