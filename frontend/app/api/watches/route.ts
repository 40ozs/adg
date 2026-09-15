/**
 * Creating a watch from the browser.
 *
 * A route of its own rather than a widened `/api/adg` proxy, and the distinction is the
 * point. That proxy forwards **GET only**, deliberately: every other write in ADG is
 * collector ingestion, which authenticates with a key from a Windows host and has no
 * business coming through a browser session. Opening it to POST would make the browser a
 * route into ingestion, which the threat model does not include.
 *
 * A watch is the one write a person legitimately makes from a page, so it gets one handler
 * that can do exactly that and nothing else — the same shape `/api/auth/dev-login` has. What
 * the browser may write is therefore reviewable by reading this file, rather than being a
 * property of an allow-list somewhere else.
 *
 * It makes **no authorization decision**. The API requires `alerts:manage`; a viewer's
 * request reaches it, is refused, and the 403 is forwarded with the API's own message,
 * because that message names the capability the account is missing.
 */

import { NextResponse } from "next/server";

import { createWatch } from "@/lib/api/adg";
import { currentSession } from "@/lib/auth/current";

interface WatchBody {
  kind?: unknown;
  key?: unknown;
  label?: unknown;
  triggers?: unknown;
  cooldown_seconds?: unknown;
  notes?: unknown;
}

export async function POST(request: Request): Promise<NextResponse> {
  const session = await currentSession();
  if (session === null) {
    return NextResponse.json(
      { detail: "Your session has ended. Sign in again to continue." },
      { status: 401 },
    );
  }

  const body = (await request.json().catch(() => null)) as WatchBody | null;
  if (body === null) {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }

  // Shape-checked here and validated there. This is not the authorization boundary and it
  // is not the domain's validation either -- which trigger a kind of watch may subscribe to
  // is a rule that lives in app/alerts/model.py, and re-implementing it here would be a
  // second copy that could disagree with the one that decides.
  if (
    typeof body.kind !== "string" ||
    typeof body.key !== "string" ||
    typeof body.label !== "string" ||
    !Array.isArray(body.triggers) ||
    !body.triggers.every((item): item is string => typeof item === "string")
  ) {
    return NextResponse.json(
      { detail: "A watch needs a kind, a key, a label and at least one trigger." },
      { status: 400 },
    );
  }

  const result = await createWatch(session.accessToken, {
    kind: body.kind,
    key: body.key,
    label: body.label,
    triggers: body.triggers,
    ...(typeof body.cooldown_seconds === "number"
      ? { cooldown_seconds: body.cooldown_seconds }
      : {}),
    ...(typeof body.notes === "string" ? { notes: body.notes } : {}),
  });

  if (result.ok) {
    return NextResponse.json(result.data, { status: 201 });
  }
  return NextResponse.json(
    { detail: result.failure.detail ?? result.failure.message },
    { status: result.failure.status ?? 502 },
  );
}
