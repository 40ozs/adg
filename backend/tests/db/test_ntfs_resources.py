r"""NTFS ingestion and the directory query endpoints, against a real PostgreSQL.

What these pin down is the shape of the answers rather than the SQL, and above all the one
thing this phase exists to make possible: **a share's raw SMB ACL and the raw NTFS ACL of
its root are two separate answers.** Access over SMB is limited by both; access at the
console is limited only by the second. An auditor who cannot see them apart cannot tell
which layer is doing the restricting, and a tool that merged them would be answering a
question about effective access while claiming to report raw facts.

The rest follows the rules the resource tables already had: an absent parent is reported as
``null`` rather than dropped, derived values are derived, resolution is a join, and a
descriptor's own ``ace_count`` is reported beside the number of entries actually stored so
that a shortfall is visible instead of reconciled away.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import quote

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1 import keys
from app.domain import AceType, AclAceFacts, acl_hash
from app.models.schema import ntfs_aces, ntfs_resources, principal_references
from tests.fixtures import load_raw
from tests.support.ingest import replay, smb_only, storable_document, with_run_id

pytestmark = pytest.mark.anyio

DOMAIN_SID = "S-1-5-21-1004336348-1177238915-682003330"
FINANCE_RW = f"{DOMAIN_SID}-1202"
ALICE = f"{DOMAIN_SID}-1104"
ADMINISTRATORS = "S-1-5-32-544"
FOREIGN = "S-1-5-21-999888777-666555444-333222111-1234"

MODIFY = 0x001301BF
FULL_CONTROL = 0x001F01FF
READ_EXECUTE = 0x001200A9

# Backslashes cannot appear inside an f-string expression on the pinned Python version, so
# the UNC spellings under test live here.
FINANCE_UNC = "\\\\FS01\\Finance"
FINANCE_KEY = "\\\\fs01\\finance"
PAYROLL_UNC = "\\\\FS01\\Finance\\Payroll"
PAYROLL_KEY = "\\\\fs01\\finance\\payroll"
REPORTS_UNC = "\\\\FS01\\Finance\\Reports"
WIDE_UNC = "\\\\FS01\\Wide"
BUDGET_UNC = "\\\\FS01\\Finance\\Budget.xlsx"
DETACHED_UNC = "\\\\FS02\\Payroll\\Detached"
DETACHED_PARENT_KEY = "\\\\fs02\\payroll"


def encoded(path: str) -> str:
    return quote(path, safe="")


async def count(session: AsyncSession, table: sa.Table) -> int:
    return int((await session.execute(sa.select(sa.func.count()).select_from(table))).scalar_one())


def observation(kind: str, run_id: str, index: int, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.2",
        "kind": kind,
        "run_id": run_id,
        "observed_at": f"2026-09-14T08:{index:02d}:00Z",
        **fields,
    }


def resource(run_id: str, index: int, path: str, **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "dacl_present": True,
        "ace_count": 0,
        **fields,
    }
    return observation(
        "ntfs_resource",
        run_id,
        index,
        source_key=keys.ntfs_resource_key(path),
        path=path,
        **body,
    )


def ace(
    run_id: str,
    index: int,
    path: str,
    trustee: str,
    *,
    ace_type: str = "allow",
    mask: int = MODIFY,
    flags: int = 0x03,
    order: int = 0,
    **fields: Any,
) -> dict[str, Any]:
    from app.domain import Sid

    return observation(
        "ntfs_ace",
        run_id,
        index,
        source_key=keys.ntfs_ace_key(path, Sid(trustee), ace_type, mask, flags),
        path=path,
        trustee_sid=trustee,
        ace_type=ace_type,
        access_mask=mask,
        ace_flags=flags,
        source="inherited" if flags & 0x10 else "explicit",
        order_index=order,
        **fields,
    )


def digest(*entries: dict[str, Any], protected: bool = False) -> str:
    return acl_hash(
        dacl_present=True,
        dacl_protected=protected,
        aces=[
            AclAceFacts(
                trustee_sid=item["trustee_sid"],
                ace_type=AceType(item["ace_type"]),
                access_mask=item["access_mask"],
                ace_flags=item["ace_flags"],
                order_index=item["order_index"],
            )
            for item in entries
        ],
    )


def run(observations: list[dict[str, Any]], *, scope: str = FINANCE_KEY) -> dict[str, Any]:
    """A complete, incremental NTFS transcript around the given observations.

    Incremental because that is what this collector sends: a share-root read has enumerated
    no directory tree, and the server refuses to let such a run reconcile anything.
    """
    run_id = str(uuid.uuid4())
    for item in observations:
        item["run_id"] = run_id
    return {
        "start": {
            "schema_version": "1.2",
            "run_id": run_id,
            "source": {
                "collector": "ntfs",
                "collector_host": "COLLECTOR01",
                "method": "DirectorySecurity.GetSecurityDescriptorBinaryForm",
            },
            "started_at": "2026-09-14T08:00:00Z",
            "scopes": [{"kind": "directory_tree", "key": scope}],
            "incremental": True,
        },
        "batches": [
            {
                "schema_version": "1.2",
                "run_id": run_id,
                "batch_id": str(uuid.uuid4()),
                "sequence": 1,
                "is_final": True,
                "observations": observations,
            }
        ],
        "completion": {
            "schema_version": "1.2",
            "run_id": run_id,
            "status": "succeeded",
            "completed_at": "2026-09-14T08:10:00Z",
            "batch_count": 1,
            "observation_count": len(observations),
            "error_count": 0,
            "errors": [],
            "reconciled_scopes": [],
        },
    }


class TestStoringADirectory:
    async def test_a_root_and_its_entries_become_rows(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, storable_document("06-unresolved-sid"))

        assert await count(session, ntfs_resources) == 1
        assert await count(session, ntfs_aces) == 2

    async def test_the_key_is_the_case_folded_path_and_the_path_keeps_its_spelling(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, storable_document("06-unresolved-sid"))

        row = (await session.execute(sa.select(ntfs_resources))).mappings().one()
        assert row["resource_key"] == FINANCE_KEY
        assert row["path"] == FINANCE_UNC

    async def test_a_directory_is_linked_to_the_share_it_sits_under(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # The column that makes "show me this share's SMB ACL and its root's NTFS ACL" a
        # pair of lookups rather than a path-parsing exercise at every call site.
        await replay(client, storable_document("06-unresolved-sid"))

        row = (await session.execute(sa.select(ntfs_resources))).mappings().one()
        assert row["share_key"] == "fs01|finance"
        assert row["server_key"] == "fs01"

    async def test_a_builtin_trustee_is_scoped_to_the_server_it_was_read_on(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, storable_document("09-broken-inheritance"))

        rows = (
            (
                await session.execute(
                    sa.select(ntfs_aces).where(ntfs_aces.c.trustee_sid == ADMINISTRATORS)
                )
            )
            .mappings()
            .all()
        )
        assert [row["trustee_key"] for row in rows] == [f"fs01|{ADMINISTRATORS}"]

    async def test_an_ace_records_that_a_directory_names_its_trustee(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(client, storable_document("06-unresolved-sid"))

        rows = (
            (
                await session.execute(
                    sa.select(principal_references).where(
                        principal_references.c.reference_kind == "ntfs_ace"
                    )
                )
            )
            .mappings()
            .all()
        )
        assert {row["reference_key"] for row in rows} == {FINANCE_KEY}
        assert {row["sid"] for row in rows} == {ALICE, FOREIGN}

    async def test_re_sending_a_run_changes_nothing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        document = storable_document("08-inherited-ace")
        await replay(client, document)
        before = (await count(session, ntfs_resources), await count(session, ntfs_aces))

        await replay(client, with_run_id(load_raw("08-inherited-ace")))

        assert (await count(session, ntfs_resources), await count(session, ntfs_aces)) == before

    async def test_a_directory_is_never_deleted_because_a_later_run_omitted_it(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # Absence is inferred only from a reconciled scope, by Phase 7 - and an incremental
        # NTFS run reconciles nothing at all.
        await replay(client, storable_document("09-broken-inheritance"))
        assert await count(session, ntfs_resources) == 2

        await replay(client, storable_document("11-ntfs-more-restrictive"))
        assert await count(session, ntfs_resources) == 2


class TestTheAclHashOnArrival:
    async def test_a_matching_digest_is_stored(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        run_id = str(uuid.uuid4())
        entry = ace(run_id, 1, FINANCE_UNC, FINANCE_RW)
        root = resource(run_id, 0, FINANCE_UNC, ace_count=1, acl_hash=digest(entry))
        await replay(client, run([root, entry]))

        stored = (await session.execute(sa.select(ntfs_resources.c.acl_hash))).scalar_one()
        assert stored == digest(entry)

    async def test_a_contradicting_digest_is_refused_and_nothing_is_written(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        run_id = str(uuid.uuid4())
        entry = ace(run_id, 1, FINANCE_UNC, FINANCE_RW)
        root = resource(run_id, 0, FINANCE_UNC, ace_count=1, acl_hash="0" * 64)
        document = run([root, entry])

        await client.post("/api/v1/scan-runs", json=document["start"])
        response = await client.post(
            f"/api/v1/scan-runs/{document['start']['run_id']}/batches",
            json=document["batches"][0],
        )

        assert response.status_code == 422, response.text
        assert "acl_hash" in response.text
        # A partially applied batch would be worse than a rejected one: the collector would
        # believe the whole batch landed.
        assert await count(session, ntfs_resources) == 0
        assert await count(session, ntfs_aces) == 0

    async def test_a_collector_that_reports_no_digest_is_accepted(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # The published scenarios are contract 1.0 payloads, which predate the field.
        await replay(client, storable_document("06-unresolved-sid"))

        assert (await session.execute(sa.select(ntfs_resources.c.acl_hash))).scalar_one() is None


class TestOneDirectory:
    @pytest.fixture(autouse=True)
    async def stored(self, client: AsyncClient) -> None:
        await replay(client, storable_document("09-broken-inheritance"))

    async def test_it_reports_the_descriptor_facts_the_ace_list_cannot_carry(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/resources/" + encoded(PAYROLL_UNC))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["dacl_present"] is True
        assert body["dacl_protected"] is True
        assert body["inheritance_enabled"] is False
        assert body["is_acl_boundary"] is True

    async def test_it_derives_what_is_a_function_of_the_path(self, client: AsyncClient) -> None:
        root = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC))).json()
        child = (await client.get("/api/v1/resources/" + encoded(PAYROLL_UNC))).json()

        assert root["is_share_root"] is True
        assert child["is_share_root"] is False
        assert child["share_key"] == root["share_key"] == "fs01|finance"

    async def test_it_reports_the_share_and_server_it_belongs_to(self, client: AsyncClient) -> None:
        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC))).json()

        assert body["share"]["key"] == "fs01|finance"
        assert body["server"]["key"] == "fs01"

    async def test_it_reports_what_was_declared_beside_what_was_stored(
        self, client: AsyncClient
    ) -> None:
        # Equal is the normal case; unequal means entries were read and never arrived, and
        # reconciling them here by returning the smaller would hide a coverage gap.
        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC))).json()

        assert body["declared_ace_count"] == 1
        assert body["stored_ace_count"] == 1

    async def test_any_spelling_of_one_path_lands_on_one_directory(
        self, client: AsyncClient
    ) -> None:
        upper = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC))).json()
        lower = (await client.get("/api/v1/resources/" + encoded(FINANCE_KEY))).json()
        assert upper["key"] == lower["key"]

    async def test_a_share_key_is_refused_rather_than_converted(self, client: AsyncClient) -> None:
        # A share and the directory it publishes have different ACLs. Guessing which one was
        # meant would answer a question nobody asked.
        response = await client.get("/api/v1/resources/" + encoded("fs01|finance"))

        assert response.status_code == 422
        assert "root-acl" in response.text

    async def test_an_unread_directory_is_a_404_that_says_what_absence_means(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/resources/" + encoded(REPORTS_UNC))

        assert response.status_code == 404
        assert "no run has read" in response.json()["detail"].lower()


class TestTheRawNtfsAcl:
    @pytest.fixture(autouse=True)
    async def stored(self, client: AsyncClient) -> None:
        await replay(client, storable_document("08-inherited-ace"))

    async def test_it_says_out_loud_that_it_is_not_an_access_decision(
        self, client: AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl")).json()
        assert body["kind"] == "raw_ntfs_acl"

    async def test_an_inherited_entry_says_where_to_make_the_fix(self, client: AsyncClient) -> None:
        body = (await client.get("/api/v1/resources/" + encoded(REPORTS_UNC) + "/acl")).json()

        entry = body["entries"][0]
        assert entry["source"] == "inherited"
        # Redundant with the flags byte and reported anyway: an administrator should not
        # have to remember that 0x10 is INHERITED_ACE.
        assert entry["ace_flags"] & 0x10

    async def test_an_explicit_entry_is_marked_explicit(self, client: AsyncClient) -> None:
        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl")).json()
        assert body["entries"][0]["source"] == "explicit"

    async def test_the_mask_is_reported_raw_and_named_separately(self, client: AsyncClient) -> None:
        # The mask stays authoritative; the names are a rendering of it. A label can never
        # grant, which is the rule the whole rights model is built on.
        entry = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl")).json()[
            "entries"
        ][0]

        assert entry["access_mask"] == MODIFY
        assert "WRITE_DATA" in entry["rights"]
        assert entry["unrecognized_bits"] == 0

    async def test_the_trustee_is_resolved_by_a_join_and_reported_either_way(
        self, client: AsyncClient
    ) -> None:
        entry = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl")).json()[
            "entries"
        ][0]

        assert entry["trustee"]["sid"] == FINANCE_RW
        assert entry["trustee"]["resolved"] is True

    async def test_an_acl_read_for_a_directory_nobody_described_still_comes_back(
        self, client: AsyncClient
    ) -> None:
        # An orphaned ACL is what a partial scan legitimately produces; dropping it would
        # quietly shrink the estate.
        run_id = str(uuid.uuid4())
        entry = ace(run_id, 0, WIDE_UNC, ADMINISTRATORS, mask=FULL_CONTROL)
        await replay(client, run([entry], scope="\\\\fs01\\wide"))

        body = (await client.get("/api/v1/resources/" + encoded(WIDE_UNC) + "/acl")).json()

        assert body["resource"] is None
        assert len(body["entries"]) == 1
        # Without the descriptor's own facts there is nothing to hash: dacl_present and
        # dacl_protected are part of the document, and guessing either would produce a
        # digest that is wrong in a way nobody could see.
        assert body["acl_hash"] is None


class TestTheRecomputedAclHash:
    async def test_the_server_reports_its_own_digest_beside_the_collector_s(
        self, client: AsyncClient
    ) -> None:
        run_id = str(uuid.uuid4())
        entry = ace(run_id, 1, FINANCE_UNC, FINANCE_RW)
        root = resource(run_id, 0, FINANCE_UNC, ace_count=1, acl_hash=digest(entry))
        await replay(client, run([root, entry]))

        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl")).json()

        assert body["acl_hash"]["reported"] == digest(entry)
        assert body["acl_hash"]["computed"] == digest(entry)
        assert body["acl_hash"]["agrees"] is True
        assert body["acl_hash"]["ace_count_agrees"] is True

    async def test_a_missing_report_is_unknown_rather_than_a_disagreement(
        self, client: AsyncClient
    ) -> None:
        await replay(client, storable_document("06-unresolved-sid"))

        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl")).json()

        assert body["acl_hash"]["reported"] is None
        assert body["acl_hash"]["agrees"] is None
        assert body["acl_hash"]["computed"]

    async def test_it_names_the_format_it_hashed(self, client: AsyncClient) -> None:
        await replay(client, storable_document("06-unresolved-sid"))

        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl")).json()

        assert body["acl_hash"]["algorithm"] == "sha256"
        assert body["acl_hash"]["normal_form_version"] == "adg-acl/1"
        assert body["acl_hash"]["ordered"] is True

    async def test_the_digest_covers_the_whole_dacl_not_the_page(self, client: AsyncClient) -> None:
        # A digest over part of a DACL is not a digest of the DACL. Paging must not change
        # the number, or two clients would disagree about one directory.
        await replay(client, storable_document("06-unresolved-sid"))

        whole = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl")).json()
        page = (
            await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl?limit=1")
        ).json()

        assert page["page"]["has_more"] is True
        assert len(page["entries"]) == 1
        assert page["acl_hash"]["computed"] == whole["acl_hash"]["computed"]

    async def test_a_shortfall_between_declared_and_stored_entries_is_visible(
        self, client: AsyncClient
    ) -> None:
        # The descriptor said two, one arrived. Nothing here reconciles that away.
        run_id = str(uuid.uuid4())
        entry = ace(run_id, 1, FINANCE_UNC, FINANCE_RW)
        root = resource(run_id, 0, FINANCE_UNC, ace_count=2)
        await replay(client, run([root, entry]))

        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl")).json()

        assert body["acl_hash"]["declared_ace_count"] == 2
        assert body["acl_hash"]["stored_ace_count"] == 1
        assert body["acl_hash"]["ace_count_agrees"] is False


class TestANullDacl:
    async def test_it_is_reported_as_unrestricted_rather_than_as_an_empty_acl(
        self, client: AsyncClient
    ) -> None:
        run_id = str(uuid.uuid4())
        root = resource(run_id, 0, WIDE_UNC, dacl_present=False, ace_count=0)
        await replay(client, run([root], scope="\\\\fs01\\wide"))

        body = (await client.get("/api/v1/resources/" + encoded(WIDE_UNC))).json()

        assert body["dacl_present"] is False
        assert body["grants_everyone_full_access"] is True
        assert body["denies_everyone"] is False

    async def test_an_empty_dacl_is_the_opposite_fact(self, client: AsyncClient) -> None:
        run_id = str(uuid.uuid4())
        root = resource(run_id, 0, FINANCE_UNC, dacl_present=True, ace_count=0)
        await replay(client, run([root]))

        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC))).json()

        assert body["grants_everyone_full_access"] is False
        assert body["denies_everyone"] is True
        # The owner keeps implicit control rights whatever the DACL says, which is why it is
        # reported beside it rather than folded into the entry list.
        assert "owner_sid" in body


class TestTheTwoLayersSideBySide:
    @pytest.fixture(autouse=True)
    async def stored(self, client: AsyncClient) -> None:
        await replay(client, storable_document("11-ntfs-more-restrictive"))

    async def test_a_share_reports_its_root_without_merging_the_two_acls(
        self, client: AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/shares/" + encoded(FINANCE_UNC))).json()

        assert body["root_resource"]["key"] == FINANCE_KEY
        assert body["ace_count"] == 1, "the share ACL count, not the NTFS one"

    async def test_the_two_acls_come_back_on_separate_routes_with_separate_kinds(
        self, client: AsyncClient
    ) -> None:
        share_acl = (await client.get("/api/v1/shares/" + encoded(FINANCE_UNC) + "/acl")).json()
        root_acl = (await client.get("/api/v1/shares/" + encoded(FINANCE_UNC) + "/root-acl")).json()

        assert share_acl["kind"] == "raw_smb_acl"
        assert root_acl["kind"] == "raw_ntfs_acl"

    async def test_the_layers_can_disagree_and_neither_is_adjusted(
        self, client: AsyncClient
    ) -> None:
        # This scenario is the point of the fixture's name: the share grants Full and the
        # file system grants Read-Execute, so the file system is the layer doing the
        # limiting. The effective answer is the intersection, and computing it here would
        # turn raw facts into an access decision.
        share_acl = (await client.get("/api/v1/shares/" + encoded(FINANCE_UNC) + "/acl")).json()
        root_acl = (await client.get("/api/v1/shares/" + encoded(FINANCE_UNC) + "/root-acl")).json()

        assert share_acl["entries"][0]["permission"] == "full"
        assert root_acl["entries"][0]["access_mask"] == READ_EXECUTE
        # Neither response carries anything resembling a combined answer.
        assert "effective" not in share_acl and "effective" not in root_acl

    async def test_the_root_acl_route_accepts_every_spelling_of_a_share(
        self, client: AsyncClient
    ) -> None:
        by_path = await client.get("/api/v1/shares/" + encoded(FINANCE_UNC) + "/root-acl")
        by_key = await client.get("/api/v1/shares/" + encoded("fs01|finance") + "/root-acl")

        assert by_path.status_code == by_key.status_code == 200
        assert by_path.json()["resource_key"] == by_key.json()["resource_key"]

    async def test_a_folder_path_is_still_refused_as_a_share(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/shares/" + encoded(PAYROLL_UNC) + "/root-acl")
        assert response.status_code == 422


class TestAShareWhoseNtfsLayerNobodyHasRead:
    @pytest.fixture(autouse=True)
    async def stored(self, client: AsyncClient) -> None:
        await replay(client, with_run_id(smb_only(load_raw("11-ntfs-more-restrictive"))))

    async def test_the_share_says_its_root_is_undescribed_rather_than_open(
        self, client: AsyncClient
    ) -> None:
        # Null means "nobody has looked", not "nothing restricts it". The two collectors run
        # independently, and reporting the second would invent access.
        body = (await client.get("/api/v1/shares/" + encoded(FINANCE_UNC))).json()
        assert body["root_resource"] is None

    async def test_asking_for_the_root_acl_is_a_404_that_explains_itself(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/shares/" + encoded(FINANCE_UNC) + "/root-acl")

        assert response.status_code == 404
        detail = response.json()["detail"]
        assert "collected separately" in detail
        assert "not that it grants nothing" in detail

    async def test_the_share_acl_is_unaffected(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/shares/" + encoded(FINANCE_UNC) + "/acl")
        assert response.status_code == 200
        assert response.json()["entries"]


class TestAclOrder:
    async def test_entries_come_back_in_evaluation_order(self, client: AsyncClient) -> None:
        # Windows evaluates a DACL in order: a Deny ahead of an Allow refuses what the Allow
        # would have granted, and the same Deny behind it does not.
        run_id = str(uuid.uuid4())
        deny = ace(run_id, 1, FINANCE_UNC, FINANCE_RW, ace_type="deny", order=0)
        allow = ace(run_id, 2, FINANCE_UNC, ALICE, mask=FULL_CONTROL, order=1)
        root = resource(run_id, 0, FINANCE_UNC, ace_count=2, acl_hash=digest(deny, allow))
        await replay(client, run([root, allow, deny]))

        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl")).json()

        assert [entry["order_index"] for entry in body["entries"]] == [0, 1]
        assert body["entries"][0]["ace_type"] == "deny"

    async def test_paging_walks_the_acl_without_reordering_it(self, client: AsyncClient) -> None:
        run_id = str(uuid.uuid4())
        deny = ace(run_id, 1, FINANCE_UNC, FINANCE_RW, ace_type="deny", order=0)
        allow = ace(run_id, 2, FINANCE_UNC, ALICE, mask=FULL_CONTROL, order=1)
        root = resource(run_id, 0, FINANCE_UNC, ace_count=2, acl_hash=digest(deny, allow))
        await replay(client, run([root, allow, deny]))

        first = (
            await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl?limit=1")
        ).json()
        cursor = first["page"]["next_cursor"]
        second = (
            await client.get(
                "/api/v1/resources/" + encoded(FINANCE_UNC) + f"/acl?limit=1&cursor={cursor}"
            )
        ).json()

        assert first["entries"][0]["order_index"] == 0
        assert second["entries"][0]["order_index"] == 1


class TestAnOrphanedTrustee:
    @pytest.fixture(autouse=True)
    async def stored(self, client: AsyncClient) -> None:
        await replay(client, storable_document("06-unresolved-sid"))

    async def test_a_sid_described_only_as_unresolved_is_reported_as_such(
        self, client: AsyncClient
    ) -> None:
        # Two different claims, and the API keeps them apart: `resolved` says ADG holds a
        # row for the SID, `kind` says what that row managed to establish. Here a collector
        # reported the SID as unresolvable, so the row exists and says "deleted" - which is
        # a finding, not a gap.
        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl")).json()

        trustees = {entry["trustee"]["sid"]: entry["trustee"] for entry in body["entries"]}
        assert FOREIGN in trustees
        assert trustees[FOREIGN]["resolved"] is True
        assert trustees[FOREIGN]["kind"] == "unresolved"
        assert trustees[FOREIGN]["unresolved_reason"] == "deleted"
        # No name is invented; the one seen in an earlier scan is labelled as historical.
        assert trustees[FOREIGN].get("display_name") is None
        assert trustees[FOREIGN]["last_known_name"] == "CORP\jdoe"

    async def test_a_sid_nothing_has_described_at_all_is_reported_unresolved(
        self, client: AsyncClient
    ) -> None:
        # The other half: an ACE naming a SID no run has ever described. Filtering it out
        # for want of a label would hide exactly the finding this tool exists to produce.
        run_id = str(uuid.uuid4())
        stranger = "S-1-5-21-111111111-222222222-333333333-4242"
        entry = ace(run_id, 1, PAYROLL_UNC, stranger, mask=FULL_CONTROL)
        root = resource(run_id, 0, PAYROLL_UNC, ace_count=1)
        await replay(client, run([root, entry], scope=PAYROLL_KEY))

        body = (await client.get("/api/v1/resources/" + encoded(PAYROLL_UNC) + "/acl")).json()

        trustee = body["entries"][0]["trustee"]
        assert trustee["sid"] == stranger
        assert trustee["resolved"] is False
        assert trustee.get("display_name") is None

    async def test_the_grant_it_names_is_reported_in_full(self, client: AsyncClient) -> None:
        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl")).json()

        orphan = next(e for e in body["entries"] if e["trustee"]["sid"] == FOREIGN)
        assert orphan["access_mask"] == FULL_CONTROL
        assert "DELETE" in orphan["rights"]


class TestTheBoundaryVerdict:
    r"""The collector's boundary claim, checked against the parent the database holds.

    Phase 3A stored ``is_acl_boundary`` unread: a share-root collector set it ``true``
    because a root has no comparable parent, and nothing checked it. A tree walk claims it
    for directories that *do* have a parent, and a wrong claim is expensive in exactly one
    direction — a boundary reported ``false`` tells the next scan it may stop looking, and
    every permission change beneath it is silently dropped.

    So the server redoes the arithmetic from what it holds, and reports both answers. The
    comparison is against what the parent **projects** onto a child, never against the
    parent's own digest: those differ by construction, because inheritance sets the
    INHERITED bit on every entry it copies.
    """

    @pytest.fixture(autouse=True)
    async def stored(self, client: AsyncClient) -> None:
        run_id = str(uuid.uuid4())
        # A root with two inheritable entries, and a child holding exactly what Windows
        # would have given it: the same entries with INHERITED added.
        self.root_aces = [
            ace(run_id, 1, FINANCE_UNC, ADMINISTRATORS, mask=FULL_CONTROL, flags=0x03, order=0),
            ace(run_id, 2, FINANCE_UNC, FINANCE_RW, mask=READ_EXECUTE, flags=0x03, order=1),
        ]
        self.child_aces = [
            ace(run_id, 3, REPORTS_UNC, ADMINISTRATORS, mask=FULL_CONTROL, flags=0x13, order=0),
            ace(run_id, 4, REPORTS_UNC, FINANCE_RW, mask=READ_EXECUTE, flags=0x13, order=1),
        ]
        root = resource(
            run_id,
            0,
            FINANCE_UNC,
            ace_count=2,
            acl_hash=digest(*self.root_aces),
            is_acl_boundary=True,
            boundary_reason="share_root",
        )
        child = resource(
            run_id,
            5,
            REPORTS_UNC,
            ace_count=2,
            acl_hash=digest(*self.child_aces),
            parent_acl_hash=digest(*self.root_aces),
        )
        await replay(
            client, run([root, *self.root_aces, child, *self.child_aces], scope=FINANCE_KEY)
        )

    async def test_the_server_agrees_that_a_clean_child_is_not_a_boundary(
        self, client: AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/resources/" + encoded(REPORTS_UNC))).json()

        assert body["is_acl_boundary"] is False
        assert body["boundary"]["reported"] is False
        assert body["boundary"]["computed"] is False
        assert body["boundary"]["agrees"] is True
        assert body["boundary"]["computed_reason"] is None

    async def test_it_compares_against_the_projection_not_the_parents_own_digest(
        self, client: AsyncClient
    ) -> None:
        # The distinction the whole design turns on. If these were equal, a child that
        # inherited perfectly would be reported as a boundary — and so would every other
        # directory in the estate.
        body = (await client.get("/api/v1/resources/" + encoded(REPORTS_UNC))).json()
        boundary = body["boundary"]

        assert boundary["projected_child_acl_hash"] != boundary["parent_acl_hash"]
        assert boundary["projected_child_acl_hash"] == boundary["resource_acl_hash"]

    async def test_it_finds_the_parent_by_path_rather_than_by_a_stored_link(
        self, client: AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/resources/" + encoded(REPORTS_UNC))).json()

        assert body["parent_path"] == FINANCE_UNC
        assert body["boundary"]["parent_key"] == FINANCE_KEY
        assert body["boundary"]["parent_observed"] is True
        assert body["parent"]["key"] == FINANCE_KEY

    async def test_it_confirms_the_reading_of_the_parent_the_verdict_was_made_against(
        self, client: AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/resources/" + encoded(REPORTS_UNC))).json()

        assert body["boundary"]["reported_parent_acl_hash"] == digest(*self.root_aces)
        assert body["boundary"]["parent_acl_hash_agrees"] is True

    async def test_it_withholds_a_verdict_at_a_share_root(self, client: AsyncClient) -> None:
        # A share root's parent lies outside the share, so there is nothing to compare —
        # which the server can establish from the path alone, and which means the
        # collector's claim is one it never compared either.
        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC))).json()
        boundary = body["boundary"]

        assert boundary["reported"] is True
        assert boundary["reported_reason"] == "share_root"
        assert boundary["computed_reason"] == "share_root"
        assert boundary["parent_key"] is None
        assert boundary["projection_available"] is False
        # Neither side compared anything, so there is nothing to agree about.
        assert boundary["agrees"] is None

    async def test_it_disagrees_out_loud_when_a_child_was_edited(self, client: AsyncClient) -> None:
        # The case that matters: a collector that reported no boundary for a directory whose
        # stored entries do not match what its parent hands down. Left unchecked, the next
        # scan would stop there.
        run_id = str(uuid.uuid4())
        entries = [
            ace(run_id, 1, PAYROLL_UNC, ADMINISTRATORS, mask=FULL_CONTROL, flags=0x13, order=0),
            ace(run_id, 2, PAYROLL_UNC, ALICE, mask=FULL_CONTROL, flags=0x03, order=1),
        ]
        edited = resource(
            run_id, 0, PAYROLL_UNC, ace_count=2, acl_hash=digest(*entries), is_acl_boundary=False
        )
        await replay(client, run([edited, *entries], scope=FINANCE_KEY))

        boundary = (await client.get("/api/v1/resources/" + encoded(PAYROLL_UNC))).json()[
            "boundary"
        ]

        assert boundary["reported"] is False
        assert boundary["computed"] is True
        assert boundary["computed_reason"] == "acl_differs_from_parent"
        assert boundary["agrees"] is False

    async def test_it_reaches_no_verdict_when_the_parent_was_never_read(
        self, client: AsyncClient
    ) -> None:
        # Unknown, never "no boundary". A parent nobody has read cannot settle anything, and
        # a server that answered `false` here would be inventing the one value that lets a
        # later scan stop looking.
        run_id = str(uuid.uuid4())
        entries = [ace(run_id, 1, DETACHED_UNC, ADMINISTRATORS, mask=FULL_CONTROL, flags=0x13)]
        detached = resource(
            run_id,
            0,
            DETACHED_UNC,
            ace_count=1,
            acl_hash=digest(*entries),
            is_acl_boundary=True,
            boundary_reason="scan_root",
        )
        await replay(client, run([detached, *entries], scope=DETACHED_PARENT_KEY))

        body = (await client.get("/api/v1/resources/" + encoded(DETACHED_UNC))).json()
        boundary = body["boundary"]

        assert boundary["parent_key"] == DETACHED_PARENT_KEY
        assert boundary["parent_observed"] is False
        assert boundary["projection_available"] is False
        assert boundary["computed"] is None
        assert boundary["agrees"] is None
        assert body["parent"] is None

    async def test_a_protected_dacl_is_settled_without_the_parent(
        self, client: AsyncClient
    ) -> None:
        await replay(client, storable_document("09-broken-inheritance"))

        response = await client.get("/api/v1/resources/" + encoded(PAYROLL_UNC))
        assert response.status_code == 200, response.text
        boundary = response.json()["boundary"]

        assert boundary["computed_reason"] == "protected_dacl"
        assert boundary["computed"] is True

    async def test_stored_entries_that_disagree_about_position_do_not_fail_the_request(
        self, client: AsyncClient
    ) -> None:
        # An ACE's identity excludes order_index — deliberately, so reordering a DACL does
        # not look like every entry being deleted and recreated — and nothing removes an
        # entry a later scan stopped seeing. So the ACE that used to sit at position 0 and
        # the different one that replaced it both survive, both claiming position 0. The
        # normalizer rightly refuses to hash that, and the server falls back to the
        # unordered form rather than taking a directory's whole ACL off the air.
        run_id = str(uuid.uuid4())
        replacement = [
            ace(run_id, 1, REPORTS_UNC, ALICE, mask=FULL_CONTROL, flags=0x13, order=0),
        ]
        rescanned = resource(run_id, 0, REPORTS_UNC, ace_count=1, acl_hash=digest(*replacement))
        await replay(client, run([rescanned, *replacement], scope=FINANCE_KEY))

        response = await client.get("/api/v1/resources/" + encoded(REPORTS_UNC) + "/acl")
        assert response.status_code == 200, response.text
        body = response.json()

        # Reported plainly rather than papered over: the digest says it could not establish
        # evaluation order, and the entry counts say entries are there that the newest
        # descriptor did not declare.
        assert body["acl_hash"]["ordered"] is False
        assert body["acl_hash"]["agrees"] is False
        assert body["acl_hash"]["ace_count_agrees"] is False

        detail = await client.get("/api/v1/resources/" + encoded(REPORTS_UNC))
        assert detail.status_code == 200, detail.text

    async def test_the_verdict_is_reproducible_from_the_two_acl_responses(
        self, client: AsyncClient
    ) -> None:
        # Everything the server used is on the wire, so a client can check the arithmetic
        # rather than taking the verdict on trust.
        boundary = (await client.get("/api/v1/resources/" + encoded(REPORTS_UNC))).json()[
            "boundary"
        ]
        parent_acl = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC) + "/acl")).json()
        own_acl = (await client.get("/api/v1/resources/" + encoded(REPORTS_UNC) + "/acl")).json()

        assert boundary["parent_acl_hash"] == parent_acl["acl_hash"]["computed"]
        assert boundary["resource_acl_hash"] == own_acl["acl_hash"]["computed"]


class TestAFileResource:
    """Opt-in file scanning, stored and reported as a distinct kind."""

    @pytest.fixture(autouse=True)
    async def stored(self, client: AsyncClient) -> None:
        run_id = str(uuid.uuid4())
        entries = [ace(run_id, 1, BUDGET_UNC, ADMINISTRATORS, mask=FULL_CONTROL, flags=0x10)]
        budget = resource(
            run_id,
            0,
            BUDGET_UNC,
            ace_count=1,
            acl_hash=digest(*entries),
            resource_kind="file",
        )
        await replay(client, run([budget, *entries], scope=FINANCE_KEY))

    async def test_it_is_stored_and_reported_as_a_file(self, client: AsyncClient) -> None:
        body = (await client.get("/api/v1/resources/" + encoded(BUDGET_UNC))).json()
        assert body["resource_kind"] == "file"

    async def test_a_directory_is_reported_as_one_without_being_told(
        self, client: AsyncClient
    ) -> None:
        # Every contract 1.2 payload meant "directory", which is why the column is defaulted
        # rather than nullable: there is a right answer for those rows.
        await replay(client, storable_document("09-broken-inheritance"))
        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC))).json()
        assert body["resource_kind"] == "directory"

    async def test_it_still_belongs_to_the_share_in_its_own_path(self, client: AsyncClient) -> None:
        body = (await client.get("/api/v1/resources/" + encoded(BUDGET_UNC))).json()
        assert body["share_key"] == "fs01|finance"
        assert body["is_share_root"] is False


class TestAPre13Collector:
    """A payload from a collector that predates boundary_reason is still accepted."""

    async def test_a_boundary_with_no_reason_is_stored_as_reported(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # The additive promise: a 1.0 through 1.2 collector sets is_acl_boundary on a share
        # root and has never heard of the reason field. Rejecting it would make the bump
        # breaking, and backfilling a reason would write a verdict nobody made.
        run_id = str(uuid.uuid4())
        legacy = resource(run_id, 0, FINANCE_UNC, ace_count=0, is_acl_boundary=True)
        await replay(client, run([legacy], scope=FINANCE_KEY))

        stored = (
            await session.execute(
                sa.select(ntfs_resources.c.is_acl_boundary, ntfs_resources.c.boundary_reason).where(
                    ntfs_resources.c.resource_key == FINANCE_KEY
                )
            )
        ).one()
        assert stored.is_acl_boundary is True
        assert stored.boundary_reason is None

        body = (await client.get("/api/v1/resources/" + encoded(FINANCE_UNC))).json()
        assert body["is_acl_boundary"] is True
        assert body["boundary_reason"] is None
        # And the server still offers its own derivation, which is more than the collector
        # was able to say.
        assert body["boundary"]["computed_reason"] == "share_root"

    async def test_a_reason_without_a_boundary_is_refused_at_every_version(
        self, client: AsyncClient
    ) -> None:
        run_id = str(uuid.uuid4())
        contradictory = resource(
            run_id, 0, FINANCE_UNC, ace_count=0, is_acl_boundary=False, boundary_reason="share_root"
        )
        document = run([contradictory], scope=FINANCE_KEY)

        await client.post("/api/v1/scan-runs", json=document["start"])
        response = await client.post(
            f"/api/v1/scan-runs/{document['start']['run_id']}/batches", json=document["batches"][0]
        )

        assert response.status_code == 422
        assert "boundary" in response.text.lower()
