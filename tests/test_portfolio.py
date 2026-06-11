"""Cross-sectional momentum portfolio: ranking, weighting, P&L, turnover."""
from hermes_trading import portfolio as pf


def test_momentum_needs_history():
    closes = {"A": [1, 2, 3], "B": list(range(40))}
    mom = pf.momentum(closes, lookback=30, skip=0)
    assert "A" not in mom and "B" in mom


def test_momentum_value():
    closes = {"X": [100.0] * 10 + [110.0]}   # +10% over the last 10 bars
    mom = pf.momentum(closes, lookback=10, skip=0)
    assert abs(mom["X"] - 0.10) < 1e-9


def test_target_weights_long_short_dollar_neutral():
    mom = {"a": -0.3, "b": -0.1, "c": 0.1, "d": 0.3}
    w = pf.target_weights(mom, k=1, allow_short=True, size_total=1.0)
    assert w == {"d": 1.0, "a": -1.0}            # top long, bottom short
    assert abs(sum(v for v in w.values() if v > 0) - 1.0) < 1e-9
    assert abs(sum(v for v in w.values() if v < 0) + 1.0) < 1e-9


def test_target_weights_long_only():
    mom = {"a": -0.3, "b": -0.1, "c": 0.1, "d": 0.3}
    w = pf.target_weights(mom, k=2, allow_short=False, size_total=1.0)
    assert set(w) == {"c", "d"} and all(v > 0 for v in w.values())


def test_target_weights_insufficient_breadth():
    assert pf.target_weights({"a": 0.1}, k=2, allow_short=True, size_total=1.0) == {}


def test_rebalance_pnl_long_and_short():
    weights = {"up": 0.5, "down": -0.5}
    entry = {"up": 100.0, "down": 100.0}
    now = {"up": 110.0, "down": 90.0}            # long +10%, short +10% (price fell)
    pnl = pf.rebalance_pnl(weights, entry, now)
    assert abs(pnl - (0.5 * 0.10 + -0.5 * -0.10)) < 1e-9   # = 0.10


def test_turnover():
    old = {"a": 0.5, "b": -0.5}
    new = {"a": 0.5, "c": -0.5}                  # b out, c in
    assert abs(pf.turnover(old, new) - 1.0) < 1e-9   # |−0.5−0| (b) + |−0.5−0| (c)
