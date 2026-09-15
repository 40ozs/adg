import type { JSX } from "react";
import Link from "next/link";

import { rightsSummary } from "@/lib/access";
import type { AccessRow } from "@/lib/access";
import {
  fetchAccessibleResources,
  fetchAccessibleShares,
  fetchCollectionStatus,
  fetchDirectGroups,
  fetchDirectMembers,
  fetchEffectiveGroups,
  fetchEffectiveMembers,
  fetchPrincipalDetail,
  fetchTrusteeShares,
} from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type { CollectionStatus, MemberInclusion, PrincipalDetail } from "@/lib/contracts";
import { ambiguousLabels, isGroup, principalKindLabel, principalLabel } from "@/lib/identity";
import { cursorFor, decodeTrail, hrefWith, pageNavigation, type Trail } from "@/lib/paging";
import { shareHref } from "@/lib/resources";
import { classify, coverageCaveat } from "@/lib/state";
import { CoverageCaveat, StateMessage } from "@/components/Banners";
import {
  EffectiveLayer,
  FactSection,
  RawLayer,
} from "@/components/Layer";
import { FactList, Maybe, NotReported, TriState } from "@/components/Facts";
import { GlobalSearch } from "@/components/GlobalSearch";
import { PrincipalName } from "@/components/Identity";
import { PagePosition, Pager } from "@/components/Pager";
import { ResourceAccessTable, TokenSummary } from "@/components/EffectiveAccess";
import { ShareAceTable } from "@/components/RawAcl";
import { SimulateAction } from "@/components/SimulateLink";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { Tabs, type TabDefinition } from "@/components/Tabs";

const PATH = "/identities/principal";

/**
 * One principal: who it is, what it belongs to, what belongs to it, and what it reaches.
 *
 * A user page and a group page are the same page because ADG cannot know which it is
 * before it looks: the key is a SID, and `is_group` comes back from the lookup — sometimes
 * as null, when a membership edge reached a SID no run has described. The sections offered
 * follow that answer rather than a guess, and an undescribed principal is offered all of
 * them with a note saying why.
 *
 * Only the selected section runs a query. Membership traversal and effective access are
 * bounded but real work, and a page that eagerly loaded all five would spend most of it on
 * sections nobody opened.
 */
export default async function PrincipalPage({
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
    return <NoIdentityChosen />;
  }

  const token = viewer.session.accessToken;
  const host = single(params.host)?.trim() || undefined;
  const [coverage, detail] = await Promise.all([
    fetchCollectionStatus(token),
    fetchPrincipalDetail(token, key, { host }),
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
  const tabs = tabsFor(principal, { key, host });
  const current = tabs.find((tab) => tab.id === single(params.tab))?.id ?? tabs[0].id;
  const trail = decodeTrail(single(params.p));

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

      <Identity principal={principal} />

      <Tabs label="Identity sections" tabs={tabs} current={current} />
      <PagePosition
        trail={trail}
        firstPageHref={hrefWith(PATH, { key: principal.key, host, tab: current })}
      />

      {await panel({
        principal,
        tab: current,
        token,
        host,
        trail,
        params: { key, host, tab: current, include: single(params.include) },
        coverage: coverageData,
      })}
    </>
  );
}

/* ----------------------------------------------------------------- the header */

function Identity({ principal }: { principal: PrincipalDetail }): JSX.Element {
  return (
    <FactSection title="Identity" id="identity-facts">
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
    </FactSection>
  );
}

/* ------------------------------------------------------------------ the panels */

interface PanelOptions {
  principal: PrincipalDetail;
  tab: string;
  token: string;
  host: string | undefined;
  trail: Trail;
  params: Record<string, string | undefined>;
  coverage: CollectionStatus | null;
}

async function panel(options: PanelOptions): Promise<JSX.Element> {
  switch (options.tab) {
    case "members":
      return directMembersPanel(options);
    case "effective-members":
      return effectiveMembersPanel(options);
    case "effective-groups":
      return effectiveGroupsPanel(options);
    case "acl-references":
      return aclReferencesPanel(options);
    case "shares":
      return reachPanel(options, "share");
    case "directories":
      return reachPanel(options, "directory");
    default:
      return directGroupsPanel(options);
  }
}

async function directGroupsPanel(options: PanelOptions): Promise<JSX.Element> {
  const { principal, token, host, trail, coverage } = options;
  const result = await fetchDirectGroups(token, principal.key, {
    host,
    cursor: cursorFor(trail),
  });
  const state = classify(result, {
    isEmpty: (data) => data.items.length === 0,
    coverage,
    subject: "parent groups",
  });

  return (
    <FactSection title="Groups this principal is directly inside" id="panel-groups">
      <p className="muted">
        Only groups whose own membership list names this principal. Nesting is the next
        section, and the two are different answers.
      </p>
      <StateMessage state={state} />
      {state.kind === "ready" && (
        <>
          <MembershipTable
            rows={state.data.items.map((item) => ({
              principal: item.principal,
              edge: item.edge_kind,
              foreign: item.is_foreign_security_principal,
              lastSeen: item.last_observed_at,
            }))}
            membership={{ subjectKey: principal.key, direction: "groups-of" }}
          />
          <Pager
            navigation={pageNavigation({
              basePath: PATH,
              params: options.params,
              trailParam: "p",
              trail,
              nextCursor: state.data.page.next_cursor,
            })}
            page={state.data.page}
            subject="groups"
          />
        </>
      )}
    </FactSection>
  );
}

async function effectiveGroupsPanel(options: PanelOptions): Promise<JSX.Element> {
  const { principal, token, host, trail, coverage } = options;
  const result = await fetchEffectiveGroups(token, principal.key, {
    host,
    cursor: cursorFor(trail),
  });
  const state = classify(result, {
    isEmpty: (data) => data.items.length === 0,
    coverage,
    subject: "groups",
  });

  return (
    <FactSection title="Every group this principal ends up inside" id="panel-effective-groups">
      <p className="muted">
        Direct memberships and every nesting hop beyond them. This is the set that becomes
        SIDs in the principal&apos;s access token.
      </p>
      <StateMessage state={state} />
      {state.kind === "ready" && (
        <>
          <TraversalNotice traversal={state.data.traversal} cycles={state.data.cycles} subject="groups" />
          <ChainTable
            rows={state.data.items.map((item) => ({
              principal: item.principal,
              depth: item.depth,
              path: item.path,
              foreign: item.via_foreign_security_principal,
            }))}
            columnLabel="Group"
          />
          <Pager
            navigation={pageNavigation({
              basePath: PATH,
              params: options.params,
              trailParam: "p",
              trail,
              nextCursor: state.data.page.next_cursor,
            })}
            page={state.data.page}
            subject="groups"
          />
        </>
      )}
    </FactSection>
  );
}

async function directMembersPanel(options: PanelOptions): Promise<JSX.Element> {
  const { principal, token, host, trail, coverage } = options;
  const result = await fetchDirectMembers(token, principal.key, {
    host,
    cursor: cursorFor(trail),
  });
  const state = classify(result, {
    isEmpty: (data) => data.items.length === 0,
    coverage,
    subject: "direct members",
  });

  return (
    <FactSection title="Direct members" id="panel-members">
      <p className="muted">
        The principals this group&apos;s own membership list names. A nested group appears
        here as one row, whatever it contains.
      </p>
      <StateMessage state={state} />
      {state.kind === "ready" && (
        <>
          <MembershipTable
            rows={state.data.items.map((item) => ({
              principal: item.principal,
              edge: item.edge_kind,
              foreign: item.is_foreign_security_principal,
              lastSeen: item.last_observed_at,
            }))}
            membership={{ subjectKey: principal.key, direction: "members-of" }}
          />
          <Pager
            navigation={pageNavigation({
              basePath: PATH,
              params: options.params,
              trailParam: "p",
              trail,
              nextCursor: state.data.page.next_cursor,
            })}
            page={state.data.page}
            subject="members"
          />
        </>
      )}
    </FactSection>
  );
}

const INCLUSIONS: readonly { id: MemberInclusion; label: string }[] = [
  { id: "users", label: "Users only" },
  { id: "non_groups", label: "Everything that is not a group" },
  { id: "all", label: "Including nested groups" },
];

async function effectiveMembersPanel(options: PanelOptions): Promise<JSX.Element> {
  const { principal, token, host, trail, coverage } = options;
  const include = (INCLUSIONS.find((item) => item.id === options.params.include)?.id ??
    "non_groups") as MemberInclusion;

  const result = await fetchEffectiveMembers(token, principal.key, {
    host,
    include,
    cursor: cursorFor(trail),
  });
  const state = classify(result, {
    isEmpty: (data) => data.items.length === 0,
    coverage,
    subject: "effective members",
  });

  return (
    <FactSection title="Everything that ends up inside this group" id="panel-effective-members">
      <p className="muted">
        Every principal reached through nesting, with the shortest chain that puts it there.
        Filtering happens on the server, so the page count is a count of what matched.
      </p>
      <nav className="filter-strip" aria-label="Member kinds">
        {INCLUSIONS.map((item) => (
          <Link
            key={item.id}
            href={hrefWith(PATH, { ...options.params, include: item.id, p: null })}
            aria-current={item.id === include ? "true" : undefined}
          >
            {item.label}
          </Link>
        ))}
      </nav>
      <StateMessage state={state} />
      {state.kind === "ready" && (
        <>
          <TraversalNotice
            traversal={state.data.traversal}
            cycles={state.data.cycles}
            subject="members"
          />
          <ChainTable
            rows={state.data.items.map((item) => ({
              principal: item.principal,
              depth: item.depth,
              path: item.path,
              foreign: item.via_foreign_security_principal,
            }))}
            columnLabel="Member"
          />
          <Pager
            navigation={pageNavigation({
              basePath: PATH,
              params: options.params,
              trailParam: "p",
              trail,
              nextCursor: state.data.page.next_cursor,
            })}
            page={state.data.page}
            subject="members"
          />
        </>
      )}
    </FactSection>
  );
}

async function aclReferencesPanel(options: PanelOptions): Promise<JSX.Element> {
  const { principal, token, trail, coverage } = options;
  const result = await fetchTrusteeShares(token, principal.key, { cursor: cursorFor(trail) });
  const state = classify(result, {
    isEmpty: (data) => data.items.length === 0,
    coverage,
    subject: "share ACLs naming this principal",
  });

  return (
    <RawLayer title="Share ACLs that name this principal" id="panel-acl-references" layer="SMB share">
      <StateMessage state={state} />
      {state.kind === "ready" && (
        <>
          <p className="muted">
            Scope: <code>{state.data.scope}</code>.{" "}
            {state.data.scope === "sid"
              ? "Every server where this SID appears, which for a BUILTIN SID spans servers on purpose."
              : "One host-scoped principal on one server."}
          </p>
          {state.data.items.map((item) => (
            <section key={item.share_key} className="acl-group">
              <h3>
                <Link href={shareHref(item.share_key)}>
                  {item.share?.unc_path ?? item.share_key}
                </Link>
              </h3>
              {item.share === null && (
                <p className="status-warn">
                  These entries were collected for a share no run has described.
                </p>
              )}
              <ShareAceTable entries={item.aces} />
            </section>
          ))}
          <Pager
            navigation={pageNavigation({
              basePath: PATH,
              params: options.params,
              trailParam: "p",
              trail,
              nextCursor: state.data.page.next_cursor,
            })}
            page={state.data.page}
            subject="shares"
          />
        </>
      )}
    </RawLayer>
  );
}

async function reachPanel(
  options: PanelOptions,
  subject: "share" | "directory",
): Promise<JSX.Element> {
  const { principal, token, host, trail, coverage } = options;
  const fetcher = subject === "share" ? fetchAccessibleShares : fetchAccessibleResources;
  const result = await fetcher(token, principal.key, { host, cursor: cursorFor(trail) });
  const state = classify(result, {
    isEmpty: (data) => data.items.length === 0,
    coverage,
    subject: subject === "share" ? "reachable shares" : "reachable directories",
  });

  const title = subject === "share" ? "Shares this principal can reach" : "Directories this principal can reach";

  return (
    <EffectiveLayer title={title} id={`panel-${subject}s`}>
      <StateMessage state={state} />
      {state.kind === "ready" && (
        <>
          <TokenSummary token={state.data.token} />
          <RightSummary
            items={state.data.items}
            subject={subject === "share" ? "shares" : "directories"}
          />
          <ResourceAccessTable
            items={state.data.items}
            subject={subject}
            explainFor={principal.key}
          />
          <Pager
            navigation={pageNavigation({
              basePath: PATH,
              params: options.params,
              trailParam: "p",
              trail,
              nextCursor: state.data.page.next_cursor,
            })}
            page={state.data.page}
            subject={subject === "share" ? "shares" : "directories"}
          />
        </>
      )}
    </EffectiveLayer>
  );
}

/* ------------------------------------------------------------------- fragments */

/**
 * The effective-right summary, scoped out loud to the rows it was computed from.
 *
 * A summary over one page is not a summary over the estate, and saying so is the whole
 * value of it: "3 of 100 on this page" invites the next page, where "3 rows grant write"
 * invites a conclusion.
 */
function RightSummary({
  items,
  subject,
}: {
  items: readonly AccessRow[];
  subject: string;
}): JSX.Element | null {
  const summary = rightsSummary(items);
  if (summary.rows.length === 0) {
    return null;
  }
  return (
    <div className="banner banner-info" role="status">
      <h2>Effective rights across the {items.length} {subject} on this page</h2>
      <p>
        {summary.rows.map((row) => `${row.count} × ${row.category}`).join(", ")}.
        {summary.uncertain > 0 && (
          <>
            {" "}
            {summary.uncertain === 1
              ? "One of them is a bound rather than a measurement."
              : `${summary.uncertain} of them are bounds rather than measurements.`}
          </>
        )}
      </p>
      <p className="muted">
        This counts the rows on this page only. It is not a total for the estate.
      </p>
    </div>
  );
}

function MembershipTable({
  rows,
  membership,
}: {
  rows: readonly {
    principal: Parameters<typeof PrincipalName>[0]["principal"];
    edge: string;
    foreign: boolean;
    lastSeen: string;
  }[];
  /**
   * Which end of each edge this page is looking from, so a `Simulate` action can name the
   * group and the member the right way round. Omitted, no action is offered: a row that
   * could not say which of the two principals is the group must not propose to change the
   * membership, because the proposal would be about the wrong edge.
   */
  membership?: { subjectKey: string; direction: "groups-of" | "members-of" };
}): JSX.Element {
  const ambiguous = ambiguousLabels(rows.map((row) => row.principal));
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">Principal</th>
            <th scope="col">Edge</th>
            <th scope="col">Last observed</th>
            {membership && <th scope="col">What if</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.principal.key}>
              <th scope="row">
                <PrincipalName principal={row.principal} ambiguous={ambiguous} />
              </th>
              <td>
                {row.edge}
                {row.foreign && (
                  <div className="ace-notes">
                    through a foreign security principal — the real account lives in another
                    domain
                  </div>
                )}
              </td>
              <td className="muted">
                <time dateTime={row.lastSeen}>{row.lastSeen}</time>
              </td>
              {membership && (
                <td>
                  <SimulateAction
                    seed={{
                      kind: "remove_member",
                      group_key:
                        membership.direction === "groups-of"
                          ? row.principal.key
                          : membership.subjectKey,
                      member_key:
                        membership.direction === "groups-of"
                          ? membership.subjectKey
                          : row.principal.key,
                      edge_kind: row.edge,
                      subject_key:
                        membership.direction === "groups-of"
                          ? membership.subjectKey
                          : row.principal.key,
                    }}
                    title="Measure what removing this membership would do. Nothing is applied."
                  />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChainTable({
  rows,
  columnLabel,
}: {
  rows: readonly {
    principal: Parameters<typeof PrincipalName>[0]["principal"];
    depth: number;
    path: string[];
    foreign: boolean;
  }[];
  columnLabel: string;
}): JSX.Element {
  const ambiguous = ambiguousLabels(rows.map((row) => row.principal));
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">{columnLabel}</th>
            <th scope="col">Hops</th>
            <th scope="col">Shortest chain</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.principal.key}>
              <th scope="row">
                <PrincipalName principal={row.principal} ambiguous={ambiguous} />
              </th>
              <td>{row.depth}</td>
              <td className="muted">
                {row.path.join(" → ")}
                {row.foreign && <div className="ace-notes">via a foreign security principal</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Whether a traversal finished, and whether it found a loop.
 *
 * An incomplete traversal is a lower bound, and a list rendered from one must not be read
 * as a membership list. A cycle is a finding in the directory itself.
 */
function TraversalNotice({
  traversal,
  cycles,
  subject,
}: {
  traversal: { complete: boolean; truncation: string[]; depth_reached: number; nodes_visited: number };
  cycles: readonly { representative_path: string[] }[];
  subject: string;
}): JSX.Element | null {
  if (traversal.complete && cycles.length === 0) {
    return null;
  }
  return (
    <div className="banner banner-warning" role="alert">
      {!traversal.complete && (
        <>
          <h2>This is not the whole list</h2>
          <p>
            The traversal stopped at {traversal.depth_reached} hop(s) after visiting{" "}
            {traversal.nodes_visited} principal(s)
            {traversal.truncation.length > 0 ? ` (${traversal.truncation.join(", ")})` : ""}. More{" "}
            {subject} may qualify; read this as a lower bound rather than as a list.
          </p>
        </>
      )}
      {cycles.length > 0 && (
        <>
          <h2>Membership cycle</h2>
          {cycles.map((cycle) => (
            <p key={cycle.representative_path.join(">")}>
              <code>{cycle.representative_path.join(" → ")}</code>
            </p>
          ))}
          <p className="muted">
            A group that contains itself is a fault in the directory, not a rendering
            artifact.
          </p>
        </>
      )}
    </div>
  );
}

function NoIdentityChosen(): JSX.Element {
  return (
    <>
      <h1>Identity</h1>
      <div className="card">
        <h2>Which principal?</h2>
        <p className="muted">
          This page needs a SID or a host-scoped key. Search for one, or follow a trustee
          from an ACL.
        </p>
        <GlobalSearch />
      </div>
    </>
  );
}

/* --------------------------------------------------------------------- helpers */

function tabsFor(
  principal: PrincipalDetail,
  params: { key: string; host: string | undefined },
): TabDefinition[] {
  const at = (tab: string): string => hrefWith(PATH, { key: principal.key, host: params.host, tab });
  const group = isGroup(principal);

  const membership: TabDefinition[] =
    group === false
      ? []
      : [
          {
            id: "members",
            label: "Direct members",
            href: at("members"),
            hint: "What this group's own membership list names.",
          },
          {
            id: "effective-members",
            label: "Effective members",
            href: at("effective-members"),
            hint: "Everything reached through nesting, with the chain that puts it there.",
          },
        ];

  return [
    ...membership,
    {
      id: "groups",
      label: group === true ? "Parent groups" : "Direct groups",
      href: at("groups"),
      hint: "Groups whose membership list names this principal directly.",
    },
    {
      id: "effective-groups",
      label: "Effective groups",
      href: at("effective-groups"),
      hint: "Every group reached through nesting — the set that becomes token SIDs.",
    },
    {
      id: "shares",
      label: "Reachable shares",
      href: at("shares"),
      hint: "Engine answers: the share ACL crossed with the NTFS ACL of the directory it publishes.",
    },
    {
      id: "directories",
      label: "Reachable directories",
      href: at("directories"),
      hint: "Engine answers for directories whose ACLs could involve this principal.",
    },
    {
      id: "acl-references",
      label: "Named on share ACLs",
      href: at("acl-references"),
      hint: "Raw entries that name this SID. Being named is not the same as having access.",
    },
  ];
}

/** Next.js hands a repeated query parameter over as an array; one value is expected here. */
function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
