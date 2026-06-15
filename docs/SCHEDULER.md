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
trend — the dual-momentum exit signal. Notifies + appends to `state/trend_check.log`.
Currently **alert-only** (it does not sell). One-off: `python scripts/live_report.py
--trend`.

## Caveats

- **Laptop-dependent.** If the Mac is off (not just asleep) at the fire time and
  stays off, that week is skipped. Check `state/rebalance.log` weekly.
- Caps ($100 total / $25 order) bound every run regardless of strategy output.
- Secrets stay in `.env` (gitignored) — they are never in the plist or this repo.
