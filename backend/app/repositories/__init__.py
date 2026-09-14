"""Query-side access to stored facts.

A repository turns rows into the small typed records the domain and API layers work with,
and nothing else: no permission logic, no traversal, no interpretation. The membership
repository additionally implements :class:`app.domain.AdjacencyProvider`, which is the
entire coupling between the graph algorithms and PostgreSQL — one batched query per
breadth-first level, and no SQL anywhere in the traversal itself.
"""

from __future__ import annotations

from app.repositories.membership import (
    EDGE_FETCH_CEILING,
    AliasRecord,
    DirectEdgeRecord,
    MembershipRepository,
    Page,
    PrincipalRecord,
    PrincipalResolution,
)
from app.repositories.resources import (
    ResourceRepository,
    ServerRecord,
    ShareAceRecord,
    ShareRecord,
    ShareReferenceRecord,
)

__all__ = [
    "EDGE_FETCH_CEILING",
    "AliasRecord",
    "DirectEdgeRecord",
    "MembershipRepository",
    "Page",
    "PrincipalRecord",
    "PrincipalResolution",
    "ResourceRepository",
    "ServerRecord",
    "ShareAceRecord",
    "ShareRecord",
    "ShareReferenceRecord",
]
