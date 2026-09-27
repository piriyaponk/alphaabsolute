"""
System Optimizer — searches for parameter combinations that beat System 4.0.

Objective: maximize AlphaScore = 0.35×CAGR + 0.35×(Sharpe×10) - 0.20×MaxDD + 0.10×Alpha_vs_QQQ

Search space (S4 parameter variants):
  - rs_weights        : 4 lookback weights [20d, 60d, 126d, 252d]
  - vol_lookback_days : volatility window for position sizing
  - skip_days         : skip-period to avoid short-term reversal
  - top_n             : number of stocks to hold
  - sizing_method     : vol_parity | equal_weight
  - regime_ticker     : IWM | SPY | none
  - rebalance_days    : monthly=21, bimonthly=42

Output:
  data/research/system_leaderboard.json  — all runs ranked by AlphaScore
  data/research/champion.json            — current best config
  Obsidian: 05_Quant/Optimizer_Results_YYYY-MM-DD.md
  Telegram: summary + new champion alert

Strategy: exhaustive grid on small space (~200 combos), then focus on top regions.
Runs: Sunday pipeline after hypothesis_tester
"""

from __future__ import annotations
import json
import math
import sqlite3
import logging
import itertools
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "research"
DATA_DIR.mkdir(parents=True, exist_ok=True)
OHLCV_DB = ROOT / "data" / "ohlcv.db"
LEADERBOARD_FILE = DATA_DIR / "system_leaderboard.json"
CHAMPION_FILE = DATA_DIR / "champion.json"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# Search space
# ---------------------------------------------------------------------------
SEARCH_SPACE = {
    # RS weights must sum to 1.0 — defined as (w20, w60, w126, w252) normalized
    "rs_weight_profile": [
        "balanced",       # [0.10, 0.20, 0.30, 0.40]  — S4 baseline
        "long_only",      # [0.05, 0.10, 0.35, 0.50]  — heavy 252d
        "short_boost",    # [0.20, 0.30, 0.30, 0.20]  — more 20d/60d
        "medium_focus",   # [0.05, 0.25, 0.45, 0.25]  — heavy 126d
    ],
    "vol_lookback_days": [20, 42, 63],
    "skip_days":         [0, 10, 21],
    "top_n":             [10, 15, 20],
    "sizing_method":     ["vol_parity", "equal_weight"],
    "regime_ticker":     ["IWM", "SPY", "none"],
    "rebalance_days":    [21, 42],
}

RS_WEIGHT_PROFILES = {
    "balanced":     [0.10, 0.20, 0.30, 0.40],
    "long_only":    [0.05, 0.10, 0.35, 0.50],
    "short_boost":  [0.20, 0.30, 0.30, 0.20],
    "medium_focus": [0.05, 0.25, 0.45, 0.25],
}

S4_BASELINE_PARAMS = {
    "rs_weight_profile": "balanced",
    "rs_weights": [0.10, 0.20, 0.30, 0.40],
    "rs_lookbacks": [20, 60, 126, 252],
    "vol_lookback_days": 20,
    "skip_days": 0,
    "top_n": 15,
    "sizing_method": "vol_parity",
    "regime_ticker": "IWM",
    "rebalance_days": 21,
}


# ---------------------------------------------------------------------------
# Data layer (reused from hypothesis_tester)
# ---------------------------------------------------------------------------

def _get_prices(tickers: list[str], start: str, end: str) -> dict[str, dict[str, float]]:
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
        log.warning(f"DB: {e}")
    return result


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


def _cagr(nav_series: list[float], n_days: int) -> float:
    n_years = n_days / 252
    if n_years <= 0 or nav_series[0] <= 0:
        return 0.0
    return ((nav_series[-1] / nav_series[0]) ** (1 / n_years) - 1) * 100


# ---------------------------------------------------------------------------
# Core backtest engine
# ---------------------------------------------------------------------------

# Cache prices across all runs to avoid re-querying
_price_cache: dict[str, dict[str, float]] = {}
_universe_cache: list[str] = []
_spy_prices: dict[str, float] = {}
_qqq_prices: dict[str, float] = {}
_all_dates: list[str] = []


def _init_cache(start: str = "2024-01-01") -> bool:
    global _price_cache, _universe_cache, _spy_prices, _qqq_prices, _all_dates

    rs_file = ROOT / "data" / "rs_universe" / "latest.json"
    if not rs_file.exists():
        log.error("No RS universe file")
        return False

    rs_data = json.loads(rs_file.read_text(encoding="utf-8"))
    universe_raw = rs_data.get("universe", {})
    if isinstance(universe_raw, dict):
        _universe_cache = list(universe_raw.keys())[:300]
    elif isinstance(universe_raw, list):
        _universe_cache = [r["ticker"] for r in universe_raw if "ticker" in r][:300]

    if not _universe_cache:
        log.error("Empty universe")
        return False

    log.info(f"Loading prices for {len(_universe_cache)} tickers + SPY/QQQ/IWM...")
    all_tickers = list(set(_universe_cache + ["SPY", "QQQ", "IWM"]))
    _price_cache = _get_prices(all_tickers, start, TODAY)

    _spy_prices = _price_cache.get("SPY", {})
    _qqq_prices = _price_cache.get("QQQ", {})
    _all_dates = sorted(_spy_prices.keys())

    log.info(f"Price cache ready: {len(_all_dates)} trading days, {len([t for t in _universe_cache if _price_cache.get(t)])} tickers with data")
    return len(_all_dates) > 100


def _backtest(params: dict, start_idx_offset: int = 252) -> dict[str, Any]:
    rs_weights = RS_WEIGHT_PROFILES[params.get("rs_weight_profile", "balanced")]
    rs_lbs = [20, 60, 126, 252]
    vol_lb = params["vol_lookback_days"]
    skip = params["skip_days"]
    top_n = params["top_n"]
    sizing = params["sizing_method"]
    regime_ticker = params["regime_ticker"]
    rebal_days = params["rebalance_days"]

    if len(_all_dates) < start_idx_offset + rebal_days + 10:
        return {"error": "insufficient data", "n_rebalances": 0, "alpha_score": 0}

    regime_prices = _price_cache.get(regime_ticker, {}) if regime_ticker != "none" else {}

    nav = 1.0
    qqq_nav = 1.0
    nav_series = [nav]
    qqq_series = [qqq_nav]
    current_weights: dict[str, float] = {}
    last_rebal_idx = start_idx_offset
    n_rebalances = 0

    for idx in range(start_idx_offset, len(_all_dates)):
        dt = _all_dates[idx]
        prev_dt = _all_dates[idx - 1] if idx > 0 else None

        # Rebalance?
        if (idx - last_rebal_idx) >= rebal_days or not current_weights:

            # Regime gate
            in_bull = True
            if regime_ticker != "none" and regime_prices:
                rd = sorted(d for d in regime_prices if d <= dt)
                if len(rd) >= 200:
                    ma200 = sum(regime_prices[d] for d in rd[-200:]) / 200
                    in_bull = regime_prices[rd[-1]] > ma200

            if not in_bull:
                current_weights = {}
                last_rebal_idx = idx
                n_rebalances += 1
                continue

            # Score stocks by RS composite
            scores: dict[str, float] = {}
            available = [d for d in _all_dates if d <= dt]
            eff_end = len(available) - 1 - skip

            spy_dates = sorted(d for d in _spy_prices if d <= dt)
            spy_eff = len(spy_dates) - 1 - skip

            if eff_end < max(rs_lbs) or spy_eff < max(rs_lbs):
                last_rebal_idx = idx
                n_rebalances += 1
                continue

            for ticker in _universe_cache:
                tp = _price_cache.get(ticker, {})
                td = sorted(d for d in tp if d <= dt)
                if len(td) < max(rs_lbs) + skip + 5:
                    continue

                t_eff = len(td) - 1 - skip
                if t_eff < max(rs_lbs):
                    continue

                composite = 0.0
                ok = True
                for lb, w in zip(rs_lbs, rs_weights):
                    if t_eff < lb or spy_eff < lb:
                        ok = False
                        break
                    t_ret = tp[td[t_eff]] / tp[td[t_eff - lb]] - 1
                    s_ret = _spy_prices[spy_dates[spy_eff]] / _spy_prices[spy_dates[spy_eff - lb]] - 1
                    composite += w * (t_ret - s_ret)
                if ok:
                    scores[ticker] = composite

            if not scores:
                last_rebal_idx = idx
                n_rebalances += 1
                continue

            top = sorted(scores, key=lambda x: scores[x], reverse=True)[:top_n]

            # Sizing
            if sizing == "vol_parity":
                vols = {}
                for t in top:
                    tp = _price_cache.get(t, {})
                    td = sorted(d for d in tp if d <= dt)
                    if len(td) < vol_lb + 2:
                        vols[t] = 0.02
                        continue
                    rets = [tp[td[i]] / tp[td[i - 1]] - 1 for i in range(max(1, len(td) - vol_lb), len(td))]
                    mean_r = sum(rets) / len(rets)
                    var = sum((r - mean_r) ** 2 for r in rets) / len(rets)
                    vols[t] = math.sqrt(var * 252) if var > 0 else 0.02
                inv = {t: 1.0 / max(vols[t], 0.005) for t in top}
                total = sum(inv.values())
                current_weights = {t: inv[t] / total for t in top}
            else:
                w = 1.0 / len(top)
                current_weights = {t: w for t in top}

            last_rebal_idx = idx
            n_rebalances += 1

        # Daily P&L
        if prev_dt and current_weights:
            daily = 0.0
            for t, w in current_weights.items():
                tp = _price_cache.get(t, {})
                if dt in tp and prev_dt in tp and tp[prev_dt] > 0:
                    daily += w * (tp[dt] / tp[prev_dt] - 1)
            nav *= (1 + daily)

        # QQQ benchmark
        if prev_dt and dt in _qqq_prices and prev_dt in _qqq_prices and _qqq_prices[prev_dt] > 0:
            qqq_nav *= (1 + _qqq_prices[dt] / _qqq_prices[prev_dt] - 1)

        nav_series.append(nav)
        qqq_series.append(qqq_nav)

    n_days = len(_all_dates) - start_idx_offset
    cagr = _cagr(nav_series, n_days)
    sharpe = _sharpe(nav_series)
    max_dd = _max_drawdown(nav_series)
    qqq_cagr = _cagr(qqq_series, n_days)
    alpha = cagr - qqq_cagr

    score = (0.35 * cagr) + (0.35 * sharpe * 10) - (0.20 * max_dd) + (0.10 * alpha)

    return {
        "cagr_pct": round(cagr, 2),
        "sharpe": round(sharpe, 3),
        "max_dd_pct": round(max_dd, 2),
        "alpha_vs_qqq_pct": round(alpha, 2),
        "alpha_score": round(score, 2),
        "n_rebalances": n_rebalances,
        "qqq_cagr_pct": round(qqq_cagr, 2),
    }


# ---------------------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------------------

def _combos_from_agent_ideas(tested_ids: set) -> list[dict]:
    """Convert agent_ideas.json pending ideas into S4 param dicts for testing."""
    ideas_file = DATA_DIR / "agent_ideas.json"
    if not ideas_file.exists():
        return []

    ideas = json.loads(ideas_file.read_text(encoding="utf-8"))
    combos = []
    for idea in ideas:
        if idea["status"] != "pending":
            continue
        param = idea.get("param")
        if not param:
            continue

        # Build S4 variant from the idea's param change
        variant = dict(S4_BASELINE_PARAMS)
        key = param["param"]
        val = param["value"]

        # Map param name to search space key
        if key in ("vol_lookback_days", "skip_days", "top_n", "sizing_method", "regime_ticker", "rebalance_days"):
            variant[key] = val
        elif key == "rs_weight_profile":
            if val in RS_WEIGHT_PROFILES:
                variant["rs_weight_profile"] = val
                variant["rs_weights"] = RS_WEIGHT_PROFILES[val]
            else:
                continue
        else:
            continue

        cid = _combo_id(variant)
        if cid not in tested_ids:
            variant["_idea_id"] = idea["idea_id"]
            variant["_idea_agent"] = idea["agent"]
            combos.append(variant)

    return combos


def _all_combos() -> list[dict]:
    keys = list(SEARCH_SPACE.keys())
    combos = []
    for vals in itertools.product(*[SEARCH_SPACE[k] for k in keys]):
        combos.append(dict(zip(keys, vals)))
    return combos


def _combo_id(params: dict) -> str:
    import hashlib
    s = json.dumps({k: params[k] for k in sorted(params)}, sort_keys=True)
    return hashlib.md5(s.encode()).hexdigest()[:10]


def _load_leaderboard() -> list[dict]:
    if LEADERBOARD_FILE.exists():
        return json.loads(LEADERBOARD_FILE.read_text(encoding="utf-8"))
    return []


def _save_leaderboard(board: list[dict]) -> None:
    board_sorted = sorted(board, key=lambda x: x.get("alpha_score", 0), reverse=True)[:100]
    LEADERBOARD_FILE.write_text(json.dumps(board_sorted, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(max_combos: int = 50) -> dict:
    log.info("=== System Optimizer START ===")

    if not _init_cache():
        return {"error": "cache init failed"}

    # Load already-tested combos
    leaderboard = _load_leaderboard()
    tested_ids = {entry["combo_id"] for entry in leaderboard}

    # Run baseline first if not tested
    baseline_params = S4_BASELINE_PARAMS.copy()
    baseline_id = _combo_id(baseline_params)
    baseline_result = None

    if baseline_id not in tested_ids:
        log.info("Running S4 baseline...")
        res = _backtest(baseline_params)
        if "error" not in res:
            entry = {"combo_id": baseline_id, "params": baseline_params, "is_baseline": True, "tested_date": TODAY, **res}
            leaderboard.append(entry)
            tested_ids.add(baseline_id)
            baseline_result = res
            log.info(f"Baseline: CAGR={res['cagr_pct']}% Sharpe={res['sharpe']} DD={res['max_dd_pct']}% AlphaScore={res['alpha_score']}")

    if baseline_result is None:
        baseline_entry = next((e for e in leaderboard if e.get("is_baseline")), None)
        baseline_result = baseline_entry if baseline_entry else {"cagr_pct": 0, "sharpe": 0, "max_dd_pct": 100, "alpha_vs_qqq_pct": 0, "alpha_score": 0}

    baseline_score = baseline_result.get("alpha_score", 0)

    # Pull agent ideas first — they get priority in the test queue
    agent_idea_combos = _combos_from_agent_ideas(tested_ids)
    log.info(f"Agent ideas ready to test: {len(agent_idea_combos)}")

    # Generate grid combos
    all_combos = _all_combos()
    untested_grid = [c for c in all_combos if _combo_id(c) not in tested_ids]
    log.info(f"Grid combos: {len(all_combos)} total | Untested: {len(untested_grid)}")

    # Agent ideas first, then grid fill
    untested = agent_idea_combos + [c for c in untested_grid if _combo_id(c) not in {_combo_id(x) for x in agent_idea_combos}]

    # Run up to max_combos
    batch = untested[:max_combos]
    new_champions = []
    tested_this_run = 0

    for params in batch:
        cid = _combo_id(params)
        res = _backtest(params)
        tested_this_run += 1

        if "error" in res or res.get("n_rebalances", 0) < 3:
            continue

        beats = res["alpha_score"] > baseline_score + 0.5
        entry = {
            "combo_id": cid,
            "params": params,
            "is_baseline": False,
            "beats_baseline": beats,
            "tested_date": TODAY,
            **res,
        }
        leaderboard.append(entry)
        tested_ids.add(cid)

        log.info(
            f"[{tested_this_run}/{len(batch)}] "
            f"rs={params['rs_weight_profile']} vol={params['vol_lookback_days']}d "
            f"skip={params['skip_days']} top={params['top_n']} "
            f"regime={params['regime_ticker']} rebal={params['rebalance_days']}d "
            f"→ Score={res['alpha_score']} CAGR={res['cagr_pct']}% "
            f"{'★ BEATS BASELINE' if beats else ''}"
        )

        if beats:
            new_champions.append(entry)

        # Record KPI for agent ideas
        idea_id = params.get("_idea_id")
        if idea_id:
            try:
                import sys as _sys
                if str(ROOT) not in _sys.path:
                    _sys.path.insert(0, str(ROOT))
                from scripts.brain.agent_ideas import record_result as _record
                _record(idea_id, {**res, "verdict": "PASS" if beats else "FAIL"}, baseline_score)
            except Exception as _e:
                log.warning(f"KPI record: {_e}")

    _save_leaderboard(leaderboard)

    # Update champion
    board_sorted = sorted(leaderboard, key=lambda x: x.get("alpha_score", 0), reverse=True)
    if board_sorted:
        champion = board_sorted[0]
        # NEVER auto-deploy — CIO must approve before any config change goes live
        champion["deployment_status"] = "pending_cio_approval"
        champion["deploy_note"] = "Review required. Run: python scripts/brain/system_optimizer.py --approve to see options."
        CHAMPION_FILE.write_text(json.dumps(champion, indent=2, ensure_ascii=False), encoding="utf-8")

    remaining = len(untested) - len(batch)
    summary = {
        "date": TODAY,
        "tested_this_run": tested_this_run,
        "total_tested": len(leaderboard),
        "remaining": remaining,
        "baseline_score": round(baseline_score, 2),
        "new_beats_baseline": len(new_champions),
        "current_champion": board_sorted[0] if board_sorted else None,
        "top5": [
            {
                "rank": i + 1,
                "alpha_score": e["alpha_score"],
                "cagr_pct": e["cagr_pct"],
                "sharpe": e["sharpe"],
                "max_dd_pct": e["max_dd_pct"],
                "alpha_vs_qqq_pct": e.get("alpha_vs_qqq_pct", 0),
                "params_summary": f"rs={e['params']['rs_weight_profile']} vol={e['params']['vol_lookback_days']}d skip={e['params']['skip_days']} top={e['params']['top_n']} {e['params']['regime_ticker']} {e['params']['rebalance_days']}d {e['params']['sizing_method']}",
                "is_baseline": e.get("is_baseline", False),
                "beats_s4": not e.get("is_baseline", False) and e["alpha_score"] > baseline_score,
            }
            for i, e in enumerate(board_sorted[:5])
        ],
    }

    _write_obsidian(summary)
    # Telegram push — only when a config beats the S4 baseline
    if summary["new_beats_baseline"] > 0:
        _send_telegram(summary)

    log.info(f"=== Optimizer DONE === tested={tested_this_run} new_champions={len(new_champions)} remaining={remaining}")
    return summary


def _write_obsidian(summary: dict) -> None:
    try:
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from scripts.brain.obsidian_writer_v2 import write as obs_write
        from scripts.brain.alpha_score import label

        champ = summary.get("current_champion", {})
        champ_params = champ.get("params", {}) if champ else {}

        lines = [
            f"---",
            f"type: optimizer_results",
            f"date: {TODAY}",
            f"tags: [optimizer, S4, backtest, auto]",
            f"---",
            f"",
            f"# System Optimizer Results — {TODAY}",
            f"",
            f"## Run Summary",
            f"- Tested this run: **{summary['tested_this_run']}** configs",
            f"- Total tested ever: **{summary['total_tested']}**",
            f"- Remaining in search space: **{summary['remaining']}**",
            f"- New configs beating S4 baseline: **{summary['new_beats_baseline']}**",
            f"",
            f"## S4 Baseline Score: {summary['baseline_score']}",
            f"",
            f"## Leaderboard Top 5",
            f"",
            f"| Rank | AlphaScore | CAGR% | Sharpe | MaxDD% | Alpha vs QQQ | Config |",
            f"|------|-----------|-------|--------|--------|-------------|--------|",
        ]

        for r in summary["top5"]:
            badge = "🏆 S4+" if r["beats_s4"] else ("📌 baseline" if r["is_baseline"] else "")
            lines.append(
                f"| {r['rank']} | **{r['alpha_score']}** {badge} | {r['cagr_pct']}% | {r['sharpe']} | {r['max_dd_pct']}% | {r['alpha_vs_qqq_pct']}% | {r['params_summary']} |"
            )

        lines += [
            f"",
            f"## Current Champion",
            f"```json",
            json.dumps(champ_params, indent=2, ensure_ascii=False),
            f"```",
            f"",
            f"**AlphaScore: {champ.get('alpha_score', 'N/A')} — {label(champ.get('alpha_score', 0))}**",
            f"",
            f"## Links",
            f"[[05_Quant/Hypothesis_Results_{TODAY}]]",
            f"[[15_Research_Library/151_Trading_Strategies]]",
        ]

        obs_write(f"05_Quant/Optimizer_Results_{TODAY}.md", "\n".join(lines))
    except Exception as e:
        log.warning(f"Obsidian: {e}")


def _send_telegram(summary: dict) -> None:
    import os
    import requests
    bot = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not bot or not chat:
        return

    champ = summary.get("current_champion", {})
    lines = [
        f"🔬 SYSTEM OPTIMIZER [{summary['date']}]",
        f"Tested: {summary['tested_this_run']} | Total: {summary['total_tested']} | Left: {summary['remaining']}",
        f"Baseline AlphaScore: {summary['baseline_score']}",
        f"New configs beating S4: {summary['new_beats_baseline']}",
        "",
        "TOP 5:",
    ]
    for r in summary["top5"]:
        badge = " ★" if r["beats_s4"] else (" [S4]" if r["is_baseline"] else "")
        lines.append(f"#{r['rank']} Score={r['alpha_score']} CAGR={r['cagr_pct']}% DD={r['max_dd_pct']}%{badge}")
        lines.append(f"   {r['params_summary']}")

    if summary["new_beats_baseline"] > 0:
        lines += [
            "",
            f"★ NEW CONFIG FOUND: Score={champ.get('alpha_score')} CAGR={champ.get('cagr_pct')}%",
            "⚠️ NOT DEPLOYED — ต้องขออนุมัติ CIO ก่อน",
            "→ ดู data/research/champion.json แล้วแจ้งถ้าต้องการ review",
        ]

    try:
        requests.post(
            f"https://api.telegram.org/bot{bot}/sendMessage",
            json={"chat_id": chat, "text": "\n".join(lines)},
            timeout=10,
            verify=False,
        )
    except Exception as e:
        log.warning(f"Telegram: {e}")


if __name__ == "__main__":
    import sys
    max_c = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    result = run(max_combos=max_c)
    print(json.dumps({k: v for k, v in result.items() if k != "current_champion"}, indent=2, ensure_ascii=False))
    if result.get("current_champion"):
        champ = result["current_champion"]
        print(f"\nCHAMPION AlphaScore={champ.get('alpha_score')} CAGR={champ.get('cagr_pct')}% Sharpe={champ.get('sharpe')} DD={champ.get('max_dd_pct')}%")
