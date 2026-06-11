"""Offline backtest — does this strategy have an edge AFTER costs?

  python -m hermes_trading.backtest                      # 5000 1m bars, 70/30 split
  python -m hermes_trading.backtest --bars 20000 --timeframe 5m
  python -m hermes_trading.backtest --asset ETH/USDT --split 0.8

Runs the SAME PaperEngine used live (so there's one decision path) over historical
candles, with the fees/slippage from strategy.yaml applied to every fill. Splits
the data into an in-sample (train) and out-of-sample (test) segment and reports
both — a strategy that only looks good in-sample is overfit, not profitable.

Writes NOTHING to state/ — it never calls append_trade. Pure read + report.
"""
from __future__ import annotations

import argparse
import asyncio
from typing import List, Optional

import yaml
from rich import print as rprint

from . import adapters, metrics
from .loop import PaperEngine, GOAL_FILE, load_strategy


def _replay_segment(strategy: dict, asset: str, candles: List[list],
                    risk_cfg: Optional[dict]) -> List[dict]:
    """Run the engine over a candle segment in memory. No persistence."""
    eng = PaperEngine(strategy, asset, risk_cfg=risk_cfg)
    closed: List[dict] = []
    for c in candles:
        trade = eng.on_bar(c[4], c[0] / 1000.0, bar=c)
        if trade:
            closed.append(trade)
    return closed


async def run(asset: str, bars: int, split: float, timeframe: str) -> dict:
    strategy = load_strategy()
    goal = yaml.safe_load(GOAL_FILE.read_text()) if GOAL_FILE.exists() else {}
    risk_cfg = goal.get("risk")

    rprint(f"[bold]backtest[/] · {asset} · {timeframe} · fetching {bars} candles…")
    hist = await adapters.price.fetch_history(asset, timeframe=timeframe, total=bars)
    candles = hist["candles"]
    n = len(candles)
    cut = int(n * split)
    train, test = candles[:cut], candles[cut:]

    costs = strategy.get("costs", {}) or {}
    rprint(f"[dim]got {n} candles from {hist['exchange']} · "
           f"costs: {costs.get('fees_bps', 0)}bps fee + "
           f"{costs.get('slippage_bps', 0)}bps slip per side[/]\n")

    results = {}
    for label, seg in (("IN-SAMPLE (train)", train), ("OUT-OF-SAMPLE (test)", test)):
        closed = _replay_segment(strategy, asset, seg, risk_cfg)
        s = metrics.summary(closed)
        results[label] = s
        rprint(metrics.format_summary(s, label))
        rprint("")

    # Plain-language verdict on the out-of-sample result — the only one that counts.
    oos = results["OUT-OF-SAMPLE (test)"]
    if oos.get("n", 0) < 30:
        rprint("[yellow]Verdict: too few out-of-sample trades to conclude anything. "
               "Pull more bars.[/]")
    elif oos["net_return"] > 0 and oos["profit_factor"] > 1.1 and oos["sharpe"] > 0.5:
        rprint("[green]Verdict: positive out-of-sample after costs. Worth deeper "
               "validation (more history, multiple regimes).[/]")
    else:
        rprint("[red]Verdict: no edge out-of-sample after costs. Do NOT risk money "
               "on this configuration.[/]")
    return results


def main() -> None:
    ap = argparse.ArgumentParser(prog="hermes_trading.backtest")
    goal = yaml.safe_load(GOAL_FILE.read_text()) if GOAL_FILE.exists() else {}
    ap.add_argument("--asset", default=goal.get("asset", "SOL/USDT"))
    ap.add_argument("--bars", type=int, default=5000, help="historical candles to fetch")
    ap.add_argument("--split", type=float, default=0.7, help="train fraction (0–1)")
    ap.add_argument("--timeframe", default="1m")
    args = ap.parse_args()
    asyncio.run(run(args.asset, args.bars, args.split, args.timeframe))


if __name__ == "__main__":
    main()
