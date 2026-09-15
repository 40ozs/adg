r"""The governance workflow: create, freeze, assign, decide, propose, close, verify.

Everything a governance request does passes through here, and the reason to have the layer
at all is that three rules have to hold on **every** path and must not be restated per
route:

1. **Every act is audited before it is answered.** A decision and its audit event are
   written in one transaction, so there is no ordering in which a reviewer is told "recorded"
   and no event exists.
2. **Capability is not authority.** Holding ``governance:review`` admits a request to the
   route; it does not let anyone decide any item. The second gate is the **assignment**: an
   item may be answered only by the reviewer it was assigned to, checked here against the
   database, so the frontend hiding a button is decoration and this is the control.
3. **Nothing here writes a collected fact.** The repository is the only thing that touches
   the database and its collected-state reads are read-only; a ``revoke`` decision produces a
   :class:`~app.governance.model.RemediationProposal`, which is ADG's record of a change
   somebody *might* make in Windows.

**This layer owns the transaction.** Each public mutating method commits once, at its
end, after its audit event has been appended — the same shape
:class:`app.ingestion.service.IngestionService` has, and for the same reason. The request
scope does not commit (see :func:`app.api.deps.get_session`), so a service that did not would
roll everything back the moment the response was sent; and committing *after* the event means
there is no ordering in which a reviewer is told "recorded" and no event exists.

The other property worth naming is **reproducibility**. :meth:`GovernanceService.generate`
and :meth:`GovernanceService.verify_campaign` call the same two functions in the same order
— the repository's baseline read and the pure generator — so verification is a re-run rather
than a second implementation of the rules, and it cannot agree with a campaign that a fresh
generation would disagree with.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import (
    CampaignFocus,
    CampaignStatus,
    CommentRequirement,
    DecisionKind,
    GovernanceEventType,
    OwnershipRole,
    RemediationAction,
    RemediationStatus,
    ReviewItemStatus,
    ReviewScopeKind,
    ReviewTargetKind,
)
from app.governance.audit import (
    OWNERS_CHAIN,
    ChainVerification,
    GovernanceEvent,
    campaign_chain,
    verify_chain,
)
from app.governance.drift import DriftVerdict, ItemDrift
from app.governance.generation import (
    MAX_CAMPAIGN_ITEMS,
    CampaignTooLarge,
    GenerationResult,
    generate_items,
)
from app.governance.model import (
    CampaignScope,
    GenerationOptions,
    RemediationProposal,
    ResourceOwner,
    ReviewAssignment,
    ReviewCampaign,
    ReviewDecision,
    ReviewItem,
    validate_baseline,
    validate_due_date,
    validate_note,
    validate_rationale,
    validate_scopes,
    validate_transition,
)
from app.governance.repository import (
    GovernanceRepository,
    ItemPage,
    ScopeTooLarge,
    StatusCounts,
)
from app.governance.review import MAX_DRIFT_ITEMS, ReviewContext, ReviewContextService

__all__ = [
    "MAX_BULK_ITEMS",
    "Actor",
    "BulkOutcome",
    "CampaignStatusReport",
    "CampaignVerification",
    "DriftReport",
    "GenerationOutcome",
    "GovernanceConflict",
    "GovernanceForbidden",
    "GovernanceNotFound",
    "GovernanceService",
    "NotHomogeneous",
    "QueueEntry",
    "ReviewerProgress",
]

logger = logging.getLogger("adg.governance")


class GovernanceNotFound(Exception):
    """A campaign, item, assignment or ownership record does not exist. HTTP 404."""


class GovernanceConflict(Exception):
    """The request is well-formed and the current state refuses it. HTTP 409.

    Separate from a validation error because the caller does something different with it: a
    422 means "fix the request", a 409 means "the request was fine; look at what happened
    since". Activating an already-active campaign is the second, not the first.
    """


class GovernanceForbidden(Exception):
    """The caller holds the capability and is still not permitted this act. HTTP 403.

    The only source of these is the assignment gate. A capability says what a *kind* of
    request may do; this says whether this particular person was asked this particular
    question.
    """


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is performing a governance act, as the audit trail will record them.

    A value rather than the request's ``AuthenticatedPrincipal`` so that the service can be
    exercised without minting a token, and so that what reaches the audit trail is exactly
    the four fields it stores — nothing about the request survives into the record by
    accident.
    """

    subject: str
    display_name: str | None = None
    email: str | None = None
    roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    """What one generation produced, for the caller and for the audit event."""

    campaign: ReviewCampaign
    items: tuple[ReviewItem, ...]
    excluded: Mapping[str, int]
    digest: str
    regenerated: bool
    """True when this replaced an earlier draft generation rather than being the first."""

    @property
    def item_count(self) -> int:
        return len(self.items)


MAX_BULK_ITEMS = 200
"""Items one bulk decision may cover.

Not a performance ceiling — each one writes a decision and an audit event, and two hundred
of those is nothing. It is a ceiling on how much a single click can assert. A reviewer who
certifies two hundred grants at once has plausibly looked at them; one who certifies five
thousand has pressed a button, and the record would read the same for both.
"""


@dataclass(frozen=True, slots=True)
class ReviewerProgress:
    """One reviewer's share of a campaign, and whether they are behind."""

    assignment_id: UUID
    reviewer_subject: str
    reviewer_display_name: str | None
    scope: CampaignScope | None
    due_at: dt.datetime | None
    assigned: int
    decided: int
    late_decisions: int = 0
    """Current decisions this reviewer recorded after their deadline. Counted over current
    decisions only: a late answer later corrected is one item, not two failures."""

    overdue: bool = False
    """Work still outstanding past the deadline. Computed against the assignment's own due
    date where it has one and the campaign's otherwise, and **false once nothing is
    pending** — a reviewer who finished late is not overdue, they are done, and
    ``late_decisions`` is where that shows."""

    @property
    def pending(self) -> int:
        return self.assigned - self.decided

    @property
    def completion(self) -> float:
        return 0.0 if self.assigned == 0 else self.decided / self.assigned


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """One campaign as it appears in a reviewer's own queue."""

    campaign: ReviewCampaign
    assigned: int
    decided: int
    due_at: dt.datetime | None
    """The earliest of this reviewer's own assignment deadlines in this campaign, falling
    back to the campaign's. Theirs first: a reviewer is late against what they were asked
    for, and showing a later campaign deadline would tell them they still had time."""

    overdue: bool

    @property
    def pending(self) -> int:
        return self.assigned - self.decided


@dataclass(frozen=True, slots=True)
class DriftReport:
    """What the estate has done to one campaign's items since it was frozen.

    A **page**, and the field names say so. Drift is computed on read against live state, so
    a report over a five-thousand-item campaign would re-read every target to render a
    banner. ``covered`` beside ``total_items`` is what stops a reader taking "3 changed" for
    the whole answer.
    """

    campaign_id: UUID
    compared_at: dt.datetime
    total_items: int
    covered: int
    counts: Mapping[str, int]
    """Items by :class:`app.governance.drift.DriftVerdict`, over the part covered."""

    drifted: tuple[tuple[ReviewItem, ItemDrift], ...]
    """Only the items whose verdict is drift, in item order. An unchanged item is counted
    and not listed: a drift report that returned everything would be an item list."""

    @property
    def has_more(self) -> bool:
        return self.covered < self.total_items

    @property
    def drifted_count(self) -> int:
        return len(self.drifted)


@dataclass(frozen=True, slots=True)
class BulkOutcome:
    """One bulk decision: every item answered separately, and what was superseded."""

    campaign_id: UUID
    decision: DecisionKind
    decisions: tuple[ReviewDecision, ...]
    superseded: int
    """How many of the items already carried a decision this one replaced. Zero under the
    homogeneity rule below, which refuses a batch containing a decided item — kept as a
    field so the invariant is visible in the response rather than only in the rule."""

    @property
    def item_count(self) -> int:
        return len(self.decisions)


class NotHomogeneous(Exception):
    """A bulk decision was asked to cover items that are not one question.

    Its own exception because the API answers 422 and the message has to name the axis that
    failed. See :meth:`GovernanceService.bulk_decide` for what homogeneous means here and
    why each part of the rule is there.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class CampaignStatusReport:
    """Where a campaign stands, including the parts that are nobody's."""

    campaign: ReviewCampaign
    counts: StatusCounts
    unassigned_items: int
    """Items attached to no active assignment. These can never be decided, so they are
    reported on their own rather than inside the pending total: a campaign with three
    thousand of them is stuck, not slow."""

    overdue: bool
    reviewers: tuple[ReviewerProgress, ...]
    audit_head: str | None
    """The newest audit event's digest. Worth recording outside the database — in an export
    or a ticket — because it is what a later verification is compared against."""


@dataclass(frozen=True, slots=True)
class CampaignVerification:
    """Whether a campaign's stored items are still what its baseline produces.

    A campaign is reproducible when regenerating it from its own row — focus, scopes,
    options, baseline instant — yields the same items with the same evidence. Divergence is
    a genuine finding rather than noise: ``object_versions`` is append-only and a version's
    interval is fixed once written, so the only ways a baseline can come to produce a
    different answer are that history was edited, restored from a partial backup, or pruned
    by retention past the baseline. Each of those is something an auditor must be told.
    """

    campaign_id: UUID
    reproducible: bool
    stored_digest: str | None
    recomputed_digest: str
    stored_item_count: int
    recomputed_item_count: int
    missing: tuple[tuple[str, str, str], ...]
    """Items the baseline produces now that the campaign does not hold."""

    unexpected: tuple[tuple[str, str, str], ...]
    """Items the campaign holds that the baseline no longer produces."""

    evidence_changed: tuple[tuple[str, str, str], ...]
    """Items present on both sides whose frozen evidence no longer matches."""

    explanation: str


class GovernanceService:
    """One session's worth of governance work."""

    def __init__(self, session: AsyncSession, *, now: dt.datetime | None = None) -> None:
        self._session = session
        self._repository = GovernanceRepository(session)
        self._fixed_now = now

    def _now(self) -> dt.datetime:
        return self._fixed_now or dt.datetime.now(tz=dt.UTC)

    # --------------------------------------------------------------------------- owners

    async def assign_owner(
        self,
        actor: Actor,
        *,
        target_kind: ReviewTargetKind,
        target_key: str,
        ownership_role: OwnershipRole = OwnershipRole.OWNER,
        owner_subject: str | None = None,
        owner_principal_key: str | None = None,
        owner_display_name: str | None = None,
        note: str | None = None,
    ) -> ResourceOwner:
        """Record that a party is accountable for a resource.

        This says nothing about the Windows security descriptor and changes nothing in it.
        See ADR-0028 and the module docstring of :mod:`app.governance.model`.
        """
        now = self._now()
        owner = ResourceOwner(
            owner_id=uuid4(),
            target_kind=target_kind,
            target_key=target_key.casefold(),
            ownership_role=ownership_role,
            owner_subject=owner_subject,
            owner_principal_key=owner_principal_key,
            owner_display_name=owner_display_name,
            note=validate_note(note, field_name="note"),
            assigned_by_subject=actor.subject,
            assigned_at=now,
        )
        await self._repository.insert_owner(owner)
        await self._append(
            actor,
            chain_key=OWNERS_CHAIN,
            event_type=GovernanceEventType.OWNER_ASSIGNED,
            occurred_at=now,
            payload={
                "owner_id": str(owner.owner_id),
                "target_kind": owner.target_kind.value,
                "target_key": owner.target_key,
                "ownership_role": owner.ownership_role.value,
                "owner": owner.identity,
            },
        )
        await self._session.commit()
        return owner

    async def revoke_owner(self, actor: Actor, owner_id: UUID) -> ResourceOwner:
        existing = await self._repository.get_owner(owner_id)
        if existing is None:
            raise GovernanceNotFound(f"No ownership record {owner_id}.")
        if not existing.is_active:
            raise GovernanceConflict(
                f"Ownership record {owner_id} was already revoked at "
                f"{existing.revoked_at:%Y-%m-%d %H:%M} UTC."
            )
        now = self._now()
        await self._repository.revoke_owner(owner_id, subject=actor.subject, at=now)
        await self._append(
            actor,
            chain_key=OWNERS_CHAIN,
            event_type=GovernanceEventType.OWNER_REVOKED,
            occurred_at=now,
            payload={
                "owner_id": str(owner_id),
                "target_key": existing.target_key,
                "owner": existing.identity,
            },
        )
        await self._session.commit()
        refreshed = await self._repository.get_owner(owner_id)
        if refreshed is None:  # pragma: no cover - written in this transaction
            raise GovernanceNotFound(f"No ownership record {owner_id}.")
        return refreshed

    async def list_owners(
        self,
        *,
        target_kind: ReviewTargetKind | None = None,
        target_key: str | None = None,
        owner_subject: str | None = None,
        include_revoked: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[tuple[ResourceOwner, ...], bool]:
        return await self._repository.list_owners(
            target_kind=target_kind,
            target_key=target_key,
            owner_subject=owner_subject,
            include_revoked=include_revoked,
            limit=limit,
            offset=offset,
        )

    # ------------------------------------------------------------------------ campaigns

    async def create_campaign(
        self,
        actor: Actor,
        *,
        name: str,
        focus: CampaignFocus,
        scopes: Sequence[CampaignScope],
        baseline_at: dt.datetime,
        description: str | None = None,
        due_at: dt.datetime | None = None,
        options: GenerationOptions | None = None,
        comment_requirement: CommentRequirement = CommentRequirement.STANDARD,
    ) -> ReviewCampaign:
        """Create a campaign in ``draft``. Nothing is frozen until it is generated."""
        now = self._now()
        validate_scopes(focus, scopes)
        baseline = validate_baseline(baseline_at, now)
        due = validate_due_date(due_at, baseline)
        cleaned_name = name.strip()
        campaign = ReviewCampaign(
            campaign_id=uuid4(),
            name=cleaned_name,
            description=validate_note(description, field_name="description"),
            focus=focus,
            status=CampaignStatus.DRAFT,
            baseline_at=baseline,
            due_at=due,
            options=options or GenerationOptions(),
            scopes=tuple(dict.fromkeys(scopes)),
            created_by_subject=actor.subject,
            created_at=now,
            comment_requirement=comment_requirement,
        )
        await self._repository.insert_campaign(campaign)
        await self._append(
            actor,
            chain_key=campaign_chain(campaign.campaign_id),
            event_type=GovernanceEventType.CAMPAIGN_CREATED,
            occurred_at=now,
            campaign_id=campaign.campaign_id,
            payload={
                "name": campaign.name,
                "focus": focus.value,
                "baseline_at": baseline.isoformat(),
                "due_at": None if due is None else due.isoformat(),
                "scopes": [f"{scope.kind.value}:{scope.key}" for scope in campaign.scopes],
                "options": campaign.options.as_digestible(),
                "comment_requirement": comment_requirement.value,
            },
        )
        await self._session.commit()
        return campaign

    async def get_campaign(self, campaign_id: UUID) -> ReviewCampaign:
        campaign = await self._repository.get_campaign(campaign_id)
        if campaign is None:
            raise GovernanceNotFound(f"No review campaign {campaign_id}.")
        return campaign

    async def list_campaigns(
        self,
        *,
        status: CampaignStatus | None = None,
        reviewer_subject: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[tuple[ReviewCampaign, ...], bool]:
        return await self._repository.list_campaigns(
            status=status, reviewer_subject=reviewer_subject, limit=limit, offset=offset
        )

    async def generate(self, actor: Actor, campaign_id: UUID) -> GenerationOutcome:
        """Freeze the campaign's item set against its baseline.

        Permitted only in ``draft``, and idempotent in spirit rather than in letter: running
        it again on a draft **replaces** the items, because a draft has no decisions and an
        operator who widened the scope means the new set. Running it on an active campaign is
        refused — that would change what reviewers are answering underneath them, and the
        decisions already recorded would be about items that no longer exist.
        """
        campaign = await self.get_campaign(campaign_id)
        if campaign.status is not CampaignStatus.DRAFT:
            raise GovernanceConflict(
                f"Campaign {campaign_id} is {campaign.status.value} and its item set is "
                "frozen. Generation is permitted only while a campaign is a draft, because "
                "regenerating an open campaign would change what reviewers are answering "
                "and orphan the decisions already recorded against it."
            )

        result = await self._generate_from_baseline(campaign)
        regenerated = campaign.is_generated
        now = self._now()
        await self._repository.clear_items(campaign_id)
        items = await self._repository.insert_items(campaign_id, result.items, at=now)
        await self._repository.record_generation(
            campaign_id,
            digest=result.digest,
            item_count=len(items),
            excluded=result.excluded,
            at=now,
        )
        await self._append(
            actor,
            chain_key=campaign_chain(campaign_id),
            event_type=GovernanceEventType.CAMPAIGN_GENERATED,
            occurred_at=now,
            campaign_id=campaign_id,
            payload={
                "item_count": len(items),
                "snapshot_digest": result.digest,
                "excluded": dict(result.excluded),
                "regenerated": regenerated,
            },
        )
        await self._session.commit()
        refreshed = await self.get_campaign(campaign_id)
        return GenerationOutcome(
            campaign=refreshed,
            items=items,
            excluded=result.excluded,
            digest=result.digest,
            regenerated=regenerated,
        )

    async def _generate_from_baseline(self, campaign: ReviewCampaign) -> GenerationResult:
        """The one path from a campaign row to an item set.

        Called by :meth:`generate` and by :meth:`verify_campaign`, which is what makes
        verification a re-run rather than a second implementation of the rules.
        """
        grants = await self._repository.grants_at(
            focus=campaign.focus, scopes=campaign.scopes, at=campaign.baseline_at
        )
        names = await self._repository.principal_names_at(
            [grant.principal_key for grant in grants], campaign.baseline_at
        )
        return generate_items(
            focus=campaign.focus,
            options=campaign.options,
            grants=grants,
            principal_names=names,
            ceiling=MAX_CAMPAIGN_ITEMS,
        )

    async def activate(self, actor: Actor, campaign_id: UUID) -> ReviewCampaign:
        """Open a generated campaign for decisions."""
        campaign = await self.get_campaign(campaign_id)
        validate_transition(campaign.status, CampaignStatus.ACTIVE)
        if not campaign.is_generated:
            raise GovernanceConflict(
                f"Campaign {campaign_id} has no item set yet. Generate it before activating: "
                "a campaign opened with nothing in it would report itself complete."
            )
        if campaign.item_count == 0:
            raise GovernanceConflict(
                f"Campaign {campaign_id} generated zero items from its scopes, so there is "
                "nothing to review. Check the scopes and the baseline instant — an empty "
                "result usually means the baseline predates the first scan of that scope."
            )
        return await self._transition(actor, campaign_id, CampaignStatus.ACTIVE)

    async def close(self, actor: Actor, campaign_id: UUID) -> ReviewCampaign:
        """Finish a campaign. Decisions stay readable; no new one may be recorded.

        Closing with items still pending is **allowed**, and the count is recorded in the
        audit event. A campaign that could not be closed until every item was answered would
        either never close or be closed by somebody rubber-stamping the remainder, and the
        honest record of "closed with 12 undecided" is worth more than either.
        """
        campaign = await self.get_campaign(campaign_id)
        validate_transition(campaign.status, CampaignStatus.CLOSED)
        counts = await self._repository.status_counts(campaign_id)
        return await self._transition(
            actor,
            campaign_id,
            CampaignStatus.CLOSED,
            payload={"decided": counts.decided, "undecided": counts.pending},
        )

    async def cancel(
        self, actor: Actor, campaign_id: UUID, *, reason: str | None = None
    ) -> ReviewCampaign:
        """Abandon a campaign. Any decisions already recorded stay and stay attributable."""
        campaign = await self.get_campaign(campaign_id)
        validate_transition(campaign.status, CampaignStatus.CANCELED)
        return await self._transition(
            actor,
            campaign_id,
            CampaignStatus.CANCELED,
            payload={"reason": validate_note(reason, field_name="reason")},
        )

    async def _transition(
        self,
        actor: Actor,
        campaign_id: UUID,
        status: CampaignStatus,
        *,
        payload: dict[str, Any] | None = None,
    ) -> ReviewCampaign:
        now = self._now()
        subject = actor.subject if status is not CampaignStatus.ACTIVE else None
        await self._repository.set_campaign_status(campaign_id, status, at=now, subject=subject)
        await self._append(
            actor,
            chain_key=campaign_chain(campaign_id),
            event_type=_TRANSITION_EVENTS[status],
            occurred_at=now,
            campaign_id=campaign_id,
            payload={"status": status.value, **(payload or {})},
        )
        await self._session.commit()
        return await self.get_campaign(campaign_id)

    # ---------------------------------------------------------------------- assignments

    async def assign(
        self,
        actor: Actor,
        campaign_id: UUID,
        *,
        reviewer_subject: str,
        reviewer_display_name: str | None = None,
        reviewer_email: str | None = None,
        scope: CampaignScope | None = None,
        due_at: dt.datetime | None = None,
    ) -> tuple[ReviewAssignment, int]:
        """Ask one reviewer for one slice of a campaign. Returns the items it attached.

        An item already attached to another assignment is reattached: an assignment answers
        "who is being asked *now*", and two reviewers each believing an item is theirs is
        worse than one reviewer being replaced. The reattachment count is in the audit event.
        """
        campaign = await self.get_campaign(campaign_id)
        if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.ACTIVE):
            raise GovernanceConflict(
                f"Campaign {campaign_id} is {campaign.status.value}; reviewers can be "
                "assigned only while it is a draft or active."
            )
        if scope is not None and scope.kind not in _ASSIGNABLE_SCOPES[campaign.focus]:
            allowed = ", ".join(sorted(kind.value for kind in _ASSIGNABLE_SCOPES[campaign.focus]))
            raise GovernanceConflict(
                f"A {campaign.focus.value}-focused campaign cannot have an assignment scoped "
                f"by {scope.kind.value}; it accepts {allowed}. A scope that selects nothing "
                "would look like an assignment and give the reviewer no items."
            )
        cleaned = reviewer_subject.strip()
        if not cleaned:
            raise GovernanceConflict(
                "An assignment needs a reviewer subject. An attestation with no attributable "
                "reviewer is not an attestation."
            )
        now = self._now()
        assignment = ReviewAssignment(
            assignment_id=uuid4(),
            campaign_id=campaign_id,
            reviewer_subject=cleaned,
            reviewer_display_name=reviewer_display_name,
            reviewer_email=reviewer_email,
            scope=scope,
            due_at=validate_due_date(due_at, campaign.baseline_at),
            assigned_by_subject=actor.subject,
            assigned_at=now,
        )
        await self._repository.insert_assignment(assignment)
        attached = await self._repository.attach_items(campaign_id, assignment, at=now)
        await self._append(
            actor,
            chain_key=campaign_chain(campaign_id),
            event_type=GovernanceEventType.REVIEWER_ASSIGNED,
            occurred_at=now,
            campaign_id=campaign_id,
            payload={
                "assignment_id": str(assignment.assignment_id),
                "reviewer_subject": cleaned,
                "scope": None if scope is None else f"{scope.kind.value}:{scope.key}",
                "items_attached": attached,
            },
        )
        await self._session.commit()
        return assignment, attached

    async def revoke_assignment(
        self, actor: Actor, campaign_id: UUID, assignment_id: UUID
    ) -> ReviewAssignment:
        """Withdraw an assignment. Decisions the reviewer already made stay recorded.

        The items stay attached to the revoked assignment rather than being detached, so
        "who was asked about this" remains answerable. The decision gate checks the
        assignment is *active*, so a revoked reviewer can no longer answer.
        """
        assignment = await self._repository.get_assignment(assignment_id)
        if assignment is None or assignment.campaign_id != campaign_id:
            raise GovernanceNotFound(f"No assignment {assignment_id} on campaign {campaign_id}.")
        if not assignment.is_active:
            raise GovernanceConflict(f"Assignment {assignment_id} was already revoked.")
        now = self._now()
        await self._repository.revoke_assignment(assignment_id, subject=actor.subject, at=now)
        await self._append(
            actor,
            chain_key=campaign_chain(campaign_id),
            event_type=GovernanceEventType.REVIEWER_REVOKED,
            occurred_at=now,
            campaign_id=campaign_id,
            payload={
                "assignment_id": str(assignment_id),
                "reviewer_subject": assignment.reviewer_subject,
            },
        )
        await self._session.commit()
        refreshed = await self._repository.get_assignment(assignment_id)
        if refreshed is None:  # pragma: no cover - written in this transaction
            raise GovernanceNotFound(f"No assignment {assignment_id}.")
        return refreshed

    async def list_assignments(
        self, campaign_id: UUID, *, include_revoked: bool = False
    ) -> tuple[ReviewAssignment, ...]:
        await self.get_campaign(campaign_id)
        return await self._repository.list_assignments(campaign_id, include_revoked=include_revoked)

    # ---------------------------------------------------------------------------- items

    async def list_items(
        self,
        campaign_id: UUID,
        *,
        status: ReviewItemStatus | None = None,
        reviewer_subject: str | None = None,
        unassigned_only: bool = False,
        principal_key: str | None = None,
        target_key: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> ItemPage:
        """One page of a campaign's items.

        ``reviewer_subject`` narrows to the items that reviewer was actually assigned, which
        is what "my queue" means. It resolves through the assignments rather than matching a
        column, so a revoked assignment drops out of the queue without any item being edited.
        """
        await self.get_campaign(campaign_id)
        assignment_ids: Sequence[UUID] | None = None
        if reviewer_subject is not None:
            active = await self._repository.active_assignments_for(campaign_id, reviewer_subject)
            assignment_ids = [item.assignment_id for item in active]
            if not assignment_ids:
                return ItemPage(items=(), has_more=False, total=0)
        return await self._repository.list_items(
            campaign_id,
            status=status,
            assignment_ids=assignment_ids,
            unassigned_only=unassigned_only,
            principal_key=principal_key,
            target_key=target_key,
            limit=limit,
            offset=offset,
        )

    async def get_item(self, item_id: UUID) -> ReviewItem:
        item = await self._repository.get_item(item_id)
        if item is None:
            raise GovernanceNotFound(f"No review item {item_id}.")
        return item

    async def item_decisions(self, item_id: UUID) -> tuple[ReviewDecision, ...]:
        await self.get_item(item_id)
        return await self._repository.decisions_for_item(item_id)

    # ------------------------------------------------------------------------ decisions

    async def decide(
        self,
        actor: Actor,
        item_id: UUID,
        *,
        decision: DecisionKind,
        rationale: str | None = None,
    ) -> ReviewDecision:
        """Record one attestation, superseding the reviewer's previous answer if there is one.

        Three gates, and they are independent on purpose. The capability gate is on the route.
        This method enforces the other two: the campaign must be **active**, and the item must
        be assigned to **this** caller. Neither can be satisfied by holding a role.
        """
        item = await self.get_item(item_id)
        campaign = await self.get_campaign(item.campaign_id)
        if not campaign.accepts_decisions:
            raise GovernanceConflict(
                f"Campaign {campaign.campaign_id} is {campaign.status.value} and accepts no "
                "decisions. Only an active campaign does."
            )
        assignment = await self._assignment_for(item, actor)
        cleaned = validate_rationale(decision, rationale, requirement=campaign.comment_requirement)

        now = self._now()
        deadline = assignment.due_at or campaign.due_at
        previous = await self._repository.current_decision(item_id)
        record = ReviewDecision(
            decision_id=uuid4(),
            item_id=item_id,
            campaign_id=item.campaign_id,
            decision=decision,
            rationale=cleaned,
            decided_by_subject=actor.subject,
            decided_by_display_name=actor.display_name,
            decided_at=now,
            decided_late=deadline is not None and now > deadline,
            supersedes_decision_id=None if previous is None else previous.decision_id,
        )
        # The supersession is written first so that the partial unique index on
        # (item_id) WHERE superseded_at IS NULL never sees two current rows, not even
        # inside the transaction. Inserting first would violate it immediately.
        if previous is not None:
            await self._repository.supersede_decision(
                previous.decision_id, successor=record.decision_id, at=now
            )
        await self._repository.insert_decision(record)
        await self._repository.mark_item_decided(item_id, decision_id=record.decision_id, at=now)

        if previous is not None:
            await self._append(
                actor,
                chain_key=campaign_chain(item.campaign_id),
                event_type=GovernanceEventType.DECISION_SUPERSEDED,
                occurred_at=now,
                campaign_id=item.campaign_id,
                item_id=item_id,
                decision_id=previous.decision_id,
                payload={
                    "superseded_decision": previous.decision.value,
                    "superseded_by": str(record.decision_id),
                    "originally_decided_by": previous.decided_by_subject,
                },
            )
        await self._append(
            actor,
            chain_key=campaign_chain(item.campaign_id),
            event_type=GovernanceEventType.DECISION_RECORDED,
            occurred_at=now,
            campaign_id=item.campaign_id,
            item_id=item_id,
            decision_id=record.decision_id,
            payload={
                "decision": decision.value,
                # The evidence digest is in the event so the trail says *what* was certified,
                # not only that something was. Without it, "Alice certified item 4f2" would
                # depend on the item row still holding the evidence it held at the time.
                "evidence_digest": item.evidence_digest,
                "target_kind": item.target_kind.value,
                "target_key": item.target_key,
                "principal_key": item.principal_key,
                "certainty": item.certainty.value,
                "late": record.decided_late,
                "assignment_id": str(assignment.assignment_id),
                "has_rationale": cleaned is not None,
            },
        )
        await self._session.commit()
        logger.info(
            "governance.decision.recorded",
            extra={
                "campaign_id": str(item.campaign_id),
                "item_id": str(item_id),
                "decision": decision.value,
                "subject": actor.subject,
            },
        )
        return record

    async def _assignment_for(self, item: ReviewItem, actor: Actor) -> ReviewAssignment:
        """The active assignment that entitles ``actor`` to answer ``item``, or 403.

        Three refusals, worded apart because they send an operator to three different places:
        nobody was asked, somebody else was asked, or the person asked has been stood down.
        """
        if item.assignment_id is None:
            raise GovernanceForbidden(
                f"Review item {item.item_id} is assigned to nobody, so no one can decide it. "
                "A governance administrator must assign a reviewer to its scope first."
            )
        assignment = await self._repository.get_assignment(item.assignment_id)
        if assignment is None:  # pragma: no cover - the foreign key prevents this
            raise GovernanceForbidden(
                f"Review item {item.item_id} names an assignment that no longer exists."
            )
        if assignment.reviewer_subject != actor.subject:
            raise GovernanceForbidden(
                f"Review item {item.item_id} is assigned to another reviewer. An item may be "
                "answered only by the person it was asked of, so that every decision is "
                "attributable to somebody who was accountable for it."
            )
        if not assignment.is_active:
            raise GovernanceForbidden(
                f"Your assignment on campaign {item.campaign_id} was revoked, so you can no "
                "longer record decisions on it. Decisions you already made stay recorded."
            )
        return assignment

    # ---------------------------------------------------------------------- remediation

    async def propose_remediation(
        self,
        actor: Actor,
        item_id: UUID,
        *,
        action: RemediationAction,
        details: Mapping[str, Any] | None = None,
    ) -> RemediationProposal:
        """Record the change somebody might make in Windows. ADG does not make it.

        Attached only to an item whose current decision is **not** ``certify``: a proposal to
        remove a grant somebody just certified is a contradiction, and storing it would leave
        two answers with nothing to choose between them.
        """
        item = await self.get_item(item_id)
        campaign = await self.get_campaign(item.campaign_id)
        if not campaign.accepts_decisions:
            raise GovernanceConflict(
                f"Campaign {campaign.campaign_id} is {campaign.status.value} and accepts no "
                "further proposals. Only an active campaign does."
            )
        assignment = await self._assignment_for(item, actor)
        current = await self._repository.current_decision(item_id)
        if current is None:
            raise GovernanceConflict(
                f"Review item {item_id} has no decision yet. A remediation proposal records "
                "what should follow from a decision, so the decision comes first."
            )
        if current.decision is DecisionKind.CERTIFY:
            raise GovernanceConflict(
                f"Review item {item_id} was certified, so there is nothing to remediate. "
                "Record a 'revoke' or 'modify' decision first if the grant should change."
            )
        now = self._now()
        proposal = RemediationProposal(
            proposal_id=uuid4(),
            item_id=item_id,
            campaign_id=item.campaign_id,
            decision_id=current.decision_id,
            action=action,
            status=RemediationStatus.PROPOSED,
            target_kind=item.target_kind,
            target_key=item.target_key,
            principal_key=item.principal_key,
            # The entries the frozen evidence named, not whatever the ACL holds now. A
            # proposal built from current state would silently retarget itself between the
            # review and the change window.
            ace_keys=tuple(grant.ace_key for grant in item.grants),
            details=dict(details or {}),
            proposed_by_subject=actor.subject,
            proposed_at=now,
        )
        await self._repository.insert_proposal(proposal)
        await self._append(
            actor,
            chain_key=campaign_chain(item.campaign_id),
            event_type=GovernanceEventType.REMEDIATION_PROPOSED,
            occurred_at=now,
            campaign_id=item.campaign_id,
            item_id=item_id,
            decision_id=current.decision_id,
            payload={
                "proposal_id": str(proposal.proposal_id),
                "action": action.value,
                "target_key": item.target_key,
                "principal_key": item.principal_key,
                "ace_keys": list(proposal.ace_keys),
                "assignment_id": str(assignment.assignment_id),
            },
        )
        await self._session.commit()
        return proposal

    async def list_proposals(
        self, campaign_id: UUID, *, item_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> tuple[tuple[RemediationProposal, ...], bool]:
        await self.get_campaign(campaign_id)
        return await self._repository.list_proposals(
            campaign_id, item_id=item_id, limit=limit, offset=offset
        )

    # --------------------------------------------------------------- status and audit

    async def campaign_status(self, campaign_id: UUID) -> CampaignStatusReport:
        campaign = await self.get_campaign(campaign_id)
        now = self._now()
        counts = await self._repository.status_counts(campaign_id)
        progress = await self._repository.progress_by_assignment(campaign_id)
        late = await self._repository.late_decisions_by_assignment(campaign_id)
        assignments = await self._repository.list_assignments(campaign_id)
        head = await self._chain_head(campaign_id)
        reviewers = tuple(
            _reviewer_progress(assignment, campaign, progress, late, now)
            for assignment in assignments
        )
        return CampaignStatusReport(
            campaign=campaign,
            counts=counts,
            unassigned_items=progress.get(None, (0, 0))[0],
            overdue=campaign.is_overdue(now),
            reviewers=reviewers,
            audit_head=head,
        )

    async def reviewer_queue(
        self, subject: str, *, include_closed: bool = False
    ) -> tuple[QueueEntry, ...]:
        """Every campaign this reviewer has outstanding work in, most urgent first.

        Resolved through **active** assignments, so a reviewer stood down from a campaign
        stops seeing it without any item being edited — the same rule ``mine=true`` on the
        item list follows, and stated once so the queue and the list cannot disagree.

        Closed and canceled campaigns are excluded by default. Their decisions stay readable
        through the campaign itself; a queue is a list of things to do, and a campaign that
        accepts no decisions has nothing on it to do.
        """
        statuses = (
            (CampaignStatus.ACTIVE, CampaignStatus.CLOSED)
            if include_closed
            else (CampaignStatus.ACTIVE,)
        )
        counts = await self._repository.queue_counts_for(subject, statuses)
        if not counts:
            return ()
        now = self._now()
        entries: list[QueueEntry] = []
        for campaign_id, (assigned, decided, due_at) in counts.items():
            campaign = await self._repository.get_campaign(campaign_id)
            if campaign is None:  # pragma: no cover - the foreign key prevents this
                continue
            deadline = due_at or campaign.due_at
            entries.append(
                QueueEntry(
                    campaign=campaign,
                    assigned=assigned,
                    decided=decided,
                    due_at=deadline,
                    overdue=(
                        deadline is not None
                        and now > deadline
                        and assigned > decided
                        and campaign.status is CampaignStatus.ACTIVE
                    ),
                )
            )
        # Overdue first, then by deadline, then by how much is left. A reviewer opening this
        # page should not have to sort it to find out what is already late.
        entries.sort(
            key=lambda entry: (
                not entry.overdue,
                entry.due_at or dt.datetime.max.replace(tzinfo=dt.UTC),
                -entry.pending,
            )
        )
        return tuple(entries)

    # --------------------------------------------------------------- drift and context

    async def item_context(self, item_id: UUID) -> ReviewContext:
        """One item with the evidence a reviewer needs around it. See :mod:`.review`."""
        item = await self.get_item(item_id)
        campaign = await self.get_campaign(item.campaign_id)
        return await ReviewContextService(self._session, now=self._fixed_now).context_for(
            item, campaign
        )

    async def campaign_drift(
        self, campaign_id: UUID, *, limit: int = MAX_DRIFT_ITEMS, offset: int = 0
    ) -> DriftReport:
        """How far a page of a campaign's items has drifted from its baseline.

        Bounded, and the report says by how much: comparing a five-thousand-item campaign on
        every page load would re-read every target in it. ``covered`` against ``total_items``
        is what keeps "nothing has changed" from meaning "nothing in the first five hundred".
        """
        await self.get_campaign(campaign_id)
        page_size = max(1, min(limit, MAX_DRIFT_ITEMS))
        page = await self._repository.items_for_drift(campaign_id, limit=page_size, offset=offset)
        now = self._now()
        context = ReviewContextService(self._session, now=self._fixed_now)
        drifts = await context.drift_for(page.items, at=now)

        counts: dict[str, int] = {verdict.value: 0 for verdict in DriftVerdict}
        drifted: list[tuple[ReviewItem, ItemDrift]] = []
        for item in page.items:
            drift = drifts[item.item_id]
            counts[drift.verdict.value] += 1
            if drift.has_drifted:
                drifted.append((item, drift))
        return DriftReport(
            campaign_id=campaign_id,
            compared_at=now,
            total_items=page.total if page.total is not None else len(page.items),
            covered=len(page.items),
            counts=counts,
            drifted=tuple(drifted),
        )

    # ------------------------------------------------------------------ bulk decisions

    async def bulk_decide(
        self,
        actor: Actor,
        campaign_id: UUID,
        *,
        item_ids: Sequence[UUID],
        decision: DecisionKind,
        rationale: str | None = None,
    ) -> BulkOutcome:
        """Answer several items at once, writing each decision and each audit event separately.

        **Every gate the single-item path applies still applies, per item.** The campaign
        must be active, the caller must hold the assignment on each item, the rationale must
        satisfy the campaign's comment requirement. Nothing here is a shortcut around
        :meth:`decide`'s rules; it is the same rules applied to a set in one transaction,
        so that a batch either lands whole or not at all.

        **Homogeneity is what makes the batch honest**, and it is four rules:

        * *One campaign.* Ids naming another campaign come back as not found rather than
          being acted on quietly.
        * *One target kind, and one shared axis* — either one principal across many targets,
          or one target across many principals. "Alice on eleven folders" and "eleven people
          on one folder" are questions somebody can answer in one judgment; an arbitrary
          basket of grants is eleven judgments wearing one click.
        * *Nothing already decided.* Changing one's mind is a supersession, which replaces a
          specific attestation with a specific reason, and doing that in bulk is how a
          reviewer overwrites an answer they have not looked at.
        * *Nothing drifted.* An item whose grant has changed since the baseline is precisely
          the one that needs reading, and a batch is where it would not be read. Refused by
          id, so the reviewer is told which ones to open.

        Each item still gets its own decision row, its own audit event and its own evidence
        digest in that event, so the record afterwards is indistinguishable from the same
        decisions made one at a time — which is what "individually auditable" has to mean.
        """
        campaign = await self.get_campaign(campaign_id)
        if not campaign.accepts_decisions:
            raise GovernanceConflict(
                f"Campaign {campaign_id} is {campaign.status.value} and accepts no "
                "decisions. Only an active campaign does."
            )
        requested = list(dict.fromkeys(item_ids))
        if not requested:
            raise NotHomogeneous(
                "A bulk decision needs at least one item. An empty batch would record an "
                "audit event saying somebody decided nothing."
            )
        if len(requested) > MAX_BULK_ITEMS:
            raise NotHomogeneous(
                f"A bulk decision may cover {MAX_BULK_ITEMS} items and this one names "
                f"{len(requested)}. The ceiling is on how much one click may assert, not on "
                "what the database can write: select fewer, or use a narrower filter."
            )

        items = await self._repository.items_by_ids(campaign_id, requested)
        found = {item.item_id for item in items}
        missing = [item_id for item_id in requested if item_id not in found]
        if missing:
            raise GovernanceNotFound(
                f"{len(missing)} of the {len(requested)} items named are not in campaign "
                f"{campaign_id}: {', '.join(str(item_id) for item_id in missing[:5])}"
                + ("…" if len(missing) > 5 else "")
            )
        _require_homogeneous(items)

        decided = [item for item in items if item.status is ReviewItemStatus.DECIDED]
        if decided:
            raise NotHomogeneous(
                f"{len(decided)} of these items already carry a decision. Changing an "
                "answer supersedes a specific attestation and needs its own reason, so it "
                "is done one item at a time: "
                + ", ".join(str(item.item_id) for item in decided[:5])
                + ("…" if len(decided) > 5 else "")
            )

        drifts = await ReviewContextService(self._session, now=self._fixed_now).drift_for(
            items, at=self._now()
        )
        moved = [item for item in items if drifts[item.item_id].has_drifted]
        if moved:
            raise NotHomogeneous(
                f"{len(moved)} of these items have changed since the campaign was frozen, "
                "which is exactly the case that needs reading rather than batching. Open "
                "them individually: "
                + ", ".join(str(item.item_id) for item in moved[:5])
                + ("…" if len(moved) > 5 else "")
            )

        cleaned = validate_rationale(decision, rationale, requirement=campaign.comment_requirement)
        now = self._now()
        records: list[ReviewDecision] = []
        for item in items:
            assignment = await self._assignment_for(item, actor)
            deadline = assignment.due_at or campaign.due_at
            record = ReviewDecision(
                decision_id=uuid4(),
                item_id=item.item_id,
                campaign_id=campaign_id,
                decision=decision,
                rationale=cleaned,
                decided_by_subject=actor.subject,
                decided_by_display_name=actor.display_name,
                decided_at=now,
                decided_late=deadline is not None and now > deadline,
            )
            await self._repository.insert_decision(record)
            await self._repository.mark_item_decided(
                item.item_id, decision_id=record.decision_id, at=now
            )
            await self._append(
                actor,
                chain_key=campaign_chain(campaign_id),
                event_type=GovernanceEventType.DECISION_RECORDED,
                occurred_at=now,
                campaign_id=campaign_id,
                item_id=item.item_id,
                decision_id=record.decision_id,
                payload={
                    "decision": decision.value,
                    "evidence_digest": item.evidence_digest,
                    "target_kind": item.target_kind.value,
                    "target_key": item.target_key,
                    "principal_key": item.principal_key,
                    "certainty": item.certainty.value,
                    "late": record.decided_late,
                    "assignment_id": str(assignment.assignment_id),
                    "has_rationale": cleaned is not None,
                    # Present on every event in the batch, so that an auditor reading one
                    # event can see it was one of many and find the others. Without it a
                    # bulk certification is indistinguishable from somebody working fast.
                    "bulk": True,
                    "bulk_size": len(items),
                },
            )
            records.append(record)

        await self._session.commit()
        logger.info(
            "governance.decision.bulk",
            extra={
                "campaign_id": str(campaign_id),
                "decision": decision.value,
                "items": len(records),
                "subject": actor.subject,
            },
        )
        return BulkOutcome(
            campaign_id=campaign_id,
            decision=decision,
            decisions=tuple(records),
            superseded=0,
        )

    async def _chain_head(self, campaign_id: UUID) -> str | None:
        events, _ = await self._repository.campaign_events(campaign_id, limit=1_000)
        return events[-1].digest if events else None

    async def audit_trail(
        self, campaign_id: UUID, *, limit: int = 500, offset: int = 0
    ) -> tuple[tuple[GovernanceEvent, ...], bool, ChainVerification]:
        """A campaign's events, and whether its chain still verifies.

        The verification always reads the **whole** chain, whatever page was asked for: a
        chain verified over a window would report "intact" for a trail whose earlier events
        had been rewritten, which is worse than not checking.
        """
        page, has_more = await self._repository.campaign_events(
            campaign_id, limit=limit, offset=offset
        )
        whole, _ = await self._repository.campaign_events(campaign_id, limit=100_000)
        return page, has_more, verify_chain(whole)

    async def verify_campaign(self, campaign_id: UUID) -> CampaignVerification:
        """Regenerate the campaign from its baseline and compare with what it holds."""
        campaign = await self.get_campaign(campaign_id)
        if not campaign.is_generated:
            raise GovernanceConflict(
                f"Campaign {campaign_id} has not been generated, so there is no frozen item "
                "set to verify."
            )
        try:
            result = await self._generate_from_baseline(campaign)
        except (CampaignTooLarge, ScopeTooLarge) as exc:
            # The estate grew past a ceiling since the campaign was cut. That is itself the
            # answer: the campaign is no longer reproducible with the limits in force, and
            # saying so beats reporting every item as missing.
            raise GovernanceConflict(
                f"Campaign {campaign_id} can no longer be regenerated within the current "
                f"limits, so its reproducibility cannot be checked: {exc}"
            ) from exc

        stored = await self._repository.all_item_fingerprints(campaign_id)
        recomputed = {item.natural_key: item.digest for item in result.items}
        missing = tuple(sorted(set(recomputed) - set(stored)))
        unexpected = tuple(sorted(set(stored) - set(recomputed)))
        changed = tuple(
            sorted(key for key in set(stored) & set(recomputed) if stored[key] != recomputed[key])
        )
        reproducible = (
            not missing
            and not unexpected
            and not changed
            and campaign.snapshot_digest == result.digest
        )
        return CampaignVerification(
            campaign_id=campaign_id,
            reproducible=reproducible,
            stored_digest=campaign.snapshot_digest,
            recomputed_digest=result.digest,
            stored_item_count=len(stored),
            recomputed_item_count=len(recomputed),
            missing=missing,
            unexpected=unexpected,
            evidence_changed=changed,
            explanation=_verification_explanation(reproducible, missing, unexpected, changed),
        )

    # ---------------------------------------------------------------------------- audit

    async def _append(
        self,
        actor: Actor,
        *,
        chain_key: str,
        event_type: GovernanceEventType,
        occurred_at: dt.datetime,
        campaign_id: UUID | None = None,
        item_id: UUID | None = None,
        decision_id: UUID | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> GovernanceEvent:
        return await self._repository.append_event(
            chain_key=chain_key,
            event_type=event_type,
            occurred_at=occurred_at,
            actor_subject=actor.subject,
            actor_display_name=actor.display_name,
            actor_roles=actor.roles,
            campaign_id=campaign_id,
            item_id=item_id,
            decision_id=decision_id,
            payload=dict(payload or {}),
        )


_TRANSITION_EVENTS: dict[CampaignStatus, GovernanceEventType] = {
    CampaignStatus.ACTIVE: GovernanceEventType.CAMPAIGN_ACTIVATED,
    CampaignStatus.CLOSED: GovernanceEventType.CAMPAIGN_CLOSED,
    CampaignStatus.CANCELED: GovernanceEventType.CAMPAIGN_CANCELED,
}

#: Which scope kinds an assignment may narrow by, per campaign focus. A resource-focused
#: campaign's items are grouped by target, so a principal-scoped assignment would select
#: across every share at once -- which is a different campaign, not a slice of this one.
_ASSIGNABLE_SCOPES: dict[CampaignFocus, frozenset[ReviewScopeKind]] = {
    CampaignFocus.RESOURCE: frozenset(
        {ReviewScopeKind.SERVER, ReviewScopeKind.SHARE, ReviewScopeKind.DIRECTORY_TREE}
    ),
    CampaignFocus.PRINCIPAL: frozenset({ReviewScopeKind.PRINCIPAL}),
}


def _reviewer_progress(
    assignment: ReviewAssignment,
    campaign: ReviewCampaign,
    progress: Mapping[UUID | None, tuple[int, int]],
    late: Mapping[UUID | None, int],
    now: dt.datetime,
) -> ReviewerProgress:
    """One reviewer's row, including whether they are behind.

    The deadline is the assignment's own where it has one, and the campaign's otherwise. A
    reviewer given a shorter deadline than the campaign is late against theirs, and a
    reviewer given none inherits the campaign's rather than being exempt from every date.
    """
    assigned, decided = progress.get(assignment.assignment_id, (0, 0))
    deadline = assignment.due_at or campaign.due_at
    return ReviewerProgress(
        assignment_id=assignment.assignment_id,
        reviewer_subject=assignment.reviewer_subject,
        reviewer_display_name=assignment.reviewer_display_name,
        scope=assignment.scope,
        due_at=deadline,
        assigned=assigned,
        decided=decided,
        late_decisions=late.get(assignment.assignment_id, 0),
        overdue=(
            deadline is not None
            and now > deadline
            and assigned > decided
            and assignment.is_active
            and campaign.status is CampaignStatus.ACTIVE
        ),
    )


def _require_homogeneous(items: Sequence[ReviewItem]) -> None:
    """Refuse a batch that is more than one question. See :meth:`bulk_decide`."""
    kinds = {item.target_kind for item in items}
    if len(kinds) > 1:
        raise NotHomogeneous(
            "These items sit on different kinds of access-control list — "
            + ", ".join(sorted(kind.value for kind in kinds))
            + ". Share permissions and file-system permissions are removed in different "
            "places, often by different people, so certifying both in one act asserts more "
            "than one thing."
        )
    principals = {item.principal_key for item in items}
    targets = {item.target_key for item in items}
    if len(principals) > 1 and len(targets) > 1:
        raise NotHomogeneous(
            f"These {len(items)} items cover {len(principals)} principals across "
            f"{len(targets)} targets, which is not one question. A batch must share an "
            "axis: one principal over many targets, or one target over many principals. "
            "Anything else is several judgments recorded as one."
        )


def _verification_explanation(
    reproducible: bool,
    missing: Sequence[tuple[str, str, str]],
    unexpected: Sequence[tuple[str, str, str]],
    changed: Sequence[tuple[str, str, str]],
) -> str:
    """A sentence an auditor can act on, rather than three numbers.

    Divergence here is not a rendering difference. ``object_versions`` is append-only and a
    version's interval is fixed once written, so a baseline that produces a different answer
    today means the timeline itself changed -- history edited, restored from a partial
    backup, or pruned by retention past the baseline instant. The message names those
    causes, because an auditor reading "42 unexpected" has no way to guess them.
    """
    if reproducible:
        return (
            "This campaign is reproducible: regenerating it from its baseline instant "
            "produces exactly the items it holds, with the same evidence."
        )
    parts: list[str] = []
    if missing:
        parts.append(f"{len(missing)} item(s) the baseline produces now are not in the campaign")
    if unexpected:
        parts.append(f"{len(unexpected)} item(s) in the campaign are no longer produced")
    if changed:
        parts.append(f"{len(changed)} item(s) have different evidence than when frozen")
    return (
        "This campaign is NOT reproducible from its baseline: "
        + "; ".join(parts)
        + ". Because object_versions is append-only and a version's interval is fixed once "
        "written, this means the timeline itself changed since the campaign was cut — "
        "history edited directly, restored from a partial backup, or pruned by retention "
        "past the baseline instant. The decisions remain attributable; what can no longer "
        "be shown is that they were made against the state the estate actually held."
    )
