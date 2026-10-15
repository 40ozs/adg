"""ADG-DEMO-03 after: the bounded comparison through the API on the reset Null Meridian estate."""
import asyncio, json, sys, time, collections
from httpx import ASGITransport, AsyncClient
import sqlalchemy as sa
from app.config import build_settings
from app.main import create_app
from tests.support.auth import auth_headers

URL = "postgresql+psycopg://adg:adg_ci_password@localhost:5432/adg_demo"
G1, G3 = "2026-09-04T00:00:00Z", "2026-10-02T00:00:00Z"
CASES = [
    ("estate g1->g3, standard window", {"from": G1, "to": G3}),
    ("estate g1->g3, 1 ms drill window", {"from": G1, "to": G3, "time_budget_ms": "1"}),
    ("kind ntfs_ace g1->g3", {"from": G1, "to": G3, "kind": "ntfs_ace"}),
    ("kind smb_ace g1->g3", {"from": G1, "to": G3, "kind": "smb_ace"}),
    ("server NM-ENG-FS01 g1->g3", {"from": G1, "to": G3, "server": "NM-ENG-FS01"}),
    ("estate g2->g3", {"from": "2026-09-18T00:00:00Z", "to": G3}),
]

async def main(repeat: int, out: str) -> None:
    settings = build_settings(environment="test", log_format="text", database_url=URL, runtime_mode="demo")
    app = create_app(settings)
    headers = auth_headers(settings)
    rows = []
    async with app.router.lifespan_context(app):
        engine = app.state.database.engine.sync_engine
        st = {"n": 0, "sql": 0.0}
        @sa.event.listens_for(engine, "before_cursor_execute")
        def b(conn, cur, s, p, ctx, em): ctx._t0 = time.perf_counter()
        @sa.event.listens_for(engine, "after_cursor_execute")
        def a(conn, cur, s, p, ctx, em):
            st["n"] += 1; st["sql"] += time.perf_counter() - ctx._t0
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", timeout=600) as c:
            for name, params in CASES:
                for i in range(repeat):
                    st["n"] = 0; st["sql"] = 0.0
                    t0 = time.perf_counter()
                    r = await c.get("/api/v1/changes/compare", params=params, headers=headers)
                    wall = time.perf_counter() - t0
                    body = r.json()
                    rec = {"case": name, "run": i, "status": r.status_code, "wall_ms": round(wall * 1000, 1),
                           "statements": st["n"], "sql_ms": round(st["sql"] * 1000, 1), "bytes": len(r.content)}
                    if r.status_code == 200:
                        for k in ("outcome", "complete", "stopped_in", "compared_through", "window_ms", "elapsed_ms",
                                  "objects_read_from", "objects_read_to", "differences_found", "unchanged",
                                  "unobserved_at_from", "unobserved_at_to"):
                            rec[k] = body[k]
                        rec["changes"] = len(body["changes"])
                        rec["recovery"] = [(x["action"], x["parameters"].get("kind"), x.get("objects_at_from"), x.get("objects_at_to")) for x in body["recovery"]]
                    else:
                        rec["detail"] = str(body)[:300]
                    rows.append(rec)
    with open(out, "w") as f:
        for rec in rows:
            f.write(json.dumps(rec) + "\n")

asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
