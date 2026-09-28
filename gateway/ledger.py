"""Usage ledger: one row per call with cost-attribution labels. SQLite here; BigQuery/Postgres in production."""
from __future__ import annotations

import sqlite3
import time


class Ledger:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""create table if not exists usage(ts real, agent text, team text, project text, env text,
                           tier text, provider text, model text, in_tok int, out_tok int, cost_usd real, status text, degraded int)""")

    def add(self, **row) -> None:
        cols = ",".join(row)
        self.db.execute(f"insert into usage(ts,{cols}) values(?,{','.join('?' * len(row))})", (time.time(), *row.values()))
        self.db.commit()

    def spent(self, agent: str, since: float) -> float:
        (v,) = self.db.execute("select coalesce(sum(cost_usd),0) from usage where agent=? and ts>=?", (agent, since)).fetchone()
        return v

    def by(self, dim: str) -> list[dict]:
        assert dim in {"team", "agent", "project", "model"}
        rows = self.db.execute(f"select {dim}, count(*), sum(in_tok), sum(out_tok), round(sum(cost_usd),6) from usage group by {dim} order by 5 desc")
        return [{dim: r[0], "calls": r[1], "in_tok": r[2], "out_tok": r[3], "cost_usd": r[4]} for r in rows]
