"""Performance metrics."""
from hermes_trading import metrics


def _t(ret, gross=None, ts_in=0.0, ts_out=1.0):
    d = {"status": "closed", "return_pct": ret, "entry_ts": ts_in, "exit_ts": ts_out}
    if gross is not None:
        d["gross_return_pct"] = gross
        d["cost_pct"] = gross - ret
    return d


def test_empty():
    assert metrics.summary([])["n"] == 0


def test_basic_counts_and_winrate():
    s = metrics.summary([_t(0.02), _t(-0.01), _t(0.03)])
    assert s["n"] == 3 and s["wins"] == 2 and s["losses"] == 1
    assert abs(s["win_rate"] - 2 / 3) < 1e-9


def test_profit_factor_and_expectancy():
    s = metrics.summary([_t(0.04), _t(-0.02)])
    assert abs(s["profit_factor"] - 2.0) < 1e-9
    assert abs(s["expectancy"] - 0.01) < 1e-9


def test_max_drawdown_positive_on_losses():
    s = metrics.summary([_t(0.10), _t(-0.20)])
    assert s["max_drawdown"] > 0


def test_cost_drag_reported_when_present():
    s = metrics.summary([_t(0.01, gross=0.013), _t(-0.005, gross=-0.002)])
    assert s["gross_return_additive"] is not None
    assert s["cost_drag_total"] is not None


def test_format_runs():
    out = metrics.format_summary(metrics.summary([_t(0.01), _t(-0.01)]), "X")
    assert "trades" in out and "profit factor" in out
