import type { JSX, ReactNode } from "react";

import { currentViewer } from "@/lib/auth/current";
import type { PrincipalDetail } from "@/lib/contracts";
import {
  isGroup,
  principalHrefForKey,
  principalKindLabel,
  principalLabel,
  principalViews,
} from "@/lib/identity";
import { classify, coverageCaveat } from "@/lib/state";
import { CoverageCaveat, StateMessage } from "@/components/Banners";
import { FactList, Maybe, NotReported, TriState } from "@/components/Facts";
import { GlobalSearch } from "@/components/GlobalSearch";
import { PrincipalViewTabs } from "@/components/PrincipalViewTabs";
import { SignedOutNotice } from "@/components/SignedOutNotice";

import { collectionStatus, keyFromSegment, principalDetail } from "./data";

/**
 * One principal: who it is, above whichever relationship view is open.
 *
 * A user page and a group page are the same page because ADG cannot know which it is
 * before it looks: the key is a SID, and `is_group` comes back from the lookup — sometimes
 * as null, when a membership edge reached a SID no run has described. The views offered
 * follow that answer rather than a guess (`principalViews`).
 *
 * This is a layout, and the view is a page beneath it, so that moving between views is a
 * client-side navigation that re-renders the view alone: the identity is read once per
 * principal, the application shell and this header stay put, and the reader's folding of
 * the identity section survives. Only the selected view runs its query.
 */
export default async function PrincipalLayout({
  params,
  children,
}: {
  params: Promise<{ key: string }>;
  children: ReactNode;
}) {
  const [viewer, route] = await Promise.all([currentViewer(), params]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const key = keyFromSegment(route.key);
  const token = viewer.session.accessToken;
  const [coverage, detail] = await Promise.all([
    collectionStatus(token),
    principalDetail(token, key),
  ]);
  const coverageData = coverage.ok ? coverage.data : null;

  if (!detail.ok) {
    return (
      <>
        <h1>Identity</h1>
        <p className="muted">
          <code>{key}</code>
        </p>
        <StateMessage
          state={classify(detail, {
            isEmpty: () => false,
            coverage: coverageData,
            subject: "identity",
          })}
        />
        <div className="card">
          <h2>Look up another identity</h2>
          <GlobalSearch />
        </div>
      </>
    );
  }

  const principal = detail.data;
  return (
    <>
      <h1>
        {principalLabel(principal)}{" "}
        <span className="title-kind">{principalKindLabel(principal)}</span>
      </h1>

      <CoverageCaveat caveat={coverageCaveat(coverageData)} />
      {!principal.resolved && (
        <div className="banner banner-warning" role="alert">
          <h2>No run has described this SID</h2>
          <p>
            ADG holds no principal record for <code>{principal.key}</code>
            {principal.unresolved_reason
              ? ` (${principal.unresolved_reason.replace(/_/g, " ")})`
              : ""}
            . It is still a real trustee on real ACLs — an orphaned SID is a finding, not a
            blank page — but its name, kind, and membership are unknown.
          </p>
        </div>
      )}

      <IdentityInformation principal={principal} />

      <PrincipalViewTabs
        label="Identity sections"
        tabs={principalViews(principal).map((view) => ({
          ...view,
          href: principalHrefForKey(principal.key, view.id),
        }))}
      />

      {children}
    </>
  );
}

/**
 * Identity information, as a disclosure the reader can fold away.
 *
 * A native `<details>`: the browser supplies the keyboard behavior and the expanded state
 * assistive technology announces, and its open state is the DOM's. Because it lives in this
 * layout, moving between views does not render it again, so a folded section stays folded.
 * Opening another principal renders a new layout, which starts open.
 */
function IdentityInformation({ principal }: { principal: PrincipalDetail }): JSX.Element {
  return (
    <details className="card disclosure" open>
      <summary>
        <h2 id="identity-facts">Identity information</h2>
      </summary>
      <FactList
        facts={[
          { term: "SID", value: <code>{principal.sid}</code> },
          {
            term: "Storage key",
            value: (
              <>
                <code>{principal.key}</code>
                {principal.host_key && (
                  <span className="muted">
                    {" "}
                    — scoped to <code>{principal.host_key}</code>, so this SID means
                    something different on every other computer
                  </span>
                )}
              </>
            ),
          },
          { term: "Kind", value: principalKindLabel(principal) },
          {
            term: "Enabled",
            value: (
              <>
                <TriState value={principal.enabled} />
                {principal.enabled === false && (
                  <span className="muted">
                    {" "}
                    — a disabled account still appears on ACLs, because the ACL has not
                    changed
                  </span>
                )}
              </>
            ),
          },
          { term: "Deleted", value: principal.is_deleted ? "yes" : "no" },
          { term: "Account name", value: <Maybe value={principal.sam_account_name} /> },
          { term: "User principal name", value: <Maybe value={principal.user_principal_name} /> },
          { term: "Distinguished name", value: <Maybe value={principal.distinguished_name} /> },
          { term: "Domain SID", value: principal.domain_sid ? <code>{principal.domain_sid}</code> : <NotReported /> },
          { term: "Group scope", value: <Maybe value={principal.group_scope} /> },
          { term: "Group type", value: <Maybe value={principal.group_type} /> },
          // "Direct members: 0" beside an account is noise at best, and an invitation to
          // read it as a group at worst. The row exists only where it means something.
          ...(isGroup(principal) === false
            ? []
            : [{ term: "Direct members", value: principal.direct_member_count }]),
          { term: "Directly inside", value: `${principal.direct_group_count} group(s)` },
          {
            term: "First observed",
            value: principal.first_observed_at ? (
              <time dateTime={principal.first_observed_at}>{principal.first_observed_at}</time>
            ) : (
              <NotReported />
            ),
          },
          {
            term: "Last observed",
            value: principal.last_observed_at ? (
              <time dateTime={principal.last_observed_at}>{principal.last_observed_at}</time>
            ) : (
              <NotReported />
            ),
          },
          {
            term: "Last run",
            value: principal.last_observed_run_id ? (
              <code>{principal.last_observed_run_id}</code>
            ) : (
              <NotReported />
            ),
          },
        ]}
      />

      {principal.aliases.length > 0 && (
        <>
          <h3>Names this SID has been seen under</h3>
          <p className="muted">
            A SID keeps its identity when its name changes. An ACE written under an old name
            still names this principal.
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Kind</th>
                  <th scope="col">Value</th>
                  <th scope="col">First seen</th>
                  <th scope="col">Last seen</th>
                </tr>
              </thead>
              <tbody>
                {principal.aliases.map((alias) => (
                  <tr key={`${alias.alias_kind}:${alias.value}`}>
                    <td>{alias.alias_kind}</td>
                    <th scope="row">{alias.value}</th>
                    <td className="muted">{alias.first_observed_at}</td>
                    <td className="muted">{alias.last_observed_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </details>
  );
}
