"""
AlphaAbsolute -- Current State Updater
Syncs live pipeline data -> Obsidian 99_Current_State/state.md

Reads:
  data/regime/market_health.json   (A01 output)
  data/regime/macro_state.json     (A02 output)
  data/rs_universe/theme_rs_latest.json (A05 output)
  data/paper_trading/state.json (System 4 v4_paper_trader output)

Writes: Obsidian 99_Current_State/state.md

Called by: session_start.py + pre_market_runner.py (post-report step)
Cost: $0 -- pure file I/O
"""
import sys, json
from pathlib import Path
from datetime import date, datetime
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.brain.obsidian_writer_v2 import write, is_available

# ── Data loaders ──────────────────────────────────────────────────────────────

def _load(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None


def _regime_icon(regime: str) -> str:
    return {"Markup": "GREEN", "Sideways": "YELLOW", "Distribution": "ORANGE",
            "Markdown": "RED"}.get(regime, "GREY")


def _pct(v, total) -> str:
    if not total:
        return "--"
    return f"{v/total*100:.0f}%"


# ── Builder ───────────────────────────────────────────────────────────────────

def build_state_note() -> str:
    today = date.today().isoformat()
    now   = datetime.now().strftime("%H:%M")

    mh    = _load(ROOT / "data/regime/market_health.json") or {}
    macro = _load(ROOT / "data/regime/macro_state.json") or {}
    theme = _load(ROOT / "data/rs_universe/theme_rs_latest.json") or {}
    port  = _load(ROOT / "data/paper_trading/state.json") or {}

    regime = mh.get("regime", "Unknown")
    score  = mh.get("effective_score", mh.get("score", "?"))
    cash_floor   = mh.get("cash_floor", "?")
    max_deployed = mh.get("max_deployed", "?")
    regime_note  = mh.get("regime_note", "")
    dist_days    = mh.get("distribution_days", "?")
    abv50        = mh.get("pct_above_50dma", "?")
    abv200       = mh.get("pct_above_200dma", "?")

    rate_env  = macro.get("rate_environment", "?")
    hy_spread = macro.get("hy_spread_bps", "?")
    ycurve    = macro.get("yield_curve", "?")
    macro_mod = macro.get("macro_modifier", 1.0)
    macro_note= macro.get("macro_note", "")

    # Theme hot/warm/weak
    hot = []; warm = []; weak = []
    theme_rows = ""
    if isinstance(theme, dict):
        themes_list = theme.get("themes", theme)
        if isinstance(themes_list, list):
            for t in themes_list:
                n  = t.get("theme", t.get("name", "?"))
                pct_val = t.get("theme_rs_pct", t.get("pct", 0))
                if pct_val >= 75:
                    hot.append(n)
                elif pct_val >= 50:
                    warm.append(n)
                else:
                    weak.append(n)
                theme_rows += f"| {n} | {pct_val:.0f}th | {'HOT' if pct_val>=75 else 'WARM' if pct_val>=50 else 'WEAK'} |\n"
        elif isinstance(themes_list, dict):
            for n, data in themes_list.items():
                if isinstance(data, dict):
                    pct_val = data.get("theme_rs_pct", data.get("pct", 0))
                    if pct_val >= 75:
                        hot.append(n)
                    elif pct_val >= 50:
                        warm.append(n)
                    else:
                        weak.append(n)
                    theme_rows += f"| {n} | {pct_val:.0f}th | {'HOT' if pct_val>=75 else 'WARM' if pct_val>=50 else 'WEAK'} |\n"

    # Portfolio
    positions = port.get("positions", port.get("holdings", {}))
    n_pos = len(positions) if isinstance(positions, dict) else 0
    cash = port.get("cash", port.get("cash_balance", 0))
    nav  = port.get("nav", port.get("total_value", 0))
    nav_pct  = port.get("nav_return_pct", port.get("pnl_pct", 0))
    qqq_pct  = port.get("qqq_return_pct", 0)
    alpha    = nav_pct - qqq_pct if isinstance(nav_pct, (int, float)) and isinstance(qqq_pct, (int, float)) else 0
    cash_pct = (cash / nav * 100) if nav else 0

    pos_rows = ""
    if isinstance(positions, dict):
        for ticker, p in list(positions.items())[:10]:
            ep  = p.get("entry_price", 0)
            cur = p.get("current_price", p.get("last_price", ep))
            pnl = (cur - ep) / ep * 100 if ep else 0
            pnl_str = f"+{pnl:.1f}%" if pnl >= 0 else f"{pnl:.1f}%"
            sz  = p.get("size_pct", p.get("weight_pct", 0))
            pos_rows += f"| {ticker} | {pnl_str} | {sz:.0f}% |\n"

    # Early warnings
    warnings = mh.get("early_warnings", [])
    warn_str = ""
    if warnings:
        for w in warnings:
            if isinstance(w, dict):
                warn_str += f"- **{w.get('name', w)}**: {w.get('description', '')}\n"
            else:
                warn_str += f"- {w}\n"

    # Belief delta (what changed this week -- placeholder for manual fill)
    icon = _regime_icon(regime)

    note = f"""\
---
type: current_state
regime: {regime}
regime_score: {score}
updated: {today}
updated_time: {now}
auto_update: true
cash_floor: {cash_floor}
max_deployed: {max_deployed}
---

# Current State -- {today} {now}

> Auto-updated by pipeline. Add manual conviction notes below the --- line.

## Regime: {regime} [{icon}]

| Factor | Value |
|--------|-------|
| Score | {score}/85 |
| Cash floor | {int(float(cash_floor)*100) if cash_floor != '?' else '?'}% |
| Max deployed | {int(float(max_deployed)*100) if max_deployed != '?' else '?'}% |
| New entries ok | {'YES' if str(mh.get('new_entries_ok', mh.get('leaders_ok', True))).lower() == 'true' else 'NO'} |
| Dist days | {dist_days} |
| % above 50DMA | {abv50}% |
| % above 200DMA | {abv200}% |
| Regime note | {regime_note} |

{"#### Early Warnings" + chr(10) + warn_str if warn_str else ""}

## Macro

| Factor | Reading |
|--------|---------|
| Rate environment | {rate_env} |
| HY spread | {hy_spread} bps |
| Yield curve | {ycurve} |
| Macro modifier | {macro_mod}x |

{macro_note}

## Themes

| Theme | RS Pct | Status |
|-------|--------|--------|
{theme_rows if theme_rows else "| -- | -- | No data |\n"}

**HOT:** {", ".join(hot) if hot else "--"}
**WARM:** {", ".join(warm) if warm else "--"}
**WEAK:** {", ".join(weak) if weak else "--"}

## Portfolio

| Metric | Value |
|--------|-------|
| Positions | {n_pos} |
| Cash | {cash_pct:.1f}% |
| NAV return | {nav_pct:+.1f}% |
| QQQ return | {qqq_pct:+.1f}% |
| Alpha | {alpha:+.1f}% |

{"#### Positions" + chr(10) + "| Ticker | P&L | Size |" + chr(10) + "|--------|-----|------|" + chr(10) + pos_rows if pos_rows else ""}

---
## Manual Conviction Notes

_What I believe now vs last week:_

_Primary risks:_
1.
2.
3.

_Dominant narrative vs my read:_

"""
    return note


def run() -> str:
    if not is_available():
        return "[CurrentState] Obsidian vault not available -- skipped"
    note = build_state_note()
    ok   = write("99_Current_State/state.md", note)
    regime = json.loads((ROOT / "data/regime/market_health.json").read_text(encoding="utf-8")).get("regime", "?") \
        if (ROOT / "data/regime/market_health.json").exists() else "?"
    return f"[CurrentState] {'OK' if ok else 'FAIL'} -- 99_Current_State/state.md | Regime: {regime}"


if __name__ == "__main__":
    print(run())
