import type { Metadata } from "next";

import { AppShell } from "@/components/AppShell";

import "./globals.css";

export const metadata: Metadata = {
  title: "ADG",
  description: "Windows-domain share-access auditing and governance",
};

/**
 * Every page renders inside the shell, and the shell resolves the viewer server-side.
 *
 * `force-dynamic` because every page depends on the session cookie and on collection state.
 * A statically rendered shell would serve one user's identity to the next -- and would serve
 * a coverage banner from whenever the build ran, which is exactly the stale reassurance this
 * product must not give.
 */
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
