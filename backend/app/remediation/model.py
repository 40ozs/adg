r"""What a change plan is, what it must say, and what it may never say.

Pure. No database, no framework, no clock beyond the instants a caller passes in. Everything
here is a value with an invariant attached, so that the rules a plan has to satisfy are
readable in one file and testable without a server.

Four ideas carry the module.

**A change names the exact object it acts on.** Not "Alice's access to Finance" — the
``ace_key`` of one entry on one DACL, or one ``(group, member)`` edge, by the identity the
collector reported. A plan that named a resource and a trustee would be ambiguous the moment
a second entry existed for the same pair, and the person executing it would resolve the
ambiguity by guessing.

**A change carries the state it was written against.** :class:`EntrySnapshot` is the entry as
ADG last observed it, frozen into the plan and digested. That digest is the precondition: at
export time ADG re-reads the object and refuses if what it finds is not byte-identical. So an
entry somebody widened between the review and the change window stops the instruction instead
of being narrowed from a mask nobody measured.

**A modification may only narrow.** Every rule in :func:`validate_narrowing` exists because the
opposite mistake is silent: a plan that widens a mask, converts an Allow to a Deny, or grants
a permission level above the one observed reads exactly like a remediation and is an
escalation. Remediation that can grant access is not remediation.

**An approval is of a digest, not of a plan.** :func:`plan_digest` covers every field an
approver could have read and acted on. An approval records the digest it was given, and
export compares. Editing a plan after approval therefore invalidates the approval by
arithmetic rather than by anybody remembering to clear it — which is the only way that rule
survives a future author adding a field.

See ADR-0035 (a plan is an instruction), ADR-0036 (approval binds to a digest and a basis)
and ADR-0038 (three people, not two).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from app.domain import (
    AceSource,
    AceType,
    MembershipEdgeKind,
    PrincipalKind,
    SharePermission,
)
from app.domain.remediation import (
    ApprovalDecision,
    ChangePlanStatus,
    ChangeTargetKind,
    PlannedChangeKind,
    PreconditionVerdict,
)
from app.remediation.errors import RemediationValidationError

__all__ = [
    "APPROVABLE_STATUSES",
    "DIGEST_LENGTH",
    "EDITABLE_STATUSES",
    "MAX_PLAN_CHANGES",
    "MAX_RATIONALE_LENGTH",
    "MAX_TITLE_LENGTH",
    "MEMBERSHIP_KINDS",
    "NTFS_KINDS",
    "PLAN_DOCUMENT_VERSION",
    "PLAN_TRANSITIONS",
    "SHARE_KINDS",
    "TERMINAL_STATUSES",
    "ApprovalRecord",
    "ChangePlan",
    "ChangePrecondition",
    "EntrySnapshot",
    "MembershipSnapshot",
    "PlanExport",
    "PlannedChange",
    "PreconditionReport",
    "canonical_json",
    "change_digest",
    "plan_chain",
    "plan_digest",
    "validate_changes",
    "validate_narrowing",
    "validate_rationale",
    "validate_title",
    "validate_transition",
]

DIGEST_LENGTH: Final = 64
"""Hex characters in every digest this module produces. Full SHA-256, matching
``GOVERNANCE_DIGEST_LENGTH``: a plan digest is what an approval is pinned to and what a
signature covers, so it is not truncated the way a cache key may be."""

MAX_PLAN_CHANGES: Final = 100
"""Changes one plan may hold.

A ceiling rather than a guideline, and a much lower one than
:data:`app.simulation.overlay.MAX_CHANGES` allows, for a different reason: a simulation of two
hundred changes is a report somebody reads, and a *change plan* of two hundred changes is work
somebody performs by hand at two in the morning. A plan nobody can hold in their head is one
whose blast radius nobody actually checked. Refused rather than truncated — see
:func:`validate_changes`.
"""

MAX_TITLE_LENGTH: Final = 200
MAX_RATIONALE_LENGTH: Final = 4_000

PLAN_DOCUMENT_VERSION: Final = "1.0"
"""The version of the canonical plan document a digest and a signature are taken over.

Stored on every export. A signature verifies a *document*, and a document whose shape changed
under a reader would verify against a different set of bytes than the one the approver saw —
so the version travels with it and a verifier that does not recognize it refuses rather than
reinterpreting.
"""

MEMBERSHIP_KINDS: Final[frozenset[PlannedChangeKind]] = frozenset(
    {PlannedChangeKind.REMOVE_GROUP_MEMBER}
)
SHARE_KINDS: Final[frozenset[PlannedChangeKind]] = frozenset(
    {PlannedChangeKind.REMOVE_SHARE_ACE, PlannedChangeKind.MODIFY_SHARE_ACE}
)
NTFS_KINDS: Final[frozenset[PlannedChangeKind]] = frozenset(
    {PlannedChangeKind.REMOVE_NTFS_ACE, PlannedChangeKind.MODIFY_NTFS_ACE}
)

#: The lifecycle, as a graph. Every transition a plan may make, and no others.
#:
#: Three properties worth reading off it. ``REJECTED``, ``EXPORTED`` and ``CANCELED`` lead
#: nowhere: a refused plan is superseded by a new one rather than argued back into life, and
#: an exported one has left the building. ``APPROVED`` can fall back to ``PENDING_APPROVAL``
#: only through ``INVALIDATED``, so a plan can never quietly regain an approval it lost.
#: And nothing returns to ``DRAFT`` — a plan whose facts have moved is rewritten, because the
#: alternative is an approval history attached to changes it never described.
PLAN_TRANSITIONS: Final[dict[ChangePlanStatus, frozenset[ChangePlanStatus]]] = {
    ChangePlanStatus.DRAFT: frozenset(
        {ChangePlanStatus.PENDING_APPROVAL, ChangePlanStatus.CANCELED, ChangePlanStatus.INVALIDATED}
    ),
    ChangePlanStatus.PENDING_APPROVAL: frozenset(
        {
            ChangePlanStatus.APPROVED,
            ChangePlanStatus.REJECTED,
            ChangePlanStatus.CANCELED,
            ChangePlanStatus.INVALIDATED,
        }
    ),
    ChangePlanStatus.APPROVED: frozenset(
        {ChangePlanStatus.EXPORTED, ChangePlanStatus.CANCELED, ChangePlanStatus.INVALIDATED}
    ),
    ChangePlanStatus.EXPORTED: frozenset(),
    ChangePlanStatus.REJECTED: frozenset(),
    ChangePlanStatus.INVALIDATED: frozenset(),
    ChangePlanStatus.CANCELED: frozenset(),
}

TERMINAL_STATUSES: Final[frozenset[ChangePlanStatus]] = frozenset(
    status for status, onward in PLAN_TRANSITIONS.items() if not onward
)

EDITABLE_STATUSES: Final[frozenset[ChangePlanStatus]] = frozenset({ChangePlanStatus.DRAFT})
"""The only status in which a plan's changes may be altered. Everything else has either been
put in front of an approver or answered by one, and both are statements about a specific set
of changes."""

APPROVABLE_STATUSES: Final[frozenset[ChangePlanStatus]] = frozenset(
    {ChangePlanStatus.PENDING_APPROVAL}
)


def canonical_json(payload: Any) -> str:
    """The one serialization every digest and every signature in this package is taken over.

    Sorted keys, no whitespace, no ASCII escaping. Identical to
    :func:`app.governance.audit._canonical` and deliberately so: two canonicalizations in one
    codebase is two answers to "what exactly was signed", and the day they disagree is the day
    a verification fails for a reason nobody can find.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def plan_chain(plan_id: UUID) -> str:
    """The audit chain key for one plan's events.

    Per plan, for the reason :func:`app.governance.audit.campaign_chain` is per campaign: a
    plan is the unit an auditor examines and exports, and the head digest of its chain is what
    an export records so that a later verification has something to compare against.
    """
    return f"plan:{plan_id}"


# --------------------------------------------------------------------------------------
# What the plan was written against
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EntrySnapshot:
    """One access-control entry as ADG last observed it, frozen into a plan.

    This is the *precondition*, not decoration. :attr:`content_digest` is recomputed against
    current collected state before a plan may be exported, and anything but equality stops
    the export — so the fields chosen here decide what counts as "somebody changed this entry
    underneath us".

    Everything that alters the entry's effect is included. ``order_index`` is included even
    though it is not part of the entry's *content*, because a Deny moved behind an Allow
    changes what the DACL does without changing any ACE. ``inherited_from`` is included
    because an entry that started being inherited from a different ancestor is a different
    entry for the purposes of removing it.

    Deliberately **excluded**: ``version_id``, ``observed_from``, ``last_confirmed_at`` and
    ``certainty``. Those say when ADG learned the entry and how firmly — properties of the
    observation, not of the entry — and a re-scan that confirms an unchanged ACE must not read
    as somebody having edited it. This is the same split
    :class:`app.governance.drift.GrantEvidence` draws between its evidence digest and its
    content digest, and for the same reason.
    """

    ace_key: str
    trustee_sid: str
    trustee_key: str
    ace_type: AceType
    access_mask: int | None = None
    """``None`` only for a share ACE a source reported as a named permission level."""

    permission: SharePermission | None = None
    """Share layer only. A source reports a mask or a level, never both."""

    ace_flags: int | None = None
    """NTFS layer only: the raw ``ACE_HEADER.AceFlags`` byte, unknown bits included."""

    source: AceSource | None = None
    inherited_from: str | None = None
    order_index: int | None = None
    version_id: int | None = None
    """The ``object_versions`` row this was copied from, when the plan was built out of a
    campaign's frozen evidence. Carried for provenance and **not digested**: it moves whenever
    the timeline writes a new row, including for an entry removed and restored unchanged."""

    observed_from: dt.datetime | None = None
    last_confirmed_at: dt.datetime | None = None

    def __post_init__(self) -> None:
        if not self.ace_key.strip():
            raise RemediationValidationError(
                "A planned change names one entry, by the key the collector reported. "
                "An empty key names every entry or none, and a person executing the plan "
                "would have to guess which.",
                field="ace_key",
            )
        if self.access_mask is None and self.permission is None:
            raise RemediationValidationError(
                f"Entry {self.ace_key} carries neither an access mask nor a permission level, "
                "so there is nothing to say what it grants and nothing to check it against "
                "later.",
                field="access_mask",
            )
        if self.access_mask is not None and not 0 <= self.access_mask <= 0xFFFFFFFF:
            raise RemediationValidationError(
                f"An access mask is a 32-bit value; {self.access_mask} is outside it.",
                field="access_mask",
            )

    @property
    def is_inherited(self) -> bool:
        return self.source is AceSource.INHERITED

    @property
    def is_deny(self) -> bool:
        return self.ace_type is AceType.DENY

    def as_digestible(self) -> dict[str, Any]:
        """The fields the precondition digest is taken over. See the class docstring."""
        return {
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
        }

    @property
    def content_digest(self) -> str:
        return _digest(self.as_digestible())

    def document(self) -> dict[str, Any]:
        """The full record, provenance included, as stored and as exported."""
        return {
            **self.as_digestible(),
            "version_id": self.version_id,
            "observed_from": _instant(self.observed_from),
            "last_confirmed_at": _instant(self.last_confirmed_at),
            "content_digest": self.content_digest,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> EntrySnapshot:
        permission = document.get("permission")
        source = document.get("source")
        return cls(
            ace_key=document["ace_key"],
            trustee_sid=document["trustee_sid"],
            trustee_key=document["trustee_key"],
            ace_type=AceType(document["ace_type"]),
            access_mask=document.get("access_mask"),
            permission=None if permission is None else SharePermission(permission),
            ace_flags=document.get("ace_flags"),
            source=None if source is None else AceSource(source),
            inherited_from=document.get("inherited_from"),
            order_index=document.get("order_index"),
            version_id=document.get("version_id"),
            observed_from=_parse_instant(document.get("observed_from")),
            last_confirmed_at=_parse_instant(document.get("last_confirmed_at")),
        )


@dataclass(frozen=True, slots=True)
class MembershipSnapshot:
    """One membership edge as ADG last observed it.

    The edge kind is part of the identity rather than a label. A BUILTIN group's key carries
    its host (``fs01|S-1-5-32-544``) and names a different group on every computer, so an edge
    into one is a *local* group membership and an edge into a domain group is not — and the
    two are removed with different tools, on different machines, by different people. A plan
    that got this wrong would send somebody to a domain controller to edit a local group.
    """

    group_key: str
    member_key: str
    member_sid: str
    edge_kind: MembershipEdgeKind = MembershipEdgeKind.DIRECTORY_GROUP_MEMBER
    member_kind: PrincipalKind | None = None
    group_display_name: str | None = None
    member_display_name: str | None = None
    version_id: int | None = None
    observed_from: dt.datetime | None = None
    last_confirmed_at: dt.datetime | None = None

    def __post_init__(self) -> None:
        if not self.group_key.strip() or not self.member_key.strip():
            raise RemediationValidationError(
                "A membership change names a group and a member, by the keys the collector "
                "reported.",
                field="group_key" if not self.group_key.strip() else "member_key",
            )
        if self.group_key == self.member_key:
            raise RemediationValidationError(
                f"A group cannot be a direct member of itself ({self.group_key}). Windows "
                "does not create such an edge, so no plan may claim to remove one.",
                field="member_key",
            )
        local = self.edge_kind is MembershipEdgeKind.LOCAL_GROUP_MEMBER
        if local != (self.host_key is not None):
            raise RemediationValidationError(
                f"{self.group_key} and {self.edge_kind.value} disagree about where this group "
                "lives. A host-scoped key means a local group, edited in Local Users and "
                "Groups on that computer; an unscoped key means a directory group, edited in "
                "Active Directory. A plan that confused the two would send somebody to the "
                "wrong machine.",
                field="edge_kind",
            )

    @property
    def host_key(self) -> str | None:
        """The computer a host-scoped group lives on, read off its key.

        Split on the last separator: a SID never contains one, and a host name may contain
        very nearly anything else. The same derivation
        :attr:`app.simulation.overlay.MembershipChange.host_key` performs, kept identical on
        purpose — a plan and the simulation of that plan must agree about which machine is
        involved.
        """
        host, separator, _ = self.group_key.rpartition("|")
        return host if separator else None

    @property
    def is_local(self) -> bool:
        return self.edge_kind is MembershipEdgeKind.LOCAL_GROUP_MEMBER

    def as_digestible(self) -> dict[str, Any]:
        return {
            "group_key": self.group_key,
            "member_key": self.member_key,
            "member_sid": self.member_sid,
            "edge_kind": self.edge_kind.value,
        }

    @property
    def content_digest(self) -> str:
        return _digest(self.as_digestible())

    def document(self) -> dict[str, Any]:
        return {
            **self.as_digestible(),
            "member_kind": None if self.member_kind is None else self.member_kind.value,
            "group_display_name": self.group_display_name,
            "member_display_name": self.member_display_name,
            "version_id": self.version_id,
            "observed_from": _instant(self.observed_from),
            "last_confirmed_at": _instant(self.last_confirmed_at),
            "content_digest": self.content_digest,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> MembershipSnapshot:
        member_kind = document.get("member_kind")
        return cls(
            group_key=document["group_key"],
            member_key=document["member_key"],
            member_sid=document["member_sid"],
            edge_kind=MembershipEdgeKind(document["edge_kind"]),
            member_kind=None if member_kind is None else PrincipalKind(member_kind),
            group_display_name=document.get("group_display_name"),
            member_display_name=document.get("member_display_name"),
            version_id=document.get("version_id"),
            observed_from=_parse_instant(document.get("observed_from")),
            last_confirmed_at=_parse_instant(document.get("last_confirmed_at")),
        )


# --------------------------------------------------------------------------------------
# One planned change
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlannedChange:
    """One precise change, with the state it was written against and where it came from.

    The three halves of that sentence are the three things a reviewable instruction needs and
    the three things a hand-written change ticket usually lacks: *what exactly*, *from what
    starting point*, and *on whose authority*.
    """

    change_id: UUID
    sequence_index: int
    """Execution order within the plan, from zero and contiguous. It matters for
    ``REPLACE_WITH_GROUP``, whose two halves must not be reordered, and it is what an exported
    runbook numbers its steps by — so a person and an auditor reading the same plan are
    looking at the same step 4."""

    kind: PlannedChangeKind
    target_kind: ChangeTargetKind
    target_key: str
    """The UNC path of a directory, the storage key of a share, or the key of a group."""

    target_display: str | None = None
    """Case-preserving path or name, for the runbook. Comparison is always on
    ``target_key``."""

    principal_sid: str = ""
    """The principal losing or gaining access. SID, because SID is authoritative (ADR-0001)
    and a name is what an operator retypes into a Windows dialog."""

    principal_key: str = ""
    principal_display_name: str | None = None

    entry: EntrySnapshot | None = None
    """The ACE this change acts on. ``None`` exactly for a membership change."""

    membership: MembershipSnapshot | None = None
    """The edge this change acts on. ``None`` except for a membership change, and for the
    membership half of ``REPLACE_WITH_GROUP``."""

    after_access_mask: int | None = None
    """The mask a ``MODIFY_NTFS_ACE`` leaves behind. Must be a strict subset of the observed
    one."""

    after_permission: SharePermission | None = None
    """The level a ``MODIFY_SHARE_ACE`` leaves behind. Must be strictly lower than the
    observed one."""

    replacement_group_key: str | None = None
    replacement_group_sid: str | None = None
    replacement_group_display_name: str | None = None
    """The group a ``REPLACE_WITH_GROUP`` change puts the principal into. Named by both key
    and SID: the key is what the traversal matches and the simulation overlays, the SID is
    what a person types."""

    item_id: UUID | None = None
    """The review item whose decision produced this change, when it came from a campaign."""

    decision_id: UUID | None = None
    proposal_id: UUID | None = None
    risk_finding_key: str | None = None
    """The risk finding that produced this change, when it came from the risk engine rather
    than a review. A plan may cite either, or neither — a plan written straight out of an
    operator's own judgment is legitimate and says so by citing nothing."""

    notes: str | None = None

    def __post_init__(self) -> None:
        if self.sequence_index < 0:
            raise RemediationValidationError(
                "A step number is not negative.", field="sequence_index"
            )
        _validate_target_kind(self.kind, self.target_kind)
        if not self.target_key.strip():
            raise RemediationValidationError(
                f"A {self.kind.value} change must name what it acts on.", field="target_key"
            )
        if self.kind in MEMBERSHIP_KINDS:
            if self.membership is None:
                raise RemediationValidationError(
                    "A membership change must carry the edge it removes, as ADG observed it. "
                    "Without it there is no precondition to check and nothing to put in front "
                    "of the person performing the change.",
                    field="membership",
                )
            if self.entry is not None:
                raise RemediationValidationError(
                    "A membership change acts on an edge, not on an access-control entry.",
                    field="entry",
                )
            if self.membership.group_key != self.target_key:
                raise RemediationValidationError(
                    f"The change targets {self.target_key} and the edge is on "
                    f"{self.membership.group_key}. A step whose target and evidence disagree "
                    "would be checked against one object and performed on another.",
                    field="target_key",
                )
        else:
            if self.entry is None:
                raise RemediationValidationError(
                    f"A {self.kind.value} change must carry the entry it acts on, as ADG "
                    "observed it. That snapshot is the precondition: without it ADG cannot "
                    "tell, at export time, whether somebody has edited the entry since.",
                    field="entry",
                )
        if self.kind is PlannedChangeKind.REPLACE_WITH_GROUP:
            if self.membership is None:
                raise RemediationValidationError(
                    "Replacing a direct permission with group-based access has two halves: the "
                    "entry to remove and the group to join. This change names no group "
                    "membership, so it would remove the access and grant nothing back.",
                    field="membership",
                )
            if not (self.replacement_group_key and self.replacement_group_sid):
                raise RemediationValidationError(
                    "A replacement group is named by both key and SID: the key is what ADG "
                    "matches and simulates, the SID is what a person types into Windows.",
                    field="replacement_group_key",
                )
        validate_narrowing(self)

    # ------------------------------------------------------------------ derived

    @property
    def acts_on_membership(self) -> bool:
        return self.kind in MEMBERSHIP_KINDS or self.kind is PlannedChangeKind.REPLACE_WITH_GROUP

    @property
    def precondition_digest(self) -> str:
        """The digest export re-checks against current collected state.

        For ``REPLACE_WITH_GROUP`` this is the *entry's* digest: the half of the change that
        removes something is the half that can do harm if the world has moved, and the group
        it adds is checked separately by the simulation rather than by an equality test.
        """
        if self.entry is not None:
            return self.entry.content_digest
        assert self.membership is not None  # guaranteed by __post_init__
        return self.membership.content_digest

    @property
    def observed_mask(self) -> int | None:
        return None if self.entry is None else self.entry.access_mask

    def as_digestible(self) -> dict[str, Any]:
        """What :func:`plan_digest` covers for this change.

        Provenance (``item_id``, ``decision_id``, ``risk_finding_key``) is **included**: an
        approver who was told a change comes from a signed-off access review approved that
        claim too, and swapping the citation afterwards would change what the plan asserts
        without changing what it does.
        """
        return {
            "sequence_index": self.sequence_index,
            "kind": self.kind.value,
            "target_kind": self.target_kind.value,
            "target_key": self.target_key,
            "principal_sid": self.principal_sid,
            "principal_key": self.principal_key,
            "entry": None if self.entry is None else self.entry.as_digestible(),
            "membership": None if self.membership is None else self.membership.as_digestible(),
            "after_access_mask": self.after_access_mask,
            "after_permission": (
                None if self.after_permission is None else self.after_permission.value
            ),
            "replacement_group_key": self.replacement_group_key,
            "replacement_group_sid": self.replacement_group_sid,
            "item_id": None if self.item_id is None else str(self.item_id),
            "decision_id": None if self.decision_id is None else str(self.decision_id),
            "risk_finding_key": self.risk_finding_key,
        }

    def document(self) -> dict[str, Any]:
        return {
            **self.as_digestible(),
            "change_id": str(self.change_id),
            "target_display": self.target_display,
            "principal_display_name": self.principal_display_name,
            "replacement_group_display_name": self.replacement_group_display_name,
            "proposal_id": None if self.proposal_id is None else str(self.proposal_id),
            "notes": self.notes,
            "entry_detail": None if self.entry is None else self.entry.document(),
            "membership_detail": (None if self.membership is None else self.membership.document()),
            "precondition_digest": self.precondition_digest,
        }

    @property
    def natural_key(self) -> tuple[str, str, str, str]:
        """What makes two changes the same change, for duplicate detection within a plan.

        Two steps acting on one object are not a plan; they are a conflict — and the second
        one was written against the state the first one leaves behind, which nothing in the
        precondition check can see.
        """
        object_key = (
            self.entry.ace_key
            if self.entry is not None
            else f"{self.membership.group_key}->{self.membership.member_key}"  # type: ignore[union-attr]
        )
        return (self.target_kind.value, self.target_key, self.principal_key, object_key)


def _validate_target_kind(kind: PlannedChangeKind, target_kind: ChangeTargetKind) -> None:
    expected = {
        PlannedChangeKind.REMOVE_GROUP_MEMBER: ChangeTargetKind.GROUP,
        PlannedChangeKind.REMOVE_SHARE_ACE: ChangeTargetKind.SHARE,
        PlannedChangeKind.MODIFY_SHARE_ACE: ChangeTargetKind.SHARE,
        PlannedChangeKind.REMOVE_NTFS_ACE: ChangeTargetKind.RESOURCE,
        PlannedChangeKind.MODIFY_NTFS_ACE: ChangeTargetKind.RESOURCE,
        PlannedChangeKind.REPLACE_WITH_GROUP: ChangeTargetKind.RESOURCE,
    }[kind]
    if target_kind is not expected:
        raise RemediationValidationError(
            f"A {kind.value} change acts on a {expected.value}, not on a {target_kind.value}. "
            "The two are edited with different tools, and a plan that named the wrong one "
            "would send somebody to the wrong dialog box.",
            field="target_kind",
        )


def validate_narrowing(change: PlannedChange) -> None:
    """Refuse any modification that does not strictly reduce what a principal may do.

    Every clause here exists because the opposite mistake is silent. A plan that widens a
    mask, raises a share permission level, or turns an Allow into a Deny reads exactly like
    remediation — same title, same rationale, same approval — and is an escalation or a
    denial-of-service against people the review never considered. ADG will describe a change
    that grants access; it will not describe one while calling it remediation.

    The Deny clause is the least obvious and the most important. Converting an Allow to a Deny
    does remove the principal's access, so a rule that only checked masks would pass it — and
    a Deny reaches every group the principal is in, on every path that touches this entry,
    which is a far larger blast radius than the Allow ever had.
    """
    if change.kind is PlannedChangeKind.MODIFY_NTFS_ACE:
        entry = change.entry
        assert entry is not None  # guaranteed by __post_init__
        if change.after_access_mask is None:
            raise RemediationValidationError(
                "A modification must say what the entry should become. Without a resulting "
                "mask there is nothing to simulate and nothing to instruct.",
                field="after_access_mask",
            )
        observed = entry.access_mask or 0
        after = change.after_access_mask
        if after & ~observed:
            raise RemediationValidationError(
                f"Entry {entry.ace_key} currently grants {observed:#010x} and the plan would "
                f"leave {after:#010x}, which adds rights it does not hold. Remediation "
                "narrows; a change that grants is a grant, and belongs in a request somebody "
                "approves as one.",
                field="after_access_mask",
            )
        if after == observed:
            raise RemediationValidationError(
                f"Entry {entry.ace_key} would be left exactly as it is. A step that changes "
                "nothing still costs a change window and still reads as work done.",
                field="after_access_mask",
            )
        if entry.is_deny:
            raise RemediationValidationError(
                f"Entry {entry.ace_key} is a Deny. Narrowing a Deny *grants* access — it is "
                "the one case where taking rights out of a mask gives them to somebody — so "
                "it is not remediation and is not describable here. Remove the Deny, or leave "
                "it alone.",
                field="after_access_mask",
            )
    elif change.kind is PlannedChangeKind.MODIFY_SHARE_ACE:
        entry = change.entry
        assert entry is not None
        if change.after_permission is None and change.after_access_mask is None:
            raise RemediationValidationError(
                "A share modification must say what the entry should become: a permission "
                "level, or a mask.",
                field="after_permission",
            )
        if change.after_permission is not None and entry.permission is not None:
            order = {SharePermission.READ: 0, SharePermission.CHANGE: 1, SharePermission.FULL: 2}
            if order[change.after_permission] >= order[entry.permission]:
                raise RemediationValidationError(
                    f"Entry {entry.ace_key} grants {entry.permission.value} and the plan would "
                    f"leave {change.after_permission.value}, which is not less. A share "
                    "remediation that raises or holds a permission level is not a "
                    "remediation.",
                    field="after_permission",
                )
        if (
            change.after_access_mask is not None
            and entry.access_mask is not None
            and change.after_access_mask & ~entry.access_mask
        ):
            raise RemediationValidationError(
                f"Entry {entry.ace_key} grants {entry.access_mask:#010x} and the plan "
                f"would leave {change.after_access_mask:#010x}, which adds rights.",
                field="after_access_mask",
            )
        if entry.is_deny:
            raise RemediationValidationError(
                f"Entry {entry.ace_key} is a Deny; narrowing it grants access.",
                field="after_permission",
            )
    else:
        if change.after_access_mask is not None or change.after_permission is not None:
            raise RemediationValidationError(
                f"A {change.kind.value} change removes an entry; it has no resulting state. "
                "A step carrying one would read as a modification to whoever performs it.",
                field="after_access_mask",
            )


def validate_changes(changes: Sequence[PlannedChange]) -> None:
    """The rules a *set* of changes must satisfy, which no single change can check.

    Three of them, and each has a failure it prevents:

    * **Non-empty.** A plan with no changes is approvable, exportable, and instructs nobody to
      do anything — a signed document asserting that a review was acted on when it was not.
    * **Bounded.** See :data:`MAX_PLAN_CHANGES`.
    * **One step per object.** Two steps on the same entry conflict, and the second was
      written against state the first one leaves behind — which the precondition check reads
      as unchanged, because it compares against what ADG *observed*, not against what the
      plan's earlier steps would produce.
    * **Contiguous ordering.** Step numbers run 0..n-1 exactly once each, so the runbook a
      person follows and the list an auditor reads cannot silently omit a step.
    """
    if not changes:
        raise RemediationValidationError(
            "A change plan with no changes instructs nobody to do anything, and would still "
            "be approved and signed. Add at least one change, or cancel the plan.",
            field="changes",
        )
    if len(changes) > MAX_PLAN_CHANGES:
        raise RemediationValidationError(
            f"A change plan holds at most {MAX_PLAN_CHANGES} changes and this one has "
            f"{len(changes)}. This is a refusal rather than a truncation: a plan nobody can "
            "hold in their head is one whose blast radius nobody actually checked. Split it "
            "by server, by share, or by change window.",
            field="changes",
        )
    seen: dict[tuple[str, str, str, str], int] = {}
    for change in changes:
        key = change.natural_key
        if key in seen:
            raise RemediationValidationError(
                f"Steps {seen[key]} and {change.sequence_index} both act on {key[3]}. Two "
                "steps on one object conflict, and the second was written against the state "
                "the first one leaves behind — which nothing in the precondition check can "
                "see. Describe the end state you want as one step.",
                field="changes",
            )
        seen[key] = change.sequence_index
    indices = sorted(change.sequence_index for change in changes)
    if indices != list(range(len(changes))):
        raise RemediationValidationError(
            f"Step numbers must run 0 to {len(changes) - 1}, once each; these are {indices}. "
            "A gap or a repeat means the runbook a person follows and the list an auditor "
            "reads are not the same list.",
            field="sequence_index",
        )


def change_digest(change: PlannedChange) -> str:
    """One change's contribution to the plan digest, also useful on its own in a log line."""
    return _digest(change.as_digestible())


# --------------------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------------------


def validate_title(value: str) -> str:
    title = value.strip()
    if not title:
        raise RemediationValidationError(
            "A change plan needs a title: it is what appears in a change ticket and in the "
            "approval request, and an untitled plan is one nobody can refer to.",
            field="title",
        )
    if len(title) > MAX_TITLE_LENGTH:
        raise RemediationValidationError(
            f"A title is at most {MAX_TITLE_LENGTH} characters; this one is {len(title)}.",
            field="title",
        )
    return title


def validate_rationale(value: str) -> str:
    """Every plan says why, without exception and with no setting that relaxes it.

    The same floor ``ck_review_decisions_reason_required`` puts under a revocation, for the
    same reason and one step further along: somebody is about to lose access, and the person
    losing it is entitled to an answer better than "it was on the list".
    """
    rationale = value.strip()
    if not rationale:
        raise RemediationValidationError(
            "A change plan must say why. Somebody is about to lose access; 'it was on the "
            "list' is not an answer they can be given, and an approver has nothing to weigh "
            "without one.",
            field="rationale",
        )
    if len(rationale) > MAX_RATIONALE_LENGTH:
        raise RemediationValidationError(
            f"A rationale is at most {MAX_RATIONALE_LENGTH} characters; this one is "
            f"{len(rationale)}. Attach the detail to the change ticket and summarize here.",
            field="rationale",
        )
    return rationale


def validate_transition(current: ChangePlanStatus, requested: ChangePlanStatus) -> None:
    """Refuse a lifecycle move the plan does not allow, naming both states.

    Both are named because the two mistakes this catches — approving a plan twice, and
    exporting one somebody rejected — read identically from a caller that only sees "invalid
    state", and send an operator to two different people.
    """
    allowed = PLAN_TRANSITIONS[current]
    if requested in allowed:
        return
    permitted = ", ".join(sorted(status.value for status in allowed)) or "nothing"
    raise RemediationValidationError(
        f"A plan that is {current.value} cannot become {requested.value}. "
        f"From {current.value} it may become: {permitted}.",
        field="status",
    )


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """One approver's answer, and exactly what they were answering about.

    ``plan_digest`` and ``basis_token`` are the two halves of "what did you approve". The
    first pins the plan's content; the second pins the collected state it was measured
    against. An approval that recorded neither would still be attached to the plan after
    somebody edited it and after a scan moved the estate — which is how a signed change gets
    executed against facts nobody approved.
    """

    approval_id: UUID
    plan_id: UUID
    decision: ApprovalDecision
    approver_subject: str
    approver_display_name: str | None
    approver_roles: tuple[str, ...]
    decided_at: dt.datetime
    rationale: str | None
    plan_digest: str
    basis_token: str
    simulation_id: UUID | None = None
    """The blast-radius report in front of the approver when they answered. Recorded so that
    "this was approved without anybody looking at the impact" is a question the data can
    answer."""

    @property
    def approved(self) -> bool:
        return self.decision is ApprovalDecision.APPROVE

    def document(self) -> dict[str, Any]:
        return {
            "approval_id": str(self.approval_id),
            "decision": self.decision.value,
            "approver_subject": self.approver_subject,
            "approver_display_name": self.approver_display_name,
            "approver_roles": list(self.approver_roles),
            "decided_at": _instant(self.decided_at),
            "rationale": self.rationale,
            "plan_digest": self.plan_digest,
            "basis_token": self.basis_token,
            "simulation_id": None if self.simulation_id is None else str(self.simulation_id),
        }


@dataclass(frozen=True, slots=True)
class ChangePlan:
    """A reviewable, approvable, exportable description of work somebody might do in Windows.

    ADG does none of it. See the package docstring and ADR-0035.
    """

    plan_id: UUID
    title: str
    rationale: str
    status: ChangePlanStatus
    changes: tuple[PlannedChange, ...]
    requested_by_subject: str
    requested_by_display_name: str | None
    requested_at: dt.datetime
    basis_token: str
    """The collection basis the plan was written against — the identity of everything ADG had
    collected at that moment (:mod:`app.domain.basis`). It moves if and only if a collector
    has written something, which is what lets a plan be told it is stale as a fact rather than
    as a time-based guess."""

    basis_run_id: str | None = None
    basis_captured_at: dt.datetime | None = None
    campaign_id: UUID | None = None
    simulation_id: UUID | None = None
    """The stored what-if whose report is this plan's blast radius. ``None`` until the plan
    has been simulated, and a plan may not be submitted without one."""

    simulation_basis_token: str | None = None
    """The basis the simulation was computed against. Compared with :attr:`basis_token` before
    submission: a report measured against a different world than the plan describes is not
    this plan's blast radius, and the two drifting apart is invisible unless both are
    recorded."""

    impact_summary: Mapping[str, Any] = field(default_factory=dict)
    submitted_at: dt.datetime | None = None
    decided_at: dt.datetime | None = None
    approved_by_subject: str | None = None
    approved_plan_digest: str | None = None
    approved_basis_token: str | None = None
    rejection_reason: str | None = None
    exported_at: dt.datetime | None = None
    invalidated_at: dt.datetime | None = None
    invalidation_reason: str | None = None
    canceled_at: dt.datetime | None = None
    canceled_by_subject: str | None = None
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None

    @property
    def digest(self) -> str:
        return plan_digest(self)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_editable(self) -> bool:
        return self.status in EDITABLE_STATUSES

    @property
    def has_current_simulation(self) -> bool:
        """Whether a blast-radius report exists and was measured against this plan's world."""
        return (
            self.simulation_id is not None
            and self.simulation_basis_token is not None
            and self.simulation_basis_token == self.basis_token
        )

    @property
    def approval_is_current(self) -> bool:
        """Whether the recorded approval still describes this plan.

        False the moment the plan's content changes — which cannot happen after submission —
        and false if the approval was recorded against a different digest, which is the
        belt-and-braces check that makes the rule survive a future author adding an editable
        field.
        """
        return (
            self.status is ChangePlanStatus.APPROVED
            and self.approved_plan_digest is not None
            and self.approved_plan_digest == self.digest
        )

    def is_stale_against(self, current_token: str) -> bool:
        """Whether a collector has written anything since this plan was measured."""
        return current_token != self.basis_token

    def document(self) -> dict[str, Any]:
        """The full record, for an export and for an API response."""
        return {
            "plan_id": str(self.plan_id),
            "title": self.title,
            "rationale": self.rationale,
            "status": self.status.value,
            "campaign_id": None if self.campaign_id is None else str(self.campaign_id),
            "requested_by_subject": self.requested_by_subject,
            "requested_by_display_name": self.requested_by_display_name,
            "requested_at": _instant(self.requested_at),
            "basis_token": self.basis_token,
            "basis_run_id": self.basis_run_id,
            "basis_captured_at": _instant(self.basis_captured_at),
            "simulation_id": None if self.simulation_id is None else str(self.simulation_id),
            "simulation_basis_token": self.simulation_basis_token,
            "impact_summary": dict(self.impact_summary),
            "changes": [change.document() for change in self.changes],
            "plan_digest": self.digest,
        }


def plan_digest(plan: ChangePlan) -> str:
    """The digest an approval pins and a signature covers.

    Covers everything an approver could have read and acted on: the title, the rationale, the
    basis the plan was measured against, the simulation whose impact they were shown, and
    every change in order with its frozen before-state and its intended after-state.

    Deliberately **excludes** the lifecycle fields — status, timestamps, the approval itself.
    Including them would make the digest change when the plan was approved, so the approval
    could never record the digest it approved; and they are not claims about what the change
    *is*.
    """
    material = {
        "document_version": PLAN_DOCUMENT_VERSION,
        "plan_id": str(plan.plan_id),
        "title": plan.title,
        "rationale": plan.rationale,
        "campaign_id": None if plan.campaign_id is None else str(plan.campaign_id),
        "requested_by_subject": plan.requested_by_subject,
        "basis_token": plan.basis_token,
        "simulation_id": None if plan.simulation_id is None else str(plan.simulation_id),
        "changes": [
            change.as_digestible()
            for change in sorted(plan.changes, key=lambda item: item.sequence_index)
        ],
    }
    return _digest(material)


# --------------------------------------------------------------------------------------
# Preconditions
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChangePrecondition:
    """Whether one change still describes the estate, and what to do if it does not."""

    change_id: UUID
    sequence_index: int
    verdict: PreconditionVerdict
    expected_digest: str
    observed_digest: str | None
    summary: str
    """A sentence built on the server. The four verdicts send an operator to four different
    places and a client that rendered them from the enum alone would have to reinvent the
    distinction between *gone* and *nobody looked*, which is the one that matters most."""

    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def blocks_export(self) -> bool:
        return self.verdict.blocks_export


@dataclass(frozen=True, slots=True)
class PreconditionReport:
    """Every change's precondition, and whether the plan as a whole may proceed."""

    plan_id: UUID
    checked_at: dt.datetime
    basis_token: str
    plan_basis_token: str
    preconditions: tuple[ChangePrecondition, ...]

    @property
    def basis_moved(self) -> bool:
        """Whether a collector has written anything since the plan was measured.

        Reported beside the per-change verdicts rather than folded into them, because it is
        not by itself a reason to refuse: a scan that touched a different server moves the
        token and changes nothing this plan names. What refuses is a change whose own
        precondition is not satisfied.
        """
        return self.basis_token != self.plan_basis_token

    @property
    def satisfied(self) -> bool:
        return not any(item.blocks_export for item in self.preconditions)

    @property
    def blocking(self) -> tuple[ChangePrecondition, ...]:
        return tuple(item for item in self.preconditions if item.blocks_export)

    def counts(self) -> dict[str, int]:
        counted = {verdict.value: 0 for verdict in PreconditionVerdict}
        for item in self.preconditions:
            counted[item.verdict.value] += 1
        return counted

    def document(self) -> dict[str, Any]:
        return {
            "plan_id": str(self.plan_id),
            "checked_at": _instant(self.checked_at),
            "basis_token": self.basis_token,
            "plan_basis_token": self.plan_basis_token,
            "basis_moved": self.basis_moved,
            "satisfied": self.satisfied,
            "counts": self.counts(),
            "preconditions": [
                {
                    "change_id": str(item.change_id),
                    "sequence_index": item.sequence_index,
                    "verdict": item.verdict.value,
                    "expected_digest": item.expected_digest,
                    "observed_digest": item.observed_digest,
                    "summary": item.summary,
                    "detail": dict(item.detail),
                }
                for item in sorted(self.preconditions, key=lambda item: item.sequence_index)
            ],
        }


# --------------------------------------------------------------------------------------
# The export
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanExport:
    """One signed change plan, as handed to a human administrator.

    Append-only and never regenerated in place: exporting twice produces two rows, because the
    document a person is holding is the one that was signed at that moment and a second
    signature over edited content is a different document.
    """

    export_id: UUID
    plan_id: UUID
    exported_by_subject: str
    exported_by_display_name: str | None
    exported_at: dt.datetime
    document: Mapping[str, Any]
    document_digest: str
    signature: str
    signature_algorithm: str
    signature_key_id: str
    basis_token: str
    audit_head_digest: str | None
    """The head of the plan's audit chain at the moment of export. This is the value worth
    recording outside the database — in the change ticket, in an email — because it is what a
    later verification is compared against (:class:`app.governance.audit.ChainVerification`).
    """

    def document_envelope(self) -> dict[str, Any]:
        return {
            "export_id": str(self.export_id),
            "plan_id": str(self.plan_id),
            "exported_by_subject": self.exported_by_subject,
            "exported_by_display_name": self.exported_by_display_name,
            "exported_at": _instant(self.exported_at),
            "document_digest": self.document_digest,
            "signature": self.signature,
            "signature_algorithm": self.signature_algorithm,
            "signature_key_id": self.signature_key_id,
            "basis_token": self.basis_token,
            "audit_head_digest": self.audit_head_digest,
        }


# --------------------------------------------------------------------------------------


def _instant(value: dt.datetime | None) -> str | None:
    """UTC, microseconds, always. A digest taken over a timestamp read back under another
    session time zone must not differ from the one taken when it was written."""
    if value is None:
        return None
    return value.astimezone(dt.UTC).isoformat(timespec="microseconds")


def _parse_instant(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime.fromisoformat(value)
