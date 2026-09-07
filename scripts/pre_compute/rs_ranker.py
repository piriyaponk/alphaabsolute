"""
AlphaAbsolute -- RS Universe Ranker  (I1 agent -- $0 cost, pure Python)
======================================================================
Ranks every tracked stock by Relative Strength vs SPY across 6 timeframes.
Detects RS Inflections (Phase 2->3 transitions) -- the most reliable early
signal that institutional money is discovering a stock.

Run:  4:15PM daily (after canslim_scorer.py)
Output: data/rs_universe/latest.json
        data/rs_universe/inflections_{YYMMDD}.json

RS Inflection Definition:
  - RS crossed from BELOW 50th percentile -> ABOVE 70th percentile in <=4 weeks
  - 1M RS rank rising while 3M rank also improving
  - This is a PHASE 2->3 entry signal -- load into watchlist immediately

Cost: $0 (pure Python formulas on price data -- no LLM calls)
"""

import json
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

import urllib3
urllib3.disable_warnings()

# ── Encoding fix for Thai terminal (cp874) ───────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data" / "rs_universe"
PORT_FILE = BASE_DIR / "data" / "paper_trading" / "state.json"
CANSLIM_DIR = BASE_DIR / "data" / "canslim_scores"
ACTIVE_UNIVERSE_FILE = BASE_DIR / "data" / "universe" / "active_universe.json"
SCREEN_UNIVERSE_FILE = BASE_DIR / "data" / "universe" / "screen_universe.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Load .env ────────────────────────────────────────────────────────────────
def _load_env():
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()


# ─── Reporting Universe (hardcoded base) ─────────────────────────────────────
# ALL stocks that appear in the daily report or any AlphaAbsolute output.
# rs_ranker ALWAYS computes market-relative RS for these tickers.
# Add new names here when adding to daily_report.py WATCHLIST or _build_report.py lists.
REPORTING_UNIVERSE = sorted(set([
    # ── Theme 1: AI-Related ─────────────────────────────────────────────────────
    "NVDA", "AMD", "PLTR", "SOUN", "MSFT", "ORCL", "CRM", "IBM", "META",
    # ── Theme 2: Memory / HBM ───────────────────────────────────────────────────
    "MU", "WDC", "STX", "AMAT", "LRCX", "KLAC", "MRAM",
    # ── Theme 3: Space ──────────────────────────────────────────────────────────
    "RKLB", "LUNR", "ASTS", "RDW", "PL",
    # ── Theme 4: Quantum Computing ──────────────────────────────────────────────
    "IONQ", "RGTI", "QUBT", "IBM",
    # ── Theme 5: Photonics / Optical ────────────────────────────────────────────
    "LITE", "COHR", "AAOI", "IPGP", "MRAM",
    # ── Theme 6: Defense Tech / GovTech ─────────────────────────────────────────
    "PLTR", "AXON", "CACI", "LDOS", "SAIC", "BAH", "KTOS", "AVAV",
    # ── Theme 7: Data Center (REIT + Infrastructure) ─────────────────────────────
    "EQIX", "DLR", "VRT", "ETN", "ANET", "APH",
    # ── Theme 8: Nuclear / SMR ──────────────────────────────────────────────────
    "NNE", "OKLO", "CEG", "CCJ", "VST", "BWXT", "SMR",
    # ── Theme 9: NeoCloud / Compute ─────────────────────────────────────────────
    "CRWV", "CORZ", "SMCI", "NTAP", "DELL", "CLSK",
    # ── Theme 10: AI Infrastructure ─────────────────────────────────────────────
    "VRT", "DELL", "ANET", "APH", "EME", "PWR",
    # ── Theme 11: Data Center Infra (build-out) ──────────────────────────────────
    "PWR", "EME", "AMPS", "GLDD",
    # ── Theme 12: Drone / UAV ───────────────────────────────────────────────────
    "ACHR", "JOBY", "RCAT", "AVAV", "KTOS",
    # ── Theme 13: Robotics / Automation ─────────────────────────────────────────
    "ISRG", "TER", "AZTA", "TSLA",
    # ── Theme 14: Connectivity / Satcom ─────────────────────────────────────────
    "TMUS", "ASTS", "ERIC", "NOK",
    # ── Semiconductors Broad ────────────────────────────────────────────────────
    "AVGO", "MRVL", "QCOM", "MPWR", "ON", "NXPI",
    # ── Daily Report BIG CAP (always show) ──────────────────────────────────────
    "CIEN",
]))

# ─── Ticker Universe ─────────────────────────────────────────────────────────

def get_tracked_universe() -> list:
    """
    Build tracked universe:
      Base: REPORTING_UNIVERSE (all stocks in daily report + theme watchlist) — always included
      +Full S&P+NASDAQ: BENCHMARK_TICKERS from rs_benchmark.py (~480 tickers) — the full
         benchmark universe so no top RS stock is missed
      +Dynamic: held positions + CANSLIM scored tickers
    This ensures full S&P+NASDAQ coverage, not just the curated list.
    """
    tickers = set(REPORTING_UNIVERSE)   # start with reporting base

    # ── Full S&P+NASDAQ benchmark (primary expansion) ──────────────────────────
    # Import BENCHMARK_TICKERS from rs_benchmark.py — this is the full ~480-ticker
    # market-representative universe (S&P 500 + Nasdaq 100 + AlphaAbsolute themes).
    # trend_template_screener.py uses rs_universe.keys() so this is the ONLY place
    # needed to expand coverage to full S&P+NASDAQ as user requested.
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "rs_benchmark",
            Path(__file__).parent / "rs_benchmark.py"
        )
        _rsb = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_rsb)
        for t in _rsb.BENCHMARK_TICKERS:
            if t:
                tickers.add(t.upper())
        print(f"  [Universe] Benchmark loaded: {len(_rsb.BENCHMARK_TICKERS)} tickers")
    except Exception as _e:
        print(f"  [Universe] Benchmark import failed ({_e}) -- using curated list only")

    # ── Screen universe file (optional override / additional tickers) ────────────
    if SCREEN_UNIVERSE_FILE.exists():
        try:
            su = json.loads(SCREEN_UNIVERSE_FILE.read_text(encoding="utf-8"))
            for t in su.get("tickers", []):
                tickers.add(t.upper())
        except Exception:
            pass

    # From active universe (legacy curated list — still included)
    if ACTIVE_UNIVERSE_FILE.exists():
        try:
            au = json.loads(ACTIVE_UNIVERSE_FILE.read_text(encoding="utf-8"))
            for t in au.get("tickers", []):
                tickers.add(t.upper())
        except Exception:
            pass

    # From portfolio positions (always track held stocks)
    if PORT_FILE.exists():
        try:
            port = json.loads(PORT_FILE.read_text(encoding="utf-8"))
            for t in port.get("positions", {}).keys():
                tickers.add(t.upper())
        except Exception:
            pass

    # From CANSLIM scored tickers
    if CANSLIM_DIR.exists():
        for f in CANSLIM_DIR.glob("*.json"):
            if f.stem != "_summary":
                tickers.add(f.stem.upper())

    # Remove benchmark + ETFs + empty
    for excl in {"SPY", "QQQ", "IWM", "MDYG", "VIX", "^VIX", "^GSPC", "^IXIC", "^SOX", ""}:
        tickers.discard(excl)

    return sorted(tickers)


# ─── Price Data Fetch ─────────────────────────────────────────────────────────

def fetch_ohlcv(ticker: str, period_days: int = 280) -> dict:
    """
    OHLCV fetch priority — never loses historical data:
      1. JSON cache (fast_prefetch / ohlcv_prefetch — fresh data preferred)
      2. SQLite ohlcv.db (permanent store — survives Polygon outages)
      3. data_engine live fetch (last resort, live API call)

    Returns dict with 'dates', 'close', 'volume' lists (oldest first).
    """
    # ── Priority 1: JSON cache (fast_prefetch writes here) ───────────────────
    try:
        cache_path = BASE_DIR / "data" / "ohlcv_cache" / f"{ticker}.json"
        if cache_path.exists():
            d = json.loads(cache_path.read_text(encoding="utf-8"))
            closes = d.get("close", [])
            if closes and len(closes) >= 30:
                return d
    except Exception:
        pass

    # ── Also try legacy ohlcv_prefetch cache ──────────────────────────────────
    try:
        from scripts.pre_compute.ohlcv_prefetch import read_cache
        cached = read_cache(ticker)
        if cached.get("close") and len(cached["close"]) >= 30:
            return cached
    except Exception:
        pass

    # ── Priority 2: SQLite (permanent, never expires) ─────────────────────────
    # This is the key fix: even if Polygon fails today, SQLite has yesterday's data
    try:
        sys.path.insert(0, str(BASE_DIR))
        from scripts.utils.ohlcv_store import OHLCVStore
        result = OHLCVStore().get_ohlcv(ticker, days=period_days)
        if result and len(result.get("close", [])) >= 30:
            return result
    except Exception:
        pass

    # ── Priority 3: live fetch via data_engine ────────────────────────────────
    # SKIP for rs_ranker: if SQLite (ohlcv.db) has no data for this ticker, it is
    # likely delisted or never in our universe. Live API fetch for 38+ dead tickers
    # causes Tiingo 429 rate-limit sleeps and adds 30+ minutes to rs_ranker runtime.
    # The percentile calculation is not materially affected by skipping ghost tickers.
    return {}


def get_index_closes(period_days: int = 280) -> list:
    """
    Cache QQQ (NASDAQ) closes for the session.
    Index: QQQ — same index used by rs_benchmark.py for the distribution.
    Formula: RS = (1 + stock_ret) / (1 + qqq_ret) - 1
    """
    cache_file = DATA_DIR / "_index_cache.json"
    today_str = date.today().isoformat()

    if cache_file.exists():
        try:
            c = json.loads(cache_file.read_text())
            if c.get("date") == today_str and c.get("ticker") == "QQQ":
                return c["closes"]
        except Exception:
            pass

    data = fetch_ohlcv("QQQ", period_days)
    closes = data.get("close", [])
    if closes:
        cache_file.write_text(json.dumps({"date": today_str, "ticker": "QQQ", "closes": closes}), encoding="utf-8")
    return closes


# ─── RS Calculation ───────────────────────────────────────────────────────────

def calc_rs_ratio(stock_closes: list, index_closes: list, days: int) -> float:
    """
    RS = (1 + stock_return) / (1 + index_return) - 1, expressed as %.
    Index = QQQ (NASDAQ).

    Example: stock +5%, NASDAQ +2% → 1.05/1.02 - 1 = +2.94%
    Example: stock -10%, NASDAQ -20% → 0.90/0.80 - 1 = +12.5% (outperformed)

    This is the multiplicative (ratio) form — mathematically correct vs SPY/QQQ.
    Difference from additive excess is material when returns are large (>15%).
    """
    if len(stock_closes) < days + 1 or len(index_closes) < days + 1:
        return None
    s_start, s_end = stock_closes[-(days + 1)], stock_closes[-1]
    m_start, m_end = index_closes[-(days + 1)], index_closes[-1]
    if s_start <= 0 or m_start <= 0:
        return None
    stock_ratio = s_end / s_start    # e.g. 1.05 for +5%
    index_ratio = m_end / m_start    # e.g. 1.02 for +2%
    return round((stock_ratio / index_ratio - 1) * 100, 2)


def calc_all_timeframes(stock_closes: list, index_closes: list) -> dict:
    """
    Compute RS ratio vs QQQ for 6 timeframes.
    Returns dict with keys: rs_1w, rs_2w, rs_1m, rs_3m, rs_6m, rs_12m
    Values are RS ratio %, centered at 0 (positive = outperformed NASDAQ).
    """
    result = {}
    TF_DAYS = {
        "rs_1w":  5,
        "rs_2w":  10,
        "rs_1m":  21,
        "rs_3m":  63,
        "rs_6m":  126,
        "rs_12m": 252,
    }
    for key, days in TF_DAYS.items():
        result[key] = calc_rs_ratio(stock_closes, index_closes, days)

    # RS momentum: 2W vs 1M -- is recent RS better than longer RS?
    r2w = result.get("rs_2w")
    r1m = result.get("rs_1m")
    r3m = result.get("rs_3m")
    r6m = result.get("rs_6m")
    r12m = result.get("rs_12m")

    # Use explicit None check — `if r2w and r1m` is falsy when value is exactly 0.0 (valid RS return)
    result["rs_mom_2w_1m"] = round(r2w - r1m, 2) if (r2w is not None and r1m is not None) else None
    result["rs_mom_3m_6m"] = round(r3m - r6m, 2) if (r3m is not None and r6m is not None) else None
    result["rs_mom_6m_12m"] = round(r6m - r12m, 2) if (r6m is not None and r12m is not None) else None

    return result


# ─── RS Percentile Ranking ────────────────────────────────────────────────────

def _load_benchmark() -> dict:
    """
    Load pre-built market benchmark distribution from rs_benchmark.py output.
    Returns distributions dict or {} if not built yet.
    """
    dist_file = DATA_DIR / "benchmark_distribution.json"
    if not dist_file.exists():
        return {}
    try:
        d = json.loads(dist_file.read_text(encoding="utf-8"))
        return d.get("distributions", {})
    except Exception:
        return {}


def _excess_to_market_pct(excess: float, tf_dist: dict) -> float:
    """
    Map an excess return (%) to market-relative percentile (0-100)
    using the benchmark distribution breakpoints.
    """
    if not tf_dist or "breakpoints" not in tf_dist:
        return None
    bps = tf_dist["breakpoints"]
    pts = sorted([(float(k), float(v)) for k, v in bps.items()], key=lambda x: x[0])

    if excess <= pts[0][1]:
        # Below p1 threshold -- interpolate down to 0
        if pts[0][1] == 0:
            return 0.0
        return max(0.0, round(pts[0][0] * excess / pts[0][1], 1))

    if excess >= pts[-1][1]:
        # Above p99 -- clamp at 99.9
        return min(99.9, round(pts[-1][0] + (100 - pts[-1][0]) * 0.5, 1))

    for i in range(len(pts) - 1):
        lo_pct, lo_val = pts[i]
        hi_pct, hi_val = pts[i + 1]
        if lo_val <= excess <= hi_val:
            if hi_val == lo_val:
                return round(lo_pct, 1)
            t = (excess - lo_val) / (hi_val - lo_val)
            return round(lo_pct + t * (hi_pct - lo_pct), 1)

    return 50.0


def compute_percentiles(universe_data: dict, timeframe: str = "rs_1m",
                        benchmark: dict = None) -> dict:
    """
    Compute percentile rank for each ticker on a given timeframe.

    If benchmark is provided (from rs_benchmark.py):
        -> market-relative percentile (vs ~300 S&P/Nasdaq stocks) [PREFERRED]
    If not:
        -> within-watchlist percentile (fallback, clearly labelled)

    Returns {ticker: percentile 0-100}.
    """
    tf_dist = (benchmark or {}).get(timeframe, {})
    use_market = bool(tf_dist)

    values = []
    for ticker, data in universe_data.items():
        v = data.get(timeframe)
        if v is not None:
            values.append((ticker, v))

    if not values:
        return {}

    result = {}

    if use_market:
        # Market-relative: map each excess return through benchmark distribution
        for ticker, er in values:
            pct = _excess_to_market_pct(er, tf_dist)
            result[ticker] = round(max(0.0, min(99.9, pct)), 1) if pct is not None else None
    else:
        # Fallback: within-watchlist rank (i / n-1 * 100)
        values.sort(key=lambda x: x[1])
        n = len(values)
        for i, (ticker, _) in enumerate(values):
            result[ticker] = round(i / max(n - 1, 1) * 100, 1)

    return result, use_market


def rank_universe(universe_data: dict) -> dict:
    """
    Compute percentile ranks for all 6 timeframes.
    Uses market-relative benchmark if benchmark_distribution.json exists,
    otherwise falls back to within-watchlist ranking.
    Returns {ticker: {rs_1m_pct: X, rs_3m_pct: Y, ..., rs_source: 'market'|'watchlist'}}.
    """
    benchmark = _load_benchmark()
    timeframes = ["rs_1w", "rs_2w", "rs_1m", "rs_3m", "rs_6m", "rs_12m"]
    all_pcts   = {}
    source_used = "watchlist"  # will be updated to "market" if benchmark used

    for tf in timeframes:
        result = compute_percentiles(universe_data, tf, benchmark)
        # Handle both old (dict) and new (dict, bool) return forms
        if isinstance(result, tuple):
            pcts, used_market = result
            if used_market:
                source_used = "market"
        else:
            pcts = result

        for ticker, pct in pcts.items():
            if pct is None:
                continue
            if ticker not in all_pcts:
                all_pcts[ticker] = {}
            all_pcts[ticker][f"{tf}_pct"] = pct

    # Composite RS score -- weighted average of available timeframes
    # Weights: 1W=10%, 2W=15%, 1M=25%, 3M=25%, 6M=15%, 12M=10%
    WEIGHTS = {
        "rs_1w_pct":  0.10,
        "rs_2w_pct":  0.15,
        "rs_1m_pct":  0.25,
        "rs_3m_pct":  0.25,
        "rs_6m_pct":  0.15,
        "rs_12m_pct": 0.10,
    }
    for ticker, pcts in all_pcts.items():
        composite = 0.0
        weight_sum = 0.0
        for key, weight in WEIGHTS.items():
            v = pcts.get(key)
            if v is not None:
                composite += v * weight
                weight_sum += weight
        if weight_sum > 0:
            all_pcts[ticker]["rs_composite_pct"] = round(composite / weight_sum, 1)
        all_pcts[ticker]["rs_source"] = source_used

    return all_pcts


# ─── RS Inflection Detection ──────────────────────────────────────────────────

def detect_rs_inflection(ticker: str, current_pct: dict, history_file: Path) -> dict:
    """
    Detect RS inflection -- most powerful Phase 2->3 transition signal.

    Inflection = stock crossed from below 50th percentile -> above 70th
    within the last 4 weeks (20 trading days).

    Returns inflection_signal dict.
    """
    result = {
        "ticker":          ticker,
        "is_inflection":   False,
        "inflection_type": None,
        "strength":        "NONE",
        "notes":           "",
    }

    curr_comp = current_pct.get("rs_composite_pct", 0)
    curr_1m   = current_pct.get("rs_1m_pct", 0)
    curr_3m   = current_pct.get("rs_3m_pct", 0)

    # Load historical percentile ranks from saved data
    if not history_file.exists():
        result["notes"] = "No history -- first run"
        return result

    try:
        history = json.loads(history_file.read_text(encoding="utf-8"))
        # history is a list of {date, rs_composite_pct, rs_1m_pct, ...}
        # Get readings from 4 weeks ago (20 days)
        records = sorted(history, key=lambda x: x.get("date", ""))

        # Get reading 4 weeks ago
        prev_comp = None
        prev_1m   = None
        if len(records) >= 4:
            # Weekly history entries -- go back 4 records (~4 weeks)
            old = records[-4]
            prev_comp = old.get("rs_composite_pct")
            prev_1m   = old.get("rs_1m_pct")
        elif records:
            old = records[0]
            prev_comp = old.get("rs_composite_pct")
            prev_1m   = old.get("rs_1m_pct")

        # ── Check inflection conditions ──────────────────────────────────────
        inflected = False
        signal_type = None
        strength = "NONE"

        if prev_comp is not None and curr_comp is not None:
            # Type A: Classic RS cross (below 50 -> above 70)
            if prev_comp < 50 and curr_comp >= 70:
                inflected = True
                signal_type = "RS_CROSS_50_TO_70"
                strength = "STRONG" if curr_comp >= 80 else "MODERATE"

            # Type B: RS acceleration (already above 70, accelerating to top decile)
            elif curr_comp >= 90 and prev_comp < 80:
                inflected = True
                signal_type = "RS_SURGE_TO_TOP_DECILE"
                strength = "STRONG"

            # Type C: 1M RS improvement with 3M confirmation
            elif curr_1m >= 70 and curr_3m >= 60 and (prev_1m or 0) < 50:
                inflected = True
                signal_type = "RS_MULTI_TF_INFLECTION"
                strength = "MODERATE"

        if inflected:
            result["is_inflection"]   = True
            result["inflection_type"] = signal_type
            result["strength"]        = strength
            result["prev_composite_pct"] = prev_comp
            result["curr_composite_pct"] = curr_comp
            result["notes"] = (
                f"RS crossed from {prev_comp:.0f}th -> {curr_comp:.0f}th percentile. "
                f"Phase 2->3 transition signal."
            )

    except Exception as e:
        result["notes"] = f"History read error: {e}"

    return result



# ─── History Persistence ──────────────────────────────────────────────────────

def _get_history_file(ticker: str) -> Path:
    hist_dir = DATA_DIR / "history"
    hist_dir.mkdir(exist_ok=True)
    return hist_dir / f"{ticker}_rs_history.json"


def _append_history(ticker: str, record: dict):
    """Append today's RS record to ticker history (weekly snapshots, keep 52)."""
    f = _get_history_file(ticker)
    try:
        history = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    except Exception:
        history = []

    today = date.today().isoformat()
    # Don't duplicate same-day entries
    history = [h for h in history if h.get("date") != today]
    record["date"] = today
    history.append(record)
    # Keep last 52 weekly snapshots
    history = history[-52:]
    f.write_text(json.dumps(history, indent=2), encoding="utf-8")


# ─── Main Ranking Engine ──────────────────────────────────────────────────────

def run():
    """
    Full RS ranking pass over the tracked universe.
    Writes:
      data/rs_universe/latest.json          -- full ranked universe
      data/rs_universe/inflections_YYMMDD.json -- today's inflection signals
      data/rs_universe/_spy_cache.json       -- SPY price cache (intraday reuse)
    """
    today_str = date.today().strftime("%y%m%d")
    today_iso = date.today().isoformat()
    print(f"\n[RS Ranker] Running at {datetime.now().strftime('%H:%M:%S')} | {today_str}")

    # ── Freshness check: skip if latest.json was already built today ────────────
    # The fast_prefetch + Polygon pipeline already ran earlier and produced a full
    # 1,246-ticker ranking. No need to re-run the slow Yahoo fallback path.
    latest_file = DATA_DIR / "latest.json"
    if latest_file.exists():
        try:
            existing = json.loads(latest_file.read_text(encoding="utf-8"))
            if existing.get("date") == today_iso and len(existing.get("ranked", [])) >= 100:
                n = len(existing["ranked"])
                print(f"[RS Ranker] latest.json is already fresh for {today_iso} ({n} tickers) — skipping rebuild.")
                print(f"[RS Ranker] To force a full rebuild, delete data/rs_universe/latest.json first.")
                return
        except Exception:
            pass  # corrupted file — proceed with rebuild

    # 1. Fetch QQQ (NASDAQ) as index benchmark
    index_closes = get_index_closes(280)
    if len(index_closes) < 30:
        print("[RS Ranker] ERROR: Cannot fetch QQQ (NASDAQ) data -- aborting.")
        return
    print(f"[RS Ranker] Index: QQQ (NASDAQ) | {len(index_closes)} bars")

    # 2. Build universe
    universe = get_tracked_universe()
    if not universe:
        print("[RS Ranker] WARNING: Universe is empty -- add tickers to portfolio or universe files.")
        return

    print(f"[RS Ranker] Universe: {len(universe)} tickers -- {', '.join(universe[:10])}{'...' if len(universe) > 10 else ''}")

    # 3. Fetch all tickers
    raw_data = {}   # ticker -> {rs_1w, rs_2w, ..., price_fields}
    for ticker in universe:
        data = fetch_ohlcv(ticker, 400)   # 400 days = enough for 1.5Y high + 200D MA
        closes  = data.get("close", [])
        volumes = data.get("volume", [])
        if len(closes) < 25:
            print(f"  [RS Ranker] SKIP {ticker}: insufficient data ({len(closes)} bars)")
            continue
        tf_rs = calc_all_timeframes(closes, index_closes)

        # ── Trend Template Price Fields ─────────────────────────────────────
        # Compute MA and distance metrics while price data is in memory
        import statistics as _stats
        cur = closes[-1]

        def _ma(n):
            """Simple moving average of last n bars."""
            if len(closes) < n:
                return None
            return sum(closes[-n:]) / n

        ma50  = _ma(50)
        ma150 = _ma(150)
        ma200 = _ma(200)

        hi52w  = max(closes[-252:]) if len(closes) >= 252 else max(closes)
        lo52w  = min(closes[-252:]) if len(closes) >= 252 else min(closes)
        hi378  = max(closes[-378:]) if len(closes) >= 378 else max(closes)  # ~1.5Y

        # 6M ADTV (USD): avg daily volume × price (last 126 trading days)
        n_adtv = min(126, len(closes), len(volumes))
        adtv_usd = None
        if n_adtv >= 20 and volumes:
            daily_vals = [closes[-n_adtv + i] * (volumes[-n_adtv + i] or 0)
                          for i in range(n_adtv)]
            adtv_usd = sum(daily_vals) / n_adtv

        price_fields = {
            "_close_last":      cur,
            # % distances (positive = above)
            "pct_from_52w_high":  round((cur / hi52w - 1) * 100, 2) if hi52w else None,
            "pct_from_52w_low":   round((cur / lo52w - 1) * 100, 2) if lo52w else None,
            "pct_from_50d_ma":    round((cur / ma50  - 1) * 100, 2) if ma50  else None,
            "pct_from_1_5y_high": round((cur / hi378 - 1) * 100, 2) if hi378 else None,
            "ma150_vs_ma200_pct": round((ma150 / ma200 - 1) * 100, 2) if (ma150 and ma200) else None,
            "adtv_6m_usd":        round(adtv_usd, 0) if adtv_usd else None,
            "adtv_6m_m":          round(adtv_usd / 1e6, 2) if adtv_usd else None,
        }

        raw_data[ticker] = {**tf_rs, **price_fields}

    if len(raw_data) < 3:
        print(f"[RS Ranker] Only {len(raw_data)} tickers with data -- skipping percentile rank.")
        return

    # 4. Compute percentile ranks
    pct_ranks = rank_universe(raw_data)

    # 5. Detect inflections for each ticker
    inflections    = []
    full_universe  = {}

    for ticker in sorted(raw_data.keys()):
        tf_data = raw_data[ticker]
        pcts    = pct_ranks.get(ticker, {})
        hist_f  = _get_history_file(ticker)

        # Momentum = percentile-point differences (shorter TF pct - longer TF pct)
        # Positive = recent rank BETTER than longer-term rank = ACCELERATING (good)
        # < -10pp = deceleration warning | < -20pp = sharp decel warning
        #
        # KEY SIGNALS (RS momentum):
        #   rs_mom_1m_3m  = rs_1m_pct - rs_3m_pct  → PRIMARY: is 1M rank > 3M rank?
        #   rs_mom_3m_6m  = rs_3m_pct - rs_6m_pct  → CONFIRM: is 3M rank > 6M rank?
        p2w  = pcts.get("rs_2w_pct")
        p1m  = pcts.get("rs_1m_pct")
        p3m  = pcts.get("rs_3m_pct")
        p6m  = pcts.get("rs_6m_pct")
        p12m = pcts.get("rs_12m_pct")
        momentum = {
            # Use explicit None check — `if (p1m and p3m)` is falsy when value is 0.0 (valid)
            "rs_mom_1m_3m":  round(p1m  - p3m,  2) if (p1m is not None and p3m is not None)  else None,  # PRIMARY
            "rs_mom_3m_6m":  round(p3m  - p6m,  2) if (p3m is not None and p6m is not None)  else None,  # CONFIRM
            "rs_mom_6m_12m": round(p6m  - p12m, 2) if (p6m is not None and p12m is not None) else None,
            "rs_mom_2w_1m":  round(p2w  - p1m,  2) if (p2w is not None and p1m is not None)  else None,  # SHORT TERM
        }

        inflection_sig = detect_rs_inflection(ticker, pcts, hist_f)

        rs_source = pcts.get("rs_source", "watchlist")
        entry = {
            "ticker":           ticker,
            "rs_composite_pct": pcts.get("rs_composite_pct"),
            "rs_1m_pct":        pcts.get("rs_1m_pct"),
            "rs_3m_pct":        pcts.get("rs_3m_pct"),
            "rs_6m_pct":        pcts.get("rs_6m_pct"),
            "rs_12m_pct":       pcts.get("rs_12m_pct"),
            "rs_1w_pct":        pcts.get("rs_1w_pct"),
            "rs_2w_pct":        pcts.get("rs_2w_pct"),
            # RS ratio vs QQQ (NOT percentile) — formula: (1+stock)/(1+qqq)-1, in %
            "rs_1m_excess":     tf_data.get("rs_1m"),
            "rs_3m_excess":     tf_data.get("rs_3m"),
            "rs_6m_excess":     tf_data.get("rs_6m"),
            # Momentum = PERCENTILE-POINT differences (shorter minus longer)
            # Positive = recent rank BETTER than longer-term = ACCELERATING
            # rs_mom_1m_3m PRIMARY: is 1M rank > 3M rank?
            # rs_mom_3m_6m CONFIRM: is 3M rank > 6M rank?
            "rs_mom_1m_3m":     momentum.get("rs_mom_1m_3m"),   # PRIMARY
            "rs_mom_3m_6m":     momentum.get("rs_mom_3m_6m"),   # CONFIRM
            "rs_mom_6m_12m":    momentum.get("rs_mom_6m_12m"),
            "rs_mom_2w_1m":     momentum.get("rs_mom_2w_1m"),   # SHORT TERM
            # ── Trend Template Price Fields (Minervini SEPA criteria) ─────
            "pct_from_52w_high":  tf_data.get("pct_from_52w_high"),   # > -20%
            "pct_from_52w_low":   tf_data.get("pct_from_52w_low"),    # > +10% (>15% superstrong)
            "pct_from_50d_ma":    tf_data.get("pct_from_50d_ma"),     # > -5%
            "pct_from_1_5y_high": tf_data.get("pct_from_1_5y_high"), # > 0%
            "ma150_vs_ma200_pct": tf_data.get("ma150_vs_ma200_pct"), # > -5% (>0% superstrong)
            "adtv_6m_usd":        tf_data.get("adtv_6m_usd"),        # > $15M
            "adtv_6m_m":          tf_data.get("adtv_6m_m"),
            "inflection":       inflection_sig["is_inflection"],
            "inflection_type":  inflection_sig.get("inflection_type"),
            "inflection_strength": inflection_sig.get("strength", "NONE"),
            "inflection_notes": inflection_sig.get("notes", ""),
            "price_last":       tf_data.get("_close_last", 0),
            "rs_source":        rs_source,  # "market" or "watchlist"
            "computed_at":      datetime.now().isoformat(),
        }
        full_universe[ticker] = entry

        if inflection_sig["is_inflection"]:
            inflections.append(entry)

        # 6. Append to history (for future inflection detection)
        history_record = {
            "rs_composite_pct": pcts.get("rs_composite_pct"),
            "rs_1m_pct":        pcts.get("rs_1m_pct"),
            "rs_3m_pct":        pcts.get("rs_3m_pct"),
        }
        _append_history(ticker, history_record)

    # 7. Sort by composite RS percentile (highest first)
    ranked_list = sorted(
        full_universe.items(),
        key=lambda x: x[1].get("rs_composite_pct") or 0,
        reverse=True
    )

    # 8. Build output
    # Determine rs_source from any ticker's metadata
    sample_source = next(
        (v.get("rs_source", "watchlist") for v in full_universe.values()), "watchlist"
    )
    benchmark_meta = {}
    bm_file = DATA_DIR / "benchmark_distribution.json"
    if bm_file.exists():
        try:
            bm = json.loads(bm_file.read_text())
            benchmark_meta = {
                "benchmark_date": bm.get("date"),
                "benchmark_size": bm.get("benchmark_size"),
            }
        except Exception:
            pass

    output = {
        "date":           date.today().isoformat(),
        "computed_at":    datetime.now().isoformat(),
        "rs_source":      sample_source,   # "market" or "watchlist"
        "index": "QQQ",
        "rs_formula": "RS = (1 + stock_ret) / (1 + qqq_ret) - 1",
        "rs_source_note": (
            "RS ratio vs QQQ (NASDAQ) | percentiles vs S&P+Nasdaq+R2000 benchmark (~600 stocks)"
            if sample_source == "market"
            else "RS ratio vs QQQ | percentiles within watchlist only -- run rs_benchmark.py for market-relative ranking"
        ),
        **benchmark_meta,
        "universe_count": len(full_universe),
        "inflection_count": len(inflections),
        # Top 10 RS leaders
        "top_10_leaders": [
            {
                "rank": i + 1,
                "ticker": ticker,
                "rs_composite_pct": data.get("rs_composite_pct"),
                "rs_1m_pct":  data.get("rs_1m_pct"),
                "rs_3m_pct":  data.get("rs_3m_pct"),
                "inflection": data.get("inflection"),
            }
            for i, (ticker, data) in enumerate(ranked_list[:10])
        ],
        # Bottom 5 (RS laggards — sell candidates)
        "rs_laggards": [
            {
                "rank": len(ranked_list) - i,
                "ticker": ticker,
                "rs_composite_pct": data.get("rs_composite_pct"),
                "warning": "RS LAGGARD -- review for exit",
            }
            for i, (ticker, data) in enumerate(reversed(ranked_list[-5:]))
        ],
        # Full ranked universe
        "universe": {ticker: data for ticker, data in ranked_list},
    }

    # 9. Save files
    latest_file = DATA_DIR / "latest.json"
    latest_file.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    # 9b. Persist RS scores to SQLite rs_daily table
    # This means latest.json can always be rebuilt from SQLite even if Polygon fails
    try:
        from scripts.utils.ohlcv_store import OHLCVStore
        store = OHLCVStore()
        # Use the last actual trading date from OHLCV, not today's calendar date.
        # When rs_ranker runs on a weekend, today's date is not a trading day and
        # would create phantom non-trading rows in rs_daily.
        _market_date = None
        try:
            import sqlite3 as _sqlite3
            _db_path = str(BASE_DIR / "data" / "ohlcv.db")
            with _sqlite3.connect(_db_path) as _conn:
                _row = _conn.execute("SELECT MAX(date) FROM ohlcv WHERE close IS NOT NULL").fetchone()
                _market_date = _row[0] if _row and _row[0] else None
        except Exception:
            pass
        today_iso = _market_date if _market_date else date.today().isoformat()
        if today_iso != date.today().isoformat():
            print(f"  [rs_daily] Using last market date {today_iso} (today={date.today().isoformat()})")
        rs_rows = []
        for ticker, data in ranked_list:
            rs_rows.append((
                ticker,
                today_iso,
                data.get("rs_1m_pct") or 0.0,
                data.get("rs_3m_pct") or 0.0,
                data.get("rs_6m_pct") or 0.0,
                data.get("rs_composite_pct") or 0.0,
                data.get("phase", ""),
            ))
        n_written = store.upsert_rs_batch(rs_rows)
        print(f"  SQLite rs_daily: {n_written} rows written for {today_iso}")
    except Exception as e:
        print(f"  [WARN] SQLite RS persist failed: {e}")

    if inflections:
        inflect_file = DATA_DIR / f"inflections_{today_str}.json"
        inflect_file.write_text(json.dumps({
            "date":       date.today().isoformat(),
            "count":      len(inflections),
            "signals":    sorted(inflections,
                                 key=lambda x: x.get("rs_composite_pct") or 0,
                                 reverse=True),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n[RED] RS INFLECTIONS DETECTED ({len(inflections)}):")
        for sig in inflections:
            print(f"   {sig['ticker']}: {sig['inflection_type']} | "
                  f"Strength={sig['inflection_strength']} | {sig['inflection_notes']}")

    # 10. Print summary
    print(f"\n[RS Ranker] Complete -- {len(full_universe)} tickers ranked")
    print(f"  Top 5 RS Leaders:")
    for i, (t, d) in enumerate(ranked_list[:5], 1):
        rs_ok = (d.get("rs_composite_pct") or 0) >= 70
        gate = "[OK]" if rs_ok else "[--]"
        inf  = "[RED] INFLECTION" if d.get("inflection") else ""
        print(f"    #{i} {t:<6} RS={d.get('rs_composite_pct',0):.0f}th | "
              f"1M={d.get('rs_1m_pct',0):.0f}th | "
              f"3M={d.get('rs_3m_pct',0):.0f}th | {gate} {inf}")

    if not inflections:
        print("  No RS inflections today.")

    print(f"  Output: {latest_file}")
    return output


if __name__ == "__main__":
    run()
