"""What the resolver could not settle, and what it had to assume.

An effective-access answer is only as good as its inputs, and ADG's inputs are
observations: a group whose membership nobody collected, a share whose ACL has not been
read, a token SID that depends on how the user logged on. A resolver that returns a single
mask and says nothing else forces every consumer to treat "no access" and "no data" as the
same answer, which in an audit tool is how a finding disappears.

So every uncertainty is named. :class:`AccessCondition` is the closed vocabulary, and
:class:`AccessFinding` is one occurrence of it with the evidence attached. The two rules
that hold throughout:

1. **A condition is never silently dropped.** If the resolver could not establish
   something, it says so, and :class:`~app.access_engine.resolver.AccessCertainty` records
   in which direction the answer may be wrong.
2. **A condition is not a verdict.** ``NULL_DACL`` is a statement about a descriptor;
   whether it matters for this principal is what the rights mask says. Consumers decide
   severity; this module supplies facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

__all__ = [
    "CONDITION_DESCRIPTIONS",
    "OVERSTATING_CONDITIONS",
    "UNDERSTATING_CONDITIONS",
    "AccessCondition",
    "AccessFinding",
    "describe_condition",
]


class AccessCondition(StrEnum):
    """Something the resolver assumed, could not evaluate, or judged worth reporting.

    Grouped below by what produced it. The values are stable: they appear in API responses
    and a client is expected to branch on them, so a rename is a contract change.
    """

    # -- descriptor-level facts --------------------------------------------------------

    NULL_DACL = "null_dacl"
    """The object has no DACL at all, which grants everyone full access."""

    EMPTY_DACL = "empty_dacl"
    """The DACL is present and contains no entries: nobody has access through it. The
    owner still holds implicit control rights, which is a separate condition."""

    PROTECTED_DACL = "protected_dacl"
    """``SE_DACL_PROTECTED``: the object refuses inherited entries, so an ancestor's grant
    stops above it. Reported because it is where an administrator has to make a fix."""

    NON_CANONICAL_DACL = "non_canonical_dacl"
    """Entries are not in the order Windows maintains (explicit before inherited, Deny
    before Allow within each). Windows still evaluates them in the order stored, so the
    ACL editor and the access check disagree about this object."""

    ORDER_DEPENDENT_RESULT = "order_dependent_result"
    """This subject's rights differ from what the canonical-ACL model would compute — an
    Allow ahead of a Deny actually grants. The strongest possible evidence that the
    ordering matters here, and it is reported per subject because it is a fact about one
    principal's evaluation, not about the ACL alone."""

    ACE_COUNT_MISMATCH = "ace_count_mismatch"
    """The descriptor declared a different number of entries than ADG holds. Entries were
    read and never stored, so the evaluation ran over part of a DACL."""

    ACL_TRUNCATED = "acl_truncated"
    """The DACL exceeded the evaluator's entry ceiling and was not evaluated in full."""

    # -- trustees the access check cannot resolve --------------------------------------

    OWNER_IMPLICIT_RIGHTS = "owner_implicit_rights"
    """The subject owns the object, so Windows grants ``READ_CONTROL`` and ``WRITE_DAC``
    regardless of the DACL — an escalation path no ACE shows."""

    OWNER_RIGHTS_ACE = "owner_rights_ace"
    """An ``OWNER RIGHTS`` (``S-1-3-4``) entry is present. It replaces the owner's implicit
    rights with whatever it grants, which is the one way to take ``WRITE_DAC`` away from an
    owner."""

    CREATOR_OWNER_ACE = "creator_owner_ace"
    """A ``CREATOR OWNER`` / ``CREATOR GROUP`` entry applies to this object rather than
    only descending. No token contains those SIDs, so the entry grants nothing here; it is
    reported because it looks like a grant in every ACL viewer."""

    LOGON_TYPE_TRUSTEE = "logon_type_trustee"
    """An entry names a SID whose presence in a token depends on how the session was
    established (``BATCH``, ``SERVICE``, ``REMOTE INTERACTIVE``, and similar). ADG models
    one logon type per access path and cannot rule the others in or out."""

    UNRESOLVED_TRUSTEE = "unresolved_trustee"
    """An entry names a SID no run has described. The grant is real; the grantee is not
    nameable, which is the orphaned-SID finding."""

    TRUSTEE_MEMBERSHIP_UNOBSERVED = "trustee_membership_unobserved"
    """An entry names a group ADG holds no membership edges for. "Not a member" cannot be
    concluded from that, so a principal may hold rights this answer does not show."""

    # -- the subject and its token -----------------------------------------------------

    SUBJECT_UNRESOLVED = "subject_unresolved"
    """No principal record describes the subject. Its group memberships may be incomplete
    and its kind is unknown, so the token had to be assumed."""

    SUBJECT_IS_A_GROUP = "subject_is_a_group"
    """The subject is a group. A group holds no token; the rights reported are those of an
    authenticated member of it, which is the useful question but not the literal one."""

    MEMBERSHIP_TRUNCATED = "membership_truncated"
    """The membership traversal hit a limit. The token is a lower bound, so the rights are
    too."""

    MEMBERSHIP_CYCLE = "membership_cycle"
    """A group cycle was traversed. Expansion terminated correctly; the cycle is itself a
    finding worth showing to whoever maintains the groups."""

    ASSUMED_TOKEN_SIDS = "assumed_token_sids"
    """Well-known SIDs were added to the token because Windows puts them in every token of
    this kind (``Everyone``, ``Authenticated Users``, the logon-type SID). They are named
    individually on the finding so the assumption can be checked."""

    # -- coverage gaps -----------------------------------------------------------------

    SHARE_ACL_NOT_OBSERVED = "share_acl_not_observed"
    """No run has read this share's ACL, so the share layer could not restrict the result.
    The rights reported are an upper bound: the real answer cannot be wider."""

    NTFS_ACL_NOT_OBSERVED = "ntfs_acl_not_observed"
    """No run has read this path's security descriptor, and no ancestor was available to
    derive one from."""

    NTFS_ACL_DERIVED = "ntfs_acl_derived"
    """The path's own descriptor was never read; the DACL was projected from the nearest
    ancestor that was. Correct only if nothing between them broke inheritance."""

    INTERMEDIATE_PATH_UNOBSERVED = "intermediate_path_unobserved"
    """The ancestor a DACL was derived from is more than one level up, and the directories
    in between were not read. Any of them could be a boundary."""

    RESOURCE_NOT_UNDER_SHARE = "resource_not_under_share"
    """A remote calculation was asked for against a path whose share ADG cannot identify,
    so the share layer could not be applied."""

    # -- rights the mask cannot express ------------------------------------------------

    INDETERMINATE_RIGHTS = "indeterminate_rights"
    """An evaluated mask carried ``MAXIMUM_ALLOWED``, whose meaning Windows resolves per
    open against the calling token. No fixed rights set exists for it."""

    UNRECOGNIZED_RIGHTS_BITS = "unrecognized_rights_bits"
    """An evaluated mask carried bits matching no right ADG knows. They are kept in the
    result and excluded from every label."""

    GENERIC_RIGHTS_EXPANDED = "generic_rights_expanded"
    """A mask carried generic bits, resolved through the file-system generic mapping before
    evaluation. The raw form is retained on the entry."""

    ESCALATION_RIGHTS = "escalation_rights"
    """The result includes ``WRITE_DAC`` or ``WRITE_OWNER``: the holder can grant
    themselves anything, whatever the friendly label says."""


CONDITION_DESCRIPTIONS: Final[dict[AccessCondition, str]] = {
    AccessCondition.NULL_DACL: (
        "This object has no DACL. Windows grants every principal full access to an object "
        "with a NULL DACL, whatever the share permissions say."
    ),
    AccessCondition.EMPTY_DACL: (
        "This object's DACL is present and empty, so it grants nobody anything. Only the "
        "owner's implicit rights remain."
    ),
    AccessCondition.PROTECTED_DACL: (
        "This object blocks inherited permissions (SE_DACL_PROTECTED), so grants made on "
        "its parents do not reach it."
    ),
    AccessCondition.NON_CANONICAL_DACL: (
        "The entries are not in canonical order. Windows evaluates them in the order "
        "stored, so what the ACL editor shows and what the access check does can differ."
    ),
    AccessCondition.ORDER_DEPENDENT_RESULT: (
        "An Allow entry precedes a Deny entry that would otherwise have removed these "
        "rights, so this principal's access depends on the order of the entries."
    ),
    AccessCondition.ACE_COUNT_MISMATCH: (
        "The descriptor declared a different number of entries than ADG holds, so this "
        "evaluation ran over an incomplete DACL."
    ),
    AccessCondition.ACL_TRUNCATED: (
        "The DACL is larger than the evaluator's entry ceiling and was not evaluated in full."
    ),
    AccessCondition.OWNER_IMPLICIT_RIGHTS: (
        "The subject owns this object, so Windows grants READ_CONTROL and WRITE_DAC "
        "regardless of the DACL. The owner can therefore grant themselves anything."
    ),
    AccessCondition.OWNER_RIGHTS_ACE: (
        "An OWNER RIGHTS (S-1-3-4) entry is present. It replaces the owner's implicit "
        "rights rather than adding to them."
    ),
    AccessCondition.CREATOR_OWNER_ACE: (
        "A CREATOR OWNER or CREATOR GROUP entry applies to this object. No access token "
        "contains those SIDs, so it grants nothing here even though an ACL viewer shows it."
    ),
    AccessCondition.LOGON_TYPE_TRUSTEE: (
        "An entry names a SID whose presence in a token depends on how the session was "
        "established. ADG models one logon type per access path and cannot decide the rest."
    ),
    AccessCondition.UNRESOLVED_TRUSTEE: (
        "An entry names a SID no run has described. The grant is real; who holds it is not known."
    ),
    AccessCondition.TRUSTEE_MEMBERSHIP_UNOBSERVED: (
        "An entry names a group whose membership ADG has never collected, so nobody can be "
        "ruled out of it."
    ),
    AccessCondition.SUBJECT_UNRESOLVED: (
        "No run has described this principal, so its memberships may be incomplete and its "
        "token had to be assumed."
    ),
    AccessCondition.SUBJECT_IS_A_GROUP: (
        "The subject is a group. Groups do not hold access tokens; these are the rights an "
        "authenticated member of the group would have."
    ),
    AccessCondition.MEMBERSHIP_TRUNCATED: (
        "The membership traversal stopped at a limit, so the token is a lower bound and the "
        "rights may be wider than reported."
    ),
    AccessCondition.MEMBERSHIP_CYCLE: (
        "A group cycle was traversed while building the token. Expansion terminated; the "
        "cycle itself is worth fixing."
    ),
    AccessCondition.ASSUMED_TOKEN_SIDS: (
        "Well-known SIDs that Windows places in every token of this kind were added to the "
        "subject's token. They are listed so the assumption can be checked."
    ),
    AccessCondition.SHARE_ACL_NOT_OBSERVED: (
        "No run has read this share's permissions, so the share layer could not restrict "
        "the result. The real access cannot be wider than what is reported."
    ),
    AccessCondition.NTFS_ACL_NOT_OBSERVED: (
        "No run has read this path's security descriptor and no ancestor was available to "
        "derive one from."
    ),
    AccessCondition.NTFS_ACL_DERIVED: (
        "This path's own descriptor was never read. The DACL was projected from the nearest "
        "ancestor that was, which is correct only if nothing in between breaks inheritance."
    ),
    AccessCondition.INTERMEDIATE_PATH_UNOBSERVED: (
        "The directories between this path and the ancestor its DACL was derived from were "
        "not read. Any of them could set its own permissions."
    ),
    AccessCondition.RESOURCE_NOT_UNDER_SHARE: (
        "ADG cannot identify the share publishing this path, so the share layer could not "
        "be applied to a remote calculation."
    ),
    AccessCondition.INDETERMINATE_RIGHTS: (
        "An entry carried MAXIMUM_ALLOWED, which Windows resolves per open against the "
        "calling token. The rights set has no fixed value."
    ),
    AccessCondition.UNRECOGNIZED_RIGHTS_BITS: (
        "An evaluated mask carried bits matching no right ADG knows. They are retained in "
        "the result and excluded from every label."
    ),
    AccessCondition.GENERIC_RIGHTS_EXPANDED: (
        "An entry carried generic rights, resolved through the file-system generic mapping "
        "before evaluation."
    ),
    AccessCondition.ESCALATION_RIGHTS: (
        "The result includes WRITE_DAC or WRITE_OWNER. Whatever the label reads, the holder "
        "can grant themselves any other right."
    ),
}
"""One sentence per condition, written for the operator who has to act on it."""


OVERSTATING_CONDITIONS: Final[frozenset[AccessCondition]] = frozenset(
    {
        AccessCondition.SHARE_ACL_NOT_OBSERVED,
        AccessCondition.NTFS_ACL_DERIVED,
        AccessCondition.INTERMEDIATE_PATH_UNOBSERVED,
        AccessCondition.RESOURCE_NOT_UNDER_SHARE,
        AccessCondition.ACE_COUNT_MISMATCH,
        AccessCondition.ACL_TRUNCATED,
        AccessCondition.SUBJECT_IS_A_GROUP,
        AccessCondition.SUBJECT_UNRESOLVED,
    }
)
"""Conditions under which the reported rights may be **wider** than the truth.

A restriction ADG could not see would only ever remove rights, so the answer stands as an
upper bound. ``ACE_COUNT_MISMATCH``, ``ACL_TRUNCATED`` and ``SUBJECT_UNRESOLVED`` are in
both sets: the entries that went missing could have been either kind, and a subject nothing
describes has both unknown memberships and an unknown principal kind.

``ASSUMED_TOKEN_SIDS`` is deliberately in **neither** set. Windows places ``Everyone``,
``Authenticated Users`` and the access path's logon SID in every token of the declared
kind, so the assumption is exact once the kind is known — and whether the kind is known is
what ``SUBJECT_IS_A_GROUP`` and ``SUBJECT_UNRESOLVED`` report. Counting it as an uncertainty
would mark every ordinary answer in the system uncertain, which is how a certainty flag
stops being read.
"""

UNDERSTATING_CONDITIONS: Final[frozenset[AccessCondition]] = frozenset(
    {
        AccessCondition.MEMBERSHIP_TRUNCATED,
        AccessCondition.TRUSTEE_MEMBERSHIP_UNOBSERVED,
        AccessCondition.LOGON_TYPE_TRUSTEE,
        AccessCondition.SUBJECT_UNRESOLVED,
        AccessCondition.ACE_COUNT_MISMATCH,
        AccessCondition.ACL_TRUNCATED,
    }
)
"""Conditions under which the reported rights may be **narrower** than the truth.

A membership ADG has not collected can only add trustees to the token, so a grant may
exist that this answer does not show. These are the ones that hide a finding, which is why
they are tracked separately rather than lumped in with the rest.
"""


def describe_condition(condition: AccessCondition) -> str:
    """The operator-facing sentence for a condition."""
    return CONDITION_DESCRIPTIONS[condition]


@dataclass(frozen=True, slots=True)
class AccessFinding:
    """One occurrence of a condition, with the evidence that produced it.

    ``detail`` carries whatever identifies the occurrence — the trustee SID, the ancestor a
    DACL came from, the SIDs an assumption added. It is deliberately a free-form mapping
    rather than a union of typed payloads: a client renders the message and the condition,
    and anything richer than that belongs to the specific consumer rather than to the
    vocabulary.
    """

    condition: AccessCondition
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> str:
        """The operator-facing description of this finding's condition."""
        return describe_condition(self.condition)

    @property
    def may_overstate(self) -> bool:
        """Whether this finding means the rights could be wider than reported."""
        return self.condition in OVERSTATING_CONDITIONS

    @property
    def may_understate(self) -> bool:
        """Whether this finding means the rights could be narrower than reported."""
        return self.condition in UNDERSTATING_CONDITIONS

    def __str__(self) -> str:
        return f"{self.condition.value}: {self.message}"
