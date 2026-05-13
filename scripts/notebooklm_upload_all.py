"""
AlphaAbsolute — Bulk NotebookLM Upload Script
Uploads all framework files to the correct notebooks.
Run once to populate the knowledge base.
"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime

# ── SSL patch (needed on this machine due to proxy cert) ──────────────────────
import httpx
_orig_init = httpx.AsyncClient.__init__
def _patched_init(self, *a, **kw):
    kw['verify'] = False
    _orig_init(self, *a, **kw)
httpx.AsyncClient.__init__ = _patched_init

from notebooklm import NotebookLMClient

# ── Notebook IDs (from live query) ────────────────────────────────────────────
NOTEBOOKS = {
    "PULSE Framework":          "569b5919-38bc-4d42-bc43-547870153ace",
    "NRGC Framework":           "6770bece-e5e1-408e-862b-27af7622a7ce",
    "Thai Market Intelligence": "c31701b2-6670-48ac-8a19-5f09209b2a7a",
    "Investment Lessons":       "cfbfa617-b8e8-4ca6-81b1-d2437c56f33c",
    "Global Macro Database":    "bba5d196-0f70-4000-be28-7232f7656787",
    "Megatrend Themes":         "69bcfcea-0ecc-4851-bf65-3ab680e43ae3",
}

BASE_AA   = Path(r"C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute")
BASE_MEM  = Path(r"C:\Users\Pizza\.claude\projects\C--Users-Pizza-OneDrive-Desktop-AlphaAbsolute\memory")
INDEX     = BASE_AA / "memory" / "notebooklm_index.md"
TODAY     = datetime.now().strftime("%y%m%d")

# ── Upload manifest ───────────────────────────────────────────────────────────
# Format: (notebook_key, file_path, label)
UPLOADS = [
    # ── PULSE Framework ──────────────────────────────────────────────────────
    ("PULSE Framework",
     BASE_AA / "output" / "EMLS_Framework_260513.md",
     f"{TODAY} | System | Framework | EMLS Master Framework v1.0"),

    ("PULSE Framework",
     BASE_MEM / "minervini_framework.md",
     f"{TODAY} | System | Memory | Minervini SEPA Framework — Complete Rules"),

    ("PULSE Framework",
     BASE_MEM / "ibd_trading_system.md",
     f"{TODAY} | System | Memory | IBD CAN SLIM Trading System — Full Rules"),

    ("PULSE Framework",
     BASE_MEM / "zyo71_framework.md",
     f"{TODAY} | System | Memory | Zyo71 Trading Framework — EMA Rules + 20 Themes"),

    ("PULSE Framework",
     BASE_MEM / "varich_analysis.md",
     f"{TODAY} | System | Memory | Varich Analysis — Memory 2027 Thesis + DRAM Cycle"),

    # ── NRGC Framework ───────────────────────────────────────────────────────
    ("NRGC Framework",
     BASE_MEM / "nrgc_pulse_framework.md",
     f"{TODAY} | System | Framework | NRGC + PULSE Core Investment Philosophy v2.0"),

    # ── Megatrend Themes ─────────────────────────────────────────────────────
    ("Megatrend Themes",
     BASE_MEM / "ai_optics_interconnect.md",
     f"{TODAY} | System | Memory | AI Optics & Interconnect Thesis — 4-Layer Ecosystem"),

    ("Megatrend Themes",
     BASE_MEM / "semiconductor_tier_list.md",
     f"{TODAY} | System | Memory | Semiconductor Tier List — Risk/Reward S-E Tiers"),

    ("Megatrend Themes",
     BASE_MEM / "space_economy_warp.md",
     f"{TODAY} | System | Memory | Space Economy & WARP ETF — 4-Layer Ecosystem"),

    # ── Investment Lessons ───────────────────────────────────────────────────
    ("Investment Lessons",
     BASE_MEM / "research_sources.md",
     f"{TODAY} | System | Memory | Research Source Stack — Tier 1-3 Sources + Style Targets"),
]


def update_index(notebook: str, label: str, summary: str) -> None:
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    entry = f"| {date} | {notebook} | {label} | {summary[:80]} |\n"
    with INDEX.open("a", encoding="utf-8") as f:
        f.write(entry)


async def main():
    print(f"\n{'='*60}")
    print("  AlphaAbsolute - NotebookLM Bulk Upload")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    async with await NotebookLMClient.from_storage() as client:
        success = 0
        failed  = 0

        for nb_key, file_path, label in UPLOADS:
            nb_id = NOTEBOOKS[nb_key]
            print(f"[UPLOAD] [{nb_key}]")
            print(f"   File  : {file_path.name}")
            print(f"   Label : {label}")

            if not file_path.exists():
                print(f"   [SKIP] FILE NOT FOUND\n")
                failed += 1
                continue

            content = file_path.read_text(encoding="utf-8", errors="replace")
            word_count = len(content.split())
            print(f"   Size  : {len(content):,} chars / {word_count:,} words")

            try:
                await client.sources.add_text(nb_id, title=label, content=content)
                update_index(nb_key, label, content[:100])
                print(f"   [OK] Uploaded successfully\n")
                success += 1
            except Exception as e:
                print(f"   [ERR] Error: {e}\n")
                failed += 1

        print(f"{'='*60}")
        print(f"  DONE: {success} uploaded, {failed} failed/skipped")
        print(f"  Index updated: memory/notebooklm_index.md")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
