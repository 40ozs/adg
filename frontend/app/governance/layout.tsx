import type { Metadata } from "next";
import type { ReactNode } from "react";

import { sectionTitle } from "@/lib/nav";

export const metadata: Metadata = { title: sectionTitle("governance") };

export default function GovernanceLayout({ children }: { children: ReactNode }) {
  return children;
}
