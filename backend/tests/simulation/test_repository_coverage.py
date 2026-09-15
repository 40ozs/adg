"""Every read an overlay repository can be asked for is accounted for, by name.

:class:`app.simulation.OverlayResourceRepository` and its membership counterpart are
*subclasses* of the repositories the access engine takes by injection. That is what satisfies
the engine's type contract — and it is also a hazard, because a method that is not overridden
would answer from the baseline with no overlay applied, silently returning facts from the
wrong world.

Phase 7A's as-of repositories tolerate that hazard: an inherited read there returns *today's*
facts, which is wrong but recognizably so. Here it is not tolerable, so nothing is inherited.
Every public read is in exactly one of three sets:

* **overlay-aware** — the read the simulated surface needs, with the overlay applied;
* **delegated** — the overlay provably cannot change the answer, and the reason is in the
  method's own docstring;
* **refused** — outside the simulated surface, and raising rather than answering.

This module asserts the three sets partition every public read of each base class, so a
method added to a base repository fails the suite until somebody decides which it is.
"""

from __future__ import annotations

import inspect

import pytest

from app.repositories.membership import MembershipRepository
from app.repositories.resources import ResourceRepository
from app.simulation import (
    OverlayMembershipRepository,
    OverlayResourceRepository,
    SimulationOverlay,
    SimulationUnsupportedRead,
)
from tests.support.simulation import FakeMembershipRepository, FakeResourceRepository

#: Membership reads that apply the overlay.
MEMBERSHIP_OVERLAY_AWARE = frozenset({"neighbors"})

#: Membership reads that delegate unchanged, each for a reason stated in its docstring.
#:
#: ``keys_with_members`` is the one worth reading twice. It answers *"has ADG ever enumerated
#: the inside of this group?"*, and a proposal to add one member is not an enumeration —
#: letting a simulated edge satisfy it would retire a coverage finding on the strength of a
#: hypothetical.
MEMBERSHIP_DELEGATED = frozenset({"get_principal", "principals_by_keys", "keys_with_members"})

MEMBERSHIP_REFUSED = frozenset(
    {"resolve", "aliases_for", "is_known", "direct_members", "direct_groups", "count_direct"}
)

RESOURCE_OVERLAY_AWARE = frozenset(
    {
        "full_ntfs_acl",
        "ntfs_acls_for",
        "full_share_acl",
        "share_acls_for",
        "get_ntfs_resource",
        "ntfs_resources_by_keys",
        "resources_named_by",
        "shares_named_by",
    }
)

#: Nothing in an access check reads a descriptive field of a share — the ACL is fetched
#: separately — so an overlay has nothing to change here.
RESOURCE_DELEGATED = frozenset({"get_share", "shares_by_keys"})

RESOURCE_REFUSED = frozenset(
    {
        "list_servers",
        "get_server",
        "count_servers",
        "servers_by_keys",
        "list_shares",
        "count_shares",
        "has_aces",
        "share_acl",
        "count_acl",
        "get_share_root_resource",
        "has_ntfs_aces",
        "ntfs_acl",
        "count_ntfs_acl",
        "recompute_acl_hash",
        "verify_boundary",
        "shares_referencing",
        "count_shares_referencing",
        "reference_keys_for",
    }
)

CASES = [
    (
        MembershipRepository,
        OverlayMembershipRepository,
        MEMBERSHIP_OVERLAY_AWARE,
        MEMBERSHIP_DELEGATED,
        MEMBERSHIP_REFUSED,
    ),
    (
        ResourceRepository,
        OverlayResourceRepository,
        RESOURCE_OVERLAY_AWARE,
        RESOURCE_DELEGATED,
        RESOURCE_REFUSED,
    ),
]
IDS = ["membership", "resources"]


def public_reads(cls: type) -> set[str]:
    """Every public coroutine method a repository exposes."""
    return {
        name
        for name, member in inspect.getmembers(cls, inspect.iscoroutinefunction)
        if not name.startswith("_")
    }


@pytest.mark.parametrize(("base", "overlay", "aware", "delegated", "refused"), CASES, ids=IDS)
def test_the_three_sets_partition_every_public_read(base, overlay, aware, delegated, refused):
    named = aware | delegated | refused
    reads = public_reads(base)

    assert not reads - named, (
        f"{base.__name__} gained public read(s) {sorted(reads - named)}. Decide whether "
        f"{overlay.__name__} applies the overlay to them, may delegate them unchanged, or "
        "must refuse them, and add the name to the matching set in this module. A read that "
        "is none of the three is inherited, and an inherited read on an overlay repository "
        "answers from the wrong world without saying so."
    )
    assert not named - reads, sorted(named - reads)
    assert len(aware) + len(delegated) + len(refused) == len(named), "the sets overlap"


@pytest.mark.parametrize(("base", "overlay", "aware", "delegated", "refused"), CASES, ids=IDS)
def test_nothing_is_inherited(base, overlay, aware, delegated, refused):
    """Including the delegated ones: they delegate to the *baseline*, not to ``super()``.

    An inherited method would run against the overlay repository's own session and read live
    rows even when the baseline is a point-in-time repository — a simulation over history
    that quietly mixed in today's facts.
    """
    for name in aware | delegated | refused:
        assert getattr(overlay, name) is not getattr(base, name), f"{overlay.__name__}.{name}"


def build(
    overlay: SimulationOverlay | None = None,
) -> tuple[OverlayResourceRepository, OverlayMembershipRepository]:
    proposal = overlay or SimulationOverlay()
    return (
        OverlayResourceRepository(FakeResourceRepository(), proposal),
        OverlayMembershipRepository(FakeMembershipRepository(), proposal),
    )


async def call_refused(
    repository: OverlayResourceRepository | OverlayMembershipRepository, name: str
) -> None:
    """Call a refused read with whatever arity it declares.

    The refusals keep each base method's own signature so that mypy still checks the
    override; ``count_servers`` happens to take none. Passing one argument to every method
    would make this test about argument counts rather than about the refusal.
    """
    method = getattr(repository, name)
    parameters = [
        parameter
        for parameter in inspect.signature(method).parameters.values()
        if parameter.kind
        in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD, parameter.VAR_POSITIONAL)
    ]
    await method(*["anything"] * min(len(parameters), 1))


@pytest.mark.parametrize("name", sorted(RESOURCE_REFUSED))
async def test_every_refused_resource_read_raises(name):
    resources, _ = build()

    with pytest.raises(SimulationUnsupportedRead, match=name):
        await call_refused(resources, name)


@pytest.mark.parametrize("name", sorted(MEMBERSHIP_REFUSED))
async def test_every_refused_membership_read_raises(name):
    _, membership = build()

    with pytest.raises(SimulationUnsupportedRead, match=name):
        await call_refused(membership, name)


def test_the_overlay_repositories_expose_no_write_methods():
    """The isolation guarantee, asserted against the class rather than against discipline.

    Nothing named like a write exists on either class or on the repositories they subclass.
    A simulation reaches the database only through reads.
    """
    forbidden = ("save", "write", "insert", "update", "delete", "upsert", "commit", "flush")
    for cls in (OverlayResourceRepository, OverlayMembershipRepository):
        for name in dir(cls):
            if name.startswith("_"):
                continue
            assert not any(name.startswith(word) for word in forbidden), f"{cls.__name__}.{name}"


def test_both_repositories_read_the_same_proposal():
    """A pair whose two halves held different overlays would answer about a world nobody
    proposed; the factory is what makes that unconstructible."""
    from app.simulation import simulated_repositories

    overlay = SimulationOverlay()
    resources, membership = simulated_repositories(
        FakeResourceRepository(), FakeMembershipRepository(), overlay
    )

    assert resources.overlay is membership.overlay is overlay
