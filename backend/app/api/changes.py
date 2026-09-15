r"""Change endpoints: what moved, whether it matters, and what it did.

Everything under ``/api/v1/changes`` is **derived**, in the same sense the access endpoints
are: collectors report states, and a change is a relation between two of them that ADG
computed. So the rules the derived-response contract already states apply here without
amendment — the conclusion is carried in the response, never recomputed by a client — and
three of them are worth restating because they shape every field below.

* **A change happened in a window, never at an instant.** ``window`` carries both ends
  (ADR-0019). ``at`` sits beside it and is *the instant the resulting version opened* — the
  moment somebody looked, which is what the feed sorts and pages on and is not the moment
  the change happened. A client that renders ``at`` as the change time has reverted to the
  model ADR-0019 rejected; render ``window``.
* **``first_observed`` is not a creation.** It means ADG had no prior view of the thing that
  contains the object. It is excluded from the default feed, counted in every summary, and
  must never be rendered as "added".
* **A filtered page says what it hid.** ``GET /summary`` counts the whole window before the
  filter, so a client can show "12 shown, 431 hidden" rather than a clean-looking page that
  is clean because of a default.

## Two routers, two capabilities

The feed, the summary, the timeline and the comparison require ``changes:read``. The impact
endpoint requires ``access:read``, because it discloses effective access — what a principal
could do to a resource — which is a different and more sensitive answer than the list of
what was edited. The same distinction ``/api/v1/groups`` already draws between knowing who
is in a group and knowing what that group reaches.

## Nothing here mutates anything

Every route is a ``GET``. ADG remains read-only; a change endpoint that could revert a
change would be remediation, which is Phase 8 at the earliest and reserved behind a role
that grants nothing today.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.access_engine import AccessPath, RightsMask
from app.api.access import RightsView, render_rights
from app.api.deps import PRINTABLE_IDENTIFIER, Session, TraversalBounds
from app.api.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    InvalidCursor,
    PageInfo,
    decode_keyset_cursor,
    encode_keyset_cursor,
    normalize_limit,
)
from app.changes import (
    AceEdit,
    ChangeAction,
    ChangeComparison,
    ChangeFeed,
    ChangeFilter,
    ChangeImpact,
    ChangeImpactService,
    ChangeScope,
    ChangeService,
    ChangeSeverity,
    ChangeSignificance,
    ChangeSummary,
    Correlation,
    ObjectChange,
    ObjectChanges,
    ScopeTarget,
    VersionCursor,
)
from app.changes.correlation import key_of
from app.changes.impact import AccessDelta, MembershipDelta
from app.contracts.v1.common import ObservationKind
from app.domain import DomainValidationError
from app.history.model import ChangeWindow, ObjectVersion

router = APIRouter(prefix="/api/v1/changes", tags=["changes"])
impact_router = APIRouter(prefix="/api/v1/changes", tags=["changes"])

__all__ = ["impact_router", "router"]


# ------------------------------------------------------------------- parameters

FromQuery = Annotated[
    dt.datetime,
    Query(
        alias="from",
        description=(
            "Start of the window, inclusive. Must carry a UTC offset: a naive instant "
            "cannot be ordered against observations from a host in another time zone, and "
            "a window an hour out reports a different day's changes."
        ),
    ),
]

ToQuery = Annotated[
    dt.datetime,
    Query(
        alias="to",
        description=(
            "End of the window, exclusive, so that two adjacent windows partition the "
            "timeline and a change on the boundary is reported in exactly one of them."
        ),
    ),
]

ScopeQuery = Annotated[
    str | None,
    Query(
        max_length=1024,
        pattern=PRINTABLE_IDENTIFIER,
        description="At most one scope may be given across server/share/directory/principal/group.",
    ),
]

KindQuery = Annotated[
    list[ObservationKind] | None,
    Query(alias="kind", description="Repeatable. Omit for every kind."),
]

ActionQuery = Annotated[
    list[ChangeAction] | None,
    Query(
        alias="action",
        description=(
            "Repeatable. Defaults to added, modified and removed. 'first_observed' is "
            "excluded unless asked for: it records that ADG started looking, not that "
            "anything was created, and an estate's first scan produces one per object."
        ),
    ),
]

SignificanceQuery = Annotated[
    list[ChangeSignificance] | None,
    Query(
        alias="significance",
        description=(
            "Repeatable. Defaults to security and undetermined. Pass 'metadata' and 'noise' "
            "as well to see everything; /summary always counts everything either way."
        ),
    ),
]

SeverityQuery = Annotated[
    ChangeSeverity,
    Query(
        alias="min_severity",
        description="Minimum severity, by the severity ordering and not alphabetically.",
    ),
]

LimitQuery = Annotated[
    int | None,
    Query(ge=1, le=MAX_LIMIT, description=f"Changes per page (default {DEFAULT_LIMIT})."),
]

CursorQuery = Annotated[
    str | None, Query(description="Opaque cursor from a previous response's next_cursor.")
]


# ------------------------------------------------------------------------ views


class ChangeWindowView(BaseModel):
    """When a change happened, as the interval it is known to have happened inside."""

    after: dt.datetime = Field(
        description="The last instant the previous state was confirmed. The change is later."
    )
    at_or_before: dt.datetime = Field(
        description="The observation that contradicted it. The change had happened by then."
    )
    is_exact: bool = Field(
        description="True when two observations bracket the change to a single instant."
    )
    duration_seconds: float = Field(description="How wide the uncertainty is. Zero when is_exact.")


class VersionView(BaseModel):
    """One side of a change: a state, and the interval it was observed over."""

    present: bool = Field(
        description=(
            "False is a tombstone: a scan that reconciled a scope containing this object "
            "looked and did not find it. It is never the same as having no version at all."
        )
    )
    valid_from: dt.datetime
    last_seen_at: dt.datetime = Field(
        description="The newest observation that confirmed this state."
    )
    valid_to: dt.datetime | None = Field(
        default=None, description="Null while this is the state currently believed to hold."
    )
    origin: str = Field(
        description=(
            "'observed', or 'backfilled' when the Phase 7A migration reconstructed this "
            "version from a pre-history row that kept no evidence of intermediate states."
        )
    )
    opened_by_run_id: str
    last_seen_run_id: str
    state: dict[str, Any] | None = Field(
        default=None,
        description="The stored state, or null for a tombstone. This is the before/after.",
    )


class FieldDeltaView(BaseModel):
    """One field that moved."""

    field: str
    before: Any = None
    after: Any = None
    significance: str = Field(
        description=(
            "identity, security, metadata, noise, order, derived, or unclassified. 'order' "
            "is resolved per change rather than per field: see the change's own "
            "significance."
        )
    )


class SubjectView(BaseModel):
    """What the change is about, for a reader who thinks in resources and people."""

    container_kind: str | None = None
    container_key: str | None = Field(
        default=None, description="The share of an ACE, the group of a membership edge."
    )
    related_kind: str | None = None
    related_key: str | None = Field(
        default=None, description="The trustee of an ACE, the member of a membership edge."
    )


class ChangeView(BaseModel):
    """One classified change."""

    kind: str
    key: str
    action: str = Field(
        description=(
            "added, modified, removed, or first_observed. 'first_observed' is not a "
            "creation; render it as 'first seen'."
        )
    )
    significance: str
    direction: str = Field(
        description=(
            "Which way this *edit* moved access. Not whether anybody's effective access "
            "moved — ask /changes/impact for that, and expect the two to disagree."
        )
    )
    severity: str
    reasons: list[str] = Field(
        description="Why this severity and direction, in sentences. Render, never parse."
    )
    rule_ids: list[str] = Field(
        description="The rule that fired, for support and for grepping the rule table."
    )
    at: dt.datetime = Field(
        description=(
            "The instant the resulting version opened: when somebody looked, not when the "
            "change happened. Sort and page on this; render 'window'."
        )
    )
    window: ChangeWindowView | None = Field(
        default=None,
        description=(
            "Null only when there is no earlier version to bound the change, which is "
            "exactly when the action is first_observed."
        ),
    )
    reconstructed: bool = Field(
        description=(
            "True when either side came from the Phase 7A backfill. Such a version spans "
            "everything the old schema knew and conceals any state held inside it."
        )
    )
    subject: SubjectView
    before: VersionView | None = None
    after: VersionView
    deltas: list[FieldDeltaView] = Field(
        default_factory=list,
        description="Empty for added, removed and first_observed: there is one state, not two.",
    )
    edit: int | None = Field(
        default=None,
        description=(
            "Index into this response's 'edits' when this change is one half of an ACL "
            "entry being rewritten. Two changes sharing an index are one edit."
        ),
    )


class AceEditView(BaseModel):
    """A removal and an addition that are one edit of one ACL entry."""

    index: int
    kind: str
    container_key: str = Field(description="The share or directory whose ACL was edited.")
    trustee_key: str
    ace_type: str
    direction: str = Field(
        description=(
            "Computed by comparing the two masks, not assigned by a rule. A Deny inverts: "
            "a Deny whose mask grew withholds more, so it narrows access."
        )
    )
    severity: str
    summary: str
    rights_before: RightsView | None = None
    rights_after: RightsView | None = None


class FiltersView(BaseModel):
    """The filter that produced this list, echoed back."""

    window_from: dt.datetime
    window_to: dt.datetime
    scope_target: str | None = None
    scope_key: str | None = None
    kinds: list[str] | None = None
    actions: list[str]
    significance: list[str]
    min_severity: str


class ChangesResponse(BaseModel):
    """One page of changes."""

    filters: FiltersView
    changes: list[ChangeView]
    edits: list[AceEditView] = Field(
        description=(
            "ACL entries rewritten, paired from the removal and addition that record them. "
            "ADG stores an ACE's rights as part of its identity, so one edit is two rows."
        )
    )
    page: PageInfo
    scanned: int = Field(description="Raw versions examined to build this page, filtered or not.")
    scan_exhausted: bool = Field(
        description=(
            "True when the page ended because the server's scan budget ran out rather than "
            "because the page filled or the window did. More changes exist either way; a "
            "short page with this set is not the end of the data."
        )
    )


class SummaryResponse(BaseModel):
    """Counts over the whole window, taken before any filter."""

    window_from: dt.datetime
    window_to: dt.datetime
    total: int
    returned: int = Field(description="How many the given filter would let through.")
    excluded: int = Field(description="total - returned. What a filtered page is not showing.")
    highest_severity: str | None = None
    reconstructed: int
    truncated: bool = Field(
        description=(
            "True when the window holds more changes than the summary is allowed to "
            "classify, so the counts are a floor rather than a total."
        )
    )
    by_action: dict[str, int]
    by_significance: dict[str, int]
    by_severity: dict[str, int]
    by_kind: dict[str, int]


class TimelineResponse(BaseModel):
    """Every transition of one object, newest first."""

    kind: str
    key: str
    changes: list[ChangeView]
    edits: list[AceEditView]
    truncated: bool = Field(
        description="True when older versions exist beyond the page that was read."
    )


class ComparisonResponse(BaseModel):
    """What is different between two instants."""

    at_from: dt.datetime
    at_to: dt.datetime
    scope_target: str | None = None
    scope_key: str | None = None
    changes: list[ChangeView]
    edits: list[AceEditView]
    unchanged: int = Field(description="Objects present and identical at both instants.")
    unobserved_at_from: int = Field(
        description=(
            "Objects covered at the later instant and by no version at all at the earlier "
            "one. Counted, never reported as additions: nothing had looked yet."
        )
    )
    unobserved_at_to: int = Field(
        description=(
            "Objects covered at the earlier instant and by no version at the later one. "
            "Counted, never reported as removals: reporting these would revoke access on "
            "the strength of nobody having looked."
        )
    )
    truncated: bool
    summary: SummaryResponse


class AccessSideView(BaseModel):
    """One effective-access answer, at one instant."""

    at: dt.datetime
    access: bool
    outcome: str = Field(
        description="granted, denied, no_grant, or indeterminate. Branch on this, never on access."
    )
    conclusive: bool
    rights: RightsView
    certainty: str = Field(description="How firmly the historical inputs were grounded.")
    reason: str


class AccessDeltaView(BaseModel):
    """What one principal could do to one resource, either side of the change."""

    subject_key: str
    resource_key: str
    access_path: str
    before: AccessSideView
    after: AccessSideView
    gained: RightsView
    lost: RightsView
    direction: str = Field(
        description=(
            "Which way *effective* access moved. Compare with the change's own direction: "
            "an ACL that broadened while this is 'neutral' was overridden by the other "
            "layer, by a Deny, or by the principal's own state."
        )
    )
    certainty: str
    conclusive: bool = Field(
        description=(
            "False when either answer was indeterminate. While it is false, 'neutral' is "
            "not evidence that nothing changed — only that nothing could be established."
        )
    )


class MembershipDeltaView(BaseModel):
    """Which groups a principal reached either side of the change."""

    subject_key: str
    gained: list[str]
    lost: list[str]
    before_count: int
    after_count: int
    certainty: str


class ImpactResponse(BaseModel):
    """Why access changed, resolved by the live engine over historical inputs."""

    change: ChangeView
    at_before: dt.datetime
    at_after: dt.datetime
    verdict: str = Field(
        description=(
            "resolved, needs_a_subject, needs_a_resource, unbounded, or not_applicable. "
            "Anything but 'resolved' means the engine was not given a pair to resolve, not "
            "that nothing happened."
        )
    )
    explanation: str
    access: AccessDeltaView | None = None
    membership: MembershipDeltaView | None = None


# ----------------------------------------------------------------------- routes


@router.get(
    "",
    response_model=ChangesResponse,
    summary="What changed in a window",
    description=(
        "Classified transitions, newest first. Defaults to security-relevant and "
        "unclassifiable changes and excludes first sightings; call /summary for the counts "
        "the filter is hiding."
    ),
)
async def list_changes(
    session: Session,
    window_from: FromQuery,
    window_to: ToQuery,
    server: ScopeQuery = None,
    share: ScopeQuery = None,
    directory: ScopeQuery = None,
    principal: ScopeQuery = None,
    group: ScopeQuery = None,
    kind: KindQuery = None,
    action: ActionQuery = None,
    significance: SignificanceQuery = None,
    min_severity: SeverityQuery = ChangeSeverity.INFO,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> ChangesResponse:
    size = normalize_limit(limit)
    filters = _filter(
        window_from,
        window_to,
        server=server,
        share=share,
        directory=directory,
        principal=principal,
        group=group,
        kind=kind,
        action=action,
        significance=significance,
        min_severity=min_severity,
        limit=size,
    )
    feed = await ChangeService(session).feed(filters, cursor=_cursor(cursor))
    return _changes_response(feed, size)


@router.get(
    "/summary",
    response_model=SummaryResponse,
    summary="How much changed, and how much a filter is hiding",
    description=(
        "Counts over the whole window, taken before the filter is applied. The one thing a "
        "client cannot compute from a page: whether the page it is showing is the whole "
        "story."
    ),
)
async def summarize_changes(
    session: Session,
    window_from: FromQuery,
    window_to: ToQuery,
    server: ScopeQuery = None,
    share: ScopeQuery = None,
    directory: ScopeQuery = None,
    principal: ScopeQuery = None,
    group: ScopeQuery = None,
    kind: KindQuery = None,
    action: ActionQuery = None,
    significance: SignificanceQuery = None,
    min_severity: SeverityQuery = ChangeSeverity.INFO,
) -> SummaryResponse:
    filters = _filter(
        window_from,
        window_to,
        server=server,
        share=share,
        directory=directory,
        principal=principal,
        group=group,
        kind=kind,
        action=action,
        significance=significance,
        min_severity=min_severity,
        limit=DEFAULT_LIMIT,
    )
    return _summary_view(await ChangeService(session).summary(filters))


@router.get(
    "/timeline",
    response_model=TimelineResponse,
    summary="Every transition of one object",
    description=(
        "The object's own history, as changes rather than as versions. Its first version is "
        "reported only when it follows a tombstone; otherwise it is where the record begins."
    ),
)
async def object_timeline(
    session: Session,
    kind: Annotated[ObservationKind, Query(description="The object's kind.")],
    key: Annotated[
        str,
        Query(
            min_length=1,
            max_length=1024,
            pattern=PRINTABLE_IDENTIFIER,
            description=(
                "The object's storage key, exactly as a change reports it. Given as a query "
                "parameter rather than a path segment because a directory's key is a UNC "
                "path, which several proxies normalize or reject inside a path."
            ),
        ),
    ],
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> TimelineResponse:
    changes = await ChangeService(session).object_changes(kind, key, limit=limit)
    return _timeline_view(changes)


@router.get(
    "/compare",
    response_model=ComparisonResponse,
    summary="What is different between two instants",
    description=(
        "A different question from the feed, with a different answer: an entry added and "
        "removed inside the interval appears in the feed twice and here not at all. Objects "
        "not covered by a version at one of the instants are counted, never reported as "
        "additions or removals."
    ),
)
async def compare_instants(
    session: Session,
    at_from: Annotated[dt.datetime, Query(alias="from", description="The earlier instant.")],
    at_to: Annotated[dt.datetime, Query(alias="to", description="The later instant.")],
    server: ScopeQuery = None,
    share: ScopeQuery = None,
    directory: ScopeQuery = None,
    principal: ScopeQuery = None,
    group: ScopeQuery = None,
    kind: KindQuery = None,
    significance: SignificanceQuery = None,
    min_severity: SeverityQuery = ChangeSeverity.INFO,
) -> ComparisonResponse:
    scope = _scope(
        server=server, share=share, directory=directory, principal=principal, group=group
    )
    comparison = await ChangeService(session).compare(
        _aware(at_from, "from"),
        _aware(at_to, "to"),
        scope=scope,
        kinds=frozenset(kind) if kind else None,
        significance=_significance(significance),
        min_severity=min_severity,
    )
    return _comparison_view(comparison)


@impact_router.get(
    "/impact",
    response_model=ImpactResponse,
    summary="Why access changed",
    description=(
        "Effective access resolved either side of one change by the ordinary engine over "
        "as-of inputs. Requires access:read rather than changes:read: this discloses what a "
        "principal could do, which is a more sensitive answer than what was edited."
    ),
)
async def change_impact(
    session: Session,
    limits: TraversalBounds,
    kind: Annotated[ObservationKind, Query(description="The changed object's kind.")],
    key: Annotated[
        str,
        Query(min_length=1, max_length=1024, pattern=PRINTABLE_IDENTIFIER),
    ],
    at: Annotated[
        dt.datetime,
        Query(
            description=(
                "The change's own 'at' value: the instant its resulting version opened. "
                "Hand back what the feed gave you."
            )
        ),
    ],
    subject: Annotated[
        str | None,
        Query(
            max_length=512,
            pattern=PRINTABLE_IDENTIFIER,
            description="Override the principal, or supply one the change does not name.",
        ),
    ] = None,
    resource: Annotated[
        str | None,
        Query(
            max_length=1024,
            pattern=PRINTABLE_IDENTIFIER,
            description="Override the resource, or supply one the change does not name.",
        ),
    ] = None,
    access_path: Annotated[
        AccessPath,
        Query(description="remote_smb applies both layers; local applies only NTFS."),
    ] = AccessPath.REMOTE_SMB,
) -> ImpactResponse:
    impact = await ChangeImpactService(session).impact_of(
        kind,
        key,
        _aware(at, "at"),
        subject_key=subject,
        resource_key=resource,
        path=access_path,
        limits=limits,
    )
    return _impact_view(impact)


# ------------------------------------------------------------------- assembling


def _aware(moment: dt.datetime, field: str) -> dt.datetime:
    """Refuse a naive instant rather than assuming a time zone for it.

    Assuming UTC would answer about a different moment than the caller meant, silently, for
    every caller in a time zone that is not UTC — which is most of them.
    """
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise DomainValidationError(
            f"{field!r} must carry a UTC offset, for example 2026-03-06T09:00:00Z. A naive "
            "instant cannot be ordered against observations from a host in another time "
            "zone, and would answer about a different moment than you meant.",
            field=field,
        )
    return moment.astimezone(dt.UTC)


def _scope(
    *,
    server: str | None,
    share: str | None,
    directory: str | None,
    principal: str | None,
    group: str | None,
) -> ChangeScope | None:
    """At most one scope, refused rather than silently ranked when several are given.

    Two scopes have no defensible combination: ``server=fs01&group=Domain Admins`` could
    mean either intersection or union, and whichever one the server picked would be right
    for somebody and wrong silently for everybody else.
    """
    given = {
        ScopeTarget.SERVER: server,
        ScopeTarget.SHARE: share,
        ScopeTarget.DIRECTORY_TREE: directory,
        ScopeTarget.PRINCIPAL: principal,
        ScopeTarget.GROUP: group,
    }
    supplied = [(target, value) for target, value in given.items() if value]
    if not supplied:
        return None
    if len(supplied) > 1:
        raise DomainValidationError(
            "Give at most one of server, share, directory, principal or group. Two scopes "
            "at once could mean their intersection or their union, and either reading would "
            "be wrong for somebody without saying so.",
            field="scope",
        )
    target, value = supplied[0]
    return ChangeScope(target=target, key=value)


def _significance(values: Sequence[ChangeSignificance] | None) -> frozenset[ChangeSignificance]:
    from app.changes import DEFAULT_SIGNIFICANCE

    return frozenset(values) if values else DEFAULT_SIGNIFICANCE


def _filter(
    window_from: dt.datetime,
    window_to: dt.datetime,
    *,
    server: str | None,
    share: str | None,
    directory: str | None,
    principal: str | None,
    group: str | None,
    kind: Sequence[ObservationKind] | None,
    action: Sequence[ChangeAction] | None,
    significance: Sequence[ChangeSignificance] | None,
    min_severity: ChangeSeverity,
    limit: int,
) -> ChangeFilter:
    from app.changes import DEFAULT_ACTIONS

    return ChangeFilter(
        window_from=_aware(window_from, "from"),
        window_to=_aware(window_to, "to"),
        scope=_scope(
            server=server, share=share, directory=directory, principal=principal, group=group
        ),
        kinds=frozenset(kind) if kind else None,
        actions=frozenset(action) if action else DEFAULT_ACTIONS,
        significance=_significance(significance),
        min_severity=min_severity,
        limit=limit,
    )


def _cursor(raw: str | None) -> VersionCursor | None:
    if raw is None:
        return None
    try:
        decoded = decode_keyset_cursor(raw)
    except InvalidCursor as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    position = VersionCursor.decode(decoded or "")
    if position is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "This cursor does not carry a position in the change feed. Cursors are "
                "opaque: pass back the next_cursor value from a previous response."
            ),
        )
    return position


def _filters_view(filters: ChangeFilter) -> FiltersView:
    return FiltersView(
        window_from=filters.window_from,
        window_to=filters.window_to,
        scope_target=filters.scope.target.value if filters.scope else None,
        scope_key=filters.scope.key if filters.scope else None,
        kinds=sorted(item.value for item in filters.kinds) if filters.kinds else None,
        actions=sorted(item.value for item in filters.actions),
        significance=sorted(item.value for item in filters.significance),
        min_severity=filters.min_severity.value,
    )


def _window_view(window: ChangeWindow | None) -> ChangeWindowView | None:
    if window is None:
        return None
    return ChangeWindowView(
        after=window.after,
        at_or_before=window.at_or_before,
        is_exact=window.is_exact,
        duration_seconds=window.duration.total_seconds(),
    )


def _version_view(version: ObjectVersion | None) -> VersionView | None:
    if version is None:
        return None
    return VersionView(
        present=version.is_present,
        valid_from=version.valid_from,
        last_seen_at=version.last_seen_at,
        valid_to=version.valid_to,
        origin=version.origin.value,
        opened_by_run_id=str(version.opened_by_run_id),
        last_seen_run_id=str(version.last_seen_run_id),
        state=dict(version.state) if version.state is not None else None,
    )


def _change_view(change: ObjectChange, correlation: Correlation) -> ChangeView:
    after = _version_view(change.after)
    assert after is not None
    return ChangeView(
        kind=change.kind.value,
        key=change.key,
        action=change.action.value,
        significance=change.significance.value,
        direction=change.direction.value,
        severity=change.severity.value,
        reasons=list(change.reasons),
        rule_ids=list(change.rule_ids),
        at=change.at,
        window=_window_view(change.window),
        reconstructed=change.reconstructed,
        subject=SubjectView(
            container_kind=(
                change.subject.container_kind.value if change.subject.container_kind else None
            ),
            container_key=change.subject.container_key,
            related_kind=(
                change.subject.related_kind.value if change.subject.related_kind else None
            ),
            related_key=change.subject.related_key,
        ),
        before=_version_view(change.before),
        after=after,
        deltas=[
            FieldDeltaView(
                field=delta.field,
                before=delta.before,
                after=delta.after,
                significance=delta.significance.value,
            )
            for delta in change.significant_deltas()
        ],
        edit=correlation.edit_of.get(key_of(change)),
    )


def _edit_view(index: int, edit: AceEdit) -> AceEditView:
    return AceEditView(
        index=index,
        kind=edit.identity.kind.value,
        container_key=edit.identity.container_key,
        trustee_key=edit.identity.trustee_key,
        ace_type=edit.identity.ace_type,
        direction=edit.direction.value,
        severity=edit.severity.value,
        summary=edit.summary,
        rights_before=_rights(edit.rights_before),
        rights_after=_rights(edit.rights_after),
    )


def _rights(mask: RightsMask | None) -> RightsView | None:
    return render_rights(mask) if mask is not None else None


def _changes_response(feed: ChangeFeed, limit: int) -> ChangesResponse:
    return ChangesResponse(
        filters=_filters_view(feed.filters),
        changes=[_change_view(change, feed.correlation) for change in feed.changes],
        edits=[_edit_view(index, edit) for index, edit in enumerate(feed.correlation.edits)],
        page=PageInfo(
            limit=limit,
            has_more=feed.has_more,
            next_cursor=(
                encode_keyset_cursor(feed.next_cursor.encode())
                if feed.has_more and feed.next_cursor is not None
                else None
            ),
        ),
        scanned=feed.scanned,
        scan_exhausted=feed.scan_exhausted,
    )


def _summary_view(summary: ChangeSummary) -> SummaryResponse:
    return SummaryResponse(
        window_from=summary.window_from,
        window_to=summary.window_to,
        total=summary.total,
        returned=summary.returned,
        excluded=summary.excluded,
        highest_severity=summary.highest.value if summary.highest else None,
        reconstructed=summary.reconstructed,
        truncated=summary.truncated,
        by_action={key.value: value for key, value in summary.by_action.items()},
        by_significance={key.value: value for key, value in summary.by_significance.items()},
        by_severity={key.value: value for key, value in summary.by_severity.items()},
        by_kind={key.value: value for key, value in summary.by_kind.items()},
    )


def _timeline_view(changes: ObjectChanges) -> TimelineResponse:
    from app.changes import correlate

    correlation = correlate(changes.changes)
    return TimelineResponse(
        kind=changes.kind.value,
        key=changes.key,
        changes=[_change_view(change, correlation) for change in changes.changes],
        edits=[_edit_view(index, edit) for index, edit in enumerate(correlation.edits)],
        truncated=changes.truncated,
    )


def _comparison_view(comparison: ChangeComparison) -> ComparisonResponse:
    return ComparisonResponse(
        at_from=comparison.at_from,
        at_to=comparison.at_to,
        scope_target=comparison.scope.target.value if comparison.scope else None,
        scope_key=comparison.scope.key if comparison.scope else None,
        changes=[_change_view(change, comparison.correlation) for change in comparison.changes],
        edits=[_edit_view(index, edit) for index, edit in enumerate(comparison.correlation.edits)],
        unchanged=comparison.unchanged,
        unobserved_at_from=comparison.unobserved_at_from,
        unobserved_at_to=comparison.unobserved_at_to,
        truncated=comparison.truncated,
        summary=_summary_view(comparison.summary),
    )


def _access_side(at: dt.datetime, answer: Any) -> AccessSideView:
    from app.access_engine import classify_access

    effective = answer.access.access
    verdict = classify_access(effective)
    return AccessSideView(
        at=at,
        access=effective.has_access,
        outcome=verdict.outcome.value,
        conclusive=verdict.is_conclusive,
        rights=render_rights(effective.rights),
        certainty=answer.certainty.value,
        reason=verdict.reason,
    )


def _impact_view(impact: ChangeImpact) -> ImpactResponse:
    from app.changes import correlate

    correlation = correlate([impact.change])
    return ImpactResponse(
        change=_change_view(impact.change, correlation),
        at_before=impact.at_before,
        at_after=impact.at_after,
        verdict=impact.verdict.value,
        explanation=impact.explanation,
        access=_access_delta_view(impact),
        membership=_membership_view(impact.membership),
    )


def _access_delta_view(impact: ChangeImpact) -> AccessDeltaView | None:
    delta: AccessDelta | None = impact.access
    if delta is None:
        return None
    return AccessDeltaView(
        subject_key=delta.subject_key,
        resource_key=delta.resource_key,
        access_path=delta.path.value,
        before=_access_side(impact.at_before, delta.before),
        after=_access_side(impact.at_after, delta.after),
        gained=render_rights(delta.gained),
        lost=render_rights(delta.lost),
        direction=delta.direction.value,
        certainty=delta.certainty.value,
        conclusive=delta.conclusive,
    )


def _membership_view(delta: MembershipDelta | None) -> MembershipDeltaView | None:
    if delta is None:
        return None
    return MembershipDeltaView(
        subject_key=delta.subject_key,
        gained=list(delta.gained),
        lost=list(delta.lost),
        before_count=len(delta.before),
        after_count=len(delta.after),
        certainty=delta.certainty.value,
    )
