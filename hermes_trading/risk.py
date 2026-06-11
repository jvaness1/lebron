"""Risk controls — the kill switch the bot was missing.

The engine's circuit breaker only trips on DATA-fetch failures. This module trips
on LOSING MONEY: it gates new entries (it never force-closes an open position) when

  - open drawdown breaches the budget,
  - the realised loss SO FAR TODAY (UTC) breaches a daily limit, or
  - too many losses have stacked up in a row.

Pure functions over the closed-trade list (net `return_pct`). `evaluate` returns a
verdict the engine consults before opening; it is deliberately advisory about
ENTRIES only, so a halt can never strand an open position with no manager.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List, Mapping, Optional


def _closed(trades) -> List[Mapping]:
    return [t for t in trades if t.get("status") == "closed"]


def equity_curve(trades) -> List[float]:
    eq = [1.0]
    for t in _closed(trades):
        eq.append(eq[-1] * (1.0 + float(t.get("return_pct", 0.0))))
    return eq


def current_drawdown(trades) -> float:
    eq = equity_curve(trades)
    peak = eq[0]
    worst = 0.0
    for v in eq:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def realised_pnl_today(trades, now: Optional[float] = None) -> float:
    """Sum of net returns for trades closed on the current UTC calendar day."""
    now = now if now is not None else time.time()
    today = datetime.fromtimestamp(now, tz=timezone.utc).date()
    total = 0.0
    for t in _closed(trades):
        ts = t.get("exit_ts")
        if ts is None:
            continue
        if datetime.fromtimestamp(float(ts), tz=timezone.utc).date() == today:
            total += float(t.get("return_pct", 0.0))
    return total


def consecutive_losses(trades) -> int:
    streak = 0
    for t in reversed(_closed(trades)):
        if float(t.get("return_pct", 0.0)) <= 0:
            streak += 1
        else:
            break
    return streak


def evaluate(risk_cfg: Mapping, trades, now: Optional[float] = None) -> dict:
    """Return {allow_entry: bool, reason: str|None, ...diagnostics}.

    An empty/absent risk_cfg means "no limits configured" → always allow.
    """
    if not risk_cfg:
        return {"allow_entry": True, "reason": None}

    dd = current_drawdown(trades)
    pnl_today = realised_pnl_today(trades, now)
    streak = consecutive_losses(trades)

    halt_dd = risk_cfg.get("halt_on_drawdown")
    daily_limit = risk_cfg.get("daily_loss_limit_pct")
    max_streak = risk_cfg.get("max_consecutive_losses")

    reason = None
    if halt_dd is not None and dd >= float(halt_dd):
        reason = f"drawdown {dd*100:.2f}% ≥ halt_on_drawdown {float(halt_dd)*100:.2f}%"
    elif daily_limit is not None and pnl_today <= -abs(float(daily_limit)):
        reason = (f"today's P&L {pnl_today*100:.2f}% ≤ -daily_loss_limit "
                  f"{abs(float(daily_limit))*100:.2f}%")
    elif max_streak is not None and streak >= int(max_streak):
        reason = f"{streak} consecutive losses ≥ max_consecutive_losses {int(max_streak)}"

    return {
        "allow_entry": reason is None,
        "reason": reason,
        "drawdown": dd,
        "pnl_today": pnl_today,
        "consecutive_losses": streak,
    }
