"""
AlphaAbsolute — Narrative Keyword Frequency Tracker
Tracks how fast investment themes are being talked about in the media.

Core insight: NARRATIVE PRECEDES PRICE.
When keywords about a theme accelerate in news coverage by 2-3x,
institutional positioning is usually 4-8 weeks behind.
Catching this = catching NRGC Phase 2→3 early.

How it works:
  1. Reads scraped news from data/raw/ (already collected by research_scraper.py)
  2. Counts keyword occurrences per theme per week
  3. Compares to prior week → narrative acceleration %
  4. Maps acceleration level to NRGC phase signal
  5. Stores running history for trend analysis

Zero tokens. Zero cost. Pure pattern recognition.
"""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import urllib3
urllib3.disable_warnings()

BASE_DIR  = Path(__file__).resolve().parents[2]
DATA_DIR  = BASE_DIR / "data" / "agent_memory"
RAW_DIR   = BASE_DIR / "data" / "raw"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRACKER_FILE = DATA_DIR / "narrative_tracking.json"


def log(msg: str):
    print(f"  [NarrativeTracker] {msg}")


# ═══════════════════════════════════════════════════════════════════════════════
# THEME KEYWORD DICTIONARIES — 14 official themes + cross-theme signals
# ═══════════════════════════════════════════════════════════════════════════════

THEME_KEYWORDS = {
    "AI-Related": [
        "artificial intelligence", "AI agent", "agentic AI", "LLM", "large language model",
        "ChatGPT", "Claude AI", "Gemini", "foundation model", "AI inference",
        "AI workload", "AI demand", "AI compute", "AI spend",
    ],
    "Memory/HBM": [
        "HBM", "high bandwidth memory", "DRAM", "NAND", "memory pricing",
        "HBM2E", "HBM3", "HBM3E", "memory cycle", "DRAM pricing",
        "memory demand", "memory upcycle", "Micron", "SK Hynix",
    ],
    "Space": [
        "SpaceX", "Starship", "satellite", "rocket launch", "space economy",
        "LEO", "low earth orbit", "space station", "lunar", "RKLB", "Rocket Lab",
        "AST SpaceMobile", "ASTS",
    ],
    "Quantum": [
        "quantum computing", "qubit", "quantum advantage", "quantum error",
        "quantum hardware", "IonQ", "IBM quantum", "Google quantum",
        "quantum supremacy", "quantum application",
    ],
    "Photonics": [
        "silicon photonics", "optical interconnect", "datacom", "coherent",
        "LiDAR", "photonic chip", "optical networking", "co-packaged optics",
        "CPO", "transceiver", "LITE", "COHR", "photonics",
    ],
    "DefenseTech": [
        "defense AI", "autonomous weapon", "drone defense", "military AI",
        "JADC2", "Palantir", "defense tech", "AI surveillance",
        "defense contract", "DOD", "Pentagon AI",
    ],
    "Data Center": [
        "data center", "hyperscaler", "colocation", "AI data center",
        "data center power", "data center demand", "data center capacity",
        "GPU cluster", "compute cluster", "NVIDIA data center",
    ],
    "Nuclear/SMR": [
        "small modular reactor", "SMR", "nuclear power", "nuclear energy",
        "uranium", "reactor", "NuScale", "Oklo", "nuclear renaissance",
        "data center nuclear", "clean energy nuclear",
    ],
    "NeoCloud": [
        "CoreWeave", "CRWV", "AI cloud", "GPU cloud", "GPU-as-a-service",
        "hyperscaler alternative", "SMCI", "SuperMicro", "AI-first cloud",
    ],
    "AI Infrastructure": [
        "AI infrastructure", "cooling", "liquid cooling", "power density",
        "AI server", "VRT", "Vertiv", "Eaton", "power management",
        "UPS", "thermal management",
    ],
    "Data Center Infra": [
        "data center construction", "electrical infrastructure", "PWR",
        "Quanta Services", "EME", "electrical contractor",
        "data center developer", "power grid", "substation",
    ],
    "Drone/UAV": [
        "drone", "UAV", "unmanned aerial", "eVTOL", "ACHR", "Archer Aviation",
        "Joby", "drone delivery", "RCAT", "drone warfare", "commercial drone",
    ],
    "Robotics": [
        "humanoid robot", "Optimus", "Figure AI", "Boston Dynamics",
        "industrial robot", "automation", "robot", "ISRG", "TER",
        "robotic arm", "robotic surgery",
    ],
    "Connectivity": [
        "6G", "Starlink", "satellite internet", "TMUS", "T-Mobile",
        "ASTS", "AST SpaceMobile", "connectivity", "spectrum",
        "direct-to-cell", "satellite broadband",
    ],
}

# Cross-theme "super narratives" — when multiple themes cited together
SUPER_NARRATIVES = {
    "AI_Infrastructure_Supercycle": [
        "AI infrastructure", "data center build-out", "power demand",
        "capex cycle", "infrastructure investment",
    ],
    "Memory_Structural_Shift": [
        "HBM", "structural demand", "AI memory", "generative AI memory",
        "DRAM structural",
    ],
    "Nuclear_AI_Nexus": [
        "nuclear data center", "SMR power", "AI power demand",
        "clean power AI",
    ],
    "Defense_AI_Convergence": [
        "autonomous defense", "AI weapon", "defense AI contract",
        "military AI",
    ],
}

# NRGC signal thresholds
NARRATIVE_ACCEL_BREAKOUT = 100    # % WoW → narrative explosion (Phase 2→3)
NARRATIVE_ACCEL_HIGH     = 50     # % WoW → strong acceleration (Phase 2)
NARRATIVE_ACCEL_MED      = 25     # % WoW → building
NARRATIVE_DECEL_WARN     = -30    # % WoW → fading interest (Phase 5→6 warning)


# ═══════════════════════════════════════════════════════════════════════════════
# READING AND COUNTING NEWS DATA
# ═══════════════════════════════════════════════════════════════════════════════

def _collect_news_text(days_back: int = 7) -> str:
    """
    Collect all news text from last N days from data/raw/ folder.
    research_scraper.py saves files there.
    """
    cutoff = datetime.utcnow() - timedelta(days=days_back)
    all_text = []

    # Read from raw JSON files (research_scraper output)
    if RAW_DIR.exists():
        for f in RAW_DIR.glob("*.json"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime >= cutoff:
                    data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(data, list):
                        for item in data:
                            text = (item.get("title", "") + " " +
                                    item.get("summary", "") + " " +
                                    item.get("content", ""))
                            all_text.append(text)
                    elif isinstance(data, dict):
                        for key, items in data.items():
                            if isinstance(items, list):
                                for item in items:
                                    if isinstance(item, dict):
                                        text = (item.get("title", "") + " " +
                                                item.get("summary", "") + " " +
                                                item.get("content", ""))
                                        all_text.append(text)
            except Exception:
                continue

    # Also read from output/ folder (daily/weekly briefs = already processed content)
    output_dir = BASE_DIR / "output"
    if output_dir.exists():
        for f in output_dir.glob("*.md"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime >= cutoff:
                    all_text.append(f.read_text(encoding="utf-8", errors="replace")[:5000])
            except Exception:
                continue

    return " ".join(all_text).lower()


def count_theme_mentions(text: str, theme: str) -> int:
    """Count keyword occurrences for a theme in text."""
    if not text:
        return 0
    keywords = THEME_KEYWORDS.get(theme, [])
    count = 0
    for kw in keywords:
        count += len(re.findall(re.escape(kw.lower()), text))
    return count


# ═══════════════════════════════════════════════════════════════════════════════
# TRACKING AND COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════

def load_tracker() -> dict:
    """Load historical narrative tracking data."""
    if TRACKER_FILE.exists():
        try:
            return json.loads(TRACKER_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"history": [], "themes": {}}


def save_tracker(data: dict):
    TRACKER_FILE.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def compute_narrative_signals(current: dict, prior: dict) -> dict:
    """
    Compare current week vs prior week theme mention counts.
    Returns narrative acceleration + NRGC signals per theme.
    """
    signals = {}
    for theme in THEME_KEYWORDS:
        curr_count  = current.get(theme, 0)
        prior_count = prior.get(theme, 0)

        # Acceleration
        if prior_count == 0:
            accel = 100 if curr_count > 0 else 0
        else:
            accel = (curr_count - prior_count) / prior_count * 100

        # NRGC signal
        if accel >= NARRATIVE_ACCEL_BREAKOUT and curr_count >= 5:
            nrgc_signal = "narrative_explosion"
            nrgc_boost  = 6
        elif accel >= NARRATIVE_ACCEL_HIGH and curr_count >= 3:
            nrgc_signal = "narrative_inflection"
            nrgc_boost  = 4
        elif accel >= NARRATIVE_ACCEL_MED and curr_count >= 2:
            nrgc_signal = "narrative_accelerating"
            nrgc_boost  = 2
        elif accel <= NARRATIVE_DECEL_WARN and prior_count >= 5:
            nrgc_signal = "narrative_fading"
            nrgc_boost  = -3
        else:
            nrgc_signal = "neutral"
            nrgc_boost  = 0

        signals[theme] = {
            "current_mentions":  curr_count,
            "prior_mentions":    prior_count,
            "acceleration_pct":  round(accel, 1),
            "nrgc_signal":       nrgc_signal,
            "nrgc_boost":        nrgc_boost,
        }

    return signals


def detect_super_narratives(text: str) -> dict:
    """Detect cross-theme super-narrative patterns."""
    found = {}
    for name, keywords in SUPER_NARRATIVES.items():
        count = sum(1 for kw in keywords if kw.lower() in text)
        if count >= 2:
            found[name] = {
                "keyword_hits": count,
                "signal":       "super_narrative_active" if count >= 3 else "building",
                "nrgc_boost":   3 if count >= 3 else 1,
            }
    return found


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_narrative_tracker() -> dict:
    """
    Weekly narrative tracking run.
    Returns: {theme_signals, super_narratives, top_accelerating, top_fading, nrgc_boosts}
    """
    log("=== Narrative Tracker ===")
    tracker = load_tracker()
    today   = datetime.utcnow().strftime("%Y-%m-%d")
    week_id = datetime.utcnow().strftime("%Y-W%W")

    # Collect text from this week
    log("Collecting news text from last 7 days...")
    current_text = _collect_news_text(days_back=7)
    log(f"  Text length: {len(current_text):,} chars")

    # Count mentions per theme
    current_counts = {}
    for theme in THEME_KEYWORDS:
        current_counts[theme] = count_theme_mentions(current_text, theme)

    log(f"  Top themes by mention: " +
        ", ".join(f"{t}:{c}" for t, c in
                  sorted(current_counts.items(), key=lambda x: -x[1])[:5]))

    # Get prior week counts
    history = tracker.get("history", [])
    prior_counts = history[-1].get("counts", {}) if history else {}

    # Compute signals
    theme_signals    = compute_narrative_signals(current_counts, prior_counts)
    super_narratives = detect_super_narratives(current_text)

    # Build NRGC boost lookup (theme-level)
    nrgc_boosts = {}
    for theme, sig in theme_signals.items():
        if sig["nrgc_boost"] != 0:
            nrgc_boosts[theme] = sig["nrgc_boost"]
    for sn, data in super_narratives.items():
        log(f"  Super-narrative: {sn} (boost +{data['nrgc_boost']})")

    # Top accelerating / fading themes
    sorted_themes = sorted(theme_signals.items(),
                           key=lambda x: x[1]["acceleration_pct"], reverse=True)
    top_accel = [(t, d["acceleration_pct"], d["current_mentions"])
                 for t, d in sorted_themes if d["acceleration_pct"] > 0][:5]
    top_fading = [(t, d["acceleration_pct"], d["current_mentions"])
                  for t, d in sorted_themes if d["acceleration_pct"] < 0][-3:]

    log(f"  Accelerating: {[(t, f'{a:+.0f}%') for t, a, _ in top_accel[:3]]}")
    if top_fading:
        log(f"  Fading: {[(t, f'{a:+.0f}%') for t, a, _ in top_fading[:3]]}")

    # Update tracker history (keep 52 weeks)
    history.append({
        "week":        week_id,
        "date":        today,
        "counts":      current_counts,
        "top_accel":   [(t, a) for t, a, _ in top_accel[:3]],
        "super_narratives": list(super_narratives.keys()),
    })
    tracker["history"] = history[-52:]
    tracker["latest"]  = {
        "date":          today,
        "theme_signals": theme_signals,
        "super_narratives": super_narratives,
        "nrgc_boosts":   nrgc_boosts,
        "top_accel":     top_accel,
        "top_fading":    top_fading,
    }
    save_tracker(tracker)

    result = {
        "date":            today,
        "theme_signals":   theme_signals,
        "super_narratives": super_narratives,
        "nrgc_boosts":     nrgc_boosts,
        "top_accelerating": top_accel,
        "top_fading":      top_fading,
    }
    log(f"Done: {len(nrgc_boosts)} themes with NRGC signals, "
        f"{len(super_narratives)} super-narratives")
    return result


def get_narrative_nrgc_boost(narrative_result: dict, theme: str) -> int:
    """Quick lookup for weekly_runner NRGC enrichment."""
    return narrative_result.get("nrgc_boosts", {}).get(theme, 0)


def get_narrative_telegram_line(result: dict) -> str:
    """One-liner for Telegram weekly report."""
    if not result:
        return ""
    top = result.get("top_accelerating", [])
    super_n = list(result.get("super_narratives", {}).keys())
    top_str = ", ".join(f"{t}({a:+.0f}%)" for t, a, _ in top[:3]) if top else "—"
    sn_str  = ", ".join(super_n[:2]) if super_n else "none"
    return f"Narrative: Top themes {top_str} | Super-narratives: {sn_str}"
