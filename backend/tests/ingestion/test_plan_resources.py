r"""Planning the SMB half of a batch: keys, trustee scoping, and what is still refused.

No database here, for the same reason as `test_plan.py`: that a stored key equals the key
the contract derived, that a BUILTIN trustee is scoped to the server whose ACL named it, and
that an NTFS payload is still rejected rather than dropped, are all properties of the
translation.
"""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from app.contracts.v1 import (
    ObservationBatch,
    ServerObservation,
    SmbAceObservation,
    SmbShareObservation,
    keys,
)
from app.domain import AceType, SharePermission, Sid
from app.ingestion import UnsupportedObservationKind, plan_batch
from app.models.schema import ReferenceKind
from tests.fixtures import load_raw

RUN_ID = "6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31"
BATCH_ID = "b1a7c0de-1111-4222-8333-444455556666"
OBSERVED = dt.datetime(2026, 9, 14, 8, tzinfo=dt.UTC)
LATER = dt.datetime(2026, 9, 14, 9, tzinfo=dt.UTC)
DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"


def server(**overrides: object) -> ServerObservation:
    payload: dict[str, object] = {
        "kind": "server",
        "run_id": RUN_ID,
        "observed_at": OBSERVED,
        "name": "FS01",
        **overrides,
    }
    payload.setdefault("source_key", keys.server_key(str(payload["name"])))
    return ServerObservation.model_validate(payload)


def share(**overrides: object) -> SmbShareObservation:
    payload: dict[str, object] = {
        "kind": "smb_share",
        "run_id": RUN_ID,
        "observed_at": OBSERVED,
        "server_name": "FS01",
        "share_name": "Finance",
        **overrides,
    }
    payload.setdefault(
        "source_key",
        keys.share_key(str(payload["server_name"]), str(payload["share_name"])),
    )
    return SmbShareObservation.model_validate(payload)


def ace(**overrides: object) -> SmbAceObservation:
    payload: dict[str, object] = {
        "kind": "smb_ace",
        "run_id": RUN_ID,
        "observed_at": OBSERVED,
        "server_name": "FS01",
        "share_name": "Finance",
        "trustee_sid": "S-1-1-0",
        "ace_type": "allow",
        "permission": "read",
        **overrides,
    }
    payload.setdefault(
        "source_key",
        keys.smb_ace_key(
            str(payload["server_name"]),
            str(payload["share_name"]),
            Sid(str(payload["trustee_sid"])),
            str(payload["ace_type"]),
            payload.get("access_mask"),  # type: ignore[arg-type]
            payload.get("permission"),  # type: ignore[arg-type]
        ),
    )
    return SmbAceObservation.model_validate(payload)


def batch(*observations: object) -> ObservationBatch:
    return ObservationBatch.model_validate(
        {
            "run_id": RUN_ID,
            "batch_id": BATCH_ID,
            "sequence": 1,
            "observations": [item.model_dump(mode="json") for item in observations],  # type: ignore[attr-defined]
        }
    )


class TestKeysMatchTheContract:
    def test_a_server_row_is_keyed_by_its_domain_identity(self) -> None:
        plan = plan_batch(batch(server(dns_host_name="fs01.corp.example.com")))

        row = plan.servers[0]
        assert row.server_key == "fs01"
        assert row.source_key == "server|fs01"
        assert row.name == "FS01", "the observed spelling is kept for display"
        assert row.dns_host_name == "fs01.corp.example.com"

    def test_a_share_row_is_keyed_by_server_and_name(self) -> None:
        plan = plan_batch(batch(share(local_path="D:\\Shares\\Finance")))

        row = plan.shares[0]
        assert row.share_key == "fs01|finance"
        assert row.server_key == "fs01"
        assert row.name == "Finance"
        assert row.local_path == "D:\\Shares\\Finance"

    def test_a_share_row_stores_no_unc_path_and_no_hidden_flag(self) -> None:
        # Both are exact functions of the identity. A stored copy is a second version of
        # the truth that can disagree with the first.
        row = plan_batch(batch(share())).shares[0]
        assert not hasattr(row, "unc_path")
        assert not hasattr(row, "is_hidden")

    def test_an_ace_row_is_keyed_inside_its_share(self) -> None:
        plan = plan_batch(batch(ace(order_index=0)))

        row = plan.share_aces[0]
        assert row.ace_key == "fs01|finance|S-1-1-0|allow|read"
        assert row.share_key == "fs01|finance"
        assert row.source_key == f"smb_ace|{row.ace_key}"
        assert row.right_token == "read"

    def test_a_mask_ace_records_the_mask_and_no_permission(self) -> None:
        row = plan_batch(batch(ace(permission=None, access_mask=0x001301BF))).share_aces[0]

        assert row.access_mask == 0x001301BF
        assert row.permission is None
        assert row.right_token == "0x001301bf"

    def test_the_maximum_mask_survives_planning(self) -> None:
        # 0xFFFFFFFF overflows a signed 32-bit column; the row carries it as a plain int and
        # the column is bigint.
        row = plan_batch(batch(ace(permission=None, access_mask=0xFFFFFFFF))).share_aces[0]
        assert row.access_mask == 0xFFFFFFFF


class TestTrusteeScoping:
    def test_a_builtin_trustee_is_scoped_to_the_server_whose_acl_named_it(self) -> None:
        row = plan_batch(batch(ace(trustee_sid="S-1-5-32-544"))).share_aces[0]

        assert row.trustee_sid == "S-1-5-32-544"
        assert row.trustee_key == "fs01|S-1-5-32-544"

    def test_the_same_builtin_trustee_on_two_servers_gives_two_keys(self) -> None:
        here = plan_batch(batch(ace(trustee_sid="S-1-5-32-544"))).share_aces[0]
        there = plan_batch(batch(ace(server_name="FS02", trustee_sid="S-1-5-32-544"))).share_aces[0]

        assert here.trustee_key != there.trustee_key

    @pytest.mark.parametrize("sid", ["S-1-1-0", f"{DOMAIN_SID}-1104"])
    def test_a_global_trustee_keeps_its_global_key(self, sid: str) -> None:
        row = plan_batch(batch(ace(trustee_sid=sid))).share_aces[0]
        assert row.trustee_key == sid


class TestPrincipalReferences:
    def test_every_ace_records_a_reference_to_its_trustee(self) -> None:
        plan = plan_batch(batch(ace()))

        reference = plan.references[0]
        assert reference.principal_key == "S-1-1-0"
        assert reference.reference_kind == ReferenceKind.SMB_ACE.value
        assert reference.reference_key == "fs01|finance"

    def test_the_reference_carries_no_resolution(self) -> None:
        # Resolution is a join. A stored flag would be right only until the next AD run
        # described the SID.
        reference = plan_batch(batch(ace())).references[0]
        assert not hasattr(reference, "resolved")
        assert not hasattr(reference, "principal_kind")

    def test_two_entries_for_one_trustee_produce_one_reference(self) -> None:
        # Three grants to one group is one group that can reach the share.
        plan = plan_batch(
            batch(
                ace(ace_type="allow", permission="read"),
                ace(ace_type="deny", permission="full"),
            )
        )

        assert len(plan.share_aces) == 2
        assert len(plan.references) == 1

    def test_a_builtin_reference_records_the_host_that_scoped_it(self) -> None:
        reference = plan_batch(batch(ace(trustee_sid="S-1-5-32-544"))).references[0]

        assert reference.principal_key == "fs01|S-1-5-32-544"
        assert reference.host_key == "fs01"

    def test_a_global_reference_records_no_host(self) -> None:
        # The column answers "was this SID read in one machine's context", and for a domain
        # SID the answer is no even though a server reported it.
        reference = plan_batch(batch(ace(trustee_sid=f"{DOMAIN_SID}-1104"))).references[0]
        assert reference.host_key is None


class TestDeduplicationWithinABatch:
    def test_one_share_described_twice_in_a_batch_is_refused_by_the_envelope(self) -> None:
        # Two spellings of one share are two readings of the same object. The envelope
        # refuses them before planning, because the second would silently overwrite the
        # first and a collector would never learn which reading survived.
        with pytest.raises(ValidationError) as caught:
            batch(
                share(description="old", observed_at=OBSERVED),
                share(
                    description="new",
                    observed_at=LATER,
                    source_key="share|fs01|finance",
                    share_name="FINANCE",
                ),
            )

        assert "share|fs01|finance" in str(caught.value)

    def test_rows_come_back_in_key_order(self) -> None:
        plan = plan_batch(
            batch(
                share(share_name="Payroll"),
                share(share_name="Finance"),
                server(name="FS02"),
                server(name="FS01"),
            )
        )

        assert [row.share_key for row in plan.shares] == ["fs01|finance", "fs01|payroll"]
        assert [row.server_key for row in plan.servers] == ["fs01", "fs02"]


class TestProvenance:
    def test_each_observation_points_at_the_object_it_describes(self) -> None:
        plan = plan_batch(batch(server(), share(), ace()))

        subjects = {row.kind: row.subject_key for row in plan.observations}
        assert subjects == {
            "server": "fs01",
            "smb_share": "fs01|finance",
            "smb_ace": "fs01|finance|S-1-1-0|allow|read",
        }

    def test_the_observation_count_is_what_was_sent(self) -> None:
        plan = plan_batch(batch(server(), share(), ace()))
        assert plan.observation_count == 3


class TestWhatIsStillRefused:
    def test_the_smb_kinds_are_no_longer_rejected(self) -> None:
        plan = plan_batch(batch(server(), share(), ace()))
        assert (len(plan.servers), len(plan.shares), len(plan.share_aces)) == (1, 1, 1)

    def test_an_ntfs_payload_is_still_refused(self) -> None:
        full = ObservationBatch.model_validate(load_raw("10-smb-more-restrictive")["batches"][0])

        with pytest.raises(UnsupportedObservationKind) as caught:
            plan_batch(full)

        assert set(caught.value.kinds) == {"ntfs_resource", "ntfs_ace"}
        assert "smb_share" not in caught.value.kinds
        assert "later phase" in str(caught.value)

    def test_the_rejection_lists_what_the_endpoint_does_store(self) -> None:
        # A collector author reading the 422 needs to know what to send instead.
        with pytest.raises(UnsupportedObservationKind) as caught:
            plan_batch(
                ObservationBatch.model_validate(load_raw("01-direct-user-grant")["batches"][0])
            )

        for kind in ("server", "smb_share", "smb_ace", "principal", "membership_edge"):
            assert kind in str(caught.value)


class TestPlanningARealTranscript:
    def test_the_storable_half_of_every_scenario_plans_cleanly(self) -> None:
        storable = {"principal", "membership_edge", "server", "smb_share", "smb_ace"}
        document = load_raw("04-multiple-membership-paths")
        payload = document["batches"][0]
        payload["observations"] = [
            item for item in payload["observations"] if item["kind"] in storable
        ]

        plan = plan_batch(ObservationBatch.model_validate(payload))

        assert plan.servers and plan.shares and plan.share_aces
        assert plan.principals and plan.edges
        # Every ACE contributed exactly one reference, and nothing else did.
        assert len(plan.references) <= len(plan.share_aces)

    def test_an_ace_reported_as_a_level_keeps_the_level(self) -> None:
        payload = load_raw("10-smb-more-restrictive")["batches"][0]
        payload["observations"] = [
            item for item in payload["observations"] if item["kind"] == "smb_ace"
        ]

        row = plan_batch(ObservationBatch.model_validate(payload)).share_aces[0]

        assert row.permission == SharePermission.READ.value
        assert row.access_mask is None
        assert row.ace_type == AceType.ALLOW.value
