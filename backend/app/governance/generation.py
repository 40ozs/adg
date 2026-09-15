r"""Turning a baseline instant into a set of review items. Pure, and therefore reproducible.

This module holds the whole of the campaign-generation policy and none of the queries. It
is handed the grants that were in scope at the baseline and returns the items a reviewer
will answer, plus a tally of everything it deliberately left out.

**Reproducibility is the property this file exists to have.** An item set is a pure function
of ``(focus, options, grants)``, the grants are a pure function of ``(scopes, baseline_at)``
over ``object_versions``, and ``object_versions`` is append-only. So regenerating a campaign
months later yields the same items, in the same order, with the same digests — which is what
``GET /campaigns/{id}/verification`` recomputes. Any divergence is therefore a real finding
about the timeline, not a rendering difference, and that is only true because nothing here
reads a clock, a session, or the current-state tables.

**Items are grants, not effective access.** An item describes one ``(principal, target)``
pair and carries every access-control entry that creates it. That is deliberate: an entry is
what an administrator can actually remove, so a revoke decision maps onto a change somebody
can make. The full effective answer for the same pair — inherited rights, nested group
membership, deny ordering — is still available, computed by the ordinary engine over the
same baseline instant (:meth:`app.history.service.HistoryService.effective_access_at`), and
it is *also* frozen, because it is a pure function of the same versions. Nothing is lost by
not copying it into the item; what is gained is that the item set stays an enumeration of
removable things.

**Every exclusion is counted and reported.** A campaign that skipped inherited entries and
built-in trustees reviewed less than the estate holds, and "47 of 47 certified" must not be
readable as coverage of everything. :class:`GenerationResult` carries the tally and the
service stores it on the campaign row.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.domain import AceSource, AceType, Sid
from app.governance.model import (
    CampaignFocus,
    GenerationOptions,
    GrantEvidence,
    ReviewTargetKind,
    evidence_digest,
    item_natural_key,
    snapshot_digest,
    weakest_certainty,
)
from app.history.model import Certainty

__all__ = [
    "MAX_CAMPAIGN_ITEMS",
    "MAX_GRANTS_PER_ITEM",
    "CampaignTooLarge",
    "DraftItem",
    "ExclusionReason",
    "GenerationResult",
    "ObservedGrant",
    "generate_items",
]

MAX_CAMPAIGN_ITEMS: Final = 5_000
"""Items one campaign may contain. Exceeded, generation is **refused** rather than
truncated: a campaign that silently dropped the grants past a limit would report complete
coverage of a scope it never showed anybody, which is the exact failure an access review is
supposed to rule out. The message names the count and tells the operator to narrow the
scope."""

MAX_GRANTS_PER_ITEM: Final = 64
"""Access-control entries one ``(principal, target)`` pair may carry before the item is
reported as unusually complex. Not a refusal — the entries are all real and all shown — but
a pair with sixty-four entries is a descriptor somebody should look at for its own sake."""


class ExclusionReason:
    """Why a grant present at the baseline produced no item.

    Plain string constants rather than an enum: they are dictionary keys in a tally stored
    as JSONB and rendered in an API response, and an enum here would buy nothing a reader of
    ``{"inherited": 412}`` does not already have.
    """

    INHERITED: Final = "inherited"
    BUILTIN_TRUSTEE: Final = "builtin_trustee"
    DENY: Final = "deny"


@dataclass(frozen=True, slots=True)
class ObservedGrant:
    """One access-control entry that was in scope at the baseline, with its target.

    Built by :mod:`app.governance.repository` from a version of ``object_versions``. The
    separation matters: this dataclass is the entire interface between the queries and the
    policy, so the policy can be exercised exhaustively without a database and the queries
    have nothing to decide.
    """

    target_kind: ReviewTargetKind
    target_key: str
    target_path: str | None
    evidence: GrantEvidence

    @property
    def principal_key(self) -> str:
        return self.evidence.trustee_key

    @property
    def principal_sid(self) -> str:
        return self.evidence.trustee_sid


@dataclass(frozen=True, slots=True)
class DraftItem:
    """An item as generation produced it, before it has an id or a row."""

    focus: CampaignFocus
    target_kind: ReviewTargetKind
    target_key: str
    target_path: str | None
    principal_key: str
    principal_sid: str
    principal_display_name: str | None
    grants: tuple[GrantEvidence, ...]
    digest: str
    certainty: Certainty

    @property
    def natural_key(self) -> tuple[str, str, str]:
        return item_natural_key(self.target_kind, self.target_key, self.principal_key)

    @property
    def is_complex(self) -> bool:
        return len(self.grants) > MAX_GRANTS_PER_ITEM


class CampaignTooLarge(Exception):
    """Generation would produce more items than one campaign may hold.

    Its own exception rather than a domain validation error because the caller does
    something different with it: the message has to name the count, the ceiling, and the
    scope that produced it, and the API answers 409 rather than 422 — the request was
    well-formed, the estate is simply bigger than a single review.
    """

    def __init__(self, produced: int, ceiling: int) -> None:
        self.produced = produced
        self.ceiling = ceiling
        super().__init__(
            f"This scope produces {produced:,} review items and a campaign may hold "
            f"{ceiling:,}. Generation was refused rather than truncated, because a campaign "
            "that quietly dropped the rest would report complete coverage of a scope it "
            "never showed a reviewer. Narrow the scope — one share or one directory tree at "
            "a time — or exclude inherited entries and built-in trustees."
        )


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Everything one generation produced, including what it left out."""

    items: tuple[DraftItem, ...]
    excluded: Mapping[str, int]
    """Grants that were present at the baseline and produced no item, by reason. Stored on
    the campaign and reported with its status, so coverage is never read as wider than it
    was."""

    digest: str
    """The digest of the whole item set: what ``snapshot_digest`` on the campaign holds."""

    @property
    def excluded_total(self) -> int:
        return sum(self.excluded.values())

    @property
    def complex_items(self) -> tuple[DraftItem, ...]:
        return tuple(item for item in self.items if item.is_complex)


def generate_items(
    *,
    focus: CampaignFocus,
    options: GenerationOptions,
    grants: Sequence[ObservedGrant],
    principal_names: Mapping[str, str | None] | None = None,
    ceiling: int = MAX_CAMPAIGN_ITEMS,
) -> GenerationResult:
    """Fold the baseline's grants into review items.

    ``principal_names`` maps a principal key to the display name that principal had **at the
    baseline**, where one was known. A missing entry means the SID was named on an ACL and
    nothing had described it *then* — which is an orphaned-SID finding in its own right, and
    the item says so by carrying a null name rather than inventing one from the current
    tables.
    """
    names = principal_names or {}
    excluded: dict[str, int] = {}
    grouped: dict[tuple[str, str, str], list[ObservedGrant]] = {}

    for grant in grants:
        reason = _exclusion_reason(grant, options)
        if reason is not None:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        grouped.setdefault(
            item_natural_key(grant.target_kind, grant.target_key, grant.principal_key), []
        ).append(grant)

    if len(grouped) > ceiling:
        raise CampaignTooLarge(len(grouped), ceiling)

    items = tuple(
        _build(focus, key, members, names)
        # Sorted by the natural key so two generations return items in the same order. The
        # order is part of what a reader compares between an export and a re-run, and
        # "the same set in a different order" is a needless difference to have to explain.
        for key, members in sorted(grouped.items())
    )
    return GenerationResult(
        items=items,
        excluded=dict(sorted(excluded.items())),
        digest=snapshot_digest((item.natural_key, item.digest) for item in items),
    )


def _exclusion_reason(grant: ObservedGrant, options: GenerationOptions) -> str | None:
    """Why this grant produces no item, or ``None`` when it produces one.

    Checked in a fixed order so the tally is deterministic: a grant excluded for two reasons
    at once is counted under the first, and which one that is must not depend on dictionary
    iteration.
    """
    evidence = grant.evidence
    if not options.include_inherited and evidence.source is AceSource.INHERITED:
        return ExclusionReason.INHERITED
    if not options.include_builtin and _is_well_known(evidence.trustee_sid):
        return ExclusionReason.BUILTIN_TRUSTEE
    if not options.include_deny and evidence.ace_type is AceType.DENY:
        return ExclusionReason.DENY
    return None


def _is_well_known(sid: str) -> bool:
    """Whether a trustee is one of the SIDs that mean the same thing on every machine.

    A SID that will not parse is **not** treated as well known. An unparsable trustee is a
    finding, and excluding it as boilerplate would hide exactly the row worth looking at.
    """
    try:
        return Sid(sid).is_well_known
    except Exception:
        return False


def _build(
    focus: CampaignFocus,
    key: tuple[str, str, str],
    members: Sequence[ObservedGrant],
    names: Mapping[str, str | None],
) -> DraftItem:
    target_kind = ReviewTargetKind(key[0])
    first = members[0]
    # Sorted by ace_key, matching what the evidence digest is taken over, so the stored
    # order and the digested order are the same and a reader comparing the two by eye is
    # not comparing two different arrangements of the same set.
    evidence = tuple(sorted((grant.evidence for grant in members), key=lambda item: item.ace_key))
    # A target may be described by several grants; any path among them is the same path,
    # but prefer a non-null one so a row whose version happened not to carry the display
    # spelling does not blank the whole item.
    path = next((grant.target_path for grant in members if grant.target_path), first.target_path)
    return DraftItem(
        focus=focus,
        target_kind=target_kind,
        target_key=key[1],
        target_path=path,
        principal_key=key[2],
        principal_sid=first.principal_sid,
        principal_display_name=names.get(key[2]),
        grants=evidence,
        digest=evidence_digest(evidence),
        certainty=weakest_certainty(evidence),
    )
