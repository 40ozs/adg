r"""Effective access over stored observations.

The engine in :mod:`app.access_engine` is pure: hand it a token, a DACL, and a share ACL
and it tells you what a principal can do and what it had to assume. This module is what
gets those three things out of PostgreSQL without asking the database a question whose cost
grows with the size of the estate.

Three questions, three bounded shapes:

* **principal → resource.** One upward membership traversal for the token, two indexed ACL
  reads, one membership-coverage query. Constant in the size of the domain.
* **resource → principals.** The naive form is quadratic: every principal in the domain evaluated
  against this ACL. The bounded form inverts it — expand each ACL *trustee* **downward**
  once and invert the map — because a principal's rights here depend only on which of this
  ACL's trustees it belongs to. That is one traversal per trustee (tens), not one per
  principal (hundreds of thousands), and it is exact rather than approximate.
* **principal → resources.** Candidates come from the reference index keyed by the token's
  own trustees, unioned with the NULL-DACL rows that name nobody, keyset-paged; only the
  page is evaluated, and its ACLs are read in one query rather than one per row.

**What this module must never do is turn a coverage gap into a verdict.** Every place where
an answer rests on something ADG has not collected produces an
:class:`~app.access_engine.AccessFinding`, and the ones that matter most are the quiet
ones: a group nobody enumerated, a share ACL nobody read, a directory whose descriptor was
never fetched and whose permissions were therefore *projected* from an ancestor.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from app.access_engine import (
    AccessCondition,
    AccessFinding,
    AccessPath,
    AclEntry,
    AclProvenance,
    EffectiveAccess,
    ResourceDacl,
    ShareDacl,
    SidOrigin,
    SubjectFacts,
    SubjectToken,
    TokenAssumption,
    TokenSid,
    build_token,
    ntfs_entry,
    resolve_access,
    share_entry,
    unobserved_trustee_findings,
)
from app.domain import (
    DEFAULT_LIMITS,
    AceFlag,
    AceSource,
    AclAceFacts,
    DomainValidationError,
    PrincipalKind,
    Sid,
    TraversalLimits,
    UncPath,
    parse_share_identifier,
    parse_unc_path,
    project_inherited_acl,
    referenced_principal_key,
)
from app.repositories import (
    MembershipRepository,
    NtfsAceRecord,
    NtfsResourceRecord,
    Page,
    PrincipalRecord,
    ResourceRepository,
    ShareAceRecord,
    ShareRecord,
)
from app.services.graph import GROUP_KINDS, GraphService, MemberInclusion, ResolvedNode

__all__ = [
    "MAX_ANCESTOR_LEVELS",
    "MAX_EXPANDED_TRUSTEES",
    "AccessService",
    "PrincipalAccess",
    "ResolvedAccess",
    "ResourceAccessPage",
    "SubjectAccessPage",
]

MAX_ANCESTOR_LEVELS: Final = 64
"""How far up a path the resolver looks for an ancestor to project a DACL from.

Windows tolerates far deeper trees than this, but a directory sixty-four levels below the
nearest folder anybody has read is not a directory whose permissions can be usefully
predicted — every level in between is a place inheritance could have been broken. The
ceiling exists so that a pathological path costs one bounded lookup rather than a walk.
"""

MAX_EXPANDED_TRUSTEES: Final = 128
"""Trustees expanded downward when listing who can reach a resource.

A DACL with more distinct trustees than this is already unmanageable by hand, and each one
costs a bounded traversal. Exceeding it truncates the *candidate list*, which the response
reports, rather than the rights of any principal that is listed.
"""

_WORLD_TRUSTEES: Final[frozenset[str]] = frozenset({"S-1-1-0", "S-1-5-11", "S-1-5-7"})
"""Trustees whose membership is every principal there is, and is in no database.

Listed so that "who can reach this" can say plainly that it cannot enumerate them, rather
than returning a short list that looks complete.
"""


@dataclass(frozen=True, slots=True)
class ResolvedAccess:
    """One effective-access answer, with the stored rows that explain it.

    ``resource`` and ``share`` are ``None`` when no run has described them. That is a real
    state and the reason :attr:`EffectiveAccess.certainty` exists: an unread descriptor is
    not an empty one.
    """

    access: EffectiveAccess
    subject: PrincipalRecord | None
    resource: NtfsResourceRecord | None
    share: ShareRecord | None
    principals: dict[str, PrincipalRecord] = field(default_factory=dict)
    """Labels for every key named in the answer — token entries and ACL trustees alike."""


@dataclass(frozen=True, slots=True)
class PrincipalAccess:
    """One principal's effective rights to a resource, inside a listing of many."""

    key: str
    sid: str
    principal: PrincipalRecord | None
    access: EffectiveAccess
    via: tuple[TokenSid, ...]
    """The trustees that put this principal on the ACL, with the membership chain to each."""


@dataclass(frozen=True, slots=True)
class ResourceAccessPage:
    """Who can reach one resource, and what the enumeration could not cover."""

    resource_key: str
    share_key: str | None
    path: AccessPath
    resource: NtfsResourceRecord | None
    share: ShareRecord | None
    items: tuple[PrincipalAccess, ...]
    total: int
    has_more: bool
    unenumerable_trustees: tuple[str, ...]
    """ACL trustees whose members could not be listed: world SIDs, and groups whose
    membership no run has collected. Each one means principals exist that hold rights here
    and are not in ``items``."""

    truncated_trustees: bool
    findings: tuple[AccessFinding, ...]
    principals: dict[str, PrincipalRecord] = field(default_factory=dict)
    """Labels for every key the page names: the principals listed, and the trustees that
    put them there. Populated in one query so that nothing is rendered as unresolved
    merely because it was not looked up."""

    @property
    def complete(self) -> bool:
        """Whether every principal with rights here is in the listing, across all pages."""
        return not self.unenumerable_trustees and not self.truncated_trustees


@dataclass(frozen=True, slots=True)
class SubjectAccessPage:
    """What one principal can reach, one keyset page at a time."""

    subject_key: str
    subject: PrincipalRecord | None
    path: AccessPath
    items: tuple[ResolvedAccess, ...]
    has_more: bool
    next_key: str | None
    token: SubjectToken
    principals: dict[str, PrincipalRecord] = field(default_factory=dict)
    """Labels for the subject and every group in its token."""


class AccessService:
    """Effective-access answers for one request, over one database session."""

    def __init__(
        self,
        resources: ResourceRepository,
        membership: MembershipRepository,
        graph: GraphService | None = None,
    ) -> None:
        self._resources = resources
        self._membership = membership
        self._graph = graph or GraphService(membership)

    # ------------------------------------------------------ principal → resource

    async def effective_access(
        self,
        subject_key: str,
        resource_key: str,
        *,
        path: AccessPath = AccessPath.REMOTE_SMB,
        limits: TraversalLimits = DEFAULT_LIMITS,
        assumption: TokenAssumption | None = None,
    ) -> ResolvedAccess:
        """What one principal can do to one directory, and every reason it is not certain."""
        subject_record = await self._membership.get_principal(subject_key)
        token, token_labels = await self._build_token(
            subject_key, subject_record, path=path, limits=limits, assumption=assumption
        )

        resource, dacl = await self._resource_dacl(resource_key)
        share_record: ShareRecord | None = None
        share: ShareDacl | None = None
        if path is AccessPath.REMOTE_SMB:
            share_record, share = await self._share_dacl(resource_key, resource)

        findings = await self._coverage_findings(token, dacl, share)
        access = resolve_access(token, dacl, share, findings=findings)

        labels = {
            **token_labels,
            **await self._membership.principals_by_keys(
                sorted(self._keys_named_by(token, dacl, share))
            ),
        }
        return ResolvedAccess(
            access=access,
            subject=subject_record,
            resource=resource,
            share=share_record,
            principals=labels,
        )

    # ------------------------------------------------------ resource → principals

    async def effective_principals(
        self,
        resource_key: str,
        *,
        path: AccessPath = AccessPath.REMOTE_SMB,
        limits: TraversalLimits = DEFAULT_LIMITS,
        inclusion: MemberInclusion = MemberInclusion.NON_GROUPS,
        limit: int = 100,
        offset: int = 0,
    ) -> ResourceAccessPage:
        """Every principal ADG can show holds rights here, with why each one does.

        Bounded by inverting the question. Rather than evaluating every principal against
        this ACL, each **trustee** on the ACL is expanded downward once; a principal's
        rights here depend on nothing except which trustees it belongs to, so inverting
        that map gives every candidate together with the membership chain that explains it.

        The result is complete only when :attr:`ResourceAccessPage.complete` says so. An
        ACE naming ``Everyone``, or a group whose membership nobody has collected, puts
        principals here that no enumeration can produce — and the page says which trustees
        those are rather than returning a short list that looks whole.
        """
        resource, dacl = await self._resource_dacl(resource_key)
        share_record: ShareRecord | None = None
        share: ShareDacl | None = None
        if path is AccessPath.REMOTE_SMB:
            share_record, share = await self._share_dacl(resource_key, resource)

        trustees = self._trustee_keys(dacl, share, include_owner=True)
        truncated = len(trustees) > MAX_EXPANDED_TRUSTEES
        expandable = trustees[:MAX_EXPANDED_TRUSTEES]

        reached: dict[str, list[TokenSid]] = {}
        unenumerable: list[str] = []
        for trustee in expandable:
            if trustee in _WORLD_TRUSTEES:
                unenumerable.append(trustee)
            reached.setdefault(trustee, [])
            expansion = await self._graph.effective_members(trustee, limits)
            if not expansion.complete:
                unenumerable.append(trustee)
            for node in expansion.nodes:
                reached.setdefault(node.key, []).append(_token_sid_for(node, trustee))

        candidates = await self._candidates(reached, inclusion)
        total = len(candidates)
        window = candidates[max(0, offset) : max(0, offset) + max(1, limit)]

        labels = await self._membership.principals_by_keys(
            sorted({key for key, _ in window} | set(trustees))
        )
        items: list[PrincipalAccess] = []
        for key, via in window:
            record = labels.get(key)
            token = build_token(
                _subject_facts(key, record),
                via,
                access_path=path,
                membership_complete=True,
            )
            items.append(
                PrincipalAccess(
                    key=key,
                    sid=record.sid if record is not None else _sid_of(key),
                    principal=record,
                    access=resolve_access(token, dacl, share),
                    via=tuple(via),
                )
            )

        findings: list[AccessFinding] = []
        for trustee in dict.fromkeys(unenumerable):
            findings.append(
                AccessFinding(
                    AccessCondition.TRUSTEE_MEMBERSHIP_UNOBSERVED, {"trustee_key": trustee}
                )
            )
        if truncated:
            findings.append(
                AccessFinding(
                    AccessCondition.MEMBERSHIP_TRUNCATED,
                    {"trustees": len(trustees), "expanded": MAX_EXPANDED_TRUSTEES},
                )
            )

        return ResourceAccessPage(
            resource_key=dacl.resource_key,
            share_key=None if share is None else share.share_key,
            path=path,
            resource=resource,
            share=share_record,
            items=tuple(items),
            total=total,
            has_more=offset + len(window) < total,
            unenumerable_trustees=tuple(dict.fromkeys(unenumerable)),
            truncated_trustees=truncated,
            findings=tuple(findings),
            principals=labels,
        )

    # ------------------------------------------------------ principal → resources

    async def accessible_resources(
        self,
        subject_key: str,
        *,
        shares: bool = False,
        path: AccessPath = AccessPath.REMOTE_SMB,
        limits: TraversalLimits = DEFAULT_LIMITS,
        assumption: TokenAssumption | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> SubjectAccessPage:
        """One keyset page of what a principal can reach, evaluated row by row.

        ``shares=True`` answers the share question — for each share whose ACL names one of
        the token's trustees, the share ACL and the NTFS ACL of the directory it publishes,
        crossed. ``shares=False`` answers the directory question over paths.

        **Every candidate is returned with its verdict, including the ones that turn out to
        grant nothing.** Filtering them out inside the page would make ``has_more`` a lie
        about the candidate set and could return an empty page while more results existed —
        and, more importantly, "named on the ACL and holding no access" is precisely the
        distinction this engine exists to draw, so it is shown rather than hidden.
        """
        if shares and path is AccessPath.LOCAL:
            raise DomainValidationError(
                "A share is a remote access path. Asking which shares a principal can reach "
                "locally answers nothing: local access does not pass through a share ACL, so "
                "the question is about the directories the shares publish. Ask for those "
                "instead.",
                field="path",
            )
        subject_record = await self._membership.get_principal(subject_key)
        token, token_labels = await self._build_token(
            subject_key, subject_record, path=path, limits=limits, assumption=assumption
        )
        keys = sorted(token.keys)

        page: Page[str] = (
            await self._resources.shares_named_by(keys, limit=limit, after=after)
            if shares
            else await self._resources.resources_named_by(keys, limit=limit, after=after)
        )
        items = (
            await self._share_page(token, page.items, path)
            if shares
            else await self._resource_page(token, page.items, path)
        )
        return SubjectAccessPage(
            subject_key=subject_key,
            subject=subject_record,
            path=path,
            items=items,
            has_more=page.has_more,
            next_key=page.next_key,
            token=token,
            principals=token_labels,
        )

    # ---------------------------------------------------------------- page bodies

    async def _resource_page(
        self, token: SubjectToken, keys: Sequence[str], path: AccessPath
    ) -> tuple[ResolvedAccess, ...]:
        """Evaluate one page of directories, reading their rows and ACLs in bulk."""
        if not keys:
            return ()
        rows = await self._resources.ntfs_resources_by_keys(list(keys))
        acls = await self._resources.ntfs_acls_for(list(keys))
        share_keys = sorted({row.share_key for row in rows.values()})
        shares = await self._resources.shares_by_keys(share_keys)
        share_acls = (
            await self._resources.share_acls_for(share_keys)
            if path is AccessPath.REMOTE_SMB
            else {}
        )

        items: list[ResolvedAccess] = []
        for key in keys:
            row = rows.get(key)
            dacl = _dacl_from(key, row, acls.get(key, ()))
            share: ShareDacl | None = None
            share_record: ShareRecord | None = None
            if path is AccessPath.REMOTE_SMB:
                share_key = row.share_key if row is not None else _share_key_of(key)
                share_record = shares.get(share_key)
                share = _share_dacl_from(share_key, share_acls.get(share_key, ()))
            items.append(
                ResolvedAccess(
                    access=resolve_access(token, dacl, share),
                    subject=None,
                    resource=row,
                    share=share_record,
                )
            )
        return tuple(items)

    async def _share_page(
        self, token: SubjectToken, keys: Sequence[str], path: AccessPath
    ) -> tuple[ResolvedAccess, ...]:
        """Evaluate one page of shares against their published directories."""
        if not keys:
            return ()
        shares = await self._resources.shares_by_keys(list(keys))
        share_acls = await self._resources.share_acls_for(list(keys))
        root_keys = [parse_share_identifier(key).unc_path.comparison_key for key in keys]
        roots = await self._resources.ntfs_resources_by_keys(root_keys)
        root_acls = await self._resources.ntfs_acls_for(root_keys)

        items: list[ResolvedAccess] = []
        for share_key, root_key in zip(keys, root_keys, strict=True):
            root = roots.get(root_key)
            dacl = _dacl_from(root_key, root, root_acls.get(root_key, ()))
            share = (
                _share_dacl_from(share_key, share_acls.get(share_key, ()))
                if path is AccessPath.REMOTE_SMB
                else None
            )
            items.append(
                ResolvedAccess(
                    access=resolve_access(token, dacl, share),
                    subject=None,
                    resource=root,
                    share=shares.get(share_key),
                )
            )
        return tuple(items)

    # -------------------------------------------------------------------- inputs

    async def _build_token(
        self,
        subject_key: str,
        record: PrincipalRecord | None,
        *,
        path: AccessPath,
        limits: TraversalLimits,
        assumption: TokenAssumption | None,
    ) -> tuple[SubjectToken, dict[str, PrincipalRecord]]:
        """One upward traversal, turned into the SIDs an access check would see.

        The labels come back with it because the traversal already read them: rendering a
        token entry without its principal row would report an observed group as an
        unresolved SID, which is a different finding entirely.
        """
        expansion = await self._graph.effective_groups(subject_key, limits)
        groups = tuple(
            TokenSid(
                key=node.key,
                sid=node.sid,
                origin=SidOrigin.GROUP_MEMBERSHIP,
                depth=node.depth,
                path=node.path,
                display_name=None if node.principal is None else node.principal.display_name,
            )
            for node in expansion.nodes
        )
        findings = [
            AccessFinding(AccessCondition.MEMBERSHIP_CYCLE, {"members": list(cycle.members)})
            for cycle in expansion.cycles
        ]
        labels = {
            node.key: node.principal for node in expansion.nodes if node.principal is not None
        }
        if record is not None:
            labels[subject_key] = record
        token = build_token(
            _subject_facts(subject_key, record),
            groups,
            access_path=path,
            assumption=assumption,
            membership_complete=expansion.complete,
            findings=findings,
        )
        return token, labels

    async def _resource_dacl(
        self, resource_key: str
    ) -> tuple[NtfsResourceRecord | None, ResourceDacl]:
        """The DACL to evaluate for a path: the one that was read, or one projected for it.

        A path nobody read is not a path with no permissions. Windows would have given it
        whatever its parent projects onto a child, so when an ancestor *was* read the
        projection is computed and the answer is labelled
        :attr:`AclProvenance.DERIVED` — a real answer that a consumer can tell apart from a
        reading of the object itself, which is the only way it is safe to give.
        """
        key = _resource_key(resource_key)
        row = await self._resources.get_ntfs_resource(key)
        if row is not None:
            entries = await self._resources.full_ntfs_acl(key)
            return row, _dacl_from(key, row, entries)

        ancestor, distance = await self._nearest_ancestor(key)
        if ancestor is None:
            return None, ResourceDacl(
                resource_key=key, provenance=AclProvenance.UNOBSERVED, dacl_present=True
            )
        if not ancestor.dacl_present:
            # A NULL DACL projects nothing: what a child of such a parent holds comes from
            # the creating process's default DACL, which is not a fact about the parent and
            # is not something ADG can read after the fact.
            return None, ResourceDacl(
                resource_key=key, provenance=AclProvenance.UNOBSERVED, dacl_present=True
            )

        parent_entries = await self._resources.full_ntfs_acl(ancestor.resource_key)
        projected = project_inherited_acl(
            [entry.acl_facts for entry in parent_entries], for_container=True
        )
        server = parse_unc_path(key).server
        return None, ResourceDacl(
            resource_key=key,
            entries=tuple(_projected_entry(fact, server) for fact in projected),
            dacl_present=True,
            # Not knowable for an object nobody read. Reported as unprotected because a
            # projection describes a child that *did* inherit; a child that is protected is
            # a boundary and its own descriptor would have to be read to say so.
            dacl_protected=False,
            owner_sid=None,
            provenance=AclProvenance.DERIVED,
            derived_from=ancestor.resource_key,
            derived_distance=distance,
        )

    async def _nearest_ancestor(self, key: str) -> tuple[NtfsResourceRecord | None, int]:
        """The closest ancestor of a path that a run has read, and how far up it is.

        One query for the whole chain rather than a walk: the ancestors of a UNC path are a
        pure function of the path, so every candidate key is known before any row is read.
        """
        path: UncPath | None = parse_unc_path(key)
        chain: list[str] = []
        while path is not None and len(chain) < MAX_ANCESTOR_LEVELS:
            parent = path.parent
            if parent is None:
                break
            chain.append(parent.comparison_key)
            path = parent
        if not chain:
            return None, 0
        rows = await self._resources.ntfs_resources_by_keys(chain)
        for distance, candidate in enumerate(chain, start=1):
            row = rows.get(candidate)
            if row is not None:
                return row, distance
        return None, 0

    async def _share_dacl(
        self, resource_key: str, resource: NtfsResourceRecord | None
    ) -> tuple[ShareRecord | None, ShareDacl]:
        """The share ACL in front of a path, and whether anybody has read it."""
        share_key = resource.share_key if resource is not None else _share_key_of(resource_key)
        record = await self._resources.get_share(share_key)
        entries = await self._resources.full_share_acl(share_key)
        return record, _share_dacl_from(share_key, entries)

    async def _coverage_findings(
        self, token: SubjectToken, dacl: ResourceDacl, share: ShareDacl | None
    ) -> tuple[AccessFinding, ...]:
        """Which ACL trustees the answer turns on and nobody has looked inside.

        Only the **unmatched** trustees matter: a trustee already in the token is one the
        subject demonstrably belongs to, and one that is demonstrably a user account cannot
        contain anybody. What is left is the set of groups — and of SIDs nothing describes,
        which could be groups — whose membership has never been collected, and for each of
        those "the subject is not in it" is an assumption rather than a finding.
        """
        candidates = [
            key
            for key in self._trustee_keys(dacl, share, include_owner=False)
            if not token.contains(key)
        ]
        if not candidates:
            return ()
        records = await self._membership.principals_by_keys(candidates)
        possible_groups = [
            key
            for key in candidates
            if (record := records.get(key)) is None or record.principal_kind in GROUP_KINDS
        ]
        if not possible_groups:
            return ()
        known = await self._membership.keys_with_members(possible_groups)
        return unobserved_trustee_findings(possible_groups, trustees_with_membership=known)

    # ------------------------------------------------------------------- helpers

    @staticmethod
    def _trustee_keys(
        dacl: ResourceDacl, share: ShareDacl | None, *, include_owner: bool
    ) -> tuple[str, ...]:
        """Every principal named by either ACL, in evaluation order, without duplicates.

        ``INHERIT_ONLY`` entries are excluded: they grant nothing on this object, so a
        trustee named only by one of them holds no rights here and listing it would report
        access that does not exist.
        """
        keys: list[str] = []
        if include_owner and dacl.owner_key is not None:
            keys.append(dacl.owner_key)
        keys.extend(entry.trustee_key for entry in dacl.entries if entry.applies_to_this_object)
        if share is not None:
            keys.extend(entry.trustee_key for entry in share.entries)
        return tuple(dict.fromkeys(keys))

    @staticmethod
    def _keys_named_by(
        token: SubjectToken, dacl: ResourceDacl, share: ShareDacl | None
    ) -> set[str]:
        """Every key a response will mention, so all of them are labelled in one query."""
        keys = set(token.keys)
        keys.update(entry.trustee_key for entry in dacl.entries)
        if dacl.owner_key is not None:
            keys.add(dacl.owner_key)
        if share is not None:
            keys.update(entry.trustee_key for entry in share.entries)
        return keys

    async def _candidates(
        self, reached: dict[str, list[TokenSid]], inclusion: MemberInclusion
    ) -> tuple[tuple[str, tuple[TokenSid, ...]], ...]:
        """The principals to evaluate, filtered by kind and sorted for stable paging.

        Sorted by key rather than by rights: an offset cursor over a recomputed list is only
        honest if the list orders the same way twice, and a rights-ordered list would
        reshuffle the moment an ACL changed between pages.
        """
        records = await self._membership.principals_by_keys(sorted(reached))
        selected: list[tuple[str, tuple[TokenSid, ...]]] = []
        for key in sorted(reached):
            record = records.get(key)
            kind = record.principal_kind if record is not None else None
            if not _matches(kind, inclusion):
                continue
            selected.append((key, tuple(reached[key])))
        return tuple(selected)


def _matches(kind: PrincipalKind | None, inclusion: MemberInclusion) -> bool:
    """Whether a principal of this kind belongs in the listing.

    An **unknown** kind is kept for every inclusion except ``USERS``, for the same reason
    :class:`app.services.graph.MemberInclusion` keeps it: a SID on an ACL that nothing has
    described is the orphan finding, and filtering it out of "who can reach this" would
    delete exactly the row somebody needs to see.
    """
    if inclusion is MemberInclusion.ALL:
        return True
    if inclusion is MemberInclusion.USERS:
        return kind in (PrincipalKind.USER, PrincipalKind.MANAGED_SERVICE_ACCOUNT)
    return kind not in GROUP_KINDS


def _token_sid_for(node: ResolvedNode, trustee: str) -> TokenSid:
    """The token entry a reached principal gets for the trustee whose expansion found it.

    The entry describes the **trustee**, not the node: a token is matched on the key an ACE
    names, so what goes into it is the group that is on the ACL. The node supplies the
    explanation — how many hops away it is, and the chain.

    ``path`` is reversed from the traversal's. The expansion ran downward from the trustee,
    and an explanation reads upward from the principal: *Alice → Finance-Team → Finance-RW*,
    not the other way round.
    """
    return TokenSid(
        key=trustee,
        sid=_sid_of(trustee),
        origin=SidOrigin.GROUP_MEMBERSHIP,
        depth=node.depth,
        path=tuple(reversed(node.path)),
    )


def _subject_facts(key: str, record: PrincipalRecord | None) -> SubjectFacts:
    return SubjectFacts(
        key=key,
        sid=record.sid if record is not None else _sid_of(key),
        kind=record.principal_kind if record is not None else None,
        display_name=record.display_name if record is not None else None,
        enabled=record.enabled if record is not None else None,
    )


def _sid_of(key: str) -> str:
    """The SID inside a storage key. Split on the last separator: a SID never contains one."""
    _, separator, sid = key.rpartition("|")
    return sid if separator else key


def _resource_key(identifier: str) -> str:
    """Validate and fold a directory identifier, refusing anything that is not a UNC path."""
    try:
        return parse_unc_path(identifier).comparison_key
    except DomainValidationError:
        raise
    except ValueError as exc:  # pragma: no cover - parse_unc_path raises the domain error
        raise DomainValidationError(str(exc), value=identifier, field="resource") from exc


def _share_key_of(resource_key: str) -> str:
    r"""The share key for a path: ``\\FS01\Finance\Reports`` to ``fs01|finance``."""
    path = parse_unc_path(resource_key)
    return f"{path.server.casefold()}|{path.share.casefold()}"


def _dacl_from(
    key: str, row: NtfsResourceRecord | None, entries: Sequence[NtfsAceRecord]
) -> ResourceDacl:
    """Build the resolver's input from a stored resource row and its stored entries.

    ``row is None`` with entries present is an orphaned ACL — ACEs stored for a path no
    ``ntfs_resource`` observation describes, which is what a run that read entries and
    failed before reporting the descriptor leaves behind. The entries are evaluated,
    because they are real observations, and the descriptor facts are left at their
    conservative defaults with the provenance saying the object itself was not read.
    """
    converted = tuple(_entry_from(record) for record in entries)
    if row is None:
        return ResourceDacl(
            resource_key=key,
            entries=converted,
            dacl_present=True,
            provenance=AclProvenance.OBSERVED if converted else AclProvenance.UNOBSERVED,
        )
    return ResourceDacl(
        resource_key=row.resource_key,
        entries=converted,
        dacl_present=row.dacl_present,
        dacl_protected=row.dacl_protected,
        owner_sid=row.owner_sid,
        # The descriptor was read on the server in this path, so a BUILTIN owner means that
        # machine's local group -- the same rule, and the same function, an ACE's trustee
        # key goes through on the way in.
        owner_key=(
            None
            if row.owner_sid is None
            else referenced_principal_key(Sid(row.owner_sid), row.server_key)
        ),
        declared_ace_count=row.ace_count,
        provenance=AclProvenance.OBSERVED,
    )


def _share_dacl_from(share_key: str, entries: Sequence[ShareAceRecord]) -> ShareDacl:
    """Build the share side, treating an empty stored ACL as unread rather than as empty.

    Windows shares always carry a share ACL — there is no way to create one without — so
    zero stored entries means nobody has read it, not that it grants nothing. The
    difference is the whole of :attr:`ShareDacl.observed`, and getting it backwards would
    report every unscanned share as denying everybody.
    """
    if not entries:
        return ShareDacl(share_key=share_key, observed=False)
    return ShareDacl(
        share_key=share_key,
        entries=tuple(_share_entry_from(record) for record in entries),
        observed=True,
    )


def _entry_from(record: NtfsAceRecord) -> AclEntry:
    return ntfs_entry(
        trustee_key=record.trustee_key,
        trustee_sid=record.trustee_sid,
        ace_type=record.ace_type,
        access_mask=record.access_mask,
        flags=record.ace_flags,
        source=record.source,
        order_index=record.order_index,
        ace_key=record.ace_key,
        inherited_from=record.inherited_from,
    )


def _share_entry_from(record: ShareAceRecord) -> AclEntry:
    return share_entry(
        trustee_key=record.trustee_key,
        trustee_sid=record.trustee_sid,
        ace_type=record.ace_type,
        access_mask=record.access_mask,
        permission=record.permission,
        order_index=record.order_index,
        ace_key=record.ace_key,
    )


def _projected_entry(fact: AclAceFacts, server: str) -> AclEntry:
    """One entry of a projected DACL, with its trustee resolved in the server's context.

    The projection works in SIDs, because that is what a descriptor stores. A trustee key is
    what an access check matches on, and deriving it here — through the same function the
    collectors' ingestion uses — is what keeps a BUILTIN SID on a projected DACL scoped to
    the machine whose tree it was projected onto.

    ``source`` is read back off the flag byte rather than assumed: every entry a projection
    produces carries ``INHERITED``, which is exactly what makes it inherited, and deriving
    the two from one another is what keeps :class:`app.domain.NtfsAce`'s consistency rule
    from being violated by a value nobody checked.
    """
    return ntfs_entry(
        trustee_key=referenced_principal_key(Sid(fact.trustee_sid), server),
        trustee_sid=fact.trustee_sid,
        ace_type=fact.ace_type,
        access_mask=fact.access_mask,
        flags=fact.ace_flags,
        source=(AceSource.INHERITED if fact.ace_flags & AceFlag.INHERITED else AceSource.EXPLICIT),
        order_index=fact.order_index,
    )
