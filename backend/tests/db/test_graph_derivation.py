r"""The projector against PostgreSQL, over facts ingested through the real collector API.

Nothing here records a relationship by hand. Every estate arrives as a scan-run transcript,
the completion endpoint runs the projector exactly where it runs in production, and the tests
read back what it derived. That is the claim worth testing: that the graph a later lane will
traverse is what the stored facts establish — no more, no less — and keeps being so as the
facts change.

The lifecycle is the part a naive projector gets wrong, so it gets the most tests:

* a fact that appears makes its support current; one that stays is re-derived idempotently;
* a fact reconciled absent ends exactly the supports resting on it, and a relationship with
  another current support stays current;
* a fact that returns makes its support current again;
* a fact that is still stored and now *says something else* (an owner changed, a Deny
  appeared ahead of a grant) retires the support it no longer establishes, and reinstates it
  when it says so again;
* a fact with no row at all is unknown, and nothing turns that into gone.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import pathlib
import shutil
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Database
from app.domain.access import NtfsRight
from app.domain.identity import PrincipalKind
from app.domain.membership import MembershipEdgeKind
from app.domain.relationships import (
    DerivationClass,
    DerivationRule,
    EvidenceKind,
    EvidenceRole,
    EvidenceState,
    NodeKind,
    ProjectionStatus,
    RelationshipCondition,
    RelationshipConfidence,
    RelationshipCurrency,
    RelationshipQualifier,
    RelationshipScope,
    RelationshipType,
)
from app.models.schema import (
    graph_derivation_passes,
    graph_relationship_evidence,
    graph_relationship_supports,
    graph_relationships,
    membership_edges,
    ntfs_aces,
    ntfs_resources,
    object_versions,
    observations,
    principal_references,
    principals,
    servers,
    smb_share_aces,
    smb_shares,
)
from app.relationships import UNDERIVED, RelationshipProjector
from app.repositories.relationships import RecordOutcome, RelationshipRepository
from tests.fixtures import load_ad_graph_raw, load_raw
from tests.support.ingest import (
    ingest_storable_scenario,
    replay,
    storable,
    with_run_id,
    without_reconciliation,
)
from tests.support.relationships import (
    ace,
    all_relationships,
    at,
    edge,
    find,
    graph_snapshot,
    one,
    principal,
    resource,
    transcript,
)

DOMAIN = "S-1-5-21-1004336348-1177238915-682003330"
ALICE = f"{DOMAIN}-1104"
BOB = f"{DOMAIN}-1105"
FINANCE_TEAM = f"{DOMAIN}-1201"
FINANCE_RW = f"{DOMAIN}-1202"
AUDITORS = f"{DOMAIN}-1210"
ADMINS = "S-1-5-32-544"
RDP_USERS = "S-1-5-32-555"
FS01_ADMINS = f"fs01|{ADMINS}"
FULL = 0x001F01FF
MODIFY = 0x001301BF
READ_EXECUTE = 0x001200A9
WRITE_DAC = int(NtfsRight.WRITE_DAC)
FINANCE = "\\\\FS01\\Finance"
PAYROLL = "\\\\FS01\\Finance\\Payroll"
FINANCE_KEY = "\\\\fs01\\finance"
PAYROLL_KEY = "\\\\fs01\\finance\\payroll"
TREE = ("directory_tree", FINANCE_KEY)
CONTROL = (
    RelationshipType.GENERIC_ALL,
    RelationshipType.GENERIC_WRITE,
    RelationshipType.WRITE_DACL,
    RelationshipType.WRITE_OWNER,
)


def payroll_scan(
    minute: int,
    *,
    owner: str = ADMINS,
    entries: tuple[tuple[str, int, int | None, str], ...] = ((ADMINS, FULL, 0, "allow"),),
    declared: int | None = None,
    reconcile: bool = True,
) -> dict[str, Any]:
    """An NTFS run over ``\\\\fs01\\finance`` that reads the share root and Payroll."""
    observed = at(minute)
    items = [
        resource(FINANCE, observed, ace_count=1),
        ace(FINANCE, FINANCE_RW, MODIFY, 0, observed),
        resource(
            PAYROLL,
            observed,
            ace_count=len(entries) if declared is None else declared,
            owner=owner,
            depth=1,
        ),
        *(
            ace(PAYROLL, trustee, mask, order, observed, ace_type=kind)
            for trustee, mask, order, kind in entries
        ),
    ]
    return transcript("ntfs", [TREE], items, started=observed, reconcile=reconcile)


def local_groups_scan(
    minute: int,
    members: tuple[tuple[str, str], ...] = ((ADMINS, FINANCE_RW),),
    *,
    host: str = "FS01",
    reconcile: bool = True,
) -> dict[str, Any]:
    """A local-groups run over one host: its BUILTIN groups and their members."""
    observed = at(minute)
    groups = sorted({group for group, _ in members})
    items = [
        *(principal(group, observed, kind="local_group", host=host) for group in groups),
        *(
            edge(
                group,
                member,
                observed,
                kind="local_group_member",
                host=host,
                member_kind="domain_group",
            )
            for group, member in members
        ),
    ]
    return transcript(
        "local_groups",
        [("local_groups_host", host.casefold())],
        items,
        started=observed,
        reconcile=reconcile,
    )


def directory_scan(
    minute: int,
    edges: tuple[tuple[str, str, str], ...] = (
        (FINANCE_TEAM, ALICE, "user"),
        (FINANCE_RW, FINANCE_TEAM, "domain_group"),
    ),
    *,
    reconcile: bool = True,
) -> dict[str, Any]:
    observed = at(minute)
    sids = sorted({sid for group, member, _ in edges for sid in (group, member)})
    items = [
        *(
            principal(
                sid,
                observed,
                kind="user" if sid in (ALICE, BOB) else "domain_group",
            )
            for sid in sids
        ),
        *(edge(group, member, observed, member_kind=kind) for group, member, kind in edges),
    ]
    return transcript(
        "active_directory",
        [("domain", "corp.example.com")],
        items,
        started=observed,
        reconcile=reconcile,
    )


async def passes(session: AsyncSession) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa.select(graph_derivation_passes).order_by(graph_derivation_passes.c.completed_at)
    )
    return [dict(row) for row in rows.mappings()]


async def count(session: AsyncSession, table: sa.Table) -> int:
    return int((await session.execute(sa.select(sa.func.count()).select_from(table))).scalar_one())


def by_rule(relationship: Any) -> dict[str, Any]:
    return {str(support.rule): support for support in relationship.supports}


# --- Membership ---------------------------------------------------------------------------


class TestMembershipProjection:
    async def test_a_nested_chain_is_its_direct_hops_and_nothing_more(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await ingest_storable_scenario(client, "03-nested-group-grant")

        alice = await one(session, RelationshipType.MEMBER_OF, ALICE, FINANCE_TEAM)
        team = await one(session, RelationshipType.MEMBER_OF, FINANCE_TEAM, FINANCE_RW)
        for relationship in (alice, team):
            assert relationship.derivation_class is DerivationClass.OBSERVED
            assert relationship.currency is RelationshipCurrency.CURRENT
            assert relationship.confidence is RelationshipConfidence.CONFIRMED
            assert relationship.scope is None
        # Nested membership is a path for the traversal, never a stored relationship.
        assert not await find(session, RelationshipType.MEMBER_OF, ALICE, FINANCE_RW)
        stored_types = {r.relationship_type for r in (await all_relationships(session)).values()}
        assert RelationshipType.NESTED_MEMBER_OF not in stored_types

    async def test_every_membership_edge_kind_the_traversal_walks_is_one_relationship(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await ingest_storable_scenario(client, "04-multiple-membership-paths")
        edges = (await session.execute(sa.select(membership_edges))).mappings().all()
        projected = {
            "directory_group_member": RelationshipType.MEMBER_OF,
            "primary_group": RelationshipType.PRIMARY_GROUP_OF,
            "local_group_member": RelationshipType.LOCAL_GROUP_MEMBER_OF,
        }
        assert any(row["edge_kind"] == "primary_group" for row in edges)
        for row in edges:
            found = await find(
                session, projected[row["edge_kind"]], row["member_key"], row["group_key"]
            )
            assert len(found) == 1, row["edge_key"]
            (support,) = found[0].supports
            assert [(e.kind, e.key) for e in support.evidence] == [
                (EvidenceKind.MEMBERSHIP_EDGE, row["edge_key"])
            ]

    async def test_a_cycle_terminates_and_creates_one_relationship_per_edge(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await ingest_storable_scenario(client, "05-cyclic-group-graph")
        edges = await count(session, membership_edges)
        memberships = [
            r
            for r in (await all_relationships(session)).values()
            if r.relationship_type is RelationshipType.MEMBER_OF
        ]
        assert len(memberships) == edges
        # Replaying the cycle derives nothing new: no recursion, no growth.
        await ingest_storable_scenario(client, "05-cyclic-group-graph")
        assert await count(session, graph_relationships) == len(await all_relationships(session))
        again = [
            r
            for r in (await all_relationships(session)).values()
            if r.relationship_type is RelationshipType.MEMBER_OF
        ]
        assert len(again) == edges

    async def test_foreign_members_are_unresolved_and_their_domain_has_two_supports(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, with_run_id(load_ad_graph_raw("a06-foreign-security-principals")))
        foreign_user = "S-1-5-21-3000000001-3000000002-3000000003-1500"
        membership = await one(session, RelationshipType.MEMBER_OF, foreign_user)
        assert membership.derivation_class is DerivationClass.UNRESOLVED
        assert membership.confidence is RelationshipConfidence.INDETERMINATE
        assert RelationshipQualifier.FOREIGN_PRINCIPAL in membership.best_support.qualifiers
        assert "cannot be established" in membership.explanation()

        issued = await one(session, RelationshipType.FOREIGN_PRINCIPAL_OF, foreign_user)
        assert issued.target.kind is NodeKind.DOMAIN
        assert issued.target.key == "S-1-5-21-3000000001-3000000002-3000000003"
        assert set(by_rule(issued)) == {
            "foreign_principal.membership_edge@1",
            "foreign_principal.principal_object@1",
        }
        assert issued.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED
        assert "Supported 2 independent ways" in issued.explanation()

    async def test_an_unresolved_trustee_holds_what_its_entry_grants_and_stays_unnamed(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await ingest_storable_scenario(client, "06-unresolved-sid")
        orphan = "S-1-5-21-999888777-666555444-333222111-1234"
        relationship = await one(session, RelationshipType.GENERIC_ALL, orphan)
        assert relationship.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED
        # Resolution stays a join (ADR-0007): the relationship names the SID, never a name.
        assert relationship.explanation().startswith(f"{orphan} has full control of")


# --- Host-local --------------------------------------------------------------------------


class TestHostPrivileges:
    async def test_local_administration_is_host_scoped_and_deterministic(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, local_groups_scan(0))
        member = await one(session, RelationshipType.LOCAL_GROUP_MEMBER_OF, FINANCE_RW)
        admin = await one(session, RelationshipType.LOCAL_ADMIN_OF, FINANCE_RW)
        for relationship in (member, admin):
            assert relationship.scope == RelationshipScope.host("fs01")
            assert relationship.target.key == FS01_ADMINS
        assert admin.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED

    async def test_the_same_group_sid_on_two_hosts_is_two_relationships(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, local_groups_scan(0, host="FS01"))
        await replay(client, local_groups_scan(1, host="FS02"))
        admins = await find(session, RelationshipType.LOCAL_ADMIN_OF, FINANCE_RW)
        assert {r.target.key for r in admins} == {FS01_ADMINS, f"fs02|{ADMINS}"}
        assert {r.scope for r in admins} == {
            RelationshipScope.host("fs01"),
            RelationshipScope.host("fs02"),
        }
        assert all(r.currency is RelationshipCurrency.CURRENT for r in admins)

    async def test_remote_desktop_membership_is_conditional_on_what_is_not_collected(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, local_groups_scan(0, ((RDP_USERS, FINANCE_RW),)))
        rdp = await one(session, RelationshipType.REMOTE_DESKTOP_USER_OF, FINANCE_RW)
        assert rdp.derivation_class is DerivationClass.CONDITIONALLY_DERIVED
        assert rdp.confidence is RelationshipConfidence.PROBABLE
        assert rdp.best_support.conditions == {
            RelationshipCondition.LOGON_RIGHT_UNEVALUATED,
            RelationshipCondition.REMOTE_SERVICE_STATE_UNKNOWN,
        }
        assert "user-rights assignments are not collected" in rdp.explanation()
        assert not await find(session, RelationshipType.LOCAL_ADMIN_OF)

    async def test_a_host_removal_ends_only_that_hosts_privilege(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, local_groups_scan(0, host="FS01"))
        await replay(client, local_groups_scan(1, host="FS02"))
        # FS01 is re-read and its Administrators no longer holds Finance-RW.
        await replay(client, local_groups_scan(2, ((ADMINS, AUDITORS),), host="FS01"))
        session.expire_all()
        by_host = {
            r.target.key: r
            for r in await find(session, RelationshipType.LOCAL_ADMIN_OF, FINANCE_RW)
        }
        assert by_host[FS01_ADMINS].currency is RelationshipCurrency.NOT_CURRENT
        assert by_host[f"fs02|{ADMINS}"].currency is RelationshipCurrency.CURRENT
        auditors = await one(session, RelationshipType.LOCAL_ADMIN_OF, AUDITORS)
        assert auditors.currency is RelationshipCurrency.CURRENT


# --- NTFS --------------------------------------------------------------------------------


class TestNtfsProjection:
    async def test_the_owner_and_a_full_control_entry_are_two_supports_of_one_right(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await ingest_storable_scenario(client, "09-broken-inheritance")
        owns = await one(session, RelationshipType.OWNS, FS01_ADMINS, PAYROLL_KEY)
        assert owns.derivation_class is DerivationClass.OBSERVED
        assert owns.scope == RelationshipScope.host("fs01")

        write_dacl = await one(session, RelationshipType.WRITE_DACL, FS01_ADMINS, PAYROLL_KEY)
        supports = by_rule(write_dacl)
        assert set(supports) == {"ntfs.allow_entry@1", "ntfs.owner_implicit_rights@1"}
        entry = supports["ntfs.allow_entry@1"]
        assert [(e.kind, e.role) for e in entry.evidence] == [
            (EvidenceKind.NTFS_ACE, EvidenceRole.PREMISE),
            (EvidenceKind.NTFS_RESOURCE, EvidenceRole.CONTEXT),
        ]
        assert dict(entry.attributes) == {
            "access_mask": FULL,
            "ace_type": "allow",
            "inherited": False,
        }
        for relationship_type in CONTROL:
            assert await find(session, relationship_type, FS01_ADMINS, PAYROLL_KEY)

    async def test_modify_is_generic_write_only(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await ingest_storable_scenario(client, "09-broken-inheritance")
        derived = {
            r.relationship_type
            for r in await find(session, RelationshipType.GENERIC_WRITE, FINANCE_RW)
        } | {
            r.relationship_type
            for kind in (
                RelationshipType.GENERIC_ALL,
                RelationshipType.WRITE_DACL,
                RelationshipType.WRITE_OWNER,
            )
            for r in await find(session, kind, FINANCE_RW, FINANCE_KEY)
        }
        assert derived == {RelationshipType.GENERIC_WRITE}

    async def test_a_deny_for_another_trustee_ahead_of_the_grant_is_named_as_context(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(
            client,
            payroll_scan(
                0,
                entries=(
                    (FINANCE_TEAM, WRITE_DAC, 0, "deny"),
                    (FINANCE_RW, FULL, 1, "allow"),
                ),
            ),
        )
        write_dacl = await one(session, RelationshipType.WRITE_DACL, FINANCE_RW, PAYROLL_KEY)
        support = write_dacl.best_support
        assert support.derivation_class is DerivationClass.CONDITIONALLY_DERIVED
        assert support.conditions == {RelationshipCondition.DENY_MAY_APPLY}
        context = {e.key for e in support.evidence if e.role is EvidenceRole.CONTEXT}
        deny_key = (
            await session.execute(
                sa.select(ntfs_aces.c.ace_key).where(ntfs_aces.c.ace_type == "deny")
            )
        ).scalar_one()
        assert deny_key in context
        # The deny names WRITE_DAC alone, so taking ownership is unaffected by it.
        write_owner = await one(session, RelationshipType.WRITE_OWNER, FINANCE_RW, PAYROLL_KEY)
        assert write_owner.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED

    async def test_share_entries_establish_no_control_relationship(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        for name in ("10-smb-more-restrictive", "11-ntfs-more-restrictive"):
            await ingest_storable_scenario(client, name)
        targets = {r.target.kind for r in (await all_relationships(session)).values()}
        assert NodeKind.SMB_SHARE not in targets
        smb_evidence = await session.execute(
            sa.select(sa.func.count())
            .select_from(graph_relationship_evidence)
            .where(graph_relationship_evidence.c.evidence_kind == EvidenceKind.SMB_ACE.value)
        )
        assert smb_evidence.scalar_one() == 0


# --- Lifecycle ---------------------------------------------------------------------------


class TestReconciliationLifecycle:
    async def test_a_removed_entry_ends_its_support_and_the_owner_keeps_the_right(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, payroll_scan(0))
        await replay(client, payroll_scan(10, entries=((BOB, READ_EXECUTE, 0, "allow"),)))
        session.expire_all()

        write_dacl = await one(session, RelationshipType.WRITE_DACL, FS01_ADMINS, PAYROLL_KEY)
        supports = by_rule(write_dacl)
        assert supports["ntfs.allow_entry@1"].currency is RelationshipCurrency.NOT_CURRENT
        assert not supports["ntfs.allow_entry@1"].is_retired, "absence is the tombstone's"
        assert supports["ntfs.allow_entry@1"].evidence[0].state is EvidenceState.ABSENT
        assert supports["ntfs.owner_implicit_rights@1"].currency is RelationshipCurrency.CURRENT
        assert write_dacl.currency is RelationshipCurrency.CURRENT

        generic_all = await one(session, RelationshipType.GENERIC_ALL, FS01_ADMINS, PAYROLL_KEY)
        assert generic_all.currency is RelationshipCurrency.NOT_CURRENT
        assert "recorded its evidence gone" in generic_all.explanation()

    async def test_an_entry_that_returns_makes_its_support_current_again(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, payroll_scan(0))
        await replay(client, payroll_scan(10, entries=((BOB, READ_EXECUTE, 0, "allow"),)))
        await replay(client, payroll_scan(20))
        session.expire_all()
        generic_all = await one(session, RelationshipType.GENERIC_ALL, FS01_ADMINS, PAYROLL_KEY)
        assert generic_all.currency is RelationshipCurrency.CURRENT
        (support,) = generic_all.supports
        assert support.first_observed_at == at(0) and support.last_observed_at == at(20)

    async def test_a_membership_removed_then_restored(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, directory_scan(0))
        await replay(client, directory_scan(10, ((FINANCE_RW, FINANCE_TEAM, "domain_group"),)))
        session.expire_all()
        alice = await one(session, RelationshipType.MEMBER_OF, ALICE, FINANCE_TEAM)
        assert alice.currency is RelationshipCurrency.NOT_CURRENT
        team = await one(session, RelationshipType.MEMBER_OF, FINANCE_TEAM, FINANCE_RW)
        assert team.currency is RelationshipCurrency.CURRENT

        await replay(client, directory_scan(20))
        session.expire_all()
        alice = await one(session, RelationshipType.MEMBER_OF, ALICE, FINANCE_TEAM)
        assert alice.currency is RelationshipCurrency.CURRENT

    async def test_an_owner_changed_in_place_retires_the_old_owners_support(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, payroll_scan(0, owner=ALICE))
        await replay(client, payroll_scan(10, owner=BOB))
        session.expire_all()

        old = await one(session, RelationshipType.OWNS, ALICE, PAYROLL_KEY)
        (support,) = old.supports
        # The resource is still stored, so its presence alone would call this current.
        assert support.evidence[0].state is EvidenceState.PRESENT
        assert support.is_retired
        assert old.currency is RelationshipCurrency.NOT_CURRENT
        assert "no longer establish it" in old.explanation()
        assert (await one(session, RelationshipType.WRITE_DACL, ALICE, PAYROLL_KEY)).currency is (
            RelationshipCurrency.NOT_CURRENT
        )
        new = await one(session, RelationshipType.OWNS, BOB, PAYROLL_KEY)
        assert new.currency is RelationshipCurrency.CURRENT

        await replay(client, payroll_scan(20, owner=ALICE))
        session.expire_all()
        back = await one(session, RelationshipType.OWNS, ALICE, PAYROLL_KEY)
        assert back.currency is RelationshipCurrency.CURRENT
        assert not back.supports[0].is_retired
        assert (await one(session, RelationshipType.OWNS, BOB, PAYROLL_KEY)).currency is (
            RelationshipCurrency.NOT_CURRENT
        )
        (latest, *_) = sorted(
            await passes(session), key=lambda row: row["completed_at"], reverse=True
        )
        assert latest["supports_reinstated"] >= 2 and latest["supports_retired"] >= 2

    async def test_a_deny_that_appears_ahead_of_a_grant_retires_it_and_its_removal_restores_it(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, payroll_scan(0, owner=ALICE))
        await replay(
            client,
            payroll_scan(
                10,
                owner=ALICE,
                entries=((ADMINS, WRITE_DAC, 0, "deny"), (ADMINS, FULL, 1, "allow")),
            ),
        )
        session.expire_all()
        write_dacl = await one(session, RelationshipType.WRITE_DACL, FS01_ADMINS, PAYROLL_KEY)
        (support,) = write_dacl.supports
        assert support.is_retired and write_dacl.currency is RelationshipCurrency.NOT_CURRENT
        assert (
            await one(session, RelationshipType.WRITE_OWNER, FS01_ADMINS, PAYROLL_KEY)
        ).currency is RelationshipCurrency.CURRENT

        await replay(client, payroll_scan(20, owner=ALICE))
        session.expire_all()
        write_dacl = await one(session, RelationshipType.WRITE_DACL, FS01_ADMINS, PAYROLL_KEY)
        assert write_dacl.currency is RelationshipCurrency.CURRENT
        assert not write_dacl.supports[0].is_retired

    async def test_a_partial_reading_never_takes_a_relationship_away(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, payroll_scan(0))
        before = await graph_snapshot(session)
        # The same tree, re-read without reconciling: it says nothing about what it did not see.
        await replay(
            client,
            payroll_scan(10, entries=((BOB, READ_EXECUTE, 0, "allow"),), reconcile=False),
        )
        session.expire_all()
        generic_all = await one(session, RelationshipType.GENERIC_ALL, FS01_ADMINS, PAYROLL_KEY)
        assert generic_all.currency is RelationshipCurrency.CURRENT
        assert set(before) <= set(await graph_snapshot(session))


class TestUnknownIsNeverAbsent:
    async def test_a_fact_with_no_row_is_unknown_and_a_later_pass_keeps_it_so(
        self, client: AsyncClient, session: AsyncSession, database: Database
    ) -> None:
        await ingest_storable_scenario(client, "03-nested-group-grant")
        edge_key = (
            await session.execute(
                sa.select(membership_edges.c.edge_key).where(membership_edges.c.member_key == ALICE)
            )
        ).scalar_one()
        # Nothing in ingestion deletes a fact; the demo lifecycle does.
        await session.execute(
            sa.delete(membership_edges).where(membership_edges.c.edge_key == edge_key)
        )
        await session.commit()

        async with database.session() as other:
            await RelationshipProjector(other).rebuild(now=dt.datetime.now(tz=dt.UTC))
            await other.commit()
        session.expire_all()
        alice = await one(session, RelationshipType.MEMBER_OF, ALICE, FINANCE_TEAM)
        assert alice.supports[0].evidence[0].state is EvidenceState.MISSING
        assert not alice.supports[0].is_retired
        assert alice.currency is RelationshipCurrency.UNKNOWN


# --- Idempotency and rebuild -------------------------------------------------------------


class TestIdempotency:
    async def test_replaying_a_scan_derives_nothing_new(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await ingest_storable_scenario(client, "09-broken-inheritance")
        counts = [
            await count(session, table)
            for table in (
                graph_relationships,
                graph_relationship_supports,
                graph_relationship_evidence,
            )
        ]
        before = await graph_snapshot(session)
        await ingest_storable_scenario(client, "09-broken-inheritance")
        session.expire_all()
        assert [
            await count(session, table)
            for table in (
                graph_relationships,
                graph_relationship_supports,
                graph_relationship_evidence,
            )
        ] == counts
        assert await graph_snapshot(session) == before
        latest = (await passes(session))[-1]
        assert latest["relationships_created"] == 0 and latest["supports_created"] == 0
        assert latest["supports_retired"] == 0

    async def test_a_rescan_that_changes_nothing_confirms_every_window_and_derives_nothing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, payroll_scan(0))
        supports = await count(session, graph_relationship_supports)
        await replay(client, payroll_scan(10))
        session.expire_all()

        latest = (await passes(session))[-1]
        assert latest["units_examined"] == 0, "nothing changed, so nothing is re-derived"
        assert latest["supports_extended"] == supports
        assert latest["supports_created"] == latest["supports_rederived"] == 0
        for relationship in (await all_relationships(session)).values():
            for support in relationship.supports:
                assert (support.first_observed_at, support.last_observed_at) == (at(0), at(10))
                for link in support.evidence:
                    assert link.last_observed_at == at(10)

    async def test_a_resent_completion_does_not_derive_again(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        document = payroll_scan(0)
        await replay(client, document)
        run_id = document["start"]["run_id"]
        again = await client.post(
            f"/api/v1/scan-runs/{run_id}/completion", json=document["completion"]
        )
        assert again.status_code == 200 and again.json()["already_completed"]
        assert len(await passes(session)) == 1

    async def test_the_same_observations_sent_twice_in_one_run_are_one_support(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A collector that re-sends a batch under a new batch id (a retry it believes failed)
        delivers every observation twice; one run, one fact, one support."""
        document = local_groups_scan(0)
        (batch,) = document["batches"]
        resent = {**copy.deepcopy(batch), "batch_id": str(uuid.uuid4()), "sequence": 2}
        batch["is_final"] = False
        document["batches"].append(resent)
        document["completion"]["batch_count"] = 2
        document["completion"]["observation_count"] = 2 * len(batch["observations"])
        await replay(client, document)
        admin = await one(session, RelationshipType.LOCAL_ADMIN_OF, FINANCE_RW)
        assert len(admin.supports) == 1 and len(admin.supports[0].evidence) == 1

    async def test_the_store_collapses_a_derivation_repeated_in_one_batch(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Idempotency is the database's, not only the projector's: the same support handed to
        the store twice in one call is one row, and a contradictory repeat is refused."""
        await replay(client, local_groups_scan(0))
        (admin,) = await find(session, RelationshipType.LOCAL_ADMIN_OF, FINANCE_RW)
        await session.execute(sa.delete(graph_relationships))
        edge_row = (await session.execute(sa.select(membership_edges))).mappings().one()
        from app.relationships.rules import DerivationContext, EdgeFact, FactReading, derive_edge

        fact = EdgeFact(
            reading=FactReading(
                EvidenceKind.MEMBERSHIP_EDGE,
                edge_row["edge_key"],
                edge_row["last_observed_run_id"],
                edge_row["last_observed_at"],
            ),
            group_key=edge_row["group_key"],
            member_key=edge_row["member_key"],
            group_sid=edge_row["group_sid"],
            member_sid=edge_row["member_sid"],
            edge_kind=MembershipEdgeKind(edge_row["edge_kind"]),
            host_key=edge_row["host_key"],
            member_kind=PrincipalKind(edge_row["member_kind"]),
            is_foreign_security_principal=False,
        )
        derived = derive_edge(
            fact, DerivationContext(basis_token="a" * 32, derived_at=dt.datetime.now(tz=dt.UTC))
        ).assertions
        repository = RelationshipRepository(session)
        batch = await repository.record_many((*derived, *derived), now=dt.datetime.now(tz=dt.UTC))
        assert len(batch.results) == len(derived) == 2
        assert batch.relationships_created == 2
        assert await count(session, graph_relationship_supports) == 2
        assert await count(session, graph_relationship_evidence) == 2
        contradictory = dataclasses.replace(derived[0], rule=DerivationRule("test.other", 1))
        with pytest.raises(ValueError, match="two different ways"):
            await repository.record_many(
                (derived[0], contradictory), now=dt.datetime.now(tz=dt.UTC)
            )
        assert admin.relationship_id in {a.relationship_id for a in derived}

    async def test_a_rebuild_over_unchanged_facts_changes_nothing(
        self, client: AsyncClient, session: AsyncSession, database: Database
    ) -> None:
        for name in ("03-nested-group-grant", "09-broken-inheritance"):
            await ingest_storable_scenario(client, name)
        before = await graph_snapshot(session)
        supports = sum(len(item["supports"]) for item in before.values())

        async def rebuild() -> Any:
            async with database.session() as other:
                outcome = await RelationshipProjector(other).rebuild(now=dt.datetime.now(tz=dt.UTC))
                await other.commit()
            return outcome

        first = await rebuild()
        assert first.relationships_created == 0
        assert first.count(RecordOutcome.CREATED) == 0
        assert first.supports_retired == 0
        # 03's memberships were last derived against 03's collection basis; a rebuild reads
        # the newer one, which is a re-derivation of the same support and nothing more.
        assert set(first.outcomes) <= {RecordOutcome.REPLAYED, RecordOutcome.REDERIVED}
        assert sum(first.outcomes.values()) == supports
        second = await rebuild()
        assert dict(second.outcomes) == {RecordOutcome.REPLAYED: supports}
        assert second.relationships_created == 0 and second.supports_retired == 0
        session.expire_all()
        assert await graph_snapshot(session) == before


class TestRebuildEquivalence:
    async def test_incremental_derivation_and_a_clean_recomputation_agree(
        self, client: AsyncClient, session: AsyncSession, database: Database
    ) -> None:
        # An estate built run by run, including removals, revivals and an owner change.
        await replay(client, directory_scan(0))
        await replay(client, local_groups_scan(1))
        await replay(client, payroll_scan(2, owner=ALICE))
        await replay(client, payroll_scan(3, owner=BOB, entries=((BOB, MODIFY, 0, "allow"),)))
        await replay(client, directory_scan(4, ((FINANCE_RW, FINANCE_TEAM, "domain_group"),)))
        await replay(client, local_groups_scan(5, ((ADMINS, FINANCE_RW), (RDP_USERS, AUDITORS))))
        await replay(client, with_run_id(load_ad_graph_raw("a06-foreign-security-principals")))
        incremental = await graph_snapshot(session)
        assert incremental

        async with database.session() as other:
            outcome = await RelationshipProjector(other).rebuild(
                now=dt.datetime.now(tz=dt.UTC), discard_existing=True
            )
            await other.commit()
        assert outcome.supports_retired == 0
        session.expire_all()
        assert await graph_snapshot(session) == incremental

    async def test_a_rebuild_never_writes_a_collected_fact(
        self, client: AsyncClient, session: AsyncSession, database: Database
    ) -> None:
        await ingest_storable_scenario(client, "09-broken-inheritance")
        # Every collected fact, its history and its references: a rebuild reads them all.
        facts = (
            principals,
            principal_references,
            membership_edges,
            servers,
            smb_shares,
            smb_share_aces,
            ntfs_resources,
            ntfs_aces,
            observations,
            object_versions,
        )

        async def fingerprint() -> list[Any]:
            return [
                (
                    await session.execute(
                        sa.text(
                            f"SELECT md5(string_agg(t::text, '' ORDER BY t::text)) FROM {t.name} t"
                        )
                    )
                ).scalar_one()
                for t in facts
            ]

        before = await fingerprint()
        async with database.session() as other:
            await RelationshipProjector(other).rebuild(
                now=dt.datetime.now(tz=dt.UTC), discard_existing=True
            )
            await other.commit()
        assert await fingerprint() == before


# --- Provenance --------------------------------------------------------------------------


class TestProvenance:
    async def test_every_relationship_traces_to_stored_observations(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        for name in ("04-multiple-membership-paths", "09-broken-inheritance", "06-unresolved-sid"):
            await replay(client, without_reconciliation(with_run_id(storable(load_raw(name)))))
        relationships = await all_relationships(session)
        assert relationships
        for relationship in relationships.values():
            assert relationship.supports
            assert relationship.explanation()
            for support in relationship.supports:
                premises = [e for e in support.evidence if e.role is EvidenceRole.PREMISE]
                assert premises, relationship.relationship_id
                assert support.rule.rule_id
                assert support.basis_token
        # Each evidence link names an observation the run that supplied it really made.
        untraced = await session.execute(
            sa.select(sa.func.count())
            .select_from(graph_relationship_evidence)
            .where(
                ~sa.exists().where(
                    observations.c.run_id == graph_relationship_evidence.c.last_observed_run_id,
                    observations.c.subject_key == graph_relationship_evidence.c.evidence_key,
                    observations.c.kind == graph_relationship_evidence.c.evidence_kind,
                )
            )
        )
        assert untraced.scalar_one() == 0

    async def test_no_support_without_evidence_and_no_relationship_without_a_support(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, payroll_scan(0, owner=ALICE))
        await replay(client, payroll_scan(10, owner=BOB))
        orphan_supports = await session.execute(
            sa.select(sa.func.count())
            .select_from(graph_relationship_supports)
            .where(
                ~sa.exists().where(
                    graph_relationship_evidence.c.relationship_id
                    == graph_relationship_supports.c.relationship_id,
                    graph_relationship_evidence.c.support_key
                    == graph_relationship_supports.c.support_key,
                    graph_relationship_evidence.c.role == EvidenceRole.PREMISE.value,
                )
            )
        )
        orphan_relationships = await session.execute(
            sa.select(sa.func.count())
            .select_from(graph_relationships)
            .where(
                ~sa.exists().where(
                    graph_relationship_supports.c.relationship_id
                    == graph_relationships.c.relationship_id
                )
            )
        )
        assert orphan_supports.scalar_one() == 0
        assert orphan_relationships.scalar_one() == 0

    async def test_each_pass_records_its_basis_its_rules_and_what_it_changed(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        document = payroll_scan(0)
        await replay(client, document)
        (row,) = await passes(session)
        assert row["status"] == ProjectionStatus.SUCCEEDED.value
        assert str(row["source_run_id"]) == document["start"]["run_id"]
        assert row["error"] is None
        assert "ntfs.allow_entry@1" in row["rules"]
        assert row["relationships_created"] == await count(session, graph_relationships)
        assert row["supports_created"] == await count(session, graph_relationship_supports)
        state = await RelationshipProjector(session).state()
        assert state.is_current

    async def test_no_underived_type_is_ever_stored(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        for name in (
            "02-group-grant",
            "04-multiple-membership-paths",
            "07-deny-candidate",
            "08-inherited-ace",
            "09-broken-inheritance",
        ):
            await replay(client, without_reconciliation(with_run_id(storable(load_raw(name)))))
        stored = {r.relationship_type for r in (await all_relationships(session)).values()}
        assert stored
        assert not stored & UNDERIVED


# --- Failure safety ----------------------------------------------------------------------


class TestFailureSafety:
    async def test_a_failing_rule_rolls_back_the_whole_pass_and_never_the_completion(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(*_: Any, **__: Any) -> Any:
            raise RuntimeError("a defect in a rule")

        monkeypatch.setattr("app.relationships.projector.derive_resource", broken)
        document = with_run_id(storable(load_raw("09-broken-inheritance")))
        await replay(client, document)

        # Membership edges were derived before resources in the same pass, and none survive.
        assert await count(session, graph_relationships) == 0
        assert await count(session, graph_relationship_supports) == 0
        assert await count(session, ntfs_aces) == 2, "the facts were stored regardless"
        (failed,) = await passes(session)
        assert failed["status"] == ProjectionStatus.FAILED.value
        assert "a defect in a rule" in failed["error"]
        state = await RelationshipProjector(session).state()
        assert [str(run) for run in state.underived_runs] == [document["start"]["run_id"]]

        # The collector re-sends its completion; this time the derivation runs and holds.
        monkeypatch.undo()
        run_id = document["start"]["run_id"]
        again = await client.post(
            f"/api/v1/scan-runs/{run_id}/completion", json=document["completion"]
        )
        assert again.status_code == 200
        session.expire_all()
        assert await count(session, graph_relationships) > 0
        assert (await RelationshipProjector(session).state()).is_current

    async def test_a_write_that_fails_midway_leaves_no_support_without_its_evidence(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def broken(*_: Any, **__: Any) -> None:
            raise RuntimeError("evidence could not be written")

        monkeypatch.setattr(
            "app.repositories.relationships.RelationshipRepository._record_evidence", broken
        )
        await replay(client, payroll_scan(0))
        assert await count(session, graph_relationships) == 0
        assert await count(session, graph_relationship_supports) == 0
        assert (await passes(session))[0]["status"] == ProjectionStatus.FAILED.value


# --- Performance -------------------------------------------------------------------------


class TestDerivationCost:
    async def test_statements_per_pass_do_not_grow_with_the_number_of_facts(
        self, client: AsyncClient, session: AsyncSession, database: Database
    ) -> None:
        """Counted, never timed (``test_query_cost.py``): a pass over many facts issues a
        bounded number of statements per chunk, not several per relationship."""
        from tests.db.test_query_cost import StatementLog

        observed = at(0)
        groups = [f"{DOMAIN}-{5000 + index}" for index in range(40)]
        users = [f"{DOMAIN}-{7000 + index}" for index in range(400)]
        edges = [
            edge(groups[index % len(groups)], user, observed) for index, user in enumerate(users)
        ]
        paths = [f"{FINANCE}\\Dept{index:03d}" for index in range(60)]
        items: list[dict[str, Any]] = [resource(FINANCE, observed, ace_count=0)]
        for path in paths:
            entries = [
                ace(path, group, MODIFY, order, observed) for order, group in enumerate(groups[:6])
            ]
            entries.append(ace(path, ADMINS, FULL, len(entries), observed))
            items.append(resource(path, observed, ace_count=len(entries), owner=ADMINS, depth=1))
            items.extend(entries)
        await replay(
            client,
            transcript(
                "active_directory",
                [("domain", "corp.example.com")],
                [*(principal(u, observed) for u in users), *edges],
                started=observed,
                reconcile=False,
            ),
        )
        await replay(client, transcript("ntfs", [TREE], items, started=observed, reconcile=False))

        log = StatementLog(database.engine)
        async with database.session() as other:
            with log:
                outcome = await RelationshipProjector(other).rebuild(now=dt.datetime.now(tz=dt.UTC))
            await other.commit()
        relationships = await count(session, graph_relationships)
        supports = await count(session, graph_relationship_supports)
        assert relationships > 400 + 60 * 6
        assert outcome.relationships_created == 0 and outcome.supports_retired == 0
        assert sum(outcome.outcomes.values()) == supports
        # 400 edges and 60 descriptors of 7 entries: well under one statement per support.
        assert log.count < supports / 10, f"{log.count} statements for {supports} supports"


# --- The shipped local-groups collector --------------------------------------------------


@pytest.mark.powershell
@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 (pwsh) is not on PATH")
class TestTheLocalGroupsCollector:
    """The collector's own offline output, ingested as a collector would send it.

    End to end without a Windows host: the collector reads its host fixture, writes the exact
    payloads it would POST, and the projector derives host privileges from them at completion.
    """

    async def test_its_runs_derive_host_scoped_privileges(
        self, client: AsyncClient, session: AsyncSession, tmp_path: pathlib.Path
    ) -> None:
        from tests.contracts.test_local_groups_collector import run_offline

        runs = run_offline(tmp_path, "FS01", "FS02")
        for host in ("fs01", "fs02"):
            payloads = runs[host]
            batches = [payloads[name] for name in sorted(payloads) if name.startswith("batch-")]
            await replay(
                client,
                {
                    "start": payloads["start.json"],
                    "batches": batches,
                    "completion": payloads["completion.json"],
                },
            )

        admins = {
            r.target.key: r
            for r in await find(session, RelationshipType.LOCAL_ADMIN_OF, FINANCE_RW)
        }
        assert set(admins) == {FS01_ADMINS}
        assert admins[FS01_ADMINS].derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED
        auditors = await one(session, RelationshipType.LOCAL_ADMIN_OF, AUDITORS)
        assert auditors.scope == RelationshipScope.host("fs02")

        rdp = await one(session, RelationshipType.REMOTE_DESKTOP_USER_OF, ALICE)
        assert rdp.derivation_class is DerivationClass.CONDITIONALLY_DERIVED
        winrm = await one(session, RelationshipType.REMOTE_MANAGEMENT_USER_OF, FINANCE_TEAM)
        assert winrm.derivation_class is DerivationClass.CONDITIONALLY_DERIVED

        orphan = await one(session, RelationshipType.LOCAL_ADMIN_OF, f"{DOMAIN}-9999")
        assert orphan.derivation_class is DerivationClass.UNRESOLVED
        assert RelationshipQualifier.ENDPOINT_UNRESOLVED_AT_COLLECTION in (
            orphan.best_support.qualifiers
        )
        assert (await RelationshipProjector(session).state()).is_current
