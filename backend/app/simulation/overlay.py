r"""A proposed change to the estate, expressed as data and never as an edit.

An overlay is the *what if*: a membership somebody is thinking of granting, an ACE somebody
is thinking of removing, an inheritance flag somebody is thinking of setting. It is a value
— frozen, hashable, serializable, and entirely free of I/O — and nothing in this module can
reach Active Directory, a share, an NTFS descriptor, or a row of ADG's own collected state.
That is the property the whole phase rests on, and it is a property of the *type*, not of
the discipline of its callers: there is no method here that writes anything.

**Why a change names its target rather than carrying the row.** A change says "remove the
ACE whose key is *k* from ``\\FS01\Finance``". It does not carry a copy of that ACE. So an
overlay written on Monday and evaluated on Friday is evaluated against Friday's ACL, and if
the ACE has since been removed by somebody else the simulation reports that the change is
**not applicable** rather than quietly re-creating a row that has gone. An overlay that
embedded the row it was written against would instead simulate a world that no longer
exists and say nothing about it.

**Trustees are named by SID, never by key.** A trustee *key* is host-scoped for a BUILTIN
principal — ``fs01|S-1-5-32-544`` is a different group from ``fs02|S-1-5-32-544`` — and
which host applies is a fact about the resource the ACE sits on, not about the proposal. So
the SID is what is stored and the key is derived at application time through
:func:`app.domain.referenced_principal_key`, exactly as ingestion derives it, which is what
keeps a simulated ACE scoped the same way a collected one is (ADR-0001).

**Nothing here decides whether a change is a good idea.** An overlay can propose removing
the only path anybody has to a directory; saying so is
:mod:`app.simulation.impact`'s job, and it says so by re-running the access check rather
than by reasoning about the proposal.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, TypeAlias

from app.access_engine import MAX_ACCESS_MASK
from app.domain import (
    AceFlag,
    AceType,
    DomainValidationError,
    MembershipEdgeKind,
    PrincipalKind,
    SharePermission,
    Sid,
    parse_share_identifier,
    parse_unc_path,
    referenced_principal_key,
)

__all__ = [
    "MAX_CHANGES",
    "OVERLAY_DOCUMENT_VERSION",
    "OVERLAY_HASH_LENGTH",
    "SIMULATED_ACE_PREFIX",
    "SIMULATED_EDGE_PREFIX",
    "SIMULATED_SOURCE_KEY",
    "ChangeKind",
    "InheritanceChange",
    "InheritedAceDisposition",
    "MembershipChange",
    "NtfsAceChange",
    "ShareAceChange",
    "SimulationChange",
    "SimulationOverlay",
    "is_simulated_ace_key",
    "is_simulated_edge_key",
]

MAX_CHANGES: Final = 200
"""Changes one overlay may hold.

A ceiling rather than a guideline. Every change widens the set of principals and resources
a simulation has to evaluate, and an overlay of unbounded size would turn one request into
unbounded work no later bound could rescue. Two hundred is far past any proposal a person
reviews by hand and far short of anything that costs real time.
"""

OVERLAY_HASH_LENGTH: Final = 32
"""Hex characters kept from an overlay's digest. 128 bits, matching
:data:`app.domain.basis.TOKEN_LENGTH`, and for the same reason: long enough that two
different proposals cannot collide, short enough to read in a log line."""

OVERLAY_DOCUMENT_VERSION: Final = "1.0"
"""The version of the serialized form. Stored beside every persisted overlay so that a
proposal written today can be read back after the shape changes, rather than being
reinterpreted under new rules and quietly simulating something else."""

SIMULATED_SOURCE_KEY: Final = "simulated"
"""The ``source_key`` every fabricated row carries.

Real rows carry the deterministic source key of the observation that produced them
(:mod:`app.contracts.v1.keys`). A simulated row carries this instead, so that a record which
never came from a collector cannot be mistaken for one that did — by a test, by a log line,
or by a future reader of this code.
"""

SIMULATED_EDGE_PREFIX: Final = "simulated|"
"""Prefix on the ``edge_key`` of a membership edge an overlay invented.

The rest of the key is the ordinary :attr:`app.domain.MembershipEdge.identity_key`, so a
simulated edge is comparable with the real one it stands in for; the prefix is what makes an
explanation that names it visibly hypothetical.
"""

SIMULATED_ACE_PREFIX: Final = "simulated|"
"""The same, for the ``ace_key`` of an ACE an overlay invented."""


def is_simulated_edge_key(key: str) -> bool:
    """Whether a membership edge key names an edge that only exists in a proposal."""
    return key.startswith(SIMULATED_EDGE_PREFIX)


def is_simulated_ace_key(key: str) -> bool:
    """Whether an ACE key names an entry that only exists in a proposal."""
    return key.startswith(SIMULATED_ACE_PREFIX)


class ChangeKind(StrEnum):
    """The closed vocabulary of things an overlay can propose.

    Closed on purpose. Every kind here has a defined effect on exactly one repository read,
    and a kind with no such effect would be accepted, stored, reported as applied, and
    change nothing about the answer — the quietest possible way for a simulation to lie.
    """

    ADD_MEMBER = "add_member"
    REMOVE_MEMBER = "remove_member"
    ADD_NTFS_ACE = "add_ntfs_ace"
    MODIFY_NTFS_ACE = "modify_ntfs_ace"
    REMOVE_NTFS_ACE = "remove_ntfs_ace"
    ADD_SHARE_ACE = "add_share_ace"
    MODIFY_SHARE_ACE = "modify_share_ace"
    REMOVE_SHARE_ACE = "remove_share_ace"
    SET_INHERITANCE = "set_inheritance"

    @property
    def removes(self) -> bool:
        """Whether this kind can take an access path away.

        ``MODIFY`` counts, because a modification can narrow a mask, and the alternate-path
        analysis that protects against a false "access removed" claim has to run whenever a
        claim of loss is possible rather than only when one is certain.
        """
        return self in _REMOVING_KINDS


_REMOVING_KINDS: Final[frozenset[ChangeKind]] = frozenset(
    {
        ChangeKind.REMOVE_MEMBER,
        ChangeKind.REMOVE_NTFS_ACE,
        ChangeKind.REMOVE_SHARE_ACE,
        ChangeKind.MODIFY_NTFS_ACE,
        ChangeKind.MODIFY_SHARE_ACE,
        ChangeKind.SET_INHERITANCE,
    }
)


class InheritedAceDisposition(StrEnum):
    """What happens to the entries a directory currently inherits when it is protected.

    Windows asks this question in a dialog box, and the two answers produce genuinely
    different ACLs: one keeps every inherited grant as an explicit entry, the other removes
    them all. A simulation that picked one silently would report the wrong ACL half the time,
    so an overlay has to say which.
    """

    CONVERT_TO_EXPLICIT = "convert_to_explicit"
    """Keep the inherited entries, rewritten as explicit ones. The ACL is unchanged today
    and stops tracking the parent from now on."""

    REMOVE = "remove"
    """Drop the inherited entries. Only what was already explicit on this directory
    survives, which is usually a far smaller ACL than anybody expects."""


def _validate_key(value: str, field: str) -> str:
    if not value or not value.strip():
        raise DomainValidationError(
            f"A simulated change must name the object it acts on; {field} was empty.",
            field=field,
        )
    return value


def _validate_mask(value: int, field: str) -> int:
    if not 0 <= value <= MAX_ACCESS_MASK:
        raise DomainValidationError(
            f"An access mask is a 32-bit value; {value} is outside it.",
            value=str(value),
            field=field,
        )
    return value


@dataclass(frozen=True, slots=True)
class MembershipChange:
    """One membership edge an overlay adds or removes.

    ``group_key`` and ``member_key`` are storage keys, because that is what the traversal
    walks and what a removal has to match. A local group's key carries its host
    (``fs01|S-1-5-32-544``), and the edge kind must agree with that: an edge into a
    host-scoped group is a local-group membership and an edge into a domain group is not.
    The pair is checked rather than inferred, because an overlay that guessed would build an
    edge the traversal could not match and would then report a change that did nothing.
    """

    kind: ChangeKind
    group_key: str
    member_key: str
    edge_kind: MembershipEdgeKind = MembershipEdgeKind.DIRECTORY_GROUP_MEMBER
    member_kind: PrincipalKind | None = None
    """What the member is, when the proposal knows. Used only to label the synthesized edge;
    the traversal reads it exactly as it reads a collector's claim about a member it could
    not describe."""

    def __post_init__(self) -> None:
        if self.kind not in (ChangeKind.ADD_MEMBER, ChangeKind.REMOVE_MEMBER):
            raise DomainValidationError(
                f"{self.kind.value} is not a membership change.", field="kind"
            )
        _validate_key(self.group_key, "group_key")
        _validate_key(self.member_key, "member_key")
        if self.group_key == self.member_key:
            raise DomainValidationError(
                f"A group cannot be a direct member of itself ({self.group_key}). Windows "
                "does not create such an edge, so neither may a proposal.",
                value=self.group_key,
                field="member_key",
            )
        local = self.edge_kind is MembershipEdgeKind.LOCAL_GROUP_MEMBER
        if local != (self.host_key is not None):
            raise DomainValidationError(
                "A host-scoped group key and a local-group edge kind go together: "
                f"{self.group_key} and {self.edge_kind.value} do not. A BUILTIN SID names a "
                "different group on every computer, so an edge into one that is not marked "
                "local would be matched against the wrong group — or against none.",
                field="edge_kind",
            )

    @property
    def host_key(self) -> str | None:
        """The computer a host-scoped group lives on, read off its key.

        Split on the last separator: a SID never contains one, and a host name may contain
        very nearly anything else.
        """
        host, separator, _ = self.group_key.rpartition("|")
        return host if separator else None

    @property
    def edge_key(self) -> str:
        """The identity the synthesized edge carries, prefixed so it reads as hypothetical."""
        return f"{SIMULATED_EDGE_PREFIX}{self.group_key}->{self.member_key}|{self.edge_kind.value}"

    @property
    def target(self) -> str:
        """What this change acts on, for duplicate detection within an overlay."""
        return f"edge:{self.group_key}->{self.member_key}|{self.edge_kind.value}"

    def document(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "group_key": self.group_key,
            "member_key": self.member_key,
            "edge_kind": self.edge_kind.value,
            "member_kind": None if self.member_kind is None else self.member_kind.value,
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> MembershipChange:
        member_kind = document.get("member_kind")
        return cls(
            kind=ChangeKind(document["kind"]),
            group_key=document["group_key"],
            member_key=document["member_key"],
            edge_kind=MembershipEdgeKind(document["edge_kind"]),
            member_kind=None if member_kind is None else PrincipalKind(member_kind),
        )


@dataclass(frozen=True, slots=True)
class NtfsAceChange:
    """One entry an overlay adds to, modifies in, or removes from a directory's DACL.

    An addition names a trustee **SID**; the key it is matched on is derived from the
    server in ``resource_key`` when the change is applied, which is what scopes a BUILTIN
    trustee to the machine whose tree it sits on. A modification or a removal names an
    existing ``ace_key`` instead, because those act on an entry that is already there and
    an entry that is already there has an identity.

    ``order_index`` places an addition in the DACL. Left at ``None`` it is placed
    canonically — Deny ahead of every Allow, both ahead of everything inherited — which is
    where the Windows ACL editor puts a new entry. Given explicitly it is honored, including
    where that produces a non-canonical DACL, because simulating one is exactly how an
    operator finds out that it behaves differently from the one the editor draws.
    """

    kind: ChangeKind
    resource_key: str
    ace_key: str | None = None
    trustee_sid: str | None = None
    ace_type: AceType | None = None
    access_mask: int | None = None
    ace_flags: int | None = None
    order_index: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in _NTFS_KINDS:
            raise DomainValidationError(
                f"{self.kind.value} is not an NTFS ACE change.", field="kind"
            )
        object.__setattr__(self, "resource_key", _resource_key(self.resource_key))
        if self.access_mask is not None:
            _validate_mask(self.access_mask, "access_mask")
        if self.ace_flags is not None and not 0 <= self.ace_flags <= 0xFF:
            raise DomainValidationError(
                f"ACE flags are a single byte; {self.ace_flags} is outside it.", field="ace_flags"
            )
        if self.order_index is not None and self.order_index < 0:
            raise DomainValidationError("A DACL position is not negative.", field="order_index")
        if self.kind is ChangeKind.ADD_NTFS_ACE:
            _require_add_fields(self)
        else:
            if not self.ace_key:
                raise DomainValidationError(
                    f"{self.kind.value} acts on an entry that already exists, so it must name "
                    "it. Matching on trustee and mask instead would act on whichever entry "
                    "happened to sort first when a DACL holds two that look alike.",
                    field="ace_key",
                )
            if self.kind is ChangeKind.MODIFY_NTFS_ACE and not (
                self.access_mask is not None
                or self.ace_flags is not None
                or self.ace_type is not None
            ):
                raise DomainValidationError(
                    "A modification must change something: give a mask, flags, or a type.",
                    field="access_mask",
                )

    @property
    def trustee_key(self) -> str | None:
        """The storage key the trustee SID resolves to on this resource's server.

        ``None`` for a change that names an existing entry rather than a new trustee; the
        entry's own stored key is used then, which is the one the collector derived.
        """
        if self.trustee_sid is None:
            return None
        return referenced_principal_key(
            Sid(self.trustee_sid), parse_unc_path(self.resource_key).server
        )

    @property
    def target(self) -> str:
        if self.ace_key is not None:
            return f"ntfs_ace:{self.ace_key}"
        return (
            f"ntfs_ace:{self.resource_key}|{self.trustee_sid}"
            f"|{'' if self.ace_type is None else self.ace_type.value}"
            f"|{self.access_mask}|{self.ace_flags}"
        )

    def document(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "resource_key": self.resource_key,
            "ace_key": self.ace_key,
            "trustee_sid": self.trustee_sid,
            "ace_type": None if self.ace_type is None else self.ace_type.value,
            "access_mask": self.access_mask,
            "ace_flags": self.ace_flags,
            "order_index": self.order_index,
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> NtfsAceChange:
        ace_type = document.get("ace_type")
        return cls(
            kind=ChangeKind(document["kind"]),
            resource_key=document["resource_key"],
            ace_key=document.get("ace_key"),
            trustee_sid=document.get("trustee_sid"),
            ace_type=None if ace_type is None else AceType(ace_type),
            access_mask=document.get("access_mask"),
            ace_flags=document.get("ace_flags"),
            order_index=document.get("order_index"),
        )


@dataclass(frozen=True, slots=True)
class ShareAceChange:
    """One entry an overlay adds to, modifies in, or removes from a share ACL.

    A share ACE carries **either** a mask or one of three permission levels, never both, for
    the same reason :class:`app.domain.SmbShareAce` does: a source reports one form or the
    other, and inventing the missing one would claim precision the proposal does not have.
    """

    kind: ChangeKind
    share_key: str
    ace_key: str | None = None
    trustee_sid: str | None = None
    ace_type: AceType | None = None
    access_mask: int | None = None
    permission: SharePermission | None = None
    order_index: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in _SHARE_KINDS:
            raise DomainValidationError(
                f"{self.kind.value} is not a share ACE change.", field="kind"
            )
        object.__setattr__(self, "share_key", _share_key(self.share_key))
        if self.access_mask is not None:
            _validate_mask(self.access_mask, "access_mask")
        if self.order_index is not None and self.order_index < 0:
            raise DomainValidationError("A DACL position is not negative.", field="order_index")
        if self.kind is ChangeKind.ADD_SHARE_ACE:
            if not self.trustee_sid:
                raise DomainValidationError(
                    "A new share ACE must name the trustee it grants to.", field="trustee_sid"
                )
            Sid(self.trustee_sid)
            if self.ace_type is None:
                raise DomainValidationError(
                    "A new share ACE must say whether it allows or denies.", field="ace_type"
                )
            if (self.access_mask is None) == (self.permission is None):
                raise DomainValidationError(
                    "A share ACE carries exactly one of access_mask or permission, whichever "
                    "form the proposal is written in.",
                    field="access_mask",
                )
        else:
            if not self.ace_key:
                raise DomainValidationError(
                    f"{self.kind.value} acts on an entry that already exists, so it must name it.",
                    field="ace_key",
                )
            if self.kind is ChangeKind.MODIFY_SHARE_ACE:
                if self.access_mask is not None and self.permission is not None:
                    raise DomainValidationError(
                        "A modification sets one right form or the other, never both.",
                        field="access_mask",
                    )
                if self.access_mask is None and self.permission is None and self.ace_type is None:
                    raise DomainValidationError(
                        "A modification must change something: give a mask, a permission, "
                        "or a type.",
                        field="access_mask",
                    )

    @property
    def trustee_key(self) -> str | None:
        """The storage key the trustee SID resolves to on this share's server."""
        if self.trustee_sid is None:
            return None
        return referenced_principal_key(
            Sid(self.trustee_sid), parse_share_identifier(self.share_key).server
        )

    @property
    def target(self) -> str:
        if self.ace_key is not None:
            return f"share_ace:{self.ace_key}"
        return (
            f"share_ace:{self.share_key}|{self.trustee_sid}"
            f"|{'' if self.ace_type is None else self.ace_type.value}"
            f"|{self.access_mask}|{None if self.permission is None else self.permission.value}"
        )

    def document(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "share_key": self.share_key,
            "ace_key": self.ace_key,
            "trustee_sid": self.trustee_sid,
            "ace_type": None if self.ace_type is None else self.ace_type.value,
            "access_mask": self.access_mask,
            "permission": None if self.permission is None else self.permission.value,
            "order_index": self.order_index,
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> ShareAceChange:
        ace_type = document.get("ace_type")
        permission = document.get("permission")
        return cls(
            kind=ChangeKind(document["kind"]),
            share_key=document["share_key"],
            ace_key=document.get("ace_key"),
            trustee_sid=document.get("trustee_sid"),
            ace_type=None if ace_type is None else AceType(ace_type),
            access_mask=document.get("access_mask"),
            permission=None if permission is None else SharePermission(permission),
            order_index=document.get("order_index"),
        )


@dataclass(frozen=True, slots=True)
class InheritanceChange:
    """Setting or clearing ``SE_DACL_PROTECTED`` on one directory.

    Protecting a directory is the change administrators reach for most often and understand
    least: it does not by itself remove anybody's access, it freezes whatever the directory
    holds *right now* and stops the parent reaching it — and whether the entries it holds
    right now survive depends on an answer to a dialog box (:class:`InheritedAceDisposition`).

    Clearing protection is the reverse, and it is only representable when ADG has read the
    parent: what flows back down is a projection of the parent's inheritable entries
    (:func:`app.domain.project_inherited_acl`), and a parent nobody has read projects
    nothing that can honestly be predicted. A simulation of that is reported as not
    representable rather than computed from an empty parent, which would look exactly like a
    parent that grants nothing.
    """

    resource_key: str
    protected: bool
    inherited_entries: InheritedAceDisposition | None = None

    @property
    def kind(self) -> ChangeKind:
        return ChangeKind.SET_INHERITANCE

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_key", _resource_key(self.resource_key))
        if self.protected and self.inherited_entries is None:
            raise DomainValidationError(
                "Protecting a directory has two possible outcomes for the entries it "
                "currently inherits — kept as explicit copies, or removed — and they produce "
                "different ACLs. The proposal has to say which.",
                field="inherited_entries",
            )
        if not self.protected and self.inherited_entries is not None:
            raise DomainValidationError(
                "Clearing protection re-attaches the directory to its parent; there are no "
                "inherited entries to dispose of yet.",
                field="inherited_entries",
            )

    @property
    def target(self) -> str:
        return f"inheritance:{self.resource_key}"

    def document(self) -> dict[str, Any]:
        return {
            "kind": ChangeKind.SET_INHERITANCE.value,
            "resource_key": self.resource_key,
            "protected": self.protected,
            "inherited_entries": (
                None if self.inherited_entries is None else self.inherited_entries.value
            ),
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> InheritanceChange:
        disposition = document.get("inherited_entries")
        return cls(
            resource_key=document["resource_key"],
            protected=bool(document["protected"]),
            inherited_entries=(
                None if disposition is None else InheritedAceDisposition(disposition)
            ),
        )


SimulationChange: TypeAlias = (
    "MembershipChange | NtfsAceChange | ShareAceChange | InheritanceChange"
)

_NTFS_KINDS: Final[frozenset[ChangeKind]] = frozenset(
    {ChangeKind.ADD_NTFS_ACE, ChangeKind.MODIFY_NTFS_ACE, ChangeKind.REMOVE_NTFS_ACE}
)
_SHARE_KINDS: Final[frozenset[ChangeKind]] = frozenset(
    {ChangeKind.ADD_SHARE_ACE, ChangeKind.MODIFY_SHARE_ACE, ChangeKind.REMOVE_SHARE_ACE}
)


def _require_add_fields(change: NtfsAceChange) -> None:
    if not change.trustee_sid:
        raise DomainValidationError(
            "A new NTFS ACE must name the trustee it applies to.", field="trustee_sid"
        )
    Sid(change.trustee_sid)
    if change.ace_type is None:
        raise DomainValidationError(
            "A new NTFS ACE must say whether it allows or denies.", field="ace_type"
        )
    if change.access_mask is None:
        raise DomainValidationError(
            "A new NTFS ACE must carry the mask it grants or denies. An entry with no mask "
            "is not a permission; it is a row that changes nothing and would report itself "
            "as applied.",
            field="access_mask",
        )


def _resource_key(value: str) -> str:
    """Fold a directory identifier to the storage key, refusing anything that is not UNC."""
    _validate_key(value, "resource_key")
    return parse_unc_path(value).comparison_key


def _share_key(value: str) -> str:
    """Fold a share identifier to the storage key ``server|share``."""
    _validate_key(value, "share_key")
    return parse_share_identifier(value).identity_key


@dataclass(frozen=True, slots=True)
class SimulationOverlay:
    """An immutable set of proposed changes, grouped by what they act on.

    Grouped rather than held in one list because each group is consumed by exactly one
    repository read, and a flat list would be re-partitioned at every call site — three
    partitions that could disagree. The flat, ordered view is :attr:`changes`, which is what
    a report renders and what the digest is taken over.

    The overlay is a value. Applying it (:mod:`app.simulation.application`) returns new
    records and leaves both the overlay and the baseline rows untouched; there is no
    ``apply`` method here that could be tempted to do otherwise.
    """

    membership: tuple[MembershipChange, ...] = ()
    ntfs_aces: tuple[NtfsAceChange, ...] = ()
    share_aces: tuple[ShareAceChange, ...] = ()
    inheritance: tuple[InheritanceChange, ...] = ()

    def __post_init__(self) -> None:
        total = (
            len(self.membership)
            + len(self.ntfs_aces)
            + len(self.share_aces)
            + len(self.inheritance)
        )
        if total > MAX_CHANGES:
            raise DomainValidationError(
                f"An overlay holds at most {MAX_CHANGES} changes; this one holds {total}. "
                "Every change widens the set of principals and resources that have to be "
                "re-evaluated, so an unbounded overlay is an unbounded simulation.",
                field="changes",
            )
        seen: dict[str, ChangeKind] = {}
        for change in self.changes:
            target = change.target
            if target in seen:
                raise DomainValidationError(
                    f"Two changes act on {target} ({seen[target].value} and "
                    f"{change.kind.value}). Which one wins would depend on the order they "
                    "were applied in, and a proposal whose meaning depends on that is not a "
                    "proposal anybody can review.",
                    field="changes",
                )
            seen[target] = change.kind

    # ------------------------------------------------------------------- views

    @property
    def changes(self) -> tuple[SimulationChange, ...]:
        """Every change, in a fixed order: membership, NTFS, share, inheritance.

        A total order that does not depend on how the overlay was built, so that the digest
        below identifies the *proposal* rather than the sequence somebody typed it in.
        """
        return (*self.membership, *self.ntfs_aces, *self.share_aces, *self.inheritance)

    def __iter__(self) -> Iterator[SimulationChange]:
        return iter(self.changes)

    def __len__(self) -> int:
        return len(self.changes)

    @property
    def is_empty(self) -> bool:
        return not self.changes

    @property
    def has_removals(self) -> bool:
        """Whether any change could take an access path away.

        The switch that turns on alternate-path analysis. An overlay that only adds cannot
        produce a false "access removed" claim, so the analysis it protects against is not
        run and nothing is spent on it.
        """
        return any(change.kind.removes for change in self.changes)

    @property
    def overlay_hash(self) -> str:
        """A stable digest of the proposal.

        Stable across processes: a hash of the canonical document, not of any in-memory
        identity. Two overlays that propose the same thing digest the same however they were
        assembled, which is what makes a stored simulation comparable with one somebody
        re-enters by hand.
        """
        material = "\x1f".join(_canonical_line(change) for change in self.changes)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return digest[:OVERLAY_HASH_LENGTH]

    # ---------------------------------------------------------- what it touches

    @property
    def group_keys(self) -> frozenset[str]:
        """Groups whose direct membership the overlay changes."""
        return frozenset(change.group_key for change in self.membership)

    @property
    def member_keys(self) -> frozenset[str]:
        """Principals whose own membership the overlay changes."""
        return frozenset(change.member_key for change in self.membership)

    @property
    def resource_keys(self) -> frozenset[str]:
        """Directories whose DACL or inheritance the overlay changes."""
        return frozenset(change.resource_key for change in self.ntfs_aces) | frozenset(
            change.resource_key for change in self.inheritance
        )

    @property
    def share_keys(self) -> frozenset[str]:
        """Shares whose ACL the overlay changes."""
        return frozenset(change.share_key for change in self.share_aces)

    @property
    def trustee_sids(self) -> frozenset[str]:
        """Every trustee an addition names, as a SID.

        Keys are deliberately not offered: a BUILTIN trustee's key depends on the server of
        the object the ACE sits on, so one SID can be two keys in one overlay. Callers that
        need keys ask the individual change for the one it resolves to.
        """
        sids = {change.trustee_sid for change in self.ntfs_aces if change.trustee_sid}
        sids |= {change.trustee_sid for change in self.share_aces if change.trustee_sid}
        return frozenset(sids)

    @property
    def trustee_keys(self) -> frozenset[str]:
        """Every trustee an addition names, resolved in the context of its own object."""
        keys = {change.trustee_key for change in self.ntfs_aces}
        keys |= {change.trustee_key for change in self.share_aces}
        return frozenset(key for key in keys if key is not None)

    def ntfs_changes_for(self, resource_key: str) -> tuple[NtfsAceChange, ...]:
        folded = resource_key.casefold()
        return tuple(change for change in self.ntfs_aces if change.resource_key == folded)

    def share_changes_for(self, share_key: str) -> tuple[ShareAceChange, ...]:
        folded = share_key.casefold()
        return tuple(change for change in self.share_aces if change.share_key == folded)

    def inheritance_change_for(self, resource_key: str) -> InheritanceChange | None:
        folded = resource_key.casefold()
        for change in self.inheritance:
            if change.resource_key == folded:
                return change
        return None

    def membership_changes_for(
        self, group_keys: Iterable[str] | None = None
    ) -> tuple[MembershipChange, ...]:
        if group_keys is None:
            return self.membership
        wanted = set(group_keys)
        return tuple(change for change in self.membership if change.group_key in wanted)

    def touches_resource(self, resource_key: str) -> bool:
        folded = resource_key.casefold()
        return folded in self.resource_keys

    def touches_share(self, share_key: str) -> bool:
        return share_key.casefold() in self.share_keys

    # --------------------------------------------------------------- documents

    def document(self) -> dict[str, Any]:
        """The serialized form, versioned, for storage outside the process."""
        return {
            "document_version": OVERLAY_DOCUMENT_VERSION,
            "overlay_hash": self.overlay_hash,
            "changes": [change.document() for change in self.changes],
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> SimulationOverlay:
        """Rebuild an overlay from its stored form.

        Every change goes back through its own constructor, so a stored proposal that would
        no longer be accepted is refused on the way in rather than simulated under rules it
        was never validated against.
        """
        version = document.get("document_version")
        if version != OVERLAY_DOCUMENT_VERSION:
            raise DomainValidationError(
                f"Overlay document version {version!r} cannot be read by this build, which "
                f"writes {OVERLAY_DOCUMENT_VERSION}. Reinterpreting it under the current "
                "rules would simulate something the author did not propose.",
                field="document_version",
            )
        return cls.from_changes(_change_from(item) for item in document.get("changes", ()))

    @classmethod
    def from_changes(cls, changes: Iterable[SimulationChange]) -> SimulationOverlay:
        """Build an overlay from a flat sequence, partitioning it by kind."""
        membership: list[MembershipChange] = []
        ntfs: list[NtfsAceChange] = []
        share: list[ShareAceChange] = []
        inheritance: list[InheritanceChange] = []
        for change in changes:
            if isinstance(change, MembershipChange):
                membership.append(change)
            elif isinstance(change, NtfsAceChange):
                ntfs.append(change)
            elif isinstance(change, ShareAceChange):
                share.append(change)
            else:
                inheritance.append(change)
        return cls(
            membership=tuple(membership),
            ntfs_aces=tuple(ntfs),
            share_aces=tuple(share),
            inheritance=tuple(inheritance),
        )


def _change_from(document: dict[str, Any]) -> SimulationChange:
    kind = ChangeKind(document["kind"])
    if kind in (ChangeKind.ADD_MEMBER, ChangeKind.REMOVE_MEMBER):
        return MembershipChange.from_document(document)
    if kind in _NTFS_KINDS:
        return NtfsAceChange.from_document(document)
    if kind in _SHARE_KINDS:
        return ShareAceChange.from_document(document)
    return InheritanceChange.from_document(document)


def _canonical_line(change: SimulationChange) -> str:
    """One change rendered as a sorted key/value line, for the digest.

    Sorted keys rather than insertion order so that adding a field with a default does not
    change the digest of every overlay that does not use it.
    """
    document = change.document()
    body = "\x1e".join(f"{key}={document[key]!r}" for key in sorted(document))
    return f"{change.kind.value}\x1e{body}"


def canonical_ace_flags(value: int | AceFlag | None) -> AceFlag:
    """An ACE flag byte as the domain enum, defaulting to no flags."""
    return AceFlag(0 if value is None else int(value))


def ordered_changes(changes: Sequence[SimulationChange]) -> tuple[SimulationChange, ...]:
    """Changes in the order an overlay reports them. Exposed for tests and renderers."""
    return SimulationOverlay.from_changes(changes).changes
