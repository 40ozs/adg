import type { Metadata } from "next";
import type { ReactNode } from "react";

import { sectionTitle } from "@/lib/nav";

export const metadata: Metadata = { title: sectionTitle("identities") };

export default function IdentitiesLayout({ children }: { children: ReactNode }) {
  return children;
}
