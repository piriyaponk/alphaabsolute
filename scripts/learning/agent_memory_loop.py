"""
AlphaAbsolute — Agent Memory Loop
Tracks predictions from all agents, verifies outcomes weekly,
generates lessons, writes to each agent's memory file.
All agents get smarter automatically — no LLM cost except Groq (free).

How it works:
  1. Weekly runner saves "agent calls" (structured predictions) to data/agent_memory/
  2. Next week, this module compares calls to reality
  3. Generates 1-2 lessons per agent via Groq
  4. Appends to memory/agent_XX_learnings.md
  5. Agents read their own memory at start of next Claude session
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

BASE_DIR   = Path(__file__).resolve().parents[2]
MEM_DIR    = BASE_DIR / "memory"
CALLS_DIR  = BASE_DIR / "data" / "agent_memory"


# ─── LLM (Groq → Gemini free) ─────────────────────────────────────────────────
def _llm(prompt: str, max_tokens: int = 600) -> str:
    for key, url, body in [
        (
            os.environ.get("GROQ_API_KEY", ""),
            "https://api.groq.com/openai/v1/chat/completions",
            lambda k: {"model": "llama-3.3-70b-versatile",
                       "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens},
        ),
        (
            os.environ.get("GEMINI_API_KEY", ""),
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
            lambda k: {"contents": [{"parts": [{"text": prompt}]}]},
        ),
    ]:
        if not key:
            continue
        try:
            params = {"key": key} if "gemini" in url else {}
            headers = ({} if "gemini" in url else
                       {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            r = requests.post(url, headers=headers, params=params,
                              json=body(key), timeout=30, verify=False)
            data = r.json()
            if "gemini" in url:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            return data["choices"][0]["message"]["content"]
        except Exception:
            continue
    return ""


# ─── Live Price ───────────────────────────────────────────────────────────────
def _price(ticker: str) -> Optional[float]:
    try:
        s = requests.Session(); s.verify = False
        s.headers["User-Agent"] = "Mozilla/5.0"
        r = s.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                  params={"interval": "1d", "range": "5d"}, timeout=10)
        res = r.json()["chart"]["result"][0]
        return float(res["meta"]["regularMarketPrice"])
    except Exception:
        return None


def _prices(tickers: list) -> dict:
    result = {}
    for t in tickers:
        p = _price(t)
        if p:
            result[t] = p
        time.sleep(0.2)
    return result


# ─── Save Calls ───────────────────────────────────────────────────────────────
def save_agent_calls(synthesis: dict, nrgc_assessments: dict,
                     auto_trade_result: dict, focus_result: dict,
                     portfolio: dict, audit_log: list):
    """
    Called at END of weekly run. Saves each agent's predictions
    for verification next week.
    """
    CALLS_DIR.mkdir(parents=True, exist_ok=True)
    week = datetime.utcnow().strftime("%Y-W%V")
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # ── Agent 01: Macro regime + top themes ──────────────────────────────────
    qqq_now = _price("QQQ")
    agent01_call = {
        "agent": "01_macro",
        "week": week,
        "date": today,
        "regime_call": synthesis.get("regime_signal", "neutral") if synthesis else "neutral",
        "top_themes": (synthesis.get("top_themes", []) if synthesis else [])[:5],
        "top_opportunities": [o.get("ticker") for o in
                               (synthesis.get("top_opportunities", []) if synthesis else [])
                               if o.get("ticker")][:5],
        "qqq_at_call": qqq_now,
        "verified": False,
    }

    # ── Agent 03: Screener — NRGC phase 3 picks ───────────────────────────────
    phase3_picks = [t for t, a in nrgc_assessments.items() if a.get("phase") == 3]
    phase2_picks = [t for t, a in nrgc_assessments.items() if a.get("phase") == 2]
    agent03_call = {
        "agent": "03_screener",
        "week": week,
        "date": today,
        "phase3_picks": phase3_picks,
        "phase2_picks": phase2_picks,
        "prices_at_call": _prices(phase3_picks[:6]),
        "verified": False,
    }

    # ── Agent 05: Thematic — conviction per theme ────────────────────────────
    theme_convictions = {}
    for ticker, nrgc in nrgc_assessments.items():
        theme = nrgc.get("theme", "Unknown")
        phase = nrgc.get("phase", 0)
        score = nrgc.get("nrgc_composite_score", 0)
        if theme not in theme_convictions:
            theme_convictions[theme] = {"tickers": [], "avg_score": 0, "phase3_count": 0}
        theme_convictions[theme]["tickers"].append(ticker)
        theme_convictions[theme]["avg_score"] += score
        if phase == 3:
            theme_convictions[theme]["phase3_count"] += 1

    for theme, data in theme_convictions.items():
        n = len(data["tickers"])
        data["avg_score"] = round(data["avg_score"] / n, 1) if n else 0
        # Conviction: 1-5 based on avg score + phase3 count
        data["conviction"] = min(5, 1 + int(data["avg_score"] / 20) + data["phase3_count"])

    # Representative ticker per theme for price tracking
    theme_rep_prices = {}
    for theme, data in theme_convictions.items():
        rep = data["tickers"][0] if data["tickers"] else None
        if rep:
            p = _price(rep)
            if p:
                theme_rep_prices[theme] = {"ticker": rep, "price": p}
            time.sleep(0.15)

    agent05_call = {
        "agent": "05_thematic",
        "week": week,
        "date": today,
        "theme_convictions": theme_convictions,
        "theme_rep_prices": theme_rep_prices,
        "verified": False,
    }

    # ── Agent 09: Top opportunities ───────────────────────────────────────────
    opps = (synthesis.get("top_opportunities", []) if synthesis else [])[:5]
    opp_tickers = [o.get("ticker") for o in opps if o.get("ticker")]
    agent09_call = {
        "agent": "09_strategist",
        "week": week,
        "date": today,
        "opportunities": opps,
        "prices_at_call": _prices(opp_tickers),
        "verified": False,
    }

    # ── Agent 12: Risk flags ──────────────────────────────────────────────────
    positions = portfolio.get("positions", {})
    risk_flags = []
    for ticker, pos in positions.items():
        pnl = pos.get("pnl_pct", 0)
        days = pos.get("days_held", 0)
        if pnl <= -5:
            risk_flags.append({"ticker": ticker, "flag": "near_stop", "pnl": pnl})
        if days > 30 and pnl < 5:
            risk_flags.append({"ticker": ticker, "flag": "dead_money", "pnl": pnl, "days": days})

    agent12_call = {
        "agent": "12_risk",
        "week": week,
        "date": today,
        "risk_flags": risk_flags,
        "n_positions": len(positions),
        "cash_pct": round(portfolio.get("cash", 0) /
                          max(portfolio.get("cash", 100000) +
                              sum(p.get("shares", 0) * p.get("current_price", p.get("entry_price", 0))
                                  for p in positions.values()), 1) * 100, 1),
        "verified": False,
    }

    # ── Agent 16: Audit quality ───────────────────────────────────────────────
    agent16_call = {
        "agent": "16_auditor",
        "week": week,
        "date": today,
        "n_fixes": len([l for l in audit_log if l.startswith("FIX")]),
        "n_warnings": len([l for l in audit_log if l.startswith("WARN")]),
        "n_removals": len([l for l in audit_log if l.startswith("REMOVE")]),
        "log_sample": audit_log[:5],
        "verified": False,
    }

    # ── Save all calls ────────────────────────────────────────────────────────
    calls_file = CALLS_DIR / f"calls_{week}.json"
    all_calls = {
        "week": week,
        "date": today,
        "agent_01": agent01_call,
        "agent_03": agent03_call,
        "agent_05": agent05_call,
        "agent_09": agent09_call,
        "agent_12": agent12_call,
        "agent_16": agent16_call,
    }
    calls_file.write_text(json.dumps(all_calls, indent=2, default=str), encoding="utf-8")
    return all_calls


# ─── Verify Last Week's Calls ─────────────────────────────────────────────────
def verify_agent_calls(portfolio: dict) -> dict:
    """
    Load last week's agent calls and verify outcomes.
    Returns dict of agent_id → {accuracy, lessons_raw, ...}
    """
    CALLS_DIR.mkdir(parents=True, exist_ok=True)
    last_week = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-W%V")
    calls_file = CALLS_DIR / f"calls_{last_week}.json"

    if not calls_file.exists():
        return {}

    try:
        calls = json.loads(calls_file.read_text(encoding="utf-8"))
    except Exception:
        return {}

    results = {}
    qqq_now = _price("QQQ")

    # ── Agent 01: Was regime correct? ────────────────────────────────────────
    a01 = calls.get("agent_01", {})
    if a01 and qqq_now and a01.get("qqq_at_call"):
        qqq_start  = a01["qqq_at_call"]
        qqq_return = (qqq_now - qqq_start) / qqq_start * 100
        regime     = a01.get("regime_call", "neutral")
        # Risk-on correct if QQQ > +1%, risk-off correct if QQQ < -1%
        correct = ((regime == "risk-on"  and qqq_return >  1) or
                   (regime == "risk-off" and qqq_return < -1) or
                   (regime == "neutral"  and -1 <= qqq_return <= 1))
        # Check top opportunity performance
        opp_prices_start = a01.get("prices_at_call") or {}  # from agent09
        results["agent_01"] = {
            "regime_call": regime,
            "qqq_return_1w": round(qqq_return, 2),
            "regime_correct": correct,
            "top_themes": a01.get("top_themes", []),
        }

    # ── Agent 03: Did phase3 picks outperform? ───────────────────────────────
    a03 = calls.get("agent_03", {})
    if a03:
        picks  = a03.get("phase3_picks", [])
        prices_start = a03.get("prices_at_call", {})
        if picks and prices_start:
            prices_now = _prices(list(prices_start.keys()))
            returns = {}
            for t, p0 in prices_start.items():
                p1 = prices_now.get(t)
                if p1 and p0:
                    returns[t] = round((p1 - p0) / p0 * 100, 2)
            avg_return = (round(sum(returns.values()) / len(returns), 2)
                         if returns else None)
            beat_qqq_count = sum(1 for r in returns.values()
                                 if r > (results.get("agent_01", {}).get("qqq_return_1w", 0)))
            results["agent_03"] = {
                "phase3_picks": picks,
                "returns_1w": returns,
                "avg_return": avg_return,
                "beat_qqq_count": beat_qqq_count,
                "total_picks": len(picks),
            }

    # ── Agent 05: Did high-conviction themes outperform? ─────────────────────
    a05 = calls.get("agent_05", {})
    if a05:
        rep_prices = a05.get("theme_rep_prices", {})
        theme_results = {}
        for theme, data in rep_prices.items():
            ticker = data.get("ticker")
            p0     = data.get("price")
            if ticker and p0:
                p1 = _price(ticker)
                time.sleep(0.15)
                if p1:
                    ret = round((p1 - p0) / p0 * 100, 2)
                    conviction = a05.get("theme_convictions", {}).get(theme, {}).get("conviction", 3)
                    theme_results[theme] = {"return_1w": ret, "conviction": conviction,
                                            "ticker": ticker}
        # Did high conviction (4-5) beat low conviction (1-2)?
        high_c = [v["return_1w"] for v in theme_results.values() if v.get("conviction", 0) >= 4]
        low_c  = [v["return_1w"] for v in theme_results.values() if v.get("conviction", 0) <= 2]
        high_avg = round(sum(high_c) / len(high_c), 2) if high_c else None
        low_avg  = round(sum(low_c)  / len(low_c),  2) if low_c  else None
        results["agent_05"] = {
            "theme_results": theme_results,
            "high_conviction_avg": high_avg,
            "low_conviction_avg":  low_avg,
            "conviction_edge": (round(high_avg - low_avg, 2)
                                if high_avg is not None and low_avg is not None else None),
        }

    # ── Agent 09: Did top opportunities work? ────────────────────────────────
    a09 = calls.get("agent_09", {})
    if a09:
        prices_start = a09.get("prices_at_call", {})
        if prices_start:
            prices_now = _prices(list(prices_start.keys()))
            opp_returns = {}
            for t, p0 in prices_start.items():
                p1 = prices_now.get(t)
                if p1 and p0:
                    opp_returns[t] = round((p1 - p0) / p0 * 100, 2)
            qqq_1w = results.get("agent_01", {}).get("qqq_return_1w", 0)
            winners = {t: r for t, r in opp_returns.items() if r > qqq_1w}
            results["agent_09"] = {
                "opportunities": list(prices_start.keys()),
                "returns_1w": opp_returns,
                "beat_qqq": len(winners),
                "total": len(opp_returns),
                "hit_rate": round(len(winners) / len(opp_returns) * 100, 1) if opp_returns else 0,
            }

    # ── Agent 12: Were risk flags correct? ───────────────────────────────────
    a12 = calls.get("agent_12", {})
    if a12:
        flags    = a12.get("risk_flags", [])
        closed   = portfolio.get("closed", [])
        week_ago = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        recent_exits = {t.get("ticker"): t for t in closed
                        if t.get("exit_date", "") >= week_ago}
        flag_outcomes = []
        for flag in flags:
            ticker = flag.get("ticker")
            exit_t = recent_exits.get(ticker)
            flag_outcomes.append({
                "ticker":   ticker,
                "flag":     flag.get("flag"),
                "pnl_then": flag.get("pnl"),
                "exited":   exit_t is not None,
                "exit_pnl": exit_t.get("pnl_pct") if exit_t else None,
                "correct":  exit_t is not None and exit_t.get("pnl_pct", 0) < flag.get("pnl", 0),
            })
        correct_flags = [f for f in flag_outcomes if f.get("correct")]
        results["agent_12"] = {
            "flags_raised": len(flags),
            "flags_correct": len(correct_flags),
            "accuracy": round(len(correct_flags) / len(flags) * 100, 1) if flags else None,
            "outcomes": flag_outcomes,
        }

    # ── Agent 16: Audit quality trend ────────────────────────────────────────
    a16 = calls.get("agent_16", {})
    if a16:
        # Load last 4 weeks of audit logs to find trend
        history = []
        for i in range(1, 5):
            wk = (datetime.utcnow() - timedelta(weeks=i)).strftime("%Y-W%V")
            f  = CALLS_DIR / f"calls_{wk}.json"
            if f.exists():
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                    history.append(d.get("agent_16", {}).get("n_fixes", 0))
                except Exception:
                    pass
        trend = "improving" if (len(history) >= 2 and history[0] < history[-1]) else \
                "worsening" if (len(history) >= 2 and history[0] > history[-1]) else "stable"
        results["agent_16"] = {
            "fixes_last_week": a16.get("n_fixes", 0),
            "warnings": a16.get("n_warnings", 0),
            "fix_trend_4w": history,
            "trend": trend,
        }

    return results


# ─── Generate Lessons Per Agent ───────────────────────────────────────────────
AGENT_LESSON_PROMPTS = {
    "agent_01": """You are Agent 01 (Macro Intelligence) of AlphaAbsolute.
Your regime call was: {regime_call}
QQQ 1-week return: {qqq_return:+.1f}%
Regime correct: {correct}
Top themes called: {themes}

Generate 1-2 lessons to improve future regime calls and theme selection.
Format: LESSON: [rule] | EVIDENCE: [what happened] | ACTION: [specific change]""",

    "agent_03": """You are Agent 03 (Factor & Screener) of AlphaAbsolute.
Phase 3 picks this week: {picks}
1-week returns: {returns}
Average return: {avg_return:+.1f}%
Beat QQQ ({qqq_return:+.1f}%): {beat_qqq}/{total} picks

Generate 1-2 lessons to improve NRGC phase 3 selection accuracy.
Format: LESSON: [rule] | EVIDENCE: [what happened] | ACTION: [specific change]""",

    "agent_05": """You are Agent 05 (Thematic Research) of AlphaAbsolute.
High conviction themes (4-5): avg return {high_avg:+.1f}%
Low conviction themes (1-2): avg return {low_avg:+.1f}%
Conviction edge: {edge:+.1f}%
Theme results: {theme_results}

Generate 1-2 lessons to improve theme conviction scoring.
Format: LESSON: [rule] | EVIDENCE: [what happened] | ACTION: [specific change]""",

    "agent_09": """You are Agent 09 (Macro Strategist) of AlphaAbsolute.
Top opportunities called: {opps}
1-week returns: {returns}
Beat QQQ rate: {hit_rate:.0f}%

Generate 1-2 lessons to improve opportunity identification.
Format: LESSON: [rule] | EVIDENCE: [what happened] | ACTION: [specific change]""",

    "agent_12": """You are Agent 12 (Risk Devil's Advocate) of AlphaAbsolute.
Risk flags raised: {flags_raised}
Flags that were correct warnings: {flags_correct} ({accuracy})%
Flag outcomes: {outcomes}

Generate 1-2 lessons to improve risk flagging accuracy.
Format: LESSON: [rule] | EVIDENCE: [what happened] | ACTION: [specific change]""",

    "agent_16": """You are Agent 16 (Auditor) of AlphaAbsolute.
Corrections made last week: {fixes}
4-week correction trend: {trend_4w} → {trend}

Generate 1 lesson about data quality patterns and how to prevent recurring errors.
Format: LESSON: [rule] | EVIDENCE: [what happened] | ACTION: [specific change]""",
}


def generate_agent_lessons(verification_results: dict) -> dict:
    """Generate lessons for each agent based on verification results."""
    lessons = {}

    for agent_id, data in verification_results.items():
        prompt_template = AGENT_LESSON_PROMPTS.get(agent_id)
        if not prompt_template:
            continue

        try:
            if agent_id == "agent_01":
                prompt = prompt_template.format(
                    regime_call=data["regime_call"],
                    qqq_return=data["qqq_return_1w"],
                    correct=data["regime_correct"],
                    themes=data["top_themes"],
                )
            elif agent_id == "agent_03":
                avg = data.get("avg_return") or 0
                qqq = verification_results.get("agent_01", {}).get("qqq_return_1w", 0)
                prompt = prompt_template.format(
                    picks=data["phase3_picks"],
                    returns=data.get("returns_1w", {}),
                    avg_return=avg,
                    qqq_return=qqq,
                    beat_qqq=data.get("beat_qqq_count", 0),
                    total=data.get("total_picks", 0),
                )
            elif agent_id == "agent_05":
                ha = data.get("high_conviction_avg") or 0
                la = data.get("low_conviction_avg") or 0
                prompt = prompt_template.format(
                    high_avg=ha, low_avg=la,
                    edge=data.get("conviction_edge") or (ha - la),
                    theme_results=json.dumps(data.get("theme_results", {}), indent=2),
                )
            elif agent_id == "agent_09":
                prompt = prompt_template.format(
                    opps=data.get("opportunities", []),
                    returns=data.get("returns_1w", {}),
                    hit_rate=data.get("hit_rate", 0),
                )
            elif agent_id == "agent_12":
                prompt = prompt_template.format(
                    flags_raised=data.get("flags_raised", 0),
                    flags_correct=data.get("flags_correct", 0),
                    accuracy=data.get("accuracy", "N/A"),
                    outcomes=data.get("outcomes", []),
                )
            elif agent_id == "agent_16":
                prompt = prompt_template.format(
                    fixes=data.get("fixes_last_week", 0),
                    trend_4w=data.get("fix_trend_4w", []),
                    trend=data.get("trend", "stable"),
                )
            else:
                continue

            raw = _llm(prompt, max_tokens=500)
            if raw:
                lessons[agent_id] = {"raw": raw, "data": data}

        except Exception as e:
            lessons[agent_id] = {"error": str(e), "data": data}

    return lessons


# ─── Write to Agent Memory Files ──────────────────────────────────────────────
AGENT_NAMES = {
    "agent_01": "Agent 01 — Macro Intelligence",
    "agent_03": "Agent 03 — Factor & Screener",
    "agent_05": "Agent 05 — Thematic Research",
    "agent_09": "Agent 09 — Macro Strategist",
    "agent_12": "Agent 12 — Risk Devil's Advocate",
    "agent_16": "Agent 16 — Auditor",
}


def write_agent_memory(lessons: dict, verification: dict):
    """Write lessons to each agent's memory file."""
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    written = []

    for agent_id, lesson_data in lessons.items():
        if "error" in lesson_data or not lesson_data.get("raw"):
            continue

        mem_file = MEM_DIR / f"{agent_id}_learnings.md"
        agent_name = AGENT_NAMES.get(agent_id, agent_id)

        # Build section
        data = verification.get(agent_id, {})
        section = f"\n\n---\n## {today} — Weekly Learning\n\n"

        # Add key metric for each agent
        if agent_id == "agent_01":
            section += (f"Regime: {data.get('regime_call')} | "
                        f"QQQ 1W: {data.get('qqq_return_1w',0):+.1f}% | "
                        f"Correct: {data.get('regime_correct')}\n\n")
        elif agent_id == "agent_03":
            section += (f"Phase3 picks: {len(data.get('phase3_picks',[]))} | "
                        f"Avg return: {data.get('avg_return',0):+.1f}% | "
                        f"Beat QQQ: {data.get('beat_qqq_count',0)}/{data.get('total_picks',0)}\n\n")
        elif agent_id == "agent_05":
            section += (f"High-conviction edge: {data.get('conviction_edge',0):+.1f}%pp\n\n")
        elif agent_id == "agent_09":
            section += (f"Opportunity hit rate: {data.get('hit_rate',0):.0f}%\n\n")
        elif agent_id == "agent_12":
            section += (f"Risk flags: {data.get('flags_raised',0)} raised | "
                        f"{data.get('flags_correct',0)} correct | "
                        f"Accuracy: {data.get('accuracy','N/A')}%\n\n")
        elif agent_id == "agent_16":
            section += (f"Fixes made: {data.get('fixes_last_week',0)} | "
                        f"Trend: {data.get('trend','stable')}\n\n")

        section += lesson_data["raw"]

        existing = mem_file.read_text(encoding="utf-8") if mem_file.exists() else (
            f"# {agent_name} — Lifetime Learnings\n"
            f"Auto-generated by AlphaAbsolute Agent Memory Loop.\n"
            f"Updated weekly. Agents read this file to improve their calls.\n"
        )
        mem_file.write_text(existing + section, encoding="utf-8")
        written.append(agent_id)

    # Also write a master index
    _update_master_index(verification, today)
    return written


def _update_master_index(verification: dict, today: str):
    """Update master accuracy dashboard in memory/agent_accuracy.md"""
    idx_file = MEM_DIR / "agent_accuracy.md"

    lines = [f"\n\n## Accuracy Snapshot — {today}\n"]
    lines.append("| Agent | Metric | This Week |")
    lines.append("|---|---|---|")

    if "agent_01" in verification:
        d = verification["agent_01"]
        lines.append(f"| Agent 01 Macro | Regime correct | "
                     f"{'YES' if d.get('regime_correct') else 'NO'} "
                     f"(QQQ {d.get('qqq_return_1w',0):+.1f}%) |")
    if "agent_03" in verification:
        d = verification["agent_03"]
        lines.append(f"| Agent 03 Screener | Phase3 avg return | "
                     f"{d.get('avg_return',0):+.1f}% |")
    if "agent_05" in verification:
        d = verification["agent_05"]
        lines.append(f"| Agent 05 Thematic | Conviction edge | "
                     f"{d.get('conviction_edge',0):+.1f}%pp |")
    if "agent_09" in verification:
        d = verification["agent_09"]
        lines.append(f"| Agent 09 Strategist | Opp hit rate | "
                     f"{d.get('hit_rate',0):.0f}% |")
    if "agent_12" in verification:
        d = verification["agent_12"]
        lines.append(f"| Agent 12 Risk | Flag accuracy | "
                     f"{d.get('accuracy','N/A')}% |")
    if "agent_16" in verification:
        d = verification["agent_16"]
        lines.append(f"| Agent 16 Auditor | Fix trend | "
                     f"{d.get('trend','stable')} |")

    existing = idx_file.read_text(encoding="utf-8") if idx_file.exists() else (
        "# AlphaAbsolute Agent Accuracy Dashboard\n"
        "Auto-updated weekly. Shows how accurate each agent's calls are.\n"
    )
    idx_file.write_text(existing + "\n".join(lines), encoding="utf-8")


# ─── Main Entry Point ─────────────────────────────────────────────────────────
def run_agent_memory_loop(synthesis: dict, nrgc_assessments: dict,
                           auto_trade_result: dict, focus_result: dict,
                           portfolio: dict, audit_log: list) -> dict:
    """
    Full agent memory loop — called from weekly_runner.py.
    1. Verify last week's calls
    2. Generate lessons for each agent
    3. Write to memory files
    4. Save this week's calls for next week
    Returns summary dict for Telegram and logging.
    """
    print("  [AgentMemory] Verifying last week's agent calls...")
    verification = verify_agent_calls(portfolio)

    lessons_generated = {}
    written = []

    if verification:
        print(f"  Verified {len(verification)} agents")
        print("  Generating lessons via Groq/Gemini...")
        lessons_generated = generate_agent_lessons(verification)
        written = write_agent_memory(lessons_generated, verification)
        print(f"  Lessons written for: {written}")
    else:
        print("  No previous calls found — first week")

    print("  Saving this week's agent calls...")
    save_agent_calls(synthesis or {}, nrgc_assessments or {},
                     auto_trade_result or {}, focus_result or {},
                     portfolio or {}, audit_log or [])
    print(f"  Agent calls saved for {datetime.utcnow().strftime('%Y-W%V')}")

    return {
        "verified_agents": list(verification.keys()),
        "lessons_generated": len(lessons_generated),
        "agents_updated": written,
        "verification": verification,
    }
