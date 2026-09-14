/**
 * The rule the backend-for-frontend proxy admits a path by.
 *
 * Its own module because a Next.js route file may export only route handlers — and because
 * the rule is worth testing on its own. `app/api/adg/[...path]/route.ts` is the plumbing;
 * this is the decision.
 */

/**
 * Paths the browser may ask for through the proxy.
 *
 * An entry ending in `/` admits everything beneath it; an entry without one must match
 * exactly. The distinction is not cosmetic: matched as a bare prefix, `auth/me` also admits
 * `auth/mercy` and anything else beginning with those seven characters. Nothing like that
 * exists on the API today, which is precisely why it would go unnoticed.
 */
export const ALLOWED_PREFIXES = ["api/v1/", "auth/me"] as const;

/**
 * The API path to forward to, or `null` when this application does not proxy it.
 *
 * **The traversal check is the point of this function, not a formality.** Next.js hands the
 * catch-all route its segments already percent-decoded, and the client builds its URL with
 * `new URL()`, which resolves `..` before the request is sent. So
 * `/api/adg/api/v1/..%2F..%2Fauth%2Fconfig` arrives as the segments
 * `["api","v1","..","..","auth","config"]`, passes a `startsWith("api/v1/")` test, and then
 * collapses to `/auth/config` on the way out — an allow-list defeated by a string
 * comparison against a path that no longer exists by the time it is used.
 *
 * Nothing reachable that way was unguarded: every API path requires a capability and the
 * proxy attaches the caller's own token, so this was never a privilege escalation. It was
 * an allow-list that did not hold, which is its own defect. The reason this handler has one
 * at all is so that it can never become a way to reach arbitrary URLs on the API's network
 * from a browser, and a rule with a bypass provides none of that.
 *
 * **A backslash is a separator here too**, which is not obvious. `new URL()` rewrites `\`
 * to `/` in the path of an `http:` URL, so a segment carrying one escapes exactly as a `/`
 * would. The consequence is worth stating plainly: a UNC resource key — `\\fs01\finance`,
 * the key every directory in ADG is addressed by — **cannot travel through this proxy**.
 * Percent-encoding it does not help, because Next.js decodes each segment before this
 * function sees it and the raw backslash is then re-inserted into the URL string. Nothing
 * calls the proxy today (every page fetches server-side, where `lib/api/adg.ts` encodes the
 * key and no decode round-trip happens), so this costs nothing now; whoever adds the first
 * browser-side call for a directory will need a query parameter rather than a path segment.
 */
export function proxyTarget(segments: readonly string[]): string | null {
  if (segments.length === 0) {
    return null;
  }
  for (const segment of segments) {
    // A dot segment, an empty segment, or a separator inside one: none can appear in a
    // legitimate ADG path segment, and each is a way to make the joined string say
    // something the final URL does not.
    if (segment === "" || segment === "." || segment === ".." || /[\\/]/.test(segment)) {
      return null;
    }
  }
  const joined = segments.join("/");
  const allowed = ALLOWED_PREFIXES.some((prefix) =>
    prefix.endsWith("/") ? joined.startsWith(prefix) : joined === prefix,
  );
  return allowed ? joined : null;
}
