import type { JSX } from "react";
import Link from "next/link";

import { fetchSimulation, fetchSimulationExport } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { readStoredReport } from "@/lib/simulation";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { BaselinePanel, EvaluationHistory, SimulationNotice } from "@/components/Simulation";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { StoredResultPanel, SimulationRerun } from "@/components/SimulationDetail";

/**
 * One stored proposal: what it says, what it was measured against, and every time it ran.
 *
 * ## What a stored result can and cannot show
 *
 * The result persisted with a proposal is the engine's own compact document — masks as hex,
 * caveats as codes, principals as storage keys. That is on purpose (see
 * `readStoredReport`): the derivation is reproducible exactly, and a stored copy of the
 * rendered version would be a second account of one answer, ageing independently of the code
 * that computes it.
 *
 * So this page shows the stored result as what it is, says on screen that the names were
 * never part of the answer, and offers **Run this again** — which produces the rendered
 * report, resolved and explained, against the estate as it stands now. That button is also
 * the answer to a stale baseline, which is why staleness here is a banner and not a refusal.
 */
export default async function SimulationDetailPage({
  params,
}: {
  params: Promise<{ simulationId: string }>;
}): Promise<JSX.Element> {
  const [viewer, { simulationId }] = await Promise.all([currentViewer(), params]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const token = viewer.session.accessToken;
  const [detail, exported] = await Promise.all([
    fetchSimulation(token, simulationId),
    fetchSimulationExport(token, simulationId),
  ]);
  const state = classify(detail, { isEmpty: () => false, subject: "this proposal" });

  if (state.kind !== "ready") {
    return (
      <>
        <h1>What-if proposal</h1>
        <StateMessage state={state} />
        <p>
          <Link href="/simulations">Back to proposals</Link>
        </p>
      </>
    );
  }

  const { simulation, evaluations, stale } = state.data;
  const latest = evaluations[0];
  const stored = latest ? readStoredReport(latest.report) : null;

  return (
    <>
      <h1>{simulation.name}</h1>
      {simulation.description && <p className="muted">{simulation.description}</p>}

      <SimulationNotice notice={state.data.notice} />

      <div className="card">
        <h2>What it proposes</h2>
        <ul>
          {simulation.changes.map((change, index) => (
            <li key={`${change.kind}-${index}`}>
              <strong>{change.description}</strong>
              <p className="muted">{change.kind_description}</p>
            </li>
          ))}
        </ul>
        <p className="muted">
          Proposal digest <code>{simulation.overlay_hash}</code>. Written{" "}
          {simulation.created_at}
          {simulation.created_by ? ` by ${simulation.created_by}` : ""}.
        </p>
      </div>

      <BaselinePanel baseline={simulation.baseline} stale={stale} />

      {stored ? (
        <StoredResultPanel report={stored} computedAt={latest.computed_at} />
      ) : (
        <div className="card">
          <h2>No result</h2>
          <p className="muted">
            This proposal has never been evaluated. That is not the same as a proposal that
            was found to change nothing.
          </p>
        </div>
      )}

      <SimulationRerun simulationId={simulationId} name={simulation.name} stale={stale} />

      <EvaluationHistory evaluations={evaluations} />

      {exported.ok && (
        <details className="card">
          <summary>The structured export of this plan and its result</summary>
          <p className="muted">
            Document version {exported.data.document_version}, self-describing: the vocabulary
            travels with it, so a reader six months from now does not need this screen to know
            what <code>loss_may_not_hold</code> meant.
          </p>
          <label htmlFor="simulation-plan-export" className="visually-hidden">
            The simulation plan and result as JSON
          </label>
          <textarea
            id="simulation-plan-export"
            readOnly
            rows={20}
            style={{ width: "100%", fontFamily: "monospace", fontSize: "0.8rem" }}
            value={JSON.stringify(exported.data, null, 2)}
            spellCheck={false}
          />
        </details>
      )}

      <p>
        <Link href="/simulations">Back to proposals</Link>
      </p>
    </>
  );
}
