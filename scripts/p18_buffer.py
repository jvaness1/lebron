"""P18 — Rank-buffer (hysteresis) rebalancing for the LIVE long-only config.

The live engine rebalances to EXACTLY the top-5 momentum names (above their 100d MA) every
week. A name ranked #6 one week is sold even if it is barely behind #5 — classic momentum
churn. The momentum "buffering" technique (Novy-Marx & Velikov 2016; AQR) adds HYSTERESIS:
KEEP a held name as long as it stays inside a wider band (top-N_hold, e.g. top-8/10), only
ADD a new name when it breaks into the strict top-K=5. Winners are let run a little; marginal
rank-shuffle round-trips are avoided.

GENUINELY NEW vs the LOG: this is neither a trend-MA lever (P17), a skip-period (P5), a
regime gate (P0/P3/P11), nor vol-sizing (P2). It changes only the SELECTION/exit rule:
entry threshold = top-K (unchanged), exit threshold = top-N_hold (wider). N_hold==K reduces
EXACTLY to the live behaviour (sanity-checked in the output).

Why it might help: P8 found turnover already low (~0.42x/wk) and the edge cost-robust, so the
COST saving is modest — the real hypothesis is that cutting marginal-rank whipsaw improves
NET return / Sharpe by holding winners through transient rank dips. Why it might NOT: a wider
hold band keeps deteriorating names one extra week (a soft version of the lagging-exit problem
P16 flagged in the 2022 bear), which can ADD downside. Honest OOS decides.

Method (repo convention, comparable to P15/P17):
  * Data: cached KuCoin daily, 2020-> multi-cycle panel (full 2022 bear in input).
  * Engine: EXACT live engine (multi-horizon 14/30/60d momentum, top-5, px>100d MA else cash,
    weekly rebalance, equal weight, 15bps/side) — only the exit/hold band changes.
  * Selection: pick best N_hold on the TRAIN half (first 60%) by Sharpe; report TEST.
  * BAR TO CLEAR: candidate must beat LIVE (N_hold=5) in >=4/5 walk-forward slices AND also
    beat LIVE on the pure OOS test half (Sharpe, and not give up >30% of return).
  * Mechanism: report turnover (should fall with the band) and the bear-located 2022 slice
    (does the wider band make the bear worse, as the lagging-exit worry predicts?).

Caveat: one multi-cycle panel, one 2022 bear (~52 weekly rebals). A point estimate of a known
tradeoff, not a multi-bear law.

    /Users/jamesvaness/hermes-trading/.venv/bin/python scripts/p18_buffer.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import K, R, LBS, COST, SPLIT, MA_DAYS, multi_score  # noqa: E402

START = "2020-01-01"
N_HOLDS = [5, 6, 7, 8, 10, 12]  # 5 == LIVE
LIVE_NHOLD = 5


def bt_buf(panel, ma, n_hold, cost=COST, top_k=K, trend=True):
    """Live K5 weekly multi-horizon engine with a rank BUFFER:
      - ENTER a new name only if it is in the strict top-K and above its MA.
      - KEEP a held name as long as it is still in the top-N_HOLD and above its MA.
      - Cap holdings at K (keep the best-ranked); equal weight 1/K; cash for empty slots.
    n_hold == top_k reproduces the live behaviour exactly. Returns the standard dict."""
    rets, turns, deployed = [], [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = max(LBS)
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= top_k:
            order = list(sc.sort_values(ascending=False).index)  # best score first
            rank = {s: r for r, s in enumerate(order)}           # 0-based rank
            row = panel.iloc[i]
            marow = ma.iloc[i]

            def ok(s):
                return (not trend) or (row[s] > marow[s])

            chosen = []
            # keep held names still inside the buffer band and above MA
            held = [s for s in order if prev.get(s, 0) > 0]
            for s in sorted(held, key=lambda x: rank[x]):
                if rank[s] < n_hold and ok(s):
                    chosen.append(s)
            chosen = chosen[:top_k]
            # fill remaining slots from the strict top-K newcomers (above MA)
            for s in order[:top_k]:
                if len(chosen) >= top_k:
                    break
                if s not in chosen and ok(s):
                    chosen.append(s)
            for s in chosen:
                w[s] = 1 / top_k
        fwd = (panel.iloc[i + R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0)
        gross = (w * fwd).sum()
        turn = (w - prev).abs().sum()
        rets.append(gross - turn * cost)
        turns.append(turn)
        deployed.append(float(w.sum()))
        prev = w
        i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return dict(net=0, sharpe=0, maxdd=0, worstwk=0, turnover=0, inmkt=0,
                    n=len(rets), rets=rets)
    eq = np.cumprod(1 + rets)
    pk = np.maximum.accumulate(eq)
    return dict(net=eq[-1] - 1, sharpe=rets.mean() / rets.std() * np.sqrt(365 / R),
                maxdd=float(np.max((pk - eq) / pk)), worstwk=float(rets.min()),
                turnover=float(np.mean(turns)), inmkt=float(np.mean(deployed)),
                n=len(rets), rets=rets)


def bt_window(panel, ma, n_hold, lo, hi, cost=COST, top_k=K):
    """Same engine, but only ACCUMULATE rebalances whose index is in [lo,hi) (bear-located).
    Warmup/scoring use full trailing history -> the measured slice is OOS-in-time."""
    rets, deployed = [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = max(LBS)
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= top_k:
            order = list(sc.sort_values(ascending=False).index)
            rank = {s: r for r, s in enumerate(order)}
            row = panel.iloc[i]
            marow = ma.iloc[i]
            chosen = []
            held = [s for s in order if prev.get(s, 0) > 0]
            for s in sorted(held, key=lambda x: rank[x]):
                if rank[s] < n_hold and row[s] > marow[s]:
                    chosen.append(s)
            chosen = chosen[:top_k]
            for s in order[:top_k]:
                if len(chosen) >= top_k:
                    break
                if s not in chosen and row[s] > marow[s]:
                    chosen.append(s)
            for s in chosen:
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

    print("=" * 84)
    print("P18 — rank-buffer (hysteresis) rebalancing for the LIVE long-only config")
    print("=" * 84)
    print(f"panel {idx[0].date()}..{idx[-1].date()}  (~{yrs:.1f}y, {len(panel)} days, "
          f"{panel.shape[1]} coins)  cost {COST*1e4:.0f}bps/side")
    print(f"TRAIN first {SPLIT*100:.0f}% (-> {idx[cut].date()}), TEST remainder. "
          f"LIVE = N_hold={LIVE_NHOLD} (=top_k, no buffer).\n")

    # 5 contiguous walk-forward slices (repo convention)
    folds = np.array_split(np.arange(len(panel)), 5)

    def wf_sharpes(nh):
        out = []
        for f in folds:
            seg = panel.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            mas = ma_full.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            out.append(bt_buf(seg, mas, nh)["sharpe"])
        return [float(x) for x in out]

    live_wf = wf_sharpes(LIVE_NHOLD)

    # ---------------- (1) full + OOS + walk-forward vs LIVE ----------------
    print("-" * 84)
    print("(1) full-window, OOS(test half), 5-slice walk-forward, turnover (each vs LIVE):")
    print(f"{'N_hold':<8}{'fullNet%':>9}{'fullSh':>7}{'fullDD':>7}{'turn':>6}"
          f"{'oosNet%':>9}{'oosSh':>7}{'WF Sharpes':>30}{'>live':>7}")
    rows = {}
    for nh in N_HOLDS:
        full = bt_buf(panel_r, ma_r, nh)
        oos = bt_buf(panel_r.iloc[cut:].reset_index(drop=True),
                     ma_r.iloc[cut:].reset_index(drop=True), nh)
        wf = wf_sharpes(nh)
        beats = sum(1 for a, b in zip(wf, live_wf) if a > b)
        rows[nh] = dict(full=full, oos=oos, wf=wf, beats=beats)
        wf_str = "[" + ",".join(f"{x:+.2f}" for x in wf) + "]"
        tag = "LIVE" if nh == LIVE_NHOLD else f"{beats}/5"
        print(f"{nh:<8}{full['net']*100:>8.0f}%{full['sharpe']:>7.2f}"
              f"{full['maxdd']*100:>6.0f}%{full['turnover']:>6.2f}"
              f"{oos['net']*100:>8.0f}%{oos['sharpe']:>7.2f}{wf_str:>30}{tag:>7}")

    # sanity: N_hold=5 must equal the documented live behaviour (no buffer)
    print(f"\n  sanity: N_hold=5 full Sharpe {rows[5]['full']['sharpe']:.3f} "
          f"net {rows[5]['full']['net']*100:.0f}% turn {rows[5]['full']['turnover']:.3f} "
          f"(should match the live K5/100d engine)")

    # ---------------- (2) honest train->test ----------------
    print("\n" + "-" * 84)
    print("(2) honest train->test: pick best N_hold on TRAIN (Sharpe), report TEST:")
    ptr = panel_r.iloc[:cut].reset_index(drop=True)
    mtr = ma_r.iloc[:cut].reset_index(drop=True)
    best, best_sh = None, -1e9
    for nh in N_HOLDS:
        r = bt_buf(ptr, mtr, nh)
        if r["sharpe"] > best_sh:
            best_sh, best = r["sharpe"], nh
    te = rows[best]["oos"]
    live_te = rows[LIVE_NHOLD]["oos"]
    print(f"  TRAIN-best: N_hold={best}  (train Sharpe {best_sh:.2f})")
    print(f"  -> TEST N_hold={best}: net {te['net']*100:+.0f}%  Sharpe {te['sharpe']:.2f}  "
          f"maxDD {te['maxdd']*100:.0f}%  turn {te['turnover']:.2f}")
    print(f"  -> TEST LIVE (5)    : net {live_te['net']*100:+.0f}%  "
          f"Sharpe {live_te['sharpe']:.2f}  maxDD {live_te['maxdd']*100:.0f}%  "
          f"turn {live_te['turnover']:.2f}")

    # ---------------- (3) bear-located mechanism check ----------------
    print("\n" + "-" * 84)
    print("(3) mechanism — bear-LOCATED (rebalances inside 2022) and bull contrast:")
    windows = [("BEAR 2022", "2022-01-01", "2022-12-31"),
               ("BULL 2023->24", "2023-01-01", "2024-12-31")]
    show = [5, 8, 10, 12]
    for wname, a, b in windows:
        lo, hi = idx_range(idx, a, b)
        print(f"  [{wname}] {a}..{b}")
        print(f"    {'N_hold':<8}{'rebals':>7}{'net%':>9}{'Sharpe':>8}"
              f"{'maxDD':>7}{'worstWk':>9}{'inmkt':>7}")
        for nh in show:
            r = bt_window(panel_r, ma_r, nh, lo, hi)
            print(f"    {nh:<8}{r['n']:>7}{r['net']*100:>8.1f}%{r['sharpe']:>8.2f}"
                  f"{r['maxdd']*100:>6.0f}%{r['worstwk']*100:>8.1f}%{r['inmkt']*100:>6.0f}%")

    # ---------------- verdict scaffold ----------------
    print("\n" + "=" * 84)
    live_oos = rows[LIVE_NHOLD]["oos"]
    wf_pass = [nh for nh in N_HOLDS if nh != LIVE_NHOLD and rows[nh]["beats"] >= 4]
    real = [nh for nh in wf_pass
            if rows[nh]["oos"]["sharpe"] > live_oos["sharpe"]
            and rows[nh]["oos"]["net"] > 0.7 * live_oos["net"]]
    print(f"clears literal >=4/5 WF-Sharpe bar: {wf_pass or 'none'}")
    if real:
        print(f"ALSO beats LIVE on the OOS test half: N_hold={real} — genuine candidate.")
    else:
        print("...but NONE of those also beat LIVE on the OOS test half "
              "(Sharpe + within 30% of return). No deployable buffer edge.")


if __name__ == "__main__":
    main()
