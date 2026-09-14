"""Builders for the effective-access tests.

Everything here is a shorthand over the real constructors — nothing bypasses validation, so
a fixture that could not exist in the database cannot be built here either.

SIDs are the ones the canonical Phase 0B scenarios use, so a unit test and the fixture test
that replays the same shape are talking about the same estate.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from app.access_engine import (
    AccessPath,
    AclEntry,
    ResourceDacl,
    ShareDacl,
    SidOrigin,
    SubjectFacts,
    SubjectToken,
    TokenAssumption,
    TokenSid,
    build_token,
    ntfs_entry,
    share_entry,
)
from app.domain import AceFlag, AceSource, AceType, PrincipalKind, SharePermission

DOMAIN = "S-1-5-21-1004336348-1177238915-682003330"

ALICE = f"{DOMAIN}-1104"
BOB = f"{DOMAIN}-1105"
FINANCE_TEAM = f"{DOMAIN}-1201"
FINANCE_RW = f"{DOMAIN}-1202"
DOMAIN_USERS = f"{DOMAIN}-513"
ORPHAN = "S-1-5-21-999888777-666555444-333222111-1234"

EVERYONE = "S-1-1-0"
AUTHENTICATED_USERS = "S-1-5-11"
NETWORK = "S-1-5-2"
INTERACTIVE = "S-1-5-4"
CREATOR_OWNER = "S-1-3-0"
OWNER_RIGHTS = "S-1-3-4"
BUILTIN_ADMINS = "S-1-5-32-544"
FS01_ADMINS = f"fs01|{BUILTIN_ADMINS}"

FINANCE = "\\\\fs01\\finance"
REPORTS = "\\\\fs01\\finance\\reports"
PAYROLL = "\\\\fs01\\finance\\payroll"
FINANCE_SHARE = "fs01|finance"

READ_EXECUTE = 0x001200A9
MODIFY = 0x001301BF
FULL_CONTROL = 0x001F01FF
WRITE_DAC = 0x00040000
READ_CONTROL = 0x00020000
MAXIMUM_ALLOWED = 0x02000000


def user(key: str = ALICE, *, kind: PrincipalKind | None = PrincipalKind.USER) -> SubjectFacts:
    """A subject, defaulting to the user every canonical scenario asks about."""
    return SubjectFacts(key=key, sid=key.rpartition("|")[2], kind=kind, display_name=None)


def group_sid(key: str, *, depth: int = 1, path: tuple[str, ...] = ()) -> TokenSid:
    """One observed group membership, with the chain that reached it."""
    return TokenSid(
        key=key,
        sid=key.rpartition("|")[2],
        origin=SidOrigin.GROUP_MEMBERSHIP,
        depth=depth,
        path=path,
    )


def token(
    subject: SubjectFacts | str = ALICE,
    groups: Iterable[str | TokenSid] = (),
    *,
    path: AccessPath = AccessPath.REMOTE_SMB,
    assumption: TokenAssumption | None = TokenAssumption.SIDS_ONLY,
    membership_complete: bool = True,
) -> SubjectToken:
    """A token, defaulting to ``SIDS_ONLY`` so a test opts in to the assumed SIDs.

    The default is deliberately not the production one: most of these tests are about what
    one ACE does, and silently adding ``Everyone`` to every token would make an assertion
    about a ``Everyone`` entry pass for the wrong reason.
    """
    facts = subject if isinstance(subject, SubjectFacts) else user(subject)
    entries = [
        item if isinstance(item, TokenSid) else group_sid(item, depth=1, path=(facts.key, item))
        for item in groups
    ]
    return build_token(
        facts,
        entries,
        access_path=path,
        assumption=assumption,
        membership_complete=membership_complete,
    )


def entry_of(subject_token: SubjectToken, key: str) -> TokenSid:
    """The token entry for a key the test has just put there.

    ``SubjectToken.entry`` answers ``None`` for a key the token does not carry, which is
    the right shape for production code and makes every assertion here an optional-access
    error. This narrows it once, with a message that names the key when the assumption is
    wrong.
    """
    found = subject_token.entry(key)
    assert found is not None, f"{key} is not in this token: {sorted(subject_token.keys)}"
    return found


def allow(
    trustee: str,
    mask: int = MODIFY,
    *,
    flags: int = 0x00,
    inherited: bool = False,
    order: int | None = None,
    key: str | None = None,
) -> AclEntry:
    """An Allow entry on a file-system DACL."""
    return _ntfs(trustee, AceType.ALLOW, mask, flags, inherited, order, key)


def deny(
    trustee: str,
    mask: int = MODIFY,
    *,
    flags: int = 0x00,
    inherited: bool = False,
    order: int | None = None,
    key: str | None = None,
) -> AclEntry:
    """A Deny entry on a file-system DACL."""
    return _ntfs(trustee, AceType.DENY, mask, flags, inherited, order, key)


def _ntfs(
    trustee: str,
    ace_type: AceType,
    mask: int,
    flags: int,
    inherited: bool,
    order: int | None,
    key: str | None,
) -> AclEntry:
    resolved_flags = flags | (int(AceFlag.INHERITED) if inherited else 0)
    return ntfs_entry(
        trustee_key=trustee,
        trustee_sid=trustee.rpartition("|")[2],
        ace_type=ace_type,
        access_mask=mask,
        flags=resolved_flags,
        source=AceSource.INHERITED if inherited else AceSource.EXPLICIT,
        order_index=order,
        ace_key=key,
    )


def share_allow(
    trustee: str,
    permission: SharePermission = SharePermission.FULL,
    *,
    mask: int | None = None,
    order: int | None = None,
) -> AclEntry:
    return _share(trustee, AceType.ALLOW, permission, mask, order)


def share_deny(
    trustee: str,
    permission: SharePermission = SharePermission.FULL,
    *,
    mask: int | None = None,
    order: int | None = None,
) -> AclEntry:
    return _share(trustee, AceType.DENY, permission, mask, order)


def _share(
    trustee: str,
    ace_type: AceType,
    permission: SharePermission | None,
    mask: int | None,
    order: int | None,
) -> AclEntry:
    return share_entry(
        trustee_key=trustee,
        trustee_sid=trustee.rpartition("|")[2],
        ace_type=ace_type,
        access_mask=mask,
        permission=None if mask is not None else permission,
        order_index=order,
    )


def dacl(*entries: AclEntry, key: str = FINANCE, **facts: object) -> ResourceDacl:
    """A resource whose DACL is exactly these entries, in this order."""
    numbered = tuple(
        entry if entry.order_index is not None else _at(entry, position)
        for position, entry in enumerate(entries)
    )
    return ResourceDacl(resource_key=key, entries=numbered, **facts)  # type: ignore[arg-type]


def _at(entry: AclEntry, position: int) -> AclEntry:
    """Number an entry by its position, so a DACL built here has an evaluation order."""
    return replace(entry, order_index=position)


def share_acl(*entries: AclEntry, key: str = FINANCE_SHARE, observed: bool = True) -> ShareDacl:
    """A share ACL, or an unread one when no entries are given and ``observed`` is False."""
    if not observed:
        return ShareDacl(share_key=key, observed=False)
    numbered = tuple(
        entry if entry.order_index is not None else _at(entry, position)
        for position, entry in enumerate(entries)
    )
    return ShareDacl(share_key=key, entries=numbered, observed=True)


def unread_share(key: str = FINANCE_SHARE) -> ShareDacl:
    """A share nobody has read. Not the same as one that grants nothing."""
    return ShareDacl(share_key=key, observed=False)
