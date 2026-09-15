r"""The governance HTTP surface: campaigns, assignments, items, decisions, owners, audit.

Three capabilities divide these routes, and the split is the phase's authorization model
made visible in one file:

* ``governance:read`` — see campaigns, items, decisions, ownership and the audit trail.
* ``governance:review`` — record an attestation. Admits the request; it does **not** grant
  authority over any particular item. The item must also be assigned to the caller, which
  :mod:`app.governance.service` checks against the database. A reviewer who is not the
  assigned one gets 403 from the service even though the route let them in.
* ``governance:manage`` — create, scope, generate, assign and close campaigns, and record
  ownership. Deliberately does **not** include ``governance:review``: whoever chooses the
  questions does not also give the answers. See ADR-0029.

Every capability is declared at the **route**, not as a handler parameter, for the reason
``app/api/scan_runs.py`` states: route dependencies are solved before the handler's own, so a
request that authorization will refuse is refused before it costs a database session.
``tests/api/test_authorization.py`` holds that in place for every route here.

Nothing in this module writes a collected fact. The responses are ADG's own records; where
one sits beside an observation — a resource's ADG owner beside the owner SID Windows reports
— both are rendered, separately and labelled, because the gap between them is a finding
rather than a discrepancy to reconcile.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import AwareDatetime, BaseModel, Field

from app.api.access import RightsView, render_rights
from app.api.deps import PRINTABLE_IDENTIFIER, Session
from app.api.graph import PrincipalSummary, principal_summary
from app.api.pagination import (
    MAX_LIMIT,
    PageInfo,
    decode_offset_cursor,
    encode_offset_cursor,
    normalize_limit,
)
from app.auth.dependencies import CurrentPrincipal, requires
from app.auth.principal import AuthenticatedPrincipal
from app.auth.roles import Capability
from app.domain import (
    CampaignFocus,
    CampaignStatus,
    CommentRequirement,
    DecisionKind,
    OwnershipRole,
    RemediationAction,
    ReviewItemStatus,
    ReviewScopeKind,
    ReviewTargetKind,
)
from app.governance.audit import GovernanceEvent
from app.governance.drift import GrantChange, ItemDrift
from app.governance.model import (
    MAX_RATIONALE_LENGTH,
    CampaignScope,
    GenerationOptions,
    GrantEvidence,
    RemediationProposal,
    ResourceOwner,
    ReviewAssignment,
    ReviewCampaign,
    ReviewDecision,
    ReviewItem,
)
from app.governance.review import (
    MAX_DRIFT_ITEMS,
    AccessAt,
    EntryRemoval,
    GroupRoute,
    LastChange,
    Reach,
    RelatedFinding,
    ReviewContext,
    ReviewContextService,
)
from app.governance.service import (
    MAX_BULK_ITEMS,
    Actor,
    BulkOutcome,
    CampaignStatusReport,
    CampaignVerification,
    DriftReport,
    GovernanceService,
    QueueEntry,
    ReviewerProgress,
)

router = APIRouter(prefix="/api/v1/governance", tags=["governance"])

#: Reading governance records. Held by auditors, reviewers and governance administrators —
#: and deliberately **not** by a plain viewer: a decision rationale can name a person and say
#: something about them that no access-control list ever would.
READ = Depends(requires(Capability.GOVERNANCE_READ))

#: Recording an attestation. The assignment check in the service is the second gate.
REVIEW = Depends(requires(Capability.GOVERNANCE_REVIEW))

#: Running campaigns. Does not permit answering one.
MANAGE = Depends(requires(Capability.GOVERNANCE_MANAGE))

MAX_AUDIT_PAGE: Final = 500

CampaignIdPath = Annotated[UUID, Path(description="The campaign's identifier.")]
ItemIdPath = Annotated[UUID, Path(description="The review item's identifier.")]
OwnerIdPath = Annotated[UUID, Path(description="The ownership record's identifier.")]

#: Every free-text key that reaches a query. Phase 6D found that a NUL byte survives every
#: parser and reaches psycopg as ``PostgreSQL text fields cannot contain NUL (0x00) bytes``,
#: which is a 500 for a malformed request; these are rejected before the round trip.
KeyString = Annotated[str, Field(min_length=1, max_length=512, pattern=PRINTABLE_IDENTIFIER)]


def _actor(principal: AuthenticatedPrincipal) -> Actor:
    """The caller, as the audit trail will record them.

    The roles are captured **here**, from the verified token, rather than looked up later:
    "Alice decided this" and "Alice, who then held the reviewer role, decided this" are
    different claims, and only the second survives her assignments being changed.
    """
    return Actor(
        subject=principal.subject,
        display_name=principal.display_name,
        email=principal.email,
        roles=tuple(sorted(role.value for role in principal.roles)),
    )


# ------------------------------------------------------------------------------ requests


class ScopeBody(BaseModel):
    kind: ReviewScopeKind
    key: KeyString


class GenerationOptionsBody(BaseModel):
    """What the campaign will deliberately not ask about. All three are reported back."""

    include_inherited: bool = Field(
        default=False,
        description=(
            "Include entries inherited from an ancestor. Off by default: an inherited entry "
            "cannot be removed where it sits, so the items would not be actionable there, "
            "and the count multiplies by roughly the depth of the tree."
        ),
    )
    include_builtin: bool = Field(
        default=False,
        description=(
            "Include well-known trustees such as SYSTEM and BUILTIN\\Administrators. Off by "
            "default: they appear on nearly every descriptor and reviewing them thousands of "
            "times buries the grants that matter."
        ),
    )
    include_deny: bool = Field(
        default=True,
        description=(
            "Include Deny entries. On by default: a deny is part of the grant, and an item "
            "showing only the allow would ask somebody to certify access that does not exist."
        ),
    )


class CreateCampaignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    focus: CampaignFocus = Field(
        description=(
            "Which end of the grant the campaign enumerates, and therefore what it claims to "
            "be complete about. 'resource' scopes select shares and directories; 'principal' "
            "scopes select one principal wherever it is named."
        )
    )
    scopes: list[ScopeBody] = Field(min_length=1, max_length=50)
    baseline_at: AwareDatetime = Field(
        description=(
            "The instant the campaign is frozen against. Items are generated from the state "
            "observed at this moment, so the campaign stays reproducible however the estate "
            "changes afterwards. Must be in the past and must carry a time zone."
        )
    )
    description: str | None = Field(default=None, max_length=2000)
    due_at: AwareDatetime | None = None
    options: GenerationOptionsBody = Field(default_factory=GenerationOptionsBody)
    comment_requirement: CommentRequirement = Field(
        default=CommentRequirement.STANDARD,
        description=(
            "How hard this campaign insists a decision explain itself. 'standard' requires a "
            "rationale on every decision but 'certify'; 'always' requires one on every "
            "decision. There is no value that makes a rationale optional for a revocation: "
            "the database enforces that rule independently, and a campaign setting able to "
            "switch it off would be a way to record unexplained removals."
        ),
    )


class AssignReviewerRequest(BaseModel):
    reviewer_subject: str = Field(
        min_length=1,
        max_length=320,
        pattern=PRINTABLE_IDENTIFIER,
        description=(
            "The reviewer's authentication subject — the 'sub'/'oid' their token carries, or "
            "their development account name. Not a Windows SID: the principals in a campaign "
            "are what is being reviewed, and the reviewer is a person who signs in to ADG."
        ),
    )
    reviewer_display_name: str | None = Field(default=None, max_length=200)
    reviewer_email: str | None = Field(default=None, max_length=320)
    scope: ScopeBody | None = Field(
        default=None,
        description="Narrow the assignment to part of the campaign. Omit for all of it.",
    )
    due_at: AwareDatetime | None = None


class SubmitDecisionRequest(BaseModel):
    decision: DecisionKind
    rationale: str | None = Field(
        default=None,
        max_length=MAX_RATIONALE_LENGTH,
        description=(
            "Required for every decision but 'certify', and for that one too when the "
            "campaign's comment_requirement is 'always'. An unexplained removal cannot be "
            "defended to the person who loses access."
        ),
    )


class BulkDecisionRequest(BaseModel):
    """One decision applied to several items that are the same question.

    Every gate the single-item route applies still applies to every item here: the campaign
    must be active, the caller must hold the assignment on each, and the rationale rules are
    the campaign's. The batch is refused whole rather than partly applied.
    """

    item_ids: list[UUID] = Field(
        min_length=1,
        max_length=MAX_BULK_ITEMS,
        description=(
            "Items to answer. They must belong to this campaign, be undecided, be unchanged "
            "since the baseline, and share an axis — one principal across many targets, or "
            "one target across many principals."
        ),
    )
    decision: DecisionKind
    rationale: str | None = Field(
        default=None,
        max_length=MAX_RATIONALE_LENGTH,
        description=(
            "Applied to every item in the batch. One reason covering eleven grants is "
            "honest when the eleven are one question, which is what the homogeneity rule "
            "enforces."
        ),
    )


class ProposeRemediationRequest(BaseModel):
    action: RemediationAction
    details: dict[str, Any] = Field(default_factory=dict, description="Free-form context.")


class AssignOwnerRequest(BaseModel):
    target_kind: ReviewTargetKind
    target_key: KeyString
    ownership_role: OwnershipRole = OwnershipRole.OWNER
    owner_subject: str | None = Field(default=None, max_length=320, pattern=PRINTABLE_IDENTIFIER)
    owner_principal_key: str | None = Field(
        default=None, max_length=512, pattern=PRINTABLE_IDENTIFIER
    )
    owner_display_name: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


# ----------------------------------------------------------------------------- responses


class ScopeView(BaseModel):
    kind: str
    key: str


class CampaignView(BaseModel):
    """A campaign, including what it left out."""

    campaign_id: UUID
    name: str
    description: str | None
    focus: str
    status: str
    baseline_at: dt.datetime
    due_at: dt.datetime | None
    scopes: list[ScopeView]
    options: GenerationOptionsBody
    comment_requirement: str = Field(
        description=(
            "'standard' — a rationale on every decision but 'certify'; 'always' — one on "
            "every decision. Never less than 'standard': the database enforces that floor."
        )
    )
    item_count: int
    excluded_counts: dict[str, int] = Field(
        description=(
            "Grants present at the baseline that produced no item, by reason. Reported so "
            "that '47 of 47 certified' is never read as coverage of everything: a campaign "
            "excluding inherited entries and built-in trustees reviewed less than the estate "
            "holds."
        )
    )
    snapshot_digest: str | None = Field(
        default=None,
        description=(
            "The digest of the frozen item set. Worth recording outside ADG: it is what a "
            "later verification compares against."
        ),
    )
    generated_at: dt.datetime | None
    activated_at: dt.datetime | None
    closed_at: dt.datetime | None
    closed_by_subject: str | None
    created_by_subject: str
    created_at: dt.datetime


class CampaignListResponse(BaseModel):
    items: list[CampaignView]
    page: PageInfo


class GenerationResponse(BaseModel):
    campaign: CampaignView
    item_count: int
    excluded_counts: dict[str, int]
    snapshot_digest: str
    regenerated: bool = Field(
        description="True when this replaced an earlier draft generation rather than the first."
    )


class AssignmentView(BaseModel):
    assignment_id: UUID
    campaign_id: UUID
    reviewer_subject: str
    reviewer_display_name: str | None
    reviewer_email: str | None
    scope: ScopeView | None
    due_at: dt.datetime | None
    assigned_by_subject: str
    assigned_at: dt.datetime
    revoked_at: dt.datetime | None
    revoked_by_subject: str | None
    active: bool


class AssignmentCreatedResponse(BaseModel):
    assignment: AssignmentView
    items_attached: int = Field(
        description=(
            "Items now pointing at this assignment. Includes any that were attached to "
            "another assignment before: an assignment answers who is being asked now, and "
            "two reviewers each believing an item is theirs is worse than one being replaced."
        )
    )


class AssignmentListResponse(BaseModel):
    items: list[AssignmentView]


class GrantView(BaseModel):
    """One access-control entry, exactly as it stood at the baseline."""

    ace_key: str
    trustee_sid: str
    trustee_key: str
    ace_type: str
    access_mask: int | None
    permission: str | None
    ace_flags: int | None
    source: str | None
    inherited_from: str | None
    order_index: int | None
    inherited: bool
    deny: bool
    version_id: int = Field(
        description=(
            "The object_versions row this entry was copied from. Re-reading it reproduces "
            "this evidence exactly, which is what makes a campaign checkable after the fact."
        )
    )
    observed_from: dt.datetime
    last_confirmed_at: dt.datetime
    certainty: str = Field(
        description=(
            "How firmly this entry is known to have held at the baseline: observed, "
            "inferred, backfilled, or unobserved. Never omitted — certifying reconstructed "
            "state as though it had been watched is the one way a review can make an audit "
            "worse rather than better."
        )
    )


class ItemView(BaseModel):
    item_id: UUID
    campaign_id: UUID
    focus: str
    target_kind: str
    target_key: str
    target_path: str | None
    principal_key: str
    principal_sid: str
    principal_display_name: str | None = Field(
        default=None,
        description=(
            "The name this principal had at the baseline, where one was known. Null means "
            "the SID was named on an access-control list and nothing had described it then — "
            "an orphaned-SID finding, not a rendering gap."
        ),
    )
    grants: list[GrantView]
    evidence_digest: str
    certainty: str
    status: str
    assignment_id: UUID | None
    current_decision_id: UUID | None
    decided_at: dt.datetime | None
    created_at: dt.datetime


class ItemListResponse(BaseModel):
    items: list[ItemView]
    page: PageInfo


class DecisionView(BaseModel):
    decision_id: UUID
    item_id: UUID
    campaign_id: UUID
    decision: str
    rationale: str | None
    decided_by_subject: str
    decided_by_display_name: str | None
    decided_at: dt.datetime
    decided_late: bool
    supersedes_decision_id: UUID | None
    superseded_at: dt.datetime | None
    superseded_by_decision_id: UUID | None
    current: bool


class GrantChangeView(BaseModel):
    """One difference between the frozen evidence and the estate now."""

    kind: str = Field(description="added, removed, or changed.")
    ace_key: str
    fields: list[str] = Field(
        default_factory=list,
        description="For 'changed', the fields that differ, by their internal names.",
    )
    field_labels: list[str] = Field(
        default_factory=list,
        description=(
            "The same fields in words — 'rights', 'allow or deny'. Rendered here rather than "
            "in each client so that every screen calls the same difference the same thing."
        ),
    )
    before: GrantView | None
    after: GrantView | None


class DriftView(BaseModel):
    """What has become of this item's grant since the campaign was frozen.

    Computed on read and stored nowhere. The item is **never** rewritten: the frozen evidence
    is what the decision is about and what the audit trail records the digest of, so
    refreshing it would quietly turn every past attestation into a statement about whatever
    the grant became.
    """

    verdict: str = Field(
        description=(
            "unchanged, modified, removed, or unobserved. 'removed' is a measured absence — "
            "ADG read the target and found no such entry. 'unobserved' means it holds "
            "nothing covering the instant and cannot say, which is never rendered as a "
            "removal."
        )
    )
    has_drifted: bool = Field(
        description="True for 'modified' and 'removed' only. Not knowing is not drift."
    )
    summary: str = Field(
        description=(
            "One sentence, written for the person deciding. Carried rather than assembled by "
            "each client: the distinction between 'it was removed' and 'nobody has looked' "
            "is the one a client would get wrong, and it changes what a reviewer does next."
        )
    )
    compared_at: dt.datetime
    baseline_digest: str
    current_content_digest: str | None
    changes: list[GrantChangeView]
    current_grants: list[GrantView]
    current_certainty: str | None
    target_present: bool | None = Field(
        description=(
            "Whether the target — the share or directory — was itself there. False with a "
            "'removed' verdict means the whole share is gone rather than one grant withdrawn."
        )
    )
    target_certainty: str | None = Field(
        description=(
            "How firmly the target's own state is known now. Anything but 'observed' beside "
            "an 'unchanged' verdict means the comparison is against ADG's last reading rather "
            "than against the estate."
        )
    )
    evidence_reissued: bool = Field(
        description=(
            "The grant is identical and the timeline rows behind it are not: the entries were "
            "removed and restored, or rewritten. Not drift — the reviewer is certifying the "
            "same thing — but the change feed has a story about it."
        )
    )


class ItemDetailResponse(BaseModel):
    item: ItemView
    decisions: list[DecisionView] = Field(
        description=(
            "Every decision ever recorded on this item, oldest first. A superseded decision "
            "stays: 'certified on the 3rd, revoked on the 9th' and 'revoked on the 9th' are "
            "different histories and only the first lets an auditor ask what changed."
        )
    )
    drift: DriftView


class DriftCountsView(BaseModel):
    unchanged: int
    modified: int
    removed: int
    unobserved: int


class DriftItemView(BaseModel):
    item: ItemView
    drift: DriftView


class DriftReportResponse(BaseModel):
    """A campaign's baseline drift, over a bounded page of its items."""

    campaign_id: UUID
    compared_at: dt.datetime
    total_items: int
    covered: int = Field(
        description=(
            "Items actually compared. Reported beside the total because drift is computed "
            "against live state on every request: 'nothing has changed' must never be read "
            "as more than 'nothing in the part that was checked'."
        )
    )
    has_more: bool
    counts: DriftCountsView
    drifted: list[DriftItemView] = Field(
        description=(
            "Only the items that moved, in item order. Unchanged items are counted and not "
            "listed — a drift report that returned everything would be the item list."
        )
    )


class AccessSummaryView(BaseModel):
    """One effective-access answer about this item's principal and target."""

    at: dt.datetime
    available: bool
    unavailable_reason: str | None
    has_access: bool | None
    rights: RightsView | None
    certainty: str | None = Field(
        description=(
            "The engine's own certainty about the answer. For the baseline answer this is "
            "the weakest certainty of any historical version it read: an answer reconstructed "
            "from backfilled state is a weaker claim than one over observed state."
        )
    )
    limiting_layer: str | None = Field(
        description="Which layer held the rights down: smb_share, ntfs, both, none, unknown."
    )


class GroupRouteView(BaseModel):
    """One group through which this principal *also* reaches the target."""

    principal: PrincipalSummary
    depth: int
    chain: list[str]
    rights: RightsView
    layer: str
    inherited: bool


class EntryRemovalView(BaseModel):
    """What removing one of this item's own entries would do, on its own."""

    ace_key: str
    rights_removed: RightsView
    rights_after: RightsView
    revokes_all_access: bool
    changes_nothing: bool = Field(
        description=(
            "The entry settles nothing today — an earlier entry in the same list had already "
            "decided every right it names. Worth certifying away regardless, and worth not "
            "mistaking for a grant that matters."
        )
    )
    alternate_paths: int


class ReachView(BaseModel):
    """How the principal reaches the target, split the way a revoke decision needs."""

    available: bool
    unavailable_reason: str | None
    direct_paths: int = Field(
        description="Granting paths landing on an entry that names this principal itself."
    )
    group_paths: int = Field(
        description="Granting paths landing on an entry that names a group they are in."
    )
    routes: list[GroupRouteView]
    removals: list[EntryRemovalView]
    removing_reviewed_entries_leaves_access: bool | None = Field(
        description=(
            "True when removing this item's entries would leave the principal with access "
            "anyway — through a group, or through another entry. The most consequential "
            "field on this screen: a 'revoke' recorded in the belief that access ends, on a "
            "grant also held through a group, is an attestation that says something untrue. "
            "Null means no explanation could be produced; it is never false by default, "
            "because 'removing it works' is the reassuring answer and has to be measured."
        )
    )
    truncated: bool


class RelatedFindingView(BaseModel):
    """One open risk finding whose subject names this target, this principal, or both."""

    finding_key: str
    rule_id: str
    status: str
    severity: str
    band: str
    confidence: str
    relation: str = Field(
        description=(
            "'access' — about this principal on this place, the tightest match; 'target' — "
            "about the place; 'principal' — about the principal elsewhere. Findings are "
            "ordered by this before severity, because a critical finding about a group "
            "somewhere else matters less to this decision than a medium one about this grant."
        )
    )
    detected_at: dt.datetime
    first_detected_at: dt.datetime
    resource_key: str | None
    share_key: str | None
    principal_key: str | None
    detail: dict[str, Any]


class LastChangeView(BaseModel):
    """One recent transition of one of this item's entries, as the change feed classified it."""

    kind: str
    key: str
    action: str
    significance: str
    severity: str
    direction: str
    reasons: list[str]
    changed_after: dt.datetime | None = Field(
        description="The last instant the previous state was confirmed. The change is later."
    )
    changed_at_or_before: dt.datetime | None = Field(
        description="The observation that contradicted it. The change had happened by then."
    )
    is_exact: bool


class ItemContextResponse(BaseModel):
    """One item with the evidence a reviewer needs to answer it without opening AD tools.

    Everything here is context; the item's frozen grant is what is being decided. Each part
    is an existing ADG answer asked about this item rather than a new derivation, and each
    carries its own availability so that "nothing found" and "could not look" stay apart.
    """

    item: ItemView
    campaign_id: UUID
    comment_requirement: str
    drift: DriftView
    baseline_access: AccessSummaryView = Field(
        description="What the grant was worth at the campaign's baseline — frozen like it."
    )
    current_access: AccessSummaryView = Field(
        description="What it is worth now. Shown beside the baseline, never instead of it."
    )
    reach: ReachView
    resolved_resource_key: str | None = Field(
        description=(
            "The directory the access answers were computed against. For a share item that "
            "is the directory the share publishes, because effective access is a question "
            "about a file-system object reached by a path. Null when ADG holds no such "
            "directory, which is why an item with perfectly good evidence can still have no "
            "access answer."
        )
    )
    findings: list[RelatedFindingView]
    findings_truncated: bool
    changes: list[LastChangeView]
    changes_truncated: bool


class BulkDecisionResponse(BaseModel):
    """What one bulk decision recorded. Each item got its own row and its own audit event."""

    campaign_id: UUID
    decision: str
    item_count: int
    decisions: list[DecisionView]
    note: str = Field(
        default=(
            "Each item carries its own decision, its own evidence digest and its own audit "
            "event, so the record is indistinguishable from the same decisions made one at a "
            "time. The batch was refused whole rather than applied partly."
        ),
        description="Constant. Present so a client cannot render a batch as one attestation.",
    )


class QueueEntryView(BaseModel):
    """One campaign with work outstanding for the calling reviewer."""

    campaign_id: UUID
    name: str
    focus: str
    status: str
    baseline_at: dt.datetime
    due_at: dt.datetime | None = Field(
        description=(
            "The earliest of this reviewer's own assignment deadlines in this campaign, "
            "falling back to the campaign's. Theirs first: a reviewer is late against what "
            "they were asked for, and a later campaign date would say they still had time."
        )
    )
    assigned: int
    decided: int
    pending: int
    overdue: bool


class QueueResponse(BaseModel):
    """The calling reviewer's queue across every campaign. Overdue first."""

    subject: str
    entries: list[QueueEntryView]
    total_pending: int
    overdue_campaigns: int


class ProposalView(BaseModel):
    proposal_id: UUID
    item_id: UUID
    campaign_id: UUID
    decision_id: UUID | None
    action: str
    status: str
    target_kind: str
    target_key: str
    principal_key: str
    ace_keys: list[str]
    details: dict[str, Any]
    proposed_by_subject: str
    proposed_at: dt.datetime
    note: str = Field(
        default=(
            "ADG records this proposal and does not perform it. The application is read-only "
            "toward the estate; applying a change is somebody's decision, made elsewhere."
        ),
        description=("Constant. Present so a client cannot render a proposal as an action taken."),
    )


class OwnerView(BaseModel):
    owner_id: UUID
    target_kind: str
    target_key: str
    ownership_role: str
    owner_subject: str | None
    owner_principal_key: str | None
    owner_display_name: str | None
    note: str | None
    assigned_by_subject: str
    assigned_at: dt.datetime
    revoked_at: dt.datetime | None
    revoked_by_subject: str | None
    active: bool
    is_adg_metadata: bool = Field(
        default=True,
        description=(
            "Always true, and present to say so on every record: this is ADG's statement of "
            "accountability, not the owner field of the Windows security descriptor. The "
            "collected owner SID is reported by the resource endpoints and the two are never "
            "merged."
        ),
    )


class OwnerListResponse(BaseModel):
    items: list[OwnerView]
    page: PageInfo


class ReviewerProgressView(BaseModel):
    """One reviewer's share of a campaign, and whether they are behind on it."""

    assignment_id: UUID
    reviewer_subject: str
    reviewer_display_name: str | None
    scope: ScopeView | None
    due_at: dt.datetime | None = Field(
        description=(
            "The assignment's own deadline where it has one, and the campaign's otherwise. "
            "A reviewer given a shorter deadline is late against theirs; one given none "
            "inherits the campaign's rather than being exempt from every date."
        )
    )
    assigned: int
    decided: int
    pending: int
    completion: float
    late_decisions: int = Field(
        description=(
            "Current decisions this reviewer recorded after their deadline. Counted over "
            "current decisions only: an answer given late and later corrected is one item, "
            "not two failures, and a count that included both could never go down."
        )
    )
    overdue: bool = Field(
        description=(
            "Work still outstanding past the deadline. False once nothing is pending — a "
            "reviewer who finished late is not overdue, they are done, and late_decisions is "
            "where that shows."
        )
    )


class CampaignStatusResponse(BaseModel):
    campaign: CampaignView
    total_items: int
    pending_items: int
    decided_items: int
    unassigned_items: int = Field(
        description=(
            "Items attached to no assignment. Reported separately from the pending total "
            "because nobody can ever decide them: a campaign with three thousand of these is "
            "stuck, not slow."
        )
    )
    completion: float
    decisions_by_kind: dict[str, int]
    overdue: bool
    reviewers: list[ReviewerProgressView]
    overdue_reviewers: int = Field(
        description=(
            "How many reviewers are behind. A campaign can be short of its deadline overall "
            "and still have somebody who has not started, and the two go to different people."
        )
    )
    late_decisions: int = Field(
        description="Current decisions recorded after their reviewer's deadline, campaign-wide."
    )
    audit_head: str | None


class VerificationResponse(BaseModel):
    campaign_id: UUID
    reproducible: bool
    stored_digest: str | None
    recomputed_digest: str
    stored_item_count: int
    recomputed_item_count: int
    missing: list[list[str]]
    unexpected: list[list[str]]
    evidence_changed: list[list[str]]
    explanation: str


class AuditEventView(BaseModel):
    event_id: UUID
    chain_index: int
    event_type: str
    occurred_at: dt.datetime
    actor_subject: str
    actor_display_name: str | None
    actor_roles: list[str]
    campaign_id: UUID | None
    item_id: UUID | None
    decision_id: UUID | None
    payload: dict[str, Any]
    previous_digest: str | None
    event_digest: str


class ChainVerificationView(BaseModel):
    intact: bool
    length: int
    head_digest: str | None
    broken_at: int | None
    reason: str | None


class AuditResponse(BaseModel):
    events: list[AuditEventView]
    page: PageInfo
    chain: ChainVerificationView = Field(
        description=(
            "Verified over the whole chain, not the page returned. A chain checked over a "
            "window would report 'intact' for a trail whose earlier events had been "
            "rewritten, which is worse than not checking."
        )
    )


# ------------------------------------------------------------------------------- routes


@router.post(
    "/campaigns",
    response_model=CampaignView,
    status_code=status.HTTP_201_CREATED,
    summary="Create a review campaign",
    dependencies=[MANAGE],
    responses={
        422: {
            "description": (
                "The scopes do not match the focus, or the baseline is not a past, "
                "timezone-aware instant."
            )
        }
    },
)
async def create_campaign(
    body: CreateCampaignRequest, session: Session, principal: CurrentPrincipal
) -> CampaignView:
    campaign = await GovernanceService(session).create_campaign(
        _actor(principal),
        name=body.name,
        focus=body.focus,
        scopes=[CampaignScope(kind=scope.kind, key=scope.key) for scope in body.scopes],
        baseline_at=body.baseline_at,
        description=body.description,
        due_at=body.due_at,
        options=GenerationOptions(
            include_inherited=body.options.include_inherited,
            include_builtin=body.options.include_builtin,
            include_deny=body.options.include_deny,
        ),
        comment_requirement=body.comment_requirement,
    )
    return _campaign_view(campaign)


@router.get(
    "/campaigns",
    response_model=CampaignListResponse,
    summary="Review campaigns, newest first",
    dependencies=[READ],
)
async def list_campaigns(
    session: Session,
    principal: CurrentPrincipal,
    campaign_status: Annotated[
        CampaignStatus | None, Query(alias="status", description="Restrict to one status.")
    ] = None,
    mine: Annotated[
        bool, Query(description="Only campaigns this caller has an active assignment on.")
    ] = False,
    limit: Annotated[int | None, Query(ge=1, le=MAX_LIMIT)] = None,
    cursor: Annotated[str | None, Query(description="Opaque cursor from a previous page.")] = None,
) -> CampaignListResponse:
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    campaigns, has_more = await GovernanceService(session).list_campaigns(
        status=campaign_status,
        reviewer_subject=principal.subject if mine else None,
        limit=page_size,
        offset=offset,
    )
    return CampaignListResponse(
        items=[_campaign_view(campaign) for campaign in campaigns],
        page=PageInfo(
            limit=page_size,
            has_more=has_more,
            next_cursor=encode_offset_cursor(offset + page_size) if has_more else None,
        ),
    )


@router.get(
    "/campaigns/{campaign_id}",
    response_model=CampaignView,
    summary="Inspect a campaign",
    dependencies=[READ],
    responses={404: {"description": "No such campaign."}},
)
async def get_campaign(campaign_id: CampaignIdPath, session: Session) -> CampaignView:
    return _campaign_view(await GovernanceService(session).get_campaign(campaign_id))


@router.post(
    "/campaigns/{campaign_id}/generation",
    response_model=GenerationResponse,
    summary="Freeze the campaign's items against its baseline",
    dependencies=[MANAGE],
    responses={
        404: {"description": "No such campaign."},
        409: {
            "description": (
                "The campaign is not a draft, or its scopes select more than one review may "
                "hold. Generation is refused rather than truncated."
            )
        },
    },
)
async def generate_campaign(
    campaign_id: CampaignIdPath, session: Session, principal: CurrentPrincipal
) -> GenerationResponse:
    outcome = await GovernanceService(session).generate(_actor(principal), campaign_id)
    return GenerationResponse(
        campaign=_campaign_view(outcome.campaign),
        item_count=outcome.item_count,
        excluded_counts=dict(outcome.excluded),
        snapshot_digest=outcome.digest,
        regenerated=outcome.regenerated,
    )


@router.post(
    "/campaigns/{campaign_id}/activation",
    response_model=CampaignView,
    summary="Open a generated campaign for decisions",
    dependencies=[MANAGE],
    responses={
        404: {"description": "No such campaign."},
        409: {"description": "The campaign is not a draft, or has no items."},
    },
)
async def activate_campaign(
    campaign_id: CampaignIdPath, session: Session, principal: CurrentPrincipal
) -> CampaignView:
    return _campaign_view(await GovernanceService(session).activate(_actor(principal), campaign_id))


@router.post(
    "/campaigns/{campaign_id}/closure",
    response_model=CampaignView,
    summary="Close a campaign",
    dependencies=[MANAGE],
    responses={
        404: {"description": "No such campaign."},
        409: {"description": "The campaign is not active."},
    },
)
async def close_campaign(
    campaign_id: CampaignIdPath, session: Session, principal: CurrentPrincipal
) -> CampaignView:
    """Closing with items still undecided is permitted, and the count is in the audit event.

    A campaign that could not close until every item was answered would either never close or
    be closed by somebody rubber-stamping the remainder; "closed with 12 undecided" recorded
    honestly is worth more than either.
    """
    return _campaign_view(await GovernanceService(session).close(_actor(principal), campaign_id))


@router.post(
    "/campaigns/{campaign_id}/assignments",
    response_model=AssignmentCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ask a reviewer for part of a campaign",
    dependencies=[MANAGE],
    responses={
        404: {"description": "No such campaign."},
        409: {"description": "The campaign is closed, or the scope does not fit its focus."},
    },
)
async def assign_reviewer(
    campaign_id: CampaignIdPath,
    body: AssignReviewerRequest,
    session: Session,
    principal: CurrentPrincipal,
) -> AssignmentCreatedResponse:
    assignment, attached = await GovernanceService(session).assign(
        _actor(principal),
        campaign_id,
        reviewer_subject=body.reviewer_subject,
        reviewer_display_name=body.reviewer_display_name,
        reviewer_email=body.reviewer_email,
        scope=(
            None if body.scope is None else CampaignScope(kind=body.scope.kind, key=body.scope.key)
        ),
        due_at=body.due_at,
    )
    return AssignmentCreatedResponse(
        assignment=_assignment_view(assignment), items_attached=attached
    )


@router.get(
    "/campaigns/{campaign_id}/assignments",
    response_model=AssignmentListResponse,
    summary="Who was asked what",
    dependencies=[READ],
    responses={404: {"description": "No such campaign."}},
)
async def list_assignments(
    campaign_id: CampaignIdPath,
    session: Session,
    include_revoked: Annotated[
        bool, Query(description="Include assignments that have been withdrawn.")
    ] = False,
) -> AssignmentListResponse:
    assignments = await GovernanceService(session).list_assignments(
        campaign_id, include_revoked=include_revoked
    )
    return AssignmentListResponse(items=[_assignment_view(item) for item in assignments])


@router.get(
    "/campaigns/{campaign_id}/items",
    response_model=ItemListResponse,
    summary="A campaign's review items",
    dependencies=[READ],
    responses={404: {"description": "No such campaign."}},
)
async def list_items(
    campaign_id: CampaignIdPath,
    session: Session,
    principal: CurrentPrincipal,
    item_status: Annotated[
        ReviewItemStatus | None, Query(alias="status", description="Restrict to one status.")
    ] = None,
    mine: Annotated[
        bool, Query(description="Only items assigned to this caller — the reviewer's queue.")
    ] = False,
    unassigned: Annotated[
        bool, Query(description="Only items no reviewer was asked about.")
    ] = False,
    principal_key: Annotated[
        str | None, Query(max_length=512, pattern=PRINTABLE_IDENTIFIER)
    ] = None,
    target_key: Annotated[str | None, Query(max_length=512, pattern=PRINTABLE_IDENTIFIER)] = None,
    limit: Annotated[int | None, Query(ge=1, le=MAX_LIMIT)] = None,
    cursor: Annotated[str | None, Query(description="Opaque cursor from a previous page.")] = None,
) -> ItemListResponse:
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    page = await GovernanceService(session).list_items(
        campaign_id,
        status=item_status,
        reviewer_subject=principal.subject if mine else None,
        unassigned_only=unassigned,
        principal_key=principal_key,
        target_key=target_key,
        limit=page_size,
        offset=offset,
    )
    return ItemListResponse(
        items=[_item_view(item) for item in page.items],
        page=PageInfo(
            limit=page_size,
            has_more=page.has_more,
            next_cursor=encode_offset_cursor(offset + page_size) if page.has_more else None,
            total=page.total,
        ),
    )


@router.get(
    "/queue",
    response_model=QueueResponse,
    summary="What is waiting for the calling reviewer, across every campaign",
    dependencies=[READ],
)
async def reviewer_queue(
    session: Session,
    principal: CurrentPrincipal,
    include_closed: Annotated[
        bool,
        Query(
            description=(
                "Include campaigns that have closed. Off by default: a queue is a list of "
                "things to do, and a closed campaign accepts no decisions."
            )
        ),
    ] = False,
) -> QueueResponse:
    """The reviewer's own queue, resolved through their **active** assignments.

    Deliberately not "campaigns you can see": a queue nobody was assigned any of is an empty
    queue, and a reviewer stood down from a campaign stops seeing it here without any item
    being edited — the same rule the ``mine=true`` item filter follows.

    Requires ``governance:read`` rather than ``governance:review``, because reading what was
    asked of you is not answering it, and an auditor checking whether anyone has a backlog
    needs to be able to look.
    """
    entries = await GovernanceService(session).reviewer_queue(
        principal.subject, include_closed=include_closed
    )
    return QueueResponse(
        subject=principal.subject,
        entries=[_queue_view(entry) for entry in entries],
        total_pending=sum(entry.pending for entry in entries),
        overdue_campaigns=sum(1 for entry in entries if entry.overdue),
    )


@router.get(
    "/campaigns/{campaign_id}/drift",
    response_model=DriftReportResponse,
    summary="What the estate has done to this campaign's items since it was frozen",
    dependencies=[READ],
    responses={404: {"description": "No such campaign."}},
)
async def campaign_drift(
    campaign_id: CampaignIdPath,
    session: Session,
    limit: Annotated[int | None, Query(ge=1, le=MAX_DRIFT_ITEMS)] = None,
    cursor: Annotated[str | None, Query(description="Opaque cursor from a previous page.")] = None,
) -> DriftReportResponse:
    """Compare each item's frozen evidence with the grant as it stands now.

    **Nothing is rewritten.** The item keeps the evidence it was generated with, because that
    is what a decision is about and what the audit trail holds the digest of; a campaign that
    refreshed its own items would make every past attestation a statement about whatever the
    grant became.

    Computed on read against live state, so it is bounded and says so: ``covered`` against
    ``total_items`` is what keeps "nothing has changed" from being read as more than "nothing
    in the part that was checked".
    """
    page_size = min(normalize_limit(limit), MAX_DRIFT_ITEMS)
    offset = decode_offset_cursor(cursor)
    report = await GovernanceService(session).campaign_drift(
        campaign_id, limit=page_size, offset=offset
    )
    return _drift_report_view(report)


@router.get(
    "/campaigns/{campaign_id}/status",
    response_model=CampaignStatusResponse,
    summary="How far a campaign has got",
    dependencies=[READ],
    responses={404: {"description": "No such campaign."}},
)
async def campaign_status(campaign_id: CampaignIdPath, session: Session) -> CampaignStatusResponse:
    return _status_view(await GovernanceService(session).campaign_status(campaign_id))


@router.get(
    "/campaigns/{campaign_id}/verification",
    response_model=VerificationResponse,
    summary="Is this campaign still reproducible from its baseline?",
    dependencies=[READ],
    responses={
        404: {"description": "No such campaign."},
        409: {
            "description": (
                "The campaign has not been generated, or can no longer be regenerated "
                "within the current limits."
            )
        },
    },
)
async def verify_campaign(campaign_id: CampaignIdPath, session: Session) -> VerificationResponse:
    """Regenerate the campaign from its own row and compare with what it holds.

    ``object_versions`` is append-only and a version's interval is fixed once written, so a
    baseline that produces a different answer today means the timeline itself changed —
    history edited, restored from a partial backup, or pruned by retention past the baseline.
    Divergence here is a finding, not noise, and the explanation says so.
    """
    return _verification_view(await GovernanceService(session).verify_campaign(campaign_id))


@router.get(
    "/campaigns/{campaign_id}/audit",
    response_model=AuditResponse,
    summary="The campaign's audit trail, and whether its chain is intact",
    dependencies=[READ],
    responses={404: {"description": "No such campaign."}},
)
async def campaign_audit(
    campaign_id: CampaignIdPath,
    session: Session,
    limit: Annotated[int | None, Query(ge=1, le=MAX_AUDIT_PAGE)] = None,
    cursor: Annotated[str | None, Query(description="Opaque cursor from a previous page.")] = None,
) -> AuditResponse:
    service = GovernanceService(session)
    await service.get_campaign(campaign_id)
    page_size = min(normalize_limit(limit), MAX_AUDIT_PAGE)
    offset = decode_offset_cursor(cursor)
    events, has_more, chain = await service.audit_trail(campaign_id, limit=page_size, offset=offset)
    return AuditResponse(
        events=[_event_view(event) for event in events],
        page=PageInfo(
            limit=page_size,
            has_more=has_more,
            next_cursor=encode_offset_cursor(offset + page_size) if has_more else None,
            total=chain.length,
        ),
        chain=ChainVerificationView(
            intact=chain.intact,
            length=chain.length,
            head_digest=chain.head_digest,
            broken_at=chain.broken_at,
            reason=chain.reason,
        ),
    )


@router.get(
    "/items/{item_id}",
    response_model=ItemDetailResponse,
    summary="One review item and every decision on it",
    dependencies=[READ],
    responses={404: {"description": "No such item."}},
)
async def get_item(item_id: ItemIdPath, session: Session) -> ItemDetailResponse:
    """The item, every decision on it, and whether the estate has moved under it.

    Drift is included here rather than behind a second call because a screen that can show
    an item without showing that its grant no longer exists is a screen that will. It costs
    two indexed reads over the item's own target.
    """
    service = GovernanceService(session)
    item = await service.get_item(item_id)
    decisions = await service.item_decisions(item_id)
    drifts = await ReviewContextService(session).drift_for([item])
    return ItemDetailResponse(
        item=_item_view(item),
        decisions=[_decision_view(entry) for entry in decisions],
        drift=_drift_view(drifts[item.item_id], at=dt.datetime.now(dt.UTC)),
    )


@router.get(
    "/items/{item_id}/context",
    response_model=ItemContextResponse,
    summary="Everything a reviewer needs to decide this item",
    dependencies=[READ],
    responses={404: {"description": "No such item."}},
)
async def item_context(item_id: ItemIdPath, session: Session) -> ItemContextResponse:
    """One call behind the review screen: drift, effective access, why, risk, last change.

    The acceptance criterion this route exists for is that a reviewer can make an
    evidence-based decision without opening AD tools — so the answers are assembled here
    rather than left as five calls a client might make three of.

    Nothing here is derived anew. Each part is an existing ADG answer asked about this item:
    the drift comparison, the effective-access engine over the baseline and over now, the
    Phase 5 causal explanation (which is where direct and group-derived access are already
    separated), the open risk findings naming this target or principal, and the change feed
    for this item's own entries. Each carries its own availability, because "nothing found"
    and "could not look" must not render the same.
    """
    context = await GovernanceService(session).item_context(item_id)
    return _context_view(context)


@router.post(
    "/campaigns/{campaign_id}/decisions",
    response_model=BulkDecisionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record the same attestation on several items that are one question",
    dependencies=[REVIEW],
    responses={
        403: {
            "description": (
                "At least one item is assigned to somebody else or to nobody. The batch is "
                "refused whole: a partly-applied bulk decision would leave a reviewer unsure "
                "which items they had answered."
            )
        },
        404: {"description": "No such campaign, or an item id that is not in it."},
        409: {"description": "The campaign is not active."},
        422: {
            "description": (
                "The batch is not homogeneous — mixed target kinds, more than one principal "
                "across more than one target, an item already decided, or an item whose grant "
                "has drifted since the baseline — or the rationale the campaign requires is "
                "missing. The message names which."
            )
        },
    },
)
async def bulk_decide(
    campaign_id: CampaignIdPath,
    body: BulkDecisionRequest,
    session: Session,
    principal: CurrentPrincipal,
) -> BulkDecisionResponse:
    """Answer several items at once, writing each decision and each audit event separately.

    A bulk action is only defensible when the items are genuinely one question, so this
    refuses anything else: one target kind, one shared axis (a principal across targets or a
    target across principals), nothing already decided, and nothing that has drifted since
    the baseline — that last one because a changed grant is precisely the item that needs
    reading, and a batch is where it would not be read.

    Each item still gets its own decision row, its own audit event and its own evidence
    digest in that event, so the record afterwards is the same as for the identical decisions
    made one at a time. That is what makes the action individually auditable rather than one
    attestation wearing many.
    """
    outcome = await GovernanceService(session).bulk_decide(
        _actor(principal),
        campaign_id,
        item_ids=body.item_ids,
        decision=body.decision,
        rationale=body.rationale,
    )
    return _bulk_view(outcome)


@router.post(
    "/items/{item_id}/decisions",
    response_model=DecisionView,
    status_code=status.HTTP_201_CREATED,
    summary="Record an attestation",
    dependencies=[REVIEW],
    responses={
        403: {
            "description": (
                "The item is assigned to somebody else, to nobody, or this caller's "
                "assignment has been revoked. Holding 'governance:review' admits the request; "
                "the assignment is what grants authority over this item."
            )
        },
        404: {"description": "No such item."},
        409: {"description": "The campaign is not active."},
        422: {
            "description": (
                "A decision that must carry a rationale carries none: 'revoke', 'modify', "
                "'abstain' and 'investigate' always, and 'certify' too when the campaign's "
                "comment_requirement is 'always'."
            )
        },
    },
)
async def submit_decision(
    item_id: ItemIdPath,
    body: SubmitDecisionRequest,
    session: Session,
    principal: CurrentPrincipal,
) -> DecisionView:
    decision = await GovernanceService(session).decide(
        _actor(principal), item_id, decision=body.decision, rationale=body.rationale
    )
    return _decision_view(decision)


@router.post(
    "/items/{item_id}/remediation",
    response_model=ProposalView,
    status_code=status.HTTP_201_CREATED,
    summary="Propose the change that should follow a decision",
    dependencies=[REVIEW],
    responses={
        403: {"description": "The item is not assigned to this caller."},
        404: {"description": "No such item."},
        409: {
            "description": (
                "The item has no decision yet, or was certified — there is nothing to "
                "remediate — or the campaign is not active."
            )
        },
    },
)
async def propose_remediation(
    item_id: ItemIdPath,
    body: ProposeRemediationRequest,
    session: Session,
    principal: CurrentPrincipal,
) -> ProposalView:
    """ADG records the proposal and performs none of it. See SECURITY.md."""
    proposal = await GovernanceService(session).propose_remediation(
        _actor(principal), item_id, action=body.action, details=body.details
    )
    return _proposal_view(proposal)


@router.post(
    "/owners",
    response_model=OwnerView,
    status_code=status.HTTP_201_CREATED,
    summary="Record who is accountable for a resource",
    dependencies=[MANAGE],
    responses={
        409: {"description": "That party is already recorded in that role on that target."},
        422: {
            "description": (
                "The record names both an ADG user and a Windows principal, or neither."
            )
        },
    },
)
async def assign_owner(
    body: AssignOwnerRequest, session: Session, principal: CurrentPrincipal
) -> OwnerView:
    """ADG metadata. This neither reads nor writes the Windows security descriptor's owner."""
    if (body.owner_subject is None) == (body.owner_principal_key is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "An ownership record names either an ADG user (owner_subject) or a Windows "
                "principal (owner_principal_key), and exactly one of them. Naming both "
                "leaves two answers to 'who is accountable' with nothing to choose between; "
                "naming neither records accountability belonging to nobody."
            ),
        )
    owner = await GovernanceService(session).assign_owner(
        _actor(principal),
        target_kind=body.target_kind,
        target_key=body.target_key,
        ownership_role=body.ownership_role,
        owner_subject=body.owner_subject,
        owner_principal_key=body.owner_principal_key,
        owner_display_name=body.owner_display_name,
        note=body.note,
    )
    return _owner_view(owner)


@router.get(
    "/owners",
    response_model=OwnerListResponse,
    summary="Recorded resource ownership",
    dependencies=[READ],
)
async def list_owners(
    session: Session,
    target_kind: Annotated[ReviewTargetKind | None, Query()] = None,
    target_key: Annotated[str | None, Query(max_length=512, pattern=PRINTABLE_IDENTIFIER)] = None,
    owner_subject: Annotated[
        str | None, Query(max_length=320, pattern=PRINTABLE_IDENTIFIER)
    ] = None,
    include_revoked: Annotated[bool, Query()] = False,
    limit: Annotated[int | None, Query(ge=1, le=MAX_LIMIT)] = None,
    cursor: Annotated[str | None, Query(description="Opaque cursor from a previous page.")] = None,
) -> OwnerListResponse:
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    owners, has_more = await GovernanceService(session).list_owners(
        target_kind=target_kind,
        target_key=target_key,
        owner_subject=owner_subject,
        include_revoked=include_revoked,
        limit=page_size,
        offset=offset,
    )
    return OwnerListResponse(
        items=[_owner_view(owner) for owner in owners],
        page=PageInfo(
            limit=page_size,
            has_more=has_more,
            next_cursor=encode_offset_cursor(offset + page_size) if has_more else None,
        ),
    )


@router.delete(
    "/owners/{owner_id}",
    response_model=OwnerView,
    summary="Withdraw an ownership record",
    dependencies=[MANAGE],
    responses={
        404: {"description": "No such ownership record."},
        409: {"description": "It was already withdrawn."},
    },
)
async def revoke_owner(
    owner_id: OwnerIdPath, session: Session, principal: CurrentPrincipal
) -> OwnerView:
    """Marked withdrawn, never deleted: "who was accountable last quarter" is exactly the
    question an audit of last quarter's campaign asks."""
    return _owner_view(await GovernanceService(session).revoke_owner(_actor(principal), owner_id))


# -------------------------------------------------------------------------------- views


def _scope_view(scope: CampaignScope | None) -> ScopeView | None:
    return None if scope is None else ScopeView(kind=scope.kind.value, key=scope.key)


def _campaign_view(campaign: ReviewCampaign) -> CampaignView:
    return CampaignView(
        campaign_id=campaign.campaign_id,
        name=campaign.name,
        description=campaign.description,
        focus=campaign.focus.value,
        status=campaign.status.value,
        baseline_at=campaign.baseline_at,
        due_at=campaign.due_at,
        scopes=[ScopeView(kind=scope.kind.value, key=scope.key) for scope in campaign.scopes],
        options=GenerationOptionsBody(
            include_inherited=campaign.options.include_inherited,
            include_builtin=campaign.options.include_builtin,
            include_deny=campaign.options.include_deny,
        ),
        comment_requirement=campaign.comment_requirement.value,
        item_count=campaign.item_count,
        excluded_counts=dict(campaign.excluded_counts),
        snapshot_digest=campaign.snapshot_digest,
        generated_at=campaign.generated_at,
        activated_at=campaign.activated_at,
        closed_at=campaign.closed_at,
        closed_by_subject=campaign.closed_by_subject,
        created_by_subject=campaign.created_by_subject,
        created_at=campaign.created_at,
    )


def _assignment_view(assignment: ReviewAssignment) -> AssignmentView:
    return AssignmentView(
        assignment_id=assignment.assignment_id,
        campaign_id=assignment.campaign_id,
        reviewer_subject=assignment.reviewer_subject,
        reviewer_display_name=assignment.reviewer_display_name,
        reviewer_email=assignment.reviewer_email,
        scope=_scope_view(assignment.scope),
        due_at=assignment.due_at,
        assigned_by_subject=assignment.assigned_by_subject,
        assigned_at=assignment.assigned_at,
        revoked_at=assignment.revoked_at,
        revoked_by_subject=assignment.revoked_by_subject,
        active=assignment.is_active,
    )


def _grant_view(grant: GrantEvidence) -> GrantView:
    return GrantView(
        ace_key=grant.ace_key,
        trustee_sid=grant.trustee_sid,
        trustee_key=grant.trustee_key,
        ace_type=grant.ace_type.value,
        access_mask=grant.access_mask,
        permission=None if grant.permission is None else grant.permission.value,
        ace_flags=grant.ace_flags,
        source=None if grant.source is None else grant.source.value,
        inherited_from=grant.inherited_from,
        order_index=grant.order_index,
        inherited=grant.is_inherited,
        deny=grant.is_deny,
        version_id=grant.version_id,
        observed_from=grant.observed_from,
        last_confirmed_at=grant.last_confirmed_at,
        certainty=grant.certainty.value,
    )


def _item_view(item: ReviewItem) -> ItemView:
    return ItemView(
        item_id=item.item_id,
        campaign_id=item.campaign_id,
        focus=item.focus.value,
        target_kind=item.target_kind.value,
        target_key=item.target_key,
        target_path=item.target_path,
        principal_key=item.principal_key,
        principal_sid=item.principal_sid,
        principal_display_name=item.principal_display_name,
        grants=[_grant_view(grant) for grant in item.grants],
        evidence_digest=item.evidence_digest,
        certainty=item.certainty.value,
        status=item.status.value,
        assignment_id=item.assignment_id,
        current_decision_id=item.current_decision_id,
        decided_at=item.decided_at,
        created_at=item.created_at,
    )


def _decision_view(decision: ReviewDecision) -> DecisionView:
    return DecisionView(
        decision_id=decision.decision_id,
        item_id=decision.item_id,
        campaign_id=decision.campaign_id,
        decision=decision.decision.value,
        rationale=decision.rationale,
        decided_by_subject=decision.decided_by_subject,
        decided_by_display_name=decision.decided_by_display_name,
        decided_at=decision.decided_at,
        decided_late=decision.decided_late,
        supersedes_decision_id=decision.supersedes_decision_id,
        superseded_at=decision.superseded_at,
        superseded_by_decision_id=decision.superseded_by_decision_id,
        current=decision.is_current,
    )


def _proposal_view(proposal: RemediationProposal) -> ProposalView:
    return ProposalView(
        proposal_id=proposal.proposal_id,
        item_id=proposal.item_id,
        campaign_id=proposal.campaign_id,
        decision_id=proposal.decision_id,
        action=proposal.action.value,
        status=proposal.status.value,
        target_kind=proposal.target_kind.value,
        target_key=proposal.target_key,
        principal_key=proposal.principal_key,
        ace_keys=list(proposal.ace_keys),
        details=dict(proposal.details),
        proposed_by_subject=proposal.proposed_by_subject,
        proposed_at=proposal.proposed_at,
    )


def _owner_view(owner: ResourceOwner) -> OwnerView:
    return OwnerView(
        owner_id=owner.owner_id,
        target_kind=owner.target_kind.value,
        target_key=owner.target_key,
        ownership_role=owner.ownership_role.value,
        owner_subject=owner.owner_subject,
        owner_principal_key=owner.owner_principal_key,
        owner_display_name=owner.owner_display_name,
        note=owner.note,
        assigned_by_subject=owner.assigned_by_subject,
        assigned_at=owner.assigned_at,
        revoked_at=owner.revoked_at,
        revoked_by_subject=owner.revoked_by_subject,
        active=owner.is_active,
    )


def _progress_view(progress: ReviewerProgress) -> ReviewerProgressView:
    return ReviewerProgressView(
        assignment_id=progress.assignment_id,
        reviewer_subject=progress.reviewer_subject,
        reviewer_display_name=progress.reviewer_display_name,
        scope=_scope_view(progress.scope),
        due_at=progress.due_at,
        assigned=progress.assigned,
        decided=progress.decided,
        pending=progress.pending,
        completion=progress.completion,
        late_decisions=progress.late_decisions,
        overdue=progress.overdue,
    )


def _status_view(report: CampaignStatusReport) -> CampaignStatusResponse:
    return CampaignStatusResponse(
        campaign=_campaign_view(report.campaign),
        total_items=report.counts.total,
        pending_items=report.counts.pending,
        decided_items=report.counts.decided,
        unassigned_items=report.unassigned_items,
        completion=report.counts.completion,
        decisions_by_kind=dict(report.counts.by_decision),
        overdue=report.overdue,
        reviewers=[_progress_view(item) for item in report.reviewers],
        overdue_reviewers=sum(1 for item in report.reviewers if item.overdue),
        late_decisions=sum(item.late_decisions for item in report.reviewers),
        audit_head=report.audit_head,
    )


def _queue_view(entry: QueueEntry) -> QueueEntryView:
    return QueueEntryView(
        campaign_id=entry.campaign.campaign_id,
        name=entry.campaign.name,
        focus=entry.campaign.focus.value,
        status=entry.campaign.status.value,
        baseline_at=entry.campaign.baseline_at,
        due_at=entry.due_at,
        assigned=entry.assigned,
        decided=entry.decided,
        pending=entry.pending,
        overdue=entry.overdue,
    )


def _grant_change_view(change: GrantChange) -> GrantChangeView:
    return GrantChangeView(
        kind=change.kind.value,
        ace_key=change.ace_key,
        fields=list(change.fields),
        field_labels=list(change.field_labels),
        before=None if change.before is None else _grant_view(change.before),
        after=None if change.after is None else _grant_view(change.after),
    )


def _drift_view(drift: ItemDrift, *, at: dt.datetime) -> DriftView:
    return DriftView(
        verdict=drift.verdict.value,
        has_drifted=drift.has_drifted,
        summary=drift.summary,
        compared_at=at,
        baseline_digest=drift.baseline_digest,
        current_content_digest=drift.current_content_digest,
        changes=[_grant_change_view(change) for change in drift.changes],
        current_grants=[_grant_view(grant) for grant in drift.current_grants],
        current_certainty=(
            None if drift.current_certainty is None else drift.current_certainty.value
        ),
        target_present=drift.target_present,
        target_certainty=(None if drift.target_certainty is None else drift.target_certainty.value),
        evidence_reissued=drift.evidence_reissued,
    )


def _drift_report_view(report: DriftReport) -> DriftReportResponse:
    return DriftReportResponse(
        campaign_id=report.campaign_id,
        compared_at=report.compared_at,
        total_items=report.total_items,
        covered=report.covered,
        has_more=report.has_more,
        counts=DriftCountsView(
            unchanged=report.counts.get("unchanged", 0),
            modified=report.counts.get("modified", 0),
            removed=report.counts.get("removed", 0),
            unobserved=report.counts.get("unobserved", 0),
        ),
        drifted=[
            DriftItemView(item=_item_view(item), drift=_drift_view(drift, at=report.compared_at))
            for item, drift in report.drifted
        ],
    )


def _bulk_view(outcome: BulkOutcome) -> BulkDecisionResponse:
    return BulkDecisionResponse(
        campaign_id=outcome.campaign_id,
        decision=outcome.decision.value,
        item_count=outcome.item_count,
        decisions=[_decision_view(decision) for decision in outcome.decisions],
    )


def _access_view(answer: AccessAt) -> AccessSummaryView:
    access = answer.access
    return AccessSummaryView(
        at=answer.at,
        available=answer.available,
        unavailable_reason=answer.unavailable_reason,
        has_access=None if access is None else access.has_access,
        rights=None if access is None else render_rights(access.rights),
        certainty=(
            answer.certainty.value
            if answer.certainty is not None
            else (None if access is None else access.certainty.value)
        ),
        limiting_layer=None if access is None else access.limiting_layer.value,
    )


def _route_view(route: GroupRoute, labels: Any) -> GroupRouteView:
    return GroupRouteView(
        principal=principal_summary(route.principal_key, labels.get(route.principal_key)),
        depth=route.depth,
        chain=list(route.chain),
        rights=render_rights(route.rights),
        layer=route.layer,
        inherited=route.inherited,
    )


def _removal_view(removal: EntryRemoval) -> EntryRemovalView:
    return EntryRemovalView(
        ace_key=removal.ace_key,
        rights_removed=render_rights(removal.rights_removed),
        rights_after=render_rights(removal.rights_after),
        revokes_all_access=removal.revokes_all_access,
        changes_nothing=removal.changes_nothing,
        alternate_paths=removal.alternate_paths,
    )


def _reach_view(reach: Reach) -> ReachView:
    return ReachView(
        available=reach.available,
        unavailable_reason=reach.unavailable_reason,
        direct_paths=reach.direct_paths,
        group_paths=reach.group_paths,
        routes=[_route_view(route, reach.labels) for route in reach.routes],
        removals=[_removal_view(removal) for removal in reach.removals],
        removing_reviewed_entries_leaves_access=(reach.removing_reviewed_entries_leaves_access),
        truncated=reach.truncated,
    )


def _finding_view(finding: RelatedFinding) -> RelatedFindingView:
    return RelatedFindingView(
        finding_key=finding.finding_key,
        rule_id=finding.rule_id,
        status=finding.status,
        severity=finding.severity,
        band=finding.band,
        confidence=finding.confidence,
        relation=finding.relation,
        detected_at=finding.detected_at,
        first_detected_at=finding.first_detected_at,
        resource_key=finding.resource_key,
        share_key=finding.share_key,
        principal_key=finding.principal_key,
        detail=dict(finding.detail),
    )


def _change_view(change: LastChange) -> LastChangeView:
    return LastChangeView(
        kind=change.kind,
        key=change.key,
        action=change.action,
        significance=change.significance,
        severity=change.severity,
        direction=change.direction,
        reasons=list(change.reasons),
        changed_after=change.changed_after,
        changed_at_or_before=change.changed_at_or_before,
        is_exact=change.is_exact,
    )


def _context_view(context: ReviewContext) -> ItemContextResponse:
    return ItemContextResponse(
        item=_item_view(context.item),
        campaign_id=context.campaign.campaign_id,
        comment_requirement=context.campaign.comment_requirement.value,
        drift=_drift_view(context.drift, at=context.current_access.at),
        baseline_access=_access_view(context.baseline_access),
        current_access=_access_view(context.current_access),
        reach=_reach_view(context.reach),
        resolved_resource_key=context.resolved_resource_key,
        findings=[_finding_view(finding) for finding in context.findings],
        findings_truncated=context.findings_truncated,
        changes=[_change_view(change) for change in context.changes],
        changes_truncated=context.changes_truncated,
    )


def _keys(entries: Sequence[tuple[str, str, str]]) -> list[list[str]]:
    return [list(entry) for entry in entries]


def _verification_view(verification: CampaignVerification) -> VerificationResponse:
    return VerificationResponse(
        campaign_id=verification.campaign_id,
        reproducible=verification.reproducible,
        stored_digest=verification.stored_digest,
        recomputed_digest=verification.recomputed_digest,
        stored_item_count=verification.stored_item_count,
        recomputed_item_count=verification.recomputed_item_count,
        missing=_keys(verification.missing),
        unexpected=_keys(verification.unexpected),
        evidence_changed=_keys(verification.evidence_changed),
        explanation=verification.explanation,
    )


def _event_view(event: GovernanceEvent) -> AuditEventView:
    return AuditEventView(
        event_id=event.event_id,
        chain_index=event.chain_index,
        event_type=event.event_type.value,
        occurred_at=event.occurred_at,
        actor_subject=event.actor_subject,
        actor_display_name=event.actor_display_name,
        actor_roles=list(event.actor_roles),
        campaign_id=event.campaign_id,
        item_id=event.item_id,
        decision_id=event.decision_id,
        payload=dict(event.payload),
        previous_digest=event.previous_digest,
        event_digest=event.digest,
    )


__all__ = ["router"]
