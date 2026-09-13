"""
thai_entry_screen.py
====================
Backtest study: which buy-point signals predict positive short-term
forward returns for high-RS SET stocks?

Goal: find criteria to mark "✅ buy now" on the TH focus list.

Usage:
    python scripts/research/thai_entry_screen.py
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path

DB     = "data/research/thai_ohlcv.db"
TOP_N  = 15       # focus list size
RS_DAYS = 21      # COMBINED lookback
ADTV_MIN = 20e6   # same as eligible_universe
PRICE_MIN = 1.0
FWD_DAYS  = [1, 2, 3, 5]
MIN_HIST  = 63    # need 63 bars before a signal fires


# ── Load data ──────────────────────────────────────────────────────────────────
def load_data():
    conn = sqlite3.connect(DB)
    df = pd.read_sql(
        "SELECT date, ticker, open, high, low, close, volume FROM thai_ohlcv",
        conn, parse_dates=["date"]
    )
    conn.close()
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    return df


# ── Compute signals for one bar ────────────────────────────────────────────────
def compute_signals(hist: pd.DataFrame) -> dict:
    """hist = recent history for one ticker, sorted by date, ends at signal date."""
    if len(hist) < 30:
        return {}
    c = hist["close"].values
    v = hist["volume"].values
    h = hist["high"].values
    l = hist["low"].values

    close  = c[-1]
    vol    = v[-1]

    ma50   = c[-50:].mean() if len(c) >= 50 else np.nan
    ma20   = c[-20:].mean()
    vol20  = v[-20:].mean()
    hi20   = h[-20:].max()
    hi10   = h[-10:].max()
    lo10   = l[-10:].min()

    ret3   = close / c[-4] - 1 if len(c) >= 4 else np.nan
    ret5   = close / c[-6] - 1 if len(c) >= 6 else np.nan

    # ── Signal definitions ────────────────────────────────────────────────────
    signals = {}

    # A1: Breakout — close clears 20d high on volume
    signals["A1_bkt20_vol"] = (
        close >= hi20 * 0.999 and
        vol >= vol20 * 1.5
    ) if not np.isnan(ma20) else False

    # A2: Breakout — close clears 10d high (shorter)
    signals["A2_bkt10"] = close >= hi10 * 0.999

    # A3: Breakout with volume only (no level req)
    signals["A3_vol_surge"] = vol >= vol20 * 2.0

    # B1: Tight base near high — consolidating at resistance
    range10_pct = (hi10 - lo10) / close if close > 0 else 1
    signals["B1_tight_near_hi"] = (
        range10_pct < 0.08 and
        close >= hi20 * 0.95
    )

    # B2: Very tight (VCP-like) regardless of level
    signals["B2_tight"] = range10_pct < 0.06

    # B3: Near 20d high but not extended
    pct_from_hi20 = close / hi20 - 1
    signals["B3_near_hi20"] = -0.05 <= pct_from_hi20 <= 0.01

    # C1: Starting to move — small positive 3d return, above MA50
    signals["C1_early_move"] = (
        not np.isnan(ret3) and
        0.005 <= ret3 <= 0.06 and
        not np.isnan(ma50) and close > ma50
    )

    # C2: Price > MA50 only (basic uptrend)
    signals["C2_above_ma50"] = not np.isnan(ma50) and close > ma50

    # C3: Pocket pivot proxy — strong up day, volume > any down day in 10d
    if len(hist) >= 11:
        last10 = hist.iloc[-11:-1]
        down_days = last10[last10["close"] < last10["close"].shift(1)]
        max_down_vol = down_days["volume"].max() if len(down_days) > 0 else 0
        today_up = c[-1] > c[-2]
        signals["C3_pocket_pivot"] = today_up and vol > max_down_vol
    else:
        signals["C3_pocket_pivot"] = False

    # ── E: Pullback signals ───────────────────────────────────────────────
    # E1: Pullback to MA20 — price touched/bounced off 20d MA
    signals["E1_pb_ma20"] = (
        not np.isnan(ma20) and
        close >= ma20 * 0.98 and close <= ma20 * 1.03
    )

    # E2: Pullback to MA50 — classic add point in uptrend
    signals["E2_pb_ma50"] = (
        not np.isnan(ma50) and
        close >= ma50 * 0.97 and close <= ma50 * 1.03 and
        close > ma20  # still above shorter MA (not breakdown)
    )

    # E3: Mild pullback from recent high (3-10%), still above MA50
    pct_from_hi20 = close / hi20 - 1
    signals["E3_mild_pb"] = (
        -0.10 <= pct_from_hi20 <= -0.03 and
        not np.isnan(ma50) and close > ma50
    )

    # E4: Shallow pullback (1-5%) — very tight, almost at high
    signals["E4_shallow_pb"] = (
        -0.05 <= pct_from_hi20 <= -0.01 and
        not np.isnan(ma50) and close > ma50
    )

    # ── F: Volume dry-up signals ──────────────────────────────────────────
    # F1: Volume dry-up — today's vol very low (< 50% avg) = accumulation pause
    signals["F1_vol_dry"] = vol < vol20 * 0.50

    # F2: Vol dry after prior surge — surge in last 10d, now drying up
    if len(hist) >= 11:
        prior10_vol = v[-11:-1]
        had_surge   = (prior10_vol > vol20 * 1.5).any()
        signals["F2_dry_after_surge"] = had_surge and vol < vol20 * 0.60
    else:
        signals["F2_dry_after_surge"] = False

    # F3: 3 consecutive low-volume days (< 70% avg) = tight consolidation
    if len(v) >= 3:
        signals["F3_3day_dry"] = all(v[-3:] < vol20 * 0.70)
    else:
        signals["F3_3day_dry"] = False

    # F4: Vol dry + near high (classic VCP pre-breakout)
    signals["F4_dry_near_hi"] = (
        signals["F1_vol_dry"] and
        -0.08 <= pct_from_hi20 <= 0.01
    )

    # F5: Vol dry + above MA50 (healthy consolidation)
    signals["F5_dry_ma50"] = signals["F1_vol_dry"] and (not np.isnan(ma50) and close > ma50)

    # ── G: Inside bar / narrow range ─────────────────────────────────────
    # G1: Inside bar — today's range inside yesterday's range
    if len(hist) >= 2:
        prev_h = h[-2]; prev_l = l[-2]
        signals["G1_inside_bar"] = h[-1] <= prev_h and l[-1] >= prev_l
    else:
        signals["G1_inside_bar"] = False

    # G2: Narrow range day (NR7) — today's range smallest in 7 days
    if len(hist) >= 7:
        ranges7 = h[-7:] - l[-7:]
        signals["G2_nr7"] = (h[-1] - l[-1]) == ranges7.min()
    else:
        signals["G2_nr7"] = False

    # ── H: RS momentum ────────────────────────────────────────────────────
    # (RS is computed at portfolio level; here we use price momentum proxy)
    # H1: 5d return positive + above MA50 (momentum resuming)
    signals["H1_mom_resume"] = (
        not np.isnan(ret5) and ret5 > 0.01 and
        not np.isnan(ma50) and close > ma50
    )

    # H2: Price making new 10d high today (RS line proxy breakout)
    signals["H2_new10d_hi"] = close >= hi10 * 0.999

    # ── Combo signals (new) ────────────────────────────────────────────────
    signals["X1_pb_ma50_dry"]      = signals["E2_pb_ma50"] and signals["F1_vol_dry"]
    signals["X2_mild_pb_dry"]      = signals["E3_mild_pb"] and signals["F2_dry_after_surge"]
    signals["X3_shallow_pb_surge"] = signals["E4_shallow_pb"] and signals["F2_dry_after_surge"]
    signals["X4_inside_near_hi"]   = signals["G1_inside_bar"] and signals["B3_near_hi20"]
    signals["X5_nr7_ma50"]         = signals["G2_nr7"] and (not np.isnan(ma50) and close > ma50)
    signals["X6_dry_near_hi_ma50"] = signals["F4_dry_near_hi"] and (not np.isnan(ma50) and close > ma50)
    signals["X7_pb_dry_ma50"]      = signals["E3_mild_pb"] and signals["F1_vol_dry"] and (not np.isnan(ma50) and close > ma50)
    signals["X8_3dry_near_hi"]     = signals["F3_3day_dry"] and signals["B3_near_hi20"]

    # ── I: Short-term 1-3 day signals (new) ──────────────────────────────────
    # I1: Close Quality — close in top 25% of today's range + above-avg volume
    day_range = h[-1] - l[-1]
    if day_range > 0:
        close_quality = (c[-1] - l[-1]) / day_range
        signals["I1_close_quality"] = close_quality >= 0.75 and vol >= vol20 * 1.0
        signals["I1b_close_quality_hi_vol"] = close_quality >= 0.75 and vol >= vol20 * 1.5
    else:
        signals["I1_close_quality"] = False
        signals["I1b_close_quality_hi_vol"] = False

    # I2: Power Move — up >1.5% today on volume > 1.5x avg (Chan/Hameed/Tong 2000)
    if len(c) >= 2:
        today_ret = c[-1] / c[-2] - 1
        signals["I2_power_move"] = today_ret > 0.015 and vol >= vol20 * 1.5
        signals["I2b_power_move_strong"] = today_ret > 0.025 and vol >= vol20 * 2.0
    else:
        signals["I2_power_move"] = False
        signals["I2b_power_move_strong"] = False

    # I3: Gap-up hold — today opened above yesterday close AND close > open (institutional)
    if len(hist) >= 2:
        o_today = hist["open"].values[-1]
        gap_pct = o_today / c[-2] - 1 if c[-2] > 0 else 0
        signals["I3_gap_up_hold"] = gap_pct > 0.005 and c[-1] > o_today
        signals["I3b_gap_up_strong"] = gap_pct > 0.01 and c[-1] > o_today and vol >= vol20 * 1.3
    else:
        signals["I3_gap_up_hold"] = False
        signals["I3b_gap_up_strong"] = False

    # I4: Mean reversion — 2 consecutive down days, today bounces (oversold snap)
    if len(c) >= 4:
        down2 = c[-3] < c[-4] and c[-2] < c[-3]   # 2 prior days down
        bounce = c[-1] > c[-2]                      # today up
        signals["I4_2down_bounce"] = down2 and bounce
        # I4b: 3 consecutive down days bounce
        down3 = len(c) >= 5 and c[-4] < c[-5] and down2
        signals["I4b_3down_bounce"] = down3 and bounce
    else:
        signals["I4_2down_bounce"] = False
        signals["I4b_3down_bounce"] = False

    # I5: Capitulation reversal — high vol down day then next day gap up or close > prev high
    if len(hist) >= 2:
        prev_ret = c[-2] / c[-3] - 1 if len(c) >= 3 else 0
        prev_vol = v[-2]
        was_cap  = prev_ret < -0.02 and prev_vol >= vol20 * 2.0   # big down vol day
        signals["I5_cap_reversal"] = was_cap and c[-1] > c[-2]
    else:
        signals["I5_cap_reversal"] = False

    # I6: NR7 close quality — narrow range day AND closed in top 50% (coiled spring bias)
    signals["I6_nr7_close_hi"] = (
        signals.get("G2_nr7", False) and
        day_range > 0 and
        (c[-1] - l[-1]) / day_range >= 0.50
    )

    # I7: Dry volume + positive day + near high (quiet accumulation)
    if len(c) >= 2:
        signals["I7_dry_up_near_hi"] = (
            c[-1] > c[-2] and          # up day
            vol < vol20 * 0.70 and     # quiet volume
            -0.06 <= pct_from_hi20 <= 0.01  # near recent high
        )
    else:
        signals["I7_dry_up_near_hi"] = False

    # I8: Consecutive up days (short-term momentum continuation) — 3 up days
    if len(c) >= 4:
        signals["I8_3up_days"] = c[-1] > c[-2] and c[-2] > c[-3] and c[-3] > c[-4]
    else:
        signals["I8_3up_days"] = False

    # I9: Inside bar with close in top 50% (compression + bullish bias)
    signals["I9_inside_close_hi"] = (
        signals.get("G1_inside_bar", False) and
        day_range > 0 and
        (c[-1] - l[-1]) / day_range >= 0.50
    )

    # I10: High RS day — today's 1-day return top vs recent own history (momentum burst)
    if len(c) >= 21:
        daily_rets = np.diff(c[-21:]) / c[-21:-1]
        today_ret_raw = c[-1] / c[-2] - 1 if c[-2] > 0 else 0
        signals["I10_top_decile_day"] = today_ret_raw >= np.percentile(daily_rets, 80) and today_ret_raw > 0
    else:
        signals["I10_top_decile_day"] = False

    # ── J: Support confluence + dry volume (NEW) ──────────────────────────────
    # Core idea: dry vol pullback to SPECIFIC support level = higher probability bounce
    vol_dry   = vol < vol20 * 0.70      # volume drying up (< 70% avg)
    above_m50 = not np.isnan(ma50) and close > ma50

    ma10 = c[-10:].mean() if len(c) >= 10 else np.nan
    ma20_val = ma20  # already computed above

    # J1: Dry vol + near MA10 (tight trend add)
    signals["J1_dry_near_ma10"] = (
        vol_dry and not np.isnan(ma10) and
        close >= ma10 * 0.98 and close <= ma10 * 1.02
    )

    # J2: Dry vol + near MA20 (most common institutional add point)
    signals["J2_dry_near_ma20"] = (
        vol_dry and
        close >= ma20_val * 0.98 and close <= ma20_val * 1.03
    )

    # J3: Dry vol + near MA50 (deeper pullback, higher conviction needed)
    signals["J3_dry_near_ma50"] = (
        vol_dry and not np.isnan(ma50) and
        close >= ma50 * 0.97 and close <= ma50 * 1.03
    )

    # J4: Dry vol + price in "value area" between MA20 and MA50 (institutional zone)
    signals["J4_dry_value_area"] = (
        vol_dry and not np.isnan(ma50) and
        ma50 <= close <= ma20_val * 1.02
    )

    # J5: Prior base high as support — price near the high from 30-60 days ago
    # (prior resistance = new support after breakout above it)
    if len(h) >= 60:
        prior_hi = h[-60:-20].max()   # high from 20-60 bars ago (prior base)
        signals["J5_dry_prior_base_hi"] = (
            vol_dry and
            close >= prior_hi * 0.97 and close <= prior_hi * 1.03
        )
        # J5b: tighter band (within 2%)
        signals["J5b_dry_prior_hi_tight"] = (
            vol_dry and
            close >= prior_hi * 0.98 and close <= prior_hi * 1.02
        )
    else:
        signals["J5_dry_prior_base_hi"] = False
        signals["J5b_dry_prior_hi_tight"] = False

    # J6: Prior swing low as support — price near recent 10-30 day low
    if len(l) >= 30:
        prior_lo = l[-30:-5].min()    # swing low from 5-30 bars ago
        signals["J6_dry_near_swing_lo"] = (
            vol_dry and
            close >= prior_lo * 0.99 and close <= prior_lo * 1.04
        )
    else:
        signals["J6_dry_near_swing_lo"] = False

    # J7: Dry vol + pulled back 3-8% from high + now near MA20 (VCP-like tightest zone)
    signals["J7_vcp_zone_ma20"] = (
        vol_dry and
        -0.08 <= pct_from_hi20 <= -0.03 and
        close >= ma20_val * 0.98 and close <= ma20_val * 1.03
    )

    # J8: Dry vol + pulled back 5-15% from high + near MA50 (deeper correction add)
    signals["J8_pb_zone_ma50"] = (
        vol_dry and not np.isnan(ma50) and
        -0.15 <= pct_from_hi20 <= -0.05 and
        close >= ma50 * 0.97 and close <= ma50 * 1.03
    )

    # J9: 3-consecutive dry days + near MA20 (multi-day squeeze at support)
    signals["J9_3dry_at_ma20"] = (
        signals.get("F3_3day_dry", False) and
        close >= ma20_val * 0.98 and close <= ma20_val * 1.03
    )

    # J10: Dry vol + price between MA10 and MA20 (tightest squeeze zone)
    signals["J10_dry_between_ma10_ma20"] = (
        vol_dry and not np.isnan(ma10) and
        min(ma10, ma20_val) * 0.99 <= close <= max(ma10, ma20_val) * 1.01
    )

    # Combo: dry vol + specific support + in bull trend (MA50 above)
    signals["J_combo_ma20_trend"] = signals["J2_dry_near_ma20"] and above_m50
    signals["J_combo_ma50_trend"] = signals["J3_dry_near_ma50"] and above_m50
    signals["J_combo_vcp_ma20"]   = signals["J7_vcp_zone_ma20"] and above_m50
    signals["J_combo_prior_hi"]   = signals["J5_dry_prior_base_hi"] and above_m50

    # ── K: First pullback after recent multi-month breakout ───────────────────
    # O'Neil/Minervini: first PB after new 63d high = highest continuation prob
    # Criteria: stock hit a new 63-day high within last 30 bars, now pulling back
    # quietly. Current price down 3-8% from that recent high.
    if len(h) >= 63:
        hi63_full   = h[-63:].max()   # full 63-day high ending today
        hi_recent30 = h[-30:].max()   # peak reached in last 30 bars = breakout high
        # Confirm the 30-day peak IS the multi-month breakout
        was_bkt_hi  = hi_recent30 >= hi63_full * 0.995
        pb_from_bkt = close / hi_recent30 - 1   # how far below the breakout high

        # K1: broke to 63d high within 30 days + pulled back 3-8% + quiet vol
        signals["K1_first_pb_after_bkt"] = (
            was_bkt_hi and
            -0.08 <= pb_from_bkt <= -0.03 and
            vol < vol20 * 0.85
        )
        # K2: Same but price still above MA20 (uptrend intact)
        signals["K2_first_pb_ma20_above"] = (
            signals["K1_first_pb_after_bkt"] and close > ma20_val
        )
        # K3: Very shallow first PB (1-5%) — tightest squeeze
        signals["K3_first_pb_shallow"] = (
            was_bkt_hi and
            -0.05 <= pb_from_bkt <= -0.01 and
            vol < vol20 * 0.80 and
            not np.isnan(ma50) and close > ma50
        )
        # K4: PB to MA20 specifically after breakout (classic first test of MA20)
        signals["K4_first_pb_to_ma20"] = (
            was_bkt_hi and
            close >= ma20_val * 0.97 and close <= ma20_val * 1.03 and
            vol < vol20 * 0.85
        )
    else:
        signals["K1_first_pb_after_bkt"]  = False
        signals["K2_first_pb_ma20_above"] = False
        signals["K3_first_pb_shallow"]    = False
        signals["K4_first_pb_to_ma20"]    = False

    # ── M: VDU (Volume Dry-Up) + Expansion Day ───────────────────────────────
    # Missing piece in F-group: we enter DURING the dry vol, not on CONFIRMATION.
    # The expansion day (volume returns AND price up) = institutional re-entry signal.
    # Research: Minervini claims 70%+ in bull markets with full VDU + expansion.
    if len(v) >= 5:
        v_prior3 = v[-4:-1]   # volumes of 3 days before today
        # Strictly declining volume over 3 prior days
        consec_decline = (v_prior3[0] > v_prior3[1] > v_prior3[2]) if len(v_prior3) == 3 else False
        # Deep VDU: at least one of those days was very quiet
        deep_dry = v_prior3.min() < vol20 * 0.60 if len(v_prior3) > 0 else False
        # Expansion day: today's vol > avg of prior 3 AND price up
        prior3_avg = v_prior3.mean() if len(v_prior3) > 0 else vol20
        is_expansion = (vol > prior3_avg) and (c[-1] > c[-2])

        # M1: 3-day consecutive vol decline then expansion day
        signals["M1_vdu_expansion"] = consec_decline and is_expansion
        # M2: Same but deep VDU reached (< 60% avg on lowest day)
        signals["M2_vdu_deep_expansion"] = consec_decline and deep_dry and is_expansion
        # M3: VDU expansion + mild pullback (3-10% from recent high)
        signals["M3_vdu_expansion_pb"] = (
            signals["M1_vdu_expansion"] and -0.10 <= pct_from_hi20 <= -0.02
        )
        # M4: Full compound signal: pullback + deep VDU + expansion + above MA50
        signals["M4_vdu_full_compound"] = (
            signals["M2_vdu_deep_expansion"] and
            -0.10 <= pct_from_hi20 <= -0.02 and
            not np.isnan(ma50) and close > ma50
        )
        # M5: Post-breakout VDU expansion (K-group breakout + M-group confirmation)
        if len(h) >= 63:
            hi_recent30_m = h[-30:].max()
            hi63_m        = h[-63:].max()
            was_bkt_m     = hi_recent30_m >= hi63_m * 0.995
            pb_bkt_m      = close / hi_recent30_m - 1
            signals["M5_post_bkt_vdu_expansion"] = (
                was_bkt_m and -0.08 <= pb_bkt_m <= -0.01 and
                signals["M1_vdu_expansion"]
            )
        else:
            signals["M5_post_bkt_vdu_expansion"] = False
    else:
        signals["M1_vdu_expansion"]         = False
        signals["M2_vdu_deep_expansion"]    = False
        signals["M3_vdu_expansion_pb"]      = False
        signals["M4_vdu_full_compound"]     = False
        signals["M5_post_bkt_vdu_expansion"]= False

    # ── N: Pocket pivot at specific MA level ──────────────────────────────────
    # Pocket pivot (Morales/Kacher): up day where volume > any single down-day
    # vol in prior 10 days. Anchored to MA = highest confirmation version.
    # Research: PP at 50-day MA → 70-75% in 3-day window (Morales/Kacher 2010).
    if len(hist) >= 11:
        last10        = hist.iloc[-11:-1]
        l10_close     = last10["close"].values
        l10_close_lag = np.roll(l10_close, 1); l10_close_lag[0] = l10_close[0]
        down_mask     = l10_close < l10_close_lag
        down_mask[0]  = False
        down_vols     = last10["volume"].values[down_mask]
        max_down_vol  = down_vols.max() if len(down_vols) > 0 else 0
        is_pp         = (c[-1] > c[-2]) and (vol > max_down_vol)

        # N1: Pocket pivot near MA20 (within 5%)
        signals["N1_pp_at_ma20"] = (
            is_pp and
            close >= ma20_val * 0.95 and close <= ma20_val * 1.05
        )
        # N2: Pocket pivot near MA50 (within 5%) — highest-probability version
        signals["N2_pp_at_ma50"] = (
            is_pp and not np.isnan(ma50) and
            close >= ma50 * 0.95 and close <= ma50 * 1.05
        )
        # N3: Pocket pivot after VDU (preceded by 3-day dry = extra confirmation)
        signals["N3_pp_after_vdu"] = is_pp and signals.get("F3_3day_dry", False)
        # N4: Full compound — PP at MA50 after VDU (most selective, highest expected hit)
        signals["N4_pp_at_ma50_vdu"] = signals["N2_pp_at_ma50"] and signals.get("F3_3day_dry", False)
        # N5: PP after first pullback (K-group + N-group combined)
        signals["N5_pp_after_bkt_pb"] = is_pp and signals.get("K1_first_pb_after_bkt", False)
    else:
        signals["N1_pp_at_ma20"]        = False
        signals["N2_pp_at_ma50"]        = False
        signals["N3_pp_after_vdu"]      = False
        signals["N4_pp_at_ma50_vdu"]    = False
        signals["N5_pp_after_bkt_pb"]   = False

    # ── O: SMC — Fair Value Gap (FVG) ────────────────────────────────────────
    # FVG = 3-candle gap. Bullish: low[t] > high[t-2]. Zone = [high[t-2], low[t]].
    # Academic analog: gap-fill literature ~67% partial fill; bounce ~53%.
    # Testing "price inside FVG zone" as support signal.
    if len(hist) >= 4:
        # Bullish FVG formed 2 bars ago: low[-3] > high[-5]? No — standard is:
        # candle1=t-2, candle2=t-1, candle3=t. FVG if low[t] > high[t-2].
        # Then we watch if price returns into zone [high[t-2], low[t]].
        # For today's signal: check if price is currently IN a recently formed FVG
        for lookback in [3, 5, 8]:
            if len(hist) >= lookback + 3:
                fvg_found = False
                for j in range(1, lookback + 1):
                    c1_hi = h[-(j+2)]
                    c3_lo = l[-j]
                    if c3_lo > c1_hi:   # bullish FVG
                        fvg_lo = c1_hi
                        fvg_hi = c3_lo
                        # Today price in zone or just below (mitigation)
                        if fvg_lo <= close <= fvg_hi * 1.01:
                            fvg_found = True
                            break
                signals[f"O1_in_fvg_{lookback}d"] = fvg_found
            else:
                signals[f"O1_in_fvg_{lookback}d"] = False

    # O2: CHoCH proxy — today closed above highest close in last 10 days
    # (trend change confirmation; 55-60% win rate per Brock et al. 1992)
    if len(c) >= 11:
        prior10_max_close = c[-11:-1].max()
        signals["O2_choch_10d"] = c[-1] > prior10_max_close
    else:
        signals["O2_choch_10d"] = False

    # O3: CHoCH + dry vol (accumulation then breakout)
    signals["O3_choch_dry"] = signals.get("O2_choch_10d", False) and vol < vol20 * 0.85

    # ── P: TD Sequential ──────────────────────────────────────────────────────
    # Chen, Huang & Kuan (2016): TD Setup 9 on SET → 58% win rate, 5d alpha +1.1%
    # TD Countdown 13 on SET → ~61% win rate. Best signals for Asian markets.
    #
    # TD Buy Setup: 9 consecutive closes each below close 4 bars prior
    if len(c) >= 14:
        # Count consecutive TD buy setup bars ending today
        td_count = 0
        for k in range(1, 10):   # check up to 9 consecutive
            idx = -k              # today=-1, yesterday=-2, etc.
            lag_idx = idx - 4     # close 4 bars prior
            if abs(idx - 4) > len(c):
                break
            if c[idx] < c[lag_idx]:
                td_count += 1
            else:
                break  # broken — not consecutive

        signals["P1_td_setup_count"] = td_count   # raw count (not boolean)
        signals["P2_td_setup_9"]     = td_count >= 9   # full Setup 9
        signals["P3_td_setup_6plus"] = td_count >= 6   # partial — still meaningful

        # Perfect TD Setup: bars 8 and 9 have lows <= bar 6 low
        # (requires full 9-bar setup present)
        if td_count >= 9 and len(c) >= 14:
            bar6_lo  = l[-4]   # bar 6 counting back from setup-9: -1 is bar9, -4 is bar6
            bar8_lo  = l[-2]
            bar9_lo  = l[-1]
            signals["P4_td_perfect_setup"] = (
                bar8_lo <= bar6_lo and bar9_lo <= bar6_lo
            )
        else:
            signals["P4_td_perfect_setup"] = False

        # TD Countdown: count bars where close <= low 2 bars prior
        # (needs Setup 9 complete on some prior bar — approximate: count last 20 bars)
        if len(c) >= 20:
            countdown_count = sum(
                1 for k in range(3, 20) if c[-k] <= l[-(k+2)]
            )
            signals["P5_td_countdown_13"] = countdown_count >= 13
            signals["P6_td_countdown_8plus"] = countdown_count >= 8  # partial countdown
        else:
            signals["P5_td_countdown_13"]   = False
            signals["P6_td_countdown_8plus"] = False

        # Combo: TD signals + bull trend (MA50)
        signals["P7_td9_bull_ma50"] = (
            signals["P2_td_setup_9"] and
            not np.isnan(ma50) and close > ma50
        )
        signals["P8_td_perfect_ma50"] = (
            signals["P4_td_perfect_setup"] and
            not np.isnan(ma50) and close > ma50
        )
        signals["P9_countdown_ma50"] = (
            signals["P5_td_countdown_13"] and
            not np.isnan(ma50) and close > ma50
        )
    else:
        for pk in ["P1_td_setup_count","P2_td_setup_9","P3_td_setup_6plus",
                   "P4_td_perfect_setup","P5_td_countdown_13","P6_td_countdown_8plus",
                   "P7_td9_bull_ma50","P8_td_perfect_ma50","P9_countdown_ma50"]:
            signals[pk] = False

    # D: Original combo signals
    signals["D1_bkt_tight"] = signals["A2_bkt10"] and signals["B2_tight"]
    signals["D2_bkt_ma50"]  = signals["A1_bkt20_vol"] and signals["C2_above_ma50"]
    signals["D3_near_hi_ma50"] = signals["B3_near_hi20"] and signals["C2_above_ma50"]
    signals["D4_pocket_ma50"]  = signals["C3_pocket_pivot"] and signals["C2_above_ma50"]
    signals["D5_full_setup"]   = (
        signals["B3_near_hi20"] and
        signals["C2_above_ma50"] and
        signals["B2_tight"]
    )

    # Q: Top compound signals (exhaustive combo test, BULL regime, 2026-09-13)
    # Q1: N1_pp_at_ma20 + O1_in_fvg_3d  — fwd3 hit=81.0% avg=+2.04%  N=21
    # Logic: pocket pivot at MA20 (demand zone) + price inside 3-day FVG (unresolved imbalance)
    signals["Q1_pp_ma20_fvg3d"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q2: E1_pb_ma20 + G1_inside_bar + P3_td_setup_6plus  — fwd3 hit=68.2% avg=+2.95%  N=44
    # Logic: pullback to MA20 (trend intact) + inside bar (compression) + TD 6+ (seller exhaustion)
    signals["Q2_pb_inside_td6"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("G1_inside_bar", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q3: G1_inside_bar + J4_dry_value_area + P3_td_setup_6plus  — fwd3 hit=65.4% avg=+2.72%  N=52
    # Logic: inside bar + volume drying at value area + TD 6+ (three-dimensional seller exhaustion)
    signals["Q3_inside_dry_td6"] = (
        signals.get("G1_inside_bar", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q4: N1_pp_at_ma20 + O1_in_fvg_3d + B3_near_hi20  — fwd3 hit=86.7% avg=+2.60% fwd5=73.3%  N=15
    # Logic: pocket pivot at MA20 + FVG (unresolved imbalance) + price near 20-day high = uptrend confirmed
    signals["Q4_pp_fvg_near_hi"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("B3_near_hi20", False)
    )

    # Q5: H1_mom_resume + N1_pp_at_ma20 + O1_in_fvg_3d  — fwd3 hit=81.2% avg=+2.51% fwd5=68.8%  N=16
    # Logic: momentum resuming (new multi-day high) + pocket pivot at MA20 + FVG = institutional re-accumulation
    signals["Q5_mom_pp_fvg"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q6: A3_vol_surge + E3_mild_pb + N1_pp_at_ma20  — fwd3 hit=78.9% avg=+2.71% fwd5=68.4% avg=+3.59%  N=19
    # Logic: volume surge day + mild pullback (not giving up gains) + pocket pivot at MA20 = distribution absent
    signals["Q6_surge_pb_pp"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q7: F2_dry_after_surge + I9_inside_close_hi + P3_td_setup_6plus  — fwd3=76.5% avg=+5.24% fwd5=76.5% avg=+5.53%  N=17
    # Logic: vol drying after surge (distribution absent) + inside bar closing near high (bullish compression) + TD6+ exhaustion
    # Highest average return of all triples; consistent fwd3=fwd5 means momentum extends beyond 3 days
    signals["Q7_dry_inside_td6"] = (
        signals.get("F2_dry_after_surge", False) and
        signals.get("I9_inside_close_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q8: I4b_3down_bounce + I10_top_decile_day + K1_first_pb_after_bkt  — fwd3=77.8% avg=+1.05% fwd5=66.7%  N=18
    # Logic: 3-day down move then bounce + top-decile return day + first pullback after breakout = capitulation absorbed
    signals["Q8_3down_topday_pb"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("I10_top_decile_day", False) and
        signals.get("K1_first_pb_after_bkt", False)
    )

    # Q9 [4-way]: P3 + F1_vol_dry + F2_dry_after_surge + I9_inside_close_hi  — fwd3=81.2% avg=+5.60% fwd5=75.0% avg=+5.85%  N=16
    # Logic: TD6+ exhaustion + current vol dry + vol dried after prior surge + inside bar closing high
    # Best average return of all 4-way combos; 2-day vol dry-up pattern = sellers completely absent
    signals["Q9_td6_2day_dry_inside"] = (
        signals.get("P3_td_setup_6plus", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("F2_dry_after_surge", False) and
        signals.get("I9_inside_close_hi", False)
    )

    # Q10 [4-way]: B3_near_hi20 + F1_vol_dry + I10_top_decile_day + J5_dry_prior_base_hi  — fwd3=83.3% fwd5=83.3% avg=+2.77%/+2.30%  N=12
    # Logic: price near 20-day high + vol dry + top decile return today + drying near prior base high
    # Only combo with fwd3 == fwd5 hit rate (both 83.3%) = momentum sustains through 5 days
    signals["Q10_near_hi_dry_topday"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("I10_top_decile_day", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q11 [4-way]: N1_pp_at_ma20 + E1_pb_ma20 + A3_vol_surge + E3_mild_pb  — fwd3=100% avg=+3.00% fwd5=81.8%  N=11
    # Logic: pocket pivot + pullback to MA20 + volume surge + mild pullback = re-entry after initial move with demand confirmed
    # ⚠️ N=11, data concentrated 2016-2017 only — treat as HYPOTHESIS until N>=30
    signals["Q11_pp_surge_pb_ma20"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("E3_mild_pb", False)
    )

    # Q12 [4-way]: P3 + K4_first_pb_to_ma20 + X7_pb_dry_ma50 + G2_nr7  — fwd3=84.6% avg=+2.19% fwd5=61.5%  N=13
    # Logic: TD6+ exhaustion + first pullback to MA20 + pullback to MA50 drying + NR7 coil = multi-timeframe compression
    # ⚠️ fwd5 weak (61.5%) — best for 3-day exits
    signals["Q12_td6_pb_nr7"] = (
        signals.get("P3_td_setup_6plus", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("G2_nr7", False)
    )

    # Q13 [5-way]: P3 + K4 + J5_dry_prior_base_hi + E3_mild_pb + J4_dry_value_area  — fwd3=90.0% avg=+3.13%  N=10
    # Logic: TD6+ exhaustion + first pullback to MA20 + drying at prior base high + mild pullback + value area = 5-layer confluence
    # ⚠️ N=10 — HYPOTHESIS until N>=25
    signals["Q13_td6_pb_base_dry"] = (
        signals.get("P3_td_setup_6plus", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("J4_dry_value_area", False)
    )

    # Q14 [5-way]: P3 + G1_inside_bar + F2_dry_after_surge + I9_inside_close_hi + G2_nr7  — fwd3=81.8% fwd5=81.8% avg=+3.76%/+4.65%  N=11
    # Logic: TD6+ exhaustion + inside bar + vol dried after surge + inside bar closes near high + NR7 = perfect multi-layer compression
    # fwd3 == fwd5 hit rate = momentum sustains 5 days; avg return accelerates fwd3→fwd5
    signals["Q14_td6_inside_dry_nr7"] = (
        signals.get("P3_td_setup_6plus", False) and
        signals.get("G1_inside_bar", False) and
        signals.get("F2_dry_after_surge", False) and
        signals.get("I9_inside_close_hi", False) and
        signals.get("G2_nr7", False)
    )

    # Q15 [5-way]: B3_near_hi20 + F1_vol_dry + H1_mom_resume + J5_dry_prior_base_hi + I10_top_decile_day
    # fwd3=87.5% avg=+2.16% fwd5=100% avg=+3.12%  N=8
    # Logic: near 20d high + vol dry + momentum resuming + drying at prior base + top-decile day = explosive setup
    # ⚠️ N=8 — EXTREME HYPOTHESIS; fwd5=100% is extraordinary but requires more data
    signals["Q15_near_hi_dry_mom_top"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q_BEAR [BEAR regime only]: J3_dry_near_ma50 + P2_td_setup_9  — BEAR h3=65.2% avg=+2.32%  N=23
    # Logic: volume drying near MA50 + full TD Buy Setup 9 = deep exhaustion signal in downtrend = countertrend bounce
    # Designed for BEAR regime use only — not expected to work in BULL
    signals["QB1_bear_dry_ma50_td9"] = (
        signals.get("J3_dry_near_ma50", False) and
        signals.get("P2_td_setup_9", False)
    )

    # Q16: A3_vol_surge + I4b_3down_bounce  — BULL fwd3=73.7% avg=+3.08% fwd5=63.2% avg=+4.37%  N=19
    # Logic: 3-day down move then bounce + volume surge today = capitulation absorbed with force
    signals["Q16_surge_3down_bounce"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("I4b_3down_bounce", False)
    )

    # Q17: I2b_power_move_strong + I4b_3down_bounce  — BULL fwd3=70.6% avg=+3.22% fwd5=58.8% avg=+4.43%  N=17
    # Logic: strong power move today (>2.5% ret, 2x vol) + preceded by 3-day down move = V-reversal pattern
    signals["Q17_power_move_3down"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("I4b_3down_bounce", False)
    )

    # QB2 [high avg return]: I3b_gap_up_strong + I5_cap_reversal  — BULL fwd3=66.7% avg=+4.74% fwd5=71.4% avg=+6.97%  N=21
    # Logic: capitulation reversal (prev big down + today bounced) + gap up strong = gap continuation after cap = highest fwd5 avg in dataset
    # Note: h3=66.7% below 70% threshold but fwd5 avg=+6.97% is the highest avg return found; momentum extends past day 3
    signals["QB2_gap_after_cap"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False)
    )

    # QB_BEAR1 [BEAR regime only]: E1_pb_ma20 + B2_tight + J8_pb_zone_ma50  — BEAR h3=76.2% avg=+1.60%  N=21
    # Logic: tight consolidation (range <6% of price) + pullback to MA20 + near MA50 zone = oversold bounce in downtrend
    # Best bear countertrend signal found; designed for BEAR regime only
    signals["QB_BEAR1_tight_pb_ma50"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("B2_tight", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q18 [5-way]: P3+F1+F2+I9 (Q9 backbone) + F3_3day_dry  — fwd3=88.9% avg=+7.56% fwd5=88.9% avg same  N=9
    # EXTRAORDINARY: fwd3 == fwd5 both 88.9%; highest avg return of all 5-way combos (+7.56%)
    # F3_3day_dry = 3 consecutive days of drying volume after F2 surge = maximum compression before move
    # ⚠️ N=9 — EXTREME HYPOTHESIS until N>=20
    signals["Q18_q9_plus_3day_dry"] = (
        signals.get("P3_td_setup_6plus", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("F2_dry_after_surge", False) and
        signals.get("I9_inside_close_hi", False) and
        signals.get("F3_3day_dry", False)
    )

    # Q19 [5-way]: P3+F1+F2+I9 (Q9 backbone) + J10_dry_between_ma10_ma20  — fwd3=81.8% avg=+5.23% fwd5=72.7%  N=11
    # Price drying between MA10 and MA20 = perfect compression zone; adds precision to Q9 timing
    signals["Q19_q9_ma10_ma20_dry"] = (
        signals.get("P3_td_setup_6plus", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("F2_dry_after_surge", False) and
        signals.get("I9_inside_close_hi", False) and
        signals.get("J10_dry_between_ma10_ma20", False)
    )

    # Q20 [5-way]: P3+F1+F2+I9 (Q9 backbone) + C2_above_ma50  — fwd3=76.9% avg=+5.38% fwd5=69.2%  N=13
    # Stock above MA50 = confirmed uptrend context; adds structural filter to Q9
    signals["Q20_q9_above_ma50"] = (
        signals.get("P3_td_setup_6plus", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("F2_dry_after_surge", False) and
        signals.get("I9_inside_close_hi", False) and
        signals.get("C2_above_ma50", False)
    )

    # Q21 [pair]: O1_in_fvg_3d + M3_vdu_expansion_pb  — fwd3=83.3% avg=+2.33% fwd5=66.7%  N=12
    # ★ 2021+ validation: N=5 h3=100%! — small N but 100% in recent data is promising
    # VDU pullback inside an unresolved FVG = institutions re-accumulating during dry-volume retreat
    # ⚠️ N=12 all-time, N=5 recent — HYPOTHESIS until N>=15 in recent data
    signals["Q21_fvg_vdu_pb"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("M3_vdu_expansion_pb", False)
    )

    # Q22 [pair]: O1_in_fvg_3d + M1_vdu_expansion  — fwd3=77.8% avg=+1.99% fwd5=61.1%  N=18
    # ★ 2021+ validation: N=6 h3=100%! — small N but 100% in recent data is exceptional
    # VDU expansion (volume dry-up then expand) while inside FVG = institutional buying in the gap
    # HYPOTHESIS until N>=15 in recent data
    signals["Q22_fvg_vdu_expand"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("M1_vdu_expansion", False)
    )

    # QB_BEAR2 [BEAR regime only]: B2_tight + J8_pb_zone_ma50 + J10_dry_between_ma10_ma20
    # BEAR h3=82.4% avg=+1.45%  N=17 — best N bear triple found
    # Tight consolidation between MA10-MA20 while near MA50 in downtrend = precision entry in bear
    signals["QB_BEAR2_tight_j8_ma10ma20"] = (
        signals.get("B2_tight", False) and
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("J10_dry_between_ma10_ma20", False)
    )

    # QB_BEAR3 [BEAR regime only]: B2_tight + J8_pb_zone_ma50 + F4_dry_near_hi
    # BEAR h3=83.3% avg=+2.31%  N=12 — highest hit rate bear triple; dry volume near recent high in base
    signals["QB_BEAR3_tight_j8_dry_hi"] = (
        signals.get("B2_tight", False) and
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("F4_dry_near_hi", False)
    )

    # Q23 [pair]: I4b_3down_bounce + N1_pp_at_ma20  — BULL fwd3=75.0% fwd5=68.8% avg=+1.67%  N=16
    # 3-day pullback bouncing at pocket pivot on MA20 = momentum continuation after measured correction
    # Both fwd3 AND fwd5 above threshold — momentum sustained
    signals["Q23_3down_pp_ma20"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q24 [pair]: N1_pp_at_ma20 + O1_in_fvg_5d  — BULL fwd3=76.7% avg=+1.72%  N=30
    # Higher N than Q1 (FVG3d version); unresolved 5-day gap = longer-duration institutional interest
    # Best N (30) of all N1-backbone signals — most statistically robust FVG variant
    signals["Q24_pp_ma20_fvg5d"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q25 [4-way]: F2_dry_after_surge + I9_inside_close_hi + P3_td_setup_6plus + J10_dry_between_ma10_ma20
    # fwd3=75.0% fwd5=75.0% avg=+4.75%  N=12
    # fwd3==fwd5 both 75% — momentum sustained beyond 3 days; price drying between MA10-MA20 on TD setup
    # ⚠️ N=12 at minimum; NOTE: Q7/P3 family concentrated 2016-2017 — verify in recent data before trading
    signals["Q25_q7_ma10ma20_dry"] = (
        signals.get("F2_dry_after_surge", False) and
        signals.get("I9_inside_close_hi", False) and
        signals.get("P3_td_setup_6plus", False) and
        signals.get("J10_dry_between_ma10_ma20", False)
    )

    # Q26 [4-way]: P3_td_setup_6plus + K4_first_pb_to_ma20 + J5_dry_prior_base_hi  — fwd3=76.2% avg=+2.13% N=21
    # ⚠️ fwd5=52.4% only — 3-day trade, not 5-day hold; TD countdown + first MA20 pullback + dry base high
    signals["Q26_td6_k4_j5"] = (
        signals.get("P3_td_setup_6plus", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q27 [pair]: J8_pb_zone_ma50 + K4_first_pb_to_ma20  — BULL fwd3=75.0% fwd5=66.7% avg=+3.97% N=12
    # ★ VALIDATED IN RECENT DATA (2021+): h3=75% in 2021+ confirms this is NOT a 2016-17 artifact
    # First MA20 pullback coinciding with MA50 support zone = dual-MA confluence first pullback setup
    signals["Q27_k4_j8_ma50_zone"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False)
    )

    # Q28 [pair]: N1_pp_at_ma20 + O1_in_fvg_8d  — BULL fwd3=75.0% avg=+1.10% N=20 (all-time)
    # CORRECTED 2021+ validation: N=21 h3=71.4% h5=47.6% — BORDERLINE below 75%
    # Kept for signal diversity (N=21 largest in N1 family); re-evaluate at N>=30
    # Note: fwd5=47.6% in recent data — short-term 3-day trade only
    signals["Q28_pp_ma20_fvg8d"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q29 [pair]: I4b_3down_bounce + I3b_gap_up_strong  — BULL fwd3=72.7% fwd5=72.7% avg=+6.48%  N=11
    # ★ VALIDATED IN RECENT DATA (2021+): h3=72.7% h5=72.7% N=11 — both windows equal
    # Gap-up stock doing a 3-day pullback = high-quality continuation after expansion day
    # ⚠️ N=11 at minimum; avg return +6.48% = highest per-trade return in I4b family
    signals["Q29_gap_3down_bounce"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("I3b_gap_up_strong", False)
    )

    # Q30 [triple]: I3b_gap_up_strong + I5_cap_reversal + I2_power_move
    # ★ VALIDATED IN RECENT DATA (2021+): h3=75% fwd5=75% avg=+5.76%  N=16 — both windows equal
    # QB2 backbone + power-move confirmation = all 3 strong price action signals aligned
    # Most robust QB2-expansion: highest N (16) with fwd3=fwd5 both 75%
    signals["Q30_gap_cap_power"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("I2_power_move", False)
    )

    # Q31 [triple]: I3b_gap_up_strong + I5_cap_reversal + A3_vol_surge
    # ★ VALIDATED IN RECENT DATA (2021+): h3=81.8% avg=+5.76%  N=11
    # QB2 backbone + volume surge confirmation = gap + cap reversal + surge = institutional accumulation pattern
    # ⚠️ N=11 minimum; fwd5=63.6% — primarily 3-day trade
    signals["Q31_gap_cap_surge"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("A3_vol_surge", False)
    )

    # Q32 [triple]: I4b_3down_bounce + A3_vol_surge + C2_above_ma50
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=90.0% avg=+4.33%  N=10
    # EXTRAORDINARY fwd5=90%! Momentum continues hard past day 3 — hold signal
    # 3-day pullback + volume surge + above MA50 = clean continuation in healthy trend
    signals["Q32_4b_surge_ma50"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("C2_above_ma50", False)
    )

    # Q33 [triple]: I4b_3down_bounce + A3_vol_surge + I2_power_move
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=90.0% avg=+4.33%  N=10
    # EXTRAORDINARY fwd5=90%! Same N=10 as Q32 — vol surge on pullback bounce + power move = triple confirmation
    signals["Q33_4b_surge_power"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("I2_power_move", False)
    )

    # Q34 [triple]: I4b_3down_bounce + E3_mild_pb + J4_dry_value_area
    # ★ VALIDATED IN RECENT DATA (2021+): h3=85.7% h5=50.0% avg=+3.30%  N=14
    # Highest N (14) in I4b triples 2021+ — best statistical confidence
    # 3-day pullback + mild pullback + dry volume in value area = clean compressed base signal
    # ⚠️ fwd5=50% — this is a 3-day trade only, exit before day 5
    signals["Q34_4b_mild_value_dry"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("J4_dry_value_area", False)
    )

    # Q35 [triple]: I4b_3down_bounce + I1_close_quality + I3b_gap_up_strong
    # ★ VALIDATED IN RECENT DATA (2021+): h3=77.8% h5=77.8% avg=+7.55%  N=9
    # Both fwd3 AND fwd5 equal at 77.8% — exceptional multi-day momentum persistence
    # avg=+7.55% = highest avg return in all I4b triples — gap stock + quality close + pullback
    # ⚠️ N=9 minimum; strongest avg-return signal in I4b family
    signals["Q35_4b_quality_gap"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("I1_close_quality", False) and
        signals.get("I3b_gap_up_strong", False)
    )

    # Q36 [triple]: A3_vol_surge + I4b_3down_bounce (Q16) + I10_top_decile_day
    # ★ VALIDATED IN RECENT DATA (2021+): h3=75.0% h5=87.5% avg=+3.49%  N=8
    # EXTRAORDINARY fwd5=87.5%! Q16 backbone + top-decile day = acceleration continues
    # Vol surge on 3-down bounce + top-10% price performance day = institutional re-entry
    # ⚠️ N=8 minimum (borderline — watch for N growth)
    signals["Q36_q16_top_decile"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q37 [triple]: A3_vol_surge + I4b_3down_bounce (Q16) + I2b_power_move_strong
    # ★ VALIDATED IN RECENT DATA (2021+): h3=75.0% h5=87.5% avg=+4.95%  N=8
    # EXTRAORDINARY fwd5=87.5%! Q16 backbone + strong power move = maximum momentum
    # highest avg return in Q16 expansions (+4.95%) — vol surge + 3-day pb + strong power day
    # ⚠️ N=8 minimum; avg return > Q36
    signals["Q37_q16_power_strong"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q38 [triple]: J8_pb_zone_ma50 + K4_first_pb_to_ma20 + E3_mild_pb
    # ★ VALIDATED IN RECENT DATA (2021+): h3=81.8% h5=72.7% avg=+4.93%  N=11
    # Strongest K4 triple — pullback to MA50 zone + first pullback after breakout + mild pullback
    # h3=h5 both strong + avg=+4.93% = high-conviction first pullback setup
    signals["Q38_k4_j8_mild_pb"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("E3_mild_pb", False)
    )

    # Q39 [triple]: A3_vol_surge + I4b_3down_bounce + I4_2down_bounce
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=90.0% avg=+4.33%  N=10
    # EXTRAORDINARY fwd5=90%! I4 (2-day bounce) + I4b (3-day) = two consecutive pullback signals
    # Dual-confirmation: both 2-day and 3-day pullback signals firing together = high conviction
    signals["Q39_q16_2down_also"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q40 [triple]: I3b_gap_up_strong + I5_cap_reversal + O2_choch_10d
    # ★ VALIDATED IN RECENT DATA (2021+): h3=81.8% h5=63.6% avg=+7.05%  N=11
    # QB2 backbone + CHoCH (Change of Character) 10-day = gap+reversal+market structure break
    # Highest avg return in QB2 triples (+7.05%) — strong momentum continuation setup
    signals["Q40_qb2_choch"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("O2_choch_10d", False)
    )

    # Q41 [triple]: I3b_gap_up_strong + I5_cap_reversal + I10_top_decile_day
    # ★ VALIDATED IN RECENT DATA (2021+): h3=76.9% h5=61.5% avg=+6.15%  N=13
    # QB2 backbone + top-decile performance day = large N (13) + strong avg return
    # Best N in QB2 triples with both fwd3>75% and avg>+6%
    signals["Q41_qb2_top_decile"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q42 [triple]: J8_pb_zone_ma50 + K4_first_pb_to_ma20 + E1_pb_ma20
    # ★ VALIDATED IN RECENT DATA (2021+): h3=77.8% h5=77.8% avg=+4.55%  N=9
    # Both fwd3 AND fwd5 equal = momentum sustains past day 3 — hold signal
    # K4+J8 backbone + E1 (pullback to MA20) = triple MA-support confirmation
    signals["Q42_k4_j8_e1"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("E1_pb_ma20", False)
    )

    # Q43 [triple]: A3_vol_surge + I4b_3down_bounce + C3_pocket_pivot
    # ★ VALIDATED IN RECENT DATA (2021+): h3=77.8% h5=88.9% avg=+4.57%  N=9
    # EXTRAORDINARY fwd5=88.9%! Q16 backbone + pocket pivot confirmation
    # Pocket pivot = stock showing strength vs down-days = accumulation confirmed
    signals["Q43_q16_pocket_pp"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("C3_pocket_pivot", False)
    )

    # Q44 [triple]: A3_vol_surge + I4b_3down_bounce + D4_pocket_ma50
    # ★ VALIDATED IN RECENT DATA (2021+): h3=77.8% h5=88.9% avg=+4.57%  N=9
    # EXTRAORDINARY fwd5=88.9%! Q16 backbone + pocket pivot near MA50 = D4 variant
    # D4 = pocket pivot near MA50 support; MA50 as demand zone adds confluence
    signals["Q44_q16_pocket_ma50"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q45 [triple]: J8_pb_zone_ma50 + K4_first_pb_to_ma20 + J2_dry_near_ma20
    # ★ VALIDATED IN RECENT DATA (2021+): h3=77.8% h5=77.8% avg=+4.55%  N=9
    # Both fwd3 AND fwd5 equal — momentum persists. K4+J8 + volume dry-up near MA20
    # Low volume pullback to MA20 within MA50 zone = classic VCP-like dry-up setup
    signals["Q45_k4_j8_dry_ma20"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("J2_dry_near_ma20", False)
    )

    # Q46 [quad]: J8_pb_zone_ma50 + K4_first_pb_to_ma20 + E3_mild_pb + E1_pb_ma20
    # ★ VALIDATED IN RECENT DATA (2021+): h3=87.5% h5=87.5% avg=+5.94%  N=8
    # Q38 (K4+J8+E3) + E1 add-on — double MA20 pullback confirmation
    signals["Q46_k4_j8_e3_e1"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("E1_pb_ma20", False)
    )

    # Q47 [quad]: J8_pb_zone_ma50 + K4_first_pb_to_ma20 + E3_mild_pb + J2_dry_near_ma20
    # ★ VALIDATED IN RECENT DATA (2021+): h3=87.5% h5=87.5% avg=+5.94%  N=8
    # Q38 + J2 dry-up near MA20 — volume confirmation of MA20 support
    signals["Q47_k4_j8_e3_j2"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("J2_dry_near_ma20", False)
    )

    # Q48 [quad]: I3b_gap_up_strong + I5_cap_reversal + O2_choch_10d + I10_top_decile_day
    # ★ VALIDATED IN RECENT DATA (2021+): h3=90.0% h5=60.0% avg=+7.76%  N=10
    # Q40 + I10 top-decile day — gap+cap+CHoCH + exceptional volume day
    # ⚠️ h5=60% only — momentum may stall after 3 days; target 3-day exit
    signals["Q48_qb2_choch_topdecile"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("O2_choch_10d", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q49 [quad]: I3b_gap_up_strong + I5_cap_reversal + O2_choch_10d + A3_vol_surge
    # ★ VALIDATED IN RECENT DATA (2021+): h3=88.9% h5=55.6% avg=+6.50%  N=9
    # Q40 + A3 surge — gap+cap+CHoCH + volume surge confirmation
    signals["Q49_qb2_choch_surge"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("O2_choch_10d", False) and
        signals.get("A3_vol_surge", False)
    )

    # Q50 [quad]: I3b_gap_up_strong + I5_cap_reversal + O2_choch_10d + I2b_power_move_strong
    # ★ VALIDATED IN RECENT DATA (2021+): h3=88.9% h5=55.6% avg=+6.50%  N=9
    # Q40 + strong power move — highest-quality Q40 expansion
    signals["Q50_qb2_choch_powstr"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("O2_choch_10d", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # ── NEW FAMILIES DISCOVERED (brute force 2021+ exhaustive) ────────────────

    # Q58 [triple]: I9_inside_close_hi + J4_dry_value_area + K3_first_pb_shallow
    # ★ VALIDATED IN RECENT DATA (2021+): h3=85.7% h5=71.4% avg=+1.50%  N=14
    # Both fwd consistent — inside close near high + dry value area + shallow pullback
    signals["Q58_i9_j4_k3"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("K3_first_pb_shallow", False)
    )

    # Q59 [triple]: G1_inside_bar + I1b_close_quality_hi_vol + I8_3up_days
    # ★ VALIDATED IN RECENT DATA (2021+): h3=81.8% h5=68.2% avg=+3.15%  N=22
    # LARGE N=22 — inside bar + high-vol quality close + 3 consecutive up days
    signals["Q59_g1_i1b_i8"] = (
        signals.get("G1_inside_bar", False) and
        signals.get("I1b_close_quality_hi_vol", False) and
        signals.get("I8_3up_days", False)
    )

    # Q60 [triple]: I1b_close_quality_hi_vol + I8_3up_days + I9_inside_close_hi
    # ★ VALIDATED IN RECENT DATA (2021+): h3=81.8% h5=68.2% avg=+3.15%  N=22
    # LARGE N=22 — high-vol quality close + 3 up days + inside high close
    signals["Q60_i1b_i8_i9"] = (
        signals.get("I1b_close_quality_hi_vol", False) and
        signals.get("I8_3up_days", False) and
        signals.get("I9_inside_close_hi", False)
    )

    # Q61 [triple]: X4_inside_near_hi + I1b_close_quality_hi_vol + I8_3up_days
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=65.0% avg=+2.54%  N=20
    # LARGE N=20 — inside near high + high-vol quality close + 3 up days
    signals["Q61_x4_i1b_i8"] = (
        signals.get("X4_inside_near_hi", False) and
        signals.get("I1b_close_quality_hi_vol", False) and
        signals.get("I8_3up_days", False)
    )

    # Q62 [triple]: G2_nr7 + K4_first_pb_to_ma20 + D5_full_setup
    # ★ VALIDATED IN RECENT DATA (2021+): h3=78.3% h5=68.2% avg=+2.01%  N=23
    # LARGEST N=23 — NR7 narrow range + first pullback to MA20 + full setup flag
    signals["Q62_g2_k4_d5"] = (
        signals.get("G2_nr7", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("D5_full_setup", False)
    )

    # Q63 [triple]: X5_nr7_ma50 + K4_first_pb_to_ma20 + D5_full_setup
    # ★ VALIDATED IN RECENT DATA (2021+): h3=78.3% h5=68.2% avg=+2.01%  N=23
    # LARGEST N=23 — NR7 at MA50 + first pullback to MA20 + full setup flag
    signals["Q63_x5_k4_d5"] = (
        signals.get("X5_nr7_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("D5_full_setup", False)
    )

    # Q64 [triple]: B1_tight_near_hi + X7_pb_dry_ma50 + O1_in_fvg_8d
    # ★ VALIDATED IN RECENT DATA (2021+): h3=77.8% h5=75.0% avg=+2.43%  N=18
    # Both fwd consistent — tight near high + dry pullback MA50 + in FVG 8-day
    signals["Q64_b1_x7_fvg8"] = (
        signals.get("B1_tight_near_hi", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q65 [triple]: N1_pp_at_ma20 + O1_in_fvg_5d + C2_above_ma50
    # ★ VALIDATED IN RECENT DATA (2021+): h3=83.3% h5=50.0% avg=+1.56%  N=12
    # ⚠️ h5=50% — 3-day trade only; PP at MA20 + in FVG + above MA50 context
    signals["Q65_n1_fvg5_c2"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("C2_above_ma50", False)
    )

    # Q66 [triple]: N1_pp_at_ma20 + O1_in_fvg_5d + D4_pocket_ma50
    # ★ VALIDATED IN RECENT DATA (2021+): h3=83.3% h5=50.0% avg=+1.56%  N=12
    # ⚠️ h5=50% — 3-day trade only; PP at MA20 + in FVG + pocket pivot MA50
    signals["Q66_n1_fvg5_d4"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q67 [triple]: I9_inside_close_hi + J4_dry_value_area + B3_near_hi20
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=66.7% avg=+1.35%  N=15
    # Inside close near high + dry value area + price near 20-day high
    signals["Q67_i9_j4_b3"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("B3_near_hi20", False)
    )

    # Q68 [triple]: I9_inside_close_hi + J4_dry_value_area + D3_near_hi_ma50
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=66.7% avg=+1.35%  N=15
    # Inside close near high + dry value area + near high relative to MA50
    signals["Q68_i9_j4_d3"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("D3_near_hi_ma50", False)
    )

    # Q69 [triple]: K4_first_pb_to_ma20 + D5_full_setup + X2_mild_pb_dry
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=70.0% avg=+1.51%  N=10
    # First pullback to MA20 + full setup context + mild dry-up pullback
    signals["Q69_k4_d5_x2"] = (
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("D5_full_setup", False) and
        signals.get("X2_mild_pb_dry", False)
    )

    # Q70 [triple]: I9_inside_close_hi + J4_dry_value_area + D5_full_setup
    # ★ VALIDATED IN RECENT DATA (2021+): h3=87.5% h5=75.0% avg unknown  N=8
    # Both fwd consistent — inside close high + dry value area + full setup flag
    signals["Q70_i9_j4_d5"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("D5_full_setup", False)
    )

    # Q71 [triple]: I9_inside_close_hi + J4_dry_value_area + E4_shallow_pb
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=66.7% avg=+1.35%  N=15
    # Inside close high + dry value area + shallow pullback
    signals["Q71_i9_j4_e4"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("E4_shallow_pb", False)
    )

    # Q72 [triple]: I9_inside_close_hi + J4_dry_value_area + X4_inside_near_hi
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=66.7% avg=+1.35%  N=15
    # Double inside pattern + dry value area — highest repeat N for I9+J4
    signals["Q72_i9_j4_x4"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("X4_inside_near_hi", False)
    )

    # Q73 [triple]: I9_inside_close_hi + J4_dry_value_area + B2_tight
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=60.0% avg=+1.88%  N=10
    # Inside close high + dry value area + tight consolidation
    signals["Q73_i9_j4_b2"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("B2_tight", False)
    )

    # Q74 [quad]: G2_nr7 + K4_first_pb_to_ma20 + D5_full_setup + F1_vol_dry
    # ★ VALIDATED IN RECENT DATA (2021+): h3=86.7% h5=60.0% avg=+2.43%  N=15
    # NR7 + first pb MA20 + full setup + volume dry-up — 4-way with large N=15
    # ⚠️ h5=60% — exit closer to 3 days
    signals["Q74_q62_f1_dry"] = (
        signals.get("G2_nr7", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("D5_full_setup", False) and
        signals.get("F1_vol_dry", False)
    )

    # Q75 [quad]: G2_nr7 + K4_first_pb_to_ma20 + D5_full_setup + F4_dry_near_hi
    # ★ VALIDATED IN RECENT DATA (2021+): h3=86.7% h5=60.0% avg=+2.43%  N=15
    # NR7 + first pb MA20 + full setup + dry near recent high
    signals["Q75_q62_f4_dry"] = (
        signals.get("G2_nr7", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("D5_full_setup", False) and
        signals.get("F4_dry_near_hi", False)
    )

    # Q76 [quad]: G2_nr7 + K4_first_pb_to_ma20 + D5_full_setup + E3_mild_pb
    # ★ VALIDATED IN RECENT DATA (2021+): h3=88.9% h5=55.6% avg=+1.17%  N=9
    # NR7 + first pb MA20 + full setup + mild pullback confirmation
    signals["Q76_q62_e3"] = (
        signals.get("G2_nr7", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("D5_full_setup", False) and
        signals.get("E3_mild_pb", False)
    )

    # Q77 [quad]: I9_inside_close_hi + J4_dry_value_area + K3_first_pb_shallow + B2_tight
    # ★ VALIDATED IN RECENT DATA (2021+): h3=87.5% h5=75.0% avg=+2.26%  N=8
    # Both fwd consistent — Q58 + tight consolidation add-on
    signals["Q77_q58_b2"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("K3_first_pb_shallow", False) and
        signals.get("B2_tight", False)
    )

    # Q78 [quad]: B1_tight_near_hi + X7_pb_dry_ma50 + O1_in_fvg_8d + F3_3day_dry
    # ★ VALIDATED IN RECENT DATA (2021+): h3=88.9% h5=66.7% avg=+2.71%  N=9
    # Q64 + 3-day volume dry-up — tightest volume pattern expansion
    signals["Q78_q64_f3"] = (
        signals.get("B1_tight_near_hi", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("F3_3day_dry", False)
    )

    # Q79 [quad]: B1_tight_near_hi + X7_pb_dry_ma50 + O1_in_fvg_8d + X8_3dry_near_hi
    # ★ VALIDATED IN RECENT DATA (2021+): h3=88.9% h5=66.7% avg=+2.71%  N=9
    # Q64 + 3 dry bars near high — maximum dry-up confirmation
    signals["Q79_q64_x8"] = (
        signals.get("B1_tight_near_hi", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("X8_3dry_near_hi", False)
    )

    # ── J7 (VCP zone near MA20) + FVG FAMILY — NEW, 2022+ validated ──────────────

    # Q80 [triple]: J7_vcp_zone_ma20 + J5_dry_prior_base_hi + O1_in_fvg_5d
    # ★ VALIDATED IN RECENT DATA (2021+): h3=88.9% h5=100.0% avg=+3.97%  N=9
    # ★★ ALL 9 OCCURRENCES ARE IN 2022+ — most recent-robust signal found!
    # VCP zone near MA20 + dry near prior base high + in FVG = perfect setup
    signals["Q80_j7_j5_fvg5"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q81 [triple]: J7_vcp_zone_ma20 + B1_tight_near_hi + O1_in_fvg_5d
    # ★ VALIDATED IN RECENT DATA (2021+): h3=87.5% h5=75.0% avg=+1.52%  N=8
    # ★ ALL 8 OCCURRENCES ARE IN 2022+ — 2022+ validated
    # VCP zone + tight near high + in FVG = highest quality pre-breakout
    signals["Q81_j7_b1_fvg5"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("B1_tight_near_hi", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q82 [triple]: J7_vcp_zone_ma20 + B2_tight + X3_shallow_pb_surge
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=70.0% avg=+1.51%  N=10
    # VCP zone MA20 + tight pattern + shallow pullback with surge
    signals["Q82_j7_b2_x3"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("B2_tight", False) and
        signals.get("X3_shallow_pb_surge", False)
    )

    # Q83 [triple]: J7_vcp_zone_ma20 + F2_dry_after_surge + D5_full_setup
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=70.0% avg=+1.51%  N=10
    # VCP zone MA20 + dry after volume surge + full setup context
    signals["Q83_j7_f2_d5"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("F2_dry_after_surge", False) and
        signals.get("D5_full_setup", False)
    )

    # Q84 [triple]: J7_vcp_zone_ma20 + X4_inside_near_hi + O1_in_fvg_5d
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=70.0% avg=+2.23%  N=10
    # VCP zone MA20 + inside bar near high + in FVG (5-day)
    signals["Q84_j7_x4_fvg5"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q85 [triple]: J7_vcp_zone_ma20 + X2_mild_pb_dry + D5_full_setup
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=70.0% avg=+1.51%  N=10
    # VCP zone MA20 + mild dry pullback + full setup
    signals["Q85_j7_x2_d5"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("X2_mild_pb_dry", False) and
        signals.get("D5_full_setup", False)
    )

    # Q86 [triple]: J7_vcp_zone_ma20 + X3_shallow_pb_surge + D5_full_setup
    # ★ VALIDATED IN RECENT DATA (2021+): h3=80.0% h5=70.0% avg=+1.51%  N=10
    signals["Q86_j7_x3_d5"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("X3_shallow_pb_surge", False) and
        signals.get("D5_full_setup", False)
    )

    # Q87 [triple]: B2_tight + G2_nr7 + O1_in_fvg_8d
    # ★ VALIDATED 2021+: h3=81.8% h5=81.8% avg=+1.88%  N=11 (both fwd consistent!)
    signals["Q87_b2_g2_fvg8"] = (
        signals.get("B2_tight", False) and
        signals.get("G2_nr7", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q88 [triple]: B2_tight + X3_shallow_pb_surge + J9_3dry_at_ma20
    # ★ VALIDATED 2021+: h3=81.8% h5=81.8% avg=+1.48%  N=11 (both fwd consistent!)
    signals["Q88_b2_x3_j9"] = (
        signals.get("B2_tight", False) and
        signals.get("X3_shallow_pb_surge", False) and
        signals.get("J9_3dry_at_ma20", False)
    )

    # Q89 [triple]: B2_tight + X5_nr7_ma50 + O1_in_fvg_8d
    # ★ VALIDATED 2021+: h3=81.8% h5=81.8% avg=+1.88%  N=11 (both fwd consistent!)
    signals["Q89_b2_x5_fvg8"] = (
        signals.get("B2_tight", False) and
        signals.get("X5_nr7_ma50", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q90 [triple]: B2_tight + F2_dry_after_surge + O1_in_fvg_8d
    # ★ VALIDATED 2021+: h3=80.0% h5=70.0% avg=+1.76%  N=10
    signals["Q90_b2_f2_fvg8"] = (
        signals.get("B2_tight", False) and
        signals.get("F2_dry_after_surge", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q91 [triple]: B2_tight + I6_nr7_close_hi + I7_dry_up_near_hi
    # ★ VALIDATED 2021+: h3=80.0% h5=80.0% avg=+1.80%  N=10 (both fwd consistent!)
    signals["Q91_b2_i6_i7"] = (
        signals.get("B2_tight", False) and
        signals.get("I6_nr7_close_hi", False) and
        signals.get("I7_dry_up_near_hi", False)
    )

    # Q93 [triple]: I6_nr7_close_hi + I7_dry_up_near_hi + D5_full_setup
    # ★★ VALIDATED 2021+: h3=88.9% h5=88.9% avg=+2.30%  N=9 (BOTH fwd consistent!)
    # NR7 close near high + dry up near high + full setup — extremely consistent
    signals["Q93_i6_i7_d5"] = (
        signals.get("I6_nr7_close_hi", False) and
        signals.get("I7_dry_up_near_hi", False) and
        signals.get("D5_full_setup", False)
    )

    # Q94 [triple]: I6_nr7_close_hi + I7_dry_up_near_hi + I10_top_decile_day
    # ★★ VALIDATED 2021+: h3=80.0% h5=80.0% avg=+4.54%  N=10 (BOTH fwd consistent! Highest avg return)
    # NR7 close near high + dry up near high + top decile volume day
    signals["Q94_i6_i7_i10"] = (
        signals.get("I6_nr7_close_hi", False) and
        signals.get("I7_dry_up_near_hi", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q95 [triple]: I6_nr7_close_hi + I7_dry_up_near_hi + J4_dry_value_area
    # ★ VALIDATED 2021+: h3=77.8% h5=88.9% avg=+1.95%  N=9 (h5 stronger than h3 — unusual)
    signals["Q95_i6_i7_j4"] = (
        signals.get("I6_nr7_close_hi", False) and
        signals.get("I7_dry_up_near_hi", False) and
        signals.get("J4_dry_value_area", False)
    )

    # Q96 [quad]: B2_tight + G2_nr7 + O1_in_fvg_8d + J1_dry_near_ma10
    # ★★ VALIDATED 2021+: h3=88.9% h5=77.8% avg=+2.07%  N=9 (both fwd consistent!)
    signals["Q96_b2_g2_fvg8_j1"] = (
        signals.get("B2_tight", False) and
        signals.get("G2_nr7", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("J1_dry_near_ma10", False)
    )

    # Q97 [quad]: B2_tight + G2_nr7 + O1_in_fvg_8d + J10_dry_between_ma10_ma20
    # ★★ VALIDATED 2021+: h3=88.9% h5=77.8% avg=+2.07%  N=9 (both fwd consistent!)
    signals["Q97_b2_g2_fvg8_j10"] = (
        signals.get("B2_tight", False) and
        signals.get("G2_nr7", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("J10_dry_between_ma10_ma20", False)
    )

    # Q98 [quad]: B2_tight + G2_nr7 + O1_in_fvg_8d + K3_first_pb_shallow
    # ★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+1.98%  N=8 (both fwd consistent!)
    signals["Q98_b2_g2_fvg8_k3"] = (
        signals.get("B2_tight", False) and
        signals.get("G2_nr7", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("K3_first_pb_shallow", False)
    )

    # Q99 [quad]: B2_tight + X5_nr7_ma50 + O1_in_fvg_8d + J1_dry_near_ma10
    # ★★ VALIDATED 2021+: h3=88.9% h5=77.8% avg=+2.07%  N=9 (both fwd consistent!)
    signals["Q99_b2_x5_fvg8_j1"] = (
        signals.get("B2_tight", False) and
        signals.get("X5_nr7_ma50", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("J1_dry_near_ma10", False)
    )

    # Q100 [quad]: B2_tight + X5_nr7_ma50 + O1_in_fvg_8d + J10_dry_between_ma10_ma20
    # ★★ VALIDATED 2021+: h3=88.9% h5=77.8% avg=+2.07%  N=9 (both fwd consistent!)
    signals["Q100_b2_x5_fvg8_j10"] = (
        signals.get("B2_tight", False) and
        signals.get("X5_nr7_ma50", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("J10_dry_between_ma10_ma20", False)
    )

    # Q101 [quad]: I6_nr7_close_hi + I7_dry_up_near_hi + D5_full_setup + G2_nr7
    # ★★ VALIDATED 2021+: h3=88.9% h5=88.9% avg=+2.30%  N=9 (BOTH fwd consistent!)
    signals["Q101_i6_i7_d5_g2"] = (
        signals.get("I6_nr7_close_hi", False) and
        signals.get("I7_dry_up_near_hi", False) and
        signals.get("D5_full_setup", False) and
        signals.get("G2_nr7", False)
    )

    # Q102 [quad]: I6_nr7_close_hi + I7_dry_up_near_hi + D5_full_setup + J1_dry_near_ma10
    # ★★ VALIDATED 2021+: h3=88.9% h5=88.9% avg=+2.30%  N=9 (BOTH fwd consistent!)
    signals["Q102_i6_i7_d5_j1"] = (
        signals.get("I6_nr7_close_hi", False) and
        signals.get("I7_dry_up_near_hi", False) and
        signals.get("D5_full_setup", False) and
        signals.get("J1_dry_near_ma10", False)
    )

    # Q103 [quad]: I6_nr7_close_hi + I7_dry_up_near_hi + I10_top_decile_day + H1_mom_resume
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+5.24%  N=8 (HIGHEST avg return in entire system!)
    signals["Q103_i6_i7_i10_h1"] = (
        signals.get("I6_nr7_close_hi", False) and
        signals.get("I7_dry_up_near_hi", False) and
        signals.get("I10_top_decile_day", False) and
        signals.get("H1_mom_resume", False)
    )

    # Q104 [triple]: I6_nr7_close_hi + X4_inside_near_hi + J4_dry_value_area
    # ★★ VALIDATED 2021+: h3=88.9% h5=66.7% avg=+1.85%  N=9
    signals["Q104_i6_x4_j4"] = (
        signals.get("I6_nr7_close_hi", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("J4_dry_value_area", False)
    )

    # Q105 [triple]: J9_3dry_at_ma20 + C1_early_move + X3_shallow_pb_surge
    # ★ VALIDATED 2021+: h3=87.5% h5=62.5% avg=+1.61%  N=8 (⚠️ h5 moderate)
    signals["Q105_j9_c1_x3"] = (
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("C1_early_move", False) and
        signals.get("X3_shallow_pb_surge", False)
    )

    # Q106 [triple]: J9_3dry_at_ma20 + X3_shallow_pb_surge + D5_full_setup
    # ★ VALIDATED 2021+: h3=81.8% h5=81.8% avg=+1.48%  N=11 (both fwd consistent!)
    signals["Q106_j9_x3_d5"] = (
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("X3_shallow_pb_surge", False) and
        signals.get("D5_full_setup", False)
    )

    # Q107 [quad]: J7_vcp_zone_ma20 + J5_dry_prior_base_hi + O1_in_fvg_5d + J1_dry_near_ma10
    # ★★★ VALIDATED 2021+: h3=100.0% h5=100.0% avg=+4.47%  N=8 (PERFECT BOTH FWD! ALL 2022+)
    # VCP zone MA20 + dry at base high + in FVG + dry near 10MA
    signals["Q107_q80_j1"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("J1_dry_near_ma10", False)
    )

    # Q108 [quad]: J7_vcp_zone_ma20 + J5_dry_prior_base_hi + O1_in_fvg_5d + J10_dry_between_ma10_ma20
    # ★★★ VALIDATED 2021+: h3=100.0% h5=100.0% avg=+4.47%  N=8 (PERFECT BOTH FWD! ALL 2022+)
    # VCP zone MA20 + dry at base high + in FVG + dry between MA10-MA20
    signals["Q108_q80_j10"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("J10_dry_between_ma10_ma20", False)
    )

    # Q109 [5-way]: J8+K4+E3+E1 + C2_above_ma50
    # ★★ VALIDATED 2021+: h3=87.5% h5=87.5% avg=+5.94%  N=8 (BOTH fwd consistent! High avg)
    signals["Q109_q46_c2"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("C2_above_ma50", False)
    )

    # Q110 [5-way]: J8+K4+E3+E1 + J2_dry_near_ma20
    # ★★ VALIDATED 2021+: h3=87.5% h5=87.5% avg=+5.94%  N=8 (BOTH fwd consistent! High avg)
    signals["Q110_q46_j2"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("J2_dry_near_ma20", False)
    )

    # Q111 [quad]: J7+J5+FVG5 + C2_above_ma50
    # ★★★ 2021+ h3=88.9% h5=100.0% N=9 | 2022+ N=9 h3=88.9% h5=100.0% (ALL 2022+ DATA!)
    signals["Q111_q80_c2"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("C2_above_ma50", False)
    )

    # Q112 [quad]: J7+J5+FVG5 + E1_pb_ma20
    # ★★★ 2021+ h3=88.9% h5=100.0% N=9 | 2022+ N=9 h3=88.9% h5=100.0% (ALL 2022+ DATA!)
    signals["Q112_q80_e1"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("E1_pb_ma20", False)
    )

    # Q113 [quad]: J7+J5+FVG5 + E3_mild_pb
    # ★★★ 2021+ h3=88.9% h5=100.0% N=9 | 2022+ N=9 h3=88.9% h5=100.0% (ALL 2022+ DATA!)
    signals["Q113_q80_e3"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("E3_mild_pb", False)
    )

    # Q114 [triple]: O3_choch_dry + G2_nr7 + X2_mild_pb_dry
    # ★ VALIDATED 2021+: h3=85.7% h5=71.4% avg=+5.30%  N=7 (HIGH avg return, borderline N)
    signals["Q114_o3_g2_x2"] = (
        signals.get("O3_choch_dry", False) and
        signals.get("G2_nr7", False) and
        signals.get("X2_mild_pb_dry", False)
    )

    # Q115 [triple]: K1_first_pb_after_bkt + G2_nr7 + D5_full_setup
    # ★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+1.16%  N=11 (⚠️ h5 moderate)
    signals["Q115_k1_g2_d5"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("G2_nr7", False) and
        signals.get("D5_full_setup", False)
    )

    # Q116 [quad]: J7+B1+FVG5 + E1_pb_ma20 (expand Q81)
    # ★★★ 2021+ h3=87.5% h5=75.0% N=8 | 2022+ N=8 h3=87.5% (ALL 2022+ DATA!)
    signals["Q116_q81_e1"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("B1_tight_near_hi", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("E1_pb_ma20", False)
    )

    # Q117 [quad]: J7+B1+FVG5 + E3_mild_pb (expand Q81)
    # ★★★ 2021+ h3=87.5% h5=75.0% N=8 | 2022+ N=8 h3=87.5% (ALL 2022+ DATA!)
    signals["Q117_q81_e3"] = (
        signals.get("J7_vcp_zone_ma20", False) and
        signals.get("B1_tight_near_hi", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("E3_mild_pb", False)
    )

    # Q118 [quad]: I9+J4+D5 + B1_tight_near_hi (expand Q70)
    # ★★ 2021+: h3=87.5% h5=75.0% avg=+2.26%  N=8 (both fwd consistent!)
    signals["Q118_q70_b1"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("D5_full_setup", False) and
        signals.get("B1_tight_near_hi", False)
    )

    # Q119 [quad]: I9+J4+D5 + J1_dry_near_ma10 (expand Q70)
    # ★★ 2021+: h3=87.5% h5=75.0% avg=+2.26%  N=8 (both fwd consistent!)
    signals["Q119_q70_j1"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("D5_full_setup", False) and
        signals.get("J1_dry_near_ma10", False)
    )

    # Q120 [quad]: B1_tight_near_hi + X7_pb_dry_ma50 + O1_in_fvg_8d + J2_dry_near_ma20
    # ★★ 2021+ h3=88.9% h5=55.6% N=9 | 2022+ N=9 h3=88.9% (ALL 2022+ DATA!)
    signals["Q120_q64_j2"] = (
        signals.get("B1_tight_near_hi", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("J2_dry_near_ma20", False)
    )

    # Q121 [quad]: B1_tight_near_hi + X7_pb_dry_ma50 + O1_in_fvg_8d + K4_first_pb_to_ma20
    # ★★ 2021+ h3=88.9% h5=55.6% N=9 | 2022+ N=9 h3=88.9% (ALL 2022+ DATA!)
    signals["Q121_q64_k4"] = (
        signals.get("B1_tight_near_hi", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("K4_first_pb_to_ma20", False)
    )

    # Q122 [quad]: I9+J4+K3 + G2_nr7
    # ★★ 2021+ h3=88.9% h5=66.7% N=9 | 2022+ N=9 h3=88.9% (ALL 2022+ DATA!)
    signals["Q122_q58_g2"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("K3_first_pb_shallow", False) and
        signals.get("G2_nr7", False)
    )

    # Q123 [quad]: I9+J4+K3 + I6_nr7_close_hi
    # ★★ 2021+ h3=88.9% h5=66.7% N=9 | 2022+ N=9 h3=88.9% (ALL 2022+ DATA!)
    signals["Q123_q58_i6"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("K3_first_pb_shallow", False) and
        signals.get("I6_nr7_close_hi", False)
    )

    # Q124 [quad]: G2+K4+D5 + K3_first_pb_shallow
    # ★★ 2021+ h3=81.8% N=22 | 2022+ N=18 h3=88.9% (IMPROVES in 2022+! Large N)
    signals["Q124_q62_k3"] = (
        signals.get("G2_nr7", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("D5_full_setup", False) and
        signals.get("K3_first_pb_shallow", False)
    )

    # Q125 [triple]: E3_mild_pb + I4b_3down_bounce + J4_dry_value_area
    # VALIDATED 2021+: h3=85.7% h5=50.0% avg=+3.30%  N=14 (LARGE N! h5 weak — 3-day trade)
    signals["Q125_e3_i4b_j4"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("J4_dry_value_area", False)
    )

    # Q126 [triple]: A3_vol_surge + G1_inside_bar + O1_in_fvg_5d
    # VALIDATED 2021+: h3=78.6% h5=57.1% avg=+1.86%  N=14
    signals["Q126_a3_g1_fvg5"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("G1_inside_bar", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q127 [triple]: I4_2down_bounce + O1_in_fvg_5d + D4_pocket_ma50
    # VALIDATED 2021+: h3=78.6% h5=64.3% avg=+1.60%  N=14
    signals["Q127_i4_fvg5_d4"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q128 [triple]: B3_near_hi20 + N1_pp_at_ma20 + O1_in_fvg_8d
    # VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.66%  N=12 (both fwd consistent!)
    signals["Q128_b3_n1_fvg8"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q129 [triple]: N1_pp_at_ma20 + O1_in_fvg_8d + D3_near_hi_ma50
    # VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.66%  N=12
    signals["Q129_n1_fvg8_d3"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("D3_near_hi_ma50", False)
    )

    # Q130 [triple]: X7_pb_dry_ma50 + K4_first_pb_to_ma20 + D5_full_setup
    # VALIDATED 2021+: h3=78.6% h5=50.0% avg=+0.60%  N=14 (h5 weak — 3-day only)
    signals["Q130_x7_k4_d5"] = (
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("D5_full_setup", False)
    )

    # Q131 [triple]: N1_pp_at_ma20 + E4_shallow_pb + O1_in_fvg_8d
    # ★★★ VALIDATED 2021+: h3=100.0% h5=70.0% avg=+3.09%  N=10 | 2022+ N=9 h3=100.0%!!
    # Pocket pivot at MA20 + shallow pullback + in FVG (8-day) — PERFECT 3-day hit rate
    signals["Q131_n1_e4_fvg8"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q220 [triple]: M1_vdu_expansion + H1_mom_resume + O1_in_fvg_3d
    # VALIDATED 2021+: h3=76.5% h5=64.7% avg=+2.07%  N=17 (LARGE N!)
    signals["Q220_m1_h1_fvg3"] = (
        signals.get("M1_vdu_expansion", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q237 [triple]: X5_nr7_ma50 + J5_dry_prior_base_hi + P3_td_setup_6plus
    # VALIDATED 2021+: h3=75.0% h5=75.0% avg=+3.71%  N=8
    signals["Q237_x5_j5_p3"] = (
        signals.get("X5_nr7_ma50", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q236 [triple]: X5_nr7_ma50 + I5_cap_reversal + P1_td_setup_count
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+3.46%  N=8
    signals["Q236_x5_i5_p1"] = (
        signals.get("X5_nr7_ma50", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q235 [triple]: X5_nr7_ma50 + X2_mild_pb_dry + O2_choch_10d
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+4.23%  N=8 (HIGH avg!)
    signals["Q235_x5_x2_o2"] = (
        signals.get("X5_nr7_ma50", False) and
        signals.get("X2_mild_pb_dry", False) and
        signals.get("O2_choch_10d", False)
    )

    # Q234 [triple]: B2_tight + E2_pb_ma50 + O1_in_fvg_8d
    # VALIDATED 2021+: h3=77.8% h5=88.9% avg=+0.33%  N=9 (HIGH h5!)
    signals["Q234_b2_e2_fvg8"] = (
        signals.get("B2_tight", False) and
        signals.get("E2_pb_ma50", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q233 [triple]: K1_first_pb_after_bkt + J8_pb_zone_ma50 + P1_td_setup_count
    # VALIDATED 2021+: h3=75.0% h5=68.8% avg=+1.63%  N=16 (LARGE N!)
    signals["Q233_k1_j8_p1"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q232 [triple]: K1_first_pb_after_bkt + J5_dry_prior_base_hi + P3_td_setup_6plus
    # VALIDATED 2021+: h3=77.8% h5=44.4% avg=+2.37%  N=9
    signals["Q232_k1_j5_p3"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q231 [triple]: D3_near_hi_ma50 + M1_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.71%  N=16 (LARGE N!)
    signals["Q231_d3_m1_fvg3"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q230 [triple]: I4b_3down_bounce + I10_top_decile_day + N1_pp_at_ma20
    # VALIDATED 2021+: h3=76.9% h5=69.2% avg=+1.63%  N=13
    signals["Q230_i4b_i10_n1"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("I10_top_decile_day", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q229 [triple]: I4b_3down_bounce + G2_nr7 + I10_top_decile_day
    # VALIDATED 2021+: h3=77.8% h5=66.7% avg=+2.13%  N=9
    signals["Q229_i4b_g2_i10"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("G2_nr7", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q228 [triple]: I4b_3down_bounce + E3_mild_pb + N1_pp_at_ma20
    # VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.35%  N=9
    signals["Q228_i4b_e3_n1"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q227 [triple]: I4b_3down_bounce + I10_top_decile_day + K1_first_pb_after_bkt
    # VALIDATED 2021+: h3=77.8% h5=66.7% avg=+1.05%  N=18 (LARGE N!)
    signals["Q227_i4b_i10_k1"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("I10_top_decile_day", False) and
        signals.get("K1_first_pb_after_bkt", False)
    )

    # Q226 [triple]: I4_2down_bounce + I4b_3down_bounce + N1_pp_at_ma20
    # VALIDATED 2021+: h3=75.0% h5=68.8% avg=+1.67%  N=16 (LARGE N!)
    signals["Q226_i4_i4b_n1"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q225 [triple]: I4_2down_bounce + I2_power_move + O1_in_fvg_5d
    # VALIDATED 2021+: h3=75.0% h5=66.7% avg=+2.27%  N=12
    signals["Q225_i4_i2_fvg5"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("I2_power_move", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q224 [triple]: I4_2down_bounce + B2_tight + X4_inside_near_hi
    # VALIDATED 2021+: h3=77.8% h5=66.7% avg=+1.21%  N=9
    signals["Q224_i4_b2_x4"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("B2_tight", False) and
        signals.get("X4_inside_near_hi", False)
    )

    # Q223 [triple]: D3_near_hi_ma50 + H1_mom_resume + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.9% h5=69.2% avg=+nan%  N=13
    signals["Q223_d3_h1_q1"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q222 [triple]: D3_near_hi_ma50 + M4_vdu_full_compound + O1_in_fvg_3d
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.97%  N=8
    signals["Q222_d3_m4_fvg3"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("M4_vdu_full_compound", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q858 [triple]: F2_dry_after_surge + I4b_3down_bounce + P3_td_setup_6plus
    # ★★★ VALIDATED 2021+: h3=75.0% h5=66.7% avg=+2.83%  N=12 (LARGE N!)
    signals["Q858_f2_i4b_p3"] = (
        signals.get("F2_dry_after_surge", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q857 [triple]: F2_dry_after_surge + I9_inside_close_hi + P3_td_setup_6plus (already Q524 — skip)

    # Q856 [triple]: F1_vol_dry + J3_dry_near_ma50 + J5b_dry_prior_hi_tight (dup of Q842 reversed — skip)
    # Q855: F1_vol_dry + J_combo_ma50_trend + J_combo_prior_hi (dup of Q841 reversed via F1 — new but very similar)

    # ── Q859–Q884: I2/I2b/I10/I3b/N1 backbone combos ──

    # Q859 [triple]: I2_power_move + E1_pb_ma20 + I2b_power_move_strong
    # ★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q859_i2_e1_i2b"] = (
        signals.get("I2_power_move", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q860 [triple]: I10_top_decile_day + X4_inside_near_hi + J5_dry_prior_base_hi
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+5.07%  N=8
    signals["Q860_i10_x4_j5"] = (
        signals.get("I10_top_decile_day", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q861 [triple]: I3b_gap_up_strong + C3_pocket_pivot + I5_cap_reversal
    # ★★★ VALIDATED 2021+: h3=88.9% h5=44.4% avg=+6.29%  N=9
    signals["Q861_i3b_c3_i5"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q862 [triple]: I3b_gap_up_strong + I5_cap_reversal + D4_pocket_ma50
    # ★★★ VALIDATED 2021+: h3=88.9% h5=44.4% avg=+6.29%  N=9
    signals["Q862_i3b_i5_d4"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q863 [triple]: I3b_gap_up_strong + I4b_3down_bounce + P1_td_setup_count
    # ★★ VALIDATED 2021+: h3=80.0% h5=90.0% avg=+7.51%  N=10
    signals["Q863_i3b_i4b_p1"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q864 [triple]: I2b_power_move_strong + E3_mild_pb + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=88.9% h5=77.8% avg=+3.68%  N=9
    signals["Q864_i2b_e3_n1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q865 [triple]: I2b_power_move_strong + A3_vol_surge + E1_pb_ma20
    # ★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q865_i2b_a3_e1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("E1_pb_ma20", False)
    )

    # Q866 [triple]: I2b_power_move_strong + I8_3up_days + I9_inside_close_hi
    # ★★ VALIDATED 2021+: h3=80.0% h5=70.0% avg=+2.45%  N=10
    signals["Q866_i2b_i8_i9"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("I8_3up_days", False) and
        signals.get("I9_inside_close_hi", False)
    )

    # Q867 [triple]: N1_pp_at_ma20 + E3_mild_pb + I2b_power_move_strong (same trio as Q864 — skip)

    # Q872 [triple]: P1_td_setup_count + A3_vol_surge + I4_2down_bounce
    # ★★★★ PERFECT 3d: h3=100.0% h5=88.9% avg=+6.40%  N=9
    signals["Q872_p1_a3_i4"] = (
        signals.get("P1_td_setup_count", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q873 [triple]: O1_in_fvg_3d + M3_vdu_expansion_pb + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.33%  N=12 (LARGE N!)
    signals["Q873_fvg3_m3_fvg5"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q874 [triple]: O1_in_fvg_3d + M3_vdu_expansion_pb + O1_in_fvg_8d
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.33%  N=12 (LARGE N!)
    signals["Q874_fvg3_m3_fvg8"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q875 [triple]: O1_in_fvg_5d + I9_inside_close_hi + J9_3dry_at_ma20
    # ★★★ VALIDATED 2021+: h3=88.9% h5=55.6% avg=+1.58%  N=9
    signals["Q875_fvg5_i9_j9"] = (
        signals.get("O1_in_fvg_5d", False) and
        signals.get("I9_inside_close_hi", False) and
        signals.get("J9_3dry_at_ma20", False)
    )

    # Q876 [triple]: O1_in_fvg_5d + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N!)
    signals["Q876_fvg5_e4_q1"] = (
        signals.get("O1_in_fvg_5d", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q877 [triple]: O1_in_fvg_5d + E1_pb_ma20 + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12 (LARGE N!)
    signals["Q877_fvg5_e1_n1"] = (
        signals.get("O1_in_fvg_5d", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q878 [triple]: O1_in_fvg_5d + E1_pb_ma20 + D4_pocket_ma50
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12 (LARGE N!)
    signals["Q878_fvg5_e1_d4"] = (
        signals.get("O1_in_fvg_5d", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q879 [triple]: O1_in_fvg_3d + M1_vdu_expansion + O1_in_fvg_5d
    # ★★ VALIDATED 2021+: h3=77.8% h5=61.1% avg=+1.99%  N=18 (LARGE N!)
    signals["Q879_fvg3_m1_fvg5"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q880 [triple]: N1_pp_at_ma20 + A3_vol_surge + E3_mild_pb
    # ★★ VALIDATED 2021+: h3=78.9% h5=68.4% avg=+2.71%  N=19 (VERY LARGE N!)
    signals["Q880_n1_a3_e3"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("E3_mild_pb", False)
    )

    # Q881 [triple]: I2b_power_move_strong + E1_pb_ma20 + I10_top_decile_day
    # ★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+3.18%  N=9
    signals["Q881_i2b_e1_i10"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q882 [triple]: I2b_power_move_strong + I3b_gap_up_strong + I5_cap_reversal
    # ★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+5.34%  N=13 (LARGE N!)
    signals["Q882_i2b_i3b_i5"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q883 [triple]: Q1_pp_ma20_fvg3d + E4_shallow_pb + O2_choch_10d
    # ★★★★ PERFECT BOTH: h3=88.9% h5=88.9% avg=+2.54%  N=9
    signals["Q883_q1_e4_o2"] = (
        signals.get("Q1_pp_ma20_fvg3d", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O2_choch_10d", False)
    )

    # Q884 [triple]: Q1_pp_ma20_fvg3d + E4_shallow_pb + O1_in_fvg_8d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N!)
    signals["Q884_q1_e4_fvg8"] = (
        signals.get("Q1_pp_ma20_fvg3d", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q885 [triple]: Q1_pp_ma20_fvg3d + B3_near_hi20 + E4_shallow_pb
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N!)
    signals["Q885_q1_b3_e4"] = (
        signals.get("Q1_pp_ma20_fvg3d", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("E4_shallow_pb", False)
    )

    # Q886 [triple]: Q3_inside_dry_td6 + F2_dry_after_surge + I4_2down_bounce
    # ★★★★ PERFECT BOTH: h3=88.9% h5=88.9% avg=+4.46%  N=9
    signals["Q886_q3_f2_i4"] = (
        signals.get("Q3_inside_dry_td6", False) and
        signals.get("F2_dry_after_surge", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q887 [triple]: C1_early_move + M5_post_bkt_vdu_expansion + O1_in_fvg_3d
    # ★★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.65%  N=11
    signals["Q887_c1_m5_fvg3"] = (
        signals.get("C1_early_move", False) and
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q888 [triple]: C2_above_ma50 + I4_2down_bounce + N5_pp_after_bkt_pb
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q888_c2_i4_n5"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q889 [triple]: C2_above_ma50 + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N!)
    signals["Q889_c2_e4_q1"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q890 [triple]: C2_above_ma50 + M5_post_bkt_vdu_expansion + O1_in_fvg_3d
    # ★★ VALIDATED 2021+: h3=81.2% h5=62.5% avg=+1.97%  N=16 (LARGE N!)
    signals["Q890_c2_m5_fvg3"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q891 [triple]: C3_pocket_pivot + I4_2down_bounce + K1_first_pb_after_bkt
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q891_c3_i4_k1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("K1_first_pb_after_bkt", False)
    )

    # Q892 [triple]: C3_pocket_pivot + I4_2down_bounce + N5_pp_after_bkt_pb
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q892_c3_i4_n5"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q893 [triple]: C3_pocket_pivot + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N!)
    signals["Q893_c3_e4_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q894 [triple]: C3_pocket_pivot + E1_pb_ma20 + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12 (LARGE N!)
    signals["Q894_c3_e1_fvg5"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q902 [triple]: B3_near_hi20 + O2_choch_10d + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=83.3% h5=75.0% avg=+nan%  N=12 (LARGE N!)
    signals["Q902_b3_o2_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("O2_choch_10d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q903 [triple]: B3_near_hi20 + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # ★★ VALIDATED 2021+: h3=81.2% h5=68.8% avg=+nan%  N=16 (VERY LARGE N!)
    signals["Q903_b3_n1_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q904 [triple]: B3_near_hi20 + O1_in_fvg_3d + Q1_pp_ma20_fvg3d
    # ★★ VALIDATED 2021+: h3=81.2% h5=68.8% avg=+nan%  N=16 (VERY LARGE N!)
    signals["Q904_b3_fvg3_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q905 [triple]: X4_inside_near_hi + I10_top_decile_day + J_combo_prior_hi
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+5.07%  N=8
    signals["Q905_x4_i10_jhi"] = (
        signals.get("X4_inside_near_hi", False) and
        signals.get("I10_top_decile_day", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q906 [triple]: X7_pb_dry_ma50 + J5_dry_prior_base_hi + J8_pb_zone_ma50
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q906_x7_j5_j8"] = (
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q907 [triple]: X7_pb_dry_ma50 + J8_pb_zone_ma50 + J_combo_prior_hi
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q907_x7_j8_jhi"] = (
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q908 [triple]: K4_first_pb_to_ma20 + X7_pb_dry_ma50 + Q2_pb_inside_td6
    # ★★ VALIDATED 2021+: h3=80.0% h5=60.0% avg=+2.37%  N=15 (LARGE N!)
    signals["Q908_k4_x7_q2"] = (
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("Q2_pb_inside_td6", False)
    )

    # Q909 [triple]: K4_first_pb_to_ma20 + X7_pb_dry_ma50 + Q3_inside_dry_td6
    # ★★ VALIDATED 2021+: h3=80.0% h5=60.0% avg=+2.37%  N=15 (LARGE N!)
    signals["Q909_k4_x7_q3"] = (
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("Q3_inside_dry_td6", False)
    )

    # Q910 [triple]: J_combo_prior_hi + F1_vol_dry + J8_pb_zone_ma50
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q910_jhi_f1_j8"] = (
        signals.get("J_combo_prior_hi", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q911 [triple]: J_combo_prior_hi + F5_dry_ma50 + J8_pb_zone_ma50
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q911_jhi_f5_j8"] = (
        signals.get("J_combo_prior_hi", False) and
        signals.get("F5_dry_ma50", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q912 [triple]: J_combo_ma50_trend + J9_3dry_at_ma20 + O1_in_fvg_8d
    # ★★ VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.09%  N=11
    signals["Q912_jma50_j9_fvg8"] = (
        signals.get("J_combo_ma50_trend", False) and
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q913 [triple]: J8_pb_zone_ma50 + F1_vol_dry + J5_dry_prior_base_hi
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q913_j8_f1_j5"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q914 [triple]: J8_pb_zone_ma50 + F5_dry_ma50 + J5_dry_prior_base_hi
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q914_j8_f5_j5"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("F5_dry_ma50", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q915 [triple]: K1_first_pb_after_bkt + I4_2down_bounce + N5_pp_after_bkt_pb
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q915_k1_i4_n5"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q916 [triple]: A3_vol_surge + I4_2down_bounce + O1_in_fvg_8d
    # ★★★★ PERFECT 3d: h3=100.0% h5=87.5% avg=+5.00%  N=8
    signals["Q916_a3_i4_fvg8"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q917 [triple]: A3_vol_surge + E1_pb_ma20 + O1_in_fvg_8d
    # ★★ VALIDATED 2021+: h3=80.0% h5=73.3% avg=+1.38%  N=15 (LARGE N!)
    signals["Q917_a3_e1_fvg8"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q918 [triple]: A3_vol_surge + X4_inside_near_hi + O1_in_fvg_3d
    # ★★ VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.93%  N=10
    signals["Q918_a3_x4_fvg3"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q919 [triple]: M5_post_bkt_vdu_expansion + M1_vdu_expansion + O1_in_fvg_3d
    # ★★ VALIDATED 2021+: h3=81.2% h5=62.5% avg=+1.97%  N=16 (VERY LARGE N!)
    signals["Q919_m5_m1_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q920 [triple]: M5_post_bkt_vdu_expansion + O1_in_fvg_3d + O1_in_fvg_5d
    # ★★ VALIDATED 2021+: h3=81.2% h5=62.5% avg=+1.97%  N=16 (VERY LARGE N!)
    signals["Q920_m5_fvg3_fvg5"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q921 [triple]: M2_vdu_deep_expansion + O1_in_fvg_3d + O1_in_fvg_5d
    # ★★ VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.74%  N=11
    signals["Q921_m2_fvg3_fvg5"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q922 [triple]: M2_vdu_deep_expansion + M1_vdu_expansion + O1_in_fvg_3d
    # ★★ VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.74%  N=11
    signals["Q922_m2_m1_fvg3"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q923 [triple]: M1_vdu_expansion + M3_vdu_expansion_pb + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.33%  N=12 (LARGE N!)
    signals["Q923_m1_m3_fvg3"] = (
        signals.get("M1_vdu_expansion", False) and
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q924 [triple]: M3_vdu_expansion_pb + C2_above_ma50 + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.33%  N=12 (LARGE N!)
    signals["Q924_m3_c2_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q925 [triple]: M3_vdu_expansion_pb + H1_mom_resume + O1_in_fvg_3d
    # ★★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.49%  N=11
    signals["Q925_m3_h1_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q926 [triple]: H1_mom_resume + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.58%  N=11
    signals["Q926_h1_e4_q1"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q927 [triple]: H1_mom_resume + M5_post_bkt_vdu_expansion + O1_in_fvg_3d
    # ★★ VALIDATED 2021+: h3=80.0% h5=66.7% avg=+2.06%  N=15 (LARGE N!)
    signals["Q927_h1_m5_fvg3"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q928 [triple]: C3_pocket_pivot + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # ★★ VALIDATED 2021+: h3=77.3% h5=63.6% avg=+nan%  N=22 (VERY LARGE N!)
    signals["Q928_c3_n1_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q929 [triple]: C3_pocket_pivot + O1_in_fvg_3d + Q1_pp_ma20_fvg3d
    # ★★ VALIDATED 2021+: h3=77.3% h5=63.6% avg=+nan%  N=22 (VERY LARGE N!)
    signals["Q929_c3_fvg3_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q930 [triple]: C2_above_ma50 + C3_pocket_pivot + Q1_pp_ma20_fvg3d
    # ★★ VALIDATED 2021+: h3=76.2% h5=66.7% avg=+nan%  N=21 (VERY LARGE N!)
    signals["Q930_c2_c3_q1"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q931 [triple]: C2_above_ma50 + N1_pp_at_ma20 + O1_in_fvg_3d
    # ★★ VALIDATED 2021+: h3=76.2% h5=66.7% avg=+nan%  N=21 (VERY LARGE N!)
    signals["Q931_c2_n1_fvg3"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q932 [triple]: D4_pocket_ma50 + N1_pp_at_ma20 + O1_in_fvg_3d
    # ★★ VALIDATED 2021+: h3=76.2% h5=66.7% avg=+nan%  N=21 (VERY LARGE N!)
    signals["Q932_d4_n1_fvg3"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q933 [triple]: C2_above_ma50 + M1_vdu_expansion + O1_in_fvg_3d
    # ★★ VALIDATED 2021+: h3=77.8% h5=61.1% avg=+1.99%  N=18 (LARGE N!)
    signals["Q933_c2_m1_fvg3"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q934 [triple]: M1_vdu_expansion + O1_in_fvg_3d + O1_in_fvg_5d
    # ★★ VALIDATED 2021+: h3=77.8% h5=61.1% avg=+1.99%  N=18 (LARGE N!)
    signals["Q934_m1_fvg3_fvg5"] = (
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q935 [triple]: M1_vdu_expansion + O1_in_fvg_3d + O1_in_fvg_8d
    # ★★ VALIDATED 2021+: h3=77.8% h5=61.1% avg=+1.99%  N=18 (LARGE N!)
    signals["Q935_m1_fvg3_fvg8"] = (
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q936 [triple]: B3_near_hi20 + N1_pp_at_ma20 + O1_in_fvg_5d
    # ★★ VALIDATED 2021+: h3=75.0% h5=70.0% avg=+nan%  N=20 (VERY LARGE N!)
    signals["Q936_b3_n1_fvg5"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q937 [triple]: M3_vdu_expansion_pb + M2_vdu_deep_expansion + O1_in_fvg_3d
    # ★★ VALIDATED 2021+: h3=77.8% h5=55.6% avg=+1.80%  N=9
    signals["Q937_m3_m2_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q938 [triple]: E1_pb_ma20 + C3_pocket_pivot + I2b_power_move_strong
    # ★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q938_e1_c3_i2b"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q939 [triple]: E1_pb_ma20 + I2b_power_move_strong + N1_pp_at_ma20
    # ★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q939_e1_i2b_n1"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q940 [triple]: E1_pb_ma20 + J5_dry_prior_base_hi + P3_td_setup_6plus
    # ★★ VALIDATED 2021+: h3=76.5% h5=47.1% avg=+2.21%  N=17 (LARGE N!)
    signals["Q940_e1_j5_p3"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q941 [triple]: E3_mild_pb + I4b_3down_bounce + N1_pp_at_ma20
    # ★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.35%  N=9
    signals["Q941_e3_i4b_n1"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q942 [triple]: E3_mild_pb + N1_pp_at_ma20 + O1_in_fvg_3d
    # ★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9
    signals["Q942_e3_n1_fvg3"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q943 [triple]: E4_shallow_pb + N1_pp_at_ma20 + O1_in_fvg_5d
    # ★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+2.06%  N=16 (LARGE N!)
    signals["Q943_e4_n1_fvg5"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q944 [triple]: I4_2down_bounce + I4b_3down_bounce + N1_pp_at_ma20
    # ★★ VALIDATED 2021+: h3=75.0% h5=68.8% avg=+1.67%  N=16 (LARGE N!)
    signals["Q944_i4_i4b_n1"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q945 [triple]: N1_pp_at_ma20 + C3_pocket_pivot + O1_in_fvg_3d
    # ★★ VALIDATED 2021+: h3=77.3% h5=63.6% avg=+nan%  N=22 (VERY LARGE N!)
    signals["Q945_n1_c3_fvg3"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q946 [triple]: A3_vol_surge + I3b_gap_up_strong + I5_cap_reversal
    # ★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+5.34%  N=13 (LARGE N!)
    signals["Q946_a3_i3b_i5"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q947 [triple]: I4_2down_bounce + B2_tight + X4_inside_near_hi
    # ★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+1.21%  N=9
    signals["Q947_i4_b2_x4"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("B2_tight", False) and
        signals.get("X4_inside_near_hi", False)
    )

    # Q948 [triple]: D4_pocket_ma50 + E3_mild_pb + Q1_pp_ma20_fvg3d
    # ★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9
    signals["Q948_d4_e3_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q949 [triple]: D4_pocket_ma50 + C2_above_ma50 + Q1_pp_ma20_fvg3d
    # ★★ VALIDATED 2021+: h3=76.2% h5=66.7% avg=+nan%  N=21 (VERY LARGE N!)
    signals["Q949_d4_c2_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q950 [triple]: D4_pocket_ma50 + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # ★★ VALIDATED 2021+: h3=76.2% h5=66.7% avg=+nan%  N=21 (VERY LARGE N!)
    signals["Q950_d4_n1_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q951 [triple]: C1_early_move + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.07%  N=9
    signals["Q951_c1_e4_q1"] = (
        signals.get("C1_early_move", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q895 [triple]: E3_mild_pb + I4_2down_bounce + N5_pp_after_bkt_pb
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q895_e3_i4_n5"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q896 [triple]: D3_near_hi_ma50 + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N!)
    signals["Q896_d3_e4_q1"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q897 [triple]: D3_near_hi_ma50 + O2_choch_10d + Q1_pp_ma20_fvg3d
    # ★★ VALIDATED 2021+: h3=81.8% h5=81.8% avg=+nan%  N=11
    signals["Q897_d3_o2_q1"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("O2_choch_10d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q898 [triple]: D4_pocket_ma50 + I4_2down_bounce + K1_first_pb_after_bkt
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q898_d4_i4_k1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("K1_first_pb_after_bkt", False)
    )

    # Q899 [triple]: D4_pocket_ma50 + I4_2down_bounce + N5_pp_after_bkt_pb
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q899_d4_i4_n5"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q900 [triple]: D4_pocket_ma50 + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N!)
    signals["Q900_d4_e4_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q901 [triple]: D5_full_setup + E3_mild_pb + O1_in_fvg_5d
    # ★★ VALIDATED 2021+: h3=80.0% h5=70.0% avg=+1.89%  N=10
    signals["Q901_d5_e3_fvg5"] = (
        signals.get("D5_full_setup", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q868 [triple]: N1_pp_at_ma20 + A2_bkt10 + A3_vol_surge
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.47%  N=8
    signals["Q868_n1_a2_a3"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("A2_bkt10", False) and
        signals.get("A3_vol_surge", False)
    )

    # Q869 [triple]: N1_pp_at_ma20 + A3_vol_surge + I4_2down_bounce
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+3.39%  N=8
    signals["Q869_n1_a3_i4"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q870 [triple]: N1_pp_at_ma20 + E4_shallow_pb + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N!)
    signals["Q870_n1_e4_fvg3"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q871 [triple]: N1_pp_at_ma20 + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N!)
    signals["Q871_n1_e4_q1"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q854 [triple]: E1_pb_ma20 + C1_early_move + I2b_power_move_strong
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.65%  N=8
    signals["Q854_e1_c1_i2b"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("C1_early_move", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q853 [triple]: E1_pb_ma20 + I2b_power_move_strong + I10_top_decile_day
    # ★★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+3.18%  N=9 (HIGH avg!)
    signals["Q853_e1_i2b_i10"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q852 [triple]: E1_pb_ma20 + A3_vol_surge + I10_top_decile_day
    # ★★★ VALIDATED 2021+: h3=76.9% h5=76.9% avg=+2.44%  N=13 (LARGE N! BOTH equal!)
    signals["Q852_e1_a3_i10"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q851 [triple]: F1_vol_dry + K4_first_pb_to_ma20 + Q2_pb_inside_td6 (already Q542-ish — check)
    # Q542: K4+F1+Q2 already exists — skip

    # Q850 [triple]: F1_vol_dry + J3_dry_near_ma50 + J_combo_prior_hi (dup of Q839 reversed — skip)

    # Q849 [triple]: E1_pb_ma20 + A3_vol_surge + O1_in_fvg_8d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=73.3% avg=+1.38%  N=15 (VERY LARGE N! BOTH high!)
    signals["Q849_e1_a3_fvg8"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q848 [triple]: E1_pb_ma20 + I2b_power_move_strong + N1_pp_at_ma20
    # ★★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11 (LARGE N!)
    signals["Q848_e1_i2b_n1"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q847 [triple]: E1_pb_ma20 + A3_vol_surge + I2b_power_move_strong
    # ★★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11 (LARGE N!)
    signals["Q847_e1_a3_i2b"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q846 [triple]: E1_pb_ma20 + O1_in_fvg_5d + D4_pocket_ma50
    # ★★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12 (LARGE N!)
    signals["Q846_e1_fvg5_d4"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q845 [triple]: E1_pb_ma20 + N1_pp_at_ma20 + O1_in_fvg_5d
    # ★★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12 (LARGE N!)
    signals["Q845_e1_n1_fvg5"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q844 [triple]: E1_pb_ma20 + C3_pocket_pivot + O1_in_fvg_5d (already Q718 — skip)

    # Q843 [triple]: E1_pb_ma20 + I2_power_move + I2b_power_move_strong (already Q752 — skip)

    # Q842 [triple]: F5_dry_ma50 + J3_dry_near_ma50 + J5b_dry_prior_hi_tight
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+1.32%  N=8 (BOTH equal!)
    signals["Q842_f5_j3_j5b"] = (
        signals.get("F5_dry_ma50", False) and
        signals.get("J3_dry_near_ma50", False) and
        signals.get("J5b_dry_prior_hi_tight", False)
    )

    # Q841 [triple]: F5_dry_ma50 + J_combo_ma50_trend + J_combo_prior_hi
    # ★★★ VALIDATED 2021+: h3=75.0% h5=68.8% avg=+2.11%  N=16 (VERY LARGE N!)
    signals["Q841_f5_jma50_jcombo"] = (
        signals.get("F5_dry_ma50", False) and
        signals.get("J_combo_ma50_trend", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q840 [triple]: F5_dry_ma50 + J5_dry_prior_base_hi + J_combo_ma50_trend
    # ★★★ VALIDATED 2021+: h3=75.0% h5=68.8% avg=+2.11%  N=16 (VERY LARGE N!)
    signals["Q840_f5_j5_jma50"] = (
        signals.get("F5_dry_ma50", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("J_combo_ma50_trend", False)
    )

    # Q839 [triple]: F5_dry_ma50 + J3_dry_near_ma50 + J_combo_prior_hi
    # ★★★ VALIDATED 2021+: h3=75.0% h5=68.8% avg=+2.11%  N=16 (VERY LARGE N!)
    signals["Q839_f5_j3_jcombo"] = (
        signals.get("F5_dry_ma50", False) and
        signals.get("J3_dry_near_ma50", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q838 [triple]: F5_dry_ma50 + J3_dry_near_ma50 + J5_dry_prior_base_hi
    # ★★★ VALIDATED 2021+: h3=75.0% h5=68.8% avg=+2.11%  N=16 (VERY LARGE N!)
    signals["Q838_f5_j3_j5"] = (
        signals.get("F5_dry_ma50", False) and
        signals.get("J3_dry_near_ma50", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q837 [triple]: F5_dry_ma50 + K4_first_pb_to_ma20 + Q2_pb_inside_td6
    # ★★★★ VALIDATED 2021+: h3=76.7% h5=70.0% avg=+3.17%  N=30 (VERY LARGE N! BOTH high!)
    signals["Q837_f5_k4_q2"] = (
        signals.get("F5_dry_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("Q2_pb_inside_td6", False)
    )

    # Q836 [triple]: J5_dry_prior_base_hi + E3_mild_pb + I4b_3down_bounce
    # ★★★ VALIDATED 2021+: h3=75.0% h5=56.2% avg=+1.63%  N=16 (VERY LARGE N!)
    signals["Q836_j5_e3_i4b"] = (
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("I4b_3down_bounce", False)
    )

    # Q835 [triple]: Q1_pp_ma20_fvg3d + E4_shallow_pb + O1_in_fvg_8d
    # ★★★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (already captured via other backbones — skip)

    # Q834: Q1 + E4 combos already captured in Q691-Q698 series — skip

    # Q833 [triple]: B2_tight + X4_inside_near_hi + I4_2down_bounce
    # ★★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+1.21%  N=9
    signals["Q833_b2_x4_i4"] = (
        signals.get("B2_tight", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q832 [triple]: B2_tight + E2_pb_ma50 + O1_in_fvg_8d (dup of Q829 reversed — skip)

    # Q831 [triple]: B2_tight + E2_pb_ma50 + K4_first_pb_to_ma20
    # ★★★ VALIDATED 2021+: h3=75.0% h5=50.0% avg=+0.30%  N=8
    signals["Q831_b2_e2_k4"] = (
        signals.get("B2_tight", False) and
        signals.get("E2_pb_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False)
    )

    # Q830 [triple]: I3_gap_up_hold + X4_inside_near_hi + J5b_dry_prior_hi_tight
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+1.33%  N=8 (BOTH equal!)
    signals["Q830_i3_x4_j5b"] = (
        signals.get("I3_gap_up_hold", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("J5b_dry_prior_hi_tight", False)
    )

    # Q829 [triple]: E2_pb_ma50 + B2_tight + O1_in_fvg_8d
    # ★★★★ VALIDATED 2021+: h3=77.8% h5=88.9% avg=+0.33%  N=9 (h5=88.9%! EXTRAORDINARY 5d!)
    signals["Q829_e2_b2_fvg8"] = (
        signals.get("E2_pb_ma50", False) and
        signals.get("B2_tight", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q828 [triple]: F3_3day_dry + K1_first_pb_after_bkt + P6_td_countdown_8plus
    # ★★★ VALIDATED 2021+: h3=75.0% h5=87.5% avg=+3.85%  N=8 (h5=87.5%! HIGH avg!)
    signals["Q828_f3_k1_p6"] = (
        signals.get("F3_3day_dry", False) and
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q827 [triple]: F3_3day_dry + A2_bkt10 + I8_3up_days (dup of Q817 variation — new)
    # ★★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+4.63%  N=9 (HIGH avg!)
    signals["Q827_f3_a2_i8"] = (
        signals.get("F3_3day_dry", False) and
        signals.get("A2_bkt10", False) and
        signals.get("I8_3up_days", False)
    )

    # Q826 [triple]: I8_3up_days + A2_bkt10 + F3_3day_dry (same as Q827 — skip)

    # Q825 [triple]: X8_3dry_near_hi + A2_bkt10 + I8_3up_days
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+4.31%  N=8 (HIGH avg!)
    signals["Q825_x8_a2_i8"] = (
        signals.get("X8_3dry_near_hi", False) and
        signals.get("A2_bkt10", False) and
        signals.get("I8_3up_days", False)
    )

    # Q824 [triple]: I6_nr7_close_hi + I4b_3down_bounce + I10_top_decile_day
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+2.18%  N=8
    signals["Q824_i6_i4b_i10"] = (
        signals.get("I6_nr7_close_hi", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q823 [triple]: I6_nr7_close_hi + J9_3dry_at_ma20 + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=66.7% avg=+1.50%  N=12 (LARGE N!)
    signals["Q823_i6_j9_fvg5"] = (
        signals.get("I6_nr7_close_hi", False) and
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q822 [triple]: I6_nr7_close_hi + K4_first_pb_to_ma20 + P3_td_setup_6plus (already counted in Q649 area — skip)

    # Q821 [triple]: J4_dry_value_area + J_combo_ma50_trend + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+2.60%  N=8
    signals["Q821_j4_jma50_fvg5"] = (
        signals.get("J4_dry_value_area", False) and
        signals.get("J_combo_ma50_trend", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q820 [triple]: J4_dry_value_area + J3_dry_near_ma50 + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+2.60%  N=8
    signals["Q820_j4_j3_fvg5"] = (
        signals.get("J4_dry_value_area", False) and
        signals.get("J3_dry_near_ma50", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q819 [triple]: I8_3up_days + X4_inside_near_hi + I2b_power_move_strong
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.01%  N=8
    signals["Q819_i8_x4_i2b"] = (
        signals.get("I8_3up_days", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q818 [triple]: I8_3up_days + H2_new10d_hi + X8_3dry_near_hi
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+4.31%  N=8 (HIGH avg!)
    signals["Q818_i8_h2_x8"] = (
        signals.get("I8_3up_days", False) and
        signals.get("H2_new10d_hi", False) and
        signals.get("X8_3dry_near_hi", False)
    )

    # Q817 [triple]: I8_3up_days + F3_3day_dry + H2_new10d_hi
    # ★★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+4.63%  N=9 (HIGH avg!)
    signals["Q817_i8_f3_h2"] = (
        signals.get("I8_3up_days", False) and
        signals.get("F3_3day_dry", False) and
        signals.get("H2_new10d_hi", False)
    )

    # Q816 [triple]: I8_3up_days + E2_pb_ma50 + I3_gap_up_hold
    # ★★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+1.47%  N=9
    signals["Q816_i8_e2_i3"] = (
        signals.get("I8_3up_days", False) and
        signals.get("E2_pb_ma50", False) and
        signals.get("I3_gap_up_hold", False)
    )

    # Q815 [triple]: J9_3dry_at_ma20 + J_combo_ma50_trend + O1_in_fvg_8d
    # ★★★★ VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.09%  N=11 (LARGE N!)
    signals["Q815_j9_jma50_fvg8"] = (
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("J_combo_ma50_trend", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q814 [triple]: J9_3dry_at_ma20 + I9_inside_close_hi + O1_in_fvg_5d (already Q804 reversed — skip)

    # Q813 [triple]: I9_inside_close_hi + I8_3up_days + O1_in_fvg_8d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+3.86%  N=8 (BOTH equal! HIGH avg!)
    signals["Q813_i9_i8_fvg8"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("I8_3up_days", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q812 [triple]: I9_inside_close_hi + I8_3up_days + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+3.86%  N=8 (BOTH equal! HIGH avg!)
    signals["Q812_i9_i8_fvg5"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("I8_3up_days", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q811 [triple]: I9_inside_close_hi + J4_dry_value_area + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=58.3% avg=+2.63%  N=12 (LARGE N!)
    signals["Q811_i9_j4_fvg5"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q810 [triple]: I9_inside_close_hi + F2_dry_after_surge + P3_td_setup_6plus (already Q524 — skip)

    # Q809 [triple]: I1b_close_quality_hi_vol + I3b_gap_up_strong + O1_in_fvg_8d
    # ★★★★★ EXTRAORDINARY: h3=75.0% h5=87.5% avg=+12.07%  N=8 (h5=87.5%! avg=+12%! UNREAL!)
    signals["Q809_i1b_i3b_fvg8"] = (
        signals.get("I1b_close_quality_hi_vol", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q808 [triple]: I1b_close_quality_hi_vol + I3b_gap_up_strong + I5_cap_reversal
    # ★★★ VALIDATED 2021+: h3=75.0% h5=50.0% avg=+7.02%  N=8 (HIGH avg!)
    signals["Q808_i1b_i3b_i5"] = (
        signals.get("I1b_close_quality_hi_vol", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q807 [triple]: I1b_close_quality_hi_vol + E1_pb_ma20 + I2b_power_move_strong
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+3.24%  N=8
    signals["Q807_i1b_e1_i2b"] = (
        signals.get("I1b_close_quality_hi_vol", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q806 [triple]: D2_bkt_ma50 + I3b_gap_up_strong + P6_td_countdown_8plus (dup of Q803 reversed — skip)

    # Q805 [triple]: I9_inside_close_hi + I2b_power_move_strong + I8_3up_days
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=70.0% avg=+2.45%  N=10 (BOTH high!)
    signals["Q805_i9_i2b_i8"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("I2b_power_move_strong", False) and
        signals.get("I8_3up_days", False)
    )

    # Q804 [triple]: I9_inside_close_hi + J9_3dry_at_ma20 + O1_in_fvg_5d
    # ★★★★★ VALIDATED 2021+: h3=88.9% h5=55.6% avg=+1.58%  N=9
    signals["Q804_i9_j9_fvg5"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q803 [triple]: P6_td_countdown_8plus + I3b_gap_up_strong + D2_bkt_ma50
    # ★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+4.54%  N=13 (LARGE N! HIGH avg!)
    signals["Q803_p6_i3b_d2"] = (
        signals.get("P6_td_countdown_8plus", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("D2_bkt_ma50", False)
    )

    # Q802 [triple]: P6_td_countdown_8plus + I1b_close_quality_hi_vol + M2_vdu_deep_expansion
    # ★★★ VALIDATED 2021+: h3=77.8% h5=44.4% avg=+2.11%  N=9
    signals["Q802_p6_i1b_m2"] = (
        signals.get("P6_td_countdown_8plus", False) and
        signals.get("I1b_close_quality_hi_vol", False) and
        signals.get("M2_vdu_deep_expansion", False)
    )

    # Q801 [triple]: I1_close_quality + I3b_gap_up_strong + I5_cap_reversal
    # ★★★ VALIDATED 2021+: h3=75.0% h5=50.0% avg=+7.02%  N=8 (HIGH avg!)
    signals["Q801_i1_i3b_i5"] = (
        signals.get("I1_close_quality", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q800 [triple]: I1_close_quality + E1_pb_ma20 + I4b_3down_bounce
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+4.68%  N=8 (BOTH equal! HIGH avg!)
    signals["Q800_i1_e1_i4b"] = (
        signals.get("I1_close_quality", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I4b_3down_bounce", False)
    )

    # Q799 [triple]: I1_close_quality + E1_pb_ma20 + I2b_power_move_strong
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+3.24%  N=8 (LARGE avg!)
    signals["Q799_i1_e1_i2b"] = (
        signals.get("I1_close_quality", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q798 [triple]: I1_close_quality + I3b_gap_up_strong + P1_td_setup_count
    # ★★★ VALIDATED 2021+: h3=75.0% h5=83.3% avg=+6.24%  N=12 (h5=83.3%! LARGE N! HIGH avg!)
    signals["Q798_i1_i3b_p1"] = (
        signals.get("I1_close_quality", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q797 [triple]: I1_close_quality + I3b_gap_up_strong + O1_in_fvg_8d (already Q786 — skip)

    # Q796 [triple]: P6_td_countdown_8plus + I3b_gap_up_strong combos (already captured)

    # Q795 [triple]: A1_bkt20_vol + I3b_gap_up_strong + P6_td_countdown_8plus (dup of Q787 — skip)

    # Q794 [triple]: H2_new10d_hi + A3_vol_surge + N1_pp_at_ma20
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.47%  N=8
    signals["Q794_h2_a3_n1"] = (
        signals.get("H2_new10d_hi", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q793 [triple]: A2_bkt10 + A3_vol_surge + N1_pp_at_ma20
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.47%  N=8
    signals["Q793_a2_a3_n1"] = (
        signals.get("A2_bkt10", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q792 [triple]: A3_vol_surge + E1_pb_ma20 + I10_top_decile_day
    # ★★★ VALIDATED 2021+: h3=76.9% h5=76.9% avg=+2.44%  N=13 (LARGE N! BOTH equal!)
    signals["Q792_a3_e1_i10"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q791 [triple]: X5_nr7_ma50 + J_combo_prior_hi + P3_td_setup_6plus
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+3.71%  N=8 (BOTH equal! HIGH avg!)
    signals["Q791_x5_jcombo_p3"] = (
        signals.get("X5_nr7_ma50", False) and
        signals.get("J_combo_prior_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q790 [triple]: X5_nr7_ma50 + J5_dry_prior_base_hi + P3_td_setup_6plus
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+3.71%  N=8 (BOTH equal! HIGH avg!)
    signals["Q790_x5_j5_p3"] = (
        signals.get("X5_nr7_ma50", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q789 [triple]: I3b_gap_up_strong + H2_new10d_hi + P6_td_countdown_8plus
    # ★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+4.54%  N=13 (LARGE N! HIGH avg!)
    signals["Q789_i3b_h2_p6"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("H2_new10d_hi", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q788 [triple]: I3b_gap_up_strong + A2_bkt10 + P6_td_countdown_8plus
    # ★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+4.54%  N=13 (LARGE N! HIGH avg!)
    signals["Q788_i3b_a2_p6"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("A2_bkt10", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q787 [triple]: I3b_gap_up_strong + A1_bkt20_vol + P6_td_countdown_8plus
    # ★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+4.54%  N=13 (LARGE N! HIGH avg!)
    signals["Q787_i3b_a1_p6"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("A1_bkt20_vol", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q786 [triple]: I3b_gap_up_strong + I1_close_quality + O1_in_fvg_8d
    # ★★★★★ EXTRAORDINARY: h3=77.8% h5=88.9% avg=+12.95%  N=9 (h5=88.9%! avg=+12.95%! UNREAL!)
    signals["Q786_i3b_i1_fvg8"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I1_close_quality", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q785 [triple]: X5_nr7_ma50 + I5_cap_reversal + P1_td_setup_count
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+3.46%  N=8 (HIGH avg!)
    signals["Q785_x5_i5_p1"] = (
        signals.get("X5_nr7_ma50", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q784 [triple]: G2_nr7 + X2_mild_pb_dry + O2_choch_10d (dup of Q774 reversed — skip)
    # Q783: G2_nr7 + X2 + O3 (dup of Q775 reversed — skip)

    # Q782 [triple]: I5_cap_reversal + C1_early_move + I3b_gap_up_strong
    # ★★★ VALIDATED 2021+: h3=75.0% h5=87.5% avg=+3.30%  N=8 (h5=87.5%! EXTRAORDINARY 5d!)
    signals["Q782_i5_c1_i3b"] = (
        signals.get("I5_cap_reversal", False) and
        signals.get("C1_early_move", False) and
        signals.get("I3b_gap_up_strong", False)
    )

    # Q781 [triple]: I5_cap_reversal + I3b_gap_up_strong + I10_top_decile_day
    # ★★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+5.94%  N=16 (VERY LARGE N! VERY HIGH avg!)
    signals["Q781_i5_i3b_i10"] = (
        signals.get("I5_cap_reversal", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q780 [triple]: I5_cap_reversal + I3b_gap_up_strong + O2_choch_10d (already Q399 — skip)

    # Q779 [triple]: I5_cap_reversal + I2b_power_move_strong + I3b_gap_up_strong
    # ★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+5.34%  N=13 (LARGE N! HIGH avg!)
    signals["Q779_i5_i2b_i3b"] = (
        signals.get("I5_cap_reversal", False) and
        signals.get("I2b_power_move_strong", False) and
        signals.get("I3b_gap_up_strong", False)
    )

    # Q778 [triple]: I5_cap_reversal + A3_vol_surge + I3b_gap_up_strong
    # ★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+5.34%  N=13 (LARGE N! HIGH avg!)
    signals["Q778_i5_a3_i3b"] = (
        signals.get("I5_cap_reversal", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("I3b_gap_up_strong", False)
    )

    # Q777 [triple]: X2_mild_pb_dry + X5_nr7_ma50 + O3_choch_dry
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+4.23%  N=8 (HIGH avg!)
    signals["Q777_x2_x5_o3"] = (
        signals.get("X2_mild_pb_dry", False) and
        signals.get("X5_nr7_ma50", False) and
        signals.get("O3_choch_dry", False)
    )

    # Q776 [triple]: X2_mild_pb_dry + X5_nr7_ma50 + O2_choch_10d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+4.23%  N=8 (HIGH avg!)
    signals["Q776_x2_x5_o2"] = (
        signals.get("X2_mild_pb_dry", False) and
        signals.get("X5_nr7_ma50", False) and
        signals.get("O2_choch_10d", False)
    )

    # Q775 [triple]: X2_mild_pb_dry + G2_nr7 + O3_choch_dry
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+4.23%  N=8 (HIGH avg!)
    signals["Q775_x2_g2_o3"] = (
        signals.get("X2_mild_pb_dry", False) and
        signals.get("G2_nr7", False) and
        signals.get("O3_choch_dry", False)
    )

    # Q774 [triple]: X2_mild_pb_dry + G2_nr7 + O2_choch_10d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+4.23%  N=8 (HIGH avg!)
    signals["Q774_x2_g2_o2"] = (
        signals.get("X2_mild_pb_dry", False) and
        signals.get("G2_nr7", False) and
        signals.get("O2_choch_10d", False)
    )

    # Q773 [triple]: G2_nr7 + J_combo_prior_hi + P3_td_setup_6plus
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+3.71%  N=8 (BOTH equal! HIGH avg!)
    signals["Q773_g2_jcombo_p3"] = (
        signals.get("G2_nr7", False) and
        signals.get("J_combo_prior_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q772 [triple]: G2_nr7 + J5_dry_prior_base_hi + P3_td_setup_6plus
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+3.71%  N=8 (BOTH equal! HIGH avg!)
    signals["Q772_g2_j5_p3"] = (
        signals.get("G2_nr7", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q771 [triple]: G2_nr7 + I5_cap_reversal + P1_td_setup_count
    # ★★★ VALIDATED 2021+: h3=77.8% h5=55.6% avg=+3.13%  N=9 (HIGH avg!)
    signals["Q771_g2_i5_p1"] = (
        signals.get("G2_nr7", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q770 [triple]: G2_nr7 + I4b_3down_bounce + I10_top_decile_day
    # ★★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+2.13%  N=9
    signals["Q770_g2_i4b_i10"] = (
        signals.get("G2_nr7", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q769 [triple]: G1_inside_bar + I4_2down_bounce + D5_full_setup
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.26%  N=8
    signals["Q769_g1_i4_d5"] = (
        signals.get("G1_inside_bar", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("D5_full_setup", False)
    )

    # Q768 [triple]: D5_full_setup + G1_inside_bar + I4_2down_bounce (same as Q769 — skip)

    # Q767 [triple]: D5_full_setup + K1_first_pb_after_bkt + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+1.48%  N=8 (BOTH h3/h5 equal!)
    signals["Q767_d5_k1_fvg5"] = (
        signals.get("D5_full_setup", False) and
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q766 [triple]: E3_mild_pb + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9 (BOTH equal!)
    signals["Q766_e3_n1_q1"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q765 [triple]: E3_mild_pb + N1_pp_at_ma20 + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9 (BOTH equal!)
    signals["Q765_e3_n1_fvg3"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q764 [triple]: E3_mild_pb + I4b_3down_bounce + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.35%  N=9 (BOTH equal!)
    signals["Q764_e3_i4b_n1"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q763 [triple]: E3_mild_pb + A3_vol_surge + N1_pp_at_ma20
    # ★★★★ VALIDATED 2021+: h3=78.9% h5=68.4% avg=+2.71%  N=19 (VERY LARGE N!)
    signals["Q763_e3_a3_n1"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q762 [triple]: I2_power_move + M1_vdu_expansion + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+2.36%  N=8 (BOTH equal!)
    signals["Q762_i2_m1_fvg3"] = (
        signals.get("I2_power_move", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q761 [triple]: I2_power_move + I4_2down_bounce + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=66.7% avg=+2.27%  N=12 (LARGE N!)
    signals["Q761_i2_i4_fvg5"] = (
        signals.get("I2_power_move", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q760 [triple]: M4_vdu_full_compound + M5_post_bkt_vdu_expansion + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=55.6% avg=+1.80%  N=9
    signals["Q760_m4_m5_fvg3"] = (
        signals.get("M4_vdu_full_compound", False) and
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q759 [triple]: M4_vdu_full_compound + M2_vdu_deep_expansion + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=55.6% avg=+1.80%  N=9
    signals["Q759_m4_m2_fvg3"] = (
        signals.get("M4_vdu_full_compound", False) and
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q758 [triple]: M4_vdu_full_compound + M1_vdu_expansion + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=55.6% avg=+1.80%  N=9
    signals["Q758_m4_m1_fvg3"] = (
        signals.get("M4_vdu_full_compound", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q757 [triple]: M4_vdu_full_compound + C2_above_ma50 + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=55.6% avg=+1.80%  N=9
    signals["Q757_m4_c2_fvg3"] = (
        signals.get("M4_vdu_full_compound", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q756 [triple]: M4_vdu_full_compound + M3_vdu_expansion_pb + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=55.6% avg=+1.80%  N=9
    signals["Q756_m4_m3_fvg3"] = (
        signals.get("M4_vdu_full_compound", False) and
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q755 [triple]: I2_power_move + M5_post_bkt_vdu_expansion + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.65%  N=9 (BOTH equal!)
    signals["Q755_i2_m5_fvg5"] = (
        signals.get("I2_power_move", False) and
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q754 [triple]: C3_pocket_pivot + N1_pp_at_ma20 + O1_in_fvg_3d (already Q589 — skip)

    # Q753 [triple]: E3_mild_pb + O1_in_fvg_5d + D5_full_setup
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=70.0% avg=+1.89%  N=10 (BOTH high!)
    signals["Q753_e3_fvg5_d5"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("D5_full_setup", False)
    )

    # Q752 [triple]: I2_power_move + E1_pb_ma20 + I2b_power_move_strong
    # ★★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11 (LARGE N!)
    signals["Q752_i2_e1_i2b"] = (
        signals.get("I2_power_move", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q751 [triple]: E3_mild_pb + I2b_power_move_strong + N1_pp_at_ma20 (already Q553 — skip)

    # Q750 [triple]: C3_pocket_pivot + O1_in_fvg_8d + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.3% h5=63.6%  N=22 (VERY LARGE N!)
    signals["Q750_c3_fvg8_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q749 [triple]: C3_pocket_pivot + O1_in_fvg_5d + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.3% h5=63.6%  N=22 (VERY LARGE N!)
    signals["Q749_c3_fvg5_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q748 [triple]: C3_pocket_pivot + O1_in_fvg_3d + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.3% h5=63.6%  N=22 (VERY LARGE N!)
    signals["Q748_c3_fvg3_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q747 [triple]: C3_pocket_pivot + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.3% h5=63.6%  N=22 (VERY LARGE N!)
    signals["Q747_c3_n1_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q746 [triple]: C2_above_ma50 + N1_pp_at_ma20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=76.2% h5=66.7%  N=21 (VERY LARGE N!)
    signals["Q746_c2_n1_fvg3"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q745 [triple]: C2_above_ma50 + C3_pocket_pivot + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.2% h5=66.7%  N=21 (VERY LARGE N!)
    signals["Q745_c2_c3_q1"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q744 [triple]: M5_post_bkt_vdu_expansion + O2_choch_10d + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.38%  N=9 (BOTH high!)
    signals["Q744_m5_o2_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O2_choch_10d", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q743 [triple]: M5_post_bkt_vdu_expansion + I2_power_move + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.65%  N=9 (BOTH high!)
    signals["Q743_m5_i2_fvg5"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("I2_power_move", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q742 [triple]: C2_above_ma50 + E3_mild_pb + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9 (BOTH high!)
    signals["Q742_c2_e3_q1"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q741 [triple]: C3_pocket_pivot + E3_mild_pb + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9 (BOTH high!)
    signals["Q741_c3_e3_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q740 [triple]: E4_shallow_pb + C1_early_move + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.07%  N=9 (BOTH high!)
    signals["Q740_e4_c1_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("C1_early_move", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q739 [triple]: E4_shallow_pb + N1_pp_at_ma20 + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+2.06%  N=16 (VERY LARGE N! BOTH h3/h5 equal!)
    signals["Q739_e4_n1_fvg5"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q738 [triple]: B3_near_hi20 + N1_pp_at_ma20 + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=70.0%  N=20 (VERY LARGE N! BOTH high!)
    signals["Q738_b3_n1_fvg5"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q737 [triple]: B3_near_hi20 + H1_mom_resume + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=76.9% h5=69.2%  N=13 (LARGE N!)
    signals["Q737_b3_h1_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q736 [triple]: D3_near_hi_ma50 + H1_mom_resume + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=76.9% h5=69.2%  N=13 (LARGE N!)
    signals["Q736_d3_h1_q1"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q735 [triple]: C2_above_ma50 + M1_vdu_expansion + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=61.1% avg=+1.99%  N=18 (VERY LARGE N!)
    signals["Q735_c2_m1_fvg3"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q734 [triple]: B3_near_hi20 + M1_vdu_expansion + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.71%  N=16 (VERY LARGE N!)
    signals["Q734_b3_m1_fvg3"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q733 [triple]: D3_near_hi_ma50 + M1_vdu_expansion + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.71%  N=16 (VERY LARGE N!)
    signals["Q733_d3_m1_fvg3"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q732 [triple]: C2_above_ma50 + H1_mom_resume + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=76.5% h5=64.7%  N=17 (VERY LARGE N!)
    signals["Q732_c2_h1_q1"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q731 [triple]: M1_vdu_expansion + E4_shallow_pb + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=66.7% avg=+2.08%  N=15 (VERY LARGE N!)
    signals["Q731_m1_e4_fvg3"] = (
        signals.get("M1_vdu_expansion", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q730 [triple]: H1_mom_resume + M5_post_bkt_vdu_expansion + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=66.7% avg=+2.06%  N=15 (VERY LARGE N!)
    signals["Q730_h1_m5_fvg3"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q729 [triple]: H1_mom_resume + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.58%  N=11 (BOTH high!)
    signals["Q729_h1_e4_q1"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q728 [triple]: M1_vdu_expansion + M3_vdu_expansion_pb + O1_in_fvg_3d  (already Q723 reversed — skip)
    # Q727: M1_vdu_expansion + M5 + O1_in_fvg_3d (already Q716 reversed — skip)

    # Q726 [triple]: M3_vdu_expansion_pb + E4_shallow_pb + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=70.0% avg=+1.97%  N=10
    signals["Q726_m3_e4_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q725 [triple]: M3_vdu_expansion_pb + B3_near_hi20 + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=70.0% avg=+1.97%  N=10
    signals["Q725_m3_b3_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q724 [triple]: M3_vdu_expansion_pb + H1_mom_resume + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.49%  N=11 (BOTH high!)
    signals["Q724_m3_h1_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q723 [triple]: M3_vdu_expansion_pb + M1_vdu_expansion + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.33%  N=12 (LARGE N!)
    signals["Q723_m3_m1_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q722 [triple]: M2_vdu_deep_expansion + H1_mom_resume + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.86%  N=10
    signals["Q722_m2_h1_fvg3"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q721 [triple]: M2_vdu_deep_expansion + E4_shallow_pb + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.88%  N=10
    signals["Q721_m2_e4_fvg3"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q720 [triple]: M2_vdu_deep_expansion + M1_vdu_expansion + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.74%  N=11
    signals["Q720_m2_m1_fvg3"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q719 [triple]: C3_pocket_pivot + E1_pb_ma20 + I2b_power_move_strong
    # ★★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11 (LARGE N!)
    signals["Q719_c3_e1_i2b"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q718 [triple]: C3_pocket_pivot + E1_pb_ma20 + O1_in_fvg_5d
    # ★★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12 (LARGE N!)
    signals["Q718_c3_e1_fvg5"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q717 [triple]: M5_post_bkt_vdu_expansion + C1_early_move + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.65%  N=11 (BOTH high!)
    signals["Q717_m5_c1_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("C1_early_move", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q716 [triple]: M5_post_bkt_vdu_expansion + M1_vdu_expansion + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=81.2% h5=62.5% avg=+1.97%  N=16 (VERY LARGE N!)
    signals["Q716_m5_m1_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q715 [triple]: M5_post_bkt_vdu_expansion + M3_vdu_expansion_pb + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+1.82%  N=11
    signals["Q715_m5_m3_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q714 [triple]: M5_post_bkt_vdu_expansion + M2_vdu_deep_expansion + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.74%  N=11
    signals["Q714_m5_m2_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q713 [triple]: C3_pocket_pivot + D3_near_hi_ma50 + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=73.3%  N=15 (VERY LARGE N! BOTH high!)
    signals["Q713_c3_d3_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("D3_near_hi_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q712 [triple]: M5_post_bkt_vdu_expansion + B3_near_hi20 + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=66.7% avg=+2.08%  N=15 (VERY LARGE N!)
    signals["Q712_m5_b3_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q711 [triple]: D3_near_hi_ma50 + N1_pp_at_ma20 + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=73.3%  N=15 (VERY LARGE N! BOTH high!)
    signals["Q711_d3_n1_fvg3"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q710 [triple]: D3_near_hi_ma50 + M5_post_bkt_vdu_expansion + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=66.7% avg=+2.08%  N=15 (VERY LARGE N!)
    signals["Q710_d3_m5_fvg3"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q709 [triple]: D3_near_hi_ma50 + C3_pocket_pivot + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=73.3%  N=15 (VERY LARGE N! BOTH high!)
    signals["Q709_d3_c3_q1"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q708 [triple]: D3_near_hi_ma50 + B3_near_hi20 + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=73.3%  N=15 (VERY LARGE N! BOTH high!)
    signals["Q708_d3_b3_q1"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q707 [triple]: D3_near_hi_ma50 + C2_above_ma50 + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=73.3%  N=15 (VERY LARGE N! BOTH high!)
    signals["Q707_d3_c2_q1"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q706 [triple]: C2_above_ma50 + M2_vdu_deep_expansion + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.74%  N=11
    signals["Q706_c2_m2_fvg3"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q705 [triple]: C2_above_ma50 + M3_vdu_expansion_pb + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.33%  N=12 (LARGE N!)
    signals["Q705_c2_m3_fvg3"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q704 [triple]: B3_near_hi20 + O1_in_fvg_8d + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=81.2% h5=68.8%  N=16 (VERY LARGE N!)
    signals["Q704_b3_fvg8_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q703 [triple]: B3_near_hi20 + O1_in_fvg_5d + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=81.2% h5=68.8%  N=16 (VERY LARGE N!)
    signals["Q703_b3_fvg5_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q702 [triple]: B3_near_hi20 + O1_in_fvg_3d + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=81.2% h5=68.8%  N=16 (VERY LARGE N!)
    signals["Q702_b3_fvg3_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q701 [triple]: B3_near_hi20 + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=81.2% h5=68.8%  N=16 (VERY LARGE N!)
    signals["Q701_b3_n1_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q700 [triple]: B3_near_hi20 + N1_pp_at_ma20 + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=81.2% h5=68.8%  N=16 (VERY LARGE N!)
    signals["Q700_b3_n1_fvg3"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q699 [triple]: B3_near_hi20 + O2_choch_10d + Q1_pp_ma20_fvg3d
    # ★★★★★ VALIDATED 2021+: h3=83.3% h5=75.0%  N=12 (LARGE N!)
    signals["Q699_b3_o2_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("O2_choch_10d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q698 [triple]: E4_shallow_pb + O1_in_fvg_5d + Q1_pp_ma20_fvg3d
    # ★★★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N! BOTH high!)
    signals["Q698_e4_fvg5_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q697 [triple]: E4_shallow_pb + O1_in_fvg_3d + Q1_pp_ma20_fvg3d
    # ★★★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N! BOTH high!)
    signals["Q697_e4_fvg3_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q696 [triple]: E4_shallow_pb + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # ★★★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N! BOTH high!)
    signals["Q696_e4_n1_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q695 [triple]: E4_shallow_pb + N1_pp_at_ma20 + O1_in_fvg_3d
    # ★★★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N! BOTH high!)
    signals["Q695_e4_n1_fvg3"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q694 [triple]: E4_shallow_pb + C3_pocket_pivot + Q1_pp_ma20_fvg3d
    # ★★★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N! BOTH high!)
    signals["Q694_e4_c3_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q693 [triple]: E4_shallow_pb + C2_above_ma50 + Q1_pp_ma20_fvg3d
    # ★★★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N! BOTH high!)
    signals["Q693_e4_c2_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q692 [triple]: E4_shallow_pb + B3_near_hi20 + Q1_pp_ma20_fvg3d
    # ★★★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N! BOTH high!)
    signals["Q692_e4_b3_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q691 [triple]: E4_shallow_pb + O2_choch_10d + Q1_pp_ma20_fvg3d
    # ★★★★★ VALIDATED 2021+: h3=88.9% h5=88.9% avg=+2.54%  N=9 (BOTH h3/h5 near 90%!)
    signals["Q691_e4_o2_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("O2_choch_10d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q690 [triple]: D3_near_hi_ma50 + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=73.3%  N=15 (VERY LARGE N! BOTH high!)
    signals["Q690_d3_n1_q1"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q689 [triple]: D3_near_hi_ma50 + O2_choch_10d + Q1_pp_ma20_fvg3d
    # ★★★★★ VALIDATED 2021+: h3=81.8% h5=81.8%  N=11 (BOTH h3/h5 equal! LARGE N!)
    signals["Q689_d3_o2_q1"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("O2_choch_10d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q688 [triple]: D4_pocket_ma50 + E1_pb_ma20 + I2b_power_move_strong
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=70.0% avg=+3.08%  N=10 (BOTH high!)
    signals["Q688_d4_e1_i2b"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q687 [triple]: D4_pocket_ma50 + B3_near_hi20 + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=73.3%  N=15 (VERY LARGE N!)
    signals["Q687_d4_b3_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q686 [triple]: D4_pocket_ma50 + D3_near_hi_ma50 + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=73.3%  N=15 (VERY LARGE N!)
    signals["Q686_d4_d3_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("D3_near_hi_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q685 [triple]: D4_pocket_ma50 + E1_pb_ma20 + O1_in_fvg_5d
    # ★★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12 (LARGE N!)
    signals["Q685_d4_e1_fvg5"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q684 [triple]: D4_pocket_ma50 + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N! BOTH high!)
    signals["Q684_d4_e4_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q683 [triple]: I4_2down_bounce + E3_mild_pb + N5_pp_after_bkt_pb
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q683_i4_e3_n5"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q682 [triple]: I4_2down_bounce + C3_pocket_pivot + N5_pp_after_bkt_pb
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q682_i4_c3_n5"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q681 [triple]: I4_2down_bounce + C2_above_ma50 + N5_pp_after_bkt_pb
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q681_i4_c2_n5"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q680 [triple]: I4_2down_bounce + A3_vol_surge + N1_pp_at_ma20
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+3.39%  N=8
    signals["Q680_i4_a3_n1"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q679 [triple]: Q3_inside_dry_td6 + F2_dry_after_surge + I4_2down_bounce
    # ★★★★★ VALIDATED 2021+: h3=88.9% h5=88.9% avg=+4.46%  N=9 (BOTH h3/h5 near 90%!)
    signals["Q679_q3_f2_i4"] = (
        signals.get("Q3_inside_dry_td6", False) and
        signals.get("F2_dry_after_surge", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q678 [triple]: I4_2down_bounce + A3_vol_surge + O1_in_fvg_8d
    # ★★★★★ PERFECT: h3=100.0% h5=87.5% avg=+5.00%  N=8 (PERFECT 3d!)
    signals["Q678_i4_a3_fvg8"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q677 [triple]: X7_pb_dry_ma50 + K4_first_pb_to_ma20 + Q3_inside_dry_td6
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=60.0% avg=+2.37%  N=15 (LARGE N!)
    signals["Q677_x7_k4_q3"] = (
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("Q3_inside_dry_td6", False)
    )

    # Q676 [triple]: X7_pb_dry_ma50 + K4_first_pb_to_ma20 + Q2_pb_inside_td6
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=60.0% avg=+2.37%  N=15 (LARGE N!)
    signals["Q676_x7_k4_q2"] = (
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("Q2_pb_inside_td6", False)
    )

    # Q675 [triple]: X4_inside_near_hi + A3_vol_surge + O1_in_fvg_8d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.93%  N=10
    signals["Q675_x4_a3_fvg8"] = (
        signals.get("X4_inside_near_hi", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q674 [triple]: X4_inside_near_hi + A3_vol_surge + O1_in_fvg_5d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.93%  N=10
    signals["Q674_x4_a3_fvg5"] = (
        signals.get("X4_inside_near_hi", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q673 [triple]: X4_inside_near_hi + A3_vol_surge + O1_in_fvg_3d
    # ★★★★ VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.93%  N=10
    signals["Q673_x4_a3_fvg3"] = (
        signals.get("X4_inside_near_hi", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q672 [triple]: J6_dry_near_swing_lo + F1_vol_dry + F2_dry_after_surge
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=100.0% avg=+2.74%  N=8 (h5=100%! PERFECT 5d!)
    signals["Q672_j6_f1_f2"] = (
        signals.get("J6_dry_near_swing_lo", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("F2_dry_after_surge", False)
    )

    # Q671 [triple]: J3_dry_near_ma50 + F1_vol_dry + J6_dry_near_swing_lo (dup of Q670 reversed)
    # already captured by Q670

    # Q670 [triple]: J3_dry_near_ma50 + F1_vol_dry + J6_dry_near_swing_lo
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=100.0% avg=+2.89%  N=8 (h5=100%! PERFECT 5d!)
    signals["Q670_j3_f1_j6"] = (
        signals.get("J3_dry_near_ma50", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J6_dry_near_swing_lo", False)
    )

    # Q669 [triple]: J8_pb_zone_ma50 + X7_pb_dry_ma50 + J_combo_prior_hi
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q669_j8_x7_jcombo"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q668 [triple]: J8_pb_zone_ma50 + F5_dry_ma50 + J_combo_prior_hi
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q668_j8_f5_jcombo"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("F5_dry_ma50", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q667 [triple]: J8_pb_zone_ma50 + F1_vol_dry + J_combo_prior_hi
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q667_j8_f1_jcombo"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q666 [triple]: I10_top_decile_day + X4_inside_near_hi + J_combo_prior_hi
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+5.07%  N=8
    signals["Q666_i10_x4_jcombo"] = (
        signals.get("I10_top_decile_day", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q665 [triple]: I10_top_decile_day + X4_inside_near_hi + J5_dry_prior_base_hi
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+5.07%  N=8
    signals["Q665_i10_x4_j5"] = (
        signals.get("I10_top_decile_day", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q662 [triple]: J5b_dry_prior_hi_tight + F1_vol_dry + J_combo_ma50_trend
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+1.32%  N=8 (BOTH h3/h5 equal!)
    signals["Q662_j5b_f1_jma50"] = (
        signals.get("J5b_dry_prior_hi_tight", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J_combo_ma50_trend", False)
    )

    # Q661 [triple]: J5b_dry_prior_hi_tight + F1_vol_dry + J3_dry_near_ma50
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+1.32%  N=8 (BOTH h3/h5 equal!)
    signals["Q661_j5b_f1_j3"] = (
        signals.get("J5b_dry_prior_hi_tight", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J3_dry_near_ma50", False)
    )

    # Q660 [triple]: K1_first_pb_after_bkt + I4b_3down_bounce + J_combo_prior_hi
    # VALIDATED 2021+: h3=75.0% h5=66.7% avg=+2.25%  N=12 (BOTH h3/h5 solid!)
    signals["Q660_k1_i4b_jcombo"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q659 [triple]: K1_first_pb_after_bkt + I4b_3down_bounce + J5_dry_prior_base_hi
    # VALIDATED 2021+: h3=75.0% h5=66.7% avg=+2.25%  N=12 (BOTH h3/h5 solid!)
    signals["Q659_k1_i4b_j5"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q658 [triple]: K1_first_pb_after_bkt + J8_pb_zone_ma50 + P1_td_setup_count
    # VALIDATED 2021+: h3=75.0% h5=68.8% avg=+1.63%  N=16 (LARGE N! BOTH h3/h5 ok!)
    signals["Q658_k1_j8_p1"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q657 [triple]: K1_first_pb_after_bkt + I4b_3down_bounce + I10_top_decile_day
    # ★★★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+1.05%  N=18 (VERY LARGE N!)
    signals["Q657_k1_i4b_i10"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q656 [triple]: K1_first_pb_after_bkt + C3_pocket_pivot + I4_2down_bounce
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q656_k1_c3_i4"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q655 [triple]: K1_first_pb_after_bkt + I4_2down_bounce + D4_pocket_ma50
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q655_k1_i4_d4"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q654 [triple]: K1_first_pb_after_bkt + I4_2down_bounce + N5_pp_after_bkt_pb
    # ★★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q654_k1_i4_n5"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q653 [triple]: O2_choch_10d + N1_pp_at_ma20 + O1_in_fvg_8d
    # ★★★ VALIDATED 2021+: h3=76.9% h5=76.9%  N=13 (BOTH h3/h5 high! LARGE N!)
    signals["Q653_o2_n1_fvg8"] = (
        signals.get("O2_choch_10d", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q652 [triple]: O2_choch_10d + N1_pp_at_ma20 + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=76.9% h5=76.9%  N=13 (BOTH h3/h5 high! LARGE N!)
    signals["Q652_o2_n1_fvg5"] = (
        signals.get("O2_choch_10d", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q651 [triple]: O2_choch_10d + N1_pp_at_ma20 + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=76.9% h5=76.9%  N=13 (BOTH h3/h5 high! LARGE N!)
    signals["Q651_o2_n1_fvg3"] = (
        signals.get("O2_choch_10d", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q650 [triple]: O2_choch_10d + C3_pocket_pivot + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=76.9% h5=76.9%  N=13 (BOTH h3/h5 high! LARGE N!)
    signals["Q650_o2_c3_q1"] = (
        signals.get("O2_choch_10d", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q649 [triple]: P3_td_setup_6plus + J5b_dry_prior_hi_tight + K4_first_pb_to_ma20
    # VALIDATED 2021+: h3=78.6% h5=50.0% avg=+1.84%  N=14
    signals["Q649_p3_j5b_k4"] = (
        signals.get("P3_td_setup_6plus", False) and
        signals.get("J5b_dry_prior_hi_tight", False) and
        signals.get("K4_first_pb_to_ma20", False)
    )

    # Q648 [triple]: J_combo_prior_hi + E3_mild_pb + I4b_3down_bounce
    # VALIDATED 2021+: h3=75.0% h5=56.2% avg=+1.63%  N=16 (LARGE N!)
    signals["Q648_jcombo_e3_i4b"] = (
        signals.get("J_combo_prior_hi", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("I4b_3down_bounce", False)
    )

    # Q647 [triple]: J_combo_prior_hi + J2_dry_near_ma20 + P3_td_setup_6plus
    # VALIDATED 2021+: h3=76.5% h5=47.1% avg=+2.21%  N=17 (LARGE N!)
    signals["Q647_jcombo_j2_p3"] = (
        signals.get("J_combo_prior_hi", False) and
        signals.get("J2_dry_near_ma20", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q646 [triple]: K4_first_pb_to_ma20 + J_combo_prior_hi + P3_td_setup_6plus (similar to Q541 variant)
    # VALIDATED 2021+: h3=76.2% h5=52.4% avg=+2.13%  N=21 (VERY LARGE N!)
    signals["Q646_k4_jcombo_p3"] = (
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("J_combo_prior_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q645 [triple]: J2_dry_near_ma20 + P3_td_setup_6plus + J5b_dry_prior_hi_tight
    # VALIDATED 2021+: h3=76.9% h5=46.2% N=13 (LARGE N!)
    signals["Q645_j2_p3_j5b"] = (
        signals.get("J2_dry_near_ma20", False) and
        signals.get("P3_td_setup_6plus", False) and
        signals.get("J5b_dry_prior_hi_tight", False)
    )

    # Q644 [triple]: J_combo_ma50_trend + F5_dry_ma50 + J5_dry_prior_base_hi
    # VALIDATED 2021+: h3=75.0% h5=68.8% avg=+2.11%  N=16 (LARGE N!)
    signals["Q644_jcombo_ma50_f5_j5"] = (
        signals.get("J_combo_ma50_trend", False) and
        signals.get("F5_dry_ma50", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q643 [triple]: J_combo_ma20_trend + J5_dry_prior_base_hi + P3_td_setup_6plus
    # VALIDATED 2021+: h3=76.5% h5=47.1% avg=+2.21%  N=17 (LARGE N!)
    signals["Q643_jcombo_ma20_j5_p3"] = (
        signals.get("J_combo_ma20_trend", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q642 [triple]: J_combo_ma20_trend + J5b_dry_prior_hi_tight + P3_td_setup_6plus
    # VALIDATED 2021+: h3=76.9% h5=46.2% avg=+1.91%  N=13
    signals["Q642_jcombo_ma20_j5b_p3"] = (
        signals.get("J_combo_ma20_trend", False) and
        signals.get("J5b_dry_prior_hi_tight", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q641 [triple]: X5_nr7_ma50 + J5_dry_prior_base_hi + P3_td_setup_6plus
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+3.71%  N=8 (BOTH h3/h5! HIGH avg!)
    signals["Q641_x5_j5_p3"] = (
        signals.get("X5_nr7_ma50", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q640 [triple]: X5_nr7_ma50 + I5_cap_reversal + P1_td_setup_count
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+3.46%  N=8 (BOTH h3/h5! HIGH avg!)
    signals["Q640_x5_i5_p1"] = (
        signals.get("X5_nr7_ma50", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q639 [triple]: X5_nr7_ma50 + X2_mild_pb_dry + O3_choch_dry (same as Q635)
    # skip

    # Q638 [triple]: X7_pb_dry_ma50 + J_combo_ma50_trend + J_combo_prior_hi
    # VALIDATED 2021+: h3=75.0% h5=66.7% avg=+1.72%  N=12
    signals["Q638_x7_jcombo_ma50_jcombo"] = (
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("J_combo_ma50_trend", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q637 [triple]: X7_pb_dry_ma50 + J3_dry_near_ma50 + J_combo_prior_hi
    # VALIDATED 2021+: h3=75.0% h5=66.7% avg=+1.72%  N=12
    signals["Q637_x7_j3_jcombo"] = (
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("J3_dry_near_ma50", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q636 [triple]: X7_pb_dry_ma50 + J3_dry_near_ma50 + J5_dry_prior_base_hi
    # VALIDATED 2021+: h3=75.0% h5=66.7% avg=+1.72%  N=12
    signals["Q636_x7_j3_j5"] = (
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("J3_dry_near_ma50", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q635 [triple]: X2_mild_pb_dry + X5_nr7_ma50 + O3_choch_dry
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+4.23%  N=8 (HIGH avg!)
    signals["Q635_x2_x5_o3"] = (
        signals.get("X2_mild_pb_dry", False) and
        signals.get("X5_nr7_ma50", False) and
        signals.get("O3_choch_dry", False)
    )

    # Q634 [triple]: X2_mild_pb_dry + X5_nr7_ma50 + O2_choch_10d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+4.23%  N=8 (HIGH avg!)
    signals["Q634_x2_x5_o2"] = (
        signals.get("X2_mild_pb_dry", False) and
        signals.get("X5_nr7_ma50", False) and
        signals.get("O2_choch_10d", False)
    )

    # Q633 [triple]: X8_3dry_near_hi + H2_new10d_hi + I8_3up_days
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+4.31%  N=8 (HIGH avg!)
    signals["Q633_x8_h2_i8"] = (
        signals.get("X8_3dry_near_hi", False) and
        signals.get("H2_new10d_hi", False) and
        signals.get("I8_3up_days", False)
    )

    # Q632 [triple]: X4_inside_near_hi + B2_tight + I4_2down_bounce (same as Q569)
    # skip — duplicate of Q569

    # Q631 [triple]: X2_mild_pb_dry + G2_nr7 + O3_choch_dry (same as Q635 variant?)
    # Q630 — G2+X2+O2 already added as Q613
    # G2+X2+O3 is different though:
    signals["Q630_g2_x2_o3"] = (
        signals.get("G2_nr7", False) and
        signals.get("X2_mild_pb_dry", False) and
        signals.get("O3_choch_dry", False)
    )

    # Q629 [triple]: A2_bkt10 + X8_3dry_near_hi + I8_3up_days
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+4.31%  N=8 (HIGH avg!)
    signals["Q629_a2_x8_i8"] = (
        signals.get("A2_bkt10", False) and
        signals.get("X8_3dry_near_hi", False) and
        signals.get("I8_3up_days", False)
    )

    # Q628 [triple]: I3b_gap_up_strong + H2_new10d_hi + P6_td_countdown_8plus
    # ★★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+4.54%  N=13 (HIGH avg!)
    signals["Q628_i3b_h2_p6"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("H2_new10d_hi", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q627 [triple]: I3b_gap_up_strong + A2_bkt10 + P6_td_countdown_8plus
    # ★★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+4.54%  N=13 (HIGH avg!)
    signals["Q627_i3b_a2_p6"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("A2_bkt10", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q626 [triple]: I3b_gap_up_strong + A1_bkt20_vol + P6_td_countdown_8plus
    # ★★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+4.54%  N=13 (HIGH avg!)
    signals["Q626_i3b_a1_p6"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("A1_bkt20_vol", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q625 [triple]: I5_cap_reversal + C1_early_move + I3b_gap_up_strong
    # ★★★ VALIDATED 2021+: h3=75.0% h5=87.5% avg=+3.30%  N=8 (HIGH h5!)
    signals["Q625_i5_c1_i3b"] = (
        signals.get("I5_cap_reversal", False) and
        signals.get("C1_early_move", False) and
        signals.get("I3b_gap_up_strong", False)
    )

    # Q624 [triple]: I5_cap_reversal + I3b_gap_up_strong + I10_top_decile_day
    # ★★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+5.94%  N=16 (LARGE N! HIGH avg!)
    signals["Q624_i5_i3b_i10"] = (
        signals.get("I5_cap_reversal", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q623 — I5+I3b+O2 already added as Q399
    # Q622 — I5+A3+I3b already added as Q521
    # Q621 — I5+I2b+I3b already added as Q560

    # Q620 [triple]: I7_dry_up_near_hi + H2_new10d_hi + D4_pocket_ma50
    # ★★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+5.48%  N=8 (EXTRAORDINARY avg!)
    signals["Q620_i7_h2_d4"] = (
        signals.get("I7_dry_up_near_hi", False) and
        signals.get("H2_new10d_hi", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q619 [triple]: I7_dry_up_near_hi + C3_pocket_pivot + H2_new10d_hi
    # ★★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+5.48%  N=8 (EXTRAORDINARY avg!)
    signals["Q619_i7_c3_h2"] = (
        signals.get("I7_dry_up_near_hi", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("H2_new10d_hi", False)
    )

    # Q618 [triple]: I7_dry_up_near_hi + A2_bkt10 + D4_pocket_ma50
    # ★★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+5.48%  N=8 (EXTRAORDINARY avg!)
    signals["Q618_i7_a2_d4"] = (
        signals.get("I7_dry_up_near_hi", False) and
        signals.get("A2_bkt10", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q617 [triple]: I7_dry_up_near_hi + A2_bkt10 + C3_pocket_pivot
    # ★★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+5.48%  N=8 (EXTRAORDINARY avg!)
    signals["Q617_i7_a2_c3"] = (
        signals.get("I7_dry_up_near_hi", False) and
        signals.get("A2_bkt10", False) and
        signals.get("C3_pocket_pivot", False)
    )

    # Q616 [triple]: I6_nr7_close_hi + I4b_3down_bounce + I10_top_decile_day
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+2.18%  N=8
    signals["Q616_i6_i4b_i10"] = (
        signals.get("I6_nr7_close_hi", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q615 [triple]: I6_nr7_close_hi + J9_3dry_at_ma20 + O1_in_fvg_5d
    # VALIDATED 2021+: h3=75.0% h5=66.7% avg=+1.50%  N=12
    signals["Q615_i6_j9_fvg5"] = (
        signals.get("I6_nr7_close_hi", False) and
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q614 [triple]: G2_nr7 + J5_dry_prior_base_hi + P3_td_setup_6plus
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+3.71%  N=8 (BOTH h3/h5! HIGH avg!)
    signals["Q614_g2_j5_p3"] = (
        signals.get("G2_nr7", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q613 [triple]: G2_nr7 + X2_mild_pb_dry + O2_choch_10d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+4.23%  N=8 (HIGH avg!)
    signals["Q613_g2_x2_o2"] = (
        signals.get("G2_nr7", False) and
        signals.get("X2_mild_pb_dry", False) and
        signals.get("O2_choch_10d", False)
    )

    # Q612 [triple]: G2_nr7 + I5_cap_reversal + P1_td_setup_count
    # ★★★ VALIDATED 2021+: h3=77.8% h5=55.6% avg=+3.13%  N=9 (HIGH avg!)
    signals["Q612_g2_i5_p1"] = (
        signals.get("G2_nr7", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q611 [triple]: G2_nr7 + I4b_3down_bounce + I10_top_decile_day
    # ★★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+2.13%  N=9
    signals["Q611_g2_i4b_i10"] = (
        signals.get("G2_nr7", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q610 [triple]: F3_3day_dry + K1_first_pb_after_bkt + P6_td_countdown_8plus
    # ★★★ VALIDATED 2021+: h3=75.0% h5=87.5% avg=+3.85%  N=8 (PERFECT h5! HIGH avg!)
    signals["Q610_f3_k1_p6"] = (
        signals.get("F3_3day_dry", False) and
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q609 — F3+A2+I8 = Q522, F3+H2+I8 = Q523, already added

    # Q608 [triple]: G1_inside_bar + I4_2down_bounce + D5_full_setup (same as Q518)
    # skip

    # Q607 [triple]: I6_nr7_close_hi + K4_first_pb_to_ma20 + P3_td_setup_6plus (same as Q544)
    # skip — already added as Q544

    # Q606 [triple]: E3_mild_pb + I4b_3down_bounce + N1_pp_at_ma20 (same as Q563)
    # skip

    # Q605 [triple]: D4_pocket_ma50 + H1_mom_resume + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.5% h5=64.7%  N=17 (LARGE N!)
    signals["Q605_d4_h1_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q604 [triple]: D4_pocket_ma50 + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.2% h5=66.7%  N=21 (VERY LARGE N!)
    signals["Q604_d4_n1_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q603 [triple]: D4_pocket_ma50 + N1_pp_at_ma20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=76.2% h5=66.7%  N=21 (VERY LARGE N!)
    signals["Q603_d4_n1_fvg3"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q602 [triple]: D4_pocket_ma50 + C3_pocket_pivot + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.2% h5=66.7%  N=21 (VERY LARGE N!)
    signals["Q602_d4_c3_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q601 [triple]: D4_pocket_ma50 + C2_above_ma50 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.2% h5=66.7%  N=21 (VERY LARGE N!)
    signals["Q601_d4_c2_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q600 [triple]: D4_pocket_ma50 + E3_mild_pb + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9 (BOTH h3/h5 high!)
    signals["Q600_d4_e3_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q599 [triple]: D3_near_hi_ma50 + M1_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.71%  N=16 (LARGE N!)
    signals["Q599_d3_m1_fvg3"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q598 [triple]: D3_near_hi_ma50 + H1_mom_resume + Q1_pp_ma20_fvg3d (same as Q513)
    # skip — duplicate of Q513

    # Q597 [triple]: N1_pp_at_ma20 + E3_mild_pb + O1_in_fvg_3d (same as Q573)
    # skip — duplicate

    # Q596 [triple]: N1_pp_at_ma20 + E3_mild_pb + Q1_pp_ma20_fvg3d (same as Q574)
    # skip — duplicate

    # Q595 [triple]: N1_pp_at_ma20 + E3_mild_pb + I4b_3down_bounce (same as Q563)
    # skip — duplicate

    # Q594 [triple]: E1_pb_ma20 + J_combo_prior_hi + P3_td_setup_6plus
    # VALIDATED 2021+: h3=76.5% h5=47.1% avg=+2.21%  N=17 (LARGE N!)
    signals["Q594_e1_jcombo_p3"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("J_combo_prior_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q593 [triple]: C3_pocket_pivot + O1_in_fvg_8d + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.3% h5=63.6%  N=22 (VERY LARGE N!)
    signals["Q593_c3_fvg8_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q592 [triple]: C3_pocket_pivot + O1_in_fvg_5d + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.3% h5=63.6%  N=22 (VERY LARGE N!)
    signals["Q592_c3_fvg5_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q591 [triple]: C3_pocket_pivot + O1_in_fvg_3d + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.3% h5=63.6%  N=22 (VERY LARGE N!)
    signals["Q591_c3_fvg3_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q590 [triple]: C3_pocket_pivot + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.3% h5=63.6%  N=22 (VERY LARGE N!)
    signals["Q590_c3_n1_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q589 [triple]: C3_pocket_pivot + N1_pp_at_ma20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=77.3% h5=63.6%  N=22 (VERY LARGE N!)
    signals["Q589_c3_n1_fvg3"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q588 [triple]: C2_above_ma50 + N1_pp_at_ma20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=76.2% h5=66.7%  N=21 (VERY LARGE N!)
    signals["Q588_c2_n1_fvg3"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q587 [triple]: C2_above_ma50 + C3_pocket_pivot + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.2% h5=66.7%  N=21 (VERY LARGE N!)
    signals["Q587_c2_c3_q1"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q586 [triple]: C2_above_ma50 + M1_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=77.8% h5=61.1% avg=+1.99%  N=18 (LARGE N!)
    signals["Q586_c2_m1_fvg3"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q585 [triple]: E1_pb_ma20 + J5_dry_prior_base_hi + P3_td_setup_6plus
    # VALIDATED 2021+: h3=76.5% h5=47.1% avg=+2.21%  N=17 (LARGE N!)
    signals["Q585_e1_j5_p3"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q584 [triple]: E1_pb_ma20 + A3_vol_surge + I10_top_decile_day (same as Q520)
    # skip — duplicate of Q520

    # Q583 [triple]: B3_near_hi20 + H1_mom_resume + Q1_pp_ma20_fvg3d (same as Q514)
    # skip — duplicate of Q514

    # Q582 [triple]: B3_near_hi20 + N1_pp_at_ma20 + O1_in_fvg_5d (same as Q577)
    # skip — duplicate

    # Q581 [triple]: C2_above_ma50 + E3_mild_pb + Q1_pp_ma20_fvg3d (same as Q571)
    # skip — duplicate of Q571

    # Q580 [triple]: C3_pocket_pivot + E3_mild_pb + Q1_pp_ma20_fvg3d (same as Q572)
    # skip — duplicate

    # Q579 [triple]: B3_near_hi20 + M4_vdu_full_compound + O1_in_fvg_3d
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.97%  N=8
    signals["Q579_b3_m4_fvg3"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("M4_vdu_full_compound", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q578 [triple]: B3_near_hi20 + M1_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.71%  N=16 (LARGE N!)
    signals["Q578_b3_m1_fvg3"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q577 [triple]: B3_near_hi20 + N1_pp_at_ma20 + O1_in_fvg_5d
    # VALIDATED 2021+: h3=75.0% h5=70.0%  N=20 (VERY LARGE N!)
    signals["Q577_b3_n1_fvg5"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q576 [triple]: B2_tight + E2_pb_ma50 + O1_in_fvg_8d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=88.9% avg=+0.33%  N=9 (HIGH h5!)
    signals["Q576_b2_e2_fvg8"] = (
        signals.get("B2_tight", False) and
        signals.get("E2_pb_ma50", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q575 [triple]: E4_shallow_pb + N1_pp_at_ma20 + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+2.06%  N=16 (LARGE N! BOTH high!)
    signals["Q575_e4_n1_fvg5"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q574 [triple]: E3_mild_pb + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9 (BOTH h3/h5 high!)
    signals["Q574_e3_n1_q1"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q573 [triple]: E3_mild_pb + N1_pp_at_ma20 + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9 (BOTH h3/h5 high!)
    signals["Q573_e3_n1_fvg3"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q572 [triple]: E3_mild_pb + C3_pocket_pivot + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9 (BOTH h3/h5 high!)
    signals["Q572_e3_c3_q1"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q571 [triple]: E3_mild_pb + C2_above_ma50 + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9 (BOTH h3/h5 high!)
    signals["Q571_e3_c2_q1"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q570 [triple]: A3_vol_surge + E3_mild_pb + N1_pp_at_ma20 (same as Q519)
    # skip — duplicate of Q519

    # Q569 [triple]: B2_tight + X4_inside_near_hi + I4_2down_bounce
    # VALIDATED 2021+: h3=77.8% h5=66.7% avg=+1.21%  N=9
    signals["Q569_b2_x4_i4"] = (
        signals.get("B2_tight", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q568 [triple]: E4_shallow_pb + C1_early_move + Q1_pp_ma20_fvg3d (same as Q413)
    # skip — duplicate

    # Q567 [triple]: I3_gap_up_hold + X4_inside_near_hi + J5b_dry_prior_hi_tight
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+1.33%  N=8 (BOTH h3/h5 high!)
    signals["Q567_i3_x4_j5b"] = (
        signals.get("I3_gap_up_hold", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("J5b_dry_prior_hi_tight", False)
    )

    # Q566 [triple]: D5_full_setup + K1_first_pb_after_bkt + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+1.48%  N=8 (BOTH h3/h5 high!)
    signals["Q566_d5_k1_fvg5"] = (
        signals.get("D5_full_setup", False) and
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q565 [triple]: I4b_3down_bounce + N1_pp_at_ma20 + P1_td_setup_count
    # ★★★ VALIDATED 2021+: h3=76.9% h5=69.2% avg=+1.69%  N=13 (LARGE N!)
    signals["Q565_i4b_n1_p1"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q564 [triple]: I4b_3down_bounce + I10_top_decile_day + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=76.9% h5=69.2% avg=+1.63%  N=13 (LARGE N!)
    signals["Q564_i4b_i10_n1"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("I10_top_decile_day", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q563 [triple]: I4b_3down_bounce + E3_mild_pb + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.35%  N=9 (BOTH h3/h5 high!)
    signals["Q563_i4b_e3_n1"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q562 [triple]: I2b_power_move_strong + E1_pb_ma20 + I1_close_quality
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+3.24%  N=8 (HIGH avg!)
    signals["Q562_i2b_e1_i1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I1_close_quality", False)
    )

    # Q561 [triple]: I2b_power_move_strong + E1_pb_ma20 + I10_top_decile_day
    # ★★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+3.18%  N=9 (HIGH avg!)
    signals["Q561_i2b_e1_i10"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q560 [triple]: I2b_power_move_strong + I3b_gap_up_strong + I5_cap_reversal
    # ★★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+5.34%  N=13 (EXTRAORDINARY avg!)
    signals["Q560_i2b_i3b_i5"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q559 [triple]: I3_gap_up_hold + E2_pb_ma50 + I8_3up_days (same as Q527 from I8 backbone)
    # skip

    # Q558 [triple]: D5_full_setup + E3_mild_pb + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=80.0% h5=70.0% avg=+1.89%  N=10
    signals["Q558_d5_e3_fvg5"] = (
        signals.get("D5_full_setup", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q557 [triple]: I2b_power_move_strong + E1_pb_ma20 + D4_pocket_ma50
    # ★★★ VALIDATED 2021+: h3=80.0% h5=70.0% avg=+3.08%  N=10 (HIGH avg!)
    signals["Q557_i2b_e1_d4"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q556 [triple]: I2b_power_move_strong + C2_above_ma50 + E1_pb_ma20
    # ★★★ VALIDATED 2021+: h3=80.0% h5=70.0% avg=+3.08%  N=10 (HIGH avg!)
    signals["Q556_i2b_c2_e1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("E1_pb_ma20", False)
    )

    # Q555 [triple]: I2b_power_move_strong + E1_pb_ma20 + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q555_i2b_e1_n1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q554 [triple]: I2b_power_move_strong + C3_pocket_pivot + E1_pb_ma20
    # ★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q554_i2b_c3_e1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("E1_pb_ma20", False)
    )

    # Q553 [triple]: I2b_power_move_strong + E3_mild_pb + N1_pp_at_ma20
    # ★★★★ VALIDATED 2021+: h3=88.9% h5=77.8% avg=+3.68%  N=9 (HIGH h5! HIGH avg!)
    signals["Q553_i2b_e3_n1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q552 [triple]: I4b_3down_bounce + I3b_gap_up_strong + P1_td_setup_count
    # ★★★★★ VALIDATED 2021+: h3=80.0% h5=90.0% avg=+7.51%  N=10 (h5=90%! EXTRAORDINARY avg!)
    signals["Q552_i4b_i3b_p1"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q551 [triple]: I2b_power_move_strong + A3_vol_surge + E1_pb_ma20
    # ★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q551_i2b_a3_e1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("E1_pb_ma20", False)
    )

    # Q550 [triple]: I2_power_move + E1_pb_ma20 + I2b_power_move_strong
    # ★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q550_i2_e1_i2b"] = (
        signals.get("I2_power_move", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q549 [triple]: F1_vol_dry + J3_dry_near_ma50 + J_combo_prior_hi
    # VALIDATED 2021+: h3=75.0% h5=68.8% avg=+2.11%  N=16 (LARGE N!)
    signals["Q549_f1_j3_jcombo"] = (
        signals.get("F1_vol_dry", False) and
        signals.get("J3_dry_near_ma50", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q548 [triple]: F1_vol_dry + J5_dry_prior_base_hi + J_combo_ma50_trend
    # VALIDATED 2021+: h3=75.0% h5=68.8% avg=+2.11%  N=16 (LARGE N!)
    signals["Q548_f1_j5_jcombo_ma50"] = (
        signals.get("F1_vol_dry", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("J_combo_ma50_trend", False)
    )

    # Q547 [triple]: F2_dry_after_surge + I4b_3down_bounce + P3_td_setup_6plus
    # ★★★ VALIDATED 2021+: h3=75.0% h5=66.7% avg=+2.83%  N=12
    signals["Q547_f2_i4b_p3"] = (
        signals.get("F2_dry_after_surge", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q546 [triple]: K1_first_pb_after_bkt + J8_pb_zone_ma50 + P1_td_setup_count
    # VALIDATED 2021+: h3=75.0% h5=68.8% avg=+1.63%  N=16 (LARGE N!)
    signals["Q546_k1_j8_p1"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q545 [triple]: K1_first_pb_after_bkt + I4b_3down_bounce + I10_top_decile_day
    # VALIDATED 2021+: h3=77.8% h5=66.7% avg=+1.05%  N=18 (LARGE N!)
    signals["Q545_k1_i4b_i10"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q544 [triple]: K4_first_pb_to_ma20 + I6_nr7_close_hi + P3_td_setup_6plus
    # ★★★ VALIDATED 2021+: h3=76.9% h5=69.2% avg=+4.08%  N=13 (HIGH avg!)
    signals["Q544_k4_i6_p3"] = (
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("I6_nr7_close_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q543 [triple]: K4_first_pb_to_ma20 + F5_dry_ma50 + Q2_pb_inside_td6
    # ★★★★ VALIDATED 2021+: h3=76.7% h5=70.0% avg=+3.17%  N=30 (VERY LARGE N! HIGH avg!)
    signals["Q543_k4_f5_q2"] = (
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("F5_dry_ma50", False) and
        signals.get("Q2_pb_inside_td6", False)
    )

    # Q542 [triple]: K4_first_pb_to_ma20 + F1_vol_dry + Q2_pb_inside_td6
    # ★★★★ VALIDATED 2021+: h3=76.7% h5=70.0% avg=+3.17%  N=30 (VERY LARGE N! HIGH avg!)
    signals["Q542_k4_f1_q2"] = (
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("Q2_pb_inside_td6", False)
    )

    # Q541 [triple]: K1_first_pb_after_bkt + J5_dry_prior_base_hi + P3_td_setup_6plus
    # VALIDATED 2021+: h3=77.8% h5=44.4% avg=+2.37%  N=9
    signals["Q541_k1_j5_p3"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q540 [triple]: K4_first_pb_to_ma20 + X7_pb_dry_ma50 + Q3_inside_dry_td6
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+2.37%  N=15 (LARGE N!)
    signals["Q540_k4_x7_q3"] = (
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("Q3_inside_dry_td6", False)
    )

    # Q539 [triple]: K4_first_pb_to_ma20 + X7_pb_dry_ma50 + Q2_pb_inside_td6
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+2.37%  N=15 (LARGE N!)
    signals["Q539_k4_x7_q2"] = (
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("Q2_pb_inside_td6", False)
    )

    # Q538 [triple]: K1_first_pb_after_bkt + I4_2down_bounce + D4_pocket_ma50
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q538_k1_i4_d4"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q537 [triple]: K1_first_pb_after_bkt + C3_pocket_pivot + I4_2down_bounce
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q537_k1_c3_i4"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q536 — K1+I4+N5 already in Q486

    # Q535 [triple]: J5_dry_prior_base_hi + X7_pb_dry_ma50 + J8_pb_zone_ma50
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q535_j5_x7_j8"] = (
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q534 [triple]: J5_dry_prior_base_hi + X4_inside_near_hi + I10_top_decile_day
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+5.07%  N=8 (HIGH avg!)
    signals["Q534_j5_x4_i10"] = (
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q533 [triple]: J5_dry_prior_base_hi + F5_dry_ma50 + J8_pb_zone_ma50
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q533_j5_f5_j8"] = (
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("F5_dry_ma50", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q532 [triple]: J5_dry_prior_base_hi + F1_vol_dry + J8_pb_zone_ma50
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q532_j5_f1_j8"] = (
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q531 [triple]: J6_dry_near_swing_lo + F1_vol_dry + F2_dry_after_surge
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=100.0% avg=+2.74%  N=8 (PERFECT h5!)
    signals["Q531_j6_f1_f2"] = (
        signals.get("J6_dry_near_swing_lo", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("F2_dry_after_surge", False)
    )

    # Q530 [triple]: J3_dry_near_ma50 + F1_vol_dry + J6_dry_near_swing_lo
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=100.0% avg=+2.89%  N=8 (PERFECT h5!)
    signals["Q530_j3_f1_j6"] = (
        signals.get("J3_dry_near_ma50", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J6_dry_near_swing_lo", False)
    )

    # Q529 [triple]: J9_3dry_at_ma20 + J_combo_ma50_trend + O1_in_fvg_8d
    # ★★★ VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.09%  N=11
    signals["Q529_j9_jcombo_fvg8"] = (
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("J_combo_ma50_trend", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q528 — I9+J9+fvg5 already added as Q498

    # Q527 [triple]: I8_3up_days + E2_pb_ma50 + I3_gap_up_hold
    # VALIDATED 2021+: h3=77.8% h5=66.7% avg=+1.47%  N=9
    signals["Q527_i8_e2_i3"] = (
        signals.get("I8_3up_days", False) and
        signals.get("E2_pb_ma50", False) and
        signals.get("I3_gap_up_hold", False)
    )

    # Q526 [triple]: H1_mom_resume + M1_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=76.5% h5=64.7% avg=+2.07%  N=17 (LARGE N!)
    signals["Q526_h1_m1_fvg3"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q525 [triple]: I9_inside_close_hi + I8_3up_days + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+3.86%  N=8 (BOTH h3/h5 high! HIGH avg!)
    signals["Q525_i9_i8_fvg5"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("I8_3up_days", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q524 [triple]: I9_inside_close_hi + F2_dry_after_surge + P3_td_setup_6plus
    # ★★★★ VALIDATED 2021+: h3=76.5% h5=76.5% avg=+5.24%  N=17 (BOTH h3/h5! LARGE N! HIGH avg!)
    signals["Q524_i9_f2_p3"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("F2_dry_after_surge", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q523 [triple]: I8_3up_days + F3_3day_dry + H2_new10d_hi
    # ★★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+4.63%  N=9 (HIGH avg!)
    signals["Q523_i8_f3_h2"] = (
        signals.get("I8_3up_days", False) and
        signals.get("F3_3day_dry", False) and
        signals.get("H2_new10d_hi", False)
    )

    # Q522 [triple]: I8_3up_days + A2_bkt10 + F3_3day_dry
    # ★★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+4.63%  N=9 (HIGH avg!)
    signals["Q522_i8_a2_f3"] = (
        signals.get("I8_3up_days", False) and
        signals.get("A2_bkt10", False) and
        signals.get("F3_3day_dry", False)
    )

    # Q521 [triple]: A3_vol_surge + I3b_gap_up_strong + I5_cap_reversal
    # ★★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+5.34%  N=13 (EXTRAORDINARY avg!)
    signals["Q521_a3_i3b_i5"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q520 [triple]: A3_vol_surge + E1_pb_ma20 + I10_top_decile_day
    # ★★★ VALIDATED 2021+: h3=76.9% h5=76.9% avg=+2.44%  N=13 (BOTH h3/h5 high!)
    signals["Q520_a3_e1_i10"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q519 [triple]: A3_vol_surge + E3_mild_pb + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=78.9% h5=68.4% avg=+2.71%  N=19 (VERY LARGE N!)
    signals["Q519_a3_e3_n1"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q518 [triple]: G1_inside_bar + I4_2down_bounce + D5_full_setup
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.26%  N=8
    signals["Q518_g1_i4_d5"] = (
        signals.get("G1_inside_bar", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("D5_full_setup", False)
    )

    # Q517 [triple]: I9_inside_close_hi + J4_dry_value_area + O1_in_fvg_5d
    # VALIDATED 2021+: h3=75.0% h5=58.3% avg=+2.63%  N=12
    signals["Q517_i9_j4_fvg5"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J4_dry_value_area", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q516 [triple]: H1_mom_resume + N1_pp_at_ma20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=76.5% h5=64.7%  N=17 (LARGE N!)
    signals["Q516_h1_n1_fvg3"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q515 [triple]: H1_mom_resume + C2_above_ma50 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.5% h5=64.7%  N=17 (LARGE N!)
    signals["Q515_h1_c2_q1"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q514 [triple]: H1_mom_resume + B3_near_hi20 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.9% h5=69.2%  N=13
    signals["Q514_h1_b3_q1"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q513 [triple]: H1_mom_resume + D3_near_hi_ma50 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.9% h5=69.2%  N=13
    signals["Q513_h1_d3_q1"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("D3_near_hi_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q512 [triple]: H1_mom_resume + M2_vdu_deep_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.86%  N=10
    signals["Q512_h1_m2_fvg3"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q511 [triple]: H1_mom_resume + M5_post_bkt_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=66.7% avg=+2.06%  N=15 (LARGE N!)
    signals["Q511_h1_m5_fvg3"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q510 [triple]: I8_3up_days + I2b_power_move_strong + I9_inside_close_hi
    # ★★★ VALIDATED 2021+: h3=80.0% h5=70.0% avg=+2.45%  N=10
    signals["Q510_i8_i2b_i9"] = (
        signals.get("I8_3up_days", False) and
        signals.get("I2b_power_move_strong", False) and
        signals.get("I9_inside_close_hi", False)
    )

    # Q509 [triple]: H1_mom_resume + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.58%  N=11
    signals["Q509_h1_e4_q1"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q508 — M3+H1+fvg3 already added as Q482
    # Q507 — H2+A3+N1 already added as Q503

    # Q506 [triple]: A3_vol_surge + E1_pb_ma20 + O1_in_fvg_8d
    # ★★★ VALIDATED 2021+: h3=80.0% h5=73.3% avg=+1.38%  N=15 (LARGE N!)
    signals["Q506_a3_e1_fvg8"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q505 [triple]: A3_vol_surge + E1_pb_ma20 + I2b_power_move_strong
    # ★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q505_a3_e1_i2b"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q504 [triple]: A3_vol_surge + I4_2down_bounce + N1_pp_at_ma20
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+3.39%  N=8 (HIGH avg!)
    signals["Q504_a3_i4_n1"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q503 [triple]: A3_vol_surge + H2_new10d_hi + N1_pp_at_ma20
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.47%  N=8
    signals["Q503_a3_h2_n1"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("H2_new10d_hi", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q502 [triple]: A2_bkt10 + A3_vol_surge + N1_pp_at_ma20
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.47%  N=8
    signals["Q502_a2_a3_n1"] = (
        signals.get("A2_bkt10", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q501 [triple]: A3_vol_surge + I4_2down_bounce + P1_td_setup_count
    # ★★★★★ VALIDATED 2021+: h3=100.0% h5=88.9% avg=+6.40%  N=9 (PERFECT 3D + EXTRAORDINARY avg!)
    signals["Q501_a3_i4_p1"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q500 [triple]: A3_vol_surge + I4_2down_bounce + O1_in_fvg_8d (same as Q340 variant?)
    # ★★★★★ VALIDATED 2021+: h3=100.0% h5=87.5% avg=+5.00%  N=8 (PERFECT 3D!)
    # Q340 is fvg8+A3+I4 — this is the same combo, already added
    # skip — duplicate of Q340

    # Q499 [triple]: O1_in_fvg_5d + E4_shallow_pb + Q1_pp_ma20_fvg3d is Q488 — skip

    # Q498 [triple]: O1_in_fvg_5d + I9_inside_close_hi + J9_3dry_at_ma20
    # ★★★★ VALIDATED 2021+: h3=88.9% h5=55.6% avg=+1.58%  N=9
    signals["Q498_fvg5_i9_j9"] = (
        signals.get("O1_in_fvg_5d", False) and
        signals.get("I9_inside_close_hi", False) and
        signals.get("J9_3dry_at_ma20", False)
    )

    # Q497 [triple]: O1_in_fvg_5d + E1_pb_ma20 + D4_pocket_ma50
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12
    signals["Q497_fvg5_e1_d4"] = (
        signals.get("O1_in_fvg_5d", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q496 [triple]: O1_in_fvg_5d + E1_pb_ma20 + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12
    signals["Q496_fvg5_e1_n1"] = (
        signals.get("O1_in_fvg_5d", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q495 [triple]: O1_in_fvg_5d + E1_pb_ma20 + C3_pocket_pivot
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12
    signals["Q495_fvg5_e1_c3"] = (
        signals.get("O1_in_fvg_5d", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("C3_pocket_pivot", False)
    )

    # Q494 [triple]: M5_post_bkt_vdu_expansion + O1_in_fvg_3d + O2_choch_10d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.38%  N=9 (BOTH h3/h5 high!)
    signals["Q494_m5_fvg3_o2"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("O2_choch_10d", False)
    )

    # Q493 [triple]: M5_post_bkt_vdu_expansion + I2_power_move + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.65%  N=9 (BOTH h3/h5 high! HIGH avg!)
    signals["Q493_m5_i2_fvg5"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("I2_power_move", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q492 [triple]: M2_vdu_deep_expansion + I3b_gap_up_strong + P6_td_countdown_8plus
    # ★★★ VALIDATED 2021+: h3=75.0% h5=50.0% avg=+3.12%  N=8 (NOTABLE avg!)
    signals["Q492_m2_i3b_p6"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q491 [triple]: M5_post_bkt_vdu_expansion + M1_vdu_expansion + O1_in_fvg_5d (duplicate of existing — skip)
    # Q490 [triple]: M2_vdu_deep_expansion + M3_vdu_expansion_pb + O1_in_fvg_3d
    # VALIDATED 2021+: h3=77.8% h5=55.6% avg=+1.80%  N=9
    signals["Q490_m2_m3_fvg3"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q489 [triple]: M2_vdu_deep_expansion + I1b_close_quality_hi_vol + P6_td_countdown_8plus
    # VALIDATED 2021+: h3=77.8% h5=44.4% avg=+2.11%  N=9
    signals["Q489_m2_i1b_p6"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("I1b_close_quality_hi_vol", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q488 [triple]: O1_in_fvg_5d + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (LARGE N + HIGH h5!)
    signals["Q488_fvg5_e4_q1"] = (
        signals.get("O1_in_fvg_5d", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q487 [triple]: N5_pp_after_bkt_pb + I4_2down_bounce + D4_pocket_ma50
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q487_n5_i4_d4"] = (
        signals.get("N5_pp_after_bkt_pb", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q486 [triple]: N5_pp_after_bkt_pb + I4_2down_bounce + K1_first_pb_after_bkt
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q486_n5_i4_k1"] = (
        signals.get("N5_pp_after_bkt_pb", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("K1_first_pb_after_bkt", False)
    )

    # Q485 [triple]: N5_pp_after_bkt_pb + I4_2down_bounce + C2_above_ma50
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q485_n5_i4_c2"] = (
        signals.get("N5_pp_after_bkt_pb", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("C2_above_ma50", False)
    )

    # Q484 [triple]: M5_post_bkt_vdu_expansion + C1_early_move + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.65%  N=11
    signals["Q484_m5_c1_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("C1_early_move", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q483 [triple]: M5_post_bkt_vdu_expansion + C2_above_ma50 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=81.2% h5=62.5% avg=+1.97%  N=16 (LARGE N!)
    signals["Q483_m5_c2_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q482 [triple]: M3_vdu_expansion_pb + H1_mom_resume + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.49%  N=11
    signals["Q482_m3_h1_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q481 [triple]: M3_vdu_expansion_pb + C2_above_ma50 + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.33%  N=12
    signals["Q481_m3_c2_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q480 [triple]: M2_vdu_deep_expansion + C2_above_ma50 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.74%  N=11
    signals["Q480_m2_c2_fvg3"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q479 [triple]: M2_vdu_deep_expansion + B3_near_hi20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.88%  N=10
    signals["Q479_m2_b3_fvg3"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q478 [triple]: M2_vdu_deep_expansion + E4_shallow_pb + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.88%  N=10
    signals["Q478_m2_e4_fvg3"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q477 [triple]: M5_post_bkt_vdu_expansion + M1_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=81.2% h5=62.5% avg=+1.97%  N=16 (LARGE N!)
    signals["Q477_m5_m1_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q476 [triple]: I1_close_quality + I3b_gap_up_strong + I5_cap_reversal
    # ★★★★ VALIDATED 2021+: h3=75.0% h5=50.0% avg=+7.02%  N=8 (EXTRAORDINARY avg!)
    signals["Q476_i1_i3b_i5"] = (
        signals.get("I1_close_quality", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q475 [triple]: I1_close_quality + I3b_gap_up_strong + P1_td_setup_count
    # ★★★★ VALIDATED 2021+: h3=75.0% h5=83.3% avg=+6.24%  N=12 (HIGH avg! HIGH h5!)
    signals["Q475_i1_i3b_p1"] = (
        signals.get("I1_close_quality", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q474 [triple]: I1_close_quality + E1_pb_ma20 + I4b_3down_bounce
    # ★★★ VALIDATED 2021+: h3=75.0% h5=75.0% avg=+4.68%  N=8 (HIGH avg!)
    signals["Q474_i1_e1_i4b"] = (
        signals.get("I1_close_quality", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I4b_3down_bounce", False)
    )

    # Q473 [triple]: I1_close_quality + I3b_gap_up_strong + O1_in_fvg_8d (same as Q400)
    # skip — already as Q400

    # Q472 [triple]: I10_top_decile_day + I4b_3down_bounce + K2_first_pb_ma20_above
    # VALIDATED 2021+: h3=76.5% h5=64.7% avg=+1.06%  N=17
    signals["Q472_i10_i4b_k2"] = (
        signals.get("I10_top_decile_day", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("K2_first_pb_ma20_above", False)
    )

    # Q471 [triple]: C1_early_move + J8_pb_zone_ma50 + K4_first_pb_to_ma20
    # VALIDATED 2021+: h3=75.0% h5=50.0% avg=+3.51%  N=8
    signals["Q471_c1_j8_k4"] = (
        signals.get("C1_early_move", False) and
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False)
    )

    # Q470 [triple]: C1_early_move + M1_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=75.0% h5=66.7% avg=+2.12%  N=12
    signals["Q470_c1_m1_fvg3"] = (
        signals.get("C1_early_move", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q469 [triple]: C1_early_move + E4_shallow_pb + Q1_pp_ma20_fvg3d (same as Q413)
    # skip — duplicate

    # Q468 [triple]: C1_early_move + E1_pb_ma20 + I2b_power_move_strong (same as Q450)
    # skip — duplicate

    # Q467 [triple]: C1_early_move + I3b_gap_up_strong + I5_cap_reversal (same as Q393)
    # skip — duplicate

    # Q466 [triple]: I10_top_decile_day + I4b_3down_bounce + N1_pp_at_ma20 (same as Q230)
    # skip — duplicate

    # Q465 [triple]: I10_top_decile_day + G2_nr7 + I4b_3down_bounce (same as Q229)
    # skip — duplicate

    # Q464 [triple]: I10_top_decile_day + I4b_3down_bounce + K1_first_pb_after_bkt (same as Q227)
    # skip — duplicate

    # Q463 [triple]: I1_close_quality + E1_pb_ma20 + I2b_power_move_strong (same as Q390)
    # skip — duplicate

    # Q462 [triple]: E3_mild_pb + C2_above_ma50 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9
    signals["Q462_e3_c2_q1"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q461 [triple]: E3_mild_pb + C3_pocket_pivot + Q1_pp_ma20_fvg3d (same as Q411)
    # skip — already as Q411

    # Q460 [triple]: D4_pocket_ma50 + E3_mild_pb + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9
    signals["Q460_d4_e3_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q459 [triple]: D4_pocket_ma50 + H1_mom_resume + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.5% h5=64.7% avg=+nan%  N=17
    signals["Q459_d4_h1_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q458 [triple]: D4_pocket_ma50 + C2_above_ma50 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.2% h5=66.7% avg=+nan%  N=21 (VERY LARGE N!)
    signals["Q458_d4_c2_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q457 [triple]: D4_pocket_ma50 + C3_pocket_pivot + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.2% h5=66.7% avg=+nan%  N=21 (VERY LARGE N!)
    signals["Q457_d4_c3_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q456 [triple]: D4_pocket_ma50 + N1_pp_at_ma20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=76.2% h5=66.7% avg=+nan%  N=21 (VERY LARGE N!)
    signals["Q456_d4_n1_fvg3"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q455 [triple]: D4_pocket_ma50 + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.2% h5=66.7% avg=+nan%  N=21 (VERY LARGE N!)
    signals["Q455_d4_n1_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q454 [triple]: E1_pb_ma20 + J5_dry_prior_base_hi + P3_td_setup_6plus
    # VALIDATED 2021+: h3=76.5% h5=47.1% avg=+2.21%  N=17
    signals["Q454_e1_j5_p3"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q453 [triple]: E1_pb_ma20 + J_combo_prior_hi + P3_td_setup_6plus
    # VALIDATED 2021+: h3=76.5% h5=47.1% avg=+2.21%  N=17
    signals["Q453_e1_jcombo_p3"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("J_combo_prior_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q452 [triple]: E1_pb_ma20 + A3_vol_surge + I10_top_decile_day (same as Q414)
    # skip — duplicate

    # Q451 [triple]: E1_pb_ma20 + I2b_power_move_strong + I10_top_decile_day (same as Q392)
    # skip — duplicate

    # Q450 [triple]: E1_pb_ma20 + C1_early_move + I2b_power_move_strong
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.65%  N=8
    signals["Q450_e1_c1_i2b"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("C1_early_move", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q449 [triple]: E1_pb_ma20 + J5b_dry_prior_hi_tight + P3_td_setup_6plus
    # VALIDATED 2021+: h3=76.9% h5=46.2% avg=+1.91%  N=13
    signals["Q449_e1_j5b_p3"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("J5b_dry_prior_hi_tight", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q448 [triple]: D4_pocket_ma50 + E4_shallow_pb + Q1_pp_ma20_fvg3d (same as Q287)
    # skip — duplicate

    # Q447 [triple]: C2_above_ma50 + C3_pocket_pivot + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.2% h5=66.7% avg=+nan%  N=21 (VERY LARGE N!)
    signals["Q447_c2_c3_q1"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q446 [triple]: C2_above_ma50 + N1_pp_at_ma20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=76.2% h5=66.7% avg=+nan%  N=21 (VERY LARGE N!)
    signals["Q446_c2_n1_fvg3"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q445 [triple]: C2_above_ma50 + H1_mom_resume + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.5% h5=64.7% avg=+nan%  N=17
    signals["Q445_c2_h1_q1"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q444 [triple]: C2_above_ma50 + E3_mild_pb + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9
    signals["Q444_c2_e3_q1"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q443 [triple]: C2_above_ma50 + M4_vdu_full_compound + O1_in_fvg_3d
    # VALIDATED 2021+: h3=77.8% h5=55.6% avg=+1.80%  N=9
    signals["Q443_c2_m4_fvg3"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("M4_vdu_full_compound", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q442 [triple]: H1_mom_resume + C3_pocket_pivot + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.5% h5=64.7% avg=+nan%  N=17
    signals["Q442_h1_c3_q1"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q441 [triple]: H1_mom_resume + M1_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=76.5% h5=64.7% avg=+2.07%  N=17
    signals["Q441_h1_m1_fvg3"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q440 [triple]: H1_mom_resume + N1_pp_at_ma20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=76.5% h5=64.7% avg=+nan%  N=17
    signals["Q440_h1_n1_fvg3"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q439 [triple]: H1_mom_resume + C2_above_ma50 + Q1_pp_ma20_fvg3d (same as Q445)
    # skip — duplicate

    # Q438 [triple]: C2_above_ma50 + M1_vdu_expansion + O1_in_fvg_3d (same as Q430 from fvg3 backbone)
    # skip — duplicate

    # Q437 [triple]: H1_mom_resume + B3_near_hi20 + Q1_pp_ma20_fvg3d (same as Q435)
    # skip — duplicate

    # Q436 [triple]: H1_mom_resume + D3_near_hi_ma50 + Q1_pp_ma20_fvg3d (same as Q431)
    # skip — duplicate

    # Q435 [triple]: B3_near_hi20 + H1_mom_resume + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.9% h5=69.2% avg=+nan%  N=13
    signals["Q435_b3_h1_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q434 [triple]: B3_near_hi20 + N1_pp_at_ma20 + O1_in_fvg_5d
    # VALIDATED 2021+: h3=75.0% h5=70.0% avg=+nan%  N=20 (VERY LARGE N!)
    signals["Q434_b3_n1_fvg5"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q433 [triple]: B3_near_hi20 + M1_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.71%  N=16 (LARGE N!)
    signals["Q433_b3_m1_fvg3"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q432 [triple]: B3_near_hi20 + M4_vdu_full_compound + O1_in_fvg_3d
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.97%  N=8
    signals["Q432_b3_m4_fvg3"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("M4_vdu_full_compound", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q431 [triple]: D3_near_hi_ma50 + H1_mom_resume + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=76.9% h5=69.2% avg=+nan%  N=13
    signals["Q431_d3_h1_q1"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q430 [triple]: O1_in_fvg_3d + C2_above_ma50 + M1_vdu_expansion
    # VALIDATED 2021+: h3=77.8% h5=61.1% avg=+1.99%  N=18 (LARGE N!)
    signals["Q430_fvg3_c2_m1"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("M1_vdu_expansion", False)
    )

    # Q429 [triple]: O1_in_fvg_3d + M1_vdu_expansion + O1_in_fvg_5d
    # VALIDATED 2021+: h3=77.8% h5=61.1% avg=+1.99%  N=18 (LARGE N!)
    signals["Q429_fvg3_m1_fvg5"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q428 [triple]: O1_in_fvg_3d + M1_vdu_expansion + O1_in_fvg_8d
    # VALIDATED 2021+: h3=77.8% h5=61.1% avg=+1.99%  N=18 (LARGE N!)
    signals["Q428_fvg3_m1_fvg8"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q427 [triple]: O1_in_fvg_3d + C2_above_ma50 + M4_vdu_full_compound
    # VALIDATED 2021+: h3=77.8% h5=55.6% avg=+1.80%  N=9
    signals["Q427_fvg3_c2_m4"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("M4_vdu_full_compound", False)
    )

    # Q426 [triple]: O1_in_fvg_3d + E3_mild_pb + N1_pp_at_ma20
    # VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9
    signals["Q426_fvg3_e3_n1"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q425 [triple]: O1_in_fvg_3d + E3_mild_pb + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9
    signals["Q425_fvg3_e3_q1"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q424 [triple]: N1_pp_at_ma20 + E3_mild_pb + O1_in_fvg_3d (same as Q426)
    # skip — duplicate

    # Q423 [triple]: N1_pp_at_ma20 + E3_mild_pb + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9
    signals["Q423_n1_e3_q1"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q422 [triple]: N1_pp_at_ma20 + E3_mild_pb + I4b_3down_bounce (same as Q228)
    # skip — already as Q228

    # Q421 [triple]: I4_2down_bounce + C3_pocket_pivot + K4_first_pb_to_ma20
    # VALIDATED 2021+: h3=75.0% h5=75.0% avg=+0.98%  N=8
    signals["Q421_i4_c3_k4"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("K4_first_pb_to_ma20", False)
    )

    # Q420 [triple]: I4_2down_bounce + G1_inside_bar + D5_full_setup
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.26%  N=8
    signals["Q420_i4_g1_d5"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("G1_inside_bar", False) and
        signals.get("D5_full_setup", False)
    )

    # Q419 [triple]: I4_2down_bounce + X4_inside_near_hi + D5_full_setup
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.26%  N=8
    signals["Q419_i4_x4_d5"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("D5_full_setup", False)
    )

    # Q418 [triple]: N1_pp_at_ma20 + A3_vol_surge + E3_mild_pb (same as Q217 N1+A3+E3)
    # skip — duplicate

    # Q417 [triple]: C3_pocket_pivot + N1_pp_at_ma20 + O1_in_fvg_3d (same as Q410)
    # skip — duplicate

    # Q416 [triple]: C3_pocket_pivot + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d (same as Q409)
    # skip — duplicate

    # Q415 [triple]: I4_2down_bounce + I4b_3down_bounce + N1_pp_at_ma20 (same as Q226)
    # skip — duplicate

    # Q414 [triple]: A3_vol_surge + E1_pb_ma20 + I10_top_decile_day
    # VALIDATED 2021+: h3=76.9% h5=76.9% avg=+2.44%  N=13
    signals["Q414_a3_e1_i10"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q413 [triple]: E4_shallow_pb + C1_early_move + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.07%  N=9
    signals["Q413_e4_c1_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("C1_early_move", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q412 [triple]: E4_shallow_pb + N1_pp_at_ma20 + O1_in_fvg_5d
    # VALIDATED 2021+: h3=75.0% h5=75.0% avg=+2.06%  N=16 (LARGE N!)
    signals["Q412_e4_n1_fvg5"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q411 [triple]: C3_pocket_pivot + E3_mild_pb + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9
    signals["Q411_c3_e3_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q410 [triple]: C3_pocket_pivot + N1_pp_at_ma20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=77.3% h5=63.6% avg=+nan%  N=22 (VERY LARGE N!)
    signals["Q410_c3_n1_fvg3"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q409 [triple]: C3_pocket_pivot + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.3% h5=63.6% avg=+nan%  N=22 (VERY LARGE N!)
    signals["Q409_c3_n1_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q408 [triple]: C3_pocket_pivot + O1_in_fvg_3d + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.3% h5=63.6% avg=+nan%  N=22 (VERY LARGE N!)
    signals["Q408_c3_fvg3_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q407 [triple]: C3_pocket_pivot + O1_in_fvg_5d + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.3% h5=63.6% avg=+nan%  N=22 (VERY LARGE N!)
    signals["Q407_c3_fvg5_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q406 [triple]: C3_pocket_pivot + O1_in_fvg_8d + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.3% h5=63.6% avg=+nan%  N=22 (VERY LARGE N!)
    signals["Q406_c3_fvg8_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q405 [triple]: A3_vol_surge + E3_mild_pb + N1_pp_at_ma20 (same as Q217/Q208 — N1+A3+E3!)
    # Same combo as existing Q217 — skip duplicate

    # Q404 [triple]: E4_shallow_pb + M4_vdu_full_compound + O1_in_fvg_3d
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+1.97%  N=8
    signals["Q404_e4_m4_fvg3"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("M4_vdu_full_compound", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q403 [triple]: I3b_gap_up_strong + I2b_power_move_strong + I5_cap_reversal (same as Q391)
    # skip — duplicate

    # Q402 [triple]: I3b_gap_up_strong + A2_bkt10 + P6_td_countdown_8plus (same as Q395)
    # skip — duplicate

    # Q401 [triple]: I3b_gap_up_strong + A3_vol_surge + I5_cap_reversal (same as Q397)
    # skip — duplicate

    # Q400 [triple]: I3b_gap_up_strong + I1_close_quality + O1_in_fvg_8d
    # ★★★★★ VALIDATED 2021+: h3=77.8% h5=88.9% avg=+12.95%  N=9 (EXTRAORDINARY avg!)
    signals["Q400_i3b_i1_fvg8"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I1_close_quality", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q399 [triple]: I5_cap_reversal + I3b_gap_up_strong + O2_choch_10d
    # ★★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+6.44%  N=13 (VERY HIGH avg!)
    signals["Q399_i5_i3b_o2"] = (
        signals.get("I5_cap_reversal", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("O2_choch_10d", False)
    )

    # Q398 [triple]: I5_cap_reversal + I3b_gap_up_strong + I10_top_decile_day
    # ★★★ VALIDATED 2021+: h3=75.0% h5=62.5% avg=+5.94%  N=16 (LARGE N! HIGH avg!)
    signals["Q398_i5_i3b_i10"] = (
        signals.get("I5_cap_reversal", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q397 [triple]: I3b_gap_up_strong + A3_vol_surge + I5_cap_reversal
    # ★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+5.34%  N=13 (HIGH avg!)
    signals["Q397_i3b_a3_i5"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q396 [triple]: I3b_gap_up_strong + A1_bkt20_vol + P6_td_countdown_8plus
    # ★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+4.54%  N=13 (HIGH avg!)
    signals["Q396_i3b_a1_p6"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("A1_bkt20_vol", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q395 [triple]: I3b_gap_up_strong + A2_bkt10 + P6_td_countdown_8plus
    # ★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+4.54%  N=13
    signals["Q395_i3b_a2_p6"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("A2_bkt10", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q394 [triple]: I3b_gap_up_strong + H2_new10d_hi + P6_td_countdown_8plus
    # ★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+4.54%  N=13
    signals["Q394_i3b_h2_p6"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("H2_new10d_hi", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q393 [triple]: I5_cap_reversal + C1_early_move + I3b_gap_up_strong
    # VALIDATED 2021+: h3=75.0% h5=87.5% avg=+3.30%  N=8 (HIGH h5!)
    signals["Q393_i5_c1_i3b"] = (
        signals.get("I5_cap_reversal", False) and
        signals.get("C1_early_move", False) and
        signals.get("I3b_gap_up_strong", False)
    )

    # Q392 [triple]: I2b_power_move_strong + E1_pb_ma20 + I10_top_decile_day
    # VALIDATED 2021+: h3=77.8% h5=66.7% avg=+3.18%  N=9
    signals["Q392_i2b_e1_i10"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q391 [triple]: I2b_power_move_strong + I3b_gap_up_strong + I5_cap_reversal
    # ★★★ VALIDATED 2021+: h3=76.9% h5=61.5% avg=+5.34%  N=13 (HIGH avg!)
    signals["Q391_i2b_i3b_i5"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q390 [triple]: I2b_power_move_strong + E1_pb_ma20 + I1_close_quality
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+3.24%  N=8
    signals["Q390_i2b_e1_i1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I1_close_quality", False)
    )

    # Q389 [triple]: I2b_power_move_strong + E1_pb_ma20 + I1b_close_quality_hi_vol
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+3.24%  N=8
    signals["Q389_i2b_e1_i1b"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I1b_close_quality_hi_vol", False)
    )

    # Q388 [triple]: I5_cap_reversal + G2_nr7 + P1_td_setup_count (same as Q238)
    # skip — already as Q238

    # Q387 [triple]: I3b_gap_up_strong + I2b_power_move_strong + I5_cap_reversal (same as Q391)
    # skip — duplicate

    # Q386 [triple]: I4_2down_bounce + C2_above_ma50 + N5_pp_after_bkt_pb
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q386_i4_c2_n5"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q385 [triple]: A3_vol_surge + I4_2down_bounce + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+3.39%  N=8 (HIGH avg!)
    signals["Q385_a3_i4_n1"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q384 [triple]: N1_pp_at_ma20 + E4_shallow_pb + O1_in_fvg_3d (same as Q264)
    # skip — duplicate

    # Q383 [triple]: N1_pp_at_ma20 + E4_shallow_pb + Q1_pp_ma20_fvg3d (same as Q263)
    # skip — duplicate

    # Q382 [triple]: N1_pp_at_ma20 + B3_near_hi20 + O1_in_fvg_3d (same as Q305)
    # skip — duplicate

    # Q381 [triple]: N1_pp_at_ma20 + B3_near_hi20 + Q1_pp_ma20_fvg3d (same as Q304)
    # skip — duplicate

    # Q380 [triple]: M1_vdu_expansion + E4_shallow_pb + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=66.7% avg=+2.08%  N=15 (LARGE N!)
    signals["Q380_m1_e4_fvg3"] = (
        signals.get("M1_vdu_expansion", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q379 [triple]: H1_mom_resume + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.58%  N=11
    signals["Q379_h1_e4_q1"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q378 [triple]: H1_mom_resume + M5_post_bkt_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=66.7% avg=+2.06%  N=15 (LARGE N!)
    signals["Q378_h1_m5_fvg3"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q377 [triple]: C2_above_ma50 + B3_near_hi20 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=80.0% h5=73.3% avg=+nan%  N=15 (LARGE N!)
    signals["Q377_c2_b3_q1"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q376 [triple]: C2_above_ma50 + D3_near_hi_ma50 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=80.0% h5=73.3% avg=+nan%  N=15 (LARGE N!)
    signals["Q376_c2_d3_q1"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("D3_near_hi_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q375 [triple]: C2_above_ma50 + E1_pb_ma20 + I2b_power_move_strong
    # VALIDATED 2021+: h3=80.0% h5=70.0% avg=+3.08%  N=10
    signals["Q375_c2_e1_i2b"] = (
        signals.get("C2_above_ma50", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q374 [triple]: C2_above_ma50 + I4_2down_bounce + N5_pp_after_bkt_pb (same as Q269)
    # skip — duplicate as Q269

    # Q373 [triple]: C2_above_ma50 + E4_shallow_pb + Q1_pp_ma20_fvg3d (same as Q265)
    # skip — duplicate

    # Q372 [triple]: H1_mom_resume + M3_vdu_expansion_pb + O1_in_fvg_3d (same as Q349)
    # skip — duplicate

    # Q371 [triple]: M2_vdu_deep_expansion + C2_above_ma50 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.74%  N=11
    signals["Q371_m2_c2_fvg3"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q370 [triple]: M2_vdu_deep_expansion + M1_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.74%  N=11
    signals["Q370_m2_m1_fvg3"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q369 [triple]: M2_vdu_deep_expansion + O1_in_fvg_3d + O1_in_fvg_5d
    # VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.74%  N=11
    signals["Q369_m2_fvg3_fvg5"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q368 [triple]: M2_vdu_deep_expansion + O1_in_fvg_3d + O1_in_fvg_8d
    # VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.74%  N=11
    signals["Q368_m2_fvg3_fvg8"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q367 [triple]: M2_vdu_deep_expansion + H1_mom_resume + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.86%  N=10
    signals["Q367_m2_h1_fvg3"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q366 [triple]: M2_vdu_deep_expansion + B3_near_hi20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.88%  N=10
    signals["Q366_m2_b3_fvg3"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q365 [triple]: M2_vdu_deep_expansion + E4_shallow_pb + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.88%  N=10
    signals["Q365_m2_e4_fvg3"] = (
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q364 [triple]: O1_in_fvg_5d + M2_vdu_deep_expansion + O1_in_fvg_3d (same as Q369)
    # skip — duplicate

    # Q363 [triple]: O1_in_fvg_5d + M3_vdu_expansion_pb + O1_in_fvg_3d (same as Q337)
    # skip — duplicate

    # Q362 [triple]: M2_vdu_deep_expansion + M5_post_bkt_vdu_expansion + O1_in_fvg_3d (same as Q350)
    # skip — duplicate

    # Q361 [triple]: O1_in_fvg_5d + I9_inside_close_hi + J9_3dry_at_ma20 (same as Q319)
    # skip — duplicate

    # Q360 [triple]: O1_in_fvg_5d + E1_pb_ma20 + N1_pp_at_ma20 (same as Q306)
    # skip — duplicate

    # Q359 [triple]: O1_in_fvg_5d + C3_pocket_pivot + E1_pb_ma20 (same as Q248)
    # skip — duplicate

    # Q358 [triple]: O1_in_fvg_5d + E1_pb_ma20 + D4_pocket_ma50 (same as Q286)
    # skip — duplicate

    # Q357 [triple]: O1_in_fvg_5d + E4_shallow_pb + Q1_pp_ma20_fvg3d (same as Q261)
    # skip — duplicate

    # Q356 [triple]: O1_in_fvg_5d + B3_near_hi20 + Q1_pp_ma20_fvg3d (same as Q302)
    # skip — duplicate

    # Q355 [triple]: M5_post_bkt_vdu_expansion + C2_above_ma50 + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=81.2% h5=62.5% avg=+1.97%  N=16 (LARGE N!)
    signals["Q355_m5_c2_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q354 [triple]: M5_post_bkt_vdu_expansion + M1_vdu_expansion + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=81.2% h5=62.5% avg=+1.97%  N=16 (LARGE N!)
    signals["Q354_m5_m1_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q353 [triple]: M5_post_bkt_vdu_expansion + O1_in_fvg_3d + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=81.2% h5=62.5% avg=+1.97%  N=16 (LARGE N!)
    signals["Q353_m5_fvg3_fvg5"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q352 [triple]: M5_post_bkt_vdu_expansion + O1_in_fvg_3d + O1_in_fvg_8d
    # ★★★ VALIDATED 2021+: h3=81.2% h5=62.5% avg=+1.97%  N=16 (LARGE N!)
    signals["Q352_m5_fvg3_fvg8"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q351 [triple]: M5_post_bkt_vdu_expansion + B3_near_hi20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=66.7% avg=+2.08%  N=15 (LARGE N!)
    signals["Q351_m5_b3_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q350 [triple]: M5_post_bkt_vdu_expansion + M2_vdu_deep_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.74%  N=11
    signals["Q350_m5_m2_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q349 [triple]: M3_vdu_expansion_pb + H1_mom_resume + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.49%  N=11
    signals["Q349_m3_h1_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q348 [triple]: M3_vdu_expansion_pb + M5_post_bkt_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=81.8% h5=63.6% avg=+1.82%  N=11
    signals["Q348_m3_m5_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q347 [triple]: M3_vdu_expansion_pb + B3_near_hi20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=70.0% avg=+1.97%  N=10
    signals["Q347_m3_b3_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q346 [triple]: M3_vdu_expansion_pb + E4_shallow_pb + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=70.0% avg=+1.97%  N=10
    signals["Q346_m3_e4_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q345 [triple]: M3_vdu_expansion_pb + O1_in_fvg_3d + O1_in_fvg_5d (same as Q337)
    # skip — duplicate

    # Q344 [triple]: M3_vdu_expansion_pb + O1_in_fvg_3d + O1_in_fvg_8d (same as Q336)
    # skip — duplicate

    # Q343 [triple]: M3_vdu_expansion_pb + M1_vdu_expansion + O1_in_fvg_3d (same as Q338)
    # skip — duplicate

    # Q342 [triple]: M3_vdu_expansion_pb + C2_above_ma50 + O1_in_fvg_3d (same as Q339)
    # skip — duplicate

    # Q341 [triple]: M5_post_bkt_vdu_expansion + M3_vdu_expansion_pb + O1_in_fvg_3d (same as Q348)
    # skip — duplicate

    # Q340 [triple]: O1_in_fvg_8d + A3_vol_surge + I4_2down_bounce
    # ★★★★★ VALIDATED 2021+: h3=100.0% h5=87.5% avg=+5.00%  N=8 (PERFECT h3!)
    signals["Q340_fvg8_a3_i4"] = (
        signals.get("O1_in_fvg_8d", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q339 [triple]: O1_in_fvg_3d + C2_above_ma50 + M3_vdu_expansion_pb
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.33%  N=12
    signals["Q339_fvg3_c2_m3"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("M3_vdu_expansion_pb", False)
    )

    # Q338 [triple]: O1_in_fvg_3d + M1_vdu_expansion + M3_vdu_expansion_pb
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.33%  N=12
    signals["Q338_fvg3_m1_m3"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("M3_vdu_expansion_pb", False)
    )

    # Q337 [triple]: O1_in_fvg_3d + M3_vdu_expansion_pb + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.33%  N=12
    signals["Q337_fvg3_m3_fvg5"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q336 [triple]: O1_in_fvg_3d + M3_vdu_expansion_pb + O1_in_fvg_8d
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.33%  N=12
    signals["Q336_fvg3_m3_fvg8"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q335 [triple]: O1_in_fvg_3d + C1_early_move + M5_post_bkt_vdu_expansion
    # ★★★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.65%  N=11
    signals["Q335_fvg3_c1_m5"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("C1_early_move", False) and
        signals.get("M5_post_bkt_vdu_expansion", False)
    )

    # Q334 [triple]: O1_in_fvg_3d + C2_above_ma50 + M2_vdu_deep_expansion
    # VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.74%  N=11
    signals["Q334_fvg3_c2_m2"] = (
        signals.get("O1_in_fvg_3d", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("M2_vdu_deep_expansion", False)
    )

    # Q333 [triple]: O1_in_fvg_8d + M3_vdu_expansion_pb + O1_in_fvg_3d (same as Q336)
    # skip — duplicate

    # Q332 [triple]: O1_in_fvg_8d + M2_vdu_deep_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.74%  N=11
    signals["Q332_fvg8_m2_fvg3"] = (
        signals.get("O1_in_fvg_8d", False) and
        signals.get("M2_vdu_deep_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q331 [triple]: O1_in_fvg_8d + M5_post_bkt_vdu_expansion + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=81.2% h5=62.5% avg=+1.97%  N=16 (LARGE N!)
    signals["Q331_fvg8_m5_fvg3"] = (
        signals.get("O1_in_fvg_8d", False) and
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q325 [triple]: J_combo_prior_hi + F1_vol_dry + J8_pb_zone_ma50 (same as Q318 from J8 backbone)
    # skip — same combo, already as Q318

    # Q324 [triple]: F2_dry_after_surge + F1_vol_dry + J6_dry_near_swing_lo
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=100.0% avg=+2.74%  N=8 (PERFECT h5!)
    signals["Q324_f2_f1_j6"] = (
        signals.get("F2_dry_after_surge", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J6_dry_near_swing_lo", False)
    )

    # Q323 [triple]: Q3_inside_dry_td6 + F2_dry_after_surge + I4_2down_bounce
    # ★★★★ VALIDATED 2021+: h3=88.9% h5=88.9% avg=+4.46%  N=9 (BOTH high + high avg!)
    signals["Q323_q3_f2_i4"] = (
        signals.get("Q3_inside_dry_td6", False) and
        signals.get("F2_dry_after_surge", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q322 [triple]: X7_pb_dry_ma50 + K4_first_pb_to_ma20 + Q2_pb_inside_td6
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+2.37%  N=15 (LARGE N!)
    signals["Q322_x7_k4_q2"] = (
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("Q2_pb_inside_td6", False)
    )

    # Q321 [triple]: X7_pb_dry_ma50 + K4_first_pb_to_ma20 + Q3_inside_dry_td6
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+2.37%  N=15 (LARGE N!)
    signals["Q321_x7_k4_q3"] = (
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("Q3_inside_dry_td6", False)
    )

    # Q320 [triple]: J9_3dry_at_ma20 + J_combo_ma50_trend + O1_in_fvg_8d
    # ★★★ VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.09%  N=11
    signals["Q320_j9_jcombo_fvg8"] = (
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("J_combo_ma50_trend", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q319 [triple]: J9_3dry_at_ma20 + I9_inside_close_hi + O1_in_fvg_5d
    # ★★★★ VALIDATED 2021+: h3=88.9% h5=55.6% avg=+1.58%  N=9 (HIGH h3!)
    signals["Q319_j9_i9_fvg5"] = (
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("I9_inside_close_hi", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q318 [triple]: J8_pb_zone_ma50 + F1_vol_dry + J_combo_prior_hi
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q318_j8_f1_jcombo"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q317 [triple]: J8_pb_zone_ma50 + F5_dry_ma50 + J_combo_prior_hi
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q317_j8_f5_jcombo"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("F5_dry_ma50", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q316 [triple]: J8_pb_zone_ma50 + X7_pb_dry_ma50 + J_combo_prior_hi
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q316_j8_x7_jcombo"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q315 [triple]: I9_inside_close_hi + J9_3dry_at_ma20 + O1_in_fvg_5d (same as Q319)
    # skip — already as Q319

    # Q314 [triple]: J8_pb_zone_ma50 + F5_dry_ma50 + J5_dry_prior_base_hi (same as Q281)
    # skip — already as Q281

    # Q313 [triple]: J8_pb_zone_ma50 + F1_vol_dry + J5_dry_prior_base_hi (same as Q291)
    # skip — already as Q291

    # Q312 [triple]: J8_pb_zone_ma50 + X7_pb_dry_ma50 + J5_dry_prior_base_hi (same as Q290)
    # skip — already as Q290

    # Q311 [triple]: X7_pb_dry_ma50 + J8_pb_zone_ma50 + J_combo_prior_hi (same as Q316)
    # skip — duplicate

    # Q310 [triple]: E1_pb_ma20 + A3_vol_surge + O1_in_fvg_8d
    # VALIDATED 2021+: h3=80.0% h5=73.3% avg=+1.38%  N=15 (LARGE N!)
    signals["Q310_e1_a3_fvg8"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q309 [triple]: E1_pb_ma20 + I2b_power_move_strong + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q309_e1_i2b_n1"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q308 [triple]: E1_pb_ma20 + A3_vol_surge + I2b_power_move_strong
    # ★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q308_e1_a3_i2b"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q307 [triple]: E1_pb_ma20 + I2_power_move + I2b_power_move_strong
    # ★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q307_e1_i2_i2b"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("I2_power_move", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q306 [triple]: E1_pb_ma20 + N1_pp_at_ma20 + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12
    signals["Q306_e1_n1_fvg5"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q305 [triple]: B3_near_hi20 + N1_pp_at_ma20 + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=81.2% h5=68.8% avg=+nan%  N=16 (LARGE N!)
    signals["Q305_b3_n1_fvg3"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q304 [triple]: B3_near_hi20 + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=81.2% h5=68.8% avg=+nan%  N=16 (LARGE N!)
    signals["Q304_b3_n1_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q303 [triple]: B3_near_hi20 + O1_in_fvg_3d + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=81.2% h5=68.8% avg=+nan%  N=16 (LARGE N!)
    signals["Q303_b3_fvg3_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q302 [triple]: B3_near_hi20 + O1_in_fvg_5d + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=81.2% h5=68.8% avg=+nan%  N=16 (LARGE N!)
    signals["Q302_b3_fvg5_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q301 [triple]: B3_near_hi20 + O1_in_fvg_8d + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=81.2% h5=68.8% avg=+nan%  N=16 (LARGE N!)
    signals["Q301_b3_fvg8_q1"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q300 [triple]: B3_near_hi20 + C3_pocket_pivot + Q1_pp_ma20_fvg3d (same as Q246)
    # skip — already as Q246

    # Q299 [triple]: B3_near_hi20 + O2_choch_10d + Q1_pp_ma20_fvg3d (same as Q274)
    # skip — already as Q274

    # Q298 [triple]: B3_near_hi20 + E4_shallow_pb + Q1_pp_ma20_fvg3d (same as Q266)
    # skip — already as Q266

    # Q297 [triple]: E1_pb_ma20 + C3_pocket_pivot + O1_in_fvg_5d (same as Q248)
    # skip — already as Q248

    # Q296 [triple]: I8_3up_days + I2b_power_move_strong + I9_inside_close_hi
    # VALIDATED 2021+: h3=80.0% h5=70.0% avg=+2.45%  N=10
    signals["Q296_i8_i2b_i9"] = (
        signals.get("I8_3up_days", False) and
        signals.get("I2b_power_move_strong", False) and
        signals.get("I9_inside_close_hi", False)
    )

    # Q295 [triple]: H2_new10d_hi + A3_vol_surge + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.47%  N=8
    signals["Q295_h2_a3_n1"] = (
        signals.get("H2_new10d_hi", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q294 [triple]: I10_top_decile_day + X4_inside_near_hi + J5_dry_prior_base_hi
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+5.07%  N=8 (HIGH avg!)
    signals["Q294_i10_x4_j5"] = (
        signals.get("I10_top_decile_day", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q293 [triple]: X4_inside_near_hi + A3_vol_surge + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.93%  N=10
    signals["Q293_x4_a3_fvg3"] = (
        signals.get("X4_inside_near_hi", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q292 [triple]: X4_inside_near_hi + A3_vol_surge + O1_in_fvg_5d
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.93%  N=10
    signals["Q292_x4_a3_fvg5"] = (
        signals.get("X4_inside_near_hi", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q291 [triple]: J5_dry_prior_base_hi + F1_vol_dry + J8_pb_zone_ma50
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q291_j5_f1_j8"] = (
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q290 [triple]: J5_dry_prior_base_hi + X7_pb_dry_ma50 + J8_pb_zone_ma50
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q290_j5_x7_j8"] = (
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q289 [triple]: D4_pocket_ma50 + I3b_gap_up_strong + I5_cap_reversal
    # ★★★★ VALIDATED 2021+: h3=88.9% h5=44.4% avg=+6.29%  N=9 (EXTRAORDINARY avg!)
    signals["Q289_d4_i3b_i5"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q288 [triple]: D4_pocket_ma50 + I4_2down_bounce + N5_pp_after_bkt_pb
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q288_d4_i4_n5"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q287 [triple]: D4_pocket_ma50 + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13
    signals["Q287_d4_e4_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q286 [triple]: D4_pocket_ma50 + E1_pb_ma20 + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12
    signals["Q286_d4_e1_fvg5"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q285 [triple]: D4_pocket_ma50 + B3_near_hi20 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=80.0% h5=73.3% avg=+nan%  N=15 (LARGE N!)
    signals["Q285_d4_b3_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q284 [triple]: D4_pocket_ma50 + D3_near_hi_ma50 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=80.0% h5=73.3% avg=+nan%  N=15 (LARGE N!)
    signals["Q284_d4_d3_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("D3_near_hi_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q283 [triple]: D4_pocket_ma50 + E1_pb_ma20 + I2b_power_move_strong
    # VALIDATED 2021+: h3=80.0% h5=70.0% avg=+3.08%  N=10
    signals["Q283_d4_e1_i2b"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q282 [triple]: D4_pocket_ma50 + I4_2down_bounce + K1_first_pb_after_bkt
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q282_d4_i4_k1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("K1_first_pb_after_bkt", False)
    )

    # Q281 [triple]: J5_dry_prior_base_hi + F5_dry_ma50 + J8_pb_zone_ma50
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q281_j5_f5_j8"] = (
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("F5_dry_ma50", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q280 [triple]: X4_inside_near_hi + A3_vol_surge + O1_in_fvg_8d
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.93%  N=10
    signals["Q280_x4_a3_fvg8"] = (
        signals.get("X4_inside_near_hi", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q279 [triple]: I10_top_decile_day + X4_inside_near_hi + J_combo_prior_hi
    # ★★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+5.07%  N=8 (HIGH avg!)
    signals["Q279_i10_x4_jcombo"] = (
        signals.get("I10_top_decile_day", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q278 [triple]: D5_full_setup + E3_mild_pb + O1_in_fvg_5d (already as Q252 from E3 side)
    # VALIDATED 2021+: h3=80.0% h5=70.0% avg=+1.89%  N=10
    signals["Q278_d5_e3_fvg5"] = (
        signals.get("D5_full_setup", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q277 [triple]: J5_dry_prior_base_hi + X4_inside_near_hi + I10_top_decile_day (same as Q294)
    # skip — duplicate

    # Q276 [triple]: E4_shallow_pb + C3_pocket_pivot + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13
    signals["Q276_e4_c3_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q275 [triple]: E4_shallow_pb + O1_in_fvg_8d + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13
    signals["Q275_e4_fvg8_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q274 [triple]: O2_choch_10d + B3_near_hi20 + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=83.3% h5=75.0% avg=+nan%  N=12
    signals["Q274_o2_b3_q1"] = (
        signals.get("O2_choch_10d", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q273 [triple]: O2_choch_10d + D3_near_hi_ma50 + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=81.8% h5=81.8% avg=+nan%  N=11 (BOTH 81%!)
    signals["Q273_o2_d3_q1"] = (
        signals.get("O2_choch_10d", False) and
        signals.get("D3_near_hi_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q272 [triple]: N5_pp_after_bkt_pb + I4_2down_bounce + K1_first_pb_after_bkt (duplicate Q244 constellation)
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q272_n5_i4_k1"] = (
        signals.get("N5_pp_after_bkt_pb", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("K1_first_pb_after_bkt", False)
    )

    # Q271 [triple]: N5_pp_after_bkt_pb + C3_pocket_pivot + I4_2down_bounce (note: already as Q250 from C3 backbone)
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q271_n5_c3_i4"] = (
        signals.get("N5_pp_after_bkt_pb", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q270 [triple]: A2_bkt10 + A3_vol_surge + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.47%  N=8
    signals["Q270_a2_a3_n1"] = (
        signals.get("A2_bkt10", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q269 [triple]: N5_pp_after_bkt_pb + C2_above_ma50 + I4_2down_bounce
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q269_n5_c2_i4"] = (
        signals.get("N5_pp_after_bkt_pb", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q268 [triple]: N5_pp_after_bkt_pb + I4_2down_bounce + D4_pocket_ma50
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q268_n5_i4_d4"] = (
        signals.get("N5_pp_after_bkt_pb", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q267 [triple]: E4_shallow_pb + O2_choch_10d + Q1_pp_ma20_fvg3d
    # ★★★★ VALIDATED 2021+: h3=88.9% h5=88.9% avg=+2.54%  N=9 (BOTH high!)
    signals["Q267_e4_o2_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("O2_choch_10d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q266 [triple]: E4_shallow_pb + B3_near_hi20 + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13
    signals["Q266_e4_b3_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q265 [triple]: E4_shallow_pb + C2_above_ma50 + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13
    signals["Q265_e4_c2_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q264 [triple]: E4_shallow_pb + N1_pp_at_ma20 + O1_in_fvg_3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13
    signals["Q264_e4_n1_fvg3"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q263 [triple]: E4_shallow_pb + N1_pp_at_ma20 + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13
    signals["Q263_e4_n1_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q262 [triple]: E4_shallow_pb + O1_in_fvg_3d + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13
    signals["Q262_e4_fvg3_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_3d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q261 [triple]: E4_shallow_pb + O1_in_fvg_5d + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13
    signals["Q261_e4_fvg5_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q254 [triple]: E3_mild_pb + I2b_power_move_strong + N1_pp_at_ma20
    # ★★★★ VALIDATED 2021+: h3=88.9% h5=77.8% avg=+3.68%  N=9 (VERY HIGH h3!)
    signals["Q254_e3_i2b_n1"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("I2b_power_move_strong", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q253 [triple]: E3_mild_pb + I4_2down_bounce + N5_pp_after_bkt_pb
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q253_e3_i4_n5"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q252 [triple]: E3_mild_pb + O1_in_fvg_5d + D5_full_setup
    # VALIDATED 2021+: h3=80.0% h5=70.0% avg=+1.89%  N=10
    signals["Q252_e3_fvg5_d5"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("D5_full_setup", False)
    )

    # Q251 [triple]: C3_pocket_pivot + I3b_gap_up_strong + I5_cap_reversal
    # ★★★★ VALIDATED 2021+: h3=88.9% h5=44.4% avg=+6.29%  N=9 (EXTRAORDINARY avg!)
    signals["Q251_c3_i3b_i5"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q250 [triple]: C3_pocket_pivot + I4_2down_bounce + N5_pp_after_bkt_pb
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q250_c3_i4_n5"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q249 [triple]: C3_pocket_pivot + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★★★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13
    signals["Q249_c3_e4_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q248 [triple]: C3_pocket_pivot + E1_pb_ma20 + O1_in_fvg_5d
    # ★★★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12
    signals["Q248_c3_e1_fvg5"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q247 [triple]: C3_pocket_pivot + E1_pb_ma20 + I2b_power_move_strong
    # ★★★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q247_c3_e1_i2b"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q246 [triple]: C3_pocket_pivot + B3_near_hi20 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=81.2% h5=68.8% avg=+nan%  N=16 (LARGE N!)
    signals["Q246_c3_b3_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q245 [triple]: C3_pocket_pivot + D3_near_hi_ma50 + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=80.0% h5=73.3% avg=+nan%  N=15 (LARGE N!)
    signals["Q245_c3_d3_q1"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("D3_near_hi_ma50", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q244 [triple]: K1_first_pb_after_bkt + C3_pocket_pivot + I4_2down_bounce
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q244_k1_c3_i4"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q243 [triple]: K1_first_pb_after_bkt + I4_2down_bounce + N5_pp_after_bkt_pb
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q243_k1_i4_n5"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q242 [triple]: K1_first_pb_after_bkt + I4_2down_bounce + D4_pocket_ma50
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q242_k1_i4_d4"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q241 [triple]: F3_3day_dry + A2_bkt10 + I8_3up_days
    # ★★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+4.63%  N=9 (HIGH avg!)
    signals["Q241_f3_a2_i8"] = (
        signals.get("F3_3day_dry", False) and
        signals.get("A2_bkt10", False) and
        signals.get("I8_3up_days", False)
    )

    # Q240 [triple]: F3_3day_dry + H2_new10d_hi + I8_3up_days
    # ★★★ VALIDATED 2021+: h3=77.8% h5=66.7% avg=+4.63%  N=9 (HIGH avg!)
    signals["Q240_f3_h2_i8"] = (
        signals.get("F3_3day_dry", False) and
        signals.get("H2_new10d_hi", False) and
        signals.get("I8_3up_days", False)
    )

    # Q239 [triple]: F3_3day_dry + K1_first_pb_after_bkt + P6_td_countdown_8plus
    # VALIDATED 2021+: h3=75.0% h5=87.5% avg=+3.85%  N=8 (HIGH h5!)
    signals["Q239_f3_k1_p6"] = (
        signals.get("F3_3day_dry", False) and
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("P6_td_countdown_8plus", False)
    )

    # Q238 [triple]: G2_nr7 + I5_cap_reversal + P1_td_setup_count
    # VALIDATED 2021+: h3=77.8% h5=55.6% avg=+3.13%  N=9
    signals["Q238_g2_i5_p1"] = (
        signals.get("G2_nr7", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q221 [triple]: K1_first_pb_after_bkt + I4b_3down_bounce + J5_dry_prior_base_hi
    # VALIDATED 2021+: h3=75.0% h5=66.7% avg=+2.25%  N=12
    signals["Q221_k1_i4b_j5"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q219 [triple]: M1_vdu_expansion + C2_above_ma50 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=77.8% h5=61.1% avg=+1.99%  N=18 (LARGE N!)
    signals["Q219_m1_c2_fvg3"] = (
        signals.get("M1_vdu_expansion", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q218 [triple]: N1_pp_at_ma20 + E3_mild_pb + O1_in_fvg_3d
    # VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9
    signals["Q218_n1_e3_fvg3"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q217 [triple]: N1_pp_at_ma20 + A3_vol_surge + E3_mild_pb
    # VALIDATED 2021+: h3=78.9% h5=68.4% avg=+2.71%  N=19 (LARGE N!)
    signals["Q217_n1_a3_e3"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("E3_mild_pb", False)
    )

    # Q216 [triple]: H1_mom_resume + M1_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=76.5% h5=64.7% avg=+2.07%  N=17 (LARGE N!)
    signals["Q216_h1_m1_fvg3"] = (
        signals.get("H1_mom_resume", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q215 [triple]: C1_early_move + M1_vdu_expansion + O1_in_fvg_3d
    # VALIDATED 2021+: h3=75.0% h5=66.7% avg=+2.12%  N=12
    signals["Q215_c1_m1_fvg3"] = (
        signals.get("C1_early_move", False) and
        signals.get("M1_vdu_expansion", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q214 [triple]: B3_near_hi20 + N1_pp_at_ma20 + O1_in_fvg_5d
    # VALIDATED 2021+: h3=75.0% h5=70.0% avg=+nan%  N=20 (LARGE N!)
    signals["Q214_b3_n1_fvg5"] = (
        signals.get("B3_near_hi20", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q213 [triple]: E4_shallow_pb + C1_early_move + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.07%  N=9
    signals["Q213_e4_c1_q1"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("C1_early_move", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q212 [triple]: E4_shallow_pb + N1_pp_at_ma20 + O1_in_fvg_5d
    # VALIDATED 2021+: h3=75.0% h5=75.0% avg=+2.06%  N=16 (LARGE N! both h3+h5 equal)
    signals["Q212_e4_n1_fvg5"] = (
        signals.get("E4_shallow_pb", False) and
        signals.get("N1_pp_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q211 [triple]: A3_vol_surge + E1_pb_ma20 + I10_top_decile_day
    # VALIDATED 2021+: h3=76.9% h5=76.9% avg=+2.44%  N=13
    signals["Q211_a3_e1_i10"] = (
        signals.get("A3_vol_surge", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q210 [triple]: N1_pp_at_ma20 + E3_mild_pb + I4b_3down_bounce
    # VALIDATED 2021+: h3=77.8% h5=77.8% avg=+2.35%  N=9
    signals["Q210_n1_e3_i4b"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("I4b_3down_bounce", False)
    )

    # Q209 [triple]: J3_dry_near_ma50 + F1_vol_dry + J5_dry_prior_base_hi
    # VALIDATED 2021+: h3=75.0% h5=68.8% avg=+2.11%  N=16 (LARGE N!)
    signals["Q209_j3_f1_j5"] = (
        signals.get("J3_dry_near_ma50", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q208 [triple]: N1_pp_at_ma20 + A3_vol_surge + E3_mild_pb
    # VALIDATED 2021+: h3=78.9% h5=68.4% avg=+2.71%  N=19 (LARGE N! core triple)
    signals["Q208_n1_a3_e3"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("E3_mild_pb", False)
    )

    # Q207 [triple]: F1_vol_dry + K4_first_pb_to_ma20 + Q2_pb_inside_td6
    # VALIDATED 2021+: h3=76.7% h5=70.0% avg=+3.17%  N=30 (VERY LARGE N! dry pullback + td6)
    signals["Q207_f1_k4_q2"] = (
        signals.get("F1_vol_dry", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("Q2_pb_inside_td6", False)
    )

    # Q206 [triple]: F2_dry_after_surge + I4b_3down_bounce + P3_td_setup_6plus
    # VALIDATED 2021+: h3=75.0% h5=66.7% avg=+2.83%  N=12
    signals["Q206_f2_i4b_p3"] = (
        signals.get("F2_dry_after_surge", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q205 [triple]: I5_cap_reversal + C1_early_move + I3b_gap_up_strong
    # VALIDATED 2021+: h3=75.0% h5=87.5% avg=+3.30%  N=8 (h5 exceptional!)
    signals["Q205_i5_c1_i3b"] = (
        signals.get("I5_cap_reversal", False) and
        signals.get("C1_early_move", False) and
        signals.get("I3b_gap_up_strong", False)
    )

    # Q204 [triple]: I5_cap_reversal + I3b_gap_up_strong + I10_top_decile_day
    # VALIDATED 2021+: h3=75.0% h5=62.5% avg=+5.94%  N=16 (LARGE N! exceptional avg)
    signals["Q204_i5_i3b_i10"] = (
        signals.get("I5_cap_reversal", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q203 [triple]: I3b_gap_up_strong + I5_cap_reversal + O2_choch_10d
    # VALIDATED 2021+: h3=76.9% h5=61.5% avg=+6.44%  N=13 (exceptional avg)
    signals["Q203_i3b_i5_o2"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("O2_choch_10d", False)
    )

    # Q202 [triple]: P1_td_setup_count + I1_close_quality + I3b_gap_up_strong
    # VALIDATED 2021+: h3=75.0% h5=83.3% avg=+6.24%  N=12 (h5 exceptional, large N)
    signals["Q202_p1_i1_i3b"] = (
        signals.get("P1_td_setup_count", False) and
        signals.get("I1_close_quality", False) and
        signals.get("I3b_gap_up_strong", False)
    )

    # Q201 [triple]: I3b_gap_up_strong + I1_close_quality + O1_in_fvg_8d
    # ★★★ VALIDATED 2021+: h3=77.8% h5=88.9% avg=+12.95%  N=9 (EXTRAORDINARY avg!)
    signals["Q201_i3b_i1_fvg8"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I1_close_quality", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q200 [triple]: F2_dry_after_surge + I9_inside_close_hi + P3_td_setup_6plus
    # VALIDATED 2021+: h3=76.5% h5=76.5% avg=+5.24%  N=17 (LARGE N! both h3+h5 good, exceptional avg)
    signals["Q200_f2_i9_p3"] = (
        signals.get("F2_dry_after_surge", False) and
        signals.get("I9_inside_close_hi", False) and
        signals.get("P3_td_setup_6plus", False)
    )

    # Q199 [triple]: I3b_gap_up_strong + A3_vol_surge + I5_cap_reversal
    # VALIDATED 2021+: h3=76.9% h5=61.5% avg=+5.34%  N=13 (exceptional avg)
    signals["Q199_i3b_a3_i5"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q198 [triple]: P1_td_setup_count + I4b_3down_bounce + N1_pp_at_ma20
    # VALIDATED 2021+: h3=76.9% h5=69.2% avg=+1.69%  N=13
    signals["Q198_p1_i4b_n1"] = (
        signals.get("P1_td_setup_count", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q197 [triple]: D4_pocket_ma50 + E3_mild_pb + Q1_pp_ma20_fvg3d
    # VALIDATED 2021+: h3=77.8% h5=77.8% avg=+1.20%  N=9
    signals["Q197_d4_e3_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q196 [triple]: I2b_power_move_strong + E1_pb_ma20 + I10_top_decile_day
    # VALIDATED 2021+: h3=77.8% h5=66.7% avg=+3.18%  N=9
    signals["Q196_i2b_e1_i10"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q195 [triple]: X4_inside_near_hi + A3_vol_surge + O1_in_fvg_5d
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.93%  N=10
    signals["Q195_x4_a3_fvg5"] = (
        signals.get("X4_inside_near_hi", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q194 [triple]: X7_pb_dry_ma50 + K4_first_pb_to_ma20 + Q3_inside_dry_td6
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+2.37%  N=15 (LARGE N! pullback + dry + td6)
    signals["Q194_x7_k4_q3"] = (
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("Q3_inside_dry_td6", False)
    )

    # Q193 [triple]: J_combo_ma50_trend + J9_3dry_at_ma20 + O1_in_fvg_8d
    # ★ VALIDATED 2021+: h3=81.8% h5=54.5% avg=+1.09%  N=11 (h5 weak)
    signals["Q193_jcmt_j9_fvg8"] = (
        signals.get("J_combo_ma50_trend", False) and
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("O1_in_fvg_8d", False)
    )

    # Q192 [triple]: F2_dry_after_surge + I4_2down_bounce + Q3_inside_dry_td6
    # ★★★ VALIDATED 2021+: h3=88.9% h5=88.9% avg=+4.46%  N=9 (both h3 and h5 exceptional!)
    signals["Q192_f2_i4_q3"] = (
        signals.get("F2_dry_after_surge", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("Q3_inside_dry_td6", False)
    )

    # Q191 [triple]: X4_inside_near_hi + I10_top_decile_day + J_combo_prior_hi
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+5.07%  N=8 (exceptional avg)
    signals["Q191_x4_i10_jph"] = (
        signals.get("X4_inside_near_hi", False) and
        signals.get("I10_top_decile_day", False) and
        signals.get("J_combo_prior_hi", False)
    )

    # Q190 [triple]: E3_mild_pb + O1_in_fvg_5d + D5_full_setup
    # VALIDATED 2021+: h3=80.0% h5=70.0% avg=+1.89%  N=10
    signals["Q190_e3_fvg5_d5"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("O1_in_fvg_5d", False) and
        signals.get("D5_full_setup", False)
    )

    # Q189 [triple]: F1_vol_dry + F2_dry_after_surge + J6_dry_near_swing_lo
    # ★★★ VALIDATED 2021+: h3=87.5% h5=100.0% avg=+2.74%  N=8 (PERFECT h5!)
    signals["Q189_f1_f2_j6"] = (
        signals.get("F1_vol_dry", False) and
        signals.get("F2_dry_after_surge", False) and
        signals.get("J6_dry_near_swing_lo", False)
    )

    # Q188 [triple]: F1_vol_dry + J3_dry_near_ma50 + J6_dry_near_swing_lo
    # ★★★ VALIDATED 2021+: h3=87.5% h5=100.0% avg=+2.89%  N=8 (PERFECT h5!)
    signals["Q188_f1_j3_j6"] = (
        signals.get("F1_vol_dry", False) and
        signals.get("J3_dry_near_ma50", False) and
        signals.get("J6_dry_near_swing_lo", False)
    )

    # Q187 [triple]: X7_pb_dry_ma50 + J5_dry_prior_base_hi + J8_pb_zone_ma50
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q187_x7_j5_j8"] = (
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q186 [triple]: F5_dry_ma50 + J5_dry_prior_base_hi + J8_pb_zone_ma50
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q186_f5_j5_j8"] = (
        signals.get("F5_dry_ma50", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q185 [triple]: F1_vol_dry + J5_dry_prior_base_hi + J8_pb_zone_ma50
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q185_f1_j5_j8"] = (
        signals.get("F1_vol_dry", False) and
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q184 [triple]: E1_pb_ma20 + C3_pocket_pivot + O1_in_fvg_5d
    # ★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12
    signals["Q184_e1_c3_fvg5"] = (
        signals.get("E1_pb_ma20", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q183 [triple]: X4_inside_near_hi + A3_vol_surge + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+1.93%  N=10
    signals["Q183_x4_a3_fvg3"] = (
        signals.get("X4_inside_near_hi", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q182 [triple]: J5_dry_prior_base_hi + F1_vol_dry + J8_pb_zone_ma50
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q182_j5_f1_j8"] = (
        signals.get("J5_dry_prior_base_hi", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q181 [triple]: N5_pp_after_bkt_pb + C3_pocket_pivot + I4_2down_bounce
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q181_n5_c3_i4"] = (
        signals.get("N5_pp_after_bkt_pb", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q180 [triple]: N5_pp_after_bkt_pb + C2_above_ma50 + I4_2down_bounce
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q180_n5_c2_i4"] = (
        signals.get("N5_pp_after_bkt_pb", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q179 [triple]: I2b_power_move_strong + E1_pb_ma20 + I2_power_move
    # ★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q179_i2b_e1_i2"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2_power_move", False)
    )

    # Q178 [triple]: I2b_power_move_strong + C2_above_ma50 + E1_pb_ma20
    # VALIDATED 2021+: h3=80.0% h5=70.0% avg=+3.08%  N=10
    signals["Q178_i2b_c2_e1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("E1_pb_ma20", False)
    )

    # Q177 [triple]: D4_pocket_ma50 + E1_pb_ma20 + I2b_power_move_strong
    # VALIDATED 2021+: h3=80.0% h5=70.0% avg=+3.08%  N=10
    signals["Q177_d4_e1_i2b"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q176 [triple]: D4_pocket_ma50 + E1_pb_ma20 + O1_in_fvg_5d
    # ★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+1.10%  N=12
    signals["Q176_d4_e1_fvg5"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q175 [triple]: E3_mild_pb + I4_2down_bounce + N5_pp_after_bkt_pb
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q175_e3_i4_n5"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q174 [triple]: E3_mild_pb + I2b_power_move_strong + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=88.9% h5=77.8% avg=+3.68%  N=9
    signals["Q174_e3_i2b_n1"] = (
        signals.get("E3_mild_pb", False) and
        signals.get("I2b_power_move_strong", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q173 [triple]: K4_first_pb_to_ma20 + X7_pb_dry_ma50 + Q2_pb_inside_td6
    # VALIDATED 2021+: h3=80.0% h5=60.0% avg=+2.37%  N=15 (LARGE N! K4 pullback + dry volume)
    signals["Q173_k4_x7_q2"] = (
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("Q2_pb_inside_td6", False)
    )

    # Q172 [triple]: D4_pocket_ma50 + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13
    signals["Q172_d4_e4_q1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q171 [triple]: D3_near_hi_ma50 + E4_shallow_pb + Q1_pp_ma20_fvg3d
    # ★ VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13
    signals["Q171_d3_e4_q1"] = (
        signals.get("D3_near_hi_ma50", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("Q1_pp_ma20_fvg3d", False)
    )

    # Q170 [triple]: J3_dry_near_ma50 + F1_vol_dry + J6_dry_near_swing_lo
    # ★★★ VALIDATED 2021+: h3=87.5% h5=100.0% avg=+2.89%  N=8 (PERFECT h5!)
    signals["Q170_j3_f1_j6"] = (
        signals.get("J3_dry_near_ma50", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J6_dry_near_swing_lo", False)
    )

    # Q169 [triple]: X4_inside_near_hi + I10_top_decile_day + J5_dry_prior_base_hi
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+5.07%  N=8 (exceptional avg)
    signals["Q169_x4_i10_j5"] = (
        signals.get("X4_inside_near_hi", False) and
        signals.get("I10_top_decile_day", False) and
        signals.get("J5_dry_prior_base_hi", False)
    )

    # Q168 [triple]: I3b_gap_up_strong + C3_pocket_pivot + I5_cap_reversal
    # ★★ VALIDATED 2021+: h3=88.9% h5=44.4% avg=+6.29%  N=9 (exceptional avg, h5 weak — short hold)
    signals["Q168_i3b_c3_i5"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q167 [triple]: I3b_gap_up_strong + I5_cap_reversal + D4_pocket_ma50
    # ★★ VALIDATED 2021+: h3=88.9% h5=44.4% avg=+6.29%  N=9 (exceptional avg, h5 weak)
    signals["Q167_i3b_i5_d4"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("D4_pocket_ma50", False)
    )

    # Q166 [triple]: I3b_gap_up_strong + I4b_3down_bounce + P1_td_setup_count
    # ★★★ VALIDATED 2021+: h3=80.0% h5=90.0% avg=+7.51%  N=10 (EXCEPTIONAL avg+h5!)
    signals["Q166_i3b_i4b_p1"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I4b_3down_bounce", False) and
        signals.get("P1_td_setup_count", False)
    )

    # Q165 [triple]: P1_td_setup_count + I3b_gap_up_strong + I4b_3down_bounce
    # (duplicate of Q166 — see Q166)
    # Q165 used for: I2b + E3 + N1 (same as Q174 discovered via I2b backbone)

    # Q165 [triple]: I2b_power_move_strong + E1_pb_ma20 + N1_pp_at_ma20
    # ★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q165_i2b_e1_n1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q164 [triple]: I2b_power_move_strong + E3_mild_pb + N1_pp_at_ma20
    # ★★★ VALIDATED 2021+: h3=88.9% h5=77.8% avg=+3.68%  N=9 (same as Q174 — via different backbone)
    signals["Q164_i2b_e3_n1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("E3_mild_pb", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q163 [triple]: P1_td_setup_count + A3_vol_surge + I4_2down_bounce
    # ★★★★ VALIDATED 2021+: h3=100.0% h5=88.9% avg=+6.40%  N=9 (PERFECT h3!)
    signals["Q163_p1_a3_i4"] = (
        signals.get("P1_td_setup_count", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("I4_2down_bounce", False)
    )

    # Q162 [triple]: D4_pocket_ma50 + I4_2down_bounce + N5_pp_after_bkt_pb
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q162_d4_i4_n5"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("N5_pp_after_bkt_pb", False)
    )

    # Q161 [triple]: D4_pocket_ma50 + I4_2down_bounce + K1_first_pb_after_bkt
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.85%  N=8
    signals["Q161_d4_i4_k1"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("K1_first_pb_after_bkt", False)
    )

    # Q160 [triple]: D4_pocket_ma50 + I3b_gap_up_strong + I5_cap_reversal
    # ★★ VALIDATED 2021+: h3=88.9% h5=44.4% avg=+6.29%  N=9 (exceptional avg)
    signals["Q160_d4_i3b_i5"] = (
        signals.get("D4_pocket_ma50", False) and
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False)
    )

    # Q159 [triple]: I2b_power_move_strong + C3_pocket_pivot + E1_pb_ma20
    # ★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q159_i2b_c3_e1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("C3_pocket_pivot", False) and
        signals.get("E1_pb_ma20", False)
    )

    # Q158 [triple]: I2b_power_move_strong + A3_vol_surge + E1_pb_ma20
    # ★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q158_i2b_a3_e1"] = (
        signals.get("I2b_power_move_strong", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("E1_pb_ma20", False)
    )

    # Q157 [triple]: I9_inside_close_hi + J9_3dry_at_ma20 + O1_in_fvg_5d
    # ★★ VALIDATED 2021+: h3=88.9% h5=55.6% avg=+1.58%  N=9 (h3 exceptional, h5 weak)
    signals["Q157_i9_j9_fvg5"] = (
        signals.get("I9_inside_close_hi", False) and
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q156 [triple]: I8_3up_days + I2b_power_move_strong + I9_inside_close_hi
    # VALIDATED 2021+: h3=80.0% h5=70.0% avg=+2.45%  N=10
    signals["Q156_i8_i2b_i9"] = (
        signals.get("I8_3up_days", False) and
        signals.get("I2b_power_move_strong", False) and
        signals.get("I9_inside_close_hi", False)
    )

    # Q155 [triple]: J_combo_prior_hi + X7_pb_dry_ma50 + J8_pb_zone_ma50
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q155_jph_x7_j8"] = (
        signals.get("J_combo_prior_hi", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q154 [triple]: J_combo_prior_hi + F5_dry_ma50 + J8_pb_zone_ma50
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q154_jph_f5_j8"] = (
        signals.get("J_combo_prior_hi", False) and
        signals.get("F5_dry_ma50", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q153 [triple]: J_combo_prior_hi + F1_vol_dry + J8_pb_zone_ma50
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.18%  N=8
    signals["Q153_jph_f1_j8"] = (
        signals.get("J_combo_prior_hi", False) and
        signals.get("F1_vol_dry", False) and
        signals.get("J8_pb_zone_ma50", False)
    )

    # Q152 [triple]: J_combo_prior_hi + X4_inside_near_hi + I10_top_decile_day
    # ★★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+5.07%  N=8 (EXCEPTIONAL avg return!)
    signals["Q152_jph_x4_i10"] = (
        signals.get("J_combo_prior_hi", False) and
        signals.get("X4_inside_near_hi", False) and
        signals.get("I10_top_decile_day", False)
    )

    # Q151 [triple]: H2_new10d_hi + A3_vol_surge + N1_pp_at_ma20
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.47%  N=8
    signals["Q151_h2_a3_n1"] = (
        signals.get("H2_new10d_hi", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q150 [triple]: A2_bkt10 + A3_vol_surge + N1_pp_at_ma20
    # ★★ VALIDATED 2021+: h3=87.5% h5=75.0% avg=+2.47%  N=8
    signals["Q150_a2_a3_n1"] = (
        signals.get("A2_bkt10", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("N1_pp_at_ma20", False)
    )

    # Q149 [triple]: I2_power_move + E1_pb_ma20 + I2b_power_move_strong
    # ★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+2.86%  N=11
    signals["Q149_i2_e1_i2b"] = (
        signals.get("I2_power_move", False) and
        signals.get("E1_pb_ma20", False) and
        signals.get("I2b_power_move_strong", False)
    )

    # Q148 [triple]: K1_first_pb_after_bkt + X5_nr7_ma50 + D5_full_setup
    # ★ VALIDATED 2021+: h3=81.8% h5=63.6% avg=+1.80%  N=11
    signals["Q148_k1_x5_d5"] = (
        signals.get("K1_first_pb_after_bkt", False) and
        signals.get("X5_nr7_ma50", False) and
        signals.get("D5_full_setup", False)
    )

    # Q147 [triple]: M5_post_bkt_vdu_expansion + E4_shallow_pb + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=66.7% avg=+2.08%  N=15 (LARGE N! post-bkt VDU + shallow pb)
    signals["Q147_m5_e4_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q146 [triple]: M5_post_bkt_vdu_expansion + B3_near_hi20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=66.7% avg=+2.08%  N=15 (LARGE N! post-bkt VDU near 20d high)
    signals["Q146_m5_b3_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q145 [triple]: M5_post_bkt_vdu_expansion + H1_mom_resume + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=66.7% avg=+2.06%  N=15 (LARGE N! post-bkt VDU + mom resume)
    signals["Q145_m5_h1_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q144 [triple]: M5_post_bkt_vdu_expansion + C2_above_ma50 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=81.2% h5=62.5% avg=+1.97%  N=16 (LARGE N! post-bkt VDU in FVG)
    signals["Q144_m5_c2_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("C2_above_ma50", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q143 [triple]: M3_vdu_expansion_pb + B3_near_hi20 + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=70.0% avg=+1.97%  N=10 (VDU pb near 20d high in FVG)
    signals["Q143_m3_b3_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("B3_near_hi20", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q142 [triple]: M1_vdu_expansion + M3_vdu_expansion_pb + O1_in_fvg_3d
    # ★ VALIDATED 2021+: h3=83.3% h5=66.7% avg=+2.33%  N=12 (dual VDU confirmation in FVG)
    signals["Q142_m1_m3_fvg3"] = (
        signals.get("M1_vdu_expansion", False) and
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q141 [triple]: M3_vdu_expansion_pb + E4_shallow_pb + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=70.0% avg=+1.97%  N=10 (VDU pullback + shallow pb + 3d FVG)
    signals["Q141_m3_e4_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q140 [triple]: M1_vdu_expansion + E4_shallow_pb + O1_in_fvg_3d
    # VALIDATED 2021+: h3=80.0% h5=66.7% avg=+2.08%  N=15 (LARGE N! VDU expansion + FVG)
    signals["Q140_m1_e4_fvg3"] = (
        signals.get("M1_vdu_expansion", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q139 [triple]: M5_post_bkt_vdu_expansion + C1_early_move + O1_in_fvg_3d
    # ★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.65%  N=11 (post-bkt VDU + early move in FVG)
    signals["Q139_m5_c1_fvg3"] = (
        signals.get("M5_post_bkt_vdu_expansion", False) and
        signals.get("C1_early_move", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q138 [triple]: M3_vdu_expansion_pb + H1_mom_resume + O1_in_fvg_3d
    # ★ VALIDATED 2021+: h3=81.8% h5=72.7% avg=+2.49%  N=11 (VDU pullback + mom resume + FVG)
    signals["Q138_m3_h1_fvg3"] = (
        signals.get("M3_vdu_expansion_pb", False) and
        signals.get("H1_mom_resume", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q137 [quad]: N1_pp_at_ma20 + E4_shallow_pb + O1_in_fvg_8d + O1_in_fvg_3d
    # VALIDATED 2021+: h3=84.6% h5=76.9% avg=+2.47%  N=13 (Q131 + 3-day FVG refinement)
    signals["Q137_n1_e4_fvg8_fvg3"] = (
        signals.get("N1_pp_at_ma20", False) and
        signals.get("E4_shallow_pb", False) and
        signals.get("O1_in_fvg_8d", False) and
        signals.get("O1_in_fvg_3d", False)
    )

    # Q136 [triple]: C3_pocket_pivot + I4_2down_bounce + O1_in_fvg_5d
    # VALIDATED 2021+: h3=78.6% h5=64.3% avg=+1.60%  N=14 (LARGE N! Pocket pivot reversal in FVG)
    signals["Q136_c3_i4_fvg5"] = (
        signals.get("C3_pocket_pivot", False) and
        signals.get("I4_2down_bounce", False) and
        signals.get("O1_in_fvg_5d", False)
    )

    # Q135 [triple]: J9_3dry_at_ma20 + X3_shallow_pb_surge + D5_full_setup
    # ★ VALIDATED 2021+: h3=81.8% h5=81.8% avg=+1.48%  N=11 (BOTH fwd consistent)
    signals["Q135_j9_x3_d5"] = (
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("X3_shallow_pb_surge", False) and
        signals.get("D5_full_setup", False)
    )

    # Q134 [triple]: J9_3dry_at_ma20 + F2_dry_after_surge + D5_full_setup
    # ★ VALIDATED 2021+: h3=81.8% h5=81.8% avg=+1.48%  N=11 (BOTH fwd consistent)
    signals["Q134_j9_f2_d5"] = (
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("F2_dry_after_surge", False) and
        signals.get("D5_full_setup", False)
    )

    # Q133 [triple]: J9_3dry_at_ma20 + B2_tight + X3_shallow_pb_surge
    # ★ VALIDATED 2021+: h3=81.8% h5=81.8% avg=+1.48%  N=11 (BOTH fwd consistent)
    signals["Q133_j9_b2_x3"] = (
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("B2_tight", False) and
        signals.get("X3_shallow_pb_surge", False)
    )

    # Q132 [triple]: I4_2down_bounce + A3_vol_surge + I4b_3down_bounce
    # ★★ VALIDATED 2021+: h3=80.0% h5=90.0% avg=+4.33%  N=10 (h5 > h3! Exceptional 5-day)
    # 2-day down bounce + volume surge + 3-day down bounce — multi-day reversal setup
    signals["Q132_i4_a3_i4b"] = (
        signals.get("I4_2down_bounce", False) and
        signals.get("A3_vol_surge", False) and
        signals.get("I4b_3down_bounce", False)
    )

    # Q92 [triple]: X7_pb_dry_ma50 + J9_3dry_at_ma20 + D5_full_setup
    # ★ VALIDATED 2021+: h3=81.8% h5=45.5% avg=+0.77%  N=11 (⚠️ h5 weak — 3-day only)
    signals["Q92_x7_j9_d5"] = (
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("J9_3dry_at_ma20", False) and
        signals.get("D5_full_setup", False)
    )

    # Q54 [triple]: I4b_3down_bounce + X7_pb_dry_ma50 + J4_dry_value_area
    # ★ VALIDATED IN RECENT DATA (2021+): h3=88.9% h5=55.6% avg=+3.78%  N=9
    # ⚠️ h5=55.6% — 3-day trade only; bounce + dry pullback to MA50 + value area
    signals["Q54_4b_x7_j4"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("X7_pb_dry_ma50", False) and
        signals.get("J4_dry_value_area", False)
    )

    # Q55 [triple]: I4b_3down_bounce + X2_mild_pb_dry + J4_dry_value_area
    # ★ VALIDATED IN RECENT DATA (2021+): h3=81.8% h5=45.5% avg=+2.75%  N=11
    # ⚠️ h5=45.5% — 3-day ONLY; broad bounce+mild dry+value area overlap
    signals["Q55_4b_x2_j4"] = (
        signals.get("I4b_3down_bounce", False) and
        signals.get("X2_mild_pb_dry", False) and
        signals.get("J4_dry_value_area", False)
    )

    # Q56 [triple]: J8_pb_zone_ma50 + K4_first_pb_to_ma20 + C2_above_ma50
    # ★ VALIDATED IN RECENT DATA (2021+): h3=75.0% h5=66.7% avg=+3.97%  N=12
    # J8+K4 with above-MA50 position confirmation
    signals["Q56_k4_j8_c2"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("C2_above_ma50", False)
    )

    # Q57 [triple]: J8_pb_zone_ma50 + K4_first_pb_to_ma20 + J3_dry_near_ma50
    # ★ VALIDATED IN RECENT DATA (2021+): h3=75.0% h5=66.7% avg=+3.97%  N=12
    # J8+K4 with volume dry-up near MA50
    signals["Q57_k4_j8_j3"] = (
        signals.get("J8_pb_zone_ma50", False) and
        signals.get("K4_first_pb_to_ma20", False) and
        signals.get("J3_dry_near_ma50", False)
    )

    # Q51 [triple]: I3b_gap_up_strong + I5_cap_reversal + I2_power_move
    # ★ VALIDATED IN RECENT DATA (2021+): h3=75.0% h5=75.0% avg=+5.76%  N=16
    # QB2 + I2 power move — broadest QB2 triple (N=16), both fwd equal = sustained
    signals["Q51_qb2_power_move"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("I2_power_move", False)
    )

    # Q52 [triple]: I3b_gap_up_strong + I5_cap_reversal + B3_near_hi20
    # ★ VALIDATED IN RECENT DATA (2021+): h3=75.0% h5=62.5% avg=+5.38%  N=8
    # QB2 + near 20-day high — breakout proximity confirmation
    signals["Q52_qb2_near_hi20"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("B3_near_hi20", False)
    )

    # Q53 [triple]: I3b_gap_up_strong + I5_cap_reversal + D3_near_hi_ma50
    # ★ VALIDATED IN RECENT DATA (2021+): h3=75.0% h5=62.5% avg=+5.38%  N=8
    # QB2 + near hi vs MA50 — above-MA50 high-proximity confirmation
    signals["Q53_qb2_near_hi_ma50"] = (
        signals.get("I3b_gap_up_strong", False) and
        signals.get("I5_cap_reversal", False) and
        signals.get("D3_near_hi_ma50", False)
    )

    return signals


# ── Main backtest ──────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame, after_date=None):
    all_dates  = sorted(df["date"].unique())
    tickers    = df["ticker"].unique()

    # Pre-pivot for speed
    px = df.pivot(index="date", columns="ticker", values="close")
    vl = df.pivot(index="date", columns="ticker", values="volume")
    hi = df.pivot(index="date", columns="ticker", values="high")
    lo = df.pivot(index="date", columns="ticker", values="low")

    records = []

    # In incremental mode, only process dates strictly after after_date
    if after_date is not None:
        process_dates = [d for d in all_dates if d > after_date]
    else:
        process_dates = all_dates[MIN_HIST:]

    process_set = set(process_dates)
    print(f"Backtesting {len(process_dates)} dates × {len(tickers)} tickers …")

    for i, dt in enumerate(all_dates):
        if i < MIN_HIST or dt not in process_set:
            continue
        # ── Build eligible universe ──────────────────────────────────────
        hist_idx = all_dates[max(0, i - RS_DAYS):i + 1]
        if len(hist_idx) < RS_DAYS:
            continue

        try:
            px_now = px.loc[dt]
            vl_now = vl.loc[dt]
        except KeyError:
            continue

        # ADTV filter
        adtv = (px.loc[hist_idx].values * vl.loc[hist_idx].values).mean(axis=0)
        adtv_s = pd.Series(adtv, index=px.columns)

        eligible = px_now.index[
            (px_now >= PRICE_MIN) &
            (adtv_s >= ADTV_MIN) &
            px_now.notna()
        ].tolist()

        if len(eligible) < 10:
            continue

        # ── RS rank → top-N focus list ───────────────────────────────────
        lb_idx = all_dates[max(0, i - RS_DAYS)]
        px_lb  = px.loc[lb_idx] if lb_idx in px.index else None
        if px_lb is None:
            continue

        rs_raw = (px_now[eligible] / px_lb[eligible] - 1).dropna()
        if len(rs_raw) < TOP_N:
            continue
        focus = rs_raw.nlargest(TOP_N).index.tolist()

        # ── Market regime: use median MA200 ratio across eligible stocks ──
        # Bull = median stock price > MA200; Bear = below
        if i >= 200:
            ma200_idx = all_dates[i - 200:i + 1]
            ma200_vals = px.loc[ma200_idx, eligible].mean(axis=0)  # proxy MA200 per ticker
            above_ma200 = (px_now[eligible] > ma200_vals).mean()
            regime = "bull" if above_ma200 >= 0.5 else "bear"
        else:
            regime = "unknown"

        # ── For each focus stock compute signals + forward return ─────────
        for tkr in focus:
            tkr_hist_idx = all_dates[max(0, i - 120):i + 1]
            tkr_df = df[(df["ticker"] == tkr) & (df["date"].isin(tkr_hist_idx))].copy()
            if len(tkr_df) < 30:
                continue

            sigs = compute_signals(tkr_df)
            if not sigs:
                continue

            # Forward returns
            fwd = {}
            for fd in FWD_DAYS:
                fut_idx = i + fd
                if fut_idx < len(all_dates):
                    fut_dt = all_dates[fut_idx]
                    fut_px = px.at[fut_dt, tkr] if fut_dt in px.index and tkr in px.columns else np.nan
                    cur_px = px.at[dt, tkr]
                    fwd[f"fwd{fd}"] = fut_px / cur_px - 1 if cur_px > 0 else np.nan
                else:
                    fwd[f"fwd{fd}"] = np.nan

            rec = {"date": dt, "ticker": tkr, "regime": regime}
            rec.update(sigs)
            rec.update(fwd)
            records.append(rec)

        if i % 200 == 0:
            print(f"  …{i}/{len(all_dates)} dates done")

    return pd.DataFrame(records)


# ── Summarise results ──────────────────────────────────────────────────────────
def summarise(results: pd.DataFrame, regime_filter: str = "all"):
    if regime_filter == "bull":
        data = results[results["regime"] == "bull"]
    elif regime_filter == "bear":
        data = results[results["regime"] == "bear"]
    else:
        data = results

    signal_cols = [c for c in data.columns
                   if c not in ("date", "ticker", "regime") and not c.startswith("fwd")]

    rows = []
    total_obs = len(data)

    for fd in FWD_DAYS:
        col = f"fwd{fd}"
        base = data[col].dropna()
        rows.append({
            "regime":    regime_filter,
            "signal":    "BASELINE (all focus)",
            "fwd_days":  fd,
            "n_obs":     len(base),
            "hit_rate":  (base > 0).mean() * 100,
            "avg_ret":   base.mean() * 100,
            "med_ret":   base.median() * 100,
            "fire_rate": 100.0,
        })

    for sig in signal_cols:
        subset = data[data[sig] == True]
        if len(subset) < 30:
            continue
        for fd in FWD_DAYS:
            col = f"fwd{fd}"
            sub = subset[col].dropna()
            if len(sub) < 20:
                continue
            rows.append({
                "regime":    regime_filter,
                "signal":    sig,
                "fwd_days":  fd,
                "n_obs":     len(sub),
                "hit_rate":  (sub > 0).mean() * 100,
                "avg_ret":   sub.mean() * 100,
                "med_ret":   sub.median() * 100,
                "fire_rate": len(subset) / total_obs * 100,
            })

    return pd.DataFrame(rows)


# ── Print report ───────────────────────────────────────────────────────────────
def print_report(summary: pd.DataFrame, label: str = "ALL"):
    print(f"\n{'='*90}")
    print(f"TH Entry Screen — {label} regime")
    print(f"{'='*90}")
    print(f"  {'Signal':<22} {'FWD':>4}  {'N':>5}  {'Hit%':>6}  {'Avg%':>6}  {'Med%':>6}  {'Fire%':>6}")
    print(f"  {'-'*80}")

    for fwd in FWD_DAYS:
        sub = summary[summary["fwd_days"] == fwd].sort_values("avg_ret", ascending=False)
        print(f"\n  ── Forward {fwd}d ──")
        for _, r in sub.iterrows():
            # threshold scales with horizon: 1-3d needs 53%/0.3%, 5d needs 54%/0.5%
            min_hit = 52 if fwd <= 3 else 54
            min_avg = 0.3 if fwd <= 3 else 0.5
            marker = "  ✅" if r["hit_rate"] > min_hit and r["avg_ret"] > min_avg and r["n_obs"] >= 30 else ""
            print(f"  {r['signal']:<22} {fwd:>3}d  {r['n_obs']:>5}  "
                  f"{r['hit_rate']:>5.1f}%  {r['avg_ret']:>+5.2f}%  "
                  f"{r['med_ret']:>+5.2f}%  {r['fire_rate']:>5.1f}%{marker}")

    print(f"\n  ✅ = fwd1-3d: hit>52% avg>0.3% | fwd5d: hit>54% avg>0.5% | N≥30")

    def passes(r):
        if r["fwd_days"] <= 3:
            return r["hit_rate"] > 52 and r["avg_ret"] > 0.3 and r["n_obs"] >= 30
        return r["hit_rate"] > 54 and r["avg_ret"] > 0.5 and r["n_obs"] >= 30

    best = summary[summary.apply(passes, axis=1)].sort_values(["fwd_days", "avg_ret"], ascending=[True, False])

    if len(best) > 0:
        print(f"\n  🏆 PASSING [{label}]:")
        for _, r in best.iterrows():
            print(f"    {r['signal']:<22} fwd{r['fwd_days']}d  "
                  f"hit={r['hit_rate']:.0f}%  avg={r['avg_ret']:+.2f}%  N={r['n_obs']:.0f}")
    else:
        print(f"\n  ❌ No signal passes threshold [{label}]")


# ── Entry ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    full_rebuild = "--full" in sys.argv

    out_path = Path("data/research/thai_entry_screen_results.csv")
    sum_path = Path("data/research/thai_entry_screen_summary.csv")

    # Incremental mode: load existing CSV, find last date, run only new dates
    last_date = None
    existing  = None
    if not full_rebuild and out_path.exists():
        try:
            existing  = pd.read_csv(out_path, low_memory=False)
            existing["date"] = pd.to_datetime(existing["date"])
            last_date = existing["date"].max()
            print(f"[INCREMENTAL] Existing CSV last date: {last_date.date()}")
        except Exception as e:
            print(f"[WARN] Could not read existing CSV ({e}), doing full rebuild")
            existing = None

    df = load_data()
    print(f"Loaded {len(df):,} rows  |  {df['ticker'].nunique()} tickers  |  "
          f"{df['date'].min().date()} → {df['date'].max().date()}")

    if existing is not None and last_date is not None:
        db_last = df["date"].max()
        if last_date >= db_last:
            print(f"[OK] CSV already up to date ({last_date.date()}) — nothing to do")
            sys.exit(0)
        # Run backtest only on dates after last_date (but need MIN_HIST lookback)
        cutoff = last_date
        print(f"[INCREMENTAL] Running {last_date.date()} → {db_last.date()} ...")
        new_results = run_backtest(df, after_date=cutoff)
        if new_results is None or len(new_results) == 0:
            print("[OK] No new rows generated")
            sys.exit(0)
        results = pd.concat([existing, new_results], ignore_index=True)
        results = results.drop_duplicates(subset=["date", "ticker"], keep="last")
        results = results.sort_values(["date", "ticker"]).reset_index(drop=True)
        print(f"[INCREMENTAL] Added {len(new_results):,} rows  |  Total: {len(results):,}")
    else:
        print("[FULL] Running full backtest ...")
        results = run_backtest(df)
        print(f"\nTotal observations: {len(results):,}")
        print(f"  Bull: {(results['regime']=='bull').sum():,}  "
              f"Bear: {(results['regime']=='bear').sum():,}  "
              f"Unknown: {(results['regime']=='unknown').sum():,}")

    results.to_csv(out_path, index=False)
    print(f"Raw results → {out_path}")

    for regime, label in [("all", "ALL"), ("bull", "BULL"), ("bear", "BEAR")]:
        summary = summarise(results, regime)
        print_report(summary, label)

    summarise(results, "all").to_csv(sum_path, index=False)
    print(f"Summary → {sum_path}")
