"""P14 — Income / withdrawal & sequence-of-returns risk model for the LIVE config (v06).

GOAL: the live goal is CONSISTENT INCOME. This asks the only question that matters for
that: if I withdraw a fixed monthly income from this strategy, what annual withdrawal
rate survives the drawdown profile without ruin? i.e. a Safe Withdrawal Rate (SWR) for
a high-vol crypto-momentum book, the crypto analogue of the equities "4% rule".

HONEST INPUTS (per the BACKLOG NBs, not the optimistic survivors-only curve):
  - RETURN DISTRIBUTION = the P10 survivorship-stressed weekly net returns (live config
    + random deaths @20% 3-yr rate). This already bakes in the ~1/3 haircut AND the fat
    left tail (held-death weeks) that the survivors-only backtest never sees. We pool
    weekly returns across many death-seeds to get a realistic marginal distribution.
  - CONSISTENCY DIAL = PARTIAL CASH (P11's per-name weight cap = uniform de-leverage).
    invested fraction f in {1.0, 0.75, 0.50}; remainder in cash (modelled at 0%/yr — a
    conservative floor; a real HYSA ~4% would only help). This is the ONLY Sharpe-
    preserving DD lever P11 found; vol-targeting/stops are dead ends (not modelled).

SEQUENCE RISK via BLOCK BOOTSTRAP: a single historical path is one ordering. We resample
contiguous BLOCKs of weekly returns (block length preserves momentum autocorrelation &
drawdown clustering) from within a seed's series, stitch them into many synthetic multi-
year paths, and withdraw a fixed monthly income along each. Ruin = equity can't fund the
next withdrawal. We report ruin probability + terminal-wealth distribution per (f, W),
and back out the SWR (max W with ruin <= 5%).

CAVEAT stated up front: the base window is ~3yr of survivors (one bull + one bear). The
bootstrap inherits that window's mean, which is bull-flattered even after the P10 haircut.
So we ALSO run a RETURN-STRESS row (halve the pooled mean) to show SWR's fragility to the
forward-return assumption. SWR is an upper bound on what's prudent, not a promise.

EXCHANGE_ID=kucoin python scripts/p14_income_model.py
"""
import os, asyncio, yaml
import numpy as np, pandas as pd

os.environ.setdefault("EXCHANGE_ID", "kucoin")
from hermes_trading import adapters

K, R = 5, 7                       # top-5, weekly
MA_DAYS = 100
COST = 15 / 1e4                   # strategy.yaml: 10bps fee + 5bps slippage
FLOOR = 0.02                      # dead coin -> 2% of pre-death px (P10 model)
DECAY_DAYS = 5
IMMORTAL = {"BTC/USDT", "ETH/USDT"}
DEATH_FRAC = 0.20                 # central 3-yr death rate (P10's headline)
N_POOL_SEEDS = 60                 # death-seeds pooled for the return distribution
N_PATHS = 4000                    # bootstrap paths per (f, W) cell
BLOCK = 8                         # bootstrap block length (weeks) ~ 2 months
WEEKS_PER_YEAR = 52.0
WEEKS_PER_MONTH = WEEKS_PER_YEAR / 12.0
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RNG = np.random.default_rng(12345)


def live_universe():
    with open(os.path.join(REPO, "state", "strategy.yaml")) as f:
        return yaml.safe_load(f)["universe"]


def multi_score(panel, i):
    return ((panel.iloc[i]/panel.iloc[i-14]-1) + (panel.iloc[i]/panel.iloc[i-30]-1)
            + (panel.iloc[i]/panel.iloc[i-60]-1)) / 3


def weekly_returns(panel, ma, trend=True):
    """Live long-only multi-horizon K5 weekly engine. Returns the weekly net-return
    array over the FULL window (more samples for the bootstrap distribution)."""
    rets, prev, i = [], pd.Series(0.0, index=panel.columns), 60
    while i + R < len(panel):
        sc = multi_score(panel, i).dropna()
        w = pd.Series(0.0, index=panel.columns)
        if len(sc) >= K:
            for s in sc.sort_values().index[-K:]:
                if (not trend) or panel.iloc[i][s] > ma.iloc[i][s]:
                    w[s] = 1/K
        fwd = (panel.iloc[i+R]/panel.iloc[i]-1).reindex(panel.columns).fillna(0)
        rets.append((w*fwd).sum() - (w-prev).abs().sum()*COST)
        prev = w; i += R
    return np.array(rets)


def inject_deaths(full, frac, seed):
    """P10 death model: kill floor(frac*eligible) coins at random dates over the WHOLE
    window (uniform), crash to FLOOR over DECAY_DAYS, then delisted-flat."""
    rng = np.random.default_rng(seed)
    eligible = [c for c in full.columns if c not in IMMORTAL]
    n_kill = int(np.floor(frac * len(eligible)))
    if n_kill == 0:
        return full.copy()
    doomed = rng.choice(eligible, size=n_kill, replace=False)
    p = full.copy(); n = len(p)
    lo, hi = MA_DAYS + 60, n - R - 2
    for c in doomed:
        d = int(rng.integers(lo, hi))
        base = p[c].iloc[d-1]
        if not np.isfinite(base) or base <= 0:
            continue
        for k in range(DECAY_DAYS):
            if d+k < n:
                p.iloc[d+k, p.columns.get_loc(c)] = base*(FLOOR**((k+1)/DECAY_DAYS))
        if d+DECAY_DAYS < n:
            p.iloc[d+DECAY_DAYS:, p.columns.get_loc(c)] = base*FLOOR
    return p


def bootstrap_path(pool, length, rng):
    """Block bootstrap a weekly-return path of `length` weeks. Each block is drawn from
    within ONE seed-series (preserves local autocorrelation / DD clustering)."""
    out = []
    while len(out) < length:
        series = pool[rng.integers(len(pool))]
        if len(series) <= BLOCK:
            out.extend(series.tolist()); continue
        start = rng.integers(0, len(series) - BLOCK)
        out.extend(series[start:start+BLOCK].tolist())
    return np.array(out[:length])


def simulate(pool, f, w_rate, horizon_weeks, n_paths, mean_shift=0.0):
    """Monte-Carlo monthly withdrawals. f=invested fraction (rest cash@0%); w_rate=annual
    withdrawal as fraction of INITIAL capital, paid monthly in fixed nominal dollars.
    Returns (ruin_prob, median_terminal, p10_terminal) with equity normalized to 1.0."""
    monthly_wd = w_rate / 12.0
    ruin = 0
    terminals = np.empty(n_paths)
    rng = np.random.default_rng(RNG.integers(1 << 30))
    for p in range(n_paths):
        path = bootstrap_path(pool, horizon_weeks, rng) + mean_shift
        eq = 1.0
        month = 0
        dead = False
        for t in range(horizon_weeks):
            eq *= (1 + f * path[t])
            # withdraw at each month boundary
            if int((t+1) / WEEKS_PER_MONTH) > month:
                month += 1
                eq -= monthly_wd
                if eq <= 0:
                    dead = True; eq = 0.0; break
        if dead:
            ruin += 1
        terminals[p] = eq
    return ruin/n_paths, float(np.median(terminals)), float(np.percentile(terminals, 10))


async def main():
    global BLOCK
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
    years = (full.index[-1]-full.index[0])/86400000/365
    print(f"\nP14 income / sequence-risk model — LIVE config (multi-horizon K5 weekly trend)")
    print(f"{n_coins}/{len(syms)} coins · ~{years:.1f}yr base window · {COST*1e4:.0f}bps/side")
    print(f"return distn = P10 survivorship-stressed (@{DEATH_FRAC*100:.0f}% deaths, "
          f"{N_POOL_SEEDS} seeds pooled) · block bootstrap B={BLOCK}w · {N_PATHS} paths/cell\n")

    # ---- build the weekly-return pools ----
    ma_full = full.rolling(MA_DAYS).mean()
    surv = weekly_returns(full, ma_full, trend=True)        # survivors-only (optimistic)
    pool = []
    for sd in range(N_POOL_SEEDS):
        pdead = inject_deaths(full, DEATH_FRAC, sd)
        pool.append(weekly_returns(pdead, pdead.rolling(MA_DAYS).mean(), trend=True))
    all_dead = np.concatenate(pool)
    surv_geo = (np.prod(1+surv)**(WEEKS_PER_YEAR/len(surv)) - 1)
    dead_geo = (np.prod(1+all_dead)**(WEEKS_PER_YEAR/len(all_dead)) - 1) if (1+all_dead).min() > 0 else float('nan')
    print(f"weekly-return pools (annualized geometric):")
    print(f"  survivors-only : mean {surv.mean()*100:+.2f}%/wk  ~{surv_geo*100:+.1f}%/yr  "
          f"(n={len(surv)})")
    print(f"  P10-stressed   : mean {all_dead.mean()*100:+.2f}%/wk  ~{dead_geo*100:+.1f}%/yr  "
          f"(n={len(all_dead)}, worstWk {all_dead.min()*100:.1f}%)\n")

    W_RATES = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40]
    F_DIALS = [1.0, 0.75, 0.50]
    HORIZON = 5
    hw = int(round(HORIZON*WEEKS_PER_YEAR))

    print(f"=== RUIN PROBABILITY over {HORIZON}yr (monthly withdrawals), P10-stressed returns ===")
    print(f"{'withdraw/yr':>12} | " + " | ".join(f"f={f:.2f}" for f in F_DIALS)
          + "    (ruin% | median terminal x)")
    swr = {}
    for f in F_DIALS:
        swr[f] = 0.0
    for wr in W_RATES:
        cells = []
        for f in F_DIALS:
            rp, med, p10 = simulate(pool, f, wr, hw, N_PATHS)
            cells.append(f"{rp*100:4.0f}% {med:4.1f}x")
            if rp <= 0.05 and wr > swr[f]:
                swr[f] = wr
        print(f"{wr*100:>10.0f}%  | " + " | ".join(cells))
    print(f"\nSWR (max withdrawal/yr with ruin <= 5% over {HORIZON}yr):")
    for f in F_DIALS:
        print(f"  invested f={f:.2f}: {swr[f]*100:.0f}%/yr")

    # ---- horizon sensitivity at full investment ----
    print(f"\n=== RUIN vs HORIZON (f=1.00, P10-stressed) ===")
    for H in (1, 3, 5, 10):
        hwx = int(round(H*WEEKS_PER_YEAR))
        row = []
        for wr in (0.10, 0.20, 0.30):
            rp, med, _ = simulate(pool, 1.0, wr, hwx, N_PATHS)
            row.append(f"{wr*100:.0f}%/yr->{rp*100:3.0f}%")
        print(f"  {H:>2}yr: " + "   ".join(row))

    # ---- RETURN-STRESS: halve the pooled mean (forward-return fragility) ----
    shift = -all_dead.mean() * 0.5    # subtract half the mean weekly return
    print(f"\n=== RETURN-STRESS: pooled mean halved ({all_dead.mean()*100:+.2f}->"
          f"{(all_dead.mean()+shift)*100:+.2f}%/wk, ~{((np.prod(1+all_dead+shift))**(WEEKS_PER_YEAR/len(all_dead))-1)*100:+.0f}%/yr), f=1.00, {HORIZON}yr ===")
    swr_s = 0.0
    for wr in W_RATES:
        rp, med, _ = simulate(pool, 1.0, wr, hw, N_PATHS, mean_shift=shift)
        flag = ""
        if rp <= 0.05 and wr > swr_s:
            swr_s = wr
        print(f"  {wr*100:>3.0f}%/yr: ruin {rp*100:4.0f}%  median terminal {med:4.1f}x")
    print(f"  -> stressed SWR (ruin<=5%, f=1.00): {swr_s*100:.0f}%/yr")

    # ---- block-length sensitivity (does autocorrelation assumption matter?) ----
    print(f"\n=== BLOCK-LENGTH sensitivity (f=1.00, 20%/yr withdraw, {HORIZON}yr ruin%) ===")
    for b in (1, 4, 8, 16):
        BLOCK = b
        rp, _, _ = simulate(pool, 1.0, 0.20, hw, N_PATHS)
        print(f"  block={b:>2}w: ruin {rp*100:.0f}%")
    BLOCK = 8

    print("\nVERDICT GUIDE: SWR is the sustainable income per $ capital (e.g. SWR 15% ->")
    print("$100 supports ~$15/yr at <=5% 5yr-ruin). Compare the stressed SWR (forward-return")
    print("fragility) and the f-dial (partial cash trades income for ruin-safety). This is a")
    print("backtest-window bound, NOT a promise — the base window is bull-flattered.")


asyncio.run(main())
