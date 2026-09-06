"""
One-off script to write BOA-017 board files:
  - DEC-023 to decisions.json
  - BOA-017-A1 through A4 to action_items.json
Run once then delete.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "data" / "board"

# ── DEC-023 ───────────────────────────────────────────────────────────────────
dec_path = BOARD / "decisions.json"
dec_data = json.loads(dec_path.read_text(encoding="utf-8"))

dec_023 = {
    "id": "DEC-023",
    "agenda_item": "BOA-017",
    "date": "2026-05-24",
    "topic": "Monster Scout Analyst Persona — Lynch+Fisher+Shay Narrative Re-rating Score — CONDITIONAL APPROVED (Narrowed Scope)",
    "outcome": "CONDITIONAL_APPROVED",
    "vote": {
        "fund_manager": "CONDITIONAL Y — Bottleneck signal only, re-rating as TEXT field not scored, TAM rejected. Bottleneck ownership is the most mechanical of the three signals. Lynch two-minute drill + Fisher five-year competitive position + Shay re-rating trigger are three genuinely complementary lenses that improve thesis construction. Technical floor required: score_candidate() must maintain minimum 35/100 from Technical layer before narrative signals apply. Market_cap dead code bug must be resolved before Phase 2.",
        "research": "CONDITIONAL Y (synthesized — rate limit exceeded, Lynch/Fisher/Soros precedent used) — Lynch two-minute drill + Fisher qualitative moat + Soros reflexivity are independently validated 50-year frameworks. Shay Boloor methodology (re-rating prediction, bottleneck mapping) is the modern instantiation. External hit rates: Fisher Common Stocks and Uncommon Profits (1958), Lynch One Up on Wall Street (1989), Soros Alchemy of Finance (1987). No quantified hit rate for re-rating detection — intrinsically qualitative.",
        "quant": "N (5.7/20, N=0, formula mismatch, re-rating proxy inverted) — Re-rating proxy (old_label != current_label) is inverted: a stock that has ALREADY been re-rated is Phase 4-5 of narrative lifecycle, not Phase 2. No backtestable formula for prospective re-rating. Bottleneck signal feasible but requires bottleneck_owners.json infrastructure that does not exist. Score 5.7/20. Cannot approve scored dimension with N=0.",
        "da": "CONDITIONAL Y (Signal 2 bottleneck only, infrastructure prerequisite) — Re-rating: unverifiable, reject as scored dimension. Bottleneck: verifiable via lookup table, but bottleneck_owners.json does not exist as machine-readable file — BOA-018 prerequisite. TAM expansion: inconsistently defined, reject. Critical bug found: market_cap block in score_candidate() always outputs +0 because monster_scout.py never writes market_cap to candidates.json. Stated 100-pt system is actually 90-pt max."
    },
    "outcome_rationale": "CONDITIONAL APPROVED 3/4 narrowed scope. Quant block (N=0) prevented full approval. Approved scope: (1) Lynch+Fisher+Shay persona added to monster_deep_research.py Haiku prompt. (2) Re-rating as required TEXT FIELD in Telegram MSG 2 — not scored. (3) Bottleneck owner +5 pts PENDING BOA-018 infrastructure. (4) market_cap dead code fixed. TAM rejected. Revisit scoring after N>=20 Mode B closed trades.",
    "narrowed_scope": {
        "approved": [
            "Lynch+Fisher+Shay 3-layer persona in monster_deep_research.py Haiku prompt",
            "Re-rating as required TEXT FIELD in Telegram MSG 2 (not scored)",
            "Bottleneck owner +5 pts AFTER BOA-018 infrastructure complete",
            "market_cap dead code bug fix in monster_of_week_selector.py"
        ],
        "rejected": [
            "Re-rating as scored dimension (+8 pts) — N=0, inverted proxy formula",
            "TAM expansion dimension (+2 pts) — inconsistently defined, not machine-readable",
            "Narrative Quality as standalone 4th layer reducing other layers proportionally"
        ]
    },
    "rules_added": [
        "Lynch+Fisher+Shay 3-layer persona: Lynch=explain in 1 min (name the customer), Fisher=competitive position in 5 years (GM trend), Shay=old narrative vs new narrative (bottleneck + re-rating trigger)",
        "Telegram MSG 2 must include re-rating annotation: 'Re-rating: [old narrative] → [new narrative]' — required field, blank not permitted",
        "Bottleneck owner +5 pts: is_bottleneck_owner = ticker in bottleneck_owners.json. PENDING BOA-018. Zero pts until file exists.",
        "market_cap dead code: remove non-functional block OR wire Finnhub fetch. Actual max score documented as 90 pts until fixed."
    ],
    "hypothesis_tags": [
        "Bottleneck +5 pts — HYPOTHESIS until N>=10 Mode B closed trades with bottleneck_owner=True vs False comparison",
        "Re-rating text field quality — HYPOTHESIS until N>=20 MOTW briefs with re-rating annotation tracked vs actual outcome"
    ],
    "conditions": [
        "CONDITION-1: BOA-018 must complete (bottleneck_owners.json) before bottleneck +5 activates",
        "CONDITION-2: Re-rating field in MSG 2 must be populated — blank not acceptable",
        "CONDITION-3: market_cap dead code fixed before any future claim of 100-pt system",
        "CONDITION-4: Technical floor: bottleneck +5 only when Technical layer score >= 35",
        "CONDITION-5: After N>=20 Mode B closed trades, BOA-019 re-opens to evaluate re-rating as scored dimension",
        "CONDITION-6: Score ceiling documented: 95 pts max after BOA-018 (or 90 pts without bottleneck)"
    ],
    "action_items": ["BOA-017-A1", "BOA-017-A2", "BOA-017-A3", "BOA-017-A4"],
    "adjacent_signals_opened": ["BOA-018", "BOA-019"],
    "cio_override": False
}

# Insert DEC-023 at the front
dec_data["decisions"].insert(0, dec_023)
dec_data["last_updated"] = "2026-05-24T23:55"
dec_path.write_text(json.dumps(dec_data, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"OK: DEC-023 written  |  total decisions: {len(dec_data['decisions'])}")


# ── action_items.json ─────────────────────────────────────────────────────────
ai_path = BOARD / "action_items.json"
ai_data = json.loads(ai_path.read_text(encoding="utf-8"))

new_items = [
    {
        "id": "BOA-017-A1",
        "agenda_item": "BOA-017 / DEC-023",
        "task": "Build data/themes/bottleneck_owners.json — machine-readable static lookup from thematic_bottleneck_framework.md",
        "owner": "DE",
        "assigned_date": "2026-05-24",
        "status": "PENDING",
        "priority": "P1",
        "complexity": "Easy",
        "data_source": "memory/thematic_bottleneck_framework.md (Claude memory) — Super Bottlenecks table + per-theme bottleneck owners",
        "script_target": "data/themes/bottleneck_owners.json (new static file)",
        "implementation_notes": (
            "Read thematic_bottleneck_framework.md from Claude memory (MEMORY.md index). "
            "Extract all tickers listed as 'bottleneck owners' or 'THE bottleneck' per theme. "
            "Include Super Bottlenecks cross-theme table entries. "
            "Schema per entry: {ticker, company_name, bottleneck_description, chain_position, pricing_power (H/M/L), theme, source_section}. "
            "Target ~30-50 tickers. Examples: NVDA (GPU compute), ASML (EUV lithography), AVGO (custom ASIC), COHU (AI thermal test), PLAB (photomask). "
            "File is STATIC — updated manually when thematic_bottleneck_framework.md is updated. "
            "Format: {last_updated, source_doc, bottleneck_owners: [{ticker, ...}, ...]}. "
            "This file is the PREREQUISITE for BOA-017-A4 (bottleneck +5 scoring). "
            "DO NOT activate scoring until this file exists and is validated."
        ),
        "blocking": ["BOA-017-A4"],
        "blocked_by": []
    },
    {
        "id": "BOA-017-A2",
        "agenda_item": "BOA-017 / DEC-023",
        "task": "Update monster_deep_research.py with Lynch+Fisher+Shay 3-layer persona + re-rating required text field in Telegram MSG 2",
        "owner": "DE",
        "assigned_date": "2026-05-24",
        "status": "PENDING",
        "priority": "P1",
        "complexity": "Easy",
        "data_source": "scripts/pre_compute/monster_deep_research.py (existing)",
        "script_target": "scripts/pre_compute/monster_deep_research.py (modify _generate_thesis_ai + format_motw_messages)",
        "implementation_notes": (
            "PART 1 — Update _generate_thesis_ai() Haiku prompt with 3-layer persona: "
            "Persona block: 'You are AlphaAbsolute Monster Scout — a team of three analysts: "
            "(1) Peter Lynch: explain in 60 seconds who buys this product and why it sells. Name the actual customer. No jargon. "
            "(2) Philip Fisher: will the competitive position be stronger in 5 years? What is the gross margin trend? "
            "(3) Shay Boloor: what is the OLD narrative the market currently uses to price this stock? "
            "What is the NEW narrative that will re-price it higher? Where is the specific bottleneck this company owns in its value chain?' "
            "Required outputs in thesis JSON: {lynch_60s (1-2 sentences plain language), fisher_5yr (1-2 sentences), "
            "shay_old_narrative (1 sentence), shay_new_narrative (1 sentence), shay_bottleneck (1 sentence or None)}. "
            "PART 2 — Update format_motw_messages() MSG 2 to include re-rating section: "
            "After thesis paragraph, add: 'Re-rating: [shay_old_narrative] → [shay_new_narrative]'. "
            "This field is REQUIRED. If Haiku returns None: default text = 'Re-rating: [narrative analysis pending — manual review]'. "
            "Blank is NOT permitted in Telegram output."
        ),
        "blocking": [],
        "blocked_by": []
    },
    {
        "id": "BOA-017-A3",
        "agenda_item": "BOA-017 / DEC-023",
        "task": "Fix market_cap dead code in monster_of_week_selector.py score_candidate() — document actual max score",
        "owner": "DE",
        "assigned_date": "2026-05-24",
        "status": "PENDING",
        "priority": "P1",
        "complexity": "Easy",
        "data_source": "scripts/pre_compute/monster_of_week_selector.py (existing)",
        "script_target": "scripts/pre_compute/monster_of_week_selector.py (modify score_candidate)",
        "implementation_notes": (
            "The market_cap scoring block (cap_pts 0-10) in score_candidate() always outputs +0 "
            "because monster_scout.py never writes market_cap or mkt_cap to candidates.json. "
            "The cap_label calculation also has a bug: 'mkt_cap and mkt_cap < 1e9' with mkt_cap=0 always evaluates to False. "
            "OPTION A (Phase 1 — recommended): Remove the dead block entirely. "
            "Update docstring from '100 pts max' to '90 pts max (market_cap block pending Phase 2)'. "
            "Remove cap_pts from score, remove cap_label from breakdown. "
            "OPTION B (Phase 2 — future): Wire Finnhub /quote or FMP /profile market_cap fetch into monster_scout.py "
            "and write market_cap to candidates.json, then restore scoring block. "
            "DO Option A now. Option B in BOA-013-A4 Phase 2 work. "
            "Also update the module docstring at top of file: change '100 pts max' to reflect actual breakdown "
            "(Fundamental 40 + Market Structure 15 [was 25 but cap removed] + Technical 35 = 90 pts). "
            "Note: BOA-017-A4 will add Bottleneck +5 → new max 95 pts after BOA-018 complete."
        ),
        "blocking": [],
        "blocked_by": []
    },
    {
        "id": "BOA-017-A4",
        "agenda_item": "BOA-017 / DEC-023",
        "task": "Add bottleneck_owner +5 scoring to score_candidate() in monster_of_week_selector.py",
        "owner": "DE",
        "assigned_date": "2026-05-24",
        "status": "PENDING",
        "priority": "P3",
        "complexity": "Easy",
        "data_source": "data/themes/bottleneck_owners.json (from BOA-017-A1) + scripts/pre_compute/monster_of_week_selector.py",
        "script_target": "scripts/pre_compute/monster_of_week_selector.py (modify score_candidate + _load helpers)",
        "implementation_notes": (
            "PREREQUISITE: BOA-017-A1 must complete and bottleneck_owners.json must exist. "
            "Add loader: _load_bottleneck_owners() -> set of tickers. "
            "Load from data/themes/bottleneck_owners.json. Returns set() if file missing (graceful fallback). "
            "In score_candidate(): "
            "is_bottleneck_owner = ticker in bottleneck_owners_set "
            "bottleneck_pts = 5 if is_bottleneck_owner else 0 "
            "CONDITION: only award +5 if Technical layer score >= 35 (prevents narrative inflation of weak charts). "
            "score += bottleneck_pts "
            "breakdown['bottleneck'] = f'Bottleneck owner: +{bottleneck_pts}' "
            "Add to returned dict: is_bottleneck_owner, bottleneck_description (from owners file lookup). "
            "Update module docstring: Narrative layer (5 pts): bottleneck owner lookup. Max score: 95 pts. "
            "HYPOTHESIS tag: monitor N>=10 Mode B closed trades, compare outcomes bottleneck_owner=True vs False."
        ),
        "blocking": [],
        "blocked_by": ["BOA-017-A1", "BOA-017-A3"]
    }
]

# Prepend new items to the items list
ai_data["items"] = new_items + ai_data["items"]

# Update summary
old_total = ai_data["summary"]["total"]
old_pending = ai_data["summary"]["pending"]
ai_data["summary"]["total"] = old_total + 4
ai_data["summary"]["pending"] = old_pending + 4
ai_data["summary"]["notes"] = (
    "BOA-017 CONDITIONAL APPROVED: 4 action items added (A1=bottleneck_owners.json, "
    "A2=Lynch/Fisher/Shay persona update, A3=market_cap dead code fix, A4=bottleneck scoring). "
    "A1 is prerequisite for A4. A3 prerequisite for A4. "
    "BOA-013 items also pending (Monster Scout v2 scoring)."
)
ai_data["last_updated"] = "2026-05-24T23:55"

ai_path.write_text(json.dumps(ai_data, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"OK: action_items.json updated  |  total={ai_data['summary']['total']}  pending={ai_data['summary']['pending']}")
print("Done.")
