import type { Metadata } from "next";
import type { ReactNode } from "react";

import { documentTitle } from "@/lib/nav";

// Generic on purpose: a share's name is its UNC path, which browser history must not keep.
export const metadata: Metadata = { title: documentTitle("Share") };

export default function ShareLayout({ children }: { children: ReactNode }) {
  return children;
}
