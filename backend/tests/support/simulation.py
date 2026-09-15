r"""An estate in memory, and the repositories that serve it.

The simulation engine is the live effective-access engine reading through an overlay, so the
only honest way to test it is to run the live engine. That needs the two repositories
:class:`app.services.AccessService` takes by injection — and needs them as *subclasses*, because
the overlay repositories wrap a baseline and read its session off it.

So these are real subclasses backed by dictionaries. Nothing here reimplements a query: each
override answers the same question the SQL does, over rows built through the same record
types the repositories return, in the same order (``order_index`` first, ``NULLS LAST``). A
test that passes against these and fails against PostgreSQL means the fakes are wrong, and
``tests/db/test_simulation.py`` runs the same scenarios against a real database for exactly
that reason.

The estate is small and deliberately shaped. ``alice`` reaches ``Finance-RW`` through a nested
group **and** holds a weaker grant through ``Domain Users``; ``bob`` reaches it only one way.
That is the shape every interesting claim in a what-if turns on: remove one membership and one
of them loses access and the other does not, and a tool that cannot tell them apart is worse
than no tool.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import (
    AceFlag,
    AceSource,
    AceType,
    AclAceFacts,
    Direction,
    GraphEdge,
    MembershipEdgeKind,
    PrincipalKind,
    ResourceKind,
    ShareType,
    Sid,
    acl_hash,
    ntfs_ace_identity_key,
    parse_unc_path,
    referenced_principal_key,
    share_ace_identity_key,
    share_ace_right_token,
)
from app.domain.basis import CollectionBasis
from app.repositories.membership import MembershipRepository, Page, PrincipalRecord
from app.repositories.resources import (
    MAX_ACL_FETCH,
    NtfsAceRecord,
    NtfsResourceRecord,
    ResourceRepository,
    ShareAceRecord,
    ShareRecord,
)
from app.simulation.model import BaselineKind, SimulationBaseline

DOMAIN: Final = "S-1-5-21-1004336348-1177238915-682003330"

ALICE: Final = f"{DOMAIN}-1104"
BOB: Final = f"{DOMAIN}-1105"
CAROL: Final = f"{DOMAIN}-1106"
DAVE: Final = f"{DOMAIN}-1107"
FINANCE_TEAM: Final = f"{DOMAIN}-1201"
FINANCE_RW: Final = f"{DOMAIN}-1202"
DOMAIN_USERS: Final = f"{DOMAIN}-513"

SERVER: Final = "fs01"
SHARE_KEY: Final = "fs01|finance"
FINANCE: Final = "\\\\fs01\\finance"
REPORTS: Final = "\\\\fs01\\finance\\reports"

READ_EXECUTE: Final = 0x001200A9
MODIFY: Final = 0x001301BF
FULL_CONTROL: Final = 0x001F01FF

RUN_ID: Final = uuid.UUID("11111111-2222-3333-4444-555555555555")
OBSERVED_AT: Final = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.UTC)


# --------------------------------------------------------------------------------------
# Record builders
# --------------------------------------------------------------------------------------


def principal(
    sid: str,
    kind: PrincipalKind,
    name: str,
    *,
    host_key: str | None = None,
) -> PrincipalRecord:
    key = f"{host_key}|{sid}" if host_key else sid
    return PrincipalRecord(
        principal_key=key,
        sid=sid,
        principal_kind=kind,
        host_key=host_key,
        domain_sid=DOMAIN,
        display_name=name,
        sam_account_name=name,
        user_principal_name=None,
        distinguished_name=None,
        group_scope=None,
        group_type=None,
        enabled=True,
        is_deleted=False,
        unresolved_reason=None,
        last_known_name=None,
        first_observed_at=OBSERVED_AT,
        first_observed_run_id=RUN_ID,
        last_observed_at=OBSERVED_AT,
        last_observed_run_id=RUN_ID,
    )


def edge(group: str, member: str) -> GraphEdge:
    kind = MembershipEdgeKind.DIRECTORY_GROUP_MEMBER
    return GraphEdge(
        edge_key=f"{group}->{member}|{kind.value}",
        group_key=group,
        member_key=member,
        kind=kind,
        host_key=None,
        member_kind=None,
        is_foreign_security_principal=False,
    )


def ntfs_ace(
    path: str,
    trustee_sid: str,
    access_mask: int,
    *,
    ace_type: AceType = AceType.ALLOW,
    flags: int = 0,
    source: AceSource = AceSource.EXPLICIT,
    order_index: int = 0,
    inherited_from: str | None = None,
) -> NtfsAceRecord:
    resource_key = parse_unc_path(path).comparison_key
    return NtfsAceRecord(
        ace_key=ntfs_ace_identity_key(
            resource_key=resource_key,
            trustee_sid=Sid(trustee_sid),
            ace_type=ace_type,
            access_mask=access_mask,
            flags=AceFlag(flags),
        ),
        resource_key=resource_key,
        trustee_sid=trustee_sid,
        trustee_key=referenced_principal_key(Sid(trustee_sid), SERVER),
        ace_type=ace_type,
        access_mask=access_mask,
        ace_flags=flags,
        source=source,
        inherited_from=inherited_from,
        order_index=order_index,
        source_key=f"ntfs_ace|{resource_key}|{trustee_sid}",
        first_observed_at=OBSERVED_AT,
        first_observed_run_id=RUN_ID,
        last_observed_at=OBSERVED_AT,
        last_observed_run_id=RUN_ID,
    )


def share_ace(
    trustee_sid: str, access_mask: int, *, ace_type: AceType = AceType.ALLOW, order_index: int = 0
) -> ShareAceRecord:
    token = share_ace_right_token(access_mask, None)
    return ShareAceRecord(
        ace_key=share_ace_identity_key(
            share_key=SHARE_KEY,
            trustee_sid=Sid(trustee_sid),
            ace_type=ace_type,
            right_token=token,
        ),
        share_key=SHARE_KEY,
        trustee_sid=trustee_sid,
        trustee_key=referenced_principal_key(Sid(trustee_sid), SERVER),
        ace_type=ace_type,
        access_mask=access_mask,
        permission=None,
        right_token=token,
        order_index=order_index,
        source_key=f"smb_ace|{SHARE_KEY}|{trustee_sid}",
        first_observed_at=OBSERVED_AT,
        first_observed_run_id=RUN_ID,
        last_observed_at=OBSERVED_AT,
        last_observed_run_id=RUN_ID,
    )


def resource(
    path: str,
    entries: Sequence[NtfsAceRecord],
    *,
    dacl_present: bool = True,
    dacl_protected: bool = False,
    owner_sid: str | None = None,
) -> NtfsResourceRecord:
    key = parse_unc_path(path).comparison_key
    return NtfsResourceRecord(
        resource_key=key,
        path=path,
        server_key=SERVER,
        share_key=SHARE_KEY,
        local_path=None,
        owner_sid=owner_sid,
        group_sid=None,
        dacl_present=dacl_present,
        dacl_protected=dacl_protected,
        inheritance_enabled=not dacl_protected,
        is_acl_boundary=dacl_protected,
        ace_count=len(entries),
        depth_from_share_root=len(parse_unc_path(path).segments),
        resource_kind=ResourceKind.DIRECTORY,
        boundary_reason=None,
        acl_hash=acl_hash(
            dacl_present=dacl_present,
            dacl_protected=dacl_protected,
            aces=[item.acl_facts for item in entries] if dacl_present else [],
        ),
        parent_acl_hash=None,
        source_key=f"resource|{key}",
        first_observed_at=OBSERVED_AT,
        first_observed_run_id=RUN_ID,
        last_observed_at=OBSERVED_AT,
        last_observed_run_id=RUN_ID,
    )


def share() -> ShareRecord:
    return ShareRecord(
        share_key=SHARE_KEY,
        server_key=SERVER,
        name="Finance",
        local_path="D:\\Shares\\Finance",
        share_type=ShareType.DISK,
        description=None,
        concurrent_user_limit=None,
        caching_mode=None,
        is_special=False,
        source_key=f"share|{SHARE_KEY}",
        first_observed_at=OBSERVED_AT,
        first_observed_run_id=RUN_ID,
        last_observed_at=OBSERVED_AT,
        last_observed_run_id=RUN_ID,
    )


def facts(entries: Iterable[NtfsAceRecord]) -> list[AclAceFacts]:
    return [entry.acl_facts for entry in entries]


# --------------------------------------------------------------------------------------
# Repositories
# --------------------------------------------------------------------------------------

_NO_SESSION = cast(AsyncSession, None)
"""These fakes never execute a statement, so there is nothing for a session to be.

Typed rather than left as ``None`` so that an override which *did* reach for the session
fails loudly with an ``AttributeError`` at the first call rather than silently returning
something plausible.
"""


class FakeMembershipRepository(MembershipRepository):
    """Adjacency, principals and membership coverage, over dictionaries."""

    def __init__(
        self,
        edges: Iterable[GraphEdge] = (),
        principals: Mapping[str, PrincipalRecord] | None = None,
        *,
        enumerated: Iterable[str] | None = None,
    ) -> None:
        super().__init__(_NO_SESSION)
        self.edges = list(edges)
        self.principals = dict(principals or {})
        self._enumerated = (
            set(enumerated) if enumerated is not None else {item.group_key for item in self.edges}
        )
        self.calls = 0

    async def neighbors(
        self, direction: Direction, keys: Sequence[str]
    ) -> Mapping[str, Sequence[GraphEdge]]:
        self.calls += 1
        wanted = list(dict.fromkeys(keys))
        found: dict[str, list[GraphEdge]] = {key: [] for key in wanted}
        for item in self.edges:
            origin = item.origin(direction)
            if origin in found:
                found[origin].append(item)
                self._edges_fetched += 1
        return found

    async def get_principal(self, principal_key: str) -> PrincipalRecord | None:
        return self.principals.get(principal_key)

    async def principals_by_keys(self, keys: Sequence[str]) -> dict[str, PrincipalRecord]:
        return {key: self.principals[key] for key in keys if key in self.principals}

    async def keys_with_members(self, keys: Sequence[str]) -> frozenset[str]:
        return frozenset(key for key in keys if key in self._enumerated)


class FakeResourceRepository(ResourceRepository):
    """Shares, directories and their ACLs, over dictionaries.

    Only the reads an effective-access resolution makes are implemented; everything else is
    inherited and would fail on the absent session, which is the intended outcome — a test
    that reached for a listing endpoint through this class is testing something these fakes
    do not model.
    """

    def __init__(
        self,
        resources: Iterable[NtfsResourceRecord] = (),
        ntfs_acls: Mapping[str, Sequence[NtfsAceRecord]] | None = None,
        shares: Iterable[ShareRecord] = (),
        share_acls: Mapping[str, Sequence[ShareAceRecord]] | None = None,
    ) -> None:
        super().__init__(_NO_SESSION)
        self.resources = {item.resource_key: item for item in resources}
        self.ntfs_acls = {key: tuple(value) for key, value in (ntfs_acls or {}).items()}
        self.shares = {item.share_key: item for item in shares}
        self.share_acls = {key: tuple(value) for key, value in (share_acls or {}).items()}

    # ------------------------------------------------------------------- reads

    async def get_ntfs_resource(self, resource_key: str) -> NtfsResourceRecord | None:
        return self.resources.get(resource_key.casefold())

    async def ntfs_resources_by_keys(self, keys: Sequence[str]) -> dict[str, NtfsResourceRecord]:
        return {
            key.casefold(): self.resources[key.casefold()]
            for key in keys
            if key.casefold() in self.resources
        }

    async def full_ntfs_acl(
        self, resource_key: str, *, limit: int = MAX_ACL_FETCH
    ) -> tuple[NtfsAceRecord, ...]:
        return _ordered(self.ntfs_acls.get(resource_key.casefold(), ()))[: limit + 1]

    async def ntfs_acls_for(
        self, keys: Sequence[str], *, limit: int = MAX_ACL_FETCH
    ) -> dict[str, tuple[NtfsAceRecord, ...]]:
        found: dict[str, tuple[NtfsAceRecord, ...]] = {}
        for key in keys:
            entries = self.ntfs_acls.get(key.casefold())
            if entries:
                found[key.casefold()] = _ordered(entries)[: limit + 1]
        return found

    async def get_share(self, share_key: str) -> ShareRecord | None:
        return self.shares.get(share_key.casefold())

    async def shares_by_keys(self, keys: Sequence[str]) -> dict[str, ShareRecord]:
        return {
            key.casefold(): self.shares[key.casefold()]
            for key in keys
            if key.casefold() in self.shares
        }

    async def full_share_acl(
        self, share_key: str, *, limit: int = MAX_ACL_FETCH
    ) -> tuple[ShareAceRecord, ...]:
        return _ordered(self.share_acls.get(share_key.casefold(), ()))[: limit + 1]

    async def share_acls_for(
        self, keys: Sequence[str], *, limit: int = MAX_ACL_FETCH
    ) -> dict[str, tuple[ShareAceRecord, ...]]:
        found: dict[str, tuple[ShareAceRecord, ...]] = {}
        for key in keys:
            entries = self.share_acls.get(key.casefold())
            if entries:
                found[key.casefold()] = _ordered(entries)[: limit + 1]
        return found

    async def resources_named_by(
        self, principal_keys: Sequence[str], *, limit: int = 100, after: str | None = None
    ) -> Page[str]:
        named = set(principal_keys)
        candidates = {
            key
            for key, entries in self.ntfs_acls.items()
            if any(entry.trustee_key in named for entry in entries)
        }
        candidates |= {key for key, item in self.resources.items() if not item.dacl_present}
        return _page(sorted(candidates), limit, after)

    async def shares_named_by(
        self, principal_keys: Sequence[str], *, limit: int = 100, after: str | None = None
    ) -> Page[str]:
        named = set(principal_keys)
        candidates = {
            key
            for key, entries in self.share_acls.items()
            if any(entry.trustee_key in named for entry in entries)
        }
        return _page(sorted(candidates), limit, after)


def _ordered(entries: Sequence[Any]) -> tuple[Any, ...]:
    """Evaluation order: reported position first, entries without one last, then by key."""
    return tuple(
        sorted(
            entries,
            key=lambda entry: (entry.order_index is None, entry.order_index or 0, entry.ace_key),
        )
    )


def _page(keys: Sequence[str], limit: int, after: str | None) -> Page[str]:
    page_size = max(1, min(limit, 500))
    remaining = [key for key in keys if after is None or key > after]
    visible = tuple(remaining[:page_size])
    has_more = len(remaining) > page_size
    return Page(
        items=visible, has_more=has_more, next_key=visible[-1] if has_more and visible else None
    )


# --------------------------------------------------------------------------------------
# The estate
# --------------------------------------------------------------------------------------


def finance_estate() -> tuple[FakeResourceRepository, FakeMembershipRepository]:
    r"""``\\fs01\finance``, two grants, and three people who reach it differently.

    * ``Finance-RW`` is granted Modify on the directory; ``Domain Users`` is granted
      Read/Execute.
    * ``alice`` is in ``Finance-Team``, which is in ``Finance-RW``, **and** in ``Domain
      Users`` — two routes, delivering different rights.
    * ``bob`` is in ``Finance-RW`` directly and in nothing else.
    * ``carol`` is in ``Domain Users`` only.
    * ``dave`` is in nothing and reaches nothing, which is what makes *gaining* access
      testable: a principal the baseline enumeration cannot even see.

    The share ACL grants ``Domain Users`` Full Control, so the share never limits and every
    difference in the answers comes from NTFS — which keeps a test's failure message about
    the thing it was testing.
    """
    entries = [
        ntfs_ace(FINANCE, FINANCE_RW, MODIFY, order_index=0),
        ntfs_ace(FINANCE, DOMAIN_USERS, READ_EXECUTE, order_index=1),
    ]
    child = [
        ntfs_ace(
            REPORTS,
            FINANCE_RW,
            MODIFY,
            flags=int(AceFlag.CONTAINER_INHERIT | AceFlag.OBJECT_INHERIT | AceFlag.INHERITED),
            source=AceSource.INHERITED,
            inherited_from=parse_unc_path(FINANCE).comparison_key,
            order_index=0,
        )
    ]
    resources = FakeResourceRepository(
        resources=[resource(FINANCE, entries), resource(REPORTS, child)],
        ntfs_acls={
            parse_unc_path(FINANCE).comparison_key: entries,
            parse_unc_path(REPORTS).comparison_key: child,
        },
        shares=[share()],
        share_acls={SHARE_KEY: [share_ace(DOMAIN_USERS, FULL_CONTROL, order_index=0)]},
    )
    membership = FakeMembershipRepository(
        edges=[
            edge(FINANCE_RW, FINANCE_TEAM),
            edge(FINANCE_TEAM, ALICE),
            edge(FINANCE_RW, BOB),
            edge(DOMAIN_USERS, ALICE),
            edge(DOMAIN_USERS, CAROL),
        ],
        principals={
            ALICE: principal(ALICE, PrincipalKind.USER, "alice"),
            BOB: principal(BOB, PrincipalKind.USER, "bob"),
            CAROL: principal(CAROL, PrincipalKind.USER, "carol"),
            DAVE: principal(DAVE, PrincipalKind.USER, "dave"),
            FINANCE_TEAM: principal(FINANCE_TEAM, PrincipalKind.DOMAIN_GROUP, "Finance-Team"),
            FINANCE_RW: principal(FINANCE_RW, PrincipalKind.DOMAIN_GROUP, "Finance-RW"),
            DOMAIN_USERS: principal(DOMAIN_USERS, PrincipalKind.DOMAIN_GROUP, "Domain Users"),
        },
    )
    return resources, membership


def fixed_baseline(*, observations: int = 12) -> SimulationBaseline:
    """A current-state baseline with a stable token, for tests that have no ``scan_runs``.

    Built from a real :class:`app.domain.CollectionBasis` rather than a stub, so the token a
    test compares is the token the production code computes -- and ``observations`` is the
    knob that moves it, which is what makes staleness testable without a database.
    """
    return SimulationBaseline(
        kind=BaselineKind.CURRENT,
        basis=CollectionBasis(
            runs=1,
            latest_run_id=str(RUN_ID),
            latest_activity_at=OBSERVED_AT,
            observations_applied=observations,
            batches_received=1,
        ),
        captured_at=OBSERVED_AT,
    )
