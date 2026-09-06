"""
Label Corrections — one-time patch to fix misclassifications found during audit.
Run once: python scripts/pre_compute/apply_label_corrections.py
"""
import json
from pathlib import Path

BASE_DIR    = Path(__file__).resolve().parents[2]
LABELS_FILE = BASE_DIR / "data" / "themes" / "ticker_labels.json"

CORRECTIONS = {
    # === AI_Related → correct category (keyword mis-fires) ===
    "ABLV": "Consumer_Retail",       # Able View Global - Chinese consumer goods
    "APEI": "Software",              # American Public Education - online edu
    "AROW": "Finance_Bank",          # Arrow Financial Corp - regional bank
    "AIRJ": "Energy_Renewable",      # AirJoule Technologies - water/energy tech
    "BLIN": "Software",              # Bridgeline Digital - digital marketing SaaS
    "BOTJ": "Finance_Bank",          # Bank of the James - community bank
    "BOX":  "Software",              # Box Inc - cloud content management
    "BRAI": "Tech_Hardware",         # Braiin - IoT hardware
    "DY":   "Industrials",           # Dycom - telecom infrastructure contractor

    # === Consumer_Auto → correct category ===
    "ADEA": "Software",              # Adeia - IP licensing platform (fmr TiVo)
    "AIHS": "Consumer_Travel",       # Senmiao Technology - ride-hailing China
    "WAB":  "Industrials",           # Wabtec - rail/transit equipment manufacturer

    # === Consumer_Food → correct category ===
    "AMBP": "Materials",             # Ardagh Metal Packaging - cans/packaging
    "ASH":  "Materials",             # Ashland Inc - specialty chemicals

    # === Consumer_Media → correct category ===
    "ANGI": "Software",              # Angi Inc - home services marketplace
    "APP":  "Software",              # Applovin - mobile ad tech
    "BMBL": "Software",              # Bumble - dating app
    "BOC":  "Finance_Investment",    # Boston Omaha - conglomerate
    "BCE":  "Telecom_Services",      # Bell Canada - major Canadian telco
    "CABO": "Telecom_Services",      # Cable One - ISP/cable provider
    "TTD":  "Software",              # The Trade Desk - programmatic adtech
    "AUUD": "Consumer_Media",        # Auddia - audio/podcast app (was Tech_Hardware)

    # === Consumer_Restaurant → correct category ===
    "AGAE": "Consumer_Media",        # Allied Gaming & Entertainment - esports
    "BALY": "Consumer_Travel",       # Bally's - casinos
    "BKNG": "Consumer_Travel",       # Booking Holdings - travel booking
    "BRAG": "Consumer_Media",        # Bragg Gaming - online gaming/casino
    "BRSL": "Consumer_Media",        # Brightstar Lottery - lottery
    "CHDN": "Consumer_Travel",       # Churchill Downs - racing/casinos
    "XPOF": "Healthcare_Services",   # Xponential Fitness - fitness studios

    # === Consumer_Retail → Industrials_Construction (homebuilders) ===
    "BLD":  "Industrials_Construction",   # TopBuild - insulation installer
    "DHI":  "Industrials_Construction",   # D.R. Horton - homebuilder
    "LEN":  "Industrials_Construction",   # Lennar - homebuilder
    "NVR":  "Industrials_Construction",   # NVR Inc - homebuilder
    "PHM":  "Industrials_Construction",   # Pultegroup - homebuilder
    "TOL":  "Industrials_Construction",   # Toll Brothers - homebuilder
    "TPH":  "Industrials_Construction",   # Tri Pointe Homes - homebuilder
    "BHST": "Healthcare_Biotech",         # BioHarvest Sciences - plant biotech
    "FAST": "Industrials",                # Fastenal - industrial fasteners dist.

    # === Finance_Bank → Finance_Investment (BDC mis-classified as bank) ===
    "BXSL": "Finance_Investment",    # Blackstone Secured Lending - BDC fund

    # === Finance_Investment → Finance_Bank (regional banks) ===
    "AUB":  "Finance_Bank",          # Atlantic Union Bankshares
    "BMO":  "Finance_Bank",          # Bank of Montreal
    "BNS":  "Finance_Bank",          # Bank of Nova Scotia
    "BOKF": "Finance_Bank",          # BOK Financial Corp
    "BY":   "Finance_Bank",          # Byline Bancorp
    "AMTB": "Finance_Bank",          # Amerant Bancorp
    "ALRS": "Finance_Bank",          # Alerus Financial Corp
    "AMAL": "Finance_Bank",          # Amalgamated Financial Corp

    # === Finance_Investment → other ===
    "AGPU": "NeoCloud",              # Axe Compute - GPU cloud compute
    "APPS": "Software",              # Digital Turbine - mobile advertising platform

    # === ETF_Fund → correct category ===
    "MSCI": "Finance_Investment",    # MSCI Inc - financial analytics (not ETF)

    # === Industrials → correct category ===
    "AMST": "Software",              # Amesite - edtech SaaS
    "AENT": "Consumer_Media",        # Alliance Entertainment - media distributor
    "CAPL": "Energy_OilGas",         # CrossAmerica Partners - fuel distribution
    "CHEF": "Consumer_Food",         # Chef's Warehouse - specialty food dist.
    "EBAY": "Consumer_Retail",       # eBay - e-commerce marketplace
    "ETSY": "Consumer_Retail",       # Etsy - artisan marketplace
    "ATEN": "Software",              # A10 Networks - app delivery/security
    "YETI": "Consumer_Retail",       # YETI Holdings - consumer products

    # === Industrials_Aerospace → correct category ===
    "AZ":   "Tech_Hardware",         # A2Z Cust2Mate - retail automation tech

    # === Materials → correct category ===
    "BPYPN": "Real_Estate",          # Brookfield Property Partners REIT preferred
    "BPYPO": "Real_Estate",          # Brookfield Property Partners REIT preferred
    "CHD":  "Consumer_Staples",      # Church & Dwight - consumer products
    "TREX": "Industrials_Construction",  # Trex Company - composite decking
    "HWM":  "Industrials_Aerospace", # Howmet Aerospace - aerospace components

    # === Real_Estate → correct category ===
    "AVBC": "Finance_Bank",          # Avidia Bancorp - bank
    "BATRA": "Consumer_Media",       # Atlanta Braves Holdings - sports/media
    "BATRK": "Consumer_Media",       # Atlanta Braves Holdings - sports/media
    "BPRN": "Finance_Bank",          # Princeton Bancorp - bank

    # === Tech_Hardware → Software (pure SaaS miscategorized) ===
    "AEYE": "Software",              # AudioEye - web accessibility SaaS
    "AGYS": "Software",              # Agilysys - hospitality management software
    "AIMD": "Healthcare_MedDevice",  # Ainos Inc - AI diagnostic health device
    "AIRE": "Real_Estate",           # reAlpha Tech - AI real estate platform
    "AISP": "DefenseTech",           # Airship AI - AI surveillance/security
    "AMPL": "Software",              # Amplitude - product analytics SaaS
    "APPF": "Software",              # AppFolio - property management software
    "APPN": "Software",              # Appian - low-code platform
    "ASAN": "Software",              # Asana - work management SaaS
    "BL":   "Software",              # BlackLine - accounting close software
    "BLKB": "Software",              # Blackbaud - nonprofit software
    "BLND": "Finance_Fintech",       # Blend Labs - digital lending software
    "CTSH": "Software",              # Cognizant Tech - IT services
    "DSGX": "Software",              # Descartes Systems - supply chain software
    "EPAM": "Software",              # EPAM Systems - software engineering
    "GLOB": "Software",              # Globant - tech services
    "IT":   "Software",              # Gartner - IT research/advisory
    "QLYS": "Software",              # Qualys - cloud security SaaS
    "SAP":  "Software",              # SAP SE - enterprise software

    # === Software → correct category ===
    "AIFF": "Healthcare_Services",   # Firefly Neuroscience - brain health AI
    "ASTH": "Healthcare_Services",   # Astrana Health - physician management
    "AMWL": "Healthcare_Services",   # Amwell - telehealth platform
    "APG":  "Industrials",           # APi Group - fire/life safety services
    "KFRC": "Industrials",           # Kforce - technology staffing

    # === Healthcare_Biotech → correct category ===
    "AIXC": "Finance_Investment",    # AIxCrypto Holdings - crypto/finance
    "ALP":  "NeoCloud",              # Alpha Compute Corp - GPU cloud compute

    # === Other → correct labels ===
    "AU":   "Materials",             # AngloGold Ashanti - gold mining
    "BROS": "Consumer_Restaurant",   # Dutch Bros - coffee chain
    "LPLA": "Finance_Investment",    # LPL Financial - wealth management
    "SPGI": "Finance_Investment",    # S&P Global - financial analytics
    "STZ":  "Consumer_Food",         # Constellation Brands - beverages (beer/wine)
    "TDG":  "Industrials_Aerospace", # TransDigm Group - aerospace components
    "RBBN": "Telecom_Services",      # Ribbon Communications - networking
    "AVPT": "Software",              # AvePoint - cloud data management
    "BKD":  "Healthcare_Services",   # Brookdale Senior Living - senior care
    "ANNX": "Healthcare_Biotech",    # Annexon - biotech
    "APOG": "Industrials",           # Apogee Enterprises - glass/services
    "AIOT": "Software",              # PowerFleet - fleet management SaaS
    "ALIT": "Software",              # Alight Inc - HR/benefits technology
    "ALNT": "Tech_Hardware",         # Allient Inc - electronic components
    "ADNT": "Consumer_Auto",         # Adient plc - automotive seating
    "ACFN": "Industrials",           # Acorn Energy - industrial IoT
    "AEC":  "Nuclear_SMR",           # Anfield Energy - uranium
    "BMR":  "Tech_Hardware",         # Beamr Imaging - video tech

    # === Utilities → correct category ===
    "ATRO": "Industrials_Aerospace", # Astronics Corp - aircraft systems
    "GREE": "NeoCloud",              # Greenidge Generation - crypto mining

    # === Connectivity → correct category ===
    "BNAI": "AI_Related",            # Brand Engagement Network - AI chatbot platform
}


def run():
    data    = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    labels  = data["labels"]
    sources = data.get("sources", {})

    changed       = 0
    skipped_manual = 0
    not_found     = []

    PROTECTED_SOURCES = {"manual"}

    for ticker, new_label in sorted(CORRECTIONS.items()):
        if ticker not in labels:
            not_found.append(ticker)
            continue
        old_label  = labels[ticker]
        old_source = sources.get(ticker, "")
        if old_source in PROTECTED_SOURCES:
            skipped_manual += 1
            print(f"  SKIP (manual) {ticker}: {old_label}")
            continue
        if old_label == new_label:
            continue
        labels[ticker]  = new_label
        sources[ticker] = "override"
        changed += 1
        print(f"  FIX  {ticker:8s}: {old_label:35s} -> {new_label}")

    print(f"\n  Changed  : {changed}")
    print(f"  Skipped  : {skipped_manual} (manual theme labels protected)")
    if not_found:
        print(f"  Not found: {not_found}")

    data["labels"]  = labels
    data["sources"] = sources
    LABELS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved ticker_labels.json")


if __name__ == "__main__":
    run()
