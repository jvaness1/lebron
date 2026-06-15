# Weekly live rebalance scheduler (macOS launchd)

The live strategy rebalances **weekly**. On this Mac that's driven by a launchd
job. The plist lives outside the repo (it points at machine-local paths and is
per-user); this doc makes the setup reproducible.

## What runs

`scripts/weekly_rebalance.sh` → `python -m hermes_trading.execute --live
--max-total 100 --max-order 25` (quote = USDC, `EXCHANGE_ID=kucoin` for symbol
resolution). Safe-by-design: long-only, hard $ caps, reconciles the real Coinbase
account each run, never sells more than held, never withdraws. Output appends to
`state/rebalance.log`.

## Schedule

`~/Library/LaunchAgents/com.hermes.rebalance.plist` — **Sundays 17:00 local**
(`Weekday 0`). `RunAtLoad` is false (loading never fires a live trade). launchd
runs missed calendar jobs when the Mac next wakes, so a sleeping laptop only
delays the rebalance, it doesn't skip it. Job-level errors → `state/launchd.*.log`.

## Manage it

```bash
# load / reload after editing the plist
launchctl unload ~/Library/LaunchAgents/com.hermes.rebalance.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/com.hermes.rebalance.plist
launchctl list | grep com.hermes.rebalance        # confirm registered

# run it now by hand
bash scripts/weekly_rebalance.sh                  # LIVE (places real orders)
DRY=1 bash scripts/weekly_rebalance.sh            # dry-run, places nothing

# stop scheduling
launchctl unload ~/Library/LaunchAgents/com.hermes.rebalance.plist

tail -f state/rebalance.log                       # watch results
```

## Live position report (every 30 min)

`~/Library/LaunchAgents/com.hermes.report.plist` runs `scripts/live_report.py
--notify --log` every 1800s (`StartInterval`, `RunAtLoad` on so it fires once at
load). Read-only: reads the real Coinbase account, computes per-coin + total P&L
vs actual fill cost basis. Fires a macOS notification with the summary line and
appends the full report to `state/live_report.log`. Coinbase-only — independent
of the paper/Railway worker.

```bash
launchctl load   ~/Library/LaunchAgents/com.hermes.report.plist   # start
launchctl unload ~/Library/LaunchAgents/com.hermes.report.plist   # stop
tail -f state/live_report.log                                     # history
python scripts/live_report.py                                     # one-off, stdout only
```

## End-of-day 100-day trend check (daily 23:55)

`~/Library/LaunchAgents/com.hermes.trend.plist` runs `scripts/live_report.py
--trend --notify --log` daily at 23:55 local. For each held coin it compares the
latest daily close to its **100-day SMA** (the strategy's actual exit rule,
`entry.trend_ma_days`) and **100-day EMA**, and flags any coin trading below its
100d SMA (the strategy's actual exit rule) — the dual-momentum exit signal.
**Alert-only**: notifies + appends to `state/trend_check.log`, places no orders.
The weekly rebalance handles the actual exit. (Daily auto-exit was tried and
reverted: it deviates from the backtested weekly cadence and is exit-daily /
re-enter-weekly asymmetric.)

```bash
python scripts/live_report.py --trend          # check only, no orders (what the job runs)
python scripts/live_report.py --trend --exit   # DORMANT capability: check + auto-sell
                                               # breaks to USDC (LIVE). Not enabled in the
                                               # launchd job. Backtest before using.
```

## Local research automation

Real research must run LOCALLY (the cloud sandbox can't reach exchanges). Two jobs:

**Autonomous agent — daily 04:00** (`com.hermes.research-daily` →
`scripts/research_agent.sh`). Runs a local headless Claude (`claude -p`) in an
ISOLATED git worktree `~/hermes-trading-research` on branch `research/auto`. That
worktree has no `.env` → no Coinbase keys → it physically cannot trade (keys are
also blanked in the wrapper env). It works one `research/BACKLOG.md` item (or
proposes a new hypothesis), validates with the locked methodology, appends to
`research/LOG.md`, and writes any improvement to `state/strategy.candidate.yaml`
— never the live `state/strategy.yaml`. Commits to `research/auto` and pushes for
review; never pushes/merges to `main`. Instructions + hard safety invariants:
`research/AGENT_PROMPT.md`. Log: `state/research_agent.log`.

Review its work: `git log main..research/auto`, or open a PR for `research/auto`.
To deploy a proposal: review `state/strategy.candidate.yaml`, then YOU copy it to
`state/strategy.yaml` and commit. Nothing auto-deploys.

**Deterministic health-check — Mon 06:00** (`com.hermes.research-weekly` →
`scripts/research_healthcheck.sh`). No LLM. Runs the drift tracker (live equity
vs backtest) + xsmom walk-forward (edge persistence), appends to `research/LOG.md`,
commits notes to `main`. Read-only on the strategy.

```bash
bash scripts/research_agent.sh          # run the agent now (isolated, safe)
bash scripts/research_healthcheck.sh    # run the weekly check now
git worktree list                       # see the research worktree
```

## Caveats

- **Laptop-dependent.** If the Mac is off (not just asleep) at the fire time and
  stays off, that week is skipped. Check `state/rebalance.log` weekly.
- Caps ($100 total / $25 order) bound every run regardless of strategy output.
- Secrets stay in `.env` (gitignored) — they are never in the plist or this repo.
