r"""Watches, the alert feed, and the delivery queue.

Two capabilities divide these routes:

* ``alerts:read`` — the feed, one alert's history, the watch list, and the queue's state.
* ``alerts:manage`` — create, edit and remove a watch, and drain the queue by hand.

The split is not tidiness. A watch decides who gets woken up, so somebody who could quietly
disable the watch on the payroll share could make an exposure land in nobody's inbox — which
is exactly the shape of the thing this product exists to find, performed on the product.

Every capability is declared at the **route**, for the reason ``app/api/scan_runs.py``
states: route dependencies are solved before the handler's own, so a request that
authorization will refuse is refused before it costs a database session.

## What the feed is careful about

**Suppressed occurrences are visible.** ``GET /api/v1/alerts/{key}`` lists every event,
including the ones a cooldown held back, with the reason. An alert feed that showed only what
it delivered would make "you were not told" and "it did not happen" the same reading, which
is the failure this whole pipeline is built around.

**The queue's state is a first-class answer.** ``GET /api/v1/alerts/deliveries`` reports depth
*and* the age of the oldest undelivered item, because those separate the two cases that look
identical in a depth reading alone: a busy pipeline, and one nothing is draining. It also
reports abandoned deliveries, which are alerts somebody was meant to receive and did not.

**A watch that cannot fire is refused, not saved.** A membership trigger on a directory watch
would be configured, listed in the interface, and silent forever. :class:`app.alerts.Watch`
refuses it and this returns the refusal as a 422 naming what that kind of watch supports.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import AwareDatetime, BaseModel, Field

from app.alerts import (
    MAX_COOLDOWN,
    MIN_COOLDOWN,
    TRIGGER_DESCRIPTIONS,
    TRIGGERS_BY_WATCH_KIND,
    AlertPolicy,
    AlertStatus,
    AlertTrigger,
    DeliveryStatus,
    PolicyError,
    Watch,
    WatchKind,
    describe_policy,
)
from app.api.deps import PRINTABLE_IDENTIFIER, Session
from app.api.pagination import (
    MAX_LIMIT,
    PageInfo,
    decode_offset_cursor,
    encode_offset_cursor,
    normalize_limit,
)
from app.auth.dependencies import CurrentPrincipal, requires
from app.auth.roles import Capability
from app.config import Settings, get_settings
from app.domain import DomainValidationError
from app.models.schema import alert_deliveries
from app.models.schema import alerts as alerts_table
from app.repositories.alerts import (
    AlertRepository,
    StoredAlert,
    WatchConflict,
    WatchRepository,
    stored_alert,
)
from app.services.alerts import AlertService

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

#: Reading alerts, watches and the queue.
READ = Depends(requires(Capability.ALERTS_READ))

#: Configuring watches and draining the queue. An operations capability.
MANAGE = Depends(requires(Capability.ALERTS_MANAGE))

__all__ = ["router"]

SettingsDep = Annotated[Settings, Depends(get_settings)]

AlertKeyPath = Annotated[
    str,
    Path(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$", description="The alert key."),
]

#: How long a delivery may sit undelivered before it is counted as stale. Fifteen minutes is
#: long enough that an ordinary retry cycle does not register and short enough that a drain
#: nobody is running shows up within one working coffee break.
STALE_AFTER = dt.timedelta(minutes=15)


# ----------------------------------------------------------------------------- views


class WatchView(BaseModel):
    """One standing subscription."""

    watch_id: UUID
    kind: str
    key: str = Field(description="The case-folded key of the thing watched.")
    label: str
    triggers: list[str]
    cooldown_seconds: int = Field(
        description=(
            "How long this watch stays quiet after notifying. Occurrences inside the window "
            "are recorded and counted, never discarded, and the next notification says how "
            "many it stands for."
        )
    )
    enabled: bool
    notes: str | None = None
    created_by: str
    created_at: dt.datetime
    updated_at: dt.datetime


class WatchKindView(BaseModel):
    """One kind of thing that can be watched, and what it can be told about."""

    kind: str
    triggers: list[str]
    covers: str


class WatchesResponse(BaseModel):
    watches: list[WatchView]
    #: What each kind supports, so a client can build the form without hard-coding the table
    #: and without being able to disagree with the server about it.
    kinds: list[WatchKindView]
    min_cooldown_seconds: int
    max_cooldown_seconds: int
    default_cooldown_seconds: int = Field(
        description=(
            "What a new watch gets if it does not name one -- this installation's configured "
            "default. Served rather than assumed, so a client's form shows the value the "
            "watch will actually be created with."
        )
    )


class WatchCreate(BaseModel):
    """A new watch."""

    kind: str = Field(description="resource, share or group.")
    key: str = Field(
        min_length=1,
        max_length=512,
        pattern=PRINTABLE_IDENTIFIER,
        description=(
            "A directory's UNC path (\\\\fs01\\finance), a share key "
            "(<server>|<share>, e.g. fs01|finance -- not the 'share|' prefixed form, which "
            "is an observation source key), or a group's principal key or bare SID. "
            "Case-folded on the way in. A key the change feed could never scope on is "
            "refused with 422 rather than saved."
        ),
    )
    label: str = Field(min_length=1, max_length=200)
    triggers: list[str] = Field(
        min_length=1,
        description="What to be told about. Must be supported by the kind; see /alerts/watches.",
    )
    cooldown_seconds: int | None = Field(
        default=None,
        ge=int(MIN_COOLDOWN.total_seconds()),
        le=int(MAX_COOLDOWN.total_seconds()),
        description="Defaults to the installation's configured cooldown.",
    )
    enabled: bool = True
    notes: str | None = Field(default=None, max_length=2000)


class WatchUpdate(BaseModel):
    """An edit. The kind and the key are the watch's identity and cannot be changed.

    Re-pointing a watch would silently re-attribute every alert already raised under it, and
    the history would then say a directory was edited when it was not. Delete and create one.
    """

    label: str | None = Field(default=None, min_length=1, max_length=200)
    triggers: list[str] | None = Field(default=None, min_length=1)
    cooldown_seconds: int | None = Field(
        default=None,
        ge=int(MIN_COOLDOWN.total_seconds()),
        le=int(MAX_COOLDOWN.total_seconds()),
    )
    enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)


class AlertView(BaseModel):
    """One alert, as a row in the feed."""

    alert_key: str
    trigger: str
    trigger_description: str
    lifecycle: str = Field(
        description=(
            "stateful alerts mirror a condition and can resolve and reopen. transient ones "
            "report something that happened; they never resolve, because an edit cannot "
            "un-happen, and they age out of a view by date."
        )
    )
    status: str
    summary: str
    watch_id: UUID | None = None
    watch_label: str | None = None
    resource_key: str | None = None
    share_key: str | None = None
    principal_key: str | None = None
    first_raised_at: dt.datetime
    last_raised_at: dt.datetime
    last_notified_at: dt.datetime | None = None
    resolved_at: dt.datetime | None = None
    occurrence_count: int
    suppressed_total: int = Field(
        description=(
            "Occurrences recorded but not delivered, ever. Above zero means this alert has "
            "happened more often than anybody was told."
        )
    )
    suppressed_since_notice: int
    payload: dict[str, Any] = Field(default_factory=dict)
    detail_url: str


class AlertEventView(BaseModel):
    """One occurrence, delivered or not."""

    event_id: UUID
    transition: str
    suppression_reason: str | None = Field(
        default=None,
        description=(
            "Why this occurrence was not delivered. identical_content means detection ran "
            "twice; within_cooldown means something new arrived inside the quiet window; "
            "watch_disabled means the subscription was off and the occurrence was kept "
            "anyway."
        ),
    )
    notified: bool
    folds: int = Field(
        description="Occurrences this event speaks for, itself included. Above 1 means a "
        "cooldown folded others into it."
    )
    summary: str
    occurred_at: dt.datetime
    recorded_at: dt.datetime
    payload_digest: str
    source_run_id: UUID | None = None
    source_evaluation_id: UUID | None = None


class DeliveryView(BaseModel):
    """One attempt to put an alert somewhere."""

    delivery_id: UUID
    sink_name: str
    status: str
    attempts: int
    last_error: str | None = None
    enqueued_at: dt.datetime
    next_attempt_at: dt.datetime
    delivered_at: dt.datetime | None = None


class AlertDetailView(BaseModel):
    """One alert, every occurrence of it, and where each one went."""

    alert: AlertView
    events: list[AlertEventView]
    deliveries: list[DeliveryView]


class AlertsResponse(BaseModel):
    filters: dict[str, Any]
    items: list[AlertView]
    status_counts: dict[str, int]
    trigger_counts: dict[str, int]
    page: PageInfo


class QueueView(BaseModel):
    """The delivery queue, as an operator needs to read it."""

    depth: dict[str, int] = Field(description="Deliveries in each state.")
    stale: int = Field(
        description=(
            "Deliveries due more than fifteen minutes ago and still undelivered. Depth alone "
            "cannot separate a busy pipeline from one nothing is draining; this can."
        )
    )
    abandoned: int = Field(
        description=(
            "Alerts somebody was meant to receive and did not, because the retry schedule ran "
            "out or the sink reported a failure retrying cannot fix. Nothing deletes these."
        )
    )
    oldest_pending_at: dt.datetime | None = None
    policy: list[str] = Field(
        description=(
            "The alert policy in force, in sentences -- including the triggers that are off "
            "and whether anything is configured to receive a delivery at all."
        )
    )


class DrainResponse(BaseModel):
    """What one hand-run drain did."""

    attempted: int
    delivered: int
    retrying: int
    abandoned: int
    unroutable: int
    has_more: bool
    errors: list[str]
    summary: str


# ---------------------------------------------------------------------------- routes


@router.get(
    "/watches",
    response_model=WatchesResponse,
    summary="List watches",
    dependencies=[READ],
)
async def list_watches(
    session: Session,
    settings: SettingsDep,
    kind: Annotated[str | None, Query(description="resource, share or group.")] = None,
) -> WatchesResponse:
    """Every watch, and the table of what each kind can be told about.

    The table is served rather than hard-coded in the client for the reason the navigation's
    capability list is: a second copy of a rule is a second copy that can be wrong, and the
    wrong one is always the one somebody trusts.
    """
    selected = _watch_kind(kind) if kind else None
    watches = await WatchRepository(session).list(kind=selected)
    return WatchesResponse(
        watches=[_watch_view(watch) for watch in watches],
        kinds=[
            WatchKindView(
                kind=member.value,
                triggers=sorted(trigger.value for trigger in TRIGGERS_BY_WATCH_KIND[member]),
                covers=(member.__doc__ or "").strip(),
            )
            for member in WatchKind
        ],
        min_cooldown_seconds=int(MIN_COOLDOWN.total_seconds()),
        max_cooldown_seconds=int(MAX_COOLDOWN.total_seconds()),
        default_cooldown_seconds=int(_policy(settings).default_cooldown.total_seconds()),
    )


@router.post(
    "/watches",
    response_model=WatchView,
    status_code=status.HTTP_201_CREATED,
    summary="Create a watch",
    dependencies=[MANAGE],
    responses={
        409: {"description": "A watch already covers that thing."},
        422: {"description": "A trigger that kind of watch could never fire."},
    },
)
async def create_watch(
    body: WatchCreate,
    session: Session,
    principal: CurrentPrincipal,
    settings: SettingsDep,
) -> WatchView:
    """Subscribe to one directory, share or group.

    **One watch per thing.** A second on the same directory would double every alert about it
    and give each copy its own cooldown, so an operator who set a quiet window would still be
    notified at the other watch's rate. A duplicate is a 409 naming the existing watch.
    """
    policy = _policy(settings)
    now = dt.datetime.now(tz=dt.UTC)
    cooldown = (
        dt.timedelta(seconds=body.cooldown_seconds)
        if body.cooldown_seconds is not None
        else policy.default_cooldown
    )
    try:
        kind = _watch_kind(body.kind)
        watch = Watch(
            watch_id=uuid4(),
            kind=kind,
            key=_checked_key(kind, body.key),
            label=body.label,
            triggers=_triggers(body.triggers),
            cooldown=cooldown,
            created_at=now,
            updated_at=now,
            created_by=principal.subject,
            enabled=body.enabled,
            notes=body.notes,
        )
        stored = await WatchRepository(session).create(watch, actor=principal.subject)
    except WatchConflict as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except DomainValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    await session.commit()
    return _watch_view(stored)


@router.patch(
    "/watches/{watch_id}",
    response_model=WatchView,
    summary="Edit a watch",
    dependencies=[MANAGE],
    responses={
        404: {"description": "No such watch."},
        422: {"description": "A trigger that kind of watch could never fire."},
    },
)
async def update_watch(
    watch_id: UUID,
    body: WatchUpdate,
    session: Session,
    principal: CurrentPrincipal,
) -> WatchView:
    """Change a watch's label, triggers, cooldown, notes, or whether it is on.

    Disabling is not deleting, and the difference is deliberate: a disabled watch still
    records what happens to the thing it covers, marked ``watch_disabled``. Turning it back
    on then shows what it missed, where a deleted watch would have kept nothing.
    """
    repository = WatchRepository(session)
    existing = await repository.get(watch_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No watch {watch_id}.")
    try:
        updated = Watch(
            watch_id=existing.watch_id,
            kind=existing.kind,
            key=existing.key,
            label=body.label if body.label is not None else existing.label,
            triggers=(_triggers(body.triggers) if body.triggers is not None else existing.triggers),
            cooldown=(
                dt.timedelta(seconds=body.cooldown_seconds)
                if body.cooldown_seconds is not None
                else existing.cooldown
            ),
            created_at=existing.created_at,
            updated_at=dt.datetime.now(tz=dt.UTC),
            created_by=existing.created_by,
            enabled=body.enabled if body.enabled is not None else existing.enabled,
            notes=body.notes if body.notes is not None else existing.notes,
        )
        await repository.update(updated, actor=principal.subject)
    except DomainValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    await session.commit()
    return _watch_view(updated)


@router.delete(
    "/watches/{watch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a watch",
    dependencies=[MANAGE],
    responses={404: {"description": "No such watch."}},
)
async def delete_watch(watch_id: UUID, session: Session) -> None:
    """Delete a watch. **The alerts it raised are kept.**

    An alert is the record that somebody was told something, and deleting the subscription
    does not un-tell them. Nothing here cascades.
    """
    if not await WatchRepository(session).delete(watch_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No watch {watch_id}.")
    await session.commit()


@router.get(
    "",
    response_model=AlertsResponse,
    summary="The alert feed",
    dependencies=[READ],
    responses={422: {"description": "An unknown filter value, or a malformed cursor."}},
)
async def list_alerts(
    session: Session,
    status_: Annotated[
        list[str] | None, Query(alias="status", description="open or resolved.")
    ] = None,
    trigger: Annotated[list[str] | None, Query(alias="trigger")] = None,
    watch_id: Annotated[UUID | None, Query()] = None,
    since: Annotated[AwareDatetime | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=MAX_LIMIT)] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> AlertsResponse:
    """Alerts, newest first.

    Unlike the risk report, the default here is **every status**. A transient alert never
    resolves, so filtering to open alerts by default would be a filter that does nothing for
    three of the four triggers while looking like it does something.
    """
    page_size = normalize_limit(limit)
    offset = decode_offset_cursor(cursor)
    statuses = _parse_set(status_, AlertStatus, "status")
    triggers = _parse_set(trigger, AlertTrigger, "trigger")

    clauses: list[Any] = []
    if statuses:
        clauses.append(alerts_table.c.status.in_(sorted(item.value for item in statuses)))
    if triggers:
        clauses.append(alerts_table.c.trigger.in_(sorted(item.value for item in triggers)))
    if watch_id is not None:
        clauses.append(alerts_table.c.watch_id == watch_id)
    if since is not None:
        clauses.append(alerts_table.c.last_raised_at >= since)

    rows = (
        await session.execute(
            sa.select(alerts_table)
            .where(*clauses)
            .order_by(alerts_table.c.last_raised_at.desc(), alerts_table.c.alert_key.asc())
            .limit(page_size + 1)
            .offset(offset)
        )
    ).all()
    has_more = len(rows) > page_size
    total = int(
        (
            await session.execute(
                sa.select(sa.func.count()).select_from(alerts_table).where(*clauses)
            )
        ).scalar_one()
    )
    return AlertsResponse(
        filters={
            "status": sorted(item.value for item in statuses),
            "trigger": sorted(item.value for item in triggers),
            "watch_id": str(watch_id) if watch_id else None,
            "since": since.isoformat() if since else None,
        },
        items=[_alert_view(stored_alert(row)) for row in rows[:page_size]],
        status_counts=await _counts(session, alerts_table.c.status, AlertStatus, clauses),
        trigger_counts=await _counts(session, alerts_table.c.trigger, AlertTrigger, clauses),
        page=PageInfo(
            limit=page_size,
            has_more=has_more,
            next_cursor=encode_offset_cursor(offset + page_size) if has_more else None,
            total=total,
        ),
    )


@router.get(
    "/deliveries",
    response_model=QueueView,
    summary="The delivery queue",
    dependencies=[READ],
)
async def delivery_queue(session: Session, settings: SettingsDep) -> QueueView:
    """Depth, staleness, abandonment, and the policy in force.

    All four, because each answers a question the others cannot. Depth says how much is
    waiting. Staleness separates a busy pipeline from one nothing is draining. Abandonment
    counts the alerts nobody received. And the policy says whether anything is configured to
    receive a delivery at all — an installation with every sink disabled has a depth that
    only grows, and no other field here would say why.
    """
    policy = _policy(settings)
    now = dt.datetime.now(tz=dt.UTC)
    service = AlertService(session, policy)
    depth = await service.outbox.depth(now=now)
    stale = await service.outbox.pending_older_than(now - STALE_AFTER)
    oldest = (
        await session.execute(
            sa.select(sa.func.min(alert_deliveries.c.next_attempt_at)).where(
                alert_deliveries.c.status.in_(
                    (DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value)
                )
            )
        )
    ).scalar_one_or_none()
    return QueueView(
        depth={key.value: value for key, value in depth.items()},
        stale=stale,
        abandoned=depth[DeliveryStatus.ABANDONED],
        oldest_pending_at=oldest,
        policy=list(describe_policy(policy)),
    )


@router.post(
    "/deliveries/drain",
    response_model=DrainResponse,
    summary="Attempt the deliveries that are due",
    dependencies=[MANAGE],
)
async def drain_queue(
    session: Session,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DrainResponse:
    """Run one bounded drain by hand.

    Bounded, and it reports whether more remained. The supported way to drain continuously is
    ``python -m app.alerts``; this exists so that an operator who has just fixed a webhook can
    push the backlog through without waiting for the next scheduled pass.
    """
    policy = _policy(settings)
    report = await AlertService(session, policy).dispatch(
        now=dt.datetime.now(tz=dt.UTC), limit=limit
    )
    await session.commit()
    return DrainResponse(
        attempted=report.attempted,
        delivered=report.delivered,
        retrying=report.retrying,
        abandoned=report.abandoned,
        unroutable=report.unroutable,
        has_more=report.has_more,
        errors=list(report.errors),
        summary=report.summary,
    )


@router.get(
    "/{key}",
    response_model=AlertDetailView,
    summary="One alert, every occurrence, and where each went",
    dependencies=[READ],
    responses={404: {"description": "No such alert."}},
)
async def get_alert(key: AlertKeyPath, session: Session) -> AlertDetailView:
    """The alert, its whole event history including the suppressed occurrences, and its
    deliveries.

    The suppressed events are the point of this route. Without them, an operator asking "why
    was I not told about the other four changes" has no answer, and a cooldown becomes
    indistinguishable from a pipeline that silently dropped them.
    """
    repository = AlertRepository(session)
    record = await repository.get(key)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No alert {key}.")
    events = await repository.events_for(key)
    deliveries = await repository.deliveries_for(key)
    return AlertDetailView(
        alert=_alert_view(record),
        events=[_event_view(row) for row in events],
        deliveries=[_delivery_view(row) for row in deliveries],
    )


# ------------------------------------------------------------------------ renderers


def _policy(settings: Settings) -> AlertPolicy:
    try:
        return settings.alert_policy
    except PolicyError as error:  # pragma: no cover - startup validates the path
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)
        ) from error


def _watch_kind(value: str) -> WatchKind:
    try:
        return WatchKind(value.strip())
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"{value!r} is not a watch kind. Known kinds: "
                f"{sorted(member.value for member in WatchKind)!r}."
            ),
        ) from error


def _checked_key(kind: WatchKind, key: str) -> str:
    """Refuse a key the change feed could never scope on.

    The same principle as refusing a trigger a kind cannot fire, applied to the other half of
    a watch's identity. A directory watch whose key is not a UNC path, or a group watch whose
    key is not a SID or a principal key, is accepted by every validator a watch has -- and
    then fails inside every detection pass, forever, leaving a log line and a feed that looks
    quiet.

    The shapes are the change feed's own (`app.changes.scope`), so this cannot drift into
    accepting something that module would refuse: it builds the scope the pass will build and
    lets it object.
    """
    from app.changes import ChangeScope
    from app.changes.scope import predicates_for
    from app.services.alerts import _SCOPE_FOR_WATCH, _scope_key

    probe = Watch(
        watch_id=uuid4(),
        kind=kind,
        key=key,
        label="probe",
        triggers=TRIGGERS_BY_WATCH_KIND[kind],
        cooldown=MIN_COOLDOWN,
        created_at=dt.datetime.now(tz=dt.UTC),
        updated_at=dt.datetime.now(tz=dt.UTC),
        created_by="probe",
    )
    try:
        # Built *and exercised*: ChangeScope's own validation only refuses an empty key,
        # and the parse that rejects a malformed UNC path happens when the predicates are
        # built. Constructing the scope alone would accept the key here and then fail on
        # every detection pass afterwards, which is the state this check exists to stop.
        predicates_for(ChangeScope(target=_SCOPE_FOR_WATCH[kind], key=_scope_key(probe)))
    except DomainValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"{key!r} is not a key a {kind.value} watch can be matched on: {error} "
                "Refused now rather than saved: a watch with a key nothing can scope is "
                "configured, listed, and silent forever, which reads as a quiet estate."
            ),
        ) from error
    return key


def _triggers(values: list[str]) -> frozenset[AlertTrigger]:
    parsed = _parse_set(values, AlertTrigger, "trigger")
    if not parsed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A watch must subscribe to at least one trigger.",
        )
    return frozenset(parsed)


def _parse_set(values: list[str] | None, enum: type[Any], name: str) -> frozenset[Any]:
    """Every value, or a 422 naming the one that is wrong. Never silently dropped."""
    if not values:
        return frozenset()
    known = {member.value: member for member in enum}
    parsed: set[Any] = set()
    for value in values:
        member = known.get(value.strip())
        if member is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{value!r} is not a {name}. Known values: {sorted(known)!r}.",
            )
        parsed.add(member)
    return frozenset(parsed)


async def _counts(session: Any, column: Any, enum: type[Any], clauses: list[Any]) -> dict[str, int]:
    """Counts over the filter, with every member present including the zeros.

    A facet list that omits the empty triggers makes a feed with no membership alerts look
    like a feed that has no membership category, and a reader cannot tell which.
    """
    rows = (
        await session.execute(
            sa.select(column, sa.func.count().label("total")).where(*clauses).group_by(column)
        )
    ).all()
    counts = {member.value: 0 for member in enum}
    for row in rows:
        counts[str(row[0])] = int(row.total)
    return counts


def _watch_view(watch: Watch) -> WatchView:
    return WatchView(
        watch_id=watch.watch_id,
        kind=watch.kind.value,
        key=watch.key,
        label=watch.label,
        triggers=sorted(trigger.value for trigger in watch.triggers),
        cooldown_seconds=int(watch.cooldown.total_seconds()),
        enabled=watch.enabled,
        notes=watch.notes,
        created_by=watch.created_by,
        created_at=watch.created_at,
        updated_at=watch.updated_at,
    )


def _alert_view(record: StoredAlert) -> AlertView:
    trigger = record.trigger
    return AlertView(
        alert_key=record.key,
        trigger=trigger.value,
        trigger_description=TRIGGER_DESCRIPTIONS[trigger],
        lifecycle=record.lifecycle.value,
        status=record.status.value,
        summary=record.summary,
        watch_id=record.watch_id,
        watch_label=record.watch_label,
        resource_key=record.resource_key,
        share_key=record.share_key,
        principal_key=record.principal_key,
        first_raised_at=record.first_raised_at,
        last_raised_at=record.last_raised_at,
        last_notified_at=record.last_notified_at,
        resolved_at=record.resolved_at,
        occurrence_count=int(record.occurrence_count),
        suppressed_total=int(record.suppressed_total),
        suppressed_since_notice=int(record.suppressed_since_notice),
        payload=dict(record.payload or {}),
        detail_url=f"/api/v1/alerts/{record.key}",
    )


def _event_view(row: Any) -> AlertEventView:
    return AlertEventView(
        event_id=row["event_id"],
        transition=row["transition"],
        suppression_reason=row["suppression_reason"],
        notified=bool(row["notified"]),
        folds=int(row["folds"]),
        summary=row["summary"],
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
        payload_digest=row["payload_digest"],
        source_run_id=row["source_run_id"],
        source_evaluation_id=row["source_evaluation_id"],
    )


def _delivery_view(row: Any) -> DeliveryView:
    return DeliveryView(
        delivery_id=row["delivery_id"],
        sink_name=row["sink_name"],
        status=row["status"],
        attempts=int(row["attempts"]),
        last_error=row["last_error"],
        enqueued_at=row["enqueued_at"],
        next_attempt_at=row["next_attempt_at"],
        delivered_at=row["delivered_at"],
    )
