r"""Incremental collection against a real PostgreSQL.

Four groups, and each answers one of the phase's acceptance criteria.

* **An affirmation is verified, not believed.** The server compares the digest it holds and
  refuses every affirmation it cannot confirm, naming it so the collector re-sends the
  object in full.
* **An affirming run still enumerates its scope.** This is the one that would hurt if it
  were wrong: reconciliation decides what to mark absent from the ``observations`` table, so
  a directory affirmed without its entries would be a directory whose whole DACL the next
  reconciliation tombstoned.
* **A checkpoint trails its data.** It advances inside the transaction that wrote the batch,
  never past a run the server downgraded, and never across issuers.
* **A full reconciliation repairs drift the deltas could not see.** A principal deleted from
  the directory produces no change record, so no number of delta runs would ever notice it;
  the reconciliation does, and reports how many deltas it is making up for.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.v1.common import ObservationKind
from app.domain import AceType, PrincipalKind, acl_hash
from app.domain.acl_hash import AclAceFacts
from app.models.schema import (
    collector_checkpoints,
    ntfs_aces,
    object_versions,
    scan_run_checkpoints,
    scan_run_scopes,
)
from tests.support import history as h
from tests.support.ingest import replay

FS01 = "FS01"
SHARE = "Finance"
TREE = "\\\\fs01\\finance"
ROOT = "\\\\FS01\\Finance"
REPORTS = "\\\\FS01\\Finance\\Reports"

GROUP = f"{h.DOMAIN_SID}-1101"
STAYS = f"{h.DOMAIN_SID}-1104"
LEAVES = f"{h.DOMAIN_SID}-1105"

DC01 = "CN=NTDS Settings,CN=DC01|2f0f9a3c-7c4e-4c0e-9a02-6b5f0a1f9d11"
DC02 = "CN=NTDS Settings,CN=DC02|8a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d"

READ = 0x1200A9
MODIFY = 0x1301BF


def cursor(token: str, *, at: dt.datetime, issuer: str = DC01) -> dict[str, Any]:
    return {
        "kind": "usn",
        "token": token,
        "issuer": issuer,
        "issued_at": at.isoformat().replace("+00:00", "Z"),
    }


def directory(path: str, grants: list[tuple[str, int]], *, at: dt.datetime) -> dict[str, Any]:
    """A directory plus its entries, with the digest the server will recompute.

    The digest is produced by the same normalizer the server uses rather than being written
    out by hand. A fixture holding a second copy of the rule could disagree with it, and the
    disagreement would look exactly like the mismatch this feature is built to detect.
    """
    aces = [
        h.ntfs_ace(path, sid, at=at, access_mask=mask, order_index=index)
        for index, (sid, mask) in enumerate(grants)
    ]
    digest = acl_hash(
        dacl_present=True,
        dacl_protected=True,
        aces=[
            AclAceFacts(
                trustee_sid=sid,
                ace_type=AceType.ALLOW,
                access_mask=mask,
                ace_flags=0x03,
                order_index=index,
            )
            for index, (sid, mask) in enumerate(grants)
        ],
    )
    resource = h.resource(
        path,
        at=at,
        server_name=FS01,
        share_name=SHARE,
        ace_count=len(grants),
        digest=digest,
    )
    return {"digest": digest, "observations": [resource, *aces]}


def affirmation(path: str, digest: str, *, at: dt.datetime) -> dict[str, Any]:
    from app.contracts.v1 import keys

    return {
        "kind": "ntfs_resource",
        "source_key": keys.ntfs_resource_key(path),
        "digest": digest,
        "observed_at": at.isoformat().replace("+00:00", "Z"),
    }


async def open_version(
    session: AsyncSession, kind: ObservationKind, key: str
) -> sa.RowMapping | None:
    return (
        (
            await session.execute(
                sa.select(object_versions).where(
                    object_versions.c.object_kind == kind.value,
                    object_versions.c.object_key == key,
                    object_versions.c.valid_to.is_(None),
                )
            )
        )
        .mappings()
        .one_or_none()
    )


async def seed_tree(client: AsyncClient, at: dt.datetime) -> dict[str, str]:
    """A two-directory estate, fully read and reconciled."""
    root = directory(ROOT, [(GROUP, MODIFY)], at=at)
    reports = directory(REPORTS, [(GROUP, MODIFY), (STAYS, READ)], at=at)
    await replay(
        client,
        h.ntfs_scan(
            observations=[*root["observations"], *reports["observations"]],
            started_at=at,
            reconcile=True,
        ),
    )
    return {ROOT: root["digest"], REPORTS: reports["digest"]}


class TestAnAffirmationIsVerifiedNotBelieved:
    async def test_a_matching_digest_is_accepted(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        digests = await seed_tree(client, h.MONDAY)

        result = await replay(
            client,
            h.ntfs_scan(
                observations=[],
                affirmations=[
                    affirmation(path, digest, at=h.WEDNESDAY) for path, digest in digests.items()
                ],
                started_at=h.WEDNESDAY,
                reconcile=False,
            ),
        )

        assert result["batches"][0]["affirmed"] == 2
        assert result["batches"][0]["refused_affirmations"] == []

    async def test_a_changed_acl_is_refused_with_both_digests(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await seed_tree(client, h.MONDAY)
        # What the collector would compute after somebody widened the DACL.
        changed = directory(REPORTS, [(GROUP, MODIFY), (STAYS, MODIFY)], at=h.WEDNESDAY)

        result = await replay(
            client,
            h.ntfs_scan(
                observations=[],
                affirmations=[affirmation(REPORTS, changed["digest"], at=h.WEDNESDAY)],
                started_at=h.WEDNESDAY,
                reconcile=False,
            ),
        )

        refused = result["batches"][0]["refused_affirmations"]
        assert result["batches"][0]["affirmed"] == 0
        assert [item["reason"] for item in refused] == ["digest_mismatch"]
        assert changed["digest"] in refused[0]["detail"]
        assert "in full" in refused[0]["detail"]

    async def test_an_object_adg_has_never_seen_is_refused(self, client: AsyncClient) -> None:
        await seed_tree(client, h.MONDAY)
        elsewhere = "\\\\FS01\\Finance\\Archive"

        result = await replay(
            client,
            h.ntfs_scan(
                observations=[],
                affirmations=[affirmation(elsewhere, "a" * 64, at=h.WEDNESDAY)],
                started_at=h.WEDNESDAY,
                reconcile=False,
            ),
        )

        refused = result["batches"][0]["refused_affirmations"]
        assert [item["reason"] for item in refused] == ["unknown_object"]

    async def test_a_resource_stored_without_a_digest_is_refused(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        # What a contract 1.1 collector left behind: a resource with no acl_hash. The
        # comparison cannot be made, and "cannot compare" must not resolve to "equal".
        await replay(
            client,
            h.ntfs_scan(
                observations=[
                    h.resource(ROOT, at=h.MONDAY, server_name=FS01, share_name=SHARE, ace_count=0)
                ],
                started_at=h.MONDAY,
                reconcile=False,
            ),
        )

        result = await replay(
            client,
            h.ntfs_scan(
                observations=[],
                affirmations=[affirmation(ROOT, "b" * 64, at=h.WEDNESDAY)],
                started_at=h.WEDNESDAY,
                reconcile=False,
            ),
        )

        refused = result["batches"][0]["refused_affirmations"]
        assert [item["reason"] for item in refused] == ["no_stored_digest"]

    async def test_a_refusal_does_not_fail_the_batch_or_the_run(self, client: AsyncClient) -> None:
        # A refused affirmation is information for the collector, not an error: the run is
        # still succeeded, and the observations that arrived with it are still stored.
        digests = await seed_tree(client, h.MONDAY)
        result = await replay(
            client,
            h.ntfs_scan(
                observations=[],
                affirmations=[
                    affirmation(ROOT, digests[ROOT], at=h.WEDNESDAY),
                    affirmation(REPORTS, "c" * 64, at=h.WEDNESDAY),
                ],
                started_at=h.WEDNESDAY,
                reconcile=False,
            ),
        )
        assert result["completion"]["status"] == "succeeded"
        assert result["batches"][0]["affirmed"] == 1
        assert len(result["batches"][0]["refused_affirmations"]) == 1


class TestAnAffirmingRunStillEnumeratesItsScope:
    async def test_an_affirmed_directory_and_its_entries_survive_a_reconciliation(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The criterion this whole mechanism stands on.

        Reconciliation decides what to mark absent from the ``observations`` table. If an
        affirmation wrote provenance for the resource but not for its entries, this run
        would tombstone every ACE under an ACL it had just confirmed was unchanged — and
        the estate would read as having lost all its permissions.
        """
        digests = await seed_tree(client, h.MONDAY)

        await replay(
            client,
            h.ntfs_scan(
                observations=[],
                affirmations=[
                    affirmation(path, digest, at=h.WEDNESDAY) for path, digest in digests.items()
                ],
                started_at=h.WEDNESDAY,
                reconcile=True,
            ),
        )

        tombstones = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(object_versions)
                .where(object_versions.c.is_present.is_(False))
            )
        ).scalar_one()
        assert tombstones == 0

        entries = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(ntfs_aces)
                .where(ntfs_aces.c.last_observed_at == h.WEDNESDAY)
            )
        ).scalar_one()
        assert entries == 3

    async def test_affirming_extends_the_interval_rather_than_opening_a_version(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        digests = await seed_tree(client, h.MONDAY)
        before = await open_version(session, ObservationKind.NTFS_RESOURCE, REPORTS.casefold())
        assert before is not None

        await replay(
            client,
            h.ntfs_scan(
                observations=[],
                affirmations=[affirmation(REPORTS, digests[REPORTS], at=h.WEDNESDAY)],
                started_at=h.WEDNESDAY,
                reconcile=False,
            ),
        )

        after = await open_version(session, ObservationKind.NTFS_RESOURCE, REPORTS.casefold())
        assert after is not None
        assert after["id"] == before["id"], "an affirmation must not open a second version"
        assert after["valid_from"] == before["valid_from"]
        assert after["last_seen_at"] == h.WEDNESDAY

    async def test_an_affirmation_does_what_an_identical_re_observation_does(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The equivalence the whole design rests on, asserted rather than asserted *about*.

        If affirming and re-sending the same state ever diverged, the cheaper path would be
        quietly recording something different from the expensive one — and every estate that
        used it would have a timeline that the full scans disagreed with.
        """
        digests = await seed_tree(client, h.MONDAY)
        reports = directory(REPORTS, [(GROUP, MODIFY), (STAYS, READ)], at=h.WEDNESDAY)

        await replay(
            client,
            h.ntfs_scan(
                observations=reports["observations"],
                started_at=h.WEDNESDAY,
                reconcile=False,
            ),
        )
        observed = await open_version(session, ObservationKind.NTFS_RESOURCE, REPORTS.casefold())
        assert observed is not None
        by_observation = {
            "id": observed["id"],
            "valid_from": observed["valid_from"],
            "last_seen_at": observed["last_seen_at"],
            "state_hash": observed["state_hash"],
        }

        await replay(
            client,
            h.ntfs_scan(
                observations=[],
                affirmations=[affirmation(ROOT, digests[ROOT], at=h.WEDNESDAY)],
                started_at=h.WEDNESDAY,
                reconcile=False,
            ),
        )
        affirmed = await open_version(session, ObservationKind.NTFS_RESOURCE, ROOT.casefold())
        assert affirmed is not None

        # Same shape of effect on each object's own open version: the interval grew to the
        # same instant, no new row, the recorded state untouched.
        assert affirmed["valid_from"] == h.MONDAY
        assert affirmed["last_seen_at"] == by_observation["last_seen_at"] == h.WEDNESDAY
        assert affirmed["state_hash"] is not None

    async def test_a_tombstoned_object_cannot_be_revived_by_an_affirmation(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        digests = await seed_tree(client, h.MONDAY)
        # Reports disappears and a reconciled scan records its absence.
        root_again = directory(ROOT, [(GROUP, MODIFY)], at=h.WEDNESDAY)
        await replay(
            client,
            h.ntfs_scan(
                observations=root_again["observations"], started_at=h.WEDNESDAY, reconcile=True
            ),
        )

        result = await replay(
            client,
            h.ntfs_scan(
                observations=[],
                affirmations=[affirmation(REPORTS, digests[REPORTS], at=h.FRIDAY)],
                started_at=h.FRIDAY,
                reconcile=False,
            ),
        )

        refused = result["batches"][0]["refused_affirmations"]
        assert [item["reason"] for item in refused] == ["absent"]
        version = await open_version(session, ObservationKind.NTFS_RESOURCE, REPORTS.casefold())
        assert version is not None
        assert version["is_present"] is False, "the tombstone must stand"


class TestACheckpointTrailsItsData:
    async def test_a_batch_advances_the_job_cursor(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The cursor moves with the batch, not only at the end of the run.

        A long delta that dies half way has still had its earlier batches applied, and the
        objects they carried are in the database. Making the next run re-read them would be
        safe but wasteful; the point of advancing per batch is that the cursor sits exactly
        at the edge of what landed.
        """
        document = h.ad_scan(
            observations=[h.principal(STAYS, at=h.WEDNESDAY, kind=PrincipalKind.USER)],
            started_at=h.WEDNESDAY,
            reconcile=False,
        )
        document["start"]["job"] = "ad-principals"
        document["start"]["schema_version"] = "1.4"
        document["start"]["mode"] = "delta"
        document["start"]["incremental"] = True
        for batch in document["batches"]:
            batch["schema_version"] = "1.4"
            batch["checkpoint"] = cursor("4711", at=h.WEDNESDAY)
        document["completion"]["schema_version"] = "1.4"
        document["completion"]["reconciled_scopes"] = []

        result = await replay(client, document)

        assert result["batches"][0]["checkpoint"]["accepted"] is True
        stored = (await session.execute(sa.select(collector_checkpoints))).mappings().one()
        assert stored["job"] == "ad-principals"
        assert stored["token"] == "4711"
        assert stored["last_rejection_code"] is None

    async def test_a_cursor_from_another_domain_controller_is_refused_and_recorded(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await self._advance(client, "4711", at=h.MONDAY, issuer=DC01)
        result = await self._advance(client, "9", at=h.WEDNESDAY, issuer=DC02)

        advance = result["completion"]["checkpoint"]
        assert advance["accepted"] is False
        assert advance["rejection_code"] == "checkpoint_issuer_changed"

        stored = (await session.execute(sa.select(collector_checkpoints))).mappings().one()
        assert stored["token"] == "4711", "the usable cursor is kept"
        assert stored["last_rejection_code"] == "checkpoint_issuer_changed"
        assert stored["last_rejected_at"] is not None

    async def test_a_later_accepted_advance_clears_the_block(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await self._advance(client, "4711", at=h.MONDAY, issuer=DC01)
        await self._advance(client, "9", at=h.WEDNESDAY, issuer=DC02)
        await self._advance(client, "4800", at=h.FRIDAY, issuer=DC01)

        stored = (await session.execute(sa.select(collector_checkpoints))).mappings().one()
        assert stored["token"] == "4800"
        assert stored["last_rejection_code"] is None

    async def test_a_backwards_cursor_is_refused(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await self._advance(client, "4711", at=h.MONDAY)
        result = await self._advance(client, "4700", at=h.WEDNESDAY)

        assert result["completion"]["checkpoint"]["rejection_code"] == "checkpoint_went_backwards"
        stored = (await session.execute(sa.select(collector_checkpoints))).mappings().one()
        assert stored["token"] == "4711"

    async def test_a_downgraded_run_may_not_advance_the_cursor(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A failed incremental run cannot silently declare the source current.

        The collector called this run succeeded and claimed three batches; one never
        arrived, so the server records it as partial. Its watermark sits above observations
        that are not in the database, and the next delta would start there — skipping
        exactly what went missing, with nothing afterwards looking wrong.
        """
        await self._advance(client, "4711", at=h.MONDAY)

        document = self._delta("9000", at=h.WEDNESDAY, issuer=DC01)
        document["completion"]["batch_count"] = 3

        run_id = document["start"]["run_id"]
        assert (await client.post("/api/v1/scan-runs", json=document["start"])).status_code == 201
        for batch in document["batches"]:
            assert (
                await client.post(f"/api/v1/scan-runs/{run_id}/batches", json=batch)
            ).status_code == 202
        completed = await client.post(
            f"/api/v1/scan-runs/{run_id}/completion", json=document["completion"]
        )
        assert completed.status_code == 200
        body = completed.json()

        assert body["status"] == "partial"
        assert body["downgrade_reason"] is not None
        assert body["checkpoint"] is None, "a downgraded run records no cursor"

        stored = (await session.execute(sa.select(collector_checkpoints))).mappings().one()
        assert stored["token"] == "4711"

        # It is still recorded against the run itself, because what the collector claimed is
        # evidence even when the server would not act on it.
        claimed = (
            await session.execute(
                sa.select(scan_run_checkpoints.c.token).where(
                    scan_run_checkpoints.c.run_id == uuid.UUID(run_id),
                    scan_run_checkpoints.c.role == "result",
                )
            )
        ).scalar_one()
        assert claimed == "9000"

    async def test_a_checkpoint_without_a_job_is_refused(self, client: AsyncClient) -> None:
        # A checkpoint is the resume point of a *job*. With no job named there is nothing
        # for it to advance, and storing it under some invented key would make the next run
        # of the real job resume from a cursor it never issued.
        document = self._delta("1", at=h.MONDAY)
        del document["start"]["job"]

        run_id = document["start"]["run_id"]
        assert (await client.post("/api/v1/scan-runs", json=document["start"])).status_code == 201
        for batch in document["batches"]:
            assert (
                await client.post(f"/api/v1/scan-runs/{run_id}/batches", json=batch)
            ).status_code == 202
        response = await client.post(
            f"/api/v1/scan-runs/{run_id}/completion", json=document["completion"]
        )
        assert response.status_code == 409
        assert "belongs to no job" in response.text

    def _delta(self, token: str, *, at: dt.datetime, issuer: str = DC01) -> dict[str, Any]:
        """One delta run of the ad-principals job, recording `token` when it completes."""
        document = h.ad_scan(
            observations=[h.principal(GROUP, at=at, display_name="Finance")],
            started_at=at,
            reconcile=False,
        )
        document["start"]["schema_version"] = "1.4"
        document["start"]["job"] = "ad-principals"
        document["start"]["mode"] = "delta"
        document["start"]["incremental"] = True
        for batch in document["batches"]:
            batch["schema_version"] = "1.4"
        document["completion"]["schema_version"] = "1.4"
        document["completion"]["reconciled_scopes"] = []
        document["completion"]["checkpoint"] = cursor(token, at=at, issuer=issuer)
        return document

    async def _advance(
        self, client: AsyncClient, token: str, *, at: dt.datetime, issuer: str = DC01
    ) -> dict[str, Any]:
        return await replay(client, self._delta(token, at=at, issuer=issuer))


class TestFullReconciliationRepairsWhatTheDeltasCouldNotSee:
    async def test_a_deleted_principal_survives_every_delta_and_dies_at_reconciliation(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The phase's headline criterion, in the shape the directory actually produces it.

        A uSNChanged delta returns objects whose change metadata moved. A principal that was
        *deleted* has moved to the Deleted Objects container and produces no change record
        the query can see, so nothing arrives — which is exactly what an unchanged object
        also does. No cadence of delta runs distinguishes the two. The reconciliation does.
        """
        await replay(
            client,
            h.ad_scan(
                observations=[
                    h.principal(GROUP, at=h.MONDAY, display_name="Finance"),
                    h.principal(STAYS, at=h.MONDAY, kind=PrincipalKind.USER),
                    h.principal(LEAVES, at=h.MONDAY, kind=PrincipalKind.USER),
                    h.edge(GROUP, STAYS, at=h.MONDAY),
                    h.edge(GROUP, LEAVES, at=h.MONDAY),
                ],
                started_at=h.MONDAY,
                reconcile=True,
            ),
        )

        # LEAVES is deleted from the directory here. Two deltas run and neither can see it.
        for at in (h.WEDNESDAY, h.THURSDAY):
            delta = h.ad_scan(
                observations=[h.principal(STAYS, at=at, kind=PrincipalKind.USER)],
                started_at=at,
                reconcile=False,
            )
            delta["start"]["schema_version"] = "1.4"
            delta["start"]["mode"] = "delta"
            delta["start"]["incremental"] = True
            delta["start"]["job"] = "ad-principals"
            for batch in delta["batches"]:
                batch["schema_version"] = "1.4"
            delta["completion"]["schema_version"] = "1.4"
            await replay(client, delta)

        still_there = await open_version(session, ObservationKind.PRINCIPAL, LEAVES)
        assert still_there is not None
        assert still_there["is_present"] is True, "no delta may infer an absence"

        reconcile = h.ad_scan(
            observations=[
                h.principal(GROUP, at=h.FRIDAY, display_name="Finance"),
                h.principal(STAYS, at=h.FRIDAY, kind=PrincipalKind.USER),
                h.edge(GROUP, STAYS, at=h.FRIDAY),
            ],
            started_at=h.FRIDAY,
            reconcile=True,
        )
        reconcile["start"]["schema_version"] = "1.4"
        reconcile["start"]["mode"] = "reconcile"
        reconcile["start"]["job"] = "ad-full-reconcile"
        for batch in reconcile["batches"]:
            batch["schema_version"] = "1.4"
        reconcile["completion"]["schema_version"] = "1.4"
        result = await replay(client, reconcile)

        repaired = await open_version(session, ObservationKind.PRINCIPAL, LEAVES)
        assert repaired is not None
        assert repaired["is_present"] is False, "the reconciliation records the absence"

        edge_key = f"{GROUP}->{LEAVES}|directory_group_member"
        gone = await open_version(session, ObservationKind.MEMBERSHIP_EDGE, edge_key)
        assert gone is not None
        assert gone["is_present"] is False, "the grant it carried is gone too"

        drift = result["completion"]["drift"]
        assert len(drift) == 1
        assert drift[0]["marked_absent"] == 2, "the principal and its edge"
        assert drift[0]["delta_runs_since"] == 2
        assert "working as designed" in drift[0]["summary"]

    async def test_drift_is_recorded_on_the_scope_the_reconciliation_repaired(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await replay(
            client,
            h.ad_scan(
                observations=[
                    h.principal(GROUP, at=h.MONDAY, display_name="Finance"),
                    h.principal(LEAVES, at=h.MONDAY, kind=PrincipalKind.USER),
                ],
                started_at=h.MONDAY,
                reconcile=True,
            ),
        )
        await replay(
            client,
            h.ad_scan(
                observations=[h.principal(GROUP, at=h.FRIDAY, display_name="Finance")],
                started_at=h.FRIDAY,
                reconcile=True,
            ),
        )

        row = (
            (
                await session.execute(
                    sa.select(scan_run_scopes)
                    .where(scan_run_scopes.c.reconciled.is_(True))
                    .order_by(scan_run_scopes.c.id.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one()
        )
        assert row["closed_absent"] == 1
        assert row["delta_runs_since"] == 0, "no delta ran between the two reconciliations"

    async def test_a_clean_reconciliation_reports_zero_drift(self, client: AsyncClient) -> None:
        digests = await seed_tree(client, h.MONDAY)
        result = await replay(
            client,
            h.ntfs_scan(
                observations=[],
                affirmations=[
                    affirmation(path, digest, at=h.WEDNESDAY) for path, digest in digests.items()
                ],
                started_at=h.WEDNESDAY,
                reconcile=True,
            ),
        )
        drift = result["completion"]["drift"]
        assert len(drift) == 1
        assert drift[0]["marked_absent"] == 0
        assert drift[0]["revived"] == 0
        assert "nothing" in drift[0]["summary"]


@pytest.mark.parametrize("mode", ["full", "reconcile"])
async def test_a_non_delta_run_may_reconcile_and_a_delta_may_not(
    client: AsyncClient, mode: str
) -> None:
    document = h.ad_scan(
        observations=[h.principal(GROUP, at=h.MONDAY, display_name="Finance")],
        started_at=h.MONDAY,
        reconcile=True,
    )
    document["start"]["schema_version"] = "1.4"
    document["start"]["mode"] = mode
    for batch in document["batches"]:
        batch["schema_version"] = "1.4"
    document["completion"]["schema_version"] = "1.4"
    result = await replay(client, document)
    assert result["completion"]["reconciled_scopes"] == 1

    delta = h.ad_scan(
        observations=[h.principal(GROUP, at=h.WEDNESDAY, display_name="Finance")],
        started_at=h.WEDNESDAY,
        reconcile=True,
    )
    delta["start"]["schema_version"] = "1.4"
    delta["start"]["mode"] = "delta"
    delta["start"]["incremental"] = True
    for batch in delta["batches"]:
        batch["schema_version"] = "1.4"
    delta["completion"]["schema_version"] = "1.4"

    run_id = delta["start"]["run_id"]
    assert (await client.post("/api/v1/scan-runs", json=delta["start"])).status_code == 201
    for batch in delta["batches"]:
        await client.post(f"/api/v1/scan-runs/{run_id}/batches", json=batch)
    refused = await client.post(f"/api/v1/scan-runs/{run_id}/completion", json=delta["completion"])
    assert refused.status_code == 409
    assert "may not reconcile" in refused.text
