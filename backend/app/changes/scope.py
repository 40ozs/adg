r"""What "changes on this share" means, kind by kind.

A change feed is only useful if it can be pointed at something. The awkward part is that
"this share" means a different predicate for each of the five kinds of object that can sit
under a share, and none of them is ``share_key = ...``:

============== =============================================================
kind           how a share scope selects it
============== =============================================================
``smb_share``  its own key
``smb_ace``    ``container_key`` — the ACE's share
``ntfs_resource`` ``container_key`` — the directory's share
``ntfs_ace``   a **prefix** of ``container_key``, because an NTFS ACE's container is a
               directory and a directory's key is its UNC path
others         not selected at all
============== =============================================================

So this module is a rule table, in the same shape and for the same reasons as
:mod:`app.history.closure`: a scope maps to one predicate per kind, and **a kind with no
entry is excluded**. The alternative — a scope that filters the kinds it understands and
lets the rest through — produces a "changes on \\\\fs01\\finance" page containing a domain
group's display name, which teaches the reader that the filter does not work.

## Prefixes are ``starts_with``, never ``LIKE``

The same rule :class:`app.history.closure.DirectorySubtree` states, and it is worth stating
twice because getting it wrong is silent. PostgreSQL's default ``LIKE`` escape character is
a backslash, which is the separator in every UNC path, and ``_`` is a ``LIKE`` wildcard and
an ordinary character in Windows directory names. A pattern built from a real path would
both mis-escape and over-match.

Every prefix here also ends in a separator and is paired with an equality on the key
itself. Without the separator, ``\\\\fs01\\finance`` selects ``\\\\fs01\\finance-archive``,
which is a different share; without the equality, the tree's own root is missing from its
own scope.

## The window is the driving predicate, and that is a design decision

Every query this module contributes to is bounded first by ``valid_from`` inside the
requested interval, served by ``ix_object_versions_opened_at``. The scope predicates are
filters applied to what that returns. It is why a prefix test costs nothing here and would
cost a great deal in a query shaped the other way round, and it is why the feed refuses an
unbounded window (:data:`app.changes.service.MAX_UNSCOPED_WINDOW`) rather than offering one that
would degrade into a full scan of the timeline.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import ColumnElement, func, or_

from app.contracts.v1.common import ObservationKind
from app.domain.errors import DomainValidationError
from app.domain.identity import Sid
from app.domain.paths import parse_unc_path
from app.models.schema import object_versions

__all__ = [
    "ChangeScope",
    "ScopeTarget",
    "predicates_for",
    "selected_kinds",
]


class ScopeTarget(StrEnum):
    """The five things a change feed can be pointed at."""

    SERVER = "server"
    """One file server: its shares, their ACLs, its directories, their ACLs, and the local
    principals and local group memberships that machine issues."""

    SHARE = "share"
    """One share: its own record, its share ACL, and every directory and NTFS ACE beneath
    it."""

    DIRECTORY_TREE = "directory_tree"
    """One directory and everything under it."""

    PRINCIPAL = "principal"
    """One SID, everywhere it appears: as itself, as a member, as a group, and as a trustee
    on either ACL layer. This is the "what happened to Alice" question."""

    GROUP = "group"
    """One group's membership, and the group's own record. Narrower than ``principal`` on
    purpose: "who joined or left this group" is a different question from "what happened to
    this principal", and a filter that answered both at once would answer neither."""


@dataclass(frozen=True, slots=True)
class ChangeScope:
    """A target and the key that names it."""

    target: ScopeTarget
    key: str

    def __post_init__(self) -> None:
        if not self.key or not self.key.strip():
            raise DomainValidationError(
                f"A {self.target.value} scope needs a key. An empty one would select the "
                "whole estate while looking like a filter.",
                field="key",
            )


_Predicate = Callable[[str], ColumnElement[bool]]

_KEY = object_versions.c.object_key
_CONTAINER = object_versions.c.container_key
_RELATED = object_versions.c.related_key


# Every comparison below is written column-first and carries `noqa: SIM300`. The rule reads
# `column == value` as a Yoda condition and would have it the other way round, which is
# wrong here in a way that is not a style preference: `value == column` reaches SQLAlchemy
# only through `str.__eq__` returning `NotImplemented` and Python falling back to the
# reflected operand. It builds the same SQL and it types as `bool`, so mypy stops seeing a
# predicate at all -- and a helper annotated `ColumnElement[bool]` that mypy believes
# returns `bool` is a scope filter nothing is checking.


def _key_is(value: str) -> ColumnElement[bool]:
    return _KEY == value  # noqa: SIM300


def _container_is(value: str) -> ColumnElement[bool]:
    return _CONTAINER == value  # noqa: SIM300


def _related_is(value: str) -> ColumnElement[bool]:
    return _RELATED == value  # noqa: SIM300


def _member_or_group_is(value: str) -> ColumnElement[bool]:
    """Either end of a membership edge.

    Both, because a principal scope asks "what happened to this principal" and a principal
    is on the far end of the edges that put it into groups and on the near end of the edges
    that put members into it. Answering only one direction would hide half of every
    membership change about a group.
    """
    return or_(_CONTAINER == value, _RELATED == value)  # noqa: SIM300


def _under(column: ColumnElement[Any], root: str, separator: str) -> ColumnElement[bool]:
    """``column`` is ``root``, or lies beneath it.

    ``starts_with`` and not ``LIKE``; see the module docstring. The separator is passed in
    rather than assumed, because the two key namespaces this is used on spell containment
    differently: a UNC path joins with a backslash and a storage key joins with a pipe.
    """
    return or_(column == root, _beneath(column, root, separator))


def _beneath(column: ColumnElement[Any], root: str, separator: str) -> ColumnElement[bool]:
    """``column`` lies strictly beneath ``root``, which is never equal to it.

    Separate from :func:`_under` rather than a flag on it, because the two cases are
    genuinely different and mixing them is how a scope grows a branch that can never be
    true. A share ACE's key begins with its server's key and is never equal to it; a
    directory's key can be the tree root itself. A predicate carrying an equality branch
    that no row can satisfy is not wrong -- it is a claim nobody will ever check, which is
    worse.
    """
    return func.starts_with(column, f"{root}{separator}")


def _unc_of_share(share_key: str) -> str:
    r"""``fs01|finance`` -> ``\\fs01\finance``.

    An NTFS ACE's container is a directory, and a directory's key is its UNC path, so a
    share scope reaches NTFS ACEs only through the path spelling of the share. Built with
    :func:`app.domain.paths.parse_unc_path` rather than by string joining, so the one
    canonicalization the rest of the product uses is the one used here.
    """
    server, _, share = share_key.partition("|")
    if not server or not share:
        raise DomainValidationError(
            f"{share_key!r} is not a share key. A share key is <server>|<share>, which is "
            "how every share row in the database is spelled; anything else would select "
            "nothing and look like a share with no changes.",
            value=share_key,
            field="key",
        )
    return parse_unc_path(f"\\\\{server}\\{share}").comparison_key


#: One predicate builder per (target, kind). A missing pair is a kind the scope excludes.
_RULES: Final[dict[ScopeTarget, dict[ObservationKind, _Predicate]]] = {
    ScopeTarget.SERVER: {
        ObservationKind.SERVER: _key_is,
        ObservationKind.SMB_SHARE: _container_is,
        # A share ACE's key begins with its share key, which begins with the server.
        ObservationKind.SMB_ACE: lambda key: _beneath(_KEY, key, "|"),
        # ix_object_versions_related: an NTFS resource's far end is the server it sits on.
        ObservationKind.NTFS_RESOURCE: _related_is,
        ObservationKind.NTFS_ACE: lambda key: func.starts_with(_CONTAINER, f"\\\\{key}\\"),
        # Local principals and local groups are host-scoped; domain ones have no container
        # and are correctly excluded from a server scope.
        ObservationKind.PRINCIPAL: _container_is,
        ObservationKind.MEMBERSHIP_EDGE: lambda key: _beneath(_CONTAINER, key, "|"),
    },
    ScopeTarget.SHARE: {
        ObservationKind.SMB_SHARE: _key_is,
        ObservationKind.SMB_ACE: _container_is,
        ObservationKind.NTFS_RESOURCE: _container_is,
        ObservationKind.NTFS_ACE: lambda key: _under(_CONTAINER, _unc_of_share(key), "\\"),
    },
    ScopeTarget.DIRECTORY_TREE: {
        ObservationKind.NTFS_RESOURCE: lambda key: _under(_KEY, key, "\\"),
        ObservationKind.NTFS_ACE: lambda key: _under(_CONTAINER, key, "\\"),
    },
    ScopeTarget.PRINCIPAL: {
        ObservationKind.PRINCIPAL: _key_is,
        ObservationKind.MEMBERSHIP_EDGE: _member_or_group_is,
        ObservationKind.SMB_ACE: _related_is,
        ObservationKind.NTFS_ACE: _related_is,
    },
    ScopeTarget.GROUP: {
        ObservationKind.PRINCIPAL: _key_is,
        ObservationKind.MEMBERSHIP_EDGE: _container_is,
    },
}


def selected_kinds(scope: ChangeScope) -> frozenset[ObservationKind]:
    """The kinds this scope can select. Every other kind is excluded outright."""
    return frozenset(_RULES[scope.target])


def predicates_for(scope: ChangeScope) -> dict[ObservationKind, ColumnElement[bool]]:
    """The predicate that selects each kind inside ``scope``.

    The caller ORs these together under their own ``object_kind`` equality, so a single
    query answers for the whole scope and a kind with no rule contributes no branch — which
    is what makes exclusion the default rather than something each caller has to remember.
    """
    key = _normalize(scope)
    return {kind: build(key) for kind, build in _RULES[scope.target].items()}


def _normalize(scope: ChangeScope) -> str:
    r"""The key in the spelling the stored columns use.

    A scope key typed by a human — a server name from a ticket, a path pasted from
    Explorer, a SID from a script — is not necessarily spelled the way the row is, and a
    mis-spelled key matches nothing while looking exactly like a scope with no changes in
    it. So each target normalizes the way the identity it names is derived, and **not one
    of the three rules is "lower-case it"**:

    * A server or share key is derived from an ``identity_key`` that case-folds, so it is
      case-folded here.
    * A directory tree goes through :func:`app.domain.paths.parse_unc_path`, so
      ``\\FS01\Finance\``, ``//fs01/finance`` and ``\\fs01\finance`` are one scope.
    * **A principal key is not case-folded and must not be.** It is ``<sid>``, or
      ``<case-folded host>|<sid>`` for a BUILTIN SID, and the SID half is canonical —
      upper-case ``S-1-5-32-544``. Folding the whole key would produce
      ``s-1-5-32-544``, which matches no row ADG has ever stored.
    """
    if scope.target is ScopeTarget.DIRECTORY_TREE:
        return parse_unc_path(scope.key).comparison_key
    if scope.target in (ScopeTarget.PRINCIPAL, ScopeTarget.GROUP):
        return _principal_key(scope.key)
    return scope.key.casefold()


def _principal_key(raw: str) -> str:
    """A principal or trustee key, spelled as :func:`referenced_principal_key` spells it.

    Raises:
        DomainValidationError: the value is not a SID, with or without a host prefix. A
            scope key that is not an identity selects nothing, and reporting that as an
            empty change list would be indistinguishable from a quiet week.
    """
    host, separator, candidate = raw.rpartition("|")
    try:
        sid = Sid.parse(candidate)
    except DomainValidationError as exc:
        raise DomainValidationError(
            f"{raw!r} does not name a principal. A principal scope key is a SID, "
            "optionally prefixed with the host that scopes a BUILTIN SID "
            "(fs01|S-1-5-32-544).",
            value=raw,
            field="key",
        ) from exc
    return f"{host.casefold()}{separator}{sid.value}" if separator else sid.value
