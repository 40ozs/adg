/**
 * Cloudflare Access, checked again inside the Worker.
 *
 * Access at Cloudflare's edge is what keeps staging private: a visitor without an Access
 * session never reaches the Worker. This module is the second lock, and the reason it
 * exists is that the first one is a dashboard setting. If Access is switched off, scoped to
 * the wrong hostname, or never configured, the Worker still refuses every request — it does
 * not quietly become a public site.
 *
 * Every request must carry a `Cf-Access-Jwt-Assertion` that Access signed for this
 * application: RS256, a key from the team's published certificates, this application's
 * audience tag, the team domain as issuer, and in date. Without `ACCESS_TEAM_DOMAIN` and
 * `ACCESS_AUD` configured, the answer is 503 for everyone (fail closed).
 *
 * https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/
 *
 * Pure WebCrypto, no `cloudflare:*` import: unit-tested in Node (`tests/cloudflare/`).
 */

export const ACCESS_JWT_HEADER = "cf-access-jwt-assertion";

/** Clock skew tolerated on `exp` and `nbf`, in seconds. */
const LEEWAY_SECONDS = 60;
/** How long a fetched certificate set is trusted before it is fetched again. */
const CERTS_TTL_MS = 10 * 60 * 1000;

export interface AccessConfiguration {
  /** `https://<team>.cloudflareaccess.com`, no trailing slash. */
  teamDomain: string;
  /** The Access application's audience (AUD) tag. */
  audience: string;
}

export type AccessVerdict =
  | { ok: true; identity: string }
  | { ok: false; status: 401 | 403 | 503; reason: string };

/**
 * The configured Access application, or `null` when it is incomplete.
 *
 * The team domain must be https. Plain http is accepted only for a loopback host, which is
 * what the local rehearsal serves its own test certificates from; no Cloudflare team domain
 * is ever loopback, so this admits nothing in a real deployment.
 */
export function accessConfiguration(variables: {
  ACCESS_TEAM_DOMAIN?: string;
  ACCESS_AUD?: string;
}): AccessConfiguration | null {
  const rawDomain = variables.ACCESS_TEAM_DOMAIN?.trim().replace(/\/+$/, "");
  const audience = variables.ACCESS_AUD?.trim();
  if (!rawDomain || !audience) {
    return null;
  }
  let url: URL;
  try {
    url = new URL(rawDomain);
  } catch {
    return null;
  }
  const loopback = url.hostname === "127.0.0.1" || url.hostname === "localhost";
  if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) {
    return null;
  }
  if (url.pathname !== "/" || url.search || url.hash) {
    return null;
  }
  return { teamDomain: url.origin, audience };
}

type Fetcher = (url: string) => Promise<Response>;

interface Jwk extends JsonWebKey {
  kid?: string;
}

const certificateCache = new Map<string, { keys: Jwk[]; fetchedAt: number }>();

/** Forget cached certificates. Tests only. */
export function clearAccessCertificateCache(): void {
  certificateCache.clear();
}

async function certificates(
  teamDomain: string,
  fetcher: Fetcher,
  now: number,
  refresh: boolean,
): Promise<Jwk[]> {
  const cached = certificateCache.get(teamDomain);
  if (cached && !refresh && now - cached.fetchedAt < CERTS_TTL_MS) {
    return cached.keys;
  }
  const response = await fetcher(`${teamDomain}/cdn-cgi/access/certs`);
  if (!response.ok) {
    throw new Error(`Access certificates answered ${response.status}`);
  }
  const body = (await response.json()) as { keys?: Jwk[] };
  const keys = Array.isArray(body.keys) ? body.keys : [];
  certificateCache.set(teamDomain, { keys, fetchedAt: now });
  return keys;
}

function base64UrlDecode(segment: string): Uint8Array<ArrayBuffer> {
  const base64 = segment.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function decodeJson(segment: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(new TextDecoder().decode(base64UrlDecode(segment)));
    return value !== null && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/**
 * Decide whether `request` came through this Access application.
 *
 * `now` is milliseconds since the epoch; `fetcher` fetches the certificate set.
 */
export async function verifyAccess(
  request: Request,
  configuration: AccessConfiguration | null,
  fetcher: Fetcher = (url) => fetch(url),
  now: number = Date.now(),
): Promise<AccessVerdict> {
  if (configuration === null) {
    return {
      ok: false,
      status: 503,
      reason:
        "Cloudflare Access is not configured for this staging deployment " +
        "(ACCESS_TEAM_DOMAIN, ACCESS_AUD), so it serves nobody.",
    };
  }
  const token = request.headers.get(ACCESS_JWT_HEADER)?.trim();
  if (!token) {
    return { ok: false, status: 401, reason: "No Cloudflare Access assertion on the request." };
  }

  const parts = token.split(".");
  if (parts.length !== 3) {
    return { ok: false, status: 403, reason: "The Access assertion is not a JWT." };
  }
  const [encodedHeader, encodedPayload, encodedSignature] = parts;
  const header = decodeJson(encodedHeader);
  const payload = decodeJson(encodedPayload);
  if (header === null || payload === null) {
    return { ok: false, status: 403, reason: "The Access assertion could not be decoded." };
  }
  if (header.alg !== "RS256" || typeof header.kid !== "string") {
    return { ok: false, status: 403, reason: "The Access assertion is not RS256 with a key id." };
  }

  let key: Jwk | undefined;
  try {
    key = (await certificates(configuration.teamDomain, fetcher, now, false)).find(
      (candidate) => candidate.kid === header.kid,
    );
    if (key === undefined) {
      // Access rotates its keys; one refetch covers a rotation since the last fetch.
      key = (await certificates(configuration.teamDomain, fetcher, now, true)).find(
        (candidate) => candidate.kid === header.kid,
      );
    }
  } catch (error) {
    return {
      ok: false,
      status: 503,
      reason: `The Access certificates could not be fetched: ${
        error instanceof Error ? error.message : String(error)
      }`,
    };
  }
  if (key === undefined) {
    return { ok: false, status: 403, reason: "The Access assertion names an unknown key." };
  }

  let valid = false;
  try {
    const cryptoKey = await crypto.subtle.importKey(
      "jwk",
      { kty: key.kty, n: key.n, e: key.e, alg: "RS256", ext: true },
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
      false,
      ["verify"],
    );
    valid = await crypto.subtle.verify(
      "RSASSA-PKCS1-v1_5",
      cryptoKey,
      base64UrlDecode(encodedSignature),
      new TextEncoder().encode(`${encodedHeader}.${encodedPayload}`),
    );
  } catch {
    valid = false;
  }
  if (!valid) {
    return { ok: false, status: 403, reason: "The Access assertion's signature does not verify." };
  }

  const audiences = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!audiences.includes(configuration.audience)) {
    return { ok: false, status: 403, reason: "The Access assertion is for another application." };
  }
  if (payload.iss !== configuration.teamDomain) {
    return { ok: false, status: 403, reason: "The Access assertion has another issuer." };
  }
  const seconds = Math.floor(now / 1000);
  if (typeof payload.exp !== "number" || payload.exp + LEEWAY_SECONDS < seconds) {
    return { ok: false, status: 403, reason: "The Access assertion has expired." };
  }
  if (typeof payload.nbf === "number" && payload.nbf - LEEWAY_SECONDS > seconds) {
    return { ok: false, status: 403, reason: "The Access assertion is not valid yet." };
  }

  // A person carries an email; a service token carries its common name.
  const identity =
    typeof payload.email === "string"
      ? payload.email
      : typeof payload.common_name === "string"
        ? `service-token:${payload.common_name}`
        : "unknown";
  return { ok: true, identity };
}
