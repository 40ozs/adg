r"""What a reconciled scope may mark absent, as a table of rules rather than as behavior.

These tests are about *permission*, not about what a particular estate contains. The
question each one asks is "could this collector, reconciling this scope, ever close a row of
this kind", and the dangerous answers are the ones nobody would notice: an SMB run erasing
the file-system half of a server, a domain run erasing a machine's local groups.
"""

from __future__ import annotations

import pytest

from app.contracts.v1.common import ObservationKind, ScopeKind
from app.domain.errors import DomainValidationError
from app.domain.observation import CollectorKind
from app.history.bindings import BINDINGS
from app.history.closure import (
    CLOSURE_RULES,
    ColumnEquals,
    LocalScoping,
    RunDomains,
    SubtreeOfScopeKey,
    ViaParent,
    closure_rules_for,
    directory_subtree_of,
    reconcilable_kinds,
)


class TestACollectorMayOnlyCloseWhatItCanSee:
    def test_an_smb_run_reconciling_a_server_never_touches_the_file_system(self) -> None:
        kinds = reconcilable_kinds(CollectorKind.SMB, ScopeKind.SERVER)

        assert ObservationKind.NTFS_RESOURCE not in kinds
        assert ObservationKind.NTFS_ACE not in kinds

    def test_an_ntfs_run_reconciling_a_server_never_touches_the_share_list(self) -> None:
        kinds = reconcilable_kinds(CollectorKind.NTFS, ScopeKind.SERVER)

        assert kinds == {ObservationKind.NTFS_RESOURCE, ObservationKind.NTFS_ACE}

    def test_a_domain_run_closes_principals_and_edges_and_nothing_else(self) -> None:
        kinds = reconcilable_kinds(CollectorKind.ACTIVE_DIRECTORY, ScopeKind.DOMAIN)

        assert kinds == {ObservationKind.PRINCIPAL, ObservationKind.MEMBERSHIP_EDGE}

    @pytest.mark.parametrize(
        ("collector", "scope"),
        [
            (CollectorKind.SMB, ScopeKind.DIRECTORY_TREE),
            (CollectorKind.NTFS, ScopeKind.DOMAIN),
            (CollectorKind.ACTIVE_DIRECTORY, ScopeKind.SERVER),
            (CollectorKind.LOCAL_GROUPS, ScopeKind.SHARE),
        ],
    )
    def test_a_pair_with_no_rule_closes_nothing(
        self, collector: CollectorKind, scope: ScopeKind
    ) -> None:
        """An unbounded claim is ignored rather than guessed at."""
        assert closure_rules_for(collector, scope) == ()


class TestALocalGroupAndADomainGroupAreNeverConfused:
    def test_a_domain_run_may_not_close_a_host_scoped_principal(self) -> None:
        rules = {
            rule.kind: rule.selector
            for rule in CLOSURE_RULES[(CollectorKind.ACTIVE_DIRECTORY, ScopeKind.DOMAIN)]
        }
        principals = rules[ObservationKind.PRINCIPAL]
        edges = rules[ObservationKind.MEMBERSHIP_EDGE]

        assert isinstance(principals, RunDomains)
        assert principals.scoping is LocalScoping.DOMAIN_ONLY
        assert isinstance(edges, ViaParent)
        assert edges.scoping is LocalScoping.DOMAIN_ONLY

    def test_a_local_groups_run_may_only_close_host_scoped_rows(self) -> None:
        rules = CLOSURE_RULES[(CollectorKind.LOCAL_GROUPS, ScopeKind.LOCAL_GROUPS_HOST)]

        for rule in rules:
            selector = rule.selector
            assert isinstance(selector, ColumnEquals)
            assert selector.column == "host_key"
            assert selector.scoping is LocalScoping.HOST_ONLY


class TestAclEntriesAreSelectedThroughTheirParent:
    @pytest.mark.parametrize(
        ("collector", "scope", "kind", "parent"),
        [
            (
                CollectorKind.SMB,
                ScopeKind.SERVER,
                ObservationKind.SMB_ACE,
                ObservationKind.SMB_SHARE,
            ),
            (
                CollectorKind.SMB,
                ScopeKind.SHARE,
                ObservationKind.SMB_ACE,
                ObservationKind.SMB_SHARE,
            ),
            (
                CollectorKind.NTFS,
                ScopeKind.DIRECTORY_TREE,
                ObservationKind.NTFS_ACE,
                ObservationKind.NTFS_RESOURCE,
            ),
        ],
    )
    def test_an_ace_is_selected_by_the_object_it_belongs_to(
        self,
        collector: CollectorKind,
        scope: ScopeKind,
        kind: ObservationKind,
        parent: ObservationKind,
    ) -> None:
        """A collector reads a descriptor; the entries come with it."""
        rule = next(r for r in CLOSURE_RULES[(collector, scope)] if r.kind is kind)

        assert isinstance(rule.selector, ViaParent)
        assert rule.selector.parent is parent

    def test_a_dangling_parent_reference_is_refused_rather_than_selecting_nothing(self) -> None:
        from app.history.closure import ClosureRule

        broken = (
            ClosureRule(
                ObservationKind.NTFS_ACE,
                ViaParent("resource_key", ObservationKind.NTFS_RESOURCE),
            ),
        )
        original = CLOSURE_RULES.get((CollectorKind.SMB, ScopeKind.DOMAIN))
        CLOSURE_RULES[(CollectorKind.SMB, ScopeKind.DOMAIN)] = broken
        try:
            with pytest.raises(DomainValidationError, match="close nothing"):
                closure_rules_for(CollectorKind.SMB, ScopeKind.DOMAIN)
        finally:
            if original is None:
                del CLOSURE_RULES[(CollectorKind.SMB, ScopeKind.DOMAIN)]
            else:  # pragma: no cover - the pair has no rule today
                CLOSURE_RULES[(CollectorKind.SMB, ScopeKind.DOMAIN)] = original


class TestTheDirectoryTreeScope:
    def test_a_share_root_reduces_to_an_indexed_share_key(self) -> None:
        subtree = directory_subtree_of("\\\\FS01\\Finance")

        assert subtree.share_key == "fs01|finance"
        assert subtree.tree_key == "\\\\fs01\\finance"
        assert subtree.is_share_root

    def test_a_subdirectory_keeps_both_predicates(self) -> None:
        subtree = directory_subtree_of("\\\\fs01\\finance\\Reports")

        assert subtree.share_key == "fs01|finance"
        assert not subtree.is_share_root

    def test_a_scope_key_that_is_not_a_unc_path_is_refused_here(self) -> None:
        """The writer turns this into "this scope closes nothing" rather than a failed run."""
        with pytest.raises(DomainValidationError, match="not a UNC path"):
            directory_subtree_of("finance")

    def test_the_resource_rule_is_resolved_at_closure_time(self) -> None:
        rule = next(
            r
            for r in CLOSURE_RULES[(CollectorKind.NTFS, ScopeKind.DIRECTORY_TREE)]
            if r.kind is ObservationKind.NTFS_RESOURCE
        )

        assert isinstance(rule.selector, SubtreeOfScopeKey)


class TestEveryRuleIsAddressable:
    def test_every_selected_kind_has_a_storage_binding(self) -> None:
        for (collector, scope), rules in CLOSURE_RULES.items():
            for rule in rules:
                assert rule.kind in BINDINGS, f"{collector.value}/{scope.value}: {rule.kind}"

    def test_every_named_column_exists_on_its_table(self) -> None:
        for (collector, scope), rules in CLOSURE_RULES.items():
            for rule in rules:
                column = getattr(rule.selector, "column", None)
                if column is None:
                    continue
                table = BINDINGS[rule.kind].table
                assert column in table.c, (
                    f"{collector.value}/{scope.value}: {rule.kind.value} selects on "
                    f"{column!r}, which {table.name} does not have."
                )

    def test_a_scoped_rule_only_names_a_table_that_has_a_host_key(self) -> None:
        """Host scoping that silently does nothing would let a domain run close a local group."""
        for rules in CLOSURE_RULES.values():
            for rule in rules:
                scoping = getattr(rule.selector, "scoping", LocalScoping.ANY)
                if scoping is LocalScoping.ANY:
                    continue
                assert "host_key" in BINDINGS[rule.kind].table.c
