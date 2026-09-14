# ADR-0011: An effective-access answer carries the conditions that qualify it

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 4B (`phase-04/02-effective-access-resolver.md`)
- **Deciders:** Phase 4B implementation

## Context

Every input to an effective-access answer is an observation, and observations have gaps. A
share whose ACL no run has read. A group nobody enumerated. A directory whose descriptor
was never fetched. A traversal that hit its limit. A mask carrying `MAXIMUM_ALLOWED`, whose
meaning Windows resolves per open.

A resolver that returns a mask and nothing else forces every consumer downstream to treat
**"no access"** and **"no data"** as the same answer. In an audit tool that is precisely how
a finding disappears: a risk rule reads zero rights, concludes nobody can reach the data,
and stops looking at the one directory where the membership was never collected.

The project has taken the same position at every prior layer rather than settling it once:
a boundary whose parent was unread is reported as a boundary with reason
`parent_unreadable` (ADR-0009); an unreconciled scope may not be used to infer absence
(Phase 2B); a bounded traversal reports its own truncation (ADR-0006); a collector's
`acl_hash` is reported beside the server's recomputation rather than one being preferred
(ADR-0008). Phase 4B is where those gaps meet in a single number that somebody will act on.

## Decision

**Every effective-access answer names what the resolver assumed or could not settle, and
states the direction in which it may therefore be wrong.**

1. **`app.access_engine.conditions.AccessCondition` is a closed vocabulary.** Its values
   appear in API responses and clients branch on them, so a rename is a contract change.
   Each occurrence is an `AccessFinding` carrying the evidence — the trustee, the ancestor,
   the SIDs an assumption added — and an operator-facing sentence.
2. **Conditions fold into a direction, not a score.** `AccessCertainty` is `certain`,
   `at_most` (an unseen restriction could only narrow this), `at_least` (an unseen grant or
   membership could only widen this), or `uncertain`. Two directions at once do **not**
   cancel: a result nobody can bound must not be reported as a number without qualification.
3. **`access: false` with `certainty: at_least` means no access was established, never that
   none exists.** The distinction is the point of the field.
4. **A coverage gap is never resolved into a default.** An unread share ACL does not become
   unrestricted; a path with no stored descriptor does not become "no access"; a group with
   no collected membership does not become "not a member". Each raises its own condition and
   moves the certainty.
5. **`TRUSTEE_MEMBERSHIP_UNOBSERVED` is raised by the service, not the domain.** The
   judgement belongs to the vocabulary and the evidence needs a database, so
   `unobserved_trustee_findings` takes the trustees and the coverage and the service supplies
   the coverage. A caller-supplied finding is folded into the certainty exactly as an
   internally derived one is.
6. **A listing says what it could not list.** `/access/resources/{r}/principals` reports
   `enumeration.complete` and names the trustees whose members cannot be enumerated — a world
   SID such as `Everyone`, or a group whose expansion was truncated — rather than returning a
   short list that looks whole.
7. **`ASSUMED_TOKEN_SIDS` is in neither direction.** Windows places `Everyone`,
   `Authenticated Users` and the access path's logon SID in every token of the declared kind,
   so the assumption is exact once the kind is known; whether the kind is known is what
   `SUBJECT_IS_A_GROUP` and `SUBJECT_UNRESOLVED` report. It is still reported, because an
   assumption nobody can see is one nobody can check.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Return the mask alone | Makes "no access" and "no data" indistinguishable, which is the failure this whole phase is built against. |
| A numeric confidence score | Not derivable from anything, not comparable between answers, and it hides the one thing that matters: *which* direction the answer could be wrong in. |
| A single `complete: bool` | Collapses "the share ACL was never read" (upper bound) into "a group was never enumerated" (lower bound). They call for opposite responses. |
| Free-text warnings | Cannot be branched on. A risk rule needs a value, not a sentence — so the vocabulary is closed and the sentence comes with it. |
| Raise conditions only when they change the mask | Not knowable. A membership nobody collected might change the answer and might not; that it *might* is the finding. |
| Let the two directions cancel to `certain` | Two gaps that could each move the answer the other way do not make it right. |
| Treat an unread share ACL as Everyone Full Control | Convenient and wrong: it turns an unknown into a claim, and would report every unscanned share as fully open. |
| Treat an unread share ACL as granting nothing | The opposite error, and worse for an audit: every unscanned share would report no access, hiding every finding beneath it. |

## Consequences

**Positive**

- A risk rule, a report, and a UI can each distinguish a finding from a coverage gap without
  re-deriving the distinction.
- The conditions double as a scan-coverage report: `SHARE_ACL_NOT_OBSERVED` and
  `NTFS_ACL_NOT_OBSERVED` across an estate name exactly what has not been collected yet.
- Escalation stays visible. `ESCALATION_RIGHTS` is raised on the **final** effective mask, so
  a share granting Full Control in front of NTFS granting nothing does not produce a false
  one, and `Modify` hiding `WRITE_DAC` does not produce a missing one.

**Negative / accepted costs**

- Responses are larger, and a client that ignores `findings` gets a bare mask that may be a
  bound. The field names make that visible; nothing can force it to be read.
- An estate that has only been partly scanned produces conditions on nearly every answer.
  That is accurate, and it will read as noise until the scan coverage improves.
- The vocabulary is a contract. Adding a condition is additive; renaming or removing one is
  breaking.

**Follow-up required**

- The risk engine (Phase 5) must branch on `certainty` rather than on the mask alone, and
  must not treat `at_least` with no rights as a clean result.
- The UI must render `access: false, certainty: at_least` differently from
  `access: false, certainty: certain`.

## Compliance

- `tests/access_engine/test_resolver.py::TestCertainty` — the four directions, and that two
  gaps do not cancel.
- `tests/access_engine/test_resolver.py::TestAnUnreadShareIsNotAnOpenOne` — no share
  evaluation is fabricated and the answer becomes an upper bound.
- `tests/access_engine/test_resolver.py::TestTrusteeCoverage` — a caller-supplied coverage
  finding reaches the certainty.
- `tests/db/test_access_api.py::TestWhoCanReachAResource` — an `Everyone` ACE makes a listing
  incomplete and names the trustee.
- `CONDITION_DESCRIPTIONS` covers every member of `AccessCondition`; `describe_condition`
  raises a `KeyError` on a condition with no sentence, so one cannot be added silently.
