"""Build the adversarial AD-graph transcripts under ``ad_graph/``.

The canonical scenarios under ``scenarios/`` pin *semantics*: one small, hand-maintained
transcript per access shape. This set pins *correctness under stress* — the graphs a real
directory produces when it has been alive for a decade: nesting twenty-four deep, groups
that contain each other, three people called Jordan Rivera, a SID nobody can resolve any
more, a group with five hundred members, and a BUILTIN SID that means a different group on
every computer it appears on.

Those transcripts are too large and too repetitive to maintain by hand, so this module
builds them through the contract models — every ``source_key`` is derived by
:mod:`app.contracts.v1.keys`, so a fixture is valid by construction rather than by review.
The JSON it writes is the artifact the tests load; ``test_ad_graph_fixtures.py`` rebuilds it
in memory and compares, so the two can never drift.

Regenerate with::

    python -m tests.fixtures.build_ad_graph            # from backend\\
    python -m tests.fixtures.build_ad_graph --check    # verify without writing

Every SID here is synthetic. Two synthetic domains appear, because half of these hazards
only exist when more than one authority is in the picture.
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import sys
from collections.abc import Iterable, Sequence
from typing import Any

from app.contracts.v1 import keys
from app.contracts.v1.common import (
    GroupScope,
    GroupType,
    MembershipEdgeKind,
    PrincipalKind,
    UnresolvedReason,
)
from app.contracts.v1.envelopes import ObservationBatch, ScanRunCompletion, ScanRunStart
from app.contracts.v1.observations import MembershipObservation, PrincipalObservation
from app.domain import Sid
from tests.fixtures import AD_GRAPH_DIR

CORP = "S-1-5-21-2000000001-2000000002-2000000003"
"""The domain under audit."""

PARTNER = "S-1-5-21-3000000001-3000000002-3000000003"
"""A trusted forest. Its principals appear here only as SIDs."""

CORP_DN = "DC=corp,DC=example,DC=com"
BASE_TIME = dt.datetime(2026, 9, 14, 9, 0, 0, tzinfo=dt.UTC)

MAX_OBSERVATIONS_PER_BATCH = 500
"""Well under the contract's 1,000, so a large fixture spans batches like a real run."""


def corp(rid: int) -> str:
    return f"{CORP}-{rid}"


def partner(rid: int) -> str:
    return f"{PARTNER}-{rid}"


class Transcript:
    """One scan run under construction: observations in, a JSON document out."""

    def __init__(self, name: str, title: str, description: str, run_suffix: str) -> None:
        self.name = name
        self.title = title
        self.description = description
        self.run_id = f"00000000-0000-4000-8000-0000{run_suffix}"
        self.expectations: dict[str, Any] = {}
        self._observations: list[PrincipalObservation | MembershipObservation] = []

    # ----------------------------------------------------------------- observations

    def _next_time(self) -> str:
        moment = BASE_TIME + dt.timedelta(seconds=len(self._observations) + 1)
        return moment.isoformat().replace("+00:00", "Z")

    def principal(
        self,
        sid: str,
        principal_kind: PrincipalKind,
        *,
        host_key: str | None = None,
        **fields: Any,
    ) -> str:
        """Record a principal observation and return its storage key."""
        source_key = keys.principal_key(Sid(sid), principal_kind, host_key)
        observation = PrincipalObservation(
            run_id=self.run_id,
            observed_at=self._next_time(),  # type: ignore[arg-type]
            source_key=source_key,
            sid=sid,
            principal_kind=principal_kind,
            host_key=host_key,
            **fields,
        )
        self._observations.append(observation)
        return _storage_key(observation)

    def user(self, sid: str, name: str, sam: str, **fields: Any) -> str:
        return self.principal(
            sid,
            PrincipalKind.USER,
            display_name=name,
            sam_account_name=sam,
            user_principal_name=f"{sam}@corp.example.com",
            distinguished_name=f"CN={name},OU=Users,{CORP_DN}",
            enabled=fields.pop("enabled", True),
            **fields,
        )

    def group(
        self,
        sid: str,
        name: str,
        *,
        scope: GroupScope = GroupScope.UNIVERSAL,
        group_type: GroupType = GroupType.SECURITY,
        **fields: Any,
    ) -> str:
        return self.principal(
            sid,
            PrincipalKind.DOMAIN_GROUP,
            display_name=name,
            sam_account_name=name,
            distinguished_name=f"CN={name},OU=Groups,{CORP_DN}",
            group_scope=scope,
            group_type=group_type,
            **fields,
        )

    def edge(
        self,
        group_sid: str,
        member_sid: str,
        *,
        edge_kind: MembershipEdgeKind = MembershipEdgeKind.DIRECTORY_GROUP_MEMBER,
        host_key: str | None = None,
        member_kind: PrincipalKind | None = None,
        foreign: bool = False,
    ) -> str:
        """Record a membership observation and return the edge's storage key."""
        source_key = keys.membership_key(Sid(group_sid), Sid(member_sid), edge_kind, host_key)
        observation = MembershipObservation(
            run_id=self.run_id,
            observed_at=self._next_time(),  # type: ignore[arg-type]
            source_key=source_key,
            group_sid=group_sid,
            member_sid=member_sid,
            edge_kind=edge_kind,
            host_key=host_key,
            member_kind=member_kind,
            is_foreign_security_principal=foreign,
        )
        self._observations.append(observation)
        return observation.to_domain().identity_key

    def chain(self, keys_in_order: Sequence[str], member_kind: PrincipalKind) -> None:
        """``keys_in_order[i]`` is a member of ``keys_in_order[i + 1]``."""
        for lower, upper in itertools.pairwise(keys_in_order):
            self.edge(upper, lower, member_kind=member_kind)

    # --------------------------------------------------------------------- document

    def document(self) -> dict[str, Any]:
        batches = list(self._batches())
        start = ScanRunStart(
            run_id=self.run_id,
            source={  # type: ignore[arg-type]
                "collector": "active_directory",
                "collector_host": "COLLECTOR01",
                "method": "System.DirectoryServices.Protocols",
                "collector_version": "0.1.0",
                "target": "corp.example.com",
            },
            started_at=BASE_TIME,
            scopes=[{"kind": "domain", "key": CORP}],  # type: ignore[list-item]
            incremental=False,
        )
        completion = ScanRunCompletion(
            run_id=self.run_id,
            status="succeeded",
            completed_at=BASE_TIME + dt.timedelta(minutes=10),
            batch_count=len(batches),
            observation_count=len(self._observations),
            error_count=0,
            errors=[],
            reconciled_scopes=[{"kind": "domain", "key": CORP}],  # type: ignore[list-item]
        )
        return {
            "scenario": self.name,
            "title": self.title,
            "description": self.description,
            "expectations": self.expectations,
            "start": _dump(start),
            "batches": [_dump(batch) for batch in batches],
            "completion": _dump(completion),
        }

    def _batches(self) -> Iterable[ObservationBatch]:
        chunks = [
            self._observations[start : start + MAX_OBSERVATIONS_PER_BATCH]
            for start in range(0, len(self._observations), MAX_OBSERVATIONS_PER_BATCH)
        ]
        for index, chunk in enumerate(chunks, start=1):
            yield ObservationBatch(
                run_id=self.run_id,
                batch_id=f"00000000-0000-4000-8000-1000{index:08x}",
                sequence=index,
                is_final=index == len(chunks),
                observations=list(chunk),
            )


def _storage_key(observation: PrincipalObservation) -> str:
    return observation.to_domain().identity_key


def _dump(model: Any) -> dict[str, Any]:
    """Serialize a contract model the way a collector would put it on the wire.

    Null fields are omitted rather than written, matching the canonical scenarios: every
    optional field defaults to ``None``, so an absent key and an explicit null mean the same
    thing to the models, and omitting them keeps a 512-member fixture readable.
    """
    return json.loads(model.model_dump_json(exclude_none=True))  # type: ignore[no-any-return]


# ------------------------------------------------------------------ the transcripts


def deep_nesting() -> Transcript:
    depth = 24
    transcript = Transcript(
        "a01-deep-nesting",
        "Twenty-four levels of group nesting",
        "One user at the bottom of a 24-link chain of groups. Deeper than any sane "
        "directory and shallower than the default depth limit, so the complete answer and "
        "the truncated one are both reachable from the same fixture.",
        "00000a01",
    )
    user = transcript.user(corp(2001), "Nadia Osei", "nosei")
    ladder = [
        transcript.group(corp(2100 + level), f"Nest-L{level:02d}") for level in range(1, depth + 1)
    ]
    transcript.edge(corp(2101), corp(2001), member_kind=PrincipalKind.USER)
    transcript.chain(
        [corp(2100 + level) for level in range(1, depth + 1)], PrincipalKind.DOMAIN_GROUP
    )
    transcript.expectations = {
        "top_group_key": ladder[-1],
        "bottom_user_key": user,
        "nesting_depth": depth,
        "user_depth_from_top": depth,
        "complete_at_default_depth": True,
        "truncates_at_max_depth": 10,
        "note": (
            "Expanding the top group must reach the user at depth 24 and report the chain "
            "as the explanation. A depth limit below 24 must report max_depth truncation "
            "rather than a short member list."
        ),
    }
    return transcript


def cycles() -> Transcript:
    transcript = Transcript(
        "a02-cycles",
        "Two independent membership cycles",
        "A two-group ring and a three-group ring, reached one after the other from a "
        "single top group, with a user beyond both. Expansion must terminate, must reach "
        "the user, must report both cycles, and must still call itself complete: a cycle "
        "is a finding, not a truncation.",
        "00000a02",
    )
    top = transcript.group(corp(2200), "Cycle-Top")
    ring_a = transcript.group(corp(2210), "Ring-A")
    ring_b = transcript.group(corp(2211), "Ring-B")
    loop_1 = transcript.group(corp(2220), "Loop-1")
    loop_2 = transcript.group(corp(2221), "Loop-2")
    loop_3 = transcript.group(corp(2222), "Loop-3")
    user = transcript.user(corp(2230), "Priya Raman", "praman")

    transcript.edge(corp(2200), corp(2210), member_kind=PrincipalKind.DOMAIN_GROUP)
    # Ring-A contains Ring-B contains Ring-A.
    transcript.edge(corp(2210), corp(2211), member_kind=PrincipalKind.DOMAIN_GROUP)
    transcript.edge(corp(2211), corp(2210), member_kind=PrincipalKind.DOMAIN_GROUP)
    # Ring-B also contains Loop-3, which opens the second ring.
    transcript.edge(corp(2211), corp(2222), member_kind=PrincipalKind.DOMAIN_GROUP)
    transcript.edge(corp(2222), corp(2221), member_kind=PrincipalKind.DOMAIN_GROUP)
    transcript.edge(corp(2221), corp(2220), member_kind=PrincipalKind.DOMAIN_GROUP)
    transcript.edge(corp(2220), corp(2222), member_kind=PrincipalKind.DOMAIN_GROUP)
    transcript.edge(corp(2220), corp(2230), member_kind=PrincipalKind.USER)

    transcript.expectations = {
        "top_group_key": top,
        "user_key": user,
        "cycle_count": 2,
        "cycle_members": [sorted([ring_a, ring_b]), sorted([loop_1, loop_2, loop_3])],
        "expansion_is_complete": True,
        "effective_non_group_members": [user],
        "note": (
            "A cyclic graph still yields a complete answer. The cycles are reported as "
            "strongly connected components with a concrete loop to break."
        ),
    }
    return transcript


def duplicate_names() -> Transcript:
    transcript = Transcript(
        "a03-duplicate-names",
        "Three principals, one name",
        "Two users in one domain share a display name; a third in a trusted forest shares "
        "both the display name and the sAMAccountName. Two groups in different domains "
        "share a name as well. Nothing here may merge: names are metadata, SIDs are "
        "identity.",
        "00000a03",
    )
    first = transcript.user(corp(2301), "Jordan Rivera", "jrivera")
    second = transcript.user(corp(2302), "Jordan Rivera", "jrivera2")
    # Same display name *and* same sAMAccountName as `first`, issued by another domain.
    third = transcript.principal(
        partner(2301),
        PrincipalKind.USER,
        display_name="Jordan Rivera",
        sam_account_name="jrivera",
        enabled=True,
    )
    corp_finance = transcript.group(corp(2310), "Finance")
    partner_finance = transcript.principal(
        partner(2310),
        PrincipalKind.DOMAIN_GROUP,
        display_name="Finance",
        sam_account_name="Finance",
        group_scope=GroupScope.UNIVERSAL,
        group_type=GroupType.SECURITY,
    )

    transcript.edge(corp(2310), corp(2301), member_kind=PrincipalKind.USER)
    transcript.edge(corp(2310), corp(2302), member_kind=PrincipalKind.USER)
    transcript.edge(partner(2310), partner(2301), member_kind=PrincipalKind.USER)

    transcript.expectations = {
        "distinct_principal_keys": sorted([first, second, third, corp_finance, partner_finance]),
        "shared_display_name": "Jordan Rivera",
        "shared_sam_account_name": "jrivera",
        "corp_finance_effective_members": sorted([first, second]),
        "partner_finance_effective_members": [third],
        "note": (
            "Five principals, five keys. The two groups named Finance must not merge, and "
            "the corp Finance group must never return the partner-forest Jordan Rivera."
        ),
    }
    return transcript


def rename_before() -> Transcript:
    transcript = Transcript(
        "a04a-rename-before",
        "A principal before it was renamed",
        "The first of a pair. Replay this run, then a04b, to observe a rename: the names "
        "change, the SID does not, and neither does anything keyed on it.",
        "0000a04a",
    )
    subject = transcript.user(corp(2401), "Sam Beckett", "sbeckett")
    group = transcript.group(corp(2410), "Payroll-RW")
    edge_key = transcript.edge(corp(2410), corp(2401), member_kind=PrincipalKind.USER)
    transcript.expectations = {
        "subject_key": subject,
        "group_key": group,
        "edge_key": edge_key,
        "display_name": "Sam Beckett",
        "sam_account_name": "sbeckett",
        "distinguished_name": f"CN=Sam Beckett,OU=Users,{CORP_DN}",
        "note": "Replay a04b next; nothing keyed on the SID may change.",
    }
    return transcript


def rename_after() -> Transcript:
    transcript = Transcript(
        "a04b-rename-after",
        "The same principal after a rename and a move",
        "Display name, sAMAccountName, UPN, and distinguished name all changed; the SID "
        "did not. The principal key, the edge key, and the membership must be unchanged, "
        "and every previous name must still be retrievable as an alias.",
        "0000a04b",
    )
    subject = transcript.principal(
        corp(2401),
        PrincipalKind.USER,
        display_name="Samantha Beckett-Nolan",
        sam_account_name="sbnolan",
        user_principal_name="sbnolan@corp.example.com",
        distinguished_name=f"CN=Samantha Beckett-Nolan,OU=Contractors,{CORP_DN}",
        enabled=True,
    )
    group = transcript.group(corp(2410), "Payroll-RW")
    edge_key = transcript.edge(corp(2410), corp(2401), member_kind=PrincipalKind.USER)
    transcript.expectations = {
        "subject_key": subject,
        "group_key": group,
        "edge_key": edge_key,
        "display_name": "Samantha Beckett-Nolan",
        "sam_account_name": "sbnolan",
        "distinguished_name": f"CN=Samantha Beckett-Nolan,OU=Contractors,{CORP_DN}",
        "alias_count_after_both_runs": 8,
        "previous_names": ["Sam Beckett", "sbeckett", "sbeckett@corp.example.com"],
        "note": (
            "Eight aliases after both runs: four name kinds, two values each. The older "
            "values are kept because 'this SID used to be called sbeckett' is evidence."
        ),
    }
    return transcript


def unresolved_and_deleted() -> Transcript:
    transcript = Transcript(
        "a05-unresolved-and-deleted",
        "Orphaned SIDs, a deleted account, and a member nothing describes",
        "A group whose membership is mostly wreckage: a SID whose account was deleted, a "
        "SID from a domain this collector cannot query, a SID whose lookup failed, an "
        "account flagged deleted in the directory, and an edge naming a SID that no "
        "observation describes at all. Every one of them is a finding; none may be "
        "dropped.",
        "00000a05",
    )
    group = transcript.group(corp(2500), "Legacy-Access")
    deleted_sid = transcript.principal(
        corp(2501),
        PrincipalKind.UNRESOLVED,
        unresolved_reason=UnresolvedReason.DELETED,
        last_known_name="svc-oldbackup",
    )
    untrusted = transcript.principal(
        partner(2502),
        PrincipalKind.UNRESOLVED,
        unresolved_reason=UnresolvedReason.UNTRUSTED_DOMAIN,
    )
    lookup_failed = transcript.principal(
        corp(2503),
        PrincipalKind.UNRESOLVED,
        unresolved_reason=UnresolvedReason.LOOKUP_FAILED,
        last_known_name="CORP\\jdoe",
    )
    tombstoned = transcript.user(corp(2504), "Marcus Webb", "mwebb", is_deleted=True, enabled=False)

    transcript.edge(corp(2500), corp(2501), member_kind=PrincipalKind.UNRESOLVED)
    transcript.edge(corp(2500), partner(2502), member_kind=PrincipalKind.UNRESOLVED)
    transcript.edge(corp(2500), corp(2503), member_kind=PrincipalKind.UNRESOLVED)
    transcript.edge(corp(2500), corp(2504), member_kind=PrincipalKind.USER)
    # An edge whose member has no principal observation anywhere in this run. The
    # membership was observed; only the description is missing.
    transcript.edge(corp(2500), corp(2599))

    transcript.expectations = {
        "group_key": group,
        "unresolved_keys": sorted([deleted_sid, untrusted, lookup_failed]),
        "deleted_account_key": tombstoned,
        "undescribed_member_key": corp(2599),
        "effective_member_count": 5,
        "undescribed_member_is_resolved": False,
        "undescribed_member_kind": None,
        "note": (
            "All five are effective members under the default include=non_groups filter. "
            "The undescribed SID is returned with kind null, resolved false, and is_group "
            "null — never a guessed false, and never filtered away for want of a label."
        ),
    }
    return transcript


def foreign_security_principals() -> Transcript:
    transcript = Transcript(
        "a06-foreign-security-principals",
        "Members from a trusted forest",
        "Principals whose only local representation is a stub object named after their "
        "SID. One is a user, one is a group whose own membership this domain cannot see, "
        "and one is reachable by two routes of different lengths — only one of which "
        "crosses the trust.",
        "00000a06",
    )
    top = transcript.group(corp(2600), "Cross-Forest-RW")
    nested = transcript.group(corp(2601), "Cross-Forest-Nested")
    local_user = transcript.user(corp(2602), "Elena Marsh", "emarsh")
    fsp_user = transcript.principal(
        partner(1500),
        PrincipalKind.FOREIGN_SECURITY_PRINCIPAL,
        distinguished_name=f"CN={partner(1500)},CN=ForeignSecurityPrincipals,{CORP_DN}",
    )
    fsp_group = transcript.principal(
        partner(1600),
        PrincipalKind.FOREIGN_SECURITY_PRINCIPAL,
        distinguished_name=f"CN={partner(1600)},CN=ForeignSecurityPrincipals,{CORP_DN}",
    )

    transcript.edge(corp(2600), corp(2601), member_kind=PrincipalKind.DOMAIN_GROUP)
    transcript.edge(
        corp(2600),
        partner(1500),
        member_kind=PrincipalKind.FOREIGN_SECURITY_PRINCIPAL,
        foreign=True,
    )
    transcript.edge(
        corp(2601),
        partner(1600),
        member_kind=PrincipalKind.FOREIGN_SECURITY_PRINCIPAL,
        foreign=True,
    )
    transcript.edge(corp(2601), corp(2602), member_kind=PrincipalKind.USER)

    transcript.expectations = {
        "top_group_key": top,
        "nested_group_key": nested,
        "foreign_user_key": fsp_user,
        "foreign_group_key": fsp_group,
        "local_user_key": local_user,
        "keys_reached_across_the_trust": sorted([fsp_user, fsp_group]),
        "keys_reached_without_crossing_the_trust": sorted([nested, local_user]),
        "foreign_group_has_no_observed_members": True,
        "note": (
            "via_foreign_security_principal must be true for exactly the two SIDs reached "
            "through a trust hop. The foreign group is a leaf here: this domain cannot "
            "enumerate it, and inventing members for it would fabricate access."
        ),
    }
    return transcript


def disabled_and_empty() -> Transcript:
    transcript = Transcript(
        "a07-disabled-empty-distribution",
        "Disabled accounts, an empty group, and a distribution group",
        "Membership that a naive filter would quietly discard: an account that is "
        "disabled but still a member, a group with no members at all, a distribution "
        "group that grants nothing but whose membership is still real, and a group whose "
        "only member is another group.",
        "00000a07",
    )
    helpdesk = transcript.group(corp(2700), "Helpdesk")
    disabled = transcript.user(corp(2701), "Tomas Klein", "tklein", enabled=False)
    active = transcript.user(corp(2702), "Ada Fenwick", "afenwick", enabled=True)
    # An account whose enabled state the directory did not report at all.
    unknown_state = transcript.principal(
        corp(2703),
        PrincipalKind.USER,
        display_name="Rowan Vale",
        sam_account_name="rvale",
    )
    empty = transcript.group(corp(2710), "Retired-Access")
    wrapper = transcript.group(corp(2711), "Retired-Access-Wrapper")
    announce = transcript.group(corp(2720), "All-Staff-Announce", group_type=GroupType.DISTRIBUTION)

    transcript.edge(corp(2700), corp(2701), member_kind=PrincipalKind.USER)
    transcript.edge(corp(2700), corp(2702), member_kind=PrincipalKind.USER)
    transcript.edge(corp(2700), corp(2703), member_kind=PrincipalKind.USER)
    transcript.edge(corp(2711), corp(2710), member_kind=PrincipalKind.DOMAIN_GROUP)
    transcript.edge(corp(2720), corp(2702), member_kind=PrincipalKind.USER)

    transcript.expectations = {
        "helpdesk_key": helpdesk,
        "disabled_user_key": disabled,
        "active_user_key": active,
        "unknown_enabled_state_key": unknown_state,
        "empty_group_key": empty,
        "wrapper_group_key": wrapper,
        "distribution_group_key": announce,
        "helpdesk_effective_members": sorted([disabled, active, unknown_state]),
        "empty_group_effective_members": [],
        "wrapper_effective_members_non_groups": [],
        "wrapper_effective_members_all": [empty],
        "note": (
            "A disabled account is still a member and must still be returned; whether that "
            "matters is the reviewer's judgment, not the traversal's. An account whose "
            "enabled state was never reported is null, not false. An empty group expands "
            "to nothing and is complete."
        ),
    }
    return transcript


def large_group() -> Transcript:
    described_users = 10
    undescribed_members = 500
    nested_members = 3
    transcript = Transcript(
        "a08-large-group",
        "A group with five hundred and twelve direct members",
        "Wide rather than deep: 512 direct members, of which 500 are named by an edge and "
        "described by nothing. Crosses the default page size five times and the maximum "
        "page size once, so pagination, the unlabelled-member rule, and the cost of one "
        "wide breadth-first level are all exercised at once.",
        "00000a08",
    )
    everyone = transcript.group(corp(2800), "Everyone-RW")
    nested = transcript.group(corp(2801), "Everyone-RW-Nested")
    described = [
        transcript.user(corp(2810 + index), f"Bulk User {index:02d}", f"bulk{index:02d}")
        for index in range(described_users)
    ]
    computer = transcript.principal(
        corp(2850),
        PrincipalKind.COMPUTER,
        display_name="WKS-4410",
        sam_account_name="WKS-4410$",
        distinguished_name=f"CN=WKS-4410,OU=Workstations,{CORP_DN}",
    )
    for index in range(nested_members):
        transcript.user(corp(2860 + index), f"Nested User {index:02d}", f"nested{index:02d}")

    for index in range(described_users):
        transcript.edge(corp(2800), corp(2810 + index), member_kind=PrincipalKind.USER)
    transcript.edge(corp(2800), corp(2850), member_kind=PrincipalKind.COMPUTER)
    transcript.edge(corp(2800), corp(2801), member_kind=PrincipalKind.DOMAIN_GROUP)
    for index in range(nested_members):
        transcript.edge(corp(2801), corp(2860 + index), member_kind=PrincipalKind.USER)
    # The bulk of the membership: real edges to SIDs this run never described.
    for index in range(undescribed_members):
        transcript.edge(corp(2800), corp(40000 + index))

    direct = described_users + 1 + 1 + undescribed_members
    transcript.expectations = {
        "group_key": everyone,
        "nested_group_key": nested,
        "direct_member_count": direct,
        "described_member_keys": sorted([*described, computer, nested]),
        "undescribed_member_count": undescribed_members,
        "effective_member_count_all": direct + nested_members,
        "effective_member_count_non_groups": direct - 1 + nested_members,
        "effective_member_count_users": described_users + nested_members,
        "pages_at_default_page_size": 6,
        "pages_at_maximum_page_size": 2,
        "note": (
            "include=users drops 501 real members, which is why it is not the default. "
            "One breadth-first level costs one query however wide it is."
        ),
    }
    return transcript


def multiple_paths() -> Transcript:
    transcript = Transcript(
        "a09-multiple-paths",
        "Four independent routes to one group",
        "The same user reaches Finance-RW directly, through one nested group, through two "
        "nested groups, and through a primaryGroupID membership that never appears in any "
        "group's member attribute. Removing any one route leaves the access in place, "
        "which is the whole reason paths are enumerated rather than counted.",
        "00000a09",
    )
    subject = transcript.user(corp(2901), "Ines Duarte", "iduarte")
    finance_rw = transcript.group(corp(2910), "Finance-RW", scope=GroupScope.DOMAIN_LOCAL)
    finance_staff = transcript.group(corp(2911), "Finance-Staff", scope=GroupScope.GLOBAL)
    contractors = transcript.group(corp(2912), "Finance-Contractors", scope=GroupScope.GLOBAL)
    domain_users = transcript.group(corp(513), "Domain Users", scope=GroupScope.GLOBAL)

    transcript.edge(corp(2910), corp(2901), member_kind=PrincipalKind.USER)
    transcript.edge(corp(2911), corp(2901), member_kind=PrincipalKind.USER)
    transcript.edge(corp(2910), corp(2911), member_kind=PrincipalKind.DOMAIN_GROUP)
    transcript.edge(corp(2912), corp(2901), member_kind=PrincipalKind.USER)
    transcript.edge(corp(2911), corp(2912), member_kind=PrincipalKind.DOMAIN_GROUP)
    transcript.edge(
        corp(513),
        corp(2901),
        edge_kind=MembershipEdgeKind.PRIMARY_GROUP,
        member_kind=PrincipalKind.USER,
    )
    transcript.edge(corp(2910), corp(513), member_kind=PrincipalKind.DOMAIN_GROUP)

    transcript.expectations = {
        "subject_key": subject,
        "target_group_key": finance_rw,
        "path_count": 4,
        "shortest_path": [subject, finance_rw],
        "longest_path": [subject, contractors, finance_staff, finance_rw],
        # Shortest first, then lexicographic by key within a length. Finance-Staff
        # (RID 2911) sorts before Domain Users (RID 513) as a string, which is what makes
        # the order stable enough to diff between runs.
        "paths": [
            [subject, finance_rw],
            [subject, finance_staff, finance_rw],
            [subject, domain_users, finance_rw],
            [subject, contractors, finance_staff, finance_rw],
        ],
        "primary_group_path": [subject, domain_users, finance_rw],
        "note": (
            "A collector that reads only the member attribute loses the Domain Users "
            "route entirely. Path enumeration returns shortest first, lexicographic "
            "within a length."
        ),
    }
    return transcript


def builtin_scoping() -> Transcript:
    transcript = Transcript(
        "a10-builtin-scoping",
        "One BUILTIN SID, three different groups",
        "S-1-5-32-544 on FS10, S-1-5-32-544 on FS11, and S-1-5-32-544 as the domain's own "
        "BUILTIN group. Same SID, three groups, three memberships that have nothing to do "
        "with each other. Merging any two of them would invent access nobody has.",
        "00000a10",
    )
    administrators = "S-1-5-32-544"
    fs10 = transcript.principal(
        administrators,
        PrincipalKind.LOCAL_GROUP,
        host_key="FS10",
        display_name="Administrators",
        sam_account_name="Administrators",
    )
    fs11 = transcript.principal(
        administrators,
        PrincipalKind.LOCAL_GROUP,
        host_key="FS11",
        display_name="Administrators",
        sam_account_name="Administrators",
    )
    # The domain's own BUILTIN\Administrators, which carries no host scope at all.
    domain_builtin = transcript.principal(
        administrators,
        PrincipalKind.DOMAIN_GROUP,
        display_name="Administrators",
        sam_account_name="Administrators",
        distinguished_name=f"CN=Administrators,CN=Builtin,{CORP_DN}",
        group_scope=GroupScope.BUILTIN_LOCAL,
        group_type=GroupType.SECURITY,
    )
    fs10_member = transcript.user(corp(21001), "Owen Traore", "otraore")
    fs11_member = transcript.user(corp(21002), "Lena Fischer", "lfischer")
    domain_member = transcript.group(corp(21003), "Domain Admins", scope=GroupScope.GLOBAL)

    transcript.edge(
        administrators,
        corp(21001),
        edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
        host_key="FS10",
        member_kind=PrincipalKind.USER,
    )
    transcript.edge(
        administrators,
        corp(21002),
        edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
        host_key="FS11",
        member_kind=PrincipalKind.USER,
    )
    transcript.edge(administrators, corp(21003), member_kind=PrincipalKind.DOMAIN_GROUP)

    transcript.expectations = {
        "distinct_group_keys": sorted([fs10, fs11, domain_builtin]),
        "fs10_group_key": fs10,
        "fs11_group_key": fs11,
        "domain_builtin_group_key": domain_builtin,
        "fs10_effective_members": [fs10_member],
        "fs11_effective_members": [fs11_member],
        "domain_builtin_effective_members": [domain_member],
        "bare_sid_is_ambiguous": True,
        "bare_sid_candidate_count": 3,
        "host_qualified_lookup_is_exact": True,
        "known_hazard": (
            "Only local_group principals are host-scoped. The domain's own BUILTIN group "
            "is stored under the bare SID, so two Active Directory domains collected into "
            "one ADG would merge their BUILTIN groups and their memberships. Detected by "
            "scripts/validate-collector-output.ps1; see "
            "docs/architecture/ad-graph-validation.md."
        ),
        "note": (
            "Resolving the bare SID must return 409 with all three candidates. Resolving "
            "it with host=FS10 must return the FS10 group exactly, not the ambiguity."
        ),
    }
    return transcript


def builtin_hosts_only() -> Transcript:
    transcript = Transcript(
        "a10-builtin-scoping-hosts-only",
        "One BUILTIN SID on two servers and nowhere else",
        "What an SMB collector produces on its own: BUILTIN\\Administrators on FS12 and on "
        "FS13, host-scoped, with no domain BUILTIN group anywhere. A bare S-1-5-32-544 now "
        "matches two stored principals and nothing else, so it is genuinely ambiguous and "
        "must be answered with both candidates rather than by picking one.",
        "0000a10b",
    )
    administrators = "S-1-5-32-544"
    fs12 = transcript.principal(
        administrators,
        PrincipalKind.LOCAL_GROUP,
        host_key="FS12",
        display_name="Administrators",
        sam_account_name="Administrators",
    )
    fs13 = transcript.principal(
        administrators,
        PrincipalKind.LOCAL_GROUP,
        host_key="FS13",
        display_name="Administrators",
        sam_account_name="Administrators",
    )
    fs12_member = transcript.user(corp(21011), "Bea Nowak", "bnowak")
    fs13_member = transcript.user(corp(21012), "Caleb Mensah", "cmensah")

    transcript.edge(
        administrators,
        corp(21011),
        edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
        host_key="FS12",
        member_kind=PrincipalKind.USER,
    )
    transcript.edge(
        administrators,
        corp(21012),
        edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
        host_key="FS13",
        member_kind=PrincipalKind.USER,
    )

    transcript.expectations = {
        "distinct_group_keys": sorted([fs12, fs13]),
        "fs12_group_key": fs12,
        "fs13_group_key": fs13,
        "fs12_effective_members": [fs12_member],
        "fs13_effective_members": [fs13_member],
        "bare_sid_is_ambiguous": True,
        "bare_sid_candidate_count": 2,
        "note": (
            "Resolving the bare SID must be a 409 listing both groups. This is the shape "
            "the host-scoping rule was designed for, and the contrast with a10 — where a "
            "domain BUILTIN group occupies the bare key and wins outright — is the point."
        ),
    }
    return transcript


BUILDERS = (
    deep_nesting,
    cycles,
    duplicate_names,
    rename_before,
    rename_after,
    unresolved_and_deleted,
    foreign_security_principals,
    disabled_and_empty,
    large_group,
    multiple_paths,
    builtin_scoping,
    builtin_hosts_only,
)


def build_all() -> dict[str, dict[str, Any]]:
    """Every adversarial transcript, keyed by file stem."""
    documents: dict[str, dict[str, Any]] = {}
    for builder in BUILDERS:
        transcript = builder()
        documents[transcript.name] = transcript.document()
    return documents


def render(document: dict[str, Any]) -> str:
    """The exact bytes committed for one transcript."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed files match this generator instead of writing them.",
    )
    arguments = parser.parse_args(argv)

    AD_GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    stale = 0
    for name, document in build_all().items():
        path = AD_GRAPH_DIR / f"{name}.json"
        rendered = render(document)
        if arguments.check:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != rendered:
                stale += 1
                print(f"stale: {path}", file=sys.stderr)
            continue
        path.write_text(rendered, encoding="utf-8")
        print(f"wrote {path}")

    if arguments.check and stale:
        print(
            f"{stale} fixture(s) do not match the generator. Run "
            "`python -m tests.fixtures.build_ad_graph` and review the diff.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
