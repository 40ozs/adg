/**
 * Storing a proposal from the browser.
 *
 * The sibling of `preview/route.ts`, and the only difference between them is that this one
 * writes two rows into ADG's own `simulations` tables. It still writes nothing to Active
 * Directory, to a share, or to an NTFS descriptor: there is no code path from here to a
 * Windows object.
 *
 * A name is required, here as well as in the API. A stored proposal with no name is a row in
 * a listing nobody can identify later, which is the same as not having stored it.
 */

import { NextResponse } from "next/server";

import { storeSimulation } from "@/lib/api/adg";
import { currentSession } from "@/lib/auth/current";
import type { SimulationRequestBody } from "@/lib/simulation";

interface StoreBody extends SimulationRequestBody {
  name?: unknown;
  description?: unknown;
}

export async function POST(request: Request): Promise<NextResponse> {
  const session = await currentSession();
  if (session === null) {
    return NextResponse.json(
      { detail: "Your session has ended. Sign in again to continue." },
      { status: 401 },
    );
  }

  const body = (await request.json().catch(() => null)) as StoreBody | null;
  if (body === null || !Array.isArray(body.changes)) {
    return NextResponse.json(
      { detail: "A proposal is a list of changes. Send at least one." },
      { status: 400 },
    );
  }
  if (typeof body.name !== "string" || body.name.trim() === "") {
    return NextResponse.json(
      { detail: "Name the proposal. A stored proposal nobody can identify is not stored." },
      { status: 400 },
    );
  }

  const result = await storeSimulation(session.accessToken, {
    changes: body.changes,
    ...(body.scope ? { scope: body.scope } : {}),
    ...(body.bounds ? { bounds: body.bounds } : {}),
    name: body.name,
    ...(typeof body.description === "string" && body.description.trim() !== ""
      ? { description: body.description }
      : {}),
  });

  if (result.ok) {
    return NextResponse.json(result.data, { status: 201 });
  }
  return NextResponse.json(
    { detail: result.failure.detail ?? result.failure.message },
    { status: result.failure.status ?? 502 },
  );
}
