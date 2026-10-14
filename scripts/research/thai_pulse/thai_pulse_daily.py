"""
thai_pulse_daily.py — PULSE-TH Daily Screener
==============================================
Standard architecture identical to pulse_us_daily.py:
  1. Load quality signals from q_library_th.json (h5>=63%, pre-computed)
  2. Read today's row per ticker from thai_entry_screen_results.csv
  3. Build signal matrix (N_tickers × N_quality_signals)
  4. calc_pulse_scores() — IDENTICAL function to PULSE-US
  5. Rank by h5 (primary) + breadth (tiebreaker)
  6. Send Telegram: [TH] header + focus list + top 5

calc_pulse_scores() is the canonical shared formula:
  breadth     = distinct quality FAMILIES that fired ≥1 signal
  breadth_pct = breadth / n_quality_families * 100
  avg_h5      = avg h5 of top-3 individual quality signals that fired (%)

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
INDEX_PATH = ROOT / "data" / "research" / "thai_pulse" / "th_index_membership.json"

sys.path.insert(0, str(ROOT / "scripts" / "research"))
from entry_screen_db import read_entry_screen, get_max_date as _db_max_date
from pulse_db import upsert_pulse_signals, write_shortlist_json
from constants import RS_LOOKBACK_DAYS, RS_MIN_PCT  # single source of truth for PULSE-TH RS

_OHLCV_DB      = ROOT / "data" / "research" / "thai_ohlcv.db"
_SIGNALS_DB    = ROOT / "data" / "research" / "pulse_signals.db"

# RS floor — canonical value from constants.py (shared with thai_entry_screen.py)
# Applied to: (1) index-fill tickers, (2) ALL CSV tickers after RS map is computed
_RS_FILL_MIN = RS_MIN_PCT  # 60.0 — do NOT hardcode here


def _load_rs_from_signals_db(tickers: list, as_of_date: str) -> dict:
    """Load RS percentile from pulse_signals.db (system='TH', most recent date <= as_of_date).
    Returns {ticker: rs_pct} or empty dict if DB unavailable.
    """
    if not _SIGNALS_DB.exists():
        return {}
    try:
        conn = sqlite3.connect(str(_SIGNALS_DB))
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, rs_pct FROM pulse_signals "
            f"WHERE system='TH' AND ticker IN ({placeholders}) "
            f"AND date = ? AND rs_pct > 0",
            tickers + [as_of_date],
        ).fetchall()
        conn.close()
        # Keep most recent rs_pct per ticker
        result = {}
        for tkr, rs in rows:
            if tkr not in result:
                result[tkr] = float(rs)
        return result
    except Exception as e:
        print(f"[rs_signals_db] Failed to load RS: {e}")
        return {}


def _compute_rs_from_ohlcv(tickers: list, as_of_date: str) -> dict:
    """Compute RS percentile (3M return rank) from thai_ohlcv.db.
    Ranks against FULL universe in thai_ohlcv, not just the screen subset.
    Returns {ticker: rs_percentile_0_to_100}.
    """
    if not _OHLCV_DB.exists():
        return {}
    try:
        conn = sqlite3.connect(str(_OHLCV_DB))
        # Query FULL universe (no ticker filter) to get market-wide RS percentile
        rows = conn.execute(
            "SELECT ticker, date, close FROM thai_ohlcv "
            "WHERE date <= ? AND close > 0 ORDER BY ticker, date",
            (as_of_date,),
        ).fetchall()
        conn.close()
        if not rows:
            return {}
        df = pd.DataFrame(rows, columns=["ticker", "date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        # 3M return per ticker across full universe
        returns = {}
        for tkr, grp in df.groupby("ticker"):
            grp = grp.sort_values("date")
            if len(grp) < 10:
                continue
            latest = grp["close"].iloc[-1]
            lookback = grp.iloc[max(0, len(grp) - RS_LOOKBACK_DAYS)]["close"]
            if lookback > 0:
                returns[tkr] = (latest / lookback - 1) * 100
        if not returns:
            return {}
        # Percentile rank across full universe, return only requested tickers
        import scipy.stats as _stats
        vals = list(returns.values())
        all_tickers = list(returns.keys())
        ranks = _stats.rankdata(vals)
        n = len(vals)
        universe_rs = {t: round(float(r / n * 100), 1) for t, r in zip(all_tickers, ranks)}
        print(f"[rs_compute] Ranked {n} tickers from full universe")
        return {t: universe_rs[t] for t in tickers if t in universe_rs}
    except Exception as e:
        print(f"[rs_compute] Failed: {e}")
        return {}



def _load_prices_from_ohlcv(tickers: list, as_of_date: str) -> dict:
    """Load latest close price from thai_ohlcv.db for each ticker.
    Returns {ticker: close_price} — only populated tickers returned.
    """
    if not _OHLCV_DB.exists():
        return {}
    try:
        conn = sqlite3.connect(str(_OHLCV_DB))
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, close FROM thai_ohlcv "
            f"WHERE date = (SELECT MAX(date) FROM thai_ohlcv WHERE date <= ?) "
            f"AND ticker IN ({placeholders}) AND close > 0",
            [as_of_date] + tickers,
        ).fetchall()
        conn.close()
        return {t: float(c) for t, c in rows}
    except Exception as e:
        print(f"[prices] Failed: {e}")
        return {}

# ── Load signal library ─────────────────────────────────────────────────────
# Module-level label_th lookup: {signal_label: label_th} for h5>=85% signals
# Built once at startup from q_library_th.json
_LABEL_TH_MAP: dict[str, str] = {}


def _build_label_th_map(results: list) -> None:
    """Populate _LABEL_TH_MAP from library results (call once at startup)."""
    # Use .clear()/.update() to mutate in-place — preserves imported references.
    _LABEL_TH_MAP.clear()
    _LABEL_TH_MAP.update({
        r["label"]: r["label_th"]
        for r in results
        if r.get("label_th") and r.get("h5", r.get("h3", 0)) >= 80.0
    })


def load_quality_signals() -> list[dict]:
    """Load quality signals (h5>=63%, grade A/S/S_BOTH) from q_library_th.json."""
    with open(LIB_PATH, encoding="utf-8") as f:
        lib = json.load(f)
    quality = [r for r in lib["results"] if r["grade"] in ("S_BOTH", "S", "A")]
    _build_label_th_map(lib["results"])
    n_labelled = sum(1 for r in lib["results"] if r.get("label_th"))
    print(f"[library] {lib['total_scored']} total | {len(quality)} quality signals loaded | {n_labelled} with label_th")
    return quality


# ── Load today's signal matrix from CSV ────────────────────────────────────
def load_signal_matrix(quality_sigs: list[dict], target_date: str | None = None) -> dict | None:
    """
    Read thai_entry_screen_results.csv, pick today's rows, build signal matrix.
    Returns dict with: signal_matrix, tickers, sig_h5_arr, sig_labels,
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
    # Staleness check: abort if data is 1+ calendar days old to prevent sending wrong Telegram
    from datetime import date as _date
    days_old = (_date.today() - td.date()).days
    print(f"[screen] Date: {td.date()} | Tickers: {len(tickers)} | Days old: {days_old}")
    if days_old >= 1:
        # Root cause: thai_entry_screen.py has not written today's data yet.
        # Sending Telegram from stale data (version เก่า) is worse than not sending.
        # Fail loudly so the runner marks this step FAIL instead of sending wrong data.
        raise RuntimeError(
            f"[ABORT] Signal data is STALE ({days_old} days old — latest row {td.date()}). "
            f"thai_entry_screen.py must complete before thai_pulse_daily.py can run. "
            f"Will not send Telegram with stale data."
        )

    # RS map — priority: (1) ohlcv full-universe 63-day (authoritative), (2) pulse_signals.db, (3) 0
    # ALWAYS compute from ohlcv first: this is the same computation used by thai_entry_screen.py
    # RS filter and RS display must use identical source — pulse_signals.db values can be stale
    # (written from CSV 21-day RS in older runs, causing display/filter mismatch like SPCG RS=53%)
    rs_map = _compute_rs_from_ohlcv(tickers, str(td.date()))
    if rs_map and any(v > 0 for v in rs_map.values()):
        n_filled = sum(1 for v in rs_map.values() if v > 0)
        print(f"[rs] Computed RS from ohlcv full-universe for {n_filled}/{len(tickers)} tickers")
        # Fill any remaining tickers from pulse_signals.db (fallback)
        missing_rs = [t for t in tickers if t not in rs_map or rs_map.get(t, 0) == 0]
        if missing_rs:
            db_rs = _load_rs_from_signals_db(missing_rs, str(td.date()))
            for t in missing_rs:
                rs_map[t] = db_rs.get(t, 0.0)
                if db_rs.get(t, 0) > 0:
                    print(f"[rs] Filled {t} RS from pulse_signals.db: {db_rs[t]:.1f}%")
                else:
                    rs_map[t] = 0.0
    else:
        # Fallback: pulse_signals.db (ohlcv unavailable)
        db_rs = _load_rs_from_signals_db(tickers, str(td.date()))
        if db_rs and any(v > 0 for v in db_rs.values()):
            rs_map = {t: db_rs.get(t, 0.0) for t in tickers}
            print(f"[rs] Fallback: loaded RS from pulse_signals.db for {sum(1 for v in db_rs.values() if v > 0)}/{len(tickers)} tickers")
        else:
            rs_map = {t: 0.0 for t in tickers}
            print("[WARN] No RS data available — RS will show as 0 in focus list")

    # ── Secondary RS floor — defend against stale CSV ────────────────────────────
    # thai_entry_screen.py may have written the CSV with an old RS window (e.g. 21d)
    # before constants.py was deployed. Apply the same RS_MIN_PCT floor here so that
    # any ticker that does not meet the 63d RS threshold is dropped regardless of what
    # the CSV says. rs_map is already populated from ohlcv (authoritative 63d) above.
    before_rs_floor = len(tickers)
    tickers = [t for t in tickers if rs_map.get(t, 0) >= RS_MIN_PCT]
    today_df = today_df[today_df["ticker"].isin(tickers)].reset_index(drop=True)
    dropped_rs = before_rs_floor - len(tickers)
    if dropped_rs > 0:
        print(f"[rs_floor] Dropped {dropped_rs} tickers with RS-63d < {RS_MIN_PCT}% "
              f"(stale CSV guard — {before_rs_floor} before → {len(tickers)} after)")

    # Build signal matrix for quality signals only
    sig_labels  = []
    sig_h5_list = []
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
        sig_h5_list.append(float(sig.get("h5", sig["h3"])))  # use h5, fallback to h3
        sig_cols.append(vals)

    total_quality = len(quality_sigs)
    schema_drift_pct = round(missing / total_quality * 100, 1) if total_quality > 0 else 0.0
    if missing:
        print(f"[WARN] {missing}/{total_quality} library signals not in CSV columns "
              f"(schema drift {schema_drift_pct}%)")
        if schema_drift_pct > 20:
            print(f"[WARN CRITICAL] Schema drift {schema_drift_pct}% > 20% — "
                  f"quality signal coverage severely degraded. Re-run thai_entry_screen.py --full")

    # Universe coverage check — compare against full day count in DB for this date
    from datetime import date as _date
    import sqlite3 as _sqlite3
    _conn_uc = _sqlite3.connect(str(_OHLCV_DB))
    full_day_count = _conn_uc.execute(
        "SELECT COUNT(*) FROM entry_screen_signals WHERE date = ?", (td_str,)
    ).fetchone()[0]
    _conn_uc.close()
    # tickers = top-RS subset of full_day_count (15 of ~15-183) — that's by design
    coverage_pct = round(len(tickers) / max(full_day_count, 1) * 100, 1)
    if full_day_count < 10:
        print(f"[WARN CRITICAL] Universe coverage {coverage_pct}% ({len(tickers)}/{full_day_count}) "
              f"< 10 tickers in DB — thai_entry_screen.py may have failed")

    if not sig_cols:
        print("[FAIL] No signals matched CSV columns")
        return None

    signal_matrix = np.column_stack(sig_cols)   # (N_tickers, N_quality_sigs)
    sig_h5_arr    = np.array(sig_h5_list)

    # Family denominator: distinct families among quality signals
    sig_families_all = [_col_family(lbl) for lbl in sig_labels]
    n_quality_families = len(set(sig_families_all))
    print(f"[matrix] {len(sig_labels)} quality signals | {n_quality_families} families | {len(tickers)} tickers")

    # Capture base (non-quality) signals per ticker for Telegram display
    quality_label_set = set(sig_labels)
    _meta_c = {"date", "ticker", "regime"}
    _all_sc = [c for c in today_df.columns if c not in _meta_c]
    _bpool = [c for c in _all_sc if c not in quality_label_set]
    base_fired: dict = {}
    for _bi2, _btkr2 in enumerate(tickers):
        _br2 = today_df.iloc[_bi2]
        base_fired[_btkr2] = [c for c in _bpool if _br2.get(c, False)][:10]

    # Load prices from ohlcv
    price_map = _load_prices_from_ohlcv(tickers, str(td.date()))
    print(f"[prices] Loaded prices for {len(price_map)}/{len(tickers)} tickers")

    return {
        "signal_matrix":       signal_matrix,
        "tickers":             tickers,
        "sig_h5_arr":          sig_h5_arr,
        "sig_labels":          sig_labels,
        "n_quality_families":  n_quality_families,
        "rs_map":              rs_map,
        "price_map":           price_map,
        "price_date":          td.date(),
        "schema_drift_pct":    schema_drift_pct,
        "universe_coverage_pct": coverage_pct,
        "base_fired":          base_fired,
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
    sig_h5_arr: np.ndarray,
    sig_labels: list,
    n_quality_families: int,
) -> dict:
    """
    CANONICAL formula — identical in PULSE-TH and PULSE-US:
      breadth     = distinct quality FAMILIES that fired ≥1 signal per ticker
      breadth_pct = breadth / n_quality_families * 100
      avg_h5      = avg h5 (%) of top-3 individual quality signals that fired

    sig_h5_arr must be in % (0–100), NOT fraction.
    """
    sig_families = [_col_family(lbl) for lbl in sig_labels]

    scores = {}
    for i, ticker in enumerate(tickers):
        fired_mask = signal_matrix[i].astype(bool)

        # Breadth: distinct families with ≥1 quality signal fired
        fired_families = set(sig_families[j] for j, f in enumerate(fired_mask) if f)
        breadth     = len(fired_families)
        breadth_pct = round(breadth / n_quality_families * 100, 1) if n_quality_families > 0 else 0.0

        # avg_h5: top-3 quality signals that fired, by h5 descending
        top_signals = []
        if fired_mask.any():
            fired_indices = [j for j, f in enumerate(fired_mask) if f]
            fired_sorted = sorted(fired_indices, key=lambda j: -sig_h5_arr[j])[:3]
            top3_h5 = [sig_h5_arr[j] for j in fired_sorted]
            top_signals = [sig_labels[j] for j in fired_sorted]
            avg_h5 = round(sum(top3_h5) / len(top3_h5), 1)
        else:
            avg_h5 = 0.0

        scores[ticker] = {
            "breadth":         breadth,
            "breadth_pct":     breadth_pct,
            "avg_h5":          avg_h5,           # % (0–100)
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
        data["sig_h5_arr"], data["sig_labels"], data["n_quality_families"]
    )

    # Rank: h5 primary, breadth tiebreaker (mirror PULSE-US)
    ranked = sorted(
        pulse_scores.items(),
        key=lambda x: (x[1]["avg_h5"] * 1000 + x[1]["breadth_pct"]),
        reverse=True
    )
    ticker_hits = {t: s for t, s in ranked if s["avg_h5"] > 0}

    print(f"\nN_QUALITY={len(data['sig_labels'])} | Tickers hit: {len(ticker_hits)}")
    for tkr, sc in list(ticker_hits.items())[:5]:
        rs = data["rs_map"].get(tkr, 0)
        print(f"  {tkr}: {sc['breadth']}/{data['n_quality_families']} ({sc['breadth_pct']:.1f}%)  avg_h5={sc['avg_h5']:.1f}%  RS={rs:.0f}")

    return {
        "date":                   str(data["price_date"]),
        "n_quality":              data["n_quality_families"],
        "n_quality_signals":      len(data["sig_labels"]),
        "ticker_hits":            ticker_hits,
        "pulse_scores":           pulse_scores,
        "rs_map":                 data["rs_map"],
        "price_map":              data.get("price_map", {}),
        "schema_drift_pct":       data["schema_drift_pct"],
        "universe_coverage_pct":  data["universe_coverage_pct"],
        "base_fired":             data.get("base_fired", {}),
    }


# ── Telegram ────────────────────────────────────────────────────────────────
def _icon(h5: float) -> str:
    if h5 >= 80:  return "🔴"
    if h5 >= 75:  return "🟠"
    return "🟡"


_SIGNAL_LABELS: dict[str, str] = {
    "o2": "CHOCH", "o3": "CHOCH+Dry", "choch": "CHOCH",
    "o1": "FVG", "fvg": "FVG",
    "f1": "VolDry", "f2": "DryAfterSurge", "f4": "DryNearHi",
    "x2": "MildPBDry", "x3": "ShallowPBSurge", "x6": "DryNearHi@MA50",
    "h1": "MomResume",
    "k1": "1stPB",
    "g2": "NR7",
    "i4": "Bounce", "i10": "TopDecile",
    "b3": "NearHi20", "b1": "Tight",
    "j8": "PB@MA50", "j3": "PB@MA20",
    "e3": "MildPB", "e4": "ShallowPB",
    "c1": "EarlyMove",
    "d3": "NearHi@MA50",
}

def _signal_label(q_code: str) -> str:
    """Convert Q-code like 'Q102_i6_i7_d5_j1' or 'O2_choch_10d' to readable label."""
    parts = q_code.lower().replace("q_cmb_", "").split("_")
    seen, labels, raw_components = set(), [], []
    for p in parts:
        # skip pure numeric parts and q-number prefix (q102 → skip, j8 → keep)
        if p.isdigit():
            continue
        key = p.lstrip("0123456789") if p[:1] in "qQ" else p
        if not key or key.isdigit():
            continue
        name = _SIGNAL_LABELS.get(key) or _SIGNAL_LABELS.get(p)
        if name and name not in seen:
            seen.add(name)
            labels.append(name)
        elif len(p) <= 4 and p not in ("cmb", "cmb3", "cmb4", "near", "zone", "hi"):
            raw_components.append(p.upper())
    if labels:
        return "+".join(labels[:3])
    # fallback: show up to 3 raw component codes
    return "+".join(raw_components[:3]) if raw_components else q_code.split("_")[0]


def _load_index_membership() -> dict:
    """Load SET index membership from th_index_membership.json.

    Returns dict mapping index_name -> set of tickers already deduplicated
    (each ticker assigned to the highest-tier index only).
    Priority: SET50 > SET100 > SETHD > sSET.
    Falls back to empty dict (flat-list mode) if file missing or malformed.

    Staleness warning: if _meta.last_fetched > 40 days old, logs WARNING.
    Run update_index_membership.py to refresh.
    """
    from datetime import date as _date
    priority = ["SET50", "SET100", "SETHD", "sSET"]
    if not INDEX_PATH.exists():
        print(f"[index] th_index_membership.json not found — using flat list")
        return {}
    try:
        raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        # Staleness check
        meta = raw.get("_meta", {})
        last_fetched_str = meta.get("last_fetched")
        if last_fetched_str:
            try:
                last_fetched = _date.fromisoformat(last_fetched_str)
                age_days = (_date.today() - last_fetched).days
                if age_days > 40:
                    print(f"[index] WARNING: th_index_membership.json is {age_days} days old "
                          f"(last_fetched={last_fetched_str}). Run update_index_membership.py.")
                else:
                    print(f"[index] membership last_fetched={last_fetched_str} ({age_days}d ago) OK")
            except Exception:
                pass
        # No cross-section dedup: a ticker in SET50+SETHD appears in BOTH sections
        membership: dict = {}
        for idx in priority:
            membership[idx] = set(raw.get(idx, []))
        return membership
    except Exception as e:
        print(f"[index] Failed to load membership: {e} — using flat list")
        return {}

def _load_rs_for_fill(tickers: list, as_of_date: str) -> dict:
    """Load RS for index-fill tickers (not in main screen) from ohlcv.
    Used to fill index sections to minimum 3 tickers.
    Returns {ticker: rs_pct}.
    """
    # Try pulse_signals.db first (faster)
    rs = _load_rs_from_signals_db(tickers, as_of_date)
    # Fill any missing from ohlcv compute
    missing = [t for t in tickers if t not in rs or rs.get(t, 0) == 0]
    if missing:
        computed = _compute_rs_from_ohlcv(tickers, as_of_date)
        for t in missing:
            if t in computed:
                rs[t] = computed[t]
    # Ensure all tickers have an entry
    for t in tickers:
        rs.setdefault(t, 0.0)
    return rs


def _load_prices_for_fill(tickers: list, as_of_date: str) -> dict:
    """Load latest close prices for fill tickers from thai_ohlcv.db."""
    return _load_prices_from_ohlcv(tickers, as_of_date)


def format_telegram(result: dict, run_date: datetime) -> list[str]:
    """Return list of messages with [TH] prefix. Focus List grouped by SET index."""
    n_q        = result["n_quality_signals"]
    n_fam      = result["n_quality"]
    hits       = result["ticker_hits"]
    scores     = result["pulse_scores"]
    rs_map     = result["rs_map"]
    base_fired = result.get("base_fired", {})
    price_map  = result.get("price_map", {})
    date_str   = run_date.strftime("%d %b %Y")

    _PRIORITY_ORDER = ["SET50", "SET100", "SETHD", "sSET"]

    # Load SET index membership — falls back to {} = flat list
    membership = _load_index_membership()

    # Build multi-index map: each ticker -> set of all indices it belongs to
    # A ticker can appear in multiple sections (CIO: cross-section duplicates allowed)
    ticker_indices: dict[str, set] = {}
    for idx in _PRIORITY_ORDER:
        for tkr in membership.get(idx, set()):
            ticker_indices.setdefault(tkr, set()).add(idx)
    for tkr in rs_map:
        if tkr not in ticker_indices:
            ticker_indices[tkr] = {"Other"}
    # Keep backward-compatible single-index for Other grouping
    ticker_index: dict = {}
    for tkr, idxs in ticker_indices.items():
        # primary = highest tier
        for pri in _PRIORITY_ORDER:
            if pri in idxs:
                ticker_index[tkr] = pri
                break
        else:
            ticker_index[tkr] = "Other"

    # All tickers sorted by RS desc
    rs_sorted_all = sorted(rs_map.items(), key=lambda x: x[1], reverse=True)

    def _fmt_line(tkr: str, rs: float, index_tags: set = None) -> str:
        """Return 2-line ticker block. Line1: emoji+ticker+[tags]. Line2: price+RS+h5+label."""
        sc = scores.get(tkr, {"avg_h5": 0.0, "top_signals": []})
        h5 = sc.get("avg_h5", 0.0)
        top_sigs = sc.get("top_signals", [])
        icon = _icon(h5) if h5 > 0 else "\u26aa"
        # Build index tag string: join all indices with middle dot
        if index_tags:
            tag_str = "\u00b7".join(i for i in _PRIORITY_ORDER if i in index_tags)
            tag = f" [{tag_str}]" if tag_str else ""
        else:
            tag = ""
        price = price_map.get(tkr)
        price_str = f"\u0e3f{price:.2f}" if price and price > 0 else ""
        # label_th only when h5 >= 80%
        if h5 >= 80.0:
            desc = next((_LABEL_TH_MAP[s] for s in top_sigs if s in _LABEL_TH_MAP), "")
            sig_str = f"  \u2192 {desc}" if desc else ""
        else:
            sig_str = ""
        h5_str = f"{h5:.1f}" if h5 > 0 else "0"
        line1 = f"{icon} <b>{tkr}</b>{tag}"
        line2 = f"   {price_str}  RS={rs:.0f}%  h5={h5_str}%{sig_str}"
        return f"{line1}\n{line2}"

    # MSG 1 \u2014 Header + grouped Focus List
    msg1_lines = [
        f"<b>[TH] PULSE-TH Daily | {date_str}</b>",
        f"Quality signals: {n_q} | Families: {n_fam} | Tickers hit: {len(hits)}",
        "",
        "\U0001f4ca <b>Focus List</b> (grouped by SET index, RS-ranked, deduped)",
        "<i>h5 = avg 5-day hit rate | \u00b7 = multi-index member</i>",
    ]

    if membership:
        shown: set = set()
        # Pre-load RS+prices for ALL index-membership tickers (for fill)
        all_member_tickers = list({t for tickers in membership.values() for t in tickers})
        fill_rs    = _load_rs_for_fill(all_member_tickers, str(result["date"]))
        fill_price = _load_prices_for_fill(all_member_tickers, str(result["date"]))
        # Merge fill_rs and fill_price into the main maps for _fmt_line access
        for t, v in fill_rs.items():
            rs_map.setdefault(t, v)
        for t, v in fill_price.items():
            price_map.setdefault(t, v)

        MIN_PER_GROUP = 3

        for idx in _PRIORITY_ORDER:
            # OPTION C: each ticker placed in highest-tier section only (deduped via ticker_index)
            screen_in_idx = [t for t, _ in rs_sorted_all if ticker_index.get(t) == idx]
            # RS-fill: index members not already shown or in screen
            member_candidates = sorted(
                [t for t in membership.get(idx, set())
                 if t not in screen_in_idx and t not in shown
                 and fill_rs.get(t, 0) >= _RS_FILL_MIN],
                key=lambda t: fill_rs.get(t, 0),
                reverse=True,
            )
            display_tickers = list(screen_in_idx[:5])
            n_fill = max(0, MIN_PER_GROUP - len(display_tickers))
            fill_used = set(member_candidates[:n_fill])
            display_tickers += list(fill_used)

            if not display_tickers:
                continue

            msg1_lines.append("")
            msg1_lines.append(f"<b>{idx}</b>")
            for tkr in display_tickers[:5]:
                rs_val = rs_map.get(tkr, fill_rs.get(tkr, 0))
                is_fill = tkr in fill_used
                if is_fill:
                    price = price_map.get(tkr)
                    price_str = f"\u0e3f{price:.2f}" if price and price > 0 else ""
                    # Tag for fill tickers: show all their indices
                    ftags = ticker_indices.get(tkr, set())
                    ftag_str = "\u00b7".join(i for i in _PRIORITY_ORDER if i in ftags)
                    ftag = f" [{ftag_str}]" if ftag_str else ""
                    line1 = f"\u26aa {tkr}{ftag}"
                    line2 = f"   {price_str}  RS={rs_val:.0f}%  h5=0%"
                    msg1_lines.append(f"{line1}\n{line2}")
                else:
                    msg1_lines.append(_fmt_line(tkr, rs_val, ticker_indices.get(tkr, set())))
                msg1_lines.append("")   # blank line between tickers
                shown.add(tkr)

        # Tickers not in any known index (Other)
        other = [t for t, _ in rs_sorted_all if ticker_index.get(t) == "Other"]
        if other:
            msg1_lines.append("")
            msg1_lines.append("<b>Other</b>")
            for tkr in other[:5]:
                msg1_lines.append(_fmt_line(tkr, rs_map[tkr], set()))
                msg1_lines.append("")
    else:
        # Flat fallback
        msg1_lines.append("")
        for tkr, rs in rs_sorted_all[:15]:
            msg1_lines.append(_fmt_line(tkr, rs, set()))
            msg1_lines.append("")

    msg1 = "\n".join(msg1_lines)

    # MSG 2 — Top 5 by PULSE score (with index tag)
    top5 = list(hits.items())[:5]
    msg2_lines = [
        f"<b>[TH] PULSE-TH Top 5 | {date_str}</b>",
        "<i>rank: H5 primary, Breadth tiebreaker</i>",
        "",
    ]
    for rank, (tkr, sc) in enumerate(top5, 1):
        rs       = rs_map.get(tkr, 0)
        h5       = sc["avg_h5"]
        top_sigs = sc.get("top_signals", [])
        icon     = _icon(h5) if h5 > 0 else "⚪"
        # Build multi-index tag string (all indices, deduped, priority order)
        tkr_idxs = ticker_indices.get(tkr, set())
        tag_str  = "·".join(i for i in _PRIORITY_ORDER if i in tkr_idxs)
        idx_str  = f" [{tag_str}]" if tag_str else ""
        price    = price_map.get(tkr)
        price_str = f"฿{price:.2f}" if price and price > 0 else ""
        # label_th only at h5>=80%
        if h5 >= 80.0:
            desc = next((_LABEL_TH_MAP[s] for s in top_sigs if s in _LABEL_TH_MAP), "")
            sig_str = f"  → {desc}" if desc else ""
        else:
            sig_str = ""
        h5_str = f"{h5:.1f}" if h5 > 0 else "0"
        line1 = f"{rank}. {icon} <b>{tkr}</b>{idx_str}"
        line2 = f"   {price_str}  RS={rs:.0f}%  h5={h5_str}%{sig_str}"
        msg2_lines.append(line1 + chr(10) + line2)
        msg2_lines.append("")  # blank line between entries

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
            for line in env_path.read_text(encoding="utf-8").splitlines():
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
    with open(OUT_PATH, "w", encoding="utf-8") as f:
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
        if rs == 0.0:
            continue  # never persist zero RS — contaminates historical record
        upsert_rows.append({
            "date":            date_str,
            "system":          "TH",
            "ticker":          ticker,
            "breadth":         int(sc.get("breadth", 0)),
            "breadth_pct":     float(sc.get("breadth_pct", 0.0)),
            "avg_h3":          float(sc.get("avg_h5", 0.0)),  # h5 value stored in avg_h3 DB column
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
