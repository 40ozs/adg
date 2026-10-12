import { describe, expect, it } from "vitest";

import { type StagingVariables, originUpstream } from "../../cloudflare/environment";

const ORIGIN_KEY = "o".repeat(48);

const VALID: StagingVariables = {
  ADG_RENDER_ORIGIN: "https://adg-demo-web.onrender.com",
  ADG_ORIGIN_KEY: ORIGIN_KEY,
};

describe("originUpstream", () => {
  it("gives the Render origin and the origin key", () => {
    expect(originUpstream(VALID)).toEqual({ origin: "https://adg-demo-web.onrender.com", key: ORIGIN_KEY });
    expect(originUpstream({ ...VALID, ADG_RENDER_ORIGIN: " https://adg-demo-web.onrender.com/ " }).origin).toBe(
      "https://adg-demo-web.onrender.com",
    );
  });

  it("accepts plain http for loopback only (wrangler dev against the local image)", () => {
    expect(originUpstream({ ...VALID, ADG_RENDER_ORIGIN: "http://127.0.0.1:10000" }).origin).toBe("http://127.0.0.1:10000");
    expect(originUpstream({ ...VALID, ADG_RENDER_ORIGIN: "http://localhost:10000" }).origin).toBe("http://localhost:10000");
  });

  it.each([
    ["unset", undefined],
    ["empty", ""],
    ["plain http", "http://adg-demo-web.onrender.com"],
    ["plain http to a private address", "http://10.0.0.1"],
    ["a path", "https://adg-demo-web.onrender.com/api"],
    ["a query", "https://adg-demo-web.onrender.com/?a=1"],
    ["credentials", "https://u:p@adg-demo-web.onrender.com"],
    ["not a URL", "adg-demo-web.onrender.com"],
  ])("refuses an origin that is %s", (_label, origin) => {
    expect(() => originUpstream({ ...VALID, ADG_RENDER_ORIGIN: origin })).toThrow(/ADG_RENDER_ORIGIN/);
  });

  it("refuses to send requests the origin gate would refuse", () => {
    expect(() => originUpstream({ ...VALID, ADG_ORIGIN_KEY: undefined })).toThrow(/ADG_ORIGIN_KEY/);
    expect(() => originUpstream({ ...VALID, ADG_ORIGIN_KEY: "short" })).toThrow(/ADG_ORIGIN_KEY/);
  });

  it("never names a secret's value in a refusal", () => {
    expect(() => originUpstream({ ADG_RENDER_ORIGIN: "https://u:hunter2@h", ADG_ORIGIN_KEY: "s3cret" })).toThrow(
      expect.objectContaining({ message: expect.not.stringMatching(/hunter2|s3cret/) }),
    );
  });
});
