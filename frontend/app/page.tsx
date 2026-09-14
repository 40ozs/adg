import Link from "next/link";

import { fetchCollectionStatus } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { CollectionSummary, RoleAdviceBanner, StateMessage } from "@/components/Banners";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { visibleNavItems } from "@/lib/nav";
import { classify } from "@/lib/state";

/**
 * Overview.
 *
 * The first thing on the page is the collection verdict, not a count. A dashboard that
 * opens with "412 shares" invites the reader to believe 412 is the number of shares; ADG
 * opens by saying how much of the estate has actually been looked at.
 */
export default async function OverviewPage() {
  const viewer = await currentViewer();
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const result = await fetchCollectionStatus(viewer.session.accessToken);
  const state = classify(result, { isEmpty: () => false, subject: "collectors" });
  const sections = visibleNavItems(viewer.principal.capabilities).filter(
    (item) => item.id !== "overview",
  );

  return (
    <>
      <h1>Overview</h1>

      <RoleAdviceBanner principal={viewer.principal} />

      {state.kind === "ready" ? (
        <CollectionSummary status={state.data} />
      ) : (
        <StateMessage state={state} />
      )}

      <div className="card">
        <h2>Where to start</h2>
        <p className="muted">
          Sections this account can open. Anything missing here needs a role an
          administrator assigns.
        </p>
        <dl className="facts">
          {sections.map((item) => (
            <div key={item.id} style={{ display: "contents" }}>
              <dt>
                <Link href={item.href}>{item.label}</Link>
              </dt>
              <dd>{item.description}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="card">
        <h2>What ADG will not do</h2>
        <p>
          ADG is read-only. It never writes an ACL, never changes a group, and never deletes
          data on a target system. Remediation is a reserved capability that no role grants.
        </p>
      </div>
    </>
  );
}
