/**
 * The two derived-answer endpoints the explanation screen is built on.
 *
 * A separate module from `lib/api/adg.ts` because these are a separate contract with its
 * own version (`docs/contracts/derived-responses.md`), and `tests/explanation-contract.test.ts`
 * checks this list against the published OpenAPI document the same way
 * `tests/contracts.test.ts` checks the other one. A page still cannot invent a URL: it
 * calls one of these functions or it calls nothing.
 */

import "server-only";

import type { ApiResult } from "@/lib/api/client";
import { apiRequest } from "@/lib/api/client";
import type { AccessExplanationResponse, AccessPathPageResponse } from "@/lib/derived";

/** Derived-answer paths this application calls. Asserted against the published OpenAPI document. */
export const EXPLAIN_PATHS = ["/api/v1/access/explain", "/api/v1/access/paths"] as const;

/**
 * Everything that identifies an explanation.
 *
 * `principal` and `resource` are both required by the API, and requiredly so on purpose:
 * an access route that answers about everybody when a parameter is omitted is the
 * Cartesian product of the estate wearing a query string.
 */
export interface ExplanationQuery {
  /** A SID, or a host-scoped storage key (`fs01|S-1-5-32-544`) for a local group. */
  principal: string;
  /** The directory's canonical UNC path. */
  resource: string;
  /** Host that scopes a local group, when the identifier is a bare SID. */
  host?: string;
  /** `remote_smb` applies the share ACL and the NTFS ACL; `local` applies NTFS alone. */
  access_path?: string;
  /** Which SIDs to assume are in the subject's token beyond its observed memberships. */
  assumption?: string;
  max_causal_paths?: number;
  max_removal_targets?: number;
}

/**
 * The whole derivation for one principal against one directory.
 *
 * The query-addressed spelling, and not the Phase 5A path-addressed one: a directory is
 * named by its UNC path, and `%5C%5CFS01%5CFinance` in a URL *path* segment is normalized
 * or rejected by several proxies. In the query string it survives.
 */
export function fetchAccessExplanation(
  token: string,
  query: ExplanationQuery,
): Promise<ApiResult<AccessExplanationResponse>> {
  return apiRequest<AccessExplanationResponse>("/api/v1/access/explain", {
    token,
    query: { ...query },
    // A bounded graph traversal and up to `max_removal_targets` re-runs of the access
    // check. The default 10s is not enough on a deeply nested estate.
    timeoutMs: 30_000,
  });
}

/**
 * One page of the same causal paths, in the same deterministic order.
 *
 * For an estate that produces more routes than one explanation should carry. The caller
 * must read `complete` *and* `page.has_more`: the last page of a truncated enumeration
 * carries `has_more: false` and `complete: false` together, and reading only the first
 * would conclude "that was all of them" about a list that was cut short.
 */
export function fetchAccessPathPage(
  token: string,
  query: ExplanationQuery & { limit?: number; cursor?: string },
): Promise<ApiResult<AccessPathPageResponse>> {
  return apiRequest<AccessPathPageResponse>("/api/v1/access/paths", {
    token,
    query: { ...query },
    timeoutMs: 30_000,
  });
}
