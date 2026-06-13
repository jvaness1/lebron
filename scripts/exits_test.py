"""Honest test of intra-week exits on the live long-only strategy: daily trend-stop,
fixed stop-loss, take-profit, and combos. Daily-resolution sim between weekly rebalances.
Question: which actually improve OOS return/Sharpe/drawdown — and does take-profit kill
the momentum edge as theory predicts?

EXCHANGE_ID=kucoin python scripts/exits_test.py
"""
import os, asyncio
import ccxt, numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters
COST = 15/1e4; K, R, LB, MA, SPLIT = 5, 7, 30, 100, 0.60
STAB = {"USDC","USDT","DAI","TUSD","FDUSD","USDD","WBTC"}


def disc(n=40):
    cx = ccxt.kucoin({"enableRateLimit": True}); cx.load_markets()
    r = [(s, t.get("quoteVolume") or 0) for s, t in cx.fetch_tickers().items()
         if s.endswith("/USDT") and s.split("/")[0] not in STAB
         and not any(x in s.split("/")[0] for x in ("3L","3S","UP","DOWN")) and (t.get("quoteVolume") or 0) >= 5e6]
    r.sort(key=lambda x: -x[1]); return [s for s, _ in r[:n]]


def sim(panel, ma, daily_trendstop, sl, tp):
    """Daily loop. Weekly re-rank into top-K trend-qualified longs (1/K each). Between
    rebalances, optionally exit a held position to cash on: trend break, stop-loss, or
    take-profit. Returns daily equity-return series."""
    n = len(panel); cols = panel.columns
    pos = {}        # sym -> entry_price ; weight is 1/K each
    eq_rets = []
    prev_w = pd.Series(0.0, index=cols)
    for i in range(LB, n):
        # mark today's pre-trade return from yesterday's holdings
        if i > LB:
            r = (panel.iloc[i] / panel.iloc[i-1] - 1).reindex(cols).fillna(0)
            day = sum((1/K) * r[s] for s in pos)
        else:
            day = 0.0
        cur_w = pd.Series(0.0, index=cols);
        for s in pos: cur_w[s] = 1/K
        # ---- decisions at today's close ----
        exits = set()
        if i % R == 0:   # weekly rebalance: exit everything, re-rank
            exits = set(pos)
        else:            # between rebalances: optional intra-week exits
            for s, ep in pos.items():
                px = panel.iloc[i][s]
                if daily_trendstop and px < ma.iloc[i][s]:
                    exits.add(s)
                elif sl and (px/ep - 1) <= -sl:
                    exits.add(s)
                elif tp and (px/ep - 1) >= tp:
                    exits.add(s)
        for s in exits:
            pos.pop(s, None)
        # weekly re-entry
        if i % R == 0 and i + 1 < n:
            mom = (panel.iloc[i]/panel.iloc[i-LB]-1).dropna()
            if len(mom) >= K:
                for s in mom.sort_values().index[-K:]:
                    if panel.iloc[i][s] > ma.iloc[i][s]:
                        pos[s] = panel.iloc[i][s]
        # turnover cost from weight change
        new_w = pd.Series(0.0, index=cols)
        for s in pos: new_w[s] = 1/K
        cost = (new_w - prev_w).abs().sum() * COST
        eq_rets.append(day - cost)
        prev_w = new_w
    return np.array(eq_rets)


def perf(rets, label):
    if len(rets) < 2 or rets.std() == 0:
        return f"{label:<30} flat"
    eq = np.cumprod(1+rets); pk = np.maximum.accumulate(eq)
    sh = rets.mean()/rets.std()*np.sqrt(365)   # daily series
    return f"{label:<30}{(eq[-1]-1)*100:>9.1f}%{sh:>8.2f}{np.max((pk-eq)/pk)*100:>8.1f}%"


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
    ma = full.rolling(MA).mean(); cut = int(len(full)*SPLIT)
    p, m = full.iloc[cut:].reset_index(drop=True), ma.iloc[cut:].reset_index(drop=True)
    print(f"\nEXITS test — long-only, OOS ~{(full.index[-1]-full.index[cut])/86400000:.0f}d, {full.shape[1]} coins\n")
    print(f"{'variant':<30}{'net%':>10}{'Sharpe':>8}{'maxDD':>8}")
    print(perf(sim(p, m, False, 0, 0),       "BASELINE (weekly only)"))
    print(perf(sim(p, m, True,  0, 0),       "+ daily trend-stop"))
    print(perf(sim(p, m, False, 0.15, 0),    "+ fixed stop-loss 15%"))
    print(perf(sim(p, m, False, 0, 0.25),    "+ take-profit 25%"))
    print(perf(sim(p, m, True,  0.15, 0),    "+ trend-stop + SL15%"))
    print(perf(sim(p, m, True,  0, 0.25),    "+ trend-stop + TP25%"))
    print(perf(sim(p, m, True,  0.15, 0.25), "+ trend-stop + SL + TP"))
    print("\n(Watch what TP does to returns — momentum theory says it caps the winners that ARE the edge.)")


asyncio.run(main())
