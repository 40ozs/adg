# Handoff — Phase 6D (`phase-06/04-mvp-integration-hardening.md`)

**Date:** 2026-09-14
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-06c-explanation-ui.md](phase-06c-explanation-ui.md)
**Collector contract version after this phase:** `1.3` — unchanged.
**Derived-response contract:** `1.0` — unchanged.
**`docs/contracts/v1/openapi.json`:** regenerated. **Additive only** — 481 inserted lines,
zero deleted.

## Scope completed

Phases 0–6 built seven layers, each tested against fixtures written for that layer. This
phase built **one estate** and drove the whole product with it, which is the only way to test
the seams. It found five real defects, one of them the most dangerous class of error this
product can make.

1. **A repeatable demo estate** (`app/demo/`) covering AD, SMB and NTFS, deterministic across
   machines, replayed through the **real ingestion endpoints** — and deliberately imperfect.
2. **An end-to-end walkthrough** (`tests/db/test_mvp_end_to_end.py`): collect → ingest →
   resolve → explain → every page's own API call, as an auditor.
3. **The coverage defect, found and fixed.** A failure on one file server was hidden by a
   later success on another; the banner read `healthy` while a whole server was unobserved.
4. **An operator collector-status page**, backed by a new endpoint: last success and last
   failure of every scope, what each run failed to deliver, object counts, grouped errors.
5. **A per-capability authorization audit** that can distinguish two capabilities every real
   role holds together — which the existing route audit structurally could not.
6. **An input-validation audit** across every path and query parameter of every route. Found
   a 500 from a NUL byte and a misleading 404 message.
7. **Log redaction**, applied at the handler so it covers messages, structured values and
   tracebacks in both log formats.
8. **Query-cost budgets extended** to every endpoint added after Phase 4C. No N+1 was found;
   the budgets are now pinned.
9. **`docs/architecture/mvp-capabilities.md`** and **`docs/operations/mvp-runbook.md`**.
10. Two smaller defects: an invalid-HTML paragraph on the explanation screen, and an
    allow-list bypass in the frontend proxy.

No Phase 7 history semantics were added.

---

## The five defects

### 1. A failed scan of one server was hidden by a successful scan of another — **critical**

`app/domain/collection.py` is the module that keeps ADG's single most important distinction:
*nothing is there* versus *nobody looked*. Coverage was reduced to the newest run of each
collector **kind**. That is correct only while each kind runs against one target — and the
NTFS collector runs against every file server.

Measured against the demo estate on a live API: FS02's scan partial, FS03's failed, and one
later successful scan of FS01 was enough to make the whole verdict `healthy` with an empty
concerns list. Every empty list in the product then reads as an answer.

```
health: healthy
concerns: []
summary: Collection is current for: active_directory, local_groups, ntfs, smb.
```

**Fixed** by making the unit a **scope** — `(collector, target)` — in
`IngestionService.latest_run_per_scope`. `target` is nullable and SQL does not group NULLs,
so it is coalesced; runs with no target still collapse to one row per kind, which is what
they meant, and the Phase 2 test asserting that still passes unchanged. The same estate now
reports:

```
health: failed
concerns:
  The most recent ntfs (FS02) run is partial (some targets failed).
  The most recent ntfs (FS03) run failed; its scope is unobserved.
```

Each concern names its target, because two rows of one kind are only distinguishable by it.
`test_a_failure_on_one_server_is_not_hidden_by_a_success_on_another` fails against the old
query and passes against the new one — verified by reverting the query and re-running.

### 2. A NUL byte in a path parameter returned 500

`GET /api/v1/principals/S-1-5-21-1-2-3-1000%00` reached psycopg and raised
`PostgreSQL text fields cannot contain NUL (0x00) bytes` — an unhandled exception, a
traceback in the log instead of a rejection, and a wasted round trip. The SID parser refused
it; the identifier then fell through to a storage-key lookup, which is a plain string
comparison.

**Fixed** with `PRINTABLE_IDENTIFIER` in `app/api/deps.py`, applied to all seven `Path`
annotations and to the two `Query` parameters of `/access/explain`. No ADG identifier — SID,
storage key, host name, UNC path — can contain a control character, so the request is refused
before it costs a session. Found by fuzzing every parameter of every route; the audit lives
in `tests/db/test_input_validation.py` and covers 65 cases.

### 3. A 404 that sent the reader to the wrong place

`/api/v1/principals/not-a-sid` answered *"no principal observation and no membership edge
names it. It may simply not have been collected yet"* — which sends somebody who mistyped a
SID to look at the collectors. A storage key is a SID or `host|SID`, so a bare value that is
neither can never be collected. The message now says so and names the two forms.

### 4. Invalid HTML on the explanation screen

`LayerPanel` wrapped `<Rights>` in a `<p>`. `Rights` emits a `<div>` when the mask carries a
note, so the browser closed the paragraph early and reparented the warning **outside** the
rights it qualifies. React logged a hydration error on every render in the test suite — 620
passing tests and a warning nobody had attributed. Fixed with a `.layer-rights` block;
verified as zero `<div>`-inside-`<p>` in the live HTML.

### 5. The frontend proxy's allow-list did not hold

`/api/adg/*` checked `startsWith("api/v1/")` on the joined segments. Next.js hands a
catch-all route its segments already percent-decoded and `lib/api/client.ts` builds the URL
with `new URL()`, which resolves `..` before sending — so
`/api/adg/api/v1/..%2F..%2Fauth%2Fconfig` passed the check and left as `/auth/config`. The
rule was being checked against a path that no longer existed by the time it was used.

Nothing reachable that way was unguarded: every API path requires a capability and the proxy
attaches the caller's own token, so this was never a privilege escalation. It was a control
that did not hold. `lib/api/proxy.ts` now rejects dot segments, empty segments and separators
inside a segment, and matches an entry with no trailing slash exactly — `auth/me` previously
also admitted `auth/mercy`.

Two things learned while fixing it, both now in the module docstring: a **backslash is a
separator** here (`new URL()` rewrites `\` to `/` in an `http:` path), so a UNC resource key
cannot travel through this proxy as a path segment at all — which costs nothing today,
because nothing calls the proxy: every page fetches server-side, where the key is encoded
once and never decoded again.

### And one the phase gate was hiding

**`scripts/backend-lint.ps1` was already failing before this phase**, on seven mypy errors in
three test files untouched since Phase 5A. Verified by restoring those three files to their
`HEAD` content with everything else at this phase's state and re-running: exactly those seven
errors. `tests/access_engine/test_causality.py` carried **forty** `# type: ignore` comments,
all of which existed because two local helpers returned `object`. Typing them properly made
every one of the forty unnecessary; they were removed by a script driven by mypy's own
"unused ignore" output rather than by a pattern, so an ignore that is still load bearing
stayed. The lint gate now passes.

---

## Files and modules added or materially changed

### Added — the demo estate

| File | Lines | Contents |
| --- | ---: | --- |
| `backend/app/demo/estate.py` | 1048 | The estate as facts. Inherited ACEs, ACL hashes, boundary verdicts and depths are **derived** by the same domain functions the server uses, so the fixture cannot encode a different idea of inheritance than the product enforces. `FEATURES` lists what it carries on purpose |
| `backend/app/demo/transcripts.py` | 574 | The cut into scan runs. Every observation is built as its contract model, so a payload the API would refuse cannot be generated |
| `backend/app/demo/seed.py` | 177 | Posting through the ingestion API. No direct-to-database path |
| `backend/app/demo/__main__.py` | 172 | `python -m app.demo` — write transcripts, or post them |
| `scripts/seed-demo.ps1` | 107 | The operator-facing wrapper |

### Added — the operator view

| File | Lines | Contents |
| --- | ---: | --- |
| `backend/app/domain/operations.py` | 334 | Pure: completeness, shortfall, the three facts per scope, object counts, error groups |
| `backend/app/repositories/operations.py` | 240 | Five statements, none of which grows with the estate |
| `frontend/lib/operations.ts` | 262 | Every presentation decision on the page, as pure functions |

### Added — security and validation

| File | Lines | Contents |
| --- | ---: | --- |
| `backend/app/logging_redaction.py` | 214 | Four secret shapes removed; identifiers deliberately kept |
| `frontend/lib/api/proxy.ts` | 63 | The proxy allow-list, as a testable rule |

### Changed

| File | What |
| --- | --- |
| `backend/app/ingestion/service.py` | `latest_run_per_collector` → `latest_run_per_scope`, `DISTINCT ON (collector, coalesce(target,''))` |
| `backend/app/domain/collection.py` | Coverage is per scope; `scope_label`; concerns name their target; the healthy summary deduplicates collector names |
| `backend/app/api/collection.py` | `GET /api/v1/collection/operations` and its views |
| `backend/app/api/deps.py` | `PRINTABLE_IDENTIFIER` |
| `backend/app/api/{graph,access,resources}.py` | The pattern applied to seven path parameters and two query parameters; the principal 404 message split in two |
| `backend/app/logging_config.py` | The redaction filter installed on the handler; `TextLogFormatter` so the text format redacts tracebacks too |
| `frontend/app/collectors/page.tsx` | Rewritten as the operator page |
| `frontend/components/Explanation.tsx`, `app/globals.css` | The `<p>`/`<div>` fix |
| `frontend/lib/{contracts,api/adg}.ts` | The operations response, typed and fetched |
| `README.md` | Collectors page, the per-scope rule, redaction, the seeder, the two new documents |

### Documentation

* `docs/architecture/mvp-capabilities.md` — what the MVP answers, and what it does not. Two
  sides on purpose: in an auditing tool an unstated limit is a wrong answer waiting to be
  quoted.
* `docs/operations/mvp-runbook.md` — clean workstation to working product, pointing it at a
  real domain at least privilege, reading the Collectors page, operating it, and the seven
  things that will bite you.

---

## Design decisions worth knowing

### The demo estate derives what it can and declares only what an administrator would set

Explicit ACEs are written by hand. Inherited entries are `project_inherited_acl` applied to
the parent; the ACL hash is `acl_hash` over the result; the boundary verdict is
`boundary_reason_for`. A hand-written fixture drifts from the rules the moment the rules
change; this one cannot, because it holds no second copy of them. The end-to-end test then
checks the server's independent recomputation against it and they agree on every directory.

### It is deliberately imperfect, and the imperfections are the deliverable

A demo of a clean estate demonstrates the happy path and nothing else. `--features` prints
eighteen things that are wrong with it and why each is there. Three are worth naming:

* **A generic right** (`GENERIC_ALL` on `\\FS02\Projects`) projects onto a child as **two**
  entries — one mapped and effective, one unmapped and inherit-only. A projection that
  produced one would report every directory below it as a boundary.
* **`\\FS02\Projects\Alpha`** has no explicit entry and is still reported as differing from
  its parent, because Windows materialized the parent's `CREATOR OWNER` grant as an entry
  naming Bob, who created it. True about the DACL and misleading about intent — and exactly
  the finding an operator has to be able to read correctly.
* **FS03's share list was collected and its file system was not.** ADG knows a share is there
  and nothing about what is inside it.

### Two collection endpoints, not one

`/collection/status` is on the path of **every** screen and is kept cheap. `/collection/operations`
costs five statements and is read by one page. Both fold the **same** latest runs through the
same function, so the operator and the auditor can never be looking at two different verdicts
— asserted in `test_health_matches_the_coverage_endpoint`.

### The status page shows three facts per scope because they are three facts

Latest run, last success, last failure. A page showing only the first says "failed" and hides
that yesterday's data is on screen; one showing only the second says "succeeded" and hides
that it is stale. A scope that has never succeeded reads `never`, which is the worst state on
the page.

And `completeness` is not the run's status. A run can report `succeeded` and still have lost
a batch in transit, stored fewer rows than it claimed, or never reconciled a scope it
declared — none of which change its status, all of which mean the estate below it is less
complete than it looks. An **incremental** run is exempt from the reconciliation check: by
definition it looks at part of its scope, and counting that would mark every incremental run
incomplete and teach an operator to ignore the column.

### The authorization audit had to mint a synthetic principal

The route-table audit calls every route with no credential. That proves each route is behind
*a* capability, not behind the *right* one — and every real role holds `identities:read` and
`access:read` together, so a route requiring the wrong one of the two is reachable by exactly
the same accounts. **Proved**: swapping them on `/api/v1/groups` left the role-by-role sweep
entirely green.

So the audit narrows the role table for the duration of one test, mints a principal holding
exactly one capability, and asserts it reaches precisely the routes that need it. That is
what pins `resource-impact` to `access:read` rather than to the `identities:read` the rest of
`/api/v1/groups` carries. Re-run against the same mutation, it fails.

### Redaction keeps identifiers

Bearer tokens, JWTs, collector keys, `ADG_*` secrets and passwords inside connection strings
are removed — in the rendered message, in `extra=` values including nested mappings, and in
tracebacks, in both log formats. A mapping **key** that names a secret redacts its value
unread, because in a structured payload the label is the key and the secret is the value, so
no `key=value` rule has anything to anchor on.

SIDs, UNC paths, account names and ACL hashes are **not** redacted. They are what an operator
correlates against a Windows event log, and they are the subject of the product. A rule
widened until it eats them makes the log safe and worthless.
`TestWhatMustSurvive` is the half of that suite that stops a future rule from being widened.

That makes the log stream personal data rather than credential data, which is a retention
question; it is answered in the runbook, §6.

### The seeder respects the 409 rather than asking the API to relax

Re-seeding an already-completed run hit `409 Conflict` on its batches — the collector
protocol is explicit that a completed run accepts no more observations, and it is right to
be: a batch arriving after a completion means the collector and the server disagree about
whether the run finished. So the **seeder** changed, not the endpoint: a start that returns
`200` with a terminal status means the run is already fully applied, and its batches are
skipped. An interrupted run (started, some batches, never completed) is not terminal and
still resumes. Found by seeding twice; nothing else would have.

---

## Contracts

**One route added, nothing changed or removed.**

`GET /api/v1/collection/operations` — `COLLECTORS_READ`, the same capability a viewer holds
for the coverage banner. Response: `health`, `summary`, `notes[]`, `scopes[]` (each with
`latest`, `last_success`, `last_failure`, `completeness`, `stale_success`, `note`), `counts`,
`errors[]`, `total_errors`.

**`/api/v1/collection/status` is unchanged in shape.** Its `collectors[]` array now carries
one entry per `(collector, target)` rather than per collector kind, so a deployment with one
target per collector sees no difference and a multi-server one sees more rows. `concern`
strings now name the target when there is one.

**Path and query parameters gained a `pattern`.** Additive in the OpenAPI document; the
values it rejects were 500s or fell through to a lookup that could not match.

The snapshot was regenerated and `frontend/tests/contracts.test.ts` checks the frontend's
hand-written types against it, field by field.

---

## Tests run and exact results

| Gate | Command | Result |
| --- | --- | --- |
| Backend, hermetic | `.\scripts\backend-test.ps1` | **4,219 passed**, 10 skipped, 568 deselected, 27s — 4,066 before this phase |
| Backend, with PostgreSQL | `.\scripts\backend-test.ps1 -Smoke` | **4,786 passed**, 10 skipped, 1 xfailed, 5m43s — 4,506 before this phase |
| Backend lint and types | `.\scripts\backend-lint.ps1` | **passed** — ruff, ruff format, mypy over `app` and `tests`. Failing before this phase; see above |
| Frontend | `.\scripts\frontend-check.ps1` | **passed** — lint clean with 0 warnings, `tsc` clean, **695 tests in 20 files** (620 before this phase), production build compiled |
| Collectors | `.\scripts\collector-test.ps1` | **944 passed, 0 failed** (Pester 5) — unchanged; no collector was edited |

**280 backend tests and 75 frontend tests are attributable to this phase.** No existing test
was weakened or deleted. Three were *corrected*, because what they asserted stopped being
true on purpose: two in `test_collection_status_api.py` whose subject changed from collector
kind to scope, and its `transcript()` helper, which gained a `target`.

### The live run

Not a claim from a stub. A phase-scoped database (`adg_p6d`) was created and migrated, the
API run natively (`python -m app --port 8099`), the demo estate seeded through
`scripts/seed-demo.ps1`, and the frontend served by a real `next start` against it. Every
page was fetched as the **auditor** account — not the administrator the tests use.

Seeding, through the PowerShell wrapper:

```
Demo estate 'standard': 41 principals, 49 memberships, 3 servers, 7 shares,
                        21 directories, 238 objects in 8 scan runs.
ad                   active_directory   succeeded      88 applied
local-groups-fs01    local_groups       succeeded       2 applied
smb-fs01             smb                succeeded      10 applied
smb-fs02             smb                succeeded       6 applied
smb-fs03             smb                succeeded       3 applied
ntfs-fs01            ntfs               succeeded      55 applied
ntfs-fs02            ntfs               partial        74 applied
ntfs-fs03            ntfs               failed          0 applied
total                                                 238 applied
```

The access matrix the live engine produced, which is the estate's design read back:

```
                     finance        payroll   confidential      public        archive           beta
Alice               True:Modify    True:Modify   False           True:Full     True:Read&Ex   True:Modify
Bob                 True:Modify    False         False           True:Full     True:Read&Ex   True:Modify
Carol               True:Read&Ex   False         True:Modify     True:Full     False          False
Dan                 True:Full      True:Full     True:Full       True:Full     False          True:Modify
Erin (disabled)     True:Modify    True:Read&Ex  False           True:Full     True:Read&Ex   True:Modify
Contractor001       True:Read&Ex   False         False           True:Full     False          False
```

Dan holds Full Control everywhere on FS01 through `BUILTIN\Administrators` and **nothing** on
FS02, because that is a different group on that machine. Erin is disabled and still holds
what the ACL grants her SID. Contractor001 is denied on `HR\Confidential` despite reaching a
granting group by another route.

Every page rendered, as the auditor, with the content asserted:

```
OK 200   14585  /
OK 200   13966  /resources
OK 200   21125  /resources/server?key=fs01
OK 200   23155  /resources/share?key=fs01|finance
OK 200   35133  /resources/directory?key=\\fs01\finance
OK 200   12476  /identities
OK 200   28614  /identities/principal?key=…-1104
OK 200   17024  /access
OK 200  107385  /access/explain?principal=…-1104&resource=\\fs01\finance
OK 200   17436  /search?q=finance
OK 200   36305  /collectors
OK 200   11778  /status
OK 200   15331  /settings
```

What the Collectors page said, verbatim from the rendered HTML:

> Collection: status failed. 6 of 8 scopes are complete, 1 incomplete, 1 with nothing usable.
> 1 scope(s) have never completed a run at all.
> **What needs attention** — The latest ntfs (FS02) run was incomplete: 2 declared scope(s)
> were not fully enumerated; 2 object(s) could not be read. ntfs (FS03) has never completed a
> run. Nothing about this scope has ever been collected.

And both pages were checked for the nesting defect: zero `<div>` inside `<p>` in the rendered
HTML of `/collectors` and `/access/explain`.

### Where each acceptance criterion is checked

| Criterion | Where |
| --- | --- |
| A clean Windows + Docker setup reaches a functioning MVP by following the runbook | `docs/operations/mvp-runbook.md` §2, walked in the live run above |
| End-to-end smoke tests pass | `tests/db/test_mvp_end_to_end.py` |
| Collector failures and incomplete scans are visible | `test_a_failure_on_one_server_is_not_hidden_by_a_success_on_another`; `tests/db/test_operations_api.py`; `frontend/tests/collectors-page.test.tsx` |
| Viewer vs admin authorization is tested | `tests/api/test_authorization.py::TestEveryRouteRequiresTheRightCapability`, and the per-capability audit |
| No known critical correctness issue in effective access | The access matrix above, asserted in `TestTheAccessAnswers`; the boundary recomputation agrees on every directory in the estate |

---

## Known limitations

1. **A scope is `(collector, target)`, which is coarser than a run's declared scopes.** An
   NTFS run declares one `directory_tree` per share and the coverage unit is its target,
   usually the server. A run that read three of four shares reports one partial scope rather
   than three complete and one missing. The declared-versus-reconciled counts are on the
   operator page, so the shortfall is visible; attributing it to the share is not.
2. **Staleness is shown, not judged.** Nothing decides a scope is *too* old; there is no
   schedule to compare against.
3. **The end-to-end suite seeds once per test** — the `clean_tables` fixture is per-test by
   design — so it costs about 43 seconds for 34 tests. Two parametrizations were collapsed
   into loops for that reason, which trades granular failure names for time; both collect
   every mismatch so a failure still names them all.
4. **The frontend proxy cannot carry a UNC path.** Documented in `lib/api/proxy.ts` and
   costless today, because nothing calls the proxy.
5. **Redaction is a filter, not a proof.** It is a safety net under careful call sites. A
   credential in a shape none of the four rules matches would pass.
6. **The demo estate is synthetic.** It is validated against the contract, the schemas and
   the server's own recomputation, but no Windows host produced it. The real-tree generator
   from Phase 3C (`scripts/windows-test-tree`) remains the way to test against a real volume.
7. **No conditional GET.** Unchanged from Phase 6C.
8. **`/collection/operations` bounds the error summary** at 20 codes and 3 sample targets per
   code. A run reporting more is reported by count; the full list is on the run itself.

---

## Security and privilege assumptions

- **Read-only, unchanged.** No new write path. The one write in the product is collector
  ingestion, which the demo seeder uses exactly as a Windows collector would.
- **`/collection/operations` requires `COLLECTORS_READ`**, which a viewer holds — deliberately,
  and for the same reason a viewer holds it for the coverage banner: somebody looking at an
  empty page has to be able to find out whether anything ran. The page discloses scan
  metadata and object counts, not permissions.
- **A new disclosure worth naming:** the object counts tell any viewer how large the estate
  is. That is already inferable from the paged listings and is the point of the panel.
- **The demo estate contains no credential.** It is generated, its SIDs are synthetic, and
  `seed-demo.ps1` reads a collector key from an environment variable rather than an argument
  — an argument is visible in the process list and in shell history.
- **Log redaction added** (above). Identifiers are kept on purpose, which makes the log stream
  personal data; the runbook says so and says what follows from it.
- **The proxy allow-list now holds.** It was never a privilege boundary — the backend decides
  — but it is a control, and a control with a bypass provides nothing.
- **Least privilege documented end to end.** The runbook's §4 names `READ_CONTROL` as the one
  right to get right for the NTFS collector: the right to read a security descriptor is not
  the right to read the data, and a service account with the first and not the second can
  audit a share it cannot open.

---

## Migration and compatibility notes

- **No database migration.** The schema is unchanged; head remains `0006_effective_access`.
- **One route added, none changed or removed.** A client written against Phase 6C keeps
  working.
- **`/collection/status` returns more rows** on a multi-target deployment. Any client that
  assumed one row per collector kind now sees one per scope. The frontend was updated; the
  field set is unchanged.
- **`concern` strings now include the target.** A client matching on their exact text would
  break; nothing does — the frontend renders them.
- **A malformed path parameter now returns 422 where it previously returned 404 or 500.** The
  values affected are ones no correct client sends.
- **`IngestionService.latest_run_per_collector` was renamed** to `latest_run_per_scope`. One
  internal caller; no public surface.

---

## Prerequisites for the next prompt

**For the risk phase (Phase 7 or wherever risk lands):**

1. **Seed the demo estate first.** `scripts/seed-demo.ps1`, then `--features`. Every risk rule
   worth writing has a case in it already: `Everyone` with full control on `\\FS01\Public`, an
   orphaned SID on `\\FS02\Projects\Beta`, a disabled account holding rights on
   `\\FS01\Finance\Payroll`, and a directory whose ACL differs from its parent for a reason
   nobody chose (`\\FS02\Projects\Alpha`).
2. **A risk must never fire on `changes_nothing: true` as a remediation.** Phase 6C's
   prerequisite, restated because the demo estate now makes it trivially reproducible: Alice
   reaches Finance-RW twice over, so either membership removal is a no-op.
3. **Read `completeness`, not `status`, when deciding whether a finding's absence means
   anything.** A `succeeded` run that lost a batch is `partial` here and nowhere else.
4. **A finding about a scope that has never succeeded is not a finding.** `never_succeeded`
   on the operations report names them.

**For whoever extends the operator page:**

5. **Do not add a sixth query.** The five are budgeted in
   `test_it_reads_the_run_table_a_fixed_number_of_times`, and the property that matters is
   that none of them grows with the estate.
6. **Add a caveat to the API's `note`, not to the page.** `lib/operations.ts` uses the API's
   sentence verbatim in two places; a second wording would let them disagree.

**For whoever adds the first browser-side data call:**

7. **A directory cannot be addressed through `/api/adg/*` as a path segment.** Use a query
   parameter. `lib/api/proxy.ts` says why.

**For whoever runs the phase gates:**

8. **`backend-lint.ps1` is now a real gate.** It passes; keep it passing. It runs mypy over
   `tests` as well as `app`, and the repo convention is that test parameters are annotated —
   the `tests.*` override in `pyproject.toml` only relaxes `disallow_untyped_defs`, and the
   rule that actually bites is `disallow_incomplete_defs` from `strict = true`.
9. **Do not run two `pytest -Smoke` processes at once.** They share `adg_test` and the
   per-test `TRUNCATE` deadlocks. Observed once during this phase and mistaken for a defect
   for several minutes.

---

## `git status --short`

Taken after the commit.

```
PENDING
```
