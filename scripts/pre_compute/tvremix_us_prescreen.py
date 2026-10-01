"""
tvremix US Pre-Screen — AlphaAbsolute System 4 Pipeline
=========================================================
Runs ONE tvremix screener call → top 500 US momentum stocks.
Outputs data/rs_universe/tvremix_us_prescreen.json

Provides TradingView-native momentum signals as enrichment layer:
  price_vs_ema200_pct   → above/below MA200
  price_vs_ema50_pct    → above/below MA50
  Perf.3M               → 3-month return (RS proxy)
  RSI                   → momentum oscillator
  days_to_earnings      → earnings proximity
  adtv_usd              → liquidity filter ($10M floor)
  pct_from_3m_high      → distance from recent high

Cost: 1 tvremix call (vs 0 currently — pure enrichment, no OHLCV replacement)
Used by: rs_ranker.py (optional enrichment, graceful degradation if tvremix down)
System: System 4 US momentum — read by s4_brain as supplemental signal
"""

import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
OUT_FILE = BASE_DIR / "data" / "rs_universe" / "tvremix_us_prescreen.json"
OUT_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load_env():
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _to_ticker(tv_symbol: str) -> str:
    """NASDAQ:NVDA → NVDA  |  NYSE:AAPL → AAPL"""
    return tv_symbol.split(":")[-1] if ":" in tv_symbol else tv_symbol


def _is_listed(tv_symbol: str) -> bool:
    """Keep only NYSE/NASDAQ — exclude OTC, AMEX, etc."""
    exchange = tv_symbol.split(":")[0] if ":" in tv_symbol else ""
    return exchange in ("NASDAQ", "NYSE")


def run():
    _load_env()

    # Import tvremix client (lives in scripts/research/)
    sys.path.insert(0, str(BASE_DIR / "scripts" / "research"))
    try:
        from tvremix_client import _call_tool, _load_key, TvremixError
    except ImportError as e:
        print(f"[tvremix-US] Import failed: {e} — skipping")
        return False

    try:
        api_key = _load_key()
    except Exception as e:
        print(f"[tvremix-US] API key missing: {e} — skipping")
        return False

    today = date.today().isoformat()
    print(f"\n[tvremix-US Pre-Screen] {today}")

    # ── Single screener call — 500 US momentum stocks ────────────────────────
    t0 = time.time()
    try:
        result = _call_tool("run_screener", {
            "market": "america",
            "sort_by": "Perf.3M",
            "sort_order": "desc",
            "limit": 500,
            "filters": [
                {"left": "type",  "operation": "equal",   "right": "stock"},
                {"left": "close", "operation": "greater", "right": 5},
                # Above EMA50 = basic momentum filter (cuts ~40% of universe)
                {"left": "price_vs_ema50_pct", "operation": "greater", "right": -5},
                # Min ADTV proxy: average_volume_10d × close > $10M (keep liquid names)
                # (exact ADTV filter not available in screener — we post-filter below)
            ],
        }, api_key)
    except Exception as e:
        print(f"[tvremix-US] Screener call failed: {e} — skipping")
        return False

    elapsed = time.time() - t0

    if not result.get("success"):
        print(f"[tvremix-US] Screener returned error: {result.get('error')} — skipping")
        return False

    rows = result.get("data", {}).get("results", [])
    print(f"[tvremix-US] Raw screener rows: {len(rows)} ({elapsed:.1f}s)")

    # ── Post-filter: NYSE/NASDAQ only + ADTV > $10M ──────────────────────────
    records = []
    skipped_otc = 0
    skipped_adtv = 0

    for rank, r in enumerate(rows, start=1):
        symbol = r.get("symbol", "")
        if not _is_listed(symbol):
            skipped_otc += 1
            continue

        close = r.get("close") or 0
        vol10d = r.get("average_volume_10d_calc") or 0
        adtv_usd = close * vol10d

        if adtv_usd < 10_000_000:  # < $10M ADTV — too illiquid
            skipped_adtv += 1
            continue

        ticker = _to_ticker(symbol)

        # Map screening gate signals
        pct_ema200 = r.get("price_vs_ema200_pct")  # >0 = above MA200 (S1 proxy)
        pct_ema50  = r.get("price_vs_ema50_pct")   # >-5% = near MA50 (S6 proxy)
        pct_3m_high = r.get("pct_from_high_3m")    # >-20% = not extended down

        records.append({
            "ticker":           ticker,
            "tv_symbol":        symbol,
            "tv_rank":          rank,          # position in Perf.3M sort
            "close":            close,
            "perf_3m":          r.get("Perf.3M") or 0,
            "perf_1m":          r.get("Perf.1M") or 0,
            "rsi":              r.get("RSI") or 50,
            "ema50":            r.get("EMA50"),
            "ema200":           r.get("EMA200"),
            "pct_vs_ema200":    pct_ema200,    # >0 = above MA200
            "pct_vs_ema50":     pct_ema50,     # >-5 = price near MA50
            "pct_from_3m_high": pct_3m_high,  # >-20 = within range
            "adtv_usd":         adtv_usd,
            "vol_ratio_10d":    r.get("vol_ratio_10d") or 1.0,
            "days_to_earnings": r.get("days_to_earnings"),
            "market_cap":       r.get("market_cap_basic"),
            "sector":           r.get("sector", ""),
            # Momentum signal flags — for rs_ranker enrichment and s4_brain
            "tv_above_ema200":  bool(pct_ema200 is not None and pct_ema200 > 0),
            "tv_above_ema50":   bool(pct_ema50 is not None and pct_ema50 > -5),
            "tv_rs_positive":   bool((r.get("Perf.3M") or 0) > 0),
            "tv_rsi_ok":        bool((r.get("RSI") or 0) > 45),
            "tv_in_range":      bool(pct_3m_high is not None and pct_3m_high > -20),
            "tv_recommendation": r.get("Recommend.All"),
        })

    print(f"[tvremix-US] After filter: {len(records)} stocks "
          f"(dropped {skipped_otc} OTC, {skipped_adtv} low-ADTV)")

    # ── Gate summary for top records ─────────────────────────────────────────
    above_ema200 = sum(1 for r in records if r["tv_above_ema200"])
    rs_positive  = sum(1 for r in records if r["tv_rs_positive"])
    print(f"[tvremix-US] Above EMA200: {above_ema200}/{len(records)} | RS+: {rs_positive}/{len(records)}")

    # ── Build ticker-keyed lookup for rs_ranker enrichment ───────────────────
    ticker_index = {r["ticker"]: r for r in records}

    output = {
        "date":           today,
        "generated_at":   datetime.now().isoformat(),
        "source":         "tvremix run_screener market=america",
        "calls_used":     1,
        "total_records":  len(records),
        "records":        records,
        "ticker_index":   ticker_index,  # fast O(1) lookup by ticker symbol
    }

    OUT_FILE.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"[tvremix-US] Saved → {OUT_FILE.name} ({len(records)} tickers)")
    return True


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
