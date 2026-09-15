import type { JSX } from "react";
import Link from "next/link";

import {
  aclHashNotes,
  boundaryNotes,
  daclNotes,
  inheritanceNotes,
  storedEntryNotes,
} from "@/lib/acl";
import {
  fetchCollectionStatus,
  fetchResource,
  fetchResourceAcl,
  fetchResourcePrincipals,
} from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type { CollectionStatus, NtfsResourceDetailView } from "@/lib/contracts";
import { cursorFor, decodeTrail, hrefWith, pageNavigation, type Trail } from "@/lib/paging";
import { directoryHref, resourceBreadcrumb, shareHref } from "@/lib/resources";
import { classify, coverageCaveat } from "@/lib/state";
import { CoverageCaveat, StateMessage } from "@/components/Banners";
import {
  EnumerationNotice,
  FindingsNotice,
  PrincipalAccessTable,
} from "@/components/EffectiveAccess";
import { FactList, Maybe, NoteList, NotReported, Provenance } from "@/components/Facts";
import { GlobalSearch } from "@/components/GlobalSearch";
import { EffectiveLayer, FactSection, RawLayer } from "@/components/Layer";
import { PagePosition, Pager } from "@/components/Pager";
import { NtfsAceTable } from "@/components/RawAcl";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { Breadcrumbs, Tabs, type TabDefinition } from "@/components/Tabs";

const PATH = "/resources/directory";

/**
 * One directory: where it sits, whose entries it carries, and who gets through them.
 *
 * The question this page answers is "did permissions change here, or are these inherited
 * from somewhere else?" — because the answer decides where a fix has to be applied. A
 * change made on an inherited entry, on the wrong directory, changes nothing and looks like
 * it worked.
 *
 * So the boundary and inheritance facts come first, the entries carry their own
 * explicit / inherited source, and ADG's own boundary verdict is shown beside the
 * collector's rather than instead of it.
 */
export default async function DirectoryPage({
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
  const [coverage, detail] = await Promise.all([
    fetchCollectionStatus(token),
    fetchResource(token, key),
  ]);
  const coverageData = coverage.ok ? coverage.data : null;

  if (!detail.ok) {
    return (
      <>
        <h1>Directory</h1>
        <p className="muted">
          <code>{key}</code>
        </p>
        <StateMessage
          state={classify(detail, {
            isEmpty: () => false,
            coverage: coverageData,
            subject: "directory",
          })}
        />
      </>
    );
  }

  const resource = detail.data;
  const tabs = tabsFor(resource);
  const current = tabs.find((tab) => tab.id === single(params.tab))?.id ?? tabs[0].id;
  const trail = decodeTrail(single(params.p));

  return (
    <>
      <Breadcrumbs crumbs={resourceBreadcrumb(resource.path)} />

      <h1>
        {resource.path} <span className="title-kind">{resource.resource_kind}</span>
      </h1>

      <CoverageCaveat caveat={coverageCaveat(coverageData)} />

      <NoteList notes={[...daclNotes(resource), ...storedEntryNotes(resource)]} />

      <Placement resource={resource} />
      <Inheritance resource={resource} />

      <Tabs label="Directory sections" tabs={tabs} current={current} />
      <PagePosition
        trail={trail}
        firstPageHref={hrefWith(PATH, { key: resource.key, tab: current })}
      />

      {await panel({ resource, tab: current, token, trail, coverage: coverageData })}
    </>
  );
}

/* ------------------------------------------------------------------ the header */

function Placement({ resource }: { resource: NtfsResourceDetailView }): JSX.Element {
  return (
    <FactSection title="Where this sits" id="directory-placement">
      <FactList
        facts={[
          { term: "Storage key", value: <code>{resource.key}</code> },
          {
            term: "Share",
            value: (
              <Link href={shareHref(resource.share_key)}>
                {resource.share?.unc_path ?? resource.share_key}
              </Link>
            ),
          },
          {
            term: "Parent",
            value: resource.parent ? (
              <Link href={directoryHref(resource.parent.key)}>{resource.parent.path}</Link>
            ) : resource.is_share_root ? (
              <span className="muted">
                none inside the share — this is the directory the share publishes
              </span>
            ) : resource.parent_path ? (
              <span className="status-warn">
                <code>{resource.parent_path}</code> — derived from this path, but no run has
                read it
              </span>
            ) : (
              <NotReported />
            ),
          },
          {
            term: "Depth below the share root",
            value: <Maybe value={resource.depth_from_share_root} />,
          },
          { term: "Path on the server", value: <Maybe value={resource.local_path} /> },
          {
            term: "Owner",
            value: resource.owner_sid ? (
              <>
                <code>{resource.owner_sid}</code>
                <span className="muted">
                  {" "}
                  — the owner holds READ_CONTROL and WRITE_DAC whatever the entries below
                  say, and can therefore grant itself anything
                </span>
              </>
            ) : (
              <NotReported />
            ),
          },
          {
            term: "Primary group",
            value: resource.group_sid ? <code>{resource.group_sid}</code> : <NotReported />,
          },
          { term: "Entries declared", value: resource.declared_ace_count },
          { term: "Entries held", value: resource.stored_ace_count },
        ]}
      />
      <h3>Last scan</h3>
      <Provenance provenance={resource.provenance} />
    </FactSection>
  );
}

function Inheritance({ resource }: { resource: NtfsResourceDetailView }): JSX.Element {
  const boundary = resource.boundary;
  return (
    <FactSection title="Inheritance and the ACL boundary" id="directory-inheritance">
      <NoteList notes={inheritanceNotes(resource)} />

      <h3>ACL digest</h3>
      <p className="muted">
        A digest of the normalized DACL, taken over the entries and their order. Two
        directories with the same digest carry the same ACL; a child whose digest differs
        from the projection of its parent is where permissions changed.
      </p>
      <FactList
        facts={[
          { term: "This directory", value: <code>{boundary.resource_acl_hash}</code> },
          {
            term: "Parent",
            value: boundary.parent_acl_hash ? (
              <code>{boundary.parent_acl_hash}</code>
            ) : (
              <span className="muted">
                {boundary.parent_observed ? "not computed" : "the parent has not been read"}
              </span>
            ),
          },
          {
            term: "A clean child of that parent would carry",
            value: boundary.projected_child_acl_hash ? (
              <code>{boundary.projected_child_acl_hash}</code>
            ) : (
              <span className="muted">not available — the parent&apos;s entries were not in reach</span>
            ),
          },
          {
            term: "Collector's verdict",
            value: (
              <>
                {boundary.reported ? "a boundary" : "not a boundary"}
                {boundary.reported_reason && (
                  <span className="muted"> ({boundary.reported_reason.replace(/_/g, " ")})</span>
                )}
              </>
            ),
          },
          {
            term: "ADG's verdict",
            value:
              boundary.computed === null ? (
                <span className="muted">could not be computed from the parent ADG holds</span>
              ) : (
                <>
                  {boundary.computed ? "a boundary" : "not a boundary"}
                  {boundary.computed_reason && (
                    <span className="muted"> ({boundary.computed_reason.replace(/_/g, " ")})</span>
                  )}
                </>
              ),
          },
        ]}
      />
      <NoteList notes={boundaryNotes(resource)} />
    </FactSection>
  );
}

/* ------------------------------------------------------------------ the panels */

interface PanelOptions {
  resource: NtfsResourceDetailView;
  tab: string;
  token: string;
  trail: Trail;
  coverage: CollectionStatus | null;
}

async function panel(options: PanelOptions): Promise<JSX.Element> {
  return options.tab === "effective" ? effectivePanel(options) : aclPanel(options);
}

async function aclPanel(options: PanelOptions): Promise<JSX.Element> {
  const { resource, token, trail, coverage } = options;
  const result = await fetchResourceAcl(token, resource.key, { cursor: cursorFor(trail) });
  const state = classify(result, {
    isEmpty: (data) => data.entries.length === 0,
    coverage,
    subject: "entries on this DACL",
  });

  return (
    <RawLayer title="NTFS ACL" id="panel-acl" layer="NTFS">
      <StateMessage state={state} />
      {state.kind === "ready" && (
        <>
          <NoteList notes={aclHashNotes(state.data.acl_hash)} />
          <NtfsAceTable entries={state.data.entries} resourceKey={resource.path} />
          <p className="muted">
            Entries are listed in the order the DACL stores them, across pages. ADG does not
            offer an explicit-only filter here: filtering one page would describe the page
            rather than the ACL, and the API pages this list server-side.
          </p>
          <Pager
            navigation={pageNavigation({
              basePath: PATH,
              params: { key: resource.key, tab: "acl" },
              trailParam: "p",
              trail,
              nextCursor: state.data.page.next_cursor,
            })}
            page={state.data.page}
            subject="entries"
          />
        </>
      )}
    </RawLayer>
  );
}

async function effectivePanel(options: PanelOptions): Promise<JSX.Element> {
  const { resource, token, trail, coverage } = options;
  const result = await fetchResourcePrincipals(token, resource.key, { cursor: cursorFor(trail) });
  const state = classify(result, {
    isEmpty: (data) => data.items.length === 0,
    coverage,
    subject: "principals with access",
  });

  return (
    <EffectiveLayer title="Who can reach this directory" id="panel-effective">
      <StateMessage state={state} />
      {state.kind === "ready" && (
        <>
          <p className="muted">
            Access path: <code>{state.data.access_path}</code>.
            {state.data.share
              ? " The share ACL above this directory is part of the answer."
              : " No share was involved in this evaluation."}
          </p>
          <EnumerationNotice enumeration={state.data.enumeration} />
          <FindingsNotice findings={state.data.findings} />
          <PrincipalAccessTable items={state.data.items} explainOn={resource.key} />
          <Pager
            navigation={pageNavigation({
              basePath: PATH,
              params: { key: resource.key, tab: "effective" },
              trailParam: "p",
              trail,
              nextCursor: state.data.page.next_cursor,
            })}
            page={state.data.page}
            subject="principals"
          />
        </>
      )}
    </EffectiveLayer>
  );
}

/* --------------------------------------------------------------------- helpers */

function tabsFor(resource: NtfsResourceDetailView): TabDefinition[] {
  const at = (tab: string): string => hrefWith(PATH, { key: resource.key, tab });
  return [
    {
      id: "acl",
      label: "NTFS ACL",
      href: at("acl"),
      hint: "Raw entries on this directory's descriptor, each marked explicit or inherited.",
    },
    {
      id: "effective",
      label: "Effective access",
      href: at("effective"),
      hint: "The engine's answer for every principal it can enumerate.",
    },
  ];
}

function NothingChosen(): JSX.Element {
  return (
    <>
      <h1>Directory</h1>
      <div className="card">
        <h2>Which directory?</h2>
        <p className="muted">
          This page needs a UNC path. Search for one, or follow a share down to the
          directory it publishes.
        </p>
        <GlobalSearch />
      </div>
    </>
  );
}

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
