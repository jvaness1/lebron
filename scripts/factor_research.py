"""Genuinely-different SELECTION signals for the long-only strategy (not just param
tweaks). Current ranks by raw 30d return. Test risk-adjusted momentum, multi-horizon
momentum, and momentum+low-vol — the standard ways to make a momentum strategy more
robust. All long-only, top-5, trend-filtered (px>100d MA), weekly. Train→test honest.

EXCHANGE_ID=kucoin python scripts/factor_research.py
"""
import os, asyncio
import ccxt, numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters
COST = 15/1e4; K, R, SPLIT = 5, 7, 0.60
STAB = {"USDC","USDT","DAI","TUSD","FDUSD","USDD","WBTC"}


def disc(n=40):
    cx = ccxt.kucoin({"enableRateLimit": True}); cx.load_markets()
    r = [(s, t.get("quoteVolume") or 0) for s, t in cx.fetch_tickers().items()
         if s.endswith("/USDT") and s.split("/")[0] not in STAB
         and not any(x in s.split("/")[0] for x in ("3L","3S","UP","DOWN")) and (t.get("quoteVolume") or 0) >= 5e6]
    r.sort(key=lambda x: -x[1]); return [s for s, _ in r[:n]]


def score(panel, i, mode):
    """Return a per-coin selection score at row i (higher = prefer)."""
    c = panel.iloc[i]
    if mode == "raw":
        return panel.iloc[i] / panel.iloc[i-30] - 1
    if mode == "riskadj":
        ret = panel.iloc[i] / panel.iloc[i-30] - 1
        vol = panel.pct_change().iloc[i-30:i].std()
        return ret / vol.replace(0, np.nan)
    if mode == "multi":
        return ((panel.iloc[i]/panel.iloc[i-14]-1) + (panel.iloc[i]/panel.iloc[i-30]-1)
                + (panel.iloc[i]/panel.iloc[i-60]-1)) / 3
    if mode == "lowvol":   # among top-2K momentum, prefer lowest vol
        ret = panel.iloc[i] / panel.iloc[i-30] - 1
        vol = panel.pct_change().iloc[i-30:i].std()
        cand = ret.dropna().sort_values().index[-2*K:]
        s = pd.Series(-1e9, index=panel.columns)
        s[cand] = -vol.reindex(cand)   # higher score = lower vol
        return s
    return c


def bt(panel, ma, mode):
    rets, prev = [], pd.Series(0.0, index=panel.columns); i = 60
    while i + R < len(panel):
        sc = score(panel, i, mode).dropna(); w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= K:
            for s in sc.sort_values().index[-K:]:
                if panel.iloc[i][s] > ma.iloc[i][s]:
                    w[s] = 1/K
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
    full = pd.DataFrame(ser).sort_index().dropna(axis=1, thresh=600); ma = full.rolling(100).mean()
    cut = int(len(full)*SPLIT)
    pte, mate = full.iloc[cut:].reset_index(drop=True), ma.iloc[cut:].reset_index(drop=True)
    print(f"\nFACTOR research (long-only, K5, trend-filtered) — OOS ~{(full.index[-1]-full.index[cut])/86400000:.0f}d, {full.shape[1]} coins\n")
    print(f"{'selection signal':<28}{'net%':>9}{'Sharpe':>8}{'maxDD':>8}")
    for mode, name in [("raw","raw 30d momentum (live)"),("riskadj","risk-adjusted momentum"),
                       ("multi","multi-horizon (14/30/60)"),("lowvol","momentum + low-vol")]:
        n,s,d = bt(pte, mate, mode); print(f"{name:<28}{n*100:>8.1f}%{s:>8.2f}{d*100:>7.1f}%")

    # Walk-forward the best non-raw vs raw, 5 slices (robustness, not single window)
    print("\nwalk-forward Sharpe by slice (raw vs each alternative):")
    folds = np.array_split(np.arange(len(full)), 5)
    rows = {m: [] for m in ("raw","riskadj","multi","lowvol")}
    for idx in folds:
        seg = full.iloc[idx[0]:idx[-1]+1].reset_index(drop=True); ms = ma.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
        for m in rows: rows[m].append(bt(seg, ms, m)[1])
    for m in rows:
        wins = sum(1 for a, b in zip(rows[m], rows["raw"]) if a > b)
        print(f"  {m:<10} slices Sharpe {[round(x,2) for x in rows[m]]}  beats raw {wins}/5")
    print("\n(Adopt an alternative only if it BEATS raw robustly across slices, not one window.)")


asyncio.run(main())
