# Risk Rules — AlphaAbsolute
*Hard limits. No agent may override. All BUY/SELL decisions must reference this file.*
*Redundant with Risk.md — this modular version is loaded by subagents directly.*

## Position Sizing Limits (HARD CAPS)

Max single position:     15% of portfolio equity
Hypergrowth Base 0:       5% max
Hypergrowth Base 1:       8% max
Bottom Fish (pre-Stage 2): 4% max
ADTV rule:               size (USD) <= 20% of 6-month average daily trading volume
Theme concentration:     50% of portfolio equity max
Max earnings hold:        3% -- reduce before earnings if >3%

## Stop Loss Rules

Stop placement: -8% from entry = mandatory review flag (reassess thesis)
Stop discipline: NEVER move stop lower to avoid a loss
Consecutive losses: after 3 losing trades, reduce all new sizes by 50% until 2 wins
Max daily drawdown: 3% of total portfolio NAV = trading halt for the day

## Entry Restriction Rules

Earnings within 5 trading days: NO new entry
Stage 3 or 4 detected: NO BUY -- no exceptions, exit review triggered immediately
%>200DMA < 50%: raise cash to 30%+ minimum
%>200DMA < 30%: raise cash to 40%+ minimum
Distribution days >= 4: reduce ALL positions, no new entries
Failed breakout on volume: exit or stop -- do not average down

## ADTV Liquidity Formula

Max position size (USD) = min(15% x equity, 20% x 6M_ADTV_USD)
Thai stocks: ADTV must be > 20M THB
US stocks: ADTV must be > $10M USD

## Wyckoff Gate (mandatory before every BUY)

Stage check: Weinstein Stage 1 or 2 required. Stage 3 or 4 = RED, no entry.
Phase check: Wyckoff Accumulation or Mark-Up required. Distribution or Mark-Down = RED.
Verdict: GREEN (both pass) | YELLOW (Stage 2 but Wyckoff unclear) | RED (Stage 3/4 or Distribution)
If YELLOW or RED: document the re-check trigger (price level or volume signal to watch).

## Exit Priority Order

1. Hard stop hit (-8%) -> immediate exit, no debate
2. Stage change to 3 or 4 -> exit review within same session
3. Distribution days >= 4 -> reduce all, no new entries
4. RS decay from top quartile -> downgrade, reduce on next rally
5. Thesis break (EPS revision negative / revenue decelerating) -> exit within 1 week
