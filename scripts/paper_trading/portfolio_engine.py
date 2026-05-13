"""
AlphaAbsolute — Paper Trading Engine
PULSE framework → mechanical buy/sell signals → virtual $100k portfolio
Goal: Beat QQQ (Nasdaq-100). Paper trade min 14 days → promote to real money.

No LLM calls in core loop. Rule-based engine. Fast.
"""
import json, os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import pandas as pd
import requests
import urllib3

BASE_DIR   = Path(__file__).parent.parent.parent
DATA_DIR   = BASE_DIR / "data" / "paper_trading"
STATE_FILE = DATA_DIR / "portfolio_state.json"
LOG_FILE   = DATA_DIR / "trade_log.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

INITIAL_CAPITAL = 100_000   # $100k virtual USD
BENCHMARK       = "QQQ"     # Beat this

# ─── Direct Yahoo Finance session (bypasses SSL/proxy, no yfinance needed) ────
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
YF_SESSION = requests.Session()
YF_SESSION.verify = False
YF_SESSION.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
})

# ─── PULSE Risk Rules ─────────────────────────────────────────────────────────
RULES = {
    "max_position_pct":    0.15,   # 15% max per position
    "hypergrowth_max_pct": 0.05,   # 5% for Base 0 names
    "bottom_fish_max_pct": 0.04,   # 4% before Stage 2 confirmed
    "stop_loss_pct":      -0.08,   # -8% hard stop
    "trail_stop_pct":     -0.20,   # -20% trailing (after +30% gain)
    "earnings_max_pct":    0.03,   # reduce to 3% within 5 days of earnings
    "max_positions":       12,
    "min_cash_pct":        0.10,   # keep 10% cash minimum
    "cash_raise_200dma_50": 0.30,  # raise to 30% cash if <50% stocks above 200D MA
    "cash_raise_200dma_30": 0.40,  # raise to 40% if <30% above 200D MA
}


# ─── Portfolio State ──────────────────────────────────────────────────────────

def load_portfolio() -> dict:
    if STATE_FILE.exists():
        portfolio = json.loads(STATE_FILE.read_text())
        # Migrate: add missing fields to old state files
        if "realized_pnl_usd" not in portfolio:
            portfolio["realized_pnl_usd"] = 0.0
        if not portfolio.get("benchmark_start_price"):
            # Try to set benchmark price if never captured
            try:
                portfolio["benchmark_start_price"] = get_current_price(BENCHMARK)
                print(f"  [Init] Benchmark {BENCHMARK} start price set: ${portfolio['benchmark_start_price']:.2f}")
            except:
                pass
        return portfolio
    # Initialize fresh portfolio
    portfolio = {
        "capital":               INITIAL_CAPITAL,
        "cash":                  INITIAL_CAPITAL,
        "positions":             {},
        "closed":                [],
        "realized_pnl_usd":      0.0,
        "benchmark_start_price": None,
        "start_date":            datetime.now().strftime("%Y-%m-%d"),
        "last_updated":          datetime.now().strftime("%Y-%m-%d"),
        "rules":                 RULES,
    }
    # Capture benchmark start price at fund launch
    try:
        qqq_price = get_current_price(BENCHMARK)
        if qqq_price:
            portfolio["benchmark_start_price"] = qqq_price
            print(f"  [Init] Fund launched | {BENCHMARK} benchmark: ${qqq_price:.2f}")
    except:
        pass
    save_portfolio(portfolio)
    return portfolio


def save_portfolio(portfolio: dict):
    portfolio["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    STATE_FILE.write_text(json.dumps(portfolio, indent=2))


def load_trade_log() -> list:
    if LOG_FILE.exists():
        return json.loads(LOG_FILE.read_text())
    return []


def save_trade_log(log: list):
    LOG_FILE.write_text(json.dumps(log, indent=2))


# ─── Price Data (direct Yahoo Finance API — no yfinance needed) ───────────────

_YF_PERIOD_MAP = {
    "1mo": "1mo", "3mo": "3mo", "6mo": "6mo",
    "1y": "1y", "2y": "2y", "5y": "5y",
}

def get_price_data(ticker: str, period: str = "1y") -> Optional[pd.DataFrame]:
    """Fetch OHLCV + indicators directly from Yahoo Finance API."""
    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
        r = YF_SESSION.get(url, params={"interval": "1d", "range": period}, timeout=20)
        data = r.json()
        chart_result = data.get("chart", {}).get("result")
        if not chart_result:
            return None   # ticker not found / delisted
        result = chart_result[0]
        timestamps = result["timestamp"]
        ohlcv = result["indicators"]["quote"][0]
        adj_list = result["indicators"].get("adjclose", [{}])
        adj = adj_list[0].get("adjclose", ohlcv["close"]) if adj_list else ohlcv["close"]
        idx = (pd.to_datetime(timestamps, unit="s")
                 .tz_localize("UTC")
                 .tz_convert("America/New_York")
                 .normalize())
        df = pd.DataFrame({
            "Open": ohlcv["open"], "High": ohlcv["high"],
            "Low":  ohlcv["low"],  "Close": adj,
            "Volume": ohlcv["volume"],
        }, index=idx)
        df.index.name = "Date"
        df = df.dropna(subset=["Close"])
        if df.empty:
            return None
        # Add indicators
        df["MA20"]  = df["Close"].rolling(20).mean()
        df["MA50"]  = df["Close"].rolling(50).mean()
        df["MA150"] = df["Close"].rolling(150).mean()
        df["MA200"] = df["Close"].rolling(200).mean()
        df["MA30W"] = df["Close"].rolling(150).mean()   # ~30 weeks
        df["Vol20"] = df["Volume"].rolling(20, min_periods=5).mean()
        df["ADTV"]  = (df["Close"] * df["Volume"]).rolling(63, min_periods=20).mean()  # ~3M ADTV
        return df
    except Exception as e:
        print(f"  [Price error] {ticker}: {e}")
        return None


def get_current_price(ticker: str) -> Optional[float]:
    """Get latest price directly from Yahoo Finance."""
    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
        r = YF_SESSION.get(url, params={"interval": "1d", "range": "5d"}, timeout=10)
        data = r.json()
        chart_result = data.get("chart", {}).get("result")
        if not chart_result:
            return None
        price = chart_result[0]["meta"]["regularMarketPrice"]
        return float(price)
    except:
        return None


# ─── PULSE Signal Generator ───────────────────────────────────────────────────

def check_pulse_setup(ticker: str, setup_type: str = "leader") -> dict:
    """
    Check if ticker meets PULSE setup criteria.
    Returns dict with pass/fail for each criterion.
    """
    df = get_price_data(ticker, period="2y")
    if df is None or len(df) < 200:
        return {"valid": False, "reason": "insufficient data"}

    row = df.iloc[-1]
    price = float(row["Close"])
    hi52  = float(df["Close"].tail(252).max())
    lo52  = float(df["Close"].tail(252).min())

    checks = {}

    if setup_type == "leader":
        # Setup 1: Leader/Momentum (Minervini SEPA)
        checks["stage2"]        = price > float(row["MA30W"])
        checks["above_50dma"]   = price > float(row["MA50"]) * 0.95
        checks["ma150_gt_200"]  = float(row["MA150"]) > float(row["MA200"])
        checks["from_52w_hi"]   = (price / hi52 - 1) > -0.20
        checks["from_52w_lo"]   = (price / lo52 - 1) > 0.15
        checks["adtv_ok"]       = float(row["ADTV"]) > 10_000_000  # $10M USD
        # Volume dry-up (last 5 days avg < 50% of 20D avg = contraction)
        vol_last5 = float(df["Volume"].tail(5).mean())
        checks["vol_dryup"]     = vol_last5 < float(row["Vol20"]) * 0.80

        criteria_weights = {
            "stage2": 0.30, "above_50dma": 0.15, "ma150_gt_200": 0.10,
            "from_52w_hi": 0.15, "from_52w_lo": 0.10, "adtv_ok": 0.10, "vol_dryup": 0.10
        }

    elif setup_type == "hypergrowth":
        # Setup 3: Hypergrowth — Base 0/1, early stage
        checks["early_stage"]   = (price / lo52 - 1) < 2.0  # not 3x+ from low
        checks["above_50dma"]   = price > float(row["MA50"])
        checks["adtv_ok"]       = float(row["ADTV"]) > 5_000_000
        checks["momentum"]      = float(row["MA20"]) > float(row["MA50"])
        criteria_weights = {k: 0.25 for k in checks}

    elif setup_type == "wyckoff":
        # Setup 2: Wyckoff Spring / Stage 1→2 transition
        checks["near_30wma"]    = abs(price / float(row["MA30W"]) - 1) < 0.10
        checks["rs_turning"]    = price > float(df["Close"].tail(63).mean())  # above 3M avg
        checks["adtv_ok"]       = float(row["ADTV"]) > 5_000_000
        criteria_weights = {k: 0.33 for k in checks}

    else:
        return {"valid": False, "reason": f"unknown setup type: {setup_type}"}

    # Score
    score = sum(criteria_weights.get(k, 0) for k, v in checks.items() if v)
    passes = sum(1 for v in checks.values() if v)
    total  = len(checks)

    return {
        "valid":      True,
        "ticker":     ticker,
        "setup_type": setup_type,
        "price":      price,
        "score":      round(score, 2),
        "passes":     passes,
        "total":      total,
        "gate":       "GREEN" if score >= 0.70 else ("YELLOW" if score >= 0.50 else "RED"),
        "checks":     checks,
        "hi52":       hi52,
        "lo52":       lo52,
        "pct_from_hi": round((price/hi52-1)*100, 1),
        "pct_from_lo": round((price/lo52-1)*100, 1),
        "adtv_m":     round(float(row["ADTV"])/1e6, 1),
    }


def calc_position_size(portfolio: dict, ticker: str,
                        setup_type: str, emls_score: float = 70) -> float:
    """EMLS score → position size in USD."""
    cash = portfolio["cash"]
    capital = portfolio["capital"]

    # Base max by setup type
    if setup_type == "hypergrowth":
        max_pct = RULES["hypergrowth_max_pct"]   # 5%
    elif setup_type == "wyckoff":
        max_pct = RULES["bottom_fish_max_pct"]   # 4%
    else:
        max_pct = RULES["max_position_pct"]      # 15%

    # EMLS score → size modifier
    if emls_score >= 90:    size_mult = 1.00
    elif emls_score >= 80:  size_mult = 0.75
    elif emls_score >= 70:  size_mult = 0.50
    else:                   size_mult = 0.25

    target_usd = capital * max_pct * size_mult

    # Don't exceed available cash or max per position
    target_usd = min(target_usd, cash * 0.90)
    target_usd = min(target_usd, capital * max_pct)

    return round(target_usd, 2)


# ─── Trade Execution ──────────────────────────────────────────────────────────

def open_position(portfolio: dict, ticker: str, setup_type: str,
                  emls_score: float = 70, thesis: str = "") -> Optional[dict]:
    """Open a new paper position."""
    if len(portfolio["positions"]) >= RULES["max_positions"]:
        print(f"  [Skip] {ticker}: max positions ({RULES['max_positions']}) reached")
        return None

    if ticker in portfolio["positions"]:
        print(f"  [Skip] {ticker}: already in portfolio")
        return None

    price = get_current_price(ticker)
    if not price:
        print(f"  [Skip] {ticker}: could not get price")
        return None

    size_usd = calc_position_size(portfolio, ticker, setup_type, emls_score)
    if size_usd < 500:
        print(f"  [Skip] {ticker}: position too small (${size_usd})")
        return None

    shares = int(size_usd / price)
    cost   = shares * price
    stop   = price * (1 + RULES["stop_loss_pct"])

    position = {
        "ticker":      ticker,
        "setup_type":  setup_type,
        "emls_score":  emls_score,
        "entry_price": price,
        "shares":      shares,
        "cost":        cost,
        "stop":        round(stop, 2),
        "open_date":   datetime.now().strftime("%Y-%m-%d"),
        "thesis":      thesis,
        "current_price": price,
        "pnl_usd":    0,
        "pnl_pct":    0,
        "days_held":   0,
        "status":      "open",
        "high_since_entry": price,
        "trail_stop":  None,
    }

    portfolio["positions"][ticker] = position
    portfolio["cash"] -= cost

    # Log trade
    log = load_trade_log()
    log.append({**position, "action": "OPEN", "timestamp": datetime.now().isoformat()})
    save_trade_log(log)

    print(f"  [BUY] {ticker} @ ${price:.2f} | {shares} shares | ${cost:,.0f} | Stop: ${stop:.2f}")
    return position


def close_position(portfolio: dict, ticker: str, reason: str) -> Optional[dict]:
    """Close a paper position."""
    if ticker not in portfolio["positions"]:
        return None

    pos = portfolio["positions"][ticker]
    price = get_current_price(ticker)
    if not price:
        price = pos["current_price"]

    proceeds = pos["shares"] * price
    pnl_usd  = proceeds - pos["cost"]
    pnl_pct  = (price / pos["entry_price"] - 1) * 100

    closed = {**pos,
        "exit_price": price,
        "proceeds":   proceeds,
        "pnl_usd":    round(pnl_usd, 2),
        "pnl_pct":    round(pnl_pct, 2),
        "close_date": datetime.now().strftime("%Y-%m-%d"),
        "reason":     reason,
        "status":     "closed",
    }

    portfolio["cash"] += proceeds
    portfolio["closed"].append(closed)
    del portfolio["positions"][ticker]

    # Accumulate realized P&L
    portfolio["realized_pnl_usd"] = round(
        portfolio.get("realized_pnl_usd", 0) + pnl_usd, 2
    )

    # Log
    log = load_trade_log()
    log.append({**closed, "action": "CLOSE", "timestamp": datetime.now().isoformat()})
    save_trade_log(log)

    emoji = "PROFIT" if pnl_usd >= 0 else "LOSS"
    print(f"  [SELL] {ticker} @ ${price:.2f} | {emoji}: ${pnl_usd:+,.0f} ({pnl_pct:+.1f}%) | Reason: {reason}")
    return closed


# ─── Daily Update ─────────────────────────────────────────────────────────────

def update_positions(portfolio: dict) -> list[str]:
    """Update all positions with current prices. Check stops. Return alerts."""
    alerts = []
    for ticker, pos in list(portfolio["positions"].items()):
        price = get_current_price(ticker)
        if not price:
            continue

        days_held = (datetime.now() - datetime.strptime(pos["open_date"], "%Y-%m-%d")).days
        pnl_pct   = (price / pos["entry_price"] - 1) * 100
        pnl_usd   = (price - pos["entry_price"]) * pos["shares"]

        # Update high
        if price > pos.get("high_since_entry", price):
            pos["high_since_entry"] = price
            # Activate trailing stop after +30% gain
            if pnl_pct >= 30:
                pos["trail_stop"] = price * (1 + RULES["trail_stop_pct"])

        pos["current_price"] = price
        pos["pnl_usd"]       = round(pnl_usd, 2)
        pos["pnl_pct"]       = round(pnl_pct, 2)
        pos["days_held"]     = days_held

        # Hard stop
        if price <= pos["stop"]:
            closed = close_position(portfolio, ticker, f"STOP LOSS @ ${price:.2f}")
            alerts.append(f"STOP HIT: {ticker} {pnl_pct:+.1f}%")
            continue

        # Trailing stop
        if pos.get("trail_stop") and price <= pos["trail_stop"]:
            close_position(portfolio, ticker, f"TRAIL STOP @ ${price:.2f}")
            alerts.append(f"TRAIL STOP: {ticker} {pnl_pct:+.1f}%")
            continue

        # Stage 3/4 warning (price below 30W MA = stage change)
        df = get_price_data(ticker, period="1y")
        if df is not None and price < float(df["MA30W"].iloc[-1]) * 0.95:
            alerts.append(f"STAGE WARNING: {ticker} may be entering Stage 3/4")

        # Earnings warning (handled separately — see promotion_checker)

    return alerts


def get_performance(portfolio: dict) -> dict:
    """Calculate fund-style portfolio P&L vs QQQ benchmark."""
    capital = portfolio.get("capital", INITIAL_CAPITAL)

    # Market value of open positions
    unrealized_value = 0.0
    unrealized_pnl   = 0.0
    for pos in portfolio["positions"].values():
        curr  = pos.get("current_price", pos["entry_price"])
        mkt   = pos["shares"] * curr
        cost  = pos.get("cost", pos["entry_price"] * pos["shares"])
        unrealized_value += mkt
        unrealized_pnl   += mkt - cost

    total_value   = portfolio["cash"] + unrealized_value
    realized_pnl  = portfolio.get("realized_pnl_usd", 0.0)
    total_pnl     = realized_pnl + unrealized_pnl
    total_return  = (total_value / capital - 1) * 100
    days = (datetime.now() - datetime.strptime(portfolio["start_date"], "%Y-%m-%d")).days

    # QQQ benchmark return
    benchmark_return = 0.0
    try:
        current_qqq = get_current_price(BENCHMARK)
        start_qqq   = portfolio.get("benchmark_start_price")
        if current_qqq and start_qqq:
            benchmark_return = (current_qqq / start_qqq - 1) * 100
    except:
        pass

    alpha = total_return - benchmark_return

    # Win/loss from closed trades
    closed   = portfolio.get("closed", [])
    wins     = [t for t in closed if t.get("pnl_usd", 0) > 0]
    losses   = [t for t in closed if t.get("pnl_usd", 0) <= 0]
    win_rate = (len(wins) / len(closed) * 100) if closed else 0.0

    # Avg win / avg loss
    avg_win  = (sum(t["pnl_pct"] for t in wins)   / len(wins))   if wins   else 0.0
    avg_loss = (sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else 0.0

    return {
        "total_value":        round(total_value, 2),
        "total_return_pct":   round(total_return, 2),
        "unrealized_pnl_usd": round(unrealized_pnl, 2),
        "realized_pnl_usd":   round(realized_pnl, 2),
        "total_pnl_usd":      round(total_pnl, 2),
        "benchmark_return":   round(benchmark_return, 2),
        "alpha":              round(alpha, 2),
        "cash":               round(portfolio["cash"], 2),
        "cash_pct":           round(portfolio["cash"] / total_value * 100, 1) if total_value else 0,
        "invested_pct":       round(unrealized_value / total_value * 100, 1) if total_value else 0,
        "num_positions":      len(portfolio["positions"]),
        "positions":          len(portfolio["positions"]),
        "days_running":       days,
        "closed_trades":      len(closed),
        "win_rate":           round(win_rate, 1),
        "avg_win_pct":        round(avg_win, 2),
        "avg_loss_pct":       round(avg_loss, 2),
        "beating_nasdaq":     alpha > 0,
        "capital":            capital,
    }


if __name__ == "__main__":
    print("AlphaAbsolute Paper Trading Engine")
    portfolio = load_portfolio()

    print("\nUpdating positions...")
    alerts = update_positions(portfolio)
    save_portfolio(portfolio)

    perf = get_performance(portfolio)
    print(f"\nPortfolio Performance:")
    print(f"  Value:      ${perf['total_value']:,.0f}")
    print(f"  Return:     {perf['total_return_pct']:+.2f}%")
    print(f"  vs QQQ:     {perf['benchmark_return']:+.2f}%")
    print(f"  Alpha:      {perf['alpha']:+.2f}%")
    print(f"  Cash:       {perf['cash_pct']:.1f}%")
    print(f"  Positions:  {perf['positions']}")
    print(f"  Win rate:   {perf['win_rate']:.1f}%")
    print(f"  Beating Nasdaq: {'YES' if perf['beating_nasdaq'] else 'NO'}")

    if alerts:
        print(f"\nAlerts:")
        for a in alerts: print(f"  ! {a}")
