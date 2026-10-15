import asyncio, datetime as dt, time, collections, sys  # ADG-DEMO-03: full vs truncated cost (run at 0cee1a4)
import sqlalchemy as sa
from app.config import build_settings
from app.db import Database
from app.changes.service import ChangeService
import app.changes.service as svc

URL = "postgresql+psycopg://adg:adg_ci_password@localhost:5432/adg_demo"
A = dt.datetime(2026, 9, 4, tzinfo=dt.UTC); B = dt.datetime(2026, 10, 2, tzinfo=dt.UTC)

async def main():
    settings = build_settings(environment="test", log_format="text", database_url=URL)
    db = Database(settings)
    stats = collections.Counter(); tt = collections.defaultdict(float)
    eng = db.engine.sync_engine
    @sa.event.listens_for(eng, "before_cursor_execute")
    def b(conn, cur, st, p, ctx, em): ctx._t0 = time.perf_counter()
    @sa.event.listens_for(eng, "after_cursor_execute")
    def a(conn, cur, st, p, ctx, em):
        k = " ".join(st.split())[:60]; stats[k] += 1; tt[k] += time.perf_counter() - ctx._t0
    for ceiling in [5_000, 20_000]:
        svc.COMPARISON_CEILING = ceiling
        for i in range(3):
            stats.clear(); tt.clear()
            async with db.session() as s:
                t0 = time.perf_counter()
                r = await ChangeService(s).compare(A, B)
                wall = time.perf_counter() - t0
            print(f"ceiling={ceiling} run={i} wall_ms={wall*1000:.0f} statements={sum(stats.values())} sql_ms={sum(tt.values())*1000:.0f} changes={len(r.changes)} truncated={r.truncated}")
            if i == 2:
                for k, v in sorted(tt.items(), key=lambda kv: -kv[1])[:5]:
                    print(f"    {stats[k]:>4} x {v*1000:7.1f} ms  {k}")
    await db.dispose()
asyncio.run(main())
