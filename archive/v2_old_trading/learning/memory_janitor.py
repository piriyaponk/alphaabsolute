"""
AlphaAbsolute — Memory Janitor

Runs monthly. Keeps the knowledge base lean, fast, and forever useful.
Never loses important information — compresses history, prunes low-quality noise.

Rules:
  Indicator KB  → cap 2,000 items (quality-ranked). Protected: accuracy > 60%
  Agent memory  → keep last 52 entries detail + compress older to Annual Summary
  Smart signals → keep last 4 weekly files (FRED cache refreshes each run)
  Agent calls   → keep last 12 weeks (enough for 3-month trend analysis)
  Research thesis DB → already capped at 300 (no action needed)

This ensures:
  - indicator_kb.json never exceeds ~3MB (2,000 items × ~1.5KB)
  - agent_XX_learnings.md never exceeds ~80KB per file (52 weeks detail)
  - data/ folder stays under 10MB forever
  - Claude can load memory files in < 1 second every session
"""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

BASE_DIR   = Path(__file__).resolve().parents[2]
MEM_DIR    = BASE_DIR / "memory"
DATA_DIR   = BASE_DIR / "data" / "agent_memory"
SS_DIR     = BASE_DIR / "data" / "smart_signals"
KB_FILE    = DATA_DIR / "indicator_kb.json"


def log(msg: str):
    print(f"  [Janitor] {msg}")


# ══════════════════════════════════════════════════════════════════════════════
# 1. INDICATOR KB — quality-ranked pruning
#    Keep: top 2,000 by quality_score + any item with our_accuracy > 60%
#    Remove: lowest quality, unseen > 90 days, quality < 25
# ══════════════════════════════════════════════════════════════════════════════

KB_MAX_ITEMS    = 2_000
KB_MIN_QUALITY  = 25      # below this = always prune
KB_STALE_DAYS   = 90      # not seen in 90 days + low quality = prune
KB_ACCURACY_PROTECT = 60  # items with measured accuracy > 60% are protected


def prune_indicator_kb() -> dict:
    """
    Prune indicator KB to KB_MAX_ITEMS.
    Returns stats: {before, after, pruned, protected}
    """
    if not KB_FILE.exists():
        return {}

    kb = json.loads(KB_FILE.read_text(encoding="utf-8"))
    cutoff = (datetime.utcnow() - timedelta(days=KB_STALE_DAYS)).strftime("%Y-%m-%d")
    today  = datetime.utcnow().strftime("%Y-%m-%d")

    stats_before = kb.get("kb_stats", {}).get("total_items", 0)

    # Collect all items from indicators + strategies
    all_items = {}
    for section in ("indicators", "strategies"):
        for item_id, item in kb.get(section, {}).items():
            all_items[item_id] = (section, item)

    before = len(all_items)
    if before <= KB_MAX_ITEMS:
        log(f"Indicator KB: {before} items — under {KB_MAX_ITEMS} limit, no pruning needed")
        return {"before": before, "after": before, "pruned": 0, "protected": 0}

    # Classify items
    protected, pruneable = [], []
    for item_id, (section, item) in all_items.items():
        acc = item.get("our_accuracy") or 0
        score = item.get("quality_score", 0)
        last_seen = item.get("last_seen_date", item.get("added_date", "2020-01-01"))
        adopted = item.get("adoption_status") == "adopted"

        # Always keep: high accuracy, adopted into framework
        if acc >= KB_ACCURACY_PROTECT or adopted:
            protected.append((item_id, section, item, score))
        # Always remove: very low quality OR stale + mediocre
        elif score < KB_MIN_QUALITY:
            pass  # drop silently
        elif last_seen < cutoff and score < 50:
            pass  # stale + mediocre — drop
        else:
            pruneable.append((item_id, section, item, score))

    # Sort pruneable by quality desc, keep top (KB_MAX_ITEMS - len(protected))
    pruneable.sort(key=lambda x: x[3], reverse=True)
    slots = KB_MAX_ITEMS - len(protected)
    keep_pruneable = pruneable[:slots]
    removed = pruneable[slots:]

    # Rebuild KB
    new_indicators, new_strategies = {}, {}
    for item_id, section, item, _ in (protected + keep_pruneable):
        if section == "indicators":
            new_indicators[item_id] = item
        else:
            new_strategies[item_id] = item

    kb["indicators"] = new_indicators
    kb["strategies"] = new_strategies
    after = len(new_indicators) + len(new_strategies)

    # Update stats
    stats = kb.setdefault("kb_stats", {})
    stats["total_items"] = after
    stats["last_pruned"]  = today
    stats["pruned_total"] = stats.get("pruned_total", 0) + len(removed)

    KB_FILE.write_text(json.dumps(kb, indent=2, default=str), encoding="utf-8")

    result = {
        "before":    before,
        "after":     after,
        "pruned":    len(removed),
        "protected": len(protected),
    }
    log(f"Indicator KB: {before} -> {after} items ({len(removed)} pruned, {len(protected)} protected)")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 2. AGENT MEMORY FILES — rotate: keep last 52 + compress older to Annual Summary
#    agent_XX_learnings.md format:
#      # Header
#      ## Annual Summary YYYY (compressed)
#      ## 2026-05-14 — Weekly Learning  (recent, kept in full)
# ══════════════════════════════════════════════════════════════════════════════

KEEP_RECENT_WEEKS = 52   # 1 year of full detail
COMPRESS_LLM = False     # If True: use LLM to compress. If False: just count + truncate.


def rotate_agent_memory_files() -> dict:
    """
    For each agent_XX_learnings.md: keep last 52 weekly entries in full.
    Older entries → compressed into an Annual Summary block.
    Returns stats per file.
    """
    results = {}
    agent_files = sorted(MEM_DIR.glob("agent_*.md"))
    if not agent_files:
        log("No agent memory files found")
        return {}

    for mem_file in agent_files:
        result = _rotate_one_memory_file(mem_file)
        if result:
            results[mem_file.name] = result

    return results


def _rotate_one_memory_file(mem_file: Path) -> Optional[dict]:
    """Rotate a single agent memory file."""
    content = mem_file.read_text(encoding="utf-8", errors="replace")
    size_before = len(content)

    # Split into sections by "## YYYY-MM-DD — Weekly Learning" headers
    header_pattern = re.compile(r'\n---\n## (\d{4}-\d{2}-\d{2}) — Weekly Learning')
    sections = header_pattern.split(content)

    # sections[0] = file header (name, intro)
    # sections[1,3,5,...] = dates
    # sections[2,4,6,...] = content for each date
    if len(sections) <= 1:
        return None  # no weekly entries yet

    # Pair up: (date, content)
    entries = []
    file_header = sections[0]
    for i in range(1, len(sections) - 1, 2):
        date_str = sections[i]
        entry_content = sections[i + 1] if i + 1 < len(sections) else ""
        entries.append((date_str, entry_content))

    if len(entries) <= KEEP_RECENT_WEEKS:
        return None  # nothing to rotate yet

    # Split: recent (keep full) vs old (compress)
    old_entries = entries[:-KEEP_RECENT_WEEKS]
    recent_entries = entries[-KEEP_RECENT_WEEKS:]

    # Compress old entries by year
    by_year = {}
    for date_str, entry_content in old_entries:
        year = date_str[:4]
        by_year.setdefault(year, []).append((date_str, entry_content))

    # Build compressed annual summaries
    compressed_sections = []
    for year, year_entries in sorted(by_year.items()):
        # Extract key lesson lines from each entry
        lessons_found = []
        for _, ec in year_entries:
            for line in ec.splitlines():
                if line.strip().startswith(("LESSON:", "1. LESSON", "2. LESSON",
                                            "ACTION:", "- L:", "Key lesson")):
                    lesson = line.strip()[:120]
                    if lesson:
                        lessons_found.append(f"  - {lesson}")
        lesson_block = "\n".join(lessons_found[:20]) or "  (see archived entries)"
        compressed_sections.append(
            f"\n---\n## Annual Summary {year} "
            f"({len(year_entries)} weeks, compressed)\n"
            f"{lesson_block}\n"
        )

    # Rebuild recent entries in full
    recent_section = ""
    for date_str, entry_content in recent_entries:
        recent_section += f"\n---\n## {date_str} — Weekly Learning{entry_content}"

    # Write new content
    new_content = file_header + "".join(compressed_sections) + recent_section
    mem_file.write_text(new_content, encoding="utf-8")

    size_after = len(new_content)
    saved = size_before - size_after
    log(f"{mem_file.name}: {size_before//1024}KB -> {size_after//1024}KB "
        f"(saved {saved//1024}KB, {len(old_entries)} entries compressed)")
    return {"before_kb": size_before // 1024, "after_kb": size_after // 1024,
            "compressed": len(old_entries), "kept_full": len(recent_entries)}


# ══════════════════════════════════════════════════════════════════════════════
# 3. SMART SIGNALS — keep last 4 dated files
#    fred_regime.json, xbrl_cache.json, cik_map.json are permanent (updated in-place)
# ══════════════════════════════════════════════════════════════════════════════

KEEP_SMART_SIGNALS = 4   # last 4 weekly snapshots


def cleanup_smart_signals() -> dict:
    """Delete old dated smart_signals_YYYYMMDD.json files, keep last N."""
    if not SS_DIR.exists():
        return {}

    dated_files = sorted(SS_DIR.glob("smart_signals_*.json"), reverse=True)
    to_delete = dated_files[KEEP_SMART_SIGNALS:]
    deleted_bytes = 0
    for f in to_delete:
        deleted_bytes += f.stat().st_size
        f.unlink()

    if to_delete:
        log(f"Smart signals: deleted {len(to_delete)} old files ({deleted_bytes//1024}KB freed)")
    else:
        log(f"Smart signals: {len(dated_files)} files, none to delete yet")

    return {"kept": min(len(dated_files), KEEP_SMART_SIGNALS),
            "deleted": len(to_delete), "freed_kb": deleted_bytes // 1024}


# ══════════════════════════════════════════════════════════════════════════════
# 4. AGENT CALLS — keep last 12 weeks
# ══════════════════════════════════════════════════════════════════════════════

KEEP_CALLS_WEEKS = 12


def cleanup_agent_calls() -> dict:
    """Delete old calls_YYYY-WXX.json files, keep last N weeks."""
    if not DATA_DIR.exists():
        return {}

    call_files = sorted(DATA_DIR.glob("calls_*.json"), reverse=True)
    to_delete = call_files[KEEP_CALLS_WEEKS:]
    deleted_bytes = 0
    for f in to_delete:
        deleted_bytes += f.stat().st_size
        f.unlink()

    if to_delete:
        log(f"Agent calls: deleted {len(to_delete)} old weeks ({deleted_bytes//1024}KB freed)")
    else:
        log(f"Agent calls: {len(call_files)} weeks, none to delete yet (limit={KEEP_CALLS_WEEKS})")

    return {"kept": min(len(call_files), KEEP_CALLS_WEEKS),
            "deleted": len(to_delete), "freed_kb": deleted_bytes // 1024}


# ══════════════════════════════════════════════════════════════════════════════
# 5. STORAGE HEALTH REPORT
# ══════════════════════════════════════════════════════════════════════════════

def storage_health_report() -> dict:
    """Measure current storage usage and project growth."""
    def dir_size(d: Path) -> int:
        if not d.exists(): return 0
        return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())

    def file_size(p: Path) -> int:
        return p.stat().st_size if p.exists() else 0

    sizes = {
        "indicator_kb_kb":      file_size(KB_FILE) // 1024,
        "agent_memory_dir_kb":  dir_size(DATA_DIR) // 1024,
        "memory_md_dir_kb":     dir_size(MEM_DIR) // 1024,
        "smart_signals_dir_kb": dir_size(SS_DIR) // 1024,
        "paper_trading_dir_kb": dir_size(BASE_DIR / "data" / "paper_trading") // 1024,
        "state_dir_kb":         dir_size(BASE_DIR / "data" / "state") // 1024,
    }
    total_kb = sum(sizes.values())
    sizes["total_data_kb"] = total_kb
    sizes["github_limit_mb"] = 1000
    sizes["pct_used"] = round(total_kb / 1_000_000 * 100, 4)
    sizes["years_until_limit"] = round(1_000_000 / max(total_kb / 1, 1), 0)

    log(f"Storage health: {total_kb}KB total | {sizes['pct_used']:.3f}% of GitHub 1GB limit")
    return sizes


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RUNNER — called from monthly_runner.py
# ══════════════════════════════════════════════════════════════════════════════

def run_memory_janitor() -> dict:
    """
    Monthly maintenance. Prune, rotate, compress, clean.
    Keeps the system lean and fast forever.
    """
    print("  [MemoryJanitor] Monthly maintenance cycle...")
    results = {}

    # 1. Prune indicator KB
    results["indicator_kb"] = prune_indicator_kb()

    # 2. Rotate agent memory files
    results["agent_memory"] = rotate_agent_memory_files()

    # 3. Clean old smart signals
    results["smart_signals"] = cleanup_smart_signals()

    # 4. Clean old agent calls
    results["agent_calls"] = cleanup_agent_calls()

    # 5. Health report
    results["storage"] = storage_health_report()

    # Summary
    total_freed = (
        sum(r.get("freed_kb", 0) for r in [
            results["smart_signals"], results["agent_calls"]
        ])
    )
    print(f"  [MemoryJanitor] Done. {total_freed}KB freed | "
          f"Total: {results['storage']['total_data_kb']}KB")
    return results


def get_janitor_telegram_line(result: dict) -> str:
    """One-liner for Telegram monthly report."""
    if not result:
        return ""
    storage = result.get("storage", {})
    kb_pruned = result.get("indicator_kb", {}).get("pruned", 0)
    freed = sum(r.get("freed_kb", 0) for r in [
        result.get("smart_signals", {}), result.get("agent_calls", {})
    ])
    total_kb = storage.get("total_data_kb", 0)
    pct = storage.get("pct_used", 0)
    return (f"Memory health: {total_kb}KB total ({pct:.3f}% of GitHub limit) | "
            f"{kb_pruned} KB items pruned | {freed}KB old files freed")
