"""
Agent Ideas — submission system for all agents to propose hypotheses.

Any agent can call submit_idea() to register a testable hypothesis.
System Optimizer picks these up and backtests them Sunday.
Agent KPI leaderboard tracks who finds the best signals.

KPI per agent:
  - ideas_submitted
  - ideas_tested
  - ideas_passed   (beat S4 baseline AlphaScore by > 0.5)
  - pass_rate      (passed / tested)
  - best_alpha_score_found
  - total_alpha_improvement  (sum of AlphaScore gains from passed ideas)
  - rank           (sorted by total_alpha_improvement)

Output:
  data/research/agent_ideas.json      — all submitted ideas
  data/research/agent_kpi.json        — per-agent leaderboard
  Obsidian: 05_Quant/Agent_KPI.md     — human-readable leaderboard
"""

from __future__ import annotations
import json
import hashlib
import logging
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "research"
DATA_DIR.mkdir(parents=True, exist_ok=True)

IDEAS_FILE = DATA_DIR / "agent_ideas.json"
KPI_FILE = DATA_DIR / "agent_kpi.json"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# Known agents
# ---------------------------------------------------------------------------
AGENTS = {
    "da-quant":           "Quantitative Analyst — signal validation + backtest",
    "da-analyst":         "Data Analyst — data quality + screening interpretation",
    "research-hunter":    "Research Hunter — papers from arXiv/SSRN/AQR",
    "signal-extractor":   "Signal Extractor — hypotheses from Obsidian notes",
    "a03-rs-ranker":      "RS Universe Ranker — relative strength signals",
    "chief-quant":        "Chief Quant — cross-signal synthesis for System 4",
    "system-optimizer":   "System Optimizer — grid search configs vs S4 baseline",
    "hypothesis-tester":  "Hypothesis Tester — backtest pending ideas vs S4 baseline",
    "board-secretary":    "Board of Alpha — governance + cross-agent synthesis",
    "cio":                "CIO — manual ideas from Piriyapon",
}

# ---------------------------------------------------------------------------
# Submit an idea
# ---------------------------------------------------------------------------

def submit_idea(
    agent: str,
    title: str,
    hypothesis: str,
    param: dict | None = None,
    rationale: str = "",
    source: str = "",
) -> str:
    """
    Submit a testable hypothesis.

    param (optional): structured parameter change for S4 optimizer:
      {
        "param": "vol_lookback_days",   # S4 parameter name
        "value": 63,                    # proposed value
        "baseline": 20,                 # current S4 value
      }

    Or for search-space profile changes:
      {
        "param": "rs_weight_profile",
        "value": "long_only",
        "baseline": "balanced",
      }

    Returns idea_id.
    """
    if agent not in AGENTS:
        log.warning(f"Unknown agent '{agent}' — submitting anyway")

    idea_id = hashlib.md5(f"{agent}{title}{hypothesis}".encode()).hexdigest()[:10]

    ideas = _load_ideas()
    if any(i["idea_id"] == idea_id for i in ideas):
        log.info(f"Idea already exists: {idea_id} ({title[:50]})")
        return idea_id

    idea = {
        "idea_id": idea_id,
        "agent": agent,
        "title": title,
        "hypothesis": hypothesis,
        "param": param,
        "rationale": rationale,
        "source": source,
        "submitted": TODAY,
        "status": "pending",
        "backtest_result": None,
        "alpha_score_gain": None,
    }

    ideas.append(idea)
    _save_ideas(ideas)
    log.info(f"[{agent}] Submitted: {title[:60]} (id={idea_id})")
    return idea_id


# ---------------------------------------------------------------------------
# Mark result (called by system_optimizer after backtest)
# ---------------------------------------------------------------------------

def record_result(idea_id: str, backtest_result: dict, baseline_alpha_score: float) -> None:
    ideas = _load_ideas()
    for idea in ideas:
        if idea["idea_id"] == idea_id:
            verdict = backtest_result.get("verdict", "FAIL")
            alpha_gain = backtest_result.get("alpha_score", 0) - baseline_alpha_score
            idea["status"] = "passed" if verdict == "PASS" else "failed"
            idea["backtest_result"] = backtest_result
            idea["alpha_score_gain"] = round(alpha_gain, 2)
            break
    _save_ideas(ideas)
    _rebuild_kpi(ideas, baseline_alpha_score)


# ---------------------------------------------------------------------------
# KPI leaderboard
# ---------------------------------------------------------------------------

def _rebuild_kpi(ideas: list[dict], baseline_score: float) -> None:
    kpi: dict[str, dict] = {a: {
        "agent": a,
        "role": AGENTS.get(a, "unknown"),
        "ideas_submitted": 0,
        "ideas_tested": 0,
        "ideas_passed": 0,
        "pass_rate": 0.0,
        "best_alpha_score": 0.0,
        "total_alpha_improvement": 0.0,
        "rank": 0,
    } for a in AGENTS}

    # include agents not in AGENTS dict
    for idea in ideas:
        a = idea["agent"]
        if a not in kpi:
            kpi[a] = {
                "agent": a,
                "role": "unknown",
                "ideas_submitted": 0,
                "ideas_tested": 0,
                "ideas_passed": 0,
                "pass_rate": 0.0,
                "best_alpha_score": 0.0,
                "total_alpha_improvement": 0.0,
                "rank": 0,
            }

        kpi[a]["ideas_submitted"] += 1

        if idea["status"] in ("passed", "failed"):
            kpi[a]["ideas_tested"] += 1
            br = idea.get("backtest_result") or {}
            score = br.get("alpha_score", 0)
            gain = idea.get("alpha_score_gain") or 0

            if idea["status"] == "passed":
                kpi[a]["ideas_passed"] += 1
                kpi[a]["total_alpha_improvement"] += gain
                if score > kpi[a]["best_alpha_score"]:
                    kpi[a]["best_alpha_score"] = score

    # Compute pass_rate
    for a in kpi:
        tested = kpi[a]["ideas_tested"]
        passed = kpi[a]["ideas_passed"]
        kpi[a]["pass_rate"] = round(passed / tested * 100, 1) if tested > 0 else 0.0

    # Rank by total_alpha_improvement, then by ideas_passed
    ranked = sorted(
        kpi.values(),
        key=lambda x: (x["total_alpha_improvement"], x["ideas_passed"]),
        reverse=True,
    )
    for i, row in enumerate(ranked):
        row["rank"] = i + 1

    result = {
        "updated": TODAY,
        "baseline_alpha_score": round(baseline_score, 2),
        "leaderboard": ranked,
    }
    KPI_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_obsidian_kpi(result)


def _write_obsidian_kpi(data: dict) -> None:
    try:
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from scripts.brain.obsidian_writer_v2 import write as obs_write

        board = data["leaderboard"]
        active = [r for r in board if r["ideas_submitted"] > 0]

        lines = [
            "---",
            "type: agent_kpi",
            f"updated: {data['updated']}",
            "tags: [agent-kpi, optimizer, competition, auto]",
            "---",
            "",
            "# Agent KPI Leaderboard",
            "",
            f"> Baseline AlphaScore: **{data['baseline_alpha_score']}** (S4 current)",
            f"> Updated: {data['updated']}",
            "",
            "## Rankings",
            "",
            "| Rank | Agent | Submitted | Tested | Passed | Pass% | Best Score | Total Gain |",
            "|------|-------|-----------|--------|--------|-------|-----------|-----------|",
        ]

        for r in active:
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(r["rank"], f"#{r['rank']}")
            lines.append(
                f"| {medal} | **{r['agent']}** | {r['ideas_submitted']} "
                f"| {r['ideas_tested']} | {r['ideas_passed']} "
                f"| {r['pass_rate']}% | {r['best_alpha_score']} "
                f"| **+{r['total_alpha_improvement']:.1f}** |"
            )

        lines += [
            "",
            "## How to Submit an Idea",
            "",
            "```python",
            "from scripts.brain.agent_ideas import submit_idea",
            "",
            "submit_idea(",
            '    agent="da-quant",',
            '    title="Test 63d vol window",',
            '    hypothesis="63d vol lookback smoother than 20d for monthly rebalance",',
            '    param={"param": "vol_lookback_days", "value": 63, "baseline": 20},',
            '    rationale="Literature consensus: 63d vol for monthly rebalance (Barroso 2015)",',
            '    source="151_Trading_Strategies.md",',
            ")",
            "```",
            "",
            "Ideas are backtested every Sunday. Pass = beats S4 AlphaScore by > 0.5 points.",
            "",
            "## Links",
            "[[05_Quant/Optimizer_Results_" + TODAY + "]]",
            "[[data/research/agent_kpi.json]]",
        ]

        obs_write("05_Quant/Agent_KPI.md", "\n".join(lines))
    except Exception as e:
        log.warning(f"Obsidian KPI: {e}")


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def _load_ideas() -> list[dict]:
    if IDEAS_FILE.exists():
        return json.loads(IDEAS_FILE.read_text(encoding="utf-8"))
    return []


def _save_ideas(ideas: list[dict]) -> None:
    IDEAS_FILE.write_text(json.dumps(ideas, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Pre-seeded ideas from each agent (called once on first run)
# ---------------------------------------------------------------------------

def seed_initial_ideas() -> None:
    """Seed ideas from each agent based on their domain expertise."""

    ideas_to_seed = [
        # da-quant: from literature
        dict(
            agent="da-quant",
            title="63d vol window vs 20d for monthly rebalance",
            hypothesis="63d realized vol is smoother and better matched to 21d holding period than 20d",
            param={"param": "vol_lookback_days", "value": 63, "baseline": 20},
            rationale="Barroso & Santa-Clara 2015: vol-scaled momentum uses 1M/6M windows. 63d = 3M = consensus for monthly rebalance.",
            source="151_Trading_Strategies.md",
        ),
        dict(
            agent="da-quant",
            title="Skip-period 21d to avoid short-term reversal",
            hypothesis="Skipping last 21 trading days avoids 1-month reversal effect in momentum portfolios",
            param={"param": "skip_days", "value": 21, "baseline": 0},
            rationale="Jegadeesh & Titman 1993: standard momentum skips last 1M. Reduces bid-ask bounce contamination.",
            source="151_Trading_Strategies.md",
        ),
        dict(
            agent="da-quant",
            title="Long-only RS weights: heavy 252d",
            hypothesis="Weighting RS composite toward 252d (0.05/0.10/0.35/0.50) captures more sustained momentum",
            param={"param": "rs_weight_profile", "value": "long_only", "baseline": "balanced"},
            rationale="12M momentum has strongest academic evidence. Short-term noise adds volatility without alpha.",
            source="151_Trading_Strategies.md",
        ),
        dict(
            agent="da-quant",
            title="Bimonthly rebalance (42d) to reduce turnover cost",
            hypothesis="42-day rebalance halves turnover, reducing transaction costs. Net alpha may improve.",
            param={"param": "rebalance_days", "value": 42, "baseline": 21},
            rationale="Asness et al. (AQR): momentum decay is slow enough that 2M holding still captures most of the premium.",
            source="AQR Capital Research",
        ),
        # da-analyst: data-driven observations
        dict(
            agent="da-analyst",
            title="SPY regime gate vs IWM",
            hypothesis="SPY 200d MA is more stable regime signal than IWM for large-cap momentum portfolio",
            param={"param": "regime_ticker", "value": "SPY", "baseline": "IWM"},
            rationale="S4 universe is SP500+Nasdaq (large cap). SPY regime more correlated with our universe than IWM (small cap).",
            source="data observation",
        ),
        dict(
            agent="da-analyst",
            title="No regime gate (always invested)",
            hypothesis="Regime gate may cause whipsawing in volatile periods. Equal exposure always invested may outperform over full cycle.",
            param={"param": "regime_ticker", "value": "none", "baseline": "IWM"},
            rationale="Current data (2024-2026) is bull market — regime gate costs alpha in bull. Test full-cycle performance.",
            source="data observation",
        ),
        dict(
            agent="da-analyst",
            title="Concentrated top 10 vs top 15",
            hypothesis="Top 10 stocks by RS have higher average RS quality than top 15. Concentration may improve Sharpe.",
            param={"param": "top_n", "value": 10, "baseline": 15},
            rationale="IBD research: top decile RS stocks outperform significantly. More concentration = more factor purity.",
            source="IBD methodology",
        ),
        # a01-market-health: regime-driven
        dict(
            agent="a01-market-health",
            title="Wider portfolio (top 20) in high-breadth regimes",
            hypothesis="When breadth is strong (>70% above 50DMA), widening to top 20 captures more of the rising tide",
            param={"param": "top_n", "value": 20, "baseline": 15},
            rationale="In BULL regime with strong breadth, more stocks participate. Concentration less important.",
            source="s4_state.json regime",
        ),
        # a03-rs-ranker: RS signal ideas
        dict(
            agent="a03-rs-ranker",
            title="Medium-focus RS weights: heavy 126d",
            hypothesis="6M (126d) momentum has best balance of signal persistence and responsiveness",
            param={"param": "rs_weight_profile", "value": "medium_focus", "baseline": "balanced"},
            rationale="RS inflection at 3-6M is most actionable for institutional accumulation detection.",
            source="RS ranker observation",
        ),
        # research-hunter: from papers
        dict(
            agent="research-hunter",
            title="Short-boost RS weights: more weight on 20d/60d",
            hypothesis="Short-term RS (20d/60d) captures momentum acceleration phase better than long-term only",
            param={"param": "rs_weight_profile", "value": "short_boost", "baseline": "balanced"},
            rationale="Momentum acceleration (2nd derivative of RS) is a stronger signal than level. Short-term RS captures this.",
            source="arXiv q-fin.PM",
        ),
        # board-secretary: governance ideas
        dict(
            agent="board-secretary",
            title="Equal weight sizing vs vol-parity",
            hypothesis="Equal weight avoids over-concentrating in low-vol stocks which may be defensive names, not leaders",
            param={"param": "sizing_method", "value": "equal_weight", "baseline": "vol_parity"},
            rationale="Vol-parity underweights high-vol growth leaders. Equal weight gives them fair representation.",
            source="governance review",
        ),
    ]

    submitted = 0
    for idea_kwargs in ideas_to_seed:
        idea_id = submit_idea(**idea_kwargs)
        submitted += 1

    log.info(f"Seeded {submitted} initial ideas from all agents")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def show_leaderboard() -> None:
    if not KPI_FILE.exists():
        print("No KPI data yet — run system_optimizer.py first")
        return
    data = json.loads(KPI_FILE.read_text(encoding="utf-8"))
    print(f"\n{'='*60}")
    print(f"AGENT KPI LEADERBOARD — {data['updated']}")
    print(f"Baseline AlphaScore: {data['baseline_alpha_score']}")
    print(f"{'='*60}")
    for r in data["leaderboard"]:
        if r["ideas_submitted"] == 0:
            continue
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(r["rank"], f"#{r['rank']}")
        print(f"{medal} {r['agent']:<25} submitted={r['ideas_submitted']} passed={r['ideas_passed']}/{r['ideas_tested']} pass%={r['pass_rate']}% gain=+{r['total_alpha_improvement']:.1f}")


def show_ideas(status: str = "all") -> None:
    ideas = _load_ideas()
    if status != "all":
        ideas = [i for i in ideas if i["status"] == status]
    print(f"\n{'='*60}")
    print(f"IDEAS ({status}) — {len(ideas)} total")
    print(f"{'='*60}")
    for idea in sorted(ideas, key=lambda x: x["submitted"], reverse=True):
        result_str = ""
        if idea.get("alpha_score_gain") is not None:
            result_str = f" → gain={idea['alpha_score_gain']:+.1f}"
        print(f"[{idea['status'].upper():<8}] [{idea['agent']:<20}] {idea['title'][:55]}{result_str}")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "seed"

    if cmd == "seed":
        seed_initial_ideas()
        ideas = _load_ideas()
        print(f"\nTotal ideas in queue: {len(ideas)}")
        show_ideas("pending")

    elif cmd == "leaderboard":
        show_leaderboard()

    elif cmd == "ideas":
        status = sys.argv[2] if len(sys.argv) > 2 else "all"
        show_ideas(status)
