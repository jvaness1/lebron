"""P0a — REAL walk-forward of the breadth regime gate (the cloud agent could only
do this on synthetic data). KuCoin daily, fixed gate config (no param fitting, so
every window is genuinely out-of-sample), 5 sequential time slices. Also reports
ACTIVE-PERIOD Sharpe (Sharpe over invested periods only) to settle whether P0's
high Sharpe was just a cash-period (low-variance) artifact.

EXCHANGE_ID=kucoin python scripts/walkforward_p0a.py
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
    e9,e21,e50,e200 = (p.ewm(span=s, adjust=False).mean() for s in (9,21,50,200))
    macd = p.ewm(span=12,adjust=False).mean()-p.ewm(span=26,adjust=False).mean(); hist = macd-macd.ewm(span=9,adjust=False).mean()
    sto = (p-p.rolling(14).min())/(p.rolling(14).max()-p.rolling(14).min())*100
    d = p.diff(); g = d.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); l = (-d.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean(); rs = 100-100/(1+g/l.replace(0,np.nan))
    chk = [e9>e21,e21>e50,e50>e200,p>p.rolling(20).mean(),p>p.rolling(50).mean(),rs>50,hist>0,p.pct_change(9)>0,p>p.shift(10),sto>50]
    return sum(c.astype(float) for c in chk)


def run(p, B, gate):
    """Return (per-period net returns array, in-market mask)."""
    rets, active = [], []
    prev = pd.Series(0.0, index=p.columns); i = LB
    while i + R < len(p):
        mom = (p.iloc[i]/p.iloc[i-LB]-1).dropna(); breadth = (B.iloc[i] >= 6).mean()
        if gate and breadth < 0.4:
            w = pd.Series(0.0, index=p.columns); inmkt = False
        elif len(mom) < 2*K:
            i += R; continue
        else:
            r = mom.sort_values(); w = pd.Series(0.0, index=p.columns); w[r.index[-K:]] = 1/K; w[r.index[:K]] = -1/K; inmkt = True
        fwd = (p.iloc[i+R]/p.iloc[i]-1).reindex(p.columns).fillna(0)
        rets.append((w*fwd).sum() - (w-prev).abs().sum()*COST); active.append(inmkt); prev = w; i += R
    return np.array(rets), np.array(active)


def stats(rets):
    if len(rets) < 2 or rets.std() == 0:
        return 0.0, 0.0, 0.0
    eq = np.cumprod(1+rets); pk = np.maximum.accumulate(eq)
    return eq[-1]-1, rets.mean()/rets.std()*np.sqrt(365/R), np.max((pk-eq)/pk)


async def main():
    syms = disc(); sem = asyncio.Semaphore(6)
    async def one(s):
        async with sem:
            try:
                h = await adapters.price.fetch_history(s, timeframe="1d", total=1200); return s, h["candles"]
            except Exception: return s, None
    res = await asyncio.gather(*[one(s) for s in syms])
    ser = {s: pd.Series({c[0]: c[4] for c in cs}) for s, cs in res if cs}
    panel = pd.DataFrame(ser).sort_index().dropna(axis=1, thresh=600)
    B = bscore(panel)
    print(f"\nP0a REAL walk-forward — KuCoin daily, {panel.shape[1]} coins, "
          f"{(panel.index[-1]-panel.index[0])/86400000:.0f} days total\n")

    # 5 sequential OOS slices (fixed gate config, so each is genuine OOS).
    folds = np.array_split(np.arange(len(panel)), 5)
    print(f"{'slice':<8}{'days':>6}{'gate net%':>11}{'gate Sh':>9}{'base net%':>11}{'base Sh':>9}  winner")
    gate_wins = 0
    for j, idx in enumerate(folds):
        seg = panel.iloc[idx[0]:idx[-1]+1]; Bseg = B.iloc[idx[0]:idx[-1]+1].reset_index(drop=True); seg = seg.reset_index(drop=True)
        gr, _ = run(seg, Bseg, True); br, _ = run(seg, Bseg, False)
        gnet, gsh, _ = stats(gr); bnet, bsh, _ = stats(br)
        win = "gate" if gsh > bsh else "base"; gate_wins += (gsh > bsh)
        days = (panel.iloc[idx[-1]:idx[-1]+1].index[0]-panel.iloc[idx[0]:idx[0]+1].index[0])/86400000
        print(f"{j+1:<8}{days:>6.0f}{gnet*100:>10.1f}%{gsh:>9.2f}{bnet*100:>10.1f}%{bsh:>9.2f}  {win}")
    print(f"\n  gate beats baseline in {gate_wins}/5 sequential OOS slices")

    # Active-period Sharpe on the FULL series (settles the cash-artifact question).
    Bf = B.reset_index(drop=True); pf = panel.reset_index(drop=True)
    gr, act = run(pf, Bf, True)
    full_net, full_sh, full_dd = stats(gr)
    active_only = gr[act]
    act_net = np.prod(1+active_only)-1 if len(active_only) else 0
    act_sh = active_only.mean()/active_only.std()*np.sqrt(365/R) if len(active_only) > 1 and active_only.std() > 0 else 0
    print(f"\n  FULL gate: net {full_net*100:+.1f}%  Sharpe {full_sh:.2f}  maxDD {full_dd*100:.1f}%  "
          f"({act.sum()}/{len(act)} periods in-market)")
    print(f"  ACTIVE-PERIOD ONLY (cash periods removed): net {act_net*100:+.1f}%  Sharpe {act_sh:.2f}")
    print("  → if active-period Sharpe is still high, the edge is REAL, not a cash-variance artifact.")


asyncio.run(main())
