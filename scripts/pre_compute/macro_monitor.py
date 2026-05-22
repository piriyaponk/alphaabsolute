"""
AlphaAbsolute v2 -- A02 Macro Monitor
======================================
Tracks the macro backdrop: rates, credit, yield curve, DXY.
Outputs a macro_modifier that adjusts position sizing across all agents.

macro_modifier rules:
  Restrictive + credit_stress=True  -> 0.75 (reduce all sizes 25%)
  Restrictive only                  -> 0.90
  Neutral                           -> 1.00
  Supportive                        -> 1.00 (no leverage — cap at 1.0)

Output: data/regime/macro_state.json
Run:    6:00 AM daily (pre-market, alongside market_regime.py)

Cost: $0 (FRED only, no LLM)
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

OUT_DIR    = ROOT / "data" / "regime"
OUT_DIR.mkdir(parents=True, exist_ok=True)
MACRO_FILE = OUT_DIR / "macro_state.json"


def _fetch_fred(series_id: str, limit: int = 3) -> Optional[float]:
    try:
        from data_engine import get_macro
        obs = get_macro(series_id, limit=limit)
        if obs:
            return obs[0]["value"]
    except Exception:
        pass
    return None


def _fetch_fred_series(series_id: str, limit: int = 14) -> list[dict]:
    try:
        from data_engine import get_macro
        return get_macro(series_id, limit=limit) or []
    except Exception:
        return []


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def run() -> dict:
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  A02 Macro Monitor  [{today}]")
    print(f"{'='*55}")

    # ── Fetch FRED data ────────────────────────────────────────────────────────
    print("  Fetching FRED: yields, spread, DXY, CPI...")
    yield_10y   = _fetch_fred("DGS10")       # 10Y Treasury
    yield_2y    = _fetch_fred("DGS2")        # 2Y Treasury
    yield_curve = _fetch_fred("T10Y2Y")      # 10Y-2Y spread
    hy_spread   = _fetch_fred("BAMLH0A0HYM2") # HY OAS spread (%)
    real_yield  = _fetch_fred("DFII10")      # 10Y TIPS real yield
    fed_funds   = _fetch_fred("DFF")         # Effective Fed Funds Rate

    # CPI YoY (monthly data)
    cpi_yoy: Optional[float] = None
    cpi_obs = _fetch_fred_series("CPIAUCSL", limit=14)
    if len(cpi_obs) >= 13:
        v_now  = cpi_obs[0]["value"]
        v_prev = cpi_obs[12]["value"]
        if v_prev and v_prev > 0:
            cpi_yoy = round((v_now - v_prev) / v_prev * 100, 2)

    # ── Classify rate environment ──────────────────────────────────────────────
    rate_environment = "Neutral"
    rate_notes = []

    if yield_10y is not None:
        if yield_10y < 3.5:
            rate_environment = "Supportive"
            rate_notes.append(f"10Y={yield_10y:.2f}% (very supportive, ERP healthy)")
        elif yield_10y < 4.0:
            rate_environment = "Supportive"
            rate_notes.append(f"10Y={yield_10y:.2f}% (supportive, growth-friendly)")
        elif yield_10y < 4.5:
            rate_environment = "Neutral"
            rate_notes.append(f"10Y={yield_10y:.2f}% (neutral, ERP thin)")
        elif yield_10y < 5.0:
            rate_environment = "Restrictive"
            rate_notes.append(f"10Y={yield_10y:.2f}% (restrictive, squeezing valuation)")
        else:
            rate_environment = "Restrictive"
            rate_notes.append(f"10Y={yield_10y:.2f}% (very restrictive, ERP negative)")

    # Inflation override: CPI > 4% forces Restrictive regardless of yield level
    if cpi_yoy is not None and cpi_yoy > 4.0 and rate_environment == "Neutral":
        rate_environment = "Restrictive"
        rate_notes.append(f"CPI={cpi_yoy:.1f}% overrides to Restrictive (above Fed target by 2x)")

    # ── Classify yield curve ───────────────────────────────────────────────────
    yield_curve_label = "Unknown"
    if yield_curve is not None:
        if   yield_curve > 0.5:   yield_curve_label = "Normal"
        elif yield_curve > 0.0:   yield_curve_label = "Flat"
        elif yield_curve > -0.5:  yield_curve_label = "Flat"
        else:                     yield_curve_label = "Inverted"

    # ── Credit stress check ────────────────────────────────────────────────────
    credit_stress = False
    if hy_spread is not None and hy_spread > 5.5:
        credit_stress = True
        rate_notes.append(f"HY spread={hy_spread:.2f}% WIDE — credit stress detected")
    elif hy_spread is not None:
        rate_notes.append(f"HY spread={hy_spread:.2f}% (tight = risk-on)")

    # ── macro_modifier ─────────────────────────────────────────────────────────
    # Never exceed 1.0 — this system does not use leverage.
    if rate_environment == "Restrictive" and credit_stress:
        macro_modifier = 0.75
        modifier_note  = "Restrictive + credit stress: reduce all sizes 25%"
    elif rate_environment == "Restrictive":
        macro_modifier = 0.90
        modifier_note  = "Restrictive: reduce all sizes 10%"
    elif rate_environment == "Neutral":
        macro_modifier = 1.00
        modifier_note  = "Neutral: standard sizing"
    else:  # Supportive — cap at 1.0 (no leverage)
        macro_modifier = 1.00
        modifier_note  = "Supportive: standard sizing (no leverage)"

    # ── 1-2 sentence macro note ────────────────────────────────────────────────
    macro_note_parts = []

    if rate_environment == "Supportive":
        macro_note_parts.append("Rate environment is supportive for growth equities.")
    elif rate_environment == "Restrictive":
        macro_note_parts.append("Rates are restrictive — growth multiples under pressure.")
    else:
        macro_note_parts.append("Rate environment is neutral for equities.")

    if credit_stress:
        macro_note_parts.append("Credit markets showing stress — risk-off posture warranted.")
    elif hy_spread is not None and hy_spread < 4.0:
        macro_note_parts.append("Credit markets calm — risk appetite intact.")

    if yield_curve_label == "Inverted":
        macro_note_parts.append("Yield curve inverted — historical recession precursor.")
    elif yield_curve_label == "Normal":
        macro_note_parts.append("Yield curve healthy (normal).")

    if cpi_yoy is not None:
        if cpi_yoy > 4.0:
            macro_note_parts.append(f"CPI at {cpi_yoy:.1f}% — inflation re-acceleration risk.")
        elif cpi_yoy < 3.0:
            macro_note_parts.append(f"CPI at {cpi_yoy:.1f}% — inflation contained.")

    macro_note = " ".join(macro_note_parts[:2])  # keep it to 1-2 sentences

    # ── Compare to previous state ──────────────────────────────────────────────
    prev = _load_json(MACRO_FILE)
    prev_env = prev.get("rate_environment", rate_environment)
    changed = prev_env != rate_environment

    # ── Build output ───────────────────────────────────────────────────────────
    state = {
        "date":              today,
        "rate_environment":  rate_environment,
        "credit_stress":     credit_stress,
        "yield_curve":       yield_curve_label,
        "macro_modifier":    macro_modifier,
        "macro_note":        macro_note,
        "modifier_note":     modifier_note,
        "environment_changed": changed,

        # Raw data
        "yield_10y":    round(yield_10y,    3) if yield_10y    else None,
        "yield_2y":     round(yield_2y,     3) if yield_2y     else None,
        "yield_curve_val": round(yield_curve, 3) if yield_curve else None,
        "hy_spread_pct": round(hy_spread,   2) if hy_spread    else None,
        "real_yield":   round(real_yield,   3) if real_yield   else None,
        "fed_funds":    round(fed_funds,    2) if fed_funds     else None,
        "cpi_yoy":      cpi_yoy,

        "rate_notes":    rate_notes,
        "generated_at":  datetime.now().strftime("%H:%M"),
    }

    MACRO_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")

    # Console summary
    env_emoji = {"Supportive": "[+]", "Neutral": "[~]", "Restrictive": "[-]"}.get(rate_environment, "[?]")
    print(f"\n  {env_emoji} Rate environment: {rate_environment}")
    print(f"  Yield curve: {yield_curve_label} | Credit stress: {credit_stress}")
    print(f"  macro_modifier: {macro_modifier} — {modifier_note}")
    print(f"  Note: {macro_note}")
    if changed:
        print(f"  [!] Environment changed: {prev_env} -> {rate_environment}")
    print(f"  -> Written: {MACRO_FILE}")

    return state


if __name__ == "__main__":
    run()
