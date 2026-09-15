# Handoff — Release Audit (`99-release/01-security-performance-release-audit.md`)

**Date:** 2026-09-15
**Workspace:** `C:\code\adg`
**Branch:** `master`
**Previous handoff:** [phase-10c-remediation.md](phase-10c-remediation.md)
**Collector contract version after this phase:** **unchanged, 1.4.** The one contract change
is additive documentation of behavior the server already enforced.
**`docs/contracts/v1/openapi.json`:** unchanged. No route was added, removed or altered.

---

## Scope completed

A whole-project audit across security, correctness, performance, database and operations,
with four release documents and **eight findings — seven fixed, one accepted**.

Three of the findings were the kind only an audit turns up, because each one was invisible
from inside the phase that created it:

1. **A normative contract that never said how to authenticate.** A collector author
   implementing `collector-protocol.md` to the letter would build a collector that cannot
   talk to ADG, and would have no reason to think TLS mattered.
2. **An API endpoint returning `422` for a correct request.** `GET /api/v1/changes/compare`
   failed on an ordinary estate. The audit hit it on the first comparison it ran.
3. **A benchmark that had silently stopped running.** It had been unable to build an estate
   since Phase 6A, while the numbers it once produced still read as current — the exact
   failure its own docstring warns about.

Three areas that had never been measured now are: **risk evaluation, history comparison and
simulation**, with a committed harness.

---

## Findings

| # | Sev | Finding | Status |
| --- | --- | --- | --- |
| C-1 | **High** | `GET /api/v1/changes/compare` returned 422 on an ordinary estate | Fixed |
| S-1 | **High** | The collector contract specified no authentication or transport | Fixed |
| P-1 | **High** | The access benchmark could not run, and truncated the test database | Fixed |
| S-2 | Medium | `ADG_REMEDIATION_SIGNING_KEY` had no minimum length | Fixed |
| Q-1 | Medium | `backend-lint.ps1` failed — 7 mypy errors, 3 in production code | Fixed |
| O-1 | Medium | The container stack could not configure any setting added after Phase 6 | Fixed |
| O-2 | Medium | The runbook named 3 of 8 roles and documented backup with no restore | Fixed |
| S-3 | Low | Mutating web routes rely on `SameSite` rather than an `Origin` check | Accepted |

### C-1 in full, because it is the one that changes behavior

`ObjectChange.__post_init__` enforces ADR-0019: a `FIRST_OBSERVED` change must carry **no**
window, because a first observation is precisely the case where nothing was watching and any
lower bound would be invented. That invariant is right.

The classifier built exactly that object, because two container facts answer different
questions and can disagree:

* `action_for` asks whether the container **object** was in the record before this version
  opened;
* `window_between` falls back to the newest confirmation of a **sibling** inside that
  container.

A scan stamps each observation at the instant it was read, so a group's member edge can open
at 08:01 while the group principal is first seen at 08:03 and a second edge was already
confirmed at 08:00. No container observed before → *first observation*. A sibling bound
available → *a window*. The invariant raised a `DomainValidationError`, and the central
handler maps that to **422 Unprocessable Entity** — telling a caller to fix a request that
was entirely well-formed.

**Fix:** in `app/changes/classify.py`, the *action* decides whether a lower bound is
meaningful. A `FIRST_OBSERVED` change is given no window whatever a sibling confirmation
offers.

**Why it survived.** A test named
`test_a_first_sighting_is_refused_a_window_even_if_one_is_offered` already existed — and
never offered one. It passed `container_observed_before=False` with no
`container_confirmed_at`, making it identical to the test above it. The name asserted the
behavior; the body did not. It is now two tests: the case with nothing to bound it, and the
case that is offered a bound and must still refuse.

**All four new tests were confirmed to fail with the fix reverted**, including the API
returning 422. A regression test that does not reproduce is the thing that let this through
in the first place.

---

## Files and modules added or materially changed

### Added

| File | Contents |
| --- | --- |
| `docs/release/release-readiness.md` | The verdict, the inventory, every suite with its command and result, the database and operational reviews, all eight findings, the acceptance criteria |
| `docs/release/security-review.md` | Authn, authz, ingestion, secrets, the browser tier, input and query safety, log redaction, production defaults, minimum privileges, and what the review did **not** cover |
| `docs/release/performance-baseline.md` | Every number measured during this audit on a named machine, including three areas never measured before |
| `docs/release/known-limitations.md` | Every limitation from every phase handoff, in one place, ranked by how badly a reader could be misled |
| `backend/tests/benchmarks/release_benchmark.py` | Five shapes: full and incremental risk evaluation, the change feed, a comparison, and a what-if |

### Changed — product code

| File | What |
| --- | --- |
| `backend/app/changes/classify.py` | **C-1.** A `FIRST_OBSERVED` change is given no window, whatever a sibling confirmation offers |
| `backend/app/config.py` | **S-2.** `MIN_SIGNING_KEY_LENGTH = 32` and the validator that refuses a shorter one at startup |
| `backend/app/changes/rules.py` | **Q-1.** `NtfsRight` imported from `app.domain.access`, as every other module does |
| `backend/app/changes/impact.py` | **Q-1.** Two `scalar_one_or_none()` results annotated, so neither function returns `Any` where it declares `datetime` |

### Changed — contract and operational documents

| File | What |
| --- | --- |
| `docs/contracts/collector-protocol.md` | **S-1.** A new *Authenticating every request* section before §1, and the §9 example now sends the header and stops retrying on 401/403 |
| `docs/operations/mvp-runbook.md` | **O-2.** All eight roles with what each deliberately cannot do; the signing-key requirement; a restore procedure |
| `docker-compose.yml` | **O-1.** Retention, post-run evaluation, remediation and the two policy paths reach the API container |
| `.env.example`, `SECURITY.md` | **S-2.** The 32-character rule and why |

### Changed — test and benchmark code

| File | What |
| --- | --- |
| `backend/tests/benchmarks/access_benchmark.py` | **P-1.** Signs in; uses `<database>_bench` and creates/migrates it, never `<database>_test` |
| `backend/tests/benchmarks/test_access_benchmark_harness.py` | **P-1.** `TestItStillRunsEndToEnd` actually calls `measure()`; the URL-convention tests updated |
| `backend/tests/changes/test_classify.py` | **C-1.** The test that only appeared to cover this, split into two that do |
| `backend/tests/db/test_change_comparison.py` | **C-1.** The estate that reproduces it, at the service and at the API |
| `backend/tests/test_config.py` | **S-2.** `TestTheChangePlanSigningKey` |
| `backend/tests/db/test_change_feed.py`, `test_changes_api.py` | **Q-1.** Annotations |

---

## Architecture decisions

**No new ADR.** Nothing here decided anything new; every fix restores a rule an existing ADR
already made. C-1 enforces ADR-0019 where the action is known, S-2 applies the collector-key
reasoning to the one other bearer secret, and S-1 writes down what the server already did.

Two judgments worth recording:

1. **The authentication section is unnumbered.** Adding `## 2. Authenticating` would have
   renumbered eight sections and every internal cross-reference, on an *accepted* contract,
   to fix a documentation gap. An unnumbered section before §1 is read first and moves
   nothing. (The document already carries two dangling references, to `§0` and `§11`; this
   change neither creates nor repairs them.)
2. **S-3 was recorded rather than fixed.** Adding an `Origin` assertion to two working routes
   is a hardening change, not a defect repair, and a release audit is the wrong moment to
   change how a working route admits requests. It belongs to the next phase that touches
   them.

---

## Schemas and contracts

**None changed.** No table, no migration, no route, no response model, no JSON Schema.
`docs/contracts/v1/openapi.json` was not touched by this audit — its file is unmodified since
Phase 10C wrote it — and `tests/contracts/test_openapi_snapshot.py` passes, so the
application still serves exactly what it describes. Contract v1.4 stands.

---

## Tests run, and exact results

| Suite | Command | Result |
| --- | --- | --- |
| Backend, hermetic + database | `.\scripts\backend-test.ps1 -Smoke` | **6,783 collected · 6,772 passed, 10 skipped, 1 xfailed, 0 failed**, 26 m 29 s · **exit 0** |
| Backend lint and types | `.\scripts\backend-lint.ps1` | ruff clean · format clean · mypy clean, 361 files · **exit 0** |
| Collector (Pester) | `.\scripts\collector-test.ps1` | **1,050 passed, 0 failed, 0 skipped** |
| Frontend (lint + types + tests + build) | `.\scripts\frontend-check.ps1` | **31 files, 1,126 tests passed**; build compiled · **exit 0** |
| Clean install | `CREATE DATABASE` → `alembic upgrade head` | **17 revisions, 2.67 s**, single head `0015_remediation_change_plans` |
| Schema parity on that database | `pytest tests/db/test_schema.py tests/db/test_remediation_schema.py` | **45 passed** |
| Access benchmark | `python -m tests.benchmarks.access_benchmark --sizes 10 100 400 --repeat 10` | Ran; numbers in the baseline |
| Release benchmark | `python -m tests.benchmarks.release_benchmark --sizes 10 100 400 --repeat 8` | Ran; numbers in the baseline |
| No-mutation guards | `pytest tests/remediation/test_no_write_path.py test_executor.py test_isolation.py` | **143 passed** |

The 10 skips and 1 xfail are accounted for in `release-readiness.md` §3; none is a feature
going unrun, and the xfail is the strict one pinning the known `resource -> principals` cost.

**11 tests were added by this audit** — 5 for the signing key, 2 for the classifier, 2
against PostgreSQL for C-1 at the service and at the API, and 2 for the benchmark harness
including the end-to-end one that would have caught P-1.

### A methodology note that nearly became a false finding

The audit's first backend run reported roughly 400 failures and errors. **They were an
artifact of the invocation, not of the tree.** pytest was launched from the repository root,
where it finds no configuration file, so `asyncio_mode = "auto"` — which lives only in
`backend/pyproject.toml` — never applied, and every async test failed or errored. Run through
`scripts\backend-test.ps1`, which pushes into `backend\` first, the same tree is green.

Recorded because the failure mode is convincing: a wall of errors that looks like a broken
product and is a broken command line. **Run the scripts, not the tools they wrap.**

---

## Known limitations

The full set is `docs/release/known-limitations.md`. The four that matter most:

1. **The AD and SMB collectors have never met a real domain or file server.** Every AD test
   runs against a fixture provider. The NTFS collector is the exception and has been
   validated against real volumes and against Windows' own `AuthzAccessCheck`.
2. **Privileges are unmodeled.** `SeBackupPrivilege` bypasses the DACL; ADG reports no access
   and says nothing about it. It **under-reports**, which is the dangerous direction.
3. **Deny-only SIDs are unmodeled**, so ADG **over-reports** for members of local
   administrator groups.
4. **Risk evaluation is superlinear above ~100 shares** (45 ms → 626 ms from 100 to 400), and
   the *steady-state* incremental pass — the case incremental evaluation exists for — remains
   unmeasured. See the baseline §3.3–3.4.

Limitations of this audit itself: no penetration test, no dependency CVE scan, no live tenant,
no live domain, no load or concurrency testing, and no estate larger than 400 shares.

---

## Security and privilege assumptions

Unchanged by this audit and re-verified by it:

* No collector needs Domain Admin. NTFS needs `READ_CONTROL` — the right to read a security
  descriptor, not the data.
* The API service needs PostgreSQL rights and nothing else; the database account owns its own
  database and is not a superuser.
* **ADG has no write adapter for AD, SMB or NTFS**, no dependency that could supply one, and
  no route that reaches an executor. `remediation:execute` is granted by no role and
  implemented by nothing.
* Three production misconfigurations are refused at startup rather than warned about:
  development auth, the development database URL, and any remediation execution mode other
  than `disabled`.
* Grant the three remediation roles to **three different people**, or the separation of duties
  is a formality the software cannot restore.

---

## Migration and compatibility notes

**No migration.** Nothing to run, nothing to roll back, and no data touched.

One behavioral change a deployment will notice: **an existing `ADG_REMEDIATION_SIGNING_KEY`
shorter than 32 characters now refuses to start.** That is deliberate — it is the only
signature an administrator can check before carrying out a change — and the message names the
setting and how to generate a replacement. Empty is still valid and still means "this
deployment cannot export".

C-1 changes an answer: comparisons that previously returned 422 now return 200, and a
`FIRST_OBSERVED` change never carries a window. No client can have depended on the old
behavior, because the old behavior was an error response.

---

## Prerequisites for the next prompt

1. **Read `docs/release/known-limitations.md` §1 before planning any field work.** The first
   real installation is a validation exercise for the AD and SMB collectors, not a
   deployment.
2. The benchmarks now target `<database>_bench`, create and migrate it themselves, and
   **truncate it on every run**. They no longer touch `<database>_test`.
3. `tests/benchmarks/test_access_benchmark_harness.py::TestItStillRunsEndToEnd` needs
   PostgreSQL and is `smoke`-marked. It runs under `-Smoke` and is skipped otherwise.
4. S-3 is open and belongs to whoever next touches `frontend/app/api/simulations` or
   `frontend/app/api/watches`.
5. The four release documents are meant to be updated in place by later phases, not
   superseded. `performance-baseline.md` §7 and `known-limitations.md` are the two that go
   stale fastest.
6. If a phase adds a route, the authorization table in `tests/api/test_authorization.py` must
   gain an entry or the suite fails — which is the intended behavior, not an obstacle.

---

## Git

**No commit was made, deliberately.** The prompt permits one; this tree does not safely admit
one.

`git status --short` reports **211 entries, 127 of them untracked** — because phases 07B,
08A, 08B, 09A, 09B, 10A, 10B and 10C are all present and **uncommitted** in this working tree,
authored by other sessions working in it concurrently. The last commit is `5537b16`
("Phase 7C: record the commit hash in the handoff").

Several files this audit changed are files those phases had already modified and not
committed — `backend/app/config.py` most clearly. Committing them would sweep other sessions'
in-flight work into a commit labeled as a release audit, partially and without their
authors' knowledge, and would produce a commit that does not stand alone. The index was
empty at the end of this phase, and it was left that way.

**What should happen instead:** the phases in flight commit their own work, and this audit's
changes are then committed on top of it — they are small, independent, and every one is
listed under *Files and modules added or materially changed* above.

### The 23 files this audit touched (17 changed, 6 added)

```text
 M  .env.example
 M  README.md
 M  SECURITY.md
 M  docker-compose.yml
 M  docs/contracts/collector-protocol.md
 M  docs/operations/mvp-runbook.md
 M  backend/app/changes/classify.py
 M  backend/app/changes/impact.py
 M  backend/app/changes/rules.py
 M  backend/app/config.py
 M  backend/tests/changes/test_classify.py
 M  backend/tests/db/test_change_comparison.py
 M  backend/tests/db/test_change_feed.py
 M  backend/tests/db/test_changes_api.py
 M  backend/tests/test_config.py
 M  backend/tests/benchmarks/access_benchmark.py
 M  backend/tests/benchmarks/test_access_benchmark_harness.py
??  backend/tests/benchmarks/release_benchmark.py
??  docs/handoffs/release-audit.md
??  docs/release/known-limitations.md
??  docs/release/performance-baseline.md
??  docs/release/release-readiness.md
??  docs/release/security-review.md
```

The full `git status --short` for the whole tree — including the eight phases in flight — is
saved at `.tmp/audit-git-status.txt`, which is git-ignored.
