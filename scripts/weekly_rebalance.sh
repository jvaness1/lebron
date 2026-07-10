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

run_pass() {  # $1 = pass label
  echo "--- pass $1 ---"
  # --max-total raised 100 -> 160 on 2026-06-16: a $30 BTC reward (converted to USDC) was
  # added to the ~$100 account. 160 gives headroom so normal appreciation before the weekly
  # run never binds the cap (and never trims good positions), while still capping runaway risk.
  # per-order cap 25 -> 35 so each of the 5 target slots (~$32 at $160/5) can fill in one order.
  EXCHANGE_ID=kucoin "$PY" -m hermes_trading.execute \
      $LIVE_FLAG --max-total 160 --max-order 35 2>&1
  echo "EXIT $?"
}

{
  echo "==================================================================="
  echo "RUN $(date '+%Y-%m-%d %H:%M:%S %Z')  ${LIVE_FLAG:-DRY-RUN}"
  echo "-------------------------------------------------------------------"
  # Pass 1 places the rotation. A sell-funded BUY in the same run can fail on
  # UNSETTLED proceeds (Coinbase hasn't credited the sell yet) — not a network
  # error, so the in-code retry can't catch it. Pass 2, after a settle delay,
  # reconciles the real account again and finishes any leftover buy. It's
  # idempotent: if pass 1 fully filled, pass 2 prints "already aligned".
  set +e
  out1="$(run_pass 1)"; echo "$out1"
  if [ -n "$LIVE_FLAG" ] && echo "$out1" | grep -qiE "INSUFFICIENT_FUND|0 placed|→ error"; then
    echo "... unfinished orders detected; waiting 90s for proceeds to settle, then pass 2 ..."
    sleep 90
    run_pass 2
  fi
  set -e
} >> "$LOG" 2>&1
