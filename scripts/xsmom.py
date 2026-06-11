"""Cross-sectional momentum — a structurally different bet than per-asset TA.

Each rebalance: rank the universe by trailing return, go LONG the top-K and SHORT
the bottom-K (dollar-neutral), hold R days, repeat. This harvests the RELATIVE
strength spread, not market beta. Costs charged on turnover. Honest train/test.

Also reports a LONG-ONLY top-K variant (what a spot bot could trade) vs an
equal-weight-universe benchmark, so we can see if it beats just holding.

EXCHANGE_ID=kucoin python scripts/xsmom.py
"""
import os
import asyncio
import itertools

import ccxt
import numpy as np
import pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters            # noqa: E402
from hermes_trading.loop import load_strategy  # noqa: E402

TOP_N, BARS, MIN_VOL, CONC = 60, 1200, 5_000_000, 6   # daily bars
SPLIT = 0.60
_c = load_strategy().get("costs") or {}
COST = (_c.get("fees_bps", 10.0) + _c.get("slippage_bps", 5.0)) / 1e4   # per side, per unit turnover
STABLES = {"USDC", "USDT", "DAI", "TUSD", "FDUSD", "USDD", "PYUSD", "EUR", "BUSD", "WBTC"}


def discover():
    cx = ccxt.kucoin({"enableRateLimit": True}); cx.load_markets()
    rows = []
    for sym, t in cx.fetch_tickers().items():
        if not sym.endswith("/USDT"):
            continue
        base = sym.split("/")[0]
        if base in STABLES or any(x in base for x in ("3L", "3S", "UP", "DOWN")):
            continue
        if (t.get("quoteVolume") or 0) >= MIN_VOL:
            rows.append((sym, t["quoteVolume"]))
    rows.sort(key=lambda r: r[1], reverse=True)
    return [s for s, _ in rows[:TOP_N]]


async def fetch_one(sem, sym):
    async with sem:
        try:
            h = await adapters.price.fetch_history(sym, timeframe="1d", total=BARS)
            return sym, h["candles"]
        except Exception:  # noqa: BLE001
            return sym, None


def perf(equity, R, label):
    """equity points are spaced R days apart — annualise correctly off real time."""
    eq = np.array(equity)
    rets = np.diff(eq) / eq[:-1]
    if len(rets) < 2:
        return f"{label}: too few periods"
    days = len(rets) * R
    ann = (eq[-1] / eq[0]) ** (365.0 / days) - 1 if eq[-1] > 0 else -1.0
    ppy = 365.0 / R                                   # rebalances per year
    sharpe = (rets.mean() / rets.std() * np.sqrt(ppy)) if rets.std() > 0 else 0
    peak = np.maximum.accumulate(eq)
    mdd = np.max((peak - eq) / peak)
    return (f"{label}: total {(eq[-1]/eq[0]-1)*100:+.1f}%  ann {ann*100:+.1f}%  "
            f"Sharpe {sharpe:.2f}  maxDD {mdd*100:.1f}%")


def backtest(panel, lookback, skip, R, K, cut_idx):
    """panel: DataFrame [dates x symbols] of close. Returns (ls_eq, lo_eq) over the
    full timeline; caller slices train/test by index."""
    rets = panel.pct_change()
    dates = panel.index
    ls_eq, lo_eq = [1.0], [1.0]
    prev_w_ls = pd.Series(0.0, index=panel.columns)
    prev_w_lo = pd.Series(0.0, index=panel.columns)
    i = lookback + skip
    while i + R < len(dates):
        # momentum = return over [i-lookback-skip, i-skip]
        past = panel.iloc[i - skip] / panel.iloc[i - lookback - skip] - 1
        valid = past.dropna()
        if len(valid) < 2 * K:
            i += R; continue
        ranked = valid.sort_values()
        shorts, longs = ranked.index[:K], ranked.index[-K:]
        w_ls = pd.Series(0.0, index=panel.columns)
        w_ls[longs] = 1.0 / K; w_ls[shorts] = -1.0 / K
        w_lo = pd.Series(0.0, index=panel.columns)
        w_lo[longs] = 1.0 / K
        # forward return over the holding window
        fwd = panel.iloc[i + R] / panel.iloc[i] - 1
        fwd = fwd.reindex(panel.columns).fillna(0.0)
        turn_ls = (w_ls - prev_w_ls).abs().sum()
        turn_lo = (w_lo - prev_w_lo).abs().sum()
        ls_eq.append(ls_eq[-1] * (1 + (w_ls * fwd).sum() - turn_ls * COST))
        lo_eq.append(lo_eq[-1] * (1 + (w_lo * fwd).sum() - turn_lo * COST))
        prev_w_ls, prev_w_lo = w_ls, w_lo
        i += R
    return ls_eq, lo_eq


async def main():
    print(f"\ncross-sectional momentum · cost {COST*1e4:.0f}bps/side/turnover · daily bars")
    print("discovering universe…")
    universe = discover()
    print(f"{len(universe)} liquid pairs · fetching daily history…")
    sem = asyncio.Semaphore(CONC)
    got = await asyncio.gather(*[fetch_one(sem, s) for s in universe])

    series = {}
    for sym, candles in got:
        if not candles or len(candles) < 200:
            continue
        s = pd.Series({c[0]: c[4] for c in candles})
        series[sym] = s
    panel = pd.DataFrame(series).sort_index()
    panel = panel.dropna(axis=0, how="all")
    print(f"panel: {panel.shape[1]} coins × {panel.shape[0]} days "
          f"({(panel.index[-1]-panel.index[0])/86400000:.0f} days)\n")

    cut = int(len(panel) * SPLIT)

    def train_sharpe(p, lookback, skip, R, K):
        ls_tr, _ = backtest(p.iloc[:cut], lookback, skip, R, K, cut)
        if len(ls_tr) < 10:
            return -9, ls_tr
        r = np.diff(ls_tr) / np.array(ls_tr)[:-1]
        return (r.mean() / r.std() * np.sqrt(365.0 / R) if r.std() > 0 else 0), ls_tr

    # Sweep on TRAIN, pick best long-short Sharpe, then report TEST.
    best = None
    for lookback, skip, R, K in itertools.product([14, 30, 60, 90], [0, 2], [3, 7], [3, 5, 8]):
        sh, _ = train_sharpe(panel, lookback, skip, R, K)
        if best is None or sh > best[0]:
            best = (sh, lookback, skip, R, K)
    _, lookback, skip, R, K = best
    print(f"best TRAIN config: lookback{lookback}d skip{skip} rebal{R}d K{K}/side\n")

    def report_split(p, tag):
        ls_tr, lo_tr = backtest(p.iloc[:cut], lookback, skip, R, K, cut)
        ls_te, lo_te = backtest(p.iloc[cut:], lookback, skip, R, K, cut)
        bench = p.iloc[cut:].pct_change().mean(axis=1).fillna(0)
        bench_eq = (1 + bench).cumprod().tolist()
        print(f"{tag}")
        print("  IN-SAMPLE : " + perf(ls_tr, R, "L/S") + " | " + perf(lo_tr, R, "L-only"))
        print("  OUT-SAMPLE: " + perf(ls_te, R, "L/S"))
        print("              " + perf(lo_te, R, "L-only"))
        print("              " + perf(bench_eq, 1, "benchmark hold"))
        return ls_te

    print("── ALL coins (current top-liquid set — SURVIVORSHIP-BIASED, upper bound):")
    report_split(panel, "")

    # Survivorship mitigation: keep only coins listed before the train window began
    # (drops the newly-listed-and-pumped names that most inflate momentum).
    full = panel.dropna(axis=1, thresh=int(cut * 0.9))
    print(f"\n── FULL-HISTORY coins only ({full.shape[1]}/{panel.shape[1]} present from the start):")
    if full.shape[1] >= 2 * K:
        report_split(full, "")
    else:
        print("  too few full-history coins to form the book")

    print("\nROBUSTNESS (OOS long-short, full-history coins, lookback30/rebal7):")
    for k in (3, 5, 8):
        if full.shape[1] >= 2 * k:
            _, te = train_sharpe(full, 30, 0, 7, k)
            ls_te, _ = backtest(full.iloc[cut:], 30, 0, 7, k, cut)
            print(f"   K{k}/side: " + perf(ls_te, 7, "L/S"))
    print("\nNote: even the full-history set is survivors (coins liquid TODAY). True"
          "\npoint-in-time results would be lower. Shorts assume perp availability.")


asyncio.run(main())
