/**
 * Recently viewed: the identities, servers, shares and directories this tab opened.
 *
 * A personal way back to an entity, kept in this browser tab's `sessionStorage` and nowhere
 * else (ADG-UX-RECENT-05A). ADG never receives it: this module imports nothing from
 * `lib/api` and makes no request, and the list is never logged, put in a URL or sent.
 *
 * Storage is untrusted input. A value that is not the shape below, or that another account
 * wrote, is deleted whole; an entry that is not valid on its own is dropped. Every access
 * survives a browser that throws on `sessionStorage`.
 *
 * Time comes in as a `now` argument so expiry can be tested without a clock.
 */

import type { PrincipalSummary } from "@/lib/contracts";
import { principalHrefForKey } from "@/lib/identity";
import { directoryHref, serverHref, shareHref } from "@/lib/resources";

export const RECENT_STORAGE_KEY = "adg.recentlyViewed.v1";
export const RECENT_VERSION = 1;
export const RECENT_LIMIT = 10;
export const RECENT_MAX_AGE_MS = 8 * 60 * 60 * 1000;
export const RECENT_KEY_MAX = 1024;
export const RECENT_LABEL_MAX = 200;

export type RecentKind = "identity" | "server" | "share" | "directory";

export interface RecentEntry {
  kind: RecentKind;
  key: string;
  label: string;
  /** Epoch milliseconds of the latest recorded view. */
  at: number;
}

interface RecentValue {
  v: typeof RECENT_VERSION;
  /** Hex SHA-256 of the signed-in subject: which account wrote this list, without naming it. */
  owner: string;
  entries: RecentEntry[];
}

/** The capability each kind's detail route requires: exact while ADG authorizes per capability (ADR-0014). */
export const RECENT_CAPABILITY: Record<RecentKind, string> = {
  identity: "identities:read",
  server: "resources:read",
  share: "resources:read",
  directory: "resources:read",
};

export const RECENT_KIND_LABEL: Record<RecentKind, string> = {
  identity: "Identity",
  server: "Server",
  share: "Share",
  directory: "Directory",
};

const OWNER = /^[0-9a-f]{64}$/;

/** This tab's session storage, or null when the browser blocks it (or there is no window). */
export function sessionStore(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

/** The entries this owner may still see in this tab: newest first, unexpired, at most ten. */
export function readRecent(storage: Storage, owner: string, now = Date.now()): RecentEntry[] {
  return load(storage, owner, now);
}

/**
 * The entries an account holding `capabilities` may open, deleting the rest from storage.
 * `removed` is true when this read took something away for lost access.
 */
export function readRecentFor(
  storage: Storage,
  owner: string,
  capabilities: readonly string[],
  now = Date.now(),
): { entries: RecentEntry[]; removed: boolean } {
  const all = load(storage, owner, now);
  const held = new Set(capabilities);
  const entries = all.filter((entry) => held.has(RECENT_CAPABILITY[entry.kind]));
  if (entries.length !== all.length) {
    save(storage, owner, entries);
  }
  return { entries, removed: entries.length !== all.length };
}

/** Record a successful detail view: one entry per (kind, key), newest first. */
export function recordRecent(
  storage: Storage,
  owner: string,
  view: { kind: RecentKind; key: string; label: string },
  now = Date.now(),
): void {
  const label = view.label.trim().slice(0, RECENT_LABEL_MAX);
  if (view.key === "" || view.key.length > RECENT_KEY_MAX || label === "") {
    return;
  }
  const rest = load(storage, owner, now).filter(
    (entry) => !(entry.kind === view.kind && entry.key === view.key),
  );
  save(storage, owner, [{ kind: view.kind, key: view.key, label, at: now }, ...rest]);
}

/** Drop one entry: its detail page said the object is not found. */
export function forgetRecent(
  storage: Storage,
  owner: string,
  kind: RecentKind,
  key: string,
  now = Date.now(),
): void {
  const all = load(storage, owner, now);
  const rest = all.filter((entry) => !(entry.kind === kind && entry.key === key));
  if (rest.length !== all.length) {
    save(storage, owner, rest);
  }
}

/** Delete the whole list. Never throws: sign-out must go ahead when storage is blocked. */
export function clearRecent(storage: Storage | null = sessionStore()): void {
  try {
    storage?.removeItem(RECENT_STORAGE_KEY);
  } catch {
    // Blocked storage holds no list to clear.
  }
}

/** The canonical detail route, rebuilt from (kind, key) so a route change moves every entry. */
export function recentHref(entry: Pick<RecentEntry, "kind" | "key">): string {
  switch (entry.kind) {
    case "identity":
      return principalHrefForKey(entry.key);
    case "server":
      return serverHref(entry.key);
    case "share":
      return shareHref(entry.key);
    case "directory":
      return directoryHref(entry.key);
  }
}

/**
 * An identity's label: its name, never its UPN (05A §6 stores no UPN or email). The SID is
 * the label only when no run named it.
 */
export function identityLabel(principal: PrincipalSummary): string {
  return (
    principal.display_name ??
    principal.sam_account_name ??
    principal.last_known_name ??
    principal.sid
  );
}

/** A directory's label: its final path segment, never the full UNC path. */
export function directoryLabel(path: string): string {
  return path.split(/[\\/]/).filter((part) => part !== "").pop() ?? "";
}

/* ------------------------------------------------------------------ storage */

function load(storage: Storage, owner: string, now: number): RecentEntry[] {
  let raw: string | null;
  try {
    raw = storage.getItem(RECENT_STORAGE_KEY);
  } catch {
    return [];
  }
  if (raw === null) {
    return [];
  }
  const value = parse(raw);
  // Another account's list, or one this code cannot read, is deleted before anything uses it.
  if (value === null || value.owner !== owner) {
    clearRecent(storage);
    return [];
  }
  const entries = tidy(value.entries, now);
  if (entries.length !== value.entries.length) {
    save(storage, owner, entries);
  }
  return entries;
}

function save(storage: Storage, owner: string, entries: RecentEntry[]): void {
  if (!OWNER.test(owner)) {
    return;
  }
  const value: RecentValue = { v: RECENT_VERSION, owner, entries: entries.slice(0, RECENT_LIMIT) };
  try {
    storage.setItem(RECENT_STORAGE_KEY, JSON.stringify(value));
  } catch {
    // Blocked or full: the list is a convenience, and a lost write loses nothing else.
  }
}

/** The stored value, or null when it is not version 1 of the shape. Entries are checked one by one later. */
function parse(raw: string): { owner: string; entries: unknown[] } | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { v, owner, entries } = value as Record<string, unknown>;
  if (v !== RECENT_VERSION || typeof owner !== "string" || !OWNER.test(owner) || !Array.isArray(entries)) {
    return null;
  }
  return { owner, entries };
}

/** Valid, unexpired entries, newest first, one per (kind, key), at most ten. */
function tidy(entries: unknown[], now: number): RecentEntry[] {
  const seen = new Set<string>();
  return entries
    .filter((entry): entry is RecentEntry => isEntry(entry, now))
    .map(({ kind, key, label, at }) => ({ kind, key, label, at }))
    .sort((a, b) => b.at - a.at)
    .filter((entry) => {
      const id = `${entry.kind}\u0000${entry.key}`;
      return seen.has(id) ? false : (seen.add(id), true);
    })
    .slice(0, RECENT_LIMIT);
}

function isEntry(value: unknown, now: number): boolean {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const { kind, key, label, at } = value as Record<string, unknown>;
  return (
    typeof kind === "string" &&
    Object.hasOwn(RECENT_CAPABILITY, kind) &&
    typeof key === "string" &&
    key !== "" &&
    key.length <= RECENT_KEY_MAX &&
    typeof label === "string" &&
    label.trim() !== "" &&
    label.length <= RECENT_LABEL_MAX &&
    typeof at === "number" &&
    Number.isFinite(at) &&
    at <= now &&
    now - at <= RECENT_MAX_AGE_MS
  );
}
