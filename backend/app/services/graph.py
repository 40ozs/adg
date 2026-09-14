"""Effective membership: the traversal, joined to what is known about each node.

The algorithm lives in :mod:`app.domain.graph` and the rows live in
:mod:`app.repositories.membership`; this module is what puts a name and a kind on each key
the traversal returned, and applies the one filter that needs care.

**The filter is the subtle part.** "Effective users in this group" wants leaves, not nested
groups — but a key the traversal reached may have no ``principals`` row at all, because an
edge can name a member that no run has described yet. Such a node is reported with a null
kind and ``resolved: false``, and it is **included** in every filter except the explicit
"users only", because a SID that demonstrably sits inside a group is exactly the kind of
finding this tool exists to surface. Dropping it for want of a label would hide it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain import (
    DEFAULT_LIMITS,
    Direction,
    Expansion,
    GraphCycle,
    GraphEdge,
    MembershipEdgeKind,
    MembershipPath,
    PathSearch,
    PrincipalKind,
    ReachedNode,
    TraversalLimits,
    TruncationReason,
    expand,
    find_paths,
)
from app.repositories.membership import MembershipRepository, PrincipalRecord

__all__ = [
    "GROUP_KINDS",
    "USER_KINDS",
    "EffectiveMembership",
    "GraphService",
    "MemberInclusion",
    "PathResult",
    "ResolvedNode",
    "split_key",
]

GROUP_KINDS: frozenset[PrincipalKind] = frozenset(
    {PrincipalKind.DOMAIN_GROUP, PrincipalKind.LOCAL_GROUP}
)

USER_KINDS: frozenset[PrincipalKind] = frozenset(
    {PrincipalKind.USER, PrincipalKind.MANAGED_SERVICE_ACCOUNT}
)


class MemberInclusion(StrEnum):
    """Which reached principals an effective-membership answer keeps."""

    USERS = "users"
    """User and managed-service accounts only. Excludes anything unlabelled."""

    NON_GROUPS = "non_groups"
    """Everything that is not a known group: users, computers, well-known SIDs,
    unresolved SIDs, and principals ADG has not described yet. The default, because an
    orphaned SID inside a group is a finding and must not be filtered away."""

    ALL = "all"
    """Every reached principal, nested groups included."""


def split_key(key: str) -> tuple[str | None, str]:
    """Split a storage key into ``(host, sid)``.

    Local-group keys are ``host|sid``; everything else is a bare SID. Used only to label a
    node the traversal reached but no ``principals`` row describes.

    Split on the **last** separator, not the first: a SID can never contain ``|``, but a
    host name is only forbidden path separators and control characters, so splitting at the
    front would hand back a truncated host and a SID with the rest of the host glued to it —
    and that mislabelled pair is what an operator would be shown for the one kind of node
    ADG knows least about.
    """
    host, separator, sid = key.rpartition("|")
    return (host, sid) if separator else (None, key)


@dataclass(frozen=True, slots=True)
class ResolvedNode:
    """One node a traversal reached, labelled with whatever is known about it."""

    key: str
    sid: str
    host_key: str | None
    depth: int
    path: tuple[str, ...]
    edge_kinds: tuple[MembershipEdgeKind, ...]
    via_foreign_security_principal: bool
    principal: PrincipalRecord | None
    reported_kind: PrincipalKind | None

    @property
    def kind(self) -> PrincipalKind | None:
        """The best available classification: the stored row, else the edge's claim."""
        if self.principal is not None:
            return self.principal.principal_kind
        return self.reported_kind

    @property
    def resolved(self) -> bool:
        """Whether ADG holds a principal record for this key.

        ``False`` does not mean the membership is doubtful — the edge was observed. It
        means nothing has described what sits at the far end of it.
        """
        return self.principal is not None

    @property
    def is_group(self) -> bool | None:
        """``None`` when the kind is unknown, so "unsure" is never reported as "no"."""
        kind = self.kind
        if kind is None:
            return None
        return kind in GROUP_KINDS

    def matches(self, inclusion: MemberInclusion) -> bool:
        kind = self.kind
        if inclusion is MemberInclusion.ALL:
            return True
        if inclusion is MemberInclusion.USERS:
            return kind in USER_KINDS
        return kind not in GROUP_KINDS  # NON_GROUPS: an unknown kind is kept, not dropped


@dataclass(frozen=True, slots=True)
class EffectiveMembership:
    """A bounded recursive membership answer, with its explanation and its limits."""

    root_key: str
    direction: Direction
    nodes: tuple[ResolvedNode, ...]
    edges: tuple[GraphEdge, ...]
    """The subgraph the traversal actually walked.

    Carried so that a caller holding this answer can enumerate **alternate** routes through
    it without a second round of queries: :attr:`ResolvedNode.path` records only the
    shortest chain to each node, and an explanation that shows one chain where two exist
    invites a remediation that changes nothing.
    """

    cycles: tuple[GraphCycle, ...]
    limits: TraversalLimits
    truncation: tuple[TruncationReason, ...]
    depth_reached: int
    nodes_visited: int
    edges_read: int

    @property
    def complete(self) -> bool:
        return not self.truncation


@dataclass(frozen=True, slots=True)
class PathResult:
    """Every bounded route from a principal to a group."""

    member_key: str
    group_key: str
    paths: tuple[MembershipPath, ...]
    labels: dict[str, PrincipalRecord]
    cycles: tuple[GraphCycle, ...]
    limits: TraversalLimits
    truncation: tuple[TruncationReason, ...]

    @property
    def complete(self) -> bool:
        return not self.truncation

    @property
    def is_member(self) -> bool:
        return bool(self.paths)


class GraphService:
    """Answers the recursive membership questions over one database session."""

    def __init__(self, repository: MembershipRepository) -> None:
        self._repository = repository

    async def effective_members(
        self, group_key: str, limits: TraversalLimits = DEFAULT_LIMITS
    ) -> EffectiveMembership:
        """Every principal reachable downward from ``group_key``."""
        return await self._expand(group_key, Direction.DOWN, limits)

    async def effective_groups(
        self, principal_key: str, limits: TraversalLimits = DEFAULT_LIMITS
    ) -> EffectiveMembership:
        """Every group reachable upward from ``principal_key``."""
        return await self._expand(principal_key, Direction.UP, limits)

    async def _expand(
        self, root: str, direction: Direction, limits: TraversalLimits
    ) -> EffectiveMembership:
        expansion: Expansion = await expand(root, direction, self._repository, limits)
        labels = await self._repository.principals_by_keys([node.key for node in expansion.nodes])

        nodes = tuple(_resolve(node, labels.get(node.key)) for node in expansion.nodes)
        return EffectiveMembership(
            root_key=root,
            direction=direction,
            nodes=nodes,
            edges=expansion.edges,
            cycles=expansion.cycles,
            limits=limits,
            truncation=expansion.truncation,
            depth_reached=expansion.depth_reached,
            nodes_visited=len(expansion.nodes),
            edges_read=self._repository.edges_fetched,
        )

    async def membership_paths(
        self, member_key: str, group_key: str, limits: TraversalLimits = DEFAULT_LIMITS
    ) -> PathResult:
        """Every simple chain of memberships from ``member_key`` up to ``group_key``."""
        search: PathSearch = await find_paths(member_key, group_key, self._repository, limits)
        keys = {node for path in search.paths for node in path.nodes}
        labels = await self._repository.principals_by_keys(sorted(keys))
        return PathResult(
            member_key=member_key,
            group_key=group_key,
            paths=search.paths,
            labels=labels,
            cycles=search.cycles,
            limits=limits,
            truncation=search.truncation,
        )


def _resolve(reached: ReachedNode, principal: PrincipalRecord | None) -> ResolvedNode:
    """Label one reached key with its stored principal, falling back to the key itself."""
    host, sid = split_key(reached.key)
    return ResolvedNode(
        key=reached.key,
        sid=principal.sid if principal is not None else sid,
        host_key=principal.host_key if principal is not None else host,
        depth=reached.depth,
        path=reached.path,
        edge_kinds=reached.edge_kinds,
        via_foreign_security_principal=reached.via_foreign_security_principal,
        principal=principal,
        reported_kind=reached.reported_kind,
    )
