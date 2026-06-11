"""Walk-forward regime-gate validation (P0a)."""  # noqa
# ruff: skip-file
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters  # noqa: E402
from hermes_trading.portfolio import (  # noqa: E402
    bull_score_last,
    market_breadth,
    momentum,
    rebalance_pnl,
    target_weights,
    turnover,
)

TOP_N, BARS, REBAL = 24, 1200, 7
MIN_VOL, CONC = 5_000_000, 6
STABLES = {"USDC", "USDT", "DAI", "TUSD", "FDUSD", "USDD", "PYUSD", "EUR", "BUSD", "WBTC"}
CANDIDATES = [0.30, 0.40, 0.50, 0.60, 0.70]
UNIVERSE = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
    "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT", "LTC/USDT",
    "BCH/USDT", "TRX/USDT", "ATOM/USDT", "NEAR/USDT", "APT/USDT",
    "ARB/USDT", "OP/USDT", "INJ/USDT", "SUI/USDT", "FIL/USDT",
    "ETC/USDT", "XLM/USDT", "AAVE/USDT", "UNI/USDT",
]


def discover():
    cx = ccxt.kucoin({"enableRateLimit": True})
    cx.load_markets()
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
        except Exception:
            return sym, None


def _build_panel():
    sem = asyncio.Semaphore(CONC)
    got = asyncio.get_event_loop().run_until_complete(
        asyncio.gather(*[fetch_one(sem, s) for s in UNIVERSE])
    )
    series = {}
    for sym, candles in got:
        if not candles or len(candles) < 600:
            continue
        series[sym] = pd.Series({c[0]: c[4] for c in candles})
    panel = pd.DataFrame(series).sort_index().dropna(axis=0, how="all")
    panel = panel.dropna(axis=1, thresh=int(len(panel) * 0.90))
    panel = panel[panel.columns[:TOP_N]]
    return panel


def perf(eq, R=7):
    eq = [float(x) for x in eq]
    if len(eq) < 2:
        return {"sharpe": 0.0, "mdd": 0.0, "total": 0.0, "steps": 0, "active": 0}
    r = pd.Series(eq).pct_change().dropna()
    if r.std() == 0:
        sh = 0.0
    else:
        sh = float(r.mean() / r.std() * np.sqrt(365.0 / R))
    s = pd.Series(eq)
    mdd = float(((s.cummax() - s) / s.cummax()).max())
    tot = float((eq[-1] / eq[0] - 1) * 100)
    return {"sharpe": sh, "mdd": mdd, "total": tot, "steps": len(eq), "active": len(eq)}


def simulate(closes: Dict[str, list],
             lookback: int, skip: int, R: int, K: int,
             allow_short: bool, size_total: float, cost: float,
             regime_threshold: float | None, bull_min: int,
             start: int, end: int):
    eq = [1.0]
    prev_w: Dict[str, float] = {}
    n_active = 0
    steps = 0
    for i in range(start, end - R, R):
        steps += 1
        flat = False
        if regime_threshold is not None:
            scores = [bull_score_last(closes[s][: i + 1]) for s in closes]
            if scores:
                breadth = sum(1 for sc in scores if sc >= bull_min) / len(scores)
                flat = breadth < regime_threshold

        if flat:
            new_w: Dict[str, float] = {}
        else:
            mom: Dict[str, float] = {}
            for s, cs in closes.items():
                try:
                    rec = cs[-(1 + skip)]
                    past = cs[-(lookback + skip + 1)]
                    if past > 0:
                        mom[s] = rec / past - 1.0
                except IndexError:
                    pass
            new_w = target_weights(mom, K, allow_short, size_total)

        turn = turnover(prev_w, new_w)
        eq[-1] *= (1.0 - turn * cost)

        if new_w and not flat:
            ep, ex = {}, {}
            for s in new_w:
                try:
                    ep[s] = closes[s][i]
                    ex[s] = closes[s][i + R]
                except IndexError:
                    pass
            if ep and ex:
                pnl = rebalance_pnl(new_w, ep, ex)
                eq[-1] *= (1.0 + pnl)
                n_active += 1

        eq.append(eq[-1])
        prev_w = new_w

    return eq, n_active, steps


def choose_threshold(closes: Dict[str, list], train_end: int):
    best_thr, best_sh = 0.4, -9.0
    for thr in CANDIDATES:
        eq, _, _ = simulate(closes, 30, 0, REBAL, 5, True, 0.3, (10 + 5) / 1e4,
                            thr, 6, 0, train_end)
        p = perf(eq, REBAL)
        if p["sharpe"] > best_sh:
            best_thr = thr
            best_sh = p["sharpe"]
    return best_thr, best_sh


def main() -> int:
    print("WFV P0a · xsmom + breadth-regime gate")
    panel = _build_panel()
    print(f"panel {panel.shape[1]}x{panel.shape[0]} "
          f"{(panel.index[-1]-panel.index[0])//86400000}d")
    closes = {s: panel[s].tolist() for s in panel.columns}
    n = len(panel)

    n_folds = 5
    fold_size = (n - 600) // n_folds
    results = []
    all_eq = [1.0]

    for f in range(n_folds):
        train_end = 600 + fold_size * f
        test_start = train_end
        test_end = min(test_start + fold_size, n)
        if test_end - test_start < 100:
            break

        best_thr, best_tr = choose_threshold(closes, train_end)
        te_eq, n_active, total_steps = simulate(
            closes, 30, 0, REBAL, 5, True, 0.3, (10 + 5) / 1e4, best_thr, 6,
            test_start, test_end,
        )
        p = perf(te_eq, REBAL)
        results.append({
            "fold": f,
            "chosen_threshold": best_thr,
            "train_sharpe": float(best_tr),
            "test_sharpe": p["sharpe"],
            "test_mdd": p["mdd"],
            "test_total": p["total"],
            "active_pct": float(n_active) / max(total_steps, 1),
            "rebalances": n_active,
        })

        for j in range(1, len(te_eq)):
            all_eq.append(all_eq[-1] * (te_eq[j] / te_eq[j - 1]))

    agg = perf(all_eq, REBAL)
    print("\n-- fold results --")
    print(f"{'fold':<5} {'thr':<5} {'trainSH':>8} {'OOS SH':>8} {'maxDD':>8} {'ret%':>8} {'active%':>8}")
    for r in results:
        print(f"{r['fold']:<5} {r['chosen_threshold']:<5.1f} {r['train_sharpe']:>8.2f} "
              f"{r['test_sharpe']:>8.2f} {r['test_mdd']*100:>7.1f}% "
              f"{r['test_total']:>+8.1f}% {r['active_pct']*100:>7.0f}%")

    print("\n-- walk-forward aggregate --")
    print(f"  OOS Sharpe: {agg['sharpe']:.2f}")
    print(f"  maxDD:      {agg['mdd']*100:.1f}%")
    print(f"  total ret:  {agg['total']:+.1f}%")
    if results:
        print(f"  active ~    {np.mean([r['active_pct'] for r in results])*100:.0f}% of the time")
    print(f"  folds:      {len(results)}")

    out = Path(__file__).resolve().parent.parent / "research" / "walk_forward_p0a.json"
    out.write_text(json.dumps({
        "P0a": "walk-forward regime gate",
        "folds": [{k: (list(v) if isinstance(v, tuple) else v) for k, v in r.items()}
                  for r in results],
        "aggregate": agg,
    }, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
