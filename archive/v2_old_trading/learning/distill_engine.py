"""
AlphaAbsolute — Distillation Engine
Takes raw scraped items → extracts structured insights via LLM.

LLM Priority (auto-fallback):
  1. Google Gemini Flash (FREE — 1500 req/day, no credit card)
  2. Anthropic Claude Haiku (paid backup — ~$0.05/week)

Cost: $0/week with Gemini Flash free tier
"""
import json, os
from datetime import datetime
from pathlib import Path
from typing import Optional
import urllib3
urllib3.disable_warnings()

BASE_DIR     = Path(__file__).parent.parent.parent
RAW_DIR      = BASE_DIR / "data" / "raw"
INSIGHTS_DIR = BASE_DIR / "data" / "insights"
TODAY        = datetime.now().strftime("%y%m%d")
MONTH_DIR    = INSIGHTS_DIR / datetime.now().strftime("%Y-%m")
MONTH_DIR.mkdir(parents=True, exist_ok=True)

# Load .env — always override system env vars (fixes empty Windows env vars)
_env_path = BASE_DIR / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ[_k.strip()] = _v.strip()

# ─── LLM Clients: Groq → Gemini → Anthropic ─────────────────────────────────

_GROQ_KEY      = os.getenv("GROQ_API_KEY", "")
_GEMINI_KEY    = os.getenv("GEMINI_API_KEY", "")
_ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# 1. Groq (primary free — 14,400 req/day, 30 RPM, llama-3.3-70b)
groq_client = None
if _GROQ_KEY and _GROQ_KEY != "YOUR_GROQ_API_KEY_HERE":
    try:
        import httpx as _httpx
        from groq import Groq as _Groq
        groq_client = _Groq(
            api_key=_GROQ_KEY,
            http_client=_httpx.Client(verify=False),  # SSL bypass for corporate VPN
        )
        print("  [LLM] Groq Llama-3.3-70b ready (free — 14,400 req/day)")
    except Exception as _e:
        print(f"  [Groq init warning]: {_e}")

# 2. Gemini 2.5 Flash (secondary free — 20 req/day, use for overflow)
gemini_client = None
if _GEMINI_KEY and _GEMINI_KEY != "YOUR_GEMINI_API_KEY_HERE":
    try:
        from google import genai as _genai
        gemini_client = _genai.Client(api_key=_GEMINI_KEY)
        if not groq_client:
            print("  [LLM] Gemini 2.5 Flash ready (free — 20 req/day)")
    except Exception as _e:
        print(f"  [Gemini init warning]: {_e}")

# 3. Anthropic Haiku (paid backup)
client = None
if _ANTHROPIC_KEY and _ANTHROPIC_KEY != "YOUR_ANTHROPIC_API_KEY_HERE":
    try:
        import httpx, anthropic
        client = anthropic.Anthropic(
            api_key=_ANTHROPIC_KEY,
            http_client=httpx.Client(verify=False),
        )
        if not groq_client and not gemini_client:
            print("  [LLM] Anthropic Haiku ready (paid backup)")
    except Exception as _e:
        print(f"  [Anthropic init warning]: {_e}")

def _llm_call(prompt: str, max_tokens: int = 1500, call_type: str = "llm") -> Optional[str]:
    """Single LLM call. Priority: Groq (free) → Gemini (free) → Anthropic (paid)."""

    # 1. Groq — llama-3.3-70b, 14,400 req/day free
    if groq_client:
        try:
            resp = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.1,
            )
            _log_cost("groq-llama", 0, 0, call_type)
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"  [Groq error]: {e}")

    # 2. Gemini 2.5 Flash — 20 req/day free
    if gemini_client:
        try:
            from google.genai import types as _gtypes
            resp = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=_gtypes.GenerateContentConfig(
                    max_output_tokens=max_tokens + 500,
                    temperature=0.1,
                ),
            )
            text = None
            if resp.candidates and resp.candidates[0].content.parts:
                text = "".join(
                    p.text for p in resp.candidates[0].content.parts
                    if hasattr(p, "text") and p.text
                ).strip()
            if not text:
                raise ValueError("Empty response from Gemini")
            _log_cost("gemini-flash", 0, 0, call_type)
            return text
        except Exception as e:
            print(f"  [Gemini error]: {e}")

    # 3. Anthropic Haiku — paid backup
    if client:
        try:
            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )
            _log_cost("haiku", response.usage.input_tokens, response.usage.output_tokens, call_type)
            return response.content[0].text.strip()
        except Exception as e:
            print(f"  [Haiku error]: {e}")

    return None

# ─── Token Cost Tracker ────────────────────────────────────────────────────────
_COST_FILE = BASE_DIR / "data" / "state" / "token_cost_log.json"

def _log_cost(model: str, in_tokens: int, out_tokens: int, call_type: str):
    """Track cumulative API cost. Gemini Flash: free ($0). Haiku: $0.80/$4 per MTok."""
    rates = {
        "groq-llama":   {"in": 0.0, "out": 0.0},        # free tier
        "gemini-flash": {"in": 0.0, "out": 0.0},        # free tier
        "haiku":        {"in": 0.0000008, "out": 0.000004},
        "sonnet":       {"in": 0.000003,  "out": 0.000015},
    }
    if "groq" in model.lower():
        m = "groq-llama"
    elif "gemini" in model.lower():
        m = "gemini-flash"
    elif "haiku" in model.lower():
        m = "haiku"
    else:
        m = "sonnet"
    cost = in_tokens * rates[m]["in"] + out_tokens * rates[m]["out"]

    log = {}
    if _COST_FILE.exists():
        try: log = json.loads(_COST_FILE.read_text(encoding="utf-8"))
        except: pass
    log.setdefault("total_usd", 0)
    log.setdefault("calls", [])
    log["total_usd"] = round(log["total_usd"] + cost, 6)
    log["calls"].append({
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "type": call_type, "model": m,
        "in": in_tokens, "out": out_tokens,
        "cost_usd": round(cost, 6),
    })
    log["calls"] = log["calls"][-200:]  # keep last 200 calls
    _COST_FILE.parent.mkdir(parents=True, exist_ok=True)
    _COST_FILE.write_text(json.dumps(log, indent=2), encoding="utf-8")
    return cost

# ─── Extraction Schema ─────────────────────────────────────────────────────────
# Output is JSON — SHORT, structured, actionable. Not prose.

BATCH_EXTRACTION_PROMPT = """You are an investment research analyst for AlphaAbsolute.
Extract key investment signals from these {n} research items.
Return a JSON array. Each item = one object with this schema:
{{
  "item_idx": int,
  "ticker": "string or null",
  "signal_type": "earnings|insider|macro|theme|risk|institutional|congress",
  "headline": "string (max 12 words)",
  "key_metric": "string (most important number/data point, max 8 words)",
  "emls_impact": "positive|negative|neutral",
  "urgency": "immediate|this_week|watch|monitor",
  "themes": ["AI"|"Memory/HBM"|"Photonics"|"Space"|"Nuclear"|"NeoCloud"|"DefenseTech"|"Macro"],
  "actionable": true|false,
  "action_note": "string or null (max 10 words if actionable)",
  "nrgc_phase_signal": 1 or 2 or 3 or 4 or 5 or 6 or 7 or null,
  "nrgc_phase_reason": "string or null (why this implies that NRGC phase, max 10 words)"
}}

NRGC phase guide for tagging:
1=Neglect(revenue declining), 2=Accumulation(stabilizing/first positive signs),
3=Inflection(revenue accelerating QoQ, guidance raised, supply tight),
4=Recognition(beat-and-raise consecutive), 5=Consensus(decelerating/crowded),
6=Euphoria(parabolic/everyone bullish), 7=Distribution(guidance cut/miss)

Items:
{items_xml}

Return JSON array only. No explanation."""


SYNTHESIS_PROMPT = """You are the AlphaAbsolute Macro Strategist (Agent 09).
Synthesize this week's research signals into an investment thesis update.

Signals this week:
{signals_json}

Return JSON:
{{
  "regime_signal": "risk-on|risk-off|neutral",
  "top_themes": ["theme1","theme2","theme3"],
  "top_opportunities": [
    {{"ticker":"","thesis":"","emls_impact":"","urgency":""}}
  ],
  "key_risks": ["risk1","risk2"],
  "watchlist_adds": ["ticker1"],
  "watchlist_removes": ["ticker1"],
  "macro_notes": "string (2-3 sentences max)",
  "notebooklm_worthy": true|false,
  "notebooklm_summary": "string or null (if worth storing as permanent knowledge)"
}}"""


# ─── Batch Extraction (Haiku — cheap) ─────────────────────────────────────────

def extract_insights_batch(items: list[dict]) -> list[dict]:
    """Extract signals from up to 10 items in ONE Haiku call."""
    if not items:
        return []

    # Build compact XML representation (shorter than JSON for input)
    items_xml = ""
    for i, item in enumerate(items[:10]):
        items_xml += f"<item idx='{i}'>\n"
        items_xml += f"<src>{item.get('source_name','')}</src>\n"
        items_xml += f"<title>{item.get('title','')[:200]}</title>\n"
        items_xml += f"<text>{item.get('text','')[:600]}</text>\n"
        items_xml += "</item>\n"

    prompt = BATCH_EXTRACTION_PROMPT.format(n=len(items[:10]), items_xml=items_xml)

    if not gemini_client and not client:
        print(f"  [LLM skip]: no API client — add GEMINI_API_KEY (free) to .env")
        return []
    try:
        text = _llm_call(prompt, max_tokens=1500, call_type="extraction")
        if not text:
            return []
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        extracted = json.loads(text)
        results = []
        for ext in extracted:
            idx = ext.get("item_idx", 0)
            if idx < len(items):
                merged = {**items[idx], **ext}
                results.append(merged)
        return results
    except Exception as e:
        print(f"  [Extraction error]: {e}")
        return []


def run_extraction(raw_items: dict) -> list[dict]:
    """Process all raw items in batches of 10."""
    all_raw = []
    for source_items in raw_items.values():
        all_raw.extend(source_items)

    if not all_raw:
        return []

    print(f"  Extracting {len(all_raw)} items (batches of 10)...")
    all_insights = []
    for i in range(0, len(all_raw), 10):
        batch = all_raw[i:i+10]
        insights = extract_insights_batch(batch)
        all_insights.extend(insights)
        print(f"    Batch {i//10+1}: {len(insights)} insights extracted")

    # Save to insights store
    out = MONTH_DIR / f"{TODAY}_insights.json"
    out.write_text(json.dumps(all_insights, indent=2, ensure_ascii=False))
    print(f"  Saved: {out} ({len(all_insights)} insights)")
    return all_insights


# ─── Weekly Synthesis (Sonnet — 1 call/week) ──────────────────────────────────

def weekly_synthesis(insights: list[dict]) -> Optional[dict]:
    """Synthesize the week's insights into actionable themes. 1 Sonnet call."""
    if not insights:
        return None

    # Filter to actionable only for synthesis (reduces tokens)
    actionable = [i for i in insights if i.get("actionable") or i.get("urgency") in ("immediate","this_week")]
    # Cap at 50 items to stay in budget
    sample = actionable[:50] if len(actionable) > 50 else actionable

    # Minimal JSON for input (remove verbose fields)
    minimal = [{
        "src": i.get("source",""),
        "headline": i.get("headline",""),
        "ticker": i.get("ticker"),
        "signal": i.get("signal_type",""),
        "impact": i.get("emls_impact",""),
        "urgency": i.get("urgency",""),
        "themes": i.get("themes",[]),
        "action": i.get("action_note"),
    } for i in sample]

    try:
        text = _llm_call(SYNTHESIS_PROMPT.format(
            signals_json=json.dumps(minimal, ensure_ascii=False)
        ), max_tokens=1200, call_type="synthesis")
        if not text:
            return None
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        synthesis = json.loads(text)
        synthesis["generated"] = datetime.now().isoformat()
        synthesis["insight_count"] = len(insights)

        # Save synthesis
        out = MONTH_DIR / f"{TODAY}_weekly_synthesis.json"
        out.write_text(json.dumps(synthesis, indent=2, ensure_ascii=False))
        print(f"  Weekly synthesis saved: {out}")
        return synthesis
    except Exception as e:
        print(f"  [Synthesis error]: {e}")
        return None


# ─── Thematic Deep Dive (Sonnet — 1 call/theme/week) ──────────────────────────

THEME_DEEP_DIVE_PROMPT = """You are AlphaAbsolute Agent 05 (Thematic Research).
Write a concise thematic investment thesis for: {theme}

Use these signals as input:
{signals_json}

Return JSON:
{{
  "theme": "{theme}",
  "date": "{date}",
  "cycle_phase": "early|growth|mature|peak|declining",
  "emls_heatmap": {{
    "earnings_revision": "positive|neutral|negative",
    "rs_vs_market": "leading|inline|lagging",
    "news_flow": "positive|neutral|negative",
    "institutional_flow": "accumulating|neutral|distributing"
  }},
  "key_catalysts": ["catalyst1","catalyst2","catalyst3"],
  "key_risks": ["risk1","risk2"],
  "top_picks": [
    {{"ticker":"","reason":"","emls_score_est":0,"setup":"base0|base1|vcp|wyckoff"}}
  ],
  "avoid": ["ticker_avoid"],
  "time_horizon": "1-3m|3-6m|6-12m",
  "conviction": "high|medium|low",
  "thesis_summary": "string (3 sentences max)",
  "notebooklm_label": "string (label for NotebookLM storage)"
}}"""


def theme_deep_dive(theme: str, relevant_insights: list[dict]) -> Optional[dict]:
    """Generate a thematic deep dive for one theme. 1 Sonnet call."""
    minimal = [{
        "headline": i.get("headline",""),
        "ticker": i.get("ticker"),
        "signal": i.get("signal_type",""),
        "impact": i.get("emls_impact",""),
    } for i in relevant_insights[:20]]

    try:
        text = _llm_call(THEME_DEEP_DIVE_PROMPT.format(
            theme=theme, date=TODAY,
            signals_json=json.dumps(minimal, ensure_ascii=False)
        ), max_tokens=1000, call_type="deep_dive")
        if not text:
            return None
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        deep_dive = json.loads(text)

        # Save
        theme_slug = theme.lower().replace("/","_").replace(" ","_")
        out = MONTH_DIR / f"{TODAY}_theme_{theme_slug}.json"
        out.write_text(json.dumps(deep_dive, indent=2, ensure_ascii=False))
        print(f"  Theme deep dive saved: {out}")
        return deep_dive
    except Exception as e:
        print(f"  [Theme deep dive error] {theme}: {e}")
        return None


# ─── Load Recent Insights ─────────────────────────────────────────────────────

def load_insights(days: int = 7, theme_filter: Optional[str] = None) -> list[dict]:
    """Load insights from past N days."""
    insights = []
    cutoff = datetime.now().timestamp() - (days * 86400)
    for f in sorted(INSIGHTS_DIR.rglob("*_insights.json"), reverse=True)[:days]:
        try:
            items = json.loads(f.read_text())
            for item in items:
                if theme_filter and theme_filter not in item.get("themes",[]):
                    continue
                insights.append(item)
        except:
            pass
    return insights


if __name__ == "__main__":
    # Test with recent raw data
    import sys, glob
    raw_files = sorted(glob.glob(str(RAW_DIR / "*.json")), reverse=True)
    if not raw_files:
        print("No raw data found. Run research_scraper.py first.")
        sys.exit(1)

    print(f"Loading: {raw_files[0]}")
    raw = json.loads(Path(raw_files[0]).read_text())
    insights = run_extraction(raw)

    if len(insights) > 5:
        print("\nRunning weekly synthesis...")
        synthesis = weekly_synthesis(insights)
        if synthesis:
            print(f"  Regime: {synthesis.get('regime_signal')}")
            print(f"  Top themes: {synthesis.get('top_themes')}")
            print(f"  Opportunities: {len(synthesis.get('top_opportunities',[]))}")
