import type { JSX } from "react";
import Link from "next/link";

import { fetchCollectionStatus, fetchServer, fetchServerShares } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type { CollectionStatus, ServerDetail } from "@/lib/contracts";
import { cursorFor, decodeTrail, hrefWith, pageNavigation } from "@/lib/paging";
import { shareHref } from "@/lib/resources";
import { classify, coverageCaveat } from "@/lib/state";
import { CoverageCaveat, StateMessage } from "@/components/Banners";
import { FactList, Maybe, NotReported, Provenance, TriState } from "@/components/Facts";
import { GlobalSearch } from "@/components/GlobalSearch";
import { FactSection } from "@/components/Layer";
import { PagePosition, Pager } from "@/components/Pager";
import { SignedOutNotice } from "@/components/SignedOutNotice";

const PATH = "/resources/server";

/**
 * One server: what ADG knows about the machine, and the shares it publishes.
 *
 * The last-observed timestamps are the closest thing this product has to server health,
 * and they are the honest version of it: ADG does not probe a file server, it reports when
 * a collector last reached one. A server that stopped answering does not disappear from
 * this page — it keeps its facts and its dates get old, which is the finding.
 */
export default async function ServerPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [viewer, params] = await Promise.all([currentViewer(), searchParams]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const key = single(params.key)?.trim() ?? "";
  if (key === "") {
    return <NothingChosen />;
  }

  const token = viewer.session.accessToken;
  const trail = decodeTrail(single(params.p));
  const [coverage, detail, shares] = await Promise.all([
    fetchCollectionStatus(token),
    fetchServer(token, key),
    fetchServerShares(token, key, { cursor: cursorFor(trail) }),
  ]);
  const coverageData = coverage.ok ? coverage.data : null;

  if (!detail.ok) {
    return (
      <>
        <h1>Server</h1>
        <p className="muted">
          <code>{key}</code>
        </p>
        <StateMessage
          state={classify(detail, {
            isEmpty: () => false,
            coverage: coverageData,
            subject: "server",
          })}
        />
      </>
    );
  }

  const server = detail.data;
  const shareState = classify(shares, {
    isEmpty: (data) => data.items.length === 0,
    coverage: coverageData,
    subject: "shares",
  });

  return (
    <>
      <nav className="breadcrumb" aria-label="Location">
        <ol>
          <li>
            <Link href="/resources">Servers</Link>
          </li>
          <li>
            <span aria-current="page">{server.name}</span>
          </li>
        </ol>
      </nav>

      <h1>
        {server.name} <span className="title-kind">server</span>
      </h1>

      <CoverageCaveat caveat={coverageCaveat(coverageData)} />

      <Identity server={server} coverage={coverageData} />

      <FactSection title="Shares published by this server" id="server-shares">
        <p className="muted">
          A share is a publication of a directory, not the directory. The two carry separate
          permissions, and ADG keeps them apart.
        </p>
        <PagePosition trail={trail} firstPageHref={hrefWith(PATH, { key })} />
        <StateMessage state={shareState} />
        {shareState.kind === "ready" && (
          <>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Share</th>
                    <th scope="col">UNC path</th>
                    <th scope="col">Type</th>
                    <th scope="col">Publishes</th>
                    <th scope="col">Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {shareState.data.items.map((share) => (
                    <tr key={share.key}>
                      <th scope="row">
                        <Link href={shareHref(share.key)}>{share.name}</Link>
                      </th>
                      <td>
                        <code>{share.unc_path}</code>
                      </td>
                      <td>{share.share_type}</td>
                      <td className="muted">
                        <Maybe value={share.local_path} />
                      </td>
                      <td className="muted">
                        {[
                          share.is_administrative ? "administrative" : null,
                          share.is_hidden ? "hidden" : null,
                          share.carries_file_permissions ? null : "no NTFS layer beneath it",
                        ]
                          .filter(Boolean)
                          .join(" · ") || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager
              navigation={pageNavigation({
                basePath: PATH,
                params: { key },
                trailParam: "p",
                trail,
                nextCursor: shareState.data.page.next_cursor,
              })}
              page={shareState.data.page}
              subject="shares"
            />
          </>
        )}
      </FactSection>
    </>
  );
}

function Identity({
  server,
  coverage,
}: {
  server: ServerDetail;
  coverage: CollectionStatus | null;
}): JSX.Element {
  return (
    <FactSection title="What ADG knows about this machine" id="server-facts">
      <FactList
        facts={[
          { term: "Storage key", value: <code>{server.key}</code> },
          { term: "DNS host name", value: <Maybe value={server.dns_host_name} /> },
          { term: "NetBIOS name", value: <Maybe value={server.netbios_name} /> },
          {
            term: "Computer SID",
            value: server.computer_sid ? <code>{server.computer_sid}</code> : <NotReported />,
          },
          {
            term: "Domain SID",
            value: server.domain_sid ? <code>{server.domain_sid}</code> : <NotReported />,
          },
          { term: "Domain member", value: <TriState value={server.is_domain_member} /> },
          { term: "Operating system", value: <Maybe value={server.operating_system} /> },
          { term: "Shares held", value: server.share_count },
        ]}
      />

      <h3>Last scan</h3>
      <p className="muted">
        ADG does not probe this server; it reports when a collector last reached it. Old
        dates here are the finding.
      </p>
      <Provenance provenance={server.provenance} />
      {coverage !== null && coverage.health !== "healthy" && (
        <p className="status-warn">
          Collection across the estate is <strong>{coverage.health.replace("_", " ")}</strong>.{" "}
          <Link href="/collectors">See which runs failed.</Link>
        </p>
      )}
    </FactSection>
  );
}

function NothingChosen(): JSX.Element {
  return (
    <>
      <h1>Server</h1>
      <div className="card">
        <h2>Which server?</h2>
        <p className="muted">
          This page needs a server name. Search for one, or pick it from{" "}
          <Link href="/resources">Resources</Link>.
        </p>
        <GlobalSearch />
      </div>
    </>
  );
}

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
