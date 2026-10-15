"""ADG-DEMO-03: does a truncated comparison report what a full one would? (run at 0cee1a4)"""
import asyncio, datetime as dt, collections
from app.config import build_settings
from app.db import Database
from app.changes.service import ChangeService
import app.changes.service as svc
from app.changes.repository import ChangeRepository

URL = "postgresql+psycopg://adg:adg_ci_password@localhost:5432/adg_demo"
A = dt.datetime(2026, 9, 4, tzinfo=dt.UTC); B = dt.datetime(2026, 10, 2, tzinfo=dt.UTC)

async def main():
    settings = build_settings(environment="test", log_format="text", database_url=URL)
    db = Database(settings)
    async with db.session() as s:
        repo = ChangeRepository(s)
        a, ca = await repo.present_at(A, limit=1_000_000)
        b, cb = await repo.present_at(B, limit=1_000_000)
        print("objects at from:", len(a), "at to:", len(b))
        print("kinds at from:", collections.Counter(v.kind for v in a))
        ta, _ = await repo.present_at(A, limit=5000)
        tb, _ = await repo.present_at(B, limit=5000)
        print("last key from-side:", (ta[-1].kind, ta[-1].key)); print("last key to-side:", (tb[-1].kind, tb[-1].key))
        truncated = await ChangeService(s).compare(A, B)
        svc_ceiling = svc.COMPARISON_CEILING
        svc.COMPARISON_CEILING = 1_000_000
        full = await ChangeService(s).compare(A, B)
        svc.COMPARISON_CEILING = svc_ceiling
        print("truncated: changes", len(truncated.changes), "unchanged", truncated.unchanged, "unobs_from", truncated.unobserved_at_from, "unobs_to", truncated.unobserved_at_to, "truncated", truncated.truncated)
        print("full: changes", len(full.changes), "unchanged", full.unchanged, "unobs_from", full.unobserved_at_from, "unobs_to", full.unobserved_at_to, "truncated", full.truncated)
        fullset = {(c.kind, c.key, c.action, c.after.state_hash if c.after else None) for c in full.changes}
        bogus = [c for c in truncated.changes if (c.kind, c.key, c.action, c.after.state_hash if c.after else None) not in fullset]
        print("changes in truncated result not in full result:", len(bogus))
        for c in bogus[:8]:
            print("  ", c.kind, c.key[:80], c.action, c.significance)
        print("actions truncated:", collections.Counter(str(c.action) for c in truncated.changes))
        print("actions full:", collections.Counter(str(c.action) for c in full.changes))
    await db.dispose()

asyncio.run(main())
