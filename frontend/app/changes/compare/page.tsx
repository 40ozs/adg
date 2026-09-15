import type { JSX } from "react";
import Link from "next/link";

import { compareInstants } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { hrefWith } from "@/lib/paging";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { ChangeSummaryStrip, ChangeTimeline } from "@/components/Changes";
import { SignedOutNotice } from "@/components/SignedOutNotice";

const PATH = "/changes/compare";

/**
 * What is different between two points in time — a different question from the feed.
 *
 * An entry added on Wednesday and removed on Thursday appears in a Tuesday-to-Friday feed
 * twice and here not at all. Both answers are right, and offering only one of them would
 * quietly answer the other question badly:
 *
 * - the **feed** answers *what happened*, which is what an incident review needs;
 * - a **comparison** answers *what is different*, which is what a change-control review
 *   needs — "is the estate back the way it was before the maintenance window?"
 *
 * The number this page exists to show honestly is neither of the two lists. It is
 * ``unobserved_at_from`` and ``unobserved_at_to``: objects one of the two instants has no
 * version for. They are counted and named as gaps in observation, never folded into the
 * additions and removals, because rendering "we were not watching" as "this was deleted"
 * would revoke access on the strength of nobody having looked.
 */
export default async function ComparePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<JSX.Element> {
  const [viewer, params] = await Promise.all([currentViewer(), searchParams]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const from = single(params.from)?.trim() ?? "";
  const to = single(params.to)?.trim() ?? "";
  const share = single(params.share)?.trim();
  const directory = single(params.directory)?.trim();

  if (from === "" || to === "") {
    return <Prompt />;
  }

  const result = await compareInstants(viewer.session.accessToken, {
    from,
    to,
    share,
    directory,
    significance: ["security", "undetermined"],
  });
  const state = classify(result, {
    isEmpty: (data) => data.changes.length === 0,
    subject: "differences",
  });

  return (
    <>
      <h1>Compare two points in time</h1>
      <p className="muted">
        {from} versus {to}
      </p>
      <p>
        <Link href="/changes">Back to changes</Link>
      </p>

      {state.kind !== "ready" ? (
        <StateMessage state={state} />
      ) : (
        <>
          <section className="card">
            <h2>What this comparison could and could not compare</h2>
            <p>
              <strong>{state.data.unchanged}</strong> objects were present and identical at
              both instants.
            </p>
            {(state.data.unobserved_at_from > 0 || state.data.unobserved_at_to > 0) && (
              <div className="banner banner-info" role="status">
                <h3>Not every object could be compared</h3>
                <p>
                  {state.data.unobserved_at_from} objects have no version at all at the
                  earlier instant and {state.data.unobserved_at_to} have none at the later
                  one. They are counted here and reported in neither list: a gap in
                  observation is not a change, and rendering one as a creation or a deletion
                  would state something nothing supports.
                </p>
              </div>
            )}
            {state.data.truncated && (
              <div className="banner banner-warning" role="status">
                <p>
                  More objects lie inside this comparison than the server reads at once, so
                  these lists are incomplete. Name a share or a directory to narrow it.
                </p>
              </div>
            )}
          </section>

          <ChangeSummaryStrip summary={state.data.summary} />

          {state.data.changes.length === 0 ? (
            <div className="card">
              <h2>Nothing is different</h2>
              <p>
                Every object covered at both instants holds the same state. Anything that
                changed and changed back inside the interval is, correctly, not here — the{" "}
                <Link href="/changes">change list</Link> is what reports that.
              </p>
            </div>
          ) : (
            <ChangeTimeline
              changes={state.data.changes}
              edits={state.data.edits}
              impactHref={(change) =>
                hrefWith("/changes/impact", {
                  kind: change.kind,
                  key: change.key,
                  at: change.at,
                })
              }
              timelineHref={(change) =>
                hrefWith("/changes", { kind: change.kind, key: change.key })
              }
            />
          )}
        </>
      )}
    </>
  );
}

function Prompt(): JSX.Element {
  const now = new Date();
  const week = new Date(now.getTime() - 7 * 86_400_000);
  return (
    <>
      <h1>Compare two points in time</h1>
      <div className="card">
        <h2>Pick two instants</h2>
        <p>
          A comparison answers <em>what is different</em>, which is not the same question as{" "}
          <em>what happened</em>. An entry added and removed inside the interval appears in
          the <Link href="/changes">change list</Link> twice and in a comparison not at all.
        </p>
        <p>
          <Link href={hrefWith(PATH, { from: week.toISOString(), to: now.toISOString() })}>
            Compare now against a week ago
          </Link>
        </p>
        <p className="muted">
          Both instants must carry a UTC offset. An estate-wide comparison is limited to 400
          days apart; name a share or directory to compare any two instants.
        </p>
      </div>
    </>
  );
}

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
