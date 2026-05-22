"""
AlphaAbsolute -- RS Theme Ranker  (I1b)
=======================================
Computes the 3-Layer RS Architecture:
  Layer 3: Stock vs Full Market (from rs_ranker.py + benchmark)
  Layer 2: Theme vs All Themes (median RS of theme members vs market)
  Layer 1: Stock within Theme (leader vs laggard within own theme)

Output:
  data/rs_universe/theme_rs_latest.json    -- theme heatmap
  data/rs_universe/strong_leader_latest.json -- per-stock 3-layer score

Run: Daily (after rs_ranker.py completes)
Cost: $0 -- pure Python

Formula:
  strong_leader_score = 0.45*rs_market_pct + 0.30*theme_vs_themes_pct + 0.25*within_theme_pct
  + momentum bonus if all 3 layers improving simultaneously
"""

import json
import sys
import os
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR  = BASE_DIR / "data" / "rs_universe"
DATA_DIR.mkdir(parents=True, exist_ok=True)

THEME_OUT_FILE  = DATA_DIR / "theme_rs_latest.json"
LEADER_OUT_FILE = DATA_DIR / "strong_leader_latest.json"
HISTORY_DIR     = DATA_DIR / "theme_history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

LABELS_FILE = BASE_DIR / "data" / "themes" / "ticker_labels.json"
(BASE_DIR / "data" / "themes").mkdir(parents=True, exist_ok=True)

# ── Load .env ─────────────────────────────────────────────────────────────────
def _load_env():
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
_load_env()


# ── 14 AlphaAbsolute Themes ───────────────────────────────────────────────────
# Each theme: list of representative tickers in our universe
# NOTE: A stock can appear in multiple themes (e.g. NVDA in AI + Data Center)

THEMES = {
    "AI_Related": {
        "name": "AI-Related",
        # Software/platform AI layer + frontier model companies
        "tickers": ["NVDA", "MSFT", "PLTR", "SOUN", "AMD", "ORCL", "CRM",
                    "IBM", "META", "GOOG", "GOOGL", "AMZN", "AI", "BBAI",
                    "SNOW", "DDOG", "PANW", "NOW", "WDAY"],
    },
    "Memory_HBM": {
        "name": "Memory / HBM",
        # DRAM/NAND producers + equipment + packaging bottleneck
        "tickers": ["MU", "WDC", "AMAT", "LRCX", "KLAC", "SMCI", "STX",
                    "ONTO", "FORM", "KLIC", "ACLS", "MRAM", "NXPI"],
    },
    "Space": {
        "name": "Space",
        # Launchers, on-orbit, satellite imagery, satellite comms
        "tickers": ["RKLB", "LUNR", "ASTS", "RDW", "PL", "SPCE", "IRDM",
                    "ACHR", "JOBY", "MAXR", "BKSY", "GFI"],
    },
    "Quantum": {
        "name": "Quantum Computing",
        # Pure-play quantum + incumbent (IBM has Watson + Q) + spinoffs
        "tickers": ["IONQ", "RGTI", "QUBT", "IBM", "MSFT", "GOOGL",
                    "NVDA", "QCOM", "HON"],
    },
    "Photonics": {
        "name": "Photonics / Optical",
        # Optical transceivers, photonic chips, fiber, laser
        "tickers": ["LITE", "COHR", "AAOI", "IPGP", "MRAM",
                    "CIEN", "NOK", "ERIC", "VIAV", "IIVI",
                    "ANET", "JNPR", "APH", "MPWR"],
    },
    "DefenseTech": {
        "name": "Defense Tech / GovTech",
        # Defense primes + cyber + GovTech SaaS + drone suppliers
        "tickers": ["PLTR", "AXON", "CACI", "LDOS", "SAIC", "BAH", "KTOS",
                    "RTX", "LMT", "NOC", "GD", "BA", "AVAV",
                    "MSFT", "CRWD", "PANW"],
    },
    "DataCenter": {
        "name": "Data Center",
        # REIT operators + network fabric + power + colocation
        "tickers": ["EQIX", "DLR", "VRT", "ETN", "ANET", "APH",
                    "SMCI", "DELL", "HPE", "NTAP",
                    "CSCO", "JNPR", "FFIV"],
    },
    "Nuclear_SMR": {
        "name": "Nuclear / SMR",
        # SMR developers + nuclear operators + uranium supply chain
        "tickers": ["NNE", "OKLO", "CEG", "CCJ", "VST", "BWXT",
                    "SMR", "GE", "HON", "UEC",
                    "NEE", "DUK", "SO"],
    },
    "NeoCloud": {
        "name": "NeoCloud / Compute",
        # Hyperscaler challengers + GPU cloud + crypto compute
        "tickers": ["CRWV", "CORZ", "CLSK", "MARA", "RIOT", "SMCI", "NTAP",
                    "DELL", "HPE", "PSTG", "NVDA"],
    },
    "AI_Infra": {
        "name": "AI Infrastructure",
        # Power delivery + servers + switches + cables + connectors
        "tickers": ["VRT", "DELL", "ANET", "APH", "EME", "PWR",
                    "ETN", "SMCI", "NVDA", "AMD",
                    "CIEN", "MPWR", "ON"],
    },
    "DataCenter_Infra": {
        "name": "Data Center Infra",
        # Construction + power gen + cooling + grid buildout for hyperscalers
        "tickers": ["PWR", "EME", "AMPS", "GLDD", "VST",
                    "ETN", "GE", "HON", "CAT", "DE",
                    "CEG", "NRG", "AES"],
    },
    "Drone_UAV": {
        "name": "Drone / UAV",
        # eVTOL + military drone + counter-drone + autonomy stack
        "tickers": ["ACHR", "JOBY", "RCAT", "AVAV", "KTOS",
                    "RTX", "LMT", "NOC", "TXT",
                    "TSLA", "AXON"],
    },
    "Robotics": {
        "name": "Robotics / Automation",
        # Surgical robots + industrial automation + semiconductor test + logistics
        "tickers": ["ISRG", "TER", "AZTA", "TDY", "ROK", "EMR",
                    "ABB", "FANUY", "IR",
                    "TSLA", "NVDA", "AMD"],
    },
    "Connectivity": {
        "name": "Connectivity / Satcom",
        # 5G carriers + satcom + tower + fiber + radio access
        "tickers": ["TMUS", "ASTS", "ERIC", "NOK",
                    "AMT", "CCI", "SBAC", "T", "VZ",
                    "CMCSA", "CSCO", "JNPR"],
    },
}

# ── PRIMARY_THEME_MAP — one primary theme per ticker ─────────────────────────
# Used for WITHIN-THEME RS ranking and strong_leader_latest.json dedup.
# Tickers not listed here default to the FIRST theme they appear in.
# Goal: NVDA counted once (AI_Related), not 4x — prevents theme inflation.
#
# Assign based on WHERE the company's primary revenue driver lives:
#   NVDA = AI chips (not datacenter infra, not quantum, not NeoCloud)
#   VRT = DataCenter cooling hardware (not AI infra services)
#   PLTR = DefenseTech (gov AI, not consumer AI)
PRIMARY_THEME_MAP: dict[str, str] = {
    # ── AI-Related (foundation models, AI software, AI chips) ────────────────
    "NVDA":  "AI_Related",
    "MSFT":  "AI_Related",
    "AMD":   "AI_Related",
    "IBM":   "AI_Related",
    "META":  "AI_Related",
    "GOOG":  "AI_Related",
    "GOOGL": "AI_Related",
    "AMZN":  "AI_Related",
    "AI":    "AI_Related",
    "BBAI":  "AI_Related",
    "SNOW":  "AI_Related",
    "DDOG":  "AI_Related",
    "NOW":   "AI_Related",
    "WDAY":  "AI_Related",
    "ORCL":  "AI_Related",
    "CRM":   "AI_Related",
    # ── Memory / HBM ─────────────────────────────────────────────────────────
    "MU":    "Memory_HBM",
    "WDC":   "Memory_HBM",
    "MRAM":  "Memory_HBM",
    "NXPI":  "Memory_HBM",
    # ── Space (launch, imagery, comms — not to be confused with Connectivity) ─
    "RKLB":  "Space",
    "LUNR":  "Space",
    "RDW":   "Space",
    "PL":    "Space",
    "MAXR":  "Space",
    "BKSY":  "Space",
    "GFI":   "Space",
    # ── Quantum ──────────────────────────────────────────────────────────────
    "IONQ":  "Quantum",
    "RGTI":  "Quantum",
    "QUBT":  "Quantum",
    # HON, MSFT, NVDA, QCOM assigned to other primaries — Quantum is side revenue
    # ── Photonics ────────────────────────────────────────────────────────────
    "LITE":  "Photonics",
    "COHR":  "Photonics",
    "AAOI":  "Photonics",
    "IPGP":  "Photonics",
    "VIAV":  "Photonics",
    "CIEN":  "Photonics",    # optical networking = photonics primary
    "IIVI":  "Photonics",
    "NOK":   "Connectivity", # despite some photonics products, primary = telecom
    "ERIC":  "Connectivity",
    # ── Defense Tech / GovTech ───────────────────────────────────────────────
    "PLTR":  "DefenseTech",
    "AXON":  "DefenseTech",
    "CACI":  "DefenseTech",
    "LDOS":  "DefenseTech",
    "SAIC":  "DefenseTech",
    "BAH":   "DefenseTech",
    "KTOS":  "DefenseTech",
    "RTX":   "DefenseTech",
    "LMT":   "DefenseTech",
    "NOC":   "DefenseTech",
    "GD":    "DefenseTech",
    "BA":    "DefenseTech",
    "AVAV":  "DefenseTech",  # military drone = defense primary
    "CRWD":  "DefenseTech",
    "PANW":  "DefenseTech",
    "TXT":   "DefenseTech",
    # ── Data Center ──────────────────────────────────────────────────────────
    "EQIX":  "DataCenter",
    "DLR":   "DataCenter",
    "VRT":   "DataCenter",   # data center cooling hardware
    "SMCI":  "DataCenter",   # servers for data centers
    "DELL":  "DataCenter",
    "HPE":   "DataCenter",
    "NTAP":  "DataCenter",
    "ANET":  "DataCenter",   # ethernet fabric for data centers
    "JNPR":  "DataCenter",
    "CSCO":  "DataCenter",
    "FFIV":  "DataCenter",
    "PSTG":  "DataCenter",
    # ── Nuclear / SMR ────────────────────────────────────────────────────────
    "NNE":   "Nuclear_SMR",
    "OKLO":  "Nuclear_SMR",
    "CEG":   "Nuclear_SMR",
    "CCJ":   "Nuclear_SMR",
    "VST":   "Nuclear_SMR",
    "BWXT":  "Nuclear_SMR",
    "SMR":   "Nuclear_SMR",
    "GE":    "Nuclear_SMR",  # GE Hitachi is primary nuclear identity today
    "HON":   "Nuclear_SMR",  # TRISO fuel + grid modernization
    "UEC":   "Nuclear_SMR",
    "NEE":   "Nuclear_SMR",  # includes nuclear + wind
    "DUK":   "Nuclear_SMR",
    "SO":    "Nuclear_SMR",
    "NRG":   "Nuclear_SMR",
    "AES":   "Nuclear_SMR",
    # ── NeoCloud / Compute ────────────────────────────────────────────────────
    "CRWV":  "NeoCloud",
    "CORZ":  "NeoCloud",
    "CLSK":  "NeoCloud",
    "MARA":  "NeoCloud",
    "RIOT":  "NeoCloud",
    "CIFR":  "NeoCloud",
    # ── AI Infrastructure ─────────────────────────────────────────────────────
    "APH":   "AI_Infra",     # connectors for AI systems
    "ETN":   "AI_Infra",     # power management for AI infra
    "MPWR":  "AI_Infra",
    "ON":    "AI_Infra",
    # ── Data Center Infrastructure (construction / grid buildout) ─────────────
    "EME":   "DataCenter_Infra",
    "PWR":   "DataCenter_Infra",
    "AMPS":  "DataCenter_Infra",
    "GLDD":  "DataCenter_Infra",
    "CAT":   "DataCenter_Infra",
    "DE":    "DataCenter_Infra",
    # ── Drone / UAV (commercial and defense) ──────────────────────────────────
    "ACHR":  "Drone_UAV",
    "JOBY":  "Drone_UAV",
    "RCAT":  "Drone_UAV",
    # ── Robotics / Automation ────────────────────────────────────────────────
    "ISRG":  "Robotics",
    "TER":   "Robotics",
    "AZTA":  "Robotics",
    "TDY":   "Robotics",
    "ROK":   "Robotics",
    "EMR":   "Robotics",
    "IR":    "Robotics",
    "TSLA":  "Robotics",     # primary Optimus thesis (not EV for theme purposes)
    # ── Connectivity / Satcom ────────────────────────────────────────────────
    "TMUS":  "Connectivity",
    "ASTS":  "Connectivity",  # satellite to cellular
    "AMT":   "Connectivity",
    "CCI":   "Connectivity",
    "SBAC":  "Connectivity",
    "T":     "Connectivity",
    "VZ":    "Connectivity",
    "CMCSA": "Connectivity",
    # ── Memory / Semiconductor equipment (not mapped above) ───────────────────
    "AMAT":  "Memory_HBM",
    "LRCX":  "Memory_HBM",
    "KLAC":  "Memory_HBM",
    "ONTO":  "Memory_HBM",
    "FORM":  "Memory_HBM",
    "KLIC":  "Memory_HBM",
    "ACLS":  "Memory_HBM",
    "COHU":  "Memory_HBM",   # semiconductor test handler
    "CAMT":  "Memory_HBM",   # HBM reference inspection tool
    "PLAB":  "Memory_HBM",   # photomask for semis
    "UCTT":  "Memory_HBM",   # ultra-clean chamber parts
    "AEIS":  "Memory_HBM",   # power delivery for etch/dep
    "ACMR":  "Memory_HBM",   # wet cleaning equipment
    # ── NeoCloud R2000 additions (crypto miners / alt-compute) ────────────────
    "IREN":  "NeoCloud",     # Iris Energy — AI cloud + BTC mining
    "HUT":   "NeoCloud",     # Hut 8 — BTC mining + AI compute
    "WULF":  "NeoCloud",     # TeraWulf — nuclear-powered BTC mining
    "BTBT":  "NeoCloud",     # Bit Digital — AI cloud + BTC
    # ── Space R2000 additions ─────────────────────────────────────────────────
    "IRDM":  "Connectivity", # Iridium — satcom constellation (not launch = Connectivity)
    "GSAT":  "Connectivity", # Globalstar — satcom
    # ── Quantum R2000 additions ───────────────────────────────────────────────
    "ARQQ":  "Quantum",      # Arqit Quantum — QKD encryption
    # ── Defense Tech R2000 additions ─────────────────────────────────────────
    "MRCY":  "DefenseTech",  # Mercury Systems — defense electronics
    "SPNS":  "DefenseTech",  # Sapiens — defense software (if in R2000)
    "DZSI":  "DefenseTech",  # DASAN Zhone — edge networking for defense
    # ── Nuclear / SMR R2000 additions ────────────────────────────────────────
    "UUUU":  "Nuclear_SMR",  # Energy Fuels — uranium + REE
    "DNN":   "Nuclear_SMR",  # Denison Mines — uranium development
    "NXE":   "Nuclear_SMR",  # NexGen Energy — uranium mining Canada
    "URG":   "Nuclear_SMR",  # Uranium Resources — US uranium
    # ── Connectivity R2000 additions ─────────────────────────────────────────
    "CCOI":  "Connectivity", # Cogent Communications — fiber/IP backbone
    "CALX":  "Connectivity", # Calix — broadband access systems
    "LUMN":  "Connectivity", # Lumen Technologies — fiber network
    "SHEN":  "Connectivity", # Shenandoah Telecom — rural broadband
}

def _get_primary_theme(ticker: str) -> str:
    """
    Return the primary theme ID for a ticker.
    Explicit map first, then first theme the ticker appears in.
    """
    t = ticker.upper()
    if t in PRIMARY_THEME_MAP:
        return PRIMARY_THEME_MAP[t]
    # Fallback: first theme containing this ticker
    for theme_id, theme_data in THEMES.items():
        if t in [x.upper() for x in theme_data["tickers"]]:
            return theme_id
    return "AI_Related"   # last resort

# Flat set of all official AlphaAbsolute theme tickers (for preference scoring)
OFFICIAL_THEME_TICKERS: set = {
    t.upper() for theme_data in THEMES.values()
    for t in theme_data["tickers"]
}


# ── Ticker Label I/O ──────────────────────────────────────────────────────────

def _load_ticker_labels() -> dict:
    """
    Load ticker → theme mapping from data/themes/ticker_labels.json.
    Falls back to PRIMARY_THEME_MAP if file missing.
    Returns {TICKER: theme_id}
    """
    if LABELS_FILE.exists():
        try:
            d = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
            labels = d.get("labels", d)  # support both {labels:{}} and flat dict
            labels = {k.upper(): v for k, v in labels.items() if not k.startswith("_")}
            if labels:
                return labels
        except Exception:
            pass
    # Fallback: build from PRIMARY_THEME_MAP + THEMES inline tickers
    labels = dict(PRIMARY_THEME_MAP)
    for theme_id, info in THEMES.items():
        for t in info.get("tickers", []):
            if t.upper() not in labels:
                labels[t.upper()] = theme_id
    return labels


def save_ticker_labels(labels: dict):
    """Save ticker → theme mapping back to file (for auto-update)."""
    existing = {}
    if LABELS_FILE.exists():
        try:
            existing = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing["labels"] = dict(sorted(labels.items()))
    existing.setdefault("_meta", {})["last_updated"] = date.today().isoformat()
    LABELS_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def build_theme_membership(ticker_labels: dict) -> dict:
    """
    Invert ticker_labels into theme → [ticker list] mapping.
    Returns {theme_id: [ticker, ...]}
    Only includes theme_ids that exist in THEMES.
    """
    membership: dict[str, list] = {tid: [] for tid in THEMES}
    for ticker, theme_id in ticker_labels.items():
        if theme_id in membership:
            membership[theme_id].append(ticker.upper())
    return membership


# ── Data Loading ──────────────────────────────────────────────────────────────

def _load_rs_universe() -> dict:
    """Load latest rs_ranker output. Returns {ticker: entry_dict}."""
    rs_file = DATA_DIR / "latest.json"
    if not rs_file.exists():
        return {}
    try:
        d = json.loads(rs_file.read_text(encoding="utf-8"))
        # Structure: top-level keys include "universe" dict + metadata fields
        universe = d.get("universe", {})
        if not universe:
            # Fallback: some versions embed tickers at top level
            universe = {t: v for t, v in d.items()
                        if isinstance(v, dict) and "rs_composite_pct" in v}
        return universe
    except Exception:
        return {}


def _load_theme_history(theme_id: str, weeks_back: int = 4) -> list:
    """Load N weeks of theme RS history. Returns list of dicts (oldest first)."""
    history = []
    for f in sorted(HISTORY_DIR.glob(f"{theme_id}_*.json")):
        try:
            history.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    # Return only last N entries
    return history[-weeks_back:] if len(history) >= 1 else []


# ── Median helper ─────────────────────────────────────────────────────────────

def _median(values: list) -> float:
    if not values:
        return 50.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return (s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2)


# ── Theme RS Computation ──────────────────────────────────────────────────────

def _percentile_rank(values: list, target: float) -> float:
    """Return percentile rank of target within values list (0-100)."""
    if not values or target is None:
        return 50.0
    below = sum(1 for v in values if v < target)
    return round(below / max(len(values) - 1, 1) * 100, 1)


def _rank_within_theme(members: list, key: str) -> dict:
    """
    Rank members by a given RS key within theme.
    Returns {ticker: within_pct} where 100th = strongest.
    members: list of (ticker, entry_dict)
    """
    vals = [(t, float(e.get(key) or 0)) for t, e in members if e.get(key) is not None]
    vals.sort(key=lambda x: x[1])
    n = len(vals)
    if n == 0:
        return {}
    return {
        t: round(i / max(n - 1, 1) * 100, 1)
        for i, (t, _) in enumerate(vals)
    }


def _avg(values: list) -> float | None:
    """Simple average. Returns None if empty."""
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def compute_theme_rs(universe: dict) -> dict:
    """
    For each theme, compute RS using ticker_labels.json membership.

    Formula (per theme):
      theme_rs_1m = avg(rs_1m_pct)  of all labeled members with data
      theme_rs_3m = avg(rs_3m_pct)  of all labeled members with data
      theme_rs_6m = avg(rs_6m_pct)  of all labeled members with data
      theme_rs_composite = avg(theme_rs_1m, theme_rs_3m, theme_rs_6m)
                           → used for rank vs other themes

    Within-theme rank per TF:
      within_1m_pct: where does this stock rank vs other theme members on 1M RS?
      within_3m_pct: same for 3M
      within_6m_pct: same for 6M

    Membership source: data/themes/ticker_labels.json (auto-updated, editable)
    """
    # Load label map → build membership
    ticker_labels  = _load_ticker_labels()
    theme_membership = build_theme_membership(ticker_labels)  # {theme_id: [tickers]}

    theme_data = {}

    for theme_id, theme_info in THEMES.items():
        members_in_theme = theme_membership.get(theme_id, [])

        # Collect members that have RS data in the universe
        valid_members = []   # (ticker, entry_dict)
        for ticker in members_in_theme:
            entry = universe.get(ticker)
            if not entry:
                continue
            # Need at least one timeframe RS
            if any(entry.get(k) is not None for k in ("rs_1m_pct", "rs_3m_pct", "rs_6m_pct")):
                valid_members.append((ticker, entry))

        if not valid_members:
            theme_data[theme_id] = {
                "theme_id": theme_id, "name": theme_info["name"],
                "n_members": 0, "n_labeled": len(members_in_theme),
                "theme_rs_1m": None, "theme_rs_3m": None, "theme_rs_6m": None,
                "theme_rs_composite": None,
                "theme_mom_1m_3m": None, "theme_mom_3m_6m": None,
                "members": {}, "leaders": [], "laggards": [],
            }
            continue

        # ── Collect per-TF RS percentile values (market-wide) ─────────────────
        rs_1m_vals = [e.get("rs_1m_pct") for _, e in valid_members]
        rs_3m_vals = [e.get("rs_3m_pct") for _, e in valid_members]
        rs_6m_vals = [e.get("rs_6m_pct") for _, e in valid_members]

        # ── Theme RS = average of members' RS percentile per TF ───────────────
        theme_rs_1m = _avg(rs_1m_vals)
        theme_rs_3m = _avg(rs_3m_vals)
        theme_rs_6m = _avg(rs_6m_vals)

        # Composite = average of the 3 TF averages (for ranking vs other themes)
        composite_inputs = [v for v in [theme_rs_1m, theme_rs_3m, theme_rs_6m] if v is not None]
        theme_rs_composite = round(sum(composite_inputs) / len(composite_inputs), 1) if composite_inputs else None

        # Theme momentum: is 1M avg higher than 3M avg? (theme accelerating recently)
        theme_mom_1m_3m = round(theme_rs_1m - theme_rs_3m, 1) if (theme_rs_1m and theme_rs_3m) else None
        theme_mom_3m_6m = round(theme_rs_3m - theme_rs_6m, 1) if (theme_rs_3m and theme_rs_6m) else None

        # ── Within-theme rank per TF ───────────────────────────────────────────
        # Rank each stock vs other theme members (100th = best within theme)
        rank_1m = _rank_within_theme(valid_members, "rs_1m_pct")
        rank_3m = _rank_within_theme(valid_members, "rs_3m_pct")
        rank_6m = _rank_within_theme(valid_members, "rs_6m_pct")

        # ── Build per-member details ───────────────────────────────────────────
        member_details = []
        for ticker, entry in valid_members:
            m1m = entry.get("rs_1m_pct")
            m3m = entry.get("rs_3m_pct")
            m6m = entry.get("rs_6m_pct")
            w1m = rank_1m.get(ticker, 50.0)
            w3m = rank_3m.get(ticker, 50.0)
            w6m = rank_6m.get(ticker, 50.0)

            member_details.append({
                "ticker":          ticker,
                # Market-wide RS percentile (raw)
                "rs_1m":           round(m1m, 1) if m1m is not None else None,
                "rs_3m":           round(m3m, 1) if m3m is not None else None,
                "rs_6m":           round(m6m, 1) if m6m is not None else None,
                # Market momentum
                "rs_mom_1m_3m":    round(m1m - m3m, 1) if (m1m and m3m) else None,
                "rs_mom_3m_6m":    round(m3m - m6m, 1) if (m3m and m6m) else None,
                # Within-theme rank (0-100, 100 = best in theme)
                "within_1m_pct":   w1m,
                "within_3m_pct":   w3m,
                "within_6m_pct":   w6m,
                # Within-theme momentum
                "theme_mom_1m_3m": round(w1m - w3m, 1),
                "theme_mom_3m_6m": round(w3m - w6m, 1),
                "rs_source":       entry.get("rs_source", "?"),
            })

        # Sort by 1M RS (most recent signal) for leaders list
        member_details.sort(key=lambda x: x.get("rs_1m") or 0, reverse=True)

        theme_data[theme_id] = {
            "theme_id":           theme_id,
            "name":               theme_info["name"],
            "n_labeled":          len(members_in_theme),     # total labeled in theme
            "n_members":          len(valid_members),         # with actual RS data today
            # Theme RS averages (market-wide percentile, averaged across members)
            "theme_rs_1m":        theme_rs_1m,
            "theme_rs_3m":        theme_rs_3m,
            "theme_rs_6m":        theme_rs_6m,
            "theme_rs_composite": theme_rs_composite,        # avg(1M,3M,6M) → used for ranking
            # Momentum: is the theme accelerating?
            "theme_mom_1m_3m":    theme_mom_1m_3m,
            "theme_mom_3m_6m":    theme_mom_3m_6m,
            # Per-member data
            "members":            {m["ticker"]: m for m in member_details},
            "leaders":            member_details[:5],         # top 5 by 1M RS
            "laggards":           member_details[-3:] if len(member_details) >= 3 else [],
        }

    return theme_data


def _pct_rank_among(values: list[tuple[str, float | None]], tid: str) -> float | None:
    """
    Given list of (theme_id, raw_value), return the percentile rank of `tid`.
    0 = weakest, 100 = strongest among all themes with data.
    """
    valid = [(t, v) for t, v in values if v is not None]
    if not valid:
        return None
    sorted_vals = sorted(valid, key=lambda x: x[1])
    n = len(sorted_vals)
    for i, (t, _) in enumerate(sorted_vals):
        if t == tid:
            return round(i / max(n - 1, 1) * 100, 1)
    return None


def rank_themes(theme_data: dict) -> dict:
    """
    Rank themes against each other PER TIMEFRAME:

      Step 1: each theme has raw avg RS from members
              theme_rs_1m_raw = avg(rs_1m_pct of members)
              theme_rs_3m_raw = avg(rs_3m_pct of members)
              theme_rs_6m_raw = avg(rs_6m_pct of members)

      Step 2: percentile-rank those raws among all 14 themes
              theme_1m_pct = percentile of theme_rs_1m_raw vs all themes (0-100)
              theme_3m_pct = percentile of theme_rs_3m_raw vs all themes (0-100)
              theme_6m_pct = percentile of theme_rs_6m_raw vs all themes (0-100)

      Step 3: momentum = theme_1m_pct - theme_3m_pct
              positive → theme climbing rank recently = STRENGTHENING
              negative → theme falling rank recently = WEAKENING

    HOT/WARM classification uses theme_1m_pct (most recent).
    """
    # Collect raw scores for each TF
    raw_1m = [(tid, td.get("theme_rs_1m")) for tid, td in theme_data.items()]
    raw_3m = [(tid, td.get("theme_rs_3m")) for tid, td in theme_data.items()]
    raw_6m = [(tid, td.get("theme_rs_6m")) for tid, td in theme_data.items()]

    for tid, td in theme_data.items():
        pct_1m = _pct_rank_among(raw_1m, tid)
        pct_3m = _pct_rank_among(raw_3m, tid)
        pct_6m = _pct_rank_among(raw_6m, tid)

        # Momentum: rising in 1M rank vs 3M rank = theme strengthening
        mom_1m_3m = round(pct_1m - pct_3m, 1) if (pct_1m is not None and pct_3m is not None) else None
        mom_3m_6m = round(pct_3m - pct_6m, 1) if (pct_3m is not None and pct_6m is not None) else None

        td["theme_1m_pct"]  = pct_1m   # percentile vs other themes on 1M basis
        td["theme_3m_pct"]  = pct_3m   # percentile vs other themes on 3M basis
        td["theme_6m_pct"]  = pct_6m   # percentile vs other themes on 6M basis
        td["theme_mom_1m_3m_pct"] = mom_1m_3m  # rank momentum (1M vs 3M)
        td["theme_mom_3m_6m_pct"] = mom_3m_6m

        # Backwards compat alias used by other agents
        td["theme_vs_themes_pct"] = pct_1m  # primary = most recent (1M)

        # Rank by 1M pct (1 = highest among themes)
        td["theme_rank"] = None  # filled below

    # Set rank by 1M pct
    ranked = sorted(
        [(tid, td["theme_1m_pct"] or 0) for tid, td in theme_data.items()],
        key=lambda x: x[1], reverse=True
    )
    for rank, (tid, _) in enumerate(ranked, 1):
        theme_data[tid]["theme_rank"] = rank

    return theme_data


def classify_theme_phase(theme_1m_pct: float | None, mom_1m_3m: float | None) -> str:
    """
    Classify theme phase using theme_1m_pct (rank vs other themes on 1M basis)
    and momentum (1M pct rank minus 3M pct rank).

      HOT:      theme_1m_pct >= 75  → top quartile among themes
      WARM:     theme_1m_pct >= 55
      EMERGING: theme_1m_pct 35-55 AND momentum > 0 (climbing rank)
      COOLING:  was strong but mom < -15 (dropping rank fast)
      NEUTRAL:  theme_1m_pct 35-55, no clear momentum
      WEAK:     theme_1m_pct < 35   → bottom third
    """
    if theme_1m_pct is None:
        return "UNKNOWN"
    mom = mom_1m_3m or 0.0

    if theme_1m_pct >= 75:
        return "COOLING" if mom < -15 else "HOT"
    if theme_1m_pct >= 55:
        return "COOLING" if mom < -15 else "WARM"
    if theme_1m_pct >= 35:
        return "EMERGING" if mom > 0 else "NEUTRAL"
    return "WEAK"


def add_theme_changes(theme_data: dict) -> dict:
    """
    Load historical snapshots, compute 4W and 13W change in theme_1m_pct.
    Adds chg_4w (change in 1M percentile rank vs 4 weeks ago),
         chg_13w, phase to each theme entry.
    """
    today = date.today().isoformat()

    for theme_id, td in theme_data.items():
        history = _load_theme_history(theme_id, weeks_back=13)
        chg_4w = chg_13w = None

        if history:
            four_w_entry = thirteen_w_entry = None
            for entry in reversed(history):
                try:
                    days_ago = (date.fromisoformat(today) -
                                date.fromisoformat(entry.get("date", today))).days
                except Exception:
                    continue
                if four_w_entry is None and days_ago >= 25:
                    four_w_entry = entry
                if thirteen_w_entry is None and days_ago >= 85:
                    thirteen_w_entry = entry

            current = td.get("theme_1m_pct")
            if current is not None:
                if four_w_entry:
                    old = four_w_entry.get("theme_1m_pct")
                    if old is not None:
                        chg_4w = round(current - old, 1)
                if thirteen_w_entry:
                    old = thirteen_w_entry.get("theme_1m_pct")
                    if old is not None:
                        chg_13w = round(current - old, 1)

        td["chg_4w"]  = chg_4w
        td["chg_13w"] = chg_13w
        td["phase"]   = classify_theme_phase(
            td.get("theme_1m_pct"),
            td.get("theme_mom_1m_3m_pct")
        )

    return theme_data


def save_theme_snapshot(theme_data: dict):
    """Save today's snapshot to history. Stores per-TF pct ranks for future change tracking."""
    today     = date.today().isoformat()
    safe_date = today.replace("-", "")

    for theme_id, td in theme_data.items():
        snapshot = {
            "date":                  today,
            "theme_id":              theme_id,
            # Raw avg RS across members per TF
            "theme_rs_1m":           td.get("theme_rs_1m"),
            "theme_rs_3m":           td.get("theme_rs_3m"),
            "theme_rs_6m":           td.get("theme_rs_6m"),
            # Percentile rank vs other themes per TF (main tracking metric)
            "theme_1m_pct":          td.get("theme_1m_pct"),
            "theme_3m_pct":          td.get("theme_3m_pct"),
            "theme_6m_pct":          td.get("theme_6m_pct"),
            # Momentum
            "theme_mom_1m_3m_pct":   td.get("theme_mom_1m_3m_pct"),
            "n_members":             td.get("n_members", 0),
        }
        hist_file = HISTORY_DIR / f"{theme_id}_{safe_date}.json"
        hist_file.write_text(json.dumps(snapshot, ensure_ascii=False))


# ── Strong Leader Score ───────────────────────────────────────────────────────

def compute_strong_leader_scores(universe: dict, theme_data: dict) -> dict:
    """
    Per-stock 3-layer Strong Leader Score with full 3-TF market + theme RS.

    Score = 0.45*rs_market_1m + 0.30*theme_vs_themes_pct + 0.25*within_theme_1m_pct
            + momentum bonus (1M-3M acceleration in both market + theme)

    Uses 1M RS as primary (most recent signal, not lagging composite).

    Output per stock includes:
      Market RS:    rs_1m_pct, rs_3m_pct, rs_6m_pct + rs_mom_1m_3m, rs_mom_3m_6m
      Theme RS:     within_1m_pct, within_3m_pct, within_6m_pct + theme_mom_1m_3m, theme_mom_3m_6m
      Theme level:  theme_vs_themes_pct (how hot is the theme vs all 14?)
      Score:        strong_leader_score (0-100)
    """
    today = date.today().isoformat()
    results = {}

    # Build per-stock theme membership from computed theme_data (uses pre-computed rankings)
    stock_themes = {}  # ticker -> list of theme membership dicts

    for theme_id, td in theme_data.items():
        members_dict = td.get("members", {})  # {ticker: member_detail_dict}
        for ticker, member in members_dict.items():
            if ticker not in stock_themes:
                stock_themes[ticker] = []
            stock_themes[ticker].append({
                "theme_id":          theme_id,
                "theme_name":        td["name"],
                "theme_vs_themes_pct": td.get("theme_vs_themes_pct", 50.0),
                "theme_rs_market":   td.get("theme_rs_market", 50.0),
                "theme_rs_1m":       td.get("theme_rs_1m"),
                "theme_rs_3m":       td.get("theme_rs_3m"),
                "theme_rs_6m":       td.get("theme_rs_6m"),
                "theme_mom_1m_3m":   td.get("theme_mom_1m_3m"),   # theme-level momentum
                "theme_mom_3m_6m":   td.get("theme_mom_3m_6m"),
                "theme_phase":       td.get("phase", "NEUTRAL"),
                "theme_chg_4w":      td.get("chg_4w"),
                # Per-stock within-theme ranks
                "within_pct":        member.get("within_pct", 50.0),
                "within_1m_pct":     member.get("within_1m_pct", 50.0),
                "within_3m_pct":     member.get("within_3m_pct", 50.0),
                "within_6m_pct":     member.get("within_6m_pct", 50.0),
                "theme_mom_stock_1m_3m": member.get("theme_mom_1m_3m"),  # stock's momentum within theme
                "theme_mom_stock_3m_6m": member.get("theme_mom_3m_6m"),
            })

    for ticker, entry in universe.items():
        rs_1m  = entry.get("rs_1m_pct")
        rs_3m  = entry.get("rs_3m_pct")
        rs_6m  = entry.get("rs_6m_pct")
        rs_comp = float(entry.get("rs_composite_pct", 0) or 0)
        # Use 1M as primary market RS signal (most recent)
        rs_market_primary = float(rs_1m or rs_comp or 50.0)

        # Market momentum
        rs_mom_1m_3m = entry.get("rs_mom_1m_3m")
        rs_mom_3m_6m = entry.get("rs_mom_3m_6m")

        themes_for_stock = stock_themes.get(ticker, [])

        if not themes_for_stock:
            strong_score     = rs_market_primary
            best_theme       = None
            within_1m_pct    = 50.0
            within_3m_pct    = 50.0
            within_6m_pct    = 50.0
            theme_vs_pct     = 50.0
            theme_mom_1m_3m  = None
            theme_mom_3m_6m  = None
            all_theme_names  = []
        else:
            # Best theme = highest theme_vs_themes_pct (hottest theme stock belongs to)
            themes_for_stock.sort(key=lambda x: x["theme_vs_themes_pct"], reverse=True)
            best = themes_for_stock[0]

            within_1m_pct   = best["within_1m_pct"]
            within_3m_pct   = best["within_3m_pct"]
            within_6m_pct   = best["within_6m_pct"]
            theme_vs_pct    = best["theme_vs_themes_pct"]
            theme_mom_1m_3m = best.get("theme_mom_stock_1m_3m")
            theme_mom_3m_6m = best.get("theme_mom_stock_3m_6m")
            all_theme_names = [t["theme_name"] for t in themes_for_stock]

            # Strong Leader Score — uses 1M as primary (most recent, not lagging)
            strong_score = (
                0.45 * rs_market_primary +
                0.30 * theme_vs_pct +
                0.25 * within_1m_pct
            )

            # Momentum bonus: accelerating in market AND/OR theme
            bonus = 0
            if rs_mom_1m_3m is not None and rs_mom_1m_3m > 5:
                bonus += 4   # market 1M outperforming 3M
            if theme_mom_1m_3m is not None and theme_mom_1m_3m > 5:
                bonus += 4   # gaining rank within theme recently
            # Double bonus: both accelerating simultaneously
            if (rs_mom_1m_3m is not None and rs_mom_1m_3m > 0 and
                    theme_mom_1m_3m is not None and theme_mom_1m_3m > 0):
                bonus += 4
            # Deceleration penalty
            if rs_mom_1m_3m is not None and rs_mom_1m_3m < -15:
                bonus -= 8   # sharp market decel
            if theme_mom_1m_3m is not None and theme_mom_1m_3m < -15:
                bonus -= 5   # losing rank within theme

            strong_score = max(0.0, min(99.0, strong_score + bonus))
            best_theme = best

        # Label
        if strong_score >= 85:   label = "MAX_LEADER"
        elif strong_score >= 72: label = "STRONG_LEADER"
        elif strong_score >= 60: label = "EMERGING"
        elif strong_score >= 45: label = "WATCHLIST"
        else:                    label = "AVOID"

        results[ticker] = {
            "ticker":                ticker,
            # Market RS — 3 timeframes + momentum
            "rs_market_pct":         round(rs_comp, 1),       # composite (for compatibility)
            "rs_1m_pct":             round(rs_1m, 1) if rs_1m else None,
            "rs_3m_pct":             round(rs_3m, 1) if rs_3m else None,
            "rs_6m_pct":             round(rs_6m, 1) if rs_6m else None,
            "rs_mom_1m_3m":          round(rs_mom_1m_3m, 1) if rs_mom_1m_3m is not None else None,
            "rs_mom_3m_6m":          round(rs_mom_3m_6m, 1) if rs_mom_3m_6m is not None else None,
            # Theme RS — 3 timeframes + momentum
            "within_theme_pct":      round(within_1m_pct, 1),  # primary = 1M
            "within_theme_1m_pct":   round(within_1m_pct, 1),
            "within_theme_3m_pct":   round(within_3m_pct, 1),
            "within_theme_6m_pct":   round(within_6m_pct, 1),
            "theme_mom_1m_3m":       round(theme_mom_1m_3m, 1) if theme_mom_1m_3m is not None else None,
            "theme_mom_3m_6m":       round(theme_mom_3m_6m, 1) if theme_mom_3m_6m is not None else None,
            "theme_vs_themes_pct":   round(theme_vs_pct, 1),  # how hot is the theme?
            # Score
            "strong_leader_score":   round(strong_score, 1),
            "label":                 label,
            # Theme context
            "primary_theme":         _get_primary_theme(ticker),   # canonical theme (no double-count)
            "best_theme":            best_theme["theme_name"] if best_theme else None,
            "theme_phase":           best_theme["theme_phase"] if best_theme else "N/A",
            "theme_chg_4w":          best_theme.get("theme_chg_4w") if best_theme else None,
            "all_themes":            all_theme_names,
            "computed_at":           today,
        }

    return results


# ── Print Helpers ─────────────────────────────────────────────────────────────

def _phase_icon(phase: str) -> str:
    return {
        "HOT":      "[HOT]",
        "WARM":     "[WARM]",
        "EMERGING": "[EMRG]",
        "COOLING":  "[COOL]",
        "NEUTRAL":  "[NEUT]",
        "WEAK":     "[WEAK]",
    }.get(phase, "[?]")


def print_theme_heatmap(theme_data: dict):
    """
    Print theme heatmap sorted by 1M percentile rank (vs other themes).

    Columns:
      #       = rank (1 = strongest theme right now on 1M basis)
      1M/3M/6M = percentile rank of this theme vs all other themes per TF
                 e.g. 1M=80 means this theme's avg 1M RS is stronger than 80% of themes
      Mom     = 1M_pct - 3M_pct (positive = theme climbing rank)
      4W Chg  = change in 1M percentile rank vs 4 weeks ago
      N       = number of members with RS data
      PHASE   = HOT/WARM/EMERGING/NEUTRAL/COOLING/WEAK
    """
    today = date.today().isoformat()
    print(f"\n{'='*92}")
    print(f"  THEME HEATMAP -- {today}")
    print(f"  Pct = rank vs other themes (100=best) | Mom = 1M_pct - 3M_pct | Raw = avg member RS pct")
    print(f"{'='*92}")
    print(f"  {'#':<3} {'THEME':<22} {'1M%':>5} {'3M%':>5} {'6M%':>5} {'Mom':>5} {'4W':>6} {'N':>3}  {'PHASE':<9} TOP LEADERS")
    print(f"  {'-'*88}")

    sorted_themes = sorted(theme_data.items(),
                           key=lambda x: x[1].get("theme_1m_pct") or 0,
                           reverse=True)

    def _f(v, sign=False):
        if v is None: return "  n/a"
        return f"{v:+.0f}" if sign else f"{v:.0f}"

    for rank, (tid, td) in enumerate(sorted_themes, 1):
        p1m = td.get("theme_1m_pct")
        if p1m is None:
            continue
        p3m   = _f(td.get("theme_3m_pct"))
        p6m   = _f(td.get("theme_6m_pct"))
        mom   = _f(td.get("theme_mom_1m_3m_pct"), sign=True)
        chg   = td.get("chg_4w")
        chg_s = f"{chg:+.0f}" if chg is not None else "  n/a"
        phase = _phase_icon(td.get("phase", "NEUTRAL"))
        n     = td.get("n_members", 0)
        leaders = ", ".join(m["ticker"] for m in td.get("leaders", [])[:3])
        name  = td["name"][:21]

        print(f"  {rank:<3} {name:<22} {p1m:>5.0f} {p3m:>5} {p6m:>5} {mom:>5} {chg_s:>6}  {n:>2}  {phase:<9} {leaders}")

    print(f"{'='*92}\n")


def print_strong_leaders(leader_data: dict, top_n: int = 15):
    """Print 3-TF market + theme RS table with momentum."""
    print(f"\n{'='*100}")
    print(f"  STRONG LEADER SCORES -- {date.today().isoformat()}")
    print(f"  Market RS: 6M > 3M > 1M | D1m-3m | Theme RS: 6M > 3M > 1M | DThm | Score")
    print(f"{'='*100}")
    hdr = (f"  {'TICKER':<7} {'Score':<6} "
           f"{'Mkt6M':>6} {'Mkt3M':>6} {'Mkt1M':>6} {'D1m-3m':>7} {'D3m-6m':>7}  "
           f"{'Thm6M':>6} {'Thm3M':>6} {'Thm1M':>6} {'DThm':>6}  "
           f"{'ThmVs%':>7}  {'Label':<13} Theme")
    print(hdr)
    print(f"  {'-'*95}")

    sorted_leaders = sorted(leader_data.items(),
                            key=lambda x: x[1]["strong_leader_score"],
                            reverse=True)

    def _f(v, prefix=""):
        """Format optional float."""
        if v is None: return "  N/A"
        return f"{prefix}{v:+.0f}" if prefix else f"{v:.0f}"

    for ticker, d in sorted_leaders[:top_n]:
        score   = d["strong_leader_score"]
        m6      = _f(d.get("rs_6m_pct"))
        m3      = _f(d.get("rs_3m_pct"))
        m1      = _f(d.get("rs_1m_pct"))
        dm13    = _f(d.get("rs_mom_1m_3m"), "+")
        dm36    = _f(d.get("rs_mom_3m_6m"), "+")
        t6      = _f(d.get("within_theme_6m_pct"))
        t3      = _f(d.get("within_theme_3m_pct"))
        t1      = _f(d.get("within_theme_1m_pct"))
        dt13    = _f(d.get("theme_mom_1m_3m"), "+")
        tv      = _f(d.get("theme_vs_themes_pct"))
        label   = d["label"]
        thm_nm  = (d["best_theme"] or "N/A")[:14]

        print(f"  {ticker:<7} {score:<6.1f} "
              f"{m6:>6} {m3:>6} {m1:>6} {dm13:>7} {dm36:>7}  "
              f"{t6:>6} {t3:>6} {t1:>6} {dt13:>6}  "
              f"{tv:>7}  {label:<13} {thm_nm}")

    print(f"{'='*100}\n")


# ── Main Entry Point ──────────────────────────────────────────────────────────

def run():
    """
    Main entry: load rs_universe/latest.json, compute theme RS + strong leader scores.
    Saves theme_rs_latest.json and strong_leader_latest.json.
    """
    today = date.today().isoformat()
    print(f"\n[RS Theme Ranker] Running | {today}")

    # Load rs_ranker output
    universe = _load_rs_universe()
    if not universe:
        print("[RS Theme Ranker] ERROR: rs_universe/latest.json missing or empty.")
        print("  Run rs_ranker.py first.")
        return None

    print(f"  Universe loaded: {len(universe)} stocks")

    # Step 1: Compute theme RS from available universe members
    theme_data = compute_theme_rs(universe)

    # Step 2: Rank themes vs each other
    theme_data = rank_themes(theme_data)

    # Step 3: Add historical changes (4W, 13W)
    theme_data = add_theme_changes(theme_data)

    # Step 4: Save today's snapshot to history
    save_theme_snapshot(theme_data)

    # Step 5: Compute Strong Leader Scores
    leader_data = compute_strong_leader_scores(universe, theme_data)

    # Print outputs
    print_theme_heatmap(theme_data)
    print_strong_leaders(leader_data, top_n=15)

    # ── Build by_ticker index (O(1) lookup for other agents) ─────────────────
    # Source: ticker_labels.json (primary theme per ticker, one entry each)
    # Used by: nrgc_engine._get_theme_heat(), session_start.py theme display
    ticker_labels = _load_ticker_labels()
    by_ticker: dict = {}
    for ticker, theme_id in ticker_labels.items():
        td = theme_data.get(theme_id, {})
        comp       = td.get("theme_rs_composite")
        vs_pct     = td.get("theme_vs_themes_pct", 50.0) or 50.0
        phase      = td.get("phase", "NEUTRAL")
        heat       = "HOT" if vs_pct >= 75 else ("WARM" if vs_pct >= 40 else "WEAK")
        by_ticker[ticker] = {
            "primary_theme":        theme_id,
            "theme_name":           td.get("name", theme_id),
            "theme_heat":           heat,
            "theme_phase":          phase,
            "theme_rs_1m":          td.get("theme_rs_1m"),
            "theme_rs_3m":          td.get("theme_rs_3m"),
            "theme_rs_6m":          td.get("theme_rs_6m"),
            "theme_rs_composite":   comp,
            "theme_vs_themes_pct":  round(vs_pct, 1),
        }

    # Save theme output
    theme_output = {
        "date":        today,
        "computed_at": datetime.now().isoformat(),
        "formula":     "theme_rs_1m/3m/6m = avg(member rs_1m/3m/6m_pct) | composite = avg(1M,3M,6M)",
        "n_themes":    len(theme_data),
        "n_stocks":    len(universe),
        "n_labeled":   len(ticker_labels),
        "themes":      theme_data,
        # O(1) lookup per ticker
        "by_ticker":   by_ticker,
    }
    THEME_OUT_FILE.write_text(json.dumps(theme_output, indent=2, ensure_ascii=False))
    print(f"  Theme heatmap saved: {THEME_OUT_FILE} | by_ticker: {len(by_ticker)} entries")

    # Save strong leader output (sorted by score)
    sorted_leaders = dict(sorted(
        leader_data.items(),
        key=lambda x: x[1]["strong_leader_score"],
        reverse=True
    ))
    leader_output = {
        "date":        today,
        "computed_at": datetime.now().isoformat(),
        "formula":     "0.45*rs_market + 0.30*theme_vs_themes + 0.25*within_theme + momentum_bonus",
        "stocks":      sorted_leaders,
    }
    LEADER_OUT_FILE.write_text(json.dumps(leader_output, indent=2, ensure_ascii=False))
    print(f"  Strong leader scores saved: {LEADER_OUT_FILE}")

    return {"theme_data": theme_output, "leader_data": leader_output}


# ── Public API ────────────────────────────────────────────────────────────────

def get_theme_summary() -> list:
    """Quick read: returns list of themes sorted by composite RS, with phase and leaders."""
    if not THEME_OUT_FILE.exists():
        return []
    try:
        d = json.loads(THEME_OUT_FILE.read_text(encoding="utf-8"))
        themes = list(d.get("themes", {}).values())
        themes.sort(key=lambda x: x.get("theme_rs_composite") or 0, reverse=True)
        return themes
    except Exception:
        return []


def get_strong_leader_score(ticker: str) -> dict:
    """Quick read: returns strong leader score for a specific ticker."""
    if not LEADER_OUT_FILE.exists():
        return {}
    try:
        d = json.loads(LEADER_OUT_FILE.read_text(encoding="utf-8"))
        return d.get("stocks", {}).get(ticker, {})
    except Exception:
        return {}


def get_hot_themes(min_rs: float = 65.0) -> list:
    """Returns themes with composite RS above threshold, sorted desc."""
    themes = get_theme_summary()
    return [t for t in themes if (t.get("theme_rs_composite") or 0) >= min_rs]


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RS Theme Ranker")
    parser.add_argument("cmd", nargs="?", default="run",
                        choices=["run", "status", "themes", "leaders"])
    parser.add_argument("--ticker", type=str, default=None)
    args = parser.parse_args()

    if args.cmd == "run":
        run()

    elif args.cmd == "status":
        for f, label in [(THEME_OUT_FILE, "Theme heatmap"), (LEADER_OUT_FILE, "Leader scores")]:
            if f.exists():
                d = json.loads(f.read_text())
                print(f"{label}: {d.get('date')} | {d.get('n_stocks', d.get('n_themes', '?'))} items")
            else:
                print(f"{label}: NOT BUILT -- run rs_theme_ranker.py run")

    elif args.cmd == "themes":
        themes = get_theme_summary()
        print(f"\nTheme Heatmap ({len(themes)} themes):")
        print(f"  {'THEME':<25} {'1M':>5} {'3M':>5} {'6M':>5} {'Comp':>5} {'4W':>7}  PHASE     LEADERS")
        for t in themes:
            def _f(v): return f"{v:.0f}" if v is not None else " n/a"
            r1m   = _f(t.get("theme_rs_1m"))
            r3m   = _f(t.get("theme_rs_3m"))
            r6m   = _f(t.get("theme_rs_6m"))
            comp  = _f(t.get("theme_rs_composite"))
            chg   = t.get("chg_4w")
            chg_s = f"{chg:+.0f}pp" if chg is not None else "   n/a"
            ph    = t.get("phase", "?")
            leaders = ", ".join(m["ticker"] for m in t.get("leaders", [])[:2])
            print(f"  {t['name']:<25} {r1m:>5} {r3m:>5} {r6m:>5} {comp:>5} {chg_s:>7}  {ph:<10} {leaders}")

    elif args.cmd == "leaders":
        ticker = args.ticker
        if ticker:
            score = get_strong_leader_score(ticker)
            if score:
                print(f"\n{ticker} Strong Leader Score:")
                for k, v in score.items():
                    print(f"  {k}: {v}")
            else:
                print(f"{ticker} not found in strong leader data")
        else:
            if LEADER_OUT_FILE.exists():
                d = json.loads(LEADER_OUT_FILE.read_text())
                stocks = d.get("stocks", {})
                print(f"\nTop Strong Leaders ({d.get('date')}):")
                for ticker, s in list(stocks.items())[:15]:
                    print(f"  {ticker:<8} Score={s['strong_leader_score']:.1f} | "
                          f"Mkt={s['rs_market_pct']:.0f}th | "
                          f"Theme={s.get('theme_vs_themes_pct', 0):.0f}th | "
                          f"{s['label']} | {s.get('best_theme', 'N/A')}")
