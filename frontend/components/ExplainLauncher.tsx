import type { JSX } from "react";

import { EXPLAIN_PATH } from "@/lib/explanation";
import { INVESTIGATE_PATH } from "@/lib/investigation";
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
 *
 * `mode` picks where the same question goes: the full explanation, or the guided
 * investigation (ADG-DEMO-02). One request either way; only the reading order differs.
 */
export function ExplainLauncher({
  principal = "",
  resource = "",
  host = "",
  accessPath = "remote_smb",
  mode = "explain",
}: {
  principal?: string;
  resource?: string;
  host?: string;
  accessPath?: string;
  mode?: "explain" | "investigate";
}): JSX.Element {
  // Both launchers can sit on one page (/access), and an id must be unique in a document.
  const id = (field: string): string => `${mode}-${field}`;
  return (
    <form
      className={styles.launcher}
      method="get"
      action={mode === "investigate" ? INVESTIGATE_PATH : EXPLAIN_PATH}
    >
      <div className={styles.launcherField}>
        <label htmlFor={id("principal")}>Principal</label>
        <input
          id={id("principal")}
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
        <label htmlFor={id("resource")}>Directory</label>
        <input
          id={id("resource")}
          name="resource"
          required
          defaultValue={resource}
          placeholder="\\FS01\Finance"
        />
        <span className="muted">The canonical UNC path of the directory, not the local path.</span>
      </div>

      <div className={styles.launcherField}>
        <label htmlFor={id("host")}>Host (optional)</label>
        <input id={id("host")} name="host" defaultValue={host} placeholder="fs01" />
        <span className="muted">
          Scopes a bare SID to one computer, when the principal is a local group.
        </span>
      </div>

      <div className={styles.launcherField}>
        <label htmlFor={id("access-path")}>Access path</label>
        <select id={id("access-path")} name="access_path" defaultValue={accessPath}>
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
          {mode === "investigate" ? "Investigate this access" : "Explain this access"}
        </button>
      </div>
    </form>
  );
}
