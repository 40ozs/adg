// ADG-UI-STAGING-PROMOTE-01: contract 1.2 against hosted staging, read-only GETs as planner.
//   STAGING_URL=https://… node contract-12.mjs out.json
import fs from "node:fs";

const BASE = process.env.STAGING_URL.replace(/\/+$/, "");
const OUT = process.argv[2];
const results = [];
const evidence = {};
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 600) });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${String(detail).slice(0, 200)}` : ""}`);
};

const FATIMA = "S-1-5-21-3949361974-1583827165-633756269-1323";
const BIANCA = "S-1-5-21-3949361974-1583827165-633756269-1133";
const APOLLO = "\\\\NM-ENG-FS01\\Engineering\\Projects\\Apollo";

const login = await fetch(`${BASE}/auth/dev/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username: "planner" }),
});
const token = (await login.json()).access_token;
check("API sign-in as planner", login.status === 200 && token, `HTTP ${login.status}`);
const auth = { Authorization: `Bearer ${token}` };

async function get(path, query = {}, headers = {}) {
  const url = new URL(`${BASE}/api/v1${path}`);
  for (const [k, v] of Object.entries(query)) url.searchParams.set(k, v);
  const response = await fetch(url, { headers: { ...auth, ...headers } });
  const text = await response.text();
  return { status: response.status, etag: response.headers.get("etag"), body: text ? safe(text) : null };
}
const safe = (t) => { try { return JSON.parse(t); } catch { return t; } };

async function all(kind, query = {}) {
  const items = [];
  let cursor;
  let total;
  do {
    const r = await get("/principals", { kind, limit: "500", ...query, ...(cursor ? { cursor } : {}) });
    if (r.status !== 200) throw new Error(`/principals ${kind} HTTP ${r.status}`);
    items.push(...r.body.items);
    total = r.body.page.total;
    cursor = r.body.page.next_cursor;
  } while (cursor);
  return { items, total };
}

// ---- contract version
const explain = await get("/access/explain", { principal: FATIMA, resource: APOLLO });
check("explain: HTTP 200", explain.status === 200, explain.status);
check("explain: schema_version 1.2", explain.body?.schema_version === "1.2", explain.body?.schema_version);
check("explain: ETag carries contract 1.2", /^"1\.2-/.test(explain.etag ?? ""), explain.etag);

// ---- path_principals in the access explanation (token entries)
const entries = explain.body?.token?.entries ?? [];
const badEntries = entries.filter(
  (e) => !Array.isArray(e.path_principals) || e.path_principals.length !== e.path.length ||
    e.path_principals.some((p, i) => p.key !== e.path[i]),
);
check("explain: every token entry has path_principals keyed exactly as path, in order",
  entries.length > 0 && badEntries.length === 0, `${entries.length} entries, ${badEntries.length} mismatched`);
const invented = entries.flatMap((e) => e.path_principals).filter((p) => !p.resolved && p.display_name);
check("explain: no unresolved hop carries an invented display name", invented.length === 0, invented.length);
const deep = entries.filter((e) => e.depth >= 2).map((e) => e.path_principals.map((p) => p.display_name ?? p.sam_account_name ?? p.sid).join(" → "));
check("explain: multi-hop chains render as names", deep.length > 0 && deep.every((c) => !/undefined|null/.test(c)), deep.slice(0, 3).join(" | "));
evidence.explain_chains = deep;

// ---- path_principals in resource-impact (who can reach Apollo)
const impact = await get(`/access/resources/${encodeURIComponent(APOLLO)}/principals`);
check("resource principals: HTTP 200", impact.status === 200, impact.status);
// ResourcePrincipalsResponse carries no schema_version by design; the paged causal paths do.
const paths = await get("/access/paths", { principal: FATIMA, resource: APOLLO });
check("access paths page: schema_version 1.2", paths.status === 200 && paths.body?.schema_version === "1.2", `${paths.status} ${paths.body?.schema_version}`);
const via = (impact.body?.items ?? []).flatMap((i) => i.via ?? []);
const badVia = via.filter((e) => !Array.isArray(e.path_principals) || e.path_principals.length !== e.path.length ||
  e.path_principals.some((p, i) => p.key !== e.path[i]));
check("resource principals: every via entry has path_principals keyed exactly as path",
  via.length > 0 && badVia.length === 0, `${via.length} via entries, ${badVia.length} mismatched`);
evidence.impact_example = via.filter((e) => e.path.length >= 3).slice(0, 2)
  .map((e) => e.path_principals.map((p) => p.display_name ?? p.sid).join(" → "));

// ---- path_principals in graph effective groups (Bianca's six-level chain)
const groups = await get(`/principals/${BIANCA}/groups`, { scope: "effective", limit: "500" });
const nodes = (groups.body?.items ?? []).filter((n) => Array.isArray(n.path));
const badNodes = nodes.filter((n) => !Array.isArray(n.path_principals) || n.path_principals.length !== n.path.length ||
  n.path_principals.some((p, i) => p.key !== n.path[i]));
const longest = [...nodes].sort((a, b) => b.path.length - a.path.length)[0];
check("effective groups (Bianca): every item has path_principals keyed exactly as path",
  groups.status === 200 && nodes.length > 0 && badNodes.length === 0, `HTTP ${groups.status}, ${nodes.length} items, ${badNodes.length} mismatched`);
check("effective groups (Bianca): the longest chain is six levels of names",
  longest?.depth >= 6 && longest.path_principals.every((p) => p.display_name || p.sam_account_name),
  longest?.path_principals?.map((p) => p.display_name ?? p.sid).join(" → "));
evidence.bianca_longest = longest?.path_principals?.map((p) => p.display_name ?? p.sid);

// ---- the principals directory and exclude_local
const allGroups = await all("groups");
const domainGroups = await all("groups", { exclude_local: "true" });
const users = await all("users");
check("directory: groups total 238", allGroups.total === 238 && allGroups.items.length === 238, `${allGroups.total}/${allGroups.items.length}`);
check("directory: exclude_local total 205", domainGroups.total === 205 && domainGroups.items.length === 205, `${domainGroups.total}/${domainGroups.items.length}`);
const kept = new Set(domainGroups.items.map((p) => p.key));
const removed = allGroups.items.filter((p) => !kept.has(p.key));
const kinds = (list) => list.reduce((m, p) => ({ ...m, [p.kind]: (m[p.kind] ?? 0) + 1 }), {});
check("exclude_local: 33 removed, every one kind local_group", removed.length === 33 && removed.every((p) => p.kind === "local_group"), JSON.stringify(kinds(removed)));
check("exclude_local: no local_group remains", domainGroups.items.every((p) => p.kind !== "local_group"), JSON.stringify(kinds(domainGroups.items)));
const scope = (list) => list.reduce((m, p) => ({ ...m, [p.group_scope ?? "none"]: (m[p.group_scope ?? "none"] ?? 0) + 1 }), {});
const dl = (list) => list.filter((p) => /domain.?local/i.test(p.group_scope ?? "")).length;
check("exclude_local: every AD Domain Local group stays", dl(allGroups.items) > 0 && dl(allGroups.items) === dl(domainGroups.items),
  `domain-local before ${dl(allGroups.items)}, after ${dl(domainGroups.items)}; scopes after ${JSON.stringify(scope(domainGroups.items))}`);
check("exclude_local: kept set ⊂ full set", domainGroups.items.every((p) => allGroups.items.some((q) => q.key === p.key)));
evidence.directory = { users: users.total, groups: allGroups.total, groups_exclude_local: domainGroups.total,
  removed_kinds: kinds(removed), kept_scopes: scope(domainGroups.items) };

// ---- directory search
const nameSearch = await get("/principals", { kind: "users", q: "Fatima" });
check("search: name prefix finds Fatima Gordon", nameSearch.body?.items?.some((p) => p.key === FATIMA), nameSearch.body?.page?.total);
const sidSearch = await get("/principals", { kind: "users", q: FATIMA.slice(0, -1) });
check("search: SID prefix finds Fatima", sidSearch.body?.items?.some((p) => p.key === FATIMA), sidSearch.body?.page?.total);
const fatima = nameSearch.body?.items?.find((p) => p.key === FATIMA);
const netbios = (fatima?.distinguished_name ?? "").match(/DC=([^,]+)/i)?.[1]?.toUpperCase() ?? "NULLMERIDIAN";
const domainSearch = await get("/principals", { kind: "users", q: `${netbios}\\${fatima?.sam_account_name ?? "fatima"}` });
check("search: DOMAIN\\name finds Fatima", domainSearch.body?.items?.some((p) => p.key === FATIMA), `${netbios}\\${fatima?.sam_account_name}`);

// ---- cache transition: a 1.1 validator must refetch once, the 1.2 one must 304
const v11 = (explain.etag ?? "").replace(/^"1\.2-/, '"1.1-');
const stale = await get("/access/explain", { principal: FATIMA, resource: APOLLO }, { "If-None-Match": v11 });
check("cache: a 1.1 validator revalidates to 200 with the 1.2 body", stale.status === 200 && stale.body?.schema_version === "1.2" && stale.etag === explain.etag, `${stale.status} ${stale.etag}`);
const fresh = await get("/access/explain", { principal: FATIMA, resource: APOLLO }, { "If-None-Match": explain.etag });
check("cache: the 1.2 validator then answers 304", fresh.status === 304, fresh.status);
const fresh2 = await get("/access/explain", { principal: FATIMA, resource: APOLLO }, { "If-None-Match": explain.etag });
check("cache: and stays 304 (no repeated invalidation)", fresh2.status === 304, fresh2.status);

const passed = results.filter((r) => r.ok).length;
console.log(`\n${passed}/${results.length} passed`);
fs.writeFileSync(OUT, JSON.stringify({ base: BASE, at: new Date().toISOString(), passed, total: results.length, results, evidence }, null, 1));
process.exit(passed === results.length ? 0 : 1);
