"use client";

import { useRouter } from "next/navigation";
import type { JSX } from "react";
import { useEffect, useRef, useState } from "react";

/**
 * The global search box.
 *
 * A real `<form>` with a submit button, so it works on Enter, on click, and with assistive
 * technology, and so a result is a normal navigation with a URL somebody can bookmark or
 * paste into a ticket.
 *
 * `/` focuses it from anywhere, the shortcut every search field in every tool has — but
 * only when the user is not already typing somewhere. Stealing `/` from somebody entering a
 * UNC path into another field would be maddening, and UNC paths are full of separators.
 */
export function GlobalSearch({ initialQuery = "" }: { initialQuery?: string }): JSX.Element {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(initialQuery);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) {
        return;
      }
      const target = event.target as HTMLElement | null;
      const typing =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement ||
        target?.isContentEditable === true;
      if (typing) {
        return;
      }
      event.preventDefault();
      input.current?.focus();
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <form
      className="search-form"
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        const term = value.trim();
        if (term.length > 0) {
          router.push(`/search?q=${encodeURIComponent(term)}`);
        }
      }}
    >
      <label htmlFor="global-search" className="visually-hidden">
        Search identities, servers, shares, and directories
      </label>
      <input
        id="global-search"
        ref={input}
        type="search"
        name="q"
        value={value}
        autoComplete="off"
        placeholder="SID, name, or \\server\share"
        aria-describedby="global-search-hint"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          // Escape clears rather than closing anything: there is nothing to close, and a
          // half-typed term left behind is the commonest reason search "stops working".
          if (event.key === "Escape") {
            setValue("");
          }
        }}
      />
      <span id="global-search-hint" className="visually-hidden">
        Press the slash key from anywhere to focus this field.
      </span>
      <button type="submit">Search</button>
    </form>
  );
}
