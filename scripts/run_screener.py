#!/usr/bin/env python3
"""
AlphaAbsolute — Agent 3: PULSE screener
Framework: NRGC + PULSE v2.0 | Health Check Dashboard + 6-Phase Multibagger Theory

Runs 3 parallel screens:
  A) Leader / Momentum  — Minervini Trend Template + PULSE quantitative
  B) Bottom Fishing     — Wyckoff Spring / SOS + RS inflection
  C) Hypergrowth        — Base 0/1, RS acceleration, revenue catalyst

Data source: yfinance (Yahoo Finance) — no API key required
Output: data/screener_YYMMDD.json + output/screener_YYMMDD.md

Usage:
  python scripts/run_screener.py
  python scripts/run_screener.py --universe extended   (larger stock list)
  python scripts/run_screener.py --max 20              (max candidates per screen)
"""

import json
import sys
import argparse
import urllib3
import requests
from datetime import datetime, date
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
requests.packages.urllib3.disable_warnings()  # type: ignore

ROOT     = Path(__file__).parent.parent

# ── Central SSL patch (Cloudflare WARP bypass) ─────────────────────────────────
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "paper_trading"))
try:
    from utils.ssl_patch import apply as _ssl_apply
    _ssl_apply()
except Exception:
    pass
DATA_DIR = ROOT / "data"
OUT_DIR  = ROOT / "output"
DATA_DIR.mkdir(exist_ok=True)
OUT_DIR.mkdir(exist_ok=True)

TODAY     = date.today()
YYMMDD    = TODAY.strftime("%y%m%d")
TODAY_STR = TODAY.isoformat()


# ════════════════════════════════════════════════════════════════════
# UNIVERSE — tickers to screen
# ════════════════════════════════════════════════════════════════════

UNIVERSE_BASE = [
    # AI / Semiconductors
    "NVDA", "AMD", "AVGO", "ANET", "MRVL", "SMCI", "MU",
    # Photonics / Optical
    "CIEN", "COHR", "AAOI", "MRVL", "LITE", "FNSR",
    # DefenseTech
    "PLTR", "CACI", "AXON", "LDOS", "SAIC",
    # Space
    "RKLB", "LUNR", "ASTS", "MNTS",
    # Nuclear / Energy
    "VST", "OKLO", "NNE", "SMR", "CEG",
    # NeoCloud / Data Center
    "CRWV", "DELL", "HPE", "VRT", "ETN",
    # AI Applications
    "CRM", "SNOW", "DDOG", "NET", "GTLB",
    # Robotics / Automation
    "ISRG", "TER", "BRKS",
    # Drone / UAV
    "ACHR", "JOBY", "AVAV",
    # Quantum Computing
    "IONQ", "RGTI", "QUBT",
    # Growth / Momentum
    "TSLA", "SHOP", "MELI", "APP", "AXON",
    # Broader indices (for breadth check)
    "^GSPC", "^IXIC", "^SOX",
]

UNIVERSE_EXTENDED = UNIVERSE_BASE + [
    "NFLX", "META", "GOOGL", "MSFT", "AMZN", "AAPL",
    "TSM", "ASML", "LRCX", "KLAC", "AMAT",
    "PANW", "ZS", "CRWD", "S", "FTNT",
    "UBER", "LYFT", "ABNB", "BKNG",
    "LLY", "ABBV", "MRNA", "REGN",
    "GEV", "PWR", "FLEX",
    "RCAT", "ACHR", "JOBY",
]

# Theme mapping (for labeling candidates)
THEME_MAP = {
    "NVDA": "AI-Related / AI Infrastructure",    "AMD":  "AI-Related",
    "AVGO": "AI Infrastructure",                  "ANET": "AI Infrastructure",
    "MRVL": "Photonics / AI Infrastructure",      "MU":   "Memory / HBM",
    "SMCI": "AI Infrastructure / NeoCloud",       "CIEN": "Photonics",
    "COHR": "Photonics",                          "AAOI": "Photonics",
    "LITE": "Photonics",                          "PLTR": "DefenseTech / AI",
    "CACI": "DefenseTech",                        "AXON": "DefenseTech",
    "RKLB": "Space",                              "LUNR": "Space",
    "ASTS": "Space / Connectivity",               "VST":  "Nuclear / AI Infrastructure",
    "OKLO": "Nuclear / SMR",                      "NNE":  "Nuclear / SMR",
    "SMR":  "Nuclear / SMR",                      "CEG":  "Nuclear",
    "CRWV": "NeoCloud",                           "VRT":  "AI Infrastructure",
    "ETN":  "AI Infrastructure / Data Center",    "IONQ": "Quantum Computing",
    "RGTI": "Quantum Computing",                  "QUBT": "Quantum Computing",
    "ISRG": "Robotics",                           "TER":  "Robotics",
    "ACHR": "Drone / UAV",                        "JOBY": "Drone / UAV",
    "TSLA": "AI-Related / Robotics",              "DELL": "AI Infrastructure",
    "APP":  "AI-Related",                         "NET":  "DefenseTech / NeoCloud",
}


# ════════════════════════════════════════════════════════════════════
# DATA FETCHING
# ════════════════════════════════════════════════════════════════════

def patch_yfinance_ssl():
    """Disable SSL verification for corporate proxy."""
    try:
        from curl_cffi import requests as _cffi
        import yfinance.data as _yfd
        _orig = _yfd.YfData.__init__
        def _patched(self, session=None):
            _orig(self, session=_cffi.Session(impersonate="chrome", verify=False))
        _yfd.YfData.__init__ = _patched
        return True
    except Exception:
        return False


def _pe_get(ticker: str, period: str = "1y"):
    """Fetch price data via portfolio_engine (SSL-safe, always works)."""
    try:
        from portfolio_engine import get_price_data
        return get_price_data(ticker, period=period)
    except Exception:
        return None


def fetch_spy_benchmark() -> dict:
    """Fetch SPY benchmark returns for RS calculation (portfolio_engine source)."""
    try:
        df = _pe_get("SPY", "1y")
        if df is None or df.empty:
            return {"ret_6m": 0, "ret_3m": 0, "ret_1m": 0, "ret_1w": 0}
        c = df["Close"]
        return {
            "ret_6m": (c.iloc[-1] / c.iloc[-130] - 1) * 100 if len(c) >= 130 else 0,
            "ret_3m": (c.iloc[-1] / c.iloc[-66]  - 1) * 100 if len(c) >= 66  else 0,
            "ret_1m": (c.iloc[-1] / c.iloc[-22]  - 1) * 100 if len(c) >= 22  else 0,
            "ret_1w": (c.iloc[-1] / c.iloc[-5]   - 1) * 100 if len(c) >= 5   else 0,
        }
    except Exception as e:
        print(f"  [!] SPY benchmark fetch failed: {e}")
        return {"ret_6m": 0, "ret_3m": 0, "ret_1m": 0, "ret_1w": 0}


def fetch_ticker_data(ticker: str, spy: dict) -> dict:
    """Fetch all metrics for one ticker (portfolio_engine source — SSL safe)."""
    try:
        df = _pe_get(ticker, "1y")
        if df is None or df.empty:
            return {"ticker": ticker, "error": "no data"}

        c   = df["Close"]
        h   = df["High"]
        l   = df["Low"]
        v   = df["Volume"]

        price     = round(float(c.iloc[-1]), 2)
        prev      = round(float(c.iloc[-2]), 2) if len(c) > 1 else price
        chg_1d    = round((price / prev - 1) * 100, 2)

        # Returns
        ret_6m = round((c.iloc[-1] / c.iloc[-130] - 1) * 100, 1) if len(c) >= 130 else None
        ret_3m = round((c.iloc[-1] / c.iloc[-66]  - 1) * 100, 1) if len(c) >= 66  else None
        ret_1m = round((c.iloc[-1] / c.iloc[-22]  - 1) * 100, 1) if len(c) >= 22  else None
        ret_1w = round((c.iloc[-1] / c.iloc[-5]   - 1) * 100, 1) if len(c) >= 5   else None

        # RS vs SPY
        rs_6m = round(ret_6m - spy.get("ret_6m", 0), 1) if ret_6m is not None else None
        rs_3m = round(ret_3m - spy.get("ret_3m", 0), 1) if ret_3m is not None else None
        rs_1m = round(ret_1m - spy.get("ret_1m", 0), 1) if ret_1m is not None else None
        rs_1w = round(ret_1w - spy.get("ret_1w", 0), 1) if ret_1w is not None else None

        # RS Momentum ratio (1M vs 3M — acceleration indicator)
        rs_momentum = None
        if rs_1m is not None and rs_3m is not None and rs_3m != 0:
            rs_momentum = round((rs_1m + 50) / (rs_3m + 50), 3)

        # 52W stats
        high_52w     = round(float(h.tail(252).max()), 2)
        low_52w      = round(float(l.tail(252).min()), 2)
        pct_from_high = round((price / high_52w - 1) * 100, 1)
        pct_from_low  = round((price / low_52w  - 1) * 100, 1)

        # Moving averages
        ma50  = round(float(c.tail(50).mean()),  2) if len(c) >= 50  else None
        ma150 = round(float(c.tail(150).mean()), 2) if len(c) >= 150 else None
        ma200 = round(float(c.tail(200).mean()), 2) if len(c) >= 200 else None
        ma10  = round(float(c.tail(10).mean()),  2) if len(c) >= 10  else None
        ma30w = round(float(c.tail(150).mean()), 2) if len(c) >= 150 else None  # ~30 weeks

        # Stage 2 (Weinstein)
        stage2 = None
        if ma150 and ma200:
            stage2 = (ma150 > ma200) and (price > ma150)

        # MA trend (150 rising?)
        ma150_20d_ago = round(float(c.tail(170).head(20).mean()), 2) if len(c) >= 170 else None
        ma150_rising  = (ma150 > ma150_20d_ago) if (ma150 and ma150_20d_ago) else None

        # Volume metrics
        avg_vol_20d = float(v.tail(20).mean())
        vol_today   = float(v.iloc[-1])
        vol_ratio   = round(vol_today / avg_vol_20d, 2) if avg_vol_20d > 0 else None

        # Volume contraction (VCP proxy — last 5d vol vs 20d avg)
        avg_vol_5d  = float(v.tail(5).mean()) if len(v) >= 5 else avg_vol_20d
        vol_contraction = round(avg_vol_5d / avg_vol_20d, 2) if avg_vol_20d > 0 else None

        # Tightness (VCP proxy — daily range compression)
        ranges_5d  = [(float(h.iloc[i]) - float(l.iloc[i])) / float(c.iloc[i])
                      for i in range(-5, 0)] if len(c) >= 5 else []
        ranges_20d = [(float(h.iloc[i]) - float(l.iloc[i])) / float(c.iloc[i])
                      for i in range(-20, 0)] if len(c) >= 20 else []
        tightness_ratio = (sum(ranges_5d) / len(ranges_5d)) / (sum(ranges_20d) / len(ranges_20d)) \
                           if ranges_5d and ranges_20d and sum(ranges_20d) > 0 else None

        # Pullback depth (from recent local high — Spring proxy)
        recent_high = float(h.tail(20).max())
        pullback_pct = round((price / recent_high - 1) * 100, 1)

        # PULSE checklist (quantitative PULSE criteria)
        pulse = {
            "from_52w_high_ok":   pct_from_high >= -20,
            "from_52w_low_ok":    pct_from_low  >= 15,
            "above_ma50_ok":      (price / ma50 - 1) * 100 >= -5 if ma50 else None,
            "ma150_above_ma200":  (ma150 > ma200) if (ma150 and ma200) else None,
            "rs_6m_positive":     rs_6m > 0 if rs_6m is not None else None,
            "stage2_ok":          stage2,
        }
        pulse_pass = sum(1 for v_ in pulse.values() if v_ is True)
        pulse_total = sum(1 for v_ in pulse.values() if v_ is not None)

        return {
            "ticker":           ticker,
            "theme":            THEME_MAP.get(ticker, "Other"),
            "price":            price,
            "change_1d_pct":    chg_1d,
            "ret_1w":           ret_1w,
            "ret_1m":           ret_1m,
            "ret_3m":           ret_3m,
            "ret_6m":           ret_6m,
            "rs_1w":            rs_1w,
            "rs_1m":            rs_1m,
            "rs_3m":            rs_3m,
            "rs_6m":            rs_6m,
            "rs_momentum":      rs_momentum,
            "high_52w":         high_52w,
            "low_52w":          low_52w,
            "pct_from_high":    pct_from_high,
            "pct_from_low":     pct_from_low,
            "ma50":             ma50,
            "ma150":            ma150,
            "ma200":            ma200,
            "ma10":             ma10,
            "ma30w":            ma30w,
            "stage2":           stage2,
            "ma150_rising":     ma150_rising,
            "vol_ratio":        vol_ratio,
            "vol_contraction":  vol_contraction,
            "tightness_ratio":  tightness_ratio,
            "pullback_pct":     pullback_pct,
            "pulse":            pulse,
            "pulse_pass":       pulse_pass,
            "pulse_total":      pulse_total,
            "verified":         True,
            "source":           "yfinance",
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# ════════════════════════════════════════════════════════════════════
# SCREEN A: LEADER / MOMENTUM (Minervini Trend Template + PULSE)
# ════════════════════════════════════════════════════════════════════

def screen_leader(data: dict) -> dict:
    """
    Minervini Trend Template (8 criteria) + PULSE RS filter.
    Returns score + pass/fail verdict.
    """
    p = data.get("price")
    if not p or data.get("error"):
        return {"pass": False, "score": 0, "reason": "No data"}

    checks = {}
    reasons_fail = []

    # 1. Price > MA50
    ma50 = data.get("ma50")
    checks["above_ma50"] = p > ma50 if ma50 else None
    if ma50 and p <= ma50:
        reasons_fail.append(f"Price ${p:.2f} below MA50 ${ma50:.2f}")

    # 2. Price > MA150
    ma150 = data.get("ma150")
    checks["above_ma150"] = p > ma150 if ma150 else None
    if ma150 and p <= ma150:
        reasons_fail.append(f"Price below MA150 ${ma150:.2f}")

    # 3. Price > MA200
    ma200 = data.get("ma200")
    checks["above_ma200"] = p > ma200 if ma200 else None
    if ma200 and p <= ma200:
        reasons_fail.append(f"Price below MA200 ${ma200:.2f}")

    # 4. MA150 > MA200
    checks["ma150_above_ma200"] = (ma150 > ma200) if (ma150 and ma200) else None
    if ma150 and ma200 and ma150 <= ma200:
        reasons_fail.append(f"MA150 ${ma150:.2f} not above MA200 ${ma200:.2f}")

    # 5. MA150 trending up (proxy)
    checks["ma150_rising"] = data.get("ma150_rising")
    if data.get("ma150_rising") is False:
        reasons_fail.append("MA150 not rising")

    # 6. % from 52W high > -25% (more lenient than -20% for wider catch)
    fh = data.get("pct_from_high")
    checks["from_52w_high"] = fh >= -25 if fh is not None else None
    if fh is not None and fh < -25:
        reasons_fail.append(f"{fh:.1f}% from 52W high (need > -25%)")

    # 7. % from 52W low > +15%
    fl = data.get("pct_from_low")
    checks["from_52w_low"] = fl >= 15 if fl is not None else None
    if fl is not None and fl < 15:
        reasons_fail.append(f"+{fl:.1f}% from 52W low (need > +15%)")

    # 8. RS 6M > 0 (outperforming SPY)
    rs6 = data.get("rs_6m")
    checks["rs_6m_positive"] = rs6 > 0 if rs6 is not None else None
    if rs6 is not None and rs6 <= 0:
        reasons_fail.append(f"RS 6M {rs6:.1f}% (underperforming SPY)")

    # 9. RS momentum (1M gaining vs 3M)
    rs_mom = data.get("rs_momentum")
    checks["rs_momentum_ok"] = rs_mom >= 0.9 if rs_mom is not None else None

    # 10. Stage 2
    checks["stage2"] = data.get("stage2")
    if not data.get("stage2"):
        reasons_fail.append("Not Stage 2 (price below or MA200 declining)")

    # Score (weighted)
    score = 0
    if checks.get("rs_6m_positive"):     score += 25
    if checks.get("stage2"):             score += 20
    if checks.get("above_ma50"):         score += 10
    if checks.get("above_ma150"):        score += 10
    if checks.get("above_ma200"):        score += 10
    if checks.get("ma150_above_ma200"):  score += 10
    if checks.get("ma150_rising"):       score += 5
    if checks.get("from_52w_high"):      score += 5
    if checks.get("from_52w_low"):       score += 3
    if checks.get("rs_momentum_ok"):     score += 2

    # RS bonus
    if rs6:
        if rs6 > 50:   score += 15
        elif rs6 > 20: score += 8
        elif rs6 > 10: score += 4

    # VCP bonus (volume contraction + tightness)
    vol_c = data.get("vol_contraction")
    tight = data.get("tightness_ratio")
    vcp_signal = None
    if vol_c is not None and tight is not None:
        if vol_c < 0.7 and tight < 0.7:
            vcp_signal = "VCP: tight + low volume (setup forming)"
            score += 10
        elif vol_c < 0.85 and tight < 0.85:
            vcp_signal = "VCP: moderately tight"
            score += 5

    # Must pass: Stage 2 + RS 6M positive + not too far from high
    # Use bool() to handle numpy booleans (np.True_ is not True fails the 'is' test)
    hard_pass = (
        bool(checks.get("stage2")) and
        bool(checks.get("rs_6m_positive")) and
        bool(checks.get("from_52w_high"))
    )

    return {
        "pass":        hard_pass,
        "score":       score,
        "checks":      checks,
        "vcp_signal":  vcp_signal,
        "fail_reasons": reasons_fail[:3],
    }


# ════════════════════════════════════════════════════════════════════
# SCREEN B: BOTTOM FISHING (Wyckoff Spring + RS Inflection)
# ════════════════════════════════════════════════════════════════════

def screen_bottom_fish(data: dict) -> dict:
    """
    Wyckoff accumulation signals + Stage 1→2 transition.
    Stage MUST be 1 (forming base) or early Stage 2.
    """
    p = data.get("price")
    if not p or data.get("error"):
        return {"pass": False, "score": 0, "reason": "No data"}

    checks = {}
    reasons_fail = []

    rs6   = data.get("rs_6m")
    rs1m  = data.get("rs_1m")
    rs3m  = data.get("rs_3m")
    rs_mom = data.get("rs_momentum")
    fh    = data.get("pct_from_high")
    fl    = data.get("pct_from_low")
    ma150 = data.get("ma150")
    ma200 = data.get("ma200")
    vol_r = data.get("vol_ratio")
    pull  = data.get("pullback_pct")
    stage2 = data.get("stage2")
    chg1d  = data.get("change_1d_pct") or 0

    # RS inflection: 1M RS improving vs 3M RS
    checks["rs_inflecting"] = rs_mom > 1.05 if rs_mom is not None else None
    if rs_mom and rs_mom <= 1.05:
        reasons_fail.append(f"RS not inflecting (momentum {rs_mom:.2f})")

    # Price near 52W low (Spring zone)
    checks["near_52w_low"] = (fl is not None and fl <= 40)
    if fl and fl > 40:
        reasons_fail.append(f"+{fl:.0f}% from 52W low — too extended for Bottom Fish")

    # RS 6M negative / flat (underperformer about to turn)
    checks["rs_6m_low"] = rs6 < 20 if rs6 is not None else None

    # Volume SOS proxy: high vol today on positive day
    sos_signal = None
    if vol_r and chg1d:
        if vol_r > 1.5 and chg1d > 1:
            sos_signal = f"SOS: vol {vol_r:.1f}x avg, up {chg1d:+.1f}%"
            checks["sos_volume"] = True
        elif vol_r < 0.6 and chg1d > -0.3:
            sos_signal = f"Dry-up / Spring: vol {vol_r:.1f}x avg — accumulation"
            checks["sos_volume"] = True
        else:
            checks["sos_volume"] = False
    else:
        checks["sos_volume"] = None

    # Stage 1 or early Stage 2 transition
    near_ma200 = (p / ma200 - 1) * 100 if ma200 else None
    checks["stage_1_to_2"] = (near_ma200 is not None and abs(near_ma200) < 15) if near_ma200 is not None else None
    if near_ma200 is not None and abs(near_ma200) >= 15:
        reasons_fail.append(f"Not near Stage 1/2 transition (vs MA200: {near_ma200:+.1f}%)")

    # Score
    score = 0
    if checks.get("rs_inflecting"):     score += 25
    if checks.get("sos_volume"):        score += 20
    if checks.get("near_52w_low"):      score += 15
    if checks.get("stage_1_to_2"):      score += 15
    if checks.get("rs_6m_low"):         score += 10
    if rs_mom and rs_mom > 1.1:         score += 10
    if pull and pull > -8 and pull < 0: score += 5   # shallow pullback

    # Hard pass: RS inflecting + near 52W low territory + some volume signal
    hard_pass = (
        bool(checks.get("rs_inflecting")) and
        bool(checks.get("near_52w_low")) and
        (bool(checks.get("stage_1_to_2")) or bool(checks.get("sos_volume")))
    )

    return {
        "pass":        hard_pass,
        "score":       score,
        "checks":      checks,
        "sos_signal":  sos_signal,
        "fail_reasons": reasons_fail[:3],
    }


# ════════════════════════════════════════════════════════════════════
# SCREEN C: HYPERGROWTH (Base 0/1 + RS Acceleration)
# ════════════════════════════════════════════════════════════════════

def screen_hypergrowth(data: dict) -> dict:
    """
    Early-stage breakout + RS acceleration + structural megatrend.
    Requires Stage 2 + strong RS momentum + early base (not extended).
    """
    p = data.get("price")
    if not p or data.get("error"):
        return {"pass": False, "score": 0, "reason": "No data"}

    checks = {}
    reasons_fail = []

    rs6    = data.get("rs_6m")
    rs1m   = data.get("rs_1m")
    rs3m   = data.get("rs_3m")
    rs_mom = data.get("rs_momentum")
    fh     = data.get("pct_from_high")
    fl     = data.get("pct_from_low")
    stage2 = data.get("stage2")
    vol_r  = data.get("vol_ratio")
    vol_c  = data.get("vol_contraction")
    tight  = data.get("tightness_ratio")
    chg1d  = data.get("change_1d_pct") or 0
    theme  = data.get("theme", "")

    # Must be Stage 2
    checks["stage2"] = stage2
    if not stage2:
        reasons_fail.append("Not Stage 2")

    # RS 6M must be strong (>20% vs SPY) — already outperforming
    checks["rs_6m_strong"] = rs6 > 20 if rs6 is not None else None
    if rs6 is not None and rs6 <= 20:
        reasons_fail.append(f"RS 6M {rs6:.0f}% — need >20% for hypergrowth")

    # RS acceleration (1M > 3M)
    checks["rs_accelerating"] = rs_mom > 1.05 if rs_mom is not None else None
    if rs_mom and rs_mom <= 1.05:
        reasons_fail.append(f"RS not accelerating (mom {rs_mom:.2f})")

    # Not too extended from 52W high (early in base)
    checks["not_extended"] = fh >= -30 if fh is not None else None
    if fh is not None and fh < -30:
        reasons_fail.append(f"{fh:.0f}% from 52W high — too extended")

    # Megatrend theme bonus
    hyper_themes = [
        "AI", "Memory", "Photonics", "Space", "Nuclear",
        "NeoCloud", "DefenseTech", "Quantum", "Robotics"
    ]
    checks["hyper_theme"] = any(t.lower() in theme.lower() for t in hyper_themes)

    # Volume dry-up (base forming) OR SOS (breakout)
    base_signal = None
    if vol_c is not None and tight is not None:
        if vol_c < 0.6 and tight < 0.6:
            base_signal = f"Base forming: vol {vol_c:.1f}x 20d avg, tight range"
            checks["base_forming"] = True
        elif vol_r and vol_r > 1.8 and chg1d > 3:
            base_signal = f"Breakout: vol {vol_r:.1f}x avg, up {chg1d:+.1f}%"
            checks["base_forming"] = True
        else:
            checks["base_forming"] = False
    else:
        checks["base_forming"] = None

    # Score
    score = 0
    if checks.get("stage2"):           score += 20
    if checks.get("rs_6m_strong"):     score += 20
    if checks.get("rs_accelerating"):  score += 20
    if checks.get("not_extended"):     score += 10
    if checks.get("hyper_theme"):      score += 10
    if checks.get("base_forming"):     score += 10
    if rs6 and rs6 > 100:              score += 15
    elif rs6 and rs6 > 50:             score += 8
    if rs_mom and rs_mom > 1.15:       score += 10

    # Hard pass: Stage 2 + RS strong + RS accelerating
    hard_pass = (
        bool(checks.get("stage2")) and
        bool(checks.get("rs_6m_strong")) and
        bool(checks.get("rs_accelerating"))
    )

    return {
        "pass":         hard_pass,
        "score":        score,
        "checks":       checks,
        "base_signal":  base_signal,
        "fail_reasons": reasons_fail[:3],
    }


# ════════════════════════════════════════════════════════════════════
# WYCKOFF GATE CHECK (global rule — every recommendation must pass)
# ════════════════════════════════════════════════════════════════════

def wyckoff_gate(data: dict, setup_type: str) -> dict:
    """
    Global Wyckoff × Weinstein Stage Gate.
    Must pass before any BUY recommendation is made.
    """
    stage2 = data.get("stage2")
    rs6    = data.get("rs_6m")
    rs_mom = data.get("rs_momentum")
    fh     = data.get("pct_from_high")
    vol_r  = data.get("vol_ratio")
    chg1d  = data.get("change_1d_pct") or 0
    pullback = data.get("pullback_pct")

    # Weinstein Stage
    if stage2 is True:
        weinstein = "Stage 2 [PASS]"
        stage_pass = True
    elif stage2 is False:
        weinstein = "Stage 3/4 [FAIL — do not buy]"
        stage_pass = False
    else:
        weinstein = "Stage 1 (basing) [OK for Bottom Fish only]"
        stage_pass = setup_type == "Bottom Fishing"

    # Wyckoff signal detection (simplified — volume-based)
    wyckoff_signal = "None identified"
    if vol_r and chg1d:
        if vol_r > 1.8 and chg1d > 2:
            wyckoff_signal = "SOS (Sign of Strength) — expanding volume on up day"
        elif vol_r < 0.6 and abs(chg1d) < 0.5:
            wyckoff_signal = "LPS / VCP — low volume quiet pullback"
        elif vol_r < 0.6 and chg1d < -0.5:
            wyckoff_signal = "Spring / Dry-up — volume shrinking on down move"
        elif fh and fh > -3:
            wyckoff_signal = "Late Markup / ATH zone"
        elif rs_mom and rs_mom > 1.1 and chg1d > 0:
            wyckoff_signal = "Early Markup / CHoCH (Change of Character)"

    # Wyckoff phase
    if fh and fh > -5:
        wyckoff_phase = "Mark-Up / Distribution boundary — watch for UTAD"
    elif rs_mom and rs_mom > 1.0 and stage2:
        wyckoff_phase = "Mark-Up (mid) — SOS confirmed"
    elif stage2 is False and fh and fh < -20:
        wyckoff_phase = "Mark-Down / Possible Accumulation Phase A"
    elif pullback and -10 < pullback < -2:
        wyckoff_phase = "Possible LPS / Accumulation Phase D"
    elif pullback and pullback < -10:
        wyckoff_phase = "Possible Spring / Accumulation Phase C"
    else:
        wyckoff_phase = "Accumulation (phase unclear)"

    # Gate verdict
    if stage_pass and (vol_r is None or vol_r >= 0):
        if stage2:
            gate = "GREEN — proceed per setup rules"
        else:
            gate = "YELLOW — Stage 1/watch for transition signal"
    else:
        gate = "RED — do not buy (Stage 3/4 or criteria not met)"

    return {
        "weinstein_stage": weinstein,
        "wyckoff_phase":   wyckoff_phase,
        "wyckoff_signal":  wyckoff_signal,
        "gate_verdict":    gate,
        "stage_pass":      stage_pass,
    }


# ════════════════════════════════════════════════════════════════════
# UNIVERSE RANKING + PHASE CHANGERS
# ════════════════════════════════════════════════════════════════════

def compute_universe_score(d: dict) -> float:
    """
    Composite PULSE rank score for every stock in the universe.
    Higher = better overall positioning (RS + Stage + momentum).
    """
    rs6  = d.get("rs_6m") or 0
    rs1  = d.get("rs_1m") or 0
    mom  = d.get("rs_momentum") or 1.0
    stage2 = bool(d.get("stage2"))
    fh   = d.get("pct_from_high") or -50
    pulse = d.get("pulse_pass") or 0
    vol_r = d.get("vol_ratio") or 1.0
    chg1d = d.get("change_1d_pct") or 0

    score = 0
    score += min(max(rs6, -100), 300) * 0.35    # RS 6M — primary driver
    score += min(max(rs1, -100), 200) * 0.25    # RS 1M — recency weight
    score += (mom - 1.0) * 25                   # RS momentum ratio (1M vs 3M)
    score += 20 if stage2 else -15              # Stage 2 hard bonus/penalty
    score += max(0, 25 + fh) * 0.35            # Closeness to 52W high
    score += pulse * 3                          # PULSE checks
    # Volume bonus (SOS signal)
    if vol_r and vol_r > 1.8 and chg1d > 2:
        score += 8
    return round(score, 1)


def compute_phase_changes(raw_data: dict) -> dict:
    """
    Identify RS momentum accelerators and decelerators.
    Delta = RS percentile rank 1M minus RS percentile rank 3M (positive = gaining ranks, negative = losing ranks).
    Shown as rank count change, e.g. +12 means moved up 12 spots in the universe ranking.
    """
    changes = []
    for ticker, d in raw_data.items():
        if d.get("error"):
            continue
        # Prefer percentile rank delta (enriched by run_screens before this call)
        delta = d.get("rs_pct_delta")
        if delta is None:
            rs1 = d.get("rs_1m"); rs3 = d.get("rs_3m")
            if rs1 is None or rs3 is None: continue
            delta = round(rs1 - rs3, 1)
        else:
            delta = round(delta, 1)
        changes.append({
            "ticker":  ticker,
            "theme":   d.get("theme", ""),
            "rs_pct_3m": d.get("rs_pct_3m"),
            "rs_pct_1m": d.get("rs_pct_1m"),
            "rs_pct_6m": d.get("rs_pct_6m"),
            "delta":   delta,
            "stage2":  bool(d.get("stage2")),
            "price":   d.get("price"),
            "chg_1d":  d.get("change_1d_pct"),
        })

    changes.sort(key=lambda x: x["delta"], reverse=True)

    # Accelerating: rank moved up ≥8 spots (1M rank > 3M rank)
    accel = [c for c in changes if c["delta"] >= 8][:6]
    # Fading: rank dropped ≥8 spots (1M rank < 3M rank)
    fade  = [c for c in changes if c["delta"] <= -8]
    fade  = sorted(fade, key=lambda x: x["delta"])[:5]

    return {"accelerating": accel, "decelerating": fade}


# ════════════════════════════════════════════════════════════════════
# MAIN SCREENER
# ════════════════════════════════════════════════════════════════════

def run_screens(universe: list, spy: dict, max_per_screen: int = 10) -> dict:
    """Run all 3 screens on the universe, return structured results."""
    print(f"\n  Running PULSE screens on {len(universe)} tickers...")

    results_a = []   # Leader
    results_b = []   # Bottom Fishing
    results_c = []   # Hypergrowth
    raw_data  = {}

    for ticker in universe:
        if ticker.startswith("^"):
            continue   # skip indices

        d = fetch_ticker_data(ticker, spy)
        if d.get("error"):
            print(f"    [!] {ticker}: {d['error'][:50]}")
            continue

        raw_data[ticker] = d
        price = d.get("price", 0)
        rs6   = d.get("rs_6m")

        print(f"    {ticker:8s} ${price:8.2f} | RS6M: {rs6:+6.1f}% | Stage2: {d.get('stage2')} | "
              f"From52H: {d.get('pct_from_high'):+5.1f}%")

        # Run screens
        sa = screen_leader(d)
        sb = screen_bottom_fish(d)
        sc = screen_hypergrowth(d)

        gate_a = wyckoff_gate(d, "Leader")
        gate_b = wyckoff_gate(d, "Bottom Fishing")
        gate_c = wyckoff_gate(d, "Hypergrowth")

        if sa["pass"]:
            results_a.append({
                "ticker": ticker, "screen": "Leader",
                "score": sa["score"], "screen_detail": sa,
                "gate": gate_a, "data": d,
            })

        if sb["pass"]:
            results_b.append({
                "ticker": ticker, "screen": "Bottom Fishing",
                "score": sb["score"], "screen_detail": sb,
                "gate": gate_b, "data": d,
            })

        if sc["pass"]:
            results_c.append({
                "ticker": ticker, "screen": "Hypergrowth",
                "score": sc["score"], "screen_detail": sc,
                "gate": gate_c, "data": d,
            })

    # Sort by score
    results_a.sort(key=lambda x: x["score"], reverse=True)
    results_b.sort(key=lambda x: x["score"], reverse=True)
    results_c.sort(key=lambda x: x["score"], reverse=True)

    # ── Compute percentile ranks within the screener universe ─────
    _valid = [(t, d) for t, d in raw_data.items() if not d.get("error") and d.get("price")]
    _rs6   = sorted([d["rs_6m"]  for _, d in _valid if d.get("rs_6m")  is not None])
    _rs3   = sorted([d["rs_3m"]  for _, d in _valid if d.get("rs_3m")  is not None])
    _rs1   = sorted([d["rs_1m"]  for _, d in _valid if d.get("rs_1m")  is not None])

    def _pctile(lst, val):
        if not lst or val is None: return None
        cnt = sum(1 for x in lst if x <= val)
        return round(cnt / len(lst) * 100, 1)

    for _, d in _valid:
        d["rs_pct_6m"]   = _pctile(_rs6, d.get("rs_6m"))
        d["rs_pct_3m"]   = _pctile(_rs3, d.get("rs_3m"))
        d["rs_pct_1m"]   = _pctile(_rs1, d.get("rs_1m"))
        p1 = d.get("rs_pct_1m"); p3 = d.get("rs_pct_3m"); p6 = d.get("rs_pct_6m")
        # 1M-3M delta: positive = accelerating momentum (primary signal)
        d["rs_pct_delta"]       = round(p1 - p3, 1) if p1 is not None and p3 is not None else None
        # 3M-6M delta: positive = medium-term momentum building
        d["rs_pct_delta_3m6m"]  = round(p3 - p6, 1) if p3 is not None and p6 is not None else None

    # ── Universe ranking — score ALL stocks ────────────────────────
    universe_ranked = []
    for ticker, d in raw_data.items():
        u_score = compute_universe_score(d)
        universe_ranked.append({"ticker": ticker, "score": u_score, "data": d})
    universe_ranked.sort(key=lambda x: x["score"], reverse=True)

    # ── Phase Changers — use percentile rank delta ─────────────────
    phase_changes = compute_phase_changes(raw_data)

    return {
        "date":             TODAY_STR,
        "generated":        datetime.now().isoformat(),
        "universe_count":   len(universe),
        "screened":         len(raw_data),
        "leader":           results_a[:max_per_screen],
        "bottom_fish":      results_b[:max_per_screen],
        "hypergrowth":      results_c[:max_per_screen],
        "universe_ranked":  universe_ranked[:30],    # Top 30
        "phase_changes":    phase_changes,
        "raw":              raw_data,
    }


# ════════════════════════════════════════════════════════════════════
# REPORT WRITER
# ════════════════════════════════════════════════════════════════════

def write_screener_report(results: dict) -> str:
    """Generate markdown screener report from screen results."""
    lines = []
    today_display = TODAY.strftime("%d %B %Y")

    lines.append(f"# AlphaAbsolute PULSE screener | {today_display}")
    lines.append(f"**Framework:** NRGC + PULSE v2.0 | **Source:** yfinance")
    lines.append(f"**Universe:** {results['screened']} stocks screened | "
                 f"Generated {datetime.now().strftime('%H:%M')} TH")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Summary counts
    n_a = len(results["leader"])
    n_b = len(results["bottom_fish"])
    n_c = len(results["hypergrowth"])
    lines.append("## SCREEN SUMMARY")
    lines.append("")
    lines.append("| Screen | Candidates | Setup |")
    lines.append("|--------|-----------|-------|")
    lines.append(f"| A — Leader / Momentum | {n_a} | Minervini TT + PULSE |")
    lines.append(f"| B — Bottom Fishing | {n_b} | Wyckoff Spring + RS Inflection |")
    lines.append(f"| C — Hypergrowth | {n_c} | Base 0/1 + RS Acceleration |")
    lines.append("")
    lines.append("---")
    lines.append("")

    def _pct_rank(v):
        """Format RS percentile rank as ordinal: 85 → '85th', None → 'N/A'."""
        if v is None: return "N/A"
        v = int(round(v))
        suf = {1:"st",2:"nd",3:"rd"}.get(v%10 if v%100 not in (11,12,13) else 0,"th")
        return f"{v}{suf}"

    def _rank_delta(v):
        """Format rank change as integer with sign: +12 or -8. No pp/% suffix."""
        if v is None: return "N/A"
        sign = "+" if v >= 0 else ""
        return f"{sign}{int(round(v))}"

    def _pct(v, plus=True):
        if v is None: return "N/A"
        sign = "+" if plus and v >= 0 else ""
        return f"{sign}{v:.1f}%"

    def render_candidates(cands: list, setup: str, intro: str):
        lines.append(f"## SCREEN {setup}")
        lines.append(f"_{intro}_")
        lines.append("")
        if not cands:
            lines.append("> [R] NO CANDIDATES — ไม่มีหุ้นผ่านเกณฑ์วันนี้ รอ setup ดีขึ้น")
            lines.append("")
            return

        lines.append("| # | Ticker | Theme | Price | 1D | RS 6M | RS 1M | Stage | Wyckoff | Score |")
        lines.append("|---|--------|-------|-------|----|-------|-------|-------|---------|-------|")
        for i, c in enumerate(cands[:10], 1):
            d    = c["data"]
            g    = c["gate"]
            gate_icon = "[G]" if "GREEN" in g["gate_verdict"] else "[Y]" if "YELLOW" in g["gate_verdict"] else "[R]"
            lines.append(
                f"| {i} | **{c['ticker']}** | {d.get('theme','?')[:20]} | "
                f"${d.get('price',0):.2f} | {_pct(d.get('change_1d_pct'))} | "
                f"{_pct(d.get('rs_6m'))} | {_pct(d.get('rs_1m'))} | "
                f"{'Stage 2' if d.get('stage2') else 'Other'} | "
                f"{g['wyckoff_signal'][:25]} | {c['score']} |"
            )
        lines.append("")

        # Detail cards for top 3
        lines.append("### Top Picks — Detail")
        lines.append("")
        for c in cands[:3]:
            d = c["data"]
            g = c["gate"]
            s = c["screen_detail"]
            lines.append(f"#### {c['ticker']} — {d.get('theme','?')}")
            lines.append(f"| Field | Value |")
            lines.append(f"|-------|-------|")
            lines.append(f"| Price | ${d.get('price',0):.2f} | 1D: {_pct(d.get('change_1d_pct'))} |")
            lines.append(f"| RS vs SPY | 6M: {_pct(d.get('rs_6m'))} | 1M: {_pct(d.get('rs_1m'))} | Mom: {d.get('rs_momentum') or 'N/A'} |")
            lines.append(f"| From 52W High | {_pct(d.get('pct_from_high'), plus=False)} |")
            lines.append(f"| Stage | {'Stage 2 [PASS]' if d.get('stage2') else 'Other [CHECK]'} |")
            vol_str = f"{d.get('vol_ratio'):.1f}x" if d.get('vol_ratio') else 'N/A'
            lines.append(f"| Volume vs avg | {vol_str} |")
            lines.append(f"| PULSE pass | {d.get('pulse_pass',0)}/{d.get('pulse_total',6)} checks |")

            # Screen-specific signal
            if s.get("vcp_signal"):   lines.append(f"| VCP Signal | {s['vcp_signal']} |")
            if s.get("sos_signal"):   lines.append(f"| SOS/Spring | {s['sos_signal']} |")
            if s.get("base_signal"):  lines.append(f"| Base Signal | {s['base_signal']} |")

            lines.append(f"| Wyckoff Phase | {g['wyckoff_phase']} |")
            lines.append(f"| Wyckoff Signal | {g['wyckoff_signal']} |")
            lines.append(f"| Gate Verdict | {g['gate_verdict']} |")
            if s.get("fail_reasons"):
                lines.append(f"| Weak points | {' | '.join(s['fail_reasons'])} |")
            lines.append(f"| Score | {c['score']}/100 |")
            lines.append(f"| [!] Verify | EPS revision + chart pattern via Bloomberg/TradingView |")
            lines.append("")

    render_candidates(
        results["leader"],
        "A: LEADER / MOMENTUM",
        "Minervini Trend Template + PULSE — Stage 2, RS > SPY, near ATH, VCP forming"
    )
    render_candidates(
        results["bottom_fish"],
        "B: BOTTOM FISHING",
        "Wyckoff Spring + RS Inflection — Stage 1→2, RS turning up, SOS/LPS signal"
    )
    render_candidates(
        results["hypergrowth"],
        "C: HYPERGROWTH",
        "Base 0/1 + RS Acceleration — Stage 2, RS >20% vs SPY, momentum accelerating"
    )

    # ── Universe Top 30 ───────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## UNIVERSE TOP 30 — Full Watchlist Ranked by PULSE Score")
    lines.append("_RS Pct = ordinal percentile rank within PULSE universe (100th = top RS, 1st = bottom). "
                 "1M-3M Δ = RS Pct 1M minus RS Pct 3M (rank positions gained/lost short-term). "
                 "3M-6M Δ = RS Pct 3M minus RS Pct 6M (medium-term momentum)._")
    lines.append("")
    lines.append("| Rank | Ticker | Theme | Price | 1D | RS Pct 1M | RS Pct 3M | RS Pct 6M | 1M-3M Δ | 3M-6M Δ | Stage | Score |")
    lines.append("|------|--------|-------|-------|----|-----------|-----------|-----------|---------|---------|-------|-------|")
    for i, c in enumerate(results.get("universe_ranked", [])[:30], 1):
        d  = c["data"]
        s2 = "Stage 2 ✅" if d.get("stage2") else "Stage 3/4 ❌" if d.get("stage2") is False else "Stage 1"
        lines.append(
            f"| {i} | **{c['ticker']}** | {d.get('theme','?')[:20]} | ${d.get('price',0):.2f} | "
            f"{_pct(d.get('change_1d_pct'))} | {_pct_rank(d.get('rs_pct_1m'))} | "
            f"{_pct_rank(d.get('rs_pct_3m'))} | {_pct_rank(d.get('rs_pct_6m'))} | "
            f"{_rank_delta(d.get('rs_pct_delta'))} | {_rank_delta(d.get('rs_pct_delta_3m6m'))} | "
            f"{s2} | **{c['score']}** |"
        )
    lines.append("")

    # ── Phase Changers ────────────────────────────────────────────────────────
    pc = results.get("phase_changes", {})
    accel = pc.get("accelerating", [])
    fade  = pc.get("decelerating", [])

    lines.append("---")
    lines.append("")
    lines.append("## PHASE CHANGERS — RS Momentum Shifts")
    lines.append("_1M-3M Δ = RS Pct 1M minus RS Pct 3M (short-term momentum shift). "
                 "3M-6M Δ = RS Pct 3M minus RS Pct 6M (medium-term shift). "
                 "Positive = gaining rank positions. Negative = losing rank positions._")
    lines.append("")

    lines.append("### 🚀 RS Accelerating (1M RS Pct outpacing 3M RS Pct — momentum building)")
    lines.append("")
    if accel:
        lines.append("| Ticker | Theme | RS Pct 3M | RS Pct 1M | 1M-3M Δ | 3M-6M Δ | Stage | Signal |")
        lines.append("|--------|-------|-----------|-----------|---------|---------|-------|--------|")
        for c in accel:
            s2 = "Stage 2 ✅" if c.get("stage2") else "Stage 1/3"
            delta_1m3m = _rank_delta(c.get("delta"))
            delta_3m6m = _rank_delta(c.get("delta_3m6m")) if c.get("delta_3m6m") is not None else "N/A"
            signal = "🚀 Strong Accel" if c.get("delta", 0) > 25 else "↑ Accelerating"
            lines.append(
                f"| **{c['ticker']}** | {c.get('theme','')[:18]} | "
                f"{_pct_rank(c.get('rs_pct_3m'))} | {_pct_rank(c.get('rs_pct_1m'))} | "
                f"{delta_1m3m} | {delta_3m6m} | {s2} | {signal} |"
            )
    else:
        lines.append("> ไม่มีหุ้นที่ RS rank acceleration ชัดเจนวันนี้")
    lines.append("")

    lines.append("### ⚠️ RS Fading (RS Pct 1M dropping vs RS Pct 3M — momentum cooling)")
    lines.append("")
    if fade:
        lines.append("| Ticker | Theme | RS Pct 3M | RS Pct 1M | 1M-3M Δ | 3M-6M Δ | Stage | Signal |")
        lines.append("|--------|-------|-----------|-----------|---------|---------|-------|--------|")
        for c in fade:
            s2 = "Stage 2" if c.get("stage2") else "Stage 1/3"
            delta_1m3m = _rank_delta(c.get("delta"))
            delta_3m6m = _rank_delta(c.get("delta_3m6m")) if c.get("delta_3m6m") is not None else "N/A"
            signal = "⚠️ Sharp Fade" if c.get("delta", 0) < -25 else "↓ Cooling"
            lines.append(
                f"| **{c['ticker']}** | {c.get('theme','')[:18]} | "
                f"{_pct_rank(c.get('rs_pct_3m'))} | {_pct_rank(c.get('rs_pct_1m'))} | "
                f"{delta_1m3m} | {delta_3m6m} | {s2} | {signal} |"
            )
    else:
        lines.append("> ไม่มีหุ้นที่ RS fading ชัดเจนวันนี้")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## GLOBAL WYCKOFF × WEINSTEIN GATE")
    lines.append("")
    lines.append("ทุก recommendation ผ่าน Gate check ด้านบน:")
    lines.append("- [G] GREEN = Stage 2 confirmed + Wyckoff signal identified → proceed per setup rules")
    lines.append("- [Y] YELLOW = Stage 1 or signal unclear → watch, size smaller, wait for confirm")
    lines.append("- [R] RED = Stage 3/4 → NO BUY regardless of RS or fundamentals")
    lines.append("")
    lines.append("_Source: yfinance (Yahoo Finance) | ไม่ใช่คำแนะนำการลงทุน_")
    lines.append(f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} TH_")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="AlphaAbsolute PULSE screener — Agent 3")
    parser.add_argument("--universe", choices=["base", "extended"], default="base",
                        help="Stock universe size (default: base)")
    parser.add_argument("--max", type=int, default=10,
                        help="Max candidates per screen (default: 10)")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Skip Telegram notification")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  AlphaAbsolute PULSE screener — Agent 3")
    print(f"  Date: {TODAY.strftime('%d %B %Y')} | Universe: {args.universe}")
    print(f"{'='*60}\n")

    # Patch SSL
    patch_yfinance_ssl()

    # Universe
    universe = UNIVERSE_EXTENDED if args.universe == "extended" else UNIVERSE_BASE
    universe = [t for t in dict.fromkeys(universe)]  # deduplicate preserve order

    # Fetch SPY benchmark
    print("  [*] Fetching SPY benchmark...")
    spy = fetch_spy_benchmark()
    print(f"  SPY: 6M={spy.get('ret_6m',0):+.1f}% | 3M={spy.get('ret_3m',0):+.1f}% | "
          f"1M={spy.get('ret_1m',0):+.1f}%")

    # Run screens
    results = run_screens(universe, spy, max_per_screen=args.max)

    # Summary
    print(f"\n  --- RESULTS ---")
    print(f"  Screen A (Leader):       {len(results['leader'])} candidates")
    print(f"  Screen B (Bottom Fish):  {len(results['bottom_fish'])} candidates")
    print(f"  Screen C (Hypergrowth):  {len(results['hypergrowth'])} candidates")

    # Save JSON
    json_path = DATA_DIR / f"screener_{YYMMDD}.json"
    # Remove raw price data from JSON output (keep metadata only)
    save_results = {
        "date":          results["date"],
        "generated":     results["generated"],
        "universe_count": results["universe_count"],
        "screened":      results["screened"],
        "leader":        [{k: v for k, v in c.items() if k != "data"} for c in results["leader"]],
        "bottom_fish":   [{k: v for k, v in c.items() if k != "data"} for c in results["bottom_fish"]],
        "hypergrowth":   [{k: v for k, v in c.items() if k != "data"} for c in results["hypergrowth"]],
        "spy_benchmark": spy,
    }
    json_path.write_text(json.dumps(save_results, indent=2, default=str), encoding="utf-8")
    print(f"\n  [OK] JSON: {json_path.name}")

    # Generate markdown report
    md_text = write_screener_report(results)
    md_path = OUT_DIR / f"screener_{YYMMDD}.md"
    md_path.write_text(md_text, encoding="utf-8")
    print(f"  [OK] MD:   {md_path.name}")

    # Telegram summary
    if not args.no_telegram:
        _send_telegram_summary(results, TODAY.strftime("%d %B %Y"))

    print(f"\n{'='*60}")
    print(f"  Done! Screener results in data/screener_{YYMMDD}.json")
    print(f"{'='*60}\n")

    return results


def _send_telegram_summary(results: dict, today_display: str):
    """Send screener summary to Telegram."""
    # Load env
    env_file = ROOT / ".env"
    env = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat  = env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("  [!] Telegram not configured — skipping")
        return

    def top_ticks(cands, n=3):
        return " | ".join(c["ticker"] for c in cands[:n]) if cands else "—"

    msg = (
        f"📊 *AlphaAbsolute PULSE screener*\n"
        f"_{today_display}_\n\n"
        f"🏆 *Leader:* {top_ticks(results['leader'])}\n"
        f"🎣 *Bottom Fish:* {top_ticks(results['bottom_fish'])}\n"
        f"🚀 *Hypergrowth:* {top_ticks(results['hypergrowth'])}\n\n"
        f"Total candidates: "
        f"A={len(results['leader'])} | B={len(results['bottom_fish'])} | C={len(results['hypergrowth'])}\n"
        f"Universe screened: {results['screened']} stocks\n\n"
        f"[!] Verify EPS revision + chart before trade"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": chat,
            "text": msg,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }, timeout=15, verify=False)
        if r.ok:
            print("  [OK] Screener summary sent to Telegram")
        else:
            print(f"  [!] Telegram error: {r.text[:100]}")
    except Exception as e:
        print(f"  [!] Telegram send failed: {e}")


if __name__ == "__main__":
    main()

