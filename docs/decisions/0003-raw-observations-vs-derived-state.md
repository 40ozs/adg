# ADR-0003: Raw observations and derived state are stored separately

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 0A — Formal permission domain model
- **Deciders:** ADG project

## Context

ADG holds two different kinds of statement:

* **Observations** — "this DACL contained an Allow ACE for `S-1-5-21-…-1202` with mask
  `0x1301BF`, inherited, at 08:00 UTC on 2026-09-14, read by COLLECTOR01 through
  `GetAccessControl`." Something was seen.
* **Conclusions** — "Alice can modify `\\FS01\Finance\Reports`." Something was computed from
  many observations by a specific version of a specific algorithm.

They have different truth conditions. An observation is wrong only if the collector
misreported. A conclusion is wrong if the algorithm is wrong, if an input was stale, or if
the environment changed since. They also age differently: an observation stays a true
record of a moment, while a conclusion becomes invalid as soon as any input changes.

If the two are stored in one place, three failures follow: a corrected algorithm cannot be
re-run because the inputs were overwritten by its output; "why do you believe this?" cannot
be answered; and a bug in the engine becomes indistinguishable from a bug in a collector.

## Decision

Raw observations and derived state are separate, and conversion in either direction is
forbidden.

1. **Raw facts** — ACEs, descriptor facts, membership edges, shares, directories,
   principals — are stored as observed, together with provenance: `ScanRun`,
   `ObservationSource` (collector, host, method, version), and `observed_at`. They are
   append-only records of what a collector saw.
2. **Collectors do not compute.** No collector resolves Deny precedence, expands groups,
   maps generic rights, or intersects the share and NTFS layers. An observation that has
   been "helpfully" simplified is no longer evidence.
3. **Nothing is normalized away.** Generic rights keep their `GENERIC_*` bits; unrecognized
   mask bits are retained and exposed (`unrecognized_bits`); a NULL DACL is recorded as
   absent rather than as an empty ACE list; an unresolvable SID stays on the ACL.
4. **Derived state is labeled and attributed.** Effective access, expanded membership, risk
   findings, and simulation results record which engine version produced them and which scan
   run supplied their inputs. They are recomputable from raw facts at any time.
5. **`Observation[FactT]` wraps raw facts only.** Wrapping a computed result in it would
   make a conclusion look like something a collector saw.
6. A run that encountered errors is `partial` or `failed`, never `succeeded`, so that
   incomplete coverage is never mistaken for complete coverage.

## Alternatives considered

| Alternative | Why it was rejected |
| --- | --- |
| Store only the computed effective access | Unfalsifiable and unexplainable; an engine fix would need a full re-collection of the estate. |
| Store only raw facts and compute everything per request | Correct but too slow for interactive use over millions of ACEs; caching is legitimate as long as it is labeled derived. |
| Let collectors pre-resolve rights (expand generic bits, flatten inheritance) | Bakes one interpretation into the record, and different collector versions would disagree in the same table. |
| One table with an `is_derived` column | The distinction is too important to rest on a column that a join can drop. |

## Consequences

**Positive**

- The engine can be corrected and re-run over history without re-collecting.
- Every answer is explainable down to the ACE and the scan run that produced it.
- Collector bugs and engine bugs are separable during investigation.
- Change detection compares like with like (Phase 7).

**Negative / accepted costs**

- More storage: the same information appears as inputs and as cached conclusions.
- Cache invalidation becomes a real design problem when a scan run supersedes another.
- Derived tables must carry an engine version, and that version must actually be bumped when
  semantics change.

**Follow-up required**

- Phase 4 must define the derived effective-access record, including engine version and
  input scan runs.
- Phase 7 must define observation validity windows so that "what was true at time T" is
  answerable from raw facts.

## Compliance

- `backend/app/domain/` contains no permission-resolution algorithm at all; §7 of
  `permission-domain-model.md` states what the engine must do later.
- `NtfsAce` preserves the raw mask, generic bits, and unrecognized bits; tests in
  `backend/tests/domain/test_access.py` pin that generic rights are not expanded.
- `Observation` is generic over raw fact types only.
- A review should reject any derived value written into a raw-fact table, and any collector
  that reports a conclusion instead of a reading.
