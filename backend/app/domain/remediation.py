r"""The remediation vocabulary: the closed sets of values a proposed change is described with.

Only enumerations live here, for the reason :mod:`app.domain.governance` gives: the values
appear in database check constraints, the constraints are generated from the enum, and the
enum must therefore be importable by :mod:`app.models.schema` without importing anything
that imports it back. :mod:`app.remediation.model` builds the values and invariants on top.

**Every value in this module describes an instruction, never an act.** A change plan is
ADG's written description of work a human administrator might do in Windows. ADG does not do
it: there is no write adapter compiled into the application, ``Capability.REMEDIATION_EXECUTE``
is held by no role, and no route reaches an executor. See ADR-0035, and
``docs/architecture/remediation.md`` §8 for what enabling a write path would require.

That is why there is no ``EXECUTED`` status and no ``APPLIED`` change outcome. ADG cannot
observe that a change was made; only a later scan can, and when one does the change shows up
as ordinary collected state and as a drift verdict against the plan. Inventing a status ADG
sets on its own authority would be the quietest possible way for this product to start
claiming it changed something.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ApprovalDecision",
    "ChangePlanStatus",
    "ChangeTargetKind",
    "ExecutionMode",
    "PlannedChangeKind",
    "PreconditionVerdict",
]


class ChangeTargetKind(StrEnum):
    """What a single planned change acts on.

    Deliberately not :class:`app.domain.governance.ReviewTargetKind`, which distinguishes a
    share's permissions from the file system's and has no third case. A membership change
    acts on a *group*, which is neither, and sharing the enum would eventually let a review
    item's target kind be read as a plan's — two vocabularies that agree on two values out
    of three, which is the shape of mistake that survives review.
    """

    SHARE = "share"
    """The share-level ACL of an SMB share. Changed on the file server, in Share
    Permissions, and not visible from the file system's own security tab."""

    RESOURCE = "resource"
    """The NTFS discretionary ACL of a directory or file."""

    GROUP = "group"
    """A security group's membership. Changed in Active Directory, or in Local Users and
    Groups for a host-scoped group — a distinction the plan carries, because a BUILTIN SID
    names a different group on every computer (ADR-0001)."""


class PlannedChangeKind(StrEnum):
    """The closed vocabulary of changes a plan may describe.

    Closed for the same reason :class:`app.simulation.overlay.ChangeKind` is: every kind
    here translates to exactly one simulated change, so a kind with no translation would be
    accepted, stored, approved, exported into somebody's runbook, and never simulated — a
    change nobody had measured, wearing the paperwork of one that had been.

    :meth:`translatable` is the property that guarantees it, and
    ``tests/remediation/test_translation.py`` asserts the map is total over this enum rather
    than testing the kinds somebody remembered.
    """

    REMOVE_GROUP_MEMBER = "remove_group_member"
    """Take a principal out of a group. The most common outcome of a review, and the one
    whose blast radius is least obvious: the member usually holds access through several
    groups, and removing one of them is measured rather than assumed."""

    REMOVE_SHARE_ACE = "remove_share_ace"
    MODIFY_SHARE_ACE = "modify_share_ace"
    """Narrow a share ACE to a smaller permission level or mask. Never widens: see
    :func:`app.remediation.model.validate_narrowing`."""

    REMOVE_NTFS_ACE = "remove_ntfs_ace"
    MODIFY_NTFS_ACE = "modify_ntfs_ace"
    """Narrow an NTFS ACE's access mask. Never widens, and never converts an Allow to a
    Deny: a Deny added on the way to removing an Allow reaches principals the reviewed
    grant never did."""

    REPLACE_WITH_GROUP = "replace_with_group"
    """Remove a principal's direct entry and add the principal to a group that already
    holds the access. Two changes in one instruction, deliberately: performed separately
    and in the wrong order they produce an interval in which somebody has lost access they
    were meant to keep, and a plan that did not say they belong together would let that
    happen without anybody deciding to."""

    @property
    def removes_access(self) -> bool:
        """Whether this kind can take an access path away.

        ``MODIFY`` counts, because a narrowing is a removal of part of a mask, and
        ``REPLACE_WITH_GROUP`` counts because its removal half runs whether or not its
        addition half achieves what was intended. Only used to decide whether the
        alternate-path analysis must run; the simulation's own
        :attr:`app.simulation.overlay.ChangeKind.removes` decides it again on the changes
        this translates to, which is the answer that actually governs.
        """
        return True


class ChangePlanStatus(StrEnum):
    """Where a change plan is in its life.

    The lifecycle exists to make one thing impossible: an instruction leaving ADG that
    nobody approved, or that was approved against facts that have since moved. Every
    transition is checked in :func:`app.remediation.model.validate_transition`, and the two
    gates that matter — a current simulation before submission, satisfied preconditions
    before export — are checked again at the moment they are relied on rather than only when
    the status was set.
    """

    DRAFT = "draft"
    """Being written. Its changes may be simulated; it may not be approved."""

    PENDING_APPROVAL = "pending_approval"
    """Submitted, with a blast-radius report attached. Awaiting a different person."""

    APPROVED = "approved"
    """Approved by somebody who is not the requestor, against a named plan digest and a
    named collection basis. Both are recorded, because an approval that did not say *what*
    it approved would still be attached to the plan after the plan changed."""

    REJECTED = "rejected"
    """Refused, with a reason. Terminal: a rejected plan is superseded by a new one rather
    than edited, so the refusal and what it refused stay legible together."""

    EXPORTED = "exported"
    """A signed change plan has been handed to a human administrator. **Still not applied by
    ADG**, and ADG has no way to learn whether it was applied at all — the next scan reports
    the estate, and comparing that to the plan is what says whether the work happened."""

    INVALIDATED = "invalidated"
    """The estate moved underneath it: an entry the plan names has changed or gone, or the
    collection basis moved and re-checking found drift. Not an error and not a failure —
    it is the stale-state rejection working — and the fix is a new plan built on current
    state, not an edit to this one."""

    CANCELED = "canceled"
    """Abandoned by the requestor or a planner. Kept, with everything it said, because a
    plan that deleted itself would take its approval history with it."""


class ApprovalDecision(StrEnum):
    """What an approver concluded about a plan.

    There is no ``abstain``. An approver who cannot judge a plan has not approved it, and
    the plan stays pending — which is already what "I did not decide" looks like, and is
    distinguishable from a rejection in a way an abstention recorded as a decision would
    not be.
    """

    APPROVE = "approve"
    REJECT = "reject"


class PreconditionVerdict(StrEnum):
    """Whether one planned change still describes the estate ADG last observed.

    Four values because "the ACE is not there" means three different things, and they send
    an operator to three different places — the same argument
    :class:`app.governance.drift.DriftVerdict` makes for a review item, applied to an
    instruction somebody is about to carry out.
    """

    SATISFIED = "satisfied"
    """The entry or edge is present and byte-identical to what the plan was written
    against. The instruction still describes reality."""

    CHANGED = "changed"
    """Present, and different. Somebody has edited the entry since the plan was written —
    so the mask being removed is not the mask that was reviewed, and executing the
    instruction would take away something nobody measured."""

    MISSING = "missing"
    """Gone. Either somebody already did the work, or they did something else. Harmless to
    skip and dangerous to assume: the plan is invalidated rather than quietly shortened."""

    UNOBSERVED = "unobserved"
    """ADG has no current observation of the object — the scan that would confirm it has
    not run, or its scope no longer covers the target. Deliberately separate from
    ``MISSING``: "it is gone" and "nobody has looked" read identically in the data and
    lead to opposite conclusions, and only one of them means the work is done."""

    @property
    def blocks_export(self) -> bool:
        """Whether this verdict stops a signed instruction from leaving ADG.

        Everything but ``SATISFIED`` does. ``UNOBSERVED`` blocks because an unverifiable
        precondition is not a satisfied one, and the whole point of the check is that the
        person executing the plan is entitled to assume ADG checked.
        """
        return self is not PreconditionVerdict.SATISFIED


class ExecutionMode(StrEnum):
    """Which remediator implementation this deployment has configured.

    The default is :attr:`DISABLED` and there is deliberately no production value. Adding
    one is not a configuration change: it is a new adapter, a new capability grant, a
    credential with authority over the estate, and the review described in
    ``docs/architecture/remediation.md`` §8. A setting that could switch a real write path
    on would mean the write path already existed, which is the thing this phase exists not
    to ship.
    """

    DISABLED = "disabled"
    """No remediator. Every execution call raises
    :class:`app.remediation.executor.RemediationDisabled`. The only value a production
    deployment may hold, enforced in :class:`app.config.Settings`."""

    LAB = "lab"
    """A fixture-backed remediator for development and tests. It mutates an in-memory copy
    of a JSON fixture and can reach nothing else — no Windows API, no collected table, no
    file it was not pointed at. Refused outright when ``ADG_ENVIRONMENT=production``."""
