"""
AlphaAbsolute -- RS Benchmark Builder  (weekly, Friday only)
=============================================================
Downloads S&P 500 + Nasdaq 100 + theme stocks (~300 tickers),
computes RS excess return vs SPY for 6 timeframes, then saves
the full percentile-breakpoint table so rs_ranker.py can give
true market-relative RS percentiles instead of within-watchlist.

Run:  Weekly on Friday (via pre_market_runner.py weekly_only=True)
Output:  data/rs_universe/benchmark_distribution.json
Cost:    $0 -- pure Python, Stooq + Yahoo fallback
"""

import json
import sys
import os
import time
from datetime import date, datetime
from pathlib import Path

import urllib3
urllib3.disable_warnings()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR  = BASE_DIR / "data" / "rs_universe"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DIST_FILE    = DATA_DIR / "benchmark_distribution.json"
TICKERS_FILE = DATA_DIR / "benchmark_tickers.json"   # separate file: list of tickers that succeeded

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


# ── Benchmark Universe (~300 tickers) ─────────────────────────────────────────
# Full S&P 500 core + Nasdaq 100 + S&P MidCap 400 (selected) + AlphaAbsolute themes
# ~625 tickers — true market-representative benchmark
# Updated manually ~quarterly. Refresh with S&P/Nasdaq membership changes.

BENCHMARK_TICKERS = sorted(set([

    # ═══════════════════════════════════════════════════════════════
    # NASDAQ 100 (full, as of 2025)
    # ═══════════════════════════════════════════════════════════════
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO", "COST",
    "NFLX", "AMD", "ADBE", "QCOM", "INTU", "AMAT", "MU", "LRCX", "KLAC", "MRVL",
    "PANW", "CRWD", "SNPS", "CDNS", "MCHP", "FTNT", "TXN", "PCAR", "PAYX", "MELI",
    "BKNG", "ASML", "WDAY", "DDOG", "TTD", "TEAM", "HUBS", "ANSS", "FAST", "VRSK",
    "CSGP", "IDXX", "BIIB", "DXCM", "ALGN", "PEP", "MDLZ", "KDP", "MNST", "ADP",
    "FISV", "PYPL", "ADSK", "EA", "NXPI", "SWKS", "ON", "MPWR", "ORCL", "CRM",
    "NOW", "SNOW", "MDB", "NET", "CFLT", "GTLB", "ZS", "OKTA", "ESTC",
    "ILMN", "REGN", "VRTX", "MRNA", "GEHC", "ROST", "ODFL", "CTAS", "CPRT",
    "FANG", "CTSH", "SPLK", "MTCH", "SIRI", "MAR", "HON",  # HON dual-listed
    "AEP", "EXC", "XEL",  # Utilities with Nasdaq listing

    # ═══════════════════════════════════════════════════════════════
    # S&P 500 — Financials (~60 stocks)
    # ═══════════════════════════════════════════════════════════════
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BRK-B", "V", "MA", "AXP",
    "SPGI", "MCO", "BLK", "SCHW", "ICE", "CME", "MSCI", "COF", "DFS", "SYF",
    "USB", "PNC", "TFC", "MTB", "RF", "FITB", "KEY", "HBAN", "CFG", "ALLY",
    "AFL", "MET", "PRU", "AIG", "HIG", "TRV", "CB", "ALL", "PGR", "L",
    "CINF", "LNC", "GL", "UNM", "MKL",
    "NDAQ", "CBOE", "MKTX", "LPLA", "RJF", "SEIC", "TROW", "IVZ",
    "FDS", "BR", "NTRS", "STT", "BK",

    # ═══════════════════════════════════════════════════════════════
    # S&P 500 — Healthcare (~65 stocks)
    # ═══════════════════════════════════════════════════════════════
    "UNH", "LLY", "JNJ", "PFE", "MRK", "ABBV", "BMY", "AMGN", "GILD",
    "TMO", "ISRG", "ABT", "SYK", "MDT", "EW", "BSX", "ZBH", "HOLX",
    "CVS", "CI", "HUM", "CNC", "ELV", "MOH",
    "DXCM", "ALGN", "IDXX", "BIIB", "REGN", "VRTX", "MRNA", "ILMN",
    "GEHC", "PODD", "INSP", "NVCR", "ACAD", "ALNY", "BMRN",
    "IQV", "DGX", "LH", "CRL", "MTD", "IQVIA", "RMD",
    "BAX", "BDX", "XRAY", "HAE", "SRDX",
    "HCA", "UHS", "THC", "DVA", "OMI",
    "MCK", "CAH", "ABC",   # distributors

    # ═══════════════════════════════════════════════════════════════
    # S&P 500 — Technology (~80 stocks)
    # ═══════════════════════════════════════════════════════════════
    "AAPL", "MSFT", "NVDA", "AVGO", "AMD", "QCOM", "TXN", "AMAT", "LRCX", "KLAC",
    "MU", "MRVL", "NXPI", "SWKS", "ON", "MPWR", "MCHP", "STX", "WDC",
    "ORCL", "CRM", "SAP", "INTU", "ADP", "FISV", "FIS", "PAYX",
    "IBM", "HPQ", "HPE", "DELL", "NCR", "NTAP", "PSTG",
    "ANET", "JNPR", "CSCO", "FFIV",
    "ADSK", "ANSS", "CDNS", "SNPS", "PTC", "DSGX",
    "NOW", "WDAY", "SMAR", "TWLO", "DDOG", "NET", "ZS", "PANW", "CRWD", "FTNT",
    "OKTA", "MDB", "SNOW", "GTLB", "HUBS", "TEAM", "FRSH", "SPRINKLR",
    "PAYC", "PCTY", "CDAY", "CGNT",
    "CTSH", "EPAM", "ACN", "IT", "VRSK",

    # ═══════════════════════════════════════════════════════════════
    # S&P 500 — Consumer Discretionary (~50 stocks)
    # ═══════════════════════════════════════════════════════════════
    "AMZN", "TSLA", "HD", "LOW", "COST", "WMT", "TGT", "BKNG", "MAR", "HLT",
    "NKE", "SBUX", "MCD", "CMG", "YUM", "QSR", "DPZ", "JACK",
    "ROST", "TJX", "ORLY", "AZO", "GPC",
    "F", "GM", "APTV", "BWA", "LEA", "MGA",
    "LEN", "DHI", "PHM", "NVR", "TOL", "MDC", "TPH",
    "RCL", "CCL", "NCLH", "LVS", "MGM", "WYNN", "CZR",
    "EBAY", "ETSY", "W", "CHWY", "MELI",
    "DIS", "NFLX", "PARA", "WBD", "FOXA",

    # ═══════════════════════════════════════════════════════════════
    # S&P 500 — Consumer Staples (~30 stocks)
    # ═══════════════════════════════════════════════════════════════
    "PG", "KO", "PEP", "PM", "MO", "MDLZ", "KHC", "GIS", "CPB", "SJM",
    "K", "HSY", "MKC", "CAG", "CHD", "CLX", "CL", "EL",
    "WMT", "COST", "KR", "SFM", "GO",
    "KDP", "MNST", "STZ",

    # ═══════════════════════════════════════════════════════════════
    # S&P 500 — Energy (~30 stocks)
    # ═══════════════════════════════════════════════════════════════
    "XOM", "CVX", "COP", "SLB", "EOG", "PXD", "OXY", "MPC", "PSX", "VLO",
    "HES", "DVN", "FANG", "BKR", "HAL", "NOV", "FTI",
    "LNG", "KMI", "WMB", "OKE", "ET", "EPD", "MPLX",
    "CNQ", "SU",

    # ═══════════════════════════════════════════════════════════════
    # S&P 500 — Industrials (~55 stocks)
    # ═══════════════════════════════════════════════════════════════
    "CAT", "DE", "HON", "MMM", "GE", "RTX", "LMT", "NOC", "GD", "BA",
    "UPS", "FDX", "CSX", "NSC", "UNP", "WAB", "XPO",
    "ETN", "EMR", "ROK", "PH", "ITW", "DOV", "IR",
    "AME", "FTV", "VRST", "ROP", "LDOS", "SAIC", "BAH",
    "CACI", "KTOS", "AVAV", "AXON", "TDG", "HWM", "SPR",
    "MSI", "BRKR", "GNSS", "KEYS", "TER", "MKSI",
    "WM", "RSG", "CWST", "CLH",
    "CSGP", "FAST", "ODFL", "SAIA", "JBHT", "KNX",

    # ═══════════════════════════════════════════════════════════════
    # S&P 500 — Materials (~25 stocks)
    # ═══════════════════════════════════════════════════════════════
    "LIN", "APD", "ECL", "SHW", "PPG", "NEM", "FCX", "AA", "CF", "MOS",
    "IP", "PKG", "SEE", "AVNT", "RPM", "HUN", "OLN",
    "STLD", "NUE", "RS", "CMC", "ATI",
    "CCJ", "UEC", "NNE",   # Uranium / nuclear materials

    # ═══════════════════════════════════════════════════════════════
    # S&P 500 — Utilities & REITs (~35 stocks)
    # ═══════════════════════════════════════════════════════════════
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "PCG", "SRE", "PEG", "XEL",
    "CEG", "VST", "NRG", "OKLO", "BWXT",   # Nuclear power operators
    "AMT", "PLD", "EQIX", "DLR", "CCI", "PSA", "O", "VICI", "EPR",
    "SPG", "MAC", "KIM", "REG",

    # ═══════════════════════════════════════════════════════════════
    # S&P 500 — Communications (~20 stocks)
    # ═══════════════════════════════════════════════════════════════
    "META", "GOOGL", "GOOG", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS",
    "CHTR", "LUMN", "WBD", "FOXA", "PARA",
    "TTWO", "EA", "ATVI", "RBLX", "U",

    # ═══════════════════════════════════════════════════════════════
    # S&P MidCap 400 — High-conviction growth names
    # ═══════════════════════════════════════════════════════════════
    # Software / SaaS
    "COUP", "VEEV", "ZI", "BILL", "SAMSF", "DOCN", "DLO", "WK",
    "QLYS", "VRNS", "TENB", "PING",
    # Semiconductors mid-cap
    "ONTO", "CAMT", "ACLS", "AEIS", "FORM", "COHU", "PLAB", "UCTT", "KLIC",
    "LSCC", "POWI", "AAOI", "ACMR", "IPGP",
    # Biotech mid-cap
    "EXAS", "NTRA", "RXRX", "BBIO", "RCKT", "CRNX",
    # Defense / GovTech mid-cap
    "CW", "HEI", "AXON", "MOOG",
    # Space / Drone
    "RKLB", "LUNR", "ASTS", "RDW", "PL", "SPCE", "ACHR", "JOBY", "RCAT", "AVAV",
    # Quantum
    "IONQ", "RGTI", "QUBT",
    # NeoCloud / Crypto Mining
    "CRWV", "CORZ", "CLSK", "MARA", "RIOT", "CIFR",
    # Nuclear / SMR
    "OKLO", "NNE", "SMR",
    # Photonics / Optical
    "LITE", "COHR", "MRAM",
    # AI
    "PLTR", "SOUN", "AI", "BBAI",

    # ═══════════════════════════════════════════════════════════════
    # AlphaAbsolute watchlist universe (always included for context)
    # ═══════════════════════════════════════════════════════════════
    "SMCI", "IBM", "INTC", "QRVO", "AZTA",
    "WDC", "STX",
    "ERIC", "NOK",
    "VRT", "APH", "EME", "PWR",
    "TER", "KTOS",

    # ═══════════════════════════════════════════════════════════════
    # RUSSELL 2000 — THEMATIC SUBSET
    # Added 2026-05-17: theme stocks from R2000 for RS percentile
    # statistical validity (need 25+ per theme). These are NOT in S&P
    # but are the competitive set within each AlphaAbsolute theme.
    # Source: Russell 2000 constituents + manual theme classification.
    # ═══════════════════════════════════════════════════════════════

    # ── AI / Machine Learning small-cap ──────────────────────────────────────
    "BBAI",   # BigBear.ai — defense AI analytics
    "VRNT",   # Verint Systems — AI analytics for CX/security
    "KYNDRYL","KD",  # IT services (IBM spinoff)
    "FORR",   # Forrester Research — AI research/advisory
    "PRCT",   # Procept BioRobotics — surgical AI (adjacent)
    "MTTR",   # Matterport — 3D/spatial AI
    "GFAI",   # Guardforce AI — security robotics
    "AEYE",   # AudioEye — AI accessibility

    # ── Memory / Semiconductor Equipment small-cap ────────────────────────────
    "UCTT",   # Ultra Clean Holdings — semiconductor parts/services
    "ACMR",   # ACM Research — semiconductor cleaning equipment
    "CAMT",   # Camtek — advanced packaging inspection (HBM-critical)
    "PLAB",   # Photronics — photomasks (every chip needs these)
    "COHU",   # Cohu — semiconductor test handlers
    "AEIS",   # Advanced Energy Industries — precision power
    "MKSI",   # MKS Instruments — process instruments, lasers
    "LSCC",   # Lattice Semiconductor — low-power FPGAs
    "FORM",   # FormFactor — wafer probe cards
    "KLIC",   # Kulicke & Soffa — wire bonding/advanced packaging
    "ONTO",   # Onto Innovation — process control metrology
    "ACLS",   # Axcelis Technologies — ion implant equipment

    # ── Space / Satellite small-cap ──────────────────────────────────────────
    "BKSY",   # BlackSky Technology — real-time satellite imagery
    "GSAT",   # Globalstar — mobile satellite comms
    "SATS",   # EchoStar (Hughes) — satellite broadband
    "VSAT",   # Viasat — satellite internet/defense comms
    "MNTS",   # Momentus — in-space transportation (speculative)
    "IRDM",   # Iridium Communications — LEO satellite network
    "SPCE",   # Virgin Galactic — commercial spaceflight
    "SATL",   # Satellogic — Earth observation data

    # ── Defense Tech / GovTech small-cap ─────────────────────────────────────
    "MRCY",   # Mercury Systems — defense electronics processing
    "CW",     # Curtiss-Wright — defense/industrial electronics
    "VSEC",   # VSE Corporation — defense MRO/logistics
    "DRS",    # Leonardo DRS — defense electronics (US subsidiary)
    "MOOG",   # Moog Inc — precision motion for defense/aerospace
    "PLXS",   # Plexus Corp — electronics manufacturing (defense/aerospace)
    "SPR",    # Spirit AeroSystems — airframe structures
    "WGRD",   # WideOpenWest? no — skip
    "CACI",   # CACI International — already in main list
    "KEYW",   # Enghouse Systems? — skip (Canadian)
    "FLIR",   # Teledyne FLIR — thermal imaging (now part of TDY)

    # ── Photonics / Optical Networking small-cap ──────────────────────────────
    "VIAV",   # Viavi Solutions — optical test/measurement
    "LUNA",   # Luna Innovations — fiber optic sensing
    "NOVT",   # Novanta — precision photonics/motion
    "IPAR",   # skip — wrong sector
    "LUMENTUM", # merged into LITE — skip
    "NPTN",   # NeoPhotonics — acquired by II-VI
    "VCNX",   # Vaccinex — skip (biotech)
    "AOSL",   # Alpha and Omega Semiconductor — power semis
    "CIEN",   # Ciena — optical networking (already in main list)
    "INFN",   # Infinera — optical networking transport
    "ADTN",   # ADTRAN Holdings — optical access networking

    # ── Nuclear / SMR / Uranium small-cap ────────────────────────────────────
    "UUUU",   # Energy Fuels — uranium/vanadium miner
    "UEC",    # Uranium Energy Corp — uranium developer (already in S&P section)
    "DNN",    # Denison Mines — uranium developer (TSX/NYSE)
    "NXE",    # NexGen Energy — high-grade uranium (TSX/NYSE)
    "EU",     # enCore Energy — uranium in-situ recovery
    "LTBR",   # Lightbridge — advanced nuclear fuel tech
    "UROY",   # Uranium Royalty Corp — uranium royalties
    "FLNC",   # Fluence Energy — grid-scale energy storage (nuclear adjacent)
    "BWXT",   # BWX Technologies — already in main list (mid-cap)
    "GEV",    # GE Vernova — grid/energy spinoff from GE
    "AMRC",   # Ameresco — renewable + nuclear energy services

    # ── NeoCloud / Crypto Compute small-cap ──────────────────────────────────
    "HUT",    # Hut 8 Corp — bitcoin mining/HPC
    "IREN",   # Iris Energy — bitcoin mining + AI compute
    "CIFR",   # Cipher Mining — bitcoin mining
    "BTDR",   # Bitdeer Technologies — mining + AI cloud
    "WULF",   # TeraWulf — nuclear-powered bitcoin mining
    "BTBT",   # Bit Brother — mining
    "APLD",   # Applied Digital — AI cloud/HPC
    "SDIG",   # Stronghold Digital Mining — waste-coal power + mining
    "ARIS",   # Aris Water Solutions (adjacent, skip)
    "GREE",   # Greenidge Generation — mining + power generation

    # ── AI Infrastructure / Power small-cap ──────────────────────────────────
    "GNRC",   # Generac Holdings — backup power generators (AI UPS)
    "HDSN",   # Hudson Technologies — refrigerants for cooling
    "NOVT",   # Novanta — precision motion/power (duplicate, ok)
    "AMPS",   # Altus Power — distributed solar (already in main)
    "GEV",    # GE Vernova — grid infrastructure (duplicate, ok)
    "MTZ",    # MasTec — electrical/telecom infrastructure
    "MYR",    # MYR Group — electrical contracting (data center builds)
    "MYRG",   # MYR Group (same, different class)
    "DY",     # Dycom Industries — specialty contracting
    "ROAD",   # Construction Partners — construction infra
    "WLDN",   # Willdan Group — energy infrastructure engineering
    "ARRY",   # Array Technologies — solar tracking for data center power

    # ── Drone / UAV small-cap ─────────────────────────────────────────────────
    "BYRN",   # Byrna Technologies — non-lethal/security systems
    "ASTR",   # Astra Space — small satellite launch
    "AEAC",   # skip
    "AMMO",   # AMMO Inc — ammunition + tech platform (adjacent)
    "SKYD",   # skip
    "GOVX",   # GeoVax Labs — skip (biotech)
    "BLDE",   # Blade Air Mobility — urban air mobility
    "EVEX",   # Eve Holding — eVTOL (Embraer spinoff)
    "LILM",   # Lilium NV — eVTOL (European, Nasdaq listed)
    "RZFLY",  # skip — OTC
    "SSKN",   # skip

    # ── Robotics / Automation small-cap ──────────────────────────────────────
    "NNDM",   # Nano Dimension — 3D printing/additive manufacturing robots
    "LAZR",   # Luminar Technologies — lidar for autonomous/robotic systems
    "AEVA",   # Aeva Technologies — 4D lidar
    "MVIS",   # MicroVision — automotive lidar
    "OUST",   # Ouster — lidar sensors
    "MKFG",   # Markforged — industrial 3D printing robots
    "CGNX",   # Cognex — machine vision (already mid-cap ~$8B)
    "BRKS",   # Brooks/Azenta — skip (dead ticker, use AZTA)
    "IRBT",   # iRobot — consumer robots (acquired by Amazon, delisted → remove)
    "RBOT",   # Vicarious Surgical — surgical robots
    "RBLX",   # Roblox — skip (gaming, not robotics)
    "NOVT",   # Novanta — precision motion components

    # ── Connectivity / Satcom small-cap ──────────────────────────────────────
    "CCOI",   # Cogent Communications — internet backbone/fiber
    "LUMN",   # Lumen Technologies — fiber/enterprise networking
    "CNSL",   # Consolidated Communications — fiber internet
    "ATNI",   # ATN International — emerging market telecom
    "IDT",    # IDT Corporation — telecom services
    "RBBN",   # Ribbon Communications — telecom software/hardware
    "GIMO",   # Gigamon — skip (acquired)
    "LBRD",   # Liberty Broadband — cable/broadband
    "CABO",   # Cable One — cable ISP
    "SHEN",   # Shenandoah Telecom — rural fiber/5G
    "LTRN",   # Lantern Pharma — skip
    "CALX",   # Calix — cloud/software for broadband providers

    # ── Quantum Computing adjacent small-cap ─────────────────────────────────
    "ARQQ",   # Arqit Quantum — quantum encryption (UK, Nasdaq ADR)
    "QMCO",   # Quantum Corporation — data management (adjacent)
    "MKSI",   # MKS Instruments — lasers critical for quantum hardware
    "VIAV",   # Viavi — optical test for quantum networks
    "CEVA",   # CEVA Inc — wireless IP (quantum comms adjacent)

    # ═══════════════════════════════════════════════════════════════
    # RUSSELL 2000 — BROAD LIQUID NAMES (ADTV > $5M, MktCap > $500M)
    # Purpose: statistical anchor for true "vs market" RS percentile.
    # These are NOT theme stocks — they represent the broad small-cap
    # market so percentile = "vs full US equity universe" not just
    # large-cap/theme. Updated 2026-05-17.
    # ═══════════════════════════════════════════════════════════════

    # Technology small-cap (liquid R2000)
    "AMKR",  # Amkor Technology — semiconductor packaging
    "FORM",  # FormFactor — wafer probe cards (already in thematic)
    "ICHR",  # Ichor Holdings — semiconductor fluid delivery
    "AMBA",  # Ambarella — edge AI vision chips
    "POWI",  # Power Integrations — power conversion ICs
    "DIOD",  # Diodes Inc — discrete semiconductors
    "ALGM",  # Allegro MicroSystems — magnetic sensors
    "RAMP",  # LiveRamp Holdings — data connectivity
    "BANDWIDTH", # skip — name not ticker
    "BAND",  # Bandwidth Inc — cloud comms API
    "JAMF",  # Jamf Holdings — Apple device management
    "ALKT",  # Alkami Technology — digital banking
    "PCVX",  # Vaxcyte — biopharma (skip — non-tech)
    "MXCT",  # MaxLinear? no — skip
    "SMTC",  # Semtech — analog/IoT (already thematic)
    "CEVA",  # CEVA Inc — wireless IP cores

    # Financial small-cap (R2000 — for sector balance)
    "CUBI",  # Customers Bancorp — specialty bank
    "BANF",  # BancFirst Corp — community bank
    "BOKF",  # BOK Financial — regional bank
    "CVBF",  # CVB Financial — community bank
    "EWBC",  # East West Bancorp — S&P 400 (keep for balance)
    "FBIZ",  # First Business Financial
    "HBCP",  # Home Bancorp
    "NBTB",  # NBT Bancorp
    "TRMK",  # Trustmark Corp
    "WAFD",  # Washington Federal

    # Healthcare small-cap (R2000)
    "ACAD",  # Acadia Pharmaceuticals — CNS drugs
    "TGTX",  # TG Therapeutics — oncology/autoimmune
    "RXRX",  # Recursion Pharma — AI drug discovery
    "NVCR",  # NovaCure — tumor treating fields
    "INVA",  # Innoviva — royalty pharma
    "HALO",  # Halozyme — drug delivery platform
    "ARWR",  # Arrowhead Pharma — RNAi
    "FOLD",  # Amicus Therapeutics — rare disease
    "KRYS",  # Krystal Biotech — gene therapy dermatology
    "TARS",  # Tarsus Pharma — eye disease

    # Consumer/Retail small-cap (R2000)
    "YETI",  # YETI Holdings — premium outdoor gear
    "CELH",  # Celsius Holdings — energy drinks (S&P 400)
    "XPOF",  # Xponential Fitness — fitness franchises
    "GSHD",  # Goosehead Insurance — insurance marketplace
    "PRGS",  # Progress Software — app dev platform
    "BCPC",  # Balchem Corp — specialty gases/nutrients
    "LANC",  # Lancaster Colony — specialty foods
    "JJSF",  # J&J Snack Foods
    "CHEF",  # The Chefs' Warehouse — food distribution
    "DINE",  # Dine Brands — restaurant franchises

    # Industrial small-cap (R2000)
    "AIT",   # Applied Industrial Technologies — industrial distribution
    "GMS",   # GMS Inc — building products distribution
    "KFRC",  # Kforce Inc — tech staffing
    "TREX",  # Trex Company — composite decking (S&P 400)
    "AAON",  # AAON Inc — HVAC units
    "IESC",  # IES Holdings — electrical/mechanical construction
    "MYRG",  # skip (already removed)
    "EXPO",  # Exponent Inc — engineering/science consulting
    "MSGE",  # skip
    "ASTE",  # Astec Industries — road building equipment
    "SITE",  # SiteOne Landscape Supply — landscape distribution
    "DY",    # Dycom Industries — specialty contracting (already thematic)

    # Energy small-cap (R2000)
    "CIVI",  # Civitas Resources — oil/gas E&P (S&P 400)
    "SM",    # SM Energy — oil/gas E&P
    "PDCE",  # PDC Energy — skip (acquired by Chevron 2023)
    "PR",    # Permian Resources — oil/gas E&P
    "CHRD",  # Chord Energy — Williston Basin E&P
    "NOG",   # Northern Oil & Gas — non-op E&P
    "MNRL",  # Brigham Minerals → now MNRL (royalty)
    "VTLE",  # Vital Energy — Permian E&P
    "REX",   # REX Energy → use REX (specialty industrial, not energy)
    "TPVG",  # skip — BDC
    "FLNG",  # Flex LNG — LNG shipping
    "CEIX",  # CONSOL Energy — coal/nat gas (adjacent)

    # ═══════════════════════════════════════════════════════════════
    # S&P 400 MidCap — additional liquid names not in S&P 500
    # (completes the "S&P universe" for RS percentile validity)
    # ═══════════════════════════════════════════════════════════════
    "EXLS",   # ExlService — AI-powered analytics BPO
    "GLOB",   # Globant — digital transformation/AI
    "EPAM",   # EPAM Systems — engineering services (already in list)
    "GDDY",   # GoDaddy — web/cloud services
    "DUOL",   # Duolingo — AI education
    "CELH",   # Celsius Holdings — consumer growth
    "SAIA",   # Saia Inc — trucking/logistics (already in list)
    "LNTH",   # Lantheus Holdings — nuclear imaging
    "INSP",   # Inspire Medical Systems — (already in health list)
    "KSPI",   # Kaspi.kz — Kazakhstan fintech (Nasdaq)
    "CRUS",   # Cirrus Logic — audio ICs for Apple ecosystem
    "DOCN",   # DigitalOcean — cloud infrastructure
    "NET",    # Cloudflare — already in Nasdaq list
    "DDOG",   # Datadog — already in Nasdaq list
    "HUBS",   # HubSpot — already in Nasdaq list
    "BILL",   # Bill.com — SMB payments automation
    "ZI",     # ZoomInfo — B2B intelligence
    "GTLB",   # GitLab — DevSecOps (already in Nasdaq list)
    "S",      # SentinelOne — AI-native cybersecurity
    "CRDO",   # Credo Technology — high-speed connectivity (data center)
    "ALAB",   # Astera Labs — PCIe/CXL connectivity for AI
    "MRVL",   # Marvell — already in main list
    "ARM",    # ARM Holdings — chip architecture IP
    "SMTC",   # Semtech — analog/mixed-signal (IoT/connectivity)
    "SWKS",   # Skyworks — already in main list

]))

# Remove invalid / delisted / ETFs / empty strings
_REMOVE = {
    # ── ETFs & non-equities ───────────────────────────────────────────────────
    "SPY", "QQQ", "IWM", "NAND",
    # ── Acquired / merged / renamed ───────────────────────────────────────────
    "XLNX",      # acquired by AMD
    "ATVI",      # acquired by MSFT
    "COUP",      # acquired / taken private
    "SPLK",      # acquired by Cisco
    "PING",      # acquired
    "NPTN",      # NeoPhotonics — acquired by II-VI/Coherent 2022, delisted
    "LUMENTUM",  # merged into LITE
    "II-VI", "IIVI", "FNSR",  # merged into COHR
    "FLIR",      # Teledyne FLIR — acquired by TDY 2021
    "GIMO",      # Gigamon — taken private by Elliott 2017
    "IRBT",      # iRobot — effectively stranded post-Amazon deal collapse, near-zombie
    "MTTR",      # Matterport — acquired by CoStar Group 2023, delisted
    "BRKS",      # renamed to AZTA (Azenta) 2021 — dead ticker
    # ── Bankrupt / delisted ───────────────────────────────────────────────────
    "SPCE",      # Virgin Galactic — Chapter 11 2024, no longer trades as SPCE
    "LILM",      # Lilium NV — bankrupt 2023
    "ASTR",      # Astra Space — halted operations 2022, near-delisted
    "MKFG",      # Markforged — went private
    "RBOT",      # Vicarious Surgical — very tiny, effectively delisted
    # ── Wrong sector / completely off-theme ───────────────────────────────────
    "IPAR",      # Inter Parfums — cosmetics
    "VCNX",      # Vaccinex — neurology biotech
    "GOVX",      # GeoVax Labs — biotech vaccines
    "SSKN",      # Strata Skin Sciences — dermatology
    "LTRN",      # Lantern Pharma — pharma
    "ARIS",      # Aris Water Solutions — water midstream
    "AMMO",      # AMMO Inc — ammunition
    "FORR",      # Forrester Research — market research, micro-cap
    # ── Invalid tickers (company names, not exchange symbols) ─────────────────
    "KYNDRYL",   # company name; ticker is KD
    # ── OTC / non-Nasdaq/NYSE ─────────────────────────────────────────────────
    "RZFLY",     # OTC only
    "SAMSF",     # Korean ADR, limited US data
    # ── Too speculative / near-zero revenue / illiquid ────────────────────────
    "AEAC",      # blank / shell
    "WGRD",      # not a real ticker
    "KEYW",      # Canadian (Enghouse = ENGH.TSX, not US-listed)
    "MNTS",      # Momentus — near-zero revenue, speculative
    "SATL",      # Satellogic — micro-cap, illiquid
    "BTBT",      # Bit Brother — very speculative crypto
    "SDIG",      # Stronghold Digital Mining — coal-mining crypto
    "GFAI",      # Guardforce AI — illiquid OTC
    "AEYE",      # AudioEye — micro-cap, not AI theme
    "RBLX",      # Roblox — gaming, not robotics
    "MYRG",      # not a valid ticker (MYR = MYR Group already in list)
    "BYRN",      # Byrna Technologies — non-lethal, micro-cap
    "BLDE",      # Blade Air Mobility — $30M market cap, too small
    # ── Other illiquid / too small ────────────────────────────────────────────
    "SRDX", "SPRINKLR", "CGNT", "DLO", "WK", "VRST", "GNSS",
    # R2000 additions — remove known bad/acquired
    "PDCE",      # acquired by Chevron 2023
    "BANDWIDTH", # company name not ticker
    "PCVX",      # biopharma, non-tech — outside themes
    "MXCT",      # not valid
    "FBIZ",      # micro-cap, illiquid
    "HBCP",      # micro-cap bank
    "NBTB",      # micro-cap bank
    "TRMK",      # micro-cap bank
    "WAFD",      # micro-cap bank
    "TPVG",      # BDC, not equity
    "REX",       # ambiguous ticker (multiple companies)
    "MNRL",      # royalty — not standalone
    "DINE",      # restaurant, low growth
    "MSGE",      # sports/entertainment
    "ASTE",      # micro-cap industrial
    "LANC",      # low-growth consumer staple
    "JJSF",      # micro-cap snack food
}
BENCHMARK_TICKERS = [t for t in BENCHMARK_TICKERS if t and t not in _REMOVE]


# ── Price fetch (reuse data_engine) ──────────────────────────────────────────
def _fetch_closes(ticker: str, period_days: int = 290) -> list:
    """
    Returns list of closing prices (oldest first). Empty list on failure.
    Priority: 1) ohlcv_prefetch disk cache  2) live data_engine fetch
    """
    # Priority 1: ohlcv_prefetch disk cache (written nightly at 4:30 PM)
    try:
        from scripts.pre_compute.ohlcv_prefetch import read_cache
        cached = read_cache(ticker)
        if cached.get("close") and len(cached["close"]) >= 30:
            return cached["close"]
    except Exception:
        pass

    # Priority 2: SQLite ohlcv.db (same DB used by rest of pipeline — zero network calls)
    try:
        import sqlite3
        db_path = BASE_DIR / "data" / "ohlcv.db"
        if db_path.exists():
            with sqlite3.connect(str(db_path)) as conn:
                rows = conn.execute(
                    "SELECT close FROM ohlcv WHERE ticker=? AND close IS NOT NULL ORDER BY date ASC",
                    (ticker,)
                ).fetchall()
            if len(rows) >= 30:
                return [float(r[0]) for r in rows][-period_days:]
    except Exception:
        pass
    return []


def _get_index_closes(period_days: int = 290) -> list:
    """
    Fetch QQQ (NASDAQ) closes as the RS benchmark index.
    RS formula: stock_price_ratio / index_price_ratio
    e.g. stock +5%, NASDAQ +2% → 1.05/1.02 = 1.0294 → RS = +2.94%
    """
    cache = DATA_DIR / "_index_cache.json"
    today = date.today().isoformat()
    if cache.exists():
        try:
            c = json.loads(cache.read_text())
            if c.get("date") == today and c.get("ticker") == "QQQ":
                return c["closes"]
        except Exception:
            pass
    closes = _fetch_closes("QQQ", period_days)
    if closes:
        cache.write_text(json.dumps({"date": today, "ticker": "QQQ", "closes": closes}), encoding="utf-8")
    return closes


def _rs_ratio(stock_closes: list, index_closes: list, days: int):
    """
    RS = (1 + stock_return) / (1 + index_return) - 1, expressed as %.
    Example: stock +5%, NASDAQ +2% → 1.05/1.02 - 1 = +2.94%
    This is the mathematically correct relative strength — multiplicative, not additive.
    The difference from additive excess is material when returns are large (>15%).
    """
    if len(stock_closes) < days + 1 or len(index_closes) < days + 1:
        return None
    s_start, s_end = stock_closes[-(days + 1)], stock_closes[-1]
    m_start, m_end = index_closes[-(days + 1)], index_closes[-1]
    if s_start <= 0 or m_start <= 0:
        return None
    stock_ratio = s_end / s_start    # e.g. 1.05 for +5%
    index_ratio = m_end / m_start    # e.g. 1.02 for +2%
    return round((stock_ratio / index_ratio - 1) * 100, 2)  # → +2.94%


# ── Percentile breakpoints builder ────────────────────────────────────────────
def _build_breakpoints(values: list) -> dict:
    """
    Given a sorted list of RS excess returns, return:
      - breakpoints at [1, 5, 10, 20, 30, 40, 50, 60, 70, 72, 75, 80, 85, 90, 95, 99]
        so we can quickly map any score -> market percentile
      - mean, median, std
    """
    if len(values) < 5:
        return {}
    vals = sorted(v for v in values if v is not None)
    n = len(vals)

    def pct_val(p):
        idx = p / 100 * (n - 1)
        lo  = int(idx)
        hi  = min(lo + 1, n - 1)
        return round(vals[lo] + (idx - lo) * (vals[hi] - vals[lo]), 3)

    pct_levels = [1, 5, 10, 20, 30, 40, 50, 60, 70, 72, 75, 80, 85, 90, 95, 99]
    mean_val = sum(vals) / n
    variance = sum((v - mean_val) ** 2 for v in vals) / n
    std_val  = variance ** 0.5

    return {
        "n":           n,
        "mean":        round(mean_val, 2),
        "std":         round(std_val, 2),
        "min":         round(vals[0], 2),
        "max":         round(vals[-1], 2),
        "breakpoints": {str(p): pct_val(p) for p in pct_levels},
    }


def _score_to_percentile(excess_return: float, dist: dict) -> float:
    """
    Map an excess return value to a market percentile using saved breakpoints.
    Returns 0-100 (float).
    """
    if not dist or "breakpoints" not in dist:
        return 50.0
    bps = dist["breakpoints"]

    # Convert to list of (percentile, threshold_value)
    pts = [(float(k), float(v)) for k, v in bps.items()]
    pts.sort(key=lambda x: x[0])

    # Below min
    if excess_return <= pts[0][1]:
        return pts[0][0] * excess_return / pts[0][1] if pts[0][1] != 0 else 0.0

    # Above max
    if excess_return >= pts[-1][1]:
        return pts[-1][0] + (100 - pts[-1][0]) * min((excess_return - pts[-1][1]) / max(abs(pts[-1][1]), 1), 1.0)

    # Interpolate between brackets
    for i in range(len(pts) - 1):
        lo_pct, lo_val = pts[i]
        hi_pct, hi_val = pts[i + 1]
        if lo_val <= excess_return <= hi_val:
            if hi_val == lo_val:
                return lo_pct
            t = (excess_return - lo_val) / (hi_val - lo_val)
            return round(lo_pct + t * (hi_pct - lo_pct), 1)

    return 50.0


# ── Main builder ─────────────────────────────────────────────────────────────
def build():
    """
    Weekly benchmark builder.
    Downloads all S&P+Nasdaq+Russell tickers (from universe_builder),
    computes RS ratio vs QQQ, builds percentile breakpoints.
    """
    # Use universe_builder for dynamic constituent list (auto-updated weekly)
    try:
        import sys as _sys
        _sys.path.insert(0, str(BASE_DIR))
        from scripts.pre_compute.universe_builder import get_universe
        _universe = get_universe()
        # Exclude QQQ (the index itself) and ETFs
        _exclude = {"QQQ", "SPY", "IWM", "GOOG", ""}
        universe_to_rank = [t for t in _universe if t not in _exclude]
        print(f"\n[RS Benchmark] Building market distribution | {date.today()}")
        print(f"  Universe: {len(universe_to_rank)} tickers (S&P+NDX+SP400+R2000 via universe_builder)")
    except Exception as e:
        print(f"\n[RS Benchmark] universe_builder failed ({e}) — using hardcoded BENCHMARK_TICKERS")
        universe_to_rank = [t for t in BENCHMARK_TICKERS if t not in {"QQQ","SPY","IWM",""}]
        print(f"  Universe: {len(universe_to_rank)} tickers (hardcoded fallback)")

    index_closes = _get_index_closes()
    if not index_closes:
        print("[RS Benchmark] ERROR: Cannot fetch QQQ (NASDAQ index) -- aborting.")
        return None
    print(f"  Index: QQQ (NASDAQ) | {len(index_closes)} bars")

    TF_DAYS = {"rs_1w": 5, "rs_2w": 10, "rs_1m": 21, "rs_3m": 63, "rs_6m": 126, "rs_12m": 252}

    # Accumulate RS ratio values per timeframe
    tf_values = {tf: [] for tf in TF_DAYS}
    ok_count   = 0
    err_count  = 0
    ok_tickers: list[str] = []   # tickers that successfully contributed to distribution

    for i, ticker in enumerate(universe_to_rank):
        if i % 50 == 0 and i > 0:
            print(f"  ... {i}/{len(universe_to_rank)} ({ok_count} ok, {err_count} err)")

        closes = _fetch_closes(ticker)
        if len(closes) < 25:
            err_count += 1
            continue

        got_any = False
        for tf, days in TF_DAYS.items():
            er = _rs_ratio(closes, index_closes, days)
            if er is not None:
                tf_values[tf].append(er)
                got_any = True

        if got_any:
            ok_count += 1
            ok_tickers.append(ticker)
        else:
            err_count += 1

        # Polite delay to avoid hammering data source
        time.sleep(0.15)

    print(f"  Done: {ok_count} ok, {err_count} failed out of {len(universe_to_rank)}")

    # Build distribution for each timeframe
    distributions = {}
    for tf, vals in tf_values.items():
        if len(vals) >= 10:
            distributions[tf] = _build_breakpoints(vals)
            print(f"  {tf}: n={distributions[tf]['n']} | "
                  f"median={distributions[tf]['breakpoints'].get('50','?')} | "
                  f"p72={distributions[tf]['breakpoints'].get('72','?')}")
        else:
            print(f"  {tf}: insufficient data ({len(vals)} values) -- skipped")

    output = {
        "date":            date.today().isoformat(),
        "computed_at":     datetime.now().isoformat(),
        "benchmark_size":  ok_count,
        "tickers_tried":   len(universe_to_rank),
        "distributions":   distributions,
        "index": "QQQ",
        "_note": (
            "Percentile breakpoints for 6 RS timeframes vs QQQ (NASDAQ). "
            "Formula: (1 + stock_ret) / (1 + qqq_ret) - 1, expressed as %. "
            "Use score_to_percentile(rs_ratio, dist[tf]) to get market percentile. "
            "Refresh weekly (Friday)."
        ),
    }

    DIST_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved: {DIST_FILE}")

    # Save separate ticker list so prewarm_analyst_cache.py can enumerate the full universe
    tickers_output = {
        "date":          date.today().isoformat(),
        "count":         len(ok_tickers),
        "tickers":       sorted(ok_tickers),
    }
    TICKERS_FILE.write_text(json.dumps(tickers_output, indent=2), encoding="utf-8")
    print(f"  Saved: {TICKERS_FILE} ({len(ok_tickers)} tickers)")

    return output


def score_to_percentile(excess_return: float, timeframe: str) -> float:
    """
    Public API: given an excess return % and timeframe key (rs_1m etc.),
    return market-relative percentile 0-100.
    Falls back to 50.0 if distribution not built yet.
    """
    if not DIST_FILE.exists():
        return None   # Signal to caller: no benchmark built yet
    try:
        dist_data = json.loads(DIST_FILE.read_text())
        dist = dist_data.get("distributions", {}).get(timeframe, {})
        return _score_to_percentile(excess_return, dist)
    except Exception:
        return None


def get_distribution_meta() -> dict:
    """Return distribution file metadata (date, size) for status checks."""
    if not DIST_FILE.exists():
        return {"built": False, "date": None, "benchmark_size": 0}
    try:
        d = json.loads(DIST_FILE.read_text())
        return {
            "built":          True,
            "date":           d.get("date"),
            "benchmark_size": d.get("benchmark_size", 0),
            "tickers_tried":  d.get("tickers_tried", 0),
        }
    except Exception:
        return {"built": False}


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RS Benchmark Builder")
    parser.add_argument("cmd", nargs="?", default="build",
                        choices=["build", "status", "test"])
    args = parser.parse_args()

    if args.cmd == "status":
        meta = get_distribution_meta()
        if meta["built"]:
            print(f"Benchmark built: {meta['date']} | "
                  f"{meta['benchmark_size']}/{meta['tickers_tried']} tickers OK")
        else:
            print("Benchmark NOT built yet -- run: python rs_benchmark.py build")

    elif args.cmd == "test":
        # Test percentile lookup
        for tf in ["rs_1w", "rs_1m", "rs_3m"]:
            for er in [-20, -5, 0, 5, 15, 30, 50]:
                pct = score_to_percentile(er, tf)
                if pct is not None:
                    print(f"  {tf} excess={er:+.0f}% -> {pct:.1f}th percentile")
                else:
                    print(f"  {tf}: no distribution built yet")

    else:  # build
        build()
