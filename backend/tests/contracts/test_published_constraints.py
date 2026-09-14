"""The models must enforce the string constraints the published schemas advertise.

`test_schema_model_parity.py` compares fields, required-ness, and enumerations. It cannot
compare lengths and patterns usefully, because the models enforce most of those in
validators rather than in `Field(...)` — `canonical_sid`, `parse_unc_path`, `normalize_host`
— so a JSON-level diff is all false positives.

What matters is behavior, so these tests feed the models values the published schema
forbids and require a rejection. The gap they were written for was real: `hostName` in
`common.schema.json` has declared `maxLength: 255` and forbidden control characters since
Phase 0B, and `normalize_host` checked neither, so `host_key` — which becomes half of the
storage key of every local group — was unbounded and could contain a newline.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts.v1 import keys
from app.contracts.v1.common import MAX_HOST_NAME_LENGTH, normalize_host
from app.contracts.v1.observations import (
    MembershipObservation,
    NtfsResourceObservation,
    PrincipalObservation,
    ServerObservation,
    SmbShareObservation,
)
from app.domain import MembershipEdgeKind, PrincipalKind, Sid
from tests.contracts.test_json_schemas import SCHEMA_DIR, validator_for

RUN_ID = "00000000-0000-4000-8000-00000000c001"
OBSERVED_AT = "2026-09-14T09:00:00Z"
BUILTIN = "S-1-5-32-544"
USER_SID = "S-1-5-21-2000000001-2000000002-2000000003-1104"

# Control characters are placed *inside* the name on purpose. Python's `str.strip()` treats
# U+001C-U+001F as whitespace, so a trailing one is removed before any check sees it — which
# is harmless, because the stored key is then clean, but a test of the check that used a
# trailing one would pass for the wrong reason.
REJECTED_HOSTS = [
    pytest.param("a" * (MAX_HOST_NAME_LENGTH + 1), id="too-long"),
    pytest.param("fs01\nevil", id="newline"),
    pytest.param("fs01\x00", id="null"),
    pytest.param("fs\x1f01", id="unit-separator"),
    pytest.param("fs\x0701", id="bell"),
    pytest.param("\\\\fs01", id="unc-path"),
    pytest.param("fs01/share", id="forward-slash"),
]


def published_host_name() -> dict[str, Any]:
    common = json.loads((SCHEMA_DIR / "common.schema.json").read_text(encoding="utf-8"))
    definition: dict[str, Any] = common["$defs"]["hostName"]
    return definition


class TestTheHostNameContractIsStated:
    def test_the_published_definition_still_bounds_length_and_characters(self) -> None:
        # If the published contract relaxes, these tests are asserting the wrong thing.
        definition = published_host_name()

        assert definition["maxLength"] == MAX_HOST_NAME_LENGTH
        assert definition["minLength"] == 1
        assert "\\u0000-\\u001f" in definition["pattern"]


@pytest.mark.parametrize("host", REJECTED_HOSTS)
class TestHostNamesTheContractForbids:
    def test_the_published_schema_rejects_it(self, host: str) -> None:
        # Both sides must agree, or a collector is correct by one and wrong by the other.
        payload = {
            "schema_version": "1.0",
            "kind": "principal",
            "run_id": RUN_ID,
            "observed_at": OBSERVED_AT,
            "source_key": f"principal|{host.casefold()}|{BUILTIN}",
            "sid": BUILTIN,
            "principal_kind": "local_group",
            "host_key": host,
        }

        assert not validator_for("principal-observation.schema.json").is_valid(payload)

    def test_normalize_host_rejects_it(self, host: str) -> None:
        with pytest.raises(ValueError):
            normalize_host(host)

    def test_a_local_group_observation_rejects_it(self, host: str) -> None:
        with pytest.raises(ValidationError):
            PrincipalObservation(
                run_id=RUN_ID,
                observed_at=OBSERVED_AT,  # type: ignore[arg-type]
                source_key=f"principal|{host.casefold()}|{BUILTIN}",
                sid=BUILTIN,
                principal_kind=PrincipalKind.LOCAL_GROUP,
                host_key=host,
            )

    def test_a_local_group_edge_rejects_it(self, host: str) -> None:
        with pytest.raises(ValidationError):
            MembershipObservation(
                run_id=RUN_ID,
                observed_at=OBSERVED_AT,  # type: ignore[arg-type]
                source_key="edge|placeholder",
                group_sid=BUILTIN,
                member_sid=USER_SID,
                edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
                host_key=host,
            )

    def test_a_server_observation_rejects_it(self, host: str) -> None:
        with pytest.raises(ValidationError):
            ServerObservation(
                run_id=RUN_ID,
                observed_at=OBSERVED_AT,  # type: ignore[arg-type]
                source_key=f"server|{host.casefold()}",
                name=host,
            )

    def test_a_share_observation_rejects_it_as_a_server_name(self, host: str) -> None:
        with pytest.raises(ValidationError):
            SmbShareObservation(
                run_id=RUN_ID,
                observed_at=OBSERVED_AT,  # type: ignore[arg-type]
                source_key=f"share|{host.casefold()}|finance",
                server_name=host,
                share_name="Finance",
            )


class TestHostNamesTheContractAllows:
    @pytest.mark.parametrize(
        "host",
        ["FS01", "fs01.corp.example.com", "a" * MAX_HOST_NAME_LENGTH, "host-with-dash", "wks_01"],
    )
    def test_a_legal_host_is_accepted_and_only_trimmed(self, host: str) -> None:
        observation = PrincipalObservation(
            run_id=RUN_ID,
            observed_at=OBSERVED_AT,  # type: ignore[arg-type]
            source_key=keys.principal_key(Sid(BUILTIN), PrincipalKind.LOCAL_GROUP, host),
            sid=BUILTIN,
            principal_kind=PrincipalKind.LOCAL_GROUP,
            host_key=host,
        )

        assert observation.host_key == host
        assert observation.to_domain().identity_key == f"{host.casefold()}|{BUILTIN}"

    def test_surrounding_whitespace_is_still_stripped(self) -> None:
        assert normalize_host("  FS01  ") == "FS01"

    def test_a_whitespace_only_host_is_rejected_although_the_pattern_would_allow_it(
        self,
    ) -> None:
        # Deliberately stricter than the published pattern, which forbids only control
        # characters and path separators. A host name of three spaces names no computer,
        # and it would become a storage key of three spaces.
        with pytest.raises(ValueError):
            normalize_host("   ")


class TestTheDiagnosticNamesTheField:
    """A rejection has to say which field is wrong, or a collector author cannot act on it.

    Before the fix an over-long host was caught downstream as an oversized ``source_key``,
    which is a true statement about the wrong field.
    """

    def test_an_over_long_host_says_so(self) -> None:
        with pytest.raises(ValidationError) as failure:
            PrincipalObservation(
                run_id=RUN_ID,
                observed_at=OBSERVED_AT,  # type: ignore[arg-type]
                source_key=f"principal|{'a' * 300}|{BUILTIN}",
                sid=BUILTIN,
                principal_kind=PrincipalKind.LOCAL_GROUP,
                host_key="a" * 300,
            )

        message = str(failure.value)
        assert "host name" in message.lower()
        assert str(MAX_HOST_NAME_LENGTH) in message
        assert "host_key" in message

    def test_a_control_character_says_so(self) -> None:
        with pytest.raises(ValidationError) as failure:
            ServerObservation(
                run_id=RUN_ID,
                observed_at=OBSERVED_AT,  # type: ignore[arg-type]
                source_key="server|fs01",
                name="fs\n01",
            )

        assert "control character" in str(failure.value).lower()


class TestKeysStayInsideTheColumn:
    """Every storage key derived from a legal payload must fit the columns that hold it.

    ``principals.principal_key`` and ``membership_edges.edge_key`` are ``varchar(512)``.
    PostgreSQL raises rather than truncating, so an oversized key would be a 500 on ingest
    instead of a diagnostic — which is why the bound belongs at the contract boundary.
    """

    def test_the_longest_legal_principal_key_fits(self) -> None:
        from app.models.schema import KEY_LENGTH

        host = "a" * MAX_HOST_NAME_LENGTH
        key = keys.principal_key(Sid(BUILTIN), PrincipalKind.LOCAL_GROUP, host)

        assert len(key) <= 512, "source_key is capped at 512 by the contract"
        assert len(key) - len("principal|") <= KEY_LENGTH

    def test_an_edge_key_from_two_maximal_hosts_is_refused_at_the_boundary(self) -> None:
        # The edge key concatenates both endpoints, so two maximal host names overflow the
        # 512-character source_key. That is the contract's own bound doing its job: the
        # payload is rejected rather than stored as a truncated identity.
        host = "a" * MAX_HOST_NAME_LENGTH
        derived = keys.membership_key(
            Sid(BUILTIN), Sid("S-1-5-32-545"), MembershipEdgeKind.LOCAL_GROUP_MEMBER, host
        )

        assert len(derived) > 512
        with pytest.raises(ValidationError):
            MembershipObservation(
                run_id=RUN_ID,
                observed_at=OBSERVED_AT,  # type: ignore[arg-type]
                source_key=derived,
                group_sid=BUILTIN,
                member_sid="S-1-5-32-545",
                edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER,
                host_key=host,
            )

    def test_an_ordinary_local_group_edge_key_is_comfortably_inside_the_column(self) -> None:
        from app.models.schema import KEY_LENGTH

        derived = keys.membership_key(
            Sid(BUILTIN), Sid(USER_SID), MembershipEdgeKind.LOCAL_GROUP_MEMBER, "fs01"
        )

        assert len(derived) - len("edge|") < KEY_LENGTH


class TestPathConstraintsAreEnforcedToo:
    """A spot check that the same discipline holds for the other key-bearing field."""

    @pytest.mark.parametrize(
        "path", ["\\\\fs01", "D:\\Shares\\Finance", "\\\\fs01\\finance\\..\\other", "relative"]
    )
    def test_a_path_the_schema_forbids_is_rejected_by_the_model(self, path: str) -> None:
        with pytest.raises(ValidationError):
            NtfsResourceObservation(
                run_id=RUN_ID,
                observed_at=OBSERVED_AT,  # type: ignore[arg-type]
                source_key="resource|placeholder",
                path=path,
                dacl_present=True,
                ace_count=0,
            )
