r"""Reconstructing a past instant: membership, raw ACLs, existence, effective access.

The estate is three days long and deliberately small, because what is under test is *when*,
not how much:

* **Monday** — ``Finance-RW`` holds Full Control on ``\\fs01\finance``; Alice is in it.
* **Wednesday** — nothing changed, and the scan says so. This is what makes Monday's answer
  ``observed`` rather than merely uncontradicted.
* **Friday** — Alice is removed from the group, the directory's ACL is tightened to
  Read & Execute, and the ``HR`` share is deleted.

Every assertion below is of the form "the answer about Monday is still Monday's answer,
after Friday happened". An audit tool that cannot do that cannot answer the only question an
auditor actually asks after an incident.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1.common import ObservationKind
from app.domain import PrincipalKind, SharePermission
from app.history.model import Certainty, VersionOrigin
from app.history.service import HistoryService
from app.repositories import MembershipRepository, ResourceRepository
from app.services.access import AccessService
from tests.support import history as h
from tests.support.ingest import replay

FS01 = "FS01"
FINANCE_PATH = "\\\\fs01\\finance"
FINANCE_SHARE = "fs01|finance"
HR_SHARE = "fs01|hr"
GROUP = f"{h.DOMAIN_SID}-1101"
ALICE = f"{h.DOMAIN_SID}-1104"

FULL_CONTROL = 0x1F01FF
READ_AND_EXECUTE = 0x1200A9


@pytest.fixture
async def three_days(client: AsyncClient) -> None:
    """Monday, Wednesday and Friday, posted through the real ingestion endpoints."""
    for moment in (h.MONDAY, h.WEDNESDAY):
        await replay(
            client,
            h.ad_scan(
                observations=[
                    h.principal(GROUP, at=moment, display_name="Finance-RW"),
                    h.principal(
                        ALICE,
                        at=moment,
                        kind=PrincipalKind.USER,
                        display_name="Alice",
                        enabled=True,
                    ),
                    h.edge(GROUP, ALICE, at=moment),
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
                    h.share(FS01, "HR", at=moment),
                    h.share_ace(FS01, "Finance", GROUP, at=moment, permission=SharePermission.FULL),
                    h.share_ace(FS01, "HR", GROUP, at=moment, permission=SharePermission.FULL),
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
                        ace_count=1,
                    ),
                    h.ntfs_ace(FINANCE_PATH, GROUP, at=moment, access_mask=FULL_CONTROL),
                ],
                started_at=moment,
            ),
        )

    # Friday: Alice leaves the group, the DACL is tightened, and HR is gone.
    await replay(
        client,
        h.ad_scan(
            observations=[
                h.principal(GROUP, at=h.FRIDAY, display_name="Finance-RW"),
                h.principal(
                    ALICE, at=h.FRIDAY, kind=PrincipalKind.USER, display_name="Alice", enabled=True
                ),
            ],
            started_at=h.FRIDAY,
            completed_at=h.FRIDAY_END,
        ),
    )
    await replay(
        client,
        h.smb_scan(
            observations=[
                h.server(FS01, at=h.FRIDAY),
                h.share(FS01, "Finance", at=h.FRIDAY),
                h.share_ace(FS01, "Finance", GROUP, at=h.FRIDAY, permission=SharePermission.FULL),
            ],
            started_at=h.FRIDAY,
            completed_at=h.FRIDAY_END,
        ),
    )
    await replay(
        client,
        h.ntfs_scan(
            observations=[
                h.resource(
                    FINANCE_PATH, at=h.FRIDAY, server_name=FS01, share_name="Finance", ace_count=1
                ),
                h.ntfs_ace(FINANCE_PATH, GROUP, at=h.FRIDAY, access_mask=READ_AND_EXECUTE),
            ],
            started_at=h.FRIDAY,
            completed_at=h.FRIDAY_END,
        ),
    )


@pytest.mark.usefixtures("three_days")
class TestPointInTimeMembership:
    async def test_the_group_had_alice_in_it_on_monday(self, session: AsyncSession) -> None:
        answer = await HistoryService(session).direct_members_at(GROUP, h.MONDAY)

        assert [edge.member_key for edge in answer.edges] == [ALICE]
        assert answer.principals[ALICE].display_name == "Alice"

    async def test_it_does_not_after_friday(self, session: AsyncSession) -> None:
        answer = await HistoryService(session).direct_members_at(GROUP, h.NEXT_MONDAY)

        assert answer.edges == ()

    async def test_the_reverse_direction_answers_the_same_instant(
        self, session: AsyncSession
    ) -> None:
        service = HistoryService(session)

        assert [
            edge.group_key for edge in (await service.direct_groups_at(ALICE, h.MONDAY)).edges
        ] == [GROUP]
        assert (await service.direct_groups_at(ALICE, h.NEXT_MONDAY)).edges == ()

    async def test_an_answer_between_two_confirmations_is_observed(
        self, session: AsyncSession
    ) -> None:
        answer = await HistoryService(session).direct_members_at(GROUP, h.WEDNESDAY)

        assert answer.certainty is Certainty.OBSERVED

    async def test_an_answer_after_the_last_confirmation_is_inferred(
        self, session: AsyncSession
    ) -> None:
        """Thursday lies in the window the edge was believed to hold and nobody watched.

        Wednesday's scan confirmed it; Friday's found it gone. The edge is the best answer
        for Thursday and it was not observed then, which is exactly what ``inferred`` says.
        """
        answer = await HistoryService(session).direct_members_at(GROUP, h.THURSDAY)

        assert [edge.member_key for edge in answer.edges] == [ALICE]
        assert answer.certainty is Certainty.INFERRED

    async def test_the_effective_group_expansion_runs_over_historical_edges(
        self, session: AsyncSession
    ) -> None:
        service = HistoryService(session)

        monday, _ = await service.effective_groups_at(ALICE, h.MONDAY)
        after, _ = await service.effective_groups_at(ALICE, h.NEXT_MONDAY)

        assert GROUP in monday
        assert GROUP not in after


@pytest.mark.usefixtures("three_days")
class TestPointInTimeRawAcls:
    async def test_the_directory_dacl_is_reconstructed_as_it_stood(
        self, session: AsyncSession
    ) -> None:
        service = HistoryService(session)

        monday = await service.resource_acl_at(FINANCE_PATH, h.MONDAY)
        after = await service.resource_acl_at(FINANCE_PATH, h.NEXT_MONDAY)

        assert [entry.access_mask for entry in monday.entries] == [FULL_CONTROL]
        assert [entry.access_mask for entry in after.entries] == [READ_AND_EXECUTE]

    async def test_the_descriptor_facts_come_from_the_same_instant(
        self, session: AsyncSession
    ) -> None:
        monday = await HistoryService(session).resource_acl_at(FINANCE_PATH, h.MONDAY)

        assert monday.resource is not None
        assert monday.resource.is_acl_boundary
        assert monday.presence.exists is True
        assert monday.certainty is Certainty.OBSERVED

    async def test_the_share_acl_is_reconstructed_for_a_share_that_has_since_been_deleted(
        self, session: AsyncSession
    ) -> None:
        monday = await HistoryService(session).share_acl_at(HR_SHARE, h.MONDAY)

        assert monday.observed
        assert [entry.trustee_key for entry in monday.entries] == [GROUP]

    async def test_a_deleted_share_reports_no_acl_and_says_it_was_absent(
        self, session: AsyncSession
    ) -> None:
        after = await HistoryService(session).share_acl_at(HR_SHARE, h.NEXT_MONDAY)

        assert after.entries == ()
        assert after.observed is False
        assert after.presence.exists is False


@pytest.mark.usefixtures("three_days")
class TestExistence:
    async def test_a_share_that_existed_is_reported_present(self, session: AsyncSession) -> None:
        answer = await HistoryService(session).presence_at(
            ObservationKind.SMB_SHARE, HR_SHARE, h.MONDAY
        )

        assert answer.exists is True

    async def test_a_deleted_share_is_reported_absent_with_the_instant_it_was_found_gone(
        self, session: AsyncSession
    ) -> None:
        answer = await HistoryService(session).presence_at(
            ObservationKind.SMB_SHARE, HR_SHARE, h.NEXT_MONDAY
        )

        assert answer.exists is False
        assert answer.absent_since == h.FRIDAY_END

    async def test_an_instant_before_anything_was_collected_is_unobserved_not_absent(
        self, session: AsyncSession
    ) -> None:
        """The whole product turns on this distinction."""
        answer = await HistoryService(session).presence_at(
            ObservationKind.SMB_SHARE, HR_SHARE, h.MONDAY.replace(year=2020)
        )

        assert answer.exists is None
        assert answer.is_unobserved
        assert answer.certainty is Certainty.UNOBSERVED

    async def test_a_resource_that_was_never_collected_at_all_is_unobserved(
        self, session: AsyncSession
    ) -> None:
        answer = await HistoryService(session).resource_exists_at(
            "\\\\fs01\\finance\\never-scanned", h.MONDAY
        )

        assert answer.exists is None


@pytest.mark.usefixtures("three_days")
class TestPointInTimeEffectiveAccess:
    async def test_alice_could_write_on_monday_and_could_not_by_the_next_week(
        self, session: AsyncSession
    ) -> None:
        service = HistoryService(session)

        monday = await service.effective_access_at(ALICE, FINANCE_PATH, h.MONDAY)
        after = await service.effective_access_at(ALICE, FINANCE_PATH, h.NEXT_MONDAY)

        assert monday.access.access.has_access
        assert monday.access.access.rights.value == FULL_CONTROL
        assert not after.access.access.has_access

    async def test_the_answer_reports_the_certainty_of_the_facts_it_rests_on(
        self, session: AsyncSession
    ) -> None:
        answer = await HistoryService(session).effective_access_at(ALICE, FINANCE_PATH, h.WEDNESDAY)

        assert answer.inputs.total > 0
        assert answer.certainty is Certainty.OBSERVED
        assert not answer.rests_on_reconstructed_state

    async def test_the_live_answer_still_counts_what_a_reconciled_scan_proved_is_gone(
        self, session: AsyncSession
    ) -> None:
        """A real limitation of this phase, pinned rather than left to be discovered.

        Nothing in ADG deletes a collected fact, so ``membership_edges`` still holds the
        edge that put Alice in ``Finance-RW`` and ``ntfs_aces`` still holds the Full Control
        entry -- an ACE's identity includes its mask, so tightening the DACL *added* a row
        rather than changing one. Phase 7A records both removals in the timeline and does
        not change what the current-state repositories read, so the **live** answer
        overstates Alice's access and the **as-of-now** answer does not.

        This is the top prerequisite for Phase 7B: routing current-state reads through the
        open version's presence. It is asserted here so that the day it is fixed, this test
        fails and says what changed.
        """
        live = await AccessService(
            ResourceRepository(session), MembershipRepository(session)
        ).effective_access(ALICE, FINANCE_PATH)
        as_of_now = await HistoryService(session).effective_access_at(
            ALICE, FINANCE_PATH, h.NEXT_MONDAY
        )

        assert live.access.has_access
        assert live.access.rights.value == FULL_CONTROL
        assert not as_of_now.access.access.has_access

    async def test_an_instant_before_collection_began_grants_nothing_and_says_why(
        self, session: AsyncSession
    ) -> None:
        answer = await HistoryService(session).effective_access_at(
            ALICE, FINANCE_PATH, h.MONDAY.replace(year=2020)
        )

        assert not answer.access.access.has_access
        assert answer.certainty is Certainty.UNOBSERVED


@pytest.mark.usefixtures("three_days")
class TestTimelines:
    async def test_a_directorys_acl_change_is_two_versions_with_a_window_between_them(
        self, session: AsyncSession
    ) -> None:
        service = HistoryService(session)
        acl_at = await service.resource_acl_at(FINANCE_PATH, h.MONDAY)
        ace_key = acl_at.entries[0].ace_key

        timeline = await service.timeline(ObservationKind.NTFS_ACE, ace_key)

        assert len(timeline) == 2
        assert timeline.versions[0].is_present
        assert timeline.versions[1].is_tombstone
        window = timeline.versions[0].change_window
        assert window is not None and window.after == h.WEDNESDAY

    async def test_a_deleted_share_shows_as_a_removal(self, session: AsyncSession) -> None:
        removals = await HistoryService(session).absent_now(ObservationKind.SMB_SHARE)

        assert [version.key for version in removals] == [HR_SHARE]
        assert removals[0].valid_from == h.FRIDAY_END

    async def test_every_version_written_by_ingestion_is_marked_observed(
        self, session: AsyncSession
    ) -> None:
        timeline = await HistoryService(session).timeline(ObservationKind.SMB_SHARE, FINANCE_SHARE)

        assert all(version.origin is VersionOrigin.OBSERVED for version in timeline)


def as_dict(record: Any) -> dict[str, Any]:
    return {name: getattr(record, name) for name in record.__dataclass_fields__}
