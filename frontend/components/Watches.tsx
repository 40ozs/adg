"use client";

import { useRouter } from "next/navigation";
import type { JSX } from "react";
import { useState } from "react";

import type { WatchKindView, WatchView } from "@/lib/contracts";
import { cooldownWording, triggerWording, triggersFor, watchKindWording } from "@/lib/alerts";

/**
 * Configuring what ADG watches.
 *
 * The one client component in this feature, because a form is a form. Two decisions in it
 * are worth reading rather than skimming:
 *
 * **The trigger list comes from the server**, per watch kind, and is not a table in this
 * file. A second copy of a rule is a second copy that can be wrong, and the wrong one is
 * always the one somebody trusts — a directory watch offered a membership trigger would be
 * saved, listed, and silent forever. The API refuses it; this never offers it.
 *
 * **Disabling is offered beside deleting, and described differently.** A disabled watch
 * still records what happens to the thing it covers, marked as suppressed because the watch
 * was off, so turning it back on shows what it missed. A deleted watch keeps nothing. Those
 * are different actions and a control that blurred them would lose a week of history to a
 * misclick.
 */

export function WatchManager({
  watches,
  kinds,
  defaultCooldownSeconds,
  canManage,
}: {
  watches: WatchView[];
  kinds: WatchKindView[];
  defaultCooldownSeconds: number;
  canManage: boolean;
}): JSX.Element {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function send(
    path: string,
    method: string,
    body: unknown,
    key: string,
  ): Promise<void> {
    setBusy(key);
    setError(null);
    try {
      const response = await fetch(path, {
        method,
        headers: { "Content-Type": "application/json" },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => ({}))) as { detail?: string };
        // The API's own message, unshortened. A 422 here names the triggers the kind
        // supports and a 409 names the watch that already exists, and both are what the
        // person is about to retype.
        setError(payload.detail ?? `Request failed with status ${response.status}.`);
        return;
      }
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card" aria-label="Watches">
      <h2>Watches</h2>
      <p className="muted">
        A watch is a standing subscription to one directory, share or group. Nothing is
        watched by default: ADG will not guess which parts of an estate matter to you.
      </p>

      {error !== null && (
        <p className="status-bad" role="alert">
          {error}
        </p>
      )}

      {watches.length === 0 ? (
        <p>
          No watches are configured, so the three change triggers produce nothing. Critical
          risk findings still raise alerts on their own — they do not need a watch.
        </p>
      ) : (
        <div className="table-scroll">
          <table className="acl-table">
            <caption className="visually-hidden">Configured watches</caption>
            <thead>
              <tr>
                <th scope="col">Label</th>
                <th scope="col">What</th>
                <th scope="col">Tell me about</th>
                <th scope="col">Quiet for</th>
                <th scope="col">State</th>
                {canManage && <th scope="col">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {watches.map((watch) => (
                <tr key={watch.watch_id}>
                  <td>{watch.label}</td>
                  <td>
                    <span className="badge">{watchKindWording(watch.kind)}</span>{" "}
                    <code>{watch.key}</code>
                  </td>
                  <td>
                    {watch.triggers.map((trigger) => (
                      <span key={trigger} className="badge">
                        {triggerWording(trigger)}
                      </span>
                    ))}
                  </td>
                  <td>{cooldownWording(watch.cooldown_seconds)}</td>
                  <td className={watch.enabled ? "status-ok" : "status-warn"}>
                    {watch.enabled ? "on" : "off"}
                  </td>
                  {canManage && (
                    <td>
                      <button
                        type="button"
                        className="button"
                        disabled={busy !== null}
                        onClick={() =>
                          send(
                            `/api/watches/${watch.watch_id}`,
                            "PATCH",
                            { enabled: !watch.enabled },
                            watch.watch_id,
                          )
                        }
                      >
                        {watch.enabled ? "Turn off" : "Turn on"}
                      </button>{" "}
                      <button
                        type="button"
                        className="button"
                        disabled={busy !== null}
                        title="Deletes the subscription. The alerts it raised are kept."
                        onClick={() =>
                          send(
                            `/api/watches/${watch.watch_id}`,
                            "DELETE",
                            undefined,
                            watch.watch_id,
                          )
                        }
                      >
                        Delete
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {canManage ? (
        <NewWatchForm
          kinds={kinds}
          defaultCooldownSeconds={defaultCooldownSeconds}
          busy={busy !== null}
          onCreate={(body) => send("/api/watches", "POST", body, "new")}
        />
      ) : (
        <p className="muted">
          Your account may read alerts and may not configure a watch. A watch decides who is
          told about a change, which is held separately on purpose.
        </p>
      )}
    </section>
  );
}

function NewWatchForm({
  kinds,
  defaultCooldownSeconds,
  busy,
  onCreate,
}: {
  kinds: WatchKindView[];
  defaultCooldownSeconds: number;
  busy: boolean;
  onCreate: (body: Record<string, unknown>) => void;
}): JSX.Element {
  const [kind, setKind] = useState(kinds[0]?.kind ?? "resource");
  const [key, setKey] = useState("");
  const [label, setLabel] = useState("");
  const [triggers, setTriggers] = useState<string[]>([]);

  const available = triggersFor(kinds, kind);
  // Changing the kind clears triggers the new kind cannot fire, rather than carrying them
  // over to be refused by the API. The refusal would be correct and would arrive after the
  // person had filled in the rest of the form.
  const selected = triggers.filter((trigger) => available.includes(trigger));

  return (
    <form
      onSubmit={(submitted) => {
        submitted.preventDefault();
        onCreate({
          kind,
          key,
          label,
          triggers: selected,
          cooldown_seconds: defaultCooldownSeconds,
        });
      }}
    >
      <h3>Watch something</h3>
      <p>
        <label htmlFor="watch-kind">What kind</label>{" "}
        <select
          id="watch-kind"
          value={kind}
          onChange={(changed) => {
            setKind(changed.target.value);
            setTriggers([]);
          }}
        >
          {kinds.map((entry) => (
            <option key={entry.kind} value={entry.kind}>
              {watchKindWording(entry.kind)}
            </option>
          ))}
        </select>
      </p>
      <p className="muted">{kinds.find((entry) => entry.kind === kind)?.covers}</p>
      <p>
        <label htmlFor="watch-key">
          Its key — a UNC path for a directory, a share key, or a group&apos;s principal key
        </label>
        <br />
        <input
          id="watch-key"
          value={key}
          onChange={(changed) => setKey(changed.target.value)}
          size={50}
          required
        />
      </p>
      <p>
        <label htmlFor="watch-label">A name you will recognize in a feed</label>
        <br />
        <input
          id="watch-label"
          value={label}
          onChange={(changed) => setLabel(changed.target.value)}
          size={40}
          required
        />
      </p>
      <fieldset>
        <legend>Tell me about</legend>
        {available.map((trigger) => (
          <label key={trigger} style={{ display: "block" }}>
            <input
              type="checkbox"
              checked={selected.includes(trigger)}
              onChange={() =>
                setTriggers((current) =>
                  current.includes(trigger)
                    ? current.filter((item) => item !== trigger)
                    : [...current, trigger],
                )
              }
            />{" "}
            {triggerWording(trigger)}
          </label>
        ))}
      </fieldset>
      <p>
        <button
          type="submit"
          className="button"
          disabled={busy || selected.length === 0 || key === "" || label === ""}
        >
          Add watch
        </button>{" "}
        <span className="muted">
          Quiet for {cooldownWording(defaultCooldownSeconds)} after each notification.
          Occurrences inside that window are recorded and counted, never discarded.
        </span>
      </p>
    </form>
  );
}
