# Hermes-trading — full session handoff (2026-06-11)

Single source of truth for everything built/learned in the interactive session, so
any "brain" (the hourly cloud research agent, a future Claude Code session, or a human)
can pick up with full context. Pairs with research/LOG.md (findings) and BACKLOG.md (queue).

NOTE on naming: the `hermes` CLI on this machine = NousResearch/hermes-agent, a generic
third-party agent framework. It is NOT this trading bot's brain and has no link to it.
The actual self-improving loop is THIS repo's research pipeline (cloud routine + BACKLOG +
LOG + the memory file). Treat that pipeline as "the brain."

## Mission
Paper-only crypto trading research bot. Find a strategy with a REAL edge net of costs,
validated out-of-sample, and run it live in paper. Honesty over hype: kill anything that
doesn't survive an honest OOS test. No real-money execution path exists by design.

## The journey (what we tried, in order)
1. **RSI dip-buy (original).** Fixed bugs (dead reflection, stale-data ratchet, TP that
   booked losses), added a cost model. Verdict: NO edge after costs. Killed.
2. **1h Donchian breakout (Turtle).** Looked great on SOL (+34%/Sharpe0.9). But the
   multi-asset test showed it does NOT generalise (43% of coins positive, equal-weight
   ~flat). SOL was favorable luck. Killed as the live strategy.
3. **High-frequency search (multiple trades/day).** Every config loses to taker fees;
   only viable at maker (≤~3bps/side) fees. Killed for taker; noted as maker-only.
4. **Cross-sectional momentum (THE WINNER).** Rank a 24-coin universe by 30d return,
   long top-5 / short bottom-5, weekly rebalance, dollar-neutral. Survivorship-mitigated
   OOS: +143% / Sharpe ~1.3 while the market fell -32%. Robust across K. DEPLOYED.
5. **Regime gate overlay (P0, the improvement).** Use a market-breadth signal (% of
   universe broadly bullish via a 10-check daily bull-score) to hold the book only when
   breadth ≥ 40%, else CASH. OOS Sharpe 0.31→~1.3-1.6, maxDD 48%→4-11%, robust across
   thresholds. Per-position indicator filtering / conviction sizing did NOT help — only
   the regime gate. DEPLOYED (strategy.yaml v02).

## Current LIVE system (Railway, paper)
- **Strategy:** `state/strategy.yaml` — xsmom, 24 coins, lookback 30d, weekly rebalance,
  long top-5 / short bottom-5, size 0.3, costs 10+5 bps/side, **regime_gate on
  (breadth_threshold 0.4, bull_min 6)**.
- **Engine:** `hermes_trading/portfolio.py` (`run_xsmom_live`), dispatched from run.py when
  entry.indicator == "xsmom". Marks MtM every 30 min, rebalances weekly, emits
  REBALANCE_JSON + a per-position POSITIONS line. State in `state/portfolio.json` (volume).
- **Railway:** project hermes-trading-paper, EXCHANGE_ID=kucoin env set so all 24 pairs
  resolve. Redeploy with `railway up` (CLI at ~/.hermes/node/bin/railway).
- **Shorts are SIMULATED in paper** — real money would need perpetual futures. A long-only
  spot variant also beat the benchmark (see LOG) if staying spot.

## Honest caveats (do not forget these)
- Everything is BACKTEST-validated, not live-proven. ~3yr / ~68 weekly OOS periods / one
  venue (KuCoin) / survivors-only universe. Strong signals, not certainties.
- The regime-gate Sharpe is partly flattered by zero-return cash periods (it's flat ~66%
  of the time). maxDD reduction is real. P0a (walk-forward) is queued to pressure-test it.
- Cross-sectional momentum is a documented anomaly (raises prior confidence); the
  crypto-specific local evidence is modest.

## Infrastructure built (all in this repo)
- `loop.py` — cost-aware single-asset engine (RSI/Donchian), risk kill switch, metrics.
- `portfolio.py` — the live xsmom portfolio engine + regime gate.
- `metrics.py` — net-of-cost PF, expectancy, Sharpe/Sortino, maxDD.
- `risk.py` — drawdown / daily-loss / loss-streak entry gates (single-asset engine).
- `backtest.py` — train/test backtest with costs.
- `scripts/` — strategy_search, multi_asset_backtest, multi_asset_regime, xsmom,
  hf_search, test_overlay, validate_donchian, tf_sweep (the research tooling).
- 41 pytest tests, all green.

## Key facts for any researcher
- DATA: Kraken caps OHLCV ~720 bars; **use EXCHANGE_ID=kucoin** for deep history.
- COSTS: 10bps fee + 5bps slippage per side (in strategy.yaml). Always apply them.
- METHODOLOGY: strict train→test, select params on TRAIN only, report OOS only, state
  sample size, be explicit about survivorship. A result only counts if it survives OOS.

## The self-improving loop (the real "brain")
- Cloud routine `hermes-research` (trig_01SkvcKoTqAoJfMuGRKog9xC), HOURLY, works one
  BACKLOG item/run, writes LOG, self-merges to main.
- Local repo auto-pulls main every 20 min (cron); findings relay into chat.
- Repo: github.com/jvaness1/lebron (private). Open backlog: P0a/P0b (validate+extend the
  regime gate), P1 (daily-frame variant), P2-P9 (vol-scaling, wider universe, skip-period,
  longer history, long-only spot, cost sensitivity, live-vs-backtest drift).

## What to do next (if you're the brain picking this up)
1. Work the top BACKLOG item with the mandatory methodology; record in LOG; merge to main.
2. Only ever deploy a change that beats the live baseline OOS. Never touch real money.
3. Watch the live paper results vs backtest expectation (P9 drift tracker) — that's the
   real test of whether any of this holds.
