# ADR-0027: A change is classified along four axes, not scored on one

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 7C — change diff APIs and UI
- **Deciders:** Phase 7C implementation

## Context

Phase 7A gave every object a timeline. Turning those timelines into a page an operator reads
on a Monday morning needs an answer to one question — *is any of this a problem?* — and the
obvious way to answer it is a severity number per change.

A single number has to fold several independent facts into itself, and the folding is lossy
in exactly the direction that matters:

* **Direction.** "Everyone was granted Full Control" and "Everyone's Full Control was
  removed" touch the same object, the same field and the same magnitude of right. One is an
  incident. A scheme that scores both `critical` is useless and a scheme that scores the
  removal low has buried a control being deleted.
* **Relevance.** A display name and an ACE are both stored state. Only one of them is a
  permission, and a number cannot say which without also saying how *much* of a permission —
  at which point a metadata change with a large delta out-ranks a small real one.
* **Ignorance.** ADG regularly cannot tell whether something is security-relevant: a
  position inside an ACL, a column nobody has classified, a directory that stopped being
  protected. A number has no value that means "I do not know", so the unknown cases get a
  plausible one and become invisible.
* **What happened.** A removal in ADG rests on a reconciled scope having looked and not
  found the object. That provenance has to travel with the change and cannot be a number.

There is a second temptation with the same shape: score by accumulation, adding points per
contributing factor. That makes the severity an arithmetic result nobody can argue with, and
lets three harmless attributes out-score one Deny removal.

## Decision

A change carries **four independent judgments** and they are never folded together.

| Axis | Values | What it answers |
| --- | --- | --- |
| `action` | `added`, `modified`, `removed`, `first_observed` | What happened to the object |
| `significance` | `security`, `metadata`, `noise`, `undetermined` | Whether it is about access at all |
| `direction` | `broadened`, `narrowed`, `mixed`, `neutral`, `undetermined` | Which way access moved |
| `severity` | `critical`, `high`, `medium`, `low`, `info` | How much attention it deserves |

Three rules make the set work:

1. **Every axis has an "ADG cannot tell" value, and it is never the quiet one.** An
   undetermined change scores `medium` and is in the default filter. The cost of ADG not
   understanding something is a line an operator reads, never a line nobody sees.
2. **Severity comes from an ordered rule table, first match wins.** `app/changes/rules.py`
   is a readable list; each rule carries an id that travels in the response, so "why is this
   critical?" is answered by grepping one file. It is a decision, not an accumulation.
3. **`first_observed` is never scored.** It records that ADG had no prior view of the thing
   containing the object — not that anything was created. An estate's first scan produces one
   per object; scoring them would make the first day a page of incidents and teach the reader
   to ignore the column on every day after.

The four axes are computed by a pure function of two versions plus two static tables
(`app/changes/fields.py`, `app/changes/principals.py`). The same pair of versions always
produces the same classification, so it can be quoted in a finding.

### Severity is not risk

Severity here is a property of a **transition**. Risk (ADR-0023) is a property of the estate
as it stands. They will disagree, correctly: an `Everyone / Full Control` ACE that has been
in place for three years is a high risk and no change at all. Neither is derived from the
other.

### The direction of an edit is not the direction of access

`direction` describes what the edit grants, computed without touching the access engine.
Whether anybody's effective access actually moved is a separate answer, computed on demand by
`app/changes/impact.py` over the live engine, and the two disagree often — an Allow added
below a Deny, or beneath a share ACL that still caps it, broadens an ACL and changes nothing.
Keeping them apart is what lets the feed stay cheap and the impact answer stay real.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| One severity score per change | Folds direction into magnitude. A removed Deny and an added Allow are the same score and opposite findings |
| Additive scoring across contributing factors | Three harmless attributes out-score one Deny removal, and the total is not something anybody can argue with |
| Severity only, with direction as a free-text note | Notes are not filterable, and "show me everything that broadened access this week" is the query an operator actually has |
| No severity at all — show every change equally | A real estate's feed is mostly renumbered ACEs and display names. A reader who must skim four hundred of those to find one Deny removal stops reading the page, which is worse than any mis-scored line |
| Defer classification to the client | Two implementations of the judgment, and the derived-response contract already forbids it for exactly this reason |
| Reuse the Phase 8 risk severity | Different subject. A risk rule scores a state; these score a transition. Sharing the scale would make "critical" mean two things |

## Consequences

**Positive**

- "What broadened access in the last week, at high or above?" is a query rather than a
  reading exercise.
- A change ADG cannot classify is visible instead of silently scored as harmless.
- `rule_ids` in the response makes every severity traceable to one line of one table, which
  is what makes the table arguable — and therefore improvable.
- The feed can be filtered by default without hiding anything, because the summary counts
  the whole window before the filter and reports what was excluded.

**Negative / accepted costs**

- Four fields where clients might have wanted one, and a client that renders only `severity`
  loses the most useful column.
- The rule table is editorial. It encodes judgments — that a removed Deny is high, that
  `Everyone` is broad and `SYSTEM` is not — which a tenant may disagree with and cannot yet
  configure.
- First-match-wins means rule order is load-bearing. Mitigated by
  `tests/changes/test_rules.py`, which builds a case per rule and fails when a rule is
  shadowed by one above it.

## Compliance

- `tests/changes/test_rules.py::TestTheTableAsATable` — every rule has a case, every rule
  fires on its own case, the table always answers.
- `tests/changes/test_rules.py::TestTheJudgmentsThatCouldHaveGoneTheOtherWay` — the
  editorial calls, each with the reason it is that way round.
- `tests/changes/test_classify.py::TestSignificance` — the axes are computed independently.
- `tests/changes/test_model.py::TestSeverityIsOrderedByRankNotByName` — the ordering is by
  rank, and alphabetical order would invert it.
- `tests/db/test_change_feed.py::TestTheFirstScanIsNotAChangeReport` — a first scan is not a
  change report, and the summary still counts it.
