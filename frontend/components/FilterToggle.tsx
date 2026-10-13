"use client";

import { useEffect, useRef, type JSX } from "react";

/**
 * A filter that applies the moment it is toggled: a checkbox that submits its form.
 *
 * The form is a `next/form` GET form, so the submission is a client-side navigation: the
 * filter goes into the URL beside the form's other fields (the search term), the document is
 * not reloaded, and the page position starts over, because a page of the unfiltered list is
 * not a page of the filtered one. `form` names the form by id, so the checkbox can sit outside
 * it; without JavaScript, the form's own button applies it.
 */
export function FilterToggle({
  form,
  name,
  checked,
  children,
}: {
  form: string;
  name: string;
  checked: boolean;
  children: React.ReactNode;
}): JSX.Element {
  const input = useRef<HTMLInputElement>(null);
  // Back and Forward change the URL, not this input: follow the value the page was rendered with.
  useEffect(() => {
    if (input.current) input.current.checked = checked;
  }, [checked]);

  return (
    <label className="filter-toggle">
      <input
        ref={input}
        type="checkbox"
        form={form}
        name={name}
        value="1"
        defaultChecked={checked}
        onChange={(event) => event.currentTarget.form?.requestSubmit()}
      />
      {children}
    </label>
  );
}
