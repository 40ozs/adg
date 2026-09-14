/**
 * The frontend's response types, checked against the backend's published contract.
 *
 * `lib/contracts.ts` is hand-written, which is only defensible if something proves it still
 * matches the API. That something is this file: it reads `docs/contracts/v1/openapi.json` —
 * kept current by the backend's own `test_openapi_snapshot.py` — and asserts, field by
 * field, that every property this application believes in is a property the API declares,
 * and that no required property has been dropped.
 *
 * The failure it exists to catch is the quiet one: the API stops sending a field, the
 * TypeScript type still declares it, `tsc` agrees with the type, and the page renders
 * `undefined` where an auditor expected a SID.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { USED_PATHS } from "@/lib/api/adg";

interface Schema {
  type?: string;
  properties?: Record<string, Schema>;
  required?: string[];
  $ref?: string;
  anyOf?: Schema[];
  allOf?: Schema[];
  items?: Schema;
}

interface OpenApiDocument {
  paths: Record<string, Record<string, unknown>>;
  components: { schemas: Record<string, Schema> };
}

const document = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../docs/contracts/v1/openapi.json", import.meta.url)),
    "utf-8",
  ),
) as OpenApiDocument;

/** Field names this application reads, by the API schema they come from. */
const EXPECTED: Record<string, string[]> = {
  AuthConfigResponse: [
    "mode",
    "development",
    "environment",
    "issuer",
    "client_id",
    "authorization_endpoint",
    "token_endpoint",
    "scopes",
    "development_accounts",
    "roles",
  ],
  RoleDescription: ["role", "active", "capabilities"],
  PrincipalView: [
    "subject",
    "display_name",
    "email",
    "source",
    "development",
    "roles",
    "capabilities",
    "inactive_roles",
    "unrecognized_roles",
    "expires_at",
  ],
  DevelopmentLoginResponse: ["access_token", "token_type", "expires_at", "principal", "warning"],
  CollectionStatusResponse: ["health", "summary", "concerns", "collectors"],
  CollectorCoverageView: [
    "collector",
    "status",
    "started_at",
    "completed_at",
    "error_count",
    "target",
    "downgrade_reason",
    "trustworthy",
    "concern",
  ],
  ScanRunListResponse: ["items", "page"],
  ScanRunSummaryView: [
    "run_id",
    "status",
    "incremental",
    "started_at",
    "completed_at",
    "collector",
    "collector_host",
    "method",
    "collector_version",
    "target",
    "batch_count_received",
    "observation_count_applied",
    "error_count",
    "downgrade_reason",
  ],
  PageInfo: ["limit", "has_more", "next_cursor", "total"],
  SearchResponse: [
    "query",
    "interpreted_as",
    "interpretation",
    "limit_per_category",
    "identities",
    "servers",
    "shares",
    "directories",
    "truncated",
    "not_searched",
  ],
  IdentityHitView: [
    "principal_key",
    "sid",
    "kind",
    "display_name",
    "sam_account_name",
    "user_principal_name",
    "host_key",
    "enabled",
    "is_deleted",
  ],
  ServerHitView: ["server_key", "name", "dns_host_name"],
  ShareHitView: ["share_key", "server_key", "name", "unc_path", "share_type", "description"],
  DirectoryHitView: ["resource_key", "path", "server_key", "share_key", "is_acl_boundary"],
  SkippedCategoryView: ["category", "reason"],
  ServersResponse: ["items", "page"],
  ServerSummary: [
    "key",
    "name",
    "dns_host_name",
    "netbios_name",
    "computer_sid",
    "domain_sid",
    "is_domain_member",
    "operating_system",
    "share_count",
  ],
  ReadinessResponse: ["status", "version", "dependencies"],
  DependencyStatus: ["name", "ok", "latency_ms", "error"],
  VersionResponse: ["name", "version", "environment"],
};

describe("the published contract", () => {
  it("is present and readable", () => {
    expect(Object.keys(document.paths).length).toBeGreaterThan(20);
  });

  it("serves every path this application calls", () => {
    const served = new Set(Object.keys(document.paths));
    const missing = USED_PATHS.filter((path) => !served.has(path));

    expect(missing).toEqual([]);
  });
});

describe.each(Object.entries(EXPECTED))("the %s schema", (name, fields) => {
  it("is declared by the API", () => {
    expect(document.components.schemas[name]).toBeDefined();
  });

  it("declares every field this application reads", () => {
    const declared = Object.keys(document.components.schemas[name]?.properties ?? {});
    const missing = fields.filter((field) => !declared.includes(field));

    expect(missing, `${name} is missing: ${missing.join(", ")}`).toEqual([]);
  });

  it("has no required field this application ignores", () => {
    // The direction that catches a *new* field the frontend should be showing, rather than
    // an old one it should stop showing.
    const required = document.components.schemas[name]?.required ?? [];
    const ignored = required.filter((field) => !fields.includes(field));

    expect(ignored, `${name} requires fields lib/contracts.ts does not carry`).toEqual([]);
  });
});

describe("the search response", () => {
  it("declares exactly the interpretations the union allows", () => {
    // `interpreted_as` is a TypeScript union; a value the API can send and the union cannot
    // hold would be a type error only at runtime.
    const schema = document.components.schemas.SearchResponse?.properties?.interpreted_as as
      | { enum?: string[] }
      | undefined;

    expect(schema?.enum?.sort()).toEqual(
      ["name", "server", "share", "sid", "unc_path"].sort(),
    );
  });
});

describe("the collection status", () => {
  it("declares exactly the health values the union allows", () => {
    const schema = document.components.schemas.CollectionStatusResponse?.properties?.health as
      | { enum?: string[] }
      | undefined;

    expect(schema?.enum?.sort()).toEqual(["failed", "healthy", "incomplete", "no_data"].sort());
  });
});
