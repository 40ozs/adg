r"""Privilege relationships: what a graph edge means, and how ADG can prove it.

ADG already stores the facts a privilege graph is made of — membership edges, share and NTFS
access-control entries, owners — and already traverses one of them (``app.domain.graph``).
This module is the contract for the **general** relationship layer that later phases derive
from those facts: a typed, directed relationship between two canonical ADG objects, each one
traceable to the observations that produced it.

It is deliberately a domain module only. Nothing here reads a database, derives a
relationship from a stored fact, or walks a graph. The projector that turns facts into
relationships is :mod:`app.relationships` (ADG-GRAPH-01B), and the traversal that answers
"nested membership" already exists and stays where it is.

## The four rules this module enforces

**A relationship is a conclusion, never a fact** (ADR-0003). Even a relationship that
restates one collected fact one-to-one — ``alice member_of Finance-RW`` from one
``membership_edges`` row — is a *projection* of that fact, and it carries a reference to the
row rather than a copy of it. The raw fact stays authoritative; a relationship can be thrown
away and re-derived at any time without losing anything a collector saw.

**A relationship is only as strong as its strongest current support.** One relationship can
be supported in several independent ways: Bob may hold ``WRITE_DAC`` on a folder through an
explicit entry *and* through an inherited one. Each independent derivation is a
:class:`RelationshipSupport` with its own evidence, derivation class and observation window,
and the relationship's own class, confidence and currency are **folded** from its supports
(:meth:`StoredRelationship.best_support`). Treating every piece of evidence as a premise of one
conjunction would declare the relationship gone the moment either entry was removed, which is
the over-statement of *absence* this product exists to avoid.

**Unknown is never folded into none.** Currency has three values, not two: a support whose
premise has no stored row is :attr:`RelationshipCurrency.UNKNOWN`, not
:attr:`RelationshipCurrency.NOT_CURRENT`. Only a measured absence — an open tombstone written
by an authoritative reconciliation (``app.models.current``) — makes a premise absent. The one
other way a support stops being current is **retirement**: its facts are still stored and,
as they now read, the rule that produced the support no longer does (an owner changed, a Deny
appeared ahead of the grant). That is measured too — by the projector reading the stored fact
— and it is recorded on the support, never inferred from a missing row.

**Nothing derivable is stored.** The relationship's identity, its confidence, its currency and
its explanation are all *computed* — the identity from its components, the confidence from
the derivation class and qualifiers, the currency from the evidence's presence, the
explanation from the type's sentence and the endpoints' current labels. A stored copy of any
of them is a second value that could disagree with the first (ADR-0007).

## What is deliberately not here

* **Transitive relationships are never stored.** ``nested_member_of`` is a member of the
  taxonomy so that a traversal result has a name, but its
  :attr:`Materialization.TRAVERSAL_ONLY` forbids an assertion of it: storing a closure is the
  alternative ADR-0002 rejected.
* **No numeric confidence.** :class:`RelationshipConfidence` is a closed ordinal vocabulary
  aligned with the risk engine's ``Confidence``, for the reasons ADR-0011 rejected a score.
* **No resolution is stored.** Whether an endpoint is a principal ADG has described is a join
  performed on read (ADR-0007). :attr:`DerivationClass.UNRESOLVED` is a statement the
  *evidence itself* made — a collector reported a foreign or unresolvable member — not the
  absence of a ``principals`` row at the moment of derivation.

See ``docs/architecture/graph-relationships.md`` and ADR-0040.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID

from app.domain.errors import DomainValidationError
from app.domain.identity import Sid

__all__ = [
    "ATTRIBUTE_VALUE_MAX_LENGTH",
    "BASIS_TOKEN_LENGTH",
    "CONDITION_DESCRIPTIONS",
    "CONFIDENCE_ORDER",
    "DERIVATION_CLASS_ORDER",
    "IDENTITY_SEPARATOR",
    "KEY_MAX_LENGTH",
    "QUALIFIER_DESCRIPTIONS",
    "RELATIONSHIP_DIGEST_LENGTH",
    "RELATIONSHIP_TAXONOMY",
    "RULE_ID_PATTERN",
    "UNRESOLVED_QUALIFIERS",
    "AdjacencyDirection",
    "AttributeSpec",
    "CollectionStatus",
    "CollectionSupport",
    "DerivationClass",
    "DerivationRule",
    "DerivationStatus",
    "DerivationSupport",
    "EvidenceKind",
    "EvidenceRole",
    "EvidenceState",
    "Materialization",
    "NodeKind",
    "NodeRef",
    "ProjectionStatus",
    "ProjectionTrigger",
    "RelationshipAssertion",
    "RelationshipCondition",
    "RelationshipConfidence",
    "RelationshipCurrency",
    "RelationshipEvidence",
    "RelationshipQualifier",
    "RelationshipScope",
    "RelationshipScopeKind",
    "RelationshipSupport",
    "RelationshipType",
    "RelationshipTypeDefinition",
    "ScopeRule",
    "StoredEvidence",
    "StoredRelationship",
    "confidence_of",
    "currency_of",
    "definition_of",
    "describe_condition",
    "describe_qualifier",
    "explain",
    "relationship_id",
    "support_key",
]

KEY_MAX_LENGTH: Final = 512
"""The longest key any canonical table stores (``app.models.schema.KEY_LENGTH``)."""

RELATIONSHIP_DIGEST_LENGTH: Final = 64
"""Hex characters of a relationship id and of a support key: a full SHA-256.

Full width deliberately, as for ``object_versions.state_hash``: the digest *is* the identity,
and a truncated one would eventually merge two different relationships into one row."""

BASIS_TOKEN_LENGTH: Final = 32
"""``app.domain.basis.TOKEN_LENGTH``: the collection basis a support was derived against."""

IDENTITY_SEPARATOR: Final = "\x1f"
"""ASCII unit separator. It cannot occur in a SID, a host name, a share name or a UNC path, so
joining identity components with it is unambiguous — and the database recomputes the same
digest with ``chr(31)`` in a check constraint (``ck_graph_relationships_id_is_digest``)."""

RULE_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")
"""A rule id is a dotted lower-case name such as ``membership.direct_edge``."""

ATTRIBUTE_VALUE_MAX_LENGTH: Final = 1024


# --- Vocabulary ----------------------------------------------------------------------------


class NodeKind(StrEnum):
    """What a relationship endpoint is, which decides which canonical table names it.

    A graph node is **a reference to an existing canonical ADG object**, never a node row of
    its own: a second identity universe would be a second place for an object to be described
    and a second spelling of its key to drift. The key is exactly the primary key the
    canonical table already uses (:attr:`canonical_table`).

    Three kinds have no table yet. ``domain`` is keyed by the domain SID, which is how
    :class:`app.domain.DomainIdentifier` and every ``principals.domain_sid`` already identify a
    domain. ``gpo`` and ``directory_container`` are reserved for collectors that do not exist:
    they are defined so the taxonomy can name what a GPO relationship *would* connect, and no
    relationship of theirs can be collected today (:class:`CollectionStatus`).
    """

    PRINCIPAL = "principal"
    """``principals.principal_key``: a SID, or ``<host>|<SID>`` for a host-local principal."""

    SERVER = "server"
    """``servers.server_key``: the case-folded host name the collector addressed."""

    SMB_SHARE = "smb_share"
    """``smb_shares.share_key``: ``<server>|<share>``, case-folded."""

    NTFS_RESOURCE = "ntfs_resource"
    """``ntfs_resources.resource_key``: the case-folded canonical UNC path."""

    DOMAIN = "domain"
    """A domain SID (``S-1-5-21-x-y-z``). No table: identified as ``DomainIdentifier`` is."""

    GPO = "gpo"
    """Reserved. ``<domain SID>|<GUID>``: a GPO's GUID is unique only within its domain."""

    DIRECTORY_CONTAINER = "directory_container"
    """Reserved. An OU or site, keyed by its lower-case ``objectGUID`` — never its DN, which a
    rename or a move changes."""

    @property
    def canonical_table(self) -> str | None:
        """The table whose primary key this kind's keys are, or ``None`` if none exists yet."""
        return _CANONICAL_TABLES[self]

    @property
    def is_reserved(self) -> bool:
        """Defined for the taxonomy, with no canonical object and no collector behind it."""
        return self in (NodeKind.GPO, NodeKind.DIRECTORY_CONTAINER)


_CANONICAL_TABLES: Final[dict[NodeKind, str | None]] = {
    NodeKind.PRINCIPAL: "principals",
    NodeKind.SERVER: "servers",
    NodeKind.SMB_SHARE: "smb_shares",
    NodeKind.NTFS_RESOURCE: "ntfs_resources",
    NodeKind.DOMAIN: None,
    NodeKind.GPO: None,
    NodeKind.DIRECTORY_CONTAINER: None,
}


class DerivationClass(StrEnum):
    """How a support establishes its relationship. Ordered strongest first.

    The distinction is between *what was seen* and *what follows from it*, and between what
    follows unconditionally and what follows only if something ADG did not evaluate holds.
    """

    OBSERVED = "observed"
    """The relationship is one collected fact, restated without interpretation: a
    ``member`` value, a ``primaryGroupID``, an owner SID. Exactly one premise."""

    DETERMINISTICALLY_DERIVED = "deterministically_derived"
    """Follows necessarily from observed facts by a rule with no assumption: a local-group
    membership into ``S-1-5-32-544`` *is* local-administrators membership; an allow entry
    whose mask carries ``WRITE_DAC`` *is* an entry granting it."""

    CONDITIONALLY_DERIVED = "conditionally_derived"
    """Holds only if each named :class:`RelationshipCondition` holds — something that would
    change the answer and that ADG did not evaluate. Never presented without them."""

    UNRESOLVED = "unresolved"
    """The evidence shows a relationship exists, and the evidence itself says ADG cannot
    establish what it connects: a foreign security principal, a SID the collector could not
    resolve. Exists; meaning unknown. Never rendered as absence."""


DERIVATION_CLASS_ORDER: Final[dict[DerivationClass, int]] = {
    DerivationClass.UNRESOLVED: 0,
    DerivationClass.CONDITIONALLY_DERIVED: 1,
    DerivationClass.DETERMINISTICALLY_DERIVED: 2,
    DerivationClass.OBSERVED: 3,
}
"""Rank, weakest first."""


class RelationshipQualifier(StrEnum):
    """Something about a support's evidence that keeps it short of complete.

    A closed vocabulary, because a consumer — a risk rule, a UI — branches on it. Qualifiers
    record what the *derivation* knew about its inputs when it ran; they do not change once a
    support is written, because the inputs they describe (a run's terminal status, an origin
    marker) do not change either.
    """

    FOREIGN_PRINCIPAL = "foreign_principal"
    ENDPOINT_UNRESOLVED_AT_COLLECTION = "endpoint_unresolved_at_collection"
    EVIDENCE_FROM_PARTIAL_RUN = "evidence_from_partial_run"
    EVIDENCE_BACKFILLED = "evidence_backfilled"
    DERIVATION_TRUNCATED = "derivation_truncated"


QUALIFIER_DESCRIPTIONS: Final[dict[RelationshipQualifier, str]] = {
    RelationshipQualifier.FOREIGN_PRINCIPAL: (
        "An endpoint is a foreign security principal: ADG holds its SID, and its home domain "
        "is outside what any collector enumerates."
    ),
    RelationshipQualifier.ENDPOINT_UNRESOLVED_AT_COLLECTION: (
        "The collector reported an endpoint it could not resolve to an account."
    ),
    RelationshipQualifier.EVIDENCE_FROM_PARTIAL_RUN: (
        "Evidence came from a run that completed partially; the coverage around it is not complete."
    ),
    RelationshipQualifier.EVIDENCE_BACKFILLED: (
        "Evidence was reconstructed by the history migration rather than observed with a "
        "timeline; whether it changed and changed back in between is not known."
    ),
    RelationshipQualifier.DERIVATION_TRUNCATED: (
        "The derivation reached a traversal limit; more supports of this kind may exist."
    ),
}

UNRESOLVED_QUALIFIERS: Final = frozenset(
    {
        RelationshipQualifier.FOREIGN_PRINCIPAL,
        RelationshipQualifier.ENDPOINT_UNRESOLVED_AT_COLLECTION,
    }
)
"""An :attr:`DerivationClass.UNRESOLVED` support must name at least one of these: an
unresolved relationship that does not say *what* is unresolved cannot be investigated."""


class RelationshipCondition(StrEnum):
    """A condition a :attr:`DerivationClass.CONDITIONALLY_DERIVED` support depends on.

    Each names something that would change the answer and that ADG did not evaluate. Present
    only on conditional supports; a conditional support with none is refused, because a
    condition nobody can see is one nobody can check.
    """

    NO_DENY_EVALUATED = "no_deny_evaluated"
    INHERITANCE_UNRESOLVED = "inheritance_unresolved"
    TOKEN_FILTERING_NOT_MODELED = "token_filtering_not_modeled"
    OBJECT_TYPE_SCOPE_UNEVALUATED = "object_type_scope_unevaluated"
    GPO_LINK_STATE_UNKNOWN = "gpo_link_state_unknown"
    GPO_SECURITY_FILTERING_UNEVALUATED = "gpo_security_filtering_unevaluated"
    GPO_WMI_FILTER_UNEVALUATED = "gpo_wmi_filter_unevaluated"
    GPO_INHERITANCE_BLOCKING_UNEVALUATED = "gpo_inheritance_blocking_unevaluated"
    TRUST_SID_FILTERING_UNKNOWN = "trust_sid_filtering_unknown"
    TRUST_SELECTIVE_AUTHENTICATION_UNKNOWN = "trust_selective_authentication_unknown"
    DENY_MAY_APPLY = "deny_may_apply"
    ACL_ENTRIES_INCOMPLETE = "acl_entries_incomplete"
    ACL_ORDER_UNKNOWN = "acl_order_unknown"
    LOGON_RIGHT_UNEVALUATED = "logon_right_unevaluated"
    SERVICE_ACCESS_POLICY_UNEVALUATED = "service_access_policy_unevaluated"
    REMOTE_SERVICE_STATE_UNKNOWN = "remote_service_state_unknown"


CONDITION_DESCRIPTIONS: Final[dict[RelationshipCondition, str]] = {
    RelationshipCondition.NO_DENY_EVALUATED: (
        "Holds only if no Deny entry removes it; Deny entries were not evaluated."
    ),
    RelationshipCondition.INHERITANCE_UNRESOLVED: (
        "Depends on how an inheritable entry propagates, which was not resolved."
    ),
    RelationshipCondition.TOKEN_FILTERING_NOT_MODELED: (
        "UAC remote token filtering can reduce a local administrator to a deny-only member "
        "for network logons; it was not modeled."
    ),
    RelationshipCondition.OBJECT_TYPE_SCOPE_UNEVALUATED: (
        "The entry is limited to an object type or attribute; the limit was not evaluated."
    ),
    RelationshipCondition.GPO_LINK_STATE_UNKNOWN: (
        "Whether the link is enabled or enforced is not known."
    ),
    RelationshipCondition.GPO_SECURITY_FILTERING_UNEVALUATED: (
        "Holds only if the GPO's security filtering includes the target; it was not evaluated."
    ),
    RelationshipCondition.GPO_WMI_FILTER_UNEVALUATED: (
        "Holds only if the GPO's WMI filter matches the target; it was not evaluated."
    ),
    RelationshipCondition.GPO_INHERITANCE_BLOCKING_UNEVALUATED: (
        "Holds only if no container in between blocks inheritance; it was not evaluated."
    ),
    RelationshipCondition.TRUST_SID_FILTERING_UNKNOWN: (
        "Whether SID filtering applies across the trust is not known."
    ),
    RelationshipCondition.TRUST_SELECTIVE_AUTHENTICATION_UNKNOWN: (
        "Whether the trust uses selective authentication is not known."
    ),
    RelationshipCondition.DENY_MAY_APPLY: (
        "A Deny entry for another trustee comes before the grant; it removes the right from "
        "any holder whose token also carries that trustee, which was not evaluated."
    ),
    RelationshipCondition.ACL_ENTRIES_INCOMPLETE: (
        "The descriptor declared a different number of entries than ADG holds for it, so an "
        "entry ADG does not hold, or holds from an earlier reading, could change this."
    ),
    RelationshipCondition.ACL_ORDER_UNKNOWN: (
        "The collector did not report the order of the entries, so whether a Deny entry is "
        "evaluated before the grant is not known."
    ),
    RelationshipCondition.LOGON_RIGHT_UNEVALUATED: (
        "Holds only if the host's user-rights assignment grants the logon right and no "
        "deny-logon right removes it; user-rights assignments are not collected."
    ),
    RelationshipCondition.SERVICE_ACCESS_POLICY_UNEVALUATED: (
        "Holds only if the service's own access policy admits the group, as the Windows "
        "default does; that policy is not collected."
    ),
    RelationshipCondition.REMOTE_SERVICE_STATE_UNKNOWN: (
        "Holds only if the service is enabled and reachable on the host; its state is not "
        "collected."
    ),
}


class RelationshipConfidence(StrEnum):
    """How sure ADG is of a relationship, as an ordinal — never a number (ADR-0011).

    The first three values deliberately carry the risk engine's ``Confidence`` names and
    meanings, so a risk rule reading a relationship does not translate between two
    vocabularies that say the same thing. ``indeterminate`` is the one the risk engine does
    not need: a relationship whose endpoints the evidence itself could not resolve.
    """

    CONFIRMED = "confirmed"
    """Observed, or derived with no assumption, from evidence with no qualifier."""

    PROBABLE = "probable"
    """Conditionally derived: right unless one of its named conditions fails."""

    POSSIBLE = "possible"
    """A qualifier says the evidence is partial or bounded."""

    INDETERMINATE = "indeterminate"
    """The relationship exists and what it connects cannot be established."""


CONFIDENCE_ORDER: Final[dict[RelationshipConfidence, int]] = {
    RelationshipConfidence.INDETERMINATE: 0,
    RelationshipConfidence.POSSIBLE: 1,
    RelationshipConfidence.PROBABLE: 2,
    RelationshipConfidence.CONFIRMED: 3,
}
"""Rank, weakest first. :func:`confidence_of` takes the minimum."""


class EvidenceKind(StrEnum):
    """Which stored fact a piece of evidence is.

    The values are exactly the collector contract's ``ObservationKind``: evidence is always a
    row of a current-state table, keyed by that table's primary key — which is also
    ``object_versions.object_key`` and ``observations.subject_key``, so from one evidence
    reference the fact, its timeline and every observation of it are each one indexed read.
    Declared here rather than imported because the domain layer depends on nothing outside
    it; ``tests/domain/test_relationships.py`` pins the two enums equal.
    """

    PRINCIPAL = "principal"
    MEMBERSHIP_EDGE = "membership_edge"
    SERVER = "server"
    SMB_SHARE = "smb_share"
    SMB_ACE = "smb_ace"
    NTFS_RESOURCE = "ntfs_resource"
    NTFS_ACE = "ntfs_ace"


class EvidenceRole(StrEnum):
    """Whether the relationship depends on a piece of evidence."""

    PREMISE = "premise"
    """The support holds only while this fact does. A measured absence of any premise makes
    the support not current."""

    CONTEXT = "context"
    """Consulted by the derivation and recorded for the explanation, without the support
    depending on it — the principal row that labels a group, for instance."""


class EvidenceState(StrEnum):
    """What the store says about one premise now."""

    PRESENT = "present"
    """A current-state row exists and no open tombstone excludes it."""

    ABSENT = "absent"
    """An authoritative reconciliation recorded it gone (an open tombstone)."""

    MISSING = "missing"
    """No row exists for it at all. Nobody said it is gone; nothing says it is there."""


class RelationshipCurrency(StrEnum):
    """Whether a relationship holds in current state. Three values, never two."""

    CURRENT = "current"
    """At least one support has every premise present."""

    NOT_CURRENT = "not_current"
    """Every support has a premise an authoritative scan measured absent, or was retired: its
    facts, as now stored, no longer establish it."""

    UNKNOWN = "unknown"
    """No support is fully present, and at least one is not measured absent either."""


class RelationshipScopeKind(StrEnum):
    """The kind of boundary a relationship is valid inside."""

    HOST = "host"
    """One computer, by its case-folded host name: the scope of a local account database."""


class AdjacencyDirection(StrEnum):
    """Which end of a relationship a node is read from.

    Every relationship is directed (:attr:`RelationshipTypeDefinition.reads_as` says what
    the direction means). A fact that holds both ways, such as a two-way trust, is two
    relationships, so there is no undirected edge to special-case.
    """

    OUTBOUND = "outbound"
    """Relationships whose *source* is the node."""

    INBOUND = "inbound"
    """Relationships whose *target* is the node."""


class Materialization(StrEnum):
    """Whether a relationship type may be stored at all."""

    STORED = "stored"
    """One hop over a bounded set of facts; storable as a relationship row."""

    TRAVERSAL_ONLY = "traversal_only"
    """A path, not a hop. Computed by bounded traversal on request and never stored: a
    stored closure is the representation ADR-0002 rejected."""


class ScopeRule(StrEnum):
    """What a relationship type requires of :class:`RelationshipScope`."""

    HOST_REQUIRED = "host_required"
    """Meaningful only on one computer; every relationship of the type names its host."""

    FROM_ENDPOINTS = "from_endpoints"
    """Scoped exactly when an endpoint is host-local (a ``<host>|<SID>`` principal), and then
    to that endpoint's host; otherwise unscoped."""


class CollectionStatus(StrEnum):
    """Whether ADG collects the facts a relationship type would be derived from."""

    COLLECTED = "collected"
    """A shipped collector reports the premises."""

    PARTIALLY_COLLECTED = "partially_collected"
    """Collected for some target kinds and not others (:attr:`CollectionSupport.note`)."""

    CONTRACT_ONLY = "contract_only"
    """The collector contract accepts the premises, and no shipped collector emits them yet;
    only fixtures and the synthetic demo estate contain them."""

    NOT_COLLECTED = "not_collected"
    """No collector and no contract field carries the premises. A relationship of this type
    cannot exist in ADG yet; its absence from an answer means nothing."""


class DerivationStatus(StrEnum):
    """Whether ADG derives relationships of a type, separately from whether it collects."""

    NOT_IMPLEMENTED = "not_implemented"
    """No rule produces it yet."""

    QUERY_TIME = "query_time"
    """Computed on request by existing bounded traversal; never stored."""

    PROJECTOR = "projector"
    """Produced and stored by a projector rule."""


class ProjectionTrigger(StrEnum):
    """What started one projector pass (``graph_derivation_passes``)."""

    RUN_COMPLETION = "run_completion"
    """A scan run completed; the pass re-derived every fact that run touched."""

    REBUILD = "rebuild"
    """An operator recomputed relationships from every stored fact."""


class ProjectionStatus(StrEnum):
    """How one projector pass ended. A failed pass wrote no relationship."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


# --- Taxonomy -----------------------------------------------------------------------------


class RelationshipType(StrEnum):
    """The relationship taxonomy. Extensible: a new member needs a definition, nothing else.

    Every member here is *defined*. Whether ADG collects its premises and whether it derives
    it are separate statements (:attr:`RelationshipTypeDefinition.collection`,
    :attr:`RelationshipTypeDefinition.derivation`), because a type that is defined and not
    collected must never be read as a type that was looked for and not found.
    """

    MEMBER_OF = "member_of"
    NESTED_MEMBER_OF = "nested_member_of"
    PRIMARY_GROUP_OF = "primary_group_of"
    LOCAL_GROUP_MEMBER_OF = "local_group_member_of"
    LOCAL_ADMIN_OF = "local_admin_of"
    REMOTE_DESKTOP_USER_OF = "remote_desktop_user_of"
    REMOTE_MANAGEMENT_USER_OF = "remote_management_user_of"
    OWNS = "owns"
    GENERIC_ALL = "generic_all"
    GENERIC_WRITE = "generic_write"
    WRITE_DACL = "write_dacl"
    WRITE_OWNER = "write_owner"
    CAN_MODIFY_MEMBERSHIP = "can_modify_membership"
    CONTROLS_GPO = "controls_gpo"
    GPO_LINKED_TO = "gpo_linked_to"
    GPO_APPLIES_TO = "gpo_applies_to"
    TRUSTS = "trusts"
    FOREIGN_PRINCIPAL_OF = "foreign_principal_of"


@dataclass(frozen=True, slots=True)
class AttributeSpec:
    """One relationship-specific attribute a type allows on its supports."""

    value_type: type[str] | type[int] | type[bool]
    description: str
    required: bool = False


@dataclass(frozen=True, slots=True)
class CollectionSupport:
    status: CollectionStatus
    premises: frozenset[EvidenceKind]
    """The stored fact kinds a support of this type rests on. Empty when nothing ADG stores
    can be a premise yet."""
    note: str


@dataclass(frozen=True, slots=True)
class DerivationSupport:
    status: DerivationStatus
    note: str


@dataclass(frozen=True, slots=True)
class RelationshipTypeDefinition:
    """Everything ADG states about one relationship type."""

    relationship_type: RelationshipType
    reads_as: str
    """The meaning of the direction, as a sentence with ``{source}`` and ``{target}``."""
    source_kinds: frozenset[NodeKind]
    target_kinds: frozenset[NodeKind]
    allowed_classes: frozenset[DerivationClass]
    collection: CollectionSupport
    derivation: DerivationSupport
    scope_rule: ScopeRule = ScopeRule.FROM_ENDPOINTS
    materialization: Materialization = Materialization.STORED
    allows_self: bool = False
    """Whether source and target may be the same node. False for every membership and trust
    type, which Windows never creates as a self-edge; true where an object can hold a right
    on itself."""
    target_builtin_rid: int | None = None
    """For a specialization of local-group membership: the BUILTIN RID the target must be."""
    attributes: Mapping[str, AttributeSpec] = field(default_factory=lambda: MappingProxyType({}))

    def describe(self, source: str, target: str) -> str:
        return self.reads_as.format(source=source, target=target)


_PRINCIPAL: Final = frozenset({NodeKind.PRINCIPAL})
_ACL_TARGETS: Final = frozenset(
    {
        NodeKind.PRINCIPAL,
        NodeKind.NTFS_RESOURCE,
        NodeKind.SMB_SHARE,
        NodeKind.DOMAIN,
        NodeKind.GPO,
        NodeKind.DIRECTORY_CONTAINER,
    }
)
_MEMBERSHIP_CLASSES: Final = frozenset({DerivationClass.OBSERVED, DerivationClass.UNRESOLVED})
_SPECIALIZED_CLASSES: Final = frozenset(
    {
        DerivationClass.DETERMINISTICALLY_DERIVED,
        DerivationClass.CONDITIONALLY_DERIVED,
        DerivationClass.UNRESOLVED,
    }
)
_ACL_CLASSES: Final = _SPECIALIZED_CLASSES
_NOT_YET: Final = DerivationSupport(
    DerivationStatus.NOT_IMPLEMENTED,
    "No rule derives it: ADG collects no fact it could rest on (ADG-GRAPH-01B).",
)


def _projected(rules: str) -> DerivationSupport:
    return DerivationSupport(
        DerivationStatus.PROJECTOR, f"app.relationships.rules: {rules} (ADG-GRAPH-01B)."
    )


_MEMBERSHIP_ATTRIBUTES: Final = MappingProxyType(
    {
        "edge_kind": AttributeSpec(str, "The membership edge kind the premise records."),
    }
)
_ACE_ATTRIBUTES: Final = MappingProxyType(
    {
        "access_mask": AttributeSpec(int, "The mask of the premise entry, as read."),
        "ace_type": AttributeSpec(str, "allow, or the object-specific allow variant."),
        "inherited": AttributeSpec(bool, "Whether the premise entry was inherited."),
    }
)


def _acl_definition(
    relationship_type: RelationshipType,
    reads_as: str,
    *,
    extra_rules: str = "",
    extra_note: str = "",
) -> RelationshipTypeDefinition:
    """A right granted by an NTFS security descriptor.

    Never :attr:`DerivationClass.OBSERVED`: an entry is observed, and reading a right out of
    its mask is already an interpretation — generic bits map differently per object type, and
    whether the entry is *effective* depends on Deny entries and their order, which the access
    engine's walk decides (ADR-0010). A support reports what the descriptor grants every
    holder of the trustee, conditionally when anything it did not evaluate could remove it.

    The descriptor row is a premise kind as well as its entries: a descriptor with no DACL
    grants everyone everything, and ``OWNER RIGHTS`` grants whoever the descriptor names as
    owner. Share entries are **not** a premise kind (ADG-GRAPH-01B): a share permission limits
    what a session reaching the files through the share may do, it is not control of the share
    object, and an entry granting ``WRITE_DAC`` there does not let its holder change the share's
    permissions.
    """
    return RelationshipTypeDefinition(
        relationship_type=relationship_type,
        reads_as=reads_as,
        source_kinds=_PRINCIPAL,
        target_kinds=_ACL_TARGETS,
        allowed_classes=_ACL_CLASSES,
        allows_self=True,
        attributes=_ACE_ATTRIBUTES,
        collection=CollectionSupport(
            CollectionStatus.PARTIALLY_COLLECTED,
            frozenset({EvidenceKind.NTFS_ACE, EvidenceKind.NTFS_RESOURCE}),
            "Collected for NTFS resources (their entries and descriptor). Not collected for "
            "directory objects (users, groups, computers), GPOs or domains: their DACLs are not "
            "read. Share entries are read but are an access limit on the share path, not "
            "control of the share, so they are not a premise." + extra_note,
        ),
        derivation=_projected(
            "ntfs.allow_entry, ntfs.allow_entries_combined, ntfs.owner_rights_entry, "
            "ntfs.null_dacl" + extra_rules
        ),
    )


def _local_member_definition(
    relationship_type: RelationshipType, reads_as: str, rid: int, rule: str
) -> RelationshipTypeDefinition:
    """A specialization of local-group membership into one BUILTIN group.

    Deterministic, never observed: the premise is an ordinary ``local_group_member`` edge, and
    "this is the Administrators group" is the rule's reading of its well-known RID.
    """
    return RelationshipTypeDefinition(
        relationship_type=relationship_type,
        reads_as=reads_as,
        source_kinds=_PRINCIPAL,
        target_kinds=_PRINCIPAL,
        allowed_classes=_SPECIALIZED_CLASSES,
        scope_rule=ScopeRule.HOST_REQUIRED,
        target_builtin_rid=rid,
        attributes=_MEMBERSHIP_ATTRIBUTES,
        collection=CollectionSupport(
            CollectionStatus.COLLECTED,
            frozenset({EvidenceKind.MEMBERSHIP_EDGE}),
            "A specialization of local_group_member_of, so collected exactly when that is: by "
            "the local-groups collector (ADG-GRAPH-01B).",
        ),
        derivation=_projected(rule),
    )


RELATIONSHIP_TAXONOMY: Final[Mapping[RelationshipType, RelationshipTypeDefinition]] = (
    MappingProxyType(
        {
            RelationshipType.MEMBER_OF: RelationshipTypeDefinition(
                relationship_type=RelationshipType.MEMBER_OF,
                reads_as="{source} is a direct member of {target}",
                source_kinds=_PRINCIPAL,
                target_kinds=_PRINCIPAL,
                allowed_classes=_MEMBERSHIP_CLASSES,
                attributes=_MEMBERSHIP_ATTRIBUTES,
                collection=CollectionSupport(
                    CollectionStatus.COLLECTED,
                    frozenset({EvidenceKind.MEMBERSHIP_EDGE}),
                    "The AD collector reports directory_group_member edges; a source-stated "
                    "well_known_implicit edge is a direct membership too.",
                ),
                derivation=_projected("membership.directory_edge"),
            ),
            RelationshipType.NESTED_MEMBER_OF: RelationshipTypeDefinition(
                relationship_type=RelationshipType.NESTED_MEMBER_OF,
                reads_as="{source} is a member of {target} through nested groups",
                source_kinds=_PRINCIPAL,
                target_kinds=_PRINCIPAL,
                allowed_classes=frozenset({DerivationClass.DETERMINISTICALLY_DERIVED}),
                materialization=Materialization.TRAVERSAL_ONLY,
                collection=CollectionSupport(
                    CollectionStatus.COLLECTED,
                    frozenset({EvidenceKind.MEMBERSHIP_EDGE}),
                    "A path of collected direct edges.",
                ),
                derivation=DerivationSupport(
                    DerivationStatus.QUERY_TIME,
                    "app.domain.graph.expand and find_paths: bounded and cycle-safe, and never "
                    "stored (ADR-0002, ADR-0006).",
                ),
            ),
            RelationshipType.PRIMARY_GROUP_OF: RelationshipTypeDefinition(
                relationship_type=RelationshipType.PRIMARY_GROUP_OF,
                reads_as="{target} is the primary group of {source}",
                source_kinds=_PRINCIPAL,
                target_kinds=_PRINCIPAL,
                allowed_classes=_MEMBERSHIP_CLASSES,
                attributes=_MEMBERSHIP_ATTRIBUTES,
                collection=CollectionSupport(
                    CollectionStatus.COLLECTED,
                    frozenset({EvidenceKind.MEMBERSHIP_EDGE}),
                    "The AD collector reports primary_group edges from primaryGroupID.",
                ),
                derivation=_projected("membership.primary_group_edge"),
            ),
            RelationshipType.LOCAL_GROUP_MEMBER_OF: RelationshipTypeDefinition(
                relationship_type=RelationshipType.LOCAL_GROUP_MEMBER_OF,
                reads_as="{source} is a member of the local group {target}",
                source_kinds=_PRINCIPAL,
                target_kinds=_PRINCIPAL,
                allowed_classes=_MEMBERSHIP_CLASSES,
                scope_rule=ScopeRule.HOST_REQUIRED,
                attributes=_MEMBERSHIP_ATTRIBUTES,
                collection=CollectionSupport(
                    CollectionStatus.COLLECTED,
                    frozenset({EvidenceKind.MEMBERSHIP_EDGE}),
                    "The local-groups collector (collector/powershell/localgroups, "
                    "ADG-GRAPH-01B) reports host-scoped local_group_member edges. Its Windows "
                    "binding is validated on a standalone CI runner; a remote domain-joined "
                    "server is not yet validated.",
                ),
                derivation=_projected("membership.local_group_edge"),
            ),
            RelationshipType.LOCAL_ADMIN_OF: _local_member_definition(
                RelationshipType.LOCAL_ADMIN_OF,
                "{source} is a local administrator through {target}",
                544,
                "local_group.builtin_administrators",
            ),
            RelationshipType.REMOTE_DESKTOP_USER_OF: _local_member_definition(
                RelationshipType.REMOTE_DESKTOP_USER_OF,
                "{source} may sign in over Remote Desktop through {target}",
                555,
                "local_group.builtin_remote_desktop_users",
            ),
            RelationshipType.REMOTE_MANAGEMENT_USER_OF: _local_member_definition(
                RelationshipType.REMOTE_MANAGEMENT_USER_OF,
                "{source} may use remote management (WinRM) through {target}",
                580,
                "local_group.builtin_remote_management_users",
            ),
            RelationshipType.OWNS: RelationshipTypeDefinition(
                relationship_type=RelationshipType.OWNS,
                reads_as="{source} owns {target}",
                source_kinds=_PRINCIPAL,
                target_kinds=_ACL_TARGETS,
                allowed_classes=frozenset({DerivationClass.OBSERVED, DerivationClass.UNRESOLVED}),
                allows_self=True,
                collection=CollectionSupport(
                    CollectionStatus.PARTIALLY_COLLECTED,
                    frozenset({EvidenceKind.NTFS_RESOURCE}),
                    "Collected for NTFS resources (the descriptor's owner SID). Not collected "
                    "for directory objects, GPOs, domains or shares.",
                ),
                derivation=_projected("ntfs.owner"),
            ),
            RelationshipType.GENERIC_ALL: _acl_definition(
                RelationshipType.GENERIC_ALL, "{source} has full control of {target}"
            ),
            RelationshipType.GENERIC_WRITE: _acl_definition(
                RelationshipType.GENERIC_WRITE, "{source} has generic write on {target}"
            ),
            RelationshipType.WRITE_DACL: _acl_definition(
                RelationshipType.WRITE_DACL,
                "{source} can change the permissions of {target}",
                # An object's owner is implicitly granted READ_CONTROL and WRITE_DAC whatever
                # the DACL says, unless an OWNER RIGHTS entry replaces it -- so the descriptor's
                # owner is a premise of its own rule as well.
                extra_rules=", ntfs.owner_implicit_rights",
                extra_note=(
                    " The owner of an NTFS resource is implicitly granted it unless an OWNER "
                    "RIGHTS entry says otherwise, so the resource's owner is a premise as well."
                ),
            ),
            RelationshipType.WRITE_OWNER: _acl_definition(
                RelationshipType.WRITE_OWNER, "{source} can take ownership of {target}"
            ),
            RelationshipType.CAN_MODIFY_MEMBERSHIP: RelationshipTypeDefinition(
                relationship_type=RelationshipType.CAN_MODIFY_MEMBERSHIP,
                reads_as="{source} can change the membership of {target}",
                source_kinds=_PRINCIPAL,
                target_kinds=_PRINCIPAL,
                allowed_classes=_ACL_CLASSES,
                allows_self=True,
                collection=CollectionSupport(
                    CollectionStatus.NOT_COLLECTED,
                    frozenset(),
                    "Needs a group object's DACL (write to member, self-membership) or a host's "
                    "local administrators; directory-object ACLs are not collected.",
                ),
                derivation=_NOT_YET,
            ),
            RelationshipType.CONTROLS_GPO: RelationshipTypeDefinition(
                relationship_type=RelationshipType.CONTROLS_GPO,
                reads_as="{source} can edit the group policy object {target}",
                source_kinds=_PRINCIPAL,
                target_kinds=frozenset({NodeKind.GPO}),
                allowed_classes=_ACL_CLASSES,
                collection=CollectionSupport(
                    CollectionStatus.NOT_COLLECTED,
                    frozenset(),
                    "Needs the GPO container's DACL and its SYSVOL folder; neither is "
                    "collected as a GPO.",
                ),
                derivation=_NOT_YET,
            ),
            RelationshipType.GPO_LINKED_TO: RelationshipTypeDefinition(
                relationship_type=RelationshipType.GPO_LINKED_TO,
                reads_as="{source} is linked to {target}",
                source_kinds=frozenset({NodeKind.GPO}),
                target_kinds=frozenset({NodeKind.DOMAIN, NodeKind.DIRECTORY_CONTAINER}),
                allowed_classes=frozenset({DerivationClass.OBSERVED, DerivationClass.UNRESOLVED}),
                attributes=MappingProxyType(
                    {
                        "enforced": AttributeSpec(bool, "The link is enforced."),
                        "link_enabled": AttributeSpec(bool, "The link is enabled."),
                    }
                ),
                collection=CollectionSupport(
                    CollectionStatus.NOT_COLLECTED,
                    frozenset(),
                    "Needs gPLink; no collector reads it.",
                ),
                derivation=_NOT_YET,
            ),
            RelationshipType.GPO_APPLIES_TO: RelationshipTypeDefinition(
                relationship_type=RelationshipType.GPO_APPLIES_TO,
                reads_as="{source} applies to {target}",
                source_kinds=frozenset({NodeKind.GPO}),
                target_kinds=_PRINCIPAL,
                allowed_classes=frozenset(
                    {DerivationClass.CONDITIONALLY_DERIVED, DerivationClass.UNRESOLVED}
                ),
                collection=CollectionSupport(
                    CollectionStatus.NOT_COLLECTED,
                    frozenset(),
                    "Needs links, container placement, security filtering and WMI filters; "
                    "none is collected. Always conditional once it is.",
                ),
                derivation=_NOT_YET,
            ),
            RelationshipType.TRUSTS: RelationshipTypeDefinition(
                relationship_type=RelationshipType.TRUSTS,
                reads_as="{source} trusts {target}",
                source_kinds=frozenset({NodeKind.DOMAIN}),
                target_kinds=frozenset({NodeKind.DOMAIN}),
                allowed_classes=frozenset(
                    {
                        DerivationClass.OBSERVED,
                        DerivationClass.CONDITIONALLY_DERIVED,
                        DerivationClass.UNRESOLVED,
                    }
                ),
                attributes=MappingProxyType(
                    {
                        "trust_type": AttributeSpec(str, "forest, external, parent_child, ..."),
                        "transitive": AttributeSpec(bool, "The trust is transitive."),
                    }
                ),
                collection=CollectionSupport(
                    CollectionStatus.NOT_COLLECTED,
                    frozenset(),
                    "No collector reads trustedDomain objects. A two-way trust is two "
                    "relationships.",
                ),
                derivation=_NOT_YET,
            ),
            RelationshipType.FOREIGN_PRINCIPAL_OF: RelationshipTypeDefinition(
                relationship_type=RelationshipType.FOREIGN_PRINCIPAL_OF,
                reads_as="{source} is a foreign principal issued by {target}",
                source_kinds=_PRINCIPAL,
                target_kinds=frozenset({NodeKind.DOMAIN}),
                allowed_classes=frozenset(
                    {DerivationClass.DETERMINISTICALLY_DERIVED, DerivationClass.UNRESOLVED}
                ),
                collection=CollectionSupport(
                    CollectionStatus.COLLECTED,
                    frozenset({EvidenceKind.MEMBERSHIP_EDGE, EvidenceKind.PRINCIPAL}),
                    "Foreign members arrive as membership edges flagged "
                    "is_foreign_security_principal and as principals of kind "
                    "foreign_security_principal; the issuing domain is the SID's own prefix.",
                ),
                derivation=_projected(
                    "foreign_principal.membership_edge, foreign_principal.principal_object"
                ),
            ),
        }
    )
)
"""Every relationship type and what ADG states about it. One entry per member, enforced."""


def definition_of(relationship_type: RelationshipType) -> RelationshipTypeDefinition:
    return RELATIONSHIP_TAXONOMY[relationship_type]


def describe_condition(condition: RelationshipCondition) -> str:
    """The operator-facing sentence for ``condition``. ``KeyError`` if it has none."""
    return CONDITION_DESCRIPTIONS[condition]


def describe_qualifier(qualifier: RelationshipQualifier) -> str:
    """The operator-facing sentence for ``qualifier``. ``KeyError`` if it has none."""
    return QUALIFIER_DESCRIPTIONS[qualifier]


# --- Nodes and scope ----------------------------------------------------------------------


def _utc(value: dt.datetime, field_name: str) -> dt.datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise DomainValidationError(
            f"{field_name} must be timezone-aware: a naive timestamp cannot be ordered against "
            "observations from a host in another time zone.",
            field=field_name,
        )
    return value.astimezone(dt.UTC)


def _is_host_name(value: str) -> bool:
    return (
        bool(value)
        and value == value.casefold()
        and value == value.strip()
        and not any(char in value for char in "\\/|")
    )


def _canonical_sid(value: str) -> Sid | None:
    sid = Sid.try_parse(value)
    return sid if sid is not None and sid.value == value else None


def _is_lower_guid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _key_problem(kind: NodeKind, key: str) -> str | None:
    """Why ``key`` is not a canonical key of ``kind``, or ``None`` when it is.

    Canonical, not merely parseable: a second spelling of one object's key would make two
    nodes of one object, which is exactly the merge-or-split error the identity rules
    exist to prevent (``referenced_principal_key``).
    """
    if kind is NodeKind.PRINCIPAL:
        host, _, sid_text = key.rpartition("|")
        if host and not _is_host_name(host):
            return "a host-local principal key is '<case-folded host>|<SID>'"
        if _canonical_sid(sid_text) is None:
            return "a principal key is a canonical SID, optionally prefixed by '<host>|'"
        return None
    if kind is NodeKind.SERVER:
        return None if _is_host_name(key) else "a server key is a case-folded host name"
    if kind is NodeKind.SMB_SHARE:
        server, separator, share = key.partition("|")
        if (
            separator
            and _is_host_name(server)
            and share
            and share == share.casefold()
            and not any(char in share for char in "\\/|")
        ):
            return None
        return "a share key is '<case-folded server>|<case-folded share>'"
    if kind is NodeKind.NTFS_RESOURCE:
        if key.startswith("\\\\") and key == key.casefold():
            return None
        return "a resource key is a case-folded UNC path"
    if kind is NodeKind.DOMAIN:
        sid = _canonical_sid(key)
        return None if sid is not None and sid.is_domain_sid else "a domain key is a domain SID"
    if kind is NodeKind.GPO:
        domain, separator, guid = key.partition("|")
        sid = _canonical_sid(domain)
        if separator and sid is not None and sid.is_domain_sid and _is_lower_guid(guid):
            return None
        return "a GPO key is '<domain SID>|<lower-case GUID>'"
    return None if _is_lower_guid(key) else "a container key is a lower-case objectGUID"


@dataclass(frozen=True, slots=True, order=True)
class NodeRef:
    """A reference to one canonical ADG object: its kind and its existing primary key."""

    kind: NodeKind
    key: str

    def __post_init__(self) -> None:
        if not self.key or len(self.key) > KEY_MAX_LENGTH or IDENTITY_SEPARATOR in self.key:
            raise DomainValidationError(
                f"A {self.kind.value} key must be 1 to {KEY_MAX_LENGTH} characters with no "
                "control separator.",
                value=self.key,
                field="key",
            )
        problem = _key_problem(self.kind, self.key)
        if problem is not None:
            raise DomainValidationError(
                f"{self.key!r} is not a canonical {self.kind.value} key: {problem}.",
                value=self.key,
                field="key",
            )

    @classmethod
    def principal(cls, key: str) -> NodeRef:
        return cls(NodeKind.PRINCIPAL, key)

    @property
    def host(self) -> str | None:
        """The host a host-local principal belongs to; ``None`` for every other node."""
        if self.kind is not NodeKind.PRINCIPAL or "|" not in self.key:
            return None
        return self.key.rpartition("|")[0]

    @property
    def sid(self) -> Sid | None:
        """The SID a principal or domain node names."""
        if self.kind is NodeKind.PRINCIPAL:
            return Sid(self.key.rpartition("|")[2])
        if self.kind is NodeKind.DOMAIN:
            return Sid(self.key)
        return None

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.key}"


@dataclass(frozen=True, slots=True, order=True)
class RelationshipScope:
    """The boundary a relationship is valid inside. Part of its identity."""

    kind: RelationshipScopeKind
    key: str

    def __post_init__(self) -> None:
        if self.kind is RelationshipScopeKind.HOST and not _is_host_name(self.key):
            raise DomainValidationError(
                f"A host scope is a case-folded host name; received {self.key!r}.",
                value=self.key,
                field="scope",
            )

    @classmethod
    def host(cls, host: str) -> RelationshipScope:
        return cls(RelationshipScopeKind.HOST, host)


# --- Identity -----------------------------------------------------------------------------


def _digest(material: str) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def relationship_id(
    relationship_type: RelationshipType,
    source: NodeRef,
    target: NodeRef,
    scope: RelationshipScope | None,
) -> str:
    """The stable identity of a relationship: a digest of exactly what makes it *this* one.

    Type, both endpoints and the scope — and nothing else. Not the derivation class, so a
    relationship that becomes resolvable keeps its identity; not the rule, so a rule's new
    version re-derives the same relationship rather than a second one; not a timestamp, so a
    re-observation is the same relationship seen again. The database recomputes this digest
    in a check constraint, so a row whose id disagrees with its components cannot exist.
    """
    return _digest(
        IDENTITY_SEPARATOR.join(
            (
                relationship_type.value,
                source.kind.value,
                source.key,
                target.kind.value,
                target.key,
                scope.kind.value if scope is not None else "",
                scope.key if scope is not None else "",
            )
        )
    )


def support_key(premises: Iterable[tuple[EvidenceKind, str]]) -> str:
    """The identity of one way a relationship is supported: the set of facts it rests on.

    Premises only. Context evidence can change without the support changing; a different set
    of premises is, by definition, a different support. Order-independent.
    """
    entries = sorted({f"{kind.value}{IDENTITY_SEPARATOR}{key}" for kind, key in premises})
    if not entries:
        raise DomainValidationError("A support rests on at least one premise.", field="evidence")
    return _digest("\x1e".join(entries))


# --- Assertions: what a derivation produces -----------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class DerivationRule:
    """Which rule, at which version, produced a support (ADR-0003 attribution)."""

    rule_id: str
    version: int

    def __post_init__(self) -> None:
        if not RULE_ID_PATTERN.match(self.rule_id):
            raise DomainValidationError(
                f"{self.rule_id!r} is not a rule id: expected a dotted lower-case name.",
                value=self.rule_id,
                field="rule_id",
            )
        if self.version < 1:
            raise DomainValidationError("A rule version starts at 1.", field="version")

    def __str__(self) -> str:
        return f"{self.rule_id}@{self.version}"


@dataclass(frozen=True, slots=True)
class RelationshipEvidence:
    """One stored fact a derivation read, and the observation of it the derivation used.

    ``run_id`` and ``observed_at`` name one observation of the fact: the ``observations`` row
    ``(run_id, subject_key = key)`` and the ``object_versions`` timeline of ``(kind, key)``
    are both reachable from them, which is what keeps a relationship traceable to the
    observations it came from rather than to a copy of their content.
    """

    kind: EvidenceKind
    key: str
    run_id: UUID
    observed_at: dt.datetime
    role: EvidenceRole = EvidenceRole.PREMISE

    def __post_init__(self) -> None:
        if not self.key or len(self.key) > KEY_MAX_LENGTH:
            raise DomainValidationError(
                f"Evidence names a stored fact by its 1 to {KEY_MAX_LENGTH} character key.",
                value=self.key,
                field="key",
            )
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))

    @property
    def identity(self) -> tuple[str, str]:
        return (self.kind.value, self.key)


AttributeValue = str | int | bool


def _validate_attributes(
    definition: RelationshipTypeDefinition, attributes: Mapping[str, AttributeValue]
) -> Mapping[str, AttributeValue]:
    spec = definition.attributes
    unknown = sorted(set(attributes) - set(spec))
    if unknown:
        raise DomainValidationError(
            f"{definition.relationship_type.value} does not define the attribute(s) "
            f"{', '.join(unknown)}; allowed: {', '.join(sorted(spec)) or 'none'}.",
            field="attributes",
        )
    for name, attribute in spec.items():
        if name not in attributes:
            if attribute.required:
                raise DomainValidationError(
                    f"{definition.relationship_type.value} requires the attribute {name}.",
                    field="attributes",
                )
            continue
        value = attributes[name]
        # Exact type: bool is an int subclass, and a mask of True is not a mask.
        if type(value) is not attribute.value_type:
            raise DomainValidationError(
                f"Attribute {name} is a {attribute.value_type.__name__}; received "
                f"{type(value).__name__}.",
                value=value,
                field="attributes",
            )
        if isinstance(value, str) and len(value) > ATTRIBUTE_VALUE_MAX_LENGTH:
            raise DomainValidationError(f"Attribute {name} is too long.", field="attributes")
    return MappingProxyType(dict(sorted(attributes.items())))


def _expected_scope(
    definition: RelationshipTypeDefinition,
    source: NodeRef,
    target: NodeRef,
    scope: RelationshipScope | None,
) -> None:
    """Refuse a scope that disagrees with the endpoints or with the type.

    The scope is stored because "everything on FS01" has to be one indexed read, but it may
    never say something the endpoints contradict: a host-local endpoint *is* a statement of
    which machine the relationship lives on, and two statements of it must agree.
    """
    hosts = {host for host in (source.host, target.host) if host is not None}
    if len(hosts) > 1:
        raise DomainValidationError(
            f"A relationship cannot join principals local to two hosts ({', '.join(sorted(hosts))}"
            "): a local account database is one machine's.",
            field="scope",
        )
    endpoint_host = next(iter(hosts), None)
    name = definition.relationship_type.value
    if definition.scope_rule is ScopeRule.HOST_REQUIRED:
        if scope is None or scope.kind is not RelationshipScopeKind.HOST:
            raise DomainValidationError(
                f"{name} is meaningful only on one computer and must be host-scoped: "
                "BUILTIN group SIDs are identical on every computer.",
                field="scope",
            )
        if target.host != scope.key:
            raise DomainValidationError(
                f"{name} targets a group local to {scope.key}; {target.key!r} is not.",
                value=target.key,
                field="target",
            )
        if endpoint_host is not None and endpoint_host != scope.key:
            raise DomainValidationError(
                f"The scope says {scope.key}; an endpoint is local to {endpoint_host}.",
                field="scope",
            )
        if definition.target_builtin_rid is not None:
            expected = f"S-1-5-32-{definition.target_builtin_rid}"
            if target.sid is None or target.sid.value != expected:
                raise DomainValidationError(
                    f"{name} is membership in {expected} only; the target names {target.sid}.",
                    value=target.key,
                    field="target",
                )
        return
    expected_scope = RelationshipScope.host(endpoint_host) if endpoint_host else None
    if scope != expected_scope:
        raise DomainValidationError(
            f"{name} is scoped by its endpoints: expected "
            f"{'host ' + endpoint_host if endpoint_host else 'no scope'}, received "
            f"{'host ' + scope.key if scope else 'no scope'}.",
            field="scope",
        )


@dataclass(frozen=True, slots=True)
class RelationshipAssertion:
    """One derivation of one relationship: what a projector hands the repository to store.

    Every invariant a stored support must satisfy is checked here, on construction, so an
    invalid relationship cannot reach the database and the database's own constraints are a
    second guard rather than the first.
    """

    relationship_type: RelationshipType
    source: NodeRef
    target: NodeRef
    derivation_class: DerivationClass
    rule: DerivationRule
    evidence: tuple[RelationshipEvidence, ...]
    basis_token: str
    derived_at: dt.datetime
    scope: RelationshipScope | None = None
    qualifiers: frozenset[RelationshipQualifier] = frozenset()
    conditions: frozenset[RelationshipCondition] = frozenset()
    attributes: Mapping[str, AttributeValue] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        definition = definition_of(self.relationship_type)
        name = self.relationship_type.value
        object.__setattr__(self, "derived_at", _utc(self.derived_at, "derived_at"))
        object.__setattr__(self, "qualifiers", frozenset(self.qualifiers))
        object.__setattr__(self, "conditions", frozenset(self.conditions))

        if definition.materialization is Materialization.TRAVERSAL_ONLY:
            raise DomainValidationError(
                f"{name} is a path computed by bounded traversal and is never stored: a stored "
                "closure is the representation ADR-0002 rejected.",
                field="relationship_type",
            )
        if not definition.collection.premises:
            raise DomainValidationError(
                f"{name} cannot be asserted: ADG stores no fact it could rest on "
                f"({definition.collection.status.value}). {definition.collection.note}",
                field="relationship_type",
            )
        if self.source.kind not in definition.source_kinds:
            raise DomainValidationError(
                f"{name} starts at a {_kinds(definition.source_kinds)}; received "
                f"{self.source.kind.value}.",
                field="source",
            )
        if self.target.kind not in definition.target_kinds:
            raise DomainValidationError(
                f"{name} ends at a {_kinds(definition.target_kinds)}; received "
                f"{self.target.kind.value}.",
                field="target",
            )
        if self.source == self.target and not definition.allows_self:
            raise DomainValidationError(
                f"{name} cannot relate {self.source} to itself; observing one indicates a "
                "collector or derivation defect.",
                field="target",
            )
        _expected_scope(definition, self.source, self.target, self.scope)
        if (
            self.relationship_type is RelationshipType.FOREIGN_PRINCIPAL_OF
            and self.source.sid is not None
            and self.source.sid.domain_sid != self.target.sid
        ):
            raise DomainValidationError(
                f"{self.source.key} was not issued by {self.target.key}: the issuing domain is "
                "the SID's own prefix.",
                field="target",
            )

        if self.derivation_class not in definition.allowed_classes:
            raise DomainValidationError(
                f"{name} cannot be {self.derivation_class.value}; allowed: "
                f"{', '.join(sorted(c.value for c in definition.allowed_classes))}.",
                field="derivation_class",
            )
        conditional = self.derivation_class is DerivationClass.CONDITIONALLY_DERIVED
        if conditional != bool(self.conditions):
            raise DomainValidationError(
                "A conditionally derived support names the conditions it depends on, and no "
                "other support has any.",
                field="conditions",
            )
        if self.derivation_class is DerivationClass.UNRESOLVED and not (
            self.qualifiers & UNRESOLVED_QUALIFIERS
        ):
            raise DomainValidationError(
                "An unresolved support must say what is unresolved: "
                f"{', '.join(sorted(q.value for q in UNRESOLVED_QUALIFIERS))}.",
                field="qualifiers",
            )

        evidence = _normalize_evidence(self.evidence)
        object.__setattr__(self, "evidence", evidence)
        premises = [item for item in evidence if item.role is EvidenceRole.PREMISE]
        if not premises:
            raise DomainValidationError(
                "A relationship must rest on at least one premise: one that rests on nothing "
                "cannot be traced to an observation.",
                field="evidence",
            )
        accepted = definition.collection.premises
        if not any(item.kind in accepted for item in premises):
            raise DomainValidationError(
                f"{name} rests on {_kinds(accepted)} evidence; none of the premises is one.",
                field="evidence",
            )
        if self.derivation_class is DerivationClass.OBSERVED and len(premises) != 1:
            raise DomainValidationError(
                "An observed relationship restates exactly one collected fact; "
                f"{len(premises)} premises make it a derivation.",
                field="evidence",
            )

        if len(self.basis_token) != BASIS_TOKEN_LENGTH or not _is_hex(self.basis_token):
            raise DomainValidationError(
                f"basis_token is the {BASIS_TOKEN_LENGTH}-character collection basis token.",
                value=self.basis_token,
                field="basis_token",
            )
        object.__setattr__(self, "attributes", _validate_attributes(definition, self.attributes))

    @property
    def definition(self) -> RelationshipTypeDefinition:
        return definition_of(self.relationship_type)

    @property
    def relationship_id(self) -> str:
        return relationship_id(self.relationship_type, self.source, self.target, self.scope)

    @property
    def support_key(self) -> str:
        return support_key(
            (item.kind, item.key) for item in self.evidence if item.role is EvidenceRole.PREMISE
        )

    @property
    def supported_at(self) -> tuple[dt.datetime, UUID]:
        """The observation that completed this support: its newest premise.

        A support holds only once every premise has been seen, so the instant it is
        *established* is the latest of them. Ties go to the larger run id, so the choice is
        deterministic whatever order the evidence arrived in.
        """
        newest = max(
            (item for item in self.evidence if item.role is EvidenceRole.PREMISE),
            key=lambda item: (item.observed_at, str(item.run_id)),
        )
        return newest.observed_at, newest.run_id


def _kinds(kinds: Iterable[StrEnum]) -> str:
    return " or ".join(sorted(kind.value for kind in kinds))


def _is_hex(value: str) -> bool:
    return all(char in "0123456789abcdef" for char in value)


def _normalize_evidence(
    evidence: Iterable[RelationshipEvidence],
) -> tuple[RelationshipEvidence, ...]:
    """Sort, collapse exact repeats, and refuse contradictions.

    A repeated identical reference is a replay and collapses. The same fact named twice with
    a different role, run or instant is two statements about one fact that disagree, and the
    derivation that produced them is wrong, so it is refused rather than reconciled here.
    """
    by_identity: dict[tuple[str, str], RelationshipEvidence] = {}
    for item in evidence:
        existing = by_identity.get(item.identity)
        if existing is None:
            by_identity[item.identity] = item
        elif existing != item:
            raise DomainValidationError(
                f"Evidence {item.kind.value} {item.key!r} is named twice with different "
                "roles or observations.",
                value=item.key,
                field="evidence",
            )
    return tuple(by_identity[identity] for identity in sorted(by_identity))


# --- Stored records: what the repository reads back ---------------------------------------


@dataclass(frozen=True, slots=True)
class StoredEvidence:
    """One evidence link as stored, with what the store says about the fact *now*."""

    kind: EvidenceKind
    key: str
    role: EvidenceRole
    first_observed_at: dt.datetime
    first_observed_run_id: UUID
    last_observed_at: dt.datetime
    last_observed_run_id: UUID
    state: EvidenceState

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "key": self.key,
            "role": self.role.value,
            "first_observed_at": _iso(self.first_observed_at),
            "first_observed_run_id": str(self.first_observed_run_id),
            "last_observed_at": _iso(self.last_observed_at),
            "last_observed_run_id": str(self.last_observed_run_id),
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True)
class RelationshipSupport:
    """One stored way a relationship is supported."""

    support_key: str
    derivation_class: DerivationClass
    rule: DerivationRule
    qualifiers: frozenset[RelationshipQualifier]
    conditions: frozenset[RelationshipCondition]
    attributes: Mapping[str, AttributeValue]
    basis_token: str
    derived_at: dt.datetime
    first_observed_at: dt.datetime
    first_observed_run_id: UUID
    last_observed_at: dt.datetime
    last_observed_run_id: UUID
    evidence: tuple[StoredEvidence, ...]
    retired_at: dt.datetime | None = None
    """When a derivation over this support's facts, all still stored, stopped producing it."""
    retired_basis_token: str | None = None
    """The collection basis that derivation read, so the retirement is attributable."""

    def __post_init__(self) -> None:
        if (self.retired_at is None) != (self.retired_basis_token is None):
            raise DomainValidationError(
                "A retirement names both when it happened and the basis it read.",
                field="retired_at",
            )

    @property
    def is_retired(self) -> bool:
        return self.retired_at is not None

    @property
    def confidence(self) -> RelationshipConfidence:
        return confidence_of(self.derivation_class, self.qualifiers)

    @property
    def currency(self) -> RelationshipCurrency:
        """Current while every premise is present; not current once any is measured absent.

        A premise with no stored row at all is neither — nobody recorded it gone — and makes
        the support's currency unknown rather than false. A **retired** support is not
        current whatever its premises say: the projector read those facts, as stored, and
        they no longer establish it.
        """
        if self.is_retired:
            return RelationshipCurrency.NOT_CURRENT
        states = {item.state for item in self.evidence if item.role is EvidenceRole.PREMISE}
        if EvidenceState.ABSENT in states:
            return RelationshipCurrency.NOT_CURRENT
        if states == {EvidenceState.PRESENT}:
            return RelationshipCurrency.CURRENT
        return RelationshipCurrency.UNKNOWN

    def as_dict(self) -> dict[str, Any]:
        return {
            "support_key": self.support_key,
            "derivation_class": self.derivation_class.value,
            "rule": {"rule_id": self.rule.rule_id, "version": self.rule.version},
            "confidence": self.confidence.value,
            "currency": self.currency.value,
            "qualifiers": sorted(item.value for item in self.qualifiers),
            "conditions": sorted(item.value for item in self.conditions),
            "attributes": dict(sorted(self.attributes.items())),
            "basis_token": self.basis_token,
            "derived_at": _iso(self.derived_at),
            "first_observed_at": _iso(self.first_observed_at),
            "first_observed_run_id": str(self.first_observed_run_id),
            "last_observed_at": _iso(self.last_observed_at),
            "last_observed_run_id": str(self.last_observed_run_id),
            "retired_at": None if self.retired_at is None else _iso(self.retired_at),
            "retired_basis_token": self.retired_basis_token,
            "evidence": [
                item.as_dict() for item in sorted(self.evidence, key=lambda e: (e.kind, e.key))
            ],
        }


_CURRENCY_RANK: Final[dict[RelationshipCurrency, int]] = {
    RelationshipCurrency.NOT_CURRENT: 0,
    RelationshipCurrency.UNKNOWN: 1,
    RelationshipCurrency.CURRENT: 2,
}


@dataclass(frozen=True, slots=True)
class StoredRelationship:
    """A relationship as stored: its identity and every support it has.

    Everything a reader asks of it beyond identity — its class, confidence, currency,
    observation window — is folded from the supports on read rather than stored beside them.
    """

    relationship_id: str
    relationship_type: RelationshipType
    source: NodeRef
    target: NodeRef
    scope: RelationshipScope | None
    supports: tuple[RelationshipSupport, ...]

    def __post_init__(self) -> None:
        if not self.supports:
            raise DomainValidationError(
                "A stored relationship has at least one support.", field="supports"
            )
        object.__setattr__(
            self, "supports", tuple(sorted(self.supports, key=lambda s: s.support_key))
        )

    @property
    def definition(self) -> RelationshipTypeDefinition:
        return definition_of(self.relationship_type)

    @property
    def best_support(self) -> RelationshipSupport:
        """The support the relationship is as strong as.

        Current before unknown before not current; then the higher confidence; then the
        stronger class; then the most recently confirmed; then the support key, so the choice
        never depends on storage order.
        """
        return max(
            self.supports,
            key=lambda support: (
                _CURRENCY_RANK[support.currency],
                CONFIDENCE_ORDER[support.confidence],
                DERIVATION_CLASS_ORDER[support.derivation_class],
                support.last_observed_at,
                support.support_key,
            ),
        )

    @property
    def derivation_class(self) -> DerivationClass:
        return self.best_support.derivation_class

    @property
    def confidence(self) -> RelationshipConfidence:
        return self.best_support.confidence

    @property
    def currency(self) -> RelationshipCurrency:
        return currency_of(support.currency for support in self.supports)

    @property
    def first_observed_at(self) -> dt.datetime:
        return min(support.first_observed_at for support in self.supports)

    @property
    def last_observed_at(self) -> dt.datetime:
        return max(support.last_observed_at for support in self.supports)

    def explanation(self, labels: Mapping[NodeRef, str] | None = None) -> str:
        return explain(self, labels)

    def as_dict(self) -> dict[str, Any]:
        """A deterministic rendering: equal relationships serialize to equal values."""
        return {
            "relationship_id": self.relationship_id,
            "relationship_type": self.relationship_type.value,
            "source": {"kind": self.source.kind.value, "key": self.source.key},
            "target": {"kind": self.target.kind.value, "key": self.target.key},
            "scope": (
                None
                if self.scope is None
                else {"kind": self.scope.kind.value, "key": self.scope.key}
            ),
            "derivation_class": self.derivation_class.value,
            "confidence": self.confidence.value,
            "currency": self.currency.value,
            "first_observed_at": _iso(self.first_observed_at),
            "last_observed_at": _iso(self.last_observed_at),
            "explanation": self.explanation(),
            "supports": [support.as_dict() for support in self.supports],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat(timespec="microseconds")


# --- Folds --------------------------------------------------------------------------------


def confidence_of(
    derivation_class: DerivationClass, qualifiers: Iterable[RelationshipQualifier]
) -> RelationshipConfidence:
    """The weakest of what the class allows and what the qualifiers allow. Never a score."""
    found = set(qualifiers)
    by_class = {
        DerivationClass.OBSERVED: RelationshipConfidence.CONFIRMED,
        DerivationClass.DETERMINISTICALLY_DERIVED: RelationshipConfidence.CONFIRMED,
        DerivationClass.CONDITIONALLY_DERIVED: RelationshipConfidence.PROBABLE,
        DerivationClass.UNRESOLVED: RelationshipConfidence.INDETERMINATE,
    }[derivation_class]
    candidates = [by_class]
    if found & UNRESOLVED_QUALIFIERS:
        candidates.append(RelationshipConfidence.INDETERMINATE)
    if found - UNRESOLVED_QUALIFIERS:
        candidates.append(RelationshipConfidence.POSSIBLE)
    return min(candidates, key=CONFIDENCE_ORDER.__getitem__)


def currency_of(currencies: Iterable[RelationshipCurrency]) -> RelationshipCurrency:
    """Current if any support is; not current only if every support was measured gone."""
    found = set(currencies)
    if RelationshipCurrency.CURRENT in found:
        return RelationshipCurrency.CURRENT
    if found == {RelationshipCurrency.NOT_CURRENT}:
        return RelationshipCurrency.NOT_CURRENT
    return RelationshipCurrency.UNKNOWN


def explain(relationship: StoredRelationship, labels: Mapping[NodeRef, str] | None = None) -> str:
    """An operator-facing account of a relationship, computed and never stored.

    ``labels`` supplies a display name per node — resolved on read, because names are mutable
    metadata (ADR-0001) and a stored sentence would keep a name after the rename. A node with
    no label is shown by its key. Deterministic for equal inputs.
    """
    labels = labels or {}
    definition = relationship.definition
    sentence = definition.describe(
        labels.get(relationship.source, relationship.source.key),
        labels.get(relationship.target, relationship.target.key),
    )
    if relationship.scope is not None:
        sentence += f" (on host {relationship.scope.key})"
    parts = [sentence + "."]

    best = relationship.best_support
    premises = [item for item in best.evidence if item.role is EvidenceRole.PREMISE]
    if best.derivation_class is DerivationClass.OBSERVED:
        parts.append(f"Observed: one collected {premises[0].kind.value} states it.")
    elif best.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED:
        parts.append(f"Derived by {best.rule} from {len(premises)} observed fact(s).")
    elif best.derivation_class is DerivationClass.CONDITIONALLY_DERIVED:
        conditions = "; ".join(
            describe_condition(condition) for condition in sorted(best.conditions)
        )
        parts.append(f"Derived by {best.rule}, and holds only if: {conditions}")
    else:
        unresolved = " ".join(
            describe_qualifier(qualifier)
            for qualifier in sorted(best.qualifiers & UNRESOLVED_QUALIFIERS)
        )
        parts.append(f"It exists, and what it connects cannot be established. {unresolved}")

    # Every qualifier is said once: the unresolved ones were said above for an unresolved
    # support, and are said here for any other class that carries one (a conditional host
    # privilege whose member is foreign, for instance).
    stated = (
        UNRESOLVED_QUALIFIERS
        if best.derivation_class is DerivationClass.UNRESOLVED
        else frozenset[RelationshipQualifier]()
    )
    other = sorted(best.qualifiers - stated)
    if other:
        parts.append(" ".join(describe_qualifier(qualifier) for qualifier in other))
    if len(relationship.supports) > 1:
        parts.append(f"Supported {len(relationship.supports)} independent ways.")
    currency = relationship.currency
    if currency is RelationshipCurrency.NOT_CURRENT:
        if best.is_retired:
            parts.append(
                "Not current: the facts it rested on are still stored and, as they now read, "
                "no longer establish it."
            )
        else:
            parts.append("Not current: an authoritative scan recorded its evidence gone.")
    elif currency is RelationshipCurrency.UNKNOWN:
        parts.append(
            "Whether it still holds is unknown: evidence it rests on is no longer stored, "
            "and nothing recorded it gone."
        )
    return " ".join(parts)
