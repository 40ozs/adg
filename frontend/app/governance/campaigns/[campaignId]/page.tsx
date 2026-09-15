import type { JSX } from "react";
import Link from "next/link";

import { fetchCampaignDrift, fetchCampaignItems, fetchCampaignStatus } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { SelectableItems } from "@/components/DecisionForm";
import {
  CampaignProgress,
  DriftNotice,
  DriftSummary,
  ItemHeading,
  ReviewerProgressTable,
} from "@/components/Governance";
import { SignedOutNotice } from "@/components/SignedOutNotice";

/**
 * One campaign: how far it has got, who is behind, what has moved, and its items.
 *
 * The page is ordered by what stops a review being meaningful, not by what is easiest to
 * render. Coverage first — a campaign that excluded inherited entries reviewed less than the
 * estate holds, and "47 of 47" must never be read as more than it is. Then drift, because a
 * reviewer about to certify a grant that no longer exists should not have to open the item
 * to find out. Then the reviewers, because a campaign nobody is answering looks identical to
 * a slow one until you see the rows. The items come last: they are the work, and they are
 * the part somebody can start on once they know what they are looking at.
 *
 * `?mine=1` narrows the item list to this caller's own assignment, which is what a reviewer
 * arriving from their queue wants. `?view=drift` swaps the item table for the items that
 * moved.
 */
export default async function CampaignPage({
  params,
  searchParams,
}: {
  params: Promise<{ campaignId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<JSX.Element> {
  const [viewer, { campaignId }, query] = await Promise.all([
    currentViewer(),
    params,
    searchParams,
  ]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }
  const token = viewer.session.accessToken;
  const mine = single(query.mine) === "1";
  const driftView = single(query.view) === "drift";
  // The two centred reviews the phase names. A campaign's focus says which end it enumerated
  // and therefore what it claims to be complete about; these narrow the *reading* of it to
  // one resource or one principal, which is how a resource owner and an identity owner each
  // approach the same campaign.
  const target = single(query.target);
  const principal = single(query.principal);
  const itemStatus = single(query.status);

  const [status, items, drift] = await Promise.all([
    fetchCampaignStatus(token, campaignId),
    fetchCampaignItems(token, campaignId, {
      limit: 100,
      mine: mine ? true : undefined,
      target_key: target,
      principal_key: principal,
      status: itemStatus,
    }),
    fetchCampaignDrift(token, campaignId, { limit: 100 }),
  ]);

  const statusState = classify(status, { isEmpty: () => false, subject: "this campaign" });
  if (statusState.kind !== "ready") {
    return (
      <div className="shell-body">
        <h1>Campaign</h1>
        <StateMessage state={statusState} />
      </div>
    );
  }
  const report = statusState.data;
  const canDecide = viewer.principal.capabilities.includes("governance:review");

  const itemsState = classify(items, {
    isEmpty: (data) => data.items.length === 0,
    subject: mine ? "items assigned to you in this campaign" : "items in this campaign",
  });
  const driftState = classify(drift, { isEmpty: () => false, subject: "drift" });

  return (
    <div className="shell-body">
      <p className="breadcrumb">
        <Link href="/governance">Access reviews</Link>
      </p>
      <h1>{report.campaign.name}</h1>
      <p className="muted">
        {report.campaign.status} · frozen against{" "}
        {new Date(report.campaign.baseline_at).toISOString().slice(0, 16).replace("T", " ")} ·{" "}
        {report.campaign.focus === "principal" ? "enumerated by principal" : "enumerated by resource"}
      </p>
      {report.campaign.description === null ? null : <p>{report.campaign.description}</p>}

      <CampaignProgress status={report} />

      {driftState.kind === "ready" ? (
        <DriftSummary
          counts={driftState.data.counts}
          covered={driftState.data.covered}
          total={driftState.data.total_items}
          campaignId={campaignId}
        />
      ) : (
        <StateMessage state={driftState} />
      )}

      <section aria-labelledby="reviewers">
        <h2 id="reviewers">Reviewers</h2>
        <ReviewerProgressTable reviewers={report.reviewers} />
      </section>

      {driftView && driftState.kind === "ready" ? (
        <section aria-labelledby="drifted">
          <h2 id="drifted">Items that have moved</h2>
          <p className="muted">
            <Link href={`/governance/campaigns/${campaignId}`}>Back to all items</Link>
          </p>
          {driftState.data.drifted.length === 0 ? (
            <p className="muted">
              Nothing in the {driftState.data.covered.toLocaleString()} items compared has
              changed since this campaign was frozen.
            </p>
          ) : (
            <ul className="acl-group">
              {driftState.data.drifted.map((entry) => (
                <li key={entry.item.item_id}>
                  <h3>
                    <Link href={`/governance/items/${entry.item.item_id}`}>
                      <ItemHeading item={entry.item} />
                    </Link>
                  </h3>
                  <DriftNotice drift={entry.drift} />
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : (
        <section aria-labelledby="items">
          <h2 id="items">{itemsHeading({ mine, target, principal, itemStatus })}</h2>
          <p className="filter-strip">
            {mine ? (
              <Link href={`/governance/campaigns/${campaignId}`}>Show every item</Link>
            ) : (
              <Link href={`/governance/campaigns/${campaignId}?mine=1`}>
                Show only items assigned to me
              </Link>
            )}
            {" · "}
            <Link href={`/governance/campaigns/${campaignId}?status=pending`}>Pending only</Link>
            {" · "}
            <Link href={`/governance/campaigns/${campaignId}?status=decided`}>Decided only</Link>
            {target !== undefined || principal !== undefined || itemStatus !== undefined ? (
              <>
                {" · "}
                <Link href={`/governance/campaigns/${campaignId}`}>Clear filters</Link>
              </>
            ) : null}
          </p>
          <p className="muted">
            Reviewing one resource or one principal at a time: follow a target or a principal
            from the table below. A campaign&rsquo;s focus decides what it claims to be
            complete about; these filters narrow only what you are reading.
          </p>
          {itemsState.kind === "ready" ? (
            <SelectableItems
              campaignId={campaignId}
              items={itemsState.data.items}
              commentRequirement={report.campaign.comment_requirement}
              canDecide={canDecide && report.campaign.status === "active"}
            />
          ) : itemsState.kind === "empty" && mine ? (
            <p className="muted">
              None of this campaign&rsquo;s items are assigned to you. Holding the reviewer
              role admits you to the page; the assignment is what makes an item yours to
              answer.
            </p>
          ) : (
            <StateMessage state={itemsState} />
          )}
        </section>
      )}
    </div>
  );
}

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

/**
 * What the item list is currently showing, as a heading.
 *
 * Written out rather than left as "Items" because a filtered list that looks unfiltered is
 * this product's own failure mode applied to its own interface: a reviewer who sees three
 * rows and does not know a filter is on concludes the campaign has three items.
 */
function itemsHeading(filters: {
  mine: boolean;
  target: string | undefined;
  principal: string | undefined;
  itemStatus: string | undefined;
}): string {
  const parts: string[] = [];
  if (filters.itemStatus === "pending") {
    parts.push("Pending");
  } else if (filters.itemStatus === "decided") {
    parts.push("Decided");
  }
  parts.push(filters.mine ? "items assigned to you" : "items");
  if (filters.target !== undefined) {
    parts.push(`on ${filters.target}`);
  }
  if (filters.principal !== undefined) {
    parts.push(`for ${filters.principal}`);
  }
  const rendered = parts.join(" ");
  return rendered.charAt(0).toUpperCase() + rendered.slice(1);
}
