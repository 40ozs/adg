r"""The rules. One class per shape, each a pure predicate over :class:`RiskFacts`.

Every rule in this module obeys four constraints, and the fourth is the one that took the
most care to get right.

1. **Deterministic.** No clock, no randomness, no dictionary iteration order that is not
   sorted first. The same facts produce the same outcomes, in the same order, every time.
2. **Evidence-complete.** A rule cites every record it read. Not a summary of it — the
   record, so that :func:`app.risk_engine.engine.reproduce` can rebuild the facts and get the
   same outcome back.
3. **No second opinion about access.** A rule that needs to know what a trustee actually holds
   asks :func:`app.access_engine.evaluate_acl` — the same order-sensitive Windows access check
   the effective-access screen runs. It does not add masks up itself, and in particular it
   does not use the canonical "all allows minus all denies" model, which would under-report an
   Allow placed ahead of a Deny and so would *hide* the exposure that ordering created.
4. **A coverage gap is never a finding.** This is the one that has to be checked rule by rule,
   because the failure is silent and it always points the same way: an unread share ACL looks
   like a share that grants nothing, an unenumerated group looks like a group with no members,
   an undescribed principal looks like a principal that is not a user. Every rule below that
   could turn one of those into a verdict states in its own docstring which fact it checks
   first and what it does when the fact is unknown.

**Which entries a finding cites.** When a rule reports something about one trustee on one
access control list, it cites the container record and *every entry on that list naming that
trustee* — allow and deny alike, in stored order. That is exactly the set the access check
reads for that trustee, since it skips entries whose trustee is not in the token, so the
restricted citation reproduces the same rights as the full list. Citing the whole list would
be correct too and would put every unrelated entry in the evidence of every finding.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.access_engine import (
    CATEGORY_REQUIRED_MASKS,
    AccessPath,
    AclEntry,
    AclEvaluation,
    DaclFacts,
    RightsCategory,
    RightsLayer,
    RightsMask,
    SidOrigin,
    SubjectFacts,
    SubjectToken,
    TokenAssumption,
    TokenSid,
    evaluate_acl,
    normalize_share_permission,
    summarize,
)
from app.domain import AceFlag, AclBoundaryReason, AclLayer, Sid
from app.risk_engine.catalog import CATALOG, RuleDefinition, RuleId
from app.risk_engine.configuration import RuleConfig, SensitiveResource
from app.risk_engine.evidence import Evidence, EvidenceItem
from app.risk_engine.facts import (
    AceFacts,
    AclProvenanceFacts,
    Chain,
    PrincipalFacts,
    ResourceFacts,
    RiskFacts,
    ShareFacts,
    domain_relative_trustee,
)
from app.risk_engine.findings import FindingSubject, RuleOutcome
from app.risk_engine.severity import FactQualifier, SeverityBand

__all__ = ["RULES", "Rule", "band_for", "rule_for"]

EVERYONE_SID: Final = "S-1-1-0"
AUTHENTICATED_USERS_SID: Final = "S-1-5-11"
DOMAIN_USERS_RID: Final = 513


# --------------------------------------------------------------------------------------
# Shared machinery
# --------------------------------------------------------------------------------------


def _entry_mask(ace: AceFacts) -> RightsMask:
    """The entry's mask exactly as observed, in its own layer, generic bits intact."""
    if ace.access_mask is not None:
        if ace.layer is AclLayer.NTFS:
            return RightsMask.ntfs(ace.access_mask)
        return RightsMask.smb(ace.access_mask)
    assert ace.permission is not None  # guaranteed by AceFacts.__post_init__
    return normalize_share_permission(ace.permission)


def _acl_entry(ace: AceFacts) -> AclEntry:
    return AclEntry(
        trustee_key=ace.trustee_key,
        trustee_sid=ace.trustee_sid,
        ace_type=ace.ace_type,
        mask=_entry_mask(ace),
        flags=AceFlag(ace.ace_flags),
        source=ace.source,
        order_index=ace.order_index,
        ace_key=ace.ace_key,
    )


def _token_for(trustee_key: str, trustee_sid: str) -> SubjectToken:
    """A token containing exactly one SID.

    Built directly rather than through :func:`app.access_engine.build_token`, which would add
    the SIDs a logon session contributes. Here the question is narrower and more literal:
    *what does this access control list grant to entries naming this trustee* — so widening
    the token would fold ``Everyone``'s grant into every other trustee's finding.
    """
    return SubjectToken(
        subject=SubjectFacts(key=trustee_key, sid=trustee_sid),
        assumption=TokenAssumption.SIDS_ONLY,
        access_path=AccessPath.REMOTE_SMB,
        entries=(TokenSid(key=trustee_key, sid=trustee_sid, origin=SidOrigin.SUBJECT),),
    )


@dataclass(frozen=True, slots=True)
class _Acl:
    """One access control list, on either layer, with what a rule needs to reason about it."""

    key: str
    layer: AclLayer
    aces: tuple[AceFacts, ...]
    container: EvidenceItem
    qualifiers: frozenset[FactQualifier]
    resource: ResourceFacts | None = None
    share: ShareFacts | None = None

    @property
    def rights_layer(self) -> RightsLayer:
        return RightsLayer.NTFS if self.layer is AclLayer.NTFS else RightsLayer.SMB_SHARE

    @property
    def facts(self) -> DaclFacts:
        if self.resource is None:
            return DaclFacts()
        return DaclFacts(
            dacl_present=self.resource.dacl_present,
            dacl_protected=self.resource.dacl_protected,
            owner_sid=self.resource.owner_sid,
        )

    def subject(
        self, *, principal_key: str | None = None, discriminator: str | None = None
    ) -> FindingSubject:
        return FindingSubject(
            resource_key=None if self.resource is None else self.resource.resource_key,
            share_key=self.key if self.share is not None else None,
            principal_key=principal_key,
            discriminator=discriminator,
        )

    def entries_naming(self, trustee_key: str) -> tuple[AceFacts, ...]:
        """Every entry naming ``trustee_key``, in stored order.

        The complete set the access check reads for that trustee: it skips every entry whose
        trustee is not in the token, so nothing outside this set can change the answer.
        """
        return tuple(ace for ace in self.aces if ace.trustee_key == trustee_key)

    def evaluate_for(self, trustee_key: str, trustee_sid: str) -> AclEvaluation:
        """What this list grants a token holding only ``trustee_key``.

        Runs the real access check over the entries in stored order, so an Allow placed ahead
        of a Deny grants — which is what Windows does, and the direction that keeps a finding
        rather than losing one.
        """
        return evaluate_acl(
            (_acl_entry(ace) for ace in self.entries_naming(trustee_key)),
            _token_for(trustee_key, trustee_sid),
            layer=self.rights_layer,
            facts=self.facts,
        )

    def cite(self, trustee_key: str) -> list[EvidenceItem]:
        """The container record plus every entry naming ``trustee_key``."""
        items = [self.container]
        items.extend(EvidenceItem.for_ace(ace) for ace in self.entries_naming(trustee_key))
        return items


def _acls(facts: RiskFacts) -> Iterator[_Acl]:
    """Every access control list in the bundle, resources first, in key order.

    A share whose ACL no run has read is skipped entirely rather than yielded empty. Yielding
    it would let any rule that matches on an *absence* conclude something about a list nobody
    has looked at, and there is no way to write that rule safely once the empty list is in
    front of it.
    """
    for resource in sorted(facts.resources, key=lambda item: item.resource_key):
        yield _Acl(
            key=resource.resource_key,
            layer=AclLayer.NTFS,
            aces=resource.aces,
            container=EvidenceItem.for_resource(resource),
            qualifiers=resource.qualifiers,
            resource=resource,
        )
    for share in sorted(facts.shares, key=lambda item: item.share_key):
        if not share.acl_observed:
            continue
        yield _Acl(
            key=share.share_key,
            layer=AclLayer.SMB_SHARE,
            aces=share.aces,
            container=EvidenceItem.for_share(share),
            qualifiers=frozenset(),
            share=share,
        )


def _reaches(mask: RightsMask, category: RightsCategory) -> bool:
    """Whether ``mask`` contains every bit the category requires.

    Containment against :data:`app.access_engine.CATEGORY_REQUIRED_MASKS` rather than a rank
    comparison, for the same reason the rights model labels a mask that way: a category names
    a set of bits, and a mask either holds them all or does not describe that level of access.
    """
    required = CATEGORY_REQUIRED_MASKS.get(category)
    if required is None:  # pragma: no cover - refused by RuleConfig.option_category
        return False
    return mask.expand_generics().value & required == required


def band_for(mask: RightsMask) -> SeverityBand:
    """Which band a granted mask falls into.

    Three bands, coarsest first, because the distinction an operator acts on is *can they
    change it* and *can they re-permission it* rather than the exact bit pattern. A mask that
    reaches no named category at all is reported as ``READ``: it is the lowest band the broad
    rules declare, and a rule only reaches this function after its own threshold was met.
    """
    if _reaches(mask, RightsCategory.FULL_CONTROL):
        return SeverityBand.FULL_CONTROL
    if _reaches(mask, RightsCategory.MODIFY) or _reaches(mask, RightsCategory.WRITE):
        return SeverityBand.WRITE
    return SeverityBand.READ


class Rule(ABC):
    """One shape ADG looks for.

    Subclasses implement :meth:`evaluate` and nothing else. Identity, prose, severity bands
    and thresholds all come from the catalog entry, which :attr:`definition` resolves by
    :attr:`rule_id` — so a rule cannot describe itself differently from the way it is
    documented and configured.
    """

    rule_id: RuleId

    @property
    def definition(self) -> RuleDefinition:
        return CATALOG[self.rule_id]

    @abstractmethod
    def evaluate(self, facts: RiskFacts, config: RuleConfig) -> Iterator[RuleOutcome]:
        """Every match in ``facts``, in a deterministic order."""


# --------------------------------------------------------------------------------------
# Broad-trustee rules
# --------------------------------------------------------------------------------------


class _BroadTrusteeRule(Rule):
    """Shared body for the three rules that name one well-known population.

    The three differ only in which trustee they look for, so the predicate lives here once.
    A separate implementation each would be three chances for the Everyone rule and the
    Authenticated Users rule to disagree about what "grants Modify" means.
    """

    def matches_trustee(self, ace: AceFacts) -> bool:
        raise NotImplementedError

    def evaluate(self, facts: RiskFacts, config: RuleConfig) -> Iterator[RuleOutcome]:
        threshold = config.option_category("minimum_category")
        for acl in _acls(facts):
            yield from self._for_acl(acl, threshold, config)

    def _for_acl(
        self, acl: _Acl, threshold: RightsCategory, config: RuleConfig
    ) -> Iterator[RuleOutcome]:
        seen: set[str] = set()
        for ace in acl.aces:
            if not ace.is_allow or not ace.applies_to_this_object:
                continue
            if not self.matches_trustee(ace) or ace.trustee_key in seen:
                continue
            seen.add(ace.trustee_key)
            evaluation = acl.evaluate_for(ace.trustee_key, ace.trustee_sid)
            if not _reaches(evaluation.rights, threshold):
                continue
            yield RuleOutcome(
                subject=acl.subject(discriminator=ace.trustee_key),
                band=band_for(evaluation.rights),
                evidence=Evidence.of(acl.cite(ace.trustee_key)),
                qualifiers=acl.qualifiers,
                detail={
                    "trustee_sid": ace.trustee_sid,
                    "layer": acl.layer.value,
                    "rights_mask": evaluation.rights.value,
                    "rights_label": summarize(evaluation.rights).label,
                    "minimum_category": threshold.value,
                    "rule_version": config.definition.version,
                },
            )


class EveryoneBroadAccess(_BroadTrusteeRule):
    """Everyone (``S-1-1-0``) can reach the resource, or there is no DACL at all.

    The NULL-DACL half is folded in here rather than made its own rule because it is the same
    finding stated by a different mechanism: an object with ``SE_DACL_PRESENT`` clear grants
    every right to every caller, which is Everyone with Full Control expressed as the absence
    of a list. Reporting it separately would let an installation silence one and believe it
    had silenced both.

    A NULL DACL is emphatically not an unread one. :class:`ResourceFacts` keeps the two apart
    — ``dacl_present`` is an observation, ``provenance`` says whether anybody looked — and
    this rule matches only on the first.
    """

    rule_id = RuleId.EVERYONE_BROAD_ACCESS

    def matches_trustee(self, ace: AceFacts) -> bool:
        return ace.trustee_sid == EVERYONE_SID

    def evaluate(self, facts: RiskFacts, config: RuleConfig) -> Iterator[RuleOutcome]:
        for resource in sorted(facts.resources, key=lambda item: item.resource_key):
            # A NULL DACL is something a collector *read*. A descriptor nobody fetched is
            # unknown, and reporting it as a NULL DACL would turn every unscanned directory
            # into a critical finding -- the same distinction ResourceDacl refuses to blur.
            if resource.dacl_present or resource.provenance is not AclProvenanceFacts.OBSERVED:
                continue
            yield RuleOutcome(
                subject=FindingSubject(
                    resource_key=resource.resource_key, discriminator="null_dacl"
                ),
                band=SeverityBand.NULL_DACL,
                evidence=Evidence.of([EvidenceItem.for_resource(resource)]),
                qualifiers=resource.qualifiers,
                detail={
                    "path": resource.path,
                    "layer": AclLayer.NTFS.value,
                    "rule_version": config.definition.version,
                },
            )
        yield from super().evaluate(facts, config)


class AuthenticatedUsersBroadAccess(_BroadTrusteeRule):
    """Authenticated Users (``S-1-5-11``) can reach the resource."""

    rule_id = RuleId.AUTHENTICATED_USERS_BROAD_ACCESS

    def matches_trustee(self, ace: AceFacts) -> bool:
        return ace.trustee_sid == AUTHENTICATED_USERS_SID


class DomainUsersBroadAccess(_BroadTrusteeRule):
    """A domain's Domain Users group can reach the resource.

    Matched by RID rather than by a constant, because ``Domain Users`` is ``<domain SID>-513``
    and so is a different string in every domain and every forest. Matching by display name
    would be worse still: the group is renameable, and an attacker who renames it is not
    thereby out of scope.
    """

    rule_id = RuleId.DOMAIN_USERS_BROAD_ACCESS

    def matches_trustee(self, ace: AceFacts) -> bool:
        return domain_relative_trustee(ace.trustee_sid, DOMAIN_USERS_RID)


# --------------------------------------------------------------------------------------
# Trustee-shaped rules
# --------------------------------------------------------------------------------------


class DirectUserAce(Rule):
    """A user account is named directly on an access control list.

    **The coverage check:** the rule fires only when ADG holds a principal record saying the
    trustee is a user or a managed service account. A trustee nothing has described is not
    reported here — it is the unresolved-SID finding — because "no record" and "not a user"
    are the same absence and treating them alike would report every orphaned SID twice under
    two different remediations.
    """

    rule_id = RuleId.DIRECT_USER_ACE

    def evaluate(self, facts: RiskFacts, config: RuleConfig) -> Iterator[RuleOutcome]:
        for acl in _acls(facts):
            seen: set[str] = set()
            for ace in acl.aces:
                if not ace.is_allow or not ace.applies_to_this_object:
                    continue
                if ace.trustee_key in seen:
                    continue
                principal = facts.principal(ace.trustee_key)
                if principal is None or not principal.is_user:
                    continue
                seen.add(ace.trustee_key)
                evaluation = acl.evaluate_for(ace.trustee_key, ace.trustee_sid)
                if evaluation.rights.is_empty:
                    continue
                items = acl.cite(ace.trustee_key)
                items.append(EvidenceItem.for_principal(principal))
                yield RuleOutcome(
                    subject=acl.subject(principal_key=ace.trustee_key),
                    band=SeverityBand.DEFAULT,
                    evidence=Evidence.of(items),
                    qualifiers=acl.qualifiers,
                    detail={
                        "trustee_sid": ace.trustee_sid,
                        "trustee_label": principal.label,
                        "layer": acl.layer.value,
                        "rights_label": summarize(evaluation.rights).label,
                        "rule_version": config.definition.version,
                    },
                )


class UnresolvedSidOnAcl(Rule):
    """An access control list names a SID nothing resolved.

    Three states count, and they are deliberately reported as one finding with the reason in
    the detail rather than as three rules: no principal record at all, a record whose kind is
    ``unresolved``, and a record marked deleted. All three mean *this grant names something
    that cannot be identified*, and the remediation — find out what it was before removing it
    — is the same for each.
    """

    rule_id = RuleId.UNRESOLVED_SID_ON_ACL

    def evaluate(self, facts: RiskFacts, config: RuleConfig) -> Iterator[RuleOutcome]:
        for acl in _acls(facts):
            seen: set[str] = set()
            for ace in acl.aces:
                if ace.trustee_key in seen:
                    continue
                principal = facts.principal(ace.trustee_key)
                reason = _unresolved_reason(principal, ace.trustee_sid)
                if reason is None:
                    continue
                seen.add(ace.trustee_key)
                items = acl.cite(ace.trustee_key)
                if principal is not None:
                    items.append(EvidenceItem.for_principal(principal))
                yield RuleOutcome(
                    subject=acl.subject(discriminator=ace.trustee_key),
                    band=SeverityBand.DEFAULT,
                    evidence=Evidence.of(items),
                    qualifiers=acl.qualifiers,
                    detail={
                        "trustee_sid": ace.trustee_sid,
                        "trustee_key": ace.trustee_key,
                        "reason": reason,
                        "layer": acl.layer.value,
                        "last_known_name": None if principal is None else principal.display_name,
                        "rule_version": config.definition.version,
                    },
                )


def _unresolved_reason(principal: PrincipalFacts | None, trustee_sid: str) -> str | None:
    """Why a trustee counts as unresolvable, or ``None`` when it does not.

    A **well-known SID with no principal record is not an orphan.** ``S-1-1-0`` means
    ``Everyone`` on every Windows computer that has ever existed, and ``S-1-5-32-544`` means
    that machine's local administrators; whether some run happened to emit a row for it says
    nothing about whether it can be identified. Reporting them here would put an
    unactionable finding on almost every access control list in the estate, and would bury
    the real orphans — the deleted domain accounts, which look exactly like this except that
    their SIDs mean nothing anywhere.

    The check is by SID rather than by a stored kind, deliberately: it must hold for a
    trustee ADG holds *no* record of, which is the whole case being decided.
    """
    if principal is None:
        parsed = Sid.try_parse(trustee_sid)
        if parsed is not None and parsed.is_well_known:
            return None
        return "no_principal_record"
    if principal.is_unresolved:
        return (
            "unresolved"
            if principal.unresolved_reason is None
            else principal.unresolved_reason.value
        )
    if principal.is_deleted:
        return "deleted"
    return None


# --------------------------------------------------------------------------------------
# Membership-shaped rules
# --------------------------------------------------------------------------------------


def _trustee_index(facts: RiskFacts) -> Mapping[str, tuple[_Acl, ...]]:
    """Which access control lists name each trustee, in list order.

    Built once per evaluation so the three membership rules below do one pass over the ACLs
    between them rather than one each.
    """
    index: dict[str, dict[tuple[str, str], _Acl]] = {}
    for acl in _acls(facts):
        for ace in acl.aces:
            if not ace.is_allow or not ace.applies_to_this_object:
                continue
            index.setdefault(ace.trustee_key, {}).setdefault((acl.layer.value, acl.key), acl)
    return {
        key: tuple(value[position] for position in sorted(value))
        for key, value in sorted(index.items())
    }


class DisabledPrincipalRetainsAccess(Rule):
    """A disabled account still holds access, directly or through a group.

    **The coverage check:** only a principal whose record says ``enabled is False`` is
    reported. ``None`` means nobody recorded the account's status — which is the normal state
    for a group, for a well-known SID, and for anything a directory collector has not read —
    and a rule that treated unknown as disabled would report every group in the estate.
    """

    rule_id = RuleId.DISABLED_PRINCIPAL_RETAINS_ACCESS

    def evaluate(self, facts: RiskFacts, config: RuleConfig) -> Iterator[RuleOutcome]:
        depth = config.option_int("max_membership_depth", minimum=1)
        index = _trustee_index(facts)
        for trustee_key, acls in index.items():
            for subject_key, chain in self._disabled_below(facts, trustee_key, depth):
                principal = facts.principal(subject_key)
                if principal is None:  # pragma: no cover - _disabled_below only yields records
                    continue
                for acl in acls:
                    yield self._outcome(acl, trustee_key, principal, chain, facts, config)

    def _disabled_below(
        self, facts: RiskFacts, trustee_key: str, depth: int
    ) -> Iterator[tuple[str, Chain]]:
        """The trustee itself when disabled, then every disabled principal inside it.

        The trustee is yielded with a zero-length chain, which is how "named directly on the
        list" and "reached through three groups" become the same finding shape with a
        different chain length rather than two rules.
        """
        own = facts.principal(trustee_key)
        if own is not None and own.enabled is False:
            yield trustee_key, Chain(keys=(trustee_key,), edges=())
        for chain in facts.chains_down(trustee_key, depth):
            member = facts.principal(chain.keys[-1])
            if member is not None and member.enabled is False:
                yield chain.keys[-1], chain

    def _outcome(
        self,
        acl: _Acl,
        trustee_key: str,
        principal: PrincipalFacts,
        chain: Chain,
        facts: RiskFacts,
        config: RuleConfig,
    ) -> RuleOutcome:
        evaluation = acl.evaluate_for(trustee_key, _sid_of(facts, trustee_key))
        items = acl.cite(trustee_key)
        items.append(EvidenceItem.for_principal(principal))
        items.extend(EvidenceItem.for_membership(edge) for edge in chain.edges)
        qualifiers = set(acl.qualifiers)
        if chain.truncated:
            qualifiers.add(FactQualifier.MEMBERSHIP_TRUNCATED)
        return RuleOutcome(
            subject=acl.subject(principal_key=principal.key, discriminator=trustee_key),
            band=SeverityBand.DEFAULT,
            evidence=Evidence.of(items),
            qualifiers=frozenset(qualifiers),
            detail={
                "principal_label": principal.label,
                "principal_sid": principal.sid,
                "trustee_key": trustee_key,
                "depth": chain.depth,
                "chain": list(chain.keys),
                "layer": acl.layer.value,
                "rights_label": summarize(evaluation.rights).label,
                "rule_version": config.definition.version,
            },
        )


class DeepGroupNesting(Rule):
    """A group named on an access control list nests deeper than the configured maximum.

    Reported once per trustee at the deepest chain found, not once per chain: a trustee with
    forty routes of depth five is one problem, and forty findings about it would bury the
    rest of the report. The chain that is cited is the deepest one, chosen by length and then
    by key so the choice is stable between evaluations.
    """

    rule_id = RuleId.DEEP_GROUP_NESTING

    def evaluate(self, facts: RiskFacts, config: RuleConfig) -> Iterator[RuleOutcome]:
        maximum = config.option_int("max_depth", minimum=1)
        # One level beyond the threshold is all that is needed to establish the breach, and
        # walking further would cost more on exactly the estates that are already the worst
        # shaped for it.
        for trustee_key, acls in _trustee_index(facts).items():
            deepest = self._deepest(facts, trustee_key, maximum + 1)
            if deepest is None or deepest.depth <= maximum:
                continue
            principal = facts.principal(trustee_key)
            items: list[EvidenceItem] = [acl.container for acl in acls]
            items.extend(
                EvidenceItem.for_ace(ace) for acl in acls for ace in acl.entries_naming(trustee_key)
            )
            if principal is not None:
                items.append(EvidenceItem.for_principal(principal))
            items.extend(EvidenceItem.for_membership(edge) for edge in deepest.edges)
            qualifiers = set(facts.qualifiers_for([trustee_key]))
            if deepest.truncated:
                qualifiers.add(FactQualifier.MEMBERSHIP_TRUNCATED)
            yield RuleOutcome(
                subject=FindingSubject(principal_key=trustee_key),
                band=SeverityBand.DEFAULT,
                evidence=Evidence.of(items),
                qualifiers=frozenset(qualifiers),
                detail={
                    "depth": deepest.depth,
                    "max_depth": maximum,
                    "chain": list(deepest.keys),
                    "trustee_label": None if principal is None else principal.label,
                    "resources": [acl.key for acl in acls],
                    "rule_version": config.definition.version,
                },
            )

    def _deepest(self, facts: RiskFacts, trustee_key: str, limit: int) -> Chain | None:
        best: Chain | None = None
        for chain in facts.chains_down(trustee_key, limit):
            if best is None or (chain.depth, chain.keys) > (best.depth, best.keys):
                best = chain
        return best


class RedundantAccessPaths(Rule):
    """One principal reaches one resource through several trustees named on its lists.

    "Distinct route" means *a different trustee on the access control list*, not a different
    membership chain to the same trustee. Removing a chain to a trustee that is still on the
    list changes nothing at all, so counting chains would report a redundancy that removing
    one of them would not resolve — and the whole point of the finding is that a removal will
    not take effect.
    """

    rule_id = RuleId.REDUNDANT_ACCESS_PATHS

    def evaluate(self, facts: RiskFacts, config: RuleConfig) -> Iterator[RuleOutcome]:
        minimum = config.option_int("minimum_paths", minimum=2)
        depth = config.option_int("max_membership_depth", minimum=1)
        for acl in _acls(facts):
            yield from self._for_acl(acl, facts, minimum, depth, config)

    def _for_acl(
        self,
        acl: _Acl,
        facts: RiskFacts,
        minimum: int,
        depth: int,
        config: RuleConfig,
    ) -> Iterator[RuleOutcome]:
        reached: dict[str, dict[str, Chain]] = {}
        trustees = sorted(
            {ace.trustee_key for ace in acl.aces if ace.is_allow and ace.applies_to_this_object}
        )
        for trustee_key in trustees:
            for chain in facts.chains_down(trustee_key, depth):
                member = chain.keys[-1]
                principal = facts.principal(member)
                if principal is not None and principal.is_group:
                    continue
                routes = reached.setdefault(member, {})
                existing = routes.get(trustee_key)
                if existing is None or chain.depth < existing.depth:
                    routes[trustee_key] = chain

        for member_key in sorted(reached):
            routes = reached[member_key]
            if len(routes) < minimum:
                continue
            principal = facts.principal(member_key)
            items: list[EvidenceItem] = [acl.container]
            qualifiers = set(acl.qualifiers)
            for trustee_key in sorted(routes):
                items.extend(EvidenceItem.for_ace(ace) for ace in acl.entries_naming(trustee_key))
                chain = routes[trustee_key]
                items.extend(EvidenceItem.for_membership(edge) for edge in chain.edges)
                if chain.truncated:
                    qualifiers.add(FactQualifier.MEMBERSHIP_TRUNCATED)
            if principal is not None:
                items.append(EvidenceItem.for_principal(principal))
            yield RuleOutcome(
                subject=acl.subject(principal_key=member_key),
                band=SeverityBand.DEFAULT,
                evidence=Evidence.of(items),
                qualifiers=frozenset(qualifiers),
                detail={
                    "principal_label": None if principal is None else principal.label,
                    "route_count": len(routes),
                    "minimum_paths": minimum,
                    "trustees": sorted(routes),
                    "layer": acl.layer.value,
                    "rule_version": config.definition.version,
                },
            )


class EmptyPermissionBearingGroup(Rule):
    """A group named on an access control list that a reconciling run found empty.

    **The coverage check, and the reason this rule exists in the form it does.** A group with
    no collected members and a group with no members look identical in storage. The only thing
    that separates them is whether a run that was *authoritative for the scope the group lives
    in* enumerated it, which is what :attr:`MembershipFacts.enumerated` records — and this rule
    fires only when that is ``True``.

    ``None`` and ``False`` produce nothing. Not a lower-confidence finding, not an
    informational note: nothing. A finding that said "this group may be empty, or may simply
    never have been collected" would be acted on by somebody, and the action is to remove a
    grant from a group that may have a hundred members in it.
    """

    rule_id = RuleId.EMPTY_PERMISSION_BEARING_GROUP

    def evaluate(self, facts: RiskFacts, config: RuleConfig) -> Iterator[RuleOutcome]:
        for trustee_key, acls in _trustee_index(facts).items():
            membership = facts.membership(trustee_key)
            if membership is None or membership.enumerated is not True:
                continue
            if not membership.is_empty:
                continue
            principal = facts.principal(trustee_key)
            if principal is None or not principal.is_group:
                continue
            items: list[EvidenceItem] = [acl.container for acl in acls]
            items.extend(
                EvidenceItem.for_ace(ace) for acl in acls for ace in acl.entries_naming(trustee_key)
            )
            items.append(EvidenceItem.for_principal(principal))
            items.append(EvidenceItem.for_membership(membership))
            yield RuleOutcome(
                subject=FindingSubject(principal_key=trustee_key),
                band=SeverityBand.DEFAULT,
                evidence=Evidence.of(items),
                qualifiers=frozenset(),
                detail={
                    "group_label": principal.label,
                    "group_sid": principal.sid,
                    "resources": [acl.key for acl in acls],
                    "rule_version": config.definition.version,
                },
            )


# --------------------------------------------------------------------------------------
# Resource-shaped rules
# --------------------------------------------------------------------------------------


class BrokenInheritance(Rule):
    """Permissions change at this directory.

    **The coverage check, restated for boundaries.** :class:`app.domain.AclBoundaryReason`
    has seven values and only two of them are a statement about permissions: ``protected_dacl``
    and ``acl_differs_from_parent``. The other five — ``share_root``, ``scan_root``,
    ``parent_unreadable``, ``parent_null_dacl`` and a null DACL of its own — are reported as
    boundaries because unknown must read as a boundary for a *scan* to be safe (ADR-0009), and
    turning any of them into a risk finding would report the collector's reach as the estate's
    permissions.

    ``is_acl_boundary`` alone is therefore never enough, and this rule branches on the reason.
    """

    rule_id = RuleId.BROKEN_INHERITANCE

    def evaluate(self, facts: RiskFacts, config: RuleConfig) -> Iterator[RuleOutcome]:
        include_roots = config.option_bool("include_share_roots")
        include_diverged = config.option_bool("include_diverged")
        for resource in sorted(facts.resources, key=lambda item: item.resource_key):
            if resource.is_share_root and not include_roots:
                continue
            band = self._band(resource, include_diverged=include_diverged)
            if band is None:
                continue
            yield RuleOutcome(
                subject=FindingSubject(resource_key=resource.resource_key),
                band=band,
                evidence=Evidence.of([EvidenceItem.for_resource(resource)]),
                qualifiers=resource.qualifiers,
                detail={
                    "path": resource.path,
                    "boundary_reason": (
                        None if resource.boundary_reason is None else resource.boundary_reason.value
                    ),
                    "dacl_protected": resource.dacl_protected,
                    "rule_version": config.definition.version,
                },
            )

    def _band(self, resource: ResourceFacts, *, include_diverged: bool) -> SeverityBand | None:
        if resource.dacl_protected or (
            resource.boundary_reason is AclBoundaryReason.PROTECTED_DACL
        ):
            return SeverityBand.PROTECTED
        if (
            include_diverged
            and resource.boundary_reason is AclBoundaryReason.ACL_DIFFERS_FROM_PARENT
        ):
            return SeverityBand.DIVERGED
        return None


class BroadAccessOnSensitiveResource(Rule):
    """A broad trustee holds write or control access to a resource somebody marked sensitive.

    **Sensitivity is never inferred.** This rule reads
    :attr:`app.risk_engine.configuration.RiskConfiguration.sensitive_resources` and nothing
    else. It does not look at a folder's name, its share's description, the words in a path,
    or how many people can reach it. With no tags configured it produces nothing, and that
    silence is a configuration state rather than a result — see ADR-0024 and
    :attr:`RiskConfiguration.marks_anything_sensitive`, which exists so a report can say which
    of the two it is.
    """

    rule_id = RuleId.BROAD_ACCESS_ON_SENSITIVE_RESOURCE

    def evaluate(self, facts: RiskFacts, config: RuleConfig) -> Iterator[RuleOutcome]:
        tags = config.sensitive_resources
        if not tags:
            return
        threshold = config.option_category("minimum_category")
        broad_sids = frozenset(config.option_strings("broad_trustee_sids"))
        broad_rids = config.option_ints("broad_trustee_rids")
        for acl in _acls(facts):
            matched = self._tags_for(acl, tags)
            if not matched:
                continue
            yield from self._for_acl(acl, matched, threshold, broad_sids, broad_rids, config)

    def _tags_for(
        self, acl: _Acl, tags: Sequence[SensitiveResource]
    ) -> tuple[SensitiveResource, ...]:
        if acl.resource is not None:
            return tuple(tag for tag in tags if tag.matches_resource(acl.resource))
        assert acl.share is not None  # _acls yields one or the other
        return tuple(tag for tag in tags if tag.matches_share(acl.share))

    def _for_acl(
        self,
        acl: _Acl,
        tags: Sequence[SensitiveResource],
        threshold: RightsCategory,
        broad_sids: frozenset[str],
        broad_rids: Sequence[int],
        config: RuleConfig,
    ) -> Iterator[RuleOutcome]:
        seen: set[str] = set()
        for ace in acl.aces:
            if not ace.is_allow or not ace.applies_to_this_object or ace.trustee_key in seen:
                continue
            if not _is_broad(ace, broad_sids, broad_rids):
                continue
            seen.add(ace.trustee_key)
            evaluation = acl.evaluate_for(ace.trustee_key, ace.trustee_sid)
            if not _reaches(evaluation.rights, threshold):
                continue
            tag = tags[0]
            yield RuleOutcome(
                subject=acl.subject(discriminator=ace.trustee_key),
                band=band_for(evaluation.rights),
                evidence=Evidence.of(
                    [
                        *acl.cite(ace.trustee_key),
                        EvidenceItem.for_configuration(
                            f"sensitive_resource:{tag.label}", tag.selector
                        ),
                    ]
                ),
                qualifiers=acl.qualifiers,
                detail={
                    "trustee_sid": ace.trustee_sid,
                    "sensitivity_label": tag.label,
                    "sensitivity_selector": tag.selector,
                    "layer": acl.layer.value,
                    "rights_label": summarize(evaluation.rights).label,
                    "rule_version": config.definition.version,
                },
            )


def _is_broad(ace: AceFacts, sids: frozenset[str], rids: Sequence[int]) -> bool:
    if ace.trustee_sid in sids:
        return True
    return any(domain_relative_trustee(ace.trustee_sid, rid) for rid in rids)


def _sid_of(facts: RiskFacts, key: str) -> str:
    """The SID for a storage key: the stored one when known, else the key's own tail.

    A local-group key is ``host|sid``, so the tail is the SID even when nothing described the
    principal. Split on the last separator for the reason
    :func:`app.services.graph.split_key` gives: a host name may contain almost anything, and a
    SID may not contain ``|``.
    """
    principal = facts.principal(key)
    if principal is not None:
        return principal.sid
    _, separator, tail = key.rpartition("|")
    return tail if separator else key


# --------------------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------------------


def _build_registry() -> Mapping[RuleId, Rule]:
    implementations: tuple[Rule, ...] = (
        EveryoneBroadAccess(),
        AuthenticatedUsersBroadAccess(),
        DomainUsersBroadAccess(),
        DirectUserAce(),
        DisabledPrincipalRetainsAccess(),
        UnresolvedSidOnAcl(),
        BrokenInheritance(),
        DeepGroupNesting(),
        RedundantAccessPaths(),
        EmptyPermissionBearingGroup(),
        BroadAccessOnSensitiveResource(),
    )
    registry = {rule.rule_id: rule for rule in implementations}
    missing = sorted(rule_id.value for rule_id in CATALOG if rule_id not in registry)
    if missing:
        raise RuntimeError(
            f"The catalog describes rules with no implementation: {missing}. A described "
            "rule that cannot run would appear in the configuration reference and never "
            "produce a finding."
        )
    undescribed = sorted(rule_id.value for rule_id in registry if rule_id not in CATALOG)
    if undescribed:  # pragma: no cover - RuleId is the key type, so this cannot happen
        raise RuntimeError(f"Rules with no catalog entry: {undescribed}.")
    return registry


RULES: Final[Mapping[RuleId, Rule]] = _build_registry()
"""Every rule, keyed by identifier, checked exhaustive against the catalog at import."""


def rule_for(rule_id: RuleId) -> Rule:
    """The implementation of ``rule_id``.

    Raises:
        KeyError: no such rule in this build. A stored finding naming one is a real
            inconsistency and is left to fail rather than silently skipped, because a rule
            that cannot run also cannot resolve the findings it made.
    """
    return RULES[rule_id]
