/**
 * How a server-side request reaches the ADG API.
 *
 * Everywhere ADG runs today — a workstation, the Docker stack — that is the network:
 * `fetch` to `ADG_INTERNAL_API_URL`. One host has no network path between the web tier and
 * the API: the Cloudflare Worker that serves this application next to the API's Container
 * (`cloudflare/worker.ts`, `docs/deployment/cloudflare-demo.md`). There the Container is
 * reached through a Durable Object binding, so the Worker installs a transport here before
 * it serves its first request.
 *
 * A host installs a transport; nobody using the application can. It is keyed by a
 * registered symbol on `globalThis`, which only code running in the same isolate can set,
 * and it carries every request `lib/api/client.ts` makes — the API calls and nothing else.
 * The OIDC token exchange, the only other server-side `fetch`, stays on the network.
 */

export type ApiTransport = (request: Request) => Promise<Response>;

const TRANSPORT_KEY = Symbol.for("adg.apiTransport");

type TransportHost = { [TRANSPORT_KEY]?: ApiTransport };

/** Route every API request through `transport` from now on. Called by a host, once. */
export function installApiTransport(transport: ApiTransport): void {
  (globalThis as TransportHost)[TRANSPORT_KEY] = transport;
}

/** The installed transport, or `null` when requests go over the network. */
export function installedApiTransport(): ApiTransport | null {
  return (globalThis as TransportHost)[TRANSPORT_KEY] ?? null;
}

/**
 * Send one API request.
 *
 * Without a transport this is exactly `fetch(url, init)`, so nothing changes for any host
 * that does not install one.
 */
export function sendApiRequest(url: URL, init: RequestInit): Promise<Response> {
  const transport = installedApiTransport();
  if (transport === null) {
    return fetch(url, init);
  }
  // `cache` is a browser/Next.js fetch option with no meaning on a binding; it is dropped
  // rather than passed to a runtime that may reject it. Every API response is uncached
  // either way: the API itself never sends a cacheable permission answer.
  const { cache: _cache, ...rest } = init;
  void _cache;
  return transport(new Request(url, rest));
}
