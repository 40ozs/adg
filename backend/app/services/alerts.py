r"""Wiring the alert pipeline to the things it watches.

This is the one module allowed to know both halves. :mod:`app.alerts` is deliberately blind
to where facts come from; :mod:`app.changes` and :mod:`app.risk_engine` are deliberately
blind to the fact that anybody is being notified. The translation between them lives here,
and nowhere else — ``tests/alerts/test_layering.py`` fails if it starts living in
``app/alerts`` instead.

## What one evaluation does

1. Load the enabled watches. **If there are none and the finding trigger is off, stop** —
   without reading a single change. An installation that has configured nothing pays nothing.
2. For each watch, ask the change feed about that one thing over the window, scoped and
   restricted by kind. Per watch rather than estate-wide, because a page of estate-wide
   changes is mostly things nobody watched and the watched one is the row that falls off the
   end of it.
3. Convert what comes back into :class:`app.alerts.ChangeNotice` values and match them.
4. For watched **places**, ask the impact engine what each change actually did to effective
   access, bounded — and raise the expansion trigger only where access genuinely grew.
5. Record each candidate through :class:`app.repositories.alerts.AlertRepository`, which
   applies the deduplication decision and writes an event either way.
6. Enqueue an outbox delivery for every occurrence that notified.

## Two bounds, both reported rather than hidden

**Changes per watch** (:data:`CHANGES_PER_WATCH`) and **impact computations per pass**
(:data:`MAX_IMPACT_CHECKS`). Each exists because the alternative is unbounded work triggered
by a collector's last request: a subtree with a hundred thousand ACL edits, or one impact
resolution per edit over a group that nests forty deep.

Both are reported on :class:`AlertEvaluation` as ``truncated`` notes. That matters more here
than in most places: an alert pipeline that silently examined half of what it should have
looks exactly like one watching a quiet estate.

## Why this cannot roll back an ingestion

It does not run inside the ingestion transaction. :func:`evaluate_run_after_commit` opens its
own session **after** ``complete_run`` has committed, and swallows every exception into a log
line. The run is durable before a single watch is loaded, so an endpoint that is down, a
policy file that was edited badly, or a defect in this module cannot cost the estate an
observation.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from app.access_engine import AccessPath, RightsMask, summarize
from app.alerts import (
    AccessExpansion,
    AlertDispatcher,
    AlertEvent,
    AlertPolicy,
    AlertTrigger,
    ChangeNotice,
    DeliveryEnvelope,
    DrainReport,
    FindingNotice,
    Watch,
    WatchIndex,
    WatchKind,
    build_sinks,
    events_for_access_expansions,
    events_for_changes,
    events_for_findings,
    resolution_keys,
)
from app.changes import (
    ChangeFilter,
    ChangeImpactService,
    ChangeScope,
    ChangeService,
    ChangeSeverity,
    ChangeSignificance,
    ImpactVerdict,
    ObjectChange,
    ScopeTarget,
)
from app.changes.model import ChangeAction
from app.contracts.v1.common import ObservationKind
from app.repositories import MembershipRepository
from app.repositories.alerts import (
    AlertRepository,
    RecordedAlert,
    SqlAlertOutbox,
    WatchRepository,
)
from app.repositories.risk import RiskFactsRepository, RiskFindingRepository
from app.risk_engine import SEVERITY_ORDER, RiskFinding, Severity, definition_for
from app.services.risk import RiskService

__all__ = [
    "CHANGES_PER_WATCH",
    "MAX_IMPACT_CHECKS",
    "AlertEvaluation",
    "AlertService",
    "PostRunOutcome",
    "evaluate_run_after_commit",
    "notice_for_finding",
]

logger = logging.getLogger("app.alerts.service")

#: Changes read per watch in one pass. Generous for a watch on one directory or one group,
#: and a ceiling for a watch on a share whose subtree is being rewritten.
CHANGES_PER_WATCH: Final = 200

#: Effective-access resolutions attempted in one pass. Each one is a bounded membership
#: traversal and two access checks, so this is the number that decides what an evaluation
#: costs at its worst.
MAX_IMPACT_CHECKS: Final = 25

#: Kinds the change feed is asked for, per watch kind. Restricting the query is what keeps a
#: directory watch from paging through its subtree's display-name churn to find one ACL edit.
_KINDS_FOR_WATCH: Final[dict[WatchKind, frozenset[ObservationKind]]] = {
    WatchKind.RESOURCE: frozenset({ObservationKind.NTFS_ACE, ObservationKind.NTFS_RESOURCE}),
    WatchKind.SHARE: frozenset({ObservationKind.SMB_ACE, ObservationKind.SMB_SHARE}),
    WatchKind.GROUP: frozenset({ObservationKind.MEMBERSHIP_EDGE, ObservationKind.PRINCIPAL}),
}

_SCOPE_FOR_WATCH: Final[dict[WatchKind, ScopeTarget]] = {
    WatchKind.RESOURCE: ScopeTarget.DIRECTORY_TREE,
    WatchKind.SHARE: ScopeTarget.SHARE,
    WatchKind.GROUP: ScopeTarget.GROUP,
}

#: Every action and every significance, because a watch is a deliberate subscription to one
#: thing and the change feed's editorial defaults are for a page somebody skims. A reader who
#: asked to be told about this directory wants the metadata edit too; the payload carries the
#: classifier's own grading so they can tell which is which.
_ALL_ACTIONS: Final = frozenset(ChangeAction)
_ALL_SIGNIFICANCE: Final = frozenset(ChangeSignificance)


@dataclass(frozen=True, slots=True)
class AlertEvaluation:
    """One pass: what it looked at, what it raised, and what it could not reach."""

    started_at: dt.datetime
    window_from: dt.datetime
    window_to: dt.datetime
    watches_considered: int
    changes_examined: int
    recorded: tuple[RecordedAlert, ...] = ()
    enqueued: int = 0
    truncated: tuple[str, ...] = field(default_factory=tuple)
    resolved: tuple[str, ...] = field(default_factory=tuple)

    @property
    def notified(self) -> tuple[RecordedAlert, ...]:
        return tuple(item for item in self.recorded if item.notified)

    @property
    def suppressed(self) -> tuple[RecordedAlert, ...]:
        return tuple(item for item in self.recorded if not item.notified)

    @property
    def complete(self) -> bool:
        """Whether this pass examined everything it set out to.

        Read it before presenting silence as calm. A truncated pass that raised nothing has
        not established that nothing happened.
        """
        return not self.truncated

    @property
    def summary(self) -> str:
        """One line for an operator log, in American English."""
        return (
            f"alert evaluation over {self.window_from.isoformat()}..{self.window_to.isoformat()}: "
            f"{self.watches_considered} watches, {self.changes_examined} changes examined, "
            f"{len(self.notified)} notified, {len(self.suppressed)} suppressed, "
            f"{len(self.resolved)} resolved, {self.enqueued} queued for delivery"
            + ("" if self.complete else f"; INCOMPLETE ({'; '.join(self.truncated)})")
        )


class AlertService:
    """Detection, recording and enqueueing over one database session."""

    def __init__(
        self,
        session: Any,
        policy: AlertPolicy,
    ) -> None:
        self._session = session
        self._policy = policy
        self._watches = WatchRepository(session)
        self._alerts = AlertRepository(session)
        self._outbox = SqlAlertOutbox(session)
        self._changes = ChangeService(session)
        self._impact = ChangeImpactService(session)
        self._principals = MembershipRepository(session)

    @property
    def policy(self) -> AlertPolicy:
        return self._policy

    @property
    def watches(self) -> WatchRepository:
        return self._watches

    @property
    def alerts(self) -> AlertRepository:
        return self._alerts

    @property
    def outbox(self) -> SqlAlertOutbox:
        return self._outbox

    # -- the change-driven half -------------------------------------------------------

    async def evaluate_window(
        self, *, window_from: dt.datetime, window_to: dt.datetime, source_run_id: UUID | None = None
    ) -> AlertEvaluation:
        """Look for watched things that moved inside the window, and alert on them.

        Overlapping windows are safe and expected: a pass that re-reads a change it has
        already alerted on produces a candidate with an identical payload digest, which
        deduplication suppresses. That is the property that makes the window a
        conservative-by-default choice rather than a source of duplicates — better to look
        twice than to place a watermark a minute too late and miss an edit.
        """
        started = window_to
        watches = await self._watches.enabled()
        if not watches:
            return AlertEvaluation(
                started_at=started,
                window_from=window_from,
                window_to=window_to,
                watches_considered=0,
                changes_examined=0,
            )

        index = WatchIndex.build(watches)
        notices: list[ChangeNotice] = []
        raw: list[tuple[Watch, ObjectChange]] = []
        truncated: list[str] = []
        examined = 0

        for watch in watches:
            # One watch cannot cost the pass every other watch's alerts. A key that no longer
            # scopes -- a directory watch whose key is not a UNC path, a group watch on a
            # principal key shape the change feed cannot select on -- raises here, and without
            # this guard a single bad watch would silence the whole estate's alerting and
            # leave only a log line behind. The failure is recorded as a coverage note on the
            # evaluation, which is the thing a reader checks before believing a quiet feed.
            try:
                feed = await self._changes.feed(
                    ChangeFilter(
                        window_from=window_from,
                        window_to=window_to,
                        scope=ChangeScope(
                            target=_SCOPE_FOR_WATCH[watch.kind], key=_scope_key(watch)
                        ),
                        kinds=_KINDS_FOR_WATCH[watch.kind],
                        actions=_ALL_ACTIONS,
                        significance=_ALL_SIGNIFICANCE,
                        min_severity=ChangeSeverity.INFO,
                        limit=CHANGES_PER_WATCH,
                    )
                )
            except Exception as error:
                logger.exception(
                    "alerts.watch.unscopeable",
                    extra={"watch_id": str(watch.watch_id), "watch_key": watch.key},
                )
                truncated.append(
                    f"watch {watch.label!r} ({watch.kind.value} {watch.key}) could not be "
                    f"matched against the change feed and was skipped: {error}"
                )
                continue
            examined += feed.scanned
            if feed.has_more or feed.scan_exhausted:
                truncated.append(
                    f"watch {watch.label!r} ({watch.kind.value} {watch.key}) had more than "
                    f"{CHANGES_PER_WATCH} changes in this window; alerts cover the most recent"
                )
            raw.extend((watch, change) for change in feed.changes)

        labels = await self._labels(change for _, change in raw)
        notices = [_notice(change, labels) for _, change in raw]

        events = list(
            events_for_changes(index, notices, enabled_triggers=self._policy.enabled_triggers)
        )

        expansions, expansion_note = await self._expansions(index, raw, labels)
        if expansion_note is not None:
            truncated.append(expansion_note)
        events.extend(
            events_for_access_expansions(
                index, expansions, enabled_triggers=self._policy.enabled_triggers
            )
        )

        recorded, enqueued = await self._record_all(
            events, watches=watches, now=window_to, source_run_id=source_run_id
        )
        return AlertEvaluation(
            started_at=started,
            window_from=window_from,
            window_to=window_to,
            watches_considered=len(watches),
            changes_examined=examined,
            recorded=recorded,
            enqueued=enqueued,
            truncated=tuple(truncated),
        )

    # -- the finding-driven half ------------------------------------------------------

    async def evaluate_findings(
        self,
        *,
        opened: Sequence[RiskFinding],
        resolved_keys: Sequence[str] = (),
        now: dt.datetime,
        evaluation_id: UUID | None = None,
    ) -> AlertEvaluation:
        """Raise alerts for findings that opened, and close the alerts of ones that resolved.

        ``opened`` should carry the findings a risk evaluation opened **or reopened**: both
        are a condition becoming true, and a reopen is never suppressed
        (:mod:`app.alerts.dedupe`). ``resolved_keys`` are the finding keys the same pass
        closed — and only ones it was entitled to close, which the risk service's scope guard
        has already decided. Passing a wider set here would close alerts about exposures
        nobody looked at, which is the exact failure the guard exists to prevent, arriving one
        layer later.
        """
        trigger = AlertTrigger.CRITICAL_RISK_FINDING_OPENED
        if not self._policy.enables(trigger):
            return AlertEvaluation(
                started_at=now,
                window_from=now,
                window_to=now,
                watches_considered=0,
                changes_examined=0,
            )

        threshold = self._policy.severity_rank
        notices = [
            notice_for_finding(finding, at=now)
            for finding in opened
            if SEVERITY_ORDER[finding.severity] >= threshold
        ]
        events = events_for_findings(notices, enabled_triggers=self._policy.enabled_triggers)
        recorded, enqueued = await self._record_all(
            events, watches=(), now=now, source_evaluation_id=evaluation_id
        )

        closed: list[str] = []
        if resolved_keys:
            open_alerts = await self._alerts.open_keys_for_trigger(trigger)
            # An alert key is derived from the finding, never queried by payload: see
            # app.alerts.detection.resolution_keys. A lookup on the payload would be a second
            # implementation of the key, and the day they disagreed the resolution would
            # silently close nothing.
            for key in await self._alert_keys_for_findings(resolved_keys):
                if key not in open_alerts:
                    continue
                result = await self._alerts.resolve(
                    key, now=now, source_evaluation_id=evaluation_id
                )
                if result is None:
                    continue
                closed.append(key)
                enqueued += await self._enqueue(result, now=now)

        return AlertEvaluation(
            started_at=now,
            window_from=now,
            window_to=now,
            watches_considered=0,
            changes_examined=0,
            recorded=recorded,
            enqueued=enqueued,
            resolved=tuple(closed),
        )

    async def _alert_keys_for_findings(self, finding_keys: Sequence[str]) -> tuple[str, ...]:
        """The alert keys the given findings' alerts would have.

        The subject columns are read back from ``risk_findings`` rather than carried, because
        a resolution is reported as a key and nothing else — the finding it names no longer
        matches, so the engine has no object to hand over.
        """
        import sqlalchemy as sa

        from app.models.schema import risk_findings

        rows = (
            await self._session.execute(
                sa.select(
                    risk_findings.c.finding_key,
                    risk_findings.c.rule_id,
                    risk_findings.c.resource_key,
                    risk_findings.c.share_key,
                    risk_findings.c.principal_key,
                    risk_findings.c.severity,
                    risk_findings.c.confidence,
                    risk_findings.c.resolved_at,
                ).where(risk_findings.c.finding_key.in_(sorted(set(finding_keys))))
            )
        ).all()
        notices = [
            FindingNotice(
                finding_key=row.finding_key,
                rule_id=row.rule_id,
                title=row.rule_id,
                severity=row.severity,
                confidence=row.confidence,
                at=row.resolved_at or dt.datetime.now(tz=dt.UTC),
                resource_key=row.resource_key,
                share_key=row.share_key,
                principal_key=row.principal_key,
            )
            for row in rows
        ]
        return resolution_keys(notices)

    # -- recording and enqueueing -----------------------------------------------------

    async def _record_all(
        self,
        events: Sequence[AlertEvent],
        *,
        watches: Sequence[Watch],
        now: dt.datetime,
        source_run_id: UUID | None = None,
        source_evaluation_id: UUID | None = None,
    ) -> tuple[tuple[RecordedAlert, ...], int]:
        by_id = {watch.watch_id: watch for watch in watches}
        recorded: list[RecordedAlert] = []
        enqueued = 0
        for event in events:
            watch = by_id.get(event.watch_id) if event.watch_id is not None else None
            result = await self._alerts.record(
                event,
                now=now,
                cooldown=watch.cooldown if watch is not None else self._policy.default_cooldown,
                watch_enabled=watch.enabled if watch is not None else True,
                watch_label=watch.label if watch is not None else None,
                source_run_id=source_run_id,
                source_evaluation_id=source_evaluation_id,
            )
            recorded.append(result)
            enqueued += await self._enqueue(result, now=now)
        return tuple(recorded), enqueued

    async def _enqueue(self, result: RecordedAlert, *, now: dt.datetime) -> int:
        """Queue one occurrence for every sink that takes its trigger. Suppressed: none.

        Enqueueing happens in the same transaction as the alert, and delivery does not. That
        is the whole of "a failing endpoint cannot roll back an ingestion".
        """
        if not result.notified:
            return 0
        envelopes = [
            DeliveryEnvelope(
                event_id=result.event_id,
                alert_key=result.alert_key,
                trigger=result.trigger,
                transition=result.transition,
                sink_name=sink.name,
                occurred_at=result.occurred_at,
                summary=result.summary,
                payload=dict(result.payload),
                folds=result.decision.folds,
                watch_id=result.watch_id,
                watch_label=result.watch_label,
            )
            for sink in self._policy.sinks_for(result.trigger)
        ]
        return await self._outbox.enqueue(envelopes, now=now)

    # -- delivery ---------------------------------------------------------------------

    async def dispatch(self, *, now: dt.datetime, limit: int | None = None) -> DrainReport:
        """Attempt the deliveries that are due. Safe to call from anywhere and often."""
        dispatcher = AlertDispatcher.from_policy(
            self._outbox, self._policy, build_sinks(self._policy)
        )
        return await dispatcher.drain(now=now, limit=limit)

    # -- helpers ----------------------------------------------------------------------

    async def _expansions(
        self,
        index: WatchIndex,
        raw: Sequence[tuple[Watch, ObjectChange]],
        labels: dict[str, str],
    ) -> tuple[tuple[AccessExpansion, ...], str | None]:
        """What each change on a watched **place** did to effective access.

        Only for changes that name both a principal and a place, which is what the access
        engine needs to answer at all, and only where the answer is that access **grew**. A
        change that widened an entry while a Deny on the other layer still governs produces
        no expansion, which is the whole reason this trigger exists beside the ACL one.
        """
        if not any(watch.watches(AlertTrigger.WATCHED_ACCESS_EXPANDED) for watch, _ in raw):
            return (), None

        candidates = [
            (watch, change)
            for watch, change in raw
            if watch.kind in (WatchKind.RESOURCE, WatchKind.SHARE)
            and watch.watches(AlertTrigger.WATCHED_ACCESS_EXPANDED)
            and change.subject.related_key is not None
            and change.kind in (ObservationKind.NTFS_ACE, ObservationKind.SMB_ACE)
        ]
        # Newest first, so that the ceiling below keeps the changes an operator is most
        # likely to be asked about rather than whichever watch happened to be loaded first.
        candidates.sort(key=lambda pair: pair[1].after.valid_from, reverse=True)
        note = None
        if len(candidates) > MAX_IMPACT_CHECKS:
            note = (
                f"{len(candidates)} changes on watched places could have expanded access; "
                f"only the {MAX_IMPACT_CHECKS} most recent were resolved"
            )
            candidates = candidates[:MAX_IMPACT_CHECKS]

        expansions: list[AccessExpansion] = []
        for watch, change in candidates:
            resource_key = (
                change.subject.container_key if change.kind is ObservationKind.NTFS_ACE else None
            )
            try:
                impact = await self._impact.impact_of(
                    change.kind,
                    change.key,
                    change.after.valid_from,
                    subject_key=change.subject.related_key,
                    resource_key=resource_key,
                    path=AccessPath.REMOTE_SMB,
                )
            except Exception:
                # One change that cannot be resolved must not cost the pass every other
                # alert it was about to raise. The access engine refuses questions it cannot
                # answer -- a resource it holds no descriptor for, a principal that is not a
                # principal -- and those refusals arrive here as exceptions.
                logger.warning(
                    "alerts.impact.unresolved",
                    extra={"change_kind": change.kind.value, "change_key": change.key},
                )
                continue
            if impact.verdict is not ImpactVerdict.RESOLVED or impact.access is None:
                continue
            gained = impact.access.gained
            if not gained.value:
                continue
            expansions.append(
                AccessExpansion(
                    principal_key=impact.access.subject_key,
                    resource_key=resource_key,
                    share_key=None if resource_key else watch.key,
                    gained=_rights_text(gained),
                    before=_rights_text(impact.access.before.access.access.rights),
                    after=_rights_text(impact.access.after.access.access.rights),
                    at=change.after.valid_from,
                    cause_kind=change.kind.value,
                    cause_key=change.key,
                    principal_label=labels.get(impact.access.subject_key),
                    place_label=watch.label,
                )
            )
        return tuple(expansions), note

    async def _labels(self, changes: Any) -> dict[str, str]:
        """Display names for the principals the changes name, in one query.

        Names are metadata (ADR-0001) and the key is the truth, so a missing name is
        ordinary and the alert renders the key. What it must never do is one lookup per
        change.
        """
        keys: set[str] = set()
        for change in changes:
            for key in (change.subject.container_key, change.subject.related_key):
                if key and key.startswith("principal|"):
                    keys.add(key)
        if not keys:
            return {}
        records = await self._principals.principals_by_keys(sorted(keys))
        return {
            key: record.display_name or record.sam_account_name or record.last_known_name or key
            for key, record in records.items()
        }


def notice_for_finding(finding: RiskFinding, *, at: dt.datetime) -> FindingNotice:
    """A risk finding as the alert pipeline's flat record.

    The one place a :class:`app.risk_engine.RiskFinding` is turned into something
    :mod:`app.alerts` can read. The title comes from the catalog rather than from the rule
    identifier, because ``broad_write_on_sensitive_resource`` is a grep target and the
    catalog's sentence is what somebody reads at two in the morning.
    """
    definition = definition_for(finding.rule_id)
    severity: Severity = finding.severity
    where = (
        finding.subject.resource_key or finding.subject.share_key or finding.subject.principal_key
    )
    return FindingNotice(
        finding_key=finding.key,
        rule_id=finding.rule_id.value,
        title=definition.title,
        severity=severity.value,
        confidence=finding.confidence.value,
        at=at,
        resource_key=finding.subject.resource_key,
        share_key=finding.subject.share_key,
        principal_key=finding.subject.principal_key,
        summary=(
            f"{severity.value.capitalize()} risk finding opened: {definition.title} on {where}."
        ),
        detail=dict(finding.detail),
    )


def _scope_key(watch: Watch) -> str:
    """The key the change feed's scope wants, which is not always the watch's own key.

    A ``group`` scope selects on a SID rather than on a principal key, because a membership
    edge's own key is built from SIDs. Handing it a principal key would match nothing, and
    matching nothing in a monitoring feature reads as a quiet estate.
    """
    if watch.kind is WatchKind.GROUP and watch.key.startswith("principal|"):
        return watch.key.rsplit("|", 1)[-1]
    return watch.key


def _notice(change: ObjectChange, labels: dict[str, str]) -> ChangeNotice:
    return ChangeNotice(
        kind=change.kind.value,
        key=change.key,
        action=change.action.value,
        container_key=change.subject.container_key,
        related_key=change.subject.related_key,
        severity=change.severity.value,
        direction=change.direction.value,
        at=change.after.valid_from,
        reasons=change.reasons,
        container_label=labels.get(change.subject.container_key or ""),
        related_label=labels.get(change.subject.related_key or ""),
        window=(
            None if change.window is None else (change.window.after, change.window.at_or_before)
        ),
    )


def _rights_text(mask: RightsMask) -> str:
    """A rights mask as the access engine itself labels it.

    Rendered here rather than in :mod:`app.alerts`, which must not hold a mask it would be
    tempted to interpret -- and by :func:`app.access_engine.summarize` rather than by a
    second rendering, so that an alert and the access screen cannot describe one grant in
    two different words.
    """
    return summarize(mask).label


# --------------------------------------------------------------------------------------
# The seam to ingestion
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PostRunOutcome:
    """What the post-completion pass did. Returned for tests and for the operator log.

    ``failed`` carries the reason when something went wrong. Nothing raises out of
    :func:`evaluate_run_after_commit`, and this is how a caller that cares can find out --
    without the collector's request ever seeing it.
    """

    ran: bool
    findings_opened: int = 0
    findings_resolved: int = 0
    alerts_notified: int = 0
    alerts_suppressed: int = 0
    deliveries_enqueued: int = 0
    delivered: int = 0
    failed: str | None = None
    notes: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        if not self.ran:
            return f"post-run evaluation skipped: {self.failed or 'disabled'}"
        return (
            f"post-run evaluation: {self.findings_opened} findings opened, "
            f"{self.findings_resolved} resolved, {self.alerts_notified} alerts notified, "
            f"{self.alerts_suppressed} suppressed, {self.deliveries_enqueued} queued, "
            f"{self.delivered} delivered" + (f"; FAILED: {self.failed}" if self.failed else "")
        )


async def evaluate_run_after_commit(
    database: Any,
    settings: Any,
    *,
    run_id: UUID,
    window_from: dt.datetime,
    now: dt.datetime | None = None,
) -> PostRunOutcome:
    r"""Re-evaluate the risk rules for one run, alert on what moved, and try to deliver.

    **Called after ``complete_run`` has committed, with its own session, and it never
    raises.** That ordering is the whole of the acceptance criterion *alert delivery failure
    does not roll back source ingestion*: by the time this begins, the run, its observations
    and its reconciliation are durable. A webhook that is down, an alert policy that was
    edited badly, or a defect anywhere below cannot cost the estate an observation — the
    worst case is an error in the log and an alert nobody received, and the second of those
    is visible in the delivery queue rather than silent.

    Three steps, each in its own transaction so that a failure in one keeps what the
    previous did:

    1. **Incremental risk evaluation** for the run. Only the rules whose facts moved, only
       over the subjects the run changed, and it may resolve only what it covered — the
       scope guard :class:`app.repositories.risk.RiskFindingRepository` applies.
    2. **Alert evaluation** over the run's own window, plus the findings step 1 opened and
       resolved. The window starts at the run's start rather than at a stored watermark;
       overlapping windows re-read changes and deduplication suppresses the repeats, which
       is deliberately the safe direction to be wrong in.
    3. **A delivery attempt**, bounded. What it does not get through stays in the outbox for
       the next drain, which is the point of the outbox.
    """
    if not getattr(settings, "alerts_on_run_completion", True):
        return PostRunOutcome(ran=False, failed="disabled by configuration")

    moment = now or dt.datetime.now(tz=dt.UTC)
    opened: list[RiskFinding] = []
    resolved: list[str] = []
    notes: list[str] = []
    evaluation_id: UUID | None = None

    try:
        async with database.session() as session:
            evaluation = await RiskService(
                RiskFactsRepository(session),
                RiskFindingRepository(session),
                settings.risk_configuration,
            ).evaluate_run(run_id, now=moment)
            evaluation_id = evaluation.evaluation_id
            matched = {finding.key: finding for finding in evaluation.findings}
            opened = [
                matched[key]
                for key in (*evaluation.outcome.opened, *evaluation.outcome.reopened)
                if key in matched
            ]
            resolved = list(evaluation.outcome.resolved)
            if not evaluation.complete:
                notes.append("the risk pass was incremental, so its counts are not totals")
            await session.commit()
    except Exception as error:
        logger.exception("alerts.post_run.risk_failed", extra={"run_id": str(run_id)})
        return PostRunOutcome(ran=True, failed=f"risk evaluation: {error}")

    notified = suppressed = enqueued = 0
    try:
        async with database.session() as session:
            service = AlertService(session, settings.alert_policy)
            changes = await service.evaluate_window(
                window_from=window_from, window_to=moment, source_run_id=run_id
            )
            findings = await service.evaluate_findings(
                opened=opened,
                resolved_keys=resolved,
                now=moment,
                evaluation_id=evaluation_id,
            )
            notified = len(changes.notified) + len(findings.notified)
            suppressed = len(changes.suppressed) + len(findings.suppressed)
            enqueued = changes.enqueued + findings.enqueued
            notes.extend(changes.truncated)
            await session.commit()
    except Exception as error:
        # The change half failed. The **finding** half is independent of it and is the one
        # trigger that fires estate-wide with no watch behind it, so it is attempted anyway:
        # returning here would let one misconfigured watch silence every critical finding in
        # the estate, which is precisely the quiet this feature exists to prevent.
        logger.exception("alerts.post_run.detection_failed", extra={"run_id": str(run_id)})
        notes.append(f"watch detection failed and was skipped: {error}")
        try:
            async with database.session() as session:
                findings = await AlertService(session, settings.alert_policy).evaluate_findings(
                    opened=opened,
                    resolved_keys=resolved,
                    now=moment,
                    evaluation_id=evaluation_id,
                )
                notified = len(findings.notified)
                suppressed = len(findings.suppressed)
                enqueued = findings.enqueued
                await session.commit()
        except Exception as second:
            logger.exception("alerts.post_run.findings_failed", extra={"run_id": str(run_id)})
            return PostRunOutcome(
                ran=True,
                findings_opened=len(opened),
                findings_resolved=len(resolved),
                failed=f"alert detection: {error}; findings: {second}",
                notes=tuple(notes),
            )

    delivered = 0
    try:
        async with database.session() as session:
            report = await AlertService(session, settings.alert_policy).dispatch(now=moment)
            delivered = report.delivered
            await session.commit()
    except Exception as error:
        # The alerts are already durable and queued. A delivery that could not be attempted
        # is the one failure here that costs nothing permanent: the next drain retries it.
        logger.warning(
            "alerts.post_run.dispatch_failed",
            extra={"run_id": str(run_id), "error": str(error)},
        )
        notes.append(f"delivery was not attempted: {error}")

    return PostRunOutcome(
        ran=True,
        findings_opened=len(opened),
        findings_resolved=len(resolved),
        alerts_notified=notified,
        alerts_suppressed=suppressed,
        deliveries_enqueued=enqueued,
        delivered=delivered,
        notes=tuple(notes),
    )
