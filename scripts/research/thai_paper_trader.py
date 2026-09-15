"""
AlphaModel-TH — Daily Paper Trader
====================================
Runs COMBINED config on thai_ohlcv.db, tracks paper portfolio,
sends Telegram with [TH] prefix (separate from System 4 / -US).

Schedule: run daily after SET close (~16:30 ICT / 09:30 UTC)
  python -X utf8 scripts/research/thai_paper_trader.py

RESEARCH ONLY — AlphaModel-US (System 4) unchanged.
"""

import sys, os, json, sqlite3, warnings, urllib.request, urllib.parse
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import date, datetime

warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).resolve().parents[2]
DB_PATH    = str(ROOT / "data" / "research" / "thai_ohlcv.db")
STATE_PATH = ROOT / "data" / "research" / "thai_paper_portfolio.json"
LOG_PATH   = ROOT / "data" / "research" / "thai_paper_log.json"
STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── Config ─────────────────────────────────────────────────────────────────
COMBINED = dict(rs_days=21, top_n=10, rebal_days=5, regime_ma=50, rs_type="vol_weight")
STARTING_NAV   = 1_000_000.0   # ฿1,000,000
SET_INDEX      = "^SET.BK"
TCOST_BUY      = 0.0015
TCOST_SELL     = 0.0015
ADTV_MIN_THB   = 20_000_000
MIN_PRICE_THB  = 1.0
DAILY_RET_CAP  = 0.25
RF_ANNUAL      = 0.025

# ── Telegram ───────────────────────────────────────────────────────────────
def _tg(text: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print(f"[TG-SKIP] {text[:80]}")
        return
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception:
        # retry without parse_mode
        try:
            data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
        except Exception as e:
            print(f"[TG-FAIL] {e}")


# ── Data loading ───────────────────────────────────────────────────────────
def load_prices():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT ticker, date, close, volume FROM thai_ohlcv WHERE close IS NOT NULL",
        conn, parse_dates=["date"]
    )
    conn.close()
    closes  = df.pivot(index="date", columns="ticker", values="close").sort_index().ffill(limit=5)
    vol_raw = df.pivot(index="date", columns="ticker", values="volume").sort_index().ffill(limit=5)
    vol_raw.columns = [c + "_vol" for c in vol_raw.columns]
    return closes, vol_raw


def eligible_universe(prices, volumes, today, lookback=126):
    hard_exclude = {SET_INDEX, "^SET.BK"}
    eligible = set()
    idx_pos = prices.index.get_loc(today)
    lb_pos  = max(0, idx_pos - lookback)
    window  = prices.index[lb_pos: idx_pos + 1]
    for tkr in [c for c in prices.columns if c not in hard_exclude]:
        px = prices.loc[today, tkr]
        if pd.isna(px) or px < MIN_PRICE_THB:
            continue
        vol_col = tkr + "_vol"
        if vol_col in volumes.columns:
            px_w  = prices.loc[window, tkr].ffill()
            vol_w = volumes.loc[window, vol_col].fillna(0)
            if (px_w * vol_w).mean() < ADTV_MIN_THB:
                continue
        eligible.add(tkr)
    return eligible


# ── State I/O ─────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {
        "nav":          STARTING_NAV,
        "set_nav":      STARTING_NAV,
        "holdings":     [],
        "entry_prices": {},
        "last_rebal":   0,
        "inception":    str(date.today()),
        "last_update":  None,
        "trade_count":  0,
        "daily_log":    [],
    }


def save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def append_log(entry: dict):
    log = []
    if LOG_PATH.exists():
        log = json.loads(LOG_PATH.read_text(encoding="utf-8"))
    log.append(entry)
    LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Core signal (same COMBINED logic, single-step) ────────────────────────
def compute_signal(prices, volumes, today, state):
    rs_days    = COMBINED["rs_days"]
    top_n      = COMBINED["top_n"]
    rebal_days = COMBINED["rebal_days"]
    regime_ma  = COMBINED["regime_ma"]
    rs_type    = COMBINED["rs_type"]

    if SET_INDEX not in prices.columns:
        return state["holdings"], False, []

    trading_dates = prices.index.tolist()
    if today not in prices.index:
        return state["holdings"], False, []

    i = trading_dates.index(today)

    # Regime check
    set_idx   = prices[SET_INDEX].dropna()
    set_ma    = set_idx.rolling(regime_ma, min_periods=int(regime_ma * 0.75)).mean()
    set_today = set_idx.get(today, np.nan)
    ma_today  = set_ma.get(today, np.nan)
    bull      = bool(pd.notna(set_today) and pd.notna(ma_today) and set_today > ma_today)

    if not bull:
        return [], True, []   # exit all

    last_rebal = state.get("last_rebal", 0)
    holdings   = state.get("holdings", [])

    eligible = eligible_universe(prices, volumes, today)
    t_lb     = trading_dates[max(0, i - rs_days)]
    px_now   = prices.loc[today, list(eligible)].dropna()
    px_lb    = prices.loc[t_lb,  list(eligible)].dropna()
    common   = px_now.index.intersection(px_lb.index)

    if len(common) < 5:
        return holdings, False, []

    rs_raw = px_now[common] / px_lb[common] - 1

    if rs_type == "vol_weight":
        c_clean = [c for c in common if c + "_vol" in volumes.columns]
        if len(c_clean) >= 5:
            avg_vol   = volumes[[c + "_vol" for c in c_clean]].loc[:today].tail(21).mean()
            vol_ratio = (avg_vol / avg_vol.mean()).clip(0.1, 5.0)
            vol_ratio.index = [x.replace("_vol", "") for x in vol_ratio.index]
            rs = rs_raw.copy()
            vw = rs_raw.index.intersection(vol_ratio.index)
            rs[vw] = rs_raw[vw] * vol_ratio[vw]
        else:
            rs = rs_raw
    else:
        rs = rs_raw

    top_n_list = list(rs.nlargest(top_n).index)
    top30      = set(rs.nlargest(30).index)

    # Early drop
    dropped = [h for h in holdings if h not in top30]
    new_buys = []
    if dropped and (i - last_rebal) >= 3:
        for d in dropped:
            holdings = [h for h in holdings if h != d]
        for t in top_n_list:
            if t not in holdings and len(holdings) < top_n:
                holdings.append(t)
                new_buys.append(t)

    # Scheduled rebalance
    rebal_happened = False
    if (i - last_rebal) >= rebal_days:
        old = set(holdings)
        new = set(top_n_list)
        sells = old - new
        buys  = new - old
        if sells or buys:
            for s in sells:
                holdings = [h for h in holdings if h != s]
            for b in buys:
                if b not in holdings and len(holdings) < top_n:
                    holdings.append(b)
                    if b not in new_buys:
                        new_buys.append(b)
            rebal_happened = True
        state["last_rebal"] = i

    return holdings, rebal_happened, new_buys


# ── Daily update ──────────────────────────────────────────────────────────
def run_daily():
    today_str = str(date.today())
    print(f"\n{'='*60}")
    print(f"AlphaAbsolute-TH Paper Trader  |  {today_str}")
    print(f"{'='*60}")

    prices, volumes = load_prices()
    state = load_state()

    # Find last available trading day in DB
    trading_dates = prices.index.tolist()
    if len(trading_dates) == 0:
        print("[ERROR] No data in DB")
        return

    today_ts = pd.Timestamp(today_str)
    # Use latest available date in DB
    avail_dates = [d for d in trading_dates if d <= today_ts]
    if not avail_dates:
        print("[ERROR] No trading dates available")
        return
    today = avail_dates[-1]

    # Staleness check — warn if DB is behind today's calendar date
    # (skips weekends/holidays: only warn on weekdays)
    import datetime as _dt
    cal_today = _dt.date.today()
    db_date   = today.date()
    is_weekday = cal_today.weekday() < 5  # Mon-Fri
    if is_weekday and db_date < cal_today:
        lag = (cal_today - db_date).days
        print(f"[TH] DB ล่าสุด: {db_date} (วันนี้: {cal_today}, lag={lag}d)")
        # Continue running — use latest available data, don't abort

    if state.get("last_update") == today_str:
        print(f"[SKIP] Already updated for {today_str}")
        return

    prev_date = avail_dates[-2] if len(avail_dates) >= 2 else None

    prev_holdings = state["holdings"][:]
    prev_nav      = state["nav"]

    # ── P&L from existing holdings ────────────────────────────────────────
    port_ret  = 0.0
    pos_pnl   = {}
    if prev_holdings and prev_date is not None:
        h_rets = []
        for h in prev_holdings:
            p0 = prices.loc[prev_date, h] if h in prices.columns else np.nan
            p1 = prices.loc[today,     h] if h in prices.columns else np.nan
            if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                r = max(-DAILY_RET_CAP, min(DAILY_RET_CAP, p1 / p0 - 1))
                h_rets.append(r)
                pos_pnl[h] = round(r * 100, 2)
        port_ret = np.mean(h_rets) if h_rets else 0.0

    state["nav"] *= (1 + port_ret)

    # SET benchmark
    set_idx  = prices[SET_INDEX].dropna()
    set_p0   = set_idx.get(prev_date) if prev_date else None
    set_p1   = set_idx.get(today)
    set_ret  = (set_p1 / set_p0 - 1) if (set_p0 and set_p1 and set_p0 > 0) else 0.0
    state["set_nav"] = state.get("set_nav", STARTING_NAV) * (1 + set_ret)

    # ── Signal for today ──────────────────────────────────────────────────
    new_holdings, rebal, new_buys = compute_signal(prices, volumes, today, state)

    # Apply transaction costs
    old_set = set(prev_holdings)
    new_set = set(new_holdings)
    sells   = old_set - new_set
    buys    = new_set - old_set
    top_n   = COMBINED["top_n"]

    if not new_holdings and prev_holdings:  # regime exit
        state["nav"] *= (1 - TCOST_SELL * len(prev_holdings) / top_n)
    else:
        if sells:
            state["nav"] *= (1 - TCOST_SELL * len(sells) / top_n)
        if buys:
            state["nav"] *= (1 - TCOST_BUY * len(buys) / top_n)

    state["holdings"]    = new_holdings
    state["last_update"] = today_str  # calendar date of this run (not DB date)
    state["trade_count"] = state.get("trade_count", 0) + len(sells) + len(buys)

    # Entry prices for new buys
    ep = state.get("entry_prices", {})
    for t in sells:
        ep.pop(t, None)
    for t in buys:
        px = prices.loc[today, t] if t in prices.columns else None
        if px:
            ep[t] = round(float(px), 2)
    state["entry_prices"] = ep

    # ── Metrics ───────────────────────────────────────────────────────────
    nav          = state["nav"]
    set_nav      = state["set_nav"]
    nav_ret      = (nav / STARTING_NAV - 1) * 100
    set_ret_cum  = (set_nav / STARTING_NAV - 1) * 100
    excess       = nav_ret - set_ret_cum
    inception    = state.get("inception", today_str)

    daily_ret_pct  = port_ret * 100
    set_daily_pct  = set_ret * 100

    # Log entry
    log_entry = {
        "date":         str(today.date()),
        "nav":          round(nav, 0),
        "set_nav":      round(set_nav, 0),
        "daily_ret":    round(daily_ret_pct, 2),
        "set_daily":    round(set_daily_pct, 2),
        "holdings":     new_holdings[:],
        "sells":        list(sells),
        "buys":         list(buys),
    }
    state.setdefault("daily_log", []).append(log_entry)
    append_log(log_entry)
    save_state(state)

    # ── Console output ────────────────────────────────────────────────────
    bull_str = "BULL 📈" if new_holdings else "BEAR 🛡 (cash)"
    print(f"  Regime   : {bull_str}")
    print(f"  NAV      : ฿{nav:,.0f}  ({nav_ret:>+.1f}% since {inception})")
    print(f"  SET bench: ฿{set_nav:,.0f}  ({set_ret_cum:>+.1f}%)")
    print(f"  Alpha    : {excess:>+.1f}%")
    print(f"  Today    : port {daily_ret_pct:>+.2f}%  |  SET {set_daily_pct:>+.2f}%")
    if new_holdings:
        print(f"  Holdings ({len(new_holdings)}): {', '.join(new_holdings)}")
    if sells:
        print(f"  SOLD     : {', '.join(sorted(sells))}")
    if buys:
        print(f"  BOUGHT   : {', '.join(sorted(buys))}")

    # ── Focus list (always — even in bear) using yesterday's close ────────
    focus = compute_focus_list(prices, volumes, today, top_n=25)

    # ── Telegram ──────────────────────────────────────────────────────────
    _send_telegram(
        today=today.date(), nav=nav, nav_ret=nav_ret, set_ret_cum=set_ret_cum,
        excess=excess, daily_ret=daily_ret_pct, set_daily=set_daily_pct,
        holdings=new_holdings, sells=sells, buys=buys, bull=bool(new_holdings),
        inception=inception, pos_pnl=pos_pnl,
    )
    _send_focus_list(today=today.date(), focus=focus, holdings=set(new_holdings))
    _send_pulse_top5(today=today.date(), prices=prices)

    print(f"\n[DONE] State saved → {STATE_PATH.name}")


def _load_pulse_map() -> dict:
    """Load PULSE-TH signals with per-signal hit rates.

    Returns per-ticker PULSE score:
      avg_h3  = average fwd3 hit rate of signals that fired (quality)
      breadth = number of signals that fired (conviction)
    Only uses signals with h3 >= 55% (removes noise signals).
    """
    csv_path = ROOT / "data" / "research" / "thai_entry_screen_results.csv"
    if not csv_path.exists():
        return {}
    try:
        df = pd.read_csv(csv_path, low_memory=False)
        df["date"] = pd.to_datetime(df["date"])
        q_cols = [c for c in df.columns if c.startswith("Q") and len(c) > 1 and c[1].isdigit()]
        for col in q_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

        # ── Vectorized per-signal hit rate (fast) ────────────────────────
        q_arr = df[q_cols].values  # (N_rows, N_signals)
        import numpy as np

        if "fwd3" in df.columns:
            fwd = df["fwd3"].values
            hit = (fwd > 0).astype(float)
            hit[np.isnan(fwd)] = np.nan
            sig_h3_arr = np.full(len(q_cols), np.nan)
            for j in range(len(q_cols)):
                mask = q_arr[:, j] == 1
                if mask.sum() >= 15:
                    sig_h3_arr[j] = np.nanmean(hit[mask])
        else:
            sig_h3_arr = np.full(len(q_cols), 0.60)

        # Breadth: % of signals with h3>70% that fired  (numerator = fired, denom = total h3>70%)
        breadth_mask = (sig_h3_arr > 0.70) & (~np.isnan(sig_h3_arr))
        q_breadth    = q_arr[:, breadth_mask]                  # (N_rows, N_breadth_signals)
        n_breadth    = int(breadth_mask.sum())

        # HR: average h3 of top-3 fired signals per row (vectorized)
        # sig_h3_arr broadcast: multiply q_arr by h3 values, keep top-3 per row
        # q_arr * sig_h3_arr → h3 of fired signals, 0 for unfired
        h3_fired = q_arr * np.where(np.isnan(sig_h3_arr), 0, sig_h3_arr)  # (N_rows, N_sigs)
        # Sort each row descending, take top-3
        top3_h3 = np.sort(h3_fired, axis=1)[:, ::-1][:, :3]               # (N_rows, 3)
        top3_nonzero = (top3_h3 > 0).sum(axis=1)                           # how many non-zero
        top3_sum = top3_h3.sum(axis=1)
        avg_h3_arr   = np.where(top3_nonzero > 0, top3_sum / top3_nonzero, 0.0)
        fired_counts = q_breadth.sum(axis=1).astype(int)                   # h3>70% count

        # Build score_map {(date, ticker): {avg_h3, breadth}}
        score_map = {}
        latest_score = {}
        dates   = df["date"].values
        tickers = df["ticker"].values
        # sort by date ascending so latest overwrites in latest_score
        sort_idx = np.argsort(dates)
        for idx in sort_idx:
            key = (dates[idx], tickers[idx])
            sc  = {"avg_h3": round(float(avg_h3_arr[idx]) * 100, 1),
                   "breadth": int(fired_counts[idx])}
            score_map[key] = sc
            latest_score[tickers[idx]] = sc

        # Convert date keys to pd.Timestamp for lookup compatibility
        score_map_ts = {(pd.Timestamp(d), t): v for (d, t), v in score_map.items()}

        return {
            "score_map":    score_map_ts,
            "latest_score": latest_score,
            "n_signals":    len(q_cols),
            "n_quality":    n_breadth,   # signals with h3>70% = breadth universe
        }
    except Exception as e:
        print(f"[PULSE] load error: {e}")
        return {}


def compute_focus_list(prices, volumes, today, top_n=15):
    """Rank eligible universe by COMBINED RS signal.

    Uses the latest available close in the DB (= today after update step).
    Handles weekends/holidays automatically — avail_dates[-1] is always a
    real trading day regardless of calendar date.
    Returns list of dicts including price_date so caller can show which
    date's price was used.
    """
    rs_days = COMBINED["rs_days"]
    rs_type = COMBINED["rs_type"]

    trading_dates = prices.index.tolist()
    if today not in prices.index:
        return []
    i = trading_dates.index(today)
    price_date = today
    t_lb = trading_dates[max(0, i - rs_days)]

    eligible = eligible_universe(prices, volumes, price_date)
    px_now = prices.loc[price_date, list(eligible)].dropna()
    px_lb  = prices.loc[t_lb, list(eligible)].dropna()
    common = px_now.index.intersection(px_lb.index)
    if len(common) < 5:
        return []

    rs_raw = px_now[common] / px_lb[common] - 1

    if rs_type == "vol_weight":
        c_clean = [c for c in common if c + "_vol" in volumes.columns]
        if len(c_clean) >= 5:
            avg_vol   = volumes[[c + "_vol" for c in c_clean]].loc[:price_date].tail(21).mean()
            vol_ratio = (avg_vol / avg_vol.mean()).clip(0.1, 5.0)
            vol_ratio.index = [x.replace("_vol", "") for x in vol_ratio.index]
            rs = rs_raw.copy()
            vw = rs_raw.index.intersection(vol_ratio.index)
            rs[vw] = rs_raw[vw] * vol_ratio[vw]
        else:
            rs = rs_raw
    else:
        rs = rs_raw

    # RS percentile within full eligible universe
    rs_pct = rs.rank(pct=True) * 100

    # PULSE-TH information layer
    pulse_data = _load_pulse_map()
    today_ts   = pd.Timestamp(today)

    top = rs.nlargest(top_n)
    result = []
    for tkr, score in top.items():
        px = prices.loc[price_date, tkr] if tkr in prices.columns else None
        # Prefer today's score, fallback to latest available
        if pulse_data:
            score_key = (today_ts, tkr)
            if score_key in pulse_data.get("score_map", {}):
                ps = pulse_data["score_map"][score_key]
            else:
                ps = pulse_data.get("latest_score", {}).get(tkr, {"avg_h3": 0.0, "breadth": 0})
        else:
            ps = {"avg_h3": 0.0, "breadth": 0}

        # Breadth %: breadth / n_quality * 100
        n_quality = pulse_data.get("n_quality", 1) if pulse_data else 1
        breadth_pct = round(ps["breadth"] / n_quality * 100, 1) if n_quality > 0 else 0.0

        result.append({
            "ticker":      tkr,
            "rs_pct":      round(float(rs_pct[tkr]), 1),
            "price":       round(float(px), 2) if px else None,
            "price_date":  str(price_date.date()),
            "avg_h3":      ps["avg_h3"],
            "breadth_pct": breadth_pct,
            "breadth_n":   ps["breadth"],
        })
    return result


def _send_focus_list(*, today, focus: list, holdings: set):
    if not focus:
        return
    price_date = focus[0].get("price_date", str(today)) if focus else str(today)

    # Keep RS rank order (focus list is already sorted by RS descending)
    focus_sorted = focus

    lines = [f"<b>[TH] Focus List  |  {today}</b>"]
    lines.append(f"ราคาปิด {price_date}")

    # Check if any PULSE signals exist
    has_pulse = any(item.get("breadth_pct", 0.0) > 0 for item in focus)

    if has_pulse:
        lines.append(f"{'#':<3} {'Ticker':<12} {'RS':>4}  {'Price':>8}  {'HR':>5} {'Brd':>5}")
        lines.append("─" * 50)
    else:
        lines.append(f"{'#':<3} {'Ticker':<12} {'RS':>4}  {'Price':>8}")
        lines.append("─" * 38)

    for rank, item in enumerate(focus_sorted, 1):
        tkr  = item["ticker"]
        rs   = item["rs_pct"]
        px   = f"฿{item['price']:,.1f}" if item["price"] else "N/A"
        h3   = item.get("avg_h3", 0.0)
        bp   = item.get("breadth_pct", 0.0)
        in_port = " ●" if tkr in holdings else ""

        if bp > 0:
            # PULSE suffix with icon
            if h3 >= 75 and bp >= 20:
                icon = "🔴"
            elif h3 >= 65 or bp >= 10:
                icon = "🟠"
            else:
                icon = "🟡"
            pulse_str = f"  {icon}HR{h3:.0f}% Brd{bp:.0f}%"
        else:
            pulse_str = ""

        lines.append(f"{rank:<3} {tkr:<12} {rs:>4.0f}  {px:>8}{pulse_str}{in_port}")

    lines.append("")
    if has_pulse:
        lines.append("* HR = avg hitrate ของ top-3 signals ที่ดีที่สุดที่ fire วันนี้")
        lines.append("* Breadth = % ของ signals คุณภาพสูง (h3>70%) ที่ fire พร้อมกัน")
        lines.append("🔴HR≥75%+Breadth≥20% STRONG  🟠HR≥65% or Breadth≥10% WATCH  ●=port")
    else:
        lines.append("* RS = percentile rank ใน SET universe  ● = in portfolio")
    _tg("\n".join(lines))


def _send_pulse_top5(*, today, prices):
    """Send top 5 PULSE-TH stocks ranked purely by PULSE score (independent of RS)."""
    pulse_data = _load_pulse_map()
    if not pulse_data:
        return

    today_ts   = pd.Timestamp(today)
    n_quality  = pulse_data.get("n_quality", 1)
    score_map  = pulse_data.get("score_map", {})
    latest_sc  = pulse_data.get("latest_score", {})

    # Build ranked list: prefer today's score, fallback to latest
    rows = []
    for tkr, ls in latest_sc.items():
        sc = score_map.get((today_ts, tkr), ls)
        h3 = sc["avg_h3"]
        bd = sc["breadth"]
        bp = round(bd / n_quality * 100, 1) if n_quality > 0 else 0.0
        if h3 < 62.0:
            continue
        # Composite score: weight HR more than breadth
        composite = h3 * 0.7 + bp * 0.3
        px = None
        if tkr in prices.columns and today_ts in prices.index:
            v = prices.loc[today_ts, tkr]
            if pd.notna(v):
                px = round(float(v), 2)
        elif tkr in prices.columns:
            v = prices[tkr].dropna()
            if len(v):
                px = round(float(v.iloc[-1]), 2)
        rows.append({"ticker": tkr, "h3": h3, "bp": bp, "composite": composite, "price": px})

    if not rows:
        return

    top5 = sorted(rows, key=lambda x: -x["composite"])[:5]

    lines = [f"<b>[TH] PULSE-TH Top 5  |  {today}</b>"]
    lines.append("rank by HR × Breadth — independent of RS")
    lines.append(f"{'#':<3} {'Ticker':<12} {'Price':>8}  {'HR':>5} {'Brd':>5}")
    lines.append("─" * 44)
    for i, r in enumerate(top5, 1):
        px_str = f"฿{r['price']:,.1f}" if r["price"] else "N/A"
        if r["h3"] >= 75 and r["bp"] >= 20:
            icon = "🔴"
        elif r["h3"] >= 65 or r["bp"] >= 10:
            icon = "🟠"
        else:
            icon = "🟡"
        lines.append(f"{i:<3} {r['ticker']:<12} {px_str:>8}  {icon}HR{r['h3']:.0f}% Brd{r['bp']:.0f}%")
    lines.append("")
    lines.append("* HR = avg top-3 signal hitrate")
    lines.append("* Breadth = % ของ signals คุณภาพสูง (h3>70%) ที่ fire พร้อมกัน")
    _tg("\n".join(lines))


def _send_telegram(*, today, nav, nav_ret, set_ret_cum, excess, daily_ret,
                   set_daily, holdings, sells, buys, bull, inception, pos_pnl):
    sign  = lambda x: f"+{x:.1f}%" if x >= 0 else f"{x:.1f}%"
    lines = []

    # Header
    regime = "🟢 BULL" if bull else "🔴 CASH"
    lines.append(f"<b>[TH] AlphaAbsolute-TH  |  {today}</b>")
    lines.append(f"Regime: {regime}")
    lines.append("")

    # Portfolio summary
    lines.append(f"💰 NAV: ฿{nav:,.0f}  ({sign(nav_ret)} vs inception {inception})")
    lines.append(f"📊 SET: {sign(set_ret_cum)}  |  Alpha: <b>{sign(excess)}</b>")
    lines.append(f"📅 Today: TH {sign(daily_ret)}  |  SET {sign(set_daily)}")

    # Trades
    if sells:
        lines.append("")
        lines.append(f"🔴 SOLD: {', '.join(sorted(sells))}")
    if buys:
        lines.append("")
        lines.append(f"🟢 BOUGHT: {', '.join(sorted(buys))}")

    # Holdings
    if holdings:
        lines.append("")
        lines.append(f"📋 Holdings ({len(holdings)}):")
        # show top 5 with daily P&L
        shown = 0
        for h in holdings:
            if shown >= 5:
                break
            pnl = pos_pnl.get(h)
            pnl_str = f" ({sign(pnl)})" if pnl is not None else ""
            lines.append(f"  • {h}{pnl_str}")
            shown += 1
        if len(holdings) > 5:
            lines.append(f"  • … +{len(holdings)-5} more")
    else:
        lines.append("")
        lines.append("🛡 All cash — SET below MA50")

    lines.append("")
    lines.append("─ AlphaAbsolute-TH ─")

    _tg("\n".join(lines))


# ── Summary command ───────────────────────────────────────────────────────
def print_summary():
    if not STATE_PATH.exists():
        print("No state file — run daily update first")
        return
    state = load_state()
    nav         = state["nav"]
    set_nav     = state.get("set_nav", STARTING_NAV)
    inception   = state.get("inception", "?")
    last_update = state.get("last_update", "?")
    nav_ret     = (nav / STARTING_NAV - 1) * 100
    set_ret     = (set_nav / STARTING_NAV - 1) * 100
    excess      = nav_ret - set_ret
    holdings    = state.get("holdings", [])
    trades      = state.get("trade_count", 0)

    print(f"\n{'='*50}")
    print(f"AlphaModel-TH Paper Portfolio Summary")
    print(f"{'='*50}")
    print(f"  Inception  : {inception}")
    print(f"  Last update: {last_update}")
    print(f"  NAV        : ฿{nav:,.0f}")
    print(f"  Return     : {nav_ret:>+.1f}%  |  SET {set_ret:>+.1f}%  |  Alpha {excess:>+.1f}%")
    print(f"  Holdings   : {len(holdings)}  —  {', '.join(holdings) if holdings else 'Cash'}")
    print(f"  Total trades: {trades}")
    print(f"{'='*50}")

    # Show recent daily log
    log = state.get("daily_log", [])
    if log:
        print(f"\nRecent 10 days:")
        print(f"  {'Date':<12} {'NAV':>12} {'Daily':>8} {'SET':>8} {'Holdings':>6}")
        print(f"  {'-'*56}")
        for e in log[-10:]:
            print(f"  {e['date']:<12} ฿{e['nav']:>10,.0f}  "
                  f"{e['daily_ret']:>+6.2f}%  {e['set_daily']:>+6.2f}%  "
                  f"{len(e['holdings']):>2}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true", help="Show portfolio summary only")
    ap.add_argument("--reset",   action="store_true", help="Reset portfolio to starting NAV")
    ap.add_argument("--alert-fail", metavar="STEP", help="Send pipeline failure alert (called by GitHub Actions on error)")
    args = ap.parse_args()

    if args.reset:
        if STATE_PATH.exists():
            STATE_PATH.unlink()
        print(f"[RESET] Portfolio reset. Starting NAV: ฿{STARTING_NAV:,.0f}")
    elif args.summary:
        print_summary()
    elif args.alert_fail:
        _tg(f"<b>[TH] [WARN] PIPELINE FAILED | {args.alert_fail} | {date.today()}</b>\n"
            f"AlphaAbsolute-TH daily run failed.\nCheck GitHub Actions log.")
        print(f"[ALERT] Sent failure alert for step: {args.alert_fail}")
    else:
        run_daily()
