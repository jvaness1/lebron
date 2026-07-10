"""P26 — PRE-REGISTERED contribution-equalized momentum (the P25 lead, done right).

P25 (2026-06-29) found the live signal — rank by the EQUAL-WEIGHT average of RAW 14/30/60d
returns — is implicitly a SLOW-momentum signal (raw averaging over-weights the 60d horizon,
whose return magnitude is ~2-4x the 14d), and that equalizing each horizon's CONTRIBUTION via a
cross-sectional RANK-average ("rankavg") beats it on TRAIN + 3 windows AND — unlike the phase-luck
variants P21/P23 — SURVIVES the P20/P21 7-phase grid-luck killer. But P25 found rankavg inside a
10-variant sweep, so its result carries multiple-comparisons risk.

P26 removes that risk by PRE-REGISTRATION: rankavg is the SINGLE, ex-ante hypothesis here, chosen
for a MECHANISM reason (equalize per-horizon contribution), not by a data sweep. The decision rule
below is committed BEFORE looking at any TEST/phase output.

  ┌─ PRE-REGISTERED DECISION RULE (committed ex-ante) ────────────────────────────────────────┐
  │ Signal change: rank coins by the equal-weight average of the cross-sectional RANK of each  │
  │ horizon's raw return (14/30/60d), replacing the average of the raw returns. EVERYTHING      │
  │ else identical to live (top-5, dual-mom px>100d MA else cash, weekly R=7, eq wt, 15bps).   │
  │ Adopt rankavg -> write candidate ONLY IF ALL hold on the deepest 2020-> panel:              │
  │   (A) 7-phase OOS Sharpe edge (rankavg-live): mean > 0 AND positive in >=5/7 phases.         │
  │   (B) 7-phase FULL-window Sharpe edge: mean > 0 AND positive in >=5/7 phases.                │
  │   (C) 5-slice walk-forward (offset-0): rankavg Sharpe > live in >=4/5 slices.                │
  │   (D) bear-2022 located: rankavg net NOT materially worse than live (>= live - 2pp).         │
  │   (E) independent 2017-2020 window: rankavg net >= live net (directional confirm).           │
  │ Any failure -> KILL P26; log the live composition as the confirmed optimum for this lever.  │
  └─────────────────────────────────────────────────────────────────────────────────────────────┘

Reuses the P25/P15 engine verbatim (bt/make_score) so 'live' reproduces the canonical numbers.
wshort (the 2nd robust P25 variant) is REPORTED as a sidecar for context but is NOT the decision.

    python scripts/p26_rankavg_preregistered.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import R, SPLIT, MA_DAYS, COST  # noqa: E402
from p25_horizon_composition import bt, WARMUP  # noqa: E402  (validated engine)

PRE = "rankavg"   # the SINGLE pre-registered variant
LIVE = "live"


def seven_phase(ppanel, pma, name_a, name_b):
    """Sharpe edge (a-b) across all 7 weekly rebalance start offsets on a given (panel, ma)."""
    edges, a_sh, b_sh = [], [], []
    for ph in range(7):
        ra = bt(ppanel, pma, name=name_a, phase=ph)
        rb = bt(ppanel, pma, name=name_b, phase=ph)
        edges.append(ra["sharpe"] - rb["sharpe"])
        a_sh.append(ra["sharpe"]); b_sh.append(rb["sharpe"])
    edges = np.array(edges)
    return dict(edges=edges, mean=float(edges.mean()), std=float(edges.std()),
                pos=int((edges > 0).sum()), a=a_sh, b=b_sh)


def main():
    syms = live_universe()
    panel = load_panel(syms, "1d")
    if panel.empty:
        sys.exit("No cache. Run: EXCHANGE_ID=kucoin python scripts/data_cache.py --update")
    panel.index = pd.to_datetime(panel.index, unit="ms")
    panel = panel.sort_index()

    def prep(start, end=None):
        p = panel[panel.index >= start]
        if end:
            p = p[p.index < end]
        p = p.dropna(axis=1, thresh=120)
        m = p.rolling(MA_DAYS).mean()
        return p, m

    p20, m20 = prep("2020-01-01")
    idx = p20.index
    cut = int(len(p20) * SPLIT)
    yrs = (idx[-1] - idx[0]).days / 365

    print("=" * 86)
    print("P26 — PRE-REGISTERED rankavg (contribution-equalized momentum) vs LIVE raw-avg [14,30,60]")
    print("=" * 86)
    print(f"deep panel {idx[0].date()}..{idx[-1].date()} (~{yrs:.1f}y, {len(p20)}d), "
          f"{p20.shape[1]} coins, {COST*1e4:.0f}bps/side, global warmup {WARMUP}d")
    print(f"2020-split OOS test half starts {idx[cut].date()} (~{(idx[-1]-idx[cut]).days}d)")
    print("Decision variant PRE-REGISTERED = 'rankavg' (single ex-ante hypothesis).\n")

    pr = p20.reset_index(drop=True); mr = m20.reset_index(drop=True)
    pte = p20.iloc[cut:].reset_index(drop=True); mte = m20.iloc[cut:].reset_index(drop=True)

    # ---- sanity ----
    fl = bt(pr, mr, name=LIVE)
    print(f"[sanity] live FULL: net {fl['net']*100:.0f}%  Sharpe {fl['sharpe']:.2f}  "
          f"maxDD {fl['maxdd']*100:.0f}%  (cf P13/P20 offset-0 ~0.90)\n")

    # ---- headline full & OOS, live vs rankavg (+ wshort sidecar) ----
    print("-" * 86)
    print("(0) Full-window & 2020-split OOS (offset-0), live vs rankavg [+wshort sidecar]:")
    print(f"{'variant':<12}{'fullSh':>8}{'fullNet':>10}{'fullDD':>8}"
          f"{'oosSh':>8}{'oosNet':>10}{'oosDD':>8}{'turn':>7}")
    for nm in [LIVE, PRE, "wshort"]:
        f = bt(pr, mr, name=nm); o = bt(pte, mte, name=nm)
        print(f"{nm:<12}{f['sharpe']:>8.2f}{f['net']*100:>9.0f}%{f['maxdd']*100:>7.0f}%"
              f"{o['sharpe']:>8.2f}{o['net']*100:>9.0f}%{o['maxdd']*100:>7.0f}%{f['turnover']:>7.2f}")

    # ---- (A) 7-phase OOS ----
    print("\n" + "-" * 86)
    print("(A) 7-phase OOS Sharpe (2020-split test half) — THE grid-luck killer:")
    A = seven_phase(pte, mte, PRE, LIVE)
    print(f"  phase:   " + "  ".join(f"{ph}" for ph in range(7)))
    print(f"  live Sh: " + "  ".join(f"{s:.2f}" for s in A["b"]))
    print(f"  rank Sh: " + "  ".join(f"{s:.2f}" for s in A["a"]))
    print(f"  edge:    " + "  ".join(f"{e:+.2f}" for e in A["edges"]))
    print(f"  => mean {A['mean']:+.2f}  std {A['std']:.2f}  positive {A['pos']}/7  "
          f"[need mean>0 AND >=5/7]")

    # ---- (B) 7-phase FULL ----
    print("\n" + "-" * 86)
    print("(B) 7-phase FULL-window Sharpe edge (rankavg - live):")
    B = seven_phase(pr, mr, PRE, LIVE)
    print(f"  edge:    " + "  ".join(f"{e:+.2f}" for e in B["edges"]))
    print(f"  => mean {B['mean']:+.2f}  std {B['std']:.2f}  positive {B['pos']}/7  "
          f"[need mean>0 AND >=5/7]")

    # ---- (C) 5-slice WF (offset-0) ----
    print("\n" + "-" * 86)
    print("(C) 5-slice walk-forward (offset-0), rankavg vs live per-slice Sharpe:")
    folds = np.array_split(np.arange(len(p20)), 5)
    wins = 0; details = []
    for k, f in enumerate(folds):
        seg = p20.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
        ms = m20.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
        ra = bt(seg, ms, name=PRE)["sharpe"]; lv = bt(seg, ms, name=LIVE)["sharpe"]
        w = ra > lv; wins += int(w)
        details.append((k, lv, ra, w))
        print(f"  slice {k}: live {lv:>6.2f}   rankavg {ra:>6.2f}   {'WIN' if w else '.'}")
    print(f"  => rankavg beats live in {wins}/5 slices  [need >=4/5]")

    # ---- (C2) diagnostic: is C's failure offset-0 grid-luck, or a real regime weakness? ----
    print("  diagnostic — same WF but per-slice Sharpe AVERAGED across all 7 phases:")
    winsp = 0
    for k, f in enumerate(folds):
        seg = p20.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
        ms = m20.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
        la = [bt(seg, ms, name=LIVE, phase=ph)["sharpe"] for ph in range(7)]
        ra = [bt(seg, ms, name=PRE, phase=ph)["sharpe"] for ph in range(7)]
        lv = float(np.mean(la)); rv = float(np.mean(ra)); w = rv > lv; winsp += int(w)
        posph = sum(1 for a, b in zip(ra, la) if a > b)
        print(f"    slice {k}: live {lv:>6.2f}  rankavg {rv:>6.2f}  {'WIN' if w else '.'}"
              f"   (rankavg beats live in {posph}/7 phases here)")
    print(f"    => phase-averaged WF: rankavg beats live in {winsp}/5 slices "
          f"(if still <4/5, C's failure is a REAL regime weakness, not offset-0 grid-luck)")

    # ---- (D) bear-2022 located ----
    print("\n" + "-" * 86)
    print("(D) Located INSIDE the 2022 bear (offset-0):")
    lo = int(idx.searchsorted(pd.Timestamp("2022-01-01")))
    hi = int(idx.searchsorted(pd.Timestamp("2022-12-31")))
    bl = bt(pr, mr, name=LIVE, lo=lo, hi=hi); bp = bt(pr, mr, name=PRE, lo=lo, hi=hi)
    print(f"  live:    net {bl['net']*100:>7.1f}%  Sharpe {bl['sharpe']:>6.2f}  "
          f"maxDD {bl['maxdd']*100:.0f}%  worstWk {bl['worstwk']*100:.1f}%  inmkt {bl['inmkt']*100:.0f}%")
    print(f"  rankavg: net {bp['net']*100:>7.1f}%  Sharpe {bp['sharpe']:>6.2f}  "
          f"maxDD {bp['maxdd']*100:.0f}%  worstWk {bp['worstwk']*100:.1f}%  inmkt {bp['inmkt']*100:.0f}%")
    bear_ok = bp["net"] >= bl["net"] - 0.02
    print(f"  => rankavg net {'>=' if bear_ok else '<'} live-2pp  [need >= live-2pp]  "
          f"(dNet {(bp['net']-bl['net'])*100:+.1f}pp)")

    # ---- (E) independent 2017-2020 window ----
    print("\n" + "-" * 86)
    print("(E) INDEPENDENT 2017-2020 window (used nowhere else; THIN cross-section):")
    pe, me = prep("2017-01-01", "2020-01-01")
    per = pe.reset_index(drop=True); mer = me.reset_index(drop=True)
    el = bt(per, mer, name=LIVE); ep = bt(per, mer, name=PRE)
    print(f"  window {pe.index[0].date()}..{pe.index[-1].date()}  {pe.shape[1]} coins  "
          f"({len(pe)}d)")
    print(f"  live:    net {el['net']*100:>7.1f}%  Sharpe {el['sharpe']:>6.2f}  n={el['n']}")
    print(f"  rankavg: net {ep['net']*100:>7.1f}%  Sharpe {ep['sharpe']:>6.2f}  n={ep['n']}")
    # also 7-phase here for robustness colour (thin, informational)
    E7 = seven_phase(per, mer, PRE, LIVE)
    indep_ok = ep["net"] >= el["net"]
    print(f"  7-phase edge here: mean {E7['mean']:+.2f}  positive {E7['pos']}/7 (informational)")
    print(f"  => rankavg net {'>=' if indep_ok else '<'} live  [need >=]  "
          f"(dNet {(ep['net']-el['net'])*100:+.1f}pp)")

    # ---- 2021-> cross-check (context) ----
    print("\n" + "-" * 86)
    print("(context) 2021-> OOS window cross-check + its 7-phase:")
    p21, m21 = prep("2021-01-01")
    c2 = int(len(p21) * SPLIT)
    pte2 = p21.iloc[c2:].reset_index(drop=True); mte2 = m21.iloc[c2:].reset_index(drop=True)
    X = seven_phase(pte2, mte2, PRE, LIVE)
    ol = bt(pte2, mte2, name=LIVE); op = bt(pte2, mte2, name=PRE)
    print(f"  OOS starts {p21.index[c2].date()}: live Sh {ol['sharpe']:.2f} net {ol['net']*100:.0f}%"
          f" | rankavg Sh {op['sharpe']:.2f} net {op['net']*100:.0f}%")
    print(f"  7-phase edge: mean {X['mean']:+.2f}  positive {X['pos']}/7")

    # ---- pre-registered verdict ----
    print("\n" + "=" * 86)
    cond = {
        "A 7-phase OOS  (mean>0 & >=5/7)": A["mean"] > 0 and A["pos"] >= 5,
        "B 7-phase FULL (mean>0 & >=5/7)": B["mean"] > 0 and B["pos"] >= 5,
        "C 5-slice WF   (>=4/5)":          wins >= 4,
        "D bear-2022    (>= live-2pp)":    bear_ok,
        "E 2017-2020    (>= live)":        indep_ok,
    }
    for k, v in cond.items():
        print(f"  [{'PASS' if v else 'FAIL'}]  {k}")
    adopt = all(cond.values())
    print("-" * 86)
    print(f"  PRE-REGISTERED VERDICT: {'ADOPT rankavg -> write candidate' if adopt else 'KILL P26 — live composition stands'}")
    print("=" * 86)


if __name__ == "__main__":
    main()
