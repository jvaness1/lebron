"""Portfolio backtest: the validated 1h Donchian breakout across the liquid crypto
universe. Running it on many coins it was never tuned on is a strong out-of-sample
generalisation test. Equal-weight, each coin sized per strategy.yaml.

EXCHANGE_ID=kucoin python scripts/multi_asset_backtest.py
"""
import os
import asyncio

import ccxt
import pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters, metrics            # noqa: E402
from hermes_trading.loop import PaperEngine, load_strategy  # noqa: E402

TOP_N = 50
BARS = 6000           # ~250 days of 1h
SPLIT = 0.60
MIN_QUOTE_VOL = 2_000_000   # 24h USDT volume floor
CONCURRENCY = 6

STABLES = {"USDC", "USDT", "DAI", "TUSD", "FDUSD", "USDD", "PYUSD", "EUR", "BUSD"}


def discover_universe():
    cx = ccxt.kucoin({"enableRateLimit": True})
    cx.load_markets()
    tickers = cx.fetch_tickers()
    rows = []
    for sym, t in tickers.items():
        if not sym.endswith("/USDT"):
            continue
        base = sym.split("/")[0]
        if base in STABLES:
            continue
        if any(tag in base for tag in ("3L", "3S", "UP", "DOWN")):  # leveraged tokens
            continue
        m = cx.markets.get(sym, {})
        if not m.get("spot", True) or not m.get("active", True):
            continue
        qv = t.get("quoteVolume") or 0
        if qv >= MIN_QUOTE_VOL:
            rows.append((sym, qv))
    rows.sort(key=lambda r: r[1], reverse=True)
    return [s for s, _ in rows[:TOP_N]]


async def backtest_one(sem, strat, sym):
    async with sem:
        try:
            hist = await adapters.price.fetch_history(sym, timeframe="1h", total=BARS)
        except Exception as e:  # noqa: BLE001
            return (sym, None, f"{type(e).__name__}")
    candles = hist["candles"]
    if len(candles) < 500:
        return (sym, None, "thin history")
    cut = int(len(candles) * SPLIT)
    eng = PaperEngine(strat, sym, risk_cfg=None)
    oos = []
    for i, c in enumerate(candles):
        t = eng.on_bar(c[4], c[0] / 1000.0, bar=c)
        if t and i >= cut:          # count only out-of-sample trades
            oos.append(t)
    span_days = (candles[-1][0] - candles[cut][0]) / 86400000
    return (sym, {"summary": metrics.summary(oos), "trades": oos,
                  "span_days": span_days}, None)


async def main():
    strat = load_strategy()
    if strat.get("entry", {}).get("indicator") != "donchian":
        print("strategy.yaml is not donchian — aborting"); return
    print(f"\nDonchian {strat['entry']['breakout_lookback']}/{strat['exit']['donchian_exit']} "
          f"stop{strat['stop_loss_pct']} size{strat['position_size_r']} on 1h · "
          f"costs {strat['costs']['fees_bps']}+{strat['costs']['slippage_bps']}bps/side")
    print("discovering liquid universe…")
    universe = discover_universe()
    print(f"testing {len(universe)} pairs (top {TOP_N} by 24h volume, ≥${MIN_QUOTE_VOL/1e6:.0f}M)\n")

    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*[backtest_one(sem, strat, s) for s in universe])

    ok = [(s, r) for s, r, err in results if r is not None]
    failed = [(s, err) for s, r, err in results if r is None]

    rows = []
    pooled = []
    span_total = 0.0
    for sym, r in ok:
        s = r["summary"]
        if s.get("n", 0) == 0:
            rows.append((sym, 0, 0.0, 0.0))
            continue
        rows.append((sym, s["n"], s["net_return"], s["profit_factor"]))
        pooled.extend(r["trades"])
        span_total = max(span_total, r["span_days"])

    rows.sort(key=lambda x: x[2], reverse=True)
    print(f"{'symbol':<14}{'n':>4}{'OOS net%':>10}{'PF':>7}")
    print("-" * 35)
    for sym, n, net, pf in rows[:15]:
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"{sym:<14}{n:>4}{net*100:>9.1f}%{pf_s:>7}")
    print("   …")
    for sym, n, net, pf in rows[-5:]:
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"{sym:<14}{n:>4}{net*100:>9.1f}%{pf_s:>7}")

    traded = [r for r in rows if r[1] > 0]
    pos = [r for r in traded if r[2] > 0]
    nets = sorted(r[2] for r in traded)
    median = nets[len(nets) // 2] if nets else 0.0
    ew_net = sum(r[2] for r in traded) / len(traded) if traded else 0.0
    pooled_summary = metrics.summary(pooled)
    months = span_total / 30.0 if span_total else 1

    print("\n" + "=" * 56)
    print(f"GENERALISATION (out-of-sample, ~{span_total:.0f} days):")
    print(f"  pairs traded:            {len(traded)}/{len(ok)}")
    print(f"  pairs PROFITABLE OOS:    {len(pos)}/{len(traded)} "
          f"({len(pos)/len(traded)*100:.0f}%)")
    print(f"  median pair net:         {median*100:+.1f}%")
    print(f"  equal-weight portfolio:  {ew_net*100:+.1f}%  (avg across all pairs)")
    print(f"\nPOOLED TRADES (all pairs combined):")
    print(f"  total OOS trades:        {pooled_summary['n']}  "
          f"(~{pooled_summary['n']/months:.0f}/month across the book)")
    print(f"  win rate:                {pooled_summary['win_rate']*100:.0f}%")
    print(f"  profit factor:           {pooled_summary['profit_factor']:.2f}")
    print(f"  expectancy/trade:        {pooled_summary['expectancy']*100:+.2f}%")
    if failed:
        print(f"\n  ({len(failed)} pairs skipped: "
              f"{', '.join(s for s,_ in failed[:5])}{'…' if len(failed)>5 else ''})")

    edge = len(pos) / len(traded) if traded else 0
    print("\n" + ("✅ Edge GENERALISES across the universe — multi-asset live is justified."
                  if edge >= 0.6 and pooled_summary.get("expectancy", 0) > 0 else
                  "⚠️  Edge does NOT generalise broadly — reconsider before going multi-asset."))


asyncio.run(main())
