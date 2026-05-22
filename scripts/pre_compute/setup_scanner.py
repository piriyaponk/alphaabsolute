"""
AlphaAbsolute v2 -- A08 Setup Scanner
=======================================
For every stock in Top 10 Active (A06) + Big Shot Candidates (A07):
  - Identify the setup type (BKT/VCP/CWH/SPR/PPT/EMA/VPS/FIB)
  - Calculate entry pivot, stop, target, R:R
  - Apply TD size modifier
  - Grade A/B/C

Minimum R:R = 3:1 (hard rule — skip if below).
TD Sequential is a size modifier, NOT a gate.

Output: data/setups/setups_today.json
Run: 8:30 AM daily (after A06 + A07)

Cost: $0 (Python only)
"""

from __future__ import annotations
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

OUT_DIR    = ROOT / "data" / "setups"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SETUP_FILE = OUT_DIR / "setups_today.json"

MIN_RR = 3.0     # minimum R:R to output a setup (hard rule)


# ── Data helpers ──────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _load_list_json(path: Path) -> list:
    try:
        d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return d if isinstance(d, list) else d.get("results", d.get("candidates", []))
    except Exception:
        return []


def _fetch_bars(ticker: str, days: int = 90) -> list[dict]:
    """Returns list of OHLCV dicts, oldest first."""
    try:
        from data_engine import get_ohlcv
        df = get_ohlcv(ticker, period="6mo")
        if df is not None and not df.empty:
            bars = []
            for idx, row in df.iterrows():
                bars.append({
                    "date":   str(idx.date()) if hasattr(idx, "date") else str(idx),
                    "open":   float(row.get("Open",   0)),
                    "high":   float(row.get("High",   0)),
                    "low":    float(row.get("Low",    0)),
                    "close":  float(row.get("Close",  0)),
                    "volume": float(row.get("Volume", 0)),
                })
            return bars[-days:]
    except Exception:
        pass
    return []


def _sma(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _ema(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _avg_volume(bars: list[dict], period: int = 20) -> Optional[float]:
    vols = [b["volume"] for b in bars[-period:]]
    return sum(vols) / len(vols) if vols else None


# ── TD Signal reader ──────────────────────────────────────────────────────────

def get_td_signal(ticker: str) -> str:
    """Read TD state from file (written by exhaustion_monitor or td_sequential)."""
    td_file = ROOT / "data" / "td_sequential" / f"{ticker}.json"
    if not td_file.exists():
        return "Neutral"
    try:
        state = json.loads(td_file.read_text(encoding="utf-8"))
        ss = state.get("setup_count", 0)
        cd = state.get("countdown_count", 0)
        if   cd >= 13:   return "SellCountdown13"
        elif cd >= 10:   return "SellCountdown10"
        elif ss >= 9:    return "SellSetup9"
        elif ss >= 7:    return "SellSetup7"
        elif ss <= -9:   return "BuySetup9"
        else:            return "Neutral"
    except Exception:
        return "Neutral"


def td_size_modifier(td_signal: str) -> float:
    """v2: TD is size modifier only, never a gate."""
    return {
        "BuySetup9":      1.25,
        "Neutral":        1.00,
        "SellSetup7":     0.75,
        "SellSetup9":     0.50,
        "SellCountdown10": 0.50,
        "SellCountdown13": 0.25,
    }.get(td_signal, 1.00)


# ── Setup detection ───────────────────────────────────────────────────────────

def detect_setup(bars: list[dict]) -> tuple[str, float, float, str]:
    """
    Detect the highest-quality setup from recent price action.
    Returns: (setup_code, pivot_price, stop_price, entry_note)

    Priority order: VCP > BKT > CWH > PPT > EMA > SPR > VPS > FIB
    """
    if not bars or len(bars) < 20:
        return "NONE", 0.0, 0.0, "Insufficient data"

    closes  = [b["close"] for b in bars]
    highs   = [b["high"]  for b in bars]
    lows    = [b["low"]   for b in bars]
    volumes = [b["volume"] for b in bars]
    last    = bars[-1]
    current = closes[-1]

    avg_vol_20 = _avg_volume(bars, 20) or 1
    ema10 = _ema(closes, 10)
    ema21 = _ema(closes, 21)
    sma50 = _sma(closes, min(50, len(closes)))

    # ── VCP: Volatility Contraction Pattern ──────────────────────────────────
    # 3+ contracting swings (each swing smaller than previous), volume drying
    # Look at last 30-60 bars for swing structure
    if len(bars) >= 30:
        # Measure % range in 3 consecutive 10-day periods
        range1 = (max(highs[-30:-20]) - min(lows[-30:-20])) / closes[-25] if closes[-25] > 0 else 0
        range2 = (max(highs[-20:-10]) - min(lows[-20:-10])) / closes[-15] if closes[-15] > 0 else 0
        range3 = (max(highs[-10:])    - min(lows[-10:]))    / closes[-5]  if closes[-5]  > 0 else 0
        vol_recent = _avg_volume(bars, 10) or 1

        contracting = range1 > range2 > range3
        volume_drying = vol_recent < avg_vol_20 * 0.70  # volume < 70% of 20-day avg

        if contracting and volume_drying:
            pivot = max(highs[-5:]) * 1.001  # just above recent tight high
            stop  = min(lows[-15:]) * 0.99   # below recent range low
            return "VCP", round(pivot, 2), round(stop, 2), (
                f"VCP: contracting ranges {range1*100:.1f}%→{range2*100:.1f}%→{range3*100:.1f}%, "
                f"volume {vol_recent/avg_vol_20*100:.0f}% of avg — tight pivot at {pivot:.2f}"
            )

    # ── BKT: Breakout from resistance ────────────────────────────────────────
    # Price clearing a clear resistance level on high volume
    if len(bars) >= 20:
        resistance = max(highs[-20:-1])  # prior highs
        if current >= resistance * 0.995 and last["volume"] >= avg_vol_20 * 1.5:
            pivot = resistance
            stop  = min(lows[-10:]) * 0.99
            return "BKT", round(pivot, 2), round(stop, 2), (
                f"BKT: clearing {pivot:.2f} resistance on {last['volume']/avg_vol_20:.1f}x avg volume"
            )

    # ── PPT: Pocket Pivot ─────────────────────────────────────────────────────
    # Up-day volume > any down-day volume in prior 10 sessions
    if len(bars) >= 11:
        up_today = last["close"] > last["open"]
        if up_today:
            down_vols = [bars[-i]["volume"] for i in range(2, 12)
                         if bars[-i]["close"] < bars[-i]["open"]]
            if down_vols and last["volume"] > max(down_vols):
                if ema10 and current >= ema10 * 0.95:  # within 5% of 10EMA
                    pivot = current
                    stop  = (ema50 if (ema50 := sma50) else current * 0.92)
                    return "PPT", round(pivot, 2), round(stop, 2), (
                        f"PPT: up-day vol {last['volume']/avg_vol_20:.1f}x > all prior 10d down-day vols"
                    )

    # ── EMA Pullback ─────────────────────────────────────────────────────────
    # Price pulling back to 10EMA or 21EMA in uptrend (50DMA still rising)
    if ema10 and ema21 and sma50 and current > sma50:
        touch_10 = abs(current - ema10) / ema10 < 0.015  # within 1.5% of 10EMA
        touch_21 = abs(current - ema21) / ema21 < 0.015  # within 1.5% of 21EMA
        uptrend  = ema10 > ema21 > sma50                  # proper alignment

        if uptrend and (touch_10 or touch_21):
            pivot = current
            stop  = sma50 * 0.99
            ema_ref = "10EMA" if touch_10 else "21EMA"
            return "EMA", round(pivot, 2), round(stop, 2), (
                f"EMA pullback to {ema_ref} ({ema10 if touch_10 else ema21:.2f}) in uptrend — stop below 50DMA"
            )

    # ── SPR: Wyckoff Spring ───────────────────────────────────────────────────
    # Price dipped below support then snapped back with volume
    if len(bars) >= 20:
        support = min(lows[-20:-5])  # prior support zone
        recent_low = min(lows[-5:])
        if recent_low < support and current > support * 1.01:
            if last["volume"] >= avg_vol_20 * 1.2:  # volume confirmation
                pivot = current
                stop  = recent_low * 0.99
                return "SPR", round(pivot, 2), round(stop, 2), (
                    f"SPR: Spring — dipped below support {support:.2f} then snapped back, "
                    f"vol {last['volume']/avg_vol_20:.1f}x avg"
                )

    # ── FIB: Fibonacci Retracement ────────────────────────────────────────────
    # Price retraces to 38.2% or 50% of prior advance with confluence
    if len(bars) >= 40:
        swing_high = max(highs[-40:])
        swing_low  = min(lows[-40:highs.index(swing_high) + 1]) if max(highs[-40:]) in highs else min(lows[-40:])
        fib_382 = swing_high - (swing_high - swing_low) * 0.382
        fib_500 = swing_high - (swing_high - swing_low) * 0.500

        at_382 = abs(current - fib_382) / fib_382 < 0.015
        at_500 = abs(current - fib_500) / fib_500 < 0.015

        if at_382 or at_500:
            level = fib_382 if at_382 else fib_500
            name  = "38.2%" if at_382 else "50.0%"
            pivot = current
            stop  = swing_low * 0.99
            return "FIB", round(pivot, 2), round(stop, 2), (
                f"FIB retracement at {name} level ({level:.2f}) of {swing_low:.2f}→{swing_high:.2f} swing"
            )

    # No recognized setup
    return "NONE", current, current * 0.92, "No recognized setup pattern"


# ── Compute targets and R:R ───────────────────────────────────────────────────

def compute_rr(pivot: float, stop: float, setup_type: str) -> dict:
    """
    Compute price targets and R:R ratio.
    Target 1 = 3x risk, Target 2 = 5x risk.
    """
    if pivot <= 0 or stop <= 0:
        return {"rr_ratio": 0.0, "target_1": 0.0, "target_2": 0.0}

    risk = pivot - stop
    if risk <= 0:
        return {"rr_ratio": 0.0, "target_1": 0.0, "target_2": 0.0}

    target_1 = pivot + risk * 3.0   # 3:1 R:R
    target_2 = pivot + risk * 5.0   # 5:1 R:R (stretch)
    rr_ratio = (target_1 - pivot) / risk

    return {
        "rr_ratio":  round(rr_ratio, 2),
        "target_1":  round(target_1, 2),
        "target_2":  round(target_2, 2),
        "risk_pct":  round(risk / pivot * 100, 2),
    }


# ── Grade assignment ──────────────────────────────────────────────────────────

def assign_grade(mode: str, rr: float, setup_type: str, gates_passed: int) -> str:
    """
    Grade A: Mode A all 5 gates + BKT/VCP/CWH + R:R > 4:1
    Grade B: Mode A 4+ gates + any setup + R:R > 3:1
    Grade C: Monitor only — not ready to enter
    """
    if rr < MIN_RR:
        return "C"  # R:R below minimum — always C regardless

    if mode == "A":
        if rr >= 4.0 and setup_type in ("VCP", "BKT", "CWH") and gates_passed >= 5:
            return "A"
        elif rr >= 3.0 and gates_passed >= 4:
            return "B"
        else:
            return "C"
    else:  # Mode B
        if rr >= 3.0 and setup_type in ("BKT", "VCP"):
            return "A"
        elif rr >= 3.0:
            return "B"
        else:
            return "C"


# ── Process one candidate ─────────────────────────────────────────────────────

def process_ticker(ticker: str, mode: str, theme: Optional[str] = None,
                   gates_passed: int = 5) -> Optional[dict]:
    """
    Full setup scan for one ticker. Returns setup dict or None.
    """
    bars = _fetch_bars(ticker, days=90)
    if not bars or len(bars) < 20:
        return None

    current = bars[-1]["close"]
    if current <= 0:
        return None

    # Detect setup
    setup_type, pivot, stop, entry_note = detect_setup(bars)

    if setup_type == "NONE" or pivot <= 0 or stop <= 0:
        return None

    # Compute buy zone (pivot to pivot + 3%)
    buy_zone = [round(pivot, 2), round(pivot * 1.03, 2)]

    # R:R calculation
    rr_data = compute_rr(pivot, stop, setup_type)
    if rr_data["rr_ratio"] < MIN_RR:
        return None  # Skip — below minimum R:R

    # TD signal + size modifier
    td_signal = get_td_signal(ticker)
    size_mod  = td_size_modifier(td_signal)

    # Base size
    base_size = 10.0 if mode == "A" else 5.0
    recommended_size = round(base_size * size_mod, 1)

    # Grade
    grade = assign_grade(mode, rr_data["rr_ratio"], setup_type, gates_passed)

    # ADTV check
    adtv = None
    try:
        from data_engine import get_ohlcv
        df = get_ohlcv(ticker, period="6mo")
        if df is not None and not df.empty:
            recent = df.tail(126)
            adtv = float((recent["Close"] * recent["Volume"]).mean())
    except Exception:
        pass

    return {
        "ticker":              ticker,
        "mode":                mode,
        "theme":               theme,
        "setup_type":          setup_type,
        "current_price":       round(current, 2),
        "pivot":               round(pivot, 2),
        "buy_zone":            buy_zone,
        "stop":                round(stop, 2),
        "target_1":            rr_data["target_1"],
        "target_2":            rr_data["target_2"],
        "rr_ratio":            rr_data["rr_ratio"],
        "risk_pct":            rr_data["risk_pct"],
        "td_signal":           td_signal,
        "size_modifier":       size_mod,
        "recommended_size_pct": recommended_size,
        "setup_grade":         grade,
        "entry_note":          entry_note,
        "adtv_usd":            round(adtv, 0) if adtv else None,
        "wait_for_better_entry": rr_data["rr_ratio"] < MIN_RR,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  A08 Setup Scanner  [{today}]")
    print(f"{'='*55}")

    # Check regime
    health = _load_json(ROOT / "data" / "regime" / "market_health.json")
    regime = health.get("regime", "Unknown")

    # Load A06 top 10 active (Mode A)
    top10_file = ROOT / "data" / "leadership" / "top10_active.json"
    top10 = []
    if top10_file.exists():
        try:
            top10 = json.loads(top10_file.read_text(encoding="utf-8"))
            if isinstance(top10, dict):
                top10 = top10.get("leaders", [])
        except Exception:
            pass

    # Load A07 big shot candidates (Mode B)
    cand_data = _load_json(ROOT / "data" / "bigshot" / "candidates.json")
    bigshots  = cand_data.get("candidates", [])

    print(f"  Regime: {regime} | Mode A candidates: {len(top10)} | Mode B candidates: {len(bigshots)}")

    setups  = []
    skipped = 0

    # Process Mode A (Leaders)
    if health.get("leaders_ok", True):
        print(f"\n  Scanning {len(top10)} Mode A leaders...")
        for item in top10:
            ticker = item.get("ticker") if isinstance(item, dict) else item
            if not ticker:
                continue
            theme  = item.get("theme") if isinstance(item, dict) else None
            gates  = item.get("gates_passed", 5) if isinstance(item, dict) else 5

            result = process_ticker(ticker, "A", theme, gates)
            if result:
                setups.append(result)
                grade = result["setup_grade"]
                print(f"    {ticker:6} {result['setup_type']:4} | RR={result['rr_ratio']:.1f}x | "
                      f"Grade {grade} | TD={result['td_signal']}")
            else:
                skipped += 1
    else:
        print(f"  [BLOCKED] leaders_ok=False — no Mode A entries today (regime={regime})")

    # Process Mode B (Big Shots)
    if health.get("bigshot_ok", True):
        print(f"\n  Scanning {len(bigshots)} Mode B big shots...")
        for item in bigshots:
            ticker = item.get("ticker") if isinstance(item, dict) else item
            if not ticker:
                continue
            theme = item.get("theme") if isinstance(item, dict) else None

            result = process_ticker(ticker, "B", theme, gates_passed=3)
            if result:
                setups.append(result)
                grade = result["setup_grade"]
                print(f"    {ticker:6} {result['setup_type']:4} | RR={result['rr_ratio']:.1f}x | "
                      f"Grade {grade} | TD={result['td_signal']}")
            else:
                skipped += 1
    else:
        print(f"  [BLOCKED] bigshot_ok=False — no Mode B entries today (regime={regime})")

    # Sort: Grade A first, then B, then by R:R descending
    grade_order = {"A": 0, "B": 1, "C": 2}
    setups.sort(key=lambda x: (grade_order.get(x["setup_grade"], 3), -x["rr_ratio"]))

    # Filter: only output Grade A and B (C = monitor only)
    actionable = [s for s in setups if s["setup_grade"] in ("A", "B")]
    monitor    = [s for s in setups if s["setup_grade"] == "C"]

    print(f"\n  Results: {len(actionable)} actionable (A/B grade) | {len(monitor)} monitor only | {skipped} no setup")

    output = {
        "date":        today,
        "regime":      regime,
        "setups":      actionable,
        "monitor":     monitor,
        "total_scanned": len(top10) + len(bigshots),
        "skipped":     skipped,
        "min_rr":      MIN_RR,
        "note":        f"Grade A: {sum(1 for s in actionable if s['setup_grade']=='A')} | "
                       f"Grade B: {sum(1 for s in actionable if s['setup_grade']=='B')}",
        "generated_at": datetime.now().strftime("%H:%M"),
    }

    SETUP_FILE.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"  -> Written: {SETUP_FILE}")

    return output


if __name__ == "__main__":
    run()
