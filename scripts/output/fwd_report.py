"""
AlphaAbsolute v2 — Forward Test Attribution Report
====================================================
Reads filled forward-return data and answers the core question:

  "Which gates actually predict 4W / 8W / 13W excess returns vs QQQ?"

Output:
  1. Per-gate hit rate and average excess return
  2. Gate combinations (5-gate vs 4-gate vs 3-gate vs ≤2-gate)
  3. Regime breakdown (Distribution vs Markup returns)
  4. Setup type attribution (VCP vs BKT vs CWH vs SOS vs SPR)
  5. Written to output/fwd_report_YYMMDD.md

Run: python scripts/output/fwd_report.py
Schedule: Fridays (weekly), or whenever new fills complete.
"""

from __future__ import annotations
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
DB   = ROOT / "data" / "ohlcv.db"
OUT  = ROOT / "output"
OUT.mkdir(parents=True, exist_ok=True)

WINDOWS = ["4w", "8w", "13w"]
MIN_SAMPLE = 10  # minimum rows before showing a stat


def _mean(vals: list[float]) -> Optional[float]:
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v) * 100, 2) if v else None


def _hit_rate(excess: list[float]) -> Optional[float]:
    v = [x for x in excess if x is not None]
    return round(sum(1 for x in v if x > 0) / len(v) * 100, 1) if v else None


def _pct(val: Optional[float]) -> str:
    return f"{val:+.2f}%" if val is not None else "n/a"


def _hr(val: Optional[float]) -> str:
    return f"{val:.1f}%" if val is not None else "n/a"


def run() -> str:
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  Forward Test Report  [{today}]")
    print(f"{'='*55}")

    conn = sqlite3.connect(DB)
    cur  = conn.cursor()

    lines: list[str] = []
    lines.append(f"# AlphaAbsolute Forward Test Report")
    lines.append(f"**Date:** {today}  |  **Generated:** {datetime.now().strftime('%H:%M')}\n")

    # ── 0. Cohort summary ─────────────────────────────────────────────────────
    cur.execute("""
        SELECT cohort_date, regime, universe_n, n_5gate, n_4gate,
               fill_4w_date, fill_8w_date, fill_13w_date
        FROM fwd_test_cohorts ORDER BY cohort_date
    """)
    cohorts = cur.fetchall()

    lines.append("## Cohort Summary")
    lines.append("| Date | Regime | Universe | 5-gate | 4-gate | 4W | 8W | 13W |")
    lines.append("|------|--------|----------|--------|--------|----|----|-----|")
    for c in cohorts:
        filled = [
            "✓" if c[5] else "⏳",
            "✓" if c[6] else "⏳",
            "✓" if c[7] else "⏳",
        ]
        lines.append(f"| {c[0]} | {c[1]} | {c[2]} | {c[3]} | {c[4]} | {filled[0]} | {filled[1]} | {filled[2]} |")
    lines.append("")

    # ── 1. Check data availability ────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM screening_history WHERE fwd_ret_4w IS NOT NULL")
    n_4w = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM screening_history WHERE fwd_ret_13w IS NOT NULL")
    n_13w = cur.fetchone()[0]

    print(f"  Rows with 4W fill: {n_4w}")
    print(f"  Rows with 13W fill: {n_13w}")

    if n_4w < MIN_SAMPLE:
        lines.append(f"## Status\n")
        lines.append(f"**Insufficient data for attribution.** {n_4w} rows with 4W returns filled.")
        lines.append(f"First cohort needs 28 days before 4W returns are available.\n")
        lines.append("_Check back in 4 weeks — this file will auto-populate._")
        _write(lines, today)
        conn.close()
        return ""

    # ── 2. Gate-pass group attribution ────────────────────────────────────────
    lines.append("## Gate Attribution (Excess Return vs QQQ)")
    lines.append("")
    lines.append("*Excess return = fwd_ret_Nw − qqq_ret_Nw. Hit rate = % above QQQ.*")
    lines.append("")

    for win in WINDOWS:
        ret_col = f"fwd_ret_{win}"
        qqq_col = f"qqq_ret_{win}"
        cur.execute(f"""
            SELECT gates_passed, {ret_col}, {qqq_col}
            FROM screening_history
            WHERE {ret_col} IS NOT NULL AND {qqq_col} IS NOT NULL
            ORDER BY gates_passed DESC
        """)
        rows = cur.fetchall()
        if not rows:
            continue

        lines.append(f"### {win.upper()} Window")
        lines.append("| Gates | N | Avg Excess | Hit Rate |")
        lines.append("|-------|---|------------|----------|")

        by_gate: dict[int, list[float]] = {}
        for r in rows:
            g = r[0] or 0
            excess = (r[1] or 0) - (r[2] or 0)
            by_gate.setdefault(g, []).append(excess)

        for g in sorted(by_gate.keys(), reverse=True):
            vals = by_gate[g]
            if len(vals) < 3:
                continue
            avg_ex  = _mean(vals)
            hit     = _hit_rate(vals)
            n       = len(vals)
            label   = f"{g}-gate" if g < 5 else "5-gate (A-list)"
            lines.append(f"| {label} | {n} | {_pct(avg_ex)} | {_hr(hit)} |")

        lines.append("")

    # ── 3. Regime breakdown ───────────────────────────────────────────────────
    lines.append("## Regime Breakdown (4W Excess Return)")
    lines.append("| Regime | N | Avg Excess | Hit Rate |")
    lines.append("|--------|---|------------|----------|")

    cur.execute("""
        SELECT regime, fwd_ret_4w, qqq_ret_4w
        FROM screening_history
        WHERE fwd_ret_4w IS NOT NULL AND qqq_ret_4w IS NOT NULL
          AND regime IS NOT NULL
    """)
    by_regime: dict[str, list[float]] = {}
    for r in cur.fetchall():
        reg = r[0]
        excess = (r[1] or 0) - (r[2] or 0)
        by_regime.setdefault(reg, []).append(excess)

    for reg, vals in sorted(by_regime.items()):
        if len(vals) < 3:
            continue
        lines.append(f"| {reg} | {len(vals)} | {_pct(_mean(vals))} | {_hr(_hit_rate(vals))} |")
    lines.append("")

    # ── 4. Setup type attribution ─────────────────────────────────────────────
    lines.append("## Setup Type Attribution (4W Excess Return)")
    lines.append("| Setup | N | Avg Excess | Hit Rate |")
    lines.append("|-------|---|------------|----------|")

    cur.execute("""
        SELECT setup_type, fwd_ret_4w, qqq_ret_4w
        FROM screening_history
        WHERE fwd_ret_4w IS NOT NULL AND qqq_ret_4w IS NOT NULL
          AND setup_type IS NOT NULL
    """)
    by_setup: dict[str, list[float]] = {}
    for r in cur.fetchall():
        st = r[0]
        excess = (r[1] or 0) - (r[2] or 0)
        by_setup.setdefault(st, []).append(excess)

    for st, vals in sorted(by_setup.items(), key=lambda x: -len(x[1])):
        if len(vals) < 3:
            continue
        lines.append(f"| {st} | {len(vals)} | {_pct(_mean(vals))} | {_hr(_hit_rate(vals))} |")
    lines.append("")

    # ── 5. Individual gate lift analysis ──────────────────────────────────────
    lines.append("## Individual Gate Lift vs No-Gate Baseline (4W)")
    lines.append("")
    lines.append("*Each gate's incremental lift: mean return with gate=True vs gate=False.*")
    lines.append("")
    lines.append("| Gate | N (True) | Mean True | N (False) | Mean False | Lift |")
    lines.append("|------|----------|-----------|-----------|------------|------|")

    gate_cols = ["gate_rs", "gate_stage2", "gate_52w", "gate_adtv", "gate_eps", "gate_rev", "gate_gm"]
    for gc in gate_cols:
        cur.execute(f"""
            SELECT {gc}, fwd_ret_4w - qqq_ret_4w AS excess
            FROM screening_history
            WHERE fwd_ret_4w IS NOT NULL AND qqq_ret_4w IS NOT NULL
              AND {gc} IS NOT NULL
        """)
        gate_rows = cur.fetchall()
        true_vals  = [r[1] for r in gate_rows if r[0] == 1 and r[1] is not None]
        false_vals = [r[1] for r in gate_rows if r[0] == 0 and r[1] is not None]
        if len(true_vals) < 3 or len(false_vals) < 3:
            continue
        mt   = _mean(true_vals)
        mf   = _mean(false_vals)
        lift = round(mt - mf, 2) if mt is not None and mf is not None else None
        lines.append(f"| {gc} | {len(true_vals)} | {_pct(mt)} | {len(false_vals)} | {_pct(mf)} | {_pct(lift)} |")

    lines.append("")

    # ── 6. Top / bottom performers in most recent cohort ──────────────────────
    cur.execute("SELECT MAX(run_date) FROM screening_history WHERE fwd_ret_4w IS NOT NULL")
    latest = cur.fetchone()[0]
    if latest:
        cur.execute("""
            SELECT ticker, gates_passed, fwd_ret_4w, qqq_ret_4w, setup_type
            FROM screening_history
            WHERE run_date=? AND fwd_ret_4w IS NOT NULL AND qqq_ret_4w IS NOT NULL
            ORDER BY (fwd_ret_4w - qqq_ret_4w) DESC
            LIMIT 10
        """, (latest,))
        top10 = cur.fetchall()

        cur.execute("""
            SELECT ticker, gates_passed, fwd_ret_4w, qqq_ret_4w, setup_type
            FROM screening_history
            WHERE run_date=? AND fwd_ret_4w IS NOT NULL AND qqq_ret_4w IS NOT NULL
            ORDER BY (fwd_ret_4w - qqq_ret_4w) ASC
            LIMIT 5
        """, (latest,))
        bot5 = cur.fetchall()

        lines.append(f"## Top 10 Performers — Cohort {latest} (4W excess vs QQQ)")
        lines.append("| # | Ticker | Gates | Setup | Fwd Ret | QQQ Ret | Excess |")
        lines.append("|---|--------|-------|-------|---------|---------|--------|")
        for i, r in enumerate(top10, 1):
            ex = round((r[2] or 0) - (r[3] or 0), 4)
            lines.append(
                f"| {i} | {r[0]} | {r[1]} | {r[4] or '—'} | "
                f"{_pct(round(r[2]*100,2))} | {_pct(round(r[3]*100,2))} | {_pct(round(ex*100,2))} |"
            )
        lines.append("")

        lines.append(f"## Bottom 5 (Avoid List Review) — Cohort {latest}")
        lines.append("| Ticker | Gates | Fwd Ret | QQQ Ret | Excess |")
        lines.append("|--------|-------|---------|---------|--------|")
        for r in bot5:
            ex = round((r[2] or 0) - (r[3] or 0), 4)
            lines.append(
                f"| {r[0]} | {r[1]} | "
                f"{_pct(round(r[2]*100,2))} | {_pct(round(r[3]*100,2))} | {_pct(round(ex*100,2))} |"
            )
        lines.append("")

    # ── 7. Action items ───────────────────────────────────────────────────────
    lines.append("## Learning Loop Action Items")
    lines.append("")
    lines.append("Review these each Friday after the report runs:")
    lines.append("")
    lines.append("1. **Gate Lift**: If any gate shows negative lift (True group underperforms False), flag for da-quant review")
    lines.append("2. **Regime**: If Distribution regime shows consistent negative excess → enforce size reduction to 50%")
    lines.append("3. **Setup Type**: If SPR/SOS show <30% hit rate after N≥20 → lower their grade")
    lines.append("4. **Minimum Sample**: Need N≥50 per gate before any rule change (per CLAUDE.md)")
    lines.append("")
    lines.append(f"_Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_")

    conn.close()
    return _write(lines, today)


def _write(lines: list[str], today: str) -> str:
    fname = OUT / f"fwd_report_{today.replace('-', '')}.md"
    fname.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  -> Written: {fname}")
    return str(fname)


if __name__ == "__main__":
    run()
