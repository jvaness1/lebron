"""The trading engine and the 24/7 reliability loop.

PaperEngine holds the strategy and a single open paper position. It is pure and
synchronous, so the same code drives both the live async loop and the offline
`--demo` replay — there is only ever one decision path.

NOTHING here can place a real order. `_open`/`_close` only record simulated
fills to state/trades.jsonl.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import List, Optional

import yaml

from . import adapters
from . import features
from . import risk
from .adapters import SchemaError

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
TRADES_FILE = STATE_DIR / "trades.jsonl"
HEARTBEAT_FILE = STATE_DIR / "heartbeat.json"
STRATEGY_FILE = STATE_DIR / "strategy.yaml"
GOAL_FILE = STATE_DIR / "goal.yaml"

RSI_PERIOD = 14
BARS_HISTORY = 300  # OHLCV rows kept for the multi-timeframe feature snapshot
MAX_CONSECUTIVE_FAILURES = 5


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def rsi(closes: List[float], period: int = RSI_PERIOD) -> Optional[float]:
    """Wilder's RSI over `closes`. None until there's enough history."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


# --------------------------------------------------------------------------- #
# Paper trading engine
# --------------------------------------------------------------------------- #
class PaperEngine:
    """Long-only RSI strategy over a single paper position."""

    def __init__(self, strategy: dict, asset: str, risk_cfg: Optional[dict] = None):
        self.asset = asset
        self.strategy = strategy
        # Strategy family: "rsi" (mean-reversion dip-buy) or "donchian" (breakout).
        self.indicator = strategy.get("entry", {}).get("indicator", "rsi")
        thr = strategy.get("entry", {}).get("threshold")
        self.threshold = float(thr) if thr is not None else None
        # Donchian breakout params: enter above the prior `breakout_lookback`-bar
        # high, exit below the prior `donchian_exit`-bar low (or the stop).
        self.breakout_lookback = strategy.get("entry", {}).get("breakout_lookback")
        self.donchian_exit = strategy.get("exit", {}).get("donchian_exit")
        self.tp_rsi = float(strategy.get("exit", {}).get("rsi_take_profit", 70))
        # An RSI take-profit only fires once the trade is at least this far in
        # NET profit (after fees+slippage), so a "take_profit" exit can never book
        # a loss. 0.0 => "net break-even"; below the gate the only exit is the stop.
        self.min_profit_pct = float(strategy.get("exit", {}).get("min_profit_pct", 0.0))
        self.stop_loss_pct = float(strategy["stop_loss_pct"])
        self.size_r = float(strategy["position_size_r"])
        # Transaction costs, applied to every simulated fill. With both at 0 the
        # engine behaves exactly as before; set realistic values so paper results
        # reflect production. fees_bps = per-side fee, slippage_bps = per-side slip.
        costs = strategy.get("costs", {}) or {}
        self.fees_bps = float(costs.get("fees_bps", 0.0))
        self.slippage_bps = float(costs.get("slippage_bps", 0.0))
        self.min_bull_count = strategy.get("entry", {}).get("min_bull_count")
        self.min_adx = strategy.get("entry", {}).get("min_adx")
        self.min_return_floor = float(strategy.get("min_return_floor", 0.0))
        self.cooldown_minutes = float(strategy.get("cooldown_minutes", 0))
        self.risk_cfg = risk_cfg or {}
        self.last_risk_reason: Optional[str] = None
        self.closes: List[float] = []
        self.bars: List[list] = []  # recent OHLCV rows for feature snapshots
        self.position: Optional[dict] = None  # {entry, ts, rsi, features}
        self.closed_trades: List[dict] = []  # net-costed closed trades this engine has booked
        self.closed_count = 0

    def load_closed_history(self) -> None:
        """Seed in-memory trade history from the volume so cooldown + risk limits
        survive a worker restart (the live loop calls this; demo/backtest don't)."""
        if not TRADES_FILE.exists():
            return
        for line in TRADES_FILE.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                self.closed_trades.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    def warmup(self, candles: List[list]) -> None:
        """Seed price history WITHOUT generating trades (used on the first live tick)."""
        self.bars = list(candles)[-BARS_HISTORY:]
        self.closes = [c[4] for c in self.bars][-(RSI_PERIOD * 5):]

    def update_strategy(self, strategy: dict) -> None:
        """Refresh tuned parameters mid-run after a reflection cycle."""
        self.strategy = strategy
        self.indicator = strategy.get("entry", {}).get("indicator", self.indicator)
        thr = strategy.get("entry", {}).get("threshold")
        self.threshold = float(thr) if thr is not None else None
        self.breakout_lookback = strategy.get("entry", {}).get("breakout_lookback", self.breakout_lookback)
        self.donchian_exit = strategy.get("exit", {}).get("donchian_exit", self.donchian_exit)
        self.tp_rsi = float(strategy.get("exit", {}).get("rsi_take_profit", self.tp_rsi))
        self.min_profit_pct = float(strategy.get("exit", {}).get("min_profit_pct", self.min_profit_pct))
        self.stop_loss_pct = float(strategy["stop_loss_pct"])
        self.size_r = float(strategy["position_size_r"])
        costs = strategy.get("costs", {}) or {}
        self.fees_bps = float(costs.get("fees_bps", self.fees_bps))
        self.slippage_bps = float(costs.get("slippage_bps", self.slippage_bps))
        self.min_bull_count = strategy.get("entry", {}).get("min_bull_count")
        self.min_adx = strategy.get("entry", {}).get("min_adx")
        self.min_return_floor = float(strategy.get("min_return_floor", 0.0))
        self.cooldown_minutes = float(strategy.get("cooldown_minutes", 0))

    def _cooldown_remaining(self, now: Optional[float] = None) -> float:
        now = now if now is not None else time.time()
        cooldown = self.cooldown_minutes * 60.0
        if cooldown <= 0:
            return 0.0
        last_sl_ts: Optional[float] = None
        for t in reversed(self.closed_trades):
            if t.get("status") == "closed" and t.get("reason") == "stop_loss":
                last_sl_ts = float(t.get("exit_ts", 0.0))
                break
        if last_sl_ts is None:
            return 0.0
        return max(0.0, cooldown - (now - last_sl_ts))

    def _net_return(self, entry: float, exit_px: float) -> float:
        """Return fraction (pre-position-size) after per-side slippage + fees.
        Long-only: buy worse (higher), sell worse (lower), pay a fee each side."""
        slip = self.slippage_bps / 1e4
        fee = self.fees_bps / 1e4
        eff_entry = entry * (1.0 + slip)
        eff_exit = exit_px * (1.0 - slip)
        return (eff_exit - eff_entry) / eff_entry - 2.0 * fee

    def _risk_blocks_entry(self, now: float) -> bool:
        """Consult the kill switch before opening. Records the reason for the
        heartbeat. Never affects an already-open position."""
        verdict = risk.evaluate(self.risk_cfg, self.closed_trades, now)
        self.last_risk_reason = verdict.get("reason")
        return not verdict["allow_entry"]

    def _passes_feature_filters(self, feats: Optional[dict]) -> bool:
        if not feats:
            return True
        bullet_bull = feats.get("bull_count")
        bullet_adx = feats.get("adx")

        if isinstance(self.min_bull_count, int) and isinstance(bullet_bull, int):
            if bullet_bull < self.min_bull_count:
                return False
        if isinstance(self.min_adx, (int, float)) and isinstance(bullet_adx, (int, float)):
            if float(bullet_adx) < float(self.min_adx):
                return False
        return True

    def on_bar(self, close: float, ts: float, bar: Optional[list] = None) -> Optional[dict]:
        """Feed one bar. Returns a closed-trade dict when a trade closes. Dispatches
        to the configured strategy family (RSI mean-reversion or Donchian breakout)."""
        self.closes.append(close)
        self.closes = self.closes[-(RSI_PERIOD * 5):]  # bounded history
        if bar is not None:
            self.bars.append(list(bar))
            self.bars = self.bars[-BARS_HISTORY:]
        if self.indicator == "donchian":
            return self._on_bar_donchian(close, ts)
        return self._on_bar_rsi(close, ts)

    def _on_bar_rsi(self, close: float, ts: float) -> Optional[dict]:
        r = rsi(self.closes)
        if r is None:
            return None

        if self.position is None:
            # Cooldown after stop-loss: skip entries if within cooldown window.
            if self._cooldown_remaining(now=ts) > 0:
                return None
            if self.threshold is None or r >= self.threshold:
                return None
            feats = self._snapshot_features()
            if not self._passes_feature_filters(feats):
                return None
            # Kill switch: drawdown / daily-loss / loss-streak limits gate entries.
            if self._risk_blocks_entry(ts):
                return None
            self.position = {"entry": close, "ts": ts, "entry_rsi": r, "features": feats}
            return None

        # Manage the open long.
        entry = self.position["entry"]
        stop = entry * (1.0 - self.stop_loss_pct / 100.0)
        if close <= stop:
            return self._close(close, ts, r, reason="stop_loss")
        # Take profit on RSI exhaustion, but ONLY when the position clears the NET
        # profit floor (after fees+slippage). RSI can reach the take-profit level
        # while the trade is underwater; honouring it there would book a loss.
        if r >= self.tp_rsi and self._net_return(entry, close) >= self.min_profit_pct / 100.0:
            return self._close(close, ts, r, reason="take_profit")
        return None

    def _on_bar_donchian(self, close: float, ts: float) -> Optional[dict]:
        """Turtle-style breakout: enter long above the prior `breakout_lookback`-bar
        high; exit below the prior `donchian_exit`-bar low or on the stop. Decisions
        use only PRIOR bars (the current bar is excluded) — no lookahead."""
        lookback = int(self.breakout_lookback or 0)
        exit_n = int(self.donchian_exit or 0)
        if len(self.bars) < lookback + 1 or lookback == 0:
            return None  # not enough history to define the channel yet

        if self.position is None:
            if self._cooldown_remaining(now=ts) > 0:
                return None
            prior_highs = [b[2] for b in self.bars[-(lookback + 1):-1]]
            if not prior_highs or close <= max(prior_highs):
                return None  # no breakout
            feats = self._snapshot_features()
            if not self._passes_feature_filters(feats):
                return None
            if self._risk_blocks_entry(ts):
                return None
            self.position = {"entry": close, "ts": ts, "entry_rsi": None, "features": feats}
            return None

        # Manage the open long. The stop is a resting order: it triggers when the
        # bar's LOW touches it (intrabar), and fills at the stop price — not at the
        # close. The Donchian exit is close-based.
        entry = self.position["entry"]
        stop = entry * (1.0 - self.stop_loss_pct / 100.0)
        cur_low = self.bars[-1][3] if self.bars else close
        if cur_low <= stop:
            return self._close(stop, ts, None, reason="stop_loss")
        prior_lows = [b[3] for b in self.bars[-(exit_n + 1):-1]] if exit_n else []
        if prior_lows and close < min(prior_lows):
            return self._close(close, ts, None, reason="donchian_exit")
        return None

    def _snapshot_features(self) -> Optional[dict]:
        """Multi-timeframe indicator snapshot at this bar. Never fatal — a feature
        failure must not block a paper trade, so it degrades to None."""
        if not self.bars:
            return None
        try:
            return features.multi_timeframe(self.bars)
        except Exception:  # noqa: BLE001  context is best-effort, trading is not
            return None

    def _close(self, exit_px: float, ts: float, exit_rsi: Optional[float], reason: str) -> dict:
        pos = self.position
        self.position = None
        entry = pos["entry"]
        gross = (exit_px - entry) / entry
        net = self._net_return(entry, exit_px)
        ret = net * self.size_r  # `return_pct` is NET, position-scaled — the truth
        entry_rsi = pos.get("entry_rsi")
        trade = {
            "status": "closed",
            "asset": self.asset,
            "direction": "long",
            "entry": entry,
            "exit": exit_px,
            "entry_ts": pos["ts"],
            "exit_ts": ts,
            "entry_rsi": round(entry_rsi, 2) if entry_rsi is not None else None,
            "exit_rsi": round(exit_rsi, 2) if exit_rsi is not None else None,
            "return_pct": ret,
            "gross_return_pct": gross * self.size_r,
            "cost_pct": (gross - net) * self.size_r,
            "reason": reason,
            "mode": "paper",
            "features": pos.get("features"),
        }
        self.closed_trades.append(trade)
        self.closed_count += 1
        return trade


# --------------------------------------------------------------------------- #
# Persistence helpers
# --------------------------------------------------------------------------- #
def append_trade(trade: dict) -> None:
    TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with TRADES_FILE.open("a") as fh:
        fh.write(json.dumps(trade) + "\n")


def write_heartbeat(extra: dict) -> None:
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": time.time(), **extra}
    HEARTBEAT_FILE.write_text(json.dumps(payload, indent=2))


def load_strategy() -> dict:
    return yaml.safe_load(STRATEGY_FILE.read_text())


# --------------------------------------------------------------------------- #
# Reliability: per-adapter retry with exponential backoff
# --------------------------------------------------------------------------- #
async def fetch_with_retry(coro_factory, *, retries: int = 3, base_delay: float = 1.0):
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            return await coro_factory()
        except SchemaError:
            raise  # schema mismatch is fatal by design — do not retry
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries - 1:
                await asyncio.sleep(base_delay * (2 ** attempt))
    raise last_exc  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Replay (offline demo) and live loop share PaperEngine
# --------------------------------------------------------------------------- #
def replay(engine: PaperEngine, candles: List[list]) -> List[dict]:
    """Replay historical candles bar-by-bar. Returns the closed trades."""
    closed: List[dict] = []
    for c in candles:
        ts, close = c[0] / 1000.0, c[4]
        trade = engine.on_bar(close, ts, bar=c)
        if trade:
            append_trade(trade)
            closed.append(trade)
    return closed


async def run_live(asset: str, interval: float = 60.0, max_ticks: Optional[int] = None):
    """Live paper loop. Every `interval` seconds: pull data, decide, log, heartbeat.

    Circuit-breaks after MAX_CONSECUTIVE_FAILURES consecutive failed ticks.
    """
    from rich import print as rprint

    strategy = load_strategy()
    goal = yaml.safe_load(GOAL_FILE.read_text()) if GOAL_FILE.exists() else {}
    engine = PaperEngine(strategy, asset, risk_cfg=goal.get("risk"))
    engine.load_closed_history()  # seed cooldown + risk limits across restarts
    timeframe = strategy.get("timeframe", "1m")  # e.g. donchian breakout runs on 1h
    consecutive_failures = 0
    tick = 0
    last_ts: Optional[float] = None  # ms timestamp of the newest candle already processed

    rprint(f"[bold green]Booting hermes-trading worker[/] · {asset} · "
           f"{strategy.get('entry', {}).get('indicator', 'rsi')} on {timeframe} · paper mode")
    if engine.risk_cfg:
        rprint(f"[dim]risk limits active: {engine.risk_cfg}[/]")

    while max_ticks is None or tick < max_ticks:
        tick += 1
        try:
            price = await fetch_with_retry(lambda: adapters.price.fetch(asset, timeframe=timeframe, limit=200))
            adapters.require_schema(price, adapters.price.SCHEMA_VERSION, source="price")

            # Context adapters are best-effort; failures here don't kill the tick.
            for name, factory in (
                ("onchain", lambda: adapters.onchain.fetch(asset)),
                ("news", lambda: adapters.news.fetch(asset)),
                ("macro", lambda: adapters.macro.fetch(asset)),
            ):
                try:
                    await fetch_with_retry(factory)
                except SchemaError:
                    raise  # schema drift is always fatal
                except Exception:
                    pass

            # Process only genuinely new candles. On the first tick, warm up RSI
            # history without trading, then act only on bars that close afterwards.
            candles = price["candles"]
            if last_ts is None:
                engine.warmup(candles)
                last_ts = candles[-1][0]
            else:
                for c in candles:
                    if c[0] <= last_ts:
                        continue
                    last_ts = c[0]
                    trade = engine.on_bar(c[4], c[0] / 1000.0, bar=c)
                    if trade:
                        append_trade(trade)
                        rprint(f"[cyan]closed trade[/] {trade['reason']} "
                               f"ret={trade['return_pct']:+.4f}")
                        # Full JSON on its own line so a remote watcher (Hermes)
                        # can reconstruct every trade from `railway logs` alone —
                        # no SSH/volume access required. Plain stdout + flush.
                        print("TRADE_JSON " + json.dumps(trade), flush=True)

                        # Self-improvement: reflect every `reflection_every`
                        # closed trades (non-fatal). reflect_fallback self-gates
                        # on new trades, so re-entry on the same data is a no-op.
                        try:
                            from .reflect import reflect_fallback
                            goal = yaml.safe_load(GOAL_FILE.read_text())
                            every = int(goal.get("reflection_every", 5))
                            # Live-safety: auto-tuning is great for paper research
                            # but a liability on real capital. When auto_apply is
                            # false, reflection is skipped entirely (no param edits).
                            auto_apply = bool(goal.get("reflection", {}).get("auto_apply", True))
                            trades_now = []
                            for line in TRADES_FILE.read_text().splitlines():
                                line = line.strip()
                                if line:
                                    trades_now.append(json.loads(line))
                            closed_count = sum(
                                1 for t in trades_now if t.get("status") == "closed"
                            )
                            if not auto_apply:
                                rprint("[dim]reflection paused (auto_apply=false)[/]")
                            elif closed_count and every > 0 and closed_count % every == 0:
                                rprint(f"[yellow]reflecting after {every} trades[/]")
                                cur_strat = yaml.safe_load(STRATEGY_FILE.read_text())
                                hyp = reflect_fallback(goal, cur_strat, trades_now)
                                if hyp:
                                    rprint(f"[green]reflected[/] v{hyp['from_version']}->v{hyp['to_version']} "
                                           f"{hyp['variable']} {hyp['old']}->{hyp['new']}")
                                    # reflect_fallback wrote the NEW strategy to
                                    # disk; reload it so the engine runs the tuned
                                    # params, not the pre-reflection snapshot.
                                    strategy = load_strategy()
                                    engine.update_strategy(strategy)
                        except Exception as exc:  # noqa: BLE001
                            rprint(f"[yellow]reflection skipped[/]: {exc}")

            consecutive_failures = 0
            risk_verdict = risk.evaluate(engine.risk_cfg, engine.closed_trades, time.time())
            write_heartbeat({
                "tick": tick,
                "asset": asset,
                "last_price": price["last"],
                "open_position": engine.position is not None,
                "entries_halted": not risk_verdict["allow_entry"],
                "risk_reason": risk_verdict["reason"],
                "status": "ok",
            })
            held = "in position" if engine.position is not None else "flat"
            risk_note = "" if risk_verdict["allow_entry"] else f" · [red]ENTRIES HALTED: {risk_verdict['reason']}[/]"
            rprint(f"[dim]tick {tick}[/] · {asset} {price['last']} "
                   f"via {price['exchange']} · {held} · "
                   f"trades={len(engine.closed_trades)}{risk_note}")
        except SchemaError as exc:
            write_heartbeat({"tick": tick, "status": "halt", "error": str(exc)})
            rprint(f"[bold red]SchemaError — halting loop:[/] {exc}")
            raise
        except Exception as exc:  # noqa: BLE001
            consecutive_failures += 1
            write_heartbeat({"tick": tick, "status": "error",
                             "consecutive_failures": consecutive_failures,
                             "error": str(exc)})
            rprint(f"[yellow]tick {tick} failed[/] ({consecutive_failures}/"
                   f"{MAX_CONSECUTIVE_FAILURES}): {exc}")
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                rprint("[bold red]Circuit breaker tripped — stopping.[/]")
                raise RuntimeError("circuit breaker: too many consecutive failures") from exc

        if max_ticks is None or tick < max_ticks:
            await asyncio.sleep(interval)
