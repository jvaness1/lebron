"""Reflection cycle — look at recent outcomes and change exactly ONE variable.

  python -m hermes_trading.reflect --fallback   # deterministic rule (no LLM)
  python -m hermes_trading.reflect --llm         # Claude-driven (optional, opt-in)

Both modes obey the same guardrail: at most one variable in strategy.yaml
changes per cycle. The prior strategy is archived to state/history/v{NNNN}.yaml
and the reasoning is appended to state/hypotheses.jsonl.

The --llm mode calls the Anthropic API with the Claude model in readable code
you can audit. It is OPTIONAL and never required: --fallback is fully sufficient.
There is no remote install step and no opaque binary.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import List, Optional

import yaml

from . import score

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
GOAL_FILE = STATE_DIR / "goal.yaml"
STRATEGY_FILE = STATE_DIR / "strategy.yaml"
TRADES_FILE = STATE_DIR / "trades.jsonl"
HYPOTHESES_FILE = STATE_DIR / "hypotheses.jsonl"
HISTORY_DIR = STATE_DIR / "history"

CLAUDE_MODEL = "claude-opus-4-8"  # latest Claude when wiring the optional LLM mode


# --------------------------------------------------------------------------- #
# IO
# --------------------------------------------------------------------------- #
def _load_yaml(p: Path) -> dict:
    return yaml.safe_load(p.read_text())


def _load_trades() -> List[dict]:
    if not TRADES_FILE.exists():
        return []
    out = []
    for line in TRADES_FILE.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _archive_and_save(prior: dict, new: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    prior_version = int(str(prior["version"]))
    (HISTORY_DIR / f"v{prior_version:04d}.yaml").write_text(yaml.safe_dump(prior, sort_keys=False))
    STRATEGY_FILE.write_text(yaml.safe_dump(new, sort_keys=False))


def _append_hypothesis(record: dict) -> None:
    with HYPOTHESES_FILE.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def _bump_version(v: str) -> str:
    return f"{int(str(v)) + 1:02d}"


def _closed_count(trades: List[dict]) -> int:
    return sum(1 for t in trades if t.get("status") == "closed")


def _last_reflected_count() -> Optional[int]:
    """How many closed trades existed at the most recent reflection.

    Reflection must only act on NEW evidence. Without this gate, re-running
    reflect on a static trade set keeps applying the same rule (e.g. ratcheting
    stop_loss 1.0 -> 0.8 -> 0.6 on identical stats). Returns None if we've never
    reflected. Reads `closed_n` (written by both modes), falling back to the
    legacy `stats.n` field for older hypotheses.
    """
    if not HYPOTHESES_FILE.exists():
        return None
    last_line: Optional[str] = None
    for line in HYPOTHESES_FILE.read_text().splitlines():
        line = line.strip()
        if line:
            last_line = line
    if not last_line:
        return None
    try:
        rec = json.loads(last_line)
    except json.JSONDecodeError:
        return None
    if isinstance(rec.get("closed_n"), int):
        return rec["closed_n"]
    stats = rec.get("stats") or {}
    return int(stats["n"]) if isinstance(stats.get("n"), int) else None


def _has_new_trades(closed_now: int) -> bool:
    last = _last_reflected_count()
    return last is None or closed_now > last


# --------------------------------------------------------------------------- #
# Deterministic fallback
# --------------------------------------------------------------------------- #
# Bounds keep any single variable from running away over many cycles. Every rule
# clamps to these, and a rule that would land on the current value is skipped so
# we never write a no-op version bump.
BOUNDS = {
    "stop_loss_pct": (0.5, 4.0),
    "entry.threshold": (10.0, 45.0),
    "exit.rsi_take_profit": (60.0, 90.0),
    "position_size_r": (0.1, 1.0),
}


def _clamp(v: float, lo: float, hi: float) -> float:
    return round(max(lo, min(hi, v)), 4)


def _get_path(d: dict, dotted: str):
    node = d
    for key in dotted.split("."):
        node = node[key]
    return node


def _set_path(d: dict, dotted: str, value) -> None:
    path = dotted.split(".")
    node = d
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def _propose(strategy: dict, variable: str, delta: float, rule: str) -> Optional[dict]:
    """Build a one-variable change clamped to BOUNDS, or None if it's a no-op."""
    old = float(_get_path(strategy, variable))
    lo, hi = BOUNDS[variable]
    new_val = _clamp(old + delta, lo, hi)
    if new_val == round(old, 4):
        return None  # already at the bound in this direction — nothing to do
    return {"variable": variable, "old": old, "new": new_val, "rule": rule}


def reflect_fallback(goal: dict, strategy: dict, trades: List[dict]) -> Optional[dict]:
    """Apply exactly one deterministic change. Unlike the original tighten-only
    rules, variables move in EITHER direction based on the dominant failure mode,
    and every move is clamped to BOUNDS so nothing ratchets to an extreme:

      1. Drawdown breach            -> reduce risk: tighten stop (or cut size if floored)
      2. Whipsaw (stopped out a lot,
         but DD headroom to spare)  -> WIDEN stop, let trades breathe
      3. Poor entry quality
         (low win rate)             -> TIGHTEN entry: require a deeper dip
      4. Wins too small vs losses   -> raise rsi_take_profit, let winners run
      5. Underperforming, win rate ok-> LOOSEN entry: participate more
      6. Noisy returns (low sharpe) -> tighten stop to cut variance
    """
    closed = [t for t in trades if t.get("status") == "closed"]
    if not _has_new_trades(len(closed)):
        return None  # no new closed trades since last reflection — nothing to learn

    stats = score.realised_stats(trades)
    current_score = score.score(trades, goal)
    change = None

    if len(closed) >= 5:
        rets = [float(t.get("return_pct", 0.0)) for t in closed]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        win_rate = len(wins) / len(closed)
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = (abs(sum(losses) / len(losses))) if losses else 0.0
        sl_rate = sum(1 for t in closed if t.get("reason") == "stop_loss") / len(closed)
        stop = float(strategy["stop_loss_pct"])
        dd_budget = float(goal["max_drawdown"])

        # 1. Drawdown breach — cut risk. Tighten the stop; if it's already at the
        #    floor, pull position size down instead (still one variable/cycle).
        if stats["drawdown"] > dd_budget:
            change = _propose(strategy, "stop_loss_pct", -0.2,
                              "drawdown > max_drawdown → tighten stop_loss_pct")
            if change is None:
                change = _propose(strategy, "position_size_r", -0.1,
                                  "drawdown > max & stop at floor → cut position_size_r")

        # 2. Whipsaw — getting stopped out a lot while well under the DD budget
        #    means the stop is catching noise. Give it room.
        if change is None and sl_rate >= 0.6 and stats["drawdown"] <= dd_budget * 0.5:
            change = _propose(strategy, "stop_loss_pct", +0.2,
                              "stop-loss exits ≥60% with drawdown headroom → widen stop_loss_pct")

        # 3. Poor entry quality — low win rate means we enter too eagerly. Require
        #    a deeper oversold reading (lower RSI threshold).
        if change is None and win_rate < 0.40:
            change = _propose(strategy, "entry.threshold", -2,
                              "win_rate < 40% → tighten entry.threshold (more selective)")

        # 4. Reward asymmetry — wins much smaller than losses: let winners run by
        #    demanding a higher RSI before taking profit.
        if change is None and wins and losses and avg_win < avg_loss * 0.5:
            change = _propose(strategy, "exit.rsi_take_profit", +5,
                              "avg_win < 0.5*avg_loss → raise rsi_take_profit (let winners run)")

        # 5. Underperformance with an acceptable win rate — not enough
        #    participation. Loosen entry to take more setups.
        if (change is None and win_rate >= 0.40
                and stats["realised_return"] < goal["target_return_30d"]):
            change = _propose(strategy, "entry.threshold", +2,
                              "underperforming with ok win_rate → loosen entry.threshold")

        # 6. Noisy returns — low Sharpe with nothing else flagged: tighten the
        #    stop to shrink return variance.
        if change is None and stats["sharpe"] < goal["min_sharpe"]:
            change = _propose(strategy, "stop_loss_pct", -0.2,
                              "sharpe < min_sharpe → tighten stop_loss_pct")

    if change is None:
        return None

    new = deepcopy(strategy)
    _set_path(new, change["variable"], change["new"])
    new["version"] = _bump_version(strategy["version"])
    _archive_and_save(strategy, new)

    hypothesis = {
        "ts": time.time(),
        "mode": "fallback",
        "from_version": strategy["version"],
        "to_version": new["version"],
        "closed_n": len(closed),
        "stats": stats,
        "score_before": current_score,
        "predicted_score_direction": "up",
        **change,
    }
    _append_hypothesis(hypothesis)
    return hypothesis


# ---------------------------------------------------------------------------
# Policy: minimum return floor
# ---------------------------------------------------------------------------
def _passes_min_return_floor(trade: Mapping, min_floor: float) -> bool:
    """Return False if the trade's return is below the min-return floor.

    Breakeven / sub-floor trades are excluded from reflection counting so the
    strategy doesn't churn on noise.
    """
    return float(trade.get("return_pct", 0.0) * 100.0) >= min_floor


# ---------------------------------------------------------------------------
# Policy: feature-aware entry filters
# ---------------------------------------------------------------------------
def _passes_feature_filters(trade: Mapping, strategy: Mapping) -> bool:
    feats = (trade.get("features") or {}).get("1min") or {}
    if not feats:
        return True  # features missing => accept but reflect can learn later

    bull_count = feats.get("bull_count")
    min_bull = strategy.get("entry", {}).get("min_bull_count")
    if isinstance(min_bull, int) and isinstance(bull_count, int):
        if bull_count < min_bull:
            return False

    adx = feats.get("adx")
    min_adx = strategy.get("entry", {}).get("min_adx")
    if isinstance(min_adx, (int, float)) and isinstance(adx, (int, float)):
        if adx < float(min_adx):
            return False

    return True


# ---------------------------------------------------------------------------
# Policy: balance scaling
# ---------------------------------------------------------------------------
def scored_trades(
    trades: List[dict], goal: Mapping, strategy: Mapping
) -> List[dict]:
    """Return only trades that pass the configured quality gates."""
    min_floor = float(strategy.get("min_return_floor", 0.0))
    return [
        t
        for t in trades
        if t.get("status") == "closed"
        and _passes_min_return_floor(t, min_floor)
        and _passes_feature_filters(t, strategy)
    ]


# ---------------------------------------------------------------------------
# Policy: cooldown after stop-loss
# ---------------------------------------------------------------------------
def last_stop_loss_ts(trades: List[dict]) -> Optional[float]:
    for t in reversed(trades):
        if t.get("status") == "closed" and t.get("reason") == "stop_loss":
            return t.get("exit_ts")
    return None


def in_cooldown(
    trades: List[dict], strategy: Mapping, now: Optional[float] = None
) -> bool:
    now = now if now is not None else time.time()
    cooldown_mins = float(strategy.get("cooldown_minutes", 0))
    if cooldown_mins <= 0:
        return False
    last_sl = last_stop_loss_ts(trades)
    if last_sl is None:
        return False
    return (now - float(last_sl)) < cooldown_mins * 60.0


# --------------------------------------------------------------------------- #
# Feature context for reflection
# --------------------------------------------------------------------------- #
def _feature_table(trades: List[dict]) -> str:
    """Compress each trade's entry snapshot into one compact, learnable line:
    win/loss, return, the 1-minute bullish tally and a few key indicators."""
    rows = ["outcome  ret%    bull/21  rsi   adx   macd_h  superT"]
    for t in trades:
        feats = (t.get("features") or {}).get("1min") or {}
        outcome = "WIN " if t.get("return_pct", 0) > 0 else "LOSS"
        ret = t.get("return_pct", 0) * 100
        bull = feats.get("bull_count")
        rsi = feats.get("rsi")
        adx = feats.get("adx")
        mh = feats.get("macd_hist")
        st = feats.get("supertrend_bull")

        def _f(v, w, nd=1):
            return f"{v:>{w}.{nd}f}" if isinstance(v, (int, float)) else f"{'--':>{w}}"

        rows.append(
            f"{outcome}   {ret:>+6.2f}  {str(bull) if bull is not None else '--':>5}  "
            f"{_f(rsi, 4)}  {_f(adx, 4)}  {_f(mh, 6, 4)}  "
            f"{('yes' if st else 'no') if st is not None else '--':>4}"
        )
    return "\n".join(rows)


# --------------------------------------------------------------------------- #
# Optional Claude-driven mode (readable, auditable, opt-in)
# --------------------------------------------------------------------------- #
def reflect_llm(goal: dict, strategy: dict, trades: List[dict]) -> Optional[dict]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit(
            "--llm needs ANTHROPIC_API_KEY in your environment.\n"
            "Get one at https://console.anthropic.com/ and `export ANTHROPIC_API_KEY=...`,\n"
            "or just use --fallback (no key, fully deterministic)."
        )
    try:
        import anthropic
    except ImportError:
        raise SystemExit(
            "--llm needs the anthropic SDK. Install it with:\n"
            "  uv add anthropic\n"
            "or use --fallback instead."
        )

    closed_all = [t for t in trades if t.get("status") == "closed"]
    if not _has_new_trades(len(closed_all)):
        return None  # no new closed trades since last reflection — skip the API call

    recent = closed_all[-25:]
    feature_table = _feature_table(recent)
    prompt = (
        "You tune a long-only RSI paper-trading strategy. Propose exactly ONE "
        "variable to change in the strategy YAML to better hit the goal.\n\n"
        f"GOAL:\n{yaml.safe_dump(goal, sort_keys=False)}\n"
        f"CURRENT STRATEGY:\n{yaml.safe_dump(strategy, sort_keys=False)}\n"
        f"LAST {len(recent)} CLOSED TRADES:\n{json.dumps(recent, indent=2)}\n\n"
        "MARKET CONTEXT AT EACH ENTRY (a 21-indicator scan captured when the "
        "position opened; `bull` = how many of the 21 indicators read bullish on "
        "the 1-minute timeframe, 0=fully bearish tape, 21=fully bullish). The "
        "engine only decides on RSI, but these features reveal WHICH conditions "
        "preceded winners vs losers — use them to inform the one change:\n"
        f"{feature_table}\n\n"
        "Reply ONLY with JSON: "
        '{"variable": "<dotted.path>", "new_value": <number>, "reasoning": "<one sentence>"}'
    )
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    proposal = json.loads(text[text.index("{"): text.rindex("}") + 1])

    new = deepcopy(strategy)
    path = proposal["variable"].split(".")
    node = new
    for key in path[:-1]:
        node = node[key]
    old = node[path[-1]]
    node[path[-1]] = proposal["new_value"]
    new["version"] = _bump_version(strategy["version"])
    _archive_and_save(strategy, new)

    hypothesis = {
        "ts": time.time(), "mode": "llm", "model": CLAUDE_MODEL,
        "from_version": strategy["version"], "to_version": new["version"],
        "closed_n": len(closed_all),
        "variable": proposal["variable"], "old": old, "new": proposal["new_value"],
        "reasoning": proposal.get("reasoning", ""),
        "predicted_score_direction": "up",
    }
    _append_hypothesis(hypothesis)
    return hypothesis


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(prog="hermes_trading.reflect")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--fallback", action="store_true", help="deterministic, no LLM")
    grp.add_argument("--llm", action="store_true", help="Claude-driven (needs ANTHROPIC_API_KEY)")
    args = ap.parse_args()

    goal = _load_yaml(GOAL_FILE)
    strategy = _load_yaml(STRATEGY_FILE)
    trades = _load_trades()

    fn = reflect_llm if args.llm else reflect_fallback
    hypothesis = fn(goal, strategy, trades)

    if hypothesis is None:
        print("Goal currently met — no variable changed (strategy held at "
              f"v{strategy['version']}).")
    else:
        print(f"Reflected (v{hypothesis['from_version']} → v{hypothesis['to_version']}): "
              f"changed {hypothesis['variable']} "
              f"{hypothesis['old']} → {hypothesis['new']}")


if __name__ == "__main__":
    main()
