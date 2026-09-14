import type { JSX } from "react";

import { fetchCollectionStatus } from "@/lib/api/adg";
import { fetchAccessExplanation } from "@/lib/api/explain";
import { currentViewer } from "@/lib/auth/current";
import { classify, coverageCaveat } from "@/lib/state";
import { CoverageCaveat, StateMessage } from "@/components/Banners";
import {
  CautionPanel,
  ExplanationHeader,
  LayerPanel,
  RemovalPanel,
  VerdictPanel,
} from "@/components/Explanation";
import { ExplainLauncher } from "@/components/ExplainLauncher";
import { ExplanationExplorer } from "@/components/ExplanationExplorer";
import { ExplanationExport } from "@/components/ExplanationExport";
import { SignedOutNotice } from "@/components/SignedOutNotice";

/**
 * Why one principal can, or cannot, reach one directory.
 *
 * Everything on this page came out of `GET /api/v1/access/explain` in a single request.
 * Nothing here intersects a mask, expands a group, compares an ACE position, or decides
 * what a Deny is worth: the engine did all of it, measured against the same Windows
 * `AuthzAccessCheck` semantics it is validated against, and a browser-side second opinion
 * could only ever disagree with the answer this product exists to give.
 *
 * The order on the page is the order an administrator needs it in. The answer first, with
 * the caveats immediately under it rather than at the bottom where they would be read
 * after a decision. Then the routes — the part that answers "why" — then each ACL, and
 * last the measured effect of removing each relationship, which is the part somebody is
 * about to act on.
 */
export default async function AccessExplanationPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<JSX.Element> {
  const [viewer, params] = await Promise.all([currentViewer(), searchParams]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const principal = single(params.principal)?.trim() ?? "";
  const resource = single(params.resource)?.trim() ?? "";
  const host = single(params.host)?.trim() ?? "";
  const accessPath = single(params.access_path)?.trim() ?? "remote_smb";

  if (principal === "" || resource === "") {
    return (
      <>
        <h1>Access explanation</h1>
        <p className="muted">
          An explanation is always about one principal and one directory. Name both.
        </p>
        <div className="card">
          <ExplainLauncher
            principal={principal}
            resource={resource}
            host={host}
            accessPath={accessPath}
          />
        </div>
      </>
    );
  }

  const token = viewer.session.accessToken;
  const [coverage, result] = await Promise.all([
    fetchCollectionStatus(token),
    fetchAccessExplanation(token, {
      principal,
      resource,
      host: host === "" ? undefined : host,
      access_path: accessPath,
    }),
  ]);
  const coverageData = coverage.ok ? coverage.data : null;

  // An explanation is never an empty list: it is an answer, and "no access" is one of the
  // answers rather than an absence of them. So `isEmpty` is always false, and the empty
  // states this classifier exists for are attributed on the answer itself, by the verdict.
  const state = classify(result, {
    isEmpty: () => false,
    coverage: coverageData,
    subject: "access explanation",
  });

  if (state.kind !== "ready") {
    return (
      <>
        <h1>Access explanation</h1>
        <p className="muted">
          <code>{principal}</code> against <code>{resource}</code>
        </p>
        <StateMessage state={state} />
        <div className="card">
          <h2>Ask about a different pair</h2>
          <ExplainLauncher
            principal={principal}
            resource={resource}
            host={host}
            accessPath={accessPath}
          />
        </div>
      </>
    );
  }

  const explanation = state.data;

  return (
    <>
      <ExplanationHeader explanation={explanation} />

      <CoverageCaveat caveat={coverageCaveat(coverageData)} />

      <VerdictPanel explanation={explanation} />

      <CautionPanel explanation={explanation} />

      <ExplanationExplorer explanation={explanation} />

      <LayerPanel title="The share ACL" explanation={explanation} layer="share" />
      <LayerPanel title="The NTFS ACL" explanation={explanation} layer="ntfs" />

      <RemovalPanel explanation={explanation} />

      <ExplanationExport explanation={explanation} />

      <details className="card">
        <summary>Ask about a different pair</summary>
        <ExplainLauncher
          principal={principal}
          resource={resource}
          host={host}
          accessPath={accessPath}
        />
      </details>
    </>
  );
}

/** A repeated query parameter is a caller error, not a list. Take the first. */
function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
