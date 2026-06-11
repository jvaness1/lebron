"""hermes_trading — a local, paper-only trading research bot.

Nothing in this package can place a real order. The trading engine only
ever simulates fills against live/historical prices and records the
outcomes to state/trades.jsonl. There is no live-execution adapter.
"""

__version__ = "0.2.0"
