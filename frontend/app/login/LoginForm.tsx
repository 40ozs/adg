"use client";

import { useRouter } from "next/navigation";
import type { JSX } from "react";
import { useState } from "react";

/**
 * The development-mode account picker.
 *
 * A list of accounts with a button each, and no password field. A password field here would
 * be a lie about the boundary: development mode verifies no credential, and a form that
 * looked like a real sign-in would invite somebody to believe it was one.
 */
export function DevelopmentLoginForm({ accounts }: { accounts: string[] }): JSX.Element {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function signIn(username: string): Promise<void> {
    setBusy(username);
    setError(null);
    try {
      const response = await fetch("/api/auth/dev-login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { error?: string };
        setError(body.error ?? `Sign-in failed with status ${response.status}.`);
        return;
      }
      router.push("/");
      // The shell is a server component and would otherwise re-render from cache, still
      // showing the signed-out header.
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  if (accounts.length === 0) {
    return (
      <p className="muted">
        No development accounts are configured. Set <code>ADG_DEV_AUTH_USERS</code> on the
        API.
      </p>
    );
  }

  return (
    <>
      {error !== null && (
        <p className="status-bad" role="alert">
          {error}
        </p>
      )}
      <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: "0.5rem" }}>
        {accounts.map((account) => (
          <li key={account}>
            <button type="button" disabled={busy !== null} onClick={() => void signIn(account)}>
              {busy === account ? `Signing in as ${account}…` : `Sign in as ${account}`}
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}
