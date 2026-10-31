import { chromium } from "playwright-core";
const BASE = "http://127.0.0.1:13077";
const OUT = process.argv[2];
const results = [];
const check = async (name, fn) => {
  try { const r = await fn(); results.push(`PASS ${name}${r ? ` — ${r}` : ""}`); }
  catch (e) { results.push(`FAIL ${name} — ${e.message}`); }
};
const expect = (ok, msg) => { if (!ok) throw new Error(msg); };
const lum = (c) => { const [r, g, b] = c.match(/[\d.]+/g).slice(0, 3).map(Number).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const browser = await chromium.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const session = (t) => Buffer.from(JSON.stringify({ accessToken: t, expiresAt: "2099-01-01T00:00:00Z", mode: "development" })).toString("base64url");
async function open(token, { width, height, scheme = "light" }) {
  const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: scheme });
  await ctx.addCookies([{ name: "adg_session", value: session(token), url: BASE }]);
  return ctx.newPage();
}
for (const [label, vp] of [["desktop", { width: 1280, height: 900 }], ["phone", { width: 375, height: 812 }]]) {
  for (const scheme of ["light", "dark"]) {
    const page = await open("admin", { ...vp, scheme });
    await page.goto(`${BASE}/risks`);
    const nav = page.getByRole("navigation", { name: "Primary" });
    await check(`${label}/${scheme}: four named groups in order`, async () => {
      const names = await nav.locator("ul[aria-labelledby]").evaluateAll((uls) => uls.map((ul) => document.getElementById(ul.getAttribute("aria-labelledby")).textContent));
      expect(names.join() === "Investigate,Directory,Act,System", names.join());
      return names.join(" / ");
    });
    await check(`${label}/${scheme}: Findings is current, h1 says Findings`, async () => {
      const cur = await nav.locator('[aria-current="page"]').allInnerTexts();
      expect(cur.length === 1 && cur[0] === "Findings", `current=${cur}`);
      const h1 = await page.locator("h1").innerText();
      expect(h1 === "Findings", `h1=${h1}`);
      const w = await nav.locator('[aria-current="page"]').evaluate((a) => getComputedStyle(a).fontWeight);
      expect(Number(w) >= 600, `weight ${w}`);
      return `weight ${w} + accent bar (not colour alone)`;
    });
    await check(`${label}/${scheme}: no horizontal page overflow`, async () => {
      const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
      expect(sw <= iw, `scrollWidth ${sw} > ${iw}`);
      return `${sw} <= ${iw}`;
    });
    await check(`${label}/${scheme}: group label contrast >= 4.5`, async () => {
      const [fg, bg] = await page.locator(".nav-group-label").first().evaluate((el) => {
        let n = el, bg = "rgba(0, 0, 0, 0)";
        while (n && /rgba\(0, 0, 0, 0\)|transparent/.test(bg)) { bg = getComputedStyle(n).backgroundColor; n = n.parentElement; }
        return [getComputedStyle(el).color, bg];
      });
      const r = ratio(fg, bg);
      expect(r >= 4.5, `${fg} on ${bg} = ${r.toFixed(2)}`);
      return `${r.toFixed(2)}:1`;
    });
    await page.screenshot({ path: `${OUT}/nav-${label}-${scheme}.png`, fullPage: false });
    await page.context().close();
  }
}
const page = await open("admin", { width: 1280, height: 900 });
await page.goto(`${BASE}/risks`);
await check("skip link is the first stop and moves focus to main", async () => {
  await page.keyboard.press("Tab");
  const first = await page.evaluate(() => document.activeElement.textContent.trim());
  expect(first === "Skip to main content", first);
  await page.keyboard.press("Enter");
  const id = await page.evaluate(() => document.activeElement.id);
  expect(id === "main", `focus on #${id}`);
});
await check("Tab walks the nav links in group order, no group label takes focus", async () => {
  await page.goto(`${BASE}/risks`);
  const seen = [];
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => ({ inNav: !!document.activeElement.closest('nav[aria-label="Primary"]'), text: document.activeElement.textContent.trim(), tag: document.activeElement.tagName, outline: getComputedStyle(document.activeElement).outlineStyle }));
    if (info.inNav) { expect(info.tag === "A", `focus on ${info.tag}`); expect(info.outline !== "none", `no visible focus on ${info.text}`); seen.push(info.text); }
    else if (seen.length) break;
  }
  const want = "Overview,Findings,Access,Changes,Identities,Resources,Reviews,What-if,Collectors,Settings";
  expect(seen.join() === want, seen.join());
  return `${seen.length} links, visible focus outline on each`;
});
await check("Enter on a focused nav link navigates (native link)", async () => {
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Changes" }).focus();
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/changes$/);
  const cur = await page.locator('nav [aria-current="page"]').innerText();
  expect(cur === "Changes", cur);
});
for (const [path, want] of [["/risks/alerts", "Findings"], ["/risks?finding=does-not-exist", "Findings"], ["/identities/users", "Identities"], ["/resources", "Resources"], ["/simulations", "What-if"], ["/governance", "Reviews"]]) {
  await check(`deep link ${path} marks ${want}`, async () => {
    const r = await page.goto(`${BASE}${path}`);
    expect(r.status() < 500, `HTTP ${r.status()}`);
    const cur = await page.locator('nav[aria-label="Primary"] [aria-current="page"]').innerText();
    expect(cur === want, cur);
    return `HTTP ${r.status()}`;
  });
}
await page.context().close();
const viewer = await open("viewer", { width: 1280, height: 900 });
await viewer.goto(`${BASE}/`);
await check("viewer: no Act group, no Settings, no What-if/Reviews links", async () => {
  const nav = viewer.getByRole("navigation", { name: "Primary" });
  const text = await nav.innerText();
  expect(!/\bAct\b|Settings|What-if|Reviews/.test(text), text.replace(/\n/g, " | "));
  return text.replace(/\n/g, " | ");
});
await viewer.screenshot({ path: `${OUT}/nav-viewer-desktop.png` });
await browser.close();
console.log(results.join("\n"));
process.exit(results.some((r) => r.startsWith("FAIL")) ? 1 : 0);
