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

**SYSTEM is the one subject whose identity depends on the path.** ``S-1-5-18`` is the
LocalSystem account *of whichever machine evaluates the ACL*, and no network logon ever
carries it: a SYSTEM process reaching a share arrives as its machine's computer account.
So which identity a SYSTEM subject stands for is assessed once, here, as a
:class:`SystemMapping`, and everything downstream — the evaluation, the verdict, the
explanation and the API — reads that one value instead of deciding for itself.
"""

from __future__ import annotations

import datetime as dt
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
    "SYSTEM_MAPPING_CONDITIONS",
    "SYSTEM_SID",
    "SidOrigin",
    "SubjectFacts",
    "SubjectToken",
    "SystemMapping",
    "TokenAssumption",
    "TokenSid",
    "build_token",
    "default_assumption",
    "system_mapping_of",
]

EVERYONE_SID: Final = "S-1-1-0"
AUTHENTICATED_USERS_SID: Final = "S-1-5-11"
ANONYMOUS_LOGON_SID: Final = "S-1-5-7"
NETWORK_SID: Final = "S-1-5-2"
INTERACTIVE_SID: Final = "S-1-5-4"

SYSTEM_SID: Final = "S-1-5-18"
"""``NT AUTHORITY\\SYSTEM``. Its meaning is fixed by Windows; which machine's it is, is not."""

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


class SystemMapping(StrEnum):
    """Which identity a SYSTEM subject stands for in one access check — the one value for it.

    Every token built for ``S-1-5-18`` carries exactly one of these, and exactly one finding
    in :data:`SYSTEM_MAPPING_CONDITIONS` reports it. The verdict sentence, the explanation
    and the UI all derive from it; none of them re-derives the mapping.

    Only :attr:`CONFIRMED` may be stated as a mapping. The other three widen the certainty
    in both directions. None of the four supports a conclusive negative: even a confirmed
    LocalSystem holds groups its token here does not carry, so its condition understates,
    and an empty result for any SYSTEM subject is ``indeterminate`` rather than
    ``no_grant``.
    """

    CONFIRMED = "confirmed"
    """Local access, and a collector described ``S-1-5-18`` as the well-known SYSTEM
    principal. The subject is the LocalSystem account of the host that holds the ACL. The
    rights are a lower bound: its groups are not modeled."""

    AMBIGUOUS = "ambiguous"
    """Access over a share. A network logon never carries ``S-1-5-18``; SYSTEM arrives as the
    computer account of whichever machine it runs on — one candidate per computer, and the
    question does not choose one. The subject's own SID is therefore not presented."""

    MISSING = "missing"
    """No collector described ``S-1-5-18``, or the one that tried recorded it unresolved.
    Entries naming the SID are matched literally; nothing about it is assumed."""

    CONFLICTING = "conflicting"
    """A collector described ``S-1-5-18`` as something other than the well-known SYSTEM
    principal. The SID's fixed meaning and the collected description disagree."""


SYSTEM_MAPPING_CONDITIONS: Final[dict[SystemMapping, AccessCondition]] = {
    SystemMapping.CONFIRMED: AccessCondition.SYSTEM_MAPPING_CONFIRMED,
    SystemMapping.AMBIGUOUS: AccessCondition.SYSTEM_MAPPING_AMBIGUOUS,
    SystemMapping.MISSING: AccessCondition.SYSTEM_MAPPING_MISSING,
    SystemMapping.CONFLICTING: AccessCondition.SYSTEM_MAPPING_CONFLICTING,
}
"""The one finding each mapping state is reported by. A bijection, and tested as one."""


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
    enabled: bool | None = None
    """Whether the account can authenticate, when a run has said so.

    ``None`` is the common case and means nobody recorded it: a group has no such state, and
    neither does a SID nothing resolved. Only ``False`` changes an answer, and it changes it
    by qualifying it rather than by emptying it — the rights a disabled account holds are
    exactly the rights on the ACL, and they are a finding precisely because nothing will
    revoke them when the account is re-enabled.
    """

    last_observed_run_id: str | None = None
    """The run that last described the subject, when one did. Provenance only: it is what a
    confirmed or disputed SYSTEM mapping cites, and it never changes an answer."""

    last_observed_at: dt.datetime | None = None

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

    ``SYSTEM`` gets :attr:`TokenAssumption.SIDS_ONLY` **whatever a collector said it is.**
    Its SID is fixed by Windows, so a missing or contradictory description is not licence to
    treat it as an ordinary authenticated user — that would invent ``Everyone``,
    ``Authenticated Users`` and a logon SID for a token nobody has described. The
    description's state is reported by :func:`system_mapping_of` instead.
    """
    if subject.sid == ANONYMOUS_LOGON_SID:
        return TokenAssumption.ANONYMOUS
    if subject.sid == SYSTEM_SID or subject.is_well_known:
        return TokenAssumption.SIDS_ONLY
    return TokenAssumption.AUTHENTICATED_USER


def system_mapping_of(subject: SubjectFacts, access_path: AccessPath) -> SystemMapping | None:
    """Which identity a SYSTEM subject stands for on this path; ``None`` for any other subject.

    One state, from two pieces of evidence read in a fixed order — what a collector said the
    SID is, then which identity it is on this path — and the first that prevents a
    conclusion wins:

    1. **CONFLICTING** — described as anything but well-known (or unresolved). A
       disagreement in collected evidence has to be reconciled whatever was asked, so it is
       never hidden behind another state.
    2. **AMBIGUOUS** — over a share. ``S-1-5-18`` is never presented there, so what a
       collector said about it cannot change the answer and its absence is not what blocks
       one.
    3. **MISSING** — no description, or one recording that nothing resolved the SID.
    4. **CONFIRMED** — local access to a SYSTEM a collector described as well-known.

    The evidence behind the other states is never discarded: the finding's detail always
    carries the description that was read, ``None`` included.
    """
    if subject.sid != SYSTEM_SID:
        return None
    if subject.kind is not None and subject.kind not in (
        PrincipalKind.WELL_KNOWN,
        PrincipalKind.UNRESOLVED,
    ):
        return SystemMapping.CONFLICTING
    if access_path is AccessPath.REMOTE_SMB:
        return SystemMapping.AMBIGUOUS
    if not subject.resolved:
        return SystemMapping.MISSING
    return SystemMapping.CONFIRMED


def _presents_subject(subject: SubjectFacts, access_path: AccessPath) -> bool:
    """Whether Windows puts the subject's own SID in a session token on this path.

    Always, except for SYSTEM over a share: no network logon carries ``S-1-5-18``. Decided by
    the SID and the path alone, never by the description, because no description changes
    what a network logon contains.
    """
    return not (subject.sid == SYSTEM_SID and access_path is AccessPath.REMOTE_SMB)


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
    system_mapping: SystemMapping | None = None
    """Which identity a SYSTEM subject stands for; ``None`` when the subject is not SYSTEM,
    and for a token built directly to ask what an ACL grants entries naming a SID."""

    subject_presented: bool = True
    """Whether the subject's own SID is in the session this token models.

    False only for SYSTEM over a share. The subject entry stays in :attr:`entries` — it is
    who the question is about, and every explanation starts from it — but :meth:`contains`
    and :meth:`entry` do not offer it, so no ACE and no ownership can match it. That is the
    whole difference between "SYSTEM holds Full Control here" and the truth, which is that
    an entry naming SYSTEM is invisible to a network session.
    """

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
        return self.entry(key) is not None

    def entry(self, key: str) -> TokenSid | None:
        """The token entry for a key, or ``None`` when the session does not carry it.

        The subject's own entry is withheld when :attr:`subject_presented` is false.
        """
        for candidate in self.entries:
            if candidate.key != key:
                continue
            if candidate.origin is SidOrigin.SUBJECT and not self.subject_presented:
                return None
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

    A SYSTEM subject always leaves with its :class:`SystemMapping` and the one finding that
    reports it. Over a share its own SID is not presented, and neither are the groups
    observed for it: those are memberships of one host's LocalSystem, and a network session
    arrives as a computer account that carries none of them. They are named in the finding
    rather than dropped silently.
    """
    resolved = assumption or default_assumption(subject)
    presented = _presents_subject(subject, access_path)
    collected: list[TokenSid] = [
        TokenSid(
            key=subject.key,
            sid=subject.sid,
            origin=SidOrigin.SUBJECT,
            display_name=subject.display_name,
        )
    ]
    seen = {subject.key}
    withheld: list[str] = []
    for group in groups:
        if group.key in seen or group.key in withheld:
            continue
        if not presented:
            withheld.append(group.key)
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
    mapping = system_mapping_of(subject, access_path)
    if mapping is not None:
        notes.append(_system_mapping_finding(subject, access_path, mapping, presented, withheld))
    if subject.is_group:
        notes.append(
            AccessFinding(
                AccessCondition.SUBJECT_IS_A_GROUP,
                {"subject_sid": subject.sid, "kind": subject.kind.value if subject.kind else None},
            )
        )
    if subject.enabled is False:
        notes.append(
            AccessFinding(
                AccessCondition.SUBJECT_DISABLED,
                {"subject_sid": subject.sid, "subject_key": subject.key},
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
        system_mapping=mapping,
        subject_presented=presented,
    )


def _system_mapping_finding(
    subject: SubjectFacts,
    access_path: AccessPath,
    mapping: SystemMapping,
    presented: bool,
    withheld: Sequence[str],
) -> AccessFinding:
    """The finding that reports a SYSTEM subject's mapping, with every piece of evidence.

    The same keys in every state, so a client never has to know which state carries what:
    the description that was read (``None`` when there was none), what Windows says the SID
    is, where the description came from, and whether the SID was presented on this path.
    """
    return AccessFinding(
        SYSTEM_MAPPING_CONDITIONS[mapping],
        {
            "state": mapping.value,
            "subject_sid": subject.sid,
            "subject_key": subject.key,
            "access_path": access_path.value,
            "presented": presented,
            "described_as": None if subject.kind is None else subject.kind.value,
            "described_name": subject.display_name,
            "expected_kind": PrincipalKind.WELL_KNOWN.value,
            "last_observed_run_id": subject.last_observed_run_id,
            "last_observed_at": (
                None if subject.last_observed_at is None else subject.last_observed_at.isoformat()
            ),
            "withheld_groups": list(withheld),
        },
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
