"""P16 — Bear-LOCATED OOS test (sharpens P15's caveat).

P15 re-validated the findings stack on the 2020-> multi-cycle panel, but its 60/40 OOS
split lands the TEST half post-2023 (a BULL window). So every per-finding "OOS" number in
P15 is bull-LOCATED — including the P10 survivorship haircut, which P15 itself flagged as a
lower bound because the injected deaths fell partly in the bull test-half. P16 fixes that by
measuring the live edge and the P10 haircut DIRECTLY inside the 2022 bear, where the weak
momentum regime and real-world coin deaths (LUNA May'22, Celsius/3AC Jun'22, FTX Nov'22)
compound — the scariest combination for a long-only survivor-panel backtest.

Method (honest, no look-ahead):
  * The live engine (multi-horizon 14/30/60d momentum, top-5, dual-momentum px>100d-MA-else-
    cash, weekly rebalance, equal weight, 15bps/side) runs CONTINUOUSLY across the full cached
    panel. Momentum scores and the 100d MA at each rebalance use only TRAILING history, so a
    rebalance dated 2022-01-03 is legitimately out-of-sample-in-time (the live params were
    selected on the recent ~2023-> window, never on 2022).
  * We then measure ONLY the slice of rebalances whose date falls inside a target window.
    Turnover cost is charged inside the window (you enter the bear already holding a book).
  * Bear windows: 2022 calendar year, and the peak->trough 2021-11 -> 2022-12 "deep bear".
    Bull contrast windows: 2020-04->2021-11 and 2023-01->2024-12.
  * TRAIN=bull / TEST=bear: select (top_k, trend on/off) on a bull TRAIN (2020-2021) and
    report the bear TEST — does a bull-fit choice generalize to the bear, and is the live
    config what TRAIN picks?
  * P10 survivorship LOCATED in the bear: kill floor(frac*eligible) coins at a random date
    INSIDE the bear window (real deaths cluster in bears), then re-measure the bear-slice edge.

Single-window caveat is real: 2022 is ONE bear (~52 weekly rebals, ~23-25 coins). This
sharpens a point estimate; it is not a multi-bear law (pre-2020 universe too thin to add 2018).

    python scripts/p16_bear_oos.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import (  # noqa: E402
    K, R, MA_DAYS, LBS, COST, IMMORTAL, FLOOR, DECAY_DAYS, multi_score,
)

WEEKS_PER_YEAR = 52.0


def bt_window(panel, ma, lo, hi, cost=COST, trend=True, top_k=K, track_deaths=False):
    """Run the live engine continuously over the full panel, but only accumulate the
    rebalances whose integer index satisfies lo <= i < hi (the located window). Warmup and
    momentum/MA scoring use full trailing history (no look-ahead). Returns the usual metrics
    computed on the in-window return stream."""
    rets, turns, grosses, deployed, held_deaths = [], [], [], [], 0
    prev = pd.Series(0.0, index=panel.columns)
    i = max(LBS)
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= top_k:
            for s in sc.sort_values().index[-top_k:]:
                if (not trend) or panel.iloc[i][s] > ma.iloc[i][s]:
                    w[s] = 1 / top_k
        fwd = (panel.iloc[i + R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0)
        gross = (w * fwd).sum()
        turn = (w - prev).abs().sum()
        if lo <= i < hi:  # only measure rebalances located in the target window
            if track_deaths:
                held_deaths += int(((w > 0) & (fwd < -0.5)).sum())
            rets.append(gross - turn * cost)
            turns.append(turn)
            grosses.append(gross)
            deployed.append(float(w.sum()))  # 1.0 fully invested, 0.0 all-cash
        prev = w
        i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return dict(net=0, sharpe=0, maxdd=0, worstwk=0, turnover=0, gross=0,
                    inmkt=0, held_deaths=held_deaths, n=len(rets))
    eq = np.cumprod(1 + rets)
    pk = np.maximum.accumulate(eq)
    return dict(net=eq[-1] - 1, sharpe=rets.mean() / rets.std() * np.sqrt(365 / R),
                maxdd=float(np.max((pk - eq) / pk)), worstwk=float(rets.min()),
                turnover=float(np.mean(turns)), gross=float(np.mean(grosses)),
                inmkt=float(np.mean(deployed)),
                held_deaths=held_deaths, n=len(rets))


def inject_deaths_in_window(full, frac, seed, lo_ts, hi_ts):
    """P10 death model CONCENTRATED in [lo_ts, hi_ts]: kill floor(frac*eligible) coins at a
    random date inside the window (only coins live & with valid MA warmup at that point are
    eligible to die there), crash to FLOOR over DECAY_DAYS, then delisted-flat. Models the
    real pattern: blowups cluster in bears."""
    rng = np.random.default_rng(seed)
    idx = full.index
    win = np.where((idx >= lo_ts) & (idx <= hi_ts))[0]
    if len(win) == 0:
        return full.copy(), 0
    eligible = [c for c in full.columns if c not in IMMORTAL]
    n_kill = int(np.floor(frac * len(eligible)))
    if n_kill == 0:
        return full.copy(), 0
    doomed = rng.choice(eligible, size=n_kill, replace=False)
    p = full.copy()
    n = len(p)
    killed = 0
    for c in doomed:
        valid = np.where(np.isfinite(p[c].values))[0]
        if len(valid) < MA_DAYS + 60 + R + 2:
            continue
        first_ok = valid[0] + MA_DAYS + 60  # need warmup before a coin can be selected/killed
        cand = [d for d in win if first_ok <= d <= min(valid[-1], n - R - 2)]
        if not cand:
            continue
        d = int(rng.choice(cand))
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


def idx_range(index, lo_str, hi_str):
    lo = index.searchsorted(pd.Timestamp(lo_str))
    hi = index.searchsorted(pd.Timestamp(hi_str))
    return int(lo), int(hi)


def main():
    syms = live_universe()
    panel = load_panel(syms, "1d")
    if panel.empty:
        sys.exit("No cache. Run: EXCHANGE_ID=kucoin python scripts/data_cache.py --update")
    panel.index = pd.to_datetime(panel.index, unit="ms")
    panel = panel.sort_index()
    panel = panel.dropna(axis=1, thresh=120)
    ma = panel.rolling(MA_DAYS).mean()
    panel_r = panel.reset_index(drop=True)
    ma_r = ma.reset_index(drop=True)
    idx = panel.index

    print("=" * 80)
    print("P16 — bear-LOCATED OOS: live edge + P10 survivorship haircut INSIDE the 2022 bear")
    print("=" * 80)
    print(f"panel {idx[0].date()}..{idx[-1].date()}  {panel.shape[1]} coins  "
          f"cost {COST*1e4:.0f}bps/side\n")

    windows = [
        ("BEAR 2022 (calendar)", "2022-01-01", "2022-12-31"),
        ("BEAR deep (peak->trough)", "2021-11-08", "2022-12-31"),
        ("BULL 2020-04->2021-11", "2020-04-01", "2021-11-08"),
        ("BULL 2023-01->2024-12", "2023-01-01", "2024-12-31"),
    ]

    print("-" * 80)
    print("(1) LIVE CONFIG located in each regime window (continuous engine, measured slice):")
    print(f"{'window':<28}{'rebals':>7}{'net%':>9}{'Sharpe':>8}{'maxDD':>8}"
          f"{'worstWk':>9}{'inmkt':>7}")
    located = {}
    for name, a, b in windows:
        lo, hi = idx_range(idx, a, b)
        r = bt_window(panel_r, ma_r, lo, hi)
        located[name] = r
        print(f"{name:<28}{r['n']:>7}{r['net']*100:>8.1f}%{r['sharpe']:>8.2f}"
              f"{r['maxdd']*100:>7.1f}%{r['worstwk']*100:>8.1f}%{r['inmkt']*100:>6.0f}%")

    # ---------------- (2) TRAIN=bull / TEST=bear param selection ----------------
    print("\n" + "-" * 80)
    print("(2) TRAIN=bull(2020-04->2021-11) selects (top_k, trend); TEST=bear 2022:")
    tr_lo, tr_hi = idx_range(idx, "2020-04-01", "2021-11-08")
    te_lo, te_hi = idx_range(idx, "2022-01-01", "2022-12-31")
    best, best_sh = None, -1e9
    grid = [(k, t) for k in (3, 5, 8) for t in (True, False)]
    for k, t in grid:
        rtr = bt_window(panel_r, ma_r, tr_lo, tr_hi, top_k=k, trend=t)
        if rtr["sharpe"] > best_sh:
            best_sh, best = rtr["sharpe"], (k, t)
    k, t = best
    rte = bt_window(panel_r, ma_r, te_lo, te_hi, top_k=k, trend=t)
    rlive = bt_window(panel_r, ma_r, te_lo, te_hi, top_k=K, trend=True)
    print(f"  TRAIN-best params: top_k={k} trend={'ON' if t else 'OFF'} "
          f"(train Sharpe {best_sh:.2f})")
    print(f"  -> bear TEST with TRAIN-best: net {rte['net']*100:+.1f}%  "
          f"Sharpe {rte['sharpe']:.2f}  maxDD {rte['maxdd']*100:.1f}%")
    print(f"  -> bear TEST with LIVE config (K5,trendON): net {rlive['net']*100:+.1f}%  "
          f"Sharpe {rlive['sharpe']:.2f}  maxDD {rlive['maxdd']*100:.1f}%")
    # contrast: best param IN the bear (in-sample, can't be chosen ex-ante) = upper bound
    bear_best, bear_best_sh = None, -1e9
    for kk, tt in grid:
        rr = bt_window(panel_r, ma_r, te_lo, te_hi, top_k=kk, trend=tt)
        if rr["sharpe"] > bear_best_sh:
            bear_best_sh, bear_best = rr["sharpe"], (kk, tt, rr["net"])
    print(f"  (in-sample best-in-bear, NOT choosable ex-ante: K{bear_best[0]} "
          f"trend={'ON' if bear_best[1] else 'OFF'} net {bear_best[2]*100:+.1f}% "
          f"Sharpe {bear_best_sh:.2f})")

    # ---------------- (3) P10 survivorship LOCATED in the bear ----------------
    print("\n" + "-" * 80)
    SEEDS = 120
    lo_ts, hi_ts = pd.Timestamp("2021-11-08"), pd.Timestamp("2022-12-31")
    print(f"(3) P10 survivorship haircut LOCATED in the bear ({SEEDS} seeds, deaths placed "
          f"inside\n    the 2021-11->2022-12 bear). Measured on the 2022-calendar slice:")
    base = located["BEAR 2022 (calendar)"]
    print(f"  survivors-only (live):  net {base['net']*100:+6.1f}%  "
          f"Sharpe {base['sharpe']:.2f}  maxDD {base['maxdd']*100:.1f}%  "
          f"held-deaths {base['held_deaths']}")
    for frac in (0.10, 0.20, 0.30):
        nets, shs, dds, kills, hds = [], [], [], [], []
        for sd in range(SEEDS):
            pdead, nk = inject_deaths_in_window(panel, frac, sd, lo_ts, hi_ts)
            pdr = pdead.reset_index(drop=True)
            mdr = pdead.rolling(MA_DAYS).mean().reset_index(drop=True)
            r = bt_window(pdr, mdr, te_lo, te_hi, track_deaths=True)
            nets.append(r["net"]); shs.append(r["sharpe"]); dds.append(r["maxdd"])
            kills.append(nk); hds.append(r["held_deaths"])
        hc = (np.median(nets) - base["net"]) * 100
        print(f"  death {frac*100:>2.0f}% (~{int(np.mean(kills))} coins, "
              f"~{np.mean(hds):.1f} held-to-death): "
              f"net {np.median(nets)*100:+6.1f}% "
              f"[{np.percentile(nets,10)*100:+.0f},{np.percentile(nets,90)*100:+.0f}]  "
              f"Sharpe {np.median(shs):.2f}  maxDD {np.median(dds)*100:.0f}%  "
              f"(Δnet {hc:+.1f}pp)")
    # trend-filter protection IN the bear @20% deaths
    print("  trend-filter protection in the bear @20% deaths:")
    for trend in (True, False):
        nets, dds, hds = [], [], []
        for sd in range(SEEDS):
            pdead, _ = inject_deaths_in_window(panel, 0.20, sd, lo_ts, hi_ts)
            pdr = pdead.reset_index(drop=True)
            mdr = pdead.rolling(MA_DAYS).mean().reset_index(drop=True)
            r = bt_window(pdr, mdr, te_lo, te_hi, trend=trend, track_deaths=True)
            nets.append(r["net"]); dds.append(r["maxdd"]); hds.append(r["held_deaths"])
        print(f"    trend {'ON (live)' if trend else 'OFF     '}: "
              f"net {np.median(nets)*100:+.1f}%  maxDD {np.median(dds)*100:.0f}%  "
              f"held-deaths ~{np.mean(hds):.1f}")

    print("\n" + "=" * 80)
    print("Read: how bad is the live edge IN the bear (point estimate), and how much worse")
    print("when survivorship deaths are placed inside it? One bear, small sample — a sharpened")
    print("point estimate, not a multi-bear law.")


if __name__ == "__main__":
    main()
