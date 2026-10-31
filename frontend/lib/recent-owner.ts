import "server-only";

import { createHash } from "node:crypto";

/**
 * Which account owns a tab's Recently viewed list, without writing the account down:
 * the hex SHA-256 of the `/auth/me` subject (ADG-UX-RECENT-05A §6). Not a secret, only an
 * exact owner check that keeps a subject, UPN or email out of browser storage.
 */
export function recentOwner(subject: string): string {
  return createHash("sha256").update(subject, "utf8").digest("hex");
}
