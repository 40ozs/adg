import Link from "next/link";

import { currentViewer } from "@/lib/auth/current";
import { SignedOutNotice } from "@/components/SignedOutNotice";

/**
 * Risks.
 *
 * A placeholder that says so. The alternative -- an empty table headed "Risks" -- would read
 * as "no risks found", which is the single most dangerous sentence this product could
 * accidentally say.
 */
export default async function RisksPage() {
  const viewer = await currentViewer();
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  return (
    <>
      <h1>Risks</h1>
      <div className="banner banner-info" role="status">
        <h2>Not yet implemented</h2>
        <p>
          Risk rules arrive in a later phase. This page shows nothing because nothing is
          computed yet -- <strong>not</strong> because no risks exist in the estate.
        </p>
      </div>
      <div className="card">
        <h2>Meanwhile</h2>
        <p>
          The facts the rules will run over are already collected and queryable.{" "}
          <Link href="/resources">Resources</Link> shows what has been observed, and{" "}
          <Link href="/collectors">Collectors</Link> shows how much of the estate that
          covers.
        </p>
      </div>
    </>
  );
}
