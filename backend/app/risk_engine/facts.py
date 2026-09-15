"""The typed snapshot a rule is evaluated over, and the scope it covers.

A rule is a pure predicate over :class:`RiskFacts`. It never reads a database, never calls a
repository, and never sees a SQLAlchemy row — :mod:`app.services.risk` loads the facts and
hands them here. That separation is what makes a finding reproducible: the whole input to a
rule is a value, so the same value always produces the same finding, on any machine, in any
order, with no clock involved.

**The risk engine has no second opinion about what a mask means.** Every question about
rights is answered by :mod:`app.access_engine.rights` — the same algebra the effective-access
engine uses — and every question about inheritance by :mod:`app.domain.inheritance`. A risk
rule that decided for itself what "Modify" is would eventually disagree with the access
screen about the same ACE, and one of the two would be wrong with nothing to say which.

**Facts carry their own gaps.** A share whose ACL no run has read is ``acl_observed=False``,
not an empty ACL; a group nobody enumerated is ``enumerated=None``, not an empty group. Rules
are written against those three-valued facts, and the rules that could otherwise turn a
coverage gap into a finding — the empty-group rule above all — are required to check them.

Every record here round-trips through :mod:`app.risk_engine.evidence`: ``to_evidence`` renders
the complete record as flat, JSON-safe attributes and ``from_evidence`` rebuilds it. That is
what makes an evidence blob sufficient to re-derive the finding it justifies rather than
merely to illustrate it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from app.access_engine import (
    RightsCategory,
    RightsMask,
    RightsSummary,
    normalize_share_permission,
    summarize,
)
from app.domain import (
    AceFlag,
    AceSource,
    AceType,
    AclBoundaryReason,
    AclLayer,
    GroupScope,
    GroupType,
    PrincipalKind,
    SharePermission,
    ShareType,
    Sid,
    UnresolvedReason,
)
from app.domain.errors import DomainValidationError
from app.risk_engine.severity import FactQualifier

__all__ = [
    "MAX_WALK_DEPTH",
    "AceFacts",
    "AclProvenanceFacts",
    "Chain",
    "FactKind",
    "MembershipFacts",
    "PrincipalFacts",
    "ResourceFacts",
    "RiskFacts",
    "RiskScope",
    "ShareFacts",
    "domain_relative_trustee",
]


class FactKind(StrEnum):
    """The kinds of collected object a rule can depend on.

    Value-identical to :class:`app.contracts.v1.common.ObservationKind`, and restated rather
    than imported so the risk engine depends on nothing that validates HTTP payloads — the
    same reason :data:`app.repositories.resources.MAX_ACL_ENTRIES` is restated beside the
    evaluator's own ceiling. ``tests/risk_engine/test_facts.py`` pins the two enumerations
    equal, so a kind added to the contract and not here fails the suite rather than quietly
    producing rules that never re-evaluate.
    """

    PRINCIPAL = "principal"
    MEMBERSHIP_EDGE = "membership_edge"
    SERVER = "server"
    SMB_SHARE = "smb_share"
    SMB_ACE = "smb_ace"
    NTFS_RESOURCE = "ntfs_resource"
    NTFS_ACE = "ntfs_ace"


MAX_WALK_DEPTH: Final = 32
"""Ceiling on any membership walk a rule performs, regardless of what it asks for.

A rule asking for an unbounded walk over a graph with a cycle in it is a hang, and the graph
demonstrably has cycles in it (:mod:`app.domain.graph` reports them because real estates
contain them). The walks here terminate on a visited set as well, so this is the second of
two guards rather than the only one.
"""


class AclProvenanceFacts(StrEnum):
    """Where a resource's DACL came from.

    Mirrors :class:`app.access_engine.AclProvenance` in meaning and is restated rather than
    imported so the fact model keeps one vocabulary of its own; the service layer maps
    between them in one place.
    """

    OBSERVED = "observed"
    """A collector read this object's own security descriptor."""

    DERIVED = "derived"
    """The descriptor was never read; the DACL was projected onto this object from the
    nearest ancestor that was."""

    UNOBSERVED = "unobserved"
    """Neither this object nor any ancestor was read. Rules must not conclude anything about
    permissions from such a resource, and none of them do: no ACE means no match."""


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _enum(kind: type[StrEnum], value: object) -> Any:
    """Parse an enum from evidence, naming the field rather than raising ``ValueError``."""
    if value is None:
        return None
    try:
        return kind(str(value))
    except ValueError as error:  # pragma: no cover - defensive; evidence is machine-written
        raise DomainValidationError(
            f"{value!r} is not a valid {kind.__name__}.", value=str(value)
        ) from error


# --------------------------------------------------------------------------------------
# Access control entries
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AceFacts:
    """One access control entry, on either layer, as a collector read it.

    ``container_key`` is the resource key for an NTFS entry and the share key for a share
    entry. One record type for both layers because every rule that looks at an entry asks the
    same three questions of it — who, how much, allow or deny — and a second record type
    would mean every rule branching on the layer to ask them.
    """

    ace_key: str
    container_key: str
    layer: AclLayer
    trustee_key: str
    trustee_sid: str
    ace_type: AceType
    access_mask: int | None
    ace_flags: int = 0
    source: AceSource = AceSource.EXPLICIT
    permission: SharePermission | None = None
    order_index: int | None = None

    def __post_init__(self) -> None:
        if self.layer is AclLayer.NTFS and self.access_mask is None:
            raise DomainValidationError(
                "An NTFS entry always carries an access mask; a share entry may carry a "
                "permission level instead.",
                field="access_mask",
            )
        if self.access_mask is None and self.permission is None:
            raise DomainValidationError(
                "An entry must record either an access mask or a share permission level: "
                "an entry that records neither grants an unknown amount, and treating that "
                "as nothing would hide the grant.",
                field="access_mask",
            )

    @property
    def is_allow(self) -> bool:
        return self.ace_type is AceType.ALLOW

    @property
    def is_deny(self) -> bool:
        return self.ace_type is AceType.DENY

    @property
    def applies_to_this_object(self) -> bool:
        """False for an ``INHERIT_ONLY`` entry, which grants nothing where it is stored."""
        return not (AceFlag(self.ace_flags) & AceFlag.INHERIT_ONLY)

    @property
    def is_inherited(self) -> bool:
        return self.source is AceSource.INHERITED

    @property
    def rights(self) -> RightsMask:
        """The entry's mask, in the layer it belongs to, with generic bits expanded.

        A share entry reported as a permission level is converted through
        :func:`app.access_engine.normalize_share_permission`, which is the same conversion
        the access engine performs — so a rule and the access screen cannot disagree about
        what ``Change`` means.
        """
        if self.access_mask is not None:
            raw = (
                RightsMask.ntfs(self.access_mask)
                if self.layer is AclLayer.NTFS
                else RightsMask.smb(self.access_mask)
            )
            return raw.expand_generics()
        assert self.permission is not None  # guaranteed by __post_init__
        return normalize_share_permission(self.permission).expand_generics()

    @property
    def summary(self) -> RightsSummary:
        return summarize(self.rights)

    @property
    def category(self) -> RightsCategory:
        """The highest named category this entry's mask fully contains."""
        return self.summary.primary

    def to_evidence(self) -> dict[str, Any]:
        return {
            "ace_key": self.ace_key,
            "container_key": self.container_key,
            "layer": self.layer.value,
            "trustee_key": self.trustee_key,
            "trustee_sid": self.trustee_sid,
            "ace_type": self.ace_type.value,
            "access_mask": self.access_mask,
            "ace_flags": self.ace_flags,
            "source": self.source.value,
            "permission": None if self.permission is None else self.permission.value,
            "order_index": self.order_index,
        }

    @classmethod
    def from_evidence(cls, attributes: Mapping[str, Any]) -> AceFacts:
        mask = attributes.get("access_mask")
        return cls(
            ace_key=str(attributes["ace_key"]),
            container_key=str(attributes["container_key"]),
            layer=_enum(AclLayer, attributes["layer"]),
            trustee_key=str(attributes["trustee_key"]),
            trustee_sid=str(attributes["trustee_sid"]),
            ace_type=_enum(AceType, attributes["ace_type"]),
            access_mask=None if mask is None else int(mask),
            ace_flags=int(attributes.get("ace_flags", 0)),
            source=_enum(AceSource, attributes.get("source", AceSource.EXPLICIT.value)),
            permission=_enum(SharePermission, attributes.get("permission")),
            order_index=(
                None if attributes.get("order_index") is None else int(attributes["order_index"])
            ),
        )


# --------------------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResourceFacts:
    """One directory, its descriptor-level facts, and the entries on its DACL."""

    resource_key: str
    path: str
    share_key: str
    server_key: str
    owner_sid: str | None = None
    dacl_present: bool = True
    dacl_protected: bool = False
    inheritance_enabled: bool = True
    is_acl_boundary: bool = False
    boundary_reason: AclBoundaryReason | None = None
    undelivered_ace_count: int = 0
    """How many entries the descriptor declared that ADG does not hold.

    Stored as the *shortfall* rather than as the declared total on purpose. Evidence for a
    finding cites only the entries that bear on it — an entry naming another trustee cannot
    change this trustee's rights, because the access check skips it — so a record rebuilt
    from evidence carries fewer entries than the one the rule read. A declared total would
    then appear to be short by the entries the evidence deliberately left out, and a finding
    would reproduce with a weaker confidence than it was recorded with. The shortfall is the
    same number before and after that restriction, which is what makes it the honest field to
    keep.
    """

    depth_from_share_root: int | None = None
    provenance: AclProvenanceFacts = AclProvenanceFacts.OBSERVED
    derived_distance: int | None = None
    aces: tuple[AceFacts, ...] = ()

    @property
    def is_share_root(self) -> bool:
        """Derived from the depth the scan recorded, and from the path when it did not."""
        if self.depth_from_share_root is not None:
            return self.depth_from_share_root == 0
        return self.path.rstrip("\\").count("\\") <= 3

    @property
    def grants_everyone_full_access(self) -> bool:
        """A NULL DACL: no access control list at all, which is not an empty one."""
        return not self.dacl_present

    @property
    def ace_count_short(self) -> bool:
        """The descriptor declared more entries than ADG holds for it."""
        return self.undelivered_ace_count > 0

    @property
    def qualifiers(self) -> frozenset[FactQualifier]:
        """What keeps a finding over this resource short of directly observed."""
        found: set[FactQualifier] = set()
        if self.provenance is AclProvenanceFacts.DERIVED:
            found.add(FactQualifier.ACL_DERIVED)
            if self.derived_distance is not None and self.derived_distance > 1:
                found.add(FactQualifier.ACL_DERIVED_DISTANT)
        if self.ace_count_short:
            found.add(FactQualifier.ACE_COUNT_SHORT)
        return frozenset(found)

    def allow_entries(self) -> tuple[AceFacts, ...]:
        """Allow entries that actually apply to this directory, in stored order."""
        return tuple(ace for ace in self.aces if ace.is_allow and ace.applies_to_this_object)

    def to_evidence(self) -> dict[str, Any]:
        return {
            "resource_key": self.resource_key,
            "path": self.path,
            "share_key": self.share_key,
            "server_key": self.server_key,
            "owner_sid": self.owner_sid,
            "dacl_present": self.dacl_present,
            "dacl_protected": self.dacl_protected,
            "inheritance_enabled": self.inheritance_enabled,
            "is_acl_boundary": self.is_acl_boundary,
            "boundary_reason": (
                None if self.boundary_reason is None else self.boundary_reason.value
            ),
            "undelivered_ace_count": self.undelivered_ace_count,
            "depth_from_share_root": self.depth_from_share_root,
            "provenance": self.provenance.value,
            "derived_distance": self.derived_distance,
        }

    @classmethod
    def from_evidence(
        cls, attributes: Mapping[str, Any], aces: Sequence[AceFacts] = ()
    ) -> ResourceFacts:
        depth = attributes.get("depth_from_share_root")
        distance = attributes.get("derived_distance")
        return cls(
            resource_key=str(attributes["resource_key"]),
            path=str(attributes["path"]),
            share_key=str(attributes["share_key"]),
            server_key=str(attributes["server_key"]),
            owner_sid=_text(attributes.get("owner_sid")),
            dacl_present=bool(attributes.get("dacl_present", True)),
            dacl_protected=bool(attributes.get("dacl_protected", False)),
            inheritance_enabled=bool(attributes.get("inheritance_enabled", True)),
            is_acl_boundary=bool(attributes.get("is_acl_boundary", False)),
            boundary_reason=_enum(AclBoundaryReason, attributes.get("boundary_reason")),
            undelivered_ace_count=int(attributes.get("undelivered_ace_count", 0)),
            depth_from_share_root=None if depth is None else int(depth),
            provenance=_enum(
                AclProvenanceFacts,
                attributes.get("provenance", AclProvenanceFacts.OBSERVED.value),
            ),
            derived_distance=None if distance is None else int(distance),
            aces=tuple(aces),
        )


@dataclass(frozen=True, slots=True)
class ShareFacts:
    """One SMB share and the entries on its share-level ACL.

    ``acl_observed=False`` is the whole reason this record exists separately from a list of
    entries: a share whose ACL nobody has read is not a share that grants nothing, and a rule
    that matched on "no Everyone entry" over an unread ACL would be reporting the absence of
    a finding it never looked for.
    """

    share_key: str
    server_key: str
    name: str
    share_type: ShareType = ShareType.DISK
    is_special: bool | None = None
    acl_observed: bool = True
    aces: tuple[AceFacts, ...] = ()

    def allow_entries(self) -> tuple[AceFacts, ...]:
        return tuple(ace for ace in self.aces if ace.is_allow)

    def to_evidence(self) -> dict[str, Any]:
        return {
            "share_key": self.share_key,
            "server_key": self.server_key,
            "name": self.name,
            "share_type": self.share_type.value,
            "is_special": self.is_special,
            "acl_observed": self.acl_observed,
        }

    @classmethod
    def from_evidence(
        cls, attributes: Mapping[str, Any], aces: Sequence[AceFacts] = ()
    ) -> ShareFacts:
        special = attributes.get("is_special")
        return cls(
            share_key=str(attributes["share_key"]),
            server_key=str(attributes["server_key"]),
            name=str(attributes["name"]),
            share_type=_enum(ShareType, attributes.get("share_type", ShareType.DISK.value)),
            is_special=None if special is None else bool(special),
            acl_observed=bool(attributes.get("acl_observed", True)),
            aces=tuple(aces),
        )


# --------------------------------------------------------------------------------------
# Principals and membership
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrincipalFacts:
    """What ADG knows about one principal. Every name-like attribute is metadata."""

    key: str
    sid: str
    kind: PrincipalKind
    display_name: str | None = None
    sam_account_name: str | None = None
    enabled: bool | None = None
    is_deleted: bool = False
    group_scope: GroupScope | None = None
    group_type: GroupType | None = None
    host_key: str | None = None
    domain_sid: str | None = None
    unresolved_reason: UnresolvedReason | None = None

    @property
    def is_group(self) -> bool:
        return self.kind in (PrincipalKind.DOMAIN_GROUP, PrincipalKind.LOCAL_GROUP)

    @property
    def is_user(self) -> bool:
        return self.kind in (PrincipalKind.USER, PrincipalKind.MANAGED_SERVICE_ACCOUNT)

    @property
    def is_unresolved(self) -> bool:
        return self.kind is PrincipalKind.UNRESOLVED

    @property
    def grants_access(self) -> bool | None:
        """Distribution groups never appear on an ACL usefully; ``None`` when unknown."""
        if not self.is_group or self.group_type is None or self.group_type is GroupType.UNKNOWN:
            return None
        return self.group_type is GroupType.SECURITY

    @property
    def label(self) -> str:
        """Something to put in a report. Never a lookup key."""
        return self.display_name or self.sam_account_name or self.sid

    def to_evidence(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "sid": self.sid,
            "kind": self.kind.value,
            "display_name": self.display_name,
            "sam_account_name": self.sam_account_name,
            "enabled": self.enabled,
            "is_deleted": self.is_deleted,
            "group_scope": None if self.group_scope is None else self.group_scope.value,
            "group_type": None if self.group_type is None else self.group_type.value,
            "host_key": self.host_key,
            "domain_sid": self.domain_sid,
            "unresolved_reason": (
                None if self.unresolved_reason is None else self.unresolved_reason.value
            ),
        }

    @classmethod
    def from_evidence(cls, attributes: Mapping[str, Any]) -> PrincipalFacts:
        enabled = attributes.get("enabled")
        return cls(
            key=str(attributes["key"]),
            sid=str(attributes["sid"]),
            kind=_enum(PrincipalKind, attributes["kind"]),
            display_name=_text(attributes.get("display_name")),
            sam_account_name=_text(attributes.get("sam_account_name")),
            enabled=None if enabled is None else bool(enabled),
            is_deleted=bool(attributes.get("is_deleted", False)),
            group_scope=_enum(GroupScope, attributes.get("group_scope")),
            group_type=_enum(GroupType, attributes.get("group_type")),
            host_key=_text(attributes.get("host_key")),
            domain_sid=_text(attributes.get("domain_sid")),
            unresolved_reason=_enum(UnresolvedReason, attributes.get("unresolved_reason")),
        )


@dataclass(frozen=True, slots=True)
class MembershipFacts:
    """The direct members of one group, and whether anybody actually enumerated it.

    ``enumerated`` is the field that keeps the empty-group rule honest, and it is three-valued
    on purpose:

    * ``True`` — a run that reconciled the scope this group belongs to listed its members.
      An empty list therefore means the group is empty.
    * ``False`` — a run reported this group but did not reconcile a scope covering it, so an
      empty list means nothing was listed rather than that nothing is there.
    * ``None`` — no run has said either way.

    Only ``True`` licenses a statement about emptiness. See
    :class:`app.risk_engine.rules.EmptyPermissionBearingGroup`.
    """

    group_key: str
    member_keys: tuple[str, ...] = ()
    enumerated: bool | None = None
    truncated: bool = False
    """The member list was cut short by a fetch limit. An empty list is never truncated, so
    this never weakens the emptiness claim; it weakens claims about what is *inside*."""

    @property
    def is_empty(self) -> bool:
        return not self.member_keys

    def to_evidence(self) -> dict[str, Any]:
        return {
            "group_key": self.group_key,
            "member_keys": list(self.member_keys),
            "enumerated": self.enumerated,
            "truncated": self.truncated,
        }

    @classmethod
    def from_evidence(cls, attributes: Mapping[str, Any]) -> MembershipFacts:
        enumerated = attributes.get("enumerated")
        members = attributes.get("member_keys") or ()
        return cls(
            group_key=str(attributes["group_key"]),
            member_keys=tuple(str(item) for item in members),
            enumerated=None if enumerated is None else bool(enumerated),
            truncated=bool(attributes.get("truncated", False)),
        )

    def restricted_to(self, member_key: str) -> MembershipFacts:
        """This record reduced to one member, for evidence that names a single chain.

        A finding about a nesting chain has no business carrying the whole member list of
        every group on it. What it must carry is the edge it walked, which is exactly this.
        Emptiness is never claimed from a restricted record — ``member_keys`` is non-empty by
        construction — so reducing a record cannot manufacture the one finding that turns on
        a member list being complete.
        """
        return MembershipFacts(
            group_key=self.group_key,
            member_keys=(member_key,),
            enumerated=self.enumerated,
            truncated=self.truncated,
        )


@dataclass(frozen=True, slots=True)
class Chain:
    """One bounded route through the membership graph, with the edges it used."""

    keys: tuple[str, ...]
    """Group keys from the starting node to the end, inclusive of both."""

    edges: tuple[MembershipFacts, ...]
    """The membership record behind each step, each reduced to the member it was walked to."""

    truncated: bool = False

    @property
    def depth(self) -> int:
        """Edges traversed. A principal directly inside one group has depth 1."""
        return len(self.keys) - 1


# --------------------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskScope:
    """What a fact bundle covers, and therefore what an evaluation may resolve.

    This is the guard that keeps incremental re-evaluation from silently closing findings it
    never looked at. A partial evaluation loads the facts around what changed; it must
    reconcile only the findings whose subject is inside that scope, and leave every other
    open finding exactly as it was. A full evaluation covers everything and may reconcile
    everything.

    ``None`` for a key set means *unrestricted on this axis* — which for a partial scope is
    still narrowed by the other axes. A scope with ``complete`` set and no restrictions is
    the whole estate.
    """

    complete: bool = False
    resource_keys: frozenset[str] | None = None
    share_keys: frozenset[str] | None = None
    principal_keys: frozenset[str] | None = None

    @classmethod
    def everything(cls) -> RiskScope:
        return cls(complete=True)

    def covers_resource(self, resource_key: str | None) -> bool:
        if self.complete:
            return True
        if resource_key is None:
            return False
        return self.resource_keys is not None and resource_key in self.resource_keys

    def covers_share(self, share_key: str | None) -> bool:
        if self.complete:
            return True
        if share_key is None:
            return False
        return self.share_keys is not None and share_key in self.share_keys

    def covers_principal(self, principal_key: str | None) -> bool:
        if self.complete:
            return True
        if principal_key is None:
            return False
        return self.principal_keys is not None and principal_key in self.principal_keys


# --------------------------------------------------------------------------------------
# The bundle
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskFacts:
    """Everything a rule may read, and nothing it may not.

    Construction builds two indexes — group to members and member to groups — so that the
    membership walks below are in-process dictionary lookups rather than anything that could
    reach a database from inside a rule.
    """

    resources: tuple[ResourceFacts, ...] = ()
    shares: tuple[ShareFacts, ...] = ()
    principals: Mapping[str, PrincipalFacts] = field(default_factory=dict)
    memberships: Mapping[str, MembershipFacts] = field(default_factory=dict)
    scope: RiskScope = field(default_factory=RiskScope.everything)

    _groups_by_member: dict[str, tuple[str, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        reverse: dict[str, list[str]] = {}
        for group_key in sorted(self.memberships):
            for member_key in self.memberships[group_key].member_keys:
                reverse.setdefault(member_key, []).append(group_key)
        object.__setattr__(
            self,
            "_groups_by_member",
            {member: tuple(groups) for member, groups in reverse.items()},
        )

    # -- lookups ----------------------------------------------------------------------

    def principal(self, key: str) -> PrincipalFacts | None:
        return self.principals.get(key)

    def membership(self, group_key: str) -> MembershipFacts | None:
        return self.memberships.get(group_key)

    def resource(self, resource_key: str) -> ResourceFacts | None:
        for resource in self.resources:
            if resource.resource_key == resource_key:
                return resource
        return None

    def share(self, share_key: str) -> ShareFacts | None:
        for share in self.shares:
            if share.share_key == share_key:
                return share
        return None

    def direct_members(self, group_key: str) -> tuple[str, ...]:
        record = self.memberships.get(group_key)
        return () if record is None else record.member_keys

    def direct_groups(self, member_key: str) -> tuple[str, ...]:
        return self._groups_by_member.get(member_key, ())

    # -- bounded walks ----------------------------------------------------------------

    def chains_down(self, group_key: str, max_depth: int) -> Iterator[Chain]:
        """Every bounded downward route from ``group_key``, deepest-first within a branch.

        Yields one :class:`Chain` per reachable principal per route. Cycles terminate on the
        path's own visited set, so a group that contains an ancestor of itself is walked once
        and reported once rather than forever — the estate really does contain those (see the
        demo estate's Contractors/Project-001 cycle).
        """
        limit = min(max(max_depth, 0), MAX_WALK_DEPTH)
        yield from self._walk(group_key, limit, downward=True)

    def chains_up(self, member_key: str, max_depth: int) -> Iterator[Chain]:
        """Every bounded upward route from ``member_key`` to a group that contains it."""
        limit = min(max(max_depth, 0), MAX_WALK_DEPTH)
        yield from self._walk(member_key, limit, downward=False)

    def _walk(self, start: str, limit: int, *, downward: bool) -> Iterator[Chain]:
        def step(
            node: str, visited: tuple[str, ...], edges: tuple[MembershipFacts, ...], truncated: bool
        ) -> Iterator[Chain]:
            if len(visited) - 1 >= limit:
                if self._has_next(node, downward=downward):
                    yield Chain(keys=visited, edges=edges, truncated=True)
                return
            nexts = self.direct_members(node) if downward else self.direct_groups(node)
            for other in nexts:
                if other in visited:
                    continue
                record = self.memberships.get(node if downward else other)
                if record is None:  # pragma: no cover - index and store are built together
                    continue
                walked = record.restricted_to(other if downward else node)
                carried = truncated or record.truncated
                chain = Chain(keys=(*visited, other), edges=(*edges, walked), truncated=carried)
                yield chain
                yield from step(other, chain.keys, chain.edges, carried)

        yield from step(start, (start,), (), False)

    def _has_next(self, node: str, *, downward: bool) -> bool:
        return bool(self.direct_members(node) if downward else self.direct_groups(node))

    # -- derived views ----------------------------------------------------------------

    def acl_trustees(self) -> tuple[str, ...]:
        """Every trustee key named by any entry in this bundle, in sorted order."""
        keys = {ace.trustee_key for resource in self.resources for ace in resource.aces}
        keys |= {ace.trustee_key for share in self.shares for ace in share.aces}
        return tuple(sorted(keys))

    def qualifiers_for(self, keys: Iterable[str]) -> frozenset[FactQualifier]:
        """What the named principals contribute to a finding's confidence."""
        found: set[FactQualifier] = set()
        for key in keys:
            if key not in self.principals:
                found.add(FactQualifier.PRINCIPAL_UNDESCRIBED)
            record = self.memberships.get(key)
            if record is not None and record.truncated:
                found.add(FactQualifier.MEMBERSHIP_TRUNCATED)
        return frozenset(found)


def domain_relative_trustee(sid_value: str, rid: int) -> bool:
    """Whether ``sid_value`` is a domain-relative SID ending in ``rid``.

    Used for the well-known domain groups whose SIDs are *not* constants — ``Domain Users``
    is ``<domain SID>-513``, a different string in every domain, which is why matching it by
    name (or by a hard-coded SID) is wrong. Parsing is delegated to :class:`app.domain.Sid`
    so the risk engine holds no second SID grammar.
    """
    parsed = Sid.try_parse(sid_value)
    if parsed is None:
        return False
    return parsed.is_domain_relative and parsed.rid == rid
