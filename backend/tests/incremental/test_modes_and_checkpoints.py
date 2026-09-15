"""The rules that decide what a delta may skip and what a cursor may claim.

Everything here is pure, and all of it is refusals. A checkpoint is the only piece of state
in ADG whose failure mode is silent: it acts on what the *next* run does not read, and a run
that skipped an object reports success, sends no error, and leaves nothing missing to
notice. So each refusal is tested with the sentence saying what accepting it would have
cost.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain import (
    Checkpoint,
    CheckpointKind,
    CollectionMode,
    DomainValidationError,
    ReconciliationDrift,
)

MONDAY = dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.UTC)
TUESDAY = dt.datetime(2026, 3, 3, 9, 0, tzinfo=dt.UTC)

DC01 = "CN=NTDS Settings,CN=DC01|2f0f9a3c-7c4e-4c0e-9a02-6b5f0a1f9d11"
DC02 = "CN=NTDS Settings,CN=DC02|8a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d"
#: The same domain controller after a restore from backup: same name, new invocation id.
DC01_RESTORED = "CN=NTDS Settings,CN=DC01|c0ffee00-1111-4222-8333-444455556666"


def usn(token: str, issuer: str = DC01, at: dt.datetime = MONDAY) -> Checkpoint:
    return Checkpoint(kind=CheckpointKind.USN, token=token, issuer=issuer, issued_at=at)


class TestModeAndIncrementalCannotDisagree:
    @pytest.mark.parametrize(
        ("mode", "incremental"),
        [
            (CollectionMode.FULL, False),
            (CollectionMode.DELTA, True),
            (CollectionMode.RECONCILE, False),
        ],
    )
    def test_is_incremental_is_exactly_the_delta_mode(
        self, mode: CollectionMode, incremental: bool
    ) -> None:
        assert mode.is_incremental is incremental

    def test_only_a_delta_is_forbidden_to_reconcile(self) -> None:
        assert not CollectionMode.DELTA.may_reconcile
        assert CollectionMode.FULL.may_reconcile
        assert CollectionMode.RECONCILE.may_reconcile

    def test_reconcile_reads_the_whole_scope_just_as_full_does(self) -> None:
        # The two differ in why they ran, not in what they read. An operator reading a
        # non-zero drift number needs to tell a scheduled repair from an ordinary scan;
        # nothing else in the system treats them differently.
        assert CollectionMode.RECONCILE.reads_whole_scope
        assert CollectionMode.FULL.reads_whole_scope
        assert not CollectionMode.DELTA.reads_whole_scope


class TestACheckpointMustBeComparable:
    def test_a_usn_token_that_is_not_a_number_is_refused(self) -> None:
        with pytest.raises(DomainValidationError) as raised:
            usn("18-4213")
        assert "counter" in str(raised.value)

    def test_a_timestamp_token_must_carry_an_offset(self) -> None:
        with pytest.raises(DomainValidationError):
            Checkpoint(
                kind=CheckpointKind.TIMESTAMP,
                token="2026-03-02T09:00:00",
                issuer=DC01,
                issued_at=MONDAY,
            )

    def test_an_opaque_token_may_be_anything(self) -> None:
        cursor = Checkpoint(
            kind=CheckpointKind.OPAQUE, token="pass=2;index=17", issuer="FS01", issued_at=MONDAY
        )
        assert cursor.ordering_value is None
        assert not cursor.kind.is_ordered

    def test_an_empty_issuer_is_refused(self) -> None:
        # A cursor with no issuer looks usable everywhere, which is the one thing a
        # per-server counter must never look like.
        with pytest.raises(DomainValidationError) as raised:
            usn("4711", issuer="   ")
        assert "issuer" in str(raised.value)

    def test_a_naive_issued_at_is_refused(self) -> None:
        with pytest.raises(DomainValidationError):
            Checkpoint(
                kind=CheckpointKind.USN,
                token="1",
                issuer=DC01,
                issued_at=dt.datetime(2026, 3, 2, 9, 0),
            )

    def test_an_oversized_token_is_refused(self) -> None:
        with pytest.raises(DomainValidationError) as raised:
            Checkpoint(
                kind=CheckpointKind.OPAQUE,
                token="x" * 513,
                issuer="FS01",
                issued_at=MONDAY,
            )
        assert "resume point" in str(raised.value)


class TestACursorOnlyMovesForwardWithinOneIssuer:
    def test_the_first_checkpoint_is_always_accepted(self) -> None:
        assert usn("4711").advances_over(None) is None

    def test_a_higher_usn_from_the_same_issuer_advances(self) -> None:
        assert usn("4712", at=TUESDAY).advances_over(usn("4711")) is None

    def test_an_equal_usn_advances(self) -> None:
        # Re-recording the same cursor is what a completion does after its last batch
        # already recorded it. Refusing that would report a healthy job as blocked.
        assert usn("4711", at=TUESDAY).advances_over(usn("4711")) is None

    def test_a_lower_usn_from_the_same_issuer_is_refused(self) -> None:
        rejection = usn("4700", at=TUESDAY).advances_over(usn("4711"))
        assert rejection is not None
        assert rejection.code == "checkpoint_went_backwards"

    def test_a_different_domain_controller_is_refused(self) -> None:
        # The heart of it: DC2's counter is unrelated to DC1's, and accepting the new number
        # would make the next delta skip every object whose USN on DC2 falls below it.
        rejection = usn("9", issuer=DC02, at=TUESDAY).advances_over(usn("4711", issuer=DC01))
        assert rejection is not None
        assert rejection.code == "checkpoint_issuer_changed"
        assert "local to its issuer" in rejection.message

    def test_the_same_controller_restored_from_backup_is_refused(self) -> None:
        # Same dsServiceName, new invocationId. A restore rolls the USN counter backwards
        # and the DC reissues numbers it has already handed out, so a watermark compared on
        # the server name alone would survive the restore and skip every reused number.
        rejection = usn("9000", issuer=DC01_RESTORED, at=TUESDAY).advances_over(usn("4711"))
        assert rejection is not None
        assert rejection.code == "checkpoint_issuer_changed"

    def test_the_issuer_comparison_ignores_case(self) -> None:
        assert usn("4712", issuer=DC01.upper(), at=TUESDAY).advances_over(usn("4711")) is None

    def test_changing_the_kind_is_refused(self) -> None:
        timestamp = Checkpoint(
            kind=CheckpointKind.TIMESTAMP,
            token="2026-03-03T09:00:00Z",
            issuer=DC01,
            issued_at=TUESDAY,
        )
        rejection = timestamp.advances_over(usn("4711"))
        assert rejection is not None
        assert rejection.code == "checkpoint_kind_changed"

    def test_an_opaque_cursor_replaces_an_opaque_cursor(self) -> None:
        # There is no ordering to check, so refusing would leave such a job permanently
        # unable to record progress. What is lost is the backwards check, and the docstring
        # on CheckpointKind.OPAQUE says so rather than leaving it to be discovered.
        previous = Checkpoint(
            kind=CheckpointKind.OPAQUE, token="a", issuer="FS01", issued_at=MONDAY
        )
        later = Checkpoint(kind=CheckpointKind.OPAQUE, token="b", issuer="FS01", issued_at=TUESDAY)
        assert later.advances_over(previous) is None


class TestDriftCountsOnlyWhatDeltasCannotSee:
    def test_absences_and_revivals_are_drift(self) -> None:
        drift = ReconciliationDrift(
            scope_kind="directory_tree",
            scope_key="\\\\fs01\\finance",
            marked_absent=3,
            revived=1,
        )
        assert drift.drift == 4
        assert not drift.is_clean

    def test_ordinary_change_is_not_drift(self) -> None:
        # A superseded state may well have been caught by a delta, or would have been on the
        # next pass. Counting it would make ordinary churn look like the cadence failing.
        drift = ReconciliationDrift(
            scope_kind="domain",
            scope_key="corp.example.com",
            confirmed=900,
            superseded=40,
            delta_runs_since=12,
        )
        assert drift.drift == 0
        assert drift.is_clean

    def test_a_clean_summary_names_how_many_deltas_it_vindicates(self) -> None:
        drift = ReconciliationDrift(
            scope_kind="domain", scope_key="corp.example.com", delta_runs_since=12
        )
        assert "12 incremental run(s)" in drift.summary()

    def test_a_dirty_summary_says_the_cadence_is_working_as_designed(self) -> None:
        drift = ReconciliationDrift(
            scope_kind="domain", scope_key="corp.example.com", marked_absent=2
        )
        summary = drift.summary()
        assert "2 object(s) no longer present" in summary
        assert "working as designed" in summary

    def test_a_negative_count_is_refused(self) -> None:
        with pytest.raises(DomainValidationError):
            ReconciliationDrift(scope_kind="domain", scope_key="x", marked_absent=-1)
