r"""Directory access for one principal: the engine's verdicts, laid over the directory tree.

:class:`DirectoryAccessService` answers the question the flat *Reachable directories* list
answers — what can this principal reach inside the shares? — in the shape an administrator
reads: access roots, how much each reaches, and where the answer changes beneath them
(:mod:`app.services.directory_summary`).

It adds no access semantics. The verdicts are the access engine's, produced by
:meth:`AccessService.all_accessible_resources` with the token, candidate rule and per-
directory evaluation the paged listing uses. What this service adds is the *outline*: every
directory read in the shares a reachable directory sits in, with no ACEs, so the summary can
see where access stops — including at folders whose ACL names nobody in the token and that
the engine therefore never had a reason to evaluate.

**Bounded, and says so.** At most :data:`MAX_SUMMARY_CANDIDATES` candidates are evaluated
and at most :data:`MAX_OUTLINE_DIRECTORIES` outline rows read. Past either, the summary is
still computed from what was read, and ``complete`` is false with the reason named: a
summary of part of an estate that looks like a summary of all of it is the error ADR-0012
and ADR-0016 exist to prevent.

**Computed once per collected state.** A summary costs a whole candidate evaluation; the tree
and the exception list are slices of it. :class:`DirectoryAccessCache` keeps the last few
computations keyed by the collection basis (the same validator the ETag carries), so
expanding a branch or opening a root's exceptions reads the basis and slices — and any
ingestion changes the basis, so nothing outlives the facts it was computed from. There is no
time-to-live, as everywhere else in ADG.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Final

from app.access_engine import AccessPath, SubjectToken, TokenAssumption, classify_access
from app.domain import (
    DEFAULT_LIMITS,
    DomainValidationError,
    ResourceKind,
    TraversalLimits,
    parse_unc_path,
)
from app.repositories import MembershipRepository, PrincipalRecord, ResourceRepository
from app.services.access import AccessService, ResolvedAccess
from app.services.directory_summary import (
    DirectoryAccessSummary,
    DirectoryFacts,
    DirectoryVerdict,
    summarize_directory_access,
)

__all__ = [
    "MAX_OUTLINE_DIRECTORIES",
    "MAX_SUMMARY_CANDIDATES",
    "DirectoryAccess",
    "DirectoryAccessCache",
    "DirectoryAccessService",
    "EvaluatedPath",
]

#: Candidates evaluated for one summary. A principal named on more directories than this is
#: summarized over the first this many (in key order) and the answer says it is partial.
MAX_SUMMARY_CANDIDATES: Final = 50_000

#: Directory rows read to place the candidates in their tree. Narrow rows, no ACEs.
MAX_OUTLINE_DIRECTORIES: Final = 250_000


@dataclass(frozen=True, slots=True)
class EvaluatedPath:
    """One candidate the engine evaluated, with the facts the full-detail export reports.

    Every candidate, reachable or not, file or directory: the complete underlying set the
    paged *All paths* listing walks through, so an export of it loses nothing the summary
    grouped away.
    """

    key: str
    path: str
    share_key: str | None
    resource_kind: str | None
    observed: bool
    verdict: DirectoryVerdict
    is_acl_boundary: bool | None
    boundary_reason: str | None
    dacl_protected: bool | None


@dataclass(frozen=True, slots=True)
class DirectoryAccess:
    """One principal's directory access, summarized, with what it was computed from."""

    subject_key: str
    subject: PrincipalRecord | None
    path: AccessPath
    token: SubjectToken
    principals: dict[str, PrincipalRecord]
    summary: DirectoryAccessSummary
    paths: tuple[EvaluatedPath, ...]
    candidates_evaluated: int
    candidates_truncated: bool
    outline_truncated: bool
    files_excluded: int
    """Reachable candidates that are files. The summary is about directories; files stay in
    the full-detail paths."""

    @property
    def complete(self) -> bool:
        return not (self.candidates_truncated or self.outline_truncated)

    @property
    def truncation(self) -> list[str]:
        reasons: list[str] = []
        if self.candidates_truncated:
            reasons.append("candidates")
        if self.outline_truncated:
            reasons.append("outline")
        return reasons


def verdict_of(item: ResolvedAccess, *, named: bool = True) -> DirectoryVerdict:
    """The engine's answer for one directory, reduced to what the summary compares."""
    access = item.access
    return DirectoryVerdict(
        rights=access.rights,
        certainty=access.certainty,
        outcome=classify_access(access).outcome,
        limiting_layer=access.limiting_layer,
        conditions=tuple(condition.value for condition in access.conditions),
        named=named,
    )


def facts_of(
    key: str,
    path: str,
    share_key: str,
    *,
    is_acl_boundary: bool | None,
    boundary_reason: str | None,
    dacl_protected: bool | None,
) -> DirectoryFacts:
    """Place one directory in the tree from its path, through the domain's own parser."""
    unc = parse_unc_path(path)
    parent = unc.parent
    return DirectoryFacts(
        key=key,
        path=unc.value,
        share_key=share_key,
        parent_key=None if parent is None else parent.comparison_key,
        depth=len(unc.segments),
        is_acl_boundary=is_acl_boundary,
        boundary_reason=boundary_reason,
        dacl_protected=dacl_protected,
    )


class DirectoryAccessService:
    """Directory access answers for one request, over one database session."""

    def __init__(self, resources: ResourceRepository, membership: MembershipRepository) -> None:
        self._resources = resources
        self._access = AccessService(resources, membership)

    async def directory_access(
        self,
        subject_key: str,
        *,
        path: AccessPath = AccessPath.REMOTE_SMB,
        limits: TraversalLimits = DEFAULT_LIMITS,
        assumption: TokenAssumption | None = None,
        max_candidates: int = MAX_SUMMARY_CANDIDATES,
        max_outline: int = MAX_OUTLINE_DIRECTORIES,
    ) -> DirectoryAccess:
        if path is AccessPath.LOCAL:
            # Not refused for any deep reason: local access summarizes the same way. It is
            # refused because the UI offers only the remote answer and an unoffered variant
            # is an untested one. Ask the paged listing for local answers.
            raise DomainValidationError(
                "The directory-access summary answers for access over SMB. For local access, "
                "page through /api/v1/access/principals/{principal}/resources?access_path=local.",
                field="access_path",
            )
        evaluated = await self._access.all_accessible_resources(
            subject_key,
            path=path,
            limits=limits,
            assumption=assumption,
            max_candidates=max_candidates,
        )

        paths: list[EvaluatedPath] = []
        verdicts: dict[str, DirectoryVerdict] = {}
        reachable_shares: set[str] = set()
        files_excluded = 0
        for item in evaluated.items:
            verdict = verdict_of(item)
            row = item.resource
            key = item.access.resource_key
            share_key = row.share_key if row is not None else item.access.share_key
            kind = row.resource_kind.value if row is not None else None
            paths.append(
                EvaluatedPath(
                    key=key,
                    path=row.path if row is not None else key,
                    share_key=share_key,
                    resource_kind=kind,
                    observed=row is not None,
                    verdict=verdict,
                    is_acl_boundary=row.is_acl_boundary if row is not None else None,
                    boundary_reason=(
                        row.boundary_reason.value
                        if row is not None and row.boundary_reason is not None
                        else None
                    ),
                    dacl_protected=row.dacl_protected if row is not None else None,
                )
            )
            if kind == ResourceKind.FILE.value:
                files_excluded += int(verdict.reachable)
                continue
            verdicts[key] = verdict
            if verdict.reachable and share_key is not None:
                reachable_shares.add(share_key)

        outline, outline_truncated = await self._resources.directory_outline(
            sorted(reachable_shares), limit=max_outline
        )
        directories = [
            facts_of(
                row.resource_key,
                row.path,
                row.share_key,
                is_acl_boundary=row.is_acl_boundary,
                boundary_reason=row.boundary_reason,
                dacl_protected=row.dacl_protected,
            )
            for row in outline
        ]
        # Where access stops at a folder the reference index did not offer, ask the engine
        # anyway: "nothing names you here" is its verdict to give (no grant, a Deny, or not
        # established from what was collected), not the summary's to assume. A bounded set
        # — only the children of reachable folders — evaluated in the listing's own chunks.
        reachable = {key for key, verdict in verdicts.items() if verdict.reachable}
        stops = [
            item.key
            for item in directories
            if item.parent_key in reachable and item.key not in verdicts
        ]
        for resolved in await self._access.evaluate_resources(evaluated.token, stops, path):
            verdicts[resolved.access.resource_key] = verdict_of(resolved, named=False)
        summary = summarize_directory_access(directories, verdicts)
        return DirectoryAccess(
            subject_key=subject_key,
            subject=evaluated.subject,
            path=evaluated.path,
            token=evaluated.token,
            principals=evaluated.principals,
            summary=summary,
            paths=tuple(paths),
            candidates_evaluated=len(evaluated.items),
            candidates_truncated=evaluated.truncated,
            outline_truncated=outline_truncated,
            files_excluded=files_excluded,
        )


class DirectoryAccessCache:
    """The last few directory-access computations, keyed by collection basis and question.

    The key is the request's cache validator — contract version, collection basis, and every
    parameter that changes the answer — so an entry is reused only while nothing has been
    collected, exactly as an ETag is. Bounded by entry count; the oldest is dropped first.
    One per application, held on ``app.state``.
    """

    def __init__(self, max_entries: int = 8) -> None:
        self._entries: OrderedDict[str, DirectoryAccess] = OrderedDict()
        self._max = max_entries

    def get(self, key: str) -> DirectoryAccess | None:
        found = self._entries.get(key)
        if found is not None:
            self._entries.move_to_end(key)
        return found

    def put(self, key: str, value: DirectoryAccess) -> None:
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)
