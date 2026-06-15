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
TREND_DAYS = 100             # matches strategy.yaml entry.trend_ma_days
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


def trend_report() -> tuple[str, str, bool]:
    """End-of-day trend check: for each HELD coin, compare the latest daily close
    to its 100-day SMA (the strategy's actual exit rule) and 100-day EMA. Flags any
    coin trading below its trend — the dual-momentum exit signal. Returns
    (detail, summary, any_below). Read-only."""
    b = CoinbaseBroker(quote="USDC")
    if not b.has_keys:
        return "No Coinbase keys — cannot run trend check.", "Hermes: no keys", False
    holdings, _, _ = b.account()

    rows, below = [], []
    for c in sorted(holdings):
        sym = f"{c}/{b.quote}"
        try:
            ohlcv = b._ex.fetch_ohlcv(sym, "1d", limit=TREND_DAYS + 60)
        except Exception:
            continue
        closes = [float(x[4]) for x in ohlcv if x and x[4]]
        if len(closes) < TREND_DAYS:
            rows.append((c, closes[-1] if closes else 0.0, None, None, "n/a (history)"))
            continue
        px = closes[-1]
        sma = sum(closes[-TREND_DAYS:]) / TREND_DAYS
        # EMA(span=100) over available history, recursive (adjust=False).
        k = 2 / (TREND_DAYS + 1)
        ema = closes[0]
        for v in closes[1:]:
            ema = v * k + ema * (1 - k)
        sma_ok, ema_ok = px > sma, px > ema
        verdict = "ABOVE" if (sma_ok and ema_ok) else ("BELOW" if not sma_ok else "below EMA")
        if not sma_ok:                        # strategy's real exit trigger = SMA
            below.append(c)
        rows.append((c, px, sma, ema, verdict))

    ts = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        f"EOD 100-DAY TREND CHECK · {ts}",
        "  " + "-" * 62,
        f"  {'coin':<6} {'price':>10} {'SMA100':>10} {'EMA100':>10}   trend",
    ]
    for c, px, sma, ema, verdict in rows:
        s = f"{sma:>10g}" if sma else f"{'—':>10}"
        e = f"{ema:>10g}" if ema else f"{'—':>10}"
        mark = " ⚠️" if verdict.startswith("BELOW") else ""
        lines.append(f"  {c:<6} {px:>10g} {s} {e}   {verdict}{mark}")

    if below:
        summary = "⚠️ BELOW 100d SMA: " + ", ".join(below) + " — dual-momentum exit signal"
        lines.append(f"  >>> {summary}")
    else:
        summary = f"All {len(rows)} holdings above 100d SMA — trend intact"
        lines.append(f"  {summary}")
    return "\n".join(lines), summary, below


def exit_positions(below: list[str]) -> tuple[str, str]:
    """Auto-exit: market-sell to USDC every held coin that closed below its 100d
    SMA. Long-only sell of (almost) the full position; leaves other holdings
    untouched. Reuses the tested live order path. Returns (detail, summary)."""
    from hermes_trading.execution import Order
    # generous per-order cap so a full position exits in one order; still bounded.
    b = CoinbaseBroker(quote="USDC", live=True, max_order_usd=200.0,
                       max_total_usd=200.0, min_order_usd=1.0)
    if not b.live:
        return "  exit skipped — not live (missing keys).", "exit skipped (no keys)"
    holdings, _, prices = b.account()
    # 0.5% haircut avoids 'insufficient balance' rejects from precision/rounding.
    orders = [Order(c, "sell", round(holdings[c] * 0.995, 2))
              for c in below if holdings.get(c, 0.0) >= b.min_order_usd]
    if not orders:
        return "  nothing to exit (no held value below trend).", "no exit needed"
    receipts = b.execute(orders, prices)
    lines, sold = ["  AUTO-EXIT (below 100d SMA):"], []
    for r in receipts:
        o = r["order"]
        ok = r["status"] == "placed"
        if ok:
            sold.append(o.base)
        line = f"    SELL {o.base} ${o.usd:.2f} → {r['status']}"
        if r.get("error"):
            line += f": {r['error'][:120]}"
        lines.append(line)
    summary = ("SOLD to cash: " + ", ".join(sold)) if sold else "exit attempted — see log (errors)"
    return "\n".join(lines), summary


def _notify(summary: str, title: str) -> None:
    safe = summary.replace('"', "'")
    subprocess.run(["osascript", "-e",
                    f'display notification "{safe}" with title "{title}"'], check=False)


def main() -> None:
    if "--trend" in sys.argv:
        detail, summary, below = trend_report()
        title = "Hermes EOD trend check"
        logfile = "trend_check.log"
        if "--exit" in sys.argv and below:          # auto-sell broken coins to cash
            ex_detail, ex_summary = exit_positions(below)
            detail += "\n" + ex_detail
            summary = ex_summary                      # the action is the headline
    else:
        detail, summary = build_report()
        title = "Hermes live positions"
        logfile = "live_report.log"
    print(detail)
    if "--log" in sys.argv:
        STATE.mkdir(exist_ok=True)
        with open(STATE / logfile, "a") as f:
            f.write(detail + "\n\n")
    if "--notify" in sys.argv:
        _notify(summary, title)


if __name__ == "__main__":
    main()
