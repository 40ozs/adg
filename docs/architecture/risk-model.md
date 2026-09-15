# The risk model

ADG reports risk as **findings**: a named rule matched a named shape at a named place, and
here are the records it matched. That sentence is the whole model, and the rest of this
document is what it takes to keep it true.

For the operator-facing reference — what each rule is worth and what you can change — see
[`docs/operations/risk-rules.md`](../operations/risk-rules.md).

---

## 1. Three properties, and what each one forbids

**Deterministic.** The same facts produce the same findings, in the same order, on any
machine. No clock, no randomness, no unsorted iteration. This forbids anything adaptive: a
rule that learned from the estate would produce a different report on Tuesday than on Monday
over identical data, and nobody could tell that from a real change.

**Explainable.** A finding names the exact rule and carries the complete records it was
computed from. This forbids evidence that is a *rendering*: `"Everyone → Modify"` is a
sentence and cannot be checked; an `AceFacts` record can.

**Reproducible.** Given only a finding's evidence, the engine can rebuild the facts, re-run
the one rule that produced it, and get the same finding back
([`reproduce`](../../backend/app/risk_engine/engine.py)). This forbids a rule reading a record
it does not cite — which is the failure that matters, because evidence that merely illustrates
a finding looks exactly like evidence that justifies it until somebody tries to check it, and
by then the estate has moved on. See [ADR-0023](../decisions/0023-risk-findings-are-reproducible-from-their-evidence.md).

---

## 2. Risk never replaces permission truth

A finding is a statement **about** the permission facts. It is computed from them, it carries
them, and it is never stored in their place or consulted instead of them.

*"Who can reach this directory"* is answered by [`app.access_engine`](effective-access.md)
over what was collected, before and after any rule runs. The rules call into that same access
check rather than keeping a second opinion about what an access mask means — a risk rule that
decided for itself what "Modify" is would eventually disagree with the access screen about the
same ACE, and one of the two would be wrong with nothing to say which.

One consequence is worth stating because it is easy to get backwards. The rules evaluate a
DACL **in stored order**, through `evaluate_acl`, rather than through the canonical
"all allows minus all denies" model. The canonical model is exact for a DACL in the order
Windows maintains and wrong for one that has been reordered — and it is wrong in the direction
that *hides* an exposure: an Allow placed ahead of a Deny actually grants, and the canonical
model would subtract the Deny and report no finding. Windows honors what is stored, so the
rules do too.

## 3. Severity and confidence are different axes

They are kept apart because the two mistakes they prevent look identical in a report.

**Severity** is a judgment about consequence, so it is **configuration**. A rule never
computes one. It reports a *band* — a fact about the finding, such as *the grant reached Full
Control* — and the installation's configuration maps that band to a severity. Lowering a
severity therefore never changes what a rule matches, and nobody is ever tempted to make a
rule stop looking in order to lower a number.

**Confidence** is a statement about ADG's own evidence, so it is **derived** and cannot be
configured. It is computed from a closed set of `FactQualifier` values the rule attaches to
its outcome, and it is always the weakest that any one qualifier allows — one projected DACL
makes the whole finding rest on a projection, and reporting it as confirmed because four other
facts were observed would be arithmetic standing in for evidence.

Severities **rank**; they do not add. There is no risk score anywhere in ADG, and this is not
an omission: a number with no unit is a number people compare between two shares.

---

## 4. A coverage gap is never a finding

Every rule could be written in a way that turns a gap in ADG's collection into a verdict, and
every such mistake points the same direction — it reports something as true because nobody
looked. The failure is silent, so it is handled rule by rule rather than by a convention.

| The gap | What it looks like | What a rule must not conclude |
| --- | --- | --- |
| A group nobody enumerated | No membership edges | That the group is empty |
| A descriptor nobody read | No `ntfs_resources` row | That the directory grants nothing |
| A share ACL nobody read | No `smb_share_aces` rows | That the share grants nothing |
| A principal nobody described | No `principals` row | That the trustee is not a user |
| A well-known SID with no row | No `principals` row | That `Everyone` is an orphaned SID |
| A boundary of unknown origin | `is_acl_boundary = true` | That permissions were changed here |

The last two are worth expanding.

**Well-known SIDs.** `S-1-1-0` means `Everyone` on every Windows computer that has ever
existed. Whether some run happened to emit a principal row for it says nothing about whether
it can be identified, so the unresolved-SID rule excludes well-known and BUILTIN SIDs from the
"no record" case. Without that exclusion the rule fires on nearly every access control list in
the estate and buries the real orphans — deleted domain accounts, which look identical except
that their SIDs mean nothing anywhere.

**Boundary reasons.** `AclBoundaryReason` has seven values and only two are statements about
permissions: `protected_dacl` and `acl_differs_from_parent`. The other five are reported as
boundaries because unknown must read as a boundary for a *scan* to be safe
([ADR-0009](../decisions/0009-boundaries-are-derived-from-a-projection.md)). Turning any of
them into a risk finding would report the collector's reach as the estate's permissions.

**Emptiness gets the strictest treatment of all.** `MembershipFacts.enumerated` is three-valued
— `True` when a run that *succeeded* and *reconciled a scope* reported the group, `False` when
a run reported it without claiming completeness, `None` when nothing said either way — and only
`True` licenses a statement about emptiness. `None` and `False` produce **nothing**: not a
lower-confidence finding, not an informational note. The action an empty-group finding invites
is removing a grant, and the group may have a hundred members that no run has collected.

---

## 5. Findings over time

Three tables, mirroring the shape [`object_versions`](history-model.md) has toward the
current-state tables and for the same reason: *is this finding open* and *when did it open,
close and come back* are different questions.

### `risk_findings` — current state

One row per `finding_key`, a SHA-256 of the rule identifier and the subject. The same shape in
the same place digests to the same key on every evaluation, which is what lets a later pass
recognize a finding it has seen rather than opening a duplicate.

**The key deliberately excludes the rule version.** Correcting a predicate should re-examine
the findings it already made: if the shape is still there the finding stays open under the new
version, and if it is not the finding resolves. A version in the key would resolve every
finding and open an identical set the same second, which reads in a report as an estate that
changed overnight when nothing changed at all.

Three instants, answering three questions:

| Column | Question |
| --- | --- |
| `first_detected_at` | When was this ever first seen? Never moves. |
| `detected_at` | When did the *current* open window begin? Reset on a reopen. |
| `last_evaluated_at` | How recently did a pass actually cover it? |

An "open since" that spanned a period the finding was demonstrably gone would be a claim an
operator would act on, which is why a reopen starts a new window and `first_detected_at` stays
put.

### `risk_finding_events` — the transitions

`opened`, `reaffirmed`, `evidence_changed`, `resolved`, `reopened`. A finding that opened in
March, resolved in June and came back in July is one row in `risk_findings` and three events
here; storing only the newest pair of timestamps would lose every cycle but the last, which is
exactly the pattern worth seeing.

A resolution carries **no evidence digest**, enforced by a check constraint: the rule stopped
matching, so there are no facts it was resolved on, and a row that appeared to carry them
would invite a reader to check a resolution against evidence for the opposite conclusion.

### `risk_evaluations` — the pass

This table exists for one reason, and it is the safety property of the whole feature.

---

## 6. A pass may only resolve what it covered

An incremental evaluation loads the facts around what one scan run changed. If it were allowed
to close every finding it did not re-match, the first partial pass would close the entire
report — and an emptied report looks like an improvement.

So the scope is recorded on the evaluation row, and `RiskFindingRepository.reconcile` applies
two tests before closing anything:

1. **The rule ran.** A rule the configuration disabled, or one an incremental pass skipped
   because nothing it depends on changed, produced no matches — and an absence of matches from
   a rule that never ran says nothing at all.
2. **The scope covers every subject key the finding names.** Not any of them: all of them.

The asymmetry in (2) points the only direction it safely can. Requiring *all* keys can leave a
finding open that should have resolved, which the next full pass corrects and which in the
meantime reports a risk that is already gone. Requiring *any* key would close findings the pass
never examined, which reports a risk as fixed when nothing was done about it. The first wastes
an afternoon; the second ends an access review with a clean report over a live exposure.

A related trap is handled in the same place: **a full load that hit a ceiling is not a full
pass.** `RiskFactsRepository.load` demotes a complete scope to the keys actually loaded as soon
as any ceiling is reached, so a bundle that ran out of room cannot resolve findings about the
directories it never loaded.

### Which rules an incremental pass runs

Each rule declares the collected kinds it reads (`RuleDefinition.depends_on`). A run that
changed nothing of a kind a rule reads cannot have changed that rule's answer, so the rule is
not re-run — and, by test (1) above, cannot resolve anything either. What a run changed is read
from `object_versions` rather than from `observations`, because those answer different
questions: an observation says a run *looked at* an object, and a version says the object's
state actually moved. A run that re-read an unchanged estate touched every object and changed
none.

---

## 7. Sensitivity is declared, never inferred

ADG cannot look at a directory and know that it holds payroll data, and it does not try. The
`broad_access_on_sensitive_resource` rule reads the configured tags and nothing else — not a
folder's name, not its share's description, not the words in a path, not how many people can
reach it. With no tags configured it produces nothing.

That silence is a *configuration state* rather than a result, which is why
`RiskConfiguration.marks_anything_sensitive` exists: a report can then say *"no resource is
marked sensitive"* instead of showing an empty section that reads as a clean bill of health.
It is the same distinction [ADR-0016](../decisions/0016-an-empty-answer-names-its-own-emptiness.md)
makes about empty access answers. See
[ADR-0024](../decisions/0024-sensitivity-is-declared-not-inferred.md).

---

## 8. Where things live

| Module | Contains |
| --- | --- |
| `app/risk_engine/severity.py` | Severity, confidence, the fact qualifiers, and the derivation |
| `app/risk_engine/facts.py` | The typed snapshot a rule is evaluated over; the scope |
| `app/risk_engine/evidence.py` | The cited records, and rebuilding facts from them |
| `app/risk_engine/catalog.py` | Identity, prose, default severities, thresholds, dependencies |
| `app/risk_engine/configuration.py` | What an installation may change; the sensitivity tags |
| `app/risk_engine/findings.py` | What a rule reports, what the engine makes of it, the key |
| `app/risk_engine/rules.py` | The eleven predicates |
| `app/risk_engine/engine.py` | Running them; reproduction |
| `app/repositories/risk.py` | Loading facts out of storage; persisting findings |
| `app/services/risk.py` | The three passes |

Everything under `app/risk_engine/` is framework-free: no FastAPI, no SQLAlchemy, no I/O, no
clock. The repository is the whole seam between the rules and PostgreSQL.
