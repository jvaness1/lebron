"""Performance metrics for a set of closed paper trades.

Everything here works off the NET `return_pct` recorded on each trade (i.e. after
fees + slippage — see PaperEngine._close). `gross_return_pct` / `cost_pct` are
also read when present so we can report how much edge the costs ate.

These numbers are only as honest as the cost model that produced them; a backtest
with zero fees/slippage will look great and trade like a loss in production.
"""
from __future__ import annotations

import math
from typing import Iterable, List, Mapping, Optional


def _closed(trades: Iterable[Mapping]) -> List[Mapping]:
    return [t for t in trades if t.get("status") == "closed"]


def equity_curve(returns: List[float]) -> List[float]:
    """Compounded equity starting at 1.0."""
    eq = [1.0]
    for r in returns:
        eq.append(eq[-1] * (1.0 + r))
    return eq


def max_drawdown(equity: List[float]) -> float:
    peak = equity[0] if equity else 1.0
    worst = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def _sharpe(returns: List[float]) -> float:
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    return (mean / std) * math.sqrt(n) if std > 0 else 0.0


def _sortino(returns: List[float]) -> float:
    """Like Sharpe but penalises only downside deviation."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    downside = [min(r, 0.0) ** 2 for r in returns]
    dd = math.sqrt(sum(downside) / (n - 1))
    return (mean / dd) * math.sqrt(n) if dd > 0 else 0.0


def _time_in_market(closed: List[Mapping]) -> Optional[float]:
    """Fraction of the wall-clock span (first entry → last exit) spent holding."""
    spans = [
        (float(t["exit_ts"]) - float(t["entry_ts"]))
        for t in closed
        if t.get("entry_ts") is not None and t.get("exit_ts") is not None
    ]
    if not spans:
        return None
    entries = [float(t["entry_ts"]) for t in closed if t.get("entry_ts") is not None]
    exits = [float(t["exit_ts"]) for t in closed if t.get("exit_ts") is not None]
    total = max(exits) - min(entries)
    return (sum(spans) / total) if total > 0 else None


def summary(trades: Iterable[Mapping]) -> dict:
    """A full performance dict for the closed trades. Safe on an empty set."""
    closed = _closed(trades)
    rets = [float(t.get("return_pct", 0.0)) for t in closed]
    n = len(rets)
    if n == 0:
        return {"n": 0}

    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    eq = equity_curve(rets)
    gross_vals = [float(t["gross_return_pct"]) for t in closed if "gross_return_pct" in t]
    cost_vals = [float(t["cost_pct"]) for t in closed if "cost_pct" in t]
    gross_sum = sum(losses) + sum(wins)  # == sum(rets), net cumulative (additive view)

    return {
        "n": n,
        "net_return": eq[-1] - 1.0,            # compounded
        "net_return_additive": sum(rets),       # sum of per-trade returns
        "gross_return_additive": sum(gross_vals) if gross_vals else None,
        "cost_drag_total": sum(cost_vals) if cost_vals else None,
        "win_rate": len(wins) / n,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "expectancy": sum(rets) / n,
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf") if wins else 0.0,
        "max_drawdown": max_drawdown(eq),
        "sharpe": _sharpe(rets),
        "sortino": _sortino(rets),
        "time_in_market": _time_in_market(closed),
        "wins": len(wins),
        "losses": len(losses),
    }


def format_summary(s: dict, label: str = "") -> str:
    """Human-readable block for CLI output."""
    head = f"── {label} " + "─" * max(0, 40 - len(label)) if label else "─" * 42
    if s.get("n", 0) == 0:
        return f"{head}\n  no closed trades"

    def pct(x):
        return f"{x*100:+.2f}%" if isinstance(x, (int, float)) else "  --"

    def num(x, nd=2):
        if x is None:
            return "--"
        if x == float("inf"):
            return "inf"
        return f"{x:.{nd}f}"

    tim = s.get("time_in_market")
    lines = [
        head,
        f"  trades        {s['n']}  ({s['wins']}W / {s['losses']}L, win-rate {s['win_rate']*100:.1f}%)",
        f"  net return    {pct(s['net_return'])} compounded  ({pct(s['net_return_additive'])} additive)",
    ]
    if s.get("gross_return_additive") is not None:
        lines.append(
            f"  gross→net     {pct(s['gross_return_additive'])} gross, "
            f"{pct(s['cost_drag_total'])} cost drag"
        )
    lines += [
        f"  avg win/loss  {pct(s['avg_win'])} / {pct(s['avg_loss'])}  (expectancy {pct(s['expectancy'])})",
        f"  profit factor {num(s['profit_factor'])}   max drawdown {s['max_drawdown']*100:.2f}%",
        f"  sharpe {num(s['sharpe'])}   sortino {num(s['sortino'])}"
        + (f"   time-in-market {tim*100:.1f}%" if tim is not None else ""),
    ]
    return "\n".join(lines)
