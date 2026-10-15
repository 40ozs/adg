"""ADG-DEMO-03 baseline: GET /api/v1/changes/compare on the reset Null Meridian estate (run at 0cee1a4)."""
import asyncio, json, sys, time, collections
from httpx import ASGITransport, AsyncClient
import sqlalchemy as sa
from app.config import build_settings
from app.main import create_app
from tests.support.auth import auth_headers

URL = "postgresql+psycopg://adg:adg_ci_password@localhost:5432/adg_demo"

WINDOWS = [
    ("g1->g3 estate", {"from": "2026-09-04T00:00:00Z", "to": "2026-10-02T00:00:00Z"}),
    ("g1->g2 estate", {"from": "2026-09-04T00:00:00Z", "to": "2026-09-18T00:00:00Z"}),
    ("g2->g3 estate", {"from": "2026-09-18T00:00:00Z", "to": "2026-10-02T00:00:00Z"}),
    ("g1->g3 server NM-ENG-FS01", {"from": "2026-09-04T00:00:00Z", "to": "2026-10-02T00:00:00Z", "server": "NM-ENG-FS01"}),
    ("g1->g3 share Engineering", {"from": "2026-09-04T00:00:00Z", "to": "2026-10-02T00:00:00Z", "share": r"\\NM-ENG-FS01\Engineering"}),
]

async def main(repeat: int) -> None:
    settings = build_settings(environment="test", log_format="text", database_url=URL, runtime_mode="demo")
    app = create_app(settings)
    headers = auth_headers(settings)
    async with app.router.lifespan_context(app):
        engine = app.state.database.engine.sync_engine
        stats = {"n": 0, "sql_s": 0.0, "by": collections.Counter(), "t_by": collections.defaultdict(float)}
        @sa.event.listens_for(engine, "before_cursor_execute")
        def before(conn, cursor, statement, parameters, context, executemany):
            context._t0 = time.perf_counter()
        @sa.event.listens_for(engine, "after_cursor_execute")
        def after(conn, cursor, statement, parameters, context, executemany):
            dt_ = time.perf_counter() - context._t0
            stats["n"] += 1; stats["sql_s"] += dt_
            head = " ".join(statement.split())[:90]
            stats["by"][head] += 1; stats["t_by"][head] += dt_
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=600) as client:
            out = []
            for name, params in WINDOWS:
                for i in range(repeat):
                    stats["n"] = 0; stats["sql_s"] = 0.0; stats["by"].clear(); stats["t_by"].clear()
                    t0 = time.perf_counter()
                    r = await client.get("/api/v1/changes/compare", params=params, headers=headers)
                    wall = time.perf_counter() - t0
                    body = r.json()
                    size = len(r.content)
                    rec = {"window": name, "run": i, "status": r.status_code, "wall_ms": round(wall*1000,1),
                           "statements": stats["n"], "sql_ms": round(stats["sql_s"]*1000,1), "bytes": size}
                    if r.status_code == 200:
                        rec.update({"changes": len(body["changes"]), "unchanged": body["unchanged"],
                            "unobserved_at_from": body["unobserved_at_from"], "unobserved_at_to": body["unobserved_at_to"],
                            "truncated": body["truncated"]})
                    else:
                        rec["detail"] = str(body)[:200]
                    top = sorted(stats["t_by"].items(), key=lambda kv: -kv[1])[:4]
                    rec["top_sql"] = [(k[:70], stats["by"][k], round(v*1000,1)) for k, v in top]
                    out.append(rec)
                    print(json.dumps(rec), flush=True)

asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 1))
