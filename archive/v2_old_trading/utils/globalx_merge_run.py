"""
globalx_merge_run.py  - ASCII only, no special chars
Merge Global X ETF tickers into AlphaAbsolute ticker_labels.json
"""
import json, os, sqlite3
from datetime import date

BASE = r"C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute"
LABELS_FILE = os.path.join(BASE, "data", "themes", "ticker_labels.json")
DB_FILE = os.path.join(BASE, "data", "ohlcv.db")

OFFICIAL_THEMES = {
    "AI_Related","Memory_HBM","Space","Quantum","Photonics",
    "DefenseTech","DataCenter","Nuclear_SMR","NeoCloud","AI_Infra",
    "DataCenter_Infra","Drone_UAV","Robotics","Connectivity"
}

# Phase 1: Direct new additions from Global X mapping
TARGET_MAPPING = {
    "TSM":  "Memory_HBM",
    "CLS":  "DataCenter",
    "NTNX": "DataCenter",
    "PATH": "Robotics",
    "NBIS": "AI_Related",
    "SNDK": "Memory_HBM",
    "RMBS": "Memory_HBM",
    "SITM": "Memory_HBM",
    "VECO": "Memory_HBM",
    "MTSI": "Memory_HBM",
    "POET": "Photonics",
    "LWLG": "Photonics",
    "EMKR": "Photonics",
    "FN":   "Photonics",
    "INFN": "Photonics",
    "MOD":  "DataCenter_Infra",
    "STRL": "DataCenter_Infra",
    "FIX":  "DataCenter_Infra",
    "PRIM": "DataCenter_Infra",
    "POWL": "DataCenter_Infra",
    "FCEL": "Nuclear_SMR",
    "NVT":  "AI_Infra",
    "HUBB": "AI_Infra",
    "FLNC": "AI_Infra",
    "STEM": "AI_Infra",
    "NVTS": "AI_Infra",
    "VICR": "AI_Infra",
    "EOSE": "AI_Infra",
    "WIRE": "AI_Infra",
    "ENVX": "AI_Infra",
    "SYM":  "Robotics",
    "ZBRA": "Robotics",
    "IRBT": "Robotics",
    "SERV": "Robotics",
    "LAZR": "Robotics",
    "LIDR": "Robotics",
    "MBLY": "Robotics",
    "RIVN": "Robotics",
    "LI":   "Robotics",
    "XPEV": "Robotics",
    "CHKP": "DefenseTech",
    "CYBR": "DefenseTech",
    "RPD":  "DefenseTech",
    "GEN":  "DefenseTech",
    "TLS":  "DefenseTech",
    "RDWR": "DefenseTech",
    "CFLT": "AI_Related",
    "FROG": "AI_Related",
    "PCOR": "AI_Related",
    "EH":   "Drone_UAV",
    "EVTL": "Drone_UAV",
    "STM":  "Memory_HBM",
    "COIN": "NeoCloud",
    "MSTR": "NeoCloud",
    "CAN":  "NeoCloud",
    "HIVE": "NeoCloud",
    "BITF": "NeoCloud",
    "HOOD": "NeoCloud",
    "EXTR": "DataCenter",
    "COMM": "DataCenter",
    "SEDG": "AI_Infra",
    "SLDP": "AI_Infra",
}

# Phase 2: Upgrade sector-labeled tickers to theme labels
UPGRADE_MAPPING = {
    "AEHR": ("Tech_Hardware",            "Memory_HBM"),
    "AOSL": ("Semiconductor",            "AI_Infra"),
    "CRDO": ("Semiconductor",            "DataCenter"),
    "GEV":  ("Industrials",              "AI_Infra"),
    "GNRC": ("Industrials",              "AI_Infra"),
    "ICHR": ("Semiconductor",            "Memory_HBM"),
    "IESC": ("Industrials_Construction", "DataCenter_Infra"),
    "ATKR": ("Industrials_Construction", "AI_Infra"),
    "MTZ":  ("Industrials_Construction", "DataCenter_Infra"),
    "LSCC": ("Semiconductor",            "AI_Infra"),
    "NNDM": ("Industrials",              "Robotics"),
    "NOVT": ("Tech_Hardware",            "Robotics"),
    "CGNX": ("Tech_Hardware",            "Robotics"),
    "ALAB": ("Connectivity",             "AI_Infra"),
    "ASTS": ("Connectivity",             "Space"),
    "BB":   ("Software",                 "DefenseTech"),
    "OKTA": ("Software",                 "DefenseTech"),
    "ZS":   ("Software",                 "DefenseTech"),
    "S":    ("Software",                 "DefenseTech"),
    "TENB": ("Software",                 "DefenseTech"),
    "VRNS": ("Software",                 "DefenseTech"),
    "QLYS": ("Software",                 "DefenseTech"),
    "APP":  ("Software",                 "AI_Related"),
    "ADBE": ("Software",                 "AI_Related"),
    "DOCN": ("Software",                 "DataCenter"),
    "GTLB": ("Software",                 "AI_Related"),
    "ESTC": ("Software",                 "AI_Related"),
    "TEAM": ("Software",                 "AI_Related"),
    "BILL": ("Software",                 "AI_Related"),
    "ASAN": ("Software",                 "AI_Related"),
    "HUBS": ("Software",                 "AI_Related"),
    "TTD":  ("Software",                 "AI_Related"),
    "DUOL": ("Software",                 "AI_Related"),
    "MDB":  ("Software",                 "AI_Related"),
}

def main():
    with open(LABELS_FILE, encoding='utf-8') as f:
        data = json.load(f)
    labels = data["labels"]
    sources = data.get("sources", {})

    added = {}
    upgraded = {}
    already_correct = []
    already_diff = []

    print("=" * 70)
    print("GLOBAL X ETF THEME MERGE - AlphaAbsolute v2")
    print("=" * 70)

    # --- Phase 1 ---
    print("\n--- PHASE 1: NEW TICKERS & SECTOR UPGRADES ---")
    for ticker in sorted(TARGET_MAPPING):
        target = TARGET_MAPPING[ticker]
        if ticker not in labels:
            labels[ticker] = target
            sources[ticker] = "manual"
            added[ticker] = (target, "new")
            print(f"  ADD  {ticker:<8} -> {target}")
        else:
            current = labels[ticker]
            if current in OFFICIAL_THEMES:
                if current == target:
                    already_correct.append(ticker)
                    print(f"  OK   {ticker:<8} {current} (already correct)")
                else:
                    already_diff.append((ticker, current, target))
                    print(f"  KEEP {ticker:<8} existing={current}, target_was={target} -- keeping")
            else:
                upgraded[ticker] = (current, target)
                labels[ticker] = target
                sources[ticker] = "manual"
                print(f"  UPGR {ticker:<8} {current} -> {target} (sector upgrade)")

    # --- Phase 2 ---
    print("\n--- PHASE 2: SECTOR->THEME UPGRADES ---")
    for ticker in sorted(UPGRADE_MAPPING):
        old_label, new_theme = UPGRADE_MAPPING[ticker]
        if ticker not in labels:
            labels[ticker] = new_theme
            sources[ticker] = "manual"
            added[ticker] = (new_theme, "upgrade-new")
            print(f"  ADD  {ticker:<8} -> {new_theme} (not in file)")
            continue
        current = labels[ticker]
        if current in OFFICIAL_THEMES:
            if current == new_theme:
                print(f"  OK   {ticker:<8} {current} (correct)")
            else:
                print(f"  KEEP {ticker:<8} existing={current}, target={new_theme} -- has theme already")
        else:
            upgraded[ticker] = (current, new_theme)
            labels[ticker] = new_theme
            sources[ticker] = "manual"
            print(f"  UPGR {ticker:<8} {current} -> {new_theme}")

    # --- Summary ---
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Brand new tickers added:           {len(added)}")
    print(f"  Sector->theme upgrades:            {len(upgraded)}")
    print(f"  Already in correct theme (kept):   {len(already_correct)}")
    print(f"  In different theme (kept):         {len(already_diff)}")

    if already_diff:
        print("\n  Kept existing (different theme from target):")
        for t, cur, tgt in already_diff:
            print(f"    {t:<8} existing={cur}, target_was={tgt}")

    theme_counts = {}
    for t, (theme, _) in added.items():
        theme_counts[theme] = theme_counts.get(theme, 0) + 1
    for t, (old, new) in upgraded.items():
        theme_counts[new] = theme_counts.get(new, 0) + 1

    print("\n  Changes per theme:")
    for theme in sorted(theme_counts):
        print(f"    {theme:<25} : +{theme_counts[theme]}")

    total = len(labels)
    manual_count = sum(1 for s in sources.values() if s == "manual")
    data["labels"] = labels
    data["sources"] = sources
    data["_meta"]["last_updated"] = str(date.today())
    data["_meta"]["total_tickers"] = total
    data["_meta"]["manual_entries"] = manual_count

    with open(LABELS_FILE, "w", encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    print(f"\nSaved {LABELS_FILE}")

    # SQLite sync
    if os.path.exists(DB_FILE):
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ticker_labels'")
        if cur.fetchone():
            count = 0
            for ticker, theme in labels.items():
                src = sources.get(ticker, "manual")
                cur.execute(
                    "INSERT INTO ticker_labels (ticker, theme, source, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(ticker) DO UPDATE SET "
                    "theme=excluded.theme, source=excluded.source, updated_at=excluded.updated_at",
                    (ticker, theme, src, str(date.today()))
                )
                count += 1
            conn.commit()
            conn.close()
            print(f"Synced {count} records to SQLite ticker_labels table")
        else:
            conn.close()
            print("ticker_labels table not in SQLite - skipping")
    else:
        print("SQLite DB not found - skipping sync")

    print(f"\nDone. Total tickers in file: {total}")

if __name__ == "__main__":
    main()
