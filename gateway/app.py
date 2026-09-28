"""Unified LLM API gateway for internal agents.

per-agent key -> identity (agent, team, project) -> rate limit -> budget (degrade / block)
-> tier routing with fallback -> PII redaction -> provider -> ledger + Prometheus metrics
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import time
from collections import defaultdict, deque
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from pydantic import BaseModel

from .ledger import Ledger
from .providers import ProviderError
from .redact import redact


class ChatRequest(BaseModel):
    tier: str = "fast"
    messages: list[dict]
    max_tokens: int = 512


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def month_start() -> float:
    now = dt.datetime.now(dt.timezone.utc)
    return dt.datetime(now.year, now.month, 1, tzinfo=dt.timezone.utc).timestamp()


def create_app(config: dict | None = None, providers: dict | None = None, ledger: Ledger | None = None, env: str = "dev") -> FastAPI:
    cfg = config or yaml.safe_load((Path(__file__).parent / "config.yaml").read_text())
    agents = {a["key_sha256"]: a for a in cfg["agents"] if a.get("key_sha256")}
    providers = providers or {}
    ledger = ledger or Ledger(os.environ.get("GATEWAY_DB", ":memory:"))
    hits: dict[str, deque] = defaultdict(deque)

    reg = CollectorRegistry()
    m_req = Counter("gw_requests_total", "requests", ["team", "agent", "model", "status"], registry=reg)
    m_cost = Counter("gw_cost_usd_total", "cost in USD", ["team", "agent", "project"], registry=reg)
    m_lat = Histogram("gw_latency_seconds", "provider latency", ["provider"], registry=reg)
    m_fallback = Counter("gw_fallbacks_total", "fallbacks", ["from_provider"], registry=reg)

    app = FastAPI(title="agent-llm-gateway")

    def identity(authorization: str = Header(default="")) -> dict:
        key = authorization.removeprefix("Bearer ").strip()
        agent = agents.get(sha(key)) if key else None
        if not agent:
            raise HTTPException(401, "unknown agent key")
        return agent

    @app.post("/v1/chat")
    def chat(req: ChatRequest, agent: dict = Depends(identity)):
        aid = agent["id"]
        # 1) rate limit (sliding 60 s window, per agent)
        now, q = time.time(), hits[aid]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= agent["rpm"]:
            m_req.labels(agent["team"], aid, "-", "rate_limited").inc()
            raise HTTPException(429, "rate limit")
        q.append(now)
        # 2) tier permission + budget
        if req.tier not in agent["allowed_tiers"]:
            raise HTTPException(403, f"tier {req.tier} not allowed for {aid}")
        used = ledger.spent(aid, month_start()) / agent["monthly_budget_usd"]
        if used >= cfg["budget"]["block_at"]:
            m_req.labels(agent["team"], aid, "-", "budget_blocked").inc()
            raise HTTPException(429, "monthly budget exhausted")
        tier, degraded = req.tier, 0
        if used >= cfg["budget"]["degrade_at"] and tier == "smart":
            tier, degraded = "fast", 1
        # 3) redact before anything leaves the boundary
        messages = []
        for m in req.messages:
            text, _ = redact(m["content"])
            messages.append({"role": m["role"], "content": text})
        # 4) route with fallback
        errors = []
        for prov_name, model in cfg["tiers"][tier]:
            prov = providers.get(prov_name)
            if prov is None:
                continue
            t0 = time.perf_counter()
            try:
                out = prov.complete(model, messages, req.max_tokens)
            except ProviderError as e:
                errors.append(f"{prov_name}: {e}")
                m_fallback.labels(prov_name).inc()
                continue
            m_lat.labels(prov_name).observe(time.perf_counter() - t0)
            pin, pout = cfg["prices"][model]
            cost = (out.input_tokens * pin + out.output_tokens * pout) / 1e6
            ledger.add(agent=aid, team=agent["team"], project=agent["project"], env=env, tier=tier, provider=prov_name,
                       model=model, in_tok=out.input_tokens, out_tok=out.output_tokens, cost_usd=cost, status="ok", degraded=degraded)
            m_req.labels(agent["team"], aid, model, "ok").inc()
            m_cost.labels(agent["team"], aid, agent["project"]).inc(cost)
            return {"text": out.text, "model": model, "provider": prov_name, "degraded": bool(degraded),
                    "usage": {"input_tokens": out.input_tokens, "output_tokens": out.output_tokens, "cost_usd": round(cost, 8)}}
        m_req.labels(agent["team"], aid, "-", "all_providers_failed").inc()
        raise HTTPException(502, {"error": "all providers failed", "attempts": errors})

    @app.get("/v1/usage")
    def usage(by: str = "team"):
        return ledger.by(by)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/metrics")
    def metrics():
        return PlainTextResponse(generate_latest(reg), media_type=CONTENT_TYPE_LATEST)

    return app
