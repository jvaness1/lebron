"""Build a COINBASE-tradeable universe (required: user will trade on Coinbase, US) and
test whether EXPANDING the coin count helps the live long-only strategy. Coinbase listing
comes from ccxt; price history for the backtest comes from KuCoin (prices ~identical
across venues for the same coin). Multi-horizon long-only + trend filter, train→test.

EXCHANGE_ID=kucoin python scripts/coinbase_universe.py
"""
import os, asyncio
import ccxt, numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters
COST = 15/1e4; K, R, MA, SPLIT = 5, 7, 100, 0.60
LBS = [14, 30, 60]
STAB = {"USDC","USDT","DAI","TUSD","FDUSD","USDD","WBTC","USD","PYUSD","EUR","GUSD"}
CURRENT = ["BTC","ETH","SOL","XRP","ADA","DOGE","AVAX","LINK","DOT","LTC","BCH","TRX",
           "ATOM","NEAR","APT","ARB","OP","INJ","SUI","FIL","ETC","XLM","AAVE","UNI"]


def coinbase_bases():
    for cid in ("coinbase", "coinbaseexchange"):
        try:
            ex = getattr(ccxt, cid)({"enableRateLimit": True}); ex.load_markets()
            out = set()
            for sym, m in ex.markets.items():
                if m.get("spot", True) and m.get("active", True):
                    q = m.get("quote"); b = m.get("base")
                    if b and q in ("USD", "USDC", "USDT"):
                        out.add(b)
            if out:
                return out, cid
        except Exception:
            continue
    return set(), None


def kucoin_liquid(n=80):
    cx = ccxt.kucoin({"enableRateLimit": True}); cx.load_markets()
    r = [(s.split("/")[0], t.get("quoteVolume") or 0) for s, t in cx.fetch_tickers().items()
         if s.endswith("/USDT") and s.split("/")[0] not in STAB
         and not any(x in s.split("/")[0] for x in ("3L","3S","UP","DOWN")) and (t.get("quoteVolume") or 0) >= 1e6]
    r.sort(key=lambda x: -x[1]); return [b for b, _ in r[:n]]


def momo(p, i): return sum(p.iloc[i]/p.iloc[i-lb]-1 for lb in LBS)/len(LBS)


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
        return 0.0, 0.0, 0.0
    eq = np.cumprod(1+rets); pk = np.maximum.accumulate(eq)
    return eq[-1]-1, rets.mean()/rets.std()*np.sqrt(365/R), np.max((pk-eq)/pk)


async def main():
    cb, cid = coinbase_bases()
    print(f"\nCoinbase ({cid}) lists {len(cb)} spot bases (USD/USDC/USDT).")
    on = [c for c in CURRENT if c in cb]; off = [c for c in CURRENT if c not in cb]
    print(f"Current 24 — on Coinbase: {len(on)}  |  NOT on Coinbase: {off}")

    liq = kucoin_liquid(80)
    tradeable = [b for b in liq if b in cb]            # Coinbase-listed, liquid, vol-ranked
    print(f"\nCoinbase-tradeable & liquid (KuCoin vol-ranked): {len(tradeable)} coins")
    print(f"  top 40: {tradeable[:40]}")

    # Fetch KuCoin USDT history for the tradeable set (for backtest).
    cand = tradeable[:40]; sem = asyncio.Semaphore(6)
    async def one(b):
        async with sem:
            try:
                h = await adapters.price.fetch_history(f"{b}/USDT", timeframe="1d", total=1200); return b, h["candles"]
            except Exception: return b, None
    res = await asyncio.gather(*[one(b) for b in cand])
    ser = {b: pd.Series({c[0]: c[4] for c in cs}) for b, cs in res if cs}
    full = pd.DataFrame(ser).sort_index()
    full = full.dropna(axis=1, thresh=600)
    ma = full.rolling(MA).mean(); cut = int(len(full)*SPLIT)
    print(f"\nbacktestable Coinbase coins (>=600d hist): {full.shape[1]}\n")
    print(f"{'universe':<34}{'net%':>9}{'Sharpe':>8}{'maxDD':>8}")
    # current-24 ∩ Coinbase ∩ has-history
    cur_cols = [c for c in CURRENT if c in full.columns]
    for label, cols in [(f"current ({len(cur_cols)} Coinbase coins)", cur_cols),
                        (f"expanded top-{min(20,full.shape[1])}", list(full.columns[:20])),
                        (f"expanded ALL {full.shape[1]}", list(full.columns))]:
        sub = full[cols]; msub = ma[cols]
        n,s,d = bt(sub.iloc[cut:].reset_index(drop=True), msub.iloc[cut:].reset_index(drop=True))
        print(f"{label:<34}{n*100:>8.1f}%{s:>8.2f}{d*100:>7.1f}%")
    print("\n(Expand only if more coins clearly help OOS; but switching to Coinbase-listed is REQUIRED to trade for real.)")


asyncio.run(main())
