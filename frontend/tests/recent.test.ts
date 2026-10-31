/**
 * The Recently viewed store (ADG-UX-RECENT-05A §14 items 2–3, 7–9, 11–12, 15).
 *
 * Storage is untrusted input and time is an argument, so every rule here is checked against
 * a plain in-memory Storage and a fixed clock: no browser, no waiting eight hours.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  RECENT_KEY_MAX,
  RECENT_LABEL_MAX,
  RECENT_LIMIT,
  RECENT_MAX_AGE_MS,
  RECENT_STORAGE_KEY,
  clearRecent,
  directoryLabel,
  forgetRecent,
  identityLabel,
  readRecent,
  readRecentFor,
  recentHref,
  recordRecent,
  type RecentKind,
} from "@/lib/recent";
import { recentOwner } from "@/lib/recent-owner";
import { principal } from "./factories";

class MemoryStorage implements Storage {
  private items = new Map<string, string>();
  get length() {
    return this.items.size;
  }
  clear() {
    this.items.clear();
  }
  getItem(key: string) {
    return this.items.get(key) ?? null;
  }
  key(index: number) {
    return [...this.items.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.items.delete(key);
  }
  setItem(key: string, value: string) {
    this.items.set(key, String(value));
  }
}

/** A browser that blocks storage: every access throws, as Safari's and Firefox's do. */
class BlockedStorage extends MemoryStorage {
  getItem(): string | null {
    throw new DOMException("blocked", "SecurityError");
  }
  setItem(): void {
    throw new DOMException("blocked", "SecurityError");
  }
  removeItem(): void {
    throw new DOMException("blocked", "SecurityError");
  }
}

const ADA = recentOwner("dev:ada");
const BOB = recentOwner("dev:bob");
const T0 = Date.UTC(2026, 9, 30, 9, 0, 0);
const MINUTE = 60_000;

function stored(storage: Storage): { v: number; owner: string; entries: Record<string, unknown>[] } {
  return JSON.parse(storage.getItem(RECENT_STORAGE_KEY) ?? "null");
}

function view(kind: RecentKind, key: string, label = key) {
  return { kind, key, label };
}

describe("owner digest", () => {
  it("is the hex SHA-256 of the subject, so the subject itself is never stored", () => {
    // sha256("dev:ada"), computed independently with `shasum -a 256`.
    expect(ADA).toBe("74d0a3d7d079c98a0045bfafee53e0ca6e46b3c6855a0a1b59584c137588ef6b");
    expect(ADA).not.toBe(BOB);
    expect(recentOwner("dev:ada")).toBe(ADA);
  });
});

describe("recording", () => {
  it("stores exactly the 05A fields: v, owner, and kind/key/label/at per entry", () => {
    const storage = new MemoryStorage();
    recordRecent(storage, ADA, view("identity", "S-1-5-21-1-2-3-1104", "Jane Smith"), T0);

    expect(stored(storage)).toEqual({
      v: 1,
      owner: ADA,
      entries: [{ kind: "identity", key: "S-1-5-21-1-2-3-1104", label: "Jane Smith", at: T0 }],
    });
    expect(Object.keys(stored(storage)).sort()).toEqual(["entries", "owner", "v"]);
    expect(Object.keys(stored(storage).entries[0]).sort()).toEqual(["at", "key", "kind", "label"]);
  });

  it("lists newest first", () => {
    const storage = new MemoryStorage();
    recordRecent(storage, ADA, view("identity", "S-1"), T0);
    recordRecent(storage, ADA, view("server", "fs01"), T0 + MINUTE);

    expect(readRecent(storage, ADA, T0 + 2 * MINUTE).map((e) => e.key)).toEqual(["fs01", "S-1"]);
  });

  it("keeps one entry per (kind, key): a repeat view moves it to the front with the new label and time", () => {
    const storage = new MemoryStorage();
    recordRecent(storage, ADA, view("identity", "S-1", "Old name"), T0);
    recordRecent(storage, ADA, view("server", "fs01"), T0 + MINUTE);
    recordRecent(storage, ADA, view("identity", "S-1", "New name"), T0 + 2 * MINUTE);

    expect(readRecent(storage, ADA, T0 + 3 * MINUTE)).toEqual([
      { kind: "identity", key: "S-1", label: "New name", at: T0 + 2 * MINUTE },
      { kind: "server", key: "fs01", label: "fs01", at: T0 + MINUTE },
    ]);
  });

  it("deduplicates on (kind, key), never on the label", () => {
    const storage = new MemoryStorage();
    recordRecent(storage, ADA, view("server", "fs01", "Finance"), T0);
    recordRecent(storage, ADA, view("share", "fs01|finance", "Finance"), T0 + 1);

    expect(readRecent(storage, ADA, T0 + 2)).toHaveLength(2);
  });

  it("holds ten: the eleventh unique view evicts the oldest", () => {
    const storage = new MemoryStorage();
    for (let i = 0; i <= RECENT_LIMIT; i += 1) {
      recordRecent(storage, ADA, view("server", `srv${i}`), T0 + i);
    }

    const keys = readRecent(storage, ADA, T0 + 20).map((e) => e.key);
    expect(keys).toHaveLength(RECENT_LIMIT);
    expect(keys[0]).toBe("srv10");
    expect(keys).not.toContain("srv0");
    expect(stored(storage).entries).toHaveLength(RECENT_LIMIT);
  });

  it("does not record a key over 1,024 characters, an empty key or an empty label", () => {
    const storage = new MemoryStorage();
    recordRecent(storage, ADA, view("directory", `\\\\fs01\\${"a".repeat(RECENT_KEY_MAX)}`, "a"), T0);
    recordRecent(storage, ADA, view("server", "", "x"), T0);
    recordRecent(storage, ADA, view("server", "fs01", "   "), T0);

    expect(storage.getItem(RECENT_STORAGE_KEY)).toBeNull();
  });

  it("trims a label to 200 characters", () => {
    const storage = new MemoryStorage();
    recordRecent(storage, ADA, view("identity", "S-1", ` ${"n".repeat(300)} `), T0);

    expect(readRecent(storage, ADA, T0)[0].label).toBe("n".repeat(RECENT_LABEL_MAX));
  });
});

describe("expiry", () => {
  it("keeps an entry for eight hours and deletes it from storage after", () => {
    const storage = new MemoryStorage();
    recordRecent(storage, ADA, view("server", "old"), T0);
    recordRecent(storage, ADA, view("server", "new"), T0 + 60 * MINUTE);

    expect(readRecent(storage, ADA, T0 + RECENT_MAX_AGE_MS).map((e) => e.key)).toEqual(["new", "old"]);
    expect(readRecent(storage, ADA, T0 + RECENT_MAX_AGE_MS + 1).map((e) => e.key)).toEqual(["new"]);
    expect(stored(storage).entries.map((e) => e.key)).toEqual(["new"]);
  });

  it("applies on write too: recording drops what has expired", () => {
    const storage = new MemoryStorage();
    recordRecent(storage, ADA, view("server", "old"), T0);
    recordRecent(storage, ADA, view("server", "new"), T0 + RECENT_MAX_AGE_MS + 1);

    expect(stored(storage).entries.map((e) => e.key)).toEqual(["new"]);
  });

  it("drops a timestamp from the future or one that is not a number", () => {
    const storage = new MemoryStorage();
    storage.setItem(
      RECENT_STORAGE_KEY,
      JSON.stringify({
        v: 1,
        owner: ADA,
        entries: [
          { kind: "server", key: "future", label: "f", at: T0 + MINUTE },
          { kind: "server", key: "text", label: "t", at: "yesterday" },
          { kind: "server", key: "nan", label: "n", at: null },
          { kind: "server", key: "fine", label: "ok", at: T0 },
        ],
      }),
    );

    expect(readRecent(storage, ADA, T0).map((e) => e.key)).toEqual(["fine"]);
  });
});

describe("account binding", () => {
  it("deletes another account's list before returning anything", () => {
    const storage = new MemoryStorage();
    recordRecent(storage, ADA, view("identity", "S-1", "Ada's view"), T0);

    expect(readRecent(storage, BOB, T0)).toEqual([]);
    expect(storage.getItem(RECENT_STORAGE_KEY)).toBeNull();
  });

  it("starts a new list for the new account when it records first", () => {
    const storage = new MemoryStorage();
    recordRecent(storage, ADA, view("identity", "S-1", "Ada's view"), T0);
    recordRecent(storage, BOB, view("server", "fs01"), T0 + 1);

    expect(stored(storage).owner).toBe(BOB);
    expect(stored(storage).entries.map((e) => e.key)).toEqual(["fs01"]);
  });

  it("keeps the list for the same account: identity, not the session, owns it", () => {
    const storage = new MemoryStorage();
    recordRecent(storage, ADA, view("identity", "S-1"), T0);

    // A new session for the same subject computes the same owner.
    expect(readRecent(storage, recentOwner("dev:ada"), T0 + MINUTE)).toHaveLength(1);
  });
});

describe("capability filtering", () => {
  function four(storage: Storage) {
    recordRecent(storage, ADA, view("identity", "S-1"), T0);
    recordRecent(storage, ADA, view("server", "fs01"), T0 + 1);
    recordRecent(storage, ADA, view("share", "fs01|finance"), T0 + 2);
    recordRecent(storage, ADA, view("directory", "\\\\fs01\\finance\\q"), T0 + 3);
  }

  it("needs identities:read for identities and resources:read for the rest", () => {
    const storage = new MemoryStorage();
    four(storage);

    expect(readRecentFor(storage, ADA, ["identities:read", "resources:read"], T0 + 4)).toEqual({
      entries: expect.any(Array),
      removed: false,
    });
    expect(readRecentFor(storage, ADA, ["identities:read", "resources:read"], T0 + 4).entries).toHaveLength(4);
  });

  it("deletes entries the account can no longer open, and says so once", () => {
    const storage = new MemoryStorage();
    four(storage);

    const first = readRecentFor(storage, ADA, ["identities:read"], T0 + 4);
    expect(first.entries.map((e) => e.kind)).toEqual(["identity"]);
    expect(first.removed).toBe(true);
    expect(stored(storage).entries.map((e) => e.kind)).toEqual(["identity"]);

    // Gone from storage, so regaining the role does not bring them back, and no notice repeats.
    const again = readRecentFor(storage, ADA, ["identities:read", "resources:read"], T0 + 5);
    expect(again).toEqual({ entries: first.entries, removed: false });
  });

  it("removes identities when identities:read is lost", () => {
    const storage = new MemoryStorage();
    four(storage);

    expect(readRecentFor(storage, ADA, ["resources:read"], T0 + 4).entries.map((e) => e.kind)).toEqual([
      "directory",
      "share",
      "server",
    ]);
  });
});

describe("untrusted storage", () => {
  it.each([
    ["malformed JSON", "{not json"],
    ["a non-object", "42"],
    ["null", "null"],
    ["another version", JSON.stringify({ v: 2, owner: "a".repeat(64), entries: [] })],
    ["no version", JSON.stringify({ owner: "a".repeat(64), entries: [] })],
    ["entries not an array", JSON.stringify({ v: 1, owner: "a".repeat(64), entries: {} })],
    ["an owner that is not a digest", JSON.stringify({ v: 1, owner: "dev:ada", entries: [] })],
  ])("deletes %s whole and reads as empty", (_, raw) => {
    const storage = new MemoryStorage();
    storage.setItem(RECENT_STORAGE_KEY, raw);

    expect(readRecent(storage, ADA, T0)).toEqual([]);
    expect(storage.getItem(RECENT_STORAGE_KEY)).toBeNull();
  });

  it("drops invalid entries one by one and writes back the valid rest", () => {
    const storage = new MemoryStorage();
    storage.setItem(
      RECENT_STORAGE_KEY,
      JSON.stringify({
        v: 1,
        owner: ADA,
        entries: [
          { kind: "finding", key: "r-1", label: "Everyone has Full Control", at: T0 },
          { kind: "review", key: "c-1", label: "Q4 review", at: T0 },
          { kind: "server", label: "no key", at: T0 },
          { kind: "server", key: "fs02", at: T0 },
          { kind: "server", key: "fs03", label: "", at: T0 },
          { kind: "server", key: "fs04", label: "x".repeat(RECENT_LABEL_MAX + 1), at: T0 },
          "not an entry",
          null,
          { kind: "server", key: "fs01", label: "FS01", at: T0, upn: "ada@example.test" },
        ],
      }),
    );

    expect(readRecent(storage, ADA, T0)).toEqual([{ kind: "server", key: "fs01", label: "FS01", at: T0 }]);
    // Extra fields are not carried forward either.
    expect(stored(storage).entries).toEqual([{ kind: "server", key: "fs01", label: "FS01", at: T0 }]);
  });

  it("cuts an oversized array to the ten newest and removes duplicates", () => {
    const storage = new MemoryStorage();
    const entries = Array.from({ length: 50 }, (_, i) => ({
      kind: "server",
      key: `srv${i % 25}`,
      label: `srv${i % 25}`,
      at: T0 - i,
    }));
    storage.setItem(RECENT_STORAGE_KEY, JSON.stringify({ v: 1, owner: ADA, entries }));

    const keys = readRecent(storage, ADA, T0).map((e) => e.key);
    expect(keys).toEqual(Array.from({ length: 10 }, (_, i) => `srv${i}`));
    expect(stored(storage).entries).toHaveLength(10);
  });

  it("never throws when storage is blocked", () => {
    const storage = new BlockedStorage();

    expect(() => recordRecent(storage, ADA, view("server", "fs01"), T0)).not.toThrow();
    expect(readRecent(storage, ADA, T0)).toEqual([]);
    expect(readRecentFor(storage, ADA, ["resources:read"], T0)).toEqual({ entries: [], removed: false });
    expect(() => forgetRecent(storage, ADA, "server", "fs01", T0)).not.toThrow();
    expect(() => clearRecent(storage)).not.toThrow();
    expect(() => clearRecent(null)).not.toThrow();
  });
});

describe("forget and clear", () => {
  it("forgets one (kind, key) and leaves the rest", () => {
    const storage = new MemoryStorage();
    recordRecent(storage, ADA, view("server", "fs01"), T0);
    recordRecent(storage, ADA, view("share", "fs01"), T0 + 1);

    forgetRecent(storage, ADA, "server", "fs01", T0 + 2);

    expect(readRecent(storage, ADA, T0 + 2).map((e) => e.kind)).toEqual(["share"]);
  });

  it("clears the whole key", () => {
    const storage = new MemoryStorage();
    recordRecent(storage, ADA, view("server", "fs01"), T0);

    clearRecent(storage);

    expect(storage.length).toBe(0);
  });
});

describe("labels and links", () => {
  it("labels a directory with its final segment, never the UNC path", () => {
    expect(directoryLabel("\\\\HOST\\Share\\Department\\Confidential\\Project")).toBe("Project");
    expect(directoryLabel("\\\\FS01\\Finance\\Quarterly\\2026\\")).toBe("2026");
    expect(directoryLabel("//FS01/Finance")).toBe("Finance");
  });

  it("labels an identity by name and never by UPN, falling back to the SID", () => {
    const upn = "jane.smith@example.test";
    expect(identityLabel(principal({ display_name: "Jane Smith", user_principal_name: upn }))).toBe("Jane Smith");
    expect(identityLabel(principal({ sam_account_name: "jsmith", user_principal_name: upn }))).toBe("jsmith");
    expect(identityLabel(principal({ user_principal_name: upn }))).toBe("S-1-5-21-1-2-3-1104");
  });

  it("rebuilds each kind's canonical route from its key", () => {
    expect(recentHref({ kind: "identity", key: "fs01|S-1-5-32-544" })).toBe(
      "/identities/principal/fs01%7CS-1-5-32-544",
    );
    expect(recentHref({ kind: "server", key: "fs01" })).toBe("/resources/server?key=fs01");
    expect(recentHref({ kind: "share", key: "fs01|finance" })).toBe("/resources/share?key=fs01%7Cfinance");
    expect(recentHref({ kind: "directory", key: "\\\\fs01\\finance" })).toBe(
      "/resources/directory?key=%5C%5Cfs01%5Cfinance",
    );
  });
});

describe("no network", () => {
  it("imports nothing from lib/api and makes no request (05A §14 item 3)", () => {
    const files = ["../lib/recent.ts", "../lib/identity.ts", "../lib/resources.ts", "../lib/paging.ts"];
    for (const file of files) {
      const source = readFileSync(fileURLToPath(new URL(file, import.meta.url)), "utf8");
      expect(source, file).not.toMatch(/@\/lib\/api|from "\.\.?\/api/);
      expect(source, file).not.toMatch(/\bfetch\(|XMLHttpRequest|sendBeacon|WebSocket|EventSource/);
    }
    const recent = readFileSync(fileURLToPath(new URL("../lib/recent.ts", import.meta.url)), "utf8");
    expect(recent).not.toMatch(/localStorage|indexedDB|document\.cookie|console\./);
  });
});
