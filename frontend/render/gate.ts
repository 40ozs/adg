/**
 * The front gate of the ADG demo's Render service: the only listener on the public port.
 *
 *   Cloudflare Worker ──HTTPS──► Render $PORT ──► this gate ─┬─ API paths ─► FastAPI 127.0.0.1:8000 ─► Supabase
 *   (X-ADG-Origin-Key)                                       └─ the rest ──► Next.js 127.0.0.1:3000
 *                                                                             its server-side API calls ─┘
 *
 * Render's free tier has no private network or IP allow-list, so the service's onrender.com
 * address is public. The gate answers a request only when it carries the Worker's
 * `X-ADG-Origin-Key` (compared in constant time). The one exception is `/health/live`, Render's
 * health check, which FastAPI answers without touching a dependency. Without a key the gate
 * does not start.
 *
 * The gate also supervises the two processes. It starts FastAPI and Next.js on loopback and
 * opens the public port only once both accept connections. If either one exits, the gate exits
 * too, so Render restarts the whole service instead of serving half of it. Neither process
 * receives the origin key, and Next.js receives only the variables it reads.
 *
 * Node's own `http` only, no dependencies. Run as `node gate.ts` (Node ≥ 22.18 strips the
 * types). See docker/Dockerfile.render and docs/deployment/cloudflare-demo.md.
 */

import { type ChildProcess, spawn } from "node:child_process";
import { createHash, timingSafeEqual } from "node:crypto";
import http from "node:http";
import net from "node:net";
import { pathToFileURL } from "node:url";

export const KEY_VARIABLE = "ADG_ORIGIN_KEY";
export const KEY_HEADER = "x-adg-origin-key";
export const MINIMUM_KEY_LENGTH = 32;
/** Render's health check carries no key. FastAPI's liveness reads nothing and names only a version. */
export const OPEN_PATHS: ReadonlySet<string> = new Set(["/health/live"]);
export const REFUSAL = "This service answers only its staging proxy.";

const LOOPBACK = "127.0.0.1";
export const API_PORT = 8000;
export const WEB_PORT = 3000;

/** The ADG API's own paths (`backend/app/api/`), the same list the Worker uses (`cloudflare/routing.ts`). */
const API_PREFIXES = ["/api/v1/", "/auth/", "/health/"];
const API_EXACT = ["/version", "/runtime"];

/** Whether FastAPI answers `pathname`; Next.js answers everything else. */
export function isApiPath(pathname: string): boolean {
  return API_EXACT.includes(pathname) || API_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

/** RFC 9110 §7.6.1: these describe one connection and are never forwarded. */
const HOP_BY_HOP = [
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
];
/** Never forwarded to FastAPI: the key, and the web tier's session cookie (the API reads a bearer token). */
const NOT_FOR_THE_API = new Set([KEY_HEADER, "cookie"]);
/** Never forwarded to Next.js: the key, and forwarding headers the gate sets itself. */
const NOT_FOR_THE_WEB = new Set([KEY_HEADER, "x-forwarded-host", "x-forwarded-proto"]);

type Log = (entry: Record<string, unknown>) => void;
type Environment = Record<string, string | undefined>;
const jsonLog: Log = (entry) => console.log(JSON.stringify(entry));

/**
 * `rawHeaders` (name, value, name, value, …) without hop-by-hop headers, without any header
 * the `Connection` header names, and without `drop` (lower-case names).
 */
export function forwardableHeaders(rawHeaders: readonly string[], drop: ReadonlySet<string> = new Set()): string[] {
  const excluded = new Set([...HOP_BY_HOP, ...drop]);
  for (let i = 0; i < rawHeaders.length; i += 2) {
    if (rawHeaders[i].toLowerCase() === "connection") {
      for (const token of rawHeaders[i + 1].split(",")) {
        excluded.add(token.trim().toLowerCase());
      }
    }
  }
  const kept: string[] = [];
  for (let i = 0; i < rawHeaders.length; i += 2) {
    if (!excluded.has(rawHeaders[i].toLowerCase())) {
      kept.push(rawHeaders[i], rawHeaders[i + 1]);
    }
  }
  return kept;
}

const sha256 = (value: string): Buffer => createHash("sha256").update(value).digest();

/**
 * Whether a request's key header is the key. Both values are hashed first, so the comparison
 * takes the same time whatever the supplied value's length. A repeated header is no key.
 */
export function keyMatches(supplied: string | string[] | undefined, expectedDigest: Buffer): boolean {
  return typeof supplied === "string" && timingSafeEqual(sha256(supplied), expectedDigest);
}

function refuse(response: http.ServerResponse, status: number, detail: string): void {
  const body = JSON.stringify({ detail });
  response.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
  });
  response.end(body);
}

export interface GateOptions {
  key: string;
  apiPort?: number;
  webPort?: number;
  /**
   * The public address visitors use (`ADG_WEB_URL`). Next.js is told it serves that host, so
   * its Server Actions accept the browser's `Origin` and its absolute URLs name the public host.
   */
  publicUrl?: string;
  log?: Log;
  /** Called with each answer's upstream and status (for the first-response timings in `main`). */
  observe?: (upstream: "api" | "web", status: number) => void;
}

/** The gate's request handler: check the key, then relay to FastAPI or Next.js on loopback. */
export function createGate(options: GateOptions): http.RequestListener {
  if (options.key.length < MINIMUM_KEY_LENGTH) {
    throw new Error(`${KEY_VARIABLE} must be at least ${MINIMUM_KEY_LENGTH} characters`);
  }
  const expected = sha256(options.key);
  const apiPort = options.apiPort ?? API_PORT;
  const webPort = options.webPort ?? WEB_PORT;
  const log = options.log ?? jsonLog;
  const publicUrl = options.publicUrl?.trim() ? new URL(options.publicUrl.trim()) : null;

  return (request, response) => {
    const target = request.url ?? "";
    if (!target.startsWith("/")) {
      refuse(response, 400, "Bad request target.");
      return;
    }
    const queryAt = target.indexOf("?");
    const pathname = queryAt === -1 ? target : target.slice(0, queryAt);
    const open = OPEN_PATHS.has(pathname);
    if (!open && !keyMatches(request.headers[KEY_HEADER], expected)) {
      refuse(response, 403, REFUSAL);
      return;
    }

    const upstream = isApiPath(pathname) ? "api" : "web";
    const headers = forwardableHeaders(request.rawHeaders, upstream === "api" ? NOT_FOR_THE_API : NOT_FOR_THE_WEB);
    if (upstream === "web" && publicUrl) {
      headers.push("X-Forwarded-Host", publicUrl.host, "X-Forwarded-Proto", publicUrl.protocol.slice(0, -1));
    }

    const relay = http.request(
      {
        host: LOOPBACK,
        port: upstream === "api" ? apiPort : webPort,
        method: request.method,
        // The open path is relayed as itself: nothing a caller adds reaches FastAPI unkeyed.
        path: open ? pathname : target,
        headers,
        agent: false,
      },
      (answer) => {
        const status = answer.statusCode ?? 502;
        response.writeHead(status, answer.statusMessage, forwardableHeaders(answer.rawHeaders));
        answer.pipe(response);
        options.observe?.(upstream, status);
      },
    );
    relay.on("error", (error) => {
      log({ event: "adg.gate.upstream_unavailable", upstream, error: error.message });
      if (response.headersSent) {
        response.destroy(error);
      } else {
        refuse(response, 502, "The demo service did not answer.");
      }
    });
    response.on("close", () => {
      if (!response.writableFinished) {
        relay.destroy();
      }
    });
    request.pipe(relay);
  };
}

/** FastAPI's environment: the service's, minus the origin key it no longer needs. */
export function apiEnvironment(env: Environment): Environment {
  const selected = { ...env };
  delete selected[KEY_VARIABLE];
  return selected;
}

/**
 * Next.js's environment: only what it reads (`lib/config.ts`). It never sees the database URL,
 * the development-session secret or the origin key.
 */
export function webEnvironment(env: Environment): Environment {
  const selected: Environment = {
    NODE_ENV: "production",
    NEXT_TELEMETRY_DISABLED: "1",
    HOSTNAME: LOOPBACK,
    PORT: String(WEB_PORT),
    ADG_INTERNAL_API_URL: `http://${LOOPBACK}:${API_PORT}`,
  };
  for (const name of ["PATH", "HOME", "TZ"]) {
    if (env[name] !== undefined) {
      selected[name] = env[name];
    }
  }
  const webUrl = env.ADG_WEB_URL?.trim();
  if (webUrl) {
    selected.ADG_WEB_URL = webUrl;
    selected.NEXT_PUBLIC_ADG_API_URL = webUrl;
  }
  return selected;
}

function accepting(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect({ host: LOOPBACK, port });
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => resolve(false));
  });
}

/** Start FastAPI and Next.js, then open the public port. Exits when either process does. */
export async function main(env: Environment = process.env): Promise<void> {
  const started = performance.now();
  const elapsed = () => Math.round(performance.now() - started);
  const key = env[KEY_VARIABLE]?.trim() ?? "";
  if (key.length < MINIMUM_KEY_LENGTH) {
    console.error(`${KEY_VARIABLE} is not set (at least ${MINIMUM_KEY_LENGTH} characters); not serving.`);
    process.exit(1);
  }
  const port = Number(env.PORT || 10000);
  jsonLog({ event: "adg.gate.starting", port });

  const children: ChildProcess[] = [];
  let server: http.Server | null = null;
  let stopping = false;
  const stop = (code: number) => {
    if (stopping) {
      return;
    }
    stopping = true;
    server?.close();
    const exited = children.map(
      (child) =>
        new Promise((resolve) => {
          if (child.exitCode !== null || child.signalCode !== null) {
            resolve(null);
          } else {
            child.once("exit", resolve);
            child.kill("SIGTERM");
          }
        }),
    );
    void Promise.all(exited).then(() => process.exit(code));
    setTimeout(() => process.exit(code), 10_000).unref();
  };
  const start = (name: string, command: string, args: string[], cwd: string, childEnv: Environment) => {
    const child = spawn(command, args, { cwd, env: childEnv as NodeJS.ProcessEnv, stdio: "inherit" });
    child.on("error", (error) => {
      jsonLog({ event: "adg.gate.child_failed", child: name, error: error.message });
      stop(1);
    });
    child.on("exit", (code, signal) => {
      jsonLog({ event: "adg.gate.child_exited", child: name, code, signal });
      stop(stopping ? 0 : 1);
    });
    children.push(child);
  };
  process.on("SIGTERM", () => stop(0));
  process.on("SIGINT", () => stop(0));

  const apiArgs = ["-m", "app", "--host", LOOPBACK, "--port", String(API_PORT)];
  start("api", "python", apiArgs, env.ADG_GATE_API_DIR ?? "/app/backend", apiEnvironment(env));
  start("web", process.execPath, ["server.js"], env.ADG_GATE_WEB_DIR ?? "/app/web", webEnvironment(env));

  const ready = async (name: string, childPort: number) => {
    while (!stopping && !(await accepting(childPort))) {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    jsonLog({ event: "adg.gate.child_ready", child: name, after_ms: elapsed() });
  };
  await Promise.all([ready("api", API_PORT), ready("web", WEB_PORT)]);
  if (stopping) {
    return;
  }

  const first = new Set<string>();
  server = http.createServer(
    createGate({
      key,
      publicUrl: env.ADG_WEB_URL,
      observe: (upstream, status) => {
        if (!first.has(upstream)) {
          first.add(upstream);
          jsonLog({ event: "adg.gate.first_answer", upstream, status, after_ms: elapsed() });
        }
      },
    }),
  );
  // Longer than the platform proxy's idle timeout, so it never reuses a socket the gate closed.
  server.keepAliveTimeout = 120_000;
  server.headersTimeout = 125_000;
  server.listen(port, "0.0.0.0", () => jsonLog({ event: "adg.gate.listening", port, after_ms: elapsed() }));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  void main();
}
