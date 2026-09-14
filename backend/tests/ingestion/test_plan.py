"""Planning a batch: keys, aliases, and what is refused.

No database here on purpose. The properties that matter — that a stored key equals the key
the contract derived, that a local group stays host-scoped, that an unstorable kind is
rejected rather than dropped — are properties of the translation, and testing them without
PostgreSQL means they are checked on every ordinary test run.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter

import pytest

from app.contracts.v1 import MembershipObservation, ObservationBatch, PrincipalObservation, keys
from app.contracts.v1.common import SourceDescriptor
from app.domain import CollectorKind, DomainValidationError, MembershipEdgeKind, PrincipalKind, Sid
from app.ingestion import UnsupportedObservationKind, plan_batch, source_fingerprint
from app.models.schema import AliasKind
from tests.fixtures import load_raw, load_scenario

RUN_ID = "6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31"
BATCH_ID = "b1a7c0de-1111-4222-8333-444455556666"
OBSERVED = dt.datetime(2026, 9, 14, 8, tzinfo=dt.UTC)
DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"


def principal(**overrides: object) -> PrincipalObservation:
    payload: dict[str, object] = {
        "kind": "principal",
        "run_id": RUN_ID,
        "observed_at": OBSERVED,
        "sid": f"{DOMAIN_SID}-1104",
        "principal_kind": "user",
        **overrides,
    }
    payload.setdefault(
        "source_key",
        keys.principal_key(
            Sid(str(payload["sid"])),
            PrincipalKind(str(payload["principal_kind"])),
            payload.get("host_key"),  # type: ignore[arg-type]
        ),
    )
    return PrincipalObservation.model_validate(payload)


def membership(**overrides: object) -> MembershipObservation:
    payload: dict[str, object] = {
        "kind": "membership_edge",
        "run_id": RUN_ID,
        "observed_at": OBSERVED,
        "group_sid": f"{DOMAIN_SID}-1201",
        "member_sid": f"{DOMAIN_SID}-1104",
        "edge_kind": "directory_group_member",
        **overrides,
    }
    payload.setdefault(
        "source_key",
        keys.membership_key(
            Sid(str(payload["group_sid"])),
            Sid(str(payload["member_sid"])),
            MembershipEdgeKind(str(payload["edge_kind"])),
            payload.get("host_key"),  # type: ignore[arg-type]
        ),
    )
    return MembershipObservation.model_validate(payload)


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
    def test_a_principal_row_is_keyed_by_its_domain_identity(self) -> None:
        plan = plan_batch(batch(principal()))

        row = plan.principals[0]
        assert row.principal_key == f"{DOMAIN_SID}-1104"
        assert row.source_key == f"principal|{DOMAIN_SID}-1104"
        assert row.host_key is None
        assert row.domain_sid == DOMAIN_SID

    def test_a_local_group_row_stays_host_scoped(self) -> None:
        plan = plan_batch(
            batch(
                principal(
                    sid="S-1-5-32-544",
                    principal_kind="local_group",
                    host_key="FS01",
                    source_key="principal|fs01|S-1-5-32-544",
                )
            )
        )

        row = plan.principals[0]
        assert row.principal_key == "fs01|S-1-5-32-544"
        assert row.host_key == "fs01", "the stored host must be folded, like the key"

    def test_an_edge_row_is_keyed_by_the_domain_edge_identity(self) -> None:
        plan = plan_batch(batch(membership()))

        row = plan.edges[0]
        assert row.edge_key == f"{DOMAIN_SID}-1201->{DOMAIN_SID}-1104|directory_group_member"
        assert row.group_key == f"{DOMAIN_SID}-1201"
        assert row.member_key == f"{DOMAIN_SID}-1104"

    def test_a_local_edge_scopes_the_group_but_not_a_domain_member(self) -> None:
        # A domain user inside BUILTIN\Administrators on FS01 keeps its global key; only the
        # group is host-scoped. Scoping the member too would fragment one user into many.
        plan = plan_batch(
            batch(
                membership(
                    group_sid="S-1-5-32-544",
                    member_sid=f"{DOMAIN_SID}-1104",
                    edge_kind="local_group_member",
                    host_key="FS01",
                )
            )
        )

        row = plan.edges[0]
        assert row.group_key == "fs01|S-1-5-32-544"
        assert row.member_key == f"{DOMAIN_SID}-1104"
        assert row.host_key == "fs01"

    def test_a_builtin_member_of_a_local_group_is_host_scoped_too(self) -> None:
        plan = plan_batch(
            batch(
                membership(
                    group_sid="S-1-5-32-544",
                    member_sid="S-1-5-32-545",
                    edge_kind="local_group_member",
                    host_key="fs01",
                )
            )
        )

        row = plan.edges[0]
        assert row.group_key == "fs01|S-1-5-32-544"
        assert row.member_key == "fs01|S-1-5-32-545"

    def test_the_subject_of_every_observation_is_the_row_it_justifies(self) -> None:
        plan = plan_batch(batch(principal(), membership()))

        subjects = {row.kind: row.subject_key for row in plan.observations}
        assert subjects["principal"] == plan.principals[0].principal_key
        assert subjects["membership_edge"] == plan.edges[0].edge_key


class TestAliases:
    def test_every_name_like_field_becomes_an_alias(self) -> None:
        plan = plan_batch(
            batch(
                principal(
                    display_name="Alice Smith",
                    sam_account_name="asmith",
                    user_principal_name="asmith@corp.example",
                    distinguished_name="CN=Alice Smith,OU=Users,DC=corp,DC=example",
                )
            )
        )

        assert {alias.alias_kind for alias in plan.aliases} == {
            AliasKind.DISPLAY_NAME.value,
            AliasKind.SAM_ACCOUNT_NAME.value,
            AliasKind.USER_PRINCIPAL_NAME.value,
            AliasKind.DISTINGUISHED_NAME.value,
        }

    def test_alias_uniqueness_folds_case(self) -> None:
        plan = plan_batch(batch(principal(display_name="Finance-RW")))

        alias = next(item for item in plan.aliases if item.alias_kind == "display_name")
        assert alias.value == "Finance-RW"
        assert alias.value_folded == "finance-rw"

    def test_an_unresolved_principal_contributes_only_its_last_known_name(self) -> None:
        plan = plan_batch(
            batch(
                principal(
                    principal_kind="unresolved",
                    unresolved_reason="deleted",
                    last_known_name="svc-backup",
                )
            )
        )

        assert [alias.alias_kind for alias in plan.aliases] == [AliasKind.LAST_KNOWN_NAME.value]
        assert plan.principals[0].unresolved_reason == "deleted"

    def test_a_missing_unresolved_reason_defaults_rather_than_nulling(self) -> None:
        # NULL would read as "this principal is not unresolved", which is the opposite fact.
        plan = plan_batch(batch(principal(principal_kind="unresolved")))

        assert plan.principals[0].unresolved_reason == "unknown"

    def test_a_principal_with_no_names_produces_no_aliases(self) -> None:
        plan = plan_batch(batch(principal()))

        assert plan.aliases == ()


class TestRejection:
    @pytest.mark.parametrize(
        "scenario_name", ["08-inherited-ace", "10-smb-more-restrictive", "11-ntfs-more-restrictive"]
    )
    def test_every_observation_in_a_scenario_is_planned(self, scenario_name: str) -> None:
        # These three batches used to be refused for carrying NTFS kinds. Phase 3A stores
        # them, so the property worth pinning is the stronger one: nothing in a published
        # scenario is dropped, and the row count matches the observation count kind by kind.
        scenario = load_scenario(scenario_name)
        full = ObservationBatch.model_validate(load_raw(scenario_name)["batches"][0])

        plan = plan_batch(full)

        sent = Counter(item.kind for item in scenario.observations)
        planned = {
            "principal": len(plan.principals),
            "membership_edge": len(plan.edges),
            "server": len(plan.servers),
            "smb_share": len(plan.shares),
            "smb_ace": len(plan.share_aces),
            "ntfs_resource": len(plan.ntfs_resources),
            "ntfs_ace": len(plan.ntfs_aces),
        }
        for kind, count in sent.items():
            assert planned[kind] == count, f"{kind} observations were sent but not planned"
        assert plan.observation_count == len(scenario.observations)

    def test_the_rejection_names_every_offending_kind(self) -> None:
        # Raised directly: no contract v1 kind reaches this path any more, and the guard has
        # to keep working for whichever kind a later contract adds.
        with pytest.raises(UnsupportedObservationKind) as caught:
            raise UnsupportedObservationKind(["registry_key", "scheduled_task"])

        assert caught.value.kinds == ("registry_key", "scheduled_task")
        assert "principal" not in caught.value.kinds
        assert "membership_edge" not in caught.value.kinds

    def test_the_rejection_is_a_domain_validation_error(self) -> None:
        # Which is what maps it to a 422 rather than a 500 at the API boundary.
        assert issubclass(UnsupportedObservationKind, DomainValidationError)


class TestSourceFingerprint:
    def descriptor(self, **overrides: object) -> SourceDescriptor:
        payload: dict[str, object] = {
            "collector": CollectorKind.ACTIVE_DIRECTORY,
            "collector_host": "COLLECTOR01",
            "method": "Get-ADGroupMember",
            **overrides,
        }
        return SourceDescriptor.model_validate(payload)

    def test_the_same_source_fingerprints_identically(self) -> None:
        assert source_fingerprint(self.descriptor()) == source_fingerprint(self.descriptor())

    def test_the_host_comparison_is_case_insensitive(self) -> None:
        assert source_fingerprint(self.descriptor()) == source_fingerprint(
            self.descriptor(collector_host="collector01")
        )

    def test_a_different_method_is_a_different_source(self) -> None:
        # The mechanism is part of the evidence: different APIs report different detail.
        assert source_fingerprint(self.descriptor()) != source_fingerprint(
            self.descriptor(method="Get-ADObject")
        )

    def test_fields_cannot_collide_by_concatenation(self) -> None:
        a = self.descriptor(method="abc", collector_version="def")
        b = self.descriptor(method="abcdef")
        assert source_fingerprint(a) != source_fingerprint(b)


class TestPlanShape:
    def test_a_scenario_plans_exactly_its_ad_observations(self) -> None:
        scenario = load_scenario("04-multiple-membership-paths")
        document = load_raw("04-multiple-membership-paths")
        ad_only = [
            item
            for item in document["batches"][0]["observations"]
            if item["kind"] in {"principal", "membership_edge"}
        ]
        reduced = ObservationBatch.model_validate(
            {**document["batches"][0], "observations": ad_only}
        )

        plan = plan_batch(reduced)

        assert len(plan.principals) == len(scenario.principals)
        assert len(plan.edges) == len(scenario.edges)
        assert plan.observation_count == len(ad_only)
        assert not plan.is_empty

    def test_rows_are_sorted_so_a_plan_is_deterministic(self) -> None:
        plan = plan_batch(
            batch(
                principal(sid=f"{DOMAIN_SID}-1300"),
                principal(sid=f"{DOMAIN_SID}-1100"),
                principal(sid=f"{DOMAIN_SID}-1200"),
            )
        )

        assert [row.principal_key for row in plan.principals] == sorted(
            row.principal_key for row in plan.principals
        )

    def test_the_run_and_batch_ids_are_carried_through_as_uuids(self) -> None:
        plan = plan_batch(batch(principal()))

        assert plan.run_id == uuid.UUID(RUN_ID)
        assert plan.batch_id == uuid.UUID(BATCH_ID)
        assert all(row.run_id == plan.run_id for row in plan.principals)
