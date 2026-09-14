/**
 * Paging a bounded list from a server-rendered page, with the position in the URL.
 *
 * The API pages forward only: a response carries `next_cursor` and nothing that walks
 * back. That is the right contract for the API — a keyset cursor resumes after a key it
 * was given, and a backwards cursor would have to mean something different — but a person
 * reading page four of a group's membership expects to get back to page three.
 *
 * So the *trail* of cursors used to reach the current page travels in the query string.
 * "Back" is the trail with its last element removed, re-requested; "forward" is the trail
 * with `next_cursor` appended. Nothing is stored between requests, every page is a plain
 * link a screen reader can announce and a browser can bookmark, and the position survives
 * a reload.
 *
 * Two things are deliberate:
 *
 * - **A trail that is not a trail resets to page one, and says so.** Cursors are base64url
 *   (`A-Za-z0-9-_`), so anything else in the parameter was not issued by this application.
 *   Paging on regardless would show page one while the pager claimed page four, and a
 *   short page read as page four is read as the end of the list.
 * - **The page number is the length of the trail, not a count of rows.** It is a position,
 *   not a total; the API does not always know a total, and this module never invents one.
 */

/** Cursors are base64url with no padding, so this separator cannot occur inside one. */
export const TRAIL_SEPARATOR = "~";

const CURSOR_PATTERN = /^[A-Za-z0-9_-]+$/;

export interface Trail {
  /** The cursors used to reach the current page, oldest first. Empty on page one. */
  cursors: string[];
  /** True when the parameter carried something this application did not issue. */
  rejected: boolean;
}

export const FIRST_PAGE: Trail = { cursors: [], rejected: false };

/**
 * Read a trail out of a query parameter.
 *
 * Next.js hands a repeated parameter over as an array; that is a caller mistake rather
 * than an attack, and it is rejected for the same reason as a malformed cursor.
 */
export function decodeTrail(raw: string | string[] | undefined): Trail {
  if (raw === undefined || raw === "") {
    return { cursors: [], rejected: false };
  }
  if (Array.isArray(raw)) {
    return { cursors: [], rejected: true };
  }
  const parts = raw.split(TRAIL_SEPARATOR);
  if (parts.some((part) => !CURSOR_PATTERN.test(part))) {
    return { cursors: [], rejected: true };
  }
  return { cursors: parts, rejected: false };
}

/** The parameter value for a trail, or null when it is page one and needs no parameter. */
export function encodeTrail(cursors: readonly string[]): string | null {
  return cursors.length === 0 ? null : cursors.join(TRAIL_SEPARATOR);
}

/** The cursor to send with the current request: the last one walked through. */
export function cursorFor(trail: Trail): string | undefined {
  return trail.cursors.length === 0 ? undefined : trail.cursors[trail.cursors.length - 1];
}

export type QueryValue = string | number | boolean | null | undefined;

/**
 * A relative href.
 *
 * Empty, null and undefined values are dropped rather than written as empty parameters,
 * so a link to the first page is the bare path and not `?tab=&page=`.
 */
export function hrefWith(basePath: string, params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") {
      continue;
    }
    search.set(name, String(value));
  }
  const query = search.toString();
  return query === "" ? basePath : `${basePath}?${query}`;
}

export interface PageNavigation {
  /** One-based, derived from the trail length. Never a claim about how many pages exist. */
  pageNumber: number;
  previousHref: string | null;
  nextHref: string | null;
  /** Offered from page two onward: a long trail is tedious to walk back one step at a time. */
  firstHref: string | null;
}

export interface PageNavigationOptions {
  basePath: string;
  /** Everything else the page needs to re-render identically: the key, the tab, filters. */
  params: Record<string, QueryValue>;
  /** Query parameter the trail travels in. Distinct per list on a page with several. */
  trailParam: string;
  trail: Trail;
  /** `page.next_cursor` from the response just rendered. */
  nextCursor: string | null | undefined;
}

export function pageNavigation(options: PageNavigationOptions): PageNavigation {
  const { basePath, params, trailParam, trail, nextCursor } = options;
  const at = (cursors: readonly string[]): string =>
    hrefWith(basePath, { ...params, [trailParam]: encodeTrail(cursors) });

  const hasPrevious = trail.cursors.length > 0;
  return {
    pageNumber: trail.cursors.length + 1,
    previousHref: hasPrevious ? at(trail.cursors.slice(0, -1)) : null,
    nextHref: nextCursor ? at([...trail.cursors, nextCursor]) : null,
    firstHref: hasPrevious ? at([]) : null,
  };
}
