"""Membership as a graph of edges.

Group membership is stored as **edges, never as expanded sets**. An expanded set ("these
480 users are in Finance-RW") answers *who*, but throws away *why*, and "why" is the
product. It also goes stale the moment one nested group changes, and it cannot be diffed
usefully. One edge per observed relationship keeps the explanation
(`alice → Finance-Team → Finance-RW → ACE on \\\\FS01\\Finance`) reconstructible for as
long as the edges are stored.

Four realities this model must survive:

* **Nesting.** Groups contain groups, several levels deep, across domains.
* **Local groups.** A domain group inside ``BUILTIN\\Administrators`` on one server grants
  nothing on any other server. Local edges are therefore scoped by host.
* **Foreign security principals.** A member from a trusted forest appears only as a SID
  (in AD, as an FSP object). It is a real edge to a principal this domain cannot describe.
* **Cycles and malformed graphs.** Directories can end up containing cycles, and a
  collector can observe an edge whose endpoint it cannot resolve. Both are **recorded, not
  rejected**: refusing to store an observed edge would hide the anomaly instead of
  reporting it. Only a self-edge is rejected, because it can only be a collector defect.

Expansion of these edges is deliberately absent here; it belongs to Phase 1's graph APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.errors import DomainValidationError
from app.domain.identity import PrincipalKind, Sid


class MembershipEdgeKind(StrEnum):
    """How a membership relationship was established."""

    DIRECTORY_GROUP_MEMBER = "directory_group_member"
    """A ``member``/``memberOf`` relationship in a directory (user→group or group→group)."""

    PRIMARY_GROUP = "primary_group"
    """Membership via ``primaryGroupID``.

    This membership does **not** appear in the group's ``member`` attribute. A collector
    that reads only ``member`` silently loses every user's primary group — typically
    ``Domain Users``, which is on a great many ACLs.
    """

    LOCAL_GROUP_MEMBER = "local_group_member"
    """Membership in a group in one computer's local account database."""

    WELL_KNOWN_IMPLICIT = "well_known_implicit"
    """Implicit membership such as Authenticated Users or Everyone.

    Recorded only when a source states it. Windows grants these at logon; they are never
    directory edges, and inventing them here would fabricate membership.
    """


@dataclass(frozen=True, slots=True)
class MembershipEdge:
    """A directed edge: ``member_sid`` is a member of ``group_sid``.

    Attributes:
        group_sid: the containing group.
        member_sid: the contained principal, which may itself be a group.
        kind: how the relationship was established.
        host_key: required for local-group edges; the computer the group lives on. A
            BUILTIN group SID is identical everywhere, so an unscoped local edge would
            merge unrelated servers' administrators into one group.
        member_kind: what the member is, when the source resolved it.
        is_foreign_security_principal: the member comes from another domain or forest and
            is represented locally by its SID only.
    """

    group_sid: Sid
    member_sid: Sid
    kind: MembershipEdgeKind = MembershipEdgeKind.DIRECTORY_GROUP_MEMBER
    host_key: str | None = None
    member_kind: PrincipalKind | None = None
    is_foreign_security_principal: bool = False

    def __post_init__(self) -> None:
        if self.group_sid == self.member_sid:
            raise DomainValidationError(
                f"A group cannot be a direct member of itself ({self.group_sid}). Windows "
                "does not create such an edge; observing one indicates a collector defect.",
                value=self.group_sid.value,
                field="member_sid",
            )
        if self.kind is MembershipEdgeKind.LOCAL_GROUP_MEMBER:
            if not self.host_key or not self.host_key.strip():
                raise DomainValidationError(
                    "A local-group membership edge must record the host it was observed "
                    "on: BUILTIN group SIDs are identical on every computer.",
                    field="host_key",
                )
        elif self.host_key is not None:
            raise DomainValidationError(
                f"host_key applies only to local-group edges; edge kind is {self.kind.value}.",
                field="host_key",
            )

    @property
    def group_key(self) -> str:
        """Storage key of the containing group, scoped by host for local groups."""
        if self.host_key is not None:
            return f"{self.host_key.casefold()}|{self.group_sid.value}"
        return self.group_sid.value

    @property
    def member_key(self) -> str:
        """Storage key of the member.

        A member is only host-scoped when it is itself a local principal of that host;
        domain principals keep their global key even inside a local group.
        """
        if self.host_key is not None and self.member_sid.is_builtin:
            return f"{self.host_key.casefold()}|{self.member_sid.value}"
        return self.member_sid.value

    @property
    def identity_key(self) -> str:
        """Uniqueness of an edge: one row per (group, member, kind, host)."""
        return f"{self.group_key}->{self.member_key}|{self.kind.value}"

    @property
    def crosses_domain(self) -> bool | None:
        """True when group and member were issued by different domains.

        ``None`` when either side is not domain-relative (a BUILTIN or well-known SID), so
        that "unknown" is never reported as "no".
        """
        group_domain = self.group_sid.domain_sid
        member_domain = self.member_sid.domain_sid
        if group_domain is None or member_domain is None:
            return None
        return group_domain != member_domain
