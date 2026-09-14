"""The retention policy as a value: what it refuses to be configured as, and its cutoff."""

from __future__ import annotations

import datetime as dt

import pytest

from app.config import build_settings
from app.domain.errors import DomainValidationError
from app.history.retention import MIN_RETENTION_DAYS, RetentionPolicy

NOW = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.UTC)


class TestTheDefaultKeepsEverything:
    def test_an_unconfigured_policy_deletes_nothing(self) -> None:
        policy = RetentionPolicy()

        assert policy.keeps_everything
        assert not policy.enabled
        assert policy.cutoff(NOW) is None

    def test_the_settings_default_is_the_same(self) -> None:
        assert build_settings(environment="test").history_retention_policy.keeps_everything

    def test_keeping_everything_is_a_statable_policy_not_merely_an_absence(self) -> None:
        assert RetentionPolicy().describe() == (
            "History retention: every version is kept indefinitely."
        )


class TestWhatItRefuses:
    def test_a_window_shorter_than_a_month_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="audit cycle"):
            RetentionPolicy(retain_days=MIN_RETENTION_DAYS - 1, enabled=True)

    def test_a_negative_window_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="negative"):
            RetentionPolicy(retain_days=-1)

    def test_a_window_beyond_a_century_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="must be 0"):
            RetentionPolicy(retain_days=40_000)

    def test_enabling_a_policy_that_keeps_everything_is_refused(self) -> None:
        """Enabled with nothing to enforce reads as protection that is not there."""
        with pytest.raises(DomainValidationError, match="nothing to enforce"):
            RetentionPolicy(retain_days=0, enabled=True)


class TestTwoSwitchesNotOne:
    def test_a_window_alone_authorizes_nothing(self) -> None:
        policy = RetentionPolicy(retain_days=365)

        assert not policy.enabled
        assert "not enabled" in policy.describe()

    def test_the_cutoff_is_the_window_before_now(self) -> None:
        policy = RetentionPolicy(retain_days=90, enabled=True)

        assert policy.cutoff(NOW) == NOW - dt.timedelta(days=90)

    def test_both_switches_together_are_reported_as_enabled(self) -> None:
        assert "enabled" in RetentionPolicy(retain_days=365, enabled=True).describe()
