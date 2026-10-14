r"""What each relationship type means to a traversal: the one place that says so.

GRAPH-01A defined what a relationship *is* and GRAPH-01B derives the ones ADG can prove. Not
every relationship that exists is a step a privilege can take, and the decision of which are
belongs in one table rather than in the handlers that happen to walk the graph. This module is
that table (:data:`TRAVERSAL_SEMANTICS`), plus the two statements every answer needs beside
it: which traversal policies exist (:class:`TraversalPolicy`), and what the graph does not
model at all (:func:`coverage_gaps`).

## Four roles

* **Hop** — a privilege transition. ``member_of``, ``primary_group_of`` and
  ``local_group_member_of`` move a token's holder into a group; the five NTFS control types
  (``owns``, ``generic_all``, ``generic_write``, ``write_dacl``, ``write_owner``) end at the
  resource they control. Exactly these, and nothing else, are followed by path search.
* **Reading** — ``local_admin_of``, ``remote_desktop_user_of`` and
  ``remote_management_user_of``. Each is the projector's reading of one
  ``local_group_member_of`` edge into a host's BUILTIN group (architecture §9.3), with the
  same source, target and scope. Following both would walk one fact twice and return every
  route through a BUILTIN group twice, so a reading is **never a hop**: it is attached to the
  ``local_group_member_of`` step it reads, with its own derivation class and conditions, and
  a path that *ends* on one carries it into its own assessment (§9.11).
* **Informational** — ``foreign_principal_of``. It says which domain issued a foreign SID.
  That is provenance, not a privilege: a principal does not gain anything from the domain it
  came from, and a trust (which would be the privilege question) is not collected. Neighbor
  exploration shows it; path search never follows it.
* **Not traversable** — ``nested_member_of`` is a path, never a stored relationship
  (ADR-0002): a nested chain is its real direct hops. The five ``not_collected`` types have no
  premise ADG can store, so no relationship of theirs exists to traverse, and their absence
  from an answer says nothing about the estate (:func:`coverage_gaps`).

## Direction

Every relationship is directed and its sentence says what the direction means
(``RelationshipTypeDefinition.reads_as``). A hop is followed from source to target, which is
the direction a privilege flows: the member gains the group, the trustee controls the
resource. Reverse reachability walks the same relationships target-to-source to answer "who
can reach this?"; that changes which end a search starts from, never what an edge means.

## Conditional relationships

A conditional relationship holds only if conditions ADG did not evaluate hold. Path search
treats that explicitly, through a :class:`TraversalPolicy` the caller names and every answer
echoes: ``all_current`` follows every current hop and marks what it rests on; ``confirmed_only``
follows only hops whose confidence is ``confirmed`` and counts every hop it declined. Neither
drops a conditional relationship silently, and neither reports one as confirmed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.access_engine.subjects import (
    ANONYMOUS_LOGON_SID,
    AUTHENTICATED_USERS_SID,
    EVERYONE_SID,
    LOGON_SESSION_SIDS,
)
from app.domain.relationships import (
    RELATIONSHIP_TAXONOMY,
    CollectionStatus,
    DerivationStatus,
    RelationshipConfidence,
    RelationshipCurrency,
    RelationshipType,
)

__all__ = [
    "HOP_TYPES",
    "IMPLICIT_MEMBERSHIP_SIDS",
    "INFORMATIONAL_TYPES",
    "READING_TYPES",
    "STORED_TYPES",
    "TRAVERSAL_SEMANTICS",
    "CoverageGap",
    "RelationshipClass",
    "TraversalPolicy",
    "TraversalRole",
    "TraversalSemantics",
    "coverage_gaps",
    "semantics_of",
]


class TraversalRole(StrEnum):
    """What the path engine does with a relationship type."""

    HOP = "hop"
    """A privilege transition: followed by path search, source to target."""

    READING = "reading"
    """A specialization of a hop's fact. Attached to the hop it reads; never followed on its
    own."""

    INFORMATIONAL = "informational"
    """Shown by neighbor exploration. Never a privilege transition."""

    TRAVERSAL_ONLY = "traversal_only"
    """A path, never a stored relationship (``nested_member_of``)."""

    NOT_COLLECTED = "not_collected"
    """No collected fact can establish it. Never stored, never traversed, never fabricated."""


class RelationshipClass(StrEnum):
    """The kind of security statement a relationship makes. Classes are never merged: read
    access to a directory is not control of its server, membership of Remote Desktop Users is
    not a successful sign-in, and a foreign principal's domain is not a privilege over it."""

    IDENTITY_MEMBERSHIP = "identity_membership"
    HOST_LOCAL_MEMBERSHIP = "host_local_membership"
    HOST_LOCAL_PRIVILEGE = "host_local_privilege"
    CONDITIONAL_REMOTE_ACCESS = "conditional_remote_access"
    FILE_RESOURCE_CONTROL = "file_resource_control"
    INFORMATIONAL = "informational"
    DIRECTORY_OBJECT_CONTROL = "directory_object_control"
    GROUP_POLICY = "group_policy"
    TRUST = "trust"


@dataclass(frozen=True, slots=True)
class TraversalSemantics:
    """Everything the path engine states about one relationship type."""

    relationship_type: RelationshipType
    role: TraversalRole
    relationship_class: RelationshipClass
    label: str
    """A short human-readable name, for a client that renders an edge."""
    reverse_meaningful: bool
    """Whether walking it target-to-source answers a reverse-reachability question. True
    exactly for hops: who can reach a node is who holds a hop chain into it."""
    reads: RelationshipType | None = None
    """For a reading: the hop whose fact it reads."""
    always_conditional: bool = False
    """Every relationship of this type is conditional by construction (Remote Desktop and
    WinRM: the logon right, the service state and WinRM's own policy are not collected)."""
    note: str = ""

    @property
    def privilege_traversable(self) -> bool:
        """Followed by path search."""
        return self.role is TraversalRole.HOP


def _hop(
    relationship_type: RelationshipType,
    relationship_class: RelationshipClass,
    label: str,
    note: str,
) -> TraversalSemantics:
    return TraversalSemantics(
        relationship_type=relationship_type,
        role=TraversalRole.HOP,
        relationship_class=relationship_class,
        label=label,
        reverse_meaningful=True,
        note=note,
    )


def _reading(
    relationship_type: RelationshipType,
    relationship_class: RelationshipClass,
    label: str,
    note: str,
    *,
    always_conditional: bool,
) -> TraversalSemantics:
    return TraversalSemantics(
        relationship_type=relationship_type,
        role=TraversalRole.READING,
        relationship_class=relationship_class,
        label=label,
        reverse_meaningful=False,
        reads=RelationshipType.LOCAL_GROUP_MEMBER_OF,
        always_conditional=always_conditional,
        note=note,
    )


def _not_collected(
    relationship_type: RelationshipType, relationship_class: RelationshipClass, label: str
) -> TraversalSemantics:
    return TraversalSemantics(
        relationship_type=relationship_type,
        role=TraversalRole.NOT_COLLECTED,
        relationship_class=relationship_class,
        label=label,
        reverse_meaningful=False,
        note=(
            "Not collected: no fact ADG stores can establish it, so no relationship of it "
            "exists to traverse. Its absence from an answer says nothing about the estate."
        ),
    )


_CONTROL_NOTE: Final = (
    "NTFS control only: the target is always an NTFS resource. It is not Active Directory "
    "object control and it is not control of the server or the share. Reaching the resource "
    "over SMB also passes the share's own permissions, which the access engine evaluates."
)

TRAVERSAL_SEMANTICS: Final[Mapping[RelationshipType, TraversalSemantics]] = MappingProxyType(
    {
        RelationshipType.MEMBER_OF: _hop(
            RelationshipType.MEMBER_OF,
            RelationshipClass.IDENTITY_MEMBERSHIP,
            "Member of",
            "A direct membership. Nested membership is a chain of these, never a stored edge.",
        ),
        RelationshipType.PRIMARY_GROUP_OF: _hop(
            RelationshipType.PRIMARY_GROUP_OF,
            RelationshipClass.IDENTITY_MEMBERSHIP,
            "Primary group",
            "Membership through primaryGroupID; followed member to group, as the membership "
            "traversal already does.",
        ),
        RelationshipType.LOCAL_GROUP_MEMBER_OF: _hop(
            RelationshipType.LOCAL_GROUP_MEMBER_OF,
            RelationshipClass.HOST_LOCAL_MEMBERSHIP,
            "Member of local group",
            "A membership of one host's local group, valid on that host only. The same group "
            "SID on another host is another node.",
        ),
        RelationshipType.LOCAL_ADMIN_OF: _reading(
            RelationshipType.LOCAL_ADMIN_OF,
            RelationshipClass.HOST_LOCAL_PRIVILEGE,
            "Local administrator",
            "The reading of a local_group_member_of edge into the host's BUILTIN\\"
            "Administrators: administration of that host, and nothing wider. Remote use by a "
            "local account is subject to UAC remote token filtering, which a path that uses "
            "the group's rights reports as token_filtering_not_modeled.",
            always_conditional=False,
        ),
        RelationshipType.REMOTE_DESKTOP_USER_OF: _reading(
            RelationshipType.REMOTE_DESKTOP_USER_OF,
            RelationshipClass.CONDITIONAL_REMOTE_ACCESS,
            "Remote Desktop user (conditional)",
            "Membership of the host's Remote Desktop Users. Signing in also needs the logon "
            "right and the service, neither collected: never a statement that a sign-in "
            "succeeds.",
            always_conditional=True,
        ),
        RelationshipType.REMOTE_MANAGEMENT_USER_OF: _reading(
            RelationshipType.REMOTE_MANAGEMENT_USER_OF,
            RelationshipClass.CONDITIONAL_REMOTE_ACCESS,
            "Remote management user (conditional)",
            "Membership of the host's Remote Management Users. WinRM also needs its listener "
            "and its own access policy, neither collected: never a statement that a session "
            "succeeds.",
            always_conditional=True,
        ),
        RelationshipType.OWNS: _hop(
            RelationshipType.OWNS,
            RelationshipClass.FILE_RESOURCE_CONTROL,
            "Owns",
            "Ownership of an NTFS resource, restated from its descriptor. What ownership lets "
            "the owner do is its own relationship (write_dacl by the implicit grant). "
            + _CONTROL_NOTE,
        ),
        RelationshipType.GENERIC_ALL: _hop(
            RelationshipType.GENERIC_ALL,
            RelationshipClass.FILE_RESOURCE_CONTROL,
            "Full control",
            _CONTROL_NOTE,
        ),
        RelationshipType.GENERIC_WRITE: _hop(
            RelationshipType.GENERIC_WRITE,
            RelationshipClass.FILE_RESOURCE_CONTROL,
            "Generic write",
            "Writing the resource's data. " + _CONTROL_NOTE,
        ),
        RelationshipType.WRITE_DACL: _hop(
            RelationshipType.WRITE_DACL,
            RelationshipClass.FILE_RESOURCE_CONTROL,
            "Change permissions",
            _CONTROL_NOTE,
        ),
        RelationshipType.WRITE_OWNER: _hop(
            RelationshipType.WRITE_OWNER,
            RelationshipClass.FILE_RESOURCE_CONTROL,
            "Take ownership",
            _CONTROL_NOTE,
        ),
        RelationshipType.FOREIGN_PRINCIPAL_OF: TraversalSemantics(
            relationship_type=RelationshipType.FOREIGN_PRINCIPAL_OF,
            role=TraversalRole.INFORMATIONAL,
            relationship_class=RelationshipClass.INFORMATIONAL,
            label="Foreign principal of",
            reverse_meaningful=False,
            note=(
                "Which domain issued a foreign SID. Provenance, not a privilege: it is never "
                "a step of a privilege path, and trusts are not collected."
            ),
        ),
        RelationshipType.NESTED_MEMBER_OF: TraversalSemantics(
            relationship_type=RelationshipType.NESTED_MEMBER_OF,
            role=TraversalRole.TRAVERSAL_ONLY,
            relationship_class=RelationshipClass.IDENTITY_MEMBERSHIP,
            label="Member through nesting",
            reverse_meaningful=False,
            note=(
                "Never stored (ADR-0002). A path search returns a nested membership as its "
                "real member_of hops."
            ),
        ),
        RelationshipType.CAN_MODIFY_MEMBERSHIP: _not_collected(
            RelationshipType.CAN_MODIFY_MEMBERSHIP,
            RelationshipClass.DIRECTORY_OBJECT_CONTROL,
            "Can modify membership",
        ),
        RelationshipType.CONTROLS_GPO: _not_collected(
            RelationshipType.CONTROLS_GPO, RelationshipClass.GROUP_POLICY, "Controls GPO"
        ),
        RelationshipType.GPO_LINKED_TO: _not_collected(
            RelationshipType.GPO_LINKED_TO, RelationshipClass.GROUP_POLICY, "GPO linked to"
        ),
        RelationshipType.GPO_APPLIES_TO: _not_collected(
            RelationshipType.GPO_APPLIES_TO, RelationshipClass.GROUP_POLICY, "GPO applies to"
        ),
        RelationshipType.TRUSTS: _not_collected(
            RelationshipType.TRUSTS, RelationshipClass.TRUST, "Trusts"
        ),
    }
)
"""One entry per relationship type, enforced by ``tests/traversal/test_semantics.py``."""


def semantics_of(relationship_type: RelationshipType) -> TraversalSemantics:
    return TRAVERSAL_SEMANTICS[relationship_type]


def _types_with(role: TraversalRole) -> frozenset[RelationshipType]:
    return frozenset(item for item, entry in TRAVERSAL_SEMANTICS.items() if entry.role is role)


HOP_TYPES: Final = _types_with(TraversalRole.HOP)
READING_TYPES: Final = _types_with(TraversalRole.READING)
INFORMATIONAL_TYPES: Final = _types_with(TraversalRole.INFORMATIONAL)
STORED_TYPES: Final = HOP_TYPES | READING_TYPES | INFORMATIONAL_TYPES
"""Every type the projector derives and the graph can hold."""


IMPLICIT_MEMBERSHIP_SIDS: Final[frozenset[str]] = frozenset(
    {
        EVERYONE_SID,
        AUTHENTICATED_USERS_SID,
        ANONYMOUS_LOGON_SID,
        "S-1-5-9",  # ENTERPRISE DOMAIN CONTROLLERS
        "S-1-5-15",  # This Organization
        "S-1-5-1000",  # Other Organization
        *LOGON_SESSION_SIDS,
    }
)
"""SIDs Windows places in a token at logon rather than through a stored membership.

The access engine assumes them when it builds a token (``app.access_engine.subjects``). The
relationship graph holds no membership edge into them unless a source stated one, so a route
that passes through one — ``Authenticated Users`` named on a folder, ``INTERACTIVE`` inside a
host's ``Users`` group — is a route the graph cannot follow from any principal. A search that
meets one reports it as a boundary rather than reading its absence as "no path"."""


class TraversalPolicy(StrEnum):
    """Which current hops a search follows. Named by the caller and echoed by every answer."""

    ALL_CURRENT = "all_current"
    """Every current hop, whatever its confidence. A path that rests on a conditional or
    unresolved hop says so, at the step and on the path."""

    CONFIRMED_ONLY = "confirmed_only"
    """Only current hops whose confidence is ``confirmed``. Every hop declined is counted, and
    an answer that declined any cannot be a conclusive "no path"."""

    def permits(self, currency: RelationshipCurrency, confidence: RelationshipConfidence) -> bool:
        """Whether a hop with this currency and confidence is followed."""
        if currency is not RelationshipCurrency.CURRENT:
            return False
        if self is TraversalPolicy.CONFIRMED_ONLY:
            return confidence is RelationshipConfidence.CONFIRMED
        return True


@dataclass(frozen=True, slots=True)
class CoverageGap:
    """Something the relationship graph does not model, stated on every answer."""

    code: str
    summary: str
    relationship_types: tuple[RelationshipType, ...] = ()


def coverage_gaps() -> tuple[CoverageGap, ...]:
    """What an answer over today's graph cannot see, derived from the taxonomy.

    Computed rather than hand-listed for the relationship types, so a type that becomes
    collected stops being reported as a gap the moment its taxonomy entry says so.
    """
    not_collected = tuple(
        sorted(
            (
                relationship_type
                for relationship_type, definition in RELATIONSHIP_TAXONOMY.items()
                if definition.collection.status is CollectionStatus.NOT_COLLECTED
            ),
            key=lambda item: item.value,
        )
    )
    partial = tuple(
        sorted(
            (
                relationship_type
                for relationship_type, definition in RELATIONSHIP_TAXONOMY.items()
                if definition.collection.status is CollectionStatus.PARTIALLY_COLLECTED
                and definition.derivation.status is DerivationStatus.PROJECTOR
            ),
            key=lambda item: item.value,
        )
    )
    return (
        CoverageGap(
            "relationship_types_not_collected",
            "Membership modification, GPO control, linking and application, and domain trusts "
            "are not collected. No answer can contain them, and none rules them out.",
            not_collected,
        ),
        CoverageGap(
            "directory_object_control_not_collected",
            "Ownership and control are derived for NTFS resources only. Control of users, "
            "groups, computers, GPOs and domains (their security descriptors) is not "
            "collected.",
            partial,
        ),
        CoverageGap(
            "implicit_token_memberships",
            "Everyone, Authenticated Users and the logon-session SIDs are added to a token at "
            "logon and are not relationships, so a route through one is not followed from any "
            "principal. The access engine (/api/v1/access/explain) evaluates them.",
        ),
        CoverageGap(
            "cross_domain_membership",
            "A foreign principal's own members, and anything behind a trust, are outside the "
            "collected domain. A foreign principal is a boundary, not an end point.",
        ),
        CoverageGap(
            "share_layer",
            "NTFS control is stated at the NTFS layer. Reaching a resource over SMB also "
            "passes the share's permissions, which only the access engine evaluates.",
        ),
    )
