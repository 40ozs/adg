"use server";

/**
 * Recording a decision, from a form, on the server.
 *
 * **Why a server action and not the proxy.** `app/api/adg/[...path]/route.ts` forwards `GET`
 * only, and its docstring says why: it exists so the browser can read the API without ever
 * holding a token, and turning it into a general write path would make it a route into the
 * API that the threat model does not include. A server action is the narrower tool — it
 * reaches exactly these two operations, the session is read on the server, and the browser
 * still never sees an access token.
 *
 * **Nothing here decides anything.** Every rule that governs a decision is the API's: the
 * campaign must be active, the item must be assigned to this caller, the rationale must
 * satisfy the campaign's requirement, and a batch must be one question. These functions
 * submit and report; a refusal is rendered with the message the API sent, because that
 * message names the rule that failed and this application would only paraphrase it worse.
 */

import { revalidatePath } from "next/cache";

import { submitBulkDecision, submitDecision } from "@/lib/api/adg";
import { currentSession } from "@/lib/auth/current";

/** What a form learns. Never a thrown error: a form that throws loses what was typed. */
export interface ActionResult {
  status: "recorded" | "refused" | "signed-out";
  /** Written for the person at the screen. The API's own wording where it sent one. */
  message: string;
}

const SIGNED_OUT: ActionResult = {
  status: "signed-out",
  message: "Your session has ended. Sign in again; nothing was recorded.",
};

export async function recordDecision(
  itemId: string,
  decision: string,
  rationale: string,
): Promise<ActionResult> {
  const session = await currentSession();
  if (session === null) {
    return SIGNED_OUT;
  }
  const trimmed = rationale.trim();
  const result = await submitDecision(session.accessToken, itemId, {
    decision,
    // Blank is sent as absent rather than as an empty string, so the API applies its own
    // rule about what needs a reason instead of accepting whitespace as one.
    rationale: trimmed === "" ? null : trimmed,
  });
  if (!result.ok) {
    return { status: "refused", message: refusal(result.failure.detail, result.failure.message) };
  }
  revalidatePath("/governance", "layout");
  return { status: "recorded", message: "Decision recorded." };
}

export async function recordBulkDecision(
  campaignId: string,
  itemIds: string[],
  decision: string,
  rationale: string,
): Promise<ActionResult> {
  const session = await currentSession();
  if (session === null) {
    return SIGNED_OUT;
  }
  const trimmed = rationale.trim();
  const result = await submitBulkDecision(session.accessToken, campaignId, {
    item_ids: itemIds,
    decision,
    rationale: trimmed === "" ? null : trimmed,
  });
  if (!result.ok) {
    return { status: "refused", message: refusal(result.failure.detail, result.failure.message) };
  }
  revalidatePath("/governance", "layout");
  return {
    status: "recorded",
    message: `Recorded on ${result.data.item_count} ${
      result.data.item_count === 1 ? "item" : "items"
    }, each with its own decision and audit event.`,
  };
}

/**
 * The API's own explanation, falling back to the classified one.
 *
 * Preferred in that order because the detail is the sentence that names the rule — "these
 * items have changed since the campaign was frozen", "the item is assigned to another
 * reviewer" — and the classified message only says which kind of failure it was.
 */
function refusal(detail: string | undefined, message: string): string {
  return detail ?? message;
}
