/**
 * Directory access: the principal view that groups reachable directories into access roots.
 *
 * Three modes of one view, switched by `?mode=` so the principal (the path) never changes:
 *
 * - **Summary** (default) — one row per access root, with reachable descendants and
 *   exceptions. Server-rendered from `/directory-access`.
 * - **Tree** — the share roots, opened a branch at a time. The browser asks the proxy for
 *   one level per expansion and keeps what it has loaded for as long as the page is open.
 * - **All paths** — the existing flat, paged listing, unchanged: every evaluated directory.
 *
 * Directories travel as query parameters, never path segments: a UNC key cannot pass the
 * proxy as a segment (`lib/api/proxy.ts`).
 */

import type {
  DirectoryTreeResponse,
  DirectoryVerdictView,
  ExceptionReason,
} from "@/lib/contracts";
import { principalHrefForKey } from "@/lib/identity";
import { hrefWith } from "@/lib/paging";

export type DirectoryMode = "summary" | "tree" | "paths";

export const DIRECTORY_VIEW = "directories";

export const DIRECTORY_MODES: readonly { id: DirectoryMode; label: string; hint: string }[] = [
  {
    id: "summary",
    label: "Summary",
    hint: "Where access begins, how far it runs, and where it changes.",
  },
  { id: "tree", label: "Tree", hint: "The shares, opened one folder at a time." },
  { id: "paths", label: "All paths", hint: "Every evaluated directory, one row each." },
];

/** The mode a `?mode=` value names; anything else is the summary. */
export function directoryMode(value: string | undefined): DirectoryMode {
  return DIRECTORY_MODES.find((mode) => mode.id === value)?.id ?? "summary";
}

/** A Directory access address for one principal, keeping the given filters. */
export function directoryAccessHref(
  key: string,
  params: { mode?: DirectoryMode; q?: string; access?: string; share?: string } = {},
): string {
  const { mode, ...filters } = params;
  return hrefWith(principalHrefForKey(key, DIRECTORY_VIEW), {
    mode: mode === "summary" ? undefined : mode,
    ...filters,
  });
}

const PROXY = "/api/adg/api/v1/access/principals";

/** The proxy address of one level of the tree. No parent means the share roots. */
export function treeUrl(key: string, parent?: string, cursor?: string | null): string {
  return hrefWith(`${PROXY}/${encodeURIComponent(key)}/directory-access/tree`, {
    parent,
    cursor: cursor ?? undefined,
  });
}

/** The proxy address of one root's exceptions. */
export function exceptionsUrl(key: string, root: string): string {
  return hrefWith(`${PROXY}/${encodeURIComponent(key)}/directory-access/exceptions`, {
    root,
    limit: 500,
  });
}

/** A download of the summary or of every evaluated directory, as CSV. */
export function exportHref(key: string, mode: "summary" | "paths"): string {
  return hrefWith("/api/directory-access/export", { key, mode });
}

/** Whether a reason means access continues through the folder or stops at it. */
export function reasonTone(reason: ExceptionReason): "change" | "stop" {
  return reason === "access_denied" ||
    reason === "access_ends" ||
    reason === "access_unknown" ||
    reason === "not_observed"
    ? "stop"
    : "change";
}

/** One word for an engine verdict, never colour alone. */
export function verdictWord(verdict: DirectoryVerdictView | null, named: boolean): string {
  if (verdict === null) {
    return named ? "Not evaluated" : "No access";
  }
  switch (verdict.outcome) {
    case "granted":
      return verdict.rights.primary || verdict.rights.label;
    case "denied":
      return "Denied";
    case "indeterminate":
      return "Not established";
    default:
      return "No access";
  }
}

/** "1 directory", "184 directories". */
export function directories(count: number): string {
  return `${count.toLocaleString("en-US")} ${count === 1 ? "directory" : "directories"}`;
}

/* --------------------------------------------------------------- branch loading */

export type BranchResult =
  | { ok: true; data: DirectoryTreeResponse }
  | { ok: false; message: string };

export type BranchFetch = (url: string) => Promise<BranchResult>;

/**
 * Loads tree levels once each, for as long as the page that made it is open.
 *
 * A level is requested on its first expansion only: a second expansion, a collapse and
 * re-expand, or a double click while the first request is in flight all share one request.
 * A failed request is forgotten so that trying again asks again. Nothing is requested that
 * the reader did not open, so there is no prefetch to storm the API.
 */
export class BranchLoader {
  private readonly cache = new Map<string, Promise<BranchResult>>();
  private requests = 0;

  constructor(
    private readonly principalKey: string,
    private readonly fetchBranch: BranchFetch,
  ) {}

  /** How many requests this loader has made, which is what the tests count. */
  get requestCount(): number {
    return this.requests;
  }

  load(parent?: string, cursor?: string | null): Promise<BranchResult> {
    const id = `${parent ?? ""}\u0000${cursor ?? ""}`;
    const cached = this.cache.get(id);
    if (cached) {
      return cached;
    }
    this.requests += 1;
    const pending = this.fetchBranch(treeUrl(this.principalKey, parent, cursor)).then(
      (result) => {
        if (!result.ok) {
          this.cache.delete(id);
        }
        return result;
      },
      (error: unknown) => {
        this.cache.delete(id);
        return {
          ok: false as const,
          message: error instanceof Error ? error.message : String(error),
        };
      },
    );
    this.cache.set(id, pending);
    return pending;
  }
}

/** The browser's fetch of one level through the proxy, as a `BranchResult`. */
export async function fetchBranchThroughProxy(url: string): Promise<BranchResult> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const body = (await response.json().catch(() => null)) as
    | DirectoryTreeResponse
    | { detail?: string }
    | null;
  if (!response.ok || body === null) {
    const detail = body && "detail" in body ? body.detail : undefined;
    return {
      ok: false,
      message: detail ?? `The tree could not be loaded (HTTP ${response.status}).`,
    };
  }
  return { ok: true, data: body as DirectoryTreeResponse };
}
