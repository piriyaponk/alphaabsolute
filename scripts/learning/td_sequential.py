"""
AlphaAbsolute — TD Sequential (Tom DeMark)
==========================================
PRIMARY market timing indicator integrated into PULSE + NRGC entry strategy.

TD Sequential = Setup Phase (9 bars) + Countdown Phase (13 bars)

INTEGRATION RULES:
- Market regime: SPY+QQQ TD status gates ALL new entries system-wide
- NRGC Phase 3 entry: REQUIRES TD not in Sell Setup 7-9 or Sell Countdown 9-13
- Stock level: TD Buy Setup 9 on individual name = +2 EMLS boost, priority entry
- Health Check: TD status feeds into Market indicator score

SELL SETUP:  9 consecutive bars where close > close[4 bars ago] = exhaustion warning
BUY SETUP:  -9 consecutive bars where close < close[4 bars ago] = oversold exhaustion
SELL COUNTDOWN: After Sell Setup 9 — count bars where close >= high[2] up to 13 = reversal
BUY COUNTDOWN:  After Buy Setup -9 — count bars where close <= low[2] up to -13 = reversal
"""

import os
import ssl
import json
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

# ── SSL bypass for Cloudflare WARP environments ────────────────────────────────
ssl._create_default_https_context = ssl._create_unverified_context
os.environ.setdefault("CURL_CA_BUNDLE", "")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "")

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# Patch requests session to skip SSL verification globally
try:
    import requests
    from requests import Session as _Session
    _orig_request = _Session.request
    def _patched_request(self, *args, **kwargs):
        kwargs.setdefault("verify", False)
        return _orig_request(self, *args, **kwargs)
    _Session.request = _patched_request
except Exception:
    pass

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False

ROOT = Path(__file__).resolve().parents[2]
TD_STATE_PATH = ROOT / "data/td_sequential"
TD_STATE_PATH.mkdir(parents=True, exist_ok=True)


# =============================================================================
# CORE TD SEQUENTIAL CALCULATIONS
# =============================================================================

def calc_td_setup(closes: list) -> list:
    """
    TD Setup Phase — counts consecutive bars of price exhaustion.

    Sell Setup: close > close[4 bars ago] -- counts +1 to +9
    Buy Setup:  close < close[4 bars ago] -- counts -1 to -9
    Count resets on any interruption (one bar in opposite direction).

    Returns list of int same length as closes.
    +9 = sell setup COMPLETE (upside exhaustion, potential top)
    -9 = buy setup COMPLETE (downside exhaustion, potential bottom)
    """
    n = len(closes)
    counts = [0] * n
    sell_count = 0
    buy_count = 0

    for i in range(4, n):
        c  = closes[i]
        c4 = closes[i - 4]

        if c > c4:
            sell_count += 1
            buy_count = 0
            counts[i] = min(sell_count, 9)
        elif c < c4:
            buy_count += 1
            sell_count = 0
            counts[i] = -min(buy_count, 9)
        else:
            # Equal close — reset both
            sell_count = 0
            buy_count = 0
            counts[i] = 0

    return counts


def calc_td_countdown(closes: list, highs: list, lows: list, setup: list) -> list:
    """
    TD Countdown Phase — begins after Setup 9 completes.

    Sell Countdown: close >= high[2 bars ago] -- counts +1 to +13
    Buy Countdown:  close <= low[2 bars ago]  -- counts -1 to -13

    Countdown can be cancelled by a new Setup in opposite direction.
    13 = countdown COMPLETE (strong reversal signal).

    Returns list of int same length as closes.
    """
    n = len(closes)
    counts = [0] * n

    sell_cd = 0
    buy_cd  = 0
    in_sell_cd = False
    in_buy_cd  = False

    for i in range(2, n):
        # New Sell Setup 9 starts sell countdown (cancels buy countdown)
        if setup[i] == 9:
            in_sell_cd = True
            in_buy_cd  = False
            sell_cd = 0
            buy_cd  = 0

        # New Buy Setup -9 starts buy countdown (cancels sell countdown)
        elif setup[i] == -9:
            in_buy_cd  = True
            in_sell_cd = False
            buy_cd  = 0
            sell_cd = 0

        if in_sell_cd:
            if closes[i] >= highs[i - 2]:
                sell_cd += 1
            counts[i] = min(sell_cd, 13)
            if sell_cd >= 13:
                in_sell_cd = False  # Countdown complete — wait for new setup

        elif in_buy_cd:
            if closes[i] <= lows[i - 2]:
                buy_cd += 1
            counts[i] = -min(buy_cd, 13)
            if buy_cd >= 13:
                in_buy_cd = False

    return counts


# =============================================================================
# SIGNAL EXTRACTION — SINGLE SYMBOL
# =============================================================================

def _fetch_ohlc_portfolio_engine(symbol: str, period: str = "6mo") -> Optional[dict]:
    """
    Fallback OHLC via project's portfolio_engine (already SSL-patched, always works).
    Returns dict with lists: closes, highs, lows, opens, volumes
    """
    try:
        import sys
        _pe_path = str(Path(__file__).resolve().parents[1] / "paper_trading")
        if _pe_path not in sys.path:
            sys.path.insert(0, _pe_path)
        from portfolio_engine import get_price_data
        df = get_price_data(symbol, period=period)
        if df is None or df.empty or len(df) < 15:
            return None
        return {
            "closes":  df["Close"].tolist(),
            "highs":   df["High"].tolist(),
            "lows":    df["Low"].tolist(),
            "opens":   df["Open"].tolist() if "Open" in df.columns else df["Close"].tolist(),
            "volumes": df["Volume"].tolist() if "Volume" in df.columns else [],
        }
    except Exception:
        return None


def get_td_signal(symbol: str, period: str = "6mo", interval: str = "1d") -> dict:
    """
    Fetch OHLC data and compute full TD Sequential analysis for one symbol.

    Returns:
      symbol:            ticker
      setup_count:       current setup bar (+9=sell done, -9=buy done, 0-8=in progress)
      countdown_count:   current countdown bar (+13=sell done, -13=buy done)
      setup_complete:    bool — setup 9 just fired
      countdown_complete: bool — countdown 13 just fired
      signal:            string key for downstream logic
      warning:           human readable status line
      bars_to_complete:  bars needed to reach setup 9 (0 if complete)
      last_close:        latest closing price
      as_of:             date string
    """
    result = {
        "symbol":             symbol,
        "setup_count":        0,
        "countdown_count":    0,
        "setup_complete":     False,
        "countdown_complete": False,
        "signal":             "neutral",
        "warning":            "No active TD signal",
        "bars_to_complete":   0,
        "last_close":         None,
        "as_of":              date.today().isoformat(),
    }

    closes = highs = lows = None

    # Source 1: yfinance (with SSL bypass already patched)
    if HAS_YF:
        try:
            ticker = yf.Ticker(symbol)
            hist   = ticker.history(period=period, interval=interval)
            if not hist.empty and len(hist) >= 15:
                closes = hist["Close"].tolist()
                highs  = hist["High"].tolist()
                lows   = hist["Low"].tolist()
        except Exception:
            pass

    # Source 2: portfolio_engine fallback (already SSL-resolved, always works)
    if closes is None:
        pe = _fetch_ohlc_portfolio_engine(symbol, period)
        if pe:
            closes = pe["closes"]
            highs  = pe["highs"]
            lows   = pe["lows"]

    if not closes or len(closes) < 15:
        result["warning"] = f"No price data available for {symbol} (check internet/SSL)"
        return result

    try:

        setup     = calc_td_setup(closes)
        countdown = calc_td_countdown(closes, highs, lows, setup)

        last_setup = setup[-1]
        last_cd    = countdown[-1]
        last_close = closes[-1]

        result["setup_count"]     = last_setup
        result["countdown_count"] = last_cd
        result["last_close"]      = round(last_close, 2)

        # ── Classify signal ──────────────────────────────────────────────────
        if last_setup == 9:
            result["setup_complete"] = True
            result["signal"]  = "sell_setup_9"
            result["warning"] = "[!] TD SELL SETUP 9 COMPLETE — upside exhaustion, pullback expected 1-2 weeks"

        elif last_setup == -9:
            result["setup_complete"] = True
            result["signal"]  = "buy_setup_9"
            result["warning"] = "[+] TD BUY SETUP 9 COMPLETE — downside exhaustion, bounce probable"

        elif last_cd == 13:
            result["countdown_complete"] = True
            result["signal"]  = "sell_countdown_13"
            result["warning"] = "[!!] TD SELL COUNTDOWN 13 — STRONG reversal signal, reduce all longs"

        elif last_cd == -13:
            result["countdown_complete"] = True
            result["signal"]  = "buy_countdown_13"
            result["warning"] = "[++] TD BUY COUNTDOWN 13 — STRONG buy signal, high probability reversal up"

        elif last_setup > 0:
            bars_left = 9 - last_setup
            result["signal"]           = f"sell_setup_{last_setup}"
            result["bars_to_complete"] = bars_left
            if last_setup >= 7:
                result["warning"] = f"[~] TD Sell Setup {last_setup}/9 — CAUTION ZONE ({bars_left} bars to exhaustion)"
            else:
                result["warning"] = f"TD Sell Setup {last_setup}/9 in progress ({bars_left} bars to exhaustion)"

        elif last_setup < 0:
            bars_left = 9 + last_setup  # last_setup is negative
            result["signal"]           = f"buy_setup_{-last_setup}"
            result["bars_to_complete"] = bars_left
            result["warning"] = f"TD Buy Setup {-last_setup}/9 in progress ({bars_left} bars to oversold completion)"

        elif last_cd > 0:
            result["signal"]  = f"sell_countdown_{last_cd}"
            result["warning"] = f"TD Sell Countdown {last_cd}/13 in progress"

        elif last_cd < 0:
            result["signal"]  = f"buy_countdown_{-last_cd}"
            result["warning"] = f"TD Buy Countdown {-last_cd}/13 in progress"

        # ── Save per-symbol state ────────────────────────────────────────────
        state_file = TD_STATE_PATH / f"{symbol}.json"
        state_file.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    except Exception as e:
        result["warning"] = f"Error computing TD for {symbol}: {e}"

    return result


# =============================================================================
# MARKET REGIME SIGNAL — COMPOSITE SPY + QQQ
# =============================================================================

def get_td_regime_signal(symbols: list = None) -> dict:
    """
    Composite TD Sequential signal for overall market regime.
    Uses SPY + QQQ by default. Add IWM, SMH for broader confirmation.

    Regime Modifier scoring:
      sell_countdown_13  -> -2 points (strongest bear)
      sell_setup_9       -> -1 point  (exhaustion warning)
      sell_setup_7 or 8  -> -0.5 pts  (building caution)
      buy_countdown_13   -> +2 points (strongest bull)
      buy_setup_9        -> +1 point  (oversold exhaustion)
      buy_setup_7 or 8   -> +0.5 pts  (building buy signal)

    Returns:
      regime_modifier: 'bullish' | 'neutral' | 'caution' | 'warning' | 'reversal'
      score: avg score across symbols (-2 to +2)
      signals: per-symbol signal dicts
      summary: human readable regime statement
    """
    if symbols is None:
        symbols = ["SPY", "QQQ"]

    signals = {}
    score   = 0.0

    for sym in symbols:
        sig = get_td_signal(sym)
        signals[sym] = sig
        s = sig["signal"]

        if s == "sell_countdown_13":
            score -= 2.0
        elif s == "sell_setup_9":
            score -= 1.0
        elif s.startswith("sell_setup_") and sig["setup_count"] >= 7:
            score -= 0.5
        elif s == "buy_countdown_13":
            score += 2.0
        elif s == "buy_setup_9":
            score += 1.0
        elif s.startswith("buy_setup_") and sig["setup_count"] <= -7:
            score += 0.5

    avg = score / max(len(symbols), 1)

    if avg <= -1.5:
        modifier = "reversal"
        summary  = "TD REVERSAL: Multiple sell signals confirmed — hold cash, no new longs"
    elif avg <= -0.75:
        modifier = "warning"
        summary  = "TD WARNING: Sell setup active — reduce size, pause new entries until resolved"
    elif avg <= -0.25:
        modifier = "caution"
        summary  = "TD CAUTION: Sell setup building (7-8/9) — reduce new position size 50%"
    elif avg >= 1.5:
        modifier = "bullish"
        summary  = "TD BULLISH: Buy countdown/setup complete — favorable window, deploy capital"
    elif avg >= 0.75:
        modifier = "bullish"
        summary  = "TD BULLISH: Buy setup confirmed — good entry conditions"
    else:
        modifier = "neutral"
        summary  = "TD NEUTRAL: No exhaustion signal active"

    result = {
        "regime_modifier": modifier,
        "score":           round(avg, 2),
        "signals":         signals,
        "summary":         summary,
        "as_of":           date.today().isoformat(),
    }

    state_file = TD_STATE_PATH / "_market_regime.json"
    state_file.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    return result


# =============================================================================
# AUTO TRADER INTEGRATION
# =============================================================================

def apply_td_regime_modifier(base_regime: str, td_signal: dict) -> str:
    """
    Downgrade market regime based on TD Sequential signals.
    TD NEVER upgrades regime — it only applies downgrade pressure.

    Downgrade matrix:
      risk-on  + caution/warning  -> neutral
      risk-on  + reversal         -> risk-off
      neutral  + warning          -> neutral (flag only)
      neutral  + reversal         -> risk-off
      risk-off + any              -> risk-off (already defensive)
    """
    modifier = td_signal.get("regime_modifier", "neutral")

    if base_regime == "risk-on":
        if modifier == "reversal":
            return "risk-off"
        elif modifier in ("warning", "caution"):
            return "neutral"

    elif base_regime == "neutral":
        if modifier == "reversal":
            return "risk-off"

    return base_regime


def td_entry_gate(symbol: str) -> dict:
    """
    Per-stock TD Sequential entry gate for NRGC Phase 3 confirmation.

    PASS (green light):
      - TD Buy Setup 9 complete   -> boost +2
      - TD Buy Countdown 13       -> boost +2
      - TD Buy Countdown in prog  -> boost +1
      - TD Neutral (no sell)      -> boost 0

    FAIL (block entry):
      - TD Sell Setup 7, 8, or 9  -> block (exhaustion zone)
      - TD Sell Countdown 10-13   -> block (topping process active)

    Returns: {pass: bool, reason: str, signal: str, setup_count: int, boost: int}
    The 'boost' value is added to EMLS score for final ranking.
    """
    sig      = get_td_signal(symbol)
    setup    = sig["setup_count"]
    countdown = sig["countdown_count"]
    signal   = sig["signal"]

    # Strong buy signals — pass with boost
    if signal in ("buy_setup_9", "buy_countdown_13"):
        return {
            "pass":        True,
            "reason":      f"TD BUY SIGNAL: {sig['warning']}",
            "signal":      signal,
            "setup_count": setup,
            "boost":       2,
        }

    if signal.startswith("buy_countdown_"):
        return {
            "pass":        True,
            "reason":      f"TD Buy Countdown active: {countdown}/13",
            "signal":      signal,
            "setup_count": setup,
            "boost":       1,
        }

    # Sell exhaustion zone — BLOCK entry
    if setup >= 7:
        return {
            "pass":        False,
            "reason":      f"TD BLOCKED: Sell Setup {setup}/9 — exhaustion zone, wait for reset",
            "signal":      signal,
            "setup_count": setup,
            "boost":       -1,
        }

    if countdown >= 10:
        return {
            "pass":        False,
            "reason":      f"TD BLOCKED: Sell Countdown {countdown}/13 — topping process active",
            "signal":      signal,
            "setup_count": setup,
            "boost":       -2,
        }

    # Neutral — no block, no boost
    return {
        "pass":        True,
        "reason":      f"TD OK: {sig['warning']}",
        "signal":      signal,
        "setup_count": setup,
        "boost":       0,
    }


def load_cached_td_regime() -> Optional[dict]:
    """Load today's cached market regime (avoids repeated API calls)."""
    state_file = TD_STATE_PATH / "_market_regime.json"
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
    print("=" * 60)
    print("  TD Sequential — Market Regime & Stock Gate Check")
    print("=" * 60)

    # Market-level regime
    print("\n[Market Regime] Checking SPY + QQQ + IWM ...")
    regime = get_td_regime_signal(["SPY", "QQQ", "IWM"])
    print(f"\n  Regime Modifier : {regime['regime_modifier'].upper()}")
    print(f"  Composite Score : {regime['score']}")
    print(f"  Summary         : {regime['summary']}")
    print()

    for sym, sig in regime["signals"].items():
        s  = sig["setup_count"]
        cd = sig["countdown_count"]
        print(f"  {sym:<5} Setup={s:+3}/9  Countdown={cd:+3}/13  | {sig['warning']}")

    # Stock-level gates
    print("\n[Stock TD Gates] NRGC Phase 3 candidates ...")
    print("-" * 60)
    watchlist = ["COHR", "MU", "WDC", "NVDA", "PLTR", "RKLB", "AMAT", "RGTI"]
    for ticker in watchlist:
        gate   = td_entry_gate(ticker)
        status = "PASS" if gate["pass"] else "FAIL"
        boost  = f"boost={gate['boost']:+d}" if gate["boost"] != 0 else "no boost"
        print(f"  {ticker:<6} [{status}] {boost} | {gate['reason']}")

    print("\n[Saved] TD state files written to data/td_sequential/")
