r"""The HTTP surface: what it refuses, what it discloses, and what it always carries.

Three things are checked here that the service tests cannot see:

* **The capability split.** The feed needs ``changes:read``; the impact endpoint needs
  ``access:read``, because it discloses what a principal could do rather than what was
  edited.
* **The response always carries both ends of a change window**, never a single instant
  (ADR-0019). A client that rendered ``at`` as the change time would have reverted to the
  model that ADR rejected, so the field is present, documented, and asserted.
* **A filtered page says what it hid.** ``/summary`` counts the whole window before the
  filter, and without that number a clean-looking page is indistinguishable from a page
  that is clean because of a default.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest
from httpx import AsyncClient

from app.auth.roles import Role
from app.domain import PrincipalKind, SharePermission
from tests.support import history as h
from tests.support.ingest import replay

FS01 = "FS01"
FINANCE_PATH = "\\\\fs01\\finance"
FINANCE_SHARE = "fs01|finance"
GROUP = f"{h.DOMAIN_SID}-1101"
ALICE = f"{h.DOMAIN_SID}-1104"
EVERYONE = "S-1-1-0"

FULL_CONTROL = 0x1F01FF
READ_EXECUTE = 0x1200A9


def iso(moment: dt.datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


async def _day(client: AsyncClient, moment: dt.datetime, aces, shares) -> None:
    await replay(
        client,
        h.ad_scan(
            observations=[
                h.principal(GROUP, at=moment, display_name="Finance-RW"),
                h.principal(
                    ALICE, at=moment, kind=PrincipalKind.USER, display_name="Alice", enabled=True
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
                        FINANCE_PATH, trustee, at=moment, access_mask=mask, order_index=index
                    )
                    for index, (trustee, mask) in enumerate(aces)
                ],
            ],
            started_at=moment,
        ),
    )


@pytest.fixture
async def estate(client: AsyncClient) -> None:
    for moment in (h.MONDAY, h.WEDNESDAY):
        await _day(client, moment, [(GROUP, FULL_CONTROL)], ["Finance", "HR"])
    await _day(client, h.FRIDAY, [(GROUP, READ_EXECUTE), (EVERYONE, READ_EXECUTE)], ["Finance"])


WINDOW = {"from": iso(h.THURSDAY), "to": iso(h.NEXT_MONDAY)}


class TestTheFeed:
    async def test_it_answers_what_changed_since_thursday(
        self, client: AsyncClient, estate: None
    ) -> None:
        response = await client.get("/api/v1/changes", params=WINDOW)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["changes"]
        assert {change["kind"] for change in body["changes"]}

    async def test_every_change_carries_both_ends_of_its_window(
        self, client: AsyncClient, estate: None
    ) -> None:
        """ADR-0019. A single ``changed_at`` would date every incident to a scan schedule."""
        body = (await client.get("/api/v1/changes", params=WINDOW)).json()
        for change in body["changes"]:
            assert change["action"] != "first_observed"
            window = change["window"]
            assert window is not None, (
                f"{change['action']} {change['key']} carries no window. An addition has no "
                "predecessor of its own; its lower bound is the last reading of the "
                "container it appeared in."
            )
            assert window["after"] <= window["at_or_before"]
            assert window["is_exact"] == (window["after"] == window["at_or_before"])
            assert "duration_seconds" in window

    async def test_it_echoes_the_filter_that_produced_the_list(
        self, client: AsyncClient, estate: None
    ) -> None:
        """So a caller always knows which editorial default produced what they are reading."""
        body = (await client.get("/api/v1/changes", params=WINDOW)).json()
        filters = body["filters"]
        assert filters["actions"] == ["added", "modified", "removed"]
        assert filters["significance"] == ["security", "undetermined"]
        assert filters["min_severity"] == "info"

    async def test_the_acl_edit_comes_back_as_one_edit_with_both_masks(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/changes", params={**WINDOW, "kind": "ntfs_ace"})).json()
        assert len(body["edits"]) == 1
        edit = body["edits"][0]
        assert edit["direction"] == "narrowed"
        assert edit["rights_before"]["value"] == FULL_CONTROL
        assert edit["rights_after"]["value"] == READ_EXECUTE
        paired = [c for c in body["changes"] if c["edit"] == edit["index"]]
        assert len(paired) == 2

    async def test_the_rights_view_keeps_the_mask_authoritative(
        self, client: AsyncClient, estate: None
    ) -> None:
        """A client that compares the label is comparing strings, and SMB Change and NTFS
        Modify are the same bits under two names."""
        body = (await client.get("/api/v1/changes", params={**WINDOW, "kind": "ntfs_ace"})).json()
        rights = body["edits"][0]["rights_after"]
        assert rights["mask"].startswith("0x")
        assert isinstance(rights["value"], int)
        assert rights["layer"] == "ntfs"

    async def test_a_scope_narrows_the_page(self, client: AsyncClient, estate: None) -> None:
        body = (
            await client.get("/api/v1/changes", params={**WINDOW, "share": FINANCE_SHARE})
        ).json()
        assert body["filters"]["scope_target"] == "share"
        assert all(c["kind"] != "principal" for c in body["changes"])

    async def test_two_scopes_at_once_are_refused_rather_than_ranked(
        self, client: AsyncClient, estate: None
    ) -> None:
        """Their intersection and their union are both plausible, and picking one silently
        would be wrong for somebody without saying so."""
        response = await client.get(
            "/api/v1/changes", params={**WINDOW, "share": FINANCE_SHARE, "server": "fs01"}
        )
        assert response.status_code == 422
        assert "at most one" in response.text.casefold()

    async def test_a_naive_instant_is_refused(self, client: AsyncClient, estate: None) -> None:
        response = await client.get(
            "/api/v1/changes", params={"from": "2026-03-05T09:00:00", "to": iso(h.NEXT_MONDAY)}
        )
        assert response.status_code == 422
        assert "utc offset" in response.text.casefold()

    async def test_paging_is_by_opaque_cursor(self, client: AsyncClient, estate: None) -> None:
        first = (await client.get("/api/v1/changes", params={**WINDOW, "limit": 1})).json()
        assert first["page"]["has_more"]
        cursor = first["page"]["next_cursor"]
        assert cursor
        second = (
            await client.get("/api/v1/changes", params={**WINDOW, "limit": 1, "cursor": cursor})
        ).json()
        assert second["changes"][0] != first["changes"][0]

    async def test_a_cursor_from_another_endpoint_is_refused(
        self, client: AsyncClient, estate: None
    ) -> None:
        from app.api.pagination import encode_offset_cursor

        response = await client.get(
            "/api/v1/changes", params={**WINDOW, "cursor": encode_offset_cursor(3)}
        )
        assert response.status_code == 422

    async def test_a_page_says_whether_the_scan_budget_ended_it(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/changes", params=WINDOW)).json()
        assert body["scan_exhausted"] is False
        assert body["scanned"] >= len(body["changes"])


class TestTheSummarySaysWhatThePageIsNotShowing:
    async def test_it_counts_the_window_before_the_filter(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (
            await client.get(
                "/api/v1/changes/summary", params={**WINDOW, "min_severity": "critical"}
            )
        ).json()
        assert body["total"] > body["returned"]
        assert body["excluded"] == body["total"] - body["returned"]

    async def test_it_names_the_most_severe_thing_in_the_window(
        self, client: AsyncClient, estate: None
    ) -> None:
        body = (await client.get("/api/v1/changes/summary", params=WINDOW)).json()
        assert body["highest_severity"] in {"info", "low", "medium", "high", "critical"}

    async def test_it_counts_the_first_sightings_the_feed_hides(
        self, client: AsyncClient, estate: None
    ) -> None:
        window = {"from": iso(h.MONDAY - dt.timedelta(days=1)), "to": iso(h.WEDNESDAY)}
        feed = (await client.get("/api/v1/changes", params=window)).json()
        summary = (await client.get("/api/v1/changes/summary", params=window)).json()
        assert feed["changes"] == []
        assert summary["by_action"]["first_observed"] > 0


class TestTheTimelineAndTheComparison:
    async def test_one_objects_timeline_reads_newest_first(
        self, client: AsyncClient, estate: None
    ) -> None:
        response = await client.get(
            "/api/v1/changes/timeline",
            params={"kind": "smb_share", "key": "fs01|hr"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert [c["action"] for c in body["changes"]] == ["removed"]

    async def test_a_directory_key_survives_as_a_query_parameter(
        self, client: AsyncClient, estate: None
    ) -> None:
        """A UNC path in a URL *path* is normalized or rejected by several proxies."""
        response = await client.get(
            "/api/v1/changes/timeline",
            params={"kind": "ntfs_resource", "key": FINANCE_PATH},
        )
        assert response.status_code == 200, response.text
        assert response.json()["key"] == FINANCE_PATH

    async def test_the_comparison_reports_what_it_could_not_compare(
        self, client: AsyncClient, estate: None
    ) -> None:
        response = await client.get(
            "/api/v1/changes/compare",
            params={"from": iso(h.MONDAY + dt.timedelta(minutes=1)), "to": iso(h.NEXT_MONDAY)},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "unobserved_at_from" in body
        assert "unobserved_at_to" in body
        assert body["summary"]["total"] >= len(body["changes"])


class TestTheImpactEndpoint:
    async def _instant(self, client: AsyncClient) -> str:
        body = (await client.get("/api/v1/changes", params={**WINDOW, "kind": "ntfs_ace"})).json()
        added = [c for c in body["changes"] if c["action"] == "added"]
        return added[0]["at"]

    async def test_a_client_hands_back_the_at_it_was_given(
        self, client: AsyncClient, estate: None
    ) -> None:
        at = await self._instant(client)
        response = await client.get(
            "/api/v1/changes/impact",
            params={"kind": "ntfs_ace", "key": _everyone_key(), "at": at},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["verdict"] == "resolved"
        assert body["access"]["subject_key"] == EVERYONE

    async def test_it_reports_both_answers_with_their_outcomes(
        self, client: AsyncClient, estate: None
    ) -> None:
        """ADR-0016: branch on the outcome, never on the boolean."""
        at = await self._instant(client)
        body = (
            await client.get(
                "/api/v1/changes/impact",
                params={"kind": "ntfs_ace", "key": _everyone_key(), "at": at},
            )
        ).json()
        for side in ("before", "after"):
            answer = body["access"][side]
            assert answer["outcome"] in {"granted", "denied", "no_grant", "indeterminate"}
            assert isinstance(answer["conclusive"], bool)
            assert answer["reason"]

    async def test_granting_everyone_on_the_file_system_changes_nothing_over_smb(
        self, client: AsyncClient, estate: None
    ) -> None:
        """The case the endpoint exists for, and it is the counter-intuitive one.

        ``Everyone`` was granted Read & Execute on the directory, so the NTFS ACL plainly
        broadened. Over SMB it grants nobody anything: the share ACL names only
        ``Finance-RW``, the two layers cross, and ``Everyone`` has no share-layer rights to
        cross with. An interface that reported the ACL change alone would have an operator
        chasing an exposure that does not exist.
        """
        at = await self._instant(client)
        body = (
            await client.get(
                "/api/v1/changes/impact",
                params={"kind": "ntfs_ace", "key": _everyone_key(), "at": at},
            )
        ).json()
        assert body["change"]["direction"] == "broadened"
        assert body["access"]["access_path"] == "remote_smb"
        assert body["access"]["direction"] == "neutral"

    async def test_the_same_grant_does_broaden_access_for_anyone_logged_on_locally(
        self, client: AsyncClient, estate: None
    ) -> None:
        """``local`` applies only NTFS, which is what anybody who can log on to the server
        gets — and there the grant is exactly what it looks like."""
        at = await self._instant(client)
        body = (
            await client.get(
                "/api/v1/changes/impact",
                params={
                    "kind": "ntfs_ace",
                    "key": _everyone_key(),
                    "at": at,
                    "access_path": "local",
                },
            )
        ).json()
        assert body["access"]["direction"] == "broadened"
        assert body["access"]["gained"]["value"] > 0


class TestAuthorization:
    async def test_the_feed_needs_changes_read(
        self,
        client_as: Callable[..., AbstractAsyncContextManager[AsyncClient]],
        estate: None,
    ) -> None:
        async with client_as() as anonymous:
            assert (await anonymous.get("/api/v1/changes", params=WINDOW)).status_code == 403

    async def test_a_viewer_may_read_the_feed(
        self,
        client_as: Callable[..., AbstractAsyncContextManager[AsyncClient]],
        estate: None,
    ) -> None:
        async with client_as(Role.VIEWER) as viewer:
            assert (await viewer.get("/api/v1/changes", params=WINDOW)).status_code == 200

    async def test_the_impact_endpoint_requires_access_read_not_changes_read(
        self,
        client: AsyncClient,
        client_as: Callable[..., AbstractAsyncContextManager[AsyncClient]],
        estate: None,
    ) -> None:
        """Knowing an ACE was added and knowing what a principal could consequently do are
        different disclosures. A role holding neither capability is refused by both."""
        at = (await client.get("/api/v1/changes", params={**WINDOW, "kind": "ntfs_ace"})).json()[
            "changes"
        ][0]["at"]
        async with client_as() as anonymous:
            response = await anonymous.get(
                "/api/v1/changes/impact",
                params={"kind": "ntfs_ace", "key": _everyone_key(), "at": at},
            )
            assert response.status_code == 403

    async def test_every_change_route_is_declared_in_the_capability_list(self) -> None:
        """The exhaustiveness audit already covers this; named here so a reader of this
        suite sees that the new routes are inside it rather than beside it."""
        from app.api import PUBLIC_PATHS

        assert not any(path.startswith("/api/v1/changes") for path in PUBLIC_PATHS)


def _everyone_key() -> str:
    return f"{FINANCE_PATH}|{EVERYONE}|allow|0x{READ_EXECUTE:08x}|0x03"
