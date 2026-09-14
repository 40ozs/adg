"""The Windows access check, over one DACL, for one token.

This is the arithmetic Windows performs in ``AccessCheck`` when asked for
``MAXIMUM_ALLOWED`` — *what may this token do to this object* — which is the question an
audit tool asks, as opposed to the one an application asks (*may I open for write*). The
two are the same walk; only the stopping rule differs, and computing the maximum is what
lets one evaluation answer every later question about the result.

The walk, in the order Windows does it:

1. **No DACL** (``SE_DACL_PRESENT`` clear) grants everything to everyone. This is not the
   same as an empty DACL, which grants nothing, and conflating them inverts the answer.
2. **The owner's implicit rights.** If the token holds the object's owner SID, Windows
   grants ``READ_CONTROL`` and ``WRITE_DAC`` *before* it reads a single ACE — so an
   explicit Deny cannot take them away, and an owner can always rewrite the DACL. Unless
   an ``OWNER RIGHTS`` (``S-1-3-4``) entry is present, which replaces them.
3. **Every ACE in the order stored.** ``INHERIT_ONLY`` entries are skipped; they describe
   what children get, not this object. An entry whose trustee is not in the token is
   skipped. Otherwise a Deny adds to ``denied`` whatever is not already granted, and an
   Allow adds to ``granted`` whatever is not already denied.

Step 3 is order-sensitive, and deliberately so. :func:`app.access_engine.resolve_canonical`
implements the *canonical* model — accumulate all Allow, accumulate all Deny, subtract —
which is exact for a DACL in the order Windows maintains and wrong for one that has been
reordered. Windows honors what is stored: an Allow placed ahead of a Deny wins. Rather
than pick one model, this evaluator runs the faithful one and reports
:attr:`AclEvaluation.order_dependent` when the two disagree for this subject, which is a
finding about a real object whose ACL editor and access check no longer say the same thing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from app.access_engine.conditions import AccessCondition, AccessFinding
from app.access_engine.rights import (
    FILE_ALL_ACCESS,
    NormalizedRights,
    RightsLayer,
    RightsMask,
    normalize_mask,
    normalize_share_permission,
)
from app.access_engine.subjects import (
    CREATOR_GROUP_SID,
    CREATOR_OWNER_SID,
    LOGON_SESSION_SIDS,
    OWNER_RIGHTS_SID,
    SubjectToken,
    TokenSid,
)
from app.domain.access import AceFlag, AceSource, AceType, NtfsRight, SharePermission
from app.domain.errors import DomainValidationError

__all__ = [
    "MAX_ACL_ENTRIES",
    "OWNER_IMPLICIT_RIGHTS",
    "AclEntry",
    "AclEvaluation",
    "AppliedAce",
    "DaclFacts",
    "OrderViolation",
    "OrderViolationKind",
    "canonical_order_violations",
    "evaluate_acl",
    "ntfs_entry",
    "share_entry",
]

MAX_ACL_ENTRIES: Final = 4096
"""Entries evaluated before the evaluator stops and reports truncation.

A real DACL holds tens of entries; the Windows limit is the 64 KB ACL size, which is a few
thousand. The ceiling exists so a malformed or hostile row set cannot turn one request into
unbounded work, and exceeding it is reported rather than silently accepted.
"""

OWNER_IMPLICIT_RIGHTS: Final = RightsMask.ntfs(NtfsRight.READ_CONTROL | NtfsRight.WRITE_DAC)
"""What Windows grants the owner of an object regardless of its DACL.

``WRITE_DAC`` is the consequential half: an owner who appears nowhere on the ACL, or who is
explicitly denied on it, can still rewrite the ACL and grant themselves anything. It is
invisible in every ACL viewer, which is exactly why it is modeled here.
"""

_SUBSTITUTION_PLACEHOLDERS: Final[frozenset[str]] = frozenset(
    {CREATOR_OWNER_SID, CREATOR_GROUP_SID}
)


# --------------------------------------------------------------------------------------
# The entry the evaluator works on
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AclEntry:
    """One ACE, in the form the access check needs, independent of where it was stored.

    Deliberately not :class:`app.domain.NtfsAce` or :class:`app.domain.SmbShareAce`: those
    are observation records, one per layer, and the evaluator runs the identical walk over
    both. What it needs beyond the observation is the **trustee key** — the storage key the
    SID resolves to in this object's context, which is what makes ``BUILTIN\\Administrators``
    on FS01 a different trustee from the same SID on FS02 — and a layer-tagged mask.

    ``mask`` is the raw mask as observed, generic bits and all. Expansion happens during
    evaluation and is recorded; storing an expanded mask here would lose what the
    descriptor said.
    """

    trustee_key: str
    trustee_sid: str
    ace_type: AceType
    mask: RightsMask
    flags: AceFlag = AceFlag.NONE
    source: AceSource = AceSource.EXPLICIT
    order_index: int | None = None
    ace_key: str | None = None
    inherited_from: str | None = None
    trustee_name: str | None = None

    def __post_init__(self) -> None:
        if not self.trustee_key:
            raise DomainValidationError("An ACL entry needs a trustee key.", field="trustee_key")
        if self.mask.layer is RightsLayer.EFFECTIVE:
            raise DomainValidationError(
                "An ACL entry carries a mask from an ACL, never a computed effective mask.",
                field="mask",
            )

    @property
    def layer(self) -> RightsLayer:
        return self.mask.layer

    @property
    def is_allow(self) -> bool:
        return self.ace_type is AceType.ALLOW

    @property
    def is_inherited(self) -> bool:
        return self.source is AceSource.INHERITED

    @property
    def applies_to_this_object(self) -> bool:
        """``INHERIT_ONLY`` entries exist to be passed down and grant nothing where they sit."""
        return not (self.flags & AceFlag.INHERIT_ONLY)

    @property
    def normalized(self) -> NormalizedRights:
        """The mask with generic bits resolved, and the record of what was resolved."""
        return normalize_mask(self.mask)

    @property
    def names_substitution_placeholder(self) -> bool:
        """``CREATOR OWNER`` / ``CREATOR GROUP``: never in a token, so never a match."""
        return self.trustee_sid in _SUBSTITUTION_PLACEHOLDERS

    @property
    def names_owner_rights(self) -> bool:
        """``OWNER RIGHTS``: applies to whoever owns the object at access time."""
        return self.trustee_sid == OWNER_RIGHTS_SID


def ntfs_entry(
    *,
    trustee_key: str,
    trustee_sid: str,
    ace_type: AceType,
    access_mask: int,
    flags: AceFlag | int = AceFlag.NONE,
    source: AceSource = AceSource.EXPLICIT,
    order_index: int | None = None,
    ace_key: str | None = None,
    inherited_from: str | None = None,
    trustee_name: str | None = None,
) -> AclEntry:
    """An entry from a file-system DACL."""
    return AclEntry(
        trustee_key=trustee_key,
        trustee_sid=trustee_sid,
        ace_type=ace_type,
        mask=RightsMask.ntfs(access_mask),
        flags=AceFlag(int(flags)),
        source=source,
        order_index=order_index,
        ace_key=ace_key,
        inherited_from=inherited_from,
        trustee_name=trustee_name,
    )


def share_entry(
    *,
    trustee_key: str,
    trustee_sid: str,
    ace_type: AceType,
    access_mask: int | None = None,
    permission: SharePermission | None = None,
    order_index: int | None = None,
    ace_key: str | None = None,
    trustee_name: str | None = None,
) -> AclEntry:
    """An entry from a share ACL, reported either as a mask or as a permission level.

    Exactly one form must be given, matching :class:`app.domain.SmbShareAce`: a source
    reports one or the other, and inventing the missing one would claim precision the
    collector did not have. A permission level is resolved to its mask here, which is the
    one place the two forms meet.
    """
    if (access_mask is None) == (permission is None):
        raise DomainValidationError(
            "A share ACL entry carries exactly one of access_mask or permission, whichever "
            "form the collecting API reported.",
            field="access_mask",
        )
    mask = (
        normalize_share_permission(permission)
        if permission is not None
        else RightsMask.smb(access_mask or 0)
    )
    return AclEntry(
        trustee_key=trustee_key,
        trustee_sid=trustee_sid,
        ace_type=ace_type,
        mask=mask,
        # A share ACL has no inheritance: there is nothing below a share to inherit to.
        flags=AceFlag.NONE,
        source=AceSource.EXPLICIT,
        order_index=order_index,
        ace_key=ace_key,
        trustee_name=trustee_name,
    )


# --------------------------------------------------------------------------------------
# Canonical ordering
# --------------------------------------------------------------------------------------


class OrderViolationKind(StrEnum):
    """A way in which a DACL departs from the order Windows maintains."""

    INHERITED_BEFORE_EXPLICIT = "inherited_before_explicit"
    """An explicit entry sits behind an inherited one. Explicit entries come first in a
    canonical DACL, so a grant set on the object itself is being evaluated after grants it
    was meant to override."""

    ALLOW_BEFORE_DENY = "allow_before_deny"
    """An explicit Deny sits behind an explicit Allow. In a canonical DACL every Deny
    precedes every Allow at the same level, so the Allow now wins for any trustee both
    entries name."""


@dataclass(frozen=True, slots=True)
class OrderViolation:
    """One pair of entries whose relative order is not canonical.

    Both entries are carried, not just the later one: "this Deny is out of order" is not
    actionable without the Allow it now sits behind.
    """

    kind: OrderViolationKind
    position: int
    entry: AclEntry
    earlier_position: int
    earlier_entry: AclEntry


def canonical_order_violations(entries: Sequence[AclEntry]) -> tuple[OrderViolation, ...]:
    """Where a DACL departs from canonical order, judged on what ADG can actually see.

    Canonical order is: explicit Deny, explicit Allow, then the inherited entries with the
    nearest ancestor's first and Deny before Allow within each ancestor's block.

    Only the two checks that are decidable from stored facts are made. **An inherited Deny
    behind an inherited Allow is not reported**, because it is perfectly canonical when the
    two came from different ancestors — and ADG does not populate ``inherited_from``, so
    which ancestor an entry came from is unknown. Reporting it would turn an ordinary
    two-level inheritance into a false finding on a very large number of directories.
    """
    violations: list[OrderViolation] = []
    first_inherited: tuple[int, AclEntry] | None = None
    first_explicit_allow: tuple[int, AclEntry] | None = None

    for position, entry in enumerate(entries):
        if entry.is_inherited:
            if first_inherited is None:
                first_inherited = (position, entry)
            continue
        if first_inherited is not None:
            violations.append(
                OrderViolation(
                    kind=OrderViolationKind.INHERITED_BEFORE_EXPLICIT,
                    position=position,
                    entry=entry,
                    earlier_position=first_inherited[0],
                    earlier_entry=first_inherited[1],
                )
            )
        if entry.is_allow:
            if first_explicit_allow is None:
                first_explicit_allow = (position, entry)
        elif first_explicit_allow is not None:
            violations.append(
                OrderViolation(
                    kind=OrderViolationKind.ALLOW_BEFORE_DENY,
                    position=position,
                    entry=entry,
                    earlier_position=first_explicit_allow[0],
                    earlier_entry=first_explicit_allow[1],
                )
            )
    return tuple(violations)


# --------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DaclFacts:
    """The descriptor-level facts an access check needs beyond the entries themselves.

    ``dacl_present=False`` is a NULL DACL and is the single most consequential fact in this
    type: it grants everyone everything and cannot be expressed as a list of entries.
    """

    dacl_present: bool = True
    dacl_protected: bool = False
    owner_sid: str | None = None
    owner_key: str | None = None
    """The owner's **storage key**, which is what the token is matched on.

    Distinct from ``owner_sid`` for the same reason an ACE's trustee key is distinct from
    its trustee SID: a BUILTIN owner means one machine's local group, so an object owned by
    ``S-1-5-32-544`` on FS01 is owned by ``fs01|S-1-5-32-544``, and matching the bare SID
    would hand every server's local administrators ownership of every other's files.
    Defaults to ``owner_sid``, which is correct for every SID that is not BUILTIN.
    """

    declared_ace_count: int | None = None
    """What the descriptor said it held, when known, so a shortfall against the entries
    actually supplied can be reported instead of evaluated over silently."""

    def __post_init__(self) -> None:
        if self.owner_key is None and self.owner_sid is not None:
            object.__setattr__(self, "owner_key", self.owner_sid)


UNOWNED_PRESENT_DACL: Final = DaclFacts()
"""The descriptor facts of an ordinary present, unprotected, unowned DACL.

A module-level singleton rather than a default argument, so that one shared immutable
value is used everywhere a caller has no descriptor facts to supply -- which is every
share ACL, since a share carries no owner and no protection bit.
"""


@dataclass(frozen=True, slots=True)
class AppliedAce:
    """One entry that matched the token, and what it contributed at its position.

    ``contributed`` is not the entry's mask. It is the part of the mask that survived
    everything decided before it: for an Allow, whatever was not already denied; for a
    Deny, whatever was not already granted. An entry whose contribution is empty is
    reported in :attr:`AclEvaluation.superseded` instead, because "this Allow is on the
    ACL and does nothing" is the explanation an administrator needs, and dropping it makes
    the rights look unexplained.
    """

    entry: AclEntry
    matched: TokenSid
    position: int
    considered: RightsMask
    """The entry's mask with generic bits expanded: what it would have contributed alone."""

    contributed: RightsMask

    @property
    def is_allow(self) -> bool:
        return self.entry.is_allow

    @property
    def via_group(self) -> bool:
        """Whether the ACE reached the subject through a group rather than naming it."""
        return self.matched.depth > 0 or self.matched.is_assumed


@dataclass(frozen=True, slots=True)
class AclEvaluation:
    """What one DACL grants one token, and the evidence for it."""

    layer: RightsLayer
    rights: RightsMask
    """The rights the token holds on this object at this layer. Always ``layer``-tagged."""

    granted_by: tuple[AppliedAce, ...] = ()
    denied_by: tuple[AppliedAce, ...] = ()
    superseded: tuple[AppliedAce, ...] = ()
    """Entries that matched the token and contributed nothing, because an earlier entry had
    already settled every right they name."""

    owner_rights: RightsMask | None = None
    """Rights granted because the subject owns the object, outside the DACL entirely."""

    canonical_rights: RightsMask | None = None
    """What the canonical-ACL model would have computed. ``None`` when no DACL was walked."""

    order_violations: tuple[OrderViolation, ...] = ()
    entries_evaluated: int = 0
    entries_supplied: int = 0
    findings: tuple[AccessFinding, ...] = ()

    @property
    def order_dependent(self) -> bool:
        """Whether the stored order changed this subject's answer.

        The precise statement of Phase 4A's open limitation: not "this ACL is unusual" but
        "this principal's rights depend on where the entries sit", which is a fact about
        one evaluation and is the one worth escalating.
        """
        return self.canonical_rights is not None and self.canonical_rights != self.rights

    @property
    def is_empty(self) -> bool:
        return self.rights.is_empty

    @property
    def escalation_rights(self) -> NtfsRight:
        return self.rights.escalation_rights

    def conditions(self) -> frozenset[AccessCondition]:
        return frozenset(finding.condition for finding in self.findings)


@dataclass
class _Walk:
    """Mutable accumulator for one pass over a DACL. Never leaves this module."""

    layer: RightsLayer
    granted: int = 0
    denied: int = 0
    allow_union: int = 0
    deny_union: int = 0
    granted_by: list[AppliedAce] = field(default_factory=list)
    denied_by: list[AppliedAce] = field(default_factory=list)
    superseded: list[AppliedAce] = field(default_factory=list)
    findings: list[AccessFinding] = field(default_factory=list)
    evaluated: int = 0


def evaluate_acl(
    entries: Iterable[AclEntry],
    token: SubjectToken,
    *,
    layer: RightsLayer,
    facts: DaclFacts = UNOWNED_PRESENT_DACL,
) -> AclEvaluation:
    """Run the access check for ``token`` over one DACL.

    Args:
        entries: the DACL **in evaluation order**. Order is load-bearing: the caller is
            responsible for supplying entries in the order the descriptor stored them, and
            a caller that cannot establish an order must say so rather than sorting by
            something convenient.
        token: the SIDs to match trustees against.
        layer: which ACL this is. Every mask in ``entries`` must carry it, and the result
            carries it too, so a share evaluation can never be mistaken for a file-system
            one.
        facts: descriptor-level facts — NULL DACL, protection, owner, declared entry count.

    Returns:
        The rights, the entries that produced them, and every condition that qualifies the
        answer.
    """
    if layer is RightsLayer.EFFECTIVE:
        raise DomainValidationError(
            "An ACL is evaluated at the layer it belongs to. The EFFECTIVE layer is the "
            "result of crossing two of them, which the resolver does.",
            field="layer",
        )

    supplied = list(entries)
    for entry in supplied:
        if entry.layer is not layer:
            raise DomainValidationError(
                f"Entry for {entry.trustee_sid} carries a {entry.layer.value!r} mask in a "
                f"{layer.value!r} evaluation. Crossing layers silently is the one mistake "
                "the rights model exists to prevent.",
                field="entries",
            )

    if not facts.dacl_present:
        return _null_dacl(layer, facts, len(supplied))

    walk = _Walk(layer=layer)
    truncated = len(supplied) > MAX_ACL_ENTRIES
    considered = supplied[:MAX_ACL_ENTRIES]

    # Resolved once and threaded through the walk: it is both what decides the implicit
    # rights and what an OWNER RIGHTS entry matches against.
    owner_token_sid = None if facts.owner_key is None else token.entry(facts.owner_key)
    owner_rights = _owner_rights(owner_token_sid, facts, considered, walk)

    for position, entry in enumerate(considered):
        _apply(entry, position, token, owner_token_sid, walk)

    rights = RightsMask(walk.granted, layer)
    canonical = RightsMask(walk.allow_union & ~walk.deny_union, layer)
    if owner_rights is not None:
        rights = rights.union(RightsMask(owner_rights.value, layer))
        canonical = canonical.union(RightsMask(owner_rights.value, layer))

    if not considered:
        walk.findings.append(AccessFinding(AccessCondition.EMPTY_DACL, {}))
    if facts.dacl_protected:
        walk.findings.append(AccessFinding(AccessCondition.PROTECTED_DACL, {}))
    if truncated:
        walk.findings.append(
            AccessFinding(
                AccessCondition.ACL_TRUNCATED,
                {"supplied": len(supplied), "evaluated": MAX_ACL_ENTRIES},
            )
        )
    if facts.declared_ace_count is not None and facts.declared_ace_count != len(supplied):
        walk.findings.append(
            AccessFinding(
                AccessCondition.ACE_COUNT_MISMATCH,
                {"declared": facts.declared_ace_count, "held": len(supplied)},
            )
        )

    violations = canonical_order_violations(considered)
    if violations:
        walk.findings.append(
            AccessFinding(
                AccessCondition.NON_CANONICAL_DACL,
                {
                    "violations": [
                        {
                            "kind": violation.kind.value,
                            "position": violation.position,
                            "trustee_sid": violation.entry.trustee_sid,
                            "earlier_position": violation.earlier_position,
                            "earlier_trustee_sid": violation.earlier_entry.trustee_sid,
                        }
                        for violation in violations
                    ]
                },
            )
        )
    if canonical != rights:
        walk.findings.append(
            AccessFinding(
                AccessCondition.ORDER_DEPENDENT_RESULT,
                {
                    "evaluated": f"0x{rights.value:08X}",
                    "canonical": f"0x{canonical.value:08X}",
                },
            )
        )
    _note_rights_conditions(rights, walk)

    return AclEvaluation(
        layer=layer,
        rights=rights,
        granted_by=tuple(walk.granted_by),
        denied_by=tuple(walk.denied_by),
        superseded=tuple(walk.superseded),
        owner_rights=owner_rights,
        canonical_rights=canonical,
        order_violations=violations,
        entries_evaluated=walk.evaluated,
        entries_supplied=len(supplied),
        findings=tuple(walk.findings),
    )


def _null_dacl(layer: RightsLayer, facts: DaclFacts, supplied: int) -> AclEvaluation:
    """A descriptor with no DACL: every principal holds every right.

    Reported as a condition, never as a quiet maximum. It is the most consequential state a
    descriptor can be in, and it is invisible in an ACL viewer, which shows an object with
    no DACL and one whose DACL grants Everyone Full Control identically.
    """
    findings = [AccessFinding(AccessCondition.NULL_DACL, {"owner_sid": facts.owner_sid})]
    if supplied:
        # Rows can outlive the descriptor they described: a run that read a real DACL, then
        # a later run finding it replaced by none. The entries are excluded from the
        # evaluation, because a NULL DACL has none by definition, and the disagreement is
        # reported instead of resolved by preferring whichever arrived first.
        findings.append(
            AccessFinding(
                AccessCondition.ACE_COUNT_MISMATCH,
                {"declared": 0, "held": supplied, "reason": "null_dacl"},
            )
        )
    return AclEvaluation(
        layer=layer,
        rights=RightsMask(FILE_ALL_ACCESS, layer),
        entries_supplied=supplied,
        findings=tuple(findings),
    )


def _owner_rights(
    owner_token_sid: TokenSid | None,
    facts: DaclFacts,
    entries: Sequence[AclEntry],
    walk: _Walk,
) -> RightsMask | None:
    """The rights the subject holds by owning the object, and the findings that explain them.

    ``None`` when the subject does not own it, or when an ``OWNER RIGHTS`` entry is present:
    that entry *replaces* the implicit rights rather than adding to them, which is the only
    supported way to stop an owner rewriting a DACL. The entry itself is then matched during
    the ordinary walk, so its Allow or Deny lands at its real position.
    """
    owner = facts.owner_sid
    if owner is None or owner_token_sid is None:
        return None

    if any(entry.names_owner_rights and entry.applies_to_this_object for entry in entries):
        walk.findings.append(AccessFinding(AccessCondition.OWNER_RIGHTS_ACE, {"owner_sid": owner}))
        return None

    walk.findings.append(
        AccessFinding(
            AccessCondition.OWNER_IMPLICIT_RIGHTS,
            {
                "owner_sid": owner,
                "owner_key": facts.owner_key,
                "rights": f"0x{OWNER_IMPLICIT_RIGHTS.value:08X}",
            },
        )
    )
    return OWNER_IMPLICIT_RIGHTS


def _apply(
    entry: AclEntry,
    position: int,
    token: SubjectToken,
    owner_token_sid: TokenSid | None,
    walk: _Walk,
) -> None:
    """Fold one entry into the walk, recording why it did or did not apply."""
    if not entry.applies_to_this_object:
        # INHERIT_ONLY. It is a statement about children; counting it here would report
        # access on a folder whose ACL viewer shows the entry greyed out.
        return

    if entry.names_substitution_placeholder:
        walk.findings.append(
            AccessFinding(
                AccessCondition.CREATOR_OWNER_ACE,
                {
                    "trustee_sid": entry.trustee_sid,
                    "ace_type": entry.ace_type.value,
                    "position": position,
                },
            )
        )
        return

    matched = _match(entry, token, owner_token_sid)
    if matched is None:
        if entry.trustee_sid in LOGON_SESSION_SIDS:
            walk.findings.append(
                AccessFinding(
                    AccessCondition.LOGON_TYPE_TRUSTEE,
                    {
                        "trustee_sid": entry.trustee_sid,
                        "ace_type": entry.ace_type.value,
                        "access_path": token.access_path.value,
                        "position": position,
                    },
                )
            )
        return

    walk.evaluated += 1
    normalized = entry.normalized
    if normalized.was_generic:
        walk.findings.append(
            AccessFinding(
                AccessCondition.GENERIC_RIGHTS_EXPANDED,
                {
                    "trustee_sid": entry.trustee_sid,
                    "raw": f"0x{normalized.raw.value:08X}",
                    "expanded": f"0x{normalized.normalized.value:08X}",
                },
            )
        )
    value = normalized.normalized.value

    if entry.is_allow:
        contributed = value & ~walk.denied
        walk.allow_union |= value
        applied = AppliedAce(
            entry=entry,
            matched=matched,
            position=position,
            considered=normalized.normalized,
            contributed=RightsMask(contributed, walk.layer),
        )
        if contributed:
            walk.granted |= contributed
            walk.granted_by.append(applied)
        else:
            walk.superseded.append(applied)
        return

    contributed = value & ~walk.granted
    walk.deny_union |= value
    applied = AppliedAce(
        entry=entry,
        matched=matched,
        position=position,
        considered=normalized.normalized,
        contributed=RightsMask(contributed, walk.layer),
    )
    if contributed:
        walk.denied |= contributed
        walk.denied_by.append(applied)
    else:
        walk.superseded.append(applied)


def _match(
    entry: AclEntry, token: SubjectToken, owner_token_sid: TokenSid | None
) -> TokenSid | None:
    """The token entry an ACE names, or ``None`` when it names nobody in this token.

    Matching is by **storage key**, not by SID. A BUILTIN SID means one machine's local
    group, so ``fs01|S-1-5-32-544`` and ``fs02|S-1-5-32-544`` are different trustees, and
    matching on ``S-1-5-32-544`` would hand every server's local administrators the rights
    of every other's.

    ``OWNER RIGHTS`` is the one entry matched on something other than membership. It is not
    a placeholder Windows substitutes at inheritance time — it is stored and inherited like
    any other trustee and resolved against the object's **current** owner at access time —
    so it applies exactly when the token holds the owner's SID, and it is credited to that
    token entry so an explanation says *whose* ownership made it apply.
    """
    if entry.names_owner_rights:
        return owner_token_sid
    return token.entry(entry.trustee_key)


def _note_rights_conditions(rights: RightsMask, walk: _Walk) -> None:
    """Record what the resulting mask itself says, beyond which entries produced it."""
    if rights.is_indeterminate:
        walk.findings.append(
            AccessFinding(AccessCondition.INDETERMINATE_RIGHTS, {"rights": f"0x{rights.value:08X}"})
        )
    if rights.unrecognized_bits:
        walk.findings.append(
            AccessFinding(
                AccessCondition.UNRECOGNIZED_RIGHTS_BITS,
                {"bits": f"0x{rights.unrecognized_bits:08X}"},
            )
        )
    # ESCALATION_RIGHTS is deliberately *not* raised here. One layer granting WRITE_DAC
    # says nothing on its own: a share granting Full Control in front of NTFS granting
    # nothing lets the principal change no ACL at all. It is a statement about the final
    # effective mask, and the resolver raises it there.
