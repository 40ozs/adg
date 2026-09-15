import type { JSX } from "react";

import { fetchSimulationVocabulary } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { seedFromQuery } from "@/lib/simulation";
import { SignedOutNotice } from "@/components/SignedOutNotice";
import { SimulationEditor } from "@/components/SimulationEditor";

/**
 * Writing a proposal.
 *
 * The page is a thin server shell: it reads the seed off the query string — which is how a
 * `Simulate` action from a membership row or an ACE row arrives here — fetches the API's own
 * vocabulary, and hands both to the editor.
 *
 * The seed is read here rather than in the client component so that the editor opens with the
 * change already in it on the first render, with no flash of an empty form and no dependency
 * on JavaScript having run. The URL is the state, which is what makes a seeded link
 * pasteable into a ticket.
 */
export default async function NewSimulationPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<JSX.Element> {
  const [viewer, params] = await Promise.all([currentViewer(), searchParams]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  const vocabulary = await fetchSimulationVocabulary(viewer.session.accessToken);

  return (
    <>
      <h1>Propose a change</h1>
      <p className="muted">
        Describe a permission change and measure what it would do. Nothing on this page alters
        a permission.
      </p>
      <SimulationEditor
        seed={seedFromQuery(params)}
        vocabulary={vocabulary.ok ? vocabulary.data : null}
      />
    </>
  );
}
