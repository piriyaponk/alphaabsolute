"""
pulse_us_entry_screen.py
========================
PULSE-US: backtest study — which buy-point signals predict positive short-term
forward returns for high-RS US stocks?

Mirror of thai_entry_screen.py applied to the US ohlcv.db.
Adds R-group: RS percentile signals (US-specific, from rs_daily table).

Usage:
    python scripts/research/pulse_us/pulse_us_entry_screen.py
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
try:
    from pulse_us_db import write_entry_screen as _db_write, DB_PATH as _DB_PATH
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False

DB       = "data/ohlcv.db"
OUT_DIR  = Path("data/research/pulse_us")
OUT_RESULTS = OUT_DIR / "pulse_us_entry_screen_results.csv"
OUT_SUMMARY = OUT_DIR / "pulse_us_entry_screen_summary.csv"

TOP_N     = 20       # focus list size (top-RS stocks)
RS_DAYS   = 63       # RS lookback (3 months)
ADTV_MIN  = 15e6     # $15M ADTV minimum (CLAUDE.md)
PRICE_MIN = 5.0      # min price
FWD_DAYS  = [1, 2, 3, 5]
MIN_HIST  = 100      # bars needed before first signal


# ── Load data ──────────────────────────────────────────────────────────────────
def load_data():
    conn = sqlite3.connect(DB)
    df = pd.read_sql(
        "SELECT date, ticker, open, high, low, close, volume FROM ohlcv "
        "WHERE close IS NOT NULL AND volume IS NOT NULL",
        conn, parse_dates=["date"]
    )
    # Load RS percentile data (available 2026-03-27 onwards)
    rs = pd.read_sql(
        "SELECT date, ticker, rs_3m_pct, rs_6m_pct, rs_composite, rs_comp_chg_1w "
        "FROM rs_daily",
        conn, parse_dates=["date"]
    )
    conn.close()
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    rs = rs.sort_values(["ticker", "date"]).reset_index(drop=True)
    print(f"Loaded {len(df):,} OHLCV rows | {df['ticker'].nunique()} tickers")
    print(f"Loaded {len(rs):,} RS rows | dates: {rs['date'].min().date()} – {rs['date'].max().date()}")
    return df, rs


# ── Compute price signals (identical to thai_entry_screen signal groups A-Q) ──
def compute_signals(hist: pd.DataFrame, rs_row: dict | None = None) -> dict:
    """hist = recent history for one ticker, sorted by date, ends at signal date."""
    if len(hist) < 30:
        return {}
    c = hist["close"].values
    v = hist["volume"].values
    h = hist["high"].values
    l = hist["low"].values

    close = c[-1]
    vol   = v[-1]

    ma50  = c[-50:].mean() if len(c) >= 50 else np.nan
    ma20  = c[-20:].mean()
    ma150 = c[-150:].mean() if len(c) >= 150 else np.nan
    ma200 = c[-200:].mean() if len(c) >= 200 else np.nan
    vol20 = v[-20:].mean()
    hi20  = h[-20:].max()
    hi10  = h[-10:].max()
    lo10  = l[-10:].min()

    ret3 = close / c[-4] - 1 if len(c) >= 4 else np.nan
    ret5 = close / c[-6] - 1 if len(c) >= 6 else np.nan

    signals = {}

    # ── A: Breakout signals ───────────────────────────────────────────────────
    signals["A1_bkt20_vol"] = (
        close >= hi20 * 0.999 and vol >= vol20 * 1.5
    ) if not np.isnan(ma20) else False
    signals["A2_bkt10"] = close >= hi10 * 0.999
    signals["A3_vol_surge"] = vol >= vol20 * 2.0

    # ── B: Tight base / near-high signals ────────────────────────────────────
    range10_pct    = (hi10 - lo10) / close if close > 0 else 1
    pct_from_hi20  = close / hi20 - 1
    signals["B1_tight_near_hi"] = range10_pct < 0.08 and close >= hi20 * 0.95
    signals["B2_tight"]         = range10_pct < 0.06
    signals["B3_near_hi20"]     = -0.05 <= pct_from_hi20 <= 0.01

    # ── C: Trend / momentum signals ───────────────────────────────────────────
    signals["C1_early_move"] = (
        not np.isnan(ret3) and 0.005 <= ret3 <= 0.06 and
        not np.isnan(ma50) and close > ma50
    )
    signals["C2_above_ma50"] = not np.isnan(ma50) and close > ma50
    if len(hist) >= 11:
        last10 = hist.iloc[-11:-1]
        down_days = last10[last10["close"] < last10["close"].shift(1)]
        max_down_vol = down_days["volume"].max() if len(down_days) > 0 else 0
        signals["C3_pocket_pivot"] = (c[-1] > c[-2]) and (vol > max_down_vol)
    else:
        signals["C3_pocket_pivot"] = False

    # ── E: Pullback signals ───────────────────────────────────────────────────
    signals["E1_pb_ma20"] = not np.isnan(ma20) and close >= ma20 * 0.98 and close <= ma20 * 1.03
    signals["E2_pb_ma50"] = (
        not np.isnan(ma50) and
        close >= ma50 * 0.97 and close <= ma50 * 1.03 and close > ma20
    )
    signals["E3_mild_pb"] = (
        -0.10 <= pct_from_hi20 <= -0.03 and not np.isnan(ma50) and close > ma50
    )
    signals["E4_shallow_pb"] = (
        -0.05 <= pct_from_hi20 <= -0.01 and not np.isnan(ma50) and close > ma50
    )

    # ── F: Volume dry-up signals ──────────────────────────────────────────────
    signals["F1_vol_dry"] = vol < vol20 * 0.50
    if len(hist) >= 11:
        prior10_vol = v[-11:-1]
        had_surge   = (prior10_vol > vol20 * 1.5).any()
        signals["F2_dry_after_surge"] = had_surge and vol < vol20 * 0.60
    else:
        signals["F2_dry_after_surge"] = False
    signals["F3_3day_dry"]   = len(v) >= 3 and all(v[-3:] < vol20 * 0.70)
    signals["F4_dry_near_hi"] = signals["F1_vol_dry"] and -0.08 <= pct_from_hi20 <= 0.01
    signals["F5_dry_ma50"]    = signals["F1_vol_dry"] and (not np.isnan(ma50) and close > ma50)

    # ── G: Inside bar / NR7 ───────────────────────────────────────────────────
    if len(hist) >= 2:
        signals["G1_inside_bar"] = h[-1] <= h[-2] and l[-1] >= l[-2]
    else:
        signals["G1_inside_bar"] = False
    if len(hist) >= 7:
        ranges7 = h[-7:] - l[-7:]
        signals["G2_nr7"] = (h[-1] - l[-1]) == ranges7.min()
    else:
        signals["G2_nr7"] = False

    # ── H: RS / momentum proxies ──────────────────────────────────────────────
    signals["H1_mom_resume"] = (
        not np.isnan(ret5) and ret5 > 0.01 and not np.isnan(ma50) and close > ma50
    )
    signals["H2_new10d_hi"] = close >= hi10 * 0.999

    # ── I: Short-term 1-3 day signals ─────────────────────────────────────────
    day_range = h[-1] - l[-1]
    if day_range > 0:
        close_quality = (c[-1] - l[-1]) / day_range
        signals["I1_close_quality"]        = close_quality >= 0.75 and vol >= vol20 * 1.0
        signals["I1b_close_quality_hi_vol"] = close_quality >= 0.75 and vol >= vol20 * 1.5
    else:
        signals["I1_close_quality"] = signals["I1b_close_quality_hi_vol"] = False

    if len(c) >= 2:
        today_ret = c[-1] / c[-2] - 1
        signals["I2_power_move"]        = today_ret > 0.015 and vol >= vol20 * 1.5
        signals["I2b_power_move_strong"] = today_ret > 0.025 and vol >= vol20 * 2.0
    else:
        signals["I2_power_move"] = signals["I2b_power_move_strong"] = False

    if len(hist) >= 2:
        o_today = hist["open"].values[-1]
        gap_pct = o_today / c[-2] - 1 if c[-2] > 0 else 0
        signals["I3_gap_up_hold"]   = gap_pct > 0.005 and c[-1] > o_today
        signals["I3b_gap_up_strong"] = gap_pct > 0.01 and c[-1] > o_today and vol >= vol20 * 1.3
    else:
        signals["I3_gap_up_hold"] = signals["I3b_gap_up_strong"] = False

    if len(c) >= 4:
        down2 = c[-3] < c[-4] and c[-2] < c[-3]
        bounce = c[-1] > c[-2]
        signals["I4_2down_bounce"] = down2 and bounce
        down3  = len(c) >= 5 and c[-4] < c[-5] and down2
        signals["I4b_3down_bounce"] = down3 and bounce
    else:
        signals["I4_2down_bounce"] = signals["I4b_3down_bounce"] = False

    if len(hist) >= 2:
        prev_ret = c[-2] / c[-3] - 1 if len(c) >= 3 else 0
        was_cap  = prev_ret < -0.02 and v[-2] >= vol20 * 2.0
        signals["I5_cap_reversal"] = was_cap and c[-1] > c[-2]
    else:
        signals["I5_cap_reversal"] = False

    signals["I6_nr7_close_hi"] = (
        signals.get("G2_nr7", False) and day_range > 0 and
        (c[-1] - l[-1]) / day_range >= 0.50
    )
    if len(c) >= 2:
        signals["I7_dry_up_near_hi"] = (
            c[-1] > c[-2] and vol < vol20 * 0.70 and -0.06 <= pct_from_hi20 <= 0.01
        )
    else:
        signals["I7_dry_up_near_hi"] = False

    signals["I8_3up_days"] = len(c) >= 4 and c[-1] > c[-2] and c[-2] > c[-3] and c[-3] > c[-4]
    signals["I9_inside_close_hi"] = (
        signals.get("G1_inside_bar", False) and day_range > 0 and
        (c[-1] - l[-1]) / day_range >= 0.50
    )
    if len(c) >= 21:
        daily_rets   = np.diff(c[-21:]) / c[-21:-1]
        today_ret_raw = c[-1] / c[-2] - 1 if c[-2] > 0 else 0
        signals["I10_top_decile_day"] = (
            today_ret_raw >= np.percentile(daily_rets, 80) and today_ret_raw > 0
        )
    else:
        signals["I10_top_decile_day"] = False

    # ── J: Support confluence + dry volume ────────────────────────────────────
    vol_dry   = vol < vol20 * 0.70
    above_m50 = not np.isnan(ma50) and close > ma50
    ma10 = c[-10:].mean() if len(c) >= 10 else np.nan

    signals["J1_dry_near_ma10"]  = vol_dry and not np.isnan(ma10) and close >= ma10*0.98 and close <= ma10*1.02
    signals["J2_dry_near_ma20"]  = vol_dry and close >= ma20*0.98 and close <= ma20*1.03
    signals["J3_dry_near_ma50"]  = vol_dry and not np.isnan(ma50) and close >= ma50*0.97 and close <= ma50*1.03
    signals["J4_dry_value_area"] = vol_dry and not np.isnan(ma50) and ma50 <= close <= ma20*1.02

    if len(h) >= 60:
        prior_hi = h[-60:-20].max()
        signals["J5_dry_prior_base_hi"]   = vol_dry and close >= prior_hi*0.97 and close <= prior_hi*1.03
        signals["J5b_dry_prior_hi_tight"] = vol_dry and close >= prior_hi*0.98 and close <= prior_hi*1.02
    else:
        signals["J5_dry_prior_base_hi"] = signals["J5b_dry_prior_hi_tight"] = False

    if len(l) >= 30:
        prior_lo = l[-30:-5].min()
        signals["J6_dry_near_swing_lo"] = vol_dry and close >= prior_lo*0.99 and close <= prior_lo*1.04
    else:
        signals["J6_dry_near_swing_lo"] = False

    signals["J7_vcp_zone_ma20"] = vol_dry and -0.08 <= pct_from_hi20 <= -0.03 and close >= ma20*0.98 and close <= ma20*1.03
    signals["J8_pb_zone_ma50"]  = vol_dry and not np.isnan(ma50) and -0.15 <= pct_from_hi20 <= -0.05 and close >= ma50*0.97 and close <= ma50*1.03
    signals["J9_3dry_at_ma20"]  = signals.get("F3_3day_dry", False) and close >= ma20*0.98 and close <= ma20*1.03
    signals["J10_dry_between_ma10_ma20"] = (
        vol_dry and not np.isnan(ma10) and
        min(ma10, ma20)*0.99 <= close <= max(ma10, ma20)*1.01
    )
    signals["J_combo_ma20_trend"] = signals["J2_dry_near_ma20"] and above_m50
    signals["J_combo_ma50_trend"] = signals["J3_dry_near_ma50"] and above_m50
    signals["J_combo_vcp_ma20"]   = signals["J7_vcp_zone_ma20"] and above_m50
    signals["J_combo_prior_hi"]   = signals["J5_dry_prior_base_hi"] and above_m50

    # ── K: First pullback after multi-month breakout ──────────────────────────
    if len(h) >= 63:
        hi63_full    = h[-63:].max()
        hi_recent30  = h[-30:].max()
        was_bkt_hi   = hi_recent30 >= hi63_full * 0.995
        pb_from_bkt  = close / hi_recent30 - 1
        signals["K1_first_pb_after_bkt"]  = was_bkt_hi and -0.08 <= pb_from_bkt <= -0.03 and vol < vol20*0.85
        signals["K2_first_pb_ma20_above"] = signals["K1_first_pb_after_bkt"] and close > ma20
        signals["K3_first_pb_shallow"]    = was_bkt_hi and -0.05 <= pb_from_bkt <= -0.01 and vol < vol20*0.80 and not np.isnan(ma50) and close > ma50
        signals["K4_first_pb_to_ma20"]    = was_bkt_hi and close >= ma20*0.97 and close <= ma20*1.03 and vol < vol20*0.85
    else:
        signals["K1_first_pb_after_bkt"] = signals["K2_first_pb_ma20_above"] = False
        signals["K3_first_pb_shallow"]   = signals["K4_first_pb_to_ma20"]    = False

    # ── M: VDU + Expansion Day ────────────────────────────────────────────────
    if len(v) >= 5:
        v_prior3     = v[-4:-1]
        consec_decline = len(v_prior3) == 3 and v_prior3[0] > v_prior3[1] > v_prior3[2]
        deep_dry       = len(v_prior3) > 0 and v_prior3.min() < vol20*0.60
        prior3_avg     = v_prior3.mean() if len(v_prior3) > 0 else vol20
        is_expansion   = (vol > prior3_avg) and (c[-1] > c[-2])
        signals["M1_vdu_expansion"]       = consec_decline and is_expansion
        signals["M2_vdu_deep_expansion"]  = consec_decline and deep_dry and is_expansion
        signals["M3_vdu_expansion_pb"]    = signals["M1_vdu_expansion"] and -0.10 <= pct_from_hi20 <= -0.02
        signals["M4_vdu_full_compound"]   = signals["M2_vdu_deep_expansion"] and -0.10 <= pct_from_hi20 <= -0.02 and not np.isnan(ma50) and close > ma50
        if len(h) >= 63:
            hi_r30 = h[-30:].max(); hi63_ = h[-63:].max()
            was_bkt_ = hi_r30 >= hi63_*0.995
            pb_bkt_  = close / hi_r30 - 1
            signals["M5_post_bkt_vdu_expansion"] = was_bkt_ and -0.08 <= pb_bkt_ <= -0.01 and signals["M1_vdu_expansion"]
        else:
            signals["M5_post_bkt_vdu_expansion"] = False
    else:
        for k_ in ["M1_vdu_expansion","M2_vdu_deep_expansion","M3_vdu_expansion_pb","M4_vdu_full_compound","M5_post_bkt_vdu_expansion"]:
            signals[k_] = False

    # ── N: Pocket pivot at MA level ───────────────────────────────────────────
    if len(hist) >= 11:
        last10_       = hist.iloc[-11:-1]
        l10c          = last10_["close"].values
        l10c_lag      = np.roll(l10c, 1); l10c_lag[0] = l10c[0]
        down_mask_    = l10c < l10c_lag; down_mask_[0] = False
        down_vols_    = last10_["volume"].values[down_mask_]
        max_dv_       = down_vols_.max() if len(down_vols_) > 0 else 0
        is_pp_        = (c[-1] > c[-2]) and (vol > max_dv_)
        signals["N1_pp_at_ma20"]     = is_pp_ and close >= ma20*0.95 and close <= ma20*1.05
        signals["N2_pp_at_ma50"]     = is_pp_ and not np.isnan(ma50) and close >= ma50*0.95 and close <= ma50*1.05
        signals["N3_pp_after_vdu"]   = is_pp_ and signals.get("F3_3day_dry", False)
        signals["N4_pp_at_ma50_vdu"] = signals["N2_pp_at_ma50"] and signals.get("F3_3day_dry", False)
        signals["N5_pp_after_bkt_pb"]= is_pp_ and signals.get("K1_first_pb_after_bkt", False)
    else:
        for k_ in ["N1_pp_at_ma20","N2_pp_at_ma50","N3_pp_after_vdu","N4_pp_at_ma50_vdu","N5_pp_after_bkt_pb"]:
            signals[k_] = False

    # ── O: SMC — FVG + CHoCH ─────────────────────────────────────────────────
    if len(hist) >= 4:
        for lookback in [3, 5, 8]:
            if len(hist) >= lookback + 3:
                fvg_found = False
                for j in range(1, lookback + 1):
                    c1_hi = h[-(j+2)]; c3_lo = l[-j]
                    if c3_lo > c1_hi and c1_hi <= close <= c3_lo * 1.01:
                        fvg_found = True; break
                signals[f"O1_in_fvg_{lookback}d"] = fvg_found
            else:
                signals[f"O1_in_fvg_{lookback}d"] = False
    else:
        for lb in [3, 5, 8]:
            signals[f"O1_in_fvg_{lb}d"] = False

    if len(c) >= 11:
        signals["O2_choch_10d"] = c[-1] > c[-11:-1].max()
    else:
        signals["O2_choch_10d"] = False
    signals["O3_choch_dry"] = signals.get("O2_choch_10d", False) and vol < vol20*0.85

    # ── P: TD Sequential ─────────────────────────────────────────────────────
    if len(c) >= 14:
        td_count = 0
        for k in range(1, 10):
            idx = -k; lag_idx = idx - 4
            if abs(idx - 4) > len(c): break
            if c[idx] < c[lag_idx]: td_count += 1
            else: break
        signals["P1_td_setup_count"] = td_count
        signals["P2_td_setup_9"]     = td_count >= 9
        signals["P3_td_setup_6plus"] = td_count >= 6
        if td_count >= 9 and len(c) >= 14:
            signals["P4_td_perfect_setup"] = l[-2] <= l[-4] and l[-1] <= l[-4]
        else:
            signals["P4_td_perfect_setup"] = False
        if len(c) >= 20:
            cd_count = sum(1 for k in range(3, 20) if c[-k] <= l[-(k+2)])
            signals["P5_td_countdown_13"]   = cd_count >= 13
            signals["P6_td_countdown_8plus"] = cd_count >= 8
        else:
            signals["P5_td_countdown_13"] = signals["P6_td_countdown_8plus"] = False
        signals["P7_td9_bull_ma50"]     = signals["P2_td_setup_9"]     and not np.isnan(ma50) and close > ma50
        signals["P8_td_perfect_ma50"]   = signals["P4_td_perfect_setup"] and not np.isnan(ma50) and close > ma50
        signals["P9_countdown_ma50"]    = signals["P5_td_countdown_13"] and not np.isnan(ma50) and close > ma50
    else:
        for pk in ["P1_td_setup_count","P2_td_setup_9","P3_td_setup_6plus","P4_td_perfect_setup",
                   "P5_td_countdown_13","P6_td_countdown_8plus","P7_td9_bull_ma50","P8_td_perfect_ma50","P9_countdown_ma50"]:
            signals[pk] = False

    # ── D: Classic combo signals ──────────────────────────────────────────────
    signals["D1_bkt_tight"]     = signals["A2_bkt10"]       and signals["B2_tight"]
    signals["D2_bkt_ma50"]      = signals["A1_bkt20_vol"]   and signals["C2_above_ma50"]
    signals["D3_near_hi_ma50"]  = signals["B3_near_hi20"]   and signals["C2_above_ma50"]
    signals["D4_pocket_ma50"]   = signals["C3_pocket_pivot"] and signals["C2_above_ma50"]
    signals["D5_full_setup"]    = signals["B3_near_hi20"] and signals["C2_above_ma50"] and signals["B2_tight"]

    # ── X: Additional combo signals ───────────────────────────────────────────
    signals["X1_pb_ma50_dry"]      = signals["E2_pb_ma50"] and signals["F1_vol_dry"]
    signals["X2_mild_pb_dry"]      = signals["E3_mild_pb"] and signals["F2_dry_after_surge"]
    signals["X3_shallow_pb_surge"] = signals["E4_shallow_pb"] and signals["F2_dry_after_surge"]
    signals["X4_inside_near_hi"]   = signals["G1_inside_bar"] and signals["B3_near_hi20"]
    signals["X5_nr7_ma50"]         = signals["G2_nr7"] and not np.isnan(ma50) and close > ma50
    signals["X6_dry_near_hi_ma50"] = signals["F4_dry_near_hi"] and not np.isnan(ma50) and close > ma50
    signals["X7_pb_dry_ma50"]      = signals["E3_mild_pb"] and signals["F1_vol_dry"] and not np.isnan(ma50) and close > ma50
    signals["X8_3dry_near_hi"]     = signals["F3_3day_dry"] and signals["B3_near_hi20"]

    # ── R: US-specific RS signals (only available when rs_row provided) ───────
    if rs_row:
        rs3  = rs_row.get("rs_3m_pct",  np.nan)
        rs6  = rs_row.get("rs_6m_pct",  np.nan)
        rsc  = rs_row.get("rs_composite", np.nan)
        rschg = rs_row.get("rs_comp_chg_1w", np.nan)
        signals["R1_rs3_gt70"]       = not np.isnan(rs3) and rs3 > 70
        signals["R2_rs3_gt80"]       = not np.isnan(rs3) and rs3 > 80
        signals["R3_rs3_gt90"]       = not np.isnan(rs3) and rs3 > 90
        signals["R4_rs_acc"]         = not np.isnan(rschg) and rschg > 0   # RS composite improving
        signals["R5_rs_acc_strong"]  = not np.isnan(rschg) and rschg > 5   # RS composite +5pts/wk
        signals["R6_rs80_acc"]       = signals.get("R2_rs3_gt80", False) and signals.get("R4_rs_acc", False)
        signals["R7_rs90_acc"]       = signals.get("R3_rs3_gt90", False) and signals.get("R4_rs_acc", False)
        # Trend Template (Minervini) — US-specific structural gate
        if not np.isnan(ma50) and not np.isnan(ma150) and not np.isnan(ma200):
            s1 = close > ma200
            s2 = len(c) >= 221 and ma200 > c[-200:-179].mean()  # MA200 trending up
            s3 = close > ma150
            s4 = ma150 >= ma200 * 0.97   # within -3%
            s5 = ma50 > ma150
            s6 = close >= ma50 * 0.95    # within -5%
            hi52 = h[-252:].max() if len(h) >= 252 else h.max()
            lo52 = l[-252:].min() if len(l) >= 252 else l.min()
            s7 = close >= hi52 * 0.80    # within -20% of 52W high
            s8 = close >= lo52 * 1.20    # 20% above 52W low
            tt_score = sum([s1, s3, s4, s5, s6, s7, s8])  # s2 needs extra data, count rest
            signals["R8_trend_template_full"] = s1 and s3 and s4 and s5 and s6 and s7 and s8
            signals["R9_tt_score"]            = tt_score
            signals["R10_tt_partial"]         = tt_score >= 5
            # Best combos
            signals["R11_rs80_tt"]    = signals.get("R2_rs3_gt80", False) and signals["R8_trend_template_full"]
            signals["R12_rs90_tt"]    = signals.get("R3_rs3_gt90", False) and signals["R8_trend_template_full"]
            signals["R13_rs_acc_tt"]  = signals.get("R6_rs80_acc", False) and signals["R8_trend_template_full"]
        else:
            for rk in ["R8_trend_template_full","R9_tt_score","R10_tt_partial","R11_rs80_tt","R12_rs90_tt","R13_rs_acc_tt"]:
                signals[rk] = False
    else:
        for rk in ["R1_rs3_gt70","R2_rs3_gt80","R3_rs3_gt90","R4_rs_acc","R5_rs_acc_strong",
                   "R6_rs80_acc","R7_rs90_acc","R8_trend_template_full","R9_tt_score",
                   "R10_tt_partial","R11_rs80_tt","R12_rs90_tt","R13_rs_acc_tt"]:
            signals[rk] = False

    # ── Top Q compound signals (from TH validated backbones) ──────────────────
    signals["Q1_pp_ma20_fvg3d"]      = signals.get("N1_pp_at_ma20",False)   and signals.get("O1_in_fvg_3d",False)
    signals["Q4_pp_fvg_near_hi"]     = signals.get("N1_pp_at_ma20",False)   and signals.get("O1_in_fvg_3d",False) and signals.get("B3_near_hi20",False)
    signals["Q7_dry_inside_td6"]     = signals.get("F2_dry_after_surge",False) and signals.get("I9_inside_close_hi",False) and signals.get("P3_td_setup_6plus",False)
    signals["Q9_td6_2day_dry_inside"]= signals.get("P3_td_setup_6plus",False) and signals.get("F1_vol_dry",False) and signals.get("F2_dry_after_surge",False) and signals.get("I9_inside_close_hi",False)
    signals["Q16_surge_3down_bounce"]= signals.get("A3_vol_surge",False)    and signals.get("I4b_3down_bounce",False)
    signals["Q21_fvg_vdu_pb"]        = signals.get("O1_in_fvg_3d",False)    and signals.get("M3_vdu_expansion_pb",False)
    signals["Q24_pp_ma20_fvg5d"]     = signals.get("N1_pp_at_ma20",False)   and signals.get("O1_in_fvg_5d",False)
    signals["Q27_k4_j8_ma50_zone"]   = signals.get("J8_pb_zone_ma50",False) and signals.get("K4_first_pb_to_ma20",False)
    signals["Q32_4b_surge_ma50"]     = signals.get("I4b_3down_bounce",False) and signals.get("A3_vol_surge",False) and signals.get("C2_above_ma50",False)
    signals["Q38_k4_j8_mild_pb"]     = signals.get("J8_pb_zone_ma50",False) and signals.get("K4_first_pb_to_ma20",False) and signals.get("E3_mild_pb",False)
    signals["QB2_gap_after_cap"]     = signals.get("I3b_gap_up_strong",False) and signals.get("I5_cap_reversal",False)

    # R+Q combos (US-only: RS filter + TH-validated signal)
    signals["RQ1_rs80_pp_fvg"]      = signals.get("R2_rs3_gt80",False) and signals.get("Q1_pp_ma20_fvg3d",False)
    signals["RQ2_rs80_surge_bounce"] = signals.get("R2_rs3_gt80",False) and signals.get("Q16_surge_3down_bounce",False)
    signals["RQ3_rs80_k4_j8"]       = signals.get("R2_rs3_gt80",False) and signals.get("Q27_k4_j8_ma50_zone",False)
    signals["RQ4_tt_pp_fvg"]        = signals.get("R8_trend_template_full",False) and signals.get("Q1_pp_ma20_fvg3d",False)
    signals["RQ5_tt_surge_bounce"]   = signals.get("R8_trend_template_full",False) and signals.get("Q16_surge_3down_bounce",False)
    signals["RQ6_tt_k4_j8"]         = signals.get("R8_trend_template_full",False) and signals.get("Q27_k4_j8_ma50_zone",False)
    signals["RQ7_tt_dry_inside_td6"] = signals.get("R8_trend_template_full",False) and signals.get("Q7_dry_inside_td6",False)
    signals["RQ8_rs_acc_pb_fvg"]     = signals.get("R4_rs_acc",False) and signals.get("Q1_pp_ma20_fvg3d",False)

    return signals


# ── Main backtest ──────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame, rs: pd.DataFrame):
    all_dates = sorted(df["date"].unique())
    tickers   = df["ticker"].unique()

    px = df.pivot(index="date", columns="ticker", values="close")
    vl = df.pivot(index="date", columns="ticker", values="volume")
    hi = df.pivot(index="date", columns="ticker", values="high")
    lo = df.pivot(index="date", columns="ticker", values="low")
    op = df.pivot(index="date", columns="ticker", values="open")

    # RS data pivot
    rs_pivot = rs.pivot(index="date", columns="ticker", values="rs_3m_pct") if len(rs) else None

    records = []
    print(f"Backtesting {len(all_dates)} dates × {len(tickers)} tickers …")

    for i, dt in enumerate(all_dates):
        if i < MIN_HIST:
            continue

        try:
            px_now = px.loc[dt]
            vl_now = vl.loc[dt]
        except KeyError:
            continue

        # ADTV filter (21-day)
        adtv_idx = all_dates[max(0, i-21):i+1]
        adtv_px  = px.loc[adtv_idx].values if len(adtv_idx) > 0 else np.array([])
        adtv_vl  = vl.loc[adtv_idx].values if len(adtv_idx) > 0 else np.array([])
        if adtv_px.shape[0] == 0:
            continue
        adtv = (adtv_px * adtv_vl).mean(axis=0)
        adtv_s = pd.Series(adtv, index=px.columns)

        eligible = px_now.index[
            (px_now >= PRICE_MIN) &
            (adtv_s >= ADTV_MIN) &
            px_now.notna()
        ].tolist()

        if len(eligible) < 10:
            continue

        # RS rank → top-N focus list (using 63-day price return as RS proxy)
        lb_idx = all_dates[max(0, i - RS_DAYS)]
        px_lb  = px.loc[lb_idx] if lb_idx in px.index else None
        if px_lb is None:
            continue
        rs_raw = (px_now[eligible] / px_lb[eligible] - 1).dropna()
        if len(rs_raw) < TOP_N:
            continue
        focus = rs_raw.nlargest(TOP_N).index.tolist()

        # Market regime: % above 200DMA
        if i >= 200:
            ma200_idx = all_dates[i-200:i+1]
            ma200_m   = px.loc[ma200_idx, eligible].mean(axis=0)
            regime    = "bull" if (px_now[eligible] > ma200_m).mean() >= 0.5 else "bear"
        else:
            regime = "unknown"

        for tkr in focus:
            tkr_idx = all_dates[max(0, i-120):i+1]
            tkr_df  = df[(df["ticker"] == tkr) & (df["date"].isin(tkr_idx))].copy()
            if len(tkr_df) < 30:
                continue

            # RS row (if available)
            rs_row = None
            if rs_pivot is not None and dt in rs_pivot.index and tkr in rs_pivot.columns:
                rs_val = rs_pivot.at[dt, tkr]
                if not np.isnan(rs_val):
                    rs_row_s = rs[(rs["date"] == dt) & (rs["ticker"] == tkr)]
                    if len(rs_row_s):
                        rs_row = rs_row_s.iloc[0].to_dict()

            sigs = compute_signals(tkr_df, rs_row)
            if not sigs:
                continue

            # Forward returns
            fwd = {}
            for fd in FWD_DAYS:
                fut_i = i + fd
                if fut_i < len(all_dates):
                    fut_dt = all_dates[fut_i]
                    fut_px = px.at[fut_dt, tkr] if fut_dt in px.index and tkr in px.columns else np.nan
                    cur_px = px.at[dt, tkr]
                    fwd[f"fwd{fd}"] = fut_px / cur_px - 1 if cur_px > 0 else np.nan
                else:
                    fwd[f"fwd{fd}"] = np.nan

            rec = {"date": dt, "ticker": tkr, "regime": regime}
            rec.update(sigs)
            rec.update(fwd)
            records.append(rec)

        if i % 100 == 0:
            print(f"  …{i}/{len(all_dates)} dates done")

    return pd.DataFrame(records)


# ── Score signals ──────────────────────────────────────────────────────────────
def score_signals(bt: pd.DataFrame) -> pd.DataFrame:
    fwd_col = "fwd3"
    signal_cols = [c for c in bt.columns if c.startswith(
        ("A","B","C","D","E","F","G","H","I","J","K","M","N","O","P","Q","R","X")
    ) and c not in ("date","ticker","regime") and c not in [f"fwd{d}" for d in FWD_DAYS]]

    rows = []
    for sig in signal_cols:
        sub = bt[bt[sig] == True][fwd_col].dropna()
        if len(sub) < 8:
            continue
        rows.append({
            "signal":   sig,
            "n_obs":    len(sub),
            "hit_rate": (sub > 0).mean() * 100,
            "avg_ret":  sub.mean() * 100,
            "fwd1_hit": (bt[bt[sig]==True]["fwd1"].dropna() > 0).mean() * 100 if "fwd1" in bt.columns else np.nan,
            "fwd5_hit": (bt[bt[sig]==True]["fwd5"].dropna() > 0).mean() * 100 if "fwd5" in bt.columns else np.nan,
            "fwd5_avg": bt[bt[sig]==True]["fwd5"].dropna().mean() * 100 if "fwd5" in bt.columns else np.nan,
        })

    if not rows:
        print("WARNING: no signals with enough observations — check signal_cols filter")
        return pd.DataFrame()
    df_score = pd.DataFrame(rows).sort_values("hit_rate", ascending=False)

    # Bull-only scores
    bull_rows = []
    bt_bull = bt[bt["regime"] == "bull"]
    for sig in signal_cols:
        sub = bt_bull[bt_bull[sig] == True][fwd_col].dropna()
        if len(sub) < 8:
            continue
        bull_rows.append({
            "signal":   sig,
            "n_obs":    len(sub),
            "hit_rate_bull": (sub > 0).mean() * 100,
            "avg_ret_bull":  sub.mean() * 100,
        })
    df_bull = pd.DataFrame(bull_rows)

    if len(df_bull):
        df_score = df_score.merge(df_bull, on="signal", how="left")

    return df_score


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df, rs = load_data()

    # Incremental: skip already-processed dates
    if OUT_RESULTS.exists():
        existing = pd.read_csv(OUT_RESULTS, parse_dates=["date"])
        last_date = existing["date"].max()
        print(f"Incremental mode: last processed date = {last_date.date()}")
        new_records = run_backtest(df, rs)
        new_records = new_records[new_records["date"] > last_date]
        combined = pd.concat([existing, new_records], ignore_index=True)
    else:
        combined = run_backtest(df, rs)

    combined.to_csv(OUT_RESULTS, index=False)
    print(f"Saved {len(combined):,} records → {OUT_RESULTS}")

    # Write to SQLite (packed-blob format, same as Thai pipeline)
    if _DB_AVAILABLE:
        try:
            n_written = _db_write(combined)
            print(f"SQLite: {n_written:,} rows → {_DB_PATH}")
        except Exception as e:
            print(f"[WARN] SQLite write failed (CSV still saved): {e}")

    # Score
    summary = score_signals(combined)
    summary.to_csv(OUT_SUMMARY, index=False)
    print(f"\nTop 20 signals by fwd3 hit rate (≥8 obs):")
    top = summary[summary["n_obs"] >= 15].head(20)
    print(top[["signal","n_obs","hit_rate","avg_ret","fwd5_hit","fwd5_avg"]].to_string(index=False))
    print(f"\nSaved summary → {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
