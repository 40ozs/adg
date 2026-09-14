/**
 * Deciding what a view should show, and — for an empty one — what emptiness means.
 *
 * This is the module the phase exists for. An ADG list with nothing in it means one of
 * several completely different things:
 *
 * - nothing is there, and collection is current;
 * - nothing is there, but a collector failed, so nobody actually looked;
 * - the request failed;
 * - the session ended;
 * - the account may not see this.
 *
 * Reading the second as the first is the specific mistake this product must never invite:
 * "no risky permissions on this share" and "no scan of this share" look identical in a
 * table and mean opposite things. So the decision is a pure function over the API result
 * and the backend's own coverage verdict, and every view renders the state it is handed
 * rather than inferring one from `items.length === 0`.
 *
 * The backend owns the coverage judgement (`app/domain/collection.py`). Nothing here
 * re-derives it, and nothing here computes permissions.
 */

import type { ApiFailure, ApiResult } from "@/lib/api/client";
import type { CollectionStatus } from "@/lib/contracts";

export type ViewState<T> =
  | { kind: "ready"; data: T; caveat: Caveat | null }
  | { kind: "empty"; reason: EmptyReason; headline: string; explanation: string }
  | { kind: "unauthenticated"; headline: string; explanation: string }
  | { kind: "forbidden"; headline: string; explanation: string }
  | { kind: "error"; headline: string; explanation: string; failure: ApiFailure };

/** Why a view has nothing to show. */
export type EmptyReason =
  | "nothing-collected"
  | "collection-failed"
  | "collection-incomplete"
  | "no-matches";

/** A banner shown above data that is present but cannot be read as complete. */
export interface Caveat {
  severity: "warning" | "info";
  headline: string;
  explanation: string;
}

export interface ClassifyOptions<T> {
  /** True when the payload has no rows. Supplied by the caller, which knows its own shape. */
  isEmpty: (data: T) => boolean;
  /** The backend's coverage verdict, when the view could fetch one. */
  coverage?: CollectionStatus | null;
  /** What the user was looking at, for the empty message: "shares", "identities", … */
  subject: string;
}

export function classify<T>(result: ApiResult<T>, options: ClassifyOptions<T>): ViewState<T> {
  if (!result.ok) {
    return fromFailure<T>(result.failure);
  }

  const caveat = coverageCaveat(options.coverage ?? null);

  if (!options.isEmpty(result.data)) {
    return { kind: "ready", data: result.data, caveat };
  }

  return emptyState<T>(options.subject, options.coverage ?? null);
}

function fromFailure<T>(failure: ApiFailure): ViewState<T> {
  if (failure.kind === "unauthenticated") {
    return {
      kind: "unauthenticated",
      headline: "Your session has ended",
      explanation: "Sign in again to continue. Nothing was lost.",
    };
  }
  if (failure.kind === "forbidden") {
    return {
      kind: "forbidden",
      headline: "Your account cannot see this",
      // The API's message names the capability required and the roles held, which is what
      // an administrator needs in order to fix it.
      explanation: failure.message,
    };
  }
  return {
    kind: "error",
    headline: headlineFor(failure),
    explanation: failure.detail ?? failure.message,
    failure,
  };
}

function headlineFor(failure: ApiFailure): string {
  switch (failure.kind) {
    case "unreachable":
      return "ADG could not reach its API";
    case "not_found":
      return "Not found";
    case "invalid_request":
      return "That request could not be understood";
    default:
      return "Something went wrong";
  }
}

/**
 * The empty state, worded from the coverage verdict.
 *
 * Note what the wording does *not* do when coverage is unknown or imperfect: it never says
 * "there are none". It says nobody can tell yet.
 */
function emptyState<T>(subject: string, coverage: CollectionStatus | null): ViewState<T> {
  if (coverage === null) {
    return {
      kind: "empty",
      reason: "collection-incomplete",
      headline: `No ${subject} to show`,
      explanation:
        "ADG could not check whether collection has run, so this emptiness cannot be " +
        "interpreted. Open Collectors to see the state of the last runs.",
    };
  }

  switch (coverage.health) {
    case "no_data":
      return {
        kind: "empty",
        reason: "nothing-collected",
        headline: "Nothing has been collected yet",
        explanation:
          `This is empty because no collector has reported, not because there are no ` +
          `${subject}. Run a collector, then return here.`,
      };
    case "failed":
      return {
        kind: "empty",
        reason: "collection-failed",
        headline: `No ${subject} found — but a collector failed`,
        explanation:
          `${coverage.summary} Treat this emptiness as unknown rather than as an answer.`,
      };
    case "incomplete":
      return {
        kind: "empty",
        reason: "collection-incomplete",
        headline: `No ${subject} found — collection is incomplete`,
        explanation: `${coverage.summary} Open Collectors for the detail.`,
      };
    case "healthy":
      return {
        kind: "empty",
        reason: "no-matches",
        headline: `No ${subject}`,
        explanation:
          `Collection is current, so ADG holds no ${subject} matching this view. This ` +
          "emptiness is an answer.",
      };
  }
}

/**
 * The banner to show *above data that is present*.
 *
 * A populated table is the more dangerous case, not the less: a list of twelve shares looks
 * complete whether or not a collector failed on a thirteenth.
 */
export function coverageCaveat(coverage: CollectionStatus | null): Caveat | null {
  if (coverage === null) {
    return null;
  }
  switch (coverage.health) {
    case "failed":
      return {
        severity: "warning",
        headline: "Part of the estate is unobserved",
        explanation: `${coverage.summary} What is shown may be only part of the picture.`,
      };
    case "incomplete":
      return {
        severity: "warning",
        headline: "Collection is incomplete",
        explanation: coverage.summary,
      };
    case "no_data":
      return {
        severity: "info",
        headline: "Nothing has been collected yet",
        explanation: coverage.summary,
      };
    case "healthy":
      return null;
  }
}
