/**
 * Where a server, a share, and a directory live in this application, and how to walk up
 * from one to the next.
 *
 * Identifiers travel as query parameters rather than as path segments. A directory is
 * named by its UNC path — `\\FS01\Finance\Reports` — and a server's local group is keyed
 * `fs01|S-1-5-32-544`; both carry characters (`\`, `|`) whose treatment inside a URL path
 * depends on the browser, the Next.js router, and the Node runtime agreeing about
 * normalization. A query parameter has one rule and every layer applies it.
 *
 * The breadcrumb is the other half of requirement 4, and it is parsing rather than
 * navigation state: a directory's ancestors are derivable from its own path, so the page
 * needs no extra round trip to show where it sits. **Derivable is not the same as
 * observed** — an ancestor in the breadcrumb may be a directory no run has read, and the
 * page it links to will say so. That is better than hiding the path.
 */

import { hrefWith } from "@/lib/paging";

export const SERVER_PATH = "/resources/server";
export const SHARE_PATH = "/resources/share";
export const DIRECTORY_PATH = "/resources/directory";

const SEPARATOR = "\\";
const UNC_PREFIX = "\\\\";

export function serverHref(key: string): string {
  return hrefWith(SERVER_PATH, { key });
}

export function shareHref(key: string, extra: Record<string, string> = {}): string {
  return hrefWith(SHARE_PATH, { key, ...extra });
}

export function directoryHref(key: string, extra: Record<string, string> = {}): string {
  return hrefWith(DIRECTORY_PATH, { key, ...extra });
}

export interface UncParts {
  server: string;
  share: string;
  /** Path components beneath the share root, case preserved. Empty at the root itself. */
  segments: string[];
}

/**
 * Split a UNC path into its parts, or null if it is not one.
 *
 * Forward slashes are accepted because collectors and people both produce them, and the
 * API's own parser accepts them. Nothing else is repaired: a path with a `..` in it is
 * returned with the `..` intact, because resolving one without touching the file system is
 * a guess, and a guess here names a different directory.
 */
export function parseUncPath(raw: string): UncParts | null {
  const text = raw.trim().replace(/\//g, SEPARATOR);
  if (!text.startsWith(UNC_PREFIX)) {
    return null;
  }
  const parts = text.slice(UNC_PREFIX.length).split(SEPARATOR).filter((part) => part !== "");
  if (parts.length < 2) {
    return null;
  }
  return { server: parts[0], share: parts[1], segments: parts.slice(2) };
}

/** The canonical UNC path of the share root a path sits under, or null. */
export function shareRootPath(raw: string): string | null {
  const parts = parseUncPath(raw);
  return parts === null ? null : `${UNC_PREFIX}${parts.server}${SEPARATOR}${parts.share}`;
}

/**
 * The containing directory's path, or null at a share root.
 *
 * Null at the root is not "unknown": a share root's parent is outside the share, usually
 * outside anything a collector was pointed at, and linking to it would invite a reader to
 * conclude something about a directory ADG has never seen.
 */
export function parentPath(raw: string): string | null {
  const parts = parseUncPath(raw);
  if (parts === null || parts.segments.length === 0) {
    return null;
  }
  return [
    `${UNC_PREFIX}${parts.server}${SEPARATOR}${parts.share}`,
    ...parts.segments.slice(0, -1),
  ].join(SEPARATOR);
}

export interface Crumb {
  label: string;
  /** Null for the current location, which is text rather than a link to itself. */
  href: string | null;
  kind: "server" | "share" | "directory";
}

/**
 * The trail from the server down to this path.
 *
 * The share and the directory it publishes are two crumbs, not one, and they link to two
 * different pages — the share carries the SMB ACL and the directory carries the NTFS ACL.
 * Collapsing them is the layer confusion this product exists to undo.
 */
export function resourceBreadcrumb(path: string): Crumb[] {
  const parts = parseUncPath(path);
  if (parts === null) {
    return [];
  }

  const root = `${UNC_PREFIX}${parts.server}${SEPARATOR}${parts.share}`;
  const atRoot = parts.segments.length === 0;
  const crumbs: Crumb[] = [
    { label: parts.server, href: serverHref(parts.server), kind: "server" },
    { label: parts.share, href: shareHref(root), kind: "share" },
    // The directory a share publishes is its own page, carrying the NTFS ACL that the
    // share page does not. It is the destination when the path is the share root, and a
    // step on the way when the path goes deeper.
    {
      label: `${parts.share} (root directory)`,
      href: atRoot ? null : directoryHref(root),
      kind: "directory",
    },
  ];

  let walked = root;
  parts.segments.forEach((segment, index) => {
    walked = `${walked}${SEPARATOR}${segment}`;
    const last = index === parts.segments.length - 1;
    crumbs.push({
      label: segment,
      href: last ? null : directoryHref(walked),
      kind: "directory",
    });
  });

  return crumbs;
}

/** `server|share` split back into its halves, for a key that came from the API. */
export function shareKeyParts(key: string): { serverKey: string; shareName: string } | null {
  const parts = key.split("|");
  if (parts.length !== 2 || parts[0] === "" || parts[1] === "") {
    return null;
  }
  return { serverKey: parts[0], shareName: parts[1] };
}
