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


def test_bull_score_uptrend_vs_downtrend():
    up = [100 + i for i in range(60)]            # steady uptrend → strongly bullish
    down = [100 - i * 0.5 for i in range(60)]    # downtrend → bearish
    assert pf.bull_score_last(up) >= 8
    assert pf.bull_score_last(down) <= 2
    assert pf.bull_score_last([1, 2, 3]) == 0    # too little history → 0


def test_market_breadth():
    up = [100 + i for i in range(60)]
    down = [100 - i * 0.5 for i in range(60)]
    universe = {"a": up, "b": up, "c": down, "d": down}   # 2 of 4 bullish
    assert abs(pf.market_breadth(universe, bull_min=6) - 0.5) < 1e-9
    assert pf.market_breadth({"x": up, "y": up}, bull_min=6) == 1.0
    assert pf.market_breadth({"x": down}, bull_min=6) == 0.0
