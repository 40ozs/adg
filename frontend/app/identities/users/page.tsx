import type { Metadata } from "next";

import { PrincipalDirectory } from "@/components/PrincipalDirectory";
import { documentTitle } from "@/lib/nav";

export const metadata: Metadata = { title: documentTitle("Users") };

export default function UsersDirectoryPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  return PrincipalDirectory({ kind: "users", searchParams });
}
