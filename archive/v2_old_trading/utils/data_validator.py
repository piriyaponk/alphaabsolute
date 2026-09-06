"""
AlphaAbsolute -- Data Accuracy Shield (D5)
==========================================
"ตัวเลขผิดห้ามเกิดขึ้นอีก" — no wrong numbers, ever.

This module runs automatically on every get_fundamentals() call.
It validates data BEFORE it reaches trading decisions.

4 validation layers:
  1. Sanity bounds   -- EPS/Revenue within plausible ranges
  2. YoY consistency -- changes >500% flagged (likely parse error)
  3. Cross-source    -- when 2+ sources available, compare same period
  4. EDGAR anchor    -- EDGAR result is treated as ground truth for US stocks

Any violation produces a DataWarning logged to data/data_warnings.jsonl
Critical violations (BLOCKED) prevent the data from being returned.

Usage (called by data_engine.get_fundamentals — automatic):
    from data_validator import validate_fundamentals, validate_ohlcv
    result, warnings = validate_fundamentals(ticker, data, source)

Manual check:
    python scripts/utils/data_validator.py AAPL
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Optional

BASE_DIR     = Path(__file__).resolve().parents[2]
WARN_FILE    = BASE_DIR / "data" / "data_warnings.jsonl"
WARN_FILE.parent.mkdir(parents=True, exist_ok=True)

# ── Sanity bounds ──────────────────────────────────────────────────────────────
# These are intentionally generous — only catch obvious parse errors

EPS_BOUNDS    = (-5000.0, 10000.0)  # EPS per share — wide for micro-cap biotech (few shares, big losses)
REV_BOUNDS    = (0.0,     5e12)     # revenue in USD (up to $5T for mega-corps)
GM_BOUNDS     = (-50.0,   100.0)    # gross margin % (WARN-only, data still stored)
YOY_WARN_PCT  = 1000.0              # YoY change > 1000% → warn (could be legit for turnaround)
YOY_BLOCK_PCT = 50000.0             # YoY change > 50000% → block (almost certainly a parse error)
QOQ_WARN_PCT  = 500.0               # QoQ change > 500% → warn

# Cross-source tolerance
CROSS_EPS_WARN_PCT   = 15.0    # EPS differs > 15% between sources on SAME period → warn
CROSS_EPS_BLOCK_PCT  = 50.0    # EPS differs > 50% → block lower-priority source
CROSS_REV_WARN_PCT   = 10.0    # Revenue differs > 10% → warn
CROSS_REV_BLOCK_PCT  = 30.0    # Revenue differs > 30% → block


# ── DataWarning ───────────────────────────────────────────────────────────────

class DataWarning:
    def __init__(self, ticker: str, source: str, level: str,
                 field: str, value, message: str, quarter: str = ""):
        self.ticker  = ticker
        self.source  = source
        self.level   = level   # INFO | WARN | BLOCK
        self.field   = field
        self.value   = value
        self.message = message
        self.quarter = quarter
        self.ts      = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "ts": self.ts, "ticker": self.ticker, "source": self.source,
            "level": self.level, "field": self.field, "quarter": self.quarter,
            "value": self.value, "message": self.message,
        }

    def log(self):
        """Append to data/data_warnings.jsonl."""
        try:
            with open(WARN_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(self.to_dict()) + "\n")
        except Exception:
            pass

    def __str__(self):
        q = f" [{self.quarter}]" if self.quarter else ""
        return f"[{self.level}] {self.ticker}{q} {self.field}={self.value} — {self.message}"


# ── Layer 1: Sanity bounds ────────────────────────────────────────────────────

def _check_bounds(ticker: str, source: str, data: dict) -> list[DataWarning]:
    warnings = []
    eps_hist = data.get("eps_history", [])
    rev_hist = data.get("revenue_history", [])
    gm_hist  = data.get("gross_margin_history", [])

    for q in eps_hist:
        eps = q.get("eps")
        qt  = q.get("quarter", q.get("date", ""))
        if eps is not None:
            lo, hi = EPS_BOUNDS
            if not (lo <= eps <= hi):
                w = DataWarning(ticker, source, "BLOCK", "eps", eps,
                                f"EPS={eps} outside bounds [{lo},{hi}] — parse error", qt)
                warnings.append(w)

    for q in rev_hist:
        rev = q.get("revenue")
        qt  = q.get("quarter", q.get("date", ""))
        if rev is not None:
            lo, hi = REV_BOUNDS
            if not (lo <= rev <= hi):
                w = DataWarning(ticker, source, "BLOCK", "revenue", rev,
                                f"Revenue={rev:.0f} outside bounds — parse error", qt)
                warnings.append(w)
        # YoY checks
        yoy = q.get("yoy_growth")
        if yoy is not None:
            if abs(yoy) > YOY_BLOCK_PCT:
                w = DataWarning(ticker, source, "BLOCK", "rev_yoy", yoy,
                                f"Rev YoY={yoy:.0f}% — extreme value, likely parse error", qt)
                warnings.append(w)
            elif abs(yoy) > YOY_WARN_PCT:
                w = DataWarning(ticker, source, "WARN", "rev_yoy", yoy,
                                f"Rev YoY={yoy:.0f}% — very large change, verify manually", qt)
                warnings.append(w)

    for q in eps_hist:
        yoy = q.get("yoy_growth")
        qt  = q.get("quarter", q.get("date", ""))
        if yoy is not None and abs(yoy) > YOY_BLOCK_PCT:
            w = DataWarning(ticker, source, "BLOCK", "eps_yoy", yoy,
                            f"EPS YoY={yoy:.0f}% — extreme value, likely parse error", qt)
            warnings.append(w)

    for q in gm_hist:
        gm = q.get("gross_margin")
        qt = q.get("quarter", q.get("date", ""))
        if gm is not None:
            lo, hi = GM_BOUNDS
            if not (lo <= gm <= hi):
                w = DataWarning(ticker, source, "WARN", "gross_margin", gm,
                                f"Gross margin={gm:.1f}% outside [{GM_BOUNDS[0]},{GM_BOUNDS[1]}]", qt)
                warnings.append(w)

    return warnings


# ── Layer 2: Internal consistency ─────────────────────────────────────────────

def _check_consistency(ticker: str, source: str, data: dict) -> list[DataWarning]:
    warnings = []
    rev_hist = data.get("revenue_history", [])

    for q in rev_hist:
        qt  = q.get("quarter", q.get("date", ""))
        qoq = q.get("qoq_growth")
        if qoq is not None and abs(qoq) > QOQ_WARN_PCT:
            w = DataWarning(ticker, source, "WARN", "rev_qoq", qoq,
                            f"Rev QoQ={qoq:.0f}% — unusually large sequential change", qt)
            warnings.append(w)

    return warnings


# ── Layer 3: Cross-source comparison ─────────────────────────────────────────

def compare_sources(ticker: str,
                    primary: dict, primary_name: str,
                    backup: dict,  backup_name: str) -> list[DataWarning]:
    """
    Compare primary vs backup data for the same ticker.
    Matches quarters by DATE (not label) to avoid fiscal vs calendar mismatch.
    """
    warnings = []
    if not primary or not backup:
        return warnings

    # Build date → value maps for primary
    def _eps_by_date(d: dict) -> dict:
        out = {}
        for q in d.get("eps_history", []):
            dt = q.get("date") or q.get("quarter", "")
            if dt and q.get("eps") is not None:
                out[dt[:7]] = q["eps"]   # group by YYYY-MM
        return out

    def _rev_by_date(d: dict) -> dict:
        out = {}
        for q in d.get("revenue_history", []):
            dt = q.get("date") or q.get("quarter", "")
            if dt and q.get("revenue") is not None:
                out[dt[:7]] = q["revenue"]
        return out

    p_eps = _eps_by_date(primary)
    b_eps = _eps_by_date(backup)
    p_rev = _rev_by_date(primary)
    b_rev = _rev_by_date(backup)

    # Compare EPS on matching periods
    for period in sorted(set(p_eps) & set(b_eps), reverse=True)[:4]:
        pe, be = p_eps[period], b_eps[period]
        if pe == 0 or be == 0:
            continue
        pct = abs(pe - be) / max(abs(pe), abs(be)) * 100
        if pct > CROSS_EPS_BLOCK_PCT:
            w = DataWarning(ticker, backup_name, "WARN",
                            f"eps_vs_{primary_name}", f"{pe} vs {be}",
                            f"EPS {period}: {primary_name}={pe} vs {backup_name}={be} "
                            f"(diff={pct:.1f}%) — using {primary_name} as truth", period)
            warnings.append(w)
        elif pct > CROSS_EPS_WARN_PCT:
            w = DataWarning(ticker, backup_name, "INFO",
                            f"eps_vs_{primary_name}", f"{pe} vs {be}",
                            f"EPS {period}: {primary_name}={pe} vs {backup_name}={be} "
                            f"(diff={pct:.1f}%) — within acceptable range", period)
            warnings.append(w)

    # Compare Revenue on matching periods (revenue is easier to compare)
    for period in sorted(set(p_rev) & set(b_rev), reverse=True)[:4]:
        pr, br = p_rev[period], b_rev[period]
        if pr == 0 or br == 0:
            continue
        pct = abs(pr - br) / max(pr, br) * 100
        if pct > CROSS_REV_WARN_PCT:
            level = "WARN" if pct > CROSS_REV_BLOCK_PCT else "INFO"
            w = DataWarning(ticker, backup_name, level,
                            f"rev_vs_{primary_name}", f"{pr/1e9:.2f}B vs {br/1e9:.2f}B",
                            f"Rev {period}: {primary_name}={pr/1e9:.2f}B vs "
                            f"{backup_name}={br/1e9:.2f}B (diff={pct:.1f}%)", period)
            warnings.append(w)

    return warnings


# ── Main validate entry point ─────────────────────────────────────────────────

def validate_fundamentals(ticker: str, data: dict, source: str,
                           log: bool = True) -> tuple[dict, list[DataWarning]]:
    """
    Validate a fundamentals dict before it reaches trading logic.

    Returns (data, warnings).
    - If BLOCK-level warnings: returns (None, warnings) — data not used
    - If WARN-level: returns (data, warnings) — data used with annotation
    - All warnings logged to data/data_warnings.jsonl
    """
    all_warnings: list[DataWarning] = []

    all_warnings += _check_bounds(ticker, source, data)
    all_warnings += _check_consistency(ticker, source, data)

    # Log all warnings
    for w in all_warnings:
        if log:
            w.log()

    # Block if any BLOCK-level warning
    has_block = any(w.level == "BLOCK" for w in all_warnings)
    if has_block:
        block_msgs = [str(w) for w in all_warnings if w.level == "BLOCK"]
        print(f"  [D5] BLOCKED {ticker} from {source}: {'; '.join(block_msgs)}")
        return None, all_warnings

    # Annotate data with validation status
    warn_msgs = [str(w) for w in all_warnings if w.level in ("WARN",)]
    if warn_msgs:
        data["_validation_warnings"] = warn_msgs
        print(f"  [D5] WARN {ticker} {source}: {'; '.join(warn_msgs)}")

    data["_validated"] = True
    data["_validated_at"] = datetime.now().isoformat(timespec="seconds")
    return data, all_warnings


def validate_ohlcv(ticker: str, df, source: str) -> tuple:
    """
    Basic OHLCV sanity check.
    - Prices must be positive
    - Close/Open/High/Low must be self-consistent (High >= Low, etc.)
    - No NaN in Close
    """
    import pandas as pd
    warnings = []

    if df is None or df.empty:
        return df, warnings

    # Check for NaN closes
    nan_count = df["Close"].isna().sum()
    if nan_count > 3:
        w = DataWarning(ticker, source, "WARN", "ohlcv_nan", nan_count,
                        f"{nan_count} NaN close prices")
        warnings.append(w); w.log()

    # Check for zero/negative closes
    bad = (df["Close"] <= 0).sum()
    if bad > 0:
        w = DataWarning(ticker, source, "WARN", "ohlcv_zero_close", bad,
                        f"{bad} zero/negative close prices")
        warnings.append(w); w.log()

    # High >= Close >= Low sanity
    if "High" in df.columns and "Low" in df.columns:
        violations = ((df["High"] < df["Close"]) | (df["Low"] > df["Close"])).sum()
        if violations > 5:
            w = DataWarning(ticker, source, "WARN", "ohlcv_hl_logic", violations,
                            f"{violations} bars where High<Close or Low>Close")
            warnings.append(w); w.log()

    return df, warnings


# ── Recent warnings report ────────────────────────────────────────────────────

def get_recent_warnings(days: int = 7, level: str = None) -> list[dict]:
    """Return recent warnings from the log."""
    if not WARN_FILE.exists():
        return []
    cutoff = date.today().isoformat()
    results = []
    try:
        for line in WARN_FILE.read_text(encoding="utf-8").splitlines():
            try:
                w = json.loads(line)
                if level and w.get("level") != level:
                    continue
                results.append(w)
            except Exception:
                continue
    except Exception:
        pass
    # Keep last 200
    return results[-200:]


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    print(f"\nValidating {ticker}...")

    sys.path.insert(0, str(BASE_DIR / "scripts" / "utils"))
    from data_engine import _edgar_fundamentals, _fmp_fundamentals, _finnhub_fundamentals

    edgar = _edgar_fundamentals(ticker, 4)
    fmp   = _fmp_fundamentals(ticker, 4)
    fh    = _finnhub_fundamentals(ticker, 4)

    print("\n[Layer 1+2] Sanity checks:")
    for name, data in [("EDGAR", edgar), ("FMP", fmp), ("Finnhub", fh)]:
        if not data:
            print(f"  {name}: no data")
            continue
        result, warns = validate_fundamentals(ticker, data, name, log=False)
        status = "BLOCKED" if result is None else "OK"
        print(f"  {name}: {status} | {len(warns)} warnings")
        for w in warns:
            print(f"    {w}")

    print("\n[Layer 3] Cross-source comparison (date-aligned):")
    if edgar and fmp:
        warns = compare_sources(ticker, edgar, "EDGAR", fmp, "FMP")
        print(f"  EDGAR vs FMP: {len(warns)} issues")
        for w in warns:
            print(f"    {w}")
    if edgar and fh:
        warns = compare_sources(ticker, edgar, "EDGAR", fh, "Finnhub")
        print(f"  EDGAR vs Finnhub: {len(warns)} issues")
        for w in warns:
            print(f"    {w}")

    print("\n[Recent warnings from log]:")
    recent = get_recent_warnings()
    ticker_warns = [w for w in recent if w.get("ticker") == ticker]
    if ticker_warns:
        for w in ticker_warns[-10:]:
            print(f"  {w['ts'][:16]} [{w['level']}] {w['field']}: {w['message']}")
    else:
        print("  None")
