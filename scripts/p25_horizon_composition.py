"""P25 — Momentum-horizon COMPOSITION robustness (the core-alpha parameter).

The live signal ranks coins by the EQUAL-WEIGHT average of raw 14/30/60d returns
(`multi_score`). That basket was chosen ONCE (2026-06-12) only against single-30d, and the
horizon SET and the WEIGHTING have never been swept or re-validated. This is the most central
unvalidated parameter in the whole signal. P25 varies the composition itself with RAW returns
(genuinely distinct from P21 vol-risk-adjust / P23 strip-beta, which kept these horizons and
only changed per-coin normalization).

Composition variants tested (everything else = EXACT live: top-5, dual-momentum px>100d MA else
cash, weekly R=7, equal coin weight, 15bps/side). All use RAW per-horizon returns:
  live      : equal-wt [14,30,60]                    <- LIVE
  fast      : equal-wt [7,14,30]
  slow      : equal-wt [30,60,90]
  wide      : equal-wt [14,30,60,90]
  pair3060  : equal-wt [30,60]
  single30  : [30]            (the pre-multi-horizon original)
  single60  : [60]
  wlong     : [14,30,60] weighted PROPORTIONAL to lb  (emphasize the slow horizon)
  wshort    : [14,30,60] weighted PROPORTIONAL to 1/lb (emphasize the fast horizon)
  rankavg   : equal-wt average of cross-sectional RANKS per [14,30,60] horizon (equalizes each
              horizon's CONTRIBUTION regardless of magnitude — raw-return averaging implicitly
              over-weights the longest horizon since 60d returns are larger than 14d)

HONESTY: a fixed GLOBAL warmup (= max horizon across all variants) so every variant scores the
IDENTICAL rebalance dates (apples-to-apples — different warmups would mean different windows).
Variants are scored on a TRAIN half only; the TRAIN-picked blend is reported on the TEST half,
a 5-slice walk-forward, the P20/P21 7-phase rebalance-grid robustness killer, located inside the
2022 bear, and a 2021-> window cross-check. Reuses the validated p15/p21 engine + the same
2020-> cache panel as P15/P16/P20/P21.

    python scripts/p25_horizon_composition.py [--start 2020-01-01]
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import K, R, SPLIT, MA_DAYS, COST  # noqa: E402

# (lbs, weight_mode) per variant. weight_mode in {equal, linear(prop lb), invlinear(prop 1/lb), rank}
VARIANTS = [
    ("live", [14, 30, 60], "equal", "equal-wt [14,30,60] (LIVE)"),
    ("fast", [7, 14, 30], "equal", "equal-wt [7,14,30]"),
    ("slow", [30, 60, 90], "equal", "equal-wt [30,60,90]"),
    ("wide", [14, 30, 60, 90], "equal", "equal-wt [14,30,60,90]"),
    ("pair3060", [30, 60], "equal", "equal-wt [30,60]"),
    ("single30", [30], "equal", "[30] (pre-multihorizon)"),
    ("single60", [60], "equal", "[60]"),
    ("wlong", [14, 30, 60], "linear", "[14,30,60] wt prop lb"),
    ("wshort", [14, 30, 60], "invlinear", "[14,30,60] wt prop 1/lb"),
    ("rankavg", [14, 30, 60], "rank", "equal-wt RANK-avg [14,30,60]"),
]
VARMAP = {name: (lbs, wm) for name, lbs, wm, _ in VARIANTS}
WARMUP = max(lb for _, lbs, _, _ in VARIANTS for lb in lbs)  # 90 — same start for ALL variants


def make_score(panel):
    """score(i, name) -> per-coin selection score at row i using only data through row i.
    All variants share the same WARMUP so they score the identical rebalance dates."""
    def horizon_ret(i, lb):
        return panel.iloc[i] / panel.iloc[i - lb] - 1

    def score(i, name):
        lbs, wm = VARMAP[name]
        if wm == "rank":
            # cross-sectional rank (ascending: higher return -> higher rank), averaged equally
            parts = [horizon_ret(i, lb).rank() for lb in lbs]
            return sum(parts) / len(parts)
        rets = {lb: horizon_ret(i, lb) for lb in lbs}
        if wm == "equal":
            w = {lb: 1.0 for lb in lbs}
        elif wm == "linear":
            w = {lb: float(lb) for lb in lbs}
        elif wm == "invlinear":
            w = {lb: 1.0 / lb for lb in lbs}
        else:
            raise ValueError(wm)
        tot = sum(w.values())
        return sum(w[lb] * rets[lb] for lb in lbs) / tot

    return score


def bt(panel, ma, name="live", cost=COST, trend=True, top_k=K, lo=None, hi=None, phase=0):
    """Live long-only K5 weekly engine with a pluggable composition `name`.
    Fixed WARMUP across variants. lo/hi accumulate only located rebalances; phase shifts grid."""
    score = make_score(panel)
    rets, turns, deployed = [], [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = WARMUP + phase
    while i + R < len(panel):
        sc = score(i, name).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= top_k:
            for s in sc.sort_values().index[-top_k:]:
                if (not trend) or panel.iloc[i][s] > ma.iloc[i][s]:
                    w[s] = 1 / top_k
        fwd = (panel.iloc[i + R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0)
        if lo is None or (lo <= i < hi):
            rets.append((w * fwd).sum() - (w - prev).abs().sum() * cost)
            turns.append((w - prev).abs().sum())
            deployed.append(float(w.sum()))
        prev = w
        i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return dict(net=0, sharpe=0, maxdd=0, worstwk=0, turnover=0, inmkt=0, n=len(rets))
    eq = np.cumprod(1 + rets); pk = np.maximum.accumulate(eq)
    return dict(net=eq[-1] - 1, sharpe=rets.mean() / rets.std() * np.sqrt(365 / R),
                maxdd=float(np.max((pk - eq) / pk)), worstwk=float(rets.min()),
                turnover=float(np.mean(turns)), inmkt=float(np.mean(deployed)), n=len(rets))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-01-01")
    args = ap.parse_args()

    syms = live_universe()
    panel = load_panel(syms, "1d")
    if panel.empty:
        sys.exit("No cache. Run: EXCHANGE_ID=kucoin python scripts/data_cache.py --update")
    panel.index = pd.to_datetime(panel.index, unit="ms")
    panel = panel.sort_index()
    panel = panel[panel.index >= args.start].dropna(axis=1, thresh=120)
    ma = panel.rolling(MA_DAYS).mean()
    idx = panel.index
    cut = int(len(panel) * SPLIT)
    yrs = (idx[-1] - idx[0]).days / 365

    print("=" * 84)
    print("P25 — momentum-horizon COMPOSITION robustness vs live equal-wt [14,30,60]")
    print("=" * 84)
    print(f"window {idx[0].date()}..{idx[-1].date()} (~{yrs:.1f}y, {len(panel)}d), "
          f"{panel.shape[1]} coins, cost {COST*1e4:.0f}bps/side, global warmup {WARMUP}d")
    print(f"OOS test half starts {idx[cut].date()} (~{(idx[-1]-idx[cut]).days}d)\n")

    pr = panel.reset_index(drop=True); mr = ma.reset_index(drop=True)
    ptr = panel.iloc[:cut].reset_index(drop=True); mtr = ma.iloc[:cut].reset_index(drop=True)
    pte = panel.iloc[cut:].reset_index(drop=True); mte = ma.iloc[cut:].reset_index(drop=True)
    folds = np.array_split(np.arange(len(panel)), 5)

    def wf(name):
        out = []
        for f in folds:
            seg = panel.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            ms = ma.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            out.append(bt(seg, ms, name=name))
        return out

    # ---- sanity: 'live' here must reproduce the canonical live multi-horizon numbers ----
    full_live = bt(pr, mr, name="live")
    print(f"[sanity] live composition FULL: net {full_live['net']*100:.0f}%  "
          f"Sharpe {full_live['sharpe']:.2f}  maxDD {full_live['maxdd']*100:.0f}%  "
          f"(cf P13/P20 offset-0 ~Sharpe 0.90)\n")

    # ---- (1) full / OOS / WF for every variant ----
    print("-" * 84)
    print("(1) Each composition variant — FULL, OOS test-half, 5-slice WF (vs live):")
    print(f"{'variant':<30}{'fullSh':>8}{'fullNet':>9}{'oosSh':>7}{'oosNet':>9}"
          f"{'oosDD':>7}{'WF+':>5}")
    wf_live = wf("live")
    rows = {}
    for name, lbs, wm, desc in VARIANTS:
        full = bt(pr, mr, name=name)
        oos = bt(pte, mte, name=name)
        w = wf_live if name == "live" else wf(name)
        pos = sum(1 for r in w if r["sharpe"] > 0)
        rows[name] = dict(full=full, oos=oos, wf=w, wfpos=pos)
        print(f"{desc:<30}{full['sharpe']:>8.2f}{full['net']*100:>8.0f}%"
              f"{oos['sharpe']:>7.2f}{oos['net']*100:>8.0f}%{oos['maxdd']*100:>6.0f}%"
              f"{pos:>4}/5")
    print(f"  WF Sharpe by slice (live): {[round(r['sharpe'],2) for r in wf_live]}")

    # ---- (2) honest TRAIN->TEST selection across the composition set ----
    print("\n" + "-" * 84)
    print("(2) HONEST TRAIN->TEST: pick the composition by TRAIN Sharpe, report its TEST:")
    tr = {name: bt(ptr, mtr, name=name) for name, _, _, _ in VARIANTS}
    pick = max(tr, key=lambda n: tr[n]["sharpe"])
    print("  TRAIN Sharpe: " + "  ".join(f"{n}={tr[n]['sharpe']:.2f}" for n, _, _, _ in VARIANTS))
    print(f"  -> TRAIN picks: {pick}")
    te_pick = rows[pick]["oos"]; te_live = rows["live"]["oos"]
    print(f"  -> TEST {pick}:   net {te_pick['net']*100:+.0f}%  Sharpe {te_pick['sharpe']:.2f}"
          f"  maxDD {te_pick['maxdd']*100:.0f}%  WF {rows[pick]['wfpos']}/5")
    print(f"  -> TEST live:     net {te_live['net']*100:+.0f}%  Sharpe {te_live['sharpe']:.2f}"
          f"  maxDD {te_live['maxdd']*100:.0f}%  WF {rows['live']['wfpos']}/5")
    verdict = ("BEATS live" if pick != "live" and te_pick["sharpe"] > te_live["sharpe"]
               and rows[pick]["wfpos"] >= 4 else "does NOT beat live")
    print(f"  VERDICT: TRAIN-selected composition {verdict} OOS "
          f"(need: picks non-live, higher OOS Sharpe, >=4/5 WF).")

    # ---- (3) bear-2022 located ----
    print("\n" + "-" * 84)
    print("(3) Located INSIDE the 2022 bear:")
    lo = int(idx.searchsorted(pd.Timestamp("2022-01-01")))
    hi = int(idx.searchsorted(pd.Timestamp("2022-12-31")))
    print(f"{'variant':<30}{'net%':>9}{'Sharpe':>8}{'maxDD':>8}{'worstWk':>9}{'inmkt':>7}")
    for name, lbs, wm, desc in VARIANTS:
        r = bt(pr, mr, name=name, lo=lo, hi=hi)
        print(f"{desc:<30}{r['net']*100:>8.1f}%{r['sharpe']:>8.2f}{r['maxdd']*100:>7.0f}%"
              f"{r['worstwk']*100:>8.1f}%{r['inmkt']*100:>6.0f}%")

    # ---- (4) phase-luck robustness (P20/P21 KILLER): does the TRAIN-picked blend's OOS edge
    #          survive all 7 weekly rebalance start offsets, or is it grid-alignment luck? ----
    print("\n" + "-" * 84)
    print(f"(4) PHASE robustness (P20/P21 killer): OOS Sharpe live vs {pick} across 7 offsets:")
    print(f"{'phase':>6}{'live Sh':>9}{pick+' Sh':>11}{'dSh':>8}{'live net%':>11}{pick+' net%':>11}")
    edges = []
    for ph in range(7):
        rl = bt(pte, mte, name="live", phase=ph)
        rp = bt(pte, mte, name=pick, phase=ph)
        edges.append(rp["sharpe"] - rl["sharpe"])
        print(f"{ph:>6}{rl['sharpe']:>9.2f}{rp['sharpe']:>11.2f}{rp['sharpe']-rl['sharpe']:>8.2f}"
              f"{rl['net']*100:>10.0f}%{rp['net']*100:>10.0f}%")
    edges = np.array(edges)
    print(f"  Sharpe edge ({pick}-live) across phases: mean {edges.mean():+.2f}  "
          f"std {edges.std():.2f}  positive {int((edges>0).sum())}/7")
    if pick == "live":
        print("  (TRAIN picked live itself — composition is already optimal on TRAIN.)")

    # ---- (5) 2021-> window cross-check ----
    print("\n" + "-" * 84)
    print("(5) 2021-> window cross-check (OOS test half), live vs each variant:")
    p21win = panel[panel.index >= "2021-01-01"]
    m21 = ma[ma.index >= "2021-01-01"]
    c2 = int(len(p21win) * SPLIT)
    pte2 = p21win.iloc[c2:].reset_index(drop=True); mte2 = m21.iloc[c2:].reset_index(drop=True)
    print(f"  OOS starts {p21win.index[c2].date()}")
    print(f"{'variant':<30}{'oosSh':>8}{'oosNet':>9}")
    for name, lbs, wm, desc in VARIANTS:
        r = bt(pte2, mte2, name=name)
        print(f"{desc:<30}{r['sharpe']:>8.2f}{r['net']*100:>8.0f}%")

    print("\n" + "=" * 84)
    print("Adopt a different composition only if a TRAIN-selected blend beats live OOS robustly")
    print("(>=4/5 WF AND higher OOS Sharpe AND survives the 7-phase test, mean edge>0 positive")
    print(">=5/7). Otherwise the live equal-wt [14,30,60] is the optimum — log as confirmation.")


if __name__ == "__main__":
    main()
