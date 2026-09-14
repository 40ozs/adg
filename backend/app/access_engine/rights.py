"""Canonical SMB and NTFS rights algebra.

This module is the *only* place in ADG where an access mask is combined, compared, or
turned into a human label. It is deliberately framework-free: no FastAPI, no SQLAlchemy, no
I/O. Everything here is a pure function of its arguments.

**The mask is the truth; a label is a rendering.** Phase 0A stores what a descriptor
contained (`app.domain.access`). This module answers "what does that mask actually permit,
and what should we call it?" — two different questions that must never be allowed to merge.
The failure mode being designed against is the one every permissions report eventually
commits: collapsing a mask to the string ``"Modify"`` and then comparing strings. A mask of
``Modify | WRITE_DAC`` is not Modify — its holder can grant themselves Full Control — and a
string comparison cannot see the difference.

Three rules hold throughout:

1. **Layers do not mix implicitly.** An SMB share mask and an NTFS mask are different types
   of claim about different objects. :class:`RightsMask` carries its :class:`RightsLayer`,
   and union, intersection, subtraction, and comparison all refuse to operate across
   layers. The only way to combine them is :func:`effective_rights`, which is explicit
   about producing a third thing: what a user can do *over SMB*, which is the intersection.
2. **Display can never grant.** Every category label is defined by a *required* mask, and a
   mask is assigned a category only when it is a superset of that requirement. A label
   therefore always denotes a subset of the rights actually present. Rights that exceed the
   label are reported separately rather than rounded away (see :class:`RightsSummary`).
3. **Nothing is silently dropped.** Generic bits are expanded through the file-system
   generic mapping, and the expansion is recorded. Bits that match no known right are kept
   as ``unrecognized_bits``. ``MAXIMUM_ALLOWED`` is recognized as *indeterminate* rather
   than guessed at.

See ``docs/architecture/rights-model.md`` for the label-derivation rules in prose, and
ADR-0005 for why rights are represented this way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import IntFlag, StrEnum
from typing import Final

from app.domain.access import AclLayer, NtfsRight, SharePermission, SmbShareAce
from app.domain.errors import DomainValidationError

MAX_ACCESS_MASK: Final = 0xFFFFFFFF


class RightsError(DomainValidationError):
    """A rights operation was asked for something it cannot answer."""


class RightsLayerError(RightsError):
    """Two masks from different authorization layers were combined or compared.

    This is always a bug at the call site, never a data problem: SMB and NTFS rights answer
    different questions, and an intersection of them is a third kind of value. Use
    :func:`effective_rights` to cross layers deliberately.
    """


# --------------------------------------------------------------------------------------
# Bit definitions beyond the observation model
# --------------------------------------------------------------------------------------


class ExtendedRight(IntFlag):
    """Access-mask bits that are not object rights and cannot be expanded statically.

    Phase 0A's :class:`~app.domain.access.NtfsRight` covers the object-specific, standard,
    and generic rights a collector reads from a DACL. Two further bits occur in real
    descriptors and in SDDL, and both mean something the rights algebra must not guess at:

    * ``ACCESS_SYSTEM_SECURITY`` governs the SACL, not the data. It is a right, but not one
      that maps onto any Read/Write/Modify category.
    * ``MAXIMUM_ALLOWED`` is a *request*, not a grant: Windows resolves it at open time to
      whatever the rest of the descriptor permits. A stored mask carrying it has no fixed
      meaning, so ADG reports the rights set as indeterminate rather than inventing one.

    They are defined here rather than added to ``NtfsRight`` because ``NtfsRight`` is an
    accepted Phase 0A contract: widening it would silently change what
    ``NtfsAce.unrecognized_bits`` reports about stored observations.
    """

    NONE = 0x00000000
    ACCESS_SYSTEM_SECURITY = 0x01000000
    MAXIMUM_ALLOWED = 0x02000000


GENERIC_RIGHT_BITS: Final = int(
    NtfsRight.GENERIC_ALL
    | NtfsRight.GENERIC_EXECUTE
    | NtfsRight.GENERIC_WRITE
    | NtfsRight.GENERIC_READ
)

FILE_GENERIC_READ: Final = 0x00120089
"""``READ_DATA | READ_EA | READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE``."""

FILE_GENERIC_WRITE: Final = 0x00120116
"""``WRITE_DATA | APPEND_DATA | WRITE_EA | WRITE_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE``."""

FILE_GENERIC_EXECUTE: Final = 0x001200A0
"""``EXECUTE | READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE``."""

FILE_ALL_ACCESS: Final = 0x001F01FF
"""``STANDARD_RIGHTS_REQUIRED | SYNCHRONIZE | 0x1FF`` — every file-system right."""

FILE_SYSTEM_GENERIC_MAPPING: Final[dict[NtfsRight, int]] = {
    NtfsRight.GENERIC_READ: FILE_GENERIC_READ,
    NtfsRight.GENERIC_WRITE: FILE_GENERIC_WRITE,
    NtfsRight.GENERIC_EXECUTE: FILE_GENERIC_EXECUTE,
    NtfsRight.GENERIC_ALL: FILE_ALL_ACCESS,
}
"""The ``GENERIC_MAPPING`` Windows applies to file and directory objects.

Applied at evaluation time only. Collectors store generic bits verbatim (ADR-0003); this
table is how the engine interprets them, and the interpretation is recorded alongside the
result so that a report can always show the original mask.
"""

SYNCHRONIZE_BIT: Final = int(NtfsRight.SYNCHRONIZE)
"""Excluded from every display category — see :data:`CATEGORY_REQUIRED_MASKS`."""

ESCALATION_RIGHTS: Final = NtfsRight.WRITE_DAC | NtfsRight.WRITE_OWNER
"""Rights whose holder can grant themselves any other right.

Named here because they are the rights most often hidden behind a friendly label: a mask of
``Modify | WRITE_DAC`` summarizes as Modify, and the holder can rewrite the DACL at will.
:attr:`RightsSummary.escalation_rights` keeps them visible.
"""

_KNOWN_BITS: Final = int(
    NtfsRight.READ_DATA
    | NtfsRight.WRITE_DATA
    | NtfsRight.APPEND_DATA
    | NtfsRight.READ_EA
    | NtfsRight.WRITE_EA
    | NtfsRight.EXECUTE
    | NtfsRight.DELETE_CHILD
    | NtfsRight.READ_ATTRIBUTES
    | NtfsRight.WRITE_ATTRIBUTES
    | NtfsRight.DELETE
    | NtfsRight.READ_CONTROL
    | NtfsRight.WRITE_DAC
    | NtfsRight.WRITE_OWNER
    | NtfsRight.SYNCHRONIZE
    | NtfsRight.GENERIC_ALL
    | NtfsRight.GENERIC_EXECUTE
    | NtfsRight.GENERIC_WRITE
    | NtfsRight.GENERIC_READ
) | int(ExtendedRight.ACCESS_SYSTEM_SECURITY | ExtendedRight.MAXIMUM_ALLOWED)


class RightsLayer(StrEnum):
    """Which question a :class:`RightsMask` answers.

    Distinct from :class:`~app.domain.access.AclLayer`, which labels a stored ACE. An ACE
    belongs to one of two ACLs; a computed rights set can also be ``EFFECTIVE``, which is
    not an ACL at all but the result of combining the two.
    """

    SMB_SHARE = "smb_share"
    """What the share ACL permits. Applies only to access arriving over SMB."""

    NTFS = "ntfs"
    """What the file-system DACL permits, regardless of how the object is reached."""

    EFFECTIVE = "effective"
    """The result of combining layers for a specific access path."""

    @classmethod
    def from_acl_layer(cls, layer: AclLayer) -> RightsLayer:
        """Map a stored ACE's layer onto the corresponding rights layer."""
        if layer is AclLayer.SMB_SHARE:
            return cls.SMB_SHARE
        return cls.NTFS


class AccessPath(StrEnum):
    """How a principal reaches the object, which decides whether the share ACL applies."""

    REMOTE_SMB = "remote_smb"
    """Over the network through a share: both ACLs apply, and the result is the intersection."""

    LOCAL = "local"
    """At the console, over RDP, or from a service on the host: the share ACL is not consulted."""


# --------------------------------------------------------------------------------------
# The mask value object
# --------------------------------------------------------------------------------------


def _validate_mask_value(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RightsError(
            f"A rights mask must be an integer; received {type(value).__name__}.",
            field="value",
        )
    if value < 0 or value > MAX_ACCESS_MASK:
        raise RightsError(
            f"A rights mask must be an unsigned 32-bit value; received {value}.",
            value=value,
            field="value",
        )
    return value


@dataclass(frozen=True, slots=True, order=False)
class RightsMask:
    """A set of access rights, tagged with the layer it belongs to.

    The wrapped integer is authoritative and is never modified in place. Two masks are equal
    only when both their value and their layer match, so an SMB mask can never compare equal
    to an NTFS mask that happens to carry the same bits — a distinction that string labels
    lose, since SMB ``Change`` and NTFS ``Modify`` are the same bits under different names.
    """

    value: int
    layer: RightsLayer = RightsLayer.NTFS

    def __post_init__(self) -> None:
        _validate_mask_value(self.value)
        if not isinstance(self.layer, RightsLayer):
            raise RightsError(
                f"layer must be a RightsLayer; received {type(self.layer).__name__}.",
                field="layer",
            )

    # -- constructors ------------------------------------------------------------------

    @classmethod
    def ntfs(cls, value: int | NtfsRight = 0) -> RightsMask:
        """A mask read from, or destined for, a file-system DACL."""
        return cls(int(value), RightsLayer.NTFS)

    @classmethod
    def smb(cls, value: int | NtfsRight = 0) -> RightsMask:
        """A mask read from, or destined for, a share ACL."""
        return cls(int(value), RightsLayer.SMB_SHARE)

    @classmethod
    def effective(cls, value: int | NtfsRight = 0) -> RightsMask:
        """A computed result. Not an ACL; see :func:`effective_rights`."""
        return cls(int(value), RightsLayer.EFFECTIVE)

    @classmethod
    def empty(cls, layer: RightsLayer = RightsLayer.NTFS) -> RightsMask:
        """The zero mask: no rights at all."""
        return cls(0, layer)

    # -- inspection --------------------------------------------------------------------

    @property
    def rights(self) -> NtfsRight:
        """The recognized object, standard, and generic rights present in the mask.

        A view, not the truth: bits matching no known right are absent here and present in
        :attr:`unrecognized_bits`. Never reconstruct a mask from this property alone.
        """
        known = 0
        for right in NtfsRight:
            bit = int(right)
            if bit and self.value & bit == bit:
                known |= bit
        return NtfsRight(known)

    @property
    def extended(self) -> ExtendedRight:
        """``ACCESS_SYSTEM_SECURITY`` and ``MAXIMUM_ALLOWED``, when present."""
        known = 0
        for right in ExtendedRight:
            bit = int(right)
            if bit and self.value & bit == bit:
                known |= bit
        return ExtendedRight(known)

    @property
    def unrecognized_bits(self) -> int:
        """Mask bits matching no right ADG knows. Reported, never discarded."""
        return self.value & ~_KNOWN_BITS

    @property
    def is_empty(self) -> bool:
        return self.value == 0

    @property
    def has_generic_rights(self) -> bool:
        """True while generic bits are still unexpanded."""
        return bool(self.value & GENERIC_RIGHT_BITS)

    @property
    def is_indeterminate(self) -> bool:
        """True when the mask carries ``MAXIMUM_ALLOWED``.

        Its meaning is resolved by Windows at open time, so the rights set has no fixed value.
        """
        return bool(self.value & int(ExtendedRight.MAXIMUM_ALLOWED))

    @property
    def escalation_rights(self) -> NtfsRight:
        """``WRITE_DAC`` and ``WRITE_OWNER``, when present: the self-grant paths."""
        return self.rights & ESCALATION_RIGHTS

    def expand_generics(self) -> RightsMask:
        """Resolve generic bits through the file-system generic mapping.

        Returns a mask of the same layer with the ``GENERIC_*`` bits replaced by the rights
        they denote. Idempotent, and a no-op on a mask that carries none. The generic bits
        are cleared because they have been interpreted; the original mask is unchanged, and
        :func:`normalize_ntfs_mask` records both forms together.
        """
        if not self.has_generic_rights:
            return self
        expanded = self.value & ~GENERIC_RIGHT_BITS
        for generic, concrete in FILE_SYSTEM_GENERIC_MAPPING.items():
            if self.value & int(generic):
                expanded |= concrete
        return RightsMask(expanded, self.layer)

    # -- algebra -----------------------------------------------------------------------

    def _require_same_layer(self, other: RightsMask, operation: str) -> None:
        if not isinstance(other, RightsMask):
            raise RightsError(
                f"Cannot {operation} a rights mask with {type(other).__name__}.",
                field="other",
            )
        if self.layer is not other.layer:
            raise RightsLayerError(
                f"Cannot {operation} a {self.layer.value!r} mask with a "
                f"{other.layer.value!r} mask. SMB and NTFS rights are separate claims; "
                "use effective_rights() to combine them for a specific access path.",
                field="layer",
            )

    def union(self, other: RightsMask) -> RightsMask:
        """Every right in either mask. The model for accumulating multiple Allow ACEs."""
        self._require_same_layer(other, "union")
        return RightsMask(self.value | other.value, self.layer)

    def intersection(self, other: RightsMask) -> RightsMask:
        """Only rights present in both."""
        self._require_same_layer(other, "intersect")
        return RightsMask(self.value & other.value, self.layer)

    def difference(self, other: RightsMask) -> RightsMask:
        """Rights in this mask that ``other`` does not contain. The model for Deny masking."""
        self._require_same_layer(other, "subtract")
        return RightsMask(self.value & ~other.value, self.layer)

    def issubset(self, other: RightsMask) -> bool:
        """True when every right here is also in ``other``."""
        self._require_same_layer(other, "compare")
        return self.value & other.value == self.value

    def issuperset(self, other: RightsMask) -> bool:
        """True when this mask contains every right in ``other``."""
        self._require_same_layer(other, "compare")
        return self.value & other.value == other.value

    def isdisjoint(self, other: RightsMask) -> bool:
        self._require_same_layer(other, "compare")
        return self.value & other.value == 0

    def grants(self, right: NtfsRight | ExtendedRight | int) -> bool:
        """True when every bit of ``right`` is present. Use this instead of a label test."""
        bits = int(right)
        _validate_mask_value(bits)
        return self.value & bits == bits

    def __or__(self, other: RightsMask) -> RightsMask:
        return self.union(other)

    def __and__(self, other: RightsMask) -> RightsMask:
        return self.intersection(other)

    def __sub__(self, other: RightsMask) -> RightsMask:
        return self.difference(other)

    def __contains__(self, right: object) -> bool:
        """Membership test for a right. Raises on a mask — use :meth:`issuperset` instead.

        Silently answering ``False`` for a cross-layer question would be exactly the class
        of bug this module exists to prevent, so the ambiguous case is an error.
        """
        if isinstance(right, RightsMask):
            raise RightsError(
                "Use issuperset() to compare two masks; `in` tests a single right so that "
                "a layer mismatch cannot be answered silently.",
                field="right",
            )
        if isinstance(right, NtfsRight | ExtendedRight | int) and not isinstance(right, bool):
            return self.grants(right)
        raise RightsError(
            f"Cannot test membership of {type(right).__name__} in a rights mask.",
            field="right",
        )

    def __bool__(self) -> bool:
        return self.value != 0

    def __str__(self) -> str:
        return f"0x{self.value:08X}"

    def __repr__(self) -> str:
        return f"RightsMask(0x{self.value:08X}, {self.layer.value})"


# --------------------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NormalizedRights:
    """A mask as observed, alongside the interpreted form, with the interpretation recorded.

    Both forms are kept because a report must be able to show what the descriptor actually
    said. "Full Control (recorded as GENERIC_ALL)" is a more useful finding than either half
    on its own, and an operator comparing ADG against the Windows ACL editor needs the raw
    value to reconcile.
    """

    raw: RightsMask
    normalized: RightsMask
    expanded_generics: NtfsRight
    unrecognized_bits: int
    indeterminate: bool

    @property
    def layer(self) -> RightsLayer:
        return self.raw.layer

    @property
    def was_generic(self) -> bool:
        return bool(self.expanded_generics)

    @property
    def is_fully_understood(self) -> bool:
        """True when every bit was recognized and nothing is resolved at open time."""
        return self.unrecognized_bits == 0 and not self.indeterminate


def normalize_mask(mask: RightsMask) -> NormalizedRights:
    """Expand generic bits and record what could not be interpreted."""
    normalized = mask.expand_generics()
    return NormalizedRights(
        raw=mask,
        normalized=normalized,
        expanded_generics=mask.rights & NtfsRight(GENERIC_RIGHT_BITS),
        unrecognized_bits=mask.unrecognized_bits,
        indeterminate=mask.is_indeterminate,
    )


def normalize_ntfs_mask(value: int | NtfsRight) -> NormalizedRights:
    """Normalize a raw NTFS access mask, such as ``NtfsAce.access_mask``."""
    return normalize_mask(RightsMask.ntfs(value))


def normalize_share_mask(value: int | NtfsRight) -> NormalizedRights:
    """Normalize a raw share-ACL access mask."""
    return normalize_mask(RightsMask.smb(value))


SHARE_LEVEL_MASKS: Final[dict[SharePermission, int]] = {
    SharePermission.READ: 0x001200A9,
    SharePermission.CHANGE: 0x001301BF,
    SharePermission.FULL: FILE_ALL_ACCESS,
}
"""The access mask each reported share level denotes.

Identical to ``app.domain.access.SHARE_PERMISSION_MASKS``, which records the mapping as
documentation next to the observation types; this is the copy the algebra applies. The
duplication is checked by a test so the two cannot drift.

Note what the numbers say: SMB ``Read`` is the same mask as NTFS ``Read & Execute``, and SMB
``Change`` is the same mask as NTFS ``Modify``. The friendly names differ, the rights do
not — which is precisely why the two layers must be compared by mask and never by label.
"""


def normalize_share_permission(level: SharePermission) -> RightsMask:
    """The share-layer mask denoted by one of the three reported permission levels."""
    if level not in SHARE_LEVEL_MASKS:
        raise RightsError(f"Unknown share permission level {level!r}.", value=level, field="level")
    return RightsMask.smb(SHARE_LEVEL_MASKS[level])


def normalize_share_ace(ace: SmbShareAce) -> NormalizedRights:
    """Normalize a share ACE reported either as a mask or as a permission level.

    ``SmbShareAce`` records exactly one of the two forms, because that is what its source
    provided. Both arrive here as a share-layer mask; the ACE's own Allow/Deny type is *not*
    applied, since combining ACEs is the resolver's job, not the algebra's.
    """
    if ace.access_mask is not None:
        return normalize_share_mask(ace.access_mask)
    if ace.permission is not None:
        return normalize_mask(normalize_share_permission(ace.permission))
    raise RightsError(  # pragma: no cover - SmbShareAce forbids this at construction
        "A share ACE carried neither an access mask nor a permission level.",
        field="access_mask",
    )


# --------------------------------------------------------------------------------------
# Set operations over many masks
# --------------------------------------------------------------------------------------


def union_all(masks: Iterable[RightsMask], *, layer: RightsLayer | None = None) -> RightsMask:
    """Union of every mask. ``layer`` supplies the result for an empty iterable."""
    return _fold(masks, layer=layer, combine=RightsMask.union, identity=0)


def intersect_all(masks: Iterable[RightsMask], *, layer: RightsLayer | None = None) -> RightsMask:
    """Intersection of every mask. An empty iterable yields the full mask, not the empty one.

    That is the correct identity for intersection — "nothing constrains this yet" — but it
    is a dangerous default for an authorization decision, so callers that may pass an empty
    sequence must decide explicitly what no-constraints means for them.
    """
    return _fold(masks, layer=layer, combine=RightsMask.intersection, identity=MAX_ACCESS_MASK)


def _fold(
    masks: Iterable[RightsMask],
    *,
    layer: RightsLayer | None,
    combine: Callable[[RightsMask, RightsMask], RightsMask],
    identity: int,
) -> RightsMask:
    accumulator: RightsMask | None = None
    for mask in masks:
        if not isinstance(mask, RightsMask):
            raise RightsError(
                f"Expected RightsMask values; received {type(mask).__name__}.", field="masks"
            )
        if layer is not None and mask.layer is not layer:
            raise RightsLayerError(
                f"Expected {layer.value!r} masks; received a {mask.layer.value!r} mask.",
                field="layer",
            )
        accumulator = mask if accumulator is None else combine(accumulator, mask)
    if accumulator is not None:
        return accumulator
    if layer is None:
        raise RightsError(
            "Cannot fold an empty sequence of masks without an explicit layer: the result "
            "would have no layer, and a layerless rights value must not exist.",
            field="layer",
        )
    return RightsMask(identity, layer)


def apply_deny(allowed: RightsMask, denied: RightsMask) -> RightsMask:
    """Remove denied rights from an accumulated grant.

    This is the **canonical-ACL resolver model**: accumulate every Allow, accumulate every
    Deny, subtract. It is exact for a DACL in canonical order (Deny ACEs first), which is
    what Windows produces and what the ACL editor maintains.

    It is *not* a general evaluation of a non-canonical DACL, where Windows stops at the
    first ACE that satisfies the request and an Allow placed before a Deny therefore wins.
    ADG deliberately treats Deny as winning in that case: it is the conservative reading for
    a report about who can reach data, and a non-canonical ACL is itself a finding that the
    Phase 4B resolver raises separately. See ``docs/architecture/rights-model.md``.
    """
    return allowed.difference(denied)


def resolve_canonical(
    allow_masks: Iterable[RightsMask],
    deny_masks: Iterable[RightsMask],
    *,
    layer: RightsLayer,
) -> RightsMask:
    """Apply the canonical-ACL model to pre-selected Allow and Deny masks.

    Selection — which ACEs name a principal, which apply to this object, which arrive by
    inheritance — is the resolver's work in Phase 4B. This function performs only the
    arithmetic, on masks a caller has already chosen.
    """
    allowed = union_all(allow_masks, layer=layer)
    denied = union_all(deny_masks, layer=layer)
    return apply_deny(allowed, denied)


# --------------------------------------------------------------------------------------
# Crossing the layers
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EffectiveRights:
    """What a principal can do to an object by a given access path, and why.

    The inputs are retained so a report can explain a narrowing. "NTFS grants Modify but the
    share grants Read, so the user has Read" is an actionable statement; "the user has Read"
    sends an administrator to the wrong ACL.
    """

    path: AccessPath
    rights: RightsMask
    ntfs_rights: RightsMask
    share_rights: RightsMask | None = None

    def __post_init__(self) -> None:
        if self.rights.layer is not RightsLayer.EFFECTIVE:
            raise RightsError(
                "Effective rights must carry the EFFECTIVE layer; they are a computed "
                "result, not an ACL.",
                field="rights",
            )
        if self.ntfs_rights.layer is not RightsLayer.NTFS:
            raise RightsLayerError(
                f"ntfs_rights must be an NTFS mask; received {self.ntfs_rights.layer.value!r}.",
                field="ntfs_rights",
            )
        if self.share_rights is not None and self.share_rights.layer is not RightsLayer.SMB_SHARE:
            raise RightsLayerError(
                f"share_rights must be a share mask; received {self.share_rights.layer.value!r}.",
                field="share_rights",
            )
        if (self.path is AccessPath.REMOTE_SMB) != (self.share_rights is not None):
            raise RightsError(
                "A remote SMB result must record the share rights it was limited by, and a "
                "local result must not: the share ACL is not consulted for local access.",
                field="share_rights",
            )

    @property
    def limited_by_share(self) -> bool:
        """True when the share ACL removed a right NTFS granted."""
        if self.share_rights is None:
            return False
        return bool(self.ntfs_rights.value & ~self.share_rights.value)

    @property
    def limited_by_ntfs(self) -> bool:
        """True when NTFS removed a right the share granted."""
        if self.share_rights is None:
            return False
        return bool(self.share_rights.value & ~self.ntfs_rights.value)

    @property
    def is_indeterminate(self) -> bool:
        """True when any input carried ``MAXIMUM_ALLOWED``."""
        share_indeterminate = self.share_rights is not None and self.share_rights.is_indeterminate
        return self.ntfs_rights.is_indeterminate or share_indeterminate

    def summarize(self) -> RightsSummary:
        return summarize(self.rights)


def effective_rights(
    *,
    ntfs: RightsMask,
    path: AccessPath,
    share: RightsMask | None = None,
) -> EffectiveRights:
    """Combine the layers for one access path.

    Over SMB both ACLs apply and the result is their intersection: a share granting Full
    Control cannot widen NTFS, and NTFS Full Control is unreachable through a share granting
    Read. Locally the share ACL is not consulted at all, which is why a restrictive share is
    never a substitute for NTFS permissions — anyone who can log on to the server bypasses it.

    Generic bits are expanded on both inputs before intersecting; intersecting raw generic
    bits with specific ones would produce zero and silently under-report access.
    """
    if ntfs.layer is not RightsLayer.NTFS:
        raise RightsLayerError(
            f"ntfs must be an NTFS mask; received {ntfs.layer.value!r}.", field="ntfs"
        )
    if path is AccessPath.LOCAL:
        if share is not None:
            raise RightsError(
                "Local access does not consult the share ACL; passing share rights for a "
                "local path would imply a restriction Windows does not apply.",
                field="share",
            )
        resolved = ntfs.expand_generics()
        return EffectiveRights(
            path=path,
            rights=RightsMask.effective(resolved.value),
            ntfs_rights=ntfs,
        )
    if share is None:
        raise RightsError(
            "Remote SMB access requires the share rights: without them the share ACL would "
            "be treated as unrestricted, which over-reports access.",
            field="share",
        )
    if share.layer is not RightsLayer.SMB_SHARE:
        raise RightsLayerError(
            f"share must be a share mask; received {share.layer.value!r}.", field="share"
        )
    combined = share.expand_generics().value & ntfs.expand_generics().value
    return EffectiveRights(
        path=path,
        rights=RightsMask.effective(combined),
        ntfs_rights=ntfs,
        share_rights=share,
    )


# --------------------------------------------------------------------------------------
# Display categories
# --------------------------------------------------------------------------------------


class RightsCategory(StrEnum):
    """A display label. Never an authorization input.

    Each category names a *required* mask (:data:`CATEGORY_REQUIRED_MASKS`). A mask is
    assigned a category only when it contains every required bit, so a label always denotes
    a subset of the rights actually held and can never imply access that is not there.
    """

    NONE = "none"
    TRAVERSE = "traverse"
    READ = "read"
    WRITE = "write"
    READ_EXECUTE = "read_execute"
    MODIFY = "modify"
    FULL_CONTROL = "full_control"
    SPECIAL = "special"
    """Rights are held, but they reach no named category."""


CATEGORY_REQUIRED_MASKS: Final[dict[RightsCategory, int]] = {
    RightsCategory.FULL_CONTROL: 0x000F01FF,
    RightsCategory.MODIFY: 0x000301BF,
    RightsCategory.READ_EXECUTE: 0x000200A9,
    RightsCategory.WRITE: 0x00000116,
    RightsCategory.READ: 0x00020089,
    RightsCategory.TRAVERSE: 0x00000020,
}
"""The bits a mask must contain to be described by each category, highest category first.

These are the Windows ACL-editor composites with one deliberate exception: ``SYNCHRONIZE``
is excluded from every requirement. It is a wait-handle primitive rather than a right over
data, it is present in almost every real ACE, and letting its absence demote ``Full Control``
to ``Special permissions`` would produce noise, not information. It is likewise ignored when
computing the rights a label does not cover, so it never appears as a special permission.

``List folder contents`` is deliberately absent: it is not a distinct mask but
``Read & Execute`` inherited by containers only, so it is a property of the ACE's inheritance
flags. Representing it here would invent a mask distinction that does not exist.
"""

_CATEGORY_LADDER: Final[tuple[RightsCategory, ...]] = tuple(CATEGORY_REQUIRED_MASKS)

_CATEGORY_DISPLAY_NAMES: Final[dict[RightsCategory, str]] = {
    RightsCategory.NONE: "No access",
    RightsCategory.TRAVERSE: "Traverse",
    RightsCategory.READ: "Read",
    RightsCategory.WRITE: "Write",
    RightsCategory.READ_EXECUTE: "Read & Execute",
    RightsCategory.MODIFY: "Modify",
    RightsCategory.FULL_CONTROL: "Full Control",
    RightsCategory.SPECIAL: "Special permissions",
}


def category_display_name(category: RightsCategory) -> str:
    """The human name for a category, in American English, as the Windows ACL editor spells it."""
    return _CATEGORY_DISPLAY_NAMES[category]


@dataclass(frozen=True, slots=True)
class RightsSummary:
    """A mask rendered for a human, with everything the rendering does not cover.

    A single label is never enough. ``Modify`` and ``Modify`` plus ``WRITE_DAC`` would carry
    the same label, so the extra rights are reported beside it and
    :attr:`escalation_rights` names the ones that matter most. Consumers displaying
    :attr:`primary` alone must also surface :attr:`is_exact`.
    """

    raw: RightsMask
    normalized: RightsMask
    primary: RightsCategory
    categories: tuple[RightsCategory, ...]
    covered_mask: int
    extra_bits: int
    unrecognized_bits: int
    indeterminate: bool

    @property
    def is_exact(self) -> bool:
        """True when the categories account for every right in the mask."""
        return self.extra_bits == 0 and not self.indeterminate

    @property
    def extra_rights(self) -> NtfsRight:
        """Recognized rights the categories do not cover."""
        return RightsMask(self.extra_bits, self.normalized.layer).rights

    @property
    def escalation_rights(self) -> NtfsRight:
        """``WRITE_DAC`` / ``WRITE_OWNER`` present anywhere in the mask."""
        return self.normalized.escalation_rights

    @property
    def was_generic(self) -> bool:
        return self.raw.has_generic_rights

    @property
    def special_permission_bits(self) -> int:
        """Excess rights that are genuinely permissions, for rendering.

        ``MAXIMUM_ALLOWED`` is excluded: it sits in :attr:`extra_bits` because it is a bit
        the categories do not cover and nothing may be dropped, but it is a request marker
        rather than a permission, and the label reports it through its own clause. Counting
        it twice would read as "plus special permissions" for a mask that has none.
        """
        return self.extra_bits & ~int(ExtendedRight.MAXIMUM_ALLOWED)

    @property
    def label(self) -> str:
        """A one-line rendering that never overstates the rights held."""
        if self.primary is RightsCategory.NONE:
            return _CATEGORY_DISPLAY_NAMES[RightsCategory.NONE]
        if self.primary is RightsCategory.SPECIAL:
            base = _CATEGORY_DISPLAY_NAMES[RightsCategory.SPECIAL]
        else:
            base = ", ".join(_CATEGORY_DISPLAY_NAMES[c] for c in self.categories)
            if self.special_permission_bits:
                base = f"{base} (plus special permissions)"
        if self.indeterminate:
            base = f"{base} (indeterminate: MAXIMUM_ALLOWED)"
        return base


def _contained_categories(mask_value: int) -> tuple[RightsCategory, ...]:
    contained = [
        category
        for category in _CATEGORY_LADDER
        if mask_value & CATEGORY_REQUIRED_MASKS[category] == CATEGORY_REQUIRED_MASKS[category]
    ]
    maximal: list[RightsCategory] = []
    for category in contained:
        required = CATEGORY_REQUIRED_MASKS[category]
        if any(
            other is not category and required & CATEGORY_REQUIRED_MASKS[other] == required
            for other in contained
        ):
            continue
        maximal.append(category)
    return tuple(maximal)


def summarize(mask: RightsMask) -> RightsSummary:
    """Derive display categories from a mask, without ever exceeding it.

    The derivation, in full:

    1. Expand generic bits, so ``GENERIC_ALL`` is described rather than called special.
    2. Collect every category whose required mask is a subset of the result.
    3. Drop any collected category that is contained in another — ``Full Control`` implies
       ``Modify``, and reporting both is noise.
    4. Take :attr:`RightsSummary.primary` from the remaining categories in ladder order,
       broadest first.
    5. Report every remaining right as ``extra_bits``, ignoring ``SYNCHRONIZE``.

    Steps 2 and 5 are what make the safety property hold: the categories are subsets of the
    mask by construction, and the bits they do not cover are reported rather than dropped.
    """
    if not isinstance(mask, RightsMask):
        raise RightsError(
            f"summarize() expects a RightsMask; received {type(mask).__name__}.", field="mask"
        )
    normalized = mask.expand_generics()
    categories = _contained_categories(normalized.value)
    covered = 0
    for category in categories:
        covered |= CATEGORY_REQUIRED_MASKS[category]
    extra = normalized.value & ~covered & ~SYNCHRONIZE_BIT
    if categories:
        primary = categories[0]
    elif normalized.value & ~SYNCHRONIZE_BIT:
        primary = RightsCategory.SPECIAL
    else:
        primary = RightsCategory.NONE
    return RightsSummary(
        raw=mask,
        normalized=normalized,
        primary=primary,
        categories=categories,
        covered_mask=covered,
        extra_bits=extra,
        unrecognized_bits=normalized.unrecognized_bits,
        indeterminate=normalized.is_indeterminate,
    )


def classify_share_mask(mask: RightsMask) -> SharePermission | None:
    """The highest share level a share mask fully contains, or ``None``.

    ``None`` is a real answer, not a failure: a share ACL can carry a mask that is neither
    Read, Change, nor Full, and reporting it as the nearest level would overstate or
    understate it. Callers should fall back to :func:`summarize`.
    """
    if mask.layer is not RightsLayer.SMB_SHARE:
        raise RightsLayerError(
            f"classify_share_mask expects a share mask; received {mask.layer.value!r}.",
            field="mask",
        )
    value = mask.expand_generics().value
    for level in (SharePermission.FULL, SharePermission.CHANGE, SharePermission.READ):
        required = SHARE_LEVEL_MASKS[level] & ~SYNCHRONIZE_BIT
        if value & required == required:
            return level
    return None


__all__ = [
    "CATEGORY_REQUIRED_MASKS",
    "ESCALATION_RIGHTS",
    "FILE_ALL_ACCESS",
    "FILE_GENERIC_EXECUTE",
    "FILE_GENERIC_READ",
    "FILE_GENERIC_WRITE",
    "FILE_SYSTEM_GENERIC_MAPPING",
    "GENERIC_RIGHT_BITS",
    "MAX_ACCESS_MASK",
    "SHARE_LEVEL_MASKS",
    "SYNCHRONIZE_BIT",
    "AccessPath",
    "EffectiveRights",
    "ExtendedRight",
    "NormalizedRights",
    "RightsCategory",
    "RightsError",
    "RightsLayer",
    "RightsLayerError",
    "RightsMask",
    "RightsSummary",
    "apply_deny",
    "category_display_name",
    "classify_share_mask",
    "effective_rights",
    "intersect_all",
    "normalize_mask",
    "normalize_ntfs_mask",
    "normalize_share_ace",
    "normalize_share_mask",
    "normalize_share_permission",
    "resolve_canonical",
    "summarize",
    "union_all",
]
