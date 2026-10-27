import type { JSX } from "react";
import Form from "next/form";

import { fetchCollectionStatus, fetchPrincipals } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type { DirectoryKind, ListedPrincipalView } from "@/lib/contracts";
import { ambiguousLabels, principalKindLabel, principalLabel } from "@/lib/identity";
import { cursorFor, decodeTrail, pageNavigation } from "@/lib/paging";
import { classify } from "@/lib/state";
import { CoverageCaveat, StateMessage } from "@/components/Banners";
import { DirectoryTabs } from "@/components/DirectoryTabs";
import { Maybe, Sid, TriState } from "@/components/Facts";
import { FilterToggle } from "@/components/FilterToggle";
import { PrincipalName } from "@/components/Identity";
import { Pager } from "@/components/Pager";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { type SortableColumn, SortableTable, type SortableRow } from "@/components/SortableTable";

const PAGE_SIZE = 50;

const PATHS: Record<DirectoryKind, string> = {
  users: "/identities/users",
  groups: "/identities/groups",
};

/**
 * The users or the groups directory.
 *
 * A server-paged listing, never the whole domain: the API pages by key, and the search is a
 * prefix of a SID (`S-1-5-21-…`) or of a display, account or UPN name, run on the server.
 * The search box is a GET form handled by Next.js, so a search is a client-side navigation
 * with the term in the URL, and the page position travels in `p` as it does everywhere
 * else. A row opens its principal by storage key.
 *
 * Groups can leave out local groups: those on one computer, by kind, filtered by the API so
 * that the total and the pages count only what is shown. A Domain Local group is an Active
 * Directory group and stays.
 */
export async function PrincipalDirectory({
  kind,
  searchParams,
}: {
  kind: DirectoryKind;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<JSX.Element> {
  const [viewer, params] = await Promise.all([currentViewer(), searchParams]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const q = single(params.q)?.trim() ?? "";
  const excludeLocal = kind === "groups" && single(params.exclude_local) === "1";
  const trail = decodeTrail(single(params.p));
  const token = viewer.session.accessToken;
  const [coverage, result] = await Promise.all([
    fetchCollectionStatus(token),
    fetchPrincipals(token, kind, {
      q: q || undefined,
      exclude_local: excludeLocal ? "true" : undefined,
      limit: PAGE_SIZE,
      cursor: cursorFor(trail),
    }),
  ]);
  const state = classify(result, {
    isEmpty: (data) => data.items.length === 0,
    coverage: coverage.ok ? coverage.data : null,
    subject: q ? `${kind} matching "${q}"` : kind,
  });
  const title = kind === "users" ? "Users" : "Groups";

  return (
    <>
      <h1>{title}</h1>
      <DirectoryTabs current={kind} />

      <Form id="directory-search-form" action={PATHS[kind]} className="search-form" role="search">
        <label htmlFor="directory-search" className="visually-hidden">
          Search {kind} by name or SID
        </label>
        <input
          id="directory-search"
          type="search"
          name="q"
          defaultValue={q}
          autoComplete="off"
          placeholder="Name, account name, or SID"
        />
        <button type="submit">Search</button>
      </Form>
      <p className="muted">
        Matches the start of a display, account or UPN name, case-insensitively, or the start
        of a SID. <code className="nowrap">DOMAIN\name</code> matches on the account name.
      </p>
      {kind === "groups" && (
        <p>
          <FilterToggle form="directory-search-form" name="exclude_local" checked={excludeLocal}>
            Exclude local groups
          </FilterToggle>{" "}
          <span className="muted">
            Local groups exist on one computer, such as BUILTIN\Administrators on a file server.
            Domain Local groups are Active Directory groups and stay.
          </span>
        </p>
      )}

      {state.kind === "ready" && <CoverageCaveat caveat={state.caveat} />}
      <StateMessage state={state} />
      {state.kind === "ready" && (
        <>
          <SortableTable
            caption="Pages follow SID order. Sorting orders the rows on this page."
            columns={kind === "users" ? USER_COLUMNS : GROUP_COLUMNS}
            rows={directoryRows(kind, state.data.items)}
          />
          <Pager
            navigation={pageNavigation({
              basePath: PATHS[kind],
              params: { q: q || null, exclude_local: excludeLocal ? "1" : null },
              trailParam: "p",
              trail,
              nextCursor: state.data.page.next_cursor,
            })}
            page={state.data.page}
            subject={kind}
          />
        </>
      )}
    </>
  );
}

const USER_COLUMNS: SortableColumn[] = [
  { label: "Name" },
  { label: "Account name" },
  { label: "User principal name", className: "muted" },
  { label: "SID" },
  { label: "Enabled" },
  { label: "Direct groups", type: "number" },
];

// Short labels stay on one line, so a row is one line high however narrow the page.
const GROUP_COLUMNS: SortableColumn[] = [
  { label: "Name" },
  { label: "Account name", className: "nowrap" },
  { label: "Kind", className: "nowrap" },
  { label: "Scope", className: "nowrap" },
  { label: "SID" },
  { label: "Direct members", type: "number" },
  { label: "Parent groups", type: "number" },
];

function directoryRows(kind: DirectoryKind, items: readonly ListedPrincipalView[]): SortableRow[] {
  const ambiguous = ambiguousLabels(items);
  return items.map((item) => {
    const name = <PrincipalName principal={item} ambiguous={ambiguous} />;
    const sid = <Sid value={item.sid} />;
    return kind === "users"
      ? {
          key: item.key,
          sort: [
            principalLabel(item),
            item.sam_account_name,
            item.user_principal_name,
            item.sid,
            item.enabled,
            item.direct_group_count,
          ],
          cells: [
            name,
            <Maybe key="account" value={item.sam_account_name} />,
            <Maybe key="upn" value={item.user_principal_name} />,
            sid,
            <TriState key="enabled" value={item.enabled} />,
            item.direct_group_count,
          ],
        }
      : {
          key: item.key,
          sort: [
            principalLabel(item),
            item.sam_account_name,
            principalKindLabel(item),
            item.group_scope,
            item.sid,
            item.direct_member_count,
            item.direct_group_count,
          ],
          cells: [
            name,
            <Maybe key="account" value={item.sam_account_name} />,
            principalKindLabel(item),
            <Maybe key="scope" value={item.group_scope?.replace(/_/g, " ")} />,
            sid,
            item.direct_member_count,
            item.direct_group_count,
          ],
        };
  });
}

/** Next.js hands a repeated query parameter over as an array; one value is expected here. */
function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
