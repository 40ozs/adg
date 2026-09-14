/**
 * Environment-based frontend configuration.
 *
 * The browser needs an absolute backend URL, so it must be supplied at build/run time
 * through NEXT_PUBLIC_ADG_API_URL. A bad value fails loudly with an actionable message
 * rather than producing confusing relative-URL fetch errors at runtime.
 */

export const DEFAULT_API_URL = "http://localhost:8000";

export type EnvironmentSource = Record<string, string | undefined>;

/**
 * Browser-facing API base URL, baked into the client bundle at build time.
 */
export function resolveApiUrl(env: EnvironmentSource = process.env): string {
  return normalize(env.NEXT_PUBLIC_ADG_API_URL, "NEXT_PUBLIC_ADG_API_URL");
}

/**
 * API base URL used by server-side rendering.
 *
 * In the Docker stack the browser reaches the API at http://localhost:8000 while the web
 * container must call it at http://api:8000 — inside that container, localhost is the
 * container itself. ADG_INTERNAL_API_URL carries the internal address; it is deliberately
 * not NEXT_PUBLIC_*, because it must never be shipped to a browser.
 */
export function resolveServerApiUrl(env: EnvironmentSource = process.env): string {
  const internal = env.ADG_INTERNAL_API_URL?.trim();
  if (internal) {
    return normalize(internal, "ADG_INTERNAL_API_URL");
  }
  return resolveApiUrl(env);
}

function normalize(value: string | undefined, variableName: string): string {
  const raw = value?.trim();
  if (!raw) {
    return DEFAULT_API_URL;
  }

  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(
      `${variableName} must be an absolute URL such as "${DEFAULT_API_URL}"; received "${raw}".`,
    );
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(
      `${variableName} must use http or https; received protocol "${parsed.protocol}".`,
    );
  }

  return raw.replace(/\/+$/, "");
}
