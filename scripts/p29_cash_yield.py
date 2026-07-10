"""P29 — Idle-cash yield: price the risk-free return on the un-deployed (cash) leg.

Every backtest in this repo (P0->P28) implicitly assumes the cash leg earns 0%. But the live
long-only + dual-momentum book is only ~46-85% deployed (P16): when the trend filter benches names
(or a gate/halt flattens the book) the freed capital sits in USDC, which can earn a real risk-free
yield (~4-5%/yr HYSA / USDC reward). That idle-cash yield is a RISKLESS additive return the whole
stack ignores. This is a pure accounting add, no look-ahead, one assumed parameter (rf).

Two honest questions:
  (1) DIRECT ADD — how much does a realistic cash yield add to the LIVE config's net/Sharpe, applied
      ONLY to the un-deployed fraction each period? (sweep rf in 0/2/4/5 %/yr)
  (2) REOPEN — does pricing cash correctly reopen a partial-cash lever that was killed only because
      cash was DEAD money? Specifically re-score, WITH rf:
        - P28 DD-halt (OFF vs permanent-0.30 vs reenter-0.30)          [daily-marked engine]
        - P11 weight-cap / partial-cash de-leverage (scale in {1,.75,.5}) and market-DD-gate.

Method: extend the validated p15/p25 weekly engine with `rf*(1 - deployed)*R/365` per period, and
the p28 daily-marked halt engine with `rf*(1 - deployed)/365` per day (cash = 1 - w.sum(); a halted
book is 100% cash). rf=0 reproduces the canonical live/OFF numbers (sanity-checked below).

HONESTY TRAP flagged up front: adding a near-constant +rf on the cash fraction MECHANICALLY raises
Sharpe for any LOW-deployment config (in the limit, 100% cash at rf has ~infinite Sharpe — it is the
risk-free rate, not alpha). So a partial-cash lever "winning on Sharpe" once cash is paid is NOT an
edge. The only honest test that a lever is REOPENED is that it beats live on BOTH risk-adj AND total
return, or cuts drawdown without giving up return — same bar as every prior consistency item.

    /Users/jamesvaness/hermes-trading/.venv/bin/python scripts/p29_cash_yield.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import K, R, SPLIT, MA_DAYS, LBS, COST, multi_score  # noqa: E402

START = "2020-01-01"
WARM = max(LBS)                 # 60 — matches p15/p27/p28 canonical live warmup
RF_GRID = [0.0, 0.02, 0.04, 0.05]
YR = 365.0


# ---------------------------------------------------------------- weekly engine (+rf, +scale, +gate)
def _live_weights(panel, ma, i, top_k, trend):
    sc = multi_score(panel, i).dropna()
    w = pd.Series(0.0, index=panel.columns)
    if len(sc) >= top_k:
        for s in sc.sort_values().index[-top_k:]:
            if (not trend) or panel.iloc[i][s] > ma.iloc[i][s]:
                w[s] = 1 / top_k
    return w


def bt(panel, ma, rf=0.0, scale=1.0, mode="base", stop=None, trend=True,
       top_k=K, cost=COST, phase=0, lo=None, hi=None):
    """Live weekly K5 engine with an idle-cash-yield term.
      rf     : annualized risk-free rate paid on the un-deployed fraction each period.
      scale  : partial-cash de-leverage (P11 weight-cap); scale=1 == live, .5 == half book to cash.
      mode   : 'base' | 'ddgate' (P11 market-drawdown gate: cash while eq-wt mkt index is >stop below peak).
    rf=0, scale=1, mode='base' == the validated live engine (sanity below)."""
    if mode == "ddgate":
        mret = panel.pct_change().mean(axis=1).fillna(0)
        midx = (1 + mret).cumprod()
        mdd = (midx.cummax() - midx) / midx.cummax()
    rets, turns, deployed = [], [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = WARM + phase
    while i + R < len(panel):
        w = _live_weights(panel, ma, i, top_k, trend)
        if mode == "ddgate" and mdd.iloc[i] >= stop:
            w = pd.Series(0.0, index=panel.columns)
        w = w * scale
        fwd = (panel.iloc[i + R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0)
        d = float(w.sum())
        cash_yield = rf * (1.0 - d) * R / YR
        turn = (w - prev).abs().sum()
        if lo is None or (lo <= i < hi):
            rets.append((w * fwd).sum() - turn * cost + cash_yield)
            turns.append(turn)
            deployed.append(d)
        prev = w
        i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return dict(net=0, sharpe=0, maxdd=0, worstwk=0, turnover=0, inmkt=0, n=len(rets))
    eq = np.cumprod(1 + rets); pk = np.maximum.accumulate(eq)
    return dict(net=eq[-1] - 1, sharpe=rets.mean() / rets.std() * np.sqrt(YR / R),
                maxdd=float(np.max((pk - eq) / pk)), worstwk=float(rets.min()),
                turnover=float(np.mean(turns)), inmkt=float(np.mean(deployed)), n=len(rets))


# ---------------------------------------------------------------- daily-marked halt engine (+rf)
def bt_halt(panel, ma, rf=0.0, h=0.0, reset="permanent", top_k=K, cost=COST, offset=0, trend=True):
    """P28 daily-marked engine with a DD circuit-breaker + idle-cash yield.
    rf=0,h=0 == p28 OFF baseline (sanity). Cash (incl. a halted book) earns rf*(1-deployed)/365/day."""
    n = len(panel)
    day_rets, deployed_days = [], []
    prev = pd.Series(0.0, index=panel.columns)
    eq, peak, halted = 1.0, 1.0, False
    first_trip, n_trips, days_halted = None, 0, 0
    i = WARM + offset
    while i + R < n:
        if halted and reset == "permanent":
            w = pd.Series(0.0, index=panel.columns)
        else:
            if halted and reset == "reenter":
                halted = False
            w = _live_weights(panel, ma, i, top_k, trend)
        tc = (w - prev).abs().sum() * cost
        for dday in range(i, i + R):
            d = float(w.sum())
            dr = (w * (panel.iloc[dday + 1] / panel.iloc[dday] - 1)).sum()
            dr += rf * (1.0 - d) / YR           # idle-cash yield (halted book: d=0 -> full rf)
            if dday == i:
                dr -= tc
            eq *= (1 + dr)
            day_rets.append(dr); deployed_days.append(d)
            if halted:
                days_halted += 1
            peak = max(peak, eq)
            if (not halted) and h and (peak - eq) / peak >= h:
                halted = True; n_trips += 1
                if first_trip is None:
                    first_trip = dday
                w = pd.Series(0.0, index=panel.columns)
        prev = w; i += R
    rets = np.array(day_rets)
    if len(rets) < 2 or rets.std() == 0:
        return dict(net=0, sharpe=0, maxdd=0, first_trip=first_trip, n_trips=n_trips,
                    days_halted=days_halted, ndays=len(rets), inmkt=0)
    ec = np.cumprod(1 + rets); pk = np.maximum.accumulate(ec)
    return dict(net=ec[-1] - 1, sharpe=rets.mean() / rets.std() * np.sqrt(YR),
                maxdd=float(np.max((pk - ec) / pk)), first_trip=first_trip, n_trips=n_trips,
                days_halted=days_halted, ndays=len(rets), inmkt=float(np.mean(deployed_days)))


def seven_phase_sharpe(panel, ma, **kw):
    return np.array([bt(panel, ma, phase=ph, **kw)["sharpe"] for ph in range(7)])


def main():
    syms = live_universe()
    panel = load_panel(syms, "1d")
    if panel.empty:
        sys.exit("No cache. Run: EXCHANGE_ID=kucoin python scripts/data_cache.py --update")
    panel.index = pd.to_datetime(panel.index, unit="ms")
    panel = panel.sort_index()
    panel = panel[panel.index >= START].dropna(axis=1, thresh=120)
    ma = panel.rolling(MA_DAYS).mean()
    idx = panel.index
    cut = int(len(panel) * SPLIT)
    yrs = (idx[-1] - idx[0]).days / 365
    pr = panel.reset_index(drop=True); mr = ma.reset_index(drop=True)
    pte = panel.iloc[cut:].reset_index(drop=True); mte = ma.iloc[cut:].reset_index(drop=True)
    ptr = panel.iloc[:cut].reset_index(drop=True); mtr = ma.iloc[:cut].reset_index(drop=True)
    # daily bear window (peak-through-recovery), matching p28 (C)
    bmask = (idx >= "2021-06-01") & (idx <= "2023-06-30")
    pb = panel[bmask].reset_index(drop=True); mb = ma[bmask].reset_index(drop=True)
    # weekly-located bear-2022 for the direct add
    lo22 = int(idx.searchsorted(pd.Timestamp("2022-01-01")))
    hi22 = int(idx.searchsorted(pd.Timestamp("2022-12-31")))

    print("=" * 92)
    print("P29 — Idle-cash yield on the un-deployed leg (pure accounting add; no look-ahead)")
    print("=" * 92)
    print(f"panel {idx[0].date()}..{idx[-1].date()} (~{yrs:.1f}y, {len(panel)}d), {panel.shape[1]} coins, "
          f"{COST*1e4:.0f}bps/side, LBS={LBS} K={K} MA={MA_DAYS} R={R}")
    print(f"OOS test half starts {idx[cut].date()}.  rf grid = {[f'{r*100:.0f}%' for r in RF_GRID]}/yr\n")

    # sanity: rf=0 must reproduce the validated live numbers
    s_wk = bt(pr, mr, rf=0.0)
    s_hl = bt_halt(pr, mr, rf=0.0, h=0.0)
    print(f"[sanity] weekly rf=0 live FULL: net {s_wk['net']*100:.0f}%  Sharpe {s_wk['sharpe']:.2f}  "
          f"maxDD {s_wk['maxdd']*100:.0f}%  inmkt {s_wk['inmkt']*100:.0f}%  (cf p27 R7 ~+1220%/0.91)")
    print(f"[sanity] daily  rf=0 halt=OFF FULL: net {s_hl['net']*100:.0f}%  Sharpe {s_hl['sharpe']:.2f}  "
          f"maxDD {s_hl['maxdd']*100:.0f}%  (cf p28 OFF ~+1220%, deeper daily DD)\n")

    # ========================================================= (1) DIRECT ADD to the live config
    print("-" * 92)
    print("(1) DIRECT ADD — live config, sweep rf on the un-deployed fraction (no other change):")
    print(f"  {'rf':>5}{'fullNet':>10}{'fullSh':>8}{'fullDD':>8}{'oosNet':>10}{'oosSh':>8}"
          f"{'bearNet':>9}{'7phMeanSh':>10}")
    base_full_net = None
    for rf in RF_GRID:
        f = bt(pr, mr, rf=rf)
        o = bt(pte, mte, rf=rf)
        b = bt(pr, mr, rf=rf, lo=lo22, hi=hi22)
        ph = seven_phase_sharpe(pte, mte, rf=rf).mean()
        if rf == 0.0:
            base_full_net = f["net"]
        tag = f"  (+{(f['net']-base_full_net)*100:.0f}pp vs rf=0)" if rf > 0 else ""
        print(f"  {rf*100:>4.0f}%{f['net']*100:>9.0f}%{f['sharpe']:>8.3f}{f['maxdd']*100:>7.0f}%"
              f"{o['net']*100:>9.0f}%{o['sharpe']:>8.3f}{b['net']*100:>8.1f}%{ph:>10.3f}{tag}")
    print(f"  avg deployment (inmkt) = {s_wk['inmkt']*100:.0f}% -> cash leg ~{(1-s_wk['inmkt'])*100:.0f}% "
          f"of the time; per-yr cash contribution ~ rf*(cash frac).")

    # ========================================================= (2A) REOPEN — P28 DD-halt with rf
    print("\n" + "-" * 92)
    print("(2A) REOPEN P28 DD-halt — does paying rf on the (long) halted cash change the verdict?")
    print("     Full 2020-> (daily-marked). OFF is the validated baseline; perm-0.30 is LIVE.")
    print(f"  {'variant':<18}{'rf=0 net':>10}{'rf=0 Sh':>9}{'rf=4% net':>11}{'rf=4% Sh':>10}"
          f"{'DD':>7}{'%halted':>9}")
    for name, h, reset in [("OFF (validated)", 0.0, "permanent"),
                           ("perm 0.30 <-LIVE", 0.30, "permanent"),
                           ("reenter 0.30", 0.30, "reenter")]:
        r0 = bt_halt(pr, mr, rf=0.0, h=h, reset=reset)
        r4 = bt_halt(pr, mr, rf=0.04, h=h, reset=reset)
        pct = 100 * r4["days_halted"] / r4["ndays"] if r4["ndays"] else 0
        print(f"  {name:<18}{r0['net']*100:>9.0f}%{r0['sharpe']:>9.2f}{r4['net']*100:>10.0f}%"
              f"{r4['sharpe']:>10.2f}{r4['maxdd']*100:>6.0f}%{pct:>8.0f}%")
    print("     bear window 2021-06..2023-06 (peak-through-recovery, daily):")
    print(f"  {'variant':<18}{'rf=0 net':>10}{'rf=4% net':>11}{'rf=4% Sh':>10}{'DD':>7}")
    for name, h, reset in [("OFF (validated)", 0.0, "permanent"),
                           ("perm 0.30 <-LIVE", 0.30, "permanent")]:
        b0 = bt_halt(pb, mb, rf=0.0, h=h, reset=reset)
        b4 = bt_halt(pb, mb, rf=0.04, h=h, reset=reset)
        print(f"  {name:<18}{b0['net']*100:>9.0f}%{b4['net']*100:>10.0f}%{b4['sharpe']:>10.2f}"
              f"{b4['maxdd']*100:>6.0f}%")

    # ========================================================= (2B) REOPEN — P11 weight-cap / partial cash
    print("\n" + "-" * 92)
    print("(2B) REOPEN P11 partial-cash de-leverage (scale book, rest to cash@rf). scale=1==live.")
    print("     HONESTY: lower scale mixes in more rf -> Sharpe rises MECHANICALLY (it's rf, not alpha);")
    print("     a genuine reopen needs a BETTER return too, or DD cut WITHOUT a return give-up.")
    print(f"  {'scale':>6}{'rf':>5}{'fullNet':>10}{'fullSh':>8}{'fullDD':>8}{'oosNet':>10}{'oosSh':>8}"
          f"{'7phMeanSh':>10}")
    for scale in [1.0, 0.75, 0.5]:
        for rf in [0.0, 0.04]:
            f = bt(pr, mr, rf=rf, scale=scale)
            o = bt(pte, mte, rf=rf, scale=scale)
            ph = seven_phase_sharpe(pte, mte, rf=rf, scale=scale).mean()
            print(f"  {scale:>6.2f}{rf*100:>4.0f}%{f['net']*100:>9.0f}%{f['sharpe']:>8.3f}"
                  f"{f['maxdd']*100:>7.0f}%{o['net']*100:>9.0f}%{o['sharpe']:>8.3f}{ph:>10.3f}")
    # honest TRAIN->TEST over scale, WITH rf=4%: does a de-levered book beat live OOS on BOTH?
    print("     honest TRAIN->TEST (pick scale by TRAIN Sharpe @rf=4%), report TEST:")
    tr = {s: bt(ptr, mtr, rf=0.04, scale=s)["sharpe"] for s in [1.0, 0.75, 0.5]}
    pick = max(tr, key=lambda s: tr[s])
    tp = bt(pte, mte, rf=0.04, scale=pick); tl = bt(pte, mte, rf=0.04, scale=1.0)
    print(f"       TRAIN Sharpe: " + "  ".join(f"s={s}:{tr[s]:.2f}" for s in [1.0, 0.75, 0.5])
          + f"  -> picks scale={pick}")
    print(f"       TEST scale={pick}: net {tp['net']*100:+.0f}% Sh {tp['sharpe']:.2f} | "
          f"TEST live: net {tl['net']*100:+.0f}% Sh {tl['sharpe']:.2f}  "
          f"({'BEATS on both' if pick != 1.0 and tp['sharpe'] > tl['sharpe'] and tp['net'] > tl['net'] else 'no return beat'})")

    # ========================================================= (2C) REOPEN — P11 market-DD-gate with rf
    print("\n" + "-" * 92)
    print("(2C) REOPEN P11 market-DD-gate (cash while eq-wt mkt index >stop below peak), cash@rf.")
    print(f"  {'variant':<16}{'rf':>5}{'oosNet':>10}{'oosSh':>8}{'oosDD':>7}{'WF+':>6}{'inmkt':>7}")
    folds = np.array_split(np.arange(len(panel)), 5)

    def wf_pos(**kw):
        c = 0
        for f in folds:
            seg = panel.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            ms = ma.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            lvl = bt(seg, ms, mode="base", rf=kw.get("rf", 0.0))["sharpe"]
            gat = bt(seg, ms, **kw)["sharpe"]
            c += int(gat > lvl)
        return c
    for stop in [0.15, 0.25]:
        for rf in [0.0, 0.04]:
            o = bt(pte, mte, mode="ddgate", stop=stop, rf=rf)
            wp = wf_pos(mode="ddgate", stop=stop, rf=rf)
            print(f"  gate {stop:<11.2f}{rf*100:>4.0f}%{o['net']*100:>9.0f}%{o['sharpe']:>8.2f}"
                  f"{o['maxdd']*100:>6.0f}%{wp:>4}/5{o['inmkt']*100:>6.0f}%")
    ol = bt(pte, mte, mode="base", rf=0.04)
    print(f"  live (base)  4%{ol['net']*100:>9.0f}%{ol['sharpe']:>8.2f}{ol['maxdd']*100:>6.0f}%"
          f"{'   -':>6}{ol['inmkt']*100:>6.0f}%")

    print("\n" + "=" * 92)
    print("Verdict from (1): rf is a small RISKLESS additive (park idle USDC) — an OPS choice, no")
    print("engine change. From (2A/2B/2C): if no partial-cash lever beats live on BOTH return AND")
    print("risk-adj once cash is priced (Sharpe gains are just rf), the market-beta ceiling holds.")


if __name__ == "__main__":
    main()
