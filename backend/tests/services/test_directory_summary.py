r"""The directory-access summary: roots, runs, exceptions, and the tree, rule by rule.

Every verdict here is handed in, the way the access engine hands them to the summary in
production, so each test states exactly which directories the engine found reachable and
with what rights — and asserts only how the summary groups them. The grouping rules are the
claims under test:

* a continuous reachable subtree is one root, and its descendant count is exact;
* shares and separate subtrees stay separate;
* a change of effective rights, of certainty, or a broken inheritance is an exception, not
  silently absorbed — and a root carrying one is not reported as uniform;
* an ACL boundary that does not change this principal's answer is not an exception;
* where access stops (no grant, a Deny, an indeterminate answer, a folder nobody read) is
  an exception and is never counted as reachable;
* access that resumes below an inaccessible folder is a root of its own, linked to the run
  it resumes inside.
"""

from __future__ import annotations

import random
import time

from app.access_engine import (
    AccessCertainty,
    AccessOutcome,
    LimitingLayer,
    RightsLayer,
    RightsMask,
)
from app.services.directory_access import facts_of
from app.services.directory_summary import (
    DirectoryFacts,
    DirectoryVerdict,
    ExceptionReason,
    RootParent,
    rights_change,
    summarize_directory_access,
)

FULL = 0x001F01FF
MODIFY = 0x001301BF
READ_EXECUTE = 0x001200A9


def mask(value: int) -> RightsMask:
    return RightsMask(value, RightsLayer.EFFECTIVE)


def granted(
    value: int = MODIFY, certainty: AccessCertainty = AccessCertainty.CERTAIN
) -> DirectoryVerdict:
    return DirectoryVerdict(
        rights=mask(value),
        certainty=certainty,
        outcome=AccessOutcome.GRANTED,
        limiting_layer=LimitingLayer.NONE,
    )


def refused(outcome: AccessOutcome = AccessOutcome.NO_GRANT) -> DirectoryVerdict:
    return DirectoryVerdict(
        rights=mask(0),
        certainty=AccessCertainty.CERTAIN,
        outcome=outcome,
        limiting_layer=LimitingLayer.NONE,
    )


def directory(path: str, *, protected: bool = False, boundary: str | None = None) -> DirectoryFacts:
    key = path.rstrip("\\").casefold()
    parts = key.split("\\")
    return facts_of(
        key,
        path,
        f"{parts[2]}|{parts[3]}",
        is_acl_boundary=protected or boundary is not None,
        boundary_reason="protected_dacl" if protected else boundary,
        dacl_protected=protected,
    )


def key(path: str) -> str:
    return path.rstrip("\\").casefold()


def tree(*paths: str) -> list[DirectoryFacts]:
    return [directory(path) for path in paths]


FINANCE = [
    r"\\FS01\Finance",
    r"\\FS01\Finance\Budgets",
    r"\\FS01\Finance\Budgets\FY2025",
    r"\\FS01\Finance\Budgets\FY2026",
    r"\\FS01\Finance\Budgets\FY2026\Q1",
    r"\\FS01\Finance\Budgets\FY2026\Q2",
    r"\\FS01\Finance\Payroll",
    r"\\FS01\Finance\Payroll\Exports",
]


# ---------------------------------------------------------------- root summarization


class TestRootSummarization:
    def test_a_continuous_reachable_subtree_is_one_root(self) -> None:
        verdicts = {key(path): granted() for path in FINANCE[1:6]}  # Budgets and below
        summary = summarize_directory_access(tree(*FINANCE), verdicts)

        assert [root.key for root in summary.roots] == [key(r"\\FS01\Finance\Budgets")]
        root = summary.roots[0]
        assert root.reachable_descendants == 4
        assert root.exception_count == 0
        assert root.uniform
        assert root.parent is RootParent.NO_ACCESS
        assert root.rights_breakdown == ((mask(MODIFY), 5),)
        assert summary.reachable_directories == 5

    def test_the_share_root_itself_can_be_the_root(self) -> None:
        verdicts = {key(path): granted() for path in FINANCE}
        summary = summarize_directory_access(tree(*FINANCE), verdicts)

        assert len(summary.roots) == 1
        assert summary.roots[0].parent is RootParent.SHARE_ROOT
        assert summary.roots[0].reachable_descendants == len(FINANCE) - 1

    def test_different_shares_remain_distinct(self) -> None:
        paths = [*FINANCE, r"\\FS01\Projects", r"\\FS01\Projects\Apollo", r"\\FS02\Finance"]
        verdicts = {
            key(r"\\FS01\Finance"): granted(),
            key(r"\\FS01\Projects"): granted(),
            key(r"\\FS01\Projects\Apollo"): granted(),
            key(r"\\FS02\Finance"): granted(),
        }
        summary = summarize_directory_access(tree(*paths), verdicts)

        assert [root.key for root in summary.roots] == [
            key(r"\\FS01\Finance"),
            key(r"\\FS01\Projects"),
            key(r"\\FS02\Finance"),
        ]
        assert [root.facts.share_key for root in summary.roots] == [
            "fs01|finance",
            "fs01|projects",
            "fs02|finance",
        ]
        assert summary.shares == ("fs01|finance", "fs01|projects", "fs02|finance")

    def test_separate_subtrees_remain_separate_roots(self) -> None:
        verdicts = {
            key(r"\\FS01\Finance\Budgets"): granted(),
            key(r"\\FS01\Finance\Budgets\FY2026"): granted(),
            key(r"\\FS01\Finance\Payroll\Exports"): granted(READ_EXECUTE),
        }
        summary = summarize_directory_access(tree(*FINANCE), verdicts)

        assert {root.key: root.reachable_descendants for root in summary.roots} == {
            key(r"\\FS01\Finance\Budgets"): 1,
            key(r"\\FS01\Finance\Payroll\Exports"): 0,
        }

    def test_the_order_of_the_input_does_not_change_the_answer(self) -> None:
        verdicts = {key(path): granted() for path in FINANCE[1:]}
        verdicts[key(r"\\FS01\Finance\Budgets\FY2026")] = granted(FULL)
        shuffled = tree(*FINANCE)
        random.Random(7).shuffle(shuffled)

        first = summarize_directory_access(tree(*FINANCE), verdicts)
        second = summarize_directory_access(shuffled, dict(reversed(list(verdicts.items()))))

        assert first.roots == second.roots
        assert first.exceptions == second.exceptions


# ---------------------------------------------------------------- permission boundaries


class TestPermissionBoundaries:
    def test_an_effective_rights_change_is_an_exception_not_hidden(self) -> None:
        verdicts = {key(path): granted() for path in FINANCE[1:6]}
        verdicts[key(r"\\FS01\Finance\Budgets\FY2026")] = granted(FULL)
        verdicts[key(r"\\FS01\Finance\Budgets\FY2026\Q1")] = granted(FULL)
        verdicts[key(r"\\FS01\Finance\Budgets\FY2026\Q2")] = granted(FULL)
        summary = summarize_directory_access(tree(*FINANCE), verdicts)

        root = summary.roots[0]
        assert root.reachable_descendants == 4  # the change does not split the count
        assert root.exception_count == 1  # FY2026 only; its children match their parent
        assert not root.uniform
        assert root.reason_counts == ((ExceptionReason.RIGHTS_WIDEN, 1),)
        assert root.rights_breakdown == ((mask(FULL), 3), (mask(MODIFY), 2))
        (exception,) = summary.exceptions[root.key]
        assert exception.facts.key == key(r"\\FS01\Finance\Budgets\FY2026")
        assert exception.reachable
        assert exception.parent_verdict.rights == mask(MODIFY)

    def test_narrower_and_different_rights_are_told_apart(self) -> None:
        assert rights_change(mask(MODIFY), mask(READ_EXECUTE)) is ExceptionReason.RIGHTS_NARROW
        assert rights_change(mask(READ_EXECUTE), mask(MODIFY)) is ExceptionReason.RIGHTS_WIDEN
        assert rights_change(mask(0x00000001), mask(0x00000002)) is ExceptionReason.RIGHTS_DIFFER
        assert rights_change(mask(MODIFY), mask(MODIFY)) is None

    def test_a_broken_inheritance_is_surfaced_even_when_the_rights_match(self) -> None:
        directories = tree(*FINANCE)
        directories[3] = directory(r"\\FS01\Finance\Budgets\FY2026", protected=True)
        verdicts = {key(path): granted() for path in FINANCE[1:6]}
        summary = summarize_directory_access(directories, verdicts)

        (exception,) = summary.exceptions[key(r"\\FS01\Finance\Budgets")]
        assert exception.reasons == (ExceptionReason.INHERITANCE_BROKEN,)
        assert summary.roots[0].rights_breakdown == ((mask(MODIFY), 5),)

    def test_an_explicit_acl_that_changes_this_principals_access_is_an_exception(self) -> None:
        directories = tree(*FINANCE)
        directories[3] = directory(
            r"\\FS01\Finance\Budgets\FY2026", boundary="acl_differs_from_parent"
        )
        verdicts = {key(path): granted() for path in FINANCE[1:6]}
        for below in ("FY2026", r"FY2026\Q1", r"FY2026\Q2"):
            verdicts[key(rf"\\FS01\Finance\Budgets\{below}")] = granted(READ_EXECUTE)
        summary = summarize_directory_access(directories, verdicts)

        (exception,) = summary.exceptions[key(r"\\FS01\Finance\Budgets")]
        assert exception.reasons == (ExceptionReason.RIGHTS_NARROW,)
        assert exception.facts.boundary_reason == "acl_differs_from_parent"

    def test_an_acl_boundary_that_leaves_this_principals_answer_alone_is_not_one(self) -> None:
        """An explicit entry for somebody else is a boundary in the ACL, not in this
        principal's access. The tree still marks it; the summary does not count it."""
        directories = tree(*FINANCE)
        directories[3] = directory(
            r"\\FS01\Finance\Budgets\FY2026", boundary="acl_differs_from_parent"
        )
        verdicts = {key(path): granted() for path in FINANCE[1:6]}
        summary = summarize_directory_access(directories, verdicts)

        assert summary.roots[0].uniform
        node = summary.node(r"\\FS01\Finance\Budgets\FY2026")
        assert node is not None and node.facts.is_acl_boundary and node.reasons == ()

    def test_a_certainty_change_is_an_exception(self) -> None:
        verdicts = {key(path): granted() for path in FINANCE[1:6]}
        verdicts[key(r"\\FS01\Finance\Budgets\FY2025")] = granted(MODIFY, AccessCertainty.AT_LEAST)
        summary = summarize_directory_access(tree(*FINANCE), verdicts)

        (exception,) = summary.exceptions[key(r"\\FS01\Finance\Budgets")]
        assert exception.reasons == (ExceptionReason.CERTAINTY_CHANGE,)

    def test_an_inaccessible_branch_is_an_exception_and_is_not_counted(self) -> None:
        verdicts = {key(path): granted() for path in FINANCE[1:6]}
        verdicts[key(r"\\FS01\Finance\Budgets\FY2026")] = refused()
        summary = summarize_directory_access(tree(*FINANCE), verdicts)

        root = summary.roots[0]
        # Budgets and FY2025 only: FY2026 is refused, and Q1 and Q2 sit below the refusal.
        assert root.reachable_descendants == 1
        assert root.exception_count == 1
        (exception,) = summary.exceptions[root.key]
        assert exception.reasons == (ExceptionReason.ACCESS_ENDS,)
        assert not exception.reachable
        assert summary.reachable_directories == 4  # Q1 and Q2 are roots of their own

    def test_a_folder_whose_acl_names_nobody_in_the_token_ends_access(self) -> None:
        """The engine is never asked about a directory no token SID is named on; the
        summary sees it in the outline and reports where access stops."""
        verdicts = {key(r"\\FS01\Finance\Budgets"): granted()}
        summary = summarize_directory_access(tree(*FINANCE), verdicts)

        exceptions = summary.exceptions[key(r"\\FS01\Finance\Budgets")]
        assert {item.facts.key for item in exceptions} == {
            key(r"\\FS01\Finance\Budgets\FY2025"),
            key(r"\\FS01\Finance\Budgets\FY2026"),
        }
        assert all(item.verdict is None for item in exceptions)
        assert all(item.reasons == (ExceptionReason.ACCESS_ENDS,) for item in exceptions)

    def test_a_deny_and_an_indeterminate_answer_are_named_as_such(self) -> None:
        verdicts = {key(path): granted() for path in FINANCE[1:4]}
        verdicts[key(r"\\FS01\Finance\Budgets\FY2025")] = refused(AccessOutcome.DENIED)
        verdicts[key(r"\\FS01\Finance\Budgets\FY2026")] = refused(AccessOutcome.INDETERMINATE)
        summary = summarize_directory_access(tree(*FINANCE), verdicts)

        reasons = {
            item.facts.key: item.reasons
            for item in summary.exceptions[key(r"\\FS01\Finance\Budgets")]
        }
        assert reasons == {
            key(r"\\FS01\Finance\Budgets\FY2025"): (ExceptionReason.ACCESS_DENIED,),
            key(r"\\FS01\Finance\Budgets\FY2026"): (ExceptionReason.ACCESS_UNKNOWN,),
        }

    def test_access_resuming_below_an_inaccessible_folder_is_a_root_of_its_own(self) -> None:
        verdicts = {key(path): granted() for path in FINANCE[1:6]}
        verdicts[key(r"\\FS01\Finance\Budgets\FY2026")] = refused()
        verdicts[key(r"\\FS01\Finance\Budgets\FY2026\Q1")] = granted(READ_EXECUTE)
        summary = summarize_directory_access(tree(*FINANCE), verdicts)

        resumed = summary.root(r"\\FS01\Finance\Budgets\FY2026\Q1")
        assert resumed is not None
        assert resumed.parent is RootParent.NO_ACCESS
        assert resumed.resumes_within == key(r"\\FS01\Finance\Budgets")
        assert resumed.resumes_below == key(r"\\FS01\Finance\Budgets\FY2026")
        assert resumed.verdict.rights == mask(READ_EXECUTE)

    def test_a_folder_nobody_read_interrupts_the_run(self) -> None:
        """Continuity cannot be claimed through a directory no scan described."""
        directories = [
            item for item in tree(*FINANCE) if item.key != key(r"\\FS01\Finance\Budgets\FY2026")
        ]
        verdicts = {key(path): granted() for path in FINANCE[1:6]}
        del verdicts[key(r"\\FS01\Finance\Budgets\FY2026")]
        summary = summarize_directory_access(directories, verdicts)

        outer = summary.root(r"\\FS01\Finance\Budgets")
        assert outer is not None and outer.reachable_descendants == 1  # FY2025
        (gap,) = summary.exceptions[outer.key]
        assert gap.reasons == (ExceptionReason.NOT_OBSERVED,)
        assert not gap.facts.observed
        q1 = summary.root(r"\\FS01\Finance\Budgets\FY2026\Q1")
        assert q1 is not None and q1.parent is RootParent.NOT_OBSERVED
        assert q1.resumes_within == outer.key


# ---------------------------------------------------------------- semantic accuracy


class TestSemanticAccuracy:
    def test_a_root_with_exceptions_never_claims_to_be_uniform(self) -> None:
        verdicts = {key(path): granted() for path in FINANCE[1:6]}
        verdicts[key(r"\\FS01\Finance\Budgets\FY2025")] = granted(READ_EXECUTE)
        root = summarize_directory_access(tree(*FINANCE), verdicts).roots[0]

        assert not root.uniform
        assert len(root.rights_breakdown) == 2
        assert root.verdict.rights == mask(MODIFY)  # the root's own rights, nothing more

    def test_counts_add_up_to_the_reachable_set(self) -> None:
        verdicts = {key(path): granted() for path in FINANCE}
        verdicts[key(r"\\FS01\Finance\Budgets\FY2026")] = refused()
        verdicts[key(r"\\FS01\Finance\Payroll")] = granted(READ_EXECUTE)
        summary = summarize_directory_access(tree(*FINANCE), verdicts)

        reachable = sum(1 for verdict in verdicts.values() if verdict.reachable)
        assert sum(1 + root.reachable_descendants for root in summary.roots) == reachable
        assert summary.reachable_directories == reachable
        assert summary.unreachable_named == 1

    def test_paths_are_compared_case_insensitively_and_kept_as_displayed(self) -> None:
        directories = [
            directory(r"\\FS01\Finance"),
            directory(r"\\FS01\Finance\Budgets\\"),
            directory(r"\\fs01\FINANCE\budgets\FY2026"),
        ]
        verdicts = {
            key(r"\\FS01\Finance\Budgets"): granted(),
            key(r"\\FS01\Finance\Budgets\FY2026"): granted(),
        }
        summary = summarize_directory_access(directories, verdicts)

        assert len(summary.roots) == 1
        assert summary.roots[0].reachable_descendants == 1
        assert summary.roots[0].facts.path == r"\\FS01\Finance\Budgets"


# ---------------------------------------------------------------- tree


class TestTree:
    def test_the_top_level_is_the_share_roots_that_lead_somewhere(self) -> None:
        paths = [*FINANCE, r"\\FS01\Public"]
        verdicts = {key(r"\\FS01\Finance\Budgets"): granted()}
        summary = summarize_directory_access(tree(*paths), verdicts)

        assert [node.facts.key for node in summary.children(None)] == [key(r"\\FS01\Finance")]

    def test_children_lead_to_reach_or_mark_where_it_stops_and_hide_the_rest(self) -> None:
        verdicts = {key(r"\\FS01\Finance\Budgets"): granted()}
        summary = summarize_directory_access(tree(*FINANCE), verdicts)

        finance = summary.children(None)[0]
        assert finance.reachable_below == 1
        assert finance.child_count == 1  # Budgets; Payroll leads nowhere
        assert finance.hidden_child_count == 1
        (budgets,) = summary.children(finance.facts.key)
        assert budgets.is_root and budgets.reachable
        # Below a reachable folder every child is shown: each is where access stops.
        assert [node.reasons for node in summary.children(budgets.facts.key)] == [
            (ExceptionReason.ACCESS_ENDS,),
            (ExceptionReason.ACCESS_ENDS,),
        ]
        assert summary.children(key(r"\\FS01\Finance\Payroll")) == ()

    def test_reachable_below_counts_runs_that_resume_lower_down(self) -> None:
        verdicts = {key(r"\\FS01\Finance\Budgets"): granted()}
        verdicts[key(r"\\FS01\Finance\Budgets\FY2026\Q1")] = granted()
        summary = summarize_directory_access(tree(*FINANCE), verdicts)

        budgets = summary.node(r"\\FS01\Finance\Budgets")
        assert budgets is not None and budgets.reachable_below == 1
        fy2026 = summary.node(r"\\FS01\Finance\Budgets\FY2026")
        assert fy2026 is not None and not fy2026.reachable and fy2026.child_count == 1


# ---------------------------------------------------------------- scale


def test_a_large_estate_summarizes_in_bounded_time() -> None:
    """24 shares of ~1,000 directories, five levels deep, with protected folders, denied
    branches, resumed access and rights changes spread through every share."""
    directories: list[DirectoryFacts] = []
    verdicts: dict[str, DirectoryVerdict] = {}
    rng = random.Random(42)
    for share in range(24):
        base = rf"\\FS{share // 8:02d}\Share{share:02d}"
        frontier = [base]
        directories.append(directory(base))
        while len(frontier) < 1000:
            parent = frontier[rng.randrange(len(frontier))]
            if parent.count("\\") > 8:
                continue
            path = rf"{parent}\D{len(frontier):04d}"
            frontier.append(path)
            directories.append(directory(path, protected=rng.random() < 0.02))
        for path in frontier[1:]:
            roll = rng.random()
            if roll < 0.80:
                verdicts[key(path)] = granted()
            elif roll < 0.85:
                verdicts[key(path)] = granted(FULL)
            elif roll < 0.90:
                verdicts[key(path)] = refused(AccessOutcome.DENIED)
            elif roll < 0.95:
                verdicts[key(path)] = refused()
    started = time.perf_counter()
    summary = summarize_directory_access(directories, verdicts)
    elapsed = time.perf_counter() - started

    reachable = sum(1 for verdict in verdicts.values() if verdict.reachable)
    assert len(directories) == 24_000
    assert summary.reachable_directories == reachable
    assert sum(1 + root.reachable_descendants for root in summary.roots) == reachable
    assert len(summary.roots) < reachable / 4  # the point of the exercise
    assert summary.exception_total > 0
    assert elapsed < 5.0, f"summarizing {len(directories)} directories took {elapsed:.2f}s"
