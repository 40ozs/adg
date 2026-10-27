// @vitest-environment jsdom
/**
 * The demo environment indicator, alone and inside the real shell.
 *
 * Two claims. In demo mode the shell shows, before anyone signs in, that the organization is
 * fictional and that no directory or file server is connected — and still says the
 * application is real. In production the shell shows none of it: no pill, no footer line,
 * and nothing that could be mistaken for a switch.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/AppShell";
import { DemoEnvironmentIndicator } from "@/components/Banners";
import * as adg from "@/lib/api/adg";
import { currentViewer } from "@/lib/auth/current";
import type { AuthConfig, RuntimeInfo } from "@/lib/contracts";

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    prefetch,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
    prefetch?: boolean;
  }) => (
    // Recorded, not dropped: a list of principal links must not prefetch every one.
    <a href={href} data-prefetch={prefetch === false ? "off" : undefined} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/",
}));
vi.mock("@/lib/auth/current", () => ({ currentViewer: vi.fn() }));
vi.mock("@/lib/api/adg", () => ({ fetchAuthConfig: vi.fn(), fetchRuntime: vi.fn() }));

const demo: RuntimeInfo = {
  runtime_mode: "demo",
  data_source: "synthetic",
  synthetic: true,
  dataset: {
    key: "genesee-press-v1",
    company_name: "Genesee Press",
    domain: "corp.geneseepress.example",
    snapshot_date: "2026-10-01",
    seed: "genesee-press-v1",
    synthetic: true,
    description: "A fictional enterprise.",
    facts: { "active users": "1800" },
  },
  notice:
    "This environment uses deterministic synthetic data for Genesee Press. No production " +
    "Active Directory or file servers are connected.",
};

const production: RuntimeInfo = {
  runtime_mode: "production",
  data_source: "collectors",
  synthetic: false,
  dataset: null,
  notice: null,
};

const oidc: AuthConfig = {
  mode: "oidc",
  development: false,
  environment: "production",
  issuer: "https://login.example.test/v2.0",
  client_id: "adg",
  authorization_endpoint: null,
  token_endpoint: null,
  scopes: [],
  development_accounts: [],
  roles: [],
};

async function renderShell(runtime: RuntimeInfo | null): Promise<void> {
  vi.mocked(currentViewer).mockResolvedValue({ status: "signed-out" } as never);
  vi.mocked(adg.fetchAuthConfig).mockResolvedValue({ ok: true, data: oidc });
  vi.mocked(adg.fetchRuntime).mockResolvedValue(
    runtime
      ? { ok: true, data: runtime }
      : { ok: false, failure: { kind: "unavailable", status: null, message: "down" } as never },
  );
  render(await AppShell({ children: <p>page</p> }));
}

describe("the demo environment indicator", () => {
  it("names the environment and the fictional organization", () => {
    render(<DemoEnvironmentIndicator runtime={demo} />);

    expect(screen.getByText("Demo environment")).toBeInTheDocument();
    expect(screen.getByText(/Synthetic enterprise data · Genesee Press/)).toBeInTheDocument();
  });

  it("explains that the data is synthetic and the application is real", () => {
    render(<DemoEnvironmentIndicator runtime={demo} />);

    expect(
      screen.getByText(/No production Active Directory or file servers are connected/),
    ).toBeInTheDocument();
    expect(screen.getByText(/The application is real/)).toBeInTheDocument();
    expect(screen.getByText("corp.geneseepress.example")).toBeInTheDocument();
  });

  it("points to the demo guide, where the scenarios link into the real pages", () => {
    render(<DemoEnvironmentIndicator runtime={demo} />);

    expect(screen.getByRole("link", { name: "Explore the demo scenarios" })).toHaveAttribute(
      "href",
      "/demo",
    );
  });

  it("offers no control: nothing to click that could switch the data source", () => {
    render(<DemoEnvironmentIndicator runtime={demo} />);

    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("renders nothing in production", () => {
    const { container } = render(<DemoEnvironmentIndicator runtime={production} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("the shell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the indicator to a signed-out visitor of a demo deployment", async () => {
    await renderShell(demo);

    expect(screen.getByText("Demo environment")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toHaveTextContent(/organization and its data are fictional/);
  });

  it("shows no demo indicator in production", async () => {
    await renderShell(production);

    expect(screen.queryByText("Demo environment")).toBeNull();
    expect(screen.getByRole("contentinfo")).not.toHaveTextContent(/fictional/);
  });

  it("shows no demo indicator when the runtime cannot be read", async () => {
    await renderShell(null);

    expect(screen.queryByText("Demo environment")).toBeNull();
  });
});
