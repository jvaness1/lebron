"""Engine: RSI, entry/exit, cost model, cost-aware take-profit, cooldown."""
from hermes_trading import loop


def _rising(n=30, start=50.0, step=1.0):
    return [start + i * step for i in range(n)]


def test_rsi_none_until_warm():
    assert loop.rsi([1, 2, 3]) is None  # < period+1


def test_rsi_all_gains_is_100():
    assert loop.rsi(_rising(20)) == 100.0


def _engine(**costs):
    strat = {
        "version": "t", "entry": {"threshold": 32.0},
        "exit": {"rsi_take_profit": 70, "min_profit_pct": 0.0},
        "stop_loss_pct": 2.0, "position_size_r": 1.0,
        "costs": costs,
    }
    return loop.PaperEngine(strat, "SOL/USDT")


def test_entry_then_stop_loss_path():
    eng = _engine()
    # Downtrend pushes RSI under 32 → entry; deep drop → stop-loss.
    prices = [100 - i * 0.8 for i in range(40)]
    closed = [t for p, i in zip(prices, range(len(prices)))
              for t in [eng.on_bar(p, float(i))] if t]
    assert any(t["reason"] == "stop_loss" for t in closed)


def test_costs_reduce_return_below_gross():
    eng = _engine(fees_bps=10.0, slippage_bps=5.0)
    # Flat round-trip: gross 0, but costs make net clearly negative.
    net = eng._net_return(100.0, 100.0)
    assert net < 0
    assert abs(net - (-0.003)) < 1e-3  # ~ -0.30% for 10bps fee + 5bps slip per side


def test_zero_costs_net_equals_gross():
    eng = _engine(fees_bps=0.0, slippage_bps=0.0)
    assert abs(eng._net_return(100.0, 101.0) - 0.01) < 1e-9


def test_take_profit_requires_net_profit_under_costs():
    eng = _engine(fees_bps=10.0, slippage_bps=5.0)
    eng.closes = _rising(30)            # RSI high
    assert loop.rsi(eng.closes) >= 70
    eng.position = {"entry": 100.0, "ts": 0.0, "entry_rsi": 80.0, "features": None}
    # +0.1% gross is NOT enough to clear ~0.3% round-trip costs → must hold.
    assert eng.on_bar(100.1, 1.0) is None and eng.position is not None
    # +0.5% gross clears costs → take-profit fires with a positive NET return.
    t = eng.on_bar(100.5, 2.0)
    assert t is not None and t["reason"] == "take_profit" and t["return_pct"] > 0


def test_close_records_gross_and_cost():
    eng = _engine(fees_bps=10.0, slippage_bps=5.0)
    eng.position = {"entry": 100.0, "ts": 0.0, "entry_rsi": 25.0, "features": None}
    t = eng._close(101.0, 10.0, 60.0, reason="take_profit")
    assert t["gross_return_pct"] > t["return_pct"]          # costs ate some edge
    assert abs(t["cost_pct"] - (t["gross_return_pct"] - t["return_pct"])) < 1e-9
    assert eng.closed_trades and eng.closed_trades[-1] is t  # tracked in memory


def _donchian_engine(lookback=3, exit_n=2, stop=50.0):
    strat = {
        "version": "t", "timeframe": "1h",
        "entry": {"indicator": "donchian", "breakout_lookback": lookback},
        "exit": {"donchian_exit": exit_n}, "stop_loss_pct": stop,
        "position_size_r": 1.0, "costs": {},
    }
    return loop.PaperEngine(strat, "SOL/USDT")


def _bar(ts, o, h, l, c):
    return [ts * 1000, o, h, l, c, 1.0]


def test_donchian_entry_on_breakout_and_exit_on_channel_low():
    eng = _donchian_engine()
    bars = [
        _bar(0, 100, 100, 99, 100),
        _bar(1, 100, 100, 99, 100),
        _bar(2, 100, 100, 99, 100),
        _bar(3, 100, 101, 99, 101),   # breaks prior 3-bar high (100) → ENTRY
        _bar(4, 101, 102, 100, 102),  # hold
        _bar(5, 100, 100, 95, 96),    # breaks prior 2-bar low (99) → donchian_exit
    ]
    closed = [t for b in bars for t in [eng.on_bar(b[4], b[0] / 1000.0, bar=b)] if t]
    assert len(closed) == 1 and closed[0]["reason"] == "donchian_exit"
    assert closed[0]["entry"] == 101


def test_donchian_intrabar_stop_fills_at_stop_price():
    eng = _donchian_engine(stop=4.0)  # 4% stop
    bars = [
        _bar(0, 100, 100, 99, 100), _bar(1, 100, 100, 99, 100),
        _bar(2, 100, 100, 99, 100),
        _bar(3, 100, 101, 99, 101),    # ENTRY at 101
        _bar(4, 100, 101, 90, 95),     # low 90 < stop (101*0.96=96.96) → STOP
    ]
    closed = [t for b in bars for t in [eng.on_bar(b[4], b[0] / 1000.0, bar=b)] if t]
    assert len(closed) == 1 and closed[0]["reason"] == "stop_loss"
    assert abs(closed[0]["exit"] - 101 * 0.96) < 1e-9   # filled at stop, not the low


def test_donchian_no_entry_before_enough_history():
    eng = _donchian_engine(lookback=3)
    # Only 2 prior bars → channel undefined → no entry even on a high close.
    assert eng.on_bar(100, 0.0, bar=_bar(0, 100, 100, 99, 100)) is None
    assert eng.on_bar(200, 1.0, bar=_bar(1, 100, 200, 99, 200)) is None
    assert eng.position is None


def test_cooldown_blocks_entry_after_stop_loss():
    strat = {
        "version": "t", "entry": {"threshold": 90.0},  # trivially easy entry
        "exit": {"rsi_take_profit": 70}, "stop_loss_pct": 2.0,
        "position_size_r": 1.0, "cooldown_minutes": 30,
    }
    eng = loop.PaperEngine(strat, "SOL/USDT")
    eng.closed_trades = [{"status": "closed", "reason": "stop_loss", "exit_ts": 1000.0}]
    assert eng._cooldown_remaining(now=1000.0 + 60) > 0      # within 30-min window
    assert eng._cooldown_remaining(now=1000.0 + 31 * 60) == 0  # window elapsed
