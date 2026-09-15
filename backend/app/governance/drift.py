r"""Baseline drift: what has happened to a reviewed grant since the campaign froze it.

A campaign is frozen against an instant, which is what makes an attestation mean anything
(ADR-0030). The cost of that freeze is that the estate keeps moving, and a reviewer looking
at an item three weeks later may be certifying something that no longer exists in the form
they are being shown. This module answers "is it still like that?" — and answers it
**beside** the item, never by editing it.

**Nothing here rewrites an item.** The frozen evidence is what the decision is about and
what the audit trail records the digest of; silently refreshing it would make every
attestation a statement about whatever the grant became. Drift is therefore computed on
read, reported as its own object, and stored nowhere.

**The comparison is on content, not on version identity.** :func:`content_digest` covers the
same fields as :meth:`app.governance.model.GrantEvidence.as_digestible` minus ``version_id``.
The reason is a specific false positive: if an entry is removed and later restored to
exactly its old state, the timeline correctly holds two versions with different ids and
identical content, and a comparison on ``version_id`` would tell a reviewer their grant had
changed when the thing they are certifying is identical. The version movement is not thrown
away — :attr:`ItemDrift.evidence_reissued` reports it — but it does not make the verdict.

**"Gone" and "not looked at" are different answers and are never merged.** A target ADG
holds no version for at the comparison instant reports :attr:`DriftVerdict.UNOBSERVED`, not
``REMOVED``. Reporting access as withdrawn on the strength of nobody having scanned is the
same mistake :attr:`app.changes.service.ChangeComparison.unobserved_at_to` exists to refuse,
and it is worse here: a reviewer told "this was removed already" will close the item.

The module is pure — no session, no clock, no query. The current grants are read by
:mod:`app.governance.repository` and handed in, exactly as generation's are.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.governance.model import GrantEvidence, _canonical, evidence_digest
from app.history.model import Certainty

__all__ = [
    "DRIFT_FIELD_LABELS",
    "DriftVerdict",
    "GrantChange",
    "GrantChangeKind",
    "ItemDrift",
    "compare_grants",
    "content_digest",
]


class DriftVerdict(StrEnum):
    """What has become of a reviewed grant since its campaign's baseline."""

    UNCHANGED = "unchanged"
    """The same entries, granting the same rights. The item is still about what it says."""

    MODIFIED = "modified"
    """The grant still exists and is not the same: an entry was added, removed, or its
    rights, type, flags or ordering changed. The reviewer is shown both and decides on the
    frozen one — that is what they were asked about — but they are told."""

    REMOVED = "removed"
    """No entry names this principal on this target any more — a measured absence, because
    ADG holds a version covering the comparison instant and it carries no such entry.

    Often good news: it is what a carried-out revocation looks like from here. The wording
    must not assume that, though — ADG cannot tell a remediation from somebody deleting the
    wrong ACE, and naming a cause would be inventing one for an observation.

    Also the verdict when the **target itself** is gone, distinguished by
    :attr:`ItemDrift.target_present` rather than by a sixth verdict: the grant is absent
    either way, and a reviewer needs to know which before they read it as a fix."""

    UNOBSERVED = "unobserved"
    """ADG holds no version covering the comparison instant for this target, so it cannot
    say. Never reported as ``REMOVED``: "we looked and it is gone" and "nobody has looked"
    lead a reviewer to opposite conclusions, and only one of them is reversible."""

    @property
    def has_drifted(self) -> bool:
        """Whether the estate moved under this item. ``UNOBSERVED`` is not drift — it is the
        absence of an answer, and counting it as drift would inflate every report taken
        while a collector was down."""
        return self in {DriftVerdict.MODIFIED, DriftVerdict.REMOVED}


class GrantChangeKind(StrEnum):
    ADDED = "added"
    """An entry naming this principal on this target that the baseline did not carry."""

    REMOVED = "removed"
    """An entry the baseline carried and the current state does not."""

    CHANGED = "changed"
    """The same ``ace_key``, different content. :attr:`GrantChange.fields` says which."""


#: Human wording for each field :func:`content_digest` covers, used by the API and the UI so
#: that "access_mask" is not what a resource owner is asked to interpret. Keyed by the field
#: name in :meth:`GrantEvidence.as_digestible`.
DRIFT_FIELD_LABELS: Final[Mapping[str, str]] = {
    "ace_type": "allow or deny",
    "access_mask": "rights",
    "permission": "share permission level",
    "ace_flags": "inheritance flags",
    "source": "set here or inherited",
    "inherited_from": "inherited from",
    "order_index": "position in the list",
    "trustee_sid": "trustee SID",
    "trustee_key": "trustee",
    "target_kind": "access-control list",
    "ace_key": "entry",
}

#: Excluded from the content comparison. ``version_id`` identifies the timeline row rather
#: than the entry, and an entry removed and restored to its old state takes a new row while
#: being, to the reviewer, the same grant.
_NOT_CONTENT: Final[frozenset[str]] = frozenset({"version_id"})


def _content(grant: GrantEvidence) -> dict[str, Any]:
    return {key: value for key, value in grant.as_digestible().items() if key not in _NOT_CONTENT}


def content_digest(grants: Iterable[GrantEvidence]) -> str:
    """The digest of what a grant *is*, independent of which version rows carry it.

    Built the same way as :func:`app.governance.model.evidence_digest` — sorted by
    ``ace_key``, canonical JSON, SHA-256 — so the two are directly comparable in the one
    place it matters: equal content and unequal evidence means the entries were reissued.
    """
    rendered = sorted((_content(grant) for grant in grants), key=lambda item: item["ace_key"])
    return hashlib.sha256(_canonical(rendered).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GrantChange:
    """One difference between the frozen evidence and the current state."""

    kind: GrantChangeKind
    ace_key: str
    before: GrantEvidence | None
    after: GrantEvidence | None
    fields: tuple[str, ...] = ()
    """For :attr:`GrantChangeKind.CHANGED`, the field names that differ, sorted. Empty for
    an addition or a removal, where the whole entry is the difference."""

    @property
    def field_labels(self) -> tuple[str, ...]:
        return tuple(DRIFT_FIELD_LABELS.get(name, name) for name in self.fields)


@dataclass(frozen=True, slots=True)
class ItemDrift:
    """What became of one item's grant, as of one instant. Computed on read, stored nowhere."""

    verdict: DriftVerdict
    baseline_digest: str
    """The item's ``evidence_digest``, copied so a caller comparing two reports does not
    have to hold the item as well."""

    baseline_content_digest: str
    current_content_digest: str | None
    """``None`` when the verdict is :attr:`DriftVerdict.UNOBSERVED` — there is no current
    state to digest, and a digest of the empty set would read as "nothing is granted"."""

    current_grants: tuple[GrantEvidence, ...]
    changes: tuple[GrantChange, ...]
    current_certainty: Certainty | None
    """The weakest certainty among the current entries, or ``None`` when there are none.
    A ``MODIFIED`` verdict resting on backfilled state is a weaker claim than one resting on
    a scan an hour ago, and the difference decides whether a reviewer should act on it."""

    target_present: bool | None
    """Whether the target object — the share, the directory — was itself present at the
    comparison instant. ``False`` with a :attr:`DriftVerdict.REMOVED` verdict means the whole
    share or folder is gone, not that one grant was withdrawn; ``None`` means ADG cannot
    say, which is the :attr:`DriftVerdict.UNOBSERVED` case."""

    target_certainty: Certainty | None
    """How firmly the target's own state is known at the comparison instant.

    Load-bearing for an ``UNCHANGED`` verdict. An entry whose version is still open reads as
    unchanged forever if nobody looks again, so a campaign compared against a share last
    scanned in March would report every item unchanged — truthfully about the record, and
    misleadingly about the estate. ``INFERRED`` here says exactly that, and the summary says
    it in words."""

    evidence_reissued: bool = False
    """The content is identical and the version rows are not: the entries were removed and
    restored, or rewritten. Not drift — the reviewer is certifying the same thing — but it
    means the timeline holds a story the change feed can tell."""

    @property
    def has_drifted(self) -> bool:
        return self.verdict.has_drifted

    @property
    def added(self) -> tuple[GrantChange, ...]:
        return tuple(c for c in self.changes if c.kind is GrantChangeKind.ADDED)

    @property
    def removed(self) -> tuple[GrantChange, ...]:
        return tuple(c for c in self.changes if c.kind is GrantChangeKind.REMOVED)

    @property
    def changed(self) -> tuple[GrantChange, ...]:
        return tuple(c for c in self.changes if c.kind is GrantChangeKind.CHANGED)

    @property
    def summary(self) -> str:
        """One sentence, written for the person deciding the item.

        Prose rather than three counts because the counts are ambiguous in the direction
        that matters: "2 changes" does not distinguish a rights reduction somebody already
        made from two new people being added to the folder under review.
        """
        if self.verdict is DriftVerdict.UNOBSERVED:
            return (
                "ADG holds nothing covering this target now, so it cannot say whether this "
                "grant still stands. The evidence below is what was true at the baseline, "
                "which is what you are being asked about."
            )
        if self.verdict is DriftVerdict.REMOVED:
            if self.target_present is False:
                return (
                    "The target itself is gone: a scan since the baseline found no such "
                    "share or directory, so the grant went with it. That is not the same as "
                    "the permission having been withdrawn, and a decision here is about the "
                    "grant as it stood."
                )
            return (
                "This grant no longer exists: the target has been read since the baseline "
                "and no entry names this principal. ADG cannot tell whether somebody acted "
                "on a review or removed it for another reason. Your decision still records "
                "what should have been true of the grant as it stood."
            )
        stale = (
            ""
            if self.target_certainty in {None, Certainty.OBSERVED}
            else (
                " Note that no scan has confirmed this target recently, so this compares "
                "the baseline against ADG's last reading rather than against the estate."
            )
        )
        if self.verdict is DriftVerdict.UNCHANGED:
            if self.evidence_reissued:
                return (
                    "Unchanged. The entries grant exactly what they did at the baseline, "
                    "though the timeline holds new versions of them — they were removed and "
                    "restored, or rewritten identically, since the campaign was cut." + stale
                )
            return "Unchanged since the campaign was frozen." + stale
        parts: list[str] = []
        if self.added:
            parts.append(_count(len(self.added), "entry added", "entries added"))
        if self.removed:
            parts.append(_count(len(self.removed), "entry removed", "entries removed"))
        if self.changed:
            fields = sorted({label for change in self.changed for label in change.field_labels})
            parts.append(f"{len(self.changed)} changed ({', '.join(fields)})")
        return (
            "This grant has changed since the campaign was frozen: "
            + "; ".join(parts)
            + ". You are deciding on the frozen evidence, which is what you were asked "
            "about; the current state is shown beside it." + stale
        )


def compare_grants(
    baseline: Sequence[GrantEvidence],
    current: Sequence[GrantEvidence],
    *,
    target_present: bool | None,
    target_certainty: Certainty | None = None,
) -> ItemDrift:
    """Compare an item's frozen evidence with the same grant now.

    ``target_present`` is about the **target**, not about the entries: ``True`` when ADG
    holds a present version of the share or directory covering the comparison instant,
    ``False`` when it holds a tombstone, ``None`` when it holds nothing at all. The three
    cases produce three different answers, and collapsing them into "the current list is
    empty" is the mistake this parameter exists to make impossible — a folder nobody has
    scanned, a folder that was deleted, and a folder whose ACL no longer names this
    principal look identical from the entry list alone and mean completely different things.
    """
    baseline_evidence = evidence_digest(baseline)
    baseline_content = content_digest(baseline)

    if target_present is None:
        return ItemDrift(
            verdict=DriftVerdict.UNOBSERVED,
            baseline_digest=baseline_evidence,
            baseline_content_digest=baseline_content,
            current_content_digest=None,
            current_grants=(),
            changes=(),
            current_certainty=None,
            target_present=None,
            target_certainty=target_certainty,
        )

    current_sorted = tuple(sorted(current, key=lambda grant: grant.ace_key))
    current_content = content_digest(current_sorted)
    certainty = _weakest(current_sorted)

    if not current_sorted:
        return ItemDrift(
            verdict=DriftVerdict.REMOVED,
            baseline_digest=baseline_evidence,
            baseline_content_digest=baseline_content,
            current_content_digest=current_content,
            current_grants=(),
            changes=tuple(
                GrantChange(
                    kind=GrantChangeKind.REMOVED, ace_key=grant.ace_key, before=grant, after=None
                )
                for grant in sorted(baseline, key=lambda grant: grant.ace_key)
            ),
            current_certainty=certainty,
            target_present=target_present,
            target_certainty=target_certainty,
        )

    changes = _changes(baseline, current_sorted)
    if current_content == baseline_content:
        return ItemDrift(
            verdict=DriftVerdict.UNCHANGED,
            baseline_digest=baseline_evidence,
            baseline_content_digest=baseline_content,
            current_content_digest=current_content,
            current_grants=current_sorted,
            changes=(),
            current_certainty=certainty,
            target_present=target_present,
            target_certainty=target_certainty,
            evidence_reissued=evidence_digest(current_sorted) != baseline_evidence,
        )
    return ItemDrift(
        verdict=DriftVerdict.MODIFIED,
        baseline_digest=baseline_evidence,
        baseline_content_digest=baseline_content,
        current_content_digest=current_content,
        current_grants=current_sorted,
        changes=changes,
        current_certainty=certainty,
        target_present=target_present,
        target_certainty=target_certainty,
    )


def _count(value: int, singular: str, plural: str) -> str:
    return f"{value} {singular if value == 1 else plural}"


def _changes(
    baseline: Sequence[GrantEvidence], current: Sequence[GrantEvidence]
) -> tuple[GrantChange, ...]:
    """Every difference, ordered by entry.

    Pairs on ``ace_key`` first, then — because an ACE key is **content-addressed** — makes a
    second pass described in :func:`_pair_rewritten`.
    """
    before = {grant.ace_key: grant for grant in baseline}
    after = {grant.ace_key: grant for grant in current}
    found: list[GrantChange] = []
    orphaned_removals: list[GrantEvidence] = []
    orphaned_additions: list[GrantEvidence] = []

    for key in sorted(before.keys() | after.keys()):
        old = before.get(key)
        new = after.get(key)
        if old is not None and new is None:
            orphaned_removals.append(old)
        elif old is None and new is not None:
            orphaned_additions.append(new)
        elif old is not None and new is not None:
            differing = _differing_fields(old, new)
            if differing:
                found.append(
                    GrantChange(
                        kind=GrantChangeKind.CHANGED,
                        ace_key=key,
                        before=old,
                        after=new,
                        fields=differing,
                    )
                )

    found.extend(_pair_rewritten(orphaned_removals, orphaned_additions))
    return tuple(sorted(found, key=lambda change: change.ace_key))


def _pair_rewritten(
    removals: Sequence[GrantEvidence], additions: Sequence[GrantEvidence]
) -> list[GrantChange]:
    r"""Recognize an entry that was rewritten rather than removed and replaced.

    ``ace_key`` is content-addressed — ``keys.ntfs_ace_key`` digests the path, the trustee,
    the type, the **mask** and the **flags** — so widening Alice from Read to Modify does not
    edit an entry, it tombstones one key and opens another. Paired on the key alone, the most
    common thing that ever happens to a grant reports as "1 entry removed; 1 entry added",
    and the reviewer has to notice for themselves that the two are the same row.

    So: a removal and an addition sharing ``(trustee, allow-or-deny)`` are reported as one
    ``CHANGED`` naming the fields that moved — but **only when the pairing is unambiguous**,
    meaning exactly one of each for that pair. Two allows removed and two added for one
    trustee could be matched two ways, and guessing which entry became which would put a
    claim in front of a reviewer that ADG cannot support; those stay as separate additions
    and removals, which is the honest shape of "several entries were rewritten".
    """
    by_shape: dict[tuple[str, str], tuple[list[GrantEvidence], list[GrantEvidence]]] = {}
    for grant in removals:
        by_shape.setdefault((grant.trustee_key, grant.ace_type.value), ([], []))[0].append(grant)
    for grant in additions:
        by_shape.setdefault((grant.trustee_key, grant.ace_type.value), ([], []))[1].append(grant)

    found: list[GrantChange] = []
    for gone, arrived in by_shape.values():
        if len(gone) == 1 and len(arrived) == 1:
            old, new = gone[0], arrived[0]
            found.append(
                GrantChange(
                    kind=GrantChangeKind.CHANGED,
                    # The **current** key, so that a client following the entry forward finds
                    # the row that exists now rather than the tombstone.
                    ace_key=new.ace_key,
                    before=old,
                    after=new,
                    fields=_differing_fields(old, new),
                )
            )
            continue
        found.extend(
            GrantChange(
                kind=GrantChangeKind.REMOVED, ace_key=grant.ace_key, before=grant, after=None
            )
            for grant in gone
        )
        found.extend(
            GrantChange(kind=GrantChangeKind.ADDED, ace_key=grant.ace_key, before=None, after=grant)
            for grant in arrived
        )
    return found


def _differing_fields(before: GrantEvidence, after: GrantEvidence) -> tuple[str, ...]:
    """Which fields moved, excluding the one that is a function of the others.

    ``ace_key`` is a digest of the path, trustee, type, mask and flags, so it differs exactly
    when one of those does and reporting it would add "entry" beside "rights" on every
    rewritten entry — a second name for a change already named.
    """
    old = _content(before)
    new = _content(after)
    return tuple(
        sorted(name for name, value in old.items() if name != "ace_key" and new.get(name) != value)
    )


def _weakest(grants: Sequence[GrantEvidence]) -> Certainty | None:
    """The weakest certainty among the current entries.

    The same rule :func:`app.governance.model.weakest_certainty` applies to an item, written
    here rather than reused because it must return ``None`` for an empty set: that function
    reports :attr:`Certainty.OBSERVED` for no grants, which is right for an item (an item
    always has at least one) and would be a lie here (a removal observed nowhere).
    """
    if not grants:
        return None
    order = (Certainty.UNOBSERVED, Certainty.BACKFILLED, Certainty.INFERRED, Certainty.OBSERVED)
    return min((grant.certainty for grant in grants), key=order.index)
