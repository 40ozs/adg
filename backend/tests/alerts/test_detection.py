"""Facts in, candidate alerts out.

These run against the flat records :mod:`app.alerts.detection` reads, not against a change
feed or a risk finding — which is the whole point of the neutral inputs: the matching rules
are exercisable without staging an ingestion that happens to change the right thing.
"""

from __future__ import annotations

from uuid import uuid4

from app.alerts import (
    AlertTrigger,
    WatchIndex,
    WatchKind,
    events_for_access_expansions,
    events_for_changes,
    events_for_findings,
    resolution_keys,
)
from tests.alerts.support import (
    ALICE,
    FINANCE,
    GROUP,
    SHARE,
    ace_change,
    expansion,
    finding,
    membership_change,
    watch,
)

ALL = frozenset(AlertTrigger)
ACL = AlertTrigger.WATCHED_RESOURCE_ACL_CHANGED
MEMBERSHIP = AlertTrigger.WATCHED_GROUP_MEMBERSHIP_CHANGED
EXPANDED = AlertTrigger.WATCHED_ACCESS_EXPANDED
FINDING = AlertTrigger.CRITICAL_RISK_FINDING_OPENED


class TestMatchingAWatch:
    def test_an_entry_on_a_watched_directory_raises_an_alert(self) -> None:
        index = WatchIndex.build([watch(kind=WatchKind.RESOURCE, key=FINANCE)])

        events = events_for_changes(index, [ace_change()], enabled_triggers=ALL)

        assert [item.trigger for item in events] == [ACL]
        assert events[0].subject.resource_key == FINANCE
        assert "Alice" in events[0].summary

    def test_an_entry_on_a_directory_nobody_watches_raises_nothing(self) -> None:
        index = WatchIndex.build([watch(key="\\\\fs01\\marketing")])

        assert events_for_changes(index, [ace_change()], enabled_triggers=ALL) == ()

    def test_a_watch_key_typed_in_another_case_still_matches(self) -> None:
        r"""A resource key is a case-folded UNC path; a watch key arrives from a person.

        Folding only one side would leave a watch typed ``\\FS01\Finance`` configured,
        listed, and permanently inert.
        """
        index = WatchIndex.build([watch(key="\\\\FS01\\Finance")])

        assert len(events_for_changes(index, [ace_change()], enabled_triggers=ALL)) == 1

    def test_membership_matches_on_the_group_not_the_member(self) -> None:
        """The container is the group whose membership was enumerated.

        Matching on the member instead would alert the group's watch whenever that person
        joined anything, which is a different question and a much noisier one.
        """
        index = WatchIndex.build([watch(kind=WatchKind.GROUP, key=GROUP)])

        events = events_for_changes(index, [membership_change()], enabled_triggers=ALL)

        assert [item.trigger for item in events] == [MEMBERSHIP]
        assert events[0].subject.principal_key == GROUP

    def test_a_share_entry_matches_a_share_watch(self) -> None:
        index = WatchIndex.build(
            [watch(kind=WatchKind.SHARE, key=SHARE, triggers=frozenset({ACL}))]
        )

        events = events_for_changes(
            index, [ace_change(place=SHARE, kind="smb_ace")], enabled_triggers=ALL
        )

        assert [item.subject.share_key for item in events] == [SHARE]

    def test_a_descriptor_change_matches_on_its_own_key(self) -> None:
        """``dacl_present`` going false is a NULL DACL, which grants everyone everything.

        A watch that fired on entries and not on the descriptor would miss the worst case
        while appearing to cover the access control list.
        """
        index = WatchIndex.build([watch(key=FINANCE)])
        change = ace_change(kind="ntfs_resource")
        change = type(change)(
            kind="ntfs_resource",
            key=FINANCE,
            action="modified",
            container_key="share|fs01|finance",
            related_key=None,
            severity="critical",
            direction="broadened",
            at=change.at,
        )

        events = events_for_changes(index, [change], enabled_triggers=ALL)

        assert len(events) == 1
        assert events[0].subject.resource_key == FINANCE

    def test_a_change_of_a_kind_that_is_not_an_access_fact_raises_nothing(self) -> None:
        index = WatchIndex.build([watch(kind=WatchKind.GROUP, key=GROUP)])
        change = membership_change()
        renamed = type(change)(
            kind="principal",
            key=GROUP,
            action="modified",
            container_key=None,
            related_key=None,
            severity="info",
            direction="neutral",
            at=change.at,
        )

        assert events_for_changes(index, [renamed], enabled_triggers=ALL) == ()


class TestTwoWatchesAndTwoTrustees:
    def test_two_watches_on_one_thing_each_get_their_own_alert(self) -> None:
        """They are separate subscriptions, so one team's storm is not the other's.

        Each alert has its own key, so each is counted, suppressed and cooled down on its
        own schedule.
        """
        index = WatchIndex.build(
            [
                watch(key=FINANCE, label="Finance team"),
                watch(key=FINANCE, label="Security team"),
            ]
        )

        events = events_for_changes(index, [ace_change()], enabled_triggers=ALL)

        assert len(events) == 2
        assert len({item.watch_id for item in events}) == 2

    def test_two_trustees_edited_on_one_list_are_two_alerts(self) -> None:
        """Folding them into one would make the cooldown hide the second trustee entirely."""
        index = WatchIndex.build([watch(key=FINANCE)])

        events = events_for_changes(
            index,
            [ace_change(trustee=ALICE), ace_change(trustee="principal|S-1-5-21-9")],
            enabled_triggers=ALL,
        )

        assert len({item.key for item in events}) == 2


class TestTriggersThatAreOff:
    def test_a_disabled_trigger_produces_nothing_at_all(self) -> None:
        """Not a suppressed record: off is off, not quieted.

        A suppressed record would appear in the feed's event history as something ADG chose
        not to deliver, which would be wrong -- nobody subscribed to it.
        """
        index = WatchIndex.build([watch(key=FINANCE)])

        assert events_for_changes(index, [ace_change()], enabled_triggers=frozenset()) == ()

    def test_a_watch_subscribed_to_only_the_expansion_trigger_ignores_the_edit(self) -> None:
        index = WatchIndex.build([watch(key=FINANCE, triggers=frozenset({EXPANDED}))])

        assert events_for_changes(index, [ace_change()], enabled_triggers=ALL) == ()


class TestAccessExpansion:
    def test_it_raises_where_access_actually_grew(self) -> None:
        index = WatchIndex.build([watch(key=FINANCE, triggers=frozenset({EXPANDED}))])

        events = events_for_access_expansions(index, [expansion()], enabled_triggers=ALL)

        assert [item.trigger for item in events] == [EXPANDED]
        assert events[0].payload["gained"] == "Modify"
        assert events[0].subject.principal_key == ALICE

    def test_it_is_a_separate_alert_from_the_edit_that_caused_it(self) -> None:
        """Neither implies the other, so they must not share an identity.

        An entry can be added that grants nothing because a Deny still governs, and access
        can widen with no entry touched because a group gained a member. One alert key for
        both would deduplicate one against the other.
        """
        index = WatchIndex.build([watch(key=FINANCE, triggers=frozenset({ACL, EXPANDED}))])

        edits = events_for_changes(index, [ace_change()], enabled_triggers=ALL)
        grew = events_for_access_expansions(index, [expansion()], enabled_triggers=ALL)

        assert edits[0].key != grew[0].key


class TestFindings:
    def test_a_finding_raises_without_any_watch(self) -> None:
        """Estate-wide by policy.

        Requiring a subscription would make the feature's coverage equal to somebody's
        foresight, and the exposure worth interrupting somebody for is the one on the share
        nobody thought to watch.
        """
        events = events_for_findings([finding()], enabled_triggers=ALL)

        assert [item.trigger for item in events] == [FINDING]
        assert events[0].watch_id is None

    def test_two_rules_about_one_directory_are_two_alerts(self) -> None:
        events = events_for_findings(
            [
                finding(key="a" * 64, rule="everyone_has_access"),
                finding(key="b" * 64, rule="null_dacl"),
            ],
            enabled_triggers=ALL,
        )

        assert len({item.key for item in events}) == 2

    def test_a_resolution_derives_the_same_key_the_raise_did(self) -> None:
        """Derived the same way rather than looked up.

        A query on the payload would be a second implementation of the alert key, and the
        day the two disagreed the resolution would silently close nothing -- leaving an
        operator acting on an exposure that is gone.
        """
        opened = events_for_findings([finding()], enabled_triggers=ALL)

        assert resolution_keys([finding()]) == (opened[0].key,)


class TestTheIndex:
    def test_it_ignores_disabled_watches_at_lookup(self) -> None:
        index = WatchIndex.build([watch(key=FINANCE, enabled=False)])

        assert index.matching(WatchKind.RESOURCE, FINANCE, ACL) == ()

    def test_it_reports_emptiness(self) -> None:
        assert WatchIndex.build([]).is_empty
        assert not WatchIndex.build([watch()]).is_empty

    def test_a_null_key_matches_nothing_rather_than_raising(self) -> None:
        """A change of a kind with no container legitimately has none."""
        index = WatchIndex.build([watch(key=FINANCE)])

        assert index.matching(WatchKind.RESOURCE, None, ACL) == ()


class TestThePayload:
    def test_it_carries_the_change_window_rather_than_the_scan_instant(self) -> None:
        """ADR-0019: a change is a window, not an instant.

        A notification that dated an edit to the scan that noticed it would be quoting a
        time the edit demonstrably did not happen at.
        """
        index = WatchIndex.build([watch(key=FINANCE)])
        change = ace_change()
        windowed = type(change)(
            **{
                **{
                    field: getattr(change, field)
                    for field in (
                        "kind",
                        "key",
                        "action",
                        "container_key",
                        "related_key",
                        "severity",
                        "direction",
                        "at",
                        "reasons",
                        "container_label",
                        "related_label",
                    )
                },
                "window": (change.at, change.at),
            }
        )

        events = events_for_changes(index, [windowed], enabled_triggers=ALL)

        assert events[0].payload["window"] == {
            "from": change.at.isoformat(),
            "to": change.at.isoformat(),
        }

    def test_it_carries_the_classifier_severity_and_not_one_of_its_own(self) -> None:
        index = WatchIndex.build([watch(key=FINANCE)])

        events = events_for_changes(index, [ace_change(severity="critical")], enabled_triggers=ALL)

        assert events[0].payload["change_severity"] == "critical"
        assert not hasattr(events[0], "severity")

    def test_a_finding_payload_carries_the_risk_engine_severity(self) -> None:
        events = events_for_findings([finding(severity="high")], enabled_triggers=ALL)

        assert events[0].payload["severity"] == "high"


def test_a_watch_id_survives_onto_the_event() -> None:
    """Without it, removing the watch could not silence the alerts it raised."""
    identifier = uuid4()
    index = WatchIndex.build([watch(key=FINANCE, watch_id=identifier)])

    events = events_for_changes(index, [ace_change()], enabled_triggers=ALL)

    assert events[0].watch_id == identifier
