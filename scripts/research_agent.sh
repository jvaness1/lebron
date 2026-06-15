#!/bin/bash
# Autonomous LOCAL research agent for the hermes-trading bot.
#
# SAFE BY ISOLATION:
#  * Runs in a separate git WORKTREE (~/hermes-trading-research) on branch
#    research/auto — the live working tree (live strategy.yaml the rebalance reads)
#    is never touched.
#  * That worktree has NO .env (it's gitignored), so the agent has no Coinbase keys
#    and physically cannot trade. We also blank the key vars as belt-and-suspenders.
#  * The agent proposes only (state/strategy.candidate.yaml + research/*); it never
#    edits the live config and never deploys. Findings land on research/auto for
#    human review (open a PR / merge when you like).
#
# Driven by launchd (com.hermes.research-daily). Manual: bash scripts/research_agent.sh

set -uo pipefail

MAIN="$HOME/hermes-trading"
WT="$HOME/hermes-trading-research"
BRANCH="research/auto"
PY="$MAIN/.venv/bin/python"
CLAUDE="$HOME/.local/bin/claude"
LOG="$MAIN/state/research_agent.log"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin"
export EXCHANGE_ID=kucoin
# belt-and-suspenders: even if a .env leaked in, no usable keys => cannot go live.
export COINBASE_API_KEY=""
export COINBASE_API_SECRET=""

exec >> "$LOG" 2>&1
echo "==================================================================="
echo "RESEARCH AGENT RUN $(date '+%Y-%m-%d %H:%M:%S %Z')"

# 1) ensure the worktree exists and is freshened from main
cd "$MAIN" || exit 1
if [ ! -d "$WT/.git" ] && [ ! -f "$WT/.git" ]; then
    git worktree add -B "$BRANCH" "$WT" main || { echo "worktree add failed"; exit 1; }
else
    git -C "$WT" checkout "$BRANCH" 2>/dev/null || git -C "$WT" checkout -B "$BRANCH" main
    # bring in anything merged to main since last run; keep going if it conflicts
    git -C "$WT" merge --no-edit main || { echo "merge conflict — aborting merge, continuing on branch"; git -C "$WT" merge --abort; }
fi

# 2) run the agent in the isolated worktree (no keys, autonomous)
cd "$WT" || exit 1
echo "--- agent working in $WT on $BRANCH ---"
"$CLAUDE" -p "$(cat "$WT/research/AGENT_PROMPT.md")" \
    --dangerously-skip-permissions 2>&1
echo "--- agent finished (exit $?) ---"

# 3) commit + push whatever the agent produced (review/merge by human)
cd "$WT" || exit 1
if [ -n "$(git status --porcelain)" ]; then
    git add -A
    git commit -m "research: autonomous run $(date '+%Y-%m-%d')" || true
fi
git push origin "$BRANCH" 2>&1 || echo "push failed (review locally)"
echo "RESEARCH AGENT DONE $(date '+%H:%M:%S')"
