# Handoff — Phase 1C (`phase-01/03-ad-validation.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-01b-ad-graph.md](phase-01b-ad-graph.md)
**Landed concurrently in another session:** `bc9630e` "Phase 2B: SMB persistence and
resource query APIs", committed while this phase was in progress. None of that is this
phase's work; see "Migration and compatibility notes".

## Scope completed

Phase 1 was hardened by attacking it rather than by reading it. Six defects were found by
running something, and all six are fixed with a regression test that fails without the fix.

1. **An adversarial fixture corpus** — eleven scan-run transcripts under
   `backend/tests/fixtures/ad_graph/`, covering every hazard the prompt named: 24 levels of
   nesting, two independent cycles, duplicate names across distinct SIDs, a renamed
   principal keeping its SID, deleted and unresolved SIDs, foreign security principals,
   disabled users, an empty group, a 512-member group, four independent membership paths,
   and one BUILTIN SID meaning three different groups. They are **generated** through the
   contract models and compared byte for byte against the committed JSON, so the generator
   and the artifact cannot drift.
2. **Property and invariant tests** over 40 seeded graphs — including a **reference
   implementation** the bounded traversal is checked against, which is what caught the
   silent-truncation defect.
3. **Six defects fixed**, the most serious being a traversal that could report a truncated
   answer as `complete`.
4. **Benchmarks** over 310,001 principals and 400,048 edges, with `EXPLAIN (ANALYZE,
   BUFFERS)` captured for every query the traversal issues, and the measured conclusion
   that the database is not the bottleneck.
5. **Collector-output validation tooling** — `scripts\validate-collector-output.ps1` over
   `app/validation/`, which checks what the API would check and then checks the identity
   graph the observations describe.
6. **`docs/architecture/ad-graph-validation.md`** — measured behavior, practical limits,
   the finding catalogue, and the one identity hazard this phase deliberately did not fix.

**No SMB/NTFS scope was added.** Nothing in this phase reads a target system.

## Files and modules added or materially changed

### Fixes (six defects; see "Tests run" for how each was found)

| File | Change |
| --- | --- |
| `backend/app/domain/graph.py` | The depth-boundary probe now records `max_edges` truncation when the edge budget stops it, instead of dropping the edge silently. |
| `backend/app/repositories/membership.py` | New `EDGE_FETCH_CEILING = MAX_EDGES_CEILING + 1` so the probe row survives at the largest budget; `edge_fetch_limit` exposed; `resolve()` now tries the host-scoped key first so `?host=` actually scopes. |
| `backend/app/contracts/v1/common.py` | `normalize_host` enforces the published `hostName` bounds — 255 characters, no control characters — with messages naming `host_key`. New `MAX_HOST_NAME_LENGTH`. |
| `backend/app/services/graph.py` | `split_key` splits at the last separator, not the first. |
| `backend/app/contracts/v1/envelopes.py` | Duplicate `source_key` detection is one `Counter` pass instead of `list.count` per element. |
| `backend/app/repositories/__init__.py` | Exports `EDGE_FETCH_CEILING`. Also repairs one pre-existing mis-encoded em-dash in the module docstring, which is why the diff shows a changed line nobody edited. |

### New: collector-output validation

| Module | Contents |
| --- | --- |
| `backend/app/validation/collector_output.py` | **New.** Envelope checks through the real contract models, plus graph checks: cycles, unscoped well-known groups, one SID with several kinds, member-kind disagreement, undescribed endpoints, duplicate names, and graph shapes that will hit the default traversal limits. 29 finding codes, each with a remedy. |
| `backend/app/validation/__main__.py` | **New.** `python -m app.validation <paths>`; `--json`, `--strict`, `--quiet`. Exit 0/1/2. |
| `scripts/validate-collector-output.ps1` | **New.** Windows wrapper; defaults to validating the committed fixtures. |

### New: fixtures, tests, benchmarks

| Path | Contents |
| --- | --- |
| `backend/tests/fixtures/ad_graph/*.json` | **New.** 12 adversarial transcripts (11 builders; `a10` has a two-host variant). |
| `backend/tests/fixtures/build_ad_graph.py` | **New.** The generator, with `--check`. |
| `backend/tests/fixtures/__init__.py` | Changed: `AD_GRAPH_DIR`, `ad_graph_names`, `load_ad_graph`, `load_ad_graph_raw`, `load_all_ad_graph`. |
| `backend/tests/support/graph.py` | Changed: `InMemoryMembershipRepository` and `repository_from_fixture`, so the service layer's filters are testable without a database. Rows are built through `plan_batch`, so the keys are ingestion's keys. |
| `backend/tests/contracts/test_ad_graph_fixtures.py` | **New (160).** Schema validity, transcript coherence, generator parity, and a check that every key named in an `expectations` block exists. |
| `backend/tests/contracts/test_published_constraints.py` | **New (59).** The models must enforce what the published schemas advertise — tested behaviorally, not by diffing JSON. |
| `backend/tests/domain/test_graph_adversarial.py` | **New (49).** Every fixture's `expectations` asserted against `GraphService`. |
| `backend/tests/domain/test_graph_properties.py` | **New (1,872 passing, 8 skipped).** Seeded graphs, reference implementations, eight invariant families. |
| `backend/tests/domain/test_key_scoping.py` | **New (17).** Storage keys and `split_key`. |
| `backend/tests/api/test_traversal_pairing.py` | **New (14).** The repository ceiling and the traversal budget must stay paired. |
| `backend/tests/db/test_graph_adversarial.py` | **New (40, smoke).** The same fixtures through ingestion and the query endpoints. |
| `backend/tests/validation/test_collector_output.py` | **New (66).** Both halves of the validator, plus the whole committed corpus. |
| `backend/tests/benchmarks/graph_benchmark.py` | **New.** In-memory and PostgreSQL suites, plans captured. |
| `backend/tests/benchmarks/test_benchmark_harness.py` | **New (14).** The harness must keep working; no timing is asserted. |
| `scripts/graph-benchmark.ps1` | **New.** Runs the benchmark; the database suite uses its own `<database>_bench`. |

### Documentation

`docs/architecture/ad-graph-validation.md` (new), `docs/architecture/membership-graph.md`
(cross-reference), `README.md` (the two new scripts, and a pointer to the validation note).

## Important architecture decisions

1. **Adversarial fixtures are generated, not written.** A transcript with 512 members
   cannot be reviewed by reading it. `build_ad_graph.py` builds each one through the
   contract models — every `source_key` derived by `app.contracts.v1.keys` — so a fixture
   is valid by construction, and a test rebuilds and compares byte for byte so the two
   cannot drift. The canonical `scenarios/` set is untouched: it is a fixed point other
   phases assert against.
2. **They are a separate corpus, not more scenarios.** `scenarios/` pins access semantics
   and spans all seven observation kinds; `ad_graph/` pins identity-graph correctness and
   carries only the two AD kinds, so it replays through the ingestion endpoints verbatim
   with no reduction step.
3. **Property tests are checked against a reference implementation.** Asserting that a
   traversal is self-consistent proves little. Comparing every bounded answer to an
   unbounded breadth-first search, and every path enumeration to an exhaustive recursive
   search, is what turns "complete" into a claim that can fail — and it is what caught the
   defect below.
4. **The benchmark asserts nothing.** A performance threshold that passes on one machine
   and fails on another teaches people to ignore the suite. The numbers are recorded in
   `ad-graph-validation.md` with the machine and dataset that produced them, and a small
   harness test keeps the benchmark from silently ceasing to run.
5. **The validator grades findings so it can be a gate.** `ERROR` means the collector is
   wrong and the API would reject it; `WARNING` means ADG would accept it and then hold
   something an auditor must be told about — usually the directory is wrong, not the
   collector; `INFO` is worth knowing at scale. `--strict` fails on warnings.
6. **The validator reads the storable kinds from `app.ingestion.plan.SUPPORTED_KINDS`.**
   That set grows phase by phase — it grew during this one — and a hardcoded copy would
   start telling collector authors to split batches the API had already learned to accept.
7. **The BUILTIN identity hazard was documented and detected, not fixed.** Fixing it
   changes `Principal.identity_key` and therefore every stored key and every collector's
   `source_key` derivation. That is a versioned contract migration with a data migration
   behind it, which this phase is explicitly not the place for. See "Known limitations".

## Schemas and contracts introduced or changed

**No contract change and no schema change.** `docs/contracts/v1/` is untouched and no
migration was added by this phase.

One behavior at the contract boundary became **stricter, in the direction the published
schema already specified**: `host_key`, `server_name`, `share_name`, `name` and
`collector_host` now reject names longer than 255 characters or containing
`U+0000`–`U+001F`. `common.schema.json#/$defs/hostName` has forbidden both since Phase 0B;
only the model had not been enforcing them. A collector that follows the published schema
is unaffected. `normalize_host` remains one notch stricter than the published pattern in
rejecting a whitespace-only name, which it already did.

One API behavior changed: `GET /principals/{sid}?host=` now returns the host-scoped
principal when one exists, instead of an unscoped principal that merely shares the SID.
This is the documented meaning of the parameter; the previous behavior made it a no-op in
the one case it exists for.

## Tests run and exact results

Run from `backend\`, against an isolated database (see "Known limitations" 7).

| Command | Result |
| --- | --- |
| `python -m pytest -q -m "not smoke"` | **3,206 passed, 9 skipped, 192 deselected** in 13.79s |
| `ADG_RUN_SMOKE_TESTS=1 python -m pytest -q` | **3,398 passed, 9 skipped** in 81.55s |
| `python -m pytest tests/db -q` (smoke enabled) | **190 passed** in 82.27s |
| `python -m ruff check .` | **All checks passed** |
| `python -m ruff format --check .` | **111 files already formatted** |
| `python -m mypy app tests` (strict) | **Success: no issues found in 110 source files** |
| `python -m alembic check` | **No new upgrade operations detected** |
| `python -m tests.fixtures.build_ad_graph --check` | **In sync** |
| `python -m app.validation tests/fixtures/ad_graph --strict` | 12 documents, 667 observations, **0 errors, 4 warnings, 6 informational** — the warnings are the hazards the fixtures deliberately carry |
| `.\scripts\graph-benchmark.ps1 -Scale large -Database` | Completed; numbers in `ad-graph-validation.md` §6 |
| Relative Markdown link check, whole repository | **0 broken links** |

Test counts added by this phase: 160 + 59 + 49 + 1,872 + 17 + 14 + 40 + 66 + 14 = **2,291** passing (plus 8 skipped).
The property suite is the bulk of that: 40 seeds × 8 invariant families × several limit
combinations, each walking every node of a generated graph.

### Six defects, found by running something

1. **A traversal could report a truncated answer as `complete`.** `expand()` spends one
   extra batched lookup at the depth boundary to tell an exhausted frontier from a real
   cut-off. If the edge budget ran out *during that probe*, the edge was dropped silently —
   and when every boundary edge closed back into an already-visited node, which is what a
   cycle looks like, nothing recorded any truncation at all. The answer called itself
   complete while the cycle analysis had run on an incomplete edge set. Found by the
   completeness property once the depth sweep was added alongside the edge-budget sweep; a
   sweep over edge budgets alone never reaches that code path, which is why the two limits
   are now varied together. **This is the failure this system exists to prevent**: a short
   membership list presented as the whole one.
2. **The repository could out-truncate the traversal without saying so.** Callers pair it
   with `max_edges + 1` so the traversal is offered the edge that exceeds its budget; the
   repository clamped that back to `MAX_EDGES_CEILING`, removing the probe at exactly the
   largest budget a caller can request. Found by writing the pairing down as a test.
3. **`?host=` did not scope the lookup.** `GET /principals/S-1-5-32-544?host=FS10` returned
   the *domain's* BUILTIN group, because the bare SID matched an exact storage key before
   the host was considered. Found by the `a10` fixture through the database suite.
4. **Host names were neither bounded nor printable.** `normalize_host` checked neither the
   `maxLength: 255` nor the control-character exclusion the published schema has declared
   since Phase 0B, so `host_key` — half of every local group's storage key — could be 400
   characters or contain a newline, and was caught downstream only as an oversized
   `source_key`: a true statement about the wrong field. Found by auditing the models
   against the published schemas.
5. **`split_key` split at the first separator.** A SID never contains `|`; a host name may.
   `odd|host|S-1-5-32-544` was reported as host `odd` with a "SID" of `host|S-1-5-32-544` —
   mislabelling precisely the nodes ADG knows least about, where no stored record would
   contradict it.
6. **Batch validation was quadratic on the ingestion hot path.** `list.count` per element
   over up to 1,000 `source_key`s, on every POST. One `Counter` pass now.

### Measured, in one line each

- **One breadth-first level is one query, however wide.** A 100,000-member group costs two
  provider calls, not 100,001.
- **Both traversal indexes are used in both directions, with no sequential scan.** Adding
  `edge_key` to `ix_membership_edges_group` would remove an `Incremental Sort` measured at
  32 groups of 41 kB — declined, with the measurement.
- **The database is not the bottleneck.** A 500-row page takes 51 ms of which the query is
  0.25 ms; a 100,000-member recursive answer takes 4.75 s of which the SQL is milliseconds.
- **A fixed ~42 ms stall appears once a request exceeds ~8 KiB** on this stack — it tracks
  request bytes, not key count. Recorded, not designed around: it is one machine's loopback
  and shrinking the chunk size would trade 20 stalled requests for 1,000 fast ones.
- **`max_nodes` is a memory budget.** 754 bytes per node for a wide answer, ~2,100 for a
  deep one, because every node keeps its shortest path. At the 250,000 ceiling that is
  190–525 MB per concurrent request.

## Known limitations

1. **Only `local_group` principals and `local_group_member` edges are host-scoped.** A
   domain's own `BUILTIN\Administrators` is stored under the bare `S-1-5-32-544`, so two
   Active Directory domains collected into one ADG would merge their BUILTIN groups and
   their memberships. Not reachable today — one domain has been collected — but it is the
   most dangerous thing left in the identity model. Detected by the validator
   (`unscoped_well_known_group`); the fix is a versioned key change.
   See `ad-graph-validation.md` §4.
2. **Once a domain BUILTIN group occupies the bare key, `GET /principals/S-1-5-32-544`
   resolves it outright with 200** rather than a 409 listing all three candidates. The
   caller gets one group and no hint that others share the SID. Memberships still do not
   merge. Pinned by a test that says it is being pinned, not endorsed.
3. **`count_direct` is linear in the membership** — an index-only scan, 9.4 ms for 100,000
   rows — and `GET /principals/{id}` issues two of them.
4. **Nothing is cached.** Every effective-members answer walks the graph again.
5. **The write path is not benchmarked.** The benchmark seeds rows directly because it
   measures queries; ingestion throughput at scale is unmeasured.
6. **The measurements are from one developer workstation over loopback.** Shape-of-curve
   numbers and query plans transfer; absolute timings do not.
7. **The smoke-test database is shared between concurrent runs.** `tests/db` derives its
   database from `ADG_DATABASE_URL` plus `_test` and truncates every table before each
   test, so two suites running at once on one machine delete each other's rows mid-test.
   This happened during this phase and produced failures that looked like defects and were
   not. Point `ADG_DATABASE_URL` at a different database name to run in parallel; a
   per-worker database would be a real improvement to the harness.
8. **The validator reports, it does not verify.** No finding means well formed and
   self-consistent — it cannot know what a directory actually contains.
9. **The adversarial corpus is 460 KB**, most of it the 512-member fixture. It is generated,
   so it is maintainable, but it is not small.
10. **`a08` is the largest committed fixture at 512 members.** Anything wider is generated
    in the property and benchmark suites rather than committed.

## Security and privilege assumptions

- **Unchanged posture: read-only, least privilege.** Nothing in this phase reads a target
  system, and the validator is entirely offline — it sends nothing anywhere, which is what
  lets it run on a collector host as part of that collector's own build.
- **Every fix moves in the safe direction.** Three of the six make ADG *more* willing to
  say "this answer is incomplete" or "this input is invalid"; none makes it more willing to
  accept something. No test was made to pass by weakening a check.
- **Truncation is now honest in one more place.** The boundary probe was the last path on
  which a bounded traversal could return a short answer without saying so.
- **Input validation moved towards the published contract, never away from it.** The host
  name bounds were already specified; only enforcement was missing.
- **Every SID in the corpus is synthetic**, from two invented domains. Nothing was captured
  from a real directory, and the fixtures contain no credentials.
- **Endpoints remain unauthenticated.** Still the largest open risk in the system, and
  still outside this phase.

## Migration and compatibility notes

- **No migration, no schema change, no contract change.** `alembic check` reports no drift.
- **A collector that follows the published schemas is unaffected.** One that sent a host
  name longer than 255 characters, or containing a control character, was violating
  `common.schema.json` already and is now rejected with a message naming the field.
- **`?host=` changed meaning from "no-op" to "scope to this host"** when a host-scoped
  principal exists. Any caller relying on the previous behavior was relying on a defect.
- **Another session landed SMB/NTFS ingestion in this tree while this phase ran** and
  committed it as `bc9630e` partway through. Everything measured and tested here was run
  against the tree including that work. Only files belonging to this phase were staged, by
  name; `git status --short` below distinguishes them. The two phases touch different
  modules except `app/ingestion/plan.py` and `app/models/schema.py`, neither of which this
  phase modified — `app/validation/` reads `SUPPORTED_KINDS` from the former rather than
  restating it, which is why the validator already follows the new storable kinds.
- **No new dependencies.** The property tests use a seeded `random.Random` rather than a
  property-testing library, so failures are reproducible from the seed and CI gains no new
  pin.
- **The benchmark creates `<database>_bench`** on demand and truncates it on every run. It
  is never the development or the test database; a test pins that.

## Prerequisites for the next prompt

1. **Read `docs/architecture/ad-graph-validation.md` §4 before touching principal keys.**
   The BUILTIN scoping hazard is the one thing in the identity model that can merge two
   organizations' groups, and the fix belongs to whichever phase next revises the contract.
   Scope well-known group SIDs by their issuing authority — host for a machine-local group,
   domain SID for a domain's BUILTIN container — so the bare SID is never a storage key.
2. **Use `tests/fixtures/ad_graph/` rather than inventing identity data.** Add a builder to
   `build_ad_graph.py` and regenerate; the parity test will fail if you edit the JSON by
   hand. Give every new fixture a `note` saying what a reader should conclude from it — a
   test enforces that too.
3. **The effective-access engine must propagate `traversal.complete`.** An access answer
   computed from a truncated membership is itself a lower bound, and §3.1 of the validation
   note is what happens when that property is only assumed.
4. **Extend the property suite rather than only adding examples.** The reference-
   implementation comparison is the part that found the real defect; a new query shape
   should get one.
5. **Run `.\scripts\validate-collector-output.ps1` against any new collector's output
   before trusting it**, and add a finding code when a new class of hazard becomes
   expressible. Codes are listed in `ad-graph-validation.md` §5 and a test fails if one is
   emitted without being documented there.
6. **Re-run `.\scripts\graph-benchmark.ps1 -Scale large -Database` and update §6** whenever
   the query path, the schema, or the indexes change. A benchmark nobody re-runs is a
   number that used to be true.
7. **Point `ADG_DATABASE_URL` at your own database name** if another session may be running
   `tests/db` on the same machine.
8. New code must pass `.\scripts\backend-lint.ps1` (ruff + **mypy strict**),
   `.\scripts\backend-test.ps1`, and `.\scripts\backend-test.ps1 -Smoke` with the stack up.

## `git status --short`

Captured immediately before the phase commit. The concurrent SMB/NTFS work committed as
`bc9630e` partway through this phase, so what remains here is this phase's own work plus one
file that belongs to that other session and was **not** staged.

```text
 M README.md
 M backend/app/contracts/v1/common.py
 M backend/app/contracts/v1/envelopes.py
 M backend/app/domain/graph.py
 M backend/app/repositories/__init__.py
 M backend/app/repositories/membership.py
 M backend/app/services/graph.py
 M backend/tests/contracts/test_smb_collector.py    (other session; NOT staged)
 M backend/tests/fixtures/__init__.py
 M backend/tests/support/graph.py
 M docs/architecture/membership-graph.md
?? backend/app/validation/
?? backend/tests/api/test_traversal_pairing.py
?? backend/tests/benchmarks/
?? backend/tests/contracts/test_ad_graph_fixtures.py
?? backend/tests/contracts/test_published_constraints.py
?? backend/tests/db/test_graph_adversarial.py
?? backend/tests/domain/test_graph_adversarial.py
?? backend/tests/domain/test_graph_properties.py
?? backend/tests/domain/test_key_scoping.py
?? backend/tests/fixtures/ad_graph/
?? backend/tests/fixtures/build_ad_graph.py
?? backend/tests/validation/
?? docs/architecture/ad-graph-validation.md
?? docs/handoffs/phase-01c-ad-validation.md
?? scripts/graph-benchmark.ps1
?? scripts/validate-collector-output.ps1
```

Every path was staged explicitly by name. A bare `git add -A` in a tree two sessions are
working in would have swept the other session's file into this commit.
