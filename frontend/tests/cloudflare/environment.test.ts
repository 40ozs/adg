import { describe, expect, it } from "vitest";

import {
  INTERNAL_API_URL,
  type StagingVariables,
  containerEnvironment,
  frontendEnvironment,
} from "../../cloudflare/environment";

const DATABASE_URL = "postgresql+psycopg://adg_demo_owner:s3cret@ep-x.neon.example/adg_demo?sslmode=require";
const DEV_SECRET = "d".repeat(48);

const VALID: StagingVariables = {
  ADG_RUNTIME_MODE: "demo",
  ADG_DEMO_DATASET: "null-meridian-v1",
  ADG_DATABASE_URL: DATABASE_URL,
  ADG_DEV_AUTH_SECRET: DEV_SECRET,
  ADG_WEB_URL: "https://adg-demo-staging.example.workers.dev",
};

describe("containerEnvironment", () => {
  it("builds a Null Meridian demo environment", () => {
    expect(containerEnvironment(VALID)).toEqual({
      ADG_RUNTIME_MODE: "demo",
      ADG_DEMO_DATASET: "null-meridian-v1",
      ADG_DATABASE_URL: DATABASE_URL,
      ADG_ENVIRONMENT: "development",
      ADG_AUTH_MODE: "development",
      ADG_LOG_LEVEL: "INFO",
      ADG_LOG_FORMAT: "json",
      ADG_CORS_ALLOW_ORIGINS: "https://adg-demo-staging.example.workers.dev",
      ADG_COLLECTOR_API_KEYS: "",
      ADG_ALERT_POLICY_PATH: "",
      ADG_RISK_CONFIGURATION_PATH: "",
      ADG_REMEDIATION_EXECUTION_MODE: "disabled",
      ADG_REMEDIATION_LAB_FIXTURE_PATH: "",
      ADG_ALERTS_ON_RUN_COMPLETION: "false",
      ADG_HISTORY_RETENTION_ENABLED: "false",
      ADG_DEV_AUTH_SECRET: DEV_SECRET,
    });
  });

  it.each([
    ["unset", undefined],
    ["production", "production"],
    ["standard", "standard"],
    ["empty", ""],
  ])("refuses runtime mode %s", (_label, mode) => {
    expect(() => containerEnvironment({ ...VALID, ADG_RUNTIME_MODE: mode })).toThrow(/ADG_RUNTIME_MODE must be "demo"/);
  });

  it("refuses any dataset but Null Meridian, including the reference estate", () => {
    for (const dataset of [undefined, "", "adg-reference-estate", "null-meridian-v2"]) {
      expect(() => containerEnvironment({ ...VALID, ADG_DEMO_DATASET: dataset })).toThrow(/null-meridian-v1/);
    }
  });

  it("refuses to start without the database secret or a durable session secret", () => {
    expect(() => containerEnvironment({ ...VALID, ADG_DATABASE_URL: undefined })).toThrow(/ADG_DATABASE_URL/);
    expect(() => containerEnvironment({ ...VALID, ADG_DEV_AUTH_SECRET: "short" })).toThrow(/ADG_DEV_AUTH_SECRET/);
  });

  it("never names a secret's value in a refusal", () => {
    try {
      containerEnvironment({ ...VALID, ADG_RUNTIME_MODE: "production" });
    } catch (error) {
      expect(String(error)).not.toContain("s3cret");
      expect(String(error)).not.toContain(DEV_SECRET);
      return;
    }
    throw new Error("expected a refusal");
  });

  it("pins every production integration off, whatever the Worker was given", () => {
    const hostile = {
      ...VALID,
      ADG_COLLECTOR_API_KEYS: "collector:0123456789abcdef0123456789abcdef",
      ADG_ALERT_POLICY_PATH: "/etc/adg/alerts.json",
      ADG_REMEDIATION_EXECUTION_MODE: "lab",
      ADG_REMEDIATION_LAB_FIXTURE_PATH: "/lab.json",
      ADG_OIDC_CLIENT_SECRET: "x",
      CLOUDFLARE_API_TOKEN: "cf-token",
    } as StagingVariables;
    const environment = containerEnvironment(hostile);
    expect(environment.ADG_COLLECTOR_API_KEYS).toBe("");
    expect(environment.ADG_ALERT_POLICY_PATH).toBe("");
    expect(environment.ADG_REMEDIATION_EXECUTION_MODE).toBe("disabled");
    expect(environment.ADG_REMEDIATION_LAB_FIXTURE_PATH).toBe("");
    expect(environment).not.toHaveProperty("ADG_OIDC_CLIENT_SECRET");
    expect(JSON.stringify(environment)).not.toContain("cf-token");
  });

  it("passes the optional signing key and development accounts through", () => {
    const environment = containerEnvironment({
      ...VALID,
      ADG_REMEDIATION_SIGNING_KEY: "k".repeat(40),
      ADG_DEV_AUTH_USERS: "viewer:viewer",
    });
    expect(environment.ADG_REMEDIATION_SIGNING_KEY).toBe("k".repeat(40));
    expect(environment.ADG_DEV_AUTH_USERS).toBe("viewer:viewer");
  });
});

describe("frontendEnvironment", () => {
  it("gives Next.js the bindings and addresses it reads, and no secret", () => {
    const assets = { fetch: () => undefined };
    const environment = frontendEnvironment({ ...VALID, ASSETS: assets, ADG_API: {}, ACCESS_AUD: "aud" });
    expect(environment).toEqual({
      ASSETS: assets,
      ADG_INTERNAL_API_URL: INTERNAL_API_URL,
      ADG_WEB_URL: VALID.ADG_WEB_URL,
      NEXT_PUBLIC_ADG_API_URL: VALID.ADG_WEB_URL,
    });
    expect(JSON.stringify(environment)).not.toContain("s3cret");
  });
});
