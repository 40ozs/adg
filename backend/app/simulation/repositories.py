r"""The two repositories the access engine takes by injection, reading through an overlay.

:class:`app.services.AccessService` is handed a :class:`app.repositories.MembershipRepository`
and a :class:`app.repositories.ResourceRepository`. Phase 7A used that seam to answer about a
past instant by substituting as-of subclasses; this phase uses the same seam to answer about
a *hypothetical* one. The engine is not modified, not parameterized, and not aware: the token
build, the DACL projection for an unread path, the deny-before-allow evaluation, the coverage
findings and the causal explanation all run exactly as they do for a live answer, because the
only thing that changed is what the rows say.

That is the whole reason simulation is built here rather than as a calculator of its own. A
second implementation of the access check — however small, however carefully written — would
disagree with the first one eventually, and on the day it did, the tool would be telling an
administrator that removing a group is safe on the authority of code that has never answered
a real question.

**Reading through the overlay is not writing.** Every method below either delegates to the
baseline repository and transforms the records in memory, or refuses. There is no statement
here, no session flush, and no path from an overlay to a collected-state table.

**Nothing is inherited.** Every public read of both base classes is overridden — with an
overlay-aware implementation where the simulated surface needs one, with plain delegation
where the overlay provably cannot affect the answer, and with a refusal everywhere else.
Phase 7A's as-of repositories let the remaining reads fall through to current state and
guarded the hazard with a naming test; that is tolerable when the fall-through returns
*today's* facts, and it is not tolerable here, where a fall-through would return facts from
the wrong **world**. ``tests/simulation/test_repository_coverage.py`` asserts the three sets
partition every public read, so a method added to a base repository fails the suite until
somebody says which of the three it is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final, NoReturn

from app.domain import Direction, GraphEdge, parse_unc_path
from app.repositories.membership import MAX_PAGE_SIZE as _MAX_PAGE_SIZE
from app.repositories.membership import (
    AliasRecord,
    MembershipRepository,
    Page,
    PrincipalRecord,
    PrincipalResolution,
)
from app.repositories.resources import (
    MAX_ACL_FETCH,
    NtfsAceRecord,
    NtfsAclRecomputation,
    NtfsBoundaryVerification,
    NtfsResourceRecord,
    ResourceRepository,
    ServerRecord,
    ShareAceRecord,
    ShareRecord,
    ShareReferenceRecord,
)
from app.simulation.application import (
    overlay_edges,
    overlay_ntfs_acl,
    overlay_resource,
    overlay_share_acl,
    resource_is_container,
)
from app.simulation.errors import SimulationUnsupportedRead
from app.simulation.overlay import ChangeKind, SimulationOverlay

__all__ = [
    "OverlayMembershipRepository",
    "OverlayResourceRepository",
    "simulated_repositories",
]

_REFUSAL: Final = (
    "{name} is not part of the simulated surface. An overlay repository answers only the "
    "reads an effective-access resolution makes; every other read would return the live "
    "estate's rows under a simulation's name, which is the one mistake this class exists to "
    "make impossible. Ask the baseline repository for it."
)


def _refuse(name: str) -> NoReturn:
    raise SimulationUnsupportedRead(_REFUSAL.format(name=name))


class OverlayMembershipRepository(MembershipRepository):
    """Membership as it would stand if the overlay's edges were real.

    Subclasses the base repository so that the engine's type contract is satisfied, and
    delegates every read to the baseline instance it wraps so that a simulation over a *past*
    baseline works: hand it an :class:`app.history.HistoricalMembershipRepository` and the
    proposal is evaluated against last Tuesday's graph.
    """

    def __init__(self, baseline: MembershipRepository, overlay: SimulationOverlay) -> None:
        super().__init__(baseline.session, baseline.edge_fetch_limit)
        self._baseline = baseline
        self._overlay = overlay

    @property
    def baseline(self) -> MembershipRepository:
        return self._baseline

    @property
    def overlay(self) -> SimulationOverlay:
        return self._overlay

    # ------------------------------------------------------------------- budget

    @property
    def edge_fetch_limit(self) -> int:
        return self._baseline.edge_fetch_limit

    @property
    def edges_fetched(self) -> int:
        """Rows the *baseline* read. Simulated edges are not rows and are not counted.

        The number is a report of what the request cost the database, and an invented edge
        cost it nothing. Counting one would make a simulation look more expensive than it is
        and, worse, would consume a traversal budget that exists to bound real reads.
        """
        return self._baseline.edges_fetched

    # ------------------------------------------------------ overlay-aware reads

    async def neighbors(
        self, direction: Direction, keys: Sequence[str]
    ) -> Mapping[str, Sequence[GraphEdge]]:
        """The adjacency the traversal walks, with the overlay's edges added and removed."""
        baseline = await self._baseline.neighbors(direction, keys)
        return overlay_edges(self._overlay, direction, keys, baseline)

    # ----------------------------------------------------------- plain delegation

    async def get_principal(self, principal_key: str) -> PrincipalRecord | None:
        """Unchanged: an overlay proposes memberships and ACEs, never principals.

        A change can name a SID no ``principals`` row describes — proposing to grant access
        to an account from a trusted forest, for instance — and the right answer for that is
        the one the live engine already gives: an unresolved principal, reported as
        unresolved. Inventing a record would make an account ADG has never seen render as one
        it has.
        """
        return await self._baseline.get_principal(principal_key)

    async def principals_by_keys(self, keys: Sequence[str]) -> dict[str, PrincipalRecord]:
        """Unchanged, for the same reason as :meth:`get_principal`."""
        return await self._baseline.principals_by_keys(keys)

    async def keys_with_members(self, keys: Sequence[str]) -> frozenset[str]:
        """Unchanged — and this one is a decision, not an omission.

        The question this read answers is *"has ADG ever enumerated the inside of this
        group?"*, and it is what separates ``the subject is not in it`` from ``nobody has
        looked``. A proposal to add one member is not an enumeration. Letting a simulated edge
        satisfy it would silently retire a ``TRUSTEE_MEMBERSHIP_UNOBSERVED`` finding — turning
        a coverage gap into a verdict on the strength of a hypothetical — which is exactly the
        failure the finding exists to prevent.
        """
        return await self._baseline.keys_with_members(keys)

    # ------------------------------------------------------------------ refused

    async def resolve(self, identifier: str, host_key: str | None = None) -> PrincipalResolution:
        _refuse("resolve")

    async def aliases_for(self, principal_key: str) -> tuple[AliasRecord, ...]:
        _refuse("aliases_for")

    async def is_known(self, key: str) -> bool:
        _refuse("is_known")

    async def direct_members(self, *args: Any, **kwargs: Any) -> Any:
        _refuse("direct_members")

    async def direct_groups(self, *args: Any, **kwargs: Any) -> Any:
        _refuse("direct_groups")

    async def count_direct(self, *args: Any, **kwargs: Any) -> int:
        _refuse("count_direct")


class OverlayResourceRepository(ResourceRepository):
    """Shares and directories as they would stand if the overlay's ACL changes were real."""

    def __init__(self, baseline: ResourceRepository, overlay: SimulationOverlay) -> None:
        super().__init__(baseline.session)
        self._baseline = baseline
        self._overlay = overlay

    @property
    def baseline(self) -> ResourceRepository:
        return self._baseline

    @property
    def overlay(self) -> SimulationOverlay:
        return self._overlay

    # ------------------------------------------------------ overlay-aware reads

    async def full_ntfs_acl(
        self, resource_key: str, *, limit: int = MAX_ACL_FETCH
    ) -> tuple[NtfsAceRecord, ...]:
        """One directory's DACL, with the overlay's entries applied and renumbered."""
        folded = resource_key.casefold()
        baseline = await self._baseline.full_ntfs_acl(folded, limit=limit)
        if not self._overlay.touches_resource(folded):
            return baseline
        parents = await self._parent_entries([folded], limit=limit)
        return overlay_ntfs_acl(
            self._overlay,
            folded,
            baseline,
            parent_entries=parents.get(folded),
            for_container=await self._is_container(folded),
        )

    async def ntfs_acls_for(
        self, keys: Sequence[str], *, limit: int = MAX_ACL_FETCH
    ) -> dict[str, tuple[NtfsAceRecord, ...]]:
        """Whole DACLs for several paths, each with the overlay applied.

        Still one statement for the page, plus at most one more for the parents of whichever
        directories the overlay un-protects. The N+1 the bulk read exists to avoid stays
        avoided: the overlay is applied in memory, per key, over rows already in hand.
        """
        folded = [key.casefold() for key in keys]
        baseline = await self._baseline.ntfs_acls_for(folded, limit=limit)
        touched = [key for key in folded if self._overlay.touches_resource(key)]
        if not touched:
            return baseline
        parents = await self._parent_entries(touched, limit=limit)
        containers = await self._containers(touched)
        result = dict(baseline)
        for key in touched:
            applied = overlay_ntfs_acl(
                self._overlay,
                key,
                baseline.get(key, ()),
                parent_entries=parents.get(key),
                for_container=containers.get(key, True),
            )
            if applied:
                result[key] = applied
            else:
                # An ACL the overlay emptied is absent rather than mapped to an empty tuple,
                # matching the base repository: a key with no entries means no ACL was
                # stored, and only the caller holding the resource row can tell that apart
                # from an ACL with nothing in it.
                result.pop(key, None)
        return result

    async def full_share_acl(
        self, share_key: str, *, limit: int = MAX_ACL_FETCH
    ) -> tuple[ShareAceRecord, ...]:
        """One share's ACL, with the overlay's entries applied."""
        folded = share_key.casefold()
        baseline = await self._baseline.full_share_acl(folded, limit=limit)
        if not self._overlay.touches_share(folded):
            return baseline
        return overlay_share_acl(self._overlay, folded, baseline)

    async def share_acls_for(
        self, keys: Sequence[str], *, limit: int = MAX_ACL_FETCH
    ) -> dict[str, tuple[ShareAceRecord, ...]]:
        folded = [key.casefold() for key in keys]
        baseline = await self._baseline.share_acls_for(folded, limit=limit)
        if not any(self._overlay.touches_share(key) for key in folded):
            return baseline
        result = dict(baseline)
        for key in folded:
            if not self._overlay.touches_share(key):
                continue
            applied = overlay_share_acl(self._overlay, key, baseline.get(key, ()))
            if applied:
                result[key] = applied
            else:
                result.pop(key, None)
        return result

    async def get_ntfs_resource(self, resource_key: str) -> NtfsResourceRecord | None:
        """The directory's own row, made consistent with the DACL the overlay produced.

        ``None`` stays ``None``. A proposal that edits the ACL of a path no run has described
        does not bring the path into existence: the descriptor facts an access check needs —
        whether a DACL is present at all, who owns it — are not things a proposal supplies,
        and inventing them would answer a question about a directory nobody has looked at.
        The caller reports the change as not applicable.
        """
        folded = resource_key.casefold()
        record = await self._baseline.get_ntfs_resource(folded)
        if record is None or not self._touches(folded):
            return record
        return overlay_resource(self._overlay, record, await self.full_ntfs_acl(folded))

    async def ntfs_resources_by_keys(self, keys: Sequence[str]) -> dict[str, NtfsResourceRecord]:
        folded = [key.casefold() for key in keys]
        rows = await self._baseline.ntfs_resources_by_keys(folded)
        touched = [key for key in folded if key in rows and self._touches(key)]
        if not touched:
            return rows
        acls = await self.ntfs_acls_for(touched)
        return {
            key: (
                overlay_resource(self._overlay, record, acls.get(key, ()))
                if key in touched
                else record
            )
            for key, record in rows.items()
        }

    async def resources_named_by(
        self,
        principal_keys: Sequence[str],
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> Page[str]:
        """Candidate paths, with the ones a simulated ACE newly names merged in.

        Without this, *"what would Alice be able to reach"* would list only the directories
        whose **collected** ACLs already name one of her token's trustees — so a proposal that
        grants her access to a new folder would show no change at all, which is the one answer
        a what-if must never give.

        Nothing is removed. A removal takes rights away but leaves the path a candidate, and
        the engine returns every candidate with its verdict, including the ones that now grant
        nothing — which is precisely the row an operator needs to see.
        """
        page = await self._baseline.resources_named_by(principal_keys, limit=limit, after=after)
        named = set(principal_keys)
        extra = [
            change.resource_key
            for change in self._overlay.ntfs_aces
            if change.kind is ChangeKind.ADD_NTFS_ACE and change.trustee_key in named
        ]
        return _merged_page(page, extra, limit=limit, after=after)

    async def shares_named_by(
        self,
        principal_keys: Sequence[str],
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> Page[str]:
        page = await self._baseline.shares_named_by(principal_keys, limit=limit, after=after)
        named = set(principal_keys)
        extra = [
            change.share_key
            for change in self._overlay.share_aces
            if change.kind is ChangeKind.ADD_SHARE_ACE and change.trustee_key in named
        ]
        return _merged_page(page, extra, limit=limit, after=after)

    # ----------------------------------------------------------- plain delegation

    async def get_share(self, share_key: str) -> ShareRecord | None:
        """Unchanged: an overlay changes a share's ACL, never the share itself.

        Nothing in the access check reads a descriptive field of a share — the ACL is fetched
        separately — so there is nothing here for an overlay to change.
        """
        return await self._baseline.get_share(share_key)

    async def shares_by_keys(self, keys: Sequence[str]) -> dict[str, ShareRecord]:
        """Unchanged, for the same reason as :meth:`get_share`."""
        return await self._baseline.shares_by_keys(keys)

    # ------------------------------------------------------------------ refused

    async def list_servers(self, *args: Any, **kwargs: Any) -> Any:
        _refuse("list_servers")

    async def get_server(self, server_key: str) -> ServerRecord | None:
        _refuse("get_server")

    async def count_servers(self) -> int:
        _refuse("count_servers")

    async def servers_by_keys(self, keys: Sequence[str]) -> dict[str, ServerRecord]:
        _refuse("servers_by_keys")

    async def list_shares(self, *args: Any, **kwargs: Any) -> Any:
        _refuse("list_shares")

    async def count_shares(self, server_key: str) -> int:
        _refuse("count_shares")

    async def has_aces(self, share_key: str) -> bool:
        _refuse("has_aces")

    async def share_acl(self, *args: Any, **kwargs: Any) -> Any:
        _refuse("share_acl")

    async def count_acl(self, share_key: str) -> int:
        _refuse("count_acl")

    async def get_share_root_resource(self, share_key: str) -> NtfsResourceRecord | None:
        _refuse("get_share_root_resource")

    async def has_ntfs_aces(self, resource_key: str) -> bool:
        _refuse("has_ntfs_aces")

    async def ntfs_acl(self, *args: Any, **kwargs: Any) -> Any:
        _refuse("ntfs_acl")

    async def count_ntfs_acl(self, resource_key: str) -> int:
        _refuse("count_ntfs_acl")

    async def recompute_acl_hash(self, resource: NtfsResourceRecord) -> NtfsAclRecomputation:
        _refuse("recompute_acl_hash")

    async def verify_boundary(self, resource: NtfsResourceRecord) -> NtfsBoundaryVerification:
        _refuse("verify_boundary")

    async def shares_referencing(self, *args: Any, **kwargs: Any) -> Page[ShareReferenceRecord]:
        _refuse("shares_referencing")

    async def count_shares_referencing(self, *args: Any, **kwargs: Any) -> int:
        _refuse("count_shares_referencing")

    async def reference_keys_for(self, principal_key: str) -> tuple[str, ...]:
        _refuse("reference_keys_for")

    # ------------------------------------------------------------------ helpers

    def _touches(self, resource_key: str) -> bool:
        return self._overlay.touches_resource(resource_key)

    async def _parent_entries(
        self, keys: Sequence[str], *, limit: int
    ) -> dict[str, tuple[NtfsAceRecord, ...]]:
        """The parent DACLs needed to clear protection, in one read for the whole batch.

        Only directories the overlay *un-protects* need one: every other change is computed
        from the directory's own entries. A path whose parent nobody read is simply absent
        from the result, and :func:`app.simulation.application.overlay_ntfs_acl` then flips
        the flag and projects nothing — the gap the caller reports rather than papers over.
        """
        wanted: dict[str, str] = {}
        for key in keys:
            change = self._overlay.inheritance_change_for(key)
            if change is None or change.protected:
                continue
            parent = parse_unc_path(key).parent
            if parent is not None:
                wanted[key] = parent.comparison_key
        if not wanted:
            return {}
        acls = await self._baseline.ntfs_acls_for(sorted(set(wanted.values())), limit=limit)
        return {
            key: entries for key, parent_key in wanted.items() if (entries := acls.get(parent_key))
        }

    async def _containers(self, keys: Sequence[str]) -> dict[str, bool]:
        rows = await self._baseline.ntfs_resources_by_keys(list(keys))
        return {key: resource_is_container(rows.get(key)) for key in keys}

    async def _is_container(self, key: str) -> bool:
        return resource_is_container(await self._baseline.get_ntfs_resource(key))


def _merged_page(
    page: Page[str], extra: Sequence[str], *, limit: int, after: str | None
) -> Page[str]:
    """One keyset page with extra candidate keys merged into it.

    Keyset paging survives the merge because the cursor is recomputed from the **merged**
    page: a candidate that an injected key pushes off the end sorts after the new cursor and
    is therefore returned by the next request rather than lost. Injected keys at or before the
    cursor are dropped, for the same reason the underlying query has a ``> after`` predicate —
    the caller has already been given everything up to there.
    """
    candidates = {key for key in extra if after is None or key > after}
    if not candidates:
        return page
    page_size = max(1, min(limit, _MAX_PAGE_SIZE))
    merged = sorted(set(page.items) | candidates)
    has_more = page.has_more or len(merged) > page_size
    visible = tuple(merged[:page_size])
    return Page(
        items=visible,
        has_more=has_more,
        next_key=visible[-1] if has_more and visible else None,
    )


def simulated_repositories(
    resources: ResourceRepository,
    membership: MembershipRepository,
    overlay: SimulationOverlay,
) -> tuple[OverlayResourceRepository, OverlayMembershipRepository]:
    """Both overlay repositories over one baseline pair, in the order ``AccessService`` takes.

    A pair, and always the same overlay for both: a simulation whose two repositories read
    different proposals would answer about a world that was never proposed, and returning them
    together is the cheapest way to make that unconstructible.
    """
    return (
        OverlayResourceRepository(resources, overlay),
        OverlayMembershipRepository(membership, overlay),
    )
