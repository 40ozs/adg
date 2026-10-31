#!/usr/bin/env node
/**
 * ADG-HOSTED-STREAMING-11: is the hosted Overview the progressive build? Signs in (development
 * login), GETs PAGE_URL uncompressed and counts structural markers of React streaming SSR and of
 * the Wave C Overview. Prints counts only, never the body, cookie or token.
 *
 *   PAGE_URL=https://…/ SIGN_IN_AS=planner node structure-probe.mjs
 */
const PAGE = new URL(process.env.PAGE_URL);
const AS = process.env.SIGN_IN_AS ?? "planner";
const UA = "adg-hosted-streaming-11-probe/1";
const login = await fetch(`${PAGE.origin}/auth/dev/login`, { method: "POST", headers: { "user-agent": UA, "content-type": "application/json" }, body: JSON.stringify({ username: AS }) });
if (login.status !== 200) throw new Error(`sign-in HTTP ${login.status}`);
const b = await login.json();
const cookie = `adg_session=${Buffer.from(JSON.stringify({ accessToken: b.access_token, expiresAt: b.expires_at, mode: "development" })).toString("base64url")}`;
const res = await fetch(PAGE, { headers: { "user-agent": UA, cookie, "accept-encoding": "identity", accept: "text/html" } });
const html = (await res.text()).replaceAll("<!-- -->", ""); // React splits adjacent text nodes with empty comments
const count = (re) => (html.match(re) ?? []).length;
console.log(JSON.stringify({
  status: res.status,
  suspense_pending_boundaries: count(/<!--\$\?--><template id="B:\d+"/g),
  streamed_segments: count(/<div hidden id="S:\d+">/g),
  reveal_calls: count(/\$RC\("B:\d+","S:\d+"\)/g),
  pending_placeholders: count(/aria-busy="true"/g),
  loading_copy: ["Loading collection status", "Loading findings", "Loading changes", "Loading your review queue"].filter((s) => html.includes(s)),
  resolved_sections: ["overview-health", "overview-attention", "overview-changes", "overview-reviews"].filter((id) => html.includes(`id="${id}"`)),
  title: html.match(/<title>([^<]*)<\/title>/)?.[1],
}, null, 1));
