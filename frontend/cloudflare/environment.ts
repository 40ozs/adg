/**
 * What each half of the Cloudflare staging deployment is given, and nothing more.
 *
 * The Worker's own environment holds everything Cloudflare was told: the demo settings, the
 * database URL, the development-session secret, the Access configuration. Neither half gets
 * that whole: the API Container receives an explicit allow-list (`containerEnvironment`),
 * and the Next.js server receives only what it reads (`frontendEnvironment`). A secret
 * added to the Worker for some other reason therefore reaches neither.
 *
 * The settings that make a demo a demo are **pinned here, not read**: whatever the Worker's
 * configuration says, the Container starts with no collector key, no alert-policy file, no
 * lab fixture and remediation execution disabled. The API refuses each of those in demo
 * mode anyway (`app/config.py`); pinning them means a mistyped variable cannot even reach
 * that refusal.
 *
 * Pure: no `cloudflare:*` import, so it is unit-tested in Node (`tests/cloudflare/`).
 */

/** The only runtime mode this deployment serves. */
export const REQUIRED_RUNTIME_MODE = "demo";
/** The canonical staging dataset. Another registered dataset is a different deployment. */
export const REQUIRED_DATASET = "null-meridian-v1";
/** Where the Next.js server believes the API lives. Never resolved: the transport catches it. */
export const INTERNAL_API_URL = "http://adg-api.internal";

/** The Worker's configuration as `wrangler.jsonc` vars and `wrangler secret put` give it. */
export interface StagingVariables {
  ADG_RUNTIME_MODE?: string;
  ADG_DEMO_DATASET?: string;
  ADG_ENVIRONMENT?: string;
  ADG_AUTH_MODE?: string;
  ADG_LOG_LEVEL?: string;
  ADG_LOG_FORMAT?: string;
  ADG_WEB_URL?: string;
  ADG_DEV_AUTH_USERS?: string;
  ADG_CONTAINER_SLEEP_AFTER?: string;
  ACCESS_TEAM_DOMAIN?: string;
  ACCESS_AUD?: string;
  // Secrets.
  ADG_DATABASE_URL?: string;
  ADG_DEV_AUTH_SECRET?: string;
  ADG_REMEDIATION_SIGNING_KEY?: string;
}

/** The deployment cannot serve as configured. The message names settings, never values. */
export class StagingConfigurationError extends Error {
  override name = "StagingConfigurationError";
}

/**
 * The Container's process environment.
 *
 * Throws `StagingConfigurationError` instead of starting an API that would be anything
 * other than a Null Meridian demo on its own database.
 */
export function containerEnvironment(variables: StagingVariables): Record<string, string> {
  const problems: string[] = [];
  const mode = variables.ADG_RUNTIME_MODE?.trim();
  if (mode !== REQUIRED_RUNTIME_MODE) {
    problems.push(`ADG_RUNTIME_MODE must be "${REQUIRED_RUNTIME_MODE}"`);
  }
  const dataset = variables.ADG_DEMO_DATASET?.trim();
  if (dataset !== REQUIRED_DATASET) {
    problems.push(`ADG_DEMO_DATASET must be "${REQUIRED_DATASET}"`);
  }
  const databaseUrl = variables.ADG_DATABASE_URL?.trim();
  if (!databaseUrl) {
    problems.push("the ADG_DATABASE_URL secret is not set");
  }
  const authMode = variables.ADG_AUTH_MODE?.trim() || "development";
  const devSecret = variables.ADG_DEV_AUTH_SECRET?.trim();
  // Without a configured secret the API generates one per process, so every Container
  // restart, sleep or second instance would sign people out mid-review.
  if (authMode === "development" && (!devSecret || devSecret.length < 32)) {
    problems.push("the ADG_DEV_AUTH_SECRET secret must be set (at least 32 characters)");
  }
  if (problems.length > 0) {
    throw new StagingConfigurationError(
      `ADG staging is not configured to serve a demo: ${problems.join("; ")}.`,
    );
  }

  const environment: Record<string, string> = {
    ADG_RUNTIME_MODE: REQUIRED_RUNTIME_MODE,
    ADG_DEMO_DATASET: REQUIRED_DATASET,
    ADG_DATABASE_URL: databaseUrl as string,
    ADG_ENVIRONMENT: variables.ADG_ENVIRONMENT?.trim() || "development",
    ADG_AUTH_MODE: authMode,
    ADG_LOG_LEVEL: variables.ADG_LOG_LEVEL?.trim() || "INFO",
    ADG_LOG_FORMAT: variables.ADG_LOG_FORMAT?.trim() || "json",
    // The browser never calls the API cross-origin (the web tier proxies), so the only
    // origin worth allowing is the staging origin itself.
    ADG_CORS_ALLOW_ORIGINS: variables.ADG_WEB_URL?.trim() || "",
    // Pinned. See the module comment.
    ADG_COLLECTOR_API_KEYS: "",
    ADG_ALERT_POLICY_PATH: "",
    ADG_RISK_CONFIGURATION_PATH: "",
    ADG_REMEDIATION_EXECUTION_MODE: "disabled",
    ADG_REMEDIATION_LAB_FIXTURE_PATH: "",
    ADG_ALERTS_ON_RUN_COMPLETION: "false",
    ADG_HISTORY_RETENTION_ENABLED: "false",
  };
  if (devSecret) {
    environment.ADG_DEV_AUTH_SECRET = devSecret;
  }
  const devUsers = variables.ADG_DEV_AUTH_USERS?.trim();
  if (devUsers) {
    environment.ADG_DEV_AUTH_USERS = devUsers;
  }
  const signingKey = variables.ADG_REMEDIATION_SIGNING_KEY?.trim();
  if (signingKey) {
    environment.ADG_REMEDIATION_SIGNING_KEY = signingKey;
  }
  return environment;
}

/** Names the Next.js server reads, plus the bindings OpenNext itself uses. */
const FRONTEND_KEYS = ["ASSETS", "WORKER_SELF_REFERENCE", "IMAGES", "NEXTJS_ENV"] as const;

/**
 * The environment handed to the OpenNext handler.
 *
 * OpenNext copies every string in it into `process.env`, so this is also the whole of what
 * Next.js server code can read. The API address is the internal name the transport serves;
 * the public addresses are the staging origin.
 */
export function frontendEnvironment<E extends object>(env: E): Record<string, unknown> {
  const source = env as Record<string, unknown>;
  const selected: Record<string, unknown> = {};
  for (const key of FRONTEND_KEYS) {
    if (source[key] !== undefined) {
      selected[key] = source[key];
    }
  }
  const webUrl = typeof source.ADG_WEB_URL === "string" ? source.ADG_WEB_URL.trim() : "";
  selected.ADG_INTERNAL_API_URL = INTERNAL_API_URL;
  if (webUrl) {
    selected.ADG_WEB_URL = webUrl;
    selected.NEXT_PUBLIC_ADG_API_URL = webUrl;
  }
  return selected;
}
