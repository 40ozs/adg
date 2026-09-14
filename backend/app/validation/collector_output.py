"""Validate a collector's output, then validate the graph it describes.

Two layers, because they fail differently.

**The envelope layer** answers "would the API accept this?". Every document is parsed
through the same contract models the endpoints use, so a rejection here is exactly the
rejection a collector would get over HTTP — with the difference that this reports *all* of
them at once instead of the first, and points at the file and the observation index.

**The graph layer** answers a question no per-observation check can: "is the identity graph
these observations describe sound?". An observation can be individually perfect and still
contribute to a membership cycle, name an endpoint nothing describes, disagree with another
observation about what a SID is, or put a BUILTIN group into the graph with no host scope —
which is the one hazard in this file that can silently merge two organizations' groups. A
collector author cannot see any of that by reading one payload.

Findings are graded so the tool can be a gate:

* :attr:`Severity.ERROR` — the API would reject this, or the documents contradict
  themselves. The collector is wrong.
* :attr:`Severity.WARNING` — ADG would accept it and then hold something an auditor has to
  be told about. Usually the directory is wrong, not the collector.
* :attr:`Severity.INFO` — worth knowing before the data is trusted at scale.

Absence of findings is not a claim that the data is *true*; only that it is well formed,
self-consistent, and free of the structural hazards this tool knows how to name.
"""

from __future__ import annotations

import json
import pathlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from app.contracts.v1.envelopes import ObservationBatch, ScanRunCompletion, ScanRunStart
from app.contracts.v1.observations import MembershipObservation, PrincipalObservation
from app.domain import (
    DEFAULT_LIMITS,
    Direction,
    GraphEdge,
    MembershipEdge,
    Principal,
    PrincipalKind,
    Sid,
    find_cycles,
)
from app.ingestion.plan import SUPPORTED_KINDS

__all__ = [
    "Finding",
    "Report",
    "Severity",
    "load_documents",
    "validate_documents",
]


class Severity(StrEnum):
    """How much a finding matters. Ordered most to least serious."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}

SOURCE_FIELD = "__source__"
"""Where a document came from, attached by the loader and stripped before validation."""


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing wrong, or worth saying, about a collector's output.

    ``remedy`` is not decoration. The audience is somebody holding a PowerShell module that
    produced the bad payload, and "invalid observation" tells them nothing about which line
    to change.
    """

    severity: Severity
    code: str
    message: str
    remedy: str
    location: str = ""
    subject: str = ""

    def rendered(self) -> str:
        head = f"{self.severity.value.upper():7} {self.code}"
        where = f" [{self.location}]" if self.location else ""
        subject = f"\n          subject: {self.subject}" if self.subject else ""
        return f"{head}{where}\n          {self.message}{subject}\n          → {self.remedy}"


@dataclass
class Report:
    """Everything found, plus what was examined."""

    findings: list[Finding] = field(default_factory=list)
    documents: int = 0
    observations: int = 0
    principals: int = 0
    edges: int = 0
    runs: list[str] = field(default_factory=list)

    def add(
        self,
        severity: Severity,
        code: str,
        message: str,
        remedy: str,
        location: str = "",
        subject: str = "",
    ) -> None:
        self.findings.append(
            Finding(
                severity=severity,
                code=code,
                message=message,
                remedy=remedy,
                location=location,
                subject=subject,
            )
        )

    @property
    def errors(self) -> list[Finding]:
        return [item for item in self.findings if item.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [item for item in self.findings if item.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        """Whether the API would accept every document examined."""
        return not self.errors

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda item: (_ORDER[item.severity], item.code))

    def counts(self) -> dict[str, int]:
        tally = Counter(item.severity.value for item in self.findings)
        return {severity.value: tally.get(severity.value, 0) for severity in Severity}

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "examined": {
                "documents": self.documents,
                "runs": self.runs,
                "observations": self.observations,
                "principals": self.principals,
                "membership_edges": self.edges,
            },
            "counts": self.counts(),
            "findings": [
                {
                    "severity": item.severity.value,
                    "code": item.code,
                    "message": item.message,
                    "remedy": item.remedy,
                    "location": item.location,
                    "subject": item.subject,
                }
                for item in self.sorted_findings()
            ],
        }

    def rendered(self) -> str:
        lines = [
            "ADG collector-output validation",
            "=" * 62,
            f"documents          {self.documents}",
            f"runs               {', '.join(self.runs) if self.runs else '(none)'}",
            f"observations       {self.observations} "
            f"({self.principals} principal, {self.edges} membership_edge)",
            "",
        ]
        if not self.findings:
            lines.append("No findings. The documents are well formed and self-consistent.")
        else:
            for item in self.sorted_findings():
                lines.append(item.rendered())
                lines.append("")
            counts = self.counts()
            lines.append(
                f"{counts['error']} error(s), {counts['warning']} warning(s), "
                f"{counts['info']} informational."
            )
        lines.append("")
        lines.append(
            "Absence of findings means well formed and self-consistent, not correct: this "
            "tool cannot know what your directory actually contains."
        )
        return "\n".join(lines)


# --------------------------------------------------------------------------- loading


def load_documents(paths: Sequence[pathlib.Path]) -> tuple[list[dict[str, Any]], list[Finding]]:
    """Read JSON from files and directories, keeping the source path with each document."""
    documents: list[dict[str, Any]] = []
    findings: list[Finding] = []
    for path in _expand(paths):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    code="malformed_json",
                    message=f"{path.name} is not valid JSON: {exc}",
                    remedy=(
                        "Write the payload with ConvertTo-Json -Depth 12 and check that no "
                        "diagnostic output was written to the same stream."
                    ),
                    location=str(path),
                )
            )
            continue
        except OSError as exc:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    code="unreadable_file",
                    message=f"{path} could not be read: {exc}",
                    remedy="Check the path and the file's permissions.",
                    location=str(path),
                )
            )
            continue

        for document in parsed if isinstance(parsed, list) else [parsed]:
            if not isinstance(document, dict):
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        code="unexpected_shape",
                        message=f"{path.name} contains a {type(document).__name__}, not an object.",
                        remedy=(
                            "Send a transcript ({start, batches, completion}), a single "
                            "envelope, or an array of those."
                        ),
                        location=str(path),
                    )
                )
                continue
            document[SOURCE_FIELD] = str(path)
            documents.append(document)
    return documents, findings


def _expand(paths: Sequence[pathlib.Path]) -> Iterator[pathlib.Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.glob("*.json"))
        else:
            yield path


# ------------------------------------------------------------------- envelope layer


@dataclass
class _Parsed:
    """Everything one set of documents contributed, already through the models."""

    starts: dict[str, ScanRunStart] = field(default_factory=dict)
    completions: dict[str, ScanRunCompletion] = field(default_factory=dict)
    batches: list[tuple[str, ObservationBatch]] = field(default_factory=list)
    principals: list[PrincipalObservation] = field(default_factory=list)
    edges: list[MembershipObservation] = field(default_factory=list)
    other_kinds: Counter[str] = field(default_factory=Counter)


def validate_documents(documents: Sequence[dict[str, Any]]) -> Report:
    """Validate parsed JSON documents and the graph their observations describe."""
    report = Report(documents=len(documents))
    parsed = _Parsed()

    for document in documents:
        source = str(document.get(SOURCE_FIELD, "<input>"))
        _read_document(document, source, parsed, report)

    report.runs = sorted({run for run, _ in parsed.batches} | set(parsed.starts))
    report.observations = sum(len(batch.observations) for _, batch in parsed.batches)
    report.principals = len(parsed.principals)
    report.edges = len(parsed.edges)

    _check_run_coherence(parsed, report)
    _check_batches(parsed, report)
    _check_unsupported_kinds(parsed, report)
    _check_principal_identity(parsed, report)
    _check_graph(parsed, report)
    return report


def _read_document(document: dict[str, Any], source: str, parsed: _Parsed, report: Report) -> None:
    if "start" in document or "batches" in document or "completion" in document:
        _read_transcript(document, source, parsed, report)
        return
    kind = _envelope_kind(document)
    if kind is None:
        report.add(
            Severity.ERROR,
            "unrecognized_document",
            "The document is not a transcript, a scan-run start, an observation batch, or "
            "a completion.",
            "See docs/contracts/collector-protocol.md for the three envelope shapes.",
            location=source,
        )
        return
    _read_envelope(kind, document, source, parsed, report)


def _envelope_kind(document: dict[str, Any]) -> str | None:
    if "observations" in document and "batch_id" in document:
        return "batch"
    if "source" in document and "scopes" in document:
        return "start"
    if "status" in document and "completed_at" in document:
        return "completion"
    return None


def _read_transcript(
    document: dict[str, Any], source: str, parsed: _Parsed, report: Report
) -> None:
    if "start" in document:
        _read_envelope("start", document["start"], f"{source}:start", parsed, report)
    for index, batch in enumerate(document.get("batches", [])):
        _read_envelope("batch", batch, f"{source}:batches[{index}]", parsed, report)
    if "completion" in document:
        _read_envelope("completion", document["completion"], f"{source}:completion", parsed, report)


def _read_envelope(kind: str, payload: Any, location: str, parsed: _Parsed, report: Report) -> None:
    if isinstance(payload, dict) and SOURCE_FIELD in payload:
        # The loader tags each document with the file it came from so findings can name it.
        # Contract models forbid unknown fields — correctly — so the tag comes off here
        # rather than being allowed to masquerade as a collector's typo.
        payload = {key: value for key, value in payload.items() if key != SOURCE_FIELD}
    model: ScanRunStart | ObservationBatch | ScanRunCompletion
    try:
        if kind == "start":
            model = ScanRunStart.model_validate(payload)
        elif kind == "batch":
            model = ObservationBatch.model_validate(payload)
        else:
            model = ScanRunCompletion.model_validate(payload)
    except ValidationError as exc:
        for error in exc.errors():
            field_path = ".".join(str(part) for part in error["loc"]) or "(payload)"
            report.add(
                Severity.ERROR,
                f"invalid_{kind}",
                f"{field_path}: {error['msg']}",
                "The API rejects this payload with 422. Fix the field named above; the "
                "message is the same one the endpoint returns.",
                location=location,
            )
        return

    if isinstance(model, ScanRunStart):
        existing = parsed.starts.get(model.run_id)
        if existing is not None and existing != model:
            report.add(
                Severity.ERROR,
                "conflicting_start",
                f"Run {model.run_id} was started twice with different contents.",
                "A run_id identifies one run. Generate a fresh one, or re-send the "
                "identical start; the server treats an identical re-post as a no-op.",
                location=location,
                subject=model.run_id,
            )
        parsed.starts[model.run_id] = model
    elif isinstance(model, ScanRunCompletion):
        parsed.completions[model.run_id] = model
    else:
        parsed.batches.append((model.run_id, model))
        for observation in model.observations:
            if isinstance(observation, PrincipalObservation):
                parsed.principals.append(observation)
            elif isinstance(observation, MembershipObservation):
                parsed.edges.append(observation)
            else:
                parsed.other_kinds[observation.kind] += 1


# ---------------------------------------------------------------------- run coherence


def _check_run_coherence(parsed: _Parsed, report: Report) -> None:
    runs = set(parsed.starts) | set(parsed.completions) | {run for run, _ in parsed.batches}
    for run in sorted(runs):
        start = parsed.starts.get(run)
        completion = parsed.completions.get(run)
        batches = [batch for batch_run, batch in parsed.batches if batch_run == run]

        if start is None and batches:
            report.add(
                Severity.WARNING,
                "batches_without_a_start",
                f"Run {run} has {len(batches)} batch(es) but no start envelope in this input.",
                "Include the start envelope, or check the collector opens the run before "
                "sending observations: the API answers 404 for an unknown run.",
                subject=run,
            )
        if completion is None and batches:
            report.add(
                Severity.WARNING,
                "run_never_completed",
                f"Run {run} sent observations but no completion envelope appears here.",
                "A run without a completion stays 'running' forever and can never "
                "reconcile a scope. Post the completion even when the run failed.",
                subject=run,
            )
        if start is None or completion is None:
            continue

        if not completion.reconciles(start):
            report.add(
                Severity.ERROR,
                "reconciled_an_undeclared_scope",
                f"Run {run} reconciles a scope it did not declare at start"
                + (", and it is an incremental run." if start.incremental else "."),
                "Absence may only be inferred inside a scope the run declared and "
                "enumerated completely. The API answers 409.",
                subject=run,
            )
        _check_counts(run, completion, batches, report)


def _check_counts(
    run: str, completion: ScanRunCompletion, batches: list[ObservationBatch], report: Report
) -> None:
    sent_observations = sum(len(batch.observations) for batch in batches)
    if completion.batch_count != len(batches):
        report.add(
            Severity.ERROR,
            "batch_count_mismatch",
            f"Run {run} reports batch_count={completion.batch_count} but "
            f"{len(batches)} batch(es) are present.",
            "The server downgrades a run whose batches did not all arrive to 'partial'. "
            "Report what was sent, so a genuine shortfall is still visible.",
            subject=run,
        )
    if completion.observation_count != sent_observations:
        report.add(
            Severity.ERROR,
            "observation_count_mismatch",
            f"Run {run} reports observation_count={completion.observation_count} but "
            f"{sent_observations} observation(s) are present.",
            "Count what the batches actually carried. A run claiming more coverage than "
            "it delivered overstates what ADG knows.",
            subject=run,
        )


def _check_batches(parsed: _Parsed, report: Report) -> None:
    by_run: dict[str, list[ObservationBatch]] = defaultdict(list)
    for run, batch in parsed.batches:
        by_run[run].append(batch)

    for run, batches in sorted(by_run.items()):
        identifiers = Counter(batch.batch_id for batch in batches)
        for batch_id, count in sorted(identifiers.items()):
            if count > 1:
                report.add(
                    Severity.ERROR,
                    "duplicate_batch_id",
                    f"Run {run} contains batch {batch_id} {count} times.",
                    "(run_id, batch_id) is the idempotency key. Give each batch its own id; "
                    "reuse one only to retry that exact batch.",
                    subject=batch_id,
                )

        sequences = sorted(batch.sequence for batch in batches)
        if sequences != list(range(1, len(sequences) + 1)):
            report.add(
                Severity.WARNING,
                "batch_sequence_gap",
                f"Run {run} has batch sequences {sequences}, which are not 1..n.",
                "Sequence numbers are how an operator sees that a batch is missing. Number "
                "them consecutively from 1.",
                subject=run,
            )

        finals = [batch.batch_id for batch in batches if batch.is_final]
        if len(finals) > 1:
            report.add(
                Severity.ERROR,
                "multiple_final_batches",
                f"Run {run} marks {len(finals)} batches final.",
                "Exactly one batch ends a run. Set is_final only on the last one.",
                subject=run,
            )
        elif not finals and batches:
            report.add(
                Severity.WARNING,
                "no_final_batch",
                f"Run {run} has no batch marked final.",
                "Mark the last batch is_final so a truncated upload is distinguishable "
                "from a complete one.",
                subject=run,
            )

        duplicates = Counter(
            observation.source_key for batch in batches for observation in batch.observations
        )
        for key, count in sorted(duplicates.items()):
            if count > 1:
                report.add(
                    Severity.WARNING,
                    "repeated_source_key",
                    f"Run {run} reports {key} in {count} batches.",
                    "Ingestion is idempotent on (run_id, source_key), so this is safe but "
                    "wasteful — and it usually means the same object was enumerated twice.",
                    subject=key,
                )


def _check_unsupported_kinds(parsed: _Parsed, report: Report) -> None:
    """Flag observation kinds ingestion cannot persist yet.

    The storable set is read from :data:`app.ingestion.plan.SUPPORTED_KINDS` rather than
    restated here, because it grows phase by phase and a hardcoded copy would start telling
    collector authors to split batches that the API had already learned to accept.
    """
    storable = ", ".join(sorted(SUPPORTED_KINDS))
    for kind, count in sorted(parsed.other_kinds.items()):
        report.add(
            Severity.ERROR,
            "unstorable_observation_kind",
            f"{count} observation(s) of kind {kind!r} are valid contract v1, but ingestion "
            f"currently stores only: {storable}.",
            "Send the unsupported kinds in their own run once the phase that persists them "
            "ships. The API rejects a mixed batch with 422 rather than accepting it and "
            "dropping them, so a collector is never told an observation was stored when it "
            "was not.",
            subject=kind,
        )


# ------------------------------------------------------------------ identity checks


def _by_run(
    observations: Sequence[PrincipalObservation],
) -> dict[str, list[PrincipalObservation]]:
    grouped: dict[str, list[PrincipalObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.run_id].append(observation)
    return grouped


def _check_principal_identity(parsed: _Parsed, report: Report) -> None:
    by_key: dict[str, list[PrincipalObservation]] = defaultdict(list)
    kinds_by_sid: dict[str, set[str]] = defaultdict(set)
    names: dict[tuple[str, str], set[str]] = defaultdict(set)

    for observation in parsed.principals:
        principal: Principal = observation.to_domain()
        by_key[principal.identity_key].append(observation)
        kinds_by_sid[observation.sid].add(observation.principal_kind.value)
        for label in (observation.display_name, observation.sam_account_name):
            if label:
                names[("name", label.casefold())].add(principal.identity_key)

    for key, observations in sorted(by_key.items()):
        # Per run. The same principal described differently by two *runs* is a rename or a
        # move, which is the change history ADG exists to keep; described differently twice
        # inside one run, it was read while it was changing.
        for run, within_run in sorted(_by_run(observations).items()):
            distinct = {
                observation.model_dump_json(exclude={"observed_at", "run_id"})
                for observation in within_run
            }
            if len(distinct) > 1:
                report.add(
                    Severity.WARNING,
                    "conflicting_principal_observations",
                    f"Run {run} describes {key} {len(within_run)} times with differing content.",
                    "Newest-wins on ingest, so the last one seen becomes the stored state. "
                    "One run disagreeing with itself means the object was read twice while "
                    "it was changing, or by two code paths that translate it differently.",
                    subject=key,
                )

    for sid, kinds in sorted(kinds_by_sid.items()):
        if len(kinds) > 1:
            report.add(
                Severity.WARNING,
                "one_sid_several_kinds",
                f"{sid} is reported as {', '.join(sorted(kinds))}.",
                "Only local_group is host-scoped, so the other kinds all share one storage "
                "key and the last one written wins. Decide what the SID is, or scope it.",
                subject=sid,
            )

    for (_, label), owners in sorted(names.items()):
        if len(owners) > 1:
            report.add(
                Severity.INFO,
                "one_name_several_principals",
                f"{len(owners)} distinct principals are named {label!r}.",
                "Expected and handled: names are metadata and SIDs are identity. Worth "
                "knowing before anybody searches by name and assumes one result.",
                subject=", ".join(sorted(owners)),
            )


# --------------------------------------------------------------------- graph checks


def _check_graph(parsed: _Parsed, report: Report) -> None:
    if not parsed.edges:
        return

    described: set[str] = {
        observation.to_domain().identity_key for observation in parsed.principals
    }
    graph_edges: list[GraphEdge] = []
    endpoints: set[str] = set()

    for observation in parsed.edges:
        edge: MembershipEdge = observation.to_domain()
        graph_edges.append(
            GraphEdge(
                edge_key=edge.identity_key,
                group_key=edge.group_key,
                member_key=edge.member_key,
                kind=edge.kind,
                host_key=edge.host_key,
                member_kind=edge.member_kind,
                is_foreign_security_principal=edge.is_foreign_security_principal,
            )
        )
        endpoints |= {edge.group_key, edge.member_key}

    _check_unscoped_wellknown_groups(parsed, report)
    _check_member_kinds(parsed, report)
    _check_dangling(endpoints, described, report)
    _check_cycles(graph_edges, report)
    _check_shape(graph_edges, report)


def _check_unscoped_wellknown_groups(parsed: _Parsed, report: Report) -> None:
    """A BUILTIN SID used as a group without a host is an identity collision waiting.

    ``S-1-5-32-544`` is byte-identical on every Windows computer and in every Active
    Directory domain. Only ``local_group`` principals and ``local_group_member`` edges carry
    a host, so a BUILTIN group reported any other way is stored under the bare SID — and a
    second domain or server reported the same way merges into it, membership and all.
    """
    offenders: dict[str, set[str]] = defaultdict(set)
    for observation in parsed.edges:
        edge = observation.to_domain()
        if edge.host_key is None and Sid(observation.group_sid).is_well_known:
            offenders[edge.group_key].add(f"edge {edge.identity_key}")
    for described in parsed.principals:
        if (
            described.principal_kind is PrincipalKind.DOMAIN_GROUP
            and Sid(described.sid).is_well_known
        ):
            offenders[described.to_domain().identity_key].add(f"principal {described.source_key}")

    for key, sources in sorted(offenders.items()):
        report.add(
            Severity.WARNING,
            "unscoped_well_known_group",
            f"{key} is a well-known or BUILTIN group SID stored without a host scope "
            f"({len(sources)} observation(s)).",
            "This SID is identical on every Windows computer and in every domain, so a "
            "second server or domain reported the same way merges into this one node and "
            "its members are attributed to both. Report it as principal_kind=local_group "
            "with host_key, and its memberships as edge_kind=local_group_member, whenever "
            "the group belongs to one machine. See docs/architecture/ad-graph-validation.md.",
            subject=key,
        )


def _check_member_kinds(parsed: _Parsed, report: Report) -> None:
    """An edge's ``member_kind`` and the member's own observation must agree."""
    claimed: dict[str, set[str]] = defaultdict(set)
    for observation in parsed.edges:
        if observation.member_kind is None:
            continue
        claimed[observation.to_domain().member_key].add(observation.member_kind.value)

    observed = {
        item.to_domain().identity_key: item.principal_kind.value for item in parsed.principals
    }
    for key, kinds in sorted(claimed.items()):
        actual = observed.get(key)
        if actual is None or actual in kinds:
            continue
        report.add(
            Severity.WARNING,
            "member_kind_disagrees",
            f"Edges call {key} {', '.join(sorted(kinds))}, but its own observation says {actual}.",
            "The stored principal wins, so the edge's claim is only used for members "
            "nothing describes. A disagreement usually means the two were read at "
            "different moments or by different code paths.",
            subject=key,
        )


def _check_dangling(endpoints: set[str], described: set[str], report: Report) -> None:
    dangling = sorted(endpoints - described)
    if not dangling:
        return
    sample = ", ".join(dangling[:5]) + (" …" if len(dangling) > 5 else "")
    report.add(
        Severity.INFO,
        "members_without_a_description",
        f"{len(dangling)} membership endpoint(s) have no principal observation in this input.",
        "Legitimate when another run describes them, and the membership is stored either "
        "way — an undescribed SID inside a group is a finding ADG deliberately keeps. "
        "Confirm the principal pass covered them if this run was meant to be complete.",
        subject=sample,
    )


def _check_cycles(edges: Sequence[GraphEdge], report: Report) -> None:
    for cycle in find_cycles(edges, Direction.DOWN):
        loop = " → ".join(cycle.representative_path)
        report.add(
            Severity.WARNING,
            "membership_cycle",
            f"{cycle.length} groups contain each other: {', '.join(cycle.members)}.",
            "Stored and survived, never rejected — traversal is cycle-safe and reports the "
            f"loop. It is still a directory defect worth breaking: {loop}",
            subject=cycle.members[0],
        )


def _check_shape(edges: Sequence[GraphEdge], report: Report) -> None:
    """Warn when the observed graph is shaped to run into the default traversal limits."""
    fan_out: Counter[str] = Counter(edge.group_key for edge in edges)
    for key, width in fan_out.most_common(3):
        if width > DEFAULT_LIMITS.max_nodes:
            report.add(
                Severity.WARNING,
                "group_wider_than_the_default_limit",
                f"{key} has {width} direct members, above the default max_nodes of "
                f"{DEFAULT_LIMITS.max_nodes}.",
                "Every effective-members answer for it will be a declared lower bound "
                "unless the caller raises max_nodes. Check the client reads "
                "traversal.complete.",
                subject=key,
            )

    depth = _longest_chain(edges)
    if depth > DEFAULT_LIMITS.max_depth:
        report.add(
            Severity.WARNING,
            "nesting_deeper_than_the_default_limit",
            f"Group nesting reaches {depth} levels, above the default max_depth of "
            f"{DEFAULT_LIMITS.max_depth}.",
            "Answers will be truncated at the default. Raise max_depth on the query, and "
            "treat nesting this deep as a directory finding in its own right.",
        )
    elif depth > DEFAULT_LIMITS.max_depth // 2:
        report.add(
            Severity.INFO,
            "deep_nesting",
            f"Group nesting reaches {depth} levels.",
            "Within the default depth limit, but deep enough that a reviewer should see it.",
        )


def _longest_chain(edges: Sequence[GraphEdge]) -> int:
    """Longest downward chain, counted without recursing and without looping on a cycle."""
    children: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        children[edge.group_key].append(edge.member_key)

    best = 0
    for root in list(children):
        # Iterative depth-first walk carrying the set of nodes on the current chain, so a
        # cycle is bounded by the chain rather than followed forever.
        stack: list[tuple[str, int, frozenset[str]]] = [(root, 0, frozenset({root}))]
        while stack:
            node, depth, seen = stack.pop()
            best = max(best, depth)
            if depth >= DEFAULT_LIMITS.max_depth + 1:
                continue
            for child in children.get(node, ()):
                if child in seen:
                    continue
                stack.append((child, depth + 1, seen | {child}))
    return best


def validate_paths(paths: Sequence[pathlib.Path]) -> Report:
    """Load and validate everything under ``paths``."""
    documents, failures = load_documents(paths)
    report = validate_documents(documents)
    report.findings.extend(failures)
    report.documents = len(documents) + len(failures)
    return report


def iter_codes() -> Iterable[str]:
    """Every finding code this module can emit. Used by the tests to keep docs honest."""
    return (
        "malformed_json",
        "unreadable_file",
        "unexpected_shape",
        "unrecognized_document",
        "invalid_start",
        "invalid_batch",
        "invalid_completion",
        "conflicting_start",
        "batches_without_a_start",
        "run_never_completed",
        "reconciled_an_undeclared_scope",
        "batch_count_mismatch",
        "observation_count_mismatch",
        "duplicate_batch_id",
        "batch_sequence_gap",
        "multiple_final_batches",
        "no_final_batch",
        "repeated_source_key",
        "unstorable_observation_kind",
        "conflicting_principal_observations",
        "one_sid_several_kinds",
        "one_name_several_principals",
        "unscoped_well_known_group",
        "member_kind_disagrees",
        "members_without_a_description",
        "membership_cycle",
        "group_wider_than_the_default_limit",
        "nesting_deeper_than_the_default_limit",
        "deep_nesting",
    )
