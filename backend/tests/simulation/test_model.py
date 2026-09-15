"""The baseline, the scope, the bounds, and the shape of what a simulation reports."""

from __future__ import annotations

import datetime as dt

import pytest

from app.access_engine import AccessPath
from app.domain import EMPTY_BASIS, CollectionBasis, DomainValidationError
from app.simulation import (
    CAVEAT_DESCRIPTIONS,
    MAX_PAIRS_CEILING,
    MAX_PRINCIPALS_CEILING,
    OUTCOME_DESCRIPTIONS,
    TRUNCATION_DESCRIPTIONS,
    BaselineKind,
    ChangeApplication,
    ChangeKind,
    ChangeOutcome,
    ImpactDirection,
    MembershipChange,
    ScopeKind,
    SimulationBaseline,
    SimulationBounds,
    SimulationBoundsError,
    SimulationCaveat,
    SimulationScope,
    SimulationTruncation,
    summarize,
)
from app.simulation.model import ordered_truncation
from tests.support.simulation import ALICE, FINANCE, FINANCE_TEAM, RUN_ID, fixed_baseline

NOW = dt.datetime(2026, 9, 14, 12, 0, tzinfo=dt.UTC)


class TestTheBaseline:
    def test_an_as_of_baseline_must_carry_its_instant(self):
        with pytest.raises(DomainValidationError, match="must agree"):
            SimulationBaseline(kind=BaselineKind.AS_OF, basis=EMPTY_BASIS, captured_at=NOW, at=None)

    def test_a_current_baseline_must_not(self):
        with pytest.raises(DomainValidationError, match="must agree"):
            SimulationBaseline(
                kind=BaselineKind.CURRENT, basis=EMPTY_BASIS, captured_at=NOW, at=NOW
            )

    def test_a_naive_instant_is_refused(self):
        """It cannot be ordered against observations from a host in another time zone."""
        with pytest.raises(DomainValidationError, match="timezone-aware"):
            SimulationBaseline(
                kind=BaselineKind.AS_OF,
                basis=EMPTY_BASIS,
                captured_at=NOW,
                at=dt.datetime(2026, 9, 1, 9, 0),
            )

    def test_the_token_and_run_id_come_from_the_basis(self):
        baseline = fixed_baseline()

        assert baseline.token == baseline.basis.token
        assert baseline.run_id == str(RUN_ID)

    def test_an_estate_nobody_has_collected_says_so(self):
        baseline = SimulationBaseline(kind=BaselineKind.CURRENT, basis=EMPTY_BASIS, captured_at=NOW)

        assert baseline.is_empty

    def test_staleness_is_a_token_comparison_and_not_a_clock(self):
        """The token moves if and only if a collector has written something.

        So an unchanged token is proof the simulation would compute the same answer today —
        which is a stronger statement than any time-to-live can make.
        """
        baseline = fixed_baseline(observations=12)
        later = CollectionBasis(
            runs=1,
            latest_run_id=str(RUN_ID),
            latest_activity_at=baseline.basis.latest_activity_at,
            observations_applied=13,
            batches_received=1,
        )

        assert not baseline.is_stale_against(baseline.basis)
        assert baseline.is_stale_against(later)


class TestTheScope:
    @pytest.mark.parametrize("kind", [ScopeKind.PAIR, ScopeKind.SUBJECT])
    def test_a_subject_scope_must_name_its_subject(self, kind):
        with pytest.raises(DomainValidationError, match="one principal"):
            SimulationScope(kind=kind, resource_key=FINANCE)

    @pytest.mark.parametrize("kind", [ScopeKind.PAIR, ScopeKind.RESOURCE])
    def test_a_resource_scope_must_name_its_resource(self, kind):
        with pytest.raises(DomainValidationError, match="directory"):
            SimulationScope(kind=kind, subject_key=ALICE)

    def test_an_affected_scope_derives_its_own_subjects(self):
        """Naming one would narrow the answer without saying so."""
        with pytest.raises(DomainValidationError, match="derives its own"):
            SimulationScope(kind=ScopeKind.AFFECTED, subject_key=ALICE)

    def test_the_default_scope_is_affected_over_remote_smb(self):
        scope = SimulationScope()

        assert scope.kind is ScopeKind.AFFECTED
        assert scope.path is AccessPath.REMOTE_SMB

    def test_the_document_names_every_field_a_report_is_keyed_by(self):
        document = SimulationScope(
            kind=ScopeKind.PAIR,
            subject_key=ALICE,
            resource_key=FINANCE,
            path=AccessPath.LOCAL,
        ).document()

        assert document == {
            "kind": "pair",
            "subject_key": ALICE,
            "resource_key": FINANCE,
            "path": "local",
            "limit": 100,
            "after": None,
        }


class TestTheBounds:
    def test_a_bound_beyond_its_ceiling_is_refused_with_the_reason(self):
        with pytest.raises(SimulationBoundsError, match="answerable inside one request"):
            SimulationBounds(max_principals=MAX_PRINCIPALS_CEILING + 1)

    def test_a_bound_below_one_is_refused(self):
        with pytest.raises(SimulationBoundsError, match="at least 1"):
            SimulationBounds(max_pairs=0)

    def test_clamping_accepts_an_overlarge_request_and_narrows_it(self):
        """A caller asking for everything gets a bounded answer that says it is bounded."""
        bounds = SimulationBounds().clamped(max_pairs=MAX_PAIRS_CEILING * 10)

        assert bounds.max_pairs == MAX_PAIRS_CEILING

    def test_clamping_ignores_the_fields_it_is_not_given(self):
        bounds = SimulationBounds(max_pairs=10).clamped(max_principals=5, max_pairs=None)

        assert bounds.max_pairs == 10
        assert bounds.max_principals == 5


class TestApplications:
    def test_a_parent_that_was_never_read_still_counts_as_applied(self):
        """The protection flag did flip; what could not be computed is the projection.

        Treating it as unapplied would report a simulated ACL that the report then claims was
        never changed.
        """
        change = MembershipChange(
            kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_TEAM, member_key=ALICE
        )
        application = ChangeApplication(change, ChangeOutcome.PARENT_NOT_OBSERVED)

        assert application.applied

    @pytest.mark.parametrize(
        "outcome",
        [
            ChangeOutcome.ALREADY_PRESENT,
            ChangeOutcome.TARGET_NOT_FOUND,
            ChangeOutcome.TARGET_NOT_OBSERVED,
            ChangeOutcome.NOT_REPRESENTABLE,
        ],
    )
    def test_everything_else_leaves_the_world_unchanged(self, outcome):
        change = MembershipChange(
            kind=ChangeKind.ADD_MEMBER, group_key=FINANCE_TEAM, member_key=ALICE
        )

        assert not ChangeApplication(change, outcome).applied


class TestVocabulariesAreComplete:
    """Every value a report can carry renders. A missing sentence is a blank cell in a UI."""

    def test_every_outcome_has_a_description(self):
        assert set(OUTCOME_DESCRIPTIONS) == set(ChangeOutcome)

    def test_every_caveat_has_a_description(self):
        assert set(CAVEAT_DESCRIPTIONS) == set(SimulationCaveat)

    def test_every_truncation_reason_has_a_description(self):
        assert set(TRUNCATION_DESCRIPTIONS) == set(SimulationTruncation)


class TestSummaries:
    def test_an_empty_impact_list_summarizes_to_zero(self):
        summary = summarize([])

        assert summary.evaluated == 0
        assert not summary.any_change

    def test_truncation_reasons_come_back_in_the_enum_s_order(self):
        """A total order that does not depend on which bound was hit first."""
        one = ordered_truncation(
            [SimulationTruncation.TIME_BUDGET, SimulationTruncation.PAIR_BUDGET]
        )
        other = ordered_truncation(
            [SimulationTruncation.PAIR_BUDGET, SimulationTruncation.TIME_BUDGET]
        )

        assert (
            one
            == other
            == (
                SimulationTruncation.PAIR_BUDGET,
                SimulationTruncation.TIME_BUDGET,
            )
        )

    def test_a_reason_reported_twice_appears_once(self):
        assert ordered_truncation(
            [SimulationTruncation.PAIR_BUDGET, SimulationTruncation.PAIR_BUDGET]
        ) == (SimulationTruncation.PAIR_BUDGET,)


class TestDirections:
    @pytest.mark.parametrize("direction", [ImpactDirection.GAINED_ACCESS, ImpactDirection.EXPANDED])
    def test_gains(self, direction):
        assert direction.is_gain
        assert not direction.is_loss

    @pytest.mark.parametrize("direction", [ImpactDirection.LOST_ACCESS, ImpactDirection.REDUCED])
    def test_losses(self, direction):
        assert direction.is_loss
        assert not direction.is_gain

    @pytest.mark.parametrize("direction", [ImpactDirection.UNCHANGED, ImpactDirection.CHANGED])
    def test_neither(self, direction):
        assert not direction.is_gain
        assert not direction.is_loss
