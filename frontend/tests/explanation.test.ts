/**
 * The reading of a derived access answer.
 *
 * Every assertion here is about *wording and shape*, because that is all this module is
 * allowed to decide. The engine decides whether a right survives; these tests decide
 * whether an administrator who reads the screen will act on the right thing.
 *
 * Four of them are load-bearing:
 *
 * - the four outcomes are worded as four answers, and the inconclusive one never reads as
 *   a negative;
 * - a removal that changes nothing is never presented as a fix, and the alternatives that
 *   make it useless are named;
 * - a removal that would *widen* access is called a warning, not a remediation;
 * - the diagram and the table are drawn from one list, so neither can drop a relationship
 *   the other shows.
 */

import { describe, expect, it } from "vitest";

import {
  aceFor,
  alternateRouteWarning,
  cautions,
  certaintyNote,
  chainLabels,
  contributingPaths,
  describePath,
  describeRemoval,
  denyPaths,
  effectWording,
  explainHref,
  explanationJson,
  explanationText,
  ineffectiveRemovals,
  layoutGraph,
  nodeLabel,
  outcomeWording,
  structureRows,
  sufficientRemovals,
} from "@/lib/explanation";
import type { AccessCertainty } from "@/lib/contracts";
import type { AccessOutcome } from "@/lib/derived";
import {
  ALICE,
  ALICE_SID,
  NO_RIGHTS,
  RESOURCE_KEY,
  TEAM,
  TEAM_SID,
  applied,
  denied,
  edge,
  explanation,
  indeterminate,
  noGrant,
  node,
  path,
  removal,
  rights,
  twoRoutes,
  verdict,
} from "./explanation-factories";

describe("the four outcomes", () => {
  it.each<[AccessOutcome, string]>([
    ["granted", "Access"],
    ["denied", "Denied"],
    ["no_grant", "No access"],
    ["indeterminate", "Unknown"],
  ])("words %s as %s", (outcome, word) => {
    expect(outcomeWording(verdict({ outcome })).word).toBe(word);
  });

  it("gives every outcome a different word", () => {
    const words = (["granted", "denied", "no_grant", "indeterminate"] as AccessOutcome[]).map(
      (outcome) => outcomeWording(verdict({ outcome })).word,
    );

    expect(new Set(words).size).toBe(4);
  });

  it("never words an indeterminate answer as a negative one", () => {
    // The single most dangerous thing this screen could say. `indeterminate` means nobody
    // has collected enough to answer, and a reader who sees "no access" stops looking in
    // exactly the place they should keep going.
    const wording = outcomeWording(
      verdict({ outcome: "indeterminate", conclusive: false, certainty: "at_least" }),
    );

    expect(wording.word).toBe("Unknown");
    expect(wording.conclusive).toBe(false);
    expect(wording.guidance).toContain("not a finding of no access");
  });

  it("tells a denial apart from an absence, because the remediations differ", () => {
    const denial = outcomeWording(verdict({ outcome: "denied" }));
    const absence = outcomeWording(verdict({ outcome: "no_grant" }));

    expect(denial.guidance).toContain("relying on it");
    expect(absence.guidance).toContain("no entry here to remove");
  });

  it("carries the engine's own sentence rather than inventing one", () => {
    const wording = outcomeWording(verdict({ reason: "Something only the engine knows." }));

    expect(wording.headline).toBe("Something only the engine knows.");
  });
});

describe("certainty", () => {
  it.each<AccessCertainty>(["certain", "at_most", "at_least", "uncertain"])(
    "has a note for %s",
    (certainty) => {
      expect(certaintyNote(certainty).length).toBeGreaterThan(10);
    },
  );

  it("says which direction a bound bounds", () => {
    expect(certaintyNote("at_most")).toContain("narrow");
    expect(certaintyNote("at_least")).toContain("widen");
  });
});

describe("the diagram's layout", () => {
  it("draws every node and every edge the API sent", () => {
    const data = twoRoutes();
    const layout = layoutGraph(data.graph);

    expect(layout.nodes.map((laid) => laid.node.id).sort()).toEqual(
      data.graph.nodes.map((n) => n.id).sort(),
    );
    expect(layout.edges.map((laid) => laid.edge.id).sort()).toEqual(
      data.graph.edges.map((e) => e.id).sort(),
    );
  });

  it("is the same diagram every time, so a screenshot is evidence", () => {
    const data = twoRoutes();

    expect(layoutGraph(data.graph)).toEqual(layoutGraph(data.graph));
  });

  it("puts the subject on the left and the object on the right", () => {
    const layout = layoutGraph(twoRoutes().graph);
    const subject = layout.nodes.find((laid) => laid.node.kind === "principal");
    const object = layout.nodes.find((laid) => laid.node.kind === "resource");

    expect(subject?.column).toBe(0);
    expect(object?.column).toBeGreaterThan(subject?.column ?? 0);
    expect(object?.column).toBe(Math.max(...layout.nodes.map((laid) => laid.column)));
  });

  it("layers a nested group beyond the group that contains it", () => {
    const graph = {
      nodes: [
        node("principal:a", "principal"),
        node("group:outer", "group", { display_name: "Outer" }),
        node("group:inner", "group", { display_name: "Inner" }),
        node("resource:r", "resource"),
      ],
      edges: [
        edge("e1", "membership", "principal:a", "group:outer"),
        edge("e2", "membership", "group:outer", "group:inner"),
        // The same group is also reached directly. The longest route decides the column,
        // so no edge is drawn pointing backwards.
        edge("e3", "membership", "principal:a", "group:inner"),
      ],
    };

    const layout = layoutGraph(graph);
    const column = (id: string) => layout.nodes.find((laid) => laid.node.id === id)?.column;

    expect(column("group:outer")).toBe(1);
    expect(column("group:inner")).toBe(2);
  });

  it("terminates on a membership cycle instead of hanging the page", () => {
    // A cycle is a finding the API reports in `cycles`; the layout's job is to still draw
    // something. Relaxation is capped at one pass per node, which is what bounds this.
    const graph = {
      nodes: [
        node("principal:a", "principal"),
        node("group:one", "group", { display_name: "One" }),
        node("group:two", "group", { display_name: "Two" }),
      ],
      edges: [
        edge("e1", "membership", "principal:a", "group:one"),
        edge("e2", "membership", "group:one", "group:two"),
        edge("e3", "membership", "group:two", "group:one"),
      ],
    };

    const layout = layoutGraph(graph);

    expect(layout.nodes).toHaveLength(3);
    expect(layout.width).toBeGreaterThan(0);
    expect(layout.height).toBeGreaterThan(0);
  });

  it("drops an edge whose endpoint was not sent rather than drawing to nowhere", () => {
    const graph = {
      nodes: [node("principal:a", "principal")],
      edges: [edge("e1", "membership", "principal:a", "group:missing")],
    };

    expect(layoutGraph(graph).edges).toEqual([]);
  });

  it("gives every occupied column a heading", () => {
    const layout = layoutGraph(twoRoutes().graph);
    const occupied = new Set(layout.nodes.map((laid) => laid.column));

    expect(layout.columns.map((column) => column.index).sort()).toEqual([...occupied].sort());
  });

  it("copes with an empty graph", () => {
    const layout = layoutGraph({ nodes: [], edges: [] });

    expect(layout.nodes).toEqual([]);
    expect(layout.height).toBeGreaterThan(0);
  });
});

describe("the diagram and the table carry the same facts", () => {
  // The acceptance criterion this screen is written against: accessibility must not be a
  // paraphrase of the picture, because a paraphrase drifts.
  it("lists exactly the edges the diagram draws", () => {
    const data = twoRoutes();

    expect(structureRows(data).map((row) => row.edgeId).sort()).toEqual(
      layoutGraph(data.graph).edges.map((laid) => laid.edge.id).sort(),
    );
  });

  it("names the routes that travel each relationship", () => {
    const rows = structureRows(twoRoutes());

    expect(rows.find((row) => row.edgeId === "m1")?.paths).toEqual(["p0000"]);
    expect(rows.find((row) => row.edgeId === "t2")?.paths).toEqual(["p0001"]);
  });

  it("says which relationships cannot be removed at all", () => {
    const data = explanation({
      graph: {
        nodes: [node("principal:a", "principal"), node("assumed:S-1-1-0", "assumed_trustee", { sid: "S-1-1-0" })],
        edges: [edge("a1", "assumed_membership", "principal:a", "assumed:S-1-1-0")],
      },
    });

    expect(structureRows(data)[0]).toMatchObject({ removable: false, kind: "assumed_membership" });
  });
});

describe("naming a node", () => {
  it("numbers an entry, because two entries naming one trustee are two entries", () => {
    expect(nodeLabel(node("x", "ntfs_ace", { position: 3 }))).toBe("NTFS entry #3");
    expect(nodeLabel(node("x", "smb_ace", { position: 0 }))).toBe("Share entry #0");
  });

  it("names the three well-known SIDs ADG assumes", () => {
    expect(nodeLabel(node("x", "assumed_trustee", { sid: "S-1-1-0" }))).toBe("Everyone");
    expect(nodeLabel(node("x", "assumed_trustee", { sid: "S-1-5-11" }))).toBe("Authenticated Users");
  });

  it("falls back to the SID rather than inventing a name", () => {
    expect(nodeLabel(node("x", "group", { sid: "S-1-5-21-9-9-9-513" }))).toBe("S-1-5-21-9-9-9-513");
  });
});

describe("opening one route", () => {
  it("returns null for a route the response does not carry", () => {
    expect(describePath(twoRoutes(), "p9999")).toBeNull();
  });

  it("walks the hops the path itself names, in order", () => {
    const detail = describePath(twoRoutes(), "p0000");

    expect(detail?.steps.map((step) => step.kind)).toEqual([
      "subject",
      "membership",
      "trustee",
      "grant",
    ]);
  });

  it("joins the exact entry by key and reports where it was set", () => {
    const detail = describePath(twoRoutes(), "p0000");

    expect(detail?.ace?.position).toBe(0);
    expect(detail?.ace?.ace_key).toBe("ntfs|team");
    // A change applied to the wrong object does nothing and looks like it worked.
    expect(detail?.inheritedFrom).toBe("\\\\FS01\\Finance");
  });

  it("says an explicit entry is explicit rather than leaving the cell empty", () => {
    const detail = describePath(twoRoutes(), "p0001");

    expect(detail?.inheritedFrom).toBeNull();
  });

  it("marks a route that did not change the answer", () => {
    const data = explanation({
      paths: [path({ id: "p0000", effect: "redundant" })],
    });
    const detail = describePath(data, "p0000");

    expect(detail?.contributes).toBe(false);
    expect(detail?.worth).toContain("already settled");
  });

  it("has no entry for an ownership route, and says why", () => {
    const data = explanation({
      paths: [path({ id: "p0000", ace_key: null, ace_position: -1 })],
    });
    const detail = describePath(data, "p0000");

    expect(detail?.ace).toBeNull();
  });

  it("skips an edge id the graph does not carry rather than rendering a blank hop", () => {
    const data = twoRoutes();
    data.paths[0] = { ...data.paths[0], edges: ["m1", "ghost", "t1", "g1"] };

    expect(describePath(data, "p0000")?.steps).toHaveLength(4);
  });
});

describe("finding the entry behind a route", () => {
  it("has nothing to find when the route has no entry", () => {
    expect(aceFor(twoRoutes(), null)).toBeNull();
  });

  it("finds an entry that denied", () => {
    expect(aceFor(denied(), "ntfs|deny-team")?.ace_type).toBe("deny");
  });

  it("finds an entry that matched and took nothing", () => {
    // The most confusing entry on a real ACL. An inspector that quietly found nothing for
    // it would be hiding exactly the one somebody is about to delete.
    const superseded = applied({ ace_key: "ntfs|superseded", position: 5 });
    const base = explanation();
    const data = {
      ...base,
      effective: {
        ...base.effective,
        ntfs: { ...base.effective.ntfs, superseded: [superseded] },
      },
    };

    expect(aceFor(data, "ntfs|superseded")?.position).toBe(5);
  });

  it("searches the share layer too", () => {
    const shareAce = applied({ layer: "smb_share", ace_key: "smb|everyone" });
    const base = explanation();
    const data = {
      ...base,
      effective: {
        ...base.effective,
        share: { ...base.effective.share!, granted_by: [shareAce] },
      },
    };

    expect(aceFor(data, "smb|everyone")?.layer).toBe("smb_share");
  });

  it("returns null for a key nothing carries", () => {
    expect(aceFor(twoRoutes(), "ntfs|nothing")).toBeNull();
  });
});

describe("what a removal would do", () => {
  it("never offers a removal that changes nothing as a fix", () => {
    const data = twoRoutes();
    const note = describeRemoval(data, data.removal_targets[0]);

    expect(note.headline).toBe("Changes nothing");
    expect(note.offerAsFix).toBe(false);
    expect(note.detail).toContain("Access survives this");
  });

  it("names the routes that make it useless", () => {
    const data = twoRoutes();
    const note = describeRemoval(data, data.removal_targets[0]);

    expect(note.alternates.map((alternate) => alternate.id)).toEqual(["p0001"]);
  });

  it("calls a removal that would widen access a warning, not a remediation", () => {
    // The edge carries a Deny. Removing it grants. Offering it as a fix would be offering
    // the opposite of one.
    const data = denied();
    const note = describeRemoval(data, data.removal_targets[0]);

    expect(note.headline).toBe("Would widen access");
    expect(note.tone).toBe("bad");
    expect(note.offerAsFix).toBe(false);
  });

  it("distinguishes a partial removal from a complete one", () => {
    const data = explanation({
      removal_targets: [
        removal({
          edge_id: "partial",
          revokes_all_access: false,
          rights_removed: rights({ label: "Write" }),
          rights_after: rights({ label: "Read" }),
        }),
      ],
    });
    const note = describeRemoval(data, data.removal_targets[0]);

    expect(note.headline).toBe("Narrows access");
    expect(note.detail).toContain("Access is not revoked");
  });

  it("reports a removal that ends all access", () => {
    const data = explanation({ removal_targets: [removal()] });
    const note = describeRemoval(data, data.removal_targets[0]);

    expect(note.headline).toBe("Removes all access");
    expect(note.offerAsFix).toBe(true);
  });

  it("drops an alternate route id the response does not carry", () => {
    const data = explanation({
      removal_targets: [removal({ changes_nothing: true, alternate_paths: ["p9999"] })],
    });

    expect(describeRemoval(data, data.removal_targets[0]).alternates).toEqual([]);
  });
});

describe("which removals are fixes", () => {
  it("counts a removal that ends access", () => {
    expect(sufficientRemovals(explanation({ removal_targets: [removal()] }))).toHaveLength(1);
  });

  it("does not count one that changes nothing, whatever else it claims", () => {
    const data = explanation({
      removal_targets: [removal({ changes_nothing: true, revokes_all_access: true })],
    });

    expect(sufficientRemovals(data)).toHaveLength(0);
    expect(ineffectiveRemovals(data)).toHaveLength(1);
  });
});

describe("the alternate-route warning", () => {
  it("says more than one change is needed when no single removal helps", () => {
    const warning = alternateRouteWarning(twoRoutes());

    expect(warning).toContain("changes nothing");
    expect(warning).toContain("More than one change is needed");
  });

  it("marks the useless ones when some removals do work", () => {
    const data = twoRoutes();
    data.removal_targets = [...data.removal_targets, removal({ edge_id: "real" })];

    expect(alternateRouteWarning(data)).toContain("are not fixes");
  });

  it("forbids believing any removal when the enumeration was cut short", () => {
    // `complete: false` outranks everything: a route nobody enumerated can still deliver
    // the rights, so no measured removal is known to be sufficient.
    const data = { ...twoRoutes(), complete: false };

    expect(alternateRouteWarning(data)).toContain("no removal below may be believed sufficient");
  });

  it("says nothing when every removal measured is a real one", () => {
    expect(alternateRouteWarning(explanation({ removal_targets: [removal()] }))).toBeNull();
  });

  it("warns when access was granted and nothing removable was measured", () => {
    expect(alternateRouteWarning(explanation())).toContain("no single removal is known to help");
  });

  it("says nothing about a no-access answer with nothing to remove", () => {
    expect(alternateRouteWarning(noGrant())).toBeNull();
  });
});

describe("what could make the answer wrong", () => {
  it("finds nothing to say about a clean, complete answer", () => {
    expect(cautions(explanation())).toEqual([]);
  });

  it("collects every gap in an incomplete answer", () => {
    const notes = cautions(indeterminate()).map((note) => note.id);

    expect(notes).toContain("inconclusive");
    expect(notes).toContain("incomplete-paths");
    expect(notes).toContain("incomplete-token");
    expect(notes).toContain("unobserved-resource");
    expect(notes).toContain("provenance-unobserved");
    expect(notes).toContain("finding-unread_descriptor");
  });

  it("names the limits that were reached", () => {
    const note = cautions(indeterminate()).find((entry) => entry.id === "incomplete-paths");

    expect(note?.detail).toContain("max_depth");
  });

  it("reports a membership cycle as a finding about the directory", () => {
    const data = explanation({ cycles: [["group:a", "group:b", "group:a"]] });
    const note = cautions(data).find((entry) => entry.id === "cycles");

    expect(note?.headline).toBe("1 membership cycle");
    expect(note?.detail).toContain("group:a → group:b → group:a");
  });

  it("says nobody has looked when no collector has ever run", () => {
    const base = explanation();
    const data = { ...base, basis: { ...base.basis, is_empty: true, runs: 0 } };

    expect(cautions(data)[0].headline).toBe("Nothing has been collected");
  });

  it("carries the direction a finding could push the answer", () => {
    const data = explanation({
      warnings: [
        {
          condition: "assumed_token_sids",
          message: "Well-known SIDs were assumed.",
          may_overstate: true,
          may_understate: false,
        },
      ],
    });

    expect(cautions(data)[0].headline).toBe("Could overstate access");
  });
});

describe("the routes as a set", () => {
  it("separates the ones that changed the answer", () => {
    const data = explanation({
      paths: [path({ id: "p0000" }), path({ id: "p0001", effect: "redundant" })],
    });

    expect(contributingPaths(data).map((p) => p.id)).toEqual(["p0000"]);
  });

  it("finds the denials whatever they turned out to be worth", () => {
    expect(denyPaths(denied())).toHaveLength(1);
  });

  it("writes a chain of one as the subject rather than as nothing", () => {
    expect(chainLabels([ALICE])).toEqual(["Alice Smith"]);
    expect(chainLabels([ALICE, TEAM])).toEqual(["Alice Smith", "Finance-Team"]);
  });

  it("falls back to the SID when a chain link has no name", () => {
    expect(chainLabels([{ ...ALICE, display_name: null }])).toEqual([ALICE_SID]);
  });

  it("gives each effect its own word", () => {
    const labels = (["contributes", "redundant", "constrained"] as const).map(
      (effect) => effectWording(effect).label,
    );

    expect(new Set(labels).size).toBe(3);
  });
});

describe("the text export", () => {
  it("carries the verdict, the rights and the certainty", () => {
    const text = explanationText(twoRoutes());

    expect(text).toContain("VERDICT: ACCESS");
    expect(text).toContain("Effective rights: Modify (0x001301BF)");
    expect(text).toContain("Certainty: certain");
  });

  it("names both layers", () => {
    const text = explanationText(twoRoutes());

    expect(text).toContain("SHARE ACL: Full Control");
    expect(text).toContain("NTFS ACL: Modify");
  });

  it("writes every route with its chain and what it was worth", () => {
    const text = explanationText(twoRoutes());

    expect(text).toContain("[p0000]");
    expect(text).toContain("Alice Smith -> Finance-Team");
    expect(text).toContain("inherited from: \\\\FS01\\Finance");
  });

  it("says a route was reached through an assumption rather than an observation", () => {
    const data = explanation({ paths: [path({ assumed: true })] });

    expect(explanationText(data)).toContain("assumed token SID");
  });

  it("carries the alternate-route warning into the ticket", () => {
    // The export exists so a finding can travel. A finding that travels without the reason
    // its obvious fix is not one is worse than no export.
    expect(explanationText(twoRoutes())).toContain("More than one change is needed");
  });

  it("never leaves the caveats behind", () => {
    const text = explanationText(indeterminate());

    expect(text).toContain("VERDICT: UNKNOWN");
    expect(text).toContain("Conclusive: no");
    expect(text).toContain("This answer does not support a negative conclusion");
  });

  it("says there are no routes rather than printing an empty heading", () => {
    expect(explanationText(noGrant())).toContain("none — no entry on either ACL");
  });

  it("says which state it was computed over", () => {
    expect(explanationText(twoRoutes())).toContain("basis 0a6c0ae82c06105bc9e807805d354570");
  });

  it("is the same text twice, so two exports can be diffed", () => {
    // No clock and no locale formatting anywhere in it.
    expect(explanationText(twoRoutes())).toBe(explanationText(twoRoutes()));
  });
});

describe("the JSON export", () => {
  it("is the response itself, not a reshaping of it", () => {
    const data = twoRoutes();

    expect(JSON.parse(explanationJson(data))).toEqual(data);
  });

  it("is readable rather than one line", () => {
    expect(explanationJson(twoRoutes())).toContain("\n  ");
  });
});

describe("linking to an explanation", () => {
  it("puts the UNC path in the query string, encoded", () => {
    const href = explainHref(ALICE_SID, RESOURCE_KEY);

    expect(href).toContain("/access/explain?");
    expect(href).toContain(`principal=${ALICE_SID}`);
    expect(href).toContain("resource=%5C%5Cfs01%5Cfinance");
  });

  it("carries a host for a local group, and drops what is empty", () => {
    const href = explainHref(`fs01|S-1-5-32-544`, RESOURCE_KEY, { host: "fs01", access_path: "" });

    expect(href).toContain("host=fs01");
    expect(href).not.toContain("access_path");
  });
});

describe("the fixtures themselves", () => {
  // A scenario that stopped being the situation it is named for would quietly weaken every
  // test above it.
  it("has two routes and no single fix", () => {
    const data = twoRoutes();

    expect(data.paths).toHaveLength(2);
    expect(sufficientRemovals(data)).toHaveLength(0);
  });

  it("has a denial that is a cause, not merely present", () => {
    expect(denied().verdict.denials).toHaveLength(1);
    expect(denied().effective.ntfs.denied_by).toHaveLength(1);
  });

  it("has no grant and nothing to remove", () => {
    expect(noGrant().paths).toEqual([]);
    expect(noGrant().effective.rights).toEqual(NO_RIGHTS);
  });

  it("is inconclusive and incomplete in more than one direction", () => {
    const data = indeterminate();

    expect(data.verdict.conclusive).toBe(false);
    expect(data.complete).toBe(false);
    expect(data.token.membership_complete).toBe(false);
    expect(data.resource.observed).toBe(false);
  });

  it("keeps the two group SIDs apart", () => {
    expect(TEAM_SID).not.toBe(ALICE_SID);
  });
});
