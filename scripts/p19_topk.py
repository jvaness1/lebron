"""P19 — Holding count (top_k) re-validation on the LIVE 36-coin long-only config.

The live engine holds the top_k=5 momentum names (above their 100d MA, else cash), equal
weight 1/K, weekly. top_k=5 was INHERITED from the original 24-coin LONG-SHORT era and never
re-validated after the config became long-only / multi-horizon / trend-filtered / 36-coin (P4).
The only K test on record (improve_sweep, 2026-06-11) was single-window, long-short,
breadth-gated, 24 coins -- and it HINTED K=8 gave higher Sharpe (1.74 vs 1.62) + lower DD
(13.6% vs 19.2%): a possible consistency win that was never honestly walk-forwarded.

Hypothesis: with the EXPANDED 36-coin universe, a larger K diversifies idiosyncratic risk and
could cut drawdown / raise Sharpe (the stated consistency goal) at some return cost. Against it:
each marginal name past #5 is a weaker-ranked momentum pick, so a wider K DILUTES selection
(the same mechanism that killed P18's wider hold-band). Honest OOS decides.

GENUINELY NEW vs the LOG: this is the holdings-COUNT lever. It is NOT the selection/exit BAND
(P18 — that fixed K=5 entry and only widened the KEEP band), nor trend-MA (P17), gate
(P0/P3/P11), skip (P5), or vol-sizing (P2). It changes how many equal-weight names we hold.

Method (repo convention, mirrors P18):
  * Data: cached KuCoin daily, 2020-> multi-cycle panel (full 2022 bear in input).
  * Engine: the EXACT live engine via P18's validated bt_buf with n_hold==top_k==K (strict
    top-K, no buffer). K=5 must reproduce the documented live numbers (sanity-checked).
  * Honest selection: pick best K on the TRAIN half (first 60%) by Sharpe; report TEST.
  * BAR TO CLEAR: a candidate K must beat LIVE (K=5) in >=4/5 walk-forward slices AND beat
    LIVE on the pure OOS test half (Sharpe, and give up <=30% of return).
  * Mechanism: bear-located 2022 slice + bull contrast — does a wider K tame the bear DD
    (diversification) or just dilute the bull return?

Caveat: one multi-cycle panel, one 2022 bear (~52 weekly rebals), survivors-only universe.
A point estimate of a concentration/diversification tradeoff, not a multi-cycle law.

    /Users/jamesvaness/hermes-trading/.venv/bin/python scripts/p19_topk.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import R, LBS, COST, SPLIT, MA_DAYS  # noqa: E402
from p18_buffer import bt_buf, bt_window, idx_range  # noqa: E402

START = "2020-01-01"
KS = [3, 4, 5, 6, 7, 8, 10]  # 5 == LIVE
LIVE_K = 5


def bt_k(panel, ma, k, **kw):
    """Strict top-K live engine: bt_buf with n_hold==top_k==k (no buffer band)."""
    return bt_buf(panel, ma, n_hold=k, top_k=k, **kw)


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

    print("=" * 88)
    print("P19 — holding count (top_k) re-validation on the LIVE long-only config")
    print("=" * 88)
    print(f"panel {idx[0].date()}..{idx[-1].date()}  (~{yrs:.1f}y, {len(panel)} days, "
          f"{panel.shape[1]} coins)  cost {COST*1e4:.0f}bps/side  R={R}d  LBS={LBS}")
    print(f"TRAIN first {SPLIT*100:.0f}% (-> {idx[cut].date()}), TEST remainder. "
          f"LIVE = top_k={LIVE_K}.\n")

    folds = np.array_split(np.arange(len(panel)), 5)

    def wf_sharpes(k):
        out = []
        for f in folds:
            seg = panel.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            mas = ma_full.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            out.append(bt_k(seg, mas, k)["sharpe"])
        return [float(x) for x in out]

    live_wf = wf_sharpes(LIVE_K)

    # ---------------- (1) full + OOS + walk-forward vs LIVE ----------------
    print("-" * 88)
    print("(1) full-window, OOS(test half), 5-slice walk-forward (each vs LIVE K=5):")
    print(f"{'top_k':<7}{'fullNet%':>10}{'fullSh':>8}{'fullDD':>8}{'worstWk':>9}"
          f"{'oosNet%':>10}{'oosSh':>8}{'oosDD':>7}{'WF Sharpes':>30}{'>live':>7}")
    rows = {}
    for k in KS:
        full = bt_k(panel_r, ma_r, k)
        oos = bt_k(panel_r.iloc[cut:].reset_index(drop=True),
                   ma_r.iloc[cut:].reset_index(drop=True), k)
        wf = wf_sharpes(k)
        beats = sum(1 for a, b in zip(wf, live_wf) if a > b)
        rows[k] = dict(full=full, oos=oos, wf=wf, beats=beats)
        wf_str = "[" + ",".join(f"{x:+.2f}" for x in wf) + "]"
        tag = "LIVE" if k == LIVE_K else f"{beats}/5"
        print(f"{k:<7}{full['net']*100:>9.0f}%{full['sharpe']:>8.2f}"
              f"{full['maxdd']*100:>7.0f}%{full['worstwk']*100:>8.1f}%"
              f"{oos['net']*100:>9.0f}%{oos['sharpe']:>8.2f}{oos['maxdd']*100:>6.0f}%"
              f"{wf_str:>30}{tag:>7}")

    print(f"\n  sanity: top_k=5 full Sharpe {rows[5]['full']['sharpe']:.3f} "
          f"net {rows[5]['full']['net']*100:.0f}% DD {rows[5]['full']['maxdd']*100:.0f}% "
          f"(should match the documented live K5/100d engine CHARACTER on the 2020-> panel: "
          f"Sharpe ~0.90, maxDD ~80%; P13's +3349% was the longer 2017-> panel)")

    # ---------------- (2) honest train->test ----------------
    print("\n" + "-" * 88)
    print("(2) honest train->test: pick best top_k on TRAIN (Sharpe), report TEST:")
    ptr = panel_r.iloc[:cut].reset_index(drop=True)
    mtr = ma_r.iloc[:cut].reset_index(drop=True)
    train = {k: bt_k(ptr, mtr, k) for k in KS}
    best = max(KS, key=lambda k: train[k]["sharpe"])
    te = rows[best]["oos"]
    live_te = rows[LIVE_K]["oos"]
    print("  TRAIN Sharpes: " + "  ".join(f"K{k}={train[k]['sharpe']:.2f}" for k in KS))
    print(f"  TRAIN-best: top_k={best}  (train Sharpe {train[best]['sharpe']:.2f})")
    print(f"  -> TEST top_k={best}: net {te['net']*100:+.0f}%  Sharpe {te['sharpe']:.2f}  "
          f"maxDD {te['maxdd']*100:.0f}%")
    print(f"  -> TEST LIVE (5)   : net {live_te['net']*100:+.0f}%  "
          f"Sharpe {live_te['sharpe']:.2f}  maxDD {live_te['maxdd']*100:.0f}%")

    # ---------------- (3) bear-located mechanism check ----------------
    print("\n" + "-" * 88)
    print("(3) mechanism — bear-LOCATED (rebalances inside 2022) and bull contrast:")
    windows = [("BEAR 2022", "2022-01-01", "2022-12-31"),
               ("BULL 2023->24", "2023-01-01", "2024-12-31")]
    show = [3, 5, 8, 10]
    for wname, a, b in windows:
        lo, hi = idx_range(idx, a, b)
        print(f"  [{wname}] {a}..{b}")
        print(f"    {'top_k':<7}{'rebals':>7}{'net%':>9}{'Sharpe':>8}"
              f"{'maxDD':>7}{'worstWk':>9}{'inmkt':>7}")
        for k in show:
            r = bt_window(panel_r, ma_r, n_hold=k, lo=lo, hi=hi, top_k=k)
            print(f"    {k:<7}{r['n']:>7}{r['net']*100:>8.1f}%{r['sharpe']:>8.2f}"
                  f"{r['maxdd']*100:>6.0f}%{r['worstwk']*100:>8.1f}%{r['inmkt']*100:>6.0f}%")

    # ---------------- verdict scaffold ----------------
    print("\n" + "=" * 88)
    live_oos = rows[LIVE_K]["oos"]
    wf_pass = [k for k in KS if k != LIVE_K and rows[k]["beats"] >= 4]
    real = [k for k in wf_pass
            if rows[k]["oos"]["sharpe"] > live_oos["sharpe"]
            and rows[k]["oos"]["net"] > 0.7 * live_oos["net"]]
    print(f"clears literal >=4/5 WF-Sharpe bar: {wf_pass or 'none'}")
    if real:
        print(f"ALSO beats LIVE on the OOS test half: top_k={real} — genuine candidate; "
              f"train->test independently picks K={best}.")
    else:
        print("...but NONE of those also beat LIVE on the OOS test half "
              "(Sharpe + within 30% of return). No deployable top_k change.")


if __name__ == "__main__":
    main()
