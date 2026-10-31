import type { Metadata } from "next";
import type { ReactNode } from "react";

import { sectionTitle } from "@/lib/nav";

export const metadata: Metadata = { title: sectionTitle("collectors") };

export default function CollectorsLayout({ children }: { children: ReactNode }) {
  return children;
}
