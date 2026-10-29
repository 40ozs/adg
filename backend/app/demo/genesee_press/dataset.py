r"""Genesee Press, presented through Demo Mode's synthetic-dataset contract.

An adapter and nothing more: every value here is read from the modules that own it and is
carried to :mod:`app.demo_mode` unchanged.

=========================  ============================================================
Contract member            Source
=========================  ============================================================
``metadata``               :mod:`.canon` (company, domain, seed, snapshot) and the
                           committed manifest's statistics (:func:`.manifest.build_manifest`)
``generations()``          :func:`.generations.build_generations` -- the three scans,
                           300 runs, as generated; each risk pass dated by the generation
``baseline_generations``   all three: the canon is what ADG had collected by the snapshot
``risk_configuration()``   :data:`.risk.DEMO_RISK_CONFIGURATION`
``scenarios()``            :func:`.scenarios.golden_scenarios` (A-H)
``investigations()``       :func:`.scenarios.investigations` (the Apollo access question)
``comparisons()``          the first scan against the latest, as a recovery drill
                           (ADG-DEMO-03); instants are the generations' own risk-pass times
``probes()``               the objects scenarios A-H and the scan history are about, named
                           by canon identifiers and checked in current state on every reset
=========================  ============================================================

If this module ever needs to *construct* an object rather than point at one, the thing to
change is the canon, not this file.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cached_property
from typing import Any, Final

from app.demo.genesee_press import canon as gp
from app.demo.genesee_press.canon import Canon, build_canon
from app.demo.genesee_press.generations import GENERATIONS, build_generations
from app.demo.genesee_press.risk import DEMO_RISK_CONFIGURATION
from app.demo.genesee_press.scenarios import golden_scenarios, investigations
from app.demo_mode.datasets import (
    CANONICAL_DATASET,
    CanonicalProbe,
    DemoComparison,
    DemoDatasetMetadata,
    DemoInvestigation,
    DemoScenario,
    ScanGeneration,
    ScenarioSubject,
)
from app.risk_engine.configuration import RiskConfiguration

__all__ = ["GeneseePressDataset", "synthetic_dataset"]

FINANCE: Final = r"\\GP-FIN-FS01\Finance"
DESIGN: Final = r"\\GP-CORP-FS02\Design"
ENGINEERING: Final = r"\\GP-ENG-FS01\Engineering"
PEOPLE: Final = r"\\GP-HR-FS01\People"
ARCHIVE: Final = r"\\GP-ARCH-FS01\Archive"
REVENUE: Final = FINANCE + r"\Reports\Revenue"
PAYMENTS_LEGACY: Final = ENGINEERING + r"\Runbooks\Payments-Legacy"
ZEPHYR: Final = ENGINEERING + r"\Projects\Zephyr"
#: Deleted on 2026-09-25; the 2026-10-01 scan of its server was partial, so it stays current.
WAVE_3_STAGING: Final = r"\\GP-MIG-FS01\Migrations\Wave-3-Staging"
#: On GP-FS-03, whose 2026-10-01 NTFS scan failed: everything it held stays current.
COLLABORATION: Final = r"\\GP-FS-03\Collaboration"

#: The six nested groups of scenario G, innermost first.
DEEP_CHAIN: Final = (
    "ENG-Payments-Oncall",
    "ENG-Payments",
    "Engineering-Backend",
    "Engineering",
    "Engineering-FS",
    "Share_Engineering_Modify",
)


class GeneseePressDataset:
    """The canonical Genesee Press estate as a :class:`~app.demo_mode.datasets.SyntheticDataset`."""

    #: The reset installs every generation: the canon is the 2026-10-01 snapshot, and the
    #: history that led to it is part of what the demo shows.
    baseline_generations: Final = len(GENERATIONS)

    @cached_property
    def metadata(self) -> DemoDatasetMetadata:
        return DemoDatasetMetadata(
            key=CANONICAL_DATASET,
            company_name=gp.COMPANY,
            domain=gp.DOMAIN_DNS,
            snapshot_date=gp.SNAPSHOT,
            seed=gp.DATASET_KEY,
            description=(
                f"{gp.COMPANY}: a fictional company founded {gp.FOUNDED.year}, ten years of "
                "directory and file-server history, scanned three times by ADG in September "
                "and October 2026. Its people, accounts, groups and servers come from the "
                "shared synthetic world (40ozs/synthetic-enterprise); its file estate is ADG's own."
            ),
            facts=_facts(),
        )

    def generations(self) -> Sequence[ScanGeneration]:
        return self._generations

    @cached_property
    def _generations(self) -> tuple[ScanGeneration, ...]:
        return tuple(
            ScanGeneration(
                name=generation.key,
                description=f"{generation.day.isoformat()}: {generation.title}",
                transcripts=transcripts,
                evaluated_at=generation.evaluated_at,
            )
            for generation, transcripts in build_generations()
        )

    def risk_configuration(self) -> RiskConfiguration:
        return DEMO_RISK_CONFIGURATION

    def scenarios(self) -> Sequence[DemoScenario]:
        return self._scenarios

    @cached_property
    def _scenarios(self) -> tuple[DemoScenario, ...]:
        canon = self._canon
        return tuple(_scenario(item, canon) for item in golden_scenarios(canon))

    def investigations(self) -> Sequence[DemoInvestigation]:
        return self._investigations

    @cached_property
    def _investigations(self) -> tuple[DemoInvestigation, ...]:
        return tuple(_investigation(item) for item in investigations(self._canon))

    def comparisons(self) -> Sequence[DemoComparison]:
        """The recovery drill: the whole estate, first scan against latest (ADG-DEMO-03).

        Both instants are the dataset's own (each generation's risk-pass time, after every
        run of it has closed), so the comparison is a pure function of the canon. Estate
        wide it reads more objects per instant than one comparison does, so it can never
        be complete in one request; the drill also launches it inside a 1 ms window, which
        no host reads two sides of thousands of versions within, so it stops at its first
        checkpoint every time. What the reviewer is shown is the real comparison doing the
        real thing.
        """
        first, latest = GENERATIONS[0], GENERATIONS[-1]
        return (
            DemoComparison(
                key="first-to-latest",
                title="What is different between the first scan and the latest?",
                question=(
                    f"What is different across the estate between {first.day.isoformat()} "
                    f"and {latest.day.isoformat()}?"
                ),
                at_from=first.evaluated_at,
                at_to=latest.evaluated_at,
                drill_window_ms=1,
                summary=(
                    "The estate-wide comparison of ADG's first and latest scans, launched "
                    "inside a deliberately short interactive window. ADG stops, says so, "
                    "presents no partial list as an answer, and offers the comparisons that "
                    "can finish: the same question inside the standard window, or one kind "
                    "of object at a time."
                ),
            ),
        )

    def probes(self) -> Sequence[CanonicalProbe]:
        return self._probes

    @cached_property
    def _probes(self) -> tuple[CanonicalProbe, ...]:
        canon = self._canon

        def group(name: str) -> str:
            return canon.group(name).sid

        controller = canon.one("golden:controller")
        mover = canon.one("golden:cross-department")
        deep = canon.one("golden:deep-chain")
        chain = [deep.sid, *(group(name) for name in DEEP_CHAIN)]
        return (
            CanonicalProbe("principal", "an active Finance employee", {"sid": controller.sid}),
            # A -- modern healthy share.
            CanonicalProbe("ntfs_resource", "A: the Design share root", {"path": DESIGN}),
            CanonicalProbe(
                "ntfs_ace",
                "A: sec-share-design-write on the Design root",
                {"path": DESIGN, "trustee_sid": group("sec-share-design-write")},
            ),
            # B -- legacy finance hierarchy.
            CanonicalProbe(
                "share", "B: the Finance share", {"server": "GP-FIN-FS01", "share": "Finance"}
            ),
            CanonicalProbe(
                "membership",
                "B: Finance-RW nested in GG_Finance_RW",
                {"group_sid": group("GG_Finance_RW"), "member_sid": group("Finance-RW")},
            ),
            CanonicalProbe(
                "ntfs_ace",
                "B: GG_Finance_RW on the Finance root",
                {"path": FINANCE, "trustee_sid": group("GG_Finance_RW")},
            ),
            # C -- engineering sprawl.
            CanonicalProbe(
                "ntfs_ace",
                "C: Engineering-FS on the Engineering root",
                {"path": ENGINEERING, "trustee_sid": group("Engineering-FS")},
            ),
            # D -- the People share.
            CanonicalProbe(
                "share", "D: the People share", {"server": "GP-HR-FS01", "share": "People"}
            ),
            CanonicalProbe(
                "ntfs_ace",
                "D: sec-share-people-write on the People root",
                {"path": PEOPLE, "trustee_sid": group("sec-share-people-write")},
            ),
            # E -- archive and migration debt.
            CanonicalProbe("ntfs_resource", "E: the Archive share root", {"path": ARCHIVE}),
            CanonicalProbe(
                "ntfs_ace",
                "E: deleted Project-Vega still named on Archive\\Projects-2019",
                {"path": ARCHIVE + r"\Projects-2019", "trustee_sid": group("Project-Vega")},
            ),
            # F -- cross-department access, two ways.
            CanonicalProbe(
                "membership",
                "F: Sales-Ops nested in Revenue-Reporting",
                {"group_sid": group("Revenue-Reporting"), "member_sid": group("Sales-Ops")},
            ),
            CanonicalProbe(
                "ntfs_ace",
                "F: the transferred employee's direct entry on Finance\\Reports\\Revenue",
                {"path": REVENUE, "trustee_sid": mover.sid},
            ),
            # G -- the six-group chain, link by link.
            *(
                CanonicalProbe(
                    "membership",
                    f"G: chain link {index} of {len(DEEP_CHAIN)}",
                    {"group_sid": chain[index], "member_sid": chain[index - 1]},
                )
                for index in range(1, len(chain))
            ),
            CanonicalProbe(
                "ntfs_ace",
                "G: Share_Engineering_Modify on Runbooks\\Payments-Legacy",
                {"path": PAYMENTS_LEGACY, "trustee_sid": group("Share_Engineering_Modify")},
            ),
            # H -- the empty group still granted Modify.
            CanonicalProbe(
                "ntfs_ace",
                "H: Proj-Zephyr-RW on Engineering\\Projects\\Zephyr",
                {"path": ZEPHYR, "trustee_sid": group("Proj-Zephyr-RW")},
            ),
            # Current-state semantics the scan history promises.
            CanonicalProbe(
                "ntfs_resource",
                "history: Wave-3-Staging survives the partial 2026-10-01 scan of GP-MIG-FS01",
                {"path": WAVE_3_STAGING},
            ),
            CanonicalProbe(
                "ntfs_resource",
                "history: GP-FS-03's folders survive its failed 2026-10-01 scan",
                {"path": COLLABORATION},
            ),
        )

    @cached_property
    def _canon(self) -> Canon:
        return build_canon()


def _facts() -> dict[str, str]:
    """Headline counts, read from the manifest the committed JSON is checked against."""
    from app.demo.genesee_press.manifest import build_manifest

    statistics: dict[str, Any] = build_manifest()["statistics"]
    employees, groups, estate = (
        statistics["employees"],
        statistics["groups"],
        statistics["estate"],
    )
    runs = sum(int(item["runs"]) for item in statistics["generations"].values())
    observations = sum(int(item["observations"]) for item in statistics["generations"].values())
    return {
        "Founded": gp.FOUNDED.isoformat(),
        "Active employees": f"{employees['active']:,}",
        "Former employees": f"{employees['former']:,}",
        "Departments": f"{len(employees['by_department']):,}",
        "Groups": f"{groups['total']:,}",
        "File servers": f"{estate['servers']:,}",
        "Shares": f"{estate['shares']:,}",
        "Folders": f"{estate['directories']:,}",
        "NTFS entries": f"{estate['ntfs_aces']:,}",
        "Scan generations": f"{len(statistics['generations']):,}",
        "Scan runs": f"{runs:,}",
        "Observations": f"{observations:,}",
    }


def _scenario(item: dict[str, Any], canon: Canon) -> DemoScenario:
    subjects: list[ScenarioSubject] = [
        ScenarioSubject("person", person["name"], person["sid"])
        for person in item.get("people", [])
    ]
    for name in (*item.get("groups", ()), *item.get("chain", ())):
        sid = canon.group(name).sid
        if all(subject.sid != sid for subject in subjects):
            subjects.append(ScenarioSubject("group", name, sid))
    return DemoScenario(
        key=item["id"],
        title=item["title"],
        summary=item["demonstrates"],
        resource=item["resource"],
        subjects=tuple(subjects),
        related_resources=tuple(item.get("also", ())),
    )


def _investigation(item: dict[str, Any]) -> DemoInvestigation:
    person = item["person"]
    return DemoInvestigation(
        key=item["id"],
        title=item["title"],
        question=item["question"],
        principal=ScenarioSubject("person", person["name"], person["sid"]),
        resource=item["resource"],
        access_path=item["access_path"],
        summary=item["demonstrates"],
        scenarios=tuple(item["scenarios"]),
    )


def synthetic_dataset() -> GeneseePressDataset:
    """The registry's loader for ``genesee-press-v1``."""
    return GeneseePressDataset()
