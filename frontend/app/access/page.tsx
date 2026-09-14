import { currentViewer } from "@/lib/auth/current";
import { GlobalSearch } from "@/components/GlobalSearch";
import { SignedOutNotice } from "@/components/SignedOutNotice";

/**
 * Access.
 *
 * An entry point, not a calculator. Every effective-access answer is computed by the backend
 * engine and carries its own certainty; nothing on this page combines share rights with NTFS
 * rights, expands a group, or decides what a deny means. Doing any of that in a browser
 * would produce a second, unverified implementation of the one thing this product is for.
 */
export default async function AccessPage() {
  const viewer = await currentViewer();
  if (viewer.status !== "signed-in") {
    return <SignedOutNotice viewer={viewer} />;
  }

  return (
    <>
      <h1>Access</h1>
      <p className="muted">
        Who can reach what, and why. Start from a principal or from a path.
      </p>

      <div className="card">
        <h2>Start from an identity or a path</h2>
        <GlobalSearch />
      </div>

      <div className="card">
        <h2>How ADG answers</h2>
        <p>
          An effective-access answer is an access check: the share ACL and the NTFS ACL are
          evaluated against a token built from the principal&apos;s memberships, in that order,
          and the narrower of the two wins. The answer carries its certainty — a directory
          whose descriptor no run has read is reported as unknown, never as &ldquo;nobody has
          access&rdquo;.
        </p>
        <p className="muted">
          This page never computes any of that. The engine lives in the backend
          (<code>app/access_engine/</code>) and is checked against the Windows
          <code> AuthzAccessCheck</code> API; a browser-side copy could only ever disagree
          with it.
        </p>
      </div>
    </>
  );
}
