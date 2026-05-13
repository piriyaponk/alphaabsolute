"""
AlphaAbsolute — Weekly Focus List + Entry Zone + Infinite Learning Loop
Generates EMLS-scored watchlist with exact entry zones, tracks outcomes weekly,
generates LLM lessons to improve toward world-class trading (beat NASDAQ, low drawdown).
"""
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import urllib3
urllib3.disable_warnings()

BASE_DIR  = Path(__file__).resolve().parents[2]
DATA_DIR  = BASE_DIR / "data" / "paper_trading"
NRGC_DIR  = BASE_DIR / "data" / "nrgc" / "state"
FOCUS_FILE    = DATA_DIR / "focus_list.json"
FOCUS_HISTORY = DATA_DIR / "focus_history.json"
LESSONS_FILE  = DATA_DIR / "focus_lessons.json"

# ─── EMLS Weights ─────────────────────────────────────────────────────────────
EMLS_W = {"earnings": 0.25, "revenue": 0.20, "rs": 0.20,
           "structure": 0.15, "volume": 0.10, "regime": 0.10}

EMLS_TIERS = [(90, "Hyper Leader"), (80, "Institutional Leader"),
              (70, "Emerging Leader"), (60, "Watchlist")]

PHASE_NAMES = {1:"Neglect",2:"Accumulation",3:"Inflection",4:"Recognition",
               5:"Consensus",6:"Euphoria",7:"Distribution"}

PHASE_FACTOR = {0:0, 1:0.15, 2:0.65, 3:1.00, 4:0.80, 5:0.35, 6:0.10, 7:0}


def _emls_label(score: float) -> str:
    for threshold, label in EMLS_TIERS:
        if score >= threshold:
            return label
    return "Below threshold"


# ─── Price Data ────────────────────────────────────────────────────────────────
def _fetch_ohlcv(ticker: str, days: int = 100) -> list:
    end   = int(time.time())
    start = end - days * 86400
    url   = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
             f"?interval=1d&period1={start}&period2={end}")
    try:
        s = requests.Session()
        s.verify = False
        s.headers["User-Agent"] = "Mozilla/5.0"
        r = s.get(url, timeout=12)
        res = r.json()["chart"]["result"][0]
        ts  = res["timestamp"]
        q   = res["indicators"]["quote"][0]
        rows = []
        for i, t in enumerate(ts):
            try:
                rows.append({
                    "date":   datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                    "open":   q["open"][i],
                    "high":   q["high"][i],
                    "low":    q["low"][i],
                    "close":  q["close"][i],
                    "volume": q["volume"][i],
                })
            except (TypeError, IndexError):
                pass
        return [r for r in rows if r["close"]]
    except Exception:
        return []


def _ema(values: list, span: int) -> float:
    if not values:
        return 0.0
    k = 2 / (span + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def _sma(values: list, window: int):
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _price_levels(ohlcv: list) -> dict:
    """Calculate entry zone, trigger, stop, target from OHLCV."""
    if len(ohlcv) < 20:
        return {}

    closes = [r["close"] for r in ohlcv]
    highs  = [r["high"]  for r in ohlcv]
    lows   = [r["low"]   for r in ohlcv]
    vols   = [r["volume"] for r in ohlcv if r["volume"]]

    current = closes[-1]
    ema21   = _ema(closes, 21)
    ema10   = _ema(closes, 10)
    sma50   = _sma(closes, 50)
    sma200  = _sma(closes, 200)

    # Consolidation base (last 20 bars)
    base_closes = closes[-20:]
    base_lows   = lows[-20:]
    base_highs  = highs[-20:]
    base_low    = min(base_lows)
    base_high   = max(base_highs)

    # Pivot high (10-bar highest intraday high)
    pivot_high = max(highs[-10:]) if len(highs) >= 10 else base_high

    # VCP tightness: range of last 10 closes vs mean
    last10 = closes[-10:]
    mean10 = sum(last10) / len(last10)
    tightness_pct = (max(last10) - min(last10)) / mean10 * 100 if mean10 else 99

    # Volume contraction: last 5 vs last 20 avg
    avg_vol20 = sum(vols[-20:]) / min(20, len(vols)) if vols else 1
    avg_vol5  = sum(vols[-5:])  / min(5,  len(vols)) if vols else 1
    vol_contracting = avg_vol5 < avg_vol20 * 0.80

    # 52-week stats
    w52_high = max(highs[-252:]) if len(highs) >= 252 else max(highs)
    w52_low  = min(lows[-252:])  if len(lows)  >= 252 else min(lows)
    pct_from_52h = (current - w52_high) / w52_high * 100
    pct_from_52l = (current - w52_low)  / w52_low  * 100

    # Trigger = 1% above 10-bar pivot (breakout buy)
    trigger = round(pivot_high * 1.01, 2)
    stop    = round(trigger * 0.92, 2)          # -8% hard stop

    # Target = measured move: base depth × 1.5 projected above pivot
    base_depth_pct = (pivot_high - base_low) / base_low if base_low > 0 else 0.12
    target = round(pivot_high * (1 + base_depth_pct * 1.5), 2)

    # R/R ratio
    rr = round((target - trigger) / (trigger - stop), 2) if trigger > stop else 0

    # Entry zone: between 21EMA and just under pivot
    zone_low  = round(min(ema21, base_low * 1.03), 2)
    zone_high = round(pivot_high * 0.99, 2)

    # Extension: is price already above trigger? (extended = caution)
    extended = current > trigger

    return {
        "current_price":   round(current, 2),
        "ema10":           round(ema10, 2),
        "ema21":           round(ema21, 2),
        "sma50":           round(sma50, 2) if sma50 else None,
        "sma200":          round(sma200, 2) if sma200 else None,
        "base_low":        round(base_low, 2),
        "base_high":       round(base_high, 2),
        "pivot_high":      round(pivot_high, 2),
        "trigger":         trigger,
        "stop":            stop,
        "target":          target,
        "rr_ratio":        rr,
        "zone_low":        zone_low,
        "zone_high":       zone_high,
        "tightness_pct":   round(tightness_pct, 1),
        "vol_contracting": vol_contracting,
        "w52_high":        round(w52_high, 2),
        "w52_low":         round(w52_low, 2),
        "pct_from_52h":    round(pct_from_52h, 1),
        "pct_from_52l":    round(pct_from_52l, 1),
        "extended":        extended,
    }


# ─── EMLS Scoring ─────────────────────────────────────────────────────────────
def _emls_score(nrgc: dict, price: dict, regime: str = "neutral") -> dict:
    phase      = nrgc.get("phase", 0)
    pf         = PHASE_FACTOR.get(phase, 0)
    nrgc_score = nrgc.get("nrgc_composite_score", 0) / 100
    confidence = nrgc.get("confidence", 0.5)

    rev    = nrgc.get("revenue_signal", {})
    accel  = rev.get("acceleration_score", 0.3)
    qoq    = rev.get("latest_qoq_pct", 0) or 0

    ps     = nrgc.get("price_signal", {})
    rs_6m  = ps.get("rs_6m_pct", 0) or 0
    stage2 = ps.get("stage2", False)
    ma_ok  = ps.get("ma150_gt_200", False)
    p52h   = ps.get("pct_from_52w_hi", -50) or -50

    # 1. Earnings Acceleration (25%)
    # Blend NRGC composite score with phase factor
    earnings_s = (nrgc_score * 0.6 + confidence * 0.4) * pf

    # 2. Revenue Acceleration (20%)
    # acceleration_score + QoQ bonus
    qoq_bonus  = min(qoq / 100, 1.0) if qoq > 0 else 0
    revenue_s  = min((accel * 0.7 + qoq_bonus * 0.3) * pf, 1.0)

    # 3. Relative Strength (20%)
    # rs_6m_pct: 0% = neutral, 50% = good, 100%+ = leader
    rs_s = min(rs_6m / 150, 1.0) if rs_6m > 0 else 0.3

    # 4. Price Structure (15%)
    stage_s  = 1.0 if stage2 else 0.2
    ma_s     = 1.0 if ma_ok  else 0.3
    near52h  = max(0, 1 + p52h / 25)   # 0% from 52h=1.0, -25%=0, below=0
    tight_s  = max(0, 1 - (price.get("tightness_pct", 20) / 20)) if price else 0.4
    ext_pen  = 0.7 if price and price.get("extended") else 1.0
    structure_s = (stage_s * 0.30 + ma_s * 0.20 + near52h * 0.30 + tight_s * 0.20) * ext_pen

    # 5. Volume (10%)
    vol_s = 0.9 if price and price.get("vol_contracting") else 0.4

    # 6. Market Regime (10%)
    regime_s = {"risk-on": 1.0, "neutral": 0.65, "risk-off": 0.20}.get(regime, 0.65)

    total = (
        earnings_s  * EMLS_W["earnings"] +
        revenue_s   * EMLS_W["revenue"]  +
        rs_s        * EMLS_W["rs"]       +
        structure_s * EMLS_W["structure"]+
        vol_s       * EMLS_W["volume"]   +
        regime_s    * EMLS_W["regime"]
    ) * 100

    # Phase 3 bonus (+5 points) — inflection is the sweet spot
    if phase == 3:
        total = min(total + 5, 100)

    return {
        "emls_score":  round(total, 1),
        "emls_label":  _emls_label(total),
        "emls_earnings":  round(earnings_s * 100, 1),
        "emls_revenue":   round(revenue_s  * 100, 1),
        "emls_rs":        round(rs_s       * 100, 1),
        "emls_structure": round(structure_s* 100, 1),
        "emls_volume":    round(vol_s      * 100, 1),
        "emls_regime":    round(regime_s   * 100, 1),
    }


# ─── Focus List Generator ─────────────────────────────────────────────────────
def generate_focus_list(nrgc_assessments: dict, regime: str = "neutral") -> list:
    """
    Generate weekly top-10 focus list from NRGC assessments.
    Scores each stock on EMLS 0-100, adds entry zones. Phase 2-4 only.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    picks = []

    for ticker, nrgc in nrgc_assessments.items():
        phase = nrgc.get("phase", 0)
        if phase not in (2, 3, 4):
            continue

        ps = nrgc.get("price_signal", {})

        # Hard gates from NRGC price signal
        if not ps.get("stage2"):
            continue
        if (ps.get("pct_from_52w_hi") or -100) < -30:
            continue

        # Fetch OHLCV for EMA / pivot / tightness
        ohlcv = _fetch_ohlcv(ticker)
        price = _price_levels(ohlcv) if len(ohlcv) >= 20 else {}

        # Use NRGC price if Yahoo fetch fails
        if not price and ps.get("price"):
            price = {"current_price": ps["price"], "tightness_pct": 15,
                     "vol_contracting": ps.get("vol_trend") == "contracting",
                     "pct_from_52h": ps.get("pct_from_52w_hi", -10),
                     "extended": False}

        scores = _emls_score(nrgc, price, regime)
        if scores["emls_score"] < 60:
            continue

        # R/R gate: skip if R/R < 1.5 (unless Phase 3 high conviction)
        rr = price.get("rr_ratio", 0)
        if rr > 0 and rr < 1.5 and phase != 3:
            continue

        rev = nrgc.get("revenue_signal", {})
        pick = {
            "ticker":           ticker,
            "date":             today,
            "phase":            phase,
            "phase_name":       PHASE_NAMES.get(phase, "?"),
            "theme":            nrgc.get("theme", "—"),
            "cap_tier":         nrgc.get("cap_tier", "unknown"),
            "nrgc_score":       nrgc.get("nrgc_composite_score", 0),
            "confidence":       nrgc.get("confidence", 0),
            "narrative":        nrgc.get("narrative", ""),
            "qoq_pct":          rev.get("latest_qoq_pct"),
            "margin_trend":     rev.get("margin_trend", ""),
            "rs_6m_pct":        ps.get("rs_6m_pct"),
            "stage2":           ps.get("stage2", False),
            "ma150_gt_200":     ps.get("ma150_gt_200", False),
            "vol_trend":        ps.get("vol_trend", ""),
            **scores,
            **price,
            # Outcome fields (filled in next week)
            "triggered":        None,
            "trigger_date":     None,
            "outcome_pct":      None,
            "outcome_date":     None,
            "outcome_label":    None,
        }
        picks.append(pick)
        time.sleep(0.25)   # rate limit Yahoo Finance

    # Sort: Phase 3 first, then by EMLS score
    picks.sort(key=lambda x: (x["phase"] != 3, -x["emls_score"]))
    return picks[:10]


# ─── Outcome Tracker ──────────────────────────────────────────────────────────
def _check_trigger(ticker: str, trigger: float, stop: float,
                   from_date: str) -> tuple:
    """
    Check if trigger was hit since from_date.
    Returns (hit: bool, hit_date, current_price, outcome_label, outcome_pct).
    """
    ohlcv = _fetch_ohlcv(ticker, days=14)
    if not ohlcv:
        return False, None, None, "NO_DATA", None

    current = ohlcv[-1]["close"]

    for row in ohlcv:
        if row["date"] <= from_date:
            continue
        if row["high"] and row["high"] >= trigger:
            # Triggered — calculate outcome from trigger price
            pnl_pct = (current - trigger) / trigger * 100
            if current <= stop:
                label = "STOP"
            elif pnl_pct >= 20:
                label = "BIG_WIN"
            elif pnl_pct >= 8:
                label = "WIN"
            elif pnl_pct >= 0:
                label = "SMALL_WIN"
            else:
                label = "OPEN_LOSS"
            return True, row["date"], current, label, round(pnl_pct, 2)

    return False, None, current, "NO_TRIGGER", None


def track_focus_outcomes(picks: list) -> list:
    """Check outcomes for all picks from last week's focus list."""
    today   = datetime.utcnow().strftime("%Y-%m-%d")
    updated = []

    for pick in picks:
        if pick.get("triggered") is not None:
            updated.append(pick)
            continue

        ticker  = pick["ticker"]
        trigger = pick.get("trigger", 0)
        stop    = pick.get("stop", trigger * 0.92 if trigger else 0)
        date    = pick.get("date", "")

        hit, hit_date, current, label, pnl = _check_trigger(
            ticker, trigger, stop, date)

        updated.append({**pick,
            "triggered":     hit,
            "trigger_date":  hit_date,
            "outcome_pct":   pnl,
            "outcome_date":  today,
            "outcome_label": label,
            "current_price_check": round(current, 2) if current else None,
        })
        time.sleep(0.2)

    return updated


# ─── Lesson Generator (Groq → Gemini) ────────────────────────────────────────
LESSON_PROMPT = """You are AlphaAbsolute's Learning Agent. Mission: build a world-class trading system that consistently beats NASDAQ with low drawdown.

Analyze this week's focus list outcomes and generate specific, actionable lessons.

FOCUS LIST OUTCOMES THIS WEEK:
{results}

AGGREGATE STATS:
- Total picks: {total}
- Triggered: {triggered} ({trigger_rate:.0f}%)
- Big wins (>+20%): {big_wins}
- Wins (+8-20%): {wins}
- Small wins (0-8%): {small_wins}
- Stops hit: {stops}
- No trigger: {no_trigger}
- Avg P&L (triggered): {avg_pnl:+.1f}%
- Best: {best}
- Worst: {worst}

CURRENT SYSTEM RULES:
- Phase filter: NRGC Phase 2-4 only (Phase 3 preferred)
- EMLS minimum score: 60 (Phase 3 gets +5 bonus)
- Trigger: pivot_high × 1.01 (1% breakout buy)
- Stop: trigger × 0.92 (-8% hard stop)
- Target: measured move 1.5× base depth
- R/R gate: >1.5× (waived for Phase 3)
- Stage 2 Weinstein gate required
- Extended stocks (price > trigger) get penalty

Generate exactly 3 lessons. Format each as:
LESSON: [specific, measurable rule or observation]
EVIDENCE: [what happened this week that proves this — cite specific tickers]
ACTION: [exact, implementable change to the scoring or filtering system]

Focus on: trigger quality, phase timing, cap tier performance, which EMLS components predicted winners vs losers, entry timing patterns. Goal is to compound learning week after week toward world-class execution."""


def _call_llm(prompt: str) -> str:
    groq_key   = os.environ.get("GROQ_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")

    if groq_key:
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}",
                         "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 900},
                timeout=30, verify=False,
            )
            return r.json()["choices"][0]["message"]["content"]
        except Exception:
            pass

    if gemini_key:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.0-flash:generateContent?key={gemini_key}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=30, verify=False,
            )
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            pass

    return ""


def _parse_lessons(text: str) -> list:
    lessons, current = [], {}
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("LESSON:"):
            if current:
                lessons.append(current)
            current = {"lesson": line[7:].strip(), "evidence": "", "action": ""}
        elif line.upper().startswith("EVIDENCE:") and current:
            current["evidence"] = line[9:].strip()
        elif line.upper().startswith("ACTION:") and current:
            current["action"] = line[7:].strip()
    if current:
        lessons.append(current)
    return lessons


def generate_focus_lessons(outcomes: list) -> list:
    total      = len(outcomes)
    triggered  = [o for o in outcomes if o.get("triggered")]
    big_wins   = [o for o in triggered if o.get("outcome_label") == "BIG_WIN"]
    wins       = [o for o in triggered if o.get("outcome_label") == "WIN"]
    small_wins = [o for o in triggered if o.get("outcome_label") == "SMALL_WIN"]
    stops      = [o for o in triggered if o.get("outcome_label") == "STOP"]
    no_trig    = [o for o in outcomes  if o.get("outcome_label") == "NO_TRIGGER"]

    trigger_rate = len(triggered) / total * 100 if total else 0
    pnls = [o.get("outcome_pct", 0) or 0 for o in triggered]
    avg_pnl = sum(pnls) / len(pnls) if pnls else 0
    best  = max(triggered, key=lambda x: x.get("outcome_pct", 0) or 0)["ticker"] if triggered else "—"
    worst = min(triggered, key=lambda x: x.get("outcome_pct", 0) or 0)["ticker"] if triggered else "—"

    results_txt = ""
    for o in outcomes:
        results_txt += (
            f"- {o['ticker']} | EMLS {o.get('emls_score','?')} | "
            f"Phase {o.get('phase','?')} {o.get('phase_name','')} | "
            f"Cap={o.get('cap_tier','?')} | Theme={o.get('theme','?')} | "
            f"Trigger=${o.get('trigger','?')} | QoQ={o.get('qoq_pct','?')}% | "
            f"RS6m={o.get('rs_6m_pct','?')}% | "
            f"Result={o.get('outcome_label','?')} | P&L={o.get('outcome_pct','?')}%\n"
        )

    prompt = LESSON_PROMPT.format(
        results=results_txt, total=total,
        triggered=len(triggered), trigger_rate=trigger_rate,
        big_wins=len(big_wins), wins=len(wins),
        small_wins=len(small_wins), stops=len(stops),
        no_trigger=len(no_trig), avg_pnl=avg_pnl,
        best=best, worst=worst,
    )

    raw = _call_llm(prompt)
    return _parse_lessons(raw) if raw else []


# ─── Persistence ──────────────────────────────────────────────────────────────
def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def load_current_focus() -> dict:
    return _load_json(FOCUS_FILE, {})


def save_current_focus(picks: list):
    _save_json(FOCUS_FILE, {"date": datetime.utcnow().strftime("%Y-%m-%d"),
                             "picks": picks})


def load_focus_history() -> list:
    return _load_json(FOCUS_HISTORY, [])


def save_focus_history(history: list):
    _save_json(FOCUS_HISTORY, history[-52:])   # keep 1 year


def load_focus_lessons() -> list:
    return _load_json(LESSONS_FILE, [])


def save_focus_lessons(lessons: list):
    _save_json(LESSONS_FILE, lessons[-300:])   # keep ~3 years of lessons


# ─── Memory Update ────────────────────────────────────────────────────────────
def update_focus_memory(lessons: list, history: list):
    """Append focus list lessons to the persistent trading memory file."""
    mem_dir  = BASE_DIR / "memory"
    mem_file = mem_dir / "paper_trading_lessons.md"
    mem_dir.mkdir(parents=True, exist_ok=True)

    if not lessons:
        return

    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Aggregate stats from last 8 weeks of history
    all_triggered = []
    for week in history[-8:]:
        for p in week.get("picks", []):
            if p.get("triggered"):
                all_triggered.append(p)

    win_rate = 0
    if all_triggered:
        wins = [p for p in all_triggered
                if p.get("outcome_label") in ("WIN", "BIG_WIN", "SMALL_WIN")]
        win_rate = len(wins) / len(all_triggered) * 100

    # Build section
    section = f"\n\n## Focus List Lessons — {today}\n"
    section += f"8-week trigger rate: {len(all_triggered)} triggered | Win rate: {win_rate:.0f}%\n"
    for i, l in enumerate(lessons, 1):
        section += f"\n### Lesson {i}: {l.get('lesson','')}\n"
        section += f"**Evidence:** {l.get('evidence','')}\n"
        section += f"**Action:** {l.get('action','')}\n"

    existing = mem_file.read_text(encoding="utf-8") if mem_file.exists() else ""
    mem_file.write_text(existing + section, encoding="utf-8")


# ─── Main Weekly Cycle ────────────────────────────────────────────────────────
def run_focus_cycle(nrgc_assessments: dict, regime: str = "neutral") -> dict:
    """
    Full weekly focus cycle:
      1. Track outcomes of last week's focus list
      2. Generate lessons from outcomes (LLM)
      3. Generate new focus list with entry zones
      4. Save everything + update memory
    Returns dict for Telegram and logging.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    history     = load_focus_history()
    all_lessons = load_focus_lessons()
    new_lessons = []
    prev_outcomes = []

    # ── Step 1: Track outcomes of last week ──
    prev = load_current_focus()
    if prev and prev.get("picks"):
        print(f"  Tracking outcomes for {len(prev['picks'])} picks from {prev.get('date','?')}...")
        prev_outcomes = track_focus_outcomes(prev["picks"])

        history.append({"week": prev.get("date", ""), "picks": prev_outcomes})
        save_focus_history(history)

        # ── Step 2: Generate lessons ──
        triggered = [o for o in prev_outcomes if o.get("triggered")]
        if triggered:
            print(f"  {len(triggered)} triggered picks — generating lessons...")
            new_lessons = generate_focus_lessons(prev_outcomes)
            if new_lessons:
                dated = [{"date": datetime.utcnow().strftime("%Y-%m-%d"),
                          "week_of": prev.get("date", ""), **l}
                         for l in new_lessons]
                all_lessons.extend(dated)
                save_focus_lessons(all_lessons)
                update_focus_memory(new_lessons, history)
                print(f"  {len(new_lessons)} lessons saved to memory")
        else:
            print("  No triggered picks — no lessons this week")

    # ── Step 3: Generate new focus list ──
    print(f"  Generating new focus list from {len(nrgc_assessments)} NRGC assessments...")
    new_picks = generate_focus_list(nrgc_assessments, regime)
    save_current_focus(new_picks)
    print(f"  Focus list: {len(new_picks)} picks | top={new_picks[0]['ticker'] if new_picks else 'none'}")

    return {
        "picks":          new_picks,
        "new_lessons":    new_lessons,
        "prev_outcomes":  prev_outcomes,
        "total_lessons":  len(all_lessons),
    }
