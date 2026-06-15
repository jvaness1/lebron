"""P6 — test the LIVE long-only strategy on the LONGEST history available (the cloud
bot couldn't: no exchange access in its sandbox). Pull max KuCoin daily; keep only
long-lived coins so the common window spans multiple market cycles; walk-forward the
live config (multi-horizon momentum 14/30/60 + 100d trend filter, K5, weekly).
Question: does the edge hold across more bull/bear cycles, or was it one lucky window?

EXCHANGE_ID=kucoin python scripts/p6_longer_history.py
"""
import os, asyncio
import ccxt, numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters
COST = 15/1e4; K, R, MA = 5, 7, 100
LBS = [14, 30, 60]
STAB = {"USDC","USDT","DAI","TUSD","FDUSD","USDD","WBTC"}


def disc(n=60):
    cx = ccxt.kucoin({"enableRateLimit": True}); cx.load_markets()
    r = [(s, t.get("quoteVolume") or 0) for s, t in cx.fetch_tickers().items()
         if s.endswith("/USDT") and s.split("/")[0] not in STAB
         and not any(x in s.split("/")[0] for x in ("3L","3S","UP","DOWN")) and (t.get("quoteVolume") or 0) >= 2e6]
    r.sort(key=lambda x: -x[1]); return [s for s, _ in r[:n]]


def momo(panel, i):  # multi-horizon momentum at row i
    return sum(panel.iloc[i]/panel.iloc[i-lb]-1 for lb in LBS) / len(LBS)


def bt(panel, ma):
    rets, prev = [], pd.Series(0.0, index=panel.columns); i = max(LBS)
    while i + R < len(panel):
        sc = momo(panel, i).dropna(); w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= K:
            for s in sc.sort_values().index[-K:]:
                if panel.iloc[i][s] > ma.iloc[i][s]:
                    w[s] = 1/K
        fwd = (panel.iloc[i+R]/panel.iloc[i]-1).reindex(panel.columns).fillna(0)
        rets.append((w*fwd).sum() - (w-prev).abs().sum()*COST); prev = w; i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return 0.0, 0.0, 0.0, len(rets)
    eq = np.cumprod(1+rets); pk = np.maximum.accumulate(eq)
    return eq[-1]-1, rets.mean()/rets.std()*np.sqrt(365/R), np.max((pk-eq)/pk), len(rets)


async def main():
    syms = disc(60); sem = asyncio.Semaphore(6)
    async def one(s):
        async with sem:
            try:
                h = await adapters.price.fetch_history(s, timeframe="1d", total=3000); return s, h["candles"]
            except Exception: return s, None
    res = await asyncio.gather(*[one(s) for s in syms])
    ser = {s: pd.Series({c[0]: c[4] for c in cs}) for s, cs in res if cs}
    raw = pd.DataFrame(ser).sort_index()
    # Keep coins with long history (>=1500 days) but RAGGED — coins enter over time;
    # momentum/selection handles per-row availability (NaN coins drop out each rebalance).
    panel = raw.dropna(axis=1, thresh=1500).dropna(axis=0, how="all")
    ma = panel.rolling(MA).mean()
    span = (panel.index[-1]-panel.index[0])/86400000
    avg_live = int(panel.notna().sum(axis=1).mean())
    print(f"\nP6 longer-history test — {panel.shape[1]} long-lived coins "
          f"(~{avg_live} live per day avg), {len(panel)} days (~{span/365:.1f} years)\n")

    net, sh, dd, n = bt(panel, ma)
    print(f"FULL period: net {net*100:+.0f}%  Sharpe {sh:.2f}  maxDD {dd*100:.1f}%  ({n} weekly rebalances)")

    # Walk-forward across as many ~6-month slices as the history allows (more cycles).
    nsl = max(4, int(span/180))
    folds = np.array_split(np.arange(len(panel)), nsl); pos = 0; shs = []
    print(f"\nwalk-forward across {nsl} sequential slices (~6mo each):")
    for j, idx in enumerate(folds):
        seg = panel.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
        ms = ma.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
        sn, ss, sd, sc = bt(seg, ms)
        d0 = (panel.index[idx[-1]]-panel.index[idx[0]])/86400000
        pos += ss > 0; shs.append(ss)
        print(f"  slice {j+1} ({d0:.0f}d): net {sn*100:+7.1f}%  Sharpe {ss:5.2f}  maxDD {sd*100:4.0f}%")
    print(f"\n  positive-Sharpe slices: {pos}/{len(shs)}   median slice Sharpe: {np.median(shs):.2f}")
    print("  → edge holds across cycles iff most slices are positive on a longer, varied window.")


asyncio.run(main())
