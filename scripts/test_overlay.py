"""P0: does an indicator/regime overlay beat the pure cross-sectional momentum
baseline out-of-sample? Honest train->test: any threshold is chosen on TRAIN only.

Baseline   : rank by 30d return, long top-K / short bottom-K (the live strategy).
+Confirm   : among top-K only long those with bull-score >= thr (train-picked);
             among bottom-K only short those with bull-score <= (10-thr).
+Tilt      : weight longs by bull-score, shorts by (10-bull), dollar-neutral.
+Regime    : hold the book only when market breadth (% universe bullish) >= thr
             (train-picked), else flat.

bull-score = count of 10 daily bullish checks (EMA stack, RSI>50, MACD>0, ROC>0,
price>SMA20/50, close>10d ago, stoch>50) -- the same idea as the FPU-MAX matrix's
bull_count, computed in Python from KuCoin data.

EXCHANGE_ID=kucoin python scripts/test_overlay.py
"""
import os, asyncio, itertools
import ccxt, numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters
from hermes_trading.loop import load_strategy

_s = load_strategy()
COST = ((_s.get("costs") or {}).get("fees_bps", 10) + (_s.get("costs") or {}).get("slippage_bps", 5)) / 1e4
K, R, LB, SPLIT = 5, 7, 30, 0.60
STABLES = {"USDC","USDT","DAI","TUSD","FDUSD","USDD","PYUSD","EUR","BUSD","WBTC"}


def discover(n=40):
    cx = ccxt.kucoin({"enableRateLimit": True}); cx.load_markets()
    rows = [(s, t.get("quoteVolume") or 0) for s, t in cx.fetch_tickers().items()
            if s.endswith("/USDT") and s.split("/")[0] not in STABLES
            and not any(x in s.split("/")[0] for x in ("3L","3S","UP","DOWN"))
            and (t.get("quoteVolume") or 0) >= 5e6]
    rows.sort(key=lambda r: -r[1]); return [s for s, _ in rows[:n]]


def rsi(df, n=14):
    d = df.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + g/l.replace(0, np.nan))


def bull_score(close):
    e9, e21, e50, e200 = (close.ewm(span=s, adjust=False).mean() for s in (9,21,50,200))
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    sto = (close - close.rolling(14).min()) / (close.rolling(14).max() - close.rolling(14).min()) * 100
    checks = [e9 > e21, e21 > e50, e50 > e200, close > close.rolling(20).mean(),
              close > close.rolling(50).mean(), rsi(close) > 50, hist > 0,
              close.pct_change(9) > 0, close > close.shift(10), sto > 50]
    return sum(c.astype(float) for c in checks)   # 0..10 panel


def equity(panel, bull, weight_fn, lo, hi, **kw):
    eq, prev = [1.0], pd.Series(0.0, index=panel.columns)
    i = max(LB, lo)
    while i + R < hi:
        mom = panel.iloc[i] / panel.iloc[i-LB] - 1
        w = weight_fn(mom.dropna(), bull.iloc[i], **kw)
        if w is None:
            w = pd.Series(0.0, index=panel.columns)
        w = w.reindex(panel.columns).fillna(0.0)
        fwd = (panel.iloc[i+R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0.0)
        eq.append(eq[-1] * (1 + (w*fwd).sum() - (w-prev).abs().sum()*COST))
        prev = w; i += R
    eq = np.array(eq); r = np.diff(eq)/eq[:-1]
    sh = r.mean()/r.std()*np.sqrt(365/R) if len(r) > 1 and r.std() > 0 else 0
    peak = np.maximum.accumulate(eq); mdd = np.max((peak-eq)/peak) if len(eq) else 0
    return eq[-1]/eq[0]-1, sh, mdd, len(r)


def w_base(mom, bull):
    if len(mom) < 2*K: return None
    r = mom.sort_values(); w = pd.Series(0.0, index=mom.index)
    w[r.index[-K:]] = 1/K; w[r.index[:K]] = -1/K; return w

def w_confirm(mom, bull, thr=6):
    if len(mom) < 2*K: return None
    r = mom.sort_values()
    longs = [s for s in r.index[-K:] if bull.get(s, 0) >= thr]
    shorts = [s for s in r.index[:K] if bull.get(s, 0) <= (10-thr)]
    w = pd.Series(0.0, index=mom.index)
    if longs:  w[longs]  = 1/len(longs)
    if shorts: w[shorts] = -1/len(shorts)
    return w

def w_tilt(mom, bull):
    if len(mom) < 2*K: return None
    r = mom.sort_values(); longs, shorts = list(r.index[-K:]), list(r.index[:K])
    lb = bull.reindex(longs).fillna(0) + 0.5
    sb = (10 - bull.reindex(shorts).fillna(10)) + 0.5
    w = pd.Series(0.0, index=mom.index)
    w[longs] = lb/lb.sum(); w[shorts] = -sb/sb.sum(); return w

def w_regime(mom, bull, thr=0.5):
    breadth = (bull >= 6).mean()
    if breadth < thr: return pd.Series(0.0, index=mom.index)
    return w_base(mom, bull)


async def main():
    syms = discover(); sem = asyncio.Semaphore(6)
    async def one(s):
        async with sem:
            try:
                h = await adapters.price.fetch_history(s, timeframe="1d", total=1200)
                return s, h["candles"]
            except: return s, None
    res = await asyncio.gather(*[one(s) for s in syms])
    ser = {s: pd.Series({c[0]: c[4] for c in cs}) for s, cs in res if cs}
    panel = pd.DataFrame(ser).sort_index().dropna(axis=1, thresh=600)
    bull = bull_score(panel)
    cut = int(len(panel)*SPLIT)
    days = (panel.index[-1]-panel.index[cut])/86400000
    print(f"\n{panel.shape[1]} coins, cost {COST*1e4:.0f}bps/side, K{K} R{R}d LB{LB}d")
    print(f"OOS = last ~{days:.0f} days; train/test split at {SPLIT:.0%}\n")
    def show(name, fn, tr, **kw):
        net, sh, mdd, n = equity(panel, bull, fn, cut, len(panel), **kw)
        tag = ""
        if tr is not None:
            tag = "  WORSE OOS" if sh <= tr+0.05 else "  better OOS"
        print(f"  {name:<26} OOS: net {net*100:+6.1f}%  Sharpe {sh:4.2f}  maxDD {mdd*100:4.1f}%  n{n}{tag}")
        return sh
    # baseline
    base_sh = show("baseline (pure momentum)", w_base, None)
    # +confirm: pick thr on TRAIN
    best = max(([thr, equity(panel, bull, w_confirm, LB, cut, thr=thr)[1]] for thr in (5,6,7)), key=lambda x: x[1])
    print(f"  [confirm thr picked on train = {best[0]}]")
    show(f"+confirmation filter", w_confirm, base_sh, thr=best[0])
    # +tilt: no param
    show("+conviction tilt", w_tilt, base_sh)
    # +regime: pick thr on TRAIN
    bestr = max(([thr, equity(panel, bull, w_regime, LB, cut, thr=thr)[1]] for thr in (0.4,0.5,0.6)), key=lambda x: x[1])
    print(f"  [regime breadth thr picked on train = {bestr[0]}]")
    show("+regime gate", w_regime, base_sh, thr=bestr[0])

    # ROBUSTNESS: is the WHOLE neighbourhood good OOS, or just one threshold?
    print("\n  regime-gate OOS across thresholds (robustness):")
    for thr in (0.3, 0.4, 0.5, 0.6, 0.7):
        net, sh, mdd, n = equity(panel, bull, w_regime, cut, len(panel), thr=thr)
        # exposure: fraction of rebalances actually holding the book
        i, held, tot = max(LB, cut), 0, 0
        while i + R < len(panel):
            if (bull.iloc[i] >= 6).mean() >= thr: held += 1
            tot += 1; i += R
        print(f"    thr {thr:.1f}: net {net*100:+6.1f}%  Sharpe {sh:4.2f}  maxDD {mdd*100:4.1f}%  in-market {held/tot*100:3.0f}% of the time")
    print("\n(Verdict: an overlay only 'wins' if its OOS Sharpe clearly beats baseline ACROSS the neighbourhood.)")

asyncio.run(main())
