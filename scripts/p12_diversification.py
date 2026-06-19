"""P12 — Signal diversification for the LIVE long-only book (v06).

Momentum has multi-month droughts — the chief threat to "consistent income". This
script tests whether blending the live momentum sleeve with a LOW-CORRELATION second
long-only sleeve on the SAME universe raises the WORST quarter (consistency) even at
some cost to peak return.

Sleeves (all long-only spot, top-5, equal weight, weekly rebal, 15bps/side):
  MOM  = the live signal: avg(14/30/60d ret) -> top5, hold only if px>100d MA, else cash.
  REV  = short-term reversal: long the K MOST-oversold by short return (sweep lookback,
         optional px>100d MA "buy-the-dip-in-uptrend" gate).
  LV   = low-volatility: long the K lowest 30d realized-vol names.

Method (NON-NEGOTIABLE):
  - Sleeve params + blend weight selected on TRAIN (60%) only; reported on TEST.
  - Report sleeve-return CORRELATION (train) and a 5-slice walk-forward of the blend.
  - "Worst quarter" = min compounded return over rolling 13-rebalance (~91d) windows.

EXCHANGE_ID=kucoin python scripts/p12_diversification.py
"""
import os, asyncio, yaml
import numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters

K, R, SPLIT = 5, 7, 0.60
MA_DAYS = 100
I0 = 100                            # first rebalance index (need 100d MA + 60d mom)
COST = 15 / 1e4
QWIN = 13                           # ~quarter in weekly rebalances
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def live_universe():
    with open(os.path.join(REPO, "state", "strategy.yaml")) as f:
        return yaml.safe_load(f)["universe"]


def mom_w(panel, ma, i):
    sc = ((panel.iloc[i]/panel.iloc[i-14]-1) + (panel.iloc[i]/panel.iloc[i-30]-1)
          + (panel.iloc[i]/panel.iloc[i-60]-1)) / 3
    sc = sc.dropna()
    w = pd.Series(0.0, index=panel.columns)
    if len(sc) >= K:
        for s in sc.sort_values().index[-K:]:
            if panel.iloc[i][s] > ma.iloc[i][s]:
                w[s] = 1/K
    return w


def rev_w(panel, ma, i, lb, use_trend):
    r = (panel.iloc[i]/panel.iloc[i-lb]-1).dropna()
    w = pd.Series(0.0, index=panel.columns)
    if len(r) >= K:
        for s in r.sort_values().index[:K]:        # most oversold = lowest recent return
            if (not use_trend) or panel.iloc[i][s] > ma.iloc[i][s]:
                w[s] = 1/K
    return w


def lv_w(panel, i, vw):
    dret = panel.iloc[i-vw:i+1].pct_change().dropna()
    vol = dret.std().dropna()
    w = pd.Series(0.0, index=panel.columns)
    if len(vol) >= K:
        for s in vol.sort_values().index[:K]:
            w[s] = 1/K
    return w


def sleeve_returns(panel, ma, kind, **kw):
    """Return the per-rebalance NET return array (costs on turnover within the sleeve)."""
    rets = []
    prev = pd.Series(0.0, index=panel.columns)
    i = I0
    while i + R < len(panel):
        if kind == "mom":
            w = mom_w(panel, ma, i)
        elif kind == "rev":
            w = rev_w(panel, ma, i, kw["lb"], kw["use_trend"])
        elif kind == "lv":
            w = lv_w(panel, i, kw["vw"])
        fwd = (panel.iloc[i+R]/panel.iloc[i]-1).reindex(panel.columns).fillna(0)
        turn = (w-prev).abs().sum()
        rets.append((w*fwd).sum() - turn*COST)
        prev = w; i += R
    return np.array(rets)


def stats(rets):
    if len(rets) < 2 or np.std(rets) == 0:
        return dict(net=0.0, sharpe=0.0, mdd=0.0, wq=0.0, ww=0.0)
    e = np.cumprod(1+rets); pk = np.maximum.accumulate(e)
    sharpe = rets.mean()/rets.std()*np.sqrt(365/R)
    mdd = float(np.max((pk-e)/pk))
    # worst rolling ~quarter (13-rebalance compounded return)
    wq = 0.0
    if len(rets) >= QWIN:
        wins = [np.prod(1+rets[j:j+QWIN])-1 for j in range(len(rets)-QWIN+1)]
        wq = float(min(wins))
    return dict(net=float(e[-1]-1), sharpe=float(sharpe), mdd=mdd, wq=wq, ww=float(rets.min()))


def blend(rm, rs, alpha):
    return alpha*rm + (1-alpha)*rs


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
    print(f"\nP12 signal diversification — LIVE long-only book")
    print(f"{full.shape[1]}/{len(syms)} coins, OOS ~{oos_days:.0f}d, cost 15bps/side, "
          f"quarter={QWIN} rebals\n")

    def show(tag, st):
        print(f"  {tag:<26}net {st['net']*100:>7.1f}%  Sharpe {st['sharpe']:>5.2f}  "
              f"maxDD {st['mdd']*100:>5.1f}%  worstQ {st['wq']*100:>6.1f}%  "
              f"worstWk {st['ww']*100:>6.1f}%")

    # ---- MOM baseline ----
    mom_tr = sleeve_returns(ptr, mtr, "mom")
    mom_te = sleeve_returns(pte, mte, "mom")
    print("MOMENTUM sleeve (= LIVE config):")
    show("train", stats(mom_tr)); show("TEST (OOS)", stats(mom_te))

    # ---- candidate second sleeves: select params on TRAIN by Sharpe ----
    print("\nSECOND-SLEEVE CANDIDATES (selected on TRAIN; corr = vs MOM train returns):")
    cands = []
    for lb in (3, 5, 7, 10):
        for ut in (False, True):
            r_tr = sleeve_returns(ptr, mtr, "rev", lb=lb, use_trend=ut)
            corr = float(np.corrcoef(mom_tr[:len(r_tr)], r_tr)[0, 1])
            st = stats(r_tr)
            tag = f"REV lb{lb} trend{'Y' if ut else 'N'}"
            print(f"  {tag:<22} train Sharpe {st['sharpe']:>5.2f}  net {st['net']*100:>7.1f}%  "
                  f"corr {corr:>5.2f}")
            cands.append(("rev", dict(lb=lb, use_trend=ut), tag, st['sharpe'], corr))
    for vw in (20, 30, 60):
        r_tr = sleeve_returns(ptr, mtr, "lv", vw=vw)
        corr = float(np.corrcoef(mom_tr[:len(r_tr)], r_tr)[0, 1])
        st = stats(r_tr)
        tag = f"LV vw{vw}"
        print(f"  {tag:<22} train Sharpe {st['sharpe']:>5.2f}  net {st['net']*100:>7.1f}%  "
              f"corr {corr:>5.2f}")
        cands.append(("lv", dict(vw=vw), tag, st['sharpe'], corr))

    # Diversifier selection rule (on TRAIN): require non-negative standalone Sharpe,
    # then pick the LOWEST correlation to MOM (max diversification benefit).
    viable = [c for c in cands if c[3] > 0]
    if not viable:
        print("\nNo second sleeve has positive standalone TRAIN Sharpe -> diversification"
              " has no honest basis. STOP."); return
    kind, kw, tag, _, corr_tr = min(viable, key=lambda c: c[4])
    print(f"\n-> chosen diversifier (TRAIN: Sharpe>0, lowest corr): {tag}  (train corr {corr_tr:.2f})")

    sl_tr = sleeve_returns(ptr, mtr, kind, **kw)
    sl_te = sleeve_returns(pte, mte, kind, **kw)
    n = min(len(mom_tr), len(sl_tr)); mom_tr, sl_tr = mom_tr[:n], sl_tr[:n]
    n = min(len(mom_te), len(sl_te)); momte, slte = mom_te[:n], sl_te[:n]

    # ---- pick blend alpha on TRAIN (max Sharpe), report TEST ----
    alphas = [1.0, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.0]
    best = max(alphas, key=lambda a: stats(blend(mom_tr, sl_tr, a))['sharpe'])
    print(f"\nBLEND alpha = MOM weight. TRAIN-picked alpha (max Sharpe) = {best:.1f}")
    print("  TEST results across the alpha grid (selection = the TRAIN-picked row):")
    for a in alphas:
        st = stats(blend(momte, slte, a))
        mark = "  <- TRAIN-picked" if abs(a-best) < 1e-9 else ""
        show(f"alpha={a:.1f} (mom{a*100:.0f}/sl{(1-a)*100:.0f})", st)
        if mark: print(mark)

    corr_te = float(np.corrcoef(momte, slte)[0, 1])
    print(f"\n  TEST sleeve correlation (MOM vs {tag}): {corr_te:.2f}")
    print("  MOM-only TEST:");  show("alpha=1.0", stats(momte))
    print(f"  blend({best:.1f}) TEST:"); show(f"alpha={best:.1f}", stats(blend(momte, slte, best)))

    # ---- 5-slice walk-forward: MOM-only vs TRAIN-picked blend ----
    print("\n[WF] 5-slice walk-forward (Sharpe / worstQ% / net%):")
    folds = np.array_split(np.arange(len(full)), 5)
    for label, a in [("MOM-only", 1.0), (f"blend a={best:.1f}", best)]:
        sh, wq, nets = [], [], []
        for idx in folds:
            seg = full.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
            ms = ma.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
            rm = sleeve_returns(seg, ms, "mom")
            rs = sleeve_returns(seg, ms, kind, **kw)
            m = min(len(rm), len(rs))
            st = stats(blend(rm[:m], rs[:m], a))
            sh.append(round(st['sharpe'], 2)); wq.append(round(st['wq']*100, 1)); nets.append(round(st['net']*100, 1))
        pos = sum(1 for x in sh if x > 0)
        print(f"  {label:<14} Sharpe {sh} ({pos}/5+)  worstQ {wq}  net% {nets}")


asyncio.run(main())
