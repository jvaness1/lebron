"""Honest strategy search.

Tests several long-only strategy families across timeframes on deep KuCoin data,
with the SAME fees+slippage the live engine uses. Methodology that resists self-
deception:

  1. Split each series 60/40 into train / test.
  2. Sweep each family's params on TRAIN only.
  3. Pick the best TRAIN config (by Sharpe) that ALSO has enough trades on both
     segments (so we never crown a 3-trade fluke).
  4. Report that config's TEST (out-of-sample) result. That's the number that counts.

Writes nothing to state/. Run: EXCHANGE_ID=kucoin python scripts/strategy_search.py
"""
import os
import asyncio
import itertools

import numpy as np
import pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")  # deep history

from hermes_trading import adapters, metrics  # noqa: E402
from hermes_trading.loop import load_strategy  # noqa: E402

TIMEFRAMES = {"5m": 16000, "15m": 14000, "1h": 14000}
SPLIT = 0.60
MIN_TRAIN_TRADES = 20
MIN_TEST_TRADES = 15

_costs = (load_strategy().get("costs") or {})
FEE = float(_costs.get("fees_bps", 10.0)) / 1e4
SLIP = float(_costs.get("slippage_bps", 5.0)) / 1e4


# --------------------------------------------------------------------------- #
# Core simulator — one long position at a time, decision & fill at bar close,
# stop checked intrabar against the low. Returns closed trades (net of costs).
# --------------------------------------------------------------------------- #
def simulate(o, h, l, c, ts, entry, exit_, stop_pct, max_hold):
    trades = []
    pos = None
    n = len(c)
    for i in range(n):
        if pos is None:
            if entry[i]:
                pos = (c[i], i, ts[i])
        else:
            px, idx, ents = pos
            held = i - idx
            stop_px = px * (1.0 - stop_pct / 100.0)
            exit_px = None
            if l[i] <= stop_px:
                exit_px = stop_px
            elif exit_[i] or (max_hold and held >= max_hold):
                exit_px = c[i]
            if exit_px is not None:
                eff_in = px * (1.0 + SLIP)
                eff_out = exit_px * (1.0 - SLIP)
                net = (eff_out - eff_in) / eff_in - 2.0 * FEE
                trades.append({"status": "closed", "return_pct": net,
                               "entry_ts": ents, "exit_ts": ts[i]})
                pos = None
    return trades


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def _rsi(c, period=14):
    d = pd.Series(c).diff()
    gain = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50).to_numpy()


# --------------------------------------------------------------------------- #
# Strategy families → (entry_bool, exit_bool, stop_pct, max_hold)
# Each yields (label, params, signals)
# --------------------------------------------------------------------------- #
def fam_zscore(df):
    c = df["c"].to_numpy()
    s = pd.Series(c)
    for lb, ez, xz, stop, mh in itertools.product(
            [20, 50, 100], [2.0, 2.5, 3.0], [0.0, 0.5, 1.0], [1.5, 3.0], [48, 96]):
        sma = s.rolling(lb).mean()
        sd = s.rolling(lb).std()
        z = ((s - sma) / sd).to_numpy()
        entry = z <= -ez
        exit_ = z >= -xz
        yield (f"zscore lb{lb} z{ez}/{xz} stop{stop} hold{mh}",
               (entry, exit_, stop, mh))


def fam_donchian(df):
    h, l, c = df["h"].to_numpy(), df["l"].to_numpy(), df["c"].to_numpy()
    hs, ls = pd.Series(h), pd.Series(l)
    for en, xn, stop in itertools.product([20, 50, 100], [10, 20, 50], [2.0, 4.0, 8.0]):
        roll_hi = hs.rolling(en).max().shift(1).to_numpy()
        roll_lo = ls.rolling(xn).min().shift(1).to_numpy()
        entry = c > roll_hi
        exit_ = c < roll_lo
        yield (f"donchian in{en}/out{xn} stop{stop}", (entry, exit_, stop, 0))


def fam_ema(df):
    c = df["c"].to_numpy()
    s = pd.Series(c)
    for fast, slow, stop in itertools.product([9, 21], [50, 100, 200], [3.0, 6.0]):
        ef = s.ewm(span=fast, adjust=False).mean()
        es = s.ewm(span=slow, adjust=False).mean()
        up = (ef > es).to_numpy()
        prev = np.concatenate([[False], up[:-1]])
        entry = up & ~prev
        exit_ = ~up
        yield (f"ema {fast}/{slow} stop{stop}", (entry, exit_, stop, 0))


def fam_rsi(df):
    c = df["c"].to_numpy()
    rsi = _rsi(c, 14)
    for ent, xit, stop, mh in itertools.product([25, 30, 35], [50, 55, 60], [2.0, 4.0], [48, 96]):
        entry = rsi < ent
        exit_ = rsi > xit
        yield (f"rsi<{ent}>x{xit} stop{stop} hold{mh}", (entry, exit_, stop, mh))


FAMILIES = {"zscore": fam_zscore, "donchian": fam_donchian,
            "ema": fam_ema, "rsi": fam_rsi}


def _slice(df, sig, lo, hi):
    e, x, stop, mh = sig
    sub = df.iloc[lo:hi]
    return simulate(sub["o"].to_numpy(), sub["h"].to_numpy(), sub["l"].to_numpy(),
                    sub["c"].to_numpy(), sub["ts"].to_numpy(),
                    e[lo:hi], x[lo:hi], stop, mh)


async def main():
    print(f"\ncosts: {FEE*1e4:.0f}bps fee + {SLIP*1e4:.0f}bps slip per side · "
          f"train {SPLIT:.0%} · need ≥{MIN_TRAIN_TRADES} train & ≥{MIN_TEST_TRADES} test trades\n")
    fetched = await asyncio.gather(*[
        adapters.price.fetch_history("SOL/USDT", timeframe=tf, total=n)
        for tf, n in TIMEFRAMES.items()
    ], return_exceptions=True)

    winners = []
    for (tf, _), hist in zip(TIMEFRAMES.items(), fetched):
        if isinstance(hist, Exception):
            print(f"{tf}: fetch failed {hist}")
            continue
        cs = hist["candles"]
        df = pd.DataFrame(cs, columns=["ts", "o", "h", "l", "c", "v"])
        cut = int(len(df) * SPLIT)
        print(f"── {tf}: {len(df)} bars ({(cs[-1][0]-cs[0][0])/86400000:.0f} days), "
              f"train {cut} / test {len(df)-cut}")

        for fam_name, fam in FAMILIES.items():
            best = None  # (train_sharpe, label, train_summary, test_summary)
            for label, sig in fam(df):
                tr = metrics.summary(_slice(df, sig, 0, cut))
                if tr.get("n", 0) < MIN_TRAIN_TRADES:
                    continue
                te = metrics.summary(_slice(df, sig, cut, len(df)))
                if te.get("n", 0) < MIN_TEST_TRADES:
                    continue
                key = tr["sharpe"]
                if best is None or key > best[0]:
                    best = (key, label, tr, te)
            if best is None:
                print(f"    {fam_name:<9} — no config met the trade-count floor")
                continue
            _, label, tr, te = best
            tag = ("✅EDGE" if (te["net_return"] > 0 and te["profit_factor"] > 1.2
                               and te["sharpe"] > 0.5) else "❌")
            print(f"    {fam_name:<9} {tag}  [{label}]")
            print(f"        train: net {tr['net_return']*100:+.1f}% PF {tr['profit_factor']:.2f} "
                  f"sh {tr['sharpe']:.2f} n{tr['n']} win{tr['win_rate']*100:.0f}%")
            print(f"        TEST : net {te['net_return']*100:+.1f}% PF {te['profit_factor']:.2f} "
                  f"sh {te['sharpe']:.2f} n{te['n']} win{te['win_rate']*100:.0f}%")
            if tag.startswith("✅"):
                winners.append((tf, fam_name, label, te))
        print()

    print("=" * 64)
    if winners:
        print(f"{len(winners)} config(s) cleared costs OUT-OF-SAMPLE:")
        for tf, fam, label, te in winners:
            print(f"  {tf} {fam}: {label} → test net {te['net_return']*100:+.1f}%, "
                  f"PF {te['profit_factor']:.2f}, {te['n']} trades")
    else:
        print("No strategy/timeframe/param combo cleared costs out-of-sample with a")
        print("trustworthy trade count. The honest answer: no edge found here.")


asyncio.run(main())
