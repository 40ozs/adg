r"""What ingestion writes into the timeline, against a real PostgreSQL.

Three groups, and the middle one carries the phase's most important acceptance criterion:

* **A version opens, extends, and closes** for the right reasons, and only those.
* **A failed, partial, incremental or unreconciled run cannot mark anything absent.** That
  is what keeps a server that was rebooted mid-scan from reading as a server whose shares
  were deleted.
* **A reconciled scope closes exactly what is inside it**, and nothing a different collector
  is responsible for.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1.common import ObservationKind
from app.domain import PrincipalKind, SharePermission
from app.history.model import Certainty, CloseReason, VersionOrigin
from app.history.repository import VersionReader
from app.models.schema import object_versions, smb_shares
from tests.support import history as h
from tests.support.ingest import replay

FS01 = "FS01"
FINANCE = "fs01|finance"
HR = "fs01|hr"
GROUP = f"{h.DOMAIN_SID}-1101"
USER = f"{h.DOMAIN_SID}-1104"


async def versions_of(
    session: AsyncSession, kind: ObservationKind, key: str
) -> list[sa.RowMapping]:
    rows = (
        await session.execute(
            sa.select(object_versions)
            .where(
                object_versions.c.object_kind == kind.value,
                object_versions.c.object_key == key,
            )
            .order_by(object_versions.c.valid_from, object_versions.c.id)
        )
    ).mappings()
    return list(rows)


def finance_share(at: dt.datetime, description: str | None = None) -> list[dict[str, Any]]:
    return [h.server(FS01, at=at), h.share(FS01, "Finance", at=at, description=description)]


class TestAVersionOpensOnce:
    async def test_a_first_observation_opens_an_open_version(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, h.smb_scan(observations=finance_share(h.MONDAY), started_at=h.MONDAY))

        [version] = await versions_of(session, ObservationKind.SMB_SHARE, FINANCE)

        assert version["is_present"]
        assert version["valid_from"] == h.MONDAY
        assert version["last_seen_at"] == h.MONDAY
        assert version["valid_to"] is None
        assert version["origin"] == VersionOrigin.OBSERVED.value
        assert version["container_key"] == "fs01"

    async def test_re_reading_an_unchanged_object_extends_rather_than_versions(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A history that recorded a version per scan would be a scan log."""
        await replay(client, h.smb_scan(observations=finance_share(h.MONDAY), started_at=h.MONDAY))
        await replay(client, h.smb_scan(observations=finance_share(h.FRIDAY), started_at=h.FRIDAY))

        [version] = await versions_of(session, ObservationKind.SMB_SHARE, FINANCE)

        assert version["valid_from"] == h.MONDAY
        assert version["last_seen_at"] == h.FRIDAY

    async def test_a_changed_value_closes_one_version_and_opens_the_next(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(
            client,
            h.smb_scan(observations=finance_share(h.MONDAY, "Finance"), started_at=h.MONDAY),
        )
        await replay(
            client,
            h.smb_scan(observations=finance_share(h.WEDNESDAY, "Finance"), started_at=h.WEDNESDAY),
        )
        await replay(
            client,
            h.smb_scan(observations=finance_share(h.FRIDAY, "Archived"), started_at=h.FRIDAY),
        )

        before, after = await versions_of(session, ObservationKind.SMB_SHARE, FINANCE)

        assert before["valid_from"] == h.MONDAY
        assert before["last_seen_at"] == h.WEDNESDAY
        assert before["valid_to"] == h.FRIDAY
        assert before["close_reason"] == CloseReason.SUPERSEDED.value
        assert before["state"]["description"] == "Finance"
        assert after["valid_from"] == h.FRIDAY
        assert after["valid_to"] is None
        assert after["state"]["description"] == "Archived"

    async def test_the_change_window_is_the_interval_not_the_instant_somebody_looked(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The change happened between Wednesday and Friday; nothing says where."""
        await replay(
            client,
            h.smb_scan(observations=finance_share(h.MONDAY, "Finance"), started_at=h.MONDAY),
        )
        await replay(
            client,
            h.smb_scan(observations=finance_share(h.WEDNESDAY, "Finance"), started_at=h.WEDNESDAY),
        )
        await replay(
            client,
            h.smb_scan(observations=finance_share(h.FRIDAY, "Archived"), started_at=h.FRIDAY),
        )

        timeline = await VersionReader(session).timeline(ObservationKind.SMB_SHARE, FINANCE)
        window = timeline.versions[0].change_window

        assert window is not None
        assert (window.after, window.at_or_before) == (h.WEDNESDAY, h.FRIDAY)
        assert timeline.versions[0].certainty_at(h.WEDNESDAY) is Certainty.OBSERVED
        assert timeline.versions[0].certainty_at(h.THURSDAY) is Certainty.INFERRED


class TestNoRunMayMarkAnythingAbsentWithoutReconciling:
    @pytest.mark.parametrize(
        ("label", "overrides"),
        [
            ("a failed run", {"status": "failed", "reconcile": False}),
            ("a partial run", {"status": "partial", "reconcile": False}),
            ("an incremental run", {"incremental": True, "reconcile": False}),
            ("a successful run that reconciled nothing", {"reconcile": False}),
        ],
    )
    async def test_a_share_a_later_run_never_mentioned_is_not_tombstoned(
        self,
        client: AsyncClient,
        session: AsyncSession,
        label: str,
        overrides: dict[str, Any],
    ) -> None:
        await replay(
            client,
            h.smb_scan(
                observations=[
                    *finance_share(h.MONDAY),
                    h.share(FS01, "HR", at=h.MONDAY),
                ],
                started_at=h.MONDAY,
            ),
        )
        await replay(
            client,
            h.smb_scan(observations=finance_share(h.FRIDAY), started_at=h.FRIDAY, **overrides),
        )

        [version] = await versions_of(session, ObservationKind.SMB_SHARE, HR)

        assert version["is_present"], f"{label} marked HR absent"
        assert version["valid_to"] is None

    async def test_a_run_that_lost_a_batch_is_downgraded_and_reconciles_nothing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The server records `partial` when fewer batches arrived than were claimed."""
        await replay(
            client,
            h.smb_scan(
                observations=[*finance_share(h.MONDAY), h.share(FS01, "HR", at=h.MONDAY)],
                started_at=h.MONDAY,
            ),
        )

        short = h.smb_scan(observations=finance_share(h.FRIDAY), started_at=h.FRIDAY)
        short["completion"]["batch_count"] = 4
        await replay(client, short)

        [version] = await versions_of(session, ObservationKind.SMB_SHARE, HR)
        assert version["is_present"]


class TestAReconciledScopeClosesWhatIsInsideIt:
    async def test_a_share_a_clean_full_scan_did_not_find_is_tombstoned(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(
            client,
            h.smb_scan(
                observations=[*finance_share(h.MONDAY), h.share(FS01, "HR", at=h.MONDAY)],
                started_at=h.MONDAY,
            ),
        )
        await replay(
            client,
            h.smb_scan(
                observations=finance_share(h.FRIDAY),
                started_at=h.FRIDAY,
                completed_at=h.FRIDAY_END,
            ),
        )

        was_present, tombstone = await versions_of(session, ObservationKind.SMB_SHARE, HR)

        assert was_present["valid_to"] == h.FRIDAY_END
        assert was_present["close_reason"] == CloseReason.ABSENT.value
        assert tombstone["is_present"] is False
        assert tombstone["state"] is None
        assert tombstone["valid_from"] == h.FRIDAY_END
        assert tombstone["valid_to"] is None

    async def test_the_current_state_row_is_still_there(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Nothing in ADG deletes a collected fact; absence lives in the timeline."""
        await replay(
            client,
            h.smb_scan(
                observations=[*finance_share(h.MONDAY), h.share(FS01, "HR", at=h.MONDAY)],
                started_at=h.MONDAY,
            ),
        )
        await replay(client, h.smb_scan(observations=finance_share(h.FRIDAY), started_at=h.FRIDAY))

        stored = (
            await session.execute(
                sa.select(smb_shares.c.share_key).where(smb_shares.c.share_key == HR)
            )
        ).scalar_one_or_none()

        assert stored == HR

    async def test_a_share_ace_is_closed_when_the_entry_is_removed(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        both = [
            *finance_share(h.MONDAY),
            h.share_ace(FS01, "Finance", GROUP, at=h.MONDAY, permission=SharePermission.FULL),
            h.share_ace(
                FS01, "Finance", USER, at=h.MONDAY, permission=SharePermission.READ, order_index=1
            ),
        ]
        await replay(client, h.smb_scan(observations=both, started_at=h.MONDAY))
        await replay(
            client,
            h.smb_scan(
                observations=[
                    *finance_share(h.FRIDAY),
                    h.share_ace(
                        FS01, "Finance", GROUP, at=h.FRIDAY, permission=SharePermission.FULL
                    ),
                ],
                started_at=h.FRIDAY,
                completed_at=h.FRIDAY_END,
            ),
        )

        rows = (
            await session.execute(
                sa.select(object_versions.c.object_key, object_versions.c.is_present).where(
                    object_versions.c.object_kind == ObservationKind.SMB_ACE.value,
                    object_versions.c.valid_to.is_(None),
                )
            )
        ).all()
        by_presence = {key: present for key, present in rows}

        assert sum(1 for present in by_presence.values() if not present) == 1
        assert any(USER in key for key, present in by_presence.items() if not present)

    async def test_a_tombstone_is_reaffirmed_by_the_next_scan_that_still_does_not_find_it(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(
            client,
            h.smb_scan(
                observations=[*finance_share(h.MONDAY), h.share(FS01, "HR", at=h.MONDAY)],
                started_at=h.MONDAY,
            ),
        )
        await replay(
            client,
            h.smb_scan(
                observations=finance_share(h.FRIDAY),
                started_at=h.FRIDAY,
                completed_at=h.FRIDAY_END,
            ),
        )
        await replay(
            client,
            h.smb_scan(
                observations=finance_share(h.NEXT_MONDAY),
                started_at=h.NEXT_MONDAY,
                completed_at=h.NEXT_MONDAY_END,
            ),
        )

        _, tombstone = await versions_of(session, ObservationKind.SMB_SHARE, HR)

        assert tombstone["valid_from"] == h.FRIDAY_END
        assert tombstone["last_seen_at"] == h.NEXT_MONDAY_END

    async def test_an_object_that_comes_back_closes_its_tombstone(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(
            client,
            h.smb_scan(
                observations=[*finance_share(h.MONDAY), h.share(FS01, "HR", at=h.MONDAY)],
                started_at=h.MONDAY,
            ),
        )
        await replay(
            client,
            h.smb_scan(
                observations=finance_share(h.WEDNESDAY),
                started_at=h.WEDNESDAY,
                completed_at=h.WEDNESDAY_END,
            ),
        )
        await replay(
            client,
            h.smb_scan(
                observations=[*finance_share(h.FRIDAY), h.share(FS01, "HR", at=h.FRIDAY)],
                started_at=h.FRIDAY,
            ),
        )

        first, tombstone, revived = await versions_of(session, ObservationKind.SMB_SHARE, HR)

        assert first["close_reason"] == CloseReason.ABSENT.value
        assert tombstone["is_present"] is False
        assert tombstone["valid_to"] == h.FRIDAY
        assert tombstone["close_reason"] == CloseReason.SUPERSEDED.value
        assert revived["is_present"] is True
        assert revived["valid_from"] == h.FRIDAY


class TestOneCollectorDoesNotEraseAnothersFacts:
    async def test_an_smb_run_reconciling_a_server_leaves_the_file_system_alone(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The SMB collector never opens a directory, so it may not report one as gone."""
        path = "\\\\fs01\\finance"
        await replay(
            client,
            h.ntfs_scan(
                observations=[
                    h.resource(
                        path, at=h.MONDAY, server_name=FS01, share_name="Finance", ace_count=1
                    ),
                    h.ntfs_ace(path, GROUP, at=h.MONDAY, access_mask=0x1F01FF),
                ],
                started_at=h.MONDAY,
            ),
        )
        await replay(client, h.smb_scan(observations=finance_share(h.FRIDAY), started_at=h.FRIDAY))

        [version] = await versions_of(session, ObservationKind.NTFS_RESOURCE, path)
        assert version["is_present"]

    async def test_a_domain_run_leaves_a_machines_local_groups_alone(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """S-1-5-32-544 means a different group on every machine."""
        local = "S-1-5-32-544"
        await replay(
            client,
            h.scan(
                collector="local_groups",
                host=FS01,
                method="Win32_GroupUser",
                target=FS01,
                scopes=[("local_groups_host", "fs01")],
                observations=[
                    h.principal(
                        local,
                        at=h.MONDAY,
                        kind=PrincipalKind.LOCAL_GROUP,
                        host_key="fs01",
                        display_name="Administrators",
                    )
                ],
                started_at=h.MONDAY,
            ),
        )
        await replay(
            client,
            h.ad_scan(
                observations=[h.principal(GROUP, at=h.FRIDAY, display_name="Finance-RW")],
                started_at=h.FRIDAY,
            ),
        )

        [version] = await versions_of(session, ObservationKind.PRINCIPAL, f"fs01|{local}")
        assert version["is_present"]

    async def test_a_domain_run_does_close_a_domain_group_it_no_longer_finds(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        retired = f"{h.DOMAIN_SID}-1199"
        await replay(
            client,
            h.ad_scan(
                observations=[
                    h.principal(GROUP, at=h.MONDAY, display_name="Finance-RW"),
                    h.principal(retired, at=h.MONDAY, display_name="Old-Project"),
                ],
                started_at=h.MONDAY,
            ),
        )
        await replay(
            client,
            h.ad_scan(
                observations=[h.principal(GROUP, at=h.FRIDAY, display_name="Finance-RW")],
                started_at=h.FRIDAY,
                completed_at=h.FRIDAY_END,
            ),
        )

        _, tombstone = await versions_of(session, ObservationKind.PRINCIPAL, retired)
        assert tombstone["is_present"] is False

        [kept] = await versions_of(session, ObservationKind.PRINCIPAL, GROUP)
        assert kept["is_present"]


class TestOutOfOrderArrival:
    async def test_a_delayed_older_run_does_not_rewrite_a_newer_state(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(
            client,
            h.smb_scan(
                observations=finance_share(h.FRIDAY, "Archived"),
                started_at=h.FRIDAY,
                reconcile=False,
            ),
        )
        await replay(
            client,
            h.smb_scan(
                observations=finance_share(h.MONDAY, "Finance"),
                started_at=h.MONDAY,
                reconcile=False,
            ),
        )

        versions = await versions_of(session, ObservationKind.SMB_SHARE, FINANCE)

        assert len(versions) == 1
        assert versions[0]["state"]["description"] == "Archived"
        assert versions[0]["valid_from"] == h.FRIDAY

    async def test_a_delayed_older_run_that_agrees_extends_the_version_backwards(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Seeing the same state earlier than anything before it is new knowledge."""
        await replay(
            client,
            h.smb_scan(
                observations=finance_share(h.FRIDAY, "Finance"),
                started_at=h.FRIDAY,
                reconcile=False,
            ),
        )
        await replay(
            client,
            h.smb_scan(
                observations=finance_share(h.MONDAY, "Finance"),
                started_at=h.MONDAY,
                reconcile=False,
            ),
        )

        [version] = await versions_of(session, ObservationKind.SMB_SHARE, FINANCE)

        assert version["valid_from"] == h.MONDAY
        assert version["last_seen_at"] == h.FRIDAY

    async def test_a_stale_run_may_not_tombstone_what_a_newer_one_just_found(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(
            client,
            h.smb_scan(
                observations=[*finance_share(h.FRIDAY), h.share(FS01, "HR", at=h.FRIDAY)],
                started_at=h.FRIDAY,
                reconcile=False,
            ),
        )
        await replay(
            client,
            h.smb_scan(
                observations=finance_share(h.MONDAY),
                started_at=h.MONDAY,
                completed_at=h.WEDNESDAY,
            ),
        )

        [version] = await versions_of(session, ObservationKind.SMB_SHARE, HR)
        assert version["is_present"]


class TestTheDatabaseEnforcesTheInvariants:
    async def test_two_open_versions_of_one_object_are_impossible(
        self, session: AsyncSession
    ) -> None:
        row = {
            "object_kind": ObservationKind.SERVER.value,
            "object_key": "fs99",
            "is_present": True,
            "state": {"server_key": "fs99"},
            "state_hash": "a" * 64,
            "origin": VersionOrigin.OBSERVED.value,
            "valid_from": h.MONDAY,
            "last_seen_at": h.MONDAY,
            "opened_by_run_id": "11111111-1111-1111-1111-111111111111",
            "last_seen_run_id": "11111111-1111-1111-1111-111111111111",
            "created_at": h.MONDAY,
            "updated_at": h.MONDAY,
        }
        await session.execute(sa.insert(object_versions).values(row))

        with pytest.raises(sa.exc.IntegrityError, match="ux_object_versions_open"):
            await session.execute(
                sa.insert(object_versions).values(
                    {**row, "valid_from": h.FRIDAY, "last_seen_at": h.FRIDAY}
                )
            )
        await session.rollback()

    async def test_a_closed_version_without_a_reason_is_refused(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(sa.exc.IntegrityError, match="closed_has_a_reason"):
            await session.execute(
                sa.insert(object_versions).values(
                    {
                        "object_kind": ObservationKind.SERVER.value,
                        "object_key": "fs98",
                        "is_present": True,
                        "state": {"server_key": "fs98"},
                        "state_hash": "a" * 64,
                        "origin": VersionOrigin.OBSERVED.value,
                        "valid_from": h.MONDAY,
                        "last_seen_at": h.MONDAY,
                        "valid_to": h.FRIDAY,
                        "close_reason": None,
                        "opened_by_run_id": "11111111-1111-1111-1111-111111111111",
                        "last_seen_run_id": "11111111-1111-1111-1111-111111111111",
                        "closed_by_run_id": "11111111-1111-1111-1111-111111111111",
                        "created_at": h.MONDAY,
                        "updated_at": h.MONDAY,
                    }
                )
            )
        await session.rollback()
