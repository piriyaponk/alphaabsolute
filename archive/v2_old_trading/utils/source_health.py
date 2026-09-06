"""
AlphaAbsolute -- Source Health Tracker
=======================================
Persistent health scoring for all data sources.
Auto-degrades on failure, auto-recovers over 24h.
Used by data_engine.py to dynamically rank sources.

Usage:
    from scripts.utils.source_health import SourceHealth
    health = SourceHealth()
    health.record_success("edgar", latency=2.1)
    health.record_failure("yahoo", error="403 Forbidden")
    ranked = health.get_ranked("eps")  # -> ["edgar", "fmp", "finnhub", "yahoo"]

Storage: data/source_health.json (auto-created on first run)
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR   = Path(__file__).resolve().parents[2]
HEALTH_FILE = BASE_DIR / "data" / "source_health.json"
HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)

# Default health score (0-100). Sources start at their default.
# Higher = more trusted. Below SKIP_THRESHOLD = auto-skipped.
DEFAULT_HEALTH: dict[str, float] = {
    "edgar":    90.0,  # Free, authoritative, very reliable
    "fmp":      75.0,  # Free 250/day, well-structured
    "finnhub":  75.0,  # Free 60/min, key required
    "polygon":  80.0,  # Free EOD, fast, reliable
    "tiingo":   70.0,  # Free 500/day, good backup
    "yahoo":    50.0,  # Unofficial, WARP issues, frequently changes
    "fred":     88.0,  # Federal Reserve, extremely stable
    "set_mcp":  70.0,  # Thai SET, depends on MCP connectivity
    "stooq":    20.0,  # Now requires paid key — low default
    "yfinance": 35.0,  # Last resort only — slow, SSL issues, frequently breaks
}

SKIP_THRESHOLD  = 30.0   # Sources below this are auto-skipped
RECOVER_HOURS   = 24.0   # Hours for full recovery from 0 to default
SUCCESS_BOOST   = +8.0   # Score increase per successful call
FAILURE_HIT     = -25.0  # Score decrease per failed call
MAX_SCORE       = 100.0

# Priority matrix — STATIC fallback ordering (used when health is equal)
# Source: source_registry.py PRIORITY_MATRIX
_PRIORITY_ORDER: dict[str, list[str]] = {
    "eps":              ["edgar", "fmp", "finnhub", "yahoo"],
    "revenue":          ["edgar", "fmp", "finnhub", "yahoo"],
    "gross_margin":     ["edgar", "fmp", "finnhub"],
    "ohlcv":            ["polygon", "tiingo", "yahoo", "stooq"],
    "quote":            ["finnhub", "polygon", "yahoo"],
    "market_cap":       ["finnhub", "fmp", "yahoo"],
    "vix":              ["yahoo", "fred"],
    "macro_yields":     ["fred", "yahoo"],
    "macro_dxy":        ["fred", "yahoo"],
    "macro_gdp":        ["fred"],
    "macro_pce":        ["fred"],
    "earnings_date":    ["fmp", "finnhub", "yahoo"],
    "analyst_estimates":["fmp", "finnhub"],
    "thai_stocks":      ["set_mcp"],
}


# ---------------------------------------------------------------------------
# SourceHealth class
# ---------------------------------------------------------------------------

class SourceHealth:
    """
    Persistent health tracker for data sources.

    Health score (0-100):
    - Starts at DEFAULT_HEALTH per source
    - +SUCCESS_BOOST on every successful call
    - +FAILURE_HIT (negative) on every failed call
    - Slowly recovers toward DEFAULT_HEALTH over RECOVER_HOURS

    A source with health < SKIP_THRESHOLD is automatically excluded
    from get_ranked() results.
    """

    def __init__(self):
        self._state: dict = self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_success(self, source: str, latency: float = 0.0) -> None:
        """Record a successful API call. Boosts health score."""
        self._ensure_entry(source)
        entry = self._state[source]
        entry["score"] = min(MAX_SCORE, entry["score"] + SUCCESS_BOOST)
        entry["total_calls"]   += 1
        entry["success_calls"] += 1
        entry["last_success"]  = _now()
        entry["last_latency"]  = round(latency, 2)
        # Rolling avg latency (exponential smoothing, alpha=0.3)
        if entry["avg_latency"] == 0.0:
            entry["avg_latency"] = round(latency, 2)
        else:
            entry["avg_latency"] = round(
                0.7 * entry["avg_latency"] + 0.3 * latency, 2
            )
        self._save()

    def record_failure(self, source: str, error: str = "") -> None:
        """Record a failed API call. Degrades health score."""
        self._ensure_entry(source)
        entry = self._state[source]
        entry["score"]       = max(0.0, entry["score"] + FAILURE_HIT)
        entry["total_calls"] += 1
        entry["last_failure"] = _now()
        entry["last_error"]   = str(error)[:200]
        self._save()

    def get_health(self, source: str) -> float:
        """Return current health score for a source (with time-based recovery applied)."""
        self._ensure_entry(source)
        self._apply_recovery(source)
        return round(self._state[source]["score"], 1)

    def get_ranked(self, data_type: str) -> list[str]:
        """
        Return ordered list of sources for this data_type,
        sorted by effective health score (descending).
        Sources below SKIP_THRESHOLD are excluded.

        If data_type not in PRIORITY_MATRIX, returns all sources by health.
        """
        base_order = _PRIORITY_ORDER.get(data_type, list(DEFAULT_HEALTH.keys()))

        # Apply recovery to all entries before ranking
        for src in base_order:
            self._ensure_entry(src)
            self._apply_recovery(src)

        # Score = health × positional_weight (to break ties in favor of default order)
        # Positional weight: rank 1 = 1.0, rank 2 = 0.99, rank 3 = 0.98, ...
        scored: list[tuple[float, str]] = []
        for idx, src in enumerate(base_order):
            health = self._state[src]["score"]
            if health < SKIP_THRESHOLD:
                continue   # Skip degraded sources
            positional = 1.0 - (idx * 0.01)
            effective  = health * positional
            scored.append((effective, src))

        scored.sort(reverse=True)
        return [src for _, src in scored]

    def get_status_report(self) -> dict:
        """Return full health status dict for all sources."""
        report = {}
        for src in DEFAULT_HEALTH:
            self._ensure_entry(src)
            self._apply_recovery(src)
            e = self._state[src]
            avail = (
                round(e["success_calls"] / e["total_calls"] * 100, 1)
                if e["total_calls"] > 0 else 100.0
            )
            report[src] = {
                "health":        round(e["score"], 1),
                "default_health": DEFAULT_HEALTH.get(src, 50.0),
                "availability_pct": avail,
                "total_calls":   e["total_calls"],
                "avg_latency_s": e["avg_latency"],
                "last_success":  e["last_success"],
                "last_failure":  e["last_failure"],
                "last_error":    e["last_error"],
                "skipped":       e["score"] < SKIP_THRESHOLD,
            }
        return report

    def reset(self, source: str | None = None) -> None:
        """Reset health to default. Pass None to reset ALL sources."""
        if source:
            self._ensure_entry(source)
            self._state[source]["score"] = DEFAULT_HEALTH.get(source, 50.0)
        else:
            for src in DEFAULT_HEALTH:
                self._ensure_entry(src)
                self._state[src]["score"] = DEFAULT_HEALTH[src]
        self._save()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_entry(self, source: str) -> None:
        if source not in self._state:
            self._state[source] = {
                "score":         DEFAULT_HEALTH.get(source, 50.0),
                "last_updated":  _now(),
                "last_success":  None,
                "last_failure":  None,
                "last_error":    "",
                "last_latency":  0.0,
                "avg_latency":   0.0,
                "total_calls":   0,
                "success_calls": 0,
            }

    def _apply_recovery(self, source: str) -> None:
        """
        Slowly recover health toward DEFAULT_HEALTH based on elapsed time.
        Recovery rate: full recovery in RECOVER_HOURS.
        """
        entry   = self._state[source]
        default = DEFAULT_HEALTH.get(source, 50.0)
        now_ts  = time.time()

        last_ts_str = entry.get("last_updated")
        try:
            last_ts = datetime.fromisoformat(last_ts_str).timestamp() if last_ts_str else now_ts
        except Exception:
            last_ts = now_ts

        elapsed_hours = (now_ts - last_ts) / 3600.0
        if elapsed_hours <= 0:
            return

        # Recovery amount per hour = default / RECOVER_HOURS
        recovery_per_hour = default / RECOVER_HOURS
        recovery = elapsed_hours * recovery_per_hour

        current = entry["score"]
        if current < default:
            new_score = min(default, current + recovery)
            entry["score"] = round(new_score, 2)

        entry["last_updated"] = _now()

    def _load(self) -> dict:
        if HEALTH_FILE.exists():
            try:
                data = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
                return data
            except Exception:
                pass
        return {}

    def _save(self) -> None:
        HEALTH_FILE.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Module-level singleton (shared across all imports in same process)
# ---------------------------------------------------------------------------

_HEALTH_INSTANCE: SourceHealth | None = None


def get_health() -> SourceHealth:
    """Return the shared SourceHealth singleton."""
    global _HEALTH_INSTANCE
    if _HEALTH_INSTANCE is None:
        _HEALTH_INSTANCE = SourceHealth()
    return _HEALTH_INSTANCE


# ---------------------------------------------------------------------------
# CLI — python source_health.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    h = SourceHealth()

    if len(sys.argv) > 1 and sys.argv[1] == "reset":
        h.reset()
        print("All source health scores reset to defaults.")
        sys.exit(0)

    report = h.get_status_report()
    print("\nSource Health Status")
    print("=" * 60)
    header = f"{'Source':<12} {'Health':>7} {'Avail%':>7} {'AvgLat':>8} {'Calls':>6}  Status"
    print(header)
    print("-" * 60)
    for src, info in sorted(report.items(), key=lambda x: -x[1]["health"]):
        status = "SKIP" if info["skipped"] else "OK"
        print(
            f"{src:<12} {info['health']:>7.1f} {info['availability_pct']:>7.1f} "
            f"{info['avg_latency_s']:>8.2f} {info['total_calls']:>6}  {status}"
        )
    print()

    if len(sys.argv) > 1 and sys.argv[1] == "ranked":
        dtype = sys.argv[2] if len(sys.argv) > 2 else "eps"
        ranked = h.get_ranked(dtype)
        print(f"Ranked sources for '{dtype}': {ranked}")
