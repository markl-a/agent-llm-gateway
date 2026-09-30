# agent-llm-gateway

**One gateway for every internal AI agent: per-agent identity, rate limits, monthly budgets that degrade before they block, tier routing with provider fallback, PII redaction, and cost attribution by team / agent / project.**

Portfolio demo (Sept 2026). FastAPI + SQLite ledger + Prometheus metrics. 9 tests run offline with mock providers.

```
agent ──Bearer <per-agent key>──► /v1/chat
   1 identity   key → {agent, team, project, budget, rpm, allowed tiers}
   2 rate limit per-agent sliding window
   3 budget     ≥80% of monthly budget: "smart" is served by "fast"   ≥100%: 429
   4 redact     regex masks TW national ID / email / TW mobile / card-number formats before the prompt leaves (pattern-based, not a full PII detector)
   5 route      tier = ordered [provider, model] list, tried in order; on a provider error the next one is used (no health probing)
   6 account    ledger row per call (labels + tokens + USD) · Prometheus counters
```

## Why these choices

- **Keys identify an agent, not a team.** Cost attribution and blast radius are per agent; a leaked key affects one agent.
- **Degrade before block.** Cutting an agent off mid-month breaks a business flow; routing it to a cheaper tier keeps it working while the owner is alerted.
- **Tiers, not model names.** Agents ask for `fast` or `smart`. Swapping a model or provider is a config change and does not touch agent code.
- **Prices live in config.** Cost is computed from actual token counts returned by the provider.
- **Redaction happens in the gateway**, so it is enforced once rather than trusted to every agent. The patterns cover common formats only; names and free-text identifiers still need a proper detector.

## Run

```bash
pip install -r requirements.txt
pytest -q
ANTHROPIC_API_KEY=... OPENAI_API_KEY=... uvicorn gateway.main:app --port 8080
curl -H "Authorization: Bearer <key>" -d '{"tier":"smart","messages":[{"role":"user","content":"hi"}]}' localhost:8080/v1/chat
curl localhost:8080/v1/usage?by=team      # cost by team / agent / project / model
curl localhost:8080/metrics               # gw_requests_total, gw_cost_usd_total, gw_latency_seconds, gw_fallbacks_total
```

## Tests cover

unknown key → 401 · tier not allowed → 403 · routing + per-team cost rows · fallback when primary fails (and the fallback metric) · all providers down → 502 · rate limit → 429 · budget: normal → degraded at 85% → 429 at 100% · PII redacted before reaching the provider

## Not done here (would be next)

Streaming responses · Redis-backed rate limit for multiple replicas · semantic cache · per-agent prompt/response retention policy · budget alerts to chat.
Deployment to GKE with per-agent Workload Identity is in the companion repo **agent-platform-gke**.
