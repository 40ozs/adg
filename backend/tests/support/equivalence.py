r"""A controlled estate that can be collected, simulated against, changed, and recollected.

This is the apparatus behind Phase 9B's equivalence validation: the thing that lets a test
say *"ADG predicted Bob would lose Modify; we then made that change for real, scanned again,
and Bob has lost Modify"*. Without it, a simulation is checked only against the engine that
produced it, which proves consistency and nothing about correctness.

## The five steps, and why each is what it is

1. **A known permission state.** :func:`estate` builds a complete scan-run transcript from a
   compact description — principals, memberships, a share, a directory, both ACLs. A
   purpose-built estate rather than one of the canonical scenarios, because an equivalence
   test has to be able to say exactly which fact it changed, and the canonical scenarios are
   shaped for other questions.

2. **A simulation.** The proposal is built from the same vocabulary the API accepts, and run
   by the production :class:`app.simulation.SimulationService`.

3. **The equivalent change, applied fixture-side.** :class:`ControlledEstate` carries the
   mutators — :meth:`without_membership`, :meth:`with_ntfs_ace`, :meth:`protected`, and the
   rest — and each one edits the *observation set* the way Windows would have changed the
   object. The ACE identity keys and the descriptor's ``ace_count`` are recomputed from the
   estate's own domain functions rather than hand-written, so an edit cannot produce a
   transcript no collector would have sent.

4. **A recollection.** :meth:`ControlledEstate.collector_runs` splits the estate into the
   three runs a real deployment produces — an ``active_directory`` run reconciling its domain,
   an ``smb`` run reconciling its server, an ``ntfs`` run reconciling its directory tree —
   because **reconciliation is per collector and per scope** (:data:`app.history.closure.
   CLOSURE_RULES`). One run claiming all three would be a collector marking absent what it
   is structurally incapable of seeing, and ADG refuses to infer absence from it. Splitting
   is not a convenience here: without it a removal is never *measured* as a removal and the
   comparison cannot see one.

5. **The comparison.** :func:`observe` reads the post-change answer two ways, and reporting
   both is the point:

   * **``as_of``** — the point-in-time engine at the recollection instant, which routes every
     read through ``object_versions`` and therefore excludes what a reconciled scan proved
     gone. This is the reading a removal must be judged against, and it is the reference.
   * **``live``** — the ordinary current-state engine, which reads the ACE and edge tables
     directly. ADG deletes nothing on ingestion (by design: an absent observation is not
     evidence of removal), and current-state reads are not routed through presence, so a
     live answer still counts a grant a reconciled scan has proved is gone. That is Phase
     7A's limitation 1 and Phase 9A's limitation 6.

   Both are recorded so the second is *measured* rather than asserted in a document.
   ``tests/db/test_simulation_equivalence.py`` pins the divergence explicitly.
"""

from __future__ import annotations

import copy
import datetime as dt
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_engine import AccessCertainty, AccessPath
from app.domain import AceFlag, AceType, SharePermission, Sid
from app.domain.access import SmbShareAce, ntfs_ace_identity_key, share_ace_identity_key
from app.history.service import HistoryService
from app.repositories import MembershipRepository, ResourceRepository
from app.services.access import AccessService
from app.simulation import AccessDelta, SimulationReport
from tests.support.ingest import replay

__all__ = [
    "AceSpec",
    "Comparison",
    "ControlledEstate",
    "EdgeSpec",
    "ObservedAccess",
    "PrincipalSpec",
    "ShareAceSpec",
    "compare",
    "estate",
    "observe",
    "recollect",
]

SCHEMA_VERSION = "1.0"
BASE_INSTANT = dt.datetime(2026, 3, 1, 8, 0, 0, tzinfo=dt.UTC)

READ_EXECUTE = 0x001200A9
MODIFY = 0x001301BF
FULL_CONTROL = 0x001F01FF

INHERITABLE = int(AceFlag.OBJECT_INHERIT | AceFlag.CONTAINER_INHERIT)


# ------------------------------------------------------------------ the description


@dataclass(frozen=True, slots=True)
class PrincipalSpec:
    sid: str
    kind: str = "user"
    display_name: str = ""


@dataclass(frozen=True, slots=True)
class EdgeSpec:
    group_sid: str
    member_sid: str
    edge_kind: str = "directory_group_member"
    member_kind: str = "user"


@dataclass(frozen=True, slots=True)
class AceSpec:
    """One NTFS entry, described the way a collector reports it."""

    trustee_sid: str
    ace_type: str = "allow"
    access_mask: int = READ_EXECUTE
    ace_flags: int = INHERITABLE
    source: str = "explicit"
    order_index: int = 0


@dataclass(frozen=True, slots=True)
class ShareAceSpec:
    trustee_sid: str
    ace_type: str = "allow"
    permission: str | None = "full"
    access_mask: int | None = None
    order_index: int = 0


@dataclass(frozen=True, slots=True)
class ControlledEstate:
    r"""A complete, known permission state, and the edits Windows could make to it.

    Immutable: every mutator returns a new estate. Two estates are then two states of one
    world, which is exactly what an equivalence test compares — and a mutator that edited in
    place would make the "before" transcript unavailable the moment the "after" was built.
    """

    server: str = "FS01"
    share: str = "Finance"
    domain_sid: str = "S-1-5-21-1004336348-1177238915-682003330"
    dns_domain: str = "corp.example.com"
    local_path: str = "D:\\Shares\\Finance"
    principals: tuple[PrincipalSpec, ...] = ()
    edges: tuple[EdgeSpec, ...] = ()
    share_aces: tuple[ShareAceSpec, ...] = ()
    ntfs_aces: tuple[AceSpec, ...] = ()
    dacl_protected: bool = False
    inheritance_enabled: bool = True
    generation: int = 0
    """Bumped by every mutator. Stamps the observation instants so a recollected run is
    strictly newer than the one before it, which is what the newest-wins guard on every
    upsert compares."""

    # ------------------------------------------------------------------- identity

    @property
    def share_key(self) -> str:
        return f"{self.server.casefold()}|{self.share.casefold()}"

    @property
    def path(self) -> str:
        return f"\\\\{self.server}\\{self.share}"

    @property
    def resource_key(self) -> str:
        return self.path.casefold()

    def ntfs_ace_key(self, ace: AceSpec) -> str:
        """The stored key for one entry, from the domain's own derivation.

        Computed rather than spelled out, because a proposal that names an ACE key matches on
        it exactly: a hand-written key that is one flag byte out matches nothing, and the
        simulation reports ``target_not_found`` for a change the test believes it made.
        """
        return ntfs_ace_identity_key(
            self.resource_key,
            Sid(ace.trustee_sid),
            AceType(ace.ace_type),
            ace.access_mask,
            AceFlag(ace.ace_flags),
        )

    def share_ace_key(self, ace: ShareAceSpec) -> str:
        """The stored key for one share entry, from the domain's own derivation."""
        domain = SmbShareAce(
            trustee_sid=Sid(ace.trustee_sid),
            ace_type=AceType(ace.ace_type),
            permission=None if ace.permission is None else SharePermission(ace.permission),
            access_mask=ace.access_mask,
            order_index=ace.order_index,
        )
        return share_ace_identity_key(
            self.share_key, domain.trustee_sid, domain.ace_type, domain.right_token
        )

    # ------------------------------------------------------------------ mutators

    def with_membership(self, edge: EdgeSpec) -> ControlledEstate:
        """Put a principal into a group, as a collector would next observe it."""
        return replace(self, edges=(*self.edges, edge), generation=self.generation + 1)

    def without_membership(
        self, group_sid: str, member_sid: str, edge_kind: str = "directory_group_member"
    ) -> ControlledEstate:
        """Take a principal out of a group: the edge simply stops being observed."""
        kept = tuple(
            edge
            for edge in self.edges
            if (edge.group_sid, edge.member_sid, edge.edge_kind)
            != (group_sid, member_sid, edge_kind)
        )
        return replace(self, edges=kept, generation=self.generation + 1)

    def with_ntfs_ace(self, ace: AceSpec) -> ControlledEstate:
        return replace(self, ntfs_aces=_placed(self.ntfs_aces, ace), generation=self.generation + 1)

    def without_ntfs_ace(self, ace: AceSpec) -> ControlledEstate:
        key = self.ntfs_ace_key(ace)
        kept = tuple(item for item in self.ntfs_aces if self.ntfs_ace_key(item) != key)
        return replace(self, ntfs_aces=_renumbered(kept), generation=self.generation + 1)

    def with_modified_ntfs_ace(self, ace: AceSpec, **changes: Any) -> ControlledEstate:
        """Change one entry in place, keeping its position.

        Windows has no "edit an ACE" primitive at the identity level — the entry's mask is
        part of what identifies it in ADG — so an edit is a removal and an addition at the
        same position, which is what this does and what the simulation's
        ``modify_ntfs_ace`` models.
        """
        key = self.ntfs_ace_key(ace)
        edited = tuple(
            replace(item, **changes) if self.ntfs_ace_key(item) == key else item
            for item in self.ntfs_aces
        )
        return replace(self, ntfs_aces=edited, generation=self.generation + 1)

    def with_share_ace(self, ace: ShareAceSpec) -> ControlledEstate:
        return replace(self, share_aces=(*self.share_aces, ace), generation=self.generation + 1)

    def without_share_ace(self, ace: ShareAceSpec) -> ControlledEstate:
        key = self.share_ace_key(ace)
        kept = tuple(item for item in self.share_aces if self.share_ace_key(item) != key)
        return replace(self, share_aces=kept, generation=self.generation + 1)

    def protected(self, *, keep_inherited: bool = True) -> ControlledEstate:
        """Set ``SE_DACL_PROTECTED`` on the directory.

        ``keep_inherited`` is the dialog box Windows shows: convert the inherited entries to
        explicit ones, or drop them. This estate's directory is a share root with no parent
        in ADG, so nothing is inherited and the two answers coincide — which is itself worth
        a test, because the *simulation* must reach the same conclusion rather than inventing
        a projection from a parent it has never read.
        """
        aces = self.ntfs_aces
        if not keep_inherited:
            aces = tuple(item for item in aces if item.source != "inherited")
        return replace(
            self,
            ntfs_aces=_renumbered(aces),
            dacl_protected=True,
            inheritance_enabled=False,
            generation=self.generation + 1,
        )

    # -------------------------------------------------------------- transcripts

    def collector_runs(self) -> list[dict[str, Any]]:
        """This estate as the three scan-run transcripts a real deployment produces.

        Three, and not one, because reconciliation is keyed by ``(collector, scope kind)``:
        only an ``active_directory`` run reconciling a ``domain`` may mark a membership edge
        absent, only an ``smb`` run reconciling a ``server`` may mark a share ACE absent, and
        only an ``ntfs`` run reconciling a ``directory_tree`` may mark an NTFS entry absent.
        A single run claiming all three scopes is one collector deleting another's facts, and
        ADG will not do it.
        """
        return [self._ad_run(), self._smb_run(), self._ntfs_run()]

    def _instant(self, offset: int) -> str:
        moment = BASE_INSTANT + dt.timedelta(hours=self.generation, seconds=offset)
        return moment.isoformat().replace("+00:00", "Z")

    def _run(
        self,
        collector: str,
        method: str,
        scopes: list[dict[str, str]],
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        for item in observations:
            item["run_id"] = run_id
            item["schema_version"] = SCHEMA_VERSION
        batch_id = str(uuid.uuid4())
        return {
            "start": {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "source": {
                    "collector": collector,
                    "collector_host": "COLLECTOR01",
                    "method": method,
                    "collector_version": "0.1.0",
                    "target": self.path if collector == "ntfs" else self.server,
                },
                "started_at": self._instant(0),
                "scopes": scopes,
                "incremental": False,
            },
            "batches": [
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "batch_id": batch_id,
                    "sequence": 1,
                    "observations": observations,
                }
            ],
            "completion": {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "status": "succeeded",
                "completed_at": self._instant(600),
                "batch_count": 1,
                "observation_count": len(observations),
                "error_count": 0,
                "errors": [],
                "reconciled_scopes": scopes,
            },
        }

    def _ad_run(self) -> dict[str, Any]:
        observations: list[dict[str, Any]] = []
        for index, principal in enumerate(self.principals):
            observations.append(
                {
                    "kind": "principal",
                    "observed_at": self._instant(1 + index),
                    "source_key": f"principal|{principal.sid}",
                    "sid": principal.sid,
                    "principal_kind": principal.kind,
                    "display_name": principal.display_name or principal.sid,
                    "enabled": True,
                    "is_deleted": False,
                }
            )
        for index, edge in enumerate(self.edges):
            observations.append(
                {
                    "kind": "membership_edge",
                    "observed_at": self._instant(100 + index),
                    "source_key": (f"edge|{edge.group_sid}->{edge.member_sid}|{edge.edge_kind}"),
                    "group_sid": edge.group_sid,
                    "member_sid": edge.member_sid,
                    "edge_kind": edge.edge_kind,
                    "member_kind": edge.member_kind,
                    "is_foreign_security_principal": False,
                }
            )
        return self._run(
            "active_directory",
            "Get-ADObject",
            [{"kind": "domain", "key": self.dns_domain}],
            observations,
        )

    def _smb_run(self) -> dict[str, Any]:
        observations: list[dict[str, Any]] = [
            {
                "kind": "server",
                "observed_at": self._instant(1),
                "source_key": f"server|{self.server.casefold()}",
                "name": self.server,
                "dns_host_name": f"{self.server.casefold()}.{self.dns_domain}",
                "is_domain_member": True,
            },
            {
                "kind": "smb_share",
                "observed_at": self._instant(2),
                "source_key": f"share|{self.share_key}",
                "server_name": self.server,
                "share_name": self.share,
                "local_path": self.local_path,
                "share_type": "disk",
            },
        ]
        for index, ace in enumerate(self.share_aces):
            entry: dict[str, Any] = {
                "kind": "smb_ace",
                "observed_at": self._instant(10 + index),
                "source_key": (
                    f"smb_ace|{self.share_key}|{ace.trustee_sid}|{ace.ace_type}"
                    f"|{ace.permission or ace.access_mask}"
                ),
                "server_name": self.server,
                "share_name": self.share,
                "trustee_sid": ace.trustee_sid,
                "ace_type": ace.ace_type,
                "order_index": index,
            }
            if ace.permission is not None:
                entry["permission"] = ace.permission
            else:
                entry["access_mask"] = ace.access_mask
            observations.append(entry)
        return self._run(
            "smb",
            "Get-SmbShareAccess",
            [{"kind": "server", "key": self.server.casefold()}],
            observations,
        )

    def _ntfs_run(self) -> dict[str, Any]:
        aces = _renumbered(self.ntfs_aces)
        observations: list[dict[str, Any]] = [
            {
                "kind": "ntfs_resource",
                "observed_at": self._instant(1),
                "source_key": f"resource|{self.resource_key}",
                "path": self.path,
                "server_name": self.server,
                "share_name": self.share,
                "local_path": self.local_path,
                "dacl_present": True,
                "dacl_protected": self.dacl_protected,
                # Declared from the entries actually sent. A descriptor whose count
                # disagrees with its entries raises ACE_COUNT_MISMATCH on every resolution,
                # which would bury the finding the comparison is looking for.
                "ace_count": len(aces),
                "inheritance_enabled": self.inheritance_enabled,
                "is_acl_boundary": True,
                "depth_from_share_root": 0,
                "resource_kind": "directory",
            }
        ]
        for index, ace in enumerate(aces):
            observations.append(
                {
                    "kind": "ntfs_ace",
                    "observed_at": self._instant(10 + index),
                    "source_key": (
                        f"ntfs_ace|{self.resource_key}|{ace.trustee_sid}|{ace.ace_type}"
                        f"|0x{ace.access_mask:08x}|0x{ace.ace_flags:02x}"
                    ),
                    "path": self.path,
                    "trustee_sid": ace.trustee_sid,
                    "ace_type": ace.ace_type,
                    "access_mask": ace.access_mask,
                    "ace_flags": ace.ace_flags,
                    "source": ace.source,
                    "order_index": ace.order_index,
                }
            )
        return self._run(
            "ntfs",
            "System.IO.DirectoryInfo.GetAccessControl",
            [{"kind": "directory_tree", "key": self.resource_key}],
            observations,
        )


def _placed(aces: Sequence[AceSpec], new: AceSpec) -> tuple[AceSpec, ...]:
    """A new entry in canonical order: Deny ahead of every Allow.

    The same rule :mod:`app.simulation.application` applies to a simulated addition, so the
    estate and the proposal place a new entry in the same place. A test whose fixture put it
    somewhere else would be comparing two different ACLs and calling the difference a defect.
    """
    if new.ace_type == "deny":
        denies = [item for item in aces if item.ace_type == "deny"]
        allows = [item for item in aces if item.ace_type != "deny"]
        return _renumbered((*denies, new, *allows))
    return _renumbered((*aces, new))


def _renumbered(aces: Iterable[AceSpec]) -> tuple[AceSpec, ...]:
    """Contiguous positions from zero. A DACL with holes cannot express ordering."""
    return tuple(replace(ace, order_index=index) for index, ace in enumerate(aces))


def estate(**overrides: Any) -> ControlledEstate:
    r"""The default known state: one share, one directory, two users and two groups.

    * ``Alice`` is in ``Finance-RW``, which holds Modify on ``\\FS01\Finance``.
    * ``Bob`` is in ``Finance-RO``, which holds Read & Execute.
    * The share ACL grants ``Everyone`` Full, so the NTFS layer is the limiting one and a
      change to it shows through — a share ACL that capped access would make several of
      these changes invisible, which is a true fact about Windows and a useless fixture.
    """
    domain = "S-1-5-21-1004336348-1177238915-682003330"
    alice = f"{domain}-1104"
    bob = f"{domain}-1105"
    finance_rw = f"{domain}-1202"
    finance_ro = f"{domain}-1203"
    base = ControlledEstate(
        principals=(
            PrincipalSpec(alice, "user", "Alice Smith"),
            PrincipalSpec(bob, "user", "Bob Jones"),
            PrincipalSpec(finance_rw, "domain_group", "Finance-RW"),
            PrincipalSpec(finance_ro, "domain_group", "Finance-RO"),
        ),
        edges=(
            EdgeSpec(finance_rw, alice),
            EdgeSpec(finance_ro, bob),
        ),
        share_aces=(ShareAceSpec("S-1-1-0", "allow", "full"),),
        ntfs_aces=(
            AceSpec(finance_rw, "allow", MODIFY),
            AceSpec(finance_ro, "allow", READ_EXECUTE),
        ),
    )
    return replace(base, **overrides) if overrides else base


# --------------------------------------------------------------------- collection


async def recollect(client: AsyncClient, state: ControlledEstate) -> None:
    """Scan the estate: three runs, each reconciling only what its collector can see."""
    for document in state.collector_runs():
        await replay(client, document)


# -------------------------------------------------------------------- observation


@dataclass(frozen=True, slots=True)
class ObservedAccess:
    """What the estate actually grants, read two ways. See the module docstring."""

    subject_key: str
    resource_key: str
    as_of: int
    """Rights from the point-in-time engine, which excludes what a reconciled scan proved
    gone. The reference reading."""

    live: int
    """Rights from the ordinary current-state engine, which does not consult presence."""

    as_of_certainty: str
    live_certainty: AccessCertainty


async def observe(
    session: AsyncSession,
    pairs: Sequence[tuple[str, str]],
    *,
    at: dt.datetime | None = None,
    path: AccessPath = AccessPath.REMOTE_SMB,
) -> dict[tuple[str, str], ObservedAccess]:
    """Resolve every pair against the estate as it now stands, both ways."""
    moment = at or dt.datetime.now(dt.UTC)
    history = HistoryService(session)
    live = AccessService(ResourceRepository(session), MembershipRepository(session))
    observations: dict[tuple[str, str], ObservedAccess] = {}
    for subject_key, resource_key in pairs:
        as_of = await history.effective_access_at(subject_key, resource_key, moment, path=path)
        current = await live.effective_access(subject_key, resource_key, path=path)
        observations[(subject_key, resource_key)] = ObservedAccess(
            subject_key=subject_key,
            resource_key=resource_key,
            as_of=as_of.access.access.rights.value,
            live=current.access.rights.value,
            as_of_certainty=as_of.certainty.value,
            live_certainty=current.access.certainty,
        )
    return observations


# -------------------------------------------------------------------- comparison


@dataclass(frozen=True, slots=True)
class Comparison:
    """One predicted delta held against what the recollection actually found."""

    subject_key: str
    resource_key: str
    predicted_before: int
    predicted_after: int
    observed_before: int
    observed_after: int
    observed_after_live: int
    direction: str

    @property
    def baseline_agrees(self) -> bool:
        """Whether the simulation's *before* matched what was actually there.

        A control rather than a result: both come from the same engine over the same rows, so
        a mismatch means the harness changed something it did not intend to.
        """
        return self.predicted_before == self.observed_before

    @property
    def agrees(self) -> bool:
        """Whether the prediction matched the measured post-change state."""
        return self.predicted_after == self.observed_after

    @property
    def live_agrees(self) -> bool:
        """The same against the current-state reading, which does not consult presence."""
        return self.predicted_after == self.observed_after_live

    def __str__(self) -> str:
        return (
            f"{self.subject_key} -> {self.resource_key}: predicted "
            f"0x{self.predicted_before:08X} -> 0x{self.predicted_after:08X} ({self.direction}); "
            f"observed 0x{self.observed_before:08X} -> 0x{self.observed_after:08X} "
            f"(live 0x{self.observed_after_live:08X})"
        )


@dataclass(frozen=True, slots=True)
class Equivalence:
    """Every pair the simulation spoke about, predicted against observed."""

    comparisons: tuple[Comparison, ...] = field(default_factory=tuple)

    @property
    def discrepancies(self) -> tuple[Comparison, ...]:
        return tuple(item for item in self.comparisons if not item.agrees)

    @property
    def equivalent(self) -> bool:
        return not self.discrepancies

    def report(self) -> str:
        """Every comparison, one per line. What an assertion message should carry."""
        return "\n  ".join(str(item) for item in self.comparisons)


def compare(
    report: SimulationReport,
    before: dict[tuple[str, str], ObservedAccess],
    after: dict[tuple[str, str], ObservedAccess],
) -> Equivalence:
    """Hold a simulation's deltas against the two measurements either side of the change.

    Only the pairs the simulation actually spoke about are compared. A pair the simulation
    never evaluated is not a disagreement — it is outside the question that was asked, and
    counting it as a discrepancy would make a bounded report look wrong for being bounded.
    """
    comparisons: list[Comparison] = []
    for delta in report.deltas:
        pair = (delta.subject_key, delta.resource_key)
        if pair not in before or pair not in after:
            continue
        comparisons.append(
            Comparison(
                subject_key=delta.subject_key,
                resource_key=delta.resource_key,
                predicted_before=delta.before.rights.value,
                predicted_after=delta.after.rights.value,
                observed_before=before[pair].as_of,
                observed_after=after[pair].as_of,
                observed_after_live=after[pair].live,
                direction=delta.direction.value,
            )
        )
    return Equivalence(comparisons=tuple(comparisons))


def delta_for(report: SimulationReport, subject_key: str, resource_key: str) -> AccessDelta | None:
    """The one delta about a pair, or ``None`` when the report did not evaluate it."""
    return next(
        (
            delta
            for delta in report.deltas
            if (delta.subject_key, delta.resource_key) == (subject_key, resource_key.casefold())
        ),
        None,
    )


def without_run_ids(document: dict[str, Any]) -> dict[str, Any]:
    """A transcript with its run identity stripped, for comparing two by content."""
    reduced = copy.deepcopy(document)
    reduced.get("start", {}).pop("run_id", None)
    reduced.get("start", {}).pop("started_at", None)
    for batch in reduced.get("batches", ()):
        batch.pop("run_id", None)
        batch.pop("batch_id", None)
        for item in batch.get("observations", ()):
            item.pop("run_id", None)
            item.pop("observed_at", None)
    reduced.get("completion", {}).pop("run_id", None)
    reduced.get("completion", {}).pop("completed_at", None)
    return reduced
