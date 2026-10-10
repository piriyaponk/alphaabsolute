# -*- coding: utf-8 -*-
"""
Backtest Engine v2 — Template Architecture
==========================================
Follows template's 5-layer pattern:
  Dataloader → Strategy → Portfolio → Model → Evaluator

Key improvements over v1:
  1. Proper Portfolio class: tracks daily NAV, positions, cash
  2. Per-trade cost: slippage on entry + commission (not approximate)
  3. Softmax weighting option (vs ADTV^power)
  4. Clean Strategy/Portfolio separation
  5. Transaction log for post-mortem analysis

Criteria: Our RS+vol_trend + Codex vol_contraction+base_tight (optional)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from copy import deepcopy

ROOT        = Path(__file__).resolve().parents[2]
BT_DIR      = ROOT / "data" / "backtest"
SIGNAL_FILE = BT_DIR / "signals.parquet"


# ─────────────────────────────────────────────
# PARAMS
# ─────────────────────────────────────────────

@dataclass
class StrategyParams:
    # --- Entry criteria (ALL must pass) ---
    rs_threshold:     float = 80.0   # RS composite pct min (our best: 80)
    vol_trend_min:    float = 0.95   # 20d/60d avg volume ratio (our edge)
    vol_contract_max: float = 1.10   # vol_20/vol_60 <= this (legacy; overridden by regime-conditional below)
    base_tight_max:   float = 1.10   # range_20/range_65 <= this
    adtv_min_m:       float = 15.0   # min ADTV $M
    # --- Regime-conditional filters (Fix 2017: relax filters in bull) ---
    # -1 = disabled (use the non-conditional param instead)
    # BULL: leaders have expanding vol+price — relax contraction filters
    # BEAR: contraction = stock holding up — enforce filters
    vol_contract_bull: float = -1.0
    vol_contract_bear: float = -1.0
    vol_trend_bull:    float = -1.0  # relax vol_trend in bull (e.g. 0.85)
    vol_trend_bear:    float = -1.0  # stricter vol_trend in bear (e.g. 0.95)
    rs_bull:           float = -1.0
    rs_bear:           float = -1.0
    # --- DD circuit breaker ---
    dd_circuit_pct:   float = 0.0
    dd_circuit_exp:   float = 0.50
    # --- Optional v2 factors ---
    sharpe_60d_min:   float = -99.0
    pct_from_52w_high_min: float = -99.0
    # --- Portfolio construction ---
    weighting:        str   = "softmax"
    softmax_alpha:    float = 1.0
    adtv_power:       float = 1.5
    cap_pct:          float = 0.18
    # --- Regime overlay ---
    bear_exposure:    float = 0.30
    bull_exposure:    float = 1.00
    # --- Meta ---
    rebal_freq:  str  = "ME"
    benchmark:   str  = "QQQ"
    start_date:  str  = "2017-01-01"
    end_date:    str  = "2026-08-20"
    exclude_tickers: list = field(default_factory=lambda: ["QQQ", "SPY", "IWM"])


@dataclass
class CostParams:
    """
    Template-style cost model:
    - slippage: applied on entry (buy) and exit (sell) separately
    - commission: per-trade percentage
    Total round-trip = 2 × slippage + 2 × commission
    US retail (Fidelity/Schwab): commission=$0, slippage=0.03-0.05%
    Thai market (SET100): commission=0.30%, slippage=0.50%
    """
    slippage:    float = 0.0007   # one-way: 0.07% (realistic S&P500 bid-ask)
    commission:  float = 0.0000   # one-way: $0 at Fidelity/Schwab/IBKR
    # Total round-trip = 2*(slippage+commission) = 0.14% for US retail
    # Validation audit (2026-08-30): raised from 0.03% to 0.07% one-way
    # Rationale: S&P500 large-cap bid-ask typically 5-10 bps; 7 bps is mid-point

    @property
    def round_trip(self):
        return 2 * (self.slippage + self.commission)


# ─────────────────────────────────────────────
# LAYER 1: DATALOADER
# ─────────────────────────────────────────────

class BacktestDataloader:
    """Load and preprocess signals.parquet for backtesting."""

    def __init__(self, signal_file=SIGNAL_FILE):
        self.signals = pd.read_parquet(signal_file)
        self.signals["date"] = pd.to_datetime(self.signals["date"])
        self.signals = self.signals.sort_values(["ticker", "date"])

        # Wide price table for return calculation
        self.close_wide = self.signals.pivot_table(
            index="date", columns="ticker", values="close"
        )
        self.trading_days = self.close_wide.index.sort_values()

    def get_signals_at(self, date: pd.Timestamp, params: StrategyParams) -> pd.DataFrame:
        """Return signal row for all tickers at a given date."""
        mask = (self.signals["date"] == date) & \
               (~self.signals["ticker"].isin(params.exclude_tickers))
        return self.signals[mask].copy()

    def get_benchmark_close(self, benchmark: str) -> pd.Series:
        bench = self.signals[self.signals["ticker"] == benchmark].copy()
        bench = bench.set_index("date")["close"]
        return bench

    def get_rebal_dates(self, params: StrategyParams) -> list:
        dates = pd.date_range(params.start_date, params.end_date, freq=params.rebal_freq)
        result = []
        for d in dates:
            candidates = self.trading_days[self.trading_days <= d]
            if len(candidates) > 0:
                result.append(candidates[-1])
        return sorted(set(result))


# ─────────────────────────────────────────────
# LAYER 2: STRATEGY
# ─────────────────────────────────────────────

class BacktestStrategy:
    """
    Signal generation + position sizing.
    Applies our screening criteria + Codex criteria + softmax weighting.
    """

    def __init__(self, dataloader: BacktestDataloader, params: StrategyParams):
        self.dl = dataloader
        self.params = params

    def screen(self, date: pd.Timestamp) -> pd.DataFrame:
        """
        Apply all screening criteria at a given date.
        Returns DataFrame with ticker + weight columns.
        """
        day_sig = self.dl.get_signals_at(date, self.params)
        if day_sig.empty:
            return pd.DataFrame(columns=["ticker", "weight"])

        # ── Regime: SPY vs MA200 (needed for conditional filters) ──
        spy_row = self.dl.signals[
            (self.dl.signals["ticker"] == "SPY") &
            (self.dl.signals["date"]   == date)
        ]
        if not spy_row.empty:
            spy_above = spy_row["close"].iloc[0] > spy_row["ma_200"].iloc[0]
        else:
            spy_above = True

        # ── Regime-conditional thresholds ───────────────────
        # If vol_contract_bull/bear are set (> 0), use regime-conditional mode.
        # BULL: relax vol_contraction (breakout stocks have expanding vol)
        # BEAR: enforce vol_contraction (contracting = stock holding up)
        vc_thresh = (self.params.vol_contract_bull if spy_above else self.params.vol_contract_bear
                    ) if (self.params.vol_contract_bull > 0 and self.params.vol_contract_bear > 0
                    ) else self.params.vol_contract_max

        vt_thresh = (self.params.vol_trend_bull if spy_above else self.params.vol_trend_bear
                    ) if (self.params.vol_trend_bull > 0 and self.params.vol_trend_bear > 0
                    ) else self.params.vol_trend_min

        rs_thresh = (self.params.rs_bull if spy_above else self.params.rs_bear
                    ) if (self.params.rs_bull > 0 and self.params.rs_bear > 0
                    ) else self.params.rs_threshold

        # ── Entry criteria ──────────────────────────────────
        mask = (
            (day_sig["rs_pct"]          >= rs_thresh) &
            (day_sig["vol_trend"]       >= vt_thresh) &
            (day_sig["vol_contraction"] <= vc_thresh) &
            (day_sig["base_tight"]      <= self.params.base_tight_max) &
            (day_sig["adtv_63m"]        >= self.params.adtv_min_m) &
            (day_sig["close"]           >  day_sig["ma_200"])
        )
        if self.params.sharpe_60d_min > -90 and "sharpe_60d" in day_sig.columns:
            mask &= day_sig["sharpe_60d"] >= self.params.sharpe_60d_min
        if self.params.pct_from_52w_high_min > -90:
            mask &= day_sig["pct_from_52w_high"] >= self.params.pct_from_52w_high_min

        sel = day_sig[mask].copy()
        if sel.empty:
            return pd.DataFrame(columns=["ticker", "weight"])

        exposure = self.params.bull_exposure if spy_above else self.params.bear_exposure

        # ── Weighting ───────────────────────────────────────
        if self.params.weighting == "softmax":
            scores  = sel["rs_pct"].values
            alpha   = self.params.softmax_alpha
            shifted = alpha * (scores - scores.max())
            exp_s   = np.exp(shifted)
            weights = exp_s / exp_s.sum()
        elif self.params.weighting == "equal":
            n = len(sel)
            weights = np.ones(n) / n
        else:
            # adtv_power
            raw = sel["adtv_63m"] ** self.params.adtv_power
            weights = (raw / raw.sum()).values

        # ── Cap and normalize ───────────────────────────────
        weights = np.minimum(weights, self.params.cap_pct)
        total_w = weights.sum()
        if total_w > 0:
            weights = weights / total_w * exposure  # scale by regime exposure

        result = pd.DataFrame({
            "ticker": sel["ticker"].values,
            "weight": weights,
            "rs_pct": sel["rs_pct"].values,
            "adtv":   sel["adtv_63m"].values,
        })
        return result


# ─────────────────────────────────────────────
# LAYER 3: PORTFOLIO
# ─────────────────────────────────────────────

class BacktestPortfolio:
    """
    Template-style portfolio simulator.
    Tracks daily NAV, positions, cash.
    Applies per-trade slippage + commission (not approximate).
    """

    def __init__(self, initial_cash: float = 1_000_000, costs: CostParams = None):
        self.initial_cash = initial_cash
        self.costs        = costs or CostParams()
        self.cash         = initial_cash
        self.positions    = {}   # ticker → {shares, cost_basis, entry_price}
        self.nav_history  = []   # list of (date, nav, cash, equity)
        self.transactions = []   # full trade log
        self.monthly_returns = []

    @property
    def equity_value(self):
        return sum(v["shares"] * v["current_price"] for v in self.positions.values())

    @property
    def nav(self):
        return self.cash + self.equity_value

    def mark_to_market(self, date: pd.Timestamp, price_row: dict):
        """Update current prices of all positions."""
        for ticker in list(self.positions.keys()):
            if ticker in price_row and not np.isnan(price_row[ticker]):
                self.positions[ticker]["current_price"] = price_row[ticker]

    def rebalance(self, date: pd.Timestamp, target_weights: dict, price_row: dict):
        """
        Rebalance to target weights.
        target_weights: {ticker: weight_fraction}
        price_row: {ticker: price}
        """
        nav_before = self.nav
        target_values = {t: nav_before * w for t, w in target_weights.items()}

        # Current values
        current_values = {
            t: v["shares"] * price_row.get(t, v["current_price"])
            for t, v in self.positions.items()
        }

        # Determine sells (positions not in target OR reducing)
        all_tickers = set(current_values) | set(target_values)
        sells = {}
        buys  = {}

        for ticker in all_tickers:
            curr_val  = current_values.get(ticker, 0)
            tgt_val   = target_values.get(ticker, 0)
            price     = price_row.get(ticker, np.nan)
            if np.isnan(price) or price <= 0:
                continue

            delta = tgt_val - curr_val
            if delta < -100:  # sell threshold ($100 min)
                sells[ticker] = abs(delta)
            elif delta > 100:  # buy threshold ($100 min)
                buys[ticker]  = delta

        # Execute sells first (free up cash)
        for ticker, sell_val in sells.items():
            price = price_row[ticker]
            sell_price = price * (1 - self.costs.slippage)  # sell below market
            shares_to_sell = min(sell_val / sell_price,
                                 self.positions.get(ticker, {}).get("shares", 0))
            if shares_to_sell <= 0:
                continue
            proceeds = shares_to_sell * sell_price * (1 - self.costs.commission)
            self.cash += proceeds

            existing = self.positions.get(ticker, {}).get("shares", 0)
            remaining = existing - shares_to_sell
            if remaining < 0.001:
                self.positions.pop(ticker, None)
            else:
                self.positions[ticker]["shares"] = remaining
                self.positions[ticker]["current_price"] = price

            self.transactions.append({
                "date": date, "ticker": ticker, "action": "SELL",
                "shares": shares_to_sell, "price": sell_price,
                "value": proceeds
            })

        # Execute buys
        for ticker, buy_val in buys.items():
            if self.cash < 100:
                break
            price = price_row.get(ticker, np.nan)
            if np.isnan(price) or price <= 0:
                continue
            buy_price = price * (1 + self.costs.slippage)  # buy above market
            actual_val = min(buy_val, self.cash)
            actual_val *= (1 - self.costs.commission)
            shares_to_buy = actual_val / buy_price
            cost_total    = shares_to_buy * buy_price * (1 + self.costs.commission)
            if cost_total > self.cash:
                shares_to_buy = self.cash * (1 - self.costs.commission) / buy_price
                cost_total    = shares_to_buy * buy_price * (1 + self.costs.commission)

            self.cash -= cost_total
            if ticker in self.positions:
                old_shares = self.positions[ticker]["shares"]
                old_cost   = self.positions[ticker].get("cost_basis", buy_price)
                new_shares = old_shares + shares_to_buy
                self.positions[ticker]["shares"]        = new_shares
                self.positions[ticker]["cost_basis"]    = (old_shares*old_cost + shares_to_buy*buy_price) / new_shares
                self.positions[ticker]["current_price"] = price
            else:
                self.positions[ticker] = {
                    "shares": shares_to_buy,
                    "cost_basis": buy_price,
                    "current_price": price,
                    "entry_date": date,
                }

            self.transactions.append({
                "date": date, "ticker": ticker, "action": "BUY",
                "shares": shares_to_buy, "price": buy_price,
                "value": cost_total
            })

    def record_nav(self, date: pd.Timestamp):
        nav = self.nav
        self.nav_history.append({
            "date":   date,
            "nav":    nav,
            "cash":   self.cash,
            "equity": self.equity_value,
        })

    def monthly_return_from_nav(self):
        """Build monthly return series from nav_history."""
        nav_df = pd.DataFrame(self.nav_history).set_index("date")["nav"]
        return nav_df.resample("ME").last().pct_change().dropna()


# ─────────────────────────────────────────────
# LAYER 4: MODEL (Coordinator)
# ─────────────────────────────────────────────

class BacktestModel:
    """
    Coordinates Dataloader + Strategy + Portfolio.
    run() → equity curve + performance metrics.
    """

    def __init__(self, dataloader: BacktestDataloader,
                 strategy: BacktestStrategy,
                 portfolio: BacktestPortfolio,
                 params: StrategyParams):
        self.dl        = dataloader
        self.strategy  = strategy
        self.portfolio = portfolio
        self.params    = params

    def run(self, verbose=True) -> dict:
        rebal_dates   = self.dl.get_rebal_dates(self.params)
        bench_close   = self.dl.get_benchmark_close(self.params.benchmark)
        trading_days  = self.dl.trading_days

        period_days = trading_days[
            (trading_days >= pd.Timestamp(self.params.start_date)) &
            (trading_days <= pd.Timestamp(self.params.end_date))
        ]

        # Track holdings count per rebalance
        holdings_log  = []
        prev_nav      = self.portfolio.initial_cash
        bench_start   = bench_close.loc[bench_close.index >= pd.Timestamp(self.params.start_date)]
        if len(bench_start) == 0:
            return {}
        bench_nav     = [1.0]
        bench_dates   = [period_days[0]]
        bench_prev    = bench_close.loc[bench_start.index[0]]

        prev_port_nav = self.portfolio.initial_cash

        for i, day in enumerate(period_days):
            # Get today's prices
            price_row = {}
            if day in self.dl.close_wide.index:
                row = self.dl.close_wide.loc[day]
                price_row = row.dropna().to_dict()

            # Mark to market
            self.portfolio.mark_to_market(day, price_row)

            # Rebalance on rebal dates
            if day in rebal_dates:
                target_df = self.strategy.screen(day)
                if not target_df.empty:
                    target_weights = dict(zip(target_df["ticker"], target_df["weight"]))
                else:
                    target_weights = {}

                # ── DD circuit breaker ──────────────────────
                # If portfolio has fallen > dd_circuit_pct from its all-time peak,
                # scale down all weights by dd_circuit_exp.
                if self.params.dd_circuit_pct > 0 and self.portfolio.nav_history:
                    peak_nav = max(h["nav"] for h in self.portfolio.nav_history)
                    curr_dd  = (self.portfolio.nav - peak_nav) / peak_nav
                    if curr_dd < -self.params.dd_circuit_pct:
                        scale = self.params.dd_circuit_exp
                        target_weights = {t: w * scale for t, w in target_weights.items()}

                holdings_log.append(len(target_weights))
                self.portfolio.rebalance(day, target_weights, price_row)

            self.portfolio.record_nav(day)

            # Benchmark NAV
            if day in bench_close.index and bench_close[day] > 0:
                bench_nav.append(bench_nav[-1] * bench_close[day] / bench_prev)
                bench_prev = bench_close[day]
                bench_dates.append(day)

        # ── Performance metrics ──────────────────────────────
        nav_df  = pd.DataFrame(self.portfolio.nav_history).set_index("date")["nav"]
        # Build benchmark as daily series (reindex over all trading days)
        bench_raw = pd.Series(bench_nav, index=bench_dates)
        bench_raw = bench_raw[~bench_raw.index.duplicated(keep="last")]
        bench_s   = bench_raw.reindex(period_days, method="ffill").dropna()
        # Align
        common  = nav_df.index.intersection(bench_s.index)
        nav_s   = nav_df.loc[common]
        bench_s = bench_s.loc[common]

        years = (common[-1] - common[0]).days / 365.25

        def cagr(s):
            return (s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1 if years > 0 else 0

        def max_dd(s):
            peak = s.cummax()
            return ((s - peak) / peak).min()

        def sharpe(s):
            daily_ret = s.pct_change().dropna()
            if daily_ret.std() == 0:
                return 0
            return daily_ret.mean() / daily_ret.std() * np.sqrt(252)

        port_cagr   = cagr(nav_s)
        bench_cagr  = cagr(bench_s)
        port_mdd    = max_dd(nav_s)
        bench_mdd   = max_dd(bench_s)
        port_sharpe = sharpe(nav_s)
        port_calmar = port_cagr / abs(port_mdd) if port_mdd != 0 else 0
        avg_holdings = np.mean(holdings_log) if holdings_log else 0

        # Turnover estimate from transactions
        txn_df = pd.DataFrame(self.portfolio.transactions)
        if not txn_df.empty:
            monthly_trades = txn_df.groupby(txn_df["date"].dt.to_period("M"))["value"].sum()
            avg_monthly_vol = monthly_trades.mean()
            avg_nav = nav_s.mean()
            avg_turnover = avg_monthly_vol / avg_nav if avg_nav > 0 else 0
        else:
            avg_turnover = 0

        if verbose:
            print(f"\n{'='*60}")
            print(f"  BACKTEST v2  {self.params.start_date} to {self.params.end_date}")
            print(f"{'='*60}")
            print(f"  Portfolio CAGR   : {port_cagr*100:.2f}%")
            print(f"  {self.params.benchmark} CAGR        : {bench_cagr*100:.2f}%")
            print(f"  Excess           : {(port_cagr-bench_cagr)*100:+.2f}%")
            print(f"  Max DD           : {port_mdd*100:.2f}%  (vs {self.params.benchmark} {bench_mdd*100:.2f}%)")
            print(f"  Sharpe (daily)   : {port_sharpe:.2f}")
            print(f"  Calmar           : {port_calmar:.2f}")
            print(f"  Avg Holdings     : {avg_holdings:.1f} stocks")
            print(f"  Avg Turnover     : {avg_turnover:.1%}/mo (est.)")
            print(f"  Final NAV        : ${nav_s.iloc[-1]:,.0f}")
            print(f"  Round-trip cost  : {self.portfolio.costs.round_trip*100:.3f}%")
            print(f"{'='*60}")

        # ── Year-by-year breakdown ──────────────────────────────
        yearly = {}
        for yr, grp in nav_s.groupby(nav_s.index.year):
            if len(grp) < 20:
                continue
            b_grp = bench_s.reindex(grp.index, method="ffill").dropna()
            b_grp = b_grp.loc[b_grp.index.intersection(grp.index)]
            g_aln = grp.loc[b_grp.index]
            yr_excess = (g_aln.iloc[-1]/g_aln.iloc[0]) - (b_grp.iloc[-1]/b_grp.iloc[0])
            yr_dd     = max_dd(g_aln)
            yearly[yr] = {"excess": float(yr_excess), "dd": float(yr_dd)}

        worst_yr_dd = min((v["dd"] for v in yearly.values()), default=0)

        return {
            "port_cagr":    port_cagr,
            "bench_cagr":   bench_cagr,
            "excess":       port_cagr - bench_cagr,
            "port_mdd":     port_mdd,
            "bench_mdd":    bench_mdd,
            "sharpe":       port_sharpe,
            "calmar":       port_calmar,
            "avg_holdings": avg_holdings,
            "avg_turnover": avg_turnover,
            "yearly":       yearly,
            "worst_yr_dd":  worst_yr_dd,
            "nav_series":   nav_s,
            "bench_series": bench_s,
            "transactions": self.portfolio.transactions,
        }


# ─────────────────────────────────────────────
# CONVENIENCE FUNCTION
# ─────────────────────────────────────────────

def run_backtest_v2(strategy_params: StrategyParams,
                    cost_params: CostParams = None,
                    signals: pd.DataFrame = None,
                    verbose: bool = True) -> dict:
    """
    One-line backtest using v2 architecture.
    Equivalent to template's Model.evaluate() call.
    """
    if signals is not None:
        import tempfile, os
        tmp = Path(tempfile.mktemp(suffix=".parquet"))
        signals.to_parquet(tmp, index=False)
        dl = BacktestDataloader(signal_file=tmp)
        os.unlink(tmp)
    else:
        dl = BacktestDataloader()

    strategy  = BacktestStrategy(dl, strategy_params)
    costs     = cost_params or CostParams()
    portfolio = BacktestPortfolio(initial_cash=1_000_000, costs=costs)
    model     = BacktestModel(dl, strategy, portfolio, strategy_params)
    return model.run(verbose=verbose)


# ─────────────────────────────────────────────
# MAIN: Baseline test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    print("Loading signals...")
    dl = BacktestDataloader()
    print(f"  {len(dl.signals):,} rows, {dl.signals['ticker'].nunique()} tickers")

    # US retail costs: $0 commission, 0.03% slippage one-way
    us_costs = CostParams(slippage=0.0003, commission=0.0)

    print("\n--- SYSTEM v1.0: rs=80 + vol_trend=0.95 (our best) ---")
    p1 = StrategyParams(
        rs_threshold=80, vol_trend_min=0.95,
        weighting="softmax", softmax_alpha=1.0,
        cap_pct=0.18, bear_exposure=0.30,
    )
    strategy  = BacktestStrategy(dl, p1)
    portfolio = BacktestPortfolio(1_000_000, us_costs)
    model     = BacktestModel(dl, strategy, portfolio, p1)
    r1        = model.run()

    print("\n--- CODEX replica: rs=70 + vol_contraction + base_tight ---")
    p2 = StrategyParams(
        rs_threshold=70, vol_trend_min=0.0,
        vol_contract_max=1.0, base_tight_max=1.0,
        weighting="adtv_power", adtv_power=2.0,
        cap_pct=0.18, bear_exposure=0.40,
    )
    strategy2  = BacktestStrategy(dl, p2)
    portfolio2 = BacktestPortfolio(1_000_000, us_costs)
    model2     = BacktestModel(dl, strategy2, portfolio2, p2)
    r2         = model2.run()

    print("\n--- COMBINED: rs=80 + vol_trend + vol_contract + base_tight ---")
    p3 = StrategyParams(
        rs_threshold=80, vol_trend_min=0.95,
        vol_contract_max=1.0, base_tight_max=0.95,
        weighting="softmax", softmax_alpha=1.0,
        cap_pct=0.18, bear_exposure=0.30,
    )
    strategy3  = BacktestStrategy(dl, p3)
    portfolio3 = BacktestPortfolio(1_000_000, us_costs)
    model3     = BacktestModel(dl, strategy3, portfolio3, p3)
    r3         = model3.run()

    print("\n=== COMPARISON ===")
    print(f"{'Config':40s} | Excess | DD      | Sharpe | Calmar | Holds")
    print("-" * 90)
    for name, r in [("v1.0 rs80+vt0.95 (softmax)", r1),
                    ("Codex replica (adtv_power)", r2),
                    ("Combined rs80+vt+vc+bt", r3)]:
        print(f"{name:40s} | {r['excess']*100:+5.1f}% | {r['port_mdd']*100:6.1f}% | "
              f"{r['sharpe']:5.2f} | {r['calmar']:5.2f} | {r['avg_holdings']:5.0f}")
