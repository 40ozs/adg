"""Every field that differs between two Demo Mode reset reports (``python -m app.demo_mode reset
--report``): ``python reset_report_diff.py OLD.json NEW.json``."""
import json, sys

def walk(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for k in sorted(set(a) | set(b)) for d in walk(a.get(k), b.get(k), f"{path}/{k}")]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in walk(x, y, f"{path}[{i}]")]
    return [] if a == b else [{"path": path, "old": a, "new": b}]

old, new = (json.load(open(p)) for p in sys.argv[1:3])
differences = walk(old, new)
print(json.dumps({"differences": differences,
                  "unchanged_sections": sorted(k for k in new if not any(d["path"].startswith(f"/{k}") for d in differences))},
                 indent=1, ensure_ascii=False))
