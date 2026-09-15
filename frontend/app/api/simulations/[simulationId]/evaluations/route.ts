/**
 * Running a stored proposal again, from the browser.
 *
 * The answer to a stale baseline. A proposal measured last week against a state the estate
 * has since moved on from is still the proposal; re-running it against what is there now is
 * one request, and it **adds** a result rather than replacing one — so "this change was safe
 * on Monday and takes access away today" stays available as two results rather than being
 * overwritten by the newer half.
 *
 * Still nothing is applied. The row written is ADG's own record of an answer.
 */

import { NextResponse } from "next/server";

import { evaluateSimulation } from "@/lib/api/adg";
import { currentSession } from "@/lib/auth/current";

export async function POST(
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
  const result = await evaluateSimulation(session.accessToken, simulationId);

  if (result.ok) {
    return NextResponse.json(result.data, { status: 201 });
  }
  return NextResponse.json(
    { detail: result.failure.detail ?? result.failure.message },
    { status: result.failure.status ?? 502 },
  );
}
