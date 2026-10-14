"use client";

import { useCallback, useId, useRef, useState, type JSX, type KeyboardEvent } from "react";

import type { DirectoryTreeNodeView, DirectoryTreeResponse } from "@/lib/contracts";
import {
  BranchLoader,
  type BranchFetch,
  fetchBranchThroughProxy,
  reasonTone,
  verdictWord,
} from "@/lib/directoryAccess";

type Branch =
  | { status: "loading"; children: DirectoryTreeNodeView[] }
  | { status: "ready"; children: DirectoryTreeNodeView[]; nextCursor: string | null; total: number }
  | { status: "error"; children: DirectoryTreeNodeView[]; message: string };

/**
 * The directory-access tree, opened one folder at a time.
 *
 * The share roots arrive with the page (rendered on the server); everything below them is
 * fetched when, and only when, the reader opens it — one level per request, through the
 * proxy, kept for as long as the page is open. Collapsing a branch hides it without
 * forgetting it, so opening it again asks nothing. Nothing is prefetched.
 *
 * Each folder that can open has a real button carrying `aria-expanded` and naming the list
 * it controls; Enter and Space toggle it, and the arrow keys open (→) and close (←) it.
 * Access is spelled out in words beside every folder — never colour alone.
 */
export function DirectoryAccessTree({
  principalKey,
  initial,
  fetchBranch = fetchBranchThroughProxy,
}: {
  principalKey: string;
  initial: DirectoryTreeResponse;
  /** Injected by tests; the browser goes through the proxy. */
  fetchBranch?: BranchFetch;
}): JSX.Element {
  const loader = useRef<BranchLoader | null>(null);
  if (loader.current === null) {
    loader.current = new BranchLoader(principalKey, fetchBranch);
  }
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const [branches, setBranches] = useState<ReadonlyMap<string, Branch>>(() => new Map());
  const [announcement, setAnnouncement] = useState("");

  const load = useCallback(
    async (node: DirectoryTreeNodeView, cursor?: string | null) => {
      const key = node.directory.key;
      const loaderInstance = loader.current;
      if (loaderInstance === null) {
        return;
      }
      setBranches((current) => {
        const next = new Map(current);
        next.set(key, { status: "loading", children: current.get(key)?.children ?? [] });
        return next;
      });
      const result = await loaderInstance.load(key, cursor);
      setBranches((current) => {
        const next = new Map(current);
        const before = cursor ? (current.get(key)?.children ?? []) : [];
        if (result.ok) {
          next.set(key, {
            status: "ready",
            children: [...before, ...result.data.children],
            nextCursor: result.data.page.next_cursor,
            total: result.data.page.total ?? result.data.children.length,
          });
        } else {
          next.set(key, { status: "error", children: before, message: result.message });
        }
        return next;
      });
      setAnnouncement(
        result.ok
          ? `${node.directory.name}: ${result.data.children.length} folder(s) shown.`
          : `${node.directory.name}: ${result.message}`,
      );
    },
    [],
  );

  const toggle = useCallback(
    (node: DirectoryTreeNodeView, open?: boolean) => {
      const key = node.directory.key;
      const willOpen = open ?? !expanded.has(key);
      setExpanded((current) => {
        const next = new Set(current);
        if (willOpen) {
          next.add(key);
        } else {
          next.delete(key);
        }
        return next;
      });
      const branch = branches.get(key);
      if (willOpen && (branch === undefined || branch.status === "error")) {
        void load(node);
      }
    },
    [branches, expanded, load],
  );

  return (
    <div className="directory-tree">
      {initial.parent === null && initial.children.length === 0 ? (
        <p className="muted">No share holds a folder this principal can reach.</p>
      ) : (
        <ul className="tree-level" aria-label="Shares holding reachable folders">
          {initial.children.map((node) => (
            <TreeItem
              key={node.directory.key}
              node={node}
              top
              expanded={expanded}
              branches={branches}
              onToggle={toggle}
              onMore={load}
            />
          ))}
        </ul>
      )}
      <p className="visually-hidden" role="status" aria-live="polite">
        {announcement}
      </p>
    </div>
  );
}

function TreeItem({
  node,
  top = false,
  expanded,
  branches,
  onToggle,
  onMore,
}: {
  node: DirectoryTreeNodeView;
  top?: boolean;
  expanded: ReadonlySet<string>;
  branches: ReadonlyMap<string, Branch>;
  onToggle: (node: DirectoryTreeNodeView, open?: boolean) => void;
  onMore: (node: DirectoryTreeNodeView, cursor?: string | null) => Promise<void>;
}): JSX.Element {
  const listId = useId();
  const key = node.directory.key;
  const canOpen = node.child_count > 0;
  const open = canOpen && expanded.has(key);
  const branch = branches.get(key);
  const name = top ? `\\\\${node.directory.server}\\${node.directory.share}` : node.directory.name;

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>): void => {
    if (event.key === "ArrowRight" && !open) {
      event.preventDefault();
      onToggle(node, true);
    } else if (event.key === "ArrowLeft" && open) {
      event.preventDefault();
      onToggle(node, false);
    }
  };

  return (
    <li className={`tree-item${node.reachable ? " tree-reachable" : ""}`}>
      <div className="tree-node">
        {canOpen ? (
          <button
            type="button"
            className="tree-toggle"
            aria-expanded={open}
            aria-controls={listId}
            aria-label={`${open ? "Collapse" : "Expand"} ${name}`}
            onClick={() => onToggle(node)}
            onKeyDown={onKeyDown}
          >
            <span aria-hidden="true">{open ? "▾" : "▸"}</span>
          </button>
        ) : (
          <span className="tree-toggle tree-leaf" aria-hidden="true" />
        )}
        <code className="identifier" title={node.directory.path}>
          {name}
        </code>
        <span className={`tree-access${node.reachable ? "" : " muted"}`}>
          {verdictWord(node.verdict, node.named)}
        </span>
        {node.is_root && <span className="badge badge-allow">access root</span>}
        {node.reasons.map((reason) => (
          <span
            key={reason.reason}
            className={`badge ${reasonTone(reason.reason) === "stop" ? "badge-deny" : "badge-explicit"}`}
          >
            {reason.label}
          </span>
        ))}
        {node.directory.dacl_protected && !node.reasons.some((r) => r.reason === "inheritance_broken") && (
          <span className="badge badge-inherited">inheritance off</span>
        )}
        {node.reachable_below > 0 && (
          <span className="muted tree-count">
            {node.reachable_below.toLocaleString("en-US")} reachable below
          </span>
        )}
      </div>
      {open && (
        <ul className="tree-level" id={listId}>
          {branch?.children.map((child) => (
            <TreeItem
              key={child.directory.key}
              node={child}
              expanded={expanded}
              branches={branches}
              onToggle={onToggle}
              onMore={onMore}
            />
          ))}
          {branch?.status === "loading" && (
            <li className="muted" role="status">
              Loading…
            </li>
          )}
          {branch?.status === "error" && (
            <li className="status-bad" role="alert">
              {branch.message}{" "}
              <button type="button" className="button-link" onClick={() => void onMore(node)}>
                Try again
              </button>
            </li>
          )}
          {branch?.status === "ready" && branch.nextCursor && (
            <li>
              <button
                type="button"
                className="button-link"
                onClick={() => void onMore(node, branch.nextCursor)}
              >
                Show more ({branch.total - branch.children.length} not yet shown)
              </button>
            </li>
          )}
          {node.hidden_child_count > 0 && (
            <li className="muted tree-hidden">
              {node.hidden_child_count} other folder(s) without access, and nothing reachable
              beneath them, not shown
            </li>
          )}
        </ul>
      )}
    </li>
  );
}
