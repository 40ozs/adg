#!/usr/bin/env node
/**
 * Assemble the Worker's static assets (`.edge-assets/`, wrangler.jsonc) from a Next.js build's
 * `static` directory (as `/_next/static`) and the files in `public/`. Cloudflare serves these
 * before the Worker runs.
 *
 * Take them from the same Dockerfile stage Render builds (docker/Dockerfile.render, target
 * `web`), so the chunk names match the HTML Render serves. Two builds are never byte-identical:
 * Next.js draws Server Action IDs at random per build, so the chunk that references them differs.
 * A file missing here is therefore expected and harmless: its path reaches the Worker, which
 * proxies it to Render, whose server holds every file of its own build.
 *
 *   npm run cf:assets -- <path to a .next/static directory>     (default: .next/static)
 */

import { cpSync, existsSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname;
const out = join(root, ".edge-assets");
const built = process.argv[2] ?? join(root, ".next", "static");

if (!existsSync(built)) {
  console.error(`No ${built}: build the application first.`);
  process.exit(1);
}
rmSync(out, { recursive: true, force: true });
cpSync(built, join(out, "_next", "static"), { recursive: true });
for (const name of readdirSync(join(root, "public"))) {
  if (name !== ".gitkeep") {
    cpSync(join(root, "public", name), join(out, name), { recursive: true });
  }
}
// Content-hashed build output never changes under its name (what `next start` sends too).
writeFileSync(join(out, "_headers"), "/_next/static/*\n  Cache-Control: public, max-age=31536000, immutable\n");
console.log(`assets: ${out}`);
