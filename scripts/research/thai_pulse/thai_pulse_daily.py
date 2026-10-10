"""
thai_pulse_daily.py — PULSE-TH Daily Screener
==============================================
Standard architecture identical to pulse_us_daily.py:
  1. Load quality signals from q_library_th.json (h3>=63%, pre-computed)
  2. Read today's row per ticker from thai_entry_screen_results.csv
  3. Build signal matrix (N_tickers × N_quality_signals)
  4. calc_pulse_scores() — IDENTICAL function to PULSE-US
  5. Rank by h3 (primary) + breadth (tiebreaker)
  6. Send Telegram: [TH] header + focus list + top 5

calc_pulse_scores() is the canonical shared formula:
  breadth     = distinct quality FAMILIES that fired ≥1 signal
  breadth_pct = breadth / n_quality_families * 100
  avg_h3      = avg h3 of top-3 individual quality signals that fired (%)

Usage:
  python scripts/research/thai_pulse/thai_pulse_daily.py
  python scripts/research/thai_pulse/thai_pulse_daily.py --date 2026-10-03
  python scripts/research/thai_pulse/thai_pulse_daily.py --no-telegram
"""

import json, os, sys, argparse
import sqlite3
import warnings
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timedelta
from pathlib import Path

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

ROOT      = Path(__file__).resolve().parents[3]
LIB_PATH  = ROOT / "data" / "research" / "thai_pulse" / "q_library_th.json"
OUT_PATH  = ROOT / "data" / "research" / "thai_pulse" / "pulse_th_daily_signals.json"

sys.path.insert(0, str(ROOT / "scripts" / "research"))
from entry_screen_db import read_entry_screen, get_max_date as _db_max_date
from pulse_db import upsert_pulse_signals, write_shortlist_json

_OHLCV_DB = ROOT / "data" / "research" / "thai_ohlcv.db"


def _compute_rs_from_ohlcv(tickers: list, as_of_date: str) -> dict:
    """Compute RS percentile (3M return rank) from thai_ohlcv.db.
    Returns {ticker: rs_percentile_0_to_100}.
    """
    if not _OHLCV_DB.exists():
        return {}
    try:
        conn = sqlite3.connect(str(_OHLCV_DB))
        # Get close prices for all tickers: today and ~63 trading days ago (~3 months)
        q = """
            SELECT ticker, date, close FROM thai_ohlcv
            WHERE ticker IN ({placeholders})
              AND date <= ?
              AND close > 0
            ORDER BY ticker, date
        """.format(placeholders=",".join("?" * len(tickers)))
        rows = conn.execute(q, tickers + [as_of_date]).fetchall()
        conn.close()
        if not rows:
            return {}
        df = pd.DataFrame(rows, columns=["ticker", "date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        # Get latest and 63-day-ago price per ticker
        returns = {}
        for tkr, grp in df.groupby("ticker"):
            grp = grp.sort_values("date")
            if len(grp) < 10:
                continue
            latest = grp["close"].iloc[-1]
            lookback = grp.iloc[max(0, len(grp) - 63)]["close"]
            if lookback > 0:
                returns[tkr] = (latest / lookback - 1) * 100
        if not returns:
            return {}
        # Compute percentile rank
        vals = list(returns.values())
        tickers_list = list(returns.keys())
        import scipy.stats as _stats
        ranks = _stats.rankdata(vals)
        n = len(vals)
        return {t: round(float(r / n * 100), 1) for t, r in zip(tickers_list, ranks)}
    except Exception as e:
        print(f"[rs_compute] Failed: {e}")
        return {}


# ── Load signal library ─────────────────────────────────────────────────────
def load_quality_signals() -> list[dict]:
    """Load quality signals (h3>=63%, grade A/S/S_BOTH) from q_library_th.json."""
    with open(LIB_PATH) as f:
        lib = json.load(f)
    quality = [r for r in lib["results"] if r["grade"] in ("S_BOTH", "S", "A")]
    print(f"[library] {lib['total_scored']} total | {len(quality)} quality signals loaded")
    return quality


# ── Load today's signal matrix from CSV ────────────────────────────────────
def load_signal_matrix(quality_sigs: list[dict], target_date: str | None = None) -> dict | None:
    """
    Read thai_entry_screen_results.csv, pick today's rows, build signal matrix.
    Returns dict with: signal_matrix, tickers, sig_h3_arr, sig_labels,
                       n_quality_families, rs_map, price_date
    """
    # Point query: read only the target date from SQLite (fast)
    if target_date:
        td_str = str(target_date)[:10]
        # Find latest available date <= target_date
        max_d = _db_max_date()
        if max_d and td_str > max_d:
            td_str = max_d
        df = read_entry_screen(date=td_str)
    else:
        max_d = _db_max_date()
        if not max_d:
            print("[ERROR] No data in SQLite — run thai_entry_screen.py first")
            return None
        td_str = max_d
        df = read_entry_screen(date=td_str)

    if df.empty:
        print(f"[WARN] No data in SQLite for {td_str}")
        return None

    df["date"] = pd.to_datetime(df["date"])
    td = df["date"].iloc[0]
    today_df = df.copy()
    if today_df.empty:
        print(f"[WARN] No rows for {td.date()}")
        return None

    # Deduplicate: keep last row per ticker (CSV may have duplicates after incremental appends)
    n_before = len(today_df)
    today_df = today_df.drop_duplicates(subset=["ticker"], keep="last").reset_index(drop=True)
    if len(today_df) < n_before:
        print(f"[WARN] Removed {n_before - len(today_df)} duplicate ticker rows for {td.date()}")

    tickers = today_df["ticker"].tolist()
    # Staleness check: warn if data is 1+ calendar days old
    from datetime import date as _date
    days_old = (_date.today() - td.date()).days
    if days_old >= 1:
        print(f"[WARN] Data is STALE: latest row is {td.date()} ({days_old} days ago). "
              f"thai_entry_screen.py may have failed to fetch new data.")
    print(f"[screen] Date: {td.date()} | Tickers: {len(tickers)} | Days old: {days_old}")

    # RS map — use rs_pct_3m from CSV; if all NaN, fall back to rs_score column or 0
    rs_col = None
    for candidate in ("rs_pct_3m", "rs_score", "rs_pct"):
        if candidate in today_df.columns and today_df[candidate].notna().any():
            rs_col = candidate
            break
    rs_map = {}
    if rs_col:
        for _, row in today_df.iterrows():
            val = float(row.get(rs_col) or 0)
            rs_map[row["ticker"]] = val
        print(f"[rs] Using column '{rs_col}' for RS ranking")
    else:
        # Compute RS from thai_ohlcv.db (3M return percentile rank)
        computed = _compute_rs_from_ohlcv(tickers, str(td.date()))
        if computed:
            rs_map = {t: computed.get(t, 0.0) for t in tickers}
            print(f"[rs] Computed RS from ohlcv for {len(computed)}/{len(tickers)} tickers")
        else:
            rs_map = {t: 0.0 for t in tickers}
            print("[WARN] No RS data available — RS will show as 0 in focus list")

    # Build signal matrix for quality signals only
    sig_labels  = []
    sig_h3_list = []
    sig_cols    = []
    missing     = 0

    for sig in quality_sigs:
        col = sig["label"]
        if col not in today_df.columns:
            missing += 1
            continue
        col_data = today_df[col]
        vals = col_data.fillna(False).astype(bool).values
        sig_labels.append(col)
        sig_h3_list.append(float(sig["h3"]))  # already in % (0-100)
        sig_cols.append(vals)

    total_quality = len(quality_sigs)
    schema_drift_pct = round(missing / total_quality * 100, 1) if total_quality > 0 else 0.0
    if missing:
        print(f"[WARN] {missing}/{total_quality} library signals not in CSV columns "
              f"(schema drift {schema_drift_pct}%)")
        if schema_drift_pct > 20:
            print(f"[WARN CRITICAL] Schema drift {schema_drift_pct}% > 20% — "
                  f"quality signal coverage severely degraded. Re-run thai_entry_screen.py --full")

    # Universe coverage check
    from datetime import date as _date
    expected_universe = 183  # SET200-quality universe (update when universe changes)
    coverage_pct = round(len(tickers) / expected_universe * 100, 1)
    if coverage_pct < 70:
        print(f"[WARN CRITICAL] Universe coverage {coverage_pct}% ({len(tickers)}/{expected_universe}) "
              f"< 70% — screener is running on a near-empty universe")

    if not sig_cols:
        print("[FAIL] No signals matched CSV columns")
        return None

    signal_matrix = np.column_stack(sig_cols)   # (N_tickers, N_quality_sigs)
    sig_h3_arr    = np.array(sig_h3_list)

    # Family denominator: distinct families among quality signals
    sig_families_all = [_col_family(lbl) for lbl in sig_labels]
    n_quality_families = len(set(sig_families_all))
    print(f"[matrix] {len(sig_labels)} quality signals | {n_quality_families} families | {len(tickers)} tickers")

    return {
        "signal_matrix":       signal_matrix,
        "tickers":             tickers,
        "sig_h3_arr":          sig_h3_arr,
        "sig_labels":          sig_labels,
        "n_quality_families":  n_quality_families,
        "rs_map":              rs_map,
        "price_date":          td.date(),
        "schema_drift_pct":    schema_drift_pct,
        "universe_coverage_pct": coverage_pct,
    }


def _col_family(col: str) -> str:
    """Extract family from Q-column name (mirrors build_q_library_th.py)."""
    parts = col.split("_")
    if len(parts) < 2:
        return "other"
    if parts[0].startswith("Q") and parts[0][1:].isdigit():
        return parts[1]
    if len(parts) >= 2 and parts[1] in ("CMB", "CMB2"):
        # Q_CMB_Q670_j3_... → parts[2]=Q670, parts[3]=j3 (family)
        return parts[3] if len(parts) >= 4 else "cmb"
    return parts[1] if len(parts) > 1 else "other"


# ── CANONICAL PULSE SCORE FORMULA (identical to PULSE-US) ──────────────────
def calc_pulse_scores(
    signal_matrix: np.ndarray,
    tickers: list,
    sig_h3_arr: np.ndarray,
    sig_labels: list,
    n_quality_families: int,
) -> dict:
    """
    CANONICAL formula — identical in PULSE-TH and PULSE-US:
      breadth     = distinct quality FAMILIES that fired ≥1 signal per ticker
      breadth_pct = breadth / n_quality_families * 100
      avg_h3      = avg h3 (%) of top-3 individual quality signals that fired

    sig_h3_arr must be in % (0–100), NOT fraction.
    """
    sig_families = [_col_family(lbl) for lbl in sig_labels]

    scores = {}
    for i, ticker in enumerate(tickers):
        fired_mask = signal_matrix[i].astype(bool)

        # Breadth: distinct families with ≥1 quality signal fired
        fired_families = set(sig_families[j] for j, f in enumerate(fired_mask) if f)
        breadth     = len(fired_families)
        breadth_pct = round(breadth / n_quality_families * 100, 1) if n_quality_families > 0 else 0.0

        # avg_h3: top-3 quality signals that fired, by h3 descending
        top_signals = []
        if fired_mask.any():
            fired_indices = [j for j, f in enumerate(fired_mask) if f]
            fired_sorted = sorted(fired_indices, key=lambda j: -sig_h3_arr[j])[:3]
            top3_h3 = [sig_h3_arr[j] for j in fired_sorted]
            top_signals = [sig_labels[j] for j in fired_sorted]
            avg_h3 = round(sum(top3_h3) / len(top3_h3), 1)
        else:
            avg_h3 = 0.0

        scores[ticker] = {
            "breadth":         breadth,
            "breadth_pct":     breadth_pct,
            "avg_h3":          avg_h3,           # % (0–100)
            "n_signals_fired": int(fired_mask.sum()),
            "top_signals":     top_signals,
        }
    return scores


# ── Run screen ──────────────────────────────────────────────────────────────
def run_screen(quality_sigs: list[dict], target_date: str | None = None) -> dict | None:
    data = load_signal_matrix(quality_sigs, target_date)
    if data is None:
        return None

    pulse_scores = calc_pulse_scores(
        data["signal_matrix"], data["tickers"],
        data["sig_h3_arr"], data["sig_labels"], data["n_quality_families"]
    )

    # Rank: h3 primary, breadth tiebreaker (mirror PULSE-US)
    ranked = sorted(
        pulse_scores.items(),
        key=lambda x: (x[1]["avg_h3"] * 1000 + x[1]["breadth_pct"]),
        reverse=True
    )
    ticker_hits = {t: s for t, s in ranked if s["avg_h3"] > 0}

    print(f"\nN_QUALITY={len(data['sig_labels'])} | Tickers hit: {len(ticker_hits)}")
    for tkr, sc in list(ticker_hits.items())[:5]:
        rs = data["rs_map"].get(tkr, 0)
        print(f"  {tkr}: {sc['breadth']}/{data['n_quality_families']} ({sc['breadth_pct']:.1f}%)  avg_h3={sc['avg_h3']:.1f}%  RS={rs:.0f}")

    return {
        "date":                   str(data["price_date"]),
        "n_quality":              data["n_quality_families"],
        "n_quality_signals":      len(data["sig_labels"]),
        "ticker_hits":            ticker_hits,
        "pulse_scores":           pulse_scores,
        "rs_map":                 data["rs_map"],
        "schema_drift_pct":       data["schema_drift_pct"],
        "universe_coverage_pct":  data["universe_coverage_pct"],
    }


# ── Telegram ────────────────────────────────────────────────────────────────
def _icon(h3: float) -> str:
    if h3 >= 80:  return "🔴"
    if h3 >= 75:  return "🟠"
    return "🟡"


def format_telegram(result: dict, run_date: datetime) -> list[str]:
    """Return list of messages matching PULSE-US format with [TH] prefix."""
    n_q    = result["n_quality_signals"]
    n_fam  = result["n_quality"]
    hits   = result["ticker_hits"]
    scores = result["pulse_scores"]
    rs_map = result["rs_map"]
    date_str = run_date.strftime("%d %b %Y")

    # MSG 1 — Header
    msg1_lines = [
        f"<b>[TH] PULSE-TH Daily | {date_str}</b>",
        f"Quality signals: {n_q} | Families: {n_fam} | Tickers hit: {len(hits)}",
        "",
        "📊 <b>Focus List</b> (RS-ranked, PULSE overlay)",
        f"<i>hr = avg h3 of top-3 quality signals (fwd5 hit rate)</i>",
        f"<i>breadth = % of families fired / {n_fam} quality families</i>",
        "",
    ]

    # Top 15 by RS, with PULSE overlay
    rs_sorted = sorted(rs_map.items(), key=lambda x: x[1], reverse=True)[:15]
    for tkr, rs in rs_sorted:
        sc = scores.get(tkr, {"avg_h3": 0.0, "breadth_pct": 0.0, "breadth": 0})
        h3 = sc["avg_h3"]
        bp = sc["breadth_pct"]
        icon = _icon(h3) if h3 > 0 else "⚪"
        if h3 > 0:
            msg1_lines.append(f"{icon} <b>{tkr}</b>  RS={rs:.0f}%  hr={h3:.1f}%  br={bp:.1f}%")
        else:
            msg1_lines.append(f"⚪ {tkr}  RS={rs:.0f}%")

    msg1 = "\n".join(msg1_lines)

    # MSG 2 — Top 5 by PULSE score
    top5 = list(hits.items())[:5]
    msg2_lines = [
        f"<b>[TH] PULSE-TH Top 5 | {date_str}</b>",
        "<i>rank: HR primary, Breadth tiebreaker — independent of RS</i>",
        "",
    ]
    for rank, (tkr, sc) in enumerate(top5, 1):
        rs  = rs_map.get(tkr, 0)
        h3  = sc["avg_h3"]
        bp  = sc["breadth_pct"]
        n   = sc["breadth"]
        icon = _icon(h3)
        msg2_lines.append(
            f"{rank}. {icon} <b>{tkr}</b>\n"
            f"   hr={h3:.1f}%  breadth={bp:.1f}% ({n}/{n_fam} fam)  RS={rs:.0f}%"
        )

    if not top5:
        msg2_lines.append("No tickers fired any quality signal today.")

    msg2 = "\n".join(msg2_lines)
    return [msg1, msg2]


def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        # Try .env fallback
        env_path = ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                if line.startswith("TELEGRAM_CHAT_ID="):
                    chat = line.split("=", 1)[1].strip()
    if not token or not chat:
        print("[TG-SKIP] No credentials")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for parse_mode in ("HTML", None):
        payload = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            r = requests.post(url, json=payload, timeout=10, verify=False)
            if r.status_code == 200:
                return True
            if r.status_code == 400 and parse_mode:
                continue   # retry without parse_mode
            print(f"[TG-FAIL] {r.status_code}: {r.text[:100]}")
            return False
        except Exception as e:
            print(f"[TG-ERR] {e}")
            return False
    return False


# ── Entry points ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date",         default=None, help="YYYY-MM-DD (default: latest)")
    ap.add_argument("--no-telegram",  action="store_true")
    args = ap.parse_args()

    run_date = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()
    print(f"PULSE-TH Daily — {run_date.date()}")
    print("=" * 60)

    quality_sigs = load_quality_signals()
    result = run_screen(quality_sigs, args.date)
    if result is None:
        print("[FAIL] Screen returned no result")
        sys.exit(1)

    # Save output JSON (same pattern as PULSE-US)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        # pulse_scores values may have numpy types — convert
        out = dict(result)
        out["ticker_hits"]  = {k: {kk: float(vv) if hasattr(vv, 'item') else vv
                                    for kk, vv in v.items()}
                                for k, v in result["ticker_hits"].items()}
        out["pulse_scores"] = {k: {kk: float(vv) if hasattr(vv, 'item') else vv
                                    for kk, vv in v.items()}
                                for k, v in result["pulse_scores"].items()}
        json.dump(out, f, indent=2, default=str)
    print(f"\nSaved → {OUT_PATH}")

    # Upsert to shared pulse_signals.db
    date_str = result["date"]
    upsert_rows = []
    for ticker, sc in result["pulse_scores"].items():
        rs = result["rs_map"].get(ticker, 0) or 0
        upsert_rows.append({
            "date":            date_str,
            "system":          "TH",
            "ticker":          ticker,
            "breadth":         int(sc.get("breadth", 0)),
            "breadth_pct":     float(sc.get("breadth_pct", 0.0)),
            "avg_h3":          float(sc.get("avg_h3", 0.0)),
            "n_signals_fired": int(sc.get("n_signals_fired", 0)),
            "rs_pct":          float(rs),
            "top_signals":     sc.get("top_signals", []),
        })
    if upsert_rows:
        n = upsert_pulse_signals(upsert_rows)
        print(f"[pulse_db] Upserted {n} TH rows for {date_str}")

    # Write shortlist_today.json (TH portion — US will add its rows separately)
    meta = {
        "schema_drift_pct_th":       result.get("schema_drift_pct", 0),
        "universe_coverage_pct_th":  result.get("universe_coverage_pct", 0),
        "n_quality_signals_th":      result.get("n_quality_signals", 0),
        "n_quality_families_th":     result.get("n_quality", 0),
    }
    write_shortlist_json(today=date_str, meta=meta)

    if not args.no_telegram:
        msgs = format_telegram(result, run_date)
        for i, msg in enumerate(msgs):
            ok = send_telegram(msg)
            print(f"[Telegram] msg {i+1}: {'OK' if ok else 'FAIL'}")


def run():
    """Entry point for pre_market_runner pipeline."""
    main()


if __name__ == "__main__":
    main()
