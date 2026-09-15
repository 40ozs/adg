r"""The generated runbook actually parses as PowerShell.

Every other test in this package checks that the script *says* the right things — that it has
a dry-run switch, that each step re-checks its precondition, that a local group sends somebody
to the right computer. None of them would notice an unbalanced brace.

That gap matters more here than it usually would. The runbook is generated text, it is the one
artifact of this product a person executes, and a syntax error in it is discovered at the
worst possible moment: in a change window, on a file server, by somebody who now has a half-
applied plan and no script. So the script is handed to PowerShell's own parser.

Skipped when ``pwsh`` is not on the path, which is honest: on a machine without it this
property is unverified rather than passing. The suite that produces the script is hermetic and
runs everywhere; only this check needs the tool.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.domain import MembershipEdgeKind, SharePermission
from app.domain.remediation import PlannedChangeKind
from app.remediation.export import render_runbook
from tests.remediation import factories as f

pytestmark = pytest.mark.skipif(
    shutil.which("pwsh") is None,
    reason="PowerShell 7 is not on the path; the runbook's syntax cannot be checked here.",
)

#: One step of every kind, plus the two that render differently: a local-group membership and
#: an inherited entry. A parse check over one change kind would pass while the other six were
#: broken.
EVERY_KIND = [
    f.change(PlannedChangeKind.REMOVE_NTFS_ACE, index=0, entry=f.entry(ace_key="a")),
    f.change(
        PlannedChangeKind.MODIFY_NTFS_ACE,
        index=1,
        entry=f.entry(ace_key="b", access_mask=f.FULL_MASK),
        after_access_mask=f.READ_MASK,
    ),
    f.change(PlannedChangeKind.REMOVE_SHARE_ACE, index=2),
    f.change(
        PlannedChangeKind.MODIFY_SHARE_ACE,
        index=3,
        entry=f.share_entry(ace_key="share-2", permission=SharePermission.FULL),
        after_permission=SharePermission.READ,
    ),
    f.change(PlannedChangeKind.REMOVE_GROUP_MEMBER, index=4),
    f.change(
        PlannedChangeKind.REMOVE_GROUP_MEMBER,
        index=5,
        target_key=f.BUILTIN_ADMINS,
        membership=f.edge(
            group_key=f.BUILTIN_ADMINS, edge_kind=MembershipEdgeKind.LOCAL_GROUP_MEMBER
        ),
    ),
    f.change(PlannedChangeKind.REPLACE_WITH_GROUP, index=6, entry=f.entry(ace_key="c")),
]

#: Parses the file and prints any syntax errors as JSON. Uses PowerShell's own parser rather
#: than running the script: running it would attempt Get-Acl against paths that do not exist,
#: and -- far more to the point -- a test that executed a remediation runbook would be the one
#: thing this whole phase exists to prevent.
#:
#: Run through ``-File`` rather than ``-Command``. With ``-Command``, trailing arguments are
#: not bound to ``$args``, so the path arrives as ``$null`` and ``ParseFile`` throws -- which
#: looks exactly like the script under test being broken, and would have made this suite
#: report a failure for every input including a valid one.
_CHECKER = """param([Parameter(Mandatory)][string]$Path)
$tokens = $null
$errors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
@($errors) | Where-Object { $null -ne $_ } | ForEach-Object {
    [pscustomobject]@{ line = $_.Extent.StartLineNumber; message = $_.Message }
} | ConvertTo-Json -AsArray -Compress
"""


def _parse_errors(script: str) -> list[dict[str, object]]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "plan.ps1"
        path.write_text(script, encoding="utf-8")
        checker = Path(directory) / "check.ps1"
        checker.write_text(_CHECKER, encoding="utf-8")
        result = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(checker),
                "-Path",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    assert result.returncode == 0, result.stderr
    output = result.stdout.strip()
    return json.loads(output) if output else []


class TestTheRunbookParses:
    def test_a_plan_of_every_change_kind_is_valid_powershell(self) -> None:
        script = render_runbook(
            f.plan(*EVERY_KIND),
            document_digest_value="d" * 64,
            signature="e" * 64,
            key_id="f" * 16,
            approver="dana@example.test",
        )

        errors = _parse_errors(script)

        assert not errors, "\n".join(
            f"line {error['line']}: {error['message']}" for error in errors
        )

    def test_a_title_containing_the_comment_terminator_does_not_break_the_banner(self) -> None:
        """The defanging in ``render_runbook`` is a string replacement; this proves the
        result is still a script rather than a banner that ended early."""
        script = render_runbook(
            f.plan(title="Quarterly #> Remove-Item C:\\ -Recurse -Force"),
            document_digest_value="d" * 64,
            signature="e" * 64,
            key_id="f" * 16,
            approver=None,
        )

        assert not _parse_errors(script)

    def test_a_quote_in_a_path_does_not_break_the_script(self) -> None:
        script = render_runbook(
            f.plan(f.change(target_display="\\\\fs01\\finance\\o'brien'; Remove-Item C:\\")),
            document_digest_value="d" * 64,
            signature="e" * 64,
            key_id="f" * 16,
            approver=None,
        )

        assert not _parse_errors(script)

    def test_the_parse_check_would_notice_a_broken_script(self) -> None:
        """A checker that always returned no errors would pass forever."""
        assert _parse_errors("if ($true) { Write-Host 'unclosed'")
