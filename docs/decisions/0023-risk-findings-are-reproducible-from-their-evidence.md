# ADR-0023: A risk finding is a deterministic rule plus the records it can be re-derived from

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 8A — deterministic access risk engine
- **Deciders:** Phase 8A implementation

## Context

ADG has collected permission facts and can answer exactly who can reach what (ADR-0010) with
the uncertainty attached to each answer (ADR-0011). The next thing an access review wants is
the other direction: not *"what can Alice reach"* but *"what here should not be like this"*.

Every product in this space answers that with a risk score. A score is attractive because it
sorts, and it is a trap for three reasons that all bite at the same moment — the moment
somebody has to act on it.

* **It cannot be checked.** "This share scores 78" is not a claim anybody can verify, argue
  with, or reproduce.
* **It cannot be re-checked.** Six months later the estate has moved, the model has been
  retuned, and nothing establishes whether the finding was true when it was made.
* **It has no unit, and people compare it anyway.** A share at 78 and a share at 54 will be
  prioritized against each other, and nothing about the two numbers supports that.

An audit tool's output is read by somebody who is about to remove access from a production
file server. It has to be the kind of statement that survives being questioned.

There is also a narrower constraint from inside the system. The rules need to know what an
access control list actually grants, and ADG already has an implementation of that —
`app.access_engine`, a faithful Windows access check. A second opinion about what a mask means
would eventually disagree with the access screen about the same ACE, with nothing to say which
was right.

## Decision

**A risk finding is the output of a named, versioned, deterministic rule, and it carries the
complete records that rule matched on.**

1. **Rules are pure predicates over a typed fact bundle.** No database, no clock, no
   randomness, no unsorted iteration. `app/risk_engine/` is framework-free; the repository is
   the only seam to storage.
2. **Every finding cites the complete records it was computed from**, in the records' own
   vocabulary, not as a rendering. `app/risk_engine/engine.py::reproduce` rebuilds a fact
   bundle from a finding's evidence alone and re-runs the single rule that produced it, and
   the finding must come back identical.
3. **Severity is configuration, confidence is derived.** A rule reports a *band* — a fact
   about the finding — and configuration maps the band to a severity. Confidence is computed
   from the `FactQualifier` values attached to the outcome and is always the weakest any one
   qualifier allows. Neither can be set by a rule.
4. **Severities rank; they never add.** There is no score in ADG. Findings are counted and
   sorted, never summed.
5. **The rules ask `app.access_engine` what an access control list grants**, in stored order,
   through the same access check the effective-access screen runs.
6. **Risk never replaces permission truth.** A finding is a statement *about* the permission
   facts, computed from them and carrying them; it is never stored in their place and never
   consulted instead of them.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| A weighted risk score per resource | Cannot be checked, re-checked, or compared meaningfully. The three failures all arrive at the moment somebody acts on it. |
| Rules with evidence as a rendered sentence | A sentence cannot be rebuilt into facts, so the finding can never be re-derived — and evidence that only illustrates looks identical to evidence that justifies until somebody tries. |
| Severity computed inside each rule | Makes quieting a noisy rule a code change, and invites making a rule *stop matching* in order to lower a number. |
| Confidence as a configurable field | Raising confidence claims evidence ADG does not have. It is a statement about our own data, not about how much the finding matters. |
| Rules computing rights themselves | A second implementation of the most safety-critical code in the product, guaranteed to drift from the access screen. |
| Storing only the finding, re-reading the estate to explain it | The estate moves. "Was this true in March" becomes unanswerable the moment an administrator fixes something. |

## Consequences

**Positive**

- Every finding can be audited against the evidence stored with it, offline, months later.
- A rule that reads a record it does not cite fails its reproduction test rather than shipping
  a finding nobody can check.
- An installation re-grades severities without touching a predicate, so findings produced
  under two policies were still produced by the same logic and remain comparable.
- One implementation of Windows access semantics, used by both the access screen and the rules.

**Negative / accepted costs**

- Evidence is bulky. A finding carries whole records rather than a summary, and the evidence
  column is the largest thing in the risk tables.
- Rules cannot use anything the fact model does not carry. Adding a signal means adding it to
  the facts, to the evidence round trip, and to the reproduction test.
- No score means no single number to sort a report by. Reports sort by severity then
  confidence, which is coarser and is the point.
- Deterministic rules will miss things a model might have surfaced. That is accepted: a missed
  finding is found by the next rule somebody writes, and an unverifiable finding is acted on.

**Follow-up required**

- An HTTP surface and a screen (Phase 8B and beyond). Phase 8A ships services only.
- Evidence retention. The risk tables inherit the same disclosure class and retention question
  as `object_versions`.

## Compliance

`backend/tests/risk_engine/test_reproduction.py` builds an estate carrying every shape the
eleven rules match, asserts that every rule produces at least one finding over it, and then
re-derives **every** finding from its own evidence — asserting the key, band, severity,
confidence and qualifiers all come back identical.

Its negative control is what makes the rest mean anything:
`test_a_finding_stripped_of_one_record_no_longer_reproduces` removes one cited record and
requires the finding to stop reproducing. A rule matching on something weaker than the facts it
cites fails there.

`backend/tests/db/test_risk_findings.py::test_every_finding_reproduces_from_the_evidence_that_was_stored`
repeats the check against evidence that has made a round trip through JSONB.

A violation of the severity/confidence split fails at construction:
`RiskFinding.__post_init__` recomputes the confidence from the qualifiers and refuses any value
that is not what they support.
