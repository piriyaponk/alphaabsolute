"""
Hypothesis Tester — backtests pending S4 variants from signal_extractor output.

Reads:  data/research/pending_hypotheses.json  (status=pending, param!=None)
Runs:   S4 variant backtest for each hypothesis
Writes: data/research/backtest_results.json
        Obsidian note: 05_Quant/Hypothesis_Results_YYYY-MM-DD.md
        If passes threshold → opens BoA agenda item automatically

Pass threshold (must meet ALL):
  - alpha_vs_baseline > +0.5% annualized
  - sharpe_ratio > baseline_sharpe + 0.05
  - max_drawdown < baseline_dd + 2%   (not worse on drawdown)
  - N_rebalances >= 6  (minimum observations)

Runs: Sunday pipeline (after signal_extractor)
"""

from __future__ import annotations
import json
import logging
import sqlite3
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "research"
OHLCV_DB = ROOT / "data" / "ohlcv.db"
STATE_FILE = ROOT / "data" / "paper_trading" / "state.json"
HYPOTHESES_FILE = DATA_DIR / "pending_hypotheses.json"
RESULTS_FILE = DATA_DIR / "backtest_results.json"
BOA_AGENDA = ROOT / "data" / "board" / "agenda.json"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

TODAY = date.today().isoformat()

# S4 baseline parameters
S4_BASELINE = {
    "vol_lookback_days": 20,
    "skip_days": 0,
    "sizing_method": "vol_parity",
    "rs_weights": [0.10, 0.20, 0.30, 0.40],
    "rs_lookbacks": [20, 60, 126, 252],
    "top_n": 15,
    "rebalance_days": 21,
    "regime_ticker": "IWM",
}

PASS_THRESHOLD = {
    "min_alpha_pct": 0.5,       # annualized alpha vs baseline
    "min_sharpe_improvement": 0.05,
    "max_dd_worsening_pct": 2.0,
    "min_rebalances": 6,
}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _get_prices(tickers: list[str], start: str, end: str) -> dict[str, dict[str, float]]:
    """Returns {ticker: {date_str: close}}."""
    if not OHLCV_DB.exists():
        return {}
    result: dict[str, dict[str, float]] = {t: {} for t in tickers}
    try:
        conn = sqlite3.connect(str(OHLCV_DB))
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, date, close FROM ohlcv WHERE ticker IN ({placeholders}) AND date BETWEEN ? AND ? ORDER BY date",
            tickers + [start, end],
        ).fetchall()
        conn.close()
        for ticker, dt, close in rows:
            if close is not None:
                result[ticker][dt] = float(close)
    except Exception as e:
        log.warning(f"DB error: {e}")
    return result


def _daily_returns(prices: dict[str, float]) -> dict[str, float]:
    sorted_dates = sorted(prices)
    rets = {}
    for i in range(1, len(sorted_dates)):
        d0, d1 = sorted_dates[i - 1], sorted_dates[i]
        if prices[d0] > 0:
            rets[d1] = (prices[d1] - prices[d0]) / prices[d0]
    return rets


def _annualized_return(nav_series: list[float], n_years: float) -> float:
    if n_years <= 0 or nav_series[0] <= 0:
        return 0.0
    return (nav_series[-1] / nav_series[0]) ** (1 / n_years) - 1


def _sharpe(nav_series: list[float]) -> float:
    if len(nav_series) < 3:
        return 0.0
    rets = [(nav_series[i] / nav_series[i - 1] - 1) for i in range(1, len(nav_series))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    std = math.sqrt(var) if var > 0 else 1e-9
    return (mean / std) * math.sqrt(252)


def _max_drawdown(nav_series: list[float]) -> float:
    peak = nav_series[0]
    max_dd = 0.0
    for v in nav_series:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd * 100


# ---------------------------------------------------------------------------
# Simplified S4 backtest engine
# ---------------------------------------------------------------------------

def _run_s4_variant(params: dict, start: str = "2025-01-01") -> dict[str, Any]:
    """
    Simplified monthly cross-sectional momentum backtest.
    Uses actual OHLCV from DB. Returns performance metrics.
    """
    end = TODAY
    vol_lb = params.get("vol_lookback_days", 20)
    skip = params.get("skip_days", 0)
    rs_weights = params.get("rs_weights", [0.10, 0.20, 0.30, 0.40])
    rs_lbs = params.get("rs_lookbacks", [20, 60, 126, 252])
    top_n = params.get("top_n", 15)
    rebal_days = params.get("rebalance_days", 21)
    regime_ticker = params.get("regime_ticker", "IWM")

    # Get S&P500 + Nasdaq tickers from latest RS file
    rs_file = ROOT / "data" / "rs_universe" / "latest.json"
    if not rs_file.exists():
        return {"error": "no rs universe", "n_rebalances": 0}

    rs_data = json.loads(rs_file.read_text(encoding="utf-8"))
    # RS file format: {"universe": {ticker: {...}}, "top_10_leaders": [...], ...}
    if isinstance(rs_data, dict) and "universe" in rs_data:
        universe_dict = rs_data["universe"]
        if isinstance(universe_dict, dict):
            universe = list(universe_dict.keys())[:200]
        elif isinstance(universe_dict, list):
            universe = [r["ticker"] for r in universe_dict if isinstance(r, dict) and "ticker" in r][:200]
        else:
            universe = []
    elif isinstance(rs_data, list):
        universe = [r["ticker"] for r in rs_data if isinstance(r, dict) and "ticker" in r][:200]
    elif isinstance(rs_data, dict):
        # flat dict keyed by ticker
        universe = [k for k in rs_data.keys() if k not in ("date", "computed_at", "rs_source", "index", "rs_formula", "rs_source_note", "benchmark_date", "benchmark_size", "universe_count", "inflection_count", "top_10_leaders", "rs_laggards")][:200]
    else:
        return {"error": "bad rs format", "n_rebalances": 0}

    if not universe:
        return {"error": "empty universe", "n_rebalances": 0}

    # Fetch prices — include regime ticker
    all_tickers = list(set(universe + [regime_ticker, "SPY"]))
    prices = _get_prices(all_tickers, start, end)

    spy_prices = prices.get("SPY", {})
    regime_prices = prices.get(regime_ticker, {})
    if not spy_prices:
        return {"error": "no SPY data", "n_rebalances": 0}

    all_dates = sorted(spy_prices.keys())
    if len(all_dates) < max(rs_lbs) + rebal_days:
        return {"error": "insufficient history", "n_rebalances": 0}

    # Simulate monthly rebalances
    nav = 1.0
    nav_series = [nav]
    n_rebalances = 0
    current_weights: dict[str, float] = {}
    last_rebal_idx = max(rs_lbs)

    for idx in range(last_rebal_idx, len(all_dates)):
        dt = all_dates[idx]

        # Rebalance?
        if (idx - last_rebal_idx) >= rebal_days or not current_weights:
            # IWM regime gate: price > 200d MA
            regime_dates = sorted(regime_prices.keys())
            regime_in_bull = True
            if len(regime_dates) >= 200:
                recent_regime = {d: regime_prices[d] for d in regime_dates if d <= dt}
                if len(recent_regime) >= 200:
                    sorted_rd = sorted(recent_regime)
                    ma200 = sum(recent_regime[d] for d in sorted_rd[-200:]) / 200
                    regime_in_bull = recent_regime[sorted_rd[-1]] > ma200

            if not regime_in_bull:
                current_weights = {}  # go to cash
                last_rebal_idx = idx
                n_rebalances += 1
                continue

            # Score each stock by RS composite
            available_dates = [d for d in all_dates if d <= dt]
            scores: dict[str, float] = {}

            for ticker in universe:
                tp = prices.get(ticker, {})
                if not tp:
                    continue
                ticker_dates = sorted(d for d in tp if d <= dt)
                if len(ticker_dates) < max(rs_lbs) + skip + 5:
                    continue

                # Skip-period: exclude last `skip` days from return calculation
                effective_end_idx = len(ticker_dates) - 1 - skip
                if effective_end_idx < max(rs_lbs):
                    continue

                spy_ticker_dates = sorted(d for d in spy_prices if d <= dt)
                if len(spy_ticker_dates) < max(rs_lbs) + skip + 5:
                    continue

                spy_eff_end = len(spy_ticker_dates) - 1 - skip

                composite = 0.0
                valid = True
                for lb, w in zip(rs_lbs, rs_weights):
                    if effective_end_idx < lb or spy_eff_end < lb:
                        valid = False
                        break
                    t_ret = (tp[ticker_dates[effective_end_idx]] / tp[ticker_dates[effective_end_idx - lb]] - 1)
                    s_ret = (spy_prices[spy_ticker_dates[spy_eff_end]] / spy_prices[spy_ticker_dates[spy_eff_end - lb]] - 1)
                    composite += w * (t_ret - s_ret)

                if valid:
                    scores[ticker] = composite

            if not scores:
                current_weights = {}
                last_rebal_idx = idx
                n_rebalances += 1
                continue

            # Top N by RS composite
            top_tickers = sorted(scores, key=lambda x: scores[x], reverse=True)[:top_n]

            # Vol-parity sizing
            if params.get("sizing_method", "vol_parity") == "vol_parity":
                vols: dict[str, float] = {}
                for ticker in top_tickers:
                    tp = prices.get(ticker, {})
                    td = sorted(d for d in tp if d <= dt)
                    if len(td) < vol_lb + 2:
                        vols[ticker] = 0.02  # default
                        continue
                    rets = [(tp[td[i]] / tp[td[i - 1]] - 1) for i in range(max(1, len(td) - vol_lb), len(td))]
                    if rets:
                        mean_r = sum(rets) / len(rets)
                        var = sum((r - mean_r) ** 2 for r in rets) / len(rets)
                        vols[ticker] = math.sqrt(var * 252) if var > 0 else 0.02
                    else:
                        vols[ticker] = 0.02

                inv_vols = {t: 1.0 / max(vols[t], 0.01) for t in top_tickers}
                total_inv = sum(inv_vols.values())
                current_weights = {t: inv_vols[t] / total_inv for t in top_tickers} if total_inv > 0 else {}
            else:
                # Equal weight fallback
                w = 1.0 / len(top_tickers)
                current_weights = {t: w for t in top_tickers}

            last_rebal_idx = idx
            n_rebalances += 1

        # Daily P&L
        if idx > 0 and current_weights:
            prev_dt = all_dates[idx - 1]
            daily_ret = 0.0
            for ticker, w in current_weights.items():
                tp = prices.get(ticker, {})
                if dt in tp and prev_dt in tp and tp[prev_dt] > 0:
                    daily_ret += w * (tp[dt] / tp[prev_dt] - 1)
            nav *= (1 + daily_ret)
        nav_series.append(nav)

    n_years = len(all_dates) / 252
    ann_ret = _annualized_return(nav_series, n_years) * 100
    sharpe = _sharpe(nav_series)
    max_dd = _max_drawdown(nav_series)
    cagr = ann_ret

    return {
        "cagr_pct": round(cagr, 2),
        "sharpe": round(sharpe, 3),
        "max_dd_pct": round(max_dd, 2),
        "n_rebalances": n_rebalances,
        "n_days": len(all_dates),
        "final_nav": round(nav_series[-1], 4),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> dict:
    log.info("=== Hypothesis Tester START ===")

    if not HYPOTHESES_FILE.exists():
        log.warning("No hypotheses file — run signal_extractor first")
        return {"error": "no hypotheses file"}

    hypotheses = json.loads(HYPOTHESES_FILE.read_text(encoding="utf-8"))
    pending = [h for h in hypotheses if h["status"] == "pending" and h.get("param")]

    if not pending:
        log.info("No pending hypotheses with structured params")
        return {"tested": 0}

    log.info(f"Running baseline S4...")
    baseline = _run_s4_variant(S4_BASELINE)
    log.info(f"Baseline: CAGR={baseline.get('cagr_pct')}% Sharpe={baseline.get('sharpe')} DD={baseline.get('max_dd_pct')}%")

    results = []
    passed = []

    for hyp in pending[:5]:  # cap at 5 per run to avoid timeout
        param_def = hyp["param"]
        if not param_def:
            continue

        # Build variant params
        variant_params = dict(S4_BASELINE)
        variant_params[param_def["param"]] = param_def["value"]

        log.info(f"Testing: {hyp['hypothesis_text'][:60]}")
        log.info(f"  Param: {param_def['param']} = {param_def['value']} (baseline: {param_def.get('baseline')})")

        result = _run_s4_variant(variant_params)

        alpha = result.get("cagr_pct", 0) - baseline.get("cagr_pct", 0)
        sharpe_diff = result.get("sharpe", 0) - baseline.get("sharpe", 0)
        dd_diff = result.get("max_dd_pct", 0) - baseline.get("max_dd_pct", 0)
        n_rebal = result.get("n_rebalances", 0)

        passes = (
            alpha >= PASS_THRESHOLD["min_alpha_pct"]
            and sharpe_diff >= PASS_THRESHOLD["min_sharpe_improvement"]
            and dd_diff <= PASS_THRESHOLD["max_dd_worsening_pct"]
            and n_rebal >= PASS_THRESHOLD["min_rebalances"]
        )

        verdict = "PASS" if passes else "FAIL"
        log.info(f"  Result: CAGR={result.get('cagr_pct')}% Sharpe={result.get('sharpe')} DD={result.get('max_dd_pct')}% → {verdict}")

        backtest_result = {
            "baseline": baseline,
            "variant": result,
            "alpha_pct": round(alpha, 2),
            "sharpe_diff": round(sharpe_diff, 3),
            "dd_diff_pct": round(dd_diff, 2),
            "verdict": verdict,
            "tested_date": TODAY,
        }

        # Update hypothesis status
        for h in hypotheses:
            if h["id"] == hyp["id"]:
                h["status"] = "tested"
                h["backtest_result"] = backtest_result
                if passes:
                    h["boa_status"] = "pending_boa"

        results.append({
            "id": hyp["id"],
            "title": hyp["hypothesis_text"][:80],
            "param": param_def,
            **backtest_result,
        })

        if passes:
            passed.append(hyp)

    # Save updated hypotheses
    HYPOTHESES_FILE.write_text(json.dumps(hypotheses, indent=2, ensure_ascii=False), encoding="utf-8")

    # Save results
    existing_results = json.loads(RESULTS_FILE.read_text(encoding="utf-8")) if RESULTS_FILE.exists() else []
    RESULTS_FILE.write_text(
        json.dumps(existing_results + results, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # Auto-open BoA agenda for PASS hypotheses
    for hyp in passed:
        _open_boa_agenda(hyp)

    # Write Obsidian summary
    _write_obsidian_results(results, baseline)

    summary = {
        "date": TODAY,
        "tested": len(results),
        "passed": len(passed),
        "failed": len(results) - len(passed),
        "baseline_cagr": baseline.get("cagr_pct"),
        "baseline_sharpe": baseline.get("sharpe"),
    }

    # Telegram push — only when hypotheses were tested and at least one passed
    if summary["tested"] > 0:
        _send_telegram(summary, results)
    log.info("=== Hypothesis Tester DONE ===")
    return summary


def _open_boa_agenda(hyp: dict) -> None:
    BOA_AGENDA.parent.mkdir(parents=True, exist_ok=True)
    agenda = json.loads(BOA_AGENDA.read_text(encoding="utf-8")) if BOA_AGENDA.exists() else []

    # Get next BOA ID
    existing_ids = [item.get("boa_id", "BOA-000") for item in agenda]
    max_num = max((int(i.split("-")[1]) for i in existing_ids if "-" in i), default=0)
    new_id = f"BOA-{max_num + 1:03d}"

    agenda.append({
        "boa_id": new_id,
        "title": f"Auto: {hyp['hypothesis_text'][:80]}",
        "source": hyp.get("source_file", ""),
        "param": hyp.get("param"),
        "backtest_result": hyp.get("backtest_result"),
        "status": "PENDING_VOTE",
        "created": TODAY,
        "category": "signal_improvement",
    })

    BOA_AGENDA.write_text(json.dumps(agenda, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"BoA agenda opened: {new_id} — {hyp['hypothesis_text'][:60]}")


def _write_obsidian_results(results: list[dict], baseline: dict) -> None:
    try:
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from scripts.brain.obsidian_writer_v2 import write as obs_write

        lines = [
            f"---",
            f"type: backtest_results",
            f"date: {TODAY}",
            f"tags: [backtest, hypothesis, S4, auto]",
            f"---",
            f"",
            f"# Hypothesis Backtest Results — {TODAY}",
            f"",
            f"## Baseline S4",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| CAGR | {baseline.get('cagr_pct', 'N/A')}% |",
            f"| Sharpe | {baseline.get('sharpe', 'N/A')} |",
            f"| Max DD | {baseline.get('max_dd_pct', 'N/A')}% |",
            f"| Rebalances | {baseline.get('n_rebalances', 'N/A')} |",
            f"",
            f"## Tested Hypotheses",
            f"",
        ]

        for r in results:
            verdict_icon = "✅" if r["verdict"] == "PASS" else "❌"
            lines += [
                f"### {verdict_icon} {r['title']}",
                f"**Param:** `{r['param']['param']}` = `{r['param']['value']}` (baseline: `{r['param'].get('baseline')}`)",
                f"",
                f"| Metric | Baseline | Variant | Delta |",
                f"|--------|----------|---------|-------|",
                f"| CAGR | {r['baseline']['cagr_pct']}% | {r['variant'].get('cagr_pct', 'N/A')}% | {r['alpha_pct']:+.2f}% |",
                f"| Sharpe | {r['baseline']['sharpe']} | {r['variant'].get('sharpe', 'N/A')} | {r['sharpe_diff']:+.3f} |",
                f"| Max DD | {r['baseline']['max_dd_pct']}% | {r['variant'].get('max_dd_pct', 'N/A')}% | {r['dd_diff_pct']:+.2f}% |",
                f"",
                f"**Verdict: {r['verdict']}**",
                f"",
            ]
            if r["verdict"] == "PASS":
                lines.append(f"→ BoA agenda item opened automatically.")
                lines.append(f"")

        lines += [
            f"## Links",
            f"[[data/research/pending_hypotheses.json]]",
            f"[[05_Quant/S4_Signal_Validation]]",
        ]

        obs_write(f"05_Quant/Hypothesis_Results_{TODAY}.md", "\n".join(lines))
    except Exception as e:
        log.warning(f"Obsidian write: {e}")


def _send_telegram(summary: dict, results: list[dict]) -> None:
    import os
    import requests as req
    bot = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not bot or not chat:
        return

    lines = [
        f"🧪 HYPOTHESIS TESTER [{summary['date']}]",
        f"Tested: {summary['tested']} | PASS: {summary['passed']} | FAIL: {summary['failed']}",
        f"Baseline: CAGR={summary['baseline_cagr']}% Sharpe={summary['baseline_sharpe']}",
        "",
    ]
    for r in results[:4]:
        icon = "✅" if r["verdict"] == "PASS" else "❌"
        lines.append(f"{icon} {r['title'][:50]}")
        lines.append(f"   Alpha={r['alpha_pct']:+.2f}% Sharpe={r['sharpe_diff']:+.3f} DD={r['dd_diff_pct']:+.2f}%")

    if summary["passed"] > 0:
        lines.append("")
        lines.append(f"→ {summary['passed']} PASS → BoA agenda opened automatically")

    text = "\n".join(lines)
    try:
        req.post(
            f"https://api.telegram.org/bot{bot}/sendMessage",
            json={"chat_id": chat, "text": text},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram: {e}")


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
