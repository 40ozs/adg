# Handoff — Phase 5A (`phase-05/01-access-path-engine.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-04c-effective-hardening.md](phase-04c-effective-hardening.md)
**Contract version after this phase:** `1.3` — unchanged. No collector payload, no schema, no
migration, no change to any existing response.

## Scope completed

Phase 4B/4C answer *what* a principal can do. This phase answers *why*: the membership and
ACE paths that produced the answer, what each one is actually worth, and what would change if
any one edge were removed.

1. **A typed explanation graph** (`app.access_engine.causality`) — five node kinds
   (principal, group, assumed trustee, NTFS/SMB ACE, resource) and six edge kinds (observed
   membership, assumed membership, trustee, grant, deny, ownership).
2. **Path enumeration that preserves every route.** All distinct simple chains from the
   subject to each matched ACE, bounded, deterministically ordered, cycle-safe.
3. **Three effects, not one.** A matched ACE is not automatically a cause: it can be
   `redundant` (an earlier entry had already settled every right it names) or `constrained`
   (the other layer withholds all of it).
4. **Removal analysis by measurement, not inference** — ADR-0013. Each candidate edge is
   removed and the whole access check re-run. This is what makes the phase's central
   prohibition structural rather than a rule somebody has to remember.
5. **A shared path enumerator.** `find_paths`' DFS extracted as `app.domain.simple_paths`, pure
   and synchronous, so the explanation walks a subgraph already in hand rather than going back
   to the database per trustee — and so there is one implementation, not two that can drift.
6. **One endpoint**, `GET /api/v1/access/paths/principals/{identifier}/resources/{resource}`,
   bounded without paging and reporting the limits it applied.
7. **62 new hermetic tests and 24 new smoke tests.**

## Two findings

### 1. "Redundant grant" could not be read off the evaluator — it had to be computed

The first implementation took redundancy from `AclEvaluation.superseded`. That was wrong, and
two tests written from the prompt's own vocabulary caught it immediately.

`superseded` means something narrower than it reads. In the Windows walk an Allow contributes
`mask & ~already_denied`; it is never reduced by an earlier *Allow*. So two identical Allow
entries both land in `granted_by` with a full contribution, and `superseded` holds only
entries cancelled by a Deny.

That is correct for computing a mask — a second Allow for rights already held changes no bit —
and wrong for explaining one. "This entry is why Alice has Modify" is false of the second of
two identical grants, and an explanation that reported both as causes would send an
administrator to remove an entry whose removal changes nothing.

**Fixed** by replaying the walk in `_novel_contributions`, accumulating grants and denies
separately, so `layer_rights` is what an entry settled *that nothing before it had settled*.
Read & Execute behind Modify is now redundant; reverse the two and the redundancy moves, which
is the correct behavior because causality follows the stored order exactly as the access check
does.

The owner's implicit rights seed the granted accumulator, because Windows grants them before
reading any ACE. An ACE handing an owner the rights ownership already confers is therefore
redundant — and that is very often the entry somebody added believing it was what granted the
access.

### 2. Ownership is a cause with no ACE, and would have been reported as uncaused

An owner holds `READ_CONTROL` and `WRITE_DAC` whatever the DACL says. Phase 4B already models
this and the API already surfaces it as `owner_rights`, calling `WRITE_DAC` there "an
escalation path no ACE shows".

An explanation built only from matched ACEs would list no path at all for those rights. For an
owner explicitly denied everything, the answer is `access: true` with **zero** explaining
paths — the one output a causality engine must not produce.

**Fixed** by making ownership a first-class path: chain `(subject,)`, one `ownership` edge,
`ace_position = -1`. It is deliberately **not** a removal target, because changing an owner is
not the deletion of an edge and has a different blast radius.
`TestOwnership::test_an_owner_denied_everything_still_has_a_path` pins it.

## Files and modules added or materially changed

### Added

| File | Lines | Contents |
| --- | --- | --- |
| `backend/app/access_engine/causality.py` | 1,229 | The explanation graph, path enumeration, effect classification, removal measurement |
| `backend/tests/access_engine/test_causality.py` | 822 | 49 tests, organized by the claim each defends |
| `backend/tests/db/test_access_paths_api.py` | 375 | 24 smoke tests through HTTP against PostgreSQL |
| `docs/architecture/access-causality.md` | 241 | The causality semantics specification |
| `docs/decisions/0013-causality-is-measured-not-inferred.md` | 121 | ADR-0013 |

### Changed

| File | Change |
| --- | --- |
| `backend/app/domain/graph.py` | `simple_paths` + `PathEnumeration` extracted from `find_paths`, which now calls it. Behavior-preserving |
| `backend/app/domain/__init__.py` | Re-exports the two new names |
| `backend/app/access_engine/__init__.py` | Re-exports the causality module |
| `backend/app/services/graph.py` | `EffectiveMembership.edges` — the traversed subgraph, so alternate chains need no second traversal |
| `backend/app/services/access.py` | `explain_access()` and `ResolvedExplanation`; `effective_access` and it now share one private `_resolve_one`, so an explanation can never be derived from a differently-resolved answer |
| `backend/app/api/access.py` | The new route and its view models |
| `backend/tests/domain/test_graph.py` | +9 tests for `simple_paths`, including that it agrees with `find_paths` on the same graph |
| `backend/tests/api/test_access_bounds.py` | +4 tests; the new route added to `SINGULAR_ROUTES` with a guard that keeps the exemption honest |
| `docs/architecture/effective-access.md` | Forward link to the causality specification. No existing text altered |
| `docs/decisions/README.md` | ADR-0013 indexed |

**No Phase 0A/0B contract was modified**, and no existing response model changed. The only
API surface added is one route.

## Important architecture decisions

1. **Removal is measured by re-running the access check** (ADR-0013). Inference is wrong in
   two directions that no care in the rules fixes: removing an Allow can uncover a redundant
   Allow behind it, and removing a membership can take a Deny with it and *widen* access.
   Re-evaluation gets both right, and reports nothing removed whenever an alternate path
   survives — so the phase's prohibition is a property of the construction rather than a rule.
2. **`relation` and `effect` are orthogonal.** The prompt names four things to distinguish;
   a single five-valued enum would have made "a deny that does not bite" unrepresentable.
   Structural (grant/deny) and consequential (contributes/redundant/constrained) are separate
   fields, and the four categories fall out of their product.
3. **An ACE node is identified by its position, not its trustee.** Two entries naming one
   group are two causes with two remediations, and the stored order decides which settles a
   right (ADR-0010). Collapsing by trustee would make an explanation disagree with the access
   check printed beside it.
4. **`assumed_trustee` is its own node kind.** `Everyone` looks exactly like a group on an ACL
   and cannot be treated like one. Modelling it as a group would let the removal analysis
   propose a remediation that does not exist.
5. **The enumerator was extracted rather than rewritten.** A second DFS would have had its own
   ordering and its own cycle handling, and the day they diverged the explanation would stop
   matching the membership answer next to it. `find_paths` now calls `simple_paths`; a test
   asserts they agree on the same graph.
6. **The engine refuses to explain an answer it was not given the inputs for.** `explain_access`
   validates that the resource and share keys match the answer's, because a derivation built
   from a different ACL is plausible and wholly fictional.
7. **The explanation endpoint does not page, and is bounded instead.** It answers about one
   pair, so there is no population to slice; what grows is the graph. Capped in the schema, and
   `tests/api/test_access_bounds.py` holds the exemption to that standard rather than letting
   it be a hole in the Phase 4C guard.
8. **`max_paths` and `max_depth` are reused, not redeclared.** The traversal limits already
   mean, for the membership walk, what the explanation needs for one trustee's chains. A second
   query parameter under the same name could disagree with the first — the first draft did
   exactly that, and the bounds test caught it.

## Schemas and contracts

**Nothing existing changed.** No enum value added, no field added to an existing model, no
migration, no collector payload.

**One new route**, additive: `GET /api/v1/access/paths/principals/{identifier}/resources/{resource}`.
Its `effective` block is the identical object the existing effective-access route returns —
asserted equal in `TestItAgreesWithTheAnswerItExplains`, so the two can never drift.

`EffectiveMembership` gained an `edges` field. It is a service-layer type, not a wire contract,
and no response renders it.

`SubjectToken`, `EffectiveAccess`, `AccessCondition` and every Phase 4 type are untouched.

## Tests run and exact results

All commands from `C:\code\adg\backend` using `.venv\Scripts\python.exe`.

| Gate | Command | Result |
| --- | --- | --- |
| Causality engine | `pytest tests/access_engine/test_causality.py -q` | **49 passed in 0.04s** |
| Graph domain | `pytest tests/domain/test_graph.py -q` | **52 passed in 0.07s** (43 before; +9) |
| API bounds | `pytest tests/api/test_access_bounds.py -q` | **13 passed in 0.32s** (9 before; +4) |
| Explanation endpoint | `ADG_RUN_SMOKE_TESTS=1 pytest tests/db/test_access_paths_api.py -q` | **24 passed in 11.36s** |
| Hermetic suite | `pytest tests -q -m "not smoke"` | **3951 passed, 10 skipped, 367 deselected in 18.93s** |
| Full suite | `ADG_RUN_SMOKE_TESTS=1 pytest tests -q` | **4317 passed, 10 skipped, 1 xfailed in 206.95s** |
| Lint (this phase's files) | `ruff check`, `ruff format --check`, `mypy` on the 11 files touched | **All checks passed; 11 files already formatted; no mypy issues** |

The 1 `xfailed` is the Phase 4C pinned `resource → principals` cost defect, unchanged.

**62 hermetic tests and 24 smoke tests are attributable to this phase.** The suite totals moved
by more than that because a second session was working in this tree concurrently — see the
note below.

### The acceptance criteria, and where each is checked

| Criterion | Test |
| --- | --- |
| `Alice → Group A → Group B → ACE → Resource` returned deterministically | `TestTheChainIsReturned`, `test_paths_are_ordered_deterministically`, and `test_two_requests_return_identical_output` through HTTP |
| Multiple paths preserved, not collapsed | `TestMultiplePathsArePreserved`; end to end against the canonical estate in `TestTheCanonicalMultiplePathsScenario` |
| Contributing distinguished from constrained by the layer intersection | `TestTheOtherLayerConstrains`, `test_a_share_limited_estate_reports_a_constrained_ntfs_path` |
| Never falsely promises a removal revokes access | `TestRemovalNeverOverstates` (7 tests) and `test_no_single_removal_revokes_access` |

### The strongest test is one this phase did not write

`04-multiple-membership-paths` is a canonical Phase 0B scenario. Its description reads: *"An
explanation must show every path, and removing one must not be reported as removing access."*
It carries an `expected_paths` list of three chains, written long before this engine existed by
somebody describing the estate rather than the implementation.

`TestTheCanonicalMultiplePathsScenario` reads that list **out of the fixture** and compares,
rather than restating it in the test where it could be quietly edited to match whatever the
code does. All three chains come back, the count matches `distinct_paths_expected`, and no
single removal is reported as sufficient.

### The cost claim is measured, not asserted

`AccessService.explain_access` claims it costs exactly what `effective_access` costs.
`TestExplainingCostsNoExtraQuery` measures it with the `StatementLog` harness Phase 3C
established: the explanation issues the same statement count as the plain answer, and measuring
64 removal targets issues the same count as measuring one.

## Known limitations

1. **Each removal is measured alone.** "Which two edges together would revoke this" is a set
   cover over a bounded graph and is not attempted. Where no single edge suffices,
   `sufficient_removals` is empty and says so rather than guessing at a pair. On a real estate
   this is the common case.
2. **Only two operations are modeled**: deleting a membership edge and deleting an ACE.
   Changing an owner, breaking inheritance, and narrowing an ACE's mask are not — each is a
   different operation with a different blast radius.
3. **No bulk shape.** One principal against one resource. A rule that needs explanations across
   an estate wants a query shape this phase did not add, for the reason Phase 4C finding 3
   gives.
4. **Paths are not ranked.** Every contributing path is reported equally; deciding which
   matters needs a policy that belongs to the risk phase.
5. **`edges_not_supplied`.** When a trustee reached the token through membership and no supplied
   edge explains how, the single chain the traversal recorded is reported and the gap is named.
   This is reachable through the service only if the traversal and the ACL disagree, but it is
   the normal state for a direct `explain_access` call made without `edges`.
6. **Every limitation of the answer is inherited.** An explanation of an `at_least` answer
   explains a lower bound. Deny-only SIDs and privileges remain unmodeled — Phase 4C
   limitations 3 and 4 — so a path set for a member of a local administrators group is still
   over-reported.
7. **The explanation is not persisted.** It is recomputed per request. Nothing stores a path
   set, so nothing can diff two of them over time; the deterministic ordering exists so that
   whoever wants that can.

## Security and privilege assumptions

- **No new collection, no new privilege, nothing written anywhere.** The engine is pure and
  reads only what Phase 1–3 already stored.
- **The endpoint is read-only**, and it sits behind the `ACCESS_READ` capability at its
  router's include site, inheriting whatever the concurrent authorization work applies to the
  access router. It required no change of its own to do so.
- **Its output is a sharper reconnaissance map than the answer it explains.** The existing
  endpoint says a principal has Modify; this one names the group to join and the ACE to edit to
  get it. That is the product working as intended, and it is a reason the authorization
  boundary matters more here than on the plain answer.
- **Removal analysis changes nothing.** It computes what *would* happen; ADG remains read-only
  by default, and nothing in this phase can write to a directory or a descriptor.
- **The engine errs toward not narrowing**, consistent with ADR-0011: a truncated explanation
  reports `complete: false`, and a removal whose effect cannot be established reports no rights
  removed rather than a revocation.

## Migration and compatibility notes

- **No migration.** No table, column, index, schema or enum change.
- **Purely additive on the wire.** One new route; every existing response is byte-identical.
- **`find_paths` behavior is unchanged** by the `simple_paths` extraction — same ordering, same
  truncation, same cycle handling. The 1,979 pre-existing graph tests passed unchanged
  immediately after the refactor, and a new test asserts the two agree on the same graph.
- **`EffectiveMembership` gained a required field.** It is constructed in exactly one place
  (`GraphService._expand`) and is not a wire contract, but an external caller constructing one
  positionally would need updating.

## A concurrent session was working in this tree

Stated because it affects what the numbers above mean and what a reviewer will see.

Throughout this phase a second session was building the authentication boundary, search, and
frontend work in the same working tree — `app/auth/`, `app/api/auth.py`, `app/api/collection.py`,
`app/api/search.py`, `app/domain/search.py`, the OpenAPI snapshot, and the frontend. That work
is **not** part of this phase and is not staged in this phase's commit.

Consequences worth knowing:

1. **Suite totals include their tests.** The hermetic suite read 3,731 at the end of Phase 4C
   and 3,951 now; only 62 of that difference is this phase. The per-file figures in the test
   table are the reliable ones.
2. **Repo-wide lint is not currently clean**, and none of it is this phase: three `E501`s in
   `tests/api/test_authorization.py` and `tests/auth/test_roles.py`, and five files needing
   `ruff format`, all theirs and all in flight. Every file this phase touched passes `ruff
   check`, `ruff format --check` and `mypy` — verified against the explicit file list.
   `scripts\backend-lint.ps1` will fail repo-wide until they finish.
3. **`mypy` reports one error in `app/auth/dependencies.py`** (`no-any-return`), reached by
   following imports. Theirs.
4. **`app/main.py` and `app/api/__init__.py` were transiently unimportable twice** while they
   refactored `api_router` into `build_api_router`. Both recovered; no action needed.
5. **`backend/tests/contracts/test_smb_collector.py`** remains an unstaged formatting change
   that predates Phase 4B, exactly as the last three handoffs left it.
6. **The new route appears in their `docs/contracts/v1/openapi.json` snapshot.** The snapshot
   test passes as of the full run above. If they regenerate from a tree without this commit it
   will need regenerating again.

## Prerequisites for the next prompt

**For Phase 5B (risk):**

1. **`sufficient_removals` being empty does not mean "nothing can be done".** It means no
   *single* edge suffices, which is the common case on a real estate. A rule that reads it as
   "unfixable" will mislabel ordinary findings.
2. **Branch on `effect`, not on the presence of a path.** A `redundant` or `constrained` path
   is an ACL-hygiene finding, not an access finding. "Full Control on payroll" that is
   `constrained` by a read-only share is a different severity from one that contributes.
3. **`complete: false` forbids a negative conclusion.** With truncation, the absence of a path
   is not evidence there is none, and no removal may be believed sufficient.
4. **Ownership paths carry `ace_position = -1` and no `ace_key`.** A rule that joins paths to
   ACE rows must not assume every path has one; ownership is exactly the escalation case worth
   flagging.
5. **Do not loop the explanation over an estate.** It costs one bounded resolution per pair,
   which is fine for one and not for a hundred thousand. The bulk shape does not exist yet;
   add it deliberately, as Phase 4C prerequisite 3 already says for the answer itself.

**For Phase 6 (UI):**

6. **Render multiple paths as multiple paths.** The entire value is that there are two routes;
   a UI that shows the shortest one reintroduces the failure this engine exists to prevent.
7. **`removal_targets` where `changes_nothing` is true must not be offered as a fix**, and the
   reason — `alternate_paths` — is what the user needs to see instead.
8. **`rights_added` non-empty is a warning, not a fix**: that removal widens access.
9. `graph.nodes` and `graph.edges` are deduplicated and id-addressed, so the response is
   directly renderable as a diagram; `paths[].nodes` and `paths[].edges` are id references into
   them.

**For whoever extends the engine:**

10. **Any new path source must appear in the removal analysis or be explicitly excluded**, with
    the reason. Ownership is the worked example: it is a path and deliberately not a target.
11. **Do not add an inference shortcut to removal analysis**, however obvious a case looks.
    ADR-0013 records both failure modes and the tests that catch them.

## Commit

`153aff4` — *Phase 5A: the access path and causality engine*. Not pushed; no remote is
configured for this repository.

## `git status --short`

Taken after the commit, and filtered to this phase's own paths — the working tree also holds a
second session's uncommitted authentication, search, and frontend work, which this phase did
not stage and does not describe.

```
 M backend/tests/contracts/test_smb_collector.py
```

The full `git status --short` is longer and lists the second session's in-flight
authentication, search and frontend files; none of them belong to this phase and none were
staged. The line above is the only entry among this phase's own paths, and it is the
pre-existing formatting change the last three handoffs also recorded.
