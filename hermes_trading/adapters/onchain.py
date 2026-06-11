"""On-chain adapter — placeholder for keyless on-chain signal.

Defaults to a neutral, keyless payload so the loop never depends on a paid
provider. If GLASSNODE_API_KEY is set you can extend `fetch` to pull real
metrics; until then it returns a well-formed neutral signal.
"""
from __future__ import annotations

import os

SCHEMA_VERSION = "onchain.v1"


async def fetch(asset: str = "SOL/USDT") -> dict:
    has_key = bool(os.getenv("GLASSNODE_API_KEY"))
    return {
        "schema_version": SCHEMA_VERSION,
        "asset": asset,
        "source": "glassnode" if has_key else "neutral-default",
        # Neutral by construction; extend when a key is present.
        "active_addresses_trend": 0.0,
        "exchange_netflow_trend": 0.0,
    }
