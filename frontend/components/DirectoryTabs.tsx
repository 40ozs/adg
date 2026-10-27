import type { JSX } from "react";

import { Tabs } from "@/components/Tabs";

/** The three object directories, each a first-class page with its own URL. */
const DIRECTORIES = [
  {
    id: "servers",
    label: "Servers",
    href: "/resources",
    hint: "Servers ADG has observed, and the shares they publish.",
  },
  {
    id: "users",
    label: "Users",
    href: "/identities/users",
    hint: "User and managed service accounts, searchable by name or SID.",
  },
  {
    id: "groups",
    label: "Groups",
    href: "/identities/groups",
    hint: "Domain and local groups, searchable by name or SID.",
  },
] as const;

export type DirectoryId = (typeof DIRECTORIES)[number]["id"];

/** Moves between Servers, Users and Groups as client-side navigations. */
export function DirectoryTabs({ current }: { current: DirectoryId }): JSX.Element {
  return <Tabs label="Directories" tabs={DIRECTORIES} current={current} />;
}
