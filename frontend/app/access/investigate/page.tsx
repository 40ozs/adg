import type { JSX } from "react";

import { fetchCollectionCoverage, fetchCollectionStatus, fetchRuntime } from "@/lib/api/adg";
import { fetchAccessExplanation } from "@/lib/api/explain";
import { currentViewer } from "@/lib/auth/current";
import { classify, coverageCaveat } from "@/lib/state";
import { CoverageCaveat, StateMessage } from "@/components/Banners";
import { AnswerCoveragePanel } from "@/components/Coverage";
import { CautionPanel } from "@/components/Explanation";
import { ExplainLauncher } from "@/components/ExplainLauncher";
import {
  InvestigationQuestion,
  InvestigationUnavailable,
  NextStepsPanel,
  ObservedAccessPanel,
  OpenQuestionsPanel,
  RoutesPanel,
  SummaryPanel,
} from "@/components/Investigation";
import { SignedOutNotice } from "@/components/SignedOutNotice";

/**
 * Why can this principal access this resource — as a guided investigation (ADG-DEMO-02).
 *
 * The same single request the explanation page makes, `GET /api/v1/access/explain`, read
 * in the order an investigator works: the question, what ADG evaluated, the route that
 * carries the access and where each hop of it was observed, what could not be confirmed
 * and whether that changes the answer, a summary, and what to do next. The engine computed
 * all of it, the investigation summary included; nothing on this page intersects a mask or
 * decides which path matters. The explanation page remains the full derivation and is one
 * link away.
 */
export default async function AccessInvestigationPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<JSX.Element> {
  const [viewer, params, runtime] = await Promise.all([currentViewer(), searchParams, fetchRuntime()]);
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
        <h1>Access investigation</h1>
        <p className="muted">
          An investigation is always about one principal and one directory: why does this
          principal have access to this resource? Name both.
        </p>
        <div className="card">
          <ExplainLauncher
            principal={principal}
            resource={resource}
            host={host}
            accessPath={accessPath}
            mode="investigate"
          />
        </div>
      </>
    );
  }

  const token = viewer.session.accessToken;
  const [coverage, result, answerCoverage] = await Promise.all([
    fetchCollectionStatus(token),
    fetchAccessExplanation(token, {
      principal,
      resource,
      host: host === "" ? undefined : host,
      access_path: accessPath,
    }),
    // ADG-DEMO-03: the scopes this answer reads, and whether an incomplete one could change
    // it. Context beside the answer; a failure here leaves the investigation as it was.
    fetchCollectionCoverage(token, { resource }),
  ]);
  const coverageData = coverage.ok ? coverage.data : null;
  const state = classify(result, {
    isEmpty: () => false,
    coverage: coverageData,
    subject: "access investigation",
  });

  if (state.kind !== "ready") {
    return (
      <>
        <h1>Access investigation</h1>
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
            mode="investigate"
          />
        </div>
      </>
    );
  }

  const explanation = state.data;
  const investigation = explanation.investigation ?? null;
  const synthetic = runtime.ok && runtime.data.data_source === "synthetic";

  return (
    <>
      <InvestigationQuestion explanation={explanation} synthetic={synthetic} />
      <CoverageCaveat caveat={coverageCaveat(coverageData)} />

      {investigation === null ? (
        <InvestigationUnavailable explanation={explanation} />
      ) : (
        <>
          <ObservedAccessPanel explanation={explanation} investigation={investigation} />
          <RoutesPanel explanation={explanation} investigation={investigation} />
          <OpenQuestionsPanel investigation={investigation} />
          <SummaryPanel explanation={explanation} investigation={investigation} />
          <NextStepsPanel explanation={explanation} investigation={investigation} />
        </>
      )}

      {answerCoverage.ok && <AnswerCoveragePanel coverage={answerCoverage.data} />}

      <details className="card">
        <summary>Every caveat on this answer, as the full explanation lists them</summary>
        <CautionPanel explanation={explanation} />
      </details>

      <details className="card">
        <summary>Investigate a different pair</summary>
        <ExplainLauncher
          principal={principal}
          resource={resource}
          host={host}
          accessPath={accessPath}
          mode="investigate"
        />
      </details>
    </>
  );
}

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
