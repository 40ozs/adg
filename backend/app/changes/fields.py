r"""What each stored field means when it moves, and what contains what.

Two tables. Both are exhaustive over something the database defines, and both are checked
against it by a test rather than by anybody remembering — which is the same arrangement
:mod:`app.history.bindings` and ``tests/history/test_repository_coverage.py`` already use,
for the same reason: the failure mode of an out-of-date table here is silent.

## The field table

:data:`FIELD_SIGNIFICANCE` classifies every column of every historically tracked kind. A
column absent from it is a column ADG has no opinion about, and the opinion it would
otherwise get by default — "not security relevant" — is the single most dangerous default
this product could have. So the default is refusal:
:func:`significance_of` returns :attr:`FieldSignificance.NOISE` **only** for names the table
names, and ``tests/changes/test_fields.py`` asserts the table covers every column of every
bound table. Add a column to ``ntfs_aces`` and the suite fails until somebody says what a
change to it means.

Four classifications are worth their own sentence, because they are the ones that are not
obvious:

* ``principals.enabled`` is **security**. A disabled account cannot authenticate, so every
  grant naming it is inert; re-enabling it activates all of them at once, and nothing in
  any ACL changed.
* ``principals.group_type`` is **security**. A distribution group is not a security
  principal: it appears in no token and grants nothing. Converting one to a security group
  turns every ACE that names it live, and again no ACL moved.
* ``smb_shares.local_path`` is **security**. The share still exists, still has the same
  share ACL, and now publishes a different directory — so the entire NTFS half of every
  answer about it changed without a single NTFS row moving.
* ``ntfs_resources.acl_hash`` is **security**, and it is the one field that is a *witness*
  rather than a cause. It is the collector's digest of the normalized DACL it read. If it
  moves and no ACE of that resource moved, the collector saw an ACL change ADG did not
  store — which is under-reporting, and a finding.

``order_index`` is classified :attr:`FieldSignificance.ORDER` and deliberately not resolved
here. Whether a position change matters depends on the *other* entries in the same ACL —
an ACE renumbered because an audit entry above it was deleted has moved nothing, and one
that swapped with a Deny has changed what Windows grants. The field table cannot see the
other entries, so it declines instead of guessing; :mod:`app.changes.correlation` decides it
by comparing the normalized ACL at both ends.

## The container table

:data:`CONTAINER_KINDS` says which kind contains which. It is what separates
:attr:`app.changes.model.ChangeAction.ADDED` from
:attr:`app.changes.model.ChangeAction.FIRST_OBSERVED`: an ACE that appears on a directory
ADG has been reading for months was **added**, and the same ACE appearing on a directory
nobody had read before is simply the first time ADG looked. The object itself cannot tell
those apart. Its container can.

Two kinds have no container and say so rather than being given a plausible one. A
``server`` is a root. A domain ``principal`` is contained by a domain, and a domain is not
an object ADG stores — deriving one from ``domain_sid`` would make the parent an attribute
of the child, which is how a container test comes to answer "yes" for every object that
exists. A local principal *is* contained by its host, and gets the honest answer through
``container_key``, which the binding already fills with ``host_key``.
"""

from __future__ import annotations

from typing import Final

from app.changes.model import FieldSignificance
from app.contracts.v1.common import ObservationKind
from app.history.bindings import BINDINGS
from app.history.model import REDUNDANT_FIELDS

__all__ = [
    "CONTAINER_KINDS",
    "FIELD_SIGNIFICANCE",
    "container_kind_of",
    "significance_of",
    "state_columns_of",
]

_S = FieldSignificance

#: Every column of every tracked kind, and what a change to it means.
#:
#: Ordered as the table stores them, so a reader can compare this list against
#: ``app/models/schema.py`` line by line.
FIELD_SIGNIFICANCE: Final[dict[ObservationKind, dict[str, FieldSignificance]]] = {
    ObservationKind.PRINCIPAL: {
        # principal_key = (sid, kind, host). All three are the identity.
        "principal_key": _S.IDENTITY,
        "sid": _S.IDENTITY,
        "principal_kind": _S.IDENTITY,
        "host_key": _S.IDENTITY,
        # Derived from the SID by string surgery; it cannot move without the SID moving.
        "domain_sid": _S.DERIVED,
        "display_name": _S.METADATA,
        "sam_account_name": _S.METADATA,
        "user_principal_name": _S.METADATA,
        # An OU move. ADG models no delegation or GPO, so it has no access consequence
        # *here*; it very often has one in the estate, which is why it is metadata rather
        # than noise and appears in the diff.
        "distinguished_name": _S.METADATA,
        # Domain local / global / universal. Widening a scope widens where the group can be
        # used, and a universal group's membership is replicated to every domain.
        "group_scope": _S.SECURITY,
        # Distribution or security. A distribution group is in no token and grants nothing.
        "group_type": _S.SECURITY,
        "enabled": _S.SECURITY,
        "is_deleted": _S.SECURITY,
        # Why a SID could not be resolved to an account. A fact about collection.
        "unresolved_reason": _S.METADATA,
        "last_known_name": _S.METADATA,
        "source_key": _S.NOISE,
    },
    ObservationKind.MEMBERSHIP_EDGE: {
        # edge_key = (group, member, edge kind, host). Everything below is that tuple.
        "edge_key": _S.IDENTITY,
        "group_key": _S.IDENTITY,
        "member_key": _S.IDENTITY,
        "group_sid": _S.IDENTITY,
        "member_sid": _S.IDENTITY,
        "edge_kind": _S.IDENTITY,
        "host_key": _S.IDENTITY,
        # What the far end is, as this collector saw it. The edge grants the same thing
        # either way; a user that has become a group is a change to the principal.
        "member_kind": _S.METADATA,
        "is_foreign_security_principal": _S.METADATA,
        "source_key": _S.NOISE,
    },
    ObservationKind.SERVER: {
        "server_key": _S.IDENTITY,
        "name": _S.IDENTITY,
        "dns_host_name": _S.METADATA,
        "netbios_name": _S.METADATA,
        # The machine's own SID namespace. When it moves, the machine was rebuilt or
        # renamed at the SID level, and every local SID recorded against it now names a
        # different account than it did.
        "computer_sid": _S.SECURITY,
        "domain_sid": _S.SECURITY,
        # Joining or leaving a domain changes which authority can authenticate against it.
        "is_domain_member": _S.SECURITY,
        "operating_system": _S.METADATA,
        "source_key": _S.NOISE,
    },
    ObservationKind.SMB_SHARE: {
        "share_key": _S.IDENTITY,
        "server_key": _S.IDENTITY,
        "name": _S.IDENTITY,
        # The directory this share publishes. See the module docstring.
        "local_path": _S.SECURITY,
        # Disk, print, IPC. What is exposed at all.
        "share_type": _S.SECURITY,
        "description": _S.METADATA,
        "concurrent_user_limit": _S.METADATA,
        # Offline availability. It governs caching on clients, not who may read the share.
        "caching_mode": _S.METADATA,
        "is_special": _S.METADATA,
        "source_key": _S.NOISE,
    },
    ObservationKind.SMB_ACE: {
        # ace_key = (share, trustee, type, right token). The right is part of the identity,
        # so a share ACE cannot be edited in place: tightening one removes a row and adds
        # another. app.changes.correlation is what pairs those back together.
        "ace_key": _S.IDENTITY,
        "share_key": _S.IDENTITY,
        "trustee_sid": _S.IDENTITY,
        "trustee_key": _S.IDENTITY,
        "ace_type": _S.IDENTITY,
        "access_mask": _S.IDENTITY,
        "permission": _S.IDENTITY,
        "right_token": _S.IDENTITY,
        "order_index": _S.ORDER,
        "source_key": _S.NOISE,
    },
    ObservationKind.NTFS_RESOURCE: {
        "resource_key": _S.IDENTITY,
        "path": _S.IDENTITY,
        "server_key": _S.IDENTITY,
        "share_key": _S.IDENTITY,
        # The server-local spelling of the same directory.
        "local_path": _S.METADATA,
        # The owner holds READ_CONTROL and WRITE_DAC implicitly, whatever the DACL says.
        "owner_sid": _S.SECURITY,
        # The primary group. Windows access checks do not consult it.
        "group_sid": _S.METADATA,
        # false is a NULL DACL: everyone has full access.
        "dacl_present": _S.SECURITY,
        # SE_DACL_PROTECTED. Blocking or unblocking inheritance changes which entries apply.
        "dacl_protected": _S.SECURITY,
        "inheritance_enabled": _S.SECURITY,
        # ADG's verdict, recomputed from dacl_protected and the parent comparison.
        "is_acl_boundary": _S.DERIVED,
        "boundary_reason": _S.DERIVED,
        # The descriptor's own count. It moves because entries moved.
        "ace_count": _S.DERIVED,
        "depth_from_share_root": _S.DERIVED,
        "resource_kind": _S.METADATA,
        # The collector's digest of the normalized DACL. A witness, not a cause.
        "acl_hash": _S.SECURITY,
        # The parent's digest as this run read it. Evidence about which reading was
        # compared, not about this resource's own permissions.
        "parent_acl_hash": _S.METADATA,
        "source_key": _S.NOISE,
    },
    ObservationKind.NTFS_ACE: {
        # ace_key = (resource, trustee, type, mask, flags). As with a share ACE, the grant
        # is part of the identity and an edit is a remove plus an add.
        "ace_key": _S.IDENTITY,
        "resource_key": _S.IDENTITY,
        "trustee_sid": _S.IDENTITY,
        "trustee_key": _S.IDENTITY,
        "ace_type": _S.IDENTITY,
        "access_mask": _S.IDENTITY,
        "ace_flags": _S.IDENTITY,
        # Explicit or inherited. The same bits either way, but an entry that has become
        # explicit no longer follows its parent — somebody broke inheritance and baked it
        # in, and the next parent edit will not reach it.
        "source": _S.SECURITY,
        # Windows's account of which ancestor an inherited entry came from.
        "inherited_from": _S.METADATA,
        "order_index": _S.ORDER,
        "source_key": _S.NOISE,
    },
}


#: Which kind contains which, for the one question the object itself cannot answer.
#:
#: ``None`` is a real answer and not a gap: see the module docstring.
CONTAINER_KINDS: Final[dict[ObservationKind, ObservationKind | None]] = {
    # A local group's container is the host that issued its SID; a domain principal has
    # container_key NULL and therefore no container, which is the honest answer.
    ObservationKind.PRINCIPAL: ObservationKind.SERVER,
    # An edge belongs to the group whose membership was enumerated. So a member appearing
    # in a group ADG has read before is an addition, which is exactly the finding wanted.
    ObservationKind.MEMBERSHIP_EDGE: ObservationKind.PRINCIPAL,
    ObservationKind.SERVER: None,
    ObservationKind.SMB_SHARE: ObservationKind.SERVER,
    ObservationKind.SMB_ACE: ObservationKind.SMB_SHARE,
    ObservationKind.NTFS_RESOURCE: ObservationKind.SMB_SHARE,
    ObservationKind.NTFS_ACE: ObservationKind.NTFS_RESOURCE,
}


#: The kind at the far end of the relation an object expresses, where there is one.
#:
#: Read straight off the bindings' ``related_column`` rather than restated, so the two
#: cannot come apart. An ACE's far end is a principal; a membership edge's far end is a
#: principal; an NTFS resource's is the server it sits on.
RELATED_KINDS: Final[dict[ObservationKind, ObservationKind | None]] = {
    ObservationKind.PRINCIPAL: None,
    ObservationKind.MEMBERSHIP_EDGE: ObservationKind.PRINCIPAL,
    ObservationKind.SERVER: None,
    ObservationKind.SMB_SHARE: None,
    ObservationKind.SMB_ACE: ObservationKind.PRINCIPAL,
    ObservationKind.NTFS_RESOURCE: ObservationKind.SERVER,
    ObservationKind.NTFS_ACE: ObservationKind.PRINCIPAL,
}


def significance_of(kind: ObservationKind, name: str) -> FieldSignificance | None:
    """What a change to ``name`` on ``kind`` means, or ``None`` when the table is silent.

    ``None`` rather than a default. A caller has to handle "ADG has no opinion" explicitly,
    and :func:`app.changes.classify.classify` turns it into
    :attr:`app.changes.model.ChangeSignificance.UNDETERMINED` — a change an operator sees —
    instead of a change that quietly scores as harmless.
    """
    return FIELD_SIGNIFICANCE[kind].get(name)


def container_kind_of(kind: ObservationKind) -> ObservationKind | None:
    """The kind of the object named by this kind's ``container_key``, if any."""
    return CONTAINER_KINDS[kind]


def related_kind_of(kind: ObservationKind) -> ObservationKind | None:
    """The kind of the object named by this kind's ``related_key``, if any."""
    return RELATED_KINDS[kind]


def state_columns_of(kind: ObservationKind) -> frozenset[str]:
    """Exactly the column names a version of ``kind`` can carry in its ``state`` blob.

    Derived from the binding's table minus the columns a version stores as its own interval
    (:data:`app.history.model.REDUNDANT_FIELDS`), which is the same subtraction
    :func:`app.history.model.stored_state` makes. Deriving it is what lets the exhaustiveness
    test be a real check rather than a comparison of two hand-written lists.
    """
    return frozenset(
        column.name
        for column in BINDINGS[kind].table.columns
        if column.name not in REDUNDANT_FIELDS
    )
