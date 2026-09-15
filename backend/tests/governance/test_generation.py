"""The item-generation policy: what a campaign asks about, and what it admits it did not.

Generation is the one part of a campaign that must be *reproducible*, so the properties
tested here are determinism and honesty about exclusions. Everything is pure: the grants are
built by hand, exactly as the repository would hand them over, so the policy can be exercised
in cases a real estate would take a fixture of hundreds of rows to produce.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain import AceSource, AceType, CampaignFocus, ReviewTargetKind, SharePermission
from app.governance.generation import (
    CampaignTooLarge,
    ExclusionReason,
    GenerationResult,
    ObservedGrant,
    generate_items,
)
from app.governance.model import GenerationOptions, GrantEvidence
from app.history.model import Certainty

FROM = dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.UTC)
SEEN = dt.datetime(2026, 3, 6, 9, 0, tzinfo=dt.UTC)

FINANCE = "\\\\fs01\\finance"
HR = "\\\\fs01\\hr"
ALICE = "S-1-5-21-1-2-3-1001"
GROUP = "S-1-5-21-1-2-3-2001"
SYSTEM = "S-1-5-18"
ADMINISTRATORS = "S-1-5-32-544"


def ntfs(
    resource: str = FINANCE,
    trustee: str = ALICE,
    *,
    mask: int = 0x1200A9,
    ace_type: AceType = AceType.ALLOW,
    source: AceSource = AceSource.EXPLICIT,
    flags: int = 0x0,
    certainty: Certainty = Certainty.OBSERVED,
    version_id: int = 1,
    path: str | None = None,
) -> ObservedGrant:
    ace_key = f"{resource}|{trustee}|{ace_type.value}|{mask:#010x}|{flags:#04x}"
    return ObservedGrant(
        target_kind=ReviewTargetKind.RESOURCE,
        target_key=resource,
        target_path=path if path is not None else resource,
        evidence=GrantEvidence(
            target_kind=ReviewTargetKind.RESOURCE,
            ace_key=ace_key,
            trustee_sid=trustee,
            trustee_key=trustee,
            ace_type=ace_type,
            access_mask=mask,
            permission=None,
            ace_flags=flags,
            source=source,
            inherited_from=None,
            order_index=0,
            version_id=version_id,
            observed_from=FROM,
            last_confirmed_at=SEEN,
            certainty=certainty,
        ),
    )


def share(
    share_key: str = "fs01|finance",
    trustee: str = ALICE,
    *,
    permission: SharePermission = SharePermission.CHANGE,
) -> ObservedGrant:
    return ObservedGrant(
        target_kind=ReviewTargetKind.SHARE,
        target_key=share_key,
        target_path=f"\\\\{share_key.replace('|', chr(92))}",
        evidence=GrantEvidence(
            target_kind=ReviewTargetKind.SHARE,
            ace_key=f"{share_key}|{trustee}|allow|{permission.value}",
            trustee_sid=trustee,
            trustee_key=trustee,
            ace_type=AceType.ALLOW,
            access_mask=None,
            permission=permission,
            ace_flags=None,
            source=None,
            inherited_from=None,
            order_index=0,
            version_id=7,
            observed_from=FROM,
            last_confirmed_at=SEEN,
            certainty=Certainty.OBSERVED,
        ),
    )


def run(grants: list[ObservedGrant], **options: bool) -> GenerationResult:
    return generate_items(
        focus=CampaignFocus.RESOURCE,
        options=GenerationOptions(**options),
        grants=grants,
    )


class TestAnItemIsAGrantNotAnEntry:
    def test_two_entries_for_one_pair_become_one_item(self) -> None:
        """An allow and a deny on the same principal and folder are one relation. Two items
        would ask somebody to certify half a grant, and let the two halves disagree."""
        result = run([ntfs(), ntfs(ace_type=AceType.DENY, mask=0x10000)])

        assert len(result.items) == 1
        assert len(result.items[0].grants) == 2

    def test_the_same_principal_on_two_folders_is_two_items(self) -> None:
        result = run([ntfs(resource=FINANCE), ntfs(resource=HR)])

        assert [item.target_key for item in result.items] == [FINANCE, HR]

    def test_two_principals_on_one_folder_are_two_items(self) -> None:
        result = run([ntfs(trustee=ALICE), ntfs(trustee=GROUP)])

        assert {item.principal_key for item in result.items} == {ALICE, GROUP}

    def test_the_two_acl_layers_stay_separate(self) -> None:
        """A share's permissions and the file system's DACL are removed in different places,
        so certifying one must not look like certifying the other."""
        result = run([share(trustee=ALICE), ntfs(trustee=ALICE)])

        assert {item.target_kind for item in result.items} == {
            ReviewTargetKind.SHARE,
            ReviewTargetKind.RESOURCE,
        }

    def test_an_item_reports_that_it_carries_a_deny(self) -> None:
        result = run([ntfs(), ntfs(ace_type=AceType.DENY, mask=0x10000)])

        assert any(entry.is_deny for entry in result.items[0].grants)


class TestGenerationIsDeterministic:
    def test_the_order_grants_arrive_in_does_not_change_the_result(self) -> None:
        """The order a query returned rows in is not part of the estate, and verification
        compares an ordered rendering — so two runs must agree exactly."""
        grants = [ntfs(resource=HR), ntfs(resource=FINANCE, trustee=GROUP), ntfs()]
        forward = run(grants)
        backward = run(list(reversed(grants)))

        assert [item.natural_key for item in forward.items] == [
            item.natural_key for item in backward.items
        ]
        assert forward.digest == backward.digest

    def test_entries_within_an_item_are_ordered_too(self) -> None:
        first = run([ntfs(mask=0x1F01FF), ntfs(mask=0x1200A9)])
        second = run([ntfs(mask=0x1200A9), ntfs(mask=0x1F01FF)])

        assert [entry.ace_key for entry in first.items[0].grants] == [
            entry.ace_key for entry in second.items[0].grants
        ]

    def test_one_added_grant_changes_the_campaign_digest(self) -> None:
        assert run([ntfs()]).digest != run([ntfs(), ntfs(resource=HR)]).digest


class TestEveryExclusionIsCounted:
    def test_inherited_entries_are_excluded_by_default_and_tallied(self) -> None:
        """An inherited entry cannot be removed where it sits — the fix is on the ancestor
        that defines it — so by default a campaign asks about the grants somebody can act on
        at the target. The count is what stops "12 of 12 certified" being read as coverage."""
        result = run([ntfs(), ntfs(resource=HR, source=AceSource.INHERITED)])

        assert len(result.items) == 1
        assert result.excluded == {ExclusionReason.INHERITED: 1}

    def test_inherited_entries_can_be_asked_for(self) -> None:
        result = run([ntfs(resource=HR, source=AceSource.INHERITED)], include_inherited=True)

        assert len(result.items) == 1
        assert result.excluded == {}

    @pytest.mark.parametrize("trustee", [SYSTEM, ADMINISTRATORS])
    def test_well_known_trustees_are_excluded_by_default(self, trustee: str) -> None:
        """They appear on nearly every descriptor. Reviewing them once is useful; reviewing
        them ten thousand times buries the grants that matter."""
        result = run([ntfs(), ntfs(trustee=trustee)])

        assert len(result.items) == 1
        assert result.excluded == {ExclusionReason.BUILTIN_TRUSTEE: 1}

    def test_well_known_trustees_can_be_asked_for(self) -> None:
        result = run([ntfs(trustee=SYSTEM)], include_builtin=True)

        assert len(result.items) == 1

    def test_deny_entries_are_included_by_default(self) -> None:
        """A deny is part of the grant: an item showing only the allow would ask somebody to
        certify access the principal does not actually have."""
        result = run([ntfs(ace_type=AceType.DENY, mask=0x10000)])

        assert len(result.items) == 1
        assert result.excluded == {}

    def test_deny_entries_can_be_left_out_and_are_tallied(self) -> None:
        result = run([ntfs(), ntfs(trustee=GROUP, ace_type=AceType.DENY)], include_deny=False)

        assert result.excluded == {ExclusionReason.DENY: 1}

    def test_a_grant_excluded_twice_over_is_counted_once_under_the_first_reason(self) -> None:
        """Deterministically the first, so the tally does not depend on dictionary order."""
        result = run([ntfs(trustee=SYSTEM, source=AceSource.INHERITED)])

        assert result.excluded == {ExclusionReason.INHERITED: 1}
        assert result.excluded_total == 1

    def test_an_unparsable_trustee_is_reviewed_rather_than_dismissed_as_boilerplate(self) -> None:
        """A SID that will not parse is a finding. Treating it as well-known would hide
        exactly the row worth looking at."""
        result = run([ntfs(trustee="not-a-sid")])

        assert len(result.items) == 1


class TestWhatAnItemSaysAboutItsOwnFooting:
    def test_the_weakest_entry_decides_the_item(self) -> None:
        result = run(
            [
                ntfs(mask=0x1200A9, certainty=Certainty.OBSERVED),
                ntfs(mask=0x1F01FF, certainty=Certainty.BACKFILLED),
            ]
        )

        assert result.items[0].certainty is Certainty.BACKFILLED

    def test_a_principal_nobody_had_described_gets_no_invented_name(self) -> None:
        """A missing name means the SID was on an access-control list and nothing described
        it *at the baseline*. Filling it in from the current tables would put a name on the
        reviewer's screen that nobody knew at the instant being reviewed."""
        result = generate_items(
            focus=CampaignFocus.RESOURCE,
            options=GenerationOptions(),
            grants=[ntfs(trustee=ALICE), ntfs(trustee=GROUP)],
            principal_names={ALICE: "CONTOSO\\alice"},
        )
        names = {item.principal_key: item.principal_display_name for item in result.items}

        assert names[ALICE] == "CONTOSO\\alice"
        assert names[GROUP] is None

    def test_a_path_is_taken_from_whichever_grant_has_one(self) -> None:
        """A version that happened not to carry the display spelling must not blank the whole
        item."""
        result = run([ntfs(path=None), ntfs(mask=0x1F01FF, path=FINANCE)])

        assert result.items[0].target_path == FINANCE


class TestTheCeilingRefusesRatherThanTruncates:
    def test_too_many_items_is_an_error_not_a_short_list(self) -> None:
        """A campaign that quietly dropped the rest would report complete coverage of a scope
        it never showed a reviewer, which is the exact failure an access review rules out."""
        grants = [ntfs(resource=f"\\\\fs01\\d{index}") for index in range(5)]

        with pytest.raises(CampaignTooLarge) as caught:
            generate_items(
                focus=CampaignFocus.RESOURCE,
                options=GenerationOptions(),
                grants=grants,
                ceiling=3,
            )

        message = str(caught.value)
        assert "5 review items" in message
        assert "may hold 3" in message
        assert "Narrow the scope" in message

    def test_exactly_the_ceiling_is_allowed(self) -> None:
        grants = [ntfs(resource=f"\\\\fs01\\d{index}") for index in range(3)]

        assert len(run_with_ceiling(grants, 3).items) == 3

    def test_an_unusually_complex_item_is_flagged_and_still_shown(self) -> None:
        """Not a refusal — the entries are all real and all shown — but a pair with dozens of
        entries is a descriptor somebody should look at for its own sake."""
        grants = [ntfs(mask=index) for index in range(1, 70)]
        result = run(grants)

        assert len(result.complex_items) == 1
        assert len(result.items[0].grants) == 69


def run_with_ceiling(grants: list[ObservedGrant], ceiling: int) -> GenerationResult:
    return generate_items(
        focus=CampaignFocus.RESOURCE,
        options=GenerationOptions(),
        grants=grants,
        ceiling=ceiling,
    )


class TestAPrincipalFocusedCampaign:
    def test_it_carries_its_focus_onto_every_item(self) -> None:
        """The focus is what the campaign claims completeness about, so an item has to say
        which question it was an answer to."""
        result = generate_items(
            focus=CampaignFocus.PRINCIPAL,
            options=GenerationOptions(),
            grants=[ntfs(resource=FINANCE), ntfs(resource=HR)],
        )

        assert {item.focus for item in result.items} == {CampaignFocus.PRINCIPAL}
