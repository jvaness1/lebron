"""Execute (or dry-run) one rebalance on Coinbase for the live long-only strategy.

  python -m hermes_trading.execute                 # DRY-RUN: print the exact orders
  python -m hermes_trading.execute --max-total 200 # cap total deployed at $200
  python -m hermes_trading.execute --live          # PLACE real orders (needs keys)

Signal data comes from EXCHANGE_ID (KuCoin daily closes — prices ~identical across
venues). The ACCOUNT and order placement are Coinbase. Default is dry-run; --live
only places orders if COINBASE_API_KEY/SECRET are set (trade-only keys).
"""
from __future__ import annotations

import argparse
import asyncio

from rich import print as rprint

from .loop import load_strategy
from .portfolio import _fetch_universe_closes, momentum_multi, momentum, longonly_trend_weights
from .execution import CoinbaseBroker, reconcile


async def target_weights_now(strategy: dict) -> dict:
    """Compute the strategy's target weights right now, keyed by BASE coin."""
    e = strategy.get("entry", {})
    universe = strategy["universe"]
    lbs = e.get("momentum_lookbacks")
    lookback = int(e.get("lookback_days", 30))
    skip = int(e.get("skip_days", 0))
    k = int(e.get("top_k", 5))
    size = float(strategy.get("position_size_r", 1.0))
    ma_days = int(e.get("trend_ma_days", 100))
    closes, _ = await _fetch_universe_closes(universe, max(lbs) if lbs else lookback, skip)
    mom = momentum_multi(closes, lbs, skip) if lbs else momentum(closes, lookback, skip)
    w = longonly_trend_weights(mom, closes, k, size, ma_days)   # {sym: weight}
    return {sym.split("/")[0]: wt for sym, wt in w.items()}     # {base: weight}


def main() -> None:
    ap = argparse.ArgumentParser(prog="hermes_trading.execute")
    ap.add_argument("--live", action="store_true", help="place REAL orders (needs Coinbase keys)")
    ap.add_argument("--quote", default="USDC")   # Coinbase holds trading cash as USDC
    ap.add_argument("--max-order", type=float, default=50.0, help="max USD per order")
    ap.add_argument("--max-total", type=float, default=200.0, help="max USD deployed total")
    ap.add_argument("--min-order", type=float, default=1.0, help="min USD per order")
    ap.add_argument("--cash-buffer", type=float, default=0.02,
                    help="fraction of free cash to leave unspent for fees/spread (default 2%%)")
    args = ap.parse_args()

    strategy = load_strategy()
    broker = CoinbaseBroker(quote=args.quote, live=args.live, max_order_usd=args.max_order,
                            max_total_usd=args.max_total, min_order_usd=args.min_order)

    rprint(f"[bold]Coinbase execution[/] · mode: [bold]{broker.mode()}[/] · "
           f"caps: ${args.max_order}/order, ${args.max_total} total")
    if args.live and not broker.has_keys:
        rprint("[red]--live requested but COINBASE_API_KEY/SECRET not set — staying DRY-RUN.[/]")

    target = asyncio.run(target_weights_now(strategy))
    rprint(f"[cyan]target book[/] ({len(target)} coins): "
           + ", ".join(f"{c} {w*100:.0f}%" for c, w in sorted(target.items())))

    holdings, cash, prices = broker.account()
    rprint(f"[cyan]account[/] · cash ${cash:.2f} · holdings: "
           + (", ".join(f"{c} ${v:.0f}" for c, v in sorted(holdings.items())) or "none"))

    orders = reconcile(target, holdings, cash, min_order_usd=args.min_order,
                       max_order_usd=args.max_order, max_total_usd=args.max_total,
                       cash_buffer_frac=args.cash_buffer)
    if not orders:
        rprint("[green]already aligned — no orders needed.[/]")
        return
    rprint("[bold]orders:[/]")
    for o in orders:
        rprint(f"  {o.side.upper():<4} {o.base:<6} ${o.usd:.2f}")

    receipts = broker.execute(orders, prices)
    placed = sum(1 for r in receipts if r["status"] == "placed")
    for r in receipts:
        o = r["order"]
        line = f"  {o.side.upper():<4} {o.base:<6} ${o.usd:.2f}  → {r['status']}"
        if r.get("error"):
            line += f": {r['error'][:120]}"
        rprint(line)
    if broker.live:
        rprint(f"[green]{placed}/{len(orders)} orders placed on Coinbase.[/]" if placed
               else "[red]0 placed — see errors above.[/]")
    else:
        rprint("[dim]No real orders placed (dry-run). Re-run with --live + keys to trade.[/]")


if __name__ == "__main__":
    main()
