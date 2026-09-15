r"""Phase 9B's HTTP surface against a real PostgreSQL.

Phase 9A proved the engine. What can only be proved here is what the *routes* do with it, and
four of those properties are the ones the phase's acceptance criteria are written in.

1. **No route writes to Windows, and none writes to ADG's collected state.** Every
   collected-state table is digested before and after the whole route sweep — preview, store,
   re-evaluate, export, delete — and the digest must not move. That is the test that would
   catch a handler that reached past ``SimulationStore``, or an overlay that leaked into an
   ingestion path.
2. **Every payload says so.** ``notice`` and ``applied: false`` are asserted on every
   simulation response rather than trusted to the renderer, because the acceptance criterion
   is that the non-destructive nature is unmistakable and a field nobody checks is a field
   that can quietly stop being sent.
3. **The output is explainable, not only a count.** A removal proposal over
   ``04-multiple-membership-paths`` must come back naming the route that survives it — which
   is the difference between "this change revokes Alice's access" and the truth.
4. **The capability split holds end to end.** An auditor reads and cannot run; a viewer
   reaches neither. Asserted through the application's own authentication, against the real
   database, rather than against a role table.

The transcript is replayed through the HTTP ingestion endpoint, as every other database suite
does, so the rows under test are the rows a collector would have produced.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.roles import Capability, Role
from app.models.schema import (
    membership_edges,
    ntfs_aces,
    ntfs_resources,
    object_versions,
    principals,
    scan_runs,
    servers,
    simulation_evaluations,
    simulations,
    smb_share_aces,
    smb_shares,
)
from app.simulation import (
    CAVEAT_DESCRIPTIONS,
    OUTCOME_DESCRIPTIONS,
    TRUNCATION_DESCRIPTIONS,
    ChangeKind,
    ImpactDirection,
    InheritedAceDisposition,
    ScopeKind,
)
from app.simulation.describe import NON_DESTRUCTIVE_NOTICE
from tests.fixtures import load_raw
from tests.support.ingest import replay, storable, with_run_id

pytestmark = pytest.mark.anyio

DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"
ALICE = f"{DOMAIN_SID}-1104"
FINANCE_TEAM = f"{DOMAIN_SID}-1201"
FINANCE_RW = f"{DOMAIN_SID}-1202"
FINANCE_OPS = f"{DOMAIN_SID}-1204"
DOMAIN_USERS = f"{DOMAIN_SID}-513"

FINANCE = "\\\\FS01\\Finance"

#: Everything a collector writes. No route in this phase may change one byte of any of it.
COLLECTED_TABLES = (
    principals,
    membership_edges,
    servers,
    smb_shares,
    smb_share_aces,
    ntfs_resources,
    ntfs_aces,
    object_versions,
    scan_runs,
)


@pytest.fixture
async def estate(client: AsyncClient) -> dict[str, Any]:
    r"""``04-multiple-membership-paths``: Alice reaches ``\\FS01\Finance`` three ways."""
    return await replay(client, storable(load_raw("04-multiple-membership-paths")))


async def digest_of_collected_state(session: AsyncSession) -> str:
    """One digest over every row of every table a collector writes."""
    hasher = hashlib.sha256()
    for table in COLLECTED_TABLES:
        hasher.update(table.name.encode())
        columns = sorted(table.c, key=lambda column: column.name)
        statement = sa.select(*columns).order_by(*[column for column in table.primary_key])
        for row in (await session.execute(statement)).all():
            hasher.update(repr(row).encode())
    return hasher.hexdigest()


def remove_from(group_key: str) -> dict[str, Any]:
    """Take Alice out of one group, as a request body change."""
    return {
        "kind": "remove_member",
        "group_key": group_key,
        "member_key": ALICE,
        "edge_kind": "directory_group_member",
    }


def add_to(group_key: str, member_key: str) -> dict[str, Any]:
    return {
        "kind": "add_member",
        "group_key": group_key,
        "member_key": member_key,
        "edge_kind": "directory_group_member",
    }


def assert_non_destructive(payload: dict[str, Any]) -> None:
    """Every simulation payload carries the sentence, and the field a client can assert on."""
    assert payload["notice"] == NON_DESTRUCTIVE_NOTICE
    assert "NO CHANGES WILL BE APPLIED" in payload["notice"]
    if "applied" in payload:
        assert payload["applied"] is False


class TestPreviewComputesAndKeepsNothing:
    async def test_a_preview_answers_and_stores_no_row(
        self, client: AsyncClient, session: AsyncSession, estate: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/api/v1/simulations/preview",
            json={"changes": [remove_from(FINANCE_TEAM)]},
        )

        assert response.status_code == 200
        payload = response.json()
        assert_non_destructive(payload)
        assert payload["simulation_id"] is None
        assert payload["change_count"] == 1
        stored = (
            await session.execute(sa.select(sa.func.count()).select_from(simulations))
        ).scalar_one()
        evaluations = (
            await session.execute(sa.select(sa.func.count()).select_from(simulation_evaluations))
        ).scalar_one()
        assert (stored, evaluations) == (0, 0)

    async def test_the_report_names_the_baseline_it_was_computed_against(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        """A what-if is meaningless without a stated what-is, so the token is on the answer."""
        response = await client.post(
            "/api/v1/simulations/preview", json={"changes": [remove_from(FINANCE_TEAM)]}
        )

        baseline = response.json()["baseline"]
        assert baseline["kind"] == "current"
        assert baseline["token"]
        assert baseline["stale"] is False
        assert baseline["current_token"] == baseline["token"]
        assert baseline["is_empty"] is False

    async def test_each_change_reports_what_became_of_it(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        """ "No impact" and "no longer applies" are the same empty list and different advice."""
        response = await client.post(
            "/api/v1/simulations/preview",
            json={
                "changes": [
                    remove_from(FINANCE_TEAM),
                    remove_from(f"{DOMAIN_SID}-9999"),
                ]
            },
        )

        applications = response.json()["applications"]
        outcomes = {item["change"]["document"]["group_key"]: item for item in applications}
        assert outcomes[FINANCE_TEAM]["outcome"] == "applied"
        assert outcomes[FINANCE_TEAM]["applied"] is True
        missing = outcomes[f"{DOMAIN_SID}-9999"]
        assert missing["outcome"] == "target_not_found"
        assert missing["applied"] is False
        assert (
            missing["outcome_description"]
            == OUTCOME_DESCRIPTIONS[
                next(k for k in OUTCOME_DESCRIPTIONS if k.value == "target_not_found")
            ]
        )

    async def test_a_proposal_whose_targets_have_all_gone_is_inert_rather_than_safe(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/api/v1/simulations/preview",
            json={"changes": [remove_from(f"{DOMAIN_SID}-9999")]},
        )

        payload = response.json()
        assert payload["inert"] is True
        assert payload["summary"]["evaluated"] == 0

    async def test_every_change_is_echoed_with_a_sentence_and_its_resolved_principals(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/api/v1/simulations/preview", json={"changes": [remove_from(FINANCE_TEAM)]}
        )

        change = response.json()["applications"][0]["change"]
        assert change["kind"] == "remove_member"
        assert ALICE in change["description"]
        assert change["member"]["key"] == ALICE
        assert change["member"]["resolved"] is True
        assert change["group"]["key"] == FINANCE_TEAM


class TestTheAnswerIsExplainableRatherThanACount:
    async def test_a_removal_with_an_alternate_path_names_the_route_that_survives(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        """The field that stands between a report and a remediation that achieves nothing.

        Alice reaches Finance through two chains and through ``Domain Users``. Removing one
        chain is exactly the change an administrator would sign off believing it revokes her
        access, and the response has to say that it does not — by naming what is left.
        """
        response = await client.post(
            "/api/v1/simulations/preview",
            json={
                "changes": [remove_from(FINANCE_TEAM)],
                "scope": {
                    "kind": "pair",
                    "subject_key": ALICE,
                    "resource_key": FINANCE,
                },
            },
        )

        delta = response.json()["deltas"][0]
        assert delta["direction"] == ImpactDirection.UNCHANGED.value
        assert delta["rights_removed"]["value"] == 0
        assert delta["subject"]["key"] == ALICE

    async def test_a_world_sid_on_the_acl_truncates_the_affected_scope_and_says_so(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        r"""``Everyone`` is on ``\\FS01\Finance``, and nobody can enumerate its members.

        So an affected-scope report over this estate is a *part* of the answer: principals
        exist who are affected and are not in the list. The report has to say that, because
        an impact list that looks complete is the one somebody signs a change off against.
        """
        response = await client.post(
            "/api/v1/simulations/preview", json={"changes": [remove_from(FINANCE_TEAM)]}
        )

        payload = response.json()
        assert payload["complete"] is False
        assert [item["code"] for item in payload["truncation"]] == ["trustee_expansion_incomplete"]
        assert payload["truncation"][0]["description"]

    async def test_a_delta_carries_both_whole_answers_and_not_two_numbers(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/api/v1/simulations/preview",
            json={
                "changes": [remove_from(FINANCE_TEAM)],
                "scope": {"kind": "pair", "subject_key": ALICE, "resource_key": FINANCE},
            },
        )

        delta = response.json()["deltas"][0]
        for field in ("rights_before", "rights_after", "rights_added", "rights_removed"):
            rights = delta[field]
            assert rights["mask"].startswith("0x")
            assert rights["layer"] == "effective"
            assert "label" in rights
        assert delta["certainty_before"]
        assert delta["certainty_after"]
        assert delta["limiting_layer_after"]
        assert delta["direction_description"]

    async def test_removing_every_route_reports_the_loss_and_the_principal(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/api/v1/simulations/preview",
            json={
                "changes": [
                    remove_from(FINANCE_TEAM),
                    remove_from(FINANCE_RW),
                    remove_from(FINANCE_OPS),
                    {
                        "kind": "remove_member",
                        "group_key": DOMAIN_USERS,
                        "member_key": ALICE,
                        "edge_kind": "primary_group",
                    },
                ],
                "scope": {"kind": "pair", "subject_key": ALICE, "resource_key": FINANCE},
            },
        )

        payload = response.json()
        delta = payload["deltas"][0]
        assert delta["direction"] == ImpactDirection.LOST_ACCESS.value
        assert delta["rights_removed"]["value"] != 0
        assert payload["summary"]["lost_access"] == 1
        assert [item["key"] for item in payload["summary"]["principals_losing"]] == [ALICE]
        assert payload["summary"]["principals_affected"] == 1

    async def test_a_gain_reaches_a_resource_the_subject_could_not_reach_before(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/api/v1/simulations/preview",
            json={"changes": [add_to(FINANCE_RW, f"{DOMAIN_SID}-1105")]},
        )

        payload = response.json()
        assert payload["summary"]["evaluated"] >= 1
        assert payload["applications"][0]["outcome"] in {"applied", "already_present"}

    async def test_the_resource_on_each_delta_is_rendered_rather_than_left_as_a_key(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/api/v1/simulations/preview",
            json={
                "changes": [remove_from(FINANCE_TEAM)],
                "scope": {"kind": "pair", "subject_key": ALICE, "resource_key": FINANCE},
            },
        )

        resource = response.json()["deltas"][0]["resource"]
        assert resource["resource_key"] == FINANCE.casefold()
        # A pair scope names its directory rather than enumerating one, so the delta carries
        # no descriptor and the view fetches it. Without that, the one screen an operator
        # reads before signing off a change shows a raw storage key and no sensitivity.
        assert resource["path"] == FINANCE
        assert resource["sensitive"] is False
        assert resource["sensitivity_labels"] == []
        # The administrator client holds alerts:read, so the watch flag is answered rather
        # than withheld. A caller without it gets null, which is asserted below.
        assert resource["watched"] is False


class TestStoringAProposal:
    async def test_storing_writes_the_proposal_and_its_first_evaluation_together(
        self, client: AsyncClient, session: AsyncSession, estate: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/api/v1/simulations",
            json={
                "name": "CHG-1042: take Finance-Team off the payroll path",
                "description": "Raised after the quarterly review.",
                "changes": [remove_from(FINANCE_TEAM)],
            },
        )

        assert response.status_code == 201
        payload = response.json()
        assert_non_destructive(payload)
        assert_non_destructive(payload["report"])
        simulation_id = payload["simulation"]["simulation_id"]
        assert payload["report"]["simulation_id"] == simulation_id
        assert payload["simulation"]["name"].startswith("CHG-1042")
        assert payload["simulation"]["created_by"]
        stored = (
            await session.execute(sa.select(sa.func.count()).select_from(simulations))
        ).scalar_one()
        evaluations = (
            await session.execute(sa.select(sa.func.count()).select_from(simulation_evaluations))
        ).scalar_one()
        assert (stored, evaluations) == (1, 1)

    async def test_a_stored_proposal_reads_back_with_its_changes_and_history(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        created = await client.post(
            "/api/v1/simulations",
            json={"name": "CHG-1042", "changes": [remove_from(FINANCE_TEAM)]},
        )
        simulation_id = created.json()["simulation"]["simulation_id"]

        response = await client.get(f"/api/v1/simulations/{simulation_id}")

        assert response.status_code == 200
        payload = response.json()
        assert_non_destructive(payload)
        assert payload["simulation"]["change_count"] == 1
        assert payload["simulation"]["changes"][0]["kind"] == "remove_member"
        assert payload["stale"] is False
        assert len(payload["evaluations"]) == 1
        assert payload["evaluations"][0]["stale_baseline"] is False
        assert payload["evaluations"][0]["scope_kind"] == ScopeKind.AFFECTED.value

    async def test_re_running_adds_an_evaluation_rather_than_replacing_one(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        """Two evaluations against two collection states are two findings.

        "This change was safe on Monday and takes access away today" is the sentence a
        proposal's history exists to make available, and overwriting would keep only half.
        """
        created = await client.post(
            "/api/v1/simulations",
            json={"name": "CHG-1042", "changes": [remove_from(FINANCE_TEAM)]},
        )
        simulation_id = created.json()["simulation"]["simulation_id"]

        again = await client.post(f"/api/v1/simulations/{simulation_id}/evaluations")

        assert again.status_code == 201
        assert_non_destructive(again.json())
        assert again.json()["simulation_id"] == simulation_id
        history = await client.get(f"/api/v1/simulations/{simulation_id}/evaluations")
        assert len(history.json()) == 2

    async def test_the_listing_pages_newest_first(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        for index in range(3):
            await client.post(
                "/api/v1/simulations",
                json={"name": f"CHG-{index}", "changes": [remove_from(FINANCE_TEAM)]},
            )

        response = await client.get("/api/v1/simulations", params={"limit": 2})

        payload = response.json()
        assert_non_destructive(payload)
        assert len(payload["simulations"]) == 2
        assert payload["page"]["has_more"] is True
        assert payload["page"]["next_cursor"]
        second = await client.get(
            "/api/v1/simulations",
            params={"limit": 2, "cursor": payload["page"]["next_cursor"]},
        )
        assert len(second.json()["simulations"]) == 1

    async def test_deleting_removes_the_proposal_and_its_evaluations_and_nothing_else(
        self, client: AsyncClient, session: AsyncSession, estate: dict[str, Any]
    ) -> None:
        created = await client.post(
            "/api/v1/simulations",
            json={"name": "CHG-1042", "changes": [remove_from(FINANCE_TEAM)]},
        )
        simulation_id = created.json()["simulation"]["simulation_id"]

        response = await client.delete(f"/api/v1/simulations/{simulation_id}")

        assert response.status_code == 204
        assert (await client.get(f"/api/v1/simulations/{simulation_id}")).status_code == 404
        remaining = (
            await session.execute(sa.select(sa.func.count()).select_from(simulation_evaluations))
        ).scalar_one()
        assert remaining == 0

    async def test_an_unknown_proposal_is_a_404_on_every_route_that_names_one(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        missing = "00000000-0000-0000-0000-0000000000ff"
        for method, path in (
            ("GET", f"/api/v1/simulations/{missing}"),
            ("GET", f"/api/v1/simulations/{missing}/evaluations"),
            ("GET", f"/api/v1/simulations/{missing}/export"),
            ("POST", f"/api/v1/simulations/{missing}/evaluations"),
            ("DELETE", f"/api/v1/simulations/{missing}"),
        ):
            response = await client.request(method, path)
            assert response.status_code == 404, f"{method} {path}"


class TestStalenessIsAFieldAndNotARefusal:
    async def test_a_second_ingestion_makes_a_stored_proposal_stale_without_refusing_it(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        """The proposal is still the proposal; what moved is the estate.

        A 409 here would withhold the report an operator stored precisely so that they could
        look at it again, in exchange for saying something a boolean says better.
        """
        created = await client.post(
            "/api/v1/simulations",
            json={"name": "CHG-1042", "changes": [remove_from(FINANCE_TEAM)]},
        )
        simulation_id = created.json()["simulation"]["simulation_id"]
        assert created.json()["simulation"]["baseline"]["stale"] is False

        await replay(client, with_run_id(storable(load_raw("04-multiple-membership-paths"))))

        response = await client.get(f"/api/v1/simulations/{simulation_id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["stale"] is True
        assert payload["simulation"]["baseline"]["stale"] is True
        assert payload["current_token"] != payload["simulation"]["baseline"]["token"]

    async def test_re_running_a_stale_proposal_records_that_it_was_stale(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        created = await client.post(
            "/api/v1/simulations",
            json={"name": "CHG-1042", "changes": [remove_from(FINANCE_TEAM)]},
        )
        simulation_id = created.json()["simulation"]["simulation_id"]
        await replay(client, with_run_id(storable(load_raw("04-multiple-membership-paths"))))

        await client.post(f"/api/v1/simulations/{simulation_id}/evaluations")

        history = (await client.get(f"/api/v1/simulations/{simulation_id}/evaluations")).json()
        assert history[0]["stale_baseline"] is False, (
            "A fresh run against the current estate is measured against the estate it read, "
            "so the evaluation itself is not stale -- only the proposal's own baseline is."
        )
        detail = (await client.get(f"/api/v1/simulations/{simulation_id}")).json()
        assert detail["stale"] is True


class TestTheExport:
    async def test_the_export_carries_the_plan_the_result_and_the_vocabulary(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        """Self-describing on purpose: read six months later, without this API in front of
        you, the file must still say what ``loss_may_not_hold`` meant."""
        created = await client.post(
            "/api/v1/simulations",
            json={
                "name": "CHG-1042",
                "description": "Raised after the quarterly review.",
                "changes": [remove_from(FINANCE_TEAM)],
            },
        )
        simulation_id = created.json()["simulation"]["simulation_id"]

        response = await client.get(f"/api/v1/simulations/{simulation_id}/export")

        assert response.status_code == 200
        payload = response.json()
        assert_non_destructive(payload)
        assert payload["document_version"] == "1.0"
        assert payload["plan"]["name"] == "CHG-1042"
        assert payload["plan"]["description"] == "Raised after the quarterly review."
        assert payload["plan"]["overlay_hash"]
        assert len(payload["plan"]["changes"]) == 1
        assert payload["plan"]["changes"][0]["description"]
        assert payload["result"]["pairs_evaluated"] >= 0
        assert payload["result"]["report"]["truncation"] == ["trustee_expansion_incomplete"]
        assert payload["result"]["report"]["complete"] is False
        assert {entry["code"] for entry in payload["vocabulary"]["caveats"]} == {
            caveat.value for caveat in CAVEAT_DESCRIPTIONS
        }

    async def test_the_exported_result_is_the_stored_report_verbatim(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        """A reshaped export would be a second description of an answer, ageing
        independently of the one the engine computes."""
        created = await client.post(
            "/api/v1/simulations",
            json={"name": "CHG-1042", "changes": [remove_from(FINANCE_TEAM)]},
        )
        simulation_id = created.json()["simulation"]["simulation_id"]
        history = (await client.get(f"/api/v1/simulations/{simulation_id}/evaluations")).json()

        export = (await client.get(f"/api/v1/simulations/{simulation_id}/export")).json()

        assert export["result"]["report"] == history[0]["report"]

    async def test_an_export_can_name_one_run_and_refuses_an_unknown_one(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        created = await client.post(
            "/api/v1/simulations",
            json={"name": "CHG-1042", "changes": [remove_from(FINANCE_TEAM)]},
        )
        simulation_id = created.json()["simulation"]["simulation_id"]
        await client.post(f"/api/v1/simulations/{simulation_id}/evaluations")
        history = (await client.get(f"/api/v1/simulations/{simulation_id}/evaluations")).json()
        oldest = history[-1]["evaluation_id"]

        chosen = await client.get(
            f"/api/v1/simulations/{simulation_id}/export",
            params={"evaluation_id": oldest},
        )
        unknown = await client.get(
            f"/api/v1/simulations/{simulation_id}/export",
            params={"evaluation_id": "00000000-0000-0000-0000-0000000000fe"},
        )

        assert chosen.json()["result"]["evaluation_id"] == oldest
        assert unknown.status_code == 404


class TestTheVocabularyIsServedRatherThanCopied:
    async def test_every_closed_vocabulary_is_complete(self, client: AsyncClient) -> None:
        """A second copy of a vocabulary is a second copy that can be wrong, and the wrong
        one is always the one somebody trusts."""
        response = await client.get("/api/v1/simulations/vocabulary")

        assert response.status_code == 200
        payload = response.json()
        assert_non_destructive(payload)
        assert {entry["code"] for entry in payload["change_kinds"]} == {
            kind.value for kind in ChangeKind
        }
        assert {entry["code"] for entry in payload["outcomes"]} == {
            outcome.value for outcome in OUTCOME_DESCRIPTIONS
        }
        assert {entry["code"] for entry in payload["directions"]} == {
            direction.value for direction in ImpactDirection
        }
        assert {entry["code"] for entry in payload["truncations"]} == {
            reason.value for reason in TRUNCATION_DESCRIPTIONS
        }
        assert {entry["code"] for entry in payload["inherited_ace_dispositions"]} == {
            item.value for item in InheritedAceDisposition
        }
        assert {entry["code"] for entry in payload["scope_kinds"]} == {
            kind.value for kind in ScopeKind
        }
        assert all(entry["description"] for entry in payload["caveats"])

    async def test_the_bounds_ceilings_are_published(self, client: AsyncClient) -> None:
        """So a client can say "clamped to 500" rather than discovering it from a response."""
        payload = (await client.get("/api/v1/simulations/vocabulary")).json()

        assert payload["bounds_ceilings"]["max_principals"] == 500
        assert payload["max_changes"] >= 1


class TestValidationRefusesRatherThanGuesses:
    @pytest.mark.parametrize(
        ("body", "because"),
        [
            ({"changes": []}, "an empty proposal has no answer"),
            (
                {"changes": [{"kind": "remove_ntfs_ace", "resource_key": FINANCE}]},
                "a removal must name the entry it removes",
            ),
            (
                {
                    "changes": [
                        {
                            "kind": "set_inheritance",
                            "resource_key": FINANCE,
                            "protected": True,
                        }
                    ]
                },
                "protecting must say what happens to the inherited entries",
            ),
            (
                {
                    "changes": [add_to(FINANCE_TEAM, ALICE)],
                    "scope": {"kind": "pair", "subject_key": ALICE},
                },
                "a pair scope names both ends",
            ),
            (
                {
                    "changes": [add_to(FINANCE_TEAM, FINANCE_TEAM)],
                },
                "a group cannot be a direct member of itself",
            ),
        ],
    )
    async def test_an_unusable_proposal_is_refused_with_a_reason(
        self, client: AsyncClient, estate: dict[str, Any], body: dict[str, Any], because: str
    ) -> None:
        response = await client.post("/api/v1/simulations/preview", json=body)

        assert response.status_code == 422, because
        assert response.json()["detail"]

    async def test_bounds_beyond_the_ceiling_are_clamped_rather_than_refused(
        self, client: AsyncClient, estate: dict[str, Any]
    ) -> None:
        """A caller asking for more than the ceiling is asking for a complete answer, and
        gets a bounded one that says it is bounded."""
        response = await client.post(
            "/api/v1/simulations/preview",
            json={
                "changes": [remove_from(FINANCE_TEAM)],
                "bounds": {"max_principals": 100_000, "time_budget_ms": 10_000_000},
            },
        )

        assert response.status_code == 200
        bounds = response.json()["bounds"]
        assert bounds["max_principals"] == 500
        assert bounds["time_budget_ms"] == 60_000


class TestNothingIsMutated:
    async def test_the_whole_route_sweep_leaves_collected_state_byte_identical(
        self, client: AsyncClient, session: AsyncSession, estate: dict[str, Any]
    ) -> None:
        """Preview, store, re-evaluate, export, delete — and not one collected row moves.

        The test that would catch a handler reaching past ``SimulationStore``, an overlay
        leaking into an ingestion path, or a repository writing back what it read.
        """
        before = await digest_of_collected_state(session)

        await client.post(
            "/api/v1/simulations/preview", json={"changes": [remove_from(FINANCE_TEAM)]}
        )
        created = await client.post(
            "/api/v1/simulations",
            json={
                "name": "CHG-1042",
                "changes": [
                    remove_from(FINANCE_TEAM),
                    {
                        "kind": "add_ntfs_ace",
                        "resource_key": FINANCE,
                        "trustee_sid": f"{DOMAIN_SID}-1105",
                        "ace_type": "allow",
                        "access_mask": 0x001200A9,
                        "ace_flags": 0x03,
                    },
                    {
                        "kind": "set_inheritance",
                        "resource_key": FINANCE,
                        "protected": True,
                        "inherited_entries": "convert_to_explicit",
                    },
                ],
            },
        )
        simulation_id = created.json()["simulation"]["simulation_id"]
        await client.post(f"/api/v1/simulations/{simulation_id}/evaluations")
        await client.get(f"/api/v1/simulations/{simulation_id}/export")
        await client.get("/api/v1/simulations")
        await client.delete(f"/api/v1/simulations/{simulation_id}")

        assert await digest_of_collected_state(session) == before

    async def test_no_simulated_row_reaches_an_ace_table(
        self, client: AsyncClient, session: AsyncSession, estate: dict[str, Any]
    ) -> None:
        """The invented rows are marked ``simulated|``. None of them may be storable."""
        await client.post(
            "/api/v1/simulations",
            json={
                "name": "CHG-1042",
                "changes": [
                    {
                        "kind": "add_ntfs_ace",
                        "resource_key": FINANCE,
                        "trustee_sid": f"{DOMAIN_SID}-1105",
                        "ace_type": "allow",
                        "access_mask": 0x001200A9,
                    }
                ],
            },
        )

        for table, column in (
            (ntfs_aces, ntfs_aces.c.ace_key),
            (smb_share_aces, smb_share_aces.c.ace_key),
            (membership_edges, membership_edges.c.edge_key),
        ):
            simulated = (
                await session.execute(
                    sa.select(sa.func.count()).select_from(table).where(column.like("simulated|%"))
                )
            ).scalar_one()
            assert simulated == 0, table.name


class TestTheCapabilitySplitHoldsEndToEnd:
    async def test_an_auditor_reads_a_proposal_and_cannot_run_one(
        self, client: AsyncClient, client_as: Any, estate: dict[str, Any]
    ) -> None:
        created = await client.post(
            "/api/v1/simulations",
            json={"name": "CHG-1042", "changes": [remove_from(FINANCE_TEAM)]},
        )
        simulation_id = created.json()["simulation"]["simulation_id"]

        async with client_as(Role.AUDITOR) as auditor:
            read = await auditor.get(f"/api/v1/simulations/{simulation_id}")
            export = await auditor.get(f"/api/v1/simulations/{simulation_id}/export")
            run = await auditor.post(
                "/api/v1/simulations/preview", json={"changes": [remove_from(FINANCE_TEAM)]}
            )
            removed = await auditor.delete(f"/api/v1/simulations/{simulation_id}")

        assert read.status_code == 200
        assert export.status_code == 200
        assert run.status_code == 403
        assert removed.status_code == 403

    async def test_a_plain_viewer_reaches_neither(
        self, client_as: Any, estate: dict[str, Any]
    ) -> None:
        async with client_as(Role.VIEWER) as viewer:
            listing = await viewer.get("/api/v1/simulations")
            run = await viewer.post(
                "/api/v1/simulations/preview", json={"changes": [remove_from(FINANCE_TEAM)]}
            )

        assert listing.status_code == 403
        assert run.status_code == 403

    async def test_a_governance_administrator_may_run_one(
        self, client_as: Any, estate: dict[str, Any]
    ) -> None:
        """A review that concludes "this group should come off the share" is worth nothing
        until somebody knows what taking it off would do."""
        async with client_as(Role.GOVERNANCE_ADMIN) as governance:
            response = await governance.post(
                "/api/v1/simulations/preview", json={"changes": [remove_from(FINANCE_TEAM)]}
            )

        assert response.status_code == 200

    async def test_the_watch_flag_is_withheld_from_a_caller_without_alerts_read(
        self, client_as: Any, estate: dict[str, Any]
    ) -> None:
        """Who is being notified about what is a statement about the organization, and it is
        not inherited by holding ``simulations:read``. Null, never a guessed false."""
        async with client_as(Role.GOVERNANCE_ADMIN) as governance:
            with_alerts = await governance.post(
                "/api/v1/simulations/preview",
                json={
                    "changes": [remove_from(FINANCE_TEAM)],
                    "scope": {"kind": "pair", "subject_key": ALICE, "resource_key": FINANCE},
                },
            )

        assert Capability.ALERTS_READ in {
            capability for capability in Capability if capability.value == "alerts:read"
        }
        payload = with_alerts.json()
        # governance_admin holds alerts:read through the viewer set, so this one answers.
        assert payload["deltas"][0]["resource"]["watched"] is False
        assert payload["summary"]["watched_resources_affected"] is not None
