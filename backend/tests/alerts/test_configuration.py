"""The alert policy: what an installation may change, and what it is refused.

Refused **loudly and wholly**, never partially. A policy file that silently dropped the one
webhook somebody depended on would leave an installation that delivers nowhere and says
nothing about it — which is the same failure the risk configuration guards against, one
layer over.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from app.alerts import (
    DEFAULT_POLICY,
    AlertPolicy,
    AlertTrigger,
    PolicyError,
    RetrySchedule,
    SinkConfig,
    describe_policy,
    load_policy,
    parse_policy,
)
from app.alerts.configuration import _SEVERITY_VALUES, MAX_ATTEMPTS_CEILING
from app.alerts.sinks import build_sink, default_registry
from app.risk_engine import SEVERITY_ORDER


def test_the_severity_vocabulary_matches_the_risk_engine() -> None:
    """The one duplication the layering costs, pinned so it cannot drift.

    :mod:`app.alerts` deliberately does not import :mod:`app.risk_engine` -- a sink that
    could reach the rules through the policy object would be the coupling the whole layering
    exists to prevent -- so the severity names are written out there. This is the test that
    makes writing them out safe: add a severity to the risk engine and this fails, rather
    than a policy naming it being refused at startup in a customer's installation.
    """
    ranked = sorted(SEVERITY_ORDER, key=lambda member: SEVERITY_ORDER[member])

    assert tuple(member.value for member in ranked) == _SEVERITY_VALUES


class TestTheShippedPolicy:
    def test_every_trigger_is_live(self) -> None:
        assert DEFAULT_POLICY.enabled_triggers == frozenset(AlertTrigger)

    def test_it_has_a_destination(self) -> None:
        """A log sink rather than none, deliberately.

        An installation with nowhere to deliver would enqueue alerts nothing ever drains,
        and the backlog would grow behind a queue depth nobody is looking at.
        """
        assert DEFAULT_POLICY.has_a_sink
        assert [sink.kind for sink in DEFAULT_POLICY.sinks] == ["log"]

    def test_only_critical_findings_raise_an_alert_on_their_own(self) -> None:
        assert DEFAULT_POLICY.finding_severity_threshold == "critical"


class TestParsing:
    def test_a_misspelled_key_is_refused_rather_than_ignored(self) -> None:
        """Ignoring it would leave the setting it was meant to change at its default,
        silently -- and the operator would believe they had changed it."""
        with pytest.raises(PolicyError) as error:
            parse_policy({"default_cooldown_second": 60})

        assert "Unknown alert policy key" in str(error.value)

    def test_an_unknown_trigger_names_the_ones_that_exist(self) -> None:
        with pytest.raises(PolicyError) as error:
            parse_policy({"enabled_triggers": ["watched_group_membership_change"]})

        assert "watched_group_membership_changed" in str(error.value)

    def test_an_unknown_severity_threshold_is_refused(self) -> None:
        with pytest.raises(PolicyError):
            parse_policy({"finding_severity_threshold": "catastrophic"})

    def test_two_sinks_with_one_name_are_refused(self) -> None:
        """A delivery is identified by its alert and its sink name.

        Two sinks with one name would collapse into a single delivery history, and each
        would appear to have received what the other did.
        """
        with pytest.raises(PolicyError) as error:
            parse_policy(
                {
                    "sinks": [
                        {"name": "ops", "kind": "log"},
                        {"name": "ops", "kind": "webhook", "options": {"url": "https://x/y"}},
                    ]
                }
            )

        assert "collapse into one delivery history" in str(error.value)

    def test_a_policy_can_turn_a_trigger_off(self) -> None:
        policy = parse_policy({"enabled_triggers": ["critical_risk_finding_opened"]})

        assert policy.enables(AlertTrigger.CRITICAL_RISK_FINDING_OPENED)
        assert not policy.enables(AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED)

    def test_a_sink_can_take_only_some_triggers(self) -> None:
        """So a noisy development webhook can take the change notices while a quieter one
        takes only the findings -- without either of them knowing what a finding is."""
        policy = parse_policy(
            {
                "sinks": [
                    {
                        "name": "pager",
                        "kind": "log",
                        "triggers": ["critical_risk_finding_opened"],
                    },
                    {"name": "chatter", "kind": "log"},
                ]
            }
        )

        pager_only = policy.sinks_for(AlertTrigger.CRITICAL_RISK_FINDING_OPENED)
        acl_only = policy.sinks_for(AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED)

        assert {sink.name for sink in pager_only} == {"pager", "chatter"}
        assert {sink.name for sink in acl_only} == {"chatter"}

    def test_a_disabled_sink_receives_nothing(self) -> None:
        policy = parse_policy({"sinks": [{"name": "ops", "kind": "log", "enabled": False}]})

        assert policy.sinks_for(AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED) == ()
        assert not policy.has_a_sink


class TestTheRetrySchedule:
    def test_a_multiplier_below_one_is_refused(self) -> None:
        """It would make each attempt sooner than the last, turning a failing endpoint into
        a tight loop."""
        with pytest.raises(PolicyError):
            RetrySchedule(multiplier=0.5)

    def test_an_unbounded_attempt_count_is_refused(self) -> None:
        """A dead endpoint's backlog would grow in front of every other delivery."""
        with pytest.raises(PolicyError):
            RetrySchedule(max_attempts=MAX_ATTEMPTS_CEILING + 1)

    def test_it_is_deterministic(self) -> None:
        """No jitter, deliberately: it would buy unpredictability in a test suite, which is
        the one thing a delivery schedule must not have."""
        schedule = RetrySchedule()

        assert schedule.delay_before(3) == schedule.delay_before(3)


class TestLoadingAFile:
    def test_a_missing_file_is_an_error_rather_than_a_silent_default(self, tmp_path: Path) -> None:
        """An operator who configured a destination and typed the path wrongly would
        otherwise get an installation that delivers nowhere and says nothing about it."""
        with pytest.raises(PolicyError) as error:
            load_policy(tmp_path / "absent.json")

        assert "could not be read" in str(error.value)

    def test_malformed_json_names_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.json"
        path.write_text("{ not json", encoding="utf-8")

        with pytest.raises(PolicyError) as error:
            load_policy(path)

        assert "not valid JSON" in str(error.value)

    def test_a_valid_file_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.json"
        path.write_text(
            json.dumps(
                {
                    "version": "acme-1",
                    "default_cooldown_seconds": 600,
                    "finding_severity_threshold": "high",
                    "retry": {"initial_seconds": 10, "max_attempts": 4},
                    "sinks": [
                        {
                            "name": "ops-webhook",
                            "kind": "webhook",
                            "options": {"url": "https://example.invalid/hook"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        policy = load_policy(path)

        assert policy.version == "acme-1"
        assert policy.default_cooldown == dt.timedelta(minutes=10)
        assert policy.retry.max_attempts == 4
        assert policy.severity_rank == _SEVERITY_VALUES.index("high")


class TestDescribingAPolicy:
    def test_it_names_the_triggers_that_are_off(self) -> None:
        """A summary of only what is on would make a policy with the interesting trigger
        disabled look identical to a healthy one."""
        lines = describe_policy(
            AlertPolicy(enabled_triggers=frozenset({AlertTrigger.CRITICAL_RISK_FINDING_OPENED}))
        )

        assert any("watched_resource_acl_changed: OFF" in line for line in lines)

    def test_it_says_when_nothing_can_receive_a_delivery(self) -> None:
        no_sinks = describe_policy(AlertPolicy(sinks=()))
        all_off = describe_policy(
            AlertPolicy(sinks=(SinkConfig(name="ops", kind="log", enabled=False),))
        )

        assert any("NONE CONFIGURED" in line for line in no_sinks)
        assert any("delivered nowhere" in line for line in all_off)


class TestBuildingASink:
    def test_an_unknown_kind_names_the_ones_this_build_has(self) -> None:
        with pytest.raises(PolicyError) as error:
            build_sink(SinkConfig(name="x", kind="carrier-pigeon"))

        assert "'log'" in str(error.value)

    def test_a_webhook_without_a_url_is_refused_at_construction(self) -> None:
        with pytest.raises(PolicyError):
            build_sink(SinkConfig(name="x", kind="webhook"))

    def test_the_registry_is_a_plain_mapping_a_deployment_can_extend(self) -> None:
        assert set(default_registry()) == {"log", "webhook"}
