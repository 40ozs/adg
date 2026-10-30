import type { Metadata } from "next";
import type { ReactNode } from "react";

import { documentTitle } from "@/lib/nav";

// Generic on purpose: naming the identity would cost a read on every view switch (the
// layout reads it once per identity), and an unresolved identity's only name is its SID.
export const metadata: Metadata = { title: documentTitle("Identity") };

export default function PrincipalLayout({ children }: { children: ReactNode }) {
  return children;
}
