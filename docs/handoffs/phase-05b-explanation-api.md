# Handoff — Phase 5B (`phase-05/02-explanation-api.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-05a-access-paths.md](phase-05a-access-paths.md)
**Collector contract version after this phase:** `1.3` — unchanged. No collector payload, no
schema, no migration, no change to any existing response.
**Derived-response contract introduced:** `1.0`.

## Scope completed

Phase 5A built the causality engine and put one route in front of it. This phase makes it a
**contract a frontend can build against**: stable, query-addressed, versioned, cacheable, and —
the part that carried the most design weight — able to say which kind of "no" an empty answer
is.

1. **Three endpoints.** `GET /api/v1/access/explain`, `GET /api/v1/access/paths`, and
   `GET /api/v1/groups/{identifier}/resource-impact`.
2. **A four-valued verdict** (`app.access_engine.verdict`, ADR-0016) — `granted`, `denied`,
   `no_grant`, `indeterminate` — computed once by a pure function so that no consumer has to
   re-derive the distinction, and no consumer gets it wrong.
3. **Server-side path pagination** over the same deterministic enumeration, with `complete`
   and `has_more` kept as separate facts.
4. **Caching against collected state, never a clock** (ADR-0017): a `CollectionBasis`, an
   `ETag`, `Cache-Control: private, no-cache`, and `304` on `If-None-Match`.
5. **Versioned response schemas** under `docs/contracts/v1/derived/`, generated from the
   models, with captured example payloads and `docs/contracts/derived-responses.md` as the
   normative document.
6. **141 new tests** — 103 hermetic, 37 smoke, and one rewritten guard.

## Three findings

### 1. The Phase 4C anchoring guard tested the spelling, not the property

`tests/api/test_access_bounds.py` exists to make the Cartesian product *unreachable*: every
access route must name one principal or one resource, so there is no query string that asks
for "every principal against every directory". It enforced that by requiring `{identifier}` or
`{resource}` in the **path**.

The prompt asks for `GET /api/v1/access/explain`, which is anchored by two **required query
parameters** and has no path parameter at all. The guard failed it, correctly by its own rule
and wrongly on the merits: the route names exactly one pair and cannot grow with the estate.

The query spelling is not cosmetic. A UNC path in a URL path segment is
`%5C%5CFS01%5CFinance`, and several proxies normalize or reject encoded backslashes and
repeated separators. That is a real reason a frontend wants the parameter in the query string.

**Fixed** by making the guard test the property. `anchors_of()` accepts a path parameter *or* a
required query parameter, and `required` is doing the work: an optional `?principal=` is a
route that answers about everybody when it is omitted, which is the product wearing a query
string. Because every other test in that file passes when the detector is too generous,
`TestTheAnchorDetectorIsHonest` tests the detector directly against synthetic operations —
including that an optional principal and a required *header* are both rejected. The real
document contains no unanchored route to catch it out, so it had to be caught out deliberately.

The guard also now covers `/api/v1/groups/{identifier}/resource-impact`, which is an access
answer living outside the access prefix, and `TestTheExplanationRouteIsBoundedWithoutPaging` is
parametrized over both explanation spellings rather than only Phase 5A's.

### 2. The verdict's precedence had to put `indeterminate` above `denied`

The obvious ordering is: no rights and a Deny matched → `denied`. It overstates.

A Deny is conclusive only about the rights it names *at the position it sits*. With an
unobserved membership the subject could also match an Allow that **precedes** it, and an Allow
ahead of a Deny grants under the order Windows actually evaluates (ADR-0010). Reporting
`denied` there asserts a control that may not hold — and somebody relies on that assertion.

So `indeterminate` outranks `denied`, and the denial is not lost: it stays on
`verdict.denials`. What is withheld is the claim, not the evidence.

The mirror case had to be got right too, and is the one a careless rule breaks. An unseen
*restriction* — a share ACL nobody read — can only narrow. It cannot conjure the NTFS grant
that is absent, so an empty answer under `certainty: at_most` is a real `no_grant`, not an
`indeterminate`. The rule is therefore "understating certainty", not "any finding at all", and
`test_an_unread_share_does_not_make_an_empty_ntfs_answer_indeterminate` is what holds the two
apart.

`denied` also reads `AclEvaluation.denied_by` rather than "a Deny is present". `evaluate_acl`
files a Deny that took nothing away under `superseded`, so the invariant that makes step 3
mean *cause* rather than *presence* is asserted rather than assumed
(`TestADenyThatTookNothingIsNotACause::test_the_denied_by_invariant`).

### 3. `via_nesting` was a field that would have sent an administrator to the wrong ACL

The resource-impact rows were first given `via_nesting`, computed as "every matched entry is at
token depth > 0". A smoke test against `03-nested-group-grant` returned `false` for a group
that is named on no ACL at all.

The cause: an **assumed** token SID also sits at depth 0. The share's `Everyone` entry matched
Finance-Team at depth 0, so "all matched entries are nested" was false, and the row claimed the
group was named directly on an ACL it does not appear on.

**Fixed** by comparing **keys**, not depths, and by renaming the field to the question an
administrator actually asks. `names_group` is true when some entry names this group itself, and
false when the rights arrive through a group it is nested inside *or* through a world SID.
Both of those mean the same thing operationally — there is no entry naming this group to remove
— and the old field conflated one of them with the opposite answer.

## Files and modules added or materially changed

### Added

| File | Lines | Contents |
| --- | --- | --- |
| `backend/app/access_engine/verdict.py` | 211 | `AccessOutcome`, `AccessVerdict`, `classify_access`. Pure. |
| `backend/app/domain/basis.py` | 132 | `CollectionBasis` and its digest. Pure. |
| `backend/app/repositories/basis.py` | 75 | One aggregate over `scan_runs`. |
| `backend/app/api/caching.py` | 248 | `CONTRACT_VERSION`, `CacheValidator`, `current_validator`, `BasisView`, `not_modified`. |
| `backend/app/api/groups.py` | 353 | The resource-impact router. |
| `backend/app/contracts/derived.py` | 133 | Generates the published response schemas. |
| `backend/tests/access_engine/test_verdict.py` | 312 | 21 tests |
| `backend/tests/domain/test_basis.py` | 117 | 14 tests |
| `backend/tests/api/test_caching.py` | 204 | 24 tests |
| `backend/tests/contracts/test_derived_schemas.py` | 256 | 44 tests |
| `backend/tests/db/test_explanation_api.py` | 668 | 37 smoke tests |
| `docs/contracts/derived-responses.md` | 204 | Normative. The outbound contract. |
| `docs/contracts/v1/derived/*.schema.json` | generated | Three response schemas. |
| `docs/contracts/v1/derived/examples/*.json` | captured | Three real response bodies. |
| `docs/decisions/0016-…` | 103 | ADR-0016 |
| `docs/decisions/0017-…` | 105 | ADR-0017 |

### Changed

| File | Change |
| --- | --- |
| `backend/app/api/access.py` | Two routes, `VerdictView` / `AccessExplanationResponse` / `AccessPathPageResponse`, `layer` added to `AppliedAceView`, and `_explain` / `_explanation_limits` / `_limits_view` / `_graph_view` / `_subgraph` extracted. Six renderers renamed from `_x_view` to `render_x` so a second router can use the one implementation rather than a copy. |
| `backend/app/api/__init__.py` | `groups.router` included behind `ACCESS_READ` |
| `backend/app/access_engine/__init__.py` | Re-exports the verdict module |
| `backend/app/domain/__init__.py` | Re-exports `CollectionBasis`, `EMPTY_BASIS` |
| `backend/app/repositories/__init__.py` | Re-exports `CollectionBasisRepository` |
| `backend/tests/api/test_access_bounds.py` | The anchoring guard rewritten; +9 tests (13 → 22) |
| `docs/contracts/README.md` | Inbound/outbound framing and the derived-schema index |
| `docs/contracts/v1/openapi.json` | Regenerated — three new paths |
| `docs/architecture/access-causality.md` | Forward link to the wire contract. No existing text altered |
| `docs/decisions/README.md` | ADR-0016 and ADR-0017 indexed |

**Phase 5A's route is unchanged on the wire** and now shares `_explain` with the new one, so
the two spellings cannot drift — including in how they fail.

## Important architecture decisions

1. **The empty answer names its own emptiness** (ADR-0016). Four values, one pure function,
   `indeterminate` above `denied`, and outcome kept orthogonal to certainty so that "granted,
   and an unread share ACL could narrow it" stays representable.
2. **Cached against collected state, never a clock** (ADR-0017). The validator is built from
   the state of `scan_runs`, is computed *before* the expensive work, and there is no
   `max-age` anywhere.
3. **No in-process response cache.** The 304 path is already one query, and a process-wide
   cache keyed by principal and resource is one forgotten key component away from serving one
   caller's answer to another — of a payload that names the group to join to reach a share.
   Recorded in ADR-0017 as a rejected alternative rather than left for someone to add later.
4. **The clamped limits are in the cache key, not the requested ones.** Two callers asking for
   10,000 and 1,000,000 paths get the same ceiling and the same answer, and should share a
   cache entry.
5. **Resource impact does not loop the explanation over an estate.** Phase 5A prerequisite 5.
   Each row is an *answer* with the entries that produced it; the derivation is a URL
   (`explain`) that computes that one pair. A page of bounded resolutions is not work a server
   should do speculatively.
6. **Schemas are generated, not maintained.** Same instrument as `openapi.json`, for the same
   reason: a hand-written copy of a response shape drifts, and the drift is invisible until a
   client believes the wrong one.
7. **Examples are captured, not written.** `tests/db/test_explanation_api.py` validates every
   live response against the published schema and rewrites the examples under
   `ADG_WRITE_EXAMPLES=1`, so an example cannot illustrate a response nobody sends.
8. **Derived schemas live in `v1/derived/`.** `v1/*.schema.json` is a glob the Phase 0B tests
   use to mean "every payload a collector sends"; a response schema in it was registered as a
   collector payload and checked against the observation kinds. The directory split matches the
   inbound/outbound distinction the contracts README now draws.
9. **Resource impact requires `ACCESS_READ`, not the `IDENTITIES_READ` the rest of
   `/api/v1/groups` carries.** Knowing who is in a group and knowing what that group reaches
   are different disclosures, and the more sensitive one must not inherit the weaker
   requirement.

## Schemas and contracts

**No existing contract changed.** No collector payload, no enum value removed or renamed, no
migration, and every existing response is byte-identical except for one purely additive field.

**Added:**

- Three routes, all additive.
- `AppliedAceView.layer` — additive, and appears in the existing 4B/5A responses too. A flat
  list drawn from both ACLs is ambiguous without it.
- The derived-response contract at version `1.0`, pinned as a `const` in each schema and
  carried as `schema_version` in each body.

**Versioning rules** are in `docs/contracts/derived-responses.md`: additive changes bump the
minor, breaking changes require a new major and a `v2/` directory, and the version is part of
every cache validator so a client holding a `1.0` body cannot be handed a `304` by a server
that speaks `1.1`.

## Tests run and exact results

All commands from `C:\code\adg\backend` using `.venv\Scripts\python.exe`.

| Gate | Command | Result |
| --- | --- | --- |
| Verdict | `pytest tests/access_engine/test_verdict.py -q` | **21 passed in 0.02s** |
| Collection basis | `pytest tests/domain/test_basis.py -q` | **14 passed in 0.02s** |
| Caching | `pytest tests/api/test_caching.py -q` | **24 passed in 0.02s** |
| Derived schemas | `pytest tests/contracts/test_derived_schemas.py -q` | **44 passed in 0.18s** |
| API bounds | `pytest tests/api/test_access_bounds.py -q` | **22 passed in 0.36s** (13 before; +9) |
| Endpoints | `ADG_RUN_SMOKE_TESTS=1 pytest tests/db/test_explanation_api.py -q` | **37 passed in 19.99s** |
| Hermetic suite | `pytest tests -q -m "not smoke"` | **4066 passed, 10 skipped, 441 deselected in 20.52s** |
| Full suite | `ADG_RUN_SMOKE_TESTS=1 pytest tests -q` | **4504 passed, 10 skipped, 1 xfailed in 246.44s** |
| Lint, repo-wide | `ruff check app tests` | **All checks passed** |
| Format, repo-wide | `ruff format --check app tests` | **183 files already formatted** |
| Types, repo-wide | `mypy app` | **Success: no issues found in 78 source files** |

The 1 `xfailed` is the Phase 4C pinned `resource → principals` cost defect, unchanged.

Unlike the last three phases, **repo-wide lint and types are clean**: the concurrent session's
in-flight work landed as commit `8336ecc` before this phase finished.

**141 tests are attributable to this phase** — 21 + 14 + 24 + 44 hermetic, 37 smoke, and 9
added to the rewritten bounds guard. The full-suite total also moved by the concurrent
session's Phase 6A work; the per-file figures are the reliable ones.

### The acceptance criteria, and where each is checked

| Criterion | Test |
| --- | --- |
| The frontend can render an explanation without reimplementing permission logic | `TestTheExplanationIsOneRenderableObject` — one request carries the verdict, both layers, every path, and the graph; `test_it_agrees_with_the_answer_it_explains` proves it is the same answer the plain route gives |
| "Denied", "no grant" and "insufficient data" are distinguishable | `TestTheVerdictTellsTheThreeNegativesApart` produces all three from stored transcripts, and `TestTheVocabularyAClientBranchesOn` asserts the four values are in the schema a client validates against |
| Responses include stable identifiers, not display names as relationship keys | `TestStableIdentifiersRatherThanDisplayNames`, ending with a test that follows every node and edge reference in a real captured payload |
| Contract tests pass | `tests/contracts/test_derived_schemas.py`, 44 passed |
| Server-side path pagination | `TestPagingAgreesWithTheWhole::test_the_pages_reassemble_into_the_explanation` — the pages, concatenated, equal `/explain`'s `paths` exactly |
| Caching behind stable source/run identifiers | `TestTheConditionalGet::test_collecting_again_invalidates_it` |

### Two cost claims, measured rather than asserted

`TestWhatTheCachingActuallyCosts` uses the Phase 3C `StatementLog` harness:

- a `304` issues **exactly one statement**, and it reads `scan_runs`. If the validator ever
  moves below the work, the ETag starts costing a full explanation to save a serialization —
  the opposite of the point, and the shape a later refactor would produce;
- `/explain` issues **exactly one more statement** than Phase 5A's route for the same answer.
  That one is the basis read, and it is the only statement this phase adds.

### The strongest tests are the ones this phase did not write

`TestTheVerdictTellsTheThreeNegativesApart` reads `07-deny-candidate` and
`09-broken-inheritance` out of the Phase 0B fixtures — the subject, the resource, the expected
trustee, the limiting layer, and whether access is expected — rather than restating them.
Those files were written by somebody describing an estate, before this vocabulary existed.

`09-broken-inheritance` earned two tests rather than one. Its note reads: *"Finance-RW reaches
Payroll only because it was nested into the server's BUILTIN\Administrators, not through the
parent's ACE."* Against the same object and the same ACL, Finance-RW is `granted` and Alice is
`no_grant` — which is what makes `no_grant` a finding about Alice rather than about Payroll.
The granted side also surfaces the **ownership** path: `ace_position: -1`, no `ace_key`, and
`WRITE_DAC` in its escalation rights, because the local group owns the directory. That is the
escalation route no ACL viewer shows, and it is asserted here end to end for the first time.

## Known limitations

1. **`resource-impact` reports answers, not derivations.** Deliberate (decision 5). A caller
   wanting the paths for a row must follow `explain`, one request per row. There is still no
   bulk explanation shape and this phase did not add one.
2. **The basis is estate-wide.** Any ingestion invalidates every cached derived answer,
   including ones it could not have changed. The precise alternative — per-row provenance — is
   both wrong (it is silent about rows that would have been read had they existed) and unable
   to make a 304 cheap, since it is not computable before the work.
3. **A re-applied idempotent batch moves the basis** even though it changes no row. The safe
   direction, and stated in ADR-0017 rather than hidden.
4. **`/access/paths` does not page `removal_targets`.** They are a property of the whole
   explanation rather than of a page, and they live on `/explain`. An estate that truncates at
   `max_removal_targets` has no way to ask for the rest.
5. **Paging does not widen the enumeration.** Asking for page five does not enumerate beyond
   `max_causal_paths`; `complete: false` means routes exist that no page will ever show. The
   two facts are reported separately and a client must read both.
6. **`membership` counts are bounded** by the same traversal limits as everything else, and
   are lower bounds when `complete` is false. The real blast radius is larger, never smaller.
7. **Every Phase 4C and 5A limitation is inherited.** Deny-only SIDs and privileges remain
   unmodeled; an explanation of an `at_least` answer explains a lower bound; each removal is
   still measured alone.
8. **The captured examples carry a run id and timestamps from the run that produced them.**
   They are illustrative and are only rewritten under `ADG_WRITE_EXAMPLES=1`; the schema, not
   the example, is the contract.

## Security and privilege assumptions

- **No new collection, no new privilege, nothing written anywhere.** All three routes are
  read-only over what Phases 1–3 already stored.
- **All three require `ACCESS_READ`,** declared at the include site in `app/api/__init__.py`
  as ADR-0014 requires. `tests/api/test_authorization.py` asserts that list is exhaustive
  against the OpenAPI document, so the new `/api/v1/groups/{identifier}/resource-impact` path
  could not have shipped unprotected — it passed unchanged.
- **Resource impact is deliberately *not* on `IDENTITIES_READ`.** The rest of
  `/api/v1/groups` discloses membership; this discloses what that membership reaches.
- **`Cache-Control: private`** because the payload is a reconnaissance map of one estate and
  must never sit in a shared proxy. `Vary: Authorization` for the same reason.
- **Cursors are a position, not a capability** — unchanged from Phase 4C. The basis token in
  the `ETag` and the `X-ADG-Collection-Basis` header discloses only that collection has or has
  not changed, which any authorized caller can observe by re-requesting.
- **The explanation is a sharper reconnaissance map than the answer it explains**, as Phase 5A
  recorded. `resource-impact` is sharper still: it is the blast radius of one group across an
  estate, in one paged listing. That is the product working as intended and it is why the
  capability boundary matters more here than on a plain answer.

## Migration and compatibility notes

- **No migration.** No table, column, index, schema or enum change.
- **Purely additive on the wire.** Three new routes; one new field (`AppliedAceView.layer`) on
  three existing responses; nothing removed or renamed in any published payload.
- **`docs/contracts/v1/openapi.json` was regenerated.** It is tracked as of commit `8336ecc`,
  and this phase's three paths are in it. Regenerate with `python -m app.contracts.openapi`.
- **Six renderers in `app/api/access.py` were renamed** from `_rights_view` / `_token_view` /
  `_applied_view` / `_verdict_view` / `_resource_ref` / `_share_ref` to `render_*`. Internal to
  the API layer, no wire effect; renamed rather than aliased so `app/api/groups.py` uses the
  one implementation instead of a copy that could drift.
- **`tests/api/test_access_bounds.py` changed shape**, not strictness. `ANCHORS` became
  `PATH_ANCHORS` and `QUERY_ANCHORS`, `EXPLANATION_ROUTE` became `EXPLANATION_ROUTES`. Anyone
  adding an access route must still anchor it, and now must anchor it *requiredly*.

## Prerequisites for the next prompt

**For the risk phase:**

1. **Branch on `verdict.outcome`, never on `effective.access`.** Counting `indeterminate` as
   "no access" produces a false negative about the part of the estate nobody has scanned, and
   it is the single most dangerous thing a rule in this product can do.
2. **`conclusive: false` forbids the negative conclusion.** Not a hint; a rule that fires on
   the absence of access must not fire while it is false.
3. **`no_grant` and `denied` are different findings.** A Deny is a control somebody may be
   relying on. "There is no Deny here" is not a finding; "the Deny that used to be here is
   gone" is, and needs the diffing that does not exist yet (Phase 5A limitation 7).
4. **`granted` with `certainty: at_most` is still a finding.** The NTFS layer really does
   permit it. Do not filter it out for being uncertain.
5. Phase 5A's prerequisites 1–5 all still hold, `sufficient_removals` and `complete: false`
   above all.

**For the frontend:**

6. **Use `/api/v1/access/explain`.** It is the query-addressed, versioned, cacheable spelling;
   the Phase 5A path-addressed route remains for compatibility and requires percent-encoding a
   UNC path into a URL path segment.
7. **Send the `ETag` back in `If-None-Match`** and handle `304`. Revalidation costs one query
   and the answer can never be stale. Do not add a client-side TTL on top; it would reintroduce
   exactly the staleness this design removes.
8. **Render `basis.latest_activity_at` as "as of".** An answer with `basis.is_empty: true`
   means nobody has collected anything, which composes with the Phase 6A `ViewState`
   (ADR-0015) — that one attributes an empty *list*, this one attributes an empty *answer*.
9. **Read `complete` and `page.has_more` separately.** The last page of a truncated enumeration
   has `has_more: false` and `complete: false`.
10. **`names_group: false` means there is no entry to remove.** Do not offer "remove from ACL"
    on those rows.
11. `docs/contracts/derived-responses.md` is normative and is written for this audience.

**For whoever adds the next derived endpoint:**

12. **Call `app.api.caching.current_validator`** rather than assembling a key, so every derived
    endpoint invalidates on the same event.
13. **Put every input that can change the body into `parameters`**, including the server's
    resolved defaults and the *clamped* limits. A missing component is a wrong answer served
    as a fresh one.
14. **Anchor the route requiredly**, and add it to `SCHEMAS` in `app/contracts/derived.py` so
    its schema is published and kept current.
15. **Bump `CONTRACT_VERSION`** when the shape of a derived response changes.

## Commit

*Phase 5B: the access explanation API* — hash recorded by the follow-up commit. Not pushed; no remote is configured for
this repository.

## `git status --short`

Taken after the commit.

```
 M backend/tests/contracts/test_smb_collector.py
```

The only entry, and it is the pre-existing unstaged formatting change that predates Phase 4B
and that the last four handoffs also recorded. Nothing else in the tree belongs to this phase.
