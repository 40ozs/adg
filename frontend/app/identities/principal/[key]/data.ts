import "server-only";

import { cache } from "react";

import { fetchCollectionStatus, fetchPrincipalDetail } from "@/lib/api/adg";

/**
 * The principal and the collection state, read at most once per server request.
 *
 * The layout and the view page both need them when a principal is first opened, and React
 * renders both in that one request; `cache` makes the second read the first one's answer.
 * Moving between views renders the view page alone, so the identity is not read again.
 */
export const principalDetail = cache((token: string, key: string) =>
  fetchPrincipalDetail(token, key),
);

export const collectionStatus = cache((token: string) => fetchCollectionStatus(token));

/**
 * The storage key from its path segment.
 *
 * Decoded once and defensively: a key is a SID or `host|SID`, so a `%` in it can only be
 * the encoding of `|`, and a malformed escape is left for the API to refuse.
 */
export function keyFromSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}
