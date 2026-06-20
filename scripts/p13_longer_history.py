"""P13/P6 — re-validate the LIVE config on the LONG multi-cycle window the new cache
unblocks. P6 was blocked because the live API rate-limited a deep fetch; scripts/
data_cache.py fixed that (serial, throttled, cached). Now we can finally test the edge
across 2018->2026 (multiple bull/bear cycles) instead of one ~3.3yr window.

Live config (state/strategy.yaml v06): long-only, multi-horizon momentum (avg 14/30/60d),
top-5, dual-momentum trend filter (px>100d MA, else cash), weekly rebalance, equal weight,
15bps/side.

IMPORTANT honesty framing:
  * The live params (14/30/60, K5, 100d MA) were SELECTED on the recent ~3yr window. So the
    PRE-2023 portion of this test was never used for selection -> it is a genuine
    out-of-sample-in-TIME test across cycles (2018 bear, 2020 crash, 2021 bull, 2022 bear).
  * SURVIVORSHIP gets WORSE the further back you look (this is today's survivor panel; coins
    that existed in 2019 and later died are absent). P10 sized that haircut (~1/3). Read the
    early-period numbers as optimistic-survivor upper bounds, the walk-forward CONSISTENCY
    across regimes as the real signal.
  * Early years are THIN (few coins listed). A K5 book needs >=5 scored coins; rows with
    fewer auto-skip (cash). We print the live-coin count over time so thinness is explicit.

EXCHANGE_ID=kucoin python scripts/data_cache.py --update   # refresh cache first
python scripts/p13_longer_history.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402

K, R, MA_DAYS = 5, 7, 100
LBS = [14, 30, 60]
COST = 15 / 1e4


def multi_score(panel, i):
    return sum(panel.iloc[i] / panel.iloc[i - lb] - 1 for lb in LBS) / len(LBS)


def bt(panel, ma, cost=COST):
    """Live long-only multi-horizon K5 weekly trend-filtered backtest.
    Returns (net, Sharpe, maxDD, n_rebals, active_frac)."""
    rets, active = [], 0
    prev = pd.Series(0.0, index=panel.columns)
    i = max(LBS)
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= K:
            for s in sc.sort_values().index[-K:]:
                if panel.iloc[i][s] > ma.iloc[i][s]:
                    w[s] = 1 / K
        if w.sum() > 0:
            active += 1
        fwd = (panel.iloc[i + R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0)
        rets.append((w * fwd).sum() - (w - prev).abs().sum() * cost)
        prev = w
        i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return 0.0, 0.0, 0.0, len(rets), 0.0
    eq = np.cumprod(1 + rets)
    pk = np.maximum.accumulate(eq)
    sharpe = rets.mean() / rets.std() * np.sqrt(365 / R)
    return eq[-1] - 1, sharpe, float(np.max((pk - eq) / pk)), len(rets), active / len(rets)


def main():
    syms = live_universe()
    panel = load_panel(syms, "1d")
    if panel.empty:
        sys.exit("No cache. Run: EXCHANGE_ID=kucoin python scripts/data_cache.py --update")
    panel.index = pd.to_datetime(panel.index, unit="ms")
    panel = panel.sort_index()
    # Drop columns with too little history to ever form a signal (need >60d + buffer).
    panel = panel.dropna(axis=1, thresh=120)
    ma = panel.rolling(MA_DAYS).mean()
    span_yrs = (panel.index[-1] - panel.index[0]).days / 365

    print(f"\nP13 longer-history validation of the LIVE config")
    print(f"window {panel.index[0].date()} .. {panel.index[-1].date()} "
          f"(~{span_yrs:.1f}y, {len(panel)} days), {panel.shape[1]} coins\n")

    # Coin availability over time (honesty about early thinness).
    live_per = panel.notna().sum(axis=1)
    print("live coins over time (year-end):")
    for yr in range(panel.index[0].year, panel.index[-1].year + 1):
        sub = live_per[live_per.index.year == yr]
        if len(sub):
            print(f"  {yr}: ~{int(sub.mean()):2d} avg ({int(sub.iloc[-1])} at year end)")
    # First date with >=K coins scored (book can actually trade).
    enough = live_per[live_per >= K]
    if len(enough):
        print(f"  first date with >={K} live coins: {enough.index[0].date()}\n")

    # Full-period.
    net, sh, dd, n, act = bt(panel, ma)
    print(f"FULL {panel.index[0].year}-{panel.index[-1].year}: "
          f"net {net*100:+.0f}%  Sharpe {sh:.2f}  maxDD {dd*100:.1f}%  "
          f"({n} weekly rebals, in-market {act*100:.0f}%)\n")

    # Calendar-year walk-forward (each year a distinct, mostly-OOS-in-time slice).
    print("per-calendar-year (each year an independent regime slice):")
    yr_sh = []
    for yr in range(panel.index[0].year, panel.index[-1].year + 1):
        mask = panel.index.year == yr
        seg = panel[mask].reset_index(drop=True)
        mseg = ma[mask].reset_index(drop=True)
        if len(seg) < max(LBS) + 2 * R:
            continue
        n_, s_, d_, c_, a_ = bt(seg, mseg)
        if c_ < 3:
            continue
        yr_sh.append((yr, s_))
        print(f"  {yr}: net {n_*100:+7.1f}%  Sharpe {s_:5.2f}  maxDD {d_*100:4.0f}%  "
              f"({c_} rebals, in-mkt {a_*100:3.0f}%)")
    pos = sum(1 for _, s in yr_sh if s > 0)
    print(f"\n  positive-Sharpe years: {pos}/{len(yr_sh)}  "
          f"median year Sharpe: {np.median([s for _, s in yr_sh]):.2f}")

    # Equal-length 6-month walk-forward (comparable to prior P-numbers, but many more slices).
    nsl = max(5, int(span_yrs * 2))
    folds = np.array_split(np.arange(len(panel)), nsl)
    print(f"\n{nsl}-slice walk-forward (~6mo each):")
    shs, nets = [], []
    for j, idx in enumerate(folds):
        seg = panel.iloc[idx[0]:idx[-1] + 1].reset_index(drop=True)
        mseg = ma.iloc[idx[0]:idx[-1] + 1].reset_index(drop=True)
        n_, s_, d_, c_, _ = bt(seg, mseg)
        d0 = panel.index[idx[0]].date()
        shs.append(s_); nets.append(n_)
        print(f"  s{j+1:2d} {d0} ({c_:2d} rebals): net {n_*100:+7.1f}%  "
              f"Sharpe {s_:5.2f}  maxDD {d_*100:4.0f}%")
    pos = sum(1 for s in shs if s > 0)
    print(f"\n  positive-Sharpe slices: {pos}/{len(shs)}  median {np.median(shs):.2f}")
    print("\n  -> edge holds across cycles iff most slices/years stay positive on this "
          "LONGER, multi-regime window (read early years as survivor-optimistic).")


if __name__ == "__main__":
    main()
