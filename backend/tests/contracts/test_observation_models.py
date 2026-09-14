"""Backend request models: what the API accepts, rejects, and converts to.

The models validate more than JSON Schema can express — cross-field rules, canonical SID
form, source-key derivation — and their rejection messages are read by a collector author,
so several tests assert on the message, not only on the failure.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts.v1 import keys
from app.contracts.v1.observations import (
    MembershipObservation,
    NtfsAceObservation,
    NtfsResourceObservation,
    PrincipalObservation,
    ServerObservation,
    SmbAceObservation,
    SmbShareObservation,
)
from app.domain import (
    AceFlag,
    DirectoryResource,
    DomainGroup,
    LocalGroup,
    MembershipEdge,
    MembershipEdgeKind,
    NtfsAce,
    NtfsRight,
    PrincipalKind,
    Server,
    Sid,
    SmbShare,
    SmbShareAce,
    UnresolvedPrincipal,
    User,
)

RUN_ID = "6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31"
OBSERVED_AT = "2026-09-14T08:00:00Z"
DOMAIN = "S-1-5-21-1004336348-1177238915-682003330"
ALICE = f"{DOMAIN}-1104"
FINANCE_TEAM = f"{DOMAIN}-1201"
FINANCE_RW = f"{DOMAIN}-1202"
ADMINISTRATORS = "S-1-5-32-544"
MODIFY = 0x001301BF
READ_EXECUTE = 0x001200A9


def base(**fields: Any) -> dict[str, Any]:
    return {"run_id": RUN_ID, "observed_at": OBSERVED_AT, **fields}


def principal(**fields: Any) -> PrincipalObservation:
    sid = fields.pop("sid", ALICE)
    kind = fields.pop("principal_kind", "user")
    host_key = fields.get("host_key")
    # Derived lazily: a test that supplies its own (deliberately wrong) key must not have
    # the derivation run anyway, or the error would come from the helper, not the model.
    source_key = fields.pop("source_key", None) or keys.principal_key(
        Sid(sid), PrincipalKind(kind), host_key
    )
    return PrincipalObservation(
        **base(sid=sid, principal_kind=kind, source_key=source_key, **fields)
    )


class TestSourceKeys:
    def test_a_correct_key_is_accepted(self) -> None:
        observation = principal()

        assert observation.source_key == f"principal|{ALICE}"

    def test_a_mismatched_key_is_rejected_with_the_expected_value(self) -> None:
        with pytest.raises(ValidationError) as caught:
            principal(source_key="principal|whatever")

        message = str(caught.value)
        assert "does not match the derivation" in message
        assert f"principal|{ALICE}" in message

    def test_a_local_group_key_is_host_scoped(self) -> None:
        observation = principal(sid=ADMINISTRATORS, principal_kind="local_group", host_key="FS01")

        assert observation.source_key == f"principal|fs01|{ADMINISTRATORS}"

    def test_the_same_local_group_on_two_hosts_yields_two_keys(self) -> None:
        fs01 = principal(sid=ADMINISTRATORS, principal_kind="local_group", host_key="FS01")
        fs02 = principal(sid=ADMINISTRATORS, principal_kind="local_group", host_key="FS02")

        assert fs01.source_key != fs02.source_key

    def test_keys_are_stable_across_spellings_of_the_same_object(self) -> None:
        upper = ServerObservation(**base(source_key="server|fs01", name="FS01"))
        lower = ServerObservation(**base(source_key="server|fs01", name="fs01"))

        assert upper.source_key == lower.source_key

    def test_a_resource_key_is_case_folded(self) -> None:
        observation = NtfsResourceObservation(
            **base(
                source_key="resource|\\\\fs01\\finance",
                path="\\\\FS01\\Finance",
                dacl_present=True,
                ace_count=0,
            )
        )

        assert observation.source_key == "resource|\\\\fs01\\finance"

    def test_an_ace_key_excludes_order_so_reordering_is_not_a_rewrite(self) -> None:
        first = keys.ntfs_ace_key("\\\\FS01\\Finance", Sid(ALICE), "allow", MODIFY, 3)
        same_ace_moved = keys.ntfs_ace_key("\\\\FS01\\Finance", Sid(ALICE), "allow", MODIFY, 3)

        assert first == same_ace_moved


class TestPrincipalObservation:
    def test_a_sid_is_canonicalized(self) -> None:
        observation = principal(sid="s-1-5-21-1004336348-1177238915-682003330-01104")

        assert observation.sid == ALICE

    def test_a_name_is_not_a_sid(self) -> None:
        with pytest.raises(ValidationError, match="not a valid string-form SID"):
            PrincipalObservation(
                **base(sid="CORP\\alice", principal_kind="user", source_key="principal|x")
            )

    def test_a_local_group_must_carry_its_host(self) -> None:
        with pytest.raises(ValidationError, match="identical on every Windows computer"):
            PrincipalObservation(
                **base(
                    sid=ADMINISTRATORS,
                    principal_kind="local_group",
                    source_key=f"principal|{ADMINISTRATORS}",
                )
            )

    def test_an_unresolved_principal_may_not_carry_a_display_name(self) -> None:
        with pytest.raises(ValidationError, match="last_known_name"):
            principal(principal_kind="unresolved", display_name="Alice Smith")

    def test_an_unresolved_principal_keeps_its_previous_name_separately(self) -> None:
        observation = principal(
            principal_kind="unresolved", unresolved_reason="deleted", last_known_name="CORP\\jdoe"
        )
        domain_object = observation.to_domain()

        assert isinstance(domain_object, UnresolvedPrincipal)
        assert domain_object.last_known_name == "CORP\\jdoe"
        assert domain_object.display_name is None

    def test_a_domain_sid_contradicting_the_sid_is_rejected(self) -> None:
        observation = principal(domain_sid="S-1-5-21-9-8-7")

        with pytest.raises(Exception, match="belongs to domain"):
            observation.to_domain()

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("user", User),
            ("domain_group", DomainGroup),
            ("local_group", LocalGroup),
        ],
    )
    def test_conversion_produces_the_matching_domain_type(self, kind: str, expected: type) -> None:
        fields: dict[str, Any] = {}
        sid = ALICE
        if kind == "local_group":
            sid = ADMINISTRATORS
            fields["host_key"] = "FS01"
        observation = principal(sid=sid, principal_kind=kind, **fields)

        assert isinstance(observation.to_domain(), expected)

    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="effective_access"):
            PrincipalObservation(
                **base(
                    sid=ALICE,
                    principal_kind="user",
                    source_key=f"principal|{ALICE}",
                    effective_access="modify",
                )
            )


class TestMembershipObservation:
    def edge(self, **fields: Any) -> MembershipObservation:
        group = fields.pop("group_sid", FINANCE_RW)
        member = fields.pop("member_sid", ALICE)
        kind = fields.pop("edge_kind", "directory_group_member")
        host_key = fields.get("host_key")
        source_key = fields.pop("source_key", None) or keys.membership_key(
            Sid(group), Sid(member), MembershipEdgeKind(kind), host_key
        )
        return MembershipObservation(
            **base(
                group_sid=group,
                member_sid=member,
                edge_kind=kind,
                source_key=source_key,
                **fields,
            )
        )

    def test_a_directory_edge_converts_to_a_domain_edge(self) -> None:
        observation = self.edge(member_kind="user")

        assert isinstance(observation.to_domain(), MembershipEdge)

    def test_a_self_edge_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="member of itself"):
            self.edge(group_sid=FINANCE_RW, member_sid=FINANCE_RW, source_key="edge|x")

    def test_a_local_edge_requires_a_host(self) -> None:
        with pytest.raises(ValidationError, match="host"):
            self.edge(
                group_sid=ADMINISTRATORS,
                member_sid=FINANCE_TEAM,
                edge_kind="local_group_member",
                source_key="edge|x",
            )

    def test_a_directory_edge_may_not_carry_a_host(self) -> None:
        with pytest.raises(ValidationError, match="local-group edges"):
            self.edge(host_key="FS01", source_key="edge|x")

    def test_a_cycle_is_representable(self) -> None:
        forward = self.edge(group_sid=FINANCE_RW, member_sid=FINANCE_TEAM)
        backward = self.edge(group_sid=FINANCE_TEAM, member_sid=FINANCE_RW)

        assert forward.source_key != backward.source_key

    def test_primary_group_membership_is_representable(self) -> None:
        observation = self.edge(
            group_sid=f"{DOMAIN}-513", member_sid=ALICE, edge_kind="primary_group"
        )

        assert observation.source_key.endswith("|primary_group")


class TestServerAndShareObservations:
    def test_a_server_name_may_not_be_a_unc_path(self) -> None:
        with pytest.raises(ValidationError, match="path separators"):
            ServerObservation(**base(source_key="server|fs01", name="\\\\FS01"))

    def test_a_server_converts_to_the_domain_type(self) -> None:
        observation = ServerObservation(
            **base(
                source_key="server|fs01",
                name="FS01",
                dns_host_name="fs01.corp.example.com",
                is_domain_member=True,
            )
        )

        assert isinstance(observation.to_domain(), Server)

    def test_a_share_is_keyed_by_server_and_name(self) -> None:
        observation = SmbShareObservation(
            **base(
                source_key="share|fs01|finance",
                server_name="FS01",
                share_name="Finance",
                local_path="D:\\Shares\\Finance",
            )
        )
        share = observation.to_domain()

        assert isinstance(share, SmbShare)
        assert share.identity_key == "fs01|finance"

    def test_an_invalid_local_path_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SmbShareObservation(
                **base(
                    source_key="share|fs01|finance",
                    server_name="FS01",
                    share_name="Finance",
                    local_path="Shares\\Finance",
                )
            )


class TestSmbAceObservation:
    def ace(self, **fields: Any) -> SmbAceObservation:
        trustee = fields.pop("trustee_sid", "S-1-1-0")
        ace_type = fields.pop("ace_type", "allow")
        permission = fields.pop("permission", None)
        access_mask = fields.pop("access_mask", None)
        source_key = fields.pop("source_key", None) or keys.smb_ace_key(
            "FS01", "Finance", Sid(trustee), ace_type, access_mask, permission
        )
        return SmbAceObservation(
            **base(
                server_name="FS01",
                share_name="Finance",
                trustee_sid=trustee,
                ace_type=ace_type,
                permission=permission,
                access_mask=access_mask,
                source_key=source_key,
                **fields,
            )
        )

    def test_a_permission_level_is_accepted(self) -> None:
        observation = self.ace(permission="full")

        assert isinstance(observation.to_domain(), SmbShareAce)

    def test_an_access_mask_is_accepted(self) -> None:
        observation = self.ace(access_mask=READ_EXECUTE)

        assert observation.to_domain().access_mask == READ_EXECUTE

    def test_neither_right_form_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            self.ace(source_key="smb_ace|x")

    def test_both_right_forms_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            self.ace(permission="full", access_mask=2032127, source_key="smb_ace|x")

    def test_the_key_records_which_form_was_reported(self) -> None:
        by_level = self.ace(permission="read")
        by_mask = self.ace(access_mask=READ_EXECUTE)

        assert by_level.source_key.endswith("|read")
        assert by_mask.source_key.endswith("|0x001200a9")


class TestNtfsObservations:
    def resource(self, **fields: Any) -> NtfsResourceObservation:
        path = fields.pop("path", "\\\\FS01\\Finance")
        source_key = fields.pop("source_key", None) or keys.ntfs_resource_key(path)
        fields.setdefault("dacl_present", True)
        fields.setdefault("ace_count", 1)
        return NtfsResourceObservation(**base(path=path, source_key=source_key, **fields))

    def ace(self, **fields: Any) -> NtfsAceObservation:
        path = fields.pop("path", "\\\\FS01\\Finance")
        trustee = fields.pop("trustee_sid", FINANCE_RW)
        ace_type = fields.pop("ace_type", "allow")
        mask = fields.pop("access_mask", MODIFY)
        flags = fields.pop("ace_flags", 3)
        source = fields.pop("source", "explicit")
        source_key = fields.pop("source_key", None) or keys.ntfs_ace_key(
            path, Sid(trustee), ace_type, mask, flags
        )
        return NtfsAceObservation(
            **base(
                path=path,
                trustee_sid=trustee,
                ace_type=ace_type,
                access_mask=mask,
                ace_flags=flags,
                source=source,
                source_key=source_key,
                **fields,
            )
        )

    def test_a_resource_converts_to_a_directory_and_descriptor_facts(self) -> None:
        observation = self.resource(owner_sid=ADMINISTRATORS, dacl_protected=True)

        assert isinstance(observation.to_domain(), DirectoryResource)
        assert observation.to_descriptor_facts().dacl_protected is True

    def test_a_local_path_alone_cannot_identify_a_resource(self) -> None:
        with pytest.raises(ValidationError, match="ambiguous"):
            self.resource(path="D:\\Shares\\Finance", source_key="resource|x")

    def test_a_null_dacl_may_not_carry_aces(self) -> None:
        with pytest.raises(ValidationError, match="NULL DACL"):
            self.resource(dacl_present=False, ace_count=2)

    def test_a_null_dacl_is_distinguishable_from_an_empty_one(self) -> None:
        null_dacl = self.resource(dacl_present=False, ace_count=0)
        empty_dacl = self.resource(dacl_present=True, ace_count=0)

        assert null_dacl.to_descriptor_facts().grants_everyone_full_access is True
        assert empty_dacl.to_descriptor_facts().denies_everyone is True

    def test_blocked_inheritance_must_be_marked_as_a_boundary(self) -> None:
        with pytest.raises(ValidationError, match="ACL boundary"):
            self.resource(inheritance_enabled=False, is_acl_boundary=False)

    def test_an_ace_converts_with_its_raw_mask_intact(self) -> None:
        observation = self.ace(access_mask=0x10000000)
        ace = observation.to_domain()

        assert isinstance(ace, NtfsAce)
        assert ace.access_mask == 0x10000000
        assert ace.uses_generic_rights is True
        # Not expanded: the Phase 4 engine applies the generic mapping.
        assert NtfsRight.READ_DATA not in ace.rights

    def test_flags_convert_to_domain_flags(self) -> None:
        observation = self.ace(
            ace_flags=0x13, source="inherited", inherited_from="\\\\FS01\\Finance"
        )
        ace = observation.to_domain()

        assert AceFlag.INHERITED in ace.flags
        assert ace.is_inherited is True

    def test_source_must_agree_with_the_inherited_flag(self) -> None:
        with pytest.raises(ValidationError, match="INHERITED_ACE bit"):
            self.ace(ace_flags=0x13, source="explicit")

    def test_an_inherited_ace_missing_the_flag_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="INHERITED_ACE bit"):
            self.ace(ace_flags=0x03, source="inherited")

    def test_an_explicit_ace_may_not_record_an_origin(self) -> None:
        with pytest.raises(ValidationError, match="inheritance origin"):
            self.ace(inherited_from="\\\\FS01\\Finance")

    def test_a_deny_ace_is_recorded_without_interpretation(self) -> None:
        observation = self.ace(ace_type="deny")

        assert observation.to_domain().ace_type.value == "deny"

    @pytest.mark.parametrize("mask", [-1, 2**32])
    def test_a_mask_outside_32_bits_is_rejected(self, mask: int) -> None:
        with pytest.raises(ValidationError):
            self.ace(access_mask=mask, source_key="ntfs_ace|x")

    def test_an_unresolved_trustee_is_accepted(self) -> None:
        orphan = "S-1-5-21-999888777-666555444-333222111-1234"
        observation = self.ace(trustee_sid=orphan)

        assert observation.to_domain().trustee_sid == Sid(orphan)


class TestTimestamps:
    def test_a_naive_timestamp_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ServerObservation.model_validate(
                {
                    "run_id": RUN_ID,
                    "observed_at": "2026-09-14T08:00:00",
                    "source_key": "server|fs01",
                    "name": "FS01",
                }
            )

    def test_an_offset_timestamp_is_normalized_to_utc(self) -> None:
        observation = ServerObservation.model_validate(
            {
                "run_id": RUN_ID,
                "observed_at": "2026-09-14T04:00:00-04:00",
                "source_key": "server|fs01",
                "name": "FS01",
            }
        )

        assert observation.observed_at == dt.datetime(2026, 9, 14, 8, 0, tzinfo=dt.UTC)


class TestRunIdentifiers:
    def test_a_missing_run_id_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="run_id"):
            ServerObservation.model_validate(
                {"observed_at": OBSERVED_AT, "source_key": "server|fs01", "name": "FS01"}
            )

    def test_a_non_uuid_run_id_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be a UUID"):
            ServerObservation.model_validate(
                {
                    "run_id": "run-7",
                    "observed_at": OBSERVED_AT,
                    "source_key": "server|fs01",
                    "name": "FS01",
                }
            )

    def test_an_unsupported_schema_version_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="contract v1"):
            ServerObservation.model_validate(
                {
                    "schema_version": "2.0",
                    "run_id": RUN_ID,
                    "observed_at": OBSERVED_AT,
                    "source_key": "server|fs01",
                    "name": "FS01",
                }
            )
