"""The combinatorial case space the effective-access engine is attacked with.

Phase 4B built the engine and tested it with hand-written cases: one test per behavior, each
written by the same person who wrote the behavior. That is necessary and it is not
sufficient — a hand-written suite tests the cases its author thought of, and an access check
is exactly the kind of code whose bugs live in combinations nobody pictured.

This module generates the combinations mechanically, and it does so in a form that a
**second, independent authority** can answer: every ACL case here renders to SDDL, which
means Windows itself can be asked what it grants (``scripts/windows-access-check/``). The
expectations in :mod:`tests.validation.test_windows_oracle` are therefore not this
repository's opinion about Windows — they are Windows' answer, captured from a real host and
committed as a fixture.

Two case spaces, because two different things need attacking:

* :func:`acl_cases` — one DACL, one token, one access check. Verified against Windows.
* :func:`resolution_cases` — the whole of :func:`app.access_engine.resolve_access`: two
  layers, an access path, DACL provenance, and a subject whose description may be missing.
  Windows has no opinion about most of this (it is ADG's own uncertainty accounting), so
  these are held to *invariants* instead — properties that must hold for every case, which a
  wrong answer cannot satisfy by accident.

Both generators are **deterministic and ordered**: the same case list, in the same order,
on every machine and every run. The Windows oracle is a committed fixture keyed by case id,
and a fixture that cannot be matched to a case is a hard failure rather than a skip.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Final

from app.access_engine import (
    AccessPath,
    AclEntry,
    AclProvenance,
    ResourceDacl,
    ShareDacl,
    SidOrigin,
    SubjectFacts,
    SubjectToken,
    TokenAssumption,
    TokenSid,
    build_token,
    ntfs_entry,
    share_entry,
)
from app.domain import AceFlag, AceSource, AceType, PrincipalKind, SharePermission

__all__ = [
    "ACL_CASE_COUNT",
    "MASKS",
    "RESOLUTION_CASE_COUNT",
    "TRUSTEES",
    "AceSpec",
    "AclCase",
    "ResolutionCase",
    "acl_cases",
    "acl_cases_by_id",
    "resolution_cases",
]


# --------------------------------------------------------------------------------------
# The estate the matrix is written against
# --------------------------------------------------------------------------------------

DOMAIN: Final = "S-1-5-21-1004336348-1177238915-682003330"
"""The same domain the canonical Phase 0B scenarios use, so a case id read in a matrix
failure and a case id read in a scenario failure name the same fictional estate."""

SUBJECT: Final = f"{DOMAIN}-1104"
GROUP: Final = f"{DOMAIN}-1201"
NESTED: Final = f"{DOMAIN}-1202"
OUTSIDER: Final = f"{DOMAIN}-1301"
EVERYONE: Final = "S-1-1-0"
AUTHENTICATED_USERS: Final = "S-1-5-11"
NETWORK: Final = "S-1-5-2"
CREATOR_OWNER: Final = "S-1-3-0"
OWNER_RIGHTS: Final = "S-1-3-4"
SYSTEM: Final = "S-1-5-18"

TRUSTEES: Final[dict[str, str]] = {
    "direct": SUBJECT,
    "group": GROUP,
    "nested": NESTED,
    "outsider": OUTSIDER,
    "everyone": EVERYONE,
    "creator_owner": CREATOR_OWNER,
    "owner_rights": OWNER_RIGHTS,
    "network": NETWORK,
    "system": SYSTEM,
}

IN_TOKEN: Final[frozenset[str]] = frozenset({"direct", "group", "nested", "everyone"})
"""Which trustee names the generated token actually carries.

``outsider`` is deliberately absent: an ACE naming it must never contribute. ``network`` is
absent too — the logon-session SIDs are what :class:`TokenAssumption` governs, and the matrix
builds its tokens with :attr:`TokenAssumption.SIDS_ONLY` so that every SID in the token is
one this module put there on purpose."""


# --------------------------------------------------------------------------------------
# Rights subsets
# --------------------------------------------------------------------------------------

MASKS: Final[dict[str, int]] = {
    "read_execute": 0x001200A9,
    "modify": 0x001301BF,
    "full": 0x001F01FF,
    "write_only": 0x00000116,
    "traverse": 0x00000020,
    "read_control": 0x00020000,
    "write_dac": 0x00040000,
    "take_ownership": 0x000A0000,
    "delete": 0x00010000,
    "generic_read": 0x80000000,
    "generic_all": 0x10000000,
    "generic_read_write": 0xC0000000,
    "undefined_bit": 0x00000800,
}
"""The rights subsets the matrix draws from.

Chosen to cover the required dimensions rather than to be exhaustive: the three SMB levels
(``read_execute``/``modify``/``full``), custom NTFS subsets that no display category names
(``write_only``, ``traverse``, ``delete``), the two escalation rights, all four generic
forms, and one bit the file system does not define — which Windows grants verbatim and which
the engine must retain rather than quietly drop."""

MASK_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("modify", "write_only"),
    ("full", "read_execute"),
    ("read_execute", "modify"),
    ("generic_all", "write_dac"),
    ("generic_read_write", "traverse"),
    ("undefined_bit", "delete"),
    ("write_dac", "full"),
    ("take_ownership", "read_control"),
    ("generic_read", "full"),
)
"""``(a, b)`` mask pairs fed to the two-entry templates.

Deliberately asymmetric — a template that denies ``b`` what it allowed of ``a`` exercises a
different path when ``b`` is a subset of ``a`` than when the two overlap partially, and both
appear here."""

OWNERS: Final[tuple[str, ...]] = ("system", "direct", "outsider")
"""Who owns the object in the generated cases.

Always a real SID and never ``None``. Windows' ``AccessCheck`` refuses a descriptor whose
owner is absent -- a file system object always has one -- so an unowned descriptor is not a
state the oracle can be asked about. ADG still models ``owner_sid=None``, because a collector
may not have read the owner, and the degenerate set below covers that as an ADG-only case."""

TRUSTEE_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("direct", "group"),
    ("group", "nested"),
    ("direct", "outsider"),
    ("nested", "outsider"),
    ("everyone", "direct"),
    ("creator_owner", "direct"),
    ("owner_rights", "group"),
    ("network", "direct"),
)


# --------------------------------------------------------------------------------------
# One case
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AceSpec:
    """One ACE in a generated DACL, in the form both Python and SDDL can render."""

    ace_type: AceType
    trustee: str
    """A key of :data:`TRUSTEES`, not a SID: the case id stays readable."""

    mask: int
    flags: AceFlag = AceFlag.NONE
    inherited: bool = False

    @property
    def sid(self) -> str:
        return TRUSTEES[self.trustee]

    @property
    def effective_flags(self) -> AceFlag:
        return self.flags | (AceFlag.INHERITED if self.inherited else AceFlag.NONE)

    def to_entry(self, order: int) -> AclEntry:
        return ntfs_entry(
            trustee_key=self.sid,
            trustee_sid=self.sid,
            ace_type=self.ace_type,
            access_mask=self.mask,
            flags=self.effective_flags,
            source=AceSource.INHERITED if self.inherited else AceSource.EXPLICIT,
            order_index=order,
        )

    def to_sddl(self) -> str:
        """The ACE as SDDL, so Windows can be asked about the same entry.

        Rights are emitted as hex rather than as an SDDL alias: the aliases are shorthands
        for masks the matrix chooses deliberately, and a round trip through a name is a
        chance to change the value being tested.
        """
        kind = "A" if self.ace_type is AceType.ALLOW else "D"
        return f"({kind};{_sddl_flags(self.effective_flags)};0x{self.mask:08x};;;{self.sid})"


def _sddl_flags(flags: AceFlag) -> str:
    parts = []
    if flags & AceFlag.CONTAINER_INHERIT:
        parts.append("CI")
    if flags & AceFlag.OBJECT_INHERIT:
        parts.append("OI")
    if flags & AceFlag.NO_PROPAGATE_INHERIT:
        parts.append("NP")
    if flags & AceFlag.INHERIT_ONLY:
        parts.append("IO")
    if flags & AceFlag.INHERITED:
        parts.append("ID")
    return "".join(parts)


@dataclass(frozen=True, slots=True)
class AclCase:
    """One DACL, one token, one access check — the unit Windows can be asked about."""

    case_id: str
    aces: tuple[AceSpec, ...]
    token: tuple[str, ...]
    """Trustee names, subject first. Resolved against :data:`TRUSTEES`."""

    owner: str | None = None
    """A trustee name, or ``None`` for a descriptor with no owner."""

    dacl_present: bool = True
    dacl_protected: bool = False

    @property
    def subject_sid(self) -> str:
        return TRUSTEES[self.token[0]]

    @property
    def token_sids(self) -> tuple[str, ...]:
        return tuple(TRUSTEES[name] for name in self.token)

    @property
    def owner_sid(self) -> str | None:
        return None if self.owner is None else TRUSTEES[self.owner]

    def entries(self) -> tuple[AclEntry, ...]:
        return tuple(ace.to_entry(order) for order, ace in enumerate(self.aces))

    def resource(self, key: str = "fs01|\\\\fs01\\finance") -> ResourceDacl:
        return ResourceDacl(
            resource_key=key,
            entries=() if not self.dacl_present else self.entries(),
            dacl_present=self.dacl_present,
            dacl_protected=self.dacl_protected,
            owner_sid=self.owner_sid,
        )

    def subject_token(self, path: AccessPath = AccessPath.REMOTE_SMB) -> SubjectToken:
        """A token carrying **exactly** the SIDs the case declares.

        :attr:`TokenAssumption.SIDS_ONLY` is not the production assumption, and that is the
        point: the Windows oracle is handed this same SID list, so any well-known SID the
        engine added on its own would make the two sides disagree about the token rather
        than about the access check. Whether ADG's production assumptions match the SIDs
        Windows really puts in a token is a separate question, asked separately.
        """
        facts = SubjectFacts(key=self.subject_sid, sid=self.subject_sid, kind=PrincipalKind.USER)
        groups = [
            TokenSid(key=sid, sid=sid, origin=SidOrigin.GROUP_MEMBERSHIP, depth=index)
            for index, sid in enumerate(self.token_sids[1:], start=1)
        ]
        return build_token(facts, groups, access_path=path, assumption=TokenAssumption.SIDS_ONLY)

    def to_sddl(self) -> str:
        """The whole descriptor as SDDL.

        A NULL DACL is ``NO_ACCESS_CONTROL`` rather than an absent ``D:``, because the two
        are not the same to the SDDL parser and only the first produces the descriptor whose
        meaning is *grant everything*.
        """
        owner = "" if self.owner_sid is None else f"O:{self.owner_sid}"
        if not self.dacl_present:
            return f"{owner}D:NO_ACCESS_CONTROL"
        control = "P" if self.dacl_protected else ""
        body = "".join(ace.to_sddl() for ace in self.aces)
        return f"{owner}D:{control}{body}"

    def fingerprint(self) -> str:
        """A hash of everything that changes the answer.

        The Windows oracle is a committed fixture. If a case's inputs are edited without the
        fixture being regenerated, the stored answer is an answer to a different question —
        so the fixture stores this, and the comparison refuses to run against a mismatch
        rather than reporting a pass or a failure it cannot justify.
        """
        payload = json.dumps(
            {
                "sddl": self.to_sddl(),
                "token": list(self.token_sids),
                "dacl_present": self.dacl_present,
                "dacl_protected": self.dacl_protected,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def as_oracle_request(self) -> dict[str, object]:
        """The case as the Windows probe consumes it."""
        return {
            "case_id": self.case_id,
            "fingerprint": self.fingerprint(),
            "sddl": self.to_sddl(),
            "subject_sid": self.subject_sid,
            "token_sids": list(self.token_sids),
        }


# --------------------------------------------------------------------------------------
# ACL templates
# --------------------------------------------------------------------------------------

_ALLOW: Final = AceType.ALLOW
_DENY: Final = AceType.DENY
_CI_OI: Final = AceFlag.CONTAINER_INHERIT | AceFlag.OBJECT_INHERIT


def _templates(
    a: str, b: str, mask_a: int, mask_b: int
) -> Iterator[tuple[str, tuple[AceSpec, ...]]]:
    """Every ACE sequence the matrix builds from one trustee pair and one mask pair.

    The names are the vocabulary a failure is reported in, so they describe the *shape*
    rather than the expected outcome: ``allow_then_deny`` says where the entries sit, and
    what that produces is precisely what is under test.
    """
    yield "allow", (AceSpec(_ALLOW, a, mask_a),)
    yield "deny", (AceSpec(_DENY, a, mask_a),)
    yield "deny_then_allow", (AceSpec(_DENY, a, mask_b), AceSpec(_ALLOW, a, mask_a))
    yield "allow_then_deny", (AceSpec(_ALLOW, a, mask_a), AceSpec(_DENY, a, mask_b))
    yield "deny_b_then_allow_a", (AceSpec(_DENY, b, mask_b), AceSpec(_ALLOW, a, mask_a))
    yield "allow_a_then_deny_b", (AceSpec(_ALLOW, a, mask_a), AceSpec(_DENY, b, mask_b))
    yield "allow_a_then_allow_b", (AceSpec(_ALLOW, a, mask_a), AceSpec(_ALLOW, b, mask_b))
    yield "deny_a_then_deny_b", (AceSpec(_DENY, a, mask_a), AceSpec(_DENY, b, mask_b))
    yield "inherit_only_allow", (AceSpec(_ALLOW, a, mask_a, flags=AceFlag.INHERIT_ONLY | _CI_OI),)
    yield "inherited_allow", (AceSpec(_ALLOW, a, mask_a, inherited=True),)
    yield (
        "inherited_deny_then_explicit_allow",
        (
            AceSpec(_DENY, b, mask_b, inherited=True),
            AceSpec(_ALLOW, a, mask_a),
        ),
    )
    yield (
        "explicit_allow_then_inherited_deny",
        (
            AceSpec(_ALLOW, a, mask_a),
            AceSpec(_DENY, b, mask_b, inherited=True),
        ),
    )
    yield "propagating_allow", (AceSpec(_ALLOW, a, mask_a, flags=_CI_OI),)


def acl_cases() -> tuple[AclCase, ...]:
    """The ACL-evaluation case space, in a fixed order.

    Built as a product rather than by hand, and the product is over the dimensions the phase
    names: which trustee the grant arrives through, what it grants, where the entries sit
    relative to each other, whether they were set here or inherited, whether the DACL is
    present/protected/empty/absent, and who owns the object.
    """
    cases: list[AclCase] = []

    for trustee_a, trustee_b in TRUSTEE_PAIRS:
        for mask_a_name, mask_b_name in MASK_PAIRS:
            mask_a = MASKS[mask_a_name]
            mask_b = MASKS[mask_b_name]
            for shape, aces in _templates(trustee_a, trustee_b, mask_a, mask_b):
                for protected in (False, True):
                    for owner in OWNERS:
                        guard = "protected" if protected else "open"
                        owner_name = owner or "unowned"
                        case_id = (
                            f"acl/{trustee_a}-{trustee_b}/{mask_a_name}-{mask_b_name}"
                            f"/{shape}/{guard}/{owner_name}"
                        )
                        cases.append(
                            AclCase(
                                case_id=case_id,
                                aces=aces,
                                token=_token_for(trustee_a, trustee_b),
                                owner=owner,
                                dacl_protected=protected,
                            )
                        )

    cases.extend(_degenerate_cases())
    _assert_unique(cases)
    return tuple(cases)


def _token_for(trustee_a: str, trustee_b: str) -> tuple[str, ...]:
    """The token for a case, always subject-first and never carrying the outsider.

    A trustee that is in :data:`IN_TOKEN` joins the token; one that is not stays out, which
    is what makes ``outsider`` an ACE that must contribute nothing and ``creator_owner`` an
    ACE that must contribute nothing *even when the subject owns the object*.
    """
    names = ["direct"]
    for name in (trustee_a, trustee_b):
        if name in IN_TOKEN and name not in names:
            names.append(name)
    return tuple(names)


def _degenerate_cases() -> list[AclCase]:
    """DACL states that are not a list of entries, and the owner cases around them.

    A NULL DACL and an empty DACL look identical in every ACL viewer and mean opposite
    things, which is exactly why they are generated rather than left to a hand-written pair.
    """
    cases: list[AclCase] = []
    for owner in (None, "direct", "outsider"):
        owner_name = owner or "unowned"
        cases.append(
            AclCase(
                case_id=f"acl/degenerate/null-dacl/{owner_name}",
                aces=(),
                token=("direct", "group"),
                owner=owner,
                dacl_present=False,
            )
        )
        for protected in (False, True):
            guard = "protected" if protected else "open"
            cases.append(
                AclCase(
                    case_id=f"acl/degenerate/empty-dacl/{guard}/{owner_name}",
                    aces=(),
                    token=("direct", "group"),
                    owner=owner,
                    dacl_protected=protected,
                )
            )
    # An OWNER RIGHTS entry is the one ACE that replaces the owner's implicit rights rather
    # than adding to them, so it needs the owner varied underneath it independently.
    for owner in ("direct", "outsider"):
        for mask_name in ("read_control", "read_execute", "full"):
            cases.append(
                AclCase(
                    case_id=f"acl/owner-rights/{owner}/{mask_name}",
                    aces=(AceSpec(_ALLOW, "owner_rights", MASKS[mask_name]),),
                    token=("direct",),
                    owner=owner,
                )
            )
    return cases


def _assert_unique(cases: Sequence[AclCase]) -> None:
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise AssertionError(f"Duplicate matrix case id: {case.case_id}")
        seen.add(case.case_id)


def acl_cases_by_id() -> dict[str, AclCase]:
    return {case.case_id: case for case in acl_cases()}


ACL_CASE_COUNT: Final = len(acl_cases())


# --------------------------------------------------------------------------------------
# The resolution matrix
# --------------------------------------------------------------------------------------

SHARE_LEVELS: Final[dict[str, SharePermission | None]] = {
    "share_read": SharePermission.READ,
    "share_change": SharePermission.CHANGE,
    "share_full": SharePermission.FULL,
    "share_none": None,
}
"""``None`` is a share ACL that was read and names nobody the subject matches — which is a
different answer from one that was never read, and the pair is the point."""

SUBJECT_STATES: Final[tuple[tuple[str, PrincipalKind | None], ...]] = (
    ("user", PrincipalKind.USER),
    ("group", PrincipalKind.DOMAIN_GROUP),
    ("unresolved", PrincipalKind.UNRESOLVED),
    ("undescribed", None),
)


@dataclass(frozen=True, slots=True)
class ResolutionCase:
    """One whole-resolver case: both layers, an access path, and a subject's description."""

    case_id: str
    acl: AclCase
    path: AccessPath
    share: str | None
    """A key of :data:`SHARE_LEVELS`, ``"unobserved"``, or ``None`` for a local path."""

    provenance: AclProvenance = AclProvenance.OBSERVED
    subject_state: str = "user"
    membership_complete: bool = True
    assumption: TokenAssumption = TokenAssumption.SIDS_ONLY
    derived_distance: int | None = None

    def subject_token(self) -> SubjectToken:
        kind = dict(SUBJECT_STATES)[self.subject_state]
        sid = self.acl.subject_sid
        facts = SubjectFacts(key=sid, sid=sid, kind=kind)
        groups = [
            TokenSid(key=sid, sid=sid, origin=SidOrigin.GROUP_MEMBERSHIP, depth=index)
            for index, sid in enumerate(self.acl.token_sids[1:], start=1)
        ]
        return build_token(
            facts,
            groups,
            access_path=self.path,
            assumption=self.assumption,
            membership_complete=self.membership_complete,
        )

    def resource(self) -> ResourceDacl:
        key = "fs01|\\\\fs01\\finance\\reports"
        if self.provenance is AclProvenance.OBSERVED:
            return self.acl.resource(key)
        if self.provenance is AclProvenance.UNOBSERVED:
            return ResourceDacl(resource_key=key, entries=(), provenance=AclProvenance.UNOBSERVED)
        return ResourceDacl(
            resource_key=key,
            entries=self.acl.entries() if self.acl.dacl_present else (),
            dacl_present=self.acl.dacl_present,
            dacl_protected=self.acl.dacl_protected,
            owner_sid=self.acl.owner_sid,
            provenance=AclProvenance.DERIVED,
            derived_from="fs01|\\\\fs01\\finance",
            derived_distance=self.derived_distance or 1,
        )

    def share_dacl(self) -> ShareDacl | None:
        if self.path is AccessPath.LOCAL:
            return None
        key = "fs01|finance"
        if self.share == "unobserved":
            return ShareDacl(share_key=key, observed=False)
        level = SHARE_LEVELS[self.share or "share_none"]
        if level is None:
            return ShareDacl(share_key=key, entries=(), observed=True)
        entry = share_entry(
            trustee_key=self.acl.token_sids[0],
            trustee_sid=self.acl.token_sids[0],
            ace_type=AceType.ALLOW,
            permission=level,
            order_index=0,
        )
        return ShareDacl(share_key=key, entries=(entry,), observed=True)


def resolution_cases() -> tuple[ResolutionCase, ...]:
    """The whole-resolver case space, in a fixed order.

    The ACL half is drawn from :func:`acl_cases` rather than rebuilt, so a resolution failure
    can always be traced back to the ACL case whose Windows answer is already known — which
    is what makes it possible to say whether a wrong result came from the access check or
    from the layer crossing above it.
    """
    acl_pool = _resolution_acl_pool()
    cases: list[ResolutionCase] = []

    for acl in acl_pool:
        for share_name in ("share_read", "share_change", "share_full", "share_none", "unobserved"):
            cases.append(
                ResolutionCase(
                    case_id=f"resolve/remote/{share_name}/{acl.case_id}",
                    acl=acl,
                    path=AccessPath.REMOTE_SMB,
                    share=share_name,
                )
            )
        cases.append(
            ResolutionCase(
                case_id=f"resolve/local/{acl.case_id}",
                acl=acl,
                path=AccessPath.LOCAL,
                share=None,
            )
        )

    # Provenance, subject description and membership completeness vary independently of the
    # ACL shape: they change what the answer is *qualified by*, not what the DACL says.
    for acl in acl_pool[:12]:
        for provenance in (AclProvenance.DERIVED, AclProvenance.UNOBSERVED):
            for distance in (1, 3):
                if provenance is AclProvenance.UNOBSERVED and distance != 1:
                    continue
                cases.append(
                    ResolutionCase(
                        case_id=f"resolve/provenance/{provenance.value}-{distance}/{acl.case_id}",
                        acl=acl,
                        path=AccessPath.REMOTE_SMB,
                        share="share_full",
                        provenance=provenance,
                        derived_distance=distance,
                    )
                )
        for state, _ in SUBJECT_STATES:
            cases.append(
                ResolutionCase(
                    case_id=f"resolve/subject/{state}/{acl.case_id}",
                    acl=acl,
                    path=AccessPath.REMOTE_SMB,
                    share="share_full",
                    subject_state=state,
                )
            )
        cases.append(
            ResolutionCase(
                case_id=f"resolve/truncated-membership/{acl.case_id}",
                acl=acl,
                path=AccessPath.REMOTE_SMB,
                share="share_full",
                membership_complete=False,
            )
        )
        for assumption in (TokenAssumption.AUTHENTICATED_USER, TokenAssumption.ANONYMOUS):
            cases.append(
                ResolutionCase(
                    case_id=f"resolve/assumption/{assumption.value}/{acl.case_id}",
                    acl=acl,
                    path=AccessPath.REMOTE_SMB,
                    share="share_full",
                    assumption=assumption,
                )
            )

    ids = {case.case_id for case in cases}
    if len(ids) != len(cases):
        raise AssertionError("Duplicate resolution case id")
    return tuple(cases)


def _resolution_acl_pool() -> tuple[AclCase, ...]:
    """A slice of the ACL space wide enough to exercise every crossing path.

    The full ACL product crossed with the resolver's own dimensions would be hundreds of
    thousands of cases whose extra cost buys nothing: the crossing does not care which of
    thirteen masks produced the NTFS rights, only what they are. So the pool is taken as a
    deterministic stride through the ordered case list, plus every degenerate DACL state,
    which the crossing very much does care about.
    """
    everything = acl_cases()
    strided = everything[::97]
    degenerate = tuple(case for case in everything if "/degenerate/" in case.case_id)
    owner_rights = tuple(case for case in everything if "/owner-rights/" in case.case_id)
    seen: dict[str, AclCase] = {}
    for case in strided + degenerate + owner_rights:
        seen.setdefault(case.case_id, case)
    return tuple(seen.values())


RESOLUTION_CASE_COUNT: Final = len(resolution_cases())


# --------------------------------------------------------------------------------------
# Emitting the case space for the Windows probe
# --------------------------------------------------------------------------------------


def emit(path: str) -> int:
    """Write the ACL case space where ``scripts/windows-access-check`` can read it."""
    payload = {
        "schema": "adg.access-matrix/1",
        "case_count": ACL_CASE_COUNT,
        "cases": [case.as_oracle_request() for case in acl_cases()],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    return ACL_CASE_COUNT


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Emit the ADG effective-access case matrix.")
    parser.add_argument("--emit", required=True, help="Path to write the case JSON to.")
    arguments = parser.parse_args()
    count = emit(arguments.emit)
    print(f"{count} cases written to {arguments.emit}")


if __name__ == "__main__":
    _main()
