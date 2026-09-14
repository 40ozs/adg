"""Stable source keys.

Every observation carries a ``source_key``: the deterministic identity of the object it
describes, derived only from identifying fields. Two properties follow, and both are load
bearing:

* **Idempotency.** Ingestion is keyed on ``(run_id, source_key)``, so replaying a batch
  changes nothing.
* **Continuity.** The same object reported by a different collector, through a different
  API, or in next week's run produces the same key, which is what makes change detection
  possible at all.

The collector computes the key and the server recomputes it. A mismatch is rejected rather
than accepted, because a collector that derives keys differently would silently create a
second row for an object that already exists.

The derivations here are the normative ones; `docs/contracts/collector-protocol.md`
restates them for collector authors, and a test pins the two together.
"""

from __future__ import annotations

from app.domain import MembershipEdge, MembershipEdgeKind, PrincipalKind, Sid, parse_unc_path


def principal_key(sid: Sid, kind: PrincipalKind, host_key: str | None = None) -> str:
    """``principal|<sid>``, or ``principal|<host>|<sid>`` for a local group.

    Local groups are host-scoped because ``S-1-5-32-544`` is identical on every Windows
    computer: ``BUILTIN\\Administrators`` on FS01 and on FS02 are different groups.
    """
    if kind is PrincipalKind.LOCAL_GROUP:
        if not host_key:
            raise ValueError("A local group requires host_key to form a stable source key.")
        return f"principal|{host_key.casefold()}|{sid.value}"
    return f"principal|{sid.value}"


def membership_key(
    group_sid: Sid,
    member_sid: Sid,
    edge_kind: MembershipEdgeKind,
    host_key: str | None = None,
) -> str:
    """``edge|<group_key>-><member_key>|<kind>``.

    Derived from :class:`app.domain.MembershipEdge` itself, so the contract key and the
    domain identity can never drift apart.
    """
    edge = MembershipEdge(
        group_sid=group_sid,
        member_sid=member_sid,
        kind=edge_kind,
        host_key=host_key,
    )
    return f"edge|{edge.identity_key}"


def server_key(name: str) -> str:
    """``server|<case-folded name>``."""
    return f"server|{name.casefold()}"


def share_key(server_name: str, share_name: str) -> str:
    """``share|<case-folded server>|<case-folded share>``."""
    return f"share|{server_name.casefold()}|{share_name.casefold()}"


def smb_ace_key(
    server_name: str,
    share_name: str,
    trustee_sid: Sid,
    ace_type: str,
    access_mask: int | None,
    permission: str | None,
) -> str:
    """``smb_ace|<server>|<share>|<trustee>|<type>|<permission or 0x-mask>``.

    The right form is part of the key because a share ACL reported as levels and the same
    ACL reported as masks are different observations of the same entry; keeping them
    distinct is honest, and the Phase 4 algebra reconciles them.
    """
    right = permission if permission is not None else f"0x{access_mask or 0:08x}"
    share = f"{server_name.casefold()}|{share_name.casefold()}"
    return f"smb_ace|{share}|{trustee_sid.value}|{ace_type}|{right}"


def ntfs_resource_key(path: str) -> str:
    """``resource|<case-folded canonical UNC path>``."""
    return f"resource|{parse_unc_path(path).comparison_key}"


def ntfs_ace_key(
    path: str,
    trustee_sid: Sid,
    ace_type: str,
    access_mask: int,
    ace_flags: int,
) -> str:
    """``ntfs_ace|<resource>|<trustee>|<type>|0x-mask|0x-flags``.

    Order index is deliberately absent: two ACEs identical in trustee, type, mask, and
    flags are duplicates of one another, and an ACL edit that reorders entries must not
    look like every ACE being deleted and recreated.
    """
    resource = parse_unc_path(path).comparison_key
    return (
        f"ntfs_ace|{resource}|{trustee_sid.value}|{ace_type}|0x{access_mask:08x}|0x{ace_flags:02x}"
    )
