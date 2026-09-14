/**
 * The shapes the ADG API returns.
 *
 * Hand-written rather than generated, and then *checked* against the backend's published
 * OpenAPI document by `tests/contracts.test.ts`. Generation would produce a faithful but
 * unreadable surface (every field optional, every union widened) and would still not catch
 * the mistake that matters, which is the frontend believing in a field the backend stopped
 * sending. The check does catch that, and it fails in CI rather than in a browser.
 *
 * `docs/contracts/v1/openapi.json` is the snapshot both sides read. The backend's
 * `tests/contracts/test_openapi_snapshot.py` keeps it current.
 */

/** Roles and capabilities are strings on the wire; the backend owns the vocabulary. */
export type Capability =
  | "resources:read"
  | "identities:read"
  | "access:read"
  | "risks:read"
  | "changes:read"
  | "collectors:read"
  | "collectors:ingest"
  | "search"
  | "settings:read"
  | "settings:write"
  | "remediation:execute";

export interface RoleDescription {
  role: string;
  active: boolean;
  capabilities: string[];
}

export interface AuthConfig {
  mode: "oidc" | "development";
  development: boolean;
  environment: string;
  issuer: string | null;
  client_id: string | null;
  authorization_endpoint: string | null;
  token_endpoint: string | null;
  scopes: string[];
  development_accounts: string[];
  roles: RoleDescription[];
}

export interface Principal {
  subject: string;
  display_name: string | null;
  email: string | null;
  source: "oidc" | "development" | "collector_key";
  development: boolean;
  roles: string[];
  capabilities: string[];
  inactive_roles: string[];
  unrecognized_roles: string[];
  expires_at: string | null;
}

export interface DevelopmentLogin {
  access_token: string;
  token_type: "Bearer";
  expires_at: string;
  principal: Principal;
  warning: string;
}

/** The judgement every empty state consults. See `app/domain/collection.py`. */
export type CollectionHealth = "no_data" | "healthy" | "incomplete" | "failed";

export interface CollectorCoverage {
  collector: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  error_count: number;
  target: string | null;
  downgrade_reason: string | null;
  trustworthy: boolean;
  concern: string | null;
}

export interface CollectionStatus {
  health: CollectionHealth;
  summary: string;
  concerns: string[];
  collectors: CollectorCoverage[];
}

export interface PageInfo {
  limit: number;
  has_more: boolean;
  next_cursor: string | null;
  total: number | null;
}

export interface ScanRunSummary {
  run_id: string;
  status: string;
  incremental: boolean;
  started_at: string;
  completed_at: string | null;
  collector: string;
  collector_host: string;
  method: string;
  collector_version: string | null;
  target: string | null;
  batch_count_received: number;
  observation_count_applied: number;
  error_count: number;
  downgrade_reason: string | null;
}

export interface ScanRunList {
  items: ScanRunSummary[];
  page: PageInfo;
}

export interface IdentityHit {
  principal_key: string;
  sid: string;
  kind: string;
  display_name: string | null;
  sam_account_name: string | null;
  user_principal_name: string | null;
  host_key: string | null;
  enabled: boolean | null;
  is_deleted: boolean;
}

export interface ServerHit {
  server_key: string;
  name: string;
  dns_host_name: string | null;
}

export interface ShareHit {
  share_key: string;
  server_key: string;
  name: string;
  unc_path: string;
  share_type: string;
  description: string | null;
}

export interface DirectoryHit {
  resource_key: string;
  path: string;
  server_key: string;
  share_key: string;
  is_acl_boundary: boolean;
}

export interface SkippedCategory {
  category: string;
  reason: string;
}

export interface SearchResults {
  query: string;
  interpreted_as: "sid" | "unc_path" | "server" | "share" | "name";
  interpretation: string;
  limit_per_category: number;
  identities: IdentityHit[];
  servers: ServerHit[];
  shares: ShareHit[];
  directories: DirectoryHit[];
  truncated: string[];
  not_searched: SkippedCategory[];
}

export interface ServerSummary {
  key: string;
  name: string;
  dns_host_name: string | null;
  netbios_name: string | null;
  computer_sid: string | null;
  domain_sid: string | null;
  is_domain_member: boolean | null;
  operating_system: string | null;
  share_count: number;
}

export interface ServersResponse {
  items: ServerSummary[];
  page: PageInfo;
}
