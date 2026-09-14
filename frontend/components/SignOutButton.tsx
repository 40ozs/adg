"use client";

import { useRouter } from "next/navigation";
import type { JSX } from "react";
import { useState } from "react";

/**
 * Sign out.
 *
 * A POST rather than a link: a GET that ends a session can be triggered by any image tag on
 * any page, and being signed out by a third party's markup is a real annoyance even when it
 * is not a breach.
 */
export function SignOutButton(): JSX.Element {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  return (
    <button
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await fetch("/api/auth/logout", { method: "POST" });
          router.push("/login");
          // The shell is a server component; without this it would re-render from the
          // cache and keep showing the account that just signed out.
          router.refresh();
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? "Signing out…" : "Sign out"}
    </button>
  );
}
