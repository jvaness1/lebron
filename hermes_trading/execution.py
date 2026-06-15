"""Coinbase spot execution layer for the long-only strategy.

SAFE BY DESIGN — every default is the cautious one:
  * DRY-RUN by default: computes and logs the exact orders, places NOTHING.
  * Live orders require BOTH live=True AND real API keys present — otherwise it
    refuses and stays dry-run.
  * LONG-ONLY spot: it never shorts, never uses margin.
  * Never sells more than you actually hold; never buys with money you don't have.
  * Hard caps: max USD per order and max total USD deployed — even a bug can't
    exceed them.
  * Reads your REAL account every run and reconciles to the target — it never
    assumes its own state (so a restart/desync can't double-trade).
  * Trade-only: it never calls withdraw/transfer. Use trade-only API keys anyway.

This module is NOT auto-run by the paper bot. It's invoked explicitly
(`python -m hermes_trading.execute`), so the live paper deployment is untouched.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


def _load_env_file() -> None:
    """Load COINBASE_* secrets from a gitignored .env (so keys never live in the
    shell history or this chat). Only sets vars that aren't already in the env."""
    envp = Path(__file__).resolve().parent.parent / ".env"
    if not envp.exists():
        return
    for line in envp.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v.replace("\\n", "\n")   # restore PEM newlines


# --------------------------------------------------------------------------- #
# Order model + pure reconciliation (the money-critical logic — fully tested)
# --------------------------------------------------------------------------- #
@dataclass
class Order:
    base: str            # e.g. "BTC"
    side: str            # "buy" | "sell"
    usd: float           # notional in USD


def reconcile(target_weights: Dict[str, float], holdings_usd: Dict[str, float],
              cash_usd: float, *, min_order_usd: float, max_order_usd: float,
              max_total_usd: float) -> List[Order]:
    """Pure: given desired weights, current per-coin USD holdings, and free cash,
    return the orders to move toward target. SELLS first (free cash), then BUYS
    within available cash and the caps. Long-only (negative/short targets ignored).

    Invariants enforced here (defensively):
      - never sell more than currently held
      - never buy more than available cash allows
      - total deployed (held + new buys) never exceeds max_total_usd
      - each order clamped to max_order_usd; orders below min_order_usd skipped
    """
    held_total = sum(max(0.0, v) for v in holdings_usd.values())
    equity = max(0.0, cash_usd) + held_total
    # Deployable capital is capped; never target more than the cap allows.
    deploy_cap = min(equity, max_total_usd)
    coins = set(holdings_usd) | {c for c, w in target_weights.items() if w > 0}

    target_usd = {c: max(0.0, target_weights.get(c, 0.0)) * deploy_cap for c in coins}

    orders: List[Order] = []

    # 1) SELLS — reduce/exit positions that are above target.
    available = max(0.0, cash_usd)
    for c in sorted(coins):
        cur = max(0.0, holdings_usd.get(c, 0.0))
        delta = target_usd.get(c, 0.0) - cur
        if delta < -min_order_usd:
            amt = min(cur, -delta)                 # never sell more than held
            amt = min(amt, max_order_usd)          # per-order cap
            if amt >= min_order_usd:
                orders.append(Order(c, "sell", round(amt, 2)))
                available += amt                   # proceeds free up cash

    # 2) BUYS — increase/enter, limited by available cash and the total cap.
    deployed = held_total - sum(o.usd for o in orders if o.side == "sell")
    for c in sorted(coins):
        cur = max(0.0, holdings_usd.get(c, 0.0))
        # account for a sell on this coin (shouldn't co-occur, but be safe)
        sold = sum(o.usd for o in orders if o.base == c and o.side == "sell")
        cur_after = max(0.0, cur - sold)
        delta = target_usd.get(c, 0.0) - cur_after
        if delta > min_order_usd:
            amt = min(delta, available, max_order_usd, max(0.0, max_total_usd - deployed))
            if amt >= min_order_usd:
                orders.append(Order(c, "buy", round(amt, 2)))
                available -= amt
                deployed += amt
    return orders


# --------------------------------------------------------------------------- #
# Coinbase broker — reads the real account, places (or simulates) orders
# --------------------------------------------------------------------------- #
class CoinbaseBroker:
    def __init__(self, *, quote: str = "USDC", live: bool = False,
                 max_order_usd: float = 50.0, max_total_usd: float = 200.0,
                 min_order_usd: float = 1.0):
        self.quote = quote
        self.max_order_usd = float(max_order_usd)
        self.max_total_usd = float(max_total_usd)
        self.min_order_usd = float(min_order_usd)
        _load_env_file()
        key, secret = os.getenv("COINBASE_API_KEY"), os.getenv("COINBASE_API_SECRET")
        if secret:
            secret = secret.replace("\\n", "\n")   # CDP EC keys: restore real newlines
        self.has_keys = bool(key and secret)
        # LIVE only if explicitly asked AND keys exist — otherwise force dry-run.
        self.live = bool(live and self.has_keys)
        self._ex = None
        if self.has_keys:
            import ccxt
            self._ex = ccxt.coinbase({"apiKey": key, "secret": secret, "enableRateLimit": True})

    def mode(self) -> str:
        if self.live:
            return "LIVE (placing real orders)"
        return "DRY-RUN (no keys)" if not self.has_keys else "DRY-RUN (live disabled)"

    def account(self) -> tuple:
        """Return (holdings_usd: {base: usd}, cash_usd, prices: {base: px}).
        With no keys, returns an empty/all-cash hypothetical (for offline dry-run)."""
        if not self.has_keys:
            return {}, float(os.getenv("DRYRUN_CASH_USD", "100")), {}
        bal = self._ex.fetch_balance()
        cash = float(bal.get(self.quote, {}).get("free", 0.0) or 0.0)
        holdings_usd, prices = {}, {}
        for base, amt in (bal.get("total") or {}).items():
            if base == self.quote or not amt:
                continue
            sym = f"{base}/{self.quote}"
            try:
                px = float(self._ex.fetch_ticker(sym)["last"])
            except Exception:
                continue
            prices[base] = px
            holdings_usd[base] = float(amt) * px
        return holdings_usd, cash, prices

    def execute(self, orders: List[Order], prices: Dict[str, float]) -> List[dict]:
        """Place (live) or log (dry-run) the orders. Returns receipts."""
        receipts = []
        for o in orders:
            # final hard guards (belt-and-suspenders)
            if o.usd < self.min_order_usd or o.usd > self.max_order_usd:
                receipts.append({"order": o, "status": "skipped_cap"})
                continue
            if not self.live:
                receipts.append({"order": o, "status": "dry_run"})
                continue
            try:
                sym = f"{o.base}/{self.quote}"
                px = prices.get(o.base) or float(self._ex.fetch_ticker(sym)["last"])
                amount = o.usd / px
                # market order; Coinbase handles precision. Long-only buy/sell only.
                res = self._ex.create_order(sym, "market", o.side, amount)
                receipts.append({"order": o, "status": "placed", "id": res.get("id")})
            except Exception as exc:  # noqa: BLE001 — never let one order crash the run
                receipts.append({"order": o, "status": "error", "error": str(exc)})
        return receipts
