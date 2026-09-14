import type { JSX } from "react";
import Link from "next/link";

import type { Viewer } from "@/lib/auth/current";

/**
 * What a page shows when there is nobody to show it to.
 *
 * "Signed out" and "the API is unavailable" are deliberately different screens. Sending
 * somebody to a login page because the database is unreachable puts them in a loop that
 * signing in cannot break, and they will try it several times before concluding the product
 * is broken rather than the deployment.
 */
export function SignedOutNotice({ viewer }: { viewer: Viewer }): JSX.Element {
  if (viewer.status === "unavailable") {
    return (
      <div className="banner banner-error" role="alert">
        <h2>ADG cannot reach its API</h2>
        <p>{viewer.failure.message}</p>
        {viewer.failure.detail && <p className="muted">{viewer.failure.detail}</p>}
        <p className="muted">
          This is not a sign-in problem. Check the API and its database, then reload.{" "}
          <Link href="/status">System status</Link>
        </p>
      </div>
    );
  }

  const headline =
    viewer.status === "signed-out" && viewer.reason === "expired"
      ? "Your session has expired"
      : "Sign in to continue";

  return (
    <div className="banner banner-warning" role="alert">
      <h2>{headline}</h2>
      <p>
        ADG holds permission data for a whole Windows estate. Nothing is shown without an
        account.
      </p>
      {viewer.status === "signed-out" && viewer.detail && (
        <p className="muted">{viewer.detail}</p>
      )}
      <p>
        <Link className="button" href="/login">
          Sign in
        </Link>
      </p>
    </div>
  );
}
