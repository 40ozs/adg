import { fetchCollectionStatus, fetchScanRuns } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { CollectionSummary, StateMessage } from "@/components/Banners";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { classify } from "@/lib/state";

/**
 * Collectors.
 *
 * The page every empty table elsewhere points at. It answers one question — did anything
 * actually look at the estate, and what could it not read — so the run list shows the
 * numbers that make a run's coverage judgeable rather than only its outcome.
 */
export default async function CollectorsPage() {
  const viewer = await currentViewer();
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const token = viewer.session.accessToken;
  const [coverage, runs] = await Promise.all([
    fetchCollectionStatus(token),
    fetchScanRuns(token, { limit: 25 }),
  ]);

  const runState = classify(runs, {
    isEmpty: (data) => data.items.length === 0,
    coverage: coverage.ok ? coverage.data : null,
    subject: "scan runs",
  });

  return (
    <>
      <h1>Collectors</h1>

      {coverage.ok ? (
        <CollectionSummary status={coverage.data} />
      ) : (
        <StateMessage state={classify(coverage, { isEmpty: () => false, subject: "coverage" })} />
      )}

      {coverage.ok && coverage.data.collectors.length > 0 && (
        <div className="card">
          <h2>Latest run per collector</h2>
          <div className="table-scroll">
            <table>
              <caption>
                One row per collector kind. A collector absent from this table has never
                reported.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Collector</th>
                  <th scope="col">Status</th>
                  <th scope="col">Started</th>
                  <th scope="col">Errors</th>
                  <th scope="col">Coverage</th>
                </tr>
              </thead>
              <tbody>
                {coverage.data.collectors.map((item) => (
                  <tr key={item.collector}>
                    <th scope="row">{item.collector}</th>
                    <td className={statusClass(item.status)}>{item.status}</td>
                    <td>{formatTime(item.started_at)}</td>
                    <td>{item.error_count}</td>
                    <td className={item.trustworthy ? "status-ok" : "status-warn"}>
                      {item.trustworthy ? "complete" : (item.concern ?? "incomplete")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <h2>Run history</h2>
      <StateMessage state={runState} />

      {runState.kind === "ready" && (
        <div className="table-scroll">
          <table>
            <caption>
              {runState.data.page.total ?? runState.data.items.length} run(s), newest first.
              {runState.data.page.has_more && " More pages are available through the API."}
            </caption>
            <thead>
              <tr>
                <th scope="col">Started</th>
                <th scope="col">Collector</th>
                <th scope="col">Host</th>
                <th scope="col">Status</th>
                <th scope="col">Observations</th>
                <th scope="col">Errors</th>
                <th scope="col">Target</th>
              </tr>
            </thead>
            <tbody>
              {runState.data.items.map((run) => (
                <tr key={run.run_id}>
                  <td>{formatTime(run.started_at)}</td>
                  <td>{run.collector}</td>
                  <td>{run.collector_host}</td>
                  <td className={statusClass(run.status)}>
                    {run.status}
                    {run.downgrade_reason && (
                      <>
                        {" "}
                        <span className="muted">({run.downgrade_reason})</span>
                      </>
                    )}
                  </td>
                  <td>{run.observation_count_applied}</td>
                  <td>{run.error_count}</td>
                  <td className="muted">{run.target ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function statusClass(status: string): string {
  if (status === "succeeded") {
    return "status-ok";
  }
  if (status === "failed") {
    return "status-bad";
  }
  return "status-warn";
}

/**
 * Timestamps are rendered in UTC, always.
 *
 * A scan run's time is compared against a Windows event log and a collector log, both of
 * which an operator reads in a fixed zone. Rendering in the browser's zone would also make
 * the server and client markup disagree and produce a hydration mismatch.
 */
function formatTime(value: string | null): string {
  if (value === null) {
    return "—";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : `${parsed.toISOString().replace("T", " ").slice(0, 19)} UTC`;
}
