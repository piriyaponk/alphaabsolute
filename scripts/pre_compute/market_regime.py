"""
AlphaAbsolute v2 -- A01 Market Health Engine
=============================================
Determines market regime and sets cash floor for the day.
All other agents read this output before taking any action.

4-State Regime (v2):
  Markup       -- bull market, full deployment
  Distribution -- topping, reduce exposure
  Sideways     -- choppy, selective only
  Markdown     -- bear, mostly cash

Cash floors enforced:
  Markup       -> cash_floor=0.00, max_deployed=1.00
  Distribution -> cash_floor=0.40, max_deployed=0.60
  Sideways     -> cash_floor=0.20, max_deployed=0.80
  Markdown     -> cash_floor=0.75, max_deployed=0.25

Leading indicators (6) surface early warnings 10-20 days before regime change.
Druckenmiller Liquidity Gate (6 signals) runs as macro overlay.
TD Sequential on SPY/QQQ reported as info only (never a gate in v2).

Output: data/regime/market_health.json
Run:    6:00 AM daily (pre-market)

Cost: $0 (pure Python, no LLM)
"""

from __future__ import annotations
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

OUT_DIR = ROOT / "data" / "regime"
OUT_DIR.mkdir(parents=True, exist_ok=True)
HEALTH_FILE  = OUT_DIR / "market_health.json"
HISTORY_FILE = OUT_DIR / "regime_history.json"


# ── Data helpers ──────────────────────────────────────────────────────────────

def _fetch_ohlcv(ticker: str, days: int = 120) -> list[dict]:
    """Fetch OHLCV bars for a ticker. Fast path: SQLite ohlcv.db (benchmark tickers
    SPY/QQQ/IWM/RSP are always seeded by update_ohlcv_bulk.py). Fallback: HTTP."""
    DB_PATH = ROOT / "data" / "ohlcv.db"

    # ── Fast path: SQLite (SPY/QQQ/IWM/RSP/VIX all in BENCHMARK_TICKERS) ───
    try:
        import sqlite3
        ticker_db = ticker.lstrip("^")   # VIX stored without ^ in DB
        if DB_PATH.exists():
            with sqlite3.connect(str(DB_PATH)) as conn:
                rows = conn.execute(
                    "SELECT date,open,high,low,close,volume FROM ohlcv "
                    "WHERE ticker=? ORDER BY date ASC",
                    (ticker_db,)
                ).fetchall()
            if len(rows) >= 20:
                records = [
                    {"date": r[0], "open": float(r[1] or 0), "high": float(r[2] or 0),
                     "low": float(r[3] or 0), "close": float(r[4] or 0),
                     "volume": float(r[5] or 0)}
                    for r in rows
                ]
                return records[-days:]
    except Exception:
        pass

    # ── Fallback: HTTP ───────────────────────────────────────────────────────
    try:
        from data_engine import get_ohlcv
        df = get_ohlcv(ticker, period="6mo")
        if df is not None and not df.empty:
            records = []
            for idx, row in df.iterrows():
                records.append({
                    "date":   str(idx.date()) if hasattr(idx, "date") else str(idx),
                    "open":   float(row.get("Open",   0)),
                    "high":   float(row.get("High",   0)),
                    "low":    float(row.get("Low",    0)),
                    "close":  float(row.get("Close",  0)),
                    "volume": float(row.get("Volume", 0)),
                })
            return records[-days:]
    except Exception:
        pass
    return []


def _fetch_fred(series_id: str) -> Optional[float]:
    try:
        from data_engine import get_macro
        obs = get_macro(series_id, limit=3)
        if obs:
            return obs[0]["value"]
    except Exception:
        pass
    return None


def _compute_breadth_from_db() -> dict:
    """
    Compute real market breadth: % of universe stocks above 50DMA and 200DMA.
    Uses SQLite ohlcv table. This is the REAL breadth metric — not SPY's distance
    from its own MA (which is what score_breadth() computed before this fix).

    Returns: {pct_above_50dma, pct_above_200dma, n_above_50, n_above_200, n_total}
    """
    try:
        import sqlite3
        db_path = ROOT / "data" / "ohlcv.db"
        if not db_path.exists():
            return {}
        conn = sqlite3.connect(str(db_path))
        # 50DMA breadth — require >= 45 days of data to avoid small-N distortion
        row50 = conn.execute("""
            WITH recent AS (
                SELECT ticker, close,
                       AVG(close) OVER (PARTITION BY ticker ORDER BY date
                                        ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50,
                       COUNT(*) OVER (PARTITION BY ticker ORDER BY date
                                      ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS row_cnt,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
                FROM ohlcv
                WHERE date >= date((SELECT MAX(date) FROM ohlcv), '-120 days')
            ),
            latest AS (
                SELECT ticker, close, ma50
                FROM recent WHERE rn = 1 AND row_cnt >= 45
            )
            SELECT COUNT(*),
                   SUM(CASE WHEN close >= ma50 THEN 1 ELSE 0 END)
            FROM latest
            WHERE ticker IN (SELECT DISTINCT ticker FROM rs_daily
                             WHERE date = (SELECT MAX(date) FROM rs_daily))
        """).fetchone()
        # 200DMA breadth — require >= 150 days of data
        row200 = conn.execute("""
            WITH recent AS (
                SELECT ticker, close,
                       AVG(close) OVER (PARTITION BY ticker ORDER BY date
                                        ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS ma200,
                       COUNT(*) OVER (PARTITION BY ticker ORDER BY date
                                      ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS row_cnt,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
                FROM ohlcv
                WHERE date >= date((SELECT MAX(date) FROM ohlcv), '-300 days')
            ),
            latest AS (
                SELECT ticker, close, ma200
                FROM recent WHERE rn = 1 AND row_cnt >= 150
            )
            SELECT COUNT(*),
                   SUM(CASE WHEN close >= ma200 THEN 1 ELSE 0 END)
            FROM latest
            WHERE ticker IN (SELECT DISTINCT ticker FROM rs_daily
                             WHERE date = (SELECT MAX(date) FROM rs_daily))
        """).fetchone()
        conn.close()
        n50, above50 = (row50[0] or 0), (row50[1] or 0)
        n200, above200 = (row200[0] or 0), (row200[1] or 0)
        return {
            "pct_above_50dma":  round(above50 / n50 * 100, 1) if n50 > 0 else None,
            "pct_above_200dma": round(above200 / n200 * 100, 1) if n200 > 0 else None,
            "n_above_50dma":    above50,
            "n_above_200dma":   above200,
            "n_breadth_sample": n50,
        }
    except Exception as e:
        print(f"  [WARN] breadth_from_db failed: {e}")
        return {}


def _compute_concentration_signal() -> dict:
    """
    RSP/SPY Concentration Signal (June 2026 — Research-validated).

    Measures whether market gains are BROAD (RSP outperforms SPY) or
    CONCENTRATED in large caps (SPY outperforms RSP).

    RSP = S&P 500 Equal Weight ETF — every stock contributes equally.
    SPY = S&P 500 Cap Weight ETF  — mega-caps dominate (~35% Mag7).

    Method: 20-day rate-of-change for RSP and SPY separately, then compute
    divergence (RSP_ROC - SPY_ROC). This measures whether breadth is CHANGING,
    not just the current level, which avoids the structural Mag7 bias.

    Why NOT cap-weighted breadth:
    - Cap-weighted breadth becomes tautological (just re-creates the index).
    - The independence of equal-weight breadth from cap-weight IS its signal value.
    - RSP/SPY ROC is the best practical non-tautological concentration proxy.
    - Confirmed by: Goldman Sachs (May 2026), Humble Student of Markets methodology,
      Research Agent BOA-021 adjacent finding (June 2026).

    Flags:
      BROADENING  — RSP outperforms SPY 20d ROC by > +1.5% → genuine breadth expansion
      NARROWING   — SPY outperforms RSP 20d ROC by > +1.5% → concentration = fragility
      NEUTRAL     — divergence within ±1.5%

    Also computes cap_eq_divergence (cap-weighted % above 50DMA minus equal-weight %)
    as an INFORMATIONAL metric only — not used in regime scoring.

    Regime modifier (applied in classify_v2_regime via return value):
      NARROWING + Sideways → note added to regime_note (no cash_floor change)
      NARROWING + Markup  → early_warning flag (fragility, not immediate action)
      BROADENING          → positive confirmation in regime_note
    """
    CONC_HIST = ROOT / "data" / "regime" / "concentration_history.json"

    result = {
        "concentration_flag":   "UNKNOWN",
        "rsp_spy_20d_roc_diff": None,
        "rsp_20d_roc":          None,
        "spy_20d_roc":          None,
        "cap_eq_div_50d":       None,   # cap_pct_above50 - eq_pct_above50 (informational)
    }

    try:
        import sqlite3
        db_path = ROOT / "data" / "ohlcv.db"
        if not db_path.exists():
            return result

        with sqlite3.connect(str(db_path)) as conn:
            # ── RSP and SPY prices: last 30 trading days ──────────────────────
            for ticker in ("RSP", "SPY"):
                rows = conn.execute(
                    "SELECT date, close FROM ohlcv WHERE ticker=? ORDER BY date DESC LIMIT 30",
                    (ticker,)
                ).fetchall()
                if ticker == "RSP":
                    rsp_rows = rows
                else:
                    spy_rows = rows

        if len(rsp_rows) < 22 or len(spy_rows) < 22:
            # RSP not yet in DB (first run after adding to BENCHMARK_TICKERS)
            result["concentration_flag"] = "NO_DATA"
            return result

        # Most-recent close = index 0, 20 days ago = index 20
        rsp_now  = rsp_rows[0][1];   rsp_20d = rsp_rows[20][1]
        spy_now  = spy_rows[0][1];   spy_20d = spy_rows[20][1]

        rsp_roc = (rsp_now / rsp_20d - 1) * 100   # % change over 20 trading days
        spy_roc = (spy_now / spy_20d - 1) * 100
        diff    = round(rsp_roc - spy_roc, 2)      # positive = RSP outperforming = broadening

        result["rsp_20d_roc"]          = round(rsp_roc, 2)
        result["spy_20d_roc"]          = round(spy_roc, 2)
        result["rsp_spy_20d_roc_diff"] = diff

        THRESHOLD = 1.5   # pp — inside ±1.5% = noise

        if diff > THRESHOLD:
            flag = "BROADENING"    # RSP outperforming → breadth expanding
        elif diff < -THRESHOLD:
            flag = "NARROWING"     # SPY outperforming → concentration increasing
        else:
            flag = "NEUTRAL"

        result["concentration_flag"] = flag

        # ── Cap-weighted vs equal-weight breadth divergence (informational) ──
        # Uses pre-computed values from ticker_meta — fast, no full scan.
        try:
            with sqlite3.connect(str(db_path)) as conn:
                row = conn.execute("""
                    SELECT
                        SUM(CASE WHEN last_close > (
                            SELECT AVG(close) FROM ohlcv o2
                            WHERE o2.ticker = tm.ticker
                            ORDER BY o2.date DESC LIMIT 50
                        ) THEN market_cap ELSE 0 END) AS cap_above,
                        SUM(market_cap) AS total_cap,
                        SUM(CASE WHEN last_close > (
                            SELECT AVG(close) FROM ohlcv o2
                            WHERE o2.ticker = tm.ticker
                            ORDER BY o2.date DESC LIMIT 50
                        ) THEN 1 ELSE 0 END) AS eq_above,
                        COUNT(*) AS n
                    FROM ticker_meta tm
                    WHERE market_cap > 0 AND n_bars >= 50
                      AND last_date >= date('now', '-5 days')
                """).fetchone()
            # This query is slow — skip if it fails; use pre-cached breadth instead
            if row and row[1] and row[3]:
                cap_pct = row[0] / row[1] * 100
                eq_pct  = row[2] / row[3] * 100
                result["cap_eq_div_50d"] = round(cap_pct - eq_pct, 1)
        except Exception:
            pass  # informational only — don't fail the main signal

        # ── History ──────────────────────────────────────────────────────────
        today_str = date.today().isoformat()
        hist: list = []
        if CONC_HIST.exists():
            try:
                hist = json.loads(CONC_HIST.read_text(encoding="utf-8"))
            except Exception:
                hist = []
        hist = [h for h in hist if h.get("date") != today_str]
        hist.insert(0, {
            "date":   today_str,
            "flag":   flag,
            "diff":   diff,
            "rsp_roc": round(rsp_roc, 2),
            "spy_roc": round(spy_roc, 2),
        })
        CONC_HIST.write_text(json.dumps(hist[:90], indent=2), encoding="utf-8")

        print(f"    Concentration: {flag} | RSP 20d={rsp_roc:+.1f}% vs SPY={spy_roc:+.1f}% | diff={diff:+.1f}pp")

    except Exception as e:
        print(f"  [WARN] _compute_concentration_signal failed: {e}")

    return result


def _compute_breakout_failure_rate() -> Optional[float]:
    """
    BOA-001-A1: % of Top30 Watchlist stocks currently below their 50DMA.
    Reads yesterday's top30_watchlist.json (A06 writes at 8AM; A01 runs at 6AM).
    Returns fraction 0.0-1.0, or None if watchlist unavailable (cold start).
    """
    watchlist_path = ROOT / "data" / "leadership" / "top30_watchlist.json"
    if not watchlist_path.exists():
        return None
    try:
        import sqlite3
        data    = json.loads(watchlist_path.read_text(encoding="utf-8"))
        tickers = [s.get("ticker") for s in data.get("watchlist", []) if s.get("ticker")]
        if not tickers:
            return None

        db_path = ROOT / "data" / "ohlcv.db"
        if not db_path.exists():
            return None

        below_50 = 0
        checked  = 0
        with sqlite3.connect(str(db_path)) as conn:
            for ticker in tickers:
                rows = conn.execute("""
                    SELECT close FROM ohlcv
                    WHERE ticker = ? ORDER BY date DESC LIMIT 50
                """, (ticker,)).fetchall()
                if len(rows) < 50:
                    continue
                closes = [r[0] for r in rows]
                latest = closes[0]
                ma50   = sum(closes) / 50
                checked += 1
                if latest < ma50:
                    below_50 += 1

        return round(below_50 / checked, 3) if checked > 0 else None
    except Exception as e:
        print(f"  [WARN] breakout_failure_rate failed: {e}")
        return None


def _compute_nh_nl() -> dict:
    """
    BOA-001-A2: New 52-Week High / New 52-Week Low counts from ticker_meta.

    Uses ticker_meta.high_52w + last_close (pre-computed by pipeline_metrics.py)
    — no full ohlcv table scan needed.

    Formula (BOA-002 standard practitioner): nh_nl_ratio = nh_count / (nh_count + nl_count)
    Acceleration: ratio_today - ratio_10d_ago (positive = breadth improving).
    Divergence:   NH/NL ratio declining over last 5 days = early warning.
    """
    NH_NL_HIST = ROOT / "data" / "regime" / "nh_nl_history.json"
    today_str  = date.today().isoformat()

    try:
        import sqlite3
        db_path = ROOT / "data" / "ohlcv.db"
        if not db_path.exists():
            return {}

        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute("""
                SELECT
                    SUM(CASE WHEN last_close >= high_52w AND high_52w IS NOT NULL THEN 1 ELSE 0 END) AS nh_count,
                    SUM(CASE WHEN last_close <= low_52w  AND low_52w  IS NOT NULL THEN 1 ELSE 0 END) AS nl_count,
                    COUNT(*) AS universe_n
                FROM ticker_meta
                WHERE n_bars >= 252
                  AND last_close IS NOT NULL
            """).fetchone()

        if not row or (row[2] or 0) == 0:
            return {}

        nh_count   = int(row[0] or 0)
        nl_count   = int(row[1] or 0)
        universe_n = int(row[2])

        # Standard formula — nh/(nh+nl) avoids denominator collapse on zero-NL days
        denom        = nh_count + nl_count
        nh_nl_ratio  = round(nh_count / denom, 4) if denom > 0 else None
        # Net spread as % of universe: +100 = all NH, -100 = all NL
        nh_nl_net_pct = round((nh_count - nl_count) / universe_n * 100, 2)

        # ── History: load → dedup today → append → save (keep 60 days) ─────
        hist: dict = {}
        if NH_NL_HIST.exists():
            try:
                hist = json.loads(NH_NL_HIST.read_text(encoding="utf-8"))
            except Exception:
                hist = {}

        entries: list = hist.setdefault("history", [])
        entries[:] = [e for e in entries if e.get("date") != today_str]
        entries.append({
            "date":         today_str,
            "nh_count":     nh_count,
            "nl_count":     nl_count,
            "universe_n":   universe_n,
            "nh_nl_ratio":  nh_nl_ratio,
            "nh_nl_net_pct": nh_nl_net_pct,
        })
        hist["history"] = entries[-60:]
        NH_NL_HIST.write_text(json.dumps(hist, indent=2), encoding="utf-8")

        # ── Acceleration: today vs ~10 trading days ago ──────────────────────
        nh_acceleration: Optional[float] = None
        if len(entries) >= 11:
            old_ratio = entries[-11].get("nh_nl_ratio")
            if old_ratio is not None and nh_nl_ratio is not None:
                nh_acceleration = round(nh_nl_ratio - old_ratio, 4)

        # ── Divergence: NH/NL ratio declining 5 consecutive days ────────────
        nh_nl_divergence = False
        if len(entries) >= 5:
            recent = [e.get("nh_nl_ratio") for e in entries[-5:] if e.get("nh_nl_ratio") is not None]
            if len(recent) >= 3 and recent[-1] < recent[0]:
                nh_nl_divergence = True   # ratio fell over 5d window

        print(f"    NH/NL: {nh_count}/{nl_count} (n={universe_n}) | ratio={nh_nl_ratio} | "
              f"net={nh_nl_net_pct:+.1f}% | accel={nh_acceleration}")

        return {
            "nh_count":        nh_count,
            "nl_count":        nl_count,
            "universe_n":      universe_n,
            "nh_nl_ratio":     nh_nl_ratio,
            "nh_nl_net_pct":   nh_nl_net_pct,
            "nh_acceleration": nh_acceleration,
            "nh_nl_divergence": nh_nl_divergence,
        }

    except Exception as e:
        print(f"  [WARN] _compute_nh_nl failed: {e}")
        return {}


def _compute_ad_line() -> dict:
    """
    BOA-001-A3: Cumulative Advance-Decline line from ohlcv.db universe.
    - Advances: tickers where today's close > yesterday's close
    - Declines:  tickers where today's close < yesterday's close
    - Appends daily net to ad_line_history.json (bootstraps if missing)
    - Returns: {ad_line_today, ad_line_20d_ago, ad_slope, ad_divergence, ad_direction}
    """
    AD_HIST_FILE = ROOT / "data" / "regime" / "ad_line_history.json"
    today_str    = date.today().isoformat() if "date" not in dir() else str(date.today())

    try:
        import sqlite3
        from datetime import date as _date
        today_str = _date.today().isoformat()
        db_path   = ROOT / "data" / "ohlcv.db"
        if not db_path.exists():
            return {}

        with sqlite3.connect(str(db_path)) as conn:
            # Get latest two dates in the DB
            dates = conn.execute(
                "SELECT DISTINCT date FROM ohlcv ORDER BY date DESC LIMIT 3"
            ).fetchall()
            if len(dates) < 2:
                return {}
            d_today = dates[0][0]
            d_prev  = dates[1][0]

            # Count advances and declines
            row = conn.execute("""
                SELECT
                  SUM(CASE WHEN t.close > p.close THEN 1 ELSE 0 END) AS advances,
                  SUM(CASE WHEN t.close < p.close THEN 1 ELSE 0 END) AS declines
                FROM ohlcv t
                JOIN ohlcv p ON t.ticker = p.ticker AND p.date = ?
                WHERE t.date = ?
            """, (d_prev, d_today)).fetchone()

        if not row or row[0] is None:
            return {}
        advances = int(row[0] or 0)
        declines = int(row[1] or 0)
        net_ad   = advances - declines

        # Load / bootstrap history
        history: list = []
        if AD_HIST_FILE.exists():
            try:
                history = json.loads(AD_HIST_FILE.read_text(encoding="utf-8"))
            except Exception:
                history = []

        # Remove today's entry if already present (idempotent)
        history = [h for h in history if h.get("date") != today_str]

        # Compute cumulative A-D (append to last entry's cumulative)
        last_cum = history[0]["cumulative"] if history else 0
        new_cum  = last_cum + net_ad

        history.insert(0, {
            "date":       today_str,
            "advances":   advances,
            "declines":   declines,
            "net":        net_ad,
            "cumulative": new_cum,
        })
        history = history[:504]  # keep 2 years
        AD_HIST_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")

        # Direction: slope of cumulative A-D line over available history.
        # BOA-024: lower minimum from 21 to 10 entries — enough for directional signal.
        # Use all available history up to 20 days; shorter window = noisier but better than Unknown.
        if len(history) >= 10:
            cum_now  = history[0]["cumulative"]
            ref_idx  = min(20, len(history) - 1)   # use up to 20 days back
            cum_ref  = history[ref_idx]["cumulative"]
            ad_slope = cum_now - cum_ref
            ad_direction = "Up" if ad_slope > 0 else ("Down" if ad_slope < 0 else "Flat")
        else:
            ad_slope     = None
            ad_direction = "Unknown"

        # Divergence: QQQ near 52W high (not checked here) AND A-D slope negative ≥10 days
        neg_days = sum(1 for h in history[:10] if h.get("net", 0) < 0)
        ad_divergence = (neg_days >= 7 and ad_slope is not None and ad_slope < 0)

        return {
            "ad_line_today":        new_cum,
            "ad_line_20d_ago":      history[20]["cumulative"] if len(history) >= 21 else None,
            "ad_slope_20d":         ad_slope,
            "ad_slope_days_actual": len(history),  # BOA-024 DEC-031: label notes real window (< 21 when history is new)
            "ad_direction":         ad_direction,
            "ad_divergence":        ad_divergence,
            "ad_advances":          advances,
            "ad_declines":          declines,
        }
    except Exception as e:
        print(f"  [WARN] _compute_ad_line failed: {e}")
        return {}


def _compute_breadth_signals() -> dict:
    """
    BOA-004-A2: Zweig advance ratio (EMA-10 of adv_ratio).
    BOA-004-A3: Bollinger climax signals (bb_above_pct, bb_below_pct,
                distribution_breadth_pct) — informational only, zero regime weight.
    Uses ohlcv.db universe and regime_history.json for EMA state.
    """
    HIST_FILE = ROOT / "data" / "regime" / "regime_history.json"
    result = {
        "adv_ratio_today":       None,
        "adv_ratio_ema10":       None,
        "extreme_selling_breadth": False,
        "breadth_expansion":     False,
        "zweig_breadth_thrust":  False,
        "bb_above_pct":          None,
        "bb_below_pct":          None,
        "broad_breadth_expansion": False,
        "distribution_breadth_pct": None,
        "breadth_panic":         False,
    }
    try:
        import sqlite3
        db_path = ROOT / "data" / "ohlcv.db"
        if not db_path.exists():
            return result

        with sqlite3.connect(str(db_path)) as conn:
            # ── Get latest two dates ──────────────────────────────────────────
            dates = conn.execute(
                "SELECT DISTINCT date FROM ohlcv ORDER BY date DESC LIMIT 3"
            ).fetchall()
            if len(dates) < 2:
                return result
            d_today, d_prev = dates[0][0], dates[1][0]

            # ── Advance ratio (BOA-004-A2) ────────────────────────────────────
            row_br = conn.execute("""
                SELECT
                  SUM(CASE WHEN t.close > p.close THEN 1 ELSE 0 END) AS advances,
                  SUM(CASE WHEN t.close < p.close THEN 1 ELSE 0 END) AS declines
                FROM ohlcv t
                JOIN ohlcv p ON t.ticker = p.ticker AND p.date = ?
                WHERE t.date = ?
            """, (d_prev, d_today)).fetchone()
            if row_br and (row_br[0] or 0) + (row_br[1] or 0) > 0:
                adv  = int(row_br[0] or 0)
                dec  = int(row_br[1] or 0)
                total = adv + dec
                adv_ratio_today = round(adv / total, 4) if total > 0 else None
                result["adv_ratio_today"] = adv_ratio_today
            else:
                adv_ratio_today = None

            # ── Bollinger climax signals (BOA-004-A3) ─────────────────────────
            # Need 20-day SMA + 20-day StdDev per ticker.
            # Use a windowed query: last 20 closes per ticker, compare latest vs bands.
            bb_rows = conn.execute("""
                WITH windowed AS (
                  SELECT ticker, date, close,
                    AVG(close) OVER (PARTITION BY ticker ORDER BY date
                                     ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20,
                    AVG(close*close) OVER (PARTITION BY ticker ORDER BY date
                                     ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20_sq,
                    COUNT(*)        OVER (PARTITION BY ticker ORDER BY date
                                     ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS n_rows
                  FROM ohlcv
                ),
                latest AS (
                  SELECT ticker, date, close, sma20, sma20_sq, n_rows
                  FROM windowed
                  WHERE date = ? AND n_rows >= 20
                ),
                vol_ma AS (
                  SELECT o.ticker,
                    AVG(o.volume) AS avg_vol_20d,
                    COUNT(*) AS vol_n
                  FROM ohlcv o
                  WHERE o.date <= ? AND o.date >= date(?, '-30 days')
                  GROUP BY o.ticker
                  HAVING COUNT(*) >= 15
                )
                SELECT l.ticker, l.close, l.sma20,
                       SQRT(l.sma20_sq - l.sma20*l.sma20) AS std20,
                       o.volume, v.avg_vol_20d
                FROM latest l
                JOIN ohlcv o ON o.ticker = l.ticker AND o.date = ?
                LEFT JOIN vol_ma v ON v.ticker = l.ticker
            """, (d_today, d_today, d_today, d_today)).fetchall()

        if bb_rows:
            n_total    = len(bb_rows)
            bb_above   = 0
            bb_below   = 0
            dist_count = 0
            for ticker, close, sma20, std20, volume, avg_vol in bb_rows:
                if sma20 is None or std20 is None or std20 <= 0:
                    continue
                upper_bb = sma20 + 2 * std20
                lower_bb = sma20 - 2 * std20
                if close > upper_bb:
                    bb_above += 1
                if close < lower_bb:
                    bb_below += 1
                # Distribution breadth: down >1.5% on volume >1.5× avg
                if avg_vol and avg_vol > 0:
                    down_pct = (close - sma20) / sma20  # proxy vs prior day not available
                    # Use volume vs avg as distribution proxy
                    if volume > avg_vol * 1.5 and close < sma20 * 0.985:
                        dist_count += 1

            if n_total > 0:
                result["bb_above_pct"]            = round(bb_above / n_total * 100, 2)
                result["bb_below_pct"]            = round(bb_below / n_total * 100, 2)
                result["distribution_breadth_pct"] = round(dist_count / n_total * 100, 2)
                result["broad_breadth_expansion"] = (result["bb_above_pct"] > 20.0)
                result["breadth_panic"]           = (result["bb_below_pct"] > 10.0)

        # ── EMA-10 of advance ratio (BOA-004-A2, continued) ──────────────────
        # Load prior EMA from regime_history.json adv_ratio_ema10 field
        if adv_ratio_today is not None:
            prior_ema = None
            try:
                if HIST_FILE.exists():
                    hist_data = json.loads(HIST_FILE.read_text(encoding="utf-8"))
                    # history is list under "history" key
                    for entry in hist_data.get("history", []):
                        if entry.get("adv_ratio_ema10") is not None:
                            prior_ema = entry["adv_ratio_ema10"]
                            break
            except Exception:
                pass
            # EMA formula: EMA = price * k + prior_ema * (1-k), k = 2/(N+1)
            k = 2 / (10 + 1)
            if prior_ema is None:
                ema10 = adv_ratio_today  # seed with today's value
            else:
                ema10 = round(adv_ratio_today * k + prior_ema * (1 - k), 4)
            result["adv_ratio_ema10"] = ema10
            result["extreme_selling_breadth"] = ema10 < 0.43
            result["breadth_expansion"]       = ema10 > 0.58

            # Classic Zweig Breadth Thrust: EMA was <0.40 in last 10 days, now >0.615
            if ema10 > 0.615:
                try:
                    hist_data = json.loads(HIST_FILE.read_text(encoding="utf-8")) if HIST_FILE.exists() else {}
                    recent_emas = [e.get("adv_ratio_ema10") for e in hist_data.get("history", [])[:10]]
                    if any(e is not None and e < 0.40 for e in recent_emas):
                        result["zweig_breadth_thrust"] = True
                except Exception:
                    pass

    except Exception as e:
        print(f"  [WARN] _compute_breadth_signals failed: {e}")

    return result


def _check_ftd_watch(breadth_panic: bool) -> dict:
    """
    BOA-004-A6: Follow-Through Day (FTD) watch mode.
    When breadth_panic fires → watch for QQQ FTD as re-entry signal.
    FTD = Day 4+ of rally from low, QQQ up ≥1.7% on volume ≥ prev day volume.
    Persists watch state in market_health.json (reads prior state).
    """
    HEALTH_FILE_P = ROOT / "data" / "regime" / "market_health.json"
    result = {
        "watch_for_ftd":  False,
        "ftd_rally_day":  0,
        "ftd_triggered":  False,
        "ftd_low_date":   None,
    }
    try:
        # Load prior watch state from last health snapshot
        prior = {}
        if HEALTH_FILE_P.exists():
            try:
                prior = json.loads(HEALTH_FILE_P.read_text(encoding="utf-8"))
            except Exception:
                pass

        was_watching  = prior.get("watch_for_ftd", False)
        prior_ftd_day = prior.get("ftd_rally_day", 0)
        ftd_low_date  = prior.get("ftd_low_date")

        # Activate watch if breadth_panic just fired
        if breadth_panic and not was_watching:
            result["watch_for_ftd"] = True
            result["ftd_rally_day"] = 0
            result["ftd_low_date"]  = date.today().isoformat()
            return result

        if not was_watching and not breadth_panic:
            return result  # nothing to track

        # Fetch QQQ recent OHLCV to track rally
        try:
            from data_engine import get_ohlcv
            qqq = get_ohlcv("QQQ", period="30d")
            if qqq is None or len(qqq) < 5:
                result["watch_for_ftd"] = was_watching
                result["ftd_rally_day"] = prior_ftd_day
                result["ftd_low_date"]  = ftd_low_date
                return result

            closes  = list(qqq["Close"])
            volumes = list(qqq["Volume"])
            n       = len(closes)

            # Day count: count trading days since ftd_low_date
            if ftd_low_date:
                from datetime import date as _date
                low_d = _date.fromisoformat(ftd_low_date)
                today_d = _date.today()
                rally_days = 0
                d = low_d
                while d < today_d:
                    d = d + timedelta(days=1)
                    if d.weekday() < 5:
                        rally_days += 1
            else:
                rally_days = prior_ftd_day + 1

            result["watch_for_ftd"] = True
            result["ftd_rally_day"] = rally_days
            result["ftd_low_date"]  = ftd_low_date

            # FTD check: Day 4+ AND QQQ up ≥1.7% on volume ≥ prev_day volume
            if rally_days >= 4 and n >= 2:
                today_chg  = (closes[-1] - closes[-2]) / closes[-2] * 100
                vol_vs_prev = volumes[-1] >= volumes[-2]
                if today_chg >= 1.7 and vol_vs_prev:
                    result["ftd_triggered"]  = True
                    result["watch_for_ftd"]  = False   # clear watch after FTD fires
                    result["ftd_rally_day"]  = rally_days

        except Exception as e:
            print(f"  [WARN] FTD QQQ fetch failed: {e}")
            result["watch_for_ftd"] = was_watching
            result["ftd_rally_day"] = prior_ftd_day

    except Exception as e:
        print(f"  [WARN] _check_ftd_watch failed: {e}")

    return result


def _compute_breadth_health_score(
    breakout_failure_rate: Optional[float],
    ad_direction: str,
    adv_ratio_ema10: Optional[float],
    nh_nl_ratio: Optional[float] = None,
) -> Optional[float]:
    """
    BOA-001-A6: Breadth Health Score 0-100.
    Weights: NH/NL(40%) + AD Line(40%) + Leader Internals(20%).
    If NH/NL unavailable (BOA-002 pending): substitute adv_ratio_ema10 as NH/NL proxy
    and redistribute weights: adv_ratio(40%) + AD line(40%) + leaders(20%).
    Thresholds: >70 = Markup confirmation | 40-70 = Neutral | <40 = Distribution warning.
    """
    # AD line component (0-100)
    if ad_direction == "Up":
        ad_score = 100.0
    elif ad_direction == "Flat":
        ad_score = 50.0
    elif ad_direction in ("Down", "Unknown"):
        ad_score = 0.0 if ad_direction == "Down" else None
    else:
        ad_score = None

    # Leader internals component (0-100): 0% failure rate = 100 score
    leader_score: Optional[float] = None
    if breakout_failure_rate is not None:
        leader_score = round((1.0 - breakout_failure_rate) * 100, 1)

    # NH/NL or adv_ratio proxy (0-100)
    nh_nl_score: Optional[float] = None
    if nh_nl_ratio is not None:
        # Real NH/NL ratio: 0.0 (all NL) to 1.0 (all NH)
        nh_nl_score = round(nh_nl_ratio * 100, 1)
    elif adv_ratio_ema10 is not None:
        # Proxy: adv_ratio_ema10 rescaled — 0.43 maps to 0, 0.58 maps to 100
        # (matches extreme_selling_breadth and breadth_expansion thresholds)
        nh_nl_score = round(max(0.0, min(100.0, (adv_ratio_ema10 - 0.43) / (0.58 - 0.43) * 100)), 1)

    if nh_nl_score is None and (ad_score is None or leader_score is None):
        return None  # insufficient data

    # Compute weighted score with available components
    total_weight = 0.0
    weighted_sum = 0.0
    if nh_nl_score is not None:
        weighted_sum += 0.40 * nh_nl_score
        total_weight += 0.40
    if ad_score is not None:
        weighted_sum += 0.40 * ad_score
        total_weight += 0.40
    if leader_score is not None:
        weighted_sum += 0.20 * leader_score
        total_weight += 0.20

    if total_weight < 0.20:  # need at least 20% weight to be meaningful
        return None

    # Normalize to 100 if not all components available
    score = round(weighted_sum / total_weight, 1) if total_weight < 1.0 else round(weighted_sum, 1)
    return score


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _sma(prices: list[float], period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


# ── Factor A: Market Structure (0-25 pts) ─────────────────────────────────────

def score_market_structure(spy_bars: list, qqq_bars: list) -> tuple[int, dict]:
    details = {}
    pts = 0

    for name, bars in [("SPY", spy_bars), ("QQQ", qqq_bars)]:
        if not bars:
            continue
        closes = [b["close"] for b in bars]
        sma50  = _sma(closes, 50)
        sma200 = _sma(closes, 200)
        last   = closes[-1]
        details[name] = {
            "close":       round(last,   2),
            "sma50":       round(sma50,  2) if sma50  else None,
            "sma200":      round(sma200, 2) if sma200 else None,
            "above_50dma": last > sma50  if sma50  else False,
            "above_200dma": last > sma200 if sma200 else False,
            "pct_from_52w_high": None,  # not computed here — breadth proxy sufficient
        }

    both_above_50  = all(details.get(n, {}).get("above_50dma",  False) for n in ["SPY", "QQQ"])
    one_above_50   = any(details.get(n, {}).get("above_50dma",  False) for n in ["SPY", "QQQ"])
    both_above_200 = all(details.get(n, {}).get("above_200dma", False) for n in ["SPY", "QQQ"])
    one_above_200  = any(details.get(n, {}).get("above_200dma", False) for n in ["SPY", "QQQ"])

    if both_above_50:   pts += 15
    elif one_above_50:  pts += 8

    if both_above_200:  pts += 6
    elif one_above_200: pts += 3

    # FTD proxy: recovered >5% from 30-day low
    if spy_bars and len(spy_bars) >= 30:
        closes  = [b["close"] for b in spy_bars]
        low_30  = min(closes[-30:])
        latest  = closes[-1]
        if low_30 > 0 and (latest / low_30 - 1) > 0.05:
            pts += 4
            details["ftd_approx"] = "LIKELY"
        else:
            details["ftd_approx"] = "NOT_CONFIRMED"

    details["factor_score"] = min(pts, 25)
    return min(pts, 25), details


# ── Factor B: Volume Quality / Accumulation-Distribution (0-20 pts) ─────────
# Replaced distribution-day-count primary signal (BOA-023 DEC-027/028 + BOA-024).
# Bulkowski (N=568, 60yr): distribution days have zero predictive value in rising trends.
# Lowry/Wyckoff: what percentage of index volume was on UP-close days (accumulation)?
# This is the empirically-grounded version of the same question.

def count_distribution_days(bars: list, lookback: int = 25) -> tuple[int, list[str], int]:
    if len(bars) < lookback + 1:
        bars = bars[-max(len(bars) - 1, 1):]
    else:
        bars = bars[-lookback - 1:]

    # BOA-023 DEC-027: use 50-day average volume (IBD standard) instead of prior-day volume.
    # Prior-day comparison misfires after unusually low/high volume sessions.
    # Require 30+ bars of full window context for the avg — fall back to prior-day if insufficient.
    full_window = bars  # bars is already the lookback slice; use it for avg context
    vol_avgs = {}
    for i in range(1, len(full_window)):
        window_start = max(0, i - 50)
        vol_window = [b["volume"] for b in full_window[window_start:i] if b.get("volume")]
        if len(vol_window) >= 30:
            vol_avgs[i] = sum(vol_window) / len(vol_window)
        else:
            vol_avgs[i] = None  # fall back to prior-day

    dist_days = []
    for i in range(1, len(bars)):
        pct_chg = (bars[i]["close"] - bars[i-1]["close"]) / bars[i-1]["close"]
        vol_threshold = vol_avgs.get(i)
        if vol_threshold is not None:
            vol_ok = bars[i]["volume"] > vol_threshold
        else:
            vol_ok = bars[i]["volume"] > bars[i-1]["volume"]  # fallback
        if pct_chg <= -0.002 and vol_ok:
            dist_days.append(bars[i]["date"])

    stall_count = 0
    for i in range(1, len(bars)):
        curr = bars[i]
        if curr["high"] > curr["low"]:
            close_pct = (curr["close"] - curr["low"]) / (curr["high"] - curr["low"])
            pct_chg   = (curr["close"] - bars[i-1]["close"]) / bars[i-1]["close"]
            if 0 <= pct_chg <= 0.003 and close_pct < 0.30 and curr["volume"] > bars[i-1]["volume"] * 1.2:
                stall_count += 1

    return len(dist_days), dist_days, stall_count


def score_volume_quality(spy_bars: list, qqq_bars: list) -> tuple[int, dict]:
    """
    Measures Accumulation vs Distribution via Lowry/Wyckoff up-volume ratio.
    Primary signal: what % of 10-day index volume was on UP-close days?
    > 60% = accumulation dominant (institutions buying dips)
    < 40% = distribution dominant (institutions selling rallies)
    Distribution day count retained as mild deduct only — it is no longer a hard gate.
    BOA-024 / BOA-023 DEC-027.
    """
    def _up_vol_ratio(bars: list, lookback: int = 15) -> tuple[float | None, int, int]:
        # 15-day window: reduces single-day distortion from extreme events
        if len(bars) < lookback + 1:
            return None, 0, 0
        window = bars[-(lookback + 1):]
        up_vol    = sum(window[i]["volume"] for i in range(1, len(window))
                        if window[i].get("volume") and window[i]["close"] >= window[i-1]["close"])
        total_vol = sum(b["volume"] for b in window[1:] if b.get("volume"))
        ratio = (up_vol / total_vol * 100) if total_vol > 0 else None
        return ratio, up_vol, total_vol

    spy_ratio, _, _ = _up_vol_ratio(spy_bars)
    qqq_ratio, _, _ = _up_vol_ratio(qqq_bars)

    # Average both indexes; fall back to whichever is available
    if spy_ratio is not None and qqq_ratio is not None:
        ratio = (spy_ratio + qqq_ratio) / 2
    else:
        ratio = spy_ratio if spy_ratio is not None else (qqq_ratio if qqq_ratio is not None else 55.0)  # BOA-024 DEC-031: 55=neutral 12pts, not 50=bearish 7pts

    if   ratio >= 62: pts = 20
    elif ratio >= 57: pts = 16
    elif ratio >= 52: pts = 12
    elif ratio >= 47: pts = 7
    elif ratio >= 42: pts = 3
    else:             pts = 0

    # Distribution days: kept for audit trail and downstream context only.
    # NOT deducted from pts — up-vol ratio already captures the same information:
    # heavy down-volume days suppress the ratio directly. Double-deducting creates
    # a double-penalty on the same signal (Bulkowski + BOA-023 lesson).
    spy_dist, spy_dist_dates, spy_stall = count_distribution_days(spy_bars)
    qqq_dist, qqq_dist_dates, qqq_stall = count_distribution_days(qqq_bars)
    dist_count  = max(spy_dist, qqq_dist)
    stall_count = max(spy_stall, qqq_stall)
    dist_dates  = spy_dist_dates if spy_dist >= qqq_dist else qqq_dist_dates

    return pts, {
        "spy_up_vol_ratio":      round(spy_ratio or 0, 1),
        "qqq_up_vol_ratio":      round(qqq_ratio or 0, 1),
        "avg_up_vol_ratio":      round(ratio, 1),
        "spy_distribution_days": spy_dist,
        "qqq_distribution_days": qqq_dist,
        "dist_days_used":        dist_count,
        "dist_day_dates":        dist_dates,
        "stalling_days":         stall_count,
        "factor_score":          pts,
    }


# Keep old name as alias so any callers that still reference score_distribution() don't break
def score_distribution(spy_bars: list, qqq_bars: list) -> tuple[int, dict]:
    return score_volume_quality(spy_bars, qqq_bars)


# ── Factor C: Breadth (0-20 pts) ─────────────────────────────────────────────

def score_breadth(spy_bars: list, iwm_bars: list,
                  pct_above_50dma: Optional[float] = None,
                  pct_above_200dma: Optional[float] = None,
                  ad_direction: Optional[str] = None,
                  ad_divergence: bool = False) -> tuple[int, dict]:
    """
    BOA-024 redesign: 3 sub-scores measuring breadth DEPTH + BREADTH TREND.
    Sub1: % above 50DMA — short-term breadth depth (8 pts)
    Sub2: % above 200DMA — long-term breadth health (6 pts)
    Sub3: AD line direction — trend of breadth (advancing stocks > declining) (6 pts)
    SPY vs IWM spread removed: that measures concentration (large vs small cap), not breadth.
    Concentration is already tracked by _compute_concentration_signal() separately.
    """
    details = {}
    pts = 0

    # Sub-score 1: % above 50DMA (8 pts) — short-term breadth depth
    if pct_above_50dma is not None:
        details["pct_above_50dma"] = round(pct_above_50dma, 1)
        if   pct_above_50dma >= 65:  pts += 8
        elif pct_above_50dma >= 55:  pts += 6
        elif pct_above_50dma >= 45:  pts += 3
        elif pct_above_50dma >= 35:  pts += 1
        else:                        pts += 0
    else:
        # Fallback: SPY vs its own 50DMA — weaker proxy
        if spy_bars and len(spy_bars) >= 50:
            closes = [b["close"] for b in spy_bars]
            sma50  = _sma(closes, 50)
            last   = closes[-1]
            pct    = (last / sma50 - 1) * 100 if sma50 else 0
            details["spy_50dma_fallback_pct"] = round(pct, 2)
            if   pct > 3:   pts += 8
            elif pct > 0:   pts += 5
            elif pct > -3:  pts += 2

    # Sub-score 2: % above 200DMA (6 pts) — long-term breadth health
    if pct_above_200dma is not None:
        details["pct_above_200dma"] = round(pct_above_200dma, 1)
        if   pct_above_200dma >= 60:  pts += 6
        elif pct_above_200dma >= 50:  pts += 4
        elif pct_above_200dma >= 40:  pts += 2
        else:                         pts += 0
    else:
        # Fallback: SPY vs its own 200DMA
        if spy_bars and len(spy_bars) >= 200:
            closes = [b["close"] for b in spy_bars]
            sma200 = _sma(closes, 200)
            last   = closes[-1]
            pct    = (last / sma200 - 1) * 100 if sma200 else 0
            details["spy_200dma_fallback_pct"] = round(pct, 2)
            if   pct > 3:   pts += 6
            elif pct > 0:   pts += 4
            elif pct > -5:  pts += 1

    # Sub-score 3: AD line direction (6 pts) — trend of breadth (BOA-001 approved signal)
    # Positive AD slope = more stocks advancing than declining = healthy internals
    details["ad_direction"] = ad_direction or "Unknown"
    details["ad_divergence"] = ad_divergence
    if ad_direction == "Up" and not ad_divergence:
        pts += 6
    elif ad_direction in ("Up", "Flat") or ad_direction is None or ad_direction == "Unknown":
        pts += 3   # neutral / data not yet available — don't penalize, give half credit
    else:
        pts += 0   # Down or diverging (price at ATH but breadth declining)

    details["factor_score"] = min(pts, 20)
    return min(pts, 20), details


# ── Factor D: TD Sequential (informational only — NOT a gate in v2) ──────────

def get_td_signals() -> dict:
    """
    Read TD state files for SPY + QQQ.
    In v2, TD is NOT a gate — it is reported for size_modifier calculation only.
    A08 Setup Scanner uses TD to adjust position size.
    """
    td_dir = ROOT / "data" / "td_sequential"
    signals = {}

    for ticker in ["SPY", "QQQ"]:
        td_file = td_dir / f"{ticker}.json"
        if not td_file.exists():
            signals[ticker] = "Neutral"
            continue
        state = _load_json(td_file)
        ss = state.get("setup_count", 0)
        cd = state.get("countdown_count", 0)

        if cd >= 13:
            signals[ticker] = "SellCountdown13"
        elif cd >= 10:
            signals[ticker] = "SellCountdown10"
        elif ss >= 9:
            signals[ticker] = "SellSetup9"
        elif ss >= 7:
            signals[ticker] = "SellSetup7"
        elif ss <= -9:
            signals[ticker] = "BuySetup9"
        else:
            signals[ticker] = "Neutral"

    return signals


# ── Factor E: RS Leaders (replaces NRGC leadership — 0-10 pts) ───────────────

def score_rs_leaders() -> tuple[int, dict]:
    """
    Count stocks in RS top quartile (>75th) from latest RS ranking.
    Replaces v1 NRGC-based score_leadership().
    Data source: data/rs_universe/latest.json (from A03 rs_ranker.py)
    """
    details = {}
    pts = 5  # neutral default if no RS data yet

    latest_file = ROOT / "data" / "rs_universe" / "latest.json"
    if not latest_file.exists():
        details["note"] = "No RS data yet — using neutral 5/10"
        details["factor_score"] = pts
        return pts, details

    try:
        data = json.loads(latest_file.read_text(encoding="utf-8"))
        # DA Quant Issue 1 fix: data is stored under "universe" dict, not "results" list
        universe_dict = data.get("universe", {})
        results = list(universe_dict.values())
        total = len(results)
        if total == 0:
            details["note"] = "RS universe empty"
            return pts, details

        top_quartile = [r for r in results if (r.get("rs_3m_pct") or 0) >= 75]
        leader_count = len(top_quartile)
        leader_pct   = leader_count / total * 100 if total > 0 else 0

        details["total_ranked"]  = total
        details["rs_top_quartile_count"] = leader_count
        details["rs_top_quartile_pct"]   = round(leader_pct, 1)

        if   leader_pct >= 30:  pts = 10  # many strong leaders
        elif leader_pct >= 22:  pts = 7
        elif leader_pct >= 15:  pts = 5   # neutral
        elif leader_pct >= 10:  pts = 3
        else:                   pts = 1   # few leaders = late market / bear

    except Exception as e:
        details["error"] = str(e)

    details["factor_score"] = pts
    return pts, details


# ── Factor F: Intermarket (VIX + yields + curve, 0-5 pts) ────────────────────

def score_intermarket(vix_bars: list, yield_10y: Optional[float],
                      yield_curve: Optional[float]) -> tuple[int, dict]:
    details = {}
    pts = 0

    # VIX (0-3 pts)
    if vix_bars and len(vix_bars) >= 5:
        vix = vix_bars[-1]["close"]
        details["vix_current"] = round(vix, 2)
        if   vix < 16:  pts += 3
        elif vix < 20:  pts += 2
        elif vix < 25:  pts += 1
        else:           pts += 0
    else:
        pts += 1  # neutral default

    # 10Y yield (0-2 pts) — ERP framework
    if yield_10y is not None:
        details["yield_10y"] = round(yield_10y, 3)
        if   yield_10y < 3.5:   pts += 2; details["yield_signal"] = "VERY_SUPPORTIVE"
        elif yield_10y < 4.0:   pts += 1; details["yield_signal"] = "SUPPORTIVE"
        elif yield_10y < 4.5:   pts += 0; details["yield_signal"] = "NEUTRAL"
        elif yield_10y < 5.0:   pts -= 1; details["yield_signal"] = "RESTRICTIVE"
        else:                   pts -= 2; details["yield_signal"] = "VERY_RESTRICTIVE"

    # Yield curve (0-1 pt)
    if yield_curve is not None:
        details["yield_curve_t10y2y"] = round(yield_curve, 3)
        if yield_curve > 0:
            pts += 1
            details["curve_signal"] = "NORMAL"
        elif yield_curve > -0.5:
            details["curve_signal"] = "FLAT"
        else:
            details["curve_signal"] = "INVERTED"

    pts = min(max(pts, 0), 5)
    details["factor_score"] = pts
    return pts, details


# ── Factor G: Credit Sentiment (0-5 pts) ──────────────────────────────────────

def score_sentiment(hy_spread: Optional[float]) -> tuple[int, dict]:
    details = {}

    if hy_spread is not None:
        details["hy_spread_pct"] = round(hy_spread, 2)
        if   hy_spread < 3.0:  pts = 5; details["credit_signal"] = "RISK_ON"
        elif hy_spread < 4.0:  pts = 4; details["credit_signal"] = "CALM"
        elif hy_spread < 5.0:  pts = 3; details["credit_signal"] = "NEUTRAL"
        elif hy_spread < 7.0:  pts = 1; details["credit_signal"] = "STRESS"
        else:                  pts = 0; details["credit_signal"] = "CRISIS"
        details["factor_score"] = pts
        return pts, details

    details["note"] = "No HY spread data — using neutral 3/5"
    details["factor_score"] = 3
    return 3, details


# ── Leading Indicators (fire 10-20 days before regime change) ─────────────────

def detect_leading_indicators(spy_bars: list, qqq_bars: list,
                               vix_bars: list) -> tuple[int, list[str]]:
    warnings = []

    # 1. Volume inversion (down vol > up vol over 21 days)
    if spy_bars and len(spy_bars) >= 22:
        bars   = spy_bars[-22:]
        up_vol = sum(b["volume"] for i, b in enumerate(bars[1:], 1)
                     if b["close"] > bars[i-1]["close"])
        dn_vol = sum(b["volume"] for i, b in enumerate(bars[1:], 1)
                     if b["close"] < bars[i-1]["close"])
        if dn_vol > up_vol * 1.15:
            warnings.append("VOLUME_INVERSION: Down-vol > Up-vol 21d (distribution)")

    # 2. Momentum divergence (near ATH but decelerating)
    if spy_bars and len(spy_bars) >= 40:
        closes   = [b["close"] for b in spy_bars]
        high_60  = max(closes[-min(60, len(closes)):])
        pct_from = (closes[-1] / high_60 - 1) * 100
        mom_10d  = (closes[-1] / closes[-11] - 1) * 100
        mom_20d  = (closes[-1] / closes[-21] - 1) * 100
        if pct_from > -5 and mom_10d < mom_20d / 2 and mom_10d < 0.5:
            warnings.append("MOMENTUM_DIVERGENCE: Near ATH but momentum slowing")

    # 3. VIX rising 30% from 3-week low
    if vix_bars and len(vix_bars) >= 15:
        vix_now     = vix_bars[-1]["close"]
        vix_3w_low  = min(b["close"] for b in vix_bars[-15:])
        if vix_now > vix_3w_low * 1.3 and vix_now < 25:
            warnings.append(f"VIX_RISING: +{(vix_now/vix_3w_low-1)*100:.0f}% from 3-week low")

    # 4. QQQ lagging SPY by >2% over 10 days (rotation to defensives)
    if spy_bars and qqq_bars and len(spy_bars) >= 11 and len(qqq_bars) >= 11:
        spy_10d = (spy_bars[-1]["close"] / spy_bars[-11]["close"] - 1) * 100
        qqq_10d = (qqq_bars[-1]["close"] / qqq_bars[-11]["close"] - 1) * 100
        if qqq_10d < spy_10d - 2.0:
            warnings.append(f"GROWTH_LAGGING: QQQ {qqq_10d:.1f}% vs SPY {spy_10d:.1f}% 10d")

    # 5. Failed 50DMA recapture (price crossed above then fell back)
    if spy_bars and len(spy_bars) >= 10:
        closes = [b["close"] for b in spy_bars]
        sma50  = _sma(closes, min(50, len(closes)))
        if sma50:
            above_50 = [c > sma50 for c in closes[-10:]]
            if sum(above_50[:5]) >= 3 and not above_50[-1]:
                warnings.append("FAILED_50DMA: Market crossed above 50DMA then reversed")

    # 6. Distribution cluster: 3+ dist days in last 10 sessions
    if spy_bars and len(spy_bars) >= 11:
        bars10  = spy_bars[-11:]
        dist_10 = sum(1 for i in range(1, len(bars10))
                      if bars10[i]["close"] < bars10[i-1]["close"] * 0.998
                      and bars10[i]["volume"] > bars10[i-1]["volume"])
        if dist_10 >= 3:
            warnings.append(f"DISTRIBUTION_CLUSTER: {dist_10} dist days in last 10 sessions")

    return len(warnings), warnings


# ── Druckenmiller Liquidity Gate (6 signals) ──────────────────────────────────

def compute_liquidity_gate(hy_spread: Optional[float], yield_curve: Optional[float],
                            fed_funds: Optional[float], cpi_yoy: Optional[float],
                            yield_10y: Optional[float], real_yield: Optional[float],
                            spy_pe: float = 21.0) -> tuple[str, int, list[str]]:
    """
    6-signal macro liquidity gate (Druckenmiller: liquidity leads price).
    Returns: (gate_verdict, caution_count, notes)
    gate_verdict: SUPPORTIVE / CAUTIOUS / TIGHT
    """
    notes = []
    cautions = 0

    # 1. HY spreads
    if hy_spread is not None:
        if   hy_spread > 6.0: cautions += 1; notes.append(f"HY={hy_spread:.2f}% WIDE (credit stress)")
        elif hy_spread > 4.5: notes.append(f"HY={hy_spread:.2f}% elevated (watch)")
        else:                 notes.append(f"HY={hy_spread:.2f}% tight (risk-on)")

    # 2. Yield curve
    if yield_curve is not None:
        if   yield_curve < -0.5: cautions += 1; notes.append(f"Curve={yield_curve:.2f}% deep inversion")
        elif yield_curve < 0:    notes.append(f"Curve={yield_curve:.2f}% inverted (watch)")
        else:                    notes.append(f"Curve={yield_curve:.2f}% normal")

    # 3. Yield gap (earnings yield vs 10Y)
    if yield_10y is not None:
        earnings_yield = round(100.0 / spy_pe, 3)
        yield_gap      = round(earnings_yield - yield_10y, 3)
        if   yield_gap < 0.0: cautions += 1; notes.append(f"YieldGap={yield_gap:.2f}% negative (bonds > equities)")
        elif yield_gap < 1.5: notes.append(f"YieldGap={yield_gap:.2f}% thin (watch)")
        else:                 notes.append(f"YieldGap={yield_gap:.2f}% healthy")

    # 4. Inflation
    if cpi_yoy is not None:
        if   cpi_yoy > 4.0: cautions += 1; notes.append(f"CPI={cpi_yoy:.1f}% HIGH (Fed must stay tight)")
        elif cpi_yoy > 3.0: notes.append(f"CPI={cpi_yoy:.1f}% elevated (watch)")
        else:               notes.append(f"CPI={cpi_yoy:.1f}% contained")

    # 5. ERP (earnings yield - real yield)
    if real_yield is not None and yield_10y is not None:
        earnings_yield = round(100.0 / spy_pe, 3)
        erp = round(earnings_yield - real_yield, 3)
        if   erp < 1.0: cautions += 1; notes.append(f"ERP={erp:.2f}% THIN (stocks priced for perfection)")
        elif erp < 2.0: notes.append(f"ERP={erp:.2f}% compressed (selective only)")
        else:           notes.append(f"ERP={erp:.2f}% healthy")

    # 6. Inflation re-acceleration risk (breakeven > 3.0%)
    # (Handled via cpi_yoy signal 4 — placeholder for t5y_be if available)

    if   cautions >= 3: gate = "TIGHT"
    elif cautions >= 1: gate = "CAUTIOUS"
    else:               gate = "SUPPORTIVE"

    return gate, cautions, notes


# ── v2 Regime Classification ──────────────────────────────────────────────────

def classify_v2_regime(total_score: int, dist_days: int,
                        warn_count: int, spy_bars: list,
                        qqq_bars: list) -> tuple[str, float, float, bool, bool, str, int]:
    """
    Map score + signals to v2 4-state regime.
    DA Quant Issue 10: single source of truth for clamping logic.
    Returns: (regime, cash_floor, max_deployed, leaders_ok, bigshot_ok, note, effective_score)
    """
    # Soft downgrade for severe leading indicators only.
    # BOA-024: removed hard dist_days clamp — Factor B (volume quality) now carries
    # the accumulation/distribution signal directly. A score clamp anchored to dist_days
    # double-penalized and overrode improving breadth. Use warn_count (leading indicators)
    # as the only soft cap: genuinely dangerous early-warning clusters still cap the score.
    effective_score = total_score
    if warn_count >= 5:
        effective_score = min(total_score, 39)
    elif warn_count >= 3:
        effective_score = min(total_score, 54)

    # Check if QQQ is below 200DMA (strong markdown signal)
    qqq_below_200 = False
    if qqq_bars and len(qqq_bars) >= 200:
        closes = [b["close"] for b in qqq_bars]
        sma200 = _sma(closes, 200)
        if sma200 and closes[-1] < sma200:
            qqq_below_200 = True

    # v2 4-state mapping (use effective_score — the clamped value that drove the decision)
    if qqq_below_200 or effective_score < 30:
        return ("Markdown", 0.75, 0.25, False, False,
                "QQQ below 200DMA — capital preservation mode. Only deploy if stocks pass full screen.",
                effective_score)

    elif effective_score < 50:
        return ("Distribution", 0.40, 0.60, True, False,
                f"Score {effective_score}/85 — distribution/pressure. Mode A only, reduced size. No new Big Shot entries.",
                effective_score)

    elif effective_score < 68:
        return ("Sideways", 0.20, 0.80, True, True,
                f"Score {effective_score}/85 — choppy/sideways. High conviction only. Small initial size.",
                effective_score)

    else:
        return ("Markup", 0.00, 1.00, True, True,
                f"Score {effective_score}/85 — markup phase. Full deployment authorized. Both modes active.",
                effective_score)


# ── M0 Challenger (bull dissent in bear regime) ───────────────────────────────

def detect_regime_challenge(regime: str, spy_bars: list, qqq_bars: list,
                              vix_bars: list, td_signals: dict,
                              hy_spread: Optional[float],
                              prev_health: dict) -> dict:
    """
    Surface bull counter-signals when regime = Distribution or Markdown.
    NEVER overrides the regime call — informational only.
    Threshold: 4/5 bull signals to activate.
    3-day cooldown to suppress noise.
    """
    result = {"active": False, "bull_count": 0, "bull_signals": [], "message": ""}

    if regime not in ("Distribution", "Markdown"):
        return result

    signals = []

    # 1. TD Buy Setup 9 on SPY or QQQ
    for ticker in ["SPY", "QQQ"]:
        if td_signals.get(ticker) == "BuySetup9":
            signals.append(f"TD {ticker} Buy Setup 9 — downside exhaustion signal")
            break

    # 2. VIX falling fast (>15% drop over 5 bars)
    if len(vix_bars) >= 5:
        vix_5ago = vix_bars[-5]["close"]
        vix_now  = vix_bars[-1]["close"]
        if vix_5ago > 0 and (vix_5ago - vix_now) / vix_5ago >= 0.12:
            signals.append(f"VIX fell {(vix_5ago-vix_now)/vix_5ago*100:.0f}% over 5 bars — fear receding")

    # 3. SPY bounced >4% in last 10 bars
    if len(spy_bars) >= 10:
        closes = [b["close"] for b in spy_bars]
        bounce = (closes[-1] - closes[-10]) / closes[-10] if closes[-10] > 0 else 0
        if bounce >= 0.04:
            signals.append(f"SPY +{bounce*100:.1f}% bounce over 10 bars — price recovering")

    # 4. SPY near/above 50DMA
    if len(spy_bars) >= 50:
        closes   = [b["close"] for b in spy_bars]
        sma50    = _sma(closes, 50)
        ma50_pct = (closes[-1] - sma50) / sma50 * 100 if sma50 else None
        if ma50_pct is not None and ma50_pct > -2:
            signals.append(f"SPY {ma50_pct:+.1f}% vs 50DMA — recapture likely")

    # 5. HY spreads tightening vs last reading
    prev_hy = prev_health.get("macro", {}).get("hy_spread")
    if hy_spread is not None and prev_hy is not None:
        tightening = prev_hy - hy_spread
        if tightening >= 0.15:
            signals.append(f"HY spreads tightening {tightening*100:.0f}bps — credit leading equities up")

    bull_count = len(signals)
    THRESHOLD  = 4
    COOLDOWN   = 3

    # 3-day cooldown check
    prev_challenge  = prev_health.get("regime_challenge", {})
    prev_date_str   = prev_challenge.get("last_fired_date", "")
    prev_bull_count = prev_challenge.get("bull_count", 0)
    in_cooldown     = False
    last_fired_date = prev_date_str

    if prev_date_str:
        try:
            days_since = (date.today() - date.fromisoformat(prev_date_str)).days
            if days_since < COOLDOWN and bull_count <= prev_bull_count:
                in_cooldown = True
        except Exception:
            pass

    active = bull_count >= THRESHOLD and not in_cooldown

    if active:
        last_fired_date = date.today().isoformat()
        message = (
            f"[REGIME CHALLENGE] Regime={regime} but {bull_count}/5 bull signals active. "
            f"Regime call STANDS — watch for Follow-Through Day before re-entering. "
            f"Signals: {' | '.join(s[:60] for s in signals)}"
        )
        print(f"\n  [!!!] REGIME CHALLENGE: {bull_count} bull signals vs regime={regime}")
        for s in signals:
            print(f"    + {s[:80]}")

    return {
        "active":          active,
        "bull_count":      bull_count,
        "threshold":       THRESHOLD,
        "bull_signals":    signals,
        "message":         message if active else "",
        "in_cooldown":     in_cooldown,
        "last_fired_date": last_fired_date,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  A01 Market Health Engine  [{today}]")
    print(f"{'='*55}")

    # Fetch price data
    print("  Fetching SPY, QQQ, IWM, VIX...")
    spy_bars = _fetch_ohlcv("SPY", days=120)
    qqq_bars = _fetch_ohlcv("QQQ", days=120)
    iwm_bars = _fetch_ohlcv("IWM", days=60)
    vix_bars = _fetch_ohlcv("^VIX", days=60)

    # Fetch FRED macro
    print("  Fetching FRED macro (10Y, curve, HY, CPI, real yield)...")
    yield_10y   = _fetch_fred("DGS10")
    yield_curve = _fetch_fred("T10Y2Y")
    hy_spread   = _fetch_fred("BAMLH0A0HYM2")
    fed_funds   = _fetch_fred("DFF")
    real_yield  = _fetch_fred("DFII10")

    cpi_yoy: Optional[float] = None
    try:
        from data_engine import get_macro as _gm
        cpi_obs = _gm("CPIAUCSL", limit=14) or []
        if len(cpi_obs) >= 13:
            cpi_yoy = round((cpi_obs[0]["value"] - cpi_obs[12]["value"]) / cpi_obs[12]["value"] * 100, 2)
    except Exception:
        pass

    macro_str = (f"10Y={yield_10y:.2f}% | curve={yield_curve:.2f}% | "
                 f"HY={hy_spread:.2f}% | CPI={cpi_yoy:.1f}%"
                 if all(x is not None for x in [yield_10y, yield_curve, hy_spread, cpi_yoy])
                 else "Some FRED data unavailable")
    print(f"    {macro_str}")

    # Score all factors (A-G, max 100 pts)
    fa_score, fa_detail = score_market_structure(spy_bars, qqq_bars)
    fb_score, fb_detail = score_volume_quality(spy_bars, qqq_bars)

    # Real breadth computed BEFORE score functions so all sub-scores share the same data
    _pre_breadth     = _compute_breadth_from_db()
    _real_50d_pct    = _pre_breadth.get("pct_above_50dma")  if _pre_breadth else None
    _real_200d_pct   = _pre_breadth.get("pct_above_200dma") if _pre_breadth else None

    # AD line direction: computed here so Factor C can use it (BOA-024)
    _ad_data_early   = _compute_ad_line()
    _ad_direction    = _ad_data_early.get("ad_direction")
    _ad_divergence   = _ad_data_early.get("ad_divergence", False)

    fc_score, fc_detail = score_breadth(
        spy_bars, iwm_bars,
        pct_above_50dma  = _real_50d_pct,
        pct_above_200dma = _real_200d_pct,
        ad_direction     = _ad_direction,
        ad_divergence    = bool(_ad_divergence),
    )
    fe_score, fe_detail = score_rs_leaders()
    ff_score, ff_detail = score_intermarket(vix_bars, yield_10y, yield_curve)
    fg_score, fg_detail = score_sentiment(hy_spread)

    total_score = fa_score + fb_score + fc_score + fe_score + ff_score + fg_score
    # Note: Factor D (TD) not in composite score in v2 — informational only

    td_signals  = get_td_signals()
    warn_count, warnings = detect_leading_indicators(spy_bars, qqq_bars, vix_bars)
    dist_days   = fb_detail.get("dist_days_used", 0)

    # v2 regime classification
    # BOA-024: dist_days no longer drives hard clamp — warn_count is the only soft cap.
    # classify_v2_regime still accepts dist_days for logging / legacy callers.
    regime, cash_floor, max_deployed, leaders_ok, bigshot_ok, regime_note, effective_score = classify_v2_regime(
        total_score, dist_days, warn_count, spy_bars, qqq_bars
    )

    # Liquidity gate
    liq_gate, liq_cautions, liq_notes = compute_liquidity_gate(
        hy_spread, yield_curve, fed_funds, cpi_yoy, yield_10y, real_yield
    )

    # Adjust max_deployed for liquidity tightness (never increase cash_floor here)
    if liq_gate == "TIGHT" and max_deployed > 0.60:
        max_deployed = min(max_deployed, 0.60)
        regime_note += " [Liquidity TIGHT — max_deployed capped at 60%]"

    # Challenger (bull dissent in bearish regime)
    prev_health     = _load_json(HEALTH_FILE)
    regime_challenge = detect_regime_challenge(
        regime, spy_bars, qqq_bars, vix_bars, td_signals, hy_spread, prev_health
    )

    # Real breadth from SQLite (already computed above for score_breadth — reuse it)
    _db_breadth = _pre_breadth

    # BOA-001-A1: Breakout failure rate — % of Top30 Leaders below 50DMA
    _breakout_failure_rate = _compute_breakout_failure_rate()

    # BOA-001-A2: NH/NL counts + acceleration from ticker_meta (no full ohlcv scan)
    print("  Computing NH/NL counts from ticker_meta...")
    _nh_nl = _compute_nh_nl()

    # BOA-001-A3: Cumulative Advance-Decline line
    _ad_data = _ad_data_early  # reuse early computation — already ran above for Factor C

    # BOA-004-A2/A3: Zweig advance ratio + Bollinger breadth signals
    _breadth_signals = _compute_breadth_signals()

    # BOA-004-A6: Follow-Through Day watch mode
    _ftd = _check_ftd_watch(_breadth_signals.get("breadth_panic", False))

    # Concentration signal: RSP/SPY 20d ROC divergence (June 2026)
    _conc = _compute_concentration_signal()

    # BOA-001-A6: Breadth Health Score (composite 0-100)
    # Now uses real NH/NL ratio from BOA-001-A2 (no longer uses adv_ratio proxy for NH/NL)
    _breadth_health_score = _compute_breadth_health_score(
        breakout_failure_rate=_breakout_failure_rate,
        ad_direction=_ad_data.get("ad_direction", "Unknown"),
        adv_ratio_ema10=_breadth_signals.get("adv_ratio_ema10"),
        nh_nl_ratio=_nh_nl.get("nh_nl_ratio"),  # BOA-001-A2: real NH/NL ratio
    )

    # Concentration modifier — append note (no cash_floor change, informational)
    _conc_flag = _conc.get("concentration_flag", "UNKNOWN")
    if _conc_flag == "NARROWING":
        if regime == "Markup":
            regime_note += " [!] RSP/SPY NARROWING — gains concentrated in large caps (fragility signal)"
        elif regime == "Sideways":
            regime_note += " [!] RSP/SPY NARROWING — breadth not confirming recovery"
    elif _conc_flag == "BROADENING":
        regime_note += " [+] RSP/SPY BROADENING — breadth expanding, large AND small caps participating"

    # Trend vs yesterday
    prev_score  = prev_health.get("regime_score", total_score)
    prev_regime = prev_health.get("regime", regime)
    if   total_score > prev_score + 3:  trend = "IMPROVING"
    elif total_score < prev_score - 3:  trend = "DETERIORATING"
    else:                               trend = "STABLE"

    health = {
        # v2 standard output (read by all other agents)
        "date":          today,
        "regime":        regime,
        "cash_floor":    cash_floor,
        "max_deployed":  max_deployed,
        "leaders_ok":    leaders_ok,
        "bigshot_ok":    bigshot_ok,
        "spy_td_signal": td_signals.get("SPY", "Neutral"),
        "qqq_td_signal": td_signals.get("QQQ", "Neutral"),
        "distribution_days": dist_days,
        # Actual distribution day dates in last 25 sessions (for CIO audit)
        # CIO can review these to validate -0.2% + higher volume threshold
        "distribution_day_dates": fb_detail.get("dist_day_dates", []),
        # Real breadth: % of 1,325-stock universe above 50DMA / 200DMA (from SQLite)
        # NOTE: pct_above_50dma was previously SPY's % above its own 50DMA (a price metric,
        # not breadth). Fixed 2026-05-22 — now reads actual universe breadth from ohlcv table.
        "pct_above_50dma":   _db_breadth.get("pct_above_50dma",  round(fc_detail.get("spy_pct_above_50dma", 0), 1)),
        "pct_above_200dma":  _db_breadth.get("pct_above_200dma", None),
        "breadth_n_sample":  _db_breadth.get("n_breadth_sample", 0),
        # BOA-001-A1: Leader internals — % of Top30 watchlist below 50DMA
        "breakout_failure_rate": _breakout_failure_rate,

        # BOA-001-A2: New 52W High / New 52W Low counts (ticker_meta, n_bars>=252)
        # Formula: nh_nl_ratio = nh/(nh+nl) [BOA-002 standard practitioner]
        # HYPOTHESIS — informational only until N≥50 trades validates regime impact
        "nh_count":         _nh_nl.get("nh_count"),
        "nl_count":         _nh_nl.get("nl_count"),
        "nh_nl_ratio":      _nh_nl.get("nh_nl_ratio"),
        "nh_nl_net_pct":    _nh_nl.get("nh_nl_net_pct"),
        "nh_acceleration":  _nh_nl.get("nh_acceleration"),   # +ve = breadth improving
        "nh_nl_divergence": _nh_nl.get("nh_nl_divergence", False),

        # BOA-001-A3: Cumulative Advance-Decline line
        "ad_line_today":   _ad_data.get("ad_line_today"),
        "ad_direction":    _ad_data.get("ad_direction", "Unknown"),
        "ad_slope_20d":         _ad_data.get("ad_slope_20d"),
        "ad_slope_days_actual": _ad_data.get("ad_slope_days_actual"),
        "ad_divergence":   _ad_data.get("ad_divergence", False),
        "ad_advances":     _ad_data.get("ad_advances"),
        "ad_declines":     _ad_data.get("ad_declines"),

        # BOA-004-A2: Zweig advance ratio — INFORMATIONAL, zero regime weight until N≥20
        "adv_ratio_today":        _breadth_signals.get("adv_ratio_today"),
        "adv_ratio_ema10":        _breadth_signals.get("adv_ratio_ema10"),
        "extreme_selling_breadth": _breadth_signals.get("extreme_selling_breadth", False),
        "breadth_expansion":      _breadth_signals.get("breadth_expansion", False),
        "zweig_breadth_thrust":   _breadth_signals.get("zweig_breadth_thrust", False),

        # BOA-004-A3: Bollinger climax signals — INFORMATIONAL, zero regime weight
        "bb_above_pct":            _breadth_signals.get("bb_above_pct"),
        "broad_breadth_expansion": _breadth_signals.get("broad_breadth_expansion", False),
        "bb_below_pct":            _breadth_signals.get("bb_below_pct"),
        "breadth_panic":           _breadth_signals.get("breadth_panic", False),
        "distribution_breadth_pct": _breadth_signals.get("distribution_breadth_pct"),

        # BOA-001-A6: Breadth Health Score — composite 0-100
        # >70=Markup confirmation | 40-70=Neutral | <40=Distribution warning
        # NH/NL component uses adv_ratio_ema10 as proxy until BOA-002 resolves
        "breadth_health_score": _breadth_health_score,

        # BOA-004-A6: Follow-Through Day watch mode (re-entry signal post breadth_panic)
        "watch_for_ftd":  _ftd.get("watch_for_ftd", False),
        "ftd_rally_day":  _ftd.get("ftd_rally_day", 0),
        "ftd_triggered":  _ftd.get("ftd_triggered", False),
        "ftd_low_date":   _ftd.get("ftd_low_date"),
        # DA Analyst fix 2026-05-22: renamed spy_pct_above_50dma → spy_vs_50dma_pct
        # to clarify: this is SPY's price % above its own 50DMA (a price metric),
        # NOT the % of stocks above their 50DMA (which is pct_above_50dma above)
        "spy_vs_50dma_pct": round(fc_detail.get("spy_pct_above_50dma", 0), 1),

        # RSP/SPY Concentration Signal (June 2026)
        # BROADENING = RSP outperforms SPY 20d ROC by >1.5pp → large AND small caps up → confirm Markup
        # NARROWING  = SPY outperforms RSP 20d ROC by >1.5pp → gains concentrated → fragility warning
        # NEUTRAL    = within ±1.5pp → no strong signal
        # cap_eq_div_50d = cap-weighted % above 50DMA minus equal-weight % (informational, magnitude of concentration)
        # Research basis: Goldman Sachs (May 2026), Humble Student of Markets, RSP/SPY ratio methodology
        # Equal-weight breadth stays primary — this signal ADDS non-redundant concentration context.
        "concentration_flag":   _conc.get("concentration_flag", "UNKNOWN"),
        "rsp_spy_20d_roc_diff": _conc.get("rsp_spy_20d_roc_diff"),   # + = RSP outperforming
        "rsp_20d_roc":          _conc.get("rsp_20d_roc"),
        "spy_20d_roc":          _conc.get("spy_20d_roc"),
        "cap_eq_div_50d":       _conc.get("cap_eq_div_50d"),   # informational: +25 = large caps 25pp ahead

        "regime_note":   regime_note,

        # Internal scoring detail (not required by other agents but useful for debugging)
        "regime_score":  effective_score,  # post-clamp score (matches what regime_note shows)
        "regime_trend":  trend,
        "prior_regime":  prev_regime,
        "factor_scores": {
            "A_structure":    {"score": fa_score, "max": 25},
            "B_distribution": {"score": fb_score, "max": 20},
            "C_breadth":      {"score": fc_score, "max": 20},
            "E_rs_leaders":   {"score": fe_score, "max": 10},
            "F_intermarket":  {"score": ff_score, "max": 5},
            "G_sentiment":    {"score": fg_score, "max": 5},
        },
        "early_warnings": warnings,
        "early_warning_count": warn_count,

        # Macro snapshot
        "macro": {
            "yield_10y":   round(yield_10y,   3) if yield_10y   else None,
            "yield_curve": round(yield_curve,  3) if yield_curve else None,
            "hy_spread":   round(hy_spread,    2) if hy_spread   else None,
            "fed_funds":   round(fed_funds,    2) if fed_funds   else None,
            "real_yield":  round(real_yield,   3) if real_yield  else None,
            "cpi_yoy":     cpi_yoy,
        },
        "liquidity_gate":     liq_gate,
        "liquidity_cautions": liq_cautions,
        "liquidity_notes":    liq_notes,

        # Challenger
        "regime_challenge": regime_challenge,

        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    HEALTH_FILE.write_text(json.dumps(health, indent=2, default=str), encoding="utf-8")

    # Update history (BOA-001-A0: dedup — remove today's entry before append)
    hist = _load_json(HISTORY_FILE) if HISTORY_FILE.exists() else {"history": []}
    entries = hist.setdefault("history", [])
    # Remove any same-date duplicates before appending (idempotent re-runs)
    entries[:] = [e for e in entries if e.get("date") != today]
    entries.append({
        "date":            today,
        "regime":          regime,
        "score":           total_score,
        "cash_floor":      cash_floor,
        "warnings":        warn_count,
        "adv_ratio_ema10": _breadth_signals.get("adv_ratio_ema10"),  # ZBT lookback
        "nh_nl_ratio":     _nh_nl.get("nh_nl_ratio"),                # NH/NL acceleration lookback
    })
    hist["history"] = entries[-90:]
    HISTORY_FILE.write_text(json.dumps(hist, indent=2), encoding="utf-8")

    # Console summary
    emoji = {"Markup": "[BULL]", "Sideways": "[CHOP]", "Distribution": "[DIST]", "Markdown": "[BEAR]"}
    print(f"\n  {emoji.get(regime, '[?]')} REGIME: {regime}")
    print(f"  Score: {effective_score}/85 (raw={total_score}) | Cash floor: {int(cash_floor*100)}% | Max deployed: {int(max_deployed*100)}%")
    print(f"  Leaders: {'OK' if leaders_ok else 'BLOCKED'} | BigShot: {'OK' if bigshot_ok else 'BLOCKED'}")
    print(f"  TD: SPY={td_signals['SPY']} QQQ={td_signals['QQQ']}")
    print(f"  Liquidity gate: {liq_gate} ({liq_cautions}/5 cautions)")
    if warnings:
        print(f"  [!] Leading warnings ({warn_count}):")
        for w in warnings:
            print(f"    - {w}")
    print(f"  -> Written: {HEALTH_FILE}")

    return health


if __name__ == "__main__":
    run()
