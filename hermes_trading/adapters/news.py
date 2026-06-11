"""News adapter — keyless neutral sentiment by default.

Returns a well-formed neutral payload unless NEWS_API_KEY is set, in which case
you can extend `fetch` to pull and score headlines. The strategy does not
require this signal; it's wired in so the loop's multi-adapter contract is real.
"""
from __future__ import annotations

import os

SCHEMA_VERSION = "news.v1"


async def fetch(asset: str = "SOL/USDT") -> dict:
    has_key = bool(os.getenv("NEWS_API_KEY"))
    return {
        "schema_version": SCHEMA_VERSION,
        "asset": asset,
        "source": "newsapi" if has_key else "neutral-default",
        "headline_count": 0,
        "sentiment": 0.0,  # [-1, +1]
    }
