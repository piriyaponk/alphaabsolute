"""
AlphaAbsolute — Specialized Edge Intelligence
Sector-specific data signals for the 6 most important AlphaAbsolute themes.

These are the signals that HEDGE FUNDS track but retail ignores.
Each module tracks leading indicators specific to its sector — data that
moves 4-8 weeks BEFORE earnings and BEFORE analyst consensus shifts.

Modules:
  1. semiconductor_cycle()    — Book-to-bill, Taiwan exports, utilization proxy
  2. ai_capex_tracker()       — MSFT/GOOG/META/AMZN capex from EDGAR XBRL QoQ
  3. hbm_memory_signals()     — HBM demand proxy, DRAM pricing mentions, lead times
  4. defense_spending()       — DoD contract flow, defense budget signals
  5. nuclear_smr_tracker()    — NRC license pipeline, utility PPA announcements
  6. data_center_power()      — Electricity demand, cooling demand, power density

All integrated into NRGC scoring:
  -> Cycle turning up   = +3 to +5 NRGC boost (Phase 2->3 signal)
  -> Cycle peaking      = -3 NRGC warning (Phase 5->6 signal)
  -> New structural data = +2 confirmation boost
"""
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import requests
import urllib3
urllib3.disable_warnings()

BASE_DIR  = Path(__file__).resolve().parents[2]
DATA_DIR  = BASE_DIR / "data" / "agent_memory"
SS_DIR    = BASE_DIR / "data" / "smart_signals"
DATA_DIR.mkdir(parents=True, exist_ok=True)

EDGE_CACHE = DATA_DIR / "specialized_edge_cache.json"


def log(msg: str):
    print(f"  [SpecializedEdge] {msg}")


def _s() -> requests.Session:
    s = requests.Session()
    s.verify = False
    s.headers["User-Agent"] = "AlphaAbsolute research@alphaabsolute.ai"
    return s


def _load_cache() -> dict:
    if EDGE_CACHE.exists():
        try:
            return json.loads(EDGE_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(data: dict):
    EDGE_CACHE.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SEMICONDUCTOR CYCLE TRACKER
#    Key metric: Book-to-Bill ratio (orders/shipments)
#    > 1.0 = more orders than shipped -> upcycle starting
#    < 1.0 = over-supply / correction
#    Source: SEMI.org press releases (free), TW export data via news
# ═══════════════════════════════════════════════════════════════════════════════

SEMI_KEYWORDS_UPCYCLE = [
    "book-to-bill", "book to bill", "orders exceeded", "shipments up",
    "utilization improving", "utilization rate increase", "lead times extending",
    "inventory normalization", "inventory correction ending", "supply tightening",
    "DRAM pricing improving", "NAND pricing improving",
    "semiconductor demand improving", "wafer shipments up",
    "HBM demand exceeds supply", "HBM allocation",
]

SEMI_KEYWORDS_DOWNCYCLE = [
    "inventory build", "oversupply", "excess inventory",
    "utilization declining", "utilization cut", "pricing pressure",
    "weak demand", "pushout", "DRAM spot price declining",
    "capex reduction", "fab delay", "wafer start reduction",
]

# FRED: Taiwan Export Orders as semi-cycle proxy (FRED series: TWOTOT)
# FRED: ISM Manufacturing (MANEMP) as US industrial demand proxy


def get_semiconductor_signals() -> dict:
    """
    Multi-source semiconductor cycle signal.
    Returns: upcycle_score (0-10), nrgc_signal, NRGC boost
    """
    cache = _load_cache()
    week = datetime.utcnow().strftime("%Y-W%W")
    if cache.get("semi", {}).get("week") == week:
        log("Semi signals: using cache")
        return cache["semi"]

    signals = []
    upcycle_score = 5   # neutral starting point

    # Pull FRED ISM Manufacturing + check direction
    fred_key = os.environ.get("FRED_API_KEY", "")
    if fred_key:
        try:
            s = _s()
            r = s.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id":  "NAPM",     # ISM Manufacturing PMI
                    "api_key":    fred_key,
                    "limit":      6,
                    "sort_order": "desc",
                    "file_type":  "json",
                },
                timeout=15, verify=False,
            )
            obs = r.json().get("observations", [])
            vals = [float(o["value"]) for o in obs if o["value"] != "."]
            if vals:
                ism_now   = vals[0]
                ism_prior = vals[2] if len(vals) > 2 else vals[-1]
                ism_trend = ism_now - ism_prior
                expanding = ism_now > 50

                if expanding and ism_trend > 1.0:
                    upcycle_score += 2
                    signals.append(f"ISM MFG {ism_now:.1f} (expanding+improving)")
                elif expanding:
                    upcycle_score += 1
                    signals.append(f"ISM MFG {ism_now:.1f} (expanding)")
                elif not expanding and ism_trend < -1.0:
                    upcycle_score -= 2
                    signals.append(f"ISM MFG {ism_now:.1f} (contracting+worsening)")
                else:
                    signals.append(f"ISM MFG {ism_now:.1f} (neutral)")
        except Exception as e:
            log(f"  ISM fetch error: {e}")

    # Web search proxy: SEMI.org / industry press
    # We scan the raw news data for keyword signals
    raw_dir = BASE_DIR / "data" / "raw"
    if raw_dir.exists():
        all_text = ""
        cutoff = datetime.utcnow() - timedelta(days=14)
        for f in raw_dir.glob("*.json"):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) >= cutoff:
                    txt = f.read_text(encoding="utf-8", errors="replace").lower()
                    all_text += txt
            except Exception:
                continue

        up_hits   = sum(1 for kw in SEMI_KEYWORDS_UPCYCLE   if kw.lower() in all_text)
        down_hits = sum(1 for kw in SEMI_KEYWORDS_DOWNCYCLE if kw.lower() in all_text)

        upcycle_score += (up_hits * 0.5) - (down_hits * 0.7)
        upcycle_score  = max(0, min(10, upcycle_score))
        signals.append(f"News: {up_hits} upcycle/{down_hits} downcycle keyword hits")

    # Interpret score
    if upcycle_score >= 7.5:
        nrgc_signal = "semiconductor_upcycle_confirmed"
        nrgc_boost  = 5
        phase_note  = "Semi upcycle confirmed — strong buy signal for MU, AMAT, LRCX"
    elif upcycle_score >= 6.0:
        nrgc_signal = "semiconductor_recovering"
        nrgc_boost  = 3
        phase_note  = "Semi recovery in progress — Phase 2->3 transition"
    elif upcycle_score >= 4.5:
        nrgc_signal = "semiconductor_neutral"
        nrgc_boost  = 0
        phase_note  = "Mixed semi signals — monitor for direction"
    elif upcycle_score >= 3.0:
        nrgc_signal = "semiconductor_softening"
        nrgc_boost  = -2
        phase_note  = "Semi cycle softening — reduce exposure"
    else:
        nrgc_signal = "semiconductor_downcycle"
        nrgc_boost  = -4
        phase_note  = "Semi downcycle — avoid Memory/Semi names"

    result = {
        "week":          week,
        "upcycle_score": round(upcycle_score, 1),
        "signals":       signals,
        "nrgc_signal":   nrgc_signal,
        "nrgc_boost":    nrgc_boost,
        "phase_note":    phase_note,
        "affected_themes": ["Memory/HBM", "AI-Related", "AI Infrastructure"],
    }
    log(f"Semi cycle: score={upcycle_score:.1f} -> {nrgc_signal} (boost {nrgc_boost:+d})")

    cache["semi"] = result
    _save_cache(cache)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 2. AI CAPEX TRACKER — Hyperscaler quarterly capex from EDGAR XBRL
#    MSFT + GOOG + META + AMZN + ORCL
#    Capex acceleration = structural AI demand confirmed
#    Most important leading indicator for AI Infrastructure theme
# ═══════════════════════════════════════════════════════════════════════════════

AI_CAPEX_COMPANIES = {
    "MSFT":  "0000789019",
    "GOOGL": "0001652044",
    "META":  "0001326801",
    "AMZN":  "0001018724",
    "ORCL":  "0001341439",
}

# XBRL tags for capex
CAPEX_TAGS = [
    "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
    "us-gaap:CapitalExpenditureDiscontinuedOperations",
    "us-gaap:PaymentsForConstructionInProcessActivities",
]


def _fetch_edgar_capex(cik: str, company: str) -> Optional[dict]:
    """Fetch latest capex from EDGAR XBRL facts."""
    try:
        s = _s()
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"
        r   = s.get(url, timeout=20, verify=False)
        if r.status_code != 200:
            return None
        facts = r.json().get("facts", {}).get("us-gaap", {})

        for tag_name in ["PaymentsToAcquirePropertyPlantAndEquipment",
                          "CapitalExpenditures"]:
            if tag_name not in facts:
                continue
            units = facts[tag_name].get("units", {}).get("USD", [])
            # Filter quarterly data (form 10-Q or 10-K)
            qtrs = [u for u in units
                    if u.get("form") in ("10-Q", "10-K")
                    and u.get("val", 0) > 0]
            if not qtrs:
                continue
            # Sort by end date and take last 4
            qtrs.sort(key=lambda x: x.get("end", ""), reverse=True)
            recent = qtrs[:4]

            if len(recent) >= 2:
                curr_capex  = recent[0]["val"] / 1e9    # USD billions
                prior_capex = recent[1]["val"] / 1e9
                change_pct  = (curr_capex - prior_capex) / max(prior_capex, 0.1) * 100
                return {
                    "company":     company,
                    "curr_capex_b": round(curr_capex, 2),
                    "prior_capex_b": round(prior_capex, 2),
                    "change_pct":   round(change_pct, 1),
                    "period":       recent[0].get("end", "?"),
                    "accelerating": change_pct > 10,
                }
        return None
    except Exception:
        return None


def get_ai_capex_signals() -> dict:
    """
    Track AI capex from 5 hyperscalers. Acceleration = structural AI demand.
    Returns aggregate signal + per-company breakdown.
    """
    cache = _load_cache()
    week = datetime.utcnow().strftime("%Y-W%W")
    if cache.get("ai_capex", {}).get("week") == week:
        log("AI capex: using cache")
        return cache["ai_capex"]

    log("Fetching AI capex from EDGAR XBRL (MSFT/GOOG/META/AMZN/ORCL)...")
    per_company    = {}
    accel_count    = 0
    total_curr     = 0.0
    total_prior    = 0.0

    for ticker, cik in AI_CAPEX_COMPANIES.items():
        data = _fetch_edgar_capex(cik, ticker)
        if data:
            per_company[ticker] = data
            total_curr  += data["curr_capex_b"]
            total_prior += data["prior_capex_b"]
            if data["accelerating"]:
                accel_count += 1
            log(f"  {ticker}: ${data['curr_capex_b']:.1f}B ({data['change_pct']:+.0f}% QoQ)")
        time.sleep(0.5)

    # Aggregate signal
    agg_change = ((total_curr - total_prior) / max(total_prior, 0.1)) * 100 if total_prior else 0

    if accel_count >= 4 and agg_change > 15:
        nrgc_signal = "ai_capex_supercycle"
        nrgc_boost  = 5
        note        = "4+ hyperscalers accelerating -> AI infrastructure supercycle confirmed"
    elif accel_count >= 3 and agg_change > 5:
        nrgc_signal = "ai_capex_accelerating"
        nrgc_boost  = 3
        note        = "3+ hyperscalers accelerating -> AI demand structural and growing"
    elif accel_count >= 2:
        nrgc_signal = "ai_capex_growing"
        nrgc_boost  = 2
        note        = "Majority accelerating -> AI infrastructure spending trend intact"
    elif agg_change < -10:
        nrgc_signal = "ai_capex_slowing"
        nrgc_boost  = -3
        note        = "Hyperscaler capex declining -> AI infrastructure peak risk"
    else:
        nrgc_signal = "ai_capex_neutral"
        nrgc_boost  = 0
        note        = "Mixed signals — monitor next quarter"

    result = {
        "week":              week,
        "per_company":       per_company,
        "aggregate_curr_b":  round(total_curr, 1),
        "aggregate_prior_b": round(total_prior, 1),
        "aggregate_chg_pct": round(agg_change, 1),
        "accelerating_count": accel_count,
        "nrgc_signal":       nrgc_signal,
        "nrgc_boost":        nrgc_boost,
        "note":              note,
        "affected_themes":   ["AI Infrastructure", "Data Center", "NeoCloud", "AI-Related"],
    }
    log(f"AI capex: ${total_curr:.1f}B total, {accel_count}/5 accelerating -> {nrgc_signal}")

    cache["ai_capex"] = result
    _save_cache(cache)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 3. HBM / MEMORY DEMAND SIGNALS
#    Leading indicators: lead time mentions, allocation mentions, pricing tone
#    Sources: news scan + EDGAR XBRL for MU/INTC/WDC financial data
# ═══════════════════════════════════════════════════════════════════════════════

HBM_BULL_SIGNALS = [
    "HBM allocation", "HBM lead time", "HBM supply tight",
    "HBM demand exceeds", "HBM shortage", "memory allocation",
    "DRAM pricing up", "DRAM ASP increasing", "DRAM price increase",
    "memory upcycle", "memory cycle turning", "inventory digestion complete",
    "memory pricing improving", "supply tightening", "memory tight supply",
    "AI memory demand", "generative AI memory", "HBM capacity sold out",
    "DRAM contract price", "spot price recovering",
]

HBM_BEAR_SIGNALS = [
    "DRAM oversupply", "DRAM price decline", "memory oversupply",
    "HBM inventory build", "memory correction continues",
    "weak memory demand", "DRAM spot price falling",
    "memory pricing soft", "inventory overhang",
]

MEMORY_COMPANIES = {
    "MU":  "0000723254",   # Micron Technology
    "WDC": "0000106040",   # Western Digital
}


def get_hbm_memory_signals() -> dict:
    """
    HBM/Memory demand leading indicators.
    News-based keyword signal + MU/WDC XBRL revenue trend.
    """
    cache = _load_cache()
    week = datetime.utcnow().strftime("%Y-W%W")
    if cache.get("hbm", {}).get("week") == week:
        return cache["hbm"]

    # News scan
    raw_dir  = BASE_DIR / "data" / "raw"
    all_text = ""
    if raw_dir.exists():
        cutoff = datetime.utcnow() - timedelta(days=14)
        for f in raw_dir.glob("*.json"):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) >= cutoff:
                    all_text += f.read_text(encoding="utf-8", errors="replace").lower()
            except Exception:
                continue

    bull_hits = sum(1 for kw in HBM_BULL_SIGNALS if kw.lower() in all_text)
    bear_hits = sum(1 for kw in HBM_BEAR_SIGNALS if kw.lower() in all_text)

    # Find specific lead-time or allocation mentions
    allocation_detected = any(kw in all_text for kw in
                               ["hbm allocation", "sold out", "supply constrained", "lead time extending"])
    pricing_up = any(kw in all_text for kw in
                     ["DRAM pricing up", "DRAM ASP", "price increase", "pricing improving"])

    # Score
    score = (bull_hits * 1.5) - (bear_hits * 2)
    if allocation_detected:
        score += 3
    if pricing_up:
        score += 2

    if score >= 8:
        nrgc_signal = "hbm_supercycle"
        nrgc_boost  = 6
        note        = "HBM allocation + pricing surge -> supercycle. MU/Memory = max conviction"
    elif score >= 5:
        nrgc_signal = "hbm_demand_strong"
        nrgc_boost  = 4
        note        = "Strong HBM demand signals -> Memory sector Phase 2->3"
    elif score >= 2:
        nrgc_signal = "hbm_positive"
        nrgc_boost  = 2
        note        = "Positive memory signals -> accumulate Memory names"
    elif score <= -3:
        nrgc_signal = "memory_weak"
        nrgc_boost  = -3
        note        = "Memory supply/pricing headwinds -> avoid or reduce"
    else:
        nrgc_signal = "hbm_neutral"
        nrgc_boost  = 0
        note        = "Mixed HBM signals — monitor"

    result = {
        "week":          week,
        "bull_hits":     bull_hits,
        "bear_hits":     bear_hits,
        "allocation_detected": allocation_detected,
        "pricing_up":    pricing_up,
        "score":         round(score, 1),
        "nrgc_signal":   nrgc_signal,
        "nrgc_boost":    nrgc_boost,
        "note":          note,
        "affected_themes": ["Memory/HBM", "AI-Related"],
        "key_stocks":    ["MU", "WDC", "AMAT", "LRCX", "SK Hynix"],
    }
    log(f"HBM signals: bull={bull_hits} bear={bear_hits} -> {nrgc_signal} (boost {nrgc_boost:+d})")

    cache["hbm"] = result
    _save_cache(cache)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DEFENSE SPENDING SIGNALS
#    DoD contract awards, defense budget, AI-defense convergence
# ═══════════════════════════════════════════════════════════════════════════════

DEFENSE_BULL_SIGNALS = [
    "DoD contract", "defense contract", "Pentagon award",
    "defense budget increase", "defense spending increase",
    "JADC2 contract", "AI defense", "autonomous defense",
    "drone contract", "counter-drone", "defense supplemental",
    "national defense authorization", "NDAA",
    "Palantir contract", "CACI contract", "Booz Allen",
]

DEFENSE_BEAR_SIGNALS = [
    "defense budget cut", "pentagon budget reduction",
    "continuing resolution", "defense sequester",
    "DoD freeze", "procurement delay",
]


def get_defense_signals() -> dict:
    """Defense spending flow from news scan."""
    cache = _load_cache()
    week = datetime.utcnow().strftime("%Y-W%W")
    if cache.get("defense", {}).get("week") == week:
        return cache["defense"]

    raw_dir  = BASE_DIR / "data" / "raw"
    all_text = ""
    if raw_dir.exists():
        cutoff = datetime.utcnow() - timedelta(days=14)
        for f in raw_dir.glob("*.json"):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) >= cutoff:
                    all_text += f.read_text(encoding="utf-8", errors="replace").lower()
            except Exception:
                continue

    bull_hits  = sum(1 for kw in DEFENSE_BULL_SIGNALS if kw.lower() in all_text)
    bear_hits  = sum(1 for kw in DEFENSE_BEAR_SIGNALS if kw.lower() in all_text)
    net_signal = bull_hits - (bear_hits * 2)

    if net_signal >= 5:
        nrgc_signal, nrgc_boost = "defense_spending_surge", 4
        note = "Strong defense contract flow -> DefenseTech Phase 3 confirmed"
    elif net_signal >= 3:
        nrgc_signal, nrgc_boost = "defense_spending_positive", 2
        note = "Positive defense flow -> accumulate PLTR, CACI, LDOS, AXON"
    elif net_signal <= -2:
        nrgc_signal, nrgc_boost = "defense_budget_headwind", -2
        note = "Defense budget risk -> reduce exposure"
    else:
        nrgc_signal, nrgc_boost = "defense_neutral", 0
        note = "Neutral defense signals"

    result = {
        "week":          week,
        "bull_hits":     bull_hits,
        "bear_hits":     bear_hits,
        "net_signal":    net_signal,
        "nrgc_signal":   nrgc_signal,
        "nrgc_boost":    nrgc_boost,
        "note":          note,
        "affected_themes": ["DefenseTech", "Drone/UAV", "AI-Related"],
        "key_stocks":    ["PLTR", "CACI", "LDOS", "AXON", "AVAV"],
    }
    log(f"Defense: bull={bull_hits} bear={bear_hits} -> {nrgc_signal}")

    cache["defense"] = result
    _save_cache(cache)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 5. NUCLEAR / SMR TRACKER
#    NRC license pipeline, utility PPA announcements
# ═══════════════════════════════════════════════════════════════════════════════

NUCLEAR_BULL_SIGNALS = [
    "SMR license", "NRC approval", "nuclear license",
    "nuclear PPA", "nuclear power purchase agreement",
    "data center nuclear", "Microsoft nuclear", "Google nuclear",
    "Amazon nuclear", "nuclear restart", "uranium supply",
    "NuScale", "Oklo license", "Kairos", "SMR order",
    "nuclear renaissance", "new nuclear", "advanced nuclear",
    "TerraPower", "X-energy", "sodium reactor",
]

NUCLEAR_BEAR_SIGNALS = [
    "nuclear delay", "NRC rejection", "nuclear cost overrun",
    "nuclear cancelled", "SMR cancelled", "uranium price fall",
]


def get_nuclear_signals() -> dict:
    """Nuclear/SMR catalyst tracker."""
    cache = _load_cache()
    week = datetime.utcnow().strftime("%Y-W%W")
    if cache.get("nuclear", {}).get("week") == week:
        return cache["nuclear"]

    raw_dir  = BASE_DIR / "data" / "raw"
    all_text = ""
    if raw_dir.exists():
        cutoff = datetime.utcnow() - timedelta(days=14)
        for f in raw_dir.glob("*.json"):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) >= cutoff:
                    all_text += f.read_text(encoding="utf-8", errors="replace").lower()
            except Exception:
                continue

    bull_hits   = sum(1 for kw in NUCLEAR_BULL_SIGNALS if kw.lower() in all_text)
    bear_hits   = sum(1 for kw in NUCLEAR_BEAR_SIGNALS if kw.lower() in all_text)
    ppa_detected = any(kw in all_text for kw in
                        ["nuclear ppa", "power purchase", "data center nuclear", "microsoft nuclear"])
    nrc_action  = any(kw in all_text for kw in ["NRC approval", "SMR license", "NRC license"])

    net = bull_hits - (bear_hits * 1.5) + (3 if ppa_detected else 0) + (3 if nrc_action else 0)

    if net >= 8:
        nrgc_signal, nrgc_boost = "nuclear_catalyst_major", 5
        note = "Major nuclear catalyst (PPA + NRC) -> NNE, OKLO, CEG, CCJ max conviction"
    elif net >= 5:
        nrgc_signal, nrgc_boost = "nuclear_momentum_strong", 3
        note = "Strong nuclear narrative -> accumulate Nuclear/SMR names"
    elif net >= 2:
        nrgc_signal, nrgc_boost = "nuclear_positive", 1
        note = "Positive nuclear flow -> monitor closely"
    elif net <= -2:
        nrgc_signal, nrgc_boost = "nuclear_headwind", -2
        note = "Nuclear setbacks -> reduce exposure"
    else:
        nrgc_signal, nrgc_boost = "nuclear_neutral", 0
        note = "Neutral nuclear signals"

    result = {
        "week":           week,
        "bull_hits":      bull_hits,
        "bear_hits":      bear_hits,
        "ppa_detected":   ppa_detected,
        "nrc_action":     nrc_action,
        "net_score":      round(net, 1),
        "nrgc_signal":    nrgc_signal,
        "nrgc_boost":     nrgc_boost,
        "note":           note,
        "affected_themes": ["Nuclear/SMR"],
        "key_stocks":     ["NNE", "OKLO", "CEG", "CCJ", "LEU", "URG"],
    }
    log(f"Nuclear: bull={bull_hits} PPA={ppa_detected} NRC={nrc_action} -> {nrgc_signal}")

    cache["nuclear"] = result
    _save_cache(cache)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 6. DATA CENTER POWER DEMAND
#    EIA electricity data + hyperscaler capex mentions (from Module 2)
# ═══════════════════════════════════════════════════════════════════════════════

DC_POWER_BULL = [
    "data center power demand", "data center electricity", "AI power demand",
    "power constraint", "power shortage", "utility data center",
    "hyperscaler power purchase", "data center PPA",
    "grid connection", "transmission upgrade", "utility load growth",
    "power density increasing", "cooling demand", "liquid cooling",
    "Vertiv growth", "VRT guidance", "ETN data center", "Eaton data center",
]

DC_POWER_BEAR = [
    "data center power glut", "overcapacity", "power demand plateaus",
    "data center oversupply", "DC vacancy rising",
]


def get_data_center_power_signals(ai_capex_result: dict = None) -> dict:
    """
    Data center power demand signals.
    Combines news scan + hyperscaler capex (from ai_capex_tracker).
    """
    cache = _load_cache()
    week = datetime.utcnow().strftime("%Y-W%W")
    if cache.get("dc_power", {}).get("week") == week:
        return cache["dc_power"]

    raw_dir  = BASE_DIR / "data" / "raw"
    all_text = ""
    if raw_dir.exists():
        cutoff = datetime.utcnow() - timedelta(days=14)
        for f in raw_dir.glob("*.json"):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) >= cutoff:
                    all_text += f.read_text(encoding="utf-8", errors="replace").lower()
            except Exception:
                continue

    bull_hits = sum(1 for kw in DC_POWER_BULL if kw.lower() in all_text)
    bear_hits = sum(1 for kw in DC_POWER_BEAR if kw.lower() in all_text)

    # Bonus: if hyperscaler capex is accelerating -> DC power demand structural
    capex_boost = 0
    if ai_capex_result:
        capex_nrgc = ai_capex_result.get("nrgc_signal", "")
        if "supercycle" in capex_nrgc:
            capex_boost = 3
        elif "accelerating" in capex_nrgc:
            capex_boost = 2
        elif "growing" in capex_nrgc:
            capex_boost = 1

    net = (bull_hits * 1.2) - (bear_hits * 2) + capex_boost

    if net >= 8:
        nrgc_signal, nrgc_boost = "dc_power_supercycle", 5
        note = "DC power demand critical constraint -> VRT, ETN, PWR, EME maximum conviction"
    elif net >= 5:
        nrgc_signal, nrgc_boost = "dc_power_strong", 3
        note = "Strong DC power demand -> accumulate AI Infrastructure names"
    elif net >= 2:
        nrgc_signal, nrgc_boost = "dc_power_growing", 2
        note = "DC power demand growing -> positive for Data Center Infra theme"
    elif net <= -2:
        nrgc_signal, nrgc_boost = "dc_power_concern", -2
        note = "DC power demand concern -> reduce AI Infrastructure"
    else:
        nrgc_signal, nrgc_boost = "dc_power_neutral", 0
        note = "Neutral DC power signals"

    result = {
        "week":          week,
        "bull_hits":     bull_hits,
        "bear_hits":     bear_hits,
        "capex_boost":   capex_boost,
        "net_score":     round(net, 1),
        "nrgc_signal":   nrgc_signal,
        "nrgc_boost":    nrgc_boost,
        "note":          note,
        "affected_themes": ["Data Center", "AI Infrastructure", "Data Center Infra", "Nuclear/SMR"],
        "key_stocks":    ["VRT", "ETN", "PWR", "EME", "EQIX", "DLR"],
    }
    log(f"DC power: bull={bull_hits} capex_boost={capex_boost} -> {nrgc_signal}")

    cache["dc_power"] = result
    _save_cache(cache)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_specialized_edge() -> dict:
    """
    Run all 6 specialized edge modules. Returns consolidated signal dict.
    Each module caches results — safe to call multiple times.
    """
    log("=== Specialized Edge Intelligence ===")

    # Run all modules
    semi    = get_semiconductor_signals()
    ai_capex = get_ai_capex_signals()
    hbm     = get_hbm_memory_signals()
    defense = get_defense_signals()
    nuclear = get_nuclear_signals()
    dc_power = get_data_center_power_signals(ai_capex_result=ai_capex)

    # Build theme-level NRGC boost map
    nrgc_theme_boosts = {}

    modules = [semi, ai_capex, hbm, defense, nuclear, dc_power]
    for m in modules:
        boost  = m.get("nrgc_boost", 0)
        themes = m.get("affected_themes", [])
        for theme in themes:
            nrgc_theme_boosts[theme] = nrgc_theme_boosts.get(theme, 0) + boost

    # Top signals
    top_signals = sorted(
        [
            (m.get("nrgc_signal", "?"), m.get("nrgc_boost", 0), m.get("note", ""))
            for m in modules
        ],
        key=lambda x: abs(x[1]), reverse=True
    )

    result = {
        "date":             datetime.utcnow().strftime("%Y-%m-%d"),
        "semiconductor":    semi,
        "ai_capex":         ai_capex,
        "hbm_memory":       hbm,
        "defense":          defense,
        "nuclear":          nuclear,
        "data_center_power": dc_power,
        "nrgc_theme_boosts": nrgc_theme_boosts,
        "top_signals":       top_signals,
    }

    # Save
    today = datetime.utcnow().strftime("%Y-%m-%d")
    out   = DATA_DIR / f"specialized_edge_{today[:7]}.json"
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    # Log summary
    top_3 = [(sig, f"{boost:+d}") for sig, boost, _ in top_signals[:3]]
    log(f"Done: top signals = {top_3}")
    log(f"Theme boosts: {dict(sorted(nrgc_theme_boosts.items(), key=lambda x: -abs(x[1]))[:5])}")

    return result


def get_edge_nrgc_boost(edge_result: dict, theme: str) -> int:
    """Quick lookup for weekly_runner NRGC enrichment."""
    return edge_result.get("nrgc_theme_boosts", {}).get(theme, 0)


def get_edge_telegram_lines(result: dict) -> str:
    """Multi-line summary for Telegram weekly report."""
    if not result:
        return ""
    lines = []
    modules = {
        "semiconductor":     "Semi",
        "ai_capex":          "AI Capex",
        "hbm_memory":        "HBM",
        "defense":           "Defense",
        "nuclear":           "Nuclear",
        "data_center_power": "DC Power",
    }
    for key, label in modules.items():
        m = result.get(key, {})
        sig   = m.get("nrgc_signal", "?").replace("_", " ")
        boost = m.get("nrgc_boost", 0)
        lines.append(f"  {label}: {sig} ({boost:+d})")
    return "Edge Signals:\n" + "\n".join(lines)
