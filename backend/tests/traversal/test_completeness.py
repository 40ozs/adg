"""What an answer's outcome is, and why: the classification and the graph's readiness."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.traversal.completeness import (
    REASON_CATEGORY,
    REASON_DESCRIPTIONS,
    CompletenessReason,
    GraphReadiness,
    PathOutcome,
    ReasonCategory,
    classify,
    conclusive,
    ordered,
)

R = CompletenessReason
RUN_A, RUN_B = uuid.uuid4(), uuid.uuid4()


def readiness(**changes: object) -> GraphReadiness:
    values: dict[str, object] = {
        "nothing_collected": False,
        "passes": 3,
        "latest_pass": {
            "pass_id": uuid.UUID(int=7),
            "status": "succeeded",
            "completed_at": dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
        },
        "underived_runs": (),
        "failed_runs": (),
        "runs_in_progress": (),
    }
    values.update(changes)
    return GraphReadiness(**values)  # type: ignore[arg-type]


class TestTheVocabulary:
    def test_every_reason_has_a_category_and_a_sentence(self) -> None:
        assert set(REASON_CATEGORY) == set(CompletenessReason)
        assert set(REASON_DESCRIPTIONS) == set(CompletenessReason)
        for reason in CompletenessReason:
            assert REASON_DESCRIPTIONS[reason].endswith((".", ")"))

    def test_reasons_come_out_in_one_fixed_order(self) -> None:
        assert ordered([R.EXCLUDED_BY_POLICY, R.MAX_DEPTH, R.PROJECTION_BEHIND]) == (
            R.PROJECTION_BEHIND,
            R.MAX_DEPTH,
            R.EXCLUDED_BY_POLICY,
        )


class TestTheOutcome:
    """The lane's eight completeness cases must not collapse into one "no path"."""

    @pytest.mark.parametrize(
        ("reasons", "outcome"),
        [
            ((), PathOutcome.NO_PATH),
            ((R.PROJECTION_BEHIND,), PathOutcome.INCOMPLETE_EVIDENCE),
            ((R.PROJECTION_FAILED,), PathOutcome.INCOMPLETE_EVIDENCE),
            ((R.NOTHING_COLLECTED,), PathOutcome.INCOMPLETE_EVIDENCE),
            ((R.MAX_DEPTH,), PathOutcome.TRUNCATED),
            ((R.MAX_PATHS,), PathOutcome.TRUNCATED),
            ((R.MAX_NODES,), PathOutcome.TRUNCATED),
            ((R.UNKNOWN_RELATIONSHIPS,), PathOutcome.BOUNDARY),
            ((R.UNRESOLVED_BOUNDARY,), PathOutcome.BOUNDARY),
            ((R.IMPLICIT_MEMBERSHIP_BOUNDARY,), PathOutcome.BOUNDARY),
            ((R.EXCLUDED_BY_POLICY,), PathOutcome.POLICY_RESTRICTED),
            ((R.COLLECTION_IN_PROGRESS,), PathOutcome.NO_PATH),
        ],
    )
    def test_an_empty_answer_names_its_emptiness(
        self, reasons: tuple[CompletenessReason, ...], outcome: PathOutcome
    ) -> None:
        assert classify(False, reasons) is outcome

    def test_the_most_fundamental_reason_wins(self) -> None:
        everything = list(CompletenessReason)
        assert classify(False, everything) is PathOutcome.INCOMPLETE_EVIDENCE
        assert classify(False, [R.EXCLUDED_BY_POLICY, R.UNRESOLVED_BOUNDARY, R.MAX_DEPTH]) is (
            PathOutcome.TRUNCATED
        )
        assert classify(False, [R.EXCLUDED_BY_POLICY, R.UNKNOWN_RELATIONSHIPS]) is (
            PathOutcome.BOUNDARY
        )

    def test_a_found_path_is_found_whatever_else_is_true(self) -> None:
        assert classify(True, list(CompletenessReason)) is PathOutcome.FOUND

    def test_only_no_path_and_a_found_path_over_a_current_graph_are_conclusive(self) -> None:
        assert conclusive(PathOutcome.NO_PATH, ())
        assert conclusive(PathOutcome.FOUND, (R.MAX_DEPTH, R.UNRESOLVED_BOUNDARY))
        assert not conclusive(PathOutcome.FOUND, (R.PROJECTION_BEHIND,))
        for outcome in set(PathOutcome) - {PathOutcome.FOUND, PathOutcome.NO_PATH}:
            assert not conclusive(outcome, ())

    def test_a_notice_never_weakens_an_answer(self) -> None:
        assert REASON_CATEGORY[R.COLLECTION_IN_PROGRESS] is ReasonCategory.NOTICE
        assert conclusive(PathOutcome.FOUND, (R.COLLECTION_IN_PROGRESS,))


class TestReadiness:
    def test_a_fully_derived_graph_is_current(self) -> None:
        state = readiness()
        assert state.state == "current"
        assert state.reasons == ()

    def test_a_completed_run_never_derived_is_behind(self) -> None:
        state = readiness(underived_runs=(RUN_A,))
        assert state.state == "behind"
        assert state.reasons == (R.PROJECTION_BEHIND,)

    def test_a_failed_derivation_is_named_apart_from_one_never_attempted(self) -> None:
        failed = readiness(underived_runs=(RUN_A,), failed_runs=(RUN_A,))
        both = readiness(underived_runs=(RUN_A, RUN_B), failed_runs=(RUN_A,))
        assert failed.state == "failed"
        assert failed.reasons == (R.PROJECTION_FAILED,)
        assert both.reasons == (R.PROJECTION_FAILED, R.PROJECTION_BEHIND)

    def test_nothing_collected(self) -> None:
        state = readiness(nothing_collected=True, passes=0, latest_pass=None)
        assert state.state == "empty"
        assert state.reasons == (R.NOTHING_COLLECTED,)

    def test_a_run_still_collecting_is_a_notice(self) -> None:
        state = readiness(runs_in_progress=(RUN_B,))
        assert state.state == "current"
        assert state.reasons == (R.COLLECTION_IN_PROGRESS,)

    def test_the_token_moves_with_every_pass_and_only_then(self) -> None:
        before = readiness()
        assert readiness().token == before.token
        assert readiness(passes=4).token != before.token
        assert (
            readiness(
                latest_pass={
                    "pass_id": uuid.UUID(int=8),
                    "status": "succeeded",
                    "completed_at": dt.datetime(2026, 10, 2, tzinfo=dt.UTC),
                }
            ).token
            != before.token
        )
