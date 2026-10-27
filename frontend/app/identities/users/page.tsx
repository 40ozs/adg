import { PrincipalDirectory } from "@/components/PrincipalDirectory";

export default function UsersDirectoryPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  return PrincipalDirectory({ kind: "users", searchParams });
}
