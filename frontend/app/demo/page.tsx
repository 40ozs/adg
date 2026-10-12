import type { JSX } from "react";

import { fetchDemoOverview, fetchRuntime } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { DemoGuide } from "@/components/DemoGuide";
import { SignedOutNotice } from "@/components/SignedOutNotice";

export const dynamic = "force-dynamic";

/**
 * The demo guide: the synthetic dataset's scenarios, each linked into the real pages.
 *
 * Only a demo deployment has one. The runtime mode is read from `GET /runtime` — deployment
 * configuration — and a production deployment answers this page with a plain statement, not
 * an empty guide, because an empty guide on a production deployment would read as "the demo
 * is broken" rather than "this is not a demo".
 */
export default async function DemoPage(): Promise<JSX.Element> {
  const [viewer, runtime] = await Promise.all([currentViewer(), fetchRuntime()]);
  if (runtime.ok && runtime.data.runtime_mode !== "demo") {
    return (
      <>
        <h1>Demo guide</h1>
        <p>This deployment is not a demo environment: its data comes from its collectors.</p>
      </>
    );
  }
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const overview = await fetchDemoOverview(viewer.session.accessToken);
  if (!overview.ok) {
    return (
      <>
        <h1>Demo guide</h1>
        <StateMessage
          state={classify(overview, { isEmpty: () => false, coverage: null, subject: "demo guide" })}
        />
      </>
    );
  }
  return <DemoGuide overview={overview.data} />;
}
