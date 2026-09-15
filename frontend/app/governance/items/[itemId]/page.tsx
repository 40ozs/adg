import type { JSX } from "react";
import Link from "next/link";

import { fetchReviewContext, fetchReviewItem } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { DecisionForm } from "@/components/DecisionForm";
import {
  AccessComparison,
  DecisionHistory,
  DriftNotice,
  FindingsPanel,
  GrantEvidenceTable,
  LastChangePanel,
  ReachPanel,
} from "@/components/Governance";
import { principalLabel, targetLabel } from "@/lib/governance";
import { SignedOutNotice } from "@/components/SignedOutNotice";

/**
 * One review item, and the evidence a decision needs.
 *
 * The acceptance criterion this screen exists for is that a reviewer can make an
 * evidence-based decision **without opening AD tools**. So everything they would otherwise
 * go looking for is here, in the order the questions arrive:
 *
 * 1. *What am I certifying?* — the frozen entries, as they stood at the baseline. The
 *    subject of the decision, and the only thing on the page that is.
 * 2. *Is it still like that?* — the drift verdict, beside the evidence and never replacing
 *    it. The item is not rewritten; a campaign that refreshed its own items would make every
 *    attestation already recorded a statement about whatever the grant became.
 * 3. *What does it actually let them do?* — the effective answer at the baseline and now.
 *    An entry is not an answer: a Full Control allow under a deny that outranks it grants
 *    nothing.
 * 4. *Why do they have it, and would revoking this stop it?* — the causal explanation, split
 *    into the direct entry and the routes through groups. The most consequential panel here:
 *    a revoke recorded in the belief that access ends, on a grant also held through a group,
 *    is an attestation that says something untrue.
 * 5. *Is anything known to be wrong here?* — open risk findings naming this target or this
 *    principal.
 * 6. *When did this last move?* — the change feed for these entries.
 *
 * Two calls rather than one: the item and its decision history are cheap, and the context is
 * the expensive part. Both are awaited together, so the page costs one round trip either way.
 */
export default async function ReviewItemPage({
  params,
}: {
  params: Promise<{ itemId: string }>;
}): Promise<JSX.Element> {
  const [viewer, { itemId }] = await Promise.all([currentViewer(), params]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }
  const token = viewer.session.accessToken;
  const [detail, context] = await Promise.all([
    fetchReviewItem(token, itemId),
    fetchReviewContext(token, itemId),
  ]);

  const detailState = classify(detail, { isEmpty: () => false, subject: "this review item" });
  if (detailState.kind !== "ready") {
    return (
      <div className="shell-body">
        <h1>Review item</h1>
        <StateMessage state={detailState} />
      </div>
    );
  }
  const { item, decisions, drift } = detailState.data;
  const contextState = classify(context, { isEmpty: () => false, subject: "the evidence" });
  const canDecide = viewer.principal.capabilities.includes("governance:review");

  return (
    <div className="shell-body">
      <p className="breadcrumb">
        <Link href="/governance">Access reviews</Link>
        {" / "}
        <Link href={`/governance/campaigns/${item.campaign_id}`}>this campaign</Link>
      </p>
      <h1>
        {principalLabel(item)} on {targetLabel(item)}
      </h1>
      <p className="muted">
        <span className="principal-sid">{item.principal_sid}</span> ·{" "}
        {item.target_kind === "share" ? "share permissions" : "file-system permissions"} ·{" "}
        evidence digest <span className="principal-sid">{item.evidence_digest.slice(0, 16)}…</span>
      </p>

      <section className="card" aria-labelledby="evidence">
        <h2 id="evidence">What you are certifying</h2>
        <GrantEvidenceTable
          grants={item.grants}
          caption={
            "The entries exactly as they stood at the campaign's baseline. Copied at generation " +
            "and never refreshed, so the record of what was reviewed cannot change underneath a " +
            "recorded decision."
          }
        />
        {item.certainty !== "observed" ? (
          <p className="verdict verdict-info">
            This evidence is {item.certainty} rather than observed. Certifying reconstructed
            state as though it had been watched is the one way a review can make an audit
            worse rather than better — weigh the answer accordingly.
          </p>
        ) : null}
      </section>

      <DriftNotice drift={drift} />

      {contextState.kind === "ready" ? (
        <>
          <AccessComparison context={contextState.data} />
          <ReachPanel context={contextState.data} />
          <FindingsPanel
            findings={contextState.data.findings}
            truncated={contextState.data.findings_truncated}
          />
          <LastChangePanel context={contextState.data} />
        </>
      ) : (
        <StateMessage state={contextState} />
      )}

      <DecisionForm
        itemId={item.item_id}
        commentRequirement={
          contextState.kind === "ready" ? contextState.data.comment_requirement : "standard"
        }
        readOnly={readOnlyReason(canDecide, item.assignment_id)}
      />

      <section className="card" aria-labelledby="history">
        <h2 id="history">Decisions on this item</h2>
        <p className="muted">
          Append-only. A changed mind writes a new decision and supersedes the old one, which
          stays: &ldquo;certified on the 3rd, revoked on the 9th&rdquo; and &ldquo;revoked on
          the 9th&rdquo; are different histories, and only the first lets an auditor ask what
          changed in between.
        </p>
        <DecisionHistory decisions={decisions} />
      </section>
    </div>
  );
}

/**
 * Why the decision form is not offered, or null when it is.
 *
 * This is a courtesy and not a control — the API refuses on both counts regardless, and the
 * assignment is checked against the database on every decision. The wording matters because
 * "the button is missing" sends somebody to the wrong person: an unassigned item needs a
 * governance administrator, and a missing capability needs whoever grants roles.
 */
function readOnlyReason(
  canDecide: boolean,
  assignmentId: string | null,
): { reason: string } | null {
  if (!canDecide) {
    return {
      reason:
        "You hold governance:read but not governance:review, so you can see this review and " +
        "not answer it. Whoever runs campaigns deliberately cannot answer them either.",
    };
  }
  if (assignmentId === null) {
    return {
      reason:
        "This item is assigned to nobody, so nobody can decide it. A governance administrator " +
        "has to assign a reviewer to its scope first.",
    };
  }
  // "Assigned to somebody else" is deliberately not detected here. The item carries an
  // assignment id rather than a reviewer subject, so this application would have to guess at
  // the mapping — and a confident wrong "this is not yours" is worse than the API's own 403,
  // which names which of the three refusals applies.
  return null;
}
