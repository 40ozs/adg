# ADR-0010: Effective access is a faithful access check over a constructed token

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 4B (`phase-04/02-effective-access-resolver.md`)
- **Deciders:** Phase 4B implementation

## Context

Phase 4A built the rights algebra and left the *selection* problem open: which ACEs apply
to a principal, in what order, and against what set of SIDs. Two things forced a decision
rather than an implementation detail.

**Windows evaluates a DACL in the order it is stored.** Phase 4A's `resolve_canonical`
implements the canonical-ACL model — accumulate every Allow, accumulate every Deny,
subtract — which is exact for a DACL in the order Windows maintains and wrong for one that
has been reordered: an Allow placed ahead of a Deny genuinely wins. Phase 4A recorded this
as its limitation 1 and required Phase 4B to close it. Canonical scenario
`07-deny-candidate` states the same requirement from the other side: *"evaluation must
honor the order as written."*

**An access token is not a group list.** An ACE naming `Authenticated Users` grants a user
access without naming them and without any membership edge existing anywhere in the
directory. A resolver that matched only collected memberships would report no access where
Windows grants Read — canonical scenario `10-smb-more-restrictive` is exactly that shape.
The opposite error is equally available: assuming a SID into a token that Windows would not
place there reports access nobody has. ADG has never observed a logon and never will; the
collectors are read-only and least-privileged (ADR-0004), and a token is a property of a
session, not of a principal.

Two further constraints were fixed before this phase. Trustee identity is host-scoped for
BUILTIN SIDs and global for everything else (ADR-0001, `referenced_principal_key`), and a
mask is compared as bits and never as a label (ADR-0005).

## Decision

**The resolver performs the Windows access check as Windows performs it, over a token ADG
constructs from a named assumption.**

1. **`app.access_engine.evaluation.evaluate_acl` walks a DACL in stored order**, computing
   what Windows computes for `MAXIMUM_ALLOWED`: `INHERIT_ONLY` entries skipped, unmatched
   trustees skipped, a Deny adding whatever is not already granted, an Allow adding whatever
   is not already denied. `resolve_canonical` is computed alongside it and reported as
   `canonical_rights`; a divergence raises `ORDER_DEPENDENT_RESULT` **per subject**, and a
   departure from canonical order raises `NON_CANONICAL_DACL` about the object. This closes
   Phase 4A's limitation 1.
2. **Owner rights are granted before any ACE is read.** A token holding the object's owner
   receives `READ_CONTROL | WRITE_DAC`, which an explicit Deny cannot remove, unless an
   `OWNER RIGHTS` (`S-1-3-4`) entry applies — that entry replaces the implicit rights and is
   matched at its real position. This closes Phase 4A's limitations 2 and 3.
3. **A NULL DACL grants every right to everybody and an empty DACL grants nothing.** Both
   are raised as conditions. This closes Phase 4A's limitation 5.
4. **`app.access_engine.subjects.build_token` constructs the token** from the subject's SID,
   its transitive memberships as observed, and one explicitly named `TokenAssumption` that
   contributes `Everyone`, `Authenticated Users` (or `ANONYMOUS LOGON`), and the access
   path's logon SID. Every contributed SID is listed on an `ASSUMED_TOKEN_SIDS` finding, and
   every token entry carries its `SidOrigin`.
5. **Session-dependent SIDs are refused rather than guessed.** An ACE naming `BATCH`,
   `SERVICE`, `REMOTE INTERACTIVE` or a similar logon-session SID that the access path does
   not imply is reported as `LOGON_TYPE_TRUSTEE` and is **not** counted as unmatched.
6. **Matching is by storage key, never by SID**, at every layer and for the owner.
7. **The access path is never inferred.** A remote resolution requires the share layer even
   when its ACL was never read, and a local one refuses it.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Keep the canonical model and treat every DACL as canonical | Under-reports access on a non-canonical ACL, which is the one case where an administrator most needs the truth. Phase 4A named it as a defect to fix, not a position to hold. |
| Normalize every DACL into canonical order before evaluating | Reports what the ACL editor would show rather than what the access check does. It would silently correct the very object that needs reporting. |
| Report both models and let the caller choose | Two masks for one question, and the label-comparison failure the rights model exists to prevent, relocated one level up. The faithful result is the answer; the canonical one is evidence. |
| Match ACEs against observed memberships only | Misses every `Everyone` and `Authenticated Users` grant — the most common ACEs on a real estate, and the ones that most often constitute the finding. |
| Add `Everyone` and `Authenticated Users` silently | Correct for a user, wrong for a well-known SID asked about directly, and unverifiable in either case. An assumption nobody can see is one nobody can check. |
| Synthesize membership edges for the well-known SIDs at ingest | Fabricates observations. ADR-0003 keeps raw observations and derived state apart, and a collector never reported those edges. |
| Model every logon type and report the union | Reports access that depends on a session nobody has established. Naming the ones that could not be decided is the smaller claim and the true one. |
| Evaluate `MAXIMUM_ALLOWED` in an ACE against the constructed token | Its value depends on the calling token at open time, which is not the constructed one. Reported as indeterminate (ADR-0005, decision 8). |

## Consequences

**Positive**

- A non-canonical DACL is reported as the finding it is, per object *and* per affected
  principal, rather than being quietly corrected or quietly mis-evaluated.
- Ownership becomes visible as the escalation path it is: an owner who appears nowhere on an
  ACL, or is explicitly denied on it, still holds `WRITE_DAC`.
- The three ACE kinds that look like grants and are not — `INHERIT_ONLY`, `CREATOR OWNER`,
  and an entry superseded by an earlier one — are each reported distinctly.
- Every assumption in an answer is enumerable, so a reviewer can check the token rather than
  trusting it.

**Negative / accepted costs**

- The token is a reconstruction. If an estate's policy excludes `Everyone` from anonymous
  sessions, or a machine's local group membership was never collected, the token is wrong in
  a way only the conditions disclose.
- The canonical result is computed on every evaluation, which is a second fold over the same
  entries. Measured as free at real ACL sizes; the ceiling is 4096 entries.
- Privileges are not modeled at all. `SeBackupPrivilege` bypasses the DACL, so an
  administrator's real reach exceeds any answer here.

**Follow-up required**

- `inherited_from` is still never populated by the collectors, so canonical-order checking
  cannot distinguish one ancestor's inherited block from another's and deliberately does not
  try.
- Conditional ACEs and central access policies remain unmodeled, unchanged from Phase 0A.

## Compliance

- `tests/access_engine/test_evaluation.py::TestOrderIsLoadBearing` — an Allow ahead of a Deny
  grants, and the divergence is reported.
- `tests/access_engine/test_evaluation.py::TestTheOwner` — an explicit Deny does not remove
  the owner's `WRITE_DAC`; an `OWNER RIGHTS` entry replaces it; a BUILTIN owner is matched on
  its host-scoped key.
- `tests/access_engine/test_evaluation.py::TestTrusteesTheCheckCannotResolve` — `CREATOR
  OWNER` grants nothing, and a logon-session trustee is reported rather than ignored.
- `tests/access_engine/test_subjects.py` — every assumed SID is listed on a finding, and an
  observation is never overridden by an assumption.
- `tests/access_engine/test_scenarios.py` — all twelve canonical transcripts resolve to their
  own `expectations` block, including the deny, nested-group, cyclic, protected-DACL and
  both layer-limited cases.
- `evaluate_acl` raises `DomainValidationError` on a mask from the wrong layer, and
  `resolve_access` raises on a remote path with no share layer.
