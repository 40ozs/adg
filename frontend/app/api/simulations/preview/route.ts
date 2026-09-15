/**
 * Evaluating a proposal from the browser.
 *
 * A route of its own rather than a widened `/api/adg` proxy, for the reason the watch routes
 * state: that proxy forwards **GET only**, deliberately, and every write the browser may make
 * is individually declared here so that it is reviewable by reading one file.
 *
 * This one is the most conservative of them. It writes nothing anywhere — not to Windows, and
 * not to ADG: the API route it calls computes an answer and stores no row. A proposal typed
 * into the editor leaves no trace until somebody names it and saves it.
 *
 * It makes **no authorization decision**. The API requires `simulations:run`; an auditor's
 * request reaches it, is refused, and the 403 is forwarded with the API's own message,
 * because that message names the capability the account is missing.
 *
 * It makes **no validation decision** either, beyond checking that a list of changes arrived.
 * Which fields each change kind needs is a rule in `app/simulation/overlay.py`, and a second
 * copy of it here would be the more lenient copy.
 */

import { NextResponse } from "next/server";

import { previewSimulation } from "@/lib/api/adg";
import { currentSession } from "@/lib/auth/current";
import type { SimulationRequestBody } from "@/lib/simulation";

export async function POST(request: Request): Promise<NextResponse> {
  const session = await currentSession();
  if (session === null) {
    return NextResponse.json(
      { detail: "Your session has ended. Sign in again to continue." },
      { status: 401 },
    );
  }

  const body = (await request.json().catch(() => null)) as SimulationRequestBody | null;
  if (body === null || !Array.isArray(body.changes)) {
    return NextResponse.json(
      { detail: "A proposal is a list of changes. Send at least one." },
      { status: 400 },
    );
  }

  const result = await previewSimulation(session.accessToken, body);
  if (result.ok) {
    return NextResponse.json(result.data);
  }
  return NextResponse.json(
    { detail: result.failure.detail ?? result.failure.message },
    { status: result.failure.status ?? 502 },
  );
}
