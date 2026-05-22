"""
AlphaAbsolute v2 -- A01 Market Health Engine
=============================================
Determines market regime and sets cash floor for the day.
All other agents read this output before taking any action.

4-State Regime (v2):
  Markup       -- bull market, full deployment
  Distribution -- topping, reduce exposure
  Sideways     -- choppy, selective only
  Markdown     -- bear, mostly cash

Cash floors enforced:
  Markup       -> cash_floor=0.00, max_deployed=1.00
  Distribution -> cash_floor=0.40, max_deployed=0.60
  Sideways     -> cash_floor=0.20, max_deployed=0.80
  Markdown     -> cash_floor=0.75, max_deployed=0.25

Leading indicators (6) surface early warnings 10-20 days before regime change.
Druckenmiller Liquidity Gate (6 signals) runs as macro overlay.
TD Sequential on SPY/QQQ reported as info only (never a gate in v2).

Output: data/regime/market_health.json
Run:    6:00 AM daily (pre-market)

Cost: $0 (pure Python, no LLM)
"""

from __future__ import annotations
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

OUT_DIR = ROOT / "data" / "regime"
OUT_DIR.mkdir(parents=True, exist_ok=True)
HEALTH_FILE  = OUT_DIR / "market_health.json"
HISTORY_FILE = OUT_DIR / "regime_history.json"


# ── Data helpers ──────────────────────────────────────────────────────────────

def _fetch_ohlcv(ticker: str, days: int = 120) -> list[dict]:
    try:
        from data_engine import get_ohlcv
        df = get_ohlcv(ticker, period="6mo")
        if df is not None and not df.empty:
            records = []
            for idx, row in df.iterrows():
                records.append({
                    "date":   str(idx.date()) if hasattr(idx, "date") else str(idx),
                    "open":   float(row.get("Open",   0)),
                    "high":   float(row.get("High",   0)),
                    "low":    float(row.get("Low",    0)),
                    "close":  float(row.get("Close",  0)),
                    "volume": float(row.get("Volume", 0)),
                })
            return records[-days:]
    except Exception:
        pass
    return []


def _fetch_fred(series_id: str) -> Optional[float]:
    try:
        from data_engine import get_macro
        obs = get_macro(series_id, limit=3)
        if obs:
            return obs[0]["value"]
    except Exception:
        pass
    return None


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _sma(prices: list[float], period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


# ── Factor A: Market Structure (0-25 pts) ─────────────────────────────────────

def score_market_structure(spy_bars: list, qqq_bars: list) -> tuple[int, dict]:
    details = {}
    pts = 0

    for name, bars in [("SPY", spy_bars), ("QQQ", qqq_bars)]:
        if not bars:
            continue
        closes = [b["close"] for b in bars]
        sma50  = _sma(closes, 50)
        sma200 = _sma(closes, 200)
        last   = closes[-1]
        details[name] = {
            "close":       round(last,   2),
            "sma50":       round(sma50,  2) if sma50  else None,
            "sma200":      round(sma200, 2) if sma200 else None,
            "above_50dma": last > sma50  if sma50  else False,
            "above_200dma": last > sma200 if sma200 else False,
            "pct_from_52w_high": None,  # not computed here — breadth proxy sufficient
        }

    both_above_50  = all(details.get(n, {}).get("above_50dma",  False) for n in ["SPY", "QQQ"])
    one_above_50   = any(details.get(n, {}).get("above_50dma",  False) for n in ["SPY", "QQQ"])
    both_above_200 = all(details.get(n, {}).get("above_200dma", False) for n in ["SPY", "QQQ"])
    one_above_200  = any(details.get(n, {}).get("above_200dma", False) for n in ["SPY", "QQQ"])

    if both_above_50:   pts += 15
    elif one_above_50:  pts += 8

    if both_above_200:  pts += 6
    elif one_above_200: pts += 3

    # FTD proxy: recovered >5% from 30-day low
    if spy_bars and len(spy_bars) >= 30:
        closes  = [b["close"] for b in spy_bars]
        low_30  = min(closes[-30:])
        latest  = closes[-1]
        if low_30 > 0 and (latest / low_30 - 1) > 0.05:
            pts += 4
            details["ftd_approx"] = "LIKELY"
        else:
            details["ftd_approx"] = "NOT_CONFIRMED"

    details["factor_score"] = min(pts, 25)
    return min(pts, 25), details


# ── Factor B: Distribution Pressure (0-20 pts) ───────────────────────────────

def count_distribution_days(bars: list, lookback: int = 25) -> tuple[int, list[str], int]:
    if len(bars) < lookback + 1:
        bars = bars[-max(len(bars) - 1, 1):]
    else:
        bars = bars[-lookback - 1:]

    dist_days = []
    for i in range(1, len(bars)):
        pct_chg = (bars[i]["close"] - bars[i-1]["close"]) / bars[i-1]["close"]
        if pct_chg <= -0.002 and bars[i]["volume"] > bars[i-1]["volume"]:
            dist_days.append(bars[i]["date"])

    stall_count = 0
    for i in range(1, len(bars)):
        curr = bars[i]
        if curr["high"] > curr["low"]:
            close_pct = (curr["close"] - curr["low"]) / (curr["high"] - curr["low"])
            pct_chg   = (curr["close"] - bars[i-1]["close"]) / bars[i-1]["close"]
            if 0 <= pct_chg <= 0.003 and close_pct < 0.30 and curr["volume"] > bars[i-1]["volume"] * 1.2:
                stall_count += 1

    return len(dist_days), dist_days, stall_count


def score_distribution(spy_bars: list, qqq_bars: list) -> tuple[int, dict]:
    spy_dist, _, spy_stall = count_distribution_days(spy_bars)
    qqq_dist, _, qqq_stall = count_distribution_days(qqq_bars)
    dist_count  = max(spy_dist, qqq_dist)
    stall_count = max(spy_stall, qqq_stall)

    if   dist_count == 0: pts = 20
    elif dist_count <= 1: pts = 18
    elif dist_count == 2: pts = 14
    elif dist_count == 3: pts = 10
    elif dist_count == 4: pts = 5
    else:                 pts = 0

    pts = max(pts - min(stall_count, 4), 0)

    return pts, {
        "spy_distribution_days": spy_dist,
        "qqq_distribution_days": qqq_dist,
        "dist_days_used":        dist_count,
        "stalling_days":         stall_count,
        "factor_score":          pts,
    }


# ── Factor C: Breadth (0-20 pts) ─────────────────────────────────────────────

def score_breadth(spy_bars: list, iwm_bars: list) -> tuple[int, dict]:
    details = {}
    pts = 0

    # SPY vs IWM 20-day relative performance
    if spy_bars and iwm_bars and len(spy_bars) >= 21 and len(iwm_bars) >= 21:
        spy_perf = (spy_bars[-1]["close"] / spy_bars[-21]["close"] - 1) * 100
        iwm_perf = (iwm_bars[-1]["close"] / iwm_bars[-21]["close"] - 1) * 100
        div = spy_perf - iwm_perf
        details["spy_vs_iwm_20d"] = round(div, 2)

        if   div < 2:  pts += 10
        elif div < 5:  pts += 6
        else:          pts += 2

    # SPY vs 50DMA as breadth proxy
    if spy_bars and len(spy_bars) >= 50:
        closes = [b["close"] for b in spy_bars]
        sma50  = _sma(closes, 50)
        last   = closes[-1]
        pct    = (last / sma50 - 1) * 100 if sma50 else 0
        details["spy_pct_above_50dma"] = round(pct, 2)

        if   pct > 5:   pts += 10
        elif pct > 1:   pts += 7
        elif pct > -2:  pts += 3
        else:           pts += 0

    details["factor_score"] = min(pts, 20)
    return min(pts, 20), details


# ── Factor D: TD Sequential (informational only — NOT a gate in v2) ──────────

def get_td_signals() -> dict:
    """
    Read TD state files for SPY + QQQ.
    In v2, TD is NOT a gate — it is reported for size_modifier calculation only.
    A08 Setup Scanner uses TD to adjust position size.
    """
    td_dir = ROOT / "data" / "td_sequential"
    signals = {}

    for ticker in ["SPY", "QQQ"]:
        td_file = td_dir / f"{ticker}.json"
        if not td_file.exists():
            signals[ticker] = "Neutral"
            continue
        state = _load_json(td_file)
        ss = state.get("setup_count", 0)
        cd = state.get("countdown_count", 0)

        if cd >= 13:
            signals[ticker] = "SellCountdown13"
        elif cd >= 10:
            signals[ticker] = "SellCountdown10"
        elif ss >= 9:
            signals[ticker] = "SellSetup9"
        elif ss >= 7:
            signals[ticker] = "SellSetup7"
        elif ss <= -9:
            signals[ticker] = "BuySetup9"
        else:
            signals[ticker] = "Neutral"

    return signals


# ── Factor E: RS Leaders (replaces NRGC leadership — 0-10 pts) ───────────────

def score_rs_leaders() -> tuple[int, dict]:
    """
    Count stocks in RS top quartile (>75th) from latest RS ranking.
    Replaces v1 NRGC-based score_leadership().
    Data source: data/rs_universe/latest.json (from A03 rs_ranker.py)
    """
    details = {}
    pts = 5  # neutral default if no RS data yet

    latest_file = ROOT / "data" / "rs_universe" / "latest.json"
    if not latest_file.exists():
        details["note"] = "No RS data yet — using neutral 5/10"
        details["factor_score"] = pts
        return pts, details

    try:
        data = json.loads(latest_file.read_text(encoding="utf-8"))
        results = data.get("results", [])
        total = len(results)
        if total == 0:
            details["note"] = "RS results empty"
            return pts, details

        top_quartile = [r for r in results if r.get("rs_pct_3m", 0) >= 75]
        leader_count = len(top_quartile)
        leader_pct   = leader_count / total * 100 if total > 0 else 0

        details["total_ranked"]  = total
        details["rs_top_quartile_count"] = leader_count
        details["rs_top_quartile_pct"]   = round(leader_pct, 1)

        if   leader_pct >= 30:  pts = 10  # many strong leaders
        elif leader_pct >= 22:  pts = 7
        elif leader_pct >= 15:  pts = 5   # neutral
        elif leader_pct >= 10:  pts = 3
        else:                   pts = 1   # few leaders = late market / bear

    except Exception as e:
        details["error"] = str(e)

    details["factor_score"] = pts
    return pts, details


# ── Factor F: Intermarket (VIX + yields + curve, 0-5 pts) ────────────────────

def score_intermarket(vix_bars: list, yield_10y: Optional[float],
                      yield_curve: Optional[float]) -> tuple[int, dict]:
    details = {}
    pts = 0

    # VIX (0-3 pts)
    if vix_bars and len(vix_bars) >= 5:
        vix = vix_bars[-1]["close"]
        details["vix_current"] = round(vix, 2)
        if   vix < 16:  pts += 3
        elif vix < 20:  pts += 2
        elif vix < 25:  pts += 1
        else:           pts += 0
    else:
        pts += 1  # neutral default

    # 10Y yield (0-2 pts) — ERP framework
    if yield_10y is not None:
        details["yield_10y"] = round(yield_10y, 3)
        if   yield_10y < 3.5:   pts += 2; details["yield_signal"] = "VERY_SUPPORTIVE"
        elif yield_10y < 4.0:   pts += 1; details["yield_signal"] = "SUPPORTIVE"
        elif yield_10y < 4.5:   pts += 0; details["yield_signal"] = "NEUTRAL"
        elif yield_10y < 5.0:   pts -= 1; details["yield_signal"] = "RESTRICTIVE"
        else:                   pts -= 2; details["yield_signal"] = "VERY_RESTRICTIVE"

    # Yield curve (0-1 pt)
    if yield_curve is not None:
        details["yield_curve_t10y2y"] = round(yield_curve, 3)
        if yield_curve > 0:
            pts += 1
            details["curve_signal"] = "NORMAL"
        elif yield_curve > -0.5:
            details["curve_signal"] = "FLAT"
        else:
            details["curve_signal"] = "INVERTED"

    pts = min(max(pts, 0), 5)
    details["factor_score"] = pts
    return pts, details


# ── Factor G: Credit Sentiment (0-5 pts) ──────────────────────────────────────

def score_sentiment(hy_spread: Optional[float]) -> tuple[int, dict]:
    details = {}

    if hy_spread is not None:
        details["hy_spread_pct"] = round(hy_spread, 2)
        if   hy_spread < 3.0:  pts = 5; details["credit_signal"] = "RISK_ON"
        elif hy_spread < 4.0:  pts = 4; details["credit_signal"] = "CALM"
        elif hy_spread < 5.0:  pts = 3; details["credit_signal"] = "NEUTRAL"
        elif hy_spread < 7.0:  pts = 1; details["credit_signal"] = "STRESS"
        else:                  pts = 0; details["credit_signal"] = "CRISIS"
        details["factor_score"] = pts
        return pts, details

    details["note"] = "No HY spread data — using neutral 3/5"
    details["factor_score"] = 3
    return 3, details


# ── Leading Indicators (fire 10-20 days before regime change) ─────────────────

def detect_leading_indicators(spy_bars: list, qqq_bars: list,
                               vix_bars: list) -> tuple[int, list[str]]:
    warnings = []

    # 1. Volume inversion (down vol > up vol over 21 days)
    if spy_bars and len(spy_bars) >= 22:
        bars   = spy_bars[-22:]
        up_vol = sum(b["volume"] for i, b in enumerate(bars[1:], 1)
                     if b["close"] > bars[i-1]["close"])
        dn_vol = sum(b["volume"] for i, b in enumerate(bars[1:], 1)
                     if b["close"] < bars[i-1]["close"])
        if dn_vol > up_vol * 1.15:
            warnings.append("VOLUME_INVERSION: Down-vol > Up-vol 21d (distribution)")

    # 2. Momentum divergence (near ATH but decelerating)
    if spy_bars and len(spy_bars) >= 40:
        closes   = [b["close"] for b in spy_bars]
        high_60  = max(closes[-min(60, len(closes)):])
        pct_from = (closes[-1] / high_60 - 1) * 100
        mom_10d  = (closes[-1] / closes[-11] - 1) * 100
        mom_20d  = (closes[-1] / closes[-21] - 1) * 100
        if pct_from > -5 and mom_10d < mom_20d / 2 and mom_10d < 0.5:
            warnings.append("MOMENTUM_DIVERGENCE: Near ATH but momentum slowing")

    # 3. VIX rising 30% from 3-week low
    if vix_bars and len(vix_bars) >= 15:
        vix_now     = vix_bars[-1]["close"]
        vix_3w_low  = min(b["close"] for b in vix_bars[-15:])
        if vix_now > vix_3w_low * 1.3 and vix_now < 25:
            warnings.append(f"VIX_RISING: +{(vix_now/vix_3w_low-1)*100:.0f}% from 3-week low")

    # 4. QQQ lagging SPY by >2% over 10 days (rotation to defensives)
    if spy_bars and qqq_bars and len(spy_bars) >= 11 and len(qqq_bars) >= 11:
        spy_10d = (spy_bars[-1]["close"] / spy_bars[-11]["close"] - 1) * 100
        qqq_10d = (qqq_bars[-1]["close"] / qqq_bars[-11]["close"] - 1) * 100
        if qqq_10d < spy_10d - 2.0:
            warnings.append(f"GROWTH_LAGGING: QQQ {qqq_10d:.1f}% vs SPY {spy_10d:.1f}% 10d")

    # 5. Failed 50DMA recapture (price crossed above then fell back)
    if spy_bars and len(spy_bars) >= 10:
        closes = [b["close"] for b in spy_bars]
        sma50  = _sma(closes, min(50, len(closes)))
        if sma50:
            above_50 = [c > sma50 for c in closes[-10:]]
            if sum(above_50[:5]) >= 3 and not above_50[-1]:
                warnings.append("FAILED_50DMA: Market crossed above 50DMA then reversed")

    # 6. Distribution cluster: 3+ dist days in last 10 sessions
    if spy_bars and len(spy_bars) >= 11:
        bars10  = spy_bars[-11:]
        dist_10 = sum(1 for i in range(1, len(bars10))
                      if bars10[i]["close"] < bars10[i-1]["close"] * 0.998
                      and bars10[i]["volume"] > bars10[i-1]["volume"])
        if dist_10 >= 3:
            warnings.append(f"DISTRIBUTION_CLUSTER: {dist_10} dist days in last 10 sessions")

    return len(warnings), warnings


# ── Druckenmiller Liquidity Gate (6 signals) ──────────────────────────────────

def compute_liquidity_gate(hy_spread: Optional[float], yield_curve: Optional[float],
                            fed_funds: Optional[float], cpi_yoy: Optional[float],
                            yield_10y: Optional[float], real_yield: Optional[float],
                            spy_pe: float = 21.0) -> tuple[str, int, list[str]]:
    """
    6-signal macro liquidity gate (Druckenmiller: liquidity leads price).
    Returns: (gate_verdict, caution_count, notes)
    gate_verdict: SUPPORTIVE / CAUTIOUS / TIGHT
    """
    notes = []
    cautions = 0

    # 1. HY spreads
    if hy_spread is not None:
        if   hy_spread > 6.0: cautions += 1; notes.append(f"HY={hy_spread:.2f}% WIDE (credit stress)")
        elif hy_spread > 4.5: notes.append(f"HY={hy_spread:.2f}% elevated (watch)")
        else:                 notes.append(f"HY={hy_spread:.2f}% tight (risk-on)")

    # 2. Yield curve
    if yield_curve is not None:
        if   yield_curve < -0.5: cautions += 1; notes.append(f"Curve={yield_curve:.2f}% deep inversion")
        elif yield_curve < 0:    notes.append(f"Curve={yield_curve:.2f}% inverted (watch)")
        else:                    notes.append(f"Curve={yield_curve:.2f}% normal")

    # 3. Yield gap (earnings yield vs 10Y)
    if yield_10y is not None:
        earnings_yield = round(100.0 / spy_pe, 3)
        yield_gap      = round(earnings_yield - yield_10y, 3)
        if   yield_gap < 0.0: cautions += 1; notes.append(f"YieldGap={yield_gap:.2f}% negative (bonds > equities)")
        elif yield_gap < 1.5: notes.append(f"YieldGap={yield_gap:.2f}% thin (watch)")
        else:                 notes.append(f"YieldGap={yield_gap:.2f}% healthy")

    # 4. Inflation
    if cpi_yoy is not None:
        if   cpi_yoy > 4.0: cautions += 1; notes.append(f"CPI={cpi_yoy:.1f}% HIGH (Fed must stay tight)")
        elif cpi_yoy > 3.0: notes.append(f"CPI={cpi_yoy:.1f}% elevated (watch)")
        else:               notes.append(f"CPI={cpi_yoy:.1f}% contained")

    # 5. ERP (earnings yield - real yield)
    if real_yield is not None and yield_10y is not None:
        earnings_yield = round(100.0 / spy_pe, 3)
        erp = round(earnings_yield - real_yield, 3)
        if   erp < 1.0: cautions += 1; notes.append(f"ERP={erp:.2f}% THIN (stocks priced for perfection)")
        elif erp < 2.0: notes.append(f"ERP={erp:.2f}% compressed (selective only)")
        else:           notes.append(f"ERP={erp:.2f}% healthy")

    # 6. Inflation re-acceleration risk (breakeven > 3.0%)
    # (Handled via cpi_yoy signal 4 — placeholder for t5y_be if available)

    if   cautions >= 3: gate = "TIGHT"
    elif cautions >= 1: gate = "CAUTIOUS"
    else:               gate = "SUPPORTIVE"

    return gate, cautions, notes


# ── v2 Regime Classification ──────────────────────────────────────────────────

def classify_v2_regime(total_score: int, dist_days: int,
                        warn_count: int, spy_bars: list,
                        qqq_bars: list) -> tuple[str, float, float, bool, bool, str]:
    """
    Map score + signals to v2 4-state regime.
    Returns: (regime, cash_floor, max_deployed, leaders_ok, bigshot_ok, note)
    """
    # Force-downgrade for severe distribution or leading indicators
    if warn_count >= 5 or dist_days >= 5:
        total_score = min(total_score, 39)
    elif warn_count >= 3:
        total_score = min(total_score, 54)

    # Check if QQQ is below 200DMA (strong markdown signal)
    qqq_below_200 = False
    if qqq_bars and len(qqq_bars) >= 200:
        closes = [b["close"] for b in qqq_bars]
        sma200 = _sma(closes, 200)
        if sma200 and closes[-1] < sma200:
            qqq_below_200 = True

    # v2 4-state mapping
    if qqq_below_200 or total_score < 30:
        return ("Markdown", 0.75, 0.25, False, False,
                "QQQ below 200DMA — capital preservation mode. Only deploy if stocks pass full screen.")

    elif total_score < 50:
        return ("Distribution", 0.40, 0.60, True, False,
                f"Score {total_score}/100 — distribution/pressure. Mode A only, reduced size. No new Big Shot entries.")

    elif total_score < 68:
        return ("Sideways", 0.20, 0.80, True, True,
                f"Score {total_score}/100 — choppy/sideways. High conviction only. Small initial size.")

    else:
        return ("Markup", 0.00, 1.00, True, True,
                f"Score {total_score}/100 — markup phase. Full deployment authorized. Both modes active.")


# ── M0 Challenger (bull dissent in bear regime) ───────────────────────────────

def detect_regime_challenge(regime: str, spy_bars: list, qqq_bars: list,
                              vix_bars: list, td_signals: dict,
                              hy_spread: Optional[float],
                              prev_health: dict) -> dict:
    """
    Surface bull counter-signals when regime = Distribution or Markdown.
    NEVER overrides the regime call — informational only.
    Threshold: 4/5 bull signals to activate.
    3-day cooldown to suppress noise.
    """
    result = {"active": False, "bull_count": 0, "bull_signals": [], "message": ""}

    if regime not in ("Distribution", "Markdown"):
        return result

    signals = []

    # 1. TD Buy Setup 9 on SPY or QQQ
    for ticker in ["SPY", "QQQ"]:
        if td_signals.get(ticker) == "BuySetup9":
            signals.append(f"TD {ticker} Buy Setup 9 — downside exhaustion signal")
            break

    # 2. VIX falling fast (>15% drop over 5 bars)
    if len(vix_bars) >= 5:
        vix_5ago = vix_bars[-5]["close"]
        vix_now  = vix_bars[-1]["close"]
        if vix_5ago > 0 and (vix_5ago - vix_now) / vix_5ago >= 0.12:
            signals.append(f"VIX fell {(vix_5ago-vix_now)/vix_5ago*100:.0f}% over 5 bars — fear receding")

    # 3. SPY bounced >4% in last 10 bars
    if len(spy_bars) >= 10:
        closes = [b["close"] for b in spy_bars]
        bounce = (closes[-1] - closes[-10]) / closes[-10] if closes[-10] > 0 else 0
        if bounce >= 0.04:
            signals.append(f"SPY +{bounce*100:.1f}% bounce over 10 bars — price recovering")

    # 4. SPY near/above 50DMA
    if len(spy_bars) >= 50:
        closes   = [b["close"] for b in spy_bars]
        sma50    = _sma(closes, 50)
        ma50_pct = (closes[-1] - sma50) / sma50 * 100 if sma50 else None
        if ma50_pct is not None and ma50_pct > -2:
            signals.append(f"SPY {ma50_pct:+.1f}% vs 50DMA — recapture likely")

    # 5. HY spreads tightening vs last reading
    prev_hy = prev_health.get("macro", {}).get("hy_spread")
    if hy_spread is not None and prev_hy is not None:
        tightening = prev_hy - hy_spread
        if tightening >= 0.15:
            signals.append(f"HY spreads tightening {tightening*100:.0f}bps — credit leading equities up")

    bull_count = len(signals)
    THRESHOLD  = 4
    COOLDOWN   = 3

    # 3-day cooldown check
    prev_challenge  = prev_health.get("regime_challenge", {})
    prev_date_str   = prev_challenge.get("last_fired_date", "")
    prev_bull_count = prev_challenge.get("bull_count", 0)
    in_cooldown     = False
    last_fired_date = prev_date_str

    if prev_date_str:
        try:
            days_since = (date.today() - date.fromisoformat(prev_date_str)).days
            if days_since < COOLDOWN and bull_count <= prev_bull_count:
                in_cooldown = True
        except Exception:
            pass

    active = bull_count >= THRESHOLD and not in_cooldown

    if active:
        last_fired_date = date.today().isoformat()
        message = (
            f"[REGIME CHALLENGE] Regime={regime} but {bull_count}/5 bull signals active. "
            f"Regime call STANDS — watch for Follow-Through Day before re-entering. "
            f"Signals: {' | '.join(s[:60] for s in signals)}"
        )
        print(f"\n  [!!!] REGIME CHALLENGE: {bull_count} bull signals vs regime={regime}")
        for s in signals:
            print(f"    + {s[:80]}")

    return {
        "active":          active,
        "bull_count":      bull_count,
        "threshold":       THRESHOLD,
        "bull_signals":    signals,
        "message":         message if active else "",
        "in_cooldown":     in_cooldown,
        "last_fired_date": last_fired_date,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  A01 Market Health Engine  [{today}]")
    print(f"{'='*55}")

    # Fetch price data
    print("  Fetching SPY, QQQ, IWM, VIX...")
    spy_bars = _fetch_ohlcv("SPY", days=120)
    qqq_bars = _fetch_ohlcv("QQQ", days=120)
    iwm_bars = _fetch_ohlcv("IWM", days=60)
    vix_bars = _fetch_ohlcv("^VIX", days=60)

    # Fetch FRED macro
    print("  Fetching FRED macro (10Y, curve, HY, CPI, real yield)...")
    yield_10y   = _fetch_fred("DGS10")
    yield_curve = _fetch_fred("T10Y2Y")
    hy_spread   = _fetch_fred("BAMLH0A0HYM2")
    fed_funds   = _fetch_fred("DFF")
    real_yield  = _fetch_fred("DFII10")

    cpi_yoy: Optional[float] = None
    try:
        from data_engine import get_macro as _gm
        cpi_obs = _gm("CPIAUCSL", limit=14) or []
        if len(cpi_obs) >= 13:
            cpi_yoy = round((cpi_obs[0]["value"] - cpi_obs[12]["value"]) / cpi_obs[12]["value"] * 100, 2)
    except Exception:
        pass

    macro_str = (f"10Y={yield_10y:.2f}% | curve={yield_curve:.2f}% | "
                 f"HY={hy_spread:.2f}% | CPI={cpi_yoy:.1f}%"
                 if all(x is not None for x in [yield_10y, yield_curve, hy_spread, cpi_yoy])
                 else "Some FRED data unavailable")
    print(f"    {macro_str}")

    # Score all factors (A-G, max 100 pts)
    fa_score, fa_detail = score_market_structure(spy_bars, qqq_bars)
    fb_score, fb_detail = score_distribution(spy_bars, qqq_bars)
    fc_score, fc_detail = score_breadth(spy_bars, iwm_bars)
    fe_score, fe_detail = score_rs_leaders()
    ff_score, ff_detail = score_intermarket(vix_bars, yield_10y, yield_curve)
    fg_score, fg_detail = score_sentiment(hy_spread)

    total_score = fa_score + fb_score + fc_score + fe_score + ff_score + fg_score
    # Note: Factor D (TD) not in composite score in v2 — informational only

    td_signals  = get_td_signals()
    warn_count, warnings = detect_leading_indicators(spy_bars, qqq_bars, vix_bars)
    dist_days   = fb_detail.get("dist_days_used", 0)

    # v2 regime classification
    regime, cash_floor, max_deployed, leaders_ok, bigshot_ok, regime_note = classify_v2_regime(
        total_score, dist_days, warn_count, spy_bars, qqq_bars
    )

    # Liquidity gate
    liq_gate, liq_cautions, liq_notes = compute_liquidity_gate(
        hy_spread, yield_curve, fed_funds, cpi_yoy, yield_10y, real_yield
    )

    # Adjust max_deployed for liquidity tightness (never increase cash_floor here)
    if liq_gate == "TIGHT" and max_deployed > 0.60:
        max_deployed = min(max_deployed, 0.60)
        regime_note += " [Liquidity TIGHT — max_deployed capped at 60%]"

    # Challenger (bull dissent in bearish regime)
    prev_health     = _load_json(HEALTH_FILE)
    regime_challenge = detect_regime_challenge(
        regime, spy_bars, qqq_bars, vix_bars, td_signals, hy_spread, prev_health
    )

    # Trend vs yesterday
    prev_score  = prev_health.get("regime_score", total_score)
    prev_regime = prev_health.get("regime", regime)
    if   total_score > prev_score + 3:  trend = "IMPROVING"
    elif total_score < prev_score - 3:  trend = "DETERIORATING"
    else:                               trend = "STABLE"

    health = {
        # v2 standard output (read by all other agents)
        "date":          today,
        "regime":        regime,
        "cash_floor":    cash_floor,
        "max_deployed":  max_deployed,
        "leaders_ok":    leaders_ok,
        "bigshot_ok":    bigshot_ok,
        "spy_td_signal": td_signals.get("SPY", "Neutral"),
        "qqq_td_signal": td_signals.get("QQQ", "Neutral"),
        "distribution_days": dist_days,
        "pct_above_50dma":   round(fc_detail.get("spy_pct_above_50dma", 0), 1),
        "regime_note":   regime_note,

        # Internal scoring detail (not required by other agents but useful for debugging)
        "regime_score":  total_score,
        "regime_trend":  trend,
        "prior_regime":  prev_regime,
        "factor_scores": {
            "A_structure":    {"score": fa_score, "max": 25},
            "B_distribution": {"score": fb_score, "max": 20},
            "C_breadth":      {"score": fc_score, "max": 20},
            "E_rs_leaders":   {"score": fe_score, "max": 10},
            "F_intermarket":  {"score": ff_score, "max": 5},
            "G_sentiment":    {"score": fg_score, "max": 5},
        },
        "early_warnings": warnings,
        "early_warning_count": warn_count,

        # Macro snapshot
        "macro": {
            "yield_10y":   round(yield_10y,   3) if yield_10y   else None,
            "yield_curve": round(yield_curve,  3) if yield_curve else None,
            "hy_spread":   round(hy_spread,    2) if hy_spread   else None,
            "fed_funds":   round(fed_funds,    2) if fed_funds   else None,
            "real_yield":  round(real_yield,   3) if real_yield  else None,
            "cpi_yoy":     cpi_yoy,
        },
        "liquidity_gate":     liq_gate,
        "liquidity_cautions": liq_cautions,
        "liquidity_notes":    liq_notes,

        # Challenger
        "regime_challenge": regime_challenge,

        "generated_at": datetime.now().strftime("%H:%M"),
    }

    HEALTH_FILE.write_text(json.dumps(health, indent=2, default=str), encoding="utf-8")

    # Update history
    hist = _load_json(HISTORY_FILE) if HISTORY_FILE.exists() else {"history": []}
    hist.setdefault("history", []).append({
        "date":    today,
        "regime":  regime,
        "score":   total_score,
        "cash_floor": cash_floor,
        "warnings": warn_count,
    })
    hist["history"] = hist["history"][-90:]
    HISTORY_FILE.write_text(json.dumps(hist, indent=2), encoding="utf-8")

    # Console summary
    emoji = {"Markup": "[BULL]", "Sideways": "[CHOP]", "Distribution": "[DIST]", "Markdown": "[BEAR]"}
    print(f"\n  {emoji.get(regime, '[?]')} REGIME: {regime}")
    print(f"  Score: {total_score}/85 | Cash floor: {int(cash_floor*100)}% | Max deployed: {int(max_deployed*100)}%")
    print(f"  Leaders: {'OK' if leaders_ok else 'BLOCKED'} | BigShot: {'OK' if bigshot_ok else 'BLOCKED'}")
    print(f"  TD: SPY={td_signals['SPY']} QQQ={td_signals['QQQ']}")
    print(f"  Liquidity gate: {liq_gate} ({liq_cautions}/5 cautions)")
    if warnings:
        print(f"  [!] Leading warnings ({warn_count}):")
        for w in warnings:
            print(f"    - {w}")
    print(f"  -> Written: {HEALTH_FILE}")

    return health


if __name__ == "__main__":
    run()
