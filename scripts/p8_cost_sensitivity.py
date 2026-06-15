"""P8 — Cost/turnover sensitivity of the LIVE config (v06).

Live config: long-only, multi-horizon momentum (avg 14/30/60d), top-5, trend filter
(px>100d MA), weekly rebalance, equal-weight. strategy.yaml assumes 10bps fee + 5bps
slippage = 15bps/side. BUT the real Coinbase deploy cost was observed ~1.14% — i.e.
real retail costs may be ~50-60bps/side, far above the backtest assumption.

This script answers: how fragile is the edge to the cost assumption?
  1. Measure per-rebalance TURNOVER (so cost drag is analytic, not just empirical).
  2. Sweep round-trip cost per side and report net%, Sharpe, maxDD.
  3. Find the BREAK-EVEN cost where the OOS edge vanishes.
  4. Walk-forward at the live (15bps) and Coinbase-real (~60bps) levels for robustness.

Uses the EXACT live universe from state/strategy.yaml. Honest OOS (test half) + 5-slice
walk-forward. EXCHANGE_ID=kucoin python scripts/p8_cost_sensitivity.py
"""
import os, asyncio, yaml
import numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters

K, R, SPLIT = 5, 7, 0.60          # top-5, weekly, 60/40 train/test (report OOS only)
MA_DAYS = 100
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def live_universe():
    with open(os.path.join(REPO, "state", "strategy.yaml")) as f:
        cfg = yaml.safe_load(f)
    return cfg["universe"]


def multi_score(panel, i):
    """Live selection signal: average of 14/30/60d returns (higher = prefer)."""
    return ((panel.iloc[i]/panel.iloc[i-14]-1) + (panel.iloc[i]/panel.iloc[i-30]-1)
            + (panel.iloc[i]/panel.iloc[i-60]-1)) / 3


def bt(panel, ma, cost):
    """Long-only multi-horizon K5 weekly, trend-filtered. Returns (net, Sharpe, maxDD,
    avg_turnover, mean_gross_per_rebal). cost = per-side fraction."""
    rets, gross_list, turn_list = [], [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = 60
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= K:
            for s in sc.sort_values().index[-K:]:
                if panel.iloc[i][s] > ma.iloc[i][s]:     # dual-momentum trend filter
                    w[s] = 1/K
        fwd = (panel.iloc[i+R]/panel.iloc[i]-1).reindex(panel.columns).fillna(0)
        gross = (w*fwd).sum()
        turn = (w-prev).abs().sum()                      # round-trip turnover this rebal
        rets.append(gross - turn*cost)
        gross_list.append(gross); turn_list.append(turn)
        prev = w; i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    eq = np.cumprod(1+rets); pk = np.maximum.accumulate(eq)
    sharpe = rets.mean()/rets.std()*np.sqrt(365/R)
    return (eq[-1]-1, sharpe, np.max((pk-eq)/pk),
            float(np.mean(turn_list)), float(np.mean(gross_list)))


async def main():
    syms = live_universe()
    sem = asyncio.Semaphore(6)
    async def one(s):
        async with sem:
            try:
                h = await adapters.price.fetch_history(s, timeframe="1d", total=1200)
                return s, h["candles"]
            except Exception:
                return s, None
    res = await asyncio.gather(*[one(s) for s in syms])
    ser = {s: pd.Series({c[0]: c[4] for c in cs}) for s, cs in res if cs}
    full = pd.DataFrame(ser).sort_index().dropna(axis=1, thresh=600)
    ma = full.rolling(MA_DAYS).mean()
    cut = int(len(full)*SPLIT)
    pte = full.iloc[cut:].reset_index(drop=True)
    mate = ma.iloc[cut:].reset_index(drop=True)
    oos_days = (full.index[-1]-full.index[cut])/86400000
    n_coins = full.shape[1]
    n_missing = len(syms) - n_coins
    print(f"\nP8 cost/turnover sensitivity — LIVE config (multi-horizon, K5, weekly, trend)")
    print(f"OOS ~{oos_days:.0f}d · {n_coins}/{len(syms)} universe coins had history "
          f"({n_missing} missing/short)\n")

    # 1) Turnover + cost sweep
    costs_bps = [0, 3, 5, 10, 15, 25, 40, 60, 80]
    print(f"{'cost/side':>10}{'net%':>9}{'Sharpe':>8}{'maxDD':>8}{'turnover/rebal':>16}")
    base_turn = None
    rows = []
    for cb in costs_bps:
        n, s, d, turn, gross = bt(pte, mate, cb/1e4)
        base_turn = turn  # turnover is cost-independent (weights unchanged)
        base_gross = gross
        tag = ""
        if cb == 15: tag = "  <- backtest assumption"
        if cb == 60: tag = "  <- ~Coinbase retail observed"
        print(f"{cb:>8}bp{n*100:>8.1f}%{s:>8.2f}{d*100:>7.1f}%{turn:>14.3f}x{tag}")
        rows.append((cb, n, s, d))

    # 2) Analytic break-even: cost where mean net per-rebalance return = 0
    #    net_per_rebal = mean_gross - turnover*cost  => cost* = mean_gross/turnover
    breakeven = base_gross/base_turn if base_turn else float("nan")
    print(f"\nmean gross return / rebalance: {base_gross*100:.3f}%   "
          f"avg turnover: {base_turn:.3f}x")
    print(f"ANALYTIC break-even cost/side (mean net per-rebal = 0): "
          f"{breakeven*1e4:.0f} bps/side  ({breakeven*100:.3f}%)")

    # 3) Walk-forward at live (15bps) and Coinbase-real (60bps) for robustness
    print("\nwalk-forward (5 slices) Sharpe / net% at two cost levels:")
    folds = np.array_split(np.arange(len(full)), 5)
    for cb in (15, 60):
        sh, nets = [], []
        for idx in folds:
            seg = full.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
            ms = ma.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
            n, s, d, _, _ = bt(seg, ms, cb/1e4)
            sh.append(round(s, 2)); nets.append(round(n*100, 1))
        pos = sum(1 for x in sh if x > 0)
        print(f"  {cb:>2}bps/side  Sharpe {sh}  positive {pos}/5 | net% {nets}")
    print("\nVerdict guide: edge is robust if it stays clearly positive at the cost level")
    print("the user ACTUALLY pays. If break-even < real cost, the live edge is illusory.")


asyncio.run(main())
