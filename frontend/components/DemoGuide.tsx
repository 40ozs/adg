import type { JSX } from "react";
import Link from "next/link";

import type {
  DemoGeneration,
  DemoInvestigation,
  DemoOverview,
  DemoPolicyEntry,
  DemoScenario,
} from "@/lib/contracts";
import { explainHref } from "@/lib/explanation";
import { principalHrefForKey } from "@/lib/identity";
import { investigateHref, SECTION } from "@/lib/investigation";
import { directoryHref } from "@/lib/resources";

/**
 * The demo guide's parts. Every link points into the real application — the directory page,
 * the principal page, the access explanation — so a scenario is something to inspect with
 * ADG itself, not a description of what ADG would show. Nothing here is a fixture: the
 * scenarios, generations and policy all come from `GET /api/v1/demo`.
 */

export function DemoScenarioList({ scenarios }: { scenarios: DemoScenario[] }): JSX.Element {
  if (scenarios.length === 0) {
    return <p className="muted">This dataset defines no guided scenarios.</p>;
  }
  return (
    <ol className="demo-scenarios">
      {scenarios.map((scenario) => (
        <li key={scenario.key} className="card demo-scenario" aria-label={`Scenario ${scenario.key}`}>
          <h3>
            <span className="demo-scenario-key">{scenario.key}</span> {scenario.title}
          </h3>
          <p>{scenario.summary}</p>
          <p>
            Folder: <Link href={directoryHref(scenario.resource)}>{scenario.resource}</Link>
            {scenario.related_resources.map((related) => (
              <span key={related}>
                {" · "}
                <Link href={directoryHref(related)}>{related}</Link>
              </span>
            ))}
          </p>
          {scenario.subjects.length > 0 && (
            <ul className="demo-scenario-subjects">
              {scenario.subjects.map((subject) => (
                <li key={subject.sid}>
                  <Link href={principalHrefForKey(subject.sid)}>{subject.name}</Link>{" "}
                  <span className="muted">({subject.kind})</span>
                  {subject.kind === "person" && (
                    <>
                      {" — "}
                      <Link href={explainHref(subject.sid, scenario.resource)}>
                        explain their access to this folder
                      </Link>
                      {" · "}
                      <Link href={investigateHref(subject.sid, scenario.resource)}>
                        investigate it step by step
                      </Link>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ol>
  );
}

/**
 * The reviewer's five minutes, in order. Each step names the section of the investigation
 * page it is about and links straight to it, so the walkthrough and the page cannot drift
 * into describing different screens.
 */
export const WALKTHROUGH: readonly { step: string; section: string | null }[] = [
  { step: "Open the seeded investigation with “Investigate access” above.", section: null },
  { step: "Read the access question at the top of the page.", section: SECTION.question },
  {
    step: "Inspect what ADG evaluated: the effective access, its certainty, and both ACLs.",
    section: SECTION.observed,
  },
  {
    step: "Read the route that carries the access on each ACL — direct entry or group membership, explicit or inherited.",
    section: SECTION.routes,
  },
  {
    step: "Open “Evidence for hop …” under any hop: which collector observed it, in which scan, and when.",
    section: SECTION.routes,
  },
  {
    step: "Inspect what ADG could not confirm, and whether it affects the conclusion.",
    section: SECTION.openQuestions,
  },
  { step: "Read the investigation summary: seven questions, seven answers.", section: SECTION.summary },
  { step: "Follow the suggested next investigative action.", section: SECTION.nextSteps },
];

export function DemoInvestigationList({
  investigations,
}: {
  investigations: DemoInvestigation[];
}): JSX.Element | null {
  if (investigations.length === 0) {
    return null;
  }
  return (
    <section aria-labelledby="demo-investigation" className="card demo-investigation">
      <h2 id="demo-investigation">Start here: a guided access investigation</h2>
      {investigations.map((item) => {
        const href = investigateHref(item.principal.sid, item.resource, {
          access_path: item.access_path,
        });
        return (
          <div key={item.key}>
            <h3>{item.title}</h3>
            <p>
              <strong>{item.question}</strong>
            </p>
            <p>{item.summary}</p>
            <p>
              <Link className="button" href={href}>
                Investigate access
              </Link>{" "}
              <span className="muted">
                Synthetic data · scenarios {item.scenarios.join(" and ")} below ·{" "}
                <Link href={principalHrefForKey(item.principal.sid)}>{item.principal.name}</Link> ·{" "}
                <Link href={directoryHref(item.resource)}>the folder</Link>
              </span>
            </p>
            <h4 id={`walkthrough-${item.key}`}>The five-minute walkthrough</h4>
            <ol className="demo-walkthrough" aria-labelledby={`walkthrough-${item.key}`}>
              {WALKTHROUGH.map((entry, index) => (
                <li key={entry.step}>
                  {entry.section === null || index === 0 ? (
                    entry.step
                  ) : (
                    <Link href={`${href}#${entry.section}`}>{entry.step}</Link>
                  )}
                </li>
              ))}
            </ol>
          </div>
        );
      })}
    </section>
  );
}

export function DemoGenerationList({
  generations,
  baseline,
}: {
  generations: DemoGeneration[];
  baseline: number;
}): JSX.Element {
  return (
    <table>
      <thead>
        <tr>
          <th>Scan generation</th>
          <th>Observed</th>
          <th>State</th>
        </tr>
      </thead>
      <tbody>
        {generations.map((generation) => (
          <tr key={generation.name}>
            <td>
              <code>{generation.name}</code>
              {generation.in_baseline ? "" : " (beyond the baseline)"}
            </td>
            <td>{generation.description}</td>
            <td className={generation.applied ? "status-ok" : "muted"}>
              {generation.applied ? "applied" : "not yet applied"}
            </td>
          </tr>
        ))}
      </tbody>
      <caption className="muted">
        A reset installs the first {baseline} of {generations.length}. Each generation is a set
        of collector-protocol runs posted through ADG&rsquo;s own ingestion routes.
      </caption>
    </table>
  );
}

const TREATMENT_LABEL: Record<DemoPolicyEntry["treatment"], string> = {
  supported: "Works as in production, on demo records",
  simulated: "Simulation only — the output is inert",
  unavailable: "Not available in the demo",
};

export function DemoPolicySummary({ policy }: { policy: DemoPolicyEntry[] }): JSX.Element {
  const treatments: DemoPolicyEntry["treatment"][] = ["unavailable", "simulated", "supported"];
  return (
    <dl className="demo-policy">
      {treatments.map((treatment) => {
        const entries = policy.filter((entry) => entry.treatment === treatment);
        return (
          <div key={treatment}>
            <dt>
              {TREATMENT_LABEL[treatment]} ({entries.length})
            </dt>
            <dd>
              <ul>
                {entries.map((entry) => (
                  <li key={`${entry.method} ${entry.path}`}>
                    {entry.operation} <code>{`${entry.method} ${entry.path}`}</code>
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/** The whole guide, for one overview. */
export function DemoGuide({ overview }: { overview: DemoOverview }): JSX.Element {
  const dataset = overview.dataset;
  return (
    <>
      <h1>Demo guide: {dataset.company_name}</h1>
      <p>{dataset.description}</p>
      <p className="muted">
        Every page in this environment is the real ADG application reading synthetic data for{" "}
        {dataset.company_name} (<code>{dataset.domain}</code>, snapshot {dataset.snapshot_date},
        seed <code>{dataset.seed}</code>). No Active Directory, file server or collector is
        connected, and nothing here can change real infrastructure.
      </p>

      <DemoInvestigationList investigations={overview.investigations ?? []} />

      <h2>Scenarios</h2>
      <DemoScenarioList scenarios={overview.scenarios} />

      <h2>Scan history</h2>
      <DemoGenerationList
        generations={overview.generations}
        baseline={overview.baseline_generations}
      />

      <h2>What the demo lets you change</h2>
      <DemoPolicySummary policy={overview.mutation_policy} />
    </>
  );
}
