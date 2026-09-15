r"""The severity and direction rules, as an ordered table anybody can read.

One list, evaluated top to bottom, **first match wins**. Each rule carries an id, the kinds
it applies to, a predicate that returns the sentence to show when it fires, and the severity
and direction it assigns. Nothing else decides either value.

Written this way for three reasons, and the third is the one that matters:

1. A rule is greppable. "Why is this critical?" is answered by ``rule_ids`` in the response
   and a search for that string here.
2. The table is testable as a table. ``tests/changes/test_rules.py`` walks every rule and
   asserts it fires on a case built for it and on nothing else in the fixture set, so a rule
   that has been shadowed by one above it fails the suite rather than quietly never firing.
3. **First match wins makes the severity a decision rather than an accumulation.** A scoring
   scheme that adds points would let three harmless attributes out-score one Deny removal,
   and the arithmetic that produced the total is not something anybody can argue with. A
   rule is.

## A first observation is never scored

The first rule in the table catches :attr:`ChangeAction.FIRST_OBSERVED` and assigns
:attr:`ChangeSeverity.INFO` regardless of what the state says. An ``Everyone / Full Control``
ACE that ADG has just seen for the first time may have been there for six years; it is a
**risk**, which is Phase 8's subject, and calling it a critical *change* would make the first
scan of any estate a page of incidents and teach the reader to ignore the column.

## Direction is about the edit, not about anybody's access

``broadened`` means this edit grants something it did not grant before. Whether anyone's
effective access actually rose is a different question with a different answer — an Allow
added below a Deny broadens the ACL and changes nothing — and it is answered by
:mod:`app.changes.impact`, over the real engine, on demand. Keeping them apart is what lets
this module stay pure.

## Removing a Deny is a high-severity broadening

The rule that surprises people. A Deny entry is a control somebody deliberately placed, and
removing one hands back exactly the access it was placed to withhold — silently, with no
Allow being touched, and therefore invisible to any review that only watches for new grants.
ADR-0016 already draws the same distinction on the answer side: ``denied`` and ``no_grant``
are different findings because removing a Deny is editing a decision.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.access_engine.rights import (
    ESCALATION_RIGHTS,
    FILE_ALL_ACCESS,
    SHARE_LEVEL_MASKS,
    NtfsRight,
    RightsMask,
)
from app.changes.model import (
    ChangeAction,
    ChangeDirection,
    ChangeSeverity,
    FieldDelta,
    FieldSignificance,
)
from app.changes.principals import is_broad_trustee, is_privileged_group, trustee_display
from app.contracts.v1.common import ObservationKind
from app.domain.access import AceType, SharePermission

__all__ = ["RULES", "ChangeFacts", "Rule", "RuleOutcome", "evaluate"]

ACE_KINDS: Final = frozenset({ObservationKind.SMB_ACE, ObservationKind.NTFS_ACE})

WRITE_BITS: Final = int(
    NtfsRight.WRITE_DATA
    | NtfsRight.APPEND_DATA
    | NtfsRight.WRITE_EA
    | NtfsRight.WRITE_ATTRIBUTES
    | NtfsRight.DELETE
    | NtfsRight.DELETE_CHILD
    | NtfsRight.WRITE_DAC
    | NtfsRight.WRITE_OWNER
)
"""Rights that let a holder change something. Listed one bit at a time, deliberately.

``FILE_GENERIC_WRITE`` is the obvious constant to reach for and it is the wrong one: it
carries ``READ_CONTROL`` and ``SYNCHRONIZE``, which ``FILE_GENERIC_READ`` carries too, so
intersecting a plain Read & Execute mask with it is non-zero. A rule built on that would
report every read grant in the estate as a write grant -- at ``HIGH`` severity, for a broad
trustee -- and the severity column would stop meaning anything within one scan.
"""


@dataclass(frozen=True, slots=True)
class ChangeFacts:
    """Everything a rule is allowed to look at.

    A closed set, deliberately. A rule that could read the database would make a
    classification depend on what else had been collected by the time somebody asked, and
    the same two versions would then classify differently on two afternoons.

    The two fields that are not simply "the two states" are both answers a single object
    cannot give about itself, computed once per container by
    :mod:`app.changes.correlation` and handed in:

    * ``ordering_material`` — whether an ACE's position change altered the normalized ACL.
      ``None`` means the question was not answerable, which is not the same as "no".
    * ``sibling_ace_changes`` — how many ACEs of this resource also changed in the same
      window. ``None`` means not counted. Zero is what makes ``resource.acl_hash.unexplained``
      a finding rather than a duplicate of the ACE changes beside it.
    """

    kind: ObservationKind
    key: str
    action: ChangeAction
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None
    deltas: tuple[FieldDelta, ...]
    container_key: str | None = None
    related_key: str | None = None
    ordering_material: bool | None = None
    sibling_ace_changes: int | None = None

    # -- reading the two states --------------------------------------------------------

    @property
    def state(self) -> Mapping[str, Any]:
        """The state to describe the object by: the newer one when there is one."""
        return self.after or self.before or {}

    def moved(self, name: str) -> tuple[Any, Any] | None:
        """``(before, after)`` when ``name`` changed, else ``None``."""
        for delta in self.deltas:
            if delta.field == name:
                return delta.before, delta.after
        return None

    def became(self, name: str, value: Any) -> bool:
        """Whether ``name`` changed *to* ``value``."""
        movement = self.moved(name)
        return movement is not None and movement[1] == value

    def has_delta(self, significance: FieldSignificance) -> bool:
        return any(delta.significance is significance for delta in self.deltas)

    # -- reading an ACE ----------------------------------------------------------------

    @property
    def ace_type(self) -> AceType | None:
        raw = self.state.get("ace_type")
        try:
            return AceType(raw) if raw is not None else None
        except ValueError:  # pragma: no cover - the column is constrained to the enum
            return None

    @property
    def is_allow(self) -> bool:
        return self.ace_type is AceType.ALLOW

    @property
    def is_deny(self) -> bool:
        return self.ace_type is AceType.DENY

    @property
    def granted_mask(self) -> RightsMask | None:
        """The ACE's rights, whichever of the two forms the source reported.

        A share ACE carries either a mask or one of the three permission levels; both are
        share-layer masks and :data:`app.access_engine.rights.SHARE_LEVEL_MASKS` is the one
        mapping between them. An NTFS ACE always carries a mask, and its generic bits are
        expanded here because ``GENERIC_ALL`` and ``FILE_ALL_ACCESS`` are the same grant
        written two ways — and a rule that tested the raw bits would score one and miss
        the other.
        """
        if self.kind is ObservationKind.NTFS_ACE:
            raw = self.state.get("access_mask")
            return RightsMask.ntfs(int(raw)).expand_generics() if raw is not None else None
        if self.kind is ObservationKind.SMB_ACE:
            raw = self.state.get("access_mask")
            if raw is not None:
                return RightsMask.smb(int(raw)).expand_generics()
            level = self.state.get("permission")
            if level is None:
                return None
            try:
                return RightsMask.smb(SHARE_LEVEL_MASKS[SharePermission(level)])
            except (KeyError, ValueError):  # pragma: no cover - constrained by the contract
                return None
        return None

    @property
    def grants_full_control(self) -> bool:
        mask = self.granted_mask
        return mask is not None and mask.value & FILE_ALL_ACCESS == FILE_ALL_ACCESS

    @property
    def grants_escalation(self) -> bool:
        """``WRITE_DAC`` or ``WRITE_OWNER``: the holder can grant themselves the rest."""
        mask = self.granted_mask
        return mask is not None and bool(mask.value & int(ESCALATION_RIGHTS))

    @property
    def grants_write(self) -> bool:
        """Whether the mask lets its holder change anything.

        Tested against :data:`WRITE_BITS` and **not** against ``FILE_GENERIC_WRITE``, which
        would be wrong in the direction that matters. ``FILE_GENERIC_WRITE`` is
        ``0x00120116`` and ``FILE_GENERIC_READ`` is ``0x00120089``: they share
        ``READ_CONTROL`` and ``SYNCHRONIZE``, so intersecting a plain Read & Execute mask
        (``0x001200a9``) with generic-write is non-zero and every read grant in the estate
        would be reported as a write grant.
        """
        mask = self.granted_mask
        return mask is not None and bool(mask.value & WRITE_BITS)

    @property
    def trustee(self) -> str | None:
        value = self.state.get("trustee_key") or self.related_key
        return str(value) if value is not None else None

    @property
    def trustee_name(self) -> str:
        return trustee_display(self.trustee)


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """What a rule assigns when it fires."""

    rule_id: str
    severity: ChangeSeverity
    direction: ChangeDirection
    reason: str


@dataclass(frozen=True, slots=True)
class Rule:
    """One row of the table."""

    rule_id: str
    kinds: frozenset[ObservationKind] | None
    severity: ChangeSeverity
    direction: ChangeDirection
    test: Callable[[ChangeFacts], str | None]
    """Returns the sentence to show when the rule fires, or ``None`` when it does not.

    The sentence rather than a boolean, so the reason can name the trustee, the mask or the
    group that made the rule fire. A generic reason attached to a specific rule is how a
    change list becomes unreadable: forty lines all saying "a permission changed"."""

    def applies_to(self, kind: ObservationKind) -> bool:
        return self.kinds is None or kind in self.kinds


def _only(*kinds: ObservationKind) -> frozenset[ObservationKind]:
    return frozenset(kinds)


def _added(facts: ChangeFacts) -> bool:
    return facts.action is ChangeAction.ADDED


def _removed(facts: ChangeFacts) -> bool:
    return facts.action is ChangeAction.REMOVED


# ---------------------------------------------------------------------------- the table

RULES: Final[tuple[Rule, ...]] = (
    # -- before anything else ----------------------------------------------------------
    Rule(
        "first_observed",
        None,
        ChangeSeverity.INFO,
        ChangeDirection.NEUTRAL,
        lambda f: (
            "ADG had no prior view of what contains this object, so this is the beginning "
            "of the record rather than evidence that anything was created. Whatever it "
            "grants may have been in place for years."
            if f.action is ChangeAction.FIRST_OBSERVED
            else None
        ),
    ),
    Rule(
        "identity.drift",
        None,
        ChangeSeverity.HIGH,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            "A field that forms this object's key changed while the key did not: "
            + ", ".join(
                delta.field
                for delta in f.deltas
                if delta.significance is FieldSignificance.IDENTITY
            )
            + ". Two states that differ in an identifying field are two objects, so this "
            "is a defect in ingestion rather than a change in the estate."
            if any(delta.significance is FieldSignificance.IDENTITY for delta in f.deltas)
            else None
        ),
    ),
    # -- ACL entries -------------------------------------------------------------------
    #
    # Full Control is tested before escalation, and the order is not cosmetic: Full Control
    # *contains* WRITE_DAC and WRITE_OWNER, so the escalation rule matches every Full
    # Control grant too. Both assign CRITICAL, so the severity is the same either way and
    # the sentence is not -- "Everyone was granted Full Control" is what happened, and
    # "Everyone was granted WRITE_DAC" is a true statement that buries it.
    Rule(
        "ace.allow.broad.full",
        ACE_KINDS,
        ChangeSeverity.CRITICAL,
        ChangeDirection.BROADENED,
        lambda f: (
            f"{f.trustee_name} was granted Full Control, and its membership is not a list "
            "anybody maintains."
            if _added(f) and f.is_allow and is_broad_trustee(f.trustee) and f.grants_full_control
            else None
        ),
    ),
    Rule(
        "ace.allow.broad.escalation",
        ACE_KINDS,
        ChangeSeverity.CRITICAL,
        ChangeDirection.BROADENED,
        lambda f: (
            f"{f.trustee_name} was granted rights that let its holder rewrite the "
            "permissions themselves (WRITE_DAC or WRITE_OWNER), and its membership is not "
            "a list anybody maintains."
            if _added(f) and f.is_allow and is_broad_trustee(f.trustee) and f.grants_escalation
            else None
        ),
    ),
    Rule(
        "ace.allow.broad.write",
        ACE_KINDS,
        ChangeSeverity.HIGH,
        ChangeDirection.BROADENED,
        lambda f: (
            f"{f.trustee_name} was granted write access, and its membership is not a list "
            "anybody maintains."
            if _added(f) and f.is_allow and is_broad_trustee(f.trustee) and f.grants_write
            else None
        ),
    ),
    Rule(
        "ace.allow.broad",
        ACE_KINDS,
        ChangeSeverity.HIGH,
        ChangeDirection.BROADENED,
        lambda f: (
            f"{f.trustee_name} was granted access, and its membership is not a list anybody "
            "maintains."
            if _added(f) and f.is_allow and is_broad_trustee(f.trustee)
            else None
        ),
    ),
    Rule(
        "ace.deny.removed",
        ACE_KINDS,
        ChangeSeverity.HIGH,
        ChangeDirection.BROADENED,
        lambda f: (
            f"A Deny entry for {f.trustee_name} is gone. A Deny is a control somebody "
            "placed deliberately; removing one hands back exactly the access it withheld, "
            "without any Allow being touched."
            if _removed(f) and f.is_deny
            else None
        ),
    ),
    # Full Control before escalation again, and for the same reason. See above.
    Rule(
        "ace.allow.full",
        ACE_KINDS,
        ChangeSeverity.HIGH,
        ChangeDirection.BROADENED,
        lambda f: (
            f"{f.trustee_name} was granted Full Control."
            if _added(f) and f.is_allow and f.grants_full_control
            else None
        ),
    ),
    Rule(
        "ace.allow.escalation",
        ACE_KINDS,
        ChangeSeverity.HIGH,
        ChangeDirection.BROADENED,
        lambda f: (
            f"{f.trustee_name} was granted WRITE_DAC or WRITE_OWNER, which lets its holder "
            "grant itself every other right."
            if _added(f) and f.is_allow and f.grants_escalation
            else None
        ),
    ),
    Rule(
        "ace.order.material",
        ACE_KINDS,
        ChangeSeverity.HIGH,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            "The entry moved within the ACL and the normalized ACL changed with it. "
            "Windows evaluates a DACL in order, so a Deny that has moved below an Allow "
            "now grants what it previously refused."
            if f.ordering_material is True
            else None
        ),
    ),
    Rule(
        "ace.allow.added",
        ACE_KINDS,
        ChangeSeverity.MEDIUM,
        ChangeDirection.BROADENED,
        lambda f: f"{f.trustee_name} was granted access." if _added(f) and f.is_allow else None,
    ),
    Rule(
        "ace.deny.added",
        ACE_KINDS,
        ChangeSeverity.LOW,
        ChangeDirection.NARROWED,
        lambda f: f"Access was denied to {f.trustee_name}." if _added(f) and f.is_deny else None,
    ),
    Rule(
        "ace.allow.removed",
        ACE_KINDS,
        ChangeSeverity.LOW,
        ChangeDirection.NARROWED,
        lambda f: (
            f"An Allow entry for {f.trustee_name} is gone." if _removed(f) and f.is_allow else None
        ),
    ),
    Rule(
        "ace.source.explicit",
        ACE_KINDS,
        ChangeSeverity.MEDIUM,
        ChangeDirection.NEUTRAL,
        lambda f: (
            f"The entry for {f.trustee_name} became explicit rather than inherited. It "
            "grants the same rights today and no longer follows its parent, so the next "
            "edit to the parent will not reach it."
            if f.became("source", "explicit")
            else None
        ),
    ),
    Rule(
        "ace.source.inherited",
        ACE_KINDS,
        ChangeSeverity.LOW,
        ChangeDirection.NEUTRAL,
        lambda f: (
            f"The entry for {f.trustee_name} became inherited rather than explicit. It "
            "will now follow edits to its parent."
            if f.became("source", "inherited")
            else None
        ),
    ),
    Rule(
        "ace.order.immaterial",
        ACE_KINDS,
        ChangeSeverity.INFO,
        ChangeDirection.NEUTRAL,
        lambda f: (
            "The entry was renumbered and the normalized ACL is unchanged, so nothing "
            "about evaluation order moved. Windows reports positions relative to entries "
            "ADG does not store, so a number can move while the order does not."
            if f.ordering_material is False
            else None
        ),
    ),
    Rule(
        "ace.order.undetermined",
        ACE_KINDS,
        ChangeSeverity.MEDIUM,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            "The entry's position in the ACL changed and ADG could not reconstruct the "
            "ACL either side of the change, so whether evaluation order moved is unknown."
            if f.moved("order_index") is not None and f.ordering_material is None
            else None
        ),
    ),
    # -- membership --------------------------------------------------------------------
    Rule(
        "membership.added.privileged",
        _only(ObservationKind.MEMBERSHIP_EDGE),
        ChangeSeverity.CRITICAL,
        ChangeDirection.BROADENED,
        lambda f: (
            f"A member was added to {trustee_display(f.container_key)}, a group whose "
            "membership is itself an administrative control."
            if _added(f) and is_privileged_group(f.container_key)
            else None
        ),
    ),
    Rule(
        "membership.added.broad",
        _only(ObservationKind.MEMBERSHIP_EDGE),
        ChangeSeverity.HIGH,
        ChangeDirection.BROADENED,
        lambda f: (
            f"{trustee_display(f.related_key)} was nested into "
            f"{trustee_display(f.container_key)}, so every grant naming that group now "
            "reaches a population nobody maintains."
            if _added(f) and is_broad_trustee(f.related_key)
            else None
        ),
    ),
    Rule(
        "membership.removed.privileged",
        _only(ObservationKind.MEMBERSHIP_EDGE),
        ChangeSeverity.MEDIUM,
        ChangeDirection.NARROWED,
        lambda f: (
            f"A member was removed from {trustee_display(f.container_key)}, a group whose "
            "membership is itself an administrative control."
            if _removed(f) and is_privileged_group(f.container_key)
            else None
        ),
    ),
    Rule(
        "membership.added",
        _only(ObservationKind.MEMBERSHIP_EDGE),
        ChangeSeverity.MEDIUM,
        ChangeDirection.BROADENED,
        lambda f: (
            f"{trustee_display(f.related_key)} joined "
            f"{trustee_display(f.container_key)} and reaches everything that group reaches."
            if _added(f)
            else None
        ),
    ),
    Rule(
        "membership.removed",
        _only(ObservationKind.MEMBERSHIP_EDGE),
        ChangeSeverity.LOW,
        ChangeDirection.NARROWED,
        lambda f: (
            f"{trustee_display(f.related_key)} left {trustee_display(f.container_key)}."
            if _removed(f)
            else None
        ),
    ),
    # -- principals --------------------------------------------------------------------
    Rule(
        "principal.group_type.security",
        _only(ObservationKind.PRINCIPAL),
        ChangeSeverity.HIGH,
        ChangeDirection.BROADENED,
        lambda f: (
            "This group became a security group. A distribution group appears in no access "
            "token and grants nothing, so every entry that already named it is now live — "
            "without a single ACL having been edited."
            if f.became("group_type", "security")
            else None
        ),
    ),
    Rule(
        "principal.group_type.distribution",
        _only(ObservationKind.PRINCIPAL),
        ChangeSeverity.MEDIUM,
        ChangeDirection.NARROWED,
        lambda f: (
            "This group became a distribution group. It no longer appears in any access "
            "token, so every entry naming it now grants nothing."
            if f.became("group_type", "distribution")
            else None
        ),
    ),
    Rule(
        "principal.enabled",
        _only(ObservationKind.PRINCIPAL),
        ChangeSeverity.MEDIUM,
        ChangeDirection.BROADENED,
        lambda f: (
            "The account was enabled. Every grant naming it, and every grant naming a "
            "group it belongs to, became usable at once."
            if f.became("enabled", True)
            else None
        ),
    ),
    Rule(
        "principal.disabled",
        _only(ObservationKind.PRINCIPAL),
        ChangeSeverity.LOW,
        ChangeDirection.NARROWED,
        lambda f: (
            "The account was disabled. It cannot authenticate, so the grants naming it are "
            "inert — and they are still there."
            if f.became("enabled", False)
            else None
        ),
    ),
    Rule(
        "principal.undeleted",
        _only(ObservationKind.PRINCIPAL),
        ChangeSeverity.MEDIUM,
        ChangeDirection.BROADENED,
        lambda f: (
            "A principal that had been deleted in the directory is present again, carrying "
            "the same SID and therefore every grant that names it."
            if f.became("is_deleted", False)
            else None
        ),
    ),
    Rule(
        "principal.deleted",
        _only(ObservationKind.PRINCIPAL),
        ChangeSeverity.MEDIUM,
        ChangeDirection.NARROWED,
        lambda f: (
            "The principal was deleted in the directory. Entries naming its SID remain on "
            "every ACL that carried them and now name nobody."
            if f.became("is_deleted", True)
            else None
        ),
    ),
    Rule(
        "principal.group_scope",
        _only(ObservationKind.PRINCIPAL),
        ChangeSeverity.LOW,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            f"The group's scope changed from {(f.moved('group_scope') or (None, None))[0]} "
            f"to {(f.moved('group_scope') or (None, None))[1]}, which changes where it can "
            "be used and which directories replicate it."
            if f.moved("group_scope") is not None
            else None
        ),
    ),
    Rule(
        "principal.removed",
        _only(ObservationKind.PRINCIPAL),
        ChangeSeverity.LOW,
        ChangeDirection.NARROWED,
        lambda f: (
            "A scan that reconciled this principal's scope did not find it. Entries naming "
            "its SID are still stored and now name nobody."
            if _removed(f)
            else None
        ),
    ),
    Rule(
        "principal.added",
        _only(ObservationKind.PRINCIPAL),
        ChangeSeverity.LOW,
        ChangeDirection.NEUTRAL,
        lambda f: (
            "A principal appeared. Existing grants nothing by itself; what it can reach is "
            "whatever its memberships give it."
            if _added(f)
            else None
        ),
    ),
    # -- directories -------------------------------------------------------------------
    Rule(
        "resource.null_dacl",
        _only(ObservationKind.NTFS_RESOURCE),
        ChangeSeverity.CRITICAL,
        ChangeDirection.BROADENED,
        lambda f: (
            "The directory's DACL is gone. A NULL DACL is not an empty one: it grants "
            "everyone full access, and no entry has to exist for that to be true."
            if f.became("dacl_present", False)
            else None
        ),
    ),
    Rule(
        "resource.added.null_dacl",
        _only(ObservationKind.NTFS_RESOURCE),
        ChangeSeverity.HIGH,
        ChangeDirection.BROADENED,
        lambda f: (
            "The directory appeared carrying a NULL DACL, which grants everyone full access."
            if _added(f) and f.state.get("dacl_present") is False
            else None
        ),
    ),
    Rule(
        "resource.unprotected",
        _only(ObservationKind.NTFS_RESOURCE),
        ChangeSeverity.HIGH,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            "Inheritance protection was removed, so this directory now takes its parent's "
            "inheritable entries. Which way that moves access depends on what the parent "
            "carries — those entries appear as their own changes."
            if f.became("dacl_protected", False)
            else None
        ),
    ),
    Rule(
        "resource.protected",
        _only(ObservationKind.NTFS_RESOURCE),
        ChangeSeverity.MEDIUM,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            "Inheritance protection was applied, so this directory stops taking its "
            "parent's inheritable entries. Those may have been Allows, Denies or both."
            if f.became("dacl_protected", True)
            else None
        ),
    ),
    Rule(
        "resource.inheritance",
        _only(ObservationKind.NTFS_RESOURCE),
        ChangeSeverity.MEDIUM,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            "Whether this directory inherits from its parent changed."
            if f.moved("inheritance_enabled") is not None
            else None
        ),
    ),
    Rule(
        "resource.owner.broad",
        _only(ObservationKind.NTFS_RESOURCE),
        ChangeSeverity.HIGH,
        ChangeDirection.BROADENED,
        lambda f: (
            f"Ownership moved to {trustee_display((f.moved('owner_sid') or (None, None))[1])}, "
            "whose membership is not a list anybody maintains. An owner holds READ_CONTROL "
            "and WRITE_DAC whatever the DACL says, so every member can rewrite the "
            "permissions."
            if f.moved("owner_sid") is not None
            and is_broad_trustee((f.moved("owner_sid") or (None, None))[1])
            else None
        ),
    ),
    Rule(
        "resource.owner",
        _only(ObservationKind.NTFS_RESOURCE),
        ChangeSeverity.MEDIUM,
        ChangeDirection.BROADENED,
        lambda f: (
            f"Ownership moved to {trustee_display((f.moved('owner_sid') or (None, None))[1])}. "
            "An owner holds READ_CONTROL and WRITE_DAC implicitly, whatever the DACL says."
            if f.moved("owner_sid") is not None
            else None
        ),
    ),
    Rule(
        "resource.acl_hash.unexplained",
        _only(ObservationKind.NTFS_RESOURCE),
        ChangeSeverity.MEDIUM,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            "The collector's digest of this directory's DACL changed and none of its "
            "entries did. The collector read an ACL that differs from the one ADG stores, "
            "which means entries were lost in transit or never sent."
            if f.moved("acl_hash") is not None and f.sibling_ace_changes == 0
            else None
        ),
    ),
    Rule(
        "resource.acl_hash",
        _only(ObservationKind.NTFS_RESOURCE),
        ChangeSeverity.LOW,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            "The collector's digest of this directory's DACL changed. The entries that "
            "moved are listed as their own changes."
            if f.moved("acl_hash") is not None
            else None
        ),
    ),
    Rule(
        "resource.removed",
        _only(ObservationKind.NTFS_RESOURCE),
        ChangeSeverity.LOW,
        ChangeDirection.NARROWED,
        lambda f: (
            "A scan that reconciled this path's scope did not find the directory."
            if _removed(f)
            else None
        ),
    ),
    Rule(
        "resource.added",
        _only(ObservationKind.NTFS_RESOURCE),
        ChangeSeverity.INFO,
        ChangeDirection.NEUTRAL,
        lambda f: (
            "A directory appeared beneath a share ADG was already reading." if _added(f) else None
        ),
    ),
    # -- shares ------------------------------------------------------------------------
    Rule(
        "share.local_path",
        _only(ObservationKind.SMB_SHARE),
        ChangeSeverity.HIGH,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            f"The share now publishes {(f.moved('local_path') or (None, None))[1]!r} "
            f"instead of {(f.moved('local_path') or (None, None))[0]!r}. Its share ACL did "
            "not move; the entire file-system half of every answer about it did."
            if f.moved("local_path") is not None
            else None
        ),
    ),
    Rule(
        "share.type",
        _only(ObservationKind.SMB_SHARE),
        ChangeSeverity.MEDIUM,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            "The share's type changed, so what it exposes over the network changed with it."
            if f.moved("share_type") is not None
            else None
        ),
    ),
    Rule(
        "share.added",
        _only(ObservationKind.SMB_SHARE),
        ChangeSeverity.MEDIUM,
        ChangeDirection.BROADENED,
        lambda f: (
            "A share appeared, exposing a directory over the network that was not exposed before."
            if _added(f)
            else None
        ),
    ),
    Rule(
        "share.removed",
        _only(ObservationKind.SMB_SHARE),
        ChangeSeverity.LOW,
        ChangeDirection.NARROWED,
        lambda f: (
            "A scan that reconciled this server did not find the share. The directory it "
            "published is still there; it is no longer reachable by this name."
            if _removed(f)
            else None
        ),
    ),
    # -- servers -----------------------------------------------------------------------
    Rule(
        "server.computer_sid",
        _only(ObservationKind.SERVER),
        ChangeSeverity.HIGH,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            "The machine's own SID namespace changed, so every local account and local "
            "group SID recorded against it names a different principal than it did."
            if f.moved("computer_sid") is not None
            else None
        ),
    ),
    Rule(
        "server.domain",
        _only(ObservationKind.SERVER),
        ChangeSeverity.MEDIUM,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            "The server's domain membership changed, so which authority can authenticate "
            "against it changed with it."
            if f.moved("is_domain_member") is not None or f.moved("domain_sid") is not None
            else None
        ),
    ),
    Rule(
        "server.removed",
        _only(ObservationKind.SERVER),
        ChangeSeverity.LOW,
        ChangeDirection.NEUTRAL,
        lambda f: "A reconciled scan did not find the server." if _removed(f) else None,
    ),
    Rule(
        "server.added",
        _only(ObservationKind.SERVER),
        ChangeSeverity.INFO,
        ChangeDirection.NEUTRAL,
        lambda f: "A server appeared." if _added(f) else None,
    ),
    # -- fallbacks ---------------------------------------------------------------------
    # Reached when nothing above matched. Ordered so that "ADG does not understand this"
    # outranks "ADG understands it and it is small", which is the whole point of having an
    # undetermined value at all.
    Rule(
        "unclassified.field",
        None,
        ChangeSeverity.MEDIUM,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            "A field changed that ADG has no classification for: "
            + ", ".join(
                delta.field
                for delta in f.deltas
                if delta.significance is FieldSignificance.UNCLASSIFIED
            )
            if f.has_delta(FieldSignificance.UNCLASSIFIED)
            else None
        ),
    ),
    Rule(
        "security.other",
        None,
        ChangeSeverity.MEDIUM,
        ChangeDirection.UNDETERMINED,
        lambda f: (
            "A field that bears on access changed: "
            + ", ".join(
                delta.field
                for delta in f.deltas
                if delta.significance is FieldSignificance.SECURITY
            )
            if f.has_delta(FieldSignificance.SECURITY)
            else None
        ),
    ),
    Rule(
        "derived.other",
        None,
        ChangeSeverity.INFO,
        ChangeDirection.NEUTRAL,
        lambda f: (
            "A value ADG derives from collected facts changed because the facts it is "
            "derived from changed. Those are listed as their own changes."
            if f.has_delta(FieldSignificance.DERIVED)
            else None
        ),
    ),
    Rule(
        "metadata.other",
        None,
        ChangeSeverity.INFO,
        ChangeDirection.NEUTRAL,
        lambda f: (
            "A descriptive attribute changed: "
            + ", ".join(
                delta.field
                for delta in f.deltas
                if delta.significance is FieldSignificance.METADATA
            )
            if f.has_delta(FieldSignificance.METADATA)
            else None
        ),
    ),
    Rule(
        "provenance.only",
        None,
        ChangeSeverity.INFO,
        ChangeDirection.NEUTRAL,
        lambda f: (
            "Only the identity of the observation that reported this object changed. The "
            "object did not."
            if f.deltas and all(d.significance is FieldSignificance.NOISE for d in f.deltas)
            else None
        ),
    ),
    Rule(
        "unclassified",
        None,
        ChangeSeverity.MEDIUM,
        ChangeDirection.UNDETERMINED,
        lambda _: (
            "ADG recorded a transition it has no rule for. This is reported rather than "
            "dropped: a change nobody can classify is exactly the one worth reading."
        ),
    ),
)


def evaluate(facts: ChangeFacts, rules: Sequence[Rule] = RULES) -> RuleOutcome:
    """The first rule that fires, as an outcome.

    The table ends with a rule that always fires, so this always returns. That last rule is
    not a formality: reaching it means ADG saw a transition it has no opinion about, and the
    honest report of that is a medium-severity, undetermined line an operator reads — not a
    silent ``info``, and not an exception that loses the change entirely.
    """
    for rule in rules:
        if not rule.applies_to(facts.kind):
            continue
        reason = rule.test(facts)
        if reason is not None:
            return RuleOutcome(
                rule_id=rule.rule_id,
                severity=rule.severity,
                direction=rule.direction,
                reason=reason,
            )
    raise AssertionError(  # pragma: no cover - the table's last rule is unconditional
        "The rule table must end with a rule that always fires."
    )
