#!/usr/bin/env node
/**
 * ADG-HOSTED-STREAMING-11: applies the hosted pass rule to stream-probe.mjs output. Per run:
 * the shell (h1) and a loading placeholder arrived, at least one resolved section arrived, a
 * different section arrived at least MIN_GAP_MS after the first, and the response completed 200.
 * The section that came first is the run's "fast" section and the last one its "later" section;
 * the hosted path names neither in advance.
 *
 *   node analyze.mjs hosted-overview.gzip.json … > summary.json   (MIN_GAP_MS=100)
 */
import { readFileSync } from "node:fs";

const MIN_GAP = Number(process.env.MIN_GAP_MS ?? 100);
const SECTIONS = ["health", "attention", "changes", "reviews"];
const out = [];
for (const file of process.argv.slice(2)) {
  const doc = JSON.parse(readFileSync(file, "utf8"));
  for (const [i, r] of doc.runs.entries()) {
    const m = r.markers;
    const seen = SECTIONS.filter((s) => m[s] !== undefined).sort((a, b) => m[a] - m[b]);
    const first = seen[0], last = seen.at(-1);
    const checks = {
      status_200: r.status === 200,
      shell: m.h1 !== undefined,
      placeholder_with_shell: m.placeholder !== undefined && m.placeholder <= (m[first] ?? Infinity),
      fast_section: first !== undefined,
      later_distinct_section: last !== undefined && last !== first,
      gap_ms_ge_min: last !== undefined && m[last] - m.h1 >= MIN_GAP && m[last] - m[first] >= MIN_GAP,
      complete: r.complete_ms !== null && m.html_end !== undefined,
    };
    out.push({
      file: file.split("/").pop(), encoding: doc.encoding, run: i + 1, ok: Object.values(checks).every(Boolean), checks,
      requested: doc.encoding, returned: r.headers["content-encoding"] ?? "identity", cf_cache: r.headers["cf-cache-status"], alpn: r.alpn,
      headers_ms: r.headers_ms, first_byte_ms: r.first_byte_ms, shell_ms: m.h1, placeholder_ms: m.placeholder,
      first_section: first, first_section_ms: m[first], last_section: last, last_section_ms: m[last], order: seen,
      shell_to_last_ms: m[last] - m.h1, first_to_last_ms: m[last] - m[first], complete_ms: r.complete_ms, chunks: r.chunks.length,
    });
  }
}
console.log(JSON.stringify(out, null, 1));
for (const r of out) console.error(`${r.ok ? "PASS" : "FAIL"} ${r.encoding.padEnd(8)} run ${r.run}: hdr ${r.headers_ms} shell ${r.shell_ms} ${r.first_section} ${r.first_section_ms} … ${r.last_section} ${r.last_section_ms} (+${r.shell_to_last_ms}) done ${r.complete_ms} enc ${r.returned} chunks ${r.chunks}${r.ok ? "" : " " + JSON.stringify(Object.entries(r.checks).filter(([, v]) => !v).map(([k]) => k))}`);
