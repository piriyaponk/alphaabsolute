"""
AlphaAbsolute — NBIS Profile Screener
======================================
NBIS (Nebius Group) moved from $64 -> $221 (+245%) in ~9 months.

The EARLY signal before the big acceleration was a specific RS pattern:
  - rs_1m_pct SURGING (75-96) while rs_6m_pct still LAGGING (30-65)
  - This creates large positive rs_momentum_3m_6m: the 3M window caught the breakout,
    the 6M window still includes the pre-move base. This gap = "recent explosive mover,
    not yet fully discovered in long windows"
  - rs_momentum_1m_3m also positive: short-term is still accelerating vs medium-term
  - BEFORE becoming a full Leader (rs_3m < 90), not AFTER it's already obvious to everyone

This screener finds tickers currently matching that NBIS early-RS-acceleration profile.

Usage:  python scripts/pre_compute/find_nbis_profile.py
Output: data/screening/nbis_profile_candidates.json + console
"""

import sqlite3
import json
import os
import numpy as np
from datetime import datetime

DB_PATH = "data/ohlcv.db"
LABELS_PATH = "data/themes/ticker_labels.json"
OUTPUT_DIR = "data/screening"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# --- NBIS Reference Profile --------------------------------------------------
# Based on NBIS's data at the start of its acceleration phase (Mar 27 - Apr 8 2026)
# just before it crossed into full Leader and the price exploded Apr 9 onward.
#
# Key observations from the rs_daily data:
#  T-14d (Mar 27): rs_1m=69, rs_3m=79, rs_6m=47 -> comp=69, mom_3m_6m=+32, mom_1m_3m=-10
#  T-10d (Mar 30): rs_1m=83, rs_3m=73, rs_6m=39 -> comp=70, mom_3m_6m=+34, mom_1m_3m=+10
#  T-8d  (Mar 31): rs_1m=93, rs_3m=85, rs_6m=46 -> comp=80, mom_3m_6m=+39, mom_1m_3m=+8
#  T-5d  (Apr 2):  rs_1m=90, rs_3m=88, rs_6m=46 -> comp=81, mom_3m_6m=+42, mom_1m_3m=+2
#  Breakthrough (Apr 9): rs_3m=93, rs_6m=71 -> first time 3M>80 & 6M>70 = full Leader
#
# Core pattern: stock recently had a huge catalyst/breakout that lifted the 1M+3M RS
# but 6M window still includes pre-move base -> big 3M-6M gap
#
# Also key: NBIS was in a recognized theme (AI/Defense/NeoCloud) with clear narrative

NBIS_PROFILE = {
    "description": "Early RS acceleration — NBIS-like pre-Leader pattern",
    # CRITICAL INSIGHT: NBIS-type stocks are often NOT in Stage 2 at the early phase.
    # They broke out from deep bases (NBIS was -50%+ off highs at Sep 2025 breakout).
    # Stage 2 comes LATER as MAs catch up. We screen for the RS momentum signal, not MA structure.

    "rs_1m_pct_min": 72,      # 1M RS must be strong — recent move is real
    "rs_1m_pct_max": 99,      # Exclude ONLY perfect 100 (often small-cap noise)
    "rs_3m_pct_min": 60,      # 3M RS approaching or entering Leader territory
    "rs_3m_pct_max": 94,      # Exclude if already maxed — everyone knows it
    "rs_6m_pct_max": 75,      # 6M must lag 3M significantly (recent breakout signature)
    "rs_3m_minus_6m_min": 15, # The KEY gap: 3M must outperform 6M by at least 15 pts
    "rs_1m_minus_3m_min": -8, # 1M only slightly below 3M is acceptable
    "pct_from_52w_high_min": -70, # Allow deep corrections (NBIS was -50% off highs)
    "adtv_min_usd": 5_000_000,    # $5M ADTV minimum
    "stage2_required": False,     # NOT required — early movers often below 200DMA
    "pct_above_ma50_min": -20,    # Must be within 20% of MA50 (showing recovery)
}


def get_latest_date(conn):
    c = conn.cursor()
    c.execute("SELECT MAX(SUBSTR(date,1,10)) FROM screening_results")
    return c.fetchone()[0]


def compute_nbis_similarity(row, thresholds):
    """Score a row's similarity to the NBIS early-RS profile (0-100).

    Higher = more similar to NBIS pre-breakout profile.
    Components:
      - RS momentum gap (3M-6M): most important signal (35%)
      - RS 1M strength: recent momentum (25%)
      - RS 3M position in "approaching Leader" zone: timing (20%)
      - 1M-3M momentum direction (10%)
      - Stage 2 structure (10%)
    """
    score = 0.0

    rs1m = row.get('rs_1m_pct')
    rs3m = row.get('rs_3m_pct')
    rs6m = row.get('rs_6m_pct')
    mom_3m_6m = row.get('rs_momentum_3m_6m')
    mom_1m_3m = row.get('rs_momentum_1m_3m')
    stage2 = row.get('stage2_flag', 0)

    if rs1m is None or rs3m is None or rs6m is None:
        return 0

    # Component 1: 3M-6M momentum gap (35 pts)
    # NBIS had +30 to +42. Higher gap = more NBIS-like.
    gap_36 = rs3m - rs6m
    if gap_36 >= 40:
        score += 35
    elif gap_36 >= 30:
        score += 28 + (gap_36 - 30) * 0.7
    elif gap_36 >= 20:
        score += 18 + (gap_36 - 20) * 1.0
    elif gap_36 >= 10:
        score += 5 + (gap_36 - 10) * 1.3
    elif gap_36 >= 0:
        score += gap_36 * 0.5

    # Component 2: RS 1M strength (25 pts)
    # NBIS was 83-93 in the pre-acceleration window
    if rs1m >= 85:
        score += 25
    elif rs1m >= 75:
        score += 15 + (rs1m - 75) * 1.0
    elif rs1m >= 65:
        score += 5 + (rs1m - 65) * 1.0
    elif rs1m >= 55:
        score += (rs1m - 55) * 0.5

    # Component 3: RS 3M in "approaching Leader" zone (20 pts)
    # Sweet spot: 65-88. Penalty if already maxed (>92) = everyone sees it
    if 70 <= rs3m <= 88:
        score += 20
    elif 60 <= rs3m < 70:
        score += 12 + (rs3m - 60) * 0.8
    elif 88 < rs3m <= 92:
        score += 20 - (rs3m - 88) * 2.5  # slight penalty as it becomes obvious
    elif rs3m > 92:
        score += max(0, 10 - (rs3m - 92) * 2)  # fading if already very high

    # Component 4: 1M-3M momentum direction (10 pts)
    # NBIS: +2 to +10. Positive = 1M accelerating vs 3M
    if mom_1m_3m is not None:
        if mom_1m_3m >= 10:
            score += 10
        elif mom_1m_3m >= 5:
            score += 7 + (mom_1m_3m - 5) * 0.6
        elif mom_1m_3m >= 0:
            score += 4 + mom_1m_3m * 0.6
        elif mom_1m_3m >= -5:
            score += 2 + (mom_1m_3m + 5) * 0.4
        else:
            score += max(0, 2 + (mom_1m_3m + 5) * 0.4)
    else:
        score += 5  # neutral if no data

    # Component 5: Stage 2 structure (10 pts)
    if stage2 == 1:
        score += 10
    else:
        score += 0  # no credit for not being in uptrend

    return round(min(score, 100), 1)


def screen_nbis_profile(conn, universe, labels):
    """Screen for NBIS-like early RS acceleration profiles."""
    print("\n=== NBIS Profile Screen ===")
    print("Looking for: strong 1M RS + lagging 6M RS + large 3M-6M gap")
    print("(The 'recent breakout not yet reflected in long-window RS' pattern)")
    print()

    c = conn.cursor()
    t = NBIS_PROFILE

    latest_date = get_latest_date(conn)
    print(f"Screening date: {latest_date}")
    print(f"Universe: {len(universe)} tickers")

    placeholders = ','.join(['?' for _ in universe])

    # Pull all relevant data from screening_results + ticker_meta
    c.execute(f"""
        SELECT
            s.ticker,
            s.label,
            s.rs_1m_pct,
            s.rs_3m_pct,
            s.rs_6m_pct,
            s.rs_composite,
            s.rs_momentum_1m_3m,
            s.rs_momentum_3m_6m,
            s.phase,
            s.adtv_6m_usd,
            s.last_close,
            s.pct_from_52w_high,
            s.pct_from_3m_high,
            s.stage2_flag,
            s.rs_comp_chg_1w,
            s.rs_comp_chg_4w,
            s.vol_ma_20d,
            s.rel_vol_today,
            s.mode_b_eligible,
            s.pct_above_ma50,
            s.pct_above_ma200,
            s.gate_rs,
            s.gate_adtv,
            s.gate_52w,
            s.gate_stage2
        FROM screening_results s
        WHERE SUBSTR(s.date,1,10) = ? AND s.ticker IN ({placeholders})
    """, [latest_date] + universe)

    rows = c.fetchall()
    print(f"Loaded {len(rows)} rows from screening_results")

    candidates = []
    passing_hard_gates = 0
    stage2_count = 0

    for row in rows:
        (ticker, label, rs1m, rs3m, rs6m, rs_comp,
         mom_1m_3m, mom_3m_6m, phase,
         adtv, last_close, pct_52w, pct_3m,
         stage2, chg1w, chg4w,
         vol_ma20, rel_vol, mode_b,
         pct_ma50, pct_ma200,
         g_rs, g_adtv, g_52w, g_stage2) = row

        if rs1m is None or rs3m is None or rs6m is None:
            continue

        gap_3m_6m = rs3m - rs6m
        gap_1m_3m = rs1m - rs3m

        # -- Hard gates (must pass all) --------------------------------------
        if rs1m < t['rs_1m_pct_min']:              continue  # 1M too weak
        if rs1m > t['rs_1m_pct_max']:              continue  # exclude 100 noise
        if rs3m < t['rs_3m_pct_min']:              continue  # 3M too weak
        if rs3m > t['rs_3m_pct_max']:              continue  # 3M already well-known
        if rs6m > t['rs_6m_pct_max']:              continue  # 6M already strong = not early
        if gap_3m_6m < t['rs_3m_minus_6m_min']:   continue  # gap too small = not a recent mover
        if gap_1m_3m < t['rs_1m_minus_3m_min']:   continue  # 1M decelerating badly
        if pct_52w is None or pct_52w < t['pct_from_52w_high_min']: continue  # too extended down
        if adtv is None or adtv < t['adtv_min_usd']:               continue  # too illiquid
        # MA50 proximity: must be within 20% below MA50 (showing recovery, not just noise bounce)
        if pct_ma50 is not None and pct_ma50 < t['pct_above_ma50_min']: continue

        passing_hard_gates += 1

        # Additional context flags
        is_themed = label in {
            "AI_Related", "Memory_HBM", "Space", "Quantum", "Photonics",
            "DefenseTech", "DataCenter", "Nuclear_SMR", "NeoCloud", "AI_Infra",
            "DataCenter_Infra", "Drone_UAV", "Robotics", "Connectivity"
        }

        # Similarity score
        row_dict = {
            'rs_1m_pct': rs1m, 'rs_3m_pct': rs3m, 'rs_6m_pct': rs6m,
            'rs_momentum_1m_3m': mom_1m_3m, 'rs_momentum_3m_6m': mom_3m_6m,
            'stage2_flag': stage2
        }
        sim_score = compute_nbis_similarity(row_dict, t)

        candidates.append({
            'ticker': ticker,
            'label': label,
            'is_themed': is_themed,
            'rs_1m_pct': round(rs1m, 1),
            'rs_3m_pct': round(rs3m, 1),
            'rs_6m_pct': round(rs6m, 1),
            'rs_composite': round(rs_comp, 1) if rs_comp else None,
            'rs_momentum_1m_3m': round(mom_1m_3m, 1) if mom_1m_3m else None,
            'rs_momentum_3m_6m': round(mom_3m_6m, 1) if mom_3m_6m else None,
            'gap_3m_6m': round(gap_3m_6m, 1),
            'gap_1m_3m': round(gap_1m_3m, 1),
            'phase': phase,
            'stage2': bool(stage2),
            'adtv_m': round(adtv / 1e6, 1) if adtv else None,
            'last_close': round(last_close, 2) if last_close else None,
            'pct_from_52w_high': round(pct_52w, 1) if pct_52w else None,
            'pct_from_3m_high': round(pct_3m, 1) if pct_3m else None,
            'rs_chg_1w': round(chg1w, 1) if chg1w else None,
            'rs_chg_4w': round(chg4w, 1) if chg4w else None,
            'vol_ma_20': round(vol_ma20 / 1e6, 1) if vol_ma20 else None,
            'rel_vol': round(rel_vol, 2) if rel_vol else None,
            'pct_above_ma50': round(pct_ma50, 1) if pct_ma50 else None,
            'pct_above_ma200': round(pct_ma200, 1) if pct_ma200 else None,
            'mode_b_eligible': bool(mode_b),
            'nbis_similarity': sim_score,
            'gate_rs_mode_a': bool(g_rs),
            'gate_adtv_mode_a': bool(g_adtv),
            'gate_52w_mode_a': bool(g_52w),
        })

    # Sort by NBIS similarity score (primary) then 3M-6M gap (secondary)
    candidates.sort(key=lambda x: (-x['nbis_similarity'], -x['gap_3m_6m']))

    print(f"\nPassing hard gates: {passing_hard_gates} tickers")
    print(f"After scoring: {len(candidates)} candidates")

    return candidates, latest_date


def print_candidates(candidates, n=30):
    """Print formatted table of NBIS-profile candidates."""
    print(f"\n{'='*100}")
    print("NBIS-PROFILE CANDIDATES (Pre-Leader RS Acceleration)")
    print("Key: high rs_1m + lagging rs_6m + large 3M-6M gap = recent breakout not yet 'stale'")
    print(f"{'='*100}")

    print(f"\n  {'#':<3} {'Tkr':<7} {'Label':<18} {'Score':>6} {'1M':>4} {'3M':>4} {'6M':>4} "
          f"{'Comp':>5} {'Gap36':>6} {'Mom13':>6} {'52W%':>6} {'3M%':>6} "
          f"{'S2':>3} {'ADTV':>7} {'Price':>8} {'RS1W':>5} {'RS4W':>5}")
    print(f"  {'-'*100}")

    themed = []
    unthemed = []
    for c in candidates:
        if c['is_themed']:
            themed.append(c)
        else:
            unthemed.append(c)

    def fmt_row(i, c):
        label = (c['label'] or '?')[:17]
        score = f"{c['nbis_similarity']:.0f}"
        rs1  = f"{c['rs_1m_pct']:.0f}"
        rs3  = f"{c['rs_3m_pct']:.0f}"
        rs6  = f"{c['rs_6m_pct']:.0f}"
        comp = f"{c['rs_composite']:.0f}" if c['rs_composite'] else 'N/A'
        gap36 = f"+{c['gap_3m_6m']:.0f}"
        mom13 = f"{c['rs_momentum_1m_3m']:+.0f}" if c['rs_momentum_1m_3m'] else 'N/A'
        pct52 = f"{c['pct_from_52w_high']:.0f}%" if c['pct_from_52w_high'] else 'N/A'
        pct3m = f"{c['pct_from_3m_high']:.0f}%" if c['pct_from_3m_high'] else 'N/A'
        s2   = 'S2' if c['stage2'] else '--'
        adtv = f"${c['adtv_m']:.0f}M" if c['adtv_m'] else 'N/A'
        price = f"${c['last_close']:.2f}" if c['last_close'] else 'N/A'
        rs1w = f"{c['rs_chg_1w']:+.0f}" if c['rs_chg_1w'] else 'N/A'
        rs4w = f"{c['rs_chg_4w']:+.0f}" if c['rs_chg_4w'] else 'N/A'
        return (f"  {i:<3} {c['ticker']:<7} {label:<18} {score:>6} {rs1:>4} {rs3:>4} {rs6:>4} "
                f"{comp:>5} {gap36:>6} {mom13:>6} {pct52:>6} {pct3m:>6} "
                f"{s2:>3} {adtv:>7} {price:>8} {rs1w:>5} {rs4w:>5}")

    if themed:
        print(f"\n  --- THEMED TICKERS ({len(themed)}) ---")
        for i, c in enumerate(themed[:n], 1):
            print(fmt_row(i, c))

    if unthemed:
        print(f"\n  --- UNTHEMED / UNKNOWN ({len(unthemed)}) ---")
        for i, c in enumerate(unthemed[:15], 1):
            print(fmt_row(i, c))

    # Summary of top 10
    print(f"\n{'-'*60}")
    print("TOP 10 NBIS-PROFILE CANDIDATES SUMMARY:")
    print(f"{'-'*60}")
    for i, c in enumerate(candidates[:10], 1):
        themed_tag = "[*]" if c['is_themed'] else "   "
        s2_tag = "Stage2" if c['stage2'] else "NoS2"
        gap_tag = f"3M-6M gap +{c['gap_3m_6m']:.0f}"
        print(f"  {i:>2}. {themed_tag.strip()} ${c['ticker']:<7} "
              f"[{c['label'] or '?':<18}] "
              f"Score={c['nbis_similarity']:.0f}/100 | "
              f"RS {c['rs_1m_pct']:.0f}/{c['rs_3m_pct']:.0f}/{c['rs_6m_pct']:.0f} | "
              f"{gap_tag} | {s2_tag} | "
              f"${c['last_close']:.2f}")


def analyze_nbis_self(conn):
    """Show NBIS's own current profile for comparison."""
    print("\n--- NBIS Current Profile (for reference) ---")
    c = conn.cursor()
    c.execute("""
        SELECT rs_1m_pct, rs_3m_pct, rs_6m_pct, rs_composite,
               rs_momentum_1m_3m, rs_momentum_3m_6m,
               phase, rs_comp_chg_1w, rs_comp_chg_4w
        FROM screening_results
        WHERE ticker='NBIS'
        ORDER BY SUBSTR(date,1,10) DESC LIMIT 1
    """)
    row = c.fetchone()
    if row:
        r1m, r3m, r6m, comp, m13, m36, phase, chg1w, chg4w = row
        print(f"  RS: 1M={r1m:.0f} | 3M={r3m:.0f} | 6M={r6m:.0f} | Comp={comp:.0f}")
        print(f"  Momentum: 1M-3M={m13:+.1f} | 3M-6M={m36:+.1f}")
        print(f"  Phase: {phase} | RS chg 1W={chg1w:+.1f} | 4W={chg4w:+.1f}")
        gap = r3m - r6m
        print(f"  Current 3M-6M gap: +{gap:.0f} (NBIS pre-move profile had gap +30 to +42)")
        print(f"  NOTE: NBIS is now a FULL Leader (comp={comp:.0f}) — no longer 'early'")
    else:
        print("  NBIS not found in screening_results")


def main():
    print("AlphaAbsolute — NBIS Profile Screener")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"DB: {DB_PATH}")

    print("\n--- NBIS Historical Profile (Pre-Move Signature) ---")
    print("  Based on rs_daily data: Mar 27 – Apr 8 2026 (just before the explosive Apr 9 breakout)")
    print("  Price trajectory: $64 (Sep base) -> $95 (+50% catalyst day) -> $113-135 (Sep-Oct)")
    print("                    $75 (Dec 2025 base) -> $107 (Feb 2026) -> $130 (Mar 16)")
    print("                    -> $166 (Apr 15) -> $221 (May 14) = +245% from Sep low")
    print()
    print("  RS Signature at T-0 (Mar 27, start of acceleration):")
    print("    rs_1m=69 | rs_3m=79 | rs_6m=47 | comp=69 | mom_3m_6m=+32 | phase=Emerging")
    print("  RS Signature at T+4 (Apr 2, just before Leader transition):")
    print("    rs_1m=90 | rs_3m=88 | rs_6m=46 | comp=81 | mom_3m_6m=+42 | phase=Emerging")
    print("  RS Signature at T+10 (Apr 9, FIRST DAY AS FULL LEADER):")
    print("    rs_1m=98 | rs_3m=93 | rs_6m=71 | comp=90 | phase=Leader")
    print()
    print("  KEY PATTERN: 1M surging, 3M approaching Leader, 6M LAGGING (+30-42 3M-6M gap)")
    print("  = 'I had a recent hard breakout but 6M window still sees the old base'")

    with open(LABELS_PATH) as f:
        data = json.load(f)
    labels = data['labels']
    universe = list(labels.keys())

    conn = sqlite3.connect(DB_PATH)

    try:
        # Show NBIS's own current RS for comparison
        analyze_nbis_self(conn)

        # Run the screen
        candidates, screen_date = screen_nbis_profile(conn, universe, labels)

        # Print results
        print_candidates(candidates)

        # Save to JSON
        output = {
            "date": screen_date,
            "generated_at": datetime.now().isoformat(),
            "nbis_reference_profile": {
                "description": "NBIS rs_daily data Mar 27 – Apr 8 2026 (pre-Leader acceleration)",
                "t0_mar27": {"rs_1m": 69, "rs_3m": 79, "rs_6m": 47, "comp": 69, "gap_3m_6m": 32},
                "t4_apr2":  {"rs_1m": 90, "rs_3m": 88, "rs_6m": 46, "comp": 81, "gap_3m_6m": 42},
                "t10_apr9": {"rs_1m": 98, "rs_3m": 93, "rs_6m": 71, "comp": 90, "phase": "Leader"},
                "key_signal": "rs_3m - rs_6m gap >= 30 while rs_3m still below 93"
            },
            "screen_criteria": NBIS_PROFILE,
            "total_candidates": len(candidates),
            "themed_candidates": sum(1 for c in candidates if c['is_themed']),
            "candidates": candidates
        }

        out_path = f"{OUTPUT_DIR}/nbis_profile_{screen_date}.json"
        latest_path = f"{OUTPUT_DIR}/nbis_profile_latest.json"
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2)
        with open(latest_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2)

        print(f"\n[OK] Saved {len(candidates)} candidates to {out_path}")
        print(f"     Also saved as {latest_path}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
