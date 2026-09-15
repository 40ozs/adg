# ADG release readiness

**Status:** accepted
**Phase:** Release Audit
**Date:** 2026-09-15
**Tree audited:** `master`, phases 00A through 10C
**Companions:** [security-review.md](security-review.md) ·
[performance-baseline.md](performance-baseline.md) ·
[known-limitations.md](known-limitations.md)

---

## 1. Verdict

**Ready to release to a first, supervised installation. Not ready to be trusted unattended
against a production domain.**

The distinction is not hedging, and it is not about code quality. Every automated suite
passes, the authorization boundary is proven against the route table rather than a list, the
migration builds from empty exactly what the model declares, and the read-only posture is
enforced by five independent guards and the absence of any write adapter at all.

What is missing is **contact with a real domain**. The Active Directory collector has never
run against a domain controller and the SMB collector has never read a share ACL off a real
file server (`known-limitations.md` §1). The NTFS collector, by contrast, has been validated
against real volumes and checked against Windows' own `AuthzAccessCheck` on 5,626 of 5,628
cases — which is exactly the standard the other two have not yet been held to.

So: install it, point it at a non-production domain, and treat the first collection as the
validation exercise it is.

Eight findings, listed in §7. **Seven fixed in this audit**, one accepted and recorded. Three
were the kind that only a release audit finds: a normative contract that never said how to
authenticate, a benchmark that had silently stopped being able to run, and an API endpoint
that returned `422` for a request that was entirely correct.

---

## 2. Feature inventory

The application serves **89 paths / 100 operations**. Every area below is documented in
`README.md`, has an architecture document, and carries the ADRs its decisions were recorded
in — the inventory and the documentation do not disagree anywhere the audit could find.

| Area | Ops | Architecture | Phase |
| --- | ---: | --- | --- |
| Collector ingestion and status | 7 | `collector-protocol.md`, `resource-inventory.md` | 0B–3C |
| Identity and membership graph | 7 | `membership-graph.md`, `ad-graph-validation.md` | 1A–1C |
| Resources, raw ACLs and search | 9 | `resource-inventory.md`, `ntfs-acl-boundaries.md` | 2A–3C |
| Effective access and explanation | 7 | `effective-access.md`, `access-causality.md` | 4A–5B |
| History and changes | 5 | `history-model.md`, `change-detection.md` | 7A–7C |
| Incremental collection | — | `incremental-collection.md` | 7B |
| Risk findings | 5 | `risk-model.md` | 8A |
| Alerts and watches | 8 | `alerting.md` | 8B |
| What-if simulation | 9 | `simulation.md`, `simulation-surface.md` | 9A–9B |
| Governance and access reviews | 22 | `governance-model.md`, `access-review-workflow.md` | 10A–10B |
| Remediation change plans | 15 | `remediation.md` | 10C |
| Authentication and meta | 6 | `authentication.md` | 6A |

`docs/contracts/v1/openapi.json` is a committed snapshot and
`tests/contracts/test_openapi_snapshot.py` fails if it drifts from what the application
serves, so the number above is the application's, not a document's.

**Collector contract remains v1.4.** No finding in this audit required a version bump: the
one contract change is additive documentation of behavior the server already enforced.

---

## 3. Every automated suite, with the exact command

| Suite | Command | Result |
| --- | --- | --- |
| Backend, hermetic + database | `.\scripts\backend-test.ps1 -Smoke` | **6,783 collected · 6,772 passed, 10 skipped, 1 xfailed, 0 failed** in 26 m 29 s |
| Backend lint and types | `.\scripts\backend-lint.ps1` | **ruff clean · format clean · mypy clean (361 files)** |
| Collector (Pester) | `.\scripts\collector-test.ps1` | **1,050 passed, 0 failed, 0 skipped** |
| Frontend lint | `npm run lint` | **clean** |
| Frontend types | `npm run typecheck` | **clean** |
| Frontend tests | `npm test` | **1,126 passed, 31 files** |
| Frontend production build | `npm run build` | **succeeded** |
| Clean-install migration | `alembic upgrade head` on an empty database | **17 revisions, 2.67 s, single head** |
| Schema parity on that clean database | `pytest tests/db/test_schema.py tests/db/test_remediation_schema.py` | **45 passed** |

No suite has a justified exception: every one passes.

**The 10 skips and the 1 xfail are accounted for, because an unexplained skip is an untested
capability wearing a green tick.** Eight skips are one property-based test
(`tests/domain/test_graph_properties.py`) reporting that a particular random seed generated
no parallel memberships to check; the other two are a scenario fixture that declares no
expected NTFS mask and a JSON Schema that carries no examples. None is a feature going
unrun. The single xfail is **strict** and is the known `resource -> principals` cost
limitation (§4, `known-limitations.md` §4.1) — it fails the suite if it ever starts passing.

> ### A methodology note, because it nearly became a false finding
>
> The audit's first backend run reported roughly 400 failures and errors. They were an
> artifact of **how it was invoked**, not of the tree: pytest was launched from the
> repository root, where it finds no configuration, so `asyncio_mode = "auto"` — which lives
> only in `backend/pyproject.toml` — never applied and every async test failed or errored.
>
> Re-run through the project's own `scripts\backend-test.ps1`, which pushes into `backend\`
> first, the same tree is green. The lesson is worth keeping: **run the scripts, not the
> tools they wrap.** Nothing in the product was wrong, and a report that had taken the first
> run at face value would have been badly wrong in the other direction.

---

## 4. Correctness audit

Each area the audit was required to examine, what holds it up, and where it is wrong. "Wrong"
entries are expanded in [`known-limitations.md`](known-limitations.md).

| Area | State | Held up by |
| --- | --- | --- |
| **SID identity** | **Sound.** The SID is the key everywhere; names are metadata and every name ever observed is retained, so a rename is a new name on the same principal rather than a new principal. Keys are host-scoped only where Windows scopes them — a BUILTIN SID is `<host>\|<sid>`, a domain group nested into a local group keeps its global key. | `tests/domain/test_sid.py`, `test_key_scoping.py`, `test_principals.py`; `tests/db/test_ingestion.py` rename cases; fixtures `a03-duplicate-names`, `a04a/b-rename` |
| **Membership cycles** | **Sound.** A cycle is *stored*, not rejected — a directory can contain one and refusing it would lose the observation — and it is reported with its members. A self-edge is refused by a database constraint, because a group is never a direct member of itself. | `tests/domain/test_graph.py::TestCycles`, `tests/db/test_graph_adversarial.py::TestCycles`, fixture `a02-cycles` |
| **SMB/NTFS separation** | **Sound.** The two layers are collected separately, stored separately, and combined only at evaluation, where the crossing is an **intersection** — the effective answer is the narrower of the two. The rights model forbids comparing layers by label, because SMB `Read` and NTFS `Read & Execute` are one mask under two names. | `docs/architecture/rights-model.md` (ADR-0005); `tests/access_engine/test_scenarios.py` scenarios 10 and 11; the 5,631-case matrix |
| **Allow / Deny / inheritance** | **Sound where modeled.** Order is honored — an Allow ahead of a Deny wins, because Windows walks the ACL in order — and inheritance propagation implements the table Windows actually implements, including the generic-rights split and `CREATOR OWNER`. Verified against **Windows' own `AuthzAccessCheck`: 5,626 of 5,628 answerable cases agree exactly**, and the two that differ are documented deviations on masks no real ACL carries. | `tests/domain/test_inheritance.py`, `tests/access_engine/test_evaluation.py`, `test_matrix.py`; `docs/architecture/effective-access-limits.md` §5 |
| | **Wrong in two known ways.** Privileges are unmodeled, so ADG **under-reports** for a backup operator; deny-only SIDs are unmodeled, so it **over-reports** for members of local administrator groups. | `known-limitations.md` §2.1–2.2 |
| **History completeness** | **Sound, with one measured divergence.** Every collected object carries a validity interval; a change is a *window*, never an instant, and a gap in observation is reported as a gap rather than as a change — the single failure this product exists to prevent. **One defect fixed in this audit** (C-1). | `tests/db/test_history_versions.py`, `test_change_comparison.py`, `test_change_feed.py`; ADR-0019 |
| | **The divergence:** after a reconciled scan proves an ACE gone, the point-in-time engine stops counting it and the **live** engine does not. Pinned with exact masks rather than only documented. | `tests/db/test_simulation_equivalence.py::TestTheKnownExceptionIsMeasured`; `known-limitations.md` §3 |
| **Simulation uses the production resolver** | **Sound, and structurally so.** `app/simulation/service.py` imports `AccessService`, `GraphService` and `EffectiveAccess` — the production ones — and `simulated_repositories` wraps the production repositories in an overlay. There is no simulation-specific access arithmetic to drift (ADR-0020). Checked not only against the engine that produced it but **against the estate**: apply the change fixture-side, recollect through three reconciling runs, and compare predicted with observed. | `app/simulation/repositories.py`; `tests/db/test_simulation_equivalence.py` |

---

## 5. Database review

**Migrations.** 17 revisions, verified from a genuinely empty database (`CREATE DATABASE`,
then `alembic upgrade head`) — 2.67 s to head.

**One head, despite four concurrent branches.** Phases 7B, 8A, 9A and 10A all forked off
`0007_history_model`, and 10B and 8B both forked off `0012`. Two merge revisions
(`0012_merge_concurrent_phases`, `0014_merge_alerts_and_reviews`) rejoin them, and
`alembic heads` reports exactly one: `0015_remediation_change_plans`. Three files share the
number `0008` and two share `0013`; they are *branches*, not duplicates, and the graph is
correct. The test conftest runs `upgrade heads` rather than `head` precisely so a future
in-flight branch does not make the suite refuse to choose.

**Parity with the declaration is a test, not a convention.**
`tests/db/test_schema.py::TestMigrationMatchesDeclaration` reflects the database the
migration actually produced and compares it with `app/models/schema.py`: every declared
table, every column with its nullability, every index, the idempotency keys as primary keys,
and **every timestamp column timezone-aware**.

**Constraints bite in the database**, not only in application code that could be bypassed —
invalid principal kinds, local groups without a host, self-edges, ACEs claiming both a level
and a mask, masks beyond 32 bits, negative order indexes, and — the governance ones —
append-only attestations enforced by a trigger and self-approval refused by a check
constraint.

**Indexes** are declared with the queries that need them, including partial indexes
(`valid_to IS NULL`, `revoked_at IS NULL`, `status IN ('pending','failed')`) so the hot paths
read small indexes. Query plans for the two expensive families are recorded in
`ad-graph-validation.md` and `ntfs-scan-performance.md`.

**Retention and backup.** Retention needs two switches and the destructive one defaults to
off; nothing in the API or the collectors calls the prune. Backup *and restore* are in the
runbook — restore was added by this audit (finding O-2).

---

## 6. Operational review

| Area | State |
| --- | --- |
| Fresh install | `bootstrap.ps1` → `stack-up.ps1`, documented end to end in `mvp-runbook.md` §2, with a verification section that does not assume success. |
| Upgrade | `stack-down` → `git pull` → `bootstrap` → `alembic upgrade head` → `stack-up`. Migrations are additive within contract v1; a 1.2 collector can post to a 1.3 API. |
| Collector deployment | Native Windows scheduled tasks; least privilege documented per collector; keys in `.env` on the API host, never in source control. The contract now tells an author how to authenticate (finding S-1). |
| Failure and retry | Specified normatively: 422 never retried, 409 never retried, idempotency on `batch_id`, and a partial run reconciles nothing. Exception handlers map domain failures to the codes the contract promises, centrally, so nothing leaks as a 500 that a collector would retry forever. |
| Monitoring | `/health/live`, `/health/ready` (probes PostgreSQL, 503 when down), `/version`, and — the one that matters — `GET /api/v1/collection/status`, the verdict every view consults before rendering an empty one. `GET /api/v1/collection/operations` is the operator page: last success and last failure per scope, what each run failed to deliver, and every reported error grouped by code. |
| Logs | One JSON object per line, redacted at the handler. Identifiers deliberately not redacted, which makes the stream personal data — see `known-limitations.md` §7.1. |
| Backup / restore | `pg_dump -Fc`; restore procedure added by this audit, including the `-AsByteStream -Raw` trap that silently corrupts a custom-format dump through a PowerShell pipeline, and the instruction to confirm `alembic current` afterwards. |

**Production defaults are refused at startup, not warned about**: development auth in
production, the development database URL in production, and any remediation execution mode
other than `disabled` in production all prevent the process from beginning.

---

## 7. Findings

| # | Sev | Finding | Status |
| --- | --- | --- | --- |
| C-1 | **High** | `GET /api/v1/changes/compare` returned **422** on an ordinary estate | **Fixed** |
| S-1 | **High** | The normative collector contract specified no authentication or transport | **Fixed** |
| P-1 | **High** | The access benchmark had been unable to run since Phase 6A, and pointed at the test database | **Fixed** |
| S-2 | Medium | The change-plan signing key had no minimum length | **Fixed** |
| Q-1 | Medium | `backend-lint.ps1` failed: 7 mypy errors, 3 in production code | **Fixed** |
| O-1 | Medium | The container stack could not configure any setting added after Phase 6 | **Fixed** |
| O-2 | Medium | The runbook named 3 of 8 roles, and documented backup with no restore | **Fixed** |
| S-3 | Low | Mutating web routes rely on `SameSite` rather than an explicit `Origin` check | Accepted |

### C-1 — a correct request answered with "your request is wrong"

The one that would have been reported as a product defect by the first customer to compare
two instants.

`ObjectChange` enforces an invariant from ADR-0019: a change whose action is
`FIRST_OBSERVED` must carry **no** change window, because a first observation is precisely
the case where nothing was watching and any lower bound would be invented. The invariant is
right.

What was wrong was that the classifier built exactly that object. Two container facts answer
different questions and can disagree:

* `action_for` asks whether the container **object** was in the record before this version
  opened;
* the window falls back to the newest confirmation of a **sibling** inside that container.

A scan stamps each observation at the instant it was read, so a group's member edge can open
at 08:01 while the group principal is first seen at 08:03 and another edge was already
confirmed at 08:00 — no container observed before (*first observation*) **and** a sibling
bound available (*a window*). The invariant then raised a `DomainValidationError`, which the
central handler maps to **422 Unprocessable Entity** — telling the caller to go and fix a
request that was perfectly well-formed.

Measured, not theorized: the audit hit it on the **first comparison it ran**, against the
benchmark estate.

**Fixed** in `app/changes/classify.py`: the *action* decides whether a lower bound is
meaningful, so a `FIRST_OBSERVED` change is given no window whatever a sibling confirmation
offers. Pinned by four tests — two unit, two against PostgreSQL including the API returning
`200` — and all four were confirmed to fail with the fix reverted.

> **Why it survived until now.** A test named
> `test_a_first_sighting_is_refused_a_window_even_if_one_is_offered` already existed. It
> never offered one: it passed `container_observed_before=False` and no
> `container_confirmed_at`, making it identical to the test above it. The name asserted the
> behavior; the body did not. It is now split into the case that has nothing to bound it and
> the case that is offered a bound and must still refuse.

### P-1 — a benchmark that had quietly stopped running

`tests/benchmarks/access_benchmark.py` builds its estate through the ingestion API with an
unauthenticated client. Ingestion has required a credential since Phase 6A, so **the
benchmark could not build an estate at all** and had not been able to for several phases.

Its harness test passed throughout, because it exercised the statistics, the parser and the
URL rule — every pure part — and never called `measure()`. The module's own docstring states
the risk exactly: *"A benchmark that has quietly stopped running is worse than no benchmark,
because the last recorded numbers still look current."*

The same module also pointed at `<database>_test` — the smoke test database — and truncates
what it points at. `graph_benchmark` had already drawn that conclusion and had a test pinning
it; these two disagreed, so running a benchmark during a test run would truncate the tables
out from under it.

**Fixed:** both benchmarks sign in; `access_benchmark` moved to `<database>_bench` and
creates and migrates it; and `TestItStillRunsEndToEnd` now runs `measure()` for real against
PostgreSQL, so this cannot rot silently again. The re-measured numbers reproduce the recorded
ones closely, which retroactively validates
`docs/architecture/effective-access-performance.md`.

### Q-1 — the lint suite was red

`backend-lint.ps1` exited 1 on 7 mypy errors, three of them in production code:
`app/changes/rules.py` imported `NtfsRight` from a module that re-imports but does not export
it (every other module imports it from `app.domain.access`), and `app/changes/impact.py`
returned `Any` from two functions declared to return `datetime`. Four were missing annotations
in test helpers. All fixed; the suite is clean across 361 files.

---

## 8. Acceptance criteria

| Criterion | Met | Evidence |
| --- | --- | --- |
| Clean install and migration tests pass | **Yes** | Empty database → head in 2.67 s; 45 parity/constraint tests against it; single head |
| All automated suites pass, or exceptions justified | **Yes** | §3 — nine suites, no exceptions needed |
| No production default permits AD/SMB/NTFS mutation | **Yes** | No write adapter exists at all; `remediation:execute` granted by no role and implemented by nothing; three production guards refuse at startup. Re-run during this audit: `tests/remediation/test_no_write_path.py` + `test_executor.py` + `test_isolation.py` — **143 passed**, across five independent guards (AST, imports, routes, roles, configuration), each of which is itself fed the mistake it exists to catch |
| Performance baselines measured, not guessed | **Yes** | `performance-baseline.md` — every figure produced by a committed harness on a named machine during this audit, including three areas never measured before |
| Security review lists minimum collector/service privileges | **Yes** | `security-review.md` §10 — per collector, the API service, the database account, and the person |

---

## 9. What would change the verdict

In the order that would most improve confidence:

1. **Run the AD and SMB collectors against a real domain.** The single largest gap. Until
   then the collection half of the product is evidenced by fixtures only.
2. **Model privileges.** `SeBackupPrivilege` bypasses the DACL, and ADG will report no access
   with nothing in the answer saying a privilege might apply — it **under-reports**, which is
   the dangerous direction for an auditing tool.
3. **Model deny-only SIDs.** ADG **over-reports** for members of local administrator groups.
4. **Measure the steady-state incremental risk pass** and the estate sizes above 400 shares.
   `performance-baseline.md` §3.3 is a specific warning against extrapolating the risk curve.
5. ~~**Route current-state reads through presence**, closing the live-versus-as-of divergence
   on removals.~~ **Done** — see
   [`current-state-presence.md`](../architecture/current-state-presence.md) and
   [`p0-current-state-correctness.md`](../handoffs/p0-current-state-correctness.md).
6. **Load and concurrency testing.** Every measurement is one client against an idle server.
