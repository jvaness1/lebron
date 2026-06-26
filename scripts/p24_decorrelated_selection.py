"""P24 — Correlation-aware (cluster-decorrelated) selection within the momentum book.

The live signal ranks coins by raw multi-horizon momentum (avg 14/30/60d return), takes the
strict top-5, and holds those above their 100d MA (else cash). In a long-only crypto universe
the top-5 are frequently 5 highly-correlated names (all L1/L2 alts ripping together) — the LOG
calls the book "dominated by common market beta" (P12) and notes it "rode the high-beta cluster
into the 2022 bear" (P16). EVERY consistency lever tried so far (P11 weight-cap, P19 wider-K,
P12 low-vol sleeve) turned out to be the SAME partial-cash dial: cut net exposure, lose return
1:1. Decorrelation is a genuinely DIFFERENT axis — keep all 5 slots fully invested but choose
LESS-redundant names, so a drawdown cut (if any) need NOT cost return 1:1.

Construction (everything else = EXACT live config: K5, dual-momentum px>100d MA, weekly R=7,
equal 1/5 weight, 15bps/side):
  raw       : strict top-5 by momentum, cash any below MA            <- LIVE
  trendpool : take the 5 HIGHEST-momentum names that pass trend       (isolates BACKFILL effect:
              (backfill the slots live would have cashed)              raw can hold <5; this fills)
  decorr_t  : among trend-passing candidates ranked by momentum, greedily pick 5 skipping any
              whose trailing-CORR_WIN return-correlation to an already-picked name exceeds tau;
              if the cap leaves <5, BACKFILL by momentum to keep deployment == trendpool. This
              isolates WHICH 5 (decorrelated vs pure-momentum) at EQUAL gross exposure.
  decorr_strict : same but NO backfill (may hold <5 -> de-levers). Reported only to show that a
              no-backfill DD cut would just be partial cash again (the P11/P19 trap).

Honesty: tau is picked on a TRAIN half only; the winner is reported on the TEST half, a 5-slice
walk-forward, the P20/P21 7-phase rebalance-grid robustness test (the killer of P21/P23), and
located inside the 2022 bear. inmkt (mean deployment) is printed everywhere so a DD reduction
that is merely lower deployment is exposed as partial cash, not decorrelation.

    python scripts/p24_decorrelated_selection.py [--start 2020-01-01]
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import K, R, SPLIT, MA_DAYS, LBS, COST  # noqa: E402

CORR_WIN = 60  # trailing daily-return window for the pairwise correlation cap


def bt(panel, ma, mode="raw", tau=1.0, cost=COST, top_k=K, lo=None, hi=None,
       phase=0, corr_win=CORR_WIN):
    """Live long-only multi-horizon K5 weekly engine with a pluggable SELECTION `mode`.

    raw           : strict top-k by momentum, then cash any below MA (EXACT live).
    trendpool     : 5 highest-momentum names that PASS trend (backfills live's cashed slots).
    decorr        : trend-passing candidates by momentum, greedy pick skipping corr>tau,
                    BACKFILL by momentum to top_k (deployment == trendpool).
    decorr_strict : decorr with NO backfill (may hold <top_k -> de-levers).

    If lo/hi given, only rebalances with lo<=i<hi are accumulated. `phase` shifts the weekly
    rebalance grid start (P20 timing-luck robustness)."""
    ret_d = panel.pct_change()

    def multi_score(i):
        return sum(panel.iloc[i] / panel.iloc[i - lb] - 1 for lb in LBS) / len(LBS)

    rets, turns, grosses, deployed, nheld = [], [], [], [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = max(max(LBS), corr_win) + phase
    while i + R < len(panel):
        sc = multi_score(i).dropna()
        px = panel.iloc[i]
        above = ma.iloc[i]
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= 1:
            ranked = sc.sort_values(ascending=False)
            if mode == "raw":
                # strict top-k, cash any below MA
                for s in ranked.index[:top_k]:
                    if px[s] > above[s]:
                        w[s] = 1 / top_k
            else:
                # trend-passing candidate pool, momentum-ranked
                cand = [s for s in ranked.index if px[s] > above[s]]
                if mode == "trendpool":
                    chosen = cand[:top_k]
                else:  # decorr / decorr_strict
                    cm = ret_d.iloc[i - corr_win:i][cand].corr()
                    chosen = []
                    for s in cand:
                        if len(chosen) >= top_k:
                            break
                        if all(abs(cm.loc[s, c]) <= tau for c in chosen):
                            chosen.append(s)
                    if mode == "decorr" and len(chosen) < top_k:
                        for s in cand:  # backfill by momentum to keep gross == trendpool
                            if len(chosen) >= top_k:
                                break
                            if s not in chosen:
                                chosen.append(s)
                for s in chosen:
                    w[s] = 1 / top_k
        fwd = (panel.iloc[i + R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0)
        if lo is None or (lo <= i < hi):
            rets.append((w * fwd).sum() - (w - prev).abs().sum() * cost)
            turns.append((w - prev).abs().sum())
            grosses.append((w * fwd).sum())
            deployed.append(float(w.sum()))
            nheld.append(int((w > 0).sum()))
        prev = w
        i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return dict(net=0, sharpe=0, maxdd=0, worstwk=0, turnover=0, inmkt=0,
                    nheld=0, n=len(rets))
    eq = np.cumprod(1 + rets); pk = np.maximum.accumulate(eq)
    return dict(net=eq[-1] - 1, sharpe=rets.mean() / rets.std() * np.sqrt(365 / R),
                maxdd=float(np.max((pk - eq) / pk)), worstwk=float(rets.min()),
                turnover=float(np.mean(turns)), inmkt=float(np.mean(deployed)),
                nheld=float(np.mean(nheld)), n=len(rets))


TAUS = [0.6, 0.7, 0.8, 0.9]


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
    print("P24 — correlation-aware (cluster-decorrelated) selection vs live strict top-5")
    print("=" * 84)
    print(f"window {idx[0].date()}..{idx[-1].date()} (~{yrs:.1f}y, {len(panel)}d), "
          f"{panel.shape[1]} coins, cost {COST*1e4:.0f}bps/side, corr_win {CORR_WIN}d")
    print(f"OOS test half starts {idx[cut].date()} (~{(idx[-1]-idx[cut]).days}d)\n")

    pr = panel.reset_index(drop=True); mr = ma.reset_index(drop=True)
    ptr = panel.iloc[:cut].reset_index(drop=True); mtr = ma.iloc[:cut].reset_index(drop=True)
    pte = panel.iloc[cut:].reset_index(drop=True); mte = ma.iloc[cut:].reset_index(drop=True)
    folds = np.array_split(np.arange(len(panel)), 5)

    def wf(mode, tau=1.0):
        out = []
        for f in folds:
            seg = panel.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            ms = ma.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            out.append(bt(seg, ms, mode=mode, tau=tau))
        return out

    # ---- sanity: trendpool with no below-MA cashing should hold ~5; raw can hold <5 ----
    raw_full = bt(pr, mr, mode="raw")
    tp_full = bt(pr, mr, mode="trendpool")
    print("-" * 84)
    print("SANITY — mean names held (raw can cash below-MA slots; trendpool backfills):")
    print(f"  raw(live)  nheld {raw_full['nheld']:.2f}  inmkt {raw_full['inmkt']*100:.0f}%")
    print(f"  trendpool  nheld {tp_full['nheld']:.2f}  inmkt {tp_full['inmkt']*100:.0f}%\n")

    # ---- (1) full / OOS / WF for live, backfill, and each tau ----
    print("-" * 84)
    print("(1) FULL, OOS test-half, 5-slice WF (vs live = raw). inmkt exposes de-levering:")
    print(f"{'variant':<22}{'fullSh':>8}{'fullNet':>9}{'oosSh':>7}{'oosNet':>9}"
          f"{'oosDD':>7}{'oosInmkt':>9}{'WF+':>5}")
    variants = [("raw", "raw top-5 (LIVE)", 1.0),
                ("trendpool", "trendpool backfill", 1.0)]
    variants += [("decorr", f"decorr tau={t}", t) for t in TAUS]
    rows = {}
    for mode, name, tau in variants:
        key = mode if mode != "decorr" else f"decorr{tau}"
        full = bt(pr, mr, mode=mode, tau=tau)
        oos = bt(pte, mte, mode=mode, tau=tau)
        w = wf(mode, tau)
        pos = sum(1 for r in w if r["sharpe"] > 0)
        rows[key] = dict(full=full, oos=oos, wf=w, wfpos=pos, tau=tau, mode=mode)
        print(f"{name:<22}{full['sharpe']:>8.2f}{full['net']*100:>8.0f}%"
              f"{oos['sharpe']:>7.2f}{oos['net']*100:>8.0f}%{oos['maxdd']*100:>6.0f}%"
              f"{oos['inmkt']*100:>8.0f}%{pos:>4}/5")

    # ---- (2) honest TRAIN->TEST selection of tau (vs live raw) ----
    print("\n" + "-" * 84)
    print("(2) HONEST TRAIN->TEST: pick the decorr tau by TRAIN Sharpe, report its TEST:")
    tr = {t: bt(ptr, mtr, mode="decorr", tau=t) for t in TAUS}
    tr_raw = bt(ptr, mtr, mode="raw")
    pick = max(tr, key=lambda t: tr[t]["sharpe"])
    print(f"  TRAIN Sharpe raw(live)={tr_raw['sharpe']:.2f} | "
          + "  ".join(f"tau{t}={tr[t]['sharpe']:.2f}" for t in TAUS))
    print(f"  -> TRAIN picks: decorr tau={pick}")
    te_pick = rows[f"decorr{pick}"]["oos"]; te_live = rows["raw"]["oos"]
    print(f"  -> TEST decorr tau={pick}: net {te_pick['net']*100:+.0f}%  "
          f"Sharpe {te_pick['sharpe']:.2f}  maxDD {te_pick['maxdd']*100:.0f}%  "
          f"inmkt {te_pick['inmkt']*100:.0f}%")
    print(f"  -> TEST raw(live):        net {te_live['net']*100:+.0f}%  "
          f"Sharpe {te_live['sharpe']:.2f}  maxDD {te_live['maxdd']*100:.0f}%  "
          f"inmkt {te_live['inmkt']*100:.0f}%")
    beat = (te_pick["sharpe"] > te_live["sharpe"] and rows[f"decorr{pick}"]["wfpos"] >= 4)
    print(f"  VERDICT: TRAIN-selected decorr {'BEATS' if beat else 'does NOT beat'} live OOS "
          f"(need: higher OOS Sharpe AND >=4/5 WF).")

    # ---- (3) bear-2022 located (the cluster-crash regime decorrelation should help most) ----
    print("\n" + "-" * 84)
    print("(3) Located INSIDE the 2022 bear (where holding one correlated cluster should hurt most):")
    lo = int(idx.searchsorted(pd.Timestamp("2022-01-01")))
    hi = int(idx.searchsorted(pd.Timestamp("2022-12-31")))
    print(f"{'variant':<22}{'net%':>9}{'Sharpe':>8}{'maxDD':>8}{'worstWk':>9}{'inmkt':>7}")
    for mode, name, tau in variants:
        r = bt(pr, mr, mode=mode, tau=tau, lo=lo, hi=hi)
        print(f"{name:<22}{r['net']*100:>8.1f}%{r['sharpe']:>8.2f}{r['maxdd']*100:>7.0f}%"
              f"{r['worstwk']*100:>8.1f}%{r['inmkt']*100:>6.0f}%")

    # ---- (4) phase-luck robustness (P20/P21 killer): TRAIN-picked tau across all 7 offsets ----
    print("\n" + "-" * 84)
    print(f"(4) PHASE robustness (P20): OOS Sharpe raw vs decorr tau={pick} across all 7 offsets:")
    print(f"{'phase':>6}{'raw Sh':>9}{'dec Sh':>9}{'dSh':>8}{'raw net%':>10}{'dec net%':>10}")
    edges = []
    for ph in range(7):
        rr = bt(pte, mte, mode="raw", phase=ph)
        de = bt(pte, mte, mode="decorr", tau=pick, phase=ph)
        edges.append(de["sharpe"] - rr["sharpe"])
        print(f"{ph:>6}{rr['sharpe']:>9.2f}{de['sharpe']:>9.2f}{de['sharpe']-rr['sharpe']:>8.2f}"
              f"{rr['net']*100:>9.0f}%{de['net']*100:>9.0f}%")
    edges = np.array(edges)
    print(f"  Sharpe edge (decorr-raw) across phases: mean {edges.mean():+.2f}  "
          f"std {edges.std():.2f}  positive {int((edges>0).sum())}/7")

    # ---- (5) decorr_strict (no backfill) — show a DD cut here would just be partial cash ----
    print("\n" + "-" * 84)
    print("(5) decorr_strict (NO backfill — may hold <5): is any DD cut just de-levering?")
    print(f"{'tau':>6}{'oosSh':>8}{'oosNet':>9}{'oosDD':>7}{'oosInmkt':>9}{'nheld':>7}")
    for t in TAUS:
        r = bt(pte, mte, mode="decorr_strict", tau=t)
        print(f"{t:>6}{r['sharpe']:>8.2f}{r['net']*100:>8.0f}%{r['maxdd']*100:>6.0f}%"
              f"{r['inmkt']*100:>8.0f}%{r['nheld']:>7.2f}")

    # ---- (6) 2021-> window cross-check ----
    print("\n" + "-" * 84)
    print("(6) 2021-> window cross-check (OOS test half):")
    p21 = panel[panel.index >= "2021-01-01"]; m21 = ma[ma.index >= "2021-01-01"]
    c2 = int(len(p21) * SPLIT)
    pte2 = p21.iloc[c2:].reset_index(drop=True); mte2 = m21.iloc[c2:].reset_index(drop=True)
    r_raw2 = bt(pte2, mte2, mode="raw"); r_de2 = bt(pte2, mte2, mode="decorr", tau=pick)
    print(f"  OOS starts {p21.index[c2].date()}  raw Sh {r_raw2['sharpe']:.2f} "
          f"(net {r_raw2['net']*100:+.0f}%) | decorr tau={pick} Sh {r_de2['sharpe']:.2f} "
          f"(net {r_de2['net']*100:+.0f}%)")

    print("\n" + "=" * 84)
    print("Adopt decorrelated selection only if a TRAIN-selected tau beats live raw OOS robustly")
    print("(>=4/5 WF AND higher OOS Sharpe) AND the edge survives the 7-phase test at EQUAL inmkt")
    print("(else any DD cut is just partial cash). Otherwise live strict top-5 is the optimum.")


if __name__ == "__main__":
    main()
