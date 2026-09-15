r"""The severity table, tested as a table.

Two properties matter and neither is about any one rule.

**Every rule can fire.** ``RULES`` is evaluated first-match-wins, so a rule placed below one
that subsumes it is dead code that looks like policy. The parameterized case below builds
one input per rule and asserts the rule that fires is the one the case was written for —
which means adding a rule in the wrong position fails here rather than in six months when
somebody asks why an ACL change was never flagged.

**The table always answers.** Its last rule is unconditional, so no transition can fall out
of the bottom and be dropped. ``test_the_table_is_total`` pins that.

Beyond the mechanics there are the judgments — removing a Deny is a broadening, a Deny whose
mask grew is a narrowing, Everyone is broad and SYSTEM is not — and each of those has its own
case saying why it is that way round.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.changes.model import (
    ChangeAction,
    ChangeDirection,
    ChangeSeverity,
    FieldDelta,
    FieldSignificance,
)
from app.changes.principals import (
    is_broad_trustee,
    is_privileged_group,
    sid_of_key,
    trustee_display,
)
from app.changes.rules import RULES, ChangeFacts, evaluate
from app.contracts.v1.common import ObservationKind
from tests.changes import factories as f


def facts(
    kind: ObservationKind,
    action: ChangeAction,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    deltas: tuple[FieldDelta, ...] = (),
    container_key: str | None = None,
    related_key: str | None = None,
    ordering_material: bool | None = None,
    sibling_ace_changes: int | None = None,
) -> ChangeFacts:
    return ChangeFacts(
        kind=kind,
        key="k",
        action=action,
        before=before,
        after=after,
        deltas=deltas,
        container_key=container_key,
        related_key=related_key,
        ordering_material=ordering_material,
        sibling_ace_changes=sibling_ace_changes,
    )


def _deltas(
    kind: ObservationKind, before: dict[str, Any], after: dict[str, Any]
) -> tuple[FieldDelta, ...]:
    from app.changes.classify import deltas_between

    return deltas_between(kind, before, after)


def _ace_modification(**after_kwargs: Any) -> ChangeFacts:
    before = f.ntfs_ace_state()
    after = f.ntfs_ace_state(**after_kwargs)
    return facts(
        ObservationKind.NTFS_ACE,
        ChangeAction.MODIFIED,
        before=before,
        after=after,
        deltas=_deltas(ObservationKind.NTFS_ACE, before, after),
        container_key=f.FINANCE,
        related_key=f.FINANCE_RW,
    )


def _resource_modification(**after_kwargs: Any) -> ChangeFacts:
    before = f.resource_state()
    after = f.resource_state(**after_kwargs)
    return facts(
        ObservationKind.NTFS_RESOURCE,
        ChangeAction.MODIFIED,
        before=before,
        after=after,
        deltas=_deltas(ObservationKind.NTFS_RESOURCE, before, after),
        container_key=f.FINANCE_SHARE,
    )


def _principal_modification(**after_kwargs: Any) -> ChangeFacts:
    before = f.principal_state(kind="domain_group", group_type="distribution", enabled=None)
    after = f.principal_state(kind="domain_group", enabled=None, **after_kwargs)
    return facts(
        ObservationKind.PRINCIPAL,
        ChangeAction.MODIFIED,
        before=before,
        after=after,
        deltas=_deltas(ObservationKind.PRINCIPAL, before, after),
    )


#: One input per rule id, written so that the named rule is the one that should fire.
CASES: dict[str, ChangeFacts] = {
    "first_observed": facts(
        ObservationKind.NTFS_ACE,
        ChangeAction.FIRST_OBSERVED,
        after=f.ntfs_ace_state(trustee=f.EVERYONE),
    ),
    "identity.drift": _ace_modification(trustee=f.ALICE),
    "ace.allow.broad.escalation": facts(
        ObservationKind.NTFS_ACE,
        ChangeAction.ADDED,
        after=f.ntfs_ace_state(trustee=f.EVERYONE, access_mask=0x40000),  # WRITE_DAC alone
        related_key=f.EVERYONE,
    ),
    "ace.allow.broad.full": facts(
        ObservationKind.NTFS_ACE,
        ChangeAction.ADDED,
        after=f.ntfs_ace_state(trustee=f.EVERYONE, access_mask=f.FULL_CONTROL),
        related_key=f.EVERYONE,
    ),
    "ace.allow.broad.write": facts(
        ObservationKind.NTFS_ACE,
        ChangeAction.ADDED,
        after=f.ntfs_ace_state(trustee=f.EVERYONE, access_mask=0x116),  # write bits, no WRITE_DAC
        related_key=f.EVERYONE,
    ),
    "ace.allow.broad": facts(
        ObservationKind.NTFS_ACE,
        ChangeAction.ADDED,
        after=f.ntfs_ace_state(trustee=f.EVERYONE, access_mask=f.READ_EXECUTE),
        related_key=f.EVERYONE,
    ),
    "ace.deny.removed": facts(
        ObservationKind.NTFS_ACE,
        ChangeAction.REMOVED,
        after=f.ntfs_ace_state(ace_type="deny", access_mask=f.FULL_CONTROL),
        related_key=f.FINANCE_RW,
    ),
    "ace.allow.escalation": facts(
        ObservationKind.NTFS_ACE,
        ChangeAction.ADDED,
        after=f.ntfs_ace_state(access_mask=0x40000),
        related_key=f.FINANCE_RW,
    ),
    "ace.allow.full": facts(
        ObservationKind.NTFS_ACE,
        ChangeAction.ADDED,
        after=f.ntfs_ace_state(access_mask=f.FULL_CONTROL),
        related_key=f.FINANCE_RW,
    ),
    "ace.order.material": _ace_modification(order_index=5, source_key="ntfs_ace|2"),
    "ace.allow.added": facts(
        ObservationKind.NTFS_ACE,
        ChangeAction.ADDED,
        after=f.ntfs_ace_state(access_mask=f.READ_EXECUTE),
        related_key=f.FINANCE_RW,
    ),
    "ace.deny.added": facts(
        ObservationKind.NTFS_ACE,
        ChangeAction.ADDED,
        after=f.ntfs_ace_state(ace_type="deny", access_mask=f.FULL_CONTROL),
        related_key=f.FINANCE_RW,
    ),
    "ace.allow.removed": facts(
        ObservationKind.NTFS_ACE,
        ChangeAction.REMOVED,
        after=f.ntfs_ace_state(access_mask=f.READ_EXECUTE),
        related_key=f.FINANCE_RW,
    ),
    "ace.source.explicit": facts(
        ObservationKind.NTFS_ACE,
        ChangeAction.MODIFIED,
        before=f.ntfs_ace_state(source="inherited"),
        after=f.ntfs_ace_state(source="explicit", source_key="ntfs_ace|2"),
        deltas=_deltas(
            ObservationKind.NTFS_ACE,
            f.ntfs_ace_state(source="inherited"),
            f.ntfs_ace_state(source="explicit", source_key="ntfs_ace|2"),
        ),
        related_key=f.FINANCE_RW,
    ),
    "ace.source.inherited": _ace_modification(source="inherited", source_key="ntfs_ace|2"),
    "ace.order.immaterial": _ace_modification(order_index=5, source_key="ntfs_ace|2"),
    "ace.order.undetermined": _ace_modification(order_index=5, source_key="ntfs_ace|2"),
    "membership.added.privileged": facts(
        ObservationKind.MEMBERSHIP_EDGE,
        ChangeAction.ADDED,
        after=f.edge_state(group=f.DOMAIN_ADMINS),
        container_key=f.DOMAIN_ADMINS,
        related_key=f.ALICE,
    ),
    "membership.added.broad": facts(
        ObservationKind.MEMBERSHIP_EDGE,
        ChangeAction.ADDED,
        after=f.edge_state(member=f.EVERYONE),
        container_key=f.FINANCE_RW,
        related_key=f.EVERYONE,
    ),
    "membership.removed.privileged": facts(
        ObservationKind.MEMBERSHIP_EDGE,
        ChangeAction.REMOVED,
        after=f.edge_state(group=f.DOMAIN_ADMINS),
        container_key=f.DOMAIN_ADMINS,
        related_key=f.ALICE,
    ),
    "membership.added": facts(
        ObservationKind.MEMBERSHIP_EDGE,
        ChangeAction.ADDED,
        after=f.edge_state(),
        container_key=f.FINANCE_RW,
        related_key=f.ALICE,
    ),
    "membership.removed": facts(
        ObservationKind.MEMBERSHIP_EDGE,
        ChangeAction.REMOVED,
        after=f.edge_state(),
        container_key=f.FINANCE_RW,
        related_key=f.ALICE,
    ),
    "principal.group_type.security": _principal_modification(group_type="security"),
    "principal.group_type.distribution": facts(
        ObservationKind.PRINCIPAL,
        ChangeAction.MODIFIED,
        before=f.principal_state(kind="domain_group", group_type="security", enabled=None),
        after=f.principal_state(kind="domain_group", group_type="distribution", enabled=None),
        deltas=_deltas(
            ObservationKind.PRINCIPAL,
            f.principal_state(kind="domain_group", group_type="security", enabled=None),
            f.principal_state(kind="domain_group", group_type="distribution", enabled=None),
        ),
    ),
    "principal.enabled": facts(
        ObservationKind.PRINCIPAL,
        ChangeAction.MODIFIED,
        before=f.principal_state(enabled=False),
        after=f.principal_state(enabled=True),
        deltas=_deltas(
            ObservationKind.PRINCIPAL,
            f.principal_state(enabled=False),
            f.principal_state(enabled=True),
        ),
    ),
    "principal.disabled": facts(
        ObservationKind.PRINCIPAL,
        ChangeAction.MODIFIED,
        before=f.principal_state(enabled=True),
        after=f.principal_state(enabled=False),
        deltas=_deltas(
            ObservationKind.PRINCIPAL,
            f.principal_state(enabled=True),
            f.principal_state(enabled=False),
        ),
    ),
    "principal.undeleted": facts(
        ObservationKind.PRINCIPAL,
        ChangeAction.MODIFIED,
        before=f.principal_state(is_deleted=True),
        after=f.principal_state(is_deleted=False),
        deltas=_deltas(
            ObservationKind.PRINCIPAL,
            f.principal_state(is_deleted=True),
            f.principal_state(is_deleted=False),
        ),
    ),
    "principal.deleted": facts(
        ObservationKind.PRINCIPAL,
        ChangeAction.MODIFIED,
        before=f.principal_state(is_deleted=False),
        after=f.principal_state(is_deleted=True),
        deltas=_deltas(
            ObservationKind.PRINCIPAL,
            f.principal_state(is_deleted=False),
            f.principal_state(is_deleted=True),
        ),
    ),
    "principal.group_scope": _principal_modification(
        group_type="distribution", group_scope="universal"
    ),
    "principal.removed": facts(
        ObservationKind.PRINCIPAL, ChangeAction.REMOVED, after=f.principal_state()
    ),
    "principal.added": facts(
        ObservationKind.PRINCIPAL, ChangeAction.ADDED, after=f.principal_state()
    ),
    "resource.null_dacl": _resource_modification(dacl_present=False),
    "resource.added.null_dacl": facts(
        ObservationKind.NTFS_RESOURCE,
        ChangeAction.ADDED,
        after=f.resource_state(dacl_present=False),
    ),
    "resource.unprotected": _resource_modification(dacl_protected=False),
    "resource.protected": facts(
        ObservationKind.NTFS_RESOURCE,
        ChangeAction.MODIFIED,
        before=f.resource_state(dacl_protected=False),
        after=f.resource_state(dacl_protected=True),
        deltas=_deltas(
            ObservationKind.NTFS_RESOURCE,
            f.resource_state(dacl_protected=False),
            f.resource_state(dacl_protected=True),
        ),
    ),
    # ``inheritance_enabled`` and ``dacl_protected`` are two separate observation fields and
    # normally move together, so this case moves only one of them: the collector reported a
    # directory that stopped inheriting without SE_DACL_PROTECTED changing. Rare, real, and
    # the reason the rule exists below the two protection rules rather than instead of them.
    "resource.inheritance": facts(
        ObservationKind.NTFS_RESOURCE,
        ChangeAction.MODIFIED,
        before={**f.resource_state(dacl_protected=True), "inheritance_enabled": True},
        after={**f.resource_state(dacl_protected=True), "inheritance_enabled": False},
        deltas=_deltas(
            ObservationKind.NTFS_RESOURCE,
            {**f.resource_state(dacl_protected=True), "inheritance_enabled": True},
            {**f.resource_state(dacl_protected=True), "inheritance_enabled": False},
        ),
    ),
    "resource.owner.broad": _resource_modification(owner_sid=f.EVERYONE),
    "resource.owner": _resource_modification(owner_sid=f.ALICE),
    "resource.acl_hash.unexplained": facts(
        ObservationKind.NTFS_RESOURCE,
        ChangeAction.MODIFIED,
        before=f.resource_state(acl_hash="a" * 64),
        after=f.resource_state(acl_hash="b" * 64),
        deltas=_deltas(
            ObservationKind.NTFS_RESOURCE,
            f.resource_state(acl_hash="a" * 64),
            f.resource_state(acl_hash="b" * 64),
        ),
        sibling_ace_changes=0,
    ),
    "resource.acl_hash": facts(
        ObservationKind.NTFS_RESOURCE,
        ChangeAction.MODIFIED,
        before=f.resource_state(acl_hash="a" * 64),
        after=f.resource_state(acl_hash="b" * 64),
        deltas=_deltas(
            ObservationKind.NTFS_RESOURCE,
            f.resource_state(acl_hash="a" * 64),
            f.resource_state(acl_hash="b" * 64),
        ),
        sibling_ace_changes=3,
    ),
    "resource.removed": facts(
        ObservationKind.NTFS_RESOURCE, ChangeAction.REMOVED, after=f.resource_state()
    ),
    "resource.added": facts(
        ObservationKind.NTFS_RESOURCE, ChangeAction.ADDED, after=f.resource_state()
    ),
    "share.local_path": facts(
        ObservationKind.SMB_SHARE,
        ChangeAction.MODIFIED,
        before=f.share_state(local_path="D:\\Shares\\Finance"),
        after=f.share_state(local_path="E:\\Everything"),
        deltas=_deltas(
            ObservationKind.SMB_SHARE,
            f.share_state(local_path="D:\\Shares\\Finance"),
            f.share_state(local_path="E:\\Everything"),
        ),
    ),
    "share.type": facts(
        ObservationKind.SMB_SHARE,
        ChangeAction.MODIFIED,
        before=f.share_state(share_type="disk"),
        after=f.share_state(share_type="print"),
        deltas=_deltas(
            ObservationKind.SMB_SHARE,
            f.share_state(share_type="disk"),
            f.share_state(share_type="print"),
        ),
    ),
    "share.added": facts(ObservationKind.SMB_SHARE, ChangeAction.ADDED, after=f.share_state()),
    "share.removed": facts(ObservationKind.SMB_SHARE, ChangeAction.REMOVED, after=f.share_state()),
    "server.computer_sid": facts(
        ObservationKind.SERVER,
        ChangeAction.MODIFIED,
        before=f.server_state(computer_sid="S-1-5-21-1-1-1"),
        after=f.server_state(computer_sid="S-1-5-21-2-2-2"),
        deltas=_deltas(
            ObservationKind.SERVER,
            f.server_state(computer_sid="S-1-5-21-1-1-1"),
            f.server_state(computer_sid="S-1-5-21-2-2-2"),
        ),
    ),
    "server.domain": facts(
        ObservationKind.SERVER,
        ChangeAction.MODIFIED,
        before=f.server_state(is_domain_member=True),
        after=f.server_state(is_domain_member=False),
        deltas=_deltas(
            ObservationKind.SERVER,
            f.server_state(is_domain_member=True),
            f.server_state(is_domain_member=False),
        ),
    ),
    "server.removed": facts(ObservationKind.SERVER, ChangeAction.REMOVED, after=f.server_state()),
    "server.added": facts(ObservationKind.SERVER, ChangeAction.ADDED, after=f.server_state()),
    "metadata.other": facts(
        ObservationKind.SERVER,
        ChangeAction.MODIFIED,
        before=f.server_state(operating_system="Windows Server 2019"),
        after=f.server_state(operating_system="Windows Server 2022"),
        deltas=_deltas(
            ObservationKind.SERVER,
            f.server_state(operating_system="Windows Server 2019"),
            f.server_state(operating_system="Windows Server 2022"),
        ),
    ),
    "derived.other": facts(
        ObservationKind.NTFS_RESOURCE,
        ChangeAction.MODIFIED,
        before=f.resource_state(ace_count=3),
        after=f.resource_state(ace_count=4),
        deltas=_deltas(
            ObservationKind.NTFS_RESOURCE,
            f.resource_state(ace_count=3),
            f.resource_state(ace_count=4),
        ),
    ),
    "provenance.only": facts(
        ObservationKind.SERVER,
        ChangeAction.MODIFIED,
        before=f.server_state(source_key="server|1"),
        after=f.server_state(source_key="server|2"),
        deltas=_deltas(
            ObservationKind.SERVER,
            f.server_state(source_key="server|1"),
            f.server_state(source_key="server|2"),
        ),
    ),
    # A security field that no specific rule covers: a collector that stopped reporting
    # whether an account is enabled. Neither `became(enabled, True)` nor
    # `became(enabled, False)` holds, and the fall-through has to catch it rather than let
    # a security field move unreported.
    "security.other": facts(
        ObservationKind.PRINCIPAL,
        ChangeAction.MODIFIED,
        before=f.principal_state(enabled=True),
        after=f.principal_state(enabled=None),
        deltas=_deltas(
            ObservationKind.PRINCIPAL,
            f.principal_state(enabled=True),
            f.principal_state(enabled=None),
        ),
    ),
    "unclassified.field": facts(
        ObservationKind.PRINCIPAL,
        ChangeAction.MODIFIED,
        before=f.principal_state(),
        after=f.principal_state(),
        deltas=(
            FieldDelta(
                field="a_column_nobody_classified",
                before=None,
                after=1,
                significance=FieldSignificance.UNCLASSIFIED,
            ),
        ),
    ),
    "unclassified": facts(
        ObservationKind.MEMBERSHIP_EDGE, ChangeAction.MODIFIED, after=f.edge_state()
    ),
}

# Three rules need a fact the case table cannot express by construction alone, because they
# differ only in a value computed elsewhere. They share their inputs with a neighbour and are
# separated by the correlation answer, so they are steered rather than merely built.
CASES["ace.order.material"] = replace(CASES["ace.order.material"], ordering_material=True)
CASES["ace.order.immaterial"] = replace(CASES["ace.order.immaterial"], ordering_material=False)
CASES["ace.order.undetermined"] = replace(CASES["ace.order.undetermined"], ordering_material=None)


class TestTheTableAsATable:
    def test_every_rule_has_a_case(self) -> None:
        """A rule with no case is a rule nobody has checked can fire."""
        missing = [rule.rule_id for rule in RULES if rule.rule_id not in CASES]
        assert not missing, f"no case written for {missing}"

    def test_no_case_names_a_rule_that_is_gone(self) -> None:
        ids = {rule.rule_id for rule in RULES}
        assert not set(CASES) - ids

    def test_the_rule_ids_are_unique(self) -> None:
        ids = [rule.rule_id for rule in RULES]
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize("rule_id", list(CASES), ids=list(CASES))
    def test_each_rule_fires_on_its_own_case(self, rule_id: str) -> None:
        """First-match-wins means a rule below one that subsumes it is dead policy."""
        outcome = evaluate(CASES[rule_id])
        assert outcome.rule_id == rule_id, (
            f"{rule_id}'s case fired {outcome.rule_id} instead. Either the case is wrong or "
            "the rule is shadowed by one above it and can never fire."
        )

    def test_every_outcome_carries_a_sentence(self) -> None:
        for rule_id, case in CASES.items():
            outcome = evaluate(case)
            assert outcome.reason.strip(), f"{rule_id} fired with an empty reason"

    def test_the_table_is_total(self) -> None:
        """Nothing falls out of the bottom. A transition nobody classified is still reported."""
        outcome = evaluate(
            facts(ObservationKind.SERVER, ChangeAction.MODIFIED, after=f.server_state())
        )
        assert outcome.rule_id == "unclassified"
        assert outcome.severity is ChangeSeverity.MEDIUM


class TestTheJudgmentsThatCouldHaveGoneTheOtherWay:
    def test_removing_a_deny_broadens_access_at_high_severity(self) -> None:
        """A Deny is a control somebody placed. Removing one hands back what it withheld,
        with no Allow touched, and is therefore invisible to any review watching for grants."""
        outcome = evaluate(CASES["ace.deny.removed"])
        assert outcome.direction is ChangeDirection.BROADENED
        assert outcome.severity is ChangeSeverity.HIGH

    def test_adding_a_deny_narrows_access(self) -> None:
        outcome = evaluate(CASES["ace.deny.added"])
        assert outcome.direction is ChangeDirection.NARROWED

    def test_removing_an_allow_narrows_access(self) -> None:
        assert evaluate(CASES["ace.allow.removed"]).direction is ChangeDirection.NARROWED

    def test_unprotecting_a_directory_is_undetermined_not_broadened(self) -> None:
        """It starts inheriting its parent's entries, which may be Allows, Denies or both."""
        outcome = evaluate(CASES["resource.unprotected"])
        assert outcome.direction is ChangeDirection.UNDETERMINED
        assert outcome.severity is ChangeSeverity.HIGH

    def test_a_null_dacl_appearing_is_critical(self) -> None:
        outcome = evaluate(CASES["resource.null_dacl"])
        assert outcome.severity is ChangeSeverity.CRITICAL
        assert outcome.direction is ChangeDirection.BROADENED

    def test_joining_domain_admins_outranks_any_acl_change(self) -> None:
        assert evaluate(CASES["membership.added.privileged"]).severity is ChangeSeverity.CRITICAL

    def test_making_a_distribution_group_a_security_group_is_high(self) -> None:
        """No ACL moved; every entry naming the group just became live."""
        assert evaluate(CASES["principal.group_type.security"]).severity is ChangeSeverity.HIGH

    def test_repointing_a_share_is_high_and_undetermined(self) -> None:
        outcome = evaluate(CASES["share.local_path"])
        assert outcome.severity is ChangeSeverity.HIGH
        assert outcome.direction is ChangeDirection.UNDETERMINED

    def test_a_reordering_the_normal_form_ignores_is_informational(self) -> None:
        outcome = evaluate(CASES["ace.order.immaterial"])
        assert outcome.severity is ChangeSeverity.INFO
        assert outcome.direction is ChangeDirection.NEUTRAL

    def test_a_reordering_nobody_could_check_is_not_informational(self) -> None:
        outcome = evaluate(CASES["ace.order.undetermined"])
        assert outcome.severity is ChangeSeverity.MEDIUM
        assert outcome.direction is ChangeDirection.UNDETERMINED

    def test_a_digest_that_moved_with_no_entries_is_a_collection_finding(self) -> None:
        """The collector read an ACL that differs from the one ADG stores."""
        assert (
            evaluate(CASES["resource.acl_hash.unexplained"]).rule_id
            == "resource.acl_hash.unexplained"
        )

    def test_the_same_digest_change_beside_ace_changes_is_only_a_consequence(self) -> None:
        assert evaluate(CASES["resource.acl_hash"]).severity is ChangeSeverity.LOW


class TestReadingAnAcesRights:
    def test_generic_bits_are_expanded_before_they_are_judged(self) -> None:
        """``GENERIC_ALL`` and ``FILE_ALL_ACCESS`` are the same grant written two ways."""
        generic = facts(
            ObservationKind.NTFS_ACE,
            ChangeAction.ADDED,
            after=f.ntfs_ace_state(trustee=f.EVERYONE, access_mask=0x10000000),
            related_key=f.EVERYONE,
        )
        assert generic.grants_full_control
        assert evaluate(generic).severity is ChangeSeverity.CRITICAL

    def test_a_share_ace_reported_as_a_level_is_read_as_a_mask(self) -> None:
        """``Get-SmbShareAccess`` can only say 'full'; a descriptor says 0x001f01ff."""
        level = facts(
            ObservationKind.SMB_ACE,
            ChangeAction.ADDED,
            after=f.smb_ace_state(trustee=f.EVERYONE, right_token="full"),
            related_key=f.EVERYONE,
        )
        assert level.grants_full_control
        assert evaluate(level).rule_id == "ace.allow.broad.full"

    def test_a_share_ace_read_level_is_not_full_control(self) -> None:
        level = facts(
            ObservationKind.SMB_ACE,
            ChangeAction.ADDED,
            after=f.smb_ace_state(trustee=f.EVERYONE, right_token="read"),
            related_key=f.EVERYONE,
        )
        assert not level.grants_full_control
        assert not level.grants_write


class TestWhoCountsAsBroadOrPrivileged:
    @pytest.mark.parametrize(
        "key",
        ["S-1-1-0", "S-1-5-11", "S-1-5-32-545", f"{f.DOMAIN}-513", "fs01|S-1-5-32-545"],
    )
    def test_broad(self, key: str) -> None:
        assert is_broad_trustee(key)

    @pytest.mark.parametrize("key", ["S-1-5-18", "S-1-3-0", f.ALICE, f.FINANCE_RW])
    def test_not_broad(self, key: str) -> None:
        """SYSTEM and CREATOR OWNER are on nearly every ACL Windows creates.

        Scoring them would make the severity column unreadable, which is the failure that
        matters more than any single missed finding.
        """
        assert not is_broad_trustee(key)

    @pytest.mark.parametrize("key", ["S-1-5-32-544", f"{f.DOMAIN}-512", f"{f.DOMAIN}-519"])
    def test_privileged(self, key: str) -> None:
        assert is_privileged_group(key)

    @pytest.mark.parametrize("key", [f.ALICE, "S-1-1-0", f"{f.DOMAIN}-513"])
    def test_not_privileged(self, key: str) -> None:
        assert not is_privileged_group(key)

    def test_a_rid_is_only_matched_on_a_domain_relative_sid(self) -> None:
        """``S-1-5-32-512`` must never be read as ``Domain Admins``."""
        assert not is_privileged_group("S-1-5-32-512")

    def test_a_host_scoped_key_resolves_to_its_sid(self) -> None:
        sid = sid_of_key("fs01|S-1-5-32-544")
        assert sid is not None and sid.value == "S-1-5-32-544"

    def test_a_value_that_is_not_a_key_resolves_to_nothing(self) -> None:
        assert sid_of_key("fs01|finance") is None
        assert sid_of_key(None) is None
        assert not is_broad_trustee("fs01|finance")

    def test_a_reason_names_a_trustee_by_its_well_known_name(self) -> None:
        assert trustee_display("S-1-1-0") == "Everyone"
        assert trustee_display(f.ALICE) == f.ALICE
