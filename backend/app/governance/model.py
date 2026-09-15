r"""The governance vocabulary: owners, campaigns, reviewers, items, decisions, remediation.

This module is pure. It holds no session, issues no query and imports nothing from
:mod:`app.api` or :mod:`app.models`. Everything it declares is a value, a state machine, or
an invariant with a sentence attached — which is what makes "may this reviewer decide this
item?" answerable in a unit test rather than only against a database.

Three distinctions shape the whole module, and every later file depends on them.

**Governance is metadata about observations, never an observation.** A resource owner
recorded here is *ADG's* statement that a person is accountable for a share. It is not a
claim that anything changed in Windows, and in particular it is neither read from nor
written to ``ntfs_resources.owner_sid`` — the SID Windows actually stores as the object's
owner. The two are kept side by side and never merged: an operator looking at a directory
should be able to see that ADG holds Alice accountable for it *and* that Windows says
``BUILTIN\Administrators`` owns it, because the gap between those two facts is itself a
finding. See ADR-0028.

**A decision is a claim about a frozen past, not about the present.** A campaign names an
instant — its baseline — and every item in it is generated from the versions of
``object_versions`` that covered that instant. A reviewer therefore certifies *what was
true when the campaign was cut*, and that statement stays true no matter what a later scan
finds. Without the freeze, "I approved this" would silently come to mean "I approve of
whatever it is now", which is the failure mode that makes attestation theater.

**A decision never rewrites what a collector reported.** Nothing in this package's write
path touches a current-state table or ``object_versions``; a revoke decision produces a
*proposed remediation*, which is a governance record describing a change somebody might make
in Windows. ADG does not make it. ``Capability.REMEDIATION_EXECUTE`` stays reserved and
unheld (:mod:`app.auth.roles`), and ``tests/governance/test_isolation.py`` fails if a module
here ever writes a collected table.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from app.domain import (
    AceSource,
    AceType,
    CampaignFocus,
    CampaignStatus,
    CommentRequirement,
    DecisionKind,
    DomainValidationError,
    GovernanceEventType,
    OwnershipRole,
    RemediationAction,
    RemediationStatus,
    ReviewItemStatus,
    ReviewScopeKind,
    ReviewTargetKind,
    SharePermission,
)
from app.history.model import Certainty

__all__ = [
    "CAMPAIGN_TRANSITIONS",
    "DECISIONS_REQUIRING_RATIONALE",
    "MAX_NOTE_LENGTH",
    "MAX_RATIONALE_LENGTH",
    "RATIONALE_ALWAYS_REQUIRED",
    "SCOPE_KINDS_FOR_FOCUS",
    "CampaignFocus",
    "CampaignScope",
    "CampaignStatus",
    "CommentRequirement",
    "DecisionKind",
    "GenerationOptions",
    "GovernanceEventType",
    "GovernanceValidationError",
    "GrantEvidence",
    "OwnershipRole",
    "RemediationAction",
    "RemediationProposal",
    "RemediationStatus",
    "ResourceOwner",
    "ReviewAssignment",
    "ReviewCampaign",
    "ReviewDecision",
    "ReviewItem",
    "ReviewItemStatus",
    "ReviewScopeKind",
    "ReviewTargetKind",
    "decisions_requiring_rationale",
    "evidence_digest",
    "item_natural_key",
    "snapshot_digest",
    "validate_baseline",
    "validate_due_date",
    "validate_note",
    "validate_rationale",
    "validate_scopes",
    "validate_transition",
    "weakest_certainty",
]

MAX_RATIONALE_LENGTH: Final = 4_000
"""Characters of free text on a decision. Long enough for a paragraph of reasoning, short
enough that the column is not a document store."""

MAX_NOTE_LENGTH: Final = 2_000


# ------------------------------------------------------------------------ the closed sets

# The enumerations themselves live in :mod:`app.domain.governance`, because
# :mod:`app.models.schema` generates check constraints from them and cannot import this
# module -- which depends on storage through :mod:`app.history.model`. Re-exported here so
# that the governance package has one import for its whole vocabulary.


#: Which scope kinds a focus accepts. A principal-focused campaign scoped to a share would
#: be selecting resources while claiming completeness about a principal; the mismatch is
#: refused at creation rather than producing an item set that answers neither question.
SCOPE_KINDS_FOR_FOCUS: Final[dict[CampaignFocus, frozenset[ReviewScopeKind]]] = {
    CampaignFocus.RESOURCE: frozenset(
        {ReviewScopeKind.SERVER, ReviewScopeKind.SHARE, ReviewScopeKind.DIRECTORY_TREE}
    ),
    CampaignFocus.PRINCIPAL: frozenset({ReviewScopeKind.PRINCIPAL}),
}

#: The only status changes this application performs. A transition absent from the table is
#: refused with a message naming both states, rather than silently applied.
CAMPAIGN_TRANSITIONS: Final[dict[CampaignStatus, frozenset[CampaignStatus]]] = {
    CampaignStatus.DRAFT: frozenset({CampaignStatus.ACTIVE, CampaignStatus.CANCELED}),
    CampaignStatus.ACTIVE: frozenset({CampaignStatus.CLOSED, CampaignStatus.CANCELED}),
    CampaignStatus.CLOSED: frozenset(),
    CampaignStatus.CANCELED: frozenset(),
}

#: Decisions that must carry a reason under every campaign. ``CERTIFY`` is exempt because it
#: is the expected answer and requiring prose for it produces "ok" four thousand times, which
#: is noise that makes the rationales that *do* matter harder to find.
#:
#: This set is a **floor**, not a default: the same rule is written as
#: ``ck_review_decisions_reason_required`` in the database, so a campaign cannot lower it.
#: :data:`RATIONALE_ALWAYS_REQUIRED` is what a campaign may raise it to.
DECISIONS_REQUIRING_RATIONALE: Final[frozenset[DecisionKind]] = frozenset(
    {
        DecisionKind.REVOKE,
        DecisionKind.MODIFY,
        DecisionKind.ABSTAIN,
        DecisionKind.INVESTIGATE,
    }
)

#: Every decision, for a campaign whose ``comment_requirement`` is ``always``.
RATIONALE_ALWAYS_REQUIRED: Final[frozenset[DecisionKind]] = frozenset(DecisionKind)


class GovernanceValidationError(DomainValidationError):
    """A governance input violates an invariant. The message says what it would break."""


# ------------------------------------------------------------------------------- evidence


@dataclass(frozen=True, slots=True)
class GrantEvidence:
    """One access-control entry, exactly as it stood at a campaign's baseline instant.

    This is what a reviewer is shown and what the decision is *about*. It is copied out of
    the version that covered the baseline rather than read live, so the record of what was
    reviewed cannot change underneath a recorded decision.

    ``version_id`` is the row of ``object_versions`` it came from. Keeping it is what makes
    a campaign checkable long after the fact: re-reading that version reproduces this
    evidence exactly, and a mismatch means the timeline itself changed, which is a finding
    rather than a rendering difference.
    """

    target_kind: ReviewTargetKind
    ace_key: str
    trustee_sid: str
    trustee_key: str
    ace_type: AceType
    access_mask: int | None
    """``None`` only for a share ACE reported as a named permission level instead."""

    permission: SharePermission | None
    """Share layer only. A source reports a mask or a level, never both."""

    ace_flags: int | None
    """NTFS layer only: the raw ``ACE_HEADER.AceFlags`` byte, unknown bits included."""

    source: AceSource | None
    """NTFS layer only: whether the entry was set here or inherited from an ancestor."""

    inherited_from: str | None
    order_index: int | None
    version_id: int
    observed_from: dt.datetime
    """``valid_from`` of the version: when this entry was first observed in this state."""

    last_confirmed_at: dt.datetime
    """``last_seen_at``: when a collector last confirmed it. With ``observed_from`` this is
    the window the reviewer is told the evidence was actually watched over."""

    certainty: Certainty
    """How firmly this entry is known to have held at the baseline instant. An item built
    entirely from ``backfilled`` or ``inferred`` evidence is still reviewable and must say
    so: certifying reconstructed state as though it had been watched is the one way this
    feature could make an audit worse rather than better."""

    @property
    def is_inherited(self) -> bool:
        return self.source is AceSource.INHERITED

    @property
    def is_deny(self) -> bool:
        return self.ace_type is AceType.DENY

    def as_digestible(self) -> dict[str, Any]:
        """The fields the evidence digest is taken over.

        ``certainty`` is **excluded**, deliberately. It is a property of the question asked
        — how firm was this at the baseline — and it is derived from the version's interval
        and the instant, not from the entry. Including it would make an item's digest change
        when a later scan confirmed the same state, which is precisely the case where
        nothing about the reviewed grant changed at all.
        """
        return {
            "target_kind": self.target_kind.value,
            "ace_key": self.ace_key,
            "trustee_sid": self.trustee_sid,
            "trustee_key": self.trustee_key,
            "ace_type": self.ace_type.value,
            "access_mask": self.access_mask,
            "permission": None if self.permission is None else self.permission.value,
            "ace_flags": self.ace_flags,
            "source": None if self.source is None else self.source.value,
            "inherited_from": self.inherited_from,
            "order_index": self.order_index,
            "version_id": self.version_id,
        }


def _canonical(payload: Any) -> str:
    """One string per value, so two dictionaries built in different orders digest alike.

    The same rendering :mod:`app.history.model` uses for a version's state: sorted keys,
    tight separators, no ASCII escaping. Governance digests and history digests therefore
    behave identically, which matters because a reader comparing the two should not have to
    learn two canonicalizations.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def evidence_digest(grants: Iterable[GrantEvidence]) -> str:
    """The digest of everything one item was built from.

    Order-independent by construction: the entries are sorted by ``ace_key`` before
    digesting, because the order a query returned them in is not part of the grant. Two
    generations of the same item from the same versions therefore agree, which is what the
    campaign verification compares.
    """
    rendered = sorted((grant.as_digestible() for grant in grants), key=lambda item: item["ace_key"])
    return hashlib.sha256(_canonical(rendered).encode("utf-8")).hexdigest()


def item_natural_key(
    target_kind: ReviewTargetKind, target_key: str, principal_key: str
) -> tuple[str, str, str]:
    """What identifies an item within a campaign, independent of its surrogate id.

    A grant is a ``(principal, target)`` relation, so that pair is the identity — not the
    ACE key, of which one relation may have several (an allow and a deny, or two entries
    with different inheritance flags). Reviewing each ACE separately would ask somebody to
    certify half a grant.
    """
    return (target_kind.value, target_key, principal_key)


def snapshot_digest(natural_keys_and_evidence: Iterable[tuple[tuple[str, str, str], str]]) -> str:
    """The digest of a whole generated item set: what the campaign froze.

    Stored on the campaign so that "is this campaign still the one that was reviewed?" is a
    single comparison rather than a row-by-row diff. The diff is available too
    (:mod:`app.governance.service`); this is the cheap check that says whether to run it.
    """
    # Sorted as tuples, which order totally and deterministically, then rendered. Sorting
    # the rendered form with a key function would order by a list and is both slower and
    # harder to reason about.
    ordered = sorted(natural_keys_and_evidence)
    rendered = [[list(key), digest] for key, digest in ordered]
    return hashlib.sha256(_canonical(rendered).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------- values


@dataclass(frozen=True, slots=True)
class CampaignScope:
    """One selection a campaign made, and part of what it claims to be complete about."""

    kind: ReviewScopeKind
    key: str

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise GovernanceValidationError(
                "A campaign scope needs a key. An empty one would select either everything "
                "or nothing, and there is no way to tell which was meant.",
                field="key",
            )


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    """What a campaign chose *not* to ask about, recorded so the omission is visible.

    Every option here narrows the item set. They are stored on the campaign and reported
    with its status for one reason: a review that excluded inherited entries and built-in
    trustees reviewed less than the estate contains, and a reader who is not told that will
    read "12 of 12 certified" as coverage it is not.
    """

    include_inherited: bool = False
    """Inherited entries cannot be removed where they sit — the fix is on the ancestor that
    defines them — so by default items are built from the grants an administrator can act on
    at the target. Turning this on multiplies the item count by roughly the depth of the
    tree and is almost never what a first campaign wants."""

    include_builtin: bool = False
    """``BUILTIN\\Administrators``, ``SYSTEM`` and the other well-known trustees appear on
    nearly every descriptor. Reviewing them once is useful; reviewing them ten thousand
    times buries the grants that matter."""

    include_deny: bool = True
    """Deny entries are included by default: a deny is part of the grant relation, and an
    item that showed only the allow would ask somebody to certify access the principal does
    not actually have."""

    def as_digestible(self) -> dict[str, Any]:
        return {
            "include_inherited": self.include_inherited,
            "include_builtin": self.include_builtin,
            "include_deny": self.include_deny,
        }


@dataclass(frozen=True, slots=True)
class ResourceOwner:
    """ADG's record that a party is accountable for a resource.

    Exactly one of ``owner_subject`` (an ADG user, by authentication subject) and
    ``owner_principal_key`` (a Windows principal, by SID) is set. Both forms are real: an
    owner who signs in to ADG and reviews is a subject; an owner recorded as "the Finance
    Data Owners group" before anyone from it has ever signed in is a principal key.

    ``revoked_at`` rather than deletion, because who was accountable *last quarter* is
    exactly the question an audit of last quarter's campaign asks.
    """

    owner_id: UUID
    target_kind: ReviewTargetKind
    target_key: str
    ownership_role: OwnershipRole
    owner_subject: str | None
    owner_principal_key: str | None
    owner_display_name: str | None
    note: str | None
    assigned_by_subject: str
    assigned_at: dt.datetime
    revoked_at: dt.datetime | None = None
    revoked_by_subject: str | None = None

    def __post_init__(self) -> None:
        if (self.owner_subject is None) == (self.owner_principal_key is None):
            raise GovernanceValidationError(
                "An ownership record names either an ADG user (owner_subject) or a Windows "
                "principal (owner_principal_key), and exactly one of them. Naming both "
                "would leave two answers to 'who is accountable' with nothing to choose "
                "between them; naming neither records accountability belonging to nobody.",
                field="owner_subject",
            )

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    @property
    def identity(self) -> str:
        """The one string that identifies this owner, whichever form it took."""
        return self.owner_subject or self.owner_principal_key or ""


@dataclass(frozen=True, slots=True)
class ReviewCampaign:
    """One access review, frozen against an instant.

    ``baseline_at`` is the whole of the freeze. Items are a pure function of the campaign's
    focus, scopes and options applied to ``object_versions`` at that instant, so the item
    set is reproducible from the campaign row alone — which is what
    :meth:`app.governance.service.GovernanceService.verify_campaign` recomputes and compares
    against ``snapshot_digest``.
    """

    campaign_id: UUID
    name: str
    description: str | None
    focus: CampaignFocus
    status: CampaignStatus
    baseline_at: dt.datetime
    due_at: dt.datetime | None
    options: GenerationOptions
    scopes: tuple[CampaignScope, ...]
    created_by_subject: str
    created_at: dt.datetime
    comment_requirement: CommentRequirement = CommentRequirement.STANDARD
    """How hard this campaign insists a decision explain itself. It sits among the fields
    carrying defaults rather than beside ``options`` because campaigns created before the
    setting existed have to keep working, and the default is exactly what they did."""

    generated_at: dt.datetime | None = None
    snapshot_digest: str | None = None
    item_count: int = 0
    excluded_counts: Mapping[str, int] = field(default_factory=dict)
    activated_at: dt.datetime | None = None
    closed_at: dt.datetime | None = None
    closed_by_subject: str | None = None

    @property
    def is_generated(self) -> bool:
        return self.generated_at is not None

    @property
    def accepts_decisions(self) -> bool:
        return self.status is CampaignStatus.ACTIVE

    def is_overdue(self, now: dt.datetime) -> bool:
        """Whether the campaign has passed its due date while still open.

        A closed campaign is never overdue, whenever it closed: overdue describes work
        outstanding, and there is none.
        """
        if self.due_at is None or self.status is not CampaignStatus.ACTIVE:
            return False
        return now > self.due_at


@dataclass(frozen=True, slots=True)
class ReviewAssignment:
    """A reviewer, and the slice of a campaign they were asked to answer.

    ``scope`` is ``None`` for an assignment covering the whole campaign. A scoped assignment
    selects the same way a campaign scope does, so "Alice reviews everything under
    ``\\\\fs01\\finance``" is expressible without splitting the campaign.

    The reviewer is identified by **authentication subject**, not by SID. An ADG reviewer is
    a person who signs in to ADG; the Windows principals in a campaign are the *subjects of*
    the review. Conflating the two would make "the group being reviewed" and "the person
    reviewing it" the same kind of thing, and an attestation whose reviewer is a group is
    not attributable to anybody. See ADR-0030.
    """

    assignment_id: UUID
    campaign_id: UUID
    reviewer_subject: str
    reviewer_display_name: str | None
    reviewer_email: str | None
    scope: CampaignScope | None
    due_at: dt.datetime | None
    assigned_by_subject: str
    assigned_at: dt.datetime
    revoked_at: dt.datetime | None = None
    revoked_by_subject: str | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True, slots=True)
class ReviewItem:
    """One grant to be decided, with the evidence it was frozen from."""

    item_id: UUID
    campaign_id: UUID
    focus: CampaignFocus
    target_kind: ReviewTargetKind
    target_key: str
    target_path: str | None
    """The case-preserving path or share name, for display. Comparison is on ``target_key``."""

    principal_key: str
    principal_sid: str
    principal_display_name: str | None
    grants: tuple[GrantEvidence, ...]
    evidence_digest: str
    certainty: Certainty
    """The weakest certainty of any grant in the item. An answer is as sound as the weakest
    fact it rests on, which is the same rule :class:`app.history.service.AsOfAccess` applies
    to an effective-access answer."""

    status: ReviewItemStatus
    assignment_id: UUID | None
    created_at: dt.datetime
    current_decision_id: UUID | None = None
    decided_at: dt.datetime | None = None

    @property
    def natural_key(self) -> tuple[str, str, str]:
        return item_natural_key(self.target_kind, self.target_key, self.principal_key)

    @property
    def has_deny(self) -> bool:
        return any(grant.is_deny for grant in self.grants)

    @property
    def rests_on_reconstructed_state(self) -> bool:
        """Whether any grant came from the Phase 7 backfill rather than an observation."""
        return any(grant.certainty is Certainty.BACKFILLED for grant in self.grants)


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """One attestation. Append-only: a changed mind writes a new row and supersedes this.

    Superseding rather than updating is not bookkeeping fastidiousness. "Alice certified
    this on the 3rd and revoked it on the 9th" and "Alice revoked this on the 9th" are
    different histories, and only the first one lets an auditor ask what changed in between.
    The database enforces it: a trigger on ``review_decisions`` refuses every update except
    the one that marks a row superseded, and refuses deletion outright.
    """

    decision_id: UUID
    item_id: UUID
    campaign_id: UUID
    decision: DecisionKind
    rationale: str | None
    decided_by_subject: str
    decided_by_display_name: str | None
    decided_at: dt.datetime
    decided_late: bool
    """Recorded at the moment of the decision rather than derived later, because the due
    date it was late against is the campaign's due date *then*."""

    supersedes_decision_id: UUID | None = None
    superseded_at: dt.datetime | None = None
    superseded_by_decision_id: UUID | None = None

    @property
    def is_current(self) -> bool:
        return self.superseded_at is None


@dataclass(frozen=True, slots=True)
class RemediationProposal:
    """A change somebody might make in Windows. ADG records it and does not perform it."""

    proposal_id: UUID
    item_id: UUID
    campaign_id: UUID
    decision_id: UUID | None
    action: RemediationAction
    status: RemediationStatus
    target_kind: ReviewTargetKind
    target_key: str
    principal_key: str
    ace_keys: tuple[str, ...]
    """The exact entries the proposal is about, by the identity the collector reported. An
    instruction that named a resource and a trustee without naming the entries would be
    ambiguous the moment a second entry existed for the same pair."""

    details: Mapping[str, Any]
    proposed_by_subject: str
    proposed_at: dt.datetime
    withdrawn_at: dt.datetime | None = None
    withdrawn_by_subject: str | None = None

    @property
    def is_active(self) -> bool:
        return self.withdrawn_at is None


# ----------------------------------------------------------------------------- invariants


def validate_transition(current: CampaignStatus, requested: CampaignStatus) -> None:
    """Refuse a status change the campaign lifecycle does not allow.

    Raises with both states named, because the two mistakes this catches — activating a
    campaign twice, and deciding on a closed one — read identically from a caller that only
    sees "invalid state".
    """
    allowed = CAMPAIGN_TRANSITIONS[current]
    if requested in allowed:
        return
    permitted = ", ".join(sorted(state.value for state in allowed)) or "nothing"
    raise GovernanceValidationError(
        f"A campaign that is {current.value} cannot become {requested.value}. "
        f"From {current.value} it may become: {permitted}.",
        field="status",
    )


def decisions_requiring_rationale(
    requirement: CommentRequirement = CommentRequirement.STANDARD,
) -> frozenset[DecisionKind]:
    """Which decisions must carry a reason under one campaign's comment requirement.

    A campaign can only make the rule **stricter**. ``STANDARD`` is
    :data:`DECISIONS_REQUIRING_RATIONALE`, which the database enforces independently; there
    is no requirement that returns less than it, and a value that did would be a way to
    record an unexplained revocation.
    """
    if requirement is CommentRequirement.ALWAYS:
        return RATIONALE_ALWAYS_REQUIRED
    return DECISIONS_REQUIRING_RATIONALE


def validate_rationale(
    decision: DecisionKind,
    rationale: str | None,
    *,
    requirement: CommentRequirement = CommentRequirement.STANDARD,
) -> str | None:
    """The rationale rules, as one function both the service and the tests read.

    ``requirement`` comes from the campaign the item belongs to. It defaults to
    ``STANDARD`` so that every caller written before campaigns could configure this — and
    every test that does not care — gets the rule the database enforces anyway.
    """
    cleaned = None if rationale is None else rationale.strip()
    if not cleaned:
        cleaned = None
    if cleaned is not None and len(cleaned) > MAX_RATIONALE_LENGTH:
        raise GovernanceValidationError(
            f"A decision rationale is at most {MAX_RATIONALE_LENGTH} characters; this one "
            f"is {len(cleaned)}. Summarize here and keep the detail in the ticket.",
            field="rationale",
        )
    if cleaned is not None:
        return cleaned
    if decision in DECISIONS_REQUIRING_RATIONALE:
        raise GovernanceValidationError(
            f"A '{decision.value}' decision must say why. An unexplained removal cannot be "
            "defended to the person who loses access, an unexplained abstention tells the "
            "campaign owner nothing about who should have been asked instead, and an "
            "unexplained 'investigate' hands the next person nothing to investigate.",
            field="rationale",
        )
    if decision in decisions_requiring_rationale(requirement):
        raise GovernanceValidationError(
            f"This campaign requires a comment on every decision, '{decision.value}' "
            "included. It was created that way because an approval with nothing beside it "
            "is not evidence that anybody looked.",
            field="rationale",
        )
    return None


def validate_scopes(focus: CampaignFocus, scopes: Sequence[CampaignScope]) -> None:
    """Every scope must be one the focus can actually select by."""
    if not scopes:
        raise GovernanceValidationError(
            "A campaign needs at least one scope. A campaign with none would either review "
            "the whole estate or nothing, and an item set nobody bounded is not something a "
            "reviewer can be accountable for.",
            field="scopes",
        )
    permitted = SCOPE_KINDS_FOR_FOCUS[focus]
    wrong = [scope for scope in scopes if scope.kind not in permitted]
    if wrong:
        named = ", ".join(sorted({scope.kind.value for scope in wrong}))
        allowed = ", ".join(sorted(kind.value for kind in permitted))
        raise GovernanceValidationError(
            f"A {focus.value}-focused campaign cannot be scoped by {named}. "
            f"It accepts: {allowed}. A campaign selecting by one end of a grant and "
            "claiming completeness about the other would answer neither question.",
            field="scopes",
        )


def validate_baseline(baseline_at: dt.datetime, now: dt.datetime) -> dt.datetime:
    """A baseline is a past instant, in UTC.

    A future baseline is refused rather than clamped: it would freeze a campaign against
    state nobody has observed yet, so the item set would be empty today and different
    tomorrow — and "reproducible against its baseline" would be false by construction.
    """
    if baseline_at.tzinfo is None or baseline_at.tzinfo.utcoffset(baseline_at) is None:
        raise GovernanceValidationError(
            "A campaign baseline needs a timezone-aware instant. A naive one cannot be "
            "ordered against observations from a collector in another time zone, and the "
            "campaign would freeze a different moment than the one intended.",
            field="baseline_at",
        )
    moment = baseline_at.astimezone(dt.UTC)
    if moment > now:
        raise GovernanceValidationError(
            f"The baseline {moment.isoformat()} is in the future. A campaign freezes what "
            "was observed at an instant, and nothing has been observed at that one yet.",
            field="baseline_at",
        )
    return moment


def validate_due_date(due_at: dt.datetime | None, baseline_at: dt.datetime) -> dt.datetime | None:
    """A due date after the baseline, or none at all."""
    if due_at is None:
        return None
    if due_at.tzinfo is None or due_at.tzinfo.utcoffset(due_at) is None:
        raise GovernanceValidationError(
            "A due date needs a timezone-aware instant; a naive one means a different "
            "deadline to every reader.",
            field="due_at",
        )
    moment = due_at.astimezone(dt.UTC)
    if moment <= baseline_at:
        raise GovernanceValidationError(
            f"The due date {moment.isoformat()} is at or before the baseline "
            f"{baseline_at.isoformat()}, so the campaign would be overdue before anyone "
            "could open it.",
            field="due_at",
        )
    return moment


def validate_note(note: str | None, *, field_name: str) -> str | None:
    cleaned = None if note is None else note.strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_NOTE_LENGTH:
        raise GovernanceValidationError(
            f"A {field_name} is at most {MAX_NOTE_LENGTH} characters; this one is {len(cleaned)}.",
            field=field_name,
        )
    return cleaned


def weakest_certainty(grants: Iterable[GrantEvidence]) -> Certainty:
    """The weakest certainty among an item's grants, or ``UNOBSERVED`` for none.

    ``UNOBSERVED`` for an empty item is the honest default and it is unreachable in
    practice: generation never creates an item with no evidence, because an item *is* the
    evidence. Returning the strongest value instead would make the one bug that could
    produce an empty item present itself as a fully observed answer.
    """
    order = (Certainty.OBSERVED, Certainty.INFERRED, Certainty.BACKFILLED, Certainty.UNOBSERVED)
    weakest: Certainty | None = None
    for grant in grants:
        if weakest is None or order.index(grant.certainty) > order.index(weakest):
            weakest = grant.certainty
    return Certainty.UNOBSERVED if weakest is None else weakest
