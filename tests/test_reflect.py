"""Reflection — new-trade gate, bidirectional moves, and bound clamps."""
import yaml
import pytest

from hermes_trading import reflect


GOAL = {"target_return_30d": 0.10, "max_drawdown": 0.08, "min_sharpe": 1.2,
        "failure_below": -0.04, "reflection_every": 5}
BASE = {"version": "01", "entry": {"threshold": 32.0},
        "exit": {"rsi_take_profit": 80.0, "min_profit_pct": 0.0},
        "stop_loss_pct": 1.0, "position_size_r": 0.5}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(reflect, "HYPOTHESES_FILE", tmp_path / "h.jsonl")
    monkeypatch.setattr(reflect, "STRATEGY_FILE", tmp_path / "s.yaml")
    monkeypatch.setattr(reflect, "HISTORY_DIR", tmp_path / "hist")
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(BASE))
    return tmp_path


def _trades(n, ret, reason="stop_loss"):
    return [{"status": "closed", "return_pct": ret, "reason": reason} for _ in range(n)]


def test_gate_no_op_on_same_data(isolated):
    trades = _trades(6, -0.002)
    first = reflect.reflect_fallback(GOAL, BASE, trades)
    assert first is not None
    after = yaml.safe_load((isolated / "s.yaml").read_text())
    second = reflect.reflect_fallback(GOAL, after, trades)   # same 6 trades
    assert second is None                                    # gate holds


def test_whipsaw_widens_stop(isolated):
    # 5 small stop-loss losses, tiny drawdown → widen stop (UP).
    h = reflect.reflect_fallback(GOAL, BASE, _trades(5, -0.002, "stop_loss"))
    assert h["variable"] == "stop_loss_pct" and h["new"] > h["old"]


def test_low_winrate_tightens_entry(isolated):
    trades = _trades(1, 0.05, "take_profit") + _trades(4, -0.02, "stop_loss")
    h = reflect.reflect_fallback(GOAL, BASE, trades)
    assert h["variable"] == "entry.threshold" and h["new"] < h["old"]


def test_asymmetry_raises_take_profit(isolated):
    trades = _trades(3, 0.005, "take_profit") + _trades(2, -0.03, "stop_loss")
    h = reflect.reflect_fallback(GOAL, BASE, trades)
    assert h["variable"] == "exit.rsi_take_profit" and h["new"] > h["old"]


def test_underperf_with_ok_winrate_loosens_entry(isolated):
    trades = _trades(4, 0.01, "take_profit") + _trades(1, -0.005, "stop_loss")
    h = reflect.reflect_fallback(GOAL, BASE, trades)
    assert h["variable"] == "entry.threshold" and h["new"] > h["old"]


def test_stop_floored_cuts_position_size(isolated):
    floored = dict(BASE, stop_loss_pct=0.5)  # already at the floor
    (isolated / "s.yaml").write_text(yaml.safe_dump(floored))
    h = reflect.reflect_fallback(GOAL, floored, _trades(5, -0.05, "stop_loss"))
    assert h["variable"] == "position_size_r" and h["new"] < h["old"]


def test_bounds_never_exceeded(isolated):
    lo, hi = reflect.BOUNDS["stop_loss_pct"]
    assert reflect._clamp(99, lo, hi) == hi
    assert reflect._clamp(-99, lo, hi) == lo


def test_fewer_than_five_trades_no_change(isolated):
    assert reflect.reflect_fallback(GOAL, BASE, _trades(3, -0.02)) is None
