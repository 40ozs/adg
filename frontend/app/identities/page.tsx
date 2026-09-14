import { currentViewer } from "@/lib/auth/current";
import { GlobalSearch } from "@/components/GlobalSearch";
import { SignedOutNotice } from "@/components/SignedOutNotice";

/**
 * Identities.
 *
 * Deliberately a lookup rather than a listing. A domain's principal table is hundreds of
 * thousands of rows and scrolling it answers nothing; every real question here starts from a
 * name or a SID somebody already has, usually pasted out of an ACE.
 */
export default async function IdentitiesPage() {
  const viewer = await currentViewer();
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  return (
    <>
      <h1>Identities</h1>
      <p className="muted">
        Find a user or group by SID, display name, or account name. SID is ADG&apos;s identity;
        names are metadata, and a SID that no longer resolves still has the name the ACE
        carried.
      </p>

      <div className="card">
        <h2>Look up an identity</h2>
        <GlobalSearch />
        <p className="muted">
          A BUILTIN SID such as <code>S-1-5-32-544</code> exists separately on every computer
          that reported it, so a search for one may return several results, each scoped to
          its host.
        </p>
      </div>

      <div className="card">
        <h2>What ADG can tell you</h2>
        <dl className="facts">
          <dt>Membership</dt>
          <dd>Direct members, effective members, and every chain that puts one inside another.</dd>
          <dt>Reach</dt>
          <dd>The shares and directories a principal can reach, and by which grant.</dd>
          <dt>History of names</dt>
          <dd>Every name ever observed for a SID, with the window it was seen in.</dd>
        </dl>
      </div>
    </>
  );
}
