/**
 * Reading and writing a proposal, without a browser.
 *
 * `lib/simulation.ts` is the one place the client understands a what-if, and the properties
 * worth pinning are the ones whose failure would be quiet:
 *
 * - a seeded `Simulate` link that names the wrong end of a membership edge would propose a
 *   change nobody asked for, and the API would accept it;
 * - a seed that filled in a field nobody typed would do the same;
 * - "this changes nothing" and "none of this applies" reading alike is the mistake the whole
 *   applicability model exists to prevent;
 * - an export that carried the conclusion and dropped the caveat is worse than no export.
 */

import { describe, expect, it } from "vitest";

import {
  changeFromSeed,
  impactHeadline,
  orderedDeltas,
  reportJson,
  reportText,
  seedFromQuery,
  simulateHref,
  summaryLine,
} from "@/lib/simulation";
import { simDelta, simReport, simSummary, simPrincipal, simRights } from "./simulation-factories";

describe("a seeded simulate link", () => {
  it("carries the change it will propose in the URL", () => {
    const href = simulateHref({
      kind: "remove_member",
      group_key: "S-1-5-21-1-2-3-1202",
      member_key: "S-1-5-21-1-2-3-1104",
    });

    expect(href.startsWith("/simulations/new?")).toBe(true);
    const params = new URLSearchParams(href.split("?")[1]);
    expect(params.get("kind")).toBe("remove_member");
    expect(params.get("group_key")).toBe("S-1-5-21-1-2-3-1202");
    expect(params.get("member_key")).toBe("S-1-5-21-1-2-3-1104");
  });

  it("survives a round trip through the query string", () => {
    const seed = {
      kind: "remove_ntfs_ace" as const,
      resource_key: "\\\\FS01\\Finance",
      ace_key: "\\\\fs01\\finance|S-1-5-21-1-2-3-1202|allow|0x001301bf|0x03",
    };

    const params = Object.fromEntries(new URLSearchParams(simulateHref(seed).split("?")[1]));

    expect(seedFromQuery(params)).toEqual(seed);
  });

  it("refuses a kind the API does not have", () => {
    // A guessed kind would open the editor with a change the API will reject, after the
    // operator has typed the rest of it.
    expect(seedFromQuery({ kind: "delete_everything" })).toBeNull();
    expect(seedFromQuery({})).toBeNull();
  });

  it("takes the first value of a repeated parameter rather than an array", () => {
    expect(seedFromQuery({ kind: ["remove_member", "add_member"], group_key: "g", member_key: "m" })
      ?.kind).toBe("remove_member");
  });
});

describe("building a change from a seed", () => {
  it("fills in nothing the seed did not carry", () => {
    // An editor that invented a mask would be proposing a change the operator did not write,
    // and the API would accept it.
    const change = changeFromSeed({ kind: "add_ntfs_ace", resource_key: "\\\\FS01\\Finance" });

    expect(change).toEqual({ kind: "add_ntfs_ace", resource_key: "\\\\FS01\\Finance" });
    expect(change).not.toHaveProperty("access_mask");
    expect(change).not.toHaveProperty("ace_type");
  });

  it("refuses a seed missing the object the change acts on", () => {
    expect(changeFromSeed({ kind: "remove_member", group_key: "g" })).toBeNull();
    expect(changeFromSeed({ kind: "remove_ntfs_ace" })).toBeNull();
    expect(changeFromSeed({ kind: "remove_share_ace" })).toBeNull();
  });

  it("drops an ace_type the API would not accept rather than passing it on", () => {
    const change = changeFromSeed({
      kind: "add_ntfs_ace",
      resource_key: "\\\\FS01\\Finance",
      ace_type: "maybe",
    });

    expect(change).not.toHaveProperty("ace_type");
  });

  it("keeps a proposal to protect a directory explicit about what it protects", () => {
    expect(changeFromSeed({ kind: "set_inheritance", resource_key: "\\\\FS01\\Finance" })).toEqual({
      kind: "set_inheritance",
      resource_key: "\\\\FS01\\Finance",
      protected: true,
    });
  });
});

describe("the impact headline", () => {
  it("separates a proposal that is safe from one that no longer applies", () => {
    // The same empty impact list, and completely different advice.
    const safe = impactHeadline(simReport({ summary: simSummary({ evaluated: 3, unchanged: 3 }) }));
    const inert = impactHeadline(simReport({ inert: true }));

    expect(safe.tone).toBe("none");
    expect(inert.tone).toBe("inert");
    expect(inert.headline).not.toEqual(safe.headline);
    expect(inert.detail).toContain("not the same as a change that is safe");
  });

  it("says when nothing moved because an alternate route survives", () => {
    const headline = impactHeadline(
      simReport({
        summary: simSummary({ evaluated: 1, unchanged: 1, alternate_paths_retained: 1 }),
      }),
    );

    expect(headline.tone).toBe("none");
    expect(headline.detail).toContain("another way");
  });

  it("leads with the loss when a proposal only takes access away", () => {
    const headline = impactHeadline(
      simReport({
        summary: simSummary({
          evaluated: 2,
          lost_access: 1,
          reduced: 1,
          principals_losing: [simPrincipal(), simPrincipal({ key: "S-1-5-21-1-2-3-1105" })],
          principals_affected: 2,
        }),
      }),
    );

    expect(headline.tone).toBe("loss");
    expect(headline.headline).toContain("2 principals");
  });

  it("calls a proposal that moves access in both directions mixed", () => {
    const headline = impactHeadline(
      simReport({
        summary: simSummary({
          evaluated: 2,
          lost_access: 1,
          gained_access: 1,
          principals_affected: 2,
        }),
      }),
    );

    expect(headline.tone).toBe("mixed");
  });
});

describe("ordering the deltas", () => {
  it("puts losses first and keeps the unchanged rows", () => {
    // An unchanged row is the evidence that somebody was looked at and found unaffected,
    // which is not the same as their not having been evaluated.
    const rows = orderedDeltas([
      simDelta({ subject: simPrincipal({ key: "c" }), direction: "unchanged" }),
      simDelta({ subject: simPrincipal({ key: "b" }), direction: "gained_access" }),
      simDelta({ subject: simPrincipal({ key: "a" }), direction: "lost_access" }),
    ]);

    expect(rows.map((row) => row.direction)).toEqual([
      "lost_access",
      "gained_access",
      "unchanged",
    ]);
    expect(rows).toHaveLength(3);
  });

  it("is stable for two rows with the same direction", () => {
    const rows = orderedDeltas([
      simDelta({ subject: simPrincipal({ key: "b" }), direction: "lost_access" }),
      simDelta({ subject: simPrincipal({ key: "a" }), direction: "lost_access" }),
    ]);

    expect(rows.map((row) => row.subject.key)).toEqual(["a", "b"]);
  });
});

describe("the text export", () => {
  const report = simReport({
    summary: simSummary({ evaluated: 1, lost_access: 1, principals_losing: [simPrincipal()] }),
    deltas: [
      simDelta({
        direction: "lost_access",
        changed: true,
        rights_before: simRights({ mask: "0x001301BF", value: 0x001301bf }),
        caveats: [
          {
            code: "loss_may_not_hold",
            description: "An unseen grant could leave them in place.",
          },
        ],
      }),
    ],
  });

  it("opens with the sentence saying nothing was applied", () => {
    // A report read six months later, by somebody who was not here, must not be mistaken for
    // a record of a change that was made.
    expect(reportText(report).split("\n")[0]).toContain("NO CHANGES WILL BE APPLIED");
  });

  it("carries every caveat beside the conclusion it qualifies", () => {
    const text = reportText(report);

    expect(text).toContain("lost_access");
    expect(text).toContain("loss_may_not_hold");
    expect(text).toContain("An unseen grant could leave them in place.");
  });

  it("says when the answer is partial", () => {
    const partial = simReport({
      complete: false,
      truncation: [
        { code: "trustee_expansion_incomplete", description: "A trustee could not be listed." },
      ],
    });

    expect(reportText(partial)).toContain("INCOMPLETE");
    expect(reportText(partial)).toContain("trustee_expansion_incomplete");
  });

  it("names the collection state, and says when it has moved", () => {
    const stale = simReport({
      baseline: { ...simReport().baseline, stale: true, current_token: "d4e5f6" },
    });

    expect(reportText(stale)).toContain("STALE");
    expect(reportText(report)).not.toContain("STALE");
  });

  it("includes the proposal name when one was given", () => {
    expect(reportText(report, "CHG-1042")).toContain("Proposal: CHG-1042");
  });
});

describe("the JSON export", () => {
  it("is the API's response body verbatim", () => {
    // A reshaping would be a second description of an answer, ageing independently of the
    // one the engine computes.
    const report = simReport();

    expect(JSON.parse(reportJson(report))).toEqual(report);
  });
});

describe("the one-line summary", () => {
  it("says nothing changed rather than reporting an empty list", () => {
    expect(summaryLine(simSummary({ evaluated: 4, unchanged: 4 }))).toContain("none changed");
  });

  it("says nothing was evaluated when nothing was", () => {
    expect(summaryLine(simSummary())).toBe("No pairs evaluated.");
  });

  it("counts each direction separately", () => {
    const line = summaryLine(simSummary({ evaluated: 3, lost_access: 1, expanded: 2 }));

    expect(line).toContain("1 lose access");
    expect(line).toContain("2 expanded");
  });
});
