import type { JSX } from "react";
import Link from "next/link";

import { fetchCampaigns, fetchReviewerQueue } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { CampaignTable, ReviewerQueue } from "@/components/Governance";
import { SignedOutNotice } from "@/components/SignedOutNotice";

/**
 * Access reviews: what was asked of you, and what is being asked across the estate.
 *
 * The queue is first and the campaign list second, because the two answer different people's
 * questions and only one of them is a task. A reviewer opening this page wants "what is
 * waiting for me"; a governance administrator wants "what is running". Leading with the
 * campaign list would make every reviewer scan a table to find their own row.
 *
 * The queue resolves through **active assignments**, so it is what was asked of this person
 * rather than what they are allowed to read — an auditor with `governance:read` sees an empty
 * queue and the full campaign list, which is the right answer for both.
 */
export default async function GovernancePage(): Promise<JSX.Element> {
  const viewer = await currentViewer();
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }
  const token = viewer.session.accessToken;
  const [queue, campaigns] = await Promise.all([
    fetchReviewerQueue(token),
    fetchCampaigns(token, { limit: 50 }),
  ]);

  const queueState = classify(queue, {
    isEmpty: (data) => data.entries.length === 0,
    subject: "review work assigned to you",
  });
  const campaignState = classify(campaigns, {
    isEmpty: (data) => data.items.length === 0,
    subject: "review campaigns",
  });

  return (
    <div className="shell-body">
      <h1>Access reviews</h1>
      <p className="muted">
        A campaign is frozen against an instant. Reviewers certify what was true then, and
        ADG records the conclusion beside the observation without ever changing the
        observation: a revoke decision produces a proposal, and ADG performs none of them.
      </p>

      <section aria-labelledby="queue">
        <h2 id="queue">Your queue</h2>
        {queueState.kind === "ready" ? (
          <>
            <p>
              {queueState.data.total_pending.toLocaleString()} items to answer across{" "}
              {queueState.data.entries.length.toLocaleString()} campaigns
              {queueState.data.overdue_campaigns > 0 ? (
                <span className="status-bad">
                  {" "}
                  — {queueState.data.overdue_campaigns.toLocaleString()} overdue
                </span>
              ) : null}
              .
            </p>
            <ReviewerQueue entries={queueState.data.entries} />
          </>
        ) : queueState.kind === "empty" ? (
          <p className="muted">
            Nothing has been assigned to you. A queue is what was asked <em>of you</em>, so
            this stays empty until a governance administrator assigns you part of a campaign
            — holding a role is not the same as being asked.
          </p>
        ) : (
          <StateMessage state={queueState} />
        )}
      </section>

      <section aria-labelledby="campaigns">
        <h2 id="campaigns">Campaigns</h2>
        {campaignState.kind === "ready" ? (
          <CampaignTable campaigns={campaignState.data.items} />
        ) : campaignState.kind === "empty" ? (
          <p className="muted">
            No campaign has been created. A campaign is created and scoped by somebody
            holding <code>governance:manage</code>, then frozen against a past instant before
            anybody can answer it.
          </p>
        ) : (
          <StateMessage state={campaignState} />
        )}
      </section>

      <p className="muted">
        <Link href="/changes">What changed in the estate</Link> is the other half of this
        question: a review says what should be true, and the change feed says what moved.
      </p>
    </div>
  );
}
