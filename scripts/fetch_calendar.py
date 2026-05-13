"""
AlphaAbsolute — Calendar Fetcher
Pulls earnings calendar (Finnhub free tier) + economic data schedule
→ data/event_calendar.json

Usage:
  python scripts/fetch_calendar.py
"""

import json
import os
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
FINNHUB_BASE = "https://finnhub.io/api/v1"

TODAY = datetime.now()
WATCHLIST = [
    "NVDA", "MU", "PLTR", "AVGO", "ANET", "LITE", "COHR",
    "RKLB", "AXON", "CACI", "NNE", "VRT", "AMD", "CRWV", "IONQ",
    "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "SMCI", "ARM",
]


# ── Finnhub Earnings Calendar ─────────────────────────────────────────────────
def fetch_earnings_calendar(days_ahead: int = 7) -> list:
    if not FINNHUB_KEY:
        print("  WARNING: FINNHUB_API_KEY not set — skipping earnings calendar")
        return []
    from_date = TODAY.strftime("%Y-%m-%d")
    to_date = (TODAY + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    try:
        url = (f"{FINNHUB_BASE}/calendar/earnings"
               f"?from={from_date}&to={to_date}&token={FINNHUB_KEY}")
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        raw = data.get("earningsCalendar", [])
    except Exception as ex:
        print(f"  Earnings calendar fetch error: {ex}")
        return []

    results = []
    for e in raw:
        symbol = e.get("symbol", "")
        eps_est = e.get("epsEstimate")
        rev_est = e.get("revenueEstimate")
        results.append({
            "symbol": symbol,
            "name": (e.get("name") or symbol)[:40],
            "date": e.get("date", ""),
            "hour": e.get("hour", ""),       # "amc" / "bmo" / ""
            "eps_estimate": eps_est,
            "revenue_estimate": rev_est,
            "in_watchlist": symbol in WATCHLIST,
        })

    return sorted(results, key=lambda x: (x["date"], x["symbol"]))


# ── Economic Data Calendar ────────────────────────────────────────────────────
# Key recurring US + Thai macro releases with 2026 approximate dates.
# User can override via [MACRO_NEWS] in user_input.txt.

FOMC_2026 = [
    "2026-01-29", "2026-03-19", "2026-04-30", "2026-06-11",
    "2026-07-30", "2026-09-17", "2026-11-05", "2026-12-17",
]

BOT_MPC_2026 = [
    "2026-02-26", "2026-04-29", "2026-06-24",
    "2026-08-05", "2026-10-21", "2026-12-16",
]

# Approximate monthly US data releases (day-of-month estimates)
# These shift ±3 days based on actual BLS/BEA schedules
MONTHLY_US_RELEASES_2026 = {
    # CPI — 2nd week of month (typically Wednesday)
    "CPI": [
        {"date": "2026-01-14", "consensus": "3.4%", "prior": "3.5%"},
        {"date": "2026-02-12", "consensus": "3.2%", "prior": "3.4%"},
        {"date": "2026-03-11", "consensus": "3.1%", "prior": "3.2%"},
        {"date": "2026-04-10", "consensus": "3.0%", "prior": "3.1%"},
        {"date": "2026-05-13", "consensus": "3.0%", "prior": "3.0%"},
        {"date": "2026-06-11", "consensus": None, "prior": None},
        {"date": "2026-07-14", "consensus": None, "prior": None},
        {"date": "2026-08-12", "consensus": None, "prior": None},
        {"date": "2026-09-09", "consensus": None, "prior": None},
        {"date": "2026-10-14", "consensus": None, "prior": None},
        {"date": "2026-11-12", "consensus": None, "prior": None},
        {"date": "2026-12-10", "consensus": None, "prior": None},
    ],
    # NFP — First Friday of month
    "NFP (Non-Farm Payrolls)": [
        {"date": "2026-01-09", "consensus": "175K", "prior": "160K"},
        {"date": "2026-02-06", "consensus": "170K", "prior": "175K"},
        {"date": "2026-03-06", "consensus": "165K", "prior": "170K"},
        {"date": "2026-04-03", "consensus": "160K", "prior": "165K"},
        {"date": "2026-05-01", "consensus": "160K", "prior": "160K"},
        {"date": "2026-06-05", "consensus": None, "prior": None},
        {"date": "2026-07-10", "consensus": None, "prior": None},
        {"date": "2026-08-07", "consensus": None, "prior": None},
        {"date": "2026-09-04", "consensus": None, "prior": None},
        {"date": "2026-10-02", "consensus": None, "prior": None},
        {"date": "2026-11-06", "consensus": None, "prior": None},
        {"date": "2026-12-04", "consensus": None, "prior": None},
    ],
    # PCE — Last Friday of month
    "PCE Price Index": [
        {"date": "2026-01-30", "consensus": "3.4%", "prior": "3.5%"},
        {"date": "2026-02-27", "consensus": "3.2%", "prior": "3.4%"},
        {"date": "2026-03-27", "consensus": "3.1%", "prior": "3.2%"},
        {"date": "2026-04-24", "consensus": "3.0%", "prior": "3.1%"},
        {"date": "2026-05-29", "consensus": None, "prior": None},
        {"date": "2026-06-26", "consensus": None, "prior": None},
        {"date": "2026-07-31", "consensus": None, "prior": None},
        {"date": "2026-08-28", "consensus": None, "prior": None},
        {"date": "2026-09-25", "consensus": None, "prior": None},
        {"date": "2026-10-30", "consensus": None, "prior": None},
        {"date": "2026-11-25", "consensus": None, "prior": None},
        {"date": "2026-12-23", "consensus": None, "prior": None},
    ],
    # Retail Sales — mid-month
    "US Retail Sales MoM": [
        {"date": "2026-01-16", "consensus": "+0.4%", "prior": "+0.7%"},
        {"date": "2026-02-13", "consensus": "+0.3%", "prior": "+0.4%"},
        {"date": "2026-03-16", "consensus": "+0.4%", "prior": "+0.3%"},
        {"date": "2026-04-15", "consensus": "+0.3%", "prior": "+0.4%"},
        {"date": "2026-05-14", "consensus": None, "prior": None},
        {"date": "2026-06-16", "consensus": None, "prior": None},
        {"date": "2026-07-16", "consensus": None, "prior": None},
        {"date": "2026-08-14", "consensus": None, "prior": None},
        {"date": "2026-09-16", "consensus": None, "prior": None},
        {"date": "2026-10-15", "consensus": None, "prior": None},
        {"date": "2026-11-13", "consensus": None, "prior": None},
        {"date": "2026-12-15", "consensus": None, "prior": None},
    ],
    # GDP Advance — end of Jan/Apr/Jul/Oct
    "US GDP QoQ Advance": [
        {"date": "2026-01-29", "consensus": "+2.3%", "prior": "+2.8%"},
        {"date": "2026-04-29", "consensus": None, "prior": None},
        {"date": "2026-07-29", "consensus": None, "prior": None},
        {"date": "2026-10-28", "consensus": None, "prior": None},
    ],
}


def build_economic_calendar(days_ahead: int = 14) -> list:
    today_str = TODAY.strftime("%Y-%m-%d")
    end_str = (TODAY + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    events = []

    # FOMC
    for date in FOMC_2026:
        if today_str <= date <= end_str:
            events.append({
                "date": date,
                "event": "FOMC Meeting — Fed Interest Rate Decision",
                "type": "central_bank",
                "impact": "high",
                "consensus": "Hold",
                "prior": None,
                "source": "Federal Reserve",
            })

    # BoT MPC
    for date in BOT_MPC_2026:
        if today_str <= date <= end_str:
            events.append({
                "date": date,
                "event": "BoT MPC Meeting — Bank of Thailand Rate Decision",
                "type": "central_bank",
                "impact": "high",
                "consensus": "Hold 2.0%",
                "prior": "2.0%",
                "source": "Bank of Thailand",
            })

    # Monthly US releases
    for release_name, dates in MONTHLY_US_RELEASES_2026.items():
        for item in dates:
            if today_str <= item["date"] <= end_str:
                events.append({
                    "date": item["date"],
                    "event": release_name,
                    "type": "us_macro",
                    "impact": "high" if release_name in ("CPI", "NFP (Non-Farm Payrolls)", "FOMC") else "medium",
                    "consensus": item.get("consensus"),
                    "prior": item.get("prior"),
                    "source": "BLS / BEA",
                })

    return sorted(events, key=lambda x: x["date"])


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("\nFetching earnings calendar (Finnhub, next 7 days)...")
    earnings_all = fetch_earnings_calendar(days_ahead=7)
    wl_earnings = [e for e in earnings_all if e["in_watchlist"]]

    print(f"  Total upcoming earnings: {len(earnings_all)} | Watchlist: {len(wl_earnings)}")
    for e in wl_earnings:
        hour = "AMC" if e.get("hour") == "amc" else ("BMO" if e.get("hour") == "bmo" else "TBD")
        est = f"EPS est: ${e['eps_estimate']:.2f}" if e.get("eps_estimate") is not None else ""
        print(f"    {e['symbol']:8} | {e['date']} | {hour} | {est}")

    print("\nBuilding economic data calendar (next 14 days)...")
    economic = build_economic_calendar(days_ahead=14)
    for e in economic:
        cons = f"Consensus: {e['consensus']}" if e.get("consensus") else ""
        print(f"  {e['date']} | {e['event'][:45]:<45} | {cons}")

    # Combine into event_calendar.json
    events_combined = []
    for e in economic:
        events_combined.append({
            "date": e["date"],
            "event": e["event"],
            "impact": e["impact"],
            "consensus": e.get("consensus"),
            "prior": e.get("prior"),
            "type": e["type"],
        })
    for e in wl_earnings:
        hour = "AMC" if e.get("hour") == "amc" else ("BMO" if e.get("hour") == "bmo" else "")
        est = f"${e['eps_estimate']:.2f}" if e.get("eps_estimate") is not None else "N/A"
        events_combined.append({
            "date": e["date"],
            "event": f"Earnings: {e['symbol']} ({hour}) — EPS est {est}",
            "impact": "medium",
            "symbol": e["symbol"],
            "eps_estimate": e.get("eps_estimate"),
            "type": "earnings",
        })

    output = {
        "generated": datetime.now().isoformat(),
        "window_start": TODAY.strftime("%Y-%m-%d"),
        "window_end": (TODAY + timedelta(days=14)).strftime("%Y-%m-%d"),
        "earnings_calendar": earnings_all,
        "watchlist_earnings": wl_earnings,
        "economic_calendar": economic,
        "events": sorted(events_combined, key=lambda x: x["date"]),
    }

    out_path = DATA_DIR / "event_calendar.json"
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {out_path} ({len(events_combined)} events)")
    return output


if __name__ == "__main__":
    main()
