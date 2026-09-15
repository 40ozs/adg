"""The remediator interface: what every deployment gets, and what a lab gets instead.

The most important assertion in this file is the dullest one — the default factory returns
something that refuses. Everything else is about making sure the refusal cannot be configured
away, and that the one adapter that *does* apply anything cannot be pointed at anything real.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.domain.remediation import ExecutionMode, PlannedChangeKind
from app.remediation.errors import RemediationDisabled, RemediationValidationError
from app.remediation.executor import (
    DisabledRemediator,
    LabRemediator,
    Remediator,
    remediator_for,
)
from tests.remediation import factories as f


class TestTheExecutorEveryDeploymentGets:
    def test_the_factory_defaults_to_refusing(self) -> None:
        assert isinstance(
            remediator_for(ExecutionMode.DISABLED, environment="development"),
            DisabledRemediator,
        )

    async def test_applying_anything_raises(self) -> None:
        with pytest.raises(RemediationDisabled) as raised:
            await DisabledRemediator().apply(f.plan(), f.change())

        assert "ADG does not change Windows" in str(raised.value)

    async def test_the_refusal_says_what_would_have_to_change(self) -> None:
        """Somebody who reaches this by accident should learn what the product is, rather
        than file a bug about a missing feature."""
        with pytest.raises(RemediationDisabled) as raised:
            await DisabledRemediator().apply(f.plan(), f.change())

        message = str(raised.value)
        assert "adapter implementation" in message
        assert "remediation:execute" in message

    def test_it_describes_itself_as_unable(self) -> None:
        description = DisabledRemediator().describe()

        assert description.mode is ExecutionMode.DISABLED
        assert description.can_execute is False
        assert description.adapter == "none"

    def test_the_description_lists_every_prerequisite(self) -> None:
        """This is the response an operator reads when looking for a way to turn it on, so
        the list lives in the code rather than only in the architecture document."""
        requirements = DisabledRemediator().describe().requirements

        assert len(requirements) >= 4
        joined = " ".join(requirements)
        assert "Remediator" in joined
        assert "credential" in joined
        assert "remediation:execute" in joined

    def test_it_satisfies_the_protocol(self) -> None:
        assert isinstance(DisabledRemediator(), Remediator)


class TestLabModeCannotReachAnythingReal:
    def test_it_is_refused_in_production(self) -> None:
        with pytest.raises(RemediationValidationError) as raised:
            remediator_for(ExecutionMode.LAB, environment="production", fixture_path="fixture.json")

        assert "refused in production" in str(raised.value)

    def test_it_is_refused_without_a_fixture(self) -> None:
        """Refused rather than downgraded: a test that asked for a lab adapter and silently
        got a refusing one would pass for the wrong reason."""
        with pytest.raises(RemediationValidationError) as raised:
            remediator_for(ExecutionMode.LAB, environment="development", fixture_path="")

        assert "ADG_REMEDIATION_LAB_FIXTURE_PATH" in str(raised.value)

    def test_a_missing_fixture_file_is_refused_rather_than_invented(
        self, tmp_path: pathlib.Path
    ) -> None:
        with pytest.raises(RemediationValidationError) as raised:
            remediator_for(
                ExecutionMode.LAB,
                environment="development",
                fixture_path=str(tmp_path / "nothing.json"),
            )

        assert "could not be read" in str(raised.value)

    def test_a_malformed_fixture_is_refused(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(RemediationValidationError) as raised:
            remediator_for(ExecutionMode.LAB, environment="development", fixture_path=str(path))

        assert "not valid JSON" in str(raised.value)

    def test_a_fixture_from_disk_is_loaded(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "estate.json"
        path.write_text(
            json.dumps(
                {
                    "entries": {"ace-1": {"access_mask": f.WRITE_MASK}},
                    "edges": [[f.FINANCE_RW, f.ALICE]],
                }
            ),
            encoding="utf-8",
        )

        remediator = remediator_for(
            ExecutionMode.LAB, environment="development", fixture_path=str(path)
        )

        assert isinstance(remediator, LabRemediator)
        assert remediator.describe().can_execute is True


class TestWhatTheLabAdapterActuallyDoes:
    def _remediator(self) -> LabRemediator:
        return LabRemediator.from_fixture(
            {
                "entries": {"ace-1": {"access_mask": f.WRITE_MASK}},
                "edges": [[f.FINANCE_RW, f.ALICE]],
            }
        )

    async def test_a_removal_takes_the_entry_out_of_the_fixture(self) -> None:
        remediator = self._remediator()

        await remediator.apply(f.plan(), f.change(PlannedChangeKind.REMOVE_NTFS_ACE))

        assert "ace-1" not in remediator.entries

    async def test_a_narrowing_rewrites_the_mask_in_the_fixture(self) -> None:
        remediator = self._remediator()

        await remediator.apply(
            f.plan(),
            f.change(
                PlannedChangeKind.MODIFY_NTFS_ACE,
                entry=f.entry(access_mask=f.FULL_MASK),
                after_access_mask=f.READ_MASK,
            ),
        )

        assert remediator.entries["ace-1"]["access_mask"] == f.READ_MASK

    async def test_a_membership_removal_drops_the_edge(self) -> None:
        remediator = self._remediator()

        await remediator.apply(f.plan(), f.change(PlannedChangeKind.REMOVE_GROUP_MEMBER))

        assert (f.FINANCE_RW, f.ALICE) not in remediator.edges

    async def test_a_target_the_fixture_does_not_hold_is_refused(self) -> None:
        """The refusal matters more than the mutation: an adapter that invented its targets
        would report every plan as done, including one whose objects had been renamed out
        from under it."""
        remediator = self._remediator()

        with pytest.raises(RemediationValidationError) as raised:
            await remediator.apply(f.plan(), f.change(entry=f.entry(ace_key="not-there")))

        assert "invented its targets" in str(raised.value)

    async def test_it_records_what_it_applied(self) -> None:
        remediator = self._remediator()

        outcome = await remediator.apply(f.plan(), f.change())

        assert outcome.applied
        assert remediator.applied == [outcome]

    def test_the_fixture_is_copied_rather_than_shared(self) -> None:
        """A caller holding the document must not be able to watch it change, and a test must
        not be able to leak state into the next one."""
        document = {"entries": {"ace-1": {"access_mask": f.WRITE_MASK}}, "edges": []}
        remediator = LabRemediator.from_fixture(document)

        remediator.entries["ace-1"]["access_mask"] = 0

        assert document["entries"]["ace-1"]["access_mask"] == f.WRITE_MASK  # type: ignore[index]

    def test_a_fixture_of_the_wrong_shape_is_refused(self) -> None:
        with pytest.raises(RemediationValidationError):
            LabRemediator.from_fixture({"entries": [], "edges": []})
