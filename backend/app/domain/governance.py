r"""The governance vocabulary: the closed sets of values a review is described with.

Only enumerations live here, and they live in :mod:`app.domain` rather than beside the rest
of the governance code for the same reason :class:`app.models.schema.VersionOrigin` lives in
the schema module: the values appear in database check constraints, the constraints are
generated from the enum, and the enum must therefore be importable by
:mod:`app.models.schema` without importing anything that imports it back.
:mod:`app.governance.model` — which does depend on storage, through
:mod:`app.history.model` — builds its values and invariants on top of these.

Nothing here says anything about Windows. Every value describes an **ADG record about** a
collected fact: who ADG holds accountable, what somebody concluded, what change somebody
proposed. See ADR-0028 for why that distinction is structural rather than a naming
convention.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "CampaignFocus",
    "CampaignStatus",
    "CommentRequirement",
    "DecisionKind",
    "GovernanceEventType",
    "OwnershipRole",
    "RemediationAction",
    "RemediationStatus",
    "ReviewItemStatus",
    "ReviewScopeKind",
    "ReviewTargetKind",
]


class OwnershipRole(StrEnum):
    """Why ADG holds this party accountable for a resource.

    Both values are ADG metadata. Neither says anything about the owner field of the Windows
    security descriptor, which is collected separately and never written.
    """

    OWNER = "owner"
    """Accountable for the access on this resource."""

    DELEGATE = "delegate"
    """Acts for the owner. Recorded separately so an audit can tell a review performed by
    the accountable party from one performed by a stand-in."""


class ReviewTargetKind(StrEnum):
    """Which access-control list an item's grant sits on.

    Deliberately not :class:`app.domain.ResourceKind`, which distinguishes a directory from
    a file. This distinguishes the *share's* permissions from the *file system's*, and the
    two are removed in different places, often by different people.
    """

    SHARE = "share"
    """An entry on the share permissions of an SMB share."""

    RESOURCE = "resource"
    """An entry on the NTFS discretionary ACL of a directory or file."""


class CampaignFocus(StrEnum):
    """Which end of the grant a campaign was cut from.

    A grant is a relation with two ends, so both focuses produce items describing a
    ``(principal, target)`` pair. What differs is which end was *enumerated*, and therefore
    what the campaign claims to be complete about: a resource-focused campaign over one
    share claims every principal named on it; a principal-focused campaign over one group
    claims every place that group is named. Neither claims the other.
    """

    RESOURCE = "resource"
    PRINCIPAL = "principal"


class ReviewScopeKind(StrEnum):
    """What a campaign scope selects.

    Deliberately not :class:`app.contracts.v1.common.ScopeKind`, which a collector uses to
    declare what it was authoritative for. These select review targets out of state ADG
    already holds, and sharing one enum would eventually let a collector's claim of coverage
    be read as a review's claim of coverage.
    """

    SERVER = "server"
    """Every share on one server, and every NTFS resource ADG holds beneath them."""

    SHARE = "share"
    """One share: its share-level ACL, and the NTFS ACLs of the resources under it."""

    DIRECTORY_TREE = "directory_tree"
    """One directory and everything ADG has read below it, by UNC prefix."""

    PRINCIPAL = "principal"
    """One principal — usually a group — wherever it is named on an ACL."""


class CampaignStatus(StrEnum):
    """Where a campaign is in its life.

    ``DRAFT`` is the only state in which items may still be built and ``ACTIVE`` the only one
    in which a decision may be recorded. That pairing is what makes a campaign's coverage
    auditable: once reviewers can answer, nothing more can be added for them to answer about.
    """

    DRAFT = "draft"
    """Created and scoped. Items may be generated; no decision may be recorded."""

    ACTIVE = "active"
    """Frozen against its baseline and open for decisions."""

    CLOSED = "closed"
    """Finished. Decisions stay readable and no new one may be recorded."""

    CANCELED = "canceled"
    """Abandoned. Decisions already recorded stay, and stay attributable — a campaign that
    deleted its own evidence on cancellation would be a way to unmake an attestation."""


class ReviewItemStatus(StrEnum):
    """Whether one item has been answered."""

    PENDING = "pending"
    DECIDED = "decided"


class DecisionKind(StrEnum):
    """What a reviewer concluded about one grant.

    ``ABSTAIN`` is a first-class answer rather than a way of leaving an item pending. A
    reviewer who cannot judge a grant has said something real — usually that the wrong
    person was asked — and recording it beats an item that sits unanswered and is
    indistinguishable from one nobody opened.
    """

    CERTIFY = "certify"
    """The grant is appropriate and should remain."""

    REVOKE = "revoke"
    """The grant should be removed. ADG does not remove it."""

    MODIFY = "modify"
    """The principal should keep access at different rights. The rationale says which."""

    ABSTAIN = "abstain"
    """The reviewer cannot judge this grant, and says why."""

    INVESTIGATE = "investigate"
    """Something about this grant is wrong and the reviewer cannot yet say what should
    happen to it. Deliberately distinct from ``ABSTAIN``: an abstention says *the wrong
    person was asked*, and the campaign owner's fix is to ask somebody else; this says *the
    right person was asked, and the answer needs work before it can be given*, and the fix
    is an investigation. Collapsing the two would make a queue of items awaiting follow-up
    indistinguishable from a queue of items routed to the wrong reviewer, and those go to
    two different people."""


class CommentRequirement(StrEnum):
    """How firmly a campaign insists that a decision explain itself.

    Both values only ever **add** to the rule the database already enforces: every decision
    but ``certify`` must carry a rationale, checked by
    ``ck_review_decisions_reason_required``. There is deliberately no value making a
    rationale optional for a revocation — a campaign setting must not be able to switch off
    an invariant that exists so the person who loses access can be told why — so what is
    configurable is whether the campaign *also* demands one for the expected answer.
    """

    STANDARD = "standard"
    """A rationale on every decision but ``certify``. The default, and what every campaign
    created before this setting existed did."""

    ALWAYS = "always"
    """A rationale on every decision, ``certify`` included. For a review whose output an
    external auditor reads, where "approved" with nothing beside it is not evidence that
    anybody looked."""


class RemediationAction(StrEnum):
    """A change somebody might make in Windows as a result of a decision.

    Every member is a **proposal**. ADG performs none of them: the application is read-only
    toward the estate (SECURITY.md), ``Capability.REMEDIATION_EXECUTE`` is held by no role,
    and nothing in the governance package can reach a Windows API.
    """

    REMOVE_ACE = "remove_ace"
    REDUCE_RIGHTS = "reduce_rights"
    REMOVE_GROUP_MEMBER = "remove_group_member"
    REPLACE_WITH_GROUP = "replace_with_group"
    MANUAL_REVIEW = "manual_review"
    """No mechanical change fits and a person has to look. Recorded rather than left blank,
    so that "we decided to revoke and then nothing happened" is visible."""


class RemediationStatus(StrEnum):
    """How far a proposal has travelled. No value here means ADG changed anything."""

    PROPOSED = "proposed"
    EXPORTED = "exported"
    """Handed to whatever performs changes. Still not applied *by ADG*."""

    WITHDRAWN = "withdrawn"


class GovernanceEventType(StrEnum):
    """One entry in the immutable audit trail.

    The set is closed and the values are stored, so a reader years from now can enumerate
    what kinds of thing ever happened without parsing free text.
    """

    CAMPAIGN_CREATED = "campaign.created"
    CAMPAIGN_GENERATED = "campaign.generated"
    CAMPAIGN_ACTIVATED = "campaign.activated"
    CAMPAIGN_CLOSED = "campaign.closed"
    CAMPAIGN_CANCELED = "campaign.canceled"
    REVIEWER_ASSIGNED = "reviewer.assigned"
    REVIEWER_REVOKED = "reviewer.revoked"
    DECISION_RECORDED = "decision.recorded"
    DECISION_SUPERSEDED = "decision.superseded"
    REMEDIATION_PROPOSED = "remediation.proposed"
    REMEDIATION_WITHDRAWN = "remediation.withdrawn"
    OWNER_ASSIGNED = "owner.assigned"
    OWNER_REVOKED = "owner.revoked"

    # Phase 10C: the change-plan lifecycle. These share the trail rather than opening a
    # second one because they are the same story continuing -- a campaign produced a
    # decision, the decision produced a proposal, the proposal became a plan, somebody
    # approved it and somebody exported it -- and an auditor who had to join two
    # append-only tables to read it would be reading two accounts of one sequence. They
    # live on their own chain (``plan:<uuid>``; see
    # :func:`app.remediation.model.plan_chain`), for the reason a campaign does: a plan is
    # the unit that is examined and exported, and its head digest is what an export
    # records.
    PLAN_CREATED = "plan.created"
    PLAN_SIMULATED = "plan.simulated"
    """A blast radius was computed for the plan. Recorded separately from the submission
    because "submitted without anybody measuring the impact" must be answerable from the
    trail, and it is only answerable if measuring leaves a mark of its own."""

    PLAN_SUBMITTED = "plan.submitted"
    PLAN_APPROVED = "plan.approved"
    PLAN_REJECTED = "plan.rejected"
    PLAN_EXPORTED = "plan.exported"
    """A signed instruction left ADG. **Not** a claim that anything was changed: ADG cannot
    know that, and learns the estate's new state only from the next collection."""

    PLAN_INVALIDATED = "plan.invalidated"
    PLAN_CANCELED = "plan.canceled"
