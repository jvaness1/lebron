"""Macro adapter — free market context via yfinance (keyless).

Pulls the latest level of a broad risk proxy (default: S&P 500 via ^GSPC) and a
short trend reading. yfinance is keyless and free. On any failure it degrades to
a neutral payload rather than halting the loop, since macro is context, not the
trade trigger.
"""
from __future__ import annotations

import asyncio
import os

SCHEMA_VERSION = "macro.v1"

_TICKER = os.getenv("MACRO_TICKER", "^GSPC")


async def fetch(asset: str = "SOL/USDT") -> dict:
    def _pull() -> dict:
        import yfinance as yf

        hist = yf.Ticker(_TICKER).history(period="5d", interval="1d")
        closes = list(hist["Close"].values)
        if len(closes) < 2:
            raise ValueError("insufficient macro history")
        trend = (closes[-1] - closes[0]) / closes[0]
        return {"last": float(closes[-1]), "trend_5d": float(trend)}

    try:
        data = await asyncio.to_thread(_pull)
        source = _TICKER
    except Exception:
        data = {"last": None, "trend_5d": 0.0}
        source = "neutral-default"

    return {
        "schema_version": SCHEMA_VERSION,
        "ticker": _TICKER,
        "source": source,
        **data,
    }
