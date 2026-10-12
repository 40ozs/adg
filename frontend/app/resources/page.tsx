import Link from "next/link";

import { fetchCollectionStatus, fetchServers } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { CoverageCaveat, StateMessage } from "@/components/Banners";
import { DirectoryTabs } from "@/components/DirectoryTabs";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { SortableTable } from "@/components/SortableTable";
import { serverHref } from "@/lib/resources";
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
      <DirectoryTabs current="servers" />
      <p className="muted">
        Servers ADG has observed, and the shares they publish. A share is a publication of a
        directory, not the directory — the two carry separate permissions and ADG keeps them
        separate.
      </p>

      {state.kind === "ready" && <CoverageCaveat caveat={state.caveat} />}
      <StateMessage state={state} />

      {state.kind === "ready" && (
        <SortableTable
          caption={
            <>
              {state.data.page.total ?? state.data.items.length} server(s).
              {state.data.page.has_more && " More pages are available through the API."} Sorting
              orders the rows shown.
            </>
          }
          columns={[
            { label: "Server" },
            { label: "DNS name", className: "muted" },
            { label: "Operating system", className: "muted" },
            { label: "Domain member" },
            { label: "Shares", type: "number" },
          ]}
          rows={state.data.items.map((server) => ({
            key: server.key,
            sort: [
              server.name,
              server.dns_host_name,
              server.operating_system,
              server.is_domain_member,
              server.share_count,
            ],
            cells: [
              <Link key="server" href={serverHref(server.key)}>{server.name}</Link>,
              server.dns_host_name ?? "—",
              server.operating_system ?? "—",
              // Null is "no run established this", never a guessed false.
              server.is_domain_member === null ? "unknown" : String(server.is_domain_member),
              server.share_count,
            ],
          }))}
        />
      )}
    </>
  );
}
