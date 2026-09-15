r"""Current state is what a reconciliation last left standing, not what was ever stored.

ADG deletes no collected fact. Every row a collector has ever sent stays in ``principals``,
``membership_edges``, ``smb_share_aces``, ``ntfs_aces`` and the rest, and that is the
product: the record of what was once true is the thing an auditor came for.

The defect this suite exists for is what that used to mean for the *live* answer. After an
authoritative reconciliation proved a membership edge or an ACE gone, the point-in-time
engine correctly stopped counting it — it reads ``object_versions`` — and the ordinary
current-state engine went on counting it, because it read the tables directly. The same
reconciled estate therefore answered

    as of the recollection instant:  no access
    right now:                       modify

about the same person and the same folder. One of those is wrong, and for an access-auditing
product it is the worse of the two to be wrong about.

:mod:`app.models.current` is the fix: one predicate, stated once, that every query-side read
of a collected object goes through. This suite is the proof, and it is deliberately wider
than the one grant that started it — the same divergence could reappear in any repository,
so every kind, every direction, and every consumer is held here rather than the one endpoint
the bug was first seen through.

## What is asserted, and in what shape

Every removal case runs the same three beats, because two of them are what keep the fix from
being a deletion in disguise:

1. **before** — the estate is collected and the access exists;
2. **after an authoritative reconciliation that does not report the fact** — it is gone from
   current state, *and still reconstructible as of the earlier instant*;
3. **after it is observed again** — it is current once more.

And the negative half, which matters at least as much: a **partial** scan, a **failed** scan
and a **successful run that reconciled nothing** must all leave current state exactly as they
found it. A presence filter that removed access on the strength of a scan that crashed
halfway would be a worse defect than the one it replaced.

The apparatus is ``tests/support/equivalence.py`` — a known estate, collected through the
ordinary ingestion API as the three collector runs a real deployment produces, because
reconciliation is per collector and per scope.
"""

from __future__ import annotations

import copy
import datetime as dt
import uuid
from dataclasses import replace
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_engine import AccessPath
from app.contracts.v1.common import ObservationKind
from app.domain import Direction
from app.history import HistoryService, VersionReader
from app.repositories import MembershipRepository, ResourceRepository
from app.repositories.risk import RiskFactsRepository
from app.services.access import AccessService
from app.services.graph import GraphService
from app.simulation import (
    ChangeKind,
    ChangeOutcome,
    NtfsAceChange,
    ScopeKind,
    SimulationOverlay,
    SimulationScope,
    SimulationService,
)
from tests.support.equivalence import (
    BASE_INSTANT,
    MODIFY,
    READ_EXECUTE,
    AceSpec,
    ControlledEstate,
    EdgeSpec,
    ShareAceSpec,
    estate,
    observe,
    recollect,
)
from tests.support.ingest import replay

pytestmark = pytest.mark.anyio

DOMAIN = "S-1-5-21-1004336348-1177238915-682003330"
ALICE = f"{DOMAIN}-1104"
BOB = f"{DOMAIN}-1105"
FINANCE_RW = f"{DOMAIN}-1202"
FINANCE_RO = f"{DOMAIN}-1203"
EVERYONE = "S-1-1-0"

#: An instant inside the first collection, before anything was removed. The estate stamps
#: generation *n* at ``BASE_INSTANT + n hours``, so half past the first hour is always
#: covered by the first run's versions and never by a later one's.
BEFORE_ANY_REMOVAL = BASE_INSTANT + dt.timedelta(minutes=30)

EDGE_KEY = f"{FINANCE_RW}->{ALICE}|directory_group_member"
OTHER_EDGE_KEY = f"{FINANCE_RO}->{BOB}|directory_group_member"
SHARE_KEY = "fs01|finance"
RESOURCE_KEY = "\\\\fs01\\finance".casefold()

EVERYONE_FULL = ShareAceSpec(EVERYONE, "allow", "full")
FINANCE_RO_READ = ShareAceSpec(FINANCE_RO, "allow", "read", order_index=1)


def _estate(**overrides: Any) -> ControlledEstate:
    """The standard estate, with one extra share entry.

    ``tests/support/equivalence.estate`` publishes a single ``Everyone: Full`` share ACE,
    which makes the NTFS layer the limiting one — exactly right for its own suite and wrong
    for this one. Removing the only entry leaves an ACL with nothing in it, and an empty
    stored share ACL reads as **unread**, not as empty (``_share_dacl_from``): a share ADG
    has never read grants an unknown amount, so the answer would not move and the test would
    be measuring the wrong thing. A second entry keeps the ACL legible after the first is
    taken away.
    """
    return estate(share_aces=(EVERYONE_FULL, FINANCE_RO_READ), **overrides)


def _next_generation(state: ControlledEstate) -> ControlledEstate:
    """The same estate, stamped one hour later.

    Every mutator bumps the generation so a recollection is strictly newer than the run
    before it. A run built from an *unmutated* estate carries the instants of the first
    collection, so its tombstones would open before the instant a history assertion reads
    at — which looks exactly like the fix having eaten history.
    """
    return replace(state, generation=state.generation + 1)


# --------------------------------------------------------------------- collection shapes


def _stamped(document: dict[str, Any]) -> dict[str, Any]:
    """A transcript with fresh identifiers, so a shape can be replayed more than once."""
    fresh = copy.deepcopy(document)
    run_id = str(uuid.uuid4())
    fresh["start"]["run_id"] = run_id
    fresh["completion"]["run_id"] = run_id
    for batch in fresh["batches"]:
        batch["run_id"] = run_id
        batch["batch_id"] = str(uuid.uuid4())
        for observation in batch["observations"]:
            observation["run_id"] = run_id
    return fresh


def _partial(document: dict[str, Any]) -> dict[str, Any]:
    """The same run, reported as a partial scan: it read some of its scope and errored.

    The contract refuses a reconciliation on anything but a clean success, so a partial run
    carries no reconciled scopes — which is the whole point of collecting one here. What it
    did not send is not evidence of absence.
    """
    shaped = _stamped(document)
    shaped["completion"]["status"] = "partial"
    shaped["completion"]["error_count"] = 1
    shaped["completion"]["errors"] = [
        {
            "code": "access_denied",
            "message": "The collector could not read one of the objects in this scope.",
            "target": "\\\\fs01\\finance\\payroll",
        }
    ]
    shaped["completion"]["reconciled_scopes"] = []
    return shaped


def _failed(document: dict[str, Any]) -> dict[str, Any]:
    """The same run, reported as a failure that produced nothing usable."""
    shaped = _partial(document)
    shaped["completion"]["status"] = "failed"
    return shaped


def _unreconciled(document: dict[str, Any]) -> dict[str, Any]:
    """A clean success that declines to claim it enumerated its scope completely."""
    shaped = _stamped(document)
    shaped["completion"]["reconciled_scopes"] = []
    return shaped


def _without_kinds(document: dict[str, Any], kinds: set[str]) -> dict[str, Any]:
    """The same run with some observation kinds simply not reported.

    Not "deleted": a collector reports what it found, and a share that no longer exists
    produces no observation at all. Batches emptied by the filter are dropped and the
    declared counts corrected, because a run that claims to have sent more than it did is
    downgraded — which would take the reconciliation away and defeat the test.
    """
    shaped = _stamped(document)
    batches = []
    sent = 0
    for batch in shaped["batches"]:
        kept = [item for item in batch["observations"] if item["kind"] not in kinds]
        if not kept:
            continue
        batch["observations"] = kept
        sent += len(kept)
        batches.append(batch)
    shaped["batches"] = batches
    shaped["completion"]["batch_count"] = len(batches)
    shaped["completion"]["observation_count"] = sent
    return shaped


async def _collect(client: AsyncClient, state: ControlledEstate) -> None:
    """The three reconciling collector runs, which is what an ordinary scan looks like."""
    await recollect(client, state)


# ------------------------------------------------------------------------- readings


async def _rights(session: AsyncSession, subject_key: str, resource_key: str) -> int:
    """The ordinary current-state effective-access answer, in mask bits."""
    service = AccessService(ResourceRepository(session), MembershipRepository(session))
    resolved = await service.effective_access(subject_key, resource_key)
    return resolved.access.rights.value


async def _rights_at(
    session: AsyncSession, subject_key: str, resource_key: str, at: dt.datetime
) -> int:
    """The point-in-time answer, which has always been the correct one."""
    answer = await HistoryService(session).effective_access_at(subject_key, resource_key, at)
    return answer.access.access.rights.value


async def _edge_keys_down(session: AsyncSession, group_key: str) -> set[str]:
    neighbors = await MembershipRepository(session).neighbors(Direction.DOWN, [group_key])
    return {edge.edge_key for edge in neighbors[group_key]}


async def _effective_member_keys(session: AsyncSession, group_key: str) -> set[str]:
    expansion = await GraphService(MembershipRepository(session)).effective_members(group_key)
    return {node.key for node in expansion.nodes}


async def _token_keys(session: AsyncSession, subject_key: str) -> set[str]:
    """The SIDs an access check would see for this subject, built the production way."""
    service = AccessService(ResourceRepository(session), MembershipRepository(session))
    page = await service.accessible_resources(subject_key, limit=1)
    return set(page.token.keys)


# ---------------------------------------------------------------- the defect itself


class TestTheDefectThisFixes:
    """The pre-fix reproduction, kept as the headline case.

    Before :mod:`app.models.current`, every assertion below about the live reading failed
    and every one about the as-of reading passed. That split *was* the defect, and it was
    pinned in ``tests/db/test_simulation_equivalence.py`` as a known limitation; this is the
    same measurement, now asserting that the two readings agree.
    """

    async def test_a_reconciled_away_membership_stops_granting_access_live_as_well(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate()
        await _collect(client, before)

        first = await observe(session, [(ALICE, before.resource_key)])
        reading = first[(ALICE, before.resource_key)]
        assert reading.as_of == reading.live == MODIFY, "the estate grants Modify to begin with"

        await _collect(client, before.without_membership(FINANCE_RW, ALICE))
        session.expire_all()

        after = await observe(session, [(ALICE, before.resource_key)])
        reading = after[(ALICE, before.resource_key)]
        assert reading.as_of == 0, "the point-in-time engine has always seen the removal"
        assert reading.live == 0, (
            "and the current-state engine now sees it too: an authoritative reconciliation "
            "that did not report the edge is what makes the edge not current"
        )

    async def test_the_row_is_still_there_and_history_still_reads_it(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The fix must be a *selection* change, never a deletion.

        If the edge row were gone from ``membership_edges`` the live answer would also be
        zero and every assertion above would pass — while the product had quietly destroyed
        the evidence that Alice ever had access. So the row, its whole timeline, and the
        as-of answer over it are all asserted to survive.
        """
        before = _estate()
        await _collect(client, before)
        await _collect(client, before.without_membership(FINANCE_RW, ALICE))
        session.expire_all()

        stored = await session.execute(
            sa.text("SELECT count(*) FROM membership_edges WHERE edge_key = :key"),
            {"key": EDGE_KEY},
        )
        assert stored.scalar_one() == 1, "nothing was deleted"

        timeline = await HistoryService(session).timeline(ObservationKind.MEMBERSHIP_EDGE, EDGE_KEY)
        assert len(timeline) == 2, "the present version, then the tombstone that closed it"
        assert timeline.versions[0].is_present
        assert timeline.versions[0].state is not None
        assert timeline.versions[-1].is_tombstone
        assert timeline.versions[-1].is_open

        assert await _rights_at(session, ALICE, before.resource_key, BEFORE_ANY_REMOVAL) == MODIFY


# ------------------------------------------------------------------- membership edges


class TestMembershipCurrentState:
    """Phase 5: every way a membership edge reaches an answer."""

    async def test_every_membership_read_drops_a_reconciled_away_edge(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate()
        await _collect(client, before)
        repository = MembershipRepository(session)

        members = await repository.direct_members(FINANCE_RW)
        assert ALICE in {item.counterpart_key for item in members.items}
        groups = await repository.direct_groups(ALICE)
        assert FINANCE_RW in {item.counterpart_key for item in groups.items}
        assert EDGE_KEY in await _edge_keys_down(session, FINANCE_RW)
        assert ALICE in await _effective_member_keys(session, FINANCE_RW)
        assert FINANCE_RW in await _token_keys(session, ALICE)
        assert await repository.keys_with_members([FINANCE_RW]) == frozenset({FINANCE_RW})
        assert await repository.count_direct(group_key=FINANCE_RW) == 1

        await _collect(client, before.without_membership(FINANCE_RW, ALICE))
        session.expire_all()
        repository = MembershipRepository(session)

        members = await repository.direct_members(FINANCE_RW)
        assert members.items == (), "the direct member list no longer holds the edge"
        groups = await repository.direct_groups(ALICE)
        assert groups.items == (), "and neither does the inverse listing"
        assert await _edge_keys_down(session, FINANCE_RW) == set(), "nor graph adjacency"
        assert await _effective_member_keys(session, FINANCE_RW) == set(), "nor the closure"
        assert FINANCE_RW not in await _token_keys(session, ALICE), (
            "the access token is built from the traversal, so the group SID is gone from it "
            "— which is what actually stops the grant"
        )
        assert await repository.keys_with_members([FINANCE_RW]) == frozenset(), (
            "a group whose only edge was reconciled away is a group nobody is inside, not a "
            "group nobody has looked inside"
        )
        assert await repository.count_direct(group_key=FINANCE_RW) == 0

    async def test_the_edge_is_current_again_the_moment_it_is_observed_again(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Reappearance needs no special case: observing it closes the tombstone."""
        before = _estate()
        await _collect(client, before)
        removed = before.without_membership(FINANCE_RW, ALICE)
        await _collect(client, removed)
        session.expire_all()
        assert await _rights(session, ALICE, before.resource_key) == 0

        await _collect(client, removed.with_membership(EdgeSpec(FINANCE_RW, ALICE)))
        session.expire_all()

        assert await _rights(session, ALICE, before.resource_key) == MODIFY
        assert EDGE_KEY in await _edge_keys_down(session, FINANCE_RW)
        timeline = await HistoryService(session).timeline(ObservationKind.MEMBERSHIP_EDGE, EDGE_KEY)
        assert [version.is_present for version in timeline.versions] == [True, False, True]

    async def test_removing_one_edge_leaves_the_other_alone(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The filter is per object. A reconciliation that removes Alice's membership must
        not touch Bob's, and a predicate keyed on the wrong column would take both."""
        before = _estate()
        await _collect(client, before)
        await _collect(client, before.without_membership(FINANCE_RW, ALICE))
        session.expire_all()

        assert await _rights(session, ALICE, before.resource_key) == 0
        assert await _rights(session, BOB, before.resource_key) == READ_EXECUTE


# ----------------------------------------------------------------------- SMB ACEs


class TestSmbAceCurrentState:
    """Phase 6: the share ACL, raw and resolved."""

    async def test_a_reconciled_away_share_ace_leaves_the_raw_acl_and_the_verdict(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        r"""``Everyone: Full`` is what lets Alice through the share layer at all.

        Take it away and the ACL still has ``Finance-RO: Read`` in it — so the share is
        still one ADG has read, and Alice is simply not named on it. Her remote access
        collapses to nothing while Bob's is untouched. That pair is the assertion: the one
        entry stopped counting, and nothing else did.
        """
        before = _estate()
        await _collect(client, before)

        resources = ResourceRepository(session)
        assert {ace.trustee_sid for ace in await resources.full_share_acl(SHARE_KEY)} == {
            EVERYONE,
            FINANCE_RO,
        }
        assert await resources.count_acl(SHARE_KEY) == 2
        assert await _rights(session, ALICE, before.resource_key) == MODIFY

        await _collect(client, before.without_share_ace(EVERYONE_FULL))
        session.expire_all()
        resources = ResourceRepository(session)

        assert {ace.trustee_sid for ace in await resources.full_share_acl(SHARE_KEY)} == {
            FINANCE_RO
        }, "the raw current ACL no longer holds the entry"
        entries, _ = await resources.share_acl(SHARE_KEY)
        assert EVERYONE not in {ace.trustee_sid for ace in entries}
        assert await resources.count_acl(SHARE_KEY) == 1
        assert EVERYONE not in {
            ace.trustee_sid for ace in (await resources.share_acls_for([SHARE_KEY]))[SHARE_KEY]
        }
        assert await resources.count_shares_referencing(trustee_key=EVERYONE) == 0, (
            "and the inverse question agrees with it"
        )
        assert (await resources.shares_referencing(trustee_key=EVERYONE)).items == ()

        assert await _rights(session, ALICE, before.resource_key) == 0, (
            "no share ACE names her, so the remote path grants nothing — the resolver "
            "consuming the same current-state ACL the raw view showed"
        )
        assert await _rights(session, BOB, before.resource_key) == READ_EXECUTE
        assert (
            await _rights_at(session, ALICE, before.resource_key, BEFORE_ANY_REMOVAL) == MODIFY
        ), "while the interval the entry was valid over still resolves to Modify"

    async def test_the_share_ace_is_current_again_when_it_is_observed_again(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate()
        await _collect(client, before)
        removed = before.without_share_ace(EVERYONE_FULL)
        await _collect(client, removed)
        session.expire_all()
        assert await _rights(session, ALICE, before.resource_key) == 0

        await _collect(client, removed.with_share_ace(EVERYONE_FULL))
        session.expire_all()

        assert await _rights(session, ALICE, before.resource_key) == MODIFY
        assert len(await ResourceRepository(session).full_share_acl(SHARE_KEY)) == 2


# ---------------------------------------------------------------------- NTFS ACEs


class TestNtfsAceCurrentState:
    """Phase 7: the file-system ACL, and the affirmation that must not disturb it."""

    async def test_a_reconciled_away_ntfs_ace_leaves_the_raw_acl_and_the_verdict(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate()
        target = before.ntfs_aces[0]  # Finance-RW, Modify
        target_key = before.ntfs_ace_key(target)
        await _collect(client, before)

        resources = ResourceRepository(session)
        assert target_key in {ace.ace_key for ace in await resources.full_ntfs_acl(RESOURCE_KEY)}
        assert await resources.count_ntfs_acl(RESOURCE_KEY) == 2
        assert await _rights(session, ALICE, before.resource_key) == MODIFY

        await _collect(client, before.without_ntfs_ace(target))
        session.expire_all()
        resources = ResourceRepository(session)

        remaining = await resources.full_ntfs_acl(RESOURCE_KEY)
        assert target_key not in {ace.ace_key for ace in remaining}
        assert len(remaining) == 1, "Bob's Read & Execute entry is untouched"
        entries, _ = await resources.ntfs_acl(RESOURCE_KEY)
        assert target_key not in {ace.ace_key for ace in entries}
        assert await resources.count_ntfs_acl(RESOURCE_KEY) == 1
        assert target_key not in {
            ace.ace_key for ace in (await resources.ntfs_acls_for([RESOURCE_KEY]))[RESOURCE_KEY]
        }

        assert await _rights(session, ALICE, before.resource_key) == 0
        assert await _rights(session, BOB, before.resource_key) == READ_EXECUTE
        assert (
            await _rights_at(session, ALICE, before.resource_key, BEFORE_ANY_REMOVAL) == MODIFY
        ), "history retains the prior state and still resolves it"

    async def test_the_recomputed_acl_hash_is_taken_over_current_entries_only(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The stored-versus-reported digest comparison is a correctness surface of its own.

        Hashing a DACL that still held an entry a reconciled scan proved gone would make
        every re-read of the directory report a mismatch against a descriptor that is in
        fact exactly what the collector sent — and it is that same digest that contract 1.4
        affirmations are checked against.
        """
        before = _estate()
        await _collect(client, before)
        session.expire_all()
        resources = ResourceRepository(session)
        resource = await resources.get_ntfs_resource(RESOURCE_KEY)
        assert resource is not None
        two_entries = (await resources.recompute_acl_hash(resource)).computed

        await _collect(client, before.without_ntfs_ace(before.ntfs_aces[0]))
        session.expire_all()
        resources = ResourceRepository(session)
        resource = await resources.get_ntfs_resource(RESOURCE_KEY)
        assert resource is not None
        recomputation = await resources.recompute_acl_hash(resource)

        assert recomputation.stored_ace_count == 1, "one entry, not two"
        assert recomputation.declared_ace_count == 1, "which is what the collector declared"
        assert recomputation.computed != two_entries, (
            "and the digest moved with the removal rather than being pinned by a row that "
            "is no longer current"
        )

    async def test_an_unchanged_affirmation_does_not_expire_the_entries_it_covers(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Contract 1.4, unchanged and still correct.

        An affirmation says *your copy is still right*, and it confirms the resource **and
        the entries its digest covers** — so the next reconciliation does not treat them as
        unseen. That was true before this change and has to stay true after it: an
        affirmation that quietly let its own run tombstone a live ACL would be the same
        defect wearing the opposite sign.
        """
        before = _estate()
        await _collect(client, before)
        session.expire_all()
        resources = ResourceRepository(session)
        resource = await resources.get_ntfs_resource(RESOURCE_KEY)
        assert resource is not None
        digest = (await resources.recompute_acl_hash(resource)).computed

        # The estate's transcript predates contract 1.2 in this one respect: it declares no
        # acl_hash. One ordinary run that carries the digest ADG itself computes, so that
        # the affirmation afterwards has something to be checked against.
        published = _next_generation(before)
        await replay(client, _with_acl_hash(published.collector_runs()[2], digest))
        session.expire_all()

        affirmed = _affirming_ntfs_run(_next_generation(published), digest)
        applied = (await replay(client, affirmed))["batches"][0]
        assert applied["refused_affirmations"] == [], applied
        assert applied["affirmed"] == 1, applied
        session.expire_all()

        resources = ResourceRepository(session)
        assert await resources.get_ntfs_resource(RESOURCE_KEY) is not None
        assert len(await resources.full_ntfs_acl(RESOURCE_KEY)) == 2, (
            "the affirmation confirmed the entries as well as the descriptor, so the "
            "reconciliation it carried marked none of them absent"
        )
        assert await _rights(session, ALICE, before.resource_key) == MODIFY


def _with_acl_hash(document: dict[str, Any], digest: str) -> dict[str, Any]:
    """The same NTFS run, with the directory's whole-DACL digest declared (contract 1.2)."""
    shaped = _stamped(document)
    for batch in shaped["batches"]:
        for observation in batch["observations"]:
            if observation["kind"] == "ntfs_resource":
                observation["acl_hash"] = digest
    return shaped


def _affirming_ntfs_run(state: ControlledEstate, digest: str) -> dict[str, Any]:
    r"""An NTFS run that re-read the directory, found it unchanged, and says so.

    A contract 1.4 batch carrying one affirmation instead of the resource and its entries,
    reconciling the same ``directory_tree`` scope the ordinary run reconciles. If the
    affirmation did not confirm the entries, this run would be a reconciliation that saw
    none of them and the whole DACL would be tombstoned by it.
    """
    run_id = str(uuid.uuid4())
    started = (BASE_INSTANT + dt.timedelta(hours=state.generation)).isoformat()
    observed_at = started.replace("+00:00", "Z")
    scopes = [{"kind": "directory_tree", "key": state.resource_key}]
    return {
        "start": {
            "schema_version": "1.4",
            "run_id": run_id,
            "source": {
                "collector": "ntfs",
                "collector_host": "COLLECTOR01",
                "method": "System.IO.DirectoryInfo.GetAccessControl",
                "collector_version": "0.1.0",
                "target": state.path,
            },
            "started_at": observed_at,
            "scopes": scopes,
            "incremental": False,
        },
        "batches": [
            {
                "schema_version": "1.4",
                "run_id": run_id,
                "batch_id": str(uuid.uuid4()),
                "sequence": 1,
                "observations": [],
                "affirmations": [
                    {
                        "kind": "ntfs_resource",
                        "source_key": f"resource|{state.resource_key}",
                        "digest": digest,
                        "observed_at": observed_at,
                    }
                ],
            }
        ],
        "completion": {
            "schema_version": "1.4",
            "run_id": run_id,
            "status": "succeeded",
            "completed_at": observed_at,
            "batch_count": 1,
            "observation_count": 0,
            "affirmation_count": 1,
            "error_count": 0,
            "errors": [],
            "reconciled_scopes": scopes,
        },
    }


# ------------------------------------------------------- shares and resources themselves


class TestResourceDisappearance:
    """Phase 8: the container, not just what it contains."""

    async def test_a_share_its_source_stops_reporting_leaves_current_state(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate()
        await _collect(client, before)
        resources = ResourceRepository(session)
        assert await resources.get_share(SHARE_KEY) is not None
        assert await resources.count_shares("fs01") == 1

        # An SMB run that enumerated the server completely and found the share gone: the
        # server is still reported, the share and its ACL simply are not.
        gone = _next_generation(before).collector_runs()[1]
        await replay(client, _without_kinds(gone, {"smb_share", "smb_ace"}))
        session.expire_all()
        resources = ResourceRepository(session)

        assert await resources.get_share(SHARE_KEY) is None
        assert await resources.shares_by_keys([SHARE_KEY]) == {}
        assert (await resources.list_shares("fs01")).items == ()
        assert await resources.count_shares("fs01") == 0
        server = await resources.get_server("fs01")
        assert server is not None, "the server itself is still there"
        assert server.share_count == 0

        presence = await HistoryService(session).presence_at(
            ObservationKind.SMB_SHARE, SHARE_KEY, BEFORE_ANY_REMOVAL
        )
        assert presence.exists is True, "history still holds the share over its own interval"

    async def test_an_ntfs_resource_its_source_stops_reporting_leaves_current_state(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate()
        await _collect(client, before)
        assert await ResourceRepository(session).get_ntfs_resource(RESOURCE_KEY) is not None

        gone = _next_generation(before).collector_runs()[2]
        await replay(client, _without_kinds(gone, {"ntfs_resource", "ntfs_ace"}))
        session.expire_all()
        resources = ResourceRepository(session)

        assert await resources.get_ntfs_resource(RESOURCE_KEY) is None
        assert await resources.ntfs_resources_by_keys([RESOURCE_KEY]) == {}
        assert await resources.get_share_root_resource(SHARE_KEY) is None
        assert await resources.full_ntfs_acl(RESOURCE_KEY) == ()
        assert await resources.has_ntfs_aces(RESOURCE_KEY) is False

        presence = await HistoryService(session).resource_exists_at(
            RESOURCE_KEY, BEFORE_ANY_REMOVAL
        )
        assert presence.exists is True

    async def test_a_removed_resource_is_no_longer_offered_as_a_candidate(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """``resources_named_by`` is the bounded candidate set behind *what can this
        principal reach*, and it is built from an append-only reference index. A directory
        that has gone away must drop out of it — otherwise the answer keeps a row for a path
        nothing describes any more, which is the removed resource presented as live."""
        before = _estate()
        await _collect(client, before)
        resources = ResourceRepository(session)
        assert RESOURCE_KEY in (await resources.resources_named_by([FINANCE_RW])).items

        gone = _next_generation(before).collector_runs()[2]
        await replay(client, _without_kinds(gone, {"ntfs_resource", "ntfs_ace"}))
        session.expire_all()

        page = await ResourceRepository(session).resources_named_by([ALICE, FINANCE_RW])
        assert RESOURCE_KEY not in page.items

    async def test_a_removed_share_is_no_longer_offered_as_a_candidate(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate()
        await _collect(client, before)
        assert SHARE_KEY in (await ResourceRepository(session).shares_named_by([EVERYONE])).items

        gone = _next_generation(before).collector_runs()[1]
        await replay(client, _without_kinds(gone, {"smb_share", "smb_ace"}))
        session.expire_all()

        page = await ResourceRepository(session).shares_named_by([EVERYONE, FINANCE_RO])
        assert page.items == ()


# ------------------------------------------------- what may and may not remove a fact


class TestOnlyAnAuthoritativeReconciliationRemoves:
    """Phases 8 and 9, negative half. The dangerous direction.

    A presence filter is only as safe as the thing that writes the tombstones. These are the
    cases where a fact must **survive** a run that did not report it.
    """

    @pytest.mark.parametrize("shape", ["partial", "failed", "unreconciled"])
    async def test_a_scan_that_may_not_reconcile_removes_nothing(
        self, client: AsyncClient, session: AsyncSession, shape: str
    ) -> None:
        before = _estate()
        await _collect(client, before)

        ad_run = before.without_membership(FINANCE_RW, ALICE).collector_runs()[0]
        shaped = {"partial": _partial, "failed": _failed, "unreconciled": _unreconciled}[shape]
        await replay(client, shaped(ad_run))
        session.expire_all()

        assert await _rights(session, ALICE, before.resource_key) == MODIFY, (
            f"a {shape} run did not report the edge, and not reporting something is not "
            "evidence that it is gone"
        )
        assert EDGE_KEY in await _edge_keys_down(session, FINANCE_RW)

    async def test_an_authoritative_scan_of_the_same_shape_does_remove_it(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The control for the three above: the *only* difference is the reconciliation."""
        before = _estate()
        await _collect(client, before)

        ad_run = _stamped(before.without_membership(FINANCE_RW, ALICE).collector_runs()[0])
        await replay(client, ad_run)
        session.expire_all()

        assert await _rights(session, ALICE, before.resource_key) == 0
        assert EDGE_KEY not in await _edge_keys_down(session, FINANCE_RW)

    async def test_replaying_the_same_observation_changes_nothing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Idempotence, over the filter as well as over the tables."""
        before = _estate()
        await _collect(client, before)
        session.expire_all()
        first = await _rights(session, ALICE, before.resource_key)
        first_acl = len(await ResourceRepository(session).full_ntfs_acl(RESOURCE_KEY))

        await _collect(client, before)
        session.expire_all()

        assert await _rights(session, ALICE, before.resource_key) == first == MODIFY
        assert len(await ResourceRepository(session).full_ntfs_acl(RESOURCE_KEY)) == first_acl

    async def test_a_collector_cannot_remove_what_it_cannot_see(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """An SMB run reconciling its server says nothing about the NTFS half of it.

        The closure rules already enforce this; asserting it here is what keeps the presence
        filter from being handed a wider tombstone than the collector earned.
        """
        before = _estate()
        await _collect(client, before)

        await replay(client, _stamped(_next_generation(before).collector_runs()[1]))
        session.expire_all()

        resources = ResourceRepository(session)
        assert await resources.get_ntfs_resource(RESOURCE_KEY) is not None
        assert len(await resources.full_ntfs_acl(RESOURCE_KEY)) == 2
        assert await _rights(session, ALICE, before.resource_key) == MODIFY


# ------------------------------------------------------------ the whole matrix at once


REMOVALS = ("membership", "share_ace", "ntfs_ace")


def _removed(state: ControlledEstate, case: str) -> ControlledEstate:
    if case == "membership":
        return state.without_membership(FINANCE_RW, ALICE)
    if case == "share_ace":
        return state.without_share_ace(EVERYONE_FULL)
    if case == "ntfs_ace":
        return state.without_ntfs_ace(state.ntfs_aces[0])
    raise AssertionError(case)


def _restored(state: ControlledEstate, case: str) -> ControlledEstate:
    if case == "membership":
        return state.with_membership(EdgeSpec(FINANCE_RW, ALICE))
    if case == "share_ace":
        return state.with_share_ace(EVERYONE_FULL)
    if case == "ntfs_ace":
        return state.with_ntfs_ace(AceSpec(FINANCE_RW, "allow", MODIFY))
    raise AssertionError(case)


class TestTheAccessRegressionMatrix:
    """Phase 9, compactly: every access-producing fact, removed and re-added.

    One subject, one resource, three facts that each carry the whole grant on their own —
    so for each of them the answer must be Modify, then nothing, then Modify again, and the
    as-of reading over the first interval must be Modify throughout.
    """

    @pytest.mark.parametrize("case", REMOVALS)
    async def test_before_removal_after_removal_after_readd(
        self, client: AsyncClient, session: AsyncSession, case: str
    ) -> None:
        before = _estate()
        await _collect(client, before)
        session.expire_all()
        assert await _rights(session, ALICE, before.resource_key) == MODIFY

        removed = _removed(before, case)
        await _collect(client, removed)
        session.expire_all()
        assert await _rights(session, ALICE, before.resource_key) == 0, (
            f"removing the {case} removes the access"
        )
        assert (
            await _rights_at(session, ALICE, before.resource_key, BEFORE_ANY_REMOVAL) == MODIFY
        ), "and the as-of reading over the earlier interval is unchanged by that"

        await _collect(client, _restored(removed, case))
        session.expire_all()
        assert await _rights(session, ALICE, before.resource_key) == MODIFY, (
            f"and observing the {case} again brings it back"
        )


class TestCurrentAgreesWithAsOfTheLatestState:
    """Phase 10: the invariant that keeps this from regressing somewhere else.

    For a fully reconciled estate, the ordinary current-state answer and the point-in-time
    answer at the recollection instant are answers to the same question, so they must be the
    same number. This is the assertion that would catch a *new* repository reading a table
    directly, long after the ones fixed here have been forgotten.
    """

    @pytest.mark.parametrize("case", REMOVALS)
    async def test_the_two_readings_agree_after_a_removal(
        self, client: AsyncClient, session: AsyncSession, case: str
    ) -> None:
        before = _estate()
        await _collect(client, before)
        await _collect(client, _removed(before, case))
        session.expire_all()

        pairs = [(ALICE, before.resource_key), (BOB, before.resource_key)]
        observed = await observe(session, pairs)
        for pair in pairs:
            assert observed[pair].as_of == observed[pair].live, (
                f"{pair[0]} reads differently live and as-of after the {case} removal"
            )

    async def test_the_two_readings_agree_on_the_local_path_too(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The local path skips the share ACL entirely, so it exercises a different
        combination of the same current-state sources."""
        before = _estate()
        await _collect(client, before)
        await _collect(client, before.without_ntfs_ace(before.ntfs_aces[0]))
        session.expire_all()

        observed = await observe(session, [(ALICE, before.resource_key)], path=AccessPath.LOCAL)
        reading = observed[(ALICE, before.resource_key)]
        assert reading.as_of == reading.live == 0


# ----------------------------------------------------------- the derived consumers


class TestRiskFactsExcludeExpiredSourceFacts:
    """Phase 11: a risk snapshot must not be built out of reconciled-away access.

    Only the *source* facts are asserted here. What the finding lifecycle then does with an
    open finding — resolve it, leave it for the next reconciliation pass — is a separate
    mechanism with its own tests, and this change deliberately does not touch it.
    """

    async def test_a_removed_ace_is_not_in_the_fact_bundle(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate(
            ntfs_aces=(
                AceSpec(EVERYONE, "allow", MODIFY),
                AceSpec(FINANCE_RO, "allow", READ_EXECUTE, order_index=1),
            )
        )
        await _collect(client, before)
        session.expire_all()

        assert EVERYONE in _fact_trustees(await _facts(session)), (
            "the broad entry is a fact to begin with"
        )

        await _collect(client, before.without_ntfs_ace(before.ntfs_aces[0]))
        session.expire_all()

        assert EVERYONE not in _fact_trustees(await _facts(session)), (
            "a newly evaluated risk snapshot is built from current access facts, and the "
            "broad entry is no longer one"
        )

    async def test_a_removed_membership_is_not_in_the_fact_bundle(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate()
        await _collect(client, before)
        session.expire_all()
        facts = await _facts(session)
        assert ALICE in facts.memberships[FINANCE_RW].member_keys

        await _collect(client, before.without_membership(FINANCE_RW, ALICE))
        session.expire_all()

        facts = await _facts(session)
        membership = facts.memberships.get(FINANCE_RW)
        assert membership is None or ALICE not in membership.member_keys


async def _facts(session: AsyncSession) -> Any:
    return (await RiskFactsRepository(session).load()).facts


def _fact_trustees(facts: Any) -> set[str]:
    """Every SID named by an NTFS entry in the bundle."""
    return {ace.trustee_sid for resource in facts.resources for ace in resource.aces}


class TestSimulationBaselineExcludesExpiredSourceFacts:
    """Phase 12: a simulation starts from valid current state.

    The mathematics is untouched; what changes is which rows the baseline is built from. A
    proposal to remove an entry a reconciled scan already proved gone must report that there
    is nothing there to remove, rather than computing an impact from a grant that does not
    exist. Before this change that was the one case in the simulation surface where the
    report was confidently wrong rather than merely bounded, and the documented workaround
    was to pass an ``as_of`` baseline instead.
    """

    async def test_a_current_baseline_does_not_hold_a_reconciled_away_entry(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate()
        target = before.ntfs_aces[1]  # Finance-RO, Read & Execute
        await _collect(client, before)
        await _collect(client, before.without_ntfs_ace(target))
        session.expire_all()

        overlay = SimulationOverlay.from_changes(
            [
                NtfsAceChange(
                    kind=ChangeKind.REMOVE_NTFS_ACE,
                    resource_key=before.path,
                    ace_key=before.ntfs_ace_key(target),
                )
            ]
        )
        report = await SimulationService(session).run(
            overlay,
            scope=SimulationScope(
                kind=ScopeKind.PAIR, subject_key=BOB, resource_key=before.resource_key
            ),
        )

        assert report.applications[0].outcome is ChangeOutcome.TARGET_NOT_FOUND
        assert report.inert is True
        assert all(delta.rights_removed.value == 0 for delta in report.deltas), (
            "nothing can be lost by removing an entry that is not there"
        )


# -------------------------------------------------------------------- the API surface


class TestTheApiSurfacesDoNotShowExpiredState:
    """Phase 13: the same facts, through the endpoints that actually serve them.

    Every response shape is unchanged; the only difference is which rows reach it. Asserted
    through HTTP rather than through the repositories because an endpoint that assembled its
    own query would be the one place this fix could still have missed.
    """

    async def test_the_current_facing_endpoints_agree_that_the_grant_is_gone(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate()
        await _collect(client, before)

        pair = f"/api/v1/access/principals/{ALICE}/resources/{_quoted(before.path)}"
        assert (await client.get(pair)).json()["effective"]["rights"]["value"] == MODIFY

        await _collect(client, before.without_membership(FINANCE_RW, ALICE))

        assert (await client.get(pair)).json()["effective"]["rights"]["value"] == 0, (
            "principal -> resource"
        )

        listed = await client.get(
            f"/api/v1/access/resources/{_quoted(before.path)}/principals?members=all"
        )
        assert ALICE not in {item["principal"]["key"] for item in listed.json()["items"]}, (
            "resource -> principals"
        )

        members = (await client.get(f"/api/v1/groups/{FINANCE_RW}/members")).json()
        assert members["items"] == [], "group membership view"

        groups = (await client.get(f"/api/v1/principals/{ALICE}/groups")).json()
        assert groups["items"] == [], "the inverse membership view"

        explanation = await client.get(
            f"/api/v1/access/paths/principals/{ALICE}/resources/{_quoted(before.path)}"
        )
        assert explanation.json()["effective"]["rights"]["value"] == 0, "access explanation"

    async def test_a_removed_ace_leaves_the_raw_acl_endpoints(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate()
        target = before.ntfs_aces[0]
        url = f"/api/v1/resources/{_quoted(before.path)}/acl"
        await _collect(client, before)
        assert len((await client.get(url)).json()["entries"]) == 2

        await _collect(client, before.without_ntfs_ace(target))

        entries = (await client.get(url)).json()["entries"]
        assert len(entries) == 1
        assert before.ntfs_ace_key(target) not in {entry["ace_key"] for entry in entries}

    async def test_a_removed_share_leaves_the_inventory_endpoints(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate()
        await _collect(client, before)
        assert (await client.get("/api/v1/servers/fs01/shares")).json()["items"]

        gone = _next_generation(before).collector_runs()[1]
        await replay(client, _without_kinds(gone, {"smb_share", "smb_ace"}))

        assert (await client.get("/api/v1/servers/fs01/shares")).json()["items"] == []
        assert (await client.get(f"/api/v1/shares/{_quoted(SHARE_KEY)}")).status_code == 404

        counts = (await client.get("/api/v1/collection/operations")).json()["counts"]
        assert counts["shares"] == 0, (
            "the operator inventory counts what is current, not what has ever been stored"
        )
        assert counts["share_aces"] == 0
        assert counts["servers"] == 1, "and the server it published is still there"


def _quoted(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


# ------------------------------------------------------------------- the mechanism


class TestTheMechanismItself:
    """The predicate, held directly, so a failure above can be localized."""

    async def test_a_tombstone_is_what_removes_a_row_and_nothing_else_is(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        before = _estate()
        await _collect(client, before)
        await _collect(client, before.without_membership(FINANCE_RW, ALICE))
        session.expire_all()

        absent = await VersionReader(session).absent_now(ObservationKind.MEMBERSHIP_EDGE)
        keys = {version.key for version in absent}
        assert keys == {EDGE_KEY}, (
            "exactly one open tombstone, and it is the edge that was reconciled away — "
            f"{OTHER_EDGE_KEY} is untouched"
        )

    async def test_every_tracked_kind_has_a_current_state_source(self) -> None:
        """A kind added to the contract and not to the filter would be a kind that keeps
        answering out of history forever, invisibly."""
        from app.history.bindings import BINDINGS
        from app.models.current import CURRENT_STATE, CURRENT_STATE_KEYS

        assert set(CURRENT_STATE) == set(ObservationKind)
        assert set(CURRENT_STATE_KEYS) == set(ObservationKind)
        for kind, binding in BINDINGS.items():
            table, key_column = CURRENT_STATE_KEYS[kind]
            assert binding.table is table
            assert binding.key_column == key_column
