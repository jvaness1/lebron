"""Cross-sectional momentum PORTFOLIO engine (paper).

Unlike PaperEngine (one asset, one position), this ranks a whole universe by
trailing momentum each rebalance and holds the top-K long / bottom-K short
(dollar-neutral). It made positive out-of-sample returns net of costs while the
market fell — the only strategy in the search that did (see scripts/xsmom.py).

Accounting mirrors the backtest exactly: equity updates only at rebalances, using
each held coin's return since the last rebalance, minus turnover cost. Long-short
requires perps to trade for real; in paper we simulate it. Nothing places orders.

  REBALANCE_JSON {...}  is emitted per rebalance so a remote watcher can follow it.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

from . import adapters
from .adapters import SchemaError

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
PORTFOLIO_FILE = STATE_DIR / "portfolio.json"
HEARTBEAT_FILE = STATE_DIR / "heartbeat.json"
EQUITY_HISTORY_FILE = STATE_DIR / "equity_history.jsonl"  # feed for the drift tracker
DAY_MS = 86_400_000


# --------------------------------------------------------------------------- #
# Pure ranking / weighting — unit-tested
# --------------------------------------------------------------------------- #
def momentum(closes_by_sym: Dict[str, List[float]], lookback: int, skip: int) -> Dict[str, float]:
    """Trailing return over [-(lookback+skip), -(1+skip)] per symbol. Needs history."""
    out = {}
    for sym, closes in closes_by_sym.items():
        if len(closes) < lookback + skip + 1:
            continue
        recent = closes[-(1 + skip)]
        past = closes[-(lookback + skip + 1)]
        if past > 0:
            out[sym] = recent / past - 1.0
    return out


def target_weights(mom: Dict[str, float], k: int, allow_short: bool,
                   size_total: float) -> Dict[str, float]:
    """Equal-weight top-k long (and bottom-k short if allowed), scaled by size_total.
    Dollar-neutral when shorting: each side sums to size_total."""
    if len(mom) < (2 * k if allow_short else k):
        return {}
    ranked = sorted(mom.items(), key=lambda kv: kv[1])
    longs = [s for s, _ in ranked[-k:]]
    weights = {s: size_total / k for s in longs}
    if allow_short:
        for s, _ in ranked[:k]:
            weights[s] = -size_total / k
    return weights


def rebalance_pnl(weights: Dict[str, float], entry_px: Dict[str, float],
                  now_px: Dict[str, float]) -> float:
    """Portfolio return earned by the held book since entry (pre-cost)."""
    total = 0.0
    for sym, w in weights.items():
        e, n = entry_px.get(sym), now_px.get(sym)
        if e and n and e > 0:
            total += w * (n / e - 1.0)
    return total


def turnover(old: Dict[str, float], new: Dict[str, float]) -> float:
    keys = set(old) | set(new)
    return sum(abs(new.get(k, 0.0) - old.get(k, 0.0)) for k in keys)


def drawdown_frac(peak: float, mtm: float) -> float:
    """Fractional drop of marked equity below its high-water mark (0 if at/above)."""
    return (peak - mtm) / peak if peak > 0 and mtm < peak else 0.0


def bull_score_last(closes: List[float]) -> int:
    """0..10 count of daily bullish checks on the latest bar (same set as the
    validated test_overlay.py / FPU-MAX matrix idea). Needs ~30+ bars."""
    c = pd.Series(closes, dtype=float)
    if len(c) < 30:
        return 0
    e9, e21, e50, e200 = (c.ewm(span=s, adjust=False).mean() for s in (9, 21, 50, 200))
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    lo14, hi14 = c.rolling(14).min(), c.rolling(14).max()
    sto = (c - lo14) / (hi14 - lo14) * 100
    d = c.diff()
    g = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - 100 / (1 + g / l.replace(0, np.nan))
    last = -1
    checks = [
        e9.iloc[last] > e21.iloc[last], e21.iloc[last] > e50.iloc[last],
        e50.iloc[last] > e200.iloc[last], c.iloc[last] > c.rolling(20).mean().iloc[last],
        c.iloc[last] > c.rolling(50).mean().iloc[last], rsi.iloc[last] > 50,
        hist.iloc[last] > 0, c.pct_change(9).iloc[last] > 0,
        len(c) > 11 and c.iloc[last] > c.iloc[-11], sto.iloc[last] > 50,
    ]
    return sum(1 for x in checks if bool(x) is True)


def market_breadth(closes_by_sym: Dict[str, List[float]], bull_min: int) -> float:
    """Fraction of the universe that is broadly bullish (bull_score >= bull_min)."""
    scores = [bull_score_last(c) for c in closes_by_sym.values() if len(c)]
    return (sum(1 for s in scores if s >= bull_min) / len(scores)) if scores else 0.0


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def _load_state() -> dict:
    if PORTFOLIO_FILE.exists():
        try:
            return json.loads(PORTFOLIO_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"equity": 1.0, "weights": {}, "entry_px": {}, "last_rebalance_ms": 0, "rebalances": 0}


def _save_state(st: dict) -> None:
    PORTFOLIO_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_FILE.write_text(json.dumps(st, indent=2))


def _write_heartbeat(extra: dict) -> None:
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_FILE.write_text(json.dumps({"ts": time.time(), **extra}, indent=2))


def _append_equity(st: dict, regime: str, breadth: Optional[float] = None) -> None:
    """Persistent realised-equity series — the data the live-vs-backtest drift
    tracker reads (scripts/drift_tracker.py). Append-only, survives restarts."""
    EQUITY_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "rebalance": st.get("rebalances"),
           "equity": round(st["equity"], 6),
           "peak": round(st.get("peak_equity", st["equity"]), 6), "regime": regime}
    if breadth is not None:
        rec["breadth"] = round(breadth, 3)
    with EQUITY_HISTORY_FILE.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


# --------------------------------------------------------------------------- #
# Live loop
# --------------------------------------------------------------------------- #
async def _fetch_universe_closes(universe: List[str], lookback: int, skip: int):
    """Returns (closes_by_sym, latest_candle_ms). Illiquid/missing pairs drop out."""
    # Need enough history for both momentum (lookback) and the regime bull-score
    # (up to a 200-bar EMA), so fetch a generous fixed minimum.
    need = max(lookback + skip + 5, 220)
    sem = asyncio.Semaphore(6)

    async def one(sym):
        async with sem:
            try:
                h = await adapters.price.fetch_history(sym, timeframe="1d", total=need)
                cs = h["candles"]
                return sym, [c[4] for c in cs], cs[-1][0]
            except Exception:  # noqa: BLE001  a missing/illiquid pair just drops out
                return sym, None, 0
    res = await asyncio.gather(*[one(s) for s in universe])
    closes = {s: c for s, c, _ in res if c}
    latest_ms = max((ts for _, c, ts in res if c), default=0)
    return closes, latest_ms


async def run_xsmom_live(strategy: dict, interval: float = 3600.0,
                         max_ticks: Optional[int] = None):
    """Poll loop. Rebalances every `rebalance_days`; otherwise just heartbeats."""
    from rich import print as rprint

    e = strategy.get("entry", {})
    universe: List[str] = strategy["universe"]
    lookback = int(e.get("lookback_days", 30))
    skip = int(e.get("skip_days", 0))
    k = int(e.get("top_k", 5))
    allow_short = bool(e.get("allow_short", True))
    rebal_days = int(strategy.get("rebalance_days", 7))
    size_total = float(strategy.get("position_size_r", 0.3))
    costs = strategy.get("costs", {}) or {}
    cost = (float(costs.get("fees_bps", 10.0)) + float(costs.get("slippage_bps", 5.0))) / 1e4
    # Regime gate: only hold the momentum book when the market is broadly bullish,
    # else go to cash. Validated to lift OOS Sharpe ~0.3->1.6 and cut maxDD ~48%->9%.
    regime_gate = bool(e.get("regime_gate", False))
    breadth_thr = float(e.get("breadth_threshold", 0.4))
    bull_min = int(e.get("breadth_bull_min", 6))
    # Circuit breaker: if marked equity falls this far below its high-water mark,
    # emergency-flatten to cash and HALT until manually reset (delete portfolio.json
    # or set "halted": false). A backstop for "the strategy itself broke", separate
    # from the regime gate. 0/absent = disabled.
    max_dd_halt = float((strategy.get("risk") or {}).get("max_drawdown_halt", 0.0)) or None

    rprint(f"[bold green]Booting hermes-trading worker[/] · xsmom portfolio · paper mode")
    rprint(f"[dim]{len(universe)} coins · lookback{lookback}d skip{skip} "
           f"rebal{rebal_days}d top{k}/side {'long-short' if allow_short else 'long-only'} "
           f"size{size_total}{' · regime-gate ' + str(breadth_thr) if regime_gate else ''}[/]")

    consecutive_failures = 0
    tick = 0
    while max_ticks is None or tick < max_ticks:
        tick += 1
        try:
            closes, now_ms = await _fetch_universe_closes(universe, lookback, skip)
            if len(closes) < (2 * k if allow_short else k):
                raise RuntimeError(f"only {len(closes)} coins fetched; need {2*k if allow_short else k}")
            now_px = {s: c[-1] for s, c in closes.items()}
            st = _load_state()

            # High-water mark + circuit breaker (checked EVERY tick, so a mid-week
            # blowup trips it without waiting for the weekly rebalance).
            unreal0 = rebalance_pnl(st["weights"], st["entry_px"], now_px)
            mtm0 = st["equity"] * (1.0 + unreal0)
            st["peak_equity"] = max(st.get("peak_equity", st["equity"]), mtm0)
            dd = drawdown_frac(st["peak_equity"], mtm0)
            if max_dd_halt and not st.get("halted") and st["weights"] and dd >= max_dd_halt:
                turn = turnover(st["weights"], {})
                st["equity"] = mtm0 * (1.0 - turn * cost)   # realise loss + exit cost
                st["weights"] = {}
                st["entry_px"] = {}
                st["halted"] = True
                _save_state(st)
                _append_equity(st, regime="HALT")
                rprint(f"[bold red]RISK HALT[/] drawdown {dd:.1%} ≥ {max_dd_halt:.0%} "
                       f"— flattened to cash, trading halted (reset: delete portfolio.json)")
                print("RISK_HALT_JSON " + json.dumps(
                    {"ts": time.time(), "drawdown": round(dd, 4), "equity": round(st["equity"], 5)}),
                    flush=True)

            # Halted => never re-enter; stay in cash until manually reset.
            due = (not st.get("halted")) and (now_ms - st["last_rebalance_ms"] >= rebal_days * DAY_MS)

            if due:
                # 1) Realise P&L of the existing book since the last rebalance.
                if st["weights"]:
                    pnl = rebalance_pnl(st["weights"], st["entry_px"], now_px)
                    st["equity"] *= (1.0 + pnl)
                # 2) Regime gate: if the tape isn't broadly bullish, go to CASH.
                breadth = market_breadth(closes, bull_min) if regime_gate else 1.0
                flat = regime_gate and breadth < breadth_thr
                # 3) Form the new book (empty = cash) and charge turnover cost.
                if flat:
                    new_w = {}
                else:
                    mom = momentum(closes, lookback, skip)
                    new_w = target_weights(mom, k, allow_short, size_total)
                    if not new_w:
                        raise RuntimeError("could not form target book (insufficient ranked coins)")
                turn = turnover(st["weights"], new_w)
                st["equity"] *= (1.0 - turn * cost)
                longs = sorted([s for s, w in new_w.items() if w > 0])
                shorts = sorted([s for s, w in new_w.items() if w < 0])
                st["weights"] = new_w
                st["entry_px"] = {s: now_px[s] for s in new_w}
                st["last_rebalance_ms"] = now_ms
                st["rebalances"] += 1
                _save_state(st)
                _append_equity(st, regime=("CASH" if flat else "invested"), breadth=breadth)
                event = {"ts": time.time(), "equity": round(st["equity"], 5),
                         "rebalance": st["rebalances"], "longs": longs, "shorts": shorts,
                         "turnover": round(turn, 3), "breadth": round(breadth, 2),
                         "regime": "CASH" if flat else "invested"}
                if flat:
                    rprint(f"[yellow]REBALANCE #{st['rebalances']} → CASH[/] "
                           f"(breadth {breadth:.0%} < {breadth_thr:.0%}) equity={st['equity']:.4f}")
                else:
                    rprint(f"[cyan]REBALANCE #{st['rebalances']}[/] equity={st['equity']:.4f} "
                           f"breadth={breadth:.0%} long={longs} short={shorts}")
                print("REBALANCE_JSON " + json.dumps(event), flush=True)
            else:
                # Mark-to-market: the book trades weekly but its value moves every
                # tick. Show unrealised P&L so the strategy is visibly alive between
                # rebalances (this is display only — realised equity updates at
                # rebalance, matching the backtest accounting).
                unreal = rebalance_pnl(st["weights"], st["entry_px"], now_px)
                mtm = st["equity"] * (1.0 + unreal)
                days_left = (rebal_days * DAY_MS - (now_ms - st["last_rebalance_ms"])) / DAY_MS
                rprint(f"[dim]tick {tick}[/] · holding {len(st['weights'])} positions · "
                       f"realised={st['equity']:.4f} · live MtM={mtm:.4f} "
                       f"([{'green' if unreal>=0 else 'red'}]{unreal*100:+.2f}%[/]) · "
                       f"next rebalance in {days_left:.1f}d")
                # Per-position P&L (gain TO THE BOOK: a short profits when price
                # falls). One compact line so a remote watcher can push it to chat.
                parts = []
                for sym, w in st["weights"].items():
                    e, n = st["entry_px"].get(sym), now_px.get(sym)
                    if e and n and e > 0:
                        pl = (1 if w > 0 else -1) * (n / e - 1.0) * 100
                        parts.append((pl, f"{sym.split('/')[0]} {'L' if w > 0 else 'S'} {pl:+.1f}%"))
                parts.sort(reverse=True)
                print(f"POSITIONS t{tick} MtM={mtm:.4f}({unreal*100:+.2f}%) {days_left:.1f}d→rebal | "
                      + " | ".join(p for _, p in parts), flush=True)

            consecutive_failures = 0
            unreal_now = rebalance_pnl(st["weights"], st["entry_px"], now_px)
            _write_heartbeat({"tick": tick, "equity": st["equity"],
                              "mtm_equity": round(st["equity"] * (1.0 + unreal_now), 5),
                              "unrealised_pct": round(unreal_now * 100, 3),
                              "peak_equity": round(st.get("peak_equity", st["equity"]), 5),
                              "drawdown_pct": round(dd * 100, 2),
                              "halted": bool(st.get("halted")),
                              "rebalances": st["rebalances"], "held": len(st["weights"]),
                              "status": "halted" if st.get("halted") else "ok"})
        except SchemaError as exc:
            _write_heartbeat({"tick": tick, "status": "halt", "error": str(exc)})
            rprint(f"[bold red]SchemaError — halting:[/] {exc}")
            raise
        except Exception as exc:  # noqa: BLE001
            consecutive_failures += 1
            _write_heartbeat({"tick": tick, "status": "error",
                              "consecutive_failures": consecutive_failures, "error": str(exc)})
            rprint(f"[yellow]tick {tick} failed[/] ({consecutive_failures}/5): {exc}")
            if consecutive_failures >= 5:
                raise RuntimeError("circuit breaker: too many consecutive failures") from exc

        if max_ticks is None or tick < max_ticks:
            await asyncio.sleep(interval)
