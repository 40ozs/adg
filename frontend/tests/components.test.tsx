// @vitest-environment jsdom
/**
 * The parts of the shell a keyboard or a screen reader has to get through.
 *
 * These assert behavior rather than markup: that the current section is announced, that the
 * search box can be reached and submitted without a mouse, and — most importantly — that a
 * warning about incomplete collection is delivered as an alert rather than as grey text
 * somebody can skim past.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CoverageCaveat, DevelopmentAuthBanner, StateMessage } from "@/components/Banners";
import { GlobalSearch } from "@/components/GlobalSearch";
import { PrimaryNav } from "@/components/PrimaryNav";
import type { AuthConfig } from "@/lib/contracts";
import type { ViewState } from "@/lib/state";

const push = vi.fn();
let pathname = "/";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh: vi.fn() }),
  usePathname: () => pathname,
}));

// next/link renders an anchor; in a unit test it needs no router.
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const VIEWER = ["resources:read", "identities:read", "access:read", "risks:read", "changes:read", "collectors:read"];

describe("the primary navigation", () => {
  it("is a labelled landmark", () => {
    pathname = "/";
    render(<PrimaryNav capabilities={VIEWER} />);

    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
  });

  it("announces the current section with aria-current", () => {
    pathname = "/resources";
    render(<PrimaryNav capabilities={VIEWER} />);

    expect(screen.getByRole("link", { name: /Resources/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: /Overview/ })).not.toHaveAttribute("aria-current");
  });

  it("offers every link to the keyboard, in order", async () => {
    pathname = "/";
    render(<PrimaryNav capabilities={VIEWER} />);
    const user = userEvent.setup();

    await user.tab();
    expect(screen.getByRole("link", { name: /Overview/ })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("link", { name: /Resources/ })).toHaveFocus();
  });

  it("omits a section the account has no capability for", () => {
    pathname = "/";
    render(<PrimaryNav capabilities={VIEWER} />);

    expect(screen.queryByRole("link", { name: /Settings/ })).not.toBeInTheDocument();
  });

  it("labels a placeholder section so it is not mistaken for an empty one", () => {
    pathname = "/";
    render(<PrimaryNav capabilities={VIEWER} />);

    const risks = screen.getByRole("link", { name: /Risks/ });
    expect(within(risks).getByLabelText("not yet available")).toBeInTheDocument();
  });
});

describe("the global search box", () => {
  it("is a labelled search landmark", () => {
    render(<GlobalSearch />);

    expect(screen.getByRole("search")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Search identities, servers, shares, and directories"),
    ).toBeInTheDocument();
  });

  it("submits on Enter, without a mouse", async () => {
    push.mockClear();
    render(<GlobalSearch />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("searchbox"));
    await user.keyboard("Finance Managers{Enter}");

    expect(push).toHaveBeenCalledWith("/search?q=Finance%20Managers");
  });

  it("encodes a UNC path rather than mangling it", async () => {
    push.mockClear();
    render(<GlobalSearch />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("searchbox"));
    await user.keyboard("\\\\FS01\\Finance{Enter}");

    expect(push).toHaveBeenCalledWith("/search?q=%5C%5CFS01%5CFinance");
  });

  it("does not navigate on an empty term", async () => {
    push.mockClear();
    render(<GlobalSearch />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("searchbox"));
    await user.keyboard("   {Enter}");

    expect(push).not.toHaveBeenCalled();
  });

  it("is focused by the slash key from elsewhere on the page", async () => {
    render(
      <>
        <button type="button">Somewhere else</button>
        <GlobalSearch />
      </>,
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Somewhere else" }));
    await user.keyboard("/");

    expect(screen.getByRole("searchbox")).toHaveFocus();
  });

  it("does not steal the slash key from somebody typing a path", async () => {
    render(
      <>
        <input aria-label="Another field" />
        <GlobalSearch />
      </>,
    );
    const user = userEvent.setup();
    const other = screen.getByLabelText("Another field");

    await user.click(other);
    await user.keyboard("\\\\FS01/");

    expect(other).toHaveFocus();
    expect(other).toHaveValue("\\\\FS01/");
  });
});

describe("the development authentication banner", () => {
  const development: AuthConfig = {
    mode: "development",
    development: true,
    environment: "development",
    issuer: null,
    client_id: null,
    authorization_endpoint: null,
    token_endpoint: null,
    scopes: [],
    development_accounts: ["viewer"],
    roles: [],
  };

  it("is announced as an alert, not shown as a quiet note", () => {
    render(<DevelopmentAuthBanner config={development} />);

    expect(screen.getByRole("alert")).toHaveTextContent(/verifies no credential/);
  });

  it("says plainly that anyone can sign in as anyone", () => {
    render(<DevelopmentAuthBanner config={development} />);

    expect(screen.getByRole("alert")).toHaveTextContent(/can sign in as any account/);
  });

  it("is absent in a tenant deployment", () => {
    const { container } = render(
      <DevelopmentAuthBanner config={{ ...development, mode: "oidc", development: false }} />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});

describe("state messages", () => {
  it("announces an unattributable empty result as an alert", () => {
    const state: ViewState<never> = {
      kind: "empty",
      reason: "collection-failed",
      headline: "No shares found — but a collector failed",
      explanation: "Treat this emptiness as unknown.",
    };
    render(<StateMessage state={state} />);

    expect(screen.getByRole("alert")).toHaveTextContent("but a collector failed");
    expect(screen.getByRole("link", { name: /collectors reported/ })).toBeInTheDocument();
  });

  it("uses a quieter status for an empty result that is a real answer", () => {
    const state: ViewState<never> = {
      kind: "empty",
      reason: "no-matches",
      headline: "No shares",
      explanation: "This emptiness is an answer.",
    };
    render(<StateMessage state={state} />);

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("an answer");
  });

  it("offers a way back in when the session has ended", () => {
    const state: ViewState<never> = {
      kind: "unauthenticated",
      headline: "Your session has ended",
      explanation: "Sign in again.",
    };
    render(<StateMessage state={state} />);

    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("renders nothing at all when there is data to show", () => {
    const { container } = render(
      <StateMessage state={{ kind: "ready", data: [], caveat: null }} />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});

describe("the coverage caveat", () => {
  it("warns above data that cannot be read as complete", () => {
    render(
      <CoverageCaveat
        caveat={{
          severity: "warning",
          headline: "Part of the estate is unobserved",
          explanation: "A collector failed.",
        }}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("unobserved");
  });

  it("says nothing when collection is current", () => {
    const { container } = render(<CoverageCaveat caveat={null} />);

    expect(container).toBeEmptyDOMElement();
  });
});
