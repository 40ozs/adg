import type { JSX } from "react";
import Link from "next/link";

import { fetchChangeImpact } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type { AccessSideView } from "@/lib/contracts";
import { directionWording, impactWording, kindWording, windowWording } from "@/lib/changes";
import { hrefWith } from "@/lib/paging";
import { classify } from "@/lib/state";
import { StateMessage } from "@/components/Banners";
import { ChangeCard, ImpactCaveat } from "@/components/Changes";
import { SignedOutNotice } from "@/components/SignedOutNotice";

/**
 * Why access changed — resolved either side of one edit by the ordinary engine.
 *
 * The page exists because the feed answers a different question from the one an operator
 * actually has. The feed says an ACL broadened; this says whether anybody can now do
 * something they could not do before, and the two disagree often enough that reading the
 * first as the second is a real source of wasted incident response:
 *
 * - an Allow added below a Deny broadens the ACL and changes nobody's access;
 * - a share ACL that still caps what the file system grants makes an NTFS loosening inert;
 * - a user added to a group changes access to everything that group reaches, touching no
 *   ACL at all.
 *
 * So the page always shows **both directions side by side** — what the edit did, and what
 * effective access did — and says plainly when they differ. And when the answer is not
 * conclusive it says that too, because `neutral` plus `conclusive: false` looks exactly
 * like "nothing changed" and means "nothing could be established either side" (ADR-0016).
 */
export default async function ChangeImpactPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<JSX.Element> {
  const [viewer, params] = await Promise.all([currentViewer(), searchParams]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const kind = single(params.kind)?.trim() ?? "";
  const key = single(params.key)?.trim() ?? "";
  const at = single(params.at)?.trim() ?? "";
  const subject = single(params.subject)?.trim();
  const resource = single(params.resource)?.trim();
  const accessPath = single(params.access_path)?.trim();

  if (kind === "" || key === "" || at === "") {
    return <NothingChosen />;
  }

  const result = await fetchChangeImpact(viewer.session.accessToken, {
    kind,
    key,
    at,
    subject,
    resource,
    access_path: accessPath,
  });
  const state = classify(result, { isEmpty: () => false, subject: "impact" });

  return (
    <>
      <h1>Why access changed</h1>
      <p className="muted">
        {kindWording(kind)} · <code>{key}</code>
      </p>
      <p>
        <Link href={hrefWith("/changes", { kind, key })}>Full history of this object</Link> ·{" "}
        <Link href="/changes">Back to changes</Link>
      </p>

      {state.kind !== "ready" ? (
        <StateMessage state={state} />
      ) : (
        <>
          <div className="card">
            <h2>{impactWording(state.data.verdict).label}</h2>
            <p>{state.data.explanation}</p>
            {state.data.verdict !== "resolved" && (
              <p className="muted">{impactWording(state.data.verdict).explanation}</p>
            )}
            <p className="muted">
              Resolved as of {state.data.at_before} and {state.data.at_after}. The later
              instant is when the scan that recorded this change finished, not when the entry
              appeared: a scan opens what it found at the moment it looked and closes what it
              did not find when it completed, so any instant between the two shows both.
            </p>
          </div>

          {state.data.access !== null && (
            <section className="card">
              <h2>Effective access</h2>
              <p>
                <code>{state.data.access.subject_key}</code> on{" "}
                <code>{state.data.access.resource_key}</code>, over{" "}
                {state.data.access.access_path}.
              </p>
              <p className="verdict-note">
                The edit: <strong>{directionWording(state.data.change.direction)}</strong>.
                Effective access: <strong>{directionWording(state.data.access.direction)}</strong>.
              </p>
              {state.data.change.direction !== state.data.access.direction && (
                <div className="banner banner-info" role="status">
                  <p>
                    These disagree, and that is the answer rather than a contradiction. The
                    edit describes what the entry grants; effective access is what survives
                    the other layer, any Deny entry, and the principal&apos;s own state.
                  </p>
                </div>
              )}
              <ImpactCaveat
                direction={state.data.access.direction}
                conclusive={state.data.access.conclusive}
              />
              <div className="table-scroll">
                <table className="access-table">
                  <caption>Before and after</caption>
                  <thead>
                    <tr>
                      <th scope="col" />
                      <th scope="col">Before</th>
                      <th scope="col">After</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <th scope="row">Answer</th>
                      <Answer side={state.data.access.before} />
                      <Answer side={state.data.access.after} />
                    </tr>
                    <tr>
                      <th scope="row">Rights</th>
                      <td>
                        <code>{state.data.access.before.rights.mask}</code>{" "}
                        {state.data.access.before.rights.label}
                      </td>
                      <td>
                        <code>{state.data.access.after.rights.mask}</code>{" "}
                        {state.data.access.after.rights.label}
                      </td>
                    </tr>
                    <tr>
                      <th scope="row">Certainty</th>
                      <td>{state.data.access.before.certainty}</td>
                      <td>{state.data.access.after.certainty}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p>
                Gained <code>{state.data.access.gained.mask}</code>, lost{" "}
                <code>{state.data.access.lost.mask}</code>. The mask is the answer; the label
                beside it is a rendering, and SMB <em>Change</em> and NTFS <em>Modify</em> are
                the same bits under two names.
              </p>
            </section>
          )}

          {state.data.membership !== null && (
            <section className="card">
              <h2>Groups reached</h2>
              <p>
                <code>{state.data.membership.subject_key}</code> reached{" "}
                {state.data.membership.before_count} groups before and{" "}
                {state.data.membership.after_count} after.
              </p>
              <KeyList heading="Gained" keys={state.data.membership.gained} />
              <KeyList heading="Lost" keys={state.data.membership.lost} />
              <p className="muted">Certainty: {state.data.membership.certainty}.</p>
            </section>
          )}

          <section className="card">
            <h2>The change itself</h2>
            <p>{windowWording(state.data.change.window)}</p>
            <ChangeCard
              change={state.data.change}
              edits={[]}
              impactHref={null}
              timelineHref={() => hrefWith("/changes", { kind, key })}
            />
          </section>
        </>
      )}
    </>
  );
}

function Answer({ side }: { side: AccessSideView }): JSX.Element {
  const tone =
    side.outcome === "granted" ? "bad" : side.outcome === "indeterminate" ? "warn" : "ok";
  return (
    <td>
      <span className={`verdict verdict-${tone === "bad" ? "bad" : tone === "warn" ? "info" : "ok"}`}>
        {side.outcome}
      </span>
      <br />
      <span className="muted">{side.reason}</span>
    </td>
  );
}

function KeyList({ heading, keys }: { heading: string; keys: readonly string[] }): JSX.Element {
  return (
    <div className="acl-group">
      <h3>{heading}</h3>
      {keys.length === 0 ? (
        <p className="muted">None.</p>
      ) : (
        <ul>
          {keys.map((key) => (
            <li key={key}>
              <code>{key}</code>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function NothingChosen(): JSX.Element {
  return (
    <>
      <h1>Why access changed</h1>
      <div className="card">
        <h2>Pick a change first</h2>
        <p>
          This page resolves effective access either side of one change, so it needs the
          change: its kind, its key, and the instant its resulting version opened.{" "}
          <Link href="/changes">Start from the change list.</Link>
        </p>
      </div>
    </>
  );
}

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
