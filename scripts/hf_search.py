"""High-frequency strategy search — find something that trades multiple times/day
AND survives costs out-of-sample. Same honest train→test methodology as
strategy_search.py, plus a trades/day floor and a cost-sensitivity sweep.

EXCHANGE_ID=kucoin python scripts/hf_search.py
"""
import os
import asyncio
import itertools

import numpy as np
import pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters, metrics      # noqa: E402
from hermes_trading.loop import load_strategy      # noqa: E402

TIMEFRAMES = {"1m": 20000, "5m": 20000}
SPLIT = 0.60
MIN_TRAIN_TRADES = 50
MIN_TRADES_PER_DAY = 2.0          # "multiple times a day"
_c = load_strategy().get("costs") or {}
FEE = float(_c.get("fees_bps", 10.0)) / 1e4
SLIP = float(_c.get("slippage_bps", 5.0)) / 1e4


def simulate(h, l, c, ts, entry, exit_, stop_pct, max_hold, fee=FEE, slip=SLIP):
    trades, pos = [], None
    for i in range(len(c)):
        if pos is None:
            if entry[i]:
                pos = (c[i], i, ts[i])
        else:
            px, idx, ents = pos
            stop_px = px * (1 - stop_pct / 100)
            exit_px = None
            if l[i] <= stop_px:
                exit_px = stop_px
            elif exit_[i] or (max_hold and (i - idx) >= max_hold):
                exit_px = c[i]
            if exit_px is not None:
                ei, eo = px * (1 + slip), exit_px * (1 - slip)
                trades.append({"status": "closed", "return_pct": (eo - ei) / ei - 2 * fee,
                               "entry_ts": ents, "exit_ts": ts[i]})
                pos = None
    return trades


def _rsi(c, p=14):
    d = pd.Series(c).diff()
    g = d.clip(lower=0).ewm(alpha=1 / p, adjust=False).mean()
    ls = (-d.clip(upper=0)).ewm(alpha=1 / p, adjust=False).mean()
    return (100 - 100 / (1 + g / ls.replace(0, np.nan))).fillna(50).to_numpy()


def fam_zscore(df):
    s = pd.Series(df["c"].to_numpy())
    for lb, ez, xz, stop, mh in itertools.product(
            [10, 20, 40], [1.0, 1.5, 2.0], [0.0, 0.5], [1.0, 2.0], [24, 48]):
        z = ((s - s.rolling(lb).mean()) / s.rolling(lb).std()).to_numpy()
        yield (f"zscore lb{lb} z-{ez}/+{xz} stop{stop} hold{mh}",
               (z <= -ez, z >= xz, stop, mh))


def fam_rsi(df):
    rsi = _rsi(df["c"].to_numpy())
    for ent, xit, stop, mh in itertools.product([35, 40], [50, 55], [1.0, 2.0], [24, 48]):
        yield (f"rsi<{ent}>x{xit} stop{stop} hold{mh}", (rsi < ent, rsi > xit, stop, mh))


def fam_ema(df):
    s = pd.Series(df["c"].to_numpy())
    for fast, slow, stop in itertools.product([5, 9], [20, 50], [1.0, 2.0]):
        ef, es = s.ewm(span=fast, adjust=False).mean(), s.ewm(span=slow, adjust=False).mean()
        up = (ef > es).to_numpy()
        prev = np.concatenate([[False], up[:-1]])
        yield (f"ema {fast}/{slow} stop{stop}", (up & ~prev, ~up, stop, 0))


def fam_breakout(df):
    h, l = pd.Series(df["h"].to_numpy()), pd.Series(df["l"].to_numpy())
    c = df["c"].to_numpy()
    for en, xn, stop in itertools.product([10, 20, 40], [5, 10], [1.0, 2.0]):
        rh = h.rolling(en).max().shift(1).to_numpy()
        rl = l.rolling(xn).min().shift(1).to_numpy()
        yield (f"breakout in{en}/out{xn} stop{stop}", (c > rh, c < rl, stop, 0))


FAMILIES = {"zscore": fam_zscore, "rsi": fam_rsi, "ema": fam_ema, "breakout": fam_breakout}


def _sim_slice(df, sig, lo, hi):
    e, x, stop, mh = sig
    sub = df.iloc[lo:hi]
    return simulate(sub["h"].to_numpy(), sub["l"].to_numpy(), sub["c"].to_numpy(),
                    sub["ts"].to_numpy(), e[lo:hi], x[lo:hi], stop, mh)


def _tpd(trades, df, lo, hi):
    span = (df["ts"].iloc[hi - 1] - df["ts"].iloc[lo]) / 86400000
    return len(trades) / span if span > 0 else 0


async def main():
    print(f"\ncosts {FEE*1e4:.0f}+{SLIP*1e4:.0f}bps/side · train {SPLIT:.0%} · "
          f"need ≥{MIN_TRAIN_TRADES} train trades & ≥{MIN_TRADES_PER_DAY}/day on test\n")
    fetched = await asyncio.gather(*[
        adapters.price.fetch_history("SOL/USDT", timeframe=tf, total=n)
        for tf, n in TIMEFRAMES.items()], return_exceptions=True)

    all_best = []
    for (tf, _), hist in zip(TIMEFRAMES.items(), fetched):
        if isinstance(hist, Exception):
            print(f"{tf}: fetch failed {hist}"); continue
        df = pd.DataFrame(hist["candles"], columns=["ts", "o", "h", "l", "c", "v"])
        cut = int(len(df) * SPLIT)
        days = (df["ts"].iloc[-1] - df["ts"].iloc[0]) / 86400000
        print(f"── {tf}: {len(df)} bars ({days:.0f} days), train {cut}/test {len(df)-cut}")
        for fam_name, fam in FAMILIES.items():
            best = None
            for label, sig in fam(df):
                trtr = _sim_slice(df, sig, 0, cut)
                if len(trtr) < MIN_TRAIN_TRADES:
                    continue
                tetr = _sim_slice(df, sig, cut, len(df))
                if _tpd(tetr, df, cut, len(df)) < MIN_TRADES_PER_DAY:
                    continue
                tr, te = metrics.summary(trtr), metrics.summary(tetr)
                if best is None or tr["sharpe"] > best[0]:
                    best = (tr["sharpe"], label, tr, te, _tpd(tetr, df, cut, len(df)))
            if best is None:
                print(f"    {fam_name:<9} — nothing met frequency+trade floors")
                continue
            _, label, tr, te, tpd = best
            ok = te["net_return"] > 0 and te["profit_factor"] > 1.1
            print(f"    {fam_name:<9} {'✅' if ok else '❌'} [{label}]  {tpd:.1f} trades/day")
            print(f"        train net {tr['net_return']*100:+.1f}% PF {tr['profit_factor']:.2f} | "
                  f"TEST net {te['net_return']*100:+.1f}% PF {te['profit_factor']:.2f} "
                  f"sh {te['sharpe']:.2f} n{te['n']} win{te['win_rate']*100:.0f}%")
            all_best.append((ok, tf, fam_name, label, te, tpd, df, cut))
        print()

    winners = [b for b in all_best if b[0]]
    print("=" * 64)
    if not winners:
        print("No high-frequency strategy cleared costs out-of-sample at 10+5 bps.")
        print("This is the cost wall: frequent trading + taker fees rarely survives.\n")
        # Cost sensitivity on the least-bad HF config to show the maker-order lever.
        cands = sorted(all_best, key=lambda b: b[4]["net_return"], reverse=True)
        if cands:
            _, tf, fam, label, te, tpd, df, cut = cands[0]
            sigs = dict(_collect(df))
            e, x, stop, mh = sigs[label]
            sub = df.iloc[cut:]
            h, l, c, ts = (sub["h"].to_numpy(), sub["l"].to_numpy(),
                           sub["c"].to_numpy(), sub["ts"].to_numpy())
            et, xt = e[cut:], x[cut:]
            print(f"Cost sensitivity — least-bad HF candidate "
                  f"({tf} {fam}: {label}, {tpd:.1f}/day):")
            for fb, sb in [(10, 5), (5, 2), (2, 1), (1, 0), (0, 0)]:
                s = metrics.summary(simulate(h, l, c, ts, et, xt, stop, mh,
                                             fee=fb / 1e4, slip=sb / 1e4))
                print(f"   {fb:>2}+{sb}bps/side: test net {s['net_return']*100:+7.1f}%  "
                      f"PF {s['profit_factor']:.2f}")
            print("\n→ If net only flips positive at low bps, the signal may be real but")
            print("  needs MAKER (limit) orders / a low-fee venue, not market orders.")
    else:
        print(f"{len(winners)} HF config(s) cleared costs OOS:")
        for _, tf, fam, label, te, tpd, *_ in winners:
            print(f"  {tf} {fam}: {label} → {tpd:.1f}/day, test net "
                  f"{te['net_return']*100:+.1f}%, PF {te['profit_factor']:.2f}")


def _collect(df):
    out = []
    for fam in FAMILIES.values():
        out.extend((label, sig) for label, sig in fam(df))
    return out


asyncio.run(main())
