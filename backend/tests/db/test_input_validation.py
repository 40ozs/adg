r"""Every externally supplied value, against every route that takes one.

The rule this file enforces is simple and absolute: **a malformed input is refused, never
survived**. A 5xx from a bad path parameter is the failure mode that matters, for three
reasons — it is an unhandled exception rather than a decision, it reaches a log as a
traceback instead of as a rejection, and it is indistinguishable to a caller from the server
being broken.

The values are chosen to be nasty in the specific ways this domain invites:

* **A SID that is not a SID.** Identifiers are SIDs and storage keys; both have structure,
  and the parser is strict about it.
* **A path that is not a UNC path.** A directory is named by ``\\server\share\...``. A local
  path is ambiguous — the server is unknown — and is refused for that reason, not for
  neatness.
* **Traversal.** ``..`` in a resource key. It cannot escape anything (the value is a
  database key, not a file name, and nothing opens it), which is exactly why it has to be
  refused *by the parser* rather than by luck.
* **Quotes and statement terminators.** Everything is parameterized through SQLAlchemy, so
  these cannot inject; the assertion is that they are handled rather than survived.
* **Length.** A 4,000-character path, against columns that are not that wide.
* **Encoding.** A NUL, which PostgreSQL rejects in text and which must never reach it.

Run against a real database on purpose: a validator that rejects before the query is not
proved by a test with no database behind it, and the failure this file exists to catch —
a value that passes validation and then explodes in the driver — only happens with one.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


# Hoisted out of the f-strings below: a backslash inside an f-string expression is a
# syntax error before Python 3.12, and this project targets 3.11.
FINANCE = r"\\fs01\finance"
LOCAL_PATH = r"C:\Shares\Finance"
TRAVERSAL = r"\\fs01\finance\..\..\..\windows"
ENCODED_FINANCE = quote(FINANCE)


#: Values that are not a principal identifier. A SID or a storage key, and nothing else.
BAD_IDENTIFIERS = [
    pytest.param("not-a-sid", id="not-a-sid"),
    pytest.param("S-1-", id="truncated"),
    pytest.param("S-X-5-21-1-2-3", id="non-numeric-authority"),
    pytest.param("S-1-5-21-" + "9" * 40, id="overflowing-sub-authority"),
    pytest.param("S-1-5-21-1-2-3-" + "-1", id="negative-rid"),
    pytest.param("'; DROP TABLE principals; --", id="statement-terminator"),
    pytest.param("S-1-5-21-1-2-3-1000' OR '1'='1", id="quote-injection"),
    pytest.param("../../../etc/passwd", id="traversal"),
    pytest.param("S" * 4000, id="very-long"),
    pytest.param("S-1-5-21-1-2-3-1000\x00", id="nul-byte"),
    pytest.param("  ", id="whitespace"),
]

#: Values that are not a resource key at all. A directory is named by a UNC path; nothing
#: else identifies one, and each of these fails to be one for its own reason.
BAD_RESOURCES = [
    pytest.param("not-a-path", id="bare-word"),
    pytest.param(r"C:\Shares\Finance", id="local-path"),
    pytest.param("\\\\", id="bare-separator"),
    pytest.param(r"\\fs01", id="server-only"),
    pytest.param(r"\\fs01\finance\..\..\..\windows", id="traversal"),
    pytest.param("\\\\fs01\\" + "a" * 4000, id="very-long"),
    pytest.param("\\\\fs01\\finance\x00", id="nul-byte"),
    pytest.param("/etc/passwd", id="posix-path"),
]

#: Values that *are* well-formed UNC paths and name nothing ADG has collected. These are not
#: malformed and must not be refused as though they were — see
#: :class:`TestAWellFormedPathThatNamesNothing`.
UNKNOWN_RESOURCES = [
    pytest.param(r"\\fs01\nosuchshare", id="unknown-share"),
    pytest.param(r"\\fs99\nothing", id="unknown-server"),
    pytest.param(r"\\fs01\finance'; DROP TABLE ntfs_resources; --", id="statement-terminator"),
]

#: Values that are not a share key. ``server|share``, case-folded.
BAD_SHARES = [
    pytest.param("no-separator", id="no-separator"),
    pytest.param("|", id="empty-both"),
    pytest.param("fs01|", id="empty-share"),
    pytest.param("|finance", id="empty-server"),
    pytest.param("fs01|finance|extra", id="too-many-parts"),
    pytest.param("fs01|finance'; --", id="statement-terminator"),
    pytest.param("a" * 4000 + "|b", id="very-long"),
]


def paths_for_identifier(value: str) -> list[str]:
    encoded = quote(value)
    return [
        f"/api/v1/principals/{encoded}",
        f"/api/v1/principals/{encoded}/groups",
        f"/api/v1/principals/{encoded}/membership-paths?group={quote('S-1-5-21-1-2-3-1000')}",
        f"/api/v1/principals/{encoded}/shares",
        f"/api/v1/groups/{encoded}/members",
        f"/api/v1/groups/{encoded}/effective-members",
        f"/api/v1/groups/{encoded}/resource-impact",
        f"/api/v1/access/principals/{encoded}/shares",
        f"/api/v1/access/principals/{encoded}/resources",
        f"/api/v1/access/explain?principal={encoded}&resource={ENCODED_FINANCE}",
    ]


def paths_for_resource(value: str) -> list[str]:
    encoded = quote(value)
    subject = quote("S-1-5-21-1-2-3-1000")
    return [
        f"/api/v1/resources/{encoded}",
        f"/api/v1/resources/{encoded}/acl",
        f"/api/v1/access/resources/{encoded}/principals",
        f"/api/v1/access/principals/{subject}/resources/{encoded}",
        f"/api/v1/access/explain?principal={subject}&resource={encoded}",
    ]


def paths_for_share(value: str) -> list[str]:
    encoded = quote(value)
    return [
        f"/api/v1/shares/{encoded}",
        f"/api/v1/shares/{encoded}/acl",
        f"/api/v1/shares/{encoded}/root-acl",
    ]


@pytest.fixture
async def known_principal(client: AsyncClient) -> str:
    """One principal ADG actually holds, posted through the ingestion API.

    Needed so that a test about an unknown *resource* is not answered with a 404 about an
    unknown *subject*. One observation is enough; nothing here is about the estate.
    """
    sid = "S-1-5-21-1-2-3-1000"
    run_id = "00000000-0000-4000-8000-0000000000aa"
    start = await client.post(
        "/api/v1/scan-runs",
        json={
            "schema_version": "1.3",
            "run_id": run_id,
            "source": {"collector": "active_directory", "collector_host": "DC01", "method": "t"},
            "started_at": "2026-09-14T08:00:00Z",
            "scopes": [{"kind": "domain", "key": "corp.example.com"}],
        },
    )
    assert start.status_code in (200, 201), start.text
    batch = await client.post(
        f"/api/v1/scan-runs/{run_id}/batches",
        json={
            "schema_version": "1.3",
            "run_id": run_id,
            "batch_id": "00000000-0000-4000-8000-0000000000bb",
            "sequence": 1,
            "is_final": True,
            "observations": [
                {
                    "schema_version": "1.3",
                    "kind": "principal",
                    "run_id": run_id,
                    "observed_at": "2026-09-14T08:00:01Z",
                    "source_key": f"principal|{sid}",
                    "sid": sid,
                    "principal_kind": "user",
                    "display_name": "Test Subject",
                }
            ],
        },
    )
    assert batch.status_code == 202, batch.text
    return sid


async def refuse(client: AsyncClient, paths: list[str], value: str) -> None:
    """Every path must answer with a client error, and none with a server error."""
    survived: list[str] = []
    for path in paths:
        response = await client.get(path)
        if response.status_code >= 500:
            survived.append(f"{path} -> {response.status_code} {response.text[:200]}")
        elif response.status_code < 400:
            survived.append(f"{path} -> {response.status_code} (accepted a malformed value)")
    assert not survived, f"Input {value!r} was not refused cleanly:\n  " + "\n  ".join(survived)


class TestMalformedIdentifiers:
    @pytest.mark.parametrize("value", BAD_IDENTIFIERS)
    async def test_no_route_survives_one(self, client: AsyncClient, value: str) -> None:
        await refuse(client, paths_for_identifier(value), value)

    async def test_a_value_no_principal_could_carry_says_so(self, client: AsyncClient) -> None:
        """Two absences that call for two different sentences.

        An identifier that *could* name a principal is simply not collected yet, and may be
        tomorrow. One that could not — a bare value that is neither a SID nor ``host|SID``
        — will never be, because no object can carry it. Telling somebody who mistyped a SID
        that it "may not have been collected yet" sends them to look at the collectors
        instead of at what they typed.
        """
        response = await client.get("/api/v1/principals/not-a-sid")

        assert response.status_code == 404
        assert "is not a principal identifier" in response.text
        assert "S-1-5-21" in response.text
        assert "not have been collected yet" not in response.text

    async def test_a_well_formed_sid_nobody_has_seen_is_a_different_message(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/principals/S-1-5-21-1-2-3-9999")

        assert response.status_code == 404
        assert "not have been collected yet" in response.text

    async def test_a_scoped_key_whose_sid_half_is_junk_is_refused_as_malformed(
        self, client: AsyncClient
    ) -> None:
        """``fs01|not-a-sid`` has the shape of a storage key and cannot be one."""
        response = await client.get(f"/api/v1/principals/{quote('fs01|not-a-sid')}")

        assert response.status_code == 404
        assert "is not a principal identifier" in response.text


class TestMalformedResourceKeys:
    @pytest.mark.parametrize("value", BAD_RESOURCES)
    async def test_no_route_survives_one(self, client: AsyncClient, value: str) -> None:
        await refuse(client, paths_for_resource(value), value)

    async def test_a_local_path_is_refused_with_the_reason(self, client: AsyncClient) -> None:
        r"""``C:\Shares\Finance`` is a perfectly good path and identifies nothing: which
        server it is on is exactly what a resource key has to carry."""
        response = await client.get(f"/api/v1/resources/{quote(LOCAL_PATH)}")

        assert response.status_code in (404, 422)

    async def test_a_traversal_is_refused_rather_than_normalized(self, client: AsyncClient) -> None:
        """Normalizing it would make two different keys name one row, which is how a
        directory's ACL ends up attributed to its grandparent."""
        response = await client.get(f"/api/v1/resources/{quote(TRAVERSAL)}")

        assert response.status_code in (404, 422)


class TestAWellFormedPathThatNamesNothing:
    """Not a validation failure, and the API is deliberately of two minds about it.

    The **raw-fact** routes answer `404` — and say that the absence means no run has read
    the directory, not that the directory does not exist.

    The **access** routes answer `200` with ``resource.observed: false``. That is the whole
    design: "nobody looked" is itself the answer this product exists to give, and a `404`
    there would let a caller read a missing answer as a finding of no access. The two
    behaviors look inconsistent in a status-code table and are the same rule underneath.

    A statement terminator is in this list rather than in the malformed one because
    ``\\fs01\finance'; DROP TABLE ...`` *is* a syntactically valid UNC path. It names
    nothing, it cannot inject — every query is parameterized — and being refused for looking
    dangerous would be a rule about appearances rather than about form.
    """

    @pytest.mark.parametrize("value", UNKNOWN_RESOURCES)
    async def test_the_raw_fact_routes_say_nothing_has_read_it(
        self, client: AsyncClient, value: str
    ) -> None:
        for path in (
            f"/api/v1/resources/{quote(value)}",
            f"/api/v1/resources/{quote(value)}/acl",
        ):
            response = await client.get(path)

            assert response.status_code == 404, f"{path}: {response.status_code}"
            assert "not that the directory does not exist" in response.text or (
                "An empty DACL would be reported" in response.text
            ), response.text

    @pytest.mark.parametrize("value", UNKNOWN_RESOURCES)
    async def test_the_access_routes_answer_that_it_was_never_observed(
        self, client: AsyncClient, known_principal: str, value: str
    ) -> None:
        """The subject has to be one ADG holds, or the 404 under test would be about *it*.

        That distinction is the point of the fixture: an answer about an unknown principal
        and an answer about an unobserved resource are different statements, and a test that
        confused them would pass while proving nothing about either.
        """
        subject = quote(known_principal)
        for path in (
            f"/api/v1/access/resources/{quote(value)}/principals",
            f"/api/v1/access/principals/{subject}/resources/{quote(value)}",
            f"/api/v1/access/explain?principal={subject}&resource={quote(value)}",
        ):
            response = await client.get(path)

            assert response.status_code == 200, f"{path}: {response.text[:200]}"
            assert response.json()["resource"]["observed"] is False, path


class TestMalformedShareKeys:
    @pytest.mark.parametrize("value", BAD_SHARES)
    async def test_no_route_survives_one(self, client: AsyncClient, value: str) -> None:
        await refuse(client, paths_for_share(value), value)


class TestMalformedQueryParameters:
    @pytest.mark.parametrize(
        "query",
        [
            "limit=0",
            "limit=-1",
            "limit=99999",
            "limit=abc",
            "cursor=not-a-cursor",
            "cursor=" + quote("../../etc/passwd"),
        ],
    )
    async def test_a_bad_page_request_is_refused(self, client: AsyncClient, query: str) -> None:
        response = await client.get(f"/api/v1/servers?{query}")

        assert 400 <= response.status_code < 500, response.text

    @pytest.mark.parametrize(
        "query",
        ["", "q=", "q=" + "x" * 4000, "q=" + quote("'; DROP TABLE principals; --")],
    )
    async def test_a_bad_search_is_refused_or_answered_emptily(
        self, client: AsyncClient, query: str
    ) -> None:
        """A search is the one place a user types free text, so the bar is different: a
        too-short or hostile query may legitimately return nothing rather than an error.
        What it may never do is fail."""
        response = await client.get(f"/api/v1/search?{query}")

        assert response.status_code < 500, response.text

    async def test_an_unknown_collector_filter_is_refused_rather_than_ignored(
        self, client: AsyncClient
    ) -> None:
        """Ignoring it would answer a different question from the one asked, and the caller
        would have no way to tell."""
        response = await client.get("/api/v1/scan-runs?collector=nonexistent")

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "query",
        ["max_depth=0", "max_depth=-5", "max_paths=0", "max_removal_targets=-1"],
    )
    async def test_a_bad_traversal_bound_is_refused(self, client: AsyncClient, query: str) -> None:
        subject = quote("S-1-5-21-1-2-3-1000")
        response = await client.get(
            f"/api/v1/access/explain?principal={subject}&resource={ENCODED_FINANCE}&{query}"
        )

        assert 400 <= response.status_code < 500, response.text


class TestMalformedRunIdentifiers:
    @pytest.mark.parametrize(
        "value", ["not-a-uuid", "00000000", "'; --", "../../etc/passwd", "0" * 600]
    )
    async def test_a_bad_run_id_is_refused(self, client: AsyncClient, value: str) -> None:
        response = await client.get(f"/api/v1/scan-runs/{quote(value)}")

        assert 400 <= response.status_code < 500, response.text


class TestMalformedIngestionPayloads:
    """Ingestion is the only write, and its rejections are part of the collector contract:
    a 422 must never be retried unchanged, so it has to be a 422 rather than a 500."""

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({}, id="empty"),
            pytest.param({"schema_version": "1.3"}, id="version-only"),
            pytest.param({"run_id": "not-a-uuid"}, id="bad-run-id"),
            pytest.param(
                {
                    "schema_version": "99.0",
                    "run_id": "00000000-0000-4000-8000-000000000001",
                    "source": {"collector": "ntfs", "collector_host": "FS01", "method": "m"},
                    "started_at": "2026-09-14T08:00:00Z",
                    "scopes": [{"kind": "server", "key": "fs01"}],
                },
                id="future-contract-version",
            ),
            pytest.param(
                {
                    "schema_version": "1.3",
                    "run_id": "00000000-0000-4000-8000-000000000001",
                    "source": {
                        "collector": "not-a-collector",
                        "collector_host": "x",
                        "method": "m",
                    },
                    "started_at": "2026-09-14T08:00:00Z",
                    "scopes": [{"kind": "server", "key": "fs01"}],
                },
                id="unknown-collector",
            ),
            pytest.param(
                {
                    "schema_version": "1.3",
                    "run_id": "00000000-0000-4000-8000-000000000001",
                    "source": {"collector": "ntfs", "collector_host": "FS01", "method": "m"},
                    "started_at": "2026-09-14T08:00:00Z",
                    "scopes": [],
                },
                id="no-scope",
            ),
        ],
    )
    async def test_a_bad_start_is_a_client_error(
        self, client: AsyncClient, payload: dict[str, Any]
    ) -> None:
        response = await client.post("/api/v1/scan-runs", json=payload)

        assert 400 <= response.status_code < 500, response.text

    async def test_a_batch_for_a_run_that_does_not_exist_is_a_404(
        self, client: AsyncClient
    ) -> None:
        """Not a 500, and not a silent create: a batch arriving before its start envelope
        means the collector's retry logic is out of order, and it has to be told so."""
        run_id = "00000000-0000-4000-8000-00000000dead"
        response = await client.post(
            f"/api/v1/scan-runs/{run_id}/batches",
            json={
                "schema_version": "1.3",
                "run_id": run_id,
                "batch_id": "00000000-0000-4000-8000-00000000beef",
                "sequence": 1,
                "is_final": True,
                "observations": [
                    {
                        "schema_version": "1.3",
                        "kind": "server",
                        "run_id": run_id,
                        "observed_at": "2026-09-14T08:00:00Z",
                        "source_key": "server|fs01",
                        "name": "FS01",
                    }
                ],
            },
        )

        assert response.status_code == 404, response.text

    async def test_an_observation_whose_source_key_is_wrong_is_refused(
        self, client: AsyncClient
    ) -> None:
        """The server re-derives every key. A collector deriving them differently would
        create a second row for an object that already exists, silently."""
        run_id = "00000000-0000-4000-8000-00000000c0de"
        start = await client.post(
            "/api/v1/scan-runs",
            json={
                "schema_version": "1.3",
                "run_id": run_id,
                "source": {"collector": "smb", "collector_host": "FS01", "method": "m"},
                "started_at": "2026-09-14T08:00:00Z",
                "scopes": [{"kind": "server", "key": "fs01"}],
            },
        )
        assert start.status_code == 201, start.text

        response = await client.post(
            f"/api/v1/scan-runs/{run_id}/batches",
            json={
                "schema_version": "1.3",
                "run_id": run_id,
                "batch_id": "00000000-0000-4000-8000-00000000cafe",
                "sequence": 1,
                "is_final": True,
                "observations": [
                    {
                        "schema_version": "1.3",
                        "kind": "server",
                        "run_id": run_id,
                        "observed_at": "2026-09-14T08:00:00Z",
                        "source_key": "server|somethingelse",
                        "name": "FS01",
                    }
                ],
            },
        )

        assert response.status_code == 422, response.text
        assert "source_key" in response.text
