# ADR-0016: An empty access answer names which kind of emptiness it is

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 5B (`phase-05/02-explanation-api.md`)
- **Deciders:** Phase 5B implementation

## Context

Since Phase 4B an effective-access answer has carried `access: bool` alongside `certainty`
and a list of findings, and `EffectiveAccess.has_access` has carried a docstring warning that
`False` means two different things depending on the certainty. ADR-0011 made that information
available. It did not make it hard to ignore.

Three states arrive at a consumer as the same `access: false`:

1. **A Deny withheld the rights.** Somebody made a decision, it is on an ACL, and it may be a
   control the organization is relying on.
2. **Nothing granted anything.** No entry on either ACL names this principal. There is no
   decision here — only the absence of one.
3. **Nothing was established, because the inputs were incomplete.** A descriptor no run has
   read, a group nobody enumerated, a truncated traversal. The question was not answerable.

Distinguishing them required a consumer to read two fields, know the certainty vocabulary,
know which conditions point in which direction, and apply the precedence correctly. Phase 5B
exists to hand this to a browser. A browser will render a grey dash.

The failure is asymmetric and it is the one this product is for. Reporting (3) as (2) is a
false negative about the part of the estate nobody has scanned — an auditor closes a finding
that was never opened. Reporting (1) as (2) sends somebody to add a permission that is
already explicitly denied, or to remove a Deny they did not know existed.

ADR-0015 takes this position for an empty **list**, computed from run records and rendered by
the frontend. This is the same position for an empty **answer**, computed from that answer's
own inputs.

## Decision

**Every derived access answer carries a four-valued outcome, computed once in the engine, and
a consumer branches on it rather than on a boolean.**

1. `app.access_engine.verdict.AccessOutcome` is a closed vocabulary — `granted`, `denied`,
   `no_grant`, `indeterminate` — appearing in API responses. A rename is a contract change.
2. `classify_access` is a pure function of one `EffectiveAccess`. No database, no request, no
   second resolution. The precedence is fixed:
   - rights survive → `granted`, whatever the certainty;
   - otherwise, the certainty may understate (`at_least` or `uncertain`) → `indeterminate`;
   - otherwise, a Deny entry withheld something → `denied`;
   - otherwise → `no_grant`.
3. **`indeterminate` outranks `denied`.** A Deny is conclusive only about the rights it names
   at the position it sits: an unobserved membership could put the subject on an Allow that
   *precedes* it, which grants under the order Windows evaluates (ADR-0010). The denial is
   still reported on `verdict.denials`; what is withheld is the claim.
4. **`verdict.conclusive` is false only for `indeterminate`, and while it is false no negative
   conclusion is supported by the response.**
5. **Outcome and certainty stay orthogonal and both are rendered.** The outcome is the verdict
   on the evidence; the certainty is the direction the evidence can be wrong in.
6. `denied` is read from `AclEvaluation.denied_by`, which holds only entries that took
   something away — not "a Deny is present on the ACL". Same distinction ADR-0013 draws
   between a matched ACE and a cause.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Leave it to consumers; the fields are all there | They are, and they were, since Phase 4B. Every consumer would have to re-derive the precedence correctly, and the cost of one getting it wrong is a false negative about an unscanned estate. |
| One enum folding certainty in (`granted`, `probably_granted`, …) | Makes "granted, and an unread share ACL could narrow it" unrepresentable. That is a real, common, actionable state — the NTFS layer genuinely permits it — and it is the one a combined enum would have to drop. |
| `denied` outranking `indeterminate` | Overstates. With an unobserved membership the subject could also match an Allow ahead of the Deny, and the answer would assert a control that does not hold. |
| A numeric confidence score | Not computable from what ADG knows, and it invites thresholds. ADR-0011 already rejected this for certainty; the same reasoning applies. |

## Consequences

**Positive**

- "Nobody can reach this directory" and "nobody has scanned this directory" are different
  values in the response, and a client gets the distinction right by doing nothing.
- `denied` and `no_grant` are different remediations and are now distinguishable, with the
  responsible entries attached.
- The rule lives in one pure function with its own tests, so it can be changed in one place
  and cannot drift between endpoints.

**Negative / accepted costs**

- A fifth state is now conceivable (a Deny *and* an understating gap) and is deliberately
  reported as `indeterminate` with the denial attached, rather than as a fifth value. A
  consumer wanting that combination reads `verdict.denials`.
- `indeterminate` will be common on a partially collected estate. That is the honest answer,
  and it will make coverage gaps visible in a way a boolean did not.

**Follow-up required**

- The risk phase must branch on `outcome` and must not count `indeterminate` as "no access".
- Any new derived response must carry a verdict; `tests/contracts/test_derived_schemas.py`
  asserts this against the published schemas.

## Compliance

`backend/tests/access_engine/test_verdict.py` holds the precedence, including that an
understating certainty outranks a Deny and that an unseen *restriction* does not make an empty
answer indeterminate. `backend/tests/db/test_explanation_api.py` produces all four outcomes
from stored transcripts. `backend/tests/contracts/test_derived_schemas.py` asserts the four
values are published in the schema a client validates against — the distinction has to exist
in the document the client reads, not only in a docstring on the server.
