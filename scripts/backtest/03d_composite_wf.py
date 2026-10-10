# -*- coding: utf-8 -*-
"""
All composite weighting formulas — walk-forward comparison.
Entry criteria FIXED: rs>=80, vol_trend>=0.95, price>MA200, ADTV>=15M, bear=30%

Tests every formula option across WF1/WF2/WF3 + FULL.
Data-first: no opinion, let numbers decide.
"""
import sys, io, importlib.util
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

v2 = load_module(ROOT / "scripts/backtest/03b_backtest_v2.py", "v2")

# ── Load market cap ───────────────────────────────────────────
MKTCAP_F = ROOT / "data/backtest/market_cap.parquet"
if MKTCAP_F.exists():
    mktcap_df = pd.read_parquet(MKTCAP_F)
    mktcap_df["date"] = pd.to_datetime(mktcap_df["date"])
    HAS_MKTCAP = True
    print(f"Loaded market_cap: {len(mktcap_df):,} rows, "
          f"{mktcap_df['market_cap_B'].notna().mean()*100:.0f}% coverage")
else:
    HAS_MKTCAP = False
    print("WARNING: market_cap.parquet not found — skipping market-cap formulas")

dl = v2.BacktestDataloader()
us = v2.CostParams(slippage=0.0003, commission=0.0)

WINDOWS = [
    ("WF1 COVID+recovery", "2020-01-01", "2021-12-31"),
    ("WF2 Rate-hike bear", "2022-01-01", "2023-12-31"),
    ("WF3 AI bull       ", "2024-01-01", "2026-08-20"),
    ("FULL 2017-2026    ", "2017-01-01", "2026-08-20"),
]

# ── Patch BacktestStrategy to support composite formulas ─────
class CompositeStrategy(v2.BacktestStrategy):
    """Extended strategy with market-cap composite weighting."""

    def __init__(self, dataloader, params, formula: str, mktcap: pd.DataFrame = None,
                 beta: float = 0.5, gamma: float = 0.5):
        super().__init__(dataloader, params)
        self.formula  = formula
        self.mktcap   = mktcap   # date × ticker → market_cap_B
        self.beta     = beta
        self.gamma    = gamma

    def _get_mktcap(self, date: pd.Timestamp, tickers: list) -> pd.Series:
        if self.mktcap is None:
            return pd.Series(np.nan, index=tickers)
        sub = self.mktcap[self.mktcap["date"] == date]
        s   = sub.set_index("ticker")["market_cap_B"]
        return s.reindex(tickers)

    def screen(self, date):
        """Override screen: apply composite weighting after base selection."""
        base = super().screen(date)   # runs hard filter + base softmax
        if base.empty:
            return base

        tickers = base["ticker"].tolist()
        rs_vals = base["rs_pct"].values
        mc_vals = self._get_mktcap(date, tickers).values   # market cap $B

        # Fall back to ADTV if market_cap missing
        adtv_vals = base["adtv"].values

        weights = self._compute_weights(rs_vals, mc_vals, adtv_vals)

        # Cap at 18% and scale by regime (already done in parent — redo clean)
        weights = np.minimum(weights, self.params.cap_pct)
        total   = weights.sum()
        if total > 0:
            weights = weights / total

        # Re-apply regime (parent already scaled; undo and redo uniformly)
        spy_row = self.dl.signals[
            (self.dl.signals["ticker"] == "SPY") &
            (self.dl.signals["date"]   == date)
        ]
        spy_above = True
        if not spy_row.empty:
            spy_above = spy_row["close"].iloc[0] > spy_row["ma_200"].iloc[0]
        exposure = self.params.bull_exposure if spy_above else self.params.bear_exposure
        weights  = weights * exposure

        base["weight"] = weights
        return base

    def _compute_weights(self, rs: np.ndarray, mc: np.ndarray,
                         adtv: np.ndarray) -> np.ndarray:
        """All composite formulas in one place."""
        f = self.formula

        # Log-market-cap (use ADTV if mc NaN)
        mc_safe   = np.where(np.isnan(mc), adtv * 250, mc)   # rough proxy
        log_mc    = np.log1p(mc_safe)                          # log(1+B$)
        log_mc_n  = log_mc / (log_mc.max() + 1e-9)            # normalise 0-1
        rs_n      = rs / 100.0                                  # 0-1

        def softmax(x, alpha=1.0):
            x = np.clip(x, None, x.max())   # numerical safety
            e = np.exp(alpha * (x - x.max()))
            return e / e.sum()

        # ── Group 1: Pure RS (BASELINE) ──────────────────────
        if   f == "RS_only":
            return softmax(rs, alpha=0.5)

        # ── Group 2: Additive  softmax(RS + β × log(mc)) ────
        elif f == "add_b03":
            return softmax(rs_n + 0.3 * log_mc_n)
        elif f == "add_b05":
            return softmax(rs_n + 0.5 * log_mc_n)
        elif f == "add_b10":
            return softmax(rs_n + 1.0 * log_mc_n)
        elif f == "add_b20":
            return softmax(rs_n + 2.0 * log_mc_n)

        # ── Group 3: Multiplicative  softmax(RS × mc^γ) ─────
        elif f == "mul_g02":
            return softmax(rs_n * (mc_safe ** 0.2))
        elif f == "mul_g05":
            return softmax(rs_n * (mc_safe ** 0.5))
        elif f == "mul_g10":
            return softmax(rs_n * mc_safe)

        # ── Group 4: Market-cap only  (index-like) ───────────
        elif f == "mc_only":
            w = mc_safe / mc_safe.sum()
            return w

        # ── Group 5: ADTV^power (Codex baseline reference) ──
        elif f == "adtv_15":
            raw = adtv ** 1.5
            return raw / raw.sum()
        elif f == "adtv_20":
            raw = adtv ** 2.0
            return raw / raw.sum()

        # ── Group 6: Equal weight (diversification baseline) ─
        elif f == "equal":
            return np.ones(len(rs)) / len(rs)

        else:
            raise ValueError(f"Unknown formula: {f}")


FORMULAS = [
    # (label, formula_key)
    ("RS_only (α=0.5)      [BASELINE]", "RS_only"),
    ("Additive RS+0.3×log(mc)       ", "add_b03"),
    ("Additive RS+0.5×log(mc)       ", "add_b05"),
    ("Additive RS+1.0×log(mc)       ", "add_b10"),
    ("Additive RS+2.0×log(mc)       ", "add_b20"),
    ("Multiplicative RS×mc^0.2      ", "mul_g02"),
    ("Multiplicative RS×mc^0.5      ", "mul_g05"),
    ("Multiplicative RS×mc^1.0      ", "mul_g10"),
    ("MarketCap only (index-like)   ", "mc_only"),
    ("ADTV^1.5 (Codex ref)          ", "adtv_15"),
    ("ADTV^2.0 (Codex ref)          ", "adtv_20"),
    ("Equal weight                  ", "equal"),
]

FIXED_PARAMS = dict(
    rs_threshold=80, vol_trend_min=0.95,
    bear_exposure=0.30, cap_pct=0.18,
    weighting="softmax", softmax_alpha=0.5   # parent uses this only for base screen
)

mktcap_data = mktcap_df if HAS_MKTCAP else None

# ── Run all formulas × all windows ───────────────────────────
results = {}
total   = len(FORMULAS) * len(WINDOWS)
done    = 0
print(f"\nRunning {len(FORMULAS)} formulas × {len(WINDOWS)} windows = {total} backtests...\n")

for flabel, fkey in FORMULAS:
    results[flabel] = {}
    for wlabel, start, end in WINDOWS:
        p    = v2.StrategyParams(**FIXED_PARAMS, start_date=start, end_date=end)
        strat = CompositeStrategy(dl, p, formula=fkey, mktcap=mktcap_data)
        port  = v2.BacktestPortfolio(1_000_000, us)
        model = v2.BacktestModel(dl, strat, port, p)
        r     = model.run(verbose=False)
        results[flabel][wlabel] = r
        done += 1
        sys.stdout.write(f"\r  {done}/{total} done")
        sys.stdout.flush()

print("\n")

# ── Print excess table ────────────────────────────────────────
W_KEYS = [w[0] for w in WINDOWS]
def pad(s, n): return s[:n].ljust(n)

print("=== EXCESS RETURN vs QQQ ===")
print(f"{'Formula':40s}", end="")
for w in W_KEYS: print(f" | {pad(w.strip(),16)}", end="")
print()
print("-" * (40 + 20 * len(WINDOWS)))
for flabel, wins in results.items():
    print(f"{flabel:40s}", end="")
    for wk in W_KEYS:
        r = wins[wk]
        e = r["excess"] * 100
        print(f" | {e:+7.1f}%        ", end="")
    print()

print()
print("=== MAX DRAWDOWN ===")
print(f"{'Formula':40s}", end="")
for w in W_KEYS: print(f" | {pad(w.strip(),16)}", end="")
print()
print("-" * (40 + 20 * len(WINDOWS)))
for flabel, wins in results.items():
    print(f"{flabel:40s}", end="")
    for wk in W_KEYS:
        r = wins[wk]
        print(f" | {r['port_mdd']*100:7.1f}%        ", end="")
    print()

print()
print("=== SHARPE ===")
print(f"{'Formula':40s}", end="")
for w in W_KEYS: print(f" | {pad(w.strip(),16)}", end="")
print()
print("-" * (40 + 20 * len(WINDOWS)))
for flabel, wins in results.items():
    print(f"{flabel:40s}", end="")
    for wk in W_KEYS:
        r = wins[wk]
        print(f" | {r['sharpe']:7.2f}         ", end="")
    print()

# ── Composite score ───────────────────────────────────────────
print()
print("=== COMPOSITE SCORE = WF1×1 + WF2×2.5 + WF3×1 + FULL×1.5 ===")
print(f"{'Formula':40s} | Score  | WF2    | FULL   | DD     | Sharpe")
print("-" * 90)

scored = []
for flabel, wins in results.items():
    wf1  = wins["WF1 COVID+recovery"]["excess"] * 100
    wf2  = wins["WF2 Rate-hike bear"]["excess"] * 100
    wf3  = wins["WF3 AI bull       "]["excess"] * 100
    full = wins["FULL 2017-2026    "]["excess"] * 100
    dd   = wins["FULL 2017-2026    "]["port_mdd"] * 100
    sh   = wins["FULL 2017-2026    "]["sharpe"]
    score = wf1*1.0 + wf2*2.5 + wf3*1.0 + full*1.5
    scored.append((score, flabel, wf2, full, dd, sh))

scored.sort(reverse=True)
for i, (score, flabel, wf2, full, dd, sh) in enumerate(scored):
    marker = " <-- WINNER" if i == 0 else (" <-- 2nd" if i == 1 else "")
    print(f"{flabel:40s} | {score:+6.1f} | {wf2:+5.1f}% | {full:+5.1f}% | {dd:6.1f}% | {sh:.2f}{marker}")

winner = scored[0]
print(f"\nWINNER: {winner[1].strip()}")
print(f"  Score={winner[0]:+.1f}  WF2={winner[2]:+.1f}%  FULL excess={winner[3]:+.1f}%  "
      f"DD={winner[4]:.1f}%  Sharpe={winner[5]:.2f}")
print("\nConclusion saved above — lock this formula as default weighting.")
