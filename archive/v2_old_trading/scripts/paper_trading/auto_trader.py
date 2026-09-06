"""
AlphaAbsolute — Auto Paper Trader (Fund Model)
Philosophy: Beat Nasdaq with low drawdown. Cash is a position.
            Mid/small cap bias for alpha. Only act on strong NRGC/PULSE signals.

Entry  : Phase 3 + score >= 50 + PULSE GREEN + regime OK + cap preference
         + TD Sequential gate (no Sell Setup 7-9, no Sell Countdown 10-13)
         + Health Check >= 5/8 (7/8 for full size)
Exit   : Hard stop -8% | Phase 5/6/7 | Trail after +30% | Regime risk-off + weak position
         + TD Sell Countdown 13 on individual stock = immediate reduce
Cash   : Raise when regime = risk-off, no setups, or market breadth deteriorating.
         TD market-level warning also raises cash floor.
No LLM — pure rule-based. Fast.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

# ── TD Sequential + Health Check (primary entry indicators) ────────────────────
# Lazy import to avoid startup errors if deps missing
_TD_MODULE  = None
_HC_MODULE  = None

def _get_td_module():
    global _TD_MODULE
    if _TD_MODULE is None:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "learning"))
            import td_sequential as _td
            _TD_MODULE = _td
        except Exception:
            pass
    return _TD_MODULE

def _get_hc_module():
    global _HC_MODULE
    if _HC_MODULE is None:
        try:
            import health_check as _hc
            _HC_MODULE = _hc
        except Exception:
            pass
    return _HC_MODULE

BASE_DIR   = Path(__file__).parent.parent.parent
NRGC_DIR   = BASE_DIR / "data" / "nrgc" / "state"
TRADE_LOG  = BASE_DIR / "data" / "paper_trading" / "trade_log.json"

# ─── Entry Rules ───────────────────────────────────────────────────────────────
ENTRY_MIN_NRGC_SCORE  = 50    # minimum Phase 3 score
ENTRY_MIN_CONFIDENCE  = 0.70  # phase confidence
ENTRY_PHASES          = {3}   # Phase 3 Inflection only
MAX_POSITIONS         = 10
POSITION_PCT          = 0.10  # base equal-weight ~10%

# ─── Cap Preference (Finnhub market cap — enhanced alpha via mid/small) ────────
# Market cap in USD — calibrated for 2026 market levels
CAP_SMALL_MAX = 10_000_000_000    # < $10B  = small/mid (high alpha zone)
CAP_MID_MAX   = 100_000_000_000   # < $100B = mid/large
CAP_LARGE_MAX = 500_000_000_000   # < $500B = large
# >= $500B = mega cap
ADTV_MIN      = 5_000_000         # ADTV < $5M USD -> too illiquid, skip
# Score requirements by cap tier
CAP_MIN_SCORE = {
    "small": 50,   # standard — high alpha shots, accept
    "mid":   50,   # standard — sweet spot, prefer
    "large": 70,   # stricter — mega returns harder to achieve
    "mega":  85,   # very strict — NVDA-scale limits % upside
}
# Position sizing multiplier by cap tier
CAP_SIZE_MULT = {
    "small": 0.80,   # 8%  — slightly smaller (liquidity)
    "mid":   1.00,   # 10% — full size, this is the alpha sweet spot
    "large": 0.80,   # 8%  — less explosive upside
    "mega":  0.60,   # 6%  — only when signal is exceptional
}

# ─── Regime-Based Cash Targets ─────────────────────────────────────────────────
REGIME_MIN_CASH = {
    "risk-on":  0.10,   # deploy up to 90%, be aggressive
    "neutral":  0.20,   # 20% cash floor, selective
    "risk-off": 0.50,   # 50% cash — protect capital
}
# In risk-off: exit any position below this P&L threshold (cut losers)
REGIME_RISKOFF_EXIT_BELOW = -0.04  # exit if < -4% AND regime = risk-off

# ─── Exit Rules ────────────────────────────────────────────────────────────────
EXIT_PHASES        = {5, 6, 7}   # Consensus / Euphoria / Distribution
TRAIL_ACTIVATE_PCT = 0.30        # start trailing after +30%
TRAIL_STOP_PCT     = -0.15       # trail -15% from peak


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _load_trade_log() -> list:
    if TRADE_LOG.exists():
        try:
            return json.loads(TRADE_LOG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_trade_log(log: list):
    TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
    TRADE_LOG.write_text(json.dumps(log[-500:], indent=2, ensure_ascii=False))


def _log_trade(action: str, ticker: str, price: float, shares: float,
               reason: str, nrgc_phase: int, pnl_pct: float = None,
               cap_tier: str = None):
    log = _load_trade_log()
    entry = {
        "date":       datetime.now().strftime("%Y-%m-%d"),
        "action":     action,
        "ticker":     ticker,
        "price":      round(price, 4),
        "shares":     shares,
        "reason":     reason,
        "nrgc_phase": nrgc_phase,
    }
    if pnl_pct is not None:
        entry["pnl_pct"] = round(pnl_pct, 2)
    if cap_tier:
        entry["cap_tier"] = cap_tier
    log.append(entry)
    _save_trade_log(log)


def _get_cap_info(ticker: str) -> tuple:
    """
    Get (market_cap_usd, adtv_usd, tier) via Finnhub (market cap) + price data (ADTV).
    Finnhub: reliable market cap, 60 req/min free.
    Returns (market_cap_usd, adtv_usd, tier).
    """
    import os, sys
    sys.path.insert(0, str(BASE_DIR / "scripts" / "paper_trading"))

    # 1. Market cap — Finnhub (primary, most reliable in WARP environment)
    #    yfinance is skipped: fc.yahoo.com is blocked by Cloudflare WARP SSL.
    #    Finnhub free tier = 60 req/min, returns marketCapitalization in millions.
    market_cap = 0.0

    # Finnhub (primary market cap source — no WARP issues)
    if market_cap <= 0:
        try:
            import requests, urllib3, os
            urllib3.disable_warnings()
            _env_path = BASE_DIR / ".env"
            if _env_path.exists():
                for _ln in _env_path.read_text(encoding="utf-8-sig").splitlines():
                    _ln = _ln.strip()
                    if _ln and not _ln.startswith("#") and "=" in _ln:
                        _k, _v = _ln.split("=", 1)
                        os.environ.setdefault(_k.strip(), _v.strip())
            finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
            if finnhub_key:
                s = requests.Session()
                s.verify = False
                r = s.get("https://finnhub.io/api/v1/stock/metric",
                          params={"symbol": ticker, "metric": "all", "token": finnhub_key},
                          timeout=8)
                mc_m = r.json().get("metric", {}).get("marketCapitalization", 0)
                market_cap = float(mc_m or 0) * 1_000_000   # Finnhub returns in millions
        except Exception:
            pass

    # 2. ADTV from price data (liquidity check)
    adtv = 0.0
    try:
        from portfolio_engine import get_price_data
        df = get_price_data(ticker, period="6mo")
        if df is not None and not df.empty and "ADTV" in df.columns:
            v = df["ADTV"].dropna()
            if not v.empty:
                adtv = float(v.iloc[-1])
    except Exception:
        pass

    # 3. Tier from market cap (primary) or ADTV fallback
    if market_cap > 0:
        if market_cap < CAP_SMALL_MAX:   tier = "small"
        elif market_cap < CAP_MID_MAX:   tier = "mid"
        elif market_cap < CAP_LARGE_MAX: tier = "large"
        else:                             tier = "mega"
    elif adtv > 0:
        # ADTV fallback when Finnhub unavailable
        if adtv < 30_000_000:    tier = "small"
        elif adtv < 300_000_000: tier = "mid"
        elif adtv < 2_000_000_000: tier = "large"
        else:                      tier = "mega"
    else:
        tier = "mid"

    # Liquidity gate regardless of cap
    if adtv > 0 and adtv < ADTV_MIN:
        tier = "illiquid"

    return market_cap, adtv, tier


def _get_market_regime(portfolio: dict) -> str:
    """
    Market regime = QQQ vs 50DMA baseline THEN downgraded by TD Sequential.

    Layer 1 — Price structure (QQQ / SPY vs moving averages):
      QQQ >= 1.03 * MA50 -> risk-on
      QQQ >= 0.97 * MA50 -> neutral
      else               -> risk-off

    Layer 2 — TD Sequential override (primary market timing indicator):
      TD caution/warning  -> downgrade one level (risk-on -> neutral)
      TD reversal         -> downgrade to risk-off regardless
      TD bullish          -> confirm current level (no upgrade beyond price structure)

    Cached from synthesis if available (weekly runner passes it already adjusted).
    """
    # Check if weekly synthesis cached a regime (already TD-adjusted)
    cached = portfolio.get("cached_regime")
    if cached:
        return cached

    # Layer 1: QQQ vs 50DMA
    base_regime = "neutral"
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts" / "paper_trading"))
        from portfolio_engine import get_price_data
        df = get_price_data("QQQ", period="6mo")
        if df is not None and not df.empty:
            qqq  = float(df["Close"].iloc[-1])
            ma50 = float(df["MA50"].iloc[-1])
            ratio = qqq / ma50
            if ratio >= 1.03:   base_regime = "risk-on"
            elif ratio >= 0.97: base_regime = "neutral"
            else:               base_regime = "risk-off"
    except Exception:
        pass

    # Layer 2: TD Sequential market regime modifier
    td = _get_td_module()
    if td:
        try:
            # Use cached today's file if available, else compute fresh
            td_state = td.load_cached_td_regime()
            if td_state is None:
                td_state = td.get_td_regime_signal(["SPY", "QQQ"])
            final_regime = td.apply_td_regime_modifier(base_regime, td_state)
            if final_regime != base_regime:
                print(f"  [TD] Regime downgraded {base_regime} -> {final_regime}"
                      f" | {td_state.get('summary', '')}")
            return final_regime
        except Exception:
            pass

    return base_regime


# ─── Auto Entry ────────────────────────────────────────────────────────────────

def auto_enter(portfolio: dict, nrgc_assessments: dict,
               regime: str = None) -> list:
    """
    Fund-style entry with mid/small cap preference and regime gate.
    - risk-off regime: no new entries
    - Mid/small cap: standard bar (score >= 50)
    - Large/mega cap: higher bar (score >= 70 / 85)
    Returns list of new entries made.
    """
    from portfolio_engine import get_current_price, check_pulse_setup

    if regime is None:
        regime = _get_market_regime(portfolio)

    entries_made = []
    positions    = portfolio.get("positions", {})
    cash         = portfolio.get("cash", 0)
    capital      = portfolio.get("capital", 100_000)

    # Regime gate — no new entries in risk-off
    if regime == "risk-off":
        min_cash = REGIME_MIN_CASH["risk-off"]
        print(f"  [Regime] RISK-OFF — no new entries. Cash target: {min_cash*100:.0f}%")
        return entries_made

    min_cash_pct = REGIME_MIN_CASH.get(regime, 0.20)

    if len(positions) >= MAX_POSITIONS:
        return entries_made
    if cash / capital < min_cash_pct:
        return entries_made

    # Candidates: Phase 3, score >= base threshold, not already held
    candidates = [
        (ticker, data) for ticker, data in nrgc_assessments.items()
        if (data.get("phase") in ENTRY_PHASES
            and data.get("confidence", 0) >= ENTRY_MIN_CONFIDENCE
            and data.get("nrgc_composite_score", 0) >= ENTRY_MIN_NRGC_SCORE
            and ticker not in positions)
    ]
    # Score descending
    candidates.sort(key=lambda x: -x[1].get("nrgc_composite_score", 0))

    for ticker, nrgc in candidates:
        if len(positions) >= MAX_POSITIONS:
            break
        if cash / capital < min_cash_pct:
            break

        score = nrgc.get("nrgc_composite_score", 50)

        # Market cap + ADTV check (Finnhub primary, ADTV fallback)
        mktcap, adtv, tier = _get_cap_info(ticker)
        if tier == "illiquid":
            print(f"  [Skip] {ticker}: too illiquid (ADTV ${adtv/1e6:.1f}M)")
            continue
        min_score_for_tier = CAP_MIN_SCORE.get(tier, 50)
        if score < min_score_for_tier:
            print(f"  [Skip] {ticker}: {tier} cap requires score>={min_score_for_tier}, got {score}")
            continue

        # ── TD Sequential Gate (PRIMARY — market timing confirmation) ──────────
        td_boost       = 0
        td_gate_pass   = True
        td_signal      = "unknown"
        td_reason      = "TD module unavailable"
        td = _get_td_module()
        if td:
            try:
                gate_result  = td.td_entry_gate(ticker)
                td_gate_pass = gate_result["pass"]
                td_boost     = gate_result.get("boost", 0)
                td_signal    = gate_result.get("signal", "neutral")
                td_reason    = gate_result.get("reason", "")
                if not td_gate_pass:
                    print(f"  [Skip] {ticker}: {td_reason}")
                    continue
                # Apply TD boost to effective score for ranking/sizing
                score = score + td_boost
            except Exception:
                pass

        # ── Health Check Gate (PRIMARY — leadership state confirmation) ────────
        hc_score     = 0
        hc_rating    = "Unknown"
        hc_entry_sz  = "full"
        hc = _get_hc_module()
        if hc:
            try:
                # Load cached first (avoids duplicate API calls)
                hc_result = hc.load_cached_hc(ticker) or hc.run_health_check(ticker)
                hc_score  = hc_result.get("score", 0)
                hc_rating = hc_result.get("rating", "Unknown")
                hc_entry_sz = hc_result.get("entry_size", "full")
                if hc_score < 5:
                    print(f"  [Skip] {ticker}: Health Check {hc_score}/8 < 5 ({hc_rating})")
                    continue
            except Exception:
                pass

        # ── PULSE gate — require GREEN ─────────────────────────────────────────
        pulse = check_pulse_setup(ticker, setup_type="leader")
        if not pulse.get("valid") or pulse.get("score", 0) < 0.5:
            pulse = check_pulse_setup(ticker, setup_type="hypergrowth")
        gate = pulse.get("gate", "RED")
        if gate == "RED":
            print(f"  [Skip] {ticker}: PULSE gate RED")
            continue

        price = get_current_price(ticker)
        if not price:
            continue

        # Position size: base x cap mult x score mult x health check mult
        cap_mult   = CAP_SIZE_MULT.get(tier, 0.80)
        if score >= 90:    score_mult = 1.00
        elif score >= 70:  score_mult = 0.80
        else:              score_mult = 0.60

        # Health check size modifier
        if hc_entry_sz == "none":
            continue  # HC says don't enter
        hc_mult = 1.00 if hc_entry_sz == "full" else 0.50  # Yellow = half size

        size_usd = capital * POSITION_PCT * cap_mult * score_mult * hc_mult
        size_usd = min(size_usd, cash * 0.90)
        if size_usd < 1000:
            continue

        shares = int(size_usd / price)
        if shares < 1:
            continue
        cost     = shares * price
        stop     = round(price * 0.92, 4)
        adtv_m   = round(adtv / 1e6, 1)
        mktcap_b = round(mktcap / 1e9, 1) if mktcap else None
        setup    = "leader" if score >= 60 else "hypergrowth"

        position = {
            "ticker":           ticker,
            "setup_type":       setup,
            "cap_tier":         tier,
            "market_cap_b":     mktcap_b,
            "adtv_m":           adtv_m,
            "nrgc_phase_entry": nrgc.get("phase", 3),
            "nrgc_score_entry": score,
            "entry_price":      round(price, 4),
            "shares":           shares,
            "cost":             round(cost, 2),
            "stop":             stop,
            "open_date":        datetime.now().strftime("%Y-%m-%d"),
            "theme":            nrgc.get("theme", ""),
            "thesis":           nrgc.get("action", "NRGC Phase 3 auto-entry"),
            "current_price":    round(price, 4),
            "pnl_usd":          0.0,
            "pnl_pct":          0.0,
            "days_held":        0,
            "status":           "open",
            "high_since_entry": round(price, 4),
            "trail_stop":       None,
            "pulse_gate":       gate,
            "pulse_score":      round(pulse.get("score", 0), 2),
            "entry_regime":     regime,
            "td_signal":        td_signal,
            "td_boost":         td_boost,
            "health_check_score": hc_score,
            "health_check_rating": hc_rating,
        }
        portfolio["positions"][ticker] = position
        portfolio["cash"] = round(portfolio["cash"] - cost, 2)
        cash = portfolio["cash"]

        cap_str = f"${mktcap_b}B" if mktcap_b else f"ADTV${adtv_m}M"
        _log_trade("BUY", ticker, price, shares,
                   f"NRGC Ph3 | Score={score} | Gate={gate} | HC={hc_score}/8"
                   f" | TD={td_signal} | {tier} {cap_str} | {nrgc.get('theme','')}",
                   nrgc.get("phase", 3), cap_tier=tier)

        entries_made.append({
            "ticker": ticker, "price": price, "shares": shares,
            "cost": round(cost, 2), "theme": nrgc.get("theme", ""),
            "score": score, "gate": gate, "cap_tier": tier,
            "market_cap_b": mktcap_b, "adtv_m": adtv_m,
            "td_signal": td_signal, "hc_score": hc_score,
        })
        print(f"  [BUY] {ticker} @ ${price:.2f} x{shares} = ${cost:.0f}"
              f" | NRGC={score} | HC={hc_score}/8 | TD={td_signal}"
              f" | {tier} {cap_str} | {regime}")

    return entries_made


# ─── Auto Exit ─────────────────────────────────────────────────────────────────

def auto_exit(portfolio: dict, nrgc_assessments: dict,
              regime: str = None) -> list:
    """
    Exit logic:
    1. Hard stop -8%
    2. NRGC Phase 5/6/7 (distribution signal)
    3. Trail stop after +30% gain (-15% from peak)
    4. Regime risk-off + position < -4% (cut losers, protect capital)
    Returns list of closed trades.
    """
    from portfolio_engine import get_current_price

    if regime is None:
        regime = _get_market_regime(portfolio)

    exits_made = []
    to_close   = []
    phase_names = {1:"Neglect", 2:"Accumulation", 3:"Inflection",
                   4:"Recognition", 5:"Consensus", 6:"Euphoria", 7:"Distribution"}

    for ticker, pos in portfolio.get("positions", {}).items():
        price = get_current_price(ticker)
        if not price:
            continue

        entry      = pos.get("entry_price", price)
        stop       = pos.get("stop", entry * 0.92)
        pnl_pct    = (price / entry - 1) * 100
        high       = max(pos.get("high_since_entry", price), price)
        nrgc       = nrgc_assessments.get(ticker, {})
        curr_phase = nrgc.get("phase")

        # Update high watermark
        if price > pos.get("high_since_entry", price):
            pos["high_since_entry"] = price

        reason = None

        # 1. Hard stop -8%
        if price <= stop:
            reason = f"Hard stop -8% (${entry:.2f}->${price:.2f}, {pnl_pct:+.1f}%)"

        # 2. NRGC Phase 5/6/7
        elif curr_phase and curr_phase in EXIT_PHASES:
            reason = f"NRGC {phase_names.get(curr_phase,'?')} (Phase {curr_phase})"

        # 3. Trailing stop (after +30%)
        elif pnl_pct >= TRAIL_ACTIVATE_PCT * 100:
            trail = pos.get("trail_stop")
            if not trail:
                trail = round(high * (1 + TRAIL_STOP_PCT), 4)
                pos["trail_stop"] = trail
                print(f"  [Trail] {ticker}: +{pnl_pct:.1f}% — trail set ${trail:.2f}")
            if price <= trail:
                reason = f"Trail stop (peak ${high:.2f}->now ${price:.2f}, {pnl_pct:+.1f}%)"

        # 4. Regime risk-off: cut any position bleeding > -4%
        elif regime == "risk-off" and pnl_pct < REGIME_RISKOFF_EXIT_BELOW * 100:
            reason = f"Risk-off regime cut ({pnl_pct:+.1f}% < -{abs(REGIME_RISKOFF_EXIT_BELOW)*100:.0f}% threshold)"

        if reason:
            to_close.append((ticker, price, pnl_pct, reason,
                             curr_phase or pos.get("nrgc_phase_entry", 3)))

    for ticker, price, pnl_pct, reason, phase in to_close:
        pos      = portfolio["positions"].pop(ticker)
        shares   = pos.get("shares", 0)
        entry    = pos.get("entry_price", price)
        proceeds = shares * price
        pnl_usd  = proceeds - pos.get("cost", 0)

        closed_trade = {
            **pos,
            "exit_price":      round(price, 4),
            "exit_date":       datetime.now().strftime("%Y-%m-%d"),
            "exit_reason":     reason,
            "pnl_pct":         round(pnl_pct, 2),
            "pnl_usd":         round(pnl_usd, 2),
            "nrgc_phase_exit": phase,
            "outcome":         "WIN" if pnl_pct >= 0 else "LOSS",
        }
        portfolio.setdefault("closed", []).append(closed_trade)
        portfolio["cash"] = round(portfolio.get("cash", 0) + proceeds, 2)
        portfolio["realized_pnl_usd"] = round(
            portfolio.get("realized_pnl_usd", 0) + pnl_usd, 2
        )

        _log_trade("SELL", ticker, price, shares, reason, phase, pnl_pct,
                   cap_tier=pos.get("cap_tier"))

        exits_made.append(closed_trade)
        result = "WIN" if pnl_pct >= 0 else "LOSS"
        print(f"  [SELL] {ticker} @ ${price:.2f} | {result} {pnl_pct:+.1f}%"
              f" | {reason[:50]}")

    return exits_made


# ─── Regime Cash Management ────────────────────────────────────────────────────

def enforce_cash_target(portfolio: dict, nrgc_assessments: dict,
                         regime: str = None) -> list:
    """
    If regime is risk-off: raise cash by exiting worst-performing positions
    until cash >= 50% of capital. Exit weakest first (lowest pnl_pct).
    Returns list of forced exits.
    """
    if regime is None:
        regime = _get_market_regime(portfolio)

    capital   = portfolio.get("capital", 100_000)
    cash      = portfolio.get("cash", 0)
    cash_pct  = cash / capital if capital else 1.0
    target    = REGIME_MIN_CASH.get(regime, 0.10)

    if cash_pct >= target or regime != "risk-off":
        return []

    print(f"  [CashRaise] {regime} — cash {cash_pct*100:.1f}% < target {target*100:.0f}%. Trimming...")

    # Sort positions: exit weakest first
    from portfolio_engine import get_current_price
    ranked = []
    for ticker, pos in portfolio.get("positions", {}).items():
        price   = get_current_price(ticker) or pos.get("current_price", pos["entry_price"])
        pnl_pct = (price / pos["entry_price"] - 1) * 100
        ranked.append((ticker, price, pnl_pct))
    ranked.sort(key=lambda x: x[2])   # worst P&L first

    forced_exits = []
    for ticker, price, pnl_pct in ranked:
        if cash / capital >= target:
            break
        reason = f"Cash raise: {regime} regime (portfolio-level risk management)"
        exits = auto_exit(
            portfolio,
            {ticker: nrgc_assessments.get(ticker, {"phase": 4})},
            regime="risk-off"
        )
        if exits:
            forced_exits.extend(exits)
            cash = portfolio.get("cash", 0)

    return forced_exits


# ─── Full Auto-Trade Cycle ─────────────────────────────────────────────────────

def run_auto_trade_cycle(portfolio: dict, nrgc_assessments: dict,
                          regime: str = None) -> dict:
    """
    Full cycle:
    1. Detect market regime (QQQ trend or from synthesis)
    2. Enforce cash target (exit if risk-off)
    3. Exit individual positions on signal
    4. Enter new positions (only if regime allows)
    """
    if regime is None:
        regime = _get_market_regime(portfolio)

    print(f"  [Regime] {regime.upper()}")
    portfolio["cached_regime"] = regime

    # 1. Raise cash if risk-off (exit weakest)
    forced = enforce_cash_target(portfolio, nrgc_assessments, regime)

    # 2. Exit on individual signals
    exits = auto_exit(portfolio, nrgc_assessments, regime)

    # 3. Enter only if regime allows
    entries = auto_enter(portfolio, nrgc_assessments, regime)

    return {
        "regime":         regime,
        "entries":        entries,
        "exits":          exits,
        "forced_exits":   forced,
    }
