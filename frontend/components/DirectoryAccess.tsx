import type { JSX } from "react";
import Form from "next/form";
import Link from "next/link";

import type { AccessRootView, DirectoryAccessResponse } from "@/lib/contracts";
import {
  DIRECTORY_MODES,
  type DirectoryMode,
  directories,
  directoryAccessHref,
  exportHref,
} from "@/lib/directoryAccess";
import { directoryHref } from "@/lib/resources";
import { Identifier } from "@/components/Facts";
import { RootExceptions } from "@/components/RootExceptions";
import { SortableTable } from "@/components/SortableTable";

export interface DirectoryFilters {
  q?: string;
  access?: string;
  share?: string;
}

/**
 * Summary, Tree and All paths: three readings of one principal's directory access.
 *
 * Links, not buttons, so each mode has an address, Back returns to the last one, and the
 * principal — the path — never changes. Filters travel with the link to the summary only;
 * the tree and the full listing answer about everything.
 */
export function DirectoryModes({
  principalKey,
  current,
  filters,
}: {
  principalKey: string;
  current: DirectoryMode;
  filters: DirectoryFilters;
}): JSX.Element {
  return (
    <nav className="filter-strip" aria-label="Directory access views">
      {DIRECTORY_MODES.map((mode) => (
        <Link
          key={mode.id}
          href={directoryAccessHref(
            principalKey,
            mode.id === "summary" ? { ...filters } : { mode: mode.id },
          )}
          aria-current={mode.id === current ? "page" : undefined}
          title={mode.hint}
          prefetch={false}
        >
          {mode.label}
        </Link>
      ))}
    </nav>
  );
}

/** The CSV downloads. The full one is every evaluated directory, never only what is shown. */
export function DirectoryExports({ principalKey }: { principalKey: string }): JSX.Element {
  return (
    <p className="directory-exports">
      <a href={exportHref(principalKey, "summary")} download>
        Download summary (CSV)
      </a>
      {" · "}
      <a href={exportHref(principalKey, "paths")} download>
        Download every evaluated path (CSV)
      </a>
      <span className="muted">
        {" "}
        — the second is the complete set behind All paths, reachable or not.
      </span>
    </p>
  );
}

/**
 * The summary: one row per access root.
 *
 * A row says where an uninterrupted run of reachable directories begins, how many it
 * reaches, and how many places inside it the answer changes. It deliberately does not say
 * the run is uniform unless it is: a root with exceptions carries a "varies" marker
 * (titled "Permissions vary…") beside its rights, and the exceptions open on demand with
 * the rights breakdown.
 */
export function DirectoryAccessSummary({
  principalKey,
  data,
  filters,
  basePath,
}: {
  principalKey: string;
  data: DirectoryAccessResponse;
  filters: DirectoryFilters;
  basePath: string;
}): JSX.Element {
  const { totals } = data;
  const filtered = Boolean(filters.q || filters.access || filters.share);
  const wholeSet = !data.page.has_more && data.roots.length === data.matched;

  return (
    <>
      <div className="banner banner-info" role="status">
        <h2>
          {totals.roots.toLocaleString("en-US")} access{" "}
          {totals.roots === 1 ? "root covers" : "roots cover"}{" "}
          {directories(totals.reachable_directories)} this principal can reach
        </h2>
        <p>
          Across {totals.shares} {totals.shares === 1 ? "share" : "shares"} holding a reachable
          folder, with {totals.exceptions.toLocaleString("en-US")}{" "}
          {totals.exceptions === 1 ? "exception" : "exceptions"} — places beneath a root where
          the rights change, inheritance is broken, or access stops.
        </p>
        <p className="muted">
          A root&apos;s descendant count is the directories reached from it without
          interruption. It does not mean they all hold the root&apos;s rights: where they
          differ, the row says so.
          {totals.files_excluded > 0 &&
            ` ${totals.files_excluded} reachable file(s) are left to All paths and the full export.`}
        </p>
      </div>

      {!data.complete && (
        <div className="banner banner-warning" role="alert">
          <h2>This summary covers part of the estate</h2>
          <p>
            A bound was reached ({data.truncation.join(", ")}). The roots and counts are those of
            what was read, and more may exist. All paths pages through the full listing.
          </p>
        </div>
      )}

      <Form action={basePath} className="search-form directory-filters" role="search">
        <label htmlFor="directory-access-q" className="visually-hidden">
          Filter roots by server, share or folder
        </label>
        <input
          id="directory-access-q"
          type="search"
          name="q"
          defaultValue={filters.q}
          autoComplete="off"
          placeholder="Server, share or folder"
        />
        <label htmlFor="directory-access-level" className="visually-hidden">
          Effective access
        </label>
        <select id="directory-access-level" name="access" defaultValue={filters.access ?? ""}>
          <option value="">Any access</option>
          {data.access_levels.map((level) => (
            <option key={level} value={level}>
              {level}
            </option>
          ))}
        </select>
        {filters.share && <input type="hidden" name="share" value={filters.share} />}
        <button type="submit">Filter</button>
        {filtered && (
          <Link href={basePath} prefetch={false}>
            Clear filters
          </Link>
        )}
      </Form>
      {filters.share && (
        <p className="muted">
          Inside the share <code>{filters.share}</code> only.
        </p>
      )}

      <DirectoryExports principalKey={principalKey} />

      {data.matched === 0 ? (
        <p className="muted" role="status">
          No access root matches these filters. {totals.roots} root(s) exist without them.
        </p>
      ) : (
        <SortableTable
          className="access-table directory-summary"
          caption={
            wholeSet
              ? `${data.matched} access root(s)${filtered ? " matching the filters" : ""}. Sorting orders all of them.`
              : `Sorting orders the ${data.roots.length} roots on this page of ${data.matched}.`
          }
          rowHeader={1}
          columns={[
            { label: "Share" },
            { label: "Access root" },
            { label: "Effective access" },
            { label: "Reachable descendants", type: "number" },
            { label: "Exceptions", type: "number" },
          ]}
          rows={data.roots.map((root) => summaryRow(principalKey, root))}
        />
      )}
    </>
  );
}

function summaryRow(principalKey: string, root: AccessRootView) {
  const { directory, verdict } = root;
  const share = `\\\\${directory.server}\\${directory.share}`;
  const relative = directory.relative_path || "(share root)";
  return {
    key: directory.key,
    sort: [
      directory.share_key,
      directory.key,
      verdict.rights.primary || verdict.rights.label,
      root.reachable_descendants,
      root.exception_count,
    ],
    cells: [
      <Identifier key="share" value={share} />,
      <>
        <Link
          href={directoryHref(directory.key)}
          className="identifier"
          title={directory.path}
          prefetch={false}
        >
          {relative}
        </Link>
        <span className="visually-hidden"> — {root.starts_because_label}</span>
        {root.starts_because === "not_observed" && (
          <>
            {" "}
            <span className="badge" title={root.starts_because_label}>
              parent unscanned
            </span>
          </>
        )}
        {root.resumes_within && (
          <>
            {" "}
            <span
              className="badge badge-explicit"
              title={`Access resumes below ${root.resumes_below ?? "a folder without access"}, inside the run that starts at ${root.resumes_within}.`}
            >
              resumes
            </span>
          </>
        )}
      </>,
      <>
        <span>{verdict.rights.primary || verdict.rights.label}</span>{" "}
        <code title={verdict.rights.label}>{verdict.rights.mask}</code>
        {verdict.certainty !== "certain" && (
          <>
            {" "}
            <span className="badge" title={`The engine's certainty: ${verdict.certainty}.`}>
              {verdict.certainty.replace("_", " ")}
            </span>
          </>
        )}
        {!root.uniform && (
          <>
            {" "}
            <span
              className="badge badge-explicit"
              title="Permissions vary: not every reachable descendant holds these rights, or access stops somewhere beneath. Open the exceptions."
            >
              varies
            </span>
          </>
        )}
      </>,
      root.reachable_descendants.toLocaleString("en-US"),
      root.exception_count === 0 ? (
        <span className="muted">None</span>
      ) : (
        <RootExceptions
          principalKey={principalKey}
          rootKey={directory.key}
          rootName={relative}
          rootPath={directory.path}
          count={root.exception_count}
          reasons={root.reasons}
          breakdown={root.rights_breakdown.map((item) => ({
            label: item.rights.label,
            mask: item.rights.mask,
            directories: item.directories,
          }))}
        />
      ),
    ],
  };
}
