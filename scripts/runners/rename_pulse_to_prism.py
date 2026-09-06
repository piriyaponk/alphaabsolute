"""
Rename PULSE → PRISM across all user-facing files.
PRISM = Price · RS · Inflection · Structure · Megatrend
Scope: user-visible strings, CLAUDE.md, session_start.py
Internal Python variable names (PULSE_RS_*) and file paths kept as-is to avoid pipeline breakage.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_in_file(path: Path, replacements: list[tuple[str, str]], label: str = None):
    if not path.exists():
        print(f"  SKIP (not found): {path}")
        return 0
    txt = path.read_text(encoding="utf-8")
    orig = txt
    for old, new in replacements:
        txt = txt.replace(old, new)
    if txt != orig:
        path.write_text(txt, encoding="utf-8")
        count = sum(txt.count(new) for _, new in replacements if new)
        print(f"  OK: {label or path.name} — updated")
        return 1
    print(f"  -- {label or path.name} — no changes")
    return 0


# ─── 1. report_writer.py — user-facing Telegram + brief strings ──────────────
rw = ROOT / "scripts" / "output" / "report_writer.py"
replace_in_file(rw, [
    # docstring header
    ("── PULSE (Minervini SEPA)", "── PRISM (Minervini SEPA)"),
    ("── PULSE (", "── PRISM ("),
    ("PULSE (Minervini SEPA) — Confirmed leaders", "PRISM (Price·RS·Inflection·Structure·Megatrend) — Confirmed leaders"),
    ("⭐ TICKER — Full Setup Name | PULSE |", "⭐ TICKER — Full Setup Name | PRISM |"),
    # mode_label assignment
    ('mode_label = "PULSE" if mode == "A"', 'mode_label = "PRISM" if mode == "A"'),
    # Telegram card strings
    ("| PULSE | Grade", "| PRISM | Grade"),
    ("| PULSE |", "| PRISM |"),
    # entry_rule strings
    ("Full size | PULSE + Monster Scout", "Full size | PRISM + Monster Scout"),
    ("PULSE only | Selective", "PRISM only | Selective"),
    ("PULSE only | No Monster Scout entries", "PRISM only | No Monster Scout entries"),
    # section headers
    ("No PULSE Grade A setups", "No PRISM Grade A setups"),
    ("PULSE Grade A", "PRISM Grade A"),
    ("PULSE Grade B", "PRISM Grade B"),
    # any remaining
    ('"PULSE"', '"PRISM"'),
], "report_writer.py")


# ─── 2. session_start.py — daily startup display strings only ────────────────
ss = ROOT / "scripts" / "hooks" / "session_start.py"
replace_in_file(ss, [
    # Display labels (user-visible in terminal)
    ("[Mode A Leaders]", "[PRISM Leaders]"),
    ("Mode A Leaders]", "PRISM Leaders]"),
    ("[PULSE+NRGC", "[PRISM+NRGC"),
    ("PULSE+NRGC", "PRISM+NRGC"),
    # Comment labels
    ("# ── Mode A Full Screen", "# ── PRISM Full Screen"),
    ("# ── Super High Conviction (PULSE Grade A", "# ── Super High Conviction (PRISM Grade A"),
    # String literals
    ('"Mode A Leaders"', '"PRISM Leaders"'),
    ("'PULSE Grade A'", "'PRISM Grade A'"),
    # PULSE Backtest section
    ("# ── PULSE Backtest Summary", "# ── PRISM Backtest Summary"),
    # Any display strings
    ("PULSE Grade A", "PRISM Grade A"),
    ("PULSE Grade B", "PRISM Grade B"),
    # Conviction display
    ("W1 SUPER HIGH CONVICTION] PULSE+NRGC", "W1 SUPER HIGH CONVICTION] PRISM+NRGC"),
    ("SUPER HIGH CONVICTION] PULSE+NRGC", "SUPER HIGH CONVICTION] PRISM+NRGC"),
], "session_start.py")


# ─── 3. CLAUDE.md — documentation rename Mode A → PRISM, Mode B → Monster Scout
claude_md = ROOT / "CLAUDE.md"
replace_in_file(claude_md, [
    # Section headings
    ("### Mode A — Momentum Leadership", "### PRISM — Confirmed Leaders (Price · RS · Inflection · Structure · Megatrend)"),
    ("### Mode B — Monster Stock / Big Shot", "### Monster Scout — 10x Before The Supercycle"),
    # Mode A criteria references
    ("**Entry criteria (ALL must pass — no exceptions):**\n- RS percentile", "**PRISM Entry criteria (ALL must pass — no exceptions):**\n- RS percentile"),
    # Inline Mode A / Mode B labels throughout
    ("Mode A only", "PRISM only"),
    ("Mode A or Mode B", "PRISM or Monster Scout"),
    ("Mode A gates", "PRISM gates"),
    ("Mode A screen", "PRISM screen"),
    ("Mode A only.", "PRISM only."),
    ("Mode A: maintain", "PRISM: maintain"),
    ("all 5 Mode A gates", "all 5 PRISM gates"),
    ("full Mode A screen", "full PRISM screen"),
    ("Mode A (Minervini)", "PRISM (Minervini)"),
    ("Mode A — Momentum Leadership", "PRISM — Confirmed Leaders"),
    ("Mode B — Monster Stock / Big Shot", "Monster Scout — 10x Before The Supercycle"),
    ("Mode B screen", "Monster Scout screen"),
    ("Mode B candidates", "Monster Scout candidates"),
    ("Mode B (Big Shot)", "Monster Scout (Big Shot)"),
    ("Mode B positions", "Monster Scout positions"),
    ("Mode B bucket", "Monster Scout bucket"),
    ("Mode B entry", "Monster Scout entry"),
    ("Mode B entries", "Monster Scout entries"),
    ("new Mode B", "new Monster Scout"),
    ("Total Mode B", "Total Monster Scout"),
    ("Max Mode B", "Max Monster Scout"),
    ("Mode B: maintain", "Monster Scout: maintain"),
    ("mode=B", "mode=Monster Scout"),
    # Table row labels
    ("| A06 | Leadership Curator | 2 — Curation | Mode A screen", "| A06 | Leadership Curator | 2 — Curation | PRISM screen"),
    ("| A07 | Monster Scout | 2 — Curation | Mode B screen", "| A07 | Monster Scout | 2 — Curation | Monster Scout screen"),
    # Grade A requirements
    ("Grade A: All 5 Mode A gates", "Grade A: All 5 PRISM gates"),
    # Table regime rules
    ("Reduce size, Mode A only", "Reduce size, PRISM only"),
    # Command reference
    ("A06 → run full Mode A screen, output top 30", "A06 → run full PRISM screen, output top 30"),
    ("A07 → run Mode B screen, output candidates", "A07 → run Monster Scout screen, output candidates"),
    # Example Telegram card
    ("Mode A | VCP | Pivot", "PRISM | VCP | Pivot"),
    # Backtest findings
    ("Full Mode A 5-gate screen", "Full PRISM 5-gate screen"),
    # Additional inline references (lowercase)
    ("mode A", "PRISM"),
    ("mode B", "Monster Scout"),
    # PULSE reference in HOT theme bonus section
    ("HOT theme bonus in A06", "HOT theme bonus in A06"),  # no change needed
], "CLAUDE.md")


# ─── 4. risk_guardian.py — user-facing output strings only (not variable names)
rg = ROOT / "scripts" / "pre_compute" / "risk_guardian.py"
replace_in_file(rg, [
    # Output message strings only
    ("Grade A PULSE + Phase 3", "Grade A PRISM + Phase 3"),
    ("Mode B bucket", "Monster Scout bucket"),
    ("Mode B (Big Shot)", "Monster Scout (Big Shot)"),
    # Constant comment (cosmetic only)
    ("# 30% max total Mode B bucket", "# 30% max total Monster Scout bucket"),
    # Mode B bucket check label
    ("# Mode B bucket check", "# Monster Scout bucket check"),
    ("f\"Mode B bucket = ", "f\"Monster Scout bucket = "),
    ("f\"Reduce Mode B (Big Shot)", "f\"Reduce Monster Scout positions"),
], "risk_guardian.py")


# ─── 5. auto_postmortem.py — output strings ──────────────────────────────────
ap = ROOT / "scripts" / "pre_compute" / "auto_postmortem.py"
replace_in_file(ap, [
    ("PULSE rules at entry", "PRISM rules at entry"),
], "auto_postmortem.py")


# ─── 6. skills/agent files — user-visible labels ────────────────────────────
for skill_path in (ROOT / "skills").glob("*.md"):
    replace_in_file(skill_path, [
        ("Mode A", "PRISM"),
        ("Mode B", "Monster Scout"),
        ("PULSE", "PRISM"),
    ], f"skills/{skill_path.name}")


print("\nDone. Run syntax check...")
import subprocess
result = subprocess.run(
    ["python", "-c",
     "import py_compile; py_compile.compile('scripts/output/report_writer.py', doraise=True); "
     "py_compile.compile('scripts/hooks/session_start.py', doraise=True); "
     "py_compile.compile('scripts/pre_compute/risk_guardian.py', doraise=True); "
     "print('Syntax OK: all 3 files compile cleanly')"],
    capture_output=True, text=True, cwd=str(ROOT)
)
print(result.stdout or result.stderr)
