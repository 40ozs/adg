/**
 * Fixtures for the what-if screens.
 *
 * Every builder starts from the *least alarming* value — nothing changed, no caveat, no
 * truncation, the baseline current — so a test that cares about a warning has to put it
 * there. That is the right default here for the same reason the detail-view factories start
 * from the uninformative one: a fixture that shipped a caveat by accident would make a test
 * of "the caveat is rendered" pass whether or not the component renders it.
 */

import type {
  PrincipalSummary,
  RightsView,
  SimulationApplicationView,
  SimulationBaselineView,
  SimulationDeltaView,
  SimulationReportView,
  SimulationSummaryView,
  SimulationVocabularyResponse,
  StoredSimulationView,
} from "@/lib/contracts";

export const NOTICE =
  "NO CHANGES WILL BE APPLIED. This is a simulation: ADG computed the answer by reading " +
  "its own collected facts through a proposed change held in memory. Nothing was written " +
  "to Active Directory, to a share, or to an NTFS descriptor, and nothing in ADG's " +
  "collected state was altered.";

export const simPrincipal = (over: Partial<PrincipalSummary> = {}): PrincipalSummary => ({
  key: "S-1-5-21-1-2-3-1104",
  sid: "S-1-5-21-1-2-3-1104",
  host_key: null,
  kind: "user",
  resolved: true,
  is_group: false,
  display_name: "Alice Smith",
  sam_account_name: null,
  user_principal_name: null,
  distinguished_name: null,
  last_known_name: null,
  enabled: null,
  is_deleted: false,
  group_scope: null,
  group_type: null,
  unresolved_reason: null,
  ...over,
});

export const simRights = (over: Partial<RightsView> = {}): RightsView => ({
  mask: "0x00000000",
  value: 0,
  layer: "effective",
  label: "No access",
  primary: "none",
  categories: [],
  is_exact: true,
  extra_rights: [],
  escalation_rights: [],
  unrecognized_bits: null,
  indeterminate: false,
  ...over,
});

export const simBaseline = (
  over: Partial<SimulationBaselineView> = {},
): SimulationBaselineView => ({
  kind: "current",
  token: "a1b2c3",
  run_id: null,
  at: null,
  captured_at: "2026-09-14T10:00:00Z",
  is_empty: false,
  stale: false,
  current_token: "a1b2c3",
  ...over,
});

export const simSummary = (over: Partial<SimulationSummaryView> = {}): SimulationSummaryView => ({
  evaluated: 0,
  unchanged: 0,
  gained_access: 0,
  lost_access: 0,
  expanded: 0,
  reduced: 0,
  changed: 0,
  principals_gaining: [],
  principals_losing: [],
  principals_affected: 0,
  resources_affected: [],
  sensitive_resources_affected: [],
  watched_resources_affected: [],
  alternate_paths_retained: 0,
  ...over,
});

export const simDelta = (over: Partial<SimulationDeltaView> = {}): SimulationDeltaView => ({
  subject: simPrincipal(),
  resource: {
    resource_key: "\\\\fs01\\finance",
    share_key: "fs01|finance",
    path: "\\\\FS01\\Finance",
    sensitive: false,
    sensitivity_labels: [],
    watched: false,
  },
  access_path: "remote_smb",
  direction: "unchanged",
  direction_description: "Holds exactly what they hold today.",
  changed: false,
  rights_before: simRights(),
  rights_after: simRights(),
  rights_added: simRights(),
  rights_removed: simRights(),
  certainty_before: "certain",
  certainty_after: "certain",
  limiting_layer_after: "neither",
  caveats: [],
  alternate_path_retained: false,
  retained_routes: [],
  ...over,
});

export const simApplication = (
  over: Partial<SimulationApplicationView> = {},
): SimulationApplicationView => ({
  change: {
    kind: "remove_member",
    kind_description: "Take a principal out of a group.",
    description: "Remove S-1-5-21-1-2-3-1104 from S-1-5-21-1-2-3-1202 (directory_group_member).",
    document: { kind: "remove_member" },
    group: null,
    member: null,
    trustee: null,
  },
  outcome: "applied",
  outcome_description: "Applied to the baseline state.",
  applied: true,
  detail: {},
  ...over,
});

export const simReport = (over: Partial<SimulationReportView> = {}): SimulationReportView => ({
  notice: NOTICE,
  applied: false,
  simulation_id: null,
  overlay_hash: "0f1e2d",
  change_count: 1,
  baseline: simBaseline(),
  scope: {
    kind: "affected",
    subject_key: null,
    resource_key: null,
    path: "remote_smb",
    limit: 100,
    after: null,
  },
  bounds: {
    max_principals: 50,
    max_resources: 50,
    max_pairs: 500,
    max_explanations: 25,
    time_budget_ms: 10_000,
  },
  applications: [simApplication()],
  inert: false,
  summary: simSummary(),
  deltas: [],
  complete: true,
  truncation: [],
  cost: {
    pairs_evaluated: 0,
    resolutions: 0,
    explanations: 0,
    edges_read: 0,
    elapsed_ms: 3,
  },
  ...over,
});

export const storedSimulation = (
  over: Partial<StoredSimulationView> = {},
): StoredSimulationView => ({
  simulation_id: "00000000-0000-0000-0000-0000000000d1",
  name: "CHG-1042",
  description: null,
  created_by: "auditor@example.com",
  created_at: "2026-09-14T10:00:00Z",
  updated_at: "2026-09-14T10:00:00Z",
  change_count: 1,
  overlay_hash: "0f1e2d",
  changes: [simApplication().change],
  baseline: simBaseline(),
  ...over,
});

export const simVocabulary = (
  over: Partial<SimulationVocabularyResponse> = {},
): SimulationVocabularyResponse => ({
  notice: NOTICE,
  change_kinds: [{ code: "remove_member", description: "Take a principal out of a group." }],
  inherited_ace_dispositions: [],
  outcomes: [{ code: "applied", description: "Applied to the baseline state." }],
  directions: [],
  caveats: [
    {
      code: "loss_may_not_hold",
      description:
        "Rights are reported as removed, but the simulated answer is a lower bound: an " +
        "unseen grant or an unenumerated group could leave them in place.",
    },
  ],
  truncations: [],
  scope_kinds: [],
  max_changes: 50,
  bounds_ceilings: {
    max_principals: 500,
    max_resources: 500,
    max_pairs: 5000,
    max_explanations: 100,
    time_budget_ms: 60_000,
  },
  ...over,
});
