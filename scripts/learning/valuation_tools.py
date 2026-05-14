"""
AlphaAbsolute — Valuation Intelligence Tools
Reverse DCF + Forward DCF + Sensitivity tables + NRGC phase implication.

Core insight: Price embeds expectations. Reverse DCF tells you WHAT GROWTH
the market is already pricing. This maps directly to NRGC phase:
  High implied growth (>40% CAGR) → Phase 4/5 (crowded, narrative consensus)
  Moderate implied growth (15-40%) → Phase 3/4 (institutionally discovered)
  Low implied growth (<15%)        → Phase 1/2 (neglected, opportunity)
  Negative implied growth          → Phase 0/1 (distressed, turnaround)

Tools:
  1. reverse_dcf()       — what CAGR is priced at current price?
  2. forward_dcf()       — given assumptions, what is fair value?
  3. sensitivity_table() — fair value matrix across growth × discount rate
  4. peer_relative_value() — how expensive vs correct peer group?
  5. valuation_phase_signal() — NRGC phase from valuation inputs

All zero-cost, zero-token pure math.
"""
from __future__ import annotations
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR  = BASE_DIR / "data" / "agent_memory"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    print(f"  [ValuationTools] {msg}")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. REVERSE DCF — What growth rate is the market pricing?
# ═══════════════════════════════════════════════════════════════════════════════

def reverse_dcf(
    current_price:     float,
    shares_outstanding: float,   # millions
    revenue_ttm:       float,    # USD millions
    fcf_margin:        float,    # e.g. 0.20 = 20% FCF margin
    terminal_growth:   float = 0.025,
    discount_rate:     float = 0.10,
    years:             int   = 10,
    net_debt:          float = 0.0,   # USD millions (debt - cash)
) -> dict:
    """
    Reverse-engineer the revenue CAGR the market is pricing into a stock.

    Returns:
      implied_revenue_cagr: the growth rate that makes DCF = current price
      interpretation: NRGC phase implication
      fair_value_table: value at different growth rates
    """
    market_cap = current_price * shares_outstanding          # USD millions
    ev         = market_cap + net_debt                       # enterprise value

    if ev <= 0 or revenue_ttm <= 0 or fcf_margin <= 0:
        return {"error": "Invalid inputs", "implied_cagr": None, "nrgc_signal": "unknown"}

    # Binary search for the revenue CAGR that makes EV = DCF
    def compute_ev(cagr: float) -> float:
        """DCF of FCF stream + terminal value at given revenue CAGR."""
        pv = 0.0
        rev = revenue_ttm
        for yr in range(1, years + 1):
            rev *= (1 + cagr)
            fcf  = rev * fcf_margin
            pv  += fcf / ((1 + discount_rate) ** yr)
        # Terminal value (Gordon Growth Model)
        terminal_fcf = rev * fcf_margin * (1 + terminal_growth)
        terminal_val  = terminal_fcf / (discount_rate - terminal_growth)
        pv += terminal_val / ((1 + discount_rate) ** years)
        return pv

    # Binary search: find CAGR where compute_ev = ev
    lo, hi = -0.50, 2.00   # search −50% to +200% CAGR
    for _ in range(60):
        mid = (lo + hi) / 2
        if compute_ev(mid) > ev:
            hi = mid
        else:
            lo = mid

    implied_cagr = round((lo + hi) / 2 * 100, 1)  # in %

    # NRGC phase signal from implied growth
    if implied_cagr >= 60:
        nrgc_signal  = "phase_5_euphoria"
        nrgc_phase   = "Phase 5-6: Euphoria/Distribution — growth expectations extreme"
        nrgc_boost   = -3   # overvalued relative to reality
        conviction   = "AVOID or short"
    elif implied_cagr >= 40:
        nrgc_signal  = "phase_4_consensus"
        nrgc_phase   = "Phase 4: Recognition/Consensus — fully priced for good news"
        nrgc_boost   = -1
        conviction   = "HOLD — reduce on strength"
    elif implied_cagr >= 25:
        nrgc_signal  = "phase_3_institutionally_discovered"
        nrgc_phase   = "Phase 3: Institutional Discovery — moderate premium, still has upside"
        nrgc_boost   = 2
        conviction   = "BUY on pullback"
    elif implied_cagr >= 10:
        nrgc_signal  = "phase_2_accumulation"
        nrgc_phase   = "Phase 2: Accumulation — low bar, high potential if growth inflects"
        nrgc_boost   = 4
        conviction   = "BUY — strong asymmetric setup"
    elif implied_cagr >= 0:
        nrgc_signal  = "phase_1_neglect"
        nrgc_phase   = "Phase 1: Neglect — market pricing minimal growth"
        nrgc_boost   = 3
        conviction   = "WATCH — needs catalyst to start Phase 2"
    else:
        nrgc_signal  = "phase_0_distressed"
        nrgc_phase   = "Phase 0: Distressed/Turnaround — market pricing revenue decline"
        nrgc_boost   = 1
        conviction   = "RESEARCH ONLY — turnaround thesis required"

    # Generate fair value table at different growth rates
    growth_scenarios = [-0.10, 0.00, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
    fv_table = []
    for g in growth_scenarios:
        fv_ev    = compute_ev(g)
        fv_price = max(0.0, (fv_ev - net_debt) / shares_outstanding)
        upside   = (fv_price / current_price - 1) * 100
        fv_table.append({
            "revenue_cagr":  f"{g*100:.0f}%",
            "fair_value":    round(fv_price, 2),
            "upside_pct":    round(upside, 1),
        })

    result = {
        "current_price":      current_price,
        "implied_cagr_pct":   implied_cagr,
        "nrgc_signal":        nrgc_signal,
        "nrgc_phase":         nrgc_phase,
        "nrgc_boost":         nrgc_boost,
        "conviction":         conviction,
        "assumptions": {
            "fcf_margin":       f"{fcf_margin*100:.0f}%",
            "discount_rate":    f"{discount_rate*100:.0f}%",
            "terminal_growth":  f"{terminal_growth*100:.1f}%",
            "years":            years,
        },
        "fair_value_table":   fv_table,
        "summary":            (
            f"Market is pricing {implied_cagr:.0f}% revenue CAGR over {years}Y. "
            f"{nrgc_phase}. {conviction}."
        ),
    }
    log(f"Reverse DCF: implied CAGR={implied_cagr:.0f}% -> {nrgc_signal}")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 2. FORWARD DCF — What is fair value given your assumptions?
# ═══════════════════════════════════════════════════════════════════════════════

def forward_dcf(
    revenue_ttm:       float,    # USD millions
    fcf_margin:        float,    # e.g. 0.20 = 20%
    revenue_cagr:      float,    # e.g. 0.25 = 25%
    shares_outstanding: float,   # millions
    terminal_growth:   float = 0.025,
    discount_rate:     float = 0.10,
    years:             int   = 10,
    net_debt:          float = 0.0,
    current_price:     float = 0.0,
) -> dict:
    """
    Forward DCF: given your growth assumptions, what is fair value?
    Returns fair value per share + margin of safety.
    """
    rev = revenue_ttm
    pv  = 0.0
    for yr in range(1, years + 1):
        rev *= (1 + revenue_cagr)
        fcf  = rev * fcf_margin
        pv  += fcf / ((1 + discount_rate) ** yr)

    terminal_fcf = rev * fcf_margin * (1 + terminal_growth)
    terminal_val  = terminal_fcf / (discount_rate - terminal_growth)
    pv += terminal_val / ((1 + discount_rate) ** years)

    fair_ev    = pv
    fair_price = max(0.0, (fair_ev - net_debt) / shares_outstanding)

    mos = 0.0
    upside = 0.0
    if current_price > 0:
        upside = (fair_price / current_price - 1) * 100
        mos    = upside  # margin of safety = upside at current price

    return {
        "fair_value_per_share": round(fair_price, 2),
        "fair_ev_usd_mm":       round(fair_ev, 1),
        "current_price":        current_price,
        "upside_pct":           round(upside, 1),
        "margin_of_safety":     round(mos, 1),
        "assumptions": {
            "revenue_cagr":     f"{revenue_cagr*100:.0f}%",
            "fcf_margin":       f"{fcf_margin*100:.0f}%",
            "discount_rate":    f"{discount_rate*100:.0f}%",
            "terminal_growth":  f"{terminal_growth*100:.1f}%",
            "years":            years,
        },
        "verdict": (
            "SIGNIFICANTLY UNDERVALUED" if mos > 50 else
            "UNDERVALUED"              if mos > 20 else
            "FAIR VALUE"               if abs(mos) <= 20 else
            "OVERVALUED"               if mos < -20 else
            "SIGNIFICANTLY OVERVALUED"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SENSITIVITY TABLE — Fair value matrix across growth × discount rate
# ═══════════════════════════════════════════════════════════════════════════════

def sensitivity_table(
    revenue_ttm:       float,
    fcf_margin:        float,
    shares_outstanding: float,
    net_debt:          float = 0.0,
    terminal_growth:   float = 0.025,
    years:             int   = 10,
    current_price:     float = 0.0,
) -> dict:
    """
    Generate fair value matrix across:
      Rows: Revenue CAGR (bear / base / bull cases)
      Cols: Discount rate (8%, 10%, 12%)

    Returns text table + raw matrix for Telegram/report.
    """
    growth_rates  = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
    discount_rates = [0.08, 0.10, 0.12]

    matrix = []
    for g in growth_rates:
        row = {"growth_cagr": f"{g*100:.0f}%", "scenarios": {}}
        for dr in discount_rates:
            dcf = forward_dcf(
                revenue_ttm=revenue_ttm, fcf_margin=fcf_margin,
                revenue_cagr=g, shares_outstanding=shares_outstanding,
                terminal_growth=terminal_growth, discount_rate=dr,
                years=years, net_debt=net_debt, current_price=current_price,
            )
            fv = dcf["fair_value_per_share"]
            up = dcf["upside_pct"]
            row["scenarios"][f"dr_{int(dr*100)}pct"] = {
                "fair_value":  fv,
                "upside_pct":  up,
            }
        matrix.append(row)

    # Build text table for report
    header = f"{'CAGR':>8} | {'FV @8%':>8} {'Up%':>6} | {'FV @10%':>8} {'Up%':>6} | {'FV @12%':>8} {'Up%':>6}"
    sep    = "-" * len(header)
    rows_txt = [header, sep]
    for row in matrix:
        g_str = row["growth_cagr"]
        s8    = row["scenarios"]["dr_8pct"]
        s10   = row["scenarios"]["dr_10pct"]
        s12   = row["scenarios"]["dr_12pct"]
        line  = (
            f"{g_str:>8} | "
            f"${s8['fair_value']:>7.2f} {s8['upside_pct']:>+5.0f}% | "
            f"${s10['fair_value']:>7.2f} {s10['upside_pct']:>+5.0f}% | "
            f"${s12['fair_value']:>7.2f} {s12['upside_pct']:>+5.0f}%"
        )
        rows_txt.append(line)

    # Find bear/base/bull cases from 10% DR column
    bear_fv = matrix[0]["scenarios"]["dr_10pct"]["fair_value"]   # lowest growth
    base_fv = matrix[3]["scenarios"]["dr_10pct"]["fair_value"]   # 20% CAGR
    bull_fv = matrix[5]["scenarios"]["dr_10pct"]["fair_value"]   # 30% CAGR

    return {
        "matrix":      matrix,
        "text_table":  "\n".join(rows_txt),
        "current_price": current_price,
        "scenarios_10pct_dr": {
            "bear": {"cagr": "5%",  "fair_value": bear_fv},
            "base": {"cagr": "20%", "fair_value": base_fv},
            "bull": {"cagr": "30%", "fair_value": bull_fv},
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4. PEER RELATIVE VALUE — vs correct comparable set
# ═══════════════════════════════════════════════════════════════════════════════

def peer_relative_value(
    ticker:          str,
    ev_sales:        float,    # company's EV/Sales
    ev_sales_peers:  list,     # [peer EV/Sales values]
    revenue_growth:  float,    # company's revenue growth %
    peer_growth_avg: float,    # peer group average growth %
    category:        str = "unknown",
) -> dict:
    """
    Compare company valuation vs correct peer group.
    Detects misprice (Agent 3b style).
    """
    if not ev_sales_peers:
        return {"error": "No peer data"}

    peer_median = sorted(ev_sales_peers)[len(ev_sales_peers) // 2]
    peer_avg    = sum(ev_sales_peers) / len(ev_sales_peers)
    discount    = (ev_sales / peer_median - 1) * 100  # positive = premium, negative = discount

    # Growth-adjusted comparison (PEG-style but for revenue)
    if peer_growth_avg > 0:
        company_peg = ev_sales / max(revenue_growth, 0.01)
        peer_peg    = peer_median / peer_growth_avg
        peg_discount = (company_peg / peer_peg - 1) * 100
    else:
        peg_discount = 0

    # Misprice detection
    if discount < -30 and peg_discount < -25:
        misprice_signal = "significant_misprice"
        nrgc_boost      = 4
    elif discount < -20:
        misprice_signal = "moderate_misprice"
        nrgc_boost      = 2
    elif discount > 30 and peg_discount > 25:
        misprice_signal = "significant_premium"
        nrgc_boost      = -2
    else:
        misprice_signal = "fairly_valued_vs_peers"
        nrgc_boost      = 0

    return {
        "ticker":           ticker,
        "category":         category,
        "ev_sales":         ev_sales,
        "peer_median_ev_sales": peer_median,
        "peer_avg_ev_sales":    peer_avg,
        "premium_discount_pct": round(discount, 1),
        "growth_adj_discount":  round(peg_discount, 1),
        "misprice_signal":  misprice_signal,
        "nrgc_boost":       nrgc_boost,
        "summary":          (
            f"{ticker} trades at {ev_sales:.1f}x EV/S vs peer median {peer_median:.1f}x "
            f"= {abs(discount):.0f}% {'discount' if discount < 0 else 'premium'}. "
            f"{misprice_signal.replace('_', ' ').title()}."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. QUICK ANALYSIS SHORTCUT — all tools in one call
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_stock_valuation(
    ticker:            str,
    current_price:     float,
    shares_mm:         float,
    revenue_ttm_mm:    float,
    fcf_margin:        float       = 0.15,
    net_debt_mm:       float       = 0.0,
    expected_cagr:     float       = 0.25,
    discount_rate:     float       = 0.10,
    ev_sales:          float       = 0.0,
    peer_ev_sales:     list        = None,
    peer_growth:       float       = 0.20,
    category:          str         = "Growth",
) -> dict:
    """
    Complete valuation analysis: reverse DCF + forward DCF + sensitivity + peer.
    Call this from Agent 3b or Agent 04 for any ticker.
    """
    log(f"Analyzing {ticker}: price=${current_price} rev=${revenue_ttm_mm}mm")
    results = {"ticker": ticker, "date": datetime.utcnow().strftime("%Y-%m-%d")}

    # Reverse DCF
    results["reverse_dcf"] = reverse_dcf(
        current_price=current_price,
        shares_outstanding=shares_mm,
        revenue_ttm=revenue_ttm_mm,
        fcf_margin=fcf_margin,
        net_debt=net_debt_mm,
        discount_rate=discount_rate,
    )

    # Forward DCF at expected growth
    results["forward_dcf"] = forward_dcf(
        revenue_ttm=revenue_ttm_mm,
        fcf_margin=fcf_margin,
        revenue_cagr=expected_cagr,
        shares_outstanding=shares_mm,
        net_debt=net_debt_mm,
        discount_rate=discount_rate,
        current_price=current_price,
    )

    # Sensitivity table
    results["sensitivity"] = sensitivity_table(
        revenue_ttm=revenue_ttm_mm,
        fcf_margin=fcf_margin,
        shares_outstanding=shares_mm,
        net_debt=net_debt_mm,
        current_price=current_price,
    )

    # Peer relative value
    if ev_sales and peer_ev_sales:
        results["peer_value"] = peer_relative_value(
            ticker=ticker,
            ev_sales=ev_sales,
            ev_sales_peers=peer_ev_sales,
            revenue_growth=expected_cagr * 100,
            peer_growth_avg=peer_growth * 100,
            category=category,
        )

    # Composite NRGC boost
    rv_boost   = results["reverse_dcf"].get("nrgc_boost", 0)
    peer_boost = results.get("peer_value", {}).get("nrgc_boost", 0)
    total_boost = rv_boost + peer_boost

    implied_cagr = results["reverse_dcf"].get("implied_cagr_pct", 0)
    forward_up   = results["forward_dcf"].get("upside_pct", 0)

    results["composite"] = {
        "implied_cagr_pct":  implied_cagr,
        "forward_upside_pct": forward_up,
        "total_nrgc_boost":  total_boost,
        "nrgc_signal":       results["reverse_dcf"].get("nrgc_signal", "unknown"),
        "investment_verdict": results["forward_dcf"].get("verdict", "unknown"),
        "one_liner":          (
            f"{ticker}: Market pricing {implied_cagr:.0f}% CAGR. "
            f"At {expected_cagr*100:.0f}% expected, FV = "
            f"${results['forward_dcf']['fair_value_per_share']:.2f} "
            f"({forward_up:+.0f}% upside)."
        ),
    }

    log(f"  {results['composite']['one_liner']}")

    # Save
    out = DATA_DIR / f"valuation_{ticker}_{datetime.utcnow().strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    return results


def format_valuation_summary(result: dict) -> str:
    """Format valuation result for CIO Brief or Telegram."""
    c  = result.get("composite", {})
    rv = result.get("reverse_dcf", {})
    fd = result.get("forward_dcf", {})
    s  = result.get("sensitivity", {}).get("scenarios_10pct_dr", {})

    lines = [
        f"== Valuation: {result.get('ticker', '?')} ==",
        f"Implied CAGR (market):  {c.get('implied_cagr_pct', '?'):.0f}%",
        f"Expected CAGR (our est): {fd.get('assumptions', {}).get('revenue_cagr', '?')}",
        f"Fair Value:              ${fd.get('fair_value_per_share', 0):.2f}",
        f"Upside:                  {c.get('forward_upside_pct', 0):+.0f}%",
        f"Verdict:                 {fd.get('verdict', '?')}",
        f"NRGC signal:             {rv.get('nrgc_phase', '?')}",
        "",
        "Sensitivity (10% DR):",
        f"  Bear (5% CAGR):  ${s.get('bear', {}).get('fair_value', 0):.2f}",
        f"  Base (20% CAGR): ${s.get('base', {}).get('fair_value', 0):.2f}",
        f"  Bull (30% CAGR): ${s.get('bull', {}).get('fair_value', 0):.2f}",
    ]
    return "\n".join(lines)
