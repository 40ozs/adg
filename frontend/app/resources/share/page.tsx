import type { JSX } from "react";
import Link from "next/link";

import { daclNotes, inheritanceNotes } from "@/lib/acl";
import {
  fetchCollectionStatus,
  fetchResourcePrincipals,
  fetchShare,
  fetchShareAcl,
  fetchShareRootAcl,
} from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type { CollectionStatus, ShareDetailView } from "@/lib/contracts";
import { cursorFor, decodeTrail, hrefWith, pageNavigation, type Trail } from "@/lib/paging";
import { directoryHref, serverHref } from "@/lib/resources";
import { classify, coverageCaveat } from "@/lib/state";
import { CoverageCaveat, StateMessage } from "@/components/Banners";
import {
  EnumerationNotice,
  FindingsNotice,
  PrincipalAccessTable,
} from "@/components/EffectiveAccess";
import { FactList, Maybe, NoteList, Provenance, TriState } from "@/components/Facts";
import { GlobalSearch } from "@/components/GlobalSearch";
import { EffectiveLayer, FactSection, RawLayer } from "@/components/Layer";
import { PagePosition, Pager } from "@/components/Pager";
import { NtfsAceTable, ShareAceTable } from "@/components/RawAcl";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { Tabs, type TabDefinition } from "@/components/Tabs";

const PATH = "/resources/share";

/**
 * One share: the publication, the directory beneath it, and who gets through both.
 *
 * The three sections are three different statements, and the page never lets them blur:
 *
 * - the **share ACL** is what SMB enforces at the connection;
 * - the **root NTFS ACL** is what the file system enforces on the directory it publishes;
 * - **effective access** is the engine crossing the two against a real token.
 *
 * A share ACL granting Full Control means nothing on its own — the NTFS layer below can
 * refuse everything, and usually does. That is why "Everyone / Full Control" on a share is
 * so commonly reported as a critical finding and so commonly is not one.
 */
export default async function SharePage({
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
    fetchShare(token, key),
  ]);
  const coverageData = coverage.ok ? coverage.data : null;

  if (!detail.ok) {
    return (
      <>
        <h1>Share</h1>
        <p className="muted">
          <code>{key}</code>
        </p>
        <StateMessage
          state={classify(detail, {
            isEmpty: () => false,
            coverage: coverageData,
            subject: "share",
          })}
        />
      </>
    );
  }

  const share = detail.data;
  const tabs = tabsFor(share);
  const current = tabs.find((tab) => tab.id === single(params.tab))?.id ?? tabs[0].id;
  const trail = decodeTrail(single(params.p));

  return (
    <>
      <nav className="breadcrumb" aria-label="Location">
        <ol>
          <li>
            <Link href="/resources">Servers</Link>
          </li>
          <li>
            <Link href={serverHref(share.server_key)}>{share.server?.name ?? share.server_key}</Link>
          </li>
          <li>
            <span aria-current="page">{share.name}</span>
          </li>
        </ol>
      </nav>

      <h1>
        {share.unc_path} <span className="title-kind">share</span>
      </h1>

      <CoverageCaveat caveat={coverageCaveat(coverageData)} />

      <Identity share={share} />

      <Tabs label="Share sections" tabs={tabs} current={current} />
      <PagePosition trail={trail} firstPageHref={hrefWith(PATH, { key: share.key, tab: current })} />

      {await panel({ share, tab: current, token, trail, coverage: coverageData })}
    </>
  );
}

function Identity({ share }: { share: ShareDetailView }): JSX.Element {
  const root = share.root_resource;
  return (
    <FactSection title="Share metadata" id="share-facts">
      <FactList
        facts={[
          { term: "Storage key", value: <code>{share.key}</code> },
          {
            term: "Server",
            value: (
              <Link href={serverHref(share.server_key)}>
                {share.server?.name ?? share.server_key}
              </Link>
            ),
          },
          { term: "Type", value: share.share_type },
          { term: "Description", value: <Maybe value={share.description} /> },
          { term: "Publishes", value: <Maybe value={share.local_path} /> },
          { term: "Hidden", value: share.is_hidden ? "yes — the name ends in $" : "no" },
          { term: "Special flag", value: <TriState value={share.is_special} /> },
          {
            term: "Administrative",
            value: share.is_administrative ? "yes — ADMIN$, IPC$, or a drive share" : "no",
          },
          {
            term: "NTFS layer beneath it",
            value: share.carries_file_permissions
              ? "yes — a disk share, so file-system permissions also apply"
              : "no — this share type has no file system under it",
          },
          { term: "Share-level entries", value: share.ace_count },
          { term: "Caching", value: <Maybe value={share.caching_mode} /> },
          { term: "Concurrent user limit", value: <Maybe value={share.concurrent_user_limit} /> },
          {
            term: "Root directory",
            value: root ? (
              <Link href={directoryHref(root.key)}>{root.path}</Link>
            ) : (
              <span className="status-warn">
                no file-system run has described the directory this share publishes
              </span>
            ),
          },
        ]}
      />

      {root && (
        <>
          <h3>Inheritance at the share root</h3>
          <NoteList notes={[...inheritanceNotes(root), ...daclNotes(root)]} />
        </>
      )}

      <h3>Last scan</h3>
      <Provenance provenance={share.provenance} />
    </FactSection>
  );
}

/* ------------------------------------------------------------------ the panels */

interface PanelOptions {
  share: ShareDetailView;
  tab: string;
  token: string;
  trail: Trail;
  coverage: CollectionStatus | null;
}

async function panel(options: PanelOptions): Promise<JSX.Element> {
  switch (options.tab) {
    case "root-acl":
      return rootAclPanel(options);
    case "effective":
      return effectivePanel(options);
    default:
      return shareAclPanel(options);
  }
}

async function shareAclPanel(options: PanelOptions): Promise<JSX.Element> {
  const { share, token, trail, coverage } = options;
  const result = await fetchShareAcl(token, share.key, { cursor: cursorFor(trail) });
  const state = classify(result, {
    isEmpty: (data) => data.entries.length === 0,
    coverage,
    subject: "share-level entries",
  });

  return (
    <RawLayer title="Share-level ACL" id="panel-share-acl" layer="SMB share">
      <StateMessage state={state} />
      {state.kind === "ready" && (
        <>
          <ShareAceTable entries={state.data.entries} shareKey={share.key} />
          <Pager
            navigation={pageNavigation({
              basePath: PATH,
              params: { key: share.key, tab: "share-acl" },
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

async function rootAclPanel(options: PanelOptions): Promise<JSX.Element> {
  const { share, token, trail, coverage } = options;
  const result = await fetchShareRootAcl(token, share.key, { cursor: cursorFor(trail) });
  const state = classify(result, {
    isEmpty: (data) => data.entries.length === 0,
    coverage,
    subject: "NTFS entries on the share root",
  });

  return (
    <RawLayer title="NTFS ACL of the directory this share publishes" id="panel-root-acl" layer="NTFS">
      <p className="muted">
        A different descriptor from the one above, on a different object, enforced by a
        different component. Remote access has to satisfy both.
      </p>
      <StateMessage state={state} />
      {state.kind === "ready" && (
        <>
          {state.data.resource && (
            <p className="muted">
              Directory: <Link href={directoryHref(state.data.resource.key)}>{state.data.resource.path}</Link>
            </p>
          )}
          <NtfsAceTable entries={state.data.entries} resourceKey={state.data.resource?.path} />
          <Pager
            navigation={pageNavigation({
              basePath: PATH,
              params: { key: share.key, tab: "root-acl" },
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
  const { share, token, trail, coverage } = options;
  const root = share.root_resource;

  if (root === null) {
    return (
      <EffectiveLayer title="Who can reach this share" id="panel-effective">
        <div className="banner banner-warning" role="alert">
          <h2>ADG cannot answer this yet</h2>
          <p>
            Effective access is the share ACL crossed with the NTFS ACL of the directory the
            share publishes, and no run has described that directory. Answering from the
            share ACL alone would report access the file system may refuse.
          </p>
          <p className="muted">
            <Link href="/collectors">Check whether an NTFS collector has run against this server.</Link>
          </p>
        </div>
      </EffectiveLayer>
    );
  }

  const result = await fetchResourcePrincipals(token, root.key, { cursor: cursorFor(trail) });
  const state = classify(result, {
    isEmpty: (data) => data.items.length === 0,
    coverage,
    subject: "principals with access",
  });

  return (
    <EffectiveLayer title="Who can reach this share" id="panel-effective">
      <p className="muted">
        Evaluated over remote SMB against{" "}
        <Link href={directoryHref(root.key)}>{root.path}</Link>, so both layers apply.
      </p>
      <StateMessage state={state} />
      {state.kind === "ready" && (
        <>
          <EnumerationNotice enumeration={state.data.enumeration} />
          <FindingsNotice findings={state.data.findings} />
          <PrincipalAccessTable items={state.data.items} explainOn={root.key} />
          <Pager
            navigation={pageNavigation({
              basePath: PATH,
              params: { key: share.key, tab: "effective" },
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

function tabsFor(share: ShareDetailView): TabDefinition[] {
  const at = (tab: string): string => hrefWith(PATH, { key: share.key, tab });
  return [
    {
      id: "share-acl",
      label: "Share ACL",
      href: at("share-acl"),
      hint: "Raw entries on the share itself. What SMB enforces at the connection.",
    },
    {
      id: "root-acl",
      label: "Root NTFS ACL",
      href: at("root-acl"),
      hint: "Raw entries on the directory the share publishes. A different layer.",
    },
    {
      id: "effective",
      label: "Effective access",
      href: at("effective"),
      hint: "The engine's answer: both layers crossed against a real token.",
    },
  ];
}

function NothingChosen(): JSX.Element {
  return (
    <>
      <h1>Share</h1>
      <div className="card">
        <h2>Which share?</h2>
        <p className="muted">
          This page needs a share key (<code>fs01|finance</code>) or its UNC path. Search
          for one, or open a server from <Link href="/resources">Resources</Link>.
        </p>
        <GlobalSearch />
      </div>
    </>
  );
}

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
