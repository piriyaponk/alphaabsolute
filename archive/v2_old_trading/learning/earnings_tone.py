"""
AlphaAbsolute — Earnings Call Tone Analysis
Systematic scoring of management communication quality.

Two-tier system:
  Tier 1: Keyword scoring (zero tokens, instant)
  Tier 2: LLM analysis via Groq (for high-conviction targets only)

NRGC Integration (critical for Phase 2→3 detection):
  Tone improving + guidance raised   → +5 Phase 2→3 signal (inflection confirmed)
  Positive guidance + strong outlook → +3 Phase 3 signal
  Tone shift negative + guidance cut → Phase 6/7 warning (−4)
  "Inflection" language detected     → +4 Phase 3 multiplier

Key insight: Management tone LEADS price by 1-2 quarters.
When CEO starts saying "acceleration" instead of "stabilization" →
NRGC Phase 2 is ending, Phase 3 is starting.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
import requests
import urllib3
urllib3.disable_warnings()

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR  = BASE_DIR / "data" / "agent_memory"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    print(f"  [EarningsTone] {msg}")


# ═══════════════════════════════════════════════════════════════════════════════
# KEYWORD DICTIONARIES — Tier 1 (zero tokens)
# ═══════════════════════════════════════════════════════════════════════════════

# Positive language → management confidence
POSITIVE_WORDS = [
    "acceleration", "accelerating", "accelerate",
    "momentum", "strong demand", "robust demand", "record",
    "outperform", "beat", "exceed", "ahead of", "above expectations",
    "expansion", "expanding", "growing faster", "inflection",
    "significant growth", "strong pipeline", "increasing",
    "raise guidance", "raised guidance", "increase guidance",
    "upside", "stronger than expected", "impressive",
    "breakthrough", "transformational", "structural growth",
    "backlog growing", "design wins", "market share gains",
    "pricing power", "margin expansion", "operating leverage",
]

# Negative language → caution signals
NEGATIVE_WORDS = [
    "headwind", "headwinds", "softness", "soft demand",
    "challenging", "difficult", "cautious", "uncertainty",
    "slower than expected", "below expectations", "disappointing",
    "miss", "shortfall", "weakness", "deceleration", "decelerating",
    "inventory correction", "inventory digestion",
    "push out", "delayed", "postponed", "canceled",
    "reducing guidance", "lower guidance", "cut guidance",
    "macro pressure", "pricing pressure", "margin pressure",
    "oversupply", "excess inventory", "rightsizing",
    "restructuring", "cost reduction", "headcount reduction",
]

# "Inflection" vocabulary → strongest NRGC Phase 2→3 signal
INFLECTION_WORDS = [
    "inflection", "inflection point", "turning point",
    "beginning of", "early stages", "early innings",
    "start of a new cycle", "new cycle", "new upcycle",
    "demand is turning", "demand has turned",
    "order rates accelerating", "book-to-bill improving",
    "customers are coming back", "inventory correction ending",
    "bottom is in", "recovery beginning",
    "first in a series", "first of many",
    "AI-driven demand", "structural demand shift",
    "generational opportunity", "decades-long opportunity",
    "secular growth", "secular tailwind",
]

# Guidance direction markers
GUIDANCE_RAISE = [
    "raise", "raised", "increase", "increased", "raised guidance",
    "above the high end", "above our prior", "upside to prior",
    "now expect higher", "now forecasting higher",
]
GUIDANCE_LOWER = [
    "lower", "reduce", "reduced", "below the low end",
    "below our prior", "revised lower", "now expect lower",
    "now forecasting lower", "taking down guidance",
]
GUIDANCE_MAINTAIN = [
    "reiterate", "reiterating", "maintain", "maintaining",
    "in line with", "consistent with prior", "unchanged",
]

# Confidence markers (how sure does management sound?)
HIGH_CONFIDENCE = [
    "very confident", "highly confident", "we are certain",
    "clear visibility", "strong visibility", "confident in",
    "we expect", "we will", "we are on track",
]
LOW_CONFIDENCE = [
    "difficult to predict", "limited visibility", "uncertain",
    "we cannot guarantee", "subject to change", "if conditions",
    "assuming", "we believe but cannot", "it's early",
]


# ═══════════════════════════════════════════════════════════════════════════════
# TIER 1: KEYWORD-BASED SCORING (zero tokens)
# ═══════════════════════════════════════════════════════════════════════════════

def score_tone_keywords(text: str) -> dict:
    """
    Score transcript using keyword counting.
    Returns comprehensive tone dict with NRGC signal.
    """
    if not text or len(text) < 100:
        return {"tone_score": 0, "nrgc_signal": "insufficient_data", "nrgc_boost": 0}

    text_lower = text.lower()

    # Count matches
    pos_count      = sum(1 for w in POSITIVE_WORDS if w in text_lower)
    neg_count      = sum(1 for w in NEGATIVE_WORDS if w in text_lower)
    inflect_count  = sum(1 for w in INFLECTION_WORDS if w in text_lower)
    g_raise        = any(w in text_lower for w in GUIDANCE_RAISE)
    g_lower        = any(w in text_lower for w in GUIDANCE_LOWER)
    g_maintain     = any(w in text_lower for w in GUIDANCE_MAINTAIN)
    hi_conf        = sum(1 for w in HIGH_CONFIDENCE if w in text_lower)
    lo_conf        = sum(1 for w in LOW_CONFIDENCE  if w in text_lower)

    # Tone score: −100 to +100
    raw_score = (pos_count * 3) - (neg_count * 4) + (inflect_count * 5)
    tone_score = max(-100, min(100, raw_score))

    # Guidance direction
    guidance = (
        "raise"    if g_raise and not g_lower else
        "lower"    if g_lower and not g_raise else
        "maintain" if g_maintain else
        "mixed"    if g_raise and g_lower else "unclear"
    )

    # Confidence level
    confidence = (
        "high"   if hi_conf > lo_conf else
        "low"    if lo_conf > hi_conf else "medium"
    )

    # NRGC signal mapping
    nrgc_boost = 0
    nrgc_signal = "neutral"

    if inflect_count >= 3 and tone_score > 20:
        nrgc_signal = "phase_2_to_3_inflection"
        nrgc_boost  = 5
    elif tone_score > 30 and guidance == "raise":
        nrgc_signal = "strong_positive_guidance_raise"
        nrgc_boost  = 5
    elif tone_score > 20 and guidance in ("raise", "maintain"):
        nrgc_signal = "positive_tone"
        nrgc_boost  = 3
    elif inflect_count >= 2:
        nrgc_signal = "inflection_language_detected"
        nrgc_boost  = 4
    elif tone_score > 0 and guidance == "raise":
        nrgc_signal = "mild_positive_guidance_raise"
        nrgc_boost  = 2
    elif tone_score < -30 or guidance == "lower":
        nrgc_signal = "phase_6_7_warning"
        nrgc_boost  = -4
    elif tone_score < -10:
        nrgc_signal = "negative_tone"
        nrgc_boost  = -2

    # Top positive + negative quotes (first occurrence for context)
    pos_quotes = []
    for w in INFLECTION_WORDS + POSITIVE_WORDS[:5]:
        idx = text_lower.find(w)
        if idx >= 0:
            snippet = text[max(0, idx-30):idx+len(w)+60].replace("\n", " ").strip()
            pos_quotes.append(snippet[:120])
            if len(pos_quotes) >= 3:
                break

    neg_quotes = []
    for w in NEGATIVE_WORDS[:8]:
        idx = text_lower.find(w)
        if idx >= 0:
            snippet = text[max(0, idx-30):idx+len(w)+60].replace("\n", " ").strip()
            neg_quotes.append(snippet[:120])
            if len(neg_quotes) >= 2:
                break

    return {
        "tone_score":      tone_score,
        "pos_count":       pos_count,
        "neg_count":       neg_count,
        "inflect_count":   inflect_count,
        "guidance":        guidance,
        "confidence":      confidence,
        "hi_conf_count":   hi_conf,
        "lo_conf_count":   lo_conf,
        "nrgc_signal":     nrgc_signal,
        "nrgc_boost":      nrgc_boost,
        "top_pos_quotes":  pos_quotes,
        "top_neg_quotes":  neg_quotes,
        "inflect_words":   [w for w in INFLECTION_WORDS if w in text_lower][:5],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TIER 2: LLM-ENHANCED TONE ANALYSIS (Groq — for key targets only)
# ═══════════════════════════════════════════════════════════════════════════════

TONE_LLM_PROMPT = """Analyze this earnings call transcript excerpt for NRGC phase detection.

TRANSCRIPT:
{transcript}

Provide analysis in JSON format:
{{
  "management_sentiment": "very_bullish|bullish|neutral|cautious|bearish",
  "guidance_direction": "raised|maintained|lowered|mixed",
  "key_theme": "single most important management message in 10 words",
  "inflection_detected": true|false,
  "inflection_evidence": "quote showing inflection language if present, else null",
  "phase_signal": "phase_2_to_3|phase_3|phase_4|phase_5|phase_6|neutral",
  "confidence_in_signal": 1-5,
  "top_bullish_quote": "best positive quote from transcript, max 100 chars",
  "top_bearish_quote": "most concerning quote if any, max 100 chars",
  "nrgc_boost": number from -5 to +6 based on phase signal,
  "one_line_summary": "20 word summary of management tone"
}}

Return ONLY valid JSON, no other text."""


def _call_llm(prompt: str, max_tokens: int = 600) -> str:
    groq_key   = os.environ.get("GROQ_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    s = requests.Session(); s.verify = False

    if groq_key:
        try:
            r = s.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}",
                         "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": max_tokens,
                      "temperature": 0.1},
                timeout=35, verify=False,
            )
            return r.json()["choices"][0]["message"]["content"]
        except Exception:
            pass

    if gemini_key:
        try:
            r = s.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.0-flash:generateContent?key={gemini_key}",
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.1}},
                timeout=35, verify=False,
            )
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            pass
    return ""


def analyze_tone_llm(transcript: str, ticker: str) -> dict:
    """
    Tier 2 LLM tone analysis. Use only for high-EMLS candidates.
    Costs ~1 Groq call (~0 cents on free tier).
    """
    # Take most important parts: opening statement + Q&A (first 3000 chars)
    excerpt = transcript[:3000]
    prompt  = TONE_LLM_PROMPT.format(transcript=excerpt[:2000])

    raw = _call_llm(prompt)
    if not raw:
        return {}

    # Parse JSON from LLM response
    try:
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception:
        pass

    return {}


# ═══════════════════════════════════════════════════════════════════════════════
# QoQ TONE COMPARISON — detect tone SHIFT (most important for NRGC)
# ═══════════════════════════════════════════════════════════════════════════════

def compare_tone_shift(current_score: dict, prior_score: dict) -> dict:
    """
    Compare current quarter tone vs prior quarter.
    A TONE SHIFT is more informative than absolute tone level.
    Positive shift = management getting more confident = Phase 2→3 signal.
    """
    if not current_score or not prior_score:
        return {"shift": "unknown", "nrgc_boost": 0}

    curr_tone = current_score.get("tone_score", 0)
    prev_tone = prior_score.get("tone_score", 0)
    delta     = curr_tone - prev_tone

    curr_inflect = current_score.get("inflect_count", 0)
    prev_inflect = prior_score.get("inflect_count", 0)
    inflect_delta = curr_inflect - prev_inflect

    curr_guid = current_score.get("guidance", "unclear")
    prev_guid = prior_score.get("guidance", "unclear")

    # Determine shift quality
    if delta > 25 and curr_inflect > prev_inflect:
        shift      = "strong_positive_shift"
        nrgc_boost = 5
    elif delta > 15:
        shift      = "positive_shift"
        nrgc_boost = 3
    elif delta > 5:
        shift      = "mild_positive"
        nrgc_boost = 1
    elif delta < -20:
        shift      = "deteriorating"
        nrgc_boost = -4
    elif delta < -10:
        shift      = "mild_deterioration"
        nrgc_boost = -2
    else:
        shift      = "stable"
        nrgc_boost = 0

    # Extra boost for guidance improving
    if curr_guid == "raise" and prev_guid in ("maintain", "lower", "unclear"):
        nrgc_boost += 2

    return {
        "tone_delta":        delta,
        "inflect_delta":     inflect_delta,
        "shift":             shift,
        "guidance_improved": (curr_guid == "raise" and prev_guid != "raise"),
        "nrgc_boost":        nrgc_boost,
        "summary":           (
            f"Tone shifted +{delta} points, {inflect_delta:+d} inflection words. "
            f"Guidance: {prev_guid} → {curr_guid}."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER RUNNER — analyze a list of transcripts
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_earnings_batch(transcripts: dict, use_llm: bool = False) -> dict:
    """
    Analyze tone for multiple tickers.
    transcripts = {ticker: {"current": text, "prior": text (optional)}}
    Returns {ticker: tone_analysis} with NRGC boosts.
    """
    results     = {}
    top_signals = []

    for ticker, data in transcripts.items():
        current_text = data.get("current", "")
        prior_text   = data.get("prior", "")

        if not current_text:
            continue

        log(f"  Analyzing {ticker}...")
        current_score = score_tone_keywords(current_text)

        # Optional: LLM deep analysis for high-importance tickers
        llm_result = {}
        if use_llm and current_score.get("tone_score", 0) > 20:
            llm_result = analyze_tone_llm(current_text, ticker)
            if llm_result:
                # LLM overrides keyword boost if confident
                llm_boost = llm_result.get("nrgc_boost", 0)
                if llm_boost != 0:
                    current_score["nrgc_boost"] = llm_boost
                    current_score["nrgc_signal"] = llm_result.get("phase_signal", current_score["nrgc_signal"])
                current_score["llm"] = llm_result

        # QoQ comparison
        shift = {}
        if prior_text:
            prior_score = score_tone_keywords(prior_text)
            shift       = compare_tone_shift(current_score, prior_score)
            # Combined boost: keyword + shift
            total_boost = current_score.get("nrgc_boost", 0) + shift.get("nrgc_boost", 0)
            current_score["tone_shift"]        = shift
            current_score["combined_nrgc_boost"] = total_boost
        else:
            current_score["combined_nrgc_boost"] = current_score.get("nrgc_boost", 0)

        results[ticker] = current_score

        if current_score["nrgc_signal"] in ("phase_2_to_3_inflection",
                                             "inflection_language_detected",
                                             "strong_positive_guidance_raise"):
            top_signals.append((ticker, current_score["nrgc_signal"],
                                 current_score["combined_nrgc_boost"]))

        log(f"    {ticker}: tone={current_score['tone_score']:+d} "
            f"guidance={current_score['guidance']} "
            f"signal={current_score['nrgc_signal']} "
            f"boost={current_score['combined_nrgc_boost']:+d}")

    # Save
    today   = datetime.utcnow().strftime("%Y-%m-%d")
    out_file = DATA_DIR / f"earnings_tone_{today[:7]}.json"
    out_file.write_text(
        json.dumps({"date": today, "results": results, "top_signals": top_signals},
                   indent=2, default=str),
        encoding="utf-8"
    )

    log(f"Tone analysis complete: {len(results)} tickers, "
        f"{len(top_signals)} phase-change signals")
    return results


def get_tone_nrgc_boost(tone_results: dict, ticker: str) -> int:
    """Quick lookup: get NRGC boost for a ticker from tone results."""
    return tone_results.get(ticker, {}).get("combined_nrgc_boost", 0)
