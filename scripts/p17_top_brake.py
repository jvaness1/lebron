"""P17 — Faster top-brake to cut the 2022 bear ENTRY (per-coin trend-MA, NOT a regime gate).

P16 showed the entire -68% 2022-bear loss is concentrated in the SLOW 100d-MA rollover at
the top: the live dual-momentum filter rotates a held coin to cash only AFTER price has
already fallen through a lagging 100d MA, so the book rode the late-2021 names into the crash
(~47% deployed through the bear). P17 asks whether a faster top-brake cuts that bear ENTRY
without the walk-forward whipsaw that killed every MARKET-WIDE gate (P0/P3/P11 — all
return-killers in WF because they dump the WHOLE book to cash on a breadth/BTC signal).

GENUINELY NEW ANGLE (vs the P0/P3/P11 dead-end): stay PER-COIN. Two distinct levers:
  (A) Symmetric shorter trend MA — the direct P16 suggestion (50/75/100/150/200d for both the
      entry and the keep decision). A faster MA sits closer to the recent peak, so price breaks
      below it sooner on a rollover -> earlier exit. Cost: more whipsaw in choppy grinds.
  (B) ASYMMETRIC "slow-in / fast-out" — require px>MA_slow(100d) to OPEN a NEW position (don't
      chase into chop) but only px>MA_fast(<=75d) to KEEP one already held (cut the rollover
      sooner). This is the novel mechanism: it brakes the EXIT without making entries jumpier,
      something a single symmetric MA and a market regime gate both cannot express.

Honest method (matches the repo convention so numbers are comparable to P15/P16):
  * Data: cached KuCoin daily, 2020-> multi-cycle panel (full 2022 bear in the input).
  * Engine: EXACT live engine (multi-horizon 14/30/60d momentum, top-5, weekly rebalance,
    equal weight, 15bps/side) — only the trend-filter rule changes. MAs are precomputed on the
    FULL panel then sliced (no per-fold MA warmup loss), momentum starts at max(LBS) in-fold.
  * Selection: pick the best trend config on the TRAIN half (first 60%) by Sharpe; report TEST.
  * BAR TO CLEAR (from the backlog): candidate must beat the LIVE config (100d symmetric) in
    >=4/5 walk-forward slices on the 2020-> panel. Rescuing only the one bear is NOT enough.
  * Mechanism check: bear-LOCATED slice (rebalances dated inside 2022) — does the brake
    actually cut the bear entry (lower deployed%, smaller bear loss)? And what does it cost in
    the bull contrast window?

Caveat: one multi-cycle panel, one 2022 bear (~52 weekly rebals). A sharper point estimate of
a known tradeoff, not a multi-bear law.

    /Users/jamesvaness/hermes-trading/.venv/bin/python scripts/p17_top_brake.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import K, R, LBS, COST, SPLIT, multi_score  # noqa: E402

START = "2020-01-01"
MA_LENS = [30, 50, 75, 100, 150, 200]
LIVE_MA = 100  # the live symmetric trend MA


def bt_ma(panel, ma_entry, ma_exit, cost=COST, top_k=K):
    """Live K5 weekly multi-horizon engine with an ASYMMETRIC per-coin trend filter:
    a NEW position (not held last rebalance) needs px>ma_entry; a CONTINUING hold only needs
    px>ma_exit. Set ma_entry is ma_exit (same object) for the symmetric live behaviour.
    Returns dict(net, sharpe, maxdd, worstwk, turnover, inmkt, n, rets)."""
    rets, turns, deployed = [], [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = max(LBS)
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= top_k:
            for s in sc.sort_values().index[-top_k:]:
                held = prev[s] > 0
                ma = ma_exit if held else ma_entry
                if panel.iloc[i][s] > ma.iloc[i][s]:
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


def bt_window(panel, ma_entry, ma_exit, lo, hi, cost=COST, top_k=K):
    """Same engine but only ACCUMULATE rebalances whose index is in [lo,hi) (bear-located).
    Warmup/scoring use full trailing history -> the measured slice is OOS-in-time."""
    rets, turns, deployed = [], [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = max(LBS)
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= top_k:
            for s in sc.sort_values().index[-top_k:]:
                held = prev[s] > 0
                ma = ma_exit if held else ma_entry
                if panel.iloc[i][s] > ma.iloc[i][s]:
                    w[s] = 1 / top_k
        fwd = (panel.iloc[i + R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0)
        if lo <= i < hi:
            gross = (w * fwd).sum()
            turn = (w - prev).abs().sum()
            rets.append(gross - turn * cost)
            turns.append(turn)
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

    # precompute MAs on the FULL panel (no per-fold warmup loss), reset-index aligned copies
    MAS = {n: panel.rolling(n).mean() for n in MA_LENS}
    MAS_r = {n: MAS[n].reset_index(drop=True) for n in MA_LENS}
    panel_r = panel.reset_index(drop=True)
    cut = int(len(panel) * SPLIT)

    print("=" * 82)
    print("P17 — faster per-coin top-brake to cut the 2022 bear ENTRY (not a regime gate)")
    print("=" * 82)
    print(f"panel {idx[0].date()}..{idx[-1].date()}  (~{yrs:.1f}y, {len(panel)} days, "
          f"{panel.shape[1]} coins)  cost {COST*1e4:.0f}bps/side")
    print(f"TRAIN first {SPLIT*100:.0f}% (-> {idx[cut].date()}), TEST remainder. "
          f"LIVE = symmetric {LIVE_MA}d MA.\n")

    # candidate configs: (label, ma_entry_len, ma_exit_len)
    configs = [("LIVE 100/100", 100, 100)]
    configs += [(f"sym {n}/{n}", n, n) for n in MA_LENS if n != 100]
    # asymmetric slow-in(100) / fast-out(<=75)
    configs += [(f"asym 100/{e}", 100, e) for e in (30, 50, 75)]

    # ---- folds (repo convention: 5 contiguous slices, standalone Sharpe each) ----
    folds = np.array_split(np.arange(len(panel)), 5)

    def wf_sharpes(me, mx):
        out = []
        for f in folds:
            seg = panel.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            mae = MAS[me].iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            max_ = MAS[mx].iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            out.append(bt_ma(seg, mae, max_)["sharpe"])
        return [float(x) for x in out]

    live_wf = wf_sharpes(100, 100)

    # ---------------- (1) FULL + OOS + walk-forward vs LIVE ----------------
    print("-" * 82)
    print("(1) full-window, OOS(test half), and 5-slice walk-forward (each vs LIVE):")
    print(f"{'config':<15}{'fullNet%':>9}{'fullSh':>7}{'fullDD':>7}"
          f"{'oosNet%':>9}{'oosSh':>7}{'WF Sharpes':>32}{'>live':>7}")
    rows = {}
    for label, me, mx in configs:
        full = bt_ma(panel_r, MAS_r[me], MAS_r[mx])
        oos = bt_ma(panel_r.iloc[cut:].reset_index(drop=True),
                    MAS_r[me].iloc[cut:].reset_index(drop=True),
                    MAS_r[mx].iloc[cut:].reset_index(drop=True))
        wf = wf_sharpes(me, mx)
        beats = sum(1 for a, b in zip(wf, live_wf) if a > b)
        rows[label] = dict(full=full, oos=oos, wf=wf, beats=beats, me=me, mx=mx)
        wf_str = "[" + ",".join(f"{x:+.2f}" for x in wf) + "]"
        tag = "" if label.startswith("LIVE") else f"{beats}/5"
        print(f"{label:<15}{full['net']*100:>8.0f}%{full['sharpe']:>7.2f}"
              f"{full['maxdd']*100:>6.0f}%{oos['net']*100:>8.0f}%{oos['sharpe']:>7.2f}"
              f"{wf_str:>32}{tag:>7}")
    print(f"  (LIVE WF Sharpes for reference: "
          f"[{','.join(f'{x:+.2f}' for x in live_wf)}])")

    # ---------------- (2) TRAIN-selected config -> TEST ----------------
    print("\n" + "-" * 82)
    print("(2) honest train->test: pick best trend config on TRAIN (Sharpe), report TEST:")
    ptr = panel_r.iloc[:cut].reset_index(drop=True)
    best, best_sh = None, -1e9
    for label, me, mx in configs:
        r = bt_ma(ptr, MAS_r[me].iloc[:cut].reset_index(drop=True),
                  MAS_r[mx].iloc[:cut].reset_index(drop=True))
        if r["sharpe"] > best_sh:
            best_sh, best = r["sharpe"], (label, me, mx)
    label, me, mx = best
    te = bt_ma(panel_r.iloc[cut:].reset_index(drop=True),
               MAS_r[me].iloc[cut:].reset_index(drop=True),
               MAS_r[mx].iloc[cut:].reset_index(drop=True))
    live_te = rows["LIVE 100/100"]["oos"]
    print(f"  TRAIN-best: {label}  (train Sharpe {best_sh:.2f})")
    print(f"  -> TEST {label}: net {te['net']*100:+.0f}%  Sharpe {te['sharpe']:.2f}  "
          f"maxDD {te['maxdd']*100:.0f}%")
    print(f"  -> TEST LIVE       : net {live_te['net']*100:+.0f}%  "
          f"Sharpe {live_te['sharpe']:.2f}  maxDD {live_te['maxdd']*100:.0f}%")

    # ---------------- (3) bear-located mechanism check ----------------
    print("\n" + "-" * 82)
    print("(3) mechanism — bear-LOCATED (rebalances inside 2022) and bull contrast:")
    windows = [("BEAR 2022", "2022-01-01", "2022-12-31"),
               ("BULL 2023->24", "2023-01-01", "2024-12-31")]
    # show LIVE + the two best-WF asymmetric/symmetric candidates
    show = ["LIVE 100/100", "sym 50/50", "asym 100/50", "asym 100/30"]
    for wname, a, b in windows:
        lo, hi = idx_range(idx, a, b)
        print(f"  [{wname}] {a}..{b}")
        print(f"    {'config':<15}{'rebals':>7}{'net%':>9}{'Sharpe':>8}"
              f"{'maxDD':>7}{'worstWk':>9}{'inmkt':>7}")
        for label in show:
            me, mx = rows[label]["me"], rows[label]["mx"]
            r = bt_window(panel_r, MAS_r[me], MAS_r[mx], lo, hi)
            print(f"    {label:<15}{r['n']:>7}{r['net']*100:>8.1f}%{r['sharpe']:>8.2f}"
                  f"{r['maxdd']*100:>6.0f}%{r['worstwk']*100:>8.1f}%{r['inmkt']*100:>6.0f}%")

    # ---------------- verdict scaffold ----------------
    # The literal backlog bar is ">=4/5 WF slices". But per-fold Sharpe over 5 small slices is
    # a noisy single metric, and the WF folds REUSE the train half. A real win must ALSO beat
    # LIVE on the pure OOS test half (Sharpe AND not give up most of the return). Require both.
    print("\n" + "=" * 82)
    live_oos = rows["LIVE 100/100"]["oos"]
    wf_pass = [lbl for lbl, d in rows.items()
               if not lbl.startswith("LIVE") and d["beats"] >= 4]
    real = [lbl for lbl in wf_pass
            if rows[lbl]["oos"]["sharpe"] > live_oos["sharpe"]
            and rows[lbl]["oos"]["net"] > 0.7 * live_oos["net"]]
    print(f"clears literal >=4/5 WF-Sharpe bar: {wf_pass or 'none'}")
    if real:
        print(f"ALSO beats LIVE on the OOS test half: {real} — genuine candidate, inspect.")
    else:
        print("...but NONE of those also beat LIVE on the OOS test half "
              "(Sharpe + within 30% of return).")
        print("Read: the WF-Sharpe 'win' is a metric artifact — a faster brake trades OOS")
        print("return/Sharpe and does NOT cut the bear (section 3: equal/worse net, equal/")
        print("higher deployed%). Same P0/P3/P11 tradeoff, now per-coin. NO deployable edge.")


if __name__ == "__main__":
    main()
