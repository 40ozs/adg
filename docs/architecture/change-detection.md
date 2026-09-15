# Change detection

How ADG answers *what changed since Friday, and is any of it a problem?* — and the four
things it refuses to say while answering it.

Phase 7A recorded every state every object was observed to hold
([history-model.md](history-model.md)). This document is about the layer above: turning that
record into findings. The code is `backend/app/changes/`.

---

## 1. Every change is a version opening

The whole feed rests on one property of the Phase 7A writer:

> a creation opens a version; a modification opens a version; **a removal opens a version
> too**, because a tombstone is a version rather than the absence of a row.

So *everything that changed between Tuesday and Friday* is exactly the set of versions whose
`valid_from` falls in that interval. One range scan, served by `ix_object_versions_opened_at`
(revision `0008`). There is no second query for deletions, no union, and no way for two
halves of a feed to disagree about what a change is.

A version alone says what the state *is*. A change needs what it *was*, so each version in a
page is paired with its predecessor. The writer closes a version at the same instant it opens
the next, so the fast path is exact equality — `valid_to = the successor's valid_from`. A
slower ordered lookup exists anyway, for the page's first change of each object, and it is
what makes correctness independent of that guarantee.

---

## 2. The four things ADG refuses to say

### It never calls a gap a removal

A removal exists only when a scan that **reconciled a scope** containing the object looked
and did not find it. That guard is Phase 7A's (`app/history/closure.py`) and is enforced four
independent ways before this layer is reached. A collector that simply did not mention a
share produced fewer observations, not evidence of a deletion.

Pinned by `tests/db/test_change_feed.py::TestNoRunMayProduceARemovalWithoutReconciling`,
which posts a non-reconciling run and a failed run through the real ingestion endpoints and
asserts the feed reports no removals.

### It never calls the start of observation a creation

An object's first version is either a creation or the moment ADG started looking, and the
object is identical in both cases. What separates them is the **container**:

> an ACE that appears on a directory ADG has been reading for months was *added*;
> the same ACE on a directory nobody had read before is simply the first reading.

So `ChangeAction` has four values, not three. `first_observed` means ADG had no prior view of
what contains the object. It is excluded from the default feed, counted in every summary, and
rendered as "first seen" — an estate's first scan produces one per object, and a page calling
them additions would report a whole estate as having been built on a Tuesday.

The one creation ADG can *prove* needs no container question: a version that follows a
tombstone. Somebody looked, it was gone, and a later scan found it.

### It never dates a change to the scan that found it

Every change carries a `window` with both ends (ADR-0019), never a single instant. `at` sits
beside it and is documented for what it is — the moment a collector looked — because it is
what the feed sorts and pages on.

An **addition** has no predecessor of its own: an ACE's rights are part of its identity, so
tightening one creates a different object. Its window's lower bound comes from the last
reading of the container it appeared in. Without that, half of every ACL edit would carry only
`at`, and a client would have nothing to render but the scan time — the model ADR-0019
rejected.

### It never hides how much it is hiding

The default feed shows security-relevant and unclassifiable changes and excludes first
sightings. That is an editorial choice, not a neutral one, so `GET /api/v1/changes/summary`
counts the **whole window before the filter** and reports `excluded`. Without that number a
page that is clean because of a default is indistinguishable from a quiet week — this
product's own failure mode, turned on its own interface.

---

## 3. Classification: four axes, never one score

ADR-0027. A change carries `action`, `significance`, `direction` and `severity`, computed by a
pure function of two versions plus two static tables. Every axis has an "ADG cannot tell"
value, scored at `medium` and included by default.

### The field table

`app/changes/fields.py` classifies **every column of every tracked kind**. Exhaustive against
`app/history/bindings.py`, checked by `tests/changes/test_fields.py` — a column added to
`ntfs_aces` fails the suite until somebody says what a change to it means. The default this
avoids is "not security relevant", which is the one default this product must never have.

Four entries are worth knowing because they are not obvious:

| Field | Classified | Why |
| --- | --- | --- |
| `principals.enabled` | security | A disabled account cannot authenticate, so every grant naming it is inert. Enabling it activates all of them and no ACL moved |
| `principals.group_type` | security | A distribution group is in no access token. Converting one to a security group turns every ACE naming it live |
| `smb_shares.local_path` | security | The share still exists with the same share ACL and now publishes a different directory |
| `ntfs_resources.acl_hash` | security | The collector's digest of the DACL it read. If it moves and no ACE did, entries were lost in transit — a collection finding |

`order_index` is classified `order` and deliberately **not** resolved by the table; see §4.

### The severity rules

`app/changes/rules.py` is an ordered list, first match wins, each rule carrying an id that
travels in the response. Rule order is load-bearing and tested: `tests/changes/test_rules.py`
builds one case per rule and fails when a rule is shadowed by one above it.

Two orderings inside it are deliberate and easy to get wrong:

* **Full Control is tested before escalation.** Full Control *contains* `WRITE_DAC`, so the
  escalation rule matches every Full Control grant. Both are critical; the sentence differs,
  and "Everyone was granted Full Control" is what happened.
* **`WRITE_BITS` is listed one bit at a time rather than using `FILE_GENERIC_WRITE`.** That
  constant carries `READ_CONTROL` and `SYNCHRONIZE`, which `FILE_GENERIC_READ` carries too, so
  intersecting a plain Read & Execute mask with it is non-zero. A rule built on it would
  report every read grant in the estate as a write grant.

### Broad and privileged trustees

`app/changes/principals.py` answers two questions the rules depend on: whether a trustee's
membership is a list anybody maintains, and whether a group's membership is itself an
administrative control. The sets are small and named one SID at a time. `SYSTEM` and
`CREATOR OWNER` are excluded on purpose — they appear on nearly every ACL Windows creates, and
scoring them would make the severity column unreadable within one scan.

---

## 4. The two things a change cannot know about itself

### An ACL edit is stored as a removal and an addition

An ACE's identity includes what it grants, so tightening a DACL from Full Control to Read &
Execute removes one row and adds another. Shown apart, an operator sees two unrelated events
and acts on at most one.

`app/changes/correlation.py` pairs them, and the pair is the only place ADG **computes** a
direction rather than assigning one: the two masks are compared, so `Read → Write` comes out
`mixed` instead of being rounded to whichever half a rule noticed first. A **Deny inverts** —
a Deny whose mask grew withholds more.

The pairing is conservative. Same kind, same container, same trustee, same Allow/Deny type,
exactly one removal and one addition. Where a trustee has several entries of one type on one
ACL and more than one moved, nothing is paired: which goes with which is not in the data, and
a guess would print a before/after that never existed.

### A position that moved may or may not mean anything

Windows evaluates a DACL in order, so an entry that has moved below a Deny now grants what
that Deny refused — and an entry renumbered because an audit ACE above it was deleted has
moved nothing at all. The number changed in both cases.

`ordering_materiality` decides it the way the product already decides whether two ACLs are the
same ACL: it rebuilds the normalized DACL at both ends of the change window and compares
digests (`app/domain/acl_hash.py`). The normal form reduces positions to their **rank**, so a
renumbering that preserves the sequence produces an identical document. There is one
definition of "the same ACL" in this codebase and this reuses it.

The share layer has no such digest — a share ACE carries no flags and a share ACL has no
DACL-present bit — so `_share_sequence` renders the equivalent sequence for it, under its own
version token (`adg-share-acl/1`) so it can never be mistaken for an NTFS digest.

**A failure to reconstruct returns "unknown", never "no".** "We checked and the order did not
move" and "we could not check" are different answers, and only the first may be rendered as
noise.

---

## 5. Why access changed

`GET /api/v1/changes/impact` resolves effective access either side of one edit, through
`HistoryService.effective_access_at` — which is `AccessService`, the live engine, reading
Phase 7A's as-of repositories. There is no historical access algorithm.

It is a separate request because resolving one principal against one resource is a membership
traversal plus two ACL evaluations; doing it per feed row would make the page cost
proportional to the estate.

Two instants, and neither is the obvious one:

* **`at_after` is the run's `completed_at`, not the change's `valid_from`.** A scan opens
  versions at the instant each object was *observed* and closes what it did not find at the
  instant the run *completed*. Between the two, a directory carries both the old entry and the
  new one — a state that never existed. Resolving there reports a tightening as "nothing
  changed", confidently, because the old grant is still open.
  Pinned by
  `tests/db/test_change_impact.py::test_resolving_inside_the_run_would_have_reported_no_change`.
* **`at_before` is the newest confirmation of the state that was replaced.** The object's own
  predecessor supplies it when there is one; for an ACL edit — where there is not — it comes
  from the entry removed in the same edit.

The answer reports the direction of **effective access** beside the direction of the **edit**,
and they disagree often: an Allow added below a Deny, or beneath a share ACL that still caps
it. The UI shows both and says the disagreement is the answer rather than a contradiction.

When the change names only one side of the pair, the response says which is missing
(`needs_a_subject`, `needs_a_resource`, `unbounded`) rather than picking a plausible
principal. An impact answer about a pair nobody asked about is a confident answer to the wrong
question.

---

## 6. The feed and the comparison are different questions

An entry added on Wednesday and removed on Thursday appears in a Tuesday-to-Friday **feed**
twice and in a Tuesday-versus-Friday **comparison** not at all. Both are right:

* the feed answers *what happened* — an incident review;
* the comparison answers *what is different* — a change-control review, "is the estate back
  the way it was before the maintenance window?".

The comparison's important output is neither list. It is `unobserved_at_from` and
`unobserved_at_to`: objects one of the two instants has no version for. They are counted and
never folded into additions or removals. An object whose later version is a **tombstone** and
whose earlier instant nothing covers is reported in neither — there is no state on either
side, and calling it a removal would claim it existed at the earlier instant.

---

## 7. Scoping, and why the window drives every query

`app/changes/scope.py` is a rule table in the same shape as `app/history/closure.py`: a scope
maps to one predicate per kind, and **a kind with no entry is excluded**. A scope that
filtered the kinds it understood and let the rest through would produce a "changes on
`\\fs01\finance`" page containing a domain group's display name.

Prefix tests use `starts_with`, never `LIKE` — PostgreSQL's default `LIKE` escape is a
backslash, the separator in every UNC path, and `_` is a wildcard and an ordinary character in
Windows folder names. Every prefix ends in a separator, so `\\fs01\finance` cannot select
`\\fs01\finance-archive`.

Key normalization is per-target and **not one of the three rules is "lower-case it"**: server
and share keys case-fold, a directory goes through `parse_unc_path`, and a principal key is
`<case-folded host>|<canonical SID>` — folding the whole thing would produce
`s-1-5-32-544`, which matches no row ADG has ever stored.

The window is the driving predicate of every query, which is why an **unscoped** query is
capped at `MAX_UNSCOPED_WINDOW` (400 days) and a **scoped** one may span any interval: the
scope bounds it instead of the clock.

---

## 8. Cost and bounds

| Bound | Value | What it protects |
| --- | --- | --- |
| `MAX_UNSCOPED_WINDOW` | 400 days | An estate-wide query cannot become a scan of the whole timeline |
| `SCAN_BUDGET` | 4,000 versions | One feed request cannot walk the timeline looking for matches. When it runs out the response says `scan_exhausted`, so a short page is not read as the end of the data |
| `SUMMARY_CEILING` | 2,000 changes | A page header cannot cost a classification of a million transitions. Beyond it the counts report themselves as a floor |
| `COMPARISON_CEILING` | 5,000 objects per side | A comparison reads a bounded set and reports `truncated` |
| `MAX_PAGE` | 500 | Matches `app/api/pagination.MAX_LIMIT` |

Queries per feed page: one for the page, one or two for predecessors, up to two for container
coverage, and one pair of ACL reconstructions per `(container, instant pair)` that has an
order-only change in it — which collapses to one or two, because a scan renumbers a whole ACL
at once.

---

## 9. Known limitations

1. **`sibling_ace_changes` is counted over the page, not the window.** A resource whose ACE
   changes landed on the previous page shows zero, and can be reported as an unexplained
   digest change. A second windowed query per resource would fix it and costs more than the
   finding is worth today.
2. **The rule table is not configurable.** A tenant that disagrees that a removed Deny is
   `high`, or that `BUILTIN\Users` is broad, has to edit the table.
3. **Correlation only pairs within the window being reported.** A removal on Tuesday and an
   addition on Friday pair when both are in the window and do not when only one is. That is
   correct rather than unfortunate — the pair is a claim that these two events are one edit —
   but it means a narrow window shows halves.
4. **The comparison reads a bounded set per side and has no cursor.** It reports `truncated`
   rather than paging.
5. **A `first_observed` change carries no window at all**, by construction. There is nothing
   earlier to bound it.
6. **Severity is editorial.** See ADR-0027's "Negative / accepted costs".
