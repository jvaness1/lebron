"""Live-vs-backtest drift tracker (BACKLOG P9).

The single most important real-money gate: does the LIVE paper strategy actually
behave like the backtest? This reads the bot's realised equity series
(state/equity_history.jsonl, written each rebalance) and compares it to what the
SAME strategy would have produced on the actual price history over the same dates.

Large, persistent divergence = the edge is decaying or the backtest was wrong =
do NOT trust it with real money. Small tracking error = the live result is real.

  EXCHANGE_ID=kucoin python scripts/drift_tracker.py

Needs a few live rebalances logged before it can say much; until then it reports
how much live history exists and exits cleanly.
"""
import os, json, asyncio
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters
from hermes_trading.loop import load_strategy
from hermes_trading import portfolio as pf

HIST = Path(__file__).resolve().parent.parent / "state" / "equity_history.jsonl"


def _live_series():
    if not HIST.exists():
        return []
    out = []
    for line in HIST.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


async def _backtest_equity(strat, start_ms, end_ms):
    """Replay the live strategy over [start_ms, end_ms] and return its equity multiple,
    using the same engine logic (momentum + regime gate + costs)."""
    e = strat.get("entry", {})
    uni = strat["universe"]
    lb, skip, k = int(e.get("lookback_days", 30)), int(e.get("skip_days", 0)), int(e.get("top_k", 5))
    short, size = bool(e.get("allow_short", True)), float(strat.get("position_size_r", 0.3))
    rebal = int(strat.get("rebalance_days", 7))
    c = strat.get("costs", {}) or {}
    cost = (c.get("fees_bps", 10) + c.get("slippage_bps", 5)) / 1e4
    gate = bool(e.get("regime_gate", False))
    thr, bmin = float(e.get("breadth_threshold", 0.4)), int(e.get("breadth_bull_min", 6))

    need = max(lb + skip + 5, 220) + int((end_ms - start_ms) / pf.DAY_MS) + 5
    got = await asyncio.gather(*[adapters.price.fetch_history(s, timeframe="1d", total=need) for s in uni],
                               return_exceptions=True)
    series = {s: pd.Series({cd[0]: cd[4] for cd in g["candles"]})
              for s, g in zip(uni, got) if not isinstance(g, Exception)}
    panel = pd.DataFrame(series).sort_index()
    dates = [d for d in panel.index if start_ms <= d <= end_ms + pf.DAY_MS]
    if len(dates) < rebal:
        return None
    eq, prev = 1.0, {}
    for d in dates[::rebal]:
        win = panel.loc[:d]
        closes = {s: win[s].dropna().tolist() for s in win.columns}
        closes = {s: v for s, v in closes.items() if len(v) >= lb + skip + 1}
        if prev:
            now = {s: win[s].dropna().iloc[-1] for s in prev if s in win and len(win[s].dropna())}
            eq *= (1 + pf.rebalance_pnl(prev, entry_px, now))
        breadth = pf.market_breadth(closes, bmin) if gate else 1.0
        new = {} if (gate and breadth < thr) else pf.target_weights(pf.momentum(closes, lb, skip), k, short, size)
        eq *= (1 - pf.turnover(prev, new) * cost)
        entry_px = {s: win[s].dropna().iloc[-1] for s in new}
        prev = new
    return eq


async def main():
    strat = load_strategy()
    live = _live_series()
    print(f"\nLive equity points logged: {len(live)}")
    if len(live) < 3:
        print("Not enough live history yet (need ≥3 rebalances). The bot logs one per")
        print("weekly rebalance — check back after a few weeks of live paper running.")
        if live:
            print(f"Latest: equity={live[-1]['equity']:.4f} regime={live[-1].get('regime')}")
        return
    start_ms, end_ms = live[0]["ts"]*1000, live[-1]["ts"]*1000
    live_mult = live[-1]["equity"] / live[0]["equity"]
    bt_mult = await _backtest_equity(strat, int(start_ms), int(end_ms))
    print(f"Window: {(end_ms-start_ms)/pf.DAY_MS:.0f} days, {len(live)} rebalances")
    print(f"  LIVE     equity multiple: {live_mult:.4f}  ({(live_mult-1)*100:+.1f}%)")
    if bt_mult is None:
        print("  backtest: not enough overlapping price history to compare yet."); return
    drift = live_mult - bt_mult
    print(f"  BACKTEST equity multiple: {bt_mult:.4f}  ({(bt_mult-1)*100:+.1f}%)")
    print(f"  DRIFT (live - backtest):  {drift*100:+.2f} pct-points")
    tol = 0.03
    if abs(drift) <= tol:
        print(f"\n✅ Live tracks backtest within ±{tol*100:.0f}pp — execution model looks faithful.")
    else:
        print(f"\n⚠️  Live diverges from backtest by {abs(drift)*100:.1f}pp. Investigate before trusting:")
        print("   fills/slippage worse than modelled, data differences, or edge decay.")


asyncio.run(main())
