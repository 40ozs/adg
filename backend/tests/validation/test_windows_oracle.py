"""The effective-access engine, checked against Windows' own access check.

Every other test of `app.access_engine` asserts what this repository believes Windows does.
These assert what Windows **did**, on a real host, for every case in the combinatorial
matrix — captured by `scripts/windows-access-check/Invoke-AdgAccessOracle.ps1` and committed
as a fixture so the comparison runs everywhere rather than only where a Windows host happens
to be attached.

Two instruments produced the fixtures, and the difference between them matters:

* `ntfs-access-oracle.json` — `AuthzAccessCheck` asked for `MAXIMUM_ALLOWED` over a synthetic
  descriptor. Authz is the API behind the Windows "Effective Access" tab, so this is both the
  authoritative access check and the number an administrator sees in the UI. It reaches DACL
  shapes no API will write to disk, which is what makes a matrix of this size possible.
* `real-directory-access.json` — real directories, real descriptors, opened by a real process
  with a real token. Narrow, and it settles the one question the synthetic descriptor cannot:
  a bare descriptor carries no object type, so Authz cannot apply the file system's
  valid-rights mask and answers `0x001FFFFF` on a NULL DACL where a real directory grants
  `FILE_ALL_ACCESS`.

Regenerating either fixture is a deliberate act. The oracle stores the fingerprint of the
inputs each answer was given, and a matrix edited without re-running the probe fails here
rather than being compared against an answer to a different question.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest

from app.access_engine import (
    FILE_ALL_ACCESS,
    FILE_GENERIC_EXECUTE,
    FILE_GENERIC_READ,
    FILE_GENERIC_WRITE,
    FILE_SYSTEM_GENERIC_MAPPING,
    OWNER_IMPLICIT_RIGHTS,
    RightsLayer,
    evaluate_acl,
)
from app.domain import NtfsRight
from tests.access_engine.matrix import AclCase, acl_cases

WINDOWS_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "windows"
ORACLE_PATH = WINDOWS_DIR / "ntfs-access-oracle.json"
REAL_ACCESS_PATH = WINDOWS_DIR / "real-directory-access.json"

REGENERATE = (
    "Re-run scripts/windows-access-check/Invoke-AdgAccessOracle.ps1 on a Windows host "
    "to bring the committed answers back into step with the matrix."
)

NULL_DACL_SYNTHETIC_CEILING = 0x001FFFFF
"""What Authz grants against a NULL DACL in a descriptor with no object type.

Every standard and specific right bit, because nothing told Authz which rights this object
defines. A real directory answers `FILE_ALL_ACCESS`; see
:class:`TestTheNullDaclDivergence`, which holds both numbers and the reason.
"""


def _load(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():  # pragma: no cover - the fixtures are committed
        raise AssertionError(f"Missing Windows fixture {path.name}. {REGENERATE}")
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8-sig"))
    return document


@pytest.fixture(scope="module")
def oracle() -> dict[str, Any]:
    return _load(ORACLE_PATH)


@pytest.fixture(scope="module")
def answers(oracle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {answer["case_id"]: answer for answer in oracle["answers"]}


@pytest.fixture(scope="module")
def real_access() -> dict[str, Any]:
    return _load(REAL_ACCESS_PATH)


@pytest.fixture(scope="module")
def measured_cases(real_access: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {case["case"]: case for case in real_access["cases"]}


def engine_mask(case: AclCase) -> int:
    """What `evaluate_acl` grants for this case, as a bare integer."""
    resource = case.resource()
    result = evaluate_acl(
        resource.entries,
        case.subject_token(),
        layer=RightsLayer.NTFS,
        facts=resource.facts,
    )
    return result.rights.value


def _groups() -> list[str]:
    """The matrix split into named groups, so a failure names a region of the case space."""
    seen: dict[str, None] = {}
    for case in acl_cases():
        seen.setdefault(_group_of(case), None)
    return list(seen)


def _group_of(case: AclCase) -> str:
    parts = case.case_id.split("/")
    return f"{parts[1]}/{parts[2]}" if parts[1] in ("degenerate", "owner-rights") else parts[1]


def _cases_in(group: str) -> Iterator[AclCase]:
    for case in acl_cases():
        if _group_of(case) == group:
            yield case


class TestTheFixtureMatchesTheMatrix:
    """A stale oracle is worse than no oracle: it reports a pass for a question nobody asked."""

    def test_every_case_has_an_answer(self, answers: dict[str, dict[str, Any]]) -> None:
        missing = [case.case_id for case in acl_cases() if case.case_id not in answers]
        assert not missing, (
            f"{len(missing)} matrix cases have no recorded Windows answer, first: "
            f"{missing[:3]}. {REGENERATE}"
        )

    def test_no_answer_is_orphaned(self, answers: dict[str, dict[str, Any]]) -> None:
        known = {case.case_id for case in acl_cases()}
        orphans = sorted(set(answers) - known)
        assert not orphans, (
            f"{len(orphans)} recorded answers name cases the matrix no longer generates, "
            f"first: {orphans[:3]}. {REGENERATE}"
        )

    def test_every_answer_fingerprints_its_own_inputs(
        self, answers: dict[str, dict[str, Any]]
    ) -> None:
        stale = [
            case.case_id
            for case in acl_cases()
            if answers[case.case_id]["fingerprint"] != case.fingerprint()
        ]
        assert not stale, (
            f"{len(stale)} cases have been edited since the oracle was captured, first: "
            f"{stale[:3]}. {REGENERATE}"
        )

    def test_the_capture_records_the_host_it_ran_on(self, oracle: dict[str, Any]) -> None:
        assert oracle["schema"] == "adg.access-oracle/1"
        assert oracle["host"]["os_caption"].startswith("Microsoft Windows")
        assert oracle["host"]["os_version"]


class TestTheAccessCheckAgreesWithWindows:
    """The measurement this phase exists to make."""

    @pytest.mark.parametrize("group", _groups())
    def test_the_engine_grants_what_windows_grants(
        self, group: str, answers: dict[str, dict[str, Any]]
    ) -> None:
        mismatches: list[str] = []
        compared = 0
        for case in _cases_in(group):
            answer = answers[case.case_id]
            if answer.get("error"):
                continue
            if case.case_id in KNOWN_DIVERGENCES:
                continue
            windows = int(answer["mask"], 16)
            engine = engine_mask(case)
            compared += 1
            if engine != windows:
                mismatches.append(
                    f"{case.case_id}\n"
                    f"      sddl    {case.to_sddl()}\n"
                    f"      token   {list(case.token_sids)}\n"
                    f"      engine  0x{engine:08X}\n"
                    f"      windows 0x{windows:08X}\n"
                    f"      differ  0x{engine ^ windows:08X}"
                )
        if not compared:
            # Legitimate only when every case in the group is individually accounted for:
            # unanswerable by Windows, or a divergence pinned by its own test. Any other
            # empty group is a comparison that silently checked nothing.
            unaccounted = [
                case.case_id
                for case in _cases_in(group)
                if not answers[case.case_id].get("error") and case.case_id not in KNOWN_DIVERGENCES
            ]
            assert not unaccounted, (
                f"Group {group!r} compared nothing and {len(unaccounted)} of its cases are "
                f"neither unanswerable nor pinned: {unaccounted[:3]}"
            )
        assert not mismatches, (
            f"{len(mismatches)} of {compared} cases in {group!r} disagree with Windows:\n  "
            + "\n  ".join(mismatches[:5])
        )

    def test_the_comparison_covers_the_whole_matrix(
        self, answers: dict[str, dict[str, Any]]
    ) -> None:
        """Guards the guard: a bug in the grouping could quietly compare almost nothing."""
        compared = sum(
            1
            for case in acl_cases()
            if not answers[case.case_id].get("error") and case.case_id not in KNOWN_DIVERGENCES
        )
        assert compared >= 5_600, f"Only {compared} cases were compared against Windows."


class TestWhatWindowsCouldNotBeAsked:
    """Cases the oracle records as unanswerable, and why each one is."""

    def test_only_the_unowned_descriptors_failed(self, answers: dict[str, dict[str, Any]]) -> None:
        failed = sorted(case_id for case_id, answer in answers.items() if answer.get("error"))
        assert failed == [
            "acl/degenerate/empty-dacl/open/unowned",
            "acl/degenerate/empty-dacl/protected/unowned",
            "acl/degenerate/null-dacl/unowned",
        ]

    def test_an_unowned_descriptor_is_not_a_state_a_file_can_be_in(
        self, answers: dict[str, dict[str, Any]]
    ) -> None:
        """`AccessCheck` refuses a descriptor with no owner, and every file object has one.

        ADG still models `owner_sid=None`, because a collector may fail to read the owner
        while reading the DACL. That is a gap in ADG's observations, not a state of the
        object, and it is why these three cases exist here with no Windows answer.
        """
        answer = answers["acl/degenerate/null-dacl/unowned"]
        assert answer["mask"] is None
        assert "AuthzAccessCheck" in answer["error"]


class TestTheNullDaclDivergence:
    """The one case where the engine and the synthetic oracle disagree, and why.

    This is not an unexplained failure held open with a marker: it is a measured artifact of
    the instrument, and the real-directory probe settles it in the engine's favor. It is
    pinned here so that the day Authz starts answering differently, somebody is told.
    """

    NULL_DACL_CASES = (
        "acl/degenerate/null-dacl/direct",
        "acl/degenerate/null-dacl/outsider",
    )

    @pytest.mark.parametrize("case_id", NULL_DACL_CASES)
    def test_the_synthetic_descriptor_answers_every_bit(
        self, case_id: str, answers: dict[str, dict[str, Any]]
    ) -> None:
        assert int(answers[case_id]["mask"], 16) == NULL_DACL_SYNTHETIC_CEILING

    @pytest.mark.parametrize("case_id", NULL_DACL_CASES)
    def test_the_engine_answers_file_all_access(self, case_id: str) -> None:
        case = next(item for item in acl_cases() if item.case_id == case_id)
        assert engine_mask(case) == FILE_ALL_ACCESS

    def test_a_real_directory_settles_it_for_the_engine(
        self, measured_cases: dict[str, dict[str, Any]]
    ) -> None:
        """A directory with SE_DACL_PRESENT clear grants `FILE_ALL_ACCESS`, not every bit.

        The engine's answer is the one a real object produces. The extra `0x0000FE00` in the
        synthetic answer is specific-rights bits the file system does not define, which Authz
        cannot know to withhold because nothing told it what kind of object it was looking at.
        """
        measured = measured_cases["null-dacl"]
        assert int(measured["open_granted"], 16) == FILE_ALL_ACCESS
        assert int(measured["authz_granted"], 16) == NULL_DACL_SYNTHETIC_CEILING
        difference = NULL_DACL_SYNTHETIC_CEILING ^ FILE_ALL_ACCESS
        assert difference == 0x0000FE00
        assert difference & FILE_ALL_ACCESS == 0


KNOWN_DIVERGENCES: frozenset[str] = frozenset(TestTheNullDaclDivergence.NULL_DACL_CASES)
"""Cases excluded from the bulk comparison because they are pinned individually above.

Nothing else is excluded. An exclusion added here without a test that states the divergence
and its cause is a failure being hidden, not recorded.
"""


class TestTheGenericMappingIsMeasuredNotQuoted:
    """`FILE_SYSTEM_GENERIC_MAPPING` is four constants. These are the four Windows produced."""

    @pytest.mark.parametrize(
        ("right", "key", "expected"),
        [
            (NtfsRight.GENERIC_READ, "generic_read", FILE_GENERIC_READ),
            (NtfsRight.GENERIC_WRITE, "generic_write", FILE_GENERIC_WRITE),
            (NtfsRight.GENERIC_EXECUTE, "generic_execute", FILE_GENERIC_EXECUTE),
            (NtfsRight.GENERIC_ALL, "generic_all", FILE_ALL_ACCESS),
        ],
    )
    def test_windows_maps_this_generic_right_the_way_adg_does(
        self, right: NtfsRight, key: str, expected: int, oracle: dict[str, Any]
    ) -> None:
        measured = int(oracle["generic_mapping"][key], 16)
        assert measured == expected
        assert FILE_SYSTEM_GENERIC_MAPPING[right] == measured


class TestTheRealDirectoryMeasurements:
    """What a real object, a real descriptor and a real token do.

    These hold the facts the engine's most load-bearing decisions rest on. Each was measured
    rather than reasoned about, and each is recorded with the instrument that produced it.
    """

    def test_the_owner_holds_read_control_and_write_dac_over_an_empty_dacl(
        self, measured_cases: dict[str, dict[str, Any]]
    ) -> None:
        """The implicit grant no ACE shows, which `OWNER_IMPLICIT_RIGHTS` encodes."""
        assert int(measured_cases["empty-dacl"]["authz_granted"], 16) == int(
            OWNER_IMPLICIT_RIGHTS.value
        )

    def test_an_empty_dacl_grants_nothing_to_anyone_else(
        self, measured_cases: dict[str, dict[str, Any]]
    ) -> None:
        """And it is stored as a present, empty DACL — `D:PAI` with no entries."""
        assert measured_cases["empty-dacl"]["stored_sddl"].endswith("D:PAI")

    def test_a_null_dacl_is_stored_with_no_dacl_at_all(
        self, measured_cases: dict[str, dict[str, Any]]
    ) -> None:
        """The state an ACL viewer cannot distinguish from Everyone/Full Control.

        There is no `D:` in the stored SDDL. That is the whole difference from the case
        above, and it inverts the answer from "nobody" to "everybody".
        """
        assert "D:" not in measured_cases["null-dacl"]["stored_sddl"]

    def test_an_unresolvable_trustee_grants_the_subject_nothing(
        self, measured_cases: dict[str, dict[str, Any]]
    ) -> None:
        """The orphaned SID stays on the ACL and reaches nobody Windows can name."""
        measured = measured_cases["unresolvable-trustee-only"]
        assert int(measured["authz_granted"], 16) == int(OWNER_IMPLICIT_RIGHTS.value)

    def test_the_open_and_the_access_check_are_different_questions(
        self, measured_cases: dict[str, dict[str, Any]]
    ) -> None:
        """A measured limit, and the reason the access check is the instrument ADG models.

        On a DACL that grants the token nothing, the access check reports the owner's
        `READ_CONTROL`/`WRITE_DAC` and a `CreateFile` open is refused outright. Both are
        true: the owner really can read the descriptor — `Get-Acl` on this very directory
        succeeds, which is how the fixture recorded its SDDL — and yet cannot obtain a
        handle. ADG reports the access check, because that is what the Windows UI reports and
        because for an audit tool the safe error is the one that over-states reach.
        """
        measured = measured_cases["empty-dacl"]
        assert int(measured["authz_granted"], 16) == int(OWNER_IMPLICIT_RIGHTS.value)
        assert int(measured["open_granted"], 16) == 0
        assert measured["stored_sddl"], "Get-Acl read the descriptor the open was refused for"

    def test_the_measurement_records_its_subject_and_method(
        self, real_access: dict[str, Any]
    ) -> None:
        assert real_access["schema"] == "adg.real-access/1"
        assert real_access["subject"]["user_sid"].startswith("S-1-5-21-")
        assert "AuthzAccessCheck" in real_access["method"]["authz_granted"]
        assert "NtQueryObject" in real_access["method"]["open_granted"]
