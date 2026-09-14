import Link from "next/link";

import { fetchCollectionStatus, fetchServers } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { CoverageCaveat, StateMessage } from "@/components/Banners";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { classify } from "@/lib/state";

/**
 * Resources.
 *
 * Servers first, because a share's identity is scoped to one, and because a server ADG has
 * never heard from is the clearest possible statement of a gap in coverage.
 *
 * The coverage caveat sits above the table rather than only inside the empty state. A
 * populated table is the more dangerous case: twelve servers look complete whether or not a
 * collector failed on a thirteenth.
 */
export default async function ResourcesPage() {
  const viewer = await currentViewer();
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const token = viewer.session.accessToken;
  const [coverage, servers] = await Promise.all([
    fetchCollectionStatus(token),
    fetchServers(token, { limit: 50 }),
  ]);

  const state = classify(servers, {
    isEmpty: (data) => data.items.length === 0,
    coverage: coverage.ok ? coverage.data : null,
    subject: "servers",
  });

  return (
    <>
      <h1>Resources</h1>
      <p className="muted">
        Servers ADG has observed, and the shares they publish. A share is a publication of a
        directory, not the directory — the two carry separate permissions and ADG keeps them
        separate.
      </p>

      {state.kind === "ready" && <CoverageCaveat caveat={state.caveat} />}
      <StateMessage state={state} />

      {state.kind === "ready" && (
        <div className="table-scroll">
          <table>
            <caption>
              {state.data.page.total ?? state.data.items.length} server(s).
              {state.data.page.has_more && " More pages are available through the API."}
            </caption>
            <thead>
              <tr>
                <th scope="col">Server</th>
                <th scope="col">DNS name</th>
                <th scope="col">Operating system</th>
                <th scope="col">Domain member</th>
                <th scope="col">Shares</th>
              </tr>
            </thead>
            <tbody>
              {state.data.items.map((server) => (
                <tr key={server.key}>
                  <th scope="row">
                    <Link href={`/search?q=${encodeURIComponent(`\\\\${server.name}`)}`}>
                      {server.name}
                    </Link>
                  </th>
                  <td className="muted">{server.dns_host_name ?? "—"}</td>
                  <td className="muted">{server.operating_system ?? "—"}</td>
                  {/* Null is "no run established this", never a guessed false. */}
                  <td>{server.is_domain_member === null ? "unknown" : String(server.is_domain_member)}</td>
                  <td>{server.share_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
