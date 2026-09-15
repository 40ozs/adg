r"""The risk report: what the rules found, filtered, counted, and drillable to the evidence.

Phase 8A built an engine with no HTTP surface, deliberately. This is that surface, and the
whole of its design is one sentence: **an empty report must never be able to look like a
clean estate.**

That is not a slogan here; it is four concrete things, and every one of them is a field
somebody could have left out.

``coverage``
    What the last evaluation actually covered. Rendered on every response, whether or not it
    is reassuring, because a caveat that appears only when things are bad teaches a reader to
    skip it. ``has_ever_run: false`` is the important case — the rules have never been
    evaluated, so the empty list below means *nothing has looked*, not *nothing is wrong*.

``counts``
    Taken over the whole filtered set and over every severity **including the zeros**. A
    facet list that omits the empty severities makes a report with no critical findings look
    like a report that has no critical category, and the reader cannot tell which.

``status_counts``
    Taken with the status filter removed, so the default view can say "12 open, and 340
    resolved findings are not shown". Without it, a default filter is indistinguishable from
    a complete result.

``configuration``
    What the rules were configured to do — which are off, and whether anything has been
    marked sensitive. A rule that is disabled reports nothing, and that silence is
    configuration rather than a clean result (ADR-0024).

## The evidence drawer is a route, not a field

A finding's evidence is whole records — ACEs, resources, principals, membership — and there
can be a lot of them. Putting them on every row of a listing would make a page of twenty
findings a payload of several hundred records nobody scrolls, so the listing carries a
summary and ``GET /api/v1/risks/findings/{key}`` carries the records themselves, plus the
catalog's remediation prose and the finding's own timeline.

That route also answers the question an auditor actually asks about a finding from last
quarter: ``reproduces`` re-derives the finding from the evidence stored **with it**, reading
nothing from the estate. ``false`` is a statement about ADG rather than about the estate, and
the response says which.

## No evaluation is triggered over HTTP

Reading a report must not be able to start a full pass over the estate, and a route that
could would be one request away from being the most expensive thing in the product. An
evaluation is an operator action (``python -m app.operations evaluate-risks``) or a
consequence of a scan run completing; see ``docs/operations/risk-rules.md``.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import AwareDatetime, BaseModel, Field

from app.api.deps import PRINTABLE_IDENTIFIER, Session
from app.api.pagination import (
    MAX_LIMIT,
    PageInfo,
    decode_offset_cursor,
    encode_offset_cursor,
    normalize_limit,
)
from app.auth.dependencies import requires
from app.auth.roles import Capability
from app.config import Settings, get_settings
from app.domain import DomainValidationError
from app.repositories.risk import (
    FindingCoverage,
    FindingQuery,
    RiskFindingRepository,
    RiskReportRepository,
    StoredFinding,
    rebuild_finding,
)
from app.risk_engine import (
    CATALOG,
    Confidence,
    RiskConfiguration,
    RuleId,
    Severity,
    definition_for,
    describe_qualifier,
    reproduce,
)

router = APIRouter(prefix="/api/v1/risks", tags=["risks"])

#: Reading the report. Held by every role that can see the estate: a finding says the same
#: thing about an access control list that the access screen already says, arranged so that
#: somebody notices it.
READ = Depends(requires(Capability.RISKS_READ))

__all__ = ["router"]


SettingsDep = Annotated[Settings, Depends(get_settings)]

FindingKeyPath = Annotated[
    str,
    Path(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="The finding key, as returned by the listing.",
    ),
]


# ----------------------------------------------------------------------------- views


class FindingSubjectView(BaseModel):
    """What a finding is about, as the keys a reader would go and look at.

    Named for the finding rather than ``SubjectView``, which is what the change feed calls
    its own. Two models of one name make FastAPI qualify **both** of them in the published
    document -- so a collision here would have renamed another phase's schema, and the
    frontend type checked against it.
    """

    resource_key: str | None = None
    share_key: str | None = None
    principal_key: str | None = None
    discriminator: str | None = Field(
        default=None,
        description=(
            "What separates several findings of one rule about one subject -- most often "
            "the trustee whose entry matched. Part of the finding's identity, so two "
            "trustees on one list are two findings that resolve independently."
        ),
    )
    kind: str = Field(description="Whether this is about a place, a principal, or a pairing.")


class CoverageView(BaseModel):
    """What the last evaluation covered. The reason a count may or may not be a total."""

    has_ever_run: bool = Field(
        description=(
            "False means the rules have never been evaluated. Every count below is then "
            "zero because nothing has looked, which is not the same as a clean estate and "
            "must not be rendered as one."
        )
    )
    evaluated_at: dt.datetime | None = None
    trigger: str | None = Field(
        default=None, description="full, incremental or targeted. Only a full pass can total."
    )
    complete: bool = Field(
        description=(
            "True only when the last pass covered the whole estate and hit no ceiling. "
            "False means the counts are what ADG currently holds, not what the estate has."
        )
    )
    rules_run: list[str] = Field(default_factory=list)
    rules_skipped: list[str] = Field(
        default_factory=list,
        description="Rules that did not run. Anything they would have found is not here.",
    )
    truncation: str | None = Field(
        default=None, description="What the pass ran out of room for, when it did."
    )
    evaluation_id: UUID | None = None


class RemediationView(BaseModel):
    """What an administrator would do about a finding, and what it might cost.

    ``caution`` is not decoration and is rendered whenever the catalog carries one. Every
    remediation here removes access somebody currently has, and the most common way an
    access review does damage is removing a grant a service account was quietly relying on.
    A finding that says what to do and not what to check first is an outage waiting for a
    maintenance window.
    """

    summary: str
    steps: list[str]
    caution: str | None = None


class RuleConfigView(BaseModel):
    """One rule, as this installation has configured it."""

    rule_id: str
    title: str
    detects: str
    matters_because: str
    remediation: RemediationView
    enabled: bool
    subject_kind: str
    version: str
    severities: dict[str, str] = Field(
        description="Band to severity, as configured. A band is a fact the rule reports."
    )
    requires_configuration: bool = Field(
        description=(
            "True means the rule reports nothing until an operator supplies something -- "
            "today, a sensitivity tag. Its silence is a configuration state, not a result."
        )
    )


class ConfigurationView(BaseModel):
    """What the rules were set to do when this report was produced."""

    version: str
    rules_enabled: int
    rules_disabled: list[str] = Field(
        default_factory=list,
        description="Rules that are off. They report nothing, and that is a decision.",
    )
    marks_anything_sensitive: bool = Field(
        description=(
            "False means no resource has been declared sensitive, so the sensitive-resource "
            "rule reports nothing. ADG will not infer sensitivity (ADR-0024)."
        )
    )


class FindingSummaryView(BaseModel):
    """One finding, as a row in a report."""

    key: str
    rule_id: str
    title: str
    status: str
    severity: str
    confidence: str
    band: str = Field(description="The shape the rule saw. Configuration maps it to severity.")
    qualifiers: list[str] = Field(
        default_factory=list,
        description="Why the confidence is what it is, in sentences. Empty means confirmed.",
    )
    subject: FindingSubjectView
    detail: dict[str, Any] = Field(default_factory=dict)
    first_detected_at: dt.datetime = Field(
        description="When this was ever first seen. Never moves, including across a reopen."
    )
    detected_at: dt.datetime = Field(
        description="When the current open window began. Reset by a reopen."
    )
    last_evaluated_at: dt.datetime = Field(
        description="When a pass last covered it. How far 'still there' is actually warranted."
    )
    resolved_at: dt.datetime | None = None
    occurrence_count: int
    evidence_count: int = Field(description="Records cited. The detail route carries them.")
    evidence_digest: str
    detail_url: str


class EvidenceRecordView(BaseModel):
    """One cited record, as it was stored with the finding.

    ``record`` is the whole stored record, not a rendering of it. That is what makes the
    drawer an audit artifact rather than a restatement: a reader can check the mask, the
    flags, the inheritance and the trustee for themselves.
    """

    kind: str
    key: str
    record: dict[str, Any]


class FindingEventView(BaseModel):
    """One transition, with the evaluation that decided it."""

    event_type: str
    occurred_at: dt.datetime
    evaluation_id: UUID
    rule_version: str
    severity: str | None = None
    confidence: str | None = None
    evidence_digest: str | None = None
    previous_evidence_digest: str | None = None


class FindingDetailView(BaseModel):
    """One finding, its evidence, its history, and what to do about it."""

    finding: FindingSummaryView
    rule: RuleConfigView
    evidence: list[EvidenceRecordView]
    events: list[FindingEventView]
    reproduces: bool = Field(
        description=(
            "Whether the finding still re-derives from the evidence stored with it, reading "
            "nothing from the estate. False is a statement about ADG -- the evidence was "
            "insufficient, or the predicate has since changed -- and not about the estate."
        )
    )
    reproduction_note: str


class RiskSummaryResponse(BaseModel):
    """The dashboard's header: counts, coverage and configuration."""

    coverage: CoverageView
    configuration: ConfigurationView
    severity_counts: dict[str, int]
    rule_counts: dict[str, int]
    status_counts: dict[str, int]
    total: int


class FindingsResponse(BaseModel):
    """A filtered page of findings, with everything needed to read it honestly."""

    filters: dict[str, Any] = Field(description="The filter that produced this list, echoed back.")
    coverage: CoverageView
    configuration: ConfigurationView
    severity_counts: dict[str, int] = Field(
        description="Over the whole filtered set, every severity, zeros included."
    )
    rule_counts: dict[str, int]
    status_counts: dict[str, int] = Field(
        description=(
            "Over the same filter with the status clause removed, so a default view can say "
            "how many findings it is not showing."
        )
    )
    items: list[FindingSummaryView]
    page: PageInfo


class RulesResponse(BaseModel):
    rules: list[RuleConfigView]
    configuration: ConfigurationView


class EvaluationView(BaseModel):
    """One pass of the rules."""

    evaluation_id: UUID
    trigger: str
    started_at: dt.datetime
    completed_at: dt.datetime | None = None
    configuration_version: str
    scope_complete: bool
    source_run_id: UUID | None = None
    rules_run: list[str] = Field(default_factory=list)
    rules_skipped: list[str] = Field(default_factory=list)
    findings_matched: int = 0
    findings_opened: int = 0
    findings_reopened: int = 0
    findings_resolved: int = 0
    error: str | None = Field(
        default=None,
        description="What the pass ran out of room for. Not a failure; a coverage note.",
    )


class EvaluationsResponse(BaseModel):
    evaluations: list[EvaluationView]


# ------------------------------------------------------------------------- queries

StatusQuery = Annotated[
    list[str] | None,
    Query(
        alias="status",
        description="open or resolved. Default: open alone -- a worklist, not a history.",
    ),
]
SeverityQuery = Annotated[
    list[str] | None, Query(alias="severity", description="Repeatable. Default: every severity.")
]
ConfidenceQuery = Annotated[
    list[str] | None,
    Query(alias="confidence", description="Repeatable. Default: every confidence."),
]
RuleQuery = Annotated[
    list[str] | None, Query(alias="rule", description="Repeatable rule identifier.")
]
PlaceQuery = Annotated[
    str | None,
    Query(
        alias="place",
        max_length=512,
        pattern=PRINTABLE_IDENTIFIER,
        description=(
            "A server, share or directory key. Matches findings about it and about "
            "everything inside it."
        ),
    ),
]
PrincipalQuery = Annotated[
    str | None,
    Query(
        alias="principal",
        max_length=512,
        pattern=PRINTABLE_IDENTIFIER,
        description="A principal key. Matches findings whose subject names it.",
    ),
]
FirstSeenQuery = Annotated[
    AwareDatetime | None,
    Query(
        alias="first_seen_from",
        description=(
            "Findings ever first seen at or after this instant. Not 'detected_at': a "
            "finding open since March that reopened on Tuesday is not new this week."
        ),
    ),
]
LastSeenQuery = Annotated[
    AwareDatetime | None,
    Query(
        alias="last_seen_to",
        description=(
            "Findings last confirmed at or before this instant -- the stale-evidence "
            "question, for a finding nothing has re-examined in a month."
        ),
    ),
]
LimitQuery = Annotated[int | None, Query(ge=1, le=MAX_LIMIT)]
CursorQuery = Annotated[str | None, Query(max_length=512)]


# ------------------------------------------------------------------------- routes


@router.get(
    "/summary",
    response_model=RiskSummaryResponse,
    summary="Severity counts, coverage and rule configuration",
    dependencies=[READ],
)
async def risk_summary(session: Session, settings: SettingsDep) -> RiskSummaryResponse:
    """The header a risk dashboard opens with.

    Counts over every open finding, and the two things that decide whether those counts mean
    anything: what the last evaluation covered, and what the rules were configured to do.
    """
    configuration = settings.risk_configuration
    report = RiskReportRepository(session)
    page = await report.page(FindingQuery(), limit=0)
    return RiskSummaryResponse(
        coverage=_coverage_view(page.coverage),
        configuration=_configuration_view(configuration),
        severity_counts={key.value: value for key, value in page.severity_counts.items()},
        rule_counts=page.rule_counts,
        status_counts={key.value: value for key, value in page.status_counts.items()},
        total=page.total,
    )


@router.get(
    "/rules",
    response_model=RulesResponse,
    summary="The rule catalog, as this installation has configured it",
    dependencies=[READ],
)
async def risk_rules(settings: SettingsDep) -> RulesResponse:
    """Every rule, whether it is on, what it looks for, and what to do about a match.

    Lists the **disabled** rules too. A catalog showing only what is enabled would make an
    installation with the interesting rules turned off look identical to a healthy one.
    """
    configuration = settings.risk_configuration
    return RulesResponse(
        rules=[_rule_view(rule_id, configuration) for rule_id in CATALOG],
        configuration=_configuration_view(configuration),
    )


@router.get(
    "/evaluations",
    response_model=EvaluationsResponse,
    summary="Recent passes of the rules",
    dependencies=[READ],
)
async def risk_evaluations(
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationsResponse:
    """What has run, what it covered, and what it decided.

    The operator's answer to "is the report current?" — and to the question behind it, which
    is whether an empty page is an empty estate or an evaluation nobody has run since Friday.
    """
    rows = await RiskReportRepository(session).evaluations(limit=limit)
    return EvaluationsResponse(evaluations=[_evaluation_view(row) for row in rows])


@router.get(
    "/findings",
    response_model=FindingsResponse,
    summary="Findings, filtered",
    dependencies=[READ],
    responses={422: {"description": "An unknown filter value, or a malformed cursor."}},
)
async def risk_findings(
    session: Session,
    settings: SettingsDep,
    status_: StatusQuery = None,
    severity: SeverityQuery = None,
    confidence: ConfidenceQuery = None,
    rule: RuleQuery = None,
    place: PlaceQuery = None,
    principal: PrincipalQuery = None,
    first_seen_from: FirstSeenQuery = None,
    last_seen_to: LastSeenQuery = None,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> FindingsResponse:
    """One page of findings, most consequential first.

    Sorted by severity, then confidence, then how long it has been true, then the key. The
    last is not decoration: without a total order, two pages of the same query can overlap or
    skip, and a skipped finding in an audit tool is a missed one.

    **An unknown filter value is refused, never ignored.** A request naming a severity that
    does not exist would otherwise return the unfiltered set, which is the one wrong answer a
    caller cannot detect.
    """
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    try:
        query = FindingQuery(
            statuses=_statuses(status_),
            rules=_parse_set(rule, RuleId, "rule"),
            severities=_parse_set(severity, Severity, "severity"),
            confidences=_parse_set(confidence, Confidence, "confidence"),
            place_key=place,
            principal_key=principal,
            first_seen_from=first_seen_from,
            last_seen_to=last_seen_to,
        )
    except DomainValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error

    result = await RiskReportRepository(session).page(query, limit=page_size, offset=offset)
    return FindingsResponse(
        filters=_echo(query),
        coverage=_coverage_view(result.coverage),
        configuration=_configuration_view(settings.risk_configuration),
        severity_counts={key.value: value for key, value in result.severity_counts.items()},
        rule_counts=result.rule_counts,
        status_counts={key.value: value for key, value in result.status_counts.items()},
        items=[_summary_view(item) for item in result.items],
        page=PageInfo(
            limit=page_size,
            has_more=result.has_more,
            next_cursor=(encode_offset_cursor(offset + page_size) if result.has_more else None),
            total=result.total,
        ),
    )


@router.get(
    "/findings/{key}",
    response_model=FindingDetailView,
    summary="One finding, with the records it was matched on",
    dependencies=[READ],
    responses={404: {"description": "No such finding."}},
)
async def risk_finding(
    key: FindingKeyPath, session: Session, settings: SettingsDep
) -> FindingDetailView:
    r"""The evidence drawer: the whole records, the timeline, and the remediation.

    ``evidence`` is the records themselves and not a rendering of them. ``"Everyone ->
    Modify"`` is a sentence; it cannot be rebuilt into facts, and an auditor asked to accept
    a finding on a sentence is being asked to accept ADG's word for it. So every cited
    ``AceFacts``, ``ResourceFacts``, ``PrincipalFacts`` and ``MembershipFacts`` record is
    here as it was stored, which is what makes ``reproduces`` a check rather than a claim.
    """
    configuration = settings.risk_configuration
    repository = RiskFindingRepository(session)
    record = await repository.get(key)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No finding {key}.")

    finding = rebuild_finding(record)
    reproduced = reproduce(finding, configuration)
    events = await repository.events_for(key)
    return FindingDetailView(
        finding=_summary_view(record),
        rule=_rule_view(record.rule_id, configuration),
        evidence=[
            EvidenceRecordView(kind=item.kind.value, key=item.key, record=dict(item.attributes))
            for item in record.evidence.items
        ],
        events=[_event_view(row) for row in events],
        reproduces=reproduced is not None,
        reproduction_note=(
            "This finding re-derives from the evidence stored with it. Nothing was read "
            "from the estate to check that."
            if reproduced is not None
            else (
                "This finding no longer re-derives from its own stored evidence. That is a "
                "statement about ADG rather than about the estate: either the evidence was "
                "insufficient when it was written, or the rule's predicate has since changed "
                "in a way that no longer matches what it matched then. The finding is still "
                "what was recorded; what cannot be re-checked is the derivation."
            )
        ),
    )


# --------------------------------------------------------------------- renderers


def _statuses(values: list[str] | None) -> frozenset[Any]:
    from app.models.schema import RiskFindingStatus

    if not values:
        return frozenset({RiskFindingStatus.OPEN})
    return _parse_set(values, RiskFindingStatus, "status")


def _parse_set(values: list[str] | None, enum: type[Any], name: str) -> frozenset[Any]:
    """Every value, or a 422 naming the one that is wrong.

    Refused rather than dropped. A filter that quietly ignores an unrecognized value returns
    a wider set than the caller asked for, and a caller who asked for ``severity=critcal``
    would get every severity and no indication that they had not.
    """
    if not values:
        return frozenset()
    known = {member.value: member for member in enum}
    parsed: set[Any] = set()
    for value in values:
        member = known.get(value.strip())
        if member is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{value!r} is not a {name}. Known values: {sorted(known)!r}. Refused "
                    "rather than ignored: ignoring it would return a wider set than you "
                    "asked for, with nothing in the response to say so."
                ),
            )
        parsed.add(member)
    return frozenset(parsed)


def _echo(query: FindingQuery) -> dict[str, Any]:
    return {
        "status": sorted(item.value for item in query.statuses),
        "rule": sorted(item.value for item in query.rules),
        "severity": sorted(item.value for item in query.severities),
        "confidence": sorted(item.value for item in query.confidences),
        "place": query.place_key,
        "principal": query.principal_key,
        "first_seen_from": (query.first_seen_from.isoformat() if query.first_seen_from else None),
        "last_seen_to": query.last_seen_to.isoformat() if query.last_seen_to else None,
    }


def _coverage_view(coverage: FindingCoverage) -> CoverageView:
    return CoverageView(
        has_ever_run=coverage.has_ever_run,
        evaluated_at=coverage.evaluated_at,
        trigger=coverage.trigger.value if coverage.trigger else None,
        complete=coverage.complete,
        rules_run=list(coverage.rules_run),
        rules_skipped=list(coverage.rules_skipped),
        truncation=coverage.truncation,
        evaluation_id=coverage.evaluation_id,
    )


def _configuration_view(configuration: RiskConfiguration) -> ConfigurationView:
    disabled = [rule_id.value for rule_id in CATALOG if not configuration.for_rule(rule_id).enabled]
    return ConfigurationView(
        version=configuration.version,
        rules_enabled=len(CATALOG) - len(disabled),
        rules_disabled=disabled,
        marks_anything_sensitive=configuration.marks_anything_sensitive,
    )


def _rule_view(rule_id: RuleId, configuration: RiskConfiguration) -> RuleConfigView:
    definition = definition_for(rule_id)
    rule = configuration.for_rule(rule_id)
    return RuleConfigView(
        rule_id=rule_id.value,
        title=definition.title,
        detects=definition.detects,
        matters_because=definition.matters_because,
        remediation=RemediationView(
            summary=definition.remediation.summary,
            steps=list(definition.remediation.steps),
            caution=definition.remediation.caution,
        ),
        enabled=rule.enabled,
        subject_kind=definition.subject_kind.value,
        version=definition.version,
        severities={band.value: rule.severities[band].value for band in definition.bands},
        requires_configuration=definition.requires_configuration,
    )


def _summary_view(record: StoredFinding) -> FindingSummaryView:
    definition = definition_for(record.rule_id)
    return FindingSummaryView(
        key=record.key,
        rule_id=record.rule_id.value,
        title=definition.title,
        status=record.status.value,
        severity=record.severity.value,
        confidence=record.confidence.value,
        band=record.band.value,
        qualifiers=[describe_qualifier(item) for item in record.qualifiers],
        subject=FindingSubjectView(
            resource_key=record.subject.resource_key,
            share_key=record.subject.share_key,
            principal_key=record.subject.principal_key,
            discriminator=record.subject.discriminator,
            kind=record.subject.primary_kind.value,
        ),
        detail=dict(record.detail),
        first_detected_at=record.first_detected_at,
        detected_at=record.detected_at,
        last_evaluated_at=record.last_evaluated_at,
        resolved_at=record.resolved_at,
        occurrence_count=record.occurrence_count,
        evidence_count=len(record.evidence.items),
        evidence_digest=record.evidence_digest,
        detail_url=f"/api/v1/risks/findings/{record.key}",
    )


def _event_view(row: Any) -> FindingEventView:
    return FindingEventView(
        event_type=row["event_type"],
        occurred_at=row["occurred_at"],
        evaluation_id=row["evaluation_id"],
        rule_version=row["rule_version"],
        severity=row["severity"],
        confidence=row["confidence"],
        evidence_digest=row["evidence_digest"],
        previous_evidence_digest=row["previous_evidence_digest"],
    )


def _evaluation_view(row: Any) -> EvaluationView:
    return EvaluationView(
        evaluation_id=row["evaluation_id"],
        trigger=row["trigger"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        configuration_version=row["configuration_version"],
        scope_complete=bool(row["scope_complete"]),
        source_run_id=row["source_run_id"],
        rules_run=list(row["rules_run"] or ()),
        rules_skipped=list(row["rules_skipped"] or ()),
        findings_matched=int(row["findings_matched"]),
        findings_opened=int(row["findings_opened"]),
        findings_reopened=int(row["findings_reopened"]),
        findings_resolved=int(row["findings_resolved"]),
        error=row["error"],
    )
