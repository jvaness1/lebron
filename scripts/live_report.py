"""Live Coinbase position report — read-only.

Reads the REAL Coinbase account (holdings, cash, prices) and computes per-coin
and total P&L against the actual buy fills (cost basis incl. fees). Places no
orders, never writes account state. Coinbase-only — the Railway/paper bot is
irrelevant here.

Usage:
    EXCHANGE_ID=kucoin python scripts/live_report.py            # print report
    EXCHANGE_ID=kucoin python scripts/live_report.py --notify   # + macOS notification
    EXCHANGE_ID=kucoin python scripts/live_report.py --log      # + append to state/live_report.log
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from hermes_trading.execution import CoinbaseBroker

DEPOSIT_USD = 100.0          # original funding (USDC)
STATE = Path(__file__).resolve().parent.parent / "state"


def cost_basis(ex, sym: str) -> tuple[float, float]:
    """Net invested USD (buys cost+fee − sells proceeds−fee) and net base amount
    for a symbol, from real fills. Returns (net_usd_in, net_amount)."""
    try:
        trades = ex.fetch_my_trades(sym, limit=100)
    except Exception:
        return 0.0, 0.0
    usd_in = amt = 0.0
    for t in trades:
        cost = float(t.get("cost") or 0.0)
        fee = float((t.get("fee") or {}).get("cost") or 0.0)
        a = float(t.get("amount") or 0.0)
        if t.get("side") == "buy":
            usd_in += cost + fee
            amt += a
        else:                                  # sell — proceeds reduce basis
            usd_in -= (cost - fee)
            amt -= a
    return usd_in, amt


def build_report() -> tuple[str, str]:
    """Return (detailed_text, one_line_summary)."""
    b = CoinbaseBroker(quote="USDC")
    if not b.has_keys:
        return "No Coinbase keys found in .env — cannot read live account.", "Hermes: no keys"
    holdings, cash, prices = b.account()

    rows, total_val, total_cost = [], 0.0, 0.0
    for c in sorted(holdings, key=lambda k: -holdings[k]):
        val = holdings[c]
        invested, _ = cost_basis(b._ex, f"{c}/{b.quote}")
        pnl = val - invested if invested else 0.0
        pct = (pnl / invested * 100) if invested else 0.0
        total_val += val
        total_cost += invested
        rows.append((c, val, invested, pnl, pct, prices.get(c, 0.0)))

    equity = total_val + cash
    eq_pnl = equity - DEPOSIT_USD
    eq_pct = eq_pnl / DEPOSIT_USD * 100

    ts = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        f"LIVE COINBASE POSITIONS · {ts}",
        f"  equity ${equity:.2f}  ({eq_pnl:+.2f} / {eq_pct:+.2f}% vs ${DEPOSIT_USD:.0f} deposited)   cash ${cash:.2f}",
        "  " + "-" * 64,
        f"  {'coin':<6} {'value':>8} {'cost':>8} {'P&L $':>8} {'P&L %':>8}   price",
    ]
    for c, val, inv, pnl, pct, px in rows:
        lines.append(f"  {c:<6} {val:>8.2f} {inv:>8.2f} {pnl:>+8.2f} {pct:>+7.2f}%   {px:g}")

    # one-line summary for notifications (sorted by % so movers lead)
    movers = sorted(rows, key=lambda r: -r[4])
    tops = "  ".join(f"{c} {pct:+.1f}%" for c, _, _, _, pct, _ in movers)
    summary = f"${equity:.2f} ({eq_pct:+.2f}%)  |  {tops}"
    return "\n".join(lines), summary


def main() -> None:
    detail, summary = build_report()
    print(detail)
    if "--log" in sys.argv:
        STATE.mkdir(exist_ok=True)
        with open(STATE / "live_report.log", "a") as f:
            f.write(detail + "\n\n")
    if "--notify" in sys.argv:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{summary}" with title "Hermes live positions"'],
            check=False,
        )


if __name__ == "__main__":
    main()
