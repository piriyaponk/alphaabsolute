"""
AlphaAbsolute -- Theme Labeler
==============================
Auto-labels tickers to the 14 AlphaAbsolute investment themes using
company description + SIC code keyword matching, then updates
data/themes/ticker_labels.json.

Strategy:
  1. Load existing ticker_labels.json (manual labels preserved)
  2. Get ticker list from data/rs_universe/latest.json
  3. Skip tickers already labeled
  4. For remaining tickers: check description cache, else fetch from Polygon
  5. Classify via keyword match (priority) then SIC fallback
  6. Write new labels with source=keyword or source=sic
  7. Save report to data/themes/theme_labeler_report.json

Usage:
  python scripts/pre_compute/theme_labeler.py
  python scripts/pre_compute/theme_labeler.py --force       # re-fetch all descriptions
  python scripts/pre_compute/theme_labeler.py --dry-run     # show what would be labeled, don't save
  python scripts/pre_compute/theme_labeler.py --limit 50    # only process 50 tickers (test mode)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))   # make scripts.utils importable

# ── Paths ──────────────────────────────────────────────────────────────────────
LABELS_FILE       = BASE_DIR / "data" / "themes" / "ticker_labels.json"
DESC_CACHE_FILE   = BASE_DIR / "data" / "themes" / "description_cache.json"
COMPANY_CACHE_DIR = BASE_DIR / "data" / "themes" / "company_info_cache"
RS_UNIVERSE_FILE  = BASE_DIR / "data" / "rs_universe" / "latest.json"
REPORT_FILE       = BASE_DIR / "data" / "themes" / "theme_labeler_report.json"
LOG_DIR           = BASE_DIR / "data" / "runner_logs"

COMPANY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ────────────────────────────────────────────────────────────────────
_today = datetime.now().strftime("%y%m%d")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"theme_labeler_{_today}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── Load .env ──────────────────────────────────────────────────────────────────
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

# ── Theme Keywords (ordered most-specific → least-specific; first match wins) ──
#
# Design principles:
#  1. Multi-word phrases preferred over single words (less false positives)
#  2. Each theme covers MAKERS/BUILDERS of that tech, not mere users
#  3. Order = priority: Quantum before AI_Related, Photonics before Connectivity
#  4. Negative filters block the most common false positive classes
#
THEME_KEYWORDS_ORDERED: list[tuple[str, list[str]]] = [

    # ── 1. Quantum — most specific, very few companies ─────────────────────────
    ("Quantum", [
        "quantum computing", "quantum computer", "qubit",
        "trapped ion quantum", "superconducting qubit", "photonic qubit",
        "quantum processor", "quantum hardware", "quantum anneal",
        "quantum cryptography", "quantum key distribution", "qkd",
        "quantum sensor", "quantum communication", "quantum networking",
        "quantum error correction", "quantum advantage",
    ]),

    # ── 2. Nuclear_SMR — specific energy technology ────────────────────────────
    ("Nuclear_SMR", [
        "small modular reactor", "smr", "advanced nuclear reactor",
        "nuclear power plant", "nuclear fuel", "nuclear power generation",
        "uranium enrichment", "uranium mining", "uranium producer",
        "nuclear energy company", "fission reactor", "molten salt reactor",
        "thorium reactor", "fusion reactor", "nuclear grade material",
        "advanced nuclear", "nuclear technology company",
    ]),

    # ── 3. Space — launch + spacecraft + Earth-observation satellites ──────────
    #    (NOT satellite TV, satellite radio, or satellite internet service)
    ("Space", [
        "launch vehicle", "rocket engine", "rocket motor", "rocket propulsion",
        "spacecraft manufacturer", "spacecraft bus",
        "satellite manufacturer", "satellite constellation operator",
        "earth observation satellite", "synthetic aperture radar satellite",
        "low earth orbit satellite", "leo satellite",
        "nanosatellite", "smallsat", "cubesat",
        "orbital launch services", "space launch", "reusable rocket",
        "space exploration", "deep space mission",
        "space debris removal", "in-space propulsion",
        "space tourism vehicle", "spaceplane",
    ]),

    # ── 4. Drone_UAV — uncrewed aerial + lidar navigation systems ─────────────
    ("Drone_UAV", [
        "unmanned aerial vehicle", "uav manufacturer", "uncrewed aerial",
        "drone delivery", "drone manufacturer", "drone platform",
        "counter-drone", "anti-drone system", "counter-uas",
        "evtol", "electric vertical takeoff", "air taxi",
        "urban air mobility", "advanced air mobility",
        "autonomous flight system", "unmanned aircraft system", "uas",
        "drone swarm", "military drone", "tactical drone",
    ]),

    # ── 5. Photonics — light-based chips & components (makers, not integrators)
    ("Photonics", [
        "silicon photonics", "photonic integrated circuit",
        "electro-optic polymer", "electro-optic modulator",
        "optical modulator chip", "mach-zehnder modulator",
        "vcsel", "vertical-cavity surface-emitting laser",
        "indium phosphide photonics", "inp photonics",
        "gallium arsenide laser", "gaas laser chip",
        "photonic chip", "photonics foundry",
        "coherent optical chip", "optical dsp chip",
        "optical switch chip", "wavelength division multiplexing chip",
        "lidar chip", "photodetector array",
        # Compound semiconductor substrates (wafer suppliers to photonics industry)
        "indium phosphide wafer", "indium phosphide substrate", "inp wafer",
        "gallium arsenide wafer", "gallium arsenide substrate", "gaas wafer",
        "compound semiconductor substrate", "compound semiconductor wafer",
        "germanium wafer", "germanium substrate",
    ]),

    # ── 6. Memory_HBM — DRAM/NAND/HBM chip makers (NOT storage system companies)
    ("Memory_HBM", [
        "dram", "nand flash", "nand memory chip", "flash memory semiconductor",
        "high bandwidth memory", "hbm", "hbm3", "hbm4",
        "ddr5 memory", "ddr4 memory", "lpddr", "gddr",
        "memory semiconductor", "memory chip manufacturer",
        "3d nand", "nand flash manufacturer",
        "nor flash", "3d xpoint",
        "memory module manufacturer", "memory packaging",
        # Emerging memory types
        "mram", "magnetoresistive ram", "stt-mram", "magnetic random access memory",
        "spin-transfer torque", "reram", "pcm memory", "phase change memory",
        # Memory test / measurement (supply chain — e.g. COHU thermal test for HBM)
        "memory test system", "semiconductor test handler",
        "burn-in test", "thermal test for semiconductor", "hbm test",
    ]),

    # ── 7. NeoCloud — GPU cloud + crypto mining (MAKING compute, not using it)
    ("NeoCloud", [
        "gpu cloud computing", "ai cloud provider", "bare metal gpu",
        "hpc cloud services", "high performance computing cloud",
        "gpu as a service", "inference cloud",
        "bitcoin mining", "bitcoin miner", "bitcoin mining facility",
        "ethereum mining", "crypto mining", "cryptocurrency mining",
        "digital asset mining", "proof of work mining", "hash rate",
        "mining hardware", "asic miner manufacturer",
    ]),

    # ── 8. DataCenter_Infra — power, cooling, physical infra FOR data centers ──
    ("DataCenter_Infra", [
        "data center cooling", "liquid cooling for servers",
        "liquid cooling solution", "liquid cooled server",
        "direct liquid cooling", "immersion cooling",
        "data center thermal management",
        # Server/rack infrastructure vendors (SMCI, HPE type companies)
        "server and storage system", "ai server", "gpu server",
        "high density server", "rack server manufacturer",
        "data center power supply", "data center ups",
        "uninterruptible power supply for data center",
        "power distribution unit data center",
        "critical facility power", "precision air conditioning",
        "computer room air handler", "crac unit",
        "modular data center", "prefabricated data center",
        "data center infrastructure management", "dcim",
        "data center construction", "mission critical facility construction",
        "hyperscale power", "data center electrical",
    ]),

    # ── 9. DataCenter — operators, cloud providers, REITs ─────────────────────
    ("DataCenter", [
        "data center operator", "data center reit",
        "colocation data center", "colocation provider",
        "hyperscale cloud provider", "public cloud computing",
        "content delivery network", "cdn provider",
        "managed hosting provider", "internet exchange point",
        "cloud infrastructure services",
    ]),

    # ── 10. DefenseTech — military & defense systems manufacturers ─────────────
    ("DefenseTech", [
        "defense contractor", "prime defense contractor",
        "defense electronics", "electronic warfare system",
        "missile guidance system", "missile defense",
        "radar system for defense", "military radar",
        "military aerospace", "defense aerospace system",
        "combat system", "battlefield management",
        "naval defense system", "military shipbuilding",
        "ammunition manufacturer", "munitions manufacturer",
        "armament manufacturer", "weapons system manufacturer",
        "tactical communication system", "military satellite",
        "defense satellite", "classified defense program",
        "c4isr", "intelligence surveillance reconnaissance",
        "counter-terrorism technology", "border security system",
    ]),

    # ── 11. Connectivity — network hardware, interconnects, transceivers ───────
    #    (MAKES it — chip/system vendors; NOT telecom SERVICE providers)
    ("Connectivity", [
        # PCIe/CXL AI interconnects (e.g. ALAB — Astera Labs)
        "pcie retimer", "pcie redriver", "pcie switch chip",
        "cxl controller", "compute express link",
        "network retimer", "signal integrity semiconductor",
        # Network ASICs & SoCs
        "network asic", "network soc", "packet processing chip",
        "ethernet switch chip", "ethernet switch silicon",
        "smart nic", "data processing unit", "dpu",
        # Optical transceivers (product companies)
        "optical transceiver module", "coherent optical transceiver",
        "pluggable transceiver", "qsfp module", "osfp module",
        "cfp2 transceiver", "100g transceiver", "400g transceiver",
        # Wireless chips
        "wi-fi chip", "wlan soc", "5g modem chip",
        "5g baseband chip", "radio frequency semiconductor",
        "rf frontend module",
        # Optical networking equipment (system vendors)
        "optical networking equipment", "wdm equipment",
        "reconfigurable optical add-drop", "roadm",
        "coherent optical transport",
        # Broadband / access equipment
        "broadband access equipment", "dsl chipset", "cable modem chip",
        "network equipment manufacturer", "fiber optic network equipment",
        # IoT connectivity
        "iot connectivity module", "cellular iot module",
        "lpwan", "lorawan chip",
    ]),

    # ── 12. AI_Infra — tools & platforms for BUILDING/DEPLOYING AI ────────────
    #    (NOT the AI products themselves; NOT physical infra)
    ("AI_Infra", [
        "mlops platform", "machine learning operations",
        "model training infrastructure", "model serving platform",
        "ai model deployment", "ai inference platform",
        "vector database", "embedding database",
        "data labeling platform", "ai annotation",
        "ai observability", "ml monitoring platform",
        "feature store", "model registry",
        "synthetic data for ai", "ai data pipeline",
        "gpu orchestration", "kubernetes ml",
    ]),

    # ── 13. Robotics — robots, automation, autonomous ground systems ───────────
    ("Robotics", [
        "industrial robot", "collaborative robot", "cobot manufacturer",
        "robotic arm", "robot manipulator",
        "autonomous mobile robot", "amr manufacturer",
        "automated guided vehicle", "agv manufacturer",
        "humanoid robot", "service robot",
        "warehouse automation robot", "fulfillment robot",
        "robot software platform", "robot operating system",
        "machine vision for robotics",
        "lidar sensor", "3d lidar sensor", "solid-state lidar",
        "point cloud sensor", "time-of-flight sensor",
        "autonomous ground vehicle", "self-driving sensor",
        "pick and place robot", "welding robot",
        "exoskeleton", "surgical robot",
    ]),

    # ── 14. AI_Related — AI product companies + AI chip supply chain ────────────
    # NOTE: Do NOT use short 3-letter substrings ("llm","npu","eda") — they appear
    # inside common words ("fulfillment" has "llm", "input" has "npu", "idea" has "eda").
    # Always use full phrases so substring matching does not create false positives.
    ("AI_Related", [
        "artificial intelligence platform", "generative ai",
        "large language model", "foundation model",
        "ai accelerator chip", "neural processing unit",
        "ai chip", "ai processor", "machine learning chip",
        "deep learning accelerator", "computer vision software",
        "natural language processing platform", "nlp platform",
        "ai software platform", "ai analytics platform",
        "ai-powered", "ai-driven product", "ai-first company",
        "transformer architecture", "ai safety company",
        "responsible ai platform", "autonomous ai",
        # Semiconductor supply chain enabling AI chips (test, inspection, EDA)
        "semiconductor test equipment", "wafer inspection", "wafer metrology",
        "wafer cleaning equipment", "etch equipment", "cvd equipment",
        "semiconductor process control", "chip packaging",
        "advanced packaging", "fan-out wafer level", "fowlp",
        "electronic design automation", "eda software",
        "photomask", "reticle", "semiconductor lithography",
        "ion implant", "semiconductor process",
    ]),
]

# Build dict for backward-compat (rs_theme_ranker uses THEME_KEYWORDS)
THEME_KEYWORDS: dict[str, list[str]] = {t: kws for t, kws in THEME_KEYWORDS_ORDERED}

# ── SIC Code Fallback (conservative — only very specific codes) ────────────────
SIC_THEME_MAP: dict[str, str] = {
    "3761": "Space",            # Guided Missiles & Space Vehicles
    "3769": "Space",            # Space Propulsion Units & Parts
    "3812": "DefenseTech",      # Defense Electronics (very defense-specific)
    "3827": "Photonics",        # Optical Instruments & Lenses
    "1094": "Nuclear_SMR",      # Uranium-Radium-Vanadium Ores
    # Deliberately removed:
    #   3621 (Motors & Generators — catches wind turbine makers (AMSC), generators (GNRC), not robots)
    #   3674 (too broad — all semis, catches NVDA + random chip companies equally)
    #   3728 (Aircraft Parts — now too broad, catches Boeing suppliers)
    #   3559 (Special Industry Machinery — catches ACMR/wafer equipment which isn't Robotics)
    #   7372 (Software — catches everything from SAP to Salesforce)
    #   4812/4813 (Telephone — catches AT&T/Verizon, pure service providers)
    #   4911 (Electric Services — not all nuclear, catches utilities)
    #   3699 (Electronic Components NEC — too vague)
}

CACHE_MAX_AGE_DAYS = 30
POLYGON_DELAY_S    = 13.0   # ~4.6 req/min — safely under Polygon free-tier 5 req/min


# ── Negative filters — block well-known false positive classes ─────────────────
NEGATIVE_FILTERS: dict[str, list[str]] = {
    "DefenseTech": [
        # Consumer / food companies whose names contain "gun", "rifle", "armor" etc.
        "coffee", "food", "beverage", "restaurant", "grocery", "supermarket",
        "fast food", "burger", "pizza", "brewing", "winery", "spirits",
        # Healthcare (HUM, CNC, CI match "security"/"systems")
        "insurance", "health plan", "health insurance", "managed care",
        "pharmacy benefit", "hospital system", "healthcare provider",
        "dental", "vision plan",
        # Financial (matches "security")
        "bank", "banking", "investment bank", "wealth management",
        "financial services", "insurance underwriting",
        # Retail / consumer
        "clothing", "apparel", "fashion brand", "department store",
        "consumer products", "consumer goods",
        # Real estate
        "real estate", "property management", "residential",
    ],
    "Space": [
        # Satellite TV/radio service providers — NOT space tech
        "satellite television", "satellite tv", "direct-to-home television",
        "direct broadcast satellite television",
        "satellite radio", "satellite music service",
        "satellite internet service provider",   # ISP using satellites (DISH, Hughesnet)
        # False "space" hits from real estate / retail
        "parking space", "retail space", "office space", "living space",
        "working space", "storage space", "co-working space",
    ],
    "Nuclear_SMR": [
        # Medical nuclear = NOT energy nuclear
        "nuclear medicine", "radiopharmaceutical", "nuclear imaging",
        "positron emission", "pet scan", "molecular imaging",
        "radiation therapy", "radiotherapy", "brachytherapy",
    ],
    "NeoCloud": [
        # Crypto analytics / exchanges — NOT compute infrastructure
        "blockchain analytics", "crypto analytics",
        "nft marketplace", "non-fungible token platform",
        "crypto exchange", "digital asset exchange",
        "crypto wallet", "defi protocol", "decentralized finance",
        "blockchain consulting", "crypto lending",
    ],
    "Memory_HBM": [
        # Storage SYSTEM companies — they BUY chips, not make them
        "hard disk drive", "tape storage", "storage area network",
        "enterprise storage system", "all-flash storage",
        "storage software", "backup software", "data management software",
        "storage networking",
    ],
    "DataCenter": [
        # Power/cooling companies should go to DataCenter_Infra, not DataCenter
        "cooling system manufacturer", "thermal management",
        "liquid cooling manufacturer", "ups manufacturer",
        "power supply manufacturer", "power distribution unit manufacturer",
    ],
    "Connectivity": [
        # Telecom SERVICE providers — NOT hardware/chip makers
        "telephone service", "wireless service provider", "mobile carrier",
        "cable television service", "broadband service provider",
        "internet service provider", "isp",
        "managed telecom service",
    ],
    "Photonics": [
        # Eye care / optics retail — NOT photonics semiconductor
        "optical retail", "eye care", "vision care", "eyewear",
        "contact lens", "ophthalmic product", "corrective lens",
        # Display tech — quantum dot display ≠ photonics
        "display panel", "oled display",
    ],
    "Quantum": [
        # Quantum DOT = display tech, not quantum computing
        "quantum dot display", "qled television", "quantum dot film",
        "quantum dot enhancement",
    ],
    "AI_Related": [
        # Generic SaaS that just says "AI" in marketing copy
        "human resources software", "hr software", "payroll software",
        "accounting software", "tax software", "legal software",
        "real estate software", "property management software",
        "insurance software", "claims management",
    ],
    "Robotics": [
        # Toy robots — not industrial
        "toy robot", "educational robot for children", "hobby robot",
    ],
}


# ── Classification (ordered — first match wins) ────────────────────────────────
def classify_ticker(name: str, description: str, sic: str) -> tuple[str | None, str]:
    """
    Returns (theme_id, method) where method is 'keyword', 'sic', or 'none'.
    Processes themes in THEME_KEYWORDS_ORDERED priority order (specific → broad).
    Negative filters block false positives before committing to a match.
    """
    text = f"{name} {description}".lower()

    for theme_id, keywords in THEME_KEYWORDS_ORDERED:
        if not any(kw in text for kw in keywords):
            continue
        blockers = NEGATIVE_FILTERS.get(theme_id, [])
        if any(b in text for b in blockers):
            continue
        return theme_id, "keyword"

    if sic and sic in SIC_THEME_MAP:
        return SIC_THEME_MAP[sic], "sic"

    return None, "none"


# ── Cache Helpers ──────────────────────────────────────────────────────────────
def _load_desc_cache() -> dict:
    """Load the flat description_cache.json (legacy format)."""
    if DESC_CACHE_FILE.exists():
        try:
            return json.loads(DESC_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_desc_cache(cache: dict) -> None:
    DESC_CACHE_FILE.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _get_company_info_from_per_ticker_cache(ticker: str, force: bool) -> dict | None:
    """Check per-ticker JSON cache in company_info_cache/. Returns dict or None."""
    path = COMPANY_CACHE_DIR / f"{ticker}.json"
    if force or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched_str = data.get("fetched", "")
        if fetched_str:
            fetched_dt = datetime.fromisoformat(fetched_str)
            if datetime.now() - fetched_dt < timedelta(days=CACHE_MAX_AGE_DAYS):
                return data
    except Exception:
        pass
    return None


def _save_company_info_to_cache(ticker: str, info: dict) -> None:
    path = COMPANY_CACHE_DIR / f"{ticker}.json"
    info["fetched"] = datetime.now().isoformat()
    path.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_company_info_from_legacy_cache(ticker: str, desc_cache: dict, force: bool) -> dict | None:
    """Check the flat description_cache.json (already-fetched entries)."""
    if force or ticker not in desc_cache:
        return None
    entry = desc_cache[ticker]
    fetched_str = entry.get("fetched", "")
    if fetched_str:
        try:
            fetched_dt = datetime.fromisoformat(fetched_str)
            if datetime.now() - fetched_dt < timedelta(days=CACHE_MAX_AGE_DAYS):
                return entry
        except Exception:
            pass
    return None


# ── Polygon Fetch ──────────────────────────────────────────────────────────────
def fetch_from_polygon(ticker: str) -> dict | None:
    """
    Fetch company reference data from Polygon /v3/reference/tickers/{ticker}.
    Returns dict with name, description, sic_code, sic_description, market_cap,
    primary_exchange. Returns None on failure.
    """
    if not POLYGON_KEY:
        log.warning("POLYGON_API_KEY not set — cannot fetch reference data")
        return None

    url = f"https://api.polygon.io/v3/reference/tickers/{ticker}"
    try:
        resp = requests.get(
            url,
            params={"apiKey": POLYGON_KEY},
            timeout=10,
            verify=False,
        )
        if resp.status_code == 404:
            log.debug(f"{ticker}: 404 from Polygon reference")
            return None
        if resp.status_code == 429:
            # Sleep 65s = full rate-limit window reset (Polygon free = 5 req/min)
            log.warning(f"{ticker}: 429 rate limit — sleeping 65s then retrying once")
            time.sleep(65)
            resp = requests.get(url, params={"apiKey": POLYGON_KEY},
                                timeout=10, verify=False)
            if resp.status_code == 429:
                log.warning(f"{ticker}: still 429 after retry — skipping")
                return None
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", {})
        if not results:
            return None

        return {
            "name":             results.get("name", ""),
            "description":      results.get("description", ""),
            "sic_code":         str(results.get("sic_code", "") or ""),
            "sic_description":  results.get("sic_description", ""),
            "market_cap":       results.get("market_cap"),
            "primary_exchange": results.get("primary_exchange", ""),
        }
    except requests.exceptions.RequestException as exc:
        log.debug(f"{ticker}: fetch error — {exc}")
        return None


# ── Load Ticker Labels ─────────────────────────────────────────────────────────
def load_labels() -> tuple[dict, dict, dict]:
    """
    Returns (meta, labels, sources) dicts from ticker_labels.json.
    """
    if not LABELS_FILE.exists():
        meta = {
            "description": "Ticker -> Theme mapping. Manual entries always override auto-classification.",
            "themes": list(THEME_KEYWORDS.keys()),
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
            "total_tickers": 0,
            "auto_classified": 0,
            "manual_entries": 0,
            "unclassified": 0,
            "note": "source field: manual | keyword | sic | none",
        }
        return meta, {}, {}
    raw = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    return raw.get("_meta", {}), raw.get("labels", {}), raw.get("sources", {})


def save_labels(meta: dict, labels: dict, sources: dict) -> None:
    n_manual  = sum(1 for v in sources.values() if v == "manual")
    n_keyword = sum(1 for v in sources.values() if v == "keyword")
    n_sic     = sum(1 for v in sources.values() if v == "sic")

    meta["last_updated"]    = datetime.now().strftime("%Y-%m-%d")
    meta["total_tickers"]   = len(labels)
    meta["manual_entries"]  = n_manual
    meta["auto_classified"] = n_keyword + n_sic

    out = {"_meta": meta, "labels": labels, "sources": sources}
    LABELS_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"Saved ticker_labels.json — {len(labels)} total ({n_manual} manual, {n_keyword} keyword, {n_sic} sic)")


# ── Load RS Universe Ticker List ───────────────────────────────────────────────
def load_rs_tickers() -> list[str]:
    if not RS_UNIVERSE_FILE.exists():
        log.error(f"RS universe file not found: {RS_UNIVERSE_FILE}")
        return []
    data = json.loads(RS_UNIVERSE_FILE.read_text(encoding="utf-8"))
    ranked = data.get("ranked", [])
    tickers = [r["ticker"] for r in ranked if isinstance(r, dict) and "ticker" in r]
    log.info(f"RS universe: {len(tickers)} tickers")
    return tickers


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="AlphaAbsolute Theme Labeler")
    parser.add_argument("--force",   action="store_true", help="Re-fetch all descriptions from Polygon")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be labeled, don't save")
    parser.add_argument("--limit",   type=int, default=0,  help="Only process N tickers (test mode)")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("AlphaAbsolute Theme Labeler starting")
    log.info(f"  force={args.force}  dry_run={args.dry_run}  limit={args.limit or 'none'}")
    log.info("=" * 60)

    # Load state
    meta, labels, sources = load_labels()
    rs_tickers   = load_rs_tickers()
    desc_cache   = _load_desc_cache()

    already_labeled = set(labels.keys())
    manual_tickers  = {t for t, s in sources.items() if s == "manual"}

    # Only process tickers in RS universe that don't have a label yet
    to_process = [t for t in rs_tickers if t not in already_labeled]

    if args.limit > 0:
        to_process = to_process[: args.limit]

    log.info(f"Tickers in RS universe:   {len(rs_tickers)}")
    log.info(f"Already labeled:          {len(already_labeled)} ({len(manual_tickers)} manual)")
    log.info(f"To process:               {len(to_process)}")

    if not to_process:
        log.info("Nothing to process — all RS tickers already labeled.")
        return

    # Track stats
    n_keyword   = 0
    n_sic       = 0
    n_no_match  = 0
    n_api_fetch = 0
    n_cache_hit = 0
    newly_labeled: list[tuple[str, str, str]] = []   # (ticker, theme, method)

    for idx, ticker in enumerate(to_process, 1):
        # Progress log every 25 tickers
        if idx % 25 == 0 or idx == 1:
            log.info(f"  [{idx}/{len(to_process)}] Processing {ticker} ...")

        # 1. Try per-ticker cache (company_info_cache/)
        info = _get_company_info_from_per_ticker_cache(ticker, args.force)

        # 2. Try legacy flat cache (description_cache.json)
        if info is None:
            info = _get_company_info_from_legacy_cache(ticker, desc_cache, args.force)
            if info is not None:
                n_cache_hit += 1
                # Back-populate per-ticker cache
                _save_company_info_to_cache(ticker, dict(info))
        else:
            n_cache_hit += 1

        # 3. Fetch from Polygon if still missing
        if info is None:
            info = fetch_from_polygon(ticker)
            if info is not None:
                n_api_fetch += 1
                _save_company_info_to_cache(ticker, dict(info))
                # Also update flat cache for backward compat
                desc_cache[ticker] = dict(info)
                # Rate limit
                time.sleep(POLYGON_DELAY_S)
            else:
                # Still can't get info — skip
                n_no_match += 1
                log.debug(f"{ticker}: no company info available")
                continue

        # 4. Classify
        name        = info.get("name", "")
        description = info.get("description", "")
        sic         = info.get("sic_code", "")

        theme, method = classify_ticker(name, description, sic)

        if theme is None:
            n_no_match += 1
            log.debug(f"{ticker}: no match — name={name[:40]!r}  sic={sic}")
        else:
            newly_labeled.append((ticker, theme, method))
            if method == "keyword":
                n_keyword += 1
            else:
                n_sic += 1
            log.debug(f"{ticker}: {theme} via {method} — {name[:50]!r}")
            if not args.dry_run:
                labels[ticker]  = theme
                sources[ticker] = method

        # Write company info to SQLite regardless of theme match
        if not args.dry_run and info:
            try:
                from scripts.utils.ohlcv_store import OHLCVStore
                OHLCVStore().upsert_company_info(
                    ticker      = ticker,
                    name        = name,
                    description = description[:500],   # cap at 500 chars
                    sic_code    = sic,
                    theme       = theme,
                    theme_source= method,
                )
            except Exception:
                pass

        # Checkpoint save every 100 tickers (survive interruption)
        if not args.dry_run and idx % 100 == 0:
            save_labels(meta, labels, sources)
            log.info(f"  [checkpoint] saved {len(labels)} labels")

    # ── Summary ────────────────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 60)
    log.info("THEME LABELER COMPLETE")
    log.info(f"  Tickers processed:   {len(to_process)}")
    log.info(f"  Cache hits:          {n_cache_hit}")
    log.info(f"  Polygon API fetches: {n_api_fetch}")
    log.info(f"  Newly labeled:       {len(newly_labeled)}")
    log.info(f"    keyword match:     {n_keyword}")
    log.info(f"    SIC match:         {n_sic}")
    log.info(f"  No match:            {n_no_match}")

    # Count by theme
    by_theme: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    for ticker, theme, method in newly_labeled:
        by_theme[theme] = by_theme.get(theme, 0) + 1
        if theme not in examples:
            examples[theme] = []
        if len(examples[theme]) < 10:
            examples[theme].append(ticker)

    log.info("")
    log.info("By theme:")
    for theme, count in sorted(by_theme.items(), key=lambda x: -x[1]):
        ex = ", ".join(examples.get(theme, [])[:5])
        log.info(f"  {theme:<20} {count:>4}  eg: {ex}")

    # ── Save ───────────────────────────────────────────────────────────────────
    if args.dry_run:
        log.info("")
        log.info("[DRY RUN] — no files written")
        log.info("Would label:")
        for ticker, theme, method in newly_labeled[:30]:
            log.info(f"  {ticker:<8} -> {theme}  ({method})")
        if len(newly_labeled) > 30:
            log.info(f"  ... and {len(newly_labeled) - 30} more")
    else:
        # Save flat desc cache updates
        _save_desc_cache(desc_cache)

        # Save updated labels (final)
        save_labels(meta, labels, sources)

        # ── Sync all labels to SQLite company_info (including manual ones) ──
        try:
            from scripts.utils.ohlcv_store import OHLCVStore
            store = OHLCVStore()
            synced = 0
            for t, theme_id in labels.items():
                src = sources.get(t, "manual")
                store.upsert_company_info(ticker=t, theme=theme_id, theme_source=src)
                synced += 1
            log.info(f"SQLite company_info: {synced} labels synced to ohlcv.db")
        except Exception as e:
            log.warning(f"SQLite sync failed: {e}")

        # Save report
        report = {
            "date":             datetime.now().strftime("%Y-%m-%d"),
            "run_at":           datetime.now().isoformat(),
            "n_tickers_checked": len(to_process),
            "n_newly_labeled":  len(newly_labeled),
            "n_keyword_match":  n_keyword,
            "n_sic_match":      n_sic,
            "n_no_match":       n_no_match,
            "n_cache_hits":     n_cache_hit,
            "n_api_fetches":    n_api_fetch,
            "by_theme":         by_theme,
            "examples":         examples,
        }
        REPORT_FILE.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info(f"Report saved: {REPORT_FILE}")

    log.info("Done.")


if __name__ == "__main__":
    main()
