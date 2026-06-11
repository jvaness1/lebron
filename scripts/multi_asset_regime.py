"""Does a regime (trend) filter make the Donchian breakout generalise across the
crypto universe? Fetch each pair once, test base vs +SMA100 vs +SMA200 trend gate
(long only when price is above the MA). Same costs, OOS only.

EXCHANGE_ID=kucoin python scripts/multi_asset_regime.py
"""
import os
import asyncio

import ccxt
import numpy as np
import pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters, metrics            # noqa: E402
from hermes_trading.loop import load_strategy            # noqa: E402

TOP_N, BARS, SPLIT, MIN_VOL, CONC = 30, 16000, 0.55, 2_000_000, 6
STABLES = {"USDC", "USDT", "DAI", "TUSD", "FDUSD", "USDD", "PYUSD", "EUR", "BUSD"}
_s = load_strategy()
LB = _s["entry"]["breakout_lookback"]
XN = _s["exit"]["donchian_exit"]
STOP = _s["stop_loss_pct"]
SIZE = _s["position_size_r"]
FEE = _s["costs"]["fees_bps"] / 1e4
SLIP = _s["costs"]["slippage_bps"] / 1e4


def discover():
    cx = ccxt.kucoin({"enableRateLimit": True}); cx.load_markets()
    rows = []
    for sym, t in cx.fetch_tickers().items():
        if not sym.endswith("/USDT"):
            continue
        base = sym.split("/")[0]
        if base in STABLES or any(x in base for x in ("3L", "3S", "UP", "DOWN")):
            continue
        if (t.get("quoteVolume") or 0) >= MIN_VOL:
            rows.append((sym, t["quoteVolume"]))
    rows.sort(key=lambda r: r[1], reverse=True)
    return [s for s, _ in rows[:TOP_N]]


def simulate(h, l, c, ts, entry, exit_, lo):
    trades, pos = [], None
    for i in range(len(c)):
        if pos is None:
            if entry[i]:
                pos = (c[i], ts[i])
        else:
            px, ents = pos
            stop_px = px * (1 - STOP / 100)
            ex = stop_px if l[i] <= stop_px else (c[i] if exit_[i] else None)
            if ex is not None:
                ei, eo = px * (1 + SLIP), ex * (1 - SLIP)
                net = ((eo - ei) / ei - 2 * FEE) * SIZE
                if i >= lo:
                    trades.append({"status": "closed", "return_pct": net, "exit_ts": ts[i]})
                pos = None
    return trades


def run_variant(df, cut, trend_ma):
    h, l, c, ts = (df["h"].to_numpy(), df["l"].to_numpy(),
                   df["c"].to_numpy(), df["ts"].to_numpy())
    roll_hi = pd.Series(h).rolling(LB).max().shift(1).to_numpy()
    roll_lo = pd.Series(l).rolling(XN).min().shift(1).to_numpy()
    entry = c > roll_hi
    if trend_ma:
        sma = pd.Series(c).rolling(trend_ma).mean().to_numpy()
        entry = entry & (c > sma)
    exit_ = c < roll_lo
    return simulate(h, l, c, ts, np.nan_to_num(entry).astype(bool),
                    np.nan_to_num(exit_).astype(bool), cut)


async def fetch_one(sem, sym):
    async with sem:
        try:
            return sym, await adapters.price.fetch_history(sym, timeframe="1h", total=BARS)
        except Exception:  # noqa: BLE001
            return sym, None


def report(name, per_asset, span_days):
    traded = [(s, x) for s, x in per_asset if x["n"] > 0]
    pos = [1 for _, x in traded if x["net"] > 0]
    pooled = []
    for _, x in traded:
        pooled.extend(x["trades"])
    ps = metrics.summary(pooled)
    ew = sum(x["net"] for _, x in traded) / len(traded) if traded else 0
    nets = sorted(x["net"] for _, x in traded)
    med = nets[len(nets) // 2] if nets else 0
    months = (span_days / 30.0) or 1
    print(f"\n  {name}")
    print(f"    profitable: {sum(pos)}/{len(traded)} ({sum(pos)/max(len(traded),1)*100:.0f}%)"
          f" · median {med*100:+.1f}% · equal-weight {ew*100:+.1f}%")
    print(f"    pooled: {ps['n']} trades (~{ps['n']/months:.0f}/mo) · "
          f"win {ps['win_rate']*100:.0f}% · PF {ps['profit_factor']:.2f} · "
          f"exp {ps['expectancy']*100:+.2f}%/trade")
    return sum(pos) / max(len(traded), 1), ps.get("expectancy", 0)


async def main():
    print(f"\nDonchian {LB}/{XN} stop{STOP} size{SIZE} · costs {FEE*1e4:.0f}+{SLIP*1e4:.0f}bps")
    print("discovering universe…")
    universe = discover()
    print(f"{len(universe)} liquid pairs · fetching…")
    sem = asyncio.Semaphore(CONC)
    fetched = await asyncio.gather(*[fetch_one(sem, s) for s in universe])

    variants = {"base (no filter)": None, "+ SMA100 trend gate": 100, "+ SMA200 trend gate": 200}
    span = 0
    collected = {k: [] for k in variants}
    for sym, hist in fetched:
        if hist is None or len(hist["candles"]) < 500:
            continue
        df = pd.DataFrame(hist["candles"], columns=["ts", "o", "h", "l", "c", "v"])
        cut = int(len(df) * SPLIT)
        span = max(span, (df["ts"].iloc[-1] - df["ts"].iloc[cut]) / 86400000)
        for vname, ma in variants.items():
            tr = run_variant(df, cut, ma)
            s = metrics.summary(tr)
            collected[vname].append((sym, {"n": s.get("n", 0),
                                           "net": s.get("net_return", 0), "trades": tr}))

    print(f"\nOUT-OF-SAMPLE (~{span:.0f} days):")
    for vname in variants:
        report(vname, collected[vname], span)


asyncio.run(main())
