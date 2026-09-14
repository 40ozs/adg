r"""What a reconciled scope is allowed to mark absent.

Absence is the dangerous half of a history model. Every other operation adds knowledge; this
one removes access from the record, and an audit tool that removes access nobody revoked
reports a permission problem as solved. So the permission to infer absence is deliberately
narrow, and it is narrow in three independent ways:

1. **Only a run that reconciled a scope may close anything.** Reconciliation is already
   guarded before this module is reached: the contract models refuse to build a completion
   that reconciles while reporting a non-``succeeded`` status or any error at all
   (:class:`app.contracts.v1.ScanRunCompletion`); the ingestion service refuses a scope the
   run never declared, refuses every scope on an ``incremental`` run, and drops the
   reconciliation entirely when it downgrades a run because batches went missing
   (:meth:`app.ingestion.IngestionService.complete_run`). A failed, partial, incremental or
   short-delivered run therefore arrives here with nothing to reconcile.

2. **Only the kinds that collector reports.** An SMB run enumerates shares; it never opens
   a directory. If a ``server`` scope reconciled by the SMB collector could close NTFS
   rows, every SMB scan would erase the file-system half of that server — the collector
   would be marking things absent that it is structurally incapable of seeing.
   :data:`CLOSURE_RULES` is keyed by ``(collector, scope kind)`` for exactly this reason,
   and a pair with no entry closes nothing.

3. **Only the objects inside the scope key.** A run that reconciled ``\\fs01\finance`` may
   not speak about ``\\fs01\hr``, and the selectors below say precisely how "inside" is
   decided for each kind.

## Selectors, and why the ACL kinds are defined through their parent

An ACE is not independently enumerable: a collector reads a *descriptor*, and the entries
come with it. So the rule for an ACE kind is not a predicate of its own but
:class:`ViaParent` — "every ACE of the resources this scope selected". That is both simpler
and more correct than a predicate over ACE keys: it cannot select an ACE whose resource is
out of scope, and it cannot miss one whose key happens not to look like the scope's.

The same relation gives the AD case its shape. A ``domain`` run enumerates *groups and
their members*, so the edges it is authoritative for are the edges of the principals it is
authoritative for — :class:`ViaParent` on ``group_key``, not a guess from the edge key.

## The domain scope resolves its own key space

Every other scope key is a value stored on the rows it selects: a host name, a server key,
a share key, a UNC prefix. A ``domain`` scope key is the domain's **DNS name**, and no row
stores it. Mapping DNS to a domain SID by string surgery on ``distinguished_name`` would
work most of the time, and "most of the time" is not a property to hang deletion on.

:class:`RunDomains` instead resolves the key space from the run's own observations: the
distinct ``domain_sid`` values of the principals this run reported. That is sound because
it is the same authority the reconciliation itself rests on — the run claims to have
enumerated a domain completely, and whichever domain its principals belong to is the domain
it enumerated. It also fails safe: a run that reported no principal with a domain SID
resolves an empty key space and closes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.contracts.v1.common import ObservationKind, ScopeKind
from app.domain.errors import DomainValidationError
from app.domain.observation import CollectorKind
from app.domain.paths import parse_unc_path

__all__ = [
    "CLOSURE_RULES",
    "ClosureRule",
    "ColumnEquals",
    "DirectorySubtree",
    "LocalScoping",
    "RunDomains",
    "Selector",
    "SubtreeOfScopeKey",
    "ViaParent",
    "closure_rules_for",
    "directory_subtree_of",
    "reconcilable_kinds",
]


class LocalScoping(StrEnum):
    """Whether a selector additionally requires a row to be host-scoped, or not to be.

    ``S-1-5-32-544`` exists on every Windows machine and means a different group on each,
    so a local group and a domain group can carry SIDs that sort together and belong to
    entirely different authorities. A domain run must never close a local group, and a
    local-groups run must never close a domain principal; both would be one collector
    deleting another's facts.
    """

    ANY = "any"
    DOMAIN_ONLY = "domain_only"
    """``host_key IS NULL``."""

    HOST_ONLY = "host_only"
    """``host_key IS NOT NULL``."""


@dataclass(frozen=True, slots=True)
class ColumnEquals:
    """Rows whose ``column`` equals the scope key exactly."""

    column: str
    scoping: LocalScoping = LocalScoping.ANY


@dataclass(frozen=True, slots=True)
class DirectorySubtree:
    r"""NTFS resources at or beneath a ``directory_tree`` scope key.

    Two predicates, together, and the first one is what keeps this from being a table scan:

    * ``share_key`` equals the share the tree key names, which is an indexed equality
      (``ix_ntfs_resources_share``), and
    * the resource key is the tree key itself or lies beneath it.

    The second predicate is a prefix test written with ``starts_with`` rather than ``LIKE``.
    ``LIKE`` would be wrong twice over on this data: PostgreSQL's default escape character
    is a backslash, which is the separator in every UNC path, and ``_`` — a wildcard — is an
    ordinary and common character in a Windows directory name. A pattern built from a real
    path would both mis-escape and over-match.
    """

    tree_key: str
    share_key: str
    is_share_root: bool
    """When the scope names a whole share, the ``share_key`` equality is the entire test and
    the prefix check is redundant. Kept as a flag rather than discovered by the caller so
    that the cheaper query shape is chosen by the rule, not by remembering to."""


@dataclass(frozen=True, slots=True)
class SubtreeOfScopeKey:
    """A placeholder for :class:`DirectorySubtree`, resolved once the scope key is known.

    The rule table is a constant and a ``directory_tree`` scope key is not, so the rule
    names the *shape* of the predicate and :func:`directory_subtree_of` fills it in at
    closure time. Written as its own type rather than as a nullable field so that a rule
    reaching the query builder unresolved is a type error rather than a ``None`` that
    quietly selects everything.
    """


@dataclass(frozen=True, slots=True)
class RunDomains:
    """Principals of the domains this run itself observed. See the module docstring."""

    column: str = "domain_sid"
    scoping: LocalScoping = LocalScoping.DOMAIN_ONLY


@dataclass(frozen=True, slots=True)
class ViaParent:
    """Rows whose ``column`` names an object the parent rule already selected.

    ``parent`` is the kind whose rule produces the key set. The rule for that kind must
    appear in the same scope's rule list, and :func:`closure_rules_for` checks it: a
    ``ViaParent`` pointing at a kind nobody selected would quietly select nothing, and a
    closure that silently does nothing is indistinguishable from one that is switched off.
    """

    column: str
    parent: ObservationKind
    scoping: LocalScoping = LocalScoping.ANY


Selector = ColumnEquals | DirectorySubtree | RunDomains | SubtreeOfScopeKey | ViaParent


@dataclass(frozen=True, slots=True)
class ClosureRule:
    """One object kind a scope may mark absent, and how its members are selected."""

    kind: ObservationKind
    selector: Selector


_AD_DOMAIN: Final[tuple[ClosureRule, ...]] = (
    ClosureRule(ObservationKind.PRINCIPAL, RunDomains()),
    # An edge belongs to the group whose membership was enumerated, so the edges a domain
    # run is authoritative for are exactly the edges of the principals it is authoritative
    # for. Restricted to domain edges as well: a local group can contain a domain user, and
    # that edge is a fact about the machine, observed by the local-groups collector.
    ClosureRule(
        ObservationKind.MEMBERSHIP_EDGE,
        ViaParent("group_key", ObservationKind.PRINCIPAL, LocalScoping.DOMAIN_ONLY),
    ),
)

_LOCAL_GROUPS_HOST: Final[tuple[ClosureRule, ...]] = (
    ClosureRule(ObservationKind.PRINCIPAL, ColumnEquals("host_key", LocalScoping.HOST_ONLY)),
    ClosureRule(ObservationKind.MEMBERSHIP_EDGE, ColumnEquals("host_key", LocalScoping.HOST_ONLY)),
)

_SMB_SERVER: Final[tuple[ClosureRule, ...]] = (
    ClosureRule(ObservationKind.SERVER, ColumnEquals("server_key")),
    ClosureRule(ObservationKind.SMB_SHARE, ColumnEquals("server_key")),
    ClosureRule(ObservationKind.SMB_ACE, ViaParent("share_key", ObservationKind.SMB_SHARE)),
)

_SMB_SHARE: Final[tuple[ClosureRule, ...]] = (
    ClosureRule(ObservationKind.SMB_SHARE, ColumnEquals("share_key")),
    ClosureRule(ObservationKind.SMB_ACE, ViaParent("share_key", ObservationKind.SMB_SHARE)),
)

_NTFS_ACES_OF_SELECTED: Final = ClosureRule(
    ObservationKind.NTFS_ACE, ViaParent("resource_key", ObservationKind.NTFS_RESOURCE)
)

#: ``(collector, scope kind)`` -> what a reconciliation of that scope may close.
#:
#: A pair absent from this mapping closes nothing. That is the deliberate default: a
#: collector reconciling a scope this module has no rule for is a collector making a claim
#: ADG does not know how to bound, and the safe reading of an unbounded claim is to ignore
#: it rather than to guess at its edges.
CLOSURE_RULES: Final[dict[tuple[CollectorKind, ScopeKind], tuple[ClosureRule, ...]]] = {
    (CollectorKind.ACTIVE_DIRECTORY, ScopeKind.DOMAIN): _AD_DOMAIN,
    (CollectorKind.LOCAL_GROUPS, ScopeKind.LOCAL_GROUPS_HOST): _LOCAL_GROUPS_HOST,
    (CollectorKind.SMB, ScopeKind.SERVER): _SMB_SERVER,
    (CollectorKind.SMB, ScopeKind.SHARE): _SMB_SHARE,
    (CollectorKind.NTFS, ScopeKind.SERVER): (
        ClosureRule(ObservationKind.NTFS_RESOURCE, ColumnEquals("server_key")),
        _NTFS_ACES_OF_SELECTED,
    ),
    (CollectorKind.NTFS, ScopeKind.SHARE): (
        ClosureRule(ObservationKind.NTFS_RESOURCE, ColumnEquals("share_key")),
        _NTFS_ACES_OF_SELECTED,
    ),
    (CollectorKind.NTFS, ScopeKind.DIRECTORY_TREE): (
        ClosureRule(ObservationKind.NTFS_RESOURCE, SubtreeOfScopeKey()),
        _NTFS_ACES_OF_SELECTED,
    ),
}


def closure_rules_for(collector: CollectorKind, scope_kind: ScopeKind) -> tuple[ClosureRule, ...]:
    """The rules for one ``(collector, scope kind)`` pair; empty when there are none.

    Also checks the ``ViaParent`` references, because the failure mode of a dangling one is
    silence: it would select an empty key set, close nothing, and look exactly like a scope
    with no objects in it.
    """
    rules = CLOSURE_RULES.get((collector, scope_kind), ())
    selected = {rule.kind for rule in rules}
    for rule in rules:
        if isinstance(rule.selector, ViaParent) and rule.selector.parent not in selected:
            raise DomainValidationError(
                f"The closure rule for {rule.kind.value} under {collector.value}/"
                f"{scope_kind.value} selects through {rule.selector.parent.value}, which "
                "that scope does not select. It would close nothing, indistinguishably "
                "from a scope that is empty.",
                field="selector",
            )
    return rules


def reconcilable_kinds(
    collector: CollectorKind, scope_kind: ScopeKind
) -> frozenset[ObservationKind]:
    """Which object kinds a reconciliation of this scope may mark absent."""
    return frozenset(rule.kind for rule in closure_rules_for(collector, scope_kind))


def directory_subtree_of(tree_key: str) -> DirectorySubtree:
    r"""Parse a ``directory_tree`` scope key into the predicate that selects its resources.

    The share key is derived from the path rather than taken from the scope, because a
    ``directory_tree`` scope names a path and ``ntfs_resources.share_key`` is
    ``server|share`` — the same identity spelled the way :attr:`app.domain.SmbShare.
    identity_key` spells it. Deriving it here means the two spellings are produced by one
    piece of code and cannot drift.

    Raises:
        DomainValidationError: the key is not a UNC path, so it names no tree at all. The
            caller turns this into "this scope closes nothing" rather than a failed
            completion: a collector that sent a malformed scope key has already had its
            observations accepted, and refusing the completion afterwards would lose them.
    """
    path = parse_unc_path(tree_key)
    return DirectorySubtree(
        tree_key=path.comparison_key,
        share_key=f"{path.server.casefold()}|{path.share.casefold()}",
        is_share_root=path.is_share_root,
    )
