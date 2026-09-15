r"""The remediator interface, the executor every deployment gets, and the one for a lab.

This module is the shape of a write path and not a write path. It exists so that the
question *"what would it take for ADG to change Windows?"* has an answer written down in
code rather than argued about in a design meeting — and so that the answer is visibly
**not shipped**.

## What is here

:class:`Remediator` is a protocol with two methods. :class:`DisabledRemediator` implements it
by refusing, and is what :func:`remediator_for` returns for every configuration except one.
:class:`LabRemediator` implements it against a JSON fixture held in memory, and is refused
unless the deployment has explicitly asked for lab mode *and* is not production.

## What is deliberately not here

**No Windows API, no directory client, no SMB client, no subprocess.** Not behind a feature
flag, not behind an import guard, not in an optional extra. ADG's dependency set contains
nothing that can write a security descriptor or a group membership, and
``tests/remediation/test_no_write_path.py`` asserts the modules this package imports.

**No route.** No endpoint in the API reaches an executor, and the capability an endpoint
would require — ``Capability.REMEDIATION_EXECUTE`` — is granted by no role, including the
reserved ``remediator`` role that exists to hold its name. A future execution endpoint is a
deliberate act in at least four files, and the tests that fail when it is written are named
in ``docs/architecture/remediation.md`` §8.

**No adapter that could be pointed at production by configuration.** :class:`LabRemediator`
requires a fixture path; it mutates that fixture's in-memory copy and can reach nothing else.
There is no value of :class:`~app.domain.remediation.ExecutionMode` that names a real
adapter, because writing one is not a configuration change.

## Why an interface at all, if nothing implements it

Because the alternative is worse in a way that only shows up later. Without a named seam, the
first person to add remediation adds it wherever the request happens to be — in a route
handler, in the governance service, in a background job — and the guard rails have to be
invented at that moment by whoever is in a hurry. With one, the seam is a single protocol
with a single factory, every call site is `remediator_for(settings)`, and the preconditions,
the approval check and the audit event are already on the path.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import pathlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

from app.domain.remediation import ExecutionMode
from app.remediation.errors import RemediationDisabled, RemediationValidationError
from app.remediation.model import ChangePlan, PlannedChange

__all__ = [
    "DISABLED_MESSAGE",
    "DisabledRemediator",
    "ExecutionOutcome",
    "ExecutorDescription",
    "LabRemediator",
    "Remediator",
    "remediator_for",
]

DISABLED_MESSAGE: Final = (
    "ADG does not change Windows. This deployment has no remediation adapter, and one "
    "cannot be enabled by configuration: shipping a write path requires an adapter "
    "implementation, a credential with authority over the estate, and a grant of "
    "remediation:execute, which no role holds. Export the approved change plan and have an "
    "administrator perform it. See docs/architecture/remediation.md section 8."
)


@dataclass(frozen=True, slots=True)
class ExecutorDescription:
    """What a deployment can truthfully say about its own write posture.

    Served by ``GET /api/v1/remediation/execution-policy`` so that the guarantee is
    *observable* rather than only documented. An operator asking "can this thing change my
    domain?" gets an answer from the running process, which is the only answer worth having.
    """

    mode: ExecutionMode
    can_execute: bool
    adapter: str
    reason: str
    """Why execution is unavailable, in a sentence an operator can act on. Never empty: a
    deployment that *could* execute would say so here, and none can."""

    requirements: tuple[str, ...] = ()
    """What would have to be true before any execution path existed. Written out rather than
    left to the architecture document, because this is the response somebody reads when they
    are looking for a way to turn it on."""

    def document(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "can_execute": self.can_execute,
            "adapter": self.adapter,
            "reason": self.reason,
            "requirements": list(self.requirements),
        }


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """What one attempted change did. Only a lab executor ever produces one."""

    change_id: str
    applied: bool
    detail: str
    observed_at: dt.datetime


@runtime_checkable
class Remediator(Protocol):
    """Something that could carry out a planned change. Nothing in ADG does.

    Two methods, and the asymmetry between them is the point: :meth:`describe` is safe,
    always available, and is what the API actually calls; :meth:`apply` is the one that would
    write, and the implementation every deployment gets raises from it unconditionally.
    """

    def describe(self) -> ExecutorDescription:
        """What this executor is and whether it can do anything. Never raises."""
        ...

    async def apply(self, plan: ChangePlan, change: PlannedChange) -> ExecutionOutcome:
        """Carry out one change.

        Raises:
            RemediationDisabled: from :class:`DisabledRemediator`, always.
        """
        ...


class DisabledRemediator:
    """The executor every deployment gets. It refuses, and explains the product's posture.

    Refusing is the *behavior*, not an unimplemented stub: the message names the three things
    that would each have to be true before an execution path existed, so somebody who reaches
    this by accident learns what ADG is rather than filing a bug about a missing feature.
    """

    adapter_name: Final = "none"

    def describe(self) -> ExecutorDescription:
        return ExecutorDescription(
            mode=ExecutionMode.DISABLED,
            can_execute=False,
            adapter=self.adapter_name,
            reason=DISABLED_MESSAGE,
            requirements=(
                "An adapter implementing Remediator against a real Windows write API. None "
                "exists in this codebase and no dependency provides one.",
                "A credential with authority to modify group membership or security "
                "descriptors. ADG's collectors run read-only and hold no such credential.",
                "A role granting remediation:execute. No role grants it, including the "
                "reserved 'remediator' role, which exists to hold the name and grants "
                "nothing.",
                "An execution route. The API has none, and tests/remediation/"
                "test_no_write_path.py fails if one is added.",
                "A documented rollback for every change kind, and an approval policy that "
                "distinguishes 'approved to plan' from 'approved to execute'.",
            ),
        )

    async def apply(self, plan: ChangePlan, change: PlannedChange) -> ExecutionOutcome:
        raise RemediationDisabled(DISABLED_MESSAGE)


@dataclass
class LabRemediator:
    """A fixture-backed executor for development and tests. It touches nothing real.

    Its whole state is an in-memory copy of a JSON fixture: a dictionary of ACL entries and
    group edges, loaded once, mutated by :meth:`apply`, and discarded when the object is. It
    does not write the fixture back to disk, does not open a socket, does not touch a database
    table, and cannot name an object the fixture does not already contain.

    That last rule is the one that makes it safe to have at all. A lab adapter that accepted
    any target would be a rehearsal for a real one, and the first time somebody pointed it at
    a deployment with a production database it would produce a plausible, wrong report of work
    it had not done. Every target is checked against the fixture, and one that is not there is
    refused with the key it was looking for.

    **Not reachable from the API.** Nothing in ``app/api`` constructs one, and
    ``tests/remediation/test_no_write_path.py`` asserts it. It exists for the tests that prove
    the interface is implementable and that the guardrails around it hold.
    """

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: set[tuple[str, str]] = field(default_factory=set)
    applied: list[ExecutionOutcome] = field(default_factory=list)

    adapter_name: Final = "lab-fixture"

    @classmethod
    def from_fixture(cls, document: Mapping[str, Any]) -> LabRemediator:
        """Build one from a parsed fixture document.

        Two keys: ``entries``, a mapping of ``ace_key`` to the entry's fields, and ``edges``,
        a list of ``[group_key, member_key]`` pairs. Copied deeply, so a caller holding the
        fixture cannot watch it change and a test cannot leak state into the next one.
        """
        entries = document.get("entries", {})
        edges = document.get("edges", [])
        if not isinstance(entries, Mapping) or not isinstance(edges, Sequence):
            raise RemediationValidationError(
                "A lab fixture holds 'entries' (a mapping of ace_key to entry) and 'edges' "
                "(a list of [group_key, member_key] pairs).",
                field="fixture",
            )
        return cls(
            entries={str(key): dict(copy.deepcopy(value)) for key, value in entries.items()},
            edges={(str(pair[0]), str(pair[1])) for pair in edges},
        )

    @classmethod
    def from_path(cls, path: pathlib.Path) -> LabRemediator:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise RemediationValidationError(
                f"The lab remediation fixture at {path} could not be read: {error}. Lab mode "
                "has no fallback to a real adapter, so this is a refusal rather than a "
                "degradation.",
                field="remediation_lab_fixture_path",
            ) from error
        except json.JSONDecodeError as error:
            raise RemediationValidationError(
                f"The lab remediation fixture at {path} is not valid JSON: {error}.",
                field="remediation_lab_fixture_path",
            ) from error
        return cls.from_fixture(document)

    def describe(self) -> ExecutorDescription:
        return ExecutorDescription(
            mode=ExecutionMode.LAB,
            can_execute=True,
            adapter=self.adapter_name,
            reason=(
                "Lab mode: changes are applied to an in-memory copy of a test fixture and to "
                "nothing else. No Windows object, no collected table and no file is modified. "
                "This mode is refused when ADG_ENVIRONMENT=production."
            ),
            requirements=(),
        )

    async def apply(self, plan: ChangePlan, change: PlannedChange) -> ExecutionOutcome:
        """Mutate the fixture, or refuse because it does not describe the target.

        The refusal matters more than the mutation. A lab adapter that invented the objects it
        was asked about would report every plan as executed, including one whose targets had
        been renamed out from under it — which is the failure a real adapter would have, made
        undetectable.
        """
        now = dt.datetime.now(dt.UTC)
        if change.entry is not None:
            key = change.entry.ace_key
            if key not in self.entries:
                raise RemediationValidationError(
                    f"The lab fixture has no entry {key!r}, so there is nothing to change. A "
                    "lab adapter that invented its targets would report every plan as done.",
                    field="ace_key",
                )
            if change.after_access_mask is not None:
                self.entries[key]["access_mask"] = change.after_access_mask
                detail = f"masked {key} to {change.after_access_mask:#010x}"
            elif change.after_permission is not None:
                self.entries[key]["permission"] = change.after_permission.value
                detail = f"set {key} to {change.after_permission.value}"
            else:
                del self.entries[key]
                detail = f"removed {key}"
        else:
            edge = change.membership
            assert edge is not None
            pair = (edge.group_key, edge.member_key)
            if pair not in self.edges:
                raise RemediationValidationError(
                    f"The lab fixture has no membership edge {pair!r}.", field="membership"
                )
            self.edges.discard(pair)
            detail = f"removed {edge.member_key} from {edge.group_key}"

        outcome = ExecutionOutcome(
            change_id=str(change.change_id), applied=True, detail=detail, observed_at=now
        )
        self.applied.append(outcome)
        return outcome


def remediator_for(
    mode: ExecutionMode,
    *,
    environment: str,
    fixture_path: str = "",
) -> Remediator:
    """The executor this configuration gets. :class:`DisabledRemediator` unless proven otherwise.

    The default branch is the refusing one, deliberately: a factory whose fall-through
    returned something capable would hand a write path to any configuration it failed to
    recognize.

    Args:
        mode: the configured :class:`~app.domain.remediation.ExecutionMode`.
        environment: the deployment's environment. ``production`` refuses lab mode outright,
            here as well as in :class:`app.config.Settings`, because a guard stated once is a
            guard somebody moves.
        fixture_path: the lab fixture. Required in lab mode; a lab executor with no fixture
            would have to invent its targets.

    Raises:
        RemediationValidationError: if lab mode is asked for and cannot be given. Never
            downgraded to ``DisabledRemediator``: a test that asked for a lab adapter and
            silently got a refusing one would pass for the wrong reason.
    """
    if mode is ExecutionMode.LAB:
        if environment == "production":
            raise RemediationValidationError(
                "Lab remediation mode is refused in production. It exists so that the "
                "remediator interface can be exercised against a fixture; a production "
                "deployment running it would be one configuration change away from somebody "
                "believing ADG had made a change it had not.",
                field="remediation_execution_mode",
            )
        if not fixture_path.strip():
            raise RemediationValidationError(
                "Lab remediation mode needs a fixture: set ADG_REMEDIATION_LAB_FIXTURE_PATH. "
                "A lab executor without one would have to invent the objects it is asked "
                "about, and would then report every plan as carried out.",
                field="remediation_lab_fixture_path",
            )
        return LabRemediator.from_path(pathlib.Path(fixture_path))
    return DisabledRemediator()
