"""P27 — Rebalance INTERVAL (holding period) re-validation on the LIVE long-only config.

The live engine rebalances every R=7 days (weekly). This interval was inherited and never
honestly re-validated in the SLOWER direction. The ONLY rebalance-frequency test on record is
P1, which went the FASTER way (daily rebalance, lookback 14d) and found it strictly worse
(Sharpe 0.45 vs 1.62). Nobody has tested SLOWER-than-weekly rebalancing.

Hypothesis (grounded in the momentum literature + this LOG): cross-sectional momentum is a
slow-decaying signal — the live signal itself averages 14/30/60d horizons, whose natural
holding period is multiple weeks, and classic momentum studies rebalance monthly. Weekly
rebalancing may be CHURNING: selling names on transient weekly rank-shuffle only to re-buy
them, and (P16) whipsawing in/out around the 100d-MA trend gate. A slower interval (14/21/28d)
could hold winners through weekly noise and smooth the book. AGAINST it: a slower interval
reacts later to trend breaks (the P16 lagging-exit worry, amplified), and P8 already showed the
edge is cost-robust so the turnover saving is not itself worth much. Honest OOS decides.

GENUINELY NEW vs the LOG: this is the rebalance-FREQUENCY lever in the slow direction. It is
NOT the daily-frame variant (P1, faster + different lookback), the hold-band buffer (P18, which
fixed R=7 and only widened the exit rank), top_k (P19), trend-MA (P17), skip (P5), gate
(P0/P3/P11), or vol-sizing (P2). R==7 reproduces the live behaviour exactly (sanity-checked).

Method (repo convention, mirrors P18/P19 + the P20/P21 phase killer):
  * Data: cached KuCoin daily, 2020-> multi-cycle panel (full 2022 bear in input).
  * Engine: EXACT live strict-top-K engine (multi-horizon 14/30/60d momentum, top-5, px>100d MA
    else cash, equal weight, 15bps/side). ONLY the rebalance interval R changes. Sharpe is
    annualised with sqrt(365/R) so intervals are comparable.
  * Selection: pick best R on the TRAIN half (first 60%) by Sharpe; report TEST.
  * BAR TO CLEAR: a candidate R must (a) beat LIVE (R=7) in >=4/5 walk-forward slices AND beat
    LIVE on the pure OOS test half (Sharpe, giving up <=30% of return), AND (b) survive the
    P20/P21 phase-robustness killer — its Sharpe edge vs live, averaged over all start offsets,
    must be positive at a majority of phases (not just at the default offset-0 grid).
  * Mechanism: bear-located 2022 slice + turnover.

Caveat: one multi-cycle panel, one 2022 bear. Slower R => FEWER rebalances per fold (R=28 ~ 20
per fold) => noisier per-fold Sharpe; weigh the phase-averaged full-window result most.

    /Users/jamesvaness/hermes-trading/.venv/bin/python scripts/p27_rebalance_interval.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import K, LBS, COST, SPLIT, MA_DAYS, multi_score  # noqa: E402
from p18_buffer import idx_range  # noqa: E402

START = "2020-01-01"
RS = [7, 10, 14, 21, 28]  # 7 == LIVE (weekly)
LIVE_R = 7
WARM = max(LBS)


def bt_r(panel, ma, R, cost=COST, top_k=K, trend=True, offset=0):
    """LIVE strict-top-K engine at rebalance interval R, starting at WARM+offset.
    Enter/hold EXACTLY the top-K momentum names above their MA (no buffer); equal weight
    1/K; cash for names that fail the MA. R==7, offset==0 reproduces the live grid."""
    rets, turns, deployed = [], [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = WARM + offset
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= top_k:
            order = list(sc.sort_values(ascending=False).index)
            row = panel.iloc[i]
            marow = ma.iloc[i]
            for s in order[:top_k]:
                if (not trend) or (row[s] > marow[s]):
                    w[s] = 1 / top_k
        fwd = (panel.iloc[i + R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0)
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
    # turnover reported per-YEAR-equivalent so intervals compare (turn/rebal * rebals/yr)
    return dict(net=eq[-1] - 1, sharpe=rets.mean() / rets.std() * np.sqrt(365 / R),
                maxdd=float(np.max((pk - eq) / pk)), worstwk=float(rets.min()),
                turnover=float(np.mean(turns) * 365 / R), inmkt=float(np.mean(deployed)),
                n=len(rets))


def bt_window_r(panel, ma, R, lo, hi, cost=COST, top_k=K, offset=0):
    """Same engine; ACCUMULATE only rebalances whose index is in [lo,hi) (bear-located)."""
    rets, deployed = [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = WARM + offset
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= top_k:
            order = list(sc.sort_values(ascending=False).index)
            row = panel.iloc[i]
            marow = ma.iloc[i]
            for s in order[:top_k]:
                if row[s] > marow[s]:
                    w[s] = 1 / top_k
        fwd = (panel.iloc[i + R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0)
        if lo <= i < hi:
            rets.append((w * fwd).sum() - (w - prev).abs().sum() * cost)
            deployed.append(float(w.sum()))
        prev = w
        i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return dict(net=0, sharpe=0, maxdd=0, worstwk=0, inmkt=0, n=len(rets))
    eq = np.cumprod(1 + rets)
    pk = np.maximum.accumulate(eq)
    return dict(net=eq[-1] - 1, sharpe=rets.mean() / rets.std() * np.sqrt(365 / R),
                maxdd=float(np.max((pk - eq) / pk)), worstwk=float(rets.min()),
                inmkt=float(np.mean(deployed)), n=len(rets))


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

    print("=" * 92)
    print("P27 — rebalance INTERVAL (holding period) re-validation on the LIVE long-only config")
    print("=" * 92)
    print(f"panel {idx[0].date()}..{idx[-1].date()}  (~{yrs:.1f}y, {len(panel)} days, "
          f"{panel.shape[1]} coins)  cost {COST*1e4:.0f}bps/side  LBS={LBS}  K={K}")
    print(f"TRAIN first {SPLIT*100:.0f}% (-> {idx[cut].date()}), TEST remainder. "
          f"LIVE = R={LIVE_R}d (weekly). turnover = per-year-equiv.\n")

    folds = np.array_split(np.arange(len(panel)), 5)

    def wf_sharpes(R):
        out = []
        for f in folds:
            seg = panel.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            mas = ma_full.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            out.append(bt_r(seg, mas, R)["sharpe"])
        return [float(x) for x in out]

    live_wf = wf_sharpes(LIVE_R)

    # ---------------- (1) full + OOS + walk-forward vs LIVE ----------------
    print("-" * 92)
    print("(1) full-window, OOS(test half), 5-slice walk-forward (offset-0 grid; each vs LIVE):")
    print(f"{'R(d)':<6}{'rebals':>7}{'fullNet%':>10}{'fullSh':>8}{'fullDD':>8}{'worstWk':>9}"
          f"{'turn/yr':>8}{'oosNet%':>9}{'oosSh':>7}{'WF Sharpes':>28}{'>live':>7}")
    rows = {}
    for R in RS:
        full = bt_r(panel_r, ma_r, R)
        oos = bt_r(panel_r.iloc[cut:].reset_index(drop=True),
                   ma_r.iloc[cut:].reset_index(drop=True), R)
        wf = wf_sharpes(R)
        beats = sum(1 for a, b in zip(wf, live_wf) if a > b)
        rows[R] = dict(full=full, oos=oos, wf=wf, beats=beats)
        wf_str = "[" + ",".join(f"{x:+.2f}" for x in wf) + "]"
        tag = "LIVE" if R == LIVE_R else f"{beats}/5"
        print(f"{R:<6}{full['n']:>7}{full['net']*100:>9.0f}%{full['sharpe']:>8.2f}"
              f"{full['maxdd']*100:>7.0f}%{full['worstwk']*100:>8.1f}%{full['turnover']:>8.1f}"
              f"{oos['net']*100:>8.0f}%{oos['sharpe']:>7.2f}{wf_str:>28}{tag:>7}")

    print(f"\n  sanity: R=7 full Sharpe {rows[7]['full']['sharpe']:.3f} "
          f"net {rows[7]['full']['net']*100:.0f}% DD {rows[7]['full']['maxdd']*100:.0f}% "
          f"(should match the live K5/100d weekly engine on 2020->: Sharpe ~0.90, maxDD ~80%)")

    # ---------------- (2) honest train->test ----------------
    print("\n" + "-" * 92)
    print("(2) honest train->test: pick best R on TRAIN (Sharpe), report TEST:")
    ptr = panel_r.iloc[:cut].reset_index(drop=True)
    mtr = ma_r.iloc[:cut].reset_index(drop=True)
    train = {R: bt_r(ptr, mtr, R) for R in RS}
    best = max(RS, key=lambda R: train[R]["sharpe"])
    te = rows[best]["oos"]
    live_te = rows[LIVE_R]["oos"]
    print("  TRAIN Sharpes: " + "  ".join(f"R{R}={train[R]['sharpe']:.2f}" for R in RS))
    print(f"  TRAIN-best: R={best}  (train Sharpe {train[best]['sharpe']:.2f})")
    print(f"  -> TEST R={best}: net {te['net']*100:+.0f}%  Sharpe {te['sharpe']:.2f}  "
          f"maxDD {te['maxdd']*100:.0f}%")
    print(f"  -> TEST LIVE(7): net {live_te['net']*100:+.0f}%  "
          f"Sharpe {live_te['sharpe']:.2f}  maxDD {live_te['maxdd']*100:.0f}%")

    # ---------------- (3) PHASE robustness (the P20/P21 killer) ----------------
    # Average each R's full-window Sharpe over ALL start offsets in [0, R) and compare the
    # per-phase dSharpe vs LIVE. offset-0 (all prior BTs) is just one draw.
    print("\n" + "-" * 92)
    print("(3) PHASE robustness — full-window Sharpe averaged over start offsets (P20/P21 killer):")
    NPHASE = 7  # compare a common 0..6 offset band across all R for a fair head-to-head
    live_ph = np.array([bt_r(panel_r, ma_r, LIVE_R, offset=o)["sharpe"] for o in range(NPHASE)])
    print(f"  LIVE(7) phase Sharpes (offset 0..{NPHASE-1}): "
          f"mean {live_ph.mean():.2f} std {live_ph.std():.2f}")
    print(f"  {'R(d)':<6}{'phaseMeanSh':>12}{'phaseStd':>10}{'dVsLive_mean':>14}"
          f"{'dStd':>7}{'d>0 phases':>12}")
    phase_rob = {}
    for R in RS:
        ph = np.array([bt_r(panel_r, ma_r, R, offset=o)["sharpe"] for o in range(NPHASE)])
        d = ph - live_ph
        pos = int((d > 0).sum())
        phase_rob[R] = dict(mean=ph.mean(), dmean=d.mean(), pos=pos)
        tag = "  LIVE" if R == LIVE_R else ""
        print(f"  {R:<6}{ph.mean():>12.2f}{ph.std():>10.2f}{d.mean():>+14.2f}"
              f"{d.std():>7.2f}{pos:>9}/{NPHASE}{tag}")

    # ---------------- (4) bear-located mechanism ----------------
    print("\n" + "-" * 92)
    print("(4) mechanism — bear-LOCATED (rebalances inside 2022) and bull contrast:")
    windows = [("BEAR 2022", "2022-01-01", "2022-12-31"),
               ("BULL 2023->24", "2023-01-01", "2024-12-31")]
    for wname, a, b in windows:
        lo, hi = idx_range(idx, a, b)
        print(f"  [{wname}] {a}..{b}")
        print(f"    {'R(d)':<6}{'rebals':>7}{'net%':>9}{'Sharpe':>8}"
              f"{'maxDD':>7}{'worstWk':>9}{'inmkt':>7}")
        for R in RS:
            r = bt_window_r(panel_r, ma_r, R, lo, hi)
            print(f"    {R:<6}{r['n']:>7}{r['net']*100:>8.1f}%{r['sharpe']:>8.2f}"
                  f"{r['maxdd']*100:>6.0f}%{r['worstwk']*100:>8.1f}%{r['inmkt']*100:>6.0f}%")

    # ---------------- verdict scaffold ----------------
    print("\n" + "=" * 92)
    live_oos = rows[LIVE_R]["oos"]
    wf_pass = [R for R in RS if R != LIVE_R and rows[R]["beats"] >= 4]
    oos_beat = [R for R in wf_pass
                if rows[R]["oos"]["sharpe"] > live_oos["sharpe"]
                and rows[R]["oos"]["net"] > 0.7 * live_oos["net"]]
    phase_beat = [R for R in oos_beat
                  if phase_rob[R]["dmean"] > 0 and phase_rob[R]["pos"] >= (NPHASE // 2 + 1)]
    print(f"clears >=4/5 WF-Sharpe bar: {wf_pass or 'none'}")
    print(f"...and also beats LIVE on OOS test half: {oos_beat or 'none'}")
    print(f"...and ALSO survives the phase killer (dSharpe>0, majority of phases): "
          f"{phase_beat or 'none'}")
    if phase_beat:
        print(f"GENUINE candidate R={phase_beat}: beats live on TRAIN-selection, OOS, WF, AND "
              f"phase-robustly. Consider a candidate config.")
    else:
        print("No rebalance-interval change survives all three honest gates. Live weekly (R=7) "
              "stands. NO config change.")


if __name__ == "__main__":
    main()
