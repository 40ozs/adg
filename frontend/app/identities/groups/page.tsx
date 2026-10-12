import { PrincipalDirectory } from "@/components/PrincipalDirectory";

export default function GroupsDirectoryPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  return PrincipalDirectory({ kind: "groups", searchParams });
}
