# Going live on Coinbase — staged, safe playbook

The execution layer (`hermes_trading/execution.py` + `python -m hermes_trading.execute`)
is **dry-run by default** and **safe-by-design**: long-only, hard USD caps, never sells
more than held, reads your real account every run, never withdraws. Follow this order —
do NOT skip the dry-run-against-real-account step.

## 0. Prereqs (you)
- A **funded Coinbase account** with the coins in `state/strategy.yaml` enabled.
- **Trade-only API keys** — create with trade permission, **NOT withdrawal/transfer**.
- Decide a tiny starting size you're fine losing entirely (e.g. $100).

## 1. Set keys (locally, never commit)
```bash
export COINBASE_API_KEY=...        # trade-only
export COINBASE_API_SECRET=...
```

## 2. DRY-RUN against your real account (places nothing)
```bash
EXCHANGE_ID=kucoin python -m hermes_trading.execute --max-total 100 --max-order 25
```
It reads your real Coinbase balances, computes the target book, and prints the exact
orders it WOULD place. Verify they look sane (right coins, tiny sizes, total ≤ cap).

## 3. Go live — TINY
```bash
EXCHANGE_ID=kucoin python -m hermes_trading.execute --live --max-total 100 --max-order 25
```
Places real market orders, capped. Start at $100 total / $25 per order. The caps mean
even a bug can't deploy more than that.

## 4. Run it WEEKLY (matches the strategy's rebalance cadence)
This command does ONE rebalance. Run it once a week (the strategy rebalances weekly).
Either run it manually each week, or schedule it locally (cron/launchd). Do not run it
more often — the edge lives at weekly cadence (proven; faster whipsaws).

## 5. Scale only on confirmation
Watch live results vs the paper bot / backtest for a few weekly rebalances. If they
track, raise `--max-total` in steps. If they diverge, you've risked only lunch money —
stop and reassess.

## Safety recap (enforced in code)
- Dry-run unless `--live` AND keys present.
- Long-only spot; no shorting, no margin, no withdrawals.
- Per-order cap (`--max-order`) and total cap (`--max-total`) — hard limits.
- Never sells more than held; never buys beyond available cash.
- Reads real account each run (a restart can't double-trade).

## Honest caveats
- Signal data comes from KuCoin daily closes (prices ~identical across venues); orders
  execute on Coinbase. Fine for this daily strategy.
- This is backtest-validated, not yet live-proven. Tiny size IS the validation. Scale slowly.
- Real fills/slippage on small alts can exceed the 15bps model — watch the dry-run vs fills.
