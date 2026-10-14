"use client";

import { useCallback, useState, type JSX, type SyntheticEvent } from "react";

import type { AccessExceptionView, DirectoryExceptionsResponse, ReasonCountView } from "@/lib/contracts";
import { exceptionsUrl, reasonTone, verdictWord } from "@/lib/directoryAccess";

type Loaded =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; items: AccessExceptionView[]; total: number }
  | { status: "error"; message: string };

/**
 * One root's exceptions, opened on demand.
 *
 * A native `<details>`: the browser supplies Enter and Space, and the expanded state
 * assistive technology announces. The list is fetched the first time it opens and kept
 * while the page is open, so folding and unfolding it asks the API nothing more.
 */
export function RootExceptions({
  principalKey,
  rootKey,
  rootName,
  rootPath,
  count,
  reasons,
  breakdown,
  fetcher = fetchExceptions,
}: {
  principalKey: string;
  rootKey: string;
  rootName: string;
  /** The root's full path, so each exception can be shown from the root down. */
  rootPath: string;
  count: number;
  reasons: readonly ReasonCountView[];
  breakdown: readonly { label: string; mask: string; directories: number }[];
  /** Injected by tests; the browser goes through the proxy. */
  fetcher?: (url: string) => Promise<DirectoryExceptionsResponse>;
}): JSX.Element {
  const [loaded, setLoaded] = useState<Loaded>({ status: "idle" });

  const onToggle = useCallback(
    (event: SyntheticEvent<HTMLDetailsElement>) => {
      if (!event.currentTarget.open || loaded.status === "loading" || loaded.status === "ready") {
        return;
      }
      setLoaded({ status: "loading" });
      fetcher(exceptionsUrl(principalKey, rootKey)).then(
        (body) => setLoaded({ status: "ready", items: body.items, total: body.page.total ?? body.items.length }),
        (error: unknown) =>
          setLoaded({
            status: "error",
            message: error instanceof Error ? error.message : "The exceptions could not be loaded.",
          }),
      );
    },
    [fetcher, loaded.status, principalKey, rootKey],
  );

  const summary = reasons.map((reason) => `${reason.label}: ${reason.count}`).join(", ");
  return (
    <details className="root-exceptions" onToggle={onToggle}>
      <summary title={summary} aria-label={`${count} exception${count === 1 ? "" : "s"} under ${rootName}: ${summary}`}>
        {count.toLocaleString("en-US")}
      </summary>
      <div className="exceptions-panel">
        <p className="muted">{summary}.</p>
        {breakdown.length > 1 && (
          <p>
            Rights across this run:{" "}
            {breakdown.map((item, index) => (
              <span key={item.mask}>
                {index > 0 && ", "}
                {item.label} <code>{item.mask}</code> × {item.directories}
              </span>
            ))}
          </p>
        )}
        {loaded.status === "loading" && <p role="status">Loading exceptions…</p>}
        {loaded.status === "error" && (
          <p className="status-bad" role="alert">
            {loaded.message}
          </p>
        )}
        {loaded.status === "ready" && (
          <ul className="exception-list">
            {loaded.items.map((item) => (
              <li key={item.directory.key}>
                <code className="identifier" title={item.directory.path}>
                  {belowRoot(item.directory.path, rootPath)}
                </code>{" "}
                {item.reasons.map((reason) => (
                  <span
                    key={reason.reason}
                    className={`badge ${reasonTone(reason.reason) === "stop" ? "badge-deny" : "badge-explicit"}`}
                  >
                    {reason.label}
                  </span>
                ))}{" "}
                <span>
                  {verdictWord(item.verdict, item.named)}
                  {item.verdict?.access && (
                    <>
                      {" "}
                      <code>{item.verdict.rights.mask}</code>
                    </>
                  )}
                </span>
                <span className="muted">
                  {" "}
                  (parent: {verdictWord(item.parent_verdict, true)})
                  {!item.named && " — nothing on its ACL names this principal or its groups"}
                </span>
              </li>
            ))}
            {loaded.total > loaded.items.length && (
              <li className="muted">
                {loaded.total - loaded.items.length} more in the full export.
              </li>
            )}
          </ul>
        )}
      </div>
    </details>
  );
}

/** A path as seen from the root (`…\\FY2026\\Q1`); the full path stays in the title. */
export function belowRoot(path: string, rootPath: string): string {
  const prefix = `${rootPath}\\`;
  return path.toLowerCase().startsWith(prefix.toLowerCase())
    ? `…\\${path.slice(prefix.length)}`
    : path;
}

async function fetchExceptions(url: string): Promise<DirectoryExceptionsResponse> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const body = (await response.json().catch(() => null)) as
    | DirectoryExceptionsResponse
    | { detail?: string }
    | null;
  if (!response.ok || body === null || !("items" in body)) {
    const detail = body && "detail" in body ? body.detail : undefined;
    throw new Error(detail ?? `The exceptions could not be loaded (HTTP ${response.status}).`);
  }
  return body;
}
