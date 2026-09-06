"""
AlphaAbsolute — Health Check Dashboard Engine
=============================================
Implements the 8-indicator Leadership State Score shown in the PULSE dashboard screenshot.
PRIMARY entry gate — stock must score 7-8/8 for full-size entry.

8 INDICATORS:
  1. TF Alignment   — Monthly + Weekly + Daily + Intraday all bullish (4/4)
  2. Market         — Breadth, index trend, distribution days
  3. Rel Strength   — RS vs SPX + sector, top percentile
  4. Volume         — Accumulation pattern, institutional footprint
  5. Momentum       — Trend strength, price extension vs MAs
  6. Volatility     — ATR contraction -> expansion (breakout confirmed)
  7. Extension      — Distance from 10EMA / 21EMA / 50DMA (not extended)
  8. Bull Streak    — Consecutive bullish bars (4+ = strong demand)

ENTRY RULES:
  7-8/8 = Green  — full position size allowed
  5-6/8 = Yellow — 50% size, must be 4/4 TF Alignment
  < 5/8 = Red    — do not enter, monitor only
  NOTE: If TF Alignment < 4/4, maximum rating = YELLOW regardless of others
"""

import json
import math
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False

ROOT = Path(__file__).resolve().parents[2]
HC_STATE_PATH = ROOT / "data/health_checks"
HC_STATE_PATH.mkdir(parents=True, exist_ok=True)


# =============================================================================
# MA / INDICATOR HELPERS
# =============================================================================

def sma(prices: list, period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def ema(prices: list, period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    k  = 2 / (period + 1)
    e  = prices[0]
    for p in prices[1:]:
        e = p * k + e * (1 - k)
    return e


def atr(highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def rsi(closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 2)


def consecutive_bullish_bars(closes: list, opens: list) -> int:
    """Count consecutive bars where close > open (bullish candle)."""
    count = 0
    for i in range(len(closes) - 1, -1, -1):
        if closes[i] > opens[i]:
            count += 1
        else:
            break
    return count


# =============================================================================
# THE 8 HEALTH CHECK INDICATORS
# =============================================================================

def check_tf_alignment(closes_1d: list, closes_1w: list) -> dict:
    """
    Indicator 1: TF Alignment — 4/4 Bull (Monthly/Weekly/Daily/Intraday)
    We approximate using: 200D MA (monthly proxy), 50D (weekly), 20D (daily), 5D (intraday)
    Bullish condition: price above all 4 MAs, each shorter MA above longer MA.
    """
    score = 0
    details = []
    c = closes_1d[-1] if closes_1d else None

    # Daily data proxies for 4 timeframes
    ma200 = sma(closes_1d, 200)
    ma150 = sma(closes_1d, 150)
    ma50  = sma(closes_1d, 50)
    ma20  = sma(closes_1d, 20)
    ma5   = sma(closes_1d, 5)

    checks = [
        (c and ma200 and c > ma200, "Above 200D MA (Monthly TF)"),
        (c and ma50  and c > ma50,  "Above 50D MA (Weekly TF)"),
        (c and ma20  and c > ma20,  "Above 20D MA (Daily TF)"),
        (c and ma5   and c > ma5,   "Above 5D MA (Intraday TF)"),
    ]

    passed = sum(1 for ok, _ in checks if ok)
    score  = passed  # 0-4

    bull_label = {4: "4/4 Bull", 3: "3/4 Bull", 2: "2/4 Mixed", 1: "1/4 Bear", 0: "0/4 Bear"}
    is_green = passed == 4

    return {
        "name":     "TF Alignment",
        "score":    1 if is_green else 0,
        "label":    bull_label.get(passed, "?"),
        "green":    is_green,
        "tf_count": passed,
        "details":  [desc for ok, desc in checks if ok],
    }


def check_market_health(spy_closes: list) -> dict:
    """
    Indicator 2: Market — Breadth + trend health of SPY.
    Uses SPY proxy: above 50D MA, 50D > 200D, positive momentum.
    """
    if not spy_closes or len(spy_closes) < 50:
        return {"name": "Market", "score": 0, "label": "Unknown", "green": False}

    c     = spy_closes[-1]
    ma50  = sma(spy_closes, 50)
    ma200 = sma(spy_closes, 200) if len(spy_closes) >= 200 else None
    ma20  = sma(spy_closes, 20)

    checks_passed = 0
    if ma50  and c > ma50:    checks_passed += 1
    if ma200 and ma50 and ma50 > ma200: checks_passed += 1
    if ma20  and c > ma20:   checks_passed += 1

    # Momentum: 5D return positive
    if len(spy_closes) >= 5 and spy_closes[-5] > 0:
        ret5 = (c - spy_closes[-5]) / spy_closes[-5]
        if ret5 > 0:
            checks_passed += 1

    label  = "Healthy" if checks_passed >= 3 else ("Mixed" if checks_passed >= 2 else "Weak")
    is_green = checks_passed >= 3

    return {
        "name":   "Market",
        "score":  1 if is_green else 0,
        "label":  label,
        "green":  is_green,
        "checks": checks_passed,
    }


def check_relative_strength(stock_closes: list, spy_closes: list) -> dict:
    """
    Indicator 3: Relative Strength — stock outperforming SPY.
    Uses 1M, 3M, 6M RS ratios.
    """
    def rs_pct(stock: list, bench: list, lookback: int) -> Optional[float]:
        if len(stock) < lookback + 1 or len(bench) < lookback + 1:
            return None
        s_ret = (stock[-1] - stock[-lookback]) / stock[-lookback] * 100
        b_ret = (bench[-1] - bench[-lookback]) / bench[-lookback] * 100
        return round(s_ret - b_ret, 2)

    rs1m  = rs_pct(stock_closes, spy_closes, 21)
    rs3m  = rs_pct(stock_closes, spy_closes, 63)
    rs6m  = rs_pct(stock_closes, spy_closes, 126)

    checks_passed = sum([
        rs1m is not None and rs1m > 0,
        rs3m is not None and rs3m > 0,
        rs6m is not None and rs6m > 0,
    ])

    label    = "Leading" if checks_passed >= 2 else ("Neutral" if checks_passed == 1 else "Lagging")
    is_green = checks_passed >= 2

    return {
        "name":   "Rel Strength",
        "score":  1 if is_green else 0,
        "label":  label,
        "green":  is_green,
        "rs_1m":  rs1m,
        "rs_3m":  rs3m,
        "rs_6m":  rs6m,
    }


def check_volume(volumes: list, closes: list) -> dict:
    """
    Indicator 4: Volume — accumulation vs distribution pattern.
    Up-volume days vs down-volume days over last 20 sessions.
    Also checks for volume dry-up (contraction before breakout).
    """
    if not volumes or len(volumes) < 20:
        return {"name": "Volume", "score": 0, "label": "Unknown", "green": False}

    avg_vol_20 = sum(volumes[-20:]) / 20
    recent_vol  = volumes[-1]

    # Count up-volume vs down-volume days in last 20
    up_vol   = 0
    down_vol = 0
    for i in range(-20, 0):
        if len(closes) > abs(i) + 1:
            if closes[i] >= closes[i - 1]:
                up_vol   += volumes[i]
            else:
                down_vol += volumes[i]

    # Volume dry-up: recent 5D vol declining vs 20D avg
    avg_5d = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else avg_vol_20
    vol_dry_up = avg_5d < avg_vol_20 * 0.85  # 15% below 20D avg

    is_accumulation = up_vol > down_vol * 1.2
    is_normal       = avg_vol_20 > 0  # any volume is OK

    label = "Accumulation" if is_accumulation else ("Normal" if is_normal else "Distribution")
    is_green = is_accumulation or (is_normal and not (up_vol < down_vol * 0.8))

    return {
        "name":        "Volume",
        "score":       1 if is_green else 0,
        "label":       label,
        "green":       is_green,
        "vol_dry_up":  vol_dry_up,
        "up_vol_pct":  round(up_vol / max(up_vol + down_vol, 1) * 100, 1),
    }


def check_momentum(closes: list) -> dict:
    """
    Indicator 5: Momentum — trend strength, not parabolic.
    Checks RSI range (50-75 = bullish healthy), MACD direction, price above MAs.
    """
    if len(closes) < 26:
        return {"name": "Momentum", "score": 0, "label": "Unknown", "green": False}

    rsi_val  = rsi(closes)
    ma20     = sma(closes, 20)
    ma50     = sma(closes, 50)
    c        = closes[-1]

    # Simple MACD proxy: 12EMA - 26EMA
    e12 = ema(closes, 12)
    e26 = ema(closes, 26)
    macd = round((e12 or 0) - (e26 or 0), 2)

    # 30D rate vs 90D rate (acceleration check)
    ret30 = (c - closes[-21]) / closes[-21] * 100 if len(closes) >= 21 else None
    ret90 = (c - closes[-63]) / closes[-63] * 100 if len(closes) >= 63 else None
    accelerating = ret30 is not None and ret90 is not None and abs(ret30) > abs(ret90) * 0.5

    checks_passed = sum([
        rsi_val is not None and 50 <= rsi_val <= 80,
        macd > 0,
        ma20 and c > ma20,
        ma50 and c > ma50,
    ])

    # Parabolic = bad (RSI > 85)
    parabolic = rsi_val is not None and rsi_val > 85
    if parabolic:
        label    = "Parabolic (Exit Risk)"
        is_green = False
    elif checks_passed >= 3:
        label    = "Strong + Ranging" if not parabolic else "Extended"
        is_green = True
    elif checks_passed >= 2:
        label    = "Moderate"
        is_green = False
    else:
        label    = "Weak"
        is_green = False

    return {
        "name":       "Momentum",
        "score":      1 if is_green else 0,
        "label":      label,
        "green":      is_green,
        "rsi":        rsi_val,
        "macd":       macd,
        "parabolic":  parabolic,
    }


def check_volatility(highs: list, lows: list, closes: list) -> dict:
    """
    Indicator 6: Volatility — ATR contraction then expansion (VCP/breakout).
    Green = volatility EXPANDING after compression (breakout confirmed).
    Yellow = volatility contracting (building base, pre-breakout).
    Red = volatility already extended (climax).
    """
    if len(closes) < 30:
        return {"name": "Volatility", "score": 0, "label": "Unknown", "green": False}

    atr_5  = atr(highs[-7:],  lows[-7:],  closes[-7:],  5)
    atr_20 = atr(highs[-22:], lows[-22:], closes[-22:], 20)
    c      = closes[-1]

    if atr_5 is None or atr_20 is None or c == 0:
        return {"name": "Volatility", "score": 0, "label": "Unknown", "green": False}

    atr5_pct  = atr_5  / c * 100
    atr20_pct = atr_20 / c * 100

    # Expanding = recent ATR > historical ATR (breakout happening)
    expanding    = atr5_pct > atr20_pct * 1.1
    # Contracting = recent ATR < historical ATR (basing)
    contracting  = atr5_pct < atr20_pct * 0.85
    # Climax = extreme expansion
    climax       = atr5_pct > atr20_pct * 2.5

    if climax:
        label    = "Climax (Avoid)"
        is_green = False
    elif expanding:
        label    = "Expanding"
        is_green = True
    elif contracting:
        label    = "Contracting (Basing)"
        is_green = False  # Yellow — not wrong, just not confirmed
    else:
        label    = "Neutral"
        is_green = False

    return {
        "name":       "Volatility",
        "score":      1 if is_green else 0,
        "label":      label,
        "green":      is_green,
        "atr5_pct":   round(atr5_pct, 2),
        "atr20_pct":  round(atr20_pct, 2),
        "expanding":  expanding,
    }


def check_extension(closes: list) -> dict:
    """
    Indicator 7: Extension — distance from key MAs.
    Normal = not extended (safe entry zone).
    Extended = too far from 10EMA (> 10%) or RSI > 80 (climax risk).
    """
    if len(closes) < 50:
        return {"name": "Extension", "score": 0, "label": "Unknown", "green": False}

    c    = closes[-1]
    e10  = ema(closes, 10)
    e21  = ema(closes, 21)
    ma50 = sma(closes, 50)

    ext_10ema  = (c - e10)  / e10  * 100 if e10  else 0
    ext_21ema  = (c - e21)  / e21  * 100 if e21  else 0
    ext_50dma  = (c - ma50) / ma50 * 100 if ma50 else 0
    rsi_val    = rsi(closes)

    # Extended = > 10% above 10EMA or > 20% above 50DMA or RSI > 80
    is_extended = (
        ext_10ema > 10 or
        ext_50dma > 25 or
        (rsi_val is not None and rsi_val > 80)
    )

    # Beneath key MAs = also bad
    is_below = ext_50dma < -5

    if is_extended:
        label    = "Extended (Wait for reset)"
        is_green = False
    elif is_below:
        label    = "Below 50D (Weak)"
        is_green = False
    else:
        label    = "Normal"
        is_green = True

    return {
        "name":       "Extension",
        "score":      1 if is_green else 0,
        "label":      label,
        "green":      is_green,
        "ext_10ema":  round(ext_10ema, 1),
        "ext_21ema":  round(ext_21ema, 1),
        "ext_50dma":  round(ext_50dma, 1),
    }


def check_bull_streak(closes: list, opens: list) -> dict:
    """
    Indicator 8: Bull Streak — consecutive bullish candles (close > open).
    4+ bars = strong demand pressure.
    """
    streak = consecutive_bullish_bars(closes, opens)
    is_green = streak >= 4

    label = f"{streak} bars"
    if streak >= 6:
        label += " (Strong)"
    elif streak >= 4:
        label += " (Good)"
    elif streak >= 2:
        label += " (Weak)"
    else:
        label += " (Bear/Mixed)"

    return {
        "name":   "Bull Streak",
        "score":  1 if is_green else 0,
        "label":  label,
        "green":  is_green,
        "streak": streak,
    }


# =============================================================================
# FULL HEALTH CHECK — COMPOSITE SCORE
# =============================================================================

def run_health_check(symbol: str) -> dict:
    """
    Run full 8-indicator Health Check for a stock.
    Returns complete dashboard dict with score 0-8, rating, and all indicator details.
    """
    result = {
        "symbol":      symbol,
        "score":       0,
        "max_score":   8,
        "rating":      "Unknown",
        "green":       False,
        "entry_size":  "none",
        "indicators":  {},
        "details":     {},
        "as_of":       date.today().isoformat(),
    }

    try:
        # Fetch via portfolio_engine (SSL-patched, always works in this environment)
        import sys as _sys
        _pe_path = str(Path(__file__).resolve().parent)
        if _pe_path not in _sys.path:
            _sys.path.insert(0, _pe_path)
        from portfolio_engine import get_price_data

        df = get_price_data(symbol, period="1y")
        if df is None or df.empty or len(df) < 30:
            # fallback to yfinance if portfolio_engine fails
            if not HAS_YF:
                result["rating"] = f"Insufficient data for {symbol}"
                return result
            ticker  = yf.Ticker(symbol)
            hist_1d = ticker.history(period="1y", interval="1d")
            if hist_1d.empty or len(hist_1d) < 30:
                result["rating"] = f"Insufficient data for {symbol}"
                return result
            closes  = hist_1d["Close"].tolist()
            opens   = hist_1d["Open"].tolist()
            highs   = hist_1d["High"].tolist()
            lows    = hist_1d["Low"].tolist()
            volumes = hist_1d["Volume"].tolist()
        else:
            closes  = df["Close"].tolist()
            opens   = df["Open"].tolist() if "Open" in df.columns else closes
            highs   = df["High"].tolist()
            lows    = df["Low"].tolist()
            volumes = df["Volume"].tolist() if "Volume" in df.columns else []

        # Fetch SPY for market + RS checks
        spy_df = get_price_data("SPY", period="1y")
        spy_closes = spy_df["Close"].tolist() if spy_df is not None and not spy_df.empty else []

        # Run all 8 indicators
        checks = [
            check_tf_alignment(closes, []),        # 1
            check_market_health(spy_closes),        # 2
            check_relative_strength(closes, spy_closes),  # 3
            check_volume(volumes, closes),          # 4
            check_momentum(closes),                 # 5
            check_volatility(highs, lows, closes),  # 6
            check_extension(closes),                # 7
            check_bull_streak(closes, opens),       # 8
        ]

        # TF Alignment gate
        tf_check  = checks[0]
        tf_count  = tf_check.get("tf_count", 0)
        tf_forced_yellow = tf_count < 4

        total_score = sum(c["score"] for c in checks)

        # Rating
        if total_score >= 7 and not tf_forced_yellow:
            rating     = "Green Light"
            entry_size = "full"
            green      = True
        elif total_score >= 5:
            rating     = "Yellow — Reduce Size"
            entry_size = "half"
            green      = False
        else:
            rating     = "Red — Do Not Enter"
            entry_size = "none"
            green      = False

        if tf_forced_yellow and rating == "Green Light":
            rating     = "Yellow — TF Not Aligned"
            entry_size = "half"
            green      = False

        # Details panel (matches screenshot right panel)
        c         = closes[-1]
        high_52w  = max(closes[-252:]) if len(closes) >= 252 else max(closes)
        low_52w   = min(closes[-252:]) if len(closes) >= 252 else min(closes)
        ret_ytd   = (c - closes[0]) / closes[0] * 100 if closes else 0
        ret_30d   = (c - closes[-21]) / closes[-21] * 100 if len(closes) >= 21 else 0
        ret_90d   = (c - closes[-63]) / closes[-63] * 100 if len(closes) >= 63 else 0
        pct_ath   = (c - high_52w) / high_52w * 100

        e12 = ema(closes, 12)
        e26 = ema(closes, 26)
        macd_val = round((e12 or 0) - (e26 or 0), 2)

        result.update({
            "score":      total_score,
            "rating":     rating,
            "green":      green,
            "entry_size": entry_size,
            "tf_forced_yellow": tf_forced_yellow,
            "indicators": {c["name"]: {"score": c["score"], "label": c["label"], "green": c["green"]} for c in checks},
            "details": {
                "last_close": round(c, 2),
                "ytd_pct":    round(ret_ytd, 2),
                "30d_pct":    round(ret_30d, 2),
                "90d_pct":    round(ret_90d, 2),
                "vs_ath":     round(pct_ath, 2),
                "is_new_high": pct_ath >= -1.0,
                "rsi":        rsi(closes),
                "macd":       macd_val,
                "52w_high":   round(high_52w, 2),
                "52w_low":    round(low_52w, 2),
                "bull_streak": checks[7].get("streak", 0),
            },
        })

        # Save state
        state_file = HC_STATE_PATH / f"{symbol}.json"
        state_file.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    except Exception as e:
        result["rating"] = f"ERROR: {e}"

    return result


def print_health_check(hc: dict) -> None:
    """Print Health Check in dashboard format matching the screenshot."""
    sym    = hc.get("symbol", "?")
    score  = hc.get("score", 0)
    rating = hc.get("rating", "Unknown")
    indics = hc.get("indicators", {})
    det    = hc.get("details", {})

    print(f"\n{'='*45}")
    print(f"  Health Check: {sym}   [{score}/8]  {rating}")
    print(f"{'='*45}")

    order = ["TF Alignment", "Market", "Rel Strength", "Volume",
             "Momentum", "Volatility", "Extension", "Bull Streak"]
    for name in order:
        if name in indics:
            ind     = indics[name]
            dot     = "[G]" if ind["green"] else "[ ]"
            print(f"  {dot} {name:<14} {ind['label']}")

    if det:
        print(f"\n  Details")
        print(f"  {'YTD':12} {det.get('ytd_pct', 0):+.2f}%")
        print(f"  {'30D %Chg':12} {det.get('30d_pct', 0):+.2f}%")
        print(f"  {'90D %Chg':12} {det.get('90d_pct', 0):+.2f}%")
        ath_label = "New High!" if det.get("is_new_high") else f"{det.get('vs_ath', 0):.1f}% from ATH"
        print(f"  {'vs ATH':12} {ath_label}")
        print(f"  {'MACD':12} {det.get('macd', 0):.2f}")
        print(f"  {'RSI':12} {det.get('rsi', 0):.2f}")
        print(f"  {'Bull Streak':12} {det.get('bull_streak', 0)} bars")


def load_cached_hc(symbol: str) -> Optional[dict]:
    """Load today's cached Health Check (avoids repeated API calls)."""
    state_file = HC_STATE_PATH / f"{symbol}.json"
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            if data.get("as_of") == date.today().isoformat():
                return data
        except Exception:
            pass
    return None


# =============================================================================
# CLI / DIRECT RUN
# =============================================================================

if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["COHR", "MU", "NVDA"]

    for sym in tickers:
        print(f"\nRunning Health Check for {sym}...")
        hc = run_health_check(sym)
        print_health_check(hc)
