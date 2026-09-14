/**
 * The backend-for-frontend allow-list, and the traversal that used to defeat it.
 *
 * `/api/adg/*` exists so that the session's access token never reaches the browser. It is a
 * proxy, not a gateway: it adds authentication and nothing else. The allow-list is there so
 * that it can never become a way to reach arbitrary URLs on the API's network from a
 * browser — and a rule with a bypass provides none of that, however narrow the bypass.
 *
 * The bypass was real. Next.js hands a catch-all route its segments already percent-decoded,
 * and `lib/api/client.ts` builds its URL with `new URL()`, which resolves `..` before the
 * request goes out. So `/api/adg/api/v1/..%2F..%2Fauth%2Fconfig` arrived as the segments
 * `["api","v1","..","..","auth","config"]`, passed `startsWith("api/v1/")`, and collapsed to
 * `/auth/config` on the way out: the allow-list was checked against a path that no longer
 * existed by the time it was used.
 *
 * Nothing reachable that way was unguarded — every API path requires a capability and the
 * proxy attaches the caller's own token — so this was never a privilege escalation. It was a
 * control that did not hold, which is worth fixing on its own terms.
 */

import { describe, expect, it } from "vitest";

import { ALLOWED_PREFIXES, proxyTarget } from "@/lib/api/proxy";

describe("what the proxy forwards", () => {
  it("forwards the versioned API", () => {
    expect(proxyTarget(["api", "v1", "servers"])).toBe("api/v1/servers");
    expect(proxyTarget(["api", "v1", "collection", "operations"])).toBe(
      "api/v1/collection/operations",
    );
  });

  it("forwards the caller's own identity endpoint", () => {
    expect(proxyTarget(["auth", "me"])).toBe("auth/me");
  });

  it("keeps a share key, which carries a pipe and no separator", () => {
    expect(proxyTarget(["api", "v1", "shares", "fs01|finance"])).toBe(
      "api/v1/shares/fs01|finance",
    );
  });

  it("refuses a UNC resource key, because a backslash is a separator here", () => {
    // Not an arbitrary restriction, and worth its own test so that nobody relaxes it.
    // `new URL()` rewrites a backslash to a forward slash in the path of an http: URL, so a
    // segment carrying one escapes exactly as a `/` would. The consequence is that a
    // directory cannot be addressed through this proxy as a path segment at all —
    // percent-encoding does not help, because Next.js decodes each segment before the rule
    // sees it and the raw backslash goes back into the URL string.
    //
    // Nothing calls the proxy today; every page fetches server-side, where the key is
    // encoded once and never decoded again. Whoever adds the first browser-side call for a
    // directory needs a query parameter, not a path segment.
    expect(proxyTarget(["api", "v1", "resources", "\\\\fs01\\finance"])).toBeNull();
  });
});

describe("what the proxy refuses", () => {
  it("refuses a path outside the allow-list", () => {
    expect(proxyTarget(["auth", "dev", "login"])).toBeNull();
    expect(proxyTarget(["openapi.json"])).toBeNull();
    expect(proxyTarget(["health", "ready"])).toBeNull();
  });

  it("refuses an empty request", () => {
    expect(proxyTarget([])).toBeNull();
  });

  it("refuses a parent-directory segment, which is the bypass", () => {
    // The regression. Every one of these passes a naive prefix test and resolves, in the
    // URL the client actually builds, to something the allow-list does not cover.
    expect(proxyTarget(["api", "v1", "..", "..", "auth", "config"])).toBeNull();
    expect(proxyTarget(["api", "v1", "..", "openapi.json"])).toBeNull();
    expect(proxyTarget(["api", "v1", "servers", "..", "..", "..", "version"])).toBeNull();
  });

  it("refuses a current-directory segment too", () => {
    // Harmless on its own and part of every traversal that is not.
    expect(proxyTarget(["api", "v1", ".", "servers"])).toBeNull();
  });

  it("refuses an empty segment, which a double slash produces", () => {
    expect(proxyTarget(["api", "v1", "", "servers"])).toBeNull();
  });

  it("refuses a separator hidden inside one segment", () => {
    // `%2F` and `%5C` decode to separators before this function ever sees them, so a single
    // segment can carry a whole path. Checking the joined string alone would miss it.
    expect(proxyTarget(["api", "v1", "../../auth/config"])).toBeNull();
    expect(proxyTarget(["api", "v1", "..\\..\\auth"])).toBeNull();
  });

  it("refuses a segment that would make the joined path look allowed", () => {
    // The prefix test passes on the joined string; the check has to be on the segments.
    expect(proxyTarget(["api/v1/servers"])).toBeNull();
  });
});

describe("the allow-list itself", () => {
  it("covers the two prefixes the browser needs and no more", () => {
    // Growing this list is a deliberate act. Every entry is another surface the browser can
    // reach through a server-side credential.
    expect([...ALLOWED_PREFIXES]).toEqual(["api/v1/", "auth/me"]);
  });

  it("does not admit a path that merely starts with an allowed word", () => {
    expect(proxyTarget(["api", "v2", "servers"])).toBeNull();
  });

  it("matches an entry with no trailing slash exactly, not as a prefix", () => {
    // `auth/me` admits only itself. Matched as a bare prefix — which it was — it also
    // admitted `auth/mercy` and anything else beginning with those seven characters.
    // Nothing like that exists on the API, which is exactly why it would have gone
    // unnoticed until something did.
    expect(proxyTarget(["auth", "me"])).toBe("auth/me");
    expect(proxyTarget(["auth", "mercy"])).toBeNull();
    expect(proxyTarget(["auth", "me", "tokens"])).toBeNull();
  });
});
