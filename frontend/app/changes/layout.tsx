import type { Metadata } from "next";
import type { ReactNode } from "react";

import { sectionTitle } from "@/lib/nav";

export const metadata: Metadata = { title: sectionTitle("changes") };

export default function ChangesLayout({ children }: { children: ReactNode }) {
  return children;
}
