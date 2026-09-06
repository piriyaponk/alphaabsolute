"""
AlphaAbsolute v2 — System Health Probe
=======================================
Runs 7 diagnostic probes on the pipeline and data outputs.
Issues PASS / WARN / FAIL verdict per probe.
Writes: data/system_health/alerts.json

Run: Daily (post-computation), included in report_writer.py if any FAIL.
Run manually: python scripts/pre_compute/system_health_probe.py

Probes:
  P1  Data Freshness         — all critical JSON outputs updated today
  P2  RS Universe Size       — benchmark universe has expected ticker count
  P3  Fundamentals Coverage  — % of screened tickers have fundamentals data
  P4  Stage 2 Flag Coverage  — stage2_flag populated in ticker_meta
  P5  Regime Score Sanity    — effective_score within expected range for regime
  P6  Foreign Filer Coverage — ARM / TSM / ASML have fundamentals (any source)
  P7  Paper Portfolio        — paper_portfolio_state.json valid and internally consistent
"""

from __future__ import annotations
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR  = Path(__file__).resolve().parents[2]
DATA_DIR  = BASE_DIR / "data"
OUT_DIR   = DATA_DIR / "system_health"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TODAY = date.today().isoformat()
DB_PATH = DATA_DIR / "ohlcv.db"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _probe(name: str, verdict: str, detail: str, metric: Any = None) -> dict:
    """Build a single probe result dict."""
    assert verdict in ("PASS", "WARN", "FAIL")
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[verdict]
    return {
        "probe":   name,
        "verdict": verdict,
        "icon":    icon,
        "detail":  detail,
        "metric":  metric,
    }


def _file_age_days(path: Path) -> float | None:
    """Return file modification age in days. None if file missing."""
    if not path.exists():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return (datetime.now() - mtime).total_seconds() / 86400


# ─────────────────────────────────────────────────────────────────────────────
# Probe 1 — Data Freshness
# ─────────────────────────────────────────────────────────────────────────────

def probe_data_freshness() -> dict:
    """Check that critical pipeline outputs were updated today (or yesterday for weekend)."""
    critical_files = {
        "rs_universe/latest.json":         DATA_DIR / "rs_universe" / "latest.json",
        "trend_template/screener_latest":  DATA_DIR / "trend_template" / "screener_latest.json",
        "regime/market_health.json":       DATA_DIR / "regime" / "market_health.json",
        "regime/macro_state.json":         DATA_DIR / "regime" / "macro_state.json",
    }
    stale, missing = [], []
    for label, path in critical_files.items():
        age = _file_age_days(path)
        if age is None:
            missing.append(label)
        elif age > 1.5:   # >36h: stale (covers weekend gap)
            stale.append(f"{label} ({age:.1f}d)")

    if missing:
        return _probe("P1 Data Freshness", "FAIL",
                      f"Missing: {', '.join(missing)}", {"missing": missing})
    if stale:
        return _probe("P1 Data Freshness", "WARN",
                      f"Stale (>36h): {', '.join(stale)}", {"stale": stale})

    # Cross-check: does the JSON date field match today?
    date_mismatches = []
    for label, path in critical_files.items():
        data = _load_json(path)
        if data:
            file_date = data.get("date") or data.get("computed_date") or ""
            if file_date and file_date < TODAY:
                date_mismatches.append(f"{label} → date={file_date}")
    if date_mismatches:
        return _probe("P1 Data Freshness", "WARN",
                      f"JSON date field outdated: {'; '.join(date_mismatches)}",
                      {"date_mismatches": date_mismatches})

    return _probe("P1 Data Freshness", "PASS",
                  f"All {len(critical_files)} critical files fresh", None)


# ─────────────────────────────────────────────────────────────────────────────
# Probe 2 — RS Universe Size
# ─────────────────────────────────────────────────────────────────────────────

def probe_rs_universe_size() -> dict:
    """Universe should stay near the 1,325 benchmark size. Sudden drops = data failure."""
    path = DATA_DIR / "rs_universe" / "latest.json"
    data = _load_json(path)
    if not data:
        return _probe("P2 RS Universe Size", "FAIL",
                      "rs_universe/latest.json missing or corrupt", None)

    universe = data.get("universe", data)
    n = len(universe)

    if n < 300:
        return _probe("P2 RS Universe Size", "FAIL",
                      f"Universe collapsed: {n} tickers (expected ~1,325). "
                      "rs_ranker.py likely failed.", {"n_tickers": n})
    if n < 800:
        return _probe("P2 RS Universe Size", "WARN",
                      f"Universe smaller than expected: {n} tickers (expected ~1,325). "
                      "Check rs_benchmark.py for gaps.", {"n_tickers": n})
    if n > 2500:
        return _probe("P2 RS Universe Size", "WARN",
                      f"Universe suspiciously large: {n} tickers. "
                      "ETF/duplicate filtering may have failed.", {"n_tickers": n})

    return _probe("P2 RS Universe Size", "PASS",
                  f"Universe: {n} tickers", {"n_tickers": n})


# ─────────────────────────────────────────────────────────────────────────────
# Probe 3 — Fundamentals Coverage
# ─────────────────────────────────────────────────────────────────────────────

def probe_fundamentals_coverage() -> dict:
    """What % of screened tickers have EPS+Rev data in fundamentals_summary?"""
    if not DB_PATH.exists():
        return _probe("P3 Fundamentals Coverage", "FAIL",
                      "ohlcv.db missing", None)
    try:
        conn = sqlite3.connect(str(DB_PATH))
        # Total tickers in screening universe (from screening_results)
        total_row = conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM screening_results "
            "WHERE date = (SELECT MAX(date) FROM screening_results)"
        ).fetchone()
        total = total_row[0] if total_row else 0

        # Tickers with non-null EPS and Rev data
        covered_row = conn.execute(
            "SELECT COUNT(*) FROM fundamentals_summary "
            "WHERE eps_yoy_pct IS NOT NULL AND rev_yoy_pct IS NOT NULL"
        ).fetchone()
        covered = covered_row[0] if covered_row else 0

        # Ghost fails: tickers where BOTH eps AND rev are null
        ghost_row = conn.execute(
            "SELECT COUNT(*) FROM fundamentals_summary "
            "WHERE eps_yoy_pct IS NULL AND rev_yoy_pct IS NULL"
        ).fetchone()
        ghost = ghost_row[0] if ghost_row else 0
        conn.close()

        pct = covered / total * 100 if total else 0
        metric = {"total_screened": total, "with_fundamentals": covered,
                  "ghost_fails": ghost, "coverage_pct": round(pct, 1)}

        if pct < 40:
            return _probe("P3 Fundamentals Coverage", "FAIL",
                          f"Only {pct:.0f}% of screened tickers have fundamentals "
                          f"({covered}/{total}). EDGAR pipeline likely failing.", metric)
        if pct < 65:
            return _probe("P3 Fundamentals Coverage", "WARN",
                          f"{pct:.0f}% coverage ({covered}/{total}). "
                          f"Ghost fails: {ghost}. Run prewarm_analyst_cache.py.", metric)

        return _probe("P3 Fundamentals Coverage", "PASS",
                      f"{pct:.0f}% coverage ({covered}/{total} tickers, {ghost} ghost-fails)",
                      metric)
    except Exception as e:
        return _probe("P3 Fundamentals Coverage", "FAIL",
                      f"DB query error: {e}", None)


# ─────────────────────────────────────────────────────────────────────────────
# Probe 4 — Stage 2 Flag Coverage
# ─────────────────────────────────────────────────────────────────────────────

def probe_stage2_flag() -> dict:
    """stage2_flag and ma200_trending_up must be populated for the majority of tickers.
    If >30% are null, Gate 5 MA200-trending uses fallback too often."""
    if not DB_PATH.exists():
        return _probe("P4 Stage2 Flag Coverage", "FAIL", "ohlcv.db missing", None)
    try:
        conn = sqlite3.connect(str(DB_PATH))
        total_row  = conn.execute("SELECT COUNT(*) FROM ticker_meta").fetchone()
        null_row   = conn.execute(
            "SELECT COUNT(*) FROM ticker_meta WHERE stage2_flag IS NULL").fetchone()
        stage1_row = conn.execute(
            "SELECT COUNT(*) FROM ticker_meta WHERE stage2_flag = 1").fetchone()
        # ma200_trending_up — may not exist yet in older DB schemas
        try:
            ma200_null_row = conn.execute(
                "SELECT COUNT(*) FROM ticker_meta WHERE ma200_trending_up IS NULL").fetchone()
            ma200_up_row   = conn.execute(
                "SELECT COUNT(*) FROM ticker_meta WHERE ma200_trending_up = 1").fetchone()
            ma200_col_exists = True
        except Exception:
            ma200_null_row = (None,)
            ma200_up_row   = (None,)
            ma200_col_exists = False
        conn.close()

        total      = total_row[0]  if total_row  else 0
        null_n     = null_row[0]   if null_row   else 0
        s1_n       = stage1_row[0] if stage1_row else 0
        null_pct   = null_n / total * 100 if total else 100
        stage1_pct = s1_n / total * 100 if total else 0

        ma200_null = ma200_null_row[0] if ma200_null_row[0] is not None else total
        ma200_up   = ma200_up_row[0]   if ma200_up_row[0]   is not None else 0
        ma200_null_pct = ma200_null / total * 100 if total else 100

        metric = {
            "total": total,
            "stage2_null": null_n, "stage2_true": s1_n,
            "null_pct": round(null_pct, 1), "stage2_pct": round(stage1_pct, 1),
            "ma200_trending_col": ma200_col_exists,
            "ma200_null": ma200_null, "ma200_up": ma200_up,
            "ma200_null_pct": round(ma200_null_pct, 1),
        }

        # Hard fail: stage2_flag missing entirely (pipeline_metrics not running)
        if null_pct > 60:
            return _probe("P4 Stage2 Flag Coverage", "FAIL",
                          f"{null_pct:.0f}% of stage2_flag NULL ({null_n}/{total}). "
                          "pipeline_metrics.py Step B3 likely not running.", metric)

        # Warn: ma200_trending_up column missing (needs pipeline_metrics re-run after upgrade)
        if not ma200_col_exists:
            return _probe("P4 Stage2 Flag Coverage", "WARN",
                          "ma200_trending_up column missing from ticker_meta. "
                          "Run pipeline_metrics.py to add it (Minervini Condition 2 exact).", metric)

        # Warn: high null rate in either column (recent IPOs acceptable, but > 50% is too many)
        if max(null_pct, ma200_null_pct) > 50:
            return _probe("P4 Stage2 Flag Coverage", "WARN",
                          f"stage2_flag null={null_pct:.0f}%, ma200_trending null={ma200_null_pct:.0f}%. "
                          "Many tickers lack 200 bars of history (expected for new listings).", metric)

        return _probe("P4 Stage2 Flag Coverage", "PASS",
                      f"stage2_flag: {stage1_pct:.0f}% pass ({null_pct:.0f}% null). "
                      f"ma200_trending_up: {ma200_up}/{total-ma200_null} pass "
                      f"({ma200_null_pct:.0f}% null).",
                      metric)
    except Exception as e:
        return _probe("P4 Stage2 Flag Coverage", "FAIL",
                      f"DB query error: {e}", None)


# ─────────────────────────────────────────────────────────────────────────────
# Probe 5 — Regime Score Sanity
# ─────────────────────────────────────────────────────────────────────────────

def probe_regime_score() -> dict:
    """effective_score must be in [0,85] and consistent with the declared regime."""
    path = DATA_DIR / "regime" / "market_health.json"
    data = _load_json(path)
    if not data:
        return _probe("P5 Regime Score Sanity", "FAIL",
                      "market_health.json missing or corrupt", None)

    # market_regime.py writes key as "regime_score" (not "effective_score")
    score   = (data.get("regime_score") or data.get("effective_score") or
               data.get("raw_score") or data.get("score"))
    regime  = data.get("regime", "Unknown")
    cash_fl = data.get("cash_floor", None)
    metric  = {"regime": regime, "score": score, "cash_floor": cash_fl}

    if score is None:
        return _probe("P5 Regime Score Sanity", "WARN",
                      "No score key found in market_health.json "
                      "(checked: regime_score, effective_score, raw_score, score).", metric)
    if not (0 <= score <= 85):
        return _probe("P5 Regime Score Sanity", "FAIL",
                      f"Score {score} out of range [0,85]. Clamping logic broken.", metric)

    # Regime-score consistency check
    expected_ranges = {
        "Markup":       (50, 85),
        "Sideways":     (25, 60),
        "Distribution": (20, 55),
        "Markdown":     (0,  35),
    }
    rng = expected_ranges.get(regime)
    if rng and not (rng[0] <= score <= rng[1]):
        return _probe("P5 Regime Score Sanity", "WARN",
                      f"Regime={regime} but score={score} outside expected [{rng[0]},{rng[1]}]. "
                      "Check factor scoring in classify_v2_regime().", metric)

    return _probe("P5 Regime Score Sanity", "PASS",
                  f"Regime={regime}, score={score}, cash_floor={cash_fl}", metric)


# ─────────────────────────────────────────────────────────────────────────────
# Probe 6 — Foreign Filer Fundamentals
# ─────────────────────────────────────────────────────────────────────────────

def probe_foreign_filers() -> dict:
    """ARM and TSM must have fundamentals from any source.
    ASML coverage is tracked but allowed to be manual-only for now."""
    critical_filers  = {"ARM", "TSM"}     # must have data
    tracked_filers   = {"ASML", "UMC"}    # warn if missing

    # Check fundamentals_summary DB
    db_covered: set[str] = set()
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            rows = conn.execute(
                "SELECT ticker FROM fundamentals_summary "
                "WHERE eps_yoy_pct IS NOT NULL AND ticker IN ('ARM','TSM','ASML','UMC','GFS')"
            ).fetchall()
            conn.close()
            db_covered = {r[0] for r in rows}
        except Exception:
            pass

    # Check manual_fundamentals.json
    manual_path = DATA_DIR / "manual_fundamentals.json"
    manual = _load_json(manual_path) or {}
    manual_covered = {t for t in manual
                      if t not in ("_note", "_last_updated")
                      and manual[t].get("eps_yoy_pct") is not None}

    all_covered  = db_covered | manual_covered
    critical_gap = critical_filers - all_covered
    tracked_gap  = tracked_filers - all_covered
    metric = {"db_covered": sorted(db_covered), "manual_covered": sorted(manual_covered),
              "missing_critical": sorted(critical_gap), "missing_tracked": sorted(tracked_gap)}

    if critical_gap:
        return _probe("P6 Foreign Filer Coverage", "FAIL",
                      f"Critical filers missing fundamentals: {sorted(critical_gap)}. "
                      "Check edgar_6k or add to manual_fundamentals.json.", metric)
    if tracked_gap:
        return _probe("P6 Foreign Filer Coverage", "WARN",
                      f"Tracked filers missing: {sorted(tracked_gap)}. "
                      "Add to manual_fundamentals.json.", metric)

    db_arm_source = "edgar_6k" if "ARM" in db_covered else "manual_only"
    return _probe("P6 Foreign Filer Coverage", "PASS",
                  f"All critical filers covered. ARM source: {db_arm_source}. "
                  f"DB: {sorted(db_covered)}, Manual: {sorted(manual_covered)}",
                  metric)


# ─────────────────────────────────────────────────────────────────────────────
# Probe 7 — Paper Portfolio Integrity
# ─────────────────────────────────────────────────────────────────────────────

def probe_paper_portfolio() -> dict:
    """paper_portfolio_state.json must exist with valid structure.
    Each open position must have: ticker, entry_price, stop, size_pct, entry_date."""
    path = DATA_DIR / "portfolio" / "paper_portfolio_state.json"
    data = _load_json(path)
    if data is None:
        return _probe("P7 Paper Portfolio", "WARN",
                      "paper_portfolio_state.json missing. Paper trading not initialised.",
                      None)

    required_pos_fields = {"ticker", "entry_price", "stop_price", "size_pct", "entry_date"}
    positions_raw = data.get("positions") or data.get("open_positions") or []
    # positions may be a dict {ticker: {...}} or a list [{...}] — normalise to list
    # When converting from dict, inject dict key as 'ticker' if missing,
    # and normalise 'stop' → 'stop_price' for positions that use the old field name.
    if isinstance(positions_raw, dict):
        positions = []
        for key, val in positions_raw.items():
            if not isinstance(val, dict):
                continue
            pos = dict(val)
            if "ticker" not in pos:
                pos["ticker"] = key          # use dict key as ticker
            if "stop_price" not in pos and "stop" in pos:
                pos["stop_price"] = pos["stop"]  # normalise alias
            positions.append(pos)
    else:
        positions = list(positions_raw)
    cash_pct_raw = data.get("cash_pct") or data.get("cash_allocation")
    deployed_raw = data.get("deployed_pct")
    # Normalise: cash_pct may be fraction (0-1) or percentage (0-100).
    # Detect by value: if <= 1.0 AND portfolio clearly not 1% cash, treat as fraction.
    if cash_pct_raw is not None and cash_pct_raw <= 1.0:
        cash_pct = round(cash_pct_raw * 100, 1)      # 0.40 → 40.0%
    else:
        cash_pct = cash_pct_raw                       # already in percentage
    if deployed_raw is not None and deployed_raw <= 1.0:
        deployed_pct_norm = round(deployed_raw * 100, 1)
    else:
        deployed_pct_norm = deployed_raw

    issues = []
    for pos in positions:
        tkr = pos.get("ticker", "?")
        missing = required_pos_fields - set(pos.keys())
        if missing:
            issues.append(f"{tkr} missing: {missing}")
        # Stop must be below entry (allow stop == entry: valid breakeven trailing stop after +15% gain)
        entry = pos.get("entry_price") or 0
        stop  = pos.get("stop_price") or 0
        if entry and stop and stop > entry * 1.001:
            issues.append(f"{tkr} stop ({stop}) > entry ({entry}) — inverted stop")
        # Size must be in [1%, 30%]
        size = pos.get("size_pct") or 0
        if size and not (1 <= size <= 30):
            issues.append(f"{tkr} size_pct={size} out of [1%,30%]")

    metric = {"n_positions": len(positions), "cash_pct": cash_pct,
              "issues": issues}

    if issues:
        return _probe("P7 Paper Portfolio", "WARN",
                      f"{len(issues)} integrity issues: {'; '.join(issues[:3])}",
                      metric)

    # Cash + positions should sum ≈ 100%
    # Use deployed_pct from portfolio if available; else sum size_pct fields.
    if isinstance(positions, list) and cash_pct is not None:
        if deployed_pct_norm is not None:
            deployed = deployed_pct_norm
        else:
            raw_sizes = [p.get("size_pct", 0) or 0 for p in positions]
            # Normalise individual position sizes too
            deployed = sum((s * 100 if s <= 1.0 else s) for s in raw_sizes)
        total = deployed + cash_pct
        if abs(total - 100) > 5:
            return _probe("P7 Paper Portfolio", "WARN",
                          f"Cash ({cash_pct}%) + deployed ({deployed}%) = {total:.1f}% ≠ 100%",
                          {**metric, "deployed_pct": deployed})

    return _probe("P7 Paper Portfolio", "PASS",
                  f"{len(positions)} open positions, cash={cash_pct:.1f}%", metric)


# ─────────────────────────────────────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────────────────────────────────────

def run() -> dict:
    print(f"\n{'='*58}")
    print(f"  System Health Probe  [{TODAY}]")
    print(f"{'='*58}")

    probes = [
        probe_data_freshness,
        probe_rs_universe_size,
        probe_fundamentals_coverage,
        probe_stage2_flag,
        probe_regime_score,
        probe_foreign_filers,
        probe_paper_portfolio,
    ]

    results = []
    for fn in probes:
        try:
            r = fn()
        except Exception as e:
            r = _probe(fn.__name__, "FAIL", f"Probe raised exception: {e}", None)
        results.append(r)
        verdict_line = f"  {r['icon']} {r['probe']:<28} {r['verdict']:<5}  {r['detail']}"
        print(verdict_line)

    # Summary
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for r in results:
        counts[r["verdict"]] += 1

    fails = [r for r in results if r["verdict"] == "FAIL"]
    warns = [r for r in results if r["verdict"] == "WARN"]

    overall = "FAIL" if fails else ("WARN" if warns else "PASS")
    print(f"\n  Overall: {overall}  "
          f"(PASS={counts['PASS']} WARN={counts['WARN']} FAIL={counts['FAIL']})")
    if fails:
        print(f"  ❌ FAIL probes require immediate attention before trading.")
    if warns:
        print(f"  ⚠️  WARN probes should be reviewed — may affect signal quality.")

    output = {
        "date":         TODAY,
        "computed_at":  datetime.now().isoformat(),
        "overall":      overall,
        "counts":       counts,
        "n_fails":      counts["FAIL"],
        "n_warns":      counts["WARN"],
        "probes":       results,
        "fail_details": [r["detail"] for r in fails],
        "warn_details": [r["detail"] for r in warns],
    }
    (OUT_DIR / "alerts.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n  -> data/system_health/alerts.json  (overall={overall})")
    return output


if __name__ == "__main__":
    run()
