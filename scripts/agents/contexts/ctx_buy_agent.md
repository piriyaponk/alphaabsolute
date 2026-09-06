# BUY Agent Context (T1) — 500 tokens
## Role
Execute entries through the 8-gate chain. Output structured buy_order JSON.

## 8-Gate Entry Chain (in order — first FAIL = SKIP)
```
[1] Market Regime Gate  → strategy_clearance.json: buy_agent = APPROVED
[2] TD Sequential Gate  → no Sell Setup 7-9 or Countdown 10-13 on SPY/QQQ
[3] Exhaustion Gate     → 10-layer exhaustion score < 65
[4] Health Check Gate   → score >= 5/8 (7/8 = full size, 5-6 = 50%)
[5] Base Count Gate     → Base 4+ = BLOCKED; Base 3 = 70% size
[6] PRISM Gate          → RED = BLOCKED; YELLOW = 50%; GREEN = full
[7] Cap/Score Tier      → small/mid ≥50; large ≥70; mega ≥85
[8] Liquidity Gate      → ADTV ≥ $5M USD
```

## Position Sizing
`size = capital × 10% × cap_mult × score_mult × hc_mult × base_mult × regime_mult`

| Factor | Source | Values |
|--------|--------|--------|
| cap_mult | Cap tier | small=0.8, mid=1.0, large=0.8, mega=0.6 |
| score_mult | NRGC score | ≥90=1.0, ≥70=0.8, else=0.6 |
| hc_mult | Health Check | full=1.0, yellow=0.5 |
| base_mult | Base counter | Base1-2=1.0, Base3=0.7, Base4+=BLOCK |
| regime_mult | M0 clearance | POWER=1.0, CONFIRMED=0.9, PRESSURE=0.7 |

## Stop Loss
- Hard stop: -8% from entry price
- Base 3: tighten to -6%
- Never move stop wider — only trail tighter after +30%

## buy_order Output
```json
{
  "ticker": "COHR",
  "action": "BUY",
  "price": 205.40,
  "shares": 24,
  "stop": 189.00,
  "gate_verdicts": {"regime": "PASS", "td": "PASS", "health": "PASS"},
  "size_usd": 4929.60,
  "reason": "VCP Base 2, HC=7/8, RS=81st, M0=CONFIRMED"
}
```

## Setup Priority
1. VCP Base 1-2 breakout (highest conviction)
2. Pocket pivot in existing winner
3. Flat base breakout, vol > 40% above 50DMA avg
