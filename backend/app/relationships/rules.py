r"""The projector's rules: which relationships stored facts establish, and how surely.

Pure functions from stored facts to :class:`app.domain.relationships.RelationshipAssertion`
values. Nothing here reads a database or writes one; :mod:`app.relationships.projector` loads
the facts and records what these rules produce. Keeping the rules pure is what makes every
one of them testable over a dictionary of facts, and what lets an incremental derivation and
a clean rebuild provably run the same code.

## One rule, one reading of one kind of fact

Every rule has a stable :class:`~app.domain.relationships.DerivationRule` id (:data:`RULES`),
and every support it produces carries that id and version. The id names the *mechanism* -
``ntfs.allow_entry``, ``ntfs.owner_implicit_rights`` - not the relationship type, which is
part of the relationship's identity already. A rule implementation may be refactored freely;
its version changes only when what it concludes from the same facts changes, and the
relationship's identity never does (ADR-0040 §2).

## What each family concludes

**Membership** (:func:`derive_edge`). One ``membership_edges`` row is one direct membership
hop, restated: ``member_of`` for directory (and source-stated well-known) edges,
``primary_group_of`` for ``primaryGroupID``, ``local_group_member_of`` for one machine's local
group. Together they are exactly the edge set the existing traversal (``app.domain.graph``)
walks, so a path over relationships and the membership API cannot disagree about who is in a
group. Nested membership is never derived here: it is a path, and a stored closure is the
representation ADR-0002 rejected. A cycle is therefore just edges, and derivation is per edge,
so it cannot recurse.

A member the collector reported as a foreign security principal, or could not resolve, makes
the membership ``unresolved`` with the qualifier saying which. That is the evidence's own
statement (the edge row carries it), never the absence of a ``principals`` row at derivation
time, which resolution-as-a-join (ADR-0007) keeps out of stored state.

**Local-group specializations.** A ``local_group_member`` edge into ``S-1-5-32-544``, ``-555``
or ``-580`` on one host is also ``local_admin_of``, ``remote_desktop_user_of`` or
``remote_management_user_of`` on that host. Local administration *is* that membership, so it
is deterministic. Signing in over Remote Desktop or using WinRM needs more than membership -
the logon right, the service running, its access policy - and none of that is collected, so
those two are always conditional and name exactly what was not evaluated. Every one of them
is host-scoped; an edge whose group carries no host is refused rather than unscoped.

**Foreign principals.** ``foreign_principal_of`` from either of two independent facts: an edge
the collector flagged as a foreign member, or a ``foreign_security_principal`` object. Both
are deterministic - the issuing domain is the SID's own prefix - and when both exist the one
relationship has two supports.

**NTFS descriptors** (:func:`derive_resource`). Ownership is the descriptor's owner SID,
observed. The four control types are read out of the DACL **as Windows evaluates it**, with
the access engine's own walk (:func:`app.access_engine.evaluate_acl`): ``INHERIT_ONLY``
entries grant nothing here, ``CREATOR OWNER`` never matches, generic bits go through the
file-system mapping, ``OWNER RIGHTS`` resolves to the owner, and entries count in the order the
descriptor stored them. A grant is a support only if the entry, on its own, delivers every
bit the type requires to *any* token holding the trustee - that is, after every Deny that
applies to all such tokens (the trustee itself, ``Everyone``, ``OWNER RIGHTS`` for the owner)
and precedes it. A Deny naming anybody else depends on the member's token, which a
trustee-level relationship cannot know, so it makes the support conditional and is recorded
as context evidence. An entry whose mask merely contains a matching bit is not enough.

Share ACEs are deliberately **not** read into these types. A share permission limits what a
session reaching the files through that share may do; it is not control of the share object,
and a share entry granting ``WRITE_DAC`` does not let its holder change the share's
permissions. Effective access over SMB stays the access engine's intersection (ADR-0010).

## What is never concluded

Nothing here infers a relationship from a fact ADG does not hold. Directory-object ACLs, GPOs
and trusts are not collected, so no rule produces ``can_modify_membership``,
``controls_gpo``, ``gpo_linked_to``, ``gpo_applies_to`` or ``trusts`` (:data:`UNDERIVED`), and
the absence of such a relationship says nothing about the estate.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final
from uuid import UUID

from app.access_engine.evaluation import AclEntry, DaclFacts, evaluate_acl, ntfs_entry
from app.access_engine.rights import (
    FILE_ALL_ACCESS,
    FILE_GENERIC_WRITE,
    AccessPath,
    RightsLayer,
)
from app.access_engine.subjects import (
    CREATOR_GROUP_SID,
    CREATOR_OWNER_SID,
    EVERYONE_SID,
    OWNER_RIGHTS_SID,
    SidOrigin,
    SubjectFacts,
    SubjectToken,
    TokenAssumption,
    TokenSid,
    build_token,
)
from app.domain.access import AceFlag, AceSource, AceType, NtfsRight
from app.domain.errors import DomainValidationError
from app.domain.identity import PrincipalKind, Sid, referenced_principal_key
from app.domain.membership import MembershipEdgeKind
from app.domain.relationships import (
    DerivationClass,
    DerivationRule,
    EvidenceKind,
    EvidenceRole,
    NodeKind,
    NodeRef,
    RelationshipAssertion,
    RelationshipCondition,
    RelationshipEvidence,
    RelationshipQualifier,
    RelationshipScope,
    RelationshipType,
)

__all__ = [
    "BUILTIN_SPECIALIZATIONS",
    "CONTROL_RIGHTS",
    "RULES",
    "UNDERIVED",
    "AceFact",
    "Derivation",
    "DerivationContext",
    "EdgeFact",
    "FactReading",
    "PrincipalFact",
    "ResourceFact",
    "RuleSpec",
    "SkippedUnit",
    "derive_edge",
    "derive_principal",
    "derive_resource",
]


# --- Rules ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuleSpec:
    """One projector rule: its stable identity, what it produces, and from what."""

    rule: DerivationRule
    produces: frozenset[RelationshipType]
    reads: frozenset[EvidenceKind]
    summary: str


MEMBERSHIP_DIRECTORY: Final = DerivationRule("membership.directory_edge", 1)
MEMBERSHIP_PRIMARY_GROUP: Final = DerivationRule("membership.primary_group_edge", 1)
MEMBERSHIP_LOCAL_GROUP: Final = DerivationRule("membership.local_group_edge", 1)
LOCAL_ADMINISTRATORS: Final = DerivationRule("local_group.builtin_administrators", 1)
LOCAL_REMOTE_DESKTOP_USERS: Final = DerivationRule("local_group.builtin_remote_desktop_users", 1)
LOCAL_REMOTE_MANAGEMENT_USERS: Final = DerivationRule(
    "local_group.builtin_remote_management_users", 1
)
FOREIGN_BY_EDGE: Final = DerivationRule("foreign_principal.membership_edge", 1)
FOREIGN_BY_OBJECT: Final = DerivationRule("foreign_principal.principal_object", 1)
NTFS_OWNER: Final = DerivationRule("ntfs.owner", 1)
NTFS_OWNER_IMPLICIT_RIGHTS: Final = DerivationRule("ntfs.owner_implicit_rights", 1)
NTFS_ALLOW_ENTRY: Final = DerivationRule("ntfs.allow_entry", 1)
NTFS_ALLOW_ENTRIES_COMBINED: Final = DerivationRule("ntfs.allow_entries_combined", 1)
NTFS_OWNER_RIGHTS_ENTRY: Final = DerivationRule("ntfs.owner_rights_entry", 1)
NTFS_NULL_DACL: Final = DerivationRule("ntfs.null_dacl", 1)

CONTROL_RIGHTS: Final[Mapping[RelationshipType, int]] = MappingProxyType(
    {
        RelationshipType.GENERIC_ALL: FILE_ALL_ACCESS,
        RelationshipType.GENERIC_WRITE: FILE_GENERIC_WRITE,
        RelationshipType.WRITE_DACL: int(NtfsRight.WRITE_DAC),
        RelationshipType.WRITE_OWNER: int(NtfsRight.WRITE_OWNER),
    }
)
"""The bits an NTFS grant must deliver, after generic mapping, to establish each type.

Every bit, not any: ``generic_all`` is ``FILE_ALL_ACCESS`` and ``generic_write`` is
``FILE_GENERIC_WRITE``, exactly the masks ``GENERIC_ALL`` and ``GENERIC_WRITE`` map to on a
file system object. A full-control entry therefore supports all four types, each by its own
reading, so a later lane asking "who can change this DACL" does not have to know that full
control implies it."""

BUILTIN_SPECIALIZATIONS: Final[Mapping[str, tuple[RelationshipType, DerivationRule]]] = (
    MappingProxyType(
        {
            "S-1-5-32-544": (RelationshipType.LOCAL_ADMIN_OF, LOCAL_ADMINISTRATORS),
            "S-1-5-32-555": (RelationshipType.REMOTE_DESKTOP_USER_OF, LOCAL_REMOTE_DESKTOP_USERS),
            "S-1-5-32-580": (
                RelationshipType.REMOTE_MANAGEMENT_USER_OF,
                LOCAL_REMOTE_MANAGEMENT_USERS,
            ),
        }
    )
)
"""Which local groups' membership the taxonomy reads as a host privilege."""

_CAPABILITY_CONDITIONS: Final[Mapping[RelationshipType, frozenset[RelationshipCondition]]] = (
    MappingProxyType(
        {
            RelationshipType.LOCAL_ADMIN_OF: frozenset(),
            RelationshipType.REMOTE_DESKTOP_USER_OF: frozenset(
                {
                    RelationshipCondition.LOGON_RIGHT_UNEVALUATED,
                    RelationshipCondition.REMOTE_SERVICE_STATE_UNKNOWN,
                }
            ),
            RelationshipType.REMOTE_MANAGEMENT_USER_OF: frozenset(
                {
                    RelationshipCondition.SERVICE_ACCESS_POLICY_UNEVALUATED,
                    RelationshipCondition.REMOTE_SERVICE_STATE_UNKNOWN,
                }
            ),
        }
    )
)
"""What a host privilege depends on beyond the membership itself.

Local administration is the membership, so nothing. Remote Desktop needs the logon right
(``SeRemoteInteractiveLogonRight``, which Windows assigns to the group by default and a GPO can
take away) and the service enabled; WinRM needs the service and its own security descriptor
(which admits the group by default). None of the three is collected."""

RULES: Final[Mapping[str, RuleSpec]] = MappingProxyType(
    {
        spec.rule.rule_id: spec
        for spec in (
            RuleSpec(
                MEMBERSHIP_DIRECTORY,
                frozenset({RelationshipType.MEMBER_OF}),
                frozenset({EvidenceKind.MEMBERSHIP_EDGE}),
                "A directory_group_member (or source-stated well_known_implicit) edge, "
                "restated as one direct membership.",
            ),
            RuleSpec(
                MEMBERSHIP_PRIMARY_GROUP,
                frozenset({RelationshipType.PRIMARY_GROUP_OF}),
                frozenset({EvidenceKind.MEMBERSHIP_EDGE}),
                "A primary_group edge (primaryGroupID), restated.",
            ),
            RuleSpec(
                MEMBERSHIP_LOCAL_GROUP,
                frozenset({RelationshipType.LOCAL_GROUP_MEMBER_OF}),
                frozenset({EvidenceKind.MEMBERSHIP_EDGE}),
                "A local_group_member edge, restated inside its host's scope.",
            ),
            RuleSpec(
                LOCAL_ADMINISTRATORS,
                frozenset({RelationshipType.LOCAL_ADMIN_OF}),
                frozenset({EvidenceKind.MEMBERSHIP_EDGE}),
                "Membership in one host's BUILTIN\\Administrators is local administration.",
            ),
            RuleSpec(
                LOCAL_REMOTE_DESKTOP_USERS,
                frozenset({RelationshipType.REMOTE_DESKTOP_USER_OF}),
                frozenset({EvidenceKind.MEMBERSHIP_EDGE}),
                "Membership in one host's Remote Desktop Users, conditional on the logon right "
                "and the service, neither collected.",
            ),
            RuleSpec(
                LOCAL_REMOTE_MANAGEMENT_USERS,
                frozenset({RelationshipType.REMOTE_MANAGEMENT_USER_OF}),
                frozenset({EvidenceKind.MEMBERSHIP_EDGE}),
                "Membership in one host's Remote Management Users, conditional on WinRM's "
                "access policy and the service, neither collected.",
            ),
            RuleSpec(
                FOREIGN_BY_EDGE,
                frozenset({RelationshipType.FOREIGN_PRINCIPAL_OF}),
                frozenset({EvidenceKind.MEMBERSHIP_EDGE}),
                "A member the collector flagged as foreign, attributed to its SID's domain.",
            ),
            RuleSpec(
                FOREIGN_BY_OBJECT,
                frozenset({RelationshipType.FOREIGN_PRINCIPAL_OF}),
                frozenset({EvidenceKind.PRINCIPAL}),
                "A foreign_security_principal object, attributed to its SID's domain.",
            ),
            RuleSpec(
                NTFS_OWNER,
                frozenset({RelationshipType.OWNS}),
                frozenset({EvidenceKind.NTFS_RESOURCE}),
                "The owner SID of an NTFS security descriptor.",
            ),
            RuleSpec(
                NTFS_OWNER_IMPLICIT_RIGHTS,
                frozenset({RelationshipType.WRITE_DACL}),
                frozenset({EvidenceKind.NTFS_RESOURCE}),
                "The owner's implicit WRITE_DAC, which no Deny removes, when no OWNER RIGHTS "
                "entry replaces it.",
            ),
            RuleSpec(
                NTFS_ALLOW_ENTRY,
                frozenset(CONTROL_RIGHTS),
                frozenset({EvidenceKind.NTFS_ACE, EvidenceKind.NTFS_RESOURCE}),
                "One Allow entry that, under the access check, delivers every required bit to "
                "any token holding its trustee.",
            ),
            RuleSpec(
                NTFS_ALLOW_ENTRIES_COMBINED,
                frozenset(CONTROL_RIGHTS),
                frozenset({EvidenceKind.NTFS_ACE, EvidenceKind.NTFS_RESOURCE}),
                "Several Allow entries for one trustee that together, and none alone, deliver "
                "every required bit. All of them are premises.",
            ),
            RuleSpec(
                NTFS_OWNER_RIGHTS_ENTRY,
                frozenset(CONTROL_RIGHTS),
                frozenset({EvidenceKind.NTFS_ACE, EvidenceKind.NTFS_RESOURCE}),
                "An OWNER RIGHTS Allow entry, which grants the descriptor's current owner; the "
                "entry and the owner are both premises.",
            ),
            RuleSpec(
                NTFS_NULL_DACL,
                frozenset(CONTROL_RIGHTS),
                frozenset({EvidenceKind.NTFS_RESOURCE}),
                "A descriptor with no DACL grants every token everything; stated as Everyone.",
            ),
        )
    }
)
"""Every projector rule by id. A rule not listed here produces nothing."""

UNDERIVED: Final = frozenset(
    {
        RelationshipType.NESTED_MEMBER_OF,
        RelationshipType.CAN_MODIFY_MEMBERSHIP,
        RelationshipType.CONTROLS_GPO,
        RelationshipType.GPO_LINKED_TO,
        RelationshipType.GPO_APPLIES_TO,
        RelationshipType.TRUSTS,
    }
)
"""Types no rule produces: one is a traversal, the rest have no collected premise."""


# --- Facts ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FactReading:
    """One stored fact as the derivation reads it: its identity and its latest observation.

    The observation is the current-state row's own ``last_observed_at`` and
    ``last_observed_run_id``, so the evidence a support records names a real
    ``observations`` row and a real point on the fact's ``object_versions`` timeline.
    """

    kind: EvidenceKind
    key: str
    run_id: UUID
    observed_at: dt.datetime

    def evidence(self, role: EvidenceRole = EvidenceRole.PREMISE) -> RelationshipEvidence:
        return RelationshipEvidence(self.kind, self.key, self.run_id, self.observed_at, role)


@dataclass(frozen=True, slots=True)
class EdgeFact:
    """One current ``membership_edges`` row."""

    reading: FactReading
    group_key: str
    member_key: str
    group_sid: str
    member_sid: str
    edge_kind: MembershipEdgeKind
    host_key: str | None
    member_kind: PrincipalKind | None
    is_foreign_security_principal: bool


@dataclass(frozen=True, slots=True)
class PrincipalFact:
    """One current ``principals`` row."""

    reading: FactReading
    principal_key: str
    sid: str
    principal_kind: PrincipalKind
    host_key: str | None


@dataclass(frozen=True, slots=True)
class AceFact:
    """One current ``ntfs_aces`` row."""

    reading: FactReading
    trustee_key: str
    trustee_sid: str
    ace_type: AceType
    access_mask: int
    ace_flags: int
    source: AceSource
    order_index: int | None

    @property
    def key(self) -> str:
        return self.reading.key


@dataclass(frozen=True, slots=True)
class ResourceFact:
    """One NTFS resource: its current descriptor row, if any, and its current entries.

    ``reading is None`` is an orphaned ACL - entries stored for a path no ``ntfs_resource``
    observation describes. The entries are real observations and are read, and everything
    the descriptor row would have settled (its owner, whether it has a DACL, how many entries
    it declared) is treated as unknown rather than assumed.
    """

    resource_key: str
    server_key: str
    reading: FactReading | None
    owner_sid: str | None
    dacl_present: bool
    declared_ace_count: int | None
    aces: tuple[AceFact, ...]
    """The current entries, in the order :func:`ordered_entries` puts them."""


@dataclass(frozen=True, slots=True)
class DerivationContext:
    """What every assertion of one derivation pass shares."""

    basis_token: str
    derived_at: dt.datetime


@dataclass(frozen=True, slots=True)
class SkippedUnit:
    """A fact the rules could not read, and why. Counted by the pass, never silently lost."""

    kind: EvidenceKind
    key: str
    reason: str


@dataclass(frozen=True, slots=True)
class Derivation:
    """What the rules concluded from one unit of facts."""

    assertions: tuple[RelationshipAssertion, ...] = ()
    skipped: tuple[SkippedUnit, ...] = ()

    def __add__(self, other: Derivation) -> Derivation:
        return Derivation(self.assertions + other.assertions, self.skipped + other.skipped)


@dataclass
class _Builder:
    """Accumulates one unit's assertions, turning a refused one into a skip."""

    context: DerivationContext
    assertions: list[RelationshipAssertion] = field(default_factory=list)
    skipped: list[SkippedUnit] = field(default_factory=list)

    def add(
        self,
        relationship_type: RelationshipType,
        source: NodeRef,
        target: NodeRef,
        *,
        derivation_class: DerivationClass,
        rule: DerivationRule,
        evidence: Iterable[RelationshipEvidence],
        scope: RelationshipScope | None,
        qualifiers: Iterable[RelationshipQualifier] = (),
        conditions: Iterable[RelationshipCondition] = (),
        attributes: Mapping[str, str | int | bool] | None = None,
        unit: tuple[EvidenceKind, str],
    ) -> None:
        try:
            self.assertions.append(
                RelationshipAssertion(
                    relationship_type=relationship_type,
                    source=source,
                    target=target,
                    derivation_class=derivation_class,
                    rule=rule,
                    evidence=tuple(evidence),
                    basis_token=self.context.basis_token,
                    derived_at=self.context.derived_at,
                    scope=scope,
                    qualifiers=frozenset(qualifiers),
                    conditions=frozenset(conditions),
                    attributes=dict(attributes or {}),
                )
            )
        except DomainValidationError as error:
            self.skip(unit, f"{relationship_type.value} by {rule}: {error}")

    def skip(self, unit: tuple[EvidenceKind, str], reason: str) -> None:
        self.skipped.append(SkippedUnit(unit[0], unit[1], reason))

    def result(self) -> Derivation:
        return Derivation(tuple(self.assertions), tuple(self.skipped))


def _node(kind: NodeKind, key: str) -> NodeRef | None:
    try:
        return NodeRef(kind, key)
    except DomainValidationError:
        return None


def _scope_of(*nodes: NodeRef) -> RelationshipScope | None:
    """The scope a ``from_endpoints`` type takes: the host of a host-local endpoint, if any."""
    hosts = sorted({node.host for node in nodes if node.host is not None})
    return RelationshipScope.host(hosts[0]) if hosts else None


# --- Membership ----------------------------------------------------------------------------


_MEMBERSHIP_TYPES: Final[Mapping[MembershipEdgeKind, tuple[RelationshipType, DerivationRule]]] = (
    MappingProxyType(
        {
            MembershipEdgeKind.DIRECTORY_GROUP_MEMBER: (
                RelationshipType.MEMBER_OF,
                MEMBERSHIP_DIRECTORY,
            ),
            # Recorded only when a source states it (app.domain.membership); the existing
            # traversal walks it as a hop, so the graph does too.
            MembershipEdgeKind.WELL_KNOWN_IMPLICIT: (
                RelationshipType.MEMBER_OF,
                MEMBERSHIP_DIRECTORY,
            ),
            MembershipEdgeKind.PRIMARY_GROUP: (
                RelationshipType.PRIMARY_GROUP_OF,
                MEMBERSHIP_PRIMARY_GROUP,
            ),
            MembershipEdgeKind.LOCAL_GROUP_MEMBER: (
                RelationshipType.LOCAL_GROUP_MEMBER_OF,
                MEMBERSHIP_LOCAL_GROUP,
            ),
        }
    )
)


def _member_unresolved(edge: EdgeFact) -> frozenset[RelationshipQualifier]:
    """What the edge itself says ADG cannot establish about its member."""
    if (
        edge.is_foreign_security_principal
        or edge.member_kind is PrincipalKind.FOREIGN_SECURITY_PRINCIPAL
    ):
        return frozenset({RelationshipQualifier.FOREIGN_PRINCIPAL})
    if edge.member_kind is PrincipalKind.UNRESOLVED:
        return frozenset({RelationshipQualifier.ENDPOINT_UNRESOLVED_AT_COLLECTION})
    return frozenset()


def derive_edge(edge: EdgeFact, context: DerivationContext) -> Derivation:
    """Everything one membership edge establishes."""
    unit = (EvidenceKind.MEMBERSHIP_EDGE, edge.reading.key)
    builder = _Builder(context)
    member = _node(NodeKind.PRINCIPAL, edge.member_key)
    group = _node(NodeKind.PRINCIPAL, edge.group_key)
    if member is None or group is None:
        builder.skip(unit, "an endpoint key is not a canonical principal key")
        return builder.result()

    relationship_type, rule = _MEMBERSHIP_TYPES[edge.edge_kind]
    unresolved = _member_unresolved(edge)
    attributes = {"edge_kind": edge.edge_kind.value}
    premise = (edge.reading.evidence(),)

    local = edge.edge_kind is MembershipEdgeKind.LOCAL_GROUP_MEMBER
    if local:
        # Fail closed: a local group means nothing without its machine, so an edge whose
        # group key carries no host is refused, never stored unscoped.
        if group.host is None or not edge.host_key:
            builder.skip(unit, "a host-local membership names no host")
            return builder.result()
        scope: RelationshipScope | None = RelationshipScope.host(group.host)
    else:
        scope = _scope_of(member, group)

    builder.add(
        relationship_type,
        member,
        group,
        derivation_class=(DerivationClass.UNRESOLVED if unresolved else DerivationClass.OBSERVED),
        rule=rule,
        evidence=premise,
        scope=scope,
        qualifiers=unresolved,
        attributes=attributes,
        unit=unit,
    )

    if local and scope is not None:
        specialization = BUILTIN_SPECIALIZATIONS.get(edge.group_sid)
        if specialization is not None:
            special_type, special_rule = specialization
            conditions = _CAPABILITY_CONDITIONS[special_type]
            if conditions:
                # Conditional, with the unresolved member stated as a qualifier, so neither
                # the conditions nor what is unresolved is lost.
                special_class = DerivationClass.CONDITIONALLY_DERIVED
            elif unresolved:
                special_class = DerivationClass.UNRESOLVED
            else:
                special_class = DerivationClass.DETERMINISTICALLY_DERIVED
            builder.add(
                special_type,
                member,
                group,
                derivation_class=special_class,
                rule=special_rule,
                evidence=premise,
                scope=scope,
                qualifiers=unresolved,
                conditions=conditions,
                attributes=attributes,
                unit=unit,
            )

    if RelationshipQualifier.FOREIGN_PRINCIPAL in unresolved:
        domain = _issuing_domain(edge.member_key)
        if domain is not None:
            builder.add(
                RelationshipType.FOREIGN_PRINCIPAL_OF,
                member,
                domain,
                derivation_class=DerivationClass.DETERMINISTICALLY_DERIVED,
                rule=FOREIGN_BY_EDGE,
                evidence=premise,
                scope=None,
                unit=unit,
            )
    return builder.result()


def _issuing_domain(principal_key: str) -> NodeRef | None:
    """The domain a domain-relative, non-host-local SID was issued by; ``None`` otherwise."""
    if "|" in principal_key:
        return None
    sid = Sid.try_parse(principal_key)
    domain = sid.domain_sid if sid is not None else None
    return None if domain is None else _node(NodeKind.DOMAIN, domain.value)


def derive_principal(principal: PrincipalFact, context: DerivationContext) -> Derivation:
    """What one principal object establishes on its own: only a foreign principal's domain."""
    builder = _Builder(context)
    if principal.principal_kind is not PrincipalKind.FOREIGN_SECURITY_PRINCIPAL:
        return builder.result()
    unit = (EvidenceKind.PRINCIPAL, principal.reading.key)
    source = _node(NodeKind.PRINCIPAL, principal.principal_key)
    domain = _issuing_domain(principal.principal_key)
    if source is None or domain is None:
        # A well-known SID (Authenticated Users) can sit in ForeignSecurityPrincipals and has
        # no issuing domain; that is not a defect, so it is not counted as a skip.
        return builder.result()
    builder.add(
        RelationshipType.FOREIGN_PRINCIPAL_OF,
        source,
        domain,
        derivation_class=DerivationClass.DETERMINISTICALLY_DERIVED,
        rule=FOREIGN_BY_OBJECT,
        evidence=(principal.reading.evidence(),),
        scope=None,
        unit=unit,
    )
    return builder.result()


# --- NTFS descriptors ----------------------------------------------------------------------


_PLACEHOLDERS: Final = frozenset({CREATOR_OWNER_SID, CREATOR_GROUP_SID})


def ordered_entries(aces: Iterable[AceFact]) -> tuple[AceFact, ...]:
    """Entries in descriptor order: ``order_index`` first, nulls last, then the key.

    The order the access service and the resource API already use, so all three read one
    DACL in the same sequence. Whether that sequence is *known* is a separate question,
    answered by :func:`_order_known`, because nulls sorted last are a convention, not the
    descriptor.
    """
    return tuple(
        sorted(
            aces,
            key=lambda ace: (
                ace.order_index is None,
                ace.order_index if ace.order_index is not None else 0,
                ace.key,
            ),
        )
    )


def _order_known(aces: Sequence[AceFact]) -> bool:
    return all(ace.order_index is not None for ace in aces)


def _entry(ace: AceFact) -> AclEntry:
    return ntfs_entry(
        trustee_key=ace.trustee_key,
        trustee_sid=ace.trustee_sid,
        ace_type=ace.ace_type,
        access_mask=ace.access_mask,
        flags=ace.ace_flags,
        source=ace.source,
        order_index=ace.order_index,
        ace_key=ace.key,
    )


def _applies(ace: AceFact) -> bool:
    """Whether the entry takes part in this object's access check at all."""
    return not (ace.ace_flags & AceFlag.INHERIT_ONLY) and ace.trustee_sid not in _PLACEHOLDERS


def _normalized(ace: AceFact) -> int:
    return _entry(ace).normalized.normalized.value


@dataclass(frozen=True, slots=True)
class _Dacl:
    """One descriptor, prepared once for every trustee it names."""

    resource: ResourceFact
    target: NodeRef
    entries: tuple[AceFact, ...]
    """Every stored entry, in descriptor order."""
    owner_key: str | None
    complete: bool
    ordered: bool
    facts: DaclFacts

    @property
    def owner_rights_entries(self) -> tuple[AceFact, ...]:
        return tuple(
            ace for ace in self.entries if ace.trustee_sid == OWNER_RIGHTS_SID and _applies(ace)
        )

    def matches_every_holder(self, ace: AceFact, trustee_key: str) -> bool:
        """Whether an entry applies to every token holding ``trustee_key``.

        The trustee itself and ``Everyone``, which Windows places in every token, and
        ``OWNER RIGHTS`` when the trustee is the owner. Nothing else is in *every* holder's
        token: another group, ``Authenticated Users``, a logon-session SID all depend on who
        the holder is and how it signed in.
        """
        if ace.trustee_sid == OWNER_RIGHTS_SID:
            return self.owner_key == trustee_key
        return ace.trustee_key in (trustee_key, EVERYONE_SID)

    def own_allows(self, trustee_key: str) -> tuple[AceFact, ...]:
        """The Allow entries that grant ``trustee_key`` itself, in descriptor order."""
        return tuple(
            ace
            for ace in self.entries
            if ace.ace_type is AceType.ALLOW
            and _applies(ace)
            and (
                (ace.trustee_key == trustee_key and ace.trustee_sid != OWNER_RIGHTS_SID)
                or (ace.trustee_sid == OWNER_RIGHTS_SID and self.owner_key == trustee_key)
            )
        )


def _token(trustee_key: str, trustee_sid: str) -> SubjectToken:
    """A token holding exactly the trustee and ``Everyone``: what every holder shares."""
    groups = (
        ()
        if trustee_key == EVERYONE_SID
        else (TokenSid(key=EVERYONE_SID, sid=EVERYONE_SID, origin=SidOrigin.WELL_KNOWN),)
    )
    return build_token(
        SubjectFacts(key=trustee_key, sid=trustee_sid),
        groups,
        access_path=AccessPath.LOCAL,
        assumption=TokenAssumption.SIDS_ONLY,
    )


def _delivered(dacl: _Dacl, grants: Sequence[AceFact], trustee_key: str, sid: str) -> int:
    """The bits ``grants`` deliver to every token holding the trustee, under the access check.

    The walk is :func:`app.access_engine.evaluate_acl` itself, over the descriptor with every
    *other* Allow removed and every Deny kept: so a Deny for the trustee or for ``Everyone``
    ahead of a grant takes its bits away exactly as Windows would, a later one does not, and an
    entry the token cannot hold (``CREATOR OWNER``) or that applies only to children
    (``INHERIT_ONLY``) delivers nothing. The owner's implicit rights are excluded: they are
    their own rule (:data:`NTFS_OWNER_IMPLICIT_RIGHTS`), not a property of any entry.

    When the descriptor's order is unknown the Denies are left out of the walk: sorting them
    somewhere would decide, by convention, whether they precede the grant. The support is then
    conditional on that order instead (:func:`_qualifying_denies`).
    """
    keys = {ace.key for ace in grants}
    entries = [
        _entry(ace)
        for ace in dacl.entries
        if ace.key in keys or (dacl.ordered and ace.ace_type is AceType.DENY)
    ]
    evaluation = evaluate_acl(
        entries, _token(trustee_key, sid), layer=RightsLayer.NTFS, facts=dacl.facts
    )
    delivered = 0
    for applied in evaluation.granted_by:
        if applied.entry.ace_key in keys:
            delivered |= applied.contributed.value
    return delivered


def _qualifying_denies(
    dacl: _Dacl, grants: Sequence[AceFact], trustee_key: str, required: int
) -> tuple[frozenset[RelationshipCondition], tuple[AceFact, ...]]:
    """What could still take the right away from some holder, and which entries say so.

    * a Deny for a trustee only *some* holders carry (another group, a logon-session SID,
      ``OWNER RIGHTS`` when the trustee is not the owner) that precedes a grant;
    * any applicable Deny at all, when the descriptor's order is not known, because then
      whether it precedes the grant is not known either.
    """
    conditions: set[RelationshipCondition] = set()
    context: list[AceFact] = []
    for ace in dacl.entries:
        if ace.ace_type is not AceType.DENY or not _applies(ace):
            continue
        if not _normalized(ace) & required:
            continue
        if not dacl.ordered:
            conditions.add(RelationshipCondition.ACL_ORDER_UNKNOWN)
            if not dacl.matches_every_holder(ace, trustee_key):
                conditions.add(RelationshipCondition.DENY_MAY_APPLY)
            context.append(ace)
            continue
        if dacl.matches_every_holder(ace, trustee_key):
            # Already applied by the walk: if it preceded the grant, the bits are gone.
            continue
        if any(
            ace.order_index is not None
            and grant.order_index is not None
            and ace.order_index < grant.order_index
            for grant in grants
        ):
            conditions.add(RelationshipCondition.DENY_MAY_APPLY)
            context.append(ace)
    return frozenset(conditions), tuple(context)


def derive_resource(resource: ResourceFact, context: DerivationContext) -> Derivation:
    """Everything one NTFS descriptor and its entries establish."""
    builder = _Builder(context)
    unit = (EvidenceKind.NTFS_RESOURCE, resource.resource_key)
    target = _node(NodeKind.NTFS_RESOURCE, resource.resource_key)
    if target is None:
        builder.skip(unit, "the resource key is not a canonical UNC key")
        return builder.result()

    owner_key: str | None = None
    if resource.owner_sid is not None:
        owner = Sid.try_parse(resource.owner_sid)
        if owner is None:
            builder.skip(unit, f"the owner {resource.owner_sid!r} is not a SID")
        else:
            # The descriptor was read on its server, so a BUILTIN owner is that machine's
            # local group: the access service's rule, through the same function.
            owner_key = referenced_principal_key(owner, resource.server_key)

    entries = ordered_entries(resource.aces)
    complete = resource.reading is not None and resource.declared_ace_count == len(resource.aces)
    dacl = _Dacl(
        resource=resource,
        target=target,
        entries=entries,
        owner_key=owner_key,
        complete=complete,
        ordered=_order_known(entries),
        facts=DaclFacts(
            dacl_present=True,
            owner_sid=resource.owner_sid if owner_key is not None else None,
            owner_key=owner_key,
        ),
    )

    _derive_ownership(builder, dacl, unit)
    if resource.reading is not None and not resource.dacl_present:
        _derive_null_dacl(builder, dacl, unit)
        return builder.result()
    _derive_entries(builder, dacl, unit)
    return builder.result()


def _derive_ownership(builder: _Builder, dacl: _Dacl, unit: tuple[EvidenceKind, str]) -> None:
    resource = dacl.resource
    if resource.reading is None or dacl.owner_key is None:
        return
    owner = _node(NodeKind.PRINCIPAL, dacl.owner_key)
    if owner is None:
        builder.skip(unit, f"the owner key {dacl.owner_key!r} is not a canonical principal key")
        return
    premise = resource.reading.evidence()
    scope = _scope_of(owner)
    builder.add(
        RelationshipType.OWNS,
        owner,
        dacl.target,
        derivation_class=DerivationClass.OBSERVED,
        rule=NTFS_OWNER,
        evidence=(premise,),
        scope=scope,
        unit=unit,
    )
    null_dacl = not resource.dacl_present
    if dacl.owner_rights_entries and not null_dacl:
        # An OWNER RIGHTS entry replaces the implicit grant rather than adding to it; what the
        # owner holds then is what those entries say (NTFS_OWNER_RIGHTS_ENTRY).
        return
    # Without a DACL there is nothing an unseen entry could change; with one, an entry ADG
    # does not hold could be an OWNER RIGHTS entry that replaces this grant.
    conditions = (
        frozenset()
        if dacl.complete or null_dacl
        else frozenset({RelationshipCondition.ACL_ENTRIES_INCOMPLETE})
    )
    builder.add(
        RelationshipType.WRITE_DACL,
        owner,
        dacl.target,
        derivation_class=(
            DerivationClass.CONDITIONALLY_DERIVED
            if conditions
            else DerivationClass.DETERMINISTICALLY_DERIVED
        ),
        rule=NTFS_OWNER_IMPLICIT_RIGHTS,
        evidence=(premise,),
        scope=scope,
        conditions=conditions,
        unit=unit,
    )


def _derive_null_dacl(builder: _Builder, dacl: _Dacl, unit: tuple[EvidenceKind, str]) -> None:
    """No DACL at all: every token holds every right, which ``Everyone`` names."""
    resource = dacl.resource
    assert resource.reading is not None
    everyone = NodeRef.principal(EVERYONE_SID)
    for relationship_type in CONTROL_RIGHTS:
        builder.add(
            relationship_type,
            everyone,
            dacl.target,
            derivation_class=DerivationClass.DETERMINISTICALLY_DERIVED,
            rule=NTFS_NULL_DACL,
            evidence=(resource.reading.evidence(),),
            scope=None,
            unit=unit,
        )


def _derive_entries(builder: _Builder, dacl: _Dacl, unit: tuple[EvidenceKind, str]) -> None:
    trustees: dict[str, str] = {}
    for ace in dacl.entries:
        if ace.ace_type is not AceType.ALLOW or not _applies(ace):
            continue
        if ace.trustee_sid == OWNER_RIGHTS_SID:
            if dacl.owner_key is not None and dacl.facts.owner_sid is not None:
                trustees.setdefault(dacl.owner_key, dacl.facts.owner_sid)
            continue
        trustees.setdefault(ace.trustee_key, ace.trustee_sid)

    for trustee_key, trustee_sid in sorted(trustees.items()):
        source = _node(NodeKind.PRINCIPAL, trustee_key)
        if source is None:
            builder.skip(unit, f"the trustee key {trustee_key!r} is not a canonical principal key")
            continue
        allows = dacl.own_allows(trustee_key)
        for relationship_type, required in CONTROL_RIGHTS.items():
            supported = False
            for grant in allows:
                if _normalized(grant) & required != required:
                    continue
                if _delivered(dacl, (grant,), trustee_key, trustee_sid) & required != required:
                    continue
                supported = True
                _add_grant(builder, dacl, unit, relationship_type, source, (grant,), required)
            if supported or len(allows) < 2:
                continue
            # No single entry delivers every bit; several together may. The support is then
            # their conjunction, so removing any one of them ends it.
            contributing = tuple(grant for grant in allows if _normalized(grant) & required)
            if len(contributing) < 2:
                continue
            delivered = _delivered(dacl, contributing, trustee_key, trustee_sid)
            if delivered & required == required:
                _add_grant(builder, dacl, unit, relationship_type, source, contributing, required)


def _add_grant(
    builder: _Builder,
    dacl: _Dacl,
    unit: tuple[EvidenceKind, str],
    relationship_type: RelationshipType,
    source: NodeRef,
    grants: Sequence[AceFact],
    required: int,
) -> None:
    conditions, denies = _qualifying_denies(dacl, grants, source.key, required)
    if not dacl.complete:
        conditions = conditions | {RelationshipCondition.ACL_ENTRIES_INCOMPLETE}
    owner_rights = any(grant.trustee_sid == OWNER_RIGHTS_SID for grant in grants)
    evidence = [grant.reading.evidence() for grant in grants]
    if owner_rights:
        # OWNER RIGHTS grants whoever owns the object, so the owner is a premise as well: a
        # change of owner ends this support even though the entry is still there.
        assert dacl.resource.reading is not None
        evidence.append(dacl.resource.reading.evidence())
    elif dacl.resource.reading is not None:
        evidence.append(dacl.resource.reading.evidence(EvidenceRole.CONTEXT))
    evidence.extend(ace.reading.evidence(EvidenceRole.CONTEXT) for ace in denies)

    if len(grants) > 1:
        rule = NTFS_ALLOW_ENTRIES_COMBINED
        attributes: dict[str, str | int | bool] = {}
    else:
        (grant,) = grants
        rule = NTFS_OWNER_RIGHTS_ENTRY if owner_rights else NTFS_ALLOW_ENTRY
        attributes = {
            "access_mask": grant.access_mask,
            "ace_type": grant.ace_type.value,
            "inherited": grant.source is AceSource.INHERITED,
        }
    builder.add(
        relationship_type,
        source,
        dacl.target,
        derivation_class=(
            DerivationClass.CONDITIONALLY_DERIVED
            if conditions
            else DerivationClass.DETERMINISTICALLY_DERIVED
        ),
        rule=rule,
        evidence=evidence,
        scope=_scope_of(source),
        conditions=conditions,
        attributes=attributes,
        unit=unit,
    )
