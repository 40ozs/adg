import type { Metadata } from "next";
import type { ReactNode } from "react";

import { sectionTitle } from "@/lib/nav";

export const metadata: Metadata = { title: sectionTitle("simulations") };

export default function SimulationsLayout({ children }: { children: ReactNode }) {
  return children;
}
