"""Market-context feature snapshot — a Python port of the 21-indicator scan
from the "Free Plan Unlocked" Pine matrix.

The trading engine still decides on RSI alone (see loop.PaperEngine); this module
adds NOTHING to the decision path. Its job is to record the *full* indicator
context around each trade so the reflection loop — and the optional Claude
`--llm` reflect — can learn WHICH conditions precede winners and losers, instead
of only nudging the RSI threshold in the dark.

`snapshot(candles)` computes the 21 indicators on one timeframe.
`multi_timeframe(candles_1m, tfs)` resamples the 1m candles into higher
timeframes and snapshots each — the same cross-timeframe idea as the Pine
`request.security` matrix, bounded by the history we actually hold.

Pure functions, no IO. numpy/pandas only (already project deps). Every indicator
returns None when there isn't enough history, mirroring loop.rsi().
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

# The 21 indicators, in the same order as the Pine matrix rows. Booleans are the
# trend/cross checks; the rest are levels. `bull` marks the direction that counts
# as bullish, used only for the aggregate tally — never for trading.
INDICATORS = [
    "ema9_gt_21", "ema21_gt_50", "ema50_gt_200", "rsi", "stoch",
    "macd_bull", "macd_hist", "cci", "mfi", "adx", "di_bull", "bb_pct_b",
    "wpr", "roc", "atr_pct", "obv_up", "supertrend_bull", "mom_up",
    "gt_sma20", "gt_sma50", "gt_10ago",
]

# Default cross-timeframe set, chosen to fit inside ~200 one-minute candles.
DEFAULT_TFS = ["1min", "5min", "15min"]


# --------------------------------------------------------------------------- #
# Indicator primitives (match Pine ta.* semantics)
# --------------------------------------------------------------------------- #
def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _rma(s: pd.Series, n: int) -> pd.Series:
    # Wilder's moving average == EMA with alpha = 1/n.
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _rma(gain, n)
    avg_loss = _rma(loss, n)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.where(avg_loss != 0.0, 100.0)


def _stoch(close, high, low, n=14) -> pd.Series:
    ll = low.rolling(n).min()
    hh = high.rolling(n).max()
    rng = (hh - ll).replace(0.0, np.nan)
    return 100.0 * (close - ll) / rng


def _macd(close, fast=12, slow=26, sig=9):
    macd = _ema(close, fast) - _ema(close, slow)
    signal = _ema(macd, sig)
    return macd, signal, macd - signal


def _cci(close, high, low, n=20) -> pd.Series:
    tp = (high + low + close) / 3.0
    sma = tp.rolling(n).mean()
    mad = (tp - sma).abs().rolling(n).mean()
    return (tp - sma) / (0.015 * mad.replace(0.0, np.nan))


def _mfi(close, high, low, volume, n=14) -> pd.Series:
    tp = (high + low + close) / 3.0
    mf = tp * volume
    up = mf.where(tp > tp.shift(1), 0.0)
    dn = mf.where(tp < tp.shift(1), 0.0)
    pos = up.rolling(n).sum()
    neg = dn.rolling(n).sum().replace(0.0, np.nan)
    return 100.0 - 100.0 / (1.0 + pos / neg)


def _dmi(high, low, close, n=14):
    up = high.diff()
    dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = _rma(tr, n)
    plus_di = 100.0 * _rma(pd.Series(plus_dm, index=high.index), n) / atr
    minus_di = 100.0 * _rma(pd.Series(minus_dm, index=high.index), n) / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx = _rma(dx, n)
    return plus_di, minus_di, adx


def _bb_pct_b(close, n=20, mult=2.0) -> pd.Series:
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    upper, lower = mid + mult * sd, mid - mult * sd
    rng = (upper - lower).replace(0.0, np.nan)
    return 100.0 * (close - lower) / rng


def _wpr(close, high, low, n=14) -> pd.Series:
    hh = high.rolling(n).max()
    ll = low.rolling(n).min()
    rng = (hh - ll).replace(0.0, np.nan)
    return -100.0 * (hh - close) / rng


def _atr(high, low, close, n=14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return _rma(tr, n)


def _supertrend_bull(high, low, close, period=10, mult=3.0) -> pd.Series:
    """Return a boolean Series: True where SuperTrend direction is bullish."""
    atr = _atr(high, low, close, period)
    hl2 = (high + low) / 2.0
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr
    dir_bull = pd.Series(index=close.index, dtype="boolean")
    prev_up = prev_lo = np.nan
    bull = True
    for i in range(len(close)):
        c = close.iloc[i]
        u, lo = upper.iloc[i], lower.iloc[i]
        if np.isnan(atr.iloc[i]):
            dir_bull.iloc[i] = pd.NA
            continue
        if not np.isnan(prev_up):
            u = min(u, prev_up) if close.iloc[i - 1] <= prev_up else u
            lo = max(lo, prev_lo) if close.iloc[i - 1] >= prev_lo else lo
        if c > (prev_up if not np.isnan(prev_up) else u):
            bull = True
        elif c < (prev_lo if not np.isnan(prev_lo) else lo):
            bull = False
        dir_bull.iloc[i] = bull
        prev_up, prev_lo = u, lo
    return dir_bull


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #
def _last(s: pd.Series) -> Optional[float]:
    if s is None or len(s) == 0:
        return None
    v = s.iloc[-1]
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
        return None
    return float(v)


def _last_bool(s: pd.Series) -> Optional[bool]:
    if s is None or len(s) == 0:
        return None
    v = s.iloc[-1]
    if v is pd.NA or (isinstance(v, float) and np.isnan(v)):
        return None
    return bool(v)


def _df(candles: List[list]) -> pd.DataFrame:
    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts")


def snapshot(candles: List[list]) -> dict:
    """Compute the 21 indicators on one OHLCV series (ccxt candle rows).

    Returns a flat dict keyed by INDICATORS, plus `bull_count` (how many of the
    21 read bullish) and `n_bars`. Any indicator with insufficient history is
    None — never fabricated.
    """
    if not candles:
        return {"n_bars": 0, "bull_count": None}
    df = _df(candles)
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]

    ema9, ema21, ema50, ema200 = _ema(c, 9), _ema(c, 21), _ema(c, 50), _ema(c, 200)
    macd, signal, hist = _macd(c)
    plus_di, minus_di, adx = _dmi(h, l, c)
    obv = (np.sign(c.diff()).fillna(0.0) * v).cumsum()

    feats = {
        "ema9_gt_21": _last_bool(ema9 > ema21),
        "ema21_gt_50": _last_bool(ema21 > ema50),
        "ema50_gt_200": _last_bool(ema50 > ema200) if len(c) >= 200 else None,
        "rsi": _round(_last(_rsi(c, 14))),
        "stoch": _round(_last(_stoch(c, h, l, 14))),
        "macd_bull": _last_bool(macd > signal),
        "macd_hist": _round(_last(hist), 5),
        "cci": _round(_last(_cci(c, h, l, 20))),
        "mfi": _round(_last(_mfi(c, h, l, v, 14))),
        "adx": _round(_last(adx)),
        "di_bull": _last_bool(plus_di > minus_di),
        "bb_pct_b": _round(_last(_bb_pct_b(c, 20, 2.0))),
        "wpr": _round(_last(_wpr(c, h, l, 14))),
        "roc": _round(_last(100.0 * (c / c.shift(9) - 1.0))),
        "atr_pct": _round(_last(_atr(h, l, c, 14) / c * 100.0)),
        "obv_up": _last_bool(obv > obv.shift(1)),
        "supertrend_bull": _last_bool(_supertrend_bull(h, l, c)),
        "mom_up": _last_bool((c - c.shift(10)) > 0),
        "gt_sma20": _last_bool(c > c.rolling(20).mean()),
        "gt_sma50": _last_bool(c > c.rolling(50).mean()),
        "gt_10ago": _last_bool(c > c.shift(10)),
    }

    # Aggregate bullish tally — a single learnable "how aligned is the tape" scalar.
    bull = _bull_tally(feats)
    feats["bull_count"] = bull
    feats["n_bars"] = int(len(c))
    return feats


def _round(x: Optional[float], nd: int = 2) -> Optional[float]:
    return None if x is None else round(x, nd)


def _bull_tally(feats: dict) -> Optional[int]:
    """Count how many of the 21 indicators read bullish (levels use neutral lines
    that match the Pine matrix's colour thresholds)."""
    rules = {
        "ema9_gt_21": lambda v: v is True,
        "ema21_gt_50": lambda v: v is True,
        "ema50_gt_200": lambda v: v is True,
        "rsi": lambda v: v > 50,
        "stoch": lambda v: v > 50,
        "macd_bull": lambda v: v is True,
        "macd_hist": lambda v: v > 0,
        "cci": lambda v: v > 0,
        "mfi": lambda v: v > 50,
        "adx": lambda v: False,  # ADX is strength, not direction — excluded from tally
        "di_bull": lambda v: v is True,
        "bb_pct_b": lambda v: v > 50,
        "wpr": lambda v: v > -50,
        "roc": lambda v: v > 0,
        "atr_pct": lambda v: False,  # volatility, not direction — excluded
        "obv_up": lambda v: v is True,
        "supertrend_bull": lambda v: v is True,
        "mom_up": lambda v: v is True,
        "gt_sma20": lambda v: v is True,
        "gt_sma50": lambda v: v is True,
        "gt_10ago": lambda v: v is True,
    }
    seen = False
    count = 0
    for key, rule in rules.items():
        v = feats.get(key)
        if v is None:
            continue
        seen = True
        try:
            if rule(v):
                count += 1
        except TypeError:
            continue
    return count if seen else None


# --------------------------------------------------------------------------- #
# Multi-timeframe (resample the 1m candles, snapshot each)
# --------------------------------------------------------------------------- #
_OHLC_AGG = {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}


def _resample(candles_1m: List[list], rule: str) -> List[list]:
    df = _df(candles_1m).resample(rule, label="right", closed="right").agg(_OHLC_AGG).dropna()
    out = []
    for ts, row in df.iterrows():
        out.append([int(ts.value // 1_000_000), row["open"], row["high"],
                    row["low"], row["close"], row["volume"]])
    return out


def multi_timeframe(candles_1m: List[list], tfs: Optional[List[str]] = None) -> dict:
    """Snapshot the matrix across several timeframes resampled from 1m candles.

    Returns {tf: snapshot}. The base "1min" tf uses the candles as-is. Higher
    tfs are bounded by available history — short tfs simply yield more Nones.
    """
    tfs = tfs or DEFAULT_TFS
    out: dict = {}
    for tf in tfs:
        rows = candles_1m if tf in ("1min", "1m") else _resample(candles_1m, tf)
        out[tf] = snapshot(rows)
    return out
