"""Honest parameter study for the LONG-ONLY live strategy. Sweep K (holdings),
trend-MA length, and momentum lookback. Select best on TRAIN only (by Sharpe),
evaluate on TEST, walk-forward the winner. Deploy only if it ROBUSTLY beats the live
config (K5 / 100d MA / 30d lookback). Real KuCoin data.

EXCHANGE_ID=kucoin python scripts/longonly_sweep.py
"""
import os, asyncio, itertools
import ccxt, numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters
COST = 15/1e4; R, SPLIT = 7, 0.60
STAB = {"USDC","USDT","DAI","TUSD","FDUSD","USDD","WBTC"}


def disc(n=40):
    cx = ccxt.kucoin({"enableRateLimit": True}); cx.load_markets()
    r = [(s, t.get("quoteVolume") or 0) for s, t in cx.fetch_tickers().items()
         if s.endswith("/USDT") and s.split("/")[0] not in STAB
         and not any(x in s.split("/")[0] for x in ("3L","3S","UP","DOWN")) and (t.get("quoteVolume") or 0) >= 5e6]
    r.sort(key=lambda x: -x[1]); return [s for s, _ in r[:n]]


def bt(panel, ma_panels, lb, k, ma_days):
    """Long-only dual momentum: top-k by lb-return, hold only those above their ma_days MA."""
    ma = ma_panels[ma_days]
    rets, prev = [], pd.Series(0.0, index=panel.columns); i = max(lb, ma_days)
    while i + R < len(panel):
        mom = (panel.iloc[i]/panel.iloc[i-lb]-1).dropna(); w = pd.Series(0.0, index=panel.columns)
        if len(mom) >= k:
            for s in mom.sort_values().index[-k:]:
                if panel.iloc[i][s] > ma.iloc[i][s]:
                    w[s] = 1/k
        fwd = (panel.iloc[i+R]/panel.iloc[i]-1).reindex(panel.columns).fillna(0)
        rets.append((w*fwd).sum() - (w-prev).abs().sum()*COST); prev = w; i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return 0.0, 0.0, 0.0
    eq = np.cumprod(1+rets); pk = np.maximum.accumulate(eq)
    return eq[-1]-1, rets.mean()/rets.std()*np.sqrt(365/R), np.max((pk-eq)/pk)


async def main():
    syms = disc(40); sem = asyncio.Semaphore(6)
    async def one(s):
        async with sem:
            try:
                h = await adapters.price.fetch_history(s, timeframe="1d", total=1200); return s, h["candles"]
            except Exception: return s, None
    res = await asyncio.gather(*[one(s) for s in syms])
    ser = {s: pd.Series({c[0]: c[4] for c in cs}) for s, cs in res if cs}
    full = pd.DataFrame(ser).sort_index().dropna(axis=1, thresh=600)
    ma_panels = {d: full.rolling(d).mean() for d in (50, 100, 150)}
    cut = int(len(full)*SPLIT)
    def sl(d): return {k: v.iloc[cut:].reset_index(drop=True) for k, v in d.items()} if isinstance(d, dict) else d.iloc[cut:].reset_index(drop=True)
    ptr = full.iloc[:cut].reset_index(drop=True); matr = {d: m.iloc[:cut].reset_index(drop=True) for d, m in ma_panels.items()}
    pte = full.iloc[cut:].reset_index(drop=True); mate = {d: m.iloc[cut:].reset_index(drop=True) for d, m in ma_panels.items()}
    print(f"\nLONG-ONLY sweep — {full.shape[1]} coins, train {cut}d / test {len(full)-cut}d\n")

    # Select best on TRAIN by Sharpe.
    grid = list(itertools.product([3,5,8,10], [50,100,150], [20,30,60]))
    scored = []
    for k, mad, lb in grid:
        _, sh, _ = bt(ptr, matr, lb, k, mad)
        scored.append((sh, k, mad, lb))
    scored.sort(reverse=True)
    bestsh, bk, bmad, blb = scored[0]
    print(f"best on TRAIN: K{bk} MA{bmad}d lookback{blb}d (train Sharpe {bestsh:.2f})")
    tn, ts, td = bt(pte, mate, blb, bk, bmad)
    print(f"  → TEST (OOS): net {tn*100:+.1f}%  Sharpe {ts:.2f}  maxDD {td*100:.1f}%")
    ln, ls, ld = bt(pte, mate, 30, 5, 100)
    print(f"  live config (K5/MA100/30d) OOS: net {ln*100:+.1f}%  Sharpe {ls:.2f}  maxDD {ld*100:.1f}%")

    # Show the K effect at the live MA/lookback (does diversification help DD?).
    print("\nK (holdings) effect at MA100/30d, OOS:")
    for k in (3,5,8,10):
        n2,s2,d2 = bt(pte, mate, 30, k, 100)
        print(f"  K{k}:  net {n2*100:+6.1f}%  Sharpe {s2:.2f}  maxDD {d2*100:.1f}%")

    # Walk-forward the train-selected winner vs live, 5 slices.
    print(f"\nwalk-forward: winner (K{bk}/MA{bmad}/{blb}d) vs live (K5/MA100/30d):")
    folds = np.array_split(np.arange(len(full)), 5); wins = 0
    for j, idx in enumerate(folds):
        seg = full.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
        ms = {d: m.iloc[idx[0]:idx[-1]+1].reset_index(drop=True) for d, m in ma_panels.items()}
        wn,ws,wd = bt(seg, ms, blb, bk, bmad); lvn,lvs,lvd = bt(seg, ms, 30, 5, 100)
        wins += ws > lvs
        print(f"  slice {j+1}: winner Sh {ws:5.2f} (DD {wd*100:4.0f}%) vs live Sh {lvs:5.2f} (DD {lvd*100:4.0f}%)")
    print(f"  → winner beats live in {wins}/5 slices")
    print("\n(Deploy only if the winner CLEARLY + ROBUSTLY beats live; else keep current.)")


asyncio.run(main())
