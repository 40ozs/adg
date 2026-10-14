import type { JSX } from "react";
import Link from "next/link";

import type { AccessExplanationResponse, CausalPathView, InvestigationView, PrimaryRouteView } from "@/lib/derived";
import { certaintyNote, effectWording, explainHref, explanationWording, systemMapping } from "@/lib/explanation";
import {
  accessPathLabel,
  basisDetail,
  basisLabel,
  collectorLabel,
  contextNotes,
  formatInstant,
  impactLabel,
  impactSentence,
  investigationQuestion,
  investigationSummary,
  layerLabel,
  layerRightsWording,
  nextStepWording,
  openQuestions,
  otherRoutes,
  placementLabel,
  presenceWording,
  recordLabel,
  removalSentence,
  routeClassification,
  routeHops,
  routeKindLabel,
  SECTION,
  stateLabel,
  stateSymbol,
  stateTone,
  type Hop,
} from "@/lib/investigation";
import { PrincipalName } from "@/components/Identity";
import styles from "@/components/investigation.module.css";

/**
 * The guided access investigation (ADG-DEMO-02).
 *
 * The same answer `/access/explain` renders, read in the order an investigator asks about
 * it: the question, what ADG evaluated, the route that carries the access, where each hop
 * of it was observed, what ADG could not confirm and whether that matters, a summary, and
 * what to do next. Server components only. Every disclosure is a native `<details>`, so it
 * is keyboard-operable and announced by assistive technology without any script, and every
 * state is written out in words beside its color.
 */

export function InvestigationQuestion({
  explanation,
  synthetic,
}: {
  explanation: AccessExplanationResponse;
  synthetic: boolean;
}): JSX.Element {
  return (
    <section aria-labelledby={SECTION.question} className={styles.question}>
      <p className={styles.eyebrow}>Access investigation</p>
      <h1 id={SECTION.question}>{investigationQuestion(explanation)}</h1>
      {synthetic && (
        <p className="banner banner-info" role="note">
          Synthetic scenario. Every identity, group, folder and scan below is generated demo
          data for a fictional company; no directory, file server or real account is involved.
        </p>
      )}
      <p className="muted">
        One request to <code>GET /api/v1/access/explain</code>, computed by ADG&rsquo;s access
        engine over the collected state. This page words that answer; it computes nothing.{" "}
        <Link
          href={explainHref(explanation.subject.key, explanation.resource.path ?? explanation.resource.key, {
            access_path: explanation.access_path,
          })}
        >
          Open the full explanation
        </Link>{" "}
        for the diagram, both ACLs and every removal.
      </p>
    </section>
  );
}

export function ObservedAccessPanel({
  explanation,
  investigation,
}: {
  explanation: AccessExplanationResponse;
  investigation: InvestigationView;
}): JSX.Element {
  const presence = presenceWording(investigation.presence, explanation);
  const wording = explanationWording(explanation);
  const mapping = systemMapping(explanation);
  const basis = explanation.basis;
  return (
    <section className="card" aria-labelledby={SECTION.observed}>
      <h2 id={SECTION.observed}>1. What ADG evaluated</h2>
      <p className="muted">The observed answer, before any explanation of it.</p>
      <dl className={styles.facts}>
        <div>
          <dt>Principal</dt>
          <dd>
            <PrincipalName principal={explanation.subject} />
          </dd>
        </div>
        <div>
          <dt>Resource</dt>
          <dd>
            <code>{explanation.resource.path ?? explanation.resource.key}</code>
            {explanation.share !== null && (
              <span className="muted"> · share {explanation.share.name ?? explanation.share.key}</span>
            )}
          </dd>
        </div>
        <div>
          <dt>Effective access</dt>
          <dd>
            <span className={`verdict verdict-${presence.tone}`}>{presence.word}</span>
            <span>{wording.headline}</span>
          </dd>
        </div>
        <div>
          <dt>Effective rights</dt>
          <dd>{explanation.verdict.conclusive ? explanation.effective.rights.label : "None established"}</dd>
        </div>
        <div>
          <dt>Certainty</dt>
          <dd>
            <code>{explanation.verdict.certainty}</code> — {certaintyNote(explanation.verdict.certainty)}
          </dd>
        </div>
        {mapping !== null && (
          <div>
            <dt>SYSTEM mapping</dt>
            <dd>
              <code>{mapping.state}</code> — {mapping.message}
            </dd>
          </div>
        )}
        <div>
          <dt>Access path</dt>
          <dd>{accessPathLabel(explanation.access_path)}</dd>
        </div>
        <div>
          <dt>Each ACL</dt>
          <dd>
            NTFS: {layerRightsWording(explanation.effective.ntfs.rights, explanation.verdict.conclusive)}
            {explanation.effective.share !== null && (
              <> · share: {layerRightsWording(explanation.effective.share.rights, explanation.verdict.conclusive)}</>
            )}
            <span className="muted"> · narrower layer: {explanation.effective.limiting_layer}</span>
          </dd>
        </div>
        <div>
          <dt>Collection</dt>
          <dd>
            {basis.is_empty
              ? "No collector run is on record."
              : `${basis.runs} collector runs on record; collected state ${basis.token}. Folder ACL ${explanation.effective.acl_provenance}.`}
          </dd>
        </div>
      </dl>
    </section>
  );
}

export function RoutesPanel({
  explanation,
  investigation,
}: {
  explanation: AccessExplanationResponse;
  investigation: InvestigationView;
}): JSX.Element {
  const others = otherRoutes(explanation, investigation);
  return (
    <section className="card" aria-labelledby={SECTION.routes}>
      <h2 id={SECTION.routes}>2. Why: the route that carries the access</h2>
      {investigation.primary_routes.length === 0 ? (
        <p>No route delivers a right that survives both ACLs, so there is no access path to show.</p>
      ) : (
        <>
          <p className="muted">
            {explanation.access_path === "local"
              ? "Local access crosses one ACL, so one route carries it."
              : "Over the network a right must survive both ACLs, so the access is carried by one route through each: the share and the folder."}{" "}
            Where several routes deliver the same rights, the one shown is the route whose entry
            names the most of the final answer.
          </p>
          {investigation.primary_routes.map((route) => (
            <PrimaryRoute key={route.path_id} explanation={explanation} route={route} />
          ))}
        </>
      )}
      {!investigation.routes_complete && (
        <p className="banner banner-warning" role="note">
          Not every route was enumerated. A stronger route than the one shown may exist.
        </p>
      )}
      {others.length > 0 && <OtherRoutes explanation={explanation} paths={others} />}
    </section>
  );
}

function PrimaryRoute({
  explanation,
  route,
}: {
  explanation: AccessExplanationResponse;
  route: PrimaryRouteView;
}): JSX.Element {
  const path = explanation.paths.find((item) => item.id === route.path_id);
  const headingId = `route-${route.path_id}`;
  return (
    <article className={styles.route} aria-labelledby={headingId}>
      <h3 id={headingId}>{layerLabel(route.layer)}</h3>
      <ul className={styles.tags} aria-label="How this route reaches the principal">
        <li className="badge badge-explicit">{routeKindLabel(route.route, route.membership_hops)}</li>
        <li className={route.placement === "inherited" ? "badge badge-inherited" : "badge badge-explicit"}>
          {placementLabel(route.placement, route.layer)}
        </li>
        <li className="badge">grants {route.coverage.label}</li>
      </ul>
      {path !== undefined && <HopList explanation={explanation} path={path} />}
      <p>
        {removalSentence(route)}
        {route.alternate_path_ids.length > 0 &&
          ` ${route.alternate_path_ids.length} other contributing route${route.alternate_path_ids.length === 1 ? "" : "s"} on this ACL.`}
      </p>
    </article>
  );
}

function HopList({
  explanation,
  path,
}: {
  explanation: AccessExplanationResponse;
  path: CausalPathView;
}): JSX.Element {
  const hops = routeHops(explanation, path);
  return (
    <ol className={styles.hops} aria-label={`Hops on route ${path.id}, principal first`}>
      {hops.map((hop, index) => (
        <li key={hop.edge.id} className={styles.hop}>
          <HopItem hop={hop} position={index + 1} />
        </li>
      ))}
    </ol>
  );
}

function HopItem({ hop, position }: { hop: Hop; position: number }): JSX.Element {
  const evidence = hop.evidence;
  return (
    <>
      <p className={styles.hopSentence}>
        <span className={styles.hopRelationship}>{hop.relationship}</span> {hop.sentence}
      </p>
      {hop.principal !== null && (
        <p className={styles.hopPrincipal}>
          <PrincipalName principal={hop.principal} />
        </p>
      )}
      <details className={styles.evidence}>
        <summary>
          Evidence for hop {position}: <strong>{basisLabel(evidence?.basis)}</strong>
          {evidence?.basis === "observed" && <> by the {collectorLabel(evidence.collector)}</>}
        </summary>
        <p className="muted">{basisDetail(evidence?.basis)}</p>
        {evidence?.basis === "observed" && (
          <dl className={styles.provenance}>
            <div>
              <dt>Collector</dt>
              <dd>
                {collectorLabel(evidence.collector)}
                {evidence.collection_method && <span className="muted"> ({evidence.collection_method})</span>}
              </dd>
            </div>
            <div>
              <dt>First observed</dt>
              <dd>
                {formatInstant(evidence.first_observed_at)} <span className="muted">run</span>{" "}
                <code>{evidence.first_observed_run_id}</code>
              </dd>
            </div>
            <div>
              <dt>Last observed</dt>
              <dd>
                {formatInstant(evidence.last_observed_at)} <span className="muted">run</span>{" "}
                <code>{evidence.last_observed_run_id}</code>
                {evidence.last_run_status && <span className="muted"> ({evidence.last_run_status})</span>}
              </dd>
            </div>
            <div>
              <dt>Stored as</dt>
              <dd>
                {recordLabel(evidence.record)} <code>{evidence.record_key}</code>
              </dd>
            </div>
          </dl>
        )}
      </details>
    </>
  );
}

function OtherRoutes({
  explanation,
  paths,
}: {
  explanation: AccessExplanationResponse;
  paths: CausalPathView[];
}): JSX.Element {
  return (
    <details className={styles.others}>
      <summary>
        Every other route ADG found ({paths.length}) — redundant, constrained or alternative
      </summary>
      <p className="muted">
        A route that matched is not necessarily a cause. These reach an entry too; their worth
        is the engine&rsquo;s, measured against the entries ahead of them and the other ACL.
      </p>
      <ul className={styles.otherList}>
        {paths.map((path) => {
          const classification = routeClassification(path);
          const effect = effectWording(path.effect);
          return (
            <li key={path.id}>
              <details>
                <summary>
                  {layerLabel(path.layer)}: {path.chain.map((member) => member.display_name ?? member.sid).join(" → ")}{" "}
                  — <span className={`verdict-${effect.tone}`}>{effect.label}</span>, {classification.reach},{" "}
                  {classification.placement}
                </summary>
                <p className="muted">{effect.detail}</p>
                <HopList explanation={explanation} path={path} />
              </details>
            </li>
          );
        })}
      </ul>
    </details>
  );
}

export function OpenQuestionsPanel({
  investigation,
}: {
  investigation: InvestigationView;
}): JSX.Element {
  const open = openQuestions(investigation);
  const context = contextNotes(investigation);
  return (
    <section className="card" aria-labelledby={SECTION.openQuestions}>
      <h2 id={SECTION.openQuestions}>3. What ADG could not confirm</h2>
      {open.length === 0 ? (
        <p>Nothing that could change this answer: every input it depends on was collected.</p>
      ) : (
        <ul className={styles.questions}>
          {open.map((item, index) => (
            <li key={`${item.condition}-${index}`} className={styles.questionItem}>
              <p>
                <span className={`${styles.state} ${styles[`state_${stateTone(item.state)}`]}`}>
                  <span aria-hidden="true">{stateSymbol(item.state)} </span>
                  {stateLabel(item.state)}
                </span>{" "}
                {item.about !== null && <PrincipalName principal={item.about} />}
              </p>
              <p>{item.message}</p>
              <p>
                <strong>{impactLabel(item.impact)}.</strong> {impactSentence(item.impact, investigation.presence)}
              </p>
            </li>
          ))}
        </ul>
      )}
      {context.length > 0 && (
        <details>
          <summary>Recorded for context ({context.length})</summary>
          <ul>
            {context.map((item, index) => (
              <li key={`${item.condition}-${index}`}>
                <span className={styles.state}>
                  <span aria-hidden="true">{stateSymbol(item.state)} </span>
                  {stateLabel(item.state)}
                </span>{" "}
                {item.message} <span className="muted">{impactSentence(item.impact, investigation.presence)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

export function SummaryPanel({
  explanation,
  investigation,
}: {
  explanation: AccessExplanationResponse;
  investigation: InvestigationView;
}): JSX.Element {
  return (
    <section className="card" aria-labelledby={SECTION.summary}>
      <h2 id={SECTION.summary}>4. Investigation summary</h2>
      <dl className={styles.summary}>
        {investigationSummary(explanation, investigation).map((item) => (
          <div key={item.question}>
            <dt>{item.question}</dt>
            {item.answer.map((line) => (
              <dd key={line}>{line}</dd>
            ))}
          </div>
        ))}
      </dl>
    </section>
  );
}

export function NextStepsPanel({
  explanation,
  investigation,
}: {
  explanation: AccessExplanationResponse;
  investigation: InvestigationView;
}): JSX.Element {
  return (
    <section className="card" aria-labelledby={SECTION.nextSteps}>
      <h2 id={SECTION.nextSteps}>5. What to inspect next</h2>
      <ol className={styles.steps}>
        {investigation.next_steps.map((step, index) => {
          const wording = nextStepWording(step, explanation, investigation);
          return (
            <li key={`${step.code}-${index}`}>
              {wording.text}
              {wording.href !== null && (
                <>
                  {" "}
                  <Link href={wording.href}>{wording.linkLabel}</Link>
                </>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/** For a body without `investigation` (a contract 1.0 server): say so, and link onward. */
export function InvestigationUnavailable({
  explanation,
}: {
  explanation: AccessExplanationResponse;
}): JSX.Element {
  return (
    <p className="banner banner-warning" role="note">
      This server answered with contract {explanation.schema_version}, which carries no
      investigation summary. The{" "}
      <Link href={explainHref(explanation.subject.key, explanation.resource.path ?? explanation.resource.key)}>
        full explanation
      </Link>{" "}
      has every route and finding.
    </p>
  );
}
