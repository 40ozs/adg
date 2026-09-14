r"""A deterministic normalized form for a DACL, and the hash taken over it.

An NTFS scan asks the same question millions of times: *is this directory's DACL the same
one it inherited from its parent?* Comparing ACE lists pairwise is both slow and fragile —
two readings of one descriptor can differ in list order, in SID spelling, in integer width,
and in whether a source bothered to report a position — so ADG reduces a DACL to one
canonical text document and hashes that. Equal hashes mean the same normalized DACL; the
document itself is reproducible, so a mismatch can be explained rather than merely reported.

**What the hash covers, and what it deliberately does not.**

It covers exactly what the DACL says about access: whether a DACL is present at all,
whether it is protected from inheritance, and the ACEs themselves — trustee, allow/deny,
raw mask, raw flags byte — in the order the descriptor stores them.

It does **not** cover the owner. Ownership carries implicit rights and is recorded
separately, but folding it in would defeat the hash's main use: every folder under a share
root is legitimately owned by whoever created it, while sharing one identical inherited
DACL. An owner-sensitive hash would mark every one of those folders an ACL boundary, which
is the exact opposite of what a boundary is for.

**Order is part of the identity.** Windows evaluates a DACL in order, so a Deny moved below
an Allow grants access that was previously refused. A hash that called those two ACLs equal
would hide a real change. What is *not* part of it is the numeric value of ``order_index``:
positions are reduced to their rank, so a collector that numbered around skipped audit
entries and one that numbered only the DACL entries it kept produce the same hash for the
same sequence.

**An unordered reading can never collide with an ordered one.** When any ACE arrives
without a position, the document says ``order=unordered`` and the entries are sorted by
content instead. The two modes therefore hash differently even for identical entries —
which is honest: one reading knows the evaluation order and the other does not, and
treating them as the same observation would claim knowledge that was never collected.

The format is versioned in its own first line (``adg-acl/1``). It is mirrored byte for byte
by ``Get-AdgAclHash`` in the NTFS collector, and a contract test runs the PowerShell and
compares the two — the same arrangement that keeps source-key derivations honest.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from app.domain.access import AceType, NtfsAce
from app.domain.errors import DomainValidationError
from app.domain.identity import Sid

ACL_NORMAL_FORM_VERSION: Final = "adg-acl/1"
"""First line of every normalized document. A change of format changes this token, so two
hashes produced by different formats can never be compared as though they agreed."""

ACL_HASH_ALGORITHM: Final = "sha256"

ACL_HASH_LENGTH: Final = 64
"""Lower-case hexadecimal characters in an ``acl_hash``."""

_MAX_ACCESS_MASK: Final = 0xFFFFFFFF
_MAX_ACE_FLAGS: Final = 0xFF

__all__ = [
    "ACL_HASH_ALGORITHM",
    "ACL_HASH_LENGTH",
    "ACL_NORMAL_FORM_VERSION",
    "AclAceFacts",
    "NormalizedAcl",
    "acl_hash",
    "is_acl_hash",
    "normalize_acl",
]


@dataclass(frozen=True, slots=True)
class AclAceFacts:
    """The part of an ACE that the normalized form keeps.

    A plain record rather than :class:`app.domain.NtfsAce` so that a caller holding rows
    from the database, a contract payload, or a live descriptor can all normalize without
    first building a domain object — and so that the normalizer cannot be handed an ACE
    whose ``source`` and flags disagree, because it never looks at ``source`` at all. The
    INHERITED bit already lives in ``ace_flags``, which is what the descriptor stores.
    """

    trustee_sid: str
    ace_type: AceType
    access_mask: int
    ace_flags: int
    order_index: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.access_mask, int) or isinstance(self.access_mask, bool):
            raise DomainValidationError(
                f"An access mask must be an integer; received {type(self.access_mask).__name__}.",
                field="access_mask",
            )
        if not 0 <= self.access_mask <= _MAX_ACCESS_MASK:
            raise DomainValidationError(
                f"An access mask must be an unsigned 32-bit value; received {self.access_mask}.",
                value=self.access_mask,
                field="access_mask",
            )
        if not isinstance(self.ace_flags, int) or isinstance(self.ace_flags, bool):
            raise DomainValidationError(
                f"ACE flags must be an integer; received {type(self.ace_flags).__name__}.",
                field="ace_flags",
            )
        if not 0 <= self.ace_flags <= _MAX_ACE_FLAGS:
            raise DomainValidationError(
                f"ACE flags are a single byte; received {self.ace_flags}.",
                value=self.ace_flags,
                field="ace_flags",
            )
        if self.order_index is not None and self.order_index < 0:
            raise DomainValidationError(
                f"order_index must be non-negative; received {self.order_index}.",
                value=self.order_index,
                field="order_index",
            )
        # Canonicalize the SID here rather than trusting the caller's spelling: two
        # readings of one descriptor that differ only in SID case must hash the same.
        object.__setattr__(self, "trustee_sid", Sid(self.trustee_sid).value)

    @classmethod
    def from_ace(cls, ace: NtfsAce) -> AclAceFacts:
        """The facts a domain ACE contributes to the normalized form."""
        return cls(
            trustee_sid=ace.trustee_sid.value,
            ace_type=ace.ace_type,
            access_mask=ace.access_mask,
            ace_flags=int(ace.flags),
            order_index=ace.order_index,
        )

    @property
    def content_line(self) -> str:
        """The entry rendered without its position: everything the descriptor stores."""
        return (
            f"{self.trustee_sid}|{self.ace_type.value}"
            f"|0x{self.access_mask:08x}|0x{self.ace_flags:02x}"
        )


@dataclass(frozen=True, slots=True)
class NormalizedAcl:
    """One DACL reduced to a canonical document, plus the digest of that document.

    ``text`` is kept, not just the digest. A hash mismatch between a collector and the
    server is a question an operator has to answer — *which* entry differs — and that is
    only answerable if the thing that was hashed can be printed.
    """

    text: str
    digest: str
    ordered: bool
    ace_count: int

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(self.text.splitlines())


def normalize_acl(
    *,
    dacl_present: bool,
    dacl_protected: bool = False,
    aces: Iterable[AclAceFacts | NtfsAce] = (),
) -> NormalizedAcl:
    """Reduce a DACL to its canonical document and hash it.

    Args:
        dacl_present: ``False`` means a NULL DACL — everyone has full access. It must carry
            no ACEs, and it normalizes to a document distinct from a present-but-empty
            DACL, which grants nobody access. Collapsing those two would invert the answer.
        dacl_protected: ``SE_DACL_PROTECTED``. Part of the hash because two directories
            holding identical entries, one protected and one not, are not the same ACL:
            one is a boundary and the other inherits.
        aces: the DACL's entries, in any order. Position comes from ``order_index``, never
            from the order they are passed in, so a caller that reads rows back in
            ``ace_key`` order gets the same answer as one that preserved DACL order.

    Raises:
        DomainValidationError: if a NULL DACL carries ACEs, or two ACEs claim the same
            position — an ambiguity that would make the hash depend on iteration order.
    """
    entries = [
        item if isinstance(item, AclAceFacts) else AclAceFacts.from_ace(item) for item in aces
    ]

    if not dacl_present and entries:
        raise DomainValidationError(
            "A NULL DACL (dacl_present=false) grants everyone full access and carries no "
            f"ACEs, but {len(entries)} were supplied. A present-but-empty DACL — which "
            "grants nobody access — is the opposite fact and is reported as "
            "dacl_present=true with no entries.",
            field="dacl_present",
        )

    ordered = all(entry.order_index is not None for entry in entries)
    lines = [
        ACL_NORMAL_FORM_VERSION,
        f"dacl_present={_flag(dacl_present)}",
        f"dacl_protected={_flag(dacl_protected)}",
        f"order={'observed' if ordered else 'unordered'}",
    ]
    lines.extend(_ace_lines(entries, ordered=ordered))

    # Every line terminated, including the last: a document is a sequence of complete
    # records, so appending an entry can never change the bytes of the ones before it.
    text = "".join(f"{line}\n" for line in lines)
    return NormalizedAcl(
        text=text,
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        ordered=ordered,
        ace_count=len(entries),
    )


def acl_hash(
    *,
    dacl_present: bool,
    dacl_protected: bool = False,
    aces: Iterable[AclAceFacts | NtfsAce] = (),
) -> str:
    """The digest alone, for callers that only need to compare."""
    return normalize_acl(dacl_present=dacl_present, dacl_protected=dacl_protected, aces=aces).digest


def is_acl_hash(value: str) -> bool:
    """Whether a string is shaped like an ``acl_hash``: 64 lower-case hex characters."""
    if not isinstance(value, str) or len(value) != ACL_HASH_LENGTH:
        return False
    return all(character in "0123456789abcdef" for character in value)


def _ace_lines(entries: Sequence[AclAceFacts], *, ordered: bool) -> list[str]:
    """Render the entries, sorted so the document does not depend on input order.

    In ``observed`` mode the sort key is the reported position followed by the entry's own
    text, and the rendered rank is the entry's place in that sorted sequence — not the
    reported number. Two collectors that number a DACL differently (one counting the audit
    entries it dropped, one not) therefore agree, while any reordering of the entries
    themselves still changes the document.
    """
    if not ordered:
        # Sorted by content, which is pure ASCII here, so this is an ordinal sort in every
        # language the normalizer is mirrored into.
        return [f"ace=-|{line}" for line in sorted(entry.content_line for entry in entries)]

    # `ordered` already established that every entry has one; naming it makes that a fact
    # the type checker shares rather than a comment.
    positions = [entry.order_index for entry in entries if entry.order_index is not None]
    if len(set(positions)) != len(positions):
        duplicated = sorted({index for index in positions if positions.count(index) > 1})
        raise DomainValidationError(
            f"Two ACEs claim DACL position(s) {duplicated}. A position identifies one entry "
            "in the evaluation order; duplicates would make the normalized form depend on "
            "the order the entries happened to be read back in. Report each ACE's own "
            "index, or omit order_index entirely and accept an unordered hash.",
            value=duplicated,
            field="order_index",
        )

    keyed = sorted(
        entries,
        # Zero-padded so the key sorts numerically under an ordinal string comparison too,
        # which is what the PowerShell mirror has to use.
        key=lambda entry: f"{entry.order_index or 0:010d}|{entry.content_line}",
    )
    return [f"ace={rank}|{entry.content_line}" for rank, entry in enumerate(keyed)]


def _flag(value: bool) -> str:
    return "true" if value else "false"
