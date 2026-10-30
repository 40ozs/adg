import type { Metadata } from "next";
import type { ReactNode } from "react";

import { documentTitle } from "@/lib/nav";

// Generic on purpose: a directory's name is its full path, which browser history must not keep.
export const metadata: Metadata = { title: documentTitle("Directory") };

export default function DirectoryLayout({ children }: { children: ReactNode }) {
  return children;
}
