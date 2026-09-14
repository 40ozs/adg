import type { JSX } from "react";

import { EXPLAIN_PATH } from "@/lib/explanation";
import styles from "@/components/explanation.module.css";

/**
 * Asking the question.
 *
 * A plain `GET` form and no JavaScript: it submits on Enter, the answer is addressable by
 * URL afterwards, and an auditor can paste that URL into a ticket. A client-side navigator
 * would give up all three for nothing.
 *
 * Both fields are required because the API requires both. An access route that answers
 * about everybody when a parameter is omitted is the Cartesian product of the estate
 * wearing a query string, and this form must not look like one.
 */
export function ExplainLauncher({
  principal = "",
  resource = "",
  host = "",
  accessPath = "remote_smb",
}: {
  principal?: string;
  resource?: string;
  host?: string;
  accessPath?: string;
}): JSX.Element {
  return (
    <form className={styles.launcher} method="get" action={EXPLAIN_PATH}>
      <div className={styles.launcherField}>
        <label htmlFor="explain-principal">Principal</label>
        <input
          id="explain-principal"
          name="principal"
          required
          defaultValue={principal}
          placeholder="S-1-5-21-…-1104, or fs01|S-1-5-32-544 for a local group"
        />
        <span className="muted">
          The SID. A local group is only meaningful on one computer, so it is keyed by host as
          well.
        </span>
      </div>

      <div className={styles.launcherField}>
        <label htmlFor="explain-resource">Directory</label>
        <input
          id="explain-resource"
          name="resource"
          required
          defaultValue={resource}
          placeholder="\\FS01\Finance"
        />
        <span className="muted">The canonical UNC path of the directory, not the local path.</span>
      </div>

      <div className={styles.launcherField}>
        <label htmlFor="explain-host">Host (optional)</label>
        <input id="explain-host" name="host" defaultValue={host} placeholder="fs01" />
        <span className="muted">
          Scopes a bare SID to one computer, when the principal is a local group.
        </span>
      </div>

      <div className={styles.launcherField}>
        <label htmlFor="explain-access-path">Access path</label>
        <select id="explain-access-path" name="access_path" defaultValue={accessPath}>
          <option value="remote_smb">Over the network (share ACL and NTFS ACL)</option>
          <option value="local">On the console (NTFS ACL only)</option>
        </select>
        <span className="muted">
          Local access does not cross a share, so a share ACL that narrows remote access does
          not apply to it.
        </span>
      </div>

      <div>
        <button className="button" type="submit">
          Explain this access
        </button>
      </div>
    </form>
  );
}
