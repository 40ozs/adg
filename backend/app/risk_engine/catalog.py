r"""What each rule is, what it is worth by default, and what to do about it.

The catalog is **data about rules**, held apart from the rules themselves. A rule body in
:mod:`app.risk_engine.rules` decides whether a shape is present; everything a human reads —
the title, why the shape matters, the steps to fix it, what a fix might break — lives here,
and so does the default severity of each shape.

Three reasons for the separation, in order of how much trouble each one saves:

1. **Severity is a local judgment.** A direct user ACE on a departmental share is a tidiness
   problem; the same entry on a share holding payroll data is not. Keeping the default in a
   table means an installation overrides it in configuration
   (:class:`app.risk_engine.configuration.RuleConfig`) without touching a predicate, and
   nobody is ever tempted to make a rule stop matching in order to lower a number.
2. **Remediation guidance is advice, and ADG changes nothing.** The product is read-only by
   design (ADR-0004). Every entry here therefore describes what an administrator would do,
   names what it could break, and never implies ADG will do it.
3. **A rule's version is a contract.** :attr:`RuleDefinition.version` changes when the
   *predicate* changes — when the rule would now match something it did not, or stop matching
   something it did. Editing this file's prose does not change it. That distinction is what
   lets a stored finding say which logic produced it and lets an operator tell a re-worded
   rule from a re-scoped one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from app.access_engine import RightsCategory
from app.risk_engine.facts import FactKind
from app.risk_engine.severity import Severity, SeverityBand

__all__ = [
    "CATALOG",
    "Remediation",
    "RuleDefinition",
    "RuleId",
    "SubjectKind",
    "definition_for",
]


class RuleId(StrEnum):
    """Stable rule identifiers. These are stored on every finding; a rename is a migration."""

    EVERYONE_BROAD_ACCESS = "everyone_broad_access"
    AUTHENTICATED_USERS_BROAD_ACCESS = "authenticated_users_broad_access"
    DOMAIN_USERS_BROAD_ACCESS = "domain_users_broad_access"
    DIRECT_USER_ACE = "direct_user_ace"
    DISABLED_PRINCIPAL_RETAINS_ACCESS = "disabled_principal_retains_access"
    UNRESOLVED_SID_ON_ACL = "unresolved_sid_on_acl"
    BROKEN_INHERITANCE = "broken_inheritance"
    DEEP_GROUP_NESTING = "deep_group_nesting"
    REDUNDANT_ACCESS_PATHS = "redundant_access_paths"
    EMPTY_PERMISSION_BEARING_GROUP = "empty_permission_bearing_group"
    BROAD_ACCESS_ON_SENSITIVE_RESOURCE = "broad_access_on_sensitive_resource"


class SubjectKind(StrEnum):
    """What a rule's findings are *about*, which is what a report groups them by."""

    RESOURCE = "resource"
    """A directory or a share. The finding names a place."""

    PRINCIPAL = "principal"
    """A user or a group. The finding names an identity."""

    ACCESS = "access"
    """A pairing: this principal, at this resource. Neither half is the finding on its own."""


@dataclass(frozen=True, slots=True)
class Remediation:
    """What an administrator would do about a finding, and what it might cost.

    ``caution`` is not decoration. Every one of these changes removes access somebody
    currently has, and the most common way an access review does damage is by removing a
    grant that a service account was quietly relying on. A finding that says what to do and
    not what to check first is an outage waiting for a maintenance window.
    """

    summary: str
    steps: tuple[str, ...]
    caution: str | None = None


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    """Everything about a rule except its predicate."""

    id: RuleId
    version: str
    """Changes when the predicate changes. Prose edits do not change it."""

    title: str
    detects: str
    """One sentence: what shape in the data this rule matches."""

    matters_because: str
    """One sentence: why that shape is worth an operator's attention."""

    remediation: Remediation
    subject_kind: SubjectKind
    bands: tuple[SeverityBand, ...]
    default_severities: Mapping[SeverityBand, Severity]
    depends_on: frozenset[FactKind]
    """Which collected kinds this rule reads. An incremental re-evaluation runs a rule only
    when a run changed something of a kind the rule depends on."""

    default_options: Mapping[str, Any] = field(default_factory=dict)
    """The rule's thresholds at their shipped values.

    Held here beside the default severities so that everything about a rule an installation
    may change has one place it is declared, and :class:`RiskConfiguration` is purely the
    overrides plus the merge. A rule reading an option this mapping does not declare is a
    programming error, and :meth:`RuleConfig.option` raises rather than defaulting — an
    unrecognized threshold silently reading as zero is how a rule stops firing without
    anybody noticing.
    """

    default_enabled: bool = True
    requires_configuration: bool = False
    """True when the rule can produce nothing at all until an operator configures it. The
    sensitive-resource rule is the only one, and it is enabled by default *and* silent by
    default, which is the combination that cannot be mistaken for a guarantee."""

    def __post_init__(self) -> None:
        missing = [band for band in self.bands if band not in self.default_severities]
        if missing:
            raise ValueError(
                f"Rule {self.id.value!r} declares bands {[b.value for b in missing]} with no "
                "default severity. Every band a rule can report must have one, or a finding "
                "would have no severity and the engine would have to invent it."
            )
        extra = [band for band in self.default_severities if band not in self.bands]
        if extra:
            raise ValueError(
                f"Rule {self.id.value!r} gives a default severity for bands "
                f"{[b.value for b in extra]} it never reports."
            )


_DEFINITIONS: Final[tuple[RuleDefinition, ...]] = (
    RuleDefinition(
        id=RuleId.EVERYONE_BROAD_ACCESS,
        version="1.0",
        title="Everyone can reach this resource",
        detects=(
            "An allow entry naming Everyone (S-1-1-0) that reaches at least the configured "
            "minimum category, or a security descriptor with no DACL at all."
        ),
        matters_because=(
            "Everyone includes every authenticated account and, where anonymous access is "
            "permitted, accounts that authenticated as nobody. No group membership limits "
            "it and no directory change removes it."
        ),
        remediation=Remediation(
            summary="Replace the Everyone entry with the group that actually needs the access.",
            steps=(
                "Identify who is using the resource before changing anything: an Everyone "
                "entry is often the only grant a long-running service has.",
                "Create or choose a security group that names the intended population.",
                "Grant that group the rights the work requires, and no more.",
                "Remove the Everyone entry, and confirm the resource is still reachable by "
                "the people who need it.",
            ),
            caution=(
                "On a share root, removing Everyone from the share ACL can cut off access "
                "that the NTFS ACL would otherwise permit, including for administrators."
            ),
        ),
        subject_kind=SubjectKind.RESOURCE,
        bands=(
            SeverityBand.READ,
            SeverityBand.WRITE,
            SeverityBand.FULL_CONTROL,
            SeverityBand.NULL_DACL,
        ),
        default_severities={
            SeverityBand.READ: Severity.HIGH,
            SeverityBand.WRITE: Severity.CRITICAL,
            SeverityBand.FULL_CONTROL: Severity.CRITICAL,
            SeverityBand.NULL_DACL: Severity.CRITICAL,
        },
        default_options={"minimum_category": RightsCategory.READ.value},
        depends_on=frozenset(
            {FactKind.NTFS_RESOURCE, FactKind.NTFS_ACE, FactKind.SMB_SHARE, FactKind.SMB_ACE}
        ),
    ),
    RuleDefinition(
        id=RuleId.AUTHENTICATED_USERS_BROAD_ACCESS,
        version="1.0",
        title="Every authenticated account can reach this resource",
        detects=(
            "An allow entry naming Authenticated Users (S-1-5-11) that reaches at least the "
            "configured minimum category."
        ),
        matters_because=(
            "Authenticated Users is every account that logged on successfully, including "
            "computer accounts and accounts from trusted domains. It is narrower than "
            "Everyone and still wider than any group an access review would approve."
        ),
        remediation=Remediation(
            summary=(
                "Narrow the entry to the group that needs the access, or reduce the rights "
                "it grants."
            ),
            steps=(
                "Determine whether the resource is genuinely intended for the whole "
                "organization. Some are; say so explicitly rather than leaving it implied.",
                "If it is not, replace the entry with a security group that names the "
                "intended population.",
                "If it is, consider reducing the rights to read so that a wide audience "
                "cannot alter shared content.",
            ),
            caution=(
                "Computer accounts are authenticated users. Removing this entry can affect "
                "scheduled tasks and services running as a machine identity."
            ),
        ),
        subject_kind=SubjectKind.RESOURCE,
        bands=(SeverityBand.READ, SeverityBand.WRITE, SeverityBand.FULL_CONTROL),
        default_severities={
            SeverityBand.READ: Severity.MEDIUM,
            SeverityBand.WRITE: Severity.HIGH,
            SeverityBand.FULL_CONTROL: Severity.HIGH,
        },
        default_options={"minimum_category": RightsCategory.READ.value},
        depends_on=frozenset(
            {FactKind.NTFS_RESOURCE, FactKind.NTFS_ACE, FactKind.SMB_SHARE, FactKind.SMB_ACE}
        ),
    ),
    RuleDefinition(
        id=RuleId.DOMAIN_USERS_BROAD_ACCESS,
        version="1.0",
        title="Every account in the domain can reach this resource",
        detects=(
            "An allow entry naming a domain's Domain Users group (the domain SID with RID "
            "513) that reaches at least the configured minimum category."
        ),
        matters_because=(
            "Domain Users is the default primary group of every account created in the "
            "domain, so membership is automatic and is never reviewed. An entry naming it "
            "grants access to accounts that do not exist yet."
        ),
        remediation=Remediation(
            summary="Replace Domain Users with a group whose membership somebody maintains.",
            steps=(
                "Check whether the grant is deliberate: some resources really are for the "
                "whole domain, and those should say so.",
                "Otherwise create a security group for the intended population and move the "
                "grant to it.",
                "Remove the Domain Users entry once the replacement is in place.",
            ),
            caution=(
                "Domain Users is a primary group, so membership does not appear in the "
                "member list of the group in most directory tools. Removing the entry can "
                "affect more accounts than a member listing suggests."
            ),
        ),
        subject_kind=SubjectKind.RESOURCE,
        bands=(SeverityBand.READ, SeverityBand.WRITE, SeverityBand.FULL_CONTROL),
        default_severities={
            SeverityBand.READ: Severity.LOW,
            SeverityBand.WRITE: Severity.MEDIUM,
            SeverityBand.FULL_CONTROL: Severity.HIGH,
        },
        default_options={"minimum_category": RightsCategory.READ.value},
        depends_on=frozenset(
            {FactKind.NTFS_RESOURCE, FactKind.NTFS_ACE, FactKind.SMB_SHARE, FactKind.SMB_ACE}
        ),
    ),
    RuleDefinition(
        id=RuleId.DIRECT_USER_ACE,
        version="1.0",
        title="A user account is named directly on an access control list",
        detects=(
            "An allow entry whose trustee ADG has described as a user or managed service "
            "account rather than a group."
        ),
        matters_because=(
            "An entry naming a person is invisible to group-based access review, survives "
            "the person changing roles, and has to be found and removed one resource at a "
            "time when they leave."
        ),
        remediation=Remediation(
            summary="Move the grant to a group and remove the direct entry.",
            steps=(
                "Identify the role the access represents rather than the person holding it.",
                "Grant a security group for that role the rights the entry carries.",
                "Add the user to the group, confirm access still works, then remove the "
                "direct entry.",
            ),
            caution=(
                "Check the rights the direct entry carries before replacing it. It may grant "
                "more than the group it is being folded into, and the difference is what the "
                "person will lose."
            ),
        ),
        subject_kind=SubjectKind.ACCESS,
        bands=(SeverityBand.DEFAULT,),
        default_severities={SeverityBand.DEFAULT: Severity.LOW},
        depends_on=frozenset(
            {
                FactKind.NTFS_RESOURCE,
                FactKind.NTFS_ACE,
                FactKind.SMB_SHARE,
                FactKind.SMB_ACE,
                FactKind.PRINCIPAL,
            }
        ),
    ),
    RuleDefinition(
        id=RuleId.DISABLED_PRINCIPAL_RETAINS_ACCESS,
        version="1.0",
        title="A disabled account still holds granted access",
        detects=(
            "A principal ADG has described as disabled that is named on an access control "
            "list, directly or through a group it belongs to."
        ),
        matters_because=(
            "Disabling an account stops it logging on; it removes nothing from any access "
            "control list. The grant is still there to be inherited by whoever the account "
            "is re-enabled for, and it still counts toward who can reach the resource."
        ),
        remediation=Remediation(
            summary=(
                "Remove the account's access as part of the leaver process, not just its logon."
            ),
            steps=(
                "Confirm the account is genuinely dormant rather than disabled temporarily.",
                "Remove it from the groups that grant the access, and remove any entry that "
                "names it directly.",
                "Where the account is a service identity, find what was using it before "
                "removing anything.",
            ),
            caution=(
                "A disabled account that is a group's only member may be the reason the "
                "group exists. Removing it can leave a group that grants access to nobody, "
                "which this engine will then report separately."
            ),
        ),
        subject_kind=SubjectKind.ACCESS,
        bands=(SeverityBand.DEFAULT,),
        default_severities={SeverityBand.DEFAULT: Severity.MEDIUM},
        default_options={"max_membership_depth": 8},
        depends_on=frozenset(
            {
                FactKind.NTFS_RESOURCE,
                FactKind.NTFS_ACE,
                FactKind.SMB_SHARE,
                FactKind.SMB_ACE,
                FactKind.PRINCIPAL,
                FactKind.MEMBERSHIP_EDGE,
            }
        ),
    ),
    RuleDefinition(
        id=RuleId.UNRESOLVED_SID_ON_ACL,
        version="1.0",
        title="An access control list names a SID nothing can resolve",
        detects=(
            "An entry whose trustee has no principal record, or whose record says the "
            "account is unresolved or deleted."
        ),
        matters_because=(
            "The entry is still evaluated by Windows. If the SID belongs to a deleted "
            "account it grants nothing and is clutter; if it belongs to a domain ADG cannot "
            "query, it grants access to a population nobody here can enumerate."
        ),
        remediation=Remediation(
            summary="Establish what the SID was before removing it.",
            steps=(
                "Check whether the SID belongs to a trusted domain that was simply not "
                "collected. A collection gap and a deleted account look identical on an ACL.",
                "If the account is deleted, remove the entry.",
                "If the domain is real, either collect it or record the entry as accepted.",
            ),
            caution=(
                "An unresolvable SID is not automatically dead. Removing an entry that names "
                "a live principal in an uncollected domain removes that principal's access."
            ),
        ),
        subject_kind=SubjectKind.ACCESS,
        bands=(SeverityBand.DEFAULT,),
        default_severities={SeverityBand.DEFAULT: Severity.MEDIUM},
        depends_on=frozenset(
            {
                FactKind.NTFS_RESOURCE,
                FactKind.NTFS_ACE,
                FactKind.SMB_SHARE,
                FactKind.SMB_ACE,
                FactKind.PRINCIPAL,
            }
        ),
    ),
    RuleDefinition(
        id=RuleId.BROKEN_INHERITANCE,
        version="1.0",
        title="Permissions change at this directory",
        detects=(
            "A directory below a share root whose DACL is protected from inheritance, or "
            "whose DACL differs from what its parent projects onto a child."
        ),
        matters_because=(
            "Every place permissions change is a place a later change to the parent will not "
            "reach. A tree with many of them cannot be reasoned about from the top, which is "
            "how access quietly diverges from what anybody approved."
        ),
        remediation=Remediation(
            summary="Decide whether the divergence is intended, and record it if it is.",
            steps=(
                "Compare the directory's entries with what its parent grants.",
                "Where the divergence is deliberate, leave it and note why.",
                "Where it is accidental, re-enable inheritance and remove the entries that "
                "duplicate what the parent already grants.",
            ),
            caution=(
                "Re-enabling inheritance replaces this directory's permissions with the "
                "parent's. Anyone whose only grant is an explicit entry here loses access."
            ),
        ),
        subject_kind=SubjectKind.RESOURCE,
        bands=(SeverityBand.PROTECTED, SeverityBand.DIVERGED),
        default_severities={
            SeverityBand.PROTECTED: Severity.LOW,
            SeverityBand.DIVERGED: Severity.INFORMATIONAL,
        },
        default_options={"include_share_roots": False, "include_diverged": True},
        depends_on=frozenset({FactKind.NTFS_RESOURCE}),
    ),
    RuleDefinition(
        id=RuleId.DEEP_GROUP_NESTING,
        version="1.0",
        title="A group named on an access control list nests too deeply",
        detects=(
            "A chain of group memberships below a trustee named on an access control list "
            "that is longer than the configured maximum depth."
        ),
        matters_because=(
            "Nobody reading the resource's permissions can see who the deep members are. "
            "Access granted at the top is inherited by populations maintained by people who "
            "have never heard of the resource."
        ),
        remediation=Remediation(
            summary="Flatten the chain so that the granted group's membership is legible.",
            steps=(
                "Walk the chain and establish which level actually represents the intended "
                "population.",
                "Remove the intermediate groups that add no meaning of their own.",
                "Where the nesting is deliberate — a role hierarchy, for instance — record "
                "it rather than removing it.",
            ),
            caution=(
                "Removing a nested group removes access for everybody inside it, including "
                "populations the resource's owner has never seen."
            ),
        ),
        subject_kind=SubjectKind.PRINCIPAL,
        bands=(SeverityBand.DEFAULT,),
        default_severities={SeverityBand.DEFAULT: Severity.LOW},
        default_options={"max_depth": 3},
        depends_on=frozenset(
            {
                FactKind.NTFS_ACE,
                FactKind.SMB_ACE,
                FactKind.MEMBERSHIP_EDGE,
                FactKind.PRINCIPAL,
            }
        ),
    ),
    RuleDefinition(
        id=RuleId.REDUNDANT_ACCESS_PATHS,
        version="1.0",
        title="One principal reaches a resource by several independent routes",
        detects=(
            "A principal that belongs to more than the configured number of distinct "
            "trustees named on one resource's access control lists."
        ),
        matters_because=(
            "Removing one grant changes nothing while another still stands. An access review "
            "that removes a membership and does not re-check believes it revoked access that "
            "is still there."
        ),
        remediation=Remediation(
            summary="Reduce the routes to one, so that removing it actually removes access.",
            steps=(
                "List the trustees that reach this principal and establish which one "
                "represents the intended grant.",
                "Remove the memberships or entries that duplicate it.",
                "Re-check effective access afterwards rather than assuming the removal took.",
            ),
            caution=(
                "The routes may grant different rights. Keeping the wrong one can silently "
                "reduce or widen what the principal can do."
            ),
        ),
        subject_kind=SubjectKind.ACCESS,
        bands=(SeverityBand.DEFAULT,),
        default_severities={SeverityBand.DEFAULT: Severity.INFORMATIONAL},
        default_options={"minimum_paths": 2, "max_membership_depth": 8},
        depends_on=frozenset(
            {
                FactKind.NTFS_RESOURCE,
                FactKind.NTFS_ACE,
                FactKind.SMB_SHARE,
                FactKind.SMB_ACE,
                FactKind.MEMBERSHIP_EDGE,
                FactKind.PRINCIPAL,
            }
        ),
    ),
    RuleDefinition(
        id=RuleId.EMPTY_PERMISSION_BEARING_GROUP,
        version="1.0",
        title="A group that grants access has no members",
        detects=(
            "A group named on an access control list whose membership a reconciling run "
            "enumerated and found empty."
        ),
        matters_because=(
            "The grant is dormant rather than absent. Adding one account to the group grants "
            "it everything the group reaches, and the change looks like an ordinary group "
            "membership rather than a permission change."
        ),
        remediation=Remediation(
            summary="Remove the entry, or record why the empty group is kept.",
            steps=(
                "Establish whether the group is being kept deliberately — a seasonal team, "
                "or a role nobody currently holds.",
                "If it is obsolete, remove the entry from the access control list and then "
                "the group.",
                "If it is deliberate, note it so the next review does not repeat the work.",
            ),
            caution=(
                "Do not act on this finding for a group whose membership has not been "
                "collected. ADG does not report those, and a tool that did would be "
                "reporting the absence of data as the absence of members."
            ),
        ),
        subject_kind=SubjectKind.PRINCIPAL,
        bands=(SeverityBand.DEFAULT,),
        default_severities={SeverityBand.DEFAULT: Severity.LOW},
        depends_on=frozenset(
            {
                FactKind.NTFS_ACE,
                FactKind.SMB_ACE,
                FactKind.MEMBERSHIP_EDGE,
                FactKind.PRINCIPAL,
            }
        ),
    ),
    RuleDefinition(
        id=RuleId.BROAD_ACCESS_ON_SENSITIVE_RESOURCE,
        version="1.0",
        title="A broad group holds write or control access to a resource marked sensitive",
        detects=(
            "An allow entry reaching Modify or Full Control, naming a trustee the "
            "configuration lists as broad, on a resource the configuration marks sensitive."
        ),
        matters_because=(
            "Where the data is known to matter, the population that can change it should be "
            "named and small. This is the one rule whose severity rests on something ADG "
            "cannot observe, which is why the resource has to be marked by hand."
        ),
        remediation=Remediation(
            summary=("Reduce the grant to read, or replace the broad trustee with a named group."),
            steps=(
                "Confirm the sensitivity marking is still correct.",
                "Establish who genuinely needs to change the content, as opposed to read it.",
                "Grant that group Modify and reduce everybody else to read.",
            ),
            caution=(
                "Backup and archiving identities frequently need more than read. Identify "
                "them before narrowing a grant they depend on."
            ),
        ),
        subject_kind=SubjectKind.ACCESS,
        bands=(SeverityBand.WRITE, SeverityBand.FULL_CONTROL),
        default_severities={
            SeverityBand.WRITE: Severity.HIGH,
            SeverityBand.FULL_CONTROL: Severity.CRITICAL,
        },
        default_options={
            "minimum_category": RightsCategory.MODIFY.value,
            # Everyone, Authenticated Users, and BUILTIN\Users: trustees whose membership is
            # not a decision anybody made about this resource.
            "broad_trustee_sids": ["S-1-1-0", "S-1-5-11", "S-1-5-32-545"],
            # Domain Users (513) and Domain Computers (515), whose SIDs differ per domain and
            # so cannot be listed above.
            "broad_trustee_rids": [513, 515],
        },
        depends_on=frozenset(
            {
                FactKind.NTFS_RESOURCE,
                FactKind.NTFS_ACE,
                FactKind.SMB_SHARE,
                FactKind.SMB_ACE,
                FactKind.PRINCIPAL,
            }
        ),
        requires_configuration=True,
    ),
)


CATALOG: Final[Mapping[RuleId, RuleDefinition]] = {
    definition.id: definition for definition in _DEFINITIONS
}
"""Every rule ADG knows about, keyed by identifier.

Exhaustive by construction: :mod:`app.risk_engine.rules` builds its registry from this
mapping, and a definition with no implementation — or an implementation with no definition —
fails at import rather than producing a rule that can never fire or a finding whose severity
nothing can assign.
"""


def definition_for(rule_id: RuleId) -> RuleDefinition:
    """The catalog entry for ``rule_id``.

    Raises:
        KeyError: no such rule. Left as a hard failure: a stored finding naming a rule this
            build does not have is a real inconsistency, and rendering it with a placeholder
            would present an unexplained severity as an explained one.
    """
    return CATALOG[rule_id]
