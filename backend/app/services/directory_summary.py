r"""Directory access, summarized: where a principal's reach begins, runs, and changes.

The flat answer to "which directories can this principal reach?" is one row per directory,
and on a real share that is hundreds of rows that say the same thing. This module turns the
same answers into the few places an administrator would point at:

* an **access root** is a reachable directory whose parent is not reachable — a share root,
  a folder below one the principal cannot open, or a folder whose parent ADG never read;
* its **reachable descendants** are the directories reached from it through an unbroken run
  of reachable parents;
* an **exception** is a place inside that run where the answer changes: the effective
  rights widen or narrow, the certainty changes, inheritance is broken, or access stops.

**Nothing here decides access.** Every verdict this module reads was produced by the access
engine (:func:`app.access_engine.resolve_access`, :func:`app.access_engine.classify_access`)
for that directory, and the module only compares those verdicts along the tree. A directory
the engine was not asked about is one whose ACL names none of the principal's token SIDs
(the candidate rule of ADR-0012), and it is reported as exactly that — ``not named`` — rather
than as a verdict nobody computed.

**A summary row is not a claim of uniformity.** "Budgets — Modify — 184 descendants" says the
engine found 184 reachable directories below Budgets without access being interrupted; it
does not say they all hold Modify. Where they do not, the differences are exceptions, they
are counted on the row, and :attr:`AccessRoot.rights_breakdown` says how the rights are
spread. A root with no exception is the only one that may be read as uniform.

**Inaccessible directories are never counted as reachable**, and access that resumes below
an inaccessible folder starts a root of its own rather than joining the one above it: the
folder in between is a boundary the reader has to see.

Pure: no I/O, deterministic for a given input, and cheap enough to run over every directory
of every share a principal touches (one pass to group, one pass up each reachable chain).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.access_engine import AccessCertainty, AccessOutcome, LimitingLayer, RightsMask

__all__ = [
    "AccessException",
    "AccessRoot",
    "DirectoryAccessSummary",
    "DirectoryFacts",
    "DirectoryVerdict",
    "ExceptionReason",
    "RootParent",
    "TreeNode",
    "rights_change",
    "summarize_directory_access",
]


class ExceptionReason(StrEnum):
    """Why one directory inside a reachable run is called out rather than summarized."""

    RIGHTS_WIDEN = "rights_widen"
    """Reachable, holding every right its parent holds and more."""

    RIGHTS_NARROW = "rights_narrow"
    """Reachable, holding a strict subset of its parent's rights."""

    RIGHTS_DIFFER = "rights_differ"
    """Reachable, holding rights its parent lacks and lacking some it holds."""

    CERTAINTY_CHANGE = "certainty_change"
    """Reachable with the same rights, but the engine is sure of them in a different way."""

    INHERITANCE_BROKEN = "inheritance_broken"
    """The directory's DACL is protected: it refuses what its parent hands down."""

    ACCESS_DENIED = "access_denied"
    """Not reachable, and the engine's verdict is that a Deny entry is why."""

    ACCESS_ENDS = "access_ends"
    """Not reachable: nothing grants this principal anything here."""

    ACCESS_UNKNOWN = "access_unknown"
    """Not reachable as far as the evidence goes, and the evidence is incomplete in a
    direction that could hide a grant (``indeterminate``). Not a finding of no access."""

    OWNER_ONLY = "owner_only"
    """Nothing on the ACL names the principal or its groups, yet the engine grants rights:
    the principal owns the folder (Windows gives an owner ``READ_CONTROL`` and ``WRITE_DAC``
    whatever the DACL says, or an ``OWNER RIGHTS`` entry applies). Reported, not counted as
    reachable: the candidate index the listing pages through does not index ownership."""

    NOT_OBSERVED = "not_observed"
    """No run read this directory, though something below it was read. Whether access
    continues through it is unknown, so the run stops here rather than claiming it does."""


#: Reasons that mean access continues through the directory.
CONTINUING_REASONS = frozenset(
    {
        ExceptionReason.RIGHTS_WIDEN,
        ExceptionReason.RIGHTS_NARROW,
        ExceptionReason.RIGHTS_DIFFER,
        ExceptionReason.CERTAINTY_CHANGE,
        ExceptionReason.INHERITANCE_BROKEN,
    }
)


class RootParent(StrEnum):
    """What lies directly above an access root — why the run starts there."""

    SHARE_ROOT = "share_root"
    """The root is the directory a share publishes; there is nothing above it in the share."""

    NO_ACCESS = "no_access"
    """The parent was read and this principal cannot reach it."""

    NOT_OBSERVED = "not_observed"
    """No run has read the parent, so continuity through it cannot be claimed."""


@dataclass(frozen=True, slots=True)
class DirectoryFacts:
    """What was collected about one directory: its place in the tree and its descriptor.

    ``observed`` is false for a directory that appears here only because something below it
    was read — an ancestor no run described. Its descriptor fields are then unknown, not
    false, and are reported as such.
    """

    key: str
    path: str
    share_key: str
    parent_key: str | None
    depth: int
    observed: bool = True
    is_acl_boundary: bool | None = None
    boundary_reason: str | None = None
    dacl_protected: bool | None = None

    @property
    def name(self) -> str:
        """The last path segment, as displayed."""
        return self.path.rstrip("\\").rsplit("\\", 1)[-1]


@dataclass(frozen=True, slots=True)
class DirectoryVerdict:
    """The engine's answer for one directory, reduced to what a summary compares.

    The mask is the comparison value (ADR-0005: masks are compared, labels are rendered).
    """

    rights: RightsMask
    certainty: AccessCertainty
    outcome: AccessOutcome
    limiting_layer: LimitingLayer
    conditions: tuple[str, ...] = ()
    named: bool = True
    """Whether the directory was a candidate: its ACL names a token SID or has no DACL.
    False for a folder evaluated only because access stops at it."""

    @property
    def reachable(self) -> bool:
        """Reachable as the listing counts it: granted, and among the candidates."""
        return self.outcome is AccessOutcome.GRANTED and self.named


@dataclass(frozen=True, slots=True)
class AccessRoot:
    """One summary row: where a run of reachable directories begins, and what it holds."""

    facts: DirectoryFacts
    verdict: DirectoryVerdict
    reachable_descendants: int
    exception_count: int
    reason_counts: tuple[tuple[ExceptionReason, int], ...]
    rights_breakdown: tuple[tuple[RightsMask, int], ...]
    """Every effective mask held across the root and its reachable descendants, with how
    many directories hold it, most common first. One entry means the run is uniform in
    rights; it does not mean the ACLs are identical."""
    parent: RootParent
    resumes_within: str | None = None
    """When access resumes below an inaccessible folder that is itself inside another
    root's run, that root's key."""
    resumes_below: str | None = None
    """The inaccessible folder access resumes below — the boundary between the two runs."""

    @property
    def key(self) -> str:
        return self.facts.key

    @property
    def uniform(self) -> bool:
        """True only when nothing below the root differs from it."""
        return self.exception_count == 0


@dataclass(frozen=True, slots=True)
class AccessException:
    """One place inside a reachable run where the answer changes."""

    facts: DirectoryFacts
    root_key: str
    parent_key: str
    reasons: tuple[ExceptionReason, ...]
    verdict: DirectoryVerdict | None
    """``None`` when the engine was not asked: nothing on this ACL names the principal."""
    parent_verdict: DirectoryVerdict

    @property
    def reachable(self) -> bool:
        return self.verdict is not None and self.verdict.reachable


@dataclass(frozen=True, slots=True)
class TreeNode:
    """One directory as the tree view shows it: its answer and what lies below it."""

    facts: DirectoryFacts
    verdict: DirectoryVerdict | None
    is_root: bool
    root_key: str | None
    """The access root whose run this directory belongs to, when it is reachable."""
    reasons: tuple[ExceptionReason, ...]
    """Why this directory differs from its parent, when it is an exception."""
    reachable_below: int
    """Reachable directories anywhere beneath it, including in runs that resume lower down."""
    child_count: int
    """Children the tree offers: on the way to reachable directories, or where access stops."""
    hidden_child_count: int
    """Children read by a scan and left out: unreachable, below an unreachable folder, with
    nothing reachable beneath them."""

    @property
    def reachable(self) -> bool:
        return self.verdict is not None and self.verdict.reachable


@dataclass(frozen=True, slots=True)
class DirectoryAccessSummary:
    """Every root, every exception and the tree index, computed once for one principal."""

    roots: tuple[AccessRoot, ...]
    exceptions: Mapping[str, tuple[AccessException, ...]]
    reachable_directories: int
    unreachable_named: int
    """Directories the engine evaluated and found unreachable (named, but no access)."""
    observed_directories: int
    _nodes: Mapping[str, TreeNode] = field(repr=False)
    _children: Mapping[str | None, tuple[str, ...]] = field(repr=False)

    @property
    def exception_total(self) -> int:
        return sum(len(items) for items in self.exceptions.values())

    @property
    def shares(self) -> tuple[str, ...]:
        return tuple(sorted({root.facts.share_key for root in self.roots}))

    def root(self, key: str) -> AccessRoot | None:
        folded = key.casefold()
        return next((root for root in self.roots if root.key == folded), None)

    def node(self, key: str) -> TreeNode | None:
        return self._nodes.get(key.casefold())

    def children(self, key: str | None) -> tuple[TreeNode, ...]:
        """The tree's children of one directory, or the share roots when ``key`` is None."""
        folded = None if key is None else key.casefold()
        return tuple(self._nodes[child] for child in self._children.get(folded, ()))


def rights_change(parent: RightsMask, child: RightsMask) -> ExceptionReason | None:
    """How a child's effective mask relates to its parent's, or None when they are equal."""
    if parent.value == child.value:
        return None
    if child.value & ~parent.value == 0:
        return ExceptionReason.RIGHTS_NARROW
    if parent.value & ~child.value == 0:
        return ExceptionReason.RIGHTS_WIDEN
    return ExceptionReason.RIGHTS_DIFFER


def _unreachable_reason(facts: DirectoryFacts, verdict: DirectoryVerdict | None) -> ExceptionReason:
    if not facts.observed:
        return ExceptionReason.NOT_OBSERVED
    if verdict is None:
        return ExceptionReason.ACCESS_ENDS
    if verdict.outcome is AccessOutcome.GRANTED:
        return ExceptionReason.OWNER_ONLY
    if verdict.outcome is AccessOutcome.DENIED:
        return ExceptionReason.ACCESS_DENIED
    if verdict.outcome is AccessOutcome.INDETERMINATE:
        return ExceptionReason.ACCESS_UNKNOWN
    return ExceptionReason.ACCESS_ENDS


def _continuing_reasons(
    facts: DirectoryFacts, verdict: DirectoryVerdict, parent: DirectoryVerdict
) -> tuple[ExceptionReason, ...]:
    reasons: list[ExceptionReason] = []
    change = rights_change(parent.rights, verdict.rights)
    if change is not None:
        reasons.append(change)
    if verdict.certainty is not parent.certainty:
        reasons.append(ExceptionReason.CERTAINTY_CHANGE)
    if facts.dacl_protected:
        reasons.append(ExceptionReason.INHERITANCE_BROKEN)
    return tuple(reasons)


def summarize_directory_access(
    directories: Iterable[DirectoryFacts],
    verdicts: Mapping[str, DirectoryVerdict],
) -> DirectoryAccessSummary:
    """Group engine verdicts over a directory tree into roots, runs and exceptions.

    ``directories`` is every directory read in the shares concerned, keyed by its canonical
    comparison key; ``verdicts`` the engine's answer for every directory it evaluated. A
    reachable verdict for a directory missing from ``directories`` (its row was not read
    back) is still placed, under a synthesized facts record, so nothing reachable is lost.
    Ancestors no run read are synthesized the same way, ``observed=False``, so the tree is
    connected without claiming anything about them.
    """
    facts: dict[str, DirectoryFacts] = {item.key: item for item in directories}
    observed = len(facts)

    reachable_keys = {key for key, verdict in verdicts.items() if verdict.reachable}
    for key in reachable_keys:
        if key not in facts:
            facts[key] = _synthesized(key)
    # Ancestors of everything reachable, so the tree can get there from a share root.
    for key in sorted(reachable_keys):
        parent = facts[key].parent_key
        while parent is not None and parent not in facts:
            facts[parent] = _synthesized(parent)
            parent = facts[parent].parent_key

    # Parents before children: depth, then key, which is deterministic and total.
    ordered = sorted(facts.values(), key=lambda item: (item.depth, item.key))

    root_of: dict[str, str] = {}
    roots: list[str] = []
    exceptions: dict[str, list[AccessException]] = defaultdict(list)
    reasons_of: dict[str, tuple[ExceptionReason, ...]] = {}

    for item in ordered:
        verdict = verdicts.get(item.key)
        parent_key = item.parent_key
        parent_verdict = verdicts.get(parent_key) if parent_key is not None else None
        parent_reachable = parent_key in root_of if parent_key is not None else False

        if verdict is not None and verdict.reachable:
            if parent_reachable and parent_key is not None and parent_verdict is not None:
                root_key = root_of[parent_key]
                root_of[item.key] = root_key
                reasons = _continuing_reasons(item, verdict, parent_verdict)
                if reasons:
                    reasons_of[item.key] = reasons
                    exceptions[root_key].append(
                        AccessException(
                            facts=item,
                            root_key=root_key,
                            parent_key=parent_key,
                            reasons=reasons,
                            verdict=verdict,
                            parent_verdict=parent_verdict,
                        )
                    )
            else:
                root_of[item.key] = item.key
                roots.append(item.key)
        elif parent_reachable and parent_key is not None and parent_verdict is not None:
            # Access stops here. Only the boundary is recorded; what lies below it is
            # outside this run, and anything reachable down there is a root of its own.
            root_key = root_of[parent_key]
            reasons = (_unreachable_reason(item, verdict),)
            reasons_of[item.key] = reasons
            exceptions[root_key].append(
                AccessException(
                    facts=item,
                    root_key=root_key,
                    parent_key=parent_key,
                    reasons=reasons,
                    verdict=verdict,
                    parent_verdict=parent_verdict,
                )
            )

    members: dict[str, list[str]] = defaultdict(list)
    for key, root_key in root_of.items():
        members[root_key].append(key)

    summaries: list[AccessRoot] = []
    for root_key in roots:
        item = facts[root_key]
        verdict = verdicts[root_key]
        region = members[root_key]
        breakdown = Counter(verdicts[key].rights for key in region)
        found = exceptions.get(root_key, [])
        reason_counts = Counter(reason for entry in found for reason in entry.reasons)
        parent_state, resumes_within, resumes_below = _parent_of_root(item, facts, root_of)
        summaries.append(
            AccessRoot(
                facts=item,
                verdict=verdict,
                reachable_descendants=len(region) - 1,
                exception_count=len(found),
                reason_counts=tuple(
                    sorted(reason_counts.items(), key=lambda pair: (-pair[1], pair[0].value))
                ),
                rights_breakdown=tuple(
                    sorted(breakdown.items(), key=lambda pair: (-pair[1], pair[0].value))
                ),
                parent=parent_state,
                resumes_within=resumes_within,
                resumes_below=resumes_below,
            )
        )
    summaries.sort(key=lambda root: (root.facts.share_key, root.key))

    nodes, children = _tree(facts, verdicts, root_of, set(roots), reasons_of)
    return DirectoryAccessSummary(
        roots=tuple(summaries),
        exceptions={
            key: tuple(sorted(items, key=lambda entry: entry.facts.key))
            for key, items in exceptions.items()
        },
        reachable_directories=len(root_of),
        unreachable_named=sum(
            1 for verdict in verdicts.values() if verdict.named and not verdict.reachable
        ),
        observed_directories=observed,
        _nodes=nodes,
        _children=children,
    )


def _parent_of_root(
    item: DirectoryFacts, facts: Mapping[str, DirectoryFacts], root_of: Mapping[str, str]
) -> tuple[RootParent, str | None, str | None]:
    """Why a run starts at this root, and — if access resumes inside another run — where."""
    parent_key = item.parent_key
    if parent_key is None:
        return RootParent.SHARE_ROOT, None, None
    parent = facts.get(parent_key)
    unread = parent is None or not parent.observed
    state = RootParent.NOT_OBSERVED if unread else RootParent.NO_ACCESS
    # Walk up through what this principal cannot reach, to the nearest ancestor it can.
    below = parent_key
    ancestor = parent.parent_key if parent is not None else None
    while ancestor is not None:
        if ancestor in root_of:
            return state, root_of[ancestor], below
        below = ancestor
        found = facts.get(ancestor)
        ancestor = found.parent_key if found is not None else None
    return state, None, None


def _tree(
    facts: Mapping[str, DirectoryFacts],
    verdicts: Mapping[str, DirectoryVerdict],
    root_of: Mapping[str, str],
    roots: set[str],
    reasons_of: Mapping[str, tuple[ExceptionReason, ...]],
) -> tuple[dict[str, TreeNode], dict[str | None, tuple[str, ...]]]:
    """The tree the drill-down walks: share roots down to every reachable directory.

    A directory is offered when it is reachable, when something beneath it is, or when its
    parent is reachable and it is not — the place access stops. Everything else read under
    an unreachable folder is counted on its parent rather than listed: that is the rest of
    the file system, and listing it is what this view exists to avoid.
    """
    reachable_below: Counter[str] = Counter()
    for key in root_of:
        parent = facts[key].parent_key
        while parent is not None:
            reachable_below[parent] += 1
            parent = facts[parent].parent_key if parent in facts else None

    by_parent: dict[str | None, list[str]] = defaultdict(list)
    for key, item in facts.items():
        by_parent[item.parent_key].append(key)

    def offered(key: str) -> bool:
        if key in root_of or reachable_below[key] > 0:
            return True
        parent = facts[key].parent_key
        return parent is not None and parent in root_of

    offered_children: dict[str | None, tuple[str, ...]] = {}
    hidden: Counter[str] = Counter()
    for parent, keys in by_parent.items():
        shown = sorted(key for key in keys if offered(key))
        if parent is not None:
            hidden[parent] = len(keys) - len(shown)
        if shown:
            offered_children[parent] = tuple(shown)

    nodes: dict[str, TreeNode] = {}
    for key, item in facts.items():
        if not offered(key):
            continue
        nodes[key] = TreeNode(
            facts=item,
            verdict=verdicts.get(key),
            is_root=key in roots,
            root_key=root_of.get(key),
            reasons=reasons_of.get(key, ()),
            reachable_below=reachable_below[key],
            child_count=len(offered_children.get(key, ())),
            hidden_child_count=hidden[key],
        )
    return nodes, offered_children


def _synthesized(key: str) -> DirectoryFacts:
    """Facts for a directory known only by its key: its place in the tree and nothing more.

    Keys are canonical (case-folded) UNC paths, so the parent is the key minus its last
    segment, down to ``\\\\server\\share``.
    """
    parts = key.split("\\")
    # ['', '', server, share, *segments]
    segments = parts[4:]
    share_key = f"{parts[2]}|{parts[3]}" if len(parts) >= 4 else key
    parent = "\\".join(parts[:-1]) if segments else None
    return DirectoryFacts(
        key=key,
        path=key,
        share_key=share_key,
        parent_key=parent,
        depth=len(segments),
        observed=False,
    )
