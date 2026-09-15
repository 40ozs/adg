import type { JSX } from "react";
import Link from "next/link";

import { fetchSimulations } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { SIMULATE_PATH } from "@/lib/simulation";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { SimulationNotice, StoredSimulationsTable } from "@/components/Simulation";
import { SignedOutNotice } from "@/components/SignedOutNotice";

/**
 * Proposals somebody has written down.
 *
 * The notice comes first here too, even on a listing, because this is the screen somebody
 * lands on from the navigation with no idea what a "simulation" in this product does. A page
 * of rows named after change tickets, with no statement of what they are, would read as a
 * queue of changes that have been or will be made.
 */
export default async function SimulationsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<JSX.Element> {
  const [viewer, params] = await Promise.all([currentViewer(), searchParams]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const cursor = single(params.cursor);
  const result = await fetchSimulations(viewer.session.accessToken, { cursor });
  const state = classify(result, {
    isEmpty: (data) => data.simulations.length === 0,
    subject: "what-if proposals",
  });

  return (
    <>
      <h1>What-if proposals</h1>
      <p className="muted">
        Ask what a permission change would do, before anybody makes it.
      </p>

      <SimulationNotice
        notice={
          state.kind === "ready"
            ? state.data.notice
            : "NO CHANGES WILL BE APPLIED. A simulation computes what a proposed permission change would do, by reading ADG's own collected facts through the proposal. Nothing is written to Active Directory, to a share, or to an NTFS descriptor."
        }
      />

      <p>
        <Link className="button" href={SIMULATE_PATH}>
          Write a new proposal
        </Link>
      </p>

      {state.kind !== "ready" ? (
        <StateMessage state={state} />
      ) : (
        <div className="card">
          <h2>Stored proposals</h2>
          <p className="muted">
            Newest first. A proposal marked as having moved on was measured against a state a
            collector has since changed; open it and run it again to answer against what is
            there now.
          </p>
          <StoredSimulationsTable simulations={state.data.simulations} />
          {state.data.page.has_more && state.data.page.next_cursor && (
            <p className="pager-links">
              <Link href={`/simulations?cursor=${encodeURIComponent(state.data.page.next_cursor)}`}>
                Older proposals
              </Link>
            </p>
          )}
        </div>
      )}
    </>
  );
}

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
