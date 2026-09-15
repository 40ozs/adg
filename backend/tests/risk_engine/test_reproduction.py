r"""Every finding can be re-derived from its own evidence.

This is the phase's central acceptance criterion and the strongest property in the engine.
:func:`app.risk_engine.reproduce` throws away the estate, rebuilds a fact bundle from the
finding's evidence blob and nothing else, re-runs the one rule that produced it, and must get
the same finding back.

It fails whenever a rule reads a record it does not cite, which is the failure that matters:
an evidence set that merely *illustrates* a finding looks identical to one that justifies it
until somebody tries to check it, and by then the estate has moved on.

The estate below is built once and deliberately carries every shape the eleven rules match, so
the parameterized test is an exhaustiveness check as well as a reproduction check — a rule that
produces nothing here fails :func:`test_every_rule_is_exercised_by_the_estate` rather than
passing vacuously.
"""

from __future__ import annotations

import pytest

from app.domain import AceType, AclBoundaryReason
from app.risk_engine import (
    AclProvenanceFacts,
    Evidence,
    EvidenceKind,
    RiskConfiguration,
    RuleId,
    SensitiveResource,
    Severity,
    evaluate,
    rebuild_facts,
    reproduce,
    reproduce_all,
)
from tests.risk_engine.support import (
    ALICE,
    AUTHENTICATED_USERS,
    DOMAIN_USERS,
    EVERYONE,
    FULL_CONTROL,
    MODIFY,
    READ_EXECUTE,
    bundle,
    group,
    membership,
    ntfs_ace,
    resource,
    share,
    share_ace,
    sid,
    unresolved,
    user,
)

FINANCE = "fs01|finance"
PAYROLL = "fs01|finance|payroll"
HR = "fs01|hr|confidential"
PUBLIC = "fs01|public"

FINANCE_RW = sid(1202)
FINANCE_TEAM = sid(1201)
FINANCE_LEADS = sid(1204)
DEEP_A, DEEP_B, DEEP_C, DEEP_D = sid(1301), sid(1302), sid(1303), sid(1304)
EMPTY_GROUP = sid(1209)
ERIN = sid(1108)
ORPHAN = sid(9911)


@pytest.fixture(scope="module")
def estate():
    r"""One estate carrying every shape the eleven rules match.

    Deliberately awkward, in the same spirit as ``app/demo/estate.py``: a fixture whose every
    answer is "one grant, one group" proves nothing about a product whose job is the other
    cases.
    """
    return bundle(
        resources=(
            # Everyone with Full Control, a direct user entry, and an orphan.
            resource(
                PUBLIC,
                ntfs_ace(PUBLIC, EVERYONE, FULL_CONTROL, order=0),
                ntfs_ace(PUBLIC, ALICE, MODIFY, order=1),
                ntfs_ace(PUBLIC, ORPHAN, READ_EXECUTE, order=2),
                path="\\\\FS01\\Public",
                share_key="fs01|public",
            ),
            # Authenticated Users, Domain Users, an empty group, and a deep chain.
            resource(
                FINANCE,
                ntfs_ace(FINANCE, AUTHENTICATED_USERS, MODIFY, order=0),
                ntfs_ace(FINANCE, DOMAIN_USERS, READ_EXECUTE, order=1),
                ntfs_ace(FINANCE, EMPTY_GROUP, MODIFY, order=2),
                ntfs_ace(FINANCE, DEEP_A, MODIFY, order=3),
                path="\\\\FS01\\Finance",
            ),
            # Two routes to one principal, a disabled member, and a sensitive subtree.
            resource(
                PAYROLL,
                ntfs_ace(PAYROLL, FINANCE_RW, MODIFY, order=0),
                ntfs_ace(PAYROLL, FINANCE_LEADS, READ_EXECUTE, order=1),
                ntfs_ace(PAYROLL, EVERYONE, FULL_CONTROL, order=2),
                path="\\\\FS01\\Finance\\Payroll",
            ),
            # Protection, and a deny that wins.
            resource(
                HR,
                ntfs_ace(HR, FINANCE_TEAM, FULL_CONTROL, ace_type=AceType.DENY, order=0),
                ntfs_ace(HR, FINANCE_TEAM, MODIFY, order=1),
                path="\\\\FS01\\HR\\Confidential",
                share_key="fs01|hr",
                dacl_protected=True,
                boundary_reason=AclBoundaryReason.PROTECTED_DACL,
                is_acl_boundary=True,
            ),
            # A projected DACL, so at least one finding is reproduced at reduced confidence.
            resource(
                "fs01|projects|beta",
                ntfs_ace("fs01|projects|beta", EVERYONE, MODIFY, order=0),
                path="\\\\FS02\\Projects\\Beta",
                share_key="fs02|projects",
                provenance=AclProvenanceFacts.DERIVED,
                derived_distance=2,
            ),
        ),
        shares=(
            share("fs01|public", share_ace("fs01|public", EVERYONE)),
            share("fs01|hr", observed=False),
        ),
        principals=(
            user(ALICE, name="Alice Chen"),
            user(ERIN, name="Erin Black", enabled=False),
            group(FINANCE_TEAM, name="Finance-Team"),
            group(FINANCE_RW, name="Finance-RW"),
            group(FINANCE_LEADS, name="Finance-Leads"),
            group(EMPTY_GROUP, name="Finance-Announce"),
            group(DEEP_A),
            group(DEEP_B),
            group(DEEP_C),
            group(DEEP_D),
            unresolved(ORPHAN),
        ),
        memberships=(
            membership(EMPTY_GROUP, enumerated=True),
            membership(FINANCE_RW, ALICE, ERIN),
            membership(FINANCE_LEADS, ALICE),
            membership(FINANCE_TEAM, ALICE),
            membership(DEEP_A, DEEP_B),
            membership(DEEP_B, DEEP_C),
            membership(DEEP_C, DEEP_D),
            membership(DEEP_D, ALICE),
        ),
    )


@pytest.fixture(scope="module")
def configuration():
    return RiskConfiguration(
        version="reproduction",
        sensitive_resources=(
            SensitiveResource(label="Payroll", path_prefix="\\\\FS01\\Finance\\Payroll"),
        ),
    )


@pytest.fixture(scope="module")
def findings(estate, configuration):
    produced = evaluate(estate, configuration).findings
    assert produced, "the estate must produce findings or the suite proves nothing"
    return produced


def test_every_rule_is_exercised_by_the_estate(findings):
    """A rule producing nothing here would make its reproduction test pass vacuously."""
    covered = {finding.rule_id for finding in findings}
    missing = sorted(rule.value for rule in RuleId if rule not in covered)
    assert not missing, f"the estate exercises no shape for: {missing}"


@pytest.mark.parametrize("rule_id", list(RuleId), ids=lambda item: item.value)
def test_every_finding_of_every_rule_reproduces_from_its_evidence(rule_id, findings, configuration):
    subset = [finding for finding in findings if finding.rule_id is rule_id]
    assert subset, f"no finding for {rule_id.value}"
    for finding in subset:
        again = reproduce(finding, configuration)
        assert again is not None, (
            f"{rule_id.value} finding {finding.key} could not be re-derived from its own "
            f"evidence ({len(finding.evidence)} records cited). The rule read something it "
            "did not cite."
        )
        assert again.key == finding.key
        assert again.band is finding.band
        assert again.severity is finding.severity
        assert again.confidence is finding.confidence
        assert again.qualifiers == finding.qualifiers


def test_the_whole_report_reproduces(findings, configuration):
    report = reproduce_all(findings, configuration)
    assert report.total == len(findings)
    assert report.complete, f"did not reproduce: {report.unreproduced}"
    assert report.changed_severity == {}


def test_reproduction_reads_nothing_but_the_evidence(findings, configuration):
    """The rebuilt bundle holds the cited records and no others.

    If a reproduction quietly had the whole estate available it would prove nothing about the
    evidence, so this pins that the bundle really is minimal.
    """
    for finding in findings:
        rebuilt = rebuild_facts(finding.evidence)
        cited_resources = set(finding.evidence.keys_of(EvidenceKind.RESOURCE))
        assert {item.resource_key for item in rebuilt.resources} == cited_resources
        cited_shares = set(finding.evidence.keys_of(EvidenceKind.SHARE))
        assert {item.share_key for item in rebuilt.shares} == cited_shares


def test_evidence_is_never_empty(findings):
    assert all(len(finding.evidence) > 0 for finding in findings)


def test_evidence_survives_a_json_round_trip(findings):
    """Evidence is stored as JSONB, so the digest must be stable across serialization."""
    for finding in findings:
        again = Evidence.from_json(finding.evidence.to_json())
        assert again.digest == finding.evidence.digest
        assert again.items == finding.evidence.items


def test_the_digest_ignores_the_order_records_were_cited_in(findings):
    for finding in findings:
        shuffled = Evidence.of(reversed(finding.evidence.items))
        assert shuffled.digest == finding.evidence.digest


def test_a_finding_stripped_of_one_record_no_longer_reproduces(findings, configuration):
    """The negative control.

    Without it the suite would still pass if ``reproduce`` matched on something weaker than
    the facts — so this removes a cited record and requires the finding to stop coming back.
    """
    weakened = 0
    for finding in findings:
        aces = finding.evidence.of_kind(EvidenceKind.ACE)
        if not aces:
            continue
        without = Evidence.of(item for item in finding.evidence.items if item != aces[0])
        stripped = type(finding)(
            rule_id=finding.rule_id,
            rule_version=finding.rule_version,
            key=finding.key,
            subject=finding.subject,
            band=finding.band,
            severity=finding.severity,
            confidence=finding.confidence,
            qualifiers=finding.qualifiers,
            evidence=without,
            detail=finding.detail,
        )
        assert reproduce(stripped, configuration) is None, (
            f"{finding.rule_id.value} reproduced without the entry it cited, so its evidence "
            "is not what the rule actually matched on."
        )
        weakened += 1
    assert weakened >= 5, "the control must exercise several rules to mean anything"


def test_reproducing_under_a_different_configuration_can_change_severity(findings):
    """Severity is configuration, so a re-derivation under new settings may re-grade it.

    Asserted rather than avoided: it is the correct behavior, and a test that pinned severity
    across configurations would be pinning the opposite of the design.
    """
    lowered = RiskConfiguration(
        version="lowered",
        severity_overrides={
            RuleId.EVERYONE_BROAD_ACCESS: {
                finding.band: _one_step_down(finding.severity)
                for finding in findings
                if finding.rule_id is RuleId.EVERYONE_BROAD_ACCESS
            }
        },
    )
    subject = next(item for item in findings if item.rule_id is RuleId.EVERYONE_BROAD_ACCESS)
    again = reproduce(subject, lowered)
    assert again is not None
    assert again.key == subject.key
    assert again.severity is not subject.severity


def _one_step_down(severity: Severity) -> Severity:
    from app.risk_engine import SEVERITY_ORDER, Severity

    rank = max(0, SEVERITY_ORDER[severity] - 1)
    return next(item for item in Severity if SEVERITY_ORDER[item] == rank)
