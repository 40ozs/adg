/**
 * Removing a stored proposal from the browser.
 *
 * The one destructive operation in this whole feature, and it destroys only proposals: the
 * cascade reaches the evaluations of that proposal and stops, because nothing else in ADG
 * points at either table. No collected fact can be reached from here, and nothing in Windows
 * can.
 *
 * Deleting a proposal is not undoing anything. Nothing was ever applied, so there is nothing
 * to reverse; what goes is ADG's record that somebody asked the question.
 */

import { NextResponse } from "next/server";

import { deleteSimulation } from "@/lib/api/adg";
import { currentSession } from "@/lib/auth/current";

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ simulationId: string }> },
): Promise<NextResponse> {
  const session = await currentSession();
  if (session === null) {
    return NextResponse.json(
      { detail: "Your session has ended. Sign in again to continue." },
      { status: 401 },
    );
  }
  const { simulationId } = await context.params;
  const result = await deleteSimulation(session.accessToken, simulationId);

  if (result.ok) {
    return new NextResponse(null, { status: 204 });
  }
  return NextResponse.json(
    { detail: result.failure.detail ?? result.failure.message },
    { status: result.failure.status ?? 502 },
  );
}
