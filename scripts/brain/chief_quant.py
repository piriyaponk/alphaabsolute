"""
Chief Quant — cross-signal synthesis agent.

Reads ALL data streams weekly, finds patterns no single agent can see,
generates hypotheses, submits to agent_ideas.json.

HARD RULE: propose only. Never touch S4 config. Never deploy anything.
Every idea goes through CIO approval before any change goes live.

Runs: Sunday pipeline (after system_optimizer, before Telegram summary)
"""

from __future__ import annotations
import json
import logging
import math
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

TODAY = date.today().isoformat()
AGENT_NAME = "chief-quant"

# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------

def _read_json(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _load_regime() -> dict:
    """Read System 4 regime state (BULL/BEAR from IWM MA200 gate)."""
    s4 = _read_json(ROOT / "data" / "paper_trading" / "state.json", {})
    regime = s4.get("regime", "BULL")
    return {
        "regime": regime,
        "regime_score": 75 if regime == "BULL" else 25,
    }


def _load_rs_universe() -> dict:
    return _read_json(ROOT / "data" / "rs_universe" / "latest.json", {})


def _load_leaderboard() -> list:
    return _read_json(ROOT / "data" / "research" / "system_leaderboard.json", [])


def _load_agent_kpi() -> dict:
    return _read_json(ROOT / "data" / "research" / "agent_kpi.json", {})


def _load_backtest_results() -> list:
    return _read_json(ROOT / "data" / "research" / "backtest_results.json", [])


def _load_portfolio_state() -> dict:
    return _read_json(ROOT / "data" / "paper_trading" / "state.json", {})


def _load_champion() -> dict:
    return _read_json(ROOT / "data" / "research" / "champion.json", {})


def _load_agent_ideas() -> list:
    return _read_json(ROOT / "data" / "research" / "agent_ideas.json", [])


# ---------------------------------------------------------------------------
# Core synthesis logic — 5 observation lenses
# ---------------------------------------------------------------------------

def _analyze_model_reality_gap(
    leaderboard: list, portfolio: dict, regime: dict
) -> list[dict]:
    """
    Lens 1: Model says X is best — but live P&L disagrees.
    If top leaderboard config uses IWM gate but live portfolio lags QQQ → IWM gate may be wrong NOW.
    """
    ideas = []

    nav = portfolio.get("nav", 0)
    inception_nav = portfolio.get("inception_nav", nav or 1)
    if not inception_nav:
        return ideas

    nav_pct = (nav - inception_nav) / inception_nav * 100

    qqq_h = portfolio.get("qqq_nav_history", {})
    qqq_inc = portfolio.get("qqq_inception")
    qqq_pct = 0.0
    if isinstance(qqq_h, dict) and qqq_h and qqq_inc:
        vals = [qqq_h[k] for k in sorted(qqq_h)]
        if vals and float(qqq_inc) > 0:
            qqq_pct = (vals[-1] - float(qqq_inc)) / float(qqq_inc) * 100

    alpha = nav_pct - qqq_pct

    # If losing to QQQ AND regime is BULL → regime gate may be too restrictive
    regime_name = regime.get("regime", "")
    if alpha < -5 and regime_name == "BULL":
        ideas.append(dict(
            title="No regime gate when losing to QQQ in BULL regime",
            hypothesis=(
                f"Live portfolio alpha vs QQQ = {alpha:.1f}% in {regime_name} regime. "
                "Regime gate (IWM 200MA) may be blocking entries in confirmed bull. "
                "Test: remove regime gate entirely during BULL regime."
            ),
            param={"param": "regime_ticker", "value": "none", "baseline": "IWM"},
            rationale="Model-reality gap: backtest says IWM gate helps but live alpha is negative in BULL regime.",
        ))

    # If winning QQQ significantly → current config is working, test concentration
    if alpha > 10 and len(leaderboard) >= 5:
        champ_params = leaderboard[0].get("params", {}) if leaderboard else {}
        current_top_n = champ_params.get("top_n", 15)
        if current_top_n >= 15:
            ideas.append(dict(
                title=f"Increase concentration to top {current_top_n - 5} when strongly beating QQQ",
                hypothesis=(
                    f"Live alpha = +{alpha:.1f}%. Momentum is working well. "
                    f"Reduce from top {current_top_n} to {current_top_n - 5} — concentrate in true leaders only."
                ),
                param={"param": "top_n", "value": current_top_n - 5, "baseline": current_top_n},
                rationale="When alpha is strong, concentration amplifies edge. When weak, breadth protects.",
            ))

    return ideas


def _analyze_cross_signal(leaderboard: list, agent_kpi: dict) -> list[dict]:
    """
    Lens 2: Two agents found something independently — test the combo.
    If board-secretary found equal_weight wins AND da-analyst found SPY regime wins
    → nobody has tested equal_weight + SPY together yet.
    """
    ideas = []
    board = agent_kpi.get("leaderboard", [])

    # Find best idea per agent from kpi
    agent_best: dict[str, dict] = {}
    for row in board:
        if row.get("ideas_passed", 0) > 0 and row.get("best_alpha_score", 0) > 0:
            agent_best[row["agent"]] = row

    if len(agent_best) < 2:
        return ideas

    # Cross: sizing_method winner + regime_ticker winner
    # Look through leaderboard for which params appear in top results
    top_results = [e for e in leaderboard if not e.get("is_baseline") and e.get("beats_baseline")]
    if len(top_results) < 2:
        return ideas

    # Find most common winning params
    sizing_wins = {}
    regime_wins = {}
    rs_wins = {}
    for e in top_results:
        p = e.get("params", {})
        s = p.get("sizing_method", "")
        r = p.get("regime_ticker", "")
        rs = p.get("rs_weight_profile", "")
        if s:
            sizing_wins[s] = sizing_wins.get(s, 0) + 1
        if r:
            regime_wins[r] = regime_wins.get(r, 0) + 1
        if rs:
            rs_wins[rs] = rs_wins.get(rs, 0) + 1

    best_sizing = max(sizing_wins, key=sizing_wins.get) if sizing_wins else None
    best_regime = max(regime_wins, key=regime_wins.get) if regime_wins else None
    best_rs = max(rs_wins, key=rs_wins.get) if rs_wins else None

    # Check if combo already tested
    existing_combos = set()
    for e in leaderboard:
        p = e.get("params", {})
        sig = f"{p.get('sizing_method')}|{p.get('regime_ticker')}|{p.get('rs_weight_profile')}"
        existing_combos.add(sig)

    if best_sizing and best_regime and best_rs:
        combo_sig = f"{best_sizing}|{best_regime}|{best_rs}"
        if combo_sig not in existing_combos:
            ideas.append(dict(
                title=f"Cross-signal combo: {best_rs} + {best_regime} + {best_sizing}",
                hypothesis=(
                    f"Individually: {best_rs} RS weights, {best_regime} regime gate, {best_sizing} sizing each show wins. "
                    f"No single test has combined all three. Cross-agent synthesis suggests this combo may be optimal."
                ),
                param={"param": "rs_weight_profile", "value": best_rs, "baseline": "balanced"},
                rationale=(
                    f"Each component proven independently in leaderboard "
                    f"(sizing={best_sizing} N={sizing_wins.get(best_sizing,0)}, "
                    f"regime={best_regime} N={regime_wins.get(best_regime,0)}, "
                    f"rs={best_rs} N={rs_wins.get(best_rs,0)}). "
                    "Interaction effect untested — could be additive or cancel out."
                ),
            ))

    return ideas


def _analyze_parameter_sensitivity(leaderboard: list) -> list[dict]:
    """
    Lens 3: Which parameter is making the most difference?
    If changing regime from IWM→SPY adds +10 pts but changing skip=0→21 costs -5 pts
    → regime gate is the dominant variable; focus search there.
    """
    ideas = []
    if len(leaderboard) < 5:
        return ideas

    # Group by parameter variation, compute avg score diff
    param_impact: dict[str, list[float]] = {}
    baseline = next((e for e in leaderboard if e.get("is_baseline")), None)
    if not baseline:
        return ideas

    baseline_score = baseline.get("alpha_score", 0)

    for entry in leaderboard:
        if entry.get("is_baseline"):
            continue
        ep = entry.get("params", {})
        bp = baseline.get("params", {})
        diff = entry.get("alpha_score", 0) - baseline_score

        # Find which params differ from baseline
        for key in ("vol_lookback_days", "skip_days", "top_n", "sizing_method", "regime_ticker", "rs_weight_profile", "rebalance_days"):
            if ep.get(key) != bp.get(key):
                if key not in param_impact:
                    param_impact[key] = []
                param_impact[key].append(diff)

    if not param_impact:
        return ideas

    # Find highest-impact parameter by mean score diff
    param_means = {k: sum(v) / len(v) for k, v in param_impact.items() if v}
    best_param = max(param_means, key=param_means.get)
    worst_param = min(param_means, key=param_means.get)

    # If one parameter dominates positively → suggest more variants
    if param_means[best_param] > 3:
        ideas.append(dict(
            title=f"Deep search: {best_param} is the dominant variable (+{param_means[best_param]:.1f} avg)",
            hypothesis=(
                f"Sensitivity analysis: '{best_param}' produces average +{param_means[best_param]:.1f} pts vs baseline "
                f"across {len(param_impact[best_param])} tested variants. "
                "This is the highest-leverage parameter. Test more values in this dimension."
            ),
            param=None,  # meta-idea, no single param — signals to test more of this
            rationale=(
                f"Sensitivity matrix shows {best_param} > all other params by mean impact. "
                "Focus next 10 optimizer runs on varying this param while holding others at best-known values."
            ),
        ))

    # If one parameter consistently hurts → flag it
    if param_means[worst_param] < -2:
        ideas.append(dict(
            title=f"Flag: {worst_param} consistently hurts AlphaScore ({param_means[worst_param]:.1f} avg)",
            hypothesis=(
                f"'{worst_param}' variants average {param_means[worst_param]:.1f} pts vs baseline — consistently negative. "
                "This parameter may not be meaningful for S4. Consider fixing it at baseline and removing from search."
            ),
            param=None,
            rationale=(
                f"Negative sensitivity suggests either wrong direction or no edge. "
                "Narrowing search space improves optimizer efficiency."
            ),
        ))

    return ideas


def _analyze_regime_conditional(regime: dict, leaderboard: list) -> list[dict]:
    """
    Lens 4: Current regime should inform which params to prioritize.
    In BEAR regime → DD matters more than CAGR → propose DD-minimizing configs.
    """
    ideas = []
    regime_name = regime.get("regime", "BULL")
    score = regime.get("effective_score", regime.get("score", 50))

    if regime_name == "BEAR":
        # In BEAR: find leaderboard entries with lowest DD even if lower CAGR
        low_dd_entries = sorted(
            [e for e in leaderboard if not e.get("is_baseline") and e.get("max_dd_pct")],
            key=lambda x: x.get("max_dd_pct", 999)
        )[:3]

        if low_dd_entries:
            best_dd_entry = low_dd_entries[0]
            bp = best_dd_entry.get("params", {})
            ideas.append(dict(
                title=f"BEAR regime: use lowest-DD config (DD={best_dd_entry.get('max_dd_pct')}%)",
                hypothesis=(
                    f"Current regime = {regime_name} (score={score}). "
                    f"In BEAR regime, capital preservation > CAGR. "
                    f"Config with DD={best_dd_entry.get('max_dd_pct')}% "
                    f"(regime={bp.get('regime_ticker')}, top_n={bp.get('top_n')}, "
                    f"sizing={bp.get('sizing_method')}) may be better suited to current conditions."
                ),
                param={"param": "top_n", "value": bp.get("top_n", 10), "baseline": 15},
                rationale=(
                    "Regime-conditional parameter selection: optimal config is not static. "
                    "BEAR regime rewards lower beta, tighter concentration, stronger regime gate."
                ),
            ))

    elif regime_name == "BULL" and score >= 70:
        # Strong BULL: test more aggressive configs
        ideas.append(dict(
            title="Strong BULL regime: test aggressive top=20 with no regime gate",
            hypothesis=(
                f"Regime score = {score}/85 — strong BULL. All stocks rising. "
                "Regime gate may be suppressing gains unnecessarily. "
                "Test top=20 + no gate to maximize participation."
            ),
            param={"param": "top_n", "value": 20, "baseline": 15},
            rationale="In strong bull markets, breadth beats concentration. More positions = more market participation.",
        ))

    return ideas


def _analyze_research_gaps(leaderboard: list) -> list[dict]:
    """
    Lens 5: What hasn't been tested yet that literature says matters?
    After seeing enough results, flag under-explored regions.
    """
    ideas = []

    tested_params = {}
    for e in leaderboard:
        p = e.get("params", {})
        for k, v in p.items():
            if k not in tested_params:
                tested_params[k] = set()
            tested_params[k].add(str(v))

    # Check: has skip=10 been tested? (midpoint between 0 and 21)
    skip_tested = tested_params.get("skip_days", set())
    if "10" not in skip_tested and len(leaderboard) > 5:
        ideas.append(dict(
            title="Test skip=10d — midpoint between no-skip and skip-21",
            hypothesis=(
                "skip=0 is baseline. skip=21 failed. "
                "skip=10 not yet tested — may capture reversal avoidance benefit "
                "without losing too much recent signal."
            ),
            param={"param": "skip_days", "value": 10, "baseline": 0},
            rationale="Convex search: when endpoints differ, test midpoint. skip=10 = 2-week micro-reversal avoidance.",
        ))

    # Check: has vol=42d been tested?
    vol_tested = tested_params.get("vol_lookback_days", set())
    if "42" not in vol_tested and len(leaderboard) > 5:
        ideas.append(dict(
            title="Test vol=42d — midpoint between 20d and 63d",
            hypothesis=(
                "vol=20d is baseline. vol=63d showed in leaderboard. "
                "vol=42d (2M) not tested — may be optimal midpoint for 21d rebalance."
            ),
            param={"param": "vol_lookback_days", "value": 42, "baseline": 20},
            rationale="2× holding period as vol window is a common practitioner heuristic. 42 = 2 × 21.",
        ))

    # Check: has rebalance=14d (2-week) been tested?
    rebal_tested = tested_params.get("rebalance_days", set())
    if "14" not in rebal_tested and len(leaderboard) > 10:
        ideas.append(dict(
            title="Test 2-week rebalance (14d) — faster signal capture",
            hypothesis=(
                "21d and 42d rebalance tested. "
                "14d rebalance may capture faster RS momentum shifts, "
                "especially in BEAR regime when leaders rotate quickly."
            ),
            param={"param": "rebalance_days", "value": 14, "baseline": 21},
            rationale="Faster rebalance = faster reaction to regime shifts. Cost: higher turnover. Net effect unknown.",
        ))

    return ideas


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> dict:
    log.info("=== Chief Quant START ===")
    log.info("Reading all data streams...")

    regime = _load_regime()
    rs_data = _load_rs_universe()
    leaderboard = _load_leaderboard()
    agent_kpi = _load_agent_kpi()
    portfolio = _load_portfolio_state()
    champion = _load_champion()

    log.info(f"Regime: {regime.get('regime', 'unknown')} | Leaderboard: {len(leaderboard)} entries | Portfolio NAV loaded: {bool(portfolio)}")

    # Run all 5 lenses
    all_ideas = []
    all_ideas += _analyze_model_reality_gap(leaderboard, portfolio, regime)
    all_ideas += _analyze_cross_signal(leaderboard, agent_kpi)
    all_ideas += _analyze_parameter_sensitivity(leaderboard)
    all_ideas += _analyze_regime_conditional(regime, leaderboard)
    all_ideas += _analyze_research_gaps(leaderboard)

    log.info(f"Generated {len(all_ideas)} ideas from 5 lenses")

    # Submit ideas — PROPOSE ONLY, never deploy
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.brain.agent_ideas import submit_idea

    submitted = 0
    for idea in all_ideas:
        if not idea.get("title"):
            continue
        submit_idea(
            agent=AGENT_NAME,
            title=idea["title"],
            hypothesis=idea["hypothesis"],
            param=idea.get("param"),
            rationale=idea.get("rationale", ""),
            source="chief_quant_weekly_synthesis",
        )
        submitted += 1
        log.info(f"  PROPOSED: {idea['title'][:70]}")

    log.info(f"Submitted {submitted} proposals — all pending CIO approval")

    # Write synthesis note to Obsidian
    _write_obsidian_synthesis(all_ideas, regime, leaderboard, champion)

    summary = {
        "date": TODAY,
        "ideas_generated": len(all_ideas),
        "ideas_submitted": submitted,
        "regime": regime.get("regime", "unknown"),
        "leaderboard_entries": len(leaderboard),
        "top_alpha_score": leaderboard[0].get("alpha_score") if leaderboard else None,
        "note": "All proposals pending CIO approval. Nothing deployed.",
    }

    _send_telegram(summary, all_ideas)
    log.info("=== Chief Quant DONE ===")
    return summary


def _write_obsidian_synthesis(ideas: list, regime: dict, leaderboard: list, champion: dict) -> None:
    try:
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from scripts.brain.obsidian_writer_v2 import write as obs_write

        champ_params = champion.get("params", {})
        champ_score = champion.get("alpha_score", "N/A")

        lines = [
            "---",
            "type: chief_quant_synthesis",
            f"date: {TODAY}",
            "tags: [chief-quant, synthesis, cross-signal, auto, propose-only]",
            "---",
            "",
            f"# Chief Quant Weekly Synthesis — {TODAY}",
            "",
            "> **RULE: All proposals require CIO approval before any S4 change.**",
            "> Chief Quant proposes. CIO decides. System never self-modifies.",
            "",
            "## Market Context",
            f"- Regime: **{regime.get('regime', 'unknown')}** (score={regime.get('regime_score', '?')})",
            "",
            "## Current Champion",
            f"- AlphaScore: **{champ_score}**",
            f"- CAGR: {champion.get('cagr_pct', '?')}% | Sharpe: {champion.get('sharpe', '?')} | MaxDD: {champion.get('max_dd_pct', '?')}%",
            f"- Config: {json.dumps(champ_params, ensure_ascii=False)}",
            "",
            "## This Week's Proposals",
            f"*(All require CIO approval — none deployed)*",
            "",
        ]

        lens_labels = {
            "model_reality": "Lens 1 — Model vs Reality Gap",
            "cross_signal": "Lens 2 — Cross-Agent Signal",
            "sensitivity": "Lens 3 — Parameter Sensitivity",
            "regime_conditional": "Lens 4 — Regime-Conditional",
            "research_gap": "Lens 5 — Research Gap",
        }

        for i, idea in enumerate(ideas, 1):
            lines += [
                f"### Proposal {i}: {idea['title']}",
                "",
                f"**Hypothesis:** {idea['hypothesis']}",
                "",
                f"**Rationale:** {idea.get('rationale', '')}",
                "",
                f"**Param change:** `{json.dumps(idea.get('param'), ensure_ascii=False)}`",
                "",
                "**Status:** ⏳ Pending CIO approval → will be backtested next Sunday if approved",
                "",
                "---",
                "",
            ]

        lines += [
            "## Decision Protocol",
            "",
            "```",
            "Chief Quant proposes → agent_ideas.json (status=pending)",
            "    ↓",
            "system_optimizer backtests Sunday (auto)",
            "    ↓",
            "If PASS → BoA agenda opened + Telegram alert to CIO",
            "    ↓",
            "CIO reviews → approves or rejects",
            "    ↓",
            "Only after CIO approval → implement in S4",
            "```",
            "",
            "## Links",
            "[[05_Quant/Agent_KPI]]",
            f"[[05_Quant/Optimizer_Results_{TODAY}]]",
        ]

        obs_write(f"05_Quant/ChiefQuant_Synthesis_{TODAY}.md", "\n".join(lines))
    except Exception as e:
        log.warning(f"Obsidian: {e}")


def _send_telegram(summary: dict, ideas: list) -> None:
    import os, requests
    bot = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not bot or not chat:
        return

    lines = [
        f"🧠 CHIEF QUANT SYNTHESIS [{summary['date']}]",
        f"Regime: {summary['regime']} | Leaderboard: {summary['leaderboard_entries']} entries",
        f"Generated: {summary['ideas_generated']} proposals",
        "",
        "THIS WEEK'S PROPOSALS:",
    ]
    for i, idea in enumerate(ideas[:4], 1):
        lines.append(f"{i}. {idea['title'][:60]}")

    lines += [
        "",
        "⚠️ ALL proposals pending CIO approval",
        "→ Nothing deployed. Backtested Sunday.",
        f"→ Top AlphaScore so far: {summary['top_alpha_score']}",
    ]

    try:
        requests.post(
            f"https://api.telegram.org/bot{bot}/sendMessage",
            json={"chat_id": chat, "text": "\n".join(lines)},
            timeout=10,
            verify=False,
        )
    except Exception as e:
        log.warning(f"Telegram: {e}")


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
