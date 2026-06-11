"""Score a set of closed paper trades against the goal.

score(trades, goal) -> float in [-1.0, +1.0]

Composite of three components, each clamped to [-1, +1]:
  - return:   realised cumulative return vs target_return_30d
  - drawdown: headroom left under max_drawdown (positive = under budget)
  - sharpe:   per-trade Sharpe vs min_sharpe

Below goal["failure_below"] realised return, the score is floored steeply
negative so a blown-up strategy can never look "mediocre".
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _max_drawdown(equity: Sequence[float]) -> float:
    """Largest peak-to-trough fractional drop over an equity curve."""
    peak = equity[0]
    worst = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def _sharpe(returns: Sequence[float]) -> float:
    """Per-trade Sharpe, scaled by sqrt(n). Zero if undefined."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(n)


def realised_stats(trades: Iterable[Mapping]) -> dict:
    """Return realised cumulative return, max drawdown and Sharpe for closed trades."""
    closed = [t for t in trades if t.get("status") == "closed"]
    returns = [float(t["return_pct"]) for t in closed]
    if not returns:
        return {"n": 0, "realised_return": 0.0, "drawdown": 0.0, "sharpe": 0.0}

    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1.0 + r))

    return {
        "n": len(returns),
        "realised_return": equity[-1] - 1.0,
        "drawdown": _max_drawdown(equity),
        "sharpe": _sharpe(returns),
    }


def score(trades: Iterable[Mapping], goal: Mapping) -> float:
    stats = realised_stats(trades)
    if stats["n"] == 0:
        return 0.0

    realised = stats["realised_return"]
    failure_below = float(goal.get("failure_below", -0.04))
    if realised <= failure_below:
        # Steep floor: anything at/below the failure line is decisively bad.
        return _clamp(-1.0 + (realised - failure_below))

    target = float(goal["target_return_30d"])
    max_dd = float(goal["max_drawdown"])
    min_sharpe = float(goal["min_sharpe"])

    ret_c = _clamp(realised / target) if target else 0.0
    dd_c = _clamp((max_dd - stats["drawdown"]) / max_dd) if max_dd else 0.0
    sh_c = _clamp((stats["sharpe"] - min_sharpe) / min_sharpe) if min_sharpe else 0.0

    composite = 0.5 * ret_c + 0.25 * dd_c + 0.25 * sh_c
    return _clamp(composite)
