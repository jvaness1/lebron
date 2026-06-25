"""P23 — Residual (beta-adjusted / idiosyncratic) momentum ranking.

MOTIVATION (grounded in the LOG, not a logged dead-end):
  - P12 (2026-06-19) diagnosed the core weakness: in a long-only crypto universe the
    momentum book is "dominated by common market beta" — no second sleeve diversifies it
    because everything co-moves with the market.
  - P16 (2026-06-21) showed the live signal rode HIGH-BETA names into the 2022 bear (a
    lagging top-detector, 47% deployed through the crash) — momentum's classic crash.
  - The documented "Residual Momentum" anomaly (Blitz, Huij & Martens 2011) targets EXACTLY
    this: rank by momentum AFTER stripping out market beta, so selection is on idiosyncratic
    strength, not "rode the market up + high beta". The paper's empirical claim is ~half the
    volatility and MUCH smaller momentum crashes (the bear tail) for a similar mean — i.e. a
    higher Sharpe and a better bear, the two things we most want here.

This is a genuinely NEW ranking lever vs the live RAW multi-horizon and vs P21 (which only
risk-normalised by total vol; residual momentum removes the systematic component, a different
operation). Everything else = EXACT live config (top-5, dual-momentum px>100d MA else cash,
weekly R=7, equal weight, 15bps/side).

Ranking variants (all reduce toward the live raw signal as beta->0 / residual==total):
  raw        : mean_lb( P[i]/P[i-lb]-1 )                              <- LIVE
  resid_ew   : mean_lb( sum of daily residuals over lb ), market = EW-universe daily return
  resid_t_ew : mean_lb( residual_sum(lb) / residual_std(lb) )  (Blitz IR/t-stat form), EW mkt
  resid_btc  : resid_ew but market proxy = BTC daily return

Honesty (mirrors P21): variants scored on a TRAIN half; winner reported on TEST + 5-slice WF;
located inside the 2022 bear (residual momentum's headline benefit); and — the gate that
KILLED P21 — re-checked across all 7 weekly rebalance phases (P20) so any edge can't be
grid-alignment luck. Beta-window + market-proxy are design choices picked on TRAIN /
sensitivity-mapped. Reuses the validated p15 engine constants and the 2020-> cache panel.

    python scripts/p23_residual_momentum.py [--start 2020-01-01]
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import K, R, SPLIT, MA_DAYS, LBS, COST  # noqa: E402

BETA_W = 60  # trailing daily-return window for the rolling market-beta regression


def _rolling_beta(ret_d, mkt, w):
    """Vectorised trailing-window beta of each coin's daily return on the market series.
    beta = cov(r,m)/var(m) over a trailing w-day window (trailing only -> no look-ahead)."""
    mean_r = ret_d.rolling(w).mean()
    mean_m = mkt.rolling(w).mean()
    mean_rm = ret_d.mul(mkt, axis=0).rolling(w).mean()
    var_m = mkt.rolling(w).var(ddof=0)
    cov = mean_rm.sub(mean_r.mul(mean_m, axis=0), axis=0)
    return cov.div(var_m.replace(0, np.nan), axis=0)


def make_score(panel, beta_w=BETA_W):
    """Precompute everything once, return score(i, mode) -> per-coin selection score at row i.
    Residuals are sums of daily (r - beta*m) over each horizon; beta from a trailing beta_w
    window. Two market proxies precomputed: EW-universe mean and BTC."""
    ret_d = panel.pct_change()
    mkt_ew = ret_d.mean(axis=1)
    mkt_btc = ret_d["BTC/USDT"] if "BTC/USDT" in ret_d.columns else mkt_ew

    def resid_daily(mkt):
        beta = _rolling_beta(ret_d, mkt, beta_w)
        return ret_d.sub(beta.mul(mkt, axis=0))  # r - beta*m, per day

    rd_ew = resid_daily(mkt_ew)
    rd_btc = resid_daily(mkt_btc)
    # rolling sums / stds of residuals per horizon, per proxy (precomputed)
    rsum_ew = {lb: rd_ew.rolling(lb).sum() for lb in LBS}
    rstd_ew = {lb: rd_ew.rolling(lb).std() for lb in LBS}
    rsum_btc = {lb: rd_btc.rolling(lb).sum() for lb in LBS}

    def horizon_ret(i, lb):
        return panel.iloc[i] / panel.iloc[i - lb] - 1

    def score(i, mode):
        if mode == "raw":
            return sum(horizon_ret(i, lb) for lb in LBS) / len(LBS)
        if mode == "resid_ew":
            return sum(rsum_ew[lb].iloc[i] for lb in LBS) / len(LBS)
        if mode == "resid_t_ew":
            parts = [rsum_ew[lb].iloc[i] / rstd_ew[lb].iloc[i].replace(0, np.nan)
                     for lb in LBS]
            return sum(parts) / len(parts)
        if mode == "resid_btc":
            return sum(rsum_btc[lb].iloc[i] for lb in LBS) / len(LBS)
        raise ValueError(mode)

    return score


def bt(panel, ma, mode="raw", cost=COST, trend=True, top_k=K, lo=None, hi=None,
       phase=0, beta_w=BETA_W):
    """Live long-only multi-horizon K5 weekly engine with a pluggable ranking `mode`.
    lo/hi -> only accumulate rebalances with lo<=i<hi (located window).
    `phase` shifts the weekly grid start (P20 timing-luck robustness)."""
    score = make_score(panel, beta_w=beta_w)
    rets, turns, deployed = [], [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = max(max(LBS), beta_w) + phase
    while i + R < len(panel):
        sc = score(i, mode).replace([np.inf, -np.inf], np.nan).dropna()
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


MODES = [("raw", "raw multi-horizon (LIVE)"),
         ("resid_ew", "residual mom (EW-mkt)"),
         ("resid_t_ew", "residual IR/t (EW-mkt)"),
         ("resid_btc", "residual mom (BTC-mkt)")]


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
    print("P23 — residual (beta-adjusted) momentum ranking vs live raw multi-horizon")
    print("=" * 80)
    print(f"window {idx[0].date()}..{idx[-1].date()} (~{yrs:.1f}y, {len(panel)}d), "
          f"{panel.shape[1]} coins, cost {COST*1e4:.0f}bps/side, beta_w {BETA_W}d")
    print(f"OOS test half starts {idx[cut].date()} (~{(idx[-1]-idx[cut]).days}d)\n")

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
    print(f"{'variant':<26}{'fullSh':>8}{'fullNet':>9}{'oosSh':>7}{'oosNet':>9}"
          f"{'oosDD':>7}{'WF+':>5}")
    wf_raw = wf("raw")
    rows = {}
    for mode, name in MODES:
        full = bt(pr, mr, mode=mode)
        oos = bt(pte, mte, mode=mode)
        w = wf_raw if mode == "raw" else wf(mode)
        pos = sum(1 for r in w if r["sharpe"] > 0)
        rows[mode] = dict(full=full, oos=oos, wf=w, wfpos=pos)
        print(f"{name:<26}{full['sharpe']:>8.2f}{full['net']*100:>8.0f}%"
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

    # ---- (3) bear-2022 located (residual momentum's HEADLINE claim: smaller crash) ----
    print("\n" + "-" * 80)
    print("(3) Located INSIDE the 2022 bear (residual momentum's headline = smaller crash):")
    lo = int(idx.searchsorted(pd.Timestamp("2022-01-01")))
    hi = int(idx.searchsorted(pd.Timestamp("2022-12-31")))
    print(f"{'variant':<26}{'net%':>9}{'Sharpe':>8}{'maxDD':>8}{'worstWk':>9}{'inmkt':>7}")
    for mode, name in MODES:
        r = bt(pr, mr, mode=mode, lo=lo, hi=hi)
        print(f"{name:<26}{r['net']*100:>8.1f}%{r['sharpe']:>8.2f}{r['maxdd']*100:>7.0f}%"
              f"{r['worstwk']*100:>8.1f}%{r['inmkt']*100:>6.0f}%")

    # ---- (4) PHASE robustness (P20) — the gate that killed P21 ----
    print("\n" + "-" * 80)
    print("(4) PHASE robustness (P20): OOS Sharpe raw vs each resid variant across 7 offsets:")
    for mode, name in MODES[1:]:
        edges = []
        for ph in range(7):
            rr = bt(pte, mte, mode="raw", phase=ph)
            ra = bt(pte, mte, mode=mode, phase=ph)
            edges.append(ra["sharpe"] - rr["sharpe"])
        edges = np.array(edges)
        print(f"  {name:<22} dSharpe(resid-raw) across phases: mean {edges.mean():+.2f}  "
              f"std {edges.std():.2f}  positive {int((edges>0).sum())}/7")

    # ---- (5) beta-window sensitivity (OOS) — is any edge specific to 60d? ----
    print("\n" + "-" * 80)
    print("(5) resid_ew beta-window sensitivity (OOS) — is the edge knife-edge in beta_w?:")
    print(f"  raw(live) OOS Sharpe {rows['raw']['oos']['sharpe']:.2f}")
    print(f"{'beta_w':>8}{'oosSh':>8}{'oosNet':>9}{'oosDD':>7}")
    for bw in (30, 45, 60, 90, 120):
        r = bt(pte, mte, mode="resid_ew", beta_w=bw)
        print(f"{bw:>7}d{r['sharpe']:>8.2f}{r['net']*100:>8.0f}%{r['maxdd']*100:>6.0f}%")

    # ---- (6) 2021-> window cross-check (OOS) ----
    print("\n" + "-" * 80)
    print("(6) 2021-> window cross-check (OOS test half):")
    p21win = panel[panel.index >= "2021-01-01"]
    m21 = ma[ma.index >= "2021-01-01"]
    c2 = int(len(p21win) * SPLIT)
    pte2 = p21win.iloc[c2:].reset_index(drop=True); mte2 = m21.iloc[c2:].reset_index(drop=True)
    r_raw2 = bt(pte2, mte2, mode="raw")
    print(f"  OOS starts {p21win.index[c2].date()}  raw Sh {r_raw2['sharpe']:.2f} "
          f"(net {r_raw2['net']*100:+.0f}%)")
    for mode, name in MODES[1:]:
        r2 = bt(pte2, mte2, mode=mode)
        print(f"    {name:<22} Sh {r2['sharpe']:.2f} (net {r2['net']*100:+.0f}%)")

    print("\n" + "=" * 80)
    print("Adopt residual momentum only if a TRAIN-selected variant beats live raw OOS robustly")
    print("(>=4/5 WF AND higher OOS Sharpe) AND survives the 7-phase test (P21's killer).")
    print("Otherwise the live raw multi-horizon ranking is the optimum — log as a dead-end.")


if __name__ == "__main__":
    main()
