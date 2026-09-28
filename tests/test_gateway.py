import copy

import pytest
import yaml
from fastapi.testclient import TestClient

from gateway.app import create_app, sha
from gateway.ledger import Ledger
from gateway.providers import MockProvider
from gateway.redact import redact

from pathlib import Path

BASE = yaml.safe_load((Path(__file__).resolve().parent.parent / "gateway" / "config.yaml").read_text())
KEYS = {"search-assistant": "key-search", "store-ops-bot": "key-ops"}


def make(fail=(), cfg_patch=None):
    cfg = copy.deepcopy(BASE)
    for a in cfg["agents"]:
        a["key_sha256"] = sha(KEYS[a["id"]])
    if cfg_patch:
        cfg_patch(cfg)
    provs = {n: MockProvider(n, fail=n in fail) for n in ("anthropic", "openai_compat")}
    ledger = Ledger()
    client = TestClient(create_app(cfg, provs, ledger))
    client.cfg = cfg
    return client, ledger


def call(client, key, tier="fast", content="hello"):
    return client.post("/v1/chat", json={"tier": tier, "messages": [{"role": "user", "content": content}]},
                       headers={"Authorization": f"Bearer {key}"})


def test_unknown_key_rejected():
    c, _ = make()
    assert call(c, "nope").status_code == 401


def test_tier_permission():
    c, _ = make()
    assert call(c, KEYS["store-ops-bot"], tier="smart").status_code == 403


def test_routing_and_cost_attribution():
    c, ledger = make()
    r = call(c, KEYS["search-assistant"], tier="smart").json()
    assert r["model"] == "claude-sonnet-5" and r["usage"]["cost_usd"] > 0
    call(c, KEYS["store-ops-bot"])
    teams = {row["team"]: row for row in ledger.by("team")}
    assert set(teams) == {"ecommerce", "retail-ops"} and teams["ecommerce"]["calls"] == 1


def test_fallback_when_primary_fails():
    c, _ = make(fail=("anthropic",))
    r = call(c, KEYS["search-assistant"], tier="smart").json()
    assert r["provider"] == "openai_compat" and r["model"] == "gpt-5-mini"
    assert 'gw_fallbacks_total{from_provider="anthropic"} 1.0' in c.get("/metrics").text


def test_all_providers_down_is_502():
    c, _ = make(fail=("anthropic", "openai_compat"))
    assert call(c, KEYS["search-assistant"]).status_code == 502


def test_rate_limit():
    c, _ = make()
    codes = [call(c, KEYS["store-ops-bot"]).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


def test_budget_degrades_then_blocks():
    c, ledger = make()
    agent = c.cfg["agents"][0]
    first = call(c, KEYS["search-assistant"], tier="smart", content="x" * 400).json()
    assert first["degraded"] is False and first["model"] == "claude-sonnet-5"
    spent = first["usage"]["cost_usd"]
    agent["monthly_budget_usd"] = spent / 0.85          # now at 85% of budget
    second = call(c, KEYS["search-assistant"], tier="smart").json()
    assert second["degraded"] is True and second["model"] == "gpt-5-mini"   # smart served by the fast tier
    agent["monthly_budget_usd"] = spent                  # now at >=100%
    assert call(c, KEYS["search-assistant"], tier="smart").status_code == 429


def test_pii_redacted_before_provider():
    seen = {}

    class Spy(MockProvider):
        def complete(self, model, messages, max_tokens):
            seen["text"] = messages[0]["content"]
            return super().complete(model, messages, max_tokens)

    cfg = copy.deepcopy(BASE)
    for a in cfg["agents"]:
        a["key_sha256"] = sha(KEYS[a["id"]])
    c = TestClient(create_app(cfg, {"openai_compat": Spy("openai_compat"), "anthropic": Spy("anthropic")}, Ledger()))
    call(c, KEYS["search-assistant"], content="customer A123456789 mail a@b.com phone 0912-345-678")
    assert "A123456789" not in seen["text"] and "<TW_ID>" in seen["text"] and "<EMAIL>" in seen["text"] and "<PHONE>" in seen["text"]


def test_redact_counts():
    _, n = redact("a@b.com and c@d.org")
    assert n == 2
