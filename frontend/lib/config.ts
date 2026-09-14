/**
 * Environment-based frontend configuration.
 *
 * The browser needs an absolute backend URL, so it must be supplied at build/run time
 * through NEXT_PUBLIC_ADG_API_URL. A bad value fails loudly with an actionable message
 * rather than producing confusing relative-URL fetch errors at runtime.
 *
 * Since Phase 6A the browser no longer calls the API directly for data: every request goes
 * through this app's own `/api/adg/*` proxy, which attaches the session's access token
 * server-side. NEXT_PUBLIC_ADG_API_URL remains because the status page reports the address
 * an operator would use, and because it is the fallback for server-side calls.
 */

export const DEFAULT_API_URL = "http://localhost:8000";
export const DEFAULT_APP_URL = "http://localhost:3000";

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

/**
 * This application's own public base URL.
 *
 * Needed because the OIDC redirect URI must be absolute and must match the value registered
 * on the app registration exactly. Deriving it from the incoming request would let a
 * spoofed Host header steer the redirect, so it is configuration, not inference.
 */
export function resolveAppUrl(env: EnvironmentSource = process.env): string {
  const raw = env.ADG_WEB_URL?.trim();
  if (!raw) {
    return DEFAULT_APP_URL;
  }
  return normalize(raw, "ADG_WEB_URL");
}

/** Where the identity provider sends the browser back to. */
export function resolveRedirectUri(env: EnvironmentSource = process.env): string {
  return `${resolveAppUrl(env)}/api/auth/oidc/callback`;
}

/**
 * The confidential-client secret, when one is configured.
 *
 * Optional: a public client using PKCE needs none, and a deployment that prefers a
 * certificate credential configures neither. Never NEXT_PUBLIC_*, so it cannot reach a
 * browser bundle.
 */
export function resolveClientSecret(env: EnvironmentSource = process.env): string | null {
  const raw = env.ADG_OIDC_CLIENT_SECRET?.trim();
  return raw ? raw : null;
}

/**
 * Whether cookies should carry the Secure attribute.
 *
 * Set over https. Over plain http a Secure cookie is silently discarded by the browser and
 * the user is bounced back to the login page forever, so this follows the configured app
 * URL rather than NODE_ENV, which says nothing about the scheme actually in use.
 */
export function secureCookiesEnabled(env: EnvironmentSource = process.env): boolean {
  return resolveAppUrl(env).startsWith("https://");
}
