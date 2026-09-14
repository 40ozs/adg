import type { JSX } from "react";
import Link from "next/link";

import { fetchAuthConfig } from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import { DevelopmentAuthBanner } from "@/components/Banners";
import { GlobalSearch } from "@/components/GlobalSearch";
import { PrimaryNav } from "@/components/PrimaryNav";
import { SignOutButton } from "@/components/SignOutButton";

/**
 * The application shell: the frame every page renders inside.
 *
 * It resolves the viewer once per request and hands the capability list down, so a page
 * never asks "who is this?" for itself. The order of the landmarks is the keyboard order:
 * a skip link, then the header (brand, search, account), then navigation, then main.
 *
 * Signed-out visitors still get the frame, without navigation. Replacing the whole shell
 * with a login form would throw away the one thing they need — the development-mode banner,
 * which has to be visible before anyone signs in, not after.
 */
export async function AppShell({ children }: { children: React.ReactNode }): Promise<JSX.Element> {
  const [viewer, config] = await Promise.all([currentViewer(), fetchAuthConfig()]);
  const capabilities = viewer.status === "signed-in" ? viewer.principal.capabilities : [];

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>

      {config.ok && <DevelopmentAuthBanner config={config.data} />}

      <header className="shell-header">
        <Link className="brand" href="/">
          ADG
          <small>Access auditing and governance</small>
        </Link>

        {viewer.status === "signed-in" && <GlobalSearch />}

        <div className="account">
          {viewer.status === "signed-in" ? (
            <>
              <span>
                {viewer.principal.display_name ?? viewer.principal.subject}
                <span className="visually-hidden"> is signed in</span>
              </span>
              {viewer.principal.roles.length === 0 ? (
                <span className="role-pill">no role</span>
              ) : (
                viewer.principal.roles.map((role) => (
                  <span key={role} className="role-pill">
                    {role}
                  </span>
                ))
              )}
              <SignOutButton />
            </>
          ) : viewer.status === "unavailable" ? (
            <span className="status-bad">API unavailable</span>
          ) : (
            <Link className="button" href="/login">
              Sign in
            </Link>
          )}
        </div>
      </header>

      <div className={viewer.status === "signed-in" ? "shell-body" : "shell-body shell-body-plain"}>
        {viewer.status === "signed-in" && <PrimaryNav capabilities={capabilities} />}
        <main className="shell-main" id="main" tabIndex={-1}>
          {children}
        </main>
      </div>

      <footer className="shell-footer">
        Read-only by default. ADG observes and explains access; it does not change
        permissions.
      </footer>
    </div>
  );
}
