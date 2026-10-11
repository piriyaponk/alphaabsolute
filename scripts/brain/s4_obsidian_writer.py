"""
S4 Brain — Obsidian Writer
Writes all S4 knowledge, trade logs, post-mortems, proposals, and weekly insights
to the Obsidian vault. ONLY supports System 4. No other systems.
"""
import json
import os
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
VAULT = Path(r"C:\Users\Pizza\OneDrive\Documents\Obsidian Vault\S4_Brain")


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


# ─────────────────────────────────────────────
# 1. TRADE LOG — write monthly rebalance snapshot
# ─────────────────────────────────────────────
def write_trade_log():
    state = _read_json(ROOT / "data" / "paper_trading" / "state.json")
    if not state:
        print("[SKIP] No paper trading state found")
        return

    ym = datetime.today().strftime("%Y-%m")
    out = VAULT / "02_TradeLogs" / f"{ym}_rebalance.md"

    positions = state.get("positions", {})
    regime = state.get("regime", "UNKNOWN")
    nav = state.get("nav", 0)
    deployed = state.get("deployed_pct", 0)

    lines = [
        f"# Rebalance Log — {ym}",
        f"*Written: {_today()}*",
        "",
        f"**Regime:** {regime} | **NAV:** ${nav:,.0f} | **Deployed:** {deployed:.1f}%",
        "",
        "## Positions This Month",
        "",
        "| Ticker | Weight | RS Pct | Entry |",
        "|--------|--------|--------|-------|",
    ]
    for tkr, pos in sorted(positions.items(), key=lambda x: -x[1].get("weight_pct", 0)):
        w = pos.get("weight_pct", 0)
        rs = pos.get("rs_pct", 0)
        entry = pos.get("entry_price", 0)
        lines.append(f"| {tkr} | {w:.1f}% | {rs:.0f} | ${entry:.2f} |")

    lines += [
        "",
        "## Signal State at Rebalance",
        f"- IWM regime gate: {regime}",
        f"- vol_trend threshold: {'0.75' if regime == 'BULL' else '0.95'}",
        f"- RS floor: 80th pct | Top N: 15",
        "",
        "## Notes",
        "*[CIO adds notes here if anything notable about this rebalance]*",
        "",
        "---",
        "[[System4_Locked]] | [[Signal_Calibration]]",
    ]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] Trade log written: {out.name}")


# ─────────────────────────────────────────────
# 2. POST-MORTEM — evaluate last month's picks
# ─────────────────────────────────────────────
def write_postmortem(results: dict | None = None):
    """
    results = {
        "month": "2026-08",
        "regime_correct": True,
        "portfolio_return": 5.2,
        "qqq_return": 1.3,
        "picks": [{"ticker": "RVMD", "weight": 12.5, "return": 8.4, "rs_at_entry": 99}, ...]
    }
    If results is None, tries to load from data/paper_trading/last_month_result.json
    """
    if results is None:
        results = _read_json(ROOT / "data" / "paper_trading" / "last_month_result.json")
    if not results:
        print("[SKIP] No post-mortem data found")
        return

    month = results.get("month", _today()[:7])
    out = VAULT / "03_PostMortems" / f"{month}_postmortem.md"

    port_ret = results.get("portfolio_return", 0)
    qqq_ret = results.get("qqq_return", 0)
    excess = port_ret - qqq_ret
    regime_ok = results.get("regime_correct", True)
    picks = results.get("picks", [])

    # Stocks we held
    winners = [p for p in picks if p.get("return", 0) > 0]
    losers  = [p for p in picks if p.get("return", 0) <= 0]
    hit_rate = len(winners) / len(picks) * 100 if picks else 0

    # Stocks we MISSED — were in universe but NOT in our top-15
    # Schema: {"ticker": "HIMS", "return": 42.1, "rs_at_entry": 94,
    #          "why_missed": "rank 18 — just outside top-15",
    #          "gate_failed": null}   ← null means passed all gates, just ranked 16+
    # Or:    {"gate_failed": "vol_trend=0.68 < 0.75", ...}
    missed = results.get("missed_leaders", [])   # top performers we didn't hold

    # Stocks we held that hurt us badly (worst losers)
    big_losers = sorted(
        [p for p in picks if p.get("return", 0) < -5],
        key=lambda x: x.get("return", 0)
    )

    lines = [
        f"# Post-Mortem — {month}",
        f"*Written: {_today()}*",
        "",
        "## Result Summary",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Portfolio return | {port_ret:+.1f}% |",
        f"| QQQ return | {qqq_ret:+.1f}% |",
        f"| Excess | {excess:+.1f}% |",
        f"| Hit rate | {hit_rate:.0f}% ({len(winners)}/{len(picks)}) |",
        f"| Regime call correct | {'✅ Yes' if regime_ok else '❌ No'} |",
        "",
        "## Our Picks — Full Scorecard",
        "",
        "| Ticker | Weight | RS@Entry | Return | Result |",
        "|--------|--------|----------|--------|--------|",
    ]

    for p in sorted(picks, key=lambda x: -x.get("return", 0)):
        tkr = p.get("ticker", "?")
        w   = p.get("weight", 0)
        rs  = p.get("rs_at_entry", 0)
        ret = p.get("return", 0)
        result = "✅" if ret > 0 else "❌"
        lines.append(f"| {tkr} | {w:.1f}% | {rs:.0f} | {ret:+.1f}% | {result} |")

    # ── MISSED LEADERS ───────────────────────────────────────
    lines += [
        "",
        "## 🔍 Leaders We MISSED",
        "",
        "> Stocks that ran big this month but weren't in our top-15.",
        "> Each row tells us WHY we missed — was it a gate failure or just ranked out?",
        "",
        "| Ticker | Return | RS@Entry | Why Missed | Gate Failed? |",
        "|--------|--------|----------|-----------|--------------|",
    ]

    if missed:
        for m in sorted(missed, key=lambda x: -x.get("return", 0))[:10]:
            tkr  = m.get("ticker", "?")
            ret  = m.get("return", 0)
            rs   = m.get("rs_at_entry", 0)
            why  = m.get("why_missed", "ranked outside top-15")
            gate = m.get("gate_failed") or "✅ passed all gates"
            lines.append(f"| {tkr} | {ret:+.1f}% | {rs:.0f} | {why} | {gate} |")
    else:
        lines.append("| *No data yet — auto-populate after first full month* | | | | |")

    lines += [
        "",
        "### Miss Analysis",
        "**Structural misses** (passed all gates, just ranked 16+):",
        "*→ These are acceptable — the system can't hold everything. Note if many were better than our rank-1.*",
        "",
        "**Gate-blocked misses** (failed vol_trend / MA200 / ADTV):",
        "*→ These are the important ones. Did the gate protect us correctly, or did it block a genuine leader?*",
        "",
    ]

    # ── BIG LOSERS (stocks we held that hurt) ────────────────
    lines += [
        "## 💥 Positions That Hurt Us (>-5%)",
        "",
        "| Ticker | Weight | RS@Entry | Return | What Went Wrong |",
        "|--------|--------|----------|--------|-----------------|",
    ]

    if big_losers:
        for p in big_losers:
            tkr  = p.get("ticker", "?")
            w    = p.get("weight", 0)
            rs   = p.get("rs_at_entry", 0)
            ret  = p.get("return", 0)
            note = p.get("loss_reason", "*[fill in]*")
            lines.append(f"| {tkr} | {w:.1f}% | {rs:.0f} | {ret:+.1f}% | {note} |")
    else:
        lines.append("| *No positions down >5% this month* ✅ | | | | |")

    lines += [
        "",
        "### Loss Classification",
        "For each big loser, classify:",
        "- **A — System error:** gate should have caught this (rs_pct was already fading?)",
        "- **B — Unavoidable:** gap-down earnings, external shock. System worked correctly.",
        "- **C — Timing:** position was right thesis but early. Recheck next month.",
        "",
    ]

    # ── LESSONS ──────────────────────────────────────────────
    lines += [
        "## 📝 Lessons",
        "",
        "**What worked this month:**",
        "*[Which signal predicted the winners? IWM gate right? RS rank accurate?]*",
        "",
        "**What failed:**",
        "*[Which gate blocked a winner? Which signal missed a loser?]*",
        "",
        "**One thing to test:**",
        "*[Single hypothesis from this month → send to BoA if AlphaScore delta > +1.5]*",
        "",
        "---",
        "[[System4_Locked]] | [[Signal_Calibration]]",
    ]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] Post-mortem written: {out.name}")


# ─────────────────────────────────────────────
# 3. KNOWLEDGE — save external research/book insight
# ─────────────────────────────────────────────
def write_knowledge(title: str, source: str, content: str, tags: list[str] | None = None):
    """
    Called when CIO or agent learns something from a book/article.
    title: short name
    source: book title, URL, paper name
    content: the insight in plain text
    tags: e.g. ["rs_momentum", "regime", "position_sizing"]
    """
    slug = title.lower().replace(" ", "_").replace("/", "-")[:50]
    out = VAULT / "01_Knowledge" / f"{_today()}_{slug}.md"

    tag_str = " ".join(f"#{t}" for t in (tags or []))

    lines = [
        f"# {title}",
        f"*Source: {source} | Logged: {_today()}*",
        f"{tag_str}",
        "",
        "## Insight",
        content,
        "",
        "## Relevance to System 4",
        "*[How does this apply? Which gate/parameter/rule does it inform?]*",
        "",
        "## Action",
        "- [ ] Backtest if quantifiable",
        "- [ ] Propose to BoA if delta AlphaScore > +1.5",
        "",
        "---",
        "[[System4_Locked]]",
    ]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] Knowledge note written: {out.name}")


# ─────────────────────────────────────────────
# 4. PROPOSAL — log BoA proposal with vote result
# ─────────────────────────────────────────────
def write_proposal(title: str, proposer: str, description: str,
                   alpha_score_delta: float, vote_result: str,
                   rationale: str, status: str = "PENDING"):
    """
    status: PENDING | APPROVED | REJECTED
    vote_result: "3/4 APPROVED" or "REJECTED 4/4" etc.
    """
    slug = title.lower().replace(" ", "_")[:40]
    folder = VAULT / "04_Proposals" / status
    out = folder / f"{_today()}_{slug}.md"

    emoji = {"APPROVED": "✅", "REJECTED": "❌", "PENDING": "⏳"}.get(status, "⏳")

    lines = [
        f"# {emoji} Proposal: {title}",
        f"*Proposed by: {proposer} | Date: {_today()} | Status: {status}*",
        "",
        "## Description",
        description,
        "",
        "## Expected Impact",
        f"AlphaScore delta: **{alpha_score_delta:+.1f}** (threshold: +1.5)",
        "",
        "## BoA Vote",
        f"**Result:** {vote_result}",
        "",
        "## Rationale",
        rationale,
    ]

    if status == "REJECTED":
        lines += [
            "",
            "## Revisit When",
            "*[What specific condition would make this worth re-testing?]*",
        ]

    lines += ["", "---", "[[System4_Locked]] | [[BoA_Decisions]]"]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] Proposal written ({status}): {out.name}")


# ─────────────────────────────────────────────
# 5. WEEKLY INSIGHT — agents tell their story
# ─────────────────────────────────────────────
def write_weekly_insight(insights: dict):
    """
    insights = {
        "fund_manager": "text...",
        "quant": "text...",
        "data_analyst": "text...",
        "research": "text...",
        "risk": "text...",
    }
    """
    week = datetime.today().strftime("%Y-W%V")
    out = VAULT / "05_WeeklyInsights" / f"{week}_insight.md"

    agent_emoji = {
        "fund_manager": "💼",
        "quant": "📊",
        "data_analyst": "🔍",
        "research": "📚",
        "risk": "🛡️",
    }

    lines = [
        f"# Weekly Insight — {week}",
        f"*Generated: {_today()}*",
        "",
        "> Five agents share what they learned this week about System 4.",
        "> All observations must connect to improving or understanding the baseline.",
        "",
    ]

    for agent, text in insights.items():
        emoji = agent_emoji.get(agent, "🤖")
        name = agent.replace("_", " ").title()
        lines += [
            f"## {emoji} {name}",
            text,
            "",
        ]

    lines += [
        "---",
        "## This Week's Open Questions",
        "*[Questions that came up but need more data/time to answer]*",
        "",
        "## Proposals in Pipeline",
        "*[Any ideas from above that cleared the +1.5 AlphaScore bar?]*",
        "",
        "[[System4_Locked]] | [[Signal_Calibration]]",
    ]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] Weekly insight written: {out.name}")


# ─────────────────────────────────────────────
# 6. SIGNAL CALIBRATION — monthly hit rate update
# ─────────────────────────────────────────────
def write_signal_calibration(calibration: dict | None = None):
    """
    calibration = {
        "updated": "2026-09",
        "signals": {
            "iwm_regime_gate": {"hit_rate": 0.91, "n": 113, "note": "..."},
            "rs_pct_80": {"hit_rate": 0.58, "n": 957, "note": "..."},
            "vol_trend_0.75": {"hit_rate": 0.61, "n": 820, "note": "..."},
            ...
        }
    }
    """
    if calibration is None:
        calibration = _read_json(ROOT / "data" / "brain" / "signal_calibration.json")
    if not calibration:
        calibration = {"updated": _today()[:7], "signals": {}}

    out = VAULT / "06_SignalCalibration" / "Signal_Calibration.md"

    lines = [
        "# Signal Calibration — System 4",
        f"*Last updated: {calibration.get('updated', _today()[:7])}*",
        "",
        "> Hit rates from actual rebalance outcomes. Updates monthly.",
        "> A signal dropping below 52% hit rate for 3+ months triggers BoA review.",
        "",
        "## Signal Hit Rates",
        "",
        "| Signal | Hit Rate | N | Note |",
        "|--------|----------|---|------|",
    ]

    for sig_name, data in calibration.get("signals", {}).items():
        hr = data.get("hit_rate", 0)
        n = data.get("n", 0)
        note = data.get("note", "")
        flag = " ⚠️" if (hr is not None and hr < 0.52) else ""
        hr_str = f"{hr*100:.1f}%" if hr is not None else "N/A"
        lines.append(f"| {sig_name} | {hr_str}{flag} | {n} | {note} |")

    lines += [
        "",
        "## Baseline AlphaScore: 25.0",
        "Any proposal must beat this by +1.5 to qualify for BoA review.",
        "",
        "---",
        "[[System4_Locked]]",
    ]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] Signal calibration written: {out.name}")


# ─────────────────────────────────────────────
# MAIN — called by Sunday research workflow
# ─────────────────────────────────────────────
def run():
    print("=" * 50)
    print("S4 Brain — Obsidian Writer")
    print("=" * 50)

    # Always update trade log and signal calibration
    write_trade_log()
    write_signal_calibration()

    # Post-mortem if last month result exists
    write_postmortem()

    print("[DONE] Obsidian vault updated. Open Obsidian → S4_Brain to view.")


if __name__ == "__main__":
    run()
