"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type JSX } from "react";

import {
  RECENT_KIND_LABEL,
  clearRecent,
  forgetRecent,
  readRecentFor,
  recentHref,
  recordRecent,
  sessionStore,
  type RecentEntry,
  type RecentKind,
} from "@/lib/recent";

import styles from "@/components/overview.module.css";

/**
 * Records one successful detail view. Rendered only where the page's primary entity read is
 * ok, so a prefetch, an error or a not-found never records: none of them mounts this.
 * Recording is idempotent per (kind, key), so a re-run effect only moves the entry to the front.
 */
export function RecordRecentView({
  owner,
  kind,
  entityKey,
  label,
}: {
  owner: string;
  kind: RecentKind;
  entityKey: string;
  label: string;
}): null {
  useEffect(() => {
    const storage = sessionStore();
    if (storage) recordRecent(storage, owner, { kind, key: entityKey, label });
  }, [owner, kind, entityKey, label]);
  return null;
}

/** Rendered on a detail page's not-found branch: the entity is gone, so its entry goes too. */
export function ForgetRecentView({
  owner,
  kind,
  entityKey,
}: {
  owner: string;
  kind: RecentKind;
  entityKey: string;
}): null {
  useEffect(() => {
    const storage = sessionStore();
    if (storage) forgetRecent(storage, owner, kind, entityKey);
  }, [owner, kind, entityKey]);
  return null;
}

type Shown = { entries: RecentEntry[]; removed: boolean } | "blocked" | null;

/**
 * The Overview's Recently viewed section.
 *
 * The server renders the heading and the description only: the list lives in this tab's
 * storage, so it is filled after hydration, below every streamed section, and never as a
 * server-rendered empty state that then changes its mind. The owner check and the capability
 * filter run before the first entry is set, so another account's list never renders.
 */
export function RecentlyViewed({
  owner,
  capabilities,
}: {
  owner: string;
  capabilities: readonly string[];
}): JSX.Element {
  const [shown, setShown] = useState<Shown>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const storage = sessionStore();
    // Storage is outside React; this effect is the read that syncs it in after hydration.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setShown(storage ? readRecentFor(storage, owner, capabilities) : "blocked");
  }, [owner, capabilities]);

  return (
    <section className="card" aria-labelledby="overview-recent">
      <h2 id="overview-recent" ref={heading} tabIndex={-1}>
        Recently viewed
      </h2>
      <p className="muted">Kept in this browser tab for this account only. Not sent to ADG.</p>
      {shown === "blocked" && (
        <p>Recently viewed isn&apos;t available in this browser because it blocks session storage.</p>
      )}
      {shown !== null && shown !== "blocked" && (
        <>
          {shown.removed && (
            <p className="muted">
              Some items were removed because this account can no longer open them.
            </p>
          )}
          {shown.entries.length === 0 ? (
            <p>
              Nothing yet. Identities, servers, shares and directories you open in this tab will
              appear here.
            </p>
          ) : (
            <>
              <ul className={styles.recent} aria-label="Recently viewed items">
                {shown.entries.map((entry) => (
                  <li key={`${entry.kind}|${entry.key}`}>
                    <span className="muted">{RECENT_KIND_LABEL[entry.kind]}</span>{" "}
                    <Link href={recentHref(entry)}>{entry.label}</Link>
                  </li>
                ))}
              </ul>
              <button
                type="button"
                onClick={() => {
                  clearRecent();
                  setShown({ entries: [], removed: false });
                  // The button goes with the list; keep focus in this section, not on <body>.
                  heading.current?.focus();
                }}
              >
                Clear
              </button>
            </>
          )}
        </>
      )}
    </section>
  );
}
