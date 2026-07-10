"""P9 — Live-vs-backtest drift check (FIRST honest read, 4 live rebalances accrued).

The single most important real-money gate: does the LIVE bot actually behave like the
backtest that every LOG finding is built on? Live went real on Coinbase 2026-06-15.
As of 2026-07-07 there are 4 weekly rebalances logged in
`~/hermes-trading/state/equity_history.jsonl` — enough (>=3) to make a first read.

Live path (paper-engine equity, KuCoin-USDT marked, normalised to 1.0 at inception):
  R1 2026-06-15  0.9988
  R2 2026-06-22  0.9087   (-9.0% wk)
  R3 2026-06-29  0.7798   (-14.2% wk)
  R4 2026-07-07  0.7922   (+1.6% wk)
  cumulative R1->R4:  -20.7%   (a -21% drawdown in the first 3 weeks of real money)

Two questions, both answered here:
  (A) FIDELITY — reproduce the EXACT live strict-top-K engine (multi-horizon [14,30,60]
      momentum + 100d-MA trend filter, weekly, 15bps/side) on the SAME KuCoin panel over
      the SAME dates and compare to the realised live equity. Small tracking error =>
      the engine matches the backtest methodology, so the -21% is the strategy doing its
      thing, not a broken execution. Large error => investigate fills/data/accounting.
  (B) NORMALITY — is a -20.7% 3-week draw even unusual for THIS strategy? Compare it to
      the strategy's own historical distribution of ~22-day rolling net returns on the
      2020-> panel (P13/P20 already flagged worstWk ~-27%, maxDD ~80%).

NOTE the stale drift_tracker.py compares live to a SINGLE-lookback, no-trend-filter
engine (v02 era) — wrong for the v06 config. This uses the faithful p15/p28 engine.

    /Users/jamesvaness/hermes-trading/.venv/bin/python scripts/p9_drift_check.py
"""
import os
import sys
import json
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import K, LBS, COST, MA_DAYS, multi_score  # noqa: E402

LIVE_HIST = Path("/Users/jamesvaness/hermes-trading/state/equity_history.jsonl")


def _live_series():
    if not LIVE_HIST.exists():
        return []
    out = []
    for line in LIVE_HIST.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _target_weights(panel, ma, i, trend=True, top_k=K):
    """EXACT live weights at bar i (p28._target_weights)."""
    sc = multi_score(panel, i).dropna()
    w = pd.Series(0.0, index=panel.columns)
    if len(sc) >= 1:
        order = list(sc.sort_values(ascending=False).index)
        row, marow = panel.iloc[i], ma.iloc[i]
        picked = 0
        for s in order:
            if picked >= top_k:
                break
            if (not trend) or (row[s] > marow[s]):
                w[s] = 1.0  # provisional; normalise after
                picked += 1
    if w.sum() > 0:
        w = w / w.sum()          # equal-weight the names that pass (cash if fewer than top_k? live keeps 1/k)
        # live uses fixed 1/top_k per held name (cash residual if <k pass). Reproduce that:
        held = list(w[w > 0].index)
        w = pd.Series(0.0, index=panel.columns)
        for s in held:
            w[s] = 1.0 / top_k
    return w


def replay_between(panel, ma, i0, i1, prev):
    """Mark the live engine daily from bar i0 to bar i1, rebalancing once at i0.
    Returns (equity_multiple_over_interval, weights_used, held_names)."""
    w = _target_weights(panel, ma, i0)
    tc = (w - prev).abs().sum() * COST
    eq = 1.0 - tc
    for d in range(i0, i1):
        dr = (w * (panel.iloc[d + 1] / panel.iloc[d] - 1)).sum()
        eq *= (1 + dr)
    return eq, w, list(w[w > 0].index)


def nearest_bar(idx_ms, ts_ms):
    return int(np.argmin(np.abs(np.asarray(idx_ms) - ts_ms)))


def main():
    live = _live_series()
    print(f"\n=== P9 live-vs-backtest drift check ===")
    print(f"Live rebalances logged: {len(live)}")
    if len(live) < 3:
        print("Not enough live history yet (need >=3). Exiting."); return

    uni = live_universe()
    panel = load_panel(uni).sort_index()
    ma = panel.rolling(MA_DAYS, min_periods=MA_DAYS).mean()
    idx_ms = list(panel.index)

    # Map each live rebalance ts -> nearest panel bar.
    rebs = []
    for pt in live:
        ts_ms = int(pt["ts"] * 1000)
        b = nearest_bar(idx_ms, ts_ms)
        rebs.append((b, pt))
        d = pd.to_datetime(idx_ms[b], unit="ms").date()
        print(f"  R{pt['rebalance']} ts={pd.to_datetime(ts_ms, unit='ms').date()} "
              f"-> panel bar {b} ({d})  live_eq={pt['equity']:.4f} regime={pt.get('regime')}")

    # ---------- (A) FIDELITY: interval-by-interval replay vs live ----------
    print("\n(A) FIDELITY — faithful engine replay vs realised live equity")
    print(f"    {'interval':<14}{'live%':>9}{'backtest%':>11}{'drift(pp)':>11}   held (replay)")
    prev = pd.Series(0.0, index=panel.columns)
    bt_eq = 1.0
    live_eq0 = live[0]["equity"]
    live_cum, bt_cum_report = [], []
    for k in range(len(rebs) - 1):
        i0, pt0 = rebs[k]
        i1, pt1 = rebs[k + 1]
        live_ret = pt1["equity"] / pt0["equity"] - 1
        seg, w, held = replay_between(panel, ma, i0, i1, prev)
        prev = w
        bt_eq *= seg
        bt_ret = seg - 1
        drift = (live_ret - bt_ret) * 100
        label = f"R{pt0['rebalance']}->R{pt1['rebalance']}"
        heldnames = ", ".join(s.split("/")[0] for s in held) if held else "CASH"
        print(f"    {label:<14}{live_ret*100:>8.2f}%{bt_ret*100:>10.2f}%{drift:>10.2f}   {heldnames}")

    live_cum_ret = live[-1]["equity"] / live_eq0 - 1
    bt_cum_ret = bt_eq - 1
    print(f"    {'CUMULATIVE':<14}{live_cum_ret*100:>8.2f}%{bt_cum_ret*100:>10.2f}%"
          f"{(live_cum_ret - bt_cum_ret)*100:>10.2f}")
    tol = 3.0
    te = abs(live_cum_ret - bt_cum_ret) * 100
    if te <= tol:
        print(f"    => tracking error {te:.2f}pp <= {tol:.0f}pp: live engine matches the backtest "
              f"methodology. The -21% is the STRATEGY, not a broken execution.")
    else:
        print(f"    => tracking error {te:.2f}pp > {tol:.0f}pp: live diverges from the faithful "
              f"replay — investigate selection/data/fills.")

    # ---------- (B) NORMALITY: is -20.7%/3wk unusual for this strategy? ----------
    print("\n(B) NORMALITY — where does the live 3-week draw sit in the strategy's own history?")
    # Build the full daily-marked equity of the live engine on 2020-> and take rolling
    # N-day net returns (N = live window length in days).
    n_days_window = rebs[-1][0] - rebs[0][0]
    hist = panel.loc[panel.index >= pd.Timestamp("2020-01-01").value // 10**6]
    hist_ma = hist.rolling(MA_DAYS, min_periods=MA_DAYS).mean()
    WARM = max(LBS)
    R = 7
    prev = pd.Series(0.0, index=hist.columns)
    day_rets = []
    i = WARM
    n = len(hist)
    while i + R < n:
        w = _target_weights(hist, hist_ma, i)
        tc = (w - prev).abs().sum() * COST
        for d in range(i, i + R):
            dr = (w * (hist.iloc[d + 1] / hist.iloc[d] - 1)).sum()
            if d == i:
                dr -= tc
            day_rets.append(dr)
        prev = w
        i += R
    rets = np.array(day_rets)
    ec = np.cumprod(1 + rets)
    N = max(1, n_days_window)
    roll = ec[N:] / ec[:-N] - 1
    pctile = float((roll < live_cum_ret).mean() * 100)
    print(f"    live window ~{N} days; strategy has {len(roll)} overlapping {N}-day rolling returns")
    print(f"    live {N}-day return = {live_cum_ret*100:+.1f}%")
    print(f"    rolling {N}-day return distribution: min {roll.min()*100:+.1f}%  "
          f"p5 {np.percentile(roll,5)*100:+.1f}%  median {np.median(roll)*100:+.1f}%  "
          f"p95 {np.percentile(roll,95)*100:+.1f}%  max {roll.max()*100:+.1f}%")
    print(f"    live draw sits at the {pctile:.0f}th percentile of the strategy's own {N}-day returns")
    if pctile >= 5:
        print(f"    => a {live_cum_ret*100:.0f}% {N}-day draw is WITHIN normal strategy behaviour "
              f"(not a tail/broken-edge signal).")
    else:
        print(f"    => a {live_cum_ret*100:.0f}% {N}-day draw is in the worst 5% — unusual; watch closely.")

    print("\nCaveat: 4 rebalances / 3 intervals is a TINY sample; this is a fidelity check, "
          "not a verdict on the edge. Re-run as more live rebalances accrue.")


if __name__ == "__main__":
    main()
