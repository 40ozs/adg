import type { Metadata } from "next";

import { documentTitle } from "@/lib/nav";

// Next's built-in 404 renders its own <title> after the root layout's, so the tab kept
// reading "ADG". This page exists for the title; the words are Next's own.
export const metadata: Metadata = { title: documentTitle("Page not found") };

export default function NotFound() {
  return (
    <>
      <h1>Page not found</h1>
      <p className="muted">This page could not be found.</p>
    </>
  );
}
