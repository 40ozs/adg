r"""Running a what-if: derive the pairs, resolve both worlds, compare.

Everything expensive is delegated. The access check is
:class:`app.services.AccessService`, unmodified and used twice — once over the baseline
repositories and once over the overlay repositories that wrap them — so a simulated answer and
a live answer are produced by the same code reading differently-shaped rows. The membership
inversion that makes *"who can reach this"* bounded is the service's own; the causal paths are
the engine's own; the certainty is the engine's own. What this module adds is the question,
the bounds, and the comparison.

**Resource-centric, not principal-centric.** The naive shape of a what-if is "for every
affected principal, against every affected resource, resolve twice", and it is quadratic in a
way no bound rescues: each pair costs a membership traversal. The bounded shape inverts it, as
:meth:`app.services.AccessService.effective_principals` already does — for each affected
*resource*, expand the ACL's trustees downward once and invert the map — so one resource costs
two enumerations rather than two traversals per principal. Only the principals that appear on
one side and not the other need an individual resolution, and those are precisely the ones
whose access the proposal changed.

**Applicability is decided before anything is simulated.** A change naming an ACE that is no
longer there, a share whose ACL nobody has read, or a directory ADG has never seen cannot be
applied, and the overlay handed to the repositories is narrowed to the changes that can be.
So the report's impact list and its list of applied changes describe the same world, and a
proposal that turns out to be a no-op says which of its changes were no-ops and why — rather
than reporting "no impact", which reads as "safe".

**Nothing here writes.** Persisting a simulation is :mod:`app.simulation.store`'s job and it
is a separate call; a report can be computed, read and thrown away without a row being
written anywhere.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Sequence
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.access_engine import EffectiveAccess
from app.domain import (
    DEFAULT_LIMITS,
    CollectionBasis,
    Direction,
    DomainValidationError,
    TraversalLimits,
    parse_share_identifier,
    parse_unc_path,
)
from app.history.repository import HistoricalMembershipRepository, HistoricalResourceRepository
from app.repositories import CollectionBasisRepository, MembershipRepository, ResourceRepository
from app.repositories.resources import NtfsResourceRecord
from app.services.access import AccessService, PrincipalAccess, ResourceAccessPage
from app.services.graph import GraphService, MemberInclusion
from app.simulation.impact import compare_access, deltas_by_severity
from app.simulation.model import (
    DEFAULT_BOUNDS,
    AccessDelta,
    BaselineKind,
    ChangeApplication,
    ChangeOutcome,
    ScopeKind,
    SimulationBaseline,
    SimulationBounds,
    SimulationCost,
    SimulationReport,
    SimulationScope,
    SimulationTruncation,
    ordered_truncation,
    summarize,
)
from app.simulation.overlay import (
    ChangeKind,
    InheritanceChange,
    MembershipChange,
    NtfsAceChange,
    ShareAceChange,
    SimulationOverlay,
)
from app.simulation.repositories import simulated_repositories

__all__ = ["INHERITABLE_FLAGS", "SimulationService"]

INHERITABLE_FLAGS: Final = 0x03
"""``OBJECT_INHERIT | CONTAINER_INHERIT``: the two bits that make an entry flow downward.

Named rather than written inline because it is the test for whether a proposal reaches past
the directory it names, and getting it wrong means a report that silently omits a subtree.
"""


class SimulationService:
    """Non-destructive what-if evaluation over one database session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._basis = CollectionBasisRepository(session)

    # ---------------------------------------------------------------- baseline

    async def current_basis(self) -> CollectionBasis:
        """The identity of everything ADG has collected, right now."""
        return await self._basis.current()

    async def baseline(self, *, at: dt.datetime | None = None) -> SimulationBaseline:
        """Capture the state a simulation will be measured against.

        Captured explicitly, and before anything is resolved, so that the token on a report is
        the token of the state the report was computed from. Reading it afterwards would leave
        a window in which a scan lands and the report names a baseline it never saw.
        """
        basis = await self._basis.current()
        return SimulationBaseline(
            kind=BaselineKind.CURRENT if at is None else BaselineKind.AS_OF,
            basis=basis,
            captured_at=dt.datetime.now(dt.UTC),
            at=at,
        )

    async def is_stale(self, baseline: SimulationBaseline) -> bool:
        """Whether a collector has written anything since this baseline was captured."""
        return baseline.is_stale_against(await self._basis.current())

    # --------------------------------------------------------------------- run

    async def run(
        self,
        overlay: SimulationOverlay,
        *,
        scope: SimulationScope | None = None,
        bounds: SimulationBounds = DEFAULT_BOUNDS,
        baseline: SimulationBaseline | None = None,
        limits: TraversalLimits = DEFAULT_LIMITS,
    ) -> SimulationReport:
        """Evaluate one proposal and report what it would change.

        Args:
            overlay: the proposal. Never modified, and never written anywhere.
            scope: which pairs to evaluate. Defaults to the affected scope, which derives
                them from the overlay.
            bounds: strict size and time limits. Exhausting one truncates the report and is
                reported; it is not an error.
            baseline: a baseline captured earlier, for re-running a stored simulation against
                the state it was written for. Captured fresh when omitted.
            limits: the membership traversal limits, passed through to the engine unchanged so
                a simulated traversal truncates exactly where a live one would.
        """
        measured = baseline or await self.baseline()
        base_resources, base_membership = self._baseline_repositories(measured)
        return await self.run_against(
            overlay,
            base_resources,
            base_membership,
            baseline=measured,
            scope=scope,
            bounds=bounds,
            limits=limits,
        )

    async def run_against(
        self,
        overlay: SimulationOverlay,
        resources: ResourceRepository,
        membership: MembershipRepository,
        *,
        baseline: SimulationBaseline,
        scope: SimulationScope | None = None,
        bounds: SimulationBounds = DEFAULT_BOUNDS,
        limits: TraversalLimits = DEFAULT_LIMITS,
    ) -> SimulationReport:
        """The same evaluation, against a repository pair the caller already holds.

        :meth:`run` builds that pair from the baseline it captured; this is the form for a
        caller that has one — a request that has already constructed its repositories, or a
        future endpoint that wants the point-in-time pair without going back through
        :class:`app.simulation.model.BaselineKind`. The ``baseline`` argument is still
        required, because a report that could not name the state it was computed against
        would be the one thing this phase's acceptance criteria forbid.

        The pair is used for **both** sides: the overlay repositories wrap these, so the
        before and after answers differ by the proposal and by nothing else — not by a
        second connection, a second traversal budget, or a second moment in time.
        """
        question = scope or SimulationScope()
        started = time.perf_counter()

        base_resources, base_membership = resources, membership
        base_access = AccessService(base_resources, base_membership)

        applications = await self._applicability(overlay, base_resources, base_membership)
        applied = SimulationOverlay.from_changes(
            item.change for item in applications if item.applied
        )
        sim_resources, sim_membership = simulated_repositories(
            base_resources, base_membership, applied
        )
        sim_access = AccessService(sim_resources, sim_membership)

        run = _Run(
            overlay=applied,
            scope=question,
            bounds=bounds,
            limits=limits,
            baseline_access=base_access,
            simulated_access=sim_access,
            baseline_repositories=(base_resources, base_membership),
            started=started,
        )
        deltas = await run.evaluate()

        elapsed = int((time.perf_counter() - started) * 1000)
        return SimulationReport(
            overlay=overlay,
            baseline=baseline,
            scope=question,
            bounds=bounds,
            applications=applications,
            deltas=deltas,
            summary=summarize(deltas),
            cost=SimulationCost(
                pairs_evaluated=len(deltas),
                resolutions=run.resolutions,
                explanations=run.explanations,
                edges_read=base_membership.edges_fetched,
                elapsed_ms=elapsed,
            ),
            truncation=ordered_truncation(run.truncation),
        )

    # ------------------------------------------------------------- repositories

    def _baseline_repositories(
        self, baseline: SimulationBaseline
    ) -> tuple[ResourceRepository, MembershipRepository]:
        """The pair the overlay wraps: live repositories, or Phase 7A's as-of subclasses.

        One instance of each for the whole simulation, deliberately. The membership repository
        carries the edge-fetch budget, and sharing it means a simulation has *one* budget
        rather than one per resource — so a proposal that would read a million edge rows
        truncates and says so instead of reading a million rows a page at a time.
        """
        if baseline.kind is BaselineKind.CURRENT:
            return ResourceRepository(self._session), MembershipRepository(self._session)
        if baseline.at is None:  # pragma: no cover - the baseline validates this pairing
            raise DomainValidationError("An as-of baseline needs an instant.", field="at")
        return (
            HistoricalResourceRepository(self._session, baseline.at),
            HistoricalMembershipRepository(self._session, baseline.at),
        )

    # ------------------------------------------------------------ applicability

    async def _applicability(
        self,
        overlay: SimulationOverlay,
        resources: ResourceRepository,
        membership: MembershipRepository,
    ) -> tuple[ChangeApplication, ...]:
        """What each proposed change would actually do to the baseline, before anything runs.

        Every check here is a read of the object the change names. That is one extra query per
        distinct object in the overlay — bounded by :data:`app.simulation.MAX_CHANGES` — and it
        buys the difference between *"this proposal changes nothing"* and *"this proposal no
        longer applies"*, which are the same empty impact list and completely different advice.
        """
        results: list[ChangeApplication] = []
        for change in overlay.changes:
            if isinstance(change, MembershipChange):
                results.append(await self._membership_applicability(change, membership))
            elif isinstance(change, NtfsAceChange):
                results.append(await self._ntfs_applicability(change, resources))
            elif isinstance(change, ShareAceChange):
                results.append(await self._share_applicability(change, resources))
            else:
                results.append(await self._inheritance_applicability(change, resources))
        return tuple(results)

    @staticmethod
    async def _membership_applicability(
        change: MembershipChange, membership: MembershipRepository
    ) -> ChangeApplication:
        adjacency = await membership.neighbors(Direction.DOWN, [change.group_key])
        present = any(
            (edge.group_key, edge.member_key, edge.kind)
            == (change.group_key, change.member_key, change.edge_kind)
            for edge in adjacency.get(change.group_key, ())
        )
        if change.kind is ChangeKind.ADD_MEMBER:
            outcome = ChangeOutcome.ALREADY_PRESENT if present else ChangeOutcome.APPLIED
        else:
            outcome = ChangeOutcome.APPLIED if present else ChangeOutcome.TARGET_NOT_FOUND
        return ChangeApplication(
            change=change,
            outcome=outcome,
            detail={"group_key": change.group_key, "member_key": change.member_key},
        )

    @staticmethod
    async def _ntfs_applicability(
        change: NtfsAceChange, resources: ResourceRepository
    ) -> ChangeApplication:
        record = await resources.get_ntfs_resource(change.resource_key)
        if record is None:
            return ChangeApplication(
                change=change,
                outcome=ChangeOutcome.TARGET_NOT_OBSERVED,
                detail={"resource_key": change.resource_key},
            )
        if not record.dacl_present:
            return ChangeApplication(
                change=change,
                outcome=ChangeOutcome.NOT_REPRESENTABLE,
                detail={
                    "resource_key": change.resource_key,
                    "reason": (
                        "The directory has a NULL DACL, which grants everyone full access and "
                        "holds no entries. Editing an entry on it is not a change Windows "
                        "would make; replacing the descriptor is, and that is not what this "
                        "proposal says."
                    ),
                },
            )
        entries = await resources.full_ntfs_acl(change.resource_key)
        if change.kind is ChangeKind.ADD_NTFS_ACE:
            duplicate = any(
                entry.trustee_key == change.trustee_key
                and entry.ace_type is change.ace_type
                and entry.access_mask == change.access_mask
                and entry.ace_flags == (change.ace_flags or 0)
                for entry in entries
            )
            outcome = ChangeOutcome.ALREADY_PRESENT if duplicate else ChangeOutcome.APPLIED
        else:
            found = any(entry.ace_key == change.ace_key for entry in entries)
            outcome = ChangeOutcome.APPLIED if found else ChangeOutcome.TARGET_NOT_FOUND
        return ChangeApplication(
            change=change, outcome=outcome, detail={"resource_key": change.resource_key}
        )

    @staticmethod
    async def _share_applicability(
        change: ShareAceChange, resources: ResourceRepository
    ) -> ChangeApplication:
        entries = await resources.full_share_acl(change.share_key)
        if not entries:
            # Zero stored entries means nobody has read this share's ACL. Windows shares
            # always carry one, so simulating against an empty one would replace "never
            # looked" with "grants exactly this", in the direction that hides access.
            return ChangeApplication(
                change=change,
                outcome=ChangeOutcome.TARGET_NOT_OBSERVED,
                detail={"share_key": change.share_key},
            )
        if change.kind is ChangeKind.ADD_SHARE_ACE:
            duplicate = any(
                entry.trustee_key == change.trustee_key
                and entry.ace_type is change.ace_type
                and entry.access_mask == change.access_mask
                and entry.permission is change.permission
                for entry in entries
            )
            outcome = ChangeOutcome.ALREADY_PRESENT if duplicate else ChangeOutcome.APPLIED
        else:
            found = any(entry.ace_key == change.ace_key for entry in entries)
            outcome = ChangeOutcome.APPLIED if found else ChangeOutcome.TARGET_NOT_FOUND
        return ChangeApplication(
            change=change, outcome=outcome, detail={"share_key": change.share_key}
        )

    @staticmethod
    async def _inheritance_applicability(
        change: InheritanceChange, resources: ResourceRepository
    ) -> ChangeApplication:
        record = await resources.get_ntfs_resource(change.resource_key)
        if record is None:
            return ChangeApplication(
                change=change,
                outcome=ChangeOutcome.TARGET_NOT_OBSERVED,
                detail={"resource_key": change.resource_key},
            )
        if record.dacl_protected == change.protected:
            return ChangeApplication(
                change=change,
                outcome=ChangeOutcome.ALREADY_PRESENT,
                detail={"resource_key": change.resource_key},
            )
        if not change.protected:
            parent = parse_unc_path(change.resource_key).parent
            parent_entries = (
                () if parent is None else await resources.full_ntfs_acl(parent.comparison_key)
            )
            if not parent_entries:
                return ChangeApplication(
                    change=change,
                    outcome=ChangeOutcome.PARENT_NOT_OBSERVED,
                    detail={
                        "resource_key": change.resource_key,
                        "parent_key": "" if parent is None else parent.comparison_key,
                    },
                )
        return ChangeApplication(
            change=change,
            outcome=ChangeOutcome.APPLIED,
            detail={"resource_key": change.resource_key},
        )


class _Run:
    """One evaluation, holding the budgets it spends and the reasons it stopped.

    A separate object rather than a pile of locals because the budgets are consulted from four
    places and the truncation set is written from six; threading them through as arguments
    would make every helper's signature a list of counters, and the first one that forgot to
    return an updated count would silently exceed a bound.
    """

    def __init__(
        self,
        *,
        overlay: SimulationOverlay,
        scope: SimulationScope,
        bounds: SimulationBounds,
        limits: TraversalLimits,
        baseline_access: AccessService,
        simulated_access: AccessService,
        baseline_repositories: tuple[ResourceRepository, MembershipRepository],
        started: float,
    ) -> None:
        self._overlay = overlay
        self._scope = scope
        self._bounds = bounds
        self._limits = limits
        self._before = baseline_access
        self._after = simulated_access
        self._resources, self._membership = baseline_repositories
        self._started = started
        self.truncation: set[SimulationTruncation] = set()
        self.resolutions = 0
        self.explanations = 0

    # ------------------------------------------------------------------ budget

    @property
    def _out_of_time(self) -> bool:
        return (time.perf_counter() - self._started) * 1000 >= self._bounds.time_budget_ms

    def _explanations_left(self) -> bool:
        return self.explanations < self._bounds.max_explanations

    # ------------------------------------------------------------------- entry

    async def evaluate(self) -> tuple[AccessDelta, ...]:
        if self._scope.kind is ScopeKind.PAIR:
            return await self._pair_scope()
        if self._scope.kind is ScopeKind.RESOURCE:
            return await self._resource_scope()
        if self._scope.kind is ScopeKind.SUBJECT:
            return await self._subject_scope()
        return await self._affected_scope()

    # -------------------------------------------------------------- pair scope

    async def _pair_scope(self) -> tuple[AccessDelta, ...]:
        subject = self._scope.subject_key or ""
        resource = self._scope.resource_key or ""
        before = await self._resolve(self._before, subject, resource)
        explanation = None
        if self._overlay.has_removals and self._explanations_left():
            explained = await self._after.explain_access(
                subject, resource, path=self._scope.path, limits=self._limits
            )
            self.resolutions += 1
            self.explanations += 1
            after = explained.access
            explanation = explained.explanation
        else:
            after = await self._resolve(self._after, subject, resource)
        return (compare_access(subject, before, after, explanation=explanation),)

    # ---------------------------------------------------------- resource scope

    async def _resource_scope(self) -> tuple[AccessDelta, ...]:
        resource = self._scope.resource_key or ""
        return deltas_by_severity(await self._deltas_for_resource(resource))

    # ----------------------------------------------------------- subject scope

    async def _subject_scope(self) -> tuple[AccessDelta, ...]:
        subject = self._scope.subject_key or ""
        before_page = await self._before.accessible_resources(
            subject,
            path=self._scope.path,
            limits=self._limits,
            limit=self._scope.limit,
            after=self._scope.after,
        )
        after_page = await self._after.accessible_resources(
            subject,
            path=self._scope.path,
            limits=self._limits,
            limit=self._scope.limit,
            after=self._scope.after,
        )
        self.resolutions += len(before_page.items) + len(after_page.items)

        before_by_key = {item.access.resource_key: item.access for item in before_page.items}
        after_by_key = {item.access.resource_key: item.access for item in after_page.items}
        cursor = after_page.next_key
        keys = sorted(set(before_by_key) | set(after_by_key))
        if cursor is not None:
            # A key beyond the cursor would be returned again by the next request. Dropping it
            # here keeps the page a partition rather than an overlapping window.
            keys = [key for key in keys if key <= cursor]
        if after_page.has_more or before_page.has_more:
            self.truncation.add(SimulationTruncation.RESOURCE_BUDGET)

        deltas: list[AccessDelta] = []
        for key in keys:
            if self._out_of_time:
                self.truncation.add(SimulationTruncation.TIME_BUDGET)
                break
            if len(deltas) >= self._bounds.max_pairs:
                self.truncation.add(SimulationTruncation.PAIR_BUDGET)
                break
            before = before_by_key.get(key) or await self._resolve(self._before, subject, key)
            after = after_by_key.get(key) or await self._resolve(self._after, subject, key)
            deltas.append(await self._delta(subject, before, after))
        return deltas_by_severity(deltas)

    # ---------------------------------------------------------- affected scope

    async def _affected_scope(self) -> tuple[AccessDelta, ...]:
        resources = await self._affected_resources()
        deltas: list[AccessDelta] = []
        for resource in resources:
            if self._out_of_time:
                self.truncation.add(SimulationTruncation.TIME_BUDGET)
                break
            if len(deltas) >= self._bounds.max_pairs:
                self.truncation.add(SimulationTruncation.PAIR_BUDGET)
                break
            deltas.extend(await self._deltas_for_resource(resource))
        if len(deltas) > self._bounds.max_pairs:
            self.truncation.add(SimulationTruncation.PAIR_BUDGET)
            deltas = deltas[: self._bounds.max_pairs]
        return deltas_by_severity(deltas)

    async def _affected_resources(self) -> tuple[str, ...]:
        """Every directory the proposal could change access to, bounded and ordered.

        Three sources, and the third is the one the phase's requirement names:

        * directories whose own DACL or inheritance the overlay edits;
        * the root of every share whose ACL it edits — a share ACL applies to the whole share,
          and evaluating its root is the bounded stand-in for that, with
          ``DESCENDANTS_NOT_EVALUATED`` reported so the stand-in is not read as the whole;
        * **for a membership change, the resources the affected groups are named on.** A
          proposal to put somebody in ``Finance-RW`` changes their access to everything that
          group reaches, which is not derivable from the proposal's text and is exactly what
          the reference index answers. The group's own upward closure is included, because
          being in a group is also being in everything that contains it.
        """
        found: set[str] = set(self._overlay.resource_keys)
        for share_key in self._overlay.share_keys:
            found.add(parse_share_identifier(share_key).unc_path.comparison_key)
            self.truncation.add(SimulationTruncation.DESCENDANTS_NOT_EVALUATED)
        if self._inheritance_or_inheritable_edit():
            self.truncation.add(SimulationTruncation.DESCENDANTS_NOT_EVALUATED)

        if self._overlay.membership:
            found |= await self._resources_reached_by_changed_groups()

        ordered = sorted(found)
        if len(ordered) > self._bounds.max_resources:
            self.truncation.add(SimulationTruncation.RESOURCE_BUDGET)
        return tuple(ordered[: self._bounds.max_resources])

    def _inheritance_or_inheritable_edit(self) -> bool:
        """Whether the proposal changes something that flows down to child directories."""
        if self._overlay.inheritance:
            return True
        return any(
            change.ace_flags is not None and change.ace_flags & INHERITABLE_FLAGS
            for change in self._overlay.ntfs_aces
        )

    async def _resources_reached_by_changed_groups(self) -> set[str]:
        """Paths and share roots named by the groups whose membership changed, plus their own
        groups.

        One upward traversal per changed group, then one keyset page of the reference index.
        Both are bounded reads the engine already makes for a live answer; nothing here scans.
        """
        graph = GraphService(self._membership)
        keys: set[str] = set()
        for group_key in sorted(self._overlay.group_keys):
            keys.add(group_key)
            expansion = await graph.effective_groups(group_key, self._limits)
            keys.update(node.key for node in expansion.nodes)
            if not expansion.complete:
                self.truncation.add(SimulationTruncation.TRUSTEE_EXPANSION_INCOMPLETE)

        named = sorted(keys)
        page = await self._resources.resources_named_by(named, limit=self._bounds.max_resources)
        found = set(page.items)
        if page.has_more:
            self.truncation.add(SimulationTruncation.RESOURCE_BUDGET)

        shares = await self._resources.shares_named_by(named, limit=self._bounds.max_resources)
        for share_key in shares.items:
            found.add(parse_share_identifier(share_key).unc_path.comparison_key)
        if shares.has_more:
            self.truncation.add(SimulationTruncation.RESOURCE_BUDGET)
        return found

    # ------------------------------------------------------- one resource, both

    async def _deltas_for_resource(self, resource_key: str) -> list[AccessDelta]:
        """Everyone whose access to one directory the proposal changes.

        Both sides are enumerated by :meth:`AccessService.effective_principals`, which expands
        each ACL trustee downward once rather than evaluating every principal. A principal on
        one list and not the other is resolved individually — that is the *gain* or *loss*
        case, and it is the only case that costs a traversal.
        """
        before_page = await self._before.effective_principals(
            resource_key,
            path=self._scope.path,
            limits=self._limits,
            inclusion=MemberInclusion.NON_GROUPS,
            limit=self._bounds.max_principals,
        )
        after_page = await self._after.effective_principals(
            resource_key,
            path=self._scope.path,
            limits=self._limits,
            inclusion=MemberInclusion.NON_GROUPS,
            limit=self._bounds.max_principals,
        )
        self.resolutions += len(before_page.items) + len(after_page.items)
        self._note_enumeration(before_page)
        self._note_enumeration(after_page)

        before_by_key = _by_key(before_page.items)
        after_by_key = _by_key(after_page.items)
        deltas: list[AccessDelta] = []
        for key in sorted(set(before_by_key) | set(after_by_key)):
            if self._out_of_time:
                self.truncation.add(SimulationTruncation.TIME_BUDGET)
                break
            before = before_by_key.get(key) or await self._resolve(self._before, key, resource_key)
            after = after_by_key.get(key) or await self._resolve(self._after, key, resource_key)
            deltas.append(await self._delta(key, before, after, resource=after_page.resource))
        return deltas

    def _note_enumeration(self, page: ResourceAccessPage) -> None:
        if page.unenumerable_trustees or page.truncated_trustees:
            self.truncation.add(SimulationTruncation.TRUSTEE_EXPANSION_INCOMPLETE)
        if page.has_more:
            self.truncation.add(SimulationTruncation.PRINCIPAL_BUDGET)

    # ----------------------------------------------------------------- one pair

    async def _delta(
        self,
        subject_key: str,
        before: EffectiveAccess,
        after: EffectiveAccess,
        *,
        resource: NtfsResourceRecord | None = None,
    ) -> AccessDelta:
        """One comparison, with alternate-path analysis where a loss is being claimed.

        The analysis is run only when the proposal removes something *and* rights actually
        went down: an addition cannot produce a false claim of loss, and a pair whose rights
        did not move has no claim to check. Each analysis costs one more resolution, which is
        why it is budgeted and why exhausting the budget is reported rather than hidden.
        """
        explanation = None
        loses = not before.rights.expand_generics().issubset(after.rights.expand_generics())
        if loses and self._overlay.has_removals and after.has_access:
            if self._explanations_left():
                explained = await self._after.explain_access(
                    subject_key, after.resource_key, path=after.path, limits=self._limits
                )
                self.resolutions += 1
                self.explanations += 1
                explanation = explained.explanation
            else:
                self.truncation.add(SimulationTruncation.EXPLANATION_BUDGET)
        return compare_access(
            subject_key, before, after, explanation=explanation, resource=resource
        )

    async def _resolve(
        self, service: AccessService, subject_key: str, resource_key: str
    ) -> EffectiveAccess:
        self.resolutions += 1
        resolved = await service.effective_access(
            subject_key, resource_key, path=self._scope.path, limits=self._limits
        )
        return resolved.access


def _by_key(items: Sequence[PrincipalAccess]) -> dict[str, EffectiveAccess]:
    return {item.key: item.access for item in items}
