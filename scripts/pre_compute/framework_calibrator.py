"""
AlphaAbsolute -- I9 Bayesian Framework Calibrator  ($0 cost, pure Python)
=========================================================================
The learning brain of AlphaAbsolute.

"The system that improves itself from every trade is worth 10x a static system."

How it works:
  After every closed trade, this calibrator:
  1. Reads the trade outcome (win/loss/size)
  2. Looks up which signals were active at entry (from cached per-ticker data)
  3. Updates Bayesian hit rates for each signal type
  4. Recomputes signal weights (signals that predict wins get more weight)
  5. Writes updated weights -> used by auto_trader + EMLS scoring

Signals tracked (each has a hit_rate = wins / total when signal was active):
  - emls_score tier (90+, 80-89, 70-79, <70)
  - base_count (1, 2, 3, 4)
  - exhaustion_score tier (<40, 40-55, 55-65, >65)
  - inflection_score tier (>70, 50-70, 30-50, <30)
  - rs_composite_pct tier (>90, 70-90, 50-70, <50)
  - nrgc_phase at entry (2, 3, 4)
  - regime at entry (POWER_UPTREND, CONFIRMED, PRESSURE, CHOPPY)
  - setup_type (leader, wyckoff, hypergrowth)
  - canslim_grade (A+, A, B, C)
  - td_signal (buy_setup9, neutral, sell_setup7_9)

Output:
  data/framework_calibrator/signal_weights.json   (live weights used by system)
  data/framework_calibrator/calibration_log.json  (history of all calibrations)
  data/framework_calibrator/signal_stats.json     (hit rates per signal)

Update frequency: After each trade closes + weekly batch recalibration.

Cost: $0 (pure Python, reads trade_log.json + cached signal files)
"""

import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ── Encoding fix for Thai terminal (cp874) ───────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
OUT_DIR  = BASE_DIR / "data" / "framework_calibrator"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# auto_trader.py writes JSONL (one JSON object per line) to paper_trades.jsonl
TRADE_LOG         = BASE_DIR / "data" / "portfolio" / "paper_trades.jsonl"
# Legacy path fallback (old runs may have written here)
TRADE_LOG_LEGACY  = BASE_DIR / "data" / "paper_trading" / "trade_log.json"
WEIGHTS_FILE      = OUT_DIR / "signal_weights.json"
STATS_FILE        = OUT_DIR / "signal_stats.json"
CAL_LOG_FILE      = OUT_DIR / "calibration_log.json"
PRISM_STATS_FILE  = BASE_DIR / "data" / "backtest" / "prism_signal_stats.json"


# ── Default Weights (from EMLS framework v4 -- starting priors) ────────────────
# These are the initial Bayesian priors. They update from real trade data.
DEFAULT_WEIGHTS = {
    # Base-level signal importance (EMLS framework defaults)
    "emls_90plus":        1.00,   # Maximum conviction
    "emls_80_89":         0.75,
    "emls_70_79":         0.50,
    "emls_below_70":      0.25,

    "base_1":             1.00,   # Best R/R
    "base_2":             0.85,
    "base_3":             0.60,
    "base_4plus":         0.00,   # BLOCKED

    "phase_2":            1.00,   # Best entry phase
    "phase_3":            0.80,
    "phase_4":            0.40,
    "phase_other":        0.20,

    "rs_90plus":          1.00,   # Top decile leaders
    "rs_70_90":           0.80,
    "rs_50_70":           0.50,
    "rs_below_50":        0.20,

    "inflection_70plus":  1.00,   # Phase 2 confirmed
    "inflection_50_70":   0.75,
    "inflection_30_50":   0.40,
    "inflection_below_30": 0.10,

    "exhaustion_normal":  1.00,   # < 40
    "exhaustion_watch":   0.70,   # 40-54
    "exhaustion_high":    0.40,   # 55-64
    "exhaustion_de_risk": 0.10,   # 65+

    "regime_power":       1.00,
    "regime_confirmed":   0.85,
    "regime_pressure":    0.50,
    "regime_choppy":      0.25,
    "regime_correction":  0.00,

    "setup_leader":       0.85,
    "setup_hypergrowth":  1.00,
    "setup_wyckoff":      0.70,

    "canslim_a_plus":     1.00,
    "canslim_a":          0.80,
    "canslim_b":          0.50,
    "canslim_c":          0.25,

    "td_buy9":            1.00,   # Ideal entry
    "td_neutral":         0.75,
    "td_sell7_9":         0.00,   # Should never enter
}


# ── Signal State Tracker ──────────────────────────────────────────────────────

class SignalStats:
    """
    Tracks hit rates for each signal type.
    hit_rate = wins / (wins + losses) when this signal was ACTIVE at entry.
    Uses Laplace smoothing to avoid zero-probability extremes.
    """
    def __init__(self):
        self.data = self._load()

    def _load(self) -> dict:
        if STATS_FILE.exists():
            try:
                return json.loads(STATS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        # Initialize all signals with weak prior (2 wins, 2 losses = 50% base rate)
        return {
            key: {"wins": 2, "losses": 2, "total": 4,
                  "hit_rate": 0.50, "avg_win_pct": 0.0, "avg_loss_pct": 0.0}
            for key in DEFAULT_WEIGHTS
        }

    def update(self, signal_key: str, win: bool, pnl_pct: float):
        if signal_key not in self.data:
            self.data[signal_key] = {"wins": 2, "losses": 2, "total": 4,
                                      "hit_rate": 0.50, "avg_win_pct": 0.0, "avg_loss_pct": 0.0}
        s = self.data[signal_key]
        s["total"] += 1
        if win:
            s["wins"]      += 1
            old_avg = s.get("avg_win_pct", 0.0)
            s["avg_win_pct"] = (old_avg * (s["wins"] - 1) + pnl_pct) / s["wins"]
        else:
            s["losses"]     += 1
            old_avg = s.get("avg_loss_pct", 0.0)
            s["avg_loss_pct"] = (old_avg * (s["losses"] - 1) + pnl_pct) / s["losses"]
        # Recompute hit rate with Laplace smoothing (always at least 2+2 prior)
        s["hit_rate"] = s["wins"] / (s["wins"] + s["losses"])

    def hit_rate(self, signal_key: str) -> float:
        return self.data.get(signal_key, {}).get("hit_rate", 0.50)

    def save(self):
        STATS_FILE.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def to_dict(self) -> dict:
        return self.data


# ── Signal Key Extraction ─────────────────────────────────────────────────────

def _extract_signal_keys(trade: dict) -> list:
    """
    From a closed trade record, determine which signal keys were ACTIVE at entry.
    Reads cached signal files for the ticker at entry date.
    """
    ticker    = trade.get("ticker", "")
    setup     = trade.get("setup_type", "leader")
    emls      = float(trade.get("emls_score", 0))
    open_date = trade.get("open_date", "")
    keys      = []

    # EMLS score tier
    if emls >= 90:   keys.append("emls_90plus")
    elif emls >= 80: keys.append("emls_80_89")
    elif emls >= 70: keys.append("emls_70_79")
    else:            keys.append("emls_below_70")

    # Setup type
    if setup == "hypergrowth":   keys.append("setup_hypergrowth")
    elif setup == "wyckoff":     keys.append("setup_wyckoff")
    else:                        keys.append("setup_leader")

    # Base count (from cached file)
    bc_file = BASE_DIR / "data" / "base_counts" / f"{ticker}.json"
    if bc_file.exists():
        try:
            bc   = json.loads(bc_file.read_text(encoding="utf-8"))
            cnt  = bc.get("base_count", 0)
            if   cnt == 1:   keys.append("base_1")
            elif cnt == 2:   keys.append("base_2")
            elif cnt == 3:   keys.append("base_3")
            elif cnt >= 4:   keys.append("base_4plus")
        except Exception:
            pass

    # NRGC phase (from cached state)
    nrgc_file = BASE_DIR / "data" / "nrgc" / "state" / f"{ticker}.json"
    if nrgc_file.exists():
        try:
            nrgc  = json.loads(nrgc_file.read_text(encoding="utf-8"))
            phase = nrgc.get("phase", 0)
            if   phase == 2: keys.append("phase_2")
            elif phase == 3: keys.append("phase_3")
            elif phase == 4: keys.append("phase_4")
            else:            keys.append("phase_other")
        except Exception:
            pass

    # RS percentile
    rs_file = BASE_DIR / "data" / "rs_universe" / "latest.json"
    if rs_file.exists():
        try:
            rs_data  = json.loads(rs_file.read_text(encoding="utf-8"))
            rs_pct   = rs_data.get("universe", {}).get(ticker, {}).get("rs_composite_pct", 0)
            if   rs_pct >= 90: keys.append("rs_90plus")
            elif rs_pct >= 70: keys.append("rs_70_90")
            elif rs_pct >= 50: keys.append("rs_50_70")
            else:              keys.append("rs_below_50")
        except Exception:
            pass

    # Earnings inflection score
    infl_file = BASE_DIR / "data" / "earnings_inflection" / f"{ticker}.json"
    if infl_file.exists():
        try:
            infl  = json.loads(infl_file.read_text(encoding="utf-8"))
            sc    = infl.get("score", 0)
            if   sc >= 70: keys.append("inflection_70plus")
            elif sc >= 50: keys.append("inflection_50_70")
            elif sc >= 30: keys.append("inflection_30_50")
            else:          keys.append("inflection_below_30")
        except Exception:
            pass

    # Exhaustion score
    ex_file = BASE_DIR / "data" / "exhaustion" / f"{ticker}.json"
    if ex_file.exists():
        try:
            ex  = json.loads(ex_file.read_text(encoding="utf-8"))
            sc  = ex.get("exhaustion_score", 0)
            if   sc < 40:  keys.append("exhaustion_normal")
            elif sc < 55:  keys.append("exhaustion_watch")
            elif sc < 65:  keys.append("exhaustion_high")
            else:          keys.append("exhaustion_de_risk")
        except Exception:
            pass

    # Market regime at entry (from strategy_clearance)
    m0_file = BASE_DIR / "data" / "market_regime" / "strategy_clearance.json"
    if m0_file.exists():
        try:
            m0     = json.loads(m0_file.read_text(encoding="utf-8"))
            regime = m0.get("regime_name", "")
            if   regime == "POWER_UPTREND":     keys.append("regime_power")
            elif regime == "CONFIRMED_UPTREND": keys.append("regime_confirmed")
            elif regime == "PRESSURE":          keys.append("regime_pressure")
            elif regime == "CHOPPY":            keys.append("regime_choppy")
            elif regime in ("CORRECTION", "EARLY_BEAR", "BEAR"): keys.append("regime_correction")
        except Exception:
            pass

    # CANSLIM grade
    cs_file = BASE_DIR / "data" / "canslim_scores" / "_summary.json"
    if cs_file.exists():
        try:
            cs    = json.loads(cs_file.read_text(encoding="utf-8"))
            grade = cs.get(ticker, {}).get("grade", "")
            if   grade == "A+": keys.append("canslim_a_plus")
            elif grade == "A":  keys.append("canslim_a")
            elif grade == "B":  keys.append("canslim_b")
            elif grade == "C":  keys.append("canslim_c")
        except Exception:
            pass

    return keys


# ── PRISM Backtest Prior ──────────────────────────────────────────────────────

def _load_prism_prior() -> dict:
    """
    Read prism_signal_stats.json from backtester output.
    Returns {rs_signal_weight_adj, tt_weight_adj, pulse_hit_rate} or {} if file missing.

    Logic:
      - Grade A 60D hit rate > 65% → boost rs_70_90 / rs_90plus weights (+0.05)
      - Grade A 60D hit rate < 50% → penalize rs_70_90 / rs_90plus weights (-0.05)
      - Grade A 60D hit rate 50-65% → no adjustment (within expected range)
    """
    if not PRISM_STATS_FILE.exists():
        return {}

    try:
        data = json.loads(PRISM_STATS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [Calibrator] Warning: could not read prism_signal_stats.json: {e}")
        return {}

    # Expected schema from pulse_backtester.py:
    # {"grade_a": {"hit_rate_60d": float, "count": int, "alpha_60d": float, ...},
    #  "grade_b": {...}, "by_criterion": {...}}
    if not isinstance(data, dict):
        print(f"  [Calibrator] Warning: prism_signal_stats.json has unexpected format (not a dict)")
        return {}

    grade_a = data.get("grade_a")
    if grade_a is None:
        # Schema mismatch — file exists but 'grade_a' key missing
        known_keys = list(data.keys())[:5]
        print(f"  [Calibrator] Warning: prism_signal_stats.json missing 'grade_a' key. "
              f"Found: {known_keys} — may need to re-run backtester")
        return {}

    if not isinstance(grade_a, dict):
        print(f"  [Calibrator] Warning: 'grade_a' is not a dict in prism_signal_stats.json")
        return {}

    hit_60  = grade_a.get("hit_rate_60d")
    count   = grade_a.get("count", 0)
    alpha   = grade_a.get("alpha_60d")

    if hit_60 is None:
        print(f"  [Calibrator] Warning: 'grade_a.hit_rate_60d' missing — backtest may be incomplete")
        return {}

    if count < 10:   # need at least 10 signals to trust
        return {}

    # Compute RS weight adjustment from backtest evidence
    rs_adj = 0.0
    if hit_60 > 0.65:
        rs_adj = +0.05   # backtest confirms RS screen adds alpha
    elif hit_60 < 0.50:
        rs_adj = -0.05   # RS screen not working — penalize

    return {
        "prism_hit_rate_60d": hit_60,
        "prism_count":        count,
        "prism_alpha_60d":    alpha,
        "rs_weight_adj":      rs_adj,
    }


# ── Calibration Run ───────────────────────────────────────────────────────────

def calibrate(verbose: bool = True) -> dict:
    """
    Read all closed trades from trade_log.json.
    Update signal stats + recompute weights.
    Save to signal_weights.json.
    """
    today = date.today().isoformat()
    if verbose:
        print(f"\n[I9 Calibrator] Running {today}")

    # Determine which trade log exists
    log_path = None
    if TRADE_LOG.exists():
        log_path = TRADE_LOG
    elif TRADE_LOG_LEGACY.exists():
        log_path = TRADE_LOG_LEGACY
        if verbose:
            print(f"  [WARN] Using legacy trade log: {TRADE_LOG_LEGACY}")

    if log_path is None:
        if verbose:
            print("  No trade log found -- nothing to calibrate")
        return {}

    try:
        raw = log_path.read_text(encoding="utf-8").strip()
        # JSONL format: one JSON object per line (written by auto_trader.py)
        # Legacy JSON format: a list of objects
        if raw.startswith("["):
            # Legacy: JSON list
            log = json.loads(raw)
        else:
            # JSONL: parse line by line, skip blank/malformed lines
            log = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    log.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        print(f"  [Error] Could not read trade log: {e}")
        return {}

    # Only CLOSE actions (JSONL may contain BUY/SELL/CLOSE entries)
    closed = [t for t in log if t.get("action") in ("CLOSE", "SELL", "close", "sell")]
    # Load PRISM backtest prior (before trade-data calibration)
    prism_prior = _load_prism_prior()
    if verbose and prism_prior:
        hr   = prism_prior.get("prism_hit_rate_60d", 0)
        cnt  = prism_prior.get("prism_count", 0)
        adj  = prism_prior.get("rs_weight_adj", 0)
        print(f"  PRISM backtest prior: hit_rate_60d={hr:.1%} (n={cnt}) → RS adj {adj:+.2f}")

    if not closed:
        if verbose:
            print("  No closed trades yet -- using default weights")
        _write_defaults(prism_prior=prism_prior)
        return {}

    if verbose:
        print(f"  Closed trades: {len(closed)}")

    stats = SignalStats()
    processed = 0

    for trade in closed:
        pnl_pct = float(trade.get("pnl_pct", 0))
        win     = pnl_pct > 0

        # Get active signals at entry
        signal_keys = _extract_signal_keys(trade)

        # Update stats for each active signal
        for key in signal_keys:
            stats.update(key, win, pnl_pct)

        processed += 1

    stats.save()

    # ── Compute new weights from hit rates ──────────────────────────────────
    # Weight = normalized hit rate, scaled to [0, 1.0]
    # Signals with hit_rate > 0.65 get boosted; < 0.40 get penalized
    new_weights = {}
    all_rates   = []

    for key, default_w in DEFAULT_WEIGHTS.items():
        hr = stats.hit_rate(key)
        all_rates.append(hr)

        # Bayesian weight: blend prior (default_w) with observed (hr)
        # As sample size grows, observed wins over prior
        n    = stats.data.get(key, {}).get("total", 4)
        # Trust observed data more as n grows (diminishing returns after 20 trades)
        trust = min(n / 20.0, 1.0)
        blended = (1 - trust) * default_w + trust * hr

        # PRISM backtest prior: adjust RS weights based on backtest evidence
        if prism_prior and key in ("rs_70_90", "rs_90plus"):
            rs_adj = prism_prior.get("rs_weight_adj", 0.0)
            blended = min(1.0, max(0.0, blended + rs_adj))

        # Hard limits: certain signals must stay at 0 or 1
        if key in ("base_4plus", "regime_correction", "td_sell7_9"):
            blended = 0.00   # Never override hard blocks
        if key == "exhaustion_de_risk":
            blended = min(blended, 0.15)   # Cap -- risky regardless

        new_weights[key] = round(blended, 4)

    # Summary stats
    wins    = sum(1 for t in closed if float(t.get("pnl_pct", 0)) > 0)
    losses  = len(closed) - wins
    win_rate = wins / len(closed) * 100 if closed else 0
    avg_win  = (sum(t.get("pnl_pct", 0) for t in closed if t.get("pnl_pct", 0) > 0) / max(wins, 1))
    avg_loss = (sum(t.get("pnl_pct", 0) for t in closed if t.get("pnl_pct", 0) <= 0) / max(losses, 1))
    payoff   = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    result = {
        "calibrated_at":  datetime.now().isoformat(),
        "trades_used":    processed,
        "win_rate":       round(win_rate, 1),
        "avg_win_pct":    round(avg_win, 2),
        "avg_loss_pct":   round(avg_loss, 2),
        "payoff_ratio":   round(payoff, 2),
        "expected_value": round(win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss, 2),
        "weights":        new_weights,
        "signal_stats":   stats.to_dict(),
        "prism_prior":    prism_prior,   # backtest prior used in this calibration
    }

    WEIGHTS_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # Append to calibration log
    _append_calibration_log(result)

    if verbose:
        print(f"  Win rate: {win_rate:.1f}% | Avg win: {avg_win:+.1f}% | Avg loss: {avg_loss:+.1f}% | Payoff: {payoff:.2f}x")
        print(f"  Expected value per trade: {result['expected_value']:+.2f}%")

        # Show top improved signals
        improvements = []
        for key, new_w in new_weights.items():
            old_w = DEFAULT_WEIGHTS.get(key, 0.5)
            delta = new_w - old_w
            if abs(delta) > 0.05:
                improvements.append((key, old_w, new_w, delta))
        improvements.sort(key=lambda x: -abs(x[3]))
        if improvements:
            print(f"\n  Top weight changes from real trade data:")
            for key, old, new, delta in improvements[:5]:
                print(f"    {key}: {old:.2f} -> {new:.2f} ({delta:+.2f})")

        print(f"  Saved -> data/framework_calibrator/signal_weights.json")

    return result


def _write_defaults(prism_prior: dict = None):
    """Write default weights if no trade data exists yet."""
    # Apply PRISM backtest prior even with no trade data
    weights = dict(DEFAULT_WEIGHTS)
    if prism_prior:
        rs_adj = prism_prior.get("rs_weight_adj", 0.0)
        for key in ("rs_70_90", "rs_90plus"):
            weights[key] = round(min(1.0, max(0.0, weights[key] + rs_adj)), 4)

    result = {
        "calibrated_at": datetime.now().isoformat(),
        "trades_used":   0,
        "note":          "Default EMLS v4 weights -- no trade data yet",
        "weights":       weights,
        "signal_stats":  {},
        "prism_prior":   prism_prior or {},
    }
    WEIGHTS_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")


def _append_calibration_log(result: dict):
    """Keep a rolling log of calibration history."""
    history = []
    if CAL_LOG_FILE.exists():
        try:
            history = json.loads(CAL_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Keep last 90 calibration runs
    history.append({
        "at":         result["calibrated_at"],
        "trades":     result["trades_used"],
        "win_rate":   result["win_rate"],
        "avg_win":    result["avg_win_pct"],
        "avg_loss":   result["avg_loss_pct"],
        "payoff":     result["payoff_ratio"],
        "ev":         result["expected_value"],
    })
    CAL_LOG_FILE.write_text(
        json.dumps(history[-90:], indent=2), encoding="utf-8"
    )


def load_weights() -> dict:
    """Load current calibrated weights. Falls back to defaults if not calibrated."""
    if WEIGHTS_FILE.exists():
        try:
            d = json.loads(WEIGHTS_FILE.read_text(encoding="utf-8"))
            return d.get("weights", DEFAULT_WEIGHTS)
        except Exception:
            pass
    return DEFAULT_WEIGHTS


def score_with_live_weights(signals: dict) -> float:
    """
    Score a candidate entry using current calibrated weights.
    signals = {"emls_90plus": True, "base_2": True, "phase_3": True, ...}
    Returns composite score 0-100.
    """
    weights = load_weights()
    active  = [v for k, v in weights.items() if signals.get(k, False)]
    if not active:
        return 0.0
    # Geometric mean of active signal weights * 100
    # Geometric mean penalizes weak signals more than arithmetic
    log_sum = sum(math.log(max(w, 0.01)) for w in active)
    geo_mean = math.exp(log_sum / len(active))
    return round(geo_mean * 100, 1)


if __name__ == "__main__":
    calibrate(verbose=True)
