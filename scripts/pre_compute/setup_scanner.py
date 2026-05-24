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
    """v2: TD is size modifier only, never a gate.
    BOA-005 DEC-014 (2026-05-24): BuySetup9 bonus REMOVED (changed 1.25 → 1.00).
    Rationale: TD Buy Setup 9 scored 8/20 as direction signal (BOA-001, rejected).
    Applying it as +25% size boost contradicts that rejection. Setup 9 = potential
    price exhaustion, not acceleration. Sell-side modifiers unchanged — those
    correctly reduce size near exhaustion to preserve capital.
    """
    return {
        "BuySetup9":       1.00,   # BOA-005 DEC-014: neutral (was 1.25 — removed per BOA-001)
        "Neutral":         1.00,
        "SellSetup7":      0.75,
        "SellSetup9":      0.50,
        "SellCountdown10": 0.50,
        "SellCountdown13": 0.25,
    }.get(td_signal, 1.00)


# ── Setup detection ───────────────────────────────────────────────────────────

def detect_setup(bars: list[dict]) -> tuple[str, float, float, str]:
    """
    Detect the highest-quality setup from recent price action.
    Returns: (setup_code, pivot_price, stop_price, entry_note)

    Priority order: VCP > BKT > CWH > SOS > SPR > PPT > EMA > VPS > FIB
    Grade A eligible: VCP, BKT, CWH, SOS (institutional strength confirmation)
    Grade B max:      SPR, PPT, EMA, VPS, FIB
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
                if ema10 and current >= ema10 * 0.95:   # within 5% of 10EMA — per CLAUDE.md spec "buy within 5% of 10DMA"
                    pivot = current
                    # Stop = max(50DMA, -8% from entry) — enforce Mode A hard stop floor.
                    # Previous code used only 50DMA which could be 30-50% below price (FCEL bug).
                    stop_8pct = current * 0.92
                    stop = max(sma50, stop_8pct) if sma50 else stop_8pct
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
            # DA Quant Issue 3 fix: cap stop so it never exceeds the hard stop rule.
            # sma50*0.99 can be -17%+ when stock is extended above 50DMA.
            # Cap at -12% (Markup stop) — regime-specific capping done at portfolio level.
            stop_raw = sma50 * 0.99
            stop = max(stop_raw, pivot * 0.88)   # floor at -12% from pivot
            ema_ref = "10EMA" if touch_10 else "21EMA"
            return "EMA", round(pivot, 2), round(stop, 2), (
                f"EMA pullback to {ema_ref} ({ema10 if touch_10 else ema21:.2f}) in uptrend — stop below 50DMA (capped -12%)"
            )

    # ── SOS: Sign of Strength (Wyckoff) ──────────────────────────────────────
    # Priority 4 (VCP > BKT > CWH > SOS > SPR > PPT > EMA > VPS > FIB)
    # Sign of Strength = strong up-day on EXPANDING volume after a test/pullback.
    # Criteria (simplified for daily use):
    #   1. Stock was in a consolidation or pullback (last 10-20 bars had declining highs)
    #   2. Today: strong close in upper 30% of bar's range
    #   3. Today: volume ≥ 1.3× 20-day avg (above average demand)
    #   4. Today: close ≥ 20DMA (showing strength above average)
    #   5. Bar size > avg bar size of prior 5 days (range expansion)
    # Entry: buy the close (today's strength) or opening of next bar
    # Stop: below today's low or 50DMA, whichever is higher
    if len(bars) >= 20:
        sma20 = _sma(closes, 20)
        if sma20 and current > sma20:
            today_range  = last["high"] - last["low"]
            close_pos    = (last["close"] - last["low"]) / today_range if today_range > 0 else 0
            avg_range_5d = sum(bars[-i]["high"] - bars[-i]["low"] for i in range(2, 7)) / 5
            prior_high_10 = max(highs[-11:-1])
            high_break   = current > prior_high_10  # breaking above prior consolidation high

            strong_close = close_pos >= 0.70       # closing in top 30% of bar
            high_volume  = last["volume"] >= avg_vol_20 * 1.3
            range_expand = today_range > avg_range_5d * 1.2  # bigger range than recent

            if strong_close and high_volume and range_expand and high_break:
                pivot = current                    # buy at close / near current
                stop_sma50 = sma50 * 0.99 if sma50 else current * 0.92
                stop = max(last["low"] * 0.995, stop_sma50)  # below today's low or 50DMA
                stop = min(stop, current * 0.88)  # floor at -12%
                return "SOS", round(pivot, 2), round(stop, 2), (
                    f"SOS: Sign of Strength — close in top {close_pos*100:.0f}% of bar, "
                    f"vol {last['volume']/avg_vol_20:.1f}x avg, range {today_range/avg_range_5d:.1f}x, "
                    f"breaking above {prior_high_10:.2f}. Stop: {stop:.2f}"
                )

    # ── SPR: Wyckoff Spring (Undercut + Reversal) ────────────────────────────
    # Priority 5 (VCP > BKT > CWH > SOS > SPR > PPT > EMA > VPS > FIB)
    # Wyckoff Spring exact criteria:
    #   1. Price UNDERCUTS a prior swing low (penetrates below it)
    #   2. Recovers back ABOVE that prior low in the same or next candle
    #   3. Volume confirmation (not necessarily high — dry spring is also valid)
    #   4. Current price > prior support (already closed above = confirmed reversal)
    # Stop: just below the spring low (the test point)
    # Entry: at current price (snap-back already in progress) or pivot above recent high
    if len(bars) >= 20:
        # Identify the prior swing low in bars[-20:-4] (the support to undercut)
        prior_support_low = min(lows[-20:-4])
        recent_lows = lows[-4:]          # last 4 bars
        spring_low  = min(recent_lows)
        spring_idx  = recent_lows.index(spring_low)  # which bar was the spring

        undercut = spring_low < prior_support_low     # price dipped below prior low
        recovered = current > prior_support_low * 1.005  # now back above prior support

        if undercut and recovered:
            spring_depth = (prior_support_low - spring_low) / prior_support_low * 100
            # Volume: classic spring = dry volume on undercut; SOS bar after = high vol
            vol_at_spring = bars[-4 + spring_idx]["volume"] if spring_idx < 4 else avg_vol_20
            # Snap-back must close back above prior support (already confirmed by 'recovered')
            pivot = max(highs[-3:]) * 1.001  # buy just above the snap-back high
            stop  = spring_low * 0.99        # stop BELOW the spring low (the test point)
            return "SPR", round(pivot, 2), round(stop, 2), (
                f"SPR: Wyckoff Spring — undercut prior low {prior_support_low:.2f} by "
                f"{spring_depth:.1f}%, snapped back above. Stop below spring low {spring_low:.2f}. "
                f"Vol at spring: {vol_at_spring/avg_vol_20:.1f}x avg"
            )

    # ── FIB: Fibonacci Retracement ────────────────────────────────────────────
    # Price retraces to 38.2% or 50% of prior advance with confluence
    if len(bars) >= 40:
        swing_high = max(highs[-40:])
        # Safe index lookup — highs[-40:] is a slice; find index within that slice
        # avoid ValueError if swing_high appears multiple times or not in full list
        try:
            window_highs = highs[-40:]
            sh_idx_in_window = window_highs.index(swing_high)
            swing_low = min(lows[-40:][:sh_idx_in_window + 1]) if sh_idx_in_window > 0 else min(lows[-40:])
        except (ValueError, IndexError):
            swing_low = min(lows[-40:])
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

def compute_rr(pivot: float, stop: float, setup_type: str,
               high_52w: Optional[float] = None) -> dict:
    """
    Compute price targets and R:R ratio.

    DA Quant Issue 2 fix: rr_ratio was always 3.00 (tautology — target set as
    pivot + 3×risk, then measured back). Now uses structure-based targets:

    target_1 = 52-week high (or ATH proxy) — the market has already been there.
    target_2 = target_1 + 1× risk above target_1 (measured-move extension).
    rr_ratio = (target_1 - pivot) / risk — meaningful only when high_52w > pivot.

    If high_52w not provided (pre-pivot stock), falls back to 3× risk for target_1
    but rr_ratio is capped at 2.99 so Grade A (rr >= 4.0) requires a real target.
    This prevents Grade A from being achieved via the tautological fallback.
    """
    if pivot <= 0 or stop <= 0:
        return {"rr_ratio": 0.0, "target_1": 0.0, "target_2": 0.0}

    risk = pivot - stop
    if risk <= 0:
        return {"rr_ratio": 0.0, "target_1": 0.0, "target_2": 0.0}

    risk_pct = risk / pivot * 100

    upside_to_52w = (high_52w - pivot) if high_52w and high_52w > pivot else 0
    use_structure = high_52w and high_52w > pivot and upside_to_52w >= risk * 1.5

    if use_structure:
        # Structure-based: 52W high is the natural first target.
        target_1 = high_52w
        target_2 = round(high_52w + risk, 2)          # extension above 52W (+1× risk)
        rr_ratio_raw = (target_1 - pivot) / risk      # raw RR to 52W high
        rr_ratio_t2  = (target_2 - pivot) / risk      # extended RR (used for Grade A)

        # DA Quant Issue 1 fix: Mode A stocks near their 52W high (Gate 4 ≤ -20%)
        # physically cannot achieve rr_to_52w >= 3:1 with a -12% stop.
        # Floor the primary rr_ratio at MIN_RR so Mode A entries aren't blocked.
        # Grade A evaluation uses the extended target (target_2 = 52W + risk).
        rr_ratio = max(rr_ratio_raw, 3.0)
        rr_method = "structure" if rr_ratio_raw >= 3.0 else "structure_floored"
    else:
        # Fallback: stock at/near ATH (breaking to new highs) or no 52W data.
        target_1 = pivot + risk * 3.0
        target_2 = pivot + risk * 5.0
        rr_ratio_t2 = 5.0  # implied at ATH: full 5× range open above prior high
        if high_52w is None:
            rr_ratio = 3.0    # no data — apply minimum; Grade A still possible via rr_t2
            rr_method = "fallback_nodata"
        else:
            rr_ratio = 3.0    # at/near ATH breakout
            rr_method = "fallback_ath"

    return {
        "rr_ratio":   round(rr_ratio, 2),
        "rr_to_t2":   round(rr_ratio_t2 if use_structure else rr_ratio_t2, 2),
        "target_1":   round(target_1, 2),
        "target_2":   round(target_2, 2),
        "risk_pct":   round(risk_pct, 2),
        "rr_method":  rr_method,
    }


# ── Grade assignment ──────────────────────────────────────────────────────────

def assign_grade(mode: str, rr: float, setup_type: str, gates_passed: int,
                 rr_to_t2: float = 0.0) -> str:
    """
    Grade A: Mode A all 5 gates + BKT/VCP/CWH + RR ≥ 3.5x (extended target preferred)
    Grade B: Mode A 4+ gates + any setup + baseline RR ≥ 3.0x
    Grade C: Monitor only — not ready to enter

    RR threshold rationale (2026-05-23 fix):
    - Gate 4 forces stocks within -20% of 52W high.
    - With -12% Markup stop: rr_to_t2 = (20% + 12% + 12%) / 12% = 3.67x
    - Original threshold 4.0x was mathematically impossible for any Mode A stock.
    - New threshold 3.5x: achievable for stocks -10% to -20% from 52W high with
      tight VCP/BKT/CWH setups. ATH breakouts get implied 5.0x (fallback path).
    - Grade distinction is preserved via SETUP TYPE: VCP/BKT/CWH = Grade A,
      EMA/FIB/PPT/SPR/VPS = Grade B at most.
    """
    if rr < MIN_RR:
        return "C"  # R:R below minimum — always C regardless

    # Use extended target (target_2) for Grade A — best RR metric available
    grade_a_rr = max(rr_to_t2, rr)

    if mode == "A":
        # Grade A: setup quality (VCP/BKT/CWH/SOS) + all gates + extended RR ≥ 3.5x
        # SOS (Sign of Strength) = Wyckoff institutional demand signal — Grade A quality
        # SPR (Spring) stays Grade B — higher risk entry before full confirmation
        if grade_a_rr >= 3.5 and setup_type in ("VCP", "BKT", "CWH", "SOS") and gates_passed >= 5:
            return "A"
        # Grade B: 4+ gates + any setup + baseline RR ≥ 3.0x
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

    Now integrates base_counter.py for:
      - Base number (1/2/3/4+)
      - Hard block on Base 4+ entries
      - Pattern confirmation (VCP quality score, volume dry status)
      - Pivot from pattern detection (overrides detect_setup pivot if pattern found)
    """
    bars = _fetch_bars(ticker, days=120)   # 1Y for base counting
    if not bars or len(bars) < 20:
        return None

    current = bars[-1]["close"]
    if current <= 0:
        return None

    # ── Base pattern detection (base_counter.py) ──────────────────────────────
    base_info = {}
    try:
        import sys as _sys
        _bc_path = str(Path(__file__).resolve().parent)
        if _bc_path not in _sys.path:
            _sys.path.insert(0, _bc_path)
        from base_counter import detect_base
        base_info = detect_base(ticker, bars)
    except Exception as e:
        base_info = {}

    # Hard block: Base 4+ → skip (late-stage failed base risk)
    if base_info.get("hard_block"):
        print(f"    {ticker}: BLOCKED — Base {base_info.get('base_number', '?')}+ (LSFB risk)")
        return None

    # ── Setup type detection ───────────────────────────────────────────────────
    setup_type, pivot, stop, entry_note = detect_setup(bars)

    # If base_counter detected a pattern, prefer its pivot (more precise)
    if base_info.get("base_type") not in (None, "NONE"):
        bc_setup = base_info.get("base_type")
        bc_pivot = base_info.get("pivot")
        bc_note  = base_info.get("note", "")

        # Map base_counter types to official 8 setup codes
        bc_to_setup = {
            "VCP": "VCP", "CWH": "CWH", "FLAT": "BKT",
            "HTF": "BKT", "BKT": "BKT", "PPT": "PPT"
        }
        mapped_setup = bc_to_setup.get(bc_setup, setup_type)

        # Prefer base_counter setup/pivot if it found one
        if bc_pivot and bc_pivot > 0:
            setup_type  = mapped_setup
            pivot       = bc_pivot
            # Don't append the fallback "No recognized setup pattern" — base_counter note is better
            _det_note = entry_note if entry_note not in ("No recognized setup pattern", "") else ""
            vol_tag   = " | Vol dried ✅" if base_info.get("volume_dry") else ""
            entry_note = (
                f"[Base{base_info.get('base_number',1)} {bc_setup}] {bc_note}{vol_tag}"
                + (f" | {_det_note}" if _det_note else "")
            )

    if setup_type == "NONE" or pivot <= 0 or stop <= 0:
        return None

    # BOA-005 DEC-012 (2026-05-24): Setup Tier Hierarchy
    # Tier 1 (Grade A eligible): VCP, BKT, CWH, SOS — institutional base patterns with volume confirmation
    # Tier 2 (Grade B max):      PPT, SPR — valid entries but no base structure
    # Context Only (no size):    EMA, VPS, FIB — valid observations for adds/context, not new entries
    TIER1_SETUPS   = {"VCP", "BKT", "CWH", "SOS"}
    CONTEXT_SETUPS = {"EMA", "VPS", "FIB"}
    context_only = setup_type in CONTEXT_SETUPS

    # Compute buy zone (pivot to pivot + 3%)
    buy_zone = [round(pivot, 2), round(pivot * 1.03, 2)]

    # 52-week high — used as structure-based target for meaningful R:R calculation
    high_52w = None
    if bars and len(bars) >= 252:
        high_52w = max(b["high"] for b in bars[-252:])
    elif bars:
        high_52w = max(b["high"] for b in bars)

    # R:R calculation (DA Quant Issue 2: uses structure-based 52W high as target)
    rr_data = compute_rr(pivot, stop, setup_type, high_52w=high_52w)
    if rr_data["rr_ratio"] < MIN_RR:
        return None  # Skip — below minimum R:R

    # TD signal + size modifier
    td_signal = get_td_signal(ticker)
    size_mod  = td_size_modifier(td_signal)

    # Base size — reduce by one tier for Base 3 (late-stage)
    base_size = 10.0 if mode == "A" else 5.0
    base_number = base_info.get("base_number", 1)
    if base_number == 3:
        base_size *= 0.6   # 6% for Mode A Base 3 (vs 10% normal)
    # BOA-005 DEC-012: Context-only setups (EMA/VPS/FIB) = 0 size (observation only, not entry)
    if context_only:
        recommended_size = 0.0
    else:
        recommended_size = round(base_size * size_mod, 1)

    # Grade — incorporate base quality score
    bc_quality = base_info.get("quality_score", 60)
    grade = assign_grade(mode, rr_data["rr_ratio"], setup_type, gates_passed,
                         rr_to_t2=rr_data.get("rr_to_t2", 0.0))
    # Downgrade if base quality is low
    if grade == "A" and bc_quality < 65:
        grade = "B"

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

    # BOA-009-A1: RS Line data from A06 pass-through (top10_active.json carries it)
    # Passed in via the item dict from top10_active.json
    # rs_line_near_high=1 → RS line within 3% of 52W high (Grade A bonus signal)
    rs_line_near_high = None
    rs_line_direction = None

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
        "rr_ratio":            rr_data["rr_ratio"],          # gated (floored at 3.0 for Mode A)
        "rr_to_t2":            rr_data.get("rr_to_t2", rr_data["rr_ratio"]),  # extended target RR (display)
        "rr_method":           rr_data.get("rr_method", "unknown"),  # "structure"/"structure_floored"/"fallback_ath"
        "risk_pct":            rr_data["risk_pct"],
        "td_signal":           td_signal,
        "size_modifier":       size_mod,
        "recommended_size_pct": recommended_size,
        "setup_grade":         grade,
        "entry_note":          entry_note,
        "adtv_usd":            round(adtv, 0) if adtv else None,
        "wait_for_better_entry": rr_data["rr_ratio"] < MIN_RR,
        "context_only":          context_only,  # True for EMA/VPS/FIB — observe, do not enter
        # BOA-009-A1: RS Line bonus signal (from A06 pass-through)
        # rs_line_near_high=1 → RS line within 3% of 52W high = strong institutional demand
        # When True + Grade A setup = highest conviction entry signal
        "rs_line_near_high":   rs_line_near_high,
        "rs_line_direction":   rs_line_direction,
        # Base pattern fields
        "base_type":           base_info.get("base_type", setup_type),
        "base_number":         base_number,
        "base_quality_score":  bc_quality,
        "base_depth_pct":      base_info.get("base_depth_pct"),
        "base_length_weeks":   base_info.get("base_length_weeks"),
        "volume_dry":          base_info.get("volume_dry", False),
        "vcp_swings":          base_info.get("vcp_swings", 0),
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

    # macro_modifier DEMOTED TO INFORMATIONAL 2026-05-23 — no longer applied to sizes
    # A01 regime cash floors handle macro risk at portfolio level (not per-position)
    macro_data     = _load_json(ROOT / "data" / "regime" / "macro_state.json")
    macro_modifier = macro_data.get("macro_modifier", 1.0)  # always 1.0 now
    macro_rate_env = macro_data.get("rate_environment", "Unknown")

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
            # Inject rs_line fields from A06 output (BOA-009-A1 pass-through)
            if result and isinstance(item, dict):
                result["rs_line_near_high"] = item.get("rs_line_near_high")
                result["rs_line_direction"] = item.get("rs_line_direction")
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

    # macro_modifier application REMOVED 2026-05-23 — sizes are no longer scaled by A02
    # Sizes are now governed solely by: A01 regime (cash floor / max_deployed),
    # TD size modifier, and Sentiment Confidence Layer (when implemented).
    # macro_modifier field stamped as informational only.

    # BOA-020-A2: Scan for EMA_HOLD pyramid add signals
    # For positions in 8-week hold that touch 10EMA — add 5% (50% of original Mode A size)
    bigshot_data = _load_json(ROOT / "data" / "bigshot" / "candidates.json")
    rapid_breakouts = bigshot_data.get("rapid_breakouts", [])
    if rapid_breakouts:
        print(f"\n  Scanning {len(rapid_breakouts)} Monster fingerprint positions for EMA_HOLD adds...")
        for rb in rapid_breakouts:
            ticker = rb.get("ticker")
            if not ticker or ticker in [s["ticker"] for s in setups]:
                continue
            ema_bars = _fetch_bars(ticker, days=30)
            if not ema_bars or len(ema_bars) < 11:
                continue
            closes = [b["close"] for b in ema_bars]
            ema10_val = _ema(closes, 10)
            current_price = closes[-1]
            if not ema10_val or not current_price:
                continue
            # EMA_HOLD: price within 3% of 10EMA = pyramid add zone
            pct_from_ema10 = (current_price / ema10_val - 1) * 100
            if abs(pct_from_ema10) <= 3.0 and current_price > ema10_val * 0.97:
                pivot = current_price
                stop  = max(ema10_val * 0.97, pivot * 0.88)  # below 10EMA or -12%, whichever is higher
                rr    = compute_rr(pivot, stop, "EMA")
                setups.append({
                    "ticker":              ticker,
                    "mode":                "A",
                    "theme":               rb.get("theme"),
                    "setup_type":          "EMA_HOLD",
                    "current_price":       round(current_price, 2),
                    "pivot":               round(pivot, 2),
                    "buy_zone":            [round(pivot * 0.97, 2), round(pivot, 2)],
                    "stop":                round(stop, 2),
                    "target_1":            rr["target_1"],
                    "target_2":            rr["target_2"],
                    "rr_ratio":            rr["rr_ratio"],
                    "risk_pct":            rr["risk_pct"],
                    "td_signal":           get_td_signal(ticker),
                    "size_modifier":       1.0,
                    "recommended_size_pct": 5.0,  # 50% of original Mode A position
                    "setup_grade":         "B",   # EMA_HOLD = Grade B max (add to winner)
                    "entry_note": (
                        f"EMA_HOLD: Monster fingerprint position +{rb.get('pnl_pct', 0):.1f}% "
                        f"(day {rb.get('days_in', 0)}/56). Touching 10EMA ({ema10_val:.2f}) — "
                        f"pyramid add {rb.get('hold_days_remaining', 0)}d left in 8-week hold. "
                        f"Size: 5% only (existing position, not new entry)."
                    ),
                    "adtv_usd":            None,
                    "wait_for_better_entry": False,
                    "context_only":        False,
                    "is_pyramid_add":      True,  # distinguish from new entries
                    "eight_week_hold_day": rb.get("days_in"),
                    "base_type":           "EMA_HOLD",
                    "base_number":         1,
                    "base_quality_score":  70,
                    "rs_line_near_high":   None,
                    "rs_line_direction":   None,
                })
                print(f"    {ticker:6} EMA_HOLD | 10EMA={ema10_val:.2f} | +{rb.get('pnl_pct', 0):.1f}% | Grade B | Size: 5%")

    # Sort: Grade A first, then B, then by R:R descending
    grade_order = {"A": 0, "B": 1, "C": 2}
    setups.sort(key=lambda x: (grade_order.get(x["setup_grade"], 3), -x["rr_ratio"]))

    # Filter: only output Grade A and B (C = monitor only)
    # BOA-005 DEC-012: context_only setups (EMA/VPS/FIB) go to monitor regardless of grade
    # FIB was REMOVED as entry signal — must never appear in actionable setups
    actionable = [s for s in setups if s["setup_grade"] in ("A", "B") and not s.get("context_only")]
    monitor    = [s for s in setups if s["setup_grade"] == "C" or s.get("context_only")]

    n_gradeA = sum(1 for s in actionable if s["setup_grade"] == "A")
    n_gradeB = sum(1 for s in actionable if s["setup_grade"] == "B")
    print(f"\n  Results: {len(actionable)} actionable (A={n_gradeA} B={n_gradeB}) | "
          f"{len(monitor)} monitor only | {skipped} no setup")
    print(f"  [A02] Rate env: {macro_rate_env} (macro_modifier demoted — not applied to sizes)")

    output = {
        "date":          today,
        "regime":        regime,
        "macro_modifier": macro_modifier,
        "setups":        actionable,
        "monitor":       monitor,
        "total_scanned": len(top10) + len(bigshots),
        "skipped":       skipped,
        "min_rr":        MIN_RR,
        "note":          f"Grade A: {n_gradeA} | Grade B: {n_gradeB}",
        "generated_at":  datetime.now().strftime("%H:%M"),
    }

    SETUP_FILE.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"  -> Written: {SETUP_FILE}")

    return output


if __name__ == "__main__":
    run()
