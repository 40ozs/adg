r"""Two versions in, one classified change out.

Pure, and pure on purpose: every judgment ADG publishes about a change is a function of the
two versions plus the two tables beside this module, so the same pair always produces the
same severity. A classification that could vary with what else had been collected by the
time somebody asked could not be quoted in a finding, and quoting it in findings is the
entire point.

## Deciding the action needs something the object cannot know

Three of the four actions come straight off the two versions. The fourth does not: an
object's **first** version is either a creation or the moment ADG started looking, and the
object is identical in both cases. What separates them is the container —

    an ACE that appears on a directory ADG has been reading for months was added;
    the same ACE on a directory nobody had read before is simply the first reading.

so :func:`action_for` takes ``container_observed_before`` and refuses to guess when it is
``None``. The caller answers it with one batched lookup per page
(:mod:`app.changes.repository`), and when it cannot, the honest answer is
:attr:`ChangeAction.FIRST_OBSERVED` — which is excluded from the default feed rather than
scored, because an estate's first scan produces one per object and a page of them is not a
change report.

## A revival is an addition, and it is the one that is certain

``before`` being a tombstone means a reconciled scan looked and did not find the object, and
a later scan did. That is the only kind of creation ADG can prove, and it is reported as
:attr:`ChangeAction.ADDED` with no container question asked.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any

from app.changes.fields import significance_of
from app.changes.model import (
    ChangeAction,
    ChangeSignificance,
    FieldDelta,
    FieldSignificance,
    ObjectChange,
    SubjectRef,
)
from app.changes.rules import RULES, ChangeFacts, Rule, evaluate
from app.contracts.v1.common import ObservationKind
from app.history.model import ChangeWindow, ObjectVersion

__all__ = [
    "action_for",
    "classify",
    "deltas_between",
    "significance_for",
    "window_between",
]


def deltas_between(
    kind: ObservationKind,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> tuple[FieldDelta, ...]:
    """Every field that differs between two stored states, classified.

    Taken over the **union** of the two key sets, so a column that was absent from one side
    is a delta against ``None`` rather than being skipped. A field that appears for the
    first time because the collector started sending it is a real difference in what ADG
    knows, and silently dropping it would make a change list disagree with the two states
    printed beside it.

    Values are compared with ``==`` on the JSON forms both sides are stored in. There is no
    normalization step here, deliberately: :func:`app.history.model.stored_state` already
    normalized them on the way in, and a second normalization on the way out would be a
    second definition of equality — after which "did this change?" would have two answers.
    """
    left: Mapping[str, Any] = before or {}
    right: Mapping[str, Any] = after or {}
    deltas: list[FieldDelta] = []
    for name in sorted(set(left) | set(right)):
        old = left.get(name)
        new = right.get(name)
        if old == new:
            continue
        deltas.append(
            FieldDelta(
                field=name,
                before=old,
                after=new,
                significance=significance_of(kind, name) or FieldSignificance.UNCLASSIFIED,
            )
        )
    return tuple(deltas)


def action_for(
    before: ObjectVersion | None,
    after: ObjectVersion,
    *,
    container_observed_before: bool | None = None,
) -> ChangeAction:
    """Which of the four things happened.

    Args:
        before: the version that held immediately before, or ``None`` when ADG holds none.
        after: the version that holds from the transition onward. A removal is a tombstone,
            which is a version like any other -- so there is no case where a change has no
            resulting state, and a caller holding nothing for the later instant is holding
            a gap in observation rather than a change.
        container_observed_before: whether the object's container was already present in the
            record before this version opened. ``None`` means unanswerable, and unanswerable
            is reported rather than resolved either way.
    """
    if after.is_tombstone:
        return ChangeAction.REMOVED
    if before is None:
        return ChangeAction.ADDED if container_observed_before else ChangeAction.FIRST_OBSERVED
    if before.is_tombstone:
        # A reconciled scan proved it absent and a later one found it. The only creation
        # ADG can actually demonstrate.
        return ChangeAction.ADDED
    return ChangeAction.MODIFIED


def window_between(
    before: ObjectVersion | None,
    after: ObjectVersion,
    *,
    container_confirmed_at: dt.datetime | None = None,
) -> ChangeWindow | None:
    """When the transition happened, as the interval it is known to lie inside.

    ``(lower bound, after.valid_from]`` — ADR-0019, both ends whenever both are available.

    The lower bound is the newest instant the *previous* state was confirmed. It comes from
    two places, and the second one is what stops half of every ACL edit from reporting no
    window at all:

    * the object's own predecessor, when it has one;
    * failing that, ``container_confirmed_at``: the newest confirmation of a sibling inside
      the same container. An ACE's rights are part of its identity, so an entry that was
      *added* has no predecessor of its own — but the ACL it joined was read at a known
      instant, and the entry therefore appeared after it. Without this, every ACL addition
      would carry only ``at``, which is the instant somebody looked, and rendering that as
      the change time is precisely the model ADR-0019 rejected.

    ``None`` only when neither is available, because an invented lower bound reads exactly
    like a measured one.
    """
    lower = before.last_seen_at if before is not None else container_confirmed_at
    if lower is None:
        return None
    return ChangeWindow(
        after=lower,
        # A backward extension can pull a version's start below its predecessor's last
        # confirmation. Clamping keeps the interval from inverting; it never widens one.
        at_or_before=max(after.valid_from, lower),
    )


def significance_for(
    action: ChangeAction,
    deltas: tuple[FieldDelta, ...],
    *,
    ordering_material: bool | None,
) -> ChangeSignificance:
    """Whether this change is about access, about description, or about nothing.

    An object appearing or disappearing is always :attr:`ChangeSignificance.SECURITY`. All
    seven tracked kinds are either a grant, a holder of grants, or a thing grants attach to,
    so there is no line to draw between the kinds whose existence matters and the kinds
    whose existence does not — and any line drawn there would be somebody's judgment rather
    than a property of the data.

    For a modification the fields decide, strongest first. ``order_index`` is the one field
    whose answer is not in the table: it is security when the normalized ACL moved with it,
    noise when the ACL is byte-identical either side, and undetermined when the ACL could
    not be reconstructed — never noise by default, because "we could not check" and "we
    checked and it was nothing" are the two answers this product exists to keep apart.
    """
    if action is not ChangeAction.MODIFIED:
        return ChangeSignificance.SECURITY
    kinds = {delta.significance for delta in deltas}
    if FieldSignificance.UNCLASSIFIED in kinds or FieldSignificance.IDENTITY in kinds:
        return ChangeSignificance.UNDETERMINED
    if FieldSignificance.SECURITY in kinds:
        return ChangeSignificance.SECURITY
    if FieldSignificance.ORDER in kinds:
        if ordering_material is None:
            return ChangeSignificance.UNDETERMINED
        return ChangeSignificance.SECURITY if ordering_material else ChangeSignificance.NOISE
    if FieldSignificance.METADATA in kinds or FieldSignificance.DERIVED in kinds:
        return ChangeSignificance.METADATA
    return ChangeSignificance.NOISE


def classify(
    kind: ObservationKind,
    key: str,
    before: ObjectVersion | None,
    after: ObjectVersion,
    *,
    container_observed_before: bool | None = None,
    container_confirmed_at: dt.datetime | None = None,
    ordering_material: bool | None = None,
    sibling_ace_changes: int | None = None,
    subject: SubjectRef | None = None,
    rules: tuple[Rule, ...] = RULES,
) -> ObjectChange:
    """One classified change from two versions.

    ``ordering_material`` and ``sibling_ace_changes`` are the two facts a single object
    cannot supply about itself; :mod:`app.changes.correlation` computes them once per
    container and passes them in. Both default to ``None``, and ``None`` is carried through
    to the rules as "unknown" rather than as "no".
    """
    action = action_for(before, after, container_observed_before=container_observed_before)
    deltas = (
        deltas_between(kind, before.state if before is not None else None, after.state)
        if action is ChangeAction.MODIFIED
        else ()
    )
    facts = ChangeFacts(
        kind=kind,
        key=key,
        action=action,
        before=before.state if before is not None else None,
        after=after.state,
        deltas=deltas,
        container_key=_first_key(after, before, "container_key"),
        related_key=_first_key(after, before, "related_key"),
        ordering_material=ordering_material,
        sibling_ace_changes=sibling_ace_changes,
    )
    outcome = evaluate(facts, rules)
    # A first observation never carries a window, whatever a sibling confirmation says.
    #
    # The two container facts answer different questions and can disagree. ``action_for``
    # asks whether the *container object* was in the record before this version opened;
    # ``window_between`` falls back to the newest confirmation of a *sibling* inside that
    # container. A scan stamps its observations at different instants, so a group's member
    # edge can open at 08:01 while the group principal is first seen at 08:03 and another
    # edge was confirmed at 08:00 — no container observed before (so: first observation) and
    # a sibling bound available (so: a window). ObjectChange rejects that pair, which turned
    # an internal disagreement into a 422 on an ordinary estate; the release audit hit it on
    # the first comparison it ran.
    #
    # The action is what decides, because it is the one that says whether anything was
    # watching. If nothing was, there is no earlier confirmation of *this object's* absence
    # to bound it, and a sibling's reading is not one — it would be the invented lower bound
    # ADR-0019 exists to refuse, and it would read exactly like a measured one.
    window = (
        None
        if action is ChangeAction.FIRST_OBSERVED
        else window_between(before, after, container_confirmed_at=container_confirmed_at)
    )
    return ObjectChange(
        kind=kind,
        key=key,
        action=action,
        window=window,
        before=before,
        after=after,
        deltas=deltas,
        significance=significance_for(action, deltas, ordering_material=ordering_material),
        direction=outcome.direction,
        severity=outcome.severity,
        subject=subject or _subject_of(kind, facts.container_key, facts.related_key),
        reasons=(outcome.reason,),
        rule_ids=(outcome.rule_id,),
    )


def _first_key(
    preferred: ObjectVersion, fallback: ObjectVersion | None, attribute: str
) -> str | None:
    """A version's indexed key, taking the newer version's when both exist.

    The newer one, because a change's subject is where the object is *now*: a tombstone
    carries the same container and related keys its predecessor did (the writer copies them
    forward), so the two agree, and where they cannot, the state that holds is the one to
    name.
    """
    for version in (preferred, fallback):
        if version is not None:
            value = getattr(version, attribute)
            if value:
                return str(value)
    return None


def _subject_of(
    kind: ObservationKind, container_key: str | None, related_key: str | None
) -> SubjectRef:
    from app.changes.fields import container_kind_of, related_kind_of

    return SubjectRef(
        container_kind=container_kind_of(kind) if container_key else None,
        container_key=container_key,
        related_kind=related_kind_of(kind) if related_key else None,
        related_key=related_key,
    )
