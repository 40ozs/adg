r"""What a scope selects, kind by kind — and what it must refuse to select.

Two failure modes, both quiet.

**Over-selection.** A scope that filters the kinds it understands and lets the rest through
produces a "changes on \\fs01\finance" page containing a domain group's display name. The
reader learns the filter does not work and stops using it.

**Under-selection.** A key spelled the way a human types it rather than the way the row
stores it matches nothing, and an empty change list is indistinguishable from a quiet week.
The three normalizations here each have their own case, including the one that is *not*
"lower-case it": a principal key is ``<host>|<SID>`` and the SID half is canonical.

Predicates are compared as rendered SQL. Asserting on the text is the only way to see that a
prefix test ends in a separator, that it is ``starts_with`` and not ``LIKE``, and that a
branch no row could satisfy is not there.
"""

from __future__ import annotations

import pytest
from sqlalchemy import ColumnElement

from app.changes.scope import ChangeScope, ScopeTarget, predicates_for, selected_kinds
from app.contracts.v1.common import ObservationKind
from app.domain.errors import DomainValidationError


def sql(predicate: ColumnElement[bool]) -> str:
    return str(predicate.compile(compile_kwargs={"literal_binds": True})).replace("\n", " ")


def rendered(target: ScopeTarget, key: str) -> dict[ObservationKind, str]:
    return {kind: sql(value) for kind, value in predicates_for(ChangeScope(target, key)).items()}


class TestAScopeExcludesWhatItCannotSpeakAbout:
    def test_a_share_scope_selects_only_what_lives_under_a_share(self) -> None:
        assert selected_kinds(ChangeScope(ScopeTarget.SHARE, "fs01|finance")) == {
            ObservationKind.SMB_SHARE,
            ObservationKind.SMB_ACE,
            ObservationKind.NTFS_RESOURCE,
            ObservationKind.NTFS_ACE,
        }

    def test_a_directory_scope_selects_only_the_file_system(self) -> None:
        assert selected_kinds(ChangeScope(ScopeTarget.DIRECTORY_TREE, "\\\\fs01\\finance")) == {
            ObservationKind.NTFS_RESOURCE,
            ObservationKind.NTFS_ACE,
        }

    def test_a_group_scope_is_narrower_than_a_principal_scope(self) -> None:
        """ "Who joined this group" and "what happened to this principal" are two questions.

        A filter that answered both at once would answer neither: a group scope must not
        return the ACEs that name the group as a trustee.
        """
        group = selected_kinds(ChangeScope(ScopeTarget.GROUP, "S-1-5-21-1-2-3-512"))
        principal = selected_kinds(ChangeScope(ScopeTarget.PRINCIPAL, "S-1-5-21-1-2-3-512"))
        assert group < principal
        assert ObservationKind.NTFS_ACE not in group
        assert ObservationKind.NTFS_ACE in principal

    def test_a_server_scope_reaches_every_kind_that_sits_on_a_server(self) -> None:
        assert selected_kinds(ChangeScope(ScopeTarget.SERVER, "fs01")) == set(ObservationKind)


class TestPrefixesAreSafe:
    def test_a_directory_prefix_ends_in_a_separator(self) -> None:
        r"""Without it, ``\\fs01\finance`` selects ``\\fs01\finance-archive``."""
        predicate = rendered(ScopeTarget.DIRECTORY_TREE, "\\\\fs01\\finance")[
            ObservationKind.NTFS_RESOURCE
        ]
        assert "starts_with(object_versions.object_key, '\\\\fs01\\finance\\')" in predicate

    def test_a_directory_tree_includes_its_own_root(self) -> None:
        predicate = rendered(ScopeTarget.DIRECTORY_TREE, "\\\\fs01\\finance")[
            ObservationKind.NTFS_RESOURCE
        ]
        assert "object_versions.object_key = '\\\\fs01\\finance'" in predicate

    def test_a_share_ace_is_never_equal_to_its_server_key(self) -> None:
        """So the server scope's predicate for it carries no equality branch.

        A branch no row can satisfy is not wrong; it is a claim nobody will ever check.
        """
        predicate = rendered(ScopeTarget.SERVER, "fs01")[ObservationKind.SMB_ACE]
        assert predicate == "starts_with(object_versions.object_key, 'fs01|')"

    @pytest.mark.parametrize("target", list(ScopeTarget))
    def test_nothing_uses_like(self, target: ScopeTarget) -> None:
        """PostgreSQL's default LIKE escape is a backslash — the UNC separator — and ``_``
        is a wildcard and an ordinary character in Windows folder names."""
        keys = {
            ScopeTarget.SERVER: "fs01",
            ScopeTarget.SHARE: "fs01|finance",
            ScopeTarget.DIRECTORY_TREE: "\\\\fs01\\finance",
            ScopeTarget.PRINCIPAL: "S-1-5-21-1-2-3-1104",
            ScopeTarget.GROUP: "S-1-5-21-1-2-3-512",
        }
        for predicate in rendered(target, keys[target]).values():
            assert "LIKE" not in predicate.upper()


class TestKeysAreSpelledTheWayRowsAreSpelled:
    def test_a_server_name_is_case_folded(self) -> None:
        assert (
            rendered(ScopeTarget.SERVER, "FS01")[ObservationKind.SERVER]
            == "object_versions.object_key = 'fs01'"
        )

    def test_a_path_is_canonicalized_however_it_was_typed(self) -> None:
        r"""``\\FS01\Finance\``, ``//fs01/finance`` and ``\\fs01\finance`` are one scope."""
        spellings = ["\\\\FS01\\Finance\\", "//fs01/finance", "\\\\fs01\\finance"]
        rendered_forms = {
            rendered(ScopeTarget.DIRECTORY_TREE, spelling)[ObservationKind.NTFS_RESOURCE]
            for spelling in spellings
        }
        assert len(rendered_forms) == 1

    def test_a_principal_key_is_not_case_folded(self) -> None:
        """This is the normalization that is *not* "lower-case it".

        ``principal_key`` is ``<case-folded host>|<canonical SID>``. Folding the whole key
        would produce ``s-1-5-32-544``, which matches no row ADG has ever stored — and would
        look exactly like a principal with no changes.
        """
        predicate = rendered(ScopeTarget.PRINCIPAL, "FS01|S-1-5-32-544")[ObservationKind.PRINCIPAL]
        assert predicate == "object_versions.object_key = 'fs01|S-1-5-32-544'"

    def test_a_bare_sid_keeps_its_canonical_spelling(self) -> None:
        predicate = rendered(ScopeTarget.PRINCIPAL, "s-1-5-21-1-2-3-1104")[
            ObservationKind.PRINCIPAL
        ]
        assert predicate == "object_versions.object_key = 'S-1-5-21-1-2-3-1104'"

    def test_a_share_scope_derives_the_unc_spelling_of_the_same_share(self) -> None:
        """An NTFS ACE's container is a directory, keyed by path; the share is keyed by
        ``server|share``. One share, two spellings, and the scope needs both."""
        predicates = rendered(ScopeTarget.SHARE, "FS01|Finance")
        assert predicates[ObservationKind.SMB_ACE] == (
            "object_versions.container_key = 'fs01|finance'"
        )
        assert "'\\\\fs01\\finance\\'" in predicates[ObservationKind.NTFS_ACE]


class TestRefusals:
    def test_an_empty_key_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="needs a key"):
            ChangeScope(ScopeTarget.SERVER, "   ")

    def test_a_principal_scope_refuses_something_that_is_not_a_sid(self) -> None:
        """Selecting nothing and reporting it as an empty change list would be
        indistinguishable from a quiet week."""
        with pytest.raises(DomainValidationError, match="does not name a principal"):
            predicates_for(ChangeScope(ScopeTarget.PRINCIPAL, "Alice"))

    def test_a_share_scope_refuses_something_that_is_not_a_share_key(self) -> None:
        with pytest.raises(DomainValidationError, match="not a share key"):
            predicates_for(ChangeScope(ScopeTarget.SHARE, "finance"))

    def test_a_directory_scope_refuses_a_path_that_is_not_unc(self) -> None:
        with pytest.raises(DomainValidationError):
            predicates_for(ChangeScope(ScopeTarget.DIRECTORY_TREE, "D:\\Shares\\Finance"))


class TestMembershipIsAskedInBothDirections:
    def test_a_principal_scope_matches_either_end_of_an_edge(self) -> None:
        """A principal is the far end of the edges that put it into groups and the near end
        of the edges that put members into it. One direction would hide half of them."""
        predicate = rendered(ScopeTarget.PRINCIPAL, "S-1-5-21-1-2-3-1104")[
            ObservationKind.MEMBERSHIP_EDGE
        ]
        assert "container_key = 'S-1-5-21-1-2-3-1104'" in predicate
        assert "related_key = 'S-1-5-21-1-2-3-1104'" in predicate

    def test_a_group_scope_matches_only_its_own_membership(self) -> None:
        predicate = rendered(ScopeTarget.GROUP, "S-1-5-21-1-2-3-512")[
            ObservationKind.MEMBERSHIP_EDGE
        ]
        assert predicate == "object_versions.container_key = 'S-1-5-21-1-2-3-512'"
