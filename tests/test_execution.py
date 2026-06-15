"""Money-critical reconciliation logic — the part that decides real orders. Tested hard."""
from hermes_trading.execution import reconcile, Order, CoinbaseBroker

CAPS = dict(min_order_usd=1.0, max_order_usd=50.0, max_total_usd=200.0)


def _by(orders):
    return {(o.base, o.side): o.usd for o in orders}


def test_all_cash_enters_targets():
    # $100 cash, target BTC 20% / ETH 20% → buy each up to deploy cap
    orders = reconcile({"BTC": 0.2, "ETH": 0.2}, {}, 100.0, **CAPS)
    b = _by(orders)
    assert b[("BTC", "buy")] == 20.0 and b[("ETH", "buy")] == 20.0
    assert all(o.side == "buy" for o in orders)


def test_sell_overweight_position():
    # hold $50 BTC, target 0 → sell it all
    orders = reconcile({}, {"BTC": 50.0}, 0.0, **CAPS)
    assert _by(orders)[("BTC", "sell")] == 50.0


def test_never_sells_more_than_held():
    orders = reconcile({}, {"BTC": 10.0}, 0.0, **CAPS)
    assert _by(orders)[("BTC", "sell")] == 10.0   # not more than the $10 held


def test_per_order_cap_enforced():
    # target wants $200 of BTC but per-order cap is $50
    orders = reconcile({"BTC": 1.0}, {}, 1000.0, **CAPS)
    assert all(o.usd <= 50.0 for o in orders)


def test_total_cap_enforced():
    # $1000 cash, target 5 coins equal — total deployed must not exceed $200
    tw = {c: 0.2 for c in ("BTC", "ETH", "SOL", "XRP", "ADA")}
    orders = reconcile(tw, {}, 1000.0, **CAPS)
    assert sum(o.usd for o in orders) <= 200.0 + 1e-6


def test_buys_limited_by_cash():
    # only $30 cash, no sells available → can't buy more than ~$30 total
    orders = reconcile({"BTC": 0.5, "ETH": 0.5}, {}, 30.0, **CAPS)
    assert sum(o.usd for o in orders if o.side == "buy") <= 30.0 + 1e-6


def test_dust_below_min_skipped():
    # target only $0.50 of BTC → below $1 min → no order
    orders = reconcile({"BTC": 0.005}, {}, 100.0, **CAPS)
    assert orders == []


def test_long_only_ignores_negative_target():
    # a negative (short) weight must never produce a sell-to-short
    orders = reconcile({"BTC": -0.5}, {}, 100.0, **CAPS)
    assert all(o.side != "sell" or o.base in {} for o in orders)
    assert not any(o.base == "BTC" and o.side == "sell" for o in orders)  # nothing held to sell


def test_rebalance_sell_then_buy():
    # hold $100 BTC, want to rotate to ETH (target ETH 100% of $200-capped deploy)
    orders = reconcile({"ETH": 1.0}, {"BTC": 100.0}, 0.0, **CAPS)
    sides = {(o.base, o.side) for o in orders}
    assert ("BTC", "sell") in sides and ("ETH", "buy") in sides


def test_broker_defaults_to_dryrun_without_keys(monkeypatch):
    monkeypatch.delenv("COINBASE_API_KEY", raising=False)
    monkeypatch.delenv("COINBASE_API_SECRET", raising=False)
    b = CoinbaseBroker(live=True)              # asked for live...
    assert b.live is False                     # ...but no keys → forced dry-run
    assert "DRY-RUN" in b.mode()


def test_broker_execute_dryrun_places_nothing(monkeypatch):
    monkeypatch.delenv("COINBASE_API_KEY", raising=False)
    monkeypatch.delenv("COINBASE_API_SECRET", raising=False)
    b = CoinbaseBroker(live=False)
    rec = b.execute([Order("BTC", "buy", 20.0)], {"BTC": 50000.0})
    assert rec[0]["status"] == "dry_run"
