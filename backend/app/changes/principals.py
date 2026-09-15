r"""Two judgments about a trustee that a change's severity depends on.

Severity is not a property of an ACE alone. *Everyone* gaining Modify and one named
contractor gaining Modify are the same edit with the same mask, and only one of them is a
finding worth waking somebody for. So the rules in :mod:`app.changes.rules` ask two
questions about the principal a change is about, and this module is the only place either
one is answered.

## Broad: a trustee whose membership nobody maintains

:func:`is_broad_trustee` is true for a SID whose members are *everybody who can reach the
machine*, rather than a list somebody curates. Granting one of these is granting the
estate, and it is the single most common way a share ends up world-readable.

The set is deliberately small and named one SID at a time. It is not "well-known SIDs":
``SYSTEM`` and ``CREATOR OWNER`` are well known and are not broad, and treating them as
broad would put a high severity on the two entries that appear on almost every ACL in
Windows — after which nobody reads the severity column.

``ANONYMOUS LOGON`` is in the set and is the only member that is *narrower* than
``Everyone`` while being worse: an unauthenticated caller. Its presence here is why the
predicate is named for what it is used for rather than for the size of the group.

## Privileged: a group whose members can rewrite the estate

:func:`is_privileged_group` is true for the groups whose membership is itself an
administrative control. A member added to ``Domain Admins`` is not a permission change on
any resource and is a bigger event than most permission changes on any resource, which is
exactly why membership severity cannot be read off an ACL.

Both sets contain **domain-relative RIDs** as well as absolute SIDs, because
``Domain Admins`` is ``<domain>-512`` and there is no constant for it. The RID is matched
only on a SID that is actually domain-relative — a four-sub-authority ``S-1-5-21-...`` — so
a well-known SID that happens to end in 512 cannot be mistaken for one.

## Keys, not SIDs, are what the caller holds

A change carries ``trustee_key`` and ``member_key``, which are ``<sid>`` for a globally
unique principal and ``<host>|<sid>`` for a BUILTIN SID scoped to the machine that reported
it (:func:`app.domain.identity.referenced_principal_key`). :func:`sid_of_key` undoes that,
and it is the only place the spelling is undone: a rule that split on ``|`` itself would be
a second implementation of a rule that already has exactly one.
"""

from __future__ import annotations

from typing import Final

from app.domain.identity import Sid

__all__ = [
    "BROAD_TRUSTEE_RIDS",
    "BROAD_TRUSTEE_SIDS",
    "PRIVILEGED_GROUP_RIDS",
    "PRIVILEGED_GROUP_SIDS",
    "is_broad_trustee",
    "is_privileged_group",
    "sid_of_key",
    "trustee_display",
]

BROAD_TRUSTEE_SIDS: Final[frozenset[str]] = frozenset(
    {
        "S-1-1-0",  # Everyone
        "S-1-5-7",  # ANONYMOUS LOGON - unauthenticated, and therefore worse than Everyone
        "S-1-5-11",  # Authenticated Users
        "S-1-5-15",  # This Organization - every account in the forest
        "S-1-5-32-545",  # BUILTIN\Users - every interactive account on the machine
        "S-1-5-32-546",  # BUILTIN\Guests
        "S-1-5-113",  # Local account
        "S-1-5-114",  # Local account and member of Administrators group
        "S-1-15-2-1",  # ALL APPLICATION PACKAGES
    }
)
"""SIDs whose membership is not a list anybody maintains.

Excluded on purpose, and each for a reason worth stating:

* ``SYSTEM`` (``S-1-5-18``) and ``CREATOR OWNER`` (``S-1-3-0``) appear on nearly every ACL
  Windows creates. Scoring them would make the severity column unreadable.
* ``NETWORK`` (``S-1-5-2``) and ``INTERACTIVE`` (``S-1-5-4``) describe *how* a session was
  established, not who established it. They are broad, and an ACE naming one is almost
  always part of a template rather than a grant somebody made.
* ``BUILTIN\\Administrators`` is privileged, not broad. It is in
  :data:`PRIVILEGED_GROUP_SIDS`.
"""

BROAD_TRUSTEE_RIDS: Final[frozenset[int]] = frozenset(
    {
        513,  # Domain Users - every account in the domain
        514,  # Domain Guests
        515,  # Domain Computers
    }
)
"""Domain-relative RIDs that name everybody of a kind. See the module docstring."""

PRIVILEGED_GROUP_SIDS: Final[frozenset[str]] = frozenset(
    {
        "S-1-5-32-544",  # BUILTIN\Administrators
        "S-1-5-32-547",  # BUILTIN\Power Users
        "S-1-5-32-551",  # BUILTIN\Backup Operators - reads every file, bypassing the DACL
    }
)
"""Local groups whose membership is an administrative control on the machine."""

PRIVILEGED_GROUP_RIDS: Final[frozenset[int]] = frozenset(
    {
        512,  # Domain Admins
        518,  # Schema Admins
        519,  # Enterprise Admins
        520,  # Group Policy Creator Owners
        526,  # Key Admins
        527,  # Enterprise Key Admins
    }
)
"""Domain-relative RIDs whose membership is an administrative control on the domain.

``Administrator`` (500) is a user, not a group, and is absent: nothing can be added to it.
"""


def sid_of_key(key: str | None) -> Sid | None:
    """The SID a principal or trustee key names, or ``None`` when it names none.

    Keys are ``<sid>`` or ``<host>|<sid>``; the SID is the last segment either way. Parsed
    rather than pattern-matched, so a key that is not a key at all — a value that reached
    ``related_key`` from a kind whose far end is not a principal — returns ``None`` instead
    of a string that looks like a SID to everything downstream.
    """
    if not key:
        return None
    candidate = key.rsplit("|", 1)[-1]
    try:
        return Sid.parse(candidate)
    except Exception:
        return None


def is_broad_trustee(key: str | None) -> bool:
    """Whether a grant to this trustee is effectively a grant to the estate."""
    sid = sid_of_key(key)
    if sid is None:
        return False
    if sid.value in BROAD_TRUSTEE_SIDS:
        return True
    return _domain_rid(sid) in BROAD_TRUSTEE_RIDS


def is_privileged_group(key: str | None) -> bool:
    """Whether membership of this group is itself an administrative control."""
    sid = sid_of_key(key)
    if sid is None:
        return False
    if sid.value in PRIVILEGED_GROUP_SIDS:
        return True
    return _domain_rid(sid) in PRIVILEGED_GROUP_RIDS


def trustee_display(key: str | None) -> str:
    """A name for a trustee in a rule's sentence, falling back to the key itself.

    The well-known name only: a rule's reason is written when the change is classified, and
    resolving a display name would make classification depend on a database read — after
    which the same two versions could classify differently depending on what else had been
    collected.
    """
    sid = sid_of_key(key)
    if sid is None:
        return key or "an unnamed trustee"
    return sid.well_known_name or sid.value


def _domain_rid(sid: Sid) -> int | None:
    """The RID of a domain-relative SID, or ``None`` for any other shape.

    Guarded on ``domain_sid`` rather than on the string, because only a SID that actually
    has a domain can have a domain-relative RID — and ``S-1-5-32-512``, were Microsoft ever
    to define it, must not be read as ``Domain Admins``.
    """
    if sid.domain_sid is None:
        return None
    return sid.rid
