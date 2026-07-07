"""P28 — Validate the LIVE portfolio drawdown circuit-breaker (`risk.max_drawdown_halt: 0.30`).

The live config carries a risk parameter that NO backtest in this repo has ever modeled:

    risk:
      max_drawdown_halt: 0.30

In the live engine (hermes_trading/portfolio.py:244-288) this is a PERMANENT kill switch — the
book is marked continuously and, the first time drawdown from peak-equity reaches 30%, the engine
flattens to cash, sets state "halted": true, and STAYS halted until a human manually deletes
portfolio.json. It never auto-re-enters.

Every finding in LOG.md (P13's +3349%, the whole validated stack) was produced with halt=OFF.
But P13/P16 report the live config's own equity draws down ~65% (2022 bear) to ~80% (2020-> full).
So the 30% halt WOULD have triggered — and because it is PERMANENT, it would have flattened the
book somewhere inside the 2021-2022 decline and then sat in cash through the ENTIRE 2023-2026
recovery. That is the P0/P3/P11 "DD-gate is drawdown-insurance with a steep return premium" trap,
except IRREVERSIBLE: a market-timing exit that can never whipsaw back in also can never recover.

This is genuinely new (never in the LOG), config-expressible, deployable, and directly
real-money-relevant: live equity is already ~0.78 (−22% DD per the drift tracker), so the strategy
is one bad fortnight from tripping a switch that no validation has ever accounted for.

What this tests (halt=OFF is the baseline EVERYTHING was validated on; halt=0.30-permanent is what
is ACTUALLY LIVE):
  (A) full-window 2020->  : OFF vs permanent{0.20,0.25,0.30,0.40} vs auto-reset{0.30}
  (B) WHEN does permanent-0.30 first trip on the continuous panel, and what % of the panel does it
      then spend locked in cash (the "misses the recovery" cost)
  (C) bear-2022 located    : does it trip inside the bear; does it help the bear but kill the after
  (D) phase robustness     : halt-trip timing is weekday-sensitive (P20) — is the effect an artifact
  (E) OOS test-half        : honest split

Marking: FAITHFUL DAILY marking (the live engine marks intraday, not weekly) so the DD trigger
fires at the right time. Cumulative net return is mark-frequency-invariant when halt=OFF, so the
OFF baseline still reproduces p27's R=7 net (+1220% on 2020->); only Sharpe/maxDD deepen vs the
weekly-marked LOG numbers because daily marking sees intra-week troughs. Sharpe annualised sqrt(365).

    /Users/jamesvaness/hermes-trading/.venv/bin/python scripts/p28_drawdown_halt.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import K, LBS, COST, SPLIT, MA_DAYS, multi_score  # noqa: E402

START = "2020-01-01"
R = 7            # LIVE weekly rebalance
WARM = max(LBS)
LIVE_HALT = 0.30


def _target_weights(panel, ma, i, top_k, trend):
    """Live strict-top-K weights at bar i: top-K multi-horizon momentum names above their MA."""
    sc = multi_score(panel, i).dropna()
    w = pd.Series(0.0, index=panel.columns)
    if len(sc) >= top_k:
        order = list(sc.sort_values(ascending=False).index)
        row = panel.iloc[i]
        marow = ma.iloc[i]
        for s in order[:top_k]:
            if (not trend) or (row[s] > marow[s]):
                w[s] = 1 / top_k
    return w


def bt_halt(panel, ma, h=0.0, reset="permanent", top_k=K, cost=COST, offset=0, trend=True):
    """LIVE strict-top-K weekly engine with DAILY marking and a portfolio DD circuit-breaker.

    h==0 -> halt OFF (exactly the validated baseline; net return == p27 R=7).
    reset=='permanent' -> once DD>=h, cash for the REST of the run (live behaviour).
    reset=='reenter'   -> flatten on breach, then resume normal selection at the next rebalance.
    Returns daily-marked metrics + halt diagnostics (first_trip index, days_halted, n_trips).
    """
    n = len(panel)
    day_rets = []
    prev = pd.Series(0.0, index=panel.columns)
    eq, peak, halted = 1.0, 1.0, False
    first_trip, n_trips, days_halted = None, 0, 0
    i = WARM + offset
    while i + R < n:
        if halted and reset == "permanent":
            w = pd.Series(0.0, index=panel.columns)
        else:
            if halted and reset == "reenter":
                halted = False  # a breach only flattens until the next rebalance
            w = _target_weights(panel, ma, i, top_k, trend)
        tc = (w - prev).abs().sum() * cost
        for d in range(i, i + R):
            dr = (w * (panel.iloc[d + 1] / panel.iloc[d] - 1)).sum()
            if d == i:
                dr -= tc
            eq *= (1 + dr)
            day_rets.append(dr)
            if halted:
                days_halted += 1
            peak = max(peak, eq)
            dd = (peak - eq) / peak
            if (not halted) and h and dd >= h:
                halted = True
                n_trips += 1
                if first_trip is None:
                    first_trip = d
                w = pd.Series(0.0, index=panel.columns)  # flatten remaining days of the period
        prev = w
        i += R
    rets = np.array(day_rets)
    if len(rets) < 2 or rets.std() == 0:
        return dict(net=0, sharpe=0, maxdd=0, worstday=0, first_trip=first_trip,
                    n_trips=n_trips, days_halted=days_halted, ndays=len(rets))
    ec = np.cumprod(1 + rets)
    pk = np.maximum.accumulate(ec)
    return dict(net=ec[-1] - 1, sharpe=rets.mean() / rets.std() * np.sqrt(365),
                maxdd=float(np.max((pk - ec) / pk)), worstday=float(rets.min()),
                first_trip=first_trip, n_trips=n_trips, days_halted=days_halted, ndays=len(rets))


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

    def day_date(k):
        return idx[k].date() if (k is not None and 0 <= k < len(idx)) else None

    print("=" * 96)
    print("P28 — LIVE drawdown circuit-breaker (risk.max_drawdown_halt) validation")
    print("=" * 96)
    print(f"panel {idx[0].date()}..{idx[-1].date()}  (~{yrs:.1f}y, {len(panel)} days, "
          f"{panel.shape[1]} coins)  cost {COST*1e4:.0f}bps/side  LBS={LBS} K={K} MA={MA_DAYS} R={R}")
    print("marking = DAILY (faithful to the live intraday MtM check). Sharpe annualised sqrt(365).")
    print("halt = PERMANENT kill switch (live): once DD>=h, cash until a human resets.\n")

    # ---------------- (A) full-window: OFF vs permanent thresholds vs auto-reset ----------------
    print("-" * 96)
    print("(A) FULL WINDOW 2020-> — OFF is the halt everything was validated on; 0.30-perm is LIVE:")
    print(f"  {'variant':<20}{'net%':>10}{'Sharpe':>8}{'maxDD':>8}{'worstDay':>10}"
          f"{'trips':>7}{'firstTrip':>13}{'%halted':>9}")
    variants = [("OFF (validated)", 0.0, "permanent"),
                ("perm 0.20", 0.20, "permanent"),
                ("perm 0.25", 0.25, "permanent"),
                ("perm 0.30  <-LIVE", 0.30, "permanent"),
                ("perm 0.40", 0.40, "permanent"),
                ("reenter 0.30", 0.30, "reenter")]
    res = {}
    for name, h, reset in variants:
        r = bt_halt(panel_r, ma_r, h=h, reset=reset)
        res[name] = r
        pct_halt = 100 * r["days_halted"] / r["ndays"] if r["ndays"] else 0
        print(f"  {name:<20}{r['net']*100:>9.0f}%{r['sharpe']:>8.2f}{r['maxdd']*100:>7.0f}%"
              f"{r['worstday']*100:>9.1f}%{r['n_trips']:>7}{str(day_date(r['first_trip'])):>13}"
              f"{pct_halt:>8.0f}%")
    off = res["OFF (validated)"]
    print(f"\n  sanity: OFF net {off['net']*100:.0f}% (should match p27 R=7 ~+1220%; net is "
          f"mark-freq-invariant). daily-marked maxDD {off['maxdd']*100:.0f}% is DEEPER than the "
          f"weekly-marked LOG ~80% because daily marking sees intra-week troughs.")

    # ---------------- (B) the "misses the recovery" cost of PERMANENCE ----------------
    print("\n" + "-" * 96)
    print("(B) permanence cost — after the 0.30 halt first trips, what is forgone:")
    live = res["perm 0.30  <-LIVE"]
    if live["first_trip"] is not None:
        ft = live["first_trip"]
        # value of staying invested (OFF) from the trip day to the end vs cash
        after = bt_halt(panel_r.iloc[ft:].reset_index(drop=True),
                        ma_r.iloc[ft:].reset_index(drop=True), h=0.0)
        print(f"  0.30-permanent first trips {day_date(ft)} and then holds CASH for the remaining "
              f"{live['days_halted']} days ({100*live['days_halted']/live['ndays']:.0f}% of the panel).")
        print(f"  From that day to {idx[-1].date()}, halt-OFF would have returned "
              f"{after['net']*100:+.0f}% (Sharpe {after['sharpe']:.2f}). That is the recovery the "
              f"permanent halt forgoes.")
        print(f"  Net effect: LIVE-perm full net {live['net']*100:+.0f}%  vs  OFF full net "
              f"{off['net']*100:+.0f}%  ->  permanence costs "
              f"{(live['net']-off['net'])*100:+.0f}pp of total return.")
    else:
        print("  0.30-permanent NEVER trips on this panel (daily maxDD stayed < 30%). It is a "
              "dormant tail-insurance here — no return cost, but no test of its behaviour either.")

    # ---------------- (C) bear-2022 located ----------------
    print("\n" + "-" * 96)
    print("(C) bear-2022 located — restrict the panel to 2021-06 -> 2023-06 (peak-through-recovery):")
    m = (idx >= "2021-06-01") & (idx <= "2023-06-30")
    pb = panel[m].reset_index(drop=True)
    mb = ma_full[m].reset_index(drop=True)
    print(f"  window {idx[m][0].date()}..{idx[m][-1].date()} ({len(pb)} days)")
    print(f"  {'variant':<20}{'net%':>10}{'Sharpe':>8}{'maxDD':>8}{'trips':>7}{'firstTrip':>13}")
    bidx = idx[m]
    for name, h, reset in variants:
        r = bt_halt(pb, mb, h=h, reset=reset)
        ftd = bidx[r["first_trip"]].date() if r["first_trip"] is not None else None
        print(f"  {name:<20}{r['net']*100:>9.0f}%{r['sharpe']:>8.2f}{r['maxdd']*100:>7.0f}%"
              f"{r['n_trips']:>7}{str(ftd):>13}")
    print("  (a lower maxDD here that comes with a much lower net = the classic DD-insurance/"
          "premium tradeoff; a PERMANENT trip early in the window kills the 2023 rebound too.)")

    # ---------------- (D) phase robustness (halt-trip timing is weekday-sensitive) ----------------
    print("\n" + "-" * 96)
    print("(D) PHASE robustness — full-window over start offsets 0..6 (P20 killer). The halt TRIP "
          "TIMING depends on the weekday grid, so its effect could be pure phase-luck:")
    print(f"  {'variant':<20}{'meanNet%':>10}{'meanSh':>8}{'meanDD':>8}{'tripped/7':>11}")
    for name, h, reset in [("OFF (validated)", 0.0, "permanent"),
                           ("perm 0.30  <-LIVE", 0.30, "permanent"),
                           ("reenter 0.30", 0.30, "reenter")]:
        nets, shs, dds, trips = [], [], [], 0
        for o in range(7):
            r = bt_halt(panel_r, ma_r, h=h, reset=reset, offset=o)
            nets.append(r["net"]); shs.append(r["sharpe"]); dds.append(r["maxdd"])
            trips += (r["n_trips"] > 0)
        print(f"  {name:<20}{np.mean(nets)*100:>9.0f}%{np.mean(shs):>8.2f}{np.mean(dds)*100:>7.0f}%"
              f"{trips:>9}/7")

    # ---------------- (E) OOS test-half ----------------
    print("\n" + "-" * 96)
    print(f"(E) OOS test half (last {(1-SPLIT)*100:.0f}%, from {idx[cut].date()}):")
    pt = panel_r.iloc[cut:].reset_index(drop=True)
    mt = ma_r.iloc[cut:].reset_index(drop=True)
    print(f"  {'variant':<20}{'net%':>10}{'Sharpe':>8}{'maxDD':>8}{'trips':>7}")
    for name, h, reset in variants:
        r = bt_halt(pt, mt, h=h, reset=reset)
        print(f"  {name:<20}{r['net']*100:>9.0f}%{r['sharpe']:>8.2f}{r['maxdd']*100:>7.0f}%"
              f"{r['n_trips']:>7}")

    print("\n" + "=" * 96)
    print("Read the verdict from (A)/(B): if 0.30-permanent trips and then forgoes a large recovery,")
    print("the LIVE permanent halt is a hidden return risk not present in any prior validation.")
    print("Whether to CHANGE it (auto-reset vs off vs threshold) is a real-money call for the human;")
    print("this run only quantifies + flags it. NO candidate unless a variant beats OFF honestly.")


if __name__ == "__main__":
    main()
