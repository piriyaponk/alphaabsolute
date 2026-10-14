"""
constants.py — PULSE-TH shared constants
=========================================
Single source of truth for RS window and threshold values used by ALL PULSE-TH scripts.
Changing a value here propagates to every script that imports it.

RULE: Never hardcode RS_LOOKBACK_DAYS or RS_MIN_PCT in individual scripts.
See CLAUDE.md section "Pipeline Auto-Fix Rules — PULSE-TH RS Window".
"""

# Canonical RS window for PULSE-TH: 63 trading days ≈ 3 calendar months
# Used by: thai_entry_screen.py (focus list ranking + filter) and
#          thai_pulse_daily.py (_compute_rs_from_ohlcv, _RS_FILL_MIN)
RS_LOOKBACK_DAYS: int = 63

# Minimum RS percentile (full SET universe) required for a ticker to appear
# in the PULSE-TH focus list and Telegram output.
RS_MIN_PCT: float = 60.0
