import type { JSX } from "react";
import { fetchCollectionStatus, search } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { CoverageCaveat, StateMessage } from "@/components/Banners";
import { GlobalSearch } from "@/components/GlobalSearch";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import type { SearchResults } from "@/lib/contracts";
import { classify, coverageCaveat } from "@/lib/state";

/**
 * Search results.
 *
 * Three things are always on this page beside the hits, because without them an empty
 * result is unattributable: how ADG read the term, which categories were truncated, and
 * which the account was not allowed to search. "No group by that name" and "you may not
 * search identities" must never look alike.
 */
export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const [viewer, params] = await Promise.all([currentViewer(), searchParams]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const term = (params.q ?? "").trim();
  if (term === "") {
    return (
      <>
        <h1>Search</h1>
        <div className="card">
          <GlobalSearch />
          <p className="muted">
            A SID (<code>S-1-5-21-…</code>), a UNC path (<code>\\FS01\Finance</code>), a
            share name, or the start of a display name.
          </p>
        </div>
      </>
    );
  }

  const token = viewer.session.accessToken;
  const [coverage, results] = await Promise.all([
    fetchCollectionStatus(token),
    search(token, term),
  ]);

  const state = classify(results, {
    isEmpty: (data) => totalHits(data) === 0,
    coverage: coverage.ok ? coverage.data : null,
    subject: "matches",
  });

  return (
    <>
      <h1>Search</h1>
      <div className="card">
        <GlobalSearch initialQuery={term} />
      </div>

      {results.ok && (
        <p className="muted" role="status">
          {results.data.interpretation}
        </p>
      )}

      {state.kind === "ready" && (
        <CoverageCaveat caveat={coverageCaveat(coverage.ok ? coverage.data : null)} />
      )}
      <StateMessage state={state} />

      {results.ok && results.data.not_searched.length > 0 && (
        <div className="banner banner-warning" role="alert">
          <h2>Some categories were not searched</h2>
          {results.data.not_searched.map((item) => (
            <p key={item.category}>
              <strong>{item.category}:</strong> {item.reason}
            </p>
          ))}
          <p className="muted">
            An empty result below does not mean those objects do not exist.
          </p>
        </div>
      )}

      {state.kind === "ready" && (
        <>
          <Category
            title="Identities"
            truncated={state.data.truncated.includes("identities")}
            limit={state.data.limit_per_category}
            rows={state.data.identities.map((hit) => ({
              key: hit.principal_key,
              primary: hit.display_name ?? hit.sam_account_name ?? hit.sid,
              secondary: hit.sid,
              note: [
                hit.kind,
                hit.host_key ? `on ${hit.host_key}` : null,
                hit.enabled === false ? "disabled" : null,
                hit.is_deleted ? "deleted" : null,
              ]
                .filter(Boolean)
                .join(" · "),
            }))}
          />
          <Category
            title="Servers"
            truncated={state.data.truncated.includes("servers")}
            limit={state.data.limit_per_category}
            rows={state.data.servers.map((hit) => ({
              key: hit.server_key,
              primary: hit.name,
              secondary: hit.dns_host_name ?? hit.server_key,
              note: "",
            }))}
          />
          <Category
            title="Shares"
            truncated={state.data.truncated.includes("shares")}
            limit={state.data.limit_per_category}
            rows={state.data.shares.map((hit) => ({
              key: hit.share_key,
              primary: hit.name,
              secondary: hit.unc_path,
              note: [hit.share_type, hit.description].filter(Boolean).join(" · "),
            }))}
          />
          <Category
            title="Directories"
            truncated={state.data.truncated.includes("directories")}
            limit={state.data.limit_per_category}
            rows={state.data.directories.map((hit) => ({
              key: hit.resource_key,
              primary: hit.path,
              secondary: hit.share_key,
              note: hit.is_acl_boundary ? "ACL boundary" : "inherits from its parent",
            }))}
          />
        </>
      )}
    </>
  );
}

interface Row {
  key: string;
  primary: string;
  secondary: string;
  note: string;
}

function Category({
  title,
  rows,
  truncated,
  limit,
}: {
  title: string;
  rows: Row[];
  truncated: boolean;
  limit: number;
}): JSX.Element | null {
  if (rows.length === 0) {
    return null;
  }
  return (
    <section className="card" aria-labelledby={`category-${title.toLowerCase()}`}>
      <h2 id={`category-${title.toLowerCase()}`}>{title}</h2>
      {truncated && (
        <p className="status-warn" role="status">
          More than {limit} matched. Only the first {limit} are shown — narrow the term.
        </p>
      )}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Identifier</th>
              <th scope="col">Detail</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <th scope="row">{row.primary}</th>
                <td>
                  <code>{row.secondary}</code>
                </td>
                <td className="muted">{row.note || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function totalHits(results: SearchResults): number {
  return (
    results.identities.length +
    results.servers.length +
    results.shares.length +
    results.directories.length
  );
}
