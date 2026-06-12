"""Exhaust the remaining improvement ideas vs the LIVE config, honestly.
Each variant changes ONE thing from the live baseline; OOS only; one 60/40 split
(caveat: single window). A variant is only worth deploying if it clearly beats the
baseline on Sharpe AND return. Real KuCoin data.

EXCHANGE_ID=kucoin python scripts/improve_sweep.py
"""
import os, asyncio
import ccxt, numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters
COST = 15/1e4; SPLIT = 0.60
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


def stats(rets):
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return 0.0, 0.0, 0.0
    eq = np.cumprod(1+rets); pk = np.maximum.accumulate(eq)
    return eq[-1]-1, rets.mean()/rets.std()*np.sqrt(365/7), np.max((pk-eq)/pk)


def backtest(panel, B, btc, btcma, cfg):
    """One config over the whole given (already test-sliced, reset-index) frame."""
    LB, K, skip, R = cfg["lb"], cfg["k"], cfg["skip"], cfg["rebal"]
    gate, vol_size = cfg["gate"], cfg.get("vol", False)
    vols = panel.pct_change().rolling(20).std()
    rets, prev = [], pd.Series(0.0, index=panel.columns)
    i = LB + skip
    while i + R < len(panel):
        invested = True
        if gate == "breadth":
            invested = (B.iloc[i] >= 6).mean() >= 0.4
        elif gate == "btc":
            invested = btc.iloc[i] > btcma.iloc[i]
        if not invested:
            w = pd.Series(0.0, index=panel.columns)
        else:
            mom = (panel.iloc[i-skip] / panel.iloc[i-LB-skip] - 1).dropna()
            if len(mom) < 2*K:
                i += R; continue
            r = mom.sort_values(); longs, shorts = r.index[-K:], r.index[:K]
            w = pd.Series(0.0, index=panel.columns)
            if vol_size:
                lv = (1/vols.iloc[i].reindex(longs)).replace([np.inf, np.nan], 0); lv = lv/lv.sum() if lv.sum() else lv
                sv = (1/vols.iloc[i].reindex(shorts)).replace([np.inf, np.nan], 0); sv = sv/sv.sum() if sv.sum() else sv
                w[longs] = lv.fillna(1/K); w[shorts] = -sv.fillna(1/K)
            else:
                w[longs] = 1/K; w[shorts] = -1/K
        fwd = (panel.iloc[i+R]/panel.iloc[i]-1).reindex(panel.columns).fillna(0)
        rets.append((w*fwd).sum() - (w-prev).abs().sum()*COST); prev = w; i += R
    return stats(rets)


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
    btc_full = full["BTC/USDT"] if "BTC/USDT" in full else full.iloc[:, 0]
    btcma_full = btc_full.rolling(100).mean()
    B_full = bscore(full)
    cut = int(len(full)*SPLIT)
    def sl(df): return df.iloc[cut:].reset_index(drop=True)
    p24 = sl(full.iloc[:, :24]); p40 = sl(full)
    B24, B40 = sl(B_full.iloc[:]) if False else None, None  # recompute per universe below
    B24 = bscore(full.iloc[:, :24]).iloc[cut:].reset_index(drop=True)
    B40 = bscore(full).iloc[cut:].reset_index(drop=True)
    btc, btcma = sl(btc_full), sl(btcma_full)

    base = dict(lb=30, k=5, skip=0, rebal=7, gate="breadth", vol=False)
    print(f"\nIMPROVE sweep — OOS ~{(full.index[-1]-full.index[cut])/86400000:.0f}d, "
          f"{p24.shape[1]} coins (40 for wider), 15bps/side\n")
    print(f"{'variant':<32}{'net%':>9}{'Sharpe':>8}{'maxDD':>8}")
    def show(name, panel, Bx, cfg):
        net, sh, mdd = backtest(panel, Bx, btc, btcma, cfg)
        print(f"{name:<32}{net*100:>8.1f}%{sh:>8.2f}{mdd*100:>7.1f}%")
        return sh
    b = show("BASELINE (live)", p24, B24, base)
    show("+ vol-scaled sizing (P2)", p24, B24, dict(base, vol=True))
    show("+ skip 7d (P5)", p24, B24, dict(base, skip=7))
    show("+ wider universe 40 (P4)", p40, B40, base)
    show("+ BTC-trend gate (P0b)", p24, B24, dict(base, gate="btc"))
    show("+ lookback 60", p24, B24, dict(base, lb=60))
    show("+ K=8", p24, B24, dict(base, k=8))
    show("+ no gate (reference)", p24, B24, dict(base, gate="none"))
    print(f"\n(baseline Sharpe {b:.2f}; a variant only deploys if it CLEARLY beats it on both Sharpe & return, single-window caveat.)")


asyncio.run(main())
