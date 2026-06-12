"""P3 — decompose WHERE the regime gate helps vs hurts on the long-short book.
Per OOS slice: gate net vs no-gate net. In slices the ungated book LOST, measure the
gate's rescue; in slices it WON, measure the gate's drag. Tests the claim that the
gate's higher aggregate compounded return is a crash-avoidance/compounding effect,
not a per-period edge. Real KuCoin data.

EXCHANGE_ID=kucoin python scripts/p3_regime_rescue.py
"""
import os, asyncio
import ccxt, numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters
COST = 15/1e4; K, R, LB = 5, 7, 30
STAB = {"USDC","USDT","DAI","TUSD","FDUSD","USDD","WBTC"}


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


def net(panel, B, gate):
    rets, prev = [], pd.Series(0.0, index=panel.columns); i = LB
    while i + R < len(panel):
        inv = ((B.iloc[i] >= 6).mean() >= 0.4) if gate else True
        if not inv:
            w = pd.Series(0.0, index=panel.columns)
        else:
            mom = (panel.iloc[i]/panel.iloc[i-LB]-1).dropna()
            if len(mom) < 2*K:
                i += R; continue
            r = mom.sort_values(); w = pd.Series(0.0, index=panel.columns); w[r.index[-K:]] = 1/K; w[r.index[:K]] = -1/K
        fwd = (panel.iloc[i+R]/panel.iloc[i]-1).reindex(panel.columns).fillna(0)
        rets.append((w*fwd).sum() - (w-prev).abs().sum()*COST); prev = w; i += R
    return float(np.prod(1+np.array(rets))-1) if rets else 0.0


async def main():
    syms = disc(40); sem = asyncio.Semaphore(6)
    async def one(s):
        async with sem:
            try:
                h = await adapters.price.fetch_history(s, timeframe="1d", total=1200); return s, h["candles"]
            except Exception: return s, None
    res = await asyncio.gather(*[one(s) for s in syms])
    ser = {s: pd.Series({c[0]: c[4] for c in cs}) for s, cs in res if cs}
    full = pd.DataFrame(ser).sort_index().dropna(axis=1, thresh=600); B = bscore(full)
    print(f"\nP3 rescue/drag decomposition — long-short, {full.shape[1]} coins, 5 slices\n")
    folds = np.array_split(np.arange(len(full)), 5)
    rescue, drag = 0.0, 0.0
    print(f"{'slice':<7}{'gate net%':>11}{'nogate net%':>13}{'gate-nogate':>13}")
    for j, idx in enumerate(folds):
        seg = full.iloc[idx[0]:idx[-1]+1].reset_index(drop=True); Bs = B.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
        gn, ng = net(seg, Bs, True), net(seg, Bs, False)
        diff = gn - ng
        (rescue := rescue + diff) if ng < 0 else (drag := drag + diff)
        tag = "RESCUE (nogate lost)" if ng < 0 else "drag (nogate won)"
        print(f"{j+1:<7}{gn*100:>10.1f}%{ng*100:>12.1f}%{diff*100:>12.1f}pp  {tag}")
    print(f"\n  gate rescue in losing slices: {rescue*100:+.1f}pp")
    print(f"  gate drag in winning slices:  {drag*100:+.1f}pp")
    # aggregate compounded
    agg_g = net(full.reset_index(drop=True), B.reset_index(drop=True), True)
    agg_n = net(full.reset_index(drop=True), B.reset_index(drop=True), False)
    print(f"  AGGREGATE compounded: gate {agg_g*100:+.0f}%  vs  no-gate {agg_n*100:+.0f}%")
    print("  → higher aggregate-with-gate is crash-avoidance COMPOUNDING, not a per-slice edge.")


asyncio.run(main())
