"""P13 — throttled-fetch + local OHLCV cache (unblocks P6 longer-history tests).

The #1 caveat on every prior finding is sample size: ~3yr survivors-only, one venue,
one OOS window. P6 tried to fix it by fetching total=3000 daily x 60 coins concurrently
and got RATE-LIMITED by KuCoin (partial/failed data -> all-zeros). The fix is not more
concurrency, it is LESS: fetch SERIALLY, throttle, retry with backoff, and CACHE to disk
so we pay the slow full fetch once and every later backtest reads the cache instantly
(and only fetches the few new bars since the last run).

Cache layout (gitignored, regenerable):
    data/ohlcv/<exchange>/<timeframe>/<BASE>_<QUOTE>.csv   # ts,open,high,low,close,volume

Design choices (deliberate, for honesty + robustness):
  * SERIAL fetch, ccxt enableRateLimit ON + an extra inter-call sleep, exponential
    backoff on errors. Slow but reliable (this is what P6 lacked).
  * INCREMENTAL: re-running only fetches bars after the last cached timestamp, so the
    cache stays fresh cheaply. Overlap bars are de-duped.
  * MAX history: pages forward from a fixed early epoch to the live edge, so we capture
    each coin's full life (BTC/ETH back to ~2018 -> a ~6-7yr, multi-cycle window).
  * No new deps: CSV (pandas 3.0 here has no pyarrow/fastparquet). Same purpose as parquet.

CLI:
    EXCHANGE_ID=kucoin python scripts/data_cache.py --update            # live universe
    EXCHANGE_ID=kucoin python scripts/data_cache.py --update --symbols BTC/USDT ETH/USDT
    python scripts/data_cache.py --status                               # what's cached

Library use (other scripts):
    from data_cache import load_panel
    panel = load_panel(symbols, timeframe="1d")   # close-price DataFrame, ragged, ts index
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List, Optional

import ccxt
import numpy as np
import pandas as pd
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_ROOT = os.path.join(REPO, "data", "ohlcv")
EARLY_EPOCH_MS = 1_504_224_000_000  # 2017-09-01; ~KuCoin's founding (skips forward if earlier)
PER_CALL = 1000                     # ccxt page size
INTER_CALL_SLEEP = 0.35            # extra throttle on top of ccxt's rateLimit (seconds)
MAX_RETRIES = 5


def _exchange_id() -> str:
    return os.getenv("EXCHANGE_ID", "kucoin")


def _client():
    cfg = {"enableRateLimit": True}
    return getattr(ccxt, _exchange_id())(cfg)


def _csv_path(symbol: str, timeframe: str, exchange: Optional[str] = None) -> str:
    ex = exchange or _exchange_id()
    base, _, quote = symbol.partition("/")
    d = os.path.join(CACHE_ROOT, ex, timeframe)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{base}_{quote}.csv")


def _read_cached(symbol: str, timeframe: str, exchange: Optional[str] = None) -> pd.DataFrame:
    p = _csv_path(symbol, timeframe, exchange)
    if not os.path.exists(p):
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    df = pd.read_csv(p)
    return df.sort_values("ts").drop_duplicates("ts")


def _fetch_pages(client, symbol: str, timeframe: str, since_ms: int) -> List[list]:
    """Page forward from since_ms to the live edge, serially, with backoff.

    KuCoin honors `since` (returns `limit` bars forward from it) but returns EMPTY when
    the window predates the coin's listing. So a leading empty page does NOT mean "no
    data" — we skip the window forward (up to now) until bars appear, then page to the
    live edge. Only an empty page AFTER we already have data means we hit the live edge.
    """
    tf_ms = client.parse_timeframe(timeframe) * 1000
    now = client.milliseconds()
    out: List[list] = []
    since = since_ms
    while since < now:
        batch = None
        for attempt in range(MAX_RETRIES):
            try:
                batch = client.fetch_ohlcv(symbol, timeframe=timeframe,
                                           since=since, limit=PER_CALL)
                break
            except Exception as exc:  # noqa: BLE001 — throttle/network; retry w/ backoff
                wait = (2 ** attempt) * 1.0
                print(f"      retry {attempt+1}/{MAX_RETRIES} ({type(exc).__name__}) "
                      f"sleep {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
        if batch is None:
            raise RuntimeError(f"{symbol}: exhausted retries fetching page @ since={since}")
        if not batch:
            if out:
                break                       # past the live edge / a gap after real data
            since += PER_CALL * tf_ms       # leading empty window: skip forward to listing
            continue
        if out and batch[0][0] <= out[-1][0]:
            batch = [c for c in batch if c[0] > out[-1][0]]
        if not batch:
            break
        out.extend(batch)
        since = batch[-1][0] + tf_ms
        time.sleep(INTER_CALL_SLEEP)
        # NB: KuCoin caps each page by TIME window, often returning <PER_CALL bars even
        # mid-history, so "short page" is NOT the live edge. We terminate only via
        # `since >= now` or an empty page after real data (above).
    return out


def update_symbol(client, symbol: str, timeframe: str) -> int:
    """Incrementally fetch + cache one symbol. Returns # of new bars added."""
    cached = _read_cached(symbol, timeframe)
    if len(cached):
        # Refetch from the last cached bar (it may have been a partial/forming candle).
        last_ts = int(cached["ts"].iloc[-1])
        since = last_ts  # overlap is de-duped below
    else:
        since = EARLY_EPOCH_MS
    if symbol not in client.markets:
        print(f"  {symbol:14s} NOT LISTED on {_exchange_id()} — skip", file=sys.stderr)
        return 0
    rows = _fetch_pages(client, symbol, timeframe, since)
    if not rows:
        return 0
    new = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    merged = (pd.concat([cached, new], ignore_index=True)
              .sort_values("ts").drop_duplicates("ts", keep="last"))
    added = len(merged) - len(cached)
    merged.to_csv(_csv_path(symbol, timeframe), index=False)
    return max(added, 0)


def update_cache(symbols: List[str], timeframe: str = "1d") -> None:
    client = _client()
    client.load_markets()
    print(f"[data_cache] updating {len(symbols)} symbols, {timeframe}, "
          f"exchange={_exchange_id()} (serial, throttled)")
    for i, sym in enumerate(symbols, 1):
        try:
            added = update_symbol(client, sym, timeframe)
            df = _read_cached(sym, timeframe)
            span = ""
            if len(df):
                d0 = pd.to_datetime(int(df["ts"].iloc[0]), unit="ms").date()
                d1 = pd.to_datetime(int(df["ts"].iloc[-1]), unit="ms").date()
                span = f"{d0}..{d1} ({len(df)} bars)"
            print(f"  [{i:2d}/{len(symbols)}] {sym:14s} +{added:4d} new   {span}")
        except Exception as exc:  # noqa: BLE001 — keep going; one bad symbol shouldn't abort
            print(f"  [{i:2d}/{len(symbols)}] {sym:14s} FAILED: {exc}", file=sys.stderr)


def load_panel(symbols: List[str], timeframe: str = "1d",
               exchange: Optional[str] = None, field: str = "close") -> pd.DataFrame:
    """Return a ragged DataFrame of `field` for the symbols that have a cache.

    Index = ms timestamps (sorted). Columns = symbols. Missing bars are NaN; the
    momentum/selection harness already handles per-row availability (coins enter over
    time, NaN coins drop out of each rebalance) — same convention as P6/P8.
    """
    ser = {}
    for s in symbols:
        df = _read_cached(s, timeframe, exchange)
        if len(df):
            ser[s] = pd.Series(df[field].values, index=df["ts"].astype(np.int64).values)
    if not ser:
        return pd.DataFrame()
    return pd.DataFrame(ser).sort_index()


def live_universe() -> List[str]:
    with open(os.path.join(REPO, "state", "strategy.yaml")) as f:
        return yaml.safe_load(f)["universe"]


def _status(timeframe: str) -> None:
    ex = _exchange_id()
    d = os.path.join(CACHE_ROOT, ex, timeframe)
    if not os.path.isdir(d):
        print(f"no cache at {d}")
        return
    files = sorted(f for f in os.listdir(d) if f.endswith(".csv"))
    print(f"[data_cache] {len(files)} cached symbols at {d}:")
    for f in files:
        df = pd.read_csv(os.path.join(d, f))
        if not len(df):
            continue
        d0 = pd.to_datetime(int(df["ts"].iloc[0]), unit="ms").date()
        d1 = pd.to_datetime(int(df["ts"].iloc[-1]), unit="ms").date()
        yrs = (df["ts"].iloc[-1] - df["ts"].iloc[0]) / 86400000 / 365
        print(f"  {f[:-4]:16s} {d0}..{d1}  {len(df):5d} bars  ~{yrs:.1f}y")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="fetch/refresh the cache")
    ap.add_argument("--status", action="store_true", help="show what's cached")
    ap.add_argument("--timeframe", default="1d")
    ap.add_argument("--symbols", nargs="*", help="default: live universe")
    args = ap.parse_args()

    syms = args.symbols or live_universe()
    if args.update:
        update_cache(syms, args.timeframe)
    if args.status or not args.update:
        _status(args.timeframe)
