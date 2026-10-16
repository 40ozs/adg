/**
 * The ADG staging Worker: static files at the edge, everything else proxied to the Render
 * service, where Next.js and the ADG API run behind one origin gate. Public: no Cloudflare
 * Access. ADG's own sign-in decides what a visitor may do, and the Render service answers only
 * requests that carry the Worker's origin key.
 *
 *   request ─┬─ built static file ─► assets binding (the Worker does not run)
 *            └─ this Worker ─► Render origin gate (render/gate.ts) ─┬─ FastAPI ─► Supabase
 *                                                                  └─ Next.js
 *
 * It renders nothing and contains no application logic: no Next.js runtime is bundled here,
 * so each request costs the CPU of a proxy (Workers Free allows 10 ms). See
 * docs/deployment/cloudflare-demo.md.
 */

import { type StagingVariables, originUpstream } from "./environment";
import { routeRequest } from "./routing";

export default {
  fetch(request: Request, env: StagingVariables): Promise<Response> {
    return routeRequest(request, { upstream: () => originUpstream(env) });
  },
} satisfies ExportedHandler<StagingVariables>;
