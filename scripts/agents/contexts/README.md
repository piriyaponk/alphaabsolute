# Micro-Context Files — Agent Token Efficiency Layer

## Purpose
These files replace loading the full CLAUDE.md (5,000+ tokens) for every agent call.
Each file is 400-600 tokens — targeting 90% context savings.

## Usage Pattern (Python SDK)
```python
ctx = Path("scripts/agents/contexts/ctx_buy_agent.md").read_text()
response = client.messages.create(
    model="claude-haiku-4-5",          # Haiku for screening ($0.25/M)
    system=ctx,                         # micro-context only
    messages=[{"role": "user", "content": f"Analyse {ticker}. Data: {json_data}"}]
)
```

## Available Contexts

| File | Agent | Tokens | Purpose |
|------|-------|--------|---------|
| ctx_regime.md | M0 | ~400 | 7-state regime + leading indicators |
| ctx_buy_agent.md | T1 | ~500 | 8-gate entry chain + sizing formulas |
| ctx_sell_agent.md | T2 | ~500 | Priority sell stack + trailing stops |
| ctx_canslim.md | CANSLIM | ~500 | C+A+N+S+L+I scoring weights |
| ctx_rs_inflection.md | I1 | ~400 | RS ranking + inflection detection |
| ctx_base_watchdog.md | I4 | ~400 | VCP lifecycle + HOT criteria |
| ctx_nrgc_phases.md | W1 | ~400 | 7-phase NRGC classification |
| ctx_bear_detection.md | M0/T3 | ~450 | 6 leading indicators + cash rules |
| ctx_position_sizing.md | T1/T3 | ~400 | 5-multiplier sizing framework |

## Model Tiering (cost optimization)
| Task | Model | Cost/M tokens |
|------|-------|---------------|
| Screening, formulas, JSON parsing | claude-haiku-4-5 | $0.25/$1.25 |
| Stock analysis, narrative scoring | claude-sonnet-4-5 | $3/$15 |
| CIO synthesis, final decisions | claude-opus-4-5 | $15/$75 |

## Pre-Computed Data (read these JSONs — $0 cost)
- `data/market_regime/strategy_clearance.json` — M0 regime
- `data/canslim_scores/_summary.json` — CANSLIM scores
- `data/base_watchdog/latest.json` — VCP alerts
- `data/rs_universe/latest.json` — RS ranks + inflections
- `data/nrgc/state/{TICKER}.json` — NRGC phase per stock
- `data/base_counts/{TICKER}.json` — base count per stock
- `data/health_checks/{TICKER}.json` — health check score
- `data/td_sequential/_market_regime.json` — TD market signal

## Anti-Pattern (avoid)
```python
# ❌ WRONG — loads full CLAUDE.md = 5,000+ tokens × every call = expensive
system = Path("CLAUDE.md").read_text()

# ✅ CORRECT — load only what the agent needs
system = Path("scripts/agents/contexts/ctx_buy_agent.md").read_text()
```
