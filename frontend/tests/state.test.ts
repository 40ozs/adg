/**
 * The distinction this phase exists to make.
 *
 * Every test in this file is about the same failure: an auditor reading "nothing is here"
 * when the truth is "nobody looked". The wording assertions are not cosmetic — the wording
 * *is* the feature, and a change that makes an unobserved estate read as a clean one should
 * fail the suite.
 */

import { describe, expect, it } from "vitest";

import type { ApiResult } from "@/lib/api/client";
import type { CollectionStatus } from "@/lib/contracts";
import { classify, coverageCaveat } from "@/lib/state";

interface Rows {
  items: string[];
}

const EMPTY: ApiResult<Rows> = { ok: true, data: { items: [] } };
const FULL: ApiResult<Rows> = { ok: true, data: { items: ["a", "b"] } };

function coverage(health: CollectionStatus["health"], summary = "Summary."): CollectionStatus {
  return { health, summary, concerns: [], collectors: [] };
}

const isEmpty = (data: Rows): boolean => data.items.length === 0;

describe("an empty result", () => {
  it("is an answer only when collection is healthy", () => {
    const state = classify(EMPTY, { isEmpty, subject: "shares", coverage: coverage("healthy") });

    expect(state.kind).toBe("empty");
    if (state.kind !== "empty") return;
    expect(state.reason).toBe("no-matches");
    expect(state.explanation).toContain("This emptiness is an answer");
  });

  it("is not an answer when nothing has been collected", () => {
    const state = classify(EMPTY, { isEmpty, subject: "shares", coverage: coverage("no_data") });

    expect(state.kind).toBe("empty");
    if (state.kind !== "empty") return;
    expect(state.reason).toBe("nothing-collected");
    expect(state.headline).toBe("Nothing has been collected yet");
    expect(state.explanation).toContain("not because there are no shares");
  });

  it("is not an answer when a collector failed", () => {
    const state = classify(EMPTY, {
      isEmpty,
      subject: "shares",
      coverage: coverage("failed", "A collector's most recent run failed."),
    });

    expect(state.kind).toBe("empty");
    if (state.kind !== "empty") return;
    expect(state.reason).toBe("collection-failed");
    expect(state.explanation).toContain("unknown rather than as an answer");
  });

  it("is not an answer when collection is incomplete", () => {
    const state = classify(EMPTY, {
      isEmpty,
      subject: "directories",
      coverage: coverage("incomplete"),
    });

    expect(state.kind).toBe("empty");
    if (state.kind !== "empty") return;
    expect(state.reason).toBe("collection-incomplete");
  });

  it("is never an answer when coverage could not be read", () => {
    // The dangerous default. Unknown coverage must not quietly become good coverage.
    const state = classify(EMPTY, { isEmpty, subject: "shares", coverage: null });

    expect(state.kind).toBe("empty");
    if (state.kind !== "empty") return;
    expect(state.reason).toBe("collection-incomplete");
    expect(state.explanation).toContain("cannot be interpreted");
  });

  it("claims nothing is there in exactly one of the five cases", () => {
    const reasons = (["healthy", "no_data", "failed", "incomplete"] as const).map((health) => {
      const state = classify(EMPTY, { isEmpty, subject: "x", coverage: coverage(health) });
      return state.kind === "empty" ? state.reason : "other";
    });
    const unknown = classify(EMPTY, { isEmpty, subject: "x", coverage: null });

    expect(reasons.filter((reason) => reason === "no-matches")).toHaveLength(1);
    expect(unknown.kind === "empty" && unknown.reason).not.toBe("no-matches");
  });
});

describe("data that is present", () => {
  it("is shown with no caveat when collection is healthy", () => {
    const state = classify(FULL, { isEmpty, subject: "shares", coverage: coverage("healthy") });

    expect(state.kind).toBe("ready");
    if (state.kind !== "ready") return;
    expect(state.caveat).toBeNull();
  });

  it("carries a warning when a collector failed", () => {
    // The more dangerous case: a populated table looks complete either way.
    const state = classify(FULL, { isEmpty, subject: "shares", coverage: coverage("failed") });

    expect(state.kind).toBe("ready");
    if (state.kind !== "ready") return;
    expect(state.caveat?.severity).toBe("warning");
    expect(state.caveat?.headline).toBe("Part of the estate is unobserved");
  });

  it("carries a warning when collection is incomplete", () => {
    const state = classify(FULL, { isEmpty, subject: "shares", coverage: coverage("incomplete") });

    expect(state.kind === "ready" && state.caveat?.severity).toBe("warning");
  });
});

describe("failures", () => {
  it("separates an ended session from a refusal", () => {
    const expired = classify<Rows>(
      {
        ok: false,
        failure: { kind: "unauthenticated", status: 401, message: "Session over." },
      },
      { isEmpty, subject: "shares" },
    );
    const refused = classify<Rows>(
      {
        ok: false,
        failure: {
          kind: "forbidden",
          status: 403,
          message: "This request requires the 'resources:read' capability.",
        },
      },
      { isEmpty, subject: "shares" },
    );

    expect(expired.kind).toBe("unauthenticated");
    expect(refused.kind).toBe("forbidden");
  });

  it("passes the API's own refusal message through", () => {
    // It names the capability and the roles held, which is what an administrator needs.
    const state = classify<Rows>(
      {
        ok: false,
        failure: {
          kind: "forbidden",
          status: 403,
          message: "requires the 'settings:read' capability, which none of (viewer) grant",
        },
      },
      { isEmpty, subject: "settings" },
    );

    expect(state.kind === "forbidden" && state.explanation).toContain("settings:read");
  });

  it("does not present an unreachable API as an empty estate", () => {
    const state = classify<Rows>(
      {
        ok: false,
        failure: { kind: "unreachable", status: null, message: "No answer." },
      },
      { isEmpty, subject: "shares", coverage: coverage("healthy") },
    );

    expect(state.kind).toBe("error");
    if (state.kind !== "error") return;
    expect(state.headline).toBe("ADG could not reach its API");
  });

  it("prefers the API's detail over the generic message", () => {
    const state = classify<Rows>(
      {
        ok: false,
        failure: {
          kind: "invalid_request",
          status: 422,
          message: "That request could not be understood.",
          detail: "A search term needs at least 2 characters.",
        },
      },
      { isEmpty, subject: "matches" },
    );

    expect(state.kind === "error" && state.explanation).toBe(
      "A search term needs at least 2 characters.",
    );
  });
});

describe("coverageCaveat", () => {
  it("says nothing when there is nothing to say", () => {
    expect(coverageCaveat(coverage("healthy"))).toBeNull();
    expect(coverageCaveat(null)).toBeNull();
  });

  it("is informational, not alarming, before anything has run", () => {
    expect(coverageCaveat(coverage("no_data"))?.severity).toBe("info");
  });
});
