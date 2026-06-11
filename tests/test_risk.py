"""Risk kill switch — drawdown, daily-loss, and loss-streak gates."""
from datetime import datetime, timezone

from hermes_trading import risk


def _t(ret, ts=0.0):
    return {"status": "closed", "return_pct": ret, "exit_ts": ts}


def test_empty_config_always_allows():
    v = risk.evaluate({}, [_t(-0.5)])
    assert v["allow_entry"] is True


def test_drawdown_halt():
    cfg = {"halt_on_drawdown": 0.08}
    trades = [_t(-0.05), _t(-0.05)]  # ~9.75% compounded drawdown
    v = risk.evaluate(cfg, trades)
    assert v["allow_entry"] is False and "drawdown" in v["reason"]


def test_drawdown_ok_when_under_budget():
    cfg = {"halt_on_drawdown": 0.08}
    assert risk.evaluate(cfg, [_t(-0.02)])["allow_entry"] is True


def test_daily_loss_limit():
    cfg = {"daily_loss_limit_pct": 0.03}
    now = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc).timestamp()
    same_day = datetime(2026, 6, 11, 9, 0, tzinfo=timezone.utc).timestamp()
    v = risk.evaluate(cfg, [_t(-0.02, same_day), _t(-0.02, same_day)], now)
    assert v["allow_entry"] is False and "daily_loss_limit" in v["reason"]


def test_daily_loss_ignores_other_days():
    cfg = {"daily_loss_limit_pct": 0.03}
    now = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc).timestamp()
    yesterday = datetime(2026, 6, 10, 9, 0, tzinfo=timezone.utc).timestamp()
    v = risk.evaluate(cfg, [_t(-0.05, yesterday)], now)
    assert v["allow_entry"] is True  # yesterday's loss doesn't count today


def test_consecutive_losses():
    cfg = {"max_consecutive_losses": 4}
    losses = [_t(-0.01) for _ in range(4)]
    assert risk.evaluate(cfg, losses)["allow_entry"] is False
    # A win breaks the streak.
    assert risk.evaluate(cfg, losses + [_t(0.02)])["allow_entry"] is True
