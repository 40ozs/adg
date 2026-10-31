import type { Metadata } from "next";

import { PrincipalDirectory } from "@/components/PrincipalDirectory";
import { documentTitle } from "@/lib/nav";

export const metadata: Metadata = { title: documentTitle("Groups") };

export default function GroupsDirectoryPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  return PrincipalDirectory({ kind: "groups", searchParams });
}
