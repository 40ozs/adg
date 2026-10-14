r"""ADG-DEMO-01 through the API: the SYSTEM mapping state, stored rows, and time.

The engine suites (``tests/access_engine/test_system_mapping.py`` and
``test_system_explanation_regression.py``) prove the state model over hand-built inputs. This
one proves it survives the trip that produced the original defect — collector transcripts,
PostgreSQL, the service, the API serializer — and that it moves with **newer evidence and
only with newer evidence**.

The estate is scenario ``01-direct-user-grant`` with the one entry every Windows share root
carries added in front: SYSTEM, Full Control. Its share ACL grants ``Everyone`` Full Control,
which is the exact shape that made the defective build answer *"no entry on either ACL names
it"* beside the entry naming SYSTEM. Every SID and name is synthetic.

The runs, in the order they are posted:

======  ===================  ==============================================================
run     ``observed_at``      what it says about ``S-1-5-18``
======  ===================  ==============================================================
A       2026-09-14 08:00     nothing: the ACE names it and no collector describes it
B       2026-09-14 08:30     ``unresolved`` (``lookup_failed``)
C       2026-09-14 09:00     ``well_known``, "SYSTEM"
D       2026-09-14 10:00     ``user``, "svc-system" — a description that contradicts the SID
E       2026-09-14 09:30     ``well_known`` — posted after D but **older** than it
F       2026-09-14 11:00     ``well_known`` — newer than D
======  ===================  ==============================================================
"""

from __future__ import annotations

import copy
from typing import Any
from urllib.parse import urlencode

import pytest
from httpx import AsyncClient
from jsonschema import Draft202012Validator

from app.contracts.derived import generate
from tests.support.ingest import replay, storable_document

pytestmark = pytest.mark.anyio

SYSTEM = "S-1-5-18"
ALICE = "S-1-5-21-1004336348-1177238915-682003330-1104"
FINANCE_UNC = "\\\\FS01\\Finance"
FULL_CONTROL = 0x001F01FF

SYSTEM_MAPPING_CONDITIONS = {
    "system_mapping_confirmed",
    "system_mapping_ambiguous",
    "system_mapping_missing",
    "system_mapping_conflicting",
}

RUN_A = "00000000-0000-4000-8000-0000000d0a01"
RUNS = {
    "B": "00000000-0000-4000-8000-0000000d0a02",
    "C": "00000000-0000-4000-8000-0000000d0a03",
    "D": "00000000-0000-4000-8000-0000000d0a04",
    "E": "00000000-0000-4000-8000-0000000d0a05",
    "F": "00000000-0000-4000-8000-0000000d0a06",
}


def estate_with_system_entry() -> dict[str, Any]:
    r"""Run A: scenario 01 with ``SYSTEM: Full Control`` first on ``\\FS01\Finance``.

    The SYSTEM entry goes in front, as Windows writes it, and the declared ``ace_count``
    follows, so the run's own description of the descriptor stays true.
    """
    document = storable_document("01-direct-user-grant", RUN_A)
    observations = document["batches"][0]["observations"]
    resource = next(item for item in observations if item["kind"] == "ntfs_resource")
    existing = [item for item in observations if item["kind"] == "ntfs_ace"]
    for item in existing:
        item["order_index"] += 1
    template = existing[0]
    system_ace = {
        **copy.deepcopy(template),
        "observed_at": "2026-09-14T08:00:06Z",
        "source_key": "ntfs_ace|\\\\fs01\\finance|S-1-5-18|allow|0x001f01ff|0x03",
        "trustee_sid": SYSTEM,
        "access_mask": FULL_CONTROL,
        "order_index": 0,
    }
    observations.insert(observations.index(template), system_ace)
    resource["ace_count"] = len(existing) + 1
    document["completion"]["observation_count"] += 1
    return document


def description_run(
    run_id: str,
    observed_at: str,
    principal_kind: str,
    *,
    display_name: str | None = None,
    unresolved_reason: str | None = None,
) -> dict[str, Any]:
    """A run whose only observation is what one collector says ``S-1-5-18`` is.

    It reconciles nothing: it describes one principal and makes no claim about the rest of
    the scope, so it cannot retire anything run A stored.
    """
    principal: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "principal",
        "run_id": run_id,
        "observed_at": observed_at,
        "source_key": f"principal|{SYSTEM}",
        "sid": SYSTEM,
        "principal_kind": principal_kind,
        "display_name": display_name,
    }
    if unresolved_reason is not None:
        principal["unresolved_reason"] = unresolved_reason
    return {
        "start": {
            "schema_version": "1.0",
            "run_id": run_id,
            "source": {
                "collector": "ntfs",
                "collector_host": "COLLECTOR01",
                "method": "LookupAccountSid",
                "collector_version": "0.1.0",
            },
            "started_at": observed_at,
            "scopes": [{"kind": "directory_tree", "key": "\\\\fs01\\finance"}],
            "incremental": False,
        },
        "batches": [
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "batch_id": run_id[:-12] + "00000000b" + run_id[-3:],
                "sequence": 1,
                "is_final": True,
                "observations": [principal],
            }
        ],
        "completion": {
            "schema_version": "1.0",
            "run_id": run_id,
            "status": "succeeded",
            "completed_at": observed_at,
            "batch_count": 1,
            "observation_count": 1,
            "error_count": 0,
            "errors": [],
            "reconciled_scopes": [],
        },
    }


DESCRIPTIONS: dict[str, dict[str, Any]] = {
    "B": {
        "observed_at": "2026-09-14T08:30:00Z",
        "principal_kind": "unresolved",
        "unresolved_reason": "lookup_failed",
    },
    "C": {
        "observed_at": "2026-09-14T09:00:00Z",
        "principal_kind": "well_known",
        "display_name": "SYSTEM",
    },
    "D": {
        "observed_at": "2026-09-14T10:00:00Z",
        "principal_kind": "user",
        "display_name": "svc-system",
    },
    "E": {
        "observed_at": "2026-09-14T09:30:00Z",
        "principal_kind": "well_known",
        "display_name": "SYSTEM",
    },
    "F": {
        "observed_at": "2026-09-14T11:00:00Z",
        "principal_kind": "well_known",
        "display_name": "SYSTEM",
    },
}


async def describe(client: AsyncClient, run: str) -> None:
    await replay(client, description_run(RUNS[run], **DESCRIPTIONS[run]))


async def explain(client: AsyncClient, principal: str = SYSTEM, **query: str) -> dict[str, Any]:
    response = await client.get(
        "/api/v1/access/explain?"
        + urlencode({"principal": principal, "resource": FINANCE_UNC, **query})
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    Draft202012Validator(generate("access-explanation.schema.json")).validate(body)
    return body


def mapping(body: dict[str, Any]) -> dict[str, Any]:
    """The one SYSTEM mapping finding, asserting there is exactly one and it is consistent."""
    found: list[dict[str, Any]] = [
        w for w in body["warnings"] if w["condition"] in SYSTEM_MAPPING_CONDITIONS
    ]
    assert len(found) == 1, f"expected one SYSTEM mapping finding, got {found}"
    finding = found[0]
    assert finding["condition"] == f"system_mapping_{finding['detail']['state']}"
    assert finding in body["effective"]["findings"], "warnings and findings must agree"
    assert finding["condition"] in body["effective"]["conditions"]
    return finding


def subject_matched(body: dict[str, Any]) -> list[str]:
    """Every applied entry the subject's own SID matched, across both layers."""
    layers = [body["effective"]["ntfs"], body["effective"]["share"]]
    return [
        applied["trustee"]["key"]
        for layer in layers
        if layer is not None
        for kind in ("granted_by", "denied_by", "superseded")
        for applied in layer[kind]
        if applied["matched_key"] == SYSTEM
    ]


def assert_no_contradiction(body: dict[str, Any]) -> None:
    """The invariant the demo broke, stated over the wire format a client actually reads."""
    verdict = body["verdict"]
    state = mapping(body)["detail"]["state"]
    assert verdict["certainty"] != "certain", "no SYSTEM token here is complete"
    assert verdict["outcome"] in ("granted", "indeterminate"), "never a conclusive negative"
    assert verdict["may_understate"]
    if state != "confirmed":
        assert verdict["may_overstate"]
    if verdict["outcome"] == "no_grant" and body["effective"]["ntfs"]["granted_by"]:
        assert "No entry on either ACL names it" not in verdict["reason"]
    if body["access_path"] == "remote_smb":
        assert subject_matched(body) == []
        assert all(path["chain"][-1]["key"] != SYSTEM for path in body["paths"])


@pytest.fixture
async def estate(client: AsyncClient) -> None:
    await replay(client, estate_with_system_entry())


class TestTheDemoQuestion:
    """The request that produced the contradiction, on a stored estate."""

    async def test_over_a_share_it_is_ambiguous_and_never_a_conclusive_no(
        self, client: AsyncClient, estate: None
    ) -> None:
        await describe(client, "C")
        body = await explain(client)

        assert_no_contradiction(body)
        assert mapping(body)["detail"]["state"] == "ambiguous"
        assert mapping(body)["detail"]["presented"] is False
        assert body["verdict"]["outcome"] == "indeterminate"
        assert body["verdict"]["conclusive"] is False
        assert body["paths"] == []
        assert "computer account" in body["verdict"]["reason"]

    async def test_locally_it_is_confirmed_with_provenance(
        self, client: AsyncClient, estate: None
    ) -> None:
        await describe(client, "C")
        body = await explain(client, access_path="local")
        detail = mapping(body)["detail"]

        assert_no_contradiction(body)
        assert detail["state"] == "confirmed"
        assert detail["described_as"] == "well_known"
        assert detail["last_observed_run_id"] == RUNS["C"]
        assert detail["last_observed_at"].startswith("2026-09-14T09:00:00")
        assert body["subject"]["display_name"] == "SYSTEM"
        assert body["verdict"]["outcome"] == "granted"
        assert body["verdict"]["certainty"] == "at_least"
        assert subject_matched(body) == [SYSTEM]

    async def test_the_authenticated_variant_grants_only_what_every_session_carries(
        self, client: AsyncClient, estate: None
    ) -> None:
        await describe(client, "C")
        body = await explain(client, assumption="authenticated_user")

        assert_no_contradiction(body)
        assert mapping(body)["detail"]["state"] == "ambiguous"
        assert body["verdict"]["outcome"] == "indeterminate", (
            "nothing on this ACL is granted to Everyone, Authenticated Users or NETWORK"
        )

    async def test_other_subjects_carry_no_system_mapping(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = await explain(client, principal=ALICE)

        assert not [w for w in body["warnings"] if w["condition"] in SYSTEM_MAPPING_CONDITIONS]
        assert body["verdict"]["outcome"] == "granted"


class TestMissingEvidence:
    async def test_a_sid_nothing_stores_is_not_answered_with_an_invented_mapping(
        self, client: AsyncClient, estate: None
    ) -> None:
        """Run A only: an ACE names S-1-5-18 and no row describes it. 404, not a guess."""
        response = await client.get(
            "/api/v1/access/explain?"
            + urlencode({"principal": SYSTEM, "resource": FINANCE_UNC, "access_path": "local"})
        )

        assert response.status_code == 404
        assert "Nothing is stored about" in response.text

    async def test_an_unresolved_description_is_missing_not_confirmed(
        self, client: AsyncClient, estate: None
    ) -> None:
        await describe(client, "B")
        body = await explain(client, access_path="local")
        detail = mapping(body)["detail"]

        assert_no_contradiction(body)
        assert detail["state"] == "missing"
        assert detail["described_as"] == "unresolved"
        assert body["subject"]["kind"] == "unresolved"
        assert body["verdict"]["certainty"] == "uncertain"
        assert "no collector has described S-1-5-18" in body["verdict"]["reason"]
        assert body["token"]["assumption"] == "sids_only"
        assert [entry["principal"]["key"] for entry in body["token"]["entries"]] == [SYSTEM]


class TestConflictingEvidence:
    async def test_the_conflict_and_its_provenance_reach_the_client(
        self, client: AsyncClient, estate: None
    ) -> None:
        await describe(client, "C")
        await describe(client, "D")
        body = await explain(client, access_path="local")
        detail = mapping(body)["detail"]

        assert_no_contradiction(body)
        assert detail["state"] == "conflicting"
        assert detail["described_as"] == "user"
        assert detail["described_name"] == "svc-system"
        assert detail["expected_kind"] == "well_known"
        assert detail["last_observed_run_id"] == RUNS["D"]
        assert body["verdict"]["certainty"] == "uncertain"
        assert "disputed" in body["verdict"]["reason"]


class TestNewerEvidence:
    """The state changes when, and only when, a newer description supports the change."""

    async def test_the_state_follows_the_newest_description_through_every_run(
        self, client: AsyncClient, estate: None
    ) -> None:
        async def state(**query: str) -> str:
            body = await explain(client, **query)
            assert_no_contradiction(body)
            return str(mapping(body)["detail"]["state"])

        await describe(client, "B")
        assert await state(access_path="local") == "missing"
        assert await state() == "ambiguous"

        await describe(client, "C")
        assert await state(access_path="local") == "confirmed"
        assert await state() == "ambiguous", "a better description never settles the share"

        await describe(client, "D")
        assert await state(access_path="local") == "conflicting"
        assert await state() == "conflicting", "and a dispute is never hidden behind it"

        await describe(client, "E")
        assert await state(access_path="local") == "conflicting", (
            "an older well-known reading posted late must not settle a newer dispute"
        )

        await describe(client, "F")
        assert await state(access_path="local") == "confirmed"
        assert await state() == "ambiguous"

    async def test_a_cached_answer_is_not_served_across_a_change_of_state(
        self, client: AsyncClient, estate: None
    ) -> None:
        await describe(client, "B")
        url = "/api/v1/access/explain?" + urlencode(
            {"principal": SYSTEM, "resource": FINANCE_UNC, "access_path": "local"}
        )
        first = await client.get(url)
        etag = first.headers["etag"]
        assert (await client.get(url, headers={"If-None-Match": etag})).status_code == 304

        await describe(client, "C")
        second = await client.get(url, headers={"If-None-Match": etag})

        assert second.status_code == 200
        assert mapping(second.json())["detail"]["state"] == "confirmed"


class TestTheResourceListingAgrees:
    """Who can reach this — the same token logic, so the same answer about SYSTEM."""

    async def test_system_over_a_share_is_not_listed_as_holding_full_control(
        self, client: AsyncClient, estate: None
    ) -> None:
        await describe(client, "C")
        response = await client.get("/api/v1/access/resources/%5C%5CFS01%5CFinance/principals")
        assert response.status_code == 200, response.text
        listed = {item["principal"]["key"]: item for item in response.json()["items"]}

        system = listed.get(SYSTEM)
        assert system is not None, "SYSTEM is named on the ACL, so it is a candidate"
        assert system["access"] is False
        assert system["certainty"] == "uncertain"
        assert "system_mapping_ambiguous" in system["conditions"]
        assert listed[ALICE]["access"] is True
