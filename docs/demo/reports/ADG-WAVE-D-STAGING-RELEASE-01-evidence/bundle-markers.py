"""Which Wave D client-code markers a Next.js build carries.

NAV-07: the campaign's item link is a next/link with prefetch off -> `prefetch:!1` beside the
`/governance/items/` href. REVIEW-NAMES-08: scopeLabel reads `display_name` beside the
"(name not loaded)" fallback. Counts only; prints chunk names, never bodies.

    python3 -I bundle-markers.py local <.next/static dir> [...]
    COOKIE=... python3 -I bundle-markers.py hosted <base url> <page path> [...]   (session cookie from env, never printed)
"""

import json
import os
import pathlib
import re
import sys
import urllib.request

UA = {"User-Agent": "adg-wave-d-release-check/1.0", **({"Cookie": os.environ["COOKIE"]} if os.environ.get("COOKIE") else {})}


def markers(name: str, text: str) -> dict[str, object]:
    out: dict[str, object] = {}
    for m in re.finditer(r"/governance/items/", text):
        window = text[max(0, m.start() - 250): m.start() + 250]
        out["item_href_with_prefetch_off"] = out.get("item_href_with_prefetch_off", False) or "prefetch:!1" in window
        out["item_href"] = True
    i = text.find("name not loaded")
    if i >= 0:
        out["fallback"] = True
        out["display_name_beside_fallback"] = "display_name" in text[max(0, i - 400): i + 50]
    return {"chunk": name, **out} if out else {}


def local(dirs: list[str]) -> list[dict[str, object]]:
    rows = []
    for d in dirs:
        for f in sorted(pathlib.Path(d).rglob("*.js")):
            row = markers(f.name, f.read_text(errors="replace"))
            if row:
                rows.append({"build": d, **row})
    return rows


def hosted(base: str, paths: list[str]) -> list[dict[str, object]]:
    seen, rows = set(), []
    for path in paths:
        html = urllib.request.urlopen(urllib.request.Request(base + path, headers=UA)).read().decode()
        # <script src> tags plus the chunks the inline RSC flight data names.
        found = re.findall(r'/_next/static/[^"\\ ]+?\.js', html) + ["/_next/" + c for c in re.findall(r'static/chunks/[^"\\ ]+?\.js', html)]
        for src in dict.fromkeys(found):
            if src in seen:
                continue
            seen.add(src)
            text = urllib.request.urlopen(urllib.request.Request(base + src, headers=UA)).read().decode(errors="replace")
            row = markers(src.rsplit("/", 1)[-1], text)
            if row:
                rows.append({"page": path, **row})
    print(f"# scanned {len(seen)} hosted scripts", file=sys.stderr)
    return rows


if __name__ == "__main__":
    mode, *rest = sys.argv[1:]
    rows = local(rest) if mode == "local" else hosted(rest[0].rstrip("/"), rest[1:])
    print(json.dumps(rows, indent=1))
