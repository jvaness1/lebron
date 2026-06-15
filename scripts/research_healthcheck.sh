#!/bin/bash
# Deterministic weekly research health-check (no LLM, cheap, reliable).
#
# Re-runs the existing harness against the LIVE situation and appends a dated
# entry to research/LOG.md:
#   1) drift tracker  — live realised equity vs a backtest over the same dates
#      (the faithful live check; needs >=3 live rebalances to compare).
#   2) edge persistence — xsmom walk-forward, to see if the momentum edge still
#      shows out-of-sample as new data accrues.
# READ-ONLY on the strategy: never edits state/strategy.yaml, never trades.
#
# Driven by launchd (com.hermes.research-weekly). Manual: bash scripts/research_healthcheck.sh

set -uo pipefail

MAIN="$HOME/hermes-trading"
PY="$MAIN/.venv/bin/python"
LOGMD="$MAIN/research/LOG.md"
RUNLOG="$MAIN/state/research_healthcheck.log"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export EXCHANGE_ID=kucoin
cd "$MAIN" || exit 1

{
  echo ""
  echo "## $(date '+%Y-%m-%d') · HEALTHCHECK (deterministic, automated)"
  echo ""
  echo "Live config: $(grep -E 'indicator|lookback|trend_ma_days|k:|rebalance' state/strategy.yaml | tr '\n' ' ')"
  echo ""
  echo '```'
  echo "### drift: live realised vs backtest (same dates)"
  "$PY" scripts/drift_tracker.py 2>&1
  echo ""
  echo "### edge persistence: xsmom walk-forward"
  "$PY" scripts/xsmom_walkforward.py 2>&1
  echo '```'
  echo ""
} >> "$LOGMD"

# commit the notes (research/ only) to main and push — never strategy.yaml
git add "$LOGMD" "$MAIN/research/walk_forward_p0a.json"
git commit -m "research: automated weekly healthcheck $(date '+%Y-%m-%d')" 2>&1 || true
git push origin main 2>&1 || true

echo "$(date '+%Y-%m-%d %H:%M:%S') healthcheck done" >> "$RUNLOG"
