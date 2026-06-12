"""P1 — the daily-frame variant, done HONESTLY. Earlier a test-set search hinted
daily-rebalance + 14d/K3 → +179%/1.42, but that was selected ON the test set. Here:
pick (lookback, K, gate?) on TRAIN only, evaluate on TEST; then 5-slice walk-forward.
Compare to the live weekly config. Real KuCoin data.

EXCHANGE_ID=kucoin python scripts/p1_daily.py
"""
import os, asyncio, itertools
import ccxt, numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters
COST = 15/1e4; STAB = {"USDC","USDT","DAI","TUSD","FDUSD","USDD","WBTC"}


def disc(n=40):
    cx = ccxt.kucoin({"enableRateLimit": True}); cx.load_markets()
    r = [(s, t.get("quoteVolume") or 0) for s, t in cx.fetch_tickers().items()
         if s.endswith("/USDT") and s.split("/")[0] not in STAB
         and not any(x in s.split("/")[0] for x in ("3L","3S","UP","DOWN")) and (t.get("quoteVolume") or 0) >= 5e6]
    r.sort(key=lambda x: -x[1]); return [s for s, _ in r[:n]]


def bscore(p):
    e9,e21,e50,e200 = (p.ewm(span=s,adjust=False).mean() for s in (9,21,50,200))
    macd = p.ewm(span=12,adjust=False).mean()-p.ewm(span=26,adjust=False).mean(); hist = macd-macd.ewm(span=9,adjust=False).mean()
    sto = (p-p.rolling(14).min())/(p.rolling(14).max()-p.rolling(14).min())*100
    d = p.diff(); g = d.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); l = (-d.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean(); rs = 100-100/(1+g/l.replace(0,np.nan))
    chk=[e9>e21,e21>e50,e50>e200,p>p.rolling(20).mean(),p>p.rolling(50).mean(),rs>50,hist>0,p.pct_change(9)>0,p>p.shift(10),sto>50]
    return sum(c.astype(float) for c in chk)


def bt(panel, B, lb, k, R, gate):
    rets, prev = [], pd.Series(0.0, index=panel.columns); i = lb
    while i + R < len(panel):
        inv = ((B.iloc[i] >= 6).mean() >= 0.4) if gate else True
        if not inv:
            w = pd.Series(0.0, index=panel.columns)
        else:
            mom = (panel.iloc[i]/panel.iloc[i-lb]-1).dropna()
            if len(mom) < 2*k:
                i += R; continue
            r = mom.sort_values(); w = pd.Series(0.0, index=panel.columns); w[r.index[-k:]] = 1/k; w[r.index[:k]] = -1/k
        fwd = (panel.iloc[i+R]/panel.iloc[i]-1).reindex(panel.columns).fillna(0)
        rets.append((w*fwd).sum() - (w-prev).abs().sum()*COST); prev = w; i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return 0.0, 0.0, 0.0, len(rets)
    eq = np.cumprod(1+rets); pk = np.maximum.accumulate(eq)
    return eq[-1]-1, rets.mean()/rets.std()*np.sqrt(365/R), np.max((pk-eq)/pk), len(rets)


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
    B = bscore(full); cut = int(len(full)*0.6)
    ptr, Btr = full.iloc[:cut].reset_index(drop=True), B.iloc[:cut].reset_index(drop=True)
    pte, Bte = full.iloc[cut:].reset_index(drop=True), B.iloc[cut:].reset_index(drop=True)
    print(f"\nP1 daily-frame — {full.shape[1]} coins, train {cut}d / test {len(full)-cut}d\n")

    # Select best DAILY config on TRAIN only.
    best = None
    for lb, k, gate in itertools.product([7,10,14,21], [2,3,4], [True, False]):
        net, sh, mdd, n = bt(ptr, Btr, lb, k, 1, gate)
        if n >= 30 and (best is None or sh > best[1]):
            best = (lb, k, gate, sh)
    lb, k, gate, trsh = best[0], best[1], best[2], best[3]
    print(f"best DAILY config on TRAIN: lookback{lb} K{k} gate={gate} (train Sharpe {trsh:.2f})")
    # Evaluate that config OOS (test).
    net, sh, mdd, n = bt(pte, Bte, lb, k, 1, gate)
    print(f"  → TEST (OOS): net {net*100:+.1f}%  Sharpe {sh:.2f}  maxDD {mdd*100:.1f}%  ({n} daily rebalances)")
    # Live weekly baseline on the same test window.
    wnet, wsh, wmdd, wn = bt(pte, Bte, 30, 5, 7, True)
    print(f"  live WEEKLY baseline (OOS):  net {wnet*100:+.1f}%  Sharpe {wsh:.2f}  maxDD {wmdd*100:.1f}%  ({wn} weekly)")

    # Walk-forward the train-selected daily config across 5 slices.
    print("\nwalk-forward of the train-selected daily config:")
    folds = np.array_split(np.arange(len(full)), 5); wins = 0
    for j, idx in enumerate(folds):
        seg = full.iloc[idx[0]:idx[-1]+1].reset_index(drop=True); Bs = B.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
        dn, ds, dd, _ = bt(seg, Bs, lb, k, 1, gate)
        wn2, ws2, _, _ = bt(seg, Bs, 30, 5, 7, True)
        win = ds > ws2; wins += win
        print(f"  slice {j+1}: daily Sh {ds:5.2f} (net {dn*100:+6.1f}%) vs weekly Sh {ws2:5.2f}  {'daily' if win else 'weekly'}")
    print(f"  → daily beats weekly in {wins}/5 slices")
    print("\nVERDICT: daily-frame is only worth it if it CLEARLY beats weekly OOS + walk-forward.")


asyncio.run(main())
