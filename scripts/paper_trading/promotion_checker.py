"""
AlphaAbsolute — Promotion Checker
Paper position → Real money when ALL criteria pass.
This is the gate between testing and actual investment.

Rule: Never put real money without 14-day paper validation.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
import yfinance as yf

BASE_DIR   = Path(__file__).parent.parent.parent

# Promotion criteria — ALL must pass
PROMOTION_CRITERIA = {
    "min_days_paper":     14,     # minimum days in paper trading
    "min_pnl_pct":         0,     # paper position must be profitable
    "max_drawdown_pct":  -10,     # max drawdown from entry (-10%)
    "min_emls_score":     70,     # EMLS still above threshold
    "setup_still_valid":  True,   # not below stop, RS still OK
    "no_earnings_days":    5,     # no earnings within 5 days
    "regime_ok":          True,   # regime not RED
}


def check_promotion(position: dict, current_price: float,
                    emls_score: float = 70, regime: str = "neutral") -> dict:
    """
    Check if a paper position is ready to promote to real money.
    Returns: {ready: bool, passed: [], failed: [], recommendation: str}
    """
    passed = []
    failed = []
    entry  = position["entry_price"]
    open_date = datetime.strptime(position["open_date"], "%Y-%m-%d")
    days_held = (datetime.now() - open_date).days
    pnl_pct   = (current_price / entry - 1) * 100
    max_dd    = (current_price / position.get("high_since_entry", entry) - 1) * 100

    # 1. Min days
    if days_held >= PROMOTION_CRITERIA["min_days_paper"]:
        passed.append(f"Paper time OK ({days_held} days >= 14)")
    else:
        failed.append(f"Too early ({days_held}/{PROMOTION_CRITERIA['min_days_paper']} days)")

    # 2. Profitable in paper
    if pnl_pct >= PROMOTION_CRITERIA["min_pnl_pct"]:
        passed.append(f"Profitable in paper ({pnl_pct:+.1f}%)")
    else:
        failed.append(f"Losing in paper ({pnl_pct:+.1f}%)")

    # 3. Max drawdown
    if max_dd >= PROMOTION_CRITERIA["max_drawdown_pct"]:
        passed.append(f"Drawdown OK ({max_dd:.1f}%)")
    else:
        failed.append(f"Excessive drawdown ({max_dd:.1f}% < -10%)")

    # 4. EMLS score still valid
    if emls_score >= PROMOTION_CRITERIA["min_emls_score"]:
        passed.append(f"EMLS score OK ({emls_score})")
    else:
        failed.append(f"EMLS score dropped ({emls_score} < 70)")

    # 5. Setup still valid (above stop)
    stop = position.get("stop", entry * 0.92)
    if current_price > stop:
        passed.append(f"Above stop (${current_price:.2f} > ${stop:.2f})")
    else:
        failed.append(f"Below stop — exit immediately")

    # 6. No earnings within 5 days
    earnings_ok, earnings_note = check_no_earnings(position["ticker"])
    if earnings_ok:
        passed.append(f"No earnings risk ({earnings_note})")
    else:
        failed.append(f"Earnings risk: {earnings_note}")

    # 7. Regime not RED
    regime_ok = regime.lower() not in ("red", "risk-off")
    if regime_ok:
        passed.append(f"Regime OK ({regime})")
    else:
        failed.append(f"Regime RED — wait for improvement")

    ready = len(failed) == 0

    # Position sizing for real money
    suggested_real_pct = 0
    if ready:
        if emls_score >= 90:   suggested_real_pct = 15
        elif emls_score >= 80: suggested_real_pct = 10
        elif emls_score >= 70: suggested_real_pct = 7

    return {
        "ticker":             position["ticker"],
        "ready":              ready,
        "passed":             passed,
        "failed":             failed,
        "days_held":          days_held,
        "paper_pnl":          round(pnl_pct, 2),
        "max_drawdown":       round(max_dd, 2),
        "emls_score":         emls_score,
        "recommendation":     "PROMOTE TO REAL" if ready else f"STAY PAPER ({len(failed)} criteria missing)",
        "suggested_real_pct": suggested_real_pct,
    }


def check_no_earnings(ticker: str) -> tuple[bool, str]:
    """Check if earnings are coming within 5 days."""
    try:
        t = yf.Ticker(ticker)
        cal = t.calendar
        if cal is None or cal.empty:
            return True, "no earnings data"
        # calendar has Earnings Date column
        if "Earnings Date" in cal.columns:
            earn_date = cal["Earnings Date"].iloc[0]
            days_to_earnings = (earn_date - datetime.now()).days
            if days_to_earnings <= 5:
                return False, f"earnings in {days_to_earnings} days"
            return True, f"earnings in {days_to_earnings} days (safe)"
        return True, "no earnings scheduled"
    except:
        return True, "could not check"


def run_weekly_promotion_check(portfolio: dict, regime: str = "neutral") -> list[dict]:
    """Check all paper positions for promotion readiness."""
    results = []
    print("\n=== PROMOTION CHECK (Paper → Real Money) ===")

    for ticker, pos in portfolio.get("positions", {}).items():
        current = pos.get("current_price", pos["entry_price"])
        emls    = pos.get("emls_score", 70)
        result  = check_promotion(pos, current, emls, regime)
        results.append(result)

        status = "READY" if result["ready"] else f"NOT READY ({len(result['failed'])} fail)"
        print(f"\n  {ticker} — {status}")
        print(f"    Paper P&L: {result['paper_pnl']:+.1f}% | {result['days_held']} days held")
        if result["ready"]:
            print(f"    --> Suggested real position: {result['suggested_real_pct']}%")
            print(f"    Criteria passed: {len(result['passed'])}/7")
        else:
            print(f"    Missing: {'; '.join(result['failed'])}")

    # Save results
    out = BASE_DIR / "data" / "paper_trading" / f"{datetime.now().strftime('%y%m%d')}_promotion.json"
    out.write_text(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    from portfolio_engine import load_portfolio, update_positions, save_portfolio
    portfolio = load_portfolio()
    update_positions(portfolio)
    save_portfolio(portfolio)
    results = run_weekly_promotion_check(portfolio, regime="neutral")
    ready = [r for r in results if r["ready"]]
    print(f"\nSummary: {len(ready)}/{len(results)} positions ready for real money")
