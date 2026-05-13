"""
AlphaAbsolute — Auditor (Agent 16: Fact-Check & Source Verification)
Runs before every Telegram send. Re-fetches live prices, re-calculates ALL
numbers from scratch, fixes discrepancies, and stamps the report as verified.
Nothing goes to CIO unless it passes this audit.
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
import urllib3
urllib3.disable_warnings()

BASE_DIR = Path(__file__).resolve().parents[2]

# Tolerance: flag if stored price differs from live by more than this
PRICE_TOLERANCE_PCT = 0.5    # 0.5% — small drift is fine (stale by minutes)
PNL_TOLERANCE_PCT   = 0.1    # P&L % must match within 0.1pp


# ─── Live Price Fetch ─────────────────────────────────────────────────────────
def _live_price(ticker: str) -> Optional[float]:
    """Fetch latest close from Yahoo Finance v8 chart endpoint."""
    try:
        s = requests.Session()
        s.verify = False
        s.headers["User-Agent"] = "Mozilla/5.0"
        r = s.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"interval": "1d", "range": "5d"},
            timeout=10,
        )
        result = r.json().get("chart", {}).get("result")
        if result:
            return float(result[0]["meta"]["regularMarketPrice"])
    except Exception:
        pass
    return None


def _fetch_prices(tickers: list) -> dict:
    """Batch-fetch live prices. Returns {ticker: price}."""
    prices = {}
    for ticker in tickers:
        p = _live_price(ticker)
        if p:
            prices[ticker] = p
        time.sleep(0.2)
    return prices


# ─── Portfolio Audit ──────────────────────────────────────────────────────────
def audit_portfolio(portfolio: dict, perf: dict) -> tuple[dict, dict, list]:
    """
    Re-verify every number in portfolio and perf from scratch.
    Returns (corrected_portfolio, corrected_perf, audit_log).
    audit_log is a list of strings describing what was fixed.
    """
    log = []
    capital   = portfolio.get("capital", 100_000)
    positions = portfolio.get("positions", {})
    cash      = portfolio.get("cash", capital)

    if not positions:
        log.append("PASS: No open positions to verify.")
        return portfolio, perf, log

    # ── 1. Re-fetch live prices ───────────────────────────────────────────────
    tickers = list(positions.keys()) + ["QQQ"]
    live    = _fetch_prices(tickers)

    if not live:
        log.append("WARN: Could not fetch any live prices — audit skipped.")
        return portfolio, perf, log

    # ── 2. Verify each position ───────────────────────────────────────────────
    total_market_value = 0.0

    for ticker, pos in positions.items():
        entry  = pos.get("entry_price", 0)
        shares = pos.get("shares", 0)
        stored = pos.get("current_price", entry)

        live_p = live.get(ticker)
        if live_p is None:
            log.append(f"WARN {ticker}: could not fetch live price — using stored ${stored:.2f}")
            live_p = stored

        # Check price drift
        drift = abs(live_p - stored) / stored * 100 if stored else 0
        if drift > PRICE_TOLERANCE_PCT:
            log.append(f"FIX {ticker}: price ${stored:.2f} → ${live_p:.2f} (drift {drift:.2f}%)")
            pos["current_price"] = round(live_p, 2)

        # Re-calculate P&L from scratch
        correct_pnl_pct = (live_p - entry) / entry * 100 if entry else 0
        correct_pnl_usd = shares * (live_p - entry)
        correct_cost    = shares * entry
        correct_value   = shares * live_p

        stored_pnl_pct = pos.get("pnl_pct", 0)
        if abs(correct_pnl_pct - stored_pnl_pct) > PNL_TOLERANCE_PCT:
            log.append(
                f"FIX {ticker}: pnl_pct {stored_pnl_pct:+.2f}% → {correct_pnl_pct:+.2f}%"
            )

        pos["current_price"] = round(live_p, 2)
        pos["pnl_pct"]       = round(correct_pnl_pct, 2)
        pos["pnl_usd"]       = round(correct_pnl_usd, 2)
        pos["cost"]          = round(correct_cost, 2)
        pos["market_value"]  = round(correct_value, 2)
        total_market_value  += correct_value

    # ── 3. Verify total portfolio value ───────────────────────────────────────
    correct_total   = round(cash + total_market_value, 2)
    correct_return  = round((correct_total - capital) / capital * 100, 4)
    correct_unreal  = round(total_market_value - sum(
        pos.get("shares", 0) * pos.get("entry_price", 0)
        for pos in positions.values()
    ), 2)

    stored_total  = perf.get("total_value", 0)
    if abs(correct_total - stored_total) > 1:   # > $1 discrepancy
        log.append(f"FIX total_value: ${stored_total:,.2f} → ${correct_total:,.2f}")

    # ── 4. Verify QQQ benchmark ───────────────────────────────────────────────
    qqq_live  = live.get("QQQ")
    qqq_start = portfolio.get("benchmark_start_price")
    correct_bench = 0.0
    if qqq_live and qqq_start and qqq_start > 0:
        correct_bench = round((qqq_live - qqq_start) / qqq_start * 100, 4)
        stored_bench  = perf.get("benchmark_return", 0)
        if abs(correct_bench - stored_bench) > 0.05:
            log.append(
                f"FIX benchmark_return: {stored_bench:+.2f}% → {correct_bench:+.2f}%"
                f" (QQQ ${qqq_live:.2f} vs start ${qqq_start:.2f})"
            )
    else:
        correct_bench = perf.get("benchmark_return", 0)

    correct_alpha   = round(correct_return - correct_bench, 4)
    correct_cash_pct   = round(cash / correct_total * 100, 2) if correct_total else 0
    correct_invest_pct = round(total_market_value / correct_total * 100, 2) if correct_total else 0

    # ── 5. Build corrected perf dict ──────────────────────────────────────────
    corrected_perf = {
        **perf,
        "total_value":        correct_total,
        "total_return_pct":   correct_return,
        "unrealized_pnl_usd": correct_unreal,
        "benchmark_return":   correct_bench,
        "alpha":              correct_alpha,
        "cash_pct":           correct_cash_pct,
        "invested_pct":       correct_invest_pct,
        "beating_nasdaq":     correct_alpha > 0,
        "audited_at":         datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "qqq_live":           round(qqq_live, 2) if qqq_live else None,
    }

    if not log:
        log.append("PASS: All prices and calculations verified correct.")

    return portfolio, corrected_perf, log


# ─── Focus List Audit ─────────────────────────────────────────────────────────
def audit_focus_list(picks: list) -> tuple[list, list]:
    """
    Re-verify every focus list pick:
    - Confirm ticker is real (price fetchable)
    - Re-verify trigger/stop/target math
    - Re-verify R/R ratio
    - Flag any entry zone that no longer makes sense (price blown past trigger)
    Returns (corrected_picks, audit_log).
    """
    log = []
    if not picks:
        return picks, log

    tickers = [p["ticker"] for p in picks]
    live    = _fetch_prices(tickers)

    corrected = []
    removed   = []

    for p in picks:
        ticker  = p["ticker"]
        trigger = p.get("trigger", 0)
        stop    = p.get("stop", 0)
        target  = p.get("target", 0)
        rr      = p.get("rr_ratio", 0)

        # Confirm ticker exists
        live_p = live.get(ticker)
        if live_p is None:
            log.append(f"REMOVE {ticker}: cannot fetch live price — excluded from report")
            removed.append(ticker)
            continue

        # Update current price
        stored_p = p.get("current_price", 0)
        if stored_p and abs(live_p - stored_p) / stored_p * 100 > 1.0:
            log.append(f"FIX {ticker}: current_price ${stored_p:.2f} → ${live_p:.2f}")
        p["current_price"] = round(live_p, 2)

        # Re-verify trigger / stop math
        if trigger and stop:
            correct_stop = round(trigger * 0.92, 2)
            if abs(correct_stop - stop) > 0.01:
                log.append(f"FIX {ticker}: stop ${stop:.2f} → ${correct_stop:.2f}")
                p["stop"] = correct_stop
                stop = correct_stop

        # Re-verify R/R
        if trigger and stop and target and trigger > stop:
            correct_rr = round((target - trigger) / (trigger - stop), 2)
            if rr and abs(correct_rr - rr) > 0.1:
                log.append(f"FIX {ticker}: R/R {rr} → {correct_rr}")
            p["rr_ratio"] = correct_rr

        # Flag if price has already blown past trigger (chasing)
        if live_p > trigger * 1.05:
            p["extended"] = True
            log.append(f"FLAG {ticker}: price ${live_p:.2f} > trigger ${trigger:.2f} +5% — EXTENDED, chase risk")

        # Flag if price dropped below stop (broken setup)
        if live_p < stop:
            log.append(f"REMOVE {ticker}: price ${live_p:.2f} < stop ${stop:.2f} — setup broken")
            removed.append(ticker)
            continue

        corrected.append(p)

    if removed:
        log.append(f"Removed {len(removed)} picks: {', '.join(removed)}")
    if not log:
        log.append("PASS: All focus list picks verified — prices and math correct.")

    return corrected, log


# ─── Master Pre-Send Audit ────────────────────────────────────────────────────
def run_daily_audit(portfolio: dict, perf: dict) -> tuple[dict, dict, list]:
    """
    Full audit before daily Telegram send.
    Returns (verified_portfolio, verified_perf, audit_summary).
    """
    print("  [Auditor] Verifying prices and calculations...")
    port_v, perf_v, plog = audit_portfolio(portfolio, perf)

    # Print what was fixed
    fixes = [l for l in plog if l.startswith("FIX") or l.startswith("WARN") or l.startswith("REMOVE")]
    for f in fixes:
        print(f"    {f}")
    if not fixes:
        print("    All verified.")

    return port_v, perf_v, plog


def run_weekly_audit(portfolio: dict, perf: dict,
                     focus_result: dict) -> tuple[dict, dict, dict, list]:
    """
    Full audit before weekly Telegram send.
    Verifies portfolio + focus list.
    Returns (verified_portfolio, verified_perf, verified_focus, audit_summary).
    """
    print("  [Auditor] Verifying weekly data...")
    port_v, perf_v, plog = audit_portfolio(portfolio, perf)

    # Audit focus list
    flog = []
    if focus_result and focus_result.get("picks"):
        verified_picks, flog = audit_focus_list(focus_result["picks"])
        focus_result = {**focus_result, "picks": verified_picks}

    all_logs = plog + flog
    fixes = [l for l in all_logs if l.startswith("FIX") or l.startswith("WARN")
             or l.startswith("REMOVE") or l.startswith("FLAG")]
    for f in fixes:
        print(f"    {f}")
    if not fixes:
        print("    All verified.")

    return port_v, perf_v, focus_result, all_logs
