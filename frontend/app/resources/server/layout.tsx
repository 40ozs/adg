import type { Metadata } from "next";
import type { ReactNode } from "react";

import { documentTitle } from "@/lib/nav";

// Generic on purpose: naming the server would take a second read of it just for the title.
export const metadata: Metadata = { title: documentTitle("Server") };

export default function ServerLayout({ children }: { children: ReactNode }) {
  return children;
}
