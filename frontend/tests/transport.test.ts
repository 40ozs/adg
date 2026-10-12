import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "../lib/api/client";
import { installApiTransport, installedApiTransport, sendApiRequest } from "../lib/api/transport";

const TRANSPORT_KEY = Symbol.for("adg.apiTransport");

afterEach(() => {
  delete (globalThis as Record<symbol, unknown>)[TRANSPORT_KEY];
  vi.unstubAllGlobals();
});

describe("sendApiRequest", () => {
  it("is plain fetch when no host installed a transport", async () => {
    const fetchMock = vi.fn(async () => Response.json({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const url = new URL("http://api:8000/runtime");
    const init = { method: "GET", cache: "no-store" as const };
    await sendApiRequest(url, init);

    expect(installedApiTransport()).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith(url, init);
  });

  it("hands every request to an installed transport and never touches the network", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const seen: Request[] = [];
    installApiTransport(async (request) => {
      seen.push(request);
      return Response.json({ runtime_mode: "demo" });
    });

    const response = await sendApiRequest(new URL("http://adg-api.internal/runtime?x=1"), {
      method: "POST",
      headers: { Authorization: "Bearer t" },
      body: "{}",
      cache: "no-store",
    });

    expect(await response.json()).toEqual({ runtime_mode: "demo" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(seen).toHaveLength(1);
    expect(seen[0].url).toBe("http://adg-api.internal/runtime?x=1");
    expect(seen[0].method).toBe("POST");
    expect(seen[0].headers.get("Authorization")).toBe("Bearer t");
    expect(await seen[0].text()).toBe("{}");
  });

  it("carries the typed client's requests, so its failure classification still applies", async () => {
    installApiTransport(async () => Response.json({ detail: "nope" }, { status: 403 }));

    const result = await apiRequest("/api/v1/risks", { token: "t" });

    expect(result).toMatchObject({ ok: false, failure: { kind: "forbidden", status: 403 } });
  });

  it("reports a transport that throws as an unreachable API, like a network failure", async () => {
    installApiTransport(async () => {
      throw new Error("container did not start");
    });

    const result = await apiRequest("/runtime");

    expect(result).toMatchObject({
      ok: false,
      failure: { kind: "unreachable", detail: "container did not start" },
    });
  });
});
