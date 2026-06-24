"""P21 — Risk-adjusted MULTI-horizon momentum ranking (vs the live raw multi-horizon).

The live signal ranks coins by the AVERAGE of raw 14/30/60d returns (multi_score). In a
crypto universe per-coin daily vol ranges ~3%..>10%, so a raw-return ranking structurally
over-weights the highest-vol names every week — a known drawdown source. factor_research
(2026-06-12) tested risk-adjusted momentum but ONLY on a single 30d horizon (it then lost
the SHIP to multi-horizon, which won on a different axis). The natural, untested combination
is risk-adjusted *multi-horizon*: normalise each horizon's return by the coin's volatility
BEFORE averaging, so the top-5 is selected on consistency-per-unit-risk, not raw magnitude.

Ranking variants tested (everything else = EXACT live config: top-5, dual-momentum px>100d MA
else cash, weekly R=7, equal weight, 15bps/side):
  raw       : mean_lb( P[i]/P[i-lb] - 1 )                          <- LIVE
  ra_pooled : mean_lb(ret_lb) / sigma60       (single trailing 60d daily-vol denominator)
  ra_perhz  : mean_lb( ret_lb / sigma_lb )    (each horizon by its OWN-window daily-vol)
  ra_z      : mean_lb( xs_zscore(ret_lb) )    (cross-sectional z per horizon, then average)

Honesty: variants are scored on a TRAIN half only; the winner is reported on the TEST half
and on a 5-slice walk-forward. Also located inside the 2022 bear (the regime where over-
picking high-vol names should hurt most). Reuses the validated p15 engine (bt structure,
multi_score, constants) and the same 2020-> cache panel as P15/P16/P20.

    python scripts/p21_riskadj_momentum.py [--start 2020-01-01]
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import K, R, SPLIT, MA_DAYS, LBS, COST  # noqa: E402

VOL_POOL = 60  # trailing daily-return window for the pooled-vol denominator


def make_score(panel, vol_pool=VOL_POOL):
    """Return a function score(i, mode) -> per-coin selection score at row i.

    pct_change is precomputed once on the full panel (trailing only; no look-ahead since we
    slice [..i] windows). All forms reduce to the raw multi_score when vols are equal across
    coins, so the comparison isolates the risk-normalisation effect.
    """
    ret_d = panel.pct_change()

    def horizon_ret(i, lb):
        return panel.iloc[i] / panel.iloc[i - lb] - 1

    def score(i, mode):
        if mode == "raw":
            return sum(horizon_ret(i, lb) for lb in LBS) / len(LBS)
        if mode == "ra_pooled":
            raw = sum(horizon_ret(i, lb) for lb in LBS) / len(LBS)
            sig = ret_d.iloc[i - vol_pool:i].std()
            return raw / sig.replace(0, np.nan)
        if mode == "ra_perhz":
            parts = []
            for lb in LBS:
                sig = ret_d.iloc[i - lb:i].std()
                parts.append((horizon_ret(i, lb)) / sig.replace(0, np.nan))
            return sum(parts) / len(parts)
        if mode == "ra_z":
            parts = []
            for lb in LBS:
                r = horizon_ret(i, lb)
                z = (r - r.mean()) / (r.std() if r.std() else np.nan)
                parts.append(z)
            return sum(parts) / len(parts)
        raise ValueError(mode)

    return score


def bt(panel, ma, mode="raw", cost=COST, trend=True, top_k=K, lo=None, hi=None,
       phase=0, vol_pool=VOL_POOL):
    """Live long-only multi-horizon K5 weekly engine with a pluggable ranking `mode`.
    If lo/hi given, only rebalances with lo<=i<hi are accumulated (located window).
    `phase` shifts the weekly rebalance grid start (P20 timing-luck robustness).
    `vol_pool` overrides the pooled-vol denominator window (ra_pooled only)."""
    score = make_score(panel, vol_pool=vol_pool)
    rets, turns, grosses, deployed = [], [], [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = max(max(LBS), vol_pool) + phase
    while i + R < len(panel):
        sc = score(i, mode).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= top_k:
            for s in sc.sort_values().index[-top_k:]:
                if (not trend) or panel.iloc[i][s] > ma.iloc[i][s]:
                    w[s] = 1 / top_k
        fwd = (panel.iloc[i + R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0)
        if lo is None or (lo <= i < hi):
            rets.append((w * fwd).sum() - (w - prev).abs().sum() * cost)
            turns.append((w - prev).abs().sum())
            grosses.append((w * fwd).sum())
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


MODES = [("raw", "raw multi-horizon (LIVE)"), ("ra_pooled", "risk-adj pooled-vol"),
         ("ra_perhz", "risk-adj per-horizon vol"), ("ra_z", "risk-adj xs-zscore")]


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

    print("=" * 80)
    print("P21 — risk-adjusted MULTI-horizon momentum ranking vs live raw multi-horizon")
    print("=" * 80)
    print(f"window {idx[0].date()}..{idx[-1].date()} (~{yrs:.1f}y, {len(panel)}d), "
          f"{panel.shape[1]} coins, cost {COST*1e4:.0f}bps/side")
    print(f"OOS test half starts {idx[cut].date()} "
          f"(~{(idx[-1]-idx[cut]).days}d)\n")

    pr = panel.reset_index(drop=True); mr = ma.reset_index(drop=True)
    ptr = panel.iloc[:cut].reset_index(drop=True); mtr = ma.iloc[:cut].reset_index(drop=True)
    pte = panel.iloc[cut:].reset_index(drop=True); mte = ma.iloc[cut:].reset_index(drop=True)
    folds = np.array_split(np.arange(len(panel)), 5)

    def wf(mode):
        out = []
        for f in folds:
            seg = panel.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            ms = ma.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            out.append(bt(seg, ms, mode=mode))
        return out

    # ---- (1) full / OOS / WF for every variant ----
    print("-" * 80)
    print("(1) Each ranking variant — FULL, OOS test-half, 5-slice WF (vs live = raw):")
    print(f"{'variant':<28}{'fullSh':>8}{'fullNet':>9}{'oosSh':>7}{'oosNet':>9}"
          f"{'oosDD':>7}{'WF+':>5}")
    wf_raw = wf("raw")
    base_wf_pos = sum(1 for r in wf_raw if r["sharpe"] > 0)
    rows = {}
    for mode, name in MODES:
        full = bt(pr, mr, mode=mode)
        oos = bt(pte, mte, mode=mode)
        w = wf_raw if mode == "raw" else wf(mode)
        pos = sum(1 for r in w if r["sharpe"] > 0)
        rows[mode] = dict(full=full, oos=oos, wf=w, wfpos=pos)
        print(f"{name:<28}{full['sharpe']:>8.2f}{full['net']*100:>8.0f}%"
              f"{oos['sharpe']:>7.2f}{oos['net']*100:>8.0f}%{oos['maxdd']*100:>6.0f}%"
              f"{pos:>4}/5")
    print(f"  WF Sharpe by slice (raw/live): {[round(r['sharpe'],2) for r in wf_raw]}")
    for mode, name in MODES[1:]:
        print(f"  WF Sharpe by slice ({mode}): "
              f"{[round(r['sharpe'],2) for r in rows[mode]['wf']]}  "
              f"beats raw {sum(1 for a,b in zip(rows[mode]['wf'],wf_raw) if a['sharpe']>b['sharpe'])}/5")

    # ---- (2) honest TRAIN->TEST selection across the variant set ----
    print("\n" + "-" * 80)
    print("(2) HONEST TRAIN->TEST: pick the variant by TRAIN Sharpe, report its TEST:")
    tr = {m: bt(ptr, mtr, mode=m) for m, _ in MODES}
    pick = max(tr, key=lambda m: tr[m]["sharpe"])
    print(f"  TRAIN Sharpe: " + "  ".join(f"{m}={tr[m]['sharpe']:.2f}" for m, _ in MODES))
    print(f"  -> TRAIN picks: {pick}")
    te_pick = rows[pick]["oos"]; te_live = rows["raw"]["oos"]
    print(f"  -> TEST {pick}:  net {te_pick['net']*100:+.0f}%  Sharpe {te_pick['sharpe']:.2f}"
          f"  maxDD {te_pick['maxdd']*100:.0f}%")
    print(f"  -> TEST raw(live): net {te_live['net']*100:+.0f}%  Sharpe {te_live['sharpe']:.2f}"
          f"  maxDD {te_live['maxdd']*100:.0f}%")
    verdict = ("BEATS live" if pick != "raw" and te_pick["sharpe"] > te_live["sharpe"]
               and rows[pick]["wfpos"] >= 4 else "does NOT beat live")
    print(f"  VERDICT: TRAIN-selected variant {verdict} OOS (need: picks non-raw, higher OOS "
          f"Sharpe, >=4/5 WF).")

    # ---- (3) bear-2022 located ----
    print("\n" + "-" * 80)
    print("(3) Located INSIDE the 2022 bear (where over-picking high-vol names should hurt most):")
    lo = int(idx.searchsorted(pd.Timestamp("2022-01-01")))
    hi = int(idx.searchsorted(pd.Timestamp("2022-12-31")))
    print(f"{'variant':<28}{'net%':>9}{'Sharpe':>8}{'maxDD':>8}{'worstWk':>9}{'inmkt':>7}")
    for mode, name in MODES:
        r = bt(pr, mr, mode=mode, lo=lo, hi=hi)
        print(f"{name:<28}{r['net']*100:>8.1f}%{r['sharpe']:>8.2f}{r['maxdd']*100:>7.0f}%"
              f"{r['worstwk']*100:>8.1f}%{r['inmkt']*100:>6.0f}%")

    # ---- (4) phase-luck robustness (P20): does ra_pooled's OOS Sharpe edge survive
    #          all 7 weekly rebalance start offsets, or is it grid-alignment luck? ----
    print("\n" + "-" * 80)
    print("(4) PHASE robustness (P20): OOS Sharpe raw vs ra_pooled across all 7 weekly offsets:")
    print(f"{'phase':>6}{'raw Sh':>9}{'ra Sh':>9}{'dSh':>8}{'raw net%':>10}{'ra net%':>10}")
    edges = []
    for ph in range(7):
        rr = bt(pte, mte, mode="raw", phase=ph)
        ra = bt(pte, mte, mode="ra_pooled", phase=ph)
        edges.append(ra["sharpe"] - rr["sharpe"])
        print(f"{ph:>6}{rr['sharpe']:>9.2f}{ra['sharpe']:>9.2f}{ra['sharpe']-rr['sharpe']:>8.2f}"
              f"{rr['net']*100:>9.0f}%{ra['net']*100:>9.0f}%")
    edges = np.array(edges)
    print(f"  Sharpe edge (ra-raw) across phases: mean {edges.mean():+.2f}  "
          f"std {edges.std():.2f}  positive {int((edges>0).sum())}/7")

    # ---- (5) vol-window sensitivity: is the edge specific to a 60d denominator? ----
    print("\n" + "-" * 80)
    print("(5) ra_pooled vol-window sensitivity (OOS) — is the edge specific to 60d?:")
    print(f"  raw(live) OOS Sharpe {rows['raw']['oos']['sharpe']:.2f}")
    print(f"{'vol_pool':>9}{'oosSh':>8}{'oosNet':>9}{'oosDD':>7}")
    for vp in (20, 30, 45, 60, 90, 120):
        r = bt(pte, mte, mode="ra_pooled", vol_pool=vp)
        print(f"{vp:>8}d{r['sharpe']:>8.2f}{r['net']*100:>8.0f}%{r['maxdd']*100:>6.0f}%")

    # ---- (6) alternate start window (2021->) for window-robustness ----
    print("\n" + "-" * 80)
    print("(6) 2021-> window cross-check (OOS test half):")
    p21win = panel[panel.index >= "2021-01-01"]
    m21 = ma[ma.index >= "2021-01-01"]
    c2 = int(len(p21win) * SPLIT)
    pte2 = p21win.iloc[c2:].reset_index(drop=True); mte2 = m21.iloc[c2:].reset_index(drop=True)
    r_raw2 = bt(pte2, mte2, mode="raw"); r_ra2 = bt(pte2, mte2, mode="ra_pooled")
    print(f"  OOS starts {p21win.index[c2].date()}  raw Sh {r_raw2['sharpe']:.2f} "
          f"(net {r_raw2['net']*100:+.0f}%) | ra_pooled Sh {r_ra2['sharpe']:.2f} "
          f"(net {r_ra2['net']*100:+.0f}%)")

    print("\n" + "=" * 80)
    print("Adopt a risk-adjusted ranking only if a TRAIN-selected variant beats live raw OOS")
    print("robustly (>=4/5 WF AND higher OOS Sharpe). Otherwise the live raw multi-horizon")
    print("ranking is the optimum for this lever — log as a dead-end.")


if __name__ == "__main__":
    main()
