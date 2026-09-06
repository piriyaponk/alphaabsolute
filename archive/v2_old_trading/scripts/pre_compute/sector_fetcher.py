"""
AlphaAbsolute — Sector Fetcher
==============================
Fetches Finnhub industry classification for unlabeled tickers,
maps to our sector labels, saves to SQLite, then triggers sector_labeler.

Usage:
  python scripts/pre_compute/sector_fetcher.py
  python scripts/pre_compute/sector_fetcher.py --dry-run   # preview only

Runtime: ~8 min for 487 tickers at 60 req/min (Finnhub free tier)
"""

from __future__ import annotations
import json
import re
import sys
import time
import sqlite3
import argparse
import os
from datetime import date
from pathlib import Path
from collections import Counter

import urllib3
urllib3.disable_warnings()
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR    = Path(__file__).resolve().parents[2]
LABELS_FILE = BASE_DIR / "data" / "themes" / "ticker_labels.json"
RS_LATEST   = BASE_DIR / "data" / "rs_universe" / "latest.json"
DB_PATH     = BASE_DIR / "data" / "ohlcv.db"

FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "")

# ══════════════════════════════════════════════════════════════════════════════
# Finnhub industry → our label mapping
# Finnhub uses broad categories. We refine using name for ambiguous ones.
# ══════════════════════════════════════════════════════════════════════════════

# Direct 1:1 mappings (unambiguous)
INDUSTRY_DIRECT: dict[str, str] = {
    # Finance
    "Banking":                    "Finance_Bank",
    "Banks":                      "Finance_Bank",
    "Insurance":                  "Finance_Insurance",
    "Life Insurance":             "Finance_Insurance",
    "Property & Casualty Insurance": "Finance_Insurance",
    "Investment Trusts/Mutual Funds": "Finance_Investment",
    "Specialty Finance":          "Finance_Fintech",
    "Mortgage Finance":           "Real_Estate",
    "Real Estate":                "Real_Estate",
    "REIT":                       "Real_Estate",

    # Healthcare
    "Biotechnology":              "Healthcare_Biotech",
    "Pharmaceuticals":            "Healthcare_Pharma",
    "Medical Devices":            "Healthcare_MedDevice",
    "Medical Equipment":          "Healthcare_MedDevice",
    "Medical Supplies":           "Healthcare_MedDevice",
    "Diagnostics & Research":     "Healthcare_MedDevice",
    "Hospital & Emergency Services": "Healthcare_Services",

    # Energy
    "Oil & Gas Exploration & Production": "Energy_OilGas",
    "Oil & Gas Refining & Marketing":     "Energy_OilGas",
    "Oil & Gas Equipment & Services":     "Energy_OilGas",
    "Oil & Gas Integrated":               "Energy_OilGas",
    "Oil & Gas Midstream":                "Energy_OilGas",
    "Coal":                       "Energy_OilGas",
    "Uranium":                    "Nuclear_SMR",
    "Solar":                      "Energy_Renewable",
    "Renewable Energy":           "Energy_Renewable",
    "Wind":                       "Energy_Renewable",

    # Materials
    "Metals & Mining":            "Materials",
    "Gold":                       "Materials",
    "Silver":                     "Materials",
    "Copper":                     "Materials",
    "Specialty Chemicals":        "Materials",
    "Chemicals":                  "Materials",
    "Steel":                      "Materials",
    "Aluminum":                   "Materials",
    "Agricultural Inputs":        "Consumer_Food",
    "Lumber & Wood Production":   "Materials",
    "Paper & Paper Products":     "Materials",

    # Consumer
    "Retail":                     "Consumer_Retail",
    "Apparel Retail":             "Consumer_Retail",
    "Apparel Manufacturing":      "Consumer_Retail",
    "Footwear":                   "Consumer_Retail",
    "Auto Parts":                 "Consumer_Auto",
    "Automotive":                 "Consumer_Auto",
    "Auto Dealerships":           "Consumer_Auto",
    "Recreational Vehicles":      "Consumer_Auto",
    "Restaurants":                "Consumer_Restaurant",
    "Fast Food":                  "Consumer_Restaurant",
    "Food Distribution":          "Consumer_Food",
    "Packaged Foods":             "Consumer_Food",
    "Beverages—Non-Alcoholic":    "Consumer_Food",
    "Beverages—Alcoholic":        "Consumer_Food",
    "Beverages":                  "Consumer_Food",
    "Food":                       "Consumer_Food",
    "Tobacco":                    "Consumer_Staples",
    "Household & Personal Products": "Consumer_Staples",
    "Hotels & Entertainment Services": "Consumer_Travel",
    "Hotels":                     "Consumer_Travel",
    "Casinos & Gaming":           "Consumer_Travel",
    "Travel Services":            "Consumer_Travel",
    "Airlines":                   "Transport",
    "Shipping":                   "Transport",
    "Air Freight & Logistics":    "Transport",
    "Trucking":                   "Transport",
    "Railroads":                  "Transport",
    "Marine Shipping":            "Transport",
    "Entertainment":              "Consumer_Media",
    "Media":                      "Consumer_Media",
    "Film & Entertainment":       "Consumer_Media",
    "Electronic Gaming & Multimedia": "Consumer_Media",
    "Advertising Agencies":       "Consumer_Media",
    "Publishing":                 "Consumer_Media",

    # Industrials
    "Aerospace & Defense":        "Industrials_Aerospace",
    "Construction":               "Industrials_Construction",
    "Engineering & Construction": "Industrials_Construction",
    "Homebuilding & Construction": "Industrials_Construction",
    "Building":                   "Industrials_Construction",
    "Machinery":                  "Industrials",
    "Industrial Machinery":       "Industrials",
    "Agricultural & Farm Machinery": "Industrials",
    "Diversified Industrials":    "Industrials",
    "Staffing & Employment Services": "Industrials",
    "Waste Management":           "Industrials",
    "Business Services":          "Industrials",
    "Commercial Services":        "Industrials",
    "Research & Development":     "Industrials",
    "Security & Protection Services": "Industrials",

    # Utilities
    "Utilities":                  "Utilities",
    "Electric Utilities":         "Utilities",
    "Gas Utilities":              "Utilities",
    "Water Utilities":            "Utilities",
    "Multi-Utilities":            "Utilities",

    # Telecom
    "Telecom Services":           "Telecom_Services",
    "Telecommunications":         "Telecom_Services",
    "Wireless Telecommunications": "Telecom_Services",

    # Cannabis
    "Cannabis":                   "Consumer_Staples",
    "Drugs":                      "Healthcare_Pharma",
}

# Ambiguous industries that need name-based disambiguation
INDUSTRY_AMBIGUOUS: dict[str, list[tuple[list[str], str]]] = {
    # "Technology" → check name for clues
    "Technology": [
        (["semiconductor", "chip", "fab", "wafer", "soc", "fpga", "asic", "analog", "rf semi",
          "microcontrol", "power ic", "sensor ic"], "Semiconductor"),
        (["photonic", "lidar", "optical", "laser", "fiber optic", "transceiver", "vcsel",
          "electro-optic", "coherent"], "Photonics"),
        (["quantum comput", "qubit", "quantum encrypt"], "Quantum"),
        (["bitcoin", "crypto mining", "blockchain mining", "proof of work"], "NeoCloud"),
        (["satellite", "space tech", "rocket", "spacecraft", "cubesat"], "Space"),
        (["drone", "uav", "unmanned aerial", "evtol", "air taxi"], "Drone_UAV"),
        (["robot", "autonomous vehicle", "self-driving", "lidar"], "Robotics"),
        (["artificial intelligence", "machine learning", "generative ai",
          "large language", "ai platform", "ml platform"], "AI_Related"),
        (["data center infra", "server cooling", "liquid cooling", "ups systems",
          "power distribution unit"], "DataCenter_Infra"),
        (["data center", "cloud computing", "cdn ", "content delivery", "colocation"], "DataCenter"),
        (["network", "connectivity", "ethernet", "switch", "router", "broadband", "5g",
          "wi-fi", "wireless chip", "telecom equip"], "Connectivity"),
        (["saas", "software", "cloud software", "enterprise software", "platform software",
          "data analytics", "cybersecur", "erp", "crm software", "fintech platform"], "Software"),
        ([], "Tech_Hardware"),  # default for Technology
    ],

    # "Health Care" → need subcategory
    "Health Care": [
        (["therapeutics", "biotech", "biopharma", "biologic", "oncology", "genomic",
          "gene therapy", "cell therapy", "immunology", "antibody", "mrna",
          "bioscience", "neuroscience", "clinical stage"], "Healthcare_Biotech"),
        (["pharma", "pharmaceutical", "drug", "medicines"], "Healthcare_Pharma"),
        (["medical device", "surgical", "orthopedic", "diagnostic", "ophthalmic",
          "implant", "cardiovascular device", "dental", "monitor", "imaging"], "Healthcare_MedDevice"),
        ([], "Healthcare_Services"),  # default
    ],

    # "Financial Services" → need subcategory
    "Financial Services": [
        (["bank", "bancorp", "savings", "credit union", "deposit", "lending"], "Finance_Bank"),
        (["insurance", "casualty", "reinsurance", "underwriter", "annuity"], "Finance_Insurance"),
        (["crypto", "bitcoin", "digital asset", "blockchain", "defi", "web3"], "Finance_Fintech"),
        (["payment", "fintech", "digital pay", "point of sale", "remittance",
          "money transfer", "neobank"], "Finance_Fintech"),
        (["reit", "real estate", "property", "mortgage reit"], "Real_Estate"),
        ([], "Finance_Investment"),  # default for asset mgmt, brokers etc
    ],

    # "Communications" → need subcategory
    "Communications": [
        (["satellite", "space comms", "starlink", "low earth orbit"], "Connectivity"),
        (["5g", "wireless carrier", "mobile carrier", "broadband", "cable",
          "fiber", "internet provider", "telecom"], "Telecom_Services"),
        (["entertainment", "streaming", "gaming", "media", "broadcast",
          "film", "music", "social media"], "Consumer_Media"),
        (["network equipment", "router", "switch", "ethernet chip",
          "optical transceiver", "network chip"], "Connectivity"),
        ([], "Telecom_Services"),  # default
    ],

    # "Diversified Consumer Services"
    "Diversified Consumer Services": [
        (["health", "wellness", "fitness", "medical"], "Healthcare_Services"),
        (["education", "school", "university", "tutoring", "elearning"], "Industrials"),
        (["security", "guard", "alarm", "monitoring"], "Industrials"),
        (["gaming", "casino", "entertainment venue", "amusement"], "Consumer_Travel"),
        ([], "Industrials"),  # default
    ],
}


def disambiguate(industry: str, name: str, description: str) -> str:
    """Use name + description to refine ambiguous broad industry categories."""
    combined = (name + " " + description).lower()

    if industry in INDUSTRY_DIRECT:
        return INDUSTRY_DIRECT[industry]

    if industry in INDUSTRY_AMBIGUOUS:
        for keywords, label in INDUSTRY_AMBIGUOUS[industry]:
            if not keywords:
                return label  # default
            if any(kw in combined for kw in keywords):
                return label

    # Partial match on industry string
    ind_lower = industry.lower()
    if "bank" in ind_lower:
        return "Finance_Bank"
    if "insurance" in ind_lower or "casualty" in ind_lower:
        return "Finance_Insurance"
    if "real estate" in ind_lower or "reit" in ind_lower:
        return "Real_Estate"
    if "biotech" in ind_lower:
        return "Healthcare_Biotech"
    if "pharma" in ind_lower or "drug" in ind_lower:
        return "Healthcare_Pharma"
    if "medical" in ind_lower or "health" in ind_lower:
        return "Healthcare_Services"
    if "oil" in ind_lower or "gas" in ind_lower or "petroleum" in ind_lower:
        return "Energy_OilGas"
    if "solar" in ind_lower or "renewable" in ind_lower or "wind" in ind_lower:
        return "Energy_Renewable"
    if "mining" in ind_lower or "metal" in ind_lower or "material" in ind_lower:
        return "Materials"
    if "software" in ind_lower or "saas" in ind_lower:
        return "Software"
    if "semiconductor" in ind_lower or "chip" in ind_lower:
        return "Semiconductor"
    if "airline" in ind_lower or "aviation" in ind_lower:
        return "Transport"
    if "retail" in ind_lower or "apparel" in ind_lower:
        return "Consumer_Retail"
    if "restaurant" in ind_lower or "food service" in ind_lower:
        return "Consumer_Restaurant"
    if "utility" in ind_lower or "utilities" in ind_lower:
        return "Utilities"
    if "telecom" in ind_lower or "wireless" in ind_lower:
        return "Telecom_Services"
    if "transport" in ind_lower or "logistics" in ind_lower or "freight" in ind_lower:
        return "Transport"
    if "construction" in ind_lower or "engineering" in ind_lower or "homebuilder" in ind_lower:
        return "Industrials_Construction"
    if "industrial" in ind_lower or "machinery" in ind_lower or "manufactur" in ind_lower:
        return "Industrials"
    if "entertainment" in ind_lower or "media" in ind_lower or "gaming" in ind_lower:
        return "Consumer_Media"
    if "travel" in ind_lower or "hotel" in ind_lower or "cruise" in ind_lower:
        return "Consumer_Travel"
    if "chemical" in ind_lower:
        return "Materials"
    if "consumer" in ind_lower:
        return "Consumer_Retail"

    return "Other"


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def get_other_tickers() -> list[str]:
    """Run sector_labeler dry-run to identify unlabeled tickers."""
    sys.path.insert(0, str(BASE_DIR))
    from scripts.pre_compute.sector_labeler import classify_sector, THEME_LABELS

    rs_data    = json.loads(RS_LATEST.read_text(encoding="utf-8"))
    universe   = [r["ticker"] for r in rs_data.get("ranked", [])]
    label_data = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    labels     = label_data["labels"]

    # Load SQLite
    db_info: dict[str, dict] = {}
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        cur  = conn.cursor()
        cur.execute("SELECT ticker, name, description, sic_code FROM company_info")
        for t, n, d, s in cur.fetchall():
            db_info[t] = {"name": n or "", "description": d or "", "sic": str(s or "")}
        conn.close()

    cache_dir = BASE_DIR / "data" / "themes" / "company_info_cache"
    others = []
    for t in universe:
        if t in labels and labels[t] in THEME_LABELS:
            continue  # already has theme label
        info = db_info.get(t, {})
        name = info.get("name", "")
        desc = info.get("description", "") or ""
        sic  = info.get("sic", "") or ""
        if not name:
            p = cache_dir / f"{t}.json"
            if p.exists():
                try:
                    c = json.loads(p.read_text(encoding="utf-8"))
                    name = c.get("name", "") or ""
                except Exception:
                    pass
        label, _ = classify_sector(t, name, desc, sic, labels.get(t))
        if label == "Other":
            others.append(t)
    return others


def run(dry_run: bool = False) -> None:
    today = date.today().isoformat()

    if not FINNHUB_KEY:
        print("ERROR: FINNHUB_API_KEY not set in .env")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Sector Fetcher — {today}")
    print(f"{'='*60}")
    print("  Identifying 'Other' tickers via sector_labeler dry-run...")
    others = get_other_tickers()
    print(f"  Found {len(others)} tickers still classified as 'Other'")

    if dry_run:
        print(f"\n  [dry-run] Would fetch Finnhub data for {len(others)} tickers.")
        print(f"  Estimated time: {len(others) / 60:.1f} minutes at 60 req/min")
        print(f"  Sample tickers: {others[:20]}")
        return

    if not others:
        print("  Nothing to fetch — all tickers classified!")
        return

    # Fetch Finnhub profiles
    fetched:  list[dict] = []
    skipped:  list[str]  = []
    by_industry: Counter = Counter()

    print(f"\n  Fetching Finnhub profiles ({len(others)} tickers)...")
    print(f"  Rate: 1 req/sec — ETA {len(others) // 60}m{len(others) % 60}s\n")

    for i, ticker in enumerate(others, 1):
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/stock/profile2",
                params={"symbol": ticker, "token": FINNHUB_KEY},
                timeout=8,
                verify=False,
            )
            if r.status_code == 200:
                d = r.json()
                industry = d.get("finnhubIndustry", "") or ""
                name     = d.get("name", "") or ""
                if industry:
                    fetched.append({
                        "ticker":   ticker,
                        "industry": industry,
                        "name":     name,
                    })
                    by_industry[industry] += 1
                else:
                    skipped.append(ticker)
            elif r.status_code == 429:
                print(f"  [{i}] Rate limited — sleeping 10s...")
                time.sleep(10)
                skipped.append(ticker)
            else:
                skipped.append(ticker)

        except Exception as e:
            skipped.append(ticker)

        # Progress
        if i % 50 == 0:
            print(f"  [{i}/{len(others)}] fetched={len(fetched)} skipped={len(skipped)}")

        time.sleep(1.05)  # ~57 req/min — safe under 60/min limit

    print(f"\n  Fetch complete:")
    print(f"    Fetched with industry: {len(fetched)}")
    print(f"    No industry (empty):   {len(skipped)}")
    print(f"\n  Top Finnhub industries seen:")
    for ind, cnt in by_industry.most_common(20):
        print(f"    {ind:40} {cnt:4}")

    # Load existing SQLite data for name fallback
    db_info_map: dict[str, dict] = {}
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        cur  = conn.cursor()
        cur.execute("SELECT ticker, name, description, sic_code FROM company_info")
        for t, n, d, s in cur.fetchall():
            db_info_map[t] = {"name": n or "", "description": d or ""}
        conn.close()

    # Map industry → our label
    new_labels:  dict[str, str] = {}
    new_sources: dict[str, str] = {}
    by_label: Counter = Counter()

    for entry in fetched:
        t        = entry["ticker"]
        industry = entry["industry"]
        name     = entry["name"]
        db_entry = db_info_map.get(t, {})
        desc     = db_entry.get("description", "") or ""
        full_name = db_entry.get("name", "") or name

        label = disambiguate(industry, full_name, desc)
        new_labels[t]  = label
        new_sources[t] = "finnhub"
        by_label[label] += 1

    print(f"\n  Label distribution from Finnhub:")
    for lbl, cnt in sorted(by_label.items(), key=lambda x: -x[1]):
        print(f"    {lbl:30} {cnt:4}")

    still_other = sum(1 for l in new_labels.values() if l == "Other")
    placed = len(new_labels) - still_other
    print(f"\n  Placed:     {placed}/{len(new_labels)}")
    print(f"  Still Other:{still_other}/{len(new_labels)}")

    # Save to ticker_labels.json — re-read the file FRESH right before writing
    # (avoids stale reads if sector_labeler wrote recently)
    label_data = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    labels     = label_data["labels"]
    sources    = label_data.get("sources", {})
    print(f"\n  File has {len(labels)} existing labels — merging {len(new_labels)} Finnhub results...")

    # Only write non-Other labels (Other tickers get written as-is for transparency)
    for t, lbl in new_labels.items():
        labels[t]  = lbl
        sources[t] = new_sources[t]

    label_data["labels"]  = labels
    label_data["sources"] = sources
    label_data["_meta"]["total_tickers"] = len(labels)
    label_data["_meta"]["last_updated"]  = today
    label_data["_meta"]["note"] = (
        "source: manual|kept|override|theme_desc|desc|sic4|sic2|name|finnhub|other"
    )

    LABELS_FILE.write_text(
        json.dumps(label_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n  Saved ticker_labels.json — {len(labels)} total labels")

    # Sync to SQLite
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        rows = [(t, new_labels[t], new_sources[t], today) for t in new_labels]
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
        print(f"  SQLite synced: {len(rows)} rows")

    print(f"\n  Done! Run sector_labeler.py next to finalize.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Finnhub industry for unlabeled tickers")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
