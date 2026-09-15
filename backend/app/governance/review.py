r"""Everything a reviewer needs beside an item, assembled from answers ADG already gives.

Phase 10A froze the grant. This module answers the questions a person actually has in front
of it, and its whole design principle is that it **derives nothing new**. Each part is an
existing ADG answer, asked about this item:

* *Is it still like this?* — :mod:`app.governance.drift`, over the same baseline reads
  generation used.
* *What does this grant actually let them do?* — the effective-access engine, run twice:
  once over the campaign's baseline instant (:meth:`app.history.service.HistoryService.
  effective_access_at`) and once over the present.
* *Why do they have it, and would removing this entry stop it?* — the Phase 5 causal
  explanation, whose :attr:`app.access_engine.causality.CausalPath.via_group` already splits
  direct from group-derived, and whose removal analysis already says what a revocation would
  and would not take away.
* *Is anything known to be wrong here?* — the Phase 8 risk findings whose subject names this
  target or this principal.
* *When did this last move?* — the Phase 7 change feed for this item's own entries.

Re-deriving any of those here would be a second implementation of a rule, and the two would
disagree on the day it mattered — the argument ``docs/contracts/derived-responses.md`` makes
for clients and which applies just as much inside the application.

**Three honesty rules shape what is returned.**

*The decision is about the frozen evidence.* Everything here is context; none of it is what
the reviewer is certifying. The item is never edited, and a screen built from this must show
the frozen grant as the subject and the current state beside it.

*Group-derived access is reported because revocation is usually not enough.* An item is a
direct entry naming the principal. If the same principal also reaches the resource through a
group, removing the entry changes nothing — and a reviewer who revokes it in the belief that
access ends has been misled by the screen. :attr:`Reach.removing_reviewed_entries_leaves_access`
is the single most important field here for exactly that reason.

*An answer ADG cannot give says so.* Every part carries an availability flag and a reason
rather than an empty list: "no risk findings" and "the risk engine has never run" are
different facts, and a reviewer reading the second as the first draws the wrong conclusion.

Nothing in this module writes anything. It reads governance tables, the timeline, the
current-state tables and ``risk_findings``, and returns values.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_engine import EffectiveAccess, RightsMask
from app.access_engine.causality import (
    AccessExplanation,
    CausalPath,
    PathEffect,
    PathRelation,
)
from app.changes.model import ObjectChange
from app.changes.service import ChangeService
from app.contracts.v1.common import ObservationKind
from app.domain import ReviewTargetKind
from app.governance.drift import ItemDrift, compare_grants
from app.governance.model import ReviewCampaign, ReviewItem
from app.governance.repository import GovernanceRepository
from app.history.model import Certainty
from app.history.service import HistoryService
from app.models.schema import RiskFindingStatus, risk_findings
from app.repositories.membership import MembershipRepository, PrincipalRecord
from app.repositories.resources import ResourceRepository
from app.services.access import AccessService

__all__ = [
    "MAX_CONTEXT_FINDINGS",
    "MAX_CONTEXT_ROUTES",
    "MAX_DRIFT_ITEMS",
    "AccessAt",
    "EntryRemoval",
    "GroupRoute",
    "LastChange",
    "Reach",
    "RelatedFinding",
    "ReviewContext",
    "ReviewContextService",
]

MAX_DRIFT_ITEMS: Final = 500
"""Items one drift report compares. A page, not the campaign: drift is computed on read and
re-reading five thousand targets to render a summary is a cost nobody asked for. The report
says how much of the campaign it covered rather than implying it covered all of it."""

MAX_CONTEXT_ROUTES: Final = 25
"""Group routes listed on one item. Past this the answer is "through a lot of groups", which
is itself the finding, and a reviewer is not helped by the twenty-sixth."""

MAX_CONTEXT_FINDINGS: Final = 20
"""Risk findings listed beside one item, strongest first."""

MAX_CONTEXT_CHANGES: Final = 10
"""Recent changes listed per item, newest first, across all of its entries."""


@dataclass(frozen=True, slots=True)
class AccessAt:
    """What the principal could do to the target at one instant, or why ADG cannot say."""

    at: dt.datetime
    available: bool
    unavailable_reason: str | None
    access: EffectiveAccess | None
    certainty: Certainty | None
    """The weakest certainty of any version the resolution read. ``None`` for the present,
    where the question is about current state rather than about a reconstruction."""

    @property
    def rights(self) -> RightsMask | None:
        return None if self.access is None else self.access.rights


@dataclass(frozen=True, slots=True)
class GroupRoute:
    """One group through which this principal also reaches the target.

    Recorded per group rather than per path: "Alice is in Finance-RW, which is named on the
    folder" is the sentence a resource owner acts on, and three paths through the same group
    are one fact about one membership.
    """

    principal_key: str
    display_name: str | None
    depth: int
    """How many membership edges lie between the reviewed principal and this group."""

    chain: tuple[str, ...]
    rights: RightsMask
    layer: str
    inherited: bool
    """Whether the entry this route lands on is inherited from an ancestor — in which case
    it cannot be removed at this target either."""


@dataclass(frozen=True, slots=True)
class EntryRemoval:
    """What removing one of the item's own entries would do, evaluated one at a time.

    **One at a time** is a real limitation and is stated rather than papered over: the
    explanation engine answers "what if this edge were gone", so an item carrying an allow
    and a deny reports two independent answers and neither describes removing both. For the
    common case — one entry — it is exact.
    """

    ace_key: str
    rights_removed: RightsMask
    rights_after: RightsMask
    revokes_all_access: bool
    changes_nothing: bool
    alternate_paths: int
    """How many other paths would still reach the target. Non-zero with
    ``revokes_all_access`` false is the case a reviewer most needs to see."""


@dataclass(frozen=True, slots=True)
class Reach:
    """How the principal reaches the target, split the way a revoke decision needs."""

    available: bool
    unavailable_reason: str | None
    direct_paths: int
    """Paths landing on an entry that names the reviewed principal itself."""

    group_paths: int
    """Paths landing on an entry that names a group the principal is in."""

    routes: tuple[GroupRoute, ...]
    removals: tuple[EntryRemoval, ...]
    removing_reviewed_entries_leaves_access: bool | None
    """``True`` when removing the reviewed entries individually would leave the principal
    with access anyway — through a group, or through another entry. The field this whole
    module exists for: a revoke decision recorded in the belief that access ends, on a grant
    that is also held through a group, is an attestation that says something untrue.

    ``None`` when no explanation could be produced, never ``False`` by default: "removing it
    works" is the reassuring answer and must be measured, not assumed."""

    truncated: bool
    labels: Mapping[str, PrincipalRecord] = field(default_factory=dict)
    explanation: AccessExplanation | None = None


@dataclass(frozen=True, slots=True)
class RelatedFinding:
    """One risk finding whose subject names this item's target, principal, or both."""

    finding_key: str
    rule_id: str
    status: str
    severity: str
    band: str
    confidence: str
    detected_at: dt.datetime
    first_detected_at: dt.datetime
    relation: str
    """``access`` when the finding names this principal *and* this target — the tightest
    match and the one shown first; ``target`` or ``principal`` when it names one of them."""

    resource_key: str | None
    share_key: str | None
    principal_key: str | None
    detail: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class LastChange:
    """One recent transition of one of the item's entries, as the change feed classified it."""

    kind: str
    key: str
    action: str
    significance: str
    severity: str
    direction: str
    reasons: tuple[str, ...]
    changed_after: dt.datetime | None
    changed_at_or_before: dt.datetime | None
    is_exact: bool


@dataclass(frozen=True, slots=True)
class ReviewContext:
    """One item with everything around it. Assembled on read; nothing here is stored."""

    item: ReviewItem
    campaign: ReviewCampaign
    drift: ItemDrift
    baseline_access: AccessAt
    current_access: AccessAt
    reach: Reach
    findings: tuple[RelatedFinding, ...]
    findings_truncated: bool
    changes: tuple[LastChange, ...]
    changes_truncated: bool
    resolved_resource_key: str | None
    """The NTFS resource the effective-access answers were computed against. For a
    ``resource`` item that is the target itself; for a ``share`` item it is the directory the
    share publishes, because effective access is always a question about a file-system
    object reached by a path. ``None`` when ADG holds no such directory, which is why the
    two access answers can be unavailable on an item whose evidence is perfectly good."""


class ReviewContextService:
    """Assembles review context over one session. Reads only."""

    def __init__(self, session: AsyncSession, *, now: dt.datetime | None = None) -> None:
        self._session = session
        self._fixed_now = now
        self._governance = GovernanceRepository(session)
        self._history = HistoryService(session)
        self._changes = ChangeService(session)
        self._resources = ResourceRepository(session)

    def _now(self) -> dt.datetime:
        return self._fixed_now or dt.datetime.now(dt.UTC)

    # ----------------------------------------------------------------------------- drift

    async def drift_for(
        self, items: Sequence[ReviewItem], *, at: dt.datetime | None = None
    ) -> dict[UUID, ItemDrift]:
        """Compare many items against the estate as of ``at``, in two indexed reads.

        Batched because a campaign's items cluster on a handful of targets, and asking per
        item would issue one query per row to answer a question about eleven folders.
        """
        moment = at or self._now()
        if not items:
            return {}
        targets = list({(item.target_kind, item.target_key) for item in items})
        grants = await self._governance.grants_on_targets_at(targets, moment)
        presence = await self._governance.target_presence_at(targets, moment)

        drifts: dict[UUID, ItemDrift] = {}
        for item in items:
            bucket = (item.target_kind.value, item.target_key)
            present, certainty = presence.get(bucket, (None, None))
            current = tuple(
                observed.evidence
                for observed in grants.get(bucket, ())
                if observed.evidence.trustee_key == item.principal_key
            )
            drifts[item.item_id] = compare_grants(
                item.grants,
                current,
                target_present=present,
                target_certainty=certainty,
            )
        return drifts

    # --------------------------------------------------------------------------- context

    async def context_for(self, item: ReviewItem, campaign: ReviewCampaign) -> ReviewContext:
        """Everything around one item. Several bounded reads, none scaling with the estate."""
        moment = self._now()
        drift = (await self.drift_for([item], at=moment))[item.item_id]
        resource_key = await self._resource_for(item)

        baseline = await self._access_at(item, resource_key, campaign.baseline_at)
        current = await self._current_access(item, resource_key)
        reach = await self._reach(item, resource_key)
        findings, findings_truncated = await self._findings(item)
        changes, changes_truncated = await self._changes_for(item)

        return ReviewContext(
            item=item,
            campaign=campaign,
            drift=drift,
            baseline_access=baseline,
            current_access=current,
            reach=reach,
            findings=findings,
            findings_truncated=findings_truncated,
            changes=changes,
            changes_truncated=changes_truncated,
            resolved_resource_key=resource_key,
        )

    async def _resource_for(self, item: ReviewItem) -> str | None:
        """The NTFS resource an effective-access question about this item is asked against.

        A share item's grant sits on the share's own ACL, but "what can they do" is a
        question about the file system reached through it, and the engine answers it against
        the directory the share publishes with the share ACL as the second layer. So a share
        item resolves to its root directory; if ADG has never read one, no access answer is
        possible and the caller is told that rather than shown an empty one.
        """
        if item.target_kind is ReviewTargetKind.RESOURCE:
            return item.target_key
        root = await self._resources.get_share_root_resource(item.target_key)
        return None if root is None else root.resource_key

    async def _access_at(
        self, item: ReviewItem, resource_key: str | None, at: dt.datetime
    ) -> AccessAt:
        """The effective answer over historical state — frozen, like the item itself.

        Worth having beside the entries because an entry is not an answer: a Full Control
        allow under a deny that outranks it grants nothing, and a reviewer shown only the
        entry would certify access the principal never had.
        """
        if resource_key is None:
            return AccessAt(
                at=at,
                available=False,
                unavailable_reason=_NO_RESOURCE_REASON,
                access=None,
                certainty=None,
            )
        answer = await self._history.effective_access_at(item.principal_key, resource_key, at)
        return AccessAt(
            at=at,
            available=True,
            unavailable_reason=None,
            access=answer.access.access,
            certainty=answer.certainty,
        )

    async def _current_access(self, item: ReviewItem, resource_key: str | None) -> AccessAt:
        moment = self._now()
        if resource_key is None:
            return AccessAt(
                at=moment,
                available=False,
                unavailable_reason=_NO_RESOURCE_REASON,
                access=None,
                certainty=None,
            )
        membership = MembershipRepository(self._session)
        service = AccessService(self._resources, membership)
        resolved = await service.effective_access(item.principal_key, resource_key)
        return AccessAt(
            at=moment,
            available=True,
            unavailable_reason=None,
            access=resolved.access,
            certainty=None,
        )

    async def _reach(self, item: ReviewItem, resource_key: str | None) -> Reach:
        if resource_key is None:
            return Reach(
                available=False,
                unavailable_reason=_NO_RESOURCE_REASON,
                direct_paths=0,
                group_paths=0,
                routes=(),
                removals=(),
                removing_reviewed_entries_leaves_access=None,
                truncated=False,
            )
        membership = MembershipRepository(self._session)
        service = AccessService(self._resources, membership)
        resolved = await service.explain_access(item.principal_key, resource_key)
        explanation = resolved.explanation

        # Paths that both allow and actually deliver something. A `REDUNDANT` path matched an
        # ACE that settled nothing, and a `CONSTRAINED` one is withheld by the other layer;
        # counting either as a way the principal reaches the resource would tell a reviewer
        # access exists through a group that in fact gives them nothing.
        granting = tuple(
            path
            for path in explanation.paths
            if path.relation is PathRelation.GRANT and path.effect is PathEffect.CONTRIBUTES
        )
        direct = tuple(path for path in granting if not path.via_group)
        through_groups = tuple(path for path in granting if path.via_group)
        routes = _routes(through_groups, resolved.principals)

        # A removal target names an *edge*, not an entry; the ACE key lives on the edge in
        # the explanation graph. Joining through it is what lets "removing this item's
        # entries" be answered rather than "removing some edge".
        ace_by_edge = {
            edge.edge_id: edge.ace_key for edge in explanation.graph.edges if edge.ace_key
        }
        reviewed_keys = {grant.ace_key for grant in item.grants}
        removals = tuple(
            EntryRemoval(
                ace_key=ace_by_edge[target.edge_id],
                rights_removed=target.rights_removed,
                rights_after=target.rights_after,
                revokes_all_access=target.revokes_all_access,
                changes_nothing=target.changes_nothing,
                alternate_paths=len(target.alternate_paths),
            )
            for target in explanation.removal_targets
            if ace_by_edge.get(target.edge_id) in reviewed_keys
        )
        leaves_access: bool | None = None
        if removals:
            # False only when *every* reviewed entry, removed on its own, would end all
            # access. Any other shape means at least one of them can be taken away without
            # the access going, which is the answer a reviewer must not be left to assume.
            leaves_access = not all(removal.revokes_all_access for removal in removals)
        elif explanation.paths:
            # The entries are named in the evidence and the explanation attributes no path
            # to them: the grant contributes nothing today. Access, if any, is somebody
            # else's doing.
            leaves_access = bool(granting)

        return Reach(
            available=True,
            unavailable_reason=None,
            direct_paths=len(direct),
            group_paths=len(through_groups),
            routes=routes[:MAX_CONTEXT_ROUTES],
            removals=removals,
            removing_reviewed_entries_leaves_access=leaves_access,
            truncated=bool(explanation.truncation) or len(routes) > MAX_CONTEXT_ROUTES,
            labels=resolved.principals,
            explanation=explanation,
        )

    async def _findings(self, item: ReviewItem) -> tuple[tuple[RelatedFinding, ...], bool]:
        """Open risk findings naming this target, this principal, or both.

        Read straight from ``risk_findings`` rather than through the risk service, which
        evaluates. Governance reads; it does not run an evaluation as a side effect of
        somebody opening a page, and a read that could trigger one would make a review screen
        cost whatever the estate costs.
        """
        target_column = (
            risk_findings.c.resource_key
            if item.target_kind is ReviewTargetKind.RESOURCE
            else risk_findings.c.share_key
        )
        statement = (
            sa.select(risk_findings)
            .where(
                sa.or_(
                    target_column == item.target_key,
                    risk_findings.c.principal_key == item.principal_key,
                )
            )
            .where(risk_findings.c.status == RiskFindingStatus.OPEN.value)
            .order_by(risk_findings.c.detected_at.desc())
            .limit(MAX_CONTEXT_FINDINGS + 1)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        truncated = len(rows) > MAX_CONTEXT_FINDINGS
        found = [
            RelatedFinding(
                finding_key=row["finding_key"],
                rule_id=row["rule_id"],
                status=row["status"],
                severity=row["severity"],
                band=row["severity_band"],
                confidence=row["confidence"],
                detected_at=row["detected_at"],
                first_detected_at=row["first_detected_at"],
                relation=_relation(row[target_column.name], row["principal_key"], item),
                resource_key=row["resource_key"],
                share_key=row["share_key"],
                principal_key=row["principal_key"],
                detail=dict(row["detail"] or {}),
            )
            for row in rows[:MAX_CONTEXT_FINDINGS]
        ]
        # Ordered by how tightly the finding matches this item, then by severity, then by
        # recency. A finding about this exact pairing outranks a general one about the
        # folder, which outranks one about the principal somewhere else entirely.
        found.sort(key=lambda entry: (_RELATION_ORDER[entry.relation], -_severity_rank(entry)))
        return tuple(found), truncated

    async def _changes_for(self, item: ReviewItem) -> tuple[tuple[LastChange, ...], bool]:
        """What the change feed says about this item's own entries, newest first."""
        kind = (
            ObservationKind.NTFS_ACE
            if item.target_kind is ReviewTargetKind.RESOURCE
            else ObservationKind.SMB_ACE
        )
        collected: list[ObjectChange] = []
        for grant in item.grants:
            found = await self._changes.object_changes(kind, grant.ace_key, limit=_PER_ENTRY)
            collected.extend(found.changes)
        collected.sort(key=_change_order, reverse=True)
        truncated = len(collected) > MAX_CONTEXT_CHANGES
        return tuple(_last_change(change) for change in collected[:MAX_CONTEXT_CHANGES]), truncated


# ---------------------------------------------------------------------------- internals

_NO_RESOURCE_REASON: Final = (
    "ADG holds no directory for this target, so it cannot resolve effective access. For a "
    "share that means the directory it publishes has never been scanned; the share's own "
    "permissions are still shown, and they are what this item is about."
)

_PER_ENTRY: Final = 5
"""Transitions read per entry before merging. Small deliberately: the question is "what
happened to this recently", and a folder whose ACL churns is answered by the top of the list
rather than by all of it."""

_RELATION_ORDER: Final[Mapping[str, int]] = {"access": 0, "target": 1, "principal": 2}


#: Severity, weakest first, as :class:`app.risk_engine.severity.Severity` spells it.
#: Written out rather than imported so that governance rendering does not break the moment
#: the risk engine adds a value: an unknown severity sorts weakest and is still shown.
_SEVERITY_ORDER: Final[tuple[str, ...]] = (
    "informational",
    "low",
    "medium",
    "high",
    "critical",
)


def _severity_rank(finding: RelatedFinding) -> int:
    return _SEVERITY_ORDER.index(finding.severity) if finding.severity in _SEVERITY_ORDER else 0


def _relation(target_value: str | None, principal_value: str | None, item: ReviewItem) -> str:
    """How tightly a finding's subject matches this item.

    ``access`` — the finding is about this principal on this place, the case a reviewer
    most needs; ``target`` — about the place, whoever is on it; ``principal`` — about the
    principal, wherever it is. The ordering these produce is what
    :meth:`ReviewContextService._findings` sorts by, before severity: a critical finding
    about a group somewhere else in the estate is still less relevant to this decision than
    a medium one about this exact grant.
    """
    on_target = target_value is not None and target_value == item.target_key
    on_principal = principal_value is not None and principal_value == item.principal_key
    if on_target and on_principal:
        return "access"
    return "target" if on_target else "principal"


def _routes(
    paths: Sequence[CausalPath], labels: Mapping[str, PrincipalRecord]
) -> tuple[GroupRoute, ...]:
    """One route per group, keeping the shortest chain and the widest rights seen for it.

    Shortest because the shortest chain is the one somebody would actually break; widest
    because a group reached twice contributes the union of what its entries grant, and
    reporting the narrower of the two would understate what a revocation has to overcome.
    """
    best: dict[str, GroupRoute] = {}
    for path in paths:
        trustee = path.trustee_key
        existing = best.get(trustee)
        depth = max(len(path.chain) - 1, 0)
        candidate = GroupRoute(
            principal_key=trustee,
            display_name=_label(trustee, labels),
            depth=depth,
            chain=tuple(path.chain),
            rights=path.effective_rights,
            layer=path.layer.value,
            inherited=path.inherited,
        )
        if existing is None:
            best[trustee] = candidate
            continue
        if depth < existing.depth:
            best[trustee] = GroupRoute(
                principal_key=trustee,
                display_name=candidate.display_name,
                depth=depth,
                chain=candidate.chain,
                rights=_wider(existing.rights, candidate.rights),
                layer=candidate.layer,
                inherited=candidate.inherited,
            )
        elif candidate.rights.value | existing.rights.value != existing.rights.value:
            best[trustee] = GroupRoute(
                principal_key=existing.principal_key,
                display_name=existing.display_name,
                depth=existing.depth,
                chain=existing.chain,
                rights=_wider(existing.rights, candidate.rights),
                layer=existing.layer,
                inherited=existing.inherited,
            )
    return tuple(sorted(best.values(), key=lambda route: (route.depth, route.principal_key)))


def _wider(left: RightsMask, right: RightsMask) -> RightsMask:
    """The union of two masks on the same layer.

    Masks never cross layers (the rights model's first rule), so two routes on different
    layers are not combined — the left one, which is the one being kept, wins.
    """
    if left.layer is not right.layer:
        return left
    return RightsMask(left.value | right.value, left.layer)


def _label(key: str, labels: Mapping[str, PrincipalRecord]) -> str | None:
    record = labels.get(key)
    if record is None:
        return None
    return record.display_name or record.sam_account_name or record.last_known_name


def _change_order(change: ObjectChange) -> dt.datetime:
    """Newest first, by the upper bound of when the change is known to have happened.

    The upper bound rather than the lower one because that is the instant ADG can actually
    stand behind: the lower bound is the last confirmation of the previous state, which for
    a rarely-scanned object can be months before anything happened.
    """
    if change.window is not None:
        return change.window.at_or_before
    return change.after.valid_from


def _last_change(change: ObjectChange) -> LastChange:
    window = change.window
    return LastChange(
        kind=change.kind.value,
        key=change.key,
        action=change.action.value,
        significance=change.significance.value,
        severity=change.severity.value,
        direction=change.direction.value,
        reasons=tuple(change.reasons),
        changed_after=None if window is None else window.after,
        changed_at_or_before=None if window is None else window.at_or_before,
        is_exact=False if window is None else window.is_exact,
    )
