"""
AlphaAbsolute v2 -- Base Counter & Pattern Detector
=====================================================
Identifies chart patterns for the Setup Scanner (A08).

Detects 5 base types:
  VCP  — Volatility Contraction Pattern (3+ swings, each <75% of prior, volume dry)
  CWH  — Cup with Handle (7+ week cup, handle ≤12% depth)
  FLAT — Flat base (consolidation <15% depth over 5+ weeks)
  IPO  — IPO base (first base after IPO within 15 weeks)
  HTF  — High Tight Flag (prior advance ≥100% in ≤8 weeks, then ≤25% pullback)

Also detects BKT (Breakout) in progress and PPT (Pocket Pivot).

Base counting:
  Base 0 = IPO base or first consolidation after going public
  Base 1 = First proper base after a stage 2 advance
  Base 2 = Second base (still high conviction)
  Base 3 = Third base (late stage — reduced size)
  Base 4+ = Hard block (LSFB risk)

Output per ticker:
{
    "ticker": "NVDA",
    "base_type": "VCP",
    "base_number": 1,
    "base_depth_pct": 18.3,
    "base_length_weeks": 7,
    "pivot": 125.50,
    "is_breaking_out": false,
    "volume_dry": true,
    "vcp_swings": 3,
    "quality_score": 82,
    "grade": "A",
    "note": "3-swing VCP, volume at 35% avg, 18.3% depth — clean",
    "hard_block": false
}

Usage:
  from base_counter import detect_base
  result = detect_base("NVDA", bars)   # bars = list of OHLCV dicts oldest-first
"""

from __future__ import annotations
import statistics
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────────

VCP_SWING_RATIO     = 0.75   # each swing must be <75% of the prior
VCP_MIN_SWINGS      = 3
CWH_MIN_WEEKS       = 7
CWH_HANDLE_MAX_PCT  = 12.0   # handle depth max 12%
FLAT_MAX_DEPTH_PCT  = 15.0   # consolidation <15% = flat base
FLAT_MIN_WEEKS      = 5
HTF_MIN_ADVANCE_PCT = 100.0  # prior run must be ≥100%
HTF_MAX_WEEKS       = 8      # within 8 weeks
HTF_MAX_PULLBACK    = 25.0   # flag pullback ≤25%
BKT_VOLUME_RATIO    = 1.5    # breakout needs 1.5× avg volume
PPT_LOOKBACK        = 10     # pocket pivot looks back 10 days

BASE4_HARD_BLOCK    = True   # base 4+ = hard block on new entries


# ── Swing detection ────────────────────────────────────────────────────────────

def _find_highs_lows(bars: list[dict], window: int = 5) -> tuple[list[int], list[int]]:
    """
    Find local high and low pivot indices in price series.
    Returns (high_indices, low_indices) — indices into bars list.
    """
    closes = [b["close"] for b in bars]
    highs, lows = [], []

    for i in range(window, len(closes) - window):
        local_slice = closes[i - window: i + window + 1]
        if closes[i] == max(local_slice):
            highs.append(i)
        if closes[i] == min(local_slice):
            lows.append(i)

    return highs, lows


def _avg_volume(bars: list[dict], n: int = 20) -> float:
    """20-day average volume."""
    vols = [b.get("volume", 0) for b in bars[-n:] if b.get("volume", 0) > 0]
    return statistics.mean(vols) if vols else 0


def _volume_dry(bars: list[dict], lookback: int = 5, threshold: float = 0.5) -> bool:
    """Returns True if recent volume is below threshold × 20-day avg."""
    avg = _avg_volume(bars, 20)
    if avg == 0:
        return False
    recent = [b.get("volume", 0) for b in bars[-lookback:]]
    return statistics.mean(recent) < avg * threshold


# ── Pattern detectors ─────────────────────────────────────────────────────────

def detect_vcp(bars: list[dict]) -> Optional[dict]:
    """
    Detect Volatility Contraction Pattern.
    Requires ≥3 swings where each high-to-low contraction is <75% of the prior.
    Volume must be drying on the right side of the base.
    """
    if len(bars) < 30:
        return None

    closes = [b["close"] for b in bars]
    highs_idx, lows_idx = _find_highs_lows(bars, window=3)

    if len(highs_idx) < 2 or len(lows_idx) < 2:
        return None

    # Compute swing ranges: range = high - low between consecutive pivots
    all_pivots = sorted(
        [(i, "H", closes[i]) for i in highs_idx] +
        [(i, "L", closes[i]) for i in lows_idx],
        key=lambda x: x[0]
    )

    # Only look at last 15 weeks of bars
    bar_count = min(len(bars), 75)
    recent_bars = bars[-bar_count:]
    recent_pivots = [p for p in all_pivots if p[0] >= len(bars) - bar_count]

    if len(recent_pivots) < VCP_MIN_SWINGS * 2:
        return None

    # Build swings: (high - low) for alternating H/L pairs
    swings = []
    last_type = None
    for i in range(len(recent_pivots)):
        ptype = recent_pivots[i][1]
        if last_type is None:
            last_type = ptype
            continue
        if ptype != last_type:
            # consecutive different types = one half-swing
            if i > 0:
                p1 = recent_pivots[i-1][2]
                p2 = recent_pivots[i][2]
                swing_pct = abs(p2 - p1) / max(p1, p2) * 100
                swings.append(swing_pct)
            last_type = ptype

    if len(swings) < VCP_MIN_SWINGS:
        return None

    # Check VCP condition: each swing < 75% of prior
    swings = swings[-6:]  # last 6 swings (3 full compressions)
    valid_contractions = 0
    for i in range(1, len(swings)):
        if swings[i] < swings[i-1] * VCP_SWING_RATIO:
            valid_contractions += 1

    if valid_contractions < VCP_MIN_SWINGS - 1:
        return None

    # Pivot = recent high (right side of base)
    pivot = max(closes[-15:]) if len(closes) >= 15 else closes[-1]
    base_high = max(closes[-bar_count:])
    base_low  = min(closes[-bar_count:])
    depth_pct = (base_high - base_low) / base_high * 100

    vol_dry = _volume_dry(recent_bars)
    length_weeks = bar_count // 5

    quality = _score_vcp(depth_pct, valid_contractions, vol_dry, length_weeks)

    return {
        "base_type":        "VCP",
        "base_depth_pct":   round(depth_pct, 1),
        "base_length_weeks": length_weeks,
        "pivot":            round(pivot, 2),
        "vcp_swings":       valid_contractions + 1,
        "volume_dry":       vol_dry,
        "quality_score":    quality,
        "note":             (
            f"{valid_contractions+1}-swing VCP, "
            f"vol {'dry' if vol_dry else 'not dry'}, "
            f"{depth_pct:.1f}% depth"
        ),
    }


def _score_vcp(depth_pct: float, contractions: int, vol_dry: bool, weeks: int) -> int:
    """
    DA Quant Issue 7 fix: replace hard step-function depth scoring with continuous
    linear interpolation. Eliminates the cliff at 45% where a 0.2% difference
    in measured depth was changing grade (A→B) due to measurement sensitivity.
    """
    score = 50

    # Depth: continuous scoring via linear interpolation
    # Ideal range: 15-30%. Penalty increases linearly outside this range.
    # Max +15 at 22% (midpoint of ideal), tapering to 0 at 0% and 70%+
    if depth_pct <= 30:
        # 0% → +10, 15% → +13, 22% → +15, 30% → +12 (linear segments)
        if depth_pct <= 15:
            depth_bonus = 10 + (depth_pct / 15) * 3    # 0→10 to 15→13
        else:
            # 15-30%: peak at 22%, gentle decline to 30%
            dev = abs(depth_pct - 22) / 8               # 0 at 22%, 1 at 14 or 30
            depth_bonus = 15 - dev * 3                  # 15 at peak, ~12 at edges
    else:
        # >30%: linear penalty, -5 per 10% overage, floor at -15
        over = (depth_pct - 30) / 10
        depth_bonus = max(12 - over * 8, -15)

    score += round(depth_bonus)
    # Contractions
    score += min(contractions * 5, 15)
    # Volume
    if vol_dry:
        score += 10
    # Length: 5-15 weeks ideal
    if 5 <= weeks <= 15:
        score += 10
    return max(0, min(score, 100))


def detect_cwh(bars: list[dict]) -> Optional[dict]:
    """
    Detect Cup with Handle.
    Cup: 7+ week round base where mid-point is deepest.
    Handle: final 1-3 week pullback ≤12% from cup high.
    """
    if len(bars) < 50:
        return None

    # Use last 15 weeks max for cup detection
    cup_bars = bars[-75:]
    closes   = [b["close"] for b in cup_bars]

    cup_high = max(closes)
    cup_low  = min(closes)
    depth_pct = (cup_high - cup_low) / cup_high * 100

    if depth_pct < 12 or depth_pct > 50:
        return None  # too shallow or too deep for a cup

    # Find the bottom of the cup — should be roughly in the middle
    low_idx = closes.index(cup_low)
    total   = len(closes)
    mid_start = int(total * 0.25)
    mid_end   = int(total * 0.75)
    if not (mid_start <= low_idx <= mid_end):
        return None  # not a cup shape

    # Handle: last 1-3 weeks
    handle_bars  = bars[-15:]
    handle_highs = [b["close"] for b in handle_bars]
    handle_high  = max(handle_highs)
    handle_low   = min(handle_highs)
    handle_depth = (handle_high - handle_low) / handle_high * 100

    if handle_depth > CWH_HANDLE_MAX_PCT:
        return None  # handle too deep

    pivot = handle_high
    length_weeks = len(cup_bars) // 5
    vol_dry = _volume_dry(handle_bars, lookback=5, threshold=0.5)

    quality = _score_cwh(depth_pct, handle_depth, vol_dry, length_weeks)

    return {
        "base_type":        "CWH",
        "base_depth_pct":   round(depth_pct, 1),
        "base_length_weeks": length_weeks,
        "pivot":            round(pivot, 2),
        "vcp_swings":       0,
        "volume_dry":       vol_dry,
        "quality_score":    quality,
        "note":             (
            f"Cup {length_weeks}W ({depth_pct:.0f}% deep), "
            f"handle {handle_depth:.1f}% deep, "
            f"vol {'dry' if vol_dry else 'active'}"
        ),
    }


def _score_cwh(cup_depth: float, handle_depth: float, vol_dry: bool, weeks: int) -> int:
    score = 50
    if 12 <= cup_depth <= 33:
        score += 15
    elif cup_depth > 33:
        score -= 5
    if handle_depth <= 8:
        score += 15
    elif handle_depth <= 12:
        score += 8
    if vol_dry:
        score += 10
    if weeks >= CWH_MIN_WEEKS:
        score += 10
    return min(score, 100)


def detect_flat(bars: list[dict]) -> Optional[dict]:
    """
    Detect Flat Base.
    Consolidation <15% depth over 5+ weeks.
    """
    if len(bars) < 25:
        return None

    flat_bars = bars[-40:]  # up to 8 weeks
    closes    = [b["close"] for b in flat_bars]
    high      = max(closes)
    low       = min(closes)
    depth_pct = (high - low) / high * 100

    if depth_pct >= FLAT_MAX_DEPTH_PCT:
        return None

    length_weeks = len(flat_bars) // 5
    if length_weeks < FLAT_MIN_WEEKS:
        return None

    pivot   = high
    vol_dry = _volume_dry(flat_bars)
    quality = _score_flat(depth_pct, vol_dry, length_weeks)

    return {
        "base_type":        "FLAT",
        "base_depth_pct":   round(depth_pct, 1),
        "base_length_weeks": length_weeks,
        "pivot":            round(pivot, 2),
        "vcp_swings":       0,
        "volume_dry":       vol_dry,
        "quality_score":    quality,
        "note":             (
            f"Flat base {length_weeks}W, {depth_pct:.1f}% depth, "
            f"vol {'dry' if vol_dry else 'active'}"
        ),
    }


def _score_flat(depth_pct: float, vol_dry: bool, weeks: int) -> int:
    score = 55
    if depth_pct < 8:
        score += 15  # very tight flat
    elif depth_pct < 12:
        score += 8
    if vol_dry:
        score += 10
    if weeks >= 7:
        score += 10
    return min(score, 100)


def detect_htf(bars: list[dict]) -> Optional[dict]:
    """
    Detect High Tight Flag (HTF).
    Prior advance ≥100% in ≤8 weeks, then ≤25% pullback.
    Highest conviction pattern — extreme rarity.
    """
    if len(bars) < 20:
        return None

    # Look for 100%+ advance in last 8 weeks (40 bars)
    flag_start = max(0, len(bars) - 40)
    run_bars   = bars[flag_start:]
    closes     = [b["close"] for b in run_bars]

    # Find peak and preceding low
    peak_idx = closes.index(max(closes))
    if peak_idx < 5:
        return None  # need at least 1 week of advance

    pre_peak  = closes[:peak_idx + 1]
    run_low   = min(pre_peak)
    run_high  = max(pre_peak)
    advance   = (run_high - run_low) / run_low * 100

    if advance < HTF_MIN_ADVANCE_PCT:
        return None

    # Flag: consolidation after the peak
    if peak_idx >= len(closes) - 2:
        return None  # no flag formed yet

    flag_closes = closes[peak_idx:]
    flag_low    = min(flag_closes)
    pullback    = (run_high - flag_low) / run_high * 100

    if pullback > HTF_MAX_PULLBACK:
        return None

    pivot   = run_high
    vol_dry = _volume_dry(run_bars[-10:])
    quality = 88 if pullback < 10 else (78 if pullback < 20 else 68)

    return {
        "base_type":        "HTF",
        "base_depth_pct":   round(pullback, 1),
        "base_length_weeks": len(flag_closes) // 5 + 1,
        "pivot":            round(pivot, 2),
        "vcp_swings":       0,
        "volume_dry":       vol_dry,
        "quality_score":    quality,
        "note":             (
            f"HTF: +{advance:.0f}% advance, {pullback:.1f}% flag pullback"
        ),
    }


def detect_bkt(bars: list[dict]) -> Optional[dict]:
    """
    Detect active Breakout (BKT).
    Price is within 3% of the most recent resistance level on
    volume ≥ 1.5× 20D average.
    """
    if len(bars) < 25:
        return None

    closes = [b["close"] for b in bars]
    current_price = closes[-1]

    # Resistance = highest close in prior 20-55 sessions (4-11 weeks)
    lookback_bars = bars[-55:-1] if len(bars) >= 56 else bars[:-1]
    if not lookback_bars:
        return None

    resistance = max(b["close"] for b in lookback_bars)
    pct_from_resistance = (current_price - resistance) / resistance * 100

    if not (-1.0 <= pct_from_resistance <= 3.0):
        return None  # not near breakout

    # Volume check
    avg_vol  = _avg_volume(bars[:-1], 20)
    last_vol = bars[-1].get("volume", 0)
    vol_ratio = last_vol / avg_vol if avg_vol > 0 else 0

    if vol_ratio < BKT_VOLUME_RATIO:
        return None  # no volume confirmation

    depth_bars = bars[-55:-1]
    base_high  = max(b["close"] for b in depth_bars)
    base_low   = min(b["close"] for b in depth_bars)
    depth_pct  = (base_high - base_low) / base_high * 100
    length_wks = len(depth_bars) // 5

    quality = 75 + (15 if vol_ratio >= 2.5 else 8 if vol_ratio >= 1.5 else 0)

    return {
        "base_type":        "BKT",
        "base_depth_pct":   round(depth_pct, 1),
        "base_length_weeks": length_wks,
        "pivot":            round(resistance, 2),
        "vcp_swings":       0,
        "volume_dry":       False,
        "quality_score":    min(quality, 100),
        "note":             (
            f"Breakout: +{pct_from_resistance:.1f}% above resistance {resistance:.2f}, "
            f"vol={vol_ratio:.1f}× avg"
        ),
        "is_breaking_out":  True,
        "volume_ratio":     round(vol_ratio, 2),
    }


def detect_ppt(bars: list[dict]) -> Optional[dict]:
    """
    Detect Pocket Pivot (PPT).
    Strong up-day where volume exceeds any down-day volume in prior 10 sessions.
    Price must be within 5% of 10DMA.
    """
    if len(bars) < 15:
        return None

    closes = [b["close"] for b in bars]
    today  = bars[-1]

    # Is today an up day?
    if closes[-1] <= closes[-2]:
        return None

    today_vol = today.get("volume", 0)

    # Find max down-day volume in prior 10 sessions
    lookback = bars[-PPT_LOOKBACK - 1:-1]
    max_down_vol = 0
    for i in range(1, len(lookback)):
        if lookback[i]["close"] < lookback[i-1]["close"]:
            max_down_vol = max(max_down_vol, lookback[i].get("volume", 0))

    if today_vol <= max_down_vol:
        return None  # not a pocket pivot

    # Price within 5% of 10DMA
    ten_dma = statistics.mean([b["close"] for b in bars[-10:]])
    pct_from_dma = abs(closes[-1] - ten_dma) / ten_dma * 100
    if pct_from_dma > 5.0:
        return None

    avg_vol   = _avg_volume(bars[:-1], 20)
    vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0
    quality   = 65 + (15 if vol_ratio >= 2.0 else 8 if vol_ratio >= 1.5 else 0)

    return {
        "base_type":        "PPT",
        "base_depth_pct":   round(pct_from_dma, 1),
        "base_length_weeks": 2,
        "pivot":            round(closes[-1], 2),
        "vcp_swings":       0,
        "volume_dry":       False,
        "quality_score":    min(quality, 100),
        "note":             (
            f"Pocket Pivot: vol={vol_ratio:.1f}× avg, "
            f"{pct_from_dma:.1f}% from 10DMA ({ten_dma:.2f})"
        ),
        "is_breaking_out":  False,
    }


# ── Base number estimation ─────────────────────────────────────────────────────

def estimate_base_number(bars: list[dict], ipo_date: Optional[str] = None) -> int:
    """
    Estimate the base number by counting PROPER consolidation phases.

    Rules (Minervini / IBD definition):
      - A "base" = a significant advance (≥35% in ≤52 weeks) FOLLOWED BY
        a proper consolidation of ≥4 weeks (20 bars) where price contracts
        ≥12% from the prior run's high
      - Minimum base length: 4 weeks (20 trading days)
      - Advance required before base: ≥35% from local low
      - Lookback: last 2 years (500 bars)
      - Hard block: 4+ bases
      - IPO within 20 weeks → Base 0 or Base 1

    Heuristic: strong Stage 2 stocks near 52W high are likely Base 1-2.
    If algorithm returns ≥4 AND stock is within 15% of 52W high AND
    recent RS is high → cap at 2 (the screener already filtered for Stage 2).
    """
    if not bars:
        return 1

    # IPO base check
    if ipo_date and len(bars) < 100:
        return 0

    # Use last 2 years max
    bars_2y  = bars[-500:] if len(bars) >= 500 else bars
    closes   = [b["close"] for b in bars_2y]
    n        = len(closes)

    if n < 25:
        return 1

    # ── Count bases ────────────────────────────────────────────────────────────
    base_count = 0
    i = 0
    MIN_ADVANCE = 0.35      # 35% run before a base can start
    MIN_PULLBACK = 0.12     # 12% pullback needed to be a real base
    MIN_BASE_BARS = 20      # minimum 4 weeks of consolidation
    SKIP = 5                # step when no advance found

    while i < n - MIN_BASE_BARS:
        window_end  = min(i + 130, n)  # look for advance in next 26 weeks
        local_low   = min(closes[i:i + 5])
        if local_low <= 0:
            i += SKIP
            continue

        # Find a 35%+ advance from local_low in next 26 weeks
        advance_idx = None
        for j in range(i + 10, window_end):
            if closes[j] >= local_low * (1 + MIN_ADVANCE):
                advance_idx = j
                break

        if advance_idx is None:
            i += SKIP
            continue

        # Find the peak after the advance
        peak_end  = min(advance_idx + 60, n)
        run_high  = max(closes[advance_idx:peak_end])
        peak_idx  = closes.index(run_high) if run_high in closes[advance_idx:peak_end] else advance_idx

        # Now look for consolidation: ≥20 bars AND price contracts ≥12% from peak
        consol_found = False
        for j in range(peak_idx + MIN_BASE_BARS, min(peak_idx + 100, n)):
            consol_low = min(closes[peak_idx:j])
            pullback   = (run_high - consol_low) / run_high
            if pullback >= MIN_PULLBACK:
                consol_found = True
                base_count  += 1
                i = j  # resume counting from end of this base
                break

        if not consol_found:
            i = peak_idx + SKIP

    base_count = max(1, base_count)

    # DA Quant Issue 5 fix: Stage 2 heuristic override REMOVED.
    #
    # The prior override capped ALL stocks within 20% of 52W high at Base 1-2,
    # which disabled the BASE4 hard block for the entire Mode A universe
    # (Gate 4 requires pct_from_52w_high >= -20%, so every Mode A candidate
    # triggered the override — effectively no Base 4+ stock was ever blocked).
    #
    # Root cause of original over-counting was fixed by:
    #   (a) Raising advance threshold to 35% (was 20%)
    #   (b) Requiring 20-bar minimum consolidation after each advance
    # These fixes produce correct base counts without the override kludge.
    # Validated: ALAB=Base1, MU=Base1, SNDK=Base2, STX=Base1 post-fix.
    #
    # The BASE4 hard block now functions correctly for genuine late-stage stocks.

    return base_count


# ── Main detection function ────────────────────────────────────────────────────

def detect_base(
    ticker: str,
    bars: list[dict],
    ipo_date: Optional[str] = None,
) -> dict:
    """
    Run all pattern detectors and return the best match.

    bars format: [{"date": "YYYY-MM-DD", "open": x, "high": x, "low": x,
                   "close": x, "volume": x}, ...] — oldest first

    Returns a dict with base_type, base_number, pivot, quality_score, grade,
    hard_block, and is_breaking_out.
    """
    if not bars or len(bars) < 20:
        return {
            "ticker":           ticker,
            "base_type":        "NONE",
            "base_number":      1,
            "base_depth_pct":   None,
            "base_length_weeks": None,
            "pivot":            None,
            "is_breaking_out":  False,
            "volume_dry":       False,
            "vcp_swings":       0,
            "quality_score":    0,
            "grade":            "C",
            "note":             "Insufficient data (<20 bars)",
            "hard_block":       False,
        }

    base_number = estimate_base_number(bars, ipo_date)

    # Run all detectors — priority: BKT > HTF > VCP > CWH > PPT > FLAT
    result = None
    for detector_fn in [detect_bkt, detect_htf, detect_vcp, detect_cwh, detect_ppt, detect_flat]:
        try:
            r = detector_fn(bars)
            if r is not None:
                result = r
                break
        except Exception as e:
            continue

    if result is None:
        # No pattern detected — create a default minimal result
        closes  = [b["close"] for b in bars]
        pivot   = max(closes[-20:]) if len(closes) >= 20 else closes[-1]
        result  = {
            "base_type":        "NONE",
            "base_depth_pct":   None,
            "base_length_weeks": None,
            "pivot":            round(pivot, 2),
            "vcp_swings":       0,
            "volume_dry":       _volume_dry(bars),
            "quality_score":    20,
            "note":             "No recognized pattern — monitor for setup formation",
        }

    # Add shared fields
    is_bkt = result.get("base_type") == "BKT"
    hard_block = base_number >= 4

    # Grade: A = quality≥75 + base≤2, B = quality≥55 or base=3, C = rest
    q = result.get("quality_score", 0)
    if hard_block:
        grade = "BLOCKED"
    elif q >= 75 and base_number <= 2:
        grade = "A"
    elif q >= 55 or base_number == 3:
        grade = "B"
    else:
        grade = "C"

    return {
        "ticker":            ticker,
        "base_type":         result["base_type"],
        "base_number":       base_number,
        "base_depth_pct":    result.get("base_depth_pct"),
        "base_length_weeks": result.get("base_length_weeks"),
        "pivot":             result.get("pivot"),
        "is_breaking_out":   result.get("is_breaking_out", False),
        "volume_dry":        result.get("volume_dry", False),
        "vcp_swings":        result.get("vcp_swings", 0),
        "quality_score":     q,
        "grade":             grade,
        "note":              result.get("note", ""),
        "hard_block":        hard_block,
    }


# ── CLI usage ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "utils"))

    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "ALAB", "MU"]

    try:
        from data_engine import get_ohlcv
    except ImportError:
        print("data_engine not found — run from scripts/ directory")
        sys.exit(1)

    print(f"\n{'='*65}")
    print(f"  Base Counter | {__import__('datetime').date.today()}")
    print(f"{'='*65}")

    for t in tickers:
        df = get_ohlcv(t, period="1y")
        if df is None or df.empty:
            print(f"  {t}: no data")
            continue

        bars = [
            {
                "date":   str(idx.date()),
                "open":   float(row.get("Open", row.get("open", 0))),
                "high":   float(row.get("High", row.get("high", 0))),
                "low":    float(row.get("Low",  row.get("low",  0))),
                "close":  float(row.get("Close", row.get("close", 0))),
                "volume": int(row.get("Volume", row.get("volume", 0))),
            }
            for idx, row in df.iterrows()
        ]

        result = detect_base(t, bars)
        bk     = " [HARD BLOCK]" if result["hard_block"] else ""
        print(
            f"\n  {t}: {result['base_type']} Base{result['base_number']}{bk}"
            f" | Grade {result['grade']} | Quality {result['quality_score']}"
        )
        print(f"    Pivot: {result['pivot']} | Depth: {result['base_depth_pct']}%"
              f" | Length: {result['base_length_weeks']}W")
        print(f"    Vol dry: {result['volume_dry']}"
              f" | Breaking out: {result['is_breaking_out']}")
        print(f"    Note: {result['note']}")
