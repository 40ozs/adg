import type { Metadata } from "next";
import type { ReactNode } from "react";

import { sectionTitle } from "@/lib/nav";

export const metadata: Metadata = { title: sectionTitle("risks") };

export default function RisksLayout({ children }: { children: ReactNode }) {
  return children;
}
