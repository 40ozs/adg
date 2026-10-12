/**
 * What the Cloudflare staging Worker needs to reach the ADG API, and what it hands Next.js.
 *
 * The API runs on Render (docs/deployment/cloudflare-demo.md): its demo configuration — the
 * runtime mode, the dataset, the database, remediation disabled, no collector key — is the
 * Render service's environment, not the Worker's. The Worker holds only where the API is
 * (`ADG_API_ORIGIN`) and the key the API's origin gate requires (`ADG_ORIGIN_KEY`), and it
 * relays nothing the API did not mark as a demo answer (`REQUIRED_RUNTIME_MODE`, worker.ts).
 *
 * Pure: no `cloudflare:*` import, so it is unit-tested in Node (`tests/cloudflare/`).
 */

/** The only runtime mode whose answers this deployment relays. */
export const REQUIRED_RUNTIME_MODE = "demo";
/** Where the Next.js server believes the API lives. Never resolved: the transport catches it. */
export const INTERNAL_API_URL = "http://adg-api.internal";
/** The header the API's origin gate (`python -m app.origin_gate`) checks. */
export const ORIGIN_KEY_HEADER = "X-ADG-Origin-Key";

/** The Worker's configuration as `wrangler.jsonc` vars, `--var` and secrets give it. */
export interface StagingVariables {
  ADG_WEB_URL?: string;
  ADG_API_ORIGIN?: string;
  ACCESS_TEAM_DOMAIN?: string;
  ACCESS_AUD?: string;
  // Secret.
  ADG_ORIGIN_KEY?: string;
}

/** The deployment cannot serve as configured. The message names settings, never values. */
export class StagingConfigurationError extends Error {
  override name = "StagingConfigurationError";
}

export interface ApiUpstream {
  /** `https://<service>.onrender.com`, no path. */
  origin: string;
  key: string;
}

/**
 * Where API requests go and the key they carry.
 *
 * Throws `StagingConfigurationError` rather than send a request anywhere but an https
 * origin, or send one the origin gate would refuse anyway.
 */
export function apiUpstream(variables: StagingVariables): ApiUpstream {
  const problems: string[] = [];
  let origin = "";
  try {
    const url = new URL(variables.ADG_API_ORIGIN?.trim() ?? "");
    if (url.protocol === "https:" && url.pathname === "/" && !url.search && !url.username) {
      origin = url.origin;
    }
  } catch {
    // reported below
  }
  if (!origin) {
    problems.push("ADG_API_ORIGIN must be an https origin with no path");
  }
  const key = variables.ADG_ORIGIN_KEY?.trim() ?? "";
  if (key.length < 32) {
    problems.push("the ADG_ORIGIN_KEY secret must be set (at least 32 characters)");
  }
  if (problems.length > 0) {
    throw new StagingConfigurationError(`ADG staging cannot reach its API: ${problems.join("; ")}.`);
  }
  return { origin, key };
}

/** Names the Next.js server reads, plus the bindings OpenNext itself uses. */
const FRONTEND_KEYS = ["ASSETS", "WORKER_SELF_REFERENCE", "IMAGES", "NEXTJS_ENV"] as const;

/**
 * The environment handed to the OpenNext handler.
 *
 * OpenNext copies every string in it into `process.env`, so this is also the whole of what
 * Next.js server code can read. The API address is the internal name the transport serves;
 * the public addresses are the staging origin. Neither the Render origin nor its key is in it.
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
