# ADR-0024: Business sensitivity is declared by an operator, never inferred by ADG

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 8A — deterministic access risk engine
- **Deciders:** Phase 8A implementation

## Context

Several of the most useful things a risk engine could say depend on knowing which data
matters. *"Everyone can write to the payroll folder"* is a different sentence from *"Everyone
can write to the scratch folder"*, and only one of them is worth waking somebody for.

ADG cannot tell them apart. It collects security descriptors, group membership and share
definitions. It never reads file contents, and it is not going to: content classification is
explicitly outside the product's scope, and adding it would change what the collectors need
permission to do.

The temptation is to infer sensitivity from what ADG *can* see — folder names, share
descriptions, keyword lists, how few people can reach something. Every one of those is wrong
often enough to matter, and wrong in a way that is invisible in the output:

* A folder called `HR` in a test estate holds nothing; a folder called `Misc` holds the
  payroll.
* A narrow ACL means somebody was careful once, or that the folder is abandoned.
* Keyword lists are a guess dressed as a policy, and a reader cannot tell which findings rest
  on the guess.

The specific harm is not the false positive. It is that a heuristic tag makes ADG's report
*look* like it knows something about the business, and a reviewer will act on the difference
between a critical finding and a medium one.

## Decision

**The sensitive-resource rule fires only for resources an operator has explicitly declared
sensitive, and ADG infers sensitivity from nothing.**

1. `RiskConfiguration.sensitive_resources` is the only source. Each entry carries a `label` —
   the operator's own words for what makes the resource sensitive — and exactly one selector:
   a `path_prefix`, a `resource_key`, or a `share_key`.
2. **No name, description, path word, or access-shape heuristic is consulted, anywhere.**
3. With no tags configured, `broad_access_on_sensitive_resource` produces nothing. The rule is
   still **enabled** by default and silent, which is a state that cannot be mistaken for a
   guarantee — disabling it by default would let a later operator declare a tag and believe
   they had turned something on.
4. That silence is reported as a configuration state rather than as a result.
   `RiskConfiguration.marks_anything_sensitive` exists so a report can say *"no resource is
   marked sensitive"* instead of rendering an empty section that reads as a clean bill of
   health — the same distinction ADR-0016 makes about empty access answers.
5. The tag's `label` is carried onto every finding it produces, so a reader sees the operator's
   stated reason rather than a judgment attributed to ADG.
6. A `path_prefix` matches whole path segments, case-insensitively. `\\FS01\Finance` covers
   `\\FS01\Finance\Payroll` and does not cover `\\FS01\Finance-Archive`.
7. A `path_prefix` never matches a share's own ACL. A share is not at a path, and one tag
   meaning different things on the two layers would make the declaration ambiguous.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Keyword matching on folder and share names | Wrong often, invisibly, and in the direction that inflates severity. A guess dressed as a policy. |
| Inferring sensitivity from a restrictive ACL | A narrow ACL means somebody was careful once, or that nobody uses the folder. It cannot distinguish the two. |
| Content classification | Out of scope by design, and would require collectors to read file contents. |
| Disabling the rule when nothing is tagged | Makes "off" and "nothing declared" identical, so declaring one tag later looks like enabling a feature rather than supplying the data it needs. |
| Defaulting every resource to sensitive | Every broad grant becomes critical, the severity ordering carries no information, and the report is unreadable. |
| A single boolean column on `ntfs_resources` | Would put an ADG judgment in the collected-facts tables, which hold observations only (ADR-0003). |

## Consequences

**Positive**

- No finding's severity ever rests on a guess about the business.
- An installation that has done the work of tagging gets a materially better report; one that
  has not is told plainly that it has not, rather than shown an empty section.
- The tag's label puts the operator's own reason on the finding, so the judgment is attributed
  to the person who made it.

**Negative / accepted costs**

- Out of the box, one of the eleven rules reports nothing. That is the honest state and it
  reads as a gap in a demo.
- Tagging is manual and will drift as the estate changes. Nothing here detects a tag pointing
  at a share that no longer exists.
- A prefix tag cannot express "everything on this server except one folder"; an installation
  needing that writes several entries.

**Follow-up required**

- A screen for managing tags, so declaring them is not a file edit (a later phase).
- Reporting a tag whose selector currently matches nothing, which is the drift case above.

## Compliance

`backend/tests/risk_engine/test_rules.py::TestBroadAccessOnSensitiveResource` asserts the rule
produces nothing with no configuration, produces a finding once a resource is tagged, and does
**not** match `\\FS01\Finance-Archive` under a `\\FS01\Finance` tag.

`backend/tests/db/test_risk_findings.py::test_the_sensitive_rule_fires_only_once_a_resource_is_tagged`
runs both halves against a real seeded estate.

`backend/tests/risk_engine/test_model.py::TestCatalog::test_the_sensitive_rule_is_enabled_and_silent_by_default`
pins point 3, and `test_the_summary_names_the_rules_that_are_off_and_the_absence_of_tags` pins
point 4.

A future heuristic would have to be added to `app/risk_engine/rules.py`, where
`BroadAccessOnSensitiveResource` reads `config.sensitive_resources` and nothing else. Any other
input to that rule violates this record.
