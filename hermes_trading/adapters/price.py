"""Price adapter — OHLCV candles from a public exchange via ccxt.

Free/public by default (no API key). Some exchanges (e.g. Binance) geo-block
certain regions with HTTP 451, so this tries a list of keyless, widely-reachable
exchanges in order and uses the first that returns data. Set EXCHANGE_ID to pin
a specific one. If EXCHANGE_API_KEY / EXCHANGE_API_SECRET are set they're passed
through, but they are never required for reading public data.
"""
from __future__ import annotations

import asyncio
import os
from typing import List, Optional

import ccxt

SCHEMA_VERSION = "price.v1"

# Tried in order. Kraken/Coinbase are reachable from most regions keyless.
_CANDIDATES = ["kraken", "coinbase", "kucoin", "bybit", "binance"]

# Symbol fallbacks if the exact pair isn't listed on a given exchange.
_SYMBOL_FALLBACKS = {"USDT": ["USDT", "USD"]}


def _client(exchange_id: str) -> "ccxt.Exchange":
    cfg = {"enableRateLimit": True}
    key, secret = os.getenv("EXCHANGE_API_KEY"), os.getenv("EXCHANGE_API_SECRET")
    if key and secret:
        cfg.update(apiKey=key, secret=secret)
    return getattr(ccxt, exchange_id)(cfg)


def _symbol_variants(asset: str) -> List[str]:
    base, _, quote = asset.partition("/")
    variants = _SYMBOL_FALLBACKS.get(quote, [quote])
    return [f"{base}/{q}" for q in variants]


def _pull_sync(asset: str, timeframe: str, limit: int) -> Optional[dict]:
    pinned = os.getenv("EXCHANGE_ID")
    exchanges = [pinned] if pinned else _CANDIDATES
    last_err: Optional[Exception] = None
    for ex_id in exchanges:
        try:
            client = _client(ex_id)
            client.load_markets()
            for symbol in _symbol_variants(asset):
                if symbol not in client.markets:
                    continue
                candles = client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
                if candles:
                    return {"exchange": ex_id, "symbol": symbol, "candles": candles}
        except Exception as exc:  # noqa: BLE001  try the next exchange
            last_err = exc
            continue
    if last_err:
        raise last_err
    raise RuntimeError(f"no reachable exchange listed {asset}")


def _pull_history_sync(asset: str, timeframe: str, total: int) -> Optional[dict]:
    """Page through fetch_ohlcv to assemble up to `total` candles.

    ccxt caps a single fetch (typically ~720–1000 bars), so for a real backtest
    we walk `since` forward in chunks from `total` bars ago to now.
    """
    pinned = os.getenv("EXCHANGE_ID")
    exchanges = [pinned] if pinned else _CANDIDATES
    per_call = 1000
    last_err: Optional[Exception] = None
    for ex_id in exchanges:
        try:
            client = _client(ex_id)
            client.load_markets()
            tf_ms = client.parse_timeframe(timeframe) * 1000
            for symbol in _symbol_variants(asset):
                if symbol not in client.markets:
                    continue
                since = client.milliseconds() - total * tf_ms
                out: List[list] = []
                while len(out) < total:
                    batch = client.fetch_ohlcv(symbol, timeframe=timeframe,
                                               since=since, limit=per_call)
                    if not batch:
                        break
                    # De-dup the overlap boundary between consecutive pages.
                    if out and batch[0][0] <= out[-1][0]:
                        batch = [c for c in batch if c[0] > out[-1][0]]
                    if not batch:
                        break
                    out.extend(batch)
                    since = batch[-1][0] + tf_ms
                    if len(batch) < per_call:
                        break  # reached the live edge
                if out:
                    return {"exchange": ex_id, "symbol": symbol, "candles": out[:total]}
        except Exception as exc:  # noqa: BLE001  try the next exchange
            last_err = exc
            continue
    if last_err:
        raise last_err
    raise RuntimeError(f"no reachable exchange listed {asset}")


async def fetch_history(asset: str = "SOL/USDT", timeframe: str = "1m",
                        total: int = 5000) -> dict:
    """Like fetch(), but returns up to `total` historical candles (paginated)."""
    result = await asyncio.to_thread(_pull_history_sync, asset, timeframe, total)
    candles = result["candles"]
    closes = [c[4] for c in candles]
    return {
        "schema_version": SCHEMA_VERSION,
        "asset": asset,
        "exchange": result["exchange"],
        "symbol": result["symbol"],
        "timeframe": timeframe,
        "candles": candles,
        "closes": closes,
        "last": closes[-1] if closes else None,
    }


async def fetch(asset: str = "SOL/USDT", timeframe: str = "1m", limit: int = 200) -> dict:
    result = await asyncio.to_thread(_pull_sync, asset, timeframe, limit)
    candles = result["candles"]
    closes = [c[4] for c in candles]
    return {
        "schema_version": SCHEMA_VERSION,
        "asset": asset,
        "exchange": result["exchange"],
        "symbol": result["symbol"],
        "timeframe": timeframe,
        "candles": candles,          # [ms_ts, open, high, low, close, volume]
        "closes": closes,
        "last": closes[-1] if closes else None,
    }
