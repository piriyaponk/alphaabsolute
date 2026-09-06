"""
AlphaAbsolute — Obsidian Vault Initializer
Creates full vault structure + 14 theme template pages.
Run once (idempotent — safe to re-run, never overwrites existing notes).

Cost: $0 — pure file I/O
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.brain.obsidian_writer_v2 import VAULT, ensure_vault, write, read

THEMES_14 = [
    ("AI-Related",         "NVDA, MSFT, PLTR, SOUN, CRWV",        "AI compute, software, data"),
    ("Memory/HBM",         "MU, WDC, AMAT, MRAM",                 "HBM4, DRAM pricing cycle, CoWoS"),
    ("Space",              "RKLB, LUNR, ASTS, RDW",               "Launch, satellite comms, lunar economy"),
    ("Quantum Computing",  "IONQ, RGTI, QUBT",                    "Error correction, quantum advantage timeline"),
    ("Photonics",          "LITE, COHR, AAOI, IPGP",              "Co-packaged optics, 1.6T transceivers, AI datacenters"),
    ("DefenseTech",        "PLTR, CACI, AXON, AVAV, LDOS",        "Autonomous weapons, AI-enabled ISR, drone defense"),
    ("Data Center",        "EQIX, DLR, VRT, ETN",                 "Power, cooling, sovereign AI buildout"),
    ("Nuclear/SMR",        "NNE, OKLO, CEG, CCJ",                  "Small modular reactors, datacenter power agreements"),
    ("NeoCloud",           "CRWV, SMCI, CORZ, NTAP",              "AI-first cloud, GPU clusters, alt hyperscalers"),
    ("AI Infrastructure",  "VRT, DELL, ANET, APH",                "Power, networking, servers for AI buildout"),
    ("DC Infra",           "PWR, EME, GLDD",                      "Datacenter construction, power delivery"),
    ("Drone/UAV",          "ACHR, JOBY, RCAT, AVAV",              "Urban air mobility, counter-drone, autonomous delivery"),
    ("Robotics",           "ISRG, TER, TSLA",                     "Industrial automation, surgical robots, humanoid"),
    ("Connectivity",       "TMUS, ASTS, ERIC",                    "AST SpaceMobile, LEO connectivity, 5G+"),
]

THEME_TEMPLATE = """\
---
theme: {theme}
tickers: {tickers}
status: ACTIVE
last_updated: 2026-09-05
---

# {theme}

**Tickers:** {tickers}
**Narrative:** {narrative}

---

## Bottleneck Owner

_Who controls the critical constraint in this theme's supply chain?_

| Company | Why They Own Bottleneck | Pricing Power |
|---------|------------------------|--------------|
| TBD     | —                      | H/M/L        |

---

## Demand Drivers

-
-

## Bear Case

-
-

---

## RS & Regime Status

| Date | Theme RS Pctl | Regime | HOT/WARM/WEAK | Note |
|------|---------------|--------|---------------|------|
| 2026-09-05 | TBD | Distribution | — | Initialized |

---

## Weekly Signals

_Signals appended automatically by research_scraper.py and session_start.py_

---

## Key Research

_Notes from SemiAnalysis, Druckenmiller interviews, earnings calls_

"""

VAULT_STRUCTURE_DOC = """\
---
title: AlphaAbsolute Vault Structure
created: 2026-09-05
---

# AlphaAbsolute Obsidian Vault

## Folder Structure

```
Themes/             — 14 official investment themes + new discoveries
Signals/            — Backtested signal findings from Team Q (Quant)
Knowledge/
  Daily/            — Session-start daily research digest (auto-written)
  Druckenmiller/    — Stan Druckenmiller interview notes + lessons
  Minervini/        — Mark Minervini framework notes + trade examples
  Papers/           — Academic research (AQR, Lowry, Bulkowski)
  Interviews/       — Other macro strategist interviews
Bottlenecks/        — Supply chain bottleneck deep dives per theme
Performance/
  Monthly/          — Monthly performance attribution vs QQQ
Tickers/            — Individual stock research notes (MOAT/TAM/Bottleneck)
Trades/             — Paper trade entries with thesis + exit log
System/             — System documentation
```

## Auto-Written Notes

- `Knowledge/Daily/YYYY-MM-DD.md` — written every Claude Code session open
- `Themes/*.md` — signals appended when pipeline finds relevant news
- `Performance/Monthly/*.md` — written monthly by performance_monitor.py

## How to Search

Use Obsidian's built-in search or Dataview:
```dataview
TABLE moat_score, tam_score, bottleneck_score FROM "Tickers"
SORT moat_score DESC
```

## Data Flow

```
Python pipeline → obsidian_writer_v2.py → .md files → OneDrive sync → Obsidian
```
No REST API. No plugin required. Just file I/O.
"""


def init_vault():
    print(f"[VaultInit] Initializing vault at: {VAULT}")
    ensure_vault()

    # Index doc
    write("System/VAULT_STRUCTURE.md", VAULT_STRUCTURE_DOC)
    print("  ✓ System/VAULT_STRUCTURE.md")

    # 14 Theme pages (skip if already exists)
    for theme, tickers, narrative in THEMES_14:
        fname = theme.replace("/", "-").replace(" ", "_") + ".md"
        path = f"Themes/{fname}"
        if read(path) is not None:
            print(f"  - Themes/{fname} already exists — skipped")
            continue
        content = THEME_TEMPLATE.format(
            theme=theme, tickers=tickers, narrative=narrative
        )
        write(path, content)
        print(f"  ✓ Themes/{fname}")

    # Performance index
    perf_index = """\
---
title: Performance Index
---
# Performance vs QQQ

| Period | NAV | QQQ | Alpha | Win Rate | Avg R:R |
|--------|-----|-----|-------|----------|---------|
| 2026-09-05 | $100,000 | — | +0.0% | — | — |

_Updated monthly by performance_monitor.py_
"""
    if read("Performance/index.md") is None:
        write("Performance/index.md", perf_index)
        print("  ✓ Performance/index.md")

    print(f"\n[VaultInit] Done — vault ready at: {VAULT}")
    print("  Open Obsidian and point it to this folder to sync.")


if __name__ == "__main__":
    init_vault()
