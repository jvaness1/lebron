"""P20 — Rebalance-PHASE (weekday) timing-luck robustness of the LIVE config.

Every headline backtest in this repo (P13/P15/P16/P17/P18/P19, the live-config proof) uses a
SINGLE, fixed rebalance grid: the engine starts at i = max(LBS) = 60 and steps by R = 7 days,
so it always rebalances on the same calendar phase (one weekday). That phase is an arbitrary
artifact of where the loop happens to start -- it was never chosen for a reason. In reality the
live bot rebalances on whatever weekday the human runs the weekly job, and there are 7 equally
valid phases. If the documented edge only appears on the one phase the backtest happens to use,
a chunk of the "+3349% / Sharpe 0.9" proof is TIMING LUCK, and the real $100 -- rebalancing on
some other weekday -- would realize something different. This is a pure honesty / robustness
check, exactly in the P13/P16 spirit (correct an over-stated number), and a stated priority of
the consistency agenda.

GENUINELY NEW vs the LOG: this is the rebalance-PHASE lever (which of the 7 weekly offsets you
rebalance on). It is NOT rebalance FREQUENCY (P1 tested daily vs weekly), nor top_k (P19),
buffer band (P18), trend-MA (P17), gate (P0/P3/P11), skip (P5) or vol-sizing (P2). Offset 0
reproduces every prior backtest exactly (sanity-checked).

This is a DISPERSION measurement, NOT an optimisation. We do NOT pick the best phase -- that
would be the worst kind of overfit (the phase is unknowable ex-ante; the live weekday is just
whenever the human runs it). The deliverable is the SPREAD across all 7 phases:
  * If the edge is real, all/most phases are positive and the spread around the headline is
    modest -> the proof is not a timing artifact (confidence up).
  * If only the documented phase (offset 0) works, or offset 0 sits at the favourable extreme,
    that is a fragility / over-statement to flag (honesty correction, like P13's maxDD fix).

Method (repo convention):
  * Data: cached KuCoin daily, 2020-> multi-cycle panel (full 2022 bear in input).
  * Engine: EXACT live engine (multi-horizon 14/30/60d momentum, top-5, px>100d MA else cash,
    weekly rebalance, equal weight, 15bps/side) -- only the START OFFSET shifts (0..6 days).
  * Report each phase full-window + OOS test-half; then the cross-phase distribution (mean,
    std, min, max, % positive) and where offset 0 (every prior backtest) sits in it.
  * Mechanism: the 2022 bear across phases -- is the -66% bear loss phase-stable or did the
    documented phase get a lucky/unlucky draw?

Caveat: one multi-cycle panel, survivors-only, 7 overlapping phases of the SAME underlying
series (highly correlated -- this measures grid-alignment luck, not 7 independent histories).

    /Users/jamesvaness/hermes-trading/.venv/bin/python scripts/p20_rebalance_phase.py
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


def bt_phase(panel, ma, offset, cost=COST, trend=True, lo=None, hi=None):
    """Live K5 weekly multi-horizon engine, rebalance grid SHIFTED by `offset` days.
    Identical to p15.bt() at offset 0. If lo/hi given, only ACCUMULATE rebalances whose
    index is in [lo,hi) (warmup/scoring still use full trailing history -> time-OOS slice)."""
    rets, turns, deployed = [], [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = max(LBS) + offset
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= K:
            for s in sc.sort_values().index[-K:]:
                if (not trend) or panel.iloc[i][s] > ma.iloc[i][s]:
                    w[s] = 1 / K
        fwd = (panel.iloc[i + R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0)
        if lo is None or (lo <= i < hi):
            rets.append((w * fwd).sum() - (w - prev).abs().sum() * cost)
            turns.append(float((w - prev).abs().sum()))
            deployed.append(float(w.sum()))
        prev = w
        i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return dict(net=0, sharpe=0, maxdd=0, worstwk=0, turnover=0, inmkt=0, n=len(rets))
    eq = np.cumprod(1 + rets)
    pk = np.maximum.accumulate(eq)
    return dict(net=float(eq[-1] - 1), sharpe=float(rets.mean() / rets.std() * np.sqrt(365 / R)),
                maxdd=float(np.max((pk - eq) / pk)), worstwk=float(rets.min()),
                turnover=float(np.mean(turns)), inmkt=float(np.mean(deployed)), n=len(rets))


def dist(vals):
    a = np.array(vals, dtype=float)
    return a.mean(), a.std(), a.min(), a.max()


def idx_range(index, lo_str, hi_str):
    return (int(index.searchsorted(pd.Timestamp(lo_str))),
            int(index.searchsorted(pd.Timestamp(hi_str))))


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

    ma_full = panel.rolling(MA_DAYS).mean()
    panel_r = panel.reset_index(drop=True)
    ma_r = ma_full.reset_index(drop=True)
    cut = int(len(panel) * SPLIT)

    print("=" * 90)
    print("P20 — rebalance-PHASE (weekday) timing-luck robustness of the LIVE config")
    print("=" * 90)
    print(f"panel {idx[0].date()}..{idx[-1].date()}  (~{yrs:.1f}y, {len(panel)} days, "
          f"{panel.shape[1]} coins)  cost {COST*1e4:.0f}bps/side  R={R}d  K={K}  LBS={LBS}")
    print(f"7 weekly phases (start offset 0..6 days). Offset 0 == EVERY prior backtest.\n")

    # ---------------- (1) each phase: full window + OOS test half ----------------
    print("-" * 90)
    print("(1) per-phase full-window and OOS(test-half) — the live engine on each weekday grid:")
    print(f"{'offset':<8}{'fullNet%':>11}{'fullSh':>8}{'fullDD':>8}{'worstWk':>9}"
          f"{'inmkt':>7}{'   |':>4}{'oosNet%':>10}{'oosSh':>8}{'oosDD':>7}")
    full_rows, oos_rows = {}, {}
    for o in OFFSETS:
        full = bt_phase(panel_r, ma_r, o)
        oos = bt_phase(panel_r.iloc[cut:].reset_index(drop=True),
                       ma_r.iloc[cut:].reset_index(drop=True), o)
        full_rows[o], oos_rows[o] = full, oos
        tag = "  <-PRIOR-BTs" if o == 0 else ""
        print(f"{o:<8}{full['net']*100:>10.0f}%{full['sharpe']:>8.2f}{full['maxdd']*100:>7.0f}%"
              f"{full['worstwk']*100:>8.1f}%{full['inmkt']*100:>6.0f}%{'   |':>4}"
              f"{oos['net']*100:>9.0f}%{oos['sharpe']:>8.2f}{oos['maxdd']*100:>6.0f}%{tag}")

    # ---------------- (2) cross-phase distribution + where offset 0 sits ----------------
    print("\n" + "-" * 90)
    print("(2) cross-phase DISPERSION (the timing-luck band around the headline numbers):")
    for label, rows in [("FULL", full_rows), ("OOS ", oos_rows)]:
        for metric in ["net", "sharpe", "maxdd"]:
            vals = [rows[o][metric] for o in OFFSETS]
            m, sd, lo, hi = dist(vals)
            o0 = rows[0][metric]
            # percentile rank of offset-0 among the 7 phases
            rank = sum(1 for v in vals if v <= o0) / len(vals)
            scale = 100 if metric in ("net", "maxdd") else 1
            unit = "%" if metric in ("net", "maxdd") else ""
            print(f"  [{label}] {metric:<7} mean {m*scale:+.2f}{unit}  std {sd*scale:.2f}{unit}  "
                  f"range [{lo*scale:+.2f},{hi*scale:+.2f}]{unit}  "
                  f"offset0 {o0*scale:+.2f}{unit} (pctile {rank*100:.0f})")
    pos_full = sum(1 for o in OFFSETS if full_rows[o]["net"] > 0)
    pos_oos = sum(1 for o in OFFSETS if oos_rows[o]["net"] > 0)
    sh_pos_full = sum(1 for o in OFFSETS if full_rows[o]["sharpe"] > 0)
    print(f"\n  phases with POSITIVE full-window net: {pos_full}/7   (Sharpe>0: {sh_pos_full}/7)")
    print(f"  phases with POSITIVE OOS net:         {pos_oos}/7")

    # ---------------- (3) mechanism: the 2022 bear across phases ----------------
    print("\n" + "-" * 90)
    print("(3) mechanism — the 2022 BEAR across all 7 phases (is the bear loss phase-stable?):")
    lo, hi = idx_range(idx, "2022-01-01", "2022-12-31")
    print(f"    {'offset':<8}{'rebals':>7}{'net%':>9}{'Sharpe':>8}{'maxDD':>7}"
          f"{'worstWk':>9}{'inmkt':>7}")
    bear_nets = []
    for o in OFFSETS:
        r = bt_phase(panel_r, ma_r, o, lo=lo, hi=hi)
        bear_nets.append(r["net"])
        tag = "  <-PRIOR-BTs" if o == 0 else ""
        print(f"    {o:<8}{r['n']:>7}{r['net']*100:>8.1f}%{r['sharpe']:>8.2f}"
              f"{r['maxdd']*100:>6.0f}%{r['worstwk']*100:>8.1f}%{r['inmkt']*100:>6.0f}%{tag}")
    bm, bsd, blo, bhi = dist(bear_nets)
    print(f"  bear net across phases: mean {bm*100:+.1f}%  std {bsd*100:.1f}%  "
          f"range [{blo*100:+.1f},{bhi*100:+.1f}]%  offset0 {bear_nets[0]*100:+.1f}%")

    # ---------------- verdict scaffold ----------------
    print("\n" + "=" * 90)
    fm, fsd, flo, fhi = dist([full_rows[o]["sharpe"] for o in OFFSETS])
    o0_sh = full_rows[0]["sharpe"]
    nets = [full_rows[o]["net"] for o in OFFSETS]
    # Sharpe is the phase-robust summary; cumulative net compounds tiny timing diffs hugely.
    net_cv = np.std(nets) / np.mean(nets)
    all_pos = (sh_pos_full == 7)
    # direction matters: offset-0 ABOVE mean = favourable (over-stated); BELOW = conservative
    o0_above = o0_sh > fm + fsd
    o0_below = o0_sh < fm - fsd
    print(f"full-window Sharpe across phases: {fm:.2f} +/- {fsd:.2f}  (offset0 = {o0_sh:.2f}); "
          f"net coeff-of-variation across phases = {net_cv:.2f}")
    if all_pos and o0_above:
        print("OVER-STATED: offset-0 (every prior backtest) sits ABOVE 1 std of the phase mean — "
              "the documented Sharpe is a favourable timing draw. Edge real but discount toward "
              "the phase mean.")
    elif all_pos:
        verdict = ("CONSERVATIVE" if o0_below else "TYPICAL")
        print(f"ROBUST (edge survives timing phase): all 7 weekly phases Sharpe-positive; the "
              f"documented (offset-0) backtest is a {verdict} draw "
              f"({'below' if o0_below else 'within'} 1 std of the phase mean). The edge is NOT a "
              f"rebalance-grid artifact. CAVEAT: cumulative-RETURN headlines are wildly "
              f"phase-sensitive (net CV {net_cv:.2f}) — quote Sharpe (phase-std {fsd:.2f}), not "
              f"point cumulative return. No config change (phase is unknowable ex-ante).")
    else:
        print(f"FRAGILE: only {sh_pos_full}/7 phases are Sharpe-positive — the edge is partly a "
              "rebalance-grid artifact. Flag as a material caveat on every headline number.")


if __name__ == "__main__":
    main()
