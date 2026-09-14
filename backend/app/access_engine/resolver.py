r"""Effective access: what one principal can do to one SMB-backed resource, and why.

Three inputs meet here, and none of them is sufficient alone:

* the **identity graph**, which says which SIDs an access token would carry
  (:mod:`app.access_engine.subjects`);
* the **NTFS DACL** of the object, which is what limits local and remote access alike;
* the **share ACL** in front of it, which limits remote access only.

The result is the intersection of the two layers for a declared access path, computed by
:func:`app.access_engine.effective_rights`, over rights each layer produced by a faithful
Windows access check (:mod:`app.access_engine.evaluation`).

**Being listed on an ACL is not access.** That sentence is the whole point of this module,
and it has two halves, both of which are easy to get wrong in the direction that hides a
finding:

* an ACE naming a group the subject is in can be cancelled by a Deny, superseded by an
  earlier entry, or cut off by a share that grants less — so a trustee list is not an
  access list; and
* access can exist with no ACE naming anyone the subject knows about — a NULL DACL, an
  ``Everyone`` entry, ownership — so an *absence* from the trustee list is not an absence
  of access either.

Everything the resolver could not establish is named rather than defaulted. A missing
share ACL does not become "unrestricted"; a truncated group traversal does not become "not
a member"; a path whose descriptor was never read does not become "no access". Each
produces an :class:`~app.access_engine.conditions.AccessFinding` and moves
:attr:`EffectiveAccess.certainty` off :attr:`AccessCertainty.CERTAIN`, in the direction the
gap could be wrong in.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.access_engine.conditions import (
    OVERSTATING_CONDITIONS,
    UNDERSTATING_CONDITIONS,
    AccessCondition,
    AccessFinding,
)
from app.access_engine.evaluation import (
    UNOWNED_PRESENT_DACL,
    AclEntry,
    AclEvaluation,
    DaclFacts,
    evaluate_acl,
)
from app.access_engine.rights import (
    AccessPath,
    EffectiveRights,
    RightsLayer,
    RightsMask,
    RightsSummary,
    effective_rights,
    summarize,
)
from app.access_engine.subjects import SubjectToken
from app.domain.errors import DomainValidationError

__all__ = [
    "AccessCertainty",
    "AclProvenance",
    "EffectiveAccess",
    "LimitingLayer",
    "ResourceDacl",
    "ShareDacl",
    "certainty_of",
    "resolve_access",
    "unobserved_trustee_findings",
]


class AclProvenance(StrEnum):
    """Where the DACL that was evaluated came from.

    A projected DACL is a real answer — it is what Windows would have written on a cleanly
    inheriting child — but it is a *prediction*, and a consumer that cannot tell it from a
    reading of the object itself will eventually report one as the other.
    """

    OBSERVED = "observed"
    """A collector read this object's own security descriptor."""

    DERIVED = "derived"
    """Projected from the nearest ancestor that was read
    (:func:`app.domain.project_inherited_acl`). Correct only while nothing in between
    breaks inheritance."""

    UNOBSERVED = "unobserved"
    """Neither the object nor any ancestor was read. Nothing can be concluded."""


class LimitingLayer(StrEnum):
    """Which authorization layer removed rights the other would have granted.

    The single most actionable field in a result: it sends an administrator to the ACL that
    is actually in the way. "The user has Read" is a fact; "NTFS grants Modify and the share
    grants Read" is a fix.
    """

    NONE = "none"
    """Both layers grant the same rights, or only one layer applies and it removed nothing."""

    SMB_SHARE = "smb_share"
    NTFS = "ntfs"
    BOTH = "both"
    """Each layer withholds something the other grants."""

    UNKNOWN = "unknown"
    """One of the layers was not observed, so the comparison could not be made."""


class AccessCertainty(StrEnum):
    """How the reported rights may differ from the truth, given what ADG has not seen.

    Not a confidence score. Each value is a *direction*, derived from the conditions that
    fired, and it is what lets a consumer tell a finding from a gap: rights reported
    ``AT_LEAST`` may be wider in reality, which means a risk rule reading them may be
    looking at an under-count and must not conclude "nobody has access here".
    """

    CERTAIN = "certain"
    """Every input the answer depends on was observed."""

    AT_MOST = "at_most"
    """An unseen restriction could only narrow this. The rights are an upper bound."""

    AT_LEAST = "at_least"
    """An unseen grant or membership could only widen this. The rights are a lower bound."""

    UNCERTAIN = "uncertain"
    """Gaps in both directions, or a mask whose meaning Windows resolves at open time."""


@dataclass(frozen=True, slots=True)
class ResourceDacl:
    """The file-system side of the question: one object's DACL and descriptor facts.

    ``entries`` must be in the order the descriptor stored them. The resolver does not sort
    them, because the order is part of the meaning (see
    :func:`app.access_engine.evaluation.evaluate_acl`), and a caller that cannot establish
    an order has to say so rather than imposing a convenient one.
    """

    resource_key: str
    entries: tuple[AclEntry, ...] = ()
    dacl_present: bool = True
    dacl_protected: bool = False
    owner_sid: str | None = None
    owner_key: str | None = None
    """The owner's storage key. See :attr:`app.access_engine.DaclFacts.owner_key`; it
    defaults to ``owner_sid``, which is correct for everything but a BUILTIN owner."""

    declared_ace_count: int | None = None
    provenance: AclProvenance = AclProvenance.OBSERVED
    derived_from: str | None = None
    """The ancestor a derived DACL was projected from."""

    derived_distance: int | None = None
    """How many levels up that ancestor sits. More than one means the directories in
    between were never read, and any of them could carry its own permissions."""

    def __post_init__(self) -> None:
        if not self.resource_key:
            raise DomainValidationError("A resource needs a key.", field="resource_key")
        if (self.provenance is AclProvenance.DERIVED) != (self.derived_from is not None):
            raise DomainValidationError(
                "A derived DACL records the ancestor it was projected from, and an observed "
                "one has no ancestor to record: the two must agree.",
                field="derived_from",
            )
        if not self.dacl_present and self.provenance is AclProvenance.UNOBSERVED:
            raise DomainValidationError(
                "A NULL DACL is something a collector observed. An unread descriptor is "
                "unknown, and reporting it as a NULL DACL would grant everyone everything.",
                field="dacl_present",
            )
        if self.owner_key is None and self.owner_sid is not None:
            object.__setattr__(self, "owner_key", self.owner_sid)

    @property
    def facts(self) -> DaclFacts:
        return DaclFacts(
            dacl_present=self.dacl_present,
            dacl_protected=self.dacl_protected,
            owner_sid=self.owner_sid,
            owner_key=self.owner_key,
            declared_ace_count=self.declared_ace_count,
        )


@dataclass(frozen=True, slots=True)
class ShareDacl:
    """The share side: the ACL a remote connection passes through before NTFS is consulted.

    ``observed=False`` means no run has read it. That is emphatically **not** the same as
    an empty ACL, and the resolver refuses to treat it as one: an unread share ACL leaves
    the answer an upper bound rather than silently granting or silently denying.
    """

    share_key: str
    entries: tuple[AclEntry, ...] = ()
    observed: bool = True

    def __post_init__(self) -> None:
        if not self.share_key:
            raise DomainValidationError("A share needs a key.", field="share_key")
        if not self.observed and self.entries:
            raise DomainValidationError(
                "An unobserved share ACL cannot carry entries.", field="entries"
            )


@dataclass(frozen=True, slots=True)
class EffectiveAccess:
    """What a principal can do to a resource by one access path, with the whole derivation.

    Machine-readable throughout: :attr:`rights` is a :class:`RightsMask`, never a label, and
    :meth:`summary` recomputes the label on demand. A consumer that stores this must store
    the mask and the layer — a stored label cannot be compared, and comparing labels is the
    bug the rights model exists to prevent.
    """

    token: SubjectToken
    resource_key: str
    share_key: str | None
    path: AccessPath
    rights: RightsMask
    """The final effective mask, carrying :attr:`RightsLayer.EFFECTIVE`."""

    ntfs: AclEvaluation
    share: AclEvaluation | None
    crossed: EffectiveRights | None
    """The Phase 4A layer crossing, when both layers were available. ``None`` when the share
    ACL was not observed, because there was no second mask to cross with."""

    provenance: AclProvenance
    limiting_layer: LimitingLayer
    certainty: AccessCertainty
    findings: tuple[AccessFinding, ...]

    @property
    def has_access(self) -> bool:
        """Whether any right at all survives to the end.

        Read it together with :attr:`certainty`: ``False`` with
        :attr:`AccessCertainty.AT_LEAST` means *no access was established*, not *no access
        exists*, and an audit that treats the two alike stops looking exactly where it
        should keep looking.
        """
        return not self.rights.is_empty

    @property
    def ntfs_rights(self) -> RightsMask:
        return self.ntfs.rights

    @property
    def share_rights(self) -> RightsMask | None:
        return None if self.share is None else self.share.rights

    @property
    def grant_entries(self) -> tuple[AclEntry, ...]:
        """Every ACE that contributed a right, across both layers, in evaluation order."""
        entries = [applied.entry for applied in self.ntfs.granted_by]
        if self.share is not None:
            entries.extend(applied.entry for applied in self.share.granted_by)
        return tuple(entries)

    @property
    def deny_entries(self) -> tuple[AclEntry, ...]:
        """Every ACE that withheld a right, across both layers, in evaluation order."""
        entries = [applied.entry for applied in self.ntfs.denied_by]
        if self.share is not None:
            entries.extend(applied.entry for applied in self.share.denied_by)
        return tuple(entries)

    @property
    def conditions(self) -> tuple[AccessCondition, ...]:
        """The distinct conditions that qualify this answer, in first-seen order."""
        seen: dict[AccessCondition, None] = {}
        for finding in self.findings:
            seen.setdefault(finding.condition, None)
        return tuple(seen)

    def summary(self) -> RightsSummary:
        """The display rendering of :attr:`rights`. Recomputed, never stored."""
        return summarize(self.rights)


def resolve_access(
    token: SubjectToken,
    resource: ResourceDacl,
    share: ShareDacl | None = None,
    *,
    findings: Sequence[AccessFinding] = (),
) -> EffectiveAccess:
    """Resolve one principal's effective rights to one resource.

    Args:
        token: the SIDs to evaluate ACEs against, and the access path they were built for.
        resource: the object's DACL and descriptor facts.
        share: the share ACL in front of it. Required for
            :attr:`AccessPath.REMOTE_SMB` and refused for :attr:`AccessPath.LOCAL`, for the
            same reason :func:`app.access_engine.effective_rights` requires it: a remote
            calculation without the share layer would report access the share may not
            permit, and a local one *with* it would report a restriction Windows does not
            apply.
        findings: conditions the caller established that the domain cannot see for itself —
            :func:`unobserved_trustee_findings` above all, which needs a database to
            compute. They are folded into :attr:`EffectiveAccess.findings` and into the
            certainty, so a coverage gap the caller found is never weaker evidence than one
            the resolver found.

    Returns:
        The rights, both layers' evaluations, and every condition that qualifies them.
    """
    path = token.access_path
    if path is AccessPath.REMOTE_SMB and share is None:
        raise DomainValidationError(
            "A remote SMB resolution needs the share layer, even when its ACL was never "
            "read: an absent share ACL is unknown, and treating it as unrestricted "
            "over-reports access.",
            field="share",
        )
    if path is AccessPath.LOCAL and share is not None:
        raise DomainValidationError(
            "Local access does not pass through a share ACL. Supplying one would imply a "
            "restriction that anyone logged on to the server bypasses.",
            field="share",
        )

    collected: list[AccessFinding] = list(token.findings)

    ntfs = evaluate_acl(
        resource.entries,
        token,
        layer=RightsLayer.NTFS,
        facts=resource.facts,
    )
    collected.extend(_ntfs_findings(ntfs, resource))
    collected.extend(_provenance_findings(resource))

    share_evaluation: AclEvaluation | None = None
    crossed: EffectiveRights | None = None

    if share is None:
        crossed = effective_rights(ntfs=ntfs.rights, path=path)
        rights = crossed.rights
        limiting = LimitingLayer.NTFS
    elif share.observed:
        share_evaluation = evaluate_acl(
            share.entries, token, layer=RightsLayer.SMB_SHARE, facts=UNOWNED_PRESENT_DACL
        )
        collected.extend(share_evaluation.findings)
        crossed = effective_rights(ntfs=ntfs.rights, path=path, share=share_evaluation.rights)
        rights = crossed.rights
        limiting = _limiting_layer(crossed)
    else:
        # No share ACL was read. The share can only ever remove rights, so what NTFS grants
        # is an upper bound on what a remote connection gets — reported as one, rather than
        # crossed with a fabricated "Everyone Full Control" that would make the bound look
        # like an answer.
        collected.append(
            AccessFinding(AccessCondition.SHARE_ACL_NOT_OBSERVED, {"share_key": share.share_key})
        )
        rights = RightsMask.effective(ntfs.rights.expand_generics().value)
        limiting = LimitingLayer.UNKNOWN

    escalation = rights.escalation_rights
    if escalation:
        # Raised on the final mask, never per layer. A share granting Full Control in front
        # of NTFS granting nothing hands the principal no ability to rewrite an ACL, and a
        # per-layer finding would report one anyway on a very large number of shares.
        collected.append(
            AccessFinding(
                AccessCondition.ESCALATION_RIGHTS,
                {"rights": str(escalation), "mask": f"0x{rights.value:08X}"},
            )
        )

    collected.extend(findings)

    return EffectiveAccess(
        token=token,
        resource_key=resource.resource_key,
        share_key=None if share is None else share.share_key,
        path=path,
        rights=rights,
        ntfs=ntfs,
        share=share_evaluation,
        crossed=crossed,
        provenance=resource.provenance,
        limiting_layer=limiting,
        certainty=certainty_of(collected),
        findings=tuple(collected),
    )


def certainty_of(findings: Iterable[AccessFinding]) -> AccessCertainty:
    """Fold a set of findings into the direction the answer may be wrong in.

    Both directions at once is :attr:`AccessCertainty.UNCERTAIN` rather than a cancellation:
    two gaps that could each move the answer the other way do not make it right, and a
    result nobody can bound is exactly the one that must not be reported as a number
    without qualification. ``INDETERMINATE_RIGHTS`` alone reaches the same value, because
    ``MAXIMUM_ALLOWED`` has no fixed meaning in either direction.
    """
    conditions = {finding.condition for finding in findings}
    if AccessCondition.INDETERMINATE_RIGHTS in conditions:
        return AccessCertainty.UNCERTAIN
    overstates = bool(conditions & OVERSTATING_CONDITIONS)
    understates = bool(conditions & UNDERSTATING_CONDITIONS)
    if overstates and understates:
        return AccessCertainty.UNCERTAIN
    if overstates:
        return AccessCertainty.AT_MOST
    if understates:
        return AccessCertainty.AT_LEAST
    return AccessCertainty.CERTAIN


def _limiting_layer(crossed: EffectiveRights) -> LimitingLayer:
    if crossed.limited_by_share and crossed.limited_by_ntfs:
        return LimitingLayer.BOTH
    if crossed.limited_by_share:
        return LimitingLayer.SMB_SHARE
    if crossed.limited_by_ntfs:
        return LimitingLayer.NTFS
    return LimitingLayer.NONE


def _ntfs_findings(ntfs: AclEvaluation, resource: ResourceDacl) -> tuple[AccessFinding, ...]:
    """The evaluation's own findings, less any it could not have observed.

    An unread descriptor is evaluated against no entries, and :func:`evaluate_acl` quite
    correctly reports ``EMPTY_DACL`` for a DACL with nothing in it. But nobody observed this
    DACL to be empty — nobody observed it at all — and ``EMPTY_DACL`` is a statement about a
    descriptor that was read and found to grant nobody anything, which is a real and
    materially different finding. Reporting both leaves a consumer to decide which of two
    contradictory statements about the same object to believe.
    """
    if resource.provenance is not AclProvenance.UNOBSERVED:
        return ntfs.findings
    return tuple(
        finding for finding in ntfs.findings if finding.condition is not AccessCondition.EMPTY_DACL
    )


def _provenance_findings(resource: ResourceDacl) -> tuple[AccessFinding, ...]:
    """What the DACL's origin adds to the answer's qualifications."""
    if resource.provenance is AclProvenance.OBSERVED:
        return ()
    if resource.provenance is AclProvenance.UNOBSERVED:
        return (
            AccessFinding(
                AccessCondition.NTFS_ACL_NOT_OBSERVED, {"resource_key": resource.resource_key}
            ),
        )

    findings = [
        AccessFinding(
            AccessCondition.NTFS_ACL_DERIVED,
            {
                "resource_key": resource.resource_key,
                "derived_from": resource.derived_from,
                "distance": resource.derived_distance,
            },
        )
    ]
    if resource.derived_distance is not None and resource.derived_distance > 1:
        findings.append(
            AccessFinding(
                AccessCondition.INTERMEDIATE_PATH_UNOBSERVED,
                {
                    "resource_key": resource.resource_key,
                    "derived_from": resource.derived_from,
                    "unobserved_levels": resource.derived_distance - 1,
                },
            )
        )
    return tuple(findings)


def unobserved_trustee_findings(
    trustee_keys: Iterable[str], *, trustees_with_membership: Iterable[str]
) -> tuple[AccessFinding, ...]:
    """One finding per ACL trustee whose membership has never been collected.

    An ACE naming a group the subject is not in settles something only if ADG has actually
    collected that group's membership. When it has not, "no match" is ignorance rather than
    a conclusion, and the answer is a lower bound — the failure mode this module exists to
    make visible, because it is the one that makes a finding disappear.

    The judgement lives here, in the domain vocabulary; the *evidence* has to come from the
    caller, which is the only thing that can query for membership edges. That split is what
    keeps the resolver free of I/O without pushing the condition out of the domain.

    Args:
        trustee_keys: the trustees named on the ACLs that were evaluated, unmatched ones
            included — those are precisely the ones the subject might belong to.
        trustees_with_membership: of those, the ones ADG holds at least one membership edge
            for. Everything else is a group whose contents nobody has looked at, or a
            principal that is not a group at all; the caller distinguishes them, because
            only it knows the principal kinds.
    """
    known = set(trustees_with_membership)
    ordered = list(dict.fromkeys(trustee_keys))
    return tuple(
        AccessFinding(AccessCondition.TRUSTEE_MEMBERSHIP_UNOBSERVED, {"trustee_key": key})
        for key in ordered
        if key not in known
    )
