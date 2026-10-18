import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { type OriginUpstream } from "../../cloudflare/environment";
import { routeRequest } from "../../cloudflare/routing";

/*
 * CLOUDFLARE-DEMO-CACHE-01: every demo-data answer depends on the visitor's adg_session cookie
 * (or Bearer token), so the Worker shares no answer between visitors. Workers Cache would sit in
 * front of the Worker, key answers by path alone (cookies are not part of its key) and keep a
 * 200 without Cache-Control for 2 h: switching it on would hand one visitor's pages to the next.
 * See docs/deployment/cloudflare-demo.md "Caching".
 */
const wrangler = readFileSync(new URL("../../wrangler.jsonc", import.meta.url), "utf8");
const UPSTREAM: OriginUpstream = { origin: "https://adg-demo-web.onrender.com", key: "o".repeat(40) };

describe("Cloudflare caching for the demo Worker", () => {
  it("keeps Workers Cache off, for the Worker and for every entrypoint", () => {
    const config = wrangler.replace(/^\s*\/\/.*$/gm, "");
    expect(JSON.parse(config)).toBeTypeOf("object");
    expect(config).not.toMatch(/"cache"\s*:/);
  });

  it("shares no answer between visitors: every request reaches the origin and gets its own answer", async () => {
    const sent: string[] = [];
    const fetch = async (request: Request) => {
      const cookie = request.headers.get("Cookie") ?? "anonymous";
      sent.push(cookie);
      return new Response(`<html>${cookie}</html>`, { headers: { "Content-Type": "text/html" } });
    };
    const answers: string[] = [];
    for (const cookie of ["adg_session=planner", "adg_session=viewer", "adg_session=planner", undefined]) {
      const request = new Request("https://adg-demo-staging.example.workers.dev/risks", {
        headers: cookie ? { Cookie: cookie } : {},
      });
      answers.push(await (await routeRequest(request, { upstream: () => UPSTREAM, fetch })).text());
    }
    expect(sent).toEqual(["adg_session=planner", "adg_session=viewer", "adg_session=planner", "anonymous"]);
    expect(answers).toEqual([
      "<html>adg_session=planner</html>",
      "<html>adg_session=viewer</html>",
      "<html>adg_session=planner</html>",
      "<html>anonymous</html>",
    ]);
  });
});
