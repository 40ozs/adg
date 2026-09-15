"""The facts behind a finding, complete enough to re-derive it.

A finding that says *"Everyone has Modify on \\\\FS01\\Finance"* and leaves it there is an
assertion. An auditor cannot check it, a second tool cannot reconcile against it, and nobody
can tell six months later whether it was true when it was written. So every finding here
carries the **complete records** it was computed from, and the engine can take that evidence
alone, rebuild the facts, re-run the one rule that produced the finding, and get the same
finding back (:func:`app.risk_engine.engine.reproduce`).

That property — *evidence sufficiency* — is what the phase's test suite pins, one test per
rule. It is stronger than it sounds, and it is what forbids two shortcuts that are otherwise
very tempting:

* **A rule may not read a fact it does not cite.** If it matched on a Deny entry three
  positions up the DACL, that entry has to be in the evidence, or reproduction produces a
  different answer and the test fails.
* **Evidence may not be a rendering.** ``"Everyone → Modify"`` is a sentence; it cannot be
  rebuilt into an :class:`~app.risk_engine.facts.AceFacts`. Every item here is the record,
  whole, in the record's own vocabulary.

Items are flat on purpose. An ACE is its own item rather than a list nested inside a
resource, so a single JSONB column holds the evidence of every rule without a schema per rule,
and :func:`rebuild_facts` reassembles the containment by key.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.domain.errors import DomainValidationError
from app.risk_engine.facts import (
    AceFacts,
    MembershipFacts,
    PrincipalFacts,
    ResourceFacts,
    RiskFacts,
    RiskScope,
    ShareFacts,
)

__all__ = [
    "EVIDENCE_DIGEST_LENGTH",
    "Evidence",
    "EvidenceItem",
    "EvidenceKind",
    "rebuild_facts",
]

EVIDENCE_DIGEST_LENGTH: Final = 64
"""Characters in an evidence digest: SHA-256, hex. Matches the project's other digests."""


class EvidenceKind(StrEnum):
    """What one evidence item is a record of."""

    RESOURCE = "resource"
    SHARE = "share"
    ACE = "ace"
    PRINCIPAL = "principal"
    MEMBERSHIP = "membership"

    CONFIGURATION = "configuration"
    """A threshold or tag the rule was evaluated under.

    Recorded so a reader can see *why* a rule fired at this estate's settings, and excluded
    from :func:`rebuild_facts`: configuration is supplied to a reproduction explicitly, not
    recovered from the finding, because a finding that carried its own configuration could
    reproduce itself under settings the installation no longer uses.
    """


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One complete record a rule read, named by kind and key."""

    kind: EvidenceKind
    key: str
    attributes: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.key:
            raise DomainValidationError("An evidence item needs a key.", field="key")
        object.__setattr__(self, "attributes", dict(self.attributes))

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.kind.value, self.key)

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "key": self.key, "attributes": dict(self.attributes)}

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> EvidenceItem:
        return cls(
            kind=EvidenceKind(str(payload["kind"])),
            key=str(payload["key"]),
            attributes=dict(payload.get("attributes") or {}),
        )

    # -- constructors, one per fact record -------------------------------------------

    @classmethod
    def for_resource(cls, resource: ResourceFacts) -> EvidenceItem:
        return cls(EvidenceKind.RESOURCE, resource.resource_key, resource.to_evidence())

    @classmethod
    def for_share(cls, share: ShareFacts) -> EvidenceItem:
        return cls(EvidenceKind.SHARE, share.share_key, share.to_evidence())

    @classmethod
    def for_ace(cls, ace: AceFacts) -> EvidenceItem:
        return cls(EvidenceKind.ACE, ace.ace_key, ace.to_evidence())

    @classmethod
    def for_principal(cls, principal: PrincipalFacts) -> EvidenceItem:
        return cls(EvidenceKind.PRINCIPAL, principal.key, principal.to_evidence())

    @classmethod
    def for_membership(cls, membership: MembershipFacts) -> EvidenceItem:
        return cls(EvidenceKind.MEMBERSHIP, membership.group_key, membership.to_evidence())

    @classmethod
    def for_configuration(cls, name: str, value: Any) -> EvidenceItem:
        return cls(EvidenceKind.CONFIGURATION, name, {"value": value})


@dataclass(frozen=True, slots=True)
class Evidence:
    """The complete, ordered set of records behind one finding.

    Ordered canonically — by kind then key — so that two evaluations that read the same facts
    in different orders produce byte-identical evidence and therefore the same
    :attr:`digest`. A digest that changed with iteration order would make every re-evaluation
    look like a change to every finding.
    """

    items: tuple[EvidenceItem, ...] = ()

    @classmethod
    def of(cls, items: Iterable[EvidenceItem]) -> Evidence:
        """Canonical evidence from any iterable, de-duplicated by (kind, key).

        De-duplication keeps the **first** item for a key. A rule that cites the same record
        twice cites the same record; two different records under one key would be a bug in
        the rule, and keeping the first makes that bug deterministic rather than dependent on
        iteration order.
        """
        seen: dict[tuple[str, str], EvidenceItem] = {}
        for item in items:
            seen.setdefault(item.sort_key, item)
        return cls(tuple(seen[key] for key in sorted(seen)))

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def of_kind(self, kind: EvidenceKind) -> tuple[EvidenceItem, ...]:
        return tuple(item for item in self.items if item.kind is kind)

    def keys_of(self, kind: EvidenceKind) -> tuple[str, ...]:
        return tuple(item.key for item in self.items if item.kind is kind)

    def to_json(self) -> list[dict[str, Any]]:
        return [item.to_json() for item in self.items]

    @classmethod
    def from_json(cls, payload: Sequence[Mapping[str, Any]]) -> Evidence:
        return cls.of(EvidenceItem.from_json(entry) for entry in payload)

    @property
    def digest(self) -> str:
        """SHA-256 over the canonical rendering.

        Sorted keys and tight separators, the same canonicalization
        :func:`app.history.model.state_digest` uses, so "did this finding's evidence change"
        is answered the same way "did this object's state change" is.
        """
        canonical = json.dumps(
            self.to_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def rebuild_facts(evidence: Evidence) -> RiskFacts:
    """The fact bundle an evidence set describes, and nothing more.

    The bundle is deliberately **minimal**: it contains the cited records and no others, and
    its scope is :meth:`RiskScope.everything` because a reproduction is a reproduction of one
    rule over one set of records, not an evaluation of an estate. Re-running the rule that
    produced a finding over this bundle is how :func:`app.risk_engine.engine.reproduce` checks
    that the evidence is sufficient.

    Entries are attached to the resource or share they name through ``container_key``. An
    entry whose container is not itself cited is dropped rather than silently attached
    somewhere — which is a failure the reproduction test then reports, because the rule will
    not fire without it.
    """
    aces_by_container: dict[str, list[AceFacts]] = {}
    for item in evidence.of_kind(EvidenceKind.ACE):
        ace = AceFacts.from_evidence(item.attributes)
        aces_by_container.setdefault(ace.container_key, []).append(ace)

    def ordered(container_key: str) -> list[AceFacts]:
        found = aces_by_container.get(container_key, [])
        # Stored order is what the access check honors; an entry with no order index sorts
        # last rather than at zero, so an unordered entry cannot displace an ordered one.
        return sorted(
            found, key=lambda ace: (ace.order_index is None, ace.order_index or 0, ace.ace_key)
        )

    resources = tuple(
        ResourceFacts.from_evidence(item.attributes, ordered(item.key))
        for item in evidence.of_kind(EvidenceKind.RESOURCE)
    )
    shares = tuple(
        ShareFacts.from_evidence(item.attributes, ordered(item.key))
        for item in evidence.of_kind(EvidenceKind.SHARE)
    )
    principals = {
        item.key: PrincipalFacts.from_evidence(item.attributes)
        for item in evidence.of_kind(EvidenceKind.PRINCIPAL)
    }
    memberships = {
        item.key: MembershipFacts.from_evidence(item.attributes)
        for item in evidence.of_kind(EvidenceKind.MEMBERSHIP)
    }
    return RiskFacts(
        resources=resources,
        shares=shares,
        principals=principals,
        memberships=memberships,
        scope=RiskScope.everything(),
    )
