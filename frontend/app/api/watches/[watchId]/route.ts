/**
 * Editing and removing one watch from the browser.
 *
 * Same reasoning as the sibling route: the generic `/api/adg` proxy forwards GET only, so
 * the writes a page may make are individually declared rather than admitted by a prefix.
 *
 * **The kind and the key cannot be edited**, here or in the API. Re-pointing a watch would
 * silently re-attribute every alert already raised under it, and the history would then say
 * a directory was edited when it was not. Deleting and creating one is the honest version,
 * and deleting a watch keeps the alerts it raised.
 */

import { NextResponse } from "next/server";

import { deleteWatch, updateWatch } from "@/lib/api/adg";
import { currentSession } from "@/lib/auth/current";

interface UpdateBody {
  label?: unknown;
  triggers?: unknown;
  cooldown_seconds?: unknown;
  enabled?: unknown;
  notes?: unknown;
}

export async function PATCH(
  request: Request,
  context: { params: Promise<{ watchId: string }> },
): Promise<NextResponse> {
  const session = await currentSession();
  if (session === null) {
    return unauthenticated();
  }
  const { watchId } = await context.params;
  const body = (await request.json().catch(() => null)) as UpdateBody | null;
  if (body === null) {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }

  const result = await updateWatch(session.accessToken, watchId, {
    ...(typeof body.label === "string" ? { label: body.label } : {}),
    ...(Array.isArray(body.triggers) &&
    body.triggers.every((item): item is string => typeof item === "string")
      ? { triggers: body.triggers }
      : {}),
    ...(typeof body.cooldown_seconds === "number"
      ? { cooldown_seconds: body.cooldown_seconds }
      : {}),
    ...(typeof body.enabled === "boolean" ? { enabled: body.enabled } : {}),
    ...(typeof body.notes === "string" ? { notes: body.notes } : {}),
  });

  if (result.ok) {
    return NextResponse.json(result.data);
  }
  return forwarded(result.failure.detail ?? result.failure.message, result.failure.status);
}

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ watchId: string }> },
): Promise<NextResponse> {
  const session = await currentSession();
  if (session === null) {
    return unauthenticated();
  }
  const { watchId } = await context.params;
  const result = await deleteWatch(session.accessToken, watchId);

  if (result.ok) {
    return new NextResponse(null, { status: 204 });
  }
  return forwarded(result.failure.detail ?? result.failure.message, result.failure.status);
}

function unauthenticated(): NextResponse {
  return NextResponse.json(
    { detail: "Your session has ended. Sign in again to continue." },
    { status: 401 },
  );
}

function forwarded(detail: string, status: number | null | undefined): NextResponse {
  return NextResponse.json({ detail }, { status: status ?? 502 });
}
