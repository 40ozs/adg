"""ADG-DEMO-02: an ACL trustee nobody could identify is not ruled out of anything.

The coverage check asks, for every trustee an answer did not match, whether the subject
might be in it without ADG knowing. A group might. So might any SID whose kind nothing
established — and a stored record of kind ``unresolved`` establishes nothing about the kind:
it records that no authority could say what the SID is. Before this, such a record was
treated as "not a group", the subject was ruled out of it on no evidence, and an answer
over an ACL naming an orphaned SID could read ``certain``. The canonical demo's Apollo
folder names one (a former employee's account), which is how this was found.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.domain import PrincipalKind
from app.repositories import PrincipalRecord
from app.services.access import _could_contain_members

SID = "S-1-5-21-1004336348-1177238915-682003330-1108"
NOW = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
RUN = uuid.UUID(int=1)


def record(kind: PrincipalKind) -> PrincipalRecord:
    return PrincipalRecord(
        principal_key=SID,
        sid=SID,
        principal_kind=kind,
        host_key=None,
        domain_sid=None,
        display_name=None,
        sam_account_name=None,
        user_principal_name=None,
        distinguished_name=None,
        group_scope=None,
        group_type=None,
        enabled=None,
        is_deleted=False,
        unresolved_reason="deleted" if kind is PrincipalKind.UNRESOLVED else None,
        last_known_name=None,
        first_observed_at=NOW,
        first_observed_run_id=RUN,
        last_observed_at=NOW,
        last_observed_run_id=RUN,
    )


def test_no_record_at_all_could_be_a_group() -> None:
    assert _could_contain_members(None) is True


def test_a_record_saying_nothing_resolved_the_sid_could_be_a_group() -> None:
    assert _could_contain_members(record(PrincipalKind.UNRESOLVED)) is True


@pytest.mark.parametrize("kind", [PrincipalKind.DOMAIN_GROUP, PrincipalKind.LOCAL_GROUP])
def test_a_described_group_could_contain_the_subject(kind: PrincipalKind) -> None:
    assert _could_contain_members(record(kind)) is True


@pytest.mark.parametrize(
    "kind",
    [
        kind
        for kind in PrincipalKind
        if kind
        not in (PrincipalKind.DOMAIN_GROUP, PrincipalKind.LOCAL_GROUP, PrincipalKind.UNRESOLVED)
    ],
)
def test_a_principal_described_as_anything_else_contains_nobody(kind: PrincipalKind) -> None:
    assert _could_contain_members(record(kind)) is False
