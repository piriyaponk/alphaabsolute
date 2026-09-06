"""
AlphaAbsolute — Sector Labeler (Universal)
==========================================
Classifies ALL 1,246 RS-universe tickers by business group.

Priority chain (first match wins):
  1. Already has a theme label → keep (skip)
  2. Description keyword → theme or sector
  3. SIC 4-digit exact match → sector
  4. SIC 2-digit prefix → sector
  5. Company name pattern → sector
  6. "Other"

Label namespace:
  AlphaAbsolute themes (14):  AI_Related, Memory_HBM, Space, Quantum, Photonics,
                               DefenseTech, DataCenter, Nuclear_SMR, NeoCloud,
                               AI_Infra, DataCenter_Infra, Drone_UAV, Robotics,
                               Connectivity
  Sector labels (28):         Finance_Bank, Finance_Insurance, Finance_Investment,
                               Finance_Fintech, Healthcare_Biotech, Healthcare_Pharma,
                               Healthcare_MedDevice, Healthcare_Services,
                               Energy_OilGas, Energy_Renewable,
                               Consumer_Retail, Consumer_Restaurant, Consumer_Food,
                               Consumer_Auto, Consumer_Media, Consumer_Travel,
                               Industrials, Industrials_Aerospace,
                               Industrials_Construction, Materials,
                               Real_Estate, Transport, Software, Semiconductor,
                               Telecom_Services, Utilities, ETF_Fund, Other

Usage:
  python scripts/pre_compute/sector_labeler.py
  python scripts/pre_compute/sector_labeler.py --force   # re-label all (overwrite existing)
  python scripts/pre_compute/sector_labeler.py --dry-run # show counts only
"""

from __future__ import annotations
import json
import re
import sys
import sqlite3
import argparse
from datetime import date
from pathlib import Path
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR      = Path(__file__).resolve().parents[2]
LABELS_FILE   = BASE_DIR / "data" / "themes" / "ticker_labels.json"
CACHE_DIR     = BASE_DIR / "data" / "themes" / "company_info_cache"
RS_LATEST     = BASE_DIR / "data" / "rs_universe" / "latest.json"
DB_PATH       = BASE_DIR / "data" / "ohlcv.db"

sys.path.insert(0, str(BASE_DIR))


# ══════════════════════════════════════════════════════════════════════════════
# 1. THEME DETECTION (extended keywords for unlabeled thematic stocks)
#    These catch stocks that SHOULD be in our 14 themes but weren't labeled yet.
# ══════════════════════════════════════════════════════════════════════════════

THEME_SIGNALS: list[tuple[str, list[str]]] = [
    # ── Quantum ──────────────────────────────────────────────────────────────
    ("Quantum", [
        "quantum computing", "quantum computer", "qubit", "trapped ion", "superconducting qubit",
        "quantum key distribution", "quantum cryptography", "quantum network",
        "quantum error correction", "quantum hardware", "quantum processor",
        "photonic quantum", "quantum algorithm",
    ]),
    # ── Nuclear_SMR ──────────────────────────────────────────────────────────
    ("Nuclear_SMR", [
        "small modular reactor", "nuclear reactor", "nuclear power plant",
        "uranium enrichment", "uranium mining", "uranium production",
        "advanced nuclear", "molten salt reactor", "microreactor",
        "nuclear fuel", "nuclear energy company",
    ]),
    # ── Space ─────────────────────────────────────────────────────────────────
    ("Space", [
        "launch vehicle", "rocket propulsion", "spacecraft manufacturer",
        "low earth orbit satellite", "smallsat", "cubesat", "satellite imagery",
        "earth observation satellite", "lunar lander", "lunar mission",
        "space infrastructure", "in-space manufacturing", "space services",
        "satellite constellation operator", "geospatial intelligence satellite",
    ]),
    # ── Drone_UAV ─────────────────────────────────────────────────────────────
    ("Drone_UAV", [
        "unmanned aerial vehicle", "evtol", "electric vertical takeoff",
        "air taxi", "autonomous aircraft", "counter-drone", "drone delivery",
        "uav manufacturer", "vtol aircraft",
    ]),
    # ── Photonics ─────────────────────────────────────────────────────────────
    ("Photonics", [
        "silicon photonics", "photonic integrated circuit", "coherent optical",
        "electro-optic", "vcsel", "indium phosphide", "compound semiconductor wafer",
        "optical modulator", "photonics chip", "laser chip", "gallium arsenide",
        "inp wafer", "inp photonics", "optical component manufacturer",
        "optical transceiver manufacturer", "lidar chip", "photonic sensing",
    ]),
    # ── Memory_HBM ────────────────────────────────────────────────────────────
    ("Memory_HBM", [
        "dram manufacturer", "nand flash manufacturer", "high bandwidth memory",
        "hbm memory", "semiconductor memory chip", "flash memory chip",
        "magnetoresistive ram", "mram memory", "stt-mram",
        "wafer probe", "semiconductor test handler", "burn-in test",
        "etch system", "cvd system", "cmp system", "deposition equipment",
        "ion implantation", "diffusion furnace", "photomask manufacturer",
        "wafer cleaning system", "semiconductor packaging substrate",
    ]),
    # ── NeoCloud ──────────────────────────────────────────────────────────────
    ("NeoCloud", [
        "bitcoin mining", "bitcoin miner", "cryptocurrency mining", "crypto mining",
        "proof of work mining", "hash rate", "gpu cloud computing",
        "ai cloud infrastructure", "hyperscale gpu", "neocloud provider",
    ]),
    # ── DataCenter_Infra ─────────────────────────────────────────────────────
    ("DataCenter_Infra", [
        "data center cooling", "liquid cooling for servers", "direct liquid cooling",
        "data center power", "uninterruptible power supply for data center",
        "data center ups", "power distribution unit", "thermal management data center",
        "server rack cooling", "ai server manufacturer", "gpu server manufacturer",
        "immersion cooling", "data center generator",
    ]),
    # ── DataCenter ────────────────────────────────────────────────────────────
    ("DataCenter", [
        "colocation data center", "data center operator", "data center reit",
        "hyperscale data center", "content delivery network", "cdn provider",
        "managed hosting", "internet exchange", "cloud storage provider",
        "network attached storage", "enterprise storage system",
    ]),
    # ── DefenseTech ──────────────────────────────────────────────────────────
    ("DefenseTech", [
        "defense contractor", "electronic warfare", "missile guidance",
        "c4isr system", "military satellite", "defense intelligence",
        "cybersecurity for government", "government cybersecurity",
        "national security software", "military communication",
        "autonomous weapon system", "defense electronics system",
    ]),
    # ── Connectivity ─────────────────────────────────────────────────────────
    ("Connectivity", [
        "pcie retimer", "cxl controller", "compute express link",
        "network asic", "ethernet switch chip", "optical transceiver module",
        "400g transceiver", "800g transceiver", "coherent transceiver",
        "5g semiconductor", "wi-fi chip", "wireless lan chip",
        "baseband processor", "network switch asic", "cloud networking chip",
        "co-packaged optics", "silicon photonics transceiver",
    ]),
    # ── AI_Infra ─────────────────────────────────────────────────────────────
    ("AI_Infra", [
        "mlops platform", "model serving platform", "vector database",
        "data labeling platform", "ml training infrastructure",
        "ai observability", "feature store", "synthetic data platform",
    ]),
    # ── Robotics ─────────────────────────────────────────────────────────────
    ("Robotics", [
        "autonomous driving software", "self-driving technology",
        "lidar for autonomous vehicles", "autonomous mobile robot",
        "industrial robot manufacturer", "collaborative robot",
        "humanoid robot", "surgical robot system", "robotic automation",
        "autonomous vehicle sensor", "3d lidar", "solid-state lidar sensor",
    ]),
    # ── AI_Related ───────────────────────────────────────────────────────────
    ("AI_Related", [
        "artificial intelligence platform", "generative ai",
        "large language model", "foundation model",
        "ai accelerator chip", "neural processing unit",
        "machine learning platform", "deep learning platform",
        "computer vision platform", "ai software platform",
        "electronic design automation", "eda software",
        "semiconductor lithography", "euv lithography",
        "advanced packaging substrate", "chip on wafer",
        "ai chip design", "ai processor design",
        "autonomous ai agent", "ai-first company",
    ]),
]

# ══════════════════════════════════════════════════════════════════════════════
# 2. SECTOR LABELS — for tickers NOT in our 14 themes
# ══════════════════════════════════════════════════════════════════════════════

# ── 2a. Description keyword → sector ─────────────────────────────────────────
SECTOR_DESC_SIGNALS: list[tuple[str, list[str]]] = [
    ("Healthcare_Biotech", [
        "biopharmaceutical company", "biotechnology company", "gene therapy",
        "cell therapy", "mrna therapy", "antibody drug", "oncology drug",
        "clinical stage", "drug candidate", "therapeutic candidate",
        "genomic medicine", "precision medicine startup", "crispr",
        "biologics manufacturer", "monoclonal antibody",
    ]),
    ("Healthcare_Pharma", [
        "pharmaceutical company", "specialty pharma", "branded pharmaceutical",
        "generic pharmaceutical", "drug manufacturer", "prescription drug",
        "over the counter drug", "pharmaceutical products",
    ]),
    ("Healthcare_MedDevice", [
        "medical device", "surgical instrument", "orthopedic implant",
        "cardiovascular device", "neurostimulation", "diagnostic imaging",
        "in vitro diagnostics", "laboratory instrument", "point of care diagnostic",
        "dental equipment", "ophthalmic device", "minimally invasive surgical",
        "robotic surgical system", "implantable device",
    ]),
    ("Healthcare_Services", [
        "healthcare services", "hospital system", "health insurance",
        "managed care", "pharmacy benefit", "home health care",
        "behavioral health", "telehealth platform", "physician practice",
        "ambulatory care", "outpatient care", "health plan",
        "dental care services", "vision care services",
    ]),
    ("Energy_OilGas", [
        "crude oil", "natural gas exploration", "oil and gas producer",
        "upstream oil", "downstream refining", "oil refinery",
        "petroleum products", "oilfield services", "drilling services",
        "oil pipeline", "lng terminal", "offshore drilling",
        "oil sands", "shale oil", "fracking",
    ]),
    ("Energy_Renewable", [
        "solar energy", "solar power", "photovoltaic", "wind energy",
        "wind turbine", "fuel cell", "hydrogen fuel",
        "renewable energy developer", "clean energy", "green energy",
        "ev charging", "electric vehicle charging", "battery storage",
        "energy storage system", "grid-scale battery",
    ]),
    ("Finance_Fintech", [
        "payment processing", "payment technology", "digital payment",
        "mobile payment", "point of sale system", "merchant payment",
        "financial technology", "fintech platform", "neobank",
        "buy now pay later", "cryptocurrency exchange",
        "digital banking platform", "banking as a service",
    ]),
    ("Finance_Investment", [
        "business development company", "closed-end fund",
        "private equity firm", "hedge fund", "asset management company",
        "investment management", "wealth management",
        "exchange traded fund", "index fund", "mutual fund",
        "special purpose acquisition",  # SPAC
    ]),
    ("Finance_Insurance", [
        "life insurance", "property and casualty insurance",
        "health insurance underwriter", "reinsurance",
        "title insurance", "mortgage insurance",
        "workers compensation insurance", "specialty insurance",
        "insurance holding company",
    ]),
    ("Finance_Bank", [
        "commercial bank", "savings bank", "credit union",
        "community bank", "regional bank", "national bank",
        "banking operations", "deposit taking institution",
        "retail banking", "corporate banking",
    ]),
    ("Real_Estate", [
        "real estate investment trust", "reit", "property management",
        "commercial real estate", "residential real estate developer",
        "office building owner", "industrial reit",
        "net lease reit", "mall operator", "shopping center",
        "apartment reit", "self storage reit", "data center reit",
        "mortgage reit", "real estate development",
    ]),
    ("Consumer_Media", [
        "entertainment company", "streaming platform", "video game developer",
        "online gaming", "esports", "digital media", "social media platform",
        "advertising technology", "programmatic advertising",
        "film studio", "television network", "sports media",
        "live events company", "music streaming",
    ]),
    ("Consumer_Travel", [
        "hotel company", "resort operator", "cruise line",
        "airline company", "travel booking", "online travel agency",
        "vacation rental", "theme park", "leisure company",
        "airport services", "aircraft manufacturer",
    ]),
    ("Consumer_Restaurant", [
        "restaurant chain", "quick service restaurant", "casual dining",
        "food delivery platform", "coffee chain", "fast food",
        "bakery chain", "pizza chain", "burger chain",
    ]),
    ("Consumer_Food", [
        "food manufacturer", "food company", "food and beverage",
        "packaged food", "consumer packaged goods", "snack food",
        "beverage manufacturer", "alcoholic beverage", "soft drink",
        "dairy products", "frozen food", "organic food",
    ]),
    ("Consumer_Auto", [
        "automobile manufacturer", "automotive company",
        "car manufacturer", "electric vehicle manufacturer",
        "auto parts manufacturer", "tier 1 automotive supplier",
        "automotive technology", "connected car", "autonomous vehicle company",
    ]),
    ("Consumer_Retail", [
        "retail company", "specialty retailer", "department store",
        "e-commerce company", "online retailer", "direct to consumer",
        "consumer products company", "apparel company", "fashion brand",
        "home improvement store", "grocery chain", "drug store chain",
    ]),
    ("Software", [
        "software company", "software as a service", "saas platform",
        "cloud software", "enterprise software", "business software",
        "cybersecurity software", "erp software", "crm software",
        "data analytics software", "workflow automation",
        "human capital management", "supply chain software",
    ]),
    ("Semiconductor", [
        "semiconductor company", "fabless semiconductor", "chip design",
        "integrated circuit design", "analog semiconductor",
        "power semiconductor", "microcontroller", "microprocessor",
        "soc design", "system on chip", "application specific integrated circuit",
        "asic design company", "fpga", "memory chip",
        "sensor chip", "rf semiconductor", "mixed signal semiconductor",
    ]),
    ("Materials", [
        "specialty chemicals", "chemical company", "industrial chemicals",
        "polymer manufacturer", "advanced materials",
        "steel manufacturer", "aluminum producer", "metal mining",
        "gold mining", "silver mining", "copper mining",
        "lithium mining", "rare earth materials",
        "paper manufacturer", "packaging company",
    ]),
    ("Industrials_Aerospace", [
        "commercial aerospace", "aircraft systems", "aviation technology",
        "aerospace components", "satellite components", "space propulsion",
    ]),
    ("Industrials_Construction", [
        "homebuilder", "residential construction", "commercial construction",
        "building materials", "construction services",
        "engineering and construction", "infrastructure developer",
    ]),
    ("Transport", [
        "shipping company", "ocean freight", "air cargo",
        "logistics company", "supply chain management",
        "freight forwarding", "trucking company", "last mile delivery",
        "railroad company", "rail transport", "intermodal transport",
    ]),
    ("Telecom_Services", [
        "wireless carrier", "mobile carrier", "broadband provider",
        "cable television", "internet service provider",
        "telecommunications services", "fiber optic network operator",
        "satellite internet service",
    ]),
    ("Utilities", [
        "electric utility", "natural gas utility", "water utility",
        "power generation", "electricity distribution",
        "regulated utility", "investor owned utility",
    ]),
    ("Industrials", [
        "industrial company", "manufacturing company",
        "diversified industrial", "specialty manufacturing",
        "automation company", "process control",
        "commercial services", "business services",
    ]),
    ("ETF_Fund", [
        "exchange traded fund", "index fund", "etf",
        "closed end fund", "business development company",
        "special purpose acquisition company", "blank check company",
    ]),
]

# ── 2b. SIC 4-digit → sector ──────────────────────────────────────────────────
SIC4_MAP: dict[str, str] = {
    # Healthcare / Pharma
    "2830": "Healthcare_Pharma", "2833": "Healthcare_Pharma",
    "2834": "Healthcare_Pharma", "2835": "Healthcare_MedDevice",
    "2836": "Healthcare_Biotech",
    "3841": "Healthcare_MedDevice", "3842": "Healthcare_MedDevice",
    "3843": "Healthcare_MedDevice", "3844": "Healthcare_MedDevice",
    "3845": "Healthcare_MedDevice", "3826": "Healthcare_MedDevice",
    "8011": "Healthcare_Services", "8049": "Healthcare_Services",
    "8051": "Healthcare_Services", "8060": "Healthcare_Services",
    "8062": "Healthcare_Services", "8082": "Healthcare_Services",
    "8090": "Healthcare_Services", "8099": "Healthcare_Services",
    "8011": "Healthcare_Services",
    "8731": "Healthcare_Biotech",
    # Semiconductor & Electronics
    "3674": "Semiconductor", "3672": "Tech_Hardware",
    "3669": "Tech_Hardware", "3677": "Tech_Hardware",
    "3678": "Tech_Hardware", "3679": "Tech_Hardware",
    "3699": "Tech_Hardware", "3577": "Tech_Hardware",
    "3579": "Tech_Hardware", "3625": "Tech_Hardware",
    "3629": "Tech_Hardware", "3661": "Tech_Hardware",
    "3825": "Tech_Hardware", "3827": "Photonics",
    "3812": "DefenseTech",
    # Software
    "7371": "Software", "7372": "Software", "7374": "Software",
    "7375": "Software", "7376": "Software", "7379": "Software",
    # Finance
    "6000": "Finance_Bank", "6020": "Finance_Bank", "6022": "Finance_Bank",
    "6035": "Finance_Bank", "6036": "Finance_Bank", "6099": "Finance_Bank",
    "6110": "Finance_Investment", "6120": "Finance_Investment",
    "6141": "Finance_Investment", "6150": "Finance_Investment",
    "6159": "Finance_Investment", "6162": "Finance_Investment",
    "6199": "Finance_Investment",
    "6211": "Finance_Investment", "6221": "Finance_Investment",
    "6282": "Finance_Investment", "6726": "Finance_Investment",
    "6770": "Finance_Investment", "6722": "Finance_Investment",
    "6311": "Finance_Insurance", "6321": "Finance_Insurance",
    "6331": "Finance_Insurance", "6351": "Finance_Insurance",
    "6411": "Finance_Insurance",
    "6500": "Real_Estate", "6510": "Real_Estate", "6512": "Real_Estate",
    "6513": "Real_Estate", "6552": "Real_Estate", "6798": "Real_Estate",
    # Energy
    "1311": "Energy_OilGas", "1381": "Energy_OilGas", "1382": "Energy_OilGas",
    "1389": "Energy_OilGas", "2911": "Energy_OilGas", "5171": "Energy_OilGas",
    "4922": "Energy_OilGas", "4923": "Energy_OilGas", "4924": "Energy_OilGas",
    # Mining
    "1040": "Materials", "1090": "Materials", "1400": "Materials",
    "1094": "Nuclear_SMR",  # uranium — already in theme
    # Utilities
    "4911": "Utilities", "4920": "Utilities", "4931": "Utilities",
    "4941": "Utilities", "4950": "Utilities", "4991": "Utilities",
    # Telecom
    "4812": "Telecom_Services", "4813": "Telecom_Services",
    "4899": "Telecom_Services", "4811": "Telecom_Services",
    # Transport
    "4011": "Transport", "4013": "Transport",
    "4210": "Transport", "4213": "Transport", "4215": "Transport",
    "4400": "Transport", "4412": "Transport",
    "4512": "Transport", "4515": "Transport", "4522": "Transport",
    "4700": "Transport", "4731": "Transport",
    # Consumer
    "5812": "Consumer_Restaurant",
    "5311": "Consumer_Retail", "5331": "Consumer_Retail", "5411": "Consumer_Retail",
    "5651": "Consumer_Retail", "5912": "Consumer_Retail",
    "5711": "Consumer_Retail", "5940": "Consumer_Retail",
    "5511": "Consumer_Auto",
    "7011": "Consumer_Travel", "7996": "Consumer_Leisure",
    "7812": "Consumer_Media",
    "2080": "Consumer_Food", "2086": "Consumer_Food",
    "2111": "Consumer_Staples",
    # Industrials
    "3560": "Industrials", "3569": "Industrials", "3510": "Industrials",
    "3540": "Industrials", "3490": "Industrials", "3559": "Industrials",
    "3585": "Industrials", "3612": "Industrials", "3613": "Industrials",
    "3621": "Industrials", "3714": "Consumer_Auto", "3710": "Consumer_Auto",
    "3720": "Industrials_Aerospace", "3728": "Industrials_Aerospace",
    "1520": "Industrials_Construction", "1531": "Industrials_Construction",
    "1600": "Industrials_Construction", "1731": "Industrials_Construction",
    # Chemicals/Materials
    "2819": "Materials", "2821": "Materials", "2822": "Materials",
    "2869": "Materials", "3312": "Materials", "3317": "Materials",
    "3334": "Materials", "3339": "Materials",
    # Media
    "4833": "Consumer_Media", "4832": "Consumer_Media",
    "7999": "Consumer_Travel",
    # Misc services
    "7389": "Industrials", "7380": "Industrials",
    "8711": "Industrials", "8742": "Software",
}

# ── 2c. SIC 2-digit prefix → sector (fallback) ───────────────────────────────
SIC2_MAP: dict[str, str] = {
    "01": "Consumer_Food", "02": "Consumer_Food", "07": "Consumer_Food",
    "10": "Materials", "11": "Materials", "12": "Materials",
    "13": "Energy_OilGas", "14": "Materials",
    "15": "Industrials_Construction", "16": "Industrials_Construction",
    "17": "Industrials_Construction",
    "20": "Consumer_Food", "21": "Consumer_Staples",
    "22": "Industrials", "23": "Consumer_Retail",
    "24": "Materials", "25": "Consumer_Retail",
    "26": "Materials", "27": "Consumer_Media",
    "28": "Materials",  # overridden by SIC4 for pharma/biotech
    "29": "Energy_OilGas",
    "30": "Materials", "31": "Consumer_Retail", "32": "Materials",
    "33": "Materials", "34": "Industrials",
    "35": "Industrials", "36": "Tech_Hardware",
    "37": "Consumer_Auto",  # overridden by SIC4 for aerospace
    "38": "Tech_Hardware", "39": "Industrials",
    "40": "Transport", "41": "Transport", "42": "Transport",
    "44": "Transport", "45": "Transport", "47": "Transport",
    "48": "Telecom_Services", "49": "Utilities",
    "50": "Industrials", "51": "Industrials",
    "52": "Consumer_Retail", "53": "Consumer_Retail",
    "54": "Consumer_Retail", "55": "Consumer_Auto",
    "56": "Consumer_Retail", "57": "Consumer_Retail",
    "58": "Consumer_Restaurant", "59": "Consumer_Retail",
    "60": "Finance_Bank", "61": "Finance_Investment",
    "62": "Finance_Investment", "63": "Finance_Insurance",
    "64": "Finance_Insurance", "65": "Real_Estate",
    "67": "Finance_Investment",
    "70": "Consumer_Travel", "72": "Industrials",
    "73": "Software", "74": "Industrials", "75": "Consumer_Auto",
    "78": "Consumer_Media", "79": "Consumer_Travel",
    "80": "Healthcare_Services",
    "82": "Industrials", "83": "Industrials", "87": "Industrials",
}

# ── 2d. Company name patterns → sector ───────────────────────────────────────
# Order matters: more specific patterns first.
NAME_PATTERNS: list[tuple[str, list[str]]] = [
    # ── Finance: Banking ──────────────────────────────────────────────────────
    ("Finance_Bank", [
        r"\bbancorp\b", r"\bbancshares\b", r"\bbankers?\b", r"\bbanking\b",
        r"national bank", r"federal bank", r"savings bank", r"community bank",
        r"first bank", r"state bank", r"\bfargo\b", r"financial bank",
        r"\bfsb\b", r"trust bank", r"bank holding",
    ]),
    # ── Finance: Insurance ────────────────────────────────────────────────────
    ("Finance_Insurance", [
        r"\binsurance\b", r"\bcasualty\b", r"\bindemnity\b",
        r"life insurance", r"health insurance company",
        r"\breinsurance\b", r"\bunderwriters?\b",
        r"assurance company", r"\bannuity\b",
    ]),
    # ── Finance: Real Estate / REIT ───────────────────────────────────────────
    ("Real_Estate", [
        r"\brealty\b", r"\breit\b", r"real estate investment",
        r"\bproperties\b(?! management software)",
        r"realty trust", r"property trust", r"\bhomebuilder\b",
        r"real estate inc", r"equity residential",
        r"apartment homes", r"storage centers",
    ]),
    # ── Finance: Investment / Asset Management ────────────────────────────────
    ("Finance_Investment", [
        r"asset management", r"investment management", r"wealth management",
        r"capital management", r"\betf\b", r"exchange traded fund",
        r"closed.end fund", r"business development company",
        r"private equity", r"venture capital",
        r"\bspac\b", r"blank check company",
        r"closed end", r"invesco", r"blackrock fund",
    ]),
    # ── Healthcare: Biotech ───────────────────────────────────────────────────
    ("Healthcare_Biotech", [
        r"therapeutics",          # Aardvark Therapeutics, Adicet Bio uses Therapeutics
        r"biotechnolog",          # catches Biotechnologies (not just Biotech)
        r"\bbiotech\b",           # standalone Biotech
        r"\bbiosciences?\b",      # Bioscience/Biosciences
        r"\bbiopharma\b",         # BioPharma
        r"biotherapeutics",       # Biotherapeutics
        r"biopharmaceutical",     # Biopharmaceutical
        r"\bbio,?\s",             # standalone Bio: "Adicet Bio, Inc"
        r"biomed\b",              # BioMed
        r"biocare\b",             # GloboCare type
        r"biolog",                # biologics, biological
        r"gene therapy",
        r"cell therapy",
        r"oncolog",               # oncology, oncologic
        r"genomics",
        r"\bimmuno",              # immunology, immunogen
        r"antibody",
        r"neuroscience",          # Firefly Neuroscience
        r"\bneurol",              # neurology, neurologic
        r"cognition\b",           # Alpha Cognition
        r"alzheimer",
        r"crispr",
        r"mrna\b",
        r"vaccine\b",
    ]),
    # ── Healthcare: Pharma ────────────────────────────────────────────────────
    ("Healthcare_Pharma", [
        r"\bpharmaceuticals?\b", r"\bpharma\b(?!cy)", r"drug company",
        r"specialty pharma", r"branded pharma",
        r"\bdrug\b.*inc", r"medicines?\b.*inc",
    ]),
    # ── Healthcare: Medical Devices ───────────────────────────────────────────
    ("Healthcare_MedDevice", [
        r"medical devices?", r"medtech", r"\borthopedic\b",
        r"\bsurgical\b", r"\bdiagnostics?\b(?! software)",
        r"\bdental\b", r"life sciences equipment",
        r"\bmedical systems?\b", r"\beye care\b", r"\bophthalmic\b",
        r"cardiac\b", r"cardiovascular", r"\bimplant\b",
    ]),
    # ── Healthcare: Services ──────────────────────────────────────────────────
    ("Healthcare_Services", [
        r"health services?", r"healthcare services?", r"\bhospital\b",
        r"managed care", r"pharmacy benefit",
        r"home health", r"behavioral health",
        r"health plan", r"health systems?",
        r"healthcare.*inc\b", r"health.*corp\b",
    ]),
    # ── Energy: Oil & Gas ─────────────────────────────────────────────────────
    ("Energy_OilGas", [
        r"\boil\b.*gas", r"petroleum", r"\boilfield\b",
        r"exploration.*production", r"\bdrilling\b",
        r"\bupstream\b", r"refining company", r"\blng\b",
        r"oil sands", r"natural gas.*company",
        r"pipeline company", r"energy resources",
    ]),
    # ── Energy: Renewable ────────────────────────────────────────────────────
    ("Energy_Renewable", [
        r"\bsolar\b", r"\bwind energy\b", r"renewable energy",
        r"clean energy", r"fuel cell", r"hydrogen.*energy",
        r"ev charging", r"electric vehicle charging",
        r"battery.*energy storage", r"green energy",
        r"brookfield renewable",
    ]),
    # ── Materials & Mining ───────────────────────────────────────────────────
    ("Materials", [
        r"\bgold\b.*mining", r"\bsilver\b.*mining",
        r"\bcopper\b.*mining", r"\blithium\b.*mining",
        r"precious metals", r"base metals",
        r"specialty chemicals?", r"chemical company",
        r"\bsteel\b", r"\baluminum\b", r"metals.*company",
        r"mining company", r"\bminerals\b",
    ]),
    # ── Consumer: Restaurants ─────────────────────────────────────────────────
    ("Consumer_Restaurant", [
        r"restaurant", r"dining", r"food service",
        r"\bcoffee\b.*company", r"quick service",
        r"fast.*food", r"casual dining",
    ]),
    # ── Consumer: Food & Beverage ─────────────────────────────────────────────
    ("Consumer_Food", [
        r"food company", r"food.*beverage",
        r"packaged foods?", r"consumer foods?",
        r"agricultural company", r"grain company",
        r"dairy products", r"meat.*company",
        r"beverage company", r"spirits.*company",
        r"wine.*company", r"brewing company",
    ]),
    # ── Consumer: Automotive ─────────────────────────────────────────────────
    ("Consumer_Auto", [
        r"automobile", r"\bautomotive\b",
        r"\bmotor\b.*company", r"vehicle manufacturer",
        r"car company", r"auto parts",
    ]),
    # ── Consumer: Media & Entertainment ──────────────────────────────────────
    ("Consumer_Media", [
        r"entertainment company", r"media company",
        r"streaming.*entertainment", r"video.*game",
        r"esports", r"gaming company",
        r"advertising company", r"digital media",
        r"broadcast", r"film.*studio",
    ]),
    # ── Consumer: Travel & Hospitality ───────────────────────────────────────
    ("Consumer_Travel", [
        r"\bhotels?\b", r"\bresort\b", r"hospitality company",
        r"\bcruise\b", r"\bairlines?\b", r"\bairways\b",
        r"travel.*company", r"vacation.*rental",
        r"leisure company", r"\bcasino\b", r"gaming.*resort",
    ]),
    # ── Consumer: Retail ─────────────────────────────────────────────────────
    ("Consumer_Retail", [
        r"retail company", r"specialty retail",
        r"\bstores?\b.*inc", r"\bmarkets?\b.*corp",
        r"e-commerce", r"direct.*consumer",
        r"\bapparel\b", r"fashion company",
    ]),
    # ── Industrials: Aerospace ────────────────────────────────────────────────
    ("Industrials_Aerospace", [
        r"aerospace.*company", r"aviation.*technology",
        r"aircraft.*manufacturer", r"aircraft.*parts",
        r"commercial.*aircraft",
    ]),
    # ── Industrials: Construction ─────────────────────────────────────────────
    ("Industrials_Construction", [
        r"homebuilder", r"home.*builder", r"construction.*company",
        r"building.*materials",
        r"engineering.*construction", r"infrastructure.*developer",
    ]),
    # ── Transport ────────────────────────────────────────────────────────────
    ("Transport", [
        r"shipping.*company", r"logistics.*company",
        r"freight.*company", r"cargo.*company",
        r"trucking.*company", r"\brailroad\b",
        r"transportation.*services?",
    ]),
    # ── Software ─────────────────────────────────────────────────────────────
    ("Software", [
        r"software.*company", r"saas.*company",
        r"cloud.*software", r"enterprise.*software",
        r"data.*analytics.*platform",
    ]),
    # ── Semiconductor ─────────────────────────────────────────────────────────
    ("Semiconductor", [
        r"semiconductor.*company", r"fabless.*semiconductor",
        r"analog.*semiconductor", r"power.*semiconductor",
        r"chip.*design", r"integrated.*circuit",
    ]),
    # ── Telecom Services ─────────────────────────────────────────────────────
    ("Telecom_Services", [
        r"wireless.*carrier", r"mobile.*carrier",
        r"broadband.*provider", r"cable.*television",
        r"internet.*service.*provider",
        r"telecommunications.*services?",
    ]),
    # ── Utilities ────────────────────────────────────────────────────────────
    ("Utilities", [
        r"electric.*utility", r"gas.*utility",
        r"power.*generation", r"electricity.*distribution",
        r"regulated.*utility",
    ]),
    # ── ETF / Fund ───────────────────────────────────────────────────────────
    ("ETF_Fund", [
        r"\betf\b", r"exchange traded fund",
        r"trust,? series", r"index.*fund",
        r"invesco.*trust", r"spdr", r"ishares",
        r"vanguard.*fund",
    ]),
    # ── SPAC / Blank Check ───────────────────────────────────────────────────
    ("Finance_Investment", [
        r"acquisition corp", r"acquisition inc",
        r"blank check company", r"\bspac\b",
        r"acquisition limited", r"acquisition ltd",
        r"acquisition co\b",
    ]),
    # ── Cannabis ─────────────────────────────────────────────────────────────
    ("Consumer_Staples", [
        r"\bcannabis\b", r"\bmarijuana\b", r"\bcbd\b.*company",
    ]),
]

# Additional known-ticker overrides (manual, high-confidence)
# Priority: beats all keyword/SIC/name pattern matching
KNOWN_OVERRIDES: dict[str, str] = {
    # ── AlphaAbsolute Thematic — already labeled, listed for completeness ─────
    "ALAB":  "Connectivity",          # Astera Labs — PCIe retimers, CXL
    "ARM":   "AI_Related",            # Arm Holdings — CPU IP for AI chips
    "ASML":  "Memory_HBM",            # ASML — EUV lithography for advanced chips
    "AUR":   "Robotics",              # Aurora Innovation — autonomous driving
    "BAND":  "Connectivity",          # Bandwidth Inc — cloud comms platform
    "BTDR":  "NeoCloud",              # Bitdeer — Bitcoin mining
    "CDNS":  "AI_Related",            # Cadence Design Systems — EDA
    "FTNT":  "DefenseTech",           # Fortinet — cybersecurity
    "SMTC":  "Photonics",             # Semtech — LoRa, optical ICs
    "BIOA":  "Healthcare_Biotech",    # BioAge Labs

    # ── Software / SaaS ──────────────────────────────────────────────────────
    "AAPL":  "Tech_Hardware",         # Apple — hardware/ecosystem
    "ADBE":  "Software",              # Adobe — creative/document cloud
    "ADSK":  "Software",              # Autodesk — CAD/engineering software
    "ADP":   "Software",              # ADP — payroll/HR software
    "ACN":   "Software",              # Accenture — IT consulting
    "ANSS":  "Software",              # Ansys — simulation software
    "ACIW":  "Software",              # ACI Worldwide — payments software
    "ALKT":  "Software",              # Alkami — digital banking software
    "ALRM":  "Software",              # Alarm.com — smart home SaaS
    "AVLR":  "Software",              # Avalara (acquired by Avalara)
    "BILL":  "Software",              # Bill.com — AP/AR automation
    "COUP":  "Software",              # Coupa — procurement software
    "DBX":   "Software",              # Dropbox — cloud storage
    "DDOG":  "AI_Related",            # Already labeled
    "DOCN":  "Software",              # DigitalOcean — cloud platform
    "ESTC":  "Software",              # Elastic — search/observability
    "EXLS":  "Software",              # ExlService — analytics/outsourcing
    "FIVN":  "Software",              # Five9 — cloud contact center
    "FRSH":  "Software",              # Freshworks — CRM/ITSM SaaS
    "GH":    "Healthcare_Biotech",    # Guardant Health — liquid biopsy
    "GTLB":  "Software",              # GitLab — DevSecOps platform
    "HCP":   "Software",              # HashiCorp — cloud infra automation
    "HUBS":  "Software",              # HubSpot — CRM/marketing platform
    "ICE":   "Finance_Investment",    # Intercontinental Exchange
    "INTU":  "Software",              # Intuit — tax/accounting software
    "IOSP":  "Materials",             # Innospec — fuel additives/chemicals
    "KVYO":  "Software",              # Klaviyo — marketing automation
    "MANH":  "Software",              # Manhattan Associates — supply chain SW
    "MDB":   "Software",              # MongoDB — database platform
    "NCNO":  "Software",              # nCino — banking cloud software
    "NTNX":  "Software",              # Nutanix — cloud infrastructure SW
    "PCTY":  "Software",              # Paylocity — payroll/HR SaaS
    "PAYC":  "Software",              # Paycom — payroll software
    "PD":    "Software",              # PagerDuty — digital ops SaaS
    "PCOR":  "Software",              # Procore — construction software
    "RNG":   "Software",              # RingCentral — cloud communications
    "RGEN":  "Healthcare_Biotech",    # Repligen — bioprocess equipment
    "SAIL":  "Software",              # SailPoint — identity security
    "SE":    "Consumer_Media",        # Sea Limited — gaming/ecom/fintech
    "SHOP":  "Software",              # Shopify — ecom platform
    "SMAR":  "Software",              # Smartsheet — work mgmt SaaS
    "SPSC":  "Software",              # SPS Commerce — supply chain EDI
    "SPLK":  "Software",              # Splunk — data platform
    "SPT":   "Software",              # Sprout Social — social media mgmt
    "SWI":   "Software",              # SolarWinds — IT mgmt software
    "TEAM":  "Software",              # Atlassian — dev collab software
    "TOST":  "Software",              # Toast — restaurant POS platform
    "TYL":   "Software",              # Tyler Technologies — govt software
    "TWLO":  "Software",              # Twilio — cloud comms platform
    "VEEV":  "Software",              # Veeva — life sciences cloud SW
    "VRNS":  "Software",              # Varonis — data security SaaS
    "WEX":   "Software",              # WEX — fleet/benefits software
    "XPEL":  "Consumer_Auto",         # XPEL — auto paint protection film
    "YEXT":  "Software",              # Yext — digital presence platform
    "ZI":    "Software",              # ZoomInfo — B2B data platform
    "ZM":    "Software",              # Zoom — video conferencing

    # ── Semiconductor ────────────────────────────────────────────────────────
    "ADI":   "Semiconductor",         # Analog Devices — analog/mixed-signal
    "ALGM":  "Semiconductor",         # Allegro MicroSystems — power/sensing
    "AMKR":  "Memory_HBM",            # Amkor — chip packaging/test
    "AMBQ":  "Semiconductor",         # Ambiq Micro — ultra-low-power MCU
    "AZTA":  "Robotics",              # Already labeled
    "INTC":  "Semiconductor",         # Intel — CPUs/data center chips
    "LSCC":  "Semiconductor",         # Lattice Semiconductor — FPGA
    "MCHP":  "Semiconductor",         # Microchip Technology — MCU/analog
    "MTSI":  "Semiconductor",         # MACOM — RF/microwave/analog
    "ON":    "DataCenter_Infra",      # Already labeled (onsemi — power)
    "QRVO":  "Semiconductor",         # Qorvo — RF semiconductors
    "SITM":  "Semiconductor",         # SiTime — MEMS timing
    "SWKS":  "Semiconductor",         # Skyworks — RF semiconductors
    "TXN":   "Semiconductor",         # Texas Instruments — analog/embedded
    "WOLF":  "Semiconductor",         # Wolfspeed — SiC power semis

    # ── Tech Hardware / Equipment ─────────────────────────────────────────────
    "AVT":   "Tech_Hardware",         # Avnet — electronics distributor
    "ARW":   "Tech_Hardware",         # Arrow Electronics — components dist
    "BDC":   "Tech_Hardware",         # Belden — signal transmission cables
    "CDW":   "Tech_Hardware",         # CDW — IT solutions/hardware dist
    "CRUS":  "Tech_Hardware",         # Cirrus Logic — audio chips/hardware
    "FLEX":  "Tech_Hardware",         # Flex Ltd — contract electronics mfr
    "HPQ":   "Tech_Hardware",         # HP Inc — PCs/printers
    "HP":    "Energy_OilGas",         # Helmerich & Payne — oil drilling
    "ITRI":  "Tech_Hardware",         # Itron — smart meters/grid
    "KEYS":  "Tech_Hardware",         # Keysight — test & measurement
    "VIAV":  "Photonics",             # Already labeled
    "ZBRA":  "Tech_Hardware",         # Zebra Technologies — barcode/RFID

    # ── Fintech / Payments ────────────────────────────────────────────────────
    "AFRM":  "Finance_Fintech",       # Affirm — BNPL
    "COIN":  "Finance_Fintech",       # Coinbase — crypto exchange
    "FIS":   "Finance_Fintech",       # FIS — payment/banking tech
    "FISV":  "Finance_Fintech",       # Fiserv — payment processing
    "GPN":   "Finance_Fintech",       # Global Payments — payment tech
    "HOOD":  "Finance_Investment",    # Robinhood — retail brokerage
    "KSPI":  "Finance_Fintech",       # Kaspi.kz — digital bank/ecom
    "MA":    "Finance_Fintech",       # Mastercard — payments network
    "NUAN":  "AI_Related",            # Nuance — AI voice/healthcare AI
    "PAYO":  "Finance_Fintech",       # Payoneer — cross-border payments
    "PYPL":  "Finance_Fintech",       # PayPal — digital payments
    "SQ":    "Finance_Fintech",       # Block (Square) — payments/crypto
    "V":     "Finance_Fintech",       # Visa — payments network
    "WEX":   "Finance_Fintech",       # WEX — fleet/benefits payments

    # ── Finance: Investment / Asset Mgmt ─────────────────────────────────────
    "AB":    "Finance_Investment",    # AllianceBernstein — asset mgmt
    "AMG":   "Finance_Investment",    # Affiliated Managers Group
    "APO":   "Finance_Investment",    # Apollo Global Management — PE
    "ARES":  "Finance_Investment",    # Ares Management — alt asset mgmt
    "BK":    "Finance_Investment",    # Bank of New York Mellon
    "BLK":   "Finance_Investment",    # BlackRock — largest asset manager
    "BX":    "Finance_Investment",    # Blackstone — PE/alt assets
    "CG":    "Finance_Investment",    # Carlyle Group — PE/credit
    "CHW":   "Finance_Investment",    # Calamos fund
    "GS":    "Finance_Investment",    # Goldman Sachs — investment banking
    "HLI":   "Finance_Investment",    # Houlihan Lokey — advisory
    "ICE":   "Finance_Investment",    # Intercontinental Exchange
    "IVZ":   "Finance_Investment",    # Invesco — asset management
    "KKR":   "Finance_Investment",    # KKR — PE/alt assets
    "LAZ":   "Finance_Investment",    # Lazard — advisory/asset mgmt
    "LPL":   "Finance_Investment",    # LPL Financial — broker-dealer
    "MS":    "Finance_Investment",    # Morgan Stanley — investment bank
    "NTRS":  "Finance_Investment",    # Northern Trust — custody/wealth
    "OWL":   "Finance_Investment",    # Blue Owl Capital — alt asset mgmt
    "PJT":   "Finance_Investment",    # PJT Partners — advisory
    "RJF":   "Finance_Investment",    # Raymond James Financial
    "SCHW":  "Finance_Investment",    # Charles Schwab — brokerage
    "SF":    "Finance_Investment",    # Stifel Financial
    "STT":   "Finance_Investment",    # State Street — custody/ETF
    "VOYA":  "Finance_Investment",    # Voya Financial — retirement
    "BPYPM": "Finance_Investment",    # Brookfield Property Partners
    "AGO":   "Finance_Investment",    # Assured Guaranty — bond insurance
    "AGNC":  "Real_Estate",           # AGNC Investment — mortgage REIT

    # ── Finance: Banking ─────────────────────────────────────────────────────
    "BAC":   "Finance_Bank",          # Bank of America
    "C":     "Finance_Bank",          # Citigroup
    "CFG":   "Finance_Bank",          # Citizens Financial Group
    "CMA":   "Finance_Bank",          # Comerica
    "EWBC":  "Finance_Bank",          # East West Bancorp
    "FFIN":  "Finance_Bank",          # First Financial Bankshares
    "FITB":  "Finance_Bank",          # Fifth Third Bancorp
    "FULT":  "Finance_Bank",          # Fulton Financial
    "HBAN":  "Finance_Bank",          # Huntington Bancshares
    "JPM":   "Finance_Bank",          # JPMorgan Chase
    "KEY":   "Finance_Bank",          # KeyCorp
    "MTB":   "Finance_Bank",          # M&T Bank
    "OZK":   "Finance_Bank",          # Bank OZK
    "PNC":   "Finance_Bank",          # PNC Financial Services
    "RF":    "Finance_Bank",          # Regions Financial
    "USB":   "Finance_Bank",          # U.S. Bancorp
    "WFC":   "Finance_Bank",          # Wells Fargo
    "WSFS":  "Finance_Bank",          # WSFS Financial
    "ZION":  "Finance_Bank",          # Zions Bancorporation

    # ── Finance: Insurance ────────────────────────────────────────────────────
    "ACT":   "Finance_Insurance",     # Enact Holdings — mortgage insurance
    "ACGL":  "Finance_Insurance",     # Arch Capital — reinsurance
    "AFL":   "Finance_Insurance",     # Aflac — supplemental insurance
    "AIG":   "Finance_Insurance",     # AIG — diversified insurance
    "AII":   "Finance_Insurance",     # American Integrity Insurance
    "AJG":   "Finance_Insurance",     # Arthur J. Gallagher — broker
    "ALL":   "Finance_Insurance",     # Allstate — P&C insurance
    "ALOG":  "Software",              # Analogic (acquired) → skip
    "CB":    "Finance_Insurance",     # Chubb Limited — P&C insurance
    "CINF":  "Finance_Insurance",     # Cincinnati Financial
    "GL":    "Finance_Insurance",     # Globe Life — life/health insurance
    "HIG":   "Finance_Insurance",     # Hartford Financial Services
    "MKL":   "Finance_Insurance",     # Markel — specialty insurance
    "MMC":   "Finance_Insurance",     # Marsh McLennan — insurance broker
    "MET":   "Finance_Insurance",     # MetLife — life insurance
    "PRU":   "Finance_Insurance",     # Prudential — life/retirement
    "PGR":   "Finance_Insurance",     # Progressive — auto insurance
    "RE":    "Finance_Insurance",     # Everest Re — reinsurance
    "RNR":   "Finance_Insurance",     # RenaissanceRe — reinsurance
    "TRV":   "Finance_Insurance",     # Travelers — P&C insurance
    "WRB":   "Finance_Insurance",     # W.R. Berkley — specialty insur
    "ACIC":  "Finance_Insurance",     # American Coastal Insurance

    # ── Healthcare: Pharma ────────────────────────────────────────────────────
    "ABBV":  "Healthcare_Pharma",     # AbbVie — immunology/oncology
    "ACAD":  "Healthcare_Pharma",     # Acadia Pharmaceuticals
    "AZN":   "Healthcare_Pharma",     # AstraZeneca
    "BMY":   "Healthcare_Pharma",     # Bristol-Myers Squibb
    "HRMY":  "Healthcare_Pharma",     # Harmony Biosciences
    "JNJ":   "Healthcare_Pharma",     # Johnson & Johnson
    "JAZZ":  "Healthcare_Pharma",     # Jazz Pharmaceuticals
    "MRK":   "Healthcare_Pharma",     # Merck
    "NVO":   "Healthcare_Pharma",     # Novo Nordisk — diabetes/obesity
    "PFE":   "Healthcare_Pharma",     # Pfizer
    "PRGO":  "Healthcare_Pharma",     # Perrigo — generic OTC pharma
    "SUPN":  "Healthcare_Pharma",     # Supernus Pharmaceuticals
    "VTRS":  "Healthcare_Pharma",     # Viatris — generic pharma

    # ── Healthcare: Biotech ───────────────────────────────────────────────────
    "ABCL":  "Healthcare_Biotech",    # AbCellera Biologics
    "ALEC":  "Healthcare_Biotech",    # Alector — neurodegeneration
    "ALNY":  "Healthcare_Biotech",    # Alnylam — RNAi therapeutics
    "AMGN":  "Healthcare_Biotech",    # Amgen — large-cap biotech
    "BIIB":  "Healthcare_Biotech",    # Biogen — neuroscience
    "BMRN":  "Healthcare_Biotech",    # BioMarin — rare diseases
    "DXCM":  "Healthcare_MedDevice",  # Dexcom — CGM for diabetes
    "EXAS":  "Healthcare_Biotech",    # Exact Sciences — cancer screening
    "FATE":  "Healthcare_Biotech",    # Fate Therapeutics — cell therapy
    "GH":    "Healthcare_Biotech",    # Guardant Health — liquid biopsy
    "GILD":  "Healthcare_Biotech",    # Gilead Sciences — antivirals/HIV
    "ILMN":  "Healthcare_Biotech",    # Illumina — DNA sequencing
    "INCY":  "Healthcare_Biotech",    # Incyte — oncology/inflammation
    "MRNA":  "Healthcare_Biotech",    # Moderna — mRNA vaccines/therapeutics
    "NKTR":  "Healthcare_Biotech",    # Nektar Therapeutics
    "NVAX":  "Healthcare_Biotech",    # Novavax — vaccines
    "RARE":  "Healthcare_Biotech",    # Ultragenyx — rare diseases
    "REGN":  "Healthcare_Biotech",    # Regeneron — monoclonal antibodies
    "RGEN":  "Healthcare_Biotech",    # Repligen — bioprocess equipment
    "SGEN":  "Healthcare_Biotech",    # Seagen (acquired by Pfizer)
    "VRTX":  "Healthcare_Biotech",    # Vertex — CF/rare diseases
    "BNTX":  "Healthcare_Biotech",    # BioNTech — mRNA cancer vaccines

    # ── Healthcare: Medical Devices ───────────────────────────────────────────
    "A":     "Tech_Hardware",         # Agilent Technologies — lab instruments
    "ABT":   "Healthcare_MedDevice",  # Abbott — diagnostics/devices
    "ALGN":  "Healthcare_MedDevice",  # Align Technology — clear aligners
    "BAX":   "Healthcare_MedDevice",  # Baxter — IV/renal care
    "BDX":   "Healthcare_MedDevice",  # Becton Dickinson — medical supplies
    "BSX":   "Healthcare_MedDevice",  # Boston Scientific — cardiac/endo
    "EW":    "Healthcare_MedDevice",  # Edwards Lifesciences — heart valves
    "HOLX":  "Healthcare_MedDevice",  # Hologic — women's health devices
    "IRTC":  "Healthcare_MedDevice",  # iRhythm — cardiac monitoring
    "MDT":   "Healthcare_MedDevice",  # Medtronic — largest medical device
    "NVCR":  "Healthcare_MedDevice",  # NovoCure — tumor treating fields
    "PODD":  "Healthcare_MedDevice",  # Insulet — OmniPod insulin pump
    "RMD":   "Healthcare_MedDevice",  # ResMed — sleep/respiratory care
    "SYK":   "Healthcare_MedDevice",  # Stryker — orthopedics/instruments
    "TNDM":  "Healthcare_MedDevice",  # Tandem Diabetes Care — t:slim pump
    "TFX":   "Healthcare_MedDevice",  # Teleflex — vascular/anesthesia
    "TMO":   "Healthcare_MedDevice",  # Thermo Fisher — lab equipment/reagents
    "ZBH":   "Healthcare_MedDevice",  # Zimmer Biomet — orthopedics

    # ── Healthcare: Services ──────────────────────────────────────────────────
    "ABC":   "Healthcare_Services",   # AmerisourceBergen — drug distribution
    "ADUS":  "Healthcare_Services",   # Addus HomeCare
    "AFHC":  "Healthcare_Services",   # ??? → skip
    "AHCO":  "Healthcare_Services",   # AdaptHealth — home health equipment
    "CAH":   "Healthcare_Services",   # Cardinal Health — drug distribution
    "CI":    "Healthcare_Services",   # Cigna — health insurance/PBM
    "CNC":   "Healthcare_Services",   # Centene — Medicaid managed care
    "CVS":   "Healthcare_Services",   # CVS Health — pharmacy/insurance
    "DVA":   "Healthcare_Services",   # DaVita — kidney dialysis
    "ELV":   "Healthcare_Services",   # Elevance Health — health insurance
    "HCA":   "Healthcare_Services",   # HCA Healthcare — hospitals
    "HUM":   "Healthcare_Services",   # Humana — Medicare/health plans
    "MCK":   "Healthcare_Services",   # McKesson — healthcare distribution
    "MOH":   "Healthcare_Services",   # Molina Healthcare — Medicaid
    "UNH":   "Healthcare_Services",   # UnitedHealth Group — largest insurer
    "UHS":   "Healthcare_Services",   # Universal Health Services — hospitals

    # ── Energy: Oil & Gas ────────────────────────────────────────────────────
    "APA":   "Energy_OilGas",         # APA Corp — E&P
    "AR":    "Energy_OilGas",         # Antero Resources — nat gas E&P
    "BORR":  "Energy_OilGas",         # Borr Drilling — offshore drilling
    "CNQ":   "Energy_OilGas",         # Canadian Natural Resources
    "COP":   "Energy_OilGas",         # ConocoPhillips — E&P
    "CTRA":  "Energy_OilGas",         # Coterra Energy — gas/oil E&P
    "CVX":   "Energy_OilGas",         # Chevron — integrated oil major
    "DK":    "Energy_OilGas",         # Delek Group — refining
    "DVN":   "Energy_OilGas",         # Devon Energy — E&P
    "EOG":   "Energy_OilGas",         # EOG Resources — shale E&P
    "EQT":   "Energy_OilGas",         # EQT Corporation — nat gas E&P
    "FANG":  "Energy_OilGas",         # Diamondback Energy — Permian E&P
    "FLNG":  "Energy_OilGas",         # Flex LNG — LNG shipping
    "HAL":   "Energy_OilGas",         # Halliburton — oilfield services
    "HES":   "Energy_OilGas",         # Hess — E&P
    "MPC":   "Energy_OilGas",         # Marathon Petroleum — refining
    "MRO":   "Energy_OilGas",         # Marathon Oil — E&P
    "MTDR":  "Energy_OilGas",         # Matador Resources — Permian E&P
    "OXY":   "Energy_OilGas",         # Occidental Petroleum — E&P/chems
    "PBF":   "Energy_OilGas",         # PBF Energy — refining
    "PSX":   "Energy_OilGas",         # Phillips 66 — refining/chem
    "PXD":   "Energy_OilGas",         # Pioneer Natural Resources — E&P
    "RRC":   "Energy_OilGas",         # Range Resources — nat gas E&P
    "SLB":   "Energy_OilGas",         # SLB (Schlumberger) — oilfield svcs
    "SM":    "Energy_OilGas",         # SM Energy — E&P
    "SU":    "Energy_OilGas",         # Suncor Energy — oil sands
    "VLO":   "Energy_OilGas",         # Valero Energy — refining
    "XOM":   "Energy_OilGas",         # ExxonMobil — integrated oil major

    # ── Energy: Renewable ─────────────────────────────────────────────────────
    "ADSE":  "Energy_Renewable",      # ADS-TEC — EV charging
    "ADUR":  "Energy_Renewable",      # Aduro Clean Technologies
    "ARRY":  "Energy_Renewable",      # Array Technologies — solar trackers
    "BEP":   "Energy_Renewable",      # Brookfield Renewable Partners
    "CSIQ":  "Energy_Renewable",      # Canadian Solar — solar panels
    "ENPH":  "Energy_Renewable",      # Enphase — solar microinverters
    "FSLR":  "Energy_Renewable",      # First Solar — utility solar panels
    "HASI":  "Energy_Renewable",      # Hannon Armstrong — clean energy
    "JKS":   "Energy_Renewable",      # JinkoSolar — solar panels
    "NEP":   "Energy_Renewable",      # NextEra Energy Partners — renewables
    "NOVA":  "Energy_Renewable",      # Sunnova — residential solar
    "SEDG":  "Energy_Renewable",      # SolarEdge — solar inverters
    "SHLS":  "Energy_Renewable",      # Shoals Technologies — solar BOS
    "SPWR":  "Energy_Renewable",      # SunPower — residential solar

    # ── Materials & Mining ────────────────────────────────────────────────────
    "AA":    "Materials",             # Alcoa — aluminum
    "AEM":   "Materials",             # Agnico Eagle — gold mining
    "AG":    "Materials",             # First Majestic Silver
    "AGI":   "Materials",             # Alamos Gold
    "ALB":   "Materials",             # Albemarle — lithium/specialty chems
    "ASTL":  "Materials",             # Algoma Steel — steel
    "AAUC":  "Materials",             # Allied Gold
    "AYA":   "Materials",             # Aya Gold & Silver
    "DD":    "Materials",             # DuPont — specialty materials
    "DOW":   "Materials",             # Dow Inc — chemicals/materials
    "ECL":   "Materials",             # Ecolab — water/hygiene chemicals
    "EMN":   "Materials",             # Eastman Chemical
    "FCX":   "Materials",             # Freeport-McMoRan — copper/gold
    "GOLD":  "Materials",             # Barrick Gold
    "IFF":   "Materials",             # IFF — flavors/fragrances/materials
    "LAC":   "Materials",             # Lithium Americas
    "LIN":   "Materials",             # Linde — industrial gases
    "LTHM":  "Materials",             # Livent — lithium compounds
    "NEM":   "Materials",             # Newmont — gold mining
    "NTR":   "Materials",             # Nutrien — fertilizers
    "PPG":   "Materials",             # PPG Industries — coatings/paints
    "SHW":   "Materials",             # Sherwin-Williams — paints/coatings
    "SGML":  "Materials",             # Sigma Lithium
    "ALM":   "Materials",             # Almonty Industries — tungsten
    "ALOY":  "Materials",             # REalloys — rare earth alloys
    "FCX":   "Materials",             # Already have
    "ASC":   "Transport",             # Ardmore Shipping (override to transport)

    # ── Industrials & Engineering ─────────────────────────────────────────────
    "ABM":   "Industrials",           # ABM Industries — facility services
    "ACA":   "Industrials_Construction", # Arcosa — infrastructure products
    "ACM":   "Industrials_Construction", # AECOM — engineering/construction
    "AER":   "Transport",             # AerCap — aircraft leasing
    "AGM":   "Finance_Investment",    # Federal Ag Mortgage (Farmer Mac)
    "AIR":   "Industrials",           # AAR Corp — aircraft MRO
    "AME":   "Industrials",           # Ametek — electronic instruments
    "BIP":   "Industrials",           # Brookfield Infrastructure
    "CTAS":  "Industrials",           # Cintas — workwear/uniforms
    "CMI":   "Industrials",           # Cummins — diesel/gas engines
    "CPRT":  "Transport",             # Copart — online auto auctions
    "CW":    "Industrials",           # Curtiss-Wright — defense/industrial
    "EFX":   "Software",              # Equifax — data analytics/HR
    "GE":    "Industrials",           # GE Aerospace — jet engines
    "GEV":   "Industrials",           # GE Vernova — power/energy
    "ITW":   "Industrials",           # Illinois Tool Works — diversified
    "IEX":   "Industrials",           # IDEX — flow control/pumps
    "J":     "Industrials_Construction", # Jacobs Solutions — engineering
    "MMM":   "Industrials",           # 3M — diversified industrial
    "MTZ":   "Industrials_Construction", # MYR Group / MasTec — construction
    "PH":    "Industrials",           # Parker Hannifin — motion/control
    "PRIM":  "Industrials_Construction", # Primoris — engineering/construction
    "STRL":  "Industrials_Construction", # Sterling Infrastructure
    "VRSK":  "Software",              # Verisk — data analytics for insur
    "WSP":   "Industrials_Construction", # WSP Global — engineering consulting

    # ── Consumer: Retail / E-commerce ────────────────────────────────────────
    "AAP":   "Consumer_Retail",       # Advance Auto Parts
    "ACCO":  "Consumer_Retail",       # Acco Brands — office products
    "ACI":   "Consumer_Retail",       # Albertsons — grocery chain
    "ACVA":  "Consumer_Auto",         # ACV Auctions — auto marketplace
    "AN":    "Consumer_Auto",         # AutoNation — auto dealerships
    "ABG":   "Consumer_Auto",         # Asbury Automotive — auto dealers
    "BURL":  "Consumer_Retail",       # Burlington — off-price retail
    "CDK":   "Software",              # CDK Global — auto dealer software
    "CROX":  "Consumer_Retail",       # Crocs — footwear brand
    "CVNA":  "Consumer_Auto",         # Carvana — online auto retail
    "DKS":   "Consumer_Retail",       # Dick's Sporting Goods
    "DKNG":  "Consumer_Media",        # DraftKings — online sports betting
    "GOOS":  "Consumer_Retail",       # Canada Goose — apparel
    "GPS":   "Consumer_Retail",       # Gap Inc — apparel retail
    "HD":    "Consumer_Retail",       # Home Depot
    "HBI":   "Consumer_Retail",       # Hanesbrands — innerwear
    "KMX":   "Consumer_Auto",         # CarMax — used auto retail
    "KR":    "Consumer_Retail",       # Kroger — grocery chain
    "LAD":   "Consumer_Auto",         # Lithia Motors — auto dealers
    "LEVI":  "Consumer_Retail",       # Levi Strauss — denim/apparel
    "LOW":   "Consumer_Retail",       # Lowe's — home improvement
    "LULU":  "Consumer_Retail",       # Lululemon — activewear
    "NKE":   "Consumer_Retail",       # Nike — footwear/apparel
    "PAG":   "Consumer_Auto",         # Penske Automotive — auto dealers
    "PVH":   "Consumer_Retail",       # PVH Corp — Calvin Klein/Tommy H
    "RL":    "Consumer_Retail",       # Ralph Lauren
    "ROST":  "Consumer_Retail",       # Ross Stores — off-price retail
    "SFM":   "Consumer_Retail",       # Sprouts Farmers Market
    "SHAK":  "Consumer_Restaurant",   # Shake Shack
    "SKX":   "Consumer_Retail",       # Skechers — footwear
    "TGT":   "Consumer_Retail",       # Target
    "TJX":   "Consumer_Retail",       # TJX Companies — off-price retail
    "UA":    "Consumer_Retail",       # Under Armour
    "URBN":  "Consumer_Retail",       # Urban Outfitters
    "VFC":   "Consumer_Retail",       # VF Corporation — Timberland/Vans
    "WMT":   "Consumer_Retail",       # Walmart
    "WWW":   "Consumer_Retail",       # Wolverine World Wide — footwear

    # ── Consumer: Restaurant / Food Service ──────────────────────────────────
    "BJRI":  "Consumer_Restaurant",   # BJ's Restaurants
    "BLMN":  "Consumer_Restaurant",   # Bloomin' Brands — Outback
    "CAKE":  "Consumer_Restaurant",   # Cheesecake Factory
    "CMG":   "Consumer_Restaurant",   # Chipotle
    "DPZ":   "Consumer_Restaurant",   # Domino's Pizza
    "DINE":  "Consumer_Restaurant",   # Dine Brands — IHOP/Applebee's
    "EAT":   "Consumer_Restaurant",   # Brinker — Chili's
    "JACK":  "Consumer_Restaurant",   # Jack in the Box
    "MCD":   "Consumer_Restaurant",   # McDonald's
    "PLNT":  "Consumer_Restaurant",   # Planet Fitness (actually gym/leisure)
    "QSR":   "Consumer_Restaurant",   # Restaurant Brands — BK/Tim Hortons
    "RUTH":  "Consumer_Restaurant",   # Ruth's Chris Steak House
    "SBUX":  "Consumer_Restaurant",   # Starbucks
    "TXRH":  "Consumer_Restaurant",   # Texas Roadhouse
    "WEN":   "Consumer_Restaurant",   # Wendy's
    "YUM":   "Consumer_Restaurant",   # Yum! Brands — KFC/Pizza Hut/Taco Bell

    # ── Consumer: Food & Beverage ─────────────────────────────────────────────
    "ABVE":  "Consumer_Food",         # Above Food Ingredients
    "AGCO":  "Industrials",           # AGCO — farm equipment (not food)
    "AGRO":  "Consumer_Food",         # Adecoagro — agricultural
    "CAG":   "Consumer_Food",         # Conagra Brands
    "CL":    "Consumer_Staples",      # Colgate-Palmolive — oral care/soap
    "CLX":   "Consumer_Staples",      # Clorox — cleaning products
    "CPB":   "Consumer_Food",         # Campbell Soup
    "EL":    "Consumer_Staples",      # Estee Lauder — cosmetics
    "GIS":   "Consumer_Food",         # General Mills
    "HAIN":  "Consumer_Food",         # Hain Celestial — organic food
    "HRL":   "Consumer_Food",         # Hormel Foods
    "HSY":   "Consumer_Food",         # Hershey — chocolate/snacks
    "K":     "Consumer_Food",         # Kellogg's
    "KHC":   "Consumer_Food",         # Kraft Heinz
    "KO":    "Consumer_Food",         # Coca-Cola
    "LANC":  "Consumer_Food",         # Lancaster Colony — dressings/sauces
    "MDLZ":  "Consumer_Food",         # Mondelez — snacks/cookies
    "MKC":   "Consumer_Food",         # McCormick — spices/seasonings
    "MNST":  "Consumer_Food",         # Monster Beverage — energy drinks
    "PEP":   "Consumer_Food",         # PepsiCo — beverages/snacks
    "PG":    "Consumer_Staples",      # Procter & Gamble — consumer goods
    "POST":  "Consumer_Food",         # Post Holdings — cereals
    "SJM":   "Consumer_Food",         # J.M. Smucker — jams/Folgers
    "SMPL":  "Consumer_Food",         # Simply Good Foods

    # ── Consumer: Travel & Hospitality ───────────────────────────────────────
    "AAL":   "Transport",             # American Airlines — airline
    "ABNB":  "Consumer_Travel",       # Airbnb — vacation rental platform
    "ACEL":  "Consumer_Travel",       # Accel Entertainment — gaming machines
    "AHT":   "Real_Estate",           # Ashford Hospitality Trust — hotel REIT
    "ALGT":  "Transport",             # Allegiant Air — airline
    "ALK":   "Transport",             # Alaska Air Group — airline
    "CCL":   "Consumer_Travel",       # Carnival Corp — cruise lines
    "DAL":   "Transport",             # Delta Air Lines
    "H":     "Consumer_Travel",       # Hyatt Hotels
    "HLT":   "Consumer_Travel",       # Hilton Worldwide
    "IHG":   "Consumer_Travel",       # IHG Hotels & Resorts
    "JBLU":  "Transport",             # JetBlue Airways
    "LVS":   "Consumer_Travel",       # Las Vegas Sands — casino resort
    "MAR":   "Consumer_Travel",       # Marriott International
    "MGM":   "Consumer_Travel",       # MGM Resorts — casino/hotel
    "MLCO":  "Consumer_Travel",       # Melco Resorts — Macau casino
    "NCLH":  "Consumer_Travel",       # Norwegian Cruise Line
    "RCL":   "Consumer_Travel",       # Royal Caribbean
    "UAL":   "Transport",             # United Airlines
    "WYNN":  "Consumer_Travel",       # Wynn Resorts — luxury casino

    # ── Consumer: Media & Entertainment ──────────────────────────────────────
    "DIS":   "Consumer_Media",        # Walt Disney — entertainment/streaming
    "FOX":   "Consumer_Media",        # Fox Corp — TV/news
    "FOXA":  "Consumer_Media",        # Fox Corp Class A
    "NFLX":  "Consumer_Media",        # Netflix — streaming
    "PARA":  "Consumer_Media",        # Paramount Global — media
    "SE":    "Consumer_Media",        # Sea Limited — gaming/ecom
    "SIRI":  "Consumer_Media",        # Sirius XM — satellite radio
    "SPOT":  "Consumer_Media",        # Spotify — music streaming
    "WBD":   "Consumer_Media",        # Warner Bros. Discovery
    "TTWO":  "Consumer_Media",        # Take-Two Interactive — video games
    "EA":    "Consumer_Media",        # Electronic Arts — video games
    "IMAX":  "Consumer_Media",        # IMAX Corp — cinema technology
    "CNK":   "Consumer_Media",        # Cinemark — movie theaters
    "AMC":   "Consumer_Media",        # AMC Entertainment — theaters
    "CHTR":  "Consumer_Media",        # Charter Communications (also Connectivity but primarily cable)
    "REZI":  "Tech_Hardware",         # Resideo — home comfort tech

    # ── Real Estate ───────────────────────────────────────────────────────────
    "ABR":   "Real_Estate",           # Arbor Realty Trust — multifamily REIT
    "ACR":   "Real_Estate",           # ACRES Commercial Realty
    "ACRE":  "Real_Estate",           # Ares Commercial Real Estate
    "ADC":   "Real_Estate",           # Agree Realty — net lease REIT
    "AHR":   "Real_Estate",           # American Healthcare REIT
    "AIV":   "Real_Estate",           # Apartment Investment & Mgmt
    "AKR":   "Real_Estate",           # Acadia Realty Trust
    "ARE":   "Real_Estate",           # Alexandria Real Estate — lab REIT
    "BXP":   "Real_Estate",           # Boston Properties — office REIT
    "EXR":   "Real_Estate",           # Extra Space Storage REIT
    "KIM":   "Real_Estate",           # Kimco Realty — shopping center REIT
    "NNN":   "Real_Estate",           # National Retail Properties — net lease
    "O":     "Real_Estate",           # Realty Income — monthly dividend REIT
    "PEAK":  "Real_Estate",           # Healthpeak — life science REIT
    "PSA":   "Real_Estate",           # Public Storage — self-storage REIT
    "REG":   "Real_Estate",           # Regency Centers — shopping REIT
    "SPG":   "Real_Estate",           # Simon Property — mall REIT
    "VICI":  "Real_Estate",           # VICI Properties — gaming REIT
    "VNO":   "Real_Estate",           # Vornado Realty — office/retail REIT

    # ── Transport & Logistics ─────────────────────────────────────────────────
    "CHRW":  "Transport",             # C.H. Robinson — freight brokerage
    "CSX":   "Transport",             # CSX — railroad
    "EXPD":  "Transport",             # Expeditors — freight forwarding
    "FDX":   "Transport",             # FedEx — express delivery
    "JBHT":  "Transport",             # J.B. Hunt — trucking/logistics
    "MATX":  "Transport",             # Matson — ocean shipping
    "ODFL":  "Transport",             # Old Dominion Freight — LTL trucking
    "NSC":   "Transport",             # Norfolk Southern — railroad
    "SAIA":  "Transport",             # Saia Inc — LTL trucking
    "UNP":   "Transport",             # Union Pacific — railroad
    "UPS":   "Transport",             # UPS — parcel delivery
    "XPO":   "Transport",             # XPO — logistics/trucking
    "CP":    "Transport",             # Canadian Pacific Kansas City — railroad
    "CNI":   "Transport",             # Canadian National Railway

    # ── Utilities ─────────────────────────────────────────────────────────────
    "AEE":   "Utilities",             # Ameren — electric/gas utility
    "AEP":   "Utilities",             # American Electric Power
    "AWK":   "Utilities",             # American Water Works
    "CMS":   "Utilities",             # CMS Energy — Michigan utility
    "D":     "Utilities",             # Dominion Energy
    "ED":    "Utilities",             # Consolidated Edison
    "ES":    "Utilities",             # Eversource Energy
    "ETR":   "Utilities",             # Entergy — nuclear utility
    "EVRG":  "Utilities",             # Evergy — Midwest utility
    "EXC":   "Utilities",             # Exelon — electric utility/nuclear
    "LNT":   "Utilities",             # Alliant Energy
    "NI":    "Utilities",             # NiSource — gas utility
    "OGE":   "Utilities",             # OGE Energy — Oklahoma utility
    "PEG":   "Utilities",             # PSEG — NJ utility
    "PPL":   "Utilities",             # PPL Corp — PA/UK utility
    "SRE":   "Utilities",             # Sempra Energy — CA utility
    "WEC":   "Utilities",             # WEC Energy — Midwest utility

    # ── ETF / Funds ───────────────────────────────────────────────────────────
    "ARKG":  "ETF_Fund",              # ARK Genomics ETF
    "ARKK":  "ETF_Fund",              # ARK Innovation ETF
    "GLD":   "ETF_Fund",              # SPDR Gold ETF
    "IWM":   "ETF_Fund",              # iShares Russell 2000 ETF
    "QQQ":   "ETF_Fund",              # Invesco QQQ Trust
    "SLV":   "ETF_Fund",              # iShares Silver Trust
    "SOXL":  "ETF_Fund",              # Direxion Semis Bull 3x ETF
    "SPY":   "ETF_Fund",              # SPDR S&P 500 ETF
    "TQQQ":  "ETF_Fund",              # ProShares UltraPro QQQ
    "XLK":   "ETF_Fund",              # SPDR Tech sector ETF
    "AGNCO": "Real_Estate",           # AGNC preferred stock
    "ACGLN": "Finance_Insurance",     # Arch Capital preferred

    # ── Final 68 'Other' tickers — manually resolved ──────────────────────────
    # Theme stocks hiding in Other
    "EU":    "Nuclear_SMR",           # enCore Energy — uranium E&P
    "UROY":  "Nuclear_SMR",           # Uranium Royalty Corp — uranium royalties
    "LTBR":  "Nuclear_SMR",           # Lightbridge Corp — nuclear fuel tech
    "ARBE":  "Robotics",              # Arbe Robotics — radar for autonomous vehicles
    "AMCI":  "Robotics",              # AMC Robotics — robotics/automation
    "MVIS":  "Photonics",             # MicroVision — LiDAR/photonic sensing
    "ASTC":  "Space",                 # Astrotech Corp — space tech/payload processing
    "BAER":  "Drone_UAV",             # Bridger Aerospace — aerial firefighting UAV
    "AMPX":  "Energy_Renewable",      # Amprius Technologies — silicon anode batteries
    "AMTM":  "DefenseTech",           # Amentum Holdings — defense/govt services

    # Energy
    "AMPY":  "Energy_OilGas",         # Amplify Energy Corp — offshore oil & gas
    "AMTX":  "Energy_Renewable",      # Aemetis — biofuels/sustainable aviation fuel
    "BTU":   "Energy_OilGas",         # Peabody Energy — coal mining
    "BEEM":  "Energy_Renewable",      # Beam Global — solar EV charging
    "BNRG":  "Energy_Renewable",      # Brenmiller Energy — thermal energy storage
    "HDSN":  "Industrials",           # Hudson Technologies — refrigerant reclaim
    "EPOW":  "Energy_Renewable",      # E-Power Inc — EV/renewable power (China)
    "AQMS":  "Materials",             # Aqua Metals — lead/li battery recycling

    # Materials / Industrials
    "AMCR":  "Materials",             # Amcor — flexible/rigid packaging
    "AREC":  "Materials",             # American Resources Corp — critical materials/rare earth
    "ATR":   "Industrials",           # AptarGroup — dispensing/packaging solutions
    "AVY":   "Materials",             # Avery Dennison — labels/packaging materials
    "BALL":  "Materials",             # Ball Corporation — aluminum beverage cans
    "BCC":   "Materials",             # Boise Cascade — wood products/distribution
    "BRC":   "Industrials",           # Brady Corporation — industrial ID/labels
    "BOOM":  "Industrials",           # DMC Global — industrial products/perforating
    "IP":    "Materials",             # International Paper — paper/packaging
    "APWC":  "Industrials",           # Asia Pacific Wire & Cable — cables/energy
    "ALTG":  "Industrials",           # Alta Equipment Group — heavy equipment rental
    "BCO":   "Industrials",           # Brink's Company — cash management/security
    "CWST":  "Industrials",           # Casella Waste Systems — waste management
    "EXPO":  "Industrials",           # Exponent Inc — engineering/scientific consulting
    "BGSF":  "Industrials",           # BGSF Inc — staffing services
    "LUNA":  "Tech_Hardware",         # Luna Innovations — fiber optic/sensing measurement
    "BMI":   "Tech_Hardware",         # Badger Meter — flow measurement/IoT water

    # Consumer Auto
    "APTV":  "Consumer_Auto",         # Aptiv — advanced safety/autonomous systems
    "MGA":   "Consumer_Auto",         # Magna International — auto components
    "GPC":   "Consumer_Auto",         # Genuine Parts — auto/industrial parts distribution
    "BGSI":  "Industrials",           # Boyd Group Services — auto collision repair

    # Consumer Retail/Food
    "AOUT":  "Consumer_Retail",       # American Outdoor Brands — outdoor/rec products
    "AVO":   "Consumer_Food",         # Mission Produce — fresh avocados/produce
    "AFRI":  "Consumer_Food",         # Forafric Global — grain/flour milling Morocco
    "AGCC":  "Consumer_Food",         # Agencia Comercial Spirits — spirits distribution
    "BGS":   "Consumer_Food",         # B&G Foods — shelf-stable specialty foods
    "BIYA":  "Consumer_Food",         # Baiya International — Chinese food group
    "BOF":   "Consumer_Food",         # BranchOut Food — plant-based snacks
    "BRLS":  "Consumer_Food",         # Borealis Foods — protein fortified food
    "FAMI":  "Consumer_Food",         # Farmmi — Chinese organic mushrooms/produce
    "BRFH":  "Consumer_Food",         # Barfresh Food Group — frozen beverages

    # Healthcare
    "AVTR":  "Healthcare_MedDevice",  # Avantor — lab supplies/bioscience materials
    "BGLC":  "Healthcare_Biotech",    # BioNexus Gene Lab — genomics/biotech
    "BIO":   "Healthcare_MedDevice",  # Bio-Rad Laboratories — lab instruments
    "BLFS":  "Healthcare_Biotech",    # BioLife Solutions — biopreservation/cell therapy
    "CRL":   "Healthcare_Biotech",    # Charles River Labs — CRO/preclinical research
    "IQV":   "Healthcare_Services",   # IQVIA Holdings — clinical research/data analytics
    "MTD":   "Healthcare_MedDevice",  # Mettler-Toledo — precision laboratory instruments
    "AFJK":  "Healthcare_Services",   # Aimei Health Technology — medical services (China)
    "SITE":  "Industrials_Construction", # SiteOne Landscape — landscaping/hardscape supply

    # Software / Tech
    "BR":    "Software",              # Broadridge Financial — investor comms/fintech SaaS
    "ARLO":  "Tech_Hardware",         # Arlo Technologies — smart home cameras
    "AYI":   "Software",              # Acuity Inc — visual analytics/AI

    # Real Estate
    "BPRE":  "Real_Estate",           # Bluerock Private Real Estate Fund
    "BEEP":  "Real_Estate",           # Mobile Infrastructure Corp — cell tower/REIT

    # Finance
    "BANL":  "Transport",             # CBL International — maritime fuel supply
    "ATLN":  "Finance_Investment",    # Atlantic International Corp — SPAC/investment
    "ANPA":  "Finance_Investment",    # Rich Sparkle Holdings — SPAC

    # Misc
    "AVX":   "Tech_Hardware",         # Avax One Technology — tech hardware
    "BNBX":  "Finance_Fintech",       # BNB Plus Corp — crypto/fintech
}

# Themes that should NOT be overridden by sector labels
THEME_LABELS = {
    "AI_Related", "Memory_HBM", "Space", "Quantum", "Photonics",
    "DefenseTech", "DataCenter", "Nuclear_SMR", "NeoCloud",
    "AI_Infra", "DataCenter_Infra", "Drone_UAV", "Robotics", "Connectivity",
}


# ══════════════════════════════════════════════════════════════════════════════
# Classification engine
# ══════════════════════════════════════════════════════════════════════════════

def _in_text(keyword: str, text: str) -> bool:
    """Safe substring match — keyword must be at word boundary for short terms."""
    if len(keyword) <= 4:
        return bool(re.search(r'\b' + re.escape(keyword) + r'\b', text))
    return keyword in text


def classify_sector(ticker: str, name: str, description: str, sic: str,
                    current_label: str | None,
                    current_source: str | None = None) -> tuple[str, str]:
    """
    Returns (label, source) where source is:
      kept / override / theme_desc / theme_name / desc / sic4 / sic2 / name / other

    current_source: if "finnhub" and label is not Other → preserve without re-querying
    """
    # 0a. Preserve Finnhub-fetched labels (API cost already paid, labels are accurate)
    if (current_label and current_label not in ("Other", None)
            and current_source in ("finnhub",)):
        return current_label, "kept"

    # 0b. Already has a theme label → keep (unless it's a sector label)
    if current_label and current_label in THEME_LABELS:
        return current_label, "kept"

    # 1. Manual override (high confidence)
    if ticker in KNOWN_OVERRIDES:
        return KNOWN_OVERRIDES[ticker], "override"

    text_desc = (f"{name} {description}").lower()
    text_name = name.lower()
    sic4      = str(sic).strip().zfill(4)[:4] if sic else ""
    sic2      = sic4[:2] if sic4 else ""

    # 2. Description keyword → theme (catch thematic stocks with descriptions)
    if description:
        for theme_id, keywords in THEME_SIGNALS:
            if any(_in_text(kw, text_desc) for kw in keywords):
                return theme_id, "theme_desc"

    # 3. Description keyword → sector
    if description:
        for sector, keywords in SECTOR_DESC_SIGNALS:
            if any(_in_text(kw, text_desc) for kw in keywords):
                return sector, "desc"

    # 4. SIC 4-digit exact match
    if sic4 and sic4 in SIC4_MAP:
        return SIC4_MAP[sic4], "sic4"

    # 5. SIC 2-digit prefix fallback
    if sic2 and sic2 in SIC2_MAP:
        return SIC2_MAP[sic2], "sic2"

    # 6. Company name pattern matching
    for sector, patterns in NAME_PATTERNS:
        for pat in patterns:
            if re.search(pat, text_name, re.IGNORECASE):
                return sector, "name"

    return "Other", "other"


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def run(force: bool = False, dry_run: bool = False) -> None:
    today = date.today().isoformat()

    # Load universe
    rs_data  = json.loads(RS_LATEST.read_text(encoding="utf-8"))
    universe = [r["ticker"] for r in rs_data.get("ranked", [])]

    # Load existing labels
    label_data = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    labels  = label_data["labels"]
    sources = label_data.get("sources", {})

    print(f"\n{'='*65}")
    print(f"  Sector Labeler — {today}")
    print(f"{'='*65}")
    print(f"  Universe:     {len(universe)} tickers")
    print(f"  Pre-labeled:  {len(labels)} tickers")
    to_process = [t for t in universe if t not in labels or force]
    print(f"  To classify:  {len(to_process)} tickers")
    print()

    # Preload ALL company data from SQLite (primary source — has desc + SIC)
    db_info: dict[str, dict] = {}
    if DB_PATH.exists():
        _conn = sqlite3.connect(str(DB_PATH))
        _cur  = _conn.cursor()
        _cur.execute("SELECT ticker, name, description, sic_code FROM company_info")
        for _t, _n, _d, _s in _cur.fetchall():
            db_info[_t] = {
                "name":        _n or "",
                "description": _d or "",
                "sic":         str(_s or ""),
            }
        _conn.close()
        print(f"  SQLite loaded: {len(db_info)} company records")

    # Results tracking
    by_method: Counter = Counter()
    by_label:  Counter = Counter()
    new_labels:  dict[str, str] = {}
    new_sources: dict[str, str] = {}
    errors = []

    for i, ticker in enumerate(to_process, 1):
        # Primary: SQLite (has descriptions + SIC codes)
        info = db_info.get(ticker, {})
        name        = info.get("name", "")
        description = info.get("description", "") or ""
        sic         = info.get("sic", "") or ""

        # Fallback: file cache (has names only from Polygon batch)
        if not name:
            p = CACHE_DIR / f"{ticker}.json"
            if p.exists():
                try:
                    cache = json.loads(p.read_text(encoding="utf-8"))
                    name        = cache.get("name", "") or ""
                    description = description or cache.get("description", "") or ""
                    sic         = sic or str(cache.get("sic_code", "") or "")
                except Exception:
                    pass

        current        = labels.get(ticker)
        current_source = sources.get(ticker)
        label, source  = classify_sector(ticker, name, description, sic,
                                         current, current_source)

        new_labels[ticker]  = label
        new_sources[ticker] = source
        by_method[source] += 1
        by_label[label]  += 1

        if i % 200 == 0:
            print(f"  [{i}/{len(to_process)}] processed...")

    # Summary
    print(f"  Classification complete:")
    print(f"  {'Method':12}  {'Count':>5}")
    for method, cnt in sorted(by_method.items(), key=lambda x: -x[1]):
        print(f"  {method:12}  {cnt:>5}")

    other_count = by_label.get("Other", 0)
    total = len(to_process)
    classified = total - other_count
    pct_ok = (100 * classified / total) if total > 0 else 100.0
    pct_other = (100 * other_count / total) if total > 0 else 0.0
    print(f"\n  Classified: {classified}/{total}  ({pct_ok:.1f}%)")
    print(f"  Other:      {other_count}/{total}  ({pct_other:.1f}%)")

    if dry_run:
        print(f"\n  [dry-run] No files written.")
        # Show label distribution
        print(f"\n  Label distribution:")
        for lbl, cnt in sorted(by_label.items(), key=lambda x: -x[1]):
            print(f"    {lbl:30} {cnt:4}")
        return

    # Write back
    labels.update(new_labels)
    sources.update(new_sources)

    # Update meta
    auto_sources = ("keyword", "sic", "theme_desc", "theme_name",
                    "desc", "sic4", "sic2", "name", "other", "override")
    auto_count   = sum(1 for s in sources.values() if s in auto_sources)
    manual_count = sum(1 for s in sources.values() if s == "manual")

    label_data["labels"]  = labels
    label_data["sources"] = sources
    label_data["_meta"]["total_tickers"]   = len(labels)
    label_data["_meta"]["auto_classified"] = auto_count
    label_data["_meta"]["manual_entries"]  = manual_count
    label_data["_meta"]["last_updated"]    = today
    label_data["_meta"]["note"]            = (
        "source: manual|kept|override|theme_desc|desc|sic4|sic2|name|other"
    )

    LABELS_FILE.write_text(
        json.dumps(label_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n  Saved ticker_labels.json — {len(labels)} total labels")

    # Sync to SQLite
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS company_info (
                ticker TEXT PRIMARY KEY, name TEXT, description TEXT,
                sic_code TEXT, sector TEXT, industry TEXT,
                theme TEXT, theme_source TEXT, last_updated TEXT
            )
        """)
        rows = [
            (t, new_labels[t], new_sources[t], today)
            for t in new_labels
        ]
        conn.executemany("""
            INSERT INTO company_info (ticker, theme, theme_source, last_updated)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                theme        = excluded.theme,
                theme_source = excluded.theme_source,
                last_updated = excluded.last_updated
        """, rows)
        conn.commit()
        conn.close()
        print(f"  SQLite synced: {len(rows)} rows updated")

    # Label distribution
    all_labels = Counter(labels.values())
    print(f"\n{'='*65}")
    print(f"  FULL LABEL DISTRIBUTION ({len(labels)} tickers)")
    print(f"{'='*65}")

    # Show themes first
    theme_lbs = {k: v for k, v in all_labels.items() if k in THEME_LABELS}
    sector_lbs = {k: v for k, v in all_labels.items() if k not in THEME_LABELS}

    print(f"\n  == AlphaAbsolute Themes (14) ==")
    for lbl, cnt in sorted(theme_lbs.items(), key=lambda x: -x[1]):
        print(f"  {lbl:25} {cnt:4}")

    print(f"\n  == Sector Labels ==")
    for lbl, cnt in sorted(sector_lbs.items(), key=lambda x: -x[1]):
        print(f"  {lbl:25} {cnt:4}")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Label all RS-universe tickers by business group")
    parser.add_argument("--force",   action="store_true", help="Re-label all (overwrite existing sector labels)")
    parser.add_argument("--dry-run", action="store_true", help="Show counts only, don't write files")
    args = parser.parse_args()
    run(force=args.force, dry_run=args.dry_run)
