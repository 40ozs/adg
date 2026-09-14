"""The repository's row ceiling and the traversal's edge budget must stay paired.

A bounded traversal can only declare itself truncated when it is *offered* the edge that
exceeds its budget. If the repository stops reading rows at exactly the traversal's
``max_edges``, the traversal never sees that edge, every frontier looks exhausted, and the
answer reports ``complete: true`` while holding a fraction of the membership. That is the
worst failure this system has — a short member list presented as the whole one — and it is
invisible from inside the traversal, so it is pinned here at the seam instead.

No database is touched: the pairing is a property of how the dependency constructs the
repository, and the construction does not need a session.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from app.api.deps import membership_repository, traversal_limits
from app.domain import (
    DEFAULT_LIMITS,
    MAX_DEPTH_CEILING,
    MAX_EDGES_CEILING,
    MAX_NODES_CEILING,
    MAX_PATHS_CEILING,
    TraversalLimits,
)
from app.repositories import EDGE_FETCH_CEILING, MembershipRepository

NO_SESSION = cast(Any, None)
"""The constructor stores the session without using it, so the pairing is testable alone."""


@pytest.mark.parametrize(
    "max_edges",
    [1, 2, 100, 200_000, MAX_EDGES_CEILING - 1, MAX_EDGES_CEILING],
)
def test_the_repository_will_always_read_one_row_beyond_the_budget(max_edges: int) -> None:
    limits = TraversalLimits(max_depth=32, max_nodes=1_000, max_edges=max_edges, max_paths=10)

    repository = membership_repository(NO_SESSION, limits)

    assert repository.edge_fetch_limit == max_edges + 1, (
        "the probe row is what lets the traversal report max_edges truncation; without it "
        "a repository-truncated answer would be reported as complete"
    )


def test_the_pairing_survives_the_largest_budget_a_caller_can_ask_for() -> None:
    # The regression this exists for: clamping the repository to MAX_EDGES_CEILING took the
    # probe row away at exactly the ceiling, leaving one budget — the largest — silently
    # blind.
    limits = DEFAULT_LIMITS.clamped(max_edges=MAX_EDGES_CEILING)

    repository = membership_repository(NO_SESSION, limits)

    assert limits.max_edges == MAX_EDGES_CEILING
    assert repository.edge_fetch_limit == EDGE_FETCH_CEILING
    assert EDGE_FETCH_CEILING == MAX_EDGES_CEILING + 1


def test_a_request_above_the_ceiling_is_clamped_and_still_paired() -> None:
    limits = traversal_limits(
        max_depth=MAX_DEPTH_CEILING * 10,
        max_nodes=MAX_NODES_CEILING * 10,
        max_edges=MAX_EDGES_CEILING * 10,
        max_paths=MAX_PATHS_CEILING * 10,
    )

    repository = membership_repository(NO_SESSION, limits)

    assert limits == TraversalLimits(
        max_depth=MAX_DEPTH_CEILING,
        max_nodes=MAX_NODES_CEILING,
        max_edges=MAX_EDGES_CEILING,
        max_paths=MAX_PATHS_CEILING,
    )
    assert repository.edge_fetch_limit == MAX_EDGES_CEILING + 1


def test_the_default_repository_reads_no_more_than_the_ceiling_plus_the_probe() -> None:
    assert MembershipRepository(NO_SESSION).edge_fetch_limit == MAX_EDGES_CEILING


@pytest.mark.parametrize("requested", [0, -1, -MAX_EDGES_CEILING])
def test_a_nonsensical_ceiling_still_reads_at_least_one_row(requested: int) -> None:
    assert MembershipRepository(NO_SESSION, edge_fetch_limit=requested).edge_fetch_limit == 1


def test_no_caller_can_make_the_repository_read_without_bound() -> None:
    absurd = MembershipRepository(NO_SESSION, edge_fetch_limit=10**9)

    assert absurd.edge_fetch_limit == EDGE_FETCH_CEILING


def test_the_defaults_are_the_documented_ones() -> None:
    # `docs/architecture/membership-graph.md` publishes these; a silent change would make
    # the documentation wrong about what a client gets when it asks for nothing.
    assert (DEFAULT_LIMITS.max_depth, DEFAULT_LIMITS.max_nodes) == (32, 50_000)
    assert (DEFAULT_LIMITS.max_edges, DEFAULT_LIMITS.max_paths) == (200_000, 100)
    assert (MAX_DEPTH_CEILING, MAX_NODES_CEILING) == (128, 250_000)
    assert (MAX_EDGES_CEILING, MAX_PATHS_CEILING) == (1_000_000, 1_000)
