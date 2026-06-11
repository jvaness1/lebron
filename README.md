# hermes-trading (local paper bot)

A small, **paper-only** trading research bot you fully own. It pulls live market
data, simulates an RSI strategy, logs every outcome, and can "reflect" — changing
exactly one strategy variable at a time and versioning the history.

There is **no live-trading adapter** in this code. Nothing here can place a real
order. Going live would require writing that adapter yourself — it does not exist.

## Strategy (yours, in `state/goal.yaml`)
- Asset: `SOL/USDT`
- Target: +10% / 30 days · Min Sharpe 1.2
- Max drawdown (bail): 8%
- Reflect every 5 closed trades, change one variable

## Layout
```
hermes_trading/
  run.py        entrypoint (live loop / --demo replay / --once)
  loop.py       RSI engine + reliability loop (retries, circuit breaker, heartbeat)
  reflect.py    reflection cycle: --fallback (deterministic) or --llm (optional Claude)
  score.py      score(trades, goal) -> [-1, +1]
  metrics.py    net-of-cost performance stats (PF, expectancy, Sharpe/Sortino, DD)
  risk.py       kill switch: drawdown / daily-loss / loss-streak entry gates
  backtest.py   offline train/test backtest with costs — the edge test
  adapters/     price (ccxt) · onchain · news · macro — each keyless by default
state/
  goal.yaml     success/failure definition + risk limits + reflection.auto_apply
  strategy.yaml current strategy (versioned) incl. costs (fees_bps / slippage_bps)
  trades.jsonl  every paper trade (return_pct is NET of costs; gross + cost recorded too)
  hypotheses.jsonl  every reflection's reasoning
  history/      every prior strategy version
```

## Run it
```bash
cd ~/hermes-trading

# Backtest with realistic costs, in-sample vs out-of-sample (the honest edge test):
uv run python -m hermes_trading.backtest --bars 5000 --split 0.7
#   (note: some exchanges cap 1m history to ~720 bars — use --timeframe 5m/15m for more lookback)

# Replay recent history offline to generate paper trades fast:
uv run python -m hermes_trading.run --demo 500

# Force one reflection (deterministic — no API key needed):
uv run python -m hermes_trading.reflect --fallback

# Live paper loop (1-minute cadence, Ctrl-C to stop):
uv run python -m hermes_trading.run

# Tests:
uv run --group dev pytest
```

## Costs & risk (new)
- **Costs** (`strategy.yaml → costs`): every simulated fill pays `fees_bps` + `slippage_bps`
  per side. Paper `return_pct` is now NET of costs, so paper stops flattering the strategy.
- **Risk kill switch** (`goal.yaml → risk`): halts *new entries* (never strands an open
  position) on drawdown / daily-loss / consecutive-loss limits.
- **Reflection safety** (`goal.yaml → reflection.auto_apply`): set `false` to freeze
  auto-tuning. **Do this before ever considering real money** — an unsupervised loop
  tuning on small samples will overfit.

## Optional: Claude-driven reflection
`reflect.py --llm` uses the Anthropic API (model `claude-opus-4-8`) in readable
code. It needs `ANTHROPIC_API_KEY` and `uv add anthropic`. Entirely optional —
`--fallback` is fully deterministic and self-contained.

## Notes
- All keys read from `.env`; every adapter falls back to a free public endpoint.
- `.env` keeps `HERMES_TRADING_MODE=paper`. This repo has no code path that
  honors a "live" value — that's deliberate.
