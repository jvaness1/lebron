"""P11 — Drawdown smoothing for the LIVE long-only config (v06).

Live config: long-only, multi-horizon momentum (avg 14/30/60d), top-5, trend filter
(px>100d MA), weekly rebalance, EQUAL weight (1/5 = 20% per name), cost 15bps/side.
maxDD on the current 1200-bar window is ~38% (weekly-sampled). For STEADY INCOME, DD
depth + recovery time matter more than peak return. This script tests DD-smoothing
overlays and asks: can we cut DD without killing Sharpe, honestly OOS?

Overlays tested (each adds a parameter -> selected on TRAIN only, reported on TEST):
  1. BOOK VOL-TARGETING: scale book exposure by clip(target_vol/trailing_book_vol, 0, 1)
     (long-only spot -> no leverage, cap 1.0). De-risks in high-vol regimes.
  2. PORTFOLIO TRAILING STOP: if book equity falls >X% from its running peak, go to
     cash; re-enter when momentum re-selects above-trend names (next rebalance check).
  3. PER-NAME WEIGHT CAP / PARTIAL CASH: cap each name < 20%, remainder cash. NOTE this
     is a uniform linear de-leverage for equal-weight K5 -> Sharpe is invariant by
     construction; reported only for the absolute held-death TAIL bound (feeds P10/P14).

Honest train->test (60/40) + 5-slice walk-forward. EXACT live universe & config.
EXCHANGE_ID=kucoin python scripts/p11_dd_smoothing.py
"""
import os, asyncio, yaml
import numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters

K, R, SPLIT = 5, 7, 0.60
MA_DAYS = 100
VOL_WIN = 30                       # trailing daily window for realized book vol
COST = 15 / 1e4                    # live backtest assumption, per side
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def live_universe():
    with open(os.path.join(REPO, "state", "strategy.yaml")) as f:
        return yaml.safe_load(f)["universe"]


def multi_score(panel, i):
    return ((panel.iloc[i]/panel.iloc[i-14]-1) + (panel.iloc[i]/panel.iloc[i-30]-1)
            + (panel.iloc[i]/panel.iloc[i-60]-1)) / 3


def select(panel, ma, i):
    """Unscaled equal-weight selection (the live signal). Returns weight Series."""
    sc = multi_score(panel, i).dropna()
    w = pd.Series(0.0, index=panel.columns)
    if len(sc) >= K:
        for s in sc.sort_values().index[-K:]:
            if panel.iloc[i][s] > ma.iloc[i][s]:
                w[s] = 1/K
    return w


def book_vol_ann(panel, i, w):
    """Causal annualized vol of the book defined by w, over trailing VOL_WIN days."""
    if w.sum() == 0:
        return 0.0
    dret = panel.iloc[i-VOL_WIN:i+1].pct_change().dropna()
    if len(dret) < 5:
        return 0.0
    bret = (dret * w).sum(axis=1)
    return float(bret.std() * np.sqrt(365))


def run(panel, ma, cost, mode="base", target=None, stop=None, cap=None):
    """Generic weekly engine with a DD-smoothing overlay. Returns (net,Sharpe,maxDD,
    worst_week, avg_turnover).

    'ddgate' = causal MARKET-drawdown gate: hold cash whenever an equal-weight market
    index sits >stop below its running peak, re-enter once it recovers inside the band.
    The gate keys off the market index (which always updates), NOT the halted book's
    frozen equity, so it genuinely re-enters (a stop on the cash-frozen book never could).
    """
    # equal-weight market index (causal) for the drawdown gate
    mret = panel.pct_change().mean(axis=1).fillna(0)
    midx = (1+mret).cumprod()
    mpeak = midx.cummax()
    mdd = (mpeak - midx) / mpeak

    rets = []
    prev = pd.Series(0.0, index=panel.columns)
    i = 60
    while i + R < len(panel):
        w = select(panel, ma, i)

        if mode == "voltarget" and w.sum() > 0:
            v = book_vol_ann(panel, i, w)
            scal = min(1.0, target / v) if v > 0 else 1.0
            w = w * scal
        elif mode == "cap":
            w = w.clip(upper=cap)                       # remainder -> cash
        elif mode == "ddgate":
            if mdd.iloc[i] >= stop:                     # market deep in drawdown -> cash
                w = pd.Series(0.0, index=panel.columns)

        fwd = (panel.iloc[i+R]/panel.iloc[i]-1).reindex(panel.columns).fillna(0)
        gross = (w*fwd).sum()
        turn = (w-prev).abs().sum()
        rets.append(gross - turn*cost)
        prev = w; i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    e = np.cumprod(1+rets); pk = np.maximum.accumulate(e)
    sharpe = rets.mean()/rets.std()*np.sqrt(365/R)
    return (e[-1]-1, sharpe, float(np.max((pk-e)/pk)), float(rets.min()),
            float(np.mean([abs(x) for x in rets])))


async def main():
    syms = live_universe()
    sem = asyncio.Semaphore(6)
    async def one(s):
        async with sem:
            try:
                h = await adapters.price.fetch_history(s, timeframe="1d", total=1200)
                return s, h["candles"]
            except Exception:
                return s, None
    res = await asyncio.gather(*[one(s) for s in syms])
    ser = {s: pd.Series({c[0]: c[4] for c in cs}) for s, cs in res if cs}
    full = pd.DataFrame(ser).sort_index().dropna(axis=1, thresh=600)
    ma = full.rolling(MA_DAYS).mean()
    cut = int(len(full)*SPLIT)
    ptr, mtr = full.iloc[:cut].reset_index(drop=True), ma.iloc[:cut].reset_index(drop=True)
    pte, mte = full.iloc[cut:].reset_index(drop=True), ma.iloc[cut:].reset_index(drop=True)
    oos_days = (full.index[-1]-full.index[cut])/86400000
    print(f"\nP11 DD-smoothing — LIVE config (multi-horizon K5 weekly trend-filtered)")
    print(f"{full.shape[1]}/{len(syms)} coins, OOS ~{oos_days:.0f}d, cost 15bps/side\n")

    def fmt(tag, r):
        n, s, d, ww, _ = r
        print(f"  {tag:<28}net {n*100:>7.1f}%  Sharpe {s:>5.2f}  maxDD {d*100:>5.1f}%  "
              f"worstWk {ww*100:>6.1f}%")

    # ---- baseline (live) ----
    print("BASELINE (live config), evaluated on TRAIN then TEST:")
    fmt("train", run(ptr, mtr, COST))
    fmt("TEST (OOS)", run(pte, mte, COST))
    base_test = run(pte, mte, COST)

    # ---- 1) VOL-TARGETING: pick target on TRAIN (best Sharpe w/ DD<=baseline), report TEST
    print("\n[1] BOOK VOL-TARGETING — sweep target ann.vol; SELECT on TRAIN:")
    targets = [0.40, 0.50, 0.60, 0.70, 0.80, 1.00]
    base_tr = run(ptr, mtr, COST)
    best = None
    for t in targets:
        rtr = run(ptr, mtr, COST, mode="voltarget", target=t)
        # selection rule: maximize train Sharpe subject to DD <= baseline train DD
        ok = rtr[2] <= base_tr[2] + 1e-9
        flag = " (DD<=base)" if ok else ""
        print(f"    train target={t:.2f}: Sharpe {rtr[1]:.2f} DD {rtr[2]*100:.1f}% net {rtr[0]*100:.1f}%{flag}")
        if ok and (best is None or rtr[1] > best[1]):
            best = (t, rtr[1])
    if best is None:                # fallback: best train Sharpe outright
        best = max(((t, run(ptr, mtr, COST, mode="voltarget", target=t)[1]) for t in targets),
                   key=lambda x: x[1])
    vt = best[0]
    print(f"  -> picked target={vt:.2f} on TRAIN. OOS result:")
    fmt(f"TEST voltarget={vt:.2f}", run(pte, mte, COST, mode="voltarget", target=vt))

    # ---- 2) MARKET-DRAWDOWN GATE: pick X on TRAIN, report TEST
    print("\n[2] MARKET-DRAWDOWN GATE — cash when mkt index >X% off peak; SELECT on TRAIN:")
    stops = [0.10, 0.15, 0.20, 0.25, 0.30]
    bests = None
    for x in stops:
        rtr = run(ptr, mtr, COST, mode="ddgate", stop=x)
        print(f"    train gate={x*100:.0f}%: Sharpe {rtr[1]:.2f} DD {rtr[2]*100:.1f}% net {rtr[0]*100:.1f}%")
        if bests is None or rtr[1] > bests[1]:
            bests = (x, rtr[1])
    sx = bests[0]
    print(f"  -> picked gate={sx*100:.0f}% on TRAIN. OOS result:")
    fmt(f"TEST ddgate={sx*100:.0f}%", run(pte, mte, COST, mode="ddgate", stop=sx))

    # ---- 3) PER-NAME WEIGHT CAP (tail bound; Sharpe-invariant for equal-weight) ----
    print("\n[3] PER-NAME WEIGHT CAP / partial cash (absolute held-death TAIL bound):")
    for c in [0.20, 0.15, 0.12, 0.10]:
        fmt(f"TEST cap={c:.2f} (inv {min(1,5*c)*100:.0f}%)", run(pte, mte, COST, mode="cap", cap=c))

    # ---- walk-forward the two real overlays at their TRAIN-picked params ----
    print("\n[WF] 5-slice walk-forward (Sharpe / maxDD%):")
    folds = np.array_split(np.arange(len(full)), 5)
    for label, kw in [("baseline", {}),
                      (f"voltarget={vt:.2f}", dict(mode="voltarget", target=vt)),
                      (f"ddgate={sx*100:.0f}%", dict(mode="ddgate", stop=sx))]:
        sh, dd, nets = [], [], []
        for idx in folds:
            seg = full.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
            ms = ma.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
            n, s, d, _, _ = run(seg, ms, COST, **kw)
            sh.append(round(float(s), 2)); dd.append(round(float(d*100), 1)); nets.append(round(float(n*100), 1))
        pos = sum(1 for x in sh if x > 0)
        print(f"  {label:<16} Sharpe {sh} ({pos}/5+)  maxDD {dd}  net% {nets}")


asyncio.run(main())
