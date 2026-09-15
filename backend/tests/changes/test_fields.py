"""The field table must cover every column, and the container table every kind.

Both tables are the kind that go stale silently. A column added to ``ntfs_aces`` and not to
:data:`app.changes.fields.FIELD_SIGNIFICANCE` would be classified
:attr:`FieldSignificance.UNCLASSIFIED` at runtime — which is the safe behavior and still not
good enough, because the right time to decide what a change to a new column means is when
the column is added, by the person adding it, and not months later by whoever is reading a
change feed full of "a field changed that ADG has no classification for".

So the exhaustiveness check is derived from the database rather than restated: it walks
``app.history.bindings.BINDINGS``, subtracts exactly what a version's interval already
carries, and compares. Two hand-written lists would agree until they did not.
"""

from __future__ import annotations

import pytest

from app.changes.fields import (
    CONTAINER_KINDS,
    FIELD_SIGNIFICANCE,
    RELATED_KINDS,
    container_kind_of,
    related_kind_of,
    significance_of,
    state_columns_of,
)
from app.changes.model import FieldSignificance
from app.contracts.v1.common import ObservationKind
from app.history.bindings import BINDINGS


class TestTheFieldTableIsExhaustive:
    @pytest.mark.parametrize("kind", list(ObservationKind), ids=lambda kind: kind.value)
    def test_every_stored_column_is_classified(self, kind: ObservationKind) -> None:
        missing = state_columns_of(kind) - set(FIELD_SIGNIFICANCE[kind])
        assert not missing, (
            f"{kind.value} stores {sorted(missing)} and app/changes/fields.py says nothing "
            "about what a change to them means. Decide now: security, metadata, noise, "
            "identity, order or derived."
        )

    @pytest.mark.parametrize("kind", list(ObservationKind), ids=lambda kind: kind.value)
    def test_it_classifies_nothing_that_is_not_stored(self, kind: ObservationKind) -> None:
        """A stale entry is as bad as a missing one: it is a rule nothing can ever apply."""
        extra = set(FIELD_SIGNIFICANCE[kind]) - state_columns_of(kind)
        assert not extra, f"{kind.value} classifies {sorted(extra)}, which it does not store."

    def test_unclassified_is_unreachable_in_a_sound_build(self) -> None:
        """The value exists for the build that has drifted, and this asserts none has."""
        reached = [
            (kind.value, name)
            for kind in ObservationKind
            for name in state_columns_of(kind)
            if significance_of(kind, name) is None
        ]
        assert not reached

    def test_the_provenance_column_is_noise_everywhere(self) -> None:
        """``source_key`` names the observation, not the object.

        It differs between two runs that read identical facts, so classifying it as anything
        else would make every re-observation a change. It is the reason a change list is not
        a scan log.
        """
        for kind in ObservationKind:
            assert significance_of(kind, "source_key") is FieldSignificance.NOISE

    def test_the_key_column_is_identity_everywhere(self) -> None:
        for kind, binding in BINDINGS.items():
            assert significance_of(kind, binding.key_column) is FieldSignificance.IDENTITY


class TestTheJudgmentsWorthArguingAbout:
    """Each of these is a column somebody could reasonably have called metadata."""

    def test_an_accounts_enabled_flag_is_security(self) -> None:
        # A disabled account cannot authenticate, so every grant naming it is inert.
        # Enabling it activates all of them at once and no ACL moved.
        assert significance_of(ObservationKind.PRINCIPAL, "enabled") is FieldSignificance.SECURITY

    def test_a_groups_type_is_security(self) -> None:
        # A distribution group appears in no access token and grants nothing.
        assert (
            significance_of(ObservationKind.PRINCIPAL, "group_type") is FieldSignificance.SECURITY
        )

    def test_the_directory_a_share_publishes_is_security(self) -> None:
        # The share ACL does not move; the entire NTFS half of every answer about it does.
        assert (
            significance_of(ObservationKind.SMB_SHARE, "local_path") is FieldSignificance.SECURITY
        )

    def test_the_owner_of_a_directory_is_security(self) -> None:
        # An owner holds READ_CONTROL and WRITE_DAC whatever the DACL says.
        assert (
            significance_of(ObservationKind.NTFS_RESOURCE, "owner_sid")
            is FieldSignificance.SECURITY
        )

    def test_a_null_dacl_flag_is_security(self) -> None:
        assert (
            significance_of(ObservationKind.NTFS_RESOURCE, "dacl_present")
            is FieldSignificance.SECURITY
        )

    def test_inheritance_protection_is_security(self) -> None:
        assert (
            significance_of(ObservationKind.NTFS_RESOURCE, "dacl_protected")
            is FieldSignificance.SECURITY
        )

    def test_a_display_name_is_metadata(self) -> None:
        assert (
            significance_of(ObservationKind.PRINCIPAL, "display_name") is FieldSignificance.METADATA
        )

    def test_a_derived_verdict_is_derived_and_not_security(self) -> None:
        """``is_acl_boundary`` is ADG's own conclusion, recomputed from other columns.

        Scoring it would double-count: the thing that made it move — ``dacl_protected``, or
        the parent's ACL — is its own change with its own severity.
        """
        assert (
            significance_of(ObservationKind.NTFS_RESOURCE, "is_acl_boundary")
            is FieldSignificance.DERIVED
        )

    def test_the_ace_position_declines_to_answer(self) -> None:
        """It cannot be decided from the field alone; see app.changes.correlation."""
        for kind in (ObservationKind.SMB_ACE, ObservationKind.NTFS_ACE):
            assert significance_of(kind, "order_index") is FieldSignificance.ORDER

    def test_what_an_ace_grants_is_part_of_its_identity(self) -> None:
        """The reason an ACL edit is stored as a removal plus an addition."""
        assert (
            significance_of(ObservationKind.NTFS_ACE, "access_mask") is FieldSignificance.IDENTITY
        )
        assert significance_of(ObservationKind.SMB_ACE, "right_token") is FieldSignificance.IDENTITY


class TestTheContainerTable:
    @pytest.mark.parametrize("kind", list(ObservationKind), ids=lambda kind: kind.value)
    def test_every_kind_has_an_entry(self, kind: ObservationKind) -> None:
        """Including the two whose answer is ``None``, which is an answer and not a gap."""
        assert kind in CONTAINER_KINDS
        assert kind in RELATED_KINDS

    def test_a_kind_with_a_container_column_names_a_container_kind(self) -> None:
        """Every binding that stores a ``container_key`` must say what kind that key names.

        Otherwise the key is stored, indexed, and unusable for the one question it exists
        to answer here: was this object's container already being watched?
        """
        for kind, binding in BINDINGS.items():
            if binding.container_column is None:
                continue
            assert container_kind_of(kind) is not None, (
                f"{kind.value} stores container_key ({binding.container_column}) and "
                "CONTAINER_KINDS has no kind for it."
            )

    def test_a_kind_with_a_related_column_names_a_related_kind_or_declines(self) -> None:
        """``related_kind`` may legitimately be ``None``: a principal's far end is a domain
        SID, and a domain is not an object ADG stores."""
        for kind, binding in BINDINGS.items():
            if binding.related_column is None:
                assert related_kind_of(kind) is None

    def test_an_ace_is_contained_by_the_thing_whose_acl_it_is(self) -> None:
        assert container_kind_of(ObservationKind.NTFS_ACE) is ObservationKind.NTFS_RESOURCE
        assert container_kind_of(ObservationKind.SMB_ACE) is ObservationKind.SMB_SHARE

    def test_a_membership_edge_is_contained_by_its_group(self) -> None:
        """Which is what makes "a member joined a group ADG has been reading" an addition."""
        assert container_kind_of(ObservationKind.MEMBERSHIP_EDGE) is ObservationKind.PRINCIPAL

    def test_a_server_has_no_container(self) -> None:
        assert container_kind_of(ObservationKind.SERVER) is None
