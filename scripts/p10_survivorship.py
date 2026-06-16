"""P10 — Survivorship-bias stress test of the LIVE config (v06).

WHY (honesty, highest priority): the backtest universe is TODAY's survivors. Coins
that pumped then died (delisted / crashed to ~0) are absent. A momentum strategy
SELECTS high-momentum names, so it would have bought some of those pumpers right
before they collapsed — losses the survivors-only backtest never sees. So the
backtest OVERSTATES the live edge. This sizes the gap.

METHOD — random-dropout proxy on REAL data (the backlog-sanctioned approach):
We can't fetch delisted coins from the live KuCoin API, so we simulate death on the
real survivor panel. For a random subset ("doomed") of the universe we:
  - keep their REAL prices up to a random death date (so momentum selects them
    exactly as it would a real coin), then
  - crash the price to a small floor over `decay_days` (a delisting gap-down), and
  - hold flat at the floor afterwards (a dead/illiquid token near zero).
Because the price stays non-NaN at the floor, a coin held THROUGH its death realizes
the full crash loss (a NaN would wrongly net to 0). After death its momentum is ~0
and it sits below its 100d MA, so it is never re-selected — realistic.

This faithfully captures BOTH channels of survivorship damage:
  (1) selection — you sometimes buy a doomed pumper, and
  (2) the trend filter's PROTECTION — a dying coin loses its uptrend and the dual-
      momentum (px>100d MA) filter drops it, so we measure how much the LIVE config's
      own risk control already mitigates survivorship bias.

We sweep the death fraction, run many random seeds for a DISTRIBUTION (not one draw),
report OOS degradation vs the no-death baseline, and walk-forward at a central setting.
We also run trend-filter ON vs OFF to quantify the filter's survivorship protection.

BTC/ETH are exempt from death (they realistically won't delist); every other coin is
eligible. Uniform per-side cost = strategy.yaml (15bps). Honest OOS (test half).

EXCHANGE_ID=kucoin python scripts/p10_survivorship.py
"""
import os, asyncio, yaml
import numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters

K, R, SPLIT = 5, 7, 0.60          # top-5, weekly, 60/40 train/test (report OOS only)
MA_DAYS = 100
COST = 15 / 1e4                   # 10bps fee + 5bps slippage (strategy.yaml)
FLOOR = 0.02                      # dead coin crashes to 2% of pre-death price (~-98%)
DECAY_DAYS = 5                    # collapse happens over ~1 week
IMMORTAL = {"BTC/USDT", "ETH/USDT"}   # won't realistically delist
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def live_universe():
    with open(os.path.join(REPO, "state", "strategy.yaml")) as f:
        return yaml.safe_load(f)["universe"]


def multi_score(panel, i):
    """Live selection signal: average of 14/30/60d returns."""
    return ((panel.iloc[i]/panel.iloc[i-14]-1) + (panel.iloc[i]/panel.iloc[i-30]-1)
            + (panel.iloc[i]/panel.iloc[i-60]-1)) / 3


def bt(panel, ma, trend=True, track_deaths=None):
    """Long-only multi-horizon K5 weekly, optional trend filter. Returns
    (net, Sharpe, maxDD, death_holdings) where death_holdings counts rebalances in
    which a held name realized a < -50% forward move (proxy for 'held a dying coin')."""
    rets = []
    prev = pd.Series(0.0, index=panel.columns)
    held_deaths = 0
    i = 60
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= K:
            for s in sc.sort_values().index[-K:]:
                if (not trend) or panel.iloc[i][s] > ma.iloc[i][s]:
                    w[s] = 1/K
        fwd = (panel.iloc[i+R]/panel.iloc[i]-1).reindex(panel.columns).fillna(0)
        if track_deaths is not None:
            held_deaths += int(((w > 0) & (fwd < -0.5)).sum())
        rets.append((w*fwd).sum() - (w-prev).abs().sum()*COST)
        prev = w; i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return 0.0, 0.0, 0.0, held_deaths
    eq = np.cumprod(1+rets); pk = np.maximum.accumulate(eq)
    return eq[-1]-1, rets.mean()/rets.std()*np.sqrt(365/R), np.max((pk-eq)/pk), held_deaths


def inject_deaths(full, frac, seed, lo=None, hi=None):
    """Return a copy of `full` with floor(frac*eligible) coins killed at random dates.
    Death dates drawn uniformly over [lo,hi] (default = the reported test window) so
    deaths land where performance is measured. Returns (panel_with_deaths, n_killed)."""
    rng = np.random.default_rng(seed)
    eligible = [c for c in full.columns if c not in IMMORTAL]
    n_kill = int(np.floor(frac * len(eligible)))
    if n_kill == 0:
        return full.copy(), 0
    doomed = rng.choice(eligible, size=n_kill, replace=False)
    p = full.copy()
    n = len(p)
    if lo is None:
        lo, hi = int(n*SPLIT)+MA_DAYS, n-R-2       # default: inside the test window
    if hi <= lo:
        return p, 0
    for c in doomed:
        d = int(rng.integers(lo, hi))
        base = p[c].iloc[d-1]
        if not np.isfinite(base) or base <= 0:
            continue
        # crash to FLOOR over DECAY_DAYS, then hold flat at the floor
        for k in range(DECAY_DAYS):
            if d+k < n:
                f = FLOOR ** ((k+1)/DECAY_DAYS)    # geometric ramp 1 -> FLOOR
                p.iloc[d+k, p.columns.get_loc(c)] = base*f
        if d+DECAY_DAYS < n:
            p.iloc[d+DECAY_DAYS:, p.columns.get_loc(c)] = base*FLOOR
    return p, n_kill


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
    n_coins = full.shape[1]
    oos_days = (full.index[-1]-full.index[int(len(full)*SPLIT)])/86400000
    print(f"\nP10 survivorship stress test — LIVE config (multi-horizon K5 weekly trend)")
    print(f"OOS ~{oos_days:.0f}d · {n_coins}/{len(syms)} universe coins had history")
    print(f"death model: crash to {FLOOR*100:.0f}% of pre-death px over {DECAY_DAYS}d, "
          f"then delisted-flat; BTC/ETH exempt; {COST*1e4:.0f}bps/side\n")

    cut = int(len(full)*SPLIT)
    def oos(panel):
        return (panel.iloc[cut:].reset_index(drop=True),
                panel.rolling(MA_DAYS).mean().iloc[cut:].reset_index(drop=True))

    # Baseline = survivors-only (the optimistic backtest)
    pte, mate = oos(full)
    b_net, b_sh, b_dd, _ = bt(pte, mate, trend=True)
    print(f"BASELINE (survivors-only, no deaths): net {b_net*100:+.1f}%  "
          f"Sharpe {b_sh:.2f}  maxDD {b_dd*100:.1f}%\n")

    SEEDS = 200
    print(f"Monte-Carlo survivorship haircut ({SEEDS} seeds/row). Reporting OOS "
          f"median [10th–90th pct] across seeds:\n")
    print(f"{'death%':>7}{'#killed':>8}  {'net% (med [p10,p90])':>28}"
          f"{'Sharpe med':>12}{'maxDD med':>11}{'held-death/run':>16}")
    summary = {}
    for frac in (0.10, 0.20, 0.30):
        nets, shs, dds, deaths, nk = [], [], [], [], 0
        for sd in range(SEEDS):
            pdead, nk = inject_deaths(full, frac, sd)
            pt, mt = oos(pdead)
            net, sh, dd, hd = bt(pt, mt, trend=True, track_deaths=True)
            nets.append(net); shs.append(sh); dds.append(dd); deaths.append(hd)
        nets, shs, dds = np.array(nets), np.array(shs), np.array(dds)
        summary[frac] = (np.median(nets), np.median(shs), np.median(dds))
        print(f"{frac*100:>6.0f}%{nk:>8}  "
              f"{np.median(nets)*100:>10.1f}% [{np.percentile(nets,10)*100:+.0f},"
              f"{np.percentile(nets,90)*100:+.0f}]      "
              f"{np.median(shs):>9.2f}{np.median(dds)*100:>10.1f}%"
              f"{np.mean(deaths):>14.2f}")

    # Trend-filter protection: re-run at 20% deaths with the filter OFF
    print(f"\nTrend-filter survivorship protection (20% deaths, {SEEDS} seeds, median):")
    for trend in (True, False):
        nets, shs, dds = [], [], []
        for sd in range(SEEDS):
            pdead, _ = inject_deaths(full, 0.20, sd)
            pt, mt = oos(pdead)
            net, sh, dd, _ = bt(pt, mt, trend=trend)
            nets.append(net); shs.append(sh); dds.append(dd)
        tag = "trend ON (live)" if trend else "trend OFF"
        print(f"  {tag:<16} net {np.median(nets)*100:+.1f}%  Sharpe {np.median(shs):.2f}"
              f"  maxDD {np.median(dds)*100:.1f}%")

    # Walk-forward at 20% deaths: is the haircut consistent across regimes?
    print(f"\nWalk-forward (5 slices) at 20% deaths, median over {SEEDS} seeds:")
    folds = np.array_split(np.arange(len(full)), 5)
    fold_meds = []
    for fi, idx in enumerate(folds):
        nets, shs = [], []
        # deaths must land inside THIS slice's tradeable region (global indices)
        d_lo, d_hi = idx[0]+60, idx[-1]-R-2
        for sd in range(SEEDS):
            pdead, _ = inject_deaths(full, 0.20, sd*100+fi, lo=d_lo, hi=d_hi)
            seg = pdead.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
            ms = pdead.rolling(MA_DAYS).mean().iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
            net, sh, dd, _ = bt(seg, ms, trend=True)
            nets.append(net); shs.append(sh)
        # baseline (no death) for the same slice
        segb = full.iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
        msb = full.rolling(MA_DAYS).mean().iloc[idx[0]:idx[-1]+1].reset_index(drop=True)
        bn, bsh, _, _ = bt(segb, msb, trend=True)
        fold_meds.append((round(np.median(shs),2), round(bsh,2)))
        print(f"  slice {fi+1}: deaths Sharpe {np.median(shs):>5.2f} (net {np.median(nets)*100:+6.1f}%)"
              f"  vs baseline Sharpe {bsh:>5.2f} (net {bn*100:+6.1f}%)")
    pos = sum(1 for s, _ in fold_meds if s > 0)
    print(f"  -> deaths-Sharpe positive in {pos}/5 slices")

    print("\nVERDICT GUIDE: the gap baseline->deaths is the survivorship haircut. If the")
    print("live edge stays clearly positive at a plausible death rate, forward")
    print("expectations are trustworthy (just lower). Trend ON vs OFF shows how much the")
    print("live config's own dual-momentum filter already protects against this bias.")


asyncio.run(main())
