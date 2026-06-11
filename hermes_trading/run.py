"""Entrypoint.

  python -m hermes_trading.run                 # live paper loop (1-min cadence)
  python -m hermes_trading.run --asset ETH/USDT
  python -m hermes_trading.run --demo 500       # replay 500 historical 1m bars, then exit
  python -m hermes_trading.run --once           # one live tick, then exit

Asset comes from state/goal.yaml unless overridden with --asset.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import yaml
from rich import print as rprint

from . import loop as engine_loop

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
GOAL_FILE = STATE_DIR / "goal.yaml"
SEED_DIR = Path("/app/seed_state")  # baked into the Docker image; absent locally


def _bootstrap_state() -> None:
    """Reconcile the state dir on boot.

    Clean separation of CODE vs DATA on the persistent volume:
      - goal.yaml / strategy.yaml are CODE — deploy-controlled. They are
        OVERWRITTEN from the image's /app/seed_state on every boot, so a change
        Hermes makes locally and pushes with `railway up` actually reaches the
        worker. (The worker itself never writes these.)
      - trades.jsonl / hypotheses.jsonl / history/ are DATA — produced at
        runtime. They are created if missing and NEVER overwritten, so they
        persist across redeploys.

    No-op for the CODE files locally (SEED_DIR only exists in the image).
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "history").mkdir(exist_ok=True)
    if SEED_DIR.exists():
        for name in ("goal.yaml", "strategy.yaml"):
            src = SEED_DIR / name
            if src.exists():
                (STATE_DIR / name).write_text(src.read_text())  # deploy wins
    for name in ("trades.jsonl", "hypotheses.jsonl"):
        (STATE_DIR / name).touch(exist_ok=True)  # preserve runtime data


def _goal() -> dict:
    return yaml.safe_load(GOAL_FILE.read_text())


def main() -> None:
    _bootstrap_state()
    goal = _goal()
    ap = argparse.ArgumentParser(prog="hermes_trading.run")
    ap.add_argument("--asset", default=goal["asset"], help="ccxt ticker, e.g. SOL/USDT")
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between live ticks")
    ap.add_argument("--once", action="store_true", help="run a single live tick then exit")
    ap.add_argument("--demo", type=int, metavar="N",
                    help="replay N historical 1m candles offline, then exit")
    args = ap.parse_args()

    if args.demo:
        asyncio.run(_run_demo(args.asset, args.demo))
        return

    # Dispatch on strategy family: the cross-sectional momentum strategy is a
    # whole-universe PORTFOLIO, not a single-asset position, so it runs its own loop.
    strategy = engine_loop.load_strategy()
    if strategy.get("entry", {}).get("indicator") == "xsmom":
        from . import portfolio
        asyncio.run(portfolio.run_xsmom_live(
            strategy,
            interval=args.interval if args.interval != 60.0 else 1800.0,  # 30-min position updates
            max_ticks=1 if args.once else None,
        ))
        return

    asyncio.run(engine_loop.run_live(
        args.asset,
        interval=args.interval,
        max_ticks=1 if args.once else None,
    ))


async def _run_demo(asset: str, bars: int) -> None:
    from . import adapters, metrics

    rprint(f"[bold]demo[/] · replaying {bars} historical 1m candles for {asset}")
    strategy = engine_loop.load_strategy()
    eng = engine_loop.PaperEngine(strategy, asset, risk_cfg=_goal().get("risk"))

    price = await adapters.price.fetch(asset, timeframe="1m", limit=min(bars, 1000))
    closed = engine_loop.replay(eng, price["candles"])

    rprint(f"[green]done[/] · {len(closed)} paper trades closed · "
           f"logged to state/trades.jsonl\n")
    rprint(metrics.format_summary(metrics.summary(closed), "DEMO (net of costs)"))


if __name__ == "__main__":
    main()
