"""Design a LONG-ONLY spot strategy for a US trader (no shorting). Plain long-only
momentum is real but has ~46% DD. Test ways to tame it — the main lever is an
ABSOLUTE-momentum / trend filter (dual momentum): only hold a top-ranked coin if it's
also in its own uptrend, else hold cash for that slot. Honest train→test, OOS only.

EXCHANGE_ID=kucoin python scripts/longonly_design.py
"""
import os, asyncio
import ccxt, numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters
COST = 15/1e4; K, R, LB, SPLIT = 5, 7, 30, 0.60
STAB = {"USDC","USDT","DAI","TUSD","FDUSD","USDD","WBTC"}


def disc(n=40):
    cx = ccxt.kucoin({"enableRateLimit": True}); cx.load_markets()
    r = [(s, t.get("quoteVolume") or 0) for s, t in cx.fetch_tickers().items()
         if s.endswith("/USDT") and s.split("/")[0] not in STAB
         and not any(x in s.split("/")[0] for x in ("3L","3S","UP","DOWN")) and (t.get("quoteVolume") or 0) >= 5e6]
    r.sort(key=lambda x: -x[1]); return [s for s, _ in r[:n]]


def perf(rets, label, R=7):
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return f"{label:<34} (flat)"
    eq = np.cumprod(1+rets); pk = np.maximum.accumulate(eq)
    sh = rets.mean()/rets.std()*np.sqrt(365/R); mdd = np.max((pk-eq)/pk)
    cagr = eq[-1]**(365/(len(rets)*R)) - 1
    return f"{label:<34}{(eq[-1]-1)*100:>8.1f}%{cagr*100:>8.1f}%{sh:>8.2f}{mdd*100:>8.1f}%"


def backtest(panel, ma100, ma_btc_ok, mode):
    """mode: plain | absMA | absRet | btcregime | cashasset"""
    rets, prev = [], pd.Series(0.0, index=panel.columns)
    i = LB
    while i + R < len(panel):
        mom = (panel.iloc[i]/panel.iloc[i-LB]-1).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if mode == "btcregime" and not ma_btc_ok.iloc[i]:
            pass  # all cash
        elif len(mom) >= K:
            top = mom.sort_values().index[-K:]
            for s in top:
                ok = True
                if mode == "absMA":
                    ok = panel.iloc[i][s] > ma100.iloc[i][s]
                elif mode == "absRet":
                    ok = mom[s] > 0
                elif mode == "cashasset":
                    ok = mom[s] > 0
                if ok:
                    w[s] = 1.0 / K   # else that slot stays cash
        fwd = (panel.iloc[i+R]/panel.iloc[i]-1).reindex(panel.columns).fillna(0)
        rets.append((w*fwd).sum() - (w-prev).abs().sum()*COST); prev = w; i += R
    return rets


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
    ma100 = full.rolling(100).mean()
    btc = full["BTC/USDT"] if "BTC/USDT" in full else full.iloc[:, 0]
    btc_ok = btc > btc.rolling(100).mean()
    cut = int(len(full)*SPLIT)
    def sl(d): return d.iloc[cut:].reset_index(drop=True)
    p, m, bok = sl(full), sl(ma100), sl(btc_ok)
    days = (full.index[-1]-full.index[cut])/86400000
    print(f"\nLONG-ONLY spot design — OOS ~{days:.0f}d, {full.shape[1]} coins, weekly, K{K}, 15bps/side\n")
    print(f"{'variant':<34}{'net%':>9}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>9}")
    for mode, name in [("plain","plain long-only (P7 baseline)"),
                       ("absRet","+ abs-momentum filter (own ret>0)"),
                       ("absMA","+ trend filter (price>100d MA)"),
                       ("btcregime","+ BTC-regime gate (BTC>100d MA)")]:
        print(perf(backtest(p, m, bok, mode), name))
    # Benchmarks
    ewr = p.pct_change().mean(axis=1).fillna(0).tolist()
    print(perf(ewr, "benchmark: equal-weight hold", R=1))
    btcr = sl(btc).pct_change().fillna(0).tolist()
    print(perf(btcr, "benchmark: BTC buy & hold", R=1))
    print("\n(Goal: a long-only variant that keeps decent CAGR/Sharpe but slashes the ~46% DD.)")


asyncio.run(main())
