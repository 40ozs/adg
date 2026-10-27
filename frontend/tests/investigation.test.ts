/**
 * `lib/investigation.ts`: the guided investigation, worded (ADG-DEMO-02).
 *
 * Every function under test words something the engine computed; none decides anything.
 * The assertions are therefore about faithfulness — the hop order is the path's, the
 * evidence is the edge's, the impact sentence matches the impact the engine assigned — and
 * about the two things wording can still get wrong on its own: calling an unsettled answer
 * settled, and letting a state be told apart by color alone.
 */

import { describe, expect, it } from "vitest";

import type { EvidenceState, InvestigationView } from "@/lib/derived";
import { cautions, SYSTEM_MAPPING_NEXT_STEP } from "@/lib/explanation";
import {
  aboutLabel,
  basisLabel,
  conclusionSentence,
  formatInstant,
  impactSentence,
  INVESTIGATE_PATH,
  investigateHref,
  investigationQuestion,
  investigationSummary,
  nextStepWording,
  otherRoutes,
  placementLabel,
  presenceWording,
  routeClassification,
  routeHops,
  routeKindLabel,
  routeLine,
  stateLabel,
  stateSymbol,
} from "@/lib/investigation";
import {
  APOLLO_PATH,
  FATIMA_SID,
  ORPHAN,
  ZEPHYR,
  apollo,
  apolloWithUnreadShare,
  systemConflictingInvestigated,
  systemOverShareInvestigated,
} from "./investigation-factories";

function summaryOf(body = apollo()): InvestigationView {
  const investigation = body.investigation;
  if (investigation === undefined || investigation === null) {
    throw new Error("the factory carries an investigation");
  }
  return investigation;
}

describe("the entry point", () => {
  it("links the same pair the explanation takes, by query parameter", () => {
    const href = investigateHref(FATIMA_SID, APOLLO_PATH, { access_path: "remote_smb" });

    expect(href.startsWith(`${INVESTIGATE_PATH}?`)).toBe(true);
    const params = new URLSearchParams(href.split("?")[1]);
    expect(params.get("principal")).toBe(FATIMA_SID);
    expect(params.get("resource")).toBe(APOLLO_PATH);
    expect(params.get("access_path")).toBe("remote_smb");
  });

  it("states the question with the principal's name and the folder's path", () => {
    expect(investigationQuestion(apollo())).toBe(
      `Why does Fatima Gordon have access to ${APOLLO_PATH}?`,
    );
  });
});

describe("the routes", () => {
  it("tells direct from derived and explicit from inherited, on two axes", () => {
    expect(routeKindLabel("direct", 0)).toBe("Direct entry");
    expect(routeKindLabel("group", 1)).toBe("Through 1 group membership");
    expect(routeKindLabel("group", 5)).toBe("Through 5 nested groups");
    expect(routeKindLabel("assumed", 0)).toBe("Through an assumed token SID");
    expect(placementLabel("explicit", "ntfs")).toBe("Set explicitly on this folder");
    expect(placementLabel("explicit", "smb_share")).toBe("Set on the share");
    expect(placementLabel("inherited", "ntfs")).toBe("Inherited from a parent folder");

    const [direct, inherited] = apollo().paths;
    expect(routeClassification(direct)).toEqual({ reach: "direct", placement: "explicit" });
    expect(routeClassification(inherited)).toEqual({
      reach: "derived (5 groups)",
      placement: "inherited",
    });
  });

  it("walks a route's hops in the path's own order, principal first", () => {
    const body = apollo();
    const inherited = body.paths[1];
    const hops = routeHops(body, inherited);

    expect(hops.map((hop) => hop.edge.id)).toEqual(inherited.edges);
    expect(hops.map((hop) => hop.sentence)).toEqual([
      "Fatima Gordon is a member of ENG-Mobile",
      "ENG-Mobile is a member of Engineering-Frontend",
      "Engineering-Frontend is a member of Engineering",
      "Engineering is a member of Engineering-FS",
      "Engineering-FS is a member of Share_Engineering_Modify",
      "NTFS entry #5 names Share_Engineering_Modify: Allow Modify",
      "NTFS entry #5 applies to this folder, inherited from \\\\GP-ENG-FS01\\Engineering",
    ]);
    expect(hops[0].principal?.display_name).toBe("Fatima Gordon");
  });

  it("attaches each hop's own evidence, by edge id", () => {
    const body = apollo();
    const hops = routeHops(body, body.paths[0]);

    expect(hops.map((hop) => hop.evidence?.edge_id)).toEqual(body.paths[0].edges);
    expect(hops.map((hop) => hop.evidence?.collector)).toEqual(["ntfs", "ntfs"]);
    const membership = routeHops(body, body.paths[2])[0];
    expect(membership.relationship).toBe("group membership");
    expect(membership.evidence?.collector).toBe("active_directory");
  });

  it("says a hop's provenance was not reported, rather than that it has none", () => {
    const body = { ...apollo(), evidence: undefined };
    const [hop] = routeHops(body, body.paths[0]);

    expect(hop.evidence).toBeNull();
    expect(basisLabel(hop.evidence?.basis)).toBe("Not reported");
  });

  it("lists every route that is not a primary one", () => {
    const body = apollo();

    expect(otherRoutes(body, summaryOf(body)).map((path) => path.id)).toEqual(["p0001", "p0003"]);
  });

  it("names a route by its chain and entry", () => {
    const body = apollo();
    const [ntfs, share] = summaryOf(body).primary_routes;

    expect(routeLine(body, ntfs)).toBe(
      "Folder permissions (NTFS ACL): Fatima Gordon → NTFS entry #1 (Allow Modify)",
    );
    expect(routeLine(body, share)).toBe(
      "Share permissions (SMB share ACL): Fatima Gordon → ENG-Mobile → share entry #3 (Allow Modify)",
    );
  });
});

describe("what could not be confirmed", () => {
  it("gives every state its own word and symbol, so color is never the only signal", () => {
    const states: EvidenceState[] = [
      "confirmed",
      "ambiguous",
      "missing",
      "conflicting",
      "assumed",
      "derived",
      "context",
    ];

    expect(new Set(states.map(stateLabel)).size).toBe(states.length);
    expect(new Set(states.map(stateSymbol)).size).toBe(states.length);
  });

  it("marks an identity nobody resolved as unresolved, with its SID", () => {
    expect(aboutLabel(ORPHAN)).toBe(
      `GPCORP\\lhansen (unresolved SID ${ORPHAN.sid})`,
    );
    expect(aboutLabel(ZEPHYR)).toBe("Proj-Zephyr-RW");
  });

  it("words each impact by what the engine decided, and never as settled when it is not", () => {
    expect(impactSentence("extent", "present")).toMatch(/does not affect whether access exists/i);
    expect(impactSentence("presence", "present")).toMatch(/possibly to none/);
    expect(impactSentence("presence", "undetermined")).toMatch(/cannot be concluded/);
    expect(impactSentence("none", "present")).toMatch(/cannot change this answer/);
  });

  it("explains that the canonical uncertainty does not take the access away", () => {
    const body = apollo();

    expect(conclusionSentence(summaryOf(body), body)).toBe(
      "Access is established through the routes shown. 2 open questions remain, and none of " +
        "them can take that access away: they mean the rights shown are a lower bound.",
    );
  });

  it("calls the answer provisional when an open question could take the access away", () => {
    const body = apolloWithUnreadShare();

    expect(conclusionSentence(summaryOf(body), body)).toMatch(/provisional/);
  });

  it("never turns an undetermined SYSTEM answer into a negative", () => {
    const body = systemOverShareInvestigated();
    const sentence = conclusionSentence(summaryOf(body), body);

    expect(sentence).toMatch(/not a finding of no access/);
    expect(presenceWording("undetermined", body).word).toBe("Not determined");
  });
});

describe("what to do next", () => {
  it("words every step the canonical investigation proposes, in the engine's order", () => {
    const body = apollo();
    const investigation = summaryOf(body);
    const steps = investigation.next_steps.map((step) => nextStepWording(step, body, investigation));

    expect(steps[0].text).toBe(
      `Identify ${ORPHAN.sid}, last known as GPCORP\\lhansen: an ACL here names it and no ` +
        "collector could say what it is. Until it is identified, Fatima Gordon cannot be ruled " +
        "out of it. If it is an account that no longer exists, the orphaned entry is the clean-up.",
    );
    // The canonical principal address: the key is the route, not a query parameter.
    expect(steps[0].href).toBe(`/identities/principal/${ORPHAN.sid}`);
    expect(steps[1].text).toMatch(/^Collect the membership of Proj-Zephyr-RW, or confirm it is empty/);
    expect(steps[2].text).toMatch(/^Review Fatima Gordon's direct entry on the folder permissions/);
    expect(steps[2].text).toContain("measured to change nothing");
    expect(steps[2].href).toMatch(/^\/access\/explain\?.*#removals$/);
  });

  it("reuses ADG-DEMO-01's wording to settle a SYSTEM mapping", () => {
    for (const body of [systemOverShareInvestigated(), systemConflictingInvestigated()]) {
      const investigation = summaryOf(body);
      const [step] = investigation.next_steps;
      const state = body.investigation?.open_questions[0].state as "ambiguous" | "conflicting";

      expect(nextStepWording(step, body, investigation).text).toBe(SYSTEM_MAPPING_NEXT_STEP[state]);
    }
  });
});

describe("the seven answers", () => {
  it("answers in order, each from a computed field", () => {
    const body = apollo();
    const summary = investigationSummary(body, summaryOf(body));

    expect(summary.map((item) => item.question)).toEqual([
      "Is access present?",
      "What is the strongest supported path?",
      "Is that path direct or inherited?",
      "What evidence supports it?",
      "What evidence remains incomplete or ambiguous?",
      "Does the uncertainty affect the final conclusion?",
      "What should an investigator inspect next?",
    ]);
    expect(summary[0].answer[0]).toMatch(/^Yes\. Fatima Gordon holds Modify here\. A lower bound/);
    expect(summary[1].answer).toHaveLength(2);
    expect(summary[2].answer[0]).toMatch(/^Folder permissions \(NTFS ACL\): Direct entry; set explicitly on this folder\. Removing it changes nothing/);
    expect(summary[2].answer[1]).toMatch(/Through 1 group membership; set on the share/);
    expect(summary[3].answer[0]).toBe(
      "Folder permissions (NTFS ACL): 2 of 2 relationships observed (NTFS collector), last observed 2026-10-01 03:12 UTC.",
    );
    expect(summary[3].answer[1]).toBe(
      "Share permissions (SMB share ACL): 3 of 3 relationships observed (Active Directory collector, SMB share collector), last observed 2026-10-01 03:12 UTC.",
    );
    expect(summary[4].answer).toHaveLength(2);
    expect(summary[4].answer[0]).toMatch(/^Missing evidence — GPCORP\\lhansen \(unresolved SID/);
    expect(summary[4].answer[1]).toMatch(/^Missing evidence — Proj-Zephyr-RW: /);
    expect(summary[5].answer[0]).toMatch(/none of them can take that access away/);
    expect(summary[6].answer[0]).toMatch(new RegExp(`^Identify ${ORPHAN.sid}, last known as`));
  });

  it("has no strongest path to name when access is not determined", () => {
    const body = systemOverShareInvestigated();
    const summary = investigationSummary(body, summaryOf(body));

    expect(summary[0].answer[0]).toMatch(/^Not determined\./);
    expect(summary[1].answer).toEqual(["None: no route delivers a right that survives."]);
    expect(summary[4].answer[0]).toMatch(/^Ambiguous — SYSTEM/);
  });
});

describe("instants", () => {
  it("are UTC to the minute, whatever the browser's locale", () => {
    expect(formatInstant("2026-10-01T03:12:35Z")).toBe("2026-10-01 03:12 UTC");
    expect(formatInstant(null)).toBe("—");
  });
});

describe("cautions over a repeated condition", () => {
  it("number the repeat, so two unenumerated trustees are two distinct notes", () => {
    const ids = cautions(apollo()).map((note) => note.id);

    expect(ids).toContain("finding-trustee_membership_unobserved");
    expect(ids).toContain("finding-trustee_membership_unobserved-2");
    expect(new Set(ids).size).toBe(ids.length);
  });
});
