"""The projector's rules over facts held in memory: what each fact establishes, and how surely.

Every test here is a claim about what a stored fact means. The ones worth reading twice are the
places a naive projector is wrong:

* an Allow entry whose mask merely *contains* a bit is not a grant if a Deny every holder
  carries comes first, and is only *conditionally* one if a Deny some holders carry does;
* an ``INHERIT_ONLY`` entry and a ``CREATOR OWNER`` entry grant nothing on the object;
* the owner's implicit ``WRITE_DAC`` survives every Deny, and is replaced, not supplemented,
  by an ``OWNER RIGHTS`` entry;
* a host-local membership without a host is refused, never stored unscoped;
* a foreign or unresolvable member is *unresolved*, and stays so however the relationship
  is read.
"""

from __future__ import annotations

import datetime as dt
import itertools
import uuid
from typing import Any

import pytest

from app.domain.access import AceFlag, AceSource, AceType, NtfsRight
from app.domain.identity import PrincipalKind
from app.domain.membership import MembershipEdgeKind
from app.domain.relationships import (
    DerivationClass,
    EvidenceKind,
    EvidenceRole,
    NodeKind,
    NodeRef,
    RelationshipAssertion,
    RelationshipCondition,
    RelationshipConfidence,
    RelationshipQualifier,
    RelationshipScope,
    RelationshipType,
    confidence_of,
    definition_of,
)
from app.relationships.rules import (
    CONTROL_RIGHTS,
    RULES,
    UNDERIVED,
    AceFact,
    DerivationContext,
    EdgeFact,
    FactReading,
    PrincipalFact,
    ResourceFact,
    derive_edge,
    derive_principal,
    derive_resource,
)

RUN = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
T0 = dt.datetime(2026, 10, 14, 8, 0, tzinfo=dt.UTC)
CONTEXT = DerivationContext(basis_token="ab" * 16, derived_at=T0 + dt.timedelta(hours=1))
DOMAIN = "S-1-5-21-1004336348-1177238915-682003330"
FOREIGN = "S-1-5-21-3000000001-3000000002-3000000003"
ALICE = f"{DOMAIN}-1104"
BOB = f"{DOMAIN}-1105"
FINANCE_TEAM = f"{DOMAIN}-1201"
FINANCE_RW = f"{DOMAIN}-1202"
DOMAIN_USERS = f"{DOMAIN}-513"
EVERYONE = "S-1-1-0"
AUTHENTICATED_USERS = "S-1-5-11"
OWNER_RIGHTS = "S-1-3-4"
CREATOR_OWNER = "S-1-3-0"
FULL = 0x001F01FF
MODIFY = 0x001301BF
READ_EXECUTE = 0x001200A9
PAYROLL = "\\\\fs01\\finance\\payroll"


# --- Builders ------------------------------------------------------------------------------


def reading(kind: EvidenceKind, key: str) -> FactReading:
    return FactReading(kind, key, RUN, T0)


def edge(
    group: str,
    member: str,
    kind: MembershipEdgeKind = MembershipEdgeKind.DIRECTORY_GROUP_MEMBER,
    *,
    host: str | None = None,
    member_kind: PrincipalKind | None = PrincipalKind.USER,
    foreign: bool = False,
    group_key: str | None = None,
) -> EdgeFact:
    key_of_group = group_key or (f"{host.casefold()}|{group}" if host else group)
    member_key = (
        f"{host.casefold()}|{member}" if host and member.startswith("S-1-5-32-") else member
    )
    return EdgeFact(
        reading=reading(EvidenceKind.MEMBERSHIP_EDGE, f"{key_of_group}->{member_key}|{kind.value}"),
        group_key=key_of_group,
        member_key=member_key,
        group_sid=group,
        member_sid=member,
        edge_kind=kind,
        host_key=host,
        member_kind=member_kind,
        is_foreign_security_principal=foreign,
    )


def ace(
    trustee: str,
    mask: int,
    order: int | None,
    *,
    ace_type: AceType = AceType.ALLOW,
    flags: int = 0x03,
    resource: str = PAYROLL,
    host: str = "fs01",
) -> AceFact:
    trustee_key = f"{host}|{trustee}" if trustee.startswith("S-1-5-32-") else trustee
    return AceFact(
        reading=reading(
            EvidenceKind.NTFS_ACE,
            f"{resource}|{trustee_key}|{ace_type.value}|0x{mask:08x}|0x{flags:02x}",
        ),
        trustee_key=trustee_key,
        trustee_sid=trustee,
        ace_type=ace_type,
        access_mask=mask,
        ace_flags=flags,
        source=AceSource.INHERITED if flags & AceFlag.INHERITED else AceSource.EXPLICIT,
        order_index=order,
    )


def resource(
    *aces: AceFact,
    owner: str | None = None,
    declared: int | None = None,
    dacl_present: bool = True,
    described: bool = True,
    key: str = PAYROLL,
) -> ResourceFact:
    return ResourceFact(
        resource_key=key,
        server_key="fs01",
        reading=reading(EvidenceKind.NTFS_RESOURCE, key) if described else None,
        owner_sid=owner,
        dacl_present=dacl_present,
        declared_ace_count=(len(aces) if declared is None else declared) if described else None,
        aces=tuple(aces),
    )


def found(
    assertions: tuple[RelationshipAssertion, ...],
    relationship_type: RelationshipType,
    source: str | None = None,
) -> list[RelationshipAssertion]:
    return [
        a
        for a in assertions
        if a.relationship_type is relationship_type and (source is None or a.source.key == source)
    ]


def premises(assertion: RelationshipAssertion) -> set[tuple[EvidenceKind, str]]:
    return {(e.kind, e.key) for e in assertion.evidence if e.role is EvidenceRole.PREMISE}


def contexts(assertion: RelationshipAssertion) -> set[tuple[EvidenceKind, str]]:
    return {(e.kind, e.key) for e in assertion.evidence if e.role is EvidenceRole.CONTEXT}


def types_from(source: str, *facts: Any) -> set[RelationshipType]:
    result = derive_resource(resource(*facts), CONTEXT)
    return {a.relationship_type for a in result.assertions if a.source.key == source}


# --- Rule registry ------------------------------------------------------------------------


class TestTheRuleRegistry:
    def test_every_rule_produces_only_types_the_taxonomy_lets_it_store(self) -> None:
        for spec in RULES.values():
            for relationship_type in spec.produces:
                definition = definition_of(relationship_type)
                assert spec.reads & definition.collection.premises, spec.rule

    def test_no_rule_produces_an_underived_type(self) -> None:
        produced = {t for spec in RULES.values() for t in spec.produces}
        assert not produced & UNDERIVED
        assert produced | UNDERIVED == set(RelationshipType)

    def test_the_taxonomy_names_a_projector_for_exactly_the_produced_types(self) -> None:
        from app.domain.relationships import DerivationStatus

        produced = {t for spec in RULES.values() for t in spec.produces}
        projected = {
            t
            for t in RelationshipType
            if definition_of(t).derivation.status is DerivationStatus.PROJECTOR
        }
        assert projected == produced

    def test_rule_ids_are_unique_and_stable(self) -> None:
        assert len(RULES) == len({spec.rule.rule_id for spec in RULES.values()})
        assert all(spec.rule.version == 1 for spec in RULES.values())


# --- Membership ---------------------------------------------------------------------------


class TestDirectMembership:
    def test_a_directory_edge_is_one_observed_direct_membership(self) -> None:
        fact = edge(FINANCE_TEAM, ALICE)
        (assertion,) = derive_edge(fact, CONTEXT).assertions
        assert assertion.relationship_type is RelationshipType.MEMBER_OF
        assert (assertion.source, assertion.target) == (
            NodeRef.principal(ALICE),
            NodeRef.principal(FINANCE_TEAM),
        )
        assert assertion.derivation_class is DerivationClass.OBSERVED
        assert assertion.scope is None
        assert str(assertion.rule) == "membership.directory_edge@1"
        assert dict(assertion.attributes) == {"edge_kind": "directory_group_member"}
        assert premises(assertion) == {(EvidenceKind.MEMBERSHIP_EDGE, fact.reading.key)}
        assert assertion.basis_token == CONTEXT.basis_token

    def test_group_to_group_is_the_same_hop(self) -> None:
        (assertion,) = derive_edge(
            edge(FINANCE_RW, FINANCE_TEAM, member_kind=PrincipalKind.DOMAIN_GROUP), CONTEXT
        ).assertions
        assert assertion.relationship_type is RelationshipType.MEMBER_OF
        assert assertion.source.key == FINANCE_TEAM

    def test_a_primary_group_is_its_own_type_and_never_member_of(self) -> None:
        derived = derive_edge(
            edge(DOMAIN_USERS, ALICE, MembershipEdgeKind.PRIMARY_GROUP), CONTEXT
        ).assertions
        assert [a.relationship_type for a in derived] == [RelationshipType.PRIMARY_GROUP_OF]
        assert str(derived[0].rule) == "membership.primary_group_edge@1"

    def test_a_source_stated_implicit_membership_is_a_hop_as_traversal_walks_it(self) -> None:
        (assertion,) = derive_edge(
            edge(AUTHENTICATED_USERS, ALICE, MembershipEdgeKind.WELL_KNOWN_IMPLICIT), CONTEXT
        ).assertions
        assert assertion.relationship_type is RelationshipType.MEMBER_OF
        assert assertion.attributes["edge_kind"] == "well_known_implicit"

    def test_a_cycle_is_three_edges_and_nothing_more(self) -> None:
        ring_a, ring_b, ring_c = (f"{DOMAIN}-{rid}" for rid in (3001, 3002, 3003))
        facts = [
            edge(ring_b, ring_a, member_kind=PrincipalKind.DOMAIN_GROUP),
            edge(ring_c, ring_b, member_kind=PrincipalKind.DOMAIN_GROUP),
            edge(ring_a, ring_c, member_kind=PrincipalKind.DOMAIN_GROUP),
        ]
        derived = [a for fact in facts for a in derive_edge(fact, CONTEXT).assertions]
        assert len(derived) == 3
        assert len({a.relationship_id for a in derived}) == 3
        assert {a.relationship_type for a in derived} == {RelationshipType.MEMBER_OF}

    def test_nested_membership_is_never_derived(self) -> None:
        facts = [edge(FINANCE_TEAM, ALICE), edge(FINANCE_RW, FINANCE_TEAM)]
        derived = [a for fact in facts for a in derive_edge(fact, CONTEXT).assertions]
        assert not any(a.relationship_type is RelationshipType.NESTED_MEMBER_OF for a in derived)
        assert (ALICE, FINANCE_RW) not in {(a.source.key, a.target.key) for a in derived}


class TestUnresolvedAndForeignMembers:
    def test_a_member_the_collector_could_not_resolve_is_unresolved(self) -> None:
        orphan = f"{DOMAIN}-9999"
        derived = derive_edge(
            edge(FINANCE_TEAM, orphan, member_kind=PrincipalKind.UNRESOLVED), CONTEXT
        )
        (assertion,) = derived.assertions
        assert assertion.derivation_class is DerivationClass.UNRESOLVED
        assert assertion.qualifiers == {RelationshipQualifier.ENDPOINT_UNRESOLVED_AT_COLLECTION}
        assert confidence_of(assertion.derivation_class, assertion.qualifiers) is (
            RelationshipConfidence.INDETERMINATE
        )

    def test_a_foreign_member_is_unresolved_and_attributed_to_its_domain(self) -> None:
        member = f"{FOREIGN}-1500"
        fact = edge(
            FINANCE_TEAM,
            member,
            member_kind=PrincipalKind.FOREIGN_SECURITY_PRINCIPAL,
            foreign=True,
        )
        derived = derive_edge(fact, CONTEXT).assertions
        (membership,) = found(derived, RelationshipType.MEMBER_OF)
        assert membership.derivation_class is DerivationClass.UNRESOLVED
        assert membership.qualifiers == {RelationshipQualifier.FOREIGN_PRINCIPAL}
        (foreign,) = found(derived, RelationshipType.FOREIGN_PRINCIPAL_OF)
        assert foreign.target == NodeRef(NodeKind.DOMAIN, FOREIGN)
        assert foreign.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED
        assert premises(foreign) == premises(membership)

    def test_a_foreign_member_with_no_issuing_domain_is_not_given_one(self) -> None:
        derived = derive_edge(
            edge(FINANCE_TEAM, AUTHENTICATED_USERS, foreign=True, member_kind=None), CONTEXT
        ).assertions
        assert not found(derived, RelationshipType.FOREIGN_PRINCIPAL_OF)
        assert found(derived, RelationshipType.MEMBER_OF)[0].derivation_class is (
            DerivationClass.UNRESOLVED
        )

    def test_a_foreign_security_principal_object_is_attributed_to_its_domain(self) -> None:
        member = f"{FOREIGN}-1600"
        (assertion,) = derive_principal(
            PrincipalFact(
                reading(EvidenceKind.PRINCIPAL, member),
                member,
                member,
                PrincipalKind.FOREIGN_SECURITY_PRINCIPAL,
                None,
            ),
            CONTEXT,
        ).assertions
        assert assertion.relationship_type is RelationshipType.FOREIGN_PRINCIPAL_OF
        assert premises(assertion) == {(EvidenceKind.PRINCIPAL, member)}
        assert str(assertion.rule) == "foreign_principal.principal_object@1"

    def test_an_ordinary_principal_establishes_nothing_on_its_own(self) -> None:
        derived = derive_principal(
            PrincipalFact(
                reading(EvidenceKind.PRINCIPAL, ALICE), ALICE, ALICE, PrincipalKind.USER, None
            ),
            CONTEXT,
        )
        assert derived.assertions == () and derived.skipped == ()


class TestHostLocalMembership:
    def test_a_local_edge_is_scoped_to_its_host(self) -> None:
        derived = derive_edge(
            edge("S-1-5-32-545", FINANCE_RW, MembershipEdgeKind.LOCAL_GROUP_MEMBER, host="FS01"),
            CONTEXT,
        ).assertions
        (membership,) = derived
        assert membership.relationship_type is RelationshipType.LOCAL_GROUP_MEMBER_OF
        assert membership.scope == RelationshipScope.host("fs01")
        assert membership.target.key == "fs01|S-1-5-32-545"

    @pytest.mark.parametrize(
        ("group", "special", "conditions"),
        [
            ("S-1-5-32-544", RelationshipType.LOCAL_ADMIN_OF, frozenset()),
            (
                "S-1-5-32-555",
                RelationshipType.REMOTE_DESKTOP_USER_OF,
                {
                    RelationshipCondition.LOGON_RIGHT_UNEVALUATED,
                    RelationshipCondition.REMOTE_SERVICE_STATE_UNKNOWN,
                },
            ),
            (
                "S-1-5-32-580",
                RelationshipType.REMOTE_MANAGEMENT_USER_OF,
                {
                    RelationshipCondition.SERVICE_ACCESS_POLICY_UNEVALUATED,
                    RelationshipCondition.REMOTE_SERVICE_STATE_UNKNOWN,
                },
            ),
        ],
    )
    def test_builtin_groups_are_read_as_host_privileges(
        self, group: str, special: RelationshipType, conditions: set[RelationshipCondition]
    ) -> None:
        derived = derive_edge(
            edge(group, FINANCE_RW, MembershipEdgeKind.LOCAL_GROUP_MEMBER, host="FS01"), CONTEXT
        ).assertions
        (privilege,) = found(derived, special)
        assert privilege.scope == RelationshipScope.host("fs01")
        assert privilege.conditions == frozenset(conditions)
        expected = (
            DerivationClass.CONDITIONALLY_DERIVED
            if conditions
            else DerivationClass.DETERMINISTICALLY_DERIVED
        )
        assert privilege.derivation_class is expected
        assert premises(privilege) == premises(
            found(derived, RelationshipType.LOCAL_GROUP_MEMBER_OF)[0]
        )

    def test_remote_access_is_never_unconditional(self) -> None:
        for group in ("S-1-5-32-555", "S-1-5-32-580"):
            for fact in (
                edge(group, ALICE, MembershipEdgeKind.LOCAL_GROUP_MEMBER, host="fs01"),
                edge(
                    group,
                    f"{FOREIGN}-1500",
                    MembershipEdgeKind.LOCAL_GROUP_MEMBER,
                    host="fs01",
                    foreign=True,
                ),
            ):
                for assertion in derive_edge(fact, CONTEXT).assertions:
                    if assertion.relationship_type in (
                        RelationshipType.REMOTE_DESKTOP_USER_OF,
                        RelationshipType.REMOTE_MANAGEMENT_USER_OF,
                    ):
                        assert assertion.conditions

    def test_a_foreign_member_of_remote_desktop_keeps_both_the_conditions_and_the_doubt(
        self,
    ) -> None:
        derived = derive_edge(
            edge(
                "S-1-5-32-555",
                f"{FOREIGN}-1500",
                MembershipEdgeKind.LOCAL_GROUP_MEMBER,
                host="fs01",
                foreign=True,
            ),
            CONTEXT,
        ).assertions
        (rdp,) = found(derived, RelationshipType.REMOTE_DESKTOP_USER_OF)
        assert rdp.derivation_class is DerivationClass.CONDITIONALLY_DERIVED
        assert RelationshipQualifier.FOREIGN_PRINCIPAL in rdp.qualifiers
        assert confidence_of(rdp.derivation_class, rdp.qualifiers) is (
            RelationshipConfidence.INDETERMINATE
        )

    def test_the_same_member_on_two_hosts_is_two_relationships(self) -> None:
        on_fs01 = derive_edge(
            edge("S-1-5-32-544", ALICE, MembershipEdgeKind.LOCAL_GROUP_MEMBER, host="fs01"),
            CONTEXT,
        ).assertions
        on_fs02 = derive_edge(
            edge("S-1-5-32-544", ALICE, MembershipEdgeKind.LOCAL_GROUP_MEMBER, host="fs02"),
            CONTEXT,
        ).assertions
        admin_1 = found(on_fs01, RelationshipType.LOCAL_ADMIN_OF)[0]
        admin_2 = found(on_fs02, RelationshipType.LOCAL_ADMIN_OF)[0]
        assert admin_1.source == admin_2.source
        assert admin_1.relationship_id != admin_2.relationship_id
        assert {admin_1.scope, admin_2.scope} == {
            RelationshipScope.host("fs01"),
            RelationshipScope.host("fs02"),
        }

    def test_a_local_edge_with_no_host_fails_closed(self) -> None:
        fact = edge(
            "S-1-5-32-544",
            ALICE,
            MembershipEdgeKind.LOCAL_GROUP_MEMBER,
            host=None,
            group_key="S-1-5-32-544",
        )
        derived = derive_edge(fact, CONTEXT)
        assert derived.assertions == ()
        (skip,) = derived.skipped
        assert "names no host" in skip.reason

    def test_a_builtin_group_reached_through_the_directory_is_not_a_host_privilege(self) -> None:
        """The domain's own BUILTIN\\Administrators administers domain controllers, which ADG
        does not enumerate as hosts; it is a membership and nothing more."""
        derived = derive_edge(edge("S-1-5-32-544", ALICE), CONTEXT).assertions
        assert [a.relationship_type for a in derived] == [RelationshipType.MEMBER_OF]


# --- NTFS: ownership ----------------------------------------------------------------------


class TestOwnership:
    def test_the_owner_owns_and_can_rewrite_the_dacl(self) -> None:
        derived = derive_resource(resource(owner=ALICE), CONTEXT).assertions
        (owns,) = found(derived, RelationshipType.OWNS)
        assert owns.derivation_class is DerivationClass.OBSERVED
        assert premises(owns) == {(EvidenceKind.NTFS_RESOURCE, PAYROLL)}
        (write_dacl,) = found(derived, RelationshipType.WRITE_DACL, ALICE)
        assert str(write_dacl.rule) == "ntfs.owner_implicit_rights@1"
        assert write_dacl.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED
        assert not found(derived, RelationshipType.WRITE_OWNER, ALICE)
        assert not found(derived, RelationshipType.GENERIC_ALL, ALICE)

    def test_no_deny_takes_the_owners_implicit_right_away(self) -> None:
        derived = derive_resource(
            resource(ace(ALICE, FULL, 0, ace_type=AceType.DENY), owner=ALICE), CONTEXT
        ).assertions
        (write_dacl,) = found(derived, RelationshipType.WRITE_DACL, ALICE)
        assert write_dacl.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED

    def test_an_owner_rights_entry_replaces_the_implicit_grant(self) -> None:
        derived = derive_resource(
            resource(ace(OWNER_RIGHTS, READ_EXECUTE, 0), owner=ALICE), CONTEXT
        ).assertions
        assert found(derived, RelationshipType.OWNS)
        assert not found(derived, RelationshipType.WRITE_DACL)

    def test_an_owner_rights_grant_rests_on_the_entry_and_the_owner(self) -> None:
        grant = ace(OWNER_RIGHTS, int(NtfsRight.WRITE_DAC), 0)
        derived = derive_resource(resource(grant, owner=ALICE), CONTEXT).assertions
        (write_dacl,) = found(derived, RelationshipType.WRITE_DACL, ALICE)
        assert str(write_dacl.rule) == "ntfs.owner_rights_entry@1"
        assert premises(write_dacl) == {
            (EvidenceKind.NTFS_ACE, grant.key),
            (EvidenceKind.NTFS_RESOURCE, PAYROLL),
        }
        assert not any(a.source.key == OWNER_RIGHTS for a in derived)

    def test_a_builtin_owner_is_that_hosts_group(self) -> None:
        derived = derive_resource(resource(owner="S-1-5-32-544"), CONTEXT).assertions
        (owns,) = found(derived, RelationshipType.OWNS)
        assert owns.source.key == "fs01|S-1-5-32-544"
        assert owns.scope == RelationshipScope.host("fs01")

    def test_an_unknown_owner_owns_nothing(self) -> None:
        derived = derive_resource(resource(owner=None), CONTEXT).assertions
        assert not found(derived, RelationshipType.OWNS)

    def test_an_incomplete_dacl_makes_the_implicit_grant_conditional(self) -> None:
        derived = derive_resource(
            resource(ace(BOB, READ_EXECUTE, 0), owner=ALICE, declared=3), CONTEXT
        ).assertions
        (write_dacl,) = found(derived, RelationshipType.WRITE_DACL, ALICE)
        assert write_dacl.conditions == {RelationshipCondition.ACL_ENTRIES_INCOMPLETE}


# --- NTFS: entries ------------------------------------------------------------------------


class TestWhatAMaskEstablishes:
    def test_full_control_supports_all_four_control_types(self) -> None:
        assert types_from(FINANCE_RW, ace(FINANCE_RW, FULL, 0)) == set(CONTROL_RIGHTS)

    def test_generic_all_is_mapped_before_it_is_read(self) -> None:
        assert types_from(FINANCE_RW, ace(FINANCE_RW, int(NtfsRight.GENERIC_ALL), 0)) == set(
            CONTROL_RIGHTS
        )

    def test_modify_is_generic_write_and_nothing_more(self) -> None:
        assert types_from(FINANCE_RW, ace(FINANCE_RW, MODIFY, 0)) == {
            RelationshipType.GENERIC_WRITE
        }

    def test_generic_write_bit_is_mapped(self) -> None:
        assert types_from(FINANCE_RW, ace(FINANCE_RW, int(NtfsRight.GENERIC_WRITE), 0)) == {
            RelationshipType.GENERIC_WRITE
        }

    def test_an_unrelated_mask_establishes_nothing(self) -> None:
        assert types_from(FINANCE_RW, ace(FINANCE_RW, READ_EXECUTE, 0)) == set()

    def test_single_rights_are_read_one_by_one(self) -> None:
        assert types_from(BOB, ace(BOB, int(NtfsRight.WRITE_DAC), 0)) == {
            RelationshipType.WRITE_DACL
        }
        assert types_from(BOB, ace(BOB, int(NtfsRight.WRITE_OWNER), 0)) == {
            RelationshipType.WRITE_OWNER
        }

    def test_a_grant_carries_what_it_read(self) -> None:
        grant = ace(FINANCE_RW, int(NtfsRight.WRITE_DAC), 0, flags=0x13)
        (write_dacl,) = derive_resource(resource(grant), CONTEXT).assertions
        assert dict(write_dacl.attributes) == {
            "access_mask": int(NtfsRight.WRITE_DAC),
            "ace_type": "allow",
            "inherited": True,
        }
        assert premises(write_dacl) == {(EvidenceKind.NTFS_ACE, grant.key)}
        assert contexts(write_dacl) == {(EvidenceKind.NTFS_RESOURCE, PAYROLL)}
        assert write_dacl.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED

    def test_an_inherit_only_entry_grants_nothing_here(self) -> None:
        assert types_from(FINANCE_RW, ace(FINANCE_RW, FULL, 0, flags=0x0B)) == set()

    def test_creator_owner_is_never_a_holder(self) -> None:
        derived = derive_resource(resource(ace(CREATOR_OWNER, FULL, 0, flags=0x03)), CONTEXT)
        assert derived.assertions == ()

    def test_a_host_local_trustee_is_scoped(self) -> None:
        derived = derive_resource(resource(ace("S-1-5-32-544", FULL, 0)), CONTEXT).assertions
        assert {a.scope for a in derived} == {RelationshipScope.host("fs01")}
        assert {a.source.key for a in derived} == {"fs01|S-1-5-32-544"}

    def test_two_entries_granting_one_right_are_two_supports_of_one_relationship(self) -> None:
        derived = derive_resource(
            resource(
                ace(BOB, int(NtfsRight.WRITE_DAC), 0),
                ace(BOB, FULL, 1, flags=0x13),
            ),
            CONTEXT,
        ).assertions
        write_dacl = found(derived, RelationshipType.WRITE_DACL, BOB)
        assert len(write_dacl) == 2
        assert len({a.relationship_id for a in write_dacl}) == 1
        assert len({a.support_key for a in write_dacl}) == 2

    def test_entries_that_only_together_grant_full_control_are_one_joint_support(self) -> None:
        first = ace(BOB, MODIFY, 0)
        second = ace(BOB, FULL & ~MODIFY, 1)
        derived = derive_resource(resource(first, second), CONTEXT).assertions
        (generic_all,) = found(derived, RelationshipType.GENERIC_ALL, BOB)
        assert str(generic_all.rule) == "ntfs.allow_entries_combined@1"
        assert premises(generic_all) == {
            (EvidenceKind.NTFS_ACE, first.key),
            (EvidenceKind.NTFS_ACE, second.key),
        }
        # Each single right is still read from the one entry that carries it.
        assert premises(found(derived, RelationshipType.WRITE_DACL, BOB)[0]) == {
            (EvidenceKind.NTFS_ACE, second.key)
        }


class TestDenyAndOrder:
    def test_a_deny_for_the_trustee_ahead_of_the_grant_removes_it(self) -> None:
        assert (
            types_from(
                BOB,
                ace(BOB, int(NtfsRight.WRITE_DAC), 0, ace_type=AceType.DENY),
                ace(BOB, FULL, 1),
            )
            # Full control less WRITE_DAC: still write and take-ownership, never full control.
            == {RelationshipType.GENERIC_WRITE, RelationshipType.WRITE_OWNER}
        )

    def test_a_deny_for_everyone_ahead_of_the_grant_removes_it(self) -> None:
        assert (
            types_from(
                BOB,
                ace(EVERYONE, int(NtfsRight.WRITE_DAC), 0, ace_type=AceType.DENY),
                ace(BOB, int(NtfsRight.WRITE_DAC), 1),
            )
            == set()
        )

    def test_a_deny_behind_the_grant_takes_nothing_away(self) -> None:
        derived = derive_resource(
            resource(
                ace(BOB, int(NtfsRight.WRITE_DAC), 0),
                ace(BOB, int(NtfsRight.WRITE_DAC), 1, ace_type=AceType.DENY),
            ),
            CONTEXT,
        ).assertions
        (write_dacl,) = found(derived, RelationshipType.WRITE_DACL, BOB)
        assert write_dacl.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED

    def test_a_deny_for_another_trustee_ahead_of_the_grant_makes_it_conditional(self) -> None:
        deny = ace(FINANCE_TEAM, int(NtfsRight.WRITE_DAC), 0, ace_type=AceType.DENY)
        derived = derive_resource(resource(deny, ace(FINANCE_RW, FULL, 1)), CONTEXT).assertions
        (write_dacl,) = found(derived, RelationshipType.WRITE_DACL, FINANCE_RW)
        assert write_dacl.derivation_class is DerivationClass.CONDITIONALLY_DERIVED
        assert write_dacl.conditions == {RelationshipCondition.DENY_MAY_APPLY}
        assert (EvidenceKind.NTFS_ACE, deny.key) in contexts(write_dacl)
        # The deny names WRITE_DAC only; full control's other readings are unaffected by it.
        (write_owner,) = found(derived, RelationshipType.WRITE_OWNER, FINANCE_RW)
        assert write_owner.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED

    def test_a_deny_for_unrelated_rights_changes_nothing(self) -> None:
        derived = derive_resource(
            resource(
                ace(FINANCE_TEAM, int(NtfsRight.READ_DATA), 0, ace_type=AceType.DENY),
                ace(FINANCE_RW, int(NtfsRight.WRITE_DAC), 1),
            ),
            CONTEXT,
        ).assertions
        (write_dacl,) = found(derived, RelationshipType.WRITE_DACL, FINANCE_RW)
        assert write_dacl.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED

    def test_a_deny_for_authenticated_users_depends_on_the_token(self) -> None:
        derived = derive_resource(
            resource(
                ace(AUTHENTICATED_USERS, int(NtfsRight.WRITE_DAC), 0, ace_type=AceType.DENY),
                ace(BOB, int(NtfsRight.WRITE_DAC), 1),
            ),
            CONTEXT,
        ).assertions
        (write_dacl,) = found(derived, RelationshipType.WRITE_DACL, BOB)
        assert write_dacl.conditions == {RelationshipCondition.DENY_MAY_APPLY}

    def test_an_inherit_only_deny_takes_nothing_away_here(self) -> None:
        derived = derive_resource(
            resource(
                ace(BOB, int(NtfsRight.WRITE_DAC), 0, ace_type=AceType.DENY, flags=0x0B),
                ace(BOB, int(NtfsRight.WRITE_DAC), 1),
            ),
            CONTEXT,
        ).assertions
        (write_dacl,) = found(derived, RelationshipType.WRITE_DACL, BOB)
        assert write_dacl.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED

    def test_an_inherit_only_deny_for_another_trustee_qualifies_nothing_here(self) -> None:
        derived = derive_resource(
            resource(
                ace(FINANCE_TEAM, int(NtfsRight.WRITE_DAC), 0, ace_type=AceType.DENY, flags=0x0B),
                ace(FINANCE_RW, int(NtfsRight.WRITE_DAC), 1),
            ),
            CONTEXT,
        ).assertions
        (write_dacl,) = found(derived, RelationshipType.WRITE_DACL, FINANCE_RW)
        assert write_dacl.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED
        assert not contexts(write_dacl) - {(EvidenceKind.NTFS_RESOURCE, PAYROLL)}

    def test_an_unknown_order_never_decides_whether_a_deny_comes_first(self) -> None:
        derived = derive_resource(
            resource(
                ace(BOB, int(NtfsRight.WRITE_DAC), None, ace_type=AceType.DENY),
                ace(BOB, int(NtfsRight.WRITE_DAC), None),
            ),
            CONTEXT,
        ).assertions
        (write_dacl,) = found(derived, RelationshipType.WRITE_DACL, BOB)
        assert write_dacl.derivation_class is DerivationClass.CONDITIONALLY_DERIVED
        assert write_dacl.conditions == {RelationshipCondition.ACL_ORDER_UNKNOWN}

    def test_an_owner_rights_deny_is_universal_only_for_the_owner(self) -> None:
        deny = ace(OWNER_RIGHTS, int(NtfsRight.WRITE_DAC), 0, ace_type=AceType.DENY)
        derived = derive_resource(
            resource(deny, ace(BOB, int(NtfsRight.WRITE_DAC), 1), owner=ALICE), CONTEXT
        ).assertions
        (bob,) = found(derived, RelationshipType.WRITE_DACL, BOB)
        assert bob.conditions == {RelationshipCondition.DENY_MAY_APPLY}
        # Alice owns it; OWNER RIGHTS replaced her implicit grant and grants her nothing.
        assert not found(derived, RelationshipType.WRITE_DACL, ALICE)


class TestIncompleteDescriptors:
    def test_a_short_dacl_makes_every_grant_conditional(self) -> None:
        derived = derive_resource(resource(ace(BOB, FULL, 0), declared=2), CONTEXT).assertions
        assert derived
        for assertion in found(derived, RelationshipType.GENERIC_ALL):
            assert RelationshipCondition.ACL_ENTRIES_INCOMPLETE in assertion.conditions

    def test_an_orphaned_acl_is_read_and_qualified_never_assumed_complete(self) -> None:
        derived = derive_resource(resource(ace(BOB, FULL, 0), described=False), CONTEXT)
        assert not found(derived.assertions, RelationshipType.OWNS)
        for assertion in derived.assertions:
            assert assertion.conditions == {RelationshipCondition.ACL_ENTRIES_INCOMPLETE}
            assert not contexts(assertion)

    def test_a_null_dacl_grants_everyone_everything(self) -> None:
        derived = derive_resource(
            resource(ace(BOB, READ_EXECUTE, 0), owner=ALICE, dacl_present=False, declared=0),
            CONTEXT,
        ).assertions
        everyone = [a for a in derived if a.source.key == EVERYONE]
        assert {a.relationship_type for a in everyone} == set(CONTROL_RIGHTS)
        assert {str(a.rule) for a in everyone} == {"ntfs.null_dacl@1"}
        assert all(
            a.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED for a in everyone
        )
        assert not found(derived, RelationshipType.GENERIC_ALL, BOB)
        (owner,) = found(derived, RelationshipType.WRITE_DACL, ALICE)
        assert owner.derivation_class is DerivationClass.DETERMINISTICALLY_DERIVED


class TestDeterminism:
    def test_the_same_facts_in_any_order_derive_the_same_assertions(self) -> None:
        """Facts arrive in no particular order; the descriptor's order is ``order_index``."""
        entries = [
            ace(FINANCE_TEAM, int(NtfsRight.WRITE_DAC), 0, ace_type=AceType.DENY),
            ace(FINANCE_RW, FULL, 1),
            ace(BOB, MODIFY, 2),
            ace("S-1-5-32-544", FULL, 3, flags=0x13),
            ace(BOB, int(NtfsRight.WRITE_DAC), 4, ace_type=AceType.DENY),
            ace(BOB, FULL, 5),
            ace(BOB, int(NtfsRight.WRITE_OWNER), 6),
        ]
        expected = derive_resource(resource(*entries, owner=ALICE), CONTEXT)
        # Bob's own Deny at 4 comes before both his grants: he cannot change the DACL.
        assert not found(expected.assertions, RelationshipType.WRITE_DACL, BOB)
        assert len(found(expected.assertions, RelationshipType.WRITE_OWNER, BOB)) == 2
        for permutation in (
            *itertools.islice(itertools.permutations(entries), 0, 5040, 97),
            tuple(reversed(entries)),
        ):
            again = derive_resource(resource(*permutation, owner=ALICE), CONTEXT)
            assert again == expected

    def test_every_assertion_rests_on_a_premise_it_names(self) -> None:
        derived = derive_resource(
            resource(ace(FINANCE_RW, FULL, 0), ace(BOB, MODIFY, 1), owner=ALICE), CONTEXT
        ).assertions
        assert derived
        for assertion in derived:
            assert premises(assertion)
