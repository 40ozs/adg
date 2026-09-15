r"""The signed change plan: what leaves ADG, what covers it, and what a person runs.

An export is the product of this whole phase. ADG cannot change Windows, so the artifact it
produces is an instruction — and an instruction somebody will carry out at two in the morning
against a payroll share has to be better than a paragraph in a ticket.

Three things are built here.

**A canonical document.** Everything an executing administrator and a later auditor need: the
plan, every change with the entry it was written against, the blast radius that was measured,
who requested it, who approved it and which digest they approved, the collection basis it was
checked against, and the head of the plan's audit chain. Serialized with
:func:`app.remediation.model.canonical_json`, which is the one serialization this codebase
signs anything with.

**A signature over those bytes.** HMAC-SHA256 with a deployment key. It answers exactly one
question — *did this document come from this ADG deployment, unmodified* — and this docstring
says so rather than letting a reader assume more. It is **not** a per-person signature: it
proves the deployment emitted the document, not that a named approver pressed a button, and
anybody holding the key can produce one. Who approved what is carried *inside* the document
and chained in the audit trail, and that is where accountability lives.

**A runbook.** A PowerShell script the administrator actually runs. Two properties make it
worth generating rather than leaving to a human:

* **It does nothing by default.** Without ``-Execute`` every step reports what it would do and
  changes nothing. A script that is destructive when double-clicked is one that will
  eventually be double-clicked.
* **It re-checks the precondition on the machine.** Each step reads the live ACE or membership
  and compares it with what ADG observed, and stops the whole run if they differ. ADG's own
  check is against what it last *collected*, which can be hours old; the script's check is
  against the object itself at the moment of the change. Neither makes the other redundant.

**Why there is no unsigned export.** If no signing key is configured the export is refused,
with the setting named. An unsigned change plan is indistinguishable from one somebody typed,
and the person executing it has no way to tell — so "unsigned, but marked as such" would put
the burden of noticing a missing banner on the reader least able to bear it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
from collections.abc import Mapping, Sequence
from typing import Any, Final

from app.domain import MembershipEdgeKind
from app.domain.remediation import PlannedChangeKind
from app.remediation.errors import RemediationConflict
from app.remediation.model import (
    PLAN_DOCUMENT_VERSION,
    ApprovalRecord,
    ChangePlan,
    PlannedChange,
    PreconditionReport,
    canonical_json,
)

__all__ = [
    "EXPORT_DOCUMENT_KIND",
    "SIGNATURE_ALGORITHM",
    "build_document",
    "document_digest",
    "key_id_for",
    "render_runbook",
    "sign_document",
    "verify_document",
]

EXPORT_DOCUMENT_KIND: Final = "adg.remediation.change-plan"
"""What the document *is*, stated inside it.

A verifier that recognizes only this kind refuses anything else rather than checking a
signature over a document whose meaning it does not know — which is how a signed value from
one context gets replayed into another.
"""

SIGNATURE_ALGORITHM: Final = "HMAC-SHA256"

_KEY_ID_LENGTH: Final = 16


def key_id_for(key: str) -> str:
    """A stable, public identifier for a signing key, derived from the key itself.

    A digest prefix rather than a configured name, so that rotating the key necessarily
    changes the identifier: a verifier holding the old key can then say *"this was signed with
    a key I do not have"* instead of reporting a bad signature, which sends somebody looking
    for tampering that did not happen.

    Sixteen hex characters — 64 bits — of SHA-256 over the key. It discloses nothing usable:
    recovering the key from it means inverting SHA-256, and a deployment's key identifier
    appearing in a change ticket is the point.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:_KEY_ID_LENGTH]


def document_digest(document: Mapping[str, Any]) -> str:
    """SHA-256 over the canonical bytes. What the signature is taken of, and printable."""
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def sign_document(document: Mapping[str, Any], key: str) -> tuple[str, str]:
    """Sign a document. Returns ``(signature, key_id)``.

    Raises:
        RemediationConflict: if no key is configured. Refusing is deliberate — see the module
            docstring — and it is a conflict rather than a validation error because nothing
            about the request is wrong: the deployment is not set up to do this.
    """
    if not key:
        raise RemediationConflict(
            "This deployment has no change-plan signing key, so it cannot produce a signed "
            "export. Set ADG_REMEDIATION_SIGNING_KEY. ADG does not emit unsigned change "
            "plans: an unsigned one is indistinguishable from a document somebody typed, and "
            "the administrator executing it has no way to tell them apart."
        )
    signature = hmac.new(
        key.encode("utf-8"), canonical_json(document).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return signature, key_id_for(key)


def verify_document(document: Mapping[str, Any], signature: str, key: str) -> bool:
    """Whether a signature covers this document under this key.

    Constant-time comparison, and a plain boolean rather than an exception, because a verifier
    is usually a script and the caller decides what a failure means. The documented recipe in
    ``docs/operations/remediation-runbook.md`` is this function in four lines of Python, so a
    verification does not require ADG to be running.
    """
    if not key or not signature:
        return False
    expected = hmac.new(
        key.encode("utf-8"), canonical_json(document).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def build_document(
    plan: ChangePlan,
    *,
    approvals: Sequence[ApprovalRecord],
    preconditions: PreconditionReport,
    impact: Mapping[str, Any] | None,
    audit_head_digest: str | None,
    audit_head_index: int | None,
    exported_by_subject: str,
    exported_by_display_name: str | None,
    exported_at: dt.datetime,
) -> dict[str, Any]:
    """The canonical export document.

    Everything in it is a fact ADG already holds. Nothing is computed here that is not
    recorded somewhere else, because a document that derived a number at export time would
    make the signature cover a claim nothing in the system agrees with.

    The ``execution`` block is the part somebody skimming reads: it states, *inside the signed
    bytes*, that ADG performed none of this. A change plan that did not say so could be
    mistaken — by a person, or by an automated pipeline somebody builds later — for a record
    of work completed.
    """
    return {
        "kind": EXPORT_DOCUMENT_KIND,
        "document_version": PLAN_DOCUMENT_VERSION,
        "exported_at": exported_at.astimezone(dt.UTC).isoformat(timespec="microseconds"),
        "exported_by": {
            "subject": exported_by_subject,
            "display_name": exported_by_display_name,
        },
        "execution": {
            "performed_by_adg": False,
            "statement": (
                "ADG has made none of these changes and has no ability to make them. This "
                "document is an instruction for a human administrator. Nothing is applied "
                "until somebody applies it, and ADG learns that it happened only from the "
                "next collection."
            ),
        },
        "plan": plan.document(),
        "approvals": [approval.document() for approval in approvals],
        "preconditions": preconditions.document(),
        "impact": dict(impact or {}),
        "audit": {
            "chain_key": f"plan:{plan.plan_id}",
            # The head *as of the moment before this export was recorded*, and necessarily so:
            # the export event's payload carries this document's digest, so a document
            # containing a digest of that event would have to contain a digest of itself.
            # The index is published with it so a verifier has something exact to check.
            "head_digest": audit_head_digest,
            "head_index": audit_head_index,
            "note": (
                "This is the plan's audit chain head immediately BEFORE the export event that "
                "produced this document -- the export event itself carries this document's "
                "digest, so it cannot also be inside it. To verify later: read the chain, take "
                "the event at head_index, and check its digest equals head_digest. Record both "
                "outside ADG. A chain rewritten wholesale verifies internally and will not "
                "match a digest recorded elsewhere."
            ),
        },
    }


# --------------------------------------------------------------------------------------
# The runbook
# --------------------------------------------------------------------------------------
#
# Every PowerShell template below is a plain string with ``@@TOKEN@@`` placeholders, filled by
# _fill. Deliberately not an f-string and not str.format: PowerShell is made of braces, and a
# template language that also uses braces means every script block has to be escaped by
# doubling. That escaping is invisible when it is right, produces a syntax error in the
# *generated* script when it is wrong, and the generated script is the artifact somebody runs
# against a production file server. A token nothing in PowerShell can spell removes the class
# of mistake rather than asking a future author to be careful.


def _fill(template: str, values: Mapping[str, Any]) -> str:
    rendered = template
    for token, value in values.items():
        rendered = rendered.replace(f"@@{token}@@", str(value))
    return rendered


def _ps_string(value: str | None) -> str:
    """A PowerShell single-quoted literal. Doubling the quote is the only escape it has."""
    if value is None:
        return "$null"
    return "'" + value.replace("'", "''") + "'"


_PREAMBLE = """#Requires -Version 7
<#
    ADG change plan @@PLAN_ID@@
    @@TITLE@@

    GENERATED BY ADG. ADG HAS NOT MADE THESE CHANGES AND CANNOT MAKE THEM.

    This script does nothing unless you pass -Execute. Without it every step reports what it
    would do and changes nothing.

    Before each change the script re-reads the live object and compares it with what ADG
    observed. If they differ the run stops: the entry you are about to change is not the entry
    that was reviewed and approved.

    Document digest  : @@DIGEST@@
    Signature        : @@SIGNATURE@@
    Signing key id   : @@KEY_ID@@
    Collection basis : @@BASIS@@
    Requested by     : @@REQUESTOR@@
    Approved by      : @@APPROVER@@

    Verify the signature before running this. The recipe is in
    docs/operations/remediation-runbook.md and does not require ADG to be running.
#>

[CmdletBinding()]
param(
    # Without this switch the script is a dry run. Deliberate: a remediation script that is
    # destructive when double-clicked is one that will eventually be double-clicked.
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-AdgStep {
    param([string]$Number, [string]$Description)
    Write-Host ''
    Write-Host ('=== Step ' + $Number + ': ' + $Description) -ForegroundColor Cyan
}

function Stop-AdgRun {
    param([string]$Number, [string]$Reason)
    throw ('Step ' + $Number + ' precondition failed: ' + $Reason + ' The estate no longer ' +
        'matches the plan that was approved. Stop here and ask ADG for a plan written ' +
        'against current state.')
}

function Test-AdgShouldExecute {
    param([string]$Number, [string]$Action)
    if (-not $Execute) {
        Write-Host ('  DRY RUN: would ' + $Action) -ForegroundColor Yellow
        return $false
    }
    return $true
}

Write-Host 'ADG change plan @@PLAN_ID@@' -ForegroundColor Green
if (-not $Execute) {
    Write-Host 'Dry run. Re-run with -Execute to apply.' -ForegroundColor Yellow
}
"""

_EPILOGUE = """
Write-Host ''
if ($Execute) {
    Write-Host 'Every step completed. ADG reports the new state after the next scan.' `
        -ForegroundColor Green
}
else {
    Write-Host 'Dry run complete. Nothing was changed.' -ForegroundColor Yellow
}
"""

_NTFS_STEP = """
Write-AdgStep -Number @@LABEL@@ -Description @@ACTION@@
@@INHERITED_NOTE@@
$adgPath@@N@@ = @@PATH@@
$adgSid@@N@@ = [System.Security.Principal.SecurityIdentifier]::new(@@SID@@)
$adgExpectedMask@@N@@ = @@EXPECTED_MASK@@
$adgExpectedType@@N@@ = '@@EXPECTED_TYPE@@'
if (-not (Test-Path -LiteralPath $adgPath@@N@@)) {
    Stop-AdgRun -Number @@LABEL@@ -Reason ($adgPath@@N@@ + ' does not exist.')
}
$adgAcl@@N@@ = Get-Acl -LiteralPath $adgPath@@N@@
$adgRules@@N@@ = @($adgAcl@@N@@.Access | Where-Object {
    $_.IdentityReference.Translate(
        [System.Security.Principal.SecurityIdentifier]) -eq $adgSid@@N@@ -and
    $_.AccessControlType.ToString() -eq $adgExpectedType@@N@@ -and
    [int]$_.FileSystemRights -eq $adgExpectedMask@@N@@
})
if ($adgRules@@N@@.Count -eq 0) {
    Stop-AdgRun -Number @@LABEL@@ -Reason 'The entry ADG recorded is not on this ACL now.'
}
if ($adgRules@@N@@.Count -gt 1) {
    Stop-AdgRun -Number @@LABEL@@ `
        -Reason 'More than one entry matches; ADG planned for exactly one.'
}
$adgRule@@N@@ = $adgRules@@N@@[0]
if (Test-AdgShouldExecute -Number @@LABEL@@ -Action @@ACTION@@) {
@@APPLY@@
    Set-Acl -LiteralPath $adgPath@@N@@ -AclObject $adgAcl@@N@@
    Write-Host '  Done.' -ForegroundColor Green
}
"""

_NTFS_REMOVE = """    $adgAcl@@N@@.RemoveAccessRuleSpecific($adgRule@@N@@)"""

_NTFS_NARROW = """    $adgAcl@@N@@.RemoveAccessRuleSpecific($adgRule@@N@@)
    $adgReplacement@@N@@ = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $adgRule@@N@@.IdentityReference,
        [System.Security.AccessControl.FileSystemRights]@@NEW_MASK@@,
        $adgRule@@N@@.InheritanceFlags,
        $adgRule@@N@@.PropagationFlags,
        $adgRule@@N@@.AccessControlType)
    $adgAcl@@N@@.AddAccessRule($adgReplacement@@N@@)"""

_INHERITED_NOTE = """
# WARNING: ADG observed this entry as INHERITED from @@PARENT@@. An inherited entry cannot be
# removed on this folder. Either remove it from that ancestor -- which affects every child --
# or break inheritance here first. This script does neither: both are decisions, and the plan
# proposed neither of them.
"""

_SHARE_STEP = """
Write-AdgStep -Number @@LABEL@@ -Description @@ACTION@@

$adgShare@@N@@ = @@SHARE@@
$adgSid@@N@@ = @@SID@@
$adgObserved@@N@@ = @@OBSERVED@@
$adgAccount@@N@@ = ([System.Security.Principal.SecurityIdentifier]::new($adgSid@@N@@)).Translate(
    [System.Security.Principal.NTAccount]).Value
$adgAccess@@N@@ = @(Get-SmbShareAccess -Name $adgShare@@N@@ | Where-Object {
    $_.AccountName -eq $adgAccount@@N@@
})
if ($adgAccess@@N@@.Count -eq 0) {
    Stop-AdgRun -Number @@LABEL@@ -Reason 'The share permission ADG recorded is not present now.'
}
Write-Host ('  ADG observed ' + $adgObserved@@N@@ + '; this server reports ' +
    (($adgAccess@@N@@ | ForEach-Object { $_.AccessRight }) -join ', '))
if (Test-AdgShouldExecute -Number @@LABEL@@ -Action @@ACTION@@) {
    Revoke-SmbShareAccess -Name $adgShare@@N@@ -AccountName $adgAccount@@N@@ `
        -Force | Out-Null@@GRANT@@
    Write-Host '  Done.' -ForegroundColor Green
}
"""

_SHARE_GRANT = """
    Grant-SmbShareAccess -Name $adgShare@@N@@ -AccountName $adgAccount@@N@@ `
        -AccessRight @@LEVEL@@ -Force | Out-Null"""

_MEMBERSHIP_STEP = """
Write-AdgStep -Number @@LABEL@@ -Description @@ACTION@@
@@NOTE@@
$adgGroupSid@@N@@ = @@GROUP_SID@@
$adgMemberSid@@N@@ = @@MEMBER_SID@@
$adgPresent@@N@@ = @(@@LIST_COMMAND@@ | Where-Object { $_.SID.Value -eq $adgMemberSid@@N@@ })
@@EXPECTATION@@
if (Test-AdgShouldExecute -Number @@LABEL@@ -Action @@ACTION@@) {
    @@COMMAND@@
    Write-Host '  Done.' -ForegroundColor Green
}
"""

_REMOVAL_EXPECTATION = """if ($adgPresent@@N@@.Count -eq 0) {
    Stop-AdgRun -Number @@LABEL@@ -Reason 'The membership ADG recorded is already gone.'
}"""

_ADDITION_EXPECTATION = """if ($adgPresent@@N@@.Count -gt 0) {
    Write-Host '  Already a member; nothing to add.' -ForegroundColor Yellow
}"""

_LOCAL_NOTE = """
# This is a LOCAL group on @@HOST@@. Run this step on that computer, not on a domain
# controller: a BUILTIN SID names a different group on every machine (ADR-0001).
"""

_DIRECTORY_NOTE = """
# This is a directory group. Run this step where the ActiveDirectory module is available.
"""


def render_runbook(
    plan: ChangePlan,
    *,
    document_digest_value: str,
    signature: str,
    key_id: str,
    approver: str | None,
) -> str:
    """The PowerShell an administrator runs, generated from the plan.

    Generated rather than hand-written for one reason that outweighs convenience: every step
    carries its precondition check. A person writing this by hand writes the check for the
    first three steps and stops, and the check is both the tedious part and the part that
    prevents the failure.

    The commands are the ordinary Windows ones — ``Set-Acl``, ``Revoke-SmbShareAccess``,
    ``Remove-ADGroupMember``, ``Remove-LocalGroupMember``. ADG does not invent tooling for
    something it is not doing.
    """
    header = _fill(
        _PREAMBLE,
        {
            "PLAN_ID": plan.plan_id,
            # A title containing the close-comment sequence would end the banner early and
            # leave the rest of it as executable code. Defanged rather than refused: a plan is
            # not rejected at export time for something no upstream validator had reason to
            # catch.
            "TITLE": plan.title.replace("#>", "# >"),
            "DIGEST": document_digest_value,
            "SIGNATURE": signature,
            "KEY_ID": key_id,
            "BASIS": plan.basis_token,
            "REQUESTOR": plan.requested_by_subject,
            "APPROVER": approver or "(not approved)",
        },
    )
    body = "\n".join(
        _render_step(change)
        for change in sorted(plan.changes, key=lambda item: item.sequence_index)
    )
    return f"{header}\n{body}\n{_EPILOGUE}"


def _render_step(change: PlannedChange) -> str:
    number = change.sequence_index + 1
    match change.kind:
        case PlannedChangeKind.REMOVE_GROUP_MEMBER:
            return _membership_step(change, number, removing=True)
        case PlannedChangeKind.REMOVE_NTFS_ACE:
            return _ntfs_step(change, number, mask=None)
        case PlannedChangeKind.MODIFY_NTFS_ACE:
            return _ntfs_step(change, number, mask=change.after_access_mask)
        case PlannedChangeKind.REMOVE_SHARE_ACE:
            return _share_step(change, number, level=None)
        case PlannedChangeKind.MODIFY_SHARE_ACE:
            return _share_step(
                change,
                number,
                level=(None if change.after_permission is None else change.after_permission.value),
            )
        case PlannedChangeKind.REPLACE_WITH_GROUP:
            # Removal first, then the membership that gives the access back. The order is the
            # plan's rather than a rendering choice, and it is defensible only because the
            # simulation has already reported whether the principal ends up where they
            # started. The other order would leave a window in which they hold both routes,
            # which is the thing the plan exists to end.
            return (
                _ntfs_step(change, number, mask=None)
                + "\n"
                + _membership_step(change, number, removing=False, sub_step=True)
            )
    raise AssertionError(  # pragma: no cover - the match is total over the enum
        f"{change.kind} has no runbook rendering"
    )


def _ntfs_step(change: PlannedChange, number: int, *, mask: int | None) -> str:
    entry = change.entry
    assert entry is not None
    action = (
        f"remove the {entry.ace_type.value} entry for {entry.trustee_sid}"
        if mask is None
        else f"narrow the {entry.ace_type.value} entry for {entry.trustee_sid} to {mask:#010x}"
    )
    apply_block = (
        _fill(_NTFS_REMOVE, {"N": number})
        if mask is None
        else _fill(_NTFS_NARROW, {"N": number, "NEW_MASK": mask})
    )
    note = (
        _fill(_INHERITED_NOTE, {"PARENT": entry.inherited_from or "an ancestor"})
        if entry.is_inherited
        else ""
    )
    return _fill(
        _NTFS_STEP,
        {
            "N": number,
            "LABEL": _ps_string(str(number)),
            "ACTION": _ps_string(action),
            "INHERITED_NOTE": note,
            "PATH": _ps_string(change.target_display or change.target_key),
            "SID": _ps_string(entry.trustee_sid),
            "EXPECTED_MASK": entry.access_mask if entry.access_mask is not None else 0,
            "EXPECTED_TYPE": "Deny" if entry.is_deny else "Allow",
            "APPLY": apply_block,
        },
    )


def _share_step(change: PlannedChange, number: int, *, level: str | None) -> str:
    entry = change.entry
    assert entry is not None
    display = change.target_display or change.target_key
    # A share key is 'share|<server>|<name>' and a UNC path is a backslash-separated pair; the
    # share's own name is the last segment either way, and that is what Get-SmbShareAccess
    # takes. Splitting rather than parsing, because this is a rendering and a malformed key
    # should produce a visibly wrong script rather than an exception at export time.
    name = display.rsplit("|", 1)[-1].rsplit("\\", 1)[-1]
    observed = (
        entry.permission.value.title()
        if entry.permission is not None
        else f"mask {entry.access_mask:#010x}"
        if entry.access_mask is not None
        else "an unrecorded right"
    )
    action = (
        f"revoke share access for {entry.trustee_sid} on {name}"
        if level is None
        else f"reduce share access for {entry.trustee_sid} on {name} to {level}"
    )
    grant = "" if level is None else _fill(_SHARE_GRANT, {"N": number, "LEVEL": level.title()})
    return _fill(
        _SHARE_STEP,
        {
            "N": number,
            "LABEL": _ps_string(str(number)),
            "ACTION": _ps_string(action),
            "SHARE": _ps_string(name),
            "SID": _ps_string(entry.trustee_sid),
            "OBSERVED": _ps_string(observed),
            "GRANT": grant,
        },
    )


def _membership_step(
    change: PlannedChange, number: int, *, removing: bool, sub_step: bool = False
) -> str:
    edge = change.membership
    assert edge is not None
    label = f"{number}b" if sub_step else str(number)
    suffix = f"{number}b" if sub_step else str(number)
    group_key = edge.group_key if removing else (change.replacement_group_key or edge.group_key)
    group_sid = (
        group_key.rpartition("|")[2]
        if removing
        else (change.replacement_group_sid or group_key.rpartition("|")[2])
    )
    group_name = (
        (edge.group_display_name or group_key)
        if removing
        else (change.replacement_group_display_name or group_key)
    )
    action = (
        f"remove {edge.member_display_name or edge.member_sid} from {group_name}"
        if removing
        else f"add {edge.member_display_name or edge.member_sid} to {group_name}"
    )
    if edge.edge_kind is MembershipEdgeKind.LOCAL_GROUP_MEMBER:
        note = _fill(_LOCAL_NOTE, {"HOST": edge.host_key or "the file server"})
        list_command = f"Get-LocalGroupMember -SID $adgGroupSid{suffix}"
        command = (
            f"Remove-LocalGroupMember -SID $adgGroupSid{suffix} -Member $adgMemberSid{suffix}"
            if removing
            else f"Add-LocalGroupMember -SID $adgGroupSid{suffix} -Member $adgMemberSid{suffix}"
        )
    else:
        note = _DIRECTORY_NOTE
        list_command = f"Get-ADGroupMember -Identity $adgGroupSid{suffix}"
        command = (
            f"Remove-ADGroupMember -Identity $adgGroupSid{suffix} "
            f"-Members $adgMemberSid{suffix} -Confirm:$false"
            if removing
            else f"Add-ADGroupMember -Identity $adgGroupSid{suffix} -Members $adgMemberSid{suffix}"
        )

    # A removal stops when the membership is already gone: somebody has acted since the plan
    # was approved, so the plan no longer describes this estate. An addition does not stop --
    # already being a member is the state the step is trying to reach, and refusing there
    # would strand the half-applied replacement this step exists to complete.
    expectation = _fill(
        _REMOVAL_EXPECTATION if removing else _ADDITION_EXPECTATION,
        {"N": suffix, "LABEL": _ps_string(label)},
    )
    return _fill(
        _MEMBERSHIP_STEP,
        {
            "N": suffix,
            "LABEL": _ps_string(label),
            "ACTION": _ps_string(action),
            "NOTE": note,
            "GROUP_SID": _ps_string(group_sid),
            "MEMBER_SID": _ps_string(edge.member_sid),
            "LIST_COMMAND": list_command,
            "EXPECTATION": expectation,
            "COMMAND": command,
        },
    )
