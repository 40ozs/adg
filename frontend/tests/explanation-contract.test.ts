/**
 * `lib/derived.ts`, checked against the schema the backend publishes.
 *
 * The same instrument as `tests/contracts.test.ts` and for the same reason, pointed at the
 * other direction of the contract. `docs/contracts/v1/derived/access-explanation.schema.json`
 * is **generated** from the models the API serializes, so it cannot describe a response
 * nobody sends; these tests assert that every field this application reads is a field that
 * schema declares, and — the direction that matters more — that no required field has
 * appeared which the frontend is quietly ignoring.
 *
 * The failure this exists to catch is the quiet one. The API stops sending
 * `verdict.conclusive`, the TypeScript type still declares it, `tsc` agrees with the type,
 * and a screen renders "No access" for an answer that does not support the conclusion.
 *
 * The last block does something the field lists cannot: it runs the real captured response
 * body through every rendering function on the way to the screen. A payload that parses
 * and then throws two functions later is still a broken screen.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { EXPLAIN_PATHS } from "@/lib/api/explain";
import type { AccessExplanationResponse } from "@/lib/derived";
import {
  aceFor,
  alternateRouteWarning,
  cautions,
  describePath,
  describeRemoval,
  explanationJson,
  explanationText,
  layoutGraph,
  outcomeWording,
  structureRows,
} from "@/lib/explanation";

interface Schema {
  type?: string;
  const?: string;
  enum?: string[];
  properties?: Record<string, Schema>;
  required?: string[];
  $ref?: string;
  $defs?: Record<string, Schema>;
  anyOf?: Schema[];
  items?: Schema;
  "x-contract-version"?: string;
}

const derivedDirectory = new URL("../../docs/contracts/v1/derived/", import.meta.url);

function read<T>(relative: string): T {
  return JSON.parse(
    readFileSync(fileURLToPath(new URL(relative, derivedDirectory)), "utf-8"),
  ) as T;
}

const explanationSchema = read<Schema>("access-explanation.schema.json");
const pathsSchema = read<Schema>("access-paths.schema.json");
const example = read<AccessExplanationResponse>("examples/access-explanation.json");
const openapi = read<{ paths: Record<string, unknown> }>("../openapi.json");

const defs = explanationSchema.$defs ?? {};

/** Field names this application reads, by the schema they come from. */
const EXPECTED: Record<string, string[]> = {
  VerdictView: [
    "outcome",
    "certainty",
    "conclusive",
    "may_overstate",
    "may_understate",
    "reason",
    "denials",
  ],
  AppliedAceView: [
    "layer",
    "position",
    "ace_type",
    "ace_key",
    "access_mask",
    "contributed",
    "flags",
    "source",
    "inherited_from",
    "matched_key",
    "trustee",
    "via_group",
  ],
  AclEvaluationView: [
    "layer",
    "rights",
    "canonical_rights",
    "entries_supplied",
    "entries_evaluated",
    "order_dependent",
    "owner_rights",
    "granted_by",
    "denied_by",
    "superseded",
  ],
  EffectiveAccessView: [
    "resource_key",
    "share_key",
    "access_path",
    "access",
    "rights",
    "certainty",
    "limiting_layer",
    "acl_provenance",
    "conditions",
    "findings",
    "ntfs",
    "share",
  ],
  ExplanationNodeView: ["id", "kind", "key", "sid", "display_name", "layer", "position"],
  ExplanationEdgeView: [
    "id",
    "kind",
    "source",
    "target",
    "removable",
    "membership_edge_key",
    "ace_key",
  ],
  ExplanationGraphView: ["nodes", "edges"],
  CausalPathView: [
    "id",
    "layer",
    "relation",
    "effect",
    "ace_rights",
    "layer_rights",
    "constrained_rights",
    "effective_rights",
    "ace_key",
    "ace_position",
    "inherited",
    "via_group",
    "assumed",
    "chain",
    "nodes",
    "edges",
  ],
  RemovalTargetView: [
    "edge_id",
    "kind",
    "source",
    "target",
    "rights_after",
    "rights_removed",
    "rights_added",
    "changes_nothing",
    "revokes_all_access",
    "paths_removed",
    "alternate_paths",
  ],
  ExplanationLimitsView: [
    "max_depth",
    "max_paths",
    "max_paths_per_trustee",
    "max_removal_targets",
  ],
  BasisView: [
    "token",
    "runs",
    "latest_run_id",
    "latest_activity_at",
    "observations_applied",
    "batches_received",
    "is_empty",
  ],
  // Carried inside a derived body as well as by the listing endpoints. Checked against both
  // published descriptions, because two descriptions of one shape is one that can drift.
  RightsView: [
    "mask",
    "value",
    "layer",
    "label",
    "primary",
    "categories",
    "is_exact",
    "extra_rights",
    "escalation_rights",
    "unrecognized_bits",
    "indeterminate",
  ],
  TokenView: ["subject", "assumption", "access_path", "membership_complete", "entries"],
  TokenEntryView: ["principal", "origin", "assumed", "depth", "path"],
  FindingView: ["condition", "message", "may_overstate", "may_understate", "detail"],
  ResourceRef: [
    "key",
    "path",
    "share_key",
    "observed",
    "owner_sid",
    "dacl_present",
    "dacl_protected",
    "is_acl_boundary",
  ],
  ShareRef: ["key", "name", "server_key", "observed"],
};

/** The response body itself. */
const TOP_LEVEL = [
  "schema_version",
  "basis",
  "subject",
  "resource",
  "share",
  "access_path",
  "verdict",
  "token",
  "effective",
  "graph",
  "paths",
  "removal_targets",
  "cycles",
  "limits",
  "complete",
  "truncation",
  "warnings",
];

describe("the derived contract", () => {
  it("is the version lib/derived.ts was written against", () => {
    // A major bump means a new directory and a new endpoint prefix; a minor bump is
    // additive and safe. Either way it is a deliberate edit here, not a surprise at runtime.
    expect(explanationSchema["x-contract-version"]).toBe("1.0");
    expect(explanationSchema.properties?.schema_version?.const).toBe("1.0");
  });

  it("serves every derived path this application calls", () => {
    const served = new Set(Object.keys(openapi.paths));

    expect(EXPLAIN_PATHS.filter((path) => !served.has(path))).toEqual([]);
  });

  it("declares every top-level field the explanation screen reads", () => {
    const declared = Object.keys(explanationSchema.properties ?? {});

    expect(TOP_LEVEL.filter((field) => !declared.includes(field))).toEqual([]);
  });

  it("requires no top-level field this application ignores", () => {
    const required = explanationSchema.required ?? [];

    expect(required.filter((field) => !TOP_LEVEL.includes(field))).toEqual([]);
  });

  it("pages the same causal path shape from the paths endpoint", () => {
    // Two endpoints, one path shape. If they ever diverge, a client that pages gets a
    // different object than one that does not.
    expect(pathsSchema.$defs?.CausalPathView).toEqual(defs.CausalPathView);
  });
});

describe.each(Object.entries(EXPECTED))("the %s schema", (name, fields) => {
  it("is declared by the API", () => {
    expect(defs[name]).toBeDefined();
  });

  it("declares every field this application reads", () => {
    const declared = Object.keys(defs[name]?.properties ?? {});
    const missing = fields.filter((field) => !declared.includes(field));

    expect(missing, `${name} is missing: ${missing.join(", ")}`).toEqual([]);
  });

  it("has no required field this application ignores", () => {
    const required = defs[name]?.required ?? [];
    const ignored = required.filter((field) => !fields.includes(field));

    expect(ignored, `${name} requires fields lib/derived.ts does not carry`).toEqual([]);
  });
});

describe("the vocabulary a client branches on", () => {
  // Every one of these decides wording, and a value the API can send that the union cannot
  // hold falls through a `switch` at runtime — in the one place this product must not be
  // vague. TypeScript checks the switches are exhaustive over the union; these check the
  // union is exhaustive over the API.
  it.each([
    ["AccessOutcome", ["denied", "granted", "indeterminate", "no_grant"]],
    ["AccessCertainty", ["at_least", "at_most", "certain", "uncertain"]],
    ["PathEffect", ["constrained", "contributes", "redundant"]],
    ["PathRelation", ["deny", "grant"]],
    ["AclProvenance", ["derived", "observed", "unobserved"]],
    ["LimitingLayer", ["both", "none", "ntfs", "smb_share", "unknown"]],
    [
      "NodeKind",
      ["assumed_trustee", "group", "ntfs_ace", "principal", "resource", "share", "smb_ace"],
    ],
    [
      "EdgeKind",
      ["assumed_membership", "deny", "grant", "membership", "ownership", "trustee"],
    ],
  ])("declares exactly the %s values the union allows", (name, expected) => {
    expect(defs[name]?.enum?.slice().sort()).toEqual(expected);
  });
});

describe("the captured response body", () => {
  // Real bytes from a real request, rewritten only under ADG_WRITE_EXAMPLES=1. A field
  // list cannot catch a payload that parses and then breaks a renderer two calls later.
  it("carries only fields this application knows about", () => {
    expect(Object.keys(example).filter((key) => !TOP_LEVEL.includes(key))).toEqual([]);
  });

  it("produces a verdict wording", () => {
    expect(outcomeWording(example.verdict).word.length).toBeGreaterThan(0);
  });

  it("lays out without losing a node or an edge", () => {
    const layout = layoutGraph(example.graph);

    expect(layout.nodes).toHaveLength(example.graph.nodes.length);
    expect(layout.edges).toHaveLength(example.graph.edges.length);
  });

  it("lists in the table exactly what the diagram draws", () => {
    expect(structureRows(example).map((row) => row.edgeId).sort()).toEqual(
      layoutGraph(example.graph).edges.map((laid) => laid.edge.id).sort(),
    );
  });

  it("opens every route it carries, and finds the entry behind each one", () => {
    expect(example.paths.length).toBeGreaterThan(0);

    for (const path of example.paths) {
      const detail = describePath(example, path.id);

      expect(detail, `route ${path.id} could not be opened`).not.toBeNull();
      // One hop per edge, plus the subject. A step count that drifts from this means the
      // response referenced an edge the graph does not carry.
      expect(detail?.steps).toHaveLength(path.edges.length + 1);
      if (path.ace_key !== null) {
        expect(aceFor(example, path.ace_key), `no entry for ${path.ace_key}`).not.toBeNull();
      }
    }
  });

  it("describes every removal it measured", () => {
    for (const target of example.removal_targets) {
      expect(describeRemoval(example, target).headline.length).toBeGreaterThan(0);
    }
  });

  it("reports the assumption it made as a caveat", () => {
    expect(cautions(example).map((note) => note.id)).toContain("finding-assumed_token_sids");
  });

  it("exports as text and as its own JSON", () => {
    expect(explanationText(example)).toContain("ADG access explanation");
    expect(JSON.parse(explanationJson(example))).toEqual(example);
  });

  it("has nothing to warn about when every route is a real one", () => {
    // The captured example is complete and every measured removal does something, so the
    // warning must be absent. A warning that is always on is a warning nobody reads.
    expect(alternateRouteWarning(example)).toBeNull();
  });
});
