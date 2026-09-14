import Link from "next/link";

export default function OverviewPage() {
  return (
    <>
      <h1>Access auditing and governance</h1>
      <p className="muted">
        ADG collects identity, share, and file-system permission facts from Windows domains
        and explains who can reach what, and why.
      </p>
      <div className="card">
        <h2>Current state</h2>
        <p>
          The project skeleton is in place. Identity, share, and NTFS collection arrive in
          later phases; nothing is collected yet.
        </p>
        <p>
          <Link href="/status">Check backend status</Link>
        </p>
      </div>
    </>
  );
}
