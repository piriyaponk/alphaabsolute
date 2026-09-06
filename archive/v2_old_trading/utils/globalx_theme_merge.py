"""
globalx_theme_merge.py
Merge Global X ETF ticker list into AlphaAbsolute ticker_labels.json

Rules:
1. Only add tickers NOT already in labels (with a theme label)
2. Tickers with SECTOR labels (not official themes) → upgrade if clearly a theme stock
3. For tickers already in correct theme → keep as-is
4. Skip QS (solid-state battery → no clear theme fit) and G17 cleantech
5. Source = 'manual' for all new/changed entries
"""

import json
import sqlite3
import os
from datetime import date

BASE = r"C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute"
LABELS_FILE = os.path.join(BASE, "data", "themes", "ticker_labels.json")
DB_FILE = os.path.join(BASE, "data", "ohlcv.db")

# Official AlphaAbsolute themes (not sector labels)
OFFICIAL_THEMES = {
    'AI_Related', 'Memory_HBM', 'Space', 'Quantum', 'Photonics',
    'DefenseTech', 'DataCenter', 'Nuclear_SMR', 'NeoCloud', 'AI_Infra',
    'DataCenter_Infra', 'Drone_UAV', 'Robotics', 'Connectivity'
}

# Full mapping: ticker -> target theme (from user instructions)
# Only tickers we want to ADD or potentially upgrade
TARGET_MAPPING = {
    # G1 — AI_Related + DataCenter + NeoCloud
    "TSM":  "Memory_HBM",       # chip foundry for AI/memory
    "CLS":  "DataCenter",       # Celestica - server/DC hardware
    "NTNX": "DataCenter",       # Nutanix - hyperconverged infra
    "PATH": "Robotics",         # UiPath - process automation
    "NBIS": "AI_Related",       # Nebius - AI cloud

    # G2 — Memory_HBM
    "SNDK": "Memory_HBM",       # SanDisk/WD NAND flash
    "RMBS": "Memory_HBM",       # Rambus - memory interface chips
    "SITM": "Memory_HBM",       # SiTime - timing semiconductors
    "VECO": "Memory_HBM",       # Veeco - semiconductor equipment for memory
    "MTSI": "Memory_HBM",       # MACOM - compound semiconductors

    # G3 — Photonics
    "POET": "Photonics",        # POET Technologies - optical interposers
    "LWLG": "Photonics",        # Lightwave Logic - electro-optic polymers
    "EMKR": "Photonics",        # EMCORE - photovoltaics/photonics
    "FN":   "Photonics",        # Fabrinet - optical manufacturing
    "INFN": "Photonics",        # Infinera - optical networking

    # G4 — DataCenter + DataCenter_Infra
    "MOD":  "DataCenter_Infra", # Modine - thermal management for DCs
    "STRL": "DataCenter_Infra", # Sterling Infrastructure - DC construction
    "FIX":  "DataCenter_Infra", # Comfort Systems - HVAC for DCs
    "PRIM": "DataCenter_Infra", # Primoris - electrical/civil construction
    "POWL": "DataCenter_Infra", # Powell Industries - electrical equipment
    "FCEL": "Nuclear_SMR",      # FuelCell Energy - clean power
    "NVT":  "AI_Infra",         # nVent Electric - electrical enclosures/cooling

    # G5 — AI_Infra
    "HUBB": "AI_Infra",         # Hubbell - electrical products
    "FLNC": "AI_Infra",         # Fluence Energy - energy storage
    "STEM": "AI_Infra",         # Stem Inc - AI energy storage
    "NVTS": "AI_Infra",         # Navitas Semiconductor - GaN power chips
    "VICR": "AI_Infra",         # Vicor - power components
    "EOSE": "AI_Infra",         # Eos Energy - battery storage
    "WIRE": "AI_Infra",         # Encore Wire - electrical wire for DCs
    "ENVX": "AI_Infra",         # Enovix - silicon batteries

    # G6 — Robotics
    "SYM":  "Robotics",         # Symbotic - warehouse robots
    "ZBRA": "Robotics",         # Zebra Technologies - industrial automation
    "IRBT": "Robotics",         # iRobot - consumer robots
    "SERV": "Robotics",         # Serve Robotics - delivery robots
    "LAZR": "Robotics",         # Luminar - LiDAR
    "LIDR": "Robotics",         # AEye - LiDAR
    "MBLY": "Robotics",         # Mobileye - autonomous driving
    "RIVN": "Robotics",         # Rivian - EV/autonomous
    "LI":   "Robotics",         # Li Auto - autonomous EV China
    "XPEV": "Robotics",         # XPeng - autonomous EV China

    # G7 — DefenseTech (cybersecurity)
    "CHKP": "DefenseTech",      # Check Point - cybersecurity
    "CYBR": "DefenseTech",      # CyberArk - identity security
    "RPD":  "DefenseTech",      # Rapid7 - cybersecurity
    "GEN":  "DefenseTech",      # Gen Digital - cybersecurity
    "TLS":  "DefenseTech",      # Telos - cyber/national security
    "RDWR": "DefenseTech",      # Radware - DDoS protection

    # G8 — AI_Related (AI software)
    "CFLT": "AI_Related",       # Confluent - data streaming for AI
    "FROG": "AI_Related",       # JFrog - DevOps/MLOps
    "PCOR": "AI_Related",       # Procore - construction AI software

    # G9 — DefenseTech + Space + Drone_UAV
    "EH":   "Drone_UAV",        # EHang - autonomous aerial vehicles
    "EVTL": "Drone_UAV",        # Vertical Aerospace - eVTOL

    # G10 — Robotics (autonomous/EV)
    "STM":  "Memory_HBM",       # STMicroelectronics - MCU/chips
    # ENVX already above

    # G11 — NeoCloud (blockchain/digital assets infra)
    "COIN": "NeoCloud",         # Coinbase - crypto exchange infra
    "MSTR": "NeoCloud",         # MicroStrategy - bitcoin treasury
    "CAN":  "NeoCloud",         # Canaan - mining hardware
    "HIVE": "NeoCloud",         # HIVE Digital - crypto mining
    "BITF": "NeoCloud",         # Bitfarms - crypto mining
    "HOOD": "NeoCloud",         # Robinhood - crypto trading infra

    # Additional from G3/G4 not yet covered
    "EXTR": "DataCenter",       # Extreme Networks - networking
    "COMM": "DataCenter",       # CommScope - network infrastructure
    "NNDM": "Robotics",         # Nano Dimension - 3D printing/additive mfg

    # G5 additional
    "SEDG": "AI_Infra",         # SolarEdge - power electronics
    "SLDP": "AI_Infra",         # Solid Power - solid-state batteries

    # Skip: QS (solid-state battery, no clear theme), G17 cleantech
    # Skip: FANUY (Fanuc - already labeled Robotics via manual)
    # Skip: AEVS (not in mapping)
}

# These tickers are already in the file with SECTOR labels — we want to upgrade them
# to the correct THEME label
UPGRADE_MAPPING = {
    # ticker: (current_sector_label, target_theme_label)
    "AEHR": ("Tech_Hardware", "Memory_HBM"),        # Aehr Test - semiconductor test equip for memory
    "AOSL": ("Semiconductor", "AI_Infra"),           # Alpha and Omega Semi - power management ICs
    "CRDO": ("Semiconductor", "DataCenter"),         # Credo Tech - high-speed SerDes for DCs
    "FLNC": ("Tech_Hardware", "AI_Infra"),           # Fluence Energy - energy storage
    "GEV":  ("Industrials", "AI_Infra"),             # GE Vernova - power/energy for AI DCs
    "GNRC": ("Industrials", "AI_Infra"),             # Generac - backup power for DCs
    "ICHR": ("Semiconductor", "Memory_HBM"),         # Ichor Holdings - semi equipment
    "IESC": ("Industrials_Construction", "DataCenter_Infra"),  # IES Holdings - electrical for DCs
    "ATKR": ("Industrials_Construction", "AI_Infra"), # Atkore - electrical conduit for DCs
    "MTZ":  ("Industrials_Construction", "DataCenter_Infra"),  # MasTec - fiber/electrical construction
    "LSCC": ("Semiconductor", "AI_Infra"),           # Lattice Semi - low-power FPGAs for edge AI
    "NNDM": ("Industrials", "Robotics"),             # Nano Dimension - additive mfg robotics
    "NOVT": ("Tech_Hardware", "Robotics"),           # Novanta - precision motion/robotics
    "CGNX": ("Tech_Hardware", "Robotics"),           # Cognex - machine vision for robotics
    "AELA": ("Semiconductor", "Memory_HBM"),         # (if exists)
    "SITM": ("Semiconductor", "Memory_HBM"),         # SiTime - already will be added as new
    "ALAB": ("Connectivity", "AI_Infra"),            # Astera Labs - PCIe/CXL for AI servers
    "ASTS": ("Connectivity", "Space"),               # AST SpaceMobile - satellite connectivity
    "BB":   ("Software", "DefenseTech"),             # BlackBerry - cybersecurity/IoT security
    "OKTA": ("Software", "DefenseTech"),             # Okta - identity security
    "ZS":   ("Software", "DefenseTech"),             # Zscaler - cloud security
    "S":    ("Software", "DefenseTech"),             # SentinelOne - cybersecurity
    "NET":  ("DataCenter", "DefenseTech"),           # Cloudflare - security/networking (already DC, keep)
    "TENB": ("Software", "DefenseTech"),             # Tenable - vulnerability management
    "VRNS": ("Software", "DefenseTech"),             # Varonis - data security
    "QLYS": ("Software", "DefenseTech"),             # Qualys - cloud security
    "APP":  ("Software", "AI_Related"),              # AppLovin - AI-powered ad tech
    "ADBE": ("Software", "AI_Related"),              # Adobe - creative AI
    "DOCN": ("Software", "DataCenter"),              # DigitalOcean - cloud infrastructure
    "GTLB": ("Software", "AI_Related"),              # GitLab - DevSecOps/AI code platform
    "ESTC": ("Software", "AI_Related"),              # Elastic - AI search
    "TEAM": ("Software", "AI_Related"),              # Atlassian - AI collaboration
    "BILL": ("Software", "AI_Related"),              # Bill.com - AI financial automation
    "ASAN": ("Software", "AI_Related"),              # Asana - AI work management
    "HUBS": ("Software", "AI_Related"),              # HubSpot - AI CRM
    "TTD":  ("Software", "AI_Related"),              # The Trade Desk - programmatic AI advertising
    "DUOL": ("Software", "AI_Related"),              # Duolingo - AI language learning
    "SNOW": ("AI_Related", None),                    # Already AI_Related — no change
    "DDOG": ("AI_Related", None),                    # Already AI_Related — no change
    "MDB":  ("Software", "AI_Related"),              # MongoDB - developer data platform
    "OKTA": ("Software", "DefenseTech"),             # Okta - identity/security
}

def load_labels():
    with open(LABELS_FILE, encoding='utf-8') as f:
        return json.load(f)

def save_labels(data):
    with open(LABELS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    print(f"\nSaved {LABELS_FILE}")

def sync_to_sqlite(labels_dict, sources_dict):
    if not os.path.exists(DB_FILE):
        print(f"  [SKIP] SQLite DB not found at {DB_FILE}")
        return
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    # Check if table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ticker_labels'")
    if not cur.fetchone():
        print("  [SKIP] ticker_labels table not found in SQLite")
        conn.close()
        return

    count = 0
    for ticker, theme in labels_dict.items():
        src = sources_dict.get(ticker, 'manual')
        cur.execute("""
            INSERT INTO ticker_labels (ticker, theme, source, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                theme=excluded.theme,
                source=excluded.source,
                updated_at=excluded.updated_at
        """, (ticker, theme, src, str(date.today())))
        count += 1

    conn.commit()
    conn.close()
    print(f"  Synced {count} records to SQLite ticker_labels table")

def main():
    data = load_labels()
    labels = data['labels']
    sources = data.get('sources', {})

    added = {}         # ticker -> (theme, reason)
    upgraded = {}      # ticker -> (old_label, new_theme)
    already_correct = []
    already_different_theme = []   # in a different OFFICIAL theme already
    skipped = []

    print("=" * 70)
    print("GLOBAL X ETF THEME MERGE — AlphaAbsolute")
    print("=" * 70)

    print("\n--- PHASE 1: NEW TICKERS (not in labels) ---")
    for ticker, target_theme in sorted(TARGET_MAPPING.items()):
        if ticker not in labels:
            labels[ticker] = target_theme
            sources[ticker] = 'manual'
            added[ticker] = (target_theme, 'new')
            print(f"  ADD  {ticker:8s} -> {target_theme}")
        else:
            current = labels[ticker]
            if current in OFFICIAL_THEMES:
                if current == target_theme:
                    already_correct.append(ticker)
                    print(f"  OK   {ticker:8s}  already={current} (correct theme, keep)")
                else:
                    already_different_theme.append((ticker, current, target_theme))
                    print(f"  KEEP {ticker:8s}  already={current} vs target={target_theme} — KEEPING current theme")
            else:
                # Has a sector label, not a theme label — upgrade
                upgraded[ticker] = (current, target_theme)
                labels[ticker] = target_theme
                sources[ticker] = 'manual'
                print(f"  UPGR {ticker:8s}  {current} => {target_theme} (sector->theme upgrade)")

    print("\n--- PHASE 2: SECTOR→THEME UPGRADES (existing sector-labeled tickers) ---")
    for ticker, (old_label, new_theme) in sorted(UPGRADE_MAPPING.items()):
        if new_theme is None:
            continue  # already correct
        if ticker not in labels:
            # Not in file at all — add it
            labels[ticker] = new_theme
            sources[ticker] = 'manual'
            added[ticker] = (new_theme, 'upgrade-new')
            print(f"  ADD  {ticker:8s} -> {new_theme} (upgrade-new, wasn't in file)")
            continue
        current = labels[ticker]
        if current in OFFICIAL_THEMES:
            if current == new_theme:
                print(f"  OK   {ticker:8s}  already={current} (correct theme)")
            else:
                print(f"  KEEP {ticker:8s}  already={current} vs target={new_theme} — KEEPING current theme (already has theme)")
        elif current == old_label:
            upgraded[ticker] = (current, new_theme)
            labels[ticker] = new_theme
            sources[ticker] = 'manual'
            print(f"  UPGR {ticker:8s}  {current} -> {new_theme}")
        else:
            print(f"  INFO {ticker:8s}  current={current} (different sector than expected {old_label}), target={new_theme} — upgrading anyway")
            if current not in OFFICIAL_THEMES:
                upgraded[ticker] = (current, new_theme)
                labels[ticker] = new_theme
                sources[ticker] = 'manual'

    # --- Summary ---
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Tickers added (brand new):  {len(added)}")
    print(f"  Tickers upgraded (sector->theme): {len(upgraded)}")
    print(f"  Already in correct theme:   {len(already_correct)}")
    print(f"  In different theme (kept):  {len(already_different_theme)}")

    if already_different_theme:
        print("\n  Tickers in different theme than target (we kept existing):")
        for t, cur, tgt in already_different_theme:
            print(f"    {t:8s} existing={cur}, target_was={tgt}")

    # Per-theme breakdown of what was added/upgraded
    theme_counts = {}
    for t, (theme, reason) in added.items():
        theme_counts[theme] = theme_counts.get(theme, 0) + 1
    for t, (old, new) in upgraded.items():
        theme_counts[new] = theme_counts.get(new, 0) + 1

    print("\n  New/upgraded entries per theme:")
    for theme in sorted(theme_counts):
        print(f"    {theme:25s}: +{theme_counts[theme]}")

    # Update meta
    total = len(labels)
    manual_count = sum(1 for s in sources.values() if s == 'manual')
    data['labels'] = labels
    data['sources'] = sources
    data['_meta']['last_updated'] = str(date.today())
    data['_meta']['total_tickers'] = total
    data['_meta']['manual_entries'] = manual_count

    save_labels(data)

    # Sync to SQLite
    print("\nSyncing to SQLite...")
    sync_to_sqlite(labels, sources)

    print("\nDone.")

if __name__ == '__main__':
    main()
