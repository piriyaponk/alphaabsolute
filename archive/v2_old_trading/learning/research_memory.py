"""
AlphaAbsolute — Research Team Lifetime Learning
Tracks thesis accuracy, source quality, earnings patterns, theme intelligence.
Research team reads+writes better every week. Knowledge compounds forever.

4 Learning Dimensions:
  1. READ BETTER   — source quality ranking (which sources produce signal vs noise)
  2. WRITE DEEPER  — thesis tracking (did our narrative calls play out?)
  3. ANALYZE SMARTER — earnings intelligence + macro turning point database
  4. STORE FOREVER — searchable knowledge base that grows each week

All free. All cloud. No computer needed.
"""
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import urllib3
urllib3.disable_warnings()

BASE_DIR  = Path(__file__).resolve().parents[2]
MEM_DIR   = BASE_DIR / "memory"
DATA_DIR  = BASE_DIR / "data" / "agent_memory"

RESEARCH_MEM   = MEM_DIR / "research_learnings.md"
SOURCE_MEM     = MEM_DIR / "source_quality.md"
THESIS_MEM     = MEM_DIR / "thesis_tracker.md"
KNOWLEDGE_BASE = DATA_DIR / "knowledge_base.json"
THESIS_DB      = DATA_DIR / "thesis_db.json"
SOURCE_DB      = DATA_DIR / "source_quality_db.json"
SNAPSHOT_DIR   = DATA_DIR / "research_snapshots"


# ─── LLM ──────────────────────────────────────────────────────────────────────
def _llm(prompt: str, max_tokens: int = 800) -> str:
    for key, url, make_body in [
        (
            os.environ.get("GROQ_API_KEY", ""),
            "https://api.groq.com/openai/v1/chat/completions",
            lambda: {"model": "llama-3.3-70b-versatile",
                     "messages": [{"role": "user", "content": prompt}],
                     "max_tokens": max_tokens},
        ),
        (
            os.environ.get("GEMINI_API_KEY", ""),
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
            lambda: {"contents": [{"parts": [{"text": prompt}]}]},
        ),
    ]:
        if not key:
            continue
        try:
            is_gemini = "gemini" in url
            r = requests.post(
                url,
                headers={} if is_gemini else
                         {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                params={"key": key} if is_gemini else {},
                json=make_body(), timeout=35, verify=False,
            )
            data = r.json()
            return (data["candidates"][0]["content"]["parts"][0]["text"] if is_gemini
                    else data["choices"][0]["message"]["content"])
        except Exception:
            continue
    return ""


def _price(ticker: str) -> Optional[float]:
    try:
        s = requests.Session(); s.verify = False
        s.headers["User-Agent"] = "Mozilla/5.0"
        r = s.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                  params={"interval": "1d", "range": "5d"}, timeout=10)
        return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except Exception:
        return None


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


# ═══════════════════════════════════════════════════════════════════════════════
# DIMENSION 1 — READ BETTER: Source Quality Tracking
# ═══════════════════════════════════════════════════════════════════════════════

def update_source_quality(raw_items: dict, insights: list,
                           focus_result: dict, portfolio: dict):
    """
    Track which sources produce actionable insights vs noise.
    Signal quality = insights that led to focus list picks that triggered.
    """
    db = _load_json(SOURCE_DB, {"sources": {}, "updated": []})
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Tickers from triggered focus picks this week (= high-quality signal)
    triggered_tickers = set()
    for o in (focus_result or {}).get("prev_outcomes", []):
        if o.get("triggered"):
            triggered_tickers.add(o.get("ticker", ""))

    # Tickers entered in portfolio this week (= acted on)
    week_ago = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    entered_tickers = set()
    for t in portfolio.get("closed", []) + list(portfolio.get("positions", {}).values()):
        if t.get("entry_date", t.get("date", "")) >= week_ago:
            entered_tickers.add(t.get("ticker", ""))

    # Score each source
    for source, items in raw_items.items():
        if source not in db["sources"]:
            db["sources"][source] = {
                "total_items": 0, "total_insights": 0, "actionable": 0,
                "triggered": 0, "quality_score": 50, "history": []
            }

        s = db["sources"][source]
        n_items = len(items) if isinstance(items, list) else 0

        # Count insights from this source
        source_insights = [i for i in insights
                           if source.lower() in str(i.get("source", "")).lower()]
        n_insights = len(source_insights)

        # Count actionable (any ticker match)
        n_actionable = sum(1 for i in source_insights
                          if i.get("ticker") in entered_tickers)
        n_triggered = sum(1 for i in source_insights
                         if i.get("ticker") in triggered_tickers)

        s["total_items"]   += n_items
        s["total_insights"] += n_insights
        s["actionable"]    += n_actionable
        s["triggered"]     += n_triggered

        # Quality score: weighted average (insight rate + actionable rate + trigger rate)
        insight_rate   = n_insights / max(n_items, 1)
        actionable_rate = n_actionable / max(n_insights, 1)
        trigger_rate   = n_triggered / max(n_insights, 1)

        week_score = (insight_rate * 0.3 + actionable_rate * 0.4 + trigger_rate * 0.3) * 100
        # Exponential moving average of quality score
        s["quality_score"] = round(s["quality_score"] * 0.8 + week_score * 0.2, 1)
        s["history"].append({"date": today, "items": n_items,
                              "insights": n_insights, "score": round(week_score, 1)})
        s["history"] = s["history"][-52:]  # keep 1 year

    db["updated"].append(today)
    _save_json(SOURCE_DB, db)

    # Rank sources by quality score
    ranked = sorted(db["sources"].items(),
                    key=lambda x: x[1]["quality_score"], reverse=True)
    return ranked


# ═══════════════════════════════════════════════════════════════════════════════
# DIMENSION 2 — WRITE DEEPER: Thesis Tracking
# ═══════════════════════════════════════════════════════════════════════════════

def save_weekly_theses(synthesis: dict, nrgc_assessments: dict):
    """
    Extract and save this week's research theses for verification next week.
    Theses = synthesis opportunities + strong NRGC narratives.
    """
    db    = _load_json(THESIS_DB, {"theses": [], "verified_count": 0})
    week  = datetime.utcnow().strftime("%Y-W%V")
    today = datetime.utcnow().strftime("%Y-%m-%d")

    new_theses = []

    # From synthesis top opportunities
    opps = (synthesis or {}).get("top_opportunities", [])
    for opp in opps[:6]:
        ticker = opp.get("ticker")
        if not ticker:
            continue
        price = _price(ticker)
        time.sleep(0.15)
        new_theses.append({
            "id":          f"{week}_{ticker}",
            "week":        week,
            "date":        today,
            "source":      "agent_09_synthesis",
            "ticker":      ticker,
            "thesis":      opp.get("thesis", opp.get("narrative", "")),
            "conviction":  opp.get("conviction", 3),
            "timeframe":   "1-4 weeks",
            "price_at":    price,
            "verified":    False,
            "outcome_pct": None,
            "outcome_correct": None,
        })

    # From NRGC Phase 3 narratives (highest conviction)
    for ticker, nrgc in nrgc_assessments.items():
        if nrgc.get("phase") != 3:
            continue
        narrative = nrgc.get("narrative", "")
        if len(narrative) < 20:
            continue
        price = _price(ticker)
        time.sleep(0.15)
        new_theses.append({
            "id":          f"{week}_{ticker}_nrgc",
            "week":        week,
            "date":        today,
            "source":      "agent_nrgc_phase3",
            "ticker":      ticker,
            "thesis":      narrative,
            "conviction":  round(nrgc.get("confidence", 0.5) * 5, 1),
            "timeframe":   "2-8 weeks",
            "price_at":    price,
            "qoq_at":      nrgc.get("revenue_signal", {}).get("latest_qoq_pct"),
            "verified":    False,
            "outcome_pct": None,
            "outcome_correct": None,
        })

    # From synthesis macro narrative
    macro_narr = (synthesis or {}).get("macro_narrative", "")
    regime     = (synthesis or {}).get("regime_signal", "neutral")
    if macro_narr:
        new_theses.append({
            "id":        f"{week}_macro",
            "week":      week,
            "date":      today,
            "source":    "agent_09_macro",
            "ticker":    "QQQ",
            "thesis":    f"[{regime.upper()}] {macro_narr}",
            "conviction": 3,
            "timeframe": "1-2 weeks",
            "price_at":  _price("QQQ"),
            "verified":  False,
            "outcome_pct": None,
            "outcome_correct": None,
        })

    db["theses"].extend(new_theses)
    db["theses"] = db["theses"][-300:]   # keep ~3 years
    _save_json(THESIS_DB, db)
    return len(new_theses)


def verify_past_theses() -> dict:
    """
    Load theses from 1-4 weeks ago and verify if they played out.
    A thesis is correct if the stock moved in the predicted direction.
    """
    db = _load_json(THESIS_DB, {"theses": []})
    if not db["theses"]:
        return {"verified": 0, "correct": 0, "accuracy": None, "results": []}

    today     = datetime.utcnow().strftime("%Y-%m-%d")
    week_ago  = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    month_ago = (datetime.utcnow() - timedelta(days=28)).strftime("%Y-%m-%d")

    # Find unverified theses that are old enough (1-4 weeks)
    to_verify = [t for t in db["theses"]
                 if not t.get("verified")
                 and t.get("date", "") <= week_ago
                 and t.get("date", "") >= month_ago]

    verified, correct, results = 0, 0, []

    for thesis in to_verify:
        ticker   = thesis.get("ticker")
        price_at = thesis.get("price_at")
        if not ticker or not price_at:
            thesis["verified"] = True
            continue

        price_now = _price(ticker)
        time.sleep(0.15)
        if not price_now:
            continue

        pct = round((price_now - price_at) / price_at * 100, 2)
        # For now: positive = correct (bullish thesis assumed unless "short" in thesis)
        is_bearish = any(w in thesis.get("thesis", "").lower()
                         for w in ["short", "avoid", "exit", "reduce", "risk-off"])
        is_correct = (pct < 0) if is_bearish else (pct > 0)

        thesis["verified"]        = True
        thesis["outcome_pct"]     = pct
        thesis["outcome_date"]    = today
        thesis["outcome_correct"] = is_correct
        verified += 1
        if is_correct:
            correct += 1

        results.append({
            "ticker":    ticker,
            "thesis":    thesis.get("thesis", "")[:80],
            "source":    thesis.get("source", ""),
            "price_at":  price_at,
            "price_now": price_now,
            "pct":       pct,
            "correct":   is_correct,
            "days":      (datetime.utcnow() - datetime.strptime(
                          thesis["date"], "%Y-%m-%d")).days,
        })

    _save_json(THESIS_DB, db)

    accuracy = round(correct / verified * 100, 1) if verified else None
    return {"verified": verified, "correct": correct,
            "accuracy": accuracy, "results": results}


# ═══════════════════════════════════════════════════════════════════════════════
# DIMENSION 3 — ANALYZE SMARTER: Earnings Intelligence + Macro Memory
# ═══════════════════════════════════════════════════════════════════════════════

def accumulate_knowledge(nrgc_assessments: dict, synthesis: dict,
                         insights: list):
    """
    Build the persistent knowledge base — grows every week.
    Stores: earnings patterns, macro turning points, theme catalysts,
            narrative signals that predicted big moves.
    """
    kb   = _load_json(KNOWLEDGE_BASE, {
        "earnings_patterns": {},
        "macro_turning_points": [],
        "theme_catalysts": {},
        "narrative_signals": [],
        "updated_count": 0,
    })
    today = datetime.utcnow().strftime("%Y-%m-%d")
    added = 0

    # ── Earnings patterns per ticker ──────────────────────────────────────────
    for ticker, nrgc in nrgc_assessments.items():
        rev = nrgc.get("revenue_signal", {})
        qoq = rev.get("latest_qoq_pct")
        if qoq is None:
            continue

        if ticker not in kb["earnings_patterns"]:
            kb["earnings_patterns"][ticker] = {
                "theme": nrgc.get("theme", ""),
                "history": [],
                "peak_qoq": None,
                "phase_at_peak": None,
                "trend": None,
            }

        ep = kb["earnings_patterns"][ticker]
        last_entry = ep["history"][-1] if ep["history"] else {}

        # Only add if new data (QoQ changed)
        if last_entry.get("qoq") != qoq:
            ep["history"].append({
                "date":   today,
                "qoq":    qoq,
                "margin": rev.get("latest_margin"),
                "phase":  nrgc.get("phase"),
                "score":  nrgc.get("nrgc_composite_score"),
            })
            ep["history"] = ep["history"][-20:]  # 5 years of quarters

            # Update peak
            all_qoq = [h["qoq"] for h in ep["history"] if h.get("qoq")]
            if all_qoq:
                ep["peak_qoq"] = max(all_qoq)
                peak_idx = next(i for i, h in enumerate(ep["history"])
                                if h.get("qoq") == ep["peak_qoq"])
                ep["phase_at_peak"] = ep["history"][peak_idx].get("phase")

            # QoQ trend: last 3 quarters
            recent_qoq = [h["qoq"] for h in ep["history"][-3:] if h.get("qoq")]
            if len(recent_qoq) >= 2:
                ep["trend"] = ("accelerating" if recent_qoq[-1] > recent_qoq[-2]
                               else "decelerating")
            added += 1

    # ── Macro turning points ──────────────────────────────────────────────────
    regime = (synthesis or {}).get("regime_signal", "")
    if regime:
        last_macro = (kb["macro_turning_points"][-1]
                      if kb["macro_turning_points"] else {})
        if last_macro.get("regime") != regime:
            kb["macro_turning_points"].append({
                "date":   today,
                "regime": regime,
                "themes": (synthesis or {}).get("top_themes", [])[:3],
                "narrative": (synthesis or {}).get("macro_narrative", "")[:150],
            })
            kb["macro_turning_points"] = kb["macro_turning_points"][-52:]
            added += 1

    # ── Theme catalysts from insights ─────────────────────────────────────────
    for ins in (insights or []):
        for theme in ins.get("themes", []):
            if theme not in kb["theme_catalysts"]:
                kb["theme_catalysts"][theme] = {"catalysts": [], "signal_count": 0}
            tc = kb["theme_catalysts"][theme]
            tc["signal_count"] += 1

            # Store high-conviction catalysts (urgency = immediate or high)
            if ins.get("urgency") in ("immediate", "high") and ins.get("headline"):
                catalyst = {
                    "date":     today,
                    "headline": ins["headline"][:120],
                    "ticker":   ins.get("ticker"),
                    "urgency":  ins.get("urgency"),
                }
                # Deduplicate
                if catalyst["headline"] not in [c["headline"] for c in tc["catalysts"]]:
                    tc["catalysts"].append(catalyst)
                    tc["catalysts"] = tc["catalysts"][-50:]  # keep last 50 per theme
                    added += 1

    # ── Narrative signals that hit Phase 3 (high value signals) ──────────────
    phase3_tickers = [t for t, a in nrgc_assessments.items() if a.get("phase") == 3]
    for ins in (insights or []):
        if ins.get("ticker") in phase3_tickers and ins.get("urgency") in ("immediate", "high"):
            signal = {
                "date":     today,
                "ticker":   ins.get("ticker"),
                "headline": ins.get("headline", "")[:100],
                "theme":    ins.get("themes", [None])[0],
                "signal_type": ins.get("signal_type", "news"),
                "led_to_phase3": True,
            }
            # Avoid exact duplicates
            if signal["headline"] and signal["headline"] not in [
                    s["headline"] for s in kb["narrative_signals"][-50:]]:
                kb["narrative_signals"].append(signal)
                kb["narrative_signals"] = kb["narrative_signals"][-200:]
                added += 1

    kb["updated_count"] += 1
    kb["last_updated"] = today
    _save_json(KNOWLEDGE_BASE, kb)

    return {"added": added, "total_tickers": len(kb["earnings_patterns"]),
            "turning_points": len(kb["macro_turning_points"]),
            "theme_catalysts": len(kb["theme_catalysts"]),
            "narrative_signals": len(kb["narrative_signals"])}


# ═══════════════════════════════════════════════════════════════════════════════
# LESSON GENERATION — All 4 Dimensions
# ═══════════════════════════════════════════════════════════════════════════════

RESEARCH_LESSON_PROMPT = """You are the Research Team Learning Agent for AlphaAbsolute.
Mission: Make the research team (Agents 01/02/04/05/09/15) read better,
write more precise theses, analyze deeper, and build lasting knowledge.

THIS WEEK'S RESEARCH PERFORMANCE:

THESIS ACCURACY:
- Theses verified: {thesis_verified}
- Correct predictions: {thesis_correct}
- Accuracy: {thesis_accuracy}
- Best thesis: {best_thesis}
- Worst thesis: {worst_thesis}

TOP SOURCES BY QUALITY SCORE:
{source_rankings}

KNOWLEDGE BASE GROWTH:
- Earnings patterns tracked: {kb_tickers} tickers
- Macro turning points recorded: {kb_macro}
- Theme catalysts stored: {kb_themes} themes
- Phase-3 narrative signals: {kb_signals}

RECENT THESIS RESULTS:
{thesis_results}

Generate exactly 3 research improvement lessons:

LESSON 1 (READ BETTER): Which source is most/least valuable? What to read more/less?
EVIDENCE: [specific data from source rankings]
ACTION: [exact change to source priority or reading strategy]

LESSON 2 (WRITE DEEPER): What made the best thesis correct? What patterns predict success?
EVIDENCE: [specific thesis data — which tickers, what worked]
ACTION: [exact change to how we frame investment theses]

LESSON 3 (ANALYZE SMARTER): What earnings/macro/narrative pattern emerged this week?
EVIDENCE: [specific pattern from knowledge base or earnings data]
ACTION: [exact analytical rule to apply going forward]"""


def generate_research_lessons(source_rankings: list, thesis_verification: dict,
                               kb_stats: dict) -> str:
    results   = thesis_verification.get("results", [])
    verified  = thesis_verification.get("verified", 0)
    correct   = thesis_verification.get("correct", 0)
    accuracy  = thesis_verification.get("accuracy")

    # Best/worst thesis
    if results:
        best  = max(results, key=lambda x: abs(x.get("pct", 0)))
        worst = min(results, key=lambda x: x.get("pct", 0)
                              if not x.get("correct") else 100)
        best_str  = (f"{best['ticker']}: {best.get('thesis','')[:60]} "
                     f"→ {best.get('pct',0):+.1f}%")
        worst_str = (f"{worst['ticker']}: {worst.get('thesis','')[:60]} "
                     f"→ {worst.get('pct',0):+.1f}%")
    else:
        best_str = worst_str = "No data yet"

    # Source ranking text
    src_txt = ""
    for name, data in (source_rankings or [])[:8]:
        src_txt += (f"  {name}: quality={data.get('quality_score',0):.0f} "
                    f"items={data.get('total_items',0)} "
                    f"insights={data.get('total_insights',0)} "
                    f"triggered={data.get('triggered',0)}\n")

    # Thesis results text
    thesis_txt = ""
    for r in results[:8]:
        icon = "CORRECT" if r.get("correct") else "WRONG"
        thesis_txt += (f"  [{icon}] {r['ticker']} {r.get('pct',0):+.1f}% "
                       f"({r.get('days',0)}d): {r.get('thesis','')[:60]}\n")

    prompt = RESEARCH_LESSON_PROMPT.format(
        thesis_verified=verified,
        thesis_correct=correct,
        thesis_accuracy=f"{accuracy:.0f}%" if accuracy else "N/A (first week)",
        best_thesis=best_str,
        worst_thesis=worst_str,
        source_rankings=src_txt or "No source data yet",
        kb_tickers=kb_stats.get("total_tickers", 0),
        kb_macro=kb_stats.get("turning_points", 0),
        kb_themes=kb_stats.get("theme_catalysts", 0),
        kb_signals=kb_stats.get("narrative_signals", 0),
        thesis_results=thesis_txt or "No verified theses yet — first week",
    )
    return _llm(prompt, max_tokens=900)


# ═══════════════════════════════════════════════════════════════════════════════
# WRITE TO MEMORY
# ═══════════════════════════════════════════════════════════════════════════════

def write_research_memory(lessons: str, source_rankings: list,
                           thesis_verification: dict, kb_stats: dict):
    """Write all research learnings to persistent memory files."""
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # ── Research Learnings (lessons) ──────────────────────────────────────────
    section = f"\n\n---\n## Research Lessons — {today}\n\n"
    if lessons:
        section += lessons
    RESEARCH_MEM.write_text(
        (_load_txt(RESEARCH_MEM) or
         "# Research Team Lifetime Learnings\nAuto-updated weekly.\n") + section,
        encoding="utf-8"
    )

    # ── Source Quality Ranking ─────────────────────────────────────────────────
    src_section = f"\n\n## Source Rankings — {today}\n"
    src_section += "| Source | Quality | Items | Insights | Triggered |\n"
    src_section += "|---|---|---|---|---|\n"
    for name, data in (source_rankings or [])[:10]:
        src_section += (f"| {name} | {data.get('quality_score',0):.0f} "
                        f"| {data.get('total_items',0)} "
                        f"| {data.get('total_insights',0)} "
                        f"| {data.get('triggered',0)} |\n")
    SOURCE_MEM.write_text(
        (_load_txt(SOURCE_MEM) or
         "# Source Quality Rankings\nAuto-updated weekly. Read top sources first.\n") + src_section,
        encoding="utf-8"
    )

    # ── Thesis Tracker ────────────────────────────────────────────────────────
    results = thesis_verification.get("results", [])
    accuracy = thesis_verification.get("accuracy")
    thesis_section = f"\n\n## Thesis Outcomes — {today}\n"
    thesis_section += (f"Verified: {thesis_verification.get('verified',0)} | "
                       f"Correct: {thesis_verification.get('correct',0)} | "
                       f"Accuracy: {accuracy:.0f}%\n\n" if accuracy else
                       f"Verified: {thesis_verification.get('verified',0)} (first week)\n\n")
    for r in results[:10]:
        icon = "✅" if r.get("correct") else "❌"
        thesis_section += (f"{icon} **{r['ticker']}** {r.get('pct',0):+.1f}% "
                           f"({r.get('days',0)}d) — {r.get('thesis','')[:70]}\n")
    THESIS_MEM.write_text(
        (_load_txt(THESIS_MEM) or
         "# Thesis Tracker\nAuto-updated weekly. Measures research narrative accuracy.\n")
        + thesis_section,
        encoding="utf-8"
    )

    # ── Knowledge Base Summary (append weekly snapshot) ───────────────────────
    kb = _load_json(KNOWLEDGE_BASE, {})
    kb_summary_file = MEM_DIR / "knowledge_base_summary.md"
    kb_section = f"\n\n## KB Snapshot — {today}\n"
    kb_section += f"- Earnings patterns: **{kb_stats.get('total_tickers',0)} tickers**\n"
    kb_section += f"- Macro turning points: {kb_stats.get('turning_points',0)}\n"
    kb_section += f"- Theme catalysts: {kb_stats.get('theme_catalysts',0)} themes\n"
    kb_section += f"- Phase-3 narrative signals: {kb_stats.get('narrative_signals',0)}\n"

    # Show top 5 earnings acceleration stories
    patterns = kb.get("earnings_patterns", {})
    accel = [(t, d) for t, d in patterns.items() if d.get("trend") == "accelerating"]
    if accel:
        kb_section += "\n**Accelerating QoQ this week:**\n"
        for ticker, data in sorted(accel, key=lambda x: x[1].get("history", [{}])[-1].get("qoq", 0),
                                    reverse=True)[:5]:
            latest = data.get("history", [{}])[-1]
            kb_section += f"- {ticker} ({data.get('theme','?')}): QoQ {latest.get('qoq',0):+.1f}%\n"

    kb_summary_file.write_text(
        (_load_txt(kb_summary_file) or
         "# Knowledge Base Summary\nGrows every week. Research team reads this before analysis.\n")
        + kb_section,
        encoding="utf-8"
    )


def _load_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def run_research_memory_loop(raw_items: dict, insights: list, synthesis: dict,
                              nrgc_assessments: dict, portfolio: dict,
                              focus_result: dict) -> dict:
    """
    Full research memory cycle — called from weekly_runner.
    1. Update source quality (READ BETTER)
    2. Verify past theses (WRITE DEEPER)
    3. Accumulate knowledge base (ANALYZE SMARTER + STORE FOREVER)
    4. Generate research lessons
    5. Write all memory files
    Returns summary for Telegram.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEM_DIR.mkdir(parents=True, exist_ok=True)

    print("  [ResearchMem] Updating source quality rankings...")
    source_rankings = update_source_quality(
        raw_items or {}, insights or [], focus_result or {}, portfolio or {})
    top_source = source_rankings[0][0] if source_rankings else "N/A"
    print(f"    Top source: {top_source} | Total sources: {len(source_rankings)}")

    print("  [ResearchMem] Verifying past theses...")
    thesis_verification = verify_past_theses()
    accuracy = thesis_verification.get("accuracy")
    print(f"    Verified: {thesis_verification['verified']} | "
          f"Accuracy: {accuracy:.0f}%" if accuracy else
          f"    Verified: {thesis_verification['verified']} (no accuracy yet)")

    print("  [ResearchMem] Building knowledge base...")
    kb_stats = accumulate_knowledge(
        nrgc_assessments or {}, synthesis or {}, insights or [])
    print(f"    KB: {kb_stats['total_tickers']} tickers | "
          f"+{kb_stats['added']} new facts")

    print("  [ResearchMem] Saving this week's theses...")
    n_theses = save_weekly_theses(synthesis or {}, nrgc_assessments or {})
    print(f"    Saved {n_theses} theses for next week's verification")

    print("  [ResearchMem] Generating research lessons (Groq/Gemini)...")
    lessons = ""
    if thesis_verification["verified"] > 0 or len(source_rankings) > 0:
        lessons = generate_research_lessons(source_rankings, thesis_verification, kb_stats)

    print("  [ResearchMem] Writing to memory files...")
    write_research_memory(lessons, source_rankings, thesis_verification, kb_stats)

    return {
        "top_source":         top_source,
        "sources_ranked":     len(source_rankings),
        "theses_verified":    thesis_verification.get("verified", 0),
        "thesis_accuracy":    thesis_verification.get("accuracy"),
        "kb_tickers":         kb_stats.get("total_tickers", 0),
        "kb_added":           kb_stats.get("added", 0),
        "theses_saved":       n_theses,
        "lessons_generated":  bool(lessons),
        "thesis_results":     thesis_verification.get("results", [])[:5],
    }
