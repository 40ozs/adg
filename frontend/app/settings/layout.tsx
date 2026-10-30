import type { Metadata } from "next";
import type { ReactNode } from "react";

import { sectionTitle } from "@/lib/nav";

export const metadata: Metadata = { title: sectionTitle("settings") };

export default function SettingsLayout({ children }: { children: ReactNode }) {
  return children;
}
