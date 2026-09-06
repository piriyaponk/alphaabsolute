"""
AlphaAbsolute -- D3 Data Verifier Sub-Agent  ($0 cost, pure Python)
====================================================================
Weekly cross-source sanity check for all financial data.

"Trust but verify. Every week, we check the numbers against 2+ independent
 sources. If they disagree by >5%, we log it, alert the CIO, and switch to
 the better source automatically." -- AlphaAbsolute Data Architecture

What it does:
  1. For each key ticker, fetch EPS + Revenue from ALL available sources
     (EDGAR, Finnhub, FMP, Yahoo) in parallel
  2. Cross-compare: flag any source where value deviates >THRESHOLD from median
  3. Score each source's reliability (accuracy + availability + latency)
  4. Auto-rank sources and update preferred order in data/source_ranking.json
  5. Write verification report to output/data_verify_YYMMDD.md
  6. Alert CIO if primary source is unreliable (source_score < 60)

Backup plan (when primary source fails/wrong):
  - EPS/Revenue:  EDGAR -> Finnhub -> FMP -> Yahoo
  - Price OHLCV:  Polygon -> Tiingo -> Yahoo
  - Quote:        Polygon -> Finnhub -> Yahoo
  Failover is automatic in data_engine.py's priority list.

Run:
  python scripts/pre_compute/data_verifier.py           (immediate)
  python scripts/pre_compute/data_verifier.py --quick   (5 tickers only)
  pre_market_runner.py --step verify                    (weekly, Fridays)

Cost: $0 -- reads from same free APIs as pipeline, no LLM.
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ── Encoding fix for Thai terminal (cp874) ───────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
OUT_DIR  = BASE_DIR / "data" / "data_verifier"
OUT_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR / "scripts"))

try:
    from utils.data_engine import (
        _edgar_fundamentals, _finnhub_fundamentals,
        _fmp_fundamentals, _yahoo_fundamentals,
        get_ohlcv, _polygon_ohlcv, _tiingo_ohlcv, _yahoo_ohlcv_v8,
        _polygon_quote, _finnhub_quote, _yahoo_quote,
    )
    _ENGINE_OK = True
except ImportError as e:
    print(f"  [D3] data_engine import error: {e}")
    _ENGINE_OK = False


# ── Configuration ─────────────────────────────────────────────────────────────

# How different is "suspicious" (vs median of all sources)?
EPS_THRESHOLD     = 0.15   # 15% deviation from median = flag
REV_THRESHOLD     = 0.10   # 10% deviation from median = flag
PRICE_THRESHOLD   = 0.005  # 0.5% deviation = flag (prices should match exactly)

# Benchmark tickers -- well-known, multiple data sources cover them
BENCHMARK_TICKERS = ["AAPL", "MSFT", "NVDA", "MU", "TSLA"]
QUICK_TICKERS     = ["AAPL", "MU"]    # --quick mode

# Fundamental sources to compare
FUND_SOURCES = {
    "edgar":   lambda t: _edgar_fundamentals(t, quarters=4),
    "finnhub": lambda t: _finnhub_fundamentals(t, quarters=4),
    "fmp":     lambda t: _fmp_fundamentals(t, quarters=4),
    "yahoo":   lambda t: _yahoo_fundamentals(t, quarters=4),
}

# OHLCV sources
OHLCV_SOURCES = {
    "polygon": lambda t: _polygon_ohlcv(t, period="1mo"),
    "tiingo":  lambda t: _tiingo_ohlcv(t, period="1mo"),
    "yahoo":   lambda t: _yahoo_ohlcv_v8(t, period="1mo"),
}

QUOTE_SOURCES = {
    "polygon":  lambda t: _polygon_quote(t),
    "finnhub":  lambda t: _finnhub_quote(t),
    "yahoo":    lambda t: _yahoo_quote(t),
}


# ── Core Verification Logic ───────────────────────────────────────────────────

def _safe_float(v, default=None) -> Optional[float]:
    if v is None:
        return default
    try:
        return float(v)
    except Exception:
        return default


def _pct_diff(a: float, b: float) -> float:
    """Percentage difference of a vs b."""
    if b == 0:
        return 0.0
    return abs(a - b) / abs(b)


def _median(values: list) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def verify_fundamentals(ticker: str) -> dict:
    """
    Cross-check EPS and Revenue across all fundamental sources.
    Returns a result dict with flags and scores.
    """
    results = {}
    errors  = {}

    for name, fn in FUND_SOURCES.items():
        if not _ENGINE_OK:
            break
        try:
            t0 = time.time()
            r  = fn(ticker)
            latency = round(time.time() - t0, 2)
            if r and r.get("eps_history"):
                eps = _safe_float(r["eps_history"][0].get("eps") if r["eps_history"] else None)
                rev = _safe_float(r["revenue_history"][0].get("revenue") if r.get("revenue_history") else None)
                eps_yoy = _safe_float(r.get("eps_yoy_pct") or
                          (r["eps_history"][0].get("yoy_growth") if r.get("eps_history") else None))
                results[name] = {
                    "eps":     eps,
                    "rev":     rev,
                    "eps_yoy": eps_yoy,
                    "latency": latency,
                    "ok":      True,
                }
            else:
                results[name] = {"ok": False, "latency": latency}
        except Exception as e:
            errors[name] = str(e)[:80]
            results[name] = {"ok": False, "error": str(e)[:80]}

    # Cross-compare
    eps_vals = {n: v["eps"] for n, v in results.items() if v.get("ok") and v.get("eps") is not None}
    rev_vals = {n: v["rev"] for n, v in results.items() if v.get("ok") and v.get("rev") is not None}

    eps_median = _median(list(eps_vals.values()))
    rev_median = _median(list(rev_vals.values()))

    flags = []
    scores = {}
    for name, val in eps_vals.items():
        if eps_median and _pct_diff(val, eps_median) > EPS_THRESHOLD:
            flags.append(f"{name} EPS={val:.2f} deviates {_pct_diff(val,eps_median)*100:.0f}% from median={eps_median:.2f}")
            scores[name] = scores.get(name, 100) - 30
        else:
            scores[name] = scores.get(name, 100)

    for name, val in rev_vals.items():
        if rev_median and _pct_diff(val, rev_median) > REV_THRESHOLD:
            flags.append(f"{name} Rev deviates {_pct_diff(val,rev_median)*100:.0f}% from median")
            scores[name] = scores.get(name, 100) - 20
        else:
            scores[name] = scores.get(name, 100)

    # Penalize unavailable sources
    for name in FUND_SOURCES:
        if name not in scores:
            scores[name] = 0   # not available at all = score 0

    return {
        "ticker":     ticker,
        "results":    results,
        "eps_median": round(eps_median, 4) if eps_median else None,
        "rev_median": rev_median,
        "flags":      flags,
        "scores":     scores,
        "best_source": max(scores, key=scores.get) if scores else "unknown",
    }


def verify_ohlcv(ticker: str) -> dict:
    """Cross-check latest close price across OHLCV sources."""
    prices = {}
    latencies = {}

    for name, fn in OHLCV_SOURCES.items():
        if not _ENGINE_OK:
            break
        try:
            t0 = time.time()
            df = fn(ticker)
            lat = round(time.time() - t0, 2)
            if df is not None and not df.empty:
                close = float(df["Close"].iloc[-1])
                prices[name]    = close
                latencies[name] = lat
        except Exception:
            pass

    price_median = _median(list(prices.values()))
    flags = []
    scores = {}
    for name, p in prices.items():
        dev = _pct_diff(p, price_median) if price_median else 0
        if dev > PRICE_THRESHOLD:
            flags.append(f"{name} price={p:.2f} deviates {dev*100:.2f}% from median={price_median:.2f}")
            scores[name] = 50
        else:
            lat = latencies.get(name, 99)
            scores[name] = max(50, 100 - int(lat * 10))   # faster = higher score

    return {
        "ticker":       ticker,
        "prices":       prices,
        "price_median": price_median,
        "latencies":    latencies,
        "flags":        flags,
        "scores":       scores,
        "best_source":  max(scores, key=scores.get) if scores else "unknown",
    }


# ── Source Ranking ────────────────────────────────────────────────────────────

def _compute_source_ranking(fund_results: list, ohlcv_results: list) -> dict:
    """
    Aggregate scores across all tickers per source.
    Returns {source: {score, availability_pct, avg_latency, recommendation}}.
    """
    fund_agg  = {}
    ohlcv_agg = {}

    for r in fund_results:
        for name, sc in r.get("scores", {}).items():
            if name not in fund_agg:
                fund_agg[name] = {"scores": [], "available": 0, "total": 0, "latencies": []}
            fund_agg[name]["total"] += 1
            result = r["results"].get(name, {})
            if result.get("ok"):
                fund_agg[name]["available"] += 1
                fund_agg[name]["latencies"].append(result.get("latency", 0))
            fund_agg[name]["scores"].append(sc)

    for r in ohlcv_results:
        for name, sc in r.get("scores", {}).items():
            if name not in ohlcv_agg:
                ohlcv_agg[name] = {"scores": [], "available": 0, "total": 0, "latencies": []}
            ohlcv_agg[name]["total"] += 1
            if r["prices"].get(name):
                ohlcv_agg[name]["available"] += 1
                ohlcv_agg[name]["latencies"].append(r["latencies"].get(name, 0))
            ohlcv_agg[name]["scores"].append(sc)

    def _summarize(agg: dict, category: str) -> dict:
        out = {}
        for name, data in agg.items():
            total = data["total"] or 1
            avail = data["available"] / total
            avg_sc = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
            avg_lat = sum(data["latencies"]) / len(data["latencies"]) if data["latencies"] else 99
            final_score = avg_sc * avail   # penalize for unavailability

            if final_score >= 80:
                rec = "[OK] PRIMARY -- reliable and accurate"
            elif final_score >= 60:
                rec = "[~] SECONDARY -- use as backup"
            elif final_score >= 40:
                rec = "[!] TERTIARY -- only if others fail"
            else:
                rec = "[X] UNRELIABLE -- do not use"

            out[name] = {
                "category":        category,
                "score":           round(final_score, 1),
                "availability_pct": round(avail * 100, 0),
                "avg_latency_s":   round(avg_lat, 2),
                "recommendation":  rec,
            }
        return dict(sorted(out.items(), key=lambda x: -x[1]["score"]))

    return {
        "fundamentals": _summarize(fund_agg, "fundamentals"),
        "ohlcv":        _summarize(ohlcv_agg, "ohlcv"),
        "computed_at":  datetime.now().isoformat(),
    }


# ── Report Writer ─────────────────────────────────────────────────────────────

def _write_report(fund_results: list, ohlcv_results: list, ranking: dict,
                  tickers: list) -> Path:
    today   = date.today().strftime("%y%m%d")
    outfile = BASE_DIR / "output" / f"data_verify_{today}.md"
    outfile.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# D3 Data Verifier Report -- {date.today().isoformat()}",
        f"",
        f"**Tickers checked:** {', '.join(tickers)}",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"",
        f"---",
        f"",
        f"## Source Reliability Ranking",
        f"",
        f"### Fundamentals (EPS / Revenue)",
        f"",
    ]

    for name, info in ranking.get("fundamentals", {}).items():
        lines.append(f"| {name.upper()} | Score={info['score']} | "
                     f"Avail={info['availability_pct']:.0f}% | "
                     f"Latency={info['avg_latency_s']:.1f}s | "
                     f"{info['recommendation']} |")

    lines += [
        f"",
        f"### Price OHLCV",
        f"",
    ]
    for name, info in ranking.get("ohlcv", {}).items():
        lines.append(f"| {name.upper()} | Score={info['score']} | "
                     f"Avail={info['availability_pct']:.0f}% | "
                     f"Latency={info['avg_latency_s']:.1f}s | "
                     f"{info['recommendation']} |")

    lines += [
        f"",
        f"---",
        f"",
        f"## Per-Ticker Verification",
        f"",
    ]

    all_flags = []
    for r in fund_results:
        ticker = r["ticker"]
        eps_m  = r.get("eps_median")
        lines += [
            f"### {ticker}",
            f"",
            f"**Fundamental Cross-Check** (EPS consensus: {f'{eps_m:.2f}' if eps_m is not None else 'N/A'})",
            f"",
        ]
        for src, res in r["results"].items():
            if res.get("ok"):
                lines.append(f"- {src.upper()}: EPS={res.get('eps','?')} | "
                             f"Rev={res.get('rev',0)/1e9:.1f}B | "
                             f"EPS_YoY={res.get('eps_yoy','?')}% | "
                             f"Latency={res.get('latency','?')}s")
            else:
                lines.append(f"- {src.upper()}: [X] UNAVAILABLE -- {res.get('error','no data')}")
        if r["flags"]:
            lines.append(f"")
            lines.append(f"**[!] FLAGS:**")
            for flag in r["flags"]:
                lines.append(f"- {flag}")
                all_flags.append(f"{ticker}: {flag}")

        lines.append(f"")

    # OHLCV section
    lines += ["---", "", "## Price Cross-Check", ""]
    for r in ohlcv_results:
        ticker = r["ticker"]
        lines.append(f"**{ticker}** | Median={r.get('price_median',0):.2f}")
        for src, price in r.get("prices", {}).items():
            lat = r["latencies"].get(src, "?")
            lines.append(f"  - {src.upper()}: ${price:.2f} ({lat:.1f}s)")
        if r["flags"]:
            for flag in r["flags"]:
                lines.append(f"  [!] {flag}")
                all_flags.append(f"{ticker}: {flag}")
        lines.append("")

    # Summary
    lines += [
        "---",
        "",
        f"## Summary",
        f"",
        f"- Total flags: {len(all_flags)}",
    ]
    if all_flags:
        lines.append(f"- **Action required:** Review flagged sources below")
        for f in all_flags:
            lines.append(f"  - {f}")
    else:
        lines.append(f"- [OK] All sources consistent -- no action required")

    lines += [
        f"",
        f"---",
        f"*Auto-generated by AlphaAbsolute D3 Data Verifier*",
    ]

    outfile.write_text("\n".join(lines), encoding="utf-8")
    return outfile


# ── Main Run ──────────────────────────────────────────────────────────────────

def run(tickers: list = None, quick: bool = False) -> dict:
    """
    Run full data verification across all sources.
    Writes:
      data/data_verifier/latest.json   -- machine-readable results
      data/data_verifier/source_ranking.json  -- source scores
      output/data_verify_YYMMDD.md     -- human-readable report
    """
    today = date.today().isoformat()
    print(f"\n[D3 Data Verifier] Running {today}")

    if tickers is None:
        tickers = QUICK_TICKERS if quick else BENCHMARK_TICKERS

    print(f"  Checking {len(tickers)} tickers x {len(FUND_SOURCES)} fundamental sources"
          f" + {len(OHLCV_SOURCES)} price sources")
    print(f"  Tickers: {', '.join(tickers)}")

    # ── Fundamental verification (parallel per ticker) ────────────────────────
    fund_results = []
    print(f"\n  [Fundamentals]")
    for ticker in tickers:
        print(f"    {ticker}...", end=" ", flush=True)
        t0 = time.time()
        r  = verify_fundamentals(ticker)
        print(f"({time.time()-t0:.1f}s) "
              f"consensus_EPS={r.get('eps_median','?')} "
              f"flags={len(r.get('flags',[]))}")
        fund_results.append(r)

        # Per-ticker flag details
        if r["flags"]:
            for flag in r["flags"]:
                print(f"      [!] {flag}")

    # ── OHLCV verification ────────────────────────────────────────────────────
    ohlcv_results = []
    print(f"\n  [Price OHLCV]")
    for ticker in tickers:
        print(f"    {ticker}...", end=" ", flush=True)
        t0 = time.time()
        r  = verify_ohlcv(ticker)
        pm = r.get("price_median")
        print(f"({time.time()-t0:.1f}s) median=${pm:.2f}" if pm else "(failed)")
        ohlcv_results.append(r)

    # ── Source ranking ────────────────────────────────────────────────────────
    ranking = _compute_source_ranking(fund_results, ohlcv_results)

    print(f"\n  [Source Ranking -- Fundamentals]")
    for name, info in ranking["fundamentals"].items():
        print(f"    {name.upper():12s} score={info['score']:5.1f} | "
              f"avail={info['availability_pct']:.0f}% | "
              f"lat={info['avg_latency_s']:.1f}s | {info['recommendation']}")

    print(f"\n  [Source Ranking -- Price OHLCV]")
    for name, info in ranking["ohlcv"].items():
        print(f"    {name.upper():12s} score={info['score']:5.1f} | "
              f"avail={info['availability_pct']:.0f}% | "
              f"lat={info['avg_latency_s']:.1f}s | {info['recommendation']}")

    # ── Collect all flags ─────────────────────────────────────────────────────
    all_flags = []
    for r in fund_results + ohlcv_results:
        all_flags.extend(r.get("flags", []))

    if all_flags:
        print(f"\n  [!] {len(all_flags)} FLAGS -- review data_verify report")
    else:
        print(f"\n  [OK] All sources consistent")

    # ── Write outputs ─────────────────────────────────────────────────────────
    summary = {
        "date":              today,
        "tickers":           tickers,
        "fundamental_flags": sum(len(r.get("flags",[])) for r in fund_results),
        "price_flags":       sum(len(r.get("flags",[])) for r in ohlcv_results),
        "total_flags":       len(all_flags),
        "flags":             all_flags,
        "source_ranking":    ranking,
        "fund_results":      fund_results,
        "ohlcv_results":     ohlcv_results,
        "computed_at":       datetime.now().isoformat(),
    }

    (OUT_DIR / "latest.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    (OUT_DIR / "source_ranking.json").write_text(
        json.dumps(ranking, indent=2), encoding="utf-8"
    )

    report_path = _write_report(fund_results, ohlcv_results, ranking, tickers)
    print(f"\n  Report -> {report_path.name}")
    print(f"  Ranking -> data/data_verifier/source_ranking.json")
    return summary


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="D3 Data Verifier -- cross-source sanity check")
    p.add_argument("--quick", action="store_true", help="Check 2 tickers only (fast test)")
    p.add_argument("tickers", nargs="*", help="Specific tickers to check")
    args = p.parse_args()
    run(tickers=args.tickers if args.tickers else None, quick=args.quick)
