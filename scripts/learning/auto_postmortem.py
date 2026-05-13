"""
AlphaAbsolute — Auto Post-Mortem Generator (Agent 13 automation)
Triggered when a paper position closes.
One Haiku call per trade → structured lesson → NotebookLM Investment Lessons.

Pattern detection: 3+ similar failures → trigger framework rule review.
"""
import json, os
from datetime import datetime
from pathlib import Path
from typing import Optional
import anthropic

BASE_DIR   = Path(__file__).parent.parent.parent
LESSONS_FILE = BASE_DIR / "data" / "insights" / "investment_lessons.json"
PATTERN_FILE = BASE_DIR / "data" / "insights" / "failure_patterns.json"

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

POSTMORTEM_PROMPT = """Investment post-mortem for AlphaAbsolute PULSE framework.
Trade closed:
- Ticker: {ticker}
- Setup: {setup_type} | EMLS score: {emls_score}
- Entry: ${entry:.2f} | Exit: ${exit:.2f} | P&L: {pnl_pct:+.1f}% ({pnl_usd:+,.0f} USD)
- Days held: {days}
- Exit reason: {reason}
- Thesis: {thesis}
- Market regime at entry: {regime}

PULSE checks at entry that PASSED: {checks_passed}
PULSE checks at entry that FAILED: {checks_failed}

Return JSON only:
{{
  "worked": boolean,
  "outcome": "big_win|win|scratch|loss|big_loss",
  "primary_success_factor": "string or null",
  "primary_failure_reason": "string or null",
  "emls_signals_at_entry": integer (estimated 0-11),
  "framework_rule_violated": "string or null",
  "lesson": "string (one actionable sentence)",
  "lesson_category": "entry_timing|position_size|stop_loss|exit|setup_quality|regime|other",
  "rule_change_proposed": "string or null (specific rule to add/modify)",
  "similar_past_pattern": "string or null"
}}"""


def generate_postmortem(closed_trade: dict, regime: str = "neutral") -> Optional[dict]:
    """Generate structured post-mortem for a closed trade. ~$0.003/trade."""
    entry   = closed_trade["entry_price"]
    exit_p  = closed_trade.get("exit_price", entry)
    pnl_pct = closed_trade.get("pnl_pct", 0)
    pnl_usd = closed_trade.get("pnl_usd", 0)

    checks = closed_trade.get("checks", {})
    passed = [k for k,v in checks.items() if v]
    failed = [k for k,v in checks.items() if not v]

    prompt = POSTMORTEM_PROMPT.format(
        ticker      = closed_trade["ticker"],
        setup_type  = closed_trade.get("setup_type","leader"),
        emls_score  = closed_trade.get("emls_score",70),
        entry       = entry,
        exit        = exit_p,
        pnl_pct     = pnl_pct,
        pnl_usd     = pnl_usd,
        days        = closed_trade.get("days_held", 0),
        reason      = closed_trade.get("reason",""),
        thesis      = closed_trade.get("thesis","")[:200],
        regime      = regime,
        checks_passed = ", ".join(passed) or "none",
        checks_failed = ", ".join(failed) or "none",
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=400,
            messages=[{"role":"user","content":prompt}]
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"): text = text[4:]
        pm = json.loads(text)
        pm["ticker"]   = closed_trade["ticker"]
        pm["date"]     = datetime.now().strftime("%Y-%m-%d")
        pm["pnl_pct"]  = pnl_pct
        pm["pnl_usd"]  = pnl_usd
        pm["days_held"]= closed_trade.get("days_held",0)
        return pm
    except Exception as e:
        print(f"  [Postmortem error]: {e}")
        return None


def save_lesson(pm: dict):
    """Append lesson to investment_lessons.json."""
    LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lessons = []
    if LESSONS_FILE.exists():
        try: lessons = json.loads(LESSONS_FILE.read_text())
        except: pass
    lessons.append(pm)
    LESSONS_FILE.write_text(json.dumps(lessons, indent=2))


def check_failure_patterns() -> list[dict]:
    """Detect recurring failure patterns (3+ similar = flag for rule change)."""
    if not LESSONS_FILE.exists():
        return []
    try:
        lessons = json.loads(LESSONS_FILE.read_text())
    except:
        return []

    # Group by failure reason
    failures = [l for l in lessons if not l.get("worked",True)]
    from collections import Counter
    reason_counts = Counter(l.get("lesson_category","other") for l in failures)
    rule_proposed = Counter(l.get("rule_change_proposed") for l in failures
                           if l.get("rule_change_proposed"))

    patterns = []
    for category, count in reason_counts.items():
        if count >= 3:
            patterns.append({
                "category": category,
                "occurrences": count,
                "message": f"PATTERN DETECTED: {count} failures in '{category}'",
                "action": "REVIEW FRAMEWORK RULE",
                "rule_proposals": [r for r,c in rule_proposed.most_common(3) if r],
            })

    if patterns:
        PATTERN_FILE.write_text(json.dumps(patterns, indent=2))
        print(f"\n[ALERT] {len(patterns)} failure patterns detected — framework review needed")
        for p in patterns:
            print(f"  {p['message']}")
            if p["rule_proposals"]:
                print(f"  Proposed: {p['rule_proposals'][0]}")
    return patterns


def process_new_closed_trades(portfolio: dict, regime: str = "neutral"):
    """Process all newly closed trades that don't have post-mortems yet."""
    if not LESSONS_FILE.exists():
        processed = set()
    else:
        try:
            existing = json.loads(LESSONS_FILE.read_text())
            processed = {f"{l['ticker']}_{l['date']}" for l in existing}
        except:
            processed = set()

    new_pms = 0
    for trade in portfolio.get("closed", []):
        key = f"{trade['ticker']}_{trade.get('close_date','')}"
        if key in processed:
            continue

        print(f"  Generating post-mortem: {trade['ticker']} ({trade.get('pnl_pct',0):+.1f}%)")
        pm = generate_postmortem(trade, regime)
        if pm:
            save_lesson(pm)
            processed.add(key)
            new_pms += 1
            # Print lesson
            print(f"    Lesson: {pm.get('lesson','')}")
            if pm.get("rule_change_proposed"):
                print(f"    Proposed rule change: {pm['rule_change_proposed']}")

    if new_pms > 0:
        print(f"\n  {new_pms} post-mortems generated")
        check_failure_patterns()
    return new_pms


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(BASE_DIR / "scripts" / "paper_trading"))
    from portfolio_engine import load_portfolio
    portfolio = load_portfolio()
    print("Running post-mortem on closed trades...")
    n = process_new_closed_trades(portfolio)
    print(f"Processed {n} trades")
