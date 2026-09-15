"""Application roles and the capabilities they grant.

This module is the whole of ADG's authorization model and it is deliberately boring: a
frozen table from role to capability, and a pure function that folds a set of roles into a
set of capabilities. No database, no request, no framework. Every route and every test
reads the same table, so "what may an auditor do?" has exactly one answer in the codebase.

Two rules are structural rather than conventional:

* **A role ADG does not know grants nothing.** Tokens from an identity provider carry
  whatever the tenant put in them. An unrecognized value is dropped, never guessed at.
* **A reserved role grants nothing, even to itself.** ``REMEDIATOR`` exists so that a
  tenant can provision the app role ahead of the feature, and so the name cannot be
  reused for something else. Until remediation ships, holding it must be indistinguishable
  from not holding it — see :data:`RESERVED_ROLES`.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ACTIVE_ROLES",
    "RESERVED_CAPABILITIES",
    "RESERVED_ROLES",
    "ROLE_CAPABILITIES",
    "Capability",
    "Role",
    "capabilities_for",
    "parse_roles",
]


class Role(StrEnum):
    """An application role. The value is the string an identity provider sends."""

    VIEWER = "viewer"
    AUDITOR = "auditor"
    ADMIN = "admin"
    #: Answers review items they have been assigned. Deliberately cannot create or close a
    #: campaign: see the separation-of-duties note below the table.
    REVIEWER = "reviewer"
    #: Creates, scopes, assigns and closes campaigns, and records resource ownership.
    #: Deliberately cannot answer a review item.
    GOVERNANCE_ADMIN = "governance_admin"
    #: Turns review decisions and risk findings into change plans, and asks for approval.
    #: Deliberately cannot approve one: see the separation-of-duties note below the table.
    REMEDIATION_PLANNER = "remediation_planner"
    #: Approves or rejects a change plan somebody else wrote. Deliberately cannot write one,
    #: and deliberately cannot export the approved result.
    REMEDIATION_APPROVER = "remediation_approver"
    #: Reserved for the future ability to *carry out* a change. Grants nothing today, and
    #: there is nothing for it to grant: ADG has no write adapter (ADR-0035). Kept so the
    #: name cannot be reused, and so a tenant can provision the app role ahead of a feature
    #: that does not exist.
    REMEDIATOR = "remediator"


class Capability(StrEnum):
    """One thing a request may be permitted to do.

    Routes depend on capabilities, never on roles: a capability says what the endpoint
    needs, and the role table says who has it. Adding a role then cannot silently change
    what an endpoint requires.
    """

    RESOURCES_READ = "resources:read"
    IDENTITIES_READ = "identities:read"
    ACCESS_READ = "access:read"
    RISKS_READ = "risks:read"
    #: Read alerts, the watches that raised them, and the delivery queue's state. Separate
    #: from RISKS_READ because an alert says more than a finding does: it names who was
    #: told, when, and what a watch was configured to care about -- which is a statement
    #: about the organization rather than about the estate.
    ALERTS_READ = "alerts:read"
    #: Create, edit and remove watches, and drain the delivery queue. An operations
    #: capability: a watch decides who gets woken up, and somebody who could quietly disable
    #: the watch on the payroll share could make an exposure land in nobody's inbox.
    ALERTS_MANAGE = "alerts:manage"
    CHANGES_READ = "changes:read"
    COLLECTORS_READ = "collectors:read"
    COLLECTORS_INGEST = "collectors:ingest"
    SEARCH = "search"
    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"
    #: Read campaigns, items, decisions, ownership records and the audit trail. A more
    #: sensitive disclosure than the estate itself: a rationale can name a person and say
    #: something about them that the ACLs never would.
    GOVERNANCE_READ = "governance:read"
    #: Record an attestation. Admits a request to the route and grants authority over no
    #: particular item -- the second gate is the assignment, enforced in
    #: :mod:`app.governance.service` against the database.
    GOVERNANCE_REVIEW = "governance:review"
    #: Create, scope, generate, assign, close and cancel campaigns; record ownership.
    GOVERNANCE_MANAGE = "governance:manage"
    #: Read a stored what-if proposal and the report it produced. Held from ``auditor``
    #: upward and deliberately **not** by a plain viewer: a simulation discloses *potential*
    #: access -- "put this account in that group and it reaches the payroll share" -- which
    #: is a route map for privilege escalation assembled out of answers a reader would
    #: otherwise have to compose by hand. See ADR-0034.
    SIMULATIONS_READ = "simulations:read"
    #: Compute a simulation, store a proposal, and delete one. Separate from the read
    #: because resolving a proposal's affected scope is the most expensive request this API
    #: serves: an account that may read what somebody already ran must not thereby be able
    #: to make the estate resolve a thousand new pairs.
    SIMULATIONS_RUN = "simulations:run"
    #: Read change plans, their steps, their blast radius, their approvals and their signed
    #: exports. Held from ``auditor`` upward: whether a change was simulated and approved
    #: before it was performed is exactly what an audit asks, and it cannot be asked of
    #: records the auditor cannot read.
    REMEDIATION_READ = "remediation:read"
    #: Write a change plan, attach its blast radius, and submit it for approval. Writes
    #: nothing to Windows -- a plan is a document -- but it decides what somebody will be
    #: asked to approve, which is why it is held separately from approving.
    REMEDIATION_PLAN = "remediation:plan"
    #: Approve or reject a plan. Held by no role that also holds REMEDIATION_PLAN
    #: (ADR-0038): an account able to write a plan and approve it could produce a fully
    #: documented, fully audited removal of anybody's access with one person's involvement,
    #: and the trail would look impeccable.
    REMEDIATION_APPROVE = "remediation:approve"
    #: Produce the signed change plan a human administrator executes. The dangerous artifact
    #: of this phase, and deliberately a third pair of hands: whoever performs the work is
    #: neither whoever asked for it nor whoever approved it.
    REMEDIATION_EXPORT = "remediation:export"
    #: Reserved alongside :attr:`Role.REMEDIATOR`. No role grants it, and nothing implements
    #: it: there is no code path in ADG that writes to Active Directory, to a share, or to an
    #: NTFS descriptor. See :mod:`app.remediation.executor` and ADR-0035.
    REMEDIATION_EXECUTE = "remediation:execute"


#: Roles that exist but are not yet active. They are recognized on a token — so that the
#: principal can be told the role is inactive rather than silently ignored — and they
#: contribute no capabilities.
RESERVED_ROLES: frozenset[Role] = frozenset({Role.REMEDIATOR})

#: Capabilities no active role grants. Present so that a route can be written against the
#: eventual capability and be provably unreachable until the role is activated.
RESERVED_CAPABILITIES: frozenset[Capability] = frozenset({Capability.REMEDIATION_EXECUTE})

ACTIVE_ROLES: frozenset[Role] = frozenset(Role) - RESERVED_ROLES

_VIEWER: frozenset[Capability] = frozenset(
    {
        Capability.RESOURCES_READ,
        Capability.IDENTITIES_READ,
        Capability.ACCESS_READ,
        Capability.RISKS_READ,
        Capability.CHANGES_READ,
        # A viewer must be able to see whether collection succeeded. Otherwise an empty
        # page is indistinguishable from a failed scan, which is the single most
        # dangerous misreading this product can produce.
        Capability.COLLECTORS_READ,
        Capability.SEARCH,
        # A viewer may read alerts and may not configure one. Seeing that the payroll share
        # was edited is the same disclosure class as seeing the edit in the change feed,
        # which a viewer already has.
        Capability.ALERTS_READ,
    }
)

_AUDITOR: frozenset[Capability] = _VIEWER | {
    Capability.SETTINGS_READ,
    # An auditor's job is to verify that attestations happened and by whom. Reading them
    # is the whole of that job, and it is strictly reading: no auditor can create a
    # campaign or answer an item.
    Capability.GOVERNANCE_READ,
    # Reading a what-if somebody else ran. An auditor's question is whether a proposed
    # change was evaluated before it was signed off, and the report is the evidence. The
    # run capability is deliberately not here: an auditor reads what happened.
    Capability.SIMULATIONS_READ,
    # And the plan that what-if belonged to: who asked for the change, who approved it,
    # against which digest, and what was signed. Strictly reading -- an auditor writes no
    # plan, approves none and exports none.
    Capability.REMEDIATION_READ,
}

#: Answers items. Holds the estate-reading capabilities because a reviewer cannot judge a
#: grant without seeing what it reaches, and **not** GOVERNANCE_MANAGE: a reviewer who could
#: scope and close their own campaign could decide what they would be asked.
_REVIEWER: frozenset[Capability] = _VIEWER | {
    Capability.GOVERNANCE_READ,
    Capability.GOVERNANCE_REVIEW,
}

#: Runs campaigns, and **cannot answer one**. The omission of GOVERNANCE_REVIEW is the
#: separation of duties: whoever chooses the questions does not also give the answers. A
#: person who must do both is given both roles, which is a visible assignment rather than a
#: silent property of one.
_GOVERNANCE_ADMIN: frozenset[Capability] = _VIEWER | {
    Capability.GOVERNANCE_READ,
    Capability.GOVERNANCE_MANAGE,
    # A review that concludes "this group should come off the payroll share" is worth
    # nothing until somebody knows what taking it off would do. Running the what-if is part
    # of the same job, and it writes nothing to Windows.
    Capability.SIMULATIONS_READ,
    Capability.SIMULATIONS_RUN,
    # A campaign that concludes "this group should come off the payroll share" and stops
    # there has produced a finding nobody can act on. Turning its decisions into a precise,
    # simulated plan is the same job finishing -- and a plan is a document, so this grants no
    # authority over the estate. Approving it belongs to somebody else (ADR-0038).
    Capability.REMEDIATION_READ,
    Capability.REMEDIATION_PLAN,
}

#: Writes change plans and **cannot approve one**. Holds the estate-reading and simulation
#: capabilities because a plan cannot honestly be written without seeing what the change
#: would do.
_REMEDIATION_PLANNER: frozenset[Capability] = _VIEWER | {
    Capability.GOVERNANCE_READ,
    Capability.SIMULATIONS_READ,
    Capability.SIMULATIONS_RUN,
    Capability.REMEDIATION_READ,
    Capability.REMEDIATION_PLAN,
}

#: Approves change plans, and **cannot write one** or export the approved result. Holds
#: SIMULATIONS_READ because approving a change without reading its blast radius is the
#: failure this phase exists to prevent; it deliberately does **not** hold SIMULATIONS_RUN,
#: because an approver re-running the what-if with narrower bounds until the impact list
#: looks acceptable is the same failure wearing a different hat.
_REMEDIATION_APPROVER: frozenset[Capability] = _VIEWER | {
    Capability.GOVERNANCE_READ,
    Capability.SIMULATIONS_READ,
    Capability.REMEDIATION_READ,
    Capability.REMEDIATION_APPROVE,
}

_ADMIN: frozenset[Capability] = _AUDITOR | {
    Capability.SETTINGS_WRITE,
    # Watches and the delivery queue are operations, which is what this role is for. A
    # governance administrator deliberately does not get it: choosing who is paged is not
    # part of running an access review, and the two roles failing separately is the point.
    Capability.ALERTS_MANAGE,
    # Ingestion is an administrative act. Collectors normally authenticate with their own
    # key (see app.auth.dependencies), but an operator replaying a payload by hand needs a
    # human identity that can do it.
    Capability.COLLECTORS_INGEST,
    # Planning a permission change is an operations act, and this is the role that plans
    # one. Running a simulation still writes nothing to Active Directory, to a share, or to
    # an NTFS descriptor -- see ADR-0034 -- so this grants no authority over the estate.
    Capability.SIMULATIONS_RUN,
    # The signed change plan is produced by whoever is going to carry the work out, and that
    # is this role. Deliberately **not** REMEDIATION_PLAN or REMEDIATION_APPROVE: a platform
    # administrator who could write a plan, approve it and export it would be the single
    # account ADR-0038 exists to rule out. Export is gated on an approval somebody else
    # recorded, so this capability on its own produces nothing.
    Capability.REMEDIATION_EXPORT,
}

#: The authorization model, in one place.
#:
#: ``admin`` is a *platform* administrator: it configures the server and may replay a
#: collector payload. It deliberately grants neither GOVERNANCE_MANAGE nor GOVERNANCE_REVIEW.
#: Running an access review is a compliance function, not an operations one, and letting
#: whoever runs the server quietly create and close attestation campaigns is precisely what
#: an auditor would object to. This is a control against accident and casual misuse, not
#: against a determined operator -- anyone with the database credentials can do anything --
#: and ADR-0029 says so rather than implying more.
ROLE_CAPABILITIES: dict[Role, frozenset[Capability]] = {
    Role.VIEWER: _VIEWER,
    Role.AUDITOR: _AUDITOR,
    Role.ADMIN: _ADMIN,
    Role.REVIEWER: _REVIEWER,
    Role.GOVERNANCE_ADMIN: _GOVERNANCE_ADMIN,
    Role.REMEDIATION_PLANNER: _REMEDIATION_PLANNER,
    Role.REMEDIATION_APPROVER: _REMEDIATION_APPROVER,
    Role.REMEDIATOR: frozenset(),
}


def capabilities_for(roles: frozenset[Role]) -> frozenset[Capability]:
    """Every capability the given roles grant together.

    Reserved roles are accepted and contribute nothing, so a principal holding only
    reserved roles is authenticated with no authority at all.
    """
    granted: frozenset[Capability] = frozenset()
    for role in roles:
        if role in RESERVED_ROLES:
            continue
        granted |= ROLE_CAPABILITIES[role]
    return granted


def parse_roles(values: list[str]) -> tuple[frozenset[Role], tuple[str, ...]]:
    """Split raw role strings into the roles ADG knows and the ones it does not.

    Matching is case-insensitive and whitespace-tolerant because tenant configuration is
    typed by hand. Unknown values are returned rather than discarded so that the caller can
    log them: an administrator who assigns ``Auditors`` instead of ``auditor`` deserves to
    find out from a log line, not from a user reporting a blank screen.
    """
    known: set[Role] = set()
    unknown: list[str] = []
    for raw in values:
        candidate = raw.strip().casefold()
        if not candidate:
            continue
        try:
            known.add(Role(candidate))
        except ValueError:
            unknown.append(raw.strip())
    return frozenset(known), tuple(dict.fromkeys(unknown))
