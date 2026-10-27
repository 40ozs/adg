import type { JSX } from "react";
import { redirect } from "next/navigation";

import { fetchPrincipalDetail } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { PRINCIPAL_VIEW_IDS, principalHrefForKey } from "@/lib/identity";
import { hrefWith } from "@/lib/paging";
import { GlobalSearch } from "@/components/GlobalSearch";
import { SignedOutNotice } from "@/components/SignedOutNotice";

/**
 * The earlier address of a principal, `?key=…&tab=…`, kept so that links pasted into
 * tickets still open the right principal.
 *
 * It redirects to `/identities/principal/{key}/{view}`, carrying the page position and the
 * member filter. `?host=` scoped a bare BUILTIN SID to one computer; the API resolves that
 * pair to the storage key, and the redirect goes to the key, which needs no host.
 */
export default async function LegacyPrincipalPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [viewer, params] = await Promise.all([currentViewer(), searchParams]);
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  let key = single(params.key)?.trim() ?? "";
  if (key === "") {
    return <NoIdentityChosen />;
  }
  const host = single(params.host)?.trim();
  if (host) {
    const detail = await fetchPrincipalDetail(viewer.session.accessToken, key, { host });
    // Unresolved, the key's own page says why, which is more than a redirect could.
    key = detail.ok ? detail.data.key : key;
  }
  const tab = single(params.tab);
  const view = tab !== undefined && PRINCIPAL_VIEW_IDS.includes(tab) ? tab : undefined;
  redirect(
    hrefWith(principalHrefForKey(key, view), {
      p: single(params.p),
      include: single(params.include),
    }),
  );
}

function NoIdentityChosen(): JSX.Element {
  return (
    <>
      <h1>Identity</h1>
      <div className="card">
        <h2>Which principal?</h2>
        <p className="muted">
          This page needs a SID or a host-scoped key. Search for one, or follow a trustee
          from an ACL.
        </p>
        <GlobalSearch />
      </div>
    </>
  );
}

/** Next.js hands a repeated query parameter over as an array; one value is expected here. */
function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
