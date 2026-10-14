"""The relationship store against PostgreSQL, over facts ingested through the real API.

Every relationship here rests on evidence the ingestion endpoint actually stored — scenario 09's
host-local ``BUILTIN\\Administrators``, its local membership edge, the folder it owns and the
full-control entry it holds, and scenario 03's directory membership chain — so evidence linkage
is tested against real rows, real runs and real history, not against keys a test made up.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import pathlib
import sys
import uuid
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.relationships import (
    RELATIONSHIP_TAXONOMY,
    UNRESOLVED_QUALIFIERS,
    AdjacencyDirection,
    DerivationClass,
    DerivationRule,
    EvidenceKind,
    EvidenceRole,
    EvidenceState,
    Materialization,
    NodeKind,
    NodeRef,
    RelationshipAssertion,
    RelationshipCondition,
    RelationshipConfidence,
    RelationshipCurrency,
    RelationshipEvidence,
    RelationshipQualifier,
    RelationshipScope,
    RelationshipScopeKind,
    RelationshipType,
)
from app.models.schema import (
    RELATIONSHIP_ID_SQL,
    graph_relationship_evidence,
    graph_relationship_supports,
    graph_relationships,
    membership_edges,
    ntfs_aces,
    ntfs_resources,
    object_versions,
    observations,
)
from app.repositories.basis import CollectionBasisRepository
from app.repositories.relationships import (
    EvidenceNotStoredError,
    RecordOutcome,
    RelationshipRepository,
)
from tests.fixtures import load_raw
from tests.support.ingest import ingest_storable_scenario, replay, storable, with_run_id

NOW = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.UTC)
LATER = NOW + dt.timedelta(hours=1)
DOMAIN = "S-1-5-21-1004336348-1177238915-682003330"
FINANCE_RW = NodeRef.principal(f"{DOMAIN}-1202")
FS01_ADMINS = NodeRef.principal("fs01|S-1-5-32-544")
PAYROLL = NodeRef(NodeKind.NTFS_RESOURCE, "\\\\fs01\\finance\\payroll")
FS01 = RelationshipScope.host("fs01")
SCENARIO = "09-broken-inheritance"
PAYROLL_ACE_SOURCE = "ntfs_ace|\\\\fs01\\finance\\payroll|S-1-5-32-544|allow|0x001f01ff|0x03"

VERSIONS = pathlib.Path(__file__).resolve().parents[3] / "database" / "migrations" / "versions"
REVISION_PATH = VERSIONS / "0018_graph_relationships.py"
DERIVATION_REVISION_PATH = VERSIONS / "0019_graph_derivation.py"


class Estate:
    """The stored rows the relationships below rest on, read back after ingestion."""

    def __init__(self, rows: dict[str, Any], basis: str) -> None:
        self.rows = rows
        self.basis = basis

    def evidence(
        self,
        name: str,
        kind: EvidenceKind,
        key_column: str,
        role: EvidenceRole = EvidenceRole.PREMISE,
    ) -> RelationshipEvidence:
        row = self.rows[name]
        return RelationshipEvidence(
            kind, row[key_column], row["last_observed_run_id"], row["last_observed_at"], role
        )

    def admins_edge(self, **overrides: Any) -> RelationshipEvidence:
        evidence = self.evidence("admins_edge", EvidenceKind.MEMBERSHIP_EDGE, "edge_key")
        return _replace(evidence, **overrides)

    def payroll_ace(self, **overrides: Any) -> RelationshipEvidence:
        return _replace(self.evidence("payroll_ace", EvidenceKind.NTFS_ACE, "ace_key"), **overrides)

    def payroll(self, **overrides: Any) -> RelationshipEvidence:
        evidence = self.evidence("payroll", EvidenceKind.NTFS_RESOURCE, "resource_key")
        return _replace(evidence, **overrides)


def _replace(evidence: RelationshipEvidence, **overrides: Any) -> RelationshipEvidence:
    values = {
        "kind": evidence.kind,
        "key": evidence.key,
        "run_id": evidence.run_id,
        "observed_at": evidence.observed_at,
        "role": evidence.role,
        **overrides,
    }
    return RelationshipEvidence(**values)


@pytest.fixture(autouse=True)
def without_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests exercise the relationship *store*, so they keep it to themselves.

    Since ADG-GRAPH-01B every completion runs the projector, which would derive these very
    relationships from the scenarios as they are ingested — under its own rule ids, at its
    own instants — and the recording outcomes below are assertions about a store the test
    alone writes. ``tests/db/test_graph_derivation.py`` covers the projector over the same
    facts.
    """

    async def skip(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr("app.ingestion.service.project_completed_run", skip)


@pytest.fixture
async def estate(client: AsyncClient, session: AsyncSession) -> Estate:
    # 03 first: both scans reconcile the \\fs01\finance tree, so whichever lands second
    # tombstones what the other saw there and it did not. 09's facts are the ones used below.
    await ingest_storable_scenario(client, "03-nested-group-grant")
    await ingest_storable_scenario(client, SCENARIO)
    session.expire_all()

    async def one(statement: sa.Select[Any]) -> dict[str, Any]:
        return dict((await session.execute(statement)).mappings().one())

    rows = {
        "admins_edge": await one(
            sa.select(membership_edges).where(membership_edges.c.edge_kind == "local_group_member")
        ),
        "payroll": await one(
            sa.select(ntfs_resources).where(ntfs_resources.c.resource_key == PAYROLL.key)
        ),
        "payroll_ace": await one(
            sa.select(ntfs_aces).where(ntfs_aces.c.resource_key == PAYROLL.key)
        ),
        "alice_edge": await one(
            sa.select(membership_edges).where(
                membership_edges.c.member_key == f"{DOMAIN}-1104",
                membership_edges.c.edge_kind == "directory_group_member",
            )
        ),
    }
    basis = (await CollectionBasisRepository(session).current()).token
    return Estate(rows, basis)


def local_member(estate: Estate, **overrides: Any) -> RelationshipAssertion:
    values: dict[str, Any] = {
        "relationship_type": RelationshipType.LOCAL_GROUP_MEMBER_OF,
        "source": FINANCE_RW,
        "target": FS01_ADMINS,
        "scope": FS01,
        "derivation_class": DerivationClass.OBSERVED,
        "rule": DerivationRule("membership.local_edge", 1),
        "evidence": (estate.admins_edge(),),
        "basis_token": estate.basis,
        "derived_at": NOW,
        "attributes": {"edge_kind": "local_group_member"},
    }
    values.update(overrides)
    return RelationshipAssertion(**values)


def write_dacl_by_ace(estate: Estate, **overrides: Any) -> RelationshipAssertion:
    values: dict[str, Any] = {
        "relationship_type": RelationshipType.WRITE_DACL,
        "source": FS01_ADMINS,
        "target": PAYROLL,
        "scope": FS01,
        "derivation_class": DerivationClass.DETERMINISTICALLY_DERIVED,
        "rule": DerivationRule("acl.ntfs_write_dac", 1),
        "evidence": (estate.payroll_ace(),),
        "basis_token": estate.basis,
        "derived_at": NOW,
        "attributes": {"access_mask": 0x1F01FF, "ace_type": "allow", "inherited": False},
    }
    values.update(overrides)
    return RelationshipAssertion(**values)


def write_dacl_by_ownership(estate: Estate) -> RelationshipAssertion:
    """An owner is implicitly granted READ_CONTROL and WRITE_DAC, unless OWNER RIGHTS says
    otherwise — which nothing here evaluated, so the support is conditional."""
    return write_dacl_by_ace(
        estate,
        derivation_class=DerivationClass.CONDITIONALLY_DERIVED,
        conditions=frozenset({RelationshipCondition.NO_DENY_EVALUATED}),
        rule=DerivationRule("acl.owner_implicit_write_dac", 1),
        evidence=(estate.payroll(),),
        attributes={},
    )


async def count(session: AsyncSession, table: sa.Table) -> int:
    return int((await session.execute(sa.select(sa.func.count()).select_from(table))).scalar_one())


async def reconcile_without(client: AsyncClient, source_key: str) -> None:
    """Re-run scenario 09's reconciled scan with one observation missing.

    The history writer then tombstones that fact: an authoritative scan of the scope looked
    and did not find it, which is the only way ADG records an absence.
    """
    document = copy.deepcopy(with_run_id(storable(load_raw(SCENARIO)), str(uuid.uuid4())))
    for batch in document["batches"]:
        batch["observations"] = [
            item for item in batch["observations"] if item["source_key"] != source_key
        ]
    sent = sum(len(batch["observations"]) for batch in document["batches"])
    document["completion"]["observation_count"] = sent
    await replay(client, document)


class TestRecordingAndReading:
    async def test_recording_stores_identity_support_and_evidence(
        self, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        assertion = local_member(estate)

        result = await repository.record(assertion, now=NOW)
        await session.commit()

        assert result.relationship_created and result.outcome is RecordOutcome.CREATED
        stored = await repository.get(assertion.relationship_id)
        assert stored is not None
        assert stored.scope == FS01
        assert stored.derivation_class is DerivationClass.OBSERVED
        assert stored.confidence is RelationshipConfidence.CONFIRMED
        assert stored.currency is RelationshipCurrency.CURRENT
        (support,) = stored.supports
        assert support.basis_token == estate.basis
        assert support.first_observed_at == estate.rows["admins_edge"]["last_observed_at"]
        (link,) = support.evidence
        assert link.key == estate.rows["admins_edge"]["edge_key"]
        assert link.state is EvidenceState.PRESENT

    async def test_the_database_recomputes_the_identity(
        self, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        for assertion in (local_member(estate), write_dacl_by_ace(estate)):
            await repository.record(assertion, now=NOW)
        rows = (
            await session.execute(
                sa.select(
                    graph_relationships.c.relationship_id,
                    sa.literal_column(RELATIONSHIP_ID_SQL).label("recomputed"),
                )
            )
        ).all()
        assert len(rows) == 2
        assert all(row.relationship_id == row.recomputed for row in rows)

    async def test_evidence_is_traceable_to_the_observation_and_the_timeline(
        self, session: AsyncSession, estate: Estate
    ) -> None:
        """From one evidence link, the observation row and the fact's history are each one
        indexed read — the relationship is traceable, not a copy of what it came from."""
        await RelationshipRepository(session).record(local_member(estate), now=NOW)
        evidence = graph_relationship_evidence
        observed = (
            await session.execute(
                sa.select(sa.func.count()).select_from(
                    evidence.join(
                        observations,
                        sa.and_(
                            observations.c.run_id == evidence.c.first_observed_run_id,
                            observations.c.subject_key == evidence.c.evidence_key,
                            observations.c.kind == evidence.c.evidence_kind,
                        ),
                    )
                )
            )
        ).scalar_one()
        versions = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(
                    evidence.join(
                        object_versions,
                        sa.and_(
                            object_versions.c.object_kind == evidence.c.evidence_kind,
                            object_versions.c.object_key == evidence.c.evidence_key,
                        ),
                    )
                )
                .where(object_versions.c.valid_to.is_(None))
            )
        ).scalar_one()
        assert observed == 1
        assert versions == 1

    async def test_evidence_naming_an_unstored_fact_is_refused(
        self, session: AsyncSession, estate: Estate
    ) -> None:
        phantom = estate.admins_edge(key="fs01|S-1-5-32-544->S-1-5-21-1-2-3-9|local_group_member")
        with pytest.raises(EvidenceNotStoredError, match="not stored"):
            await RelationshipRepository(session).record(
                local_member(estate, evidence=(phantom,)), now=NOW
            )
        assert await count(session, graph_relationships) == 0

    async def test_the_reverse_lookup_finds_what_rests_on_a_fact(
        self, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        member = local_member(estate)
        admin = local_member(
            estate,
            relationship_type=RelationshipType.LOCAL_ADMIN_OF,
            derivation_class=DerivationClass.DETERMINISTICALLY_DERIVED,
            rule=DerivationRule("local.builtin_administrators", 1),
        )
        await repository.record(member, now=NOW)
        await repository.record(admin, now=NOW)
        assert await repository.supported_by(
            EvidenceKind.MEMBERSHIP_EDGE, estate.rows["admins_edge"]["edge_key"]
        ) == tuple(sorted({member.relationship_id, admin.relationship_id}))
        assert await repository.supported_by(EvidenceKind.NTFS_ACE, "\\\\nowhere") == ()


class TestDuplicateObservations:
    async def test_the_same_reading_twice_is_a_replay_and_writes_nothing(
        self, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        await repository.record(local_member(estate), now=NOW)
        before = (await session.execute(sa.select(graph_relationship_supports))).mappings().one()

        again = await repository.record(local_member(estate), now=LATER)

        assert again.outcome is RecordOutcome.REPLAYED and not again.relationship_created
        after = (await session.execute(sa.select(graph_relationship_supports))).mappings().one()
        assert dict(after) == dict(before)
        assert await count(session, graph_relationships) == 1
        assert await count(session, graph_relationship_evidence) == 1

    async def test_a_newer_run_extends_the_support_and_the_evidence(
        self, client: AsyncClient, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        first = local_member(estate)
        await repository.record(first, now=NOW)
        second_run = uuid.uuid4()
        await ingest_storable_scenario(client, SCENARIO, run_id=str(second_run))
        later = estate.admins_edge(run_id=second_run, observed_at=LATER)

        result = await repository.record(local_member(estate, evidence=(later,)), now=LATER)

        assert result.outcome is RecordOutcome.EXTENDED
        stored = await repository.get(first.relationship_id)
        assert stored is not None
        (support,) = stored.supports
        assert support.first_observed_run_id == first.supported_at[1]
        assert (support.last_observed_at, support.last_observed_run_id) == (LATER, second_run)
        (link,) = support.evidence
        assert link.first_observed_run_id == first.supported_at[1]
        assert link.last_observed_run_id == second_run

    async def test_an_older_reading_backdates_and_never_overwrites(
        self, client: AsyncClient, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        second_run = uuid.uuid4()
        await ingest_storable_scenario(client, SCENARIO, run_id=str(second_run))
        newer = local_member(
            estate,
            evidence=(estate.admins_edge(run_id=second_run, observed_at=LATER),),
            rule=DerivationRule("membership.local_edge", 2),
        )
        await repository.record(newer, now=LATER)

        older = await repository.record(local_member(estate), now=LATER)

        assert older.outcome is RecordOutcome.BACKDATED
        stored = await repository.get(newer.relationship_id)
        assert stored is not None
        (support,) = stored.supports
        assert support.first_observed_at == estate.rows["admins_edge"]["last_observed_at"]
        assert support.last_observed_at == LATER
        assert support.rule.version == 2, "an older reading's derivation is not applied"

    async def test_a_disagreeing_reading_inside_the_window_is_stale(
        self, client: AsyncClient, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        second_run = uuid.uuid4()
        await ingest_storable_scenario(client, SCENARIO, run_id=str(second_run))
        await repository.record(local_member(estate), now=NOW)
        late = estate.admins_edge(run_id=second_run, observed_at=LATER + dt.timedelta(hours=1))
        await repository.record(local_member(estate, evidence=(late,)), now=LATER)
        middle = estate.admins_edge(run_id=second_run, observed_at=LATER)

        result = await repository.record(
            local_member(
                estate,
                evidence=(middle,),
                derivation_class=DerivationClass.UNRESOLVED,
                qualifiers=frozenset({RelationshipQualifier.ENDPOINT_UNRESOLVED_AT_COLLECTION}),
            ),
            now=LATER,
        )

        assert result.outcome is RecordOutcome.STALE
        stored = await repository.get(result.relationship_id)
        assert stored is not None
        assert stored.derivation_class is DerivationClass.OBSERVED

    async def test_a_new_rule_version_on_the_same_evidence_rederives_in_place(
        self, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        await repository.record(local_member(estate), now=NOW)

        result = await repository.record(
            local_member(estate, rule=DerivationRule("membership.local_edge", 2)), now=LATER
        )

        assert result.outcome is RecordOutcome.REDERIVED
        assert await count(session, graph_relationship_supports) == 1
        stored = await repository.get(result.relationship_id)
        assert stored is not None
        assert stored.supports[0].rule.version == 2


class TestSupportsAndCurrency:
    async def test_two_independent_derivations_are_one_relationship_with_two_supports(
        self, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        by_ace = await repository.record(write_dacl_by_ace(estate), now=NOW)
        by_owner = await repository.record(write_dacl_by_ownership(estate), now=NOW)

        assert by_ace.relationship_id == by_owner.relationship_id
        assert by_ace.support_key != by_owner.support_key
        assert by_owner.outcome is RecordOutcome.CREATED and not by_owner.relationship_created
        stored = await repository.get(by_ace.relationship_id)
        assert stored is not None
        assert len(stored.supports) == 2
        assert stored.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED

    async def test_a_reconciled_absence_ends_one_support_and_not_the_relationship(
        self, client: AsyncClient, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        await repository.record(write_dacl_by_ace(estate), now=NOW)
        await repository.record(write_dacl_by_ownership(estate), now=NOW)
        await session.commit()

        await reconcile_without(client, PAYROLL_ACE_SOURCE)
        session.expire_all()

        stored = await repository.get(write_dacl_by_ace(estate).relationship_id)
        assert stored is not None
        currencies = {support.rule.rule_id: support.currency for support in stored.supports}
        assert currencies == {
            "acl.ntfs_write_dac": RelationshipCurrency.NOT_CURRENT,
            "acl.owner_implicit_write_dac": RelationshipCurrency.CURRENT,
        }
        assert stored.currency is RelationshipCurrency.CURRENT
        # As strong as its strongest *current* support: the conditional one.
        assert stored.derivation_class is DerivationClass.CONDITIONALLY_DERIVED
        assert stored.confidence is RelationshipConfidence.PROBABLE

    async def test_a_relationship_whose_only_support_was_reconciled_away_is_not_current(
        self, client: AsyncClient, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        assertion = write_dacl_by_ace(estate)
        await repository.record(assertion, now=NOW)
        await session.commit()

        await reconcile_without(client, PAYROLL_ACE_SOURCE)
        session.expire_all()

        stored = await repository.get(assertion.relationship_id)
        assert stored is not None
        assert stored.currency is RelationshipCurrency.NOT_CURRENT
        assert stored.supports[0].evidence[0].state is EvidenceState.ABSENT
        assert "Not current" in stored.explanation()

    async def test_a_fact_that_went_missing_is_unknown_never_absent(
        self, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        assertion = write_dacl_by_ace(estate)
        await repository.record(assertion, now=NOW)
        # Nothing in ingestion deletes a fact; the demo lifecycle does. A row gone without a
        # tombstone is a gap in the record, not a measured absence.
        await session.execute(
            sa.delete(ntfs_aces).where(ntfs_aces.c.ace_key == estate.rows["payroll_ace"]["ace_key"])
        )

        stored = await repository.get(assertion.relationship_id)
        assert stored is not None
        assert stored.supports[0].evidence[0].state is EvidenceState.MISSING
        assert stored.currency is RelationshipCurrency.UNKNOWN


class TestHostScopingAndAdjacency:
    async def test_host_scoped_relationships_are_listed_by_scope(
        self, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        await repository.record(local_member(estate), now=NOW)
        await repository.record(write_dacl_by_ace(estate), now=NOW)
        alice_edge = estate.evidence("alice_edge", EvidenceKind.MEMBERSHIP_EDGE, "edge_key")
        await repository.record(
            RelationshipAssertion(
                relationship_type=RelationshipType.MEMBER_OF,
                source=NodeRef.principal(estate.rows["alice_edge"]["member_key"]),
                target=NodeRef.principal(estate.rows["alice_edge"]["group_key"]),
                derivation_class=DerivationClass.OBSERVED,
                rule=DerivationRule("membership.direct_edge", 1),
                evidence=(alice_edge,),
                basis_token=estate.basis,
                derived_at=NOW,
            ),
            now=NOW,
        )
        scoped = (
            await session.execute(
                sa.select(graph_relationships.c.relationship_type)
                .where(
                    graph_relationships.c.scope_kind == RelationshipScopeKind.HOST.value,
                    graph_relationships.c.scope_key == "fs01",
                )
                .order_by(graph_relationships.c.relationship_type)
            )
        ).scalars()
        assert list(scoped) == ["local_group_member_of", "write_dacl"]

    async def test_adjacency_reads_both_directions_and_filters_by_type(
        self, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        await repository.record(local_member(estate), now=NOW)
        await repository.record(write_dacl_by_ace(estate), now=NOW)

        outbound = await repository.adjacent(FS01_ADMINS, AdjacencyDirection.OUTBOUND)
        inbound = await repository.adjacent(FS01_ADMINS, AdjacencyDirection.INBOUND)
        only_acl = await repository.adjacent(
            FS01_ADMINS, AdjacencyDirection.OUTBOUND, types={RelationshipType.MEMBER_OF}
        )

        assert [item.relationship_type for item in outbound.items] == [RelationshipType.WRITE_DACL]
        assert [item.source for item in inbound.items] == [FINANCE_RW]
        assert only_acl.items == ()
        assert outbound.next_after is None

    async def test_adjacency_pages_by_keyset_in_a_stable_order(
        self, session: AsyncSession, estate: Estate
    ) -> None:
        repository = RelationshipRepository(session)
        await repository.record(local_member(estate), now=NOW)
        await repository.record(
            local_member(
                estate,
                relationship_type=RelationshipType.LOCAL_ADMIN_OF,
                derivation_class=DerivationClass.DETERMINISTICALLY_DERIVED,
                rule=DerivationRule("local.builtin_administrators", 1),
            ),
            now=NOW,
        )

        seen: list[tuple[str, str]] = []
        pages = 0
        after = None
        while True:
            page = await repository.adjacent(
                FINANCE_RW, AdjacencyDirection.OUTBOUND, after=after, limit=1
            )
            pages += 1
            seen.extend((item.relationship_type.value, item.relationship_id) for item in page.items)
            if page.next_after is None:
                break
            after = page.next_after

        assert pages == 2
        assert [kind for kind, _ in seen] == ["local_admin_of", "local_group_member_of"]
        assert seen == sorted(seen)


class TestTheDatabaseRefusesWhatTheDomainRefuses:
    async def _insert(self, session: AsyncSession, **overrides: Any) -> None:
        source, target = FINANCE_RW, FS01_ADMINS
        values: dict[str, Any] = {
            "relationship_type": "local_group_member_of",
            "source_kind": source.kind.value,
            "source_key": source.key,
            "target_kind": target.kind.value,
            "target_key": target.key,
            "scope_kind": "host",
            "scope_key": "fs01",
            "created_at": NOW,
            "updated_at": NOW,
        }
        values.update(overrides)
        values.setdefault("relationship_id", _digest_of(values))
        await session.execute(sa.insert(graph_relationships).values(values))

    async def test_a_row_written_by_the_rule_is_accepted(self, session: AsyncSession) -> None:
        await self._insert(session, relationship_id=local_member_id())
        assert await count(session, graph_relationships) == 1

    async def test_an_id_that_is_not_the_digest_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError, match="id_is_digest"):
            await self._insert(session, relationship_id="0" * 64)

    async def test_a_traversal_only_type_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError, match="type_is_storable"):
            await self._insert(
                session,
                relationship_type="nested_member_of",
                scope_kind=None,
                scope_key=None,
                target_key=f"{DOMAIN}-1201",
            )

    async def test_a_self_membership_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError, match="no_self_edge"):
            await self._insert(
                session,
                relationship_type="member_of",
                target_key=FINANCE_RW.key,
                scope_kind=None,
                scope_key=None,
            )

    async def test_a_host_local_endpoint_outside_its_scope_is_refused(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(sa.exc.IntegrityError, match="target_host_matches_scope"):
            await self._insert(session, scope_key="fs02")
        await session.rollback()
        with pytest.raises(sa.exc.IntegrityError, match="target_host_matches_scope"):
            await self._insert(session, scope_kind=None, scope_key=None)

    async def test_an_unknown_node_kind_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(sa.exc.IntegrityError, match="source_kind_valid"):
            await self._insert(session, source_kind="mailbox")

    async def _support(self, session: AsyncSession, estate: Estate, **overrides: Any) -> None:
        relationship = local_member(estate)
        await RelationshipRepository(session).record(relationship, now=NOW)
        run = estate.rows["admins_edge"]["last_observed_run_id"]
        values: dict[str, Any] = {
            "relationship_id": relationship.relationship_id,
            "support_key": "f" * 64,
            "derivation_class": "observed",
            "rule_id": "membership.local_edge",
            "rule_version": 1,
            "qualifiers": [],
            "conditions": [],
            "attributes": {},
            "basis_token": estate.basis,
            "derived_at": NOW,
            "first_observed_at": NOW,
            "first_observed_run_id": run,
            "last_observed_at": NOW,
            "last_observed_run_id": run,
            "created_at": NOW,
            "updated_at": NOW,
        }
        values.update(overrides)
        await session.execute(sa.insert(graph_relationship_supports).values(values))

    @pytest.mark.parametrize(
        ("overrides", "constraint"),
        [
            ({"derivation_class": "conditionally_derived"}, "conditional_has_conditions"),
            ({"conditions": ["no_deny_evaluated"]}, "conditional_has_conditions"),
            ({"derivation_class": "unresolved"}, "unresolved_names_why"),
            ({"qualifiers": ["vibes"]}, "qualifiers_valid"),
            (
                {"conditions": ["vibes"], "derivation_class": "conditionally_derived"},
                "conditions_valid",
            ),
            ({"rule_id": "Not A Rule"}, "rule_id_shape"),
            ({"rule_version": 0}, "rule_version"),
            ({"attributes": []}, "attributes_object"),
            ({"basis_token": "short"}, "basis_token_shape"),
            ({"first_observed_at": LATER}, "supports_window"),
            ({"first_observed_run_id": uuid.uuid4()}, "first_observed_run_id_fkey"),
        ],
    )
    async def test_an_invalid_support_is_refused(
        self,
        session: AsyncSession,
        estate: Estate,
        overrides: dict[str, Any],
        constraint: str,
    ) -> None:
        with pytest.raises(sa.exc.IntegrityError, match=constraint):
            await self._support(session, estate, **overrides)

    async def test_deleting_a_relationship_removes_its_derived_rows_only(
        self, session: AsyncSession, estate: Estate
    ) -> None:
        await RelationshipRepository(session).record(local_member(estate), now=NOW)
        edges_before = await count(session, membership_edges)

        await session.execute(sa.delete(graph_relationships))

        assert await count(session, graph_relationship_supports) == 0
        assert await count(session, graph_relationship_evidence) == 0
        assert await count(session, membership_edges) == edges_before


def _digest_of(values: dict[str, Any]) -> str:
    """The identity digest of raw column values, so a row can be wrong in every other way."""
    material = "\x1f".join(
        values[column] or ""
        for column in (
            "relationship_type",
            "source_kind",
            "source_key",
            "target_kind",
            "target_key",
            "scope_kind",
            "scope_key",
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def local_member_id() -> str:
    from app.domain.relationships import relationship_id

    return relationship_id(RelationshipType.LOCAL_GROUP_MEMBER_OF, FINANCE_RW, FS01_ADMINS, FS01)


def load_revision(path: pathlib.Path = REVISION_PATH) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"adg_revision_{path.stem[:4]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestTheMigrationSpellsTheDomainVocabulary:
    """The revision's literals were written from the domain enums; they must stay equal."""

    def test_every_value_list_matches(self) -> None:
        from app.domain import relationships as domain

        revision = load_revision()
        assert (
            tuple(
                sorted(
                    d.relationship_type.value
                    for d in RELATIONSHIP_TAXONOMY.values()
                    if d.materialization is Materialization.STORED
                )
            )
            == revision.STORABLE_RELATIONSHIP_TYPES
        )
        assert (
            tuple(
                sorted(
                    d.relationship_type.value
                    for d in RELATIONSHIP_TAXONOMY.values()
                    if d.materialization is Materialization.STORED and not d.allows_self
                )
            )
            == revision.NO_SELF_RELATIONSHIP_TYPES
        )
        pairs = {
            "NODE_KINDS": domain.NodeKind,
            "SCOPE_KINDS": domain.RelationshipScopeKind,
            "DERIVATION_CLASSES": domain.DerivationClass,
            "QUALIFIERS": domain.RelationshipQualifier,
            "EVIDENCE_KINDS": domain.EvidenceKind,
            "EVIDENCE_ROLES": domain.EvidenceRole,
        }
        for name, enum in pairs.items():
            assert getattr(revision, name) == tuple(sorted(m.value for m in enum)), name
        # The condition vocabulary grew in 0019 (ADG-GRAPH-01B). 0018's list stays what it was
        # on the day it ran, 0019 knows it, and 0019's full list is the domain's.
        derivation = load_revision(DERIVATION_REVISION_PATH)
        assert revision.CONDITIONS == derivation.CONDITIONS_0018
        assert tuple(sorted(m.value for m in domain.RelationshipCondition)) == derivation.CONDITIONS
        assert set(derivation.ADDED_CONDITIONS) == set(derivation.CONDITIONS) - set(
            revision.CONDITIONS
        )
        assert (
            tuple(sorted(m.value for m in domain.ProjectionTrigger))
            == derivation.PROJECTION_TRIGGERS
        )
        assert (
            tuple(sorted(m.value for m in domain.ProjectionStatus))
            == derivation.PROJECTION_STATUSES
        )
        assert (
            tuple(sorted(q.value for q in UNRESOLVED_QUALIFIERS)) == revision.UNRESOLVED_QUALIFIERS
        )
        assert revision.RELATIONSHIP_ID_SQL == RELATIONSHIP_ID_SQL
