import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = {
  title: "ADG",
  description: "Windows-domain share-access auditing and governance",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <header className="shell-header">
            <span className="brand">ADG</span>
            <nav>
              <Link href="/">Overview</Link> <Link href="/status">System status</Link>
            </nav>
          </header>
          <main className="shell-main">{children}</main>
          <footer className="shell-footer">
            Read-only by default. ADG observes and explains access; it does not change
            permissions.
          </footer>
        </div>
      </body>
    </html>
  );
}
