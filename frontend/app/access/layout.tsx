import type { Metadata } from "next";
import type { ReactNode } from "react";

import { sectionTitle } from "@/lib/nav";

export const metadata: Metadata = { title: sectionTitle("access") };

export default function AccessLayout({ children }: { children: ReactNode }) {
  return children;
}
