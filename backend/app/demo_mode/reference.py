r"""The reference estate in :mod:`app.demo`, presented through the demo dataset contract.

An adapter and nothing more. Every principal, SID, path, timestamp and run id comes from
:func:`app.demo.estate.build_estate` and :func:`app.demo.transcripts.build_transcripts`
unchanged; the probes below name objects by the estate's own exported constants, so a
renumbering there moves them here too. If this module ever needs to *construct* an object
rather than point at one, the thing to change is the estate, not this file.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property

from app.demo import estate as reference
from app.demo.transcripts import build_transcripts
from app.demo_mode.datasets import (
    REFERENCE_DATASET,
    CanonicalProbe,
    DemoDatasetMetadata,
    ScanGeneration,
)

__all__ = ["ReferenceEstateDataset", "synthetic_dataset"]

#: The profile Demo Mode installs. The feature set is the same in every profile; this is the
#: one the runbook and the end-to-end walkthrough describe.
PROFILE = "standard"


@dataclass(frozen=True)
class ReferenceEstateDataset:
    """The :mod:`app.demo` estate as a :class:`~app.demo_mode.datasets.SyntheticDataset`."""

    profile: str = PROFILE

    @property
    def metadata(self) -> DemoDatasetMetadata:
        built = self._estate
        return DemoDatasetMetadata(
            key=REFERENCE_DATASET,
            company_name="ADG reference estate",
            domain=reference.DOMAIN_DNS,
            snapshot_date=reference.EPOCH.date(),
            # The estate has no random seed: it is constructed, not sampled. The profile is
            # the only input, so it is what identifies one build of it.
            seed=f"profile:{self.profile}",
            description=(
                "A small, deliberately awkward AD + SMB + NTFS estate: nested groups, a cycle, "
                "a winning deny, broken inheritance, an orphaned SID, a partial scan and a "
                "failed one."
            ),
            facts={
                "principals": str(len(built.principals)),
                "shares": str(len(built.shares)),
                "directories": str(len(built.directories)),
            },
        )

    def generations(self) -> Sequence[ScanGeneration]:
        return self._generations

    @cached_property
    def _generations(self) -> tuple[ScanGeneration, ...]:
        # One generation: the estate as its collectors reported it, partial and failed runs
        # included. The estate defines no later scan, so Demo Mode offers none. Built once per
        # instance: the demo overview asks for the generations on every request.
        return (
            ScanGeneration(
                name="baseline",
                description="Every collector's first run against the reference estate.",
                transcripts=build_transcripts(self._estate),
            ),
        )

    def probes(self) -> Sequence[CanonicalProbe]:
        sid = reference.sid
        finance = r"\\FS01\Finance"
        return (
            CanonicalProbe("principal", "Alice, a user", {"sid": sid(reference.ALICE)}),
            CanonicalProbe("principal", "Finance-RW, a group", {"sid": sid(reference.FINANCE_RW)}),
            CanonicalProbe(
                "membership",
                "Alice is a direct member of Finance-Team",
                {"group_sid": sid(reference.FINANCE_TEAM), "member_sid": sid(reference.ALICE)},
            ),
            CanonicalProbe(
                "membership",
                "Finance-Team is nested in Finance-RW",
                {
                    "group_sid": sid(reference.FINANCE_RW),
                    "member_sid": sid(reference.FINANCE_TEAM),
                },
            ),
            CanonicalProbe("share", "the Finance share", {"server": "FS01", "share": "Finance"}),
            CanonicalProbe(
                "share_ace",
                "Everyone on the Finance share ACL",
                {"server": "FS01", "share": "Finance", "trustee_sid": reference.EVERYONE},
            ),
            CanonicalProbe("ntfs_resource", "the Finance share root", {"path": finance}),
            CanonicalProbe(
                "ntfs_ace",
                "Finance-RW granted modify on the Finance root",
                {"path": finance, "trustee_sid": sid(reference.FINANCE_RW)},
            ),
        )

    @cached_property
    def _estate(self) -> reference.DemoEstate:
        return reference.build_estate(self.profile)


def synthetic_dataset() -> ReferenceEstateDataset:
    """The registry's loader for :data:`~app.demo_mode.datasets.REFERENCE_DATASET`."""
    return ReferenceEstateDataset()
