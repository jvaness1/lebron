#!/bin/bash
# Weekly LIVE rebalance for the hermes-trading bot (driven by launchd).
# Safe-by-design: long-only, hard $ caps, reconciles the real Coinbase account
# each run, never sells more than held, never withdraws. Secrets live in .env.
#
# Run by: ~/Library/LaunchAgents/com.hermes.rebalance.plist (weekly).
# Manual:  bash scripts/weekly_rebalance.sh        (live)
#          DRY=1 bash scripts/weekly_rebalance.sh  (dry-run, places nothing)

set -euo pipefail

REPO="$HOME/hermes-trading"
LOG="$REPO/state/rebalance.log"
PY="$REPO/.venv/bin/python"

cd "$REPO"

LIVE_FLAG="--live"
[ "${DRY:-0}" = "1" ] && LIVE_FLAG=""

{
  echo "==================================================================="
  echo "RUN $(date '+%Y-%m-%d %H:%M:%S %Z')  ${LIVE_FLAG:-DRY-RUN}"
  echo "-------------------------------------------------------------------"
  EXCHANGE_ID=kucoin "$PY" -m hermes_trading.execute \
      $LIVE_FLAG --max-total 100 --max-order 25 2>&1
  echo "EXIT $?"
} >> "$LOG" 2>&1
