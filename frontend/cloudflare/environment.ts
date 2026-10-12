/**
 * What the Cloudflare staging Worker needs to reach the ADG demo's Render service.
 *
 * Render runs both Next.js and the ADG API behind one origin gate (`render/gate.ts`,
 * docs/deployment/cloudflare-demo.md). Their demo configuration (runtime mode, dataset,
 * database, remediation disabled, no collector key) is the Render service's environment, not the
 * Worker's. The Worker holds only where the service is (`ADG_RENDER_ORIGIN`) and the key its gate
 * requires (`ADG_ORIGIN_KEY`). It relays no API answer that the API did not mark as a demo answer
 * (`REQUIRED_RUNTIME_MODE`, routing.ts).
 *
 * Pure: no `cloudflare:*` import, so it is unit-tested in Node (`tests/cloudflare/`).
 */

/** The only runtime mode whose API answers this deployment relays. */
export const REQUIRED_RUNTIME_MODE = "demo";
/** The header the Render service's origin gate checks. */
export const ORIGIN_KEY_HEADER = "X-ADG-Origin-Key";

/** The Worker's configuration as `wrangler.jsonc` vars, `--var` and secrets give it. */
export interface StagingVariables {
  ADG_RENDER_ORIGIN?: string;
  // Secret.
  ADG_ORIGIN_KEY?: string;
}

/** The deployment cannot serve as configured. The message names settings, never values. */
export class StagingConfigurationError extends Error {
  override name = "StagingConfigurationError";
}

export interface OriginUpstream {
  /** `https://<service>.onrender.com`, no path. */
  origin: string;
  key: string;
}

/** Plain http is accepted for loopback only: `wrangler dev` against the image run locally. */
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);

/**
 * Where requests go and the key they carry.
 *
 * Throws `StagingConfigurationError` rather than send a request anywhere but an https origin
 * (or a loopback one), or send one the origin gate would refuse anyway.
 */
export function originUpstream(variables: StagingVariables): OriginUpstream {
  const problems: string[] = [];
  let origin = "";
  try {
    const url = new URL(variables.ADG_RENDER_ORIGIN?.trim() ?? "");
    const secure = url.protocol === "https:" || (url.protocol === "http:" && LOOPBACK_HOSTS.has(url.hostname));
    if (secure && url.pathname === "/" && !url.search && !url.username) {
      origin = url.origin;
    }
  } catch {
    // reported below
  }
  if (!origin) {
    problems.push("ADG_RENDER_ORIGIN must be an https origin with no path");
  }
  const key = variables.ADG_ORIGIN_KEY?.trim() ?? "";
  if (key.length < 32) {
    problems.push("the ADG_ORIGIN_KEY secret must be set (at least 32 characters)");
  }
  if (problems.length > 0) {
    throw new StagingConfigurationError(`ADG staging cannot reach its origin: ${problems.join("; ")}.`);
  }
  return { origin, key };
}
