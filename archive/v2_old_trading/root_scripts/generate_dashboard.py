"""
AlphaAbsolute — CIO Dashboard Generator (Agent 17)
Reads portfolio.json, trade_log.json, and output/ reports → produces output/dashboard.html

Usage:
  python scripts/generate_dashboard.py

Opens automatically after generation.
"""

import json
import os
import webbrowser
from datetime import datetime
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
TRADE_LOG_FILE = DATA_DIR / "trade_log.json"
DASHBOARD_FILE = OUTPUT_DIR / "dashboard.html"

THEMES_14 = [
    "AI-Related", "Memory / HBM", "DefenseTech", "AI Infrastructure",
    "Photonics", "Nuclear / SMR", "Data Center", "Space",
    "Data Center Infra", "NeoCloud", "Robotics", "Connectivity",
    "Drone / UAV", "Quantum Computing",
]

SETUP_COLORS = {
    "Leader": "#00c896",
    "Bottom Fishing": "#4fa3e0",
    "Hypergrowth": "#f5a623",
    "Misprice": "#9b59b6",
}

# ── Data Loading ───────────────────────────────────────────────────────────────

def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def load_portfolio():
    data = load_json(PORTFOLIO_FILE, {"holdings": [], "allocation": {}, "last_updated": "—"})
    # Filter out template entries
    holdings = [h for h in data.get("holdings", []) if h.get("ticker") != "EXAMPLE"]
    data["holdings"] = holdings
    return data


def load_trades():
    data = load_json(TRADE_LOG_FILE, {"trades": []})
    trades = [t for t in data.get("trades", []) if t.get("ticker") != "EXAMPLE"]
    data["trades"] = trades
    return data


def load_recent_outputs():
    """Return the last 10 output .md files sorted newest first."""
    mds = sorted(OUTPUT_DIR.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    results = []
    for f in mds[:10]:
        results.append({
            "name": f.name,
            "date": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "size_kb": round(f.stat().st_size / 1024, 1),
        })
    return results

# ── Computation ────────────────────────────────────────────────────────────────

def compute_performance(portfolio, trades):
    """Compute basic performance metrics from holdings and closed trades."""
    holdings = portfolio["holdings"]
    closed = [t for t in trades["trades"] if t.get("outcome") in ("Win", "Loss") and t.get("realized_pnl_pct") is not None]

    total_realized_pnl = sum(t["realized_pnl_pct"] * t.get("size_pct", 0) / 100 for t in closed) if closed else 0
    open_unrealized = sum(
        (h.get("unrealized_pnl_pct") or 0) * h.get("weight_pct", 0) / 100
        for h in holdings
    )

    wins = [t for t in closed if t.get("realized_pnl_pct", 0) > 0]
    losses = [t for t in closed if t.get("realized_pnl_pct", 0) <= 0]
    win_rate = len(wins) / len(closed) * 100 if closed else 0
    avg_win = sum(t["realized_pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["realized_pnl_pct"] for t in losses) / len(losses) if losses else 0

    # Attribution by setup
    setup_stats = {}
    for t in closed:
        s = t.get("setup_type", "Unknown")
        if s not in setup_stats:
            setup_stats[s] = {"wins": 0, "total": 0, "pnl": 0}
        setup_stats[s]["total"] += 1
        setup_stats[s]["pnl"] += t.get("realized_pnl_pct", 0)
        if t.get("realized_pnl_pct", 0) > 0:
            setup_stats[s]["wins"] += 1

    # Theme attribution from holdings
    theme_stats = {}
    for h in holdings:
        theme = h.get("theme", "Unknown")
        if theme not in theme_stats:
            theme_stats[theme] = {"weight": 0, "unrealized": 0, "count": 0}
        theme_stats[theme]["weight"] += h.get("weight_pct", 0)
        theme_stats[theme]["unrealized"] += (h.get("unrealized_pnl_pct") or 0) * h.get("weight_pct", 0) / 100
        theme_stats[theme]["count"] += 1

    return {
        "total_pnl": total_realized_pnl + open_unrealized,
        "realized_pnl": total_realized_pnl,
        "open_unrealized": open_unrealized,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "trade_count": len(closed),
        "open_count": len(holdings),
        "setup_stats": setup_stats,
        "theme_stats": theme_stats,
    }


def compute_risk_flags(portfolio):
    flags = []
    holdings = portfolio["holdings"]
    for h in holdings:
        ticker = h.get("ticker", "?")
        # Earnings proximity
        earnings = h.get("earnings_date")
        if earnings:
            try:
                days_to = (datetime.strptime(earnings, "%Y-%m-%d") - datetime.now()).days
                if 0 <= days_to <= 14:
                    flags.append({"level": "red" if days_to <= 4 else "yellow", "ticker": ticker,
                                  "msg": f"Earnings in {days_to} days — consider reducing size"})
            except ValueError:
                pass
        # Stage deterioration
        stage = h.get("stage_weinstein", 2)
        if stage in (3, 4):
            flags.append({"level": "red", "ticker": ticker, "msg": f"Weinstein Stage {stage} — EXIT ZONE, do not hold"})
        # Large loss
        pnl = h.get("unrealized_pnl_pct")
        if pnl is not None and pnl <= -8:
            flags.append({"level": "red", "ticker": ticker, "msg": f"Down {pnl:.1f}% from entry — mandatory review"})
        elif pnl is not None and pnl <= -5:
            flags.append({"level": "yellow", "ticker": ticker, "msg": f"Down {pnl:.1f}% from entry — approaching stop"})
        # Overweight
        w = h.get("weight_pct", 0)
        if w > 15:
            flags.append({"level": "yellow", "ticker": ticker, "msg": f"Weight {w}% > 15% — concentration flag"})
    return flags

# ── HTML Builders ──────────────────────────────────────────────────────────────

def pnl_color(val):
    if val is None:
        return "#888"
    return "#00c896" if val >= 0 else "#e05555"


def pnl_str(val, suffix="%"):
    if val is None:
        return "—"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.1f}{suffix}"


def alloc_bar(pct, color, label):
    w = max(pct, 0)
    return f"""
    <div style="margin:6px 0">
      <div style="display:flex;align-items:center;gap:8px">
        <div style="width:120px;font-size:13px;color:#bbb">{label}</div>
        <div style="flex:1;background:#1e2530;border-radius:3px;height:18px;max-width:300px">
          <div style="width:{w}%;background:{color};height:18px;border-radius:3px;transition:width 0.4s"></div>
        </div>
        <div style="width:45px;text-align:right;font-weight:700;color:{color};font-size:14px">{pct:.0f}%</div>
      </div>
    </div>"""


def flag_row(flag):
    icon = "🔴" if flag["level"] == "red" else "🟡"
    return f'<div class="flag-row {flag["level"]}">{icon} <strong>{flag["ticker"]}</strong> — {flag["msg"]}</div>'


def holding_row(h):
    pnl = h.get("unrealized_pnl_pct")
    color = pnl_color(pnl)
    setup = h.get("setup_type", "?")
    sc = SETUP_COLORS.get(setup, "#888")
    gate = h.get("gate_verdict", "?")
    gate_color = {"GREEN": "#00c896", "YELLOW": "#f5a623", "RED": "#e05555"}.get(gate, "#888")
    stop = h.get("stop_loss_price", "?")
    entry = h.get("entry_price", "?")
    current = h.get("current_price") or "—"
    rs = h.get("rs_rank_1m", "—")
    stage = h.get("stage_weinstein", "?")
    wyckoff = h.get("wyckoff_signal", "—")
    weight = h.get("weight_pct", 0)
    why = h.get("why_bought", "—")
    sizing = h.get("why_this_weight", "—")
    theme = h.get("theme", "—")
    market = h.get("market", "?")
    pattern = h.get("pattern", "—")

    return f"""
    <tr>
      <td><strong>{h.get("ticker","?")}</strong><br><small style="color:#888">{h.get("name","")}</small></td>
      <td><span class="badge" style="background:{sc}">{setup}</span></td>
      <td style="color:#aaa;font-size:12px">{theme}</td>
      <td style="font-weight:700">{weight:.1f}%</td>
      <td>{entry}</td>
      <td>{current}</td>
      <td style="color:{color};font-weight:700">{pnl_str(pnl)}</td>
      <td>Stage {stage}</td>
      <td style="font-size:12px">{wyckoff}</td>
      <td>{rs}</td>
      <td style="color:#e05555">{stop}</td>
      <td><span style="color:{gate_color};font-weight:700">{gate}</span></td>
      <td style="font-size:11px;color:#bbb;max-width:220px">{why}</td>
      <td style="font-size:11px;color:#bbb;max-width:180px">{sizing}</td>
    </tr>"""


def trade_row(t):
    pnl = t.get("realized_pnl_pct")
    color = pnl_color(pnl)
    outcome = t.get("outcome", "Open")
    outcome_color = {"Win": "#00c896", "Loss": "#e05555", "Open": "#f5a623"}.get(outcome, "#888")
    return f"""
    <tr>
      <td>{t.get("date","?")}</td>
      <td><strong>{t.get("ticker","?")}</strong></td>
      <td>{t.get("action","?")}</td>
      <td>{t.get("setup_type","?")}</td>
      <td>{t.get("size_pct","?"):.1f}%</td>
      <td>{t.get("price","?")}</td>
      <td>{t.get("exit_price") or "—"}</td>
      <td style="color:{color};font-weight:700">{pnl_str(pnl)}</td>
      <td><span style="color:{outcome_color}">{outcome}</span></td>
      <td>{t.get("wyckoff_signal","—")}</td>
      <td style="font-size:11px;color:#bbb">{t.get("exit_reason") or "—"}</td>
    </tr>"""


def theme_heatmap_row(theme, stats):
    stat = stats.get(theme, {})
    weight = stat.get("weight", 0)
    unrealized = stat.get("unrealized", 0)
    count = stat.get("count", 0)
    contrib_color = pnl_color(unrealized)

    signal = "🟡 HOLD" if count == 0 else ("🟢 HOLD" if unrealized >= 0 else "🔴 WATCH")
    wt_str = f"{weight:.1f}%" if weight > 0 else "—"
    contrib_str = pnl_str(unrealized) if weight > 0 else "—"

    return (
        f"<tr><td>{theme}</td>"
        f"<td>🟡</td><td>🟡</td><td>🟡</td><td>🟡</td>"
        f"<td>{signal}</td><td>{wt_str}</td>"
        f'<td style="color:{contrib_color}">{contrib_str}</td></tr>'
    )


def theme_attr_row(theme, theme_stats):
    stat = theme_stats.get(theme, {})
    weight = stat.get("weight", 0)
    unrealized = stat.get("unrealized", None) if stat else None
    wt_str = f"{weight:.1f}%" if weight > 0 else "—"
    contrib_str = pnl_str(unrealized) if stat else "—"
    color = pnl_color(unrealized)
    return (
        f"<tr><td>{theme}</td><td>{wt_str}</td>"
        f'<td style="color:{color}">{contrib_str}</td></tr>'
    )


def setup_attr_row(setup, stat):
    total = stat.get("total", 0)
    wins = stat.get("wins", 0)
    pnl = stat.get("pnl", 0)
    wr = wins / total * 100 if total > 0 else 0
    avg = pnl / total if total > 0 else 0
    color = SETUP_COLORS.get(setup, "#888")
    return f"""
    <tr>
      <td><span class="badge" style="background:{color}">{setup}</span></td>
      <td>{total}</td>
      <td style="color:{'#00c896' if wr>=50 else '#e05555'};font-weight:700">{wr:.0f}%</td>
      <td style="color:{pnl_color(avg)};font-weight:700">{pnl_str(avg)}</td>
    </tr>"""


def output_file_row(f):
    return f'<tr><td><a href="../output/{f["name"]}" style="color:#4fa3e0">{f["name"]}</a></td><td>{f["date"]}</td><td>{f["size_kb"]} KB</td></tr>'

# ── Main HTML ──────────────────────────────────────────────────────────────────

def generate_html(portfolio, trades, perf, flags, outputs):
    now = datetime.now().strftime("%d %b %Y  %H:%M")
    last_updated = portfolio.get("last_updated", "—")
    holdings = portfolio["holdings"]
    alloc = portfolio.get("allocation", {})
    regime = portfolio.get("regime", "—")

    stocks_pct = alloc.get("stocks_total_pct", 0)
    gold_pct = alloc.get("gold_pct", 0)
    cash_pct = alloc.get("cash_pct", 100)
    us_pct = alloc.get("us_equity_pct", 0)
    thai_pct = alloc.get("thai_equity_pct", 0)
    breadth_us = portfolio.get("breadth_us_pct200dma")
    breadth_set = portfolio.get("breadth_set_pct200dma")

    # Breadth gate color
    def breadth_color(val):
        if val is None:
            return "#888"
        if val >= 60:
            return "#00c896"
        if val >= 40:
            return "#f5a623"
        return "#e05555"

    breadth_us_str = f"{breadth_us:.0f}%" if breadth_us is not None else "—"
    breadth_set_str = f"{breadth_set:.0f}%" if breadth_set is not None else "—"

    holdings_rows = "".join(holding_row(h) for h in holdings) if holdings else \
        '<tr><td colspan="14" style="text-align:center;color:#555;padding:40px">No open positions yet — run <code>update portfolio</code> to add positions</td></tr>'

    trade_rows = "".join(trade_row(t) for t in trades["trades"]) if trades["trades"] else \
        '<tr><td colspan="11" style="text-align:center;color:#555;padding:30px">No trades recorded yet</td></tr>'

    flag_rows = "".join(flag_row(f) for f in flags) if flags else \
        '<div style="color:#00c896;padding:12px">✅ No active risk flags</div>'

    theme_rows = "".join(theme_heatmap_row(t, perf["theme_stats"]) for t in THEMES_14)

    setup_rows = "".join(setup_attr_row(s, v) for s, v in perf["setup_stats"].items()) if perf["setup_stats"] else \
        '<tr><td colspan="4" style="color:#555;text-align:center">No closed trades yet</td></tr>'

    output_rows = "".join(output_file_row(f) for f in outputs) if outputs else \
        '<tr><td colspan="3" style="color:#555;text-align:center">No reports generated yet</td></tr>'

    pnl_total_color = pnl_color(perf["total_pnl"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AlphaAbsolute CIO Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0d1117; color: #e6edf3; font-size: 14px; }}
  .header {{ background: #161b22; border-bottom: 1px solid #30363d; padding: 16px 28px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; }}
  .header h1 {{ font-size: 18px; font-weight: 700; color: #fff; letter-spacing: 1px; }}
  .header .meta {{ color: #8b949e; font-size: 12px; text-align: right; line-height: 1.6; }}
  .container {{ max-width: 1600px; margin: 0 auto; padding: 20px 24px; }}
  .panel {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin-bottom: 20px; }}
  .panel h2 {{ font-size: 13px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 16px; border-bottom: 1px solid #21262d; padding-bottom: 10px; }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  .grid3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; }}
  .stat-box {{ background: #0d1117; border: 1px solid #21262d; border-radius: 6px; padding: 16px; }}
  .stat-box .label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 6px; }}
  .stat-box .value {{ font-size: 26px; font-weight: 700; }}
  .stat-box .sub {{ font-size: 12px; color: #8b949e; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #1c2128; color: #8b949e; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.6px; padding: 8px 10px; text-align: left; border-bottom: 1px solid #30363d; white-space: nowrap; }}
  td {{ padding: 10px 10px; border-bottom: 1px solid #21262d; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #1c2128; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; color: #fff; }}
  .flag-row {{ padding: 10px 14px; border-radius: 5px; margin: 6px 0; font-size: 13px; }}
  .flag-row.red {{ background: rgba(224,85,85,0.12); border-left: 3px solid #e05555; }}
  .flag-row.yellow {{ background: rgba(245,166,35,0.1); border-left: 3px solid #f5a623; }}
  .regime-chip {{ display: inline-block; padding: 4px 14px; border-radius: 20px; font-size: 12px; font-weight: 700; background: #1c2128; border: 1px solid #30363d; }}
  .scrollable {{ overflow-x: auto; }}
  code {{ background: #1c2128; padding: 1px 6px; border-radius: 3px; font-size: 12px; color: #79c0ff; }}
  a {{ color: #4fa3e0; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .empty-msg {{ text-align: center; color: #555; padding: 40px; }}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>⚡ AlphaAbsolute CIO Dashboard</h1>
    <div style="font-size:12px;color:#8b949e;margin-top:4px">Regime: <span class="regime-chip">{regime}</span></div>
  </div>
  <div class="meta">
    Generated: {now}<br>
    Portfolio as of: {last_updated}
  </div>
</div>

<div class="container">

<!-- PANEL 1: Portfolio Snapshot -->
<div class="panel">
  <h2>📊 Portfolio Snapshot</h2>
  <div class="grid2">
    <div>
      <div style="font-size:12px;color:#8b949e;margin-bottom:12px;text-transform:uppercase;letter-spacing:0.8px">Asset Allocation</div>
      {alloc_bar(stocks_pct, "#00c896", "Stocks")}
      {alloc_bar(us_pct, "#4fa3e0", "  └ US Equity")}
      {alloc_bar(thai_pct, "#1a9e6e", "  └ Thai Equity")}
      {alloc_bar(gold_pct, "#f5a623", "Gold")}
      {alloc_bar(cash_pct, "#8b949e", "Cash")}
    </div>
    <div>
      <div class="grid2" style="gap:12px">
        <div class="stat-box">
          <div class="label">Total P&L</div>
          <div class="value" style="color:{pnl_total_color}">{pnl_str(perf["total_pnl"])}</div>
          <div class="sub">Realized + Unrealized</div>
        </div>
        <div class="stat-box">
          <div class="label">Win Rate</div>
          <div class="value" style="color:{'#00c896' if perf['win_rate']>=50 else '#e05555'}">{perf["win_rate"]:.0f}%</div>
          <div class="sub">{perf["trade_count"]} closed trades</div>
        </div>
        <div class="stat-box">
          <div class="label">Avg Winner</div>
          <div class="value" style="color:#00c896">{pnl_str(perf["avg_win"])}</div>
        </div>
        <div class="stat-box">
          <div class="label">Avg Loser</div>
          <div class="value" style="color:#e05555">{pnl_str(perf["avg_loss"])}</div>
        </div>
      </div>
      <div style="margin-top:16px;display:flex;gap:16px">
        <div class="stat-box" style="flex:1;text-align:center">
          <div class="label">US Breadth %&gt;200DMA</div>
          <div style="font-size:20px;font-weight:700;color:{breadth_color(breadth_us)}">{breadth_us_str}</div>
        </div>
        <div class="stat-box" style="flex:1;text-align:center">
          <div class="label">SET Breadth %&gt;200DMA</div>
          <div style="font-size:20px;font-weight:700;color:{breadth_color(breadth_set)}">{breadth_set_str}</div>
        </div>
        <div class="stat-box" style="flex:1;text-align:center">
          <div class="label">Open Positions</div>
          <div style="font-size:20px;font-weight:700;color:#e6edf3">{perf["open_count"]}</div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- PANEL 2: Holdings Table -->
<div class="panel">
  <h2>📋 Current Holdings</h2>
  <div class="scrollable">
    <table>
      <thead><tr>
        <th>Ticker</th><th>Setup</th><th>Theme</th><th>Weight</th>
        <th>Entry</th><th>Current</th><th>P&L</th>
        <th>Stage</th><th>Wyckoff</th><th>RS(1M)</th><th>Stop</th>
        <th>Gate</th><th>Why Bought</th><th>Sizing Reason</th>
      </tr></thead>
      <tbody>{holdings_rows}</tbody>
    </table>
  </div>
</div>

<!-- PANEL 3: Performance Attribution -->
<div class="panel">
  <h2>📈 Performance Attribution</h2>
  <div class="grid2">
    <div>
      <div style="font-size:12px;color:#8b949e;margin-bottom:10px;text-transform:uppercase;letter-spacing:0.8px">By Setup (Closed Trades)</div>
      <table>
        <thead><tr><th>Setup</th><th>Trades</th><th>Win Rate</th><th>Avg P&L</th></tr></thead>
        <tbody>{setup_rows}</tbody>
      </table>
    </div>
    <div>
      <div style="font-size:12px;color:#8b949e;margin-bottom:10px;text-transform:uppercase;letter-spacing:0.8px">By Theme (Open Positions)</div>
      <table>
        <thead><tr><th>Theme</th><th>Weight</th><th>Unrealized</th></tr></thead>
        <tbody>
        {"".join(theme_attr_row(t, perf["theme_stats"]) for t in THEMES_14)}
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- PANEL 4: Risk Monitor -->
<div class="panel">
  <h2>⚠️ Risk Monitor</h2>
  {flag_rows}
</div>

<!-- PANEL 5b: Theme Heatmap -->
<div class="panel">
  <h2>🌡️ 14 Megatrend Theme Heatmap</h2>
  <div class="scrollable">
    <table>
      <thead><tr>
        <th>Theme</th>
        <th>RS vs Mkt</th><th>News Flow</th><th>EPS Revisions</th><th>Inst. Flow</th>
        <th>Signal</th><th>Portfolio Wt</th><th>Contrib (Unrealized)</th>
      </tr></thead>
      <tbody>{theme_rows}</tbody>
    </table>
  </div>
  <div style="margin-top:10px;font-size:11px;color:#555">
    Note: RS / News / EPS / Inst. Flow signals update when Agent 5 (Thematic) runs weekly.
    Run <code>study [theme]</code> for a deep dive on any row.
  </div>
</div>

<!-- PANEL 5: Decision Log -->
<div class="panel">
  <h2>📝 Trade Log & Decision History</h2>
  <div class="scrollable">
    <table>
      <thead><tr>
        <th>Date</th><th>Ticker</th><th>Action</th><th>Setup</th><th>Size</th>
        <th>Entry</th><th>Exit</th><th>P&L</th><th>Outcome</th><th>Wyckoff</th><th>Exit Reason</th>
      </tr></thead>
      <tbody>{trade_rows}</tbody>
    </table>
  </div>
</div>

<!-- PANEL 6: Improvement Tracker -->
<div class="panel">
  <h2>🔧 Improvement Tracker — แก้ไขยังไง</h2>
  <div style="color:#555;padding:20px;text-align:center" id="improvements-placeholder">
    No improvements logged yet. Improvements are added automatically by Agent 13 (Performance)
    after post-mortems. Run <code>what went wrong with [TICKER]</code> after any loss.
    <br><br>
    Framework updates are tracked in <code>memory/framework_updates.md</code>
  </div>
</div>

<!-- Recent Reports -->
<div class="panel">
  <h2>📄 Recent Output Files</h2>
  <table>
    <thead><tr><th>File</th><th>Generated</th><th>Size</th></tr></thead>
    <tbody>{output_rows}</tbody>
  </table>
</div>

</div><!-- /container -->
<script>
  document.title = 'AlphaAbsolute Dashboard — ' + new Date().toLocaleDateString();
</script>
</body>
</html>"""

# ── Entry Point ────────────────────────────────────────────────────────────────

def main():
    print("AlphaAbsolute Dashboard Generator")
    print(f"Reading data from: {DATA_DIR}")

    portfolio = load_portfolio()
    trades = load_trades()
    perf = compute_performance(portfolio, trades)
    flags = compute_risk_flags(portfolio)
    outputs = load_recent_outputs()

    print(f"  Holdings: {len(portfolio['holdings'])}")
    print(f"  Closed trades: {perf['trade_count']}")
    print(f"  Risk flags: {len(flags)}")
    print(f"  Output files indexed: {len(outputs)}")

    html = generate_html(portfolio, trades, perf, flags, outputs)
    DASHBOARD_FILE.write_text(html, encoding="utf-8")

    print(f"\nDashboard written to: {DASHBOARD_FILE}")
    print("Opening in browser...")
    webbrowser.open(DASHBOARD_FILE.as_uri())


if __name__ == "__main__":
    main()
