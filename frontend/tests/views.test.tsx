// @vitest-environment jsdom
/**
 * The components a detail page is built from.
 *
 * These assert the behavior an auditor depends on rather than the markup: that a SID is on
 * screen beside every name, that an inherit-only entry says it grants nothing, that a raw
 * ACL and an engine answer are labelled as different kinds of statement, and that a pager
 * never offers a page it cannot reach.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Verdict, EnumerationNotice, PrincipalAccessTable } from "@/components/EffectiveAccess";
import { NoteList, TriState } from "@/components/Facts";
import { PrincipalName } from "@/components/Identity";
import { EffectiveLayer, RawLayer } from "@/components/Layer";
import { PagePosition, Pager } from "@/components/Pager";
import { NtfsAceTable, ShareAceTable } from "@/components/RawAcl";
import { Breadcrumbs, Tabs } from "@/components/Tabs";
import { ambiguousLabels } from "@/lib/identity";
import { resourceBreadcrumb } from "@/lib/resources";
import { ntfsAce, page, principal, rights, shareAce } from "./factories";

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const FS01 = principal({
  key: "fs01|S-1-5-32-544",
  sid: "S-1-5-32-544",
  host_key: "fs01",
  display_name: "Administrators",
  is_group: true,
});
const FS02 = principal({
  key: "fs02|S-1-5-32-544",
  sid: "S-1-5-32-544",
  host_key: "fs02",
  display_name: "Administrators",
  is_group: true,
});

describe("a principal on screen", () => {
  it("shows the key beside the name, always", () => {
    render(<PrincipalName principal={FS01} />);

    expect(screen.getByText("Administrators")).toBeInTheDocument();
    expect(screen.getByText("fs01|S-1-5-32-544")).toBeInTheDocument();
  });

  it("promotes the host when two rows share a name", () => {
    const ambiguous = ambiguousLabels([FS01, FS02]);
    const { container } = render(
      <>
        <PrincipalName principal={FS01} ambiguous={ambiguous} />
        <PrincipalName principal={FS02} ambiguous={ambiguous} />
      </>,
    );

    expect(container.textContent).toContain("(on fs01)");
    expect(container.textContent).toContain("(on fs02)");
  });

  it("leaves the qualifier off when the name is unique in its list", () => {
    const { container } = render(
      <PrincipalName principal={FS01} ambiguous={ambiguousLabels([FS01])} />,
    );

    expect(container.textContent).not.toContain("(on fs01)");
  });

  it("marks an undescribed SID rather than rendering a blank", () => {
    const orphan = principal({ resolved: false, unresolved_reason: "not_collected" });
    const { container } = render(<PrincipalName principal={orphan} />);

    expect(container.textContent).toContain("unresolved (not collected)");
  });

  it("does not print the key twice when the name already is the key", () => {
    // An orphaned trustee falls back to its SID for a name, so the key line beneath would
    // repeat it: a second line carrying no second fact.
    const orphan = principal({ key: "S-1-1-0", sid: "S-1-1-0", resolved: false });
    render(<PrincipalName principal={orphan} />);

    expect(screen.getAllByText("S-1-1-0")).toHaveLength(1);
  });
});

describe("a raw NTFS ACL", () => {
  it("says an inherit-only entry grants nothing on this directory", () => {
    render(
      <NtfsAceTable entries={[ntfsAce({ applies_to_this_object: false, ace_key: "a" })]} />,
    );

    expect(screen.getByText(/no — inherit-only/)).toBeInTheDocument();
    expect(screen.getByText(/grants nothing on this directory/)).toBeInTheDocument();
  });

  it("marks each entry explicit or inherited, which is where a fix goes", () => {
    render(
      <NtfsAceTable
        entries={[
          ntfsAce({ ace_key: "a", source: "explicit", inherited_from: null }),
          ntfsAce({ ace_key: "b", source: "inherited" }),
        ]}
      />,
    );

    expect(screen.getByText("explicit")).toBeInTheDocument();
    expect(screen.getByText("inherited")).toBeInTheDocument();
  });

  it("shows the mask beside the named rights, because the mask is authoritative", () => {
    render(<NtfsAceTable entries={[ntfsAce({ access_mask: 0x001f01ff })]} />);

    expect(screen.getByText("0x001F01FF")).toBeInTheDocument();
  });

  it("says an unordered entry is unordered rather than implying a position", () => {
    render(<NtfsAceTable entries={[ntfsAce({ order_index: null })]} />);

    expect(screen.getByText("unordered")).toBeInTheDocument();
  });
});

describe("a raw share ACL", () => {
  it("renders the right in the form the source reported it", () => {
    render(<ShareAceTable entries={[shareAce({ right: "0x001301BF", permission: null })]} />);

    expect(screen.getByText("0x001301BF")).toBeInTheDocument();
  });

  it("marks a Deny entry as a deny", () => {
    render(<ShareAceTable entries={[shareAce({ ace_type: "deny" })]} />);

    expect(screen.getByText("deny")).toBeInTheDocument();
  });
});

describe("raw facts and computed answers", () => {
  it("label themselves as different kinds of statement", () => {
    render(
      <>
        <RawLayer title="NTFS ACL" id="raw" layer="NTFS">
          <p>entries</p>
        </RawLayer>
        <EffectiveLayer title="Who can reach this" id="effective">
          <p>answers</p>
        </EffectiveLayer>
      </>,
    );

    expect(screen.getByText("Raw NTFS ACL — as collected")).toBeInTheDocument();
    expect(screen.getByText("Effective access — computed answer")).toBeInTheDocument();
  });

  it("carries the caveat that being on an ACL is not access", () => {
    render(
      <RawLayer title="Share ACL" id="raw" layer="SMB share">
        <p>entries</p>
      </RawLayer>,
    );

    expect(screen.getByText(/An entry here is not access/)).toBeInTheDocument();
  });

  it("gives each section its own landmark, so they cannot be read as one table", () => {
    render(
      <>
        <RawLayer title="NTFS ACL" id="raw" layer="NTFS">
          <p>entries</p>
        </RawLayer>
        <EffectiveLayer title="Who can reach this" id="effective">
          <p>answers</p>
        </EffectiveLayer>
      </>,
    );

    expect(screen.getByRole("region", { name: "NTFS ACL" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Who can reach this" })).toBeInTheDocument();
  });
});

describe("a verdict", () => {
  it("never renders an unestablished answer as a plain No", () => {
    render(<Verdict access={false} certainty="at_least" />);

    expect(screen.getByText("None established")).toBeInTheDocument();
    expect(screen.queryByText("No")).not.toBeInTheDocument();
  });

  it("keeps the certainty on screen beside the word", () => {
    render(<Verdict access={true} certainty="at_most" />);

    expect(screen.getByText("Yes, at most")).toBeInTheDocument();
    expect(screen.getByText(/upper bound/)).toBeInTheDocument();
  });
});

describe("who reaches a directory", () => {
  it("names the chain a principal arrives through", () => {
    render(
      <PrincipalAccessTable
        items={[
          {
            principal: principal({ display_name: "Ada", key: "S-1-5-21-1-2-3-1104" }),
            access: true,
            rights: rights(),
            certainty: "certain",
            limiting_layer: "ntfs",
            conditions: [],
            via: [
              {
                principal: principal({ display_name: "Finance", key: "S-1-5-21-1-2-3-1200" }),
                origin: "group_membership",
                assumed: false,
                depth: 1,
                path: ["S-1-5-21-1-2-3-1104", "S-1-5-21-1-2-3-1200"],
              },
            ],
          },
        ]}
      />,
    );

    const row = screen.getByRole("row", { name: /Ada/ });
    expect(within(row).getByText("Finance")).toBeInTheDocument();
  });

  it("says 'directly' rather than nothing when the ACL names the principal itself", () => {
    render(
      <PrincipalAccessTable
        items={[
          {
            principal: principal({ display_name: "Ada" }),
            access: true,
            rights: rights(),
            certainty: "certain",
            limiting_layer: "none",
            conditions: [],
            via: [],
          },
        ]}
      />,
    );

    expect(screen.getByText("the ACL names this principal directly")).toBeInTheDocument();
  });

  it("warns, as an alert, when the list is not everybody", () => {
    render(
      <EnumerationNotice
        enumeration={{
          complete: false,
          trustees_truncated: false,
          unenumerable_trustees: [principal({ display_name: "Everyone", sid: "S-1-1-0" })],
        }}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(within(alert).getByText("This is not everybody")).toBeInTheDocument();
    expect(alert.textContent).toContain("Everyone");
  });

  it("says nothing when the enumeration is complete", () => {
    const { container } = render(
      <EnumerationNotice
        enumeration={{ complete: true, trustees_truncated: false, unenumerable_trustees: [] }}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});

describe("the pager", () => {
  const navigation = {
    pageNumber: 2,
    previousHref: "/x",
    nextHref: null,
    firstHref: "/x?first",
  };

  it("offers no next link when the API sent no cursor", () => {
    render(<Pager navigation={navigation} page={page({ has_more: false })} subject="entries" />);

    expect(screen.queryByRole("link", { name: "Next" })).not.toBeInTheDocument();
  });

  it("says the total was not counted rather than implying one", () => {
    render(<Pager navigation={navigation} page={page({ total: null })} subject="entries" />);

    expect(screen.getByText(/did not count the total/)).toBeInTheDocument();
  });

  it("says so when more exist than it can reach", () => {
    render(<Pager navigation={navigation} page={page({ has_more: true })} subject="entries" />);

    expect(screen.getByText(/the API sent no cursor to reach them/)).toBeInTheDocument();
  });
});

describe("the page position", () => {
  it("says nothing on page one", () => {
    const { container } = render(
      <PagePosition trail={{ cursors: [], rejected: false }} firstPageHref="/x" />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("announces a rejected position instead of silently showing page one", () => {
    render(<PagePosition trail={{ cursors: [], rejected: true }} firstPageHref="/x" />);

    expect(screen.getByRole("alert").textContent).toContain("this is page 1");
  });

  it("offers the way back from a deep page, which is what an API error leaves you on", () => {
    // A hand-edited cursor still looks like base64url, so it reaches the API and comes back
    // 422 with no rows. Without this link the reader is on an error page with no exit.
    render(<PagePosition trail={{ cursors: ["a", "b"], rejected: false }} firstPageHref="/x" />);

    expect(screen.getByText(/Page 3 of this list/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the first page." })).toHaveAttribute(
      "href",
      "/x",
    );
  });
});

describe("navigation chrome", () => {
  it("marks the selected tab and explains what it shows", () => {
    render(
      <Tabs
        label="Share sections"
        current="root-acl"
        tabs={[
          { id: "share-acl", label: "Share ACL", href: "/a" },
          { id: "root-acl", label: "Root NTFS ACL", href: "/b", hint: "A different layer." },
        ]}
      />,
    );

    expect(screen.getByRole("link", { name: "Root NTFS ACL" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Share ACL" })).not.toHaveAttribute("aria-current");
    expect(screen.getByText("A different layer.")).toBeInTheDocument();
  });

  it("falls back to the first tab when the URL names one that does not exist", () => {
    render(
      <Tabs
        label="Sections"
        current="nonsense"
        tabs={[
          { id: "a", label: "First", href: "/a" },
          { id: "b", label: "Second", href: "/b" },
        ]}
      />,
    );

    expect(screen.getByRole("link", { name: "First" })).toHaveAttribute("aria-current", "page");
  });

  it("does not link the breadcrumb's last crumb to itself", () => {
    render(<Breadcrumbs crumbs={resourceBreadcrumb("\\\\FS01\\Finance\\Reports")} />);

    expect(screen.getByRole("link", { name: "FS01" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Reports" })).not.toBeInTheDocument();
    expect(screen.getByText("Reports")).toHaveAttribute("aria-current", "page");
  });
});

describe("collected facts", () => {
  it("renders a null boolean as 'not reported', not as 'no'", () => {
    render(<TriState value={null} />);

    expect(screen.getByText("not reported")).toBeInTheDocument();
  });

  it("delivers a severe note as an alert rather than as coloured text", () => {
    render(
      <NoteList
        notes={[
          { tone: "bad", headline: "NULL DACL", detail: "Everyone has full access." },
          { tone: "info", headline: "Context", detail: "Nothing urgent." },
        ]}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(within(alert).getByText("NULL DACL")).toBeInTheDocument();
    expect(screen.getByRole("status").textContent).toContain("Context");
  });
});
