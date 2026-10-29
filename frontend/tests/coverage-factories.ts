/**
 * Bodies for ADG-DEMO-03's tests, shaped like the reset Genesee Press estate's real responses:
 * 100 collection scopes, NTFS on GP-FS-03 unavailable since 2026-10-01 and holding the
 * 2026-09-17 scan's evidence, NTFS on GP-MIG-FS01 partial; 9,386 open findings; the
 * first-to-latest comparison drill timing out, stopping incomplete, and recovering.
 */

import type {
  ChangeComparisonResponse,
  CollectionCoverage,
  CoverageSummary,
  RiskSummaryResponse,
  ScopeCoverage,
} from "@/lib/contracts";
import type { BasisView } from "@/lib/derived";

export const APOLLO = "\\\\GP-ENG-FS01\\Engineering\\Projects\\Apollo";
export const SNAPSHOT: BasisView = {
  token: "3f2a9c41d07e4b5f8a6c2e91b0d47f53",
  runs: 300,
  latest_run_id: "8a1d6f5e-0000-4000-8000-000000000300",
  latest_activity_at: "2026-10-14T22:53:30Z",
  observations_applied: 41756,
  batches_received: 300,
  is_empty: false,
};

function scope(overrides: Partial<ScopeCoverage> & Pick<ScopeCoverage, "collector" | "target">): ScopeCoverage {
  const label = overrides.target === null ? overrides.collector : `${overrides.collector} (${overrides.target})`;
  return {
    label,
    state: "collected",
    evidence: "current",
    attempted_at: "2026-10-01T03:10:00Z",
    evidence_collected_at: "2026-10-01T03:14:00Z",
    observations: 10,
    returned_nothing: false,
    failure_codes: [],
    sentence: `${label}: collected completely.`,
    ...overrides,
  };
}

export const FS03 = scope({
  collector: "ntfs",
  target: "GP-FS-03",
  state: "unavailable",
  evidence: "stale",
  attempted_at: "2026-10-01T03:35:00Z",
  evidence_collected_at: "2026-09-17T03:39:00Z",
  observations: 0,
  failure_codes: ["host_unreachable"],
  sentence:
    "ntfs (GP-FS-03): the latest scan could not reach its target. ADG still holds what the run of 2026-09-17 collected; anything that changed since is not reflected.",
});

export const MIG = scope({
  collector: "ntfs",
  target: "GP-MIG-FS01",
  state: "partial",
  sentence:
    "ntfs (GP-MIG-FS01): the latest scan was partial. 2 declared scope(s) were not fully enumerated; 1 object(s) could not be read.",
});

const APOLLO_SCOPES = [
  scope({ collector: "active_directory", target: "corp.geneseepress.example", observations: 8264 }),
  scope({ collector: "local_groups", target: "GP-ENG-FS01", observations: 3 }),
  scope({ collector: "ntfs", target: "GP-ENG-FS01", observations: 385 }),
  scope({ collector: "smb", target: "GP-ENG-FS01", observations: 52 }),
];

const HEADLINE =
  "98 of 100 collection scopes collected completely · 1 unavailable · 1 partial · 1 holding older evidence";

export function coverageSummary(overrides: Partial<CoverageSummary> = {}): CoverageSummary {
  return {
    verdict: "incomplete",
    complete: false,
    headline: HEADLINE,
    counts: {
      scopes: 100,
      collected: 98,
      partial: 1,
      unavailable: 1,
      failed: 0,
      running: 0,
      stale: 1,
      never_collected: 0,
      returned_nothing: 0,
    },
    concerns: [FS03.sentence, MIG.sentence],
    snapshot: SNAPSHOT,
    ...overrides,
  };
}

export function apolloCoverage(overrides: Partial<CollectionCoverage> = {}): CollectionCoverage {
  return {
    ...coverageSummary(),
    health: "failed",
    expectation: "dataset",
    collectors: [
      { collector: "active_directory", expected: true, state: "reported", scopes: 1, collected: 1 },
      { collector: "local_groups", expected: true, state: "reported", scopes: 33, collected: 33 },
      { collector: "ntfs", expected: true, state: "reported", scopes: 33, collected: 31 },
      { collector: "smb", expected: true, state: "reported", scopes: 33, collected: 33 },
    ],
    scopes: [...APOLLO_SCOPES, FS03, MIG],
    relevant: {
      resource: APOLLO,
      server: "GP-ENG-FS01",
      verdict: "complete",
      complete: true,
      headline: "4 of 4 collection scopes collected completely",
      scopes: APOLLO_SCOPES,
      incomplete_elsewhere: 2,
      could_change_answer: false,
      statement:
        "Every scope this answer reads was collected completely in its latest scan (active_directory (corp.geneseepress.example), local_groups (GP-ENG-FS01), ntfs (GP-ENG-FS01), smb (GP-ENG-FS01)). 2 scope(s) elsewhere in the estate are not complete. None of them holds this folder, its share, its server's local groups or the directory, so they cannot change this answer.",
    },
    ...overrides,
  };
}

/** A folder on the server whose scan could not reach it: the answer could change. */
export function fs03Coverage(): CollectionCoverage {
  const base = apolloCoverage();
  return {
    ...base,
    relevant: {
      resource: "\\\\GP-FS-03\\Collaboration",
      server: "GP-FS-03",
      verdict: "incomplete",
      complete: false,
      headline: "1 of 2 collection scopes collected completely · 1 unavailable · 1 holding older evidence",
      scopes: [APOLLO_SCOPES[0], FS03],
      incomplete_elsewhere: 1,
      could_change_answer: true,
      statement: `Not every scope this answer reads is complete: ${FS03.sentence} The answer is computed from what ADG holds; a change it has not collected could change it.`,
    },
  };
}

export function riskSummary(overrides: Partial<RiskSummaryResponse> = {}): RiskSummaryResponse {
  return {
    coverage: {
      has_ever_run: true,
      evaluated_at: "2026-10-01T06:00:00Z",
      trigger: "full",
      complete: true,
      rules_run: ["everyone_has_access"],
      rules_skipped: [],
      truncation: null,
      evaluation_id: "61631d76-b97b-439a-b198-24d3ebab1876",
    },
    configuration: { version: "demo", rules_enabled: 11, rules_disabled: [], marks_anything_sensitive: true },
    severity_counts: { critical: 3, high: 8, medium: 1957, low: 423, informational: 6995 },
    rule_counts: {},
    status_counts: { open: 9386, resolved: 0 },
    total: 9386,
    collection: coverageSummary(),
    interpretation: {
      reading: "incomplete",
      exhaustive: false,
      reasons: [
        `Collection is incomplete: ${HEADLINE}. Findings for scopes that were not read completely can be missing, and findings held for a scope whose latest scan failed date from its last successful scan.`,
      ],
      evaluated_basis: SNAPSHOT.token,
      basis_current: true,
      held: [
        {
          scope: "ntfs (GP-FS-03)",
          server: "GP-FS-03",
          evidence: "stale",
          evidence_collected_at: "2026-09-17T03:39:00Z",
          open_findings: 189,
        },
        {
          scope: "ntfs (GP-MIG-FS01)",
          server: "GP-MIG-FS01",
          evidence: "current",
          evidence_collected_at: "2026-10-01T04:34:00Z",
          open_findings: 101,
        },
      ],
    },
    ...overrides,
  };
}

export function completeRiskSummary(): RiskSummaryResponse {
  return riskSummary({
    collection: coverageSummary({
      verdict: "complete",
      complete: true,
      headline: "100 of 100 collection scopes collected completely",
      counts: { ...coverageSummary().counts, collected: 100, partial: 0, unavailable: 0, stale: 0 },
      concerns: [],
    }),
    interpretation: {
      reading: "complete",
      exhaustive: true,
      reasons: [],
      evaluated_basis: SNAPSHOT.token,
      basis_current: true,
      held: [],
    },
  });
}

export const DRILL = {
  key: "first-to-latest",
  title: "What is different between the first scan and the latest?",
  question: "What is different across the estate between 2026-09-03 and 2026-10-01?",
  at_from: "2026-09-03T06:00:00Z",
  at_to: "2026-10-01T06:00:00Z",
  drill_window_ms: 1,
  summary:
    "The estate-wide comparison of ADG's first and latest scans, launched inside a deliberately short interactive window.",
};

const FIRST_ID = "aaaaaaaa-0000-4000-8000-000000000001";
const SECOND_ID = "aaaaaaaa-0000-4000-8000-000000000002";

function comparison(overrides: Partial<ChangeComparisonResponse>): ChangeComparisonResponse {
  return {
    at_from: DRILL.at_from,
    at_to: DRILL.at_to,
    scope_target: null,
    scope_key: null,
    changes: [],
    edits: [],
    unchanged: 0,
    unobserved_at_from: 0,
    unobserved_at_to: 0,
    truncated: true,
    summary: {
      window_from: DRILL.at_from,
      window_to: DRILL.at_to,
      total: 0,
      returned: 0,
      excluded: 0,
      highest_severity: null,
      reconstructed: 0,
      truncated: false,
      by_action: {},
      by_significance: {},
      by_severity: {},
      by_kind: {},
    },
    comparison_id: FIRST_ID,
    outcome: "complete",
    complete: false,
    recovered: false,
    retry_of: null,
    stopped_in: null,
    compared_through: null,
    requested_scope: { target: null, key: null, kinds: [] },
    window_ms: 4000,
    elapsed_ms: 180,
    objects_per_side: 5000,
    objects_read_from: 4898,
    objects_read_to: 5000,
    differences_found: 0,
    recovery: [],
    ...overrides,
  };
}

const NARROW_NTFS = {
  action: "narrow_kind" as const,
  label: "Compare only folder permission entries",
  parameters: { time_budget_ms: "4000", retry_of: SECOND_ID, kind: "ntfs_ace" },
  objects_at_from: 3947,
  objects_at_to: 4124,
};

export function timedOut(): ChangeComparisonResponse {
  return comparison({
    outcome: "timed_out",
    complete: false,
    stopped_in: "read",
    window_ms: 1,
    elapsed_ms: 174,
    recovery: [
      {
        action: "retry_standard_window",
        label: "Retry inside the standard 4-second window",
        parameters: { time_budget_ms: "4000", retry_of: FIRST_ID },
        objects_at_from: null,
        objects_at_to: null,
      },
      { ...NARROW_NTFS, parameters: { ...NARROW_NTFS.parameters, retry_of: FIRST_ID } },
    ],
  });
}

export function incomplete(): ChangeComparisonResponse {
  return comparison({
    comparison_id: SECOND_ID,
    retry_of: FIRST_ID,
    outcome: "incomplete",
    complete: false,
    compared_through: {
      kind: "membership_edge",
      key: "S-1-5-21-3949361974-1583827165-633756269-513->S-1-5-21-3949361974-1583827165-633756269-2325|primary_group",
    },
    unchanged: 4855,
    unobserved_at_from: 102,
    differences_found: 145,
    recovery: [NARROW_NTFS],
  });
}

export function recovered(): ChangeComparisonResponse {
  return comparison({
    comparison_id: "aaaaaaaa-0000-4000-8000-000000000003",
    retry_of: SECOND_ID,
    outcome: "complete",
    complete: true,
    recovered: true,
    truncated: false,
    requested_scope: { target: null, key: null, kinds: ["ntfs_ace"] },
    unchanged: 3903,
    unobserved_at_from: 177,
    objects_read_from: 3947,
    objects_read_to: 4124,
    differences_found: 221,
  });
}

export function fromAnOlderServer(truncated: boolean): ChangeComparisonResponse {
  const body = comparison({ truncated });
  for (const key of [
    "comparison_id",
    "outcome",
    "complete",
    "recovered",
    "retry_of",
    "stopped_in",
    "compared_through",
    "requested_scope",
    "window_ms",
    "elapsed_ms",
    "objects_per_side",
    "objects_read_from",
    "objects_read_to",
    "differences_found",
    "recovery",
  ] as const) {
    delete body[key];
  }
  return body;
}
