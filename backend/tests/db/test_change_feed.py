r"""The change feed over a real estate, posted through the real ingestion endpoints.

The estate is three scans long and every assertion below is about one of the four things a
scan diff gets wrong:

* **Monday** — the first scan. Everything is a first sighting and **nothing is a change.**
* **Wednesday** — nothing moved, and the scan says so. This is what makes Monday's states
  observed rather than merely uncontradicted, and it is a window with no changes in it.
* **Friday** — Alice leaves ``Finance-RW``; the directory's ACL is tightened from Full
  Control to Read & Execute; ``Everyone`` is granted read; the ``HR`` share is deleted.

Nothing here builds a version by hand. Every row arrives through ``POST /api/v1/scan-runs``
and its batches, so a test cannot produce a history the product would refuse to record —
which matters most for the removals, since a tombstone can only be written by a scan that
reconciled a scope, and that guard is the whole reason the feed can be trusted.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.changes import (
    ChangeAction,
    ChangeDirection,
    ChangeScope,
    ChangeService,
    ChangeSeverity,
    ChangeSignificance,
    ScopeTarget,
)
from app.changes.service import DEFAULT_ACTIONS, ChangeFilter
from app.contracts.v1.common import ObservationKind
from app.domain import AceType, PrincipalKind, SharePermission
from app.domain.errors import DomainValidationError
from tests.support import history as h
from tests.support.ingest import replay

FS01 = "FS01"
FINANCE_PATH = "\\\\fs01\\finance"
FINANCE_SHARE = "fs01|finance"
HR_SHARE = "fs01|hr"
GROUP = f"{h.DOMAIN_SID}-1101"
ALICE = f"{h.DOMAIN_SID}-1104"
EVERYONE = "S-1-1-0"

FULL_CONTROL = 0x1F01FF
READ_EXECUTE = 0x1200A9

BEFORE_FRIDAY = h.THURSDAY
AFTER_FRIDAY = h.NEXT_MONDAY


def ace_key(mask: int, trustee: str = GROUP, ace_type: str = "allow") -> str:
    return f"{FINANCE_PATH}|{trustee}|{ace_type}|0x{mask:08x}|0x03"


async def _scan_day(client: AsyncClient, moment: dt.datetime, *, aces, members, shares) -> None:
    await replay(
        client,
        h.ad_scan(
            observations=[
                h.principal(GROUP, at=moment, display_name="Finance-RW"),
                h.principal(
                    ALICE, at=moment, kind=PrincipalKind.USER, display_name="Alice", enabled=True
                ),
                *[h.edge(GROUP, member, at=moment) for member in members],
            ],
            started_at=moment,
        ),
    )
    await replay(
        client,
        h.smb_scan(
            observations=[
                h.server(FS01, at=moment),
                *[h.share(FS01, name, at=moment) for name in shares],
                *[
                    h.share_ace(FS01, name, GROUP, at=moment, permission=SharePermission.FULL)
                    for name in shares
                ],
            ],
            started_at=moment,
        ),
    )
    await replay(
        client,
        h.ntfs_scan(
            observations=[
                h.resource(
                    FINANCE_PATH,
                    at=moment,
                    server_name=FS01,
                    share_name="Finance",
                    ace_count=len(aces),
                ),
                *[
                    h.ntfs_ace(
                        FINANCE_PATH,
                        trustee,
                        at=moment,
                        access_mask=mask,
                        order_index=index,
                    )
                    for index, (trustee, mask) in enumerate(aces)
                ],
            ],
            started_at=moment,
        ),
    )


@pytest.fixture
async def three_scans(client: AsyncClient) -> None:
    """Monday, Wednesday and Friday, through the real ingestion endpoints."""
    for moment in (h.MONDAY, h.WEDNESDAY):
        await _scan_day(
            client,
            moment,
            aces=[(GROUP, FULL_CONTROL)],
            members=[ALICE],
            shares=["Finance", "HR"],
        )
    await _scan_day(
        client,
        h.FRIDAY,
        aces=[(GROUP, READ_EXECUTE), (EVERYONE, READ_EXECUTE)],
        members=[],
        shares=["Finance"],
    )


def everything(window_from: dt.datetime, window_to: dt.datetime, **kwargs: Any) -> ChangeFilter:
    """A filter that hides nothing, for tests that assert on the whole window."""
    kwargs.setdefault("actions", frozenset(ChangeAction))
    kwargs.setdefault("significance", frozenset(ChangeSignificance))
    kwargs.setdefault("limit", 500)
    return ChangeFilter(window_from=window_from, window_to=window_to, **kwargs)


class TestTheFirstScanIsNotAChangeReport:
    async def test_every_object_of_the_first_scan_is_a_first_sighting(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        """Not an addition. ADG started looking; nothing was created on Monday."""
        feed = await ChangeService(session).feed(
            everything(h.MONDAY - dt.timedelta(days=1), h.WEDNESDAY)
        )
        assert feed.changes
        assert {change.action for change in feed.changes} == {ChangeAction.FIRST_OBSERVED}

    async def test_the_default_filter_reports_the_first_scan_as_empty(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        """An estate's first scan under the default filter is a quiet page, correctly.

        Reporting four million creations would answer a question nobody asked, and would
        train the reader to ignore the page on the one day it matters.
        """
        feed = await ChangeService(session).feed(
            ChangeFilter(window_from=h.MONDAY - dt.timedelta(days=1), window_to=h.WEDNESDAY)
        )
        assert feed.changes == ()

    async def test_the_summary_still_counts_them_so_the_exclusion_is_visible(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        summary = await ChangeService(session).summary(
            ChangeFilter(window_from=h.MONDAY - dt.timedelta(days=1), window_to=h.WEDNESDAY)
        )
        assert summary.total > 0
        assert summary.returned == 0
        assert summary.excluded == summary.total
        assert summary.by_action[ChangeAction.FIRST_OBSERVED] == summary.total

    async def test_a_scan_that_changed_nothing_produces_no_changes(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        """Wednesday re-read the whole estate and moved nothing.

        This is the assertion that separates a change feed from a scan log: an unchanged
        object extends its version rather than opening a second one, so re-reading an ACL a
        thousand times produces no lines at all.
        """
        feed = await ChangeService(session).feed(everything(h.WEDNESDAY, h.THURSDAY))
        assert feed.changes == ()


class TestWhatFridayDid:
    async def test_the_membership_removal_is_reported_as_a_removal(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        feed = await ChangeService(session).feed(
            everything(
                BEFORE_FRIDAY, AFTER_FRIDAY, kinds=frozenset({ObservationKind.MEMBERSHIP_EDGE})
            )
        )
        removals = [c for c in feed.changes if c.action is ChangeAction.REMOVED]
        assert len(removals) == 1
        assert removals[0].direction is ChangeDirection.NARROWED
        assert removals[0].subject.container_key == GROUP
        assert removals[0].subject.related_key == ALICE

    async def test_the_deleted_share_is_reported_as_a_removal(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        feed = await ChangeService(session).feed(
            everything(BEFORE_FRIDAY, AFTER_FRIDAY, kinds=frozenset({ObservationKind.SMB_SHARE}))
        )
        removed = [c for c in feed.changes if c.action is ChangeAction.REMOVED]
        assert [c.key for c in removed] == [HR_SHARE]

    async def test_granting_everyone_read_is_reported_as_an_addition(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        """And as an addition rather than a first sighting, because the directory was
        already being read — which is the distinction the container lookup exists for."""
        feed = await ChangeService(session).feed(
            everything(BEFORE_FRIDAY, AFTER_FRIDAY, kinds=frozenset({ObservationKind.NTFS_ACE}))
        )
        everyone = [c for c in feed.changes if c.subject.related_key == EVERYONE]
        assert len(everyone) == 1
        assert everyone[0].action is ChangeAction.ADDED
        assert everyone[0].severity is ChangeSeverity.HIGH
        assert everyone[0].direction is ChangeDirection.BROADENED
        assert everyone[0].rule_ids == ("ace.allow.broad",)

    async def test_a_change_reports_the_window_it_happened_in_not_the_scan_time(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        """ADR-0019. Friday's scan is when somebody looked; the edit happened after
        Wednesday's confirmation and at or before Friday's reading."""
        feed = await ChangeService(session).feed(
            everything(BEFORE_FRIDAY, AFTER_FRIDAY, kinds=frozenset({ObservationKind.NTFS_ACE}))
        )
        rewritten = [c for c in feed.changes if c.action is ChangeAction.REMOVED]
        assert rewritten
        window = rewritten[0].window
        assert window is not None
        assert window.after == h.WEDNESDAY
        assert window.at_or_before > h.WEDNESDAY
        assert not window.is_exact


class TestAnAclEditIsOneEditAgain:
    async def test_the_tightening_is_paired_into_a_single_narrowing(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        """Full Control to Read & Execute is stored as a removal plus an addition, because
        an ACE's rights are part of its identity. An operator must see one edit."""
        feed = await ChangeService(session).feed(
            everything(BEFORE_FRIDAY, AFTER_FRIDAY, kinds=frozenset({ObservationKind.NTFS_ACE}))
        )
        edits = feed.correlation.edits
        assert len(edits) == 1
        edit = edits[0]
        assert edit.identity.trustee_key == GROUP
        assert edit.direction is ChangeDirection.NARROWED
        assert edit.rights_before is not None and edit.rights_before.value == FULL_CONTROL
        assert edit.rights_after is not None and edit.rights_after.value == READ_EXECUTE

    async def test_both_halves_of_the_edit_point_at_it(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        from app.changes.correlation import key_of

        feed = await ChangeService(session).feed(
            everything(BEFORE_FRIDAY, AFTER_FRIDAY, kinds=frozenset({ObservationKind.NTFS_ACE}))
        )
        paired = [c for c in feed.changes if key_of(c) in feed.correlation.edit_of]
        assert len(paired) == 2
        assert {c.action for c in paired} == {ChangeAction.ADDED, ChangeAction.REMOVED}

    async def test_the_new_everyone_entry_is_not_swept_into_the_edit(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        """It is a different trustee, so it is a separate grant and not a rewrite."""
        feed = await ChangeService(session).feed(
            everything(BEFORE_FRIDAY, AFTER_FRIDAY, kinds=frozenset({ObservationKind.NTFS_ACE}))
        )
        assert all(edit.identity.trustee_key != EVERYONE for edit in feed.correlation.edits)


class TestScopes:
    async def test_a_share_scope_excludes_the_directory_group(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        feed = await ChangeService(session).feed(
            everything(
                h.MONDAY - dt.timedelta(days=1),
                AFTER_FRIDAY,
                scope=ChangeScope(ScopeTarget.SHARE, FINANCE_SHARE),
            )
        )
        assert feed.changes
        assert {c.kind for c in feed.changes} <= {
            ObservationKind.SMB_SHARE,
            ObservationKind.SMB_ACE,
            ObservationKind.NTFS_RESOURCE,
            ObservationKind.NTFS_ACE,
        }
        assert all(ObservationKind.PRINCIPAL is not c.kind for c in feed.changes)

    async def test_a_share_scope_reaches_the_ntfs_aces_beneath_it(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        """Through the share's UNC spelling, which is the awkward half of the rule table."""
        feed = await ChangeService(session).feed(
            everything(
                BEFORE_FRIDAY, AFTER_FRIDAY, scope=ChangeScope(ScopeTarget.SHARE, FINANCE_SHARE)
            )
        )
        assert any(c.kind is ObservationKind.NTFS_ACE for c in feed.changes)

    async def test_a_group_scope_returns_only_that_groups_membership(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        feed = await ChangeService(session).feed(
            everything(BEFORE_FRIDAY, AFTER_FRIDAY, scope=ChangeScope(ScopeTarget.GROUP, GROUP))
        )
        assert {c.kind for c in feed.changes} <= {
            ObservationKind.MEMBERSHIP_EDGE,
            ObservationKind.PRINCIPAL,
        }

    async def test_a_principal_scope_finds_the_edge_from_the_members_side(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        """Alice is the far end of the edge that removed her. A one-directional predicate
        would return nothing and look like a principal with a quiet week."""
        feed = await ChangeService(session).feed(
            everything(BEFORE_FRIDAY, AFTER_FRIDAY, scope=ChangeScope(ScopeTarget.PRINCIPAL, ALICE))
        )
        assert any(c.kind is ObservationKind.MEMBERSHIP_EDGE for c in feed.changes)

    async def test_a_scope_naming_nothing_returns_an_empty_page_not_an_error(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        feed = await ChangeService(session).feed(
            everything(
                BEFORE_FRIDAY, AFTER_FRIDAY, scope=ChangeScope(ScopeTarget.SHARE, "fs99|nothing")
            )
        )
        assert feed.changes == ()
        assert not feed.has_more


class TestFilteringAndPaging:
    async def test_the_default_filter_keeps_the_security_changes(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        feed = await ChangeService(session).feed(
            ChangeFilter(window_from=BEFORE_FRIDAY, window_to=AFTER_FRIDAY)
        )
        assert feed.changes
        assert {c.action for c in feed.changes} <= DEFAULT_ACTIONS
        assert all(c.is_security_relevant for c in feed.changes)

    async def test_a_minimum_severity_filters_by_rank(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        service = ChangeService(session)
        high = await service.feed(
            ChangeFilter(
                window_from=BEFORE_FRIDAY,
                window_to=AFTER_FRIDAY,
                min_severity=ChangeSeverity.HIGH,
            )
        )
        assert high.changes
        assert all(
            c.severity in (ChangeSeverity.HIGH, ChangeSeverity.CRITICAL) for c in high.changes
        )

    async def test_the_summary_says_how_many_the_filter_hid(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        summary = await ChangeService(session).summary(
            ChangeFilter(
                window_from=BEFORE_FRIDAY,
                window_to=AFTER_FRIDAY,
                min_severity=ChangeSeverity.CRITICAL,
            )
        )
        assert summary.total > summary.returned
        assert summary.excluded == summary.total - summary.returned

    async def test_paging_returns_every_change_exactly_once(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        service = ChangeService(session)
        seen: list[tuple] = []
        cursor = None
        for _ in range(20):
            page = await service.feed(
                everything(h.MONDAY - dt.timedelta(days=1), AFTER_FRIDAY, limit=3),
                cursor=cursor,
            )
            seen.extend((c.kind, c.key, c.at) for c in page.changes)
            if not page.has_more:
                break
            cursor = page.next_cursor
        whole = await ChangeService(session).feed(
            everything(h.MONDAY - dt.timedelta(days=1), AFTER_FRIDAY)
        )
        assert len(seen) == len(set(seen))
        assert set(seen) == {(c.kind, c.key, c.at) for c in whole.changes}

    async def test_changes_come_back_newest_first(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        feed = await ChangeService(session).feed(
            everything(h.MONDAY - dt.timedelta(days=1), AFTER_FRIDAY)
        )
        moments = [c.at for c in feed.changes]
        assert moments == sorted(moments, reverse=True)


class TestTheWindowIsRequiredAndBounded:
    async def test_an_empty_window_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="must end after it begins"):
            ChangeFilter(window_from=h.FRIDAY, window_to=h.MONDAY)

    async def test_a_naive_instant_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="timezone-aware"):
            ChangeFilter(
                window_from=dt.datetime(2026, 3, 2, 9, 0),
                window_to=dt.datetime(2026, 3, 6, 9, 0, tzinfo=dt.UTC),
            )

    async def test_an_unscoped_window_is_capped(self) -> None:
        with pytest.raises(DomainValidationError, match="estate-wide"):
            ChangeFilter(window_from=h.MONDAY - dt.timedelta(days=3000), window_to=h.FRIDAY)

    async def test_a_scoped_window_may_span_any_interval(self) -> None:
        """ "Everything that ever happened to this share" is a reasonable question, and the
        scope is what bounds it instead of the clock."""
        ChangeFilter(
            window_from=h.MONDAY - dt.timedelta(days=3000),
            window_to=h.FRIDAY,
            scope=ChangeScope(ScopeTarget.SHARE, FINANCE_SHARE),
        )

    async def test_the_upper_bound_is_exclusive_so_adjacent_windows_partition(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        service = ChangeService(session)
        boundary = h.WEDNESDAY
        low = h.MONDAY - dt.timedelta(days=1)
        high = AFTER_FRIDAY
        first = await service.feed(everything(low, boundary))
        second = await service.feed(everything(boundary, high))
        whole = await service.feed(everything(low, high))
        assert len(first.changes) + len(second.changes) == len(whole.changes)


class TestOneObjectsTimeline:
    async def test_it_reports_the_transitions_and_not_the_first_version(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        """The first version is where the record begins, not a creation."""
        timeline = await ChangeService(session).object_changes(
            ObservationKind.NTFS_ACE, ace_key(FULL_CONTROL)
        )
        assert [c.action for c in timeline.changes] == [ChangeAction.REMOVED]

    async def test_it_reads_newest_first(self, session: AsyncSession, three_scans: None) -> None:
        timeline = await ChangeService(session).object_changes(ObservationKind.SMB_SHARE, HR_SHARE)
        assert [c.action for c in timeline.changes] == [ChangeAction.REMOVED]

    async def test_an_object_nobody_collected_has_an_empty_timeline(
        self, session: AsyncSession, three_scans: None
    ) -> None:
        timeline = await ChangeService(session).object_changes(
            ObservationKind.SMB_SHARE, "fs99|nothing"
        )
        assert timeline.changes == ()
        assert not timeline.truncated


class TestNoRunMayProduceARemovalWithoutReconciling:
    async def test_a_scan_that_reconciles_nothing_reports_no_removals(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The guard the whole feed rests on, exercised end to end.

        A collector that simply did not mention a share produced fewer observations, not
        evidence of a deletion — and if this ever stopped holding, the Changes page would
        report access as revoked while it is still in force.
        """
        await _scan_day(
            client,
            h.MONDAY,
            aces=[(GROUP, FULL_CONTROL)],
            members=[ALICE],
            shares=["Finance", "HR"],
        )
        await replay(
            client,
            h.smb_scan(
                observations=[h.server(FS01, at=h.FRIDAY), h.share(FS01, "Finance", at=h.FRIDAY)],
                started_at=h.FRIDAY,
                reconcile=False,
            ),
        )
        feed = await ChangeService(session).feed(everything(BEFORE_FRIDAY, AFTER_FRIDAY))
        assert [c for c in feed.changes if c.action is ChangeAction.REMOVED] == []

    async def test_a_failed_run_reports_no_removals(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _scan_day(
            client,
            h.MONDAY,
            aces=[(GROUP, FULL_CONTROL)],
            members=[ALICE],
            shares=["Finance", "HR"],
        )
        await replay(
            client,
            h.smb_scan(
                observations=[h.server(FS01, at=h.FRIDAY), h.share(FS01, "Finance", at=h.FRIDAY)],
                started_at=h.FRIDAY,
                status="failed",
                reconcile=False,
                errors=[{"code": "rpc_unavailable", "message": "The server did not answer."}],
            ),
        )
        feed = await ChangeService(session).feed(everything(BEFORE_FRIDAY, AFTER_FRIDAY))
        assert [c for c in feed.changes if c.action is ChangeAction.REMOVED] == []


class TestAnAceThatCameBack:
    async def test_a_revival_after_a_tombstone_is_an_addition(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The only creation ADG can actually demonstrate: somebody looked, it was gone,
        and a later scan found it. No container question is asked."""
        await _scan_day(
            client, h.MONDAY, aces=[(GROUP, FULL_CONTROL)], members=[ALICE], shares=["Finance"]
        )
        await _scan_day(client, h.WEDNESDAY, aces=[], members=[ALICE], shares=["Finance"])
        await _scan_day(
            client, h.FRIDAY, aces=[(GROUP, FULL_CONTROL)], members=[ALICE], shares=["Finance"]
        )
        timeline = await ChangeService(session).object_changes(
            ObservationKind.NTFS_ACE, ace_key(FULL_CONTROL)
        )
        assert [c.action for c in timeline.changes] == [
            ChangeAction.ADDED,
            ChangeAction.REMOVED,
        ]
        revival = timeline.changes[0]
        assert revival.before is not None and revival.before.is_tombstone
        assert revival.window is not None


class TestTheAceTypeThatWasNotInTheScan:
    async def test_a_deny_removed_is_a_high_severity_broadening(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """No Allow is touched, so a review watching for new grants sees nothing."""
        for moment in (h.MONDAY, h.WEDNESDAY):
            await replay(
                client,
                h.ntfs_scan(
                    observations=[
                        h.resource(
                            FINANCE_PATH,
                            at=moment,
                            server_name=FS01,
                            share_name="Finance",
                            ace_count=2,
                        ),
                        h.ntfs_ace(
                            FINANCE_PATH, GROUP, at=moment, access_mask=FULL_CONTROL, order_index=1
                        ),
                        h.ntfs_ace(
                            FINANCE_PATH,
                            ALICE,
                            at=moment,
                            access_mask=FULL_CONTROL,
                            ace_type=AceType.DENY,
                            order_index=0,
                        ),
                    ],
                    started_at=moment,
                ),
            )
        await replay(
            client,
            h.ntfs_scan(
                observations=[
                    h.resource(
                        FINANCE_PATH,
                        at=h.FRIDAY,
                        server_name=FS01,
                        share_name="Finance",
                        ace_count=1,
                    ),
                    h.ntfs_ace(
                        FINANCE_PATH, GROUP, at=h.FRIDAY, access_mask=FULL_CONTROL, order_index=0
                    ),
                ],
                started_at=h.FRIDAY,
            ),
        )
        feed = await ChangeService(session).feed(
            everything(BEFORE_FRIDAY, AFTER_FRIDAY, kinds=frozenset({ObservationKind.NTFS_ACE}))
        )
        denied = [c for c in feed.changes if c.subject.related_key == ALICE]
        assert len(denied) == 1
        assert denied[0].action is ChangeAction.REMOVED
        assert denied[0].severity is ChangeSeverity.HIGH
        assert denied[0].direction is ChangeDirection.BROADENED
        assert denied[0].rule_ids == ("ace.deny.removed",)
