"""
AlphaAbsolute v2 — A05 Theme Mapper (Monthly)
==============================================
Auto-classifies tickers into the 14 official themes using:

  Tier 2 — Keyword match on company name + description (Polygon free API)
  Tier 3 — SIC code heuristic (from same Polygon call — no extra cost)

Manual entries in ticker_labels.json always override both tiers.

Scope: tickers in data/rs_universe/latest.json (actively ranked stocks)
       + all tickers already in ticker_labels.json
       Full 5,021-ticker universe is too slow for free Polygon (5 req/min)

Cache: data/themes/description_cache.json — 90-day TTL per ticker
       First run: ~20-40 min (new fetches). Subsequent runs: <2 min.

Schedule: Monthly (1st of month) — themes change slowly
Run:      python scripts/pre_compute/theme_mapper.py
Output:   data/themes/ticker_labels.json (updated in-place, manual entries preserved)
"""

from __future__ import annotations
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure") and getattr(_s, "encoding", "").lower() in ("cp874", "cp1252", "ascii"):
        _s.reconfigure(encoding="utf-8", errors="replace")

LABELS_FILE = BASE_DIR / "data" / "themes" / "ticker_labels.json"
CACHE_FILE  = BASE_DIR / "data" / "themes" / "description_cache.json"
(BASE_DIR / "data" / "themes").mkdir(parents=True, exist_ok=True)

CACHE_TTL_DAYS = 90          # Re-fetch description after 90 days
POLYGON_SLEEP  = 13.0        # 5 req/min free tier -> 12s + 1s buffer
MAX_NEW_FETCHES = 600        # Cap per run — avoid runaway in GitHub Actions


# ── Env ───────────────────────────────────────────────────────────────────────

def _load_env() -> None:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()
POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")


# ── Tier 3: SIC Code -> Theme ──────────────────────────────────────────────────

SIC_THEME_MAP: dict[int, str] = {
    # Semiconductors
    3674: "Memory_HBM",       # Semiconductors & Related (broad — keyword refines)
    3672: "AI_Infra",         # Printed Circuit Boards
    3679: "AI_Infra",         # Electronic Components NEC
    # Computers & Peripherals
    3571: "DataCenter",       # Electronic Computers
    3572: "Memory_HBM",       # Computer Storage Devices
    3577: "DataCenter",       # Computer Peripheral Equipment
    # Communications Equipment
    3661: "Connectivity",     # Telephone & Telegraph Apparatus
    3663: "Connectivity",     # Radio & TV Broadcast & Comm Equipment
    3669: "Connectivity",     # Communications Equipment NEC
    3812: "DefenseTech",      # Defense Electronics & Comm Equipment
    # Optical
    3827: "Photonics",        # Optical Instruments & Lenses
    3674: "Photonics",        # (overridden by keyword for photonics semis)
    # Space & Defense
    3761: "Space",            # Guided Missiles & Space Vehicles
    3769: "Space",            # Space Propulsion Units & Parts
    3489: "DefenseTech",      # Ordnance & Accessories
    3812: "DefenseTech",      # Search/Detection/Navigation Equipment
    # Machinery & Robotics
    3559: "AI_Infra",         # Special Industry Machinery
    3537: "Robotics",         # Industrial Trucks & Tractors
    3569: "Robotics",         # General Industry Machinery NEC
    3825: "AI_Infra",         # Instruments for Measuring
    # Software & Services
    7371: "AI_Related",       # Computer Programming Services
    7372: "AI_Related",       # Prepackaged Software
    7374: "DataCenter",       # Computer Processing & Data Prep
    7389: "AI_Related",       # Services-Computer Maintenance
    # Energy & Nuclear
    1094: "Nuclear_SMR",      # Uranium-Radium-Vanadium Ores Mining
    4911: "Nuclear_SMR",      # Electric Services (nuclear utilities)
    # Telecom Services
    4812: "Connectivity",     # Radiotelephone Communications
    4813: "Connectivity",     # Telephone Communications (No Radio)
    4899: "Connectivity",     # Communications Services NEC
    # REITs
    6798: "DataCenter",       # Real Estate Investment Trusts (data center)
}


# ── Tier 2: Keywords -> Theme ──────────────────────────────────────────────────
# Ordered by specificity — first match wins when scoring is tied
# Each entry: (theme_id, [keywords], weight_per_hit)

THEME_KEYWORDS: list[tuple[str, list[str], float]] = [
    # ── High-specificity themes first ────────────────────────────────────────
    ("Quantum", [
        "quantum computing", "qubit", "quantum processor", "quantum hardware",
        "quantum cryptography", "quantum communication", "quantum sensor",
        "ion trap", "superconducting qubit", "photonic quantum",
    ], 3.0),

    ("Photonics", [
        "silicon photonics", "photonic integrated", "optical transceiver",
        "coherent optics", "wavelength division", "optoelectronic",
        "fiber optic", "lidar", "laser diode", "optical interconnect",
        "photon", "optical amplif", "free-space optical",
        "optical network", "optical module", "pluggable optical",
    ], 2.5),

    ("Nuclear_SMR", [
        "small modular reactor", "smr", "nuclear reactor", "uranium",
        "nuclear power", "enrichment", "nuclear fuel", "thorium",
        "fission", "nuclear energy", "pressurized water reactor",
    ], 3.0),

    ("Drone_UAV", [
        "unmanned aerial", "drone", "uav ", " uav", "unmanned aircraft",
        "counter-drone", "anti-drone", "uncrewed", "autonomous aerial",
        "eVTOL", "electric vertical", "air taxi",
    ], 3.0),

    ("Space", [
        "satellite", "launch vehicle", "orbital", "spacecraft", "rocket",
        "space exploration", "space systems", "smallsat", "cubesat",
        "low earth orbit", "geosynchronous", "space launch",
        "earth observation", "space debris", "reentry vehicle",
        "launch services", "launch site",
    ], 2.5),

    ("DefenseTech", [
        "defense contractor", "defense systems", "military aircraft",
        "electronic warfare", "radar system", "missile defense",
        "armament", "munition", "weapon system", "cybersecurity defense",
        "national security", "intelligence surveillance",
        "counter-unmanned", "c-uas", "ballistic missile",
    ], 2.5),

    ("Robotics", [
        "collaborative robot", "cobot", "autonomous mobile robot",
        "industrial robot", "robotic arm", "robotic surgery",
        "warehouse automation", "pick-and-place", "robotic process",
        "autonomous vehicle", "self-driving", "robot", "robotics",
    ], 2.0),

    ("Memory_HBM", [
        "high bandwidth memory", "hbm", "dram", "nand flash",
        "3d nand", "flash memory", "solid state drive", "ssd",
        "dynamic random access", "memory chip", "memory module",
        "storage semiconductor", "nvm express", "nvme",
    ], 2.5),

    ("Connectivity", [
        "5g network", "5g infrastructure", "5g radio",
        "wireless network", "fiber broadband", "optical network",
        "network equipment", "antenna system", "radio access network",
        "internet of things", " iot ", "iot device",
        "cellular network", "wi-fi", "wi fi",
        "telecom equipment", "backhaul", "fronthaul",
    ], 2.0),

    # ── Broader themes — lower weight to avoid false positives ───────────────
    ("DataCenter_Infra", [
        "data center cooling", "liquid cooling", "thermal management",
        "power distribution unit", "uninterruptible power",
        "modular data center", "data center construction",
        "data center infrastructure", "hvac", "generator",
    ], 2.5),

    ("DataCenter", [
        "data center", "colocation", "hyperscale", "cloud infrastructure",
        "server rack", "network infrastructure", "bare metal cloud",
        "managed hosting", "cloud storage",
    ], 2.0),

    ("NeoCloud", [
        "cloud computing", "cloud platform", "cloud services",
        "infrastructure as a service", "iaas", "platform as a service",
        "paas", "web services", "cloud hosting", "neocloud",
        "gpu cloud", "cloud gpu", "distributed computing cloud",
    ], 2.0),

    ("AI_Infra", [
        "ai infrastructure", "high performance computing", "hpc",
        "infiniband", "ethernet switch", "network switch",
        "power semiconductor", "silicon carbide", "sic mosfet",
        "semiconductor equipment", "etch equipment", "deposition",
        "lithography", "wafer", "fab equipment",
    ], 1.5),

    ("AI_Related", [
        "artificial intelligence", "machine learning", "deep learning",
        "neural network", "large language model", "llm", "generative ai",
        "natural language processing", "nlp", "computer vision",
        "ai chip", "ai accelerator", "inference engine",
        "ai platform", "ai software", "data analytics",
    ], 1.5),
]


# ── Description cache ─────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def _cache_fresh(entry: dict) -> bool:
    try:
        fetched = datetime.fromisoformat(entry["fetched"])
        return datetime.now() - fetched < timedelta(days=CACHE_TTL_DAYS)
    except Exception:
        return False


# ── Polygon ticker detail fetch ───────────────────────────────────────────────

def _fetch_polygon_detail(ticker: str) -> dict | None:
    """
    GET /v3/reference/tickers/{ticker}
    Returns: { name, description, sic_code, sic_description }
    Sleeps POLYGON_SLEEP to respect 5 req/min free limit.
    """
    if not POLYGON_KEY:
        return None
    url = (
        f"https://api.polygon.io/v3/reference/tickers/{ticker}"
        f"?apiKey={POLYGON_KEY}"
    )
    try:
        time.sleep(POLYGON_SLEEP)
        req = urllib.request.Request(url, headers={"User-Agent": "AlphaAbsolute/2.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            r = data.get("results", {})
            return {
                "name":            r.get("name", ""),
                "description":     r.get("description", ""),
                "sic_code":        r.get("sic_code", ""),
                "sic_description": r.get("sic_description", ""),
                "fetched":         datetime.now().isoformat(),
            }
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(f"    [429] Rate limit on {ticker} — sleeping 60s")
            time.sleep(60)
            return _fetch_polygon_detail(ticker)
        return {"name": "", "description": "", "sic_code": "", "sic_description": "", "fetched": datetime.now().isoformat()}
    except Exception as e:
        print(f"    [WARN] Fetch failed {ticker}: {e}")
        return None


def _get_detail(ticker: str, cache: dict, fetched_count: list) -> dict:
    """Return cached detail or fetch fresh. Mutates cache in-place."""
    entry = cache.get(ticker, {})
    if entry and _cache_fresh(entry):
        return entry
    if fetched_count[0] >= MAX_NEW_FETCHES:
        return entry  # don't fetch more this run
    detail = _fetch_polygon_detail(ticker)
    fetched_count[0] += 1
    if detail:
        cache[ticker] = detail
    return cache.get(ticker, {})


# ── Classifier ────────────────────────────────────────────────────────────────

def _classify(detail: dict) -> tuple[str | None, str, str]:
    """
    Returns (theme_id, tier_label, matched_signal).
    theme_id = None if no match found.
    """
    name        = (detail.get("name", "") or "").lower()
    description = (detail.get("description", "") or "").lower()
    sic_raw     = detail.get("sic_code", "") or ""
    combined    = f"{name} {description}"

    # ── Tier 2: keyword scoring ───────────────────────────────────────────────
    scores: dict[str, float] = {}
    matched: dict[str, str]  = {}

    for theme_id, keywords, weight in THEME_KEYWORDS:
        for kw in keywords:
            if kw.lower() in combined:
                scores[theme_id] = scores.get(theme_id, 0) + weight
                if theme_id not in matched:
                    matched[theme_id] = kw

    if scores:
        best_theme = max(scores, key=lambda t: scores[t])
        return best_theme, "keyword", matched[best_theme]

    # ── Tier 3: SIC code ──────────────────────────────────────────────────────
    try:
        sic = int(sic_raw)
        if sic in SIC_THEME_MAP:
            return SIC_THEME_MAP[sic], "sic", f"SIC {sic}"
    except (ValueError, TypeError):
        pass

    return None, "none", ""


# ── Label loader / saver ──────────────────────────────────────────────────────

def _load_labels() -> tuple[dict, dict]:
    """Returns (manual_labels, full_labels_data)."""
    if not LABELS_FILE.exists():
        return {}, {}
    data = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    labels = data.get("labels", {})
    # Identify manual entries (no source field or source == "manual")
    sources = data.get("sources", {})
    manual = {t: l for t, l in labels.items() if sources.get(t, "manual") == "manual"}
    return manual, data


def _save_labels(labels: dict, sources: dict, stats: dict) -> None:
    data = {
        "_meta": {
            "description": "Ticker -> Theme mapping. Manual entries always override auto-classification.",
            "themes": [
                "AI_Related", "Memory_HBM", "Space", "Quantum", "Photonics",
                "DefenseTech", "DataCenter", "Nuclear_SMR", "NeoCloud",
                "AI_Infra", "DataCenter_Infra", "Drone_UAV", "Robotics", "Connectivity",
            ],
            "last_updated":   date.today().isoformat(),
            "total_tickers":  len(labels),
            "auto_classified": stats.get("auto", 0),
            "manual_entries":  stats.get("manual", 0),
            "unclassified":    stats.get("unclassified", 0),
            "note": "source field: manual | keyword | sic | none",
        },
        "labels":  labels,
        "sources": sources,
    }
    LABELS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Scope: which tickers to classify ─────────────────────────────────────────

def _get_scope() -> list[str]:
    """
    Returns tickers to classify this run:
    1. All tickers in rs_universe/latest.json (actively ranked)
    2. All tickers already in ticker_labels.json (keep them fresh)
    Deduped + sorted.
    """
    tickers: set[str] = set()

    # rs_universe ranked tickers
    rs_file = BASE_DIR / "data" / "rs_universe" / "latest.json"
    if rs_file.exists():
        try:
            rs_data = json.loads(rs_file.read_text(encoding="utf-8"))
            universe = rs_data.get("universe", {})
            tickers.update(universe.keys())
            print(f"  Scope: {len(universe)} tickers from rs_universe/latest.json")
        except Exception:
            pass

    # existing labeled tickers
    if LABELS_FILE.exists():
        try:
            ldata = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
            tickers.update(ldata.get("labels", {}).keys())
        except Exception:
            pass

    return sorted(tickers)


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  Theme Mapper  [{today}]")
    print(f"  Tier 2: keyword match | Tier 3: SIC code")
    print(f"{'='*55}")

    # Load existing labels (preserve manual entries)
    manual_labels, existing_data = _load_labels()
    existing_labels  = existing_data.get("labels", {})
    existing_sources = existing_data.get("sources", {})
    print(f"  Existing: {len(existing_labels)} labeled tickers "
          f"({len(manual_labels)} manual overrides)")

    # Load description cache
    cache = _load_cache()
    print(f"  Description cache: {len(cache)} entries (TTL {CACHE_TTL_DAYS}d)")

    # Determine scope
    scope = _get_scope()
    print(f"  Total scope: {len(scope)} tickers to process\n")

    # Classify
    new_labels:   dict[str, str] = {}
    new_sources:  dict[str, str] = {}
    fetched_count = [0]

    added = changed = skipped_manual = cached_hit = unclassified = 0

    for i, ticker in enumerate(scope, 1):
        # Manual override — never touch
        if ticker in manual_labels:
            new_labels[ticker]  = manual_labels[ticker]
            new_sources[ticker] = "manual"
            skipped_manual += 1
            continue

        # Fetch (or load from cache)
        detail = _get_detail(ticker, cache, fetched_count)
        if not detail:
            unclassified += 1
            continue
        if detail.get("fetched") and ticker in cache:
            # Check if we actually fetched fresh this call
            pass
        else:
            cached_hit += 1

        theme_id, tier, signal = _classify(detail)

        if theme_id is None:
            unclassified += 1
            new_labels[ticker]  = existing_labels.get(ticker, "")
            new_sources[ticker] = "none"
            continue

        old_theme = existing_labels.get(ticker, "")
        if old_theme and old_theme != theme_id:
            print(f"  [CHANGE] {ticker}: {old_theme} -> {theme_id} [{tier}: {signal}]")
            changed += 1
        elif not old_theme:
            print(f"  [NEW]    {ticker}: -> {theme_id} [{tier}: {signal}]")
            added += 1

        new_labels[ticker]  = theme_id
        new_sources[ticker] = tier

        # Progress every 50 tickers
        if i % 50 == 0:
            print(f"  ... {i}/{len(scope)} processed | "
                  f"fetched {fetched_count[0]} new | cached {cached_hit}")

        # Save cache periodically
        if fetched_count[0] > 0 and fetched_count[0] % 50 == 0:
            _save_cache(cache)

    # Merge: keep any existing labeled tickers not in scope
    for ticker, theme in existing_labels.items():
        if ticker not in new_labels:
            new_labels[ticker]  = theme
            new_sources[ticker] = existing_sources.get(ticker, "manual")

    # Save
    _save_cache(cache)

    stats = {
        "auto":          added + changed,
        "manual":        skipped_manual,
        "unclassified":  unclassified,
    }
    _save_labels(new_labels, new_sources, stats)

    # Theme distribution summary
    theme_counts: dict[str, int] = {}
    for t in new_labels.values():
        if t:
            theme_counts[t] = theme_counts.get(t, 0) + 1

    print(f"\n{'='*55}")
    print(f"  Done: {len(new_labels)} total labeled")
    print(f"  New: {added} | Changed: {changed} | Manual (preserved): {skipped_manual}")
    print(f"  Unclassified: {unclassified} | New API fetches: {fetched_count[0]}")
    print(f"\n  Theme distribution:")
    for theme, count in sorted(theme_counts.items(), key=lambda x: -x[1]):
        bar = "█" * min(count // 2, 30)
        print(f"    {theme:<20} {count:>3}  {bar}")
    print(f"{'='*55}\n")

    return {
        "date":          today,
        "total_labeled": len(new_labels),
        "added":         added,
        "changed":       changed,
        "manual":        skipped_manual,
        "unclassified":  unclassified,
        "new_fetches":   fetched_count[0],
        "theme_counts":  theme_counts,
    }


if __name__ == "__main__":
    run()
