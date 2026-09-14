"""The access token: what ADG observed, what it assumed, and what it refuses to guess.

The token is where an effective-access answer is most easily wrong in the direction nobody
notices. An ACE naming ``Authenticated Users`` grants a user access without naming them and
without any membership edge existing anywhere — so a resolver that matched only collected
memberships would report no access where Windows grants Read. The opposite mistake is just
as available: assuming a SID into a token that Windows would not put there reports access
nobody has.

So every SID in a token carries its origin, and every assumption is a finding.
"""

from __future__ import annotations

import pytest

from app.access_engine import (
    ANONYMOUS_LOGON_SID,
    AUTHENTICATED_USERS_SID,
    EVERYONE_SID,
    INTERACTIVE_SID,
    NETWORK_SID,
    AccessCondition,
    AccessPath,
    SidOrigin,
    SubjectFacts,
    SubjectToken,
    TokenAssumption,
    TokenSid,
    build_token,
    default_assumption,
)
from app.domain import DomainValidationError, PrincipalKind
from tests.access_engine.support import (
    ALICE,
    FINANCE_RW,
    FINANCE_TEAM,
    FS01_ADMINS,
    ORPHAN,
    entry_of,
    group_sid,
    user,
)


class TestWhatTheTokenContains:
    def test_the_subject_is_always_in_its_own_token(self):
        result = build_token(user(), access_path=AccessPath.LOCAL)

        assert result.contains(ALICE)
        assert result.entries[0].origin is SidOrigin.SUBJECT

    def test_observed_groups_keep_the_chain_that_reached_them(self):
        nested = group_sid(FINANCE_RW, depth=2, path=(ALICE, FINANCE_TEAM, FINANCE_RW))

        result = build_token(user(), [nested], access_path=AccessPath.LOCAL)

        assert entry_of(result, FINANCE_RW).path == (ALICE, FINANCE_TEAM, FINANCE_RW)
        assert entry_of(result, FINANCE_RW).depth == 2

    def test_a_host_scoped_local_group_keeps_its_host(self):
        """``fs01|S-1-5-32-544`` is a different trustee from the same SID on another server."""
        result = build_token(user(), [group_sid(FS01_ADMINS)], access_path=AccessPath.LOCAL)

        assert result.contains(FS01_ADMINS)
        assert not result.contains("S-1-5-32-544")

    def test_a_token_may_not_carry_one_key_twice(self):
        """Constructed directly, because :func:`build_token` de-duplicates for its callers.

        The guard still has to be on the type: a SID is in a token or it is not, and two
        entries for one key would let an ACE match by whichever came first.
        """
        with pytest.raises(DomainValidationError, match="same key twice"):
            SubjectToken(
                subject=user(),
                assumption=TokenAssumption.SIDS_ONLY,
                access_path=AccessPath.LOCAL,
                entries=(group_sid(FINANCE_RW), group_sid(FINANCE_RW, depth=3)),
            )

    def test_a_token_is_never_empty(self):
        with pytest.raises(DomainValidationError, match="at least the subject"):
            SubjectToken(
                subject=user(),
                assumption=TokenAssumption.SIDS_ONLY,
                access_path=AccessPath.LOCAL,
                entries=(),
            )

    def test_a_repeated_group_collapses_onto_the_observed_entry(self):
        """First occurrence wins, which is the one carrying the membership chain."""
        result = build_token(
            user(),
            [
                group_sid(FINANCE_RW, depth=1, path=(ALICE, FINANCE_RW)),
                group_sid(FINANCE_RW, depth=9, path=()),
            ],
            access_path=AccessPath.LOCAL,
            assumption=TokenAssumption.SIDS_ONLY,
        )

        assert len(result.entries) == 2
        assert entry_of(result, FINANCE_RW).depth == 1

    def test_a_group_that_is_also_the_subject_does_not_duplicate_it(self):
        result = build_token(
            user(FINANCE_RW),
            [group_sid(FINANCE_RW)],
            access_path=AccessPath.LOCAL,
            assumption=TokenAssumption.SIDS_ONLY,
        )

        assert len(result.entries) == 1
        assert result.entries[0].origin is SidOrigin.SUBJECT

    def test_group_keys_reports_only_the_observed_memberships(self):
        result = build_token(
            user(),
            [group_sid(FINANCE_RW)],
            access_path=AccessPath.REMOTE_SMB,
            assumption=TokenAssumption.AUTHENTICATED_USER,
        )

        assert result.group_keys == (FINANCE_RW,)


class TestTheAssumedSids:
    def test_an_authenticated_user_carries_everyone_and_authenticated_users(self):
        result = build_token(
            user(),
            access_path=AccessPath.REMOTE_SMB,
            assumption=TokenAssumption.AUTHENTICATED_USER,
        )

        assert result.contains(EVERYONE_SID)
        assert result.contains(AUTHENTICATED_USERS_SID)

    def test_remote_access_carries_the_network_sid_and_local_carries_interactive(self):
        remote = build_token(
            user(),
            access_path=AccessPath.REMOTE_SMB,
            assumption=TokenAssumption.AUTHENTICATED_USER,
        )
        local = build_token(
            user(),
            access_path=AccessPath.LOCAL,
            assumption=TokenAssumption.AUTHENTICATED_USER,
        )

        assert remote.contains(NETWORK_SID) and not remote.contains(INTERACTIVE_SID)
        assert local.contains(INTERACTIVE_SID) and not local.contains(NETWORK_SID)

    def test_an_anonymous_session_gets_no_authenticated_users(self):
        result = build_token(
            user(), access_path=AccessPath.REMOTE_SMB, assumption=TokenAssumption.ANONYMOUS
        )

        assert result.contains(ANONYMOUS_LOGON_SID)
        assert not result.contains(AUTHENTICATED_USERS_SID)

    def test_sids_only_assumes_nothing(self):
        result = build_token(
            user(),
            [group_sid(FINANCE_RW)],
            access_path=AccessPath.REMOTE_SMB,
            assumption=TokenAssumption.SIDS_ONLY,
        )

        assert result.keys == {ALICE, FINANCE_RW}
        assert result.assumed == ()

    def test_every_assumed_sid_is_reported_as_a_finding(self):
        result = build_token(
            user(),
            access_path=AccessPath.REMOTE_SMB,
            assumption=TokenAssumption.AUTHENTICATED_USER,
        )

        assumed = [
            finding
            for finding in result.findings
            if finding.condition is AccessCondition.ASSUMED_TOKEN_SIDS
        ]
        assert len(assumed) == 1
        assert assumed[0].detail["sids"] == [EVERYONE_SID, AUTHENTICATED_USERS_SID, NETWORK_SID]

    def test_an_assumption_never_overrides_an_observation(self):
        """A collector that reported the Everyone membership keeps its explanation."""
        observed = TokenSid(
            key=EVERYONE_SID,
            sid=EVERYONE_SID,
            origin=SidOrigin.GROUP_MEMBERSHIP,
            depth=1,
            path=(ALICE, EVERYONE_SID),
        )

        result = build_token(
            user(),
            [observed],
            access_path=AccessPath.REMOTE_SMB,
            assumption=TokenAssumption.AUTHENTICATED_USER,
        )

        assert entry_of(result, EVERYONE_SID).origin is SidOrigin.GROUP_MEMBERSHIP
        assert entry_of(result, EVERYONE_SID).path == (ALICE, EVERYONE_SID)

    def test_the_assumed_entries_are_identifiable_as_assumptions(self):
        result = build_token(
            user(),
            access_path=AccessPath.LOCAL,
            assumption=TokenAssumption.AUTHENTICATED_USER,
        )

        assert {entry.key for entry in result.assumed} == {
            EVERYONE_SID,
            AUTHENTICATED_USERS_SID,
            INTERACTIVE_SID,
        }


class TestChoosingAnAssumption:
    def test_a_user_is_an_authenticated_session(self):
        assert default_assumption(user()) is TokenAssumption.AUTHENTICATED_USER

    def test_a_well_known_sid_assumes_nothing_about_itself(self):
        """Asking what Everyone can reach must not widen the question to a logged-on user."""
        everyone = SubjectFacts(key=EVERYONE_SID, sid=EVERYONE_SID, kind=PrincipalKind.WELL_KNOWN)

        assert default_assumption(everyone) is TokenAssumption.SIDS_ONLY

    def test_anonymous_logon_is_an_anonymous_session(self):
        anonymous = SubjectFacts(
            key=ANONYMOUS_LOGON_SID, sid=ANONYMOUS_LOGON_SID, kind=PrincipalKind.WELL_KNOWN
        )

        assert default_assumption(anonymous) is TokenAssumption.ANONYMOUS

    def test_an_unresolved_sid_is_treated_as_an_authenticated_principal(self):
        """Erring toward the wider token; the uncertainty is reported, not hidden."""
        orphan = SubjectFacts(key=ORPHAN, sid=ORPHAN, kind=None)

        assert default_assumption(orphan) is TokenAssumption.AUTHENTICATED_USER

    def test_a_group_is_treated_as_an_authenticated_member_of_itself(self):
        rw = SubjectFacts(key=FINANCE_RW, sid=FINANCE_RW, kind=PrincipalKind.DOMAIN_GROUP)

        assert default_assumption(rw) is TokenAssumption.AUTHENTICATED_USER


class TestWhatTheTokenAdmitsItDoesNotKnow:
    def test_an_unresolved_subject_is_reported(self):
        result = build_token(
            SubjectFacts(key=ORPHAN, sid=ORPHAN, kind=None), access_path=AccessPath.LOCAL
        )

        assert AccessCondition.SUBJECT_UNRESOLVED in {
            finding.condition for finding in result.findings
        }

    def test_a_group_subject_is_reported_as_a_group(self):
        result = build_token(
            SubjectFacts(key=FINANCE_RW, sid=FINANCE_RW, kind=PrincipalKind.DOMAIN_GROUP),
            access_path=AccessPath.LOCAL,
        )

        assert AccessCondition.SUBJECT_IS_A_GROUP in {
            finding.condition for finding in result.findings
        }

    def test_a_truncated_traversal_is_reported(self):
        result = build_token(
            user(),
            [group_sid(FINANCE_RW)],
            access_path=AccessPath.LOCAL,
            membership_complete=False,
        )

        assert AccessCondition.MEMBERSHIP_TRUNCATED in {
            finding.condition for finding in result.findings
        }
        assert not result.membership_complete

    def test_a_complete_traversal_of_a_known_user_reports_only_the_assumption(self):
        result = build_token(
            user(),
            [group_sid(FINANCE_RW)],
            access_path=AccessPath.REMOTE_SMB,
            assumption=TokenAssumption.AUTHENTICATED_USER,
        )

        assert {finding.condition for finding in result.findings} == {
            AccessCondition.ASSUMED_TOKEN_SIDS
        }


class TestValidation:
    def test_a_subject_needs_a_key(self):
        with pytest.raises(DomainValidationError, match="storage key"):
            SubjectFacts(key="", sid=ALICE)

    def test_a_subject_sid_must_be_a_sid(self):
        with pytest.raises(DomainValidationError):
            SubjectFacts(key="nonsense", sid="nonsense")

    def test_a_token_sid_needs_a_key(self):
        with pytest.raises(DomainValidationError, match="storage key"):
            TokenSid(key="", sid=ALICE, origin=SidOrigin.SUBJECT)

    def test_a_negative_depth_is_refused(self):
        with pytest.raises(DomainValidationError, match="non-negative"):
            TokenSid(key=FINANCE_RW, sid=FINANCE_RW, origin=SidOrigin.GROUP_MEMBERSHIP, depth=-1)
