import type { JSX } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { rightsSummary } from "@/lib/access";
import type { AccessRow } from "@/lib/access";
import {
  fetchAccessibleResources,
  fetchAccessibleShares,
  fetchDirectGroups,
  fetchDirectMembers,
  fetchEffectiveGroups,
  fetchEffectiveMembers,
  fetchTrusteeShares,
} from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type { CollectionStatus, MemberInclusion, PrincipalSummary } from "@/lib/contracts";
import {
  ambiguousLabels,
  chainPrincipals,
  PRINCIPAL_VIEW_IDS,
  principalHrefForKey,
  principalLabel,
  principalViews,
} from "@/lib/identity";
import { cursorFor, decodeTrail, hrefWith, pageNavigation, type Trail } from "@/lib/paging";
import { shareHref } from "@/lib/resources";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { EffectiveLayer, FactSection, RawLayer } from "@/components/Layer";
import { PrincipalChain, PrincipalName } from "@/components/Identity";
import { PagePosition, Pager } from "@/components/Pager";
import { ResourceAccessTable, TokenSummary } from "@/components/EffectiveAccess";
import { ShareAceTable } from "@/components/RawAcl";
import { SimulateAction } from "@/components/SimulateLink";
import { SortableTable } from "@/components/SortableTable";

import { collectionStatus, keyFromSegment, principalDetail } from "../data";

/**
 * One relationship view of a principal, beneath the principal's layout.
 *
 * Only this view's query runs. Membership traversal and effective access are bounded but
 * real work, and a page that eagerly loaded every view would spend most of it on views
 * nobody opened. With no view in the path, the principal's first view is shown; finding
 * which one that is reads the principal, which the layout in the same request has already
 * read.
 */
export default async function PrincipalViewPage({
  params,
  searchParams,
}: {
  params: Promise<{ key: string; view?: string[] }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [viewer, route, query] = await Promise.all([currentViewer(), params, searchParams]);
  // The layout above says why there is nothing to show.
  if (viewer.status !== "signed-in") {
    return null;
  }
  const key = keyFromSegment(route.key);
  const token = viewer.session.accessToken;

  let view = route.view?.[0];
  if ((route.view?.length ?? 0) > 1 || (view !== undefined && !PRINCIPAL_VIEW_IDS.includes(view))) {
    notFound();
  }
  if (view === undefined) {
    const detail = await principalDetail(token, key);
    if (!detail.ok) {
      return null;
    }
    view = principalViews(detail.data)[0].id;
  }

  const coverage = await collectionStatus(token);
  const trail = decodeTrail(single(query.p));
  const basePath = principalHrefForKey(key, view);

  return (
    <>
      <PagePosition trail={trail} firstPageHref={hrefWith(basePath, { include: single(query.include) })} />
      {await panel({
        key,
        view,
        token,
        trail,
        basePath,
        include: single(query.include),
        coverage: coverage.ok ? coverage.data : null,
      })}
    </>
  );
}

/* ------------------------------------------------------------------ the panels */

interface PanelOptions {
  /** The principal's storage key, from the path. */
  key: string;
  view: string;
  token: string;
  trail: Trail;
  /** This view's own path, which paging and filtering stay on. */
  basePath: string;
  include: string | undefined;
  coverage: CollectionStatus | null;
}

async function panel(options: PanelOptions): Promise<JSX.Element> {
  switch (options.view) {
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
  const { key, token, trail, coverage } = options;
  const result = await fetchDirectGroups(token, key, { cursor: cursorFor(trail) });
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
            membership={{ subjectKey: key, direction: "groups-of" }}
          />
          <Pager
            navigation={pageNavigation({
              basePath: options.basePath,
              params: { include: options.include },
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
  const { key, token, trail, coverage } = options;
  const result = await fetchEffectiveGroups(token, key, { cursor: cursorFor(trail) });
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
              chain: chainPrincipals(item.path, item.path_principals),
              foreign: item.via_foreign_security_principal,
            }))}
            columnLabel="Group"
          />
          <Pager
            navigation={pageNavigation({
              basePath: options.basePath,
              params: { include: options.include },
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
  const { key, token, trail, coverage } = options;
  const result = await fetchDirectMembers(token, key, { cursor: cursorFor(trail) });
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
            membership={{ subjectKey: key, direction: "members-of" }}
          />
          <Pager
            navigation={pageNavigation({
              basePath: options.basePath,
              params: { include: options.include },
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
  const { key, token, trail, coverage } = options;
  const include = (INCLUSIONS.find((item) => item.id === options.include)?.id ??
    "non_groups") as MemberInclusion;

  const result = await fetchEffectiveMembers(token, key, {
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
            href={hrefWith(options.basePath, { include: item.id })}
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
              chain: chainPrincipals(item.path, item.path_principals),
              foreign: item.via_foreign_security_principal,
            }))}
            columnLabel="Member"
          />
          <Pager
            navigation={pageNavigation({
              basePath: options.basePath,
              params: { include: options.include },
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
  const { key, token, trail, coverage } = options;
  const result = await fetchTrusteeShares(token, key, { cursor: cursorFor(trail) });
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
              basePath: options.basePath,
              params: { include: options.include },
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
  const { key, token, trail, coverage } = options;
  const fetcher = subject === "share" ? fetchAccessibleShares : fetchAccessibleResources;
  const result = await fetcher(token, key, { cursor: cursorFor(trail) });
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
            explainFor={key}
          />
          <Pager
            navigation={pageNavigation({
              basePath: options.basePath,
              params: { include: options.include },
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
    principal: PrincipalSummary;
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
    <SortableTable
      caption="Sorting orders the rows on this page."
      columns={[
        { label: "Principal" },
        { label: "Edge" },
        { label: "Last observed", type: "date", className: "muted" },
        ...(membership ? [{ label: "What if", sortable: false }] : []),
      ]}
      rows={rows.map((row) => ({
        key: row.principal.key,
        sort: [principalLabel(row.principal), row.edge, row.lastSeen],
        cells: [
          <PrincipalName key="principal" principal={row.principal} ambiguous={ambiguous} />,
          <>
            {row.edge}
            {row.foreign && (
              <div className="ace-notes">
                through a foreign security principal — the real account lives in another
                domain
              </div>
            )}
          </>,
          <time key="seen" dateTime={row.lastSeen}>
            {row.lastSeen}
          </time>,
          ...(membership
            ? [
                <SimulateAction key="simulate"
                  seed={{
                    kind: "remove_member",
                    group_key:
                      membership.direction === "groups-of" ? row.principal.key : membership.subjectKey,
                    member_key:
                      membership.direction === "groups-of" ? membership.subjectKey : row.principal.key,
                    edge_kind: row.edge,
                    subject_key:
                      membership.direction === "groups-of" ? membership.subjectKey : row.principal.key,
                  }}
                  title="Measure what removing this membership would do. Nothing is applied."
                />,
              ]
            : []),
        ],
      }))}
    />
  );
}

/**
 * Principals reached through nesting, each with the shortest chain that reaches it.
 *
 * The chain is names, each opening its principal; a hop no run has described stays its SID.
 */
function ChainTable({
  rows,
  columnLabel,
}: {
  rows: readonly {
    principal: PrincipalSummary;
    depth: number;
    chain: PrincipalSummary[];
    foreign: boolean;
  }[];
  columnLabel: string;
}): JSX.Element {
  const ambiguous = ambiguousLabels(rows.map((row) => row.principal));
  return (
    <SortableTable
      caption="Sorting orders the rows on this page."
      columns={[{ label: columnLabel }, { label: "Hops", type: "number" }, { label: "Shortest chain" }]}
      rows={rows.map((row) => ({
        key: row.principal.key,
        sort: [
          principalLabel(row.principal),
          row.depth,
          row.chain.map((principal) => principalLabel(principal)).join(" → "),
        ],
        cells: [
          <PrincipalName key="principal" principal={row.principal} ambiguous={ambiguous} />,
          row.depth,
          <>
            <PrincipalChain principals={row.chain} />
            {row.foreign && <div className="ace-notes">via a foreign security principal</div>}
          </>,
        ],
      }))}
    />
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

/** Next.js hands a repeated query parameter over as an array; one value is expected here. */
function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
