"""
AlphaAbsolute — NRGC Phase Tracker (Core Engine)
Maintains per-ticker NRGC phase state. Auto-updates weekly.

Data sources combined for phase detection:
  1. Revenue acceleration (earnings_miner.py — Yahoo Finance quarterly)
  2. Price/RS signals (portfolio_engine.py — Yahoo Finance daily)
  3. Narrative signals (distill_engine.py insights — RSS/scraper)
  4. Insider/institutional signals (research_scraper.py)
  5. Industry-specific signals (industry_signals.py)

Output: data/nrgc/state/{TICKER}.json  — the "living" phase assessment
        data/nrgc/weekly/{DATE}_nrgc_report.json

Token cost: ~$0.01/ticker/week (Haiku phase synthesis)
"""
import json, os, sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

def _make_anthropic_client():
    """Create Anthropic client with SSL bypass for corporate proxy."""
    try:
        import httpx, urllib3, anthropic
        urllib3.disable_warnings()
        env_path = Path(__file__).parent.parent.parent / ".env"
        key = ""
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    key = line.split("=", 1)[1].strip()
        if not key or key == "YOUR_ANTHROPIC_API_KEY_HERE":
            return None
        return anthropic.Anthropic(
            api_key=key,
            http_client=httpx.Client(verify=False),
        )
    except Exception:
        return None

BASE_DIR   = Path(__file__).parent.parent.parent
NRGC_DIR   = BASE_DIR / "data" / "nrgc"
STATE_DIR  = NRGC_DIR / "state"
WEEKLY_DIR = NRGC_DIR / "weekly"
CASE_DIR   = NRGC_DIR / "case_studies"

for d in [NRGC_DIR, STATE_DIR, WEEKLY_DIR, CASE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR / "scripts" / "learning"))
sys.path.insert(0, str(BASE_DIR / "scripts" / "paper_trading"))

# ─── NRGC State Schema ────────────────────────────────────────────────────────
# Each ticker has a state file: data/nrgc/state/MU.json
# Schema:
# {
#   "ticker": "MU",
#   "theme": "Memory/HBM",
#   "phase": 3,
#   "phase_name": "Inflection",
#   "confidence": 0.82,
#   "phase_signals_confirmed": [...],
#   "phase_signals_missing": [...],
#   "narrative": "HBM demand from AI...",
#   "narrative_traction": 0.78,
#   "revenue_signal": {"qoq_trend": "accelerating", "phase_implied": 3, ...},
#   "price_signal": {"stage2": true, "above_50dma": true, ...},
#   "insider_signal": "buying" | "neutral" | "selling",
#   "institutional_signal": "building" | "neutral" | "reducing",
#   "nrgc_composite_score": 76,
#   "action": "Full position — highest conviction",
#   "kill_switches": [...],
#   "last_updated": "2026-05-13",
#   "history": [{...prior state snapshots...}]
# }

PHASE_SYNTHESIS_PROMPT = """You are AlphaAbsolute's NRGC (Narrative Reflexive Growth Cycle) analyst.
Determine the NRGC phase for {ticker} in the {theme} industry.

NRGC PHASES:
1=Neglect (revenue declining, avoid)
2=Accumulation (stabilizing, build 25-30%)
3=Inflection ⭐ (accelerating QoQ, full position — BEST ENTRY)
4=Recognition (beat-and-raise pattern, hold full)
5=Consensus (decelerating, tighten stops)
6=Euphoria (parabolic, trim to 30-40%)
7=Distribution (guidance cut, exit all)

REVENUE DATA (most important signal):
{revenue_data}

PRICE/TECHNICAL SIGNALS:
{price_signals}

NARRATIVE INTELLIGENCE (from news/research this week):
{narrative_signals}

INSIDER/INSTITUTIONAL SIGNALS:
{institutional_signals}

INDUSTRY-SPECIFIC CONTEXT for {theme}:
Phase 3 signals for this industry = {phase3_signals}

Based on ALL signals, determine current NRGC phase.
Return JSON only — no other text:
{{
  "phase": 2 or 3 or 4 or 5 or 6 or 7,
  "phase_name": "string",
  "confidence": 0.0 to 1.0,
  "primary_evidence": ["top 3 signals that drove this call — be specific"],
  "narrative": "1-2 sentence current narrative for this stock/theme (max 40 words)",
  "narrative_traction": 0.0 to 1.0,
  "kill_switch_triggers": ["what would make you downgrade the phase?"],
  "action": "string — what to do now",
  "phase_age_weeks": how many weeks do you estimate it has been in this phase
}}"""


def load_state(ticker: str) -> dict:
    """Load NRGC state for a ticker (or return fresh empty state)."""
    path = STATE_DIR / f"{ticker}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except:
            pass
    return {
        "ticker": ticker,
        "phase": None,
        "confidence": 0,
        "history": [],
        "last_updated": None,
        "created": datetime.now().strftime("%Y-%m-%d"),
    }


def save_state(state: dict):
    """Save NRGC state to disk."""
    ticker = state["ticker"]
    state["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    path = STATE_DIR / f"{ticker}.json"
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def archive_state(state: dict):
    """Add current state to history before updating (max 52 weeks kept)."""
    snapshot = {k: v for k, v in state.items() if k != "history"}
    snapshot["snapshot_date"] = datetime.now().strftime("%Y-%m-%d")
    history = state.get("history", [])
    history.append(snapshot)
    state["history"] = history[-52:]  # keep 52 weeks = 1 year


# ─── Signal Collectors ────────────────────────────────────────────────────────

def collect_price_signals(ticker: str) -> dict:
    """Collect price/technical NRGC signals using portfolio_engine."""
    try:
        from portfolio_engine import get_price_data
        df = get_price_data(ticker, period="2y")
        if df is None or len(df) < 100:
            return {"valid": False}

        row   = df.iloc[-1]
        price = float(row["Close"])
        hi52  = float(df["Close"].tail(252).max())
        lo52  = float(df["Close"].tail(252).min())
        ma30w = float(row.get("MA30W", row["MA200"]))
        ma50  = float(row.get("MA50", price))
        ma150 = float(row.get("MA150", price))
        ma200 = float(row.get("MA200", price))

        # RS proxy: price performance vs 6M ago
        price_6m_ago = float(df["Close"].iloc[-126]) if len(df) > 126 else price
        rs_6m = round((price / price_6m_ago - 1) * 100, 1)

        # Volume trend: recent vs 20D avg
        vol20 = float(row.get("Vol20", df["Volume"].tail(20).mean()))
        vol5  = float(df["Volume"].tail(5).mean())
        vol_trend = "expanding" if vol5 > vol20 * 1.2 else ("contracting" if vol5 < vol20 * 0.6 else "normal")

        # Weinstein stage
        stage2 = price > ma30w and ma150 > ma200

        # NRGC price-implied phase
        pct_from_hi = (price / hi52 - 1) * 100
        if price < ma30w and ma150 < ma200:
            price_phase = 1  # Stage 4
        elif abs(price / ma30w - 1) < 0.08:
            price_phase = 2  # Basing / Stage 1
        elif stage2 and pct_from_hi > -20:
            price_phase = 3 if rs_6m > 30 else 4
        elif stage2:
            price_phase = 4
        else:
            price_phase = 5

        return {
            "valid":         True,
            "price":         round(price, 2),
            "stage2":        stage2,
            "pct_from_52w_hi": round(pct_from_hi, 1),
            "pct_from_52w_lo": round((price / lo52 - 1) * 100, 1),
            "rs_6m_pct":     rs_6m,
            "ma150_gt_200":  ma150 > ma200,
            "above_30wma":   price > ma30w,
            "vol_trend":     vol_trend,
            "price_phase_implied": price_phase,
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


def collect_narrative_signals(ticker: str, theme: str, days_back: int = 14) -> dict:
    """
    Collect recent narrative signals from distill engine insights.
    Searches recent insight files for mentions of this ticker/theme.
    """
    signals = []
    phase_hints = []

    try:
        from industry_signals import detect_phase_from_language

        insights_dir = BASE_DIR / "data" / "insights"
        cutoff = datetime.now() - timedelta(days=days_back)

        # Walk all monthly insight files
        for month_dir in sorted(insights_dir.iterdir())[-3:]:  # last 3 months
            if not month_dir.is_dir():
                continue
            for f in sorted(month_dir.glob("*.json"))[-10:]:  # last 10 files
                try:
                    items = json.loads(f.read_text(encoding="utf-8"))
                    for item in items:
                        # Filter for this ticker or theme
                        relevance = (
                            ticker.upper() in str(item).upper() or
                            theme.lower() in str(item.get("themes", [])).lower()
                        )
                        if not relevance:
                            continue

                        text = item.get("headline", "") + " " + item.get("action_note", "")
                        phase_det = detect_phase_from_language(text)

                        signals.append({
                            "headline":    item.get("headline", ""),
                            "signal_type": item.get("signal_type"),
                            "urgency":     item.get("urgency"),
                            "emls_impact": item.get("emls_impact"),
                            "phase_hint":  phase_det.get("phase_hint"),
                        })
                        if phase_det.get("phase_hint"):
                            phase_hints.append(phase_det["phase_hint"])
                except:
                    continue
    except Exception as e:
        pass

    # Aggregate phase hints from narrative
    from collections import Counter
    phase_count = Counter(phase_hints)
    dominant_narrative_phase = phase_count.most_common(1)[0][0] if phase_count else None

    return {
        "signals":        signals[-10:],  # last 10 relevant signals
        "signal_count":   len(signals),
        "dominant_phase": dominant_narrative_phase,
        "positive_count": sum(1 for s in signals if s.get("emls_impact") == "positive"),
        "negative_count": sum(1 for s in signals if s.get("emls_impact") == "negative"),
    }


def collect_institutional_signals(ticker: str, days_back: int = 30) -> dict:
    """Check for insider buying / 13F changes from recent scraper data."""
    insider_signals = []
    try:
        raw_dir = BASE_DIR / "data" / "raw"
        cutoff = datetime.now() - timedelta(days=days_back)

        for f in sorted(raw_dir.glob("*.json"))[-20:]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                for source_id, items in data.items():
                    if source_id in ("openinsider", "sec_13f"):
                        for item in items:
                            text = str(item)
                            if ticker.upper() in text.upper():
                                insider_signals.append({
                                    "source":     source_id,
                                    "signal":     item.get("title", ""),
                                    "signal_type": item.get("signal_type"),
                                })
            except:
                continue
    except:
        pass

    if not insider_signals:
        return {"signal": "neutral", "details": []}

    has_buying = any("buy" in str(s).lower() or "cluster" in str(s).lower() for s in insider_signals)
    has_selling = any("sell" in str(s).lower() for s in insider_signals)

    return {
        "signal":  "buying" if has_buying else ("selling" if has_selling else "neutral"),
        "details": insider_signals[:3],
    }


# ─── Core NRGC Phase Assessment ───────────────────────────────────────────────

def assess_nrgc_phase(ticker: str, theme: str,
                       revenue_analysis: Optional[dict] = None,
                       client=None) -> dict:
    """
    Full NRGC phase assessment for a ticker.
    Combines all signals → produces phase call with confidence.
    """
    from industry_signals import get_industry_context, get_phase_name, get_phase_action

    print(f"    Assessing NRGC: {ticker} ({theme})...")

    # Collect signals
    price_sig     = collect_price_signals(ticker)
    narrative_sig = collect_narrative_signals(ticker, theme)
    inst_sig      = collect_institutional_signals(ticker)
    industry_ctx  = get_industry_context(theme)

    # Revenue analysis (passed in or empty)
    rev_analysis = revenue_analysis or {"qoq_trend": "no_data"}

    # ── Rule-based phase estimate (no LLM needed for basic signal) ─────────────
    phase_votes = []

    # Vote 1: Revenue signal (weight = 3)
    rev_phase = rev_analysis.get("phase_signal")
    if rev_phase:
        phase_votes.extend([rev_phase] * 3)

    # Vote 2: Price signal (weight = 2)
    price_phase = price_sig.get("price_phase_implied")
    if price_phase:
        phase_votes.extend([price_phase] * 2)

    # Vote 3: Narrative signal (weight = 1)
    narr_phase = narrative_sig.get("dominant_phase")
    if narr_phase:
        phase_votes.append(narr_phase)

    # Vote 4: Institutional buying → boost toward phase 2-3
    if inst_sig.get("signal") == "buying" and phase_votes:
        min_phase = min(phase_votes)
        if min_phase <= 3:
            phase_votes.append(3)  # insider buying = Phase 2-3 signal

    from collections import Counter
    if phase_votes:
        phase_count = Counter(phase_votes)
        rule_phase = phase_count.most_common(1)[0][0]
        rule_confidence = phase_count[rule_phase] / len(phase_votes)
    else:
        rule_phase = None
        rule_confidence = 0

    # ── LLM synthesis (Haiku — only if client provided) ───────────────────────
    final_phase = rule_phase
    final_confidence = rule_confidence
    narrative_text = ""
    kill_switches = []
    primary_evidence = []

    # Only call LLM for Phase 2-3 transitions (saves free-tier quota)
    # Phase 1/4/5/6/7 are clear-cut — rule-based system alone is sufficient
    if rule_phase and rule_phase in (2, 3):
        try:
            from distill_engine import _llm_call
            phase3_signals = industry_ctx.get("phase_signals", {}).get(3, [])[:5]
            prompt = PHASE_SYNTHESIS_PROMPT.format(
                ticker=ticker,
                theme=theme,
                revenue_data=json.dumps(rev_analysis, indent=2),
                price_signals=json.dumps(price_sig, indent=2),
                narrative_signals=json.dumps(narrative_sig, indent=2),
                institutional_signals=json.dumps(inst_sig, indent=2),
                phase3_signals="\n".join(f"- {s}" for s in phase3_signals),
            )
            text = _llm_call(prompt, max_tokens=512, call_type="nrgc_synthesis")
            if text:
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0]
                llm_result = json.loads(text)
                final_phase      = llm_result.get("phase", rule_phase)
                final_confidence = llm_result.get("confidence", rule_confidence)
                narrative_text   = llm_result.get("narrative", "")
                kill_switches    = llm_result.get("kill_switch_triggers", [])
                primary_evidence = llm_result.get("primary_evidence", [])
        except Exception as e:
            print(f"      [LLM error] {ticker}: {e}")
            narrative_text = industry_ctx.get("narrative_templates", {}).get(rule_phase, "")
    else:
        narrative_text = industry_ctx.get("narrative_templates", {}).get(rule_phase, "")

    # ── Composite NRGC Score (0-100) ──────────────────────────────────────────
    # Higher score = higher conviction in current phase call
    score = 0
    if rev_phase == final_phase:
        score += 35  # revenue confirms phase
    if price_phase == final_phase:
        score += 25  # price confirms phase
    if narr_phase == final_phase:
        score += 15  # narrative confirms phase
    if inst_sig.get("signal") == "buying":
        score += 15  # insider buying = strong confirmation
    score += int(final_confidence * 10)  # LLM confidence

    # Build confirmed/missing signals
    phase_signals_industry = industry_ctx.get("phase_signals", {}).get(final_phase, [])
    all_context = str(narrative_sig) + str(rev_analysis) + str(price_sig)

    confirmed = [s for s in phase_signals_industry[:5]
                 if any(word in all_context.lower()
                        for word in s.lower().split()[:3])]
    missing   = [s for s in phase_signals_industry[:5] if s not in confirmed]

    return {
        "ticker":                  ticker,
        "theme":                   theme,
        "phase":                   final_phase,
        "phase_name":              get_phase_name(final_phase) if final_phase else "Unknown",
        "confidence":              round(final_confidence, 2),
        "nrgc_composite_score":    min(score, 100),
        "action":                  get_phase_action(final_phase) if final_phase else "Monitor",
        "narrative":               narrative_text,
        "narrative_traction":      round(narrative_sig.get("positive_count", 0) /
                                         max(narrative_sig.get("signal_count", 1), 1), 2),
        "revenue_signal":          rev_analysis,
        "price_signal":            price_sig,
        "insider_signal":          inst_sig.get("signal", "neutral"),
        "narrative_signals":       narrative_sig,
        "rule_phase":              rule_phase,
        "rule_confidence":         round(rule_confidence, 2),
        "phase_signals_confirmed": confirmed,
        "phase_signals_missing":   missing,
        "kill_switches":           kill_switches,
        "primary_evidence":        primary_evidence,
        "assessed_at":             datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ─── Full Tracker Run ─────────────────────────────────────────────────────────

def run_nrgc_update(watchlist: dict, client=None,
                     earnings_results: Optional[dict] = None) -> dict:
    """
    Update NRGC phase for all tickers in watchlist.
    watchlist: {theme: [tickers]} — from source_config
    earnings_results: output from earnings_miner.run_earnings_scan()
    Returns {ticker: assessment}
    """
    print("\n[NRGC Tracker] Updating phase assessments...")
    all_assessments = {}

    for theme, tickers in watchlist.items():
        print(f"  Theme: {theme}")
        for ticker in tickers:
            # Get earnings data if available
            rev_analysis = None
            if earnings_results and ticker in earnings_results:
                rev_analysis = earnings_results[ticker].get("revenue_analysis")

            # Get current state
            state = load_state(ticker)
            archive_state(state)

            # Assess phase
            assessment = assess_nrgc_phase(ticker, theme, rev_analysis, client)

            # Update state
            state.update(assessment)
            state["theme"] = theme
            save_state(state)

            all_assessments[ticker] = assessment

            # Print summary
            phase   = assessment.get("phase", "?")
            conf    = assessment.get("confidence", 0)
            score   = assessment.get("nrgc_composite_score", 0)
            action  = assessment.get("action", "")
            print(f"    {ticker}: Phase {phase} ({assessment.get('phase_name','?')}) | "
                  f"Conf={conf:.0%} | Score={score} | {action}")

    # Save weekly report
    report = {
        "date":          datetime.now().strftime("%Y-%m-%d"),
        "assessments":   all_assessments,
        "phase_summary": _summarize_phases(all_assessments),
    }
    report_path = WEEKLY_DIR / f"{datetime.now().strftime('%y%m%d')}_nrgc_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  [NRGC] Report saved: {report_path}")

    return all_assessments


def _summarize_phases(assessments: dict) -> dict:
    """Summarize phase distribution across all tracked tickers."""
    from collections import Counter
    phase_count = Counter()
    phase_3_tickers = []
    best_setups = []

    for ticker, a in assessments.items():
        phase = a.get("phase")
        if phase:
            phase_count[phase] += 1
        if phase == 3:
            phase_3_tickers.append(ticker)
        if phase in (2, 3) and a.get("nrgc_composite_score", 0) >= 60:
            best_setups.append({
                "ticker": ticker,
                "phase": phase,
                "score": a.get("nrgc_composite_score"),
                "confidence": a.get("confidence"),
                "action": a.get("action"),
            })

    best_setups.sort(key=lambda x: x["score"], reverse=True)

    return {
        "phase_distribution": dict(phase_count),
        "phase_3_tickers":    phase_3_tickers,
        "best_setups":        best_setups[:5],
        "total_tracked":      len(assessments),
    }


# ─── Case Study Generator ─────────────────────────────────────────────────────

CASE_STUDY_PROMPT = """You are AlphaAbsolute's post-mortem analyst.
A paper trade has closed. Analyze whether the NRGC phase call was correct
and extract learnable patterns.

TRADE DETAILS:
{trade_details}

NRGC PHASE HISTORY during holding period:
{phase_history}

OUTCOME:
{outcome}

INDUSTRY: {theme}

Generate a structured NRGC case study for the knowledge library.
Return JSON only:
{{
  "title": "string — case study title (e.g. 'MU Phase 3 Memory Supercycle 2026')",
  "nrgc_call_accuracy": "correct|partially_correct|incorrect",
  "phase_at_entry": number,
  "actual_phase_that_played_out": number,
  "why_call_was_right_wrong": "string — specific explanation",
  "best_predictive_signals": ["top 3 signals that were most predictive"],
  "missed_signals": ["signals you should have seen but didn't"],
  "industry_lesson": "string — specific lesson for {theme} investors",
  "universal_lesson": "string — lesson applicable to all NRGC analysis",
  "notebooklm_worthy": true or false,
  "summary_for_memory": "2-3 sentences for NotebookLM storage"
}}"""

def generate_nrgc_case_study(closed_trade: dict, portfolio: dict,
                               client=None) -> Optional[dict]:
    """
    Generate NRGC case study from a closed trade.
    Called by auto_postmortem.py after trade closes.
    """
    if not client:
        return None

    ticker = closed_trade.get("ticker", "")
    theme  = closed_trade.get("theme", "Unknown")

    # Load phase history for this ticker
    state = load_state(ticker)
    phase_history = state.get("history", [])[-10:]  # last 10 weeks

    # Trade outcome
    pnl_pct = closed_trade.get("pnl_pct", 0)
    days_held = closed_trade.get("days_held", 0)
    outcome = f"P&L: {pnl_pct:+.1f}% over {days_held} days. {'Win' if pnl_pct > 0 else 'Loss'}."
    if closed_trade.get("close_reason"):
        outcome += f" Exit reason: {closed_trade['close_reason']}"

    try:
        prompt = CASE_STUDY_PROMPT.format(
            trade_details=json.dumps(closed_trade, indent=2),
            phase_history=json.dumps(phase_history, indent=2),
            outcome=outcome,
            theme=theme,
        )
        from distill_engine import _llm_call
        text = _llm_call(prompt, max_tokens=600, call_type="case_study")
        if not text:
            return None
        text = text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        case_study = json.loads(text)

        # Add metadata
        case_study["ticker"]     = ticker
        case_study["theme"]      = theme
        case_study["pnl_pct"]    = pnl_pct
        case_study["days_held"]  = days_held
        case_study["trade_date"] = closed_trade.get("entry_date", "")
        case_study["close_date"] = closed_trade.get("close_date", "")
        case_study["created_at"] = datetime.now().strftime("%Y-%m-%d")

        # Save case study
        filename = f"{ticker}_{theme.replace('/', '_')}_Phase{case_study.get('phase_at_entry','?')}_{datetime.now().strftime('%y%m%d')}.json"
        case_path = CASE_DIR / filename
        case_path.write_text(json.dumps(case_study, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  [Case Study] Saved: {case_path.name}")

        return case_study
    except Exception as e:
        print(f"  [Case Study error] {ticker}: {e}")
        return None


def get_similar_case_studies(theme: str, phase: int, n: int = 3) -> list[dict]:
    """
    Retrieve similar case studies from the library.
    Used to provide few-shot examples to improve phase calls.
    """
    studies = []
    for f in sorted(CASE_DIR.glob("*.json")):
        try:
            study = json.loads(f.read_text(encoding="utf-8"))
            if (study.get("theme") == theme and
                    study.get("phase_at_entry") == phase):
                studies.append(study)
        except:
            continue
    return sorted(studies, key=lambda x: x.get("created_at", ""), reverse=True)[:n]


def get_nrgc_summary_for_all() -> list[dict]:
    """Return current NRGC phase for all tracked tickers, sorted by conviction."""
    states = []
    for f in STATE_DIR.glob("*.json"):
        try:
            state = json.loads(f.read_text(encoding="utf-8"))
            if state.get("phase"):
                states.append(state)
        except:
            continue
    # Sort: Phase 3 > Phase 2 > Phase 4 > others, then by score
    def sort_key(s):
        phase = s.get("phase", 9)
        score = s.get("nrgc_composite_score", 0)
        priority = {3: 0, 2: 1, 4: 2}.get(phase, 5)
        return (priority, -score)
    return sorted(states, key=sort_key)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(BASE_DIR / "scripts" / "paper_trading"))

    from source_config import DEFAULT_WATCHLIST
    # Quick test — no LLM
    results = run_nrgc_update(DEFAULT_WATCHLIST, client=None)

    print("\n=== NRGC Summary ===")
    for s in get_nrgc_summary_for_all():
        print(f"  {s['ticker']:6} Phase {s.get('phase','?')} ({s.get('phase_name','?'):12}) "
              f"Score={s.get('nrgc_composite_score','?'):3} | {s.get('action','')}")
