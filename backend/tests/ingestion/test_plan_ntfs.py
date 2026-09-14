r"""Planning the NTFS half of a batch: keys, trustee scoping, and the acl_hash check.

No database here, for the same reason as the other planning tests: that a stored key equals
the key the contract derived, that a BUILTIN trustee is scoped to the server whose
descriptor named it, and that a collector's two statements about one DACL are checked
against each other, are all properties of the translation.

The layer link is checked here too. ``ntfs_resources.share_key`` is what lets one share
report its raw SMB ACL and the raw NTFS ACL of its root as two separate answers, and it is
derived from the path rather than from anything the collector says about it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from app.contracts.v1 import (
    NtfsAceObservation,
    NtfsResourceObservation,
    ObservationBatch,
    keys,
)
from app.domain import AceType, AclAceFacts, Sid, acl_hash
from app.ingestion import AclHashMismatch, plan_batch
from app.models.schema import ReferenceKind

RUN_ID = "6f1c1a52-3f4a-4a16-9f2e-1d6b0f2a7c31"
BATCH_ID = "b1a7c0de-1111-4222-8333-444455556666"
OBSERVED = dt.datetime(2026, 9, 14, 8, tzinfo=dt.UTC)
LATER = dt.datetime(2026, 9, 14, 9, tzinfo=dt.UTC)

FINANCE = "\\\\FS01\\Finance"
FINANCE_KEY = "\\\\fs01\\finance"
FINANCE_RW = "S-1-5-21-1004336348-1177238915-682003330-1202"
ADMINISTRATORS = "S-1-5-32-544"

MODIFY = 0x001301BF
FULL_CONTROL = 0x001F01FF


def resource(**overrides: object) -> NtfsResourceObservation:
    payload: dict[str, object] = {
        "kind": "ntfs_resource",
        "run_id": RUN_ID,
        "observed_at": OBSERVED,
        "path": FINANCE,
        "dacl_present": True,
        "ace_count": 0,
        **overrides,
    }
    payload.setdefault("source_key", keys.ntfs_resource_key(str(payload["path"])))
    return NtfsResourceObservation.model_validate(payload)


def ace(**overrides: object) -> NtfsAceObservation:
    payload: dict[str, object] = {
        "kind": "ntfs_ace",
        "run_id": RUN_ID,
        "observed_at": OBSERVED,
        "path": FINANCE,
        "trustee_sid": FINANCE_RW,
        "ace_type": "allow",
        "access_mask": MODIFY,
        "ace_flags": 0x03,
        "source": "explicit",
        "order_index": 0,
        **overrides,
    }
    payload.setdefault(
        "source_key",
        keys.ntfs_ace_key(
            str(payload["path"]),
            Sid(str(payload["trustee_sid"])),
            str(payload["ace_type"]),
            int(str(payload["access_mask"])),
            int(str(payload["ace_flags"])),
        ),
    )
    return NtfsAceObservation.model_validate(payload)


def batch(*observations: object, **overrides: object) -> ObservationBatch:
    payload: dict[str, object] = {
        "run_id": RUN_ID,
        "batch_id": BATCH_ID,
        "sequence": 1,
        "is_final": True,
        "observations": [item.model_dump(mode="json") for item in observations],  # type: ignore[attr-defined]
        **overrides,
    }
    return ObservationBatch.model_validate(payload)


def digest_of(*aces: NtfsAceObservation, protected: bool = False) -> str:
    return acl_hash(
        dacl_present=True,
        dacl_protected=protected,
        aces=[
            AclAceFacts(
                trustee_sid=item.trustee_sid,
                ace_type=AceType(item.ace_type),
                access_mask=item.access_mask,
                ace_flags=item.ace_flags,
                order_index=item.order_index,
            )
            for item in aces
        ],
    )


class TestTheResourceRow:
    def test_the_key_is_the_case_folded_path(self) -> None:
        plan = plan_batch(batch(resource(path="\\\\fs01\\FINANCE")))
        assert plan.ntfs_resources[0].resource_key == FINANCE_KEY

    def test_the_path_keeps_the_spelling_that_was_observed(self) -> None:
        # Comparison folds case; display does not. An operator recognizes "FS01".
        plan = plan_batch(batch(resource(path=FINANCE)))
        assert plan.ntfs_resources[0].path == FINANCE

    def test_the_share_link_is_derived_from_the_path(self) -> None:
        # This column is what lets one share show its raw SMB ACL and the raw NTFS ACL of
        # its root as two separate answers.
        plan = plan_batch(batch(resource()))
        assert plan.ntfs_resources[0].share_key == "fs01|finance"
        assert plan.ntfs_resources[0].server_key == "fs01"

    def test_a_collector_cannot_attach_a_directory_to_another_share(self) -> None:
        # server_name and share_name are conveniences; the path is the identity. Letting
        # them disagree would report one share's NTFS root under another share's name.
        with pytest.raises(ValidationError) as caught:
            resource(share_name="Payroll")
        assert "contradicts the path" in str(caught.value)

    def test_a_null_dacl_is_stored_as_absent_rather_than_empty(self) -> None:
        plan = plan_batch(batch(resource(dacl_present=False, ace_count=0)))
        row = plan.ntfs_resources[0]
        assert row.dacl_present is False
        assert row.ace_count == 0

    def test_a_protected_dacl_blocks_inheritance_and_is_a_boundary(self) -> None:
        plan = plan_batch(
            batch(resource(dacl_protected=True, inheritance_enabled=False, is_acl_boundary=True))
        )
        row = plan.ntfs_resources[0]
        assert (row.dacl_protected, row.inheritance_enabled, row.is_acl_boundary) == (
            True,
            False,
            True,
        )

    def test_ace_count_is_what_the_descriptor_said_not_what_arrived(self) -> None:
        # They can differ when a batch carries part of a DACL, and when they do that is the
        # finding: entries were read and never stored.
        plan = plan_batch(batch(resource(ace_count=5), ace()))
        assert plan.ntfs_resources[0].ace_count == 5
        assert len(plan.ntfs_aces) == 1

    def test_one_directory_cannot_be_described_twice_in_one_batch(self) -> None:
        # The resource key is a function of the source key, so two readings of one directory
        # collide on source_key and the envelope refuses the batch. The planner keeps a
        # newest-wins rule behind that anyway, so a future relaxation cannot turn into a
        # "cannot affect row a second time" failure deep inside an upsert.
        with pytest.raises(ValidationError) as caught:
            batch(resource(owner_sid=ADMINISTRATORS), resource(owner_sid=FINANCE_RW))
        assert "same source_key twice" in str(caught.value)

    def test_the_newest_reading_of_a_directory_wins_across_batches(self) -> None:
        # Which is how it actually arrives: one batch per run, newest run last.
        older = plan_batch(batch(resource(owner_sid=ADMINISTRATORS)))
        newer = plan_batch(
            batch(resource(owner_sid=FINANCE_RW, observed_at=LATER), batch_id=BATCH_ID)
        )
        assert older.ntfs_resources[0].resource_key == newer.ntfs_resources[0].resource_key
        assert newer.ntfs_resources[0].observed_at > older.ntfs_resources[0].observed_at


class TestTheAceRows:
    def test_the_key_comes_from_the_domain_and_matches_the_contract(self) -> None:
        entry = ace()
        plan = plan_batch(batch(resource(ace_count=1), entry))
        assert plan.ntfs_aces[0].ace_key == entry.source_key.removeprefix("ntfs_ace|")
        assert plan.ntfs_aces[0].resource_key == FINANCE_KEY

    def test_the_raw_flags_byte_survives_intact(self) -> None:
        # Inheritance and propagation are one byte in the descriptor. Splitting them into
        # columns would make a round trip lossy for any bit Windows adds later.
        plan = plan_batch(batch(resource(ace_count=1), ace(ace_flags=0x13, source="inherited")))
        assert plan.ntfs_aces[0].ace_flags == 0x13
        assert plan.ntfs_aces[0].source == "inherited"

    def test_an_explicit_entry_and_an_inherited_one_are_different_rows(self) -> None:
        explicit = ace(ace_flags=0x03, source="explicit")
        inherited = ace(ace_flags=0x13, source="inherited")
        plan = plan_batch(batch(resource(ace_count=2), explicit, inherited))
        assert len({row.ace_key for row in plan.ntfs_aces}) == 2

    def test_a_deny_is_stored_as_deny_with_its_position(self) -> None:
        plan = plan_batch(batch(resource(ace_count=1), ace(ace_type="deny", order_index=0)))
        assert plan.ntfs_aces[0].ace_type == "deny"
        assert plan.ntfs_aces[0].order_index == 0

    def test_reordering_an_acl_does_not_recreate_its_rows(self) -> None:
        # order_index is stored but is not identity: an administrator reordering a DACL must
        # not look like every entry being deleted and recreated.
        first = plan_batch(batch(resource(ace_count=1), ace(order_index=0)))
        moved = plan_batch(batch(resource(ace_count=1), ace(order_index=4)))
        assert first.ntfs_aces[0].ace_key == moved.ntfs_aces[0].ace_key
        assert moved.ntfs_aces[0].order_index == 4


class TestTrusteeScoping:
    def test_a_builtin_trustee_is_scoped_to_the_server_in_the_path(self) -> None:
        # The descriptor was read on that machine, so S-1-5-32-544 means *its* local
        # Administrators, not every computer's.
        plan = plan_batch(
            batch(resource(ace_count=1), ace(trustee_sid=ADMINISTRATORS, access_mask=FULL_CONTROL))
        )
        assert plan.ntfs_aces[0].trustee_key == f"fs01|{ADMINISTRATORS}"

    def test_a_domain_trustee_keeps_its_global_key(self) -> None:
        plan = plan_batch(batch(resource(ace_count=1), ace()))
        assert plan.ntfs_aces[0].trustee_key == FINANCE_RW

    def test_a_directory_and_a_share_agree_about_one_builtin_trustee(self) -> None:
        # Both layers go through app.domain.referenced_principal_key, so an NTFS ACE and a
        # share ACE naming the same group on the same server land on the same principal.
        plan = plan_batch(
            batch(resource(ace_count=1), ace(trustee_sid=ADMINISTRATORS, access_mask=FULL_CONTROL))
        )
        assert plan.ntfs_aces[0].trustee_key.startswith("fs01|")

    def test_nothing_records_whether_the_trustee_is_known(self) -> None:
        # Resolution is a join at query time. A stored flag would be right only until the
        # next AD run described the SID.
        plan = plan_batch(batch(resource(ace_count=1), ace(trustee_sid="S-1-5-21-1-2-3-9999")))
        assert not hasattr(plan.ntfs_aces[0], "resolved")


class TestReferences:
    def test_an_ace_records_that_a_directory_names_its_trustee(self) -> None:
        plan = plan_batch(batch(resource(ace_count=1), ace()))
        assert len(plan.references) == 1
        reference = plan.references[0]
        assert reference.reference_kind == ReferenceKind.NTFS_ACE.value
        assert reference.reference_key == FINANCE_KEY
        assert reference.principal_key == FINANCE_RW

    def test_one_reference_however_many_entries_name_the_trustee(self) -> None:
        # Three entries for one group is still one group the directory's DACL mentions.
        plan = plan_batch(
            batch(
                resource(ace_count=2),
                ace(ace_flags=0x03, order_index=0),
                ace(ace_flags=0x0B, order_index=1),
            )
        )
        assert len(plan.ntfs_aces) == 2
        assert len(plan.references) == 1

    def test_a_host_key_is_recorded_only_when_scoping_applied(self) -> None:
        scoped = plan_batch(
            batch(resource(ace_count=1), ace(trustee_sid=ADMINISTRATORS, access_mask=FULL_CONTROL))
        )
        assert scoped.references[0].host_key == "fs01"

        global_sid = plan_batch(batch(resource(ace_count=1), ace()))
        assert global_sid.references[0].host_key is None

    def test_the_two_layers_use_different_reference_kinds(self) -> None:
        # A share key and a directory key live in different namespaces; reference_kind is
        # what keeps "which resources name this SID" from confusing them.
        plan = plan_batch(batch(resource(ace_count=1), ace()))
        assert plan.references[0].reference_kind != ReferenceKind.SMB_ACE.value


class TestTheAclHashCheck:
    def test_a_matching_digest_is_stored_as_reported(self) -> None:
        entry = ace()
        plan = plan_batch(batch(resource(ace_count=1, acl_hash=digest_of(entry)), entry))
        assert plan.ntfs_resources[0].acl_hash == digest_of(entry)

    def test_a_contradicting_digest_is_refused(self) -> None:
        # Both statements came from the same collector about the same descriptor, so one is
        # wrong and there is no way to tell which. Storing either would record an ACL that
        # was never read.
        entry = ace()
        with pytest.raises(AclHashMismatch) as caught:
            plan_batch(batch(resource(ace_count=1, acl_hash="0" * 64), entry))
        assert caught.value.reported == "0" * 64
        assert caught.value.computed == digest_of(entry)

    def test_the_refusal_prints_the_document_it_hashed(self) -> None:
        # "Which entry differs" is what an operator has to answer, and it is only
        # answerable if the thing the server hashed can be read.
        entry = ace()
        with pytest.raises(AclHashMismatch) as caught:
            plan_batch(batch(resource(ace_count=1, acl_hash="0" * 64), entry))
        assert "adg-acl/1" in caught.value.normal_form
        assert FINANCE_RW in caught.value.normal_form

    def test_a_partial_batch_is_not_checked(self) -> None:
        # The collector declared five entries and sent one, which is a split batch rather
        # than a contradiction. Checking it would reject a collector that did nothing wrong.
        entry = ace()
        plan = plan_batch(batch(resource(ace_count=5, acl_hash="0" * 64), entry))
        assert plan.ntfs_resources[0].acl_hash == "0" * 64

    def test_a_resource_arriving_without_its_entries_is_not_checked(self) -> None:
        plan = plan_batch(batch(resource(ace_count=3, acl_hash="0" * 64)))
        assert plan.ntfs_resources[0].acl_hash == "0" * 64

    def test_a_resource_reporting_no_digest_is_accepted(self) -> None:
        # Optional and additive: a contract 1.0 or 1.1 collector sends none, and that is
        # not the same as sending a wrong one.
        entry = ace()
        plan = plan_batch(batch(resource(ace_count=1), entry))
        assert plan.ntfs_resources[0].acl_hash is None

    def test_an_empty_dacl_is_checked_against_its_own_digest(self) -> None:
        empty = acl_hash(dacl_present=True, aces=[])
        plan = plan_batch(batch(resource(ace_count=0, acl_hash=empty)))
        assert plan.ntfs_resources[0].acl_hash == empty

    def test_a_null_dacl_hashes_differently_from_an_empty_one(self) -> None:
        null = acl_hash(dacl_present=False)
        with pytest.raises(AclHashMismatch):
            plan_batch(batch(resource(dacl_present=True, ace_count=0, acl_hash=null)))

    def test_a_malformed_digest_never_reaches_the_planner(self) -> None:
        with pytest.raises(ValidationError) as caught:
            resource(acl_hash="not-a-digest")
        assert "hexadecimal" in str(caught.value)

    def test_a_digest_is_accepted_in_either_letter_case(self) -> None:
        # Normalized on the way in, so one digest has one spelling in storage.
        entry = ace()
        upper = digest_of(entry).upper()
        plan = plan_batch(batch(resource(ace_count=1, acl_hash=upper), entry))
        assert plan.ntfs_resources[0].acl_hash == digest_of(entry)


class TestBothLayersTogether:
    def test_a_batch_may_carry_the_share_layer_and_the_file_system_layer(self) -> None:
        # Natural for a combined run, and nothing merges them: they become rows in separate
        # tables, and no code path here intersects the two.
        from tests.ingestion.test_plan_resources import ace as share_ace
        from tests.ingestion.test_plan_resources import share

        plan = plan_batch(batch(share(), share_ace(), resource(ace_count=1), ace()))
        assert len(plan.shares) == 1
        assert len(plan.share_aces) == 1
        assert len(plan.ntfs_resources) == 1
        assert len(plan.ntfs_aces) == 1
        assert {reference.reference_kind for reference in plan.references} == {
            ReferenceKind.SMB_ACE.value,
            ReferenceKind.NTFS_ACE.value,
        }

    def test_the_observation_count_is_what_was_sent(self) -> None:
        # Two entries that differ only in position share a source_key - order is not part of
        # an ACE's identity - so a batch needs two genuinely different ones.
        plan = plan_batch(
            batch(
                resource(ace_count=2),
                ace(order_index=0),
                ace(trustee_sid=ADMINISTRATORS, access_mask=FULL_CONTROL, order_index=1),
            )
        )
        assert plan.observation_count == 3
