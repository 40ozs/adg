"""Which reads the as-of repositories actually make historical, named out loud.

:class:`app.history.repository.HistoricalMembershipRepository` and its resource counterpart
are *subclasses*. That is what lets the live effective-access engine answer about a past
instant with no changes to the engine — and it is also a hazard, because a method that is
not overridden silently returns current state.

The hazard is handled by naming, here, every public read of each base class that the
historical form deliberately does not provide. A method added to a base repository therefore
fails this test until somebody decides which of the two it is, rather than joining the
inherited set by default.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.history.repository import HistoricalMembershipRepository, HistoricalResourceRepository
from app.repositories.membership import MembershipRepository
from app.repositories.resources import ResourceRepository

#: Membership reads the historical form does not provide, and why each is safe to leave.
#:
#: All of them are listing and lookup surfaces behind current-state API endpoints. None is
#: reached by an access resolution or a graph traversal, which are the only two paths the
#: as-of repositories are used through — asserted below.
CURRENT_STATE_ONLY_MEMBERSHIP = frozenset(
    {
        "aliases_for",  # every name ever observed; a timeline in its own right, not a snapshot
        "count_direct",  # a count for a pagination header on a live listing
        "direct_groups",  # the paged live listing; the as-of form is HistoryService
        "direct_members",  # likewise
        # "has anything ever described this SID" — not a question about an instant.
        "is_known",
        # Identifier-to-principal lookup at the API boundary, before any instant is known.
        "resolve",
    }
)

#: The same, for resources. Every entry is a paged listing, a count, or a search surface
#: that belongs to a current-state screen.
CURRENT_STATE_ONLY_RESOURCES = frozenset(
    {
        # Paged listings, counts and search surfaces behind current-state screens.
        "count_acl",
        "count_ntfs_acl",
        "count_servers",
        "count_shares",
        "count_shares_referencing",
        "list_servers",
        "list_shares",
        "ntfs_acl",
        "share_acl",
        "resources_named_by",
        "shares_named_by",
        "shares_referencing",
        "reference_keys_for",
        # Lookups an access resolution does not make. ``get_share_root_resource`` is one a
        # future as-of answer would want; it is named here rather than half-implemented.
        "get_server",
        "get_share_root_resource",
        "servers_by_keys",
        "shares_by_keys",
        "has_aces",
        "has_ntfs_aces",
        # Bulk fan-out readers for the resource-to-principals direction, which the as-of
        # service does not offer yet.
        "ntfs_acls_for",
        "share_acls_for",
        # Verification of the collector's own claims against stored state. Both are
        # questions about what was collected, not about what was true at an instant.
        "recompute_acl_hash",
        "verify_boundary",
    }
)


def public_reads(cls: type) -> set[str]:
    """Every public coroutine method a repository exposes."""
    return {
        name
        for name, member in inspect.getmembers(cls, inspect.iscoroutinefunction)
        if not name.startswith("_")
    }


@pytest.mark.parametrize(
    ("base", "historical", "named"),
    [
        (MembershipRepository, HistoricalMembershipRepository, CURRENT_STATE_ONLY_MEMBERSHIP),
        (ResourceRepository, HistoricalResourceRepository, CURRENT_STATE_ONLY_RESOURCES),
    ],
    ids=["membership", "resources"],
)
def test_every_read_is_either_historical_or_named(
    base: type, historical: type, named: frozenset[str]
) -> None:
    overridden = {
        name for name in public_reads(base) if getattr(historical, name) is not getattr(base, name)
    }
    unaccounted = public_reads(base) - overridden - named

    assert not unaccounted, (
        f"{base.__name__} gained public read(s) {sorted(unaccounted)}. Either override them in "
        f"{historical.__name__} so they answer as of the instant, or add them to the list in "
        "this module with a note saying why a current-state answer from them is safe. An "
        "inherited read on an as-of repository returns live data and says nothing about it."
    )


@pytest.mark.parametrize(
    ("base", "named"),
    [
        (MembershipRepository, CURRENT_STATE_ONLY_MEMBERSHIP),
        (ResourceRepository, CURRENT_STATE_ONLY_RESOURCES),
    ],
    ids=["membership", "resources"],
)
def test_the_named_list_holds_no_methods_that_no_longer_exist(
    base: type, named: frozenset[str]
) -> None:
    """A stale exemption is worse than none: it excuses a method nobody is looking at."""
    assert named <= public_reads(base), sorted(named - public_reads(base))


def test_the_overridden_set_covers_what_an_access_resolution_calls() -> None:
    """The methods ``AccessService._resolve_one`` reaches, pinned by name.

    This is the list the whole subclassing design rests on. If a resolution starts calling a
    repository method that is not in the historical set, a point-in-time answer would quietly
    mix today's facts into a question about last month, and nothing else in the suite would
    notice.
    """
    required_membership = {"get_principal", "neighbors", "principals_by_keys", "keys_with_members"}
    required_resources = {
        "get_ntfs_resource",
        "full_ntfs_acl",
        "ntfs_resources_by_keys",
        "get_share",
        "full_share_acl",
    }

    for base, historical, required in (
        (MembershipRepository, HistoricalMembershipRepository, required_membership),
        (ResourceRepository, HistoricalResourceRepository, required_resources),
    ):
        for name in required:
            original: Any = getattr(base, name)
            assert getattr(historical, name) is not original, f"{historical.__name__}.{name}"
