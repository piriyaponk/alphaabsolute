"""
S4 Post-Mortem Computer
========================
Run on the 1st trading day of each month (or manually).
Computes last month's:
  1. Our top-15 pick performance
  2. Leaders we MISSED — stocks that ran but weren't in our portfolio
     Classified by WHY: ranked out vs gate-blocked
  3. Big losers — positions we held that dropped >5%

Writes: data/paper_trading/last_month_result.json
Then calls s4_obsidian_writer.write_postmortem() to push to Obsidian.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.brain.s4_obsidian_writer import write_postmortem


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def compute_last_month(target_month: str | None = None) -> dict:
    """
    target_month: "YYYY-MM" — defaults to last month.
    Returns the full post-mortem dict.
    """
    today = date.today()
    if target_month:
        yr, mo = map(int, target_month.split("-"))
    else:
        # Last month
        first_of_this = today.replace(day=1)
        last_month_end = first_of_this - timedelta(days=1)
        yr, mo = last_month_end.year, last_month_end.month

    month_str = f"{yr}-{mo:02d}"
    print(f"Computing post-mortem for {month_str}...")

    # Load signals parquet
    sig_path = ROOT / "data" / "backtest" / "signals.parquet"
    if not sig_path.exists():
        print("[SKIP] signals.parquet not found")
        return {}

    sig = pd.read_parquet(sig_path)
    sig["date"] = pd.to_datetime(sig["date"])

    close_px = sig.pivot_table(index="date", columns="ticker", values="close").sort_index()
    qqq = close_px["QQQ"].dropna()

    # IWM regime gate
    iwm = close_px["IWM"].dropna()
    iwm_ma200 = iwm.rolling(200, min_periods=150).mean()
    all_dates = sig["date"].sort_values().unique()
    iwm_bull = (iwm > iwm_ma200).reindex(all_dates, method="ffill").fillna(True)

    # Find the rebalance date for this month (last day of target month)
    import calendar
    last_day = calendar.monthrange(yr, mo)[1]
    rebal_ts = pd.Timestamp(yr, mo, last_day)

    # Find closest available date in signals
    avail = sig[sig["date"] <= rebal_ts]["date"].max()
    if pd.isna(avail):
        print(f"[SKIP] No signal data for {month_str}")
        return {}

    ts = avail

    # Next month end (for forward return)
    if mo == 12:
        ne_ts = pd.Timestamp(yr + 1, 1, 31)
    else:
        ne_last = calendar.monthrange(yr, mo + 1)[1]
        ne_ts = pd.Timestamp(yr, mo + 1, ne_last)

    ne_avail = sig[sig["date"] <= ne_ts]["date"].max()
    if pd.isna(ne_avail):
        print(f"[SKIP] No forward data (need prices through {ne_ts.strftime('%Y-%m')})")
        return {}

    ne = ne_avail

    # Regime this month
    bull = bool(iwm_bull.get(ts, True))
    vt = 0.75 if bull else 0.95
    exp = 1.0 if bull else 0.5
    regime = "BULL" if bull else "BEAR"

    # All stocks passing gates
    day = sig[sig["date"] == ts].copy()

    gate_rs    = day["rs_pct"] >= 80
    gate_ma200 = day["price_vs_ma200_pct"] >= 0
    gate_vt    = day["vol_trend"] >= vt
    gate_adtv  = day["adtv_63m"] >= 10
    gate_all   = gate_rs & gate_ma200 & gate_vt & gate_adtv

    passed = day[gate_all].copy()
    ranked = passed.nlargest(30, "rs_pct")  # top 30 to find missed leaders

    # Our top-15
    our_picks = ranked.head(15)

    def fwd_return(ticker: str) -> float | None:
        if ticker not in close_px.columns:
            return None
        try:
            p0 = close_px.loc[close_px.index <= ts, ticker].dropna().iloc[-1]
            p1 = close_px.loc[close_px.index <= ne, ticker].dropna().iloc[-1]
            return (p1 / p0 - 1) * 100
        except Exception:
            return None

    # QQQ return
    qqq_ret = fwd_return("QQQ")

    # ── Our picks performance ──────────────────────────────
    picks_out = []
    port_ret_sum = 0.0
    n_picks = len(our_picks)
    equal_w = 1.0 / n_picks if n_picks > 0 else 0

    for _, row in our_picks.iterrows():
        tkr = row["ticker"]
        ret = fwd_return(tkr)
        if ret is None:
            continue
        picks_out.append({
            "ticker":       tkr,
            "weight":       round(equal_w * exp * 100, 1),
            "rs_at_entry":  round(float(row["rs_pct"]), 1),
            "return":       round(ret, 2),
            "loss_reason":  None,   # CIO fills in for big losers
        })
        port_ret_sum += ret * equal_w * exp

    port_ret = round(port_ret_sum, 2)

    # ── MISSED LEADERS ─────────────────────────────────────
    # 1. Stocks ranked 16-30 (passed all gates, just outside top-15)
    ranked_outside = ranked.iloc[15:30]
    missed_out = []

    for _, row in ranked_outside.iterrows():
        tkr = row["ticker"]
        ret = fwd_return(tkr)
        if ret is None or ret < 10:   # only flag if they ran >10%
            continue
        missed_out.append({
            "ticker":       tkr,
            "return":       round(ret, 2),
            "rs_at_entry":  round(float(row["rs_pct"]), 1),
            "why_missed":   f"Ranked {int(ranked.index.get_loc(row.name)) + 1} — outside top-15",
            "gate_failed":  None,
        })

    # 2. Stocks that FAILED a gate but ran big anyway
    # These are the most interesting — the gate blocked a winner
    failed_gate = day[~gate_all].copy()

    for _, row in failed_gate.iterrows():
        tkr = row["ticker"]
        ret = fwd_return(tkr)
        if ret is None or ret < 15:   # only flag if they ran >15%
            continue

        # Diagnose which gate failed
        failed_gates = []
        if not gate_rs.get(row.name, True):
            failed_gates.append(f"rs_pct={row['rs_pct']:.0f} < 80")
        if not gate_ma200.get(row.name, True):
            failed_gates.append(f"price_vs_ma200={row.get('price_vs_ma200_pct', 0):.1f}% < 0")
        if not gate_vt.get(row.name, True):
            failed_gates.append(f"vol_trend={row['vol_trend']:.3f} < {vt}")
        if not gate_adtv.get(row.name, True):
            failed_gates.append(f"adtv={row.get('adtv_63m', 0):.1f}M < 10M")

        gate_str = " | ".join(failed_gates) if failed_gates else "unknown gate"

        missed_out.append({
            "ticker":       tkr,
            "return":       round(ret, 2),
            "rs_at_entry":  round(float(row.get("rs_pct", 0)), 1),
            "why_missed":   "gate blocked",
            "gate_failed":  gate_str,
        })

    # Sort by return descending
    missed_out = sorted(missed_out, key=lambda x: -x["return"])[:10]

    # ── IWM regime check ───────────────────────────────────
    # Was IWM gate right? (next month: did IWM>MA200 remain consistent?)
    ne_bull = bool(iwm_bull.get(ne, True))
    regime_correct = (bull == ne_bull)   # simple check — regime didn't flip mid-month unexpectedly

    # ── Assemble result ────────────────────────────────────
    result = {
        "month":             month_str,
        "regime":            regime,
        "regime_correct":    regime_correct,
        "portfolio_return":  port_ret,
        "qqq_return":        round(qqq_ret, 2) if qqq_ret is not None else 0,
        "picks":             picks_out,
        "missed_leaders":    missed_out,
    }

    # Save
    out_path = ROOT / "data" / "paper_trading" / "last_month_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Post-mortem data saved: {out_path}")

    return result


def run(target_month: str | None = None):
    print("=" * 55)
    print("S4 Post-Mortem Computer")
    print("=" * 55)

    result = compute_last_month(target_month)
    if not result:
        return

    print("\nWriting to Obsidian...")
    write_postmortem(result)

    # Quick summary print
    print(f"\n{'='*40}")
    print(f"Month:     {result['month']}")
    print(f"Regime:    {result['regime']}")
    print(f"Portfolio: {result['portfolio_return']:+.1f}%  vs QQQ {result['qqq_return']:+.1f}%")
    print(f"Picks:     {len(result['picks'])} | Missed leaders: {len(result['missed_leaders'])}")

    if result["missed_leaders"]:
        print("\nTop missed leaders:")
        for m in result["missed_leaders"][:5]:
            gate = f" [GATE: {m['gate_failed']}]" if m["gate_failed"] else " [rank out]"
            print(f"  {m['ticker']:6} +{m['return']:.1f}%{gate}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", help="YYYY-MM to compute (default: last month)")
    args = parser.parse_args()
    run(args.month)
