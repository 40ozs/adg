import { fetchBackendStatus } from "@/lib/api/health";
import { resolveApiUrl, resolveServerApiUrl } from "@/lib/config";

// Always render on request: a cached health page would be worse than no health page.
export const dynamic = "force-dynamic";

export default async function StatusPage() {
  // The browser and this server component can reach the API at different addresses; the
  // page reports both so a misconfiguration is visible rather than mysterious.
  const browserApiUrl = resolveApiUrl();
  const serverApiUrl = resolveServerApiUrl();
  const status = await fetchBackendStatus(serverApiUrl);

  return (
    <>
      <h1>System status</h1>
      <p className="muted">
        Browser API base URL: <code>{browserApiUrl}</code> (from{" "}
        <code>NEXT_PUBLIC_ADG_API_URL</code>)
      </p>
      {serverApiUrl !== browserApiUrl && (
        <p className="muted">
          Server-side API base URL: <code>{serverApiUrl}</code> (from{" "}
          <code>ADG_INTERNAL_API_URL</code>)
        </p>
      )}

      {!status.reachable ? (
        <div className="card">
          <p className="status-bad">Backend unreachable</p>
          <p className="muted">{status.error}</p>
          <p className="muted">
            Start the development stack with <code>.\scripts\stack-up.ps1</code>.
          </p>
        </div>
      ) : (
        <>
          <div className="card">
            <h2>API</h2>
            <p>
              <span className="status-ok">Responding</span> — {status.version.name}{" "}
              {status.version.version} ({status.version.environment})
            </p>
            <p>
              Readiness:{" "}
              <span className={status.readiness.status === "ready" ? "status-ok" : "status-bad"}>
                {status.readiness.status}
              </span>
            </p>
          </div>

          <div className="card">
            <h2>Dependencies</h2>
            <table>
              <thead>
                <tr>
                  <th>Dependency</th>
                  <th>State</th>
                  <th>Latency</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {status.readiness.dependencies.map((dependency) => (
                  <tr key={dependency.name}>
                    <td>{dependency.name}</td>
                    <td className={dependency.ok ? "status-ok" : "status-bad"}>
                      {dependency.ok ? "up" : "down"}
                    </td>
                    <td>{dependency.latency_ms} ms</td>
                    <td className="muted">{dependency.error ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
