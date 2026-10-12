// OpenNext's Cloudflare adapter: builds the unmodified Next.js application into
// .open-next/worker.js, which cloudflare/worker.ts serves. No incremental cache is configured:
// every ADG page is rendered per request (permission data is never cached), so there is
// nothing for one to hold. See docs/deployment/cloudflare-demo.md.
import { defineCloudflareConfig } from "@opennextjs/cloudflare";

export default defineCloudflareConfig({});
