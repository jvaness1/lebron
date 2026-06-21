"""P15 — Re-validate the prior findings stack on the less-bull-flattered 2020-> window.

P13 built a serial/throttled OHLCV cache (data/ohlcv) reaching back to 2017 and showed the
recent ~3yr basis the prior P-numbers used was BULL-FLATTERED (live-config Sharpe 1.33->0.91,
true maxDD ~80% not ~46% once the full 2022 bear is in the input). P8 (cost), P10
(survivorship), P11 (DD smoothing) and P14 (income/SWR) were ALL fit/reported on that
flattered ~2023-> window. P15 cheaply re-runs their cores on the 2020-> multi-cycle panel
(from the cache) to see whether the stack holds when the input distribution includes the
full 2022 bear.

This reuses the EXACT live engine (multi-horizon 14/30/60d momentum, top-5, dual-momentum
trend filter px>100d MA else cash, weekly rebalance, equal weight, 15bps/side) and the
EXACT death model + bootstrap from p10/p14 — only the input window changes (cache 2020->
instead of adapters total=1200). Honest OOS: 60/40 split reported on TEST, plus per-year and
5-slice walk-forward. Monte-Carlo seed counts trimmed vs the originals (this is a robustness
re-check, not a primary fit) — noted at each section.

    python scripts/p15_revalidate.py [--start 2020-01-01]
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402

K, R, SPLIT = 5, 7, 0.60
MA_DAYS = 100
LBS = [14, 30, 60]
COST = 15 / 1e4
# P10/P14 death model
FLOOR = 0.02
DECAY_DAYS = 5
IMMORTAL = {"BTC/USDT", "ETH/USDT"}
WEEKS_PER_YEAR = 52.0


def multi_score(panel, i):
    return sum(panel.iloc[i] / panel.iloc[i - lb] - 1 for lb in LBS) / len(LBS)


def bt(panel, ma, cost=COST, trend=True, mode="base", stop=None, cap=None,
       track_deaths=False):
    """Live long-only multi-horizon K5 weekly engine (+ optional DD overlay).
    Returns dict(net, sharpe, maxdd, worstwk, turnover, gross, held_deaths, n)."""
    if mode == "ddgate":
        mret = panel.pct_change().mean(axis=1).fillna(0)
        midx = (1 + mret).cumprod()
        mdd = (midx.cummax() - midx) / midx.cummax()
    rets, turns, grosses, held_deaths = [], [], [], 0
    prev = pd.Series(0.0, index=panel.columns)
    i = max(LBS)
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= K:
            for s in sc.sort_values().index[-K:]:
                if (not trend) or panel.iloc[i][s] > ma.iloc[i][s]:
                    w[s] = 1 / K
        if mode == "cap":
            w = w.clip(upper=cap)
        elif mode == "ddgate" and mdd.iloc[i] >= stop:
            w = pd.Series(0.0, index=panel.columns)
        fwd = (panel.iloc[i + R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0)
        if track_deaths:
            held_deaths += int(((w > 0) & (fwd < -0.5)).sum())
        gross = (w * fwd).sum()
        turn = (w - prev).abs().sum()
        rets.append(gross - turn * cost)
        turns.append(turn); grosses.append(gross)
        prev = w; i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return dict(net=0, sharpe=0, maxdd=0, worstwk=0, turnover=0, gross=0,
                    held_deaths=held_deaths, n=len(rets), rets=rets)
    eq = np.cumprod(1 + rets); pk = np.maximum.accumulate(eq)
    return dict(net=eq[-1] - 1, sharpe=rets.mean() / rets.std() * np.sqrt(365 / R),
                maxdd=float(np.max((pk - eq) / pk)), worstwk=float(rets.min()),
                turnover=float(np.mean(turns)), gross=float(np.mean(grosses)),
                held_deaths=held_deaths, n=len(rets), rets=rets)


def inject_deaths(full, frac, seed):
    """P10 death model, WINDOW-AWARE for ragged data: kill floor(frac*eligible) coins at a
    random date WITHIN each coin's own live (non-NaN) range, crash to FLOOR over DECAY_DAYS,
    then delisted-flat. (The original picked a global date and silently skipped coins that
    were NaN there, under-injecting on a ragged panel — fixed here.)"""
    rng = np.random.default_rng(seed)
    eligible = [c for c in full.columns if c not in IMMORTAL]
    n_kill = int(np.floor(frac * len(eligible)))
    if n_kill == 0:
        return full.copy(), 0
    doomed = rng.choice(eligible, size=n_kill, replace=False)
    p = full.copy(); n = len(p)
    killed = 0
    for c in doomed:
        valid = np.where(np.isfinite(p[c].values))[0]
        if len(valid) < MA_DAYS + 60 + R + 2:
            continue
        lo, hi = valid[0] + MA_DAYS + 60, min(valid[-1], n - R - 2)
        if hi <= lo:
            continue
        d = int(rng.integers(lo, hi))
        base = p[c].iloc[d - 1]
        if not np.isfinite(base) or base <= 0:
            continue
        loc = p.columns.get_loc(c)
        for k in range(DECAY_DAYS):
            if d + k < n:
                p.iloc[d + k, loc] = base * (FLOOR ** ((k + 1) / DECAY_DAYS))
        if d + DECAY_DAYS < n:
            p.iloc[d + DECAY_DAYS:, loc] = base * FLOOR
        killed += 1
    return p, killed


# ---------- P14 bootstrap ----------
def bootstrap_path(pool, length, rng, block):
    out = []
    while len(out) < length:
        series = pool[rng.integers(len(pool))]
        if len(series) <= block:
            out.extend(series.tolist()); continue
        start = rng.integers(0, len(series) - block)
        out.extend(series[start:start + block].tolist())
    return np.array(out[:length])


def simulate(pool, f, w_rate, horizon_weeks, n_paths, rng, block=8, mean_shift=0.0):
    monthly_wd = w_rate / 12.0
    wpm = WEEKS_PER_YEAR / 12.0
    ruin = 0; terms = np.empty(n_paths)
    for p in range(n_paths):
        path = bootstrap_path(pool, horizon_weeks, rng, block) + mean_shift
        eq = 1.0; month = 0; dead = False
        for t in range(horizon_weeks):
            eq *= (1 + f * path[t])
            if int((t + 1) / wpm) > month:
                month += 1; eq -= monthly_wd
                if eq <= 0:
                    dead = True; eq = 0.0; break
        if dead:
            ruin += 1
        terms[p] = eq
    return ruin / n_paths, float(np.median(terms))


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
    cut = int(len(panel) * SPLIT)
    yrs = (panel.index[-1] - panel.index[0]).days / 365
    print(f"\n{'='*78}\nP15 — re-validate the findings stack on the 2020-> multi-cycle window")
    print(f"{'='*78}")
    print(f"window {panel.index[0].date()} .. {panel.index[-1].date()} "
          f"(~{yrs:.1f}y, {len(panel)} days), {panel.shape[1]} coins")
    print(f"OOS test half starts {panel.index[cut].date()} "
          f"(~{(panel.index[-1]-panel.index[cut]).days}d). cost {COST*1e4:.0f}bps/side")
    live = panel.notna().sum(axis=1)
    print(f"coins live: {int(live.iloc[0])} at start -> {int(live.iloc[-1])} now "
          f"(median {int(live.median())})\n")

    pte = panel.iloc[cut:].reset_index(drop=True)
    mte = ma.iloc[cut:].reset_index(drop=True)

    # ---------------- BASELINE (the foundation P13 flagged) ----------------
    print("-" * 78)
    print("BASELINE live config on this window (the bull-flatter correction):")
    full_b = bt(panel.reset_index(drop=True), ma.reset_index(drop=True))
    oos_b = bt(pte, mte)
    print(f"  FULL  : net {full_b['net']*100:+.0f}%  Sharpe {full_b['sharpe']:.2f}  "
          f"maxDD {full_b['maxdd']*100:.0f}%  ({full_b['n']} rebals)")
    print(f"  OOS   : net {oos_b['net']*100:+.0f}%  Sharpe {oos_b['sharpe']:.2f}  "
          f"maxDD {oos_b['maxdd']*100:.0f}%  ({oos_b['n']} rebals)")

    folds = np.array_split(np.arange(len(panel)), 5)

    def wf(**kw):
        out = []
        for idx in folds:
            seg = panel.iloc[idx[0]:idx[-1] + 1].reset_index(drop=True)
            ms = ma.iloc[idx[0]:idx[-1] + 1].reset_index(drop=True)
            out.append(bt(seg, ms, **kw))
        return out

    wfb = wf()
    print(f"  5-slice WF Sharpe: {[round(float(r['sharpe']),2) for r in wfb]} "
          f"({sum(1 for r in wfb if r['sharpe']>0)}/5+)")
    print(f"  5-slice WF net%:   {[round(float(r['net']*100)) for r in wfb]}")

    # ---------------- P8: cost / turnover ----------------
    print("\n" + "-" * 78)
    print("P8 RE-CHECK — cost/turnover sensitivity (OOS):")
    print(f"{'cost/side':>10}{'net%':>9}{'Sharpe':>8}{'maxDD':>8}")
    gross = turn = None
    for cb in (0, 15, 30, 60, 100):
        r = bt(pte, mte, cost=cb / 1e4)
        gross, turn = r["gross"], r["turnover"]
        tag = "  <-backtest" if cb == 15 else ("  <-Coinbase-real" if cb == 60 else "")
        print(f"{cb:>8}bp{r['net']*100:>8.1f}%{r['sharpe']:>8.2f}{r['maxdd']*100:>7.1f}%{tag}")
    be = gross / turn if turn else float("nan")
    print(f"  turnover {turn:.3f}x/rebal · mean gross {gross*100:.3f}%/rebal "
          f"-> analytic break-even {be*1e4:.0f}bps/side ({be*100:.2f}%)")
    print("  WF net% @15bps:", [round(float(bt(panel.iloc[i[0]:i[-1]+1].reset_index(drop=True),
          ma.iloc[i[0]:i[-1]+1].reset_index(drop=True), cost=15/1e4)['net']*100)) for i in folds])
    print("  WF net% @60bps:", [round(float(bt(panel.iloc[i[0]:i[-1]+1].reset_index(drop=True),
          ma.iloc[i[0]:i[-1]+1].reset_index(drop=True), cost=60/1e4)['net']*100)) for i in folds])

    # ---------------- P10: survivorship ----------------
    print("\n" + "-" * 78)
    SEEDS = 100
    print(f"P10 RE-CHECK — survivorship haircut (OOS, {SEEDS} seeds, window-aware deaths):")
    print(f"  BASELINE (survivors-only OOS): net {oos_b['net']*100:+.1f}%  "
          f"Sharpe {oos_b['sharpe']:.2f}  maxDD {oos_b['maxdd']*100:.1f}%")
    for frac in (0.10, 0.20, 0.30):
        nets, shs, dds, kills = [], [], [], []
        for sd in range(SEEDS):
            pdead, nk = inject_deaths(panel, frac, sd)
            pt = pdead.iloc[cut:].reset_index(drop=True)
            mt = pdead.rolling(MA_DAYS).mean().iloc[cut:].reset_index(drop=True)
            r = bt(pt, mt)
            nets.append(r["net"]); shs.append(r["sharpe"]); dds.append(r["maxdd"]); kills.append(nk)
        print(f"  death {frac*100:>2.0f}% (~{int(np.mean(kills))} coins): "
              f"net {np.median(nets)*100:+6.1f}% [{np.percentile(nets,10)*100:+.0f},"
              f"{np.percentile(nets,90)*100:+.0f}]  Sharpe {np.median(shs):.2f}  "
              f"maxDD {np.median(dds)*100:.0f}%")
    # trend-filter protection @20%
    for trend in (True, False):
        nets, dds = [], []
        for sd in range(SEEDS):
            pdead, _ = inject_deaths(panel, 0.20, sd)
            pt = pdead.iloc[cut:].reset_index(drop=True)
            mt = pdead.rolling(MA_DAYS).mean().iloc[cut:].reset_index(drop=True)
            r = bt(pt, mt, trend=trend)
            nets.append(r["net"]); dds.append(r["maxdd"])
        print(f"  trend {'ON (live)' if trend else 'OFF     '} @20% deaths: "
              f"net {np.median(nets)*100:+.1f}%  maxDD {np.median(dds)*100:.0f}%")

    # ---------------- P11: DD levers ----------------
    print("\n" + "-" * 78)
    print("P11 RE-CHECK — DD levers (OOS). market-DD gate picked on TRAIN, cap is linear:")
    ptr = panel.iloc[:cut].reset_index(drop=True); mtr = ma.iloc[:cut].reset_index(drop=True)
    best = None
    for x in (0.10, 0.15, 0.20, 0.25, 0.30):
        r = bt(ptr, mtr, mode="ddgate", stop=x)
        if best is None or r["sharpe"] > best[1]:
            best = (x, r["sharpe"])
    sx = best[0]
    rg = bt(pte, mte, mode="ddgate", stop=sx)
    print(f"  market-DD gate (TRAIN-picked {sx*100:.0f}%): OOS net {rg['net']*100:+.1f}%  "
          f"Sharpe {rg['sharpe']:.2f}  maxDD {rg['maxdd']*100:.1f}%  vs base Sharpe {oos_b['sharpe']:.2f}")
    wfg = wf(mode="ddgate", stop=sx)
    print(f"    gate WF Sharpe {[round(float(r['sharpe']),2) for r in wfg]} "
          f"({sum(1 for r in wfg if r['sharpe']>0)}/5+) vs base "
          f"({sum(1 for r in wfb if r['sharpe']>0)}/5+)")
    for c in (0.20, 0.10):
        r = bt(pte, mte, mode="cap", cap=c)
        print(f"  weight cap {c:.2f} (inv {min(1,5*c)*100:.0f}%): OOS net {r['net']*100:+.1f}%  "
              f"Sharpe {r['sharpe']:.2f}  maxDD {r['maxdd']*100:.1f}%  worstWk {r['worstwk']*100:.1f}%")

    # ---------------- P14: income / SWR ----------------
    print("\n" + "-" * 78)
    N_POOL, N_PATHS, BLOCK, HORIZON = 40, 3000, 8, 5
    print(f"P14 RE-CHECK — income/SWR ({N_POOL}-seed pool, {N_PATHS} paths, block {BLOCK}w, {HORIZON}yr):")
    surv = bt(panel.reset_index(drop=True), ma.reset_index(drop=True))["rets"]
    pool = []
    for sd in range(N_POOL):
        pdead, _ = inject_deaths(panel, 0.20, sd)
        pool.append(bt(pdead.reset_index(drop=True),
                       pdead.rolling(MA_DAYS).mean().reset_index(drop=True))["rets"])
    all_dead = np.concatenate(pool)
    surv_geo = np.prod(1 + surv) ** (WEEKS_PER_YEAR / len(surv)) - 1
    dead_geo = (np.prod(1 + all_dead) ** (WEEKS_PER_YEAR / len(all_dead)) - 1
                if (1 + all_dead).min() > 0 else float("nan"))
    print(f"  weekly pools: survivors ~{surv_geo*100:+.0f}%/yr (mean {surv.mean()*100:+.2f}%/wk) | "
          f"P10-stressed ~{dead_geo*100:+.0f}%/yr (mean {all_dead.mean()*100:+.2f}%/wk, "
          f"worstWk {all_dead.min()*100:.0f}%)")
    rng = np.random.default_rng(12345)
    hw = int(round(HORIZON * WEEKS_PER_YEAR))
    print(f"  SWR (max withdraw/yr, ruin<=5% over {HORIZON}yr), P10-stressed returns:")
    for f in (1.0, 0.5):
        swr = 0.0
        for wr in (0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30):
            rp, _ = simulate(pool, f, wr, hw, N_PATHS, rng, block=BLOCK)
            if rp <= 0.05 and wr > swr:
                swr = wr
        print(f"    invested f={f:.2f}: SWR {swr*100:.0f}%/yr")
    # return-stress: halve the pooled mean
    shift = -all_dead.mean() * 0.5
    geo_s = np.prod(1 + all_dead + shift) ** (WEEKS_PER_YEAR / len(all_dead)) - 1
    swr_s = 0.0
    for wr in (0.0, 0.05, 0.10, 0.15, 0.20):
        rp, _ = simulate(pool, 1.0, wr, hw, N_PATHS, rng, block=BLOCK, mean_shift=shift)
        if rp <= 0.05 and wr > swr_s:
            swr_s = wr
    print(f"  RETURN-STRESS (mean halved -> ~{geo_s*100:+.0f}%/yr, f=1.00): SWR {swr_s*100:.0f}%/yr")

    print("\n" + "=" * 78)
    print("Read against the 2023-> originals: does each finding's CHARACTER survive when")
    print("the 2022 bear is in the input? (numbers shift; the QUALITATIVE verdict is the test)")


if __name__ == "__main__":
    main()
