"""Raw permission facts: SMB share ACEs and NTFS ACEs.

Everything in this module is an **observation**, never a conclusion. An ACE says what a
security descriptor contained; it does not say whether anybody can open a file. Those are
different claims, and conflating them is the failure mode this model exists to prevent:

* an ACE is attributed to a trustee SID, an access mask, and a source (explicit or
  inherited) exactly as read from the descriptor;
* nothing here resolves Deny precedence, expands groups, maps generic rights, or combines
  the share layer with the NTFS layer. That is the Phase 4 engine's work, and its output is
  derived state that must never be written back into an ACE row.

**The two layers are separate.** Access over SMB is limited by the share ACL *and* the NTFS
ACL: the effective right is the intersection. A share granting Full Control does not widen
NTFS, and NTFS Full Control is irrelevant over a share that grants Read. They are modeled
as two distinct types so that no code can accidentally treat one as the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag, StrEnum
from typing import Final

from app.domain.errors import DomainValidationError
from app.domain.identity import Sid


class AclLayer(StrEnum):
    """Which authorization layer an ACE belongs to."""

    SMB_SHARE = "smb_share"
    NTFS = "ntfs"


class AceType(StrEnum):
    """ACE type as stored in the descriptor.

    Only DACL types are modeled. Audit (SACL) entries govern logging, not access, and are
    out of scope; a collector that encounters one must not record it as an access ACE.
    """

    ALLOW = "allow"
    DENY = "deny"


class AceSource(StrEnum):
    """Where an ACE came from.

    Explicit and inherited ACEs are both real grants, but they are managed in different
    places: an explicit ACE was set on this object, an inherited ACE comes from an ancestor
    and disappears if the ancestor changes. An audit tool that cannot tell them apart
    cannot tell an administrator where to make a fix.
    """

    EXPLICIT = "explicit"
    INHERITED = "inherited"


class AceFlag(IntFlag):
    """ACE header flags governing inheritance and propagation (``ACE_HEADER.AceFlags``)."""

    NONE = 0x00
    OBJECT_INHERIT = 0x01
    """Child files receive this ACE."""

    CONTAINER_INHERIT = 0x02
    """Child directories receive this ACE."""

    NO_PROPAGATE_INHERIT = 0x04
    """Children receive it, grandchildren do not."""

    INHERIT_ONLY = 0x08
    """The ACE does not apply to this object; it exists only to be inherited."""

    INHERITED = 0x10
    """This ACE was inherited from an ancestor rather than set here."""


class NtfsRight(IntFlag):
    """NTFS access-mask bits (``FILE_*``, standard, and generic rights).

    Generic rights are stored as observed and are **not** expanded here. Windows maps them
    through the object type's generic mapping at evaluation time; doing that mapping in the
    collector or in this model would bake one interpretation into stored facts.
    """

    NONE = 0x00000000

    # Object-specific rights
    READ_DATA = 0x00000001  # also FILE_LIST_DIRECTORY
    WRITE_DATA = 0x00000002  # also FILE_ADD_FILE
    APPEND_DATA = 0x00000004  # also FILE_ADD_SUBDIRECTORY
    READ_EA = 0x00000008
    WRITE_EA = 0x00000010
    EXECUTE = 0x00000020  # also FILE_TRAVERSE
    DELETE_CHILD = 0x00000040
    READ_ATTRIBUTES = 0x00000080
    WRITE_ATTRIBUTES = 0x00000100

    # Standard rights
    DELETE = 0x00010000
    READ_CONTROL = 0x00020000
    """Read the security descriptor. This is the right ADG's own collectors require."""

    WRITE_DAC = 0x00040000
    """Change the DACL — an escalation path: the holder can grant themselves anything."""

    WRITE_OWNER = 0x00080000
    """Take ownership — also an escalation path."""

    SYNCHRONIZE = 0x00100000

    # Generic rights, resolved by Windows through the generic mapping.
    GENERIC_ALL = 0x10000000
    GENERIC_EXECUTE = 0x20000000
    GENERIC_WRITE = 0x40000000
    GENERIC_READ = 0x80000000


GENERIC_RIGHT_BITS: Final = (
    NtfsRight.GENERIC_ALL
    | NtfsRight.GENERIC_EXECUTE
    | NtfsRight.GENERIC_WRITE
    | NtfsRight.GENERIC_READ
)

FULL_CONTROL_MASK: Final = 0x001F01FF
"""``FILE_ALL_ACCESS``: every object-specific and standard right for a file system object."""

_MAX_ACCESS_MASK: Final = 0xFFFFFFFF


class SharePermission(StrEnum):
    """The three share-permission levels the Windows UI and SMB cmdlets report.

    Share ACLs are ordinary access masks underneath, but management tooling
    (``Get-SmbShareAccess``) reports only these levels. A collector records whichever form
    its source provides; it must not invent the other.
    """

    READ = "read"
    CHANGE = "change"
    FULL = "full"


SHARE_PERMISSION_MASKS: Final[dict[SharePermission, int]] = {
    SharePermission.READ: 0x001200A9,
    SharePermission.CHANGE: 0x001301BF,
    SharePermission.FULL: 0x001F01FF,
}
"""Conventional masks for the three levels, for reference by the Phase 4 rights algebra.

Recorded here as documentation of the mapping, not as a conversion applied to observations.
"""


def _validate_access_mask(mask: int, *, field_name: str = "access_mask") -> int:
    if not isinstance(mask, int) or isinstance(mask, bool):
        raise DomainValidationError(
            f"An access mask must be an integer; received {type(mask).__name__}.",
            field=field_name,
        )
    if mask < 0 or mask > _MAX_ACCESS_MASK:
        raise DomainValidationError(
            f"An access mask must be an unsigned 32-bit value; received {mask}.",
            value=mask,
            field=field_name,
        )
    return mask


@dataclass(frozen=True, slots=True)
class NtfsAce:
    """One ACE read from a file-system object's DACL.

    Attributes:
        trustee_sid: who the ACE names. Unresolvable SIDs are kept as SIDs.
        ace_type: Allow or Deny, as stored. Order in the DACL is preserved separately by
            ``order_index`` because canonical ordering is a property of the ACL, not of the
            ACE, and a non-canonical ACL is itself a finding.
        access_mask: the raw mask, generic bits included, exactly as observed.
        flags: the inheritance and propagation flags.
        source: explicit or inherited, kept consistent with ``AceFlag.INHERITED``.
        inherited_from: the ancestor path Windows reported as the origin, when known.
        order_index: position within the DACL, 0-based.
    """

    trustee_sid: Sid
    ace_type: AceType
    access_mask: int
    flags: AceFlag = AceFlag.NONE
    source: AceSource = AceSource.EXPLICIT
    inherited_from: str | None = None
    order_index: int | None = None

    def __post_init__(self) -> None:
        _validate_access_mask(self.access_mask)
        flag_says_inherited = bool(self.flags & AceFlag.INHERITED)
        source_says_inherited = self.source is AceSource.INHERITED
        if flag_says_inherited != source_says_inherited:
            raise DomainValidationError(
                f"ACE source {self.source.value!r} contradicts its flags "
                f"({self.flags!r}). INHERITED_ACE must be set exactly when the ACE is "
                "inherited; a collector must report what the descriptor says.",
                field="source",
            )
        if self.inherited_from is not None and self.source is AceSource.EXPLICIT:
            raise DomainValidationError(
                "An explicit ACE cannot record an inheritance origin.", field="inherited_from"
            )
        if self.order_index is not None and self.order_index < 0:
            raise DomainValidationError(
                f"order_index must be non-negative; received {self.order_index}.",
                value=self.order_index,
                field="order_index",
            )

    @property
    def rights(self) -> NtfsRight:
        """The mask as flags. Unknown bits are dropped from this view, never from the mask."""
        known = 0
        for right in NtfsRight:
            if right.value and self.access_mask & right.value == right.value:
                known |= right.value
        return NtfsRight(known)

    @property
    def unrecognized_bits(self) -> int:
        """Mask bits that match no known right. Kept visible instead of silently ignored."""
        return self.access_mask & ~int(self.rights)

    @property
    def is_inherited(self) -> bool:
        return self.source is AceSource.INHERITED

    @property
    def uses_generic_rights(self) -> bool:
        """True when the mask carries generic bits that Windows resolves at access time."""
        return bool(self.access_mask & int(GENERIC_RIGHT_BITS))

    @property
    def applies_to_this_object(self) -> bool:
        """False for INHERIT_ONLY ACEs, which grant nothing on the object that holds them."""
        return not (self.flags & AceFlag.INHERIT_ONLY)

    @property
    def is_inheritable(self) -> bool:
        return bool(self.flags & (AceFlag.OBJECT_INHERIT | AceFlag.CONTAINER_INHERIT))


def share_ace_right_token(access_mask: int | None, permission: SharePermission | None) -> str:
    """The granted right, rendered in whichever form the source reported it.

    A permission level and a mask are two readings of the same ACL, and *which* reading was
    taken is itself part of the observation: ``Get-SmbShareAccess`` can only say ``change``,
    while a security descriptor says ``0x001301bf``. Rendering one as the other would claim
    precision the source did not provide, so the token keeps them distinct and the Phase 4
    algebra reconciles them.

    Tolerates a missing right — ``0x00000000`` — because this is also how an identity is
    derived for an ACE that has not been validated yet. :class:`SmbShareAce` refuses such an
    ACE outright.
    """
    if permission is not None:
        return permission.value
    return f"0x{access_mask or 0:08x}"


def share_ace_identity_key(
    share_key: str, trustee_sid: Sid, ace_type: AceType, right_token: str
) -> str:
    """Uniqueness of a share ACE: one row per (share, trustee, type, right).

    ``order_index`` is deliberately absent, for the same reason it is absent from an NTFS
    ACE key: two entries identical in trustee, type, and right are duplicates of one
    another, and an administrator reordering an ACL must not look like every entry being
    deleted and recreated.

    This is the only implementation of the format. :meth:`SmbShareAce.identity_key` and the
    contract's ``smb_ace`` source key both call it, so a stored ACE and the key its
    collector sent cannot come to describe different entries.
    """
    return f"{share_key.casefold()}|{trustee_sid.value}|{ace_type.value}|{right_token}"


@dataclass(frozen=True, slots=True)
class SmbShareAce:
    """One ACE from a share-level ACL.

    A share ACL is reported two different ways depending on the source: as an access mask
    (security descriptor APIs) or as one of three permission levels (SMB cmdlets). Exactly
    one of ``access_mask`` and ``permission`` must be present — recording a value the source
    did not provide would be fabrication.
    """

    trustee_sid: Sid
    ace_type: AceType
    access_mask: int | None = None
    permission: SharePermission | None = None
    order_index: int | None = None

    def __post_init__(self) -> None:
        if (self.access_mask is None) == (self.permission is None):
            raise DomainValidationError(
                "A share ACE must record exactly one of access_mask or permission: "
                "whichever form the collecting API reported.",
                field="access_mask",
            )
        if self.access_mask is not None:
            _validate_access_mask(self.access_mask)
        if self.order_index is not None and self.order_index < 0:
            raise DomainValidationError(
                f"order_index must be non-negative; received {self.order_index}.",
                value=self.order_index,
                field="order_index",
            )

    @property
    def layer(self) -> AclLayer:
        return AclLayer.SMB_SHARE

    @property
    def right_token(self) -> str:
        """See :func:`share_ace_right_token`."""
        return share_ace_right_token(self.access_mask, self.permission)

    def identity_key(self, share_key: str) -> str:
        """See :func:`share_ace_identity_key`. ``share_key`` is ``SmbShare.identity_key``."""
        return share_ace_identity_key(share_key, self.trustee_sid, self.ace_type, self.right_token)


@dataclass(frozen=True, slots=True)
class SecurityDescriptorFacts:
    """What was observed about a security descriptor as a whole.

    The ACE list is only part of the story. A DACL that is absent entirely means *everyone
    has full access*, while an empty (but present) DACL means *nobody does* — opposite
    meanings that are easy to conflate if only the ACE list is stored. Protection from
    inheritance and ownership are recorded here for the same reason.
    """

    owner_sid: Sid | None = None
    group_sid: Sid | None = None
    dacl_present: bool = True
    dacl_protected: bool = False
    """The object blocks inheritance from its parent (``SE_DACL_PROTECTED``)."""

    ace_count: int = 0

    def __post_init__(self) -> None:
        if self.ace_count < 0:
            raise DomainValidationError(
                f"ace_count must be non-negative; received {self.ace_count}.",
                value=self.ace_count,
                field="ace_count",
            )
        if not self.dacl_present and self.ace_count:
            raise DomainValidationError(
                "A descriptor with no DACL cannot carry ACEs. A NULL DACL grants everyone "
                "full access and must be recorded as such, not as an empty ACE list.",
                field="dacl_present",
            )

    @property
    def grants_everyone_full_access(self) -> bool:
        """True for a NULL DACL: an unrestricted object, and always a finding."""
        return not self.dacl_present

    @property
    def denies_everyone(self) -> bool:
        """True for a present-but-empty DACL.

        Nobody has access through the DACL; the owner retains implicit control rights.
        """
        return self.dacl_present and self.ace_count == 0
