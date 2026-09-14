import { describe, expect, it } from "vitest";

import { DEFAULT_API_URL, resolveApiUrl, resolveServerApiUrl } from "../lib/config";

describe("resolveApiUrl", () => {
  it("falls back to the local development API when unset", () => {
    expect(resolveApiUrl({})).toBe(DEFAULT_API_URL);
  });

  it("trims surrounding whitespace and trailing slashes", () => {
    expect(resolveApiUrl({ NEXT_PUBLIC_ADG_API_URL: " https://adg.example.com/ " })).toBe(
      "https://adg.example.com",
    );
  });

  it("rejects a relative URL with an actionable message", () => {
    expect(() => resolveApiUrl({ NEXT_PUBLIC_ADG_API_URL: "/api" })).toThrow(
      /must be an absolute URL/,
    );
  });

  it("rejects a non-http protocol", () => {
    expect(() => resolveApiUrl({ NEXT_PUBLIC_ADG_API_URL: "ftp://adg.example.com" })).toThrow(
      /http or https/,
    );
  });
});

describe("resolveServerApiUrl", () => {
  it("prefers the internal URL when the web tier runs in a container", () => {
    expect(
      resolveServerApiUrl({
        NEXT_PUBLIC_ADG_API_URL: "http://localhost:8000",
        ADG_INTERNAL_API_URL: "http://api:8000",
      }),
    ).toBe("http://api:8000");
  });

  it("falls back to the browser URL when no internal URL is set", () => {
    expect(resolveServerApiUrl({ NEXT_PUBLIC_ADG_API_URL: "https://adg.example.com" })).toBe(
      "https://adg.example.com",
    );
  });

  it("names the offending variable when the internal URL is relative", () => {
    expect(() => resolveServerApiUrl({ ADG_INTERNAL_API_URL: "/api" })).toThrow(
      /ADG_INTERNAL_API_URL must be an absolute URL/,
    );
  });

  it("rejects a host:port value that is not a URL", () => {
    // "api:8000" parses as a URL whose protocol is "api:", so it fails the protocol check.
    expect(() => resolveServerApiUrl({ ADG_INTERNAL_API_URL: "api:8000" })).toThrow(
      /ADG_INTERNAL_API_URL must use http or https/,
    );
  });
});
