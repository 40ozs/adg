import Link from "next/link";

import { currentViewer } from "@/lib/auth/current";
import { SignedOutNotice } from "@/components/SignedOutNotice";

/**
 * Changes.
 *
 * Placeholder, and explicit about why the data cannot simply be inferred from what is
 * already stored: ADG never marks anything absent. A collector that did not mention a share
 * produced fewer observations, not evidence of removal, and treating the difference between
 * two scans as a change set would report access as revoked while it is still in force.
 */
export default async function ChangesPage() {
  const viewer = await currentViewer();
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  return (
    <>
      <h1>Changes</h1>
      <div className="banner banner-info" role="status">
        <h2>Not yet implemented</h2>
        <p>
          Change detection arrives in a later phase. It is not simply a diff of two scans:
          absence may only be inferred inside a scope a run actually reconciled, and a
          partial run reconciles nothing.
        </p>
      </div>
      <div className="card">
        <h2>What is already recorded</h2>
        <p>
          Every observation carries the run that produced it and the window it was seen in,
          and every run records the scopes it declared and reconciled. That evidence is what
          this page will read.{" "}
          <Link href="/collectors">See the runs.</Link>
        </p>
      </div>
    </>
  );
}
