import type { Metadata } from "next";
import type { ReactNode } from "react";

import { sectionTitle } from "@/lib/nav";

export const metadata: Metadata = { title: sectionTitle("resources") };

export default function ResourcesLayout({ children }: { children: ReactNode }) {
  return children;
}
