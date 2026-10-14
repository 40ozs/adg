/**
 * Directory access as CSV, downloaded from this origin.
 *
 * The JSON proxy (`/api/adg/...`) answers JSON only, and a principal key cannot be part of a
 * file name the browser picks; so the export has its own small handler. It does what the
 * proxy does — attach the session's token, forward, hide nothing — for exactly one API route,
 * and passes the CSV, its file name and its completeness header through unchanged.
 *
 * `mode=summary` is one row per access root. `mode=paths` is every directory the engine
 * evaluated, reachable or not — the complete set behind All paths, never only what a page
 * shows.
 */

import { NextResponse } from "next/server";

import { currentSession } from "@/lib/auth/current";
import { resolveServerApiUrl } from "@/lib/config";

const MODES = new Set(["summary", "paths"]);
const PASSED_HEADERS = ["content-type", "content-disposition", "x-adg-complete", "x-adg-collection-basis"];

export async function GET(request: Request): Promise<Response> {
  const params = new URL(request.url).searchParams;
  const key = params.get("key")?.trim() ?? "";
  const mode = params.get("mode") ?? "summary";
  if (key === "" || !MODES.has(mode)) {
    return NextResponse.json(
      { detail: "An export needs a principal key and a mode of summary or paths." },
      { status: 422 },
    );
  }

  const session = await currentSession();
  if (session === null) {
    return NextResponse.json(
      { detail: "Your session has ended. Sign in again to continue." },
      { status: 401 },
    );
  }

  const url = new URL(
    `${resolveServerApiUrl()}/api/v1/access/principals/${encodeURIComponent(key)}/directory-access/export`,
  );
  url.searchParams.set("mode", mode);

  let upstream: Response;
  try {
    upstream = await fetch(url, {
      headers: { Authorization: `Bearer ${session.accessToken}`, Accept: "text/csv" },
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });
  } catch (error) {
    return NextResponse.json(
      { detail: `The ADG API did not respond: ${error instanceof Error ? error.message : String(error)}` },
      { status: 502 },
    );
  }

  if (!upstream.ok) {
    const body = (await upstream.json().catch(() => null)) as { detail?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail : `The export failed (HTTP ${upstream.status}).`;
    return NextResponse.json({ detail }, { status: upstream.status });
  }

  const headers = new Headers({ "Cache-Control": "private, no-store" });
  for (const name of PASSED_HEADERS) {
    const value = upstream.headers.get(name);
    if (value !== null) {
      headers.set(name, value);
    }
  }
  return new Response(upstream.body, { status: 200, headers });
}
