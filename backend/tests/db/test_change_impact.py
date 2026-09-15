r"""Why access changed — and the case where it did not, which is the interesting one.

The feed says an ACL broadened. Whether anybody can now do something they could not do
before is a different question, and this suite is mostly about the ways those two answers
come apart:

* a share ACL that still caps what the NTFS ACL grants;
* a Deny that still wins;
* a membership edit that touches no ACL at all and changes access to everything the group
  reaches.

Every answer here is computed by :class:`app.services.AccessService` — the live engine —
reading through Phase 7A's as-of repositories. There is no historical access algorithm, so a
disagreement between "what the Changes page says" and "what the Access page says" is not
possible by construction rather than by two implementations being kept in step.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.changes import ChangeAction, ChangeDirection, ChangeImpactService, ImpactVerdict
from app.contracts.v1.common import ObservationKind
from app.domain import AceType, PrincipalKind, SharePermission
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


def ace_key(mask: int, trustee: str = GROUP, ace_type: str = "allow") -> str:
    return f"{FINANCE_PATH}|{trustee}|{ace_type}|0x{mask:08x}|0x03"


async def estate(
    client: AsyncClient,
    moment: dt.datetime,
    *,
    ntfs: list[tuple[str, int, AceType]],
    share_level: SharePermission,
    members: list[str],
) -> None:
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
                h.share(FS01, "Finance", at=moment),
                h.share_ace(FS01, "Finance", GROUP, at=moment, permission=share_level),
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
                    ace_count=len(ntfs),
                ),
                *[
                    h.ntfs_ace(
                        FINANCE_PATH,
                        trustee,
                        at=moment,
                        access_mask=mask,
                        ace_type=ace_type,
                        order_index=index,
                    )
                    for index, (trustee, mask, ace_type) in enumerate(ntfs)
                ],
            ],
            started_at=moment,
        ),
    )


class TestAnAclEditThatMovedEffectiveAccess:
    @pytest.fixture
    async def tightened(self, client: AsyncClient) -> None:
        """Full Control to Read & Execute on the file system, with a full share ACL."""
        for moment in (h.MONDAY, h.WEDNESDAY):
            await estate(
                client,
                moment,
                ntfs=[(GROUP, FULL_CONTROL, AceType.ALLOW)],
                share_level=SharePermission.FULL,
                members=[ALICE],
            )
        await estate(
            client,
            h.FRIDAY,
            ntfs=[(GROUP, READ_EXECUTE, AceType.ALLOW)],
            share_level=SharePermission.FULL,
            members=[ALICE],
        )

    async def test_the_engine_resolves_both_sides_and_reports_the_narrowing(
        self, session: AsyncSession, tightened: None
    ) -> None:
        impact = await ChangeImpactService(session).impact_of(
            ObservationKind.NTFS_ACE, ace_key(READ_EXECUTE), h.FRIDAY
        )
        assert impact.verdict is ImpactVerdict.RESOLVED
        assert impact.access is not None
        assert impact.access.direction is ChangeDirection.NARROWED
        assert impact.access.lost.value
        assert not impact.access.gained.value

    async def test_it_names_the_pair_the_change_itself_implicates(
        self, session: AsyncSession, tightened: None
    ) -> None:
        """An ACE names its trustee and its directory, so nothing has to be supplied."""
        impact = await ChangeImpactService(session).impact_of(
            ObservationKind.NTFS_ACE, ace_key(READ_EXECUTE), h.FRIDAY
        )
        assert impact.access is not None
        assert impact.access.subject_key == GROUP
        assert impact.access.resource_key == FINANCE_PATH

    async def test_a_supplied_subject_is_resolved_instead(
        self, session: AsyncSession, tightened: None
    ) -> None:
        """The operator's real question is usually about a person, not about the trustee."""
        impact = await ChangeImpactService(session).impact_of(
            ObservationKind.NTFS_ACE, ace_key(READ_EXECUTE), h.FRIDAY, subject_key=ALICE
        )
        assert impact.access is not None
        assert impact.access.subject_key == ALICE
        assert impact.access.direction is ChangeDirection.NARROWED

    async def test_the_earlier_answer_is_taken_at_a_real_observation(
        self, session: AsyncSession, tightened: None
    ) -> None:
        """Wednesday, the last confirmation of the old ACL — not an invented instant.

        The entry that was *removed* in this edit carries that confirmation. The entry that
        was added has no predecessor of its own, because an ACE's rights are part of its
        identity and a tightening creates a different object; taking the added entry's own
        instant would make "before" mean "the moment this row appeared".
        """
        impact = await ChangeImpactService(session).impact_of(
            ObservationKind.NTFS_ACE, ace_key(READ_EXECUTE), h.FRIDAY
        )
        assert impact.at_before == h.WEDNESDAY

    async def test_the_later_answer_is_taken_after_the_run_finished(
        self, session: AsyncSession, tightened: None
    ) -> None:
        """Not at the change's own instant, and this is the subtle one.

        A scan opens versions at the instant each object was *observed* and closes what it
        did not find at the instant the run *completed*. Between the two, the directory
        carries both the old entry and the new one — a state that never existed. Resolving
        there would report this tightening as "nothing changed", confidently, because Full
        Control is still open at the instant Read & Execute appeared.
        """
        impact = await ChangeImpactService(session).impact_of(
            ObservationKind.NTFS_ACE, ace_key(READ_EXECUTE), h.FRIDAY
        )
        assert impact.at_after == h.FRIDAY_END
        assert impact.at_after > h.FRIDAY

    async def test_resolving_inside_the_run_would_have_reported_no_change(
        self, session: AsyncSession, tightened: None
    ) -> None:
        """The defect the rule above prevents, asserted directly so it stays prevented.

        Asked at the instant the new entry appeared, the engine sees both entries and
        answers Full Control — identical to the answer before the edit.
        """
        from app.history.service import HistoryService

        history = HistoryService(session)
        at_change = await history.effective_access_at(GROUP, FINANCE_PATH, h.FRIDAY)
        at_completion = await history.effective_access_at(GROUP, FINANCE_PATH, h.FRIDAY_END)
        assert at_change.access.access.rights.value != at_completion.access.access.rights.value


class TestAnAclEditThatMovedNothing:
    @pytest.fixture
    async def loosened_under_a_capped_share(self, client: AsyncClient) -> None:
        """The NTFS ACL is loosened to Full Control while the share ACL still says Read.

        The two layers cross and the tighter one wins, so the ACL broadened and effective
        access did not move. An interface that reported the ACL change alone would have an
        operator chasing an exposure that does not exist.
        """
        for moment in (h.MONDAY, h.WEDNESDAY):
            await estate(
                client,
                moment,
                ntfs=[(GROUP, READ_EXECUTE, AceType.ALLOW)],
                share_level=SharePermission.READ,
                members=[ALICE],
            )
        await estate(
            client,
            h.FRIDAY,
            ntfs=[(GROUP, FULL_CONTROL, AceType.ALLOW)],
            share_level=SharePermission.READ,
            members=[ALICE],
        )

    async def test_the_change_broadened_and_effective_access_did_not(
        self, session: AsyncSession, loosened_under_a_capped_share: None
    ) -> None:
        impact = await ChangeImpactService(session).impact_of(
            ObservationKind.NTFS_ACE, ace_key(FULL_CONTROL), h.FRIDAY
        )
        assert impact.change.action is ChangeAction.ADDED
        assert impact.change.direction is ChangeDirection.BROADENED
        assert impact.access is not None
        assert impact.access.direction is ChangeDirection.NEUTRAL

    async def test_the_explanation_says_why_rather_than_showing_two_equal_masks(
        self, session: AsyncSession, loosened_under_a_capped_share: None
    ) -> None:
        """Two identical masks with no sentence leaves the reader unsure whether that is
        the answer or a bug."""
        impact = await ChangeImpactService(session).impact_of(
            ObservationKind.NTFS_ACE, ace_key(FULL_CONTROL), h.FRIDAY
        )
        assert "did not move" in impact.explanation

    async def test_a_deny_that_still_wins_produces_the_same_reading(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        for moment in (h.MONDAY, h.WEDNESDAY):
            await estate(
                client,
                moment,
                ntfs=[
                    (GROUP, FULL_CONTROL, AceType.DENY),
                    (GROUP, READ_EXECUTE, AceType.ALLOW),
                ],
                share_level=SharePermission.FULL,
                members=[ALICE],
            )
        await estate(
            client,
            h.FRIDAY,
            ntfs=[
                (GROUP, FULL_CONTROL, AceType.DENY),
                (GROUP, FULL_CONTROL, AceType.ALLOW),
            ],
            share_level=SharePermission.FULL,
            members=[ALICE],
        )
        impact = await ChangeImpactService(session).impact_of(
            ObservationKind.NTFS_ACE, ace_key(FULL_CONTROL), h.FRIDAY
        )
        assert impact.change.direction is ChangeDirection.BROADENED
        assert impact.access is not None
        assert impact.access.direction is ChangeDirection.NEUTRAL


class TestAMembershipEditNamesNoResource:
    @pytest.fixture
    async def alice_left(self, client: AsyncClient) -> None:
        for moment in (h.MONDAY, h.WEDNESDAY):
            await estate(
                client,
                moment,
                ntfs=[(GROUP, FULL_CONTROL, AceType.ALLOW)],
                share_level=SharePermission.FULL,
                members=[ALICE],
            )
        await estate(
            client,
            h.FRIDAY,
            ntfs=[(GROUP, FULL_CONTROL, AceType.ALLOW)],
            share_level=SharePermission.FULL,
            members=[],
        )

    def _edge_key(self) -> str:
        return f"{GROUP}->{ALICE}|directory_group_member"

    async def _removal_instant(self, session: AsyncSession) -> dt.datetime:
        from app.changes import ChangeService

        timeline = await ChangeService(session).object_changes(
            ObservationKind.MEMBERSHIP_EDGE, self._edge_key()
        )
        return timeline.changes[0].at

    async def test_it_reports_the_groups_the_principal_stopped_reaching(
        self, session: AsyncSession, alice_left: None
    ) -> None:
        """The honest partial answer: no resource was named, and this much is computable."""
        at = await self._removal_instant(session)
        impact = await ChangeImpactService(session).impact_of(
            ObservationKind.MEMBERSHIP_EDGE, self._edge_key(), at
        )
        assert impact.verdict is ImpactVerdict.UNBOUNDED
        assert impact.membership is not None
        assert GROUP in impact.membership.lost
        assert impact.membership.gained == ()

    async def test_naming_a_resource_resolves_the_rest(
        self, session: AsyncSession, alice_left: None
    ) -> None:
        at = await self._removal_instant(session)
        impact = await ChangeImpactService(session).impact_of(
            ObservationKind.MEMBERSHIP_EDGE,
            self._edge_key(),
            at,
            resource_key=FINANCE_PATH,
        )
        assert impact.verdict is ImpactVerdict.RESOLVED
        assert impact.access is not None
        assert impact.access.subject_key == ALICE
        assert impact.access.direction is ChangeDirection.NARROWED
        assert impact.access.lost.value


class TestWhatItRefusesToGuess:
    async def test_a_directory_change_asks_for_a_principal(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Effective access is always about somebody. Picking a plausible principal would
        be a confident answer to a question nobody asked."""
        for moment in (h.MONDAY, h.WEDNESDAY):
            await estate(
                client,
                moment,
                ntfs=[(GROUP, FULL_CONTROL, AceType.ALLOW)],
                share_level=SharePermission.FULL,
                members=[ALICE],
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
                        owner_sid=ALICE,
                    ),
                    h.ntfs_ace(
                        FINANCE_PATH, GROUP, at=h.FRIDAY, access_mask=FULL_CONTROL, order_index=0
                    ),
                ],
                started_at=h.FRIDAY,
            ),
        )
        impact = await ChangeImpactService(session).impact_of(
            ObservationKind.NTFS_RESOURCE, FINANCE_PATH, h.FRIDAY
        )
        assert impact.verdict is ImpactVerdict.NEEDS_A_SUBJECT
        assert "principal" in impact.explanation

    async def test_an_instant_that_names_no_change_is_refused(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await estate(
            client,
            h.MONDAY,
            ntfs=[(GROUP, FULL_CONTROL, AceType.ALLOW)],
            share_level=SharePermission.FULL,
            members=[ALICE],
        )
        with pytest.raises(DomainValidationError, match="No version of"):
            await ChangeImpactService(session).impact_of(
                ObservationKind.NTFS_ACE, ace_key(FULL_CONTROL), h.FRIDAY
            )
