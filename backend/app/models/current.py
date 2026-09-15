r"""Current state: the stored tables, minus what a reconciled scan proved is gone.

``principals``, ``membership_edges``, ``servers``, ``smb_shares``, ``smb_share_aces``,
``ntfs_resources`` and ``ntfs_aces`` hold the latest state of every object a collector has
ever reported, and **nothing removes a row from them**. That is deliberate and it is not
going to change: an absent observation is not evidence of removal, ADG deletes no collected
fact, and the record of what was once true is the product. See
:mod:`app.history.writer`.

The consequence, before this module existed, was a divergence: after an authoritative
reconciliation proved a membership edge or an ACE gone, the point-in-time engine correctly
stopped counting it and the live engine went on counting it, so the same reconciled estate
answered *none* as of the recollection instant and *modify* right now. For an access-auditing
product that is not a nuance, it is a wrong answer about who can read a file.

This module is the one place that resolves it. Each ``current_*`` object below is the base
table with the reconciled-away rows filtered out, and it is a drop-in for the table: the
same column names, so the same ``select(...)``, the same ``.c.x`` and the same record
constructors work over it unchanged. **Query-side code names these and never the base
tables**; the base tables are for the writers, which must go on seeing every row.

## The rule, stated once

> An object is **currently present** unless ``object_versions`` holds an *open tombstone*
> for it — a version with this ``(object_kind, object_key)``, ``valid_to IS NULL`` and
> ``is_present`` false.

Three things follow from it, and all three are the reason it is written this way rather
than as ``EXISTS (an open version that is present)``:

* **Absence must be measured.** Only :meth:`app.history.writer.HistoryWriter.close_absent`
  writes a tombstone, and only a successful, authoritative, in-scope reconciliation reaches
  it (:mod:`app.history.closure`). A partial scan, a failed scan, an incremental run and a
  run that short-delivered its batches all close nothing, so none of them can take a row
  out of current state.
* **A gap in the record is not a deletion.** An object with no versions at all — a row that
  predates history, or one a test inserted directly — stays current. The alternative would
  make "nobody has looked" and "somebody looked and it was gone" render identically, which
  is the single failure the history model was built to avoid.
* **Reappearance needs no special case.** A later observation of a tombstoned object closes
  the tombstone and opens a present version (``revived``), and the object is current again
  the moment that happens, because there is no longer an open tombstone.

The predicate is one anti-join against a partial index that covers open tombstones only
(``ix_object_versions_open_absent``), so it costs an index probe per row and the index is
the size of the removals rather than of the estate. PostgreSQL pulls these subqueries up
into the enclosing statement, so ``select(current_principals).where(...)`` plans as the
same indexed lookup ``select(principals).where(...)`` did, with the anti-join added.

## What is deliberately *not* filtered here

``principal_aliases`` and ``principal_references`` are indexes over what has been observed,
not objects with a timeline of their own — no collector reports them and ``object_versions``
holds no kind for them. ``principal_references`` in particular is a *candidate* index for
"what could this principal possibly reach"; every candidate it offers is then evaluated
against the current ACL, which is filtered, so a stale reference costs an extra row that
correctly reports no access rather than an access that is not there.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import Subquery, Table, select

from app.contracts.v1.common import ObservationKind
from app.models.schema import (
    membership_edges,
    ntfs_aces,
    ntfs_resources,
    object_versions,
    principals,
    servers,
    smb_share_aces,
    smb_shares,
)

__all__ = [
    "CURRENT_STATE",
    "CURRENT_STATE_KEYS",
    "current_membership_edges",
    "current_ntfs_aces",
    "current_ntfs_resources",
    "current_principals",
    "current_servers",
    "current_smb_share_aces",
    "current_smb_shares",
    "currently_present",
]

CURRENT_STATE_KEYS: Final[dict[ObservationKind, tuple[Table, str]]] = {
    ObservationKind.PRINCIPAL: (principals, "principal_key"),
    ObservationKind.MEMBERSHIP_EDGE: (membership_edges, "edge_key"),
    ObservationKind.SERVER: (servers, "server_key"),
    ObservationKind.SMB_SHARE: (smb_shares, "share_key"),
    ObservationKind.SMB_ACE: (smb_share_aces, "ace_key"),
    ObservationKind.NTFS_RESOURCE: (ntfs_resources, "resource_key"),
    ObservationKind.NTFS_ACE: (ntfs_aces, "ace_key"),
}
"""Which table holds each tracked kind, and which column carries its ``object_key``.

The single statement of that mapping in the codebase: :data:`app.history.bindings.BINDINGS`
builds itself from this, so the writer, the closure pass, the point-in-time readers and the
current-state filter below cannot come to disagree about where a kind lives or how its keys
are spelled. A kind added to the contract and not to this mapping raises at import rather
than silently becoming an object with no history and no presence filter.
"""


def currently_present(kind: ObservationKind) -> Subquery:
    """The rows of ``kind``'s table that no open tombstone excludes.

    Returns a subquery with the base table's own column names, so it substitutes for the
    table at every read site without touching the query around it.
    """
    table, key_column = CURRENT_STATE_KEYS[kind]
    reconciled_away = (
        select(object_versions.c.id)
        .where(
            object_versions.c.object_kind == kind.value,
            object_versions.c.object_key == table.c[key_column],
            object_versions.c.valid_to.is_(None),
            object_versions.c.is_present.is_(False),
        )
        .exists()
    )
    return select(table).where(~reconciled_away).subquery(f"current_{table.name}")


current_principals: Final = currently_present(ObservationKind.PRINCIPAL)
current_membership_edges: Final = currently_present(ObservationKind.MEMBERSHIP_EDGE)
current_servers: Final = currently_present(ObservationKind.SERVER)
current_smb_shares: Final = currently_present(ObservationKind.SMB_SHARE)
current_smb_share_aces: Final = currently_present(ObservationKind.SMB_ACE)
current_ntfs_resources: Final = currently_present(ObservationKind.NTFS_RESOURCE)
current_ntfs_aces: Final = currently_present(ObservationKind.NTFS_ACE)

CURRENT_STATE: Final[dict[ObservationKind, Subquery]] = {
    ObservationKind.PRINCIPAL: current_principals,
    ObservationKind.MEMBERSHIP_EDGE: current_membership_edges,
    ObservationKind.SERVER: current_servers,
    ObservationKind.SMB_SHARE: current_smb_shares,
    ObservationKind.SMB_ACE: current_smb_share_aces,
    ObservationKind.NTFS_RESOURCE: current_ntfs_resources,
    ObservationKind.NTFS_ACE: current_ntfs_aces,
}
"""Every tracked kind's current-state source, by kind.

Present so that a caller holding a kind rather than a table — a test sweeping all seven, a
future generic reader — cannot reach one of them and miss another.
"""
