"""The set of SIDs an access check evaluates a principal's ACEs against.

Windows never checks an ACL against a user. It checks it against an **access token** — the
user's SID, every group the user belongs to, and a handful of SIDs the logon session
itself contributes. An ACE naming ``Authenticated Users`` grants Alice access without
naming Alice and without any membership edge existing anywhere in the directory, and a
resolver that matched ACEs only against collected group memberships would miss it.

ADG cannot read a token: it has never seen a logon. So this module does the next honest
thing — it *constructs* one from the identity graph plus an explicitly named set of
assumptions, and it records every SID it added and why. The assumption is part of the
answer, not a hidden default:

* what ADG observed — the subject's SID and its transitive group memberships, including
  the host-scoped local groups a BUILTIN SID resolves to on one server;
* what Windows guarantees for a session of this kind — ``Everyone`` in every token,
  ``Authenticated Users`` in every authenticated one;
* what the access path implies — ``NETWORK`` over SMB, ``INTERACTIVE`` at the console.

Anything beyond that is refused rather than guessed. A token also contains SIDs that
depend on how the session was established (``BATCH``, ``SERVICE``, ``REMOTE INTERACTIVE``),
and no amount of collected data settles which applied; an ACE naming one is reported as
:attr:`~app.access_engine.conditions.AccessCondition.LOGON_TYPE_TRUSTEE` rather than
quietly ignored.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.access_engine.conditions import AccessCondition, AccessFinding
from app.access_engine.rights import AccessPath
from app.domain.errors import DomainValidationError
from app.domain.identity import PrincipalKind, Sid

__all__ = [
    "ANONYMOUS_LOGON_SID",
    "AUTHENTICATED_USERS_SID",
    "CREATOR_GROUP_SID",
    "CREATOR_OWNER_SID",
    "EVERYONE_SID",
    "INTERACTIVE_SID",
    "LOGON_SESSION_SIDS",
    "NETWORK_SID",
    "OWNER_RIGHTS_SID",
    "SidOrigin",
    "SubjectFacts",
    "SubjectToken",
    "TokenAssumption",
    "TokenSid",
    "build_token",
    "default_assumption",
]

EVERYONE_SID: Final = "S-1-1-0"
AUTHENTICATED_USERS_SID: Final = "S-1-5-11"
ANONYMOUS_LOGON_SID: Final = "S-1-5-7"
NETWORK_SID: Final = "S-1-5-2"
INTERACTIVE_SID: Final = "S-1-5-4"

CREATOR_OWNER_SID: Final = "S-1-3-0"
CREATOR_GROUP_SID: Final = "S-1-3-1"
OWNER_RIGHTS_SID: Final = "S-1-3-4"
"""Trustees the access check treats specially. See :mod:`app.access_engine.evaluation`.

``CREATOR OWNER`` and ``CREATOR GROUP`` are placeholders Windows substitutes when it
materializes an inherited ACE; no token ever contains them. ``OWNER RIGHTS`` is not a
placeholder — Windows resolves it against the object's current owner at access time — which
is why it is handled by the evaluator rather than excluded here.
"""

LOGON_SESSION_SIDS: Final[frozenset[str]] = frozenset(
    {
        "S-1-5-1",  # DIALUP
        "S-1-5-2",  # NETWORK
        "S-1-5-3",  # BATCH
        "S-1-5-4",  # INTERACTIVE
        "S-1-5-6",  # SERVICE
        "S-1-5-13",  # TERMINAL SERVER USER
        "S-1-5-14",  # REMOTE INTERACTIVE LOGON
        "S-1-2-0",  # LOCAL
        "S-1-2-1",  # CONSOLE LOGON
        "S-1-5-64-10",  # NTLM Authentication
        "S-1-5-64-14",  # SChannel Authentication
        "S-1-5-64-21",  # Digest Authentication
        "S-1-5-113",  # Local account
        "S-1-5-114",  # Local account and member of Administrators group
    }
)
"""SIDs whose presence in a token is a property of the **session**, not of the principal.

ADG places exactly one of them in a token — the one the access path implies — and treats
an ACE naming any of the others as unevaluated rather than unmatched. The difference
matters: "Alice is not in this group" is a conclusion, and "Alice may or may not have
logged on that way" is not.
"""


class SidOrigin(StrEnum):
    """Why a SID is in the token. Every entry carries one, so nothing arrives unexplained."""

    SUBJECT = "subject"
    """The principal being asked about."""

    GROUP_MEMBERSHIP = "group_membership"
    """A group ADG observed the subject in, directly or through nesting."""

    WELL_KNOWN = "well_known"
    """Assumed: Windows places this SID in every token of the subject's kind."""

    LOGON_TYPE = "logon_type"
    """Assumed: the access path implies this logon-session SID."""


class TokenAssumption(StrEnum):
    """How much of a real token to reconstruct around the observed memberships.

    Named and reported rather than hard-coded, because the right answer differs by
    question. "What can this user reach" needs the SIDs an authenticated session carries;
    "what does an ACE naming Everyone actually grant" needs only the SID itself, and
    adding ``Authenticated Users`` to ``Everyone``'s token would answer a wider question
    than the one asked.
    """

    AUTHENTICATED_USER = "authenticated_user"
    """``Everyone`` plus ``Authenticated Users`` plus the access path's logon SID. What any
    domain account carries once it has authenticated, which is every ordinary case."""

    ANONYMOUS = "anonymous"
    """``Everyone`` and ``ANONYMOUS LOGON`` plus the logon SID, and no ``Authenticated
    Users``. Anonymous sessions are excluded from ``Everyone`` by default policy on modern
    Windows; ADG reports what the descriptor would grant and leaves the policy to the
    operator."""

    SIDS_ONLY = "sids_only"
    """The subject and its observed groups, and nothing assumed. The literal question."""


@dataclass(frozen=True, slots=True)
class TokenSid:
    """One SID in the constructed token, with the reason it is there.

    ``path`` is the membership chain from the subject to this group, so an explanation can
    show *Alice → Finance-Team → Finance-RW* rather than asserting that Alice is somehow in
    Finance-RW.
    """

    key: str
    """The storage key: a bare SID, or ``host|sid`` for a server's local group."""

    sid: str
    origin: SidOrigin
    depth: int = 0
    path: tuple[str, ...] = ()
    display_name: str | None = None

    def __post_init__(self) -> None:
        if not self.key:
            raise DomainValidationError("A token SID needs a storage key.", field="key")
        if self.depth < 0:
            raise DomainValidationError(
                f"depth must be non-negative; received {self.depth}.",
                value=self.depth,
                field="depth",
            )

    @property
    def is_assumed(self) -> bool:
        """Whether this SID came from an assumption rather than from an observation."""
        return self.origin in (SidOrigin.WELL_KNOWN, SidOrigin.LOGON_TYPE)

    @property
    def is_direct(self) -> bool:
        """The subject itself, or a group it is a direct member of."""
        return self.origin is SidOrigin.SUBJECT or self.depth == 1


@dataclass(frozen=True, slots=True)
class SubjectFacts:
    """What ADG knows about the principal being asked about.

    ``kind`` is ``None`` when no run has described the subject, which is a real state and
    not an error: a SID on an ACL that nothing resolves is the orphan finding, and asking
    what it can reach is a reasonable question.
    """

    key: str
    sid: str
    kind: PrincipalKind | None = None
    display_name: str | None = None

    def __post_init__(self) -> None:
        if not self.key:
            raise DomainValidationError("A subject needs a storage key.", field="key")
        Sid.parse(self.sid)

    @property
    def is_group(self) -> bool:
        return self.kind in (PrincipalKind.DOMAIN_GROUP, PrincipalKind.LOCAL_GROUP)

    @property
    def is_well_known(self) -> bool:
        return self.kind is PrincipalKind.WELL_KNOWN

    @property
    def resolved(self) -> bool:
        """Whether ADG knows what this SID refers to.

        :attr:`~app.domain.PrincipalKind.UNRESOLVED` counts as **not** resolved even though
        a principal row exists for it. The row records that nothing resolved the SID, which
        is a real observation and a finding in its own right — but it says nothing about
        what the SID belongs to, so a token built around it is a lower bound exactly as one
        built around a SID with no row at all is.
        """
        return self.kind is not None and self.kind is not PrincipalKind.UNRESOLVED


def default_assumption(subject: SubjectFacts) -> TokenAssumption:
    """The token assumption that fits a subject, when a caller does not choose one.

    Three cases, and the middle one is the interesting one:

    * A **well-known SID** asked about directly gets :attr:`TokenAssumption.SIDS_ONLY`.
      Asking what ``Everyone`` can reach means ``Everyone``; folding ``Authenticated
      Users`` into its token would silently widen the question, and the answer would
      include grants that anonymous sessions do not get.
    * ``ANONYMOUS LOGON`` gets :attr:`TokenAssumption.ANONYMOUS`, because that SID names
      precisely the session kind that does not carry ``Authenticated Users``.
    * **Everything else** — a user, a computer, a group, or a SID nothing has described —
      gets :attr:`TokenAssumption.AUTHENTICATED_USER`. For a group or an unresolved SID
      that is a statement about an authenticated principal holding it, which the resolver
      records as its own condition rather than leaving implicit.
    """
    if subject.sid == ANONYMOUS_LOGON_SID:
        return TokenAssumption.ANONYMOUS
    if subject.is_well_known:
        return TokenAssumption.SIDS_ONLY
    return TokenAssumption.AUTHENTICATED_USER


@dataclass(frozen=True, slots=True)
class SubjectToken:
    """The SIDs an ACL is evaluated against, and the account of how they got there.

    Immutable and self-describing: :meth:`contains` is the only question the evaluator
    asks, and :attr:`findings` is what the resolver forwards so that an answer built on an
    assumption can never be presented as one built on an observation.
    """

    subject: SubjectFacts
    assumption: TokenAssumption
    access_path: AccessPath
    entries: tuple[TokenSid, ...]
    membership_complete: bool = True
    findings: tuple[AccessFinding, ...] = ()

    def __post_init__(self) -> None:
        if not self.entries:
            raise DomainValidationError(
                "A token always contains at least the subject's own SID.", field="entries"
            )
        seen = {entry.key for entry in self.entries}
        if len(seen) != len(self.entries):
            raise DomainValidationError(
                "A token may not carry the same key twice: a SID is in it or it is not, "
                "and two entries for one key would let an ACE match by whichever came "
                "first.",
                field="entries",
            )

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(entry.key for entry in self.entries)

    @property
    def sids(self) -> frozenset[str]:
        return frozenset(entry.sid for entry in self.entries)

    @property
    def group_keys(self) -> tuple[str, ...]:
        """Observed group memberships, in the order they were reached."""
        return tuple(
            entry.key for entry in self.entries if entry.origin is SidOrigin.GROUP_MEMBERSHIP
        )

    @property
    def assumed(self) -> tuple[TokenSid, ...]:
        """Every entry that came from an assumption rather than an observation."""
        return tuple(entry for entry in self.entries if entry.is_assumed)

    def contains(self, key: str) -> bool:
        """Whether an ACE naming ``key`` applies to this subject."""
        return any(entry.key == key for entry in self.entries)

    def entry(self, key: str) -> TokenSid | None:
        """The token entry for a key, or ``None`` when the token does not carry it."""
        for candidate in self.entries:
            if candidate.key == key:
                return candidate
        return None


def build_token(
    subject: SubjectFacts,
    groups: Iterable[TokenSid] = (),
    *,
    access_path: AccessPath,
    assumption: TokenAssumption | None = None,
    membership_complete: bool = True,
    findings: Sequence[AccessFinding] = (),
) -> SubjectToken:
    """Assemble a token from the subject, its observed groups, and one named assumption.

    ``groups`` are the memberships a traversal found, already resolved to storage keys.
    They are taken as given: which of them ADG could observe, and whether the traversal
    completed, is the caller's business — ``membership_complete=False`` is forwarded as
    :attr:`~app.access_engine.conditions.AccessCondition.MEMBERSHIP_TRUNCATED` so that the
    resolver reports a lower bound rather than a result.

    Duplicate keys collapse onto the **first** occurrence, which is the observed one: a
    group reached by traversal keeps its membership path even when the same SID would also
    have been added as an assumption.
    """
    resolved = assumption or default_assumption(subject)
    collected: list[TokenSid] = [
        TokenSid(
            key=subject.key,
            sid=subject.sid,
            origin=SidOrigin.SUBJECT,
            display_name=subject.display_name,
        )
    ]
    seen = {subject.key}
    for group in groups:
        if group.key in seen:
            continue
        seen.add(group.key)
        collected.append(group)

    notes = list(findings)
    if not subject.resolved:
        notes.append(
            AccessFinding(
                AccessCondition.SUBJECT_UNRESOLVED,
                {"subject_sid": subject.sid, "subject_key": subject.key},
            )
        )
    if subject.is_group:
        notes.append(
            AccessFinding(
                AccessCondition.SUBJECT_IS_A_GROUP,
                {"subject_sid": subject.sid, "kind": subject.kind.value if subject.kind else None},
            )
        )
    if not membership_complete:
        notes.append(
            AccessFinding(AccessCondition.MEMBERSHIP_TRUNCATED, {"subject_key": subject.key})
        )

    assumed = _assumed_sids(resolved, access_path)
    added: list[str] = []
    for sid, origin in assumed:
        if sid in seen:
            continue
        seen.add(sid)
        added.append(sid)
        collected.append(TokenSid(key=sid, sid=sid, origin=origin))
    if added:
        notes.append(
            AccessFinding(
                AccessCondition.ASSUMED_TOKEN_SIDS,
                {
                    "assumption": resolved.value,
                    "access_path": access_path.value,
                    "sids": added,
                },
            )
        )

    return SubjectToken(
        subject=subject,
        assumption=resolved,
        access_path=access_path,
        entries=tuple(collected),
        membership_complete=membership_complete,
        findings=tuple(notes),
    )


def _assumed_sids(
    assumption: TokenAssumption, access_path: AccessPath
) -> tuple[tuple[str, SidOrigin], ...]:
    """The well-known and logon-session SIDs one assumption contributes, in order.

    ``Everyone`` before ``Authenticated Users`` before the logon SID, so that the list a
    response shows is stable and reads from broadest to narrowest.
    """
    if assumption is TokenAssumption.SIDS_ONLY:
        return ()

    entries: list[tuple[str, SidOrigin]] = [(EVERYONE_SID, SidOrigin.WELL_KNOWN)]
    if assumption is TokenAssumption.AUTHENTICATED_USER:
        entries.append((AUTHENTICATED_USERS_SID, SidOrigin.WELL_KNOWN))
    else:
        entries.append((ANONYMOUS_LOGON_SID, SidOrigin.WELL_KNOWN))
    logon = NETWORK_SID if access_path is AccessPath.REMOTE_SMB else INTERACTIVE_SID
    entries.append((logon, SidOrigin.LOGON_TYPE))
    return tuple(entries)
