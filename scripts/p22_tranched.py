"""P22 — Phase-TRANCHED (overlapping) rebalancing to diversify away the timing luck P20 exposed.

P20 measured the rebalance-PHASE dispersion: across the 7 weekly start offsets the SAME live
strategy realises wildly different cumulative returns (CV 0.57, +1032%..+7393%) even though
Sharpe is phase-stable. P20 concluded "phase is unknowable ex-ante -> no config change" and
STOPPED there. It left the standard remedy untested: instead of rebalancing the WHOLE book on
one arbitrary weekday, hold all 7 phases SIMULTANEOUSLY (Jegadeesh-Titman 1993 overlapping
portfolios) -- i.e. rebalance 1/7 of capital each day. The realised book is then the equal blend
of the 7 weekly sub-books; it is ONE deterministic portfolio with NO phase choice to get lucky/
unlucky on.

Why it should help (and why it is not overfitting):
  * Averaging N return streams each with vol sigma and average pairwise corr rho gives blended
    vol = sigma*sqrt((1+(N-1)rho)/N) < sigma, mean unchanged -> Sharpe x sqrt(N/(1+(N-1)rho)).
    The 7 phases trade nearly the same names offset by days (rho high, ~0.8-0.9) so the lift is
    modest (~5-10%) but the maxDD/path-consistency gain is real -- and consistency is the stated
    live goal. There is NO free parameter (N=7 is fixed by the weekly period R), so this is a
    pure construction, not a train->test search. Nothing to overfit.

Honest benchmark: the live bot rebalances on whatever ONE weekday the human runs it, so the
ex-ante expectation of the current design is the PHASE-MEAN (mean over the 7 offsets), NOT the
offset-0 grid every prior backtest happens to use (P20: offset-0 is a conservative pctile-29
draw). The tranched book must beat the PHASE-MEAN Sharpe/maxDD (not the lucky phase-max) and the
gain must survive OOS test-half + 5-slice walk-forward + the 2022 bear + realistic costs on the
SMOOTHED daily turnover (the tranche trades a little every day).

Accounting (stated for honesty): daily-marked, fixed-fractional weights. Each day the held
weight = the target from the last rebalance (so within-week the book is held at constant target
weights, a mild simplification vs buy-and-hold drift). CRITICALLY this accounting is IDENTICAL
for the single phases and the tranche, so the COMPARISON -- the entire point -- is apples-to-
apples. Single-phase daily numbers are sanity-checked against P20's weekly-grid Sharpe (must be
close). Costs are charged on |dW| each day (weekly for a single phase, daily-small for the
tranche), 15bps/side, with a 15/30/60bps sweep because the tranche's turnover profile differs.

Caveat: same as P20 -- one survivors-only multi-cycle panel; the 7 phases are overlapping views
of ONE underlying series (high rho), so the diversification is grid-alignment smoothing, not 7
independent histories. This bounds the achievable Sharpe lift (rho is high by construction).

    /Users/jamesvaness/hermes-trading/.venv/bin/python scripts/p22_tranched.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import K, R, LBS, COST, SPLIT, MA_DAYS, multi_score  # noqa: E402

START = "2020-01-01"
OFFSETS = list(range(R))  # 0..6 ; offset 0 == every prior backtest
D0_PAD = R + 1            # start metric window after every phase has rebalanced once
ANN = np.sqrt(365.0)


def phase_weights(panel_r, ma_r, offset, trend=True):
    """Daily TARGET weight matrix for one weekly phase (shape [n_days, n_coins]).
    W[d] = the book targeted as of close of day d. Held weight earning day d's return is W[d-1].
    Identical selection logic to p15.bt()/p20.bt_phase() (multi-horizon top-K, dual-mom trend)."""
    n, m = panel_r.shape
    W = np.zeros((n, m))
    cols = panel_r.columns
    i = max(LBS) + offset
    while i + R < n:
        sc = multi_score(panel_r, i).dropna()
        w = np.zeros(m)
        if len(sc) >= K:
            row = panel_r.iloc[i]
            marow = ma_r.iloc[i]
            for s in sc.sort_values().index[-K:]:
                if (not trend) or row[s] > marow[s]:
                    w[cols.get_loc(s)] = 1.0 / K
        W[i:i + R, :] = w           # hold w over [i, i+R); next rebal overwrites day i+R
        i += R
    return W


def daily_metrics(W, dayret, cost, d0, d1):
    """Net daily returns from a target-weight matrix W over [d0,d1), costs on |dW| each day.
    Returns (net_series, dict(net,sharpe,maxdd,worstday,turn_ann,inmkt))."""
    held = W[d0 - 1:d1 - 1]                 # weight earning day d's return = prior close target
    rets = (held * dayret[d0:d1]).sum(axis=1)
    dW = np.abs(W[d0:d1] - W[d0 - 1:d1 - 1]).sum(axis=1)
    net = rets - cost * dW
    if len(net) < 2 or net.std() == 0:
        return net, dict(net=0.0, sharpe=0.0, maxdd=0.0, worstday=0.0, turn_ann=0.0, inmkt=0.0)
    eq = np.cumprod(1 + net)
    pk = np.maximum.accumulate(eq)
    return net, dict(
        net=float(eq[-1] - 1),
        sharpe=float(net.mean() / net.std() * ANN),
        maxdd=float(np.max((pk - eq) / pk)),
        worstday=float(net.min()),
        turn_ann=float(dW.sum() / len(net) * 365.0),
        inmkt=float(W[d0:d1].sum(axis=1).mean()),
    )


def run_window(panel_r, ma_r, dayret, d0, d1, cost=COST, trend=True, offsets=OFFSETS):
    """Build every phase weight matrix + the equal blend, return per-phase metrics, tranche
    metrics, the per-phase net series (for corr), and the tranche net series."""
    Ws = {o: phase_weights(panel_r, ma_r, o, trend=trend) for o in offsets}
    phase_nets, phase_stats = {}, {}
    for o in offsets:
        s, st = daily_metrics(Ws[o], dayret, cost, d0, d1)
        phase_nets[o], phase_stats[o] = s, st
    W_tr = np.mean([Ws[o] for o in offsets], axis=0)
    tr_net, tr_stats = daily_metrics(W_tr, dayret, cost, d0, d1)
    return phase_stats, tr_stats, phase_nets, tr_net


def summarise(phase_stats, key, offsets=OFFSETS):
    vals = np.array([phase_stats[o][key] for o in offsets], dtype=float)
    return vals.mean(), vals.std(), vals.min(), vals.max()


def avg_pairwise_corr(phase_nets, offsets=OFFSETS):
    M = np.vstack([phase_nets[o] for o in offsets])
    C = np.corrcoef(M)
    iu = np.triu_indices(len(offsets), k=1)
    return float(np.nanmean(C[iu]))


def main():
    syms = live_universe()
    panel = load_panel(syms, "1d")
    if panel.empty:
        sys.exit("No cache. Run: EXCHANGE_ID=kucoin python scripts/data_cache.py --update")
    panel.index = pd.to_datetime(panel.index, unit="ms")
    panel = panel.sort_index()
    panel = panel[panel.index >= START].dropna(axis=1, thresh=120)
    idx = panel.index
    yrs = (idx[-1] - idx[0]).days / 365

    panel_r = panel.reset_index(drop=True)
    ma_r = panel.rolling(MA_DAYS).mean().reset_index(drop=True)
    pv = panel_r.values
    dayret = np.zeros_like(pv)
    dayret[1:] = pv[1:] / pv[:-1] - 1
    dayret = np.nan_to_num(dayret, nan=0.0, posinf=0.0, neginf=0.0)

    n = len(panel_r)
    d0 = max(LBS) + D0_PAD
    cut = int(n * SPLIT)
    cut = max(cut, d0 + 30)

    print("=" * 92)
    print("P22 — phase-TRANCHED (overlapping) rebalancing vs single-phase timing luck")
    print("=" * 92)
    print(f"panel {idx[0].date()}..{idx[-1].date()}  (~{yrs:.1f}y, {n} days, {panel.shape[1]} "
          f"coins)  cost {COST*1e4:.0f}bps/side  R={R}d  K={K}  LBS={LBS}")
    print("Daily-marked accounting (identical for phases & tranche). Sharpe annualised x sqrt(365)."
          "\nHonest benchmark = PHASE-MEAN (random deploy weekday), NOT phase-max or offset-0.\n")

    # ---------------- (1) FULL window: each phase, the tranche, and the honest benchmark -------
    ps, tr, pnets, trnet = run_window(panel_r, ma_r, dayret, d0, n)
    rho = avg_pairwise_corr(pnets)
    print("-" * 92)
    print("(1) FULL window — 7 single phases (daily-marked) vs the equal-blend TRANCHE:")
    print(f"{'phase':<10}{'net%':>11}{'Sharpe':>9}{'maxDD':>8}{'worstDay':>10}"
          f"{'turn/yr':>9}{'inmkt':>8}")
    for o in OFFSETS:
        s = ps[o]
        tag = "  <-offset0 (all prior BTs)" if o == 0 else ""
        print(f"offset {o:<3}{s['net']*100:>10.0f}%{s['sharpe']:>9.2f}{s['maxdd']*100:>7.0f}%"
              f"{s['worstday']*100:>9.1f}%{s['turn_ann']:>8.1f}x{s['inmkt']*100:>7.0f}%{tag}")
    for key, lab in [("net", "net%"), ("sharpe", "Sharpe"), ("maxdd", "maxDD")]:
        m, sd, lo, hi = summarise(ps, key)
        sc = 100 if key != "sharpe" else 1
        u = "%" if key != "sharpe" else ""
        print(f"  phase-{lab:<7} mean {m*sc:+.2f}{u}  std {sd*sc:.2f}{u}  "
              f"range [{lo*sc:+.0f},{hi*sc:+.0f}]{u}")
    print(f"  >> avg pairwise corr of the 7 phase return streams: rho = {rho:.3f}  "
          f"(theory Sharpe lift x sqrt(7/(1+6*rho)) = {np.sqrt(7/(1+6*rho)):.3f})")
    mSh, sdSh, _, hiSh = summarise(ps, "sharpe")
    mDD, _, loDD, _ = summarise(ps, "maxdd")
    print(f"\n  TRANCHE (7 phases blended): net {tr['net']*100:+.0f}%  Sharpe {tr['sharpe']:.2f}  "
          f"maxDD {tr['maxdd']*100:.0f}%  worstDay {tr['worstday']*100:.1f}%  "
          f"turn/yr {tr['turn_ann']:.1f}x")
    print(f"    vs PHASE-MEAN   : Sharpe {mSh:.2f}  maxDD {mDD*100:.0f}%   -> "
          f"dSharpe {tr['sharpe']-mSh:+.2f}, dMaxDD {(tr['maxdd']-mDD)*100:+.0f}pp")
    print(f"    vs phase-BEST   : Sharpe {hiSh:.2f}  (tranche should be >= mean, not necessarily best)")

    # ---------------- (2) OOS test half ----------------
    print("\n" + "-" * 92)
    print(f"(2) OOS test half (from day {cut}, ~{(idx[-1]-idx[cut]).days}d) — same comparison:")
    ps2, tr2, pn2, _ = run_window(panel_r, ma_r, dayret, cut, n)
    mSh2, _, _, hiSh2 = summarise(ps2, "sharpe")
    mDD2, _, _, _ = summarise(ps2, "maxdd")
    mNet2, _, loNet2, hiNet2 = summarise(ps2, "net")
    print(f"  phase-mean: net {mNet2*100:+.0f}% (range[{loNet2*100:+.0f},{hiNet2*100:+.0f}])  "
          f"Sharpe {mSh2:.2f}  maxDD {mDD2*100:.0f}%")
    print(f"  TRANCHE   : net {tr2['net']*100:+.0f}%  Sharpe {tr2['sharpe']:.2f}  "
          f"maxDD {tr2['maxdd']*100:.0f}%  -> dSharpe {tr2['sharpe']-mSh2:+.2f}, "
          f"dMaxDD {(tr2['maxdd']-mDD2)*100:+.0f}pp")

    # ---------------- (3) 5-slice walk-forward: tranche vs phase-mean per slice ----------------
    print("\n" + "-" * 92)
    print("(3) 5-slice walk-forward — tranche Sharpe vs phase-mean Sharpe per slice:")
    folds = np.array_split(np.arange(n), 5)
    wins = 0
    print(f"    {'slice':<8}{'phaseMeanSh':>13}{'trancheSh':>11}{'dSh':>8}"
          f"{'  | trMaxDD vs meanMaxDD':>26}")
    for k, f in enumerate(folds):
        lo, hi = f[0], f[-1] + 1
        sd0 = lo + max(LBS) + D0_PAD
        if hi - sd0 < 40:
            print(f"    slice {k}: too short, skipped")
            continue
        psf, trf, _, _ = run_window(panel_r, ma_r, dayret, sd0, hi)
        mShf, _, _, _ = summarise(psf, "sharpe")
        mDDf, _, _, _ = summarise(psf, "maxdd")
        d = trf["sharpe"] - mShf
        wins += int(d > 0)
        print(f"    slice {k:<2}{mShf:>13.2f}{trf['sharpe']:>11.2f}{d:>+8.2f}"
              f"{trf['maxdd']*100:>16.0f}% vs {mDDf*100:.0f}%")
    print(f"  tranche beats phase-mean Sharpe in {wins}/5 slices")

    # ---------------- (4) 2022 bear: tranche vs phase distribution ----------------
    print("\n" + "-" * 92)
    print("(4) 2022 BEAR (located) — does the blend shallow the bear vs the phase spread?")
    blo = int(idx.searchsorted(pd.Timestamp("2022-01-01")))
    bhi = int(idx.searchsorted(pd.Timestamp("2022-12-31")))
    bsd0 = max(blo, max(LBS) + D0_PAD)
    psb, trb, _, _ = run_window(panel_r, ma_r, dayret, bsd0, bhi)
    mNetb, sdNetb, loNetb, hiNetb = summarise(psb, "net")
    mDDb, _, _, hiDDb = summarise(psb, "maxdd")
    print(f"  phase bear net: mean {mNetb*100:+.1f}% std {sdNetb*100:.1f}% "
          f"range [{loNetb*100:+.1f},{hiNetb*100:+.1f}]   phase maxDD mean {mDDb*100:.0f}%")
    print(f"  TRANCHE bear  : net {trb['net']*100:+.1f}%  maxDD {trb['maxdd']*100:.0f}%  "
          f"worstDay {trb['worstday']*100:.1f}%")

    # ---------------- (5) cost sensitivity (the tranche trades daily — check it survives) ------
    print("\n" + "-" * 92)
    print("(5) COST sensitivity (tranche turnover is smoothed/daily — does the gain survive?):")
    print(f"    {'cost/side':>10}{'phaseMeanNet':>14}{'phaseMeanSh':>13}"
          f"{'trNet':>9}{'trSh':>8}")
    for cb in (15, 30, 60):
        psc, trc, _, _ = run_window(panel_r, ma_r, dayret, d0, n, cost=cb / 1e4)
        mNetc, _, _, _ = summarise(psc, "net")
        mShc, _, _, _ = summarise(psc, "sharpe")
        tag = "  <-backtest" if cb == 15 else ("  <-Coinbase-real" if cb == 60 else "")
        print(f"    {cb:>8}bp{mNetc*100:>13.0f}%{mShc:>13.2f}{trc['net']*100:>8.0f}%"
              f"{trc['sharpe']:>8.2f}{tag}")

    # ---------------- (6) deployability: coarser tranches (less operational burden) ------------
    print("\n" + "-" * 92)
    print("(6) deployability — coarser tranches (fewer rebal days/wk) vs full 7-phase blend:")
    print(f"    {'variant':<22}{'net%':>10}{'Sharpe':>9}{'maxDD':>8}{'turn/yr':>9}")
    variants = [("offset0 (live, 1 day)", [0]),
                ("2-tranche {0,4}", [0, 4]),
                ("3-tranche {0,2,4}", [0, 2, 4]),
                ("7-tranche (full)", OFFSETS)]
    for lab, offs in variants:
        _, trv, _, _ = run_window(panel_r, ma_r, dayret, d0, n, offsets=offs)
        print(f"    {lab:<22}{trv['net']*100:>9.0f}%{trv['sharpe']:>9.2f}"
              f"{trv['maxdd']*100:>7.0f}%{trv['turn_ann']:>8.1f}x")

    # ---------------- verdict scaffold ----------------
    print("\n" + "=" * 92)
    full_gain = tr["sharpe"] - mSh
    oos_gain = tr2["sharpe"] - mSh2
    dd_gain_full = mDD - tr["maxdd"]
    print(f"FULL  dSharpe vs phase-mean {full_gain:+.2f} (mean {mSh:.2f}±{sdSh:.2f}); "
          f"maxDD {tr['maxdd']*100:.0f}% vs mean {mDD*100:.0f}% ({dd_gain_full*100:+.0f}pp better)")
    print(f"OOS   dSharpe vs phase-mean {oos_gain:+.2f}; WF tranche>mean {wins}/5; "
          f"bear maxDD {trb['maxdd']*100:.0f}% vs phase-mean {mDDb*100:.0f}%")
    # phase-luck dispersion the tranche ELIMINATES (the actual P20 worry), full + OOS
    pn_full = np.array([ps[o]["net"] for o in OFFSETS])
    pn_oos = np.array([ps2[o]["net"] for o in OFFSETS])
    cv_full = pn_full.std() / pn_full.mean()
    cv_oos = pn_oos.std() / pn_oos.mean()
    MEANINGFUL = 0.10  # a dSharpe below this is economic noise, not an edge
    sharpe_edge = (full_gain >= MEANINGFUL and oos_gain >= MEANINGFUL and wins >= 3)
    direction_ok = (full_gain >= 0 and oos_gain >= 0 and tr["maxdd"] <= mDD and tr2["maxdd"] <= mDD2)
    print(f"phase-luck dispersion REMOVED by the blend: cumulative-net CV full {cv_full:.2f} / "
          f"OOS {cv_oos:.2f}  (range full [{pn_full.min()*100:+.0f},{pn_full.max()*100:+.0f}]%)")
    if sharpe_edge:
        print("VERDICT: TRANCHING IS A RISK-ADJUSTED EDGE — beats the honest phase-mean Sharpe by "
              f">={MEANINGFUL} (full+OOS+WF) and shallows maxDD. Candidate-worthy.")
    elif direction_ok:
        print(f"VERDICT: FREE CONSISTENCY TWEAK, NOT A SHARPE EDGE. With rho={rho:.2f} the phases "
              "are nearly the same book, so the Sharpe/maxDD lift is mechanically real but TINY "
              f"(dSharpe {full_gain:+.2f}/{oos_gain:+.2f}, within noise). What tranching genuinely "
              f"buys is ELIMINATING the unrewarded weekday-luck DISPERSION (cumulative-net CV "
              f"{cv_full:.2f}->0): a single-weekday live deploy is stuck with a random draw from "
              f"[{pn_full.min()*100:+.0f},{pn_full.max()*100:+.0f}]% that compounds for years; the "
              "blend locks in ~the phase-mean deterministically. It does NOT create alpha and does "
              "NOT beat live on risk-adjusted terms -> NO config change (matches P20). Optional "
              "OPERATIONAL choice if the human wants to remove weekday luck; the 2-tranche {0,4} "
              "captures it at 2 rebal days/wk instead of 7.")
    else:
        print("VERDICT: NO ROBUST EDGE — the blend does not even reliably beat the honest "
              f"phase-mean OOS; high phase correlation (rho={rho:.2f}) leaves too little to "
              "diversify. NO config change.")
    print("CAVEAT: one survivors-only panel; the 7 phases are overlapping views of ONE series "
          f"(rho={rho:.2f}) -> grid-alignment smoothing, not 7 independent histories.")


if __name__ == "__main__":
    main()
