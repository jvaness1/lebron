"""Stress-test the 1h Donchian breakout winner before trusting it.

  1. Parameter NEIGHBOURHOOD: is the whole region positive OOS, or one knife-edge cell?
  2. Longer history: pull as much 1h as KuCoin serves.
  3. WALK-FORWARD: fixed config across sequential time slices — temporally stable?
  4. Outlier check: is OOS profit spread across trades or carried by one?

EXCHANGE_ID=kucoin python scripts/validate_donchian.py
"""
import os
import asyncio
import itertools

import numpy as np
import pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters, metrics      # noqa: E402
from hermes_trading.loop import load_strategy      # noqa: E402

_c = load_strategy().get("costs") or {}
FEE = float(_c.get("fees_bps", 10.0)) / 1e4
SLIP = float(_c.get("slippage_bps", 5.0)) / 1e4


def simulate(h, l, c, ts, entry, exit_, stop_pct):
    trades, pos = [], None
    for i in range(len(c)):
        if pos is None:
            if entry[i]:
                pos = (c[i], ts[i])
        else:
            px, ents = pos
            stop_px = px * (1 - stop_pct / 100)
            exit_px = stop_px if l[i] <= stop_px else (c[i] if exit_[i] else None)
            if exit_px is not None:
                eff_in, eff_out = px * (1 + SLIP), exit_px * (1 - SLIP)
                trades.append({"status": "closed",
                               "return_pct": (eff_out - eff_in) / eff_in - 2 * FEE,
                               "entry_ts": ents, "exit_ts": ts[i]})
                pos = None
    return trades


def donchian_signals(df, en, xn):
    h, l, c = df["h"].to_numpy(), df["l"].to_numpy(), df["c"].to_numpy()
    roll_hi = pd.Series(h).rolling(en).max().shift(1).to_numpy()
    roll_lo = pd.Series(l).rolling(xn).min().shift(1).to_numpy()
    return c > roll_hi, c < roll_lo


def run(df, en, xn, stop):
    e, x = donchian_signals(df, en, xn)
    return simulate(df["h"].to_numpy(), df["l"].to_numpy(), df["c"].to_numpy(),
                    df["ts"].to_numpy(), e, x, stop)


async def main():
    hist = await adapters.price.fetch_history("SOL/USDT", timeframe="1h", total=20000)
    cs = hist["candles"]
    df = pd.DataFrame(cs, columns=["ts", "o", "h", "l", "c", "v"])
    days = (cs[-1][0] - cs[0][0]) / 86400000
    print(f"\n1h · {len(df)} bars ({days:.0f} days) from {hist['exchange']} · "
          f"costs {FEE*1e4:.0f}+{SLIP*1e4:.0f}bps/side\n")

    # 1. Neighbourhood on a 60/40 split — count how many cells are OOS-positive.
    cut = int(len(df) * 0.6)
    print("1) PARAMETER NEIGHBOURHOOD (test-half net% / PF / n):")
    pos_cells = total_cells = 0
    for en, xn, stop in itertools.product([80, 100, 120], [5, 10, 15, 20], [3, 4, 5, 6]):
        te = metrics.summary(run(df.iloc[cut:], en, xn, stop))
        if te.get("n", 0) < 10:
            continue
        total_cells += 1
        ok = te["net_return"] > 0 and te["profit_factor"] > 1.1
        pos_cells += ok
        if (en, xn, stop) in [(100, 10, 4)] or ok:
            mark = "✅" if ok else "  "
            print(f"   {mark} in{en}/out{xn}/stop{stop}: "
                  f"{te['net_return']*100:+6.1f}%  PF {te['profit_factor']:.2f}  n{te['n']}")
    print(f"   → {pos_cells}/{total_cells} cells positive OOS "
          f"({'robust region' if pos_cells > total_cells*0.5 else 'fragile / knife-edge'})\n")

    # 2. Walk-forward: fixed sensible config across 5 sequential slices.
    en, xn, stop = 100, 10, 4
    print(f"2) WALK-FORWARD (fixed in{en}/out{xn}/stop{stop}, 5 sequential slices):")
    folds = np.array_split(np.arange(len(df)), 5)
    fold_nets = []
    for k, idx in enumerate(folds):
        seg = df.iloc[idx[0]:idx[-1] + 1]
        s = metrics.summary(run(seg, en, xn, stop))
        if s.get("n", 0) == 0:
            print(f"   slice {k+1}: no trades"); continue
        fold_nets.append(s["net_return"])
        print(f"   slice {k+1} ({(seg['ts'].iloc[-1]-seg['ts'].iloc[0])/86400000:.0f}d): "
              f"net {s['net_return']*100:+6.1f}%  PF {s['profit_factor']:.2f}  "
              f"n{s['n']}  win{s['win_rate']*100:.0f}%")
    pos_folds = sum(1 for x in fold_nets if x > 0)
    print(f"   → {pos_folds}/{len(fold_nets)} slices profitable\n")

    # 3. Full-sample run + outlier check.
    trades = run(df, en, xn, stop)
    s = metrics.summary(trades)
    rets = sorted((t["return_pct"] for t in trades), reverse=True)
    top = rets[0] if rets else 0
    total = sum(rets)
    print("3) FULL SAMPLE + OUTLIER CHECK:")
    print(metrics.format_summary(s, f"FULL in{en}/out{xn}/stop{stop}"))
    if total != 0:
        print(f"   biggest single trade = {top*100:+.1f}%  "
              f"({top/total*100:.0f}% of total additive return — "
              f"{'outlier-dependent' if total>0 and top/total>0.6 else 'well distributed'})")


asyncio.run(main())
