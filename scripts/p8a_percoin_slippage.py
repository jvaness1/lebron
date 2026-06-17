"""P8a — Per-coin slippage realism (follow-up to P8).

P8 modeled a UNIFORM per-side cost. But the live 36-coin universe mixes deep majors
(BTC/ETH) with memecoins (SHIB/PEPE) and thin newer alts (TAO/SEI/WLD/ENA/ONDO/STRK/
ETHFI/STG) whose REAL slippage is far worse. Because momentum SELECTS recent pumpers,
the book may concentrate turnover into exactly the illiquid names — so a uniform cost
could understate the true drag.

This script:
  1. Assigns each coin a liquidity TIER and a per-side cost (fee 10bps + tier slippage).
  2. Measures TURNOVER SHARE per tier — how much of the book's churn actually lands in
     the expensive names (the crux: if illiquid turnover is small, concentrated cost
     barely matters).
  3. Compares net%, Sharpe, maxDD under: uniform-15 (backtest), uniform-60 (P8 pessimistic),
     tiered-realistic, tiered-pessimistic.
  4. Break-even: scales the illiquid-tier slippage up until the OOS edge vanishes.
  5. Walk-forward (5 slices) at tiered-realistic and tiered-pessimistic for robustness.

EXACT live config + universe from strategy.yaml. Honest OOS (test half) + walk-forward.
EXCHANGE_ID=kucoin python scripts/p8a_percoin_slippage.py
"""
import os, asyncio, yaml
import numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters

K, R, SPLIT = 5, 7, 0.60
MA_DAYS = 100
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Liquidity tiers (heuristic, by market depth / age on a US retail venue). Per-side cost
# = 10bps fee + tier slippage. Tiers are a JUDGMENT call — the point is to stress the
# ILLIQUID names with pessimistic cost and see if the edge holds, and to measure how much
# turnover actually lands there. base_slip_bps below; the breakeven sweep scales tier C.
TIER_A = {"BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT", "LTC", "BCH"}
TIER_B = {"ATOM", "NEAR", "APT", "ARB", "INJ", "SUI", "XLM", "AAVE", "UNI", "ALGO",
          "ICP", "HBAR", "FET", "ZEC", "DASH"}
# Tier C = everything else in the universe: SHIB PEPE TAO SEI WLD ENA ONDO STRK ETHFI STG
FEE_BPS = 10.0
SLIP_A, SLIP_B, SLIP_C = 5.0, 15.0, 35.0           # realistic
SLIP_A_P, SLIP_B_P, SLIP_C_P = 5.0, 20.0, 55.0      # pessimistic (thin names hit hard)


def base(sym):
    return sym.split("/")[0]


def live_universe():
    with open(os.path.join(REPO, "state", "strategy.yaml")) as f:
        cfg = yaml.safe_load(f)
    return cfg["universe"]


def tier_of(sym):
    b = base(sym)
    if b in TIER_A:
        return "A"
    if b in TIER_B:
        return "B"
    return "C"


def cost_vector(cols, slips):
    """Per-coin per-side cost as a fraction. slips = {'A':..,'B':..,'C':..} in bps slippage."""
    out = pd.Series(0.0, index=cols)
    for c in cols:
        out[c] = (FEE_BPS + slips[tier_of(c)]) / 1e4
    return out


def multi_score(panel, i):
    return ((panel.iloc[i]/panel.iloc[i-14]-1) + (panel.iloc[i]/panel.iloc[i-30]-1)
            + (panel.iloc[i]/panel.iloc[i-60]-1)) / 3


def bt(panel, ma, cost_vec):
    """Long-only multi-horizon K5 weekly, trend-filtered, PER-COIN cost.
    cost_vec: Series of per-side cost fraction per column (or a scalar).
    Returns (net, Sharpe, maxDD, turn_total, turn_by_tier dict)."""
    rets = []
    turn_tier = {"A": 0.0, "B": 0.0, "C": 0.0}
    turn_total = 0.0
    prev = pd.Series(0.0, index=panel.columns)
    if np.isscalar(cost_vec):
        cost_vec = pd.Series(float(cost_vec), index=panel.columns)
    i = 60
    n_rebal = 0
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= K:
            for s in sc.sort_values().index[-K:]:
                if panel.iloc[i][s] > ma.iloc[i][s]:
                    w[s] = 1/K
        fwd = (panel.iloc[i+R]/panel.iloc[i]-1).reindex(panel.columns).fillna(0)
        gross = (w*fwd).sum()
        dturn = (w-prev).abs()                      # per-coin round-trip turnover
        cost = (dturn * cost_vec).sum()
        rets.append(gross - cost)
        for c in panel.columns:
            turn_tier[tier_of(c)] += dturn[c]
        turn_total += dturn.sum()
        prev = w; i += R; n_rebal += 1
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return 0.0, 0.0, 0.0, 0.0, turn_tier
    eq = np.cumprod(1+rets); pk = np.maximum.accumulate(eq)
    sharpe = rets.mean()/rets.std()*np.sqrt(365/R)
    return eq[-1]-1, sharpe, np.max((pk-eq)/pk), turn_total/max(n_rebal, 1), turn_tier


async def main():
    syms = live_universe()
    sem = asyncio.Semaphore(6)
    async def one(s):
        async with sem:
            try:
                h = await adapters.price.fetch_history(s, timeframe="1d", total=1200)
                return s, h["candles"]
            except Exception:
                return s, None
    res = await asyncio.gather(*[one(s) for s in syms])
    ser = {s: pd.Series({c[0]: c[4] for c in cs}) for s, cs in res if cs}
    full = pd.DataFrame(ser).sort_index().dropna(axis=1, thresh=600)
    ma = full.rolling(MA_DAYS).mean()
    cut = int(len(full)*SPLIT)
    pte = full.iloc[cut:].reset_index(drop=True)
    mate = ma.iloc[cut:].reset_index(drop=True)
    oos_days = (full.index[-1]-full.index[cut])/86400000
    cols = list(full.columns)

    # tier membership among coins WITH history
    by_tier = {"A": [], "B": [], "C": []}
    for c in cols:
        by_tier[tier_of(c)].append(base(c))
    print(f"\nP8a per-coin slippage realism — LIVE config, OOS ~{oos_days:.0f}d, "
          f"{len(cols)}/{len(syms)} coins")
    print(f"  Tier A (slip {SLIP_A:.0f}/{SLIP_A_P:.0f}bps): {sorted(by_tier['A'])}")
    print(f"  Tier B (slip {SLIP_B:.0f}/{SLIP_B_P:.0f}bps): {sorted(by_tier['B'])}")
    print(f"  Tier C (slip {SLIP_C:.0f}/{SLIP_C_P:.0f}bps): {sorted(by_tier['C'])}")

    realistic = {"A": SLIP_A, "B": SLIP_B, "C": SLIP_C}
    pessim = {"A": SLIP_A_P, "B": SLIP_B_P, "C": SLIP_C_P}

    # 1) Turnover share by tier (the crux)
    _, _, _, turn_avg, turn_tier = bt(pte, mate, cost_vector(cols, realistic))
    tot = sum(turn_tier.values()) or 1.0
    print(f"\nTURNOVER SHARE by tier (where does the book's churn actually land?):")
    for t in ("A", "B", "C"):
        print(f"  Tier {t}: {turn_tier[t]/tot*100:5.1f}% of total turnover")
    print(f"  (avg round-trip turnover/rebal: {turn_avg:.3f}x)")

    # 2) Compare cost scenarios
    print(f"\n{'scenario':>22}{'net%':>9}{'Sharpe':>8}{'maxDD':>8}")
    scenarios = [
        ("uniform 15bps (backtest)", 15/1e4),
        ("uniform 60bps (P8 pessim)", 60/1e4),
        ("TIERED realistic", cost_vector(cols, realistic)),
        ("TIERED pessimistic", cost_vector(cols, pessim)),
    ]
    for name, cv in scenarios:
        n, s, d, _, _ = bt(pte, mate, cv)
        print(f"{name:>22}{n*100:>8.1f}%{s:>8.2f}{d*100:>7.1f}%")

    # 3) Break-even: scale ONLY tier-C slippage up until OOS net <= 0
    print(f"\nBREAK-EVEN sweep (tier A/B fixed at realistic; raise tier-C slippage):")
    print(f"{'tierC slip':>12}{'net%':>9}{'Sharpe':>8}")
    last_pos = None
    for cslip in [35, 60, 100, 150, 200, 300, 400, 600, 800]:
        cv = cost_vector(cols, {"A": SLIP_A, "B": SLIP_B, "C": cslip})
        n, s, _, _, _ = bt(pte, mate, cv)
        print(f"{cslip:>10}bp{n*100:>8.1f}%{s:>8.2f}")
        if n > 0:
            last_pos = cslip

    # 4) Walk-forward at tiered realistic + pessimistic
    print(f"\nwalk-forward (5 slices) at tiered cost:")
    folds = np.array_split(np.arange(len(full)), 5)
    for name, slips in (("realistic", realistic), ("pessimistic", pessim)):
        sh, nets = [], []
        for idx in folds:
            seg = full.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
            ms = ma.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
            cv = cost_vector(list(seg.columns), slips)
            n, s, d, _, _ = bt(seg, ms, cv)
            sh.append(round(s, 2)); nets.append(round(n*100, 1))
        pos = sum(1 for x in sh if x > 0)
        print(f"  {name:>12}  Sharpe {sh}  positive {pos}/5 | net% {nets}")


asyncio.run(main())
