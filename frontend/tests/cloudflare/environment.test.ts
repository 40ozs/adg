import { describe, expect, it } from "vitest";

import {
  INTERNAL_API_URL,
  type StagingVariables,
  apiUpstream,
  frontendEnvironment,
} from "../../cloudflare/environment";

const ORIGIN_KEY = "o".repeat(48);

const VALID: StagingVariables = {
  ADG_API_ORIGIN: "https://adg-demo-api.onrender.com",
  ADG_ORIGIN_KEY: ORIGIN_KEY,
  ADG_WEB_URL: "https://adg-demo-staging.example.workers.dev",
};

describe("apiUpstream", () => {
  it("gives the API origin and the origin key", () => {
    expect(apiUpstream(VALID)).toEqual({ origin: "https://adg-demo-api.onrender.com", key: ORIGIN_KEY });
    expect(apiUpstream({ ...VALID, ADG_API_ORIGIN: " https://adg-demo-api.onrender.com/ " }).origin).toBe(
      "https://adg-demo-api.onrender.com",
    );
  });

  it.each([
    ["unset", undefined],
    ["empty", ""],
    ["plain http", "http://adg-demo-api.onrender.com"],
    ["a path", "https://adg-demo-api.onrender.com/api"],
    ["a query", "https://adg-demo-api.onrender.com/?a=1"],
    ["credentials", "https://u:p@adg-demo-api.onrender.com"],
    ["not a URL", "adg-demo-api.onrender.com"],
  ])("refuses an API origin that is %s", (_label, origin) => {
    expect(() => apiUpstream({ ...VALID, ADG_API_ORIGIN: origin })).toThrow(/ADG_API_ORIGIN/);
  });

  it("refuses to send requests the origin gate would refuse", () => {
    expect(() => apiUpstream({ ...VALID, ADG_ORIGIN_KEY: undefined })).toThrow(/ADG_ORIGIN_KEY/);
    expect(() => apiUpstream({ ...VALID, ADG_ORIGIN_KEY: "short" })).toThrow(/ADG_ORIGIN_KEY/);
  });

  it("never names a secret's value in a refusal", () => {
    expect(() => apiUpstream({ ADG_API_ORIGIN: "https://u:hunter2@h", ADG_ORIGIN_KEY: "s3cret" })).toThrow(
      expect.objectContaining({ message: expect.not.stringMatching(/hunter2|s3cret/) }),
    );
  });
});

describe("frontendEnvironment", () => {
  it("gives Next.js the bindings and addresses it reads, and neither the API origin nor its key", () => {
    const assets = { fetch: () => undefined };
    const environment = frontendEnvironment({ ...VALID, ASSETS: assets, UNRELATED: "x" });
    expect(environment).toEqual({
      ASSETS: assets,
      ADG_INTERNAL_API_URL: INTERNAL_API_URL,
      ADG_WEB_URL: VALID.ADG_WEB_URL,
      NEXT_PUBLIC_ADG_API_URL: VALID.ADG_WEB_URL,
    });
    expect(JSON.stringify(environment)).not.toContain(ORIGIN_KEY);
    expect(JSON.stringify(environment)).not.toContain("onrender.com");
  });
});
