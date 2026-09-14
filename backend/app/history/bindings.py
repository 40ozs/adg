"""Which current-state table each historically tracked kind belongs to, and how its keys
are spelled.

One mapping, used by three things that must agree: the writer (which projects a row into a
version), the closure pass (which selects candidates for absence out of the current-state
table), and the point-in-time readers (which reconstruct a record from a version's state).
Three separate literal lists of column names would eventually disagree by one, and the
symptom would be an object kind that silently stops being tracked.

``container_key`` and ``related_key`` are **projections of the state**, not new facts. They
are copied out of the same row dictionary the current-state upsert writes, so a version's
indexed columns cannot describe a different object from its own state blob. They exist only
so that "everything inside X, as of T" and "everything pointing at Y, as of T" are indexed
reads; nothing is stored in them that is not already in ``state``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import Table

from app.contracts.v1.common import ObservationKind
from app.models.schema import (
    membership_edges,
    ntfs_aces,
    ntfs_resources,
    principals,
    servers,
    smb_share_aces,
    smb_shares,
)

__all__ = ["BINDINGS", "KindBinding", "binding_for"]


@dataclass(frozen=True, slots=True)
class KindBinding:
    """How one object kind maps onto storage."""

    kind: ObservationKind
    table: Table
    """The current-state table. Its primary key is ``key_column``."""

    key_column: str
    container_column: str | None
    """The column naming the object that contains this one, or ``None`` when nothing does."""

    related_column: str | None
    """The column naming the far end of the relation this object expresses, or ``None``."""


BINDINGS: Final[dict[ObservationKind, KindBinding]] = {
    # A local group's host contains it: S-1-5-32-544 means a different group on every
    # machine, and the host is what separates them. The domain SID is the far end because
    # "which principals belonged to this domain as of T" is the question a domain-scoped
    # reconciliation is judged by.
    ObservationKind.PRINCIPAL: KindBinding(
        ObservationKind.PRINCIPAL, principals, "principal_key", "host_key", "domain_sid"
    ),
    # An edge belongs to its group -- that is the enumeration a collector performs -- and
    # points at its member. Both directions are indexed because membership is walked both
    # ways: "who was in this group" and "which groups did this principal reach".
    ObservationKind.MEMBERSHIP_EDGE: KindBinding(
        ObservationKind.MEMBERSHIP_EDGE, membership_edges, "edge_key", "group_key", "member_key"
    ),
    ObservationKind.SERVER: KindBinding(ObservationKind.SERVER, servers, "server_key", None, None),
    ObservationKind.SMB_SHARE: KindBinding(
        ObservationKind.SMB_SHARE, smb_shares, "share_key", "server_key", None
    ),
    ObservationKind.SMB_ACE: KindBinding(
        ObservationKind.SMB_ACE, smb_share_aces, "ace_key", "share_key", "trustee_key"
    ),
    ObservationKind.NTFS_RESOURCE: KindBinding(
        ObservationKind.NTFS_RESOURCE, ntfs_resources, "resource_key", "share_key", "server_key"
    ),
    ObservationKind.NTFS_ACE: KindBinding(
        ObservationKind.NTFS_ACE, ntfs_aces, "ace_key", "resource_key", "trustee_key"
    ),
}


def binding_for(kind: ObservationKind) -> KindBinding:
    """The binding for ``kind``.

    Raises:
        KeyError: the kind has no binding, which means a contract kind was added without
            giving it a place in history. Left as a hard failure rather than a default,
            because the alternative is an object kind that is stored, queried, and has no
            timeline -- a gap with no symptom.
    """
    return BINDINGS[kind]
