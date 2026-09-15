r"""Two questions that must not be conflated, and the ACL reordering that must not diff.

## The feed and the comparison are different questions

An entry added on Wednesday and removed on Thursday appears in a Tuesday-to-Friday **feed**
twice and in a Tuesday-versus-Friday **comparison** not at all. Both answers are right. The
feed answers *what happened*, which is what an incident review needs; the comparison answers
*what is different*, which is what a change-control review needs. ``TestTheyDisagree``
pins the divergence, because a build that made them agree would have quietly dropped one of
the two questions.

## A renumbered ACE is not a permission change

``order_index`` moves whenever an entry above it is removed, and Windows reports positions
relative to entries ADG does not store. So the number changes constantly and the ACL does
not. :func:`app.changes.correlation.ordering_materiality` decides which happened by
rebuilding the normalized DACL at both ends and comparing digests — the same normal form the
NTFS collector already uses to decide whether a directory inherited its parent's ACL, so
there is one definition of "the same ACL" in the codebase and this is it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.changes import ChangeAction, ChangeScope, ChangeService, ChangeSignificance, ScopeTarget
from app.changes.service import ChangeFilter
from app.contracts.v1.common import ObservationKind
from app.domain import SharePermission
from app.domain.errors import DomainValidationError
from tests.support import history as h
from tests.support.ingest import replay

FS01 = "FS01"
FINANCE_PATH = "\\\\fs01\\finance"
FINANCE_SHARE = "fs01|finance"
GROUP = f"{h.DOMAIN_SID}-1101"
ALICE = f"{h.DOMAIN_SID}-1104"

FULL_CONTROL = 0x1F01FF
READ_EXECUTE = 0x1200A9

ALL_SIGNIFICANCE = frozenset(ChangeSignificance)


async def ntfs_day(
    client: AsyncClient, moment: dt.datetime, entries: list[tuple[str, int, int]]
) -> None:
    """One NTFS scan of ``\\\\fs01\\finance`` holding exactly ``entries``.

    Each entry is ``(trustee, mask, order_index)``, so a test can move a position without
    touching anything else — which is the only way to isolate the reordering question.
    """
    await replay(
        client,
        h.ntfs_scan(
            observations=[
                h.resource(
                    FINANCE_PATH,
                    at=moment,
                    server_name=FS01,
                    share_name="Finance",
                    ace_count=len(entries),
                ),
                *[
                    h.ntfs_ace(
                        FINANCE_PATH, trustee, at=moment, access_mask=mask, order_index=index
                    )
                    for trustee, mask, index in entries
                ],
            ],
            started_at=moment,
        ),
    )


async def smb_day(client: AsyncClient, moment: dt.datetime, shares: list[str]) -> None:
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


class TestTheyDisagreeAndBothAreRight:
    @pytest.fixture
    async def added_then_removed(self, client: AsyncClient) -> None:
        """An ACE that appeared on Wednesday and was gone again by Friday."""
        await ntfs_day(client, h.MONDAY, [(GROUP, FULL_CONTROL, 0)])
        await ntfs_day(client, h.WEDNESDAY, [(GROUP, FULL_CONTROL, 0), (ALICE, READ_EXECUTE, 1)])
        await ntfs_day(client, h.FRIDAY, [(GROUP, FULL_CONTROL, 0)])

    async def test_the_feed_reports_both_events(
        self, session: AsyncSession, added_then_removed: None
    ) -> None:
        feed = await ChangeService(session).feed(
            ChangeFilter(
                window_from=h.MONDAY,
                window_to=h.NEXT_MONDAY,
                kinds=frozenset({ObservationKind.NTFS_ACE}),
                actions=frozenset(ChangeAction),
                significance=ALL_SIGNIFICANCE,
            )
        )
        about_alice = [c for c in feed.changes if c.subject.related_key == ALICE]
        assert {c.action for c in about_alice} == {ChangeAction.ADDED, ChangeAction.REMOVED}

    async def test_the_comparison_reports_neither(
        self, session: AsyncSession, added_then_removed: None
    ) -> None:
        """At both instants the entry was not there. Nothing is different."""
        comparison = await ChangeService(session).compare(
            h.MONDAY + dt.timedelta(minutes=1),
            h.NEXT_MONDAY,
            kinds=frozenset({ObservationKind.NTFS_ACE}),
            significance=ALL_SIGNIFICANCE,
        )
        assert [c for c in comparison.changes if c.subject.related_key == ALICE] == []

    async def test_the_comparison_counts_what_did_not_move(
        self, session: AsyncSession, added_then_removed: None
    ) -> None:
        comparison = await ChangeService(session).compare(
            h.MONDAY + dt.timedelta(minutes=1),
            h.NEXT_MONDAY,
            kinds=frozenset({ObservationKind.NTFS_ACE}),
            significance=ALL_SIGNIFICANCE,
        )
        assert comparison.unchanged >= 1


class TestTheComparisonNeverCallsAGapAChange:
    async def test_an_object_not_covered_at_the_later_instant_is_counted_not_removed(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The failure this whole product exists to prevent, applied to a comparison.

        ``\\\\fs01\\finance`` was read on Friday and nothing covers the Monday before it, so
        a Monday-to-Friday comparison has no earlier state to compare against. Reporting
        that as a creation would attribute the estate to the day collection started.
        """
        await ntfs_day(client, h.FRIDAY, [(GROUP, FULL_CONTROL, 0)])
        comparison = await ChangeService(session).compare(
            h.MONDAY, h.NEXT_MONDAY, significance=ALL_SIGNIFICANCE
        )
        assert comparison.unobserved_at_from > 0
        assert all(c.action is not ChangeAction.ADDED for c in comparison.changes)

    async def test_an_object_not_covered_at_the_earlier_instant_is_counted_not_added(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await ntfs_day(client, h.MONDAY, [(GROUP, FULL_CONTROL, 0)])
        comparison = await ChangeService(session).compare(
            h.MONDAY - dt.timedelta(days=1), h.NEXT_MONDAY, significance=ALL_SIGNIFICANCE
        )
        assert comparison.unobserved_at_from > 0

    async def test_a_tombstone_at_the_later_instant_is_a_removal(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A measured absence is not a gap. This is the one that *is* a removal."""
        await smb_day(client, h.MONDAY, ["Finance", "HR"])
        await smb_day(client, h.FRIDAY, ["Finance"])
        comparison = await ChangeService(session).compare(
            h.MONDAY + dt.timedelta(minutes=1),
            h.NEXT_MONDAY,
            kinds=frozenset({ObservationKind.SMB_SHARE}),
            significance=ALL_SIGNIFICANCE,
        )
        removed = [c for c in comparison.changes if c.action is ChangeAction.REMOVED]
        assert [c.key for c in removed] == ["fs01|hr"]

    async def test_the_instants_must_be_in_order(self, session: AsyncSession) -> None:
        with pytest.raises(DomainValidationError, match="two distinct instants"):
            await ChangeService(session).compare(h.FRIDAY, h.MONDAY)

    async def test_an_unscoped_comparison_is_bounded(self, session: AsyncSession) -> None:
        with pytest.raises(DomainValidationError, match="estate-wide"):
            await ChangeService(session).compare(h.MONDAY - dt.timedelta(days=3000), h.FRIDAY)

    async def test_a_scoped_comparison_may_span_any_interval(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await smb_day(client, h.MONDAY, ["Finance"])
        await ChangeService(session).compare(
            h.MONDAY - dt.timedelta(days=3000),
            h.FRIDAY,
            scope=ChangeScope(ScopeTarget.SHARE, FINANCE_SHARE),
        )


class TestAReorderingIsOnlyAChangeWhenTheAclMoved:
    async def test_a_renumbering_that_preserves_the_order_is_noise(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Both entries move down two places and nothing about evaluation order changes.

        This is the acceptance criterion: no-op reordered normalized ACL data must not
        create a false security diff.
        """
        await ntfs_day(client, h.MONDAY, [(GROUP, FULL_CONTROL, 2), (ALICE, READ_EXECUTE, 4)])
        await ntfs_day(client, h.WEDNESDAY, [(GROUP, FULL_CONTROL, 2), (ALICE, READ_EXECUTE, 4)])
        await ntfs_day(client, h.FRIDAY, [(GROUP, FULL_CONTROL, 0), (ALICE, READ_EXECUTE, 1)])
        feed = await ChangeService(session).feed(
            ChangeFilter(
                window_from=h.THURSDAY,
                window_to=h.NEXT_MONDAY,
                kinds=frozenset({ObservationKind.NTFS_ACE}),
                actions=frozenset(ChangeAction),
                significance=ALL_SIGNIFICANCE,
            )
        )
        modified = [c for c in feed.changes if c.action is ChangeAction.MODIFIED]
        assert len(modified) == 2
        assert {c.significance for c in modified} == {ChangeSignificance.NOISE}
        assert {c.rule_ids[0] for c in modified} == {"ace.order.immaterial"}

    async def test_the_default_filter_therefore_shows_nothing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await ntfs_day(client, h.MONDAY, [(GROUP, FULL_CONTROL, 2), (ALICE, READ_EXECUTE, 4)])
        await ntfs_day(client, h.FRIDAY, [(GROUP, FULL_CONTROL, 0), (ALICE, READ_EXECUTE, 1)])
        feed = await ChangeService(session).feed(
            ChangeFilter(
                window_from=h.THURSDAY,
                window_to=h.NEXT_MONDAY,
                kinds=frozenset({ObservationKind.NTFS_ACE}),
            )
        )
        assert feed.changes == ()

    async def test_a_genuine_swap_is_a_security_change(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The two entries trade places, so the normalized ACL moves with them.

        Windows evaluates a DACL in order: this is the edit that makes a Deny stop denying.
        """
        await ntfs_day(client, h.MONDAY, [(GROUP, FULL_CONTROL, 0), (ALICE, READ_EXECUTE, 1)])
        await ntfs_day(client, h.WEDNESDAY, [(GROUP, FULL_CONTROL, 0), (ALICE, READ_EXECUTE, 1)])
        await ntfs_day(client, h.FRIDAY, [(GROUP, FULL_CONTROL, 1), (ALICE, READ_EXECUTE, 0)])
        feed = await ChangeService(session).feed(
            ChangeFilter(
                window_from=h.THURSDAY,
                window_to=h.NEXT_MONDAY,
                kinds=frozenset({ObservationKind.NTFS_ACE}),
                actions=frozenset(ChangeAction),
                significance=ALL_SIGNIFICANCE,
            )
        )
        modified = [c for c in feed.changes if c.action is ChangeAction.MODIFIED]
        assert len(modified) == 2
        assert {c.significance for c in modified} == {ChangeSignificance.SECURITY}
        assert {c.rule_ids[0] for c in modified} == {"ace.order.material"}

    async def test_the_same_question_is_answered_on_one_objects_timeline(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The timeline reads through the same correlation, so it cannot disagree with the
        feed about whether a position change mattered."""
        await ntfs_day(client, h.MONDAY, [(GROUP, FULL_CONTROL, 2), (ALICE, READ_EXECUTE, 4)])
        await ntfs_day(client, h.FRIDAY, [(GROUP, FULL_CONTROL, 0), (ALICE, READ_EXECUTE, 1)])
        key = f"{FINANCE_PATH}|{GROUP}|allow|0x{FULL_CONTROL:08x}|0x03"
        timeline = await ChangeService(session).object_changes(ObservationKind.NTFS_ACE, key)
        assert [c.significance for c in timeline.changes] == [ChangeSignificance.NOISE]
